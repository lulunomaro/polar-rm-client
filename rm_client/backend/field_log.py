"""Bounded field telemetry logs and a publish-free replay reader."""

from __future__ import annotations

import base64
from collections import deque
import json
import os
from pathlib import Path
import threading
import time
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping


class RotatingJsonlLogger:
    """A bounded, asynchronous 5x20 MiB JSONL logger.

    ``emit`` snapshots a bounded record and queues it, never doing filesystem
    work or waiting for the writer to drain. A short state lock is never held
    during I/O. There is at most one writer, started lazily and retired when
    idle. Call ``close`` at final application shutdown; a scenario restart must
    keep this logger open. ``flush``/``close`` are explicit bounded waits, not
    control-loop operations, and acknowledge OS writes, not fsync durability.

    Rejected, failed and cancelled records remain visible in ``snapshot``.
    Neither a successful later write nor a flush clears loss/error counters.
    Video payload events are deliberately excluded; referee bytes retain their
    65536-byte cap. Mutable values are copied before ``emit`` returns.
    """

    def __init__(
        self,
        directory: Path,
        *,
        filename: str = "polar-field.jsonl",
        max_bytes: int = 20 * 1024 * 1024,
        backups: int = 4,
        queue_capacity: int = 256,
        queue_bytes: int = 4 * 1024 * 1024,
        max_record_bytes: int = 256 * 1024,
        idle_timeout: float = 1.0,
    ) -> None:
        self.directory = Path(directory)
        self.path = self.directory / filename
        self.max_bytes = max(1024, int(max_bytes))
        self.backups = max(1, int(backups))
        self.queue_capacity = max(1, int(queue_capacity))
        self.queue_bytes = max(1024, int(queue_bytes))
        self.max_record_bytes = min(self.max_bytes, max(1024, int(max_record_bytes)))
        self._idle_timeout = max(0.01, float(idle_timeout))
        self._condition = threading.Condition(threading.Lock())
        self._queue: deque[tuple[int, bytes]] = deque()
        self._worker: threading.Thread | None = None
        self._in_flight = False
        self._closing = self._closed = False
        self._accepted = self._written = self._completed_sequence = 0
        self._pending_records = self._pending_bytes = 0
        self._cancelled_tail = 0
        self._errors = self._flush_timeouts = self._close_timeouts = 0
        self._worker_starts = self._ignored_video = 0
        self._dropped = {reason: 0 for reason in ("full", "invalid", "closed", "write", "cancelled")}
        self._last_error = ""

    @property
    def last_error(self) -> str:
        with self._condition:
            return self._last_error

    def snapshot(self) -> Mapping[str, Any]:
        """Return detached, immutable counters, including in-flight bytes."""
        with self._condition:
            return MappingProxyType({
                "accepted_records": self._accepted,
                "written_records": self._written,
                "pending_records": self._pending_records,
                "pending_bytes": self._pending_bytes,
                "queue_capacity": self.queue_capacity,
                "queue_bytes": self.queue_bytes,
                "max_record_bytes": self.max_record_bytes,
                "dropped_records": sum(self._dropped.values()),
                **{f"dropped_{key}": value for key, value in self._dropped.items()},
                "write_errors": self._errors,
                "flush_timeouts": self._flush_timeouts,
                "close_timeouts": self._close_timeouts,
                "ignored_video": self._ignored_video,
                "worker_starts": self._worker_starts,
                "worker_active": self._worker is not None,
                "closing": self._closing,
                "closed": self._closed,
                "last_error": self._last_error,
            })

    def emit(self, event: str, **fields: Any) -> bool:
        """Return whether a record was queued; acceptance is not write success."""
        if type(event) is str and len(event) < 32 and event.lower() in {"video_frame", "video_payload", "decoded_frame"}:
            with self._condition:
                self._ignored_video += 1
            return False
        # Avoid even snapshot work after shutdown or when the record limit is
        # already exhausted. The second check below resolves producer races.
        with self._condition:
            if self._closing:
                self._dropped["closed"] += 1
                return False
            if self._pending_records >= self.queue_capacity:
                self._dropped["full"] += 1
                return False
        try:
            if type(event) is not str:
                raise TypeError("event must be a string")
            if len(fields) > 1020:
                raise ValueError("snapshot complexity limit")
            record = dict(fields)
            # Envelope fields are authoritative, including when callers pass
            # their own wall_time/schema/monotonic in diagnostic fields.
            record.update(schema=1, wall_time=time.time(), monotonic=time.perf_counter(), event=event)
            safe = self._safe(record, budget=[2048, self.max_record_bytes])
            line = (json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            if len(line) > self.max_record_bytes:
                raise ValueError("record size limit")
        except (TypeError, ValueError, RecursionError, RuntimeError, OverflowError):
            with self._condition:
                self._dropped["invalid"] += 1
                self._last_error = "record rejected: unsupported value or snapshot limit"
            return False
        start_worker = None
        with self._condition:
            if self._closing:
                self._dropped["closed"] += 1
                return False
            if self._pending_records >= self.queue_capacity or self._pending_bytes + len(line) > self.queue_bytes:
                self._dropped["full"] += 1
                return False
            self._accepted += 1
            self._queue.append((self._accepted, line))
            self._pending_records += 1
            self._pending_bytes += len(line)
            if self._worker is None:
                try:
                    start_worker = threading.Thread(
                        target=self._run, name="polar-field-log", daemon=True,
                    )
                except Exception as exc:
                    self._record_admission_failure(exc, None)
                    return False
                self._worker = start_worker
            self._condition.notify_all()
        if start_worker is not None:
            # Starting happens outside the state lock. Concurrent producers can
            # still enqueue, and close can seal ingress while startup proceeds.
            try:
                start_worker.start()
            except Exception as exc:
                with self._condition:
                    self._record_admission_failure(exc, start_worker)
                return False
        return True

    def _record_admission_failure(self, error: Exception, worker: threading.Thread | None) -> None:
        """Called under the state lock; only a never-admitted owner loses work.

        A start wrapper may raise after a real worker started or even finished.
        Its ``ident`` stays set after exit. Keep that worker's in-flight counts
        and any later writer intact; a diagnostic is not proof of release.
        """
        self._errors += 1
        self._last_error = self._error_text(error)
        if self._worker is worker and (worker is None or worker.ident is None):
            self._dropped["write"] += len(self._queue)
            self._queue.clear()
            self._pending_records = self._pending_bytes = 0
            self._completed_sequence = self._accepted
            self._worker = None
            self._closed = self._closing
        self._condition.notify_all()

    def referee_payload(self, topic: str, payload: bytes) -> bool:
        bounded = bytes(payload[:65536])
        return self.emit(
            "referee_rx", topic=topic,
            payload_b64=base64.b64encode(bounded).decode("ascii"),
            payload_bytes=len(payload), truncated=len(payload) > len(bounded),
        )

    def flush(self, timeout: float = 1.0) -> bool:
        """Wait for records accepted before this call; False on timeout/loss.

        Later producers do not extend the barrier. A true result does not
        promise power-loss durability. Do not call from frame/transport ticks.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            target = self._accepted
            while self._completed_sequence < target:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._flush_timeouts += 1
                    return False
                self._condition.wait(remaining)
            return not any(self._dropped.values()) and self._errors == 0

    def close(self, timeout: float = 1.0, *, drain: bool = True) -> bool:
        """Seal ingress and wait at most timeout; optionally cancel queued work.

        An OS write already in progress cannot be interrupted. A timed-out
        close leaves that one daemon worker draining; no replacement can start.
        ``drain=False`` explicitly counts every cancelled record. A False result
        means incomplete cleanup or recorded loss; inspect the snapshot.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            self._closing = True
            if not drain:
                self._dropped["cancelled"] += len(self._queue)
                self._pending_records -= len(self._queue)
                self._pending_bytes -= sum(len(line) for _, line in self._queue)
                self._queue.clear()
                self._cancelled_tail = self._accepted
                if not self._in_flight:
                    self._completed_sequence = self._accepted
            if self._worker is None:
                self._closed = True
            self._condition.notify_all()
            while not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._close_timeouts += 1
                    return False
                self._condition.wait(remaining)
            return not any(self._dropped.values()) and self._errors == 0

    def _run(self) -> None:
        with self._condition:
            self._worker_starts += 1
        while True:
            with self._condition:
                if not self._queue and not self._closing:
                    self._condition.wait_for(lambda: bool(self._queue) or self._closing, self._idle_timeout)
                if not self._queue:
                    self._worker = None
                    self._closed = self._closing
                    self._condition.notify_all()
                    return
                sequence, line = self._queue.popleft()
                self._in_flight = True
            error = ""
            try:
                self._write_line(line)
            except Exception as exc:
                # A disk/rotation failure or a failed file-like implementation
                # cannot kill the worker silently or strand flush waiters.
                error = self._error_text(exc)
            finally:
                with self._condition:
                    self._pending_records -= 1
                    self._pending_bytes -= len(line)
                    self._in_flight = False
                    self._completed_sequence = max(sequence, self._cancelled_tail)
                    if error:
                        self._errors += 1
                        self._dropped["write"] += 1
                        self._last_error = error
                    else:
                        self._written += 1
                    self._condition.notify_all()

    @staticmethod
    def _error_text(exc: Exception) -> str:
        # Do not echo arbitrary payloads or private filesystem paths into the
        # diagnostics; exception type and errno are enough to identify failures.
        number = getattr(exc, "errno", None)
        return f"{type(exc).__name__}" + (f" (errno={number})" if number is not None else "")

    def _write_line(self, line: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed(len(line))
        with self.path.open("ab", buffering=0) as handle:
            pending = memoryview(line)
            while pending:
                written = handle.write(pending)
                if written is None or written <= 0 or written > len(pending):
                    raise OSError("incomplete field log write")
                pending = pending[written:]

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size + incoming <= self.max_bytes:
            return
        oldest = self.path.with_suffix(self.path.suffix + f".{self.backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_suffix(self.path.suffix + f".{index}")
            if source.exists():
                os.replace(source, self.path.with_suffix(self.path.suffix + f".{index + 1}"))
        if self.path.exists():
            os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))

    @classmethod
    def _safe(cls, value: Any, *, budget: list[int], depth: int = 0) -> Any:
        # Count both nodes and aggregate scalar characters before JSON encoding.
        # Cycles fail at the depth limit; arbitrary __repr__/iterator hooks are
        # not invoked on control-loop threads. Normal JSON/builtin values keep
        # the existing schema and byte payload base64 representation.
        budget[0] -= 1
        if budget[0] < 0 or depth > 8:
            raise ValueError("snapshot complexity limit")
        value_type = type(value)
        if value is None or value_type in (bool, float):
            return value
        if value_type is int:
            if value.bit_length() > 128:
                raise ValueError("integer size limit")
            return value
        if value_type is bytes:
            value = base64.b64encode(value[:65536]).decode("ascii")
            value_type = str
        if value_type is str:
            budget[1] -= len(value)
            if budget[1] < 0:
                raise ValueError("snapshot size limit")
            return value
        if value_type is dict:
            result = {}
            for key, item in value.items():
                if type(key) not in (str, int, float, bool, type(None)):
                    raise TypeError("unsupported mapping key")
                safe_key = cls._safe(key, budget=budget, depth=depth + 1)
                result[str(safe_key)] = cls._safe(item, budget=budget, depth=depth + 1)
            return result
        if value_type in (list, tuple, set, frozenset):
            return [cls._safe(item, budget=budget, depth=depth + 1) for item in value]
        raise TypeError("unsupported field value")


class ReadOnlyFieldReplay:
    """Iterate field records. This class intentionally has no publisher."""

    def __init__(self, paths: Iterable[Path]) -> None:
        self.paths = tuple(Path(path) for path in paths)

    def records(self) -> Iterator[dict[str, Any]]:
        for path in self.paths:
            # Accept a BOM on imported/operator-created logs while the native
            # logger itself continues to write canonical BOM-free UTF-8.
            with path.open("r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, 1):
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"{path}:{line_number}: invalid JSONL") from exc
                    if isinstance(record, dict):
                        yield record
