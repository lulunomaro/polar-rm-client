"""Owner-thread window lifecycle policy, independent of Qt and window state."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable, Protocol

from ...lifecycle import LifecycleCompletion, LifecyclePhase, LifecycleSnapshot, TeardownTask
from ...backend.lifecycle import ShutdownCompletion

_LOG = logging.getLogger(__name__)


class BackendLifecycle(Protocol):
    lifecycle_pending: bool
    lifecycle_closed: bool
    lifecycle_snapshot: LifecycleSnapshot
    shutdown_completion: ShutdownCompletion | None

    def request_shutdown(self, *, teardown_tasks: tuple[TeardownTask, ...]) -> int: ...
    def poll_lifecycle(self) -> LifecycleCompletion | None: ...
    def retry_shutdown(self) -> int: ...
    def lock_control(self, reason: str) -> None: ...


@dataclass(frozen=True)
class OwnerAction:
    name: str
    run: Callable[[], object]


@dataclass(frozen=True)
class OwnerActionFailure:
    name: str
    error_type: str
    message: str

    @property
    def summary(self) -> str:
        return f"{self.name}: {self.error_type}: {self.message}"


def run_owner_actions(actions: tuple[OwnerAction, ...]) -> tuple[OwnerActionFailure, ...]:
    """A failed owner-thread cleanup must not skip an independent resource."""
    failures = []
    for action in actions:
        try:
            action.run()
        except Exception as exc:
            _LOG.error("Window owner action %s failed", action.name, exc_info=True)
            failures.append(OwnerActionFailure(action.name, type(exc).__name__, str(exc)))
    return tuple(failures)


@dataclass(frozen=True)
class FinalContextPorts:
    """Owner-thread context operations; resources are resolved only after entry.

    These callbacks must not dispatch to workers: GL retirement belongs to the
    context-owning thread. The leave factory resolves its callback in the finally
    phase, preserving lookup failures separately from release failures. Missing contexts skip GPU work; failed entry still
    attempts leave, matching Qt window retirement's established contract.
    """

    available: Callable[[], bool]
    enter: Callable[[], None]
    releases: Callable[[], tuple[OwnerAction, ...]]
    leave: Callable[[], OwnerAction]


@dataclass(frozen=True)
class LifecycleUpdate:
    runtime_allowed: bool
    schedule_close: bool
    notification: str
    failed: bool
    completion_warning: str = ""


class WindowLifecycleController:
    """Keep a closing window alive until all native owners confirm retirement.

    Backend callbacks and completion routing run in the caller. Only the named
    teardown tasks are sent to workers by the backend's bounded coordinator.
    """

    def __init__(self, backend: BackendLifecycle) -> None:
        self.backend = backend
        self.finalized = False
        self.finalization_failures: tuple[OwnerActionFailure, ...] = ()
        self.closing = False
        self.ready_to_close = False
        self.status_text = ""
        self.owner_failures: tuple[OwnerActionFailure, ...] = ()
        self._request_failed = False
        self._close_scheduled = False
        self._last_notice: tuple[object, ...] | None = None
        self.poll_failures = 0
        self.last_poll_failure: OwnerActionFailure | None = None

    def finalize(self, *, prepare: Callable[[], tuple[OwnerAction, ...]],
                 context: FinalContextPorts) -> tuple[OwnerActionFailure, ...] | None:
        """Seal owner retirement before callbacks, independently of diagnostics.

        The caller admits this only after native retirement. A repeated or
        reentrant call returns None, never replaying timer/config/GL effects.
        Preparation is resolved after sealing, so even callback lookup reentry
        cannot replay retirement. UI reporting stays outside this owner and
        cannot undo the seal.
        """
        if self.finalized:
            return None
        self.finalized = True
        failures = list(self.owner_failures)
        failures.extend(run_owner_actions(prepare()))
        try:
            has_context = context.available()
        except Exception as exc:
            _LOG.error("Window GL context inspection failed", exc_info=True)
            has_context = False
            failures.append(OwnerActionFailure("GL context", type(exc).__name__, str(exc)))
        if has_context:
            try:
                context.enter()
                failures.extend(run_owner_actions(context.releases()))
            except Exception as exc:
                _LOG.error("Window GL context cleanup failed", exc_info=True)
                failures.append(OwnerActionFailure("GL context", type(exc).__name__, str(exc)))
            finally:
                failures.extend(run_owner_actions((context.leave(),)))
        self.finalization_failures = tuple(failures)
        return self.finalization_failures

    def request_close(self, *, prepare: tuple[OwnerAction, ...], tasks: tuple[TeardownTask, ...]) -> None:
        if self.closing:
            return
        # Set before callbacks so nested paint/input events cannot restart work.
        self.closing = True
        self.status_text = "Closing connections and workers…"
        self.owner_failures = run_owner_actions(prepare)
        try:
            self.backend.request_shutdown(teardown_tasks=tasks)
        except Exception as exc:
            _LOG.error("Window shutdown request failed", exc_info=True)
            self._request_failed = True
            self.owner_failures += (OwnerActionFailure("shutdown request", type(exc).__name__, str(exc)),)

    def retry_close(self) -> bool:
        """One user close action may retry completed failures, never live work."""
        result = self.backend.shutdown_completion
        if not self.closing or self.backend.lifecycle_pending or result is None or result.may_exit:
            return False
        try:
            self.backend.retry_shutdown()
        except Exception as exc:
            _LOG.error("Window shutdown retry failed", exc_info=True)
            self._request_failed = True
            self.owner_failures += (OwnerActionFailure("shutdown retry", type(exc).__name__, str(exc)),)
            return False
        self._request_failed = False
        self._last_notice = None
        self.status_text = "Retrying failed resource cleanup…"
        return True

    def poll(self, on_completion: Callable[[LifecycleCompletion], bool]) -> LifecycleUpdate:
        try:
            return self._poll(on_completion)
        except Exception as exc:
            self.ready_to_close = False
            self.poll_failures += 1
            failure = OwnerActionFailure("lifecycle poll", type(exc).__name__, str(exc))
            if failure != self.last_poll_failure:
                _LOG.error("Window lifecycle polling failed", exc_info=True)
            self.last_poll_failure = failure
            # Do not advance a previously armed runtime after losing its owner
            # completion boundary, even if diagnostic delivery itself failed.
            try:
                self.backend.lock_control("lifecycle polling failed")
            except Exception:
                _LOG.error("Could not lock after lifecycle polling failure", exc_info=True)
            message = failure.summary
            self.status_text = message
            key = ("poll", message)
            notification = message if self._last_notice != key else ""
            self._last_notice = key
            return LifecycleUpdate(False, False, notification, True)

    def _poll(self, on_completion: Callable[[LifecycleCompletion], bool]) -> LifecycleUpdate:
        completion = self.backend.poll_lifecycle()
        handled = bool(completion is not None and on_completion(completion))
        # A profile completion may have requested a stopped rollback. Always
        # re-read state after routing it, before allowing any runtime work.
        snapshot = self.backend.lifecycle_snapshot
        pending = self.backend.lifecycle_pending
        failed = bool(snapshot.failures or snapshot.phase == LifecyclePhase.FAILED or self._request_failed)
        final = self.backend.shutdown_completion
        self.ready_to_close = bool(
            self.closing and final is not None and final.may_exit and not pending and not self._request_failed
        )
        completion_warning = ""
        if final is not None and final.may_exit and not final.succeeded:
            if not final.audit.intact:
                completion_warning = (
                    "Resources were released, but the audit log lost data. "
                    f"Dropped records: {final.audit.dropped_records}; "
                    f"write errors: {final.audit.write_errors}; "
                    f"pending records: {final.audit.pending_records}."
                )
            else:
                completion_warning = "Resources were released, but shutdown reported errors."
            if final.failures:
                completion_warning += " Details: " + "; ".join(
                    f"{failure.resource}: {failure.error}" for failure in final.failures
                )
        schedule_close = self.ready_to_close and not self._close_scheduled
        self._close_scheduled |= schedule_close
        notification = self._notification(snapshot, completion, handled, failed)
        if schedule_close and completion_warning:
            self.status_text = notification = completion_warning
        return LifecycleUpdate(
            runtime_allowed=not (self.closing or pending or failed or snapshot.final),
            schedule_close=schedule_close,
            notification=notification,
            failed=failed or bool(completion_warning),
            completion_warning=completion_warning if schedule_close else "",
        )

    def _notification(self, snapshot, completion, handled: bool, failed: bool) -> str:
        if self._request_failed:
            message = "Shutdown request failed: " + "; ".join(f.summary for f in self.owner_failures)
        elif failed:
            details = "; ".join(f"{f.resource}: {f.error}" for f in snapshot.failures)
            message = ("Shutdown" if self.closing else "Scenario transition") + " failed: " + details
            if self.closing:
                message += " Close the window again to retry failed cleanup."
        elif snapshot.phase == LifecyclePhase.BLOCKED:
            message = "Waiting for resources: " + ", ".join(snapshot.pending_resources)
        elif self.closing:
            message = "Closing connections and workers…"
        elif completion is not None and not handled and completion.succeeded:
            message = "Scenario ready"
        else:
            message = ""
        if self.closing:
            self.status_text = message
        key = (snapshot.generation, snapshot.phase, message)
        if key == self._last_notice or (handled and not self.closing):
            return ""
        self._last_notice = key
        return message
