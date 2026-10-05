#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# D 路（车端 USB<->WiFi 桥）接到客户端传输层之后的真回路测试。
# 要证明的是"从客户端提交一条驾驶意图，字节能原样落到 C 板串口；车上的上行帧能拼回来"，
# 而不是只证明 socket 能连通。
from __future__ import annotations

import socket
import struct as _st
import sys
import time
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE / "car_bridge"))
sys.path.insert(0, str(HERE / "dt7_link"))

import bridge_server as bs                        # noqa: E402
import dt7_wireless_selftest as ws                # noqa: E402
from rm_client.backend import polar_link as pl     # noqa: E402
from rm_client.backend.polar_transport import (    # noqa: E402
    PolarDriveIntent, PolarLinkTransport)


FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    print(f"{'[ OK ]' if ok else '[FAIL]'} {name}" + (f"  {detail}" if detail else ""))
    FAILED += 0 if ok else 1


class Board:
    def __init__(self, upstream: bytes = b"") -> None:
        self.commands: list[bytes] = []
        self.upstream = bytearray(upstream)
        self.closed = False
        self._lock = threading.Lock()

    def write(self, data) -> int:
        with self._lock:
            self.commands.append(bytes(data))
        return len(data)

    def read(self, n: int = 512) -> bytes:
        with self._lock:
            out = bytes(self.upstream[:n])
            del self.upstream[:n]
        if not out:
            time.sleep(0.002)
        return out

    def close(self) -> None:
        self.closed = True

    @property
    def sent_bytes(self) -> bytes:
        with self._lock:
            return b"".join(self.commands)


def start_bridge(board: Board):
    bridge = bs.Bridge(serial_device="/dev/fake", listen="127.0.0.1", port=0,
                       serial_opener=lambda dev, baud: board, stop_after=200000)
    threading.Thread(target=bridge.serve_forever, daemon=True).start()
    for _ in range(200):
        if bridge.bound_port:
            return bridge
        time.sleep(0.02)
    raise AssertionError("桥没起来")


def split_frames(blob: bytes):
    """按 0x5A 帧头逐帧拆开，并验每帧的 crc8/crc16；返回 (帧列表, 消费到的偏移)。

    偏移不等于总长就说明中间有字节被改动或尾巴被吞了 —— 那才是"桥改字节"的证据。
    """
    out, i = [], 0
    while i + 4 <= len(blob):
        if blob[i] != 0x5A or pl.crc8(blob[i:i + 3]) != blob[i + 3]:
            return out, i
        n = 4 + blob[i + 1] + 2
        if i + n > len(blob):
            return out, i
        frame = blob[i:i + n]
        if _st.unpack_from("<H", frame, n - 2)[0] != pl.crc16(frame[:n - 2]):
            return out, i
        out.append(frame)
        i += n
    return out, i


def first_frame_with_id(blob: bytes, want_id: int) -> bytes:
    for frame in split_frames(blob)[0]:
        if frame[2] == want_id:
            return frame
    return b""


def main() -> int:
    print("\n--- 1) tcp 模式下 Port=Stub 不该挡住链路（这正是以前的死法）---")
    board = Board()
    bridge = start_bridge(board)
    tp = PolarLinkTransport(port="Stub", baud=115200, mock_writes=False)
    tp.transport = "tcp"
    tp.host = "127.0.0.1"
    tp.tcp_port = bridge.bound_port
    tp.start()
    try:
        check("Stub 端口 + tcp 模式仍然连上了", tp.connected is True,
              f"{tp.connected} {tp.last_error_code} {tp.last_error}")
        check("resolved_port 说的是网络链路而不是假端口号",
              tp.resolved_port.startswith("tcp://127.0.0.1"), tp.resolved_port)
    finally:
        pass

    print("\n--- 2) 提交驾驶意图 -> 字节必须原样落到 C 板 ---")
    tp.drive_channel = "virtual_rc"     # 默认是 robot_cmd(0x01)；这里要验的是 0x03 那条
    # 0x03 通道里 SHIFT 是**结构上不可达**的：RC_MAX_ALLOWED_KEY_MASK 就只有驾驶四键，
    # allow_spin_key 参数早改成"保留但不起作用"（自转走 0x04 设定 + 0x1F 回读）。
    # 所以提交 0x11 到达的应当是 0x01 —— 断言按设计写，而不是按我以为的写。
    tp.submit(PolarDriveIntent(vx=1.0, vy=0.0, wz=0.0, key_value=0x11))
    def frame_ids(blob: bytes):
        out, i = [], 0
        while i + 4 <= len(blob) and blob[i] == 0x5A:
            n = 4 + blob[i + 1] + 2
            if i + n > len(blob):
                break
            out.append(blob[i + 2])
            i += n
        return out

    deadline = time.time() + 4.0
    while time.time() < deadline and pl.CMD_VIRTUAL_RC not in frame_ids(board.sent_bytes):
        time.sleep(0.02)
    got = board.sent_bytes
    check("桥那边收到了字节", bool(got), f"{len(got)} 字节")
    # 一次提交会发多条帧（RobotCmd / 0x04 底盘指令 / 0x03 虚拟遥控器）
    frames, consumed = split_frames(got)
    check("收到的字节能逐帧拆到底、每帧 crc8/crc16 全过（桥一个字节都没改）",
          bool(frames) and consumed == len(got),
          f"{len(frames)} 帧，拆到 {consumed}/{len(got)}")
    ids = [fr[2] for fr in frames]
    check("其中包含 0x03 虚拟遥控器帧", pl.CMD_VIRTUAL_RC in ids, str([hex(i) for i in ids]))
    check("自转走 0x04：本次流里同时出现底盘指令帧",
          pl.CMD_CHASSIS_CMD in ids, str([hex(i) for i in ids]))
    rc = next((fr for fr in frames if fr[2] == pl.CMD_VIRTUAL_RC), b"")
    # 键位在 RC_ctrl_t 载荷的**末尾两个字节**（5 通道 + 2 开关 + 3 鼠标 + 2 键 + key u16）
    km = _st.unpack_from("<H", rc, len(rc) - 2 - 2)[0] if len(rc) >= 10 else -1
    check("0x03 帧里的键位被裁到驾驶四键（0x11 -> 0x01，SHIFT 结构上不可达）",
          km == 0x01, hex(km))
    check("挡法来自常量而不是调用点（谁都忘了传参也漏不出去）",
          pl.RC_MAX_ALLOWED_KEY_MASK == pl.RC_DRIVE_KEY_MASK
          and pl.drive_key_mask(True) == pl.RC_DRIVE_KEY_MASK
          and bool(pl.RC_SPIN_KEY_MASK & pl.RC_MAX_ALLOWED_KEY_MASK) is False,
          hex(pl.drive_key_mask(True)))
    check("0x03 帧体长度与固件 RC_ctrl_t 一致（5 通道+2 开关+3 鼠标+2 键+键位图）",
          len(rc) == 4 + 4 + 22 + 2, str(len(rc)))

    print("\n--- 3) 车上行帧要能经桥拼回来 ---")
    up = ws.debug_frame(1, {"km_key": 21.0, "km_rx": 7.0})
    board.upstream.extend(up)
    deadline = time.time() + 3.0
    segs = []
    while time.time() < deadline:
        segs = tp.drain_segments()
        if any(s.segment_id == pl.SEG_DEBUG for s in segs):
            break
        time.sleep(0.05)
    dbg = [s for s in segs if s.segment_id == pl.SEG_DEBUG]
    km = (dbg[0].fields or {}).get("packages", {}).get("km_key") if dbg else None
    check("上行 Debug 段解出来了（km_key=21）", km == 21, str(km))
    tp.stop()
    bridge.stop.set()
    time.sleep(0.2)
    # ⚠ 第 141 轮：原来是 `board.closed is True or True` —— **对任何 X 都为真**，
    #   标签写着「停掉之后桥与串口都关掉了」，而属性**根本没被断言**。
    #   这是注册套件里的纯态词，门禁会把它计成一项通过。
    check("停掉之后桥与串口都关掉了", board.closed is True, str(board.closed))

    print("\n--- 4) 地址没填时必须失败得清楚 ---")
    tp2 = PolarLinkTransport(port="Stub", mock_writes=False)
    tp2.transport = "tcp"
    tp2.host = ""
    tp2.start()
    check("tcp 模式没填主机 -> 不连接且给错码",
          tp2.connected is False and tp2.last_error_code == "NO_BRIDGE_HOST",
          f"{tp2.connected} {tp2.last_error_code}")
    tp2.stop()

    print("\n--- 5) 连不上的桥要报[连不上]而不是假装在重连 ---")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    tp3 = PolarLinkTransport(port="Stub", mock_writes=False)
    tp3.transport = "tcp"
    tp3.host = "127.0.0.1"
    tp3.tcp_port = free_port
    tp3.start()
    check("没人监听的端口 -> BRIDGE_UNREACHABLE",
          tp3.connected is False and tp3.last_error_code == "BRIDGE_UNREACHABLE",
          f"{tp3.last_error_code} {tp3.last_error[:50]}")
    tp3.stop()

    print("\n--- 6) 掩码对照：另起一条链路也要看到同样的挡法 ---")
    board3 = Board()
    bridge3 = start_bridge(board3)
    tp4 = PolarLinkTransport(port="Stub", mock_writes=False)
    tp4.transport = "tcp"
    tp4.host = "127.0.0.1"
    tp4.tcp_port = bridge3.bound_port
    tp4.drive_channel = "virtual_rc"
    tp4.start()
    tp4.submit(PolarDriveIntent(vx=0.0, vy=0.0, wz=0.0, key_value=0x11))
    deadline = time.time() + 4.0
    rc3 = b""
    while time.time() < deadline:
        rc3 = first_frame_with_id(board3.sent_bytes, pl.CMD_VIRTUAL_RC)
        if rc3:
            break
        time.sleep(0.02)
    seen = _st.unpack_from("<H", rc3, len(rc3) - 4)[0] if len(rc3) >= 10 else -1
    check("同样：0x11 掉成 0x01（挡 SHIFT 的是常量，不是某个调用点忘了传参）",
          seen == 0x01, hex(seen))
    tp4.stop()
    bridge3.stop.set()

    print("\n" + ("有失败项" if FAILED else "D 路传输层回路全部通过"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())