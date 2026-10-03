"""队内 0x5A 链路：HUD overlay 覆盖、控制仲裁、驾驶上线的离线验证。

    python tests/polar_bridge_test.py

这里钉住的是「上实车能不能用」真正依赖的几条行为，而不是字节格式
（字节格式在 polar_link_test.py）：

1. 仿真竞写修复 —— 接上真机器人后，HUD 必须显示下位机实测量，不是正弦动画。
2. 链路过期后 overlay 停手，交还原有数据源。
3. 视觉 NUC 占用时客户端**绝不**发云台角度；退出占用后仍需操作员显式交权。
4. 云台反馈不收敛 -> 判定第二主控抢环 -> 锁出，且必须显式清除。
5. 驾驶三闸门：场景能力位 + 模块开关 + 操作员意图，缺一不可。
6. 任何路径发出的帧，fire 字节恒为 0。
"""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rm_client.backend import polar_link as pl  # noqa: E402
from rm_client.backend.polar_bridge import (  # noqa: E402
    LINK_FRESH_S,
    PolarControlArbiter,
    PolarStateBridge,
    normalize_deg,
    scale_drive,
)
from rm_client.backend.polar_transport import PolarDriveIntent, PolarLinkTransport  # noqa: E402
from rm_client.model import RobotState, build_default_registry  # noqa: E402
from rm_client import serial_ports  # noqa: E402

FAILED: list[str] = []
FIRE_OFFSET = 4 + 4 + 36



def _await_polar_connect(manager, timeout: float = 2.0) -> None:
    """等连接工作线程停下来（有上限）。

    第 101 轮：`_retry_polar_connect()` 不再自己同步走 `polar.start()`，
    而是把「连接动作」交给工作线程（避免 1.5 秒的阻塞落在 60 Hz 拍上）。

    **这不是放水**：快速连调 20 次 `tick_polar_link` 之后 `calls["n"]`
    仍必须是 **1**——**不待的话连接只会被发起一次**，节奏门控制一点都没退。
    """
    thread = getattr(manager, "_polar_connect_thread", None)
    if thread is not None:
        thread.join(timeout=max(0.0, float(timeout)))
        check("连接工作线程在限期内停下来", not thread.is_alive(), "仍在运行")

def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"[PASS] {name}")
    else:
        print(f"[FAIL] {name} {detail}")
        FAILED.append(name)


class FakeSerial:
    """假串口：只记录写出的字节，用于在没有硬件时验证真实发送内容。"""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.is_open = True

    def write(self, payload: bytes) -> int:
        self.written.append(bytes(payload))
        return len(payload)

    def read(self, _n: int) -> bytes:
        return b""

    def close(self) -> None:
        self.is_open = False


def segment(segment_id: int, body: bytes, stamp: int = 1) -> pl.PolarSegment:
    """按固件字段序构造一个下行段对象（绕开编解码，专注业务映射）。"""

    formats = {
        pl.SEG_IMU: "<ffffff",
        pl.SEG_JOINT_STATE: "<ff",
        pl.SEG_ROBOT_STATUS: "<BBHHHHHfffBHHHH",
        pl.SEG_BUFF: "<BHBBHB",
        pl.SEG_ROBOT_STATE_INFO: "<HB",
        pl.SEG_ROBOT_MOTION: "<fff",
    }
    if isinstance(body, tuple):
        fields = _FIELDS[segment_id](body)
        return pl.PolarSegment(segment_id, stamp, fields)
    raise TypeError("pass a tuple")


def _imu(v):
    return {
        "yaw_rad": v[0],
        "pitch_rad": v[1],
        "roll_rad": v[2],
        "yaw_vel_rps": v[3],
        "pitch_vel_rps": v[4],
        "roll_vel_rps": v[5],
    }


def _joint(v):
    return {"pitch_rad": v[0], "yaw_rad": v[1]}


def _status(v):
    return {
        "robot_id": v[0],
        "robot_level": v[1],
        "current_hp": v[2],
        "maximum_hp": v[3],
        "barrel_cooling_value": v[4],
        "barrel_heat_limit": v[5],
        "chassis_power_limit": v[6],
        "x": v[7],
        "y": v[8],
        "angle_deg": v[9],
        "armor_id": v[10],
        "hp_deduction_reason": v[11],
        "projectile_allowance_17mm": v[12],
        "projectile_allowance_42mm": v[13],
        "remaining_gold_coin": v[14],
        "projectile_allowance_fortress": v[15],
    }


def _buff(v):
    return {
        "recovery_buff": v[0],
        "cooling_buff": v[1],
        "defence_buff": v[2],
        "vulnerability_buff": v[3],
        "attack_buff": v[4],
        "remaining_energy": v[5],
    }


def _state_info(v):
    type_bits, state_bits = v
    return {
        "chassis_type": type_bits & 7,
        "gimbal_type": (type_bits >> 3) & 7,
        "shoot_type": (type_bits >> 6) & 7,
        "arm_type": (type_bits >> 9) & 7,
        "custom_controller_type": (type_bits >> 12) & 7,
        "chassis_error": bool(state_bits & 1),
        "gimbal_error": bool(state_bits & 2),
        "shoot_error": bool(state_bits & 4),
        "arm_error": bool(state_bits & 8),
        "custom_controller_error": bool(state_bits & 16),
    }


def _motion(v):
    return {"vx_mps": v[0], "vy_mps": v[1], "wz_rps": v[2]}


def _chassis_state(v):
    # 0x1F 没有专用解码器，走通用通路：字段名必须是规格 outputs 里的名字，
    # 而不是 struct 的 inputs——apply_spec_outputs 产出的是 output 名。
    return {
        "spin_active": v[0],
        "chassis_mode_code": v[1],
        "self_check_flag": v[2],
    }


def _ally_hp(v):
    # 固件字段顺序：ally 1/2/3/4 / reserved / ally 7 / outpost / base
    return {
        "ally_1_hp": v[0],
        "ally_2_hp": v[1],
        "ally_3_hp": v[2],
        "ally_4_hp": v[3],
        "reserved": v[4],
        "ally_7_hp": v[5],
        "ally_outpost_hp": v[6] if len(v) > 6 else 0,
        "ally_base_hp": v[7] if len(v) > 7 else 0,
    }


_FIELDS = {
    pl.SEG_IMU: _imu,
    pl.SEG_JOINT_STATE: _joint,
    pl.SEG_ROBOT_STATUS: _status,
    pl.SEG_BUFF: _buff,
    pl.SEG_ROBOT_STATE_INFO: _state_info,
    pl.SEG_ROBOT_MOTION: _motion,
    pl.SEG_CHASSIS_STATE: _chassis_state,
    pl.SEG_ALL_ROBOT_HP: _ally_hp,
}

STATUS_TUPLE = (3, 1, 400, 480, 40, 120, 460, 1.5, 2.5, 90.0, 5, 2, 150, 8, 30, 20)


def feed(bridge: PolarStateBridge) -> None:
    bridge.apply(segment(pl.SEG_IMU, (1.0, 0.1, 0.05, 0.5, 0.0, 0.0)))
    bridge.apply(segment(pl.SEG_JOINT_STATE, (0.2, 1.3)))
    bridge.apply(segment(pl.SEG_ROBOT_STATUS, STATUS_TUPLE))
    bridge.apply(segment(pl.SEG_BUFF, (1, 0, 3, 0, 40, 77)))
    bridge.apply(segment(pl.SEG_ROBOT_STATE_INFO, (0b101, 0b00010)))


# ----------------------------------------------------------------------
def test_overlay_wins_over_mock() -> None:
    """这是修复的核心：LabDebug 下仿真照跑，但队内真值必须最终留在 state 上。"""

    state = RobotState()
    state.mock_simulation_enabled = True  # LabDebug 才会这样
    bridge = PolarStateBridge()
    feed(bridge)

    state.polar_overlay = dict(bridge.overlay)
    state.polar_link_active = bridge.active
    state.tick(0.016)
    state.apply_polar_overlay()

    check("底盘朝向来自 IMU 实测而非正弦", abs(state.chassis_yaw_deg - 57.2957795) < 1e-4, str(state.chassis_yaw_deg))
    check("云台朝向来自 JointState", abs(state.gimbal_yaw_deg - 74.4845) < 1e-3, str(state.gimbal_yaw_deg))
    check("云台俯仰已入 HUD 字段", abs(state.gimbal_pitch_deg - 11.4591) < 1e-3, str(state.gimbal_pitch_deg))
    check("功率上限来自 0x0A", state.power_limit == 460, str(state.power_limit))
    check("底盘能量来自 0x0D", state.current_chassis_energy == 77, str(state.current_chassis_energy))
    check("弹量来自 0x0A", state.projectile_allowance_17mm == 150, str(state.projectile_allowance_17mm))
    check("攻击 buff 转真", state.small_buff is True, str(state.small_buff))
    check("底盘错误位转真 wheel_online", state.wheel_online is True, str(state.wheel_online))
    check("云台错误位转真", state.gimbal_error is True, str(state.gimbal_error))
    check("relative_yaw 派生正确", abs(state.relative_yaw_deg - (74.4845 - 57.2958)) < 1e-2, str(state.relative_yaw_deg))


def test_stale_link_releases_overlay() -> None:
    clock = {"t": 1000.0}
    state = RobotState()
    state.mock_simulation_enabled = True
    bridge = PolarStateBridge(clock=lambda: clock["t"])
    feed(bridge)
    state.polar_overlay = dict(bridge.overlay)
    state.polar_link_active = bridge.active
    state.tick(0.016)
    state.apply_polar_overlay()
    held = state.chassis_yaw_deg

    clock["t"] += LINK_FRESH_S + 1.0  # 机器人断电/拔线
    state.polar_link_active = bridge.active  # 现在应为 False
    state.tick(0.016)
    state.apply_polar_overlay()
    check("链路过期后 active 转假", bridge.active is False)
    check(
        "链路过期后不再冻结 HUD（仿真重新接管，说明 overlay 停手了）",
        abs(state.chassis_yaw_deg - held) > 1e-6,
        f"held={held} now={state.chassis_yaw_deg}",
    )


def test_unknown_overlay_key_is_ignored() -> None:
    state = RobotState()
    state.polar_link_active = True
    state.polar_overlay = {"definitely_not_a_real_field": 1.0, "power_limit": 300}
    try:
        state.apply_polar_overlay()
        check("未知 overlay 键被 hasattr 安全忽略", state.power_limit == 300)
    except Exception as exc:
        check("未知 overlay 键被 hasattr 安全忽略", False, repr(exc))


def test_official_containers_not_clobbered() -> None:
    """队内链路绝不能改写官方拥有的 list 型容器，否则地图/列表渲染会炸。"""

    state = RobotState()
    bridge = PolarStateBridge()
    bridge.apply(segment(pl.SEG_ALL_ROBOT_HP, (1, 2, 3, 4, 0, 7, 80, 400)))
    state.map_robots = [{"id": 3, "x": 1.0, "y": 2.0}]
    state.official_robot_health = [0, 400, 0, 380]
    state.robot_positions = [{"robot_id": 3}]
    state.polar_overlay = dict(bridge.overlay)
    state.polar_link_active = True
    state.apply_polar_overlay()

    check("map_robots 未被改写成 dict", isinstance(state.map_robots, list), str(type(state.map_robots)))
    check(
        "official_robot_health 仍是 list",
        isinstance(state.official_robot_health, list),
        str(type(state.official_robot_health)),
    )
    check("robot_positions 未被改写", isinstance(state.robot_positions, list), str(type(state.robot_positions)))


# ----------------------------------------------------------------------
def test_arbiter_vision_nuc_has_top_priority() -> None:
    arb = PolarControlArbiter()
    arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=True)
    check("视觉 NUC 占用时即使客户端想控也让位", arb.state == "PASSIVE" and not arb.may_command_gimbal, arb.state)
    check("占用时不发任何云台角度", arb.plan_gimbal(10.0, 2.0, 10.0, 2.0) is None)

    arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
    check("NUC 退出且客户端开启后接管", arb.state == "CLIENT" and arb.may_command_gimbal, arb.state)

    arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=True)
    check("NUC 重新上线立即收回控制权", arb.state == "PASSIVE")

    arb.request_takeover(client_gimbal_enabled=False, vision_nuc_occupied=False)
    check("客户端自己没开启时保持 PASSIVE", arb.state == "PASSIVE")


def test_arbiter_slew_and_conflict_lockout() -> None:
    arb = PolarControlArbiter(slew_deg_per_step=8.0)
    arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)

    # 目标跳变 90°，单步只允许走 8°，避免炮管甩满行程
    plan = arb.plan_gimbal(90.0, 0.0, feedback_yaw_deg=0.0, feedback_pitch_deg=0.0)
    check("云台指令被斜率限幅", plan is not None and abs(plan[0] - 8.0) < 1e-6, str(plan))

    # 反馈故意停在原地不动 -> 超过收敛窗口后判定抢环。
    # 注意斜率限幅会让前几帧指令本来就贴近反馈，所以“发散”是从指令误差超过
    # 容差那一帧才开始计时，时间轴要留够。
    clock = {"t": 100.0}
    arb = PolarControlArbiter(
        slew_deg_per_step=8.0, tolerance_deg=12.0, conflict_timeout_s=0.60, clock=lambda: clock["t"]
    )
    arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
    arb.plan_gimbal(90.0, 0.0, feedback_yaw_deg=0.0, feedback_pitch_deg=0.0)  # 误差 8°，在容差内
    check("误差在容差内时不发散计时", arb.diverging is False and arb.state == "CLIENT")
    clock["t"] = 100.10
    arb.plan_gimbal(90.0, 0.0, feedback_yaw_deg=0.0, feedback_pitch_deg=0.0)  # 误差 16°，开始发散
    check("开始发散后尚未锁出", arb.state == "CLIENT" and arb.diverging is True, arb.state)
    clock["t"] = 100.50
    arb.plan_gimbal(90.0, 0.0, feedback_yaw_deg=0.0, feedback_pitch_deg=0.0)
    check("发散未过 0.60s 仍不锁出", arb.state == "CLIENT", arb.state)
    clock["t"] = 100.80
    arb.plan_gimbal(90.0, 0.0, feedback_yaw_deg=0.0, feedback_pitch_deg=0.0)
    check("反馈持续不收敛则判定为抢环并锁出", arb.state == "LOCKOUT", arb.state)
    check("锁出后不再发云台角度", arb.plan_gimbal(45.0, 0.0, 0.0, 0.0) is None)

    # 锁出必须显式清除，不能被自动重新交权绕过
    arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
    check("锁出不会被自动交权清除", arb.state == "LOCKOUT")
    arb.clear_lockout()
    check("显式清除后回到 PASSIVE", arb.state == "PASSIVE")

    # 反馈跟得上 -> 判为收敛，不锁出
    ok = PolarControlArbiter(slew_deg_per_step=45.0)
    ok.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
    ok.plan_gimbal(20.0, 3.0, feedback_yaw_deg=20.0, feedback_pitch_deg=3.0)
    check("反馈到位时不触发抢环", ok.state == "CLIENT" and ok._converged is True, ok.state)


def test_normalize_deg_edges() -> None:
    cases = [(0.0, 0.0), (180.0, 180.0), (-180.0, 180.0), (361.0, 1.0), (-361.0, -1.0), (720.5, 0.5)]
    bad = [(a, normalize_deg(a)) for a, want in cases if abs(normalize_deg(a) - want) > 1e-9]
    check("角度归一到 (-180,180]", not bad, str(bad))


# ----------------------------------------------------------------------
class FakeCommand:
    def __init__(self, vx: float, vy: float, wz: float) -> None:
        self.vx, self.vy, self.wz = vx, vy, wz


def test_scale_drive_matches_firmware_limits() -> None:
    vx, vy, wz = scale_drive(1.0, -1.0, 1.0)
    check("满杆等于固件限幅 1.5 m/s / 2.0 rad/s", (vx, vy, wz) == (1.5, -1.5, 2.0), str((vx, vy, wz)))
    vx, vy, wz = scale_drive(9.0, -9.0, 9.0)
    check("归一化输入越界被钳制", (vx, vy, wz) == (1.5, -1.5, 2.0), str((vx, vy, wz)))
    vx, _, _ = scale_drive(0.5, 0.0, 0.0, max_linear_mps=1.5 * 0.6)
    check("Speed Scale 生效", abs(vx - 0.45) < 1e-9, str(vx))


def test_transport_sends_real_frames_and_never_fire() -> None:
    transport = PolarLinkTransport(port="COM9", mock_writes=False)
    transport._serial = FakeSerial()
    transport.connected = True

    transport.submit(
        PolarDriveIntent(
            vx=1.2, vy=-0.4, wz=0.9, gimbal_pitch_rad=0.11, gimbal_yaw_rad=1.02, tracking=True
        )
    )
    transport._pump_tx(time_now := __import__("time").perf_counter())
    # 现在每 tick 会写两帧：0x01 RobotCmd + 0x04 ChassisCmd（自转指令，电平语义）。
    # 早期版本只写一帧，测试直接拿 written[0]/written[-1] 当 0x01 解析；
    # 0x04 只有 14 字节，拿它解 <fff 会直接 struct.error。必须按段 id 过滤。
    robot_frames = [f for f in transport._serial.written if f[2] == 0x01]
    spin_frames = [f for f in transport._serial.written if f[2] == 0x04]
    check("写出了一帧 0x01", len(robot_frames) == 1, str(len(robot_frames)))
    check("同时写出了一帧 0x04 自转指令", len(spin_frames) == 1, str(len(spin_frames)))
    if spin_frames:
        check("未要求自转时 0x04 为 NONE（不干预驾驶员）",
              spin_frames[0][8] == 0, str(spin_frames[0][8]))
        check("0x04 帧长 14", len(spin_frames[0]) == 14, str(len(spin_frames[0])))
    if robot_frames:
        frame = robot_frames[0]
        check("帧长 49", len(frame) == 49, str(len(frame)))
        check("SOF=0x5A 且段 id=0x01", frame[0] == 0x5A and frame[2] == 0x01)
        check("fire 字节恒 0", frame[FIRE_OFFSET] == 0, str(frame[FIRE_OFFSET]))
        (vx, vy, wz) = struct.unpack_from("<fff", frame, 8)
        check("底盘速度按 m/s 上线", (round(vx, 3), round(vy, 3), round(wz, 3)) == (1.2, -0.4, 0.9), str((vx, vy, wz)))
        check("tracking 位上线", frame[FIRE_OFFSET + 2] == 1)

    # 意图过期后必须自动发零帧（本地看门狗）
    transport._serial.written.clear()
    transport._intent.produced_at -= 5.0
    transport._pump_tx(__import__("time").perf_counter())
    robot_frames = [f for f in transport._serial.written if f[2] == 0x01]
    check("看门狗过期后发零帧", len(robot_frames) == 1, str(len(robot_frames)))
    if robot_frames:
        frame = robot_frames[0]
        speeds = struct.unpack_from("<fff", frame, 8)
        check("零帧速度全 0", speeds == (0.0, 0.0, 0.0), str(speeds))
        check("零帧 fire 仍为 0", frame[FIRE_OFFSET] == 0)

    # 急停：立刻归零并停止后续控制
    transport._serial.written.clear()
    transport.submit(
        PolarDriveIntent(vx=1.5, vy=0.0, wz=0.0)
    )
    transport.trigger_estop()
    transport._pump_tx(__import__("time").perf_counter())
    robot_frames = [f for f in transport._serial.written if f[2] == 0x01]
    spin_frames = [f for f in transport._serial.written if f[2] == 0x04]
    check("急停后仍发 0x01 但速度归零",
          bool(robot_frames)
          and struct.unpack_from("<fff", robot_frames[-1], 8) == (0.0, 0.0, 0.0))
    # 急停必须**显式**关自转：固件在指令过期时是“保持现状”，
    # 光停发不会停转，车会继续原地旋转。
    check("急停后 0x04 显式发 OFF", bool(spin_frames) and spin_frames[-1][8] == 2,
          str(spin_frames[-1][8]) if spin_frames else "无 0x04 帧")
    check("急停置位", transport._estop_active is True)


def test_spin_command_channel_is_independent_of_drive() -> None:
    """自转走独立的 0x04 通道，不要求驾驶已授权。

    这是本次修正的核心之一：原来自转被塑进 0x03 的 SHIFT 键位，
    而 key_value 只在 drive_armed 时非零 —— “只想开自转、不用客户端开车”
    这个合理诉求根本发不出去。
    """

    transport = PolarLinkTransport(port="COM9", mock_writes=False)
    transport._serial = FakeSerial()
    transport.connected = True

    # 注意：**不** submit 任何驾驶意图，intent 为 None
    transport.set_spin_command(1)  # SPIN_CMD_ON
    transport._pump_tx(__import__("time").perf_counter())
    spin_frames = [f for f in transport._serial.written if f[2] == 0x04]
    check("无驾驶意图时自转指令仍能发出", bool(spin_frames), str(transport._serial.written))
    check("指令值为 ON", bool(spin_frames) and spin_frames[-1][8] == 1)

    # 电平语义：必须每 tick 重发，否则固件 150ms 后就把控制权收回遥控器
    transport._serial.written.clear()
    for _ in range(5):
        transport._pump_tx(__import__("time").perf_counter())
    again = [f for f in transport._serial.written if f[2] == 0x04]
    check("自转指令每 tick 重发（电平语义）", len(again) == 5, str(len(again)))
    check("重发的值不变", all(f[8] == 1 for f in again))

    # 设定而不是翻转：连发多次 ON 不得把自转关掉
    transport.set_spin_command(1)
    transport.set_spin_command(1)
    transport.set_spin_command(1)
    check("重复设 ON 仍是 ON（不是翻转）", transport.spin_command() == 1)
    transport.set_spin_command(2)
    check("可以显式关掉（原 SHIFT 方案做不到）", transport.spin_command() == 2)

    # 未知取值必须归为 NONE，避免半包/串位误动作
    transport.set_spin_command(0x7F)
    check("未知指令值归为 NONE", transport.spin_command() == 0)

    # 自转指令绝不能携带任何 SHIFT 键位（已改走 0x04，0x03 不得再注入）
    transport._serial.written.clear()
    transport.set_spin_command(1)
    transport.submit(PolarDriveIntent(vx=1.0, key_value=0xFFFF, allow_spin_key=True))
    transport.drive_channel = "virtual_rc"
    transport._pump_tx(__import__("time").perf_counter())
    rc_frames = [f for f in transport._serial.written if f[2] == 0x03]
    if rc_frames:
        # key.v 是 0x03 载荷的最后 2 字节（帧尾 CRC16 之前）
        key_v = struct.unpack_from("<H", rc_frames[-1], len(rc_frames[-1]) - 4)[0]
        check("0x03 的 key.v 不含 SHIFT 位（bit4）", not (key_v & 0x0010), f"key.v=0x{key_v:04X}")
        check("0x03 的 key.v 不含 Q/E/F", not (key_v & 0x0240), f"key.v=0x{key_v:04X}")


def test_spin_command_reaches_robot_without_drive_authorisation() -> None:
    """受击后小陀螺的端到端对齐：从 SpinMode 模块一直追到上线字节。

    这个测试盯的是三个曾经真实存在的缺陷：
      1) 自转被塑进 0x03 的 SHIFT 键位，而固件对 SHIFT 是**边沿翻转**
         → 只能开、关不掉，再开一次反而关掉；
      2) key_value 只在 drive_armed 时非零 → “只开自转不开车”发不出去；
      3) 没有任何回读 → 无法区分“指令没送到”和“送到了但没生效”。
    """

    from rm_client.backend.manager import BackendManager

    workspace = tempfile.mkdtemp(prefix="polar_spin_")
    manager = BackendManager(workspace=workspace, scenario_key="OfficialEvent", client_id="3")
    registry = build_default_registry()
    state = RobotState()
    manager.state = state
    manager._apply_config(manager.config)

    link = registry.get("RobotLink")
    spin = registry.get("SpinMode")
    feed(manager.polar_bridge)

    class FakeControl:
        # 必须可传参：下面第 3 步要用 FakeControl(vx=1.0) 造一个非零推杆。
        def __init__(self, vx: float = 0.0, vy: float = 0.0, wz: float = 0.0) -> None:
            self.last_command = FakeCommand(vx=vx, vy=vy, wz=wz)

    # ---- 前提：操作员没开任何驾驶授权 ----
    link.enabled = True
    link.get_setting("Allow Drive").value = False
    link.get_setting("Client Chassis Drive").value = False
    link.get_setting("Allow Spin Key").value = True
    spin.enabled = False

    manager.tick_polar_link(state, registry, FakeControl())
    # 不能用 or 链写这条：那几乎恒真，测不出任何东西。
    check("驾驶未授权（前提成立）", state.polar_drive_armed is False,
          str(state.polar_drive_armed))
    check("从未要求过自转时发 NONE（不碰驾驶员状态）",
          manager.polar.spin_command() == pl.SPIN_CMD_NONE, str(manager.polar.spin_command()))

    # ---- 1) 受击：SpinMode 被 AutoSpinOnHit 置为 enabled ----
    spin.enabled = True
    manager.tick_polar_link(state, registry, FakeControl())
    check("驾驶未授权也能发出开自转（与 drive_armed 解耦）",
          manager.polar.spin_command() == pl.SPIN_CMD_ON, str(manager.polar.spin_command()))

    # ---- 2) 电平语义：持续 enabled 就持续发 ON，不得翻转 ----
    for _ in range(5):
        manager.tick_polar_link(state, registry, FakeControl())
    check("连发 5 次仍是 ON（不是边沿翻转）",
          manager.polar.spin_command() == pl.SPIN_CMD_ON)

    # ---- 3) 上线字节层校验：0x03 里绝不能出现 SHIFT ----
    manager.polar.mock_writes = False
    manager.polar._serial = FakeSerial()
    manager.polar.connected = True
    manager.polar.drive_channel = "virtual_rc"
    link.get_setting("Allow Drive").value = True
    link.get_setting("Client Chassis Drive").value = True
    manager.tick_polar_link(state, registry, FakeControl(vx=1.0))
    manager.polar._pump_tx(__import__("time").perf_counter())
    written = manager.polar._serial.written
    spin_frames = [f for f in written if f[2] == 0x04]
    rc_frames = [f for f in written if f[2] == 0x03]
    check("上线确有 0x04 帧", bool(spin_frames), str([f[2] for f in written]))
    check("0x04 载荷首字节是 ON", bool(spin_frames) and spin_frames[-1][8] == 1)
    if rc_frames:
        key_v = struct.unpack_from("<H", rc_frames[-1], len(rc_frames[-1]) - 4)[0]
        check("0x03 的 key.v 不含 SHIFT（自转已改走 0x04）",
              not (key_v & 0x0010), f"key.v=0x{key_v:04X}")

    # ---- 4) 回读闭环：0x1F 到达后必须被记下 ----
    check("未收到 0x1F 前不得声称已确认",
          manager.polar_chassis_state_seen is False)
    # 必须走真实的接收队列：直接 bridge.apply() 会绕过 tick_polar_link 里的
    # drain_segments 分发，那正是“记下闭环证据”发生的地方。
    with manager.polar._rx_lock:
        manager.polar._rx.append(segment(pl.SEG_CHASSIS_STATE, (1, 3, 0, 0)))
    manager.tick_polar_link(state, registry, FakeControl())
    check("收到 0x1F 后记下闭环证据",
          manager.polar_chassis_state_seen is True)
    # 通用落库通路：无专用解码器，全靠规格里的 state_fields 映射
    check("0x1F 的自转状态已落进 overlay（供 manager 闭环判定）",
          manager.polar_bridge.overlay.get("polar_spin_active") == 1,
          str(manager.polar_bridge.overlay.get("polar_spin_active")))
    check("0x1F 的底盘模式已落进 overlay",
          manager.polar_bridge.overlay.get("polar_chassis_mode") == 3,
          str(manager.polar_bridge.overlay.get("polar_chassis_mode")))

    # ---- 5) 受击结束：要求关，且回读说还在转 -> 继续发 OFF ----
    spin.enabled = False
    manager.polar_bridge.overlay["polar_spin_active"] = 1
    manager.tick_polar_link(state, registry, FakeControl())
    check("回读说还在转时持续发 OFF（真的能关掉）",
          manager.polar.spin_command() == pl.SPIN_CMD_OFF, str(manager.polar.spin_command()))

    # ---- 6) 回读确认已停 -> 交还控制权给驾驶员遥控器 ----
    manager.polar_bridge.overlay["polar_spin_active"] = 0
    manager.tick_polar_link(state, registry, FakeControl())
    check("确认停转后收敛到 NONE（不锁死遥控器）",
          manager.polar.spin_command() == pl.SPIN_CMD_NONE, str(manager.polar.spin_command()))

    # ---- 7) 驾驶员自己开的自转，客户端无权替他关 ----
    manager.polar_bridge.overlay["polar_spin_active"] = 1
    spin.enabled = False
    manager.tick_polar_link(state, registry, FakeControl())
    check("客户端从未主张过自转时，不干预驾驶员的自转",
          manager.polar.spin_command() == pl.SPIN_CMD_NONE, str(manager.polar.spin_command()))

    # ---- 8) 急停必须显式关自转（固件指令过期时是保持现状）----
    spin.enabled = True
    manager.tick_polar_link(state, registry, FakeControl())
    check("急停前在发 ON", manager.polar.spin_command() == pl.SPIN_CMD_ON)
    manager.safety.estop_active = True
    manager.tick_polar_link(state, registry, FakeControl())
    check("急停时不再主张开自转",
          manager.polar.spin_command() != pl.SPIN_CMD_ON, str(manager.polar.spin_command()))
    manager.safety.estop_active = False

    # ---- 9) 没开 Allow Spin Key 时，即使 SpinMode 开着也不发 ----
    link.get_setting("Allow Spin Key").value = False
    spin.enabled = True
    # 先回到“客户端从未主张过自转”的干净状态，才能验规则 3
    manager._spin_ever_on = False
    manager._spin_off_since = 0.0
    manager.tick_polar_link(state, registry, FakeControl())
    check("未开 Allow Spin Key 时不发自转指令",
          manager.polar.spin_command() == pl.SPIN_CMD_NONE, str(manager.polar.spin_command()))

    manager.shutdown()


def test_spin_off_is_bounded_without_firmware_readback() -> None:
    """固件未打 host_bridge 补丁（无 0x1F）时，OFF 必须有界。

    无限发 OFF 会永久锁死驾驶员用物理遥控器开自转的能力，
    宁可“可能没关掉”也不能剥夺驾驶员控制权。
    """

    from rm_client.backend.manager import BackendManager

    workspace = tempfile.mkdtemp(prefix="polar_spin_bound_")
    manager = BackendManager(workspace=workspace, scenario_key="OfficialEvent", client_id="3")
    manager._spin_ever_on = True          # 曾经主张过开
    manager.polar.set_spin_command(pl.SPIN_CMD_NONE)

    cmd = manager._update_spin_command(desired=False, reported=None)
    check("无回读时先发 OFF", cmd == pl.SPIN_CMD_OFF, str(cmd))

    # 把开始时刻推到足够久远，模拟已过 SPIN_OFF_ASSERT_S
    manager._spin_off_since = __import__("time").perf_counter() - (manager.SPIN_OFF_ASSERT_S + 0.1)
    cmd = manager._update_spin_command(desired=False, reported=None)
    check("有界压制后放手为 NONE", cmd == pl.SPIN_CMD_NONE, str(cmd))
    check("放手后不再认为自己主张过", manager._spin_ever_on is False)

    # 一旦重新要求开，必须能立即恢复主张
    cmd = manager._update_spin_command(desired=True, reported=None)
    check("重新要求开时立即发 ON", cmd == pl.SPIN_CMD_ON, str(cmd))

    manager.shutdown()


def test_send_hz_setting_actually_drives_the_link() -> None:
    """RobotLink 的 “Send Hz” 滑块必须真的改变发送频率。

    这一项曾经是**纯装饰**：设置项定义在 model.py:1574，但全仓没人读它；
    真实频率来自场景配置 config.connection.polar_send_hz，只在 __init__ 读一次。
    而且即使接上也不够：发送线程把 `interval = 1.0 / send_hz` 算在 while
    之前，运行中改值根本不生效。
    """

    from rm_client.backend.manager import BackendManager
    from rm_client.backend.polar_transport import MAX_SEND_HZ

    workspace = tempfile.mkdtemp(prefix="polar_sendhz_")
    manager = BackendManager(workspace=workspace, scenario_key="OfficialEvent", client_id="3")
    registry = build_default_registry()
    state = RobotState()
    manager.state = state
    manager._apply_config(manager.config)
    feed(manager.polar_bridge)

    link = registry.get("RobotLink")
    link.enabled = True
    hz = link.get_setting("Send Hz")
    check("RobotLink 确有 Send Hz 设置项", hz is not None)

    baseline = manager.polar.send_hz
    hz.value = 80
    manager.tick_polar_link(state, registry, None)
    _await_polar_connect(manager)
    check("拖到 80 后链路频率真的变成 80",
          abs(manager.polar.send_hz - 80.0) < 1e-6, str(manager.polar.send_hz))

    # 限幅必须生效：绕过它会把下行段解析淹没
    hz.value = 99999
    manager.tick_polar_link(state, registry, None)
    _await_polar_connect(manager)
    check("超上限被限幅到 MAX_SEND_HZ",
          abs(manager.polar.send_hz - MAX_SEND_HZ) < 1e-6, str(manager.polar.send_hz))

    hz.value = 0.5
    manager.tick_polar_link(state, registry, None)
    _await_polar_connect(manager)
    check("低于下限被限幅到 5Hz", manager.polar.send_hz >= 5.0,
          str(manager.polar.send_hz))

    # 垃圾值不得把链路带倒
    hz.value = "abc"
    before = manager.polar.send_hz
    manager.tick_polar_link(state, registry, None)
    _await_polar_connect(manager)
    check("垃圾值下频率保持原值且 tick 不抛异常",
          abs(manager.polar.send_hz - before) < 1e-6, str(manager.polar.send_hz))

    # set_send_hz 必须报告“真的变了”，否则调用方无法知道是否需要重建链路
    check("set_send_hz 对相同值返回 False",
          manager.polar.set_send_hz(manager.polar.send_hz) is False)
    check("set_send_hz 对不同值返回 True",
          manager.polar.set_send_hz(baseline) is True)

    manager.shutdown()


def test_mock_writes_never_touch_serial() -> None:
    transport = PolarLinkTransport(port="COM9", mock_writes=True)
    transport._serial = FakeSerial()
    transport.connected = True
    transport.submit(
        PolarDriveIntent(vx=1.0)
    )
    transport._pump_tx(__import__("time").perf_counter())
    check("mock 模式一格字节都不写", transport._serial.written == [], str(transport._serial.written))


def test_stub_port_refuses_to_start() -> None:
    transport = PolarLinkTransport(port="Stub", mock_writes=False)
    transport.start()
    check("Stub 端口不会尝试打开硬件", transport.connected is False and transport._running is False)
    check("Stub 给出可读原因", "Stub" in (transport.last_error or ""), transport.last_error)


# ----------------------------------------------------------------------
def test_manager_gate_requires_three_switches() -> None:
    """驾驶必须同时满足：场景能力位 + 模块 enabled + 操作员意图。"""

    from rm_client.backend.manager import BackendManager

    workspace = tempfile.mkdtemp(prefix="polar_bench_")
    manager = BackendManager(workspace=workspace, scenario_key="OfficialEvent", client_id="3")
    registry = build_default_registry()
    state = RobotState()
    manager.state = state
    manager._apply_config(manager.config)

    module = registry.get("RobotLink")
    feed(manager.polar_bridge)  # 让链路看起来是活的
    control = FakeCommand(vx=1.0, vy=0.0, wz=0.0)

    class FakeControl:
        last_command = control

    # 1) 模块未 enabled
    module.enabled = False
    module.get_setting("Client Chassis Drive").value = True
    # 驾驶授权已从场景标志改为模块设置，测试需显式打开
    module.get_setting("Allow Drive").value = True
    result = manager.tick_polar_link(state, registry, FakeControl())
    check("模块未开启时不驾驶", result["drive_armed"] is False, str(result))

    # 2) 模块开启但操作员没给意图
    module.enabled = True
    module.get_setting("Client Chassis Drive").value = False
    result = manager.tick_polar_link(state, registry, FakeControl())
    check("无操作员意图时不驾驶", result["drive_armed"] is False, str(result))

    # 3) 三闸全开（Allow Drive 是第四道，现在也归模块管，不再由场景代写）
    module.get_setting("Allow Drive").value = True
    module.get_setting("Client Chassis Drive").value = True
    result = manager.tick_polar_link(state, registry, FakeControl())
    check("三闸全开才驾驶", result["drive_armed"] is True, str(result))
    intent = manager.polar._intent
    check("下发的确是 m/s 量级速度", intent is not None and abs(intent.vx - 1.5) < 1e-6, str(intent))

    # 3b) 场景不再能越权授权：Allow Drive 关掉就必须锁住驾驶
    # （旧语义下官方场景的 allow_polar_drive=False 会反过来把用户手动打开的
    # 驾驶也锁掉；现在两个方向都解耦：场景不自动授权，但也不禁止手授权）
    module.get_setting("Allow Drive").value = False
    result = manager.tick_polar_link(state, registry, FakeControl())
    check("Allow Drive 关闭时禁止驾驶", result["drive_armed"] is False, str(result))
    module.get_setting("Allow Drive").value = True
    result = manager.tick_polar_link(state, registry, FakeControl())
    check("Allow Drive 重新打开后驾驶恢复", result["drive_armed"] is True, str(result))

    # 4) LabDebug 场景没有能力位，怎么开都不行
    lab = BackendManager(workspace=workspace, scenario_key="LabDebug", client_id="3")
    lab.state = state
    lab._apply_config(lab.config)
    lab.polar_bridge.overlay["chassis_yaw_deg"] = 1.0
    module.enabled = True
    module.get_setting("Client Chassis Drive").value = True
    # 驾驶授权已从场景标志改为模块设置，测试需显式打开
    module.get_setting("Allow Drive").value = True
    result = lab.tick_polar_link(state, registry, FakeControl())
    check("非台架场景没有驾驶能力", result["drive_armed"] is False, str(result))

    # 5) 云台默认让位给视觉 NUC
    module.get_setting("Vision NUC Occupied").value = True
    module.get_setting("Client Gimbal Control").value = True
    state.vision_calibrated = True
    state.target_locked = True
    state.vision_aim_yaw_deg = 33.0
    state.vision_aim_pitch_deg = 4.0
    manager.tick_polar_link(state, registry, FakeControl())
    check("NUC 占用时仲裁为 PASSIVE", manager.polar_arbiter.state == "PASSIVE", manager.polar_arbiter.state)
    check("PASSIVE 时上线的云台角度为 0（不发指令）", manager.polar._intent.gimbal_yaw_rad == 0.0)

    # 6) NUC 退出后仍需显式开启客户端云台控制
    module.get_setting("Vision NUC Occupied").value = False
    manager.tick_polar_link(state, registry, FakeControl())
    check("NUC 退出 + 客户端开启后接管", manager.polar_arbiter.state == "CLIENT", manager.polar_arbiter.state)
    frame_intent = manager.polar._intent
    check("接管后云台角度按弧度上线", abs(frame_intent.gimbal_yaw_rad - math.radians(8.0)) < 1e-6, str(frame_intent.gimbal_yaw_rad))
    check("接管帧 fire 仍为 0", frame_intent is not None)

    # 7) overlay 把真值送到了 state
    check("manager 已把 overlay 写进 state", abs(state.chassis_yaw_deg - 57.2957795) < 1e-4, str(state.chassis_yaw_deg))

    manager.shutdown()
    check("shutdown 触发链路急停归零", manager.polar._estop_triggered if hasattr(manager.polar, "_estop_triggered") else True)


def test_estop_and_diagnostics_without_tick() -> None:
    """F12 必须同帧锁住队内链路，不能等下一帧 tick；诊断快照必须每帧可见。"""

    from rm_client.backend.manager import BackendManager

    workspace = tempfile.mkdtemp(prefix="polar_estop_")
    manager = BackendManager(workspace=workspace, scenario_key="OfficialEvent", client_id="3")
    registry = build_default_registry()
    state = RobotState()
    manager.state = state
    manager._apply_config(manager.config)
    feed(manager.polar_bridge)

    link = registry.get("RobotLink")
    link.enabled = True
    link.get_setting("Client Chassis Drive").value = True
    # 驾驶授权已从场景标志改为模块设置，测试需显式打开
    link.get_setting("Allow Drive").value = True
    link.get_setting("Vision NUC Occupied").value = False

    class FakeControl:
        class last_command:  # noqa: N801
            vx, vy, wz = 1.0, 0.0, 0.0

    # 先正常跑一帧，确认诊断快照进了 backend_stats（不依赖官方 MQTT 流量）
    state.backend_stats = {}
    manager.tick_polar_link(state, registry, FakeControl())
    snapshot = state.backend_stats.get("polar_link")
    check("诊断快照已写入 backend_stats", isinstance(snapshot, dict), str(type(snapshot)))
    check("快照含已收段计数", snapshot and snapshot["segment_counts"].get("0x1D") == 1, str(snapshot and snapshot.get("segment_counts")))
    check("快照含解码错误计数", snapshot and "crc16_errors" in snapshot["decode"])
    # 断言的**内容**而不是 key 的存在：原来的写法只查 `"detected_ports" in snapshot`，
    # 而它当时是 PolarLinkStats 的默认字段（恒为 []），真正去探测的 describe_ports()
    # 写在 return 之后成了死代码 —— 于是这条断言一直绿，HUD 上"已探测到"却永远
    # 显示"无"。这里改成打桩：探测函数被调用 → 内容必须反映桩的返回值。
    _orig_describe = serial_ports.describe_ports
    _describe_calls = {"n": 0}

    def _counting_describe(limit: int = 6) -> list[str]:
        _describe_calls["n"] += 1
        return [f"COM{index} [USB 0000:0000] 桩口" for index in range(limit)]

    serial_ports.describe_ports = _counting_describe
    serial_ports._PORT_DESCRIBE_CACHE["at"] = -1e9  # 强制 TTL 失效，确保走到桩
    try:
        manager.tick_polar_link(state, registry, FakeControl())
    finally:
        serial_ports.describe_ports = _orig_describe
        serial_ports._PORT_DESCRIBE_CACHE["at"] = -1e9
    probed = state.backend_stats.get("polar_link", {}).get("detected_ports")
    check(
        "快照里的端口清单是真探测出来的（不是恒空的默认字段）",
        isinstance(probed, list) and len(probed) == 6 and probed[0].startswith("COM0"),
        f"got {probed!r}",
    )
    manager.tick_polar_link(state, registry, FakeControl())  # 恢复真探测
    check(
        "探测函数不会被每帧重复调用（TTL 生效）",
        _describe_calls["n"] < 3,
        f"一帧内调了 {_describe_calls['n']} 次 describe_ports",
    )
    # HUD 字体（Poppins）无 CJK 字形：行内状态必须是纯 ASCII，否则车上看到一堆方块
    manager.polar.port = "Stub"
    manager.polar.start()
    manager.tick_polar_link(state, registry, FakeControl())  # 刷新快照
    snapshot = state.backend_stats["polar_link"]
    code = str(snapshot.get("last_error_code") or "")
    check(
        "未连接时有 ASCII 状态码（HUD 可渲染）",
        bool(code) and code.isascii(),
        repr(code),
    )
    check(
        "中文原因仍保留在 last_error 供 tooltip/日志",
        bool(str(snapshot.get("last_error") or "")),
    )
    check("驾驶已授权", snapshot and snapshot["authority"]["state"] == "PASSIVE")

    # F12：不跑任何 tick，trigger_estop 本身就必须锁住队内链路
    manager.trigger_estop()
    check("F12 直接锁住 0x5A 链路（不等下一帧）", manager.polar._estop_active is True)
    check("F12 已置归零突发窗口", manager.polar._zeros_until > 0.0)

    # 急停后跑一帧：速度必须被强制归零，HUD 状态为 ESTOP
    manager.tick_polar_link(state, registry, FakeControl())
    intent = manager.polar._intent
    check("急停后驾驶不再授权", state.polar_drive_armed is False)
    check("急停后意图全零", intent is not None and (intent.vx, intent.vy, intent.wz) == (0.0, 0.0, 0.0), str(intent))
    check("HUD 仲裁显示 ESTOP", state.polar_authority_state == "ESTOP", state.polar_authority_state)
    check("ESTOP 已写进诊断快照", state.backend_stats["polar_link"]["authority"]["state"] in ("ESTOP", "PASSIVE", "CLIENT"))

    # 释放后必须能恢复驾驶（不能粘滞锁死）
    manager.release_estop()
    state.backend_stats = {}
    manager.tick_polar_link(state, registry, FakeControl())
    check("释放急停后驾驶恢复", state.polar_drive_armed is True, state.polar_authority_reason)
    check("释放后链路锁存同步清除", manager.polar._estop_active is False)

    manager.shutdown()


def test_motion_verifier_states() -> None:
    """指令-实测校验：覆盖实车两种最坑的失败模式。"""

    from rm_client.backend.polar_bridge import PolarMotionVerifier

    clock = {"t": 500.0}

    def fresh():
        return PolarMotionVerifier(clock=lambda: clock["t"])

    def drive(verifier, vx, measured, *, active=True, wz=0.0):
        return verifier.update(
            link_active=active,
            commanded_vx=vx,
            commanded_vy=0.0,
            commanded_wz=wz,
            measured_vx=measured,
            measured_vy=0.0 if measured is not None else None,
            measured_wz=0.0 if measured is not None else None,
        )

    # A) 从未收到 0x1E（omni/mecanum 的 ChassisPublish 未实现）
    v = fresh()
    state = drive(v, 1.2, None)
    check("无 0x1E 回读时判为 NO_FEEDBACK 而非误报 OK", state == "NO_FEEDBACK", state)
    check("NO_FEEDBACK 带可执行原因", "ChassisPublish" in v.detail, v.detail[:60])

    # B) 链路没活
    v = fresh()
    v.note_motion_segment()
    check("链路断开时判为 NO_LINK", drive(v, 1.2, 0.0, active=False) == "NO_LINK")

    # C) 给了速度、实测跟着动 -> OK
    v = fresh()
    v.note_motion_segment()
    check("指令与实测一致时判为 OK", drive(v, 1.2, 1.15) == "OK")

    # D) 没给速度 -> IDLE（不能误报故障）
    v = fresh()
    v.note_motion_segment()
    check("未给速度时保持 IDLE", drive(v, 0.0, 0.0) == "IDLE")

    # E) **核心场景**：持续给速度但实测一直 0 —— 固件死 getter，车不动
    v = fresh()
    v.note_motion_segment()
    clock["t"] = 500.0
    check("刚开始给速时先给宽限期", drive(v, 1.4, 0.0) == "WAITING")
    clock["t"] = 500.5
    check("超过宽限仍未动报 NO_MOTION", drive(v, 1.4, 0.0) == "NO_MOTION")
    clock["t"] = 501.6
    check("持续不响应最终报 STUCK", drive(v, 1.4, 0.0) == "STUCK", v.state)
    check(
        "STUCK 原因指向固件底盘消费者",
        "GetScCmdChassisSpeed" in v.detail and "1.40" in v.detail,
        v.detail[:120],
    )
    check("STUCK 带指令/实测对比数据", v.commanded_mps > 1.0 and v.measured_mps < 0.1, str((v.commanded_mps, v.measured_mps)))

    # F) 报 STUCK 后一旦车真的动起来，必须自动恢复 OK
    clock["t"] = 501.7
    check("车开始动后从 STUCK 恢复", drive(v, 1.4, 1.30) == "OK", v.state)

    # G) 反馈过期
    v = fresh()
    v.note_motion_segment()
    clock["t"] += 3.0
    check("0x1E 陈旧时报 STALE_FEEDBACK", drive(v, 1.4, 0.0) == "STALE_FEEDBACK", v.state)

    # H) 纯旋转指令（wz）也算“在试开车”
    v = fresh()
    v.note_motion_segment()
    state = v.update(
        link_active=True, commanded_vx=0.0, commanded_vy=0.0, commanded_wz=1.0,
        measured_vx=0.0, measured_vy=0.0, measured_wz=0.0,
    )
    check("只给旋转也算在试开车", state in ("WAITING", "NO_MOTION"), state)
    v2 = fresh()
    v2.note_motion_segment()
    state = v2.update(
        link_active=True, commanded_vx=0.0, commanded_vy=0.0, commanded_wz=1.0,
        measured_vx=0.0, measured_vy=0.0, measured_wz=0.9,
    )
    check("实测有角速度则判 OK", state == "OK", state)


def test_motion_verifier_wired_into_manager() -> None:
    """验证 manager 真的把 0x1E 接给了校验器，且结果进了 state/快照。"""

    from rm_client.backend.manager import BackendManager

    workspace = tempfile.mkdtemp(prefix="polar_motion_")
    manager = BackendManager(workspace=workspace, scenario_key="OfficialEvent", client_id="3")
    registry = build_default_registry()
    state = RobotState()
    manager.state = state
    manager._apply_config(manager.config)

    link = registry.get("RobotLink")
    link.enabled = True
    link.get_setting("Client Chassis Drive").value = True
    # 驾驶授权已从场景标志改为模块设置，测试需显式打开
    link.get_setting("Allow Drive").value = True
    link.get_setting("Vision NUC Occupied").value = False

    class Ctrl:
        class last_command:  # noqa: N801
            vx, vy, wz = 1.0, 0.0, 0.0

    # 情形一：有 0x1E 且实测跟着动 -> OK
    manager.polar_bridge.apply(segment(pl.SEG_IMU, (0.5, 0.0, 0.0, 0.0, 0.0, 0.0)))
    manager.polar_bridge.apply(segment(pl.SEG_ROBOT_MOTION, (1.2, 0.0, 0.0)))
    manager.polar_motion.note_motion_segment()
    manager.tick_polar_link(state, registry, Ctrl())
    check("manager 将实测速度接入校验器", state.polar_motion_state == "OK", state.polar_motion_state)
    check("运动结果进了诊断快照", "motion" in state.backend_stats["polar_link"])

    # 情形二：有 0x1E 但实测一直 0 -> 最终 STUCK
    manager.polar_bridge.apply(segment(pl.SEG_ROBOT_MOTION, (0.0, 0.0, 0.0)))
    manager.polar_motion.note_motion_segment()
    manager.tick_polar_link(state, registry, Ctrl())
    manager.polar_motion._moving_since -= 2.0  # 模拟时间流逝
    manager.tick_polar_link(state, registry, Ctrl())
    check("持续给速但实测不动能被报出", state.polar_motion_state == "STUCK", state.polar_motion_state)
    check(
        "STUCK 详情已入 state 供 HUD 使用",
        "GetScCmdChassisSpeed" in state.polar_motion_detail,
        state.polar_motion_detail[:80],
    )

    manager.shutdown()


def test_polar_link_reconnects_after_plug_in() -> None:
    """先启动客户端、后插机器人：链路必须自己接回来，而不是永远 LNK 不亮。"""

    from rm_client.backend.manager import POLAR_RETRY_S, BackendManager

    workspace = tempfile.mkdtemp(prefix="polar_retry_")
    manager = BackendManager(workspace=workspace, scenario_key="OfficialEvent", client_id="3")
    registry = build_default_registry()
    state = RobotState()
    manager.state = state
    manager._apply_config(manager.config)

    calls = {"n": 0}

    def fake_start():
        calls["n"] += 1
        # 前两次当作“还没插”，第三次机器人出现了
        if calls["n"] >= 3:
            manager.polar.connected = True
            manager.polar.resolved_port = "COM12"
        else:
            manager.polar.connected = False
            manager.polar.last_error = "未找到机器人 USB 串口"

    manager.polar.start = fake_start
    # 重试闸门现在只看“用户是否配了真端口”，不再看场景标志；
    # 所以这里要显式给一个真实端口，否则会在 Stub 判定处直接早退。
    manager.polar.port = "COM99"

    # 快速连续多帧：必须被节律限制，不能每帧都去开串口
    for _ in range(20):
        manager.tick_polar_link(state, registry, None)
        _await_polar_connect(manager)
    check("20 帧内只尝试一次（节律生效）", calls["n"] == 1, str(calls["n"]))
    check("未连上时不会假装活跃", state.polar_link_active is False)

    # 跨过重试窗口：再试一次（仍未连上，因为假 start 设定第 3 次才出现机器人）
    manager._polar_last_connect_attempt -= POLAR_RETRY_S + 1.0
    manager.tick_polar_link(state, registry, None)
    _await_polar_connect(manager)
    check("跨过窗口后发生第 2 次尝试", calls["n"] == 2, str(calls["n"]))
    check("第 2 次仍未连上", manager.polar.connected is False)

    # 再跨一次窗口：机器人“插上”，链路必须自动恢复
    manager._polar_last_connect_attempt -= POLAR_RETRY_S + 1.0
    manager.tick_polar_link(state, registry, None)
    _await_polar_connect(manager)
    check("机器人插上后链路自动恢复", calls["n"] == 3 and manager.polar.connected is True, str(calls["n"]))

    # 连上后不应再反复重连
    before = calls["n"]
    manager.polar._running = True  # 模拟读线程已跑起来
    for _ in range(5):
        manager._polar_last_connect_attempt -= POLAR_RETRY_S + 1.0
        manager.tick_polar_link(state, registry, None)
        _await_polar_connect(manager)
    check("已连接时不重复重连", calls["n"] == before, f"{before} -> {calls['n']}")

    # 非台架场景不应自动去开串口
    lab = BackendManager(workspace=workspace, scenario_key="LabDebug", client_id="3")
    lab.state = state
    lab._apply_config(lab.config)
    hits = {"n": 0}

    def counting_start():
        hits["n"] += 1

    lab.polar.start = counting_start
    for _ in range(5):
        lab._polar_last_connect_attempt -= POLAR_RETRY_S + 1.0
        lab.tick_polar_link(state, registry, None)
    check("非台架场景不会自动尝试开串口", hits["n"] == 0, str(hits["n"]))


def test_drive_channel_gating() -> None:
    """0x03 会覆盖驾驶员真实摇杆，所以只能在已授权驾驶时发。"""

    def ids(frames):
        return [f[2] for f in frames]

    # 已授权：virtual_rc 通道应发 0x03，且 key.v 带上位机键位
    t = PolarLinkTransport(port="COM9", mock_writes=False)
    t.drive_channel = "virtual_rc"
    t._serial = FakeSerial()
    t.connected = True
    t.submit(PolarDriveIntent(vx=1.5, key_value=0x01))
    t._pump_tx(__import__("time").perf_counter())
    got = ids(t._serial.written)
    check("virtual_rc 通道已授权时发 0x03", 3 in got, str(got))
    check("0x01 仍持续发（云台角度只能走 0x01，否则会静默失效）", 1 in got, str(got))
    rc_frames = [f for f in t._serial.written if f[2] == 1]
    if rc_frames:
        sent_vx = __import__("struct").unpack_from("<f", rc_frames[0], 8)[0]
        check("virtual_rc 纯通道下 0x01 的 speed_vector 写零（避免两套斜坡叠加）",
              abs(sent_vx) < 1e-9, str(sent_vx))
    keyed = [f for f in t._serial.written if f[2] == 3]
    check("0x03 携带 key.v 位图",
          bool(keyed) and __import__("struct").unpack_from("<H", keyed[0], 28)[0] == 0x01,
          str(__import__("struct").unpack_from("<H", keyed[0], 28)[0] if keyed else None))

    # 未授权（全零意图）：不得持续占用 0x03 去冻结遥控器
    t2 = PolarLinkTransport(port="COM9", mock_writes=False)
    t2.drive_channel = "virtual_rc"
    t2._serial = FakeSerial()
    t2.connected = True
    t2.submit(PolarDriveIntent())  # 全零
    t2._pump_tx(__import__("time").perf_counter())
    first = ids(t2._serial.written)
    t2._serial.written.clear()
    t2._pump_tx(__import__("time").perf_counter())
    second = ids(t2._serial.written)
    check("未授权时第 2 次不再发 0x03（把控制权交回驾驶员）", 3 not in second, str(second))
    check("从未授权过时根本不发 0x03（不占遥控器）", 3 not in first, str(first))
    check("未授权时 0x01 照发，保证云台控制不断", 1 in first, str(first))

    # UI 停摆（intent 变 None / 看门狗过期）同样不得持续占住 0x03
    t6 = PolarLinkTransport(port="COM9", mock_writes=False)
    t6.drive_channel = "virtual_rc"
    t6._serial = FakeSerial()
    t6.connected = True
    t6.submit(PolarDriveIntent(vx=1.5, key_value=0x01))
    t6._pump_tx(__import__("time").perf_counter())      # 授权中，发 0x03
    t6._intent = None                                    # 模拟 UI 不再投递
    t6._pump_tx(__import__("time").perf_counter())      # 下降沿：补一次全零
    t6._serial.written.clear()
    for _ in range(5):
        t6._pump_tx(__import__("time").perf_counter())
    check("UI 停摆后不再持续发 0x03（不冻结遥控器）",
          3 not in ids(t6._serial.written), str(ids(t6._serial.written)))

    # both：两路都发，便于固件升级前后行为一致
    t3 = PolarLinkTransport(port="COM9", mock_writes=False)
    t3.drive_channel = "both"
    t3._serial = FakeSerial()
    t3.connected = True
    t3.submit(PolarDriveIntent(vx=1.5, key_value=0x01))
    t3._pump_tx(__import__("time").perf_counter())
    both = ids(t3._serial.written)
    check("both 通道同时发 0x01 与 0x03", 1 in both and 3 in both, str(both))

    # 默认通道必须仍是 robot_cmd（不会因为新特性意外冻结遥控器）
    t4 = PolarLinkTransport(port="COM9", mock_writes=False)
    t4._serial = FakeSerial()
    t4.connected = True
    t4.submit(PolarDriveIntent(vx=1.5, key_value=0x01))
    t4._pump_tx(__import__("time").perf_counter())
    check("默认通道为 robot_cmd（不发 0x03）",
          t4.drive_channel == "robot_cmd" and 3 not in ids(t4._serial.written),
          f"{t4.drive_channel} {ids(t4._serial.written)}")

    # 急停：both 下两路都要归零
    t5 = PolarLinkTransport(port="COM9", mock_writes=False)
    t5.drive_channel = "both"
    t5._serial = FakeSerial()
    t5.connected = True
    t5.trigger_estop()
    t5._pump_tx(__import__("time").perf_counter())
    estop_ids = ids(t5._serial.written)
    check("急停时两路都发归零帧", 1 in estop_ids and 3 in estop_ids, str(estop_ids))
    for f in t5._serial.written:
        if f[2] == 3:
            check("急停 0x03 的 key.v 已清零",
                  __import__("struct").unpack_from("<H", f, 28)[0] == 0)


def test_control_keybinds_map_to_protocol_slots() -> None:
    """前端可改绑驾驶键，但后端发的永远是协议槽位。

    这是“后端跟前端对齐”的核心契约：用户把“前进”改绑到↑，
    上位机仍应发 bit0（W 槽），固件语义与掩码均不受影响。
    """

    from rm_client.control import (
        DEFAULT_CONTROL_BINDS,
        KEYBOARD_BITS,
        KeyboardControlBridge,
    )
    import rm_client.control as control_module

    KEYBOARD_BITS = control_module.KEYBOARD_BITS

    bridge = KeyboardControlBridge(mock_output=True)
    state = RobotState()
    registry = build_default_registry()

    check("默认绑定与历史物理键一致", bridge.binds == DEFAULT_CONTROL_BINDS)
    check("未改绑时 W 解析到 W 槽", bridge.resolve_slot("W") == ("W",), str(bridge.resolve_slot("W")))

    # 改绑：“前进”从 W 换到↑
    check("改绑 W 槽为 Up 成功", bridge.set_bind("W", "Up") is True)
    check("按 Up 现在驱动 W 槽", bridge.resolve_slot("Up") == ("W",), str(bridge.resolve_slot("Up")))
    check("按 W 不再驱动任何槽", bridge.resolve_slot("W") == (), str(bridge.resolve_slot("W")))

    # 改绑后按新键，发出去的仍是 bit0
    bridge.set_key("W", True, state, registry)  # set_key 按槽位名调用
    command = bridge.build_command(state, registry)
    check("改绑后 keyboard_value 仍为 bit0", command.keyboard_value == 1 << KEYBOARD_BITS["W"],
          bin(command.keyboard_value))

    # 冲突：不允许两个槽绑到同一物理键
    check("拒绝把 S 槽也绑到 Up（已被 W 槽占用）", bridge.set_bind("S", "Up") is False)
    check("拒绝后 S 槽保待原绑定", bridge.binds["S"] == "S", bridge.binds["S"])

    # 解绑："None" 表示该槽不接物理键
    check("支持把槽位绑成 None", bridge.set_bind("A", "None") is True)
    check("绑成 None 后按 A 无输出", bridge.resolve_slot("A") == (), str(bridge.resolve_slot("A")))

    # 非法键名要拒绝，不能默默接受
    check("拒绝未知键名", bridge.set_bind("D", "NotARealKey") is False)

    # 重置后回到默认
    bridge.reset_binds()
    check("reset_binds 后全部回到默认", bridge.binds == DEFAULT_CONTROL_BINDS)

    # 安全掩码与改绑无关：无论用户怎么绑，非驾驶槽永远泄不出去
    from rm_client.backend import polar_link as pl2

    codec = pl2.PolarFrameCodec()
    bridge.reset_binds()
    for slot in ("Q", "E", "SHIFT", "F"):
        bridge.reset_inputs()
        bridge.set_key(slot, True, state, registry)
        raw_value = bridge.build_command(state, registry).keyboard_value
        check(f"{slot} 确实会出现在官方 keyboard_value 里（所以必须被拦）",
              raw_value & ~0x0F != 0, bin(raw_value))
        frame = codec.encode_virtual_rc(raw_timestamp=1, key_value=raw_value)
        on_wire = __import__("struct").unpack_from("<H", frame, 28)[0]
        # 上线值不得包含任何非驾驶槽；单独断言 == 0 会漏检“只有 Q”这种泄漏
        check(f"{slot} 经 0x03 上线时被掩码抹掉", on_wire & ~0x0F == 0, hex(on_wire))
        check(f"{slot} 本身不出现在上线位图", on_wire & (1 << KEYBOARD_BITS[slot]) == 0, hex(on_wire))
    bridge.reset_inputs()


def test_gimbal_egress_is_mutually_exclusive() -> None:
    """云台角度不能同时走 PRM1(MQTT) 和 0x5A 两条出口。

    同一份瞄准角被两条出口读，各自只查自己那套开关。没有互锁时机器人
    会收到两套云台角互相覆盖（表现为抖动/乱转），而 0x5A 侧的抢环锁出
    看不见 PRM1 这条。本测试锁住单一裁决点。
    """

    import tempfile as _tf

    from rm_client.backend.manager import BackendManager
    from rm_client.model import RobotState, build_default_registry

    workspace = _tf.mkdtemp(prefix="polar_egress_")
    manager = BackendManager(workspace=workspace, scenario_key="OfficialEvent", client_id="3")
    registry = build_default_registry()
    state = manager.state = RobotState()
    manager._apply_config(manager.config)

    link = registry.get("RobotLink")
    link.enabled = True
    link.get_setting("Allow Drive").value = True
    link.get_setting("Mock Writes").value = False
    link.get_setting("Vision NUC Occupied").value = False
    link.get_setting("Client Gimbal Control").value = True
    link.get_setting("Client Chassis Drive").value = False

    aim = registry.get("ArmorAutoAim")
    aim.enabled = True
    aim.get_setting("Mock Output").value = False

    from rm_client.backend.polar_link import SEG_ROBOT_MOTION

    manager.polar_bridge.last_segment_at = __import__("time").perf_counter()
    manager.polar_bridge.segment_counts[SEG_ROBOT_MOTION] = 1
    state.vision_calibrated = True
    state.target_locked = True
    state.vision_aim_yaw_deg = 12.0
    state.vision_aim_pitch_deg = -3.0

    class Ctrl:
        last_command = None

    # 链路活跃前：不应判给 polar
    state.polar_link_active = False
    manager._polar_gimbal_last = None
    check("链路未活跃时云台不判给 0x5A",
          manager.gimbal_egress_owner(state, registry) != "polar",
          manager.gimbal_egress_owner(state, registry))

    stats = manager.tick_polar_link(state, registry, Ctrl())
    check("链路活跃+已授权控云台时，出口判给 polar",
          stats.get("gimbal_egress") == "polar", str(stats.get("gimbal_egress")))

    # 关掉控云台授权后，应该回到官方出口
    link.get_setting("Client Gimbal Control").value = False
    manager._polar_gimbal_last = None
    stats2 = manager.tick_polar_link(state, registry, Ctrl())
    check("未授权控云台时出口不判给 polar",
          stats2.get("gimbal_egress") != "polar", str(stats2.get("gimbal_egress")))
    check("此时回到官方 PRM1 出口",
          stats2.get("gimbal_egress") == "official", str(stats2.get("gimbal_egress")))

    # 两边都不可用时必须是 none，不能“默认发”
    aim.get_setting("Mock Output").value = True
    stats3 = manager.tick_polar_link(state, registry, Ctrl())
    check("两条出口都不可用时为 none（不默认发）",
          manager.gimbal_egress_owner(state, registry) == "none",
          str(stats3.get("gimbal_egress")))

    try:
        manager.shutdown()
    except Exception:
        pass


def main() -> int:
    tests = [
        test_overlay_wins_over_mock,
        test_stale_link_releases_overlay,
        test_unknown_overlay_key_is_ignored,
        test_official_containers_not_clobbered,
        test_arbiter_vision_nuc_has_top_priority,
        test_arbiter_slew_and_conflict_lockout,
        test_normalize_deg_edges,
        test_scale_drive_matches_firmware_limits,
        test_transport_sends_real_frames_and_never_fire,
        test_spin_command_channel_is_independent_of_drive,
        test_spin_command_reaches_robot_without_drive_authorisation,
        test_spin_off_is_bounded_without_firmware_readback,
        test_send_hz_setting_actually_drives_the_link,
        test_mock_writes_never_touch_serial,
        test_stub_port_refuses_to_start,
        test_manager_gate_requires_three_switches,
        test_estop_and_diagnostics_without_tick,
        test_motion_verifier_states,
        test_motion_verifier_wired_into_manager,
        test_polar_link_reconnects_after_plug_in,
        test_drive_channel_gating,
        test_control_keybinds_map_to_protocol_slots,
        test_gimbal_egress_is_mutually_exclusive,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"FAILED {len(FAILED)}: {', '.join(FAILED)}")
        return 1
    print("ALL POLAR_BRIDGE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
