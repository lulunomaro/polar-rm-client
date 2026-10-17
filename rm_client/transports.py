from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import socket
import threading
import time
from typing import Callable

from .backend.resource_close import close_resource
from .backend.video_source import VideoStopStatus


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
        self.last_completed_frame_id: int | None = None
        self.stale_packet_count = 0
        self.sequence_discontinuities = 0
        self._last_completed_at = 0.0

    def clear(self) -> None:
        self.frames.clear()
        self.buffered_bytes = 0
        self.dropped_count = 0
        self.invalid_count = 0
        self.last_completed_frame_id = None
        self.stale_packet_count = 0
        self.sequence_discontinuities = 0
        self._last_completed_at = 0.0

    def push_packet(self, packet: bytes) -> bytes | None:
        if len(packet) < self.HEADER_BYTES:
            self.invalid_count += 1
            return None
        frame_id = int.from_bytes(packet[0:2], "big")
        chunk_index = int.from_bytes(packet[2:4], "big")
        total_bytes = int.from_bytes(packet[4:8], "big")
        payload = packet[self.HEADER_BYTES:]
        self._drop_old_frames()
        previous = self.last_completed_frame_id
        if previous is not None and time.monotonic() - self._last_completed_at > self.STALE_SECONDS:
            # A sender may restart its uint16 counter without restarting our
            # socket. After a bounded no-progress interval begin a new epoch,
            # dropping partial frames and forcing video keyframe recovery.
            for old_id in list(self.frames):
                self._remove_frame(old_id, dropped=True)
            self.last_completed_frame_id = previous = None
            self.sequence_discontinuities += 1
        if previous is not None and not 0 < ((frame_id - previous) & 0xFFFF) < 0x8000:
            # Completion order is not stream order. Never feed an older frame
            # (or a completed duplicate) after a newer one. Half-range serial
            # arithmetic also handles the 65535 -> 0 wrap unambiguously.
            self.stale_packet_count += 1
            self._remove_frame(frame_id, dropped=True)
            return None
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
                previous = self.last_completed_frame_id
                if previous is not None and ((frame_id - previous) & 0xFFFF) != 1:
                    self.sequence_discontinuities += 1
                self.last_completed_frame_id = frame_id
                self._last_completed_at = time.monotonic()
                for old_id in list(self.frames):
                    if not 0 < ((old_id - frame_id) & 0xFFFF) < 0x8000:
                        self._remove_frame(old_id, dropped=True)
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
class _UDPVideoSession:
    assembler: FrameAssembler = field(default_factory=FrameAssembler)
    stopped: threading.Event = field(default_factory=threading.Event)
    sock: socket.socket | None = None
    thread: threading.Thread | None = None
    close_lock: threading.Lock = field(default_factory=threading.Lock)
    close_error: str = ""


@dataclass
class UDPVideoReceiver:
    host: str = "127.0.0.1"
    port: int = 3334
    on_frame: Callable[[bytes], None] | None = None
    running: bool = False
    packets_received: int = 0
    frames_received: int = 0
    last_error: str = ""
    _thread: threading.Thread | None = field(default=None, init=False)
    _socket: socket.socket | None = field(default=None, init=False)
    _assembler: FrameAssembler = field(default_factory=FrameAssembler, init=False)
    _receiver_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _session: _UDPVideoSession | None = field(default=None, init=False, repr=False)
    _stop_worker: threading.Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        with self._receiver_lock:
            if (self.running or self._session is not None or self._stop_in_progress_locked()
                    or (self._thread is not None and self._thread.is_alive())):
                return
            # Socket, frame-ID epoch and stop signal are never reused by a
            # replacement receive worker, even if an old callback is delayed.
            session = _UDPVideoSession()
            self._session = session
            self._assembler = session.assembler
            self.running = True
            self.last_error = ""
            try:
                session.thread = threading.Thread(
                    target=self._run, args=(session, self.host, self.port, self.on_frame),
                    name="udp-video", daemon=True,
                )
                self._thread = session.thread
                session.thread.start()
            except Exception as exc:
                self.running = False
                session.stopped.set()
                self.last_error = f"UDP receiver admission failed: {type(exc).__name__}: {exc}"
                # A late-raising start wrapper may already have admitted a
                # receiver creating its socket. Keep that session and thread
                # until strict close observes its late native ownership.
                if session.thread is None or session.thread.ident is None:
                    self._session = self._thread = None
                raise

    def _stop_in_progress_locked(self) -> bool:
        return self._stop_worker is not None and self._stop_worker.is_alive()

    def request_stop(self) -> None:
        """Invalidate the receive epoch and close only its captured owner off-thread."""
        with self._receiver_lock:
            session = self._session
            self.running = False
            if session is None:
                return
            session.stopped.set()
            if self._stop_in_progress_locked():
                return
            worker = threading.Thread(target=self._finish_requested_stop, args=(session,),
                                      name="udp-video-stop", daemon=True)
            self._stop_worker = worker
            try:
                worker.start()
            except Exception as exc:
                if worker.ident is None:
                    self._stop_worker = None
                self.last_error = f"UDP stop worker failed: {type(exc).__name__}: {exc}"
                raise

    def _finish_requested_stop(self, session: _UDPVideoSession) -> None:
        self._close_session_socket(session)
        if session.thread is not None and session.thread is not threading.current_thread():
            session.thread.join()
        # A socket created after our first close attempt is still attached to
        # this session. The receiver's finally path closes it or retains its
        # failed ownership; no later receiver can be replaced by this worker.
        self._release_closed_session(session)

    def stop(self) -> None:
        with self._receiver_lock:
            session = self._session
            self.running = False
            if session is not None:
                session.stopped.set()
        if session is None:
            return
        self._close_session_socket(session)
        if session.thread is not None and session.thread is not threading.current_thread():
            session.thread.join(timeout=0.6)
        self._release_closed_session(session)

    @property
    def stop_pending(self) -> bool:
        with self._receiver_lock:
            return (self._stop_in_progress_locked()
                    or (self._session is not None and self._session.stopped.is_set()))

    @property
    def stop_status(self) -> VideoStopStatus:
        with self._receiver_lock:
            session = self._session
            stopping = self._stop_in_progress_locked()
            retired = session is not None and session.stopped.is_set()
            # Never take close_lock here: a native close can be blocked while
            # holding it and needs receiver_lock to publish its result.
            # A blocked receiver may need socket.close() from a newly
            # admitted stopper. The data thread alone is not a cleanup attempt.
            inflight = stopping or bool(retired and session.close_lock.locked())
            errors = (session.close_error,) if retired and session.close_error else ()
            return VideoStopStatus(bool(retired or stopping), inflight, errors)

    @property
    def cleanup_complete(self) -> bool:
        with self._receiver_lock:
            return (self._session is None and self._socket is None
                    and not self._stop_in_progress_locked()
                    and (self._thread is None or not self._thread.is_alive()))

    def wait_closed(self, timeout: float | None = None) -> bool:
        """Worker/CLI only: finish an existing owner, retrying failed close."""
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._receiver_lock:
            session, worker, stopper = self._session, self._thread, self._stop_worker
        for owner in (stopper, worker):
            if owner is not None and owner is not threading.current_thread():
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                owner.join(remaining)
            if owner is not None and owner.is_alive():
                return False
        if session is not None:
            self._close_session_socket(session)
            self._release_closed_session(session)
        return self.cleanup_complete

    def _close_session_socket(self, session: _UDPVideoSession) -> None:
        with session.close_lock:
            sock = session.sock
            if sock is None:
                return
            try:
                close_resource(sock)
            except Exception as exc:
                session.close_error = f"UDP socket close unconfirmed: {type(exc).__name__}: {exc}"
                with self._receiver_lock:
                    self.last_error = session.close_error
                return
            session.sock = None
            session.close_error = ""
            with self._receiver_lock:
                if self._session is session:
                    self._socket = None

    def _release_closed_session(self, session: _UDPVideoSession) -> None:
        worker = session.thread
        if session.sock is not None or (worker is not None and worker.is_alive()
                                       and worker is not threading.current_thread()):
            return
        with self._receiver_lock:
            if self._session is session:
                self._session = None
                self._socket = None
                if worker is None or not worker.is_alive():
                    self._thread = None

    def _run(self, session: _UDPVideoSession, host: str, port: int,
             on_frame: Callable[[bytes], None] | None) -> None:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Publish before bind so stop also closes a socket still starting.
            with self._receiver_lock:
                session.sock = sock
                if self._session is not session or session.stopped.is_set():
                    return
                self._socket = sock
            sock.bind((host, port))
            sock.settimeout(0.2)
            while not session.stopped.is_set():
                try:
                    packet, _addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                with self._receiver_lock:
                    if self._session is not session or session.stopped.is_set():
                        break
                    self.packets_received += 1
                    frame = session.assembler.push_packet(packet)
                    if frame is not None:
                        self.frames_received += 1
                if frame is not None and on_frame is not None and not session.stopped.is_set():
                    on_frame(frame)
        except OSError as exc:
            with self._receiver_lock:
                if self._session is session and not session.stopped.is_set():
                    self.last_error = str(exc)
        finally:
            # A late worker owns only its captured socket. It cannot close or
            # mark unavailable a newly started receiver.
            session.stopped.set()
            self._close_session_socket(session)
            with self._receiver_lock:
                if self._session is session:
                    self.running = False
            self._release_closed_session(session)


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
