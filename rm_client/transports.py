from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import socket
import threading
import time
from typing import Callable


class FrameAssembler:
    """重组 SharkDataSever 的 UDP 分片包。

    包头格式：frame_id uint16 BE、chunk_index uint16 BE、total_bytes uint32 BE。
    """

    HEADER_BYTES = 8
    MAX_FRAME_BYTES = 16 * 1024 * 1024
    MAX_BUFFERED_BYTES = 64 * 1024 * 1024
    MAX_IN_FLIGHT = 64
    MAX_CHUNKS_PER_FRAME = 4096
    STALE_SECONDS = 2.0

    def __init__(self) -> None:
        self.frames: dict[int, dict[str, object]] = {}
        self.dropped_count = 0
        self.invalid_count = 0
        self.buffered_bytes = 0

    def clear(self) -> None:
        self.frames.clear()
        self.buffered_bytes = 0
        self.dropped_count = 0
        self.invalid_count = 0

    def push_packet(self, packet: bytes) -> bytes | None:
        if len(packet) < self.HEADER_BYTES:
            self.invalid_count += 1
            return None
        frame_id = int.from_bytes(packet[0:2], "big")
        chunk_index = int.from_bytes(packet[2:4], "big")
        total_bytes = int.from_bytes(packet[4:8], "big")
        payload = packet[self.HEADER_BYTES:]
        self._drop_old_frames()
        if (
            not payload
            or not (1 <= total_bytes <= self.MAX_FRAME_BYTES)
            or len(payload) > total_bytes
            or chunk_index >= self.MAX_CHUNKS_PER_FRAME
        ):
            self.invalid_count += 1
            return None
        entry = self.frames.get(frame_id)
        if entry is not None and int(entry["total"]) != total_bytes:
            self._remove_frame(frame_id, dropped=True)
            self.invalid_count += 1
            return None
        if entry is None:
            if not self._make_room(len(payload), new_frame=True):
                self.invalid_count += 1
                return None
            entry = {
                "total": total_bytes,
                "chunks": {},
                "received": 0,
                "created": time.monotonic(),
            }
            self.frames[frame_id] = entry
        chunks: dict[int, bytes] = entry["chunks"]  # type: ignore[assignment]
        existing = chunks.get(chunk_index)
        if existing is not None and existing != payload:
            self._remove_frame(frame_id, dropped=True)
            self.invalid_count += 1
            return None
        if existing is None:
            if len(chunks) >= self.MAX_CHUNKS_PER_FRAME:
                self._remove_frame(frame_id, dropped=True)
                self.invalid_count += 1
                return None
            if not self._make_room(len(payload), protected_frame_id=frame_id):
                self._remove_frame(frame_id, dropped=True)
                self.invalid_count += 1
                return None
            chunks[chunk_index] = payload
            entry["received"] = int(entry["received"]) + len(payload)
            self.buffered_bytes += len(payload)
        received = int(entry["received"])
        if received > total_bytes:
            self._remove_frame(frame_id, dropped=True)
            self.invalid_count += 1
            return None
        if received == total_bytes:
            indexes = sorted(chunks)
            if indexes != list(range(len(indexes))):
                self._remove_frame(frame_id, dropped=True)
                self.invalid_count += 1
                return None
            ordered = [chunks[idx] for idx in indexes]
            complete = b"".join(ordered)
            if len(complete) == total_bytes:
                self._remove_frame(frame_id)
                return complete
            self._remove_frame(frame_id, dropped=True)
            self.invalid_count += 1
        return None

    def _remove_frame(self, frame_id: int, *, dropped: bool = False) -> None:
        entry = self.frames.pop(frame_id, None)
        if entry is None:
            return
        self.buffered_bytes = max(0, self.buffered_bytes - int(entry.get("received", 0)))
        if dropped:
            self.dropped_count += 1

    def _make_room(
        self,
        reserve_bytes: int,
        *,
        protected_frame_id: int | None = None,
        new_frame: bool = False,
    ) -> bool:
        """Bound both frame count and aggregate fragment memory.

        A single valid frame is at most 16 MiB, but sixty-four such partial
        frames would otherwise retain roughly one GiB.  Prefer evicting the
        oldest unrelated frame; an in-progress frame is protected while its
        next fragment is admitted.
        """

        reserve_bytes = max(0, int(reserve_bytes))
        while (
            (new_frame and len(self.frames) >= self.MAX_IN_FLIGHT)
            or self.buffered_bytes + reserve_bytes > self.MAX_BUFFERED_BYTES
        ):
            candidates = [
                frame_id
                for frame_id in self.frames
                if frame_id != protected_frame_id
            ]
            if not candidates:
                return False
            oldest = min(
                candidates,
                key=lambda frame_id: float(self.frames[frame_id].get("created", 0.0)),
            )
            self._remove_frame(oldest, dropped=True)
        return True

    def _drop_old_frames(self) -> None:
        now = time.monotonic()
        stale = [
            frame_id
            for frame_id, entry in self.frames.items()
            if now - float(entry.get("created", now)) > self.STALE_SECONDS
        ]
        for frame_id in stale:
            self._remove_frame(frame_id, dropped=True)


@dataclass
class UDPVideoReceiver:
    """SharkDataSever UDP 图传接收端。

    收包线程只做三件事：``recvfrom``、分片重组、把整帧塞进派发队列。
    帧回调（真正的解码）跑在独立的 ``udp-video-dispatch`` 线程上。
    这条分界线是必要的：解码一旦内联在收包线程上，内核 socket 缓冲就会在
    一次解码的工夫里被冲爆，而缓冲无论调多大都救不了"线程本身跑不满"。

    来源地址不再丢弃：``recvfrom`` 的第二个返回值被用来做来源甄别，
    见 :meth:`_admit_source`。
    """

    # --- 可调常量（类属性，不进 dataclass 字段，子类签名不受影响）-------
    #: 内核接收缓冲。实测 Windows 默认只有 65536 字节（64 KiB）。
    RCVBUF_BYTES = 4 * 1024 * 1024
    #: 派发队列上限（帧数与字节数双限）。解码跟不上时丢**最旧**的一帧，
    #: 宁可晚一点也不能让队列拖着过期画面。
    FRAME_QUEUE_MAX = 4
    FRAME_QUEUE_MAX_BYTES = 8 * 1024 * 1024
    #: 学习窗口：开流后多久之内认第一个"组出完整帧"的源。
    PEER_LEARN_SECONDS = 1.5
    #: 学习窗口里**所有**未验证源合起来最多能占用的重组内存。取
    #: ``FrameAssembler.MAX_FRAME_BYTES``，是为了保证任何一个重组器
    #: 肯收的合法帧都一定能在学习期里组完 —— 预算永远不会饿死真码流。
    PEER_LEARN_MAX_BYTES = FrameAssembler.MAX_FRAME_BYTES
    #: 学习窗口里最多同时跟踪几个源（UDP 源地址可伪造，跟踪表必须封顶）。
    PEER_LEARN_MAX_SOURCES = 4
    #: 已认定的源静默多久之后，另一个源才可以进入学习（对端换端口重启）。
    PEER_SILENCE_SECONDS = 1.0
    #: ``stop()`` 的**总** join 预算，沿用历史的 0.6 秒。
    STOP_JOIN_SECONDS = 0.6
    #: ``start()`` 回收上一代孤儿线程的预算。
    ORPHAN_REAP_SECONDS = 0.5
    RECV_TIMEOUT = 0.2
    RECV_BUFFER = 65535

    host: str = "127.0.0.1"
    port: int = 3334
    on_frame: Callable[[bytes], None] | None = None
    running: bool = False
    packets_received: int = 0
    frames_received: int = 0
    last_error: str = ""
    # --- 新增的可观测计数（全部 init=False，构造签名向后兼容）---
    #: 派发队列溢出丢弃的整帧数。
    frames_dropped: int = field(default=0, init=False)
    #: 因为来源不是已认定的 peer 而被拒的报文数。
    foreign_packets: int = field(default=0, init=False)
    #: 学习窗口内因为结构非法 / 超预算而被拒的报文数。
    untrusted_packets: int = field(default=0, init=False)
    #: ``on_frame`` 抛异常的次数。
    callback_errors: int = field(default=0, init=False)
    #: socket 层错误次数。
    recv_errors: int = field(default=0, init=False)
    #: 当前认定的来源地址（None = 仍在学习）。
    peer_addr: tuple[str, int] | None = field(default=None, init=False)

    _thread: threading.Thread | None = field(default=None, init=False)
    _socket: socket.socket | None = field(default=None, init=False)
    _assembler: FrameAssembler = field(default_factory=FrameAssembler, init=False)
    _dispatch_thread: threading.Thread | None = field(default=None, init=False)
    _queue: deque = field(default_factory=deque, init=False)
    _queue_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _queue_wake: threading.Event = field(default_factory=threading.Event, init=False)
    _queued_bytes: int = field(default=0, init=False)
    _generation: int = field(default=0, init=False)
    _peer_seen_at: float = field(default=0.0, init=False)
    _learn_bytes: dict = field(default_factory=dict, init=False)
    _learn_until: float = field(default=0.0, init=False)

    def start(self) -> None:
        if self.running:
            return
        if not self._reap_previous_threads():
            # 上一代线程还活着（多半卡在一次解不出来的回调里）。它仍持有
            # 旧 socket 和同一个**无锁** FrameAssembler —— 再拉一个收包线程
            # 就是两个线程抢同一份状态，症状是随机的坏帧和莫名的重组计数。
            # 宁可这一拍不启动：manager 的 refresh_vision_runtime 会 60Hz 重试。
            self.last_error = "上一次 UDP 收流线程尚未退出，本拍不启动"
            return
        self._generation += 1
        generation = self._generation
        self.running = True
        self._reset_peer_state()
        self._clear_queue()
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop,
            args=(generation,),
            name="udp-video-dispatch",
            daemon=True,
        )
        self._dispatch_thread.start()
        self._thread = threading.Thread(
            target=self._run, args=(generation,), name="udp-video", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        # 让在跑的 worker 立刻作废：它下一次循环检查就会退出，不会再往
        # 这一代的队列里塞东西。
        self._generation += 1
        self._queue_wake.set()
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        # 两个线程共用**一个** 0.6 秒预算，保持 stop() 的历史时延上界。
        deadline = time.monotonic() + self.STOP_JOIN_SECONDS
        receive_thread = self._thread
        dispatch_thread = self._dispatch_thread
        for thread in (receive_thread, dispatch_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        # 还活着的那个**不要**丢掉句柄：start() 还要靠它做孤儿回收。
        if receive_thread is None or not receive_thread.is_alive():
            self._thread = None
        if dispatch_thread is None or not dispatch_thread.is_alive():
            self._dispatch_thread = None
        # 正常路径下派发线程已经把队列送空并自行退出了；只有 join 预算
        # 被一次解不出来的回调吃光时它还活着，这时的 leftovers 只能丢。
        if dispatch_thread is not None and dispatch_thread.is_alive():
            self._clear_queue()

    # ------------------------------------------------------------------
    # 收包线程：只读 socket、只重组，不解码
    # ------------------------------------------------------------------
    def _run(self, generation: int | None = None) -> None:
        if generation is None:
            generation = self._generation
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError as exc:
            self._fail_bind(generation, exc)
            return
        try:
            try:
                self._apply_receive_buffer(sock)
                sock.bind((self.host, self.port))
                sock.settimeout(self.RECV_TIMEOUT)
            except OSError as exc:
                # 原来这里直接 return，socket 句柄一直挂在 self._socket 上
                # 要等下一次 start() 靠引用计数回收；现在当场关掉。
                self._fail_bind(generation, exc)
                return
            if generation != self._generation or not self.running:
                return
            self._socket = sock
            while self.running and self._generation == generation:
                try:
                    packet, addr = sock.recvfrom(self.RECV_BUFFER)
                except socket.timeout:
                    continue
                except OSError as exc:
                    if self.running and self._generation == generation:
                        self.last_error = str(exc)
                        self.recv_errors += 1
                    break
                self.packets_received += 1
                try:
                    self._handle_packet(packet, addr)
                except Exception as exc:  # noqa: BLE001
                    # 收包线程绝不能因为一个坏包/坏回调就静默死掉：现场
                    # 只看到 running 变 False，manager 60Hz 重启，谁也不知道
                    # 上一次为什么死。这里记账并继续收流。
                    self._record_error(
                        f"UDP 收包失败: {type(exc).__name__}: {exc}"
                    )
        except Exception as exc:  # noqa: BLE001
            self._record_error(f"UDP 收流线程异常: {type(exc).__name__}: {exc}")
        finally:
            if self._socket is sock:
                self._socket = None
                self.running = False
            try:
                sock.close()
            except OSError:
                pass

    def _handle_packet(self, packet: bytes, addr) -> None:
        now = time.monotonic()
        if not self._admit_source(packet, addr, now):
            return
        frame = self._assembler.push_packet(packet)
        if frame is None:
            return
        self.frames_received += 1
        self._promote_source(addr, now)
        self._enqueue(frame)

    # ------------------------------------------------------------------
    # 派发线程：解码在这里，收包线程不再为它停摆
    # ------------------------------------------------------------------
    def _dispatch_loop(self, generation: int | None = None) -> None:
        if generation is None:
            generation = self._generation
        # 退出条件刻意不是 `while self.running`：stop() 之后要把队列里已经
        # 重组好的帧**送完**。修复前 on_frame 是在收包线程里同步调用的，
        # 一帧只要重组出来就一定会被回调到；解码挪到独立线程之后如果不
        # 补这一下，stop() 会把排队中的帧直接丢掉 —— 那是一个可观测的行为
        # 回退。剩下的帧由 stop() 在 join 预算用尽后兜底丢弃。
        while self._queue_pending() or (self.running and self._generation == generation):
            frame = self._take_frame(0.05)
            if frame is None:
                continue
            hook = self.on_frame
            if hook is None:
                continue
            try:
                hook(frame)
            except Exception as exc:  # noqa: BLE001
                # 一次坏帧不许杀死派发线程。记进 last_error / callback_errors，
                # 这样"回调炸了"在 UI 的 last_error 上看得见，而不是变成
                # 一个没人记录的线程静默死亡。
                self.callback_errors += 1
                self._record_error(f"UDP 帧回调失败: {type(exc).__name__}: {exc}")

    def _queue_pending(self) -> bool:
        with self._queue_lock:
            return bool(self._queue)

    def _enqueue(self, frame: bytes) -> None:
        with self._queue_lock:
            self._queue.append(frame)
            self._queued_bytes += len(frame)
            while len(self._queue) > 1 and (
                len(self._queue) > self.FRAME_QUEUE_MAX
                or self._queued_bytes > self.FRAME_QUEUE_MAX_BYTES
            ):
                stale = self._queue.popleft()
                self._queued_bytes -= len(stale)
                self.frames_dropped += 1
        self._queue_wake.set()

    def _take_frame(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            # 先 clear 再看队列：这样"刚 set 完就被清掉"的竞态不存在 ——
            # 要么我们在锁里看见了它，要么 set 发生在 clear 之后。
            self._queue_wake.clear()
            with self._queue_lock:
                if self._queue:
                    frame = self._queue.popleft()
                    self._queued_bytes -= len(frame)
                    return frame
            if not self.running:
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return None
            self._queue_wake.wait(min(0.05, remaining))

    def _clear_queue(self) -> None:
        with self._queue_lock:
            self._queue.clear()
            self._queued_bytes = 0

    # ------------------------------------------------------------------
    # 来源甄别
    # ------------------------------------------------------------------
    @staticmethod
    def _looks_like_fragment(packet: bytes) -> bool:
        """与 ``FrameAssembler.push_packet`` 的结构校验逐条同构。

        凡是重组器会当成合法分片收下的包，这里必须放行 —— 这是"加来源
        甄别不改变正常码流输出"的前提，测试里用随机报文做逐位对拍来钉死。
        """

        if len(packet) <= FrameAssembler.HEADER_BYTES:
            return False
        total_bytes = int.from_bytes(packet[4:8], "big")
        if not (1 <= total_bytes <= FrameAssembler.MAX_FRAME_BYTES):
            return False
        if len(packet) - FrameAssembler.HEADER_BYTES > total_bytes:
            return False
        return int.from_bytes(packet[2:4], "big") < FrameAssembler.MAX_CHUNKS_PER_FRAME

    def _admit_source(self, packet: bytes, addr, now: float) -> bool:
        """决定这一个报文能不能进重组器。

        规则（按顺序）：

        1. 已认定的 peer 自己的报文：无条件放行。
        2. peer 还活着（静默不足 ``PEER_SILENCE_SECONDS``）时的其他地址：
           一律拒掉并计数。**这是主力防线** —— 生产环境里真码流从不静默
           超过 1 秒，所以同网段垃圾报文根本进不了重组器，挤不掉真实
           在途帧，也涨不了 RSS。
        3. peer 静默够久了（对端重启换了端口 / 换网卡）：重新学习，但仍然
           要过结构校验和全局字节预算。
        4. 学习期预算打满：冻结到本窗口结束，之后自动重置 —— 预算永远
           不可能把真码流永久饿死。
        5. 转正只认"组出完整帧"这一件事（见 :meth:`_promote_source`），
           不认"谁喊得响"。所以噪声抢不到 peer 位置。
        """

        peer = self.peer_addr
        if peer is not None and addr == peer:
            self._peer_seen_at = now
            return True
        if peer is not None and now - self._peer_seen_at < self.PEER_SILENCE_SECONDS:
            self.foreign_packets += 1
            return False
        if peer is not None:
            # 源换了：重新学习，但预算与结构校验一条都不放宽。
            self.peer_addr = None
            self._learn_bytes.clear()
            self._learn_until = now + self.PEER_LEARN_SECONDS
        if now > self._learn_until:
            self._learn_bytes.clear()
            self._learn_until = now + self.PEER_LEARN_SECONDS
        if not self._looks_like_fragment(packet):
            self.untrusted_packets += 1
            return False
        charged = sum(self._learn_bytes.values())
        if (
            addr not in self._learn_bytes
            and len(self._learn_bytes) >= self.PEER_LEARN_MAX_SOURCES
        ) or charged + len(packet) > self.PEER_LEARN_MAX_BYTES:
            self.untrusted_packets += 1
            return False
        self._learn_bytes[addr] = self._learn_bytes.get(addr, 0) + len(packet)
        return True

    def _promote_source(self, addr, now: float) -> None:
        """能组出完整帧的源即刻转正 —— 垃圾报文几乎不可能走到这一步。"""

        if self.peer_addr == addr:
            self._peer_seen_at = now
            return
        self.peer_addr = addr
        self._peer_seen_at = now
        self._learn_bytes.clear()
        self._learn_until = now + self.PEER_LEARN_SECONDS

    def _reset_peer_state(self) -> None:
        self.peer_addr = None
        self._peer_seen_at = 0.0
        self._learn_bytes.clear()
        self._learn_until = time.monotonic() + self.PEER_LEARN_SECONDS

    # ------------------------------------------------------------------
    def _fail_bind(self, generation: int, exc: OSError) -> None:
        if generation != self._generation:
            return
        self.last_error = str(exc)
        self.running = False

    def _record_error(self, message: str) -> None:
        self.last_error = message

    def _reap_previous_threads(self) -> bool:
        deadline = time.monotonic() + self.ORPHAN_REAP_SECONDS
        alive = False
        for thread in (self._thread, self._dispatch_thread):
            if thread is None or not thread.is_alive():
                continue
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
            alive = alive or thread.is_alive()
        if not alive:
            self._thread = None
            self._dispatch_thread = None
        return not alive

    @staticmethod
    def _apply_receive_buffer(sock: socket.socket) -> int:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDPVideoReceiver.RCVBUF_BYTES)
            return int(sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))
        except OSError:
            return 0



class CameraFeed:
    def __init__(self, camera_id: int = 0) -> None:
        self.camera_id = camera_id
        self.capture = None
        self.last_error = ""
        self.connected = False
        self._cv2 = None

    def start(self) -> None:
        try:
            import cv2  # type: ignore

            self._cv2 = cv2
            self.capture = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
            if not self.capture.isOpened():
                self.capture.release()
                self.capture = cv2.VideoCapture(self.camera_id)
            self.connected = bool(self.capture and self.capture.isOpened())
            if not self.connected:
                self.last_error = "摄像头不可用"
        except Exception as exc:  # pragma: no cover - depends on local camera stack
            self.last_error = str(exc)
            self.connected = False

    def stop(self) -> None:
        if self.capture is not None:
            try:
                self.capture.release()
            except Exception:
                pass
        self.capture = None
        self.connected = False

    def read_rgb(self, size: tuple[int, int]):
        if not self.capture or not self.connected or not self._cv2:
            return None
        ok, frame = self.capture.read()
        if not ok:
            return None
        width, height = size
        frame = self._cv2.resize(frame, (width, height), interpolation=self._cv2.INTER_LINEAR)
        frame = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
        return frame


class MQTTTransport:
    def __init__(self, host: str = "127.0.0.1", port: int = 3333) -> None:
        self.host = host
        self.port = port
        self.connected = False
        self.messages_received = 0
        self.last_topic = ""
        self.last_error = ""
        self.client = None

    def start(self) -> None:
        try:
            import paho.mqtt.client as mqtt  # type: ignore

            try:
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            except Exception:
                self.client = mqtt.Client()
            self.client.on_connect = self._on_connect
            self.client.on_message = self._on_message
            self.client.connect_async(self.host, self.port, keepalive=30)
            self.client.loop_start()
        except Exception as exc:
            self.last_error = str(exc)
            self.connected = False

    def stop(self) -> None:
        if self.client is not None:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        self.connected = False

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None) -> None:
        self.connected = int(reason_code) == 0 if not isinstance(reason_code, str) else reason_code == "Success"
        if self.connected:
            client.subscribe("#", qos=1)

    def _on_message(self, _client, _userdata, msg) -> None:
        self.messages_received += 1
        self.last_topic = str(msg.topic)


@dataclass
class MqttPublishRecord:
    topic: str
    payload: bytes
    qos: int
    timestamp: float
    mock_output: bool


class OfficialMqttBridge(MQTTTransport):
    """RM2026 自定义客户端 protobuf topic 的 MQTT 边界。

    这个类不会自行生成 protobuf 类；它只负责记录或发布调用方已经
    序列化好的二进制载荷。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 3333, client_id: str = "3") -> None:
        super().__init__(host, port)
        self.client_id = client_id
        self.mock_output = True
        # Debug history must not grow for the duration of an event.  Indexing,
        # len(), and append() stay compatible with the former list API.
        self.publish_records: deque[MqttPublishRecord] = deque(maxlen=1024)
        self._received_messages: deque[tuple[str, bytes, float]] = deque(maxlen=512)
        self._received_lock = threading.Lock()
        self._command_publish_at: dict[str, float] = {}

    def _on_message(self, client, userdata, msg) -> None:
        super()._on_message(client, userdata, msg)
        with self._received_lock:
            self._received_messages.append((str(msg.topic), bytes(msg.payload), time.perf_counter()))

    def drain_received_messages(self) -> list[tuple[str, bytes, float]]:
        """在 UI 主线程读取 MQTT 回调缓存，避免回调线程直接修改界面状态。"""
        with self._received_lock:
            messages = list(self._received_messages)
            self._received_messages.clear()
        return messages

    def publish_binary(self, topic: str, payload: bytes, qos: int = 1) -> bool:
        record = MqttPublishRecord(topic=topic, payload=payload, qos=qos, timestamp=time.time(), mock_output=self.mock_output)
        self.publish_records.append(record)
        if self.mock_output:
            return True
        if self.client is None:
            self.last_error = "未连接 MQTT 客户端"
            return False
        try:
            self.client.publish(topic, payload, qos=qos)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def publish_match_command(self, topic: str, payload: bytes, *, qos: int = 1, now: float | None = None) -> bool:
        """Publish a referee command while enforcing the protocol's 10 Hz ceiling."""

        now = time.perf_counter() if now is None else float(now)
        previous = self._command_publish_at.get(topic, -1.0)
        if previous >= 0.0 and now - previous < 0.10:
            self.last_error = f"{topic} 发布频率超过 10Hz"
            return False
        accepted = self.publish_binary(topic, payload, qos=qos)
        if accepted:
            self._command_publish_at[topic] = now
        return accepted

    def publish_common_command(self, payload: bytes, *, now: float | None = None) -> bool:
        return self.publish_match_command("CommonCommand", payload, qos=1, now=now)

    def publish_air_support_command(self, payload: bytes, *, now: float | None = None) -> bool:
        return self.publish_match_command("AirSupportCommand", payload, qos=1, now=now)

    def publish_keyboard_mouse(self, payload: bytes) -> bool:
        return self.publish_binary("KeyboardMouseControl", payload, qos=1)

    def publish_custom_control(self, data: bytes) -> bool:
        if len(data) > 30:
            raise ValueError("CustomControl 载荷最多 30 字节")
        return self.publish_binary("CustomControl", data, qos=1)


class VideoPipeline:
    """HEVC UDP 重组与可选转发边界。"""

    def __init__(self) -> None:
        self.assembler = FrameAssembler()
        self.frames_received = 0
        self.last_frame = b""
        self.forward_target: tuple[str, int] | None = None
        self.last_error = ""

    def push_udp_packet(self, packet: bytes) -> bytes | None:
        frame = self.assembler.push_packet(packet)
        if frame is None:
            return None
        self.frames_received += 1
        self.last_frame = frame
        self.forward_frame(frame)
        return frame

    def set_forward_target(self, host: str | None, port: int | None) -> None:
        self.forward_target = (host, port) if host and port else None

    def forward_frame(self, frame: bytes) -> None:
        if self.forward_target is None:
            return
        host, port = self.forward_target
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(frame, (host, port))
        except OSError as exc:
            self.last_error = str(exc)


class SerialBridgeStub:
    def __init__(self) -> None:
        self.connected = False
        self.last_write = b""
        self.last_error = ""

    def start(self) -> None:
        self.connected = False

    def stop(self) -> None:
        self.connected = False

    def write(self, payload: bytes) -> None:
        self.last_write = payload
