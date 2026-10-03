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

from ..protocol import RefereeFrameCodec
from ..transports import SerialBridgeStub


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
        self._reader: threading.Thread | None = None
        self._running = False
        self._rx: deque[Any] = deque(maxlen=64)
        self._rx_lock = threading.Lock()

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        if self.port in ("", "Stub", "stub", "None", "none"):
            self.connected = False
            self.last_error = "未配置真实串口端口（Stub）"
            return
        try:
            import serial  # type: ignore
            self._serial = serial.Serial(
                port=self.port, baudrate=self.baud, timeout=0.1,
                write_timeout=0.5,
            )
            self.connected = bool(self._serial and self._serial.is_open)
            if not self.connected:
                self.last_error = "串口打开失败"
                return
            self._running = True
            self._reader = threading.Thread(target=self._read_loop, name="rm-serial", daemon=True)
            self._reader.start()
        except Exception as exc:  # pragma: no cover - depends on hardware
            self.connected = False
            self.last_error = str(exc)

    def stop(self) -> None:
        self._running = False
        # 第 94 轮：**顺序反了，而且丢掉了活着的线程句柄。**
        #
        # 原来是「先 close 串口 → 再 join 线程 → 再把句柄置 None」。
        # 读线程的循环是 `while self._running and self._serial is not None:`，
        # 所以 close 之后它**下一次解引用拿到的是 None** ⇒ `AttributeError`
        # ⇒ 被那个宽 `except Exception` 接住 ⇒ **用一句管道错误覆盖了真实的串口故障**。
        #
        # 子代理在**真类**上复现了：join 预算 602.7 ms 用尽后线程仍活着，
        # 而 `self._reader = None` 让下一次 `start()` 的 `is_alive()` 守卫失效，
        # **同一个 COM 口上出现两个 rm-serial 读线程**。
        #
        # 本仓里正确的顺序就在旁边：`polar_transport.py` 是 **先 join 再 close**，
        # 并且 join 之后**保留句柄**（`transports.py:279-282`、`vision.py:379-380`
        # 也都保留句柄，让 `start()` 能拒绝重复启动）。**这里两个都做反了。**
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=0.6)
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        # 句柄**保留**：join 超时的话线程可能还活着，丢掉句柄等于让下一次
        # `start()` 的 `is_alive()` 守卫看不见它。
        self._reader = reader
        self.connected = False

    # ------------------------------------------------------------------
    def write_frame(self, cmd_id: int, data: bytes = b"") -> tuple[bool, str]:
        if int(cmd_id) == 0x0311:
            self.last_error = (
                "0x0311 属于 921600 图传链路；客户端上行必须经 MQTT "
                "CustomControl，禁止写入 115200 裁判/下位机串口"
            )
            return False, self.last_error
        frame = self.codec.encode(cmd_id, data)
        return self.write(frame)

    def write(self, payload: bytes) -> tuple[bool, str]:
        return self._write_payload(payload, bypass_estop=False)

    def _write_payload(self, payload: bytes, *, bypass_estop: bool) -> tuple[bool, str]:
        """Write one frame; only the internally-built stop frame can bypass E-stop."""
        self.last_write = payload
        if self.mock_writes:
            return True, "mock"
        if self.estop_active and not bypass_estop:
            self.last_error = "急停激活，串口写入被禁止"
            return False, self.last_error
        if self._serial is None or not self.connected:
            self.last_error = "串口未连接"
            return False, self.last_error
        try:
            written = self._serial.write(payload)
            if written != len(payload):
                self.last_error = f"串口短写 {written}/{len(payload)} 字节"
                return False, self.last_error
            self.frames_written += 1
            self.last_write_at = time.perf_counter()
            return True, "ok"
        except Exception as exc:  # pragma: no cover
            self.last_error = str(exc)
            return False, str(exc)

    # ------------------------------------------------------------------
    # 心跳/急停
    # ------------------------------------------------------------------
    def tick(self, now: float | None = None) -> None:
        # 与 safety 双向同步急停状态（避免粘滞/矛盾）
        if self.safety is not None:
            self.estop_active = bool(self.safety.estop_active)

    def trigger_estop(self) -> None:
        self.estop_active = True

    def release_estop(self) -> None:
        self.estop_active = False

    def write_stop_command(self) -> tuple[bool, str]:
        """拒绝伪造串口 0x0311；调用方应使用 BackendManager.trigger_estop。"""
        self.last_error = "0x0311 属图传/MQTT CustomControl，禁止经 115200 串口发送"
        return False, self.last_error

    # ------------------------------------------------------------------
    def _read_loop(self) -> None:
        buffer = bytearray()
        try:
            while self._running and self._serial is not None:
                chunk = self._serial.read(256)
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
            self._running = False
            self.connected = False

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
