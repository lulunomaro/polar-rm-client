"""补上不变量 5、6、10 里三个**实测为零覆盖**的子句。

# 这三个洞是怎么找到的

一个子代理对 AGENT.md 第 4 节十条不变量做了**变异验证**：
逐条把执行点改坏一行，看点名测试会不会红。结果十条里七条完全守住，
另外三条各有一句是空的 —— 每一条的证据都是"**14 个 suite 全绿**"。

本文件补的就是那三句。写法统一：**先在正确实现上确认事实**，
再把事实写成断言（而不是照着源码抄一遍 —— 那只会得到一个恒绿的测试）。

| 不变量 | 空的子句 | 后果 |
|---|---|---|
| 5 断流要归零 | `PolarLinkTransport.stop()` 不再发零帧 | 见下 |
| 6 0x03 松杆补零 | 下降沿那一次全零帧 | 固件里残留最后一次摇杆量，驾驶员真实遥控器被冻住 |
| 10 s[0] 拨杆 | 客户端的诊断提示文案 | 提示被改掉，没有任何测试有感 |

## #5 为什么不是冗余

`manager.shutdown()` 是"先 `trigger_estop()` 再 `polar.stop()`"，有急停兜底。
但**换串口 / 换波特率 / 切 Link Mode** 三条路径（`manager.py` 里三处）
**只调 `polar.stop()`，没有任何其它归零**。

而 AGENT.md 不变量 5 写的是"`stop()` / 急停 / deadman 超时都要**先发零帧再关口**"，
且明确"固件侧没有断流归零"。所以：**车上带着速度时，操作员在界面上换一下串口，
固件会把最后那条指令永久执行下去** —— 而且客户端这边连一行日志都没有，
因为 `stop()` 自己觉得"我归零了"。

## #6 现有测试为什么漏了

`test_drive_channel_gating` 断言的是"松杆之后 5 帧里没有 0x03"。
但正确行为是"松杆之后**恰好一帧**全零 0x03，之后彻底不再发"。
现有断言对"一次都不补"和"补一次"**同样通过** —— 这正是它漏掉这一句的原因。

## #10 为什么只测文案

不变量 10 描述的是**固件行为**（`chassis_mecanum.c:82-105`），
客户端侧没有对应的执行点，客户端唯一的产物就是 STUCK / NO_MOTION
诊断里那句"首查 s[0] 拨杆"。

所以客户端侧能守的就是**这句提示还在不在**。这听起来很弱，但提示被改掉之后
现场就少了一条排查线索，而它是**唯一**那条。把弱断言明写出来，
好过假装客户端能验证固件行为。

跑法：``python tests/zero_frame_and_diagnostics_test.py``
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.backend import polar_link as pl  # noqa: E402
from rm_client.backend.polar_transport import PolarDriveIntent, PolarLinkTransport  # noqa: E402

FAILURES: list[str] = []
PASSED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"[PASS] {label}")
    else:
        FAILURES.append(f"{label} :: {detail}")
        print(f"[FAIL] {label} {detail}")


class FakeSerial:
    """记录写出去每一个字节的替身串口。"""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, payload: bytes) -> int:
        self.writes.append(bytes(payload))
        return len(payload)

    def close(self) -> None:
        self.closed = True


def _frames(writes: list[bytes]) -> list[tuple[int, bytes]]:
    """把字节流切成 (段号, body)。

    帧格式：SOF(1) seg_len(1) seg_id(1) crc8(1) timestamp(4) body crc16(2)
    body 长度 = seg_len - 4 - 2。
    """
    out: list[tuple[int, bytes]] = []
    pos = 0
    while pos + 10 <= len(writes):
        if writes[pos] != pl.POLAR_SOF:
            pos += 1
            continue
        seg_len = writes[pos + 1]
        seg_id = writes[pos + 2]
        body_len = seg_len - pl.TIMESTAMP_SIZE
        if body_len < 0 or pos + pl.HEADER_SIZE + pl.TIMESTAMP_SIZE + body_len + pl.CRC16_SIZE > len(writes):
            pos += 1
            continue
        body_start = pos + pl.HEADER_SIZE + pl.TIMESTAMP_SIZE
        body = bytes(writes[body_start: body_start + body_len])
        out.append((seg_id, body))
        pos += pl.HEADER_SIZE + pl.TIMESTAMP_SIZE + body_len + pl.CRC16_SIZE
    return out


# ===========================================================================
# 1. 不变量 5：stop() 必须先发零帧再关口
# ===========================================================================

def test_stop_flushes_zero_before_disconnect() -> None:
    """带着非零意图调 `stop()`，必须**先**写出零帧。

    这条对应 AGENT.md 不变量 5 的第一句：固件不会自己断流归零。
    """
    serial = FakeSerial()
    transport = PolarLinkTransport(mock_writes=False)
    # 跟 tests/polar_bridge_test.py 一致的替法：直接换掉 _serial。
    # 构造函数只接 port/baud/mock_writes/safety/send_hz，串口是连上时才建的。
    transport._serial = serial
    transport._running = True                       # 不起真线程，只让 stop() 以为自己该归零
    transport.connected = True
    transport.set_spin_command(pl.SPIN_CMD_ON)      # 让 0x04 也带上"曾主张开"

    now = time.perf_counter()
    transport.submit(PolarDriveIntent(
        produced_at=now, vx=1.2, vy=-0.4, wz=0.9,
        gimbal_pitch_rad=0.0, gimbal_yaw_rad=0.0,
        friction_on=False, tracking=False, key_value=0x0001,
        allow_spin_key=False,
    ))
    transport._pump_tx(now)                         # 先正常发一帧非零
    transport._stop.clear()
    written_before_stop = len(serial.writes)
    check("pump 之后确实写出了东西（否则下面验不到'停前'）",
          written_before_stop > 0, f"{written_before_stop} 帧")

    transport.stop()

    after = serial.writes[written_before_stop:]
    frames = _frames(bytes(b"".join(after)))
    check("stop() 之后确实写出了帧（要归零就得有帧）", bool(frames), f"{len(frames)} 帧")

    zero_robot = [b for sid, b in frames if sid == pl.CMD_ROBOT]
    check("stop() 写出了至少一帧 0x01 RobotCmd", bool(zero_robot),
          f"段号集合 {sorted({sid for sid, _ in frames})}")
    if zero_robot:
        body = zero_robot[0]
        vx, vy, wz = float(pl._ROBOT_CMD_BODY.unpack(body)[0]), \
            float(pl._ROBOT_CMD_BODY.unpack(body)[1]), \
            float(pl._ROBOT_CMD_BODY.unpack(body)[2])
        check(f"零帧的速度三元组全 0（实际 {vx}, {vy}, {wz}）",
              vx == 0.0 and vy == 0.0 and wz == 0.0, f"{vx}, {vy}, {wz}")

    zero_chassis = [b for sid, b in frames if sid == pl.CMD_CHASSIS_CMD]
    if zero_chassis:
        check("0x04 首字节是 SPIN_CMD_OFF（自转也要交还）",
              zero_chassis[0][0] == pl.SPIN_CMD_OFF,
              f"实际 {zero_chassis[0][0]:#04x}，期望 {pl.SPIN_CMD_OFF:#04x}")

    check("stop() 之后串口被关掉", serial.closed, "没关")


def test_manager_reconfigure_paths_go_through_stop() -> None:
    """换口 / 换波特率 / 切 Link Mode 三条路径**只调 stop()**，没有别的归零。

    所以 `stop()` 的归零不是冗余 —— 这条把"为什么它必须存在"钉下来：
    如果哪天有人给这三条路径加上急停兜底，这条断言会红，
    提醒他"现在有两条归零路了，别再改 stop()"。
    """
    from rm_client.backend import manager as manager_mod

    import inspect
    src = inspect.getsource(manager_mod.BackendManager.shutdown)
    reconfigure = reconfigure_count = 0
    whole = Path(manager_mod.__file__).read_text(encoding="utf-8")
    # 只数 tick 路径里的那几处（shutdown 里那处要单独看顺序）
    tick_src = inspect.getsource(manager_mod.BackendManager.tick_polar_link)
    reconfigure = tick_src.count("self.polar.stop()")
    check(f"tick_polar_link 里有 {reconfigure} 处 polar.stop()（换口/换波特率/切 Link Mode）",
          reconfigure >= 2, f"只找到 {reconfigure} 处 —— 那些路径现在有别的方式归零了吗？")
    check("整份 manager 里 polar.stop() 只在这几处与 shutdown 里出现",
          whole.count("self.polar.stop()") == reconfigure + 1,
          f"全文 {whole.count('self.polar.stop()')} 处 vs tick {reconfigure} 处 + shutdown 1 处")

    # shutdown 内部的顺序：必须先急停再 stop。
    # 只在 **shutdown 函数体**里找 —— 整份文件里 `trigger_estop` 的第一次出现
    # 在 tick_polar_link（:565），拿全文 index 去比会得出"顺序反了"的假结论。
    estop_at = src.index("self.polar.trigger_estop()")
    stop_at = src.index("self.polar.stop()")
    check("shutdown 里是先急停再 stop（急停能赶在关口前把线上的非零帧顶掉）",
          estop_at < stop_at, f"estop@{estop_at} stop@{stop_at}")


# ===========================================================================
# 2. 不变量 6：松杆后恰好补一次全零 0x03
# ===========================================================================

def test_falling_edge_sends_exactly_one_zero_virtual_rc() -> None:
    """已授权 → 松杆：**恰好一帧**全零 0x03，之后彻底不再占用该通道。

    现有 `test_drive_channel_gating` 断言的是"之后 5 帧没有 0x03"，
    对"一次都不补"和"补一次"同样通过 —— 这一条补上"恰好一次"，
    并且**真的去解那一帧**，断言摇杆量与键位全为 0。
    """
    serial = FakeSerial()
    transport = PolarLinkTransport(mock_writes=False)
    transport._serial = serial
    transport._running = True
    transport.connected = True
    transport.drive_channel = "both"
    now = time.perf_counter()

    # --- 阶段一：已授权，带上非零驾驶意图 ---
    transport._intent = PolarDriveIntent(
        produced_at=now, vx=1.5, vy=0.0, wz=0.4,
        gimbal_pitch_rad=0.0, gimbal_yaw_rad=0.0,
        friction_on=False, tracking=False, key_value=0x0001,
        allow_spin_key=False,
    )
    transport._drive_was_armed = True
    transport._pump_tx(now)
    serial.writes.clear()

    # --- 阶段二：松杆（intent 置空 + 未授权） ---
    transport._intent = None
    transport._pump_tx(now + 0.05)
    frames = _frames(bytes(b"".join(serial.writes)))
    rc_frames = [b for sid, b in frames if sid == pl.CMD_VIRTUAL_RC]
    check("松杆那一拍写出**恰好一帧** 0x03", len(rc_frames) == 1, f"{len(rc_frames)} 帧")

    if rc_frames:
        import struct as _struct
        values = list(_struct.unpack(pl.RC_CTRL_BODY_FORMAT, rc_frames[0]))
        key_value = values[-1]
        channels = values[0:5]
        check(f"那一帧的键位全 0（实际 0x{key_value:04X}）", key_value == 0,
              f"0x{key_value:04X}")
        check(f"那一帧的 5 个通道全 0（实际 {channels}）",
              all(int(c) == 0 for c in channels), str(channels))

    # --- 阶段三：再来两拍，通道必须彻底安静 ---
    serial.writes.clear()
    transport._pump_tx(now + 0.10)
    transport._pump_tx(now + 0.15)
    later = _frames(bytes(b"".join(serial.writes)))
    later_rc = [sid for sid, _ in later if sid == pl.CMD_VIRTUAL_RC]
    check("补过一次之后彻底不再发 0x03（不占驾驶员通道）", not later_rc,
          f"又发了 {len(later_rc)} 帧")


def test_no_falling_edge_zero_when_never_armed() -> None:
    """从未授权过 → 松杆**不应该**有那一帧。

    没有上升沿就没有下降沿。不授权时不占通道是这条不变量的另一半，
    只测"授权过会补"会漏掉"没授权也补"这种退化。
    """
    serial = FakeSerial()
    transport = PolarLinkTransport(mock_writes=False)
    transport._serial = serial
    transport._running = True
    transport.connected = True
    transport.drive_channel = "both"
    now = time.perf_counter()

    transport._intent = None
    transport._pump_tx(now)
    transport._pump_tx(now + 0.05)
    frames = _frames(bytes(b"".join(serial.writes)))
    rc_frames = [sid for sid, _ in frames if sid == pl.CMD_VIRTUAL_RC]
    check("从未授权过时一次 0x03 都不该有", not rc_frames, f"{len(rc_frames)} 帧")


# ===========================================================================
# 3. 不变量 10：客户端侧的 s[0] 诊断提示
# ===========================================================================

def test_stuck_diagnostics_point_at_the_stick_first() -> None:
    """STUCK / NO_MOTION 的诊断必须**先**指向 s[0] 拨杆。

    不变量 10 描述的是固件行为（`chassis_mecanum.c:82-105`），
    客户端没有执行点 —— 客户端唯一的产物就是这两句提示。
    把提示去掉，现场就少了一条最容易验证的排查线索，
    而它是**唯一**那条能让人 10 秒排除一个常见误配置的。

    这条断言很弱，但它守的是"弱的那部分真的还在"。
    """
    from rm_client.backend.polar_bridge import PolarMotionVerifier

    # 造一个必然判 STUCK 的序列：持续给速度、实测不动、且走过 1.2s
    clock = _Clock()
    verifier = PolarMotionVerifier(clock=lambda: clock.now())
    verifier.note_motion_segment()
    stuck_detail = ""
    for _ in range(5):
        clock.advance(0.4)   # 0.4*5 = 2.0 正好卡在 FEEDBACK_SEEN_TIMEOUT 边界上；
                                 # 用 0.6 会先撞 STALE_FEEDBACK 而不是 STUCK
        state = verifier.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
                                commanded_wz=0.0, measured_vx=0.0, measured_vy=0.0,
                                measured_wz=0.0)
        stuck_detail = verifier.detail
    check("造出了 STUCK 态（否则下面验的是别的文案）", state == "STUCK", state)
    check("STUCK 诊断里提到了 s[0] 拨杆",
          "s[0]" in stuck_detail, f"实际文案：{stuck_detail[:90]}")
    check("STUCK 诊断把它列为**首查**（不是顺带一提）",
          "首查" in stuck_detail, f"实际文案：{stuck_detail[:90]}")

    # NO_MOTION 那条
    clock2 = _Clock()
    v2 = PolarMotionVerifier(clock=lambda: clock2.now())
    v2.note_motion_segment()
    # `_moving_since` 是**第一次 update 时**才起算的，所以第一拍必然是 WAITING，
    # 第二拍（elapsed > GRACE_S）才可能是 NO_MOTION。只推一次时钟是不够的。
    clock2.advance(0.4)
    first = v2.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
                      commanded_wz=0.0, measured_vx=0.0, measured_vy=0.0,
                      measured_wz=0.0)
    check("第一拍是 WAITING（计时从这一拍起算）", first == "WAITING", first)
    clock2.advance(0.4)
    state2 = v2.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
                       commanded_wz=0.0, measured_vx=0.0, measured_vy=0.0,
                       measured_wz=0.0)
    check("造出了 NO_MOTION 态", state2 == "NO_MOTION", state2)
    check("NO_MOTION 诊断里也提到了 s[0] 拨杆",
          "s[0]" in v2.detail, f"实际文案：{v2.detail[:90]}")


class _Clock:
    def __init__(self) -> None:
        self._t = 1000.0

    def now(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


def main() -> int:
    tests = [
        test_stop_flushes_zero_before_disconnect,
        test_manager_reconfigure_paths_go_through_stop,
        test_falling_edge_sends_exactly_one_zero_virtual_rc,
        test_no_falling_edge_zero_when_never_armed,
        test_stuck_diagnostics_point_at_the_stick_first,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(f"{test.__name__} 正常跑完", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"零帧与诊断补测：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"零帧与诊断补测：全部通过（{PASSED} 项）")
    print("变异验证挖出来的三处零覆盖，现在都有网了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
