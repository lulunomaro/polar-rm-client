"""Robot runtime state and compatibility facade for module settings/catalogs.

Existing callers keep importing settings and default construction from here.
The mutable settings model and pure catalog are independently usable without
runtime state or environment discovery.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import time
from typing import Any, Callable

from .match_modes import MATCH_MODE_KEYS
from .robot_profiles import RMUC_ROBOTS
from .scenarios import SCENARIO_MODES
from .referee_profiles import DEFAULT_PROFILE, supported_profiles
from .serial_ports import port_choices as polar_port_choices
from .serial_ports import rc_port_choices as dt7_port_choices
from .state_overlay import validated_polar_overlay
from .module_settings import (
    CATEGORIES, CONTROL_SLOT_ORDER, CONTROL_BIND_OPTIONS, CONTROL_RESERVED_KEYS,
    MODULE_RESERVED_KEYS, MODULE_BIND_OPTIONS, PRIMARY_GUI_MODE, LEGACY_GUI_MODES,
    GUI_MODES, GUI_MODE_MIGRATIONS, HUD_MODES, MODULE_LIST_STYLES,
    MODULE_LIST_RENDERER_KEYS, TACTICAL_MAP_STATUS_DISPLAYS, normalize_gui_mode,
    normalize_legacy_gui_mode, Setting, Module, ModuleRegistry, GuiModeManager,
    bool_setting, number_setting, mode_setting, action_setting, action_group_setting,
)


def normalize_angle_deg(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


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
    # RobotInjuryStat is cumulative and optional per calibre. Each counter
    # needs its own observed baseline before it can publish a positive delta.
    injury_stats_initialized: bool = False
    # Runtime provenance, excluded from the compatibility constructor.
    projectile_damage_small_initialized: bool = field(default=False, init=False)
    projectile_damage_large_initialized: bool = field(default=False, init=False)
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
        只接受 state_overlay 中声明的遥测字段；不能改写设置、授权或官方容器。
        通用协议扩展仍保留在 polar_overlay，供已有消费者读取。
        """

        if not self.polar_overlay:
            return
        if not self.polar_link_active:
            return
        for name, value in validated_polar_overlay(self.polar_overlay).items():
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


def build_default_registry() -> ModuleRegistry:
    """Compatibility adapter: discover choices once, then assemble owned values.

    Keep the historical call order and blocking flags here. The two explicit
    controller-port snapshots are unchanged; changing their discovery strategy
    is a separate behavior decision, not part of catalog extraction.
    """
    from .module_catalog import CatalogChoices, assemble_registry, default_modules

    choices = CatalogChoices(
        scenario_modes=tuple(SCENARIO_MODES),
        match_modes=tuple(MATCH_MODE_KEYS),
        robot_roles=tuple(profile.key for profile in RMUC_ROBOTS),
        referee_profile=DEFAULT_PROFILE,
        referee_profiles=tuple(supported_profiles()),
        angle_rx_ports=tuple(polar_port_choices()),
        angle_tx_ports=tuple(polar_port_choices()),
        robot_ports=tuple(polar_port_choices(nonblocking=True)),
        dt7_ports=tuple(dt7_port_choices(nonblocking=True)),
        cc_ports=tuple(polar_port_choices(nonblocking=True)),
    )
    modules = default_modules(choices)
    from .student_ui import student_modules

    return assemble_registry(modules, student_modules())
