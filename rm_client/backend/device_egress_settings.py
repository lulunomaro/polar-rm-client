"""Detached device configuration and immutable egress decisions.

Capture named settings once at the registry boundary. Raw scalar values retain
legacy lazy conversion: for example, an invalid CC frequency is not consumed
when CC is disabled or its replacement port is still owned by the angle relay.
Normalized endpoints and permission values never retain a registry, Setting,
control bridge, safety object, or live writer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SettingValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class EgressPermissions:
    module_enabled: bool
    allow_drive: bool
    estopped: bool

    @property
    def may_drive(self) -> bool:
        return bool(not self.estopped and self.allow_drive and self.module_enabled)


@dataclass(frozen=True, slots=True)
class Dt7Endpoint:
    port: str
    real_tx: bool

    @property
    def mock_output(self) -> bool:
        return not self.real_tx


@dataclass(frozen=True, slots=True)
class CustomControllerEndpoint:
    port: str
    max_hz: float
    mock_output: bool

    @property
    def identity(self) -> tuple[str, float, bool]:
        return self.port, self.max_hz, self.mock_output


@dataclass(frozen=True, slots=True)
class Dt7EgressSettings:
    requested_value: SettingValue = False
    port_value: SettingValue = "Auto"
    real_tx_value: SettingValue = False
    spin_value: SettingValue = False
    fire_value: SettingValue = False

    @property
    def requested(self) -> bool:
        return bool(self.requested_value)

    @property
    def spin_allowed(self) -> bool:
        return bool(self.spin_value)

    @property
    def fire_allowed(self) -> bool:
        return bool(self.fire_value)

    def endpoint(self) -> Dt7Endpoint:
        return Dt7Endpoint(str(self.port_value or "Auto"), bool(self.real_tx_value))


@dataclass(frozen=True, slots=True)
class CustomControllerSettings:
    requested_value: SettingValue = False
    port_value: SettingValue = "Auto"
    hz_value: SettingValue = 30
    mock_value: SettingValue = True
    spin_value: SettingValue = False

    @property
    def requested(self) -> bool:
        return bool(self.requested_value)

    @property
    def mock_output(self) -> bool:
        return bool(self.mock_value)

    @property
    def conflict_port(self) -> str:
        # The existing ownership gate distinguishes None/empty from Auto;
        # endpoint creation later applies the historical falsy fallback.
        return str(self.port_value)

    @property
    def spin_allowed(self) -> bool:
        return bool(self.spin_value)

    def endpoint(self) -> CustomControllerEndpoint:
        return CustomControllerEndpoint(
            str(self.port_value or "Auto"), float(self.hz_value or 30), bool(self.mock_value),
        )


@dataclass(frozen=True, slots=True)
class DeviceEgressSettings:
    module_enabled: bool
    allow_drive_value: SettingValue
    dt7: Dt7EgressSettings
    cc: CustomControllerSettings

    @classmethod
    def capture(cls, registry: Any) -> DeviceEgressSettings:
        module = registry.get("RobotLink") if registry is not None else None
        values = {}
        for name in (
            "Direct DT7 Egress", "DT7 Port", "DT7 Real TX", "DT7 Allow Fire Keys",
            "Custom Controller Egress", "CC Port", "CC Hz", "CC Mock Writes",
            "Allow Spin Key", "Allow Drive",
        ):
            setting = module.get_setting(name) if module is not None else None
            if setting is not None:
                values[name] = setting.value
        return cls(
            module_enabled=bool(module is not None and module.enabled),
            allow_drive_value=values.get("Allow Drive", False),
            dt7=Dt7EgressSettings(
                values.get("Direct DT7 Egress", False), values.get("DT7 Port", "Auto"),
                values.get("DT7 Real TX", False), values.get("Allow Spin Key", False),
                values.get("DT7 Allow Fire Keys", False),
            ),
            cc=CustomControllerSettings(
                values.get("Custom Controller Egress", False), values.get("CC Port", "Auto"),
                values.get("CC Hz", 30), values.get("CC Mock Writes", True),
                values.get("Allow Spin Key", False),
            ),
        )
