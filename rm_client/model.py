from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import time
from typing import Any, Callable

from .match_modes import MATCH_MODE_KEYS
from .scenarios import SCENARIO_MODES


CATEGORIES = ["Combat", "Movement", "Vision", "Strategy", "System", "Render"]

GUI_MODES = [
    "PolarOld",
    "PolarPanel",
    "PolarNew",
    "PolarDropdown",
]

HUD_MODES = [
    "ModuleListHud",
    "TacticalMapHud",
    "KeyBindsHud",
    "RobotListHud",
    "TargetHud",
    "StatusCardsHud",
    "RMIndicatorsHud",
]

MODULE_LIST_STYLES = [
    "PolarLegacy",
    "RainbowRight",
    "GlowRows",
    "Segmented",
    "DynamicIsland",
    "KeyBindsRows",
    "StatusBars",
    "RobotCards",
    "LiquidArrayList",
    "LiquidArrayTags",
    "LiquidArrayCompact",
    "LiquidArrayDock",
]

TACTICAL_MAP_STATUS_DISPLAYS = [
    "LiquidAuto",
    "LiquidBars",
    "LiquidCards",
    "LegacyOrbit",
    "LegacyBars",
    "Off",
]


def normalize_angle_deg(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


@dataclass
class Setting:
    name: str
    kind: str
    value: Any
    min_value: float | None = None
    max_value: float | None = None
    step: float = 1.0
    options: list[str] = field(default_factory=list)
    unit: str = ""
    description: str = ""

    def ratio(self) -> float:
        if self.kind != "number" or self.min_value is None or self.max_value is None:
            return 0.0
        span = self.max_value - self.min_value
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (float(self.value) - self.min_value) / span))

    def set_from_ratio(self, ratio: float) -> None:
        if self.kind != "number" or self.min_value is None or self.max_value is None:
            return
        ratio = max(0.0, min(1.0, ratio))
        raw = self.min_value + (self.max_value - self.min_value) * ratio
        if self.step > 0:
            raw = round(raw / self.step) * self.step
        self.value = max(self.min_value, min(self.max_value, raw))

    def cycle(self, direction: int = 1) -> None:
        if self.kind != "mode" or not self.options:
            return
        try:
            idx = self.options.index(str(self.value))
        except ValueError:
            idx = 0
        self.value = self.options[(idx + direction) % len(self.options)]

    def display_value(self) -> str:
        if self.kind == "number":
            number = float(self.value)
            if self.step >= 1:
                text = str(int(round(number)))
            else:
                text = f"{number:.2f}".rstrip("0").rstrip(".")
            return f"{text}{self.unit}"
        if self.kind == "bool":
            return "ON" if self.value else "OFF"
        return str(self.value)


@dataclass
class Module:
    name: str
    category: str
    keybind: str = "None"
    settings: list[Setting] = field(default_factory=list)
    enabled: bool = False
    description: str = ""
    bind_capture_hint: str = "右键模块或使用绑定行"

    def toggle(self) -> None:
        self.enabled = not self.enabled
        if self.enabled:
            self.on_enable()
        else:
            self.on_disable()

    def set_enabled(self, enabled: bool) -> None:
        if self.enabled == enabled:
            return
        self.toggle()

    def get_setting(self, name: str) -> Setting | None:
        for setting in self.settings:
            if setting.name == name:
                return setting
        return None

    def on_enable(self) -> None:
        pass

    def on_disable(self) -> None:
        pass

    def on_tick(self, state: "RobotState", dt: float) -> None:
        pass

    def on_frame(self, frame: Any) -> None:
        pass

    def on_message(self, topic: str, payload: bytes) -> None:
        pass


class ModuleRegistry:
    def __init__(self, modules: list[Module]) -> None:
        self.modules = modules
        self._by_name = {module.name: module for module in modules}
        self._toggle_listeners: list[Callable[[Module, bool, str], None]] = []

    def get(self, name: str) -> Module | None:
        return self._by_name.get(name)

    def by_category(self, category: str) -> list[Module]:
        return sorted(
            [module for module in self.modules if module.category == category],
            key=lambda module: module.name.lower(),
        )

    def enabled_modules(self) -> list[Module]:
        return [module for module in self.modules if module.enabled]

    def add_toggle_listener(self, listener: Callable[[Module, bool, str], None]) -> None:
        if listener not in self._toggle_listeners:
            self._toggle_listeners.append(listener)

    def toggle_module(self, module: Module | str, source: str = "internal") -> Module | None:
        target = self.get(module) if isinstance(module, str) else module
        if target is None:
            return None
        target.toggle()
        for listener in tuple(self._toggle_listeners):
            listener(target, target.enabled, source)
        return target

    def tick(self, state: "RobotState", dt: float) -> None:
        for module in self.modules:
            if module.enabled:
                module.on_tick(state, dt)


class GuiModeManager:
    def __init__(self, initial: str = "PolarPanel") -> None:
        self.mode = initial if initial in GUI_MODES else GUI_MODES[1]

    def set_mode(self, mode: str) -> None:
        if mode in GUI_MODES:
            self.mode = mode

    def next_mode(self) -> str:
        idx = GUI_MODES.index(self.mode)
        self.mode = GUI_MODES[(idx + 1) % len(GUI_MODES)]
        return self.mode


@dataclass
class RobotState:
    match_mode: str = "RMUC7v7"
    match_title: str = "RMUC 7v7"
    match_duration_sec: int = 420
    alliance_color: str = "Red"
    current_stage: str = "MATCH"
    stage_countdown_sec: int = 420
    stage_elapsed_sec: int = 0
    current_round: int = 1
    total_rounds: int = 1
    red_score: int = 0
    blue_score: int = 0
    ally_victory_points: int = 200
    enemy_victory_points: int = 200
    control_owner: str = "NEUTRAL"
    control_progress: int = 0
    control_countdown_sec: int = 0
    current_health: int = 520
    max_health: int = 600
    current_heat: float = 42.0
    max_heat: float = 240.0
    current_chassis_energy: int = 58
    max_chassis_energy: int = 100
    current_buffer_energy: int = 41
    max_buffer_energy: int = 100
    chassis_power_w: float = 58.0
    heat_cooldown_rate: float = 18.0
    remaining_ammo: int = 180
    total_projectiles_fired: int = 12
    remaining_economy: int = 260
    total_economy_obtained: int = 620
    tech_level: int = 1
    encryption_level: int = 0
    robot_id: int = 3
    robot_type: str = "Infantry"
    level: int = 2
    target_id: int = 103
    target_distance_m: float = 12.8
    target_health: int = 320
    target_max_health: int = 450
    target_locked: bool = True
    energy_target_locked: bool = False
    energy_target_distance_m: float = 18.5
    energy_rune_phase: str = "SMALL"
    energy_rune_progress: int = 48
    energy_rune_rotation: str = "CW"
    gimbal_power: int = 24
    power_limit: int = 80
    speed_limit: int = 420
    chassis_yaw_deg: float = 0.0
    gimbal_yaw_deg: float = 0.0
    relative_yaw_deg: float = 0.0
    capacitor_percent: int = 62
    friction_wheel: bool = True
    lock_state: bool = True
    small_buff: bool = True
    large_buff: bool = False
    anti_rotation: bool = True
    projectile_allowance_17mm: int = 180
    projectile_allowance_42mm: int = 12
    projectile_shop_state: str = "READY"
    projectile_shop_price: int = 50
    base_health: int = 5000
    base_max_health: int = 5000
    base_status: str = "SHIELDED"
    base_shield: int = 100
    enemy_base_health: int = 5000
    enemy_base_max_health: int = 5000
    enemy_base_status: str = "SHIELDED"
    enemy_base_shield: int = 100
    outpost_health: int = 1500
    outpost_max_health: int = 1500
    outpost_status: str = "ONLINE"
    enemy_outpost_health: int = 1500
    enemy_outpost_max_health: int = 1500
    enemy_outpost_status: str = "ONLINE"
    jump_ready: bool = False
    pte_ready: bool = True
    wheel_online: bool = True
    les_online: bool = True
    robot_rows: list[dict[str, Any]] = field(default_factory=list)
    map_robots: list[dict[str, Any]] = field(default_factory=list)
    map_markers: list[dict[str, Any]] = field(default_factory=list)
    buff_rows: list[dict[str, Any]] = field(default_factory=list)
    mqtt_connected: bool = False
    udp_video_connected: bool = False
    serial_connected: bool = False
    camera_connected: bool = False
    vision_backend: str = "Mock"
    vision_connected: bool = False
    vision_calibrated: bool = False
    vision_tracking_state: str = "mock"
    vision_inference_device: str = "mock"
    vision_inference_fps: float = 0.0
    vision_latency_ms: float = 0.0
    vision_sequence: int = 0
    vision_detection_count: int = 0
    vision_prediction_count: int = 0
    vision_aim_yaw_deg: float = 0.0
    vision_aim_pitch_deg: float = 0.0
    official_feed_at: float = 0.0
    official_robot_health: list[int] = field(default_factory=list)
    official_events_online: bool = False
    # gestalt=游戏 Attr 真源；fallback=无真源时 Lab 模拟
    telemetry_source: str = "fallback"
    gestalt_attrs_at: float = 0.0
    control_inject_status: str = "disabled"
    kill_streak: int = 0
    visual_kill_count: int = 0
    referee_kill_count: int = 0
    vision_scene_luminance: float = 0.0
    fps: float = 0.0
    latency_ms: int = 36
    last_event: str = "Armor target acquired"
    events: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    start_time: float = field(default_factory=time.perf_counter)

    def mark_gestalt_telemetry(self) -> None:
        self.telemetry_source = "gestalt"
        self.gestalt_attrs_at = time.perf_counter()
        self.official_events_online = True
        self.official_feed_at = self.gestalt_attrs_at

    def gestalt_telemetry_fresh(self, timeout_s: float = 3.5) -> bool:
        return self.gestalt_attrs_at > 0.0 and (time.perf_counter() - self.gestalt_attrs_at) <= timeout_s

    def tick(self, dt: float) -> None:
        elapsed = time.perf_counter() - self.start_time
        use_fallback = self.telemetry_source != "gestalt" or not self.gestalt_telemetry_fresh()
        if use_fallback and self.telemetry_source == "gestalt":
            self.telemetry_source = "fallback"
            self.official_events_online = False
        if not use_fallback:
            # 真源托管：绝不覆盖 heat/base/outpost/power/ammo/economy/map_robots/云台角
            # 位姿与小地图点由 gestalt_attrs 写入 WorldPos/ChassisYaw/TurretYaw
            return
        duration = max(1, int(self.match_duration_sec))
        self.stage_countdown_sec = max(0, duration - int(elapsed) % (duration + 1))
        self.stage_elapsed_sec = max(0, duration - self.stage_countdown_sec)
        # fallback 假波形：仅无游戏继电器时
        self.current_heat = 88 + 58 * math.sin(elapsed * 0.9)
        self.current_chassis_energy = int(55 + 25 * math.sin(elapsed * 0.55))
        self.current_buffer_energy = int(38 + 18 * math.cos(elapsed * 0.42))
        self.chassis_power_w = max(0.0, 58.0 + 18.0 * math.sin(elapsed * 0.64))
        self.heat_cooldown_rate = 16.0 + 4.0 * math.cos(elapsed * 0.32)
        self.capacitor_percent = max(0, min(100, int(62 + 28 * math.sin(elapsed * 0.35))))
        self.gimbal_power = 24 if int(elapsed) % 11 < 9 else 0
        self.speed_limit = int(380 + 80 * math.sin(elapsed * 0.5))
        self.chassis_yaw_deg = normalize_angle_deg(elapsed * 24.0 + 12.0 * math.sin(elapsed * 0.42))
        self.gimbal_yaw_deg = normalize_angle_deg(elapsed * 11.0 + 42.0 * math.sin(elapsed * 0.28 + 0.8))
        self.relative_yaw_deg = normalize_angle_deg(self.gimbal_yaw_deg - self.chassis_yaw_deg)
        self.friction_wheel = int(elapsed) % 8 < 6
        self.lock_state = int(elapsed * 2) % 7 < 5
        self.small_buff = int(elapsed) % 13 < 7
        self.large_buff = int(elapsed) % 17 < 5
        self.anti_rotation = int(elapsed) % 10 < 8
        self.jump_ready = int(elapsed) % 12 < 3
        self.pte_ready = int(elapsed) % 9 < 6
        self.wheel_online = int(elapsed) % 15 != 0
        self.les_online = int(elapsed) % 21 != 0
        self.target_distance_m = 10.0 + 3.0 * math.sin(elapsed * 0.4)
        self.energy_target_distance_m = 16.0 + 4.0 * math.cos(elapsed * 0.28)
        self.energy_target_locked = int(elapsed * 1.6) % 9 < 6
        self.energy_rune_phase = "LARGE" if int(elapsed) % 34 > 16 else "SMALL"
        self.energy_rune_progress = max(0, min(100, int(52 + 38 * math.sin(elapsed * 0.62))))
        self.energy_rune_rotation = "CCW" if int(elapsed / 4) % 2 else "CW"
        self.latency_ms = int(28 + 12 * abs(math.sin(elapsed * 0.7)))
        self.remaining_ammo = max(0, int(180 - (self.stage_elapsed_sec * 0.18) % 80))
        self.projectile_allowance_17mm = max(0, int(180 - (self.stage_elapsed_sec * 0.23) % 120))
        self.projectile_allowance_42mm = max(0, int(12 - (self.stage_elapsed_sec * 0.035) % 8))
        self.remaining_economy = max(0, int(260 + 80 * math.sin(elapsed * 0.16)))
        self.total_economy_obtained = 620 + int(self.stage_elapsed_sec * 1.7)
        self.tech_level = 1 + (int(elapsed / 38) % 3)
        self.encryption_level = int(elapsed / 55) % 2
        self.projectile_shop_state = "READY" if self.remaining_economy >= self.projectile_shop_price else "WAIT"
        if self.match_mode == "RMUL3v3":
            self.match_title = "RMUL 3v3"
            self.match_duration_sec = 300
            self.base_health = 0
            self.enemy_base_health = 0
            self.outpost_health = 0
            self.enemy_outpost_health = 0
            self.ally_victory_points = max(0, 200 - int(max(0.0, math.sin(elapsed * 0.11)) * 48))
            self.enemy_victory_points = max(0, 200 - int(max(0.0, math.cos(elapsed * 0.09)) * 54))
            self.control_owner = "ALLY" if int(elapsed / 11) % 3 == 0 else ("ENEMY" if int(elapsed / 11) % 3 == 1 else "NEUTRAL")
            self.control_progress = int((0.5 + 0.5 * math.sin(elapsed * 0.25)) * 100)
            self.control_countdown_sec = max(0, 20 - int(elapsed) % 21)
            self.map_robots = [
                {"id": 1, "team": "ally", "name": "Hero", "x": 2.2 + 0.35 * math.sin(elapsed * 0.18), "y": 2.1, "hp": 720, "max": 800, "highlight": False},
                {"id": 3, "team": "ally", "name": "Infantry", "x": 3.4, "y": 3.8 + 0.35 * math.cos(elapsed * 0.22), "hp": self.current_health, "max": self.max_health, "highlight": True},
                {"id": 7, "team": "ally", "name": "Sentry", "x": 1.4, "y": 6.4, "hp": 520, "max": 600, "highlight": False},
                {"id": 103, "team": "enemy", "name": "Infantry", "x": 8.4 + 0.35 * math.sin(elapsed * 0.24), "y": 5.7, "hp": self.target_health, "max": self.target_max_health, "highlight": self.target_locked},
                {"id": 104, "team": "enemy", "name": "Infantry", "x": 9.5, "y": 3.2 + 0.28 * math.cos(elapsed * 0.19), "hp": 410, "max": 600, "highlight": False},
            ]
            self.map_markers = [
                {"label": "CTRL", "x": 6.0, "y": 4.0, "kind": "control", "pulse": True},
                {"label": "SUP", "x": 10.8, "y": 6.8, "kind": "supply", "pulse": int(elapsed) % 8 < 4},
            ]
        else:
            self.match_mode = "RMUC7v7"
            self.match_title = "RMUC 7v7"
            self.match_duration_sec = 420
            self.red_score = int(self.stage_elapsed_sec * 0.08)
            self.blue_score = int(self.stage_elapsed_sec * 0.07)
            self.base_health = max(0, 5000 - int(self.stage_elapsed_sec * 1.2))
            self.enemy_base_health = max(0, 5000 - int(self.stage_elapsed_sec * 2.4))
            self.outpost_health = max(0, 1500 - int(max(0.0, self.stage_elapsed_sec - 80) * 3.2))
            self.enemy_outpost_health = max(0, 1500 - int(max(0.0, self.stage_elapsed_sec - 45) * 5.0))
            self.base_status = "SHIELDED" if self.outpost_health > 0 else "EXPOSED"
            self.enemy_base_status = "SHIELDED" if self.enemy_outpost_health > 0 else "EXPOSED"
            self.outpost_status = "ONLINE" if self.outpost_health > 0 else "DOWN"
            self.enemy_outpost_status = "ONLINE" if self.enemy_outpost_health > 0 else "DOWN"
            self.base_shield = 100 if self.base_status == "SHIELDED" else 0
            self.enemy_base_shield = 100 if self.enemy_base_status == "SHIELDED" else 0
            self.map_robots = [
                {"id": 1, "team": "ally", "name": "Hero", "x": 5.2 + 0.8 * math.sin(elapsed * 0.15), "y": 3.4, "hp": 720, "max": 800, "highlight": False},
                {"id": 3, "team": "ally", "name": "Infantry", "x": 8.2, "y": 7.6 + 0.7 * math.cos(elapsed * 0.19), "hp": self.current_health, "max": self.max_health, "highlight": True},
                {"id": 4, "team": "ally", "name": "Infantry", "x": 10.4, "y": 10.8, "hp": 520, "max": 600, "highlight": False},
                {"id": 7, "team": "ally", "name": "Sentry", "x": 3.6, "y": 12.4, "hp": 530, "max": 600, "highlight": False},
                {"id": 101, "team": "enemy", "name": "Hero", "x": 22.2, "y": 11.7 + 0.5 * math.sin(elapsed * 0.13), "hp": 640, "max": 800, "highlight": False},
                {"id": 103, "team": "enemy", "name": "Infantry", "x": 19.4 + 0.7 * math.sin(elapsed * 0.21), "y": 7.2, "hp": self.target_health, "max": self.target_max_health, "highlight": self.target_locked},
                {"id": 104, "team": "enemy", "name": "Infantry", "x": 17.8, "y": 4.9 + 0.5 * math.cos(elapsed * 0.17), "hp": 410, "max": 600, "highlight": False},
            ]
            self.map_markers = [
                {"label": "RUNE", "x": 14.0, "y": 7.5, "kind": "rune", "pulse": self.energy_target_locked},
                {"label": "BASE", "x": 25.8, "y": 12.4, "kind": "attack", "pulse": self.enemy_base_status == "EXPOSED"},
            ]
        self.buff_rows = [
            {"name": "Cooldown", "active": self.small_buff, "remaining": max(0, 26 - int(elapsed) % 27)},
            {"name": "Damage", "active": self.large_buff, "remaining": max(0, 18 - int(elapsed) % 19)},
            {"name": "AntiSpin", "active": self.anti_rotation, "remaining": max(0, 13 - int(elapsed) % 14)},
        ]
        self.robot_rows = [
            {"id": 1, "name": "Hero", "hp": int(620 + 120 * math.sin(elapsed * 0.31)), "max": 800, "state": "ALIVE"},
            {"id": 3, "name": "Infantry", "hp": self.current_health, "max": self.max_health, "state": "LOCAL"},
            {"id": 4, "name": "Infantry", "hp": int(450 + 80 * math.cos(elapsed * 0.23)), "max": 600, "state": "ALIVE"},
            {"id": 7, "name": "Sentry", "hp": int(360 + 90 * math.sin(elapsed * 0.19)), "max": 600, "state": "PATROL"},
        ]
        if int(elapsed) % 19 == 0:
            self.push_event("Radar highlight refreshed")

    def push_event(self, event: str) -> None:
        if self.events and self.events[-1] == event:
            return
        self.last_event = event
        self.events.append(event)


def bool_setting(name: str, value: bool, description: str = "") -> Setting:
    return Setting(name=name, kind="bool", value=value, description=description)


def number_setting(
    name: str,
    value: float,
    min_value: float,
    max_value: float,
    step: float,
    unit: str = "",
    description: str = "",
) -> Setting:
    return Setting(
        name=name,
        kind="number",
        value=value,
        min_value=min_value,
        max_value=max_value,
        step=step,
        unit=unit,
        description=description,
    )


def mode_setting(
    name: str,
    value: str,
    options: list[str],
    description: str = "",
) -> Setting:
    return Setting(
        name=name,
        kind="mode",
        value=value,
        options=options,
        description=description,
    )


def action_setting(name: str, description: str = "") -> Setting:
    return Setting(name=name, kind="action", value=name, description=description)


def build_default_registry() -> ModuleRegistry:
    modules = [
        Module(
            "Scenario",
            "System",
            "None",
            [
                mode_setting("Mode", "LabDebug", SCENARIO_MODES),
                bool_setting("Show Startup", True),
                mode_setting("Startup Style", "LiquidMenu", ["LiquidMenu", "PolarLauncher"]),
                bool_setting("Mock Writes", True, "默认禁用真实串口和机器人输出。"),
                bool_setting("Official Limits", False, "应用官方频率和载荷限制。"),
            ],
            enabled=True,
            description="启动场景和连接配置选择器。",
        ),
        Module(
            "StartupVisual",
            "System",
            "None",
            [
                mode_setting("Background Mode", "VideoParallax", ["PkgScene", "VideoParallax", "ParticleGrid", "Static"]),
                mode_setting("Wallpaper Profile", "TexasMP4", ["TexasMP4", "VendingMachine4K", "Default3000925581Pkg", "ParticleGrid"]),
                mode_setting("Motion Quality", "Balanced30", ["Smooth60", "Balanced30", "Source"]),
                mode_setting("Glass Style", "LiquidGlass", ["LiquidGlass", "Legacy"]),
                number_setting("Glass Strength", 1.00, 0.40, 1.40, 0.05),
                number_setting("Parallax Strength", 22, 0, 60, 1, "px"),
                number_setting("Background Zoom", 1.00, 1.0, 1.35, 0.01),
                number_setting("Dim Overlay", 0.28, 0.0, 0.8, 0.01),
                action_setting("Import Wallpaper", "导入 MP4/GIF/图片壁纸到客户端壁纸目录。"),
                action_setting("Reset Wallpaper", "恢复默认德克萨斯视频壁纸。"),
            ],
            enabled=True,
            description="启动页背景、pkg 壁纸、MP4 导入、视差和暗角管理。",
        ),
        Module(
            "MatchMode",
            "System",
            "None",
            [
                mode_setting("Mode", "RMUC7v7", MATCH_MODE_KEYS),
                mode_setting("Alliance", "Red", ["Red", "Blue"]),
                bool_setting("Second Step", True, "场景选择后显示 RMUC/RMUL 二级选择页。"),
            ],
            enabled=True,
            description="比赛制式选择器，用于切换官方地图、HUD 默认值和协议状态映射。",
        ),
        Module(
            "AllianceProfile",
            "System",
            "None",
            [
                mode_setting("Side", "Red", ["Red", "Blue"]),
                bool_setting("Mirror Tactical Map", True),
            ],
            enabled=True,
            description="本机阵营档案。统一驱动战术地图、雷达、ESP 与自瞄的红蓝敌我关系。",
        ),
        Module(
            "ConfigManager",
            "System",
            "None",
            [
                mode_setting("Profile", "default", ["default"], "configs/ 目录中的客户端配置文件。"),
                bool_setting("Auto Save", True),
                bool_setting("Load On Start", True),
                action_setting("Save Current", "保存当前模块、按键绑定、GUI、HUD 和布局。"),
                action_setting("Reload Profile", "重新读取当前选择的配置文件。"),
                action_setting("Import Config", "导入外部 .json 配置到 configs/。"),
            ],
            enabled=True,
            description="本地客户端配置保存、加载和导入管理器。",
        ),
        Module(
            "ArmorAutoAim",
            "Combat",
            "R",
            [
                bool_setting("Mock Output", True, "云台输出保持在模拟模式。"),
                number_setting("Fov", 85, 10, 180, 1, "deg"),
                number_setting("Smooth", 0.28, 0.01, 1.0, 0.01),
                mode_setting("Priority", "Armor", ["Armor", "Distance", "Health", "Radar"]),
                mode_setting("Lock Style", "White Outline", ["Polar Corner", "White Outline", "Minimal Bracket", "Rune Ring"]),
                bool_setting("Draw Fov", True),
            ],
            enabled=True,
            description="R：开关自瞄。只画外挂回传的框，并把开关经 UDP:9872 发给完整同济；Lab 下 AA 开时可跟游戏视角。",
        ),
        Module(
            "EliminationFX",
            "Render",
            "None",
            [
                mode_setting("Preset", "Default Scrolling", [
                    "Default Scrolling", "Combo", "Card", "Battlefield 1", "Minimal", "PUBG Subtitle",
                    "Battlefield 5", "Total Battlefield", "Valorant Prime", "Valorant Glitchpop",
                    "Valorant Singularity V1", "Valorant Singularity V2", "Valorant Singularity V3",
                    "Valorant Gaia", "Valorant Gaia V1", "Valorant Gaia V2", "Valorant Gaia V3",
                    "Valorant Bubblegum", "Valorant Bubblegum V1", "Valorant Bubblegum V2", "Valorant Bubblegum V3",
                    "Valorant Champions 2021", "Valorant Prelude V1", "Valorant Prelude V2", "Valorant Prelude V3",
                    "Valorant Primordium", "Valorant Primordium V1", "Valorant Primordium V2", "Valorant Primordium V3",
                    "Valorant Radiant Crisis 001", "Valorant RGX", "Valorant RGX V1", "Valorant RGX V2", "Valorant RGX V3",
                ]),
                mode_setting("Element Visibility", "Preset", ["Preset", "Full HUD", "Icons Only"]),
                mode_setting("Team Theme", "Alliance", ["Alliance", "CT Blue", "T Gold"]),
                bool_setting("Sound Enabled", True),
                number_setting("Sound Volume", 55, 0, 100, 1, "%"),
                number_setting("Scale", 1.0, 0.45, 2.0, 0.05),
                mode_setting("Responsive Scale", "Auto", ["Auto", "Fixed"]),
                mode_setting("Anchor Policy", "Combat Focus", ["Combat Focus", "Original Bottom", "Manual"]),
                number_setting("X Offset", 0, -800, 800, 1, "px"),
                number_setting("Y Offset", 0, -500, 500, 1, "px"),
                number_setting("Combo Window", 5.0, 1.0, 12.0, 0.5, "s"),
                mode_setting("Evidence Mode", "Auto", ["Auto", "Referee Only", "Vision Only"]),
                mode_setting("Preview Sequence", "Normal 1-6", ["Normal 1-6", "All Kill Types", "Single"]),
                action_setting("Preview FX", "按当前官方预设播放完整预览序列。"),
                action_setting("Test Elimination", "注入一条本地测试击杀事件。"),
            ],
            enabled=True,
            description="GD656 击杀图标、音效与官方/视觉双轨战果显示。",
        ),
        Module(
            "AudioFeedback",
            "System",
            "None",
            [
                bool_setting("Enabled", True, "客户端交互音效总开关。"),
                bool_setting("Module Toggle Sounds", True, "点击或按键切换模块时播放反馈音。"),
                mode_setting("Sound Theme", "Polar", ["Polar", "Zen", "LiquidBounce"]),
                number_setting("UI Volume", 45, 0, 100, 1, "%"),
                action_setting("Preview Enable", "试听模块开启音效。"),
                action_setting("Preview Disable", "试听模块关闭音效。"),
                action_setting("Preview Invalid", "试听无效操作提示音。"),
            ],
            enabled=True,
            description="统一管理模块切换和界面交互音效。",
        ),
        Module(
            "EnergyAutoAim",
            "Combat",
            "G",
            [
                bool_setting("Mock Output", True, "能量机关输出保持在模拟模式。"),
                mode_setting("Rune Type", "Auto", ["Auto", "Small", "Large"]),
                mode_setting("Predict", "Adaptive", ["Off", "Fixed", "Adaptive"]),
                number_setting("Lead Time", 0.18, 0.0, 0.6, 0.01, "s"),
                number_setting("Fov", 58, 10, 140, 1, "deg"),
                mode_setting("Lock Style", "Rune Ring", ["Polar Corner", "White Outline", "Minimal Bracket", "Rune Ring"]),
                bool_setting("Show Progress", True),
            ],
            description="7v7 能量机关自瞄 UI，并预留打符预测接口。",
        ),
        Module(
            "ArmorLock",
            "Combat",
            "B",
            [
                mode_setting("Lock Point", "Center", ["Center", "Top", "Predict"]),
                number_setting("Predict", 0.12, 0.0, 0.5, 0.01, "s"),
                bool_setting("Heat Guard", True),
            ],
            description="装甲板目标选择和锁定策略预留模块。",
        ),
        Module(
            "FireControlStub",
            "Combat",
            "None",
            [
                bool_setting("Safety Lock", True),
                number_setting("Max Rate", 8, 1, 20, 1, "Hz"),
                mode_setting("Mode", "Manual", ["Manual", "Confirm", "Disabled"]),
            ],
            description="不输出真实发射命令，仅保留接口占位。",
        ),
        Module(
            "SpinMode",
            "Movement",
            "V",
            [
                mode_setting("Direction", "Auto", ["Auto", "CW", "CCW"]),
                number_setting("Spin Speed", 420, 120, 720, 10, "dps"),
                bool_setting("Power Guard", True),
            ],
            enabled=True,
            description="V：client 小陀螺。开启时向游戏注入 R（游戏小陀螺键）；L-Shift 为游戏加速。",
        ),
        Module(
            "ChassisAssist",
            "Movement",
            "C",
            [
                number_setting("Power Limit", 70, 30, 120, 1, "W"),
                bool_setting("Buffer Protect", True),
                mode_setting("Follow", "Gimbal", ["Gimbal", "Field", "Free"]),
            ],
            description="底盘辅助模块，预留串口和控制输出钩子。",
        ),
        Module(
            "PowerGuard",
            "Movement",
            "None",
            [
                number_setting("Heat Warn", 180, 60, 240, 1),
                number_setting("Energy Warn", 20, 0, 100, 1, "%"),
                bool_setting("Auto Limit", False),
            ],
            description="底盘与发射机构功率、热量保护辅助模块。",
        ),
        Module(
            "CameraFeed",
            "Vision",
            "None",
            [
                mode_setting(
                    "Source",
                    "Gestalt SHM",
                    [
                        "Gestalt SHM",
                        "OBS VirtualCam",
                        "Webcam",
                        "Shark UDP",
                        "UDP HEVC",
                        "Synthetic",
                    ],
                    "Gestalt SHM：游戏 VisionBridge 共享内存（需 -visionbridge）。OBS/Webcam 为设备图传。",
                ),
                number_setting("Camera ID", 0, 0, 10, 1, "仅 OBS/Webcam：设备号。"),
                number_setting("WS Port", 0, 0, 65535, 1, "Gestalt SHM：0=自动 *.wsport/进程。"),
                number_setting("Game PID", 0, 0, 999999, 1, "Gestalt SHM：0=自动；可强制游戏 PID。"),
                number_setting("Capture Width", 960, 320, 1920, 16, "px（设备源缩放；SHM 见 Display Max Width）"),
                number_setting("Capture Height", 540, 240, 1080, 16, "px"),
                number_setting(
                    "Display Max FPS",
                    30,
                    10,
                    60,
                    1,
                    "SHM 显示限帧（自瞄不走这里）。30 更稳；调高更跟手更吃 CPU。",
                ),
                number_setting(
                    "Display Max Width",
                    960,
                    0,
                    1920,
                    16,
                    "SHM 显示最大宽度 px，0=不限制。960 推荐；1280/原生更清晰更卡。",
                ),
                bool_setting("Mirror", False),
                bool_setting(
                    "SHM Native Size",
                    False,
                    "SHM 强制按 Capture W/H 缩放。默认关，改用 Display Max Width 限宽。",
                ),
            ],
            enabled=True,
            description="图传源。Lab 默认 Gestalt SHM；显示已限帧/限宽以保证流畅。",
        ),
        Module(
            "TongjiVisionBridge",
            "Vision",
            "None",
            [
                mode_setting(
                    "Vision Source",
                    "Tongji",
                    ["Local", "Tongji", "Auto", "Mock"],
                    "Tongji/Auto：收外挂 NUC（同济）UDP 检测上屏；Local：仅 client 内同帧显示检测，无控制。",
                ),
                bool_setting("Listen", True, "接收同济/NUC 发到本机的 UDP JSON（rm.vision.tongji.v1）。"),
                number_setting("UDP Port", 9871, 1024, 65535, 1),
                number_setting("UDP Timeout", 250, 80, 2000, 10, "ms"),
                mode_setting("Local Device", "AUTO", ["AUTO", "GPU", "CPU"]),
                mode_setting("External Device", "AUTO", ["AUTO", "GPU", "CPU"]),
                mode_setting("Local Model", "Tongji YOLOv5", ["Tongji YOLOv5"]),
                bool_setting("Desktop Safe", True),
            ],
            enabled=True,
            description="显示向视觉入站。自瞄操作只在同济外挂 NUC；client 只收结果画框，不发云台。",
        ),
        Module(
            "CameraCalibration",
            "Vision",
            "None",
            [
                mode_setting("Profile", "polar_webcam", ["polar_webcam"]),
                number_setting("Board Columns", 9, 4, 14, 1),
                number_setting("Board Rows", 6, 3, 10, 1),
                number_setting("Square Size", 25, 10, 80, 1, "mm"),
                action_setting("Capture Chessboard", "从当前电脑摄像头帧采集一组棋盘格角点。"),
                action_setting("Solve Calibration", "使用已采集的角点计算内参和畸变参数。"),
                action_setting("Export Tongji YAML", "导出同济桥接器使用的桌面相机配置。"),
            ],
            enabled=True,
            description="电脑摄像头棋盘格标定。未标定时只允许检测，不允许可信预测装甲显示。",
        ),
        Module(
            "Armor3DESP",
            "Vision",
            "None",
            [
                mode_setting("Style", "Wireframe3D", ["Wireframe3D", "Zen2D", "Prediction Rack"]),
                mode_setting("Renderer", "GPU Auto", ["GPU Auto", "CPU"]),
                bool_setting("Show Prediction", True),
                bool_setting("Show Labels", True),
                bool_setting("Show Depth", True),
                number_setting("Line Width", 2.0, 1.0, 5.0, 0.25, "px"),
                number_setting("Opacity", 0.82, 0.15, 1.0, 0.05),
            ],
            enabled=True,
            description="3D ESP：厚度盒体 + 同济四装甲板预测（外挂 EKF 或 client MultiArmor 扩板）。",
        ),
        Module(
            "RobotOverlay",
            "Vision",
            "R",
            [
                bool_setting("Armor Boxes", True),
                bool_setting("Robot ID", True),
                bool_setting("Distance", True),
                mode_setting("Color", "Team", ["Team", "Threat", "Accent"]),
                mode_setting("ESP Style", "3D Wire", ["3D Wire", "3D Glass", "Zen Outline", "Flat 2D"]),
                number_setting("3D Depth", 0.22, 0.05, 0.50, 0.01),
                bool_setting("Predict Plates", True),
                number_setting("Armor Count", 4, 2, 4, 1),
                number_setting("Radius M", 0.23, 0.10, 0.45, 0.01, "m"),
                number_setting("Predict Horizon", 0.06, 0.0, 0.30, 0.01, "s"),
                number_setting("Max Targets", 6, 1, 12, 1),
                bool_setting("Only Secondary", True),
            ],
            enabled=True,
            description="3D ESP + 四板预测参数（板数/半径/时域）；与 Armor3DESP 共用。",
        ),
        Module(
            "RadarOverlay",
            "Vision",
            "None",
            [
                bool_setting("High Light", True),
                number_setting("Fade", 0.65, 0.1, 1.0, 0.05),
                mode_setting("Map", "Mini", ["Mini", "Full", "Hidden"]),
            ],
            description="雷达信息显示，并预留地图点击联动接口。",
        ),
        Module(
            "MapMarker",
            "Strategy",
            "M",
            [
                mode_setting("Mark Type", "Attack", ["Attack", "Defense", "Warning", "Custom"]),
                bool_setting("Send All", False),
                number_setting("Repeat", 1, 1, 5, 1),
            ],
            description="MapClickInfo/MapClickCmd 的 UI 预留模块。",
        ),
        Module(
            "TacticalMap",
            "Strategy",
            "Tab",
            [
                mode_setting("Map Size", "Wide", ["Compact", "Wide", "Full Tactical"]),
                mode_setting("Chrome", "Minimal", ["Minimal", "Thin", "Full"]),
                mode_setting("Status Display", "LiquidAuto", TACTICAL_MAP_STATUS_DISPLAYS),
                mode_setting("Palette", "RM Tactical", ["RM Tactical", "Liquid Neutral"]),
                mode_setting("Official Map", "RMUC 7v7 Official Map", ["RMUC 7v7 Official Map", "RMUL 3v3 Official Map"]),
                bool_setting("Mirror Blue", False, "蓝方镜像整张图。真坐标联调建议关，避免和游戏世界系不一致。"),
                bool_setting("Robot Dots", True),
                bool_setting("Map Marks", True),
                bool_setting("Radar Pulse", True),
                number_setting("Opacity", 0.72, 0.35, 1.0, 0.05),
            ],
            enabled=True,
            description="小地图。Tab/绑定键：按住放大，松开缩小（不开关模块）。",
        ),
        Module(
            "LiquidTacticalMap",
            "Render",
            "None",
            [
                mode_setting("Pointer Style", "Triangle", ["Triangle", "Wedge", "Dot"]),
                mode_setting("Entity Color", "Team", ["Team", "Distance", "Health"]),
                bool_setting("Show Labels", True),
                bool_setting("Show Values", True),
                mode_setting("Dock Animation", "Fly", ["Fly", "Flip", "Fade"]),
            ],
            enabled=True,
            description="官方 RM 小地图的 Liquid 风格视觉皮肤。",
        ),
        Module(
            "SentinelCommand",
            "Strategy",
            "None",
            [
                mode_setting("Posture", "Attack", ["Attack", "Defense", "Move"]),
                bool_setting("Confirm Prompt", True),
            ],
            description="哨兵/守卫控制指令 UI 预留模块。",
        ),
        Module(
            "AirSupport",
            "Strategy",
            "None",
            [
                mode_setting("Command", "Free", ["Free", "Paid", "Abort"]),
                bool_setting("Require Confirm", True),
            ],
            description="空中支援指令接口。",
        ),
        Module(
            "MatchTimer",
            "Strategy",
            "None",
            [
                bool_setting("Show Stage", True),
                bool_setting("Show Scores", True),
                bool_setting("Pulse Last 30s", True),
            ],
            enabled=True,
            description="GameStatus 阶段、倒计时、分数和回合信息显示。",
        ),
        Module(
            "BaseOutpostStatus",
            "Strategy",
            "None",
            [
                bool_setting("Own Base", True),
                bool_setting("Enemy Base", True),
                bool_setting("Outposts", True),
                bool_setting("Shield", True),
            ],
            enabled=True,
            description="7v7 GlobalUnitStatus 基地与前哨站血量/护盾显示。",
        ),
        Module(
            "VictoryPointStatus",
            "Strategy",
            "None",
            [
                bool_setting("Victory Points", True),
                bool_setting("Control Zone", True),
                bool_setting("Team Status", True),
            ],
            description="3v3 胜利点和控制区状态显示。",
        ),
        Module(
            "EconomyPanel",
            "Strategy",
            "None",
            [
                bool_setting("Gold", True),
                bool_setting("Tech Level", True),
                bool_setting("Radar Level", True),
            ],
            enabled=True,
            description="GlobalLogisticsStatus 金币和科技等级信息显示。",
        ),
        Module(
            "ProjectileShop",
            "Strategy",
            "None",
            [
                mode_setting("Ammo Type", "17mm", ["17mm", "42mm"]),
                bool_setting("Mock Request", True),
                bool_setting("Show Cost", True),
            ],
            enabled=True,
            description="模拟弹丸购买/请求面板；队内协议确认前不真实发送。",
        ),
        Module(
            "ThermalPowerPanel",
            "Render",
            "None",
            [
                bool_setting("Heat", True),
                bool_setting("Chassis Power", True),
                bool_setting("Buffer", True),
            ],
            enabled=True,
            description="RobotDynamicStatus 热量、底盘功率、缓冲能量和弹丸数量显示。",
        ),
        Module(
            "BuffTimeline",
            "Render",
            "None",
            [
                bool_setting("Buffs", True),
                bool_setting("Special Mechanism", True),
                bool_setting("Timeline", True),
            ],
            enabled=True,
            description="Buff 和特殊机制状态标签。",
        ),
        Module(
            "MQTTLink",
            "System",
            "None",
            [
                mode_setting("Host", "127.0.0.1", ["127.0.0.1", "192.168.12.1"]),
                number_setting("Port", 3333, 1000, 9999, 1),
                bool_setting("Auto Connect", False),
            ],
            description="SharkDataSever MQTT/protobuf 连接预留模块。",
        ),
        Module(
            "UDPVideo",
            "System",
            "None",
            [
                mode_setting("Host", "127.0.0.1", ["127.0.0.1", "0.0.0.0", "192.168.12.2"]),
                number_setting("Port", 3334, 1000, 9999, 1),
                bool_setting("Reassemble", True),
            ],
            description="SharkDataSever UDP HEVC 分片重组接口。",
        ),
        Module(
            "SerialBridge",
            "System",
            "None",
            [
                mode_setting("Port", "Stub", ["Stub", "COM1", "COM3", "COM5"]),
                number_setting("Baud", 115200, 9600, 921600, 9600),
                bool_setting("Mock Writes", True),
            ],
            description="RS232/串口桥接 stub，用于后续接入真实机器人。",
        ),
        Module(
            "GestaltControl",
            "System",
            "None",
            [
                bool_setting(
                    "Inject Enabled",
                    False,
                    "夺舍开关：游戏键/鼠标经 Input Bridge DLL 进进程（无 DLL 时 fallback 外置注入）。"
                    "模块键(R/V/RShift)留 client。F7 切换；ClickGUI 暂停。自瞄=RBExtAim。",
                ),
                bool_setting(
                    "Use Input Bridge DLL",
                    True,
                    "优先 \\.\\pipe\\POLARInputBridge.<pid>（需先 inject polar_input_bridge.dll）。",
                ),
                bool_setting(
                    "Exclusive Capture",
                    False,
                    "DLL 独占：屏蔽游戏原生键鼠，只认 client 经管道下发的输入（真·夺舍）。",
                ),
                bool_setting(
                    "Focus Game",
                    False,
                    "已废弃/勿开。",
                ),
                bool_setting(
                    "Keep Client Focus",
                    True,
                    "client 可保持前台；键鼠经 DLL 进游戏。",
                ),
                number_setting("Mouse Sensitivity", 1.0, 0.1, 5.0, 0.05),
                bool_setting(
                    "Inject Mouse",
                    False,
                    "手操视角：开战后光标锁窗口中心，移动=转视角（不飞出）。"
                    "开 ClickGUI 时自动停。自瞄仍用 RBExtAim。默认关。",
                ),
                bool_setting("Inject Fire", False, "左键开火（默认关）。"),
            ],
            enabled=True,
            description="Input Bridge DLL 键鼠夺舍 + 可选视角；自瞄 RBExtAim。",
        ),
        Module(
            "GestaltTelemetry",
            "System",
            "None",
            [
                bool_setting("Listen Attr Relay", True, "收 Workshop 继电器 UDP polar.gestalt.attrs.v1。"),
                number_setting("Relay Port", 9840, 1024, 65535, 1),
                bool_setting("Show Source Watermark", True, "HUD 显示 telemetry_source=gestalt|fallback。"),
            ],
            enabled=True,
            description="游戏 Attr 真源入站。无继电器时状态为 fallback，不得当作对局真数据。",
        ),
        Module(
            "HUD",
            "Render",
            "H",
            [
                bool_setting("Watermark", True),
                bool_setting("Module List", True),
                bool_setting("Dynamic Island", True),
                bool_setting("Target HUD", True),
                bool_setting("KeyBinds", True),
                bool_setting("Robot List", True),
                bool_setting("Status Cards", True),
                bool_setting("RM Indicators", True),
                mode_setting("Density", "CombatCompact", ["CombatCompact", "Balanced", "DebugFull"]),
                mode_setting("Visual Tone", "LiquidClean", ["LiquidClean", "PolarLegacy"]),
                mode_setting("Target Style", "RoundCompact", ["RoundCompact", "BarOnly", "FullCard"]),
                mode_setting("Right Display", "ModuleListHud", HUD_MODES),
                mode_setting("ModuleList Style", "LiquidArrayList", MODULE_LIST_STYLES),
                mode_setting("ModuleList Surface", "LiquidGlass", ["Native", "LiquidGlass"]),
                mode_setting("ModuleList Alignment", "Right", ["Right", "Left"]),
                mode_setting("ModuleList Order", "Descending", ["Descending", "Ascending"]),
                mode_setting("Module Tags", "Category", ["Category", "Keybind", "Off"]),
                mode_setting("Module Animation", "FlyFlip", ["FlyFlip", "Slide", "Fade"]),
            ],
            enabled=True,
            description="将 POLAR HUD 职责映射为 RM 状态覆盖层。",
        ),
        Module(
            "UIScale",
            "Render",
            "None",
            [
                bool_setting("Auto Scale", True, "根据当前窗口尺寸缩放 UI。"),
                bool_setting("High Res Optimize", True),
                mode_setting(
                    "Glass Quality",
                    "Full",
                    ["Full", "Balanced", "Lite"],
                    "液态玻璃质量（liquid-glass-react 语义）。Full=完整折射/色散；Lite=省性能。",
                ),
                number_setting("HUD Scale", 1.0, 0.6, 2.5, 0.05),
                number_setting("GUI Scale", 1.0, 0.6, 2.5, 0.05),
                number_setting("Animation Speed", 1.0, 0.5, 1.8, 0.05),
                number_setting("Reference Width", 1280, 960, 3840, 80, "px"),
                number_setting("Reference Height", 720, 540, 2160, 60, "px"),
            ],
            enabled=True,
            description="适配不同显示器尺寸的响应式 UI 缩放控制器；Glass Quality 控制液态玻璃。",
        ),
        Module(
            "Crosshair",
            "Render",
            "None",
            [
                mode_setting("Style", "RM Dot", ["RM Dot", "Circle", "Cross"]),
                number_setting("Size", 18, 4, 48, 1),
                bool_setting("Heat Color", True),
            ],
            enabled=True,
        ),
        Module(
            "ChassisGimbalIndicator",
            "Render",
            "None",
            [
                mode_setting("Style", "Minimal", ["Minimal", "Tank", "Compass"]),
                bool_setting("Show Angle", True),
                bool_setting("Show Mode", True),
                number_setting("Size", 56, 42, 120, 2, "px"),
                number_setting("Smooth", 0.35, 0.05, 1.0, 0.05),
            ],
            enabled=True,
            description="底盘-云台相对角方向指示器，显示车体方向、炮塔方向和 yaw 差。",
        ),
        Module(
            "ClickGui",
            "Render",
            "RightShift",
            [
                mode_setting("Mode", "PolarPanel", GUI_MODES),
                number_setting("Scale", 1.0, 0.5, 1.5, 0.25),
                bool_setting("Save Layout", True),
            ],
            description="等价于 POLAR ClickGuiModule 的 GUI 模式切换器。",
        ),
        Module(
            "GimbalPower",
            "Render",
            "None",
            [
                bool_setting("Show Voltage", True),
                bool_setting("Glow Alert", True),
                number_setting("Warn Below", 18, 0, 30, 1, "V"),
            ],
            enabled=True,
            description="把 RMUI GIMPOWER 元素转换为 POLAR 右侧 HUD。",
        ),
        Module(
            "PowerLimit",
            "Render",
            "None",
            [
                bool_setting("Show Limit", True),
                number_setting("Limit", 80, 30, 120, 1, "W"),
                bool_setting("Pulse Overrun", True),
            ],
            enabled=True,
            description="把 RMUI POWERLIM 元素转换为 POLAR 状态卡片。",
        ),
        Module(
            "SpeedLimit",
            "Render",
            "None",
            [
                bool_setting("Show Speed", True),
                number_setting("Limit", 420, 120, 720, 10, "dps"),
                mode_setting("Unit", "dps", ["dps", "rpm", "%"]),
            ],
            enabled=True,
            description="把 RMUI SPEEDLIM 元素转换为 POLAR 状态卡片。",
        ),
        Module(
            "Capacitor",
            "Render",
            "None",
            [
                bool_setting("Show Bar", True),
                number_setting("Warn", 25, 0, 100, 1, "%"),
                bool_setting("Use Glow", True),
            ],
            enabled=True,
            description="把 RMUI CAP 元素转换为 POLAR 进度卡片。",
        ),
        Module(
            "FrictionWheel",
            "Combat",
            "F",
            [
                bool_setting("Show State", True),
                bool_setting("Spin Preview", True),
                number_setting("Target RPM", 5200, 1000, 9000, 100, "rpm"),
            ],
            description="把 RMUI FRIC 元素作为摩擦轮状态模块。",
        ),
        Module(
            "LockState",
            "Combat",
            "L",
            [
                bool_setting("Show Lock", True),
                mode_setting("Lock Source", "Vision", ["Vision", "Radar", "Manual"]),
                bool_setting("Pulse Locked", True),
            ],
            enabled=True,
            description="把 RMUI LOCK 元素转换为 POLAR 目标锁定指示器。",
        ),
        Module(
            "BuffStatus",
            "Strategy",
            "None",
            [
                bool_setting("Small Buff", True),
                bool_setting("Large Buff", True),
                bool_setting("Anti Rotation", True),
            ],
            enabled=True,
            description="把 RMUI SMLBUFF/LARBUFF/ANTIRO 转换为 POLAR 状态标签。",
        ),
        Module(
            "ProjectileAllowance",
            "Strategy",
            "None",
            [
                bool_setting("17mm", True),
                bool_setting("42mm", True),
                bool_setting("Warn Low", True),
            ],
            enabled=True,
            description="POLAR HUD 风格的发弹允许量显示。",
        ),
        Module(
            "JumpPTEWheelLES",
            "System",
            "None",
            [
                bool_setting("JUP", True),
                bool_setting("PTE", True),
                bool_setting("WHE", True),
                bool_setting("LES", True),
            ],
            enabled=True,
            description="把 RMUI JUP/PTE/WHE/LES 状态元素转换为 POLAR 指示器。",
        ),
    ]
    return ModuleRegistry(modules)
