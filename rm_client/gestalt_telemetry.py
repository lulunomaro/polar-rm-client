"""接收 Workshop HUD 继电器发出的 polar.gestalt.attrs.v1 UDP JSON。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import socket
import threading
import time
from typing import Any

from .gestalt_attrs import DEFAULT_RELAY_PORT, PROTOCOL, apply_gestalt_attrs_to_state


@dataclass
class GestaltAttrReceiver:
    host: str = "127.0.0.1"
    port: int = DEFAULT_RELAY_PORT
    running: bool = False
    packets: int = 0
    last_error: str = ""
    last_payload: dict[str, Any] | None = None
    last_at: float = 0.0
    _thread: threading.Thread | None = None
    _socket: socket.socket | None = None
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def configure(self, port: int) -> None:
        port = int(port)
        if port == self.port and self.running:
            return
        was = self.running
        if was:
            self.stop()
        self.port = port
        if was:
            self.start()

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, name="gestalt-attr-udp", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None

    def _run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.settimeout(0.25)
            self._socket = sock
        except OSError as exc:
            self.last_error = str(exc)
            self.running = False
            return
        while self.running:
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if self.running:
                    self.last_error = str(exc)
                break
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.last_error = f"bad json: {exc}"
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("protocol") not in (PROTOCOL, "polar.gestalt.attrs.v1"):
                continue
            with self._lock:
                self.last_payload = payload
                self.last_at = time.perf_counter()
                self.packets += 1
                self.last_error = ""

    def apply_latest(self, state: Any) -> bool:
        with self._lock:
            payload = self.last_payload
            at = self.last_at
            packets = self.packets
            err = self.last_error
        if payload is None:
            return False
        # 略放宽：桥 20Hz 偶发卡顿时避免立刻掉回 sin 假热量
        if time.perf_counter() - at > 3.5:
            return False
        written = apply_gestalt_attrs_to_state(state, payload)
        state.mark_gestalt_telemetry()
        # 便于 HUD/调试：包计数挂到 state（若有字段）
        if hasattr(state, "gestalt_attr_packets"):
            state.gestalt_attr_packets = packets
        if hasattr(state, "gestalt_attr_error") and err:
            state.gestalt_attr_error = err
        return bool(written) or True
