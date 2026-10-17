"""Capture backend owners and preserve ordered, owner-thread neutral admission."""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable

from ..lifecycle import TeardownFailure, TeardownTask
from .lifecycle import (close_controller_angles, close_decoder, close_device,
                        close_polar, close_serial, close_video, wait_for_mqtt)

BACKEND_RESOURCE_NAMES = frozenset({
    "mqtt", "udp", "serial", "polar", "native-decoder", "controller-angles", "DT7", "CC",
})


@dataclass(frozen=True, slots=True)
class PreparedTeardown:
    tasks: tuple[TeardownTask, ...]
    failures: tuple[TeardownFailure, ...]
    estop_at: float


@dataclass(frozen=True, slots=True)
class TeardownResources:
    """Captured transport owners, not a copy of their mutable handle fields.

    Strict close adapters join captured starters/recovery workers and inspect
    the retained transport owners afterward. A native handle published late by
    an already admitted worker therefore remains part of this teardown.
    """

    safety: Any
    mqtt: Any
    udp: Any
    serial: Any
    polar: Any
    native_decoder: Any
    logger: Any
    serial_starter: threading.Thread | None = None
    native_recovery: threading.Thread | None = None
    controller_angles: Any = None
    dt7: Any = None
    cc: Any = None

    @property
    def names(self) -> tuple[str, ...]:
        names = ["mqtt", "udp", "serial", "polar", "native-decoder"]
        if self.controller_angles is not None:
            names.append("controller-angles")
        names.extend(name for name, owner in (("DT7", self.dt7), ("CC", self.cc)) if owner is not None)
        return tuple(names)

    def prepare(self, invalidate_runtime: Callable[[], None], *,
                device_wait_timeout: float | None = None) -> PreparedTeardown:
        """Run only on the owner: authority first, neutral admission, then jobs."""
        failures = []
        def neutralize(name: str, action: Callable[[], Any]) -> None:
            try:
                action()
            except Exception as exc:
                failures.append(TeardownFailure(name, f"{type(exc).__name__}: {exc}"))
        neutralize("safety", self.safety.trigger_estop)
        estop_at = self.safety.estop_at
        neutralize("serial authority", self.serial.request_estop)
        neutralize("robot authority", self.polar.request_estop)
        if self.controller_angles is not None:
            neutralize("controller angles", self.controller_angles.close)
        if self.mqtt.connected and not self.mqtt.mock_output:
            neutralize("MQTT neutral", self.mqtt.publish_estop_zero)
        neutralize("MQTT invalidate", lambda: self.mqtt.stop(publish_status=False, wait=False))
        neutralize("runtime invalidation", invalidate_runtime)
        tasks = [
            TeardownTask("mqtt", lambda: wait_for_mqtt(self.mqtt),
                         lambda: self.mqtt.stop(publish_status=False, wait=False)),
            TeardownTask("udp", lambda: close_video(self.udp)),
            TeardownTask("serial", lambda: close_serial(self.serial, self.serial_starter)),
            TeardownTask("polar", lambda: close_polar(self.polar)),
            TeardownTask("native-decoder", lambda: close_decoder(self.native_decoder, self.native_recovery)),
        ]
        if self.controller_angles is not None:
            tasks.append(TeardownTask("controller-angles", lambda: close_controller_angles(self.controller_angles)))
        for name, writer in (("DT7", self.dt7), ("CC", self.cc)):
            if writer is None:
                continue
            asynchronous = getattr(writer, "asynchronous", False)
            if asynchronous:
                neutralize(name + " close request", writer.close)
            tasks.append(TeardownTask(name,
                lambda writer=writer, name=name: close_device(writer, name, self.logger, device_wait_timeout),
                writer.close if asynchronous else None))
        return PreparedTeardown(tuple(tasks), tuple(failures), estop_at)
