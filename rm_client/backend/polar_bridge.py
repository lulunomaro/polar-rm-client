"""队内 0x5A 链路的状态桥、控制仲裁与驾驶量纲换算。

拆成独立纯逻辑模块的原因：这一段既不依赖 Qt，也不依赖真实串口，但它同时
决定「HUD 显示什么真值」和「谁有权驱动云台」，是全链路里最容易出错、也最需要
被单测钉住的部分。

三条来自实车研判的硬规则：

1. **overlay 而非改写 mock**。`RobotState.tick()` 里有约 50 行正弦仿真，且它在
   非 LabDebug 场景会整体早退（`model.py` 的 mock 门）。逐个字段加 if 既脆弱又
   会漏。这里改为把队内真值汇总成 overlay 字典，由 `RobotState.tick()` 在**末尾**
   统一回写，因此不受「mock 先跑还是段先到」的顺序影响。

2. **视觉 NUC 优先**。按现场约定，视觉 NUC 的自瞄闭环优先级最高；只要它被标记为
   占用，本客户端就不发任何云台角度。退出占用后客户端才可接管，且必须操作员显式
   开启。云台反馈长时间不收敛到已发目标，判定存在第二主控抢环，自动锁出。

3. **驾驶量纲**。`control.py` 产出的 vx/vy/wz 是归一化 [-1,1]，固件
   `GetScCmdChassisSpeed` 要 m/s、`GetScCmdChassisVelocity(AX_Z)` 要 rad/s，且
   固件自身限幅是 ±1.5 m/s、±2.0 rad/s（`robot_param_omni_infantry.h:87-88`）。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .polar_link import (
    SEG_ALL_ROBOT_HP,
    SEG_BUFF,
    SEG_DEBUG,
    SEG_EVENT,
    SEG_GAME_STATUS,
    SEG_GROUND_ROBOT_POSITION,
    SEG_IMU,
    SEG_JOINT_STATE,
    SEG_REFEREE_WARNING,
    SEG_RFID_STATUS,
    SEG_ROBOT_MOTION,
    SEG_ROBOT_STATE_INFO,
    SEG_ROBOT_STATUS,
    SEG_SHOOT_DATA,
    PolarSegment,
    rad_to_deg,
)

# 链路新鲜期。超过即认为机器人不再供数，overlay 停止回写、驾驶被禁止。
LINK_FRESH_S = 2.0
# 云台指令发出后，允许反馈收敛到该容差内；超过 CONFLICT_TIMEOUT_S 未收敛判定抢环。
GIMBAL_CONFLICT_TOLERANCE_DEG = 12.0
GIMBAL_CONFLICT_TIMEOUT_S = 0.60
# 单周期云台指令最大变化，避免视觉抖动直接把炮管甩满行程。
DEFAULT_GIMBAL_SLEW_DEG_PER_STEP = 8.0

# 固件侧限幅（机体系：前 +x、左 +y、上 +z；+wz 为逆时针）
MAX_LINEAR_SPEED_MPS = 1.5
MAX_YAW_RATE_RPS = 2.0


# 段 id → 人读名称。自检报告、HUD tooltip 与现场日志都靠它把十六进制段号
# 翻译成能看懂的东西，避免车旁边对着 0x1D 猜。
SEGMENT_NAMES: dict[int, str] = {
    SEG_DEBUG: "Debug",
    SEG_IMU: "IMU",
    SEG_ROBOT_STATE_INFO: "RobotStateInfo",
    SEG_GAME_STATUS: "GameStatus",
    SEG_ALL_ROBOT_HP: "AllRobotHp",
    SEG_EVENT: "Event",
    SEG_REFEREE_WARNING: "RefereeWarning",
    SEG_ROBOT_STATUS: "RobotStatus",
    SEG_BUFF: "Buff",
    SEG_SHOOT_DATA: "ShootData",
    SEG_RFID_STATUS: "RfidStatus",
    SEG_GROUND_ROBOT_POSITION: "GroundRobotPosition",
    SEG_JOINT_STATE: "JointState",
    SEG_ROBOT_MOTION: "RobotMotion",
}


def scale_drive(
    vx_norm: float,
    vy_norm: float,
    wz_norm: float,
    *,
    max_linear_mps: float = MAX_LINEAR_SPEED_MPS,
    max_yaw_rps: float = MAX_YAW_RATE_RPS,
) -> tuple[float, float, float]:
    """把 `ChassisCommand` 的归一化键鼠量换算成固件要的物理速度并限幅。"""

    vx = max(-1.0, min(1.0, float(vx_norm))) * float(max_linear_mps)
    vy = max(-1.0, min(1.0, float(vy_norm))) * float(max_linear_mps)
    wz = max(-1.0, min(1.0, float(wz_norm))) * float(max_yaw_rps)
    return vx, vy, wz


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


_STATE_FIELDS_CACHE: dict[int, dict[str, str]] | None = None
# 第 54 轮新增：失败的次数与最后一次原因。
# 它们**不参与判定**（仍然返回空表 = fail-closed），只是让它看得见。
_STATE_FIELDS_CACHE_ERRORS = 0
_STATE_FIELDS_CACHE_LAST_ERROR = None


def _spec_state_fields() -> dict[int, dict[str, str]]:
    """取规格里声明的“解码字段 → overlay 字段”映射（带缓存）。

    有了它，“电控新增一个下行段”只需在 JSON 里写
    `"state_fields": {"解码字段名": "polar_xxx"}`，不必改 Python。
    规格不可用时返回空表，此时无专用处理器的段仍按丢弃处理，不会造假数据。
    """

    global _STATE_FIELDS_CACHE
    if _STATE_FIELDS_CACHE is None:
        try:
            from . import polar_spec

            loaded = polar_spec.get_spec()
            _STATE_FIELDS_CACHE = dict(loaded.segment_state_fields) if loaded.ok else {}
        except Exception as exc:  # noqa: BLE001
            # 第 54 轮：原来这里也是 `_STATE_FIELDS_CACHE = {}` ——
            # **一次瞬时异常就把空表写进进程级缓存**，之后每次调用都看见
            # `is not None`、直接返回 `{}`，于是
            # **本进程余下时间里所有按 spec 映射的段都被静默丢弃**，且不留痕迹。
            #
            # 瞬时异常真实存在：配置正被原地重写时的解码竞争、
            # 杀毒软件短暂锁文件、Windows 上的 `PermissionError`。
            # 把它们当成永久结论，是把**一次抖动放大成整场**。
            #
            # **所以失败不写进缓存** —— 保持 `None` 让下一次调用重试，
            # 瞬时抖动会自愈。代价是"规格真的取不到"时每次都重试，
            # 而 `get_spec()` 自己有缓存，这条代价是有界的。
            #
            # ⚠ 控制流**不变**：仍然返回空表（fail-closed，仍然丢弃段），
            # 只是不再把它**记成结论**。
            global _STATE_FIELDS_CACHE_ERRORS, _STATE_FIELDS_CACHE_LAST_ERROR
            _STATE_FIELDS_CACHE_ERRORS += 1
            _STATE_FIELDS_CACHE_LAST_ERROR = f"{type(exc).__name__}: {exc}"
            return {}
    return _STATE_FIELDS_CACHE


class PolarStateBridge:
    """把 14 个在发段翻成 `RobotState` overlay。

    每个段的处理互相隔离：一个坏段只丢掉自己，不打断接收，也不污染其它字段。
    """

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self.clock = clock
        self.overlay: dict[str, Any] = {}
        self.last_segment_at = 0.0
        self.last_segment_id = -1
        self.segment_counts: dict[int, int] = {}
        self.dropped = 0
        self.last_error = ""
        # 0x03 的部件错误位单独留一份，供 HUD 红字用
        self.module_errors: dict[str, bool] = {}
        self.debug_packages: dict[str, float] = {}
        # 机器人上报的自身位置/朝向，用于驾驶反馈
        self.robot_report: dict[str, float] = {}

    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.link_age_s <= LINK_FRESH_S and bool(self.segment_counts)

    @property
    def link_age_s(self) -> float:
        if self.last_segment_at <= 0.0:
            return float("inf")
        return max(0.0, self.clock() - self.last_segment_at)

    def reset(self) -> None:
        self.overlay.clear()
        self.module_errors.clear()
        self.debug_packages.clear()
        self.robot_report.clear()
        self.segment_counts.clear()
        self.last_segment_at = 0.0
        self.last_segment_id = -1
        self.dropped = 0
        self.last_error = ""

    # ------------------------------------------------------------------
    def apply(self, segment: PolarSegment) -> None:
        """处理一条下行段。任何异常都被吞掉并计数，保证接收线程不被打断。"""

        self.last_segment_id = segment.segment_id
        self.segment_counts[segment.segment_id] = self.segment_counts.get(segment.segment_id, 0) + 1
        try:
            handler = _HANDLERS.get(segment.segment_id)
            if handler is None:
                # 没有专用处理器时，按规格声明的 state_fields 通用落库。
                # 新增一个下行段只需在 JSON 里写
                #   "state_fields": {"字段名": "polar_xxx"}
                # 不必改 Python。没声明的仍然计为丢弃，不静默造假数据。
                if self._apply_generic_segment(segment):
                    self.last_segment_at = self.clock()
                else:
                    self.dropped += 1
                return
            handler(self, segment.fields)
            # 只有产生可信数据的段才刷新新鲜期
            self.last_segment_at = self.clock()
        except Exception as exc:  # pragma: no cover - 防御性
            self.dropped += 1
            self.last_error = f"段 0x{segment.segment_id:02X}: {exc}"

    def _apply_generic_segment(self, segment: PolarSegment) -> bool:
        """按规格的 `state_fields` 把任意新段写进 overlay。返回是否处理了。

        只接受有限数值、布尔与字符串，避免把奇怪对象塞进状态。
        """

        mapping = _spec_state_fields().get(int(segment.segment_id)) or {}
        if not mapping:
            return False
        wrote = 0
        for field_name, state_name in mapping.items():
            if field_name not in segment.fields:
                continue
            target = str(state_name or "").strip()
            if not target:
                continue
            value = segment.fields[field_name]
            if isinstance(value, float) and not math.isfinite(value):
                continue
            self.overlay[target] = value
            wrote += 1
        return wrote > 0

    # ---------------- 各段处理 ----------------
    def _handle_imu(self, values: dict[str, Any]) -> None:
        self.overlay["chassis_yaw_deg"] = normalize_deg(rad_to_deg(_finite(values.get("yaw_rad"))))
        self.overlay["imu_pitch_deg"] = rad_to_deg(_finite(values.get("pitch_rad")))
        self.overlay["imu_roll_deg"] = rad_to_deg(_finite(values.get("roll_rad")))
        self.overlay["chassis_yaw_rate_rps"] = _finite(values.get("yaw_vel_rps"))

    def _handle_joint_state(self, values: dict[str, Any]) -> None:
        gimbal_yaw = normalize_deg(rad_to_deg(_finite(values.get("yaw_rad"))))
        self.overlay["gimbal_yaw_deg"] = gimbal_yaw
        self.overlay["gimbal_pitch_deg"] = rad_to_deg(_finite(values.get("pitch_rad")))
        chassis_yaw = self.overlay.get("chassis_yaw_deg")
        if chassis_yaw is not None:
            self.overlay["relative_yaw_deg"] = normalize_deg(gimbal_yaw - float(chassis_yaw))

    def _handle_robot_motion(self, values: dict[str, Any]) -> None:
        self.overlay["measured_vx_mps"] = _finite(values.get("vx_mps"))
        self.overlay["measured_vy_mps"] = _finite(values.get("vy_mps"))
        self.overlay["measured_wz_rps"] = _finite(values.get("wz_rps"))

    def _handle_robot_status(self, values: dict[str, Any]) -> None:
        self.overlay["current_health"] = int(_finite(values.get("current_hp")))
        self.overlay["max_health"] = int(_finite(values.get("maximum_hp")))
        self.overlay["current_heat"] = _finite(values.get("barrel_cooling_value"))
        self.overlay["max_heat"] = _finite(values.get("barrel_heat_limit"))
        # 注意：不写 heat_cooldown_rate，也不写 current_buffer_energy；前者是
        # 官方 GlobalUnitStatus 拥有的语义，后者由 0x0B 提供而固件没发。
        self.overlay["power_limit"] = int(_finite(values.get("chassis_power_limit")))
        self.overlay["projectile_allowance_17mm"] = int(_finite(values.get("projectile_allowance_17mm")))
        self.overlay["projectile_allowance_42mm"] = int(_finite(values.get("projectile_allowance_42mm")))
        self.overlay["polar_gold_coin"] = int(_finite(values.get("remaining_gold_coin")))
        self.overlay["polar_fortress_allowance"] = int(_finite(values.get("projectile_allowance_fortress")))
        self.overlay["armor_id"] = int(_finite(values.get("armor_id")))
        robot_id = int(_finite(values.get("robot_id")))
        if robot_id:
            # 写进与操作员选择不重叠的字段：早期直接写 `robot_id` 会静静
            # 改掉用户在 ClickGUI 里选的兵种，既丢了“选错车”的比对基准，
            # 也让操作员看到自己的选择被改而不知道为什么。
            self.overlay["polar_reported_robot_id"] = robot_id
        # 官方侧 robot_positions 是 list[dict]且由 manager 重建，不直接写。
        self.robot_report = {
            "x": _finite(values.get("x")),
            "y": _finite(values.get("y")),
            "angle_deg": _finite(values.get("angle_deg")),
        }
        self.overlay["polar_self_x"] = self.robot_report["x"]
        self.overlay["polar_self_y"] = self.robot_report["y"]
        self.overlay["polar_self_angle_deg"] = self.robot_report["angle_deg"]

    def _handle_buff(self, values: dict[str, Any]) -> None:
        self.overlay["small_buff"] = bool(int(_finite(values.get("attack_buff"))))
        self.overlay["large_buff"] = bool(int(_finite(values.get("defence_buff"))))
        self.overlay["recovery_buff"] = int(_finite(values.get("recovery_buff")))
        self.overlay["vulnerability_buff"] = int(_finite(values.get("vulnerability_buff")))
        self.overlay["current_chassis_energy"] = int(_finite(values.get("remaining_energy")))
        # cooling_buff 在固件 UsbSendBuffData() 里漏填，只记录不驱动 HUD
        self.overlay["report_cooling_buff"] = int(_finite(values.get("cooling_buff")))

    def _handle_state_info(self, values: dict[str, Any]) -> None:
        # 固件 state 位为 1 表示错误；wheel_online 这类展示位取反
        self.module_errors = {
            "chassis": bool(values.get("chassis_error")),
            "gimbal": bool(values.get("gimbal_error")),
            "shoot": bool(values.get("shoot_error")),
            "arm": bool(values.get("arm_error")),
            "custom_controller": bool(values.get("custom_controller_error")),
        }
        self.overlay["chassis_error"] = self.module_errors["chassis"]
        self.overlay["gimbal_error"] = self.module_errors["gimbal"]
        self.overlay["shoot_error"] = self.module_errors["shoot"]
        self.overlay["wheel_online"] = not self.module_errors["chassis"]
        self.overlay["drive_ready"] = not any(self.module_errors.values())

    def _handle_game_status(self, values: dict[str, Any]) -> None:
        self.overlay["polar_game_progress"] = int(_finite(values.get("game_progress")))
        self.overlay["polar_game_type"] = int(_finite(values.get("game_type")))
        self.overlay["polar_stage_remain_s"] = int(_finite(values.get("stage_remain_time"))) / 1000.0

    def _handle_all_robot_hp(self, values: dict[str, Any]) -> None:
        # 官方侧 `official_robot_health` 是 **list**（manager.py:444 用 [:10] 切片），
        # 队内链路只提供己方 5 个 ID，写成 dict 专存，不改官方结构。
        ally = {}
        for key, role in (
            ("ally_1_hp", 1),
            ("ally_2_hp", 2),
            ("ally_3_hp", 3),
            ("ally_4_hp", 4),
            ("ally_7_hp", 7),
        ):
            if key in values:
                ally[role] = int(_finite(values[key]))
        if ally:
            self.overlay["polar_ally_hp"] = ally
        self.overlay["base_health"] = int(_finite(values.get("ally_base_hp")))
        self.overlay["outpost_health"] = int(_finite(values.get("ally_outpost_hp")))

    def _handle_shoot_data(self, values: dict[str, Any]) -> None:
        self.overlay["native_bullet_speed"] = _finite(values.get("initial_speed_mps"))
        self.overlay["launch_frequency_hz"] = int(_finite(values.get("launching_frequency_hz")))
        self.overlay["bullet_type"] = int(_finite(values.get("bullet_type")))

    def _handle_ground_positions(self, values: dict[str, Any]) -> None:
        # `map_robots` 是 list[dict]且由 manager 从 robot_positions 重建，写它会打挂
        # 战术地图渲染。队内位置只当作冗余校验源单独暂存。
        positions = dict(self.overlay.get("polar_ground_positions") or {})
        pairs = (
            ("hero_x", "hero_y", 1),
            ("engineer_x", "engineer_y", 2),
            ("standard_3_x", "standard_3_y", 3),
            ("standard_4_x", "standard_4_y", 4),
        )
        for x_key, y_key, robot_id in pairs:
            x, y = _finite(values.get(x_key)), _finite(values.get(y_key))
            if x == 0.0 and y == 0.0:
                continue  # 固件未填时成对为 0，按无观测处理，别画出原点假点
            positions[robot_id] = (x, y)
        if positions:
            self.overlay["polar_ground_positions"] = positions

    def _handle_rfid(self, values: dict[str, Any]) -> None:
        # 只存原始位图。官方 0x030B/RFID 的位定义未在本仓得到，拿“猜测的
        # bit0”去驱动补给区门控会直接改变能否兑换弹药，所以保持现状的
        # fail-unknown：仅当队内给出位表后，才允许写 supply_zone_known。
        self.overlay["rfid_status"] = int(_finite(values.get("rfid_status")))

    def _handle_event(self, values: dict[str, Any]) -> None:
        self.overlay["polar_event_bitmap"] = int(_finite(values.get("event_data")))

    def _handle_warning(self, values: dict[str, Any]) -> None:
        self.overlay["polar_warning_level"] = int(_finite(values.get("level")))
        self.overlay["polar_warning_robot_id"] = int(_finite(values.get("offending_robot_id")))
        self.overlay["polar_warning_count"] = int(_finite(values.get("count")))

    def _handle_debug(self, values: dict[str, Any]) -> None:
        packages = values.get("packages") or {}
        if isinstance(packages, dict):
            self.debug_packages = {
                str(name): _finite(value) for name, value in packages.items()
            }
        self.overlay["polar_debug_packages"] = self.debug_packages


_HANDLERS: dict[int, Callable[[PolarStateBridge, dict[str, Any]], None]] = {
    SEG_IMU: PolarStateBridge._handle_imu,
    SEG_JOINT_STATE: PolarStateBridge._handle_joint_state,
    SEG_ROBOT_MOTION: PolarStateBridge._handle_robot_motion,
    SEG_ROBOT_STATUS: PolarStateBridge._handle_robot_status,
    SEG_BUFF: PolarStateBridge._handle_buff,
    SEG_ROBOT_STATE_INFO: PolarStateBridge._handle_state_info,
    SEG_GAME_STATUS: PolarStateBridge._handle_game_status,
    SEG_ALL_ROBOT_HP: PolarStateBridge._handle_all_robot_hp,
    SEG_SHOOT_DATA: PolarStateBridge._handle_shoot_data,
    SEG_GROUND_ROBOT_POSITION: PolarStateBridge._handle_ground_positions,
    SEG_RFID_STATUS: PolarStateBridge._handle_rfid,
    SEG_EVENT: PolarStateBridge._handle_event,
    SEG_REFEREE_WARNING: PolarStateBridge._handle_warning,
    SEG_DEBUG: PolarStateBridge._handle_debug,
}


class PolarMotionVerifier:
    """指令-实测偏差检测：把「链路正常但车不动」变成可读告警。

    实车研判发现：固件 `GetScCmdChassisSpeed/Velocity` 当前是零调用者的死
    getter，而且 `ChassisPublish()` 只在平衡底盘实现，omni/mecanum 回读为空
    （`usb_task.c:1186-1188` 直接 return）。也就是说「发了速度但车不动」在链路
    层完全看不出来。本类用 0x1E 实测速度做交叉校验，区分三种情形：

    - NO_FEEDBACK：根本没收到过 0x1E —— 回读链路断（固件需补 Publish）
    - STUCK：持续给了速度但实测几乎不动 —— 底盘消费者未接线
    - OK：给了速度且实测跟着动
    """

    MOVE_COMMAND_MPS = 0.30      # 超过此量认为“操作员确实在试图开车”
    MOVED_THRESHOLD_MPS = 0.12   # 实测超过此量认为“车动了”
    GRACE_S = 0.35               # 给定速到反馈上来的允许延迟
    STUCK_TIMEOUT_S = 1.20       # 超过此时间仍未动判为 STUCK
    FEEDBACK_SEEN_TIMEOUT_S = 2.0
    # 位移兜底：在 STUCK_TIMEOUT 内至少移动这么多才算“车动了”。
    # 0.3 m/s × 1.2 s 理论可走 0.36 m，取 0.05 m 既能躲开测速噪声又不会漏判。
    DISPLACEMENT_THRESHOLD_M = 0.05

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self.clock = clock
        self.state = "IDLE"
        self.detail = ""
        self.commanded_mps = 0.0
        self.measured_mps = 0.0
        self._moving_since: float | None = None
        self._last_feedback_segment_at: float | None = None
        self._ever_had_feedback = False
        # 位移兜底：omni/mecanum 没有速度观测器（ChassisPublish 只在平衡底盘实现），
        # 但官方 0x0A RobotStatus 带测速模块的 x/y，可以用位移判定“车到底动没动”。
        self._position_origin: tuple[float, float] | None = None
        self._position_now: tuple[float, float] | None = None
        self.displacement_m = 0.0

    # 这些状态**本身**代表“没在推杆”，但还不够：`OK` 也可能是“正在推杆且车已在响
    # 应”。因此重锁必须同时满足“手上没有驾驶指令”（见 note_position）。
    # 用白名单而不是黑名单：新增状态时默认“保持参考点”，不会把位移量归零。
    POSITION_RELOCK_STATES = frozenset({"IDLE", "OK", "NO_LINK", "STALE_FEEDBACK"})

    #: 低于此速度视为没推杆，可以重锁位移参考点
    RELOCK_COMMAND_EPS_MPS = 0.05

    def note_position(self, x: float, y: float) -> None:
        """从 0x0A RobotStatus 的测速模块坐标记录一次位置。

        参考点只在没推杆时重锁。若在推杆期间不断重锁，位移会被算成相邻两帧
        的增量而接近 0，车动了也判不出来。

        为什么不能只看状态名：`OK` 在重锁白名单里，而 `OK` 恰恰是“推杆中且已
        测到位移”的结果。只按状态判会形成自相消除的振荡：测到位移→判 OK→
        重锁参考点→位移归零→退回 NO_FEEDBACK→再测……位移永远为 0。
        所以真正的判据是“手上有没有驾驶指令”，而不是处于哪个状态。
        """

        x, y = _finite(x), _finite(y)
        commanding = self.commanded_mps > self.RELOCK_COMMAND_EPS_MPS
        if self._position_now is None or (
            not commanding and self.state in self.POSITION_RELOCK_STATES
        ):
            self._position_origin = (x, y)
        self._position_now = (x, y)
        if self._position_origin is not None:
            self.displacement_m = math.hypot(
                x - self._position_origin[0], y - self._position_origin[1]
            )

    def note_motion_segment(self) -> None:
        self._ever_had_feedback = True
        self._last_feedback_segment_at = self.clock()

    def update(
        self,
        *,
        link_active: bool,
        commanded_vx: float,
        commanded_vy: float,
        commanded_wz: float,
        measured_vx: float | None,
        measured_vy: float | None,
        measured_wz: float | None,
    ) -> str:
        now = self.clock()
        self.commanded_mps = math.hypot(_finite(commanded_vx), _finite(commanded_vy))
        linear_measured: float | None = None
        if measured_vx is not None or measured_vy is not None:
            linear_measured = math.hypot(_finite(measured_vx), _finite(measured_vy))
            self.measured_mps = linear_measured
        else:
            self.measured_mps = 0.0
        rate_measured = _finite(measured_wz) if measured_wz is not None else None
        trying = self.commanded_mps >= self.MOVE_COMMAND_MPS or abs(_finite(commanded_wz)) >= 0.3
        moving_enough = (linear_measured is not None and linear_measured >= self.MOVED_THRESHOLD_MPS) or (
            rate_measured is not None and abs(rate_measured) >= 0.15
        )

        if not link_active:
            self.state, self.detail = "NO_LINK", "队内链路未活跃，无法判定车辆是否响应"
            self._moving_since = None
            return self.state

        if not self._ever_had_feedback:
            # 没有 0x1E，改用 0x0A 测速模块的位移做判定，而不是直接放弃诊断。
            moved_by_position = self.displacement_m >= self.DISPLACEMENT_THRESHOLD_M
            if moved_by_position:
                self.state, self.detail = "OK", ""
                self._moving_since = None
                return self.state
            self.state = "NO_FEEDBACK"
            self.detail = (
                f"无 0x1E 回读，改用 0x0A 测速模块位移判定："
                f"已下发 {self.commanded_mps:.2f} m/s，累计位移 {self.displacement_m:.3f} m"
                "（固件 ChassisPublish 仅在平衡底盘实现）"
            )
            if self._moving_since is None:
                self._moving_since = now
            elif now - self._moving_since > self.STUCK_TIMEOUT_S:
                self.state = "STUCK"
                self.detail = (
                    f"已下发 {self.commanded_mps:.2f} m/s 超过 {self.STUCK_TIMEOUT_S:.1f}s，"
                    f"0x0A 位移仅 {self.displacement_m:.3f} m：底盘指令消费者未接线"
                    "（固件 GetScCmdChassisSpeed/Velocity 无调用者）"
                )
            return self.state

        if self._last_feedback_segment_at is not None and (
            now - self._last_feedback_segment_at
        ) > self.FEEDBACK_SEEN_TIMEOUT_S:
            self.state = "STALE_FEEDBACK"
            self.detail = "0x1E 回读已超过 2s 未更新"
            self._moving_since = None
            return self.state

        if not trying:
            self.state, self.detail = "IDLE", ""
            self._moving_since = None
            return self.state

        if moving_enough:
            self.state, self.detail = "OK", ""
            self._moving_since = None
            return self.state

        if self._moving_since is None:
            self._moving_since = now
        elapsed = now - self._moving_since
        if elapsed < self.GRACE_S:
            self.state, self.detail = "WAITING", ""
        elif elapsed > self.STUCK_TIMEOUT_S:
            self.state = "STUCK"
            self.detail = (
                f"已下发 {self.commanded_mps:.2f} m/s 持续 {elapsed:.1f}s，"
                f"实测仅 {self.measured_mps:.2f} m/s："
                "首查遥控器三位拨杆 s[0] 是否在中间或上方——"
                "它在下方时固件走 CHASSIS_LOCK，根本不读驾驶键位图；"
                "次查固件底盘指令消费者是否已接线"
                "（未补补丁时 GetScCmdChassisSpeed/Velocity 无调用者）"
            )
        else:
            self.state = "NO_MOTION"
            # 不能等到 STUCK 才给解释：操作员在第一帧就需要知道“在发速度但车没动”，
            # 否则 NO_MOTION 只是一个没有信息量的黄字。
            # 把“拨杆位置”放在第一建议：它是上最常见的不响应原因，而且操作员
            # 自己就能立刻改；排在它后面才轮到“没打补丁”这种需要回工位的可能。
            self.detail = (
                f"已下发 {self.commanded_mps:.2f} m/s，实测 {self.measured_mps:.2f} m/s："
                "车辆尚未响应（先确认遥控器 s[0] 拨杆不在下方）"
            )
        return self.state

    def stats(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "commanded_mps": round(self.commanded_mps, 3),
            "measured_mps": round(self.measured_mps, 3),
            "displacement_m": round(self.displacement_m, 4),
            "evidence": "0x1E" if self._ever_had_feedback else "0x0A-position",
        }


def normalize_deg(angle: float) -> float:
    """归一到 (-180, 180]。"""

    value = math.fmod(float(angle), 360.0)
    if value > 180.0:
        value -= 360.0
    elif value <= -180.0:
        value += 360.0
    return value


def _angle_delta(a: float, b: float) -> float:
    return abs(normalize_deg(a - b))


@dataclass
class GimbalAuthority:
    """云台控制权仲裁结果。"""

    state: str  # PASSIVE | CLIENT | LOCKOUT
    may_command: bool
    reason: str = ""


class PolarControlArbiter:
    """云台控制仲裁：视觉 NUC 占用时让位，退出后需显式重新交权。

    `vision_nuc_occupied` 是现场约定的最高优先级输入；一旦置位，客户端立即停发
    云台角度（发零帧会甩炮，所以是「停发并保持最后一条」——与固件无断流归零的
    现状一致，等固件补了超时再改成归零）。
    """

    def __init__(
        self,
        *,
        slew_deg_per_step: float = DEFAULT_GIMBAL_SLEW_DEG_PER_STEP,
        tolerance_deg: float = GIMBAL_CONFLICT_TOLERANCE_DEG,
        conflict_timeout_s: float = GIMBAL_CONFLICT_TIMEOUT_S,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.slew = float(slew_deg_per_step)
        self.tolerance = float(tolerance_deg)
        self.conflict_timeout = float(conflict_timeout_s)
        self.clock = clock
        self.state = "PASSIVE"
        self.reason = "客户端云台控制未开启"
        self._commanded_at = 0.0
        self._commanded_yaw = 0.0
        self._commanded_pitch = 0.0
        self._converged = True
        self._divergent_since: float | None = None
        self._lockout_reason = ""

    # ------------------------------------------------------------------
    def request_takeover(self, *, client_gimbal_enabled: bool, vision_nuc_occupied: bool) -> None:
        if self.state == "LOCKOUT":
            return  # 锁出必须显式清除
        if not client_gimbal_enabled:
            self.state, self.reason = "PASSIVE", "客户端云台控制未开启"
            return
        if vision_nuc_occupied:
            self.state, self.reason = "PASSIVE", "视觉 NUC 自瞄占用中，客户端让位"
            return
        if self.state != "CLIENT":
            self.state, self.reason = "CLIENT", "客户端已接管云台"
            self._converged = True

    def clear_lockout(self) -> None:
        if self.state == "LOCKOUT":
            self.state, self.reason = "PASSIVE", "已清除抢环锁出，等待重新交权"
            self._lockout_reason = ""
            self._converged = True
            self._divergent_since = None

    @property
    def may_command_gimbal(self) -> bool:
        return self.state == "CLIENT"

    def authority(self) -> GimbalAuthority:
        return GimbalAuthority(self.state, self.may_command_gimbal, self.reason or self._lockout_reason)

    # ------------------------------------------------------------------
    def plan_gimbal(
        self,
        target_yaw_deg: float,
        target_pitch_deg: float,
        feedback_yaw_deg: float | None,
        feedback_pitch_deg: float | None,
    ) -> tuple[float, float] | None:
        """返回本周期应发送的云台角度（度）；None 表示不发。

        同时对「发了但不收敛」做抢环检测：连续超过 conflict_timeout 仍未进入
        容差，判定存在第二主控，锁出并发零以外的指令。
        """

        if not self.may_command_gimbal:
            return None

        now = self.clock()
        slewed_yaw = self._slew_from(self._slew_reference_yaw, target_yaw_deg)
        slewed_pitch = self._slew_from(self._slew_reference_pitch, target_pitch_deg)

        self._commanded_at = now
        self._commanded_yaw = slewed_yaw
        self._commanded_pitch = slewed_pitch

        if feedback_yaw_deg is None:
            # 还没有反馈可用（例如 0x1D 从未到达），不能据此判定抢环
            return (slewed_yaw, slewed_pitch)

        error = _angle_delta(slewed_yaw, float(feedback_yaw_deg))
        if feedback_pitch_deg is not None:
            error = max(error, abs(slewed_pitch - _finite(feedback_pitch_deg)))

        if error <= self.tolerance:
            self._converged = True
            self._divergent_since = None
        else:
            self._converged = False
            if self._divergent_since is None:
                # 记下“开始发散”的时刻，而不是每帧刷新；否则永远算不出超时
                self._divergent_since = now
            elif now - self._divergent_since > self.conflict_timeout:
                self.state = "LOCKOUT"
                self._lockout_reason = (
                    f"云台反馈偏差 {error:.1f}° 持续超过 {self.conflict_timeout:.2f}s 未收敛到已发目标，"
                    "判定存在第二主控抢环，客户端已锁出"
                )
                self.reason = self._lockout_reason
                self._divergent_since = None
        return (slewed_yaw, slewed_pitch)

    def _last_command_time(self) -> float:
        return self._commanded_at

    @property
    def diverging(self) -> bool:
        """云台指令已偏离反馈、正在计时。供 HUD 提前预警。"""

        return self._divergent_since is not None

    @property
    def divergent_seconds(self) -> float:
        """当前已连续发散多久，供 HUD 提示。"""

        if self._divergent_since is None:
            return 0.0
        return max(0.0, self.clock() - self._divergent_since)

    @property
    def _slew_reference_yaw(self) -> float:
        return self._commanded_yaw

    @property
    def _slew_reference_pitch(self) -> float:
        return self._commanded_pitch

    def _slew_from(self, reference: float, target: float) -> float:
        delta = normalize_deg(float(target) - float(reference))
        delta = max(-self.slew, min(self.slew, delta))
        return normalize_deg(reference + delta)

    def stats(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "may_command": self.may_command_gimbal,
            "reason": self.reason,
            "diverging": self.diverging,
            "divergent_seconds": round(self.divergent_seconds, 3),
            "commanded_yaw_deg": round(self._commanded_yaw, 2),
            "commanded_pitch_deg": round(self._commanded_pitch, 2),
            "converged": self._converged,
        }
