"""云台直控请求位（0x04 param0 bit0）的客户端侧回归。

背景（GAPS §3 / tools/firmware/gimbal_host/）：英雄板固件里
GIMBAL_AUTO_AIM 的唯一入口是 s[0] 上档，而麦轮上档 = 底盘无条件自转，
"底盘听 spin_cmd、瞄准听上位机"在中间档本来是 GIMBAL_IMU。本位把
"中间档 + 显式请求"提档进 GIMBAL_AUTO_AIM；未打桥固件忽略该字节。

钉住的安全性质（与自转同一条逻辑）：
  * 电平语义：随 0x04 每 tick 重发；150ms 断流后固件自动撤销请求，
    云台控制权回到驾驶员手里——客户端崩溃/拔线不会把云台锁在上位机手里。
  * 急停/停发归零：trigger_estop 与零帧突发必须把请求位压回 0。
  * 闸门：只有 "Client Gimbal Control" 开 + 链路在线 + 非急停才请求。
  * 老兼容：不发请求时 param0 恒 0，与打桥前的线上字节逐位相同。

    python tests/gimbal_host_request_test.py
"""

from __future__ import annotations

import os
import sys
import time
from itertools import product
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rm_client.backend.polar_link import (  # noqa: E402
    GIMBAL_HOST_REQUEST_ON,
    SPIN_CMD_OFF,
    SPIN_CMD_ON,
    PolarFrameCodec,
)
from rm_client.backend.polar_transport import PolarLinkTransport  # noqa: E402
from rm_client.backend.manager import BackendManager  # noqa: E402
from rm_client.backend.polar_bridge import LINK_FRESH_S, PolarStateBridge  # noqa: E402
from rm_client.backend.polar_control_policy import ControlGates  # noqa: E402
from rm_client.backend.robot_link_settings import PolarGimbalSettings  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILED.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""), flush=True)


class FakeSerial:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.is_open = True

    def write(self, payload: bytes) -> int:
        self.written.append(bytes(payload))
        return len(payload)

    def read(self, _n: int) -> bytes:
        return b""


def chassis_frames(transport: PolarLinkTransport) -> list[bytes]:
    return [f for f in transport._serial.written if f[2] == 0x04]


def pumped_frames(setup) -> list[bytes]:
    transport = PolarLinkTransport(port="COM9", mock_writes=False)
    transport._serial = FakeSerial()
    transport.connected = True
    setup(transport)
    transport._pump_tx(time.perf_counter())
    return chassis_frames(transport)


def test_encoder_wire_bytes() -> None:
    codec = PolarFrameCodec()
    quiet = codec.encode_chassis_cmd(raw_timestamp=1000, spin_cmd=SPIN_CMD_ON)
    loud = codec.encode_chassis_cmd(raw_timestamp=1000, spin_cmd=SPIN_CMD_ON,
                                    param0=GIMBAL_HOST_REQUEST_ON)
    check("param0 上线在 0x04 帧第 10 字节", quiet[9] == 0 and loud[9] == GIMBAL_HOST_REQUEST_ON,
          f"{quiet[9]}/{loud[9]}")
    check("请求位不改帧长", len(quiet) == len(loud) == 14, str((len(quiet), len(loud))))
    check("spin_cmd 字节不受影响", quiet[8] == loud[8] == SPIN_CMD_ON)


def test_transport_level_semantics() -> None:
    frames = pumped_frames(lambda t: None)
    check("默认不请求：param0 恒 0（与打桥前逐位相同）",
          bool(frames) and all(f[9] == 0 for f in frames), str([f[9] for f in frames]))
    frames = pumped_frames(lambda t: t.set_gimbal_host_request(True))
    check("请求开：0x04 每 tick 带 param0 bit0", bool(frames) and all(f[9] == 1 for f in frames),
          str([f[9] for f in frames]))


def test_estop_withdraws_request() -> None:
    transport = PolarLinkTransport(port="COM9", mock_writes=False)
    transport._serial = FakeSerial()
    transport.connected = True
    transport.set_gimbal_host_request(True)
    transport.set_spin_command(SPIN_CMD_ON)
    transport.trigger_estop()
    check("急停立刻撤回请求位", transport.gimbal_host_request() == 0)
    transport._pump_tx(time.perf_counter())
    frames = chassis_frames(transport)
    check("急停后 0x04：spin OFF 且 param0=0", bool(frames)
          and all(f[8] == SPIN_CMD_OFF and f[9] == 0 for f in frames), str([(f[8], f[9]) for f in frames]))


def test_manager_gate() -> None:
    # Exercise the production manager decision and encoder together. Do not
    # latch transport E-stop here: its separate safety layer must not conceal
    # a manager that incorrectly grants authority while gates.estopped is true.
    for requested, live, estopped, module_enabled, allow_drive in product((False, True), repeat=5):
        manager, clock = manager_with_wire()
        if not live:
            clock[0] += LINK_FRESH_S + 0.01
        gates = ControlGates(estopped, module_enabled, allow_drive, manager.polar_bridge.active)
        manager._apply_polar_gimbal_host_request(
            SimpleNamespace(dt7_enabled=True), PolarGimbalSettings(requested, False), gates)
        expected = int(requested and live and not estopped)
        case = (f"requested={requested}, live={live}, estop={estopped}, "
                f"module={module_enabled}, drive={allow_drive}")
        check("manager 三条件闸门且独立于底盘授权: " + case,
              manager.polar.gimbal_host_request() == expected)
        manager.polar._pump_tx(time.perf_counter())
        frames = chassis_frames(manager.polar)
        check("manager 决策写入真实 0x04 param0: " + case,
              bool(frames) and all(frame[9] == expected for frame in frames),
              str([frame[9] for frame in frames]))


def manager_with_wire() -> tuple[BackendManager, list[float]]:
    """Own no worker or device; only the byte-stream sink and clock are fake."""
    clock = [10.0]
    manager = BackendManager.__new__(BackendManager)
    manager.polar_bridge = PolarStateBridge(clock=lambda: clock[0])
    manager.polar_bridge.last_segment_at = clock[0]
    manager.polar_bridge.segment_counts[0x01] = 1
    manager.polar = PolarLinkTransport(port="COM9", mock_writes=False)
    manager.polar._serial = FakeSerial()
    manager.polar.connected = True
    return manager, clock


def test_manager_rechecks_late_link_expiry() -> None:
    manager, clock = manager_with_wire()
    earlier_gates = ControlGates(False, False, False, manager.polar_bridge.active)
    settings = PolarGimbalSettings(True, False)
    manager._apply_polar_gimbal_host_request(None, settings, earlier_gates)
    manager.polar._pump_tx(time.perf_counter())
    check("新鲜链路即使禁用底盘也发出云台请求",
          manager.polar.gimbal_host_request() == 1 and chassis_frames(manager.polar)[-1][9] == 1)
    manager.polar._serial.written.clear()
    clock[0] += LINK_FRESH_S + 0.01
    check("早先 gate 在线，但决策时 bridge 已过期", earlier_gates.link_active
          and not manager.polar_bridge.active)
    manager._apply_polar_gimbal_host_request(None, settings, earlier_gates)
    manager.polar._pump_tx(time.perf_counter())
    frames = chassis_frames(manager.polar)
    check("迟到过期撤回上帧请求并写出 param0=0",
          manager.polar.gimbal_host_request() == 0 and bool(frames)
          and all(frame[9] == 0 for frame in frames))


def main() -> int:
    test_encoder_wire_bytes()
    test_transport_level_semantics()
    test_estop_withdraws_request()
    test_manager_gate()
    test_manager_rechecks_late_link_expiry()
    print(f"\n合计 {len(FAILED)} 项失败")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
