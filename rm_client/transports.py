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

    def __init__(self) -> None:
        self.frames: dict[int, dict[str, object]] = {}

    def push_packet(self, packet: bytes) -> bytes | None:
        if len(packet) < 8:
            return None
        frame_id = int.from_bytes(packet[0:2], "big")
        chunk_index = int.from_bytes(packet[2:4], "big")
        total_bytes = int.from_bytes(packet[4:8], "big")
        payload = packet[8:]
        entry = self.frames.setdefault(
            frame_id,
            {"total": total_bytes, "chunks": {}, "received": 0, "created": time.time()},
        )
        chunks: dict[int, bytes] = entry["chunks"]  # type: ignore[assignment]
        if chunk_index not in chunks:
            chunks[chunk_index] = payload
            entry["received"] = int(entry["received"]) + len(payload)
        if int(entry["received"]) >= total_bytes:
            ordered = [chunks[idx] for idx in sorted(chunks)]
            complete = b"".join(ordered)
            if len(complete) >= total_bytes:
                self.frames.pop(frame_id, None)
                return complete[:total_bytes]
        self._drop_old_frames()
        return None

    def _drop_old_frames(self) -> None:
        now = time.time()
        stale = [
            frame_id
            for frame_id, entry in self.frames.items()
            if now - float(entry.get("created", now)) > 2.0
        ]
        for frame_id in stale:
            self.frames.pop(frame_id, None)


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

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, name="udp-video", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def _run(self) -> None:
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.bind((self.host, self.port))
            self._socket.settimeout(0.2)
        except OSError as exc:
            self.last_error = str(exc)
            self.running = False
            return
        while self.running:
            try:
                packet, _addr = self._socket.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if self.running:
                    self.last_error = str(exc)
                break
            self.packets_received += 1
            frame = self._assembler.push_packet(packet)
            if frame is not None:
                self.frames_received += 1
                if self.on_frame:
                    self.on_frame(frame)


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
        self.publish_records: list[MqttPublishRecord] = []
        self._received_messages: deque[tuple[str, bytes, float]] = deque(maxlen=512)
        self._received_lock = threading.Lock()

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

    def publish_binary(self, topic: str, payload: bytes, qos: int = 1) -> None:
        record = MqttPublishRecord(topic=topic, payload=payload, qos=qos, timestamp=time.time(), mock_output=self.mock_output)
        self.publish_records.append(record)
        if self.mock_output:
            return
        if self.client is None:
            self.last_error = "未连接 MQTT 客户端"
            return
        try:
            self.client.publish(topic, payload, qos=qos)
        except Exception as exc:
            self.last_error = str(exc)

    def publish_keyboard_mouse(self, payload: bytes) -> None:
        self.publish_binary("KeyboardMouseControl", payload, qos=1)

    def publish_custom_control(self, data: bytes) -> None:
        if len(data) > 30:
            raise ValueError("CustomControl 载荷最多 30 字节")
        self.publish_binary("CustomControl", data, qos=1)


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
