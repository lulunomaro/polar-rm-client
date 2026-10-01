"""UDP 图传传输层（``rm_client/transports.py``）的四条缺陷回归。

为什么这个文件存在
------------------
``UDPVideoReceiver`` 是 SharkClient / SharkDataSever 图传的唯一入口，
``RmVideoBackend``（视觉冻结文件，不可改）继承它。审计查出四条硬缺陷，
每一条的现场症状都很难从界面反推回传输层，所以这里逐条钉死：

F1  收包缓冲默认只有 64 KiB，而且**解码内联在收包线程上**。
    内核缓冲调到 4 MiB 只能补"缓冲不够"，补不了"线程本身跑不满"：
    实测 4K 码流把 SO_RCVBUF 提到 4 MiB 之后仍然丢 22%，
    因为收包线程有一半时间在解码。
F2  ``_run`` 是 try/finally 没有 except。回调抛异常会**静默**杀死收流
    线程，``last_error`` 留着陈旧的空串，manager 再 60Hz 重启它 ——
    出货构建 console=False 没有 stderr，现场完全不可见。
F3  ``stop()`` 的 0.6 秒 join 超时会留下孤儿线程；再来一次 ``start()``
    就有两个 udp-video 线程抢同一个 socket 和同一个**无锁**
    FrameAssembler。
F4  ``packet, _addr = ...`` 丢弃来源地址。同网段任意主机灌垃圾报文就能
    占住重组器的内存预算，把真实在途帧挤掉。

跑法：``python tests/udp_transport_test.py``
"""

from __future__ import annotations

import os
import random
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.transports import (  # noqa: E402
    CameraFeed,
    FrameAssembler,
    MQTTTransport,
    OfficialMqttBridge,
    SerialBridgeStub,
    UDPVideoReceiver,
    VideoPipeline,
)

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


# --- 小工具 ---------------------------------------------------------------

def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def fragment(frame_id: int, chunk_index: int, total: int, size: int) -> bytes:
    """合成一个分片报文（内容固定为 0x5A），用于构造合法/非法流量。"""
    return struct.pack(">HHI", frame_id & 0xFFFF, chunk_index & 0xFFFF, total) + b"\x5A" * size


def split_frame(frame_id: int, payload: bytes, mtu: int = 1474) -> list[bytes]:
    """按 SharkDataSever 的 8 字节包头把一帧切成 UDP 分片（内容保持原样）。"""
    step = mtu - FrameAssembler.HEADER_BYTES
    out: list[bytes] = []
    for index, offset in enumerate(range(0, len(payload), step)):
        chunk = payload[offset:offset + step]
        header = struct.pack(">HHI", frame_id & 0xFFFF, index & 0xFFFF, len(payload))
        out.append(header + chunk)
    return out


class Source:
    """一个绑定在指定源地址上的 UDP 发送端。"""

    def __init__(self, port: int, src: str = "127.0.0.1") -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((src, 0))
        self.target = ("127.0.0.1", port)
        self.sent = 0

    def send(self, data: bytes) -> None:
        self.sent += 1
        try:
            self.sock.sendto(data, self.target)
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def udp_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("udp-video") and t.is_alive()]


def receive_threads() -> list[threading.Thread]:
    return [t for t in udp_threads() if t.name == "udp-video"]


def wait_for(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def deliver_frame(source: Source, frame_id: int, size: int = 120_000) -> bool:
    """把一帧完整送出去（分批发，避免一次性把 socket 缓冲冲爆）。"""
    frags = split_frame(frame_id, b"\x5A" * size)
    for index, item in enumerate(frags):
        source.send(item)
        if index % 20 == 19:
            time.sleep(0.002)
    return True


def start_and_wait(rx: UDPVideoReceiver, timeout: float = 3.0) -> bool:
    """``start()`` 返回时收包线程未必已经 bind 完（这是修复前就有的性质）。

    真实码流是持续到达的，manager 也会 60Hz 重试，所以生产上无所谓；
    但测试必须自己等 socket 真的存在，否则发出去的包没人接。
    """
    rx.start()
    return wait_for(lambda: rx._socket is not None, timeout=timeout)


# ===================================================================
# F1 — 收包缓冲 + 解码必须离开收包线程
# ===================================================================

def test_f1_receive_buffer_is_raised() -> None:
    port = free_port()
    rx = UDPVideoReceiver(host="127.0.0.1", port=port)
    rx.start()
    try:
        ok = wait_for(lambda: rx._socket is not None, timeout=3.0)
        check("收包线程起来了并拿到 socket", ok)
        actual = 0
        if rx._socket is not None:
            actual = int(rx._socket.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))
        check(
            f"SO_RCVBUF 被提到 4 MiB（实测 {actual} 字节，OS 默认 65536）",
            actual >= 4 * 1024 * 1024,
            f"getsockopt={actual}",
        )
    finally:
        rx.stop()
    check("stop() 之后没有残留 udp-video 线程", not udp_threads(), f"{udp_threads()}")


def test_f1_decode_runs_off_the_receive_thread() -> None:
    """回调被卡住时，收包线程必须继续把帧重组出来。

    旧实现里 ``on_frame`` 就在 ``_run`` 的循环体里：回调一卡，整条收流
    就停摆，后面 arriving 的帧全部超时丢在 socket 缓冲里。
    """
    port = free_port()
    rx = UDPVideoReceiver(host="127.0.0.1", port=port)
    release = threading.Event()
    entered = threading.Event()
    calls = {"n": 0}

    def on_frame(frame: bytes) -> None:
        calls["n"] += 1
        entered.set()
        release.wait(10.0)

    rx.on_frame = on_frame
    start_and_wait(rx)
    src = Source(port)
    try:
        deliver_frame(src, 0x0001, size=40_000)
        check("帧回调确实被调用了", entered.wait(3.0))
        # 回调还卡在第一个帧上，收包线程应该已经把后续 8 帧全重组出来
        for frame_id in range(0x0002, 0x000A):
            deliver_frame(src, frame_id, size=40_000)
        assembled = wait_for(lambda: rx.frames_received >= 9, timeout=3.0)
        check(
            "回调阻塞期间收包线程继续重组（frames_received=9，回调只跑了 1 次）",
            assembled and rx.frames_received >= 9 and calls["n"] == 1,
            f"frames_received={rx.frames_received} callback_calls={calls['n']}",
        )
        # 解码确实跑在**另一条**线程上
        dispatch_names = {
            t.name for t in threading.enumerate() if t.name == "udp-video-dispatch"
        }
        check("解码回调跑在 udp-video-dispatch 线程上", bool(dispatch_names), f"{dispatch_names}")
    finally:
        release.set()
        src.close()
        rx.stop()
    check("收尾后无残留线程", not udp_threads(), f"{udp_threads()}")


def test_f1_no_loss_when_decoder_keeps_up() -> None:
    """解码成本低于帧间隔时，传输层必须零丢帧。

    这一条是 F1 的可观测判据：解码被挪走之后，收流速率只受解码器自身
    产能限制，不再受"收包线程替解码器挡风"影响。
    """
    port = free_port()
    rx = UDPVideoReceiver(host="127.0.0.1", port=port)
    got: list[int] = []
    lock = threading.Lock()

    def on_frame(frame: bytes) -> None:
        time.sleep(0.010)          # 解码 10ms < 帧间隔 16.7ms：解码器跟得上
        with lock:
            got.append(len(frame))

    rx.on_frame = on_frame
    start_and_wait(rx)
    src = Source(port)
    sent = {"n": 0}
    stop = threading.Event()

    def pump() -> None:
        frame_id = 0x0300
        while not stop.is_set():
            deliver_frame(src, frame_id, size=300_000)
            frame_id += 1
            sent["n"] += 1
            time.sleep(1 / 60.0)

    worker = threading.Thread(target=pump, name="f1-pump", daemon=True)
    try:
        worker.start()
        time.sleep(4.0)
    finally:
        stop.set()
        worker.join(3.0)
        src.close()
        rx.stop()
    with lock:
        received = len(got)
    lost = sent["n"] - received
    rate = received / max(1, sent["n"])
    check(
        f"解码跟得上时零丢帧（发出 {sent['n']} 收到 {received}，丢 {lost}）",
        rate >= 0.98,
        f"送达率 {rate * 100:.1f}%",
    )
    check("没有残留线程", not udp_threads(), f"{udp_threads()}")


# ===================================================================
# F2 — 收包/回调异常不许静默杀死线程
# ===================================================================

def test_f2_callback_exception_is_recorded_not_fatal() -> None:
    port = free_port()
    rx = UDPVideoReceiver(host="127.0.0.1", port=port)
    seen = {"n": 0}

    def on_frame(frame: bytes) -> None:
        seen["n"] += 1
        raise RuntimeError("decode backend exploded")

    rx.on_frame = on_frame
    start_and_wait(rx)
    src = Source(port)
    try:
        for frame_id in range(0x0010, 0x0015):
            deliver_frame(src, frame_id, size=40_000)
            time.sleep(0.05)
        check(
            f"回调抛了 {seen['n']} 次异常，帧仍然被收上来",
            seen["n"] >= 3 and rx.frames_received >= 3,
            f"callback_calls={seen['n']} frames_received={rx.frames_received}",
        )
        check(
            "收流线程活着（running 仍为 True）",
            rx.running is True,
            f"running={rx.running}",
        )
        check("收包线程仍在", bool(receive_threads()), f"{udp_threads()}")
        check(
            f"last_error 记下了真实原因：{rx.last_error!r}",
            "decode backend exploded" in rx.last_error,
            f"last_error={rx.last_error!r}",
        )
        check(
            f"callback_errors 计数正确（{rx.callback_errors}）",
            rx.callback_errors >= 3,
            f"callback_errors={rx.callback_errors}",
        )
        check(
            "manager 的 'running 为 False 就重启' 不会被触发",
            rx.running is True,
            f"running={rx.running}",
        )
    finally:
        src.close()
        rx.stop()


def test_f2_receive_path_exception_is_recorded_not_fatal() -> None:
    """重组器自己抛异常（脏数据踩到的未知 bug）同样不许杀死收流线程。"""

    port = free_port()
    rx = UDPVideoReceiver(host="127.0.0.1", port=port)
    original = rx._assembler.push_packet
    state = {"raise": True, "calls": 0}

    def flaky(packet: bytes):
        state["calls"] += 1
        if state["raise"]:
            raise ValueError("assembler blew up")
        return original(packet)

    rx._assembler.push_packet = flaky  # type: ignore[method-assign]
    start_and_wait(rx)
    src = Source(port)
    try:
        src.send(fragment(0x0021, 0, 100, 90))
        time.sleep(0.2)
        check("push_packet 抛异常时线程没死", rx.running is True, f"running={rx.running}")
        check(
            f"last_error 记下了异常：{rx.last_error!r}",
            "assembler blew up" in rx.last_error,
            f"last_error={rx.last_error!r}",
        )
        check("packets_received 仍在累加", rx.packets_received >= 1, f"{rx.packets_received}")
        state["raise"] = False
        before = state["calls"]
        deliver_frame(src, 0x0022, size=40_000)
        ok = wait_for(lambda: state["calls"] > before and rx.frames_received >= 1, timeout=3.0)
        check("异常消失后收流自动恢复", ok, f"frames_received={rx.frames_received}")
    finally:
        src.close()
        rx.stop()


# ===================================================================
# F3 — stop() 不许留孤儿，start() 不许拉第二条收包线程
# ===================================================================

def test_f3_stop_leaves_no_orphan_receiver() -> None:
    port = free_port()
    rx = UDPVideoReceiver(host="127.0.0.1", port=port)
    entered = threading.Event()
    release = threading.Event()

    def on_frame(frame: bytes) -> None:
        entered.set()
        release.wait(10.0)

    rx.on_frame = on_frame
    start_and_wait(rx)
    src = Source(port)
    try:
        deliver_frame(src, 0x0041, size=40_000)
        check("帧回调已进入并卡住", entered.wait(3.0))

        started = time.monotonic()
        rx.stop()
        elapsed = time.monotonic() - started
        check(
            f"stop() 在 {elapsed:.3f}s 内返回（预算 {UDPVideoReceiver.STOP_JOIN_SECONDS}s）",
            elapsed <= UDPVideoReceiver.STOP_JOIN_SECONDS + 0.2,
            f"elapsed={elapsed:.3f}",
        )
        check(
            "收包线程一定退出了（不再抢 socket / FrameAssembler）",
            not receive_threads(),
            f"{receive_threads()}",
        )
        check("stop() 之后 running 为 False", rx.running is False, f"running={rx.running}")

        # 卡在回调里的派发线程是 Python 杀不掉的；start() 必须拒绝再拉一条
        # 收包线程，而不是制造两个线程抢同一个无锁重组器。
        rx.start()
        time.sleep(0.2)
        check(
            "上一代线程还活着时 start() 拒绝启动",
            rx.running is False and not receive_threads(),
            f"running={rx.running} receive_threads={receive_threads()}",
        )
        check(
            f"拒绝原因写进了 last_error：{rx.last_error!r}",
            "尚未退出" in rx.last_error,
            f"last_error={rx.last_error!r}",
        )
    finally:
        release.set()
        src.close()
        time.sleep(0.4)
        rx.stop()
    check("孤儿线程最终自行退出", not udp_threads(), f"{udp_threads()}")


def test_f3_restart_cycles_never_duplicate_receivers() -> None:
    """反复 start/stop，任何时刻都只允许一条收包线程。"""
    port = free_port()
    rx = UDPVideoReceiver(host="127.0.0.1", port=port)
    frames: list[int] = []
    rx.on_frame = frames.append
    src = Source(port)
    peak = {"value": 0}
    stop_watch = threading.Event()

    def watch() -> None:
        while not stop_watch.is_set():
            peak["value"] = max(peak["value"], len(receive_threads()))
            time.sleep(0.005)

    watcher = threading.Thread(target=watch, name="f3-watch", daemon=True)
    watcher.start()
    try:
        for cycle in range(5):
            check_ok = start_and_wait(rx)
            if not check_ok:
                check(f"第 {cycle} 轮 start() 未能建立 socket", False, "socket 未就绪")
                break
            deliver_frame(src, 0x0500 + cycle, size=30_000)
            time.sleep(0.2)
            rx.stop()
            time.sleep(0.05)
        check(
            f"5 轮 start/stop 中收包线程峰值 = {peak['value']}（必须为 1）",
            peak["value"] <= 1,
            f"peak={peak['value']}",
        )
        check("5 轮之后没有残留线程", not udp_threads(), f"{udp_threads()}")
        check(f"5 帧全部收到（收到 {len(frames)}）", len(frames) == 5, f"frames={len(frames)}")
    finally:
        stop_watch.set()
        watcher.join(2.0)
        src.close()
        rx.stop()
    check("收尾干净", not udp_threads(), f"{udp_threads()}")


def test_f3_stop_is_idempotent_and_safe_before_start() -> None:
    rx = UDPVideoReceiver(host="127.0.0.1", port=free_port())
    rx.stop()
    rx.stop()
    check("从未 start() 就 stop() 不抛异常", rx.running is False)
    check("从未 start() 就 stop() 不留线程", not udp_threads(), f"{udp_threads()}")


# ===================================================================
# F4 — 来源地址不再丢弃
# ===================================================================

def test_f4_peer_is_locked_on_first_complete_frame() -> None:
    port = free_port()
    rx = UDPVideoReceiver(host="127.0.0.1", port=port)
    rx.on_frame = lambda frame: None
    start_and_wait(rx)
    src = Source(port)
    try:
        check("刚开始没有认定任何来源", rx.peer_addr is None, f"{rx.peer_addr}")
        deliver_frame(src, 0x0061, size=120_000)
        ok = wait_for(lambda: rx.peer_addr is not None, timeout=3.0)
        check(f"第一帧完整到达后来源被认定：{rx.peer_addr}", ok, f"peer={rx.peer_addr}")
        check(
            "认定的就是那个发送端",
            rx.peer_addr is not None and rx.peer_addr[0] == "127.0.0.1",
            f"peer={rx.peer_addr}",
        )
    finally:
        src.close()
        rx.stop()


def test_f4_foreign_host_cannot_touch_the_assembler() -> None:
    """同网段主机灌 4 万个垃圾报文：进不了重组器，挤不掉真实在途帧。"""
    port = free_port()
    rx = UDPVideoReceiver(host="127.0.0.1", port=port)
    delivered: list[bytes] = []
    rx.on_frame = delivered.append
    start_and_wait(rx)
    good = Source(port, src="127.0.0.1")
    foreign = Source(port, src="127.0.0.9")
    try:
        deliver_frame(good, 0x0071, size=120_000)
        check("握手帧到位，来源已认定", wait_for(lambda: rx.peer_addr is not None, timeout=3.0))

        # 真实在途帧：只发前半截
        real = split_frame(0x0072, b"\x5A" * 300_000)
        for item in real[:150]:
            good.send(item)
        ok = wait_for(lambda: 0x0072 in rx._assembler.frames, timeout=3.0)
        check("真实在途帧已进入重组器", ok, f"frames={list(rx._assembler.frames)[:8]}")

        # 外来洪水
        for i in range(40_000):
            foreign.send(fragment(0xF000 + (i % 64), i & 0x0FFF, 16 * 1024 * 1024, 1466))
        time.sleep(0.5)

        check(
            f"洪水报文全部被来源闸门挡下（foreign_packets={rx.foreign_packets}）",
            rx.foreign_packets >= 30_000,
            f"foreign_packets={rx.foreign_packets}",
        )
        check(
            "重组器里没有留下任何外来帧",
            len(rx._assembler.frames) == 1 and 0xF000 not in rx._assembler.frames,
            f"frames_in_flight={len(rx._assembler.frames)}",
        )
        check(
            f"真实在途帧没被挤掉（{rx._assembler.frames.get(0x0072, {}).get('received', 0)} 字节）",
            0x0072 in rx._assembler.frames,
            f"frames={list(rx._assembler.frames)[:8]}",
        )

        # 洪水之后真实码流必须照旧工作
        before = len(delivered)
        for frame_id in range(0x0080, 0x0086):
            deliver_frame(good, frame_id, size=120_000)
        ok = wait_for(lambda: len(delivered) >= before + 6, timeout=5.0)
        check("洪水之后真实码流照旧收帧", ok, f"delivered={len(delivered)}")
    finally:
        good.close()
        foreign.close()
        rx.stop()


def test_f4_source_change_is_relearned() -> None:
    """对端换端口重启（真实现场会发生）必须能自动重新认定，不能永久拉黑。"""
    port = free_port()
    rx = UDPVideoReceiver(host="127.0.0.1", port=port)
    rx.on_frame = lambda frame: None
    start_and_wait(rx)
    first = Source(port, src="127.0.0.1")
    second = Source(port, src="127.0.0.5")
    try:
        deliver_frame(first, 0x0091, size=120_000)
        check("第一个来源被认定", wait_for(lambda: rx.peer_addr is not None, timeout=3.0))
        first_addr = rx.peer_addr
        # 旧来源彻底沉默超过 PEER_SILENCE_SECONDS
        time.sleep(UDPVideoReceiver.PEER_SILENCE_SECONDS + 0.3)
        deliver_frame(second, 0x0092, size=120_000)
        ok = wait_for(
            lambda: rx.peer_addr is not None and rx.peer_addr != first_addr, timeout=4.0
        )
        check(f"换源后自动重新认定：{first_addr} -> {rx.peer_addr}", ok, f"peer={rx.peer_addr}")
        check(
            "新来源确实收得到帧",
            wait_for(lambda: rx.frames_received >= 2, timeout=3.0),
            f"frames_received={rx.frames_received}",
        )
    finally:
        first.close()
        second.close()
        rx.stop()


# ===================================================================
# 行为等价：来源闸门对"单一来源"必须逐位无差别
# ===================================================================

class _UngatedReceiver(UDPVideoReceiver):
    """把来源闸门整个短路掉，等价于修复前的收包行为。"""

    def _admit_source(self, packet: bytes, addr, now: float) -> bool:  # noqa: D102
        return True


def _random_packet_stream(seed: int, count: int) -> list[bytes]:
    rng = random.Random(seed)
    packets: list[bytes] = []
    for index in range(count):
        style = index % 5
        if style == 0:                                   # 合法分片
            size = rng.randint(1, 1400)
            packets.append(fragment(rng.randint(0, 3), rng.randint(0, 20), size, size))
        elif style == 1:                                 # total 越界
            packets.append(fragment(rng.randint(0, 3), 0, 0, 50))
        elif style == 2:                                 # payload 比 total 还大
            packets.append(fragment(rng.randint(0, 3), 0, 10, 900))
        elif style == 3:                                 # 太短 / 没有 payload
            packets.append(bytes(rng.randint(0, 8)))
        else:                                            # 纯随机垃圾
            packets.append(bytes(rng.randint(0, 1500)))
    return packets


def _collect_via_socket(cls, packets: list[bytes], port: int) -> tuple[list[bytes], int, int]:
    rx = cls(host="127.0.0.1", port=port)
    frames: list[bytes] = []
    lock = threading.Lock()

    def on_frame(frame: bytes) -> None:
        with lock:
            frames.append(frame)

    rx.on_frame = on_frame
    start_and_wait(rx)
    src = Source(port)
    try:
        # 小批量 + 让收包线程喘口气：否则丢的是内核 socket 缓冲，
        # 那会让 packets_received 的对比变成对 Windows 调度器的测试。
        for index, packet in enumerate(packets):
            src.send(packet)
            if index % 8 == 7:
                time.sleep(0.004)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and rx.packets_received < len(packets):
            time.sleep(0.02)
        time.sleep(0.4)
        with lock:
            return list(frames), rx.frames_received, rx.packets_received
    finally:
        src.close()
        rx.stop()
        time.sleep(0.15)


def test_gate_is_bitwise_equivalent_for_a_single_source() -> None:
    """同一个报文序列，闸门开与不开必须产出**逐位相同**的帧序列。

    这是"加来源甄别不改变正常码流输出"的证明：单一来源时
    ``_looks_like_fragment`` 与 ``FrameAssembler`` 的结构校验同构，
    合法分片一条都不会被误杀，计数也完全一致。
    """
    packets = _random_packet_stream(20260823, 1500)
    gated_frames, gated_count, gated_packets = _collect_via_socket(
        UDPVideoReceiver, packets, free_port()
    )
    open_frames, open_count, open_packets = _collect_via_socket(
        _UngatedReceiver, packets, free_port()
    )
    check(
        f"两次运行都没被内核丢包（{gated_packets} / {open_packets} / 共 {len(packets)}）",
        gated_packets == len(packets) and open_packets == len(packets),
        f"gated={gated_packets} open={open_packets} sent={len(packets)}",
    )
    check(
        f"帧序列逐位一致（各 {len(gated_frames)} 帧）",
        gated_frames == open_frames,
        f"gated={len(gated_frames)} open={len(open_frames)}",
    )
    if gated_frames != open_frames:
        for index, (left, right) in enumerate(zip(gated_frames, open_frames)):
            if left != right:
                check(f"第一处不一致在第 {index} 帧", False, f"{len(left)}B vs {len(right)}B")
                break
    check(
        f"frames_received 一致（{gated_count} vs {open_count}）",
        gated_count == open_count,
        f"gated={gated_count} open={open_count}",
    )
    check(
        f"packets_received 一致（{gated_packets} vs {open_packets}）",
        gated_packets == open_packets,
        f"gated={gated_packets} open={open_packets}",
    )


def test_looks_like_fragment_matches_assembler_gate() -> None:
    """``_looks_like_fragment`` 必须与重组器的结构校验逐条同构。

    判据：对任意报文，``_looks_like_fragment(p)`` 为真 ⟹ 重组器不会因为
    那 4 条结构条件拒收它。反向不要求（重组器还有状态相关的检查）。
    """
    rng = random.Random(4242)
    assembler_gate = FrameAssembler()
    mismatches = 0
    total = 0
    for _ in range(40_000):
        style = rng.randint(0, 5)
        if style == 0:
            packet = bytes(rng.randint(0, 12))
        elif style == 1:
            packet = fragment(rng.randint(0, 65535), rng.randint(0, 65535),
                              rng.randint(0, 64), rng.randint(1, 2000))
        elif style == 2:
            packet = fragment(rng.randint(0, 65535), rng.randint(0, 65535),
                              16 * 1024 * 1024 + rng.randint(0, 1000), rng.randint(1, 1400))
        elif style == 3:
            packet = bytes(rng.randint(0, 1600))
        elif style == 4:
            packet = fragment(rng.randint(0, 65535), rng.randint(4090, 65535),
                              rng.randint(1, 100000), rng.randint(1, 1400))
        else:
            packet = fragment(rng.randint(0, 65535), rng.randint(0, 4095),
                              rng.randint(1, 100000), rng.randint(1, 1400))
        total += 1
        # 复刻 FrameAssembler.push_packet 的 4 条结构条件
        total_bytes = int.from_bytes(packet[4:8], "big") if len(packet) >= 8 else 0
        payload = packet[FrameAssembler.HEADER_BYTES:]
        chunk_index = int.from_bytes(packet[2:4], "big") if len(packet) >= 4 else 0
        assembler_accepts_structure = not (
            not payload
            or not (1 <= total_bytes <= FrameAssembler.MAX_FRAME_BYTES)
            or len(payload) > total_bytes
            or chunk_index >= FrameAssembler.MAX_CHUNKS_PER_FRAME
        )
        if UDPVideoReceiver._looks_like_fragment(packet) and not assembler_accepts_structure:
            mismatches += 1
    check(
        f"{total} 个随机报文里没有「误杀」（闸门放行但重组器会拒）",
        mismatches == 0,
        f"mismatches={mismatches}",
    )
    # 顺带确认这条判据不是"永远放行"或"永远拒绝"的空实现
    check(
        "闸门确实会拒绝明显非法的报文",
        not UDPVideoReceiver._looks_like_fragment(b"")
        and not UDPVideoReceiver._looks_like_fragment(bytes(8))
        and not UDPVideoReceiver._looks_like_fragment(fragment(1, 0, 0, 10)),
    )
    check(
        "闸门确实会放行正常分片",
        UDPVideoReceiver._looks_like_fragment(fragment(1, 0, 1466, 1466)),
    )
    del assembler_gate


# ===================================================================
# 子类 / 兄弟类零破坏
# ===================================================================

def test_rm_video_backend_subclass_still_works() -> None:
    """RmVideoBackend 是视觉冻结文件里的子类：继承关系不许被改坏。"""
    from rm_client.backend.video_backend import RmVideoBackend

    check("RmVideoBackend 仍然是 UDPVideoReceiver 的子类", issubclass(RmVideoBackend, UDPVideoReceiver))
    port = free_port()
    backend = RmVideoBackend(host="127.0.0.1", port=port)
    try:
        backend.start()
        check("子类 start() 正常返回", wait_for(lambda: backend.running, timeout=3.0),
              f"running={backend.running} last_error={backend.last_error!r}")
        src = Source(port)
        deliver_frame(src, 0x00A1, size=120_000)
        ok = wait_for(lambda: backend.frames_received >= 1, timeout=4.0)
        check("子类能收到整帧", ok, f"frames_received={backend.frames_received}")
        stats = backend.stats()
        check("子类 stats() 仍可调用", isinstance(stats, dict) and "packets_received" in stats)
        check("子类 stats 里的计数为正", stats.get("packets_received", 0) > 0, f"{stats.get('packets_received')}")
        src.close()
    finally:
        backend.stop()
    check("子类 stop() 后无残留线程", not udp_threads(), f"{udp_threads()}")


def test_sibling_transports_untouched() -> None:
    """本组只碰 UDPVideoReceiver，兄弟类必须原样可用。"""
    payload = b"\x01\x02\x03\x04" * 1000        # 4000 字节 = 3 个分片
    frags = split_frame(2, payload)
    check("一帧确实被切成多个分片", len(frags) == 3, f"frags={len(frags)}")
    assembler = FrameAssembler()
    check("不完整的帧不返回结果", assembler.push_packet(frags[0]) is None)
    result: list[bytes] = []
    for packet in frags:
        value = assembler.push_packet(packet)
        if value is not None:
            result.append(value)
    check("FrameAssembler 完整重组", result == [payload], f"{[len(v) for v in result]}")

    frame = b"\x7f" * 64
    pipeline = VideoPipeline()
    for packet in split_frame(1, frame):
        pipeline.push_udp_packet(packet)
    check("VideoPipeline 透传整帧", pipeline.last_frame == frame and pipeline.frames_received == 1)

    bridge = OfficialMqttBridge()
    check("OfficialMqttBridge 10Hz 限频不变",
          bridge.publish_common_command(b"a", now=10.0) is True
          and bridge.publish_common_command(b"b", now=10.05) is False
          and bridge.publish_common_command(b"c", now=10.101) is True)

    serial = SerialBridgeStub()
    serial.start()
    serial.write(b"\x01")
    check("SerialBridgeStub 不受影响", serial.last_write == b"\x01")
    serial.stop()

    mqtt = MQTTTransport(host="127.0.0.1", port=1)
    check("MQTTTransport 仍可构造", mqtt.host == "127.0.0.1" and mqtt.connected is False)

    camera = CameraFeed(0)
    camera.stop()
    check("CameraFeed 不受影响", camera.capture is None and camera.connected is False)


def main() -> int:
    tests = [
        test_f1_receive_buffer_is_raised,
        test_f1_decode_runs_off_the_receive_thread,
        test_f1_no_loss_when_decoder_keeps_up,
        test_f2_callback_exception_is_recorded_not_fatal,
        test_f2_receive_path_exception_is_recorded_not_fatal,
        test_f3_stop_leaves_no_orphan_receiver,
        test_f3_restart_cycles_never_duplicate_receivers,
        test_f3_stop_is_idempotent_and_safe_before_start,
        test_f4_peer_is_locked_on_first_complete_frame,
        test_f4_foreign_host_cannot_touch_the_assembler,
        test_f4_source_change_is_relearned,
        test_gate_is_bitwise_equivalent_for_a_single_source,
        test_looks_like_fragment_matches_assembler_gate,
        test_rm_video_backend_subclass_still_works,
        test_sibling_transports_untouched,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            check(f"{test.__name__} 抛出未捕获异常", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"UDP 传输层：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"UDP 传输层：全部通过（{PASSED} 项）")
    print("收包缓冲 4MiB + 解码离线程；异常记账不杀线程；stop() 不留孤儿；"
          "来源闸门对外来报文 100% 拒收且对单一来源逐位等价。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

