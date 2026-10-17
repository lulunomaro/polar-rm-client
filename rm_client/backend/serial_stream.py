# -*- coding: utf-8 -*-
"""串口 / TCP 两种字节流的统一入口。

为什么要有这一层：队内那条 0x5A 链路的编解码与传输本来就是分开的
（polar_link 只认 bytes 进 bytes 出），但 polar_transport 直接把
serial.Serial 写在 4 个地方，于是"想无线"就得改协议层——白担心的事。
现在只有这一层负责"字节从哪儿来到哪儿去"，协议层一个字都不用动。

安全上刻意保守：TCP 断开后不再向串口写任何字节，让固件自己的 150ms
新鲜度看门狗撤销键位（usb_task.c:1383-1392），而不是我们最后塞一帧过去赌运气。
"""
from __future__ import annotations

import socket
import select
import threading
from typing import Any

from .resource_close import close_resource


class StreamOpenError(RuntimeError):
    """A failed open still owns a native resource requiring explicit cleanup."""

    def __init__(self, owner: Any, cause: Exception) -> None:
        super().__init__(str(cause))
        self.resource_owner = owner


class TcpByteStream:
    """长得像 pyserial 对象的 TCP 字节流（polar_transport 只用到这几个成员）。"""

    def __init__(self, host: str, port: int, timeout_s: float = 0.02,
                 connect_timeout_s: float = 1.5) -> None:
        self.port = "tcp://{0}:{1}".format(host, port)
        self.host = str(host)
        self.tcp_port = int(port)
        self.timeout = float(timeout_s)
        self.connect_timeout = float(connect_timeout_s)
        self._sock = None
        self.is_open = False
        self._close_lock = threading.Lock()

    def open(self) -> None:
        if self.is_open and self._sock is not None:
            return
        self.close()
        sock = socket.create_connection((self.host, self.tcp_port),
                                        timeout=self.connect_timeout)
        self._sock = sock
        try:
            sock.settimeout(self.timeout)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        except Exception:
            self.close()
            raise
        self.is_open = True

    def read(self, size: int = 512) -> bytes:
        sock = self._sock
        if sock is None or not self.is_open:
            raise ConnectionError("TCP 未连接")
        if int(size) <= 0:
            return b""
        try:
            # RX shares a thread with the control deadline. Never wait for
            # telemetry before allowing a TX/deadman/neutral check.
            if not select.select([sock], [], [], 0)[0]:
                return b""
            data = sock.recv(int(size))
            if not data:
                raise ConnectionError("TCP 对端已关闭连接")
            return data
        except (socket.timeout, BlockingIOError):
            return b""
        except OSError:
            self.close()
            raise

    def write(self, data: bytes) -> int:
        sock = self._sock
        if sock is None or not self.is_open:
            raise RuntimeError("TCP 未连接")
        payload = bytes(data)
        try:
            sock.sendall(payload)
        except OSError as exc:
            self.close()
            raise RuntimeError("TCP 写入失败（链路已断）：{0}".format(exc)) from exc
        return len(payload)

    def close(self) -> None:
        with self._close_lock:
            sock = self._sock
            self.is_open = False
            if sock is None:
                return
            # ENOTCONN from shutdown is benign; native close failure is not.
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            close_resource(sock)
            self._sock = None


def open_serial(port: str, baud: int) -> Any:
    import serial                                  # 延迟导入：没硬件也能 import 本模块
    # RX and safety deadlines share one thread. Poll rather than filling a
    # 512-byte read, and fail closed if a write consumes a whole 50 Hz tick.
    return serial.Serial(port=port, baudrate=int(baud), timeout=0, write_timeout=0.02)


def open_stream(transport: str, *, port: str = "", baud: int = 115200,
                host: str = "", tcp_port: int = 50570, serial_opener=None) -> Any:
    """按模式打开一条字节流；两种模式返回的对象接口一致。

    serial_opener 只给测试用（注入假串口），生产路径走 pyserial。
    """
    mode = str(transport or "serial").strip().lower()
    if mode in ("tcp", "wifi", "bridge"):
        target = str(host or "").strip()
        if not target:
            raise RuntimeError("TCP 模式要填桥接主机地址（车上小电脑的 IP）")
        stream = TcpByteStream(target, int(tcp_port))
        try:
            stream.open()
        except Exception as exc:
            if stream._sock is not None:
                raise StreamOpenError(stream, exc) from exc
            raise
        return stream
    if mode != "serial":
        raise ValueError("未知字节流模式：{0}".format(mode))
    if str(port or "").strip().lower() in ("", "stub", "none"):
        raise RuntimeError("没配置真实机器人串口（Port 还是 Stub）")
    if serial_opener is not None:
        return serial_opener(str(port).strip(), int(baud))
    return open_serial(str(port).strip(), int(baud))
