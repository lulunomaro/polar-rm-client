from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
import json
import math
import socket
import threading
import time
from typing import Any

from .backend.resource_close import close_resource


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
    """后台 UDP 接收器，只发布最新一包，防止视觉结果积压。"""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self.running = False
        self.connected = False
        self.last_error = ""
        self.last_received_ns = 0
        self.packets_received = 0
        self.invalid_packets = 0
        self.parse_errors = 0
        self._latest: TongjiPacket | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._socket_close_error: Exception | None = None
        self._socket_close_error_reported = False
        self._lifecycle_lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._closing = False
        self._epoch = 0

    def configure(self, port: int, *, wait: bool = True) -> None:
        port = int(port)
        with self._lifecycle_lock:
            if self.port == port:
                return
            self.stop(wait=False)
            self.port = port
            thread = self._thread
        if wait:
            self._wait_stopped_worker(thread)

    @property
    def cleanup_pending(self) -> bool:
        with self._lifecycle_lock:
            return self._closing or (not self.running and self._thread is not None and self._thread.is_alive())

    @property
    def cleanup_error(self) -> str:
        with self._lifecycle_lock:
            return str(self._socket_close_error or "")

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.running or self._closing:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            if self._socket_close_error is not None:
                return
            self._epoch += 1
            thread = threading.Thread(target=self._run, args=(self._epoch,), name="polar-tongji-udp", daemon=True)
            self._thread = thread
            self.running = True
            try:
                thread.start()
            except Exception:
                self.running = False
                self._epoch += 1
                # Only a genuinely unstarted thread is safe to discard.
                if thread.ident is None:
                    self._thread = None
                raise

    def stop(self, *, wait: bool = True) -> None:
        """Revoke results immediately; the existing worker owns socket close.

        wait=False never joins or closes a native handle. Default wait retains
        the legacy bounded wait; only close() confirms complete teardown.
        """
        with self._lifecycle_lock:
            self.running = False
            self.connected = False
            self._epoch += 1
            thread = self._thread
            with self._lock:
                self._latest = None
        if wait:
            self._wait_stopped_worker(thread)

    def _wait_stopped_worker(self, thread: threading.Thread | None) -> None:
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.6)
        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def close(self) -> None:
        """Strict wait and explicit retry, only on a teardown worker."""
        if self._thread is threading.current_thread():
            self.stop(wait=False)
            raise RuntimeError("Vision receiver cannot wait for its own worker")
        with self._lifecycle_lock:
            # A duplicate close already queued before the first failure was
            # reported is a waiter, not an authorization to retry that error.
            retry_reported = self._socket_close_error is not None and self._socket_close_error_reported
        with self._close_lock:
            with self._lifecycle_lock:
                self._closing = True
                thread = self._thread
            try:
                self.stop()
                if thread is threading.current_thread():
                    raise RuntimeError("Vision receiver cannot wait for its own worker")
                if thread is not None:
                    thread.join()
                with self._lifecycle_lock:
                    if self._thread is thread:
                        self._thread = None
                if self._socket_close_error is not None and retry_reported:
                    try:
                        close_resource(self._socket)
                        if self._socket.fileno() != -1:
                            raise OSError("Socket remained open after close")
                    except Exception as exc:
                        self._socket_close_error = exc
                    else:
                        self._socket = None
                        self._socket_close_error = None
                        self._socket_close_error_reported = False
                if self._socket_close_error is not None:
                    self._socket_close_error_reported = True
                    raise RuntimeError(f"Vision receiver socket close failed: {self._socket_close_error}") from self._socket_close_error
            finally:
                with self._lifecycle_lock:
                    self._closing = False

    def latest(self, timeout_ms: float) -> TongjiPacket | None:
        with self._lock:
            packet = self._latest
        if packet is None or time.time_ns() - self.last_received_ns > int(max(1.0, timeout_ms) * 1e6):
            self.connected = False
            return None
        return packet

    def _run(self, epoch: int | None = None) -> None:
        with self._lifecycle_lock:
            if epoch is not None and (not self.running or epoch != self._epoch):
                return
            epoch = self._epoch if epoch is None else epoch
            port = self.port
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
            sock.settimeout(0.20)
            with self._lifecycle_lock:
                # Retain a late-created handle until this owner's finally
                # confirms native close, even after stop revoked publication.
                self._socket = sock
            while self.running and epoch == self._epoch:
                try:
                    data, _address = sock.recvfrom(512 * 1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                with self._lifecycle_lock:
                    if not self.running or epoch != self._epoch:
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
                with self._lifecycle_lock:
                    if not self.running or epoch != self._epoch:
                        break
                    with self._lock:
                        self._latest = packet
                    self.last_received_ns = time.time_ns()
                    self.connected = True
        except OSError as error:
            self.last_error = str(error)
            self.connected = False
        finally:
            if sock is not None:
                try:
                    close_resource(sock)
                    if sock.fileno() != -1:
                        raise OSError("Socket remained open after close")
                except Exception as exc:
                    with self._lifecycle_lock:
                        self._socket = sock
                        self._socket_close_error = exc
                        self.last_error = f"Vision receiver socket close failed: {exc}"
                else:
                    with self._lifecycle_lock:
                        self._socket = None
            with self._lifecycle_lock:
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
