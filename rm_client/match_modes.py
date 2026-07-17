from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


MATCH_MODE_KEYS = ["RMUC7v7", "RMUL3v3"]


@dataclass
class MapHotZone:
    name: str
    x: float
    y: float
    w: float
    h: float
    kind: str


@dataclass
class FieldLayout:
    key: str
    title: str
    asset_path: str
    width_m: float
    height_m: float
    mirror_for_blue: bool = True
    zones: list[MapHotZone] = field(default_factory=list)


@dataclass
class MatchModeConfig:
    key: str
    title: str
    subtitle: str
    duration_sec: int
    field: FieldLayout
    default_right_display: str
    default_map_size: str
    default_module_list_style: str
    enabled_modules: set[str]
    disabled_modules: set[str] = field(default_factory=set)


MATCH_MODES: dict[str, MatchModeConfig] = {
    "RMUC7v7": MatchModeConfig(
        key="RMUC7v7",
        title="RMUC 7v7",
        subtitle="官方 28m x 15m 战场",
        duration_sec=420,
        field=FieldLayout(
            key="RMUC7v7",
            title="RMUC 7v7 Official Map",
            asset_path="assets/maps/rmuc_7v7_official.png",
            width_m=28.0,
            height_m=15.0,
            zones=[
                MapHotZone("Red Base", 1.1, 1.2, 2.1, 2.8, "base"),
                MapHotZone("Blue Base", 24.8, 11.0, 2.1, 2.8, "base"),
                MapHotZone("Red Outpost", 4.9, 6.2, 1.7, 2.0, "outpost"),
                MapHotZone("Blue Outpost", 21.4, 6.8, 1.7, 2.0, "outpost"),
                MapHotZone("Power Rune", 13.3, 6.1, 1.4, 1.8, "rune"),
                MapHotZone("Red Resupply", 0.9, 11.6, 2.7, 2.2, "supply"),
                MapHotZone("Blue Resupply", 24.4, 1.2, 2.7, 2.2, "supply"),
            ],
        ),
        default_right_display="ModuleListHud",
        default_map_size="Compact",
        default_module_list_style="RobotCards",
        enabled_modules={
            "ArmorAutoAim",
            "EnergyAutoAim",
            "SpinMode",
            "CameraFeed",
            "RobotOverlay",
            "RadarOverlay",
            "MapMarker",
            "HUD",
            "TacticalMap",
            "MatchTimer",
            "BaseOutpostStatus",
            "EconomyPanel",
            "ProjectileShop",
            "ThermalPowerPanel",
            "BuffTimeline",
            "ProjectileAllowance",
            "GimbalPower",
            "ChassisGimbalIndicator",
            "PowerLimit",
            "SpeedLimit",
            "Capacitor",
            "FrictionWheel",
            "LockState",
        },
        disabled_modules={"VictoryPointStatus"},
    ),
    "RMUL3v3": MatchModeConfig(
        key="RMUL3v3",
        title="RMUL 3v3",
        subtitle="官方 12m x 8m 战场",
        duration_sec=300,
        field=FieldLayout(
            key="RMUL3v3",
            title="RMUL 3v3 Official Map",
            asset_path="assets/maps/rmul_3v3_official.png",
            width_m=12.0,
            height_m=8.0,
            zones=[
                MapHotZone("Red Supply", 0.0, 0.0, 1.5, 1.7, "supply"),
                MapHotZone("Blue Supply", 10.5, 6.3, 1.5, 1.7, "supply"),
                MapHotZone("Control", 5.0, 3.1, 2.2, 1.8, "control"),
                MapHotZone("Red High", 2.5, 0.8, 2.0, 3.1, "highland"),
                MapHotZone("Blue High", 7.2, 4.1, 2.0, 3.1, "highland"),
            ],
        ),
        default_right_display="ModuleListHud",
        default_map_size="Compact",
        default_module_list_style="StatusBars",
        enabled_modules={
            "ArmorAutoAim",
            "SpinMode",
            "CameraFeed",
            "RobotOverlay",
            "RadarOverlay",
            "MapMarker",
            "HUD",
            "TacticalMap",
            "MatchTimer",
            "VictoryPointStatus",
            "EconomyPanel",
            "ProjectileShop",
            "ThermalPowerPanel",
            "BuffTimeline",
            "ProjectileAllowance",
            "GimbalPower",
            "ChassisGimbalIndicator",
            "PowerLimit",
            "SpeedLimit",
            "Capacitor",
            "FrictionWheel",
            "LockState",
        },
        disabled_modules={"EnergyAutoAim", "BaseOutpostStatus", "AirSupport"},
    ),
}


class MatchModeManager:
    def __init__(self, default: str = "RMUC7v7") -> None:
        self.current = MATCH_MODES.get(default, MATCH_MODES["RMUC7v7"])

    def set_mode(self, key: str) -> MatchModeConfig:
        self.current = MATCH_MODES.get(key, MATCH_MODES["RMUC7v7"])
        return self.current

    def apply_to_registry(self, registry: Any) -> None:
        config = self.current
        self._set_setting(registry, "MatchMode", "Mode", config.key)
        self._set_setting(registry, "TacticalMap", "Map Size", config.default_map_size)
        self._set_setting(registry, "TacticalMap", "Official Map", config.field.title)
        self._set_setting(registry, "HUD", "Right Display", config.default_right_display)
        self._set_setting(registry, "HUD", "ModuleList Style", config.default_module_list_style)
        for module in registry.modules:
            if module.name in config.enabled_modules:
                module.set_enabled(True)
            if module.name in config.disabled_modules:
                module.set_enabled(False)

    def _set_setting(self, registry: Any, module_name: str, setting_name: str, value: object) -> None:
        module = registry.get(module_name)
        setting = module.get_setting(setting_name) if module else None
        if setting is not None:
            setting.value = value
