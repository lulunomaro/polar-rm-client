from __future__ import annotations

from dataclasses import dataclass
import json
import socket
import threading
import time
from typing import Any


PROTOCOL = "rm.vision.tongji.v1"
DEFAULT_PORT = 9871
# client → 外挂：ArmorAutoAim 开关（完整同济门控）
AA_STATE_PROTOCOL = "polar.aa.state.v1"
AA_STATE_PORT = 9872
ARMOR_NAMES = ("one", "two", "three", "four", "five", "sentry", "outpost", "base", "not_armor")


@dataclass(frozen=True)
class TongjiPacket:
    timestamp_ns: int
    frame_width: int
    frame_height: int
    mode: str
    primary: dict[str, Any] | None
    armors: tuple[dict[str, Any], ...]
    energy: dict[str, Any] | None
    gimbal: dict[str, Any]
    command: dict[str, Any]


def _armor(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    raw_points = payload.get("points", [])
    points: list[list[float]] = []
    if isinstance(raw_points, list):
        for point in raw_points[:4]:
            if isinstance(point, (tuple, list)) and len(point) >= 2:
                try:
                    points.append([float(point[0]), float(point[1])])
                except (TypeError, ValueError):
                    continue
    raw_box = payload.get("box", [])
    box: list[float] = []
    if isinstance(raw_box, (tuple, list)) and len(raw_box) >= 4:
        try:
            box = [float(value) for value in raw_box[:4]]
        except (TypeError, ValueError):
            box = []
    if len(points) == 4 and not box:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        box = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
    if len(points) != 4 and len(box) == 4:
        x, y, width, height = box
        points = [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]
    if len(points) != 4:
        return None
    raw_back = payload.get("back_points", [])
    back_points: list[list[float]] = []
    if isinstance(raw_back, list):
        for point in raw_back[:4]:
            if isinstance(point, (tuple, list)) and len(point) >= 2:
                try:
                    back_points.append([float(point[0]), float(point[1])])
                except (TypeError, ValueError):
                    continue
    return {
        "id": int(payload.get("id", -1)),
        "name": str(payload.get("name", payload.get("label", "armor"))),
        "color": str(payload.get("color", "unknown")),
        "confidence": max(0.0, min(1.0, float(payload.get("confidence", 0.0)))),
        "box": box,
        "points": points,
        "back_points": back_points,
        "locked": bool(payload.get("locked", False)),
        "predicted": bool(payload.get("predicted", False)),
        "aim": bool(payload.get("aim", False)),
        "distance_m": max(0.0, float(payload.get("distance_m", 0.0))),
        "depth_m": max(0.0, float(payload.get("depth_m", 0.0))),
        "yaw_rad": float(payload.get("yaw_rad", 0.0)),
        "plate_id": int(payload.get("plate_id", payload.get("id", -1))),
        "light_strength": max(0.0, min(1.0, float(payload.get("light_strength", 0.0)))),
        "light_state": str(payload.get("light_state", "unknown")),
    }


def parse_tongji_packet(data: bytes) -> TongjiPacket | None:
    """校验并标准化 UDP `rm.vision.tongji.v1` 包，坏包不会影响 UI 线程。"""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
        return None
    try:
        width = max(1, int(payload.get("frame_w", 1)))
        height = max(1, int(payload.get("frame_h", 1)))
    except (TypeError, ValueError):
        return None
    raw_armors = payload.get("armors", [])
    if not isinstance(raw_armors, list):
        raw_armors = []
    armors = tuple(item for item in (_armor(value) for value in raw_armors) if item is not None)
    primary = _armor(payload.get("primary"))
    if primary is None and armors:
        primary = next((armor for armor in armors if armor["locked"]), armors[0])
    energy = _armor(payload.get("energy"))
    gimbal = payload.get("gimbal", {})
    command = payload.get("command", {})
    return TongjiPacket(
        timestamp_ns=int(payload.get("timestamp_ns", time.time_ns())),
        frame_width=width,
        frame_height=height,
        mode=str(payload.get("mode", "auto_aim")),
        primary=primary,
        armors=armors,
        energy=energy,
        gimbal=gimbal if isinstance(gimbal, dict) else {},
        command=command if isinstance(command, dict) else {},
    )


def build_tongji_packet(
    frame_width: int,
    frame_height: int,
    armors: list[dict[str, Any]],
    *,
    primary: dict[str, Any] | None = None,
    mode: str = "auto_aim",
    gimbal: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
) -> bytes:
    """构造可被客户端和外部同济检测进程共用的 UDP 包。"""
    payload = {
        "protocol": PROTOCOL,
        "timestamp_ns": time.time_ns(),
        "frame_w": int(frame_width),
        "frame_h": int(frame_height),
        "mode": mode,
        "primary": primary,
        "armors": armors,
        "energy": None,
        "gimbal": gimbal or {},
        "command": command or {"control": False, "fire": False},
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class TongjiVisionReceiver:
    """后台 UDP 接收器，只发布最新一包，防止视觉结果积压。"""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self.running = False
        self.connected = False
        self.last_error = ""
        self.last_received_ns = 0
        self._latest: TongjiPacket | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    def configure(self, port: int) -> None:
        port = int(port)
        if self.port != port and self.running:
            self.stop()
        self.port = port

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, name="polar-tongji-udp", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.6)
        self._thread = None
        self._socket = None
        self.connected = False

    def latest(self, timeout_ms: float) -> TongjiPacket | None:
        with self._lock:
            packet = self._latest
        if packet is None or time.time_ns() - self.last_received_ns > int(max(1.0, timeout_ms) * 1e6):
            self.connected = False
            return None
        return packet

    def _run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.port))
            sock.settimeout(0.20)
            self._socket = sock
            while self.running:
                try:
                    data, _address = sock.recvfrom(512 * 1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                packet = parse_tongji_packet(data)
                if packet is None:
                    continue
                with self._lock:
                    self._latest = packet
                self.last_received_ns = time.time_ns()
                self.connected = True
        except OSError as error:
            self.last_error = str(error)
            self.connected = False
        finally:
            if self._socket is not None:
                try:
                    self._socket.close()
                except OSError:
                    pass
            self._socket = None


class PolarPublisher:
    """供桌面 runner 或队内检测器使用的 UDP 发布器。"""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self.target = (host, int(port))
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def publish(self, payload: bytes) -> None:
        self._socket.sendto(payload, self.target)

    def close(self) -> None:
        self._socket.close()


class GimbalCommandBridge:
    """缓存同济建议角/像素误差；真机串口不在此发。

    Lab（Gestalt）：client 可在 ArmorAutoAim 开启时把误差转为鼠标注入。
    """

    def __init__(self) -> None:
        self.last_command: dict[str, Any] = {
            "control": False,
            "fire": False,
            "yaw_rad": 0.0,
            "pitch_rad": 0.0,
            "err_x": 0.0,
            "err_y": 0.0,
        }
        self.last_received_ns = 0

    def cache(self, command: dict[str, Any]) -> None:
        raw = dict(command) if isinstance(command, dict) else {}
        self.last_command = {
            "control": bool(raw.get("control", False)),
            # 真机 fire 仍禁止从 client 下发；Lab 只跟视角
            "fire": False,
            "yaw_rad": float(raw.get("yaw_rad", 0.0) or 0.0),
            "pitch_rad": float(raw.get("pitch_rad", 0.0) or 0.0),
            "err_x": float(raw.get("err_x", 0.0) or 0.0),
            "err_y": float(raw.get("err_y", 0.0) or 0.0),
            "lab_aim": bool(raw.get("lab_aim", raw.get("control", False))),
        }
        self.last_received_ns = time.time_ns()

    def lab_aim_delta(self) -> tuple[float, float] | None:
        """返回 Lab 跟枪用的归一化误差 (err_x, err_y)，无则 None。"""
        cmd = self.last_command
        if not cmd.get("lab_aim") and not cmd.get("control"):
            return None
        err_x = float(cmd.get("err_x", 0.0) or 0.0)
        err_y = float(cmd.get("err_y", 0.0) or 0.0)
        # 也可用 yaw/pitch 近似为误差
        if abs(err_x) < 1e-9 and abs(err_y) < 1e-9:
            err_x = float(cmd.get("yaw_rad", 0.0) or 0.0)
            err_y = float(cmd.get("pitch_rad", 0.0) or 0.0)
        if abs(err_x) < 1e-9 and abs(err_y) < 1e-9:
            return None
        return err_x, err_y


class ArmorAutoAimStatePublisher:
    """向完整同济外挂广播 client ArmorAutoAim 开关。"""

    def __init__(self, host: str = "127.0.0.1", port: int = AA_STATE_PORT) -> None:
        self.target = (host, int(port))
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._last_send = 0.0
        self.enabled = False

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.publish(force=True)

    def publish(self, *, force: bool = False) -> None:
        now = time.perf_counter()
        if not force and now - self._last_send < 0.1:
            return
        self._last_send = now
        payload = {
            "protocol": AA_STATE_PROTOCOL,
            "t": time.time(),
            "armor_auto_aim": bool(self.enabled),
        }
        try:
            self._socket.sendto(json.dumps(payload, separators=(",", ":")).encode("utf-8"), self.target)
        except OSError:
            pass

    def close(self) -> None:
        try:
            self._socket.close()
        except OSError:
            pass


class ArmorAutoAimStateReceiver:
    """外挂侧：收 client 的 AA 开关（最新值）。"""

    def __init__(self, host: str = "0.0.0.0", port: int = AA_STATE_PORT) -> None:
        self.host = host
        self.port = int(port)
        self.armor_auto_aim = False
        self.last_at = 0.0
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="polar-aa-state", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def fresh(self, timeout_s: float = 1.0) -> bool:
        return self.last_at > 0 and (time.perf_counter() - self.last_at) <= timeout_s

    def _run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.settimeout(0.25)
            self._socket = sock
        except OSError:
            self._running = False
            return
        while self._running:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("protocol") != AA_STATE_PROTOCOL:
                continue
            with self._lock:
                self.armor_auto_aim = bool(payload.get("armor_auto_aim", False))
                self.last_at = time.perf_counter()
