"""Nonblocking DT7 dispatcher. Only its single worker owns the serial handle.

One latest-intent slot, one priority neutral barrier, no unbounded frame queue.
Acceptance is not delivery: counters and wire state are published by the worker.
Failed output latches locally immediately; the UI drains the lock notification.
"""
from __future__ import annotations

import threading
import copy
import time
from .dt7_link import Dt7KeyMouseLink
from .resource_close import close_resource
from .writer_thread import WriterThread


class _WorkerSafety:
    def __init__(self, owner):
        self.owner = owner

    def can_transmit_dt7(self, **kwargs):
        owner = self.owner
        if owner._fault or owner.safety is None:
            return False, owner._fault or "DT7 safety unavailable"
        # OfficialSafety.can_publish may lock on token expiry. Evaluate a
        # shallow policy snapshot so that path cannot invoke GUI callbacks or
        # mutate UI-owned state; its lock notification is routed to poll().
        policy = copy.copy(owner.safety)
        policy.input_clear_hook = self.lock_control
        return policy.can_transmit_dt7(**kwargs)

    def lock_control(self, reason):
        self.owner._latch_fault(str(reason))

    def mark_published(self, *args, **kwargs):
        # OfficialSafety owns UI-facing state. Drain this notification there.
        self.owner._published = (args, kwargs)


class AsyncDt7KeyMouseLink:
    """Production facade; synchronous codec/link remains usable by offline tools.

    ``retain_owner`` may publish the initialized facade to its caller before
    worker admission. Keep that owner through a constructor failure until
    ``closed`` is confirmed. The original exception also receives a best-effort
    ``failed_writer`` attribute for callers using the original constructor API.
    The callback runs outside the worker lock. If it calls ``close()``, the
    constructor returns that sealed owner without admitting another writer.
    """
    asynchronous = True

    def __init__(self, *, port="Auto", profile=None, mock_output=True, safety=None,
                 link=None, release_attempts=3, max_intent_age_s=0.15, retain_owner=None):
        self.port = port
        self.profile = dict(profile or {})
        self.mock_output = bool(mock_output)
        self.safety = safety
        self.authorization_guard = None
        self._link = link or Dt7KeyMouseLink(port=port, profile=self.profile,
                                            mock_output=mock_output)
        self._cv = threading.Condition()
        self._latest = None
        self._release_pending = False
        self._release_exhausted = False
        self._release_tries = 0
        self._release_limit = max(1, int(release_attempts))
        self._release_due = 0.0
        self._generation = 0
        self._inflight = False
        self._closing = False
        self._closed = threading.Event()
        self._fault = ""
        self._fault_reported = False
        self._published = None
        self._motion_deadline = 0.0
        self._qualification_hold = None
        self.max_intent_age_s = float(max_intent_age_s)
        self.queued = 0
        self.coalesced = 0
        self.stale_dropped = 0
        self.failed = 0
        self.release_attempts = 0
        self.last_send_dropped = False
        self.last_send_rate_limited = False
        self._last_error = ""
        self._link.safety = _WorkerSafety(self)
        self._worker = WriterThread(self._cv)
        self.startup_failed = False
        admission_reserved = False
        try:
            if retain_owner is not None:
                retain_owner(self)
            with self._cv:
                if self._closing:
                    return  # The retention callback already cancelled this owner.
                self._worker.reserve()
                admission_reserved = True
            self._worker.start_reserved(self._run, "dt7-writer")
        except BaseException as exc:
            self.startup_failed = True
            # A defensive start wrapper may admit the real worker then raise.
            # Cancel that owner, or request cleanup for a never-admitted one.
            # Neither cleanup admission nor exception annotation may replace
            # the primary failure. retain_owner is the independent owner path.
            try:
                with self._cv:
                    # A finished failed finalizer stays an explicit retry;
                    # the admission error must not silently run it again.
                    if not self._closing:
                        self._last_error = "DT7 writer admission failed; close required"
                    never_admitted = self._worker.never_admitted
                    if not self._closing or (admission_reserved and never_admitted):
                        self.close()
            except BaseException:
                pass
            try:
                exc.failed_writer = self
            except BaseException:
                pass
            raise

    def __getattr__(self, name):
        return getattr(self._link, name)

    @property
    def last_error(self):
        return self._last_error or self._link.last_error or self._fault

    @property
    def wire_neutral(self):
        with self._cv:
            return bool(self._link.wire_neutral and not self._inflight
                        and self._latest is None and not self._release_pending)

    @property
    def release_pending(self):
        return self._release_pending or self._release_exhausted

    def poll(self):
        """Call from UI/control tick; never run a GUI callback on the writer."""
        with self._cv:
            fault = self._fault if not self._fault_reported else ""
            if fault:
                self._fault_reported = True
            published, self._published = self._published, None
        if fault and self.safety is not None:
            self.safety.lock_control(fault)
        if published and self.safety is not None:
            self.safety.mark_published(*published[0], **published[1])

    def _epoch(self):
        return getattr(self.safety, "last_transition_at", None)

    def send(self, **kwargs):
        self.poll()
        with self._cv:
            if self._closing:
                return False, "DT7 writer closed"
            # A fresh explicit qualification/re-arm may clear the fault, but
            # never an unfinished neutral barrier or a mere reconnection.
            if self._fault:
                if self._epoch() != getattr(self, "_fault_epoch", None) and self.safety is not None:
                    allowed, _ = self.safety.can_transmit_dt7(
                        qualification_token=kwargs.get("qualification_token"))
                    if (allowed and str(getattr(getattr(self.safety, "control_state", None), "value", ""))
                            in {"MATCH_ARMED", "QUALIFICATION"}
                            and not self.release_pending and self._link.wire_neutral):
                        self._fault = ""
                        self._fault_reported = False
                if self._fault:
                    return False, self.last_error or self._fault
            if self.release_pending:
                return False, self.last_error or "DT7 neutral pending"
            created = time.monotonic()
            if self._latest is not None:
                self.coalesced += 1
                old, generation, epoch, submitted = self._latest
                if (generation == self._generation and epoch == self._epoch()
                        and time.monotonic() - submitted <= self.max_intent_age_s):
                    # Relative mouse samples must accumulate across the wire
                    # rate gate; only held-key/button state is latest-wins.
                    limit = max(1, int(self.profile.get("mouse_limit", 200) or 32767))
                    if any(old.get(axis, 0) for axis in ("mouse_dx", "mouse_y", "mouse_z")):
                        created = submitted
                    for axis in ("mouse_dx", "mouse_y", "mouse_z"):
                        kwargs[axis] = max(-limit, min(limit,
                            int(old.get(axis, 0)) + int(kwargs.get(axis, 0))))
            self.queued += 1
            # Do not carry UI-provided time into the eventual authorization.
            kwargs.pop("now", None)
            self._latest = (dict(kwargs), self._generation, self._epoch(), created)
            self._cv.notify()
        return True, "queued"

    def release(self, now=None):
        """Owner-thread compatibility entry point, including observer delivery."""
        self.poll()
        return self.request_release()

    def request_release(self):
        """Admit bounded neutral from any thread without invoking observers."""
        with self._cv:
            if self._closing:
                return self.wire_neutral, self.last_error or "DT7 writer closed"
            if self._release_exhausted:
                return False, self.last_error
            if not self._release_pending:
                self._generation += 1
                self._latest = None
                self._qualification_hold = None
                if self._link.wire_neutral and not self._inflight:
                    return True, "already neutral"
                self._release_pending = True
                self._release_tries = 0
                self._release_due = 0.0
            self._cv.notify()
        return False, "DT7 neutral queued"

    def begin_qualification_hold(self, token, until):
        """Bounded, token-authorized no-send phase of the neutral/X probe only."""
        qualification = getattr(self.safety, "_qualification", None)
        now = time.perf_counter()
        if (qualification is None or token != qualification.value
                or "dt7_selftest" not in qualification.intents
                or now >= qualification.expires_at
                or not self._link.safety.can_transmit_dt7(qualification_token=token)[0]):
            return False
        with self._cv:
            if self._fault or self._closing or self.release_pending:
                return False
            self._qualification_hold = (token, min(float(until), qualification.expires_at, now + 2.0))
            self._cv.notify()
        return True

    def retry_release(self):
        """Explicit recovery only; ordinary heartbeat cannot reset the budget."""
        with self._cv:
            if self._closing:
                return False, "DT7 writer closed"
            self._release_exhausted = False
            self._release_pending = False
        return self.release()

    def _latch_fault(self, reason, *, retry_delay=0.0):
        with self._cv:
            if not self._fault:
                self._fault = reason
                self._fault_epoch = self._epoch()
                self._fault_reported = False
            self._generation += 1
            self._latest = None
            if (not self._link.wire_neutral and not self._release_pending
                    and not self._release_exhausted):
                self._release_pending = True
                self._release_tries = 0
                self._release_due = time.monotonic() + max(retry_delay,
                    self._link._open_backoff_until - time.perf_counter(), 0.0)
            self._cv.notify()

    def _valid(self, generation, epoch, submitted, qualification_token=None):
        # Recheck AFTER a slow open, immediately before write. A release,
        # E-stop, focus loss, epoch change or stale sample cancels this intent.
        return (not self._closing and not self._fault and
                generation == self._generation and epoch == self._epoch() and
                time.monotonic() - submitted <= self.max_intent_age_s and
                (self.mock_output or self._link.safety.can_transmit_dt7(
                    qualification_token=qualification_token)[0]))

    def _run(self):
        try:
            while True:
                with self._cv:
                    while True:
                        if self._release_pending:
                            delay = self._release_due - time.monotonic()
                            if delay > 0:
                                self._cv.wait(delay)
                                continue
                            neutral, job = True, None
                            break
                        if self._closing:
                            return
                        if self._latest is not None:
                            sample = self._latest[0]
                            releasing = not any(sample.get(key, 0) for key in (
                                "pressed_bits", "mouse_dx", "mouse_y", "mouse_z", "left", "right", "mid"))
                            delay = self._link._next_due - time.perf_counter()
                            if delay > 0 and not (releasing and not self._link.wire_neutral):
                                self._cv.wait(delay)
                                continue
                            neutral, job = False, self._latest
                            self._latest = None
                            break
                        if self._motion_deadline and not self._link.wire_neutral:
                            hold = self._qualification_hold
                            if (hold is not None and time.perf_counter() < hold[1]
                                    and self._link.safety.can_transmit_dt7(qualification_token=hold[0])[0]):
                                self._cv.wait(min(.05, hold[1] - time.perf_counter()))
                                continue
                            remaining = self._motion_deadline - time.monotonic()
                            if remaining <= 0:
                                self._motion_deadline = 0.0
                                self._latch_fault("DT7 input watchdog expired; explicit re-arm required")
                                continue
                            self._cv.wait(remaining)
                        else:
                            self._cv.wait()
                    self._inflight = True
                try:
                    if neutral:
                        self._link.pre_write_guard = None
                        self.release_attempts += 1
                        ok, why = self._link.release()
                    else:
                        kwargs, generation, epoch, submitted = job
                        if not self._valid(generation, epoch, submitted, kwargs.get("qualification_token")):
                            self.stale_dropped += 1
                            self._latch_fault("DT7 stale input discarded; explicit re-arm required")
                            continue
                        self._link.authorization_guard = self.authorization_guard
                        self._link.pre_write_guard = lambda: self._valid(generation, epoch, submitted, kwargs.get("qualification_token"))
                        ok, why = self._link.send(**kwargs)
                        if not ok and self._link.last_send_rate_limited:
                            # Keep only the newest unexpired intent until the
                            # wire slot opens. A neutral request still wakes us.
                            with self._cv:
                                if self._latest is None and self._valid(generation, epoch, submitted, kwargs.get("qualification_token")):
                                    self._latest = job
                                elif self._latest is None:
                                    self.stale_dropped += 1
                                    self._latch_fault("DT7 stale input discarded; explicit re-arm required")
                                self._cv.wait(max(0.001, self._link._next_due - time.perf_counter()))
                            continue
                    if ok:
                        self._motion_deadline = (0.0 if self._link.wire_neutral else
                                                 submitted + self.max_intent_age_s)
                    self._last_error = "" if ok else str(why)
                    if not ok:
                        self.failed += 1
                    if neutral:
                        with self._cv:
                            self._release_tries += 1
                            if ok:
                                self._release_pending = False
                                self._release_exhausted = False
                            elif self._release_tries >= self._release_limit:
                                self._release_pending = False
                                self._release_exhausted = True
                                self._last_error = f"DT7 neutral unconfirmed after {self._release_tries} attempts; explicit retry required: {why}"
                            else:
                                self._release_due = time.monotonic() + min(2.0, 0.5 * 2 ** (self._release_tries - 1))
                    elif not ok and self._link.last_send_dropped:
                        self._latch_fault("DT7 disconnected/write failed; explicit re-arm required")
                except Exception as exc:
                    self._last_error = f"DT7 worker failed: {exc}"
                    if neutral:
                        with self._cv:
                            self._release_pending = False
                            self._release_exhausted = True
                    self._latch_fault(self._last_error)
                finally:
                    with self._cv:
                        self._inflight = False
                        self._cv.notify_all()
        finally:
            self._finalize_close()

    def _finalize_close(self):
        """Runs only on the sole writer or an explicit background close retry."""
        with self._cv:
            self._closing = True
            self._latest = None
        try:
            close_resource(self._link)
        except Exception as exc:
            with self._cv:
                self._last_error = f"DT7 close unconfirmed; explicit close retry required: {exc}"
                self._fault = self._last_error
                self._fault_reported = False
                self._cv.notify_all()
        else:
            self._closed.set()

    def close(self):
        # UI shutdown never joins a potentially blocked OS driver. The sole
        # owner finishes bounded neutral best-effort then closes in finally.
        with self._cv:
            if not self._closing:
                try:
                    self.request_release()
                finally:
                    self._closing = True
                    self._generation += 1
                    self._latest = None
                    self._cv.notify_all()
            if not self._worker.starting and not self.closed and not self.worker_alive:
                # An unadmitted owner may still hold an injected native link
                # or a neutral barrier. Run its normal drain off the caller.
                never_admitted = self._worker.never_admitted
                target = self._run if never_admitted else self._finalize_close
                self._worker.start(target, "dt7-close-retry")

    @property
    def closed(self):
        return self._closed.is_set()

    @property
    def _thread(self):
        """Compatibility view of the sole retained worker (never a second owner)."""
        return self._worker.thread

    @property
    def worker_alive(self):
        return self._worker.alive

    @property
    def port_handoff_ready(self):
        """Proof for another owner: neutral, no retained handle, worker gone."""
        return bool(
            self.closed and not self.worker_alive
            and getattr(self._link, "_handle", self._link) is None
            and not getattr(self._link, "_handle_close_failed", True)
            and self.wire_neutral and not self.release_pending
        )

    def wait_closed(self, timeout=2.0):
        """Background/test cleanup only; None explicitly waits without a deadline."""
        self._worker.join(timeout)
        return self.closed
