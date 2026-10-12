from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import time
from typing import Any, Callable

from .match_modes import MATCH_MODE_KEYS
from .robot_profiles import RMUC_ROBOTS
from .scenarios import SCENARIO_MODES
from .backend.referee.registry import DEFAULT_PROFILE, supported_profiles
from .serial_ports import port_choices as polar_port_choices
from .serial_ports import rc_port_choices as dt7_port_choices


CATEGORIES = ["Combat", "Movement", "Vision", "Strategy", "System", "Render"]

# 键鼠/驾驶协议槽的固定顺序，与 KeyboardMouseControl 位序一致。
CONTROL_SLOT_ORDER = (
    "W", "S", "A", "D", "SHIFT", "CTRL", "Q", "E",
    "R", "F", "G", "Z", "X", "C", "V", "B",
)

# 可绑定的物理键候选（与 control.BINDABLE_KEY_NAMES 保持同步）。
CONTROL_BIND_OPTIONS = [
    "W", "A", "S", "D", "Q", "E", "R", "F", "G", "Z", "X", "C", "V", "B",
    "Shift", "RightShift", "Ctrl", "Space", "Tab", "Enter", "Escape",
    "Backspace", "Delete", "Insert", "Home", "End", "PageUp", "PageDown",
    "Up", "Down", "Left", "Right", "F1", "F2", "F3", "F4", "F5",
    "F6", "F7", "F8", "F9", "F10", "F11", "F12", "None",
]
# These keys are consumed by the window before control dispatch. Do not offer
# a binding which can never reach its slot (F12 must remain the E-stop).
CONTROL_RESERVED_KEYS = frozenset({"rightshift", "tab", "f8", "f9", "f12"})
CONTROL_BIND_OPTIONS = [key for key in CONTROL_BIND_OPTIONS if key.lower() not in CONTROL_RESERVED_KEYS]

PRIMARY_GUI_MODE = "JelloSigma"

LEGACY_GUI_MODES = [
    "PolarOld",
    "PolarPanel",
    "PolarNew",
    "PolarDropdown",
    "PolarUnified",
    "PolarMulti",
]

# The public ClickGUI surface intentionally contains one entry. Legacy
# renderers remain available through ClickGui -> Old ClickGUIs, but they are
# not normal modes and are never persisted as the startup GUI.
GUI_MODES = [PRIMARY_GUI_MODE]

GUI_MODE_MIGRATIONS = {
    "VapeUnified": "PolarUnified",
    "VapeMulti": "PolarMulti",
}


def normalize_gui_mode(mode: str) -> str:
    del mode
    return PRIMARY_GUI_MODE


def normalize_legacy_gui_mode(mode: str) -> str | None:
    value = GUI_MODE_MIGRATIONS.get(str(mode), str(mode))
    return value if value in LEGACY_GUI_MODES else None

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
    "PolarTextList",
    "PolarRainbowRight",
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
    # Exact Sigma Jello ActiveMods renderer.  DebugWhiteList is accepted only
    # by config migration and is no longer exposed as a visual style.
    "JelloActiveMods",
]

# 每种可见样式都必须拥有独立视觉签名，避免配置项互相覆盖。
MODULE_LIST_RENDERER_KEYS = {
    "PolarTextList": "polar_text",
    "PolarRainbowRight": "polar_rainbow_right",
    "GlowRows": "glow_rows",
    "Segmented": "segmented",
    "DynamicIsland": "dynamic_island",
    "KeyBindsRows": "keybind_rows",
    "StatusBars": "status_bars",
    "RobotCards": "robot_cards",
    "LiquidArrayList": "liquid_array:list",
    "LiquidArrayTags": "liquid_array:tags",
    "LiquidArrayCompact": "liquid_array:compact",
    "LiquidArrayDock": "liquid_array:dock",
    "JelloActiveMods": "jello_active_mods",
}

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
    _enabled_change_hook: Callable[["Module", bool], None] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __setattr__(self, name: str, value: Any) -> None:
        """Keep registry cache generations correct for every enabled write.

        Production code normally uses ``toggle_module``/``set_enabled``, but
        configuration migration and diagnostic tools also assign ``enabled``
        directly.  The static HUD cache must observe all three paths without
        rebuilding an enabled-state tuple every display tick.
        """
        if name != "enabled":
            object.__setattr__(self, name, value)
            return
        existed = "enabled" in self.__dict__
        previous = self.__dict__.get("enabled")
        object.__setattr__(self, name, value)
        if existed and previous != value:
            hook = self.__dict__.get("_enabled_change_hook")
            if hook is not None:
                hook(self, bool(value))

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
        self.enabled_revision = 0
        for module in modules:
            module._enabled_change_hook = self._on_module_enabled_changed

    def _on_module_enabled_changed(self, _module: Module, _enabled: bool) -> None:
        self.enabled_revision += 1

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
    def __init__(self, initial: str = PRIMARY_GUI_MODE) -> None:
        self.mode = normalize_gui_mode(initial)

    def set_mode(self, mode: str) -> None:
        self.mode = normalize_gui_mode(mode)

    def next_mode(self) -> str:
        # Jello Sigma is the only public mode. F9 must not reveal retained
        # legacy renderers.
        self.mode = PRIMARY_GUI_MODE
        return self.mode


@dataclass
class RobotState:
    referee_profile: str = DEFAULT_PROFILE
    match_mode: str = "RMUC7v7"
    match_title: str = "RMUC 7v7"
    match_duration_sec: int = 420
    alliance_color: str = "Red"
    # 启动/赛制选择不能伪造“比赛中”；只接受真实 GameStatus 后进入 MATCH。
    current_stage: str = "NOT_STARTED"
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
    ammo_capacity: int = 180
    total_projectiles_fired: int = 12
    remaining_economy: int = 260
    total_economy_obtained: int = 620
    # The custom-client feed does not expose the robot's current supply-zone
    # RFID card bit. Unknown must never be presented as an authoritative True.
    in_supply_zone: bool = False
    supply_zone_known: bool = False
    out_of_combat: bool = True
    can_remote_heal: bool = True
    can_remote_ammo: bool = True
    respawn_pending: bool = False
    respawn_progress: float = 0.0
    respawn_can_free: bool = False
    respawn_gold_cost: int = 0
    respawn_can_pay: bool = False
    air_support_active: bool = False
    air_support_can_free: bool = False
    air_support_free_time_sec: int = 0
    air_support_cost_coins: int = 0
    economy_mock_mode: bool = True
    economy_managed: bool = False
    economy_status_revision: int = 0
    economy_command_status: str = ""
    tech_level: int = 1
    encryption_level: int = 0
    robot_id: int = 3
    robot_type: str = "Infantry"
    robot_profile: str = "Infantry1"
    projectile_type: str = "17mm"
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
    vision_kill_status: str = "IDLE"
    official_feed_at: float = 0.0
    official_robot_health: list[int] = field(default_factory=list)
    # GlobalUnitStatus.robot_bullets follows the fixed own-team role order
    # (Hero, Engineer, Infantry 3, Infantry 4, Sentry).  Keep the authoritative
    # vector separate from backend diagnostics so the HUD can be remapped when
    # RobotStaticStatus later corrects this client's robot_id.
    official_robot_bullets: list[int] = field(default_factory=list)
    official_events_online: bool = False
    kill_streak: int = 0
    visual_kill_count: int = 0
    referee_kill_count: int = 0
    vision_scene_luminance: float = 0.0
    # rm-native-viewer PDL1 telemetry.  ``native_telemetry_at`` is a local
    # monotonic timestamp; unix_ms remains diagnostic only.
    native_telemetry_at: float = 0.0
    native_telemetry_unix_ms: int = 0
    native_telemetry_frame_seq: int = 0
    native_camera_online: bool = False
    native_gimbal_online: bool = False
    native_gimbal_mode: str = "UNKNOWN"
    native_bullet_count: int = 0
    native_bullet_speed: float = 0.0
    native_yaw_deg: float = 0.0
    native_yaw_velocity: float = 0.0
    native_pitch_deg: float = 0.0
    native_pitch_velocity: float = 0.0
    native_quaternion: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    native_status_text: str = ""
    # Hero lob is operator decision support only; no value below is a fire
    # command.  Calibration/relocalisation gates intentionally start false.
    hero_lob_yaw_calibrated: bool = False
    hero_lob_pitch_calibrated: bool = False
    hero_lob_relocalization_valid: bool = False
    hero_lob_armed: bool = False
    hero_lob_status: str = "OFF"
    hero_lob_preview_yaw_deg: float = 0.0
    hero_lob_preview_pitch_deg: float = 0.0
    hero_lob_flight_time_s: float = 0.0
    fps: float = 0.0
    latency_ms: int = 36
    last_event: str = "Armor target acquired"
    events: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    start_time: float = field(default_factory=time.perf_counter)

    # --- SharkClient 对齐后端新增字段（rm_client/backend 写入，UI 只读） ---
    robot_module_status: dict[str, int] = field(default_factory=dict)
    robot_positions: list[dict[str, Any]] = field(default_factory=list)
    buffs: list[dict[str, Any]] = field(default_factory=list)
    penalties: list[dict[str, Any]] = field(default_factory=list)
    active_penalty: dict[str, Any] = field(default_factory=dict)
    radar_detections: list[dict[str, Any]] = field(default_factory=list)
    # RadarInfoToClient 最近一次到达时刻（perf_counter 秒）。渲染层据此做
    # TTL 衰减——雷达流断供后标记淡出，而不是永久残留在小地图上。
    radar_detections_at: float = 0.0
    path_plan: dict[str, Any] = field(default_factory=dict)
    tech_core: dict[str, Any] = field(default_factory=dict)
    performance_sync: dict[str, int] = field(default_factory=dict)
    deploy_mode: int = 0
    deploy_status_revision: int = 0
    deploy_status_at: float = 0.0
    rune_status: dict[str, Any] = field(default_factory=dict)
    sentry_status: dict[str, Any] = field(default_factory=dict)
    dart_status: dict[str, Any] = field(default_factory=dict)
    sentry_ctrl_result: dict[str, Any] = field(default_factory=dict)
    special_mechanisms: list[dict[str, Any]] = field(default_factory=list)
    injury_stats: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Frontend-only seam for detailed student panels. Never a control flag.
    student_ui_data: dict[str, Any] = field(default_factory=dict)
    # RobotInjuryStat is cumulative.  The backend baselines the first sample
    # and publishes only positive 17/42 mm deltas through these fields so a
    # reconnect cannot be mistaken for a fresh hit.
    injury_stats_initialized: bool = False
    projectile_damage_small_total: int = 0
    projectile_damage_large_total: int = 0
    projectile_hit_revision: int = 0
    last_projectile_hit_at: float = 0.0
    last_projectile_damage_delta: int = 0
    last_projectile_kind: str = ""
    last_projectile_killer_id: int = 0
    backend_stats: dict[str, Any] = field(default_factory=dict)
    # --- ???????????? ID ??????????backend/manager ???UI ??? ---
    robot_statics: dict[int, dict[str, Any]] = field(default_factory=dict)
    robot_dynamics: dict[int, dict[str, Any]] = field(default_factory=dict)
    robot_modules: dict[int, dict[str, Any]] = field(default_factory=dict)
    backend_frontend_synced: bool = False
    mock_simulation_enabled: bool = True
    official_state_active: bool = False
    official_state_at: float = 0.0

    # --- 队内 StandardRobot++ 0x5A 链路（下位机直供真值）-----------------
    # overlay 由 backend/polar_bridge.py 汇总，在 tick() 之后统一回写，因此
    # 不受「仿真先跑还是数据段先到」的顺序影响。
    polar_overlay: dict[str, Any] = field(default_factory=dict)
    polar_link_active: bool = False
    #: 自转没发出去时的人话原因（空字符串=没有阻碍）
    polar_spin_note: str = ""
    polar_link_age_s: float = float("inf")
    # 驾驶/云台授权与仲裁结果：上车时操作员必须能直接看到“现在指令能不能生效”
    polar_drive_armed: bool = False
    polar_gimbal_client: bool = False
    polar_authority_state: str = "PASSIVE"
    polar_authority_reason: str = ""
    # 指令-实测校验：IDLE/WAITING/OK/NO_MOTION/STUCK/NO_FEEDBACK/STALE_FEEDBACK/NO_LINK
    polar_motion_state: str = "IDLE"
    polar_motion_detail: str = ""
    polar_segments_received: int = 0
    polar_dropped_segments: int = 0
    # 云台/底盘姿态与实测速度：官方裁判协议不提供，是队内链路的独有增量
    gimbal_pitch_deg: float = 0.0
    imu_pitch_deg: float = 0.0
    imu_roll_deg: float = 0.0
    chassis_yaw_rate_rps: float = 0.0
    measured_vx_mps: float = 0.0
    measured_vy_mps: float = 0.0
    measured_wz_rps: float = 0.0
    # 下位机上报的执行器/部件状态
    chassis_error: bool = False
    gimbal_error: bool = False
    shoot_error: bool = False
    drive_ready: bool = False
    armor_id: int = 0
    launch_frequency_hz: int = 0
    bullet_type: int = 0
    recovery_buff: int = 0
    vulnerability_buff: int = 0
    report_cooling_buff: int = 0
    rfid_status: int = 0
    polar_debug_packages: dict[str, float] = field(default_factory=dict)
    # 队内链路与官方结构体形状不兼容的增量数据，一律另存，不改写
    # map_robots / robot_positions / official_robot_health 这些官方拥有的容器。
    polar_self_x: float = 0.0
    polar_self_y: float = 0.0
    polar_self_angle_deg: float = 0.0
    polar_ally_hp: dict[int, int] = field(default_factory=dict)
    polar_ground_positions: dict[int, tuple[float, float]] = field(default_factory=dict)
    polar_gold_coin: int = 0
    polar_fortress_allowance: int = 0
    polar_game_progress: int = 0
    polar_game_type: int = 0
    polar_stage_remain_s: float = 0.0
    polar_event_bitmap: int = 0
    polar_warning_level: int = 0
    polar_warning_robot_id: int = 0
    #: 机器人自己在 0x0A 里上报的编号。与操作员选的 `robot_id` 分开存，
    #: 两者不一致就是“界面选的车和实际连的车不是同一台”，必须告警。
    polar_reported_robot_id: int = 0
    polar_warning_count: int = 0
    polar_module_errors: dict[str, bool] = field(default_factory=dict)

    # Buff 类型分组（对齐 backend BUFF_TYPE_NAMES：1=攻击 2=防御 3=冷却
    # 4=底盘功率 5=回血）。攻击/防御两组作为指示灯语义；其余类型计入详情。
    BUFF_ATK_TYPES = frozenset({1})
    BUFF_DEF_TYPES = frozenset({2, 4, 5})
    MODULE_INDICATOR_FIELDS = (
        ("PWR", ("power_manager",)),
        ("SHT", ("small_shooter", "big_shooter")),
        ("CV", ("video_transmission",)),
        ("ARM", ("armor",)),
        ("MAIN", ("main_controller",)),
    )

    @property
    def effective_capacitor_percent(self) -> int:
        """电容/缓冲百分比的**唯一来源**：优先用裁判系统真实字段。

        为什么不能直接用 `capacitor_percent`：那个字段只在 mock tick 里被赋值，
        真实的 RobotDynamicStatus 从不写它。接上真车后它会永远停在 62，
        于是电容条和"电容见底告警"全都变成死数据——看着在动，实际不动。

        退回顺序：有可信的 max_buffer_energy 就用 current/max 算比例；
        否则退回 `capacitor_percent`（桌面 mock 场景仍然可看）。
        """
        try:
            maximum = int(self.max_buffer_energy or 0)
            current = int(self.current_buffer_energy or 0)
        except (TypeError, ValueError):
            return max(0, min(100, int(self.capacitor_percent or 0)))
        if maximum > 0:
            return max(0, min(100, int(round(100.0 * current / maximum))))
        return max(0, min(100, int(self.capacitor_percent or 0)))

    def buff_indicator_state(self) -> tuple[bool, bool, str]:
        """返回 (攻击增益亮, 防御/回复增益亮, 详情文本)。

        优先消费 backend 解码的本机 Buff 行（buff_rows 携带 buff_type 与
        robot_id）；LabDebug 的 mock 行没有 buff_type，此时回退旧演示布尔，
        保证演示动画不回归。真实对局中即使暂时没有本机 Buff 行也返回全灭，
        绝不借用 mock 布尔或把队友/敌方 Buff 显示成本机状态。
        """
        atk = dfn = False
        names: list[str] = []
        typed = False
        for row in getattr(self, "buff_rows", ()) or ():
            bt = row.get("buff_type")
            if bt is None:
                continue
            typed = True
            try:
                row_robot_id = int(row.get("robot_id", 0) or 0)
            except (TypeError, ValueError):
                continue
            if row_robot_id != int(self.robot_id):
                continue
            if not row.get("active"):
                continue
            b = int(bt)
            label = str(row.get("name", f"Buff {b}"))
            remaining = int(row.get("remaining", 0) or 0)
            if b in self.BUFF_ATK_TYPES:
                atk = True
                names.append(f"{label} {remaining}s")
            elif b in self.BUFF_DEF_TYPES:
                dfn = True
                names.append(f"{label} {remaining}s")
        if not typed and not self.official_state_active:
            return bool(self.small_buff), bool(self.large_buff), ""
        return atk, dfn, "; ".join(names[:4])

    def robot_module_indicator_state(self) -> tuple[tuple[str, bool], ...]:
        """Return stable HUD chips for authoritative RobotModuleStatus.

        An empty status produces no extra row, preserving the LabDebug layout.
        Shooter is online when either the small or big projectile module is
        reported online; every other chip maps one-to-one to the official
        V1.3.1 field. Only 1 confirms online; 2 is an installation fault.
        """

        modules = getattr(self, "robot_module_status", None) or {}
        if not modules:
            return ()
        return tuple(
            (
                label,
                any(modules.get(field_name) == 1 for field_name in field_names),
            )
            for label, field_names in self.MODULE_INDICATOR_FIELDS
        )

    def apply_polar_overlay(self) -> None:
        """把队内 0x5A 链路的真值回写到本状态对象。

        必须在 `tick()` 之后调用：桌面仿真会写同一批字段（底盘/云台朝向、buff、
        能量等），后写覆盖先写，才能保证接上真机器人时 HUD 显示的是实测量而不是
        正弦动画。链路过期（`polar_link_active` 为假）时不再覆盖，交还给原有数据源。
        """

        if not self.polar_overlay:
            return
        if not self.polar_link_active:
            return
        for name, value in self.polar_overlay.items():
            if hasattr(self, name):
                setattr(self, name, value)
        self.official_state_at = self.official_state_at or time.perf_counter()

    def tick(self, dt: float) -> None:
        # The desktop demo must never race 1--10 Hz referee messages. Official
        # and Campus scenes hold the last authoritative values between updates.
        if not self.mock_simulation_enabled or self.official_state_active:
            return
        elapsed = time.perf_counter() - self.start_time
        duration = max(1, int(self.match_duration_sec))
        self.stage_countdown_sec = max(0, duration - int(elapsed) % (duration + 1))
        self.stage_elapsed_sec = max(0, duration - self.stage_countdown_sec)
        # Desktop mock heat: staged cool + burst fire (visual-demo RM heat).
        # Continuous sine made the blue ring approachJar mushy with no settle.
        if getattr(self, "_mock_heat_sim", None) is None:
            from .combat_hud import MockHeatStageSimulator

            self._mock_heat_sim = MockHeatStageSimulator()
            start_pct = (
                (float(self.current_heat) / float(self.max_heat) * 100.0)
                if self.max_heat > 0
                else 48.0
            )
            # Prefer a mid heat so the outer blue ring is visible on boot.
            self._mock_heat_sim.reset(max(36.0, min(100.0, start_pct if start_pct > 5.0 else 48.0)))
        heat_pct = self._mock_heat_sim.tick(
            dt,
            now=time.perf_counter(),
            self_max_hp=float(max(1, self.max_health)),
            ammo=int(self.remaining_ammo),
        )
        self.current_heat = max(0.0, float(self.max_heat) * (heat_pct / 100.0))
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
        ammo_capacity = max(0, int(self.ammo_capacity))
        if not self.economy_managed:
            self.remaining_ammo = max(0, int(ammo_capacity - (self.stage_elapsed_sec * 0.18) % max(1, min(80, ammo_capacity))))
        self.projectile_allowance_17mm = max(0, int(180 - (self.stage_elapsed_sec * 0.23) % 120))
        self.projectile_allowance_42mm = max(0, int(12 - (self.stage_elapsed_sec * 0.035) % 8))
        if not self.economy_managed:
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


def action_group_setting(name: str, options: list[str], description: str = "") -> Setting:
    """Create a transient group of action buttons.

    The selected action and expansion state are intentionally owned by the UI
    session rather than ``value``, so profiles cannot make a legacy GUI the
    next startup surface.
    """

    return Setting(
        name=name,
        kind="action_group",
        value=None,
        options=list(options),
        description=description,
    )


def build_default_registry(*, discover_devices: bool = True) -> ModuleRegistry:
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
                mode_setting("Wallpaper Profile", "BlackHole", ["BlackHole", "TexasMP4", "VendingMachine4K", "Default3000925581Pkg", "ParticleGrid"]),
                mode_setting("Motion Quality", "Smooth60", ["Smooth60", "Balanced30", "Source"]),
                mode_setting("Glass Style", "LiquidGlass", ["LiquidGlass", "Legacy"]),
                number_setting("Glass Strength", 1.00, 0.40, 1.40, 0.05),
                number_setting("Parallax Strength", 22, 0, 60, 1, "px"),
                number_setting("Background Zoom", 1.00, 1.0, 1.35, 0.01),
                number_setting("Dim Overlay", 0.28, 0.0, 0.8, 0.01),
                action_setting("Import Wallpaper", "导入 MP4/GIF/图片壁纸到客户端壁纸目录。"),
                action_setting("Reset Wallpaper", "恢复默认黑洞壁纸。"),
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
            "RobotProfile",
            "System",
            "None",
            [
                mode_setting("Role", "Infantry1", [profile.key for profile in RMUC_ROBOTS]),
                number_setting("Level", 1, 1, 10, 1),
                mode_setting("Hero Type", "Melee", ["Melee", "Range"]),
                mode_setting("Infantry Chassis", "Power", ["Power", "Health"]),
                mode_setting("Infantry Shooter", "Burst", ["Burst", "Cooling"]),
                mode_setting("Sentinel Control", "Automatic", ["Automatic", "SemiAuto"]),
            ],
            enabled=True,
            description="按官方规则选择机器人角色、等级与性能分支，并驱动血量、热量、冷却和功率上限。",
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
            "EmergencyStop",
            "System",
            "F12",
            [
                action_setting("Trigger E-Stop", "立即发送全零键鼠帧并锁止全部上行。"),
                action_setting("Release E-Stop", "仅在确认机器人安全后解除；快捷键 Ctrl+Shift+F12。"),
            ],
            enabled=True,
            description="赛事控制急停：F12 触发，Ctrl+Shift+F12 解除。",
        ),
        Module(
            "MatchReadiness",
            "System",
            "None",
            [
                action_group_setting(
                    "Qualification Actions",
                    [
                        "New Qualification Session",
                        "Reset Qualification Session",
                        "Run Preflight",
                        "Run Video Test",
                        "Run KMC Test",
                        "Record DT7 Safety Test",
                        "Run Economy Send Test",
                        "Record E-Stop Test",
                        "Observe Firmware Zero",
                        "Record Firmware Zero Test",
                        "Run Loss Recovery Test",
                        "Record Hero Test",
                        "Record Economy Test",
                        "Review PRM1 Vision Schema",
                        "Run PRM1 Vision Bench",
                        "Review PRM1 Spin Schema",
                        "Run PRM1 Spin Bench",
                        "Start Match Soak",
                        "Stop Match Soak",
                        "Export Evidence",
                        "Field Sign-off",
                        "Arm Match Control",
                        "Lock Match Control",
                    ],
                    "现场资格证据与双人签字；没有真车、裁判与正式图传证据时保持 NOT READY。",
                ),
            ],
            enabled=True,
            description="Fail-closed 现场资格、证据导出和比赛控制硬闸门。",
        ),
        Module(
            "ArmorAutoAim",
            "Combat",
            "V",
            [
                bool_setting("Mock Output", True, "云台输出保持在模拟模式。"),
                number_setting("Fov", 85, 10, 180, 1, "deg"),
                number_setting("Smooth", 0.28, 0.01, 1.0, 0.01),
                mode_setting("Priority", "Armor", ["Armor", "Distance", "Health", "Radar"]),
                mode_setting("Lock Style", "White Outline", ["Polar Corner", "White Outline", "Minimal Bracket", "Rune Ring", "Heli Gimbal"]),
                bool_setting("Draw Fov", True, "绘制中心荧光白自瞄圆（仅圆内可锁定）。"),
                number_setting(
                    "Circle Size",
                    160,
                    32,
                    480,
                    2,
                    "px",
                    "自瞄锁定半径（逻辑像素）。只有圆心距落在此圆内的装甲才会被锁定；全屏 3D ESP 仍照常绘制。",
                ),
            ],
            enabled=True,
            description="装甲板自瞄 UI。荧光白圆限制锁定范围；3D ESP 全屏可见。",
        ),
        Module(
            "EliminationFX",
            "Render",
            "None",
            [
                mode_setting("Preset", "Default Scrolling", [
                    "Default Scrolling", "Combo", "Card", "Battlefield 1", "Minimal", "PUBG Subtitle",
                    "Battlefield 5", "Battlefield 6", "Total Battlefield", "Valorant Prime", "Valorant Glitchpop",
                    "Valorant Singularity V1", "Valorant Singularity V2", "Valorant Singularity V3",
                    "Valorant Gaia", "Valorant Gaia V1", "Valorant Gaia V2", "Valorant Gaia V3",
                    "Valorant Bubblegum", "Valorant Bubblegum V1", "Valorant Bubblegum V2", "Valorant Bubblegum V3",
                    "Valorant Champions 2021", "Valorant Prelude V1", "Valorant Prelude V2", "Valorant Prelude V3",
                    "Valorant Primordium", "Valorant Primordium V1", "Valorant Primordium V2", "Valorant Primordium V3",
                    "Valorant Radiant Crisis 001", "Valorant RGX", "Valorant RGX V1", "Valorant RGX V2", "Valorant RGX V3",
                ]),
                mode_setting("Element Visibility", "Preset", ["Preset", "Full HUD", "Icons Only"]),
                bool_setting("Kill Feed", True, "击杀信息流（ELIMINATED x N）。"),
                bool_setting("Score Popup", True, "击杀 +分数 弹字。"),
                bool_setting("Bonus Tags", True, "加分项标签行（gd656 bonus_list）。"),
                bool_setting("Lock Target Bonus", True, "锁定目标加分：持续交战同一目标后的击杀追加“锁定目标”标签与提示音（gd656 LOCKED_TARGET）。"),
                bool_setting("Combo Subtitle", True, "连杀字幕（N KILL COMBO）。"),
                bool_setting("Scrolling Icons", True, "滚动击杀图标条（默认/BF5/BF6 通用）。"),
                bool_setting("Combo Icons", True, "CF 连杀图标。"),
                bool_setting("Card Icons", True, "CS2 卡牌图标与卡池条。"),
                bool_setting("Battlefield Icons", True, "战地 1 大图标。"),
                bool_setting("Valorant Icons", True, "无畏契约系图标。"),
                bool_setting("Honor Badges", True, "荣誉徽章：特殊战况（爆头/远程/连杀/载具等）点亮 gd656 honor 图标。"),
                bool_setting("Hit Info", True, "命中信息：受击伤害弹字 + 命中音效（gd656 hit_info）。"),
                bool_setting("Match Progress", True, "顶部基地推进条：双方基地血量消耗进度与红蓝比分。"),
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
                mode_setting("Sound Theme", "Polar UI", ["Custom", "Polar UI", "Polar Classic", "LiquidBounce"]),
                mode_setting("Custom Enable Sound", "No Sound", ["No Sound"], "自定义主题的模块开启音效。"),
                mode_setting("Custom Disable Sound", "No Sound", ["No Sound"], "自定义主题的模块关闭音效。"),
                mode_setting("Custom Invalid Sound", "No Sound", ["No Sound"], "自定义主题的无效操作提示音。"),
                number_setting("UI Volume", 45, 0, 100, 1, "%"),
                action_setting("Import Enable Sound", "导入模块开启音效。"),
                action_setting("Import Disable Sound", "导入模块关闭音效。"),
                action_setting("Import Invalid Sound", "导入无效操作音效。"),
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
                mode_setting("Lock Style", "Rune Ring", ["Polar Corner", "White Outline", "Minimal Bracket", "Rune Ring", "Heli Gimbal"]),
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
            "HeroLob",
            "Combat",
            "K",
            [
                mode_setting("Mode", "Radar Assisted", ["Radar Assisted", "Manual"]),
                mode_setting("Target", "Enemy Base", ["Enemy Base", "Enemy Outpost", "Manual"]),
                number_setting("Distance", 18.0, 1.0, 60.0, 0.1, "m"),
                number_setting("Height Delta", 0.0, -12.0, 12.0, 0.05, "m"),
                number_setting("Bearing", 0.0, -180.0, 180.0, 0.1, "deg"),
                mode_setting("Arc", "High", ["High", "Low"]),
                number_setting("Muzzle Speed", 16.0, 5.0, 35.0, 0.1, "m/s"),
                number_setting("Gravity", 9.80665, 8.0, 11.0, 0.001, "m/s2"),
                number_setting("Yaw Offset", 0.0, -20.0, 20.0, 0.05, "deg"),
                number_setting("Pitch Offset", 0.0, -20.0, 20.0, 0.05, "deg"),
                number_setting("Muzzle Length", 0.35, 0.0, 1.0, 0.01, "m"),
                number_setting("Yaw Calibration Tolerance", 1.0, 0.1, 5.0, 0.1, "deg"),
                number_setting("Pitch Calibration Tolerance", 1.5, 0.1, 5.0, 0.1, "deg"),
                number_setting("Max Roll", 8.0, 1.0, 20.0, 0.5, "deg"),
                bool_setting("Require Fresh Telemetry", True),
                number_setting("Telemetry Timeout", 0.50, 0.10, 2.00, 0.05, "s"),
                number_setting("Heat Guard", 0.88, 0.40, 1.00, 0.01),
                bool_setting("Mock Output", True, "Decision support only; this module never fires automatically."),
                action_setting("Calibrate Lob Yaw", "Align yaw against a known field line."),
                action_setting("Calibrate Lob Pitch", "Store the operator-confirmed iterative pitch calibration."),
                action_setting("Confirm Relocalization", "Confirm radar/map relocalization after every reset."),
                action_setting("Invalidate Relocalization", "Fail closed after a localisation discontinuity."),
                action_setting("Open Deploy Interface", "Open the dedicated deployment/lob operator console."),
                action_setting("Preview Lob", "Calculate yaw, pitch and flight time without sending a command."),
                action_setting("Arm Assisted Lob", "Arm decision support only; no fire command is emitted."),
                action_setting("Disarm Assisted Lob"),
            ],
            description="英雄吊射辅助：雷达定位、偏航/俯仰标定与弹道预览。严格失效保护，绝不自动开火。",
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
            "X",
            [
                mode_setting("Direction", "Auto", ["Auto", "CW", "CCW"]),
                number_setting("Spin Speed", 420, 120, 720, 10, "dps"),
                bool_setting("Power Guard", True),
                bool_setting(
                    "Mock Output",
                    True,
                    "关闭后才会通过 MQTT CustomControl 发送 PRM1 kind=2；机器人固件必须实现同一 schema。",
                ),
            ],
            enabled=True,
            description="RM 底盘小陀螺模式；真实输出仅走官方 MQTT CustomControl，默认安全模拟。",
        ),
        Module(
            "AutoSpinOnHit",
            "Movement",
            "None",
            [
                number_setting("Minimum Damage", 1, 1, 500, 1, "HP"),
                number_setting("Duration", 2.5, 0.2, 8.0, 0.1, "s"),
                number_setting("Cooldown", 1.0, 0.0, 10.0, 0.1, "s"),
                bool_setting("Only During Match", True),
                bool_setting("Require Referee Link", True),
                bool_setting("Power Guard", True),
                number_setting("Minimum Buffer", 10, 0, 100, 1, "%"),
                bool_setting("Auto Disable", True),
            ],
            description=(
                "A new RobotInjuryStat 17/42 mm damage delta temporarily "
                "enables SpinMode; cumulative baselines and other damage do not trigger it."
            ),
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
                mode_setting("Source", "Webcam", ["Webcam", "Shark UDP", "UDP HEVC", "Native 0x0310", "Synthetic"]),
                number_setting("Camera ID", 0, 0, 5, 1),
                number_setting("Capture Width", 960, 320, 1920, 16, "px"),
                number_setting("Capture Height", 540, 240, 1080, 16, "px"),
                bool_setting("Mirror", False),
            ],
            enabled=True,
            description="使用电脑摄像头作为当前机器人图传。",
        ),
        Module(
            "TongjiVisionBridge",
            "Vision",
            "None",
            [
                mode_setting("Vision Source", "Local", ["Local", "Tongji", "Auto", "Mock"], "Local 直接使用同济 YOLO 权重；Tongji 接收 UDP 9871；Auto 优先外部同济。"),
                bool_setting("Listen", True, "接收外部同济检测器发到本机的 UDP JSON。"),
                number_setting("UDP Port", 9871, 1024, 65535, 1),
                number_setting("UDP Timeout", 250, 80, 2000, 10, "ms"),
                mode_setting("Local Device", "AUTO", ["AUTO", "GPU", "CPU"]),
                mode_setting("External Device", "AUTO", ["AUTO", "GPU", "CPU"]),
                mode_setting("Local Model", "Tongji YOLOv5", ["Tongji YOLOv5"]),
                mode_setting("Vision Kill Sensitivity", "Lab Responsive", ["Lab Responsive", "Balanced", "Match Reliable"], "控制视觉灯灭、低亮与丢检击杀的灵敏度；实验室优先联调，官方优先可靠。"),
                bool_setting("Desktop Safe", True),
            ],
            enabled=True,
            description="同济视觉源配置：Local 同帧 YOLO 或外部 UDP rm.vision.tongji.v1。桌面联调只缓存云台建议，不发送控制。",
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
                mode_setting(
                    "Style",
                    "JelloShadow",
                    ["JelloShadow", "Wireframe3D", "PolarOutline2D", "Prediction Rack"],
                ),
                mode_setting("Renderer", "GPU Auto", ["GPU Auto", "CPU"]),
                bool_setting("Show Prediction", False),
                bool_setting("Show Labels", False),
                bool_setting("Prediction Labels", False),
                bool_setting("Show Depth", True),
                number_setting("Line Width", 2.0, 1.0, 5.0, 0.25, "px"),
                number_setting("Opacity", 0.82, 0.15, 1.0, 0.05),
            ],
            enabled=True,
            description="使用同济 Tracker 的真实装甲四点与多装甲重投影结果渲染预测 ESP。",
        ),
        Module(
            "RobotOverlay",
            "Vision",
            "R",
            [
                bool_setting("Armor Boxes", True),
                bool_setting("Robot ID", True),
                bool_setting("Distance", True),
                mode_setting(
                    "Color",
                    "White",
                    ["White", "Team", "Accent", "Threat"],
                    "装甲盒配色。默认 White = 接线前的实际画面（纯白）；"
                    "Team 按视觉识别到的装甲板颜色分红蓝；Threat 需威胁度数据，"
                    "拿不到时降级回 Team 并在诊断里记原因。",
                ),
                mode_setting("ESP Style", "3D Wire", ["3D Wire", "3D Glass", "Polar Outline", "Flat 2D"]),
                number_setting("3D Depth", 0.22, 0.05, 0.50, 0.01),
                bool_setting("Predict Plates", True),
                number_setting("Armor Count", 4, 2, 4, 1),
                number_setting("Radius M", 0.23, 0.10, 0.45, 0.01, "m"),
                number_setting("Predict Horizon", 0.06, 0.0, 0.30, 0.01, "s"),
                number_setting("Max Targets", 6, 1, 12, 1),
                bool_setting("Only Secondary", True),
            ],
            enabled=True,
            description="将 ESP 视觉职责映射为 RM 机器人/装甲板覆盖层。",
        ),
        Module(
            "RadarOverlay",
            "Vision",
            "None",
            [
                bool_setting("High Light", True),
                number_setting("Fade", 0.65, 0.1, 1.0, 0.05),
                mode_setting(
                    "Map",
                    "Both",
                    ["Both", "Mini", "Full", "Hidden"],
                    "雷达标记画在哪种形态的地图上。默认 Both = 接线前的实际画面"
                    "（缩略与展开态都画）；Mini 只在缩略态画，Full 只在长按展开态画，"
                    "Hidden 都不画。",
                ),
            ],
            description="雷达信息显示，并预留地图点击联动接口。",
        ),
        Module(
            "MapMarker",
            "Strategy",
            "M",
            [
                mode_setting("Mark Type", "Attack", ["Attack", "Defense", "Warning", "Custom"]),
                # 默认 True 是刻意的：接这个开关之前，发送处一直硬写
                # `is_send_all=1`（全队广播）。若沿用 False 作默认，接线会把
                # 广播变成单发——那是比赛可见的行为改变，不该顺带引入。
                # wire 取值 1..4 按选项顺序映射（协议注释确认 3=mode、6=type，
                # 但官方文档未列出各取值语义，需与裁判协议表交叉核对）。
                bool_setting(
                    "Send All",
                    True,
                    "广播给全队。默认开启以匹配接线前的实际发送行为。",
                ),
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
                bool_setting("Mirror Blue", True),
                bool_setting("Robot Dots", True),
                bool_setting("Map Marks", True),
                bool_setting("Radar Pulse", True),
                number_setting("Opacity", 0.72, 0.35, 1.0, 0.05),
            ],
            enabled=True,
            description="基于官方地图的小地图，支持 RM 坐标、雷达高亮和地图点击预留位。",
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
            "TopMatchBar",
            "Render",
            "None",
            [
                number_setting("Scale", 0.72, 0.35, 1.4, 0.05),
                mode_setting("Match Scope", "RMUC Only", ["RMUC Only", "All Match Modes"]),
                mode_setting("Roster Scope", "Match Roles", ["Match Roles", "Ground Robots", "All Roles"]),
                mode_setting("Score Metric", "Referee Score", ["Unavailable", "Referee Score"]),
                bool_setting("Show Scores", True),
                bool_setting("Show Kills", True),
                bool_setting("Show Health", True),
                bool_setting("BF6 Progress", True),
                bool_setting("Source Motion Preview", False,
                             "原样式0.3s击杀标记缩放预览，尚未完成原版实录逐帧验收。"),
            ],
            enabled=True,
            description="CS式RM比赛顶部：车辆HP、独立系统状态和原BF6基地进度；缺失遥测保持未知。",
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
            "B",
            [
                bool_setting("Gold", True),
                bool_setting("Tech Level", True),
                bool_setting("Radar Level", True),
                mode_setting("Open Style", "Dynamic Island", ["Dynamic Island", "Full Sheet"]),
                bool_setting("Mock Requests", False, "正式赛事默认通过 MQTT 发送；实验室/模拟场景仍强制 Mock。"),
            ],
            enabled=True,
            description="按比赛模式与机器人状态提供弹丸、恢复、复活和支援操作。",
        ),
        Module(
            "ProjectileShop",
            "Strategy",
            "None",
            [
                mode_setting("Ammo Type", "17mm", ["17mm", "42mm"]),
                bool_setting("Mock Request", False, "正式赛事默认通过 MQTT 发送；实验室/模拟场景仍强制 Mock。"),
                bool_setting("Show Cost", True),
            ],
            enabled=True,
            description="直接打开经济工作区弹丸页；正式请求等待裁判状态确认。",
        ),
        Module(
            "MatchChat",
            "Strategy",
            "T",
            [
                bool_setting("Referee Notices", True),
                number_setting("Fade Seconds", 10, 3, 30, 1, "s"),
                number_setting("Closed Lines", 6, 1, 10, 1),
                number_setting("Open Lines", 16, 6, 24, 1),
            ],
            enabled=True,
            description="Minecraft-style local match chat and referee event feed.",
        ),
        Module(
            "QuickActionWheel",
            "System",
            "Q",
            [
                mode_setting("Sectors", "8", ["4", "6", "8"]),
                number_setting("Radius", 118, 72, 180, 2, "px"),
                number_setting("Dead Zone", 38, 18, 96, 2, "px"),
                bool_setting("Show Labels", True),
                mode_setting("Label Style", "Action", ["Action", "Compact", "Off"]),
                mode_setting("Icon Style", "Initials", ["Initials", "Dot", "Off"]),
                mode_setting(
                    "Up",
                    "Economy",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Upper Right",
                    "Ammo",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Right",
                    "Auto Aim",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Lower Right",
                    "Armor Lock",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Down",
                    "Match Chat",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Lower Left",
                    "Spin Mode",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Left",
                    "Friction Wheel",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Upper Left",
                    "ClickGUI",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
            ],
            enabled=True,
            description="Hold Q, point at an existing client action, then release to execute it.",
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
                mode_setting("Referee Profile", DEFAULT_PROFILE, list(supported_profiles())),
            ],
            description="裁判系统 MQTT/protobuf；年度协议显式选择，未知版本禁止回退。",
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
            "RobotLink",
            "System",
            "None",
            [
                # 选项启动时从系统实际探测，机器人 USB CDC 在车上可能是任意 COM 号；
                # "Auto" 会在启动时自动选中唯一/最像 STM32 的 USB 串口。
                mode_setting("Port", "Auto", (polar_port_choices(nonblocking=True) if discover_devices else ["Stub"])),
                number_setting("Baud", 115200, 9600, 921600, 9600),
                number_setting("Send Hz", 50, 10, 100, 1),
                # 能力位：由场景写入，表示本环境允许队内链路真写。
                bool_setting("Allow Drive", False),
                # 队内链路的写入开关。**不能**复用场景级标志：场景级 `mock_writes`
                # 的含义是“裁判/官方链路保持 mock”，而 `allow_polar_drive` 的含义是
                # “这个场景能不能自动授权驾驶”。早期用后者反推队内写入开关，
                # 导致大疆官方场景（allow_polar_drive=False）下队内链路被静默 mock，
                # 一帧都发不出去。现在写入权只属于本设置项。
                bool_setting("Mock Writes", False),
                # 允许客户端下发 SHIFT（小陀螺）这类**模式切换**键位。
                # 默认关：小陀螺是防御性底盘模式、不涉及发射，但它会改变底盘
                # 行为，所以仍要操作员显式授权。“受击后小陀螺”走 0x5A 需要开启。
                # 无论本项开关如何，Q/E（摩擦轮）与 F（sc_flag）永不下发。
                bool_setting("Allow Spin Key", False),
                # 无线键鼠出口：键鼠打包成 PRM1 kind=3，经 CustomControl(0x0302)
                # -> 图传链路 -> 机器人 0x0311，由车上 host_km_link 落到键位账本。
                # 默认关：开着要同时满足车上固件带 POLAR-KM-LINK、固件里
                # __CONTROL_LINK_KM 不是 CL_KM_RC，否则只是白占图传带宽。
                # 本项只是"意愿"，真正生效还要 模块启用 + Allow Drive + 非急停
                # （判定在 backend/manager.py 的 RobotLink 应用处）。
                bool_setting("Radio KM Egress", False),
                # 端到端自检：观察 2.5 秒车上回读的 0x0311 到达计数涨不涨。
                # 自检本身不注入任何按键，所以车不会因为这个动作而动。
                # ---- 自定义控制器出口（0x0306 -> 官方选手端 -> DT7）----
                # 依据：《2026 通信协议》表 1-1 "自定义控制器←→选手端 115200"、
                # 1.5 节"操作手可使用自定义控制器模拟键鼠操作选手端"、表 1-43 的
                # 8 字节布局。这条路**不走车上图传串口**，所以 0x0311 那条
                # 量不通时它仍然可用；代价是必须有官方选手端在跑。
                # 直连 DT7（不跑官方选手端也能发键鼠）。帧结构/位域/CRC 已在厂商
                # 二进制里核实，但**命令号与载荷字段顺序仍是候选值**
                # （configs/dt7_profile.json、README 第 20/21 节），
                # 因此默认关，且没现场确认前不许上正式比赛。
                bool_setting("Direct DT7 Egress", False),
                mode_setting("DT7 Port", "Auto", (dt7_port_choices(nonblocking=True) if discover_devices else ["Stub"])),
                # 串口清单是启动那一刻算出来的，而现场十有八九"先开客户端、后插 DT7"：
                # 那时下拉里只有 Auto，Auto 认不出 DJI 口就宁可拒发（不许乱开别人的口），
                # 于是只剩"重启程序"一条路 —— 场上一抖就是几十秒。给一个手动重扫。
                action_setting(
                    "Rescan Serial Ports",
                    "重新枚举 Port / DT7 Port / CC Port 三个下拉的候选口。"
                    "只换候选、不改当前值；当前值即使暂时不在列表里也会被保住。",
                ),
                # 真发闸：勾了才往 DT7 那个 DJI VCOM 写字节。不勾 = 只产帧不碰硬件。
                bool_setting("DT7 Real TX", False),
                # 开火前置执行器（摩擦轮 Q/E）默认不许经客户端注入，
                # 与 0x0311 侧的 KM_KEY_ALLOWED_MASK 同一条纪律；勾了才发。
                bool_setting("DT7 Allow Fire Keys", False),
                bool_setting("Custom Controller Egress", False),
                mode_setting("CC Port", "Auto", (polar_port_choices(nonblocking=True) if discover_devices else ["Stub"])),
                number_setting("CC Hz", 30, 5, 30, 1),
                # 默认 mock：没确认选手端挑口规则之前，绝不写真串口。
                bool_setting("CC Mock Writes", True),
                action_setting(
                    "Run DT7 Link Selftest",
                    "一键验证 C 路（PC→DT7→DR16→C 板）通不通 + DT7 会不会粘键。"
                    "刻意用固件**没有任何消费者**的 X 键当探针：链路再通也不会让车动一下。"
                    "判据只认车上合并后的最终键位 km_key（需插着 C 板 USB 0x5A 读回读）。",
                ),
                action_setting(
                    "Run Wireless KM Selftest",
                    "观察 2.5 秒车上回读的 0x0311 到达计数（km_rx）是否增长；"
                    "判据来自车那一侧，不是客户端自证。需插着 USB 0x5A 才能读回读。",
                ),
                # 意图位：由操作员控，两个都要开才会真的发。
                # ---- D 路：车端 USB<->WiFi 桥（不依赖大疆客户端、不动固件）----
                # 默认仍是直插串口；换 tcp 就是把"字节从哪儿来"换掉，
                # 协议层 polar_link 一个字都不动。
                mode_setting("Link Mode", "serial", ["serial", "tcp"]),
                mode_setting("Bridge Host", "", ["", "10.50.0.10", "192.168.12.10"],
                             description="车端 bridge_server 所在小电脑的 IP（Link Mode=tcp 才用）"),
                number_setting("Bridge Port", 50570, 1024, 65535, 1),
                bool_setting("Client Chassis Drive", False),
                bool_setting("Client Gimbal Control", False),
                # 现场约定：视觉 NUC 自瞄优先级最高，默认占用，客户端让位。
                bool_setting("Vision NUC Occupied", True),
                number_setting("Speed Scale %", 100, 10, 100, 5),
                number_setting("Yaw Scale %", 100, 10, 100, 5),
                number_setting("Gimbal Slew", 8, 1, 45, 1),
                # 驾驶上行通道。virtual_rc 复用固件已生效的 WASD 驾驶
                # （chassis_omni.c 读 rc->key.v 直接产生斜坡速度，自带限幅与
                # 低通），固件只需补一处 VIRTUAL_RC_CTRL → rc_ctrl 接线；
                # robot_cmd 则需新增 speed_vector 消费者。选 both 可平滑过渡。
                mode_setting(
                    "Drive Channel",
                    "robot_cmd",
                    ["robot_cmd", "virtual_rc", "both"],
                ),
                # 驾驶/键鼠协议槽的可自定义绑定。默认等于历史物理键（W/A/S/D…），
                # 改绑后上位机发的仍是同一个协议槽位，因此不影响固件语义，
                # 也不影响“只透传驾驶槽”的安全掩码。
                *[
                    mode_setting(f"Bind {slot}", slot, CONTROL_BIND_OPTIONS)
                    for slot in CONTROL_SLOT_ORDER
                ],
            ],
            description=(
                "队内 StandardRobot++ 0x5A 链路：读取机器人自身遥测驱动 HUD，"
                "并上发云台角度与底盘速度。fire 位始终为 0。"
            ),
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
                mode_setting("Combat Preset", "Polar", ["Polar", "Minecraft 1.21.11"]),
                mode_setting("Density", "CombatCompact", ["CombatCompact", "Balanced", "DebugFull"]),
                mode_setting("Visual Tone", "LiquidClean", ["LiquidClean", "PolarLegacy"]),
                mode_setting("Target Style", "RoundCompact", ["RoundCompact", "BarOnly", "FullCard"]),
                mode_setting("Right Display", "ModuleListHud", HUD_MODES),
                mode_setting("ModuleList Style", "JelloActiveMods", MODULE_LIST_STYLES),
                mode_setting("Jello List Size", "Normal", ["Normal", "Small", "Tiny"]),
                bool_setting("Jello List Animations", True),
                bool_setting("Jello List Sound", True, "Sigma ActiveMods toggle sound."),
                mode_setting("ModuleList Surface", "LiquidGlass", ["Native", "LiquidGlass"]),
                mode_setting("ModuleList Alignment", "Right", ["Right", "Left"]),
                mode_setting("ModuleList Order", "Descending", ["Descending", "Ascending"]),
                mode_setting("Module Tags", "Category", ["Category", "Keybind", "Off"]),
                mode_setting("Module Animation", "FlyFlip", ["FlyFlip", "Slide", "Fade"]),
                action_setting(
                    "Restore Compact HUD",
                    "仅恢复紧凑 HUD、小地图边框和目标面板，不修改模块开关、按键、连接或比赛配置。",
                ),
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
                number_setting("HUD Scale", 1.0, 0.6, 2.5, 0.05),
                number_setting("GUI Scale", 1.0, 0.6, 2.5, 0.05),
                number_setting("Animation Speed", 1.0, 0.5, 1.8, 0.05),
                mode_setting("HUD Refresh Rate", "144", ["120", "144", "165", "240"]),
                number_setting("Reference Width", 1280, 960, 3840, 80, "px"),
                number_setting("Reference Height", 720, 540, 2160, 60, "px"),
            ],
            enabled=True,
            description="适配不同显示器尺寸的响应式 UI 缩放控制器。",
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
            "CombatTargetHUD",
            "Render",
            "None",
            [
                mode_setting("Visual", "Clear", ["Classic", "Clear", "Enhanced"]),
                bool_setting("Show Bars", False),
                bool_setting("Show Status Line", True),
            ],
            enabled=True,
            description="围绕中心准星显示本机血量、射击热量、自瞄状态和弹丸余量。",
        ),
        Module(
            "MinecraftCombatHUD",
            "Render",
            "None",
            [
                mode_setting("GUI Scale", "Auto", ["Auto", "1x", "2x", "3x", "4x", "5x", "6x", "7x", "8x"]),
                number_setting(
                    "Scale Multiplier",
                    1.0,
                    0.50,
                    2.00,
                    0.05,
                    description="仅缩放原版 Minecraft HUD 像素层；外挂覆盖层保持独立缩放。",
                ),
                *[
                    mode_setting(f"Slot {index}", default, ["Empty", default])
                    for index, default in enumerate(
                        (
                            "ArmorAutoAim", "ArmorLock", "FrictionWheel", "SpinMode",
                            "EconomyPanel", "ProjectileShop", "TacticalMap", "MatchChat", "ClickGui",
                        ),
                        1,
                    )
                ],
            ],
            enabled=False,
            description="Minecraft 1.21.11 战斗 HUD 与九格自定义功能栏；可从 Jello ClickGUI 拖放模块。",
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
                number_setting("Scale", 1.0, 0.5, 2.0, 0.05),
                mode_setting("Language", "简体中文", ["English", "简体中文"], "切换界面显示语言；不改变配置键和协议值。"),
                mode_setting("Render Backend", "Unified GPU", ["Unified GPU", "Compatibility Painter"]),
                bool_setting("Save Layout", True),
                action_group_setting(
                    "Old ClickGUIs",
                    LEGACY_GUI_MODES,
                    "显示所有旧 ClickGUI",
                ),
            ],
            description="Jello Sigma 5.1.0 主 ClickGUI 一比一复刻；旧界面收纳在 Old ClickGUIs 中。",
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
    from .student_ui import student_modules
    modules.extend(student_modules())
    # Function-bar slots accept any module, but the list is populated only
    # after registry construction so it cannot drift when modules are added.
    function_bar = next((module for module in modules if module.name == "MinecraftCombatHUD"), None)
    function_choices = ["Empty", *(module.name for module in modules if module.name != "MinecraftCombatHUD")]
    if function_bar is not None:
        for setting in function_bar.settings:
            if setting.name.startswith("Slot "):
                setting.options = list(function_choices)
    return ModuleRegistry(modules)
