from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
import json
import math
import socket
import threading
import time
from typing import Any


PROTOCOL = "rm.vision.tongji.v1"
DEFAULT_PORT = 9871
ARMOR_NAMES = ("one", "two", "three", "four", "five", "sentry", "outpost", "base", "not_armor")
MAX_FRAME_DIMENSION = 16_384


def _finite_float(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


def _bounded_text(value: Any, fallback: str, limit: int = 96) -> str:
    return str(fallback if value is None else value)[:limit]


def _fail_closed_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return False


def _safe_mapping(value: Any, *, max_items: int = 32) -> dict[str, Any]:
    """Copy a small JSON object without retaining unbounded diagnostic maps."""

    if not isinstance(value, dict):
        return {}
    return {
        _bounded_text(key, "", 64): item
        for key, item in islice(value.items(), max(0, int(max_items)))
    }


def _safe_pose_mapping(value: Any) -> dict[str, Any]:
    """Normalize the only pose fields consumed by the UI.

    A malformed ``yaw_rad``/``pitch_rad`` used to survive the UDP parser and
    fail later when the UI converted it with ``float()``.  Drop that one field
    while retaining bounded, non-control diagnostics from the same packet.
    """

    result = _safe_mapping(value)
    for key in ("yaw_rad", "pitch_rad"):
        if key not in result:
            continue
        try:
            result[key] = _finite_float(result[key])
        except (TypeError, ValueError, OverflowError):
            result.pop(key, None)
    for key in ("calibrated", "control", "fire"):
        if key in result:
            # These flags gate control-capable vision output. JSON strings such
            # as "false" must not become truthy through Python's bool().
            result[key] = _fail_closed_bool(result[key])
    return result


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
                    points.append([_finite_float(point[0]), _finite_float(point[1])])
                except (TypeError, ValueError, OverflowError):
                    continue
    raw_box = payload.get("box", [])
    box: list[float] = []
    if isinstance(raw_box, (tuple, list)) and len(raw_box) >= 4:
        try:
            box = [_finite_float(value) for value in raw_box[:4]]
        except (TypeError, ValueError, OverflowError):
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
                    back_points.append([_finite_float(point[0]), _finite_float(point[1])])
                except (TypeError, ValueError, OverflowError):
                    continue
    try:
        armor_id = int(payload.get("id", -1))
        plate_id = int(payload.get("plate_id", armor_id))
        confidence = max(0.0, min(1.0, _finite_float(payload.get("confidence", 0.0))))
        distance_m = max(0.0, _finite_float(payload.get("distance_m", 0.0)))
        depth_m = max(0.0, _finite_float(payload.get("depth_m", 0.0)))
        yaw_rad = _finite_float(payload.get("yaw_rad", 0.0))
        light_strength = max(0.0, min(1.0, _finite_float(payload.get("light_strength", 0.0))))
    except (TypeError, ValueError, OverflowError):
        # One malformed observation must not discard valid detections from the
        # same datagram or terminate the long-running UDP receive thread.
        return None
    return {
        "id": armor_id,
        "name": _bounded_text(payload.get("name", payload.get("label", "armor")), "armor"),
        "color": _bounded_text(payload.get("color", "unknown"), "unknown", 32),
        "confidence": confidence,
        "box": box,
        "points": points,
        "back_points": back_points,
        "locked": _fail_closed_bool(payload.get("locked", False)),
        "predicted": _fail_closed_bool(payload.get("predicted", False)),
        "aim": _fail_closed_bool(payload.get("aim", False)),
        "distance_m": distance_m,
        "depth_m": depth_m,
        "yaw_rad": yaw_rad,
        "plate_id": plate_id,
        "light_strength": light_strength,
        "light_state": _bounded_text(payload.get("light_state", "unknown"), "unknown", 32),
    }


def parse_tongji_packet(data: bytes) -> TongjiPacket | None:
    """校验并标准化 UDP `rm.vision.tongji.v1` 包，坏包不会影响 UI 线程。"""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
        return None
    try:
        width = int(payload.get("frame_w", 1))
        height = int(payload.get("frame_h", 1))
        timestamp_ns = int(payload.get("timestamp_ns", time.time_ns()))
        if not (1 <= width <= MAX_FRAME_DIMENSION and 1 <= height <= MAX_FRAME_DIMENSION):
            return None
        if timestamp_ns < 0:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    raw_armors = payload.get("armors", [])
    if not isinstance(raw_armors, list):
        raw_armors = []
    try:
        armors = tuple(
            item for item in (_armor(value) for value in raw_armors[:256]) if item is not None
        )
        primary = _armor(payload.get("primary"))
        if primary is None and armors:
            primary = next((armor for armor in armors if armor["locked"]), armors[0])
        energy = _armor(payload.get("energy"))
        return TongjiPacket(
            timestamp_ns=timestamp_ns,
            frame_width=width,
            frame_height=height,
            mode=_bounded_text(payload.get("mode", "auto_aim"), "auto_aim", 32),
            primary=primary,
            armors=armors,
            energy=energy,
            gimbal=_safe_pose_mapping(payload.get("gimbal", {})),
            command=_safe_pose_mapping(payload.get("command", {})),
        )
    except Exception:
        # ``data`` is an untrusted UDP boundary. A parser regression remains
        # contained to this datagram and cannot kill the receiver thread.
        return None


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
    """后台 UDP 接收器，只发布最新一包，防止视觉结果积压。

    重试策略
    --------
    ``refresh_vision_runtime`` 在 **60 Hz** 的 UI tick 里调用 :meth:`start`。
    端口被占用（或填了非法端口）时，早先的实现没有任何退避：线程起来、
    ``bind()`` 抛 OSError、线程退出，下一 tick 再来一个 —— 实测 1.04 秒内生成
    **60 个线程**（57.5 个/秒），会永久重建下去。

    现在与 :class:`~rm_client.vision.CameraCaptureWorker`、``LocalAlignedVision``
    用同一套有界指数退避：失败后 0.5s / 1s / 2s / 4s / 5s（封顶），同一次
    失败窗口内 ``start()`` 直接返回，不建线程。绑定成功即清零计数。
    """

    # 与 CameraCaptureWorker._mark_capture_failure 同一条曲线（0.5s 起、5s 封顶）。
    RETRY_BASE_SECONDS = 0.5
    RETRY_MAX_SECONDS = 5.0
    RETRY_MAX_EXPONENT = 4

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self.running = False
        self.connected = False
        self.last_error = ""
        self.last_received_ns = 0
        self.packets_received = 0
        self.invalid_packets = 0
        self.parse_errors = 0
        self.open_failures = 0
        self.retry_after = 0.0
        self._latest: TongjiPacket | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    def configure(self, port: int) -> None:
        port = int(port)
        if self.port != port:
            if self.running:
                self.stop()
            # 换端口 = 换一台机器，换机器就清掉旧端口攒下的退避，
            # 否则用户改完端口还要干等最多 5 秒才听得到新端口。
            self.open_failures = 0
            self.retry_after = 0.0
        self.port = port

    def start(self) -> None:
        if self.running:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        if time.perf_counter() < self.retry_after:
            # 退避窗口内：不建线程。这是 60 Hz tick 下的唯一刹车。
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

    def _mark_open_failure(self, message: str) -> None:
        """绑定期失败的有界指数退避（与 CameraCaptureWorker 同一条曲线）。"""
        self.last_error = message
        self.open_failures += 1
        delay = min(
            self.RETRY_MAX_SECONDS,
            self.RETRY_BASE_SECONDS * (2 ** min(self.RETRY_MAX_EXPONENT, self.open_failures - 1)),
        )
        self.retry_after = time.perf_counter() + delay

    def _run(self) -> None:
        bound = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.port))
            sock.settimeout(0.20)
            self._socket = sock
            # 端口真的拿到了：清零失败计数，下一次 start() 不会被旧退避挡住。
            self.open_failures = 0
            self.retry_after = 0.0
            bound = True
            while self.running:
                try:
                    data, _address = sock.recvfrom(512 * 1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                self.packets_received += 1
                try:
                    packet = parse_tongji_packet(data)
                except Exception as error:
                    self.parse_errors += 1
                    self.last_error = f"parse error: {type(error).__name__}: {error}"
                    continue
                if packet is None:
                    self.invalid_packets += 1
                    continue
                with self._lock:
                    self._latest = packet
                self.last_received_ns = time.time_ns()
                self.connected = True
        except (OSError, OverflowError) as error:
            # OverflowError：端口号填了 70000 这类越界值时 bind() 抛的不是
            # OSError。它和端口占用是同一类问题——起不来的接收器——所以同样
            # 计入退避，否则越界端口同样会被 60 Hz tick 打成每 tick 一个线程。
            self.connected = False
            if bound:
                # 运行期故障（不是开不起来）：不占用启动退避配额。
                self.last_error = str(error)
            else:
                self._mark_open_failure(str(error))
        except Exception as error:  # noqa: BLE001 - 接收线程必须活着收下一包
            self.connected = False
            if not bound:
                self._mark_open_failure(f"{type(error).__name__}: {error}")
            else:
                self.last_error = str(error)
        finally:
            if self._socket is not None:
                try:
                    self._socket.close()
                except OSError:
                    pass
            self._socket = None
            self.running = False
            self.connected = False


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
    """只缓存同济给出的建议角度，桌面客户端从不在这里发送控制。"""

    def __init__(self) -> None:
        self.last_command: dict[str, Any] = {"control": False, "fire": False}
        self.last_received_ns = 0

    def cache(self, command: dict[str, Any]) -> None:
        self.last_command = dict(command)
        self.last_command["fire"] = False
        self.last_command["control"] = False
        self.last_received_ns = time.time_ns()
