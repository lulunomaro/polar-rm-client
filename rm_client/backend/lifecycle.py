"""Backend lifecycle values and blocking resource boundary.

Only the owner-thread manager reads registry/state/safety or applies a scenario.
This module receives detached configuration values or captured resource owners;
its close operations never call manager methods or presentation observers.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
from typing import Any

from ..lifecycle import LifecycleCompletion, TeardownTask

_LOG = logging.getLogger("rm_client.backend.manager")


@dataclass(frozen=True, slots=True)
class ScenarioRequest:
    key: str
    config: Any
    start: bool
    robot_id: str | None
    referee_profile: str
    polar_mode: str
    polar_host: str
    polar_tcp_port: int
    preserve_estop: bool
    synchronous_start: bool = False


@dataclass(frozen=True, slots=True)
class ShutdownRequest:
    restart: bool = False


@dataclass(frozen=True, slots=True)
class AuditCloseStatus:
    drained: bool
    intact: bool
    pending_records: int
    dropped_records: int
    write_errors: int

    @classmethod
    def capture(cls, logger: Any) -> "AuditCloseStatus":
        snapshot = logger.snapshot()
        pending = int(snapshot["pending_records"])
        dropped = int(snapshot["dropped_records"])
        errors = int(snapshot["write_errors"])
        drained = bool(snapshot["closed"] and not snapshot["worker_active"] and pending == 0)
        return cls(drained, dropped == 0 and errors == 0, pending, dropped, errors)


@dataclass(frozen=True, slots=True)
class ShutdownCompletion(LifecycleCompletion[ShutdownRequest]):
    """Resource release and audit integrity are independent final outcomes."""

    resources_released: bool
    audit: AuditCloseStatus
    attempt: int

    @property
    def succeeded(self) -> bool:
        return (not self.failures and self.resources_released
                and self.audit.drained and self.audit.intact)

    @property
    def may_exit(self) -> bool:
        return self.resources_released and self.audit.drained


def emit_lifecycle_event(logger: Any, event: str, **payload: Any) -> None:
    """Keep retry diagnostics out of an already sealed audit writer."""
    snapshot = getattr(logger, "snapshot", None)
    if callable(snapshot) and snapshot().get("closing", False):
        _LOG.warning("Lifecycle event after audit sealed: %s; %s", event, payload.get("error", ""))
        return
    logger.emit(event, **payload)


def _join(worker: threading.Thread | None) -> None:
    if worker is not None and worker is not threading.current_thread():
        worker.join()


def wait_for_mqtt(mqtt: Any) -> None:
    if not mqtt.wait_stopped():
        raise RuntimeError(mqtt.last_error or "MQTT cleanup did not release its owner")


def close_serial(serial: Any, starter: threading.Thread | None) -> None:
    # A start admitted before shutdown may still be opening the driver. Wait
    # for that owner before close, never let a late start reopen after close.
    _join(starter)
    reader = getattr(serial, "_reader", None)
    serial.stop()
    _join(reader)
    if not serial.cleanup_complete:
        raise RuntimeError(serial.last_error or "Serial resource close unconfirmed")


def close_polar(polar: Any) -> None:
    connector = getattr(polar, "_connector_thread", None)
    worker = getattr(polar, "_thread", None)
    stopper = getattr(polar, "_stopper_thread", None)
    polar.stop()
    for owner in (connector, worker, stopper):
        _join(owner)
    if not polar.cleanup_complete:
        raise RuntimeError(polar.last_error or "Robot resource close unconfirmed")


def close_controller_angles(relay: Any) -> None:
    if not relay.wait_closed():
        raise RuntimeError(relay.last_error or "Controller angle close unconfirmed")


def close_video(video: Any) -> None:
    receiver = getattr(video, "_thread", None)
    recovery = getattr(video, "_h265_restart_thread", None)
    video.stop()
    _join(receiver)
    _join(recovery)
    if not video.wait_closed():
        raise RuntimeError(video.last_error or "UDP resource close unconfirmed")


def close_decoder(decoder: Any, recovery: threading.Thread | None) -> None:
    decoder.stop()
    _join(recovery)
    if not decoder.wait_closed():
        raise RuntimeError(decoder.last_error or "Decoder resource close unconfirmed")


def close_device(writer: Any, name: str, logger: Any, timeout: float | None = None) -> None:
    if getattr(writer, "asynchronous", False):
        # close() was requested on the owner thread: DT7.poll() can invoke the
        # safety/input observer and is therefore forbidden on this worker.
        closed = writer.wait_closed(timeout)
        if not closed or not writer.wire_neutral:
            emit_lifecycle_event(logger, "device_shutdown_unconfirmed", device=name,
                        closed=closed, error=writer.last_error)
            raise RuntimeError(f"{name} neutral/close unconfirmed: {writer.last_error}")
    else:
        writer.close()


def close_audit(logger: Any) -> None:
    close = getattr(logger, "close", None)
    if callable(close) and not close(timeout=1.0):
        snapshot = dict(logger.snapshot())
        _LOG.warning("Field audit log shutdown incomplete or records lost: %s", snapshot)
        raise RuntimeError("Field audit log shutdown incomplete or records lost")
