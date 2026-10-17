"""Detach RobotLink's mutable registry settings at the backend boundary.

The DTOs retain absent/raw values until their existing validation point. This
preserves endpoint precedence, malformed-input behavior and early return order
without making control policy depend on UI setting names or setting objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class _Absent(Enum):
    SETTING = 0


ABSENT = _Absent.SETTING
PORT_DEFAULT = "Auto"
BAUD_DEFAULT = 115200


@dataclass(frozen=True)
class PolarDriveSettings:
    module_enabled: bool
    allow_drive: bool
    chassis_requested: bool
    radio_requested: bool
    speed_percent: Any = ABSENT
    yaw_percent: Any = ABSENT

    def scales(self) -> tuple[float, float]:
        # A structurally incomplete registry must not produce a drive intent.
        # Keep validation lazy: disabled drive never consumes these settings.
        return (_drive_scale(self.speed_percent, "Speed Scale %"),
                _drive_scale(self.yaw_percent, "Yaw Scale %"))


def _drive_scale(value: Any, name: str) -> float:
    if value is ABSENT:
        raise AttributeError(f"RobotLink setting is missing: {name}")
    return float(value or 100) / 100.0


@dataclass(frozen=True)
class PolarGimbalSettings:
    requested: bool
    vision_nuc_occupied: bool
    slew: Any = ABSENT


@dataclass(frozen=True)
class PolarSpinSettings:
    allowed: bool
    module_enabled: bool


@dataclass(frozen=True)
class PolarTransportSettings:
    port: Any = ABSENT
    baud: Any = ABSENT
    mode: Any = ABSENT
    bridge_host: Any = ABSENT
    bridge_port: Any = ABSENT
    mock_writes: Any = ABSENT
    send_hz: Any = ABSENT
    drive_channel: Any = ABSENT

    def explicit_port(self) -> str | None:
        if self.port is ABSENT:
            return None
        port = str(self.port or "").strip()
        return port if port and port != PORT_DEFAULT else None

    def explicit_baud(self) -> int | None:
        if self.baud is ABSENT:
            return None
        try:
            baud = int(self.baud)
        except (TypeError, ValueError):
            return None
        return baud if baud != BAUD_DEFAULT and 1200 <= baud <= 3_000_000 else None

    def bridge_endpoint(self) -> tuple[str, str, int] | None:
        if self.mode is ABSENT:
            return None
        mode = str(self.mode or "serial").strip().lower()
        mode = mode if mode in ("serial", "tcp") else "serial"
        host = "" if self.bridge_host is ABSENT else str(self.bridge_host or "").strip()
        raw_port = 50570 if self.bridge_port is ABSENT else self.bridge_port
        try:
            port = int(raw_port or 50570)
        except (TypeError, ValueError):
            port = 50570
        return mode, host, port

    def normalized_drive_channel(self) -> str | None:
        if self.drive_channel is ABSENT:
            return None
        channel = str(self.drive_channel or "robot_cmd").strip().lower().replace(" ", "_")
        return channel if channel in ("robot_cmd", "virtual_rc", "both") else "robot_cmd"


@dataclass(frozen=True)
class RobotLinkSettings:
    drive: PolarDriveSettings
    gimbal: PolarGimbalSettings
    spin: PolarSpinSettings
    transport: PolarTransportSettings

    @classmethod
    def capture(cls, registry: Any) -> RobotLinkSettings:
        """Read live settings once; no mutable module or setting escapes."""
        module = registry.get("RobotLink") if registry is not None else None
        spin = registry.get("SpinMode") if registry is not None else None
        values = {}
        for name in (
            "Allow Drive", "Client Chassis Drive", "Radio KM Egress",
            "Speed Scale %", "Yaw Scale %", "Client Gimbal Control",
            "Vision NUC Occupied", "Gimbal Slew", "Allow Spin Key",
            "Port", "Baud", "Link Mode", "Bridge Host", "Bridge Port",
            "Mock Writes", "Send Hz", "Drive Channel",
        ):
            setting = module.get_setting(name) if module is not None else None
            if setting is not None:
                values[name] = setting.value
        return cls(
            drive=PolarDriveSettings(
                module_enabled=bool(module is not None and module.enabled),
                allow_drive=bool(values.get("Allow Drive", False)),
                chassis_requested=bool(values.get("Client Chassis Drive", False)),
                radio_requested=bool(values.get("Radio KM Egress", False)),
                speed_percent=values.get("Speed Scale %", ABSENT),
                yaw_percent=values.get("Yaw Scale %", ABSENT),
            ),
            gimbal=PolarGimbalSettings(
                requested=bool(values.get("Client Gimbal Control", False)),
                vision_nuc_occupied=bool(values.get("Vision NUC Occupied", True)),
                slew=values.get("Gimbal Slew", ABSENT),
            ),
            spin=PolarSpinSettings(
                allowed=bool(values.get("Allow Spin Key", False)),
                module_enabled=bool(spin is not None and spin.enabled),
            ),
            transport=PolarTransportSettings(
                port=values.get("Port", ABSENT), baud=values.get("Baud", ABSENT),
                mode=values.get("Link Mode", ABSENT),
                bridge_host=values.get("Bridge Host", ABSENT),
                bridge_port=values.get("Bridge Port", ABSENT),
                mock_writes=values.get("Mock Writes", ABSENT),
                send_hz=values.get("Send Hz", ABSENT),
                drive_channel=values.get("Drive Channel", ABSENT),
            ),
        )
