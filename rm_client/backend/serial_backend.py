"""裁判/队内串口后端。

- PySerial 真实串口 + 官方 0xA5 帧编解码（RefereeFrameCodec）
- Mock Writes 模式只记录不写
- 不承载 0x0311：该命令字属于 921600 图传链路，CustomControl 由 MQTT 发布
- 急停只锁住本地串口写入；机器人归零由 KeyboardMouseControl MQTT 负责
- 后台读线程解析下位机回包
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from ..protocol import MAX_REFEREE_PAYLOAD, RefereeFrameCodec, crc8, crc16
from ..transports import SerialBridgeStub
from .resource_close import close_resource


class RmSerialBackend(SerialBridgeStub):
    """SharkClient 风格串口后端；端口为 Stub 时退化为 stub。"""

    def __init__(
        self,
        port: str = "Stub",
        baud: int = 115200,
        mock_writes: bool = True,
        safety: Any = None,
    ) -> None:
        super().__init__()
        self.port = port
        self.baud = baud
        self.mock_writes = bool(mock_writes)
        self.safety = safety
        self.codec = RefereeFrameCodec()
        self.frames_written = 0
        self.frames_read = 0
        self.last_write_at = 0.0
        self.last_frame_at = 0.0
        self.estop_active = False
        self._serial = None
        self._close_error = ""
        self._reader: threading.Thread | None = None
        self._running = False
        self._rx: deque[Any] = deque(maxlen=64)
        self._rx_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._estop_guard = threading.Lock()
        self._estop_generation = 0

    # ------------------------------------------------------------------
    def start(self) -> None:
        with self._lifecycle_lock:
            if (self._running or self._serial is not None
                    or (self._reader is not None and self._reader.is_alive())):
                return
            if str(self.port).strip().lower() in ("", "stub", "none"):
                self.connected = False
                self.last_error = "未配置真实串口端口（Stub）"
                return
            stream = None
            try:
                import serial  # type: ignore
                stream = serial.Serial(port=self.port, baudrate=self.baud,
                                       timeout=0.1, write_timeout=0.2)
                if not getattr(stream, "is_open", False):
                    raise ConnectionError("串口打开失败")
                with self._rx_lock:
                    self._rx.clear()
                self.last_frame_at = 0.0
                self._serial = stream
                self.connected = True
                self.last_error = ""
                self._running = True
                self._reader = threading.Thread(target=self._read_loop, name="rm-serial", daemon=True)
                self._reader.start()
            except Exception as exc:
                # Only a never-admitted reader is unjoinable. Keep real
                # started/finished owners for the normal strict close path.
                if self._reader is not None and self._reader.ident is None:
                    self._reader = None
                self.connected = self._running = False
                self.last_error = str(exc)
                self._serial = stream
                self._disconnect_locked()

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._write_lock:
                self._running = False
                self._disconnect_locked()
            reader = self._reader
            if reader is not None and reader.is_alive() and reader is not threading.current_thread():
                reader.join(timeout=0.6)
            # A timed-out reader still owns its epoch. Never open a replacement
            # until it has actually exited.
            if reader is None or not reader.is_alive():
                self._reader = None
            with self._rx_lock:
                self._rx.clear()
            self.last_frame_at = 0.0

    @property
    def cleanup_complete(self) -> bool:
        return (self._serial is None and not self._running
                and (self._reader is None or not self._reader.is_alive()))

    def _disconnect_locked(self) -> None:
        stream = self._serial
        self.connected = False
        self._running = False
        if stream is not None:
            try:
                close_resource(stream)
            except Exception as exc:
                # Keep the ambiguous owner unavailable for writes/replacement.
                # A later explicit stop may retry this exact handle.
                self._close_error = f"Serial close unconfirmed: {type(exc).__name__}: {exc}"
                self.last_error = self._close_error
                return
            self._serial = None
        self._close_error = ""

    # ------------------------------------------------------------------
    def write_frame(self, cmd_id: int, data: bytes = b"") -> tuple[bool, str]:
        if not isinstance(cmd_id, int) or isinstance(cmd_id, bool) or not 0 <= cmd_id <= 0xFFFF:
            self.last_error = "cmd_id 必须是 uint16 整数"
            return False, self.last_error
        if cmd_id in (0x0310, 0x0311):
            self.last_error = (
                "0x0310/0x0311 属于 921600 图传链路；客户端上行必须经 MQTT "
                "CustomControl，禁止写入 115200 裁判/下位机串口"
            )
            return False, self.last_error
        with self._write_lock:
            try:
                if not isinstance(data, (bytes, bytearray, memoryview)):
                    raise ValueError("data 必须是字节载荷")
                frame = self.codec.encode(cmd_id, data)
            except (TypeError, ValueError, OverflowError) as exc:
                self.last_error = str(exc)
                return False, self.last_error
            return self.write(frame)

    def write(self, payload: bytes) -> tuple[bool, str]:
        return self._write_payload(payload, bypass_estop=False)

    def _write_payload(self, payload: bytes, *, bypass_estop: bool) -> tuple[bool, str]:
        # Every public/raw path must enforce the same route boundary. Reject
        # incomplete/junk prefixes rather than allowing a decoder to skip them.
        with self._write_lock:
            try:
                payload = bytes(payload)
                offset = 0
                if not payload:
                    raise ValueError("串口帧为空")
                while offset < len(payload):
                    header = payload[offset:offset + 5]
                    if len(header) != 5 or header[0] != 0xA5 or crc8(header[:4]) != header[4]:
                        raise ValueError("串口帧头无效")
                    length = int.from_bytes(header[1:3], "little") + 9
                    if length > MAX_REFEREE_PAYLOAD + 9:
                        raise ValueError("串口载荷过长")
                    frame = payload[offset:offset + length]
                    if len(frame) != length or crc16(frame[:-2]) != int.from_bytes(frame[-2:], "little"):
                        raise ValueError("串口帧不完整或 CRC 无效")
                    if int.from_bytes(frame[5:7], "little") in (0x0310, 0x0311):
                        raise ValueError("0x0310/0x0311 属图传链路，禁止经 115200 串口发送")
                    offset += length
            except (TypeError, ValueError, OverflowError) as exc:
                self.last_error = str(exc)
                return False, self.last_error
            self.last_write = payload
            # No caller, including the legacy private argument, can bypass E-stop.
            if self.estop_active:
                self.last_error = "急停激活，串口写入被禁止"
                return False, self.last_error
            if self.mock_writes:
                return True, "mock"
            if self._serial is None or not self.connected:
                self.last_error = "串口未连接"
                return False, self.last_error
            try:
                written = self._serial.write(payload)
                if written != len(payload):
                    raise IOError(f"串口短写 {written}/{len(payload)} 字节")
                self.frames_written += 1
                self.last_write_at = time.perf_counter()
                return True, "ok"
            except Exception as exc:
                self.last_error = str(exc)
                self._disconnect_locked()
                return False, self.last_error

    # ------------------------------------------------------------------
    # 心跳/急停
    # ------------------------------------------------------------------
    def tick(self, now: float | None = None) -> None:
        # Revocation must not wait for a driver-held write lock. Restoring a
        # permissive local latch can wait until a later tick; never block UI.
        if self.safety is None:
            return
        with self._estop_guard:
            generation = self._estop_generation
        if self.safety.estop_active:
            self.request_estop()
            return
        if self._write_lock.acquire(blocking=False):
            try:
                desired = bool(self.safety.estop_active)
                with self._estop_guard:
                    if generation == self._estop_generation:
                        self.estop_active = desired
            finally:
                self._write_lock.release()

    def request_estop(self) -> None:
        """Revoke subsequent writes without waiting for a driver-held lock.

        Bytes already inside the driver cannot be recalled. Every later
        admission checks this latch again under the write lock.
        """
        # This metadata guard never encloses a driver wait or safety read.
        # Pairing generation/latch prevents a stale UI restoration from
        # clearing a request that arrived after its safety snapshot.
        with self._estop_guard:
            self._estop_generation += 1
            self.estop_active = True

    def trigger_estop(self) -> None:
        with self._write_lock:
            self.request_estop()

    def release_estop(self) -> None:
        with self._write_lock:
            with self._estop_guard:
                self._estop_generation += 1
                self.estop_active = False

    def write_stop_command(self) -> tuple[bool, str]:
        """拒绝伪造串口 0x0311；调用方应使用 BackendManager.trigger_estop。"""
        self.last_error = "0x0311 属图传/MQTT CustomControl，禁止经 115200 串口发送"
        return False, self.last_error

    # ------------------------------------------------------------------
    def _read_loop(self) -> None:
        buffer = bytearray()
        stream = self._serial
        try:
            while self._running and stream is not None and self._serial is stream:
                chunk = stream.read(256)
                if not self._running or self._serial is not stream:
                    break
                if not getattr(stream, "is_open", True):
                    raise ConnectionError("串口已断开")
                if not chunk:
                    continue
                buffer.extend(chunk)
                frames, consumed = self.codec.decode_with_consumed(bytes(buffer))
                if consumed > 0:
                    del buffer[:consumed]  # 保留半帧，避免分帧数据丢失
                if frames:
                    with self._rx_lock:
                        for frame in frames:
                            self._rx.append(frame)
                            self.frames_read += 1
                            self.last_frame_at = time.perf_counter()
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.last_error = str(exc)
        finally:
            with self._write_lock:
                if self._serial is stream:
                    self._disconnect_locked()
            with self._rx_lock:
                self._rx.clear()
            self.last_frame_at = 0.0

    def drain_frames(self) -> list[Any]:
        with self._rx_lock:
            frames = list(self._rx)
            self._rx.clear()
        return frames

    def stats(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "port": self.port,
            "baud": self.baud,
            "mock_writes": self.mock_writes,
            "frames_written": self.frames_written,
            "frames_read": self.frames_read,
            "last_write_at": self.last_write_at,
            "estop_active": self.estop_active,
            "last_error": self.last_error,
        }
