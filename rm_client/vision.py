from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
import socket
import struct
import threading
import time
from typing import Any, Callable

from .model import RobotState
from .backend.resource_close import close_resource, release_capture
from .tongji_bridge import TongjiPacket


PROTOCOL_MAGIC = b"PLVR"
PROTOCOL_VERSION = 1
PACKET_HEADER = struct.Struct("!4sHHIQI")
PACKET_FRAME = 1
PACKET_RESULT = 2


@dataclass
class VisionTarget:
    x: float
    y: float
    w: float
    h: float
    confidence: float
    label: str
    kind: str = "armor"


@dataclass(frozen=True)
class ArmorObservation:
    armor_id: int
    label: str
    color: str
    confidence: float
    points: tuple[tuple[float, float], ...]
    distance_m: float = 0.0
    predicted: bool = False
    aim: bool = False
    depth_m: float = 0.0
    back_points: tuple[tuple[float, float], ...] = ()
    yaw_rad: float = 0.0
    plate_id: int = -1
    light_strength: float = 0.0
    light_state: str = "unknown"
    target_id: int = -1
    held: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ArmorObservation":
        raw_points = payload.get("points", [])
        points: list[tuple[float, float]] = []
        if isinstance(raw_points, list):
            for point in raw_points[:4]:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    try:
                        points.append((float(point[0]), float(point[1])))
                    except (TypeError, ValueError):
                        continue
        raw_back = payload.get("back_points", [])
        back_points: list[tuple[float, float]] = []
        if isinstance(raw_back, list):
            for point in raw_back[:4]:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    try:
                        back_points.append((float(point[0]), float(point[1])))
                    except (TypeError, ValueError):
                        continue
        return cls(
            armor_id=int(payload.get("id", -1)),
            label=str(payload.get("label", "armor")),
            color=str(payload.get("color", "unknown")),
            confidence=max(0.0, min(1.0, float(payload.get("confidence", 0.0)))),
            points=tuple(points),
            distance_m=max(0.0, float(payload.get("distance_m", 0.0))),
            predicted=bool(payload.get("predicted", False)),
            aim=bool(payload.get("aim", False)),
            depth_m=max(0.0, float(payload.get("depth_m", 0.0))),
            back_points=tuple(back_points),
            yaw_rad=float(payload.get("yaw_rad", 0.0)),
            plate_id=int(payload.get("plate_id", payload.get("id", -1))),
            light_strength=max(0.0, min(1.0, float(payload.get("light_strength", 0.0)))),
            light_state=str(payload.get("light_state", "unknown")),
            target_id=int(payload.get("target_id", payload.get("id", -1))),
            held=bool(payload.get("held", False)),
        )


@dataclass(frozen=True)
class VisionLossEvidence:
    """Local-only evidence captured after a tracked armor disappears."""

    target_id: int
    label: str
    light_strength: float
    baseline_strength: float
    probe_dark: bool
    moving_fast: bool
    near_edge: bool
    sequence: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "VisionLossEvidence | None":
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                target_id=int(payload.get("target_id", -1)),
                label=str(payload.get("label", "armor")),
                light_strength=max(0.0, min(1.0, float(payload.get("light_strength", 0.0)))),
                baseline_strength=max(0.0, min(1.0, float(payload.get("baseline_strength", 0.0)))),
                probe_dark=bool(payload.get("probe_dark", False)),
                moving_fast=bool(payload.get("moving_fast", False)),
                near_edge=bool(payload.get("near_edge", False)),
                sequence=int(payload.get("sequence", -1)),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class VisionResult:
    sequence: int
    timestamp_ns: int
    source_width: int
    source_height: int
    detections: tuple[ArmorObservation, ...] = ()
    predicted_armors: tuple[ArmorObservation, ...] = ()
    aim_armor: ArmorObservation | None = None
    tracking_state: str = "disconnected"
    calibrated: bool = False
    inference_device: str = "unknown"
    inference_fps: float = 0.0
    latency_ms: float = 0.0
    aim_yaw_deg: float = 0.0
    aim_pitch_deg: float = 0.0
    loss_evidence: VisionLossEvidence | None = None

    @classmethod
    def from_payload(cls, sequence: int, timestamp_ns: int, payload: dict[str, Any]) -> "VisionResult":
        def armor_list(key: str) -> tuple[ArmorObservation, ...]:
            values = payload.get(key, [])
            if not isinstance(values, list):
                return ()
            return tuple(ArmorObservation.from_payload(item) for item in values if isinstance(item, dict))

        detected = armor_list("detections")
        predicted = armor_list("predicted_armors")
        raw_aim = payload.get("aim_armor")
        aim = ArmorObservation.from_payload(raw_aim) if isinstance(raw_aim, dict) else next((item for item in predicted if item.aim), None)
        return cls(
            sequence=sequence,
            timestamp_ns=int(payload.get("timestamp_ns", timestamp_ns)),
            source_width=max(1, int(payload.get("source_width", 1))),
            source_height=max(1, int(payload.get("source_height", 1))),
            detections=detected,
            predicted_armors=predicted,
            aim_armor=aim,
            tracking_state=str(payload.get("tracking_state", "lost")),
            calibrated=bool(payload.get("calibrated", False)),
            inference_device=str(payload.get("inference_device", "unknown")),
            inference_fps=max(0.0, float(payload.get("inference_fps", 0.0))),
            latency_ms=max(0.0, float(payload.get("latency_ms", 0.0))),
            aim_yaw_deg=float(payload.get("aim_yaw_deg", 0.0)),
            aim_pitch_deg=float(payload.get("aim_pitch_deg", 0.0)),
            loss_evidence=VisionLossEvidence.from_payload(payload.get("loss_evidence")),
        )

    @property
    def is_tracking(self) -> bool:
        return self.tracking_state.lower() in ("tracking", "locked", "detected") and bool(self.detections or self.predicted_armors)


@dataclass(frozen=True)
class VisionSnapshot:
    """所有视觉来源统一交给 HUD 的快照；不携带摄像头句柄或推理线程状态。"""

    source: str
    result: VisionResult
    received_ns: int
    gimbal_command: dict[str, Any] = field(default_factory=dict)

    @property
    def primary(self) -> ArmorObservation | None:
        return self.result.aim_armor or next(iter(self.result.detections), None)

    @property
    def armors(self) -> tuple[ArmorObservation, ...]:
        return self.result.detections


def _observation_from_tongji(payload: dict[str, Any], *, force_aim: bool = False) -> ArmorObservation:
    return ArmorObservation.from_payload({
        "id": payload.get("id", -1),
        "label": payload.get("name", "armor"),
        "color": payload.get("color", "unknown"),
        "confidence": payload.get("confidence", 0.0),
        "points": payload.get("points", []),
        "back_points": payload.get("back_points", []),
        "distance_m": payload.get("distance_m", 0.0),
        "depth_m": payload.get("depth_m", 0.0),
        "predicted": payload.get("predicted", False),
        "aim": force_aim or payload.get("aim", False) or payload.get("locked", False),
        "yaw_rad": payload.get("yaw_rad", 0.0),
        "plate_id": payload.get("plate_id", payload.get("id", -1)),
        "light_strength": payload.get("light_strength", 0.0),
        "light_state": payload.get("light_state", "unknown"),
        "target_id": payload.get("target_id", payload.get("id", -1)),
        "held": payload.get("held", False),
    })


def snapshot_from_tongji_packet(packet: TongjiPacket, *, source: str = "Tongji", inference_device: str = "external") -> VisionSnapshot:
    """将 UDP JSON 几何数据转换成 UI 使用的同一套 VisionResult。"""
    detections = tuple(_observation_from_tongji(armor) for armor in packet.armors)
    primary = _observation_from_tongji(packet.primary, force_aim=True) if packet.primary else None
    predicted = tuple(armor for armor in detections if armor.predicted)
    observed = tuple(armor for armor in detections if not armor.predicted)
    result = VisionResult(
        sequence=int(packet.timestamp_ns & 0xFFFFFFFF),
        timestamp_ns=packet.timestamp_ns,
        source_width=packet.frame_width,
        source_height=packet.frame_height,
        detections=observed,
        predicted_armors=predicted,
        aim_armor=primary,
        tracking_state="tracking" if primary else "detected" if observed else "lost",
        # External detections are display-only until the producer explicitly
        # asserts calibration; omission must never unlock control semantics.
        calibrated=packet.command.get("calibrated") is True,
        inference_device=inference_device,
        aim_yaw_deg=math.degrees(float(packet.command.get("yaw_rad", packet.gimbal.get("yaw_rad", 0.0)))),
        aim_pitch_deg=math.degrees(float(packet.command.get("pitch_rad", packet.gimbal.get("pitch_rad", 0.0)))),
    )
    return VisionSnapshot(source, result, time.time_ns(), packet.command)


def snapshot_from_local_result(
    timestamp_ns: int, sequence: int, width: int, height: int, armors: list[dict[str, Any]], inference_fps: float, latency_ms: float, device: str,
    loss_evidence: dict[str, Any] | None = None,
    *, calibrated: bool = False, camera_matrix: list[float] | None = None,
) -> VisionSnapshot:
    packet = TongjiPacket(timestamp_ns, width, height, "auto_aim", armors[0] if armors else None, tuple(armors), None, {}, {"control": False, "fire": False})
    snapshot = snapshot_from_tongji_packet(packet, source="Local", inference_device=device)
    aim_yaw_deg = 0.0
    aim_pitch_deg = 0.0
    primary = snapshot.result.aim_armor
    matrix = camera_matrix or []
    if calibrated and primary is not None and len(primary.points) >= 2 and len(matrix) >= 9:
        fx, fy = float(matrix[0]), float(matrix[4])
        cx, cy = float(matrix[2]), float(matrix[5])
        if fx > 0.0 and fy > 0.0:
            point_x = sum(point[0] for point in primary.points) / len(primary.points)
            point_y = sum(point[1] for point in primary.points) / len(primary.points)
            aim_yaw_deg = math.degrees(math.atan2(point_x - cx, fx))
            aim_pitch_deg = -math.degrees(math.atan2(point_y - cy, fy))
    result = VisionResult(
        sequence=sequence,
        timestamp_ns=timestamp_ns,
        source_width=width,
        source_height=height,
        detections=snapshot.result.detections,
        # Local PnP 已在后台输出整车预测板；这里必须原样交给 HUD，不能降级为空。
        predicted_armors=snapshot.result.predicted_armors,
        aim_armor=snapshot.result.aim_armor,
        tracking_state=snapshot.result.tracking_state,
        calibrated=bool(calibrated),
        inference_device=device,
        inference_fps=inference_fps,
        latency_ms=latency_ms,
        aim_yaw_deg=aim_yaw_deg,
        aim_pitch_deg=aim_pitch_deg,
        loss_evidence=VisionLossEvidence.from_payload(loss_evidence),
    )
    return VisionSnapshot(
        "Local",
        result,
        timestamp_ns,
        {
            "control": False,
            "fire": False,
            "calibrated": bool(calibrated),
            "yaw_rad": math.radians(aim_yaw_deg),
            "pitch_rad": math.radians(aim_pitch_deg),
        },
    )


class CompositeVisionPipeline:
    """按 Mock / Local / Tongji / Auto 选择最新可用视觉快照。"""

    def __init__(self) -> None:
        self.last_snapshot: VisionSnapshot | None = None

    def choose(self, source: str, local: VisionSnapshot | None, tongji: VisionSnapshot | None) -> VisionSnapshot | None:
        chosen: VisionSnapshot | None
        if source == "Local":
            chosen = local
        elif source == "Tongji":
            chosen = tongji
        elif source == "Auto":
            chosen = tongji or local
        else:
            chosen = None
        if chosen is not None:
            self.last_snapshot = chosen
        return chosen


@dataclass(frozen=True)
class CapturedFrame:
    sequence: int
    timestamp_ns: int
    width: int
    height: int
    rgb: Any
    bgr: Any


class CameraCaptureWorker:
    """独占电脑摄像头的后台线程，只保留最新帧供显示和桥接发送。"""

    def __init__(self, camera_id: int = 0, size: tuple[int, int] = (960, 540)) -> None:
        self.camera_id = camera_id
        self.size = size
        self.connected = False
        self.running = False
        self.last_error = ""
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest: CapturedFrame | None = None
        self._sequence = 0
        self.on_frame: Callable[[CapturedFrame], None] | None = None
        self.retry_after = 0.0
        self.open_failures = 0
        self.read_failures = 0
        self._stop_event = threading.Event()
        self._unreleased_capture: Any = None
        self._capture_close_error: Exception | None = None
        self._capture_close_error_reported = False
        self._capture_close_revision = 0
        self._lifecycle_lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._close_requests = 0
        self._epoch = 0
        self._start_done = threading.Event()
        self._start_done.set()

    def configure(self, camera_id: int, size: tuple[int, int]) -> None:
        """Set the next start's target; callers stop before changing a live stream."""
        with self._lifecycle_lock:
            self.camera_id = camera_id
            self.size = (max(160, int(size[0])), max(120, int(size[1])))

    @property
    def cleanup_pending(self) -> bool:
        with self._lifecycle_lock:
            return bool(self._close_requests) or (not self.running and (
                not self._start_done.is_set() or
                (self._thread is not None and self._thread.is_alive())))

    @property
    def cleanup_error(self) -> str:
        with self._lifecycle_lock:
            return str(self._capture_close_error or "")

    def start(self) -> bool:
        with self._lifecycle_lock:
            if self._capture_close_error is not None or self._close_requests:
                return False
            if self.running:
                return True
            if not self._start_done.is_set() or (self._thread is not None and self._thread.is_alive()):
                return False
            if time.perf_counter() < self.retry_after:
                return False
            self._epoch += 1
            epoch = self._epoch
            stop_event = threading.Event()
            start_done = threading.Event()
            try:
                thread = threading.Thread(
                    target=self._run,
                    args=(stop_event, epoch, start_done, (self.camera_id, self.size)),
                    name="polar-camera",
                    daemon=True,
                )
            except Exception as exc:
                self._mark_capture_failure(f"Camera worker start failed: {exc}")
                raise
            self._stop_event, self._start_done = stop_event, start_done
            self._thread = thread
            self.running = True
        # Thread.start may itself be delayed. Keep the revocation lock free;
        # the worker's admission gate prevents native open before it resolves.
        try:
            thread.start()
        except Exception as exc:
            with self._lifecycle_lock:
                self.running = False
                self.connected = False
                self._epoch += 1
                stop_event.set()
                self._mark_capture_failure(f"Camera worker start failed: {exc}")
                # A wrapper can raise after admission, even after exit. Only
                # a genuinely unstarted placeholder can be discarded here.
                if thread.ident is None:
                    self._thread = None
            raise
        finally:
            start_done.set()
        with self._lifecycle_lock:
            return epoch == self._epoch and not stop_event.is_set()

    def stop(self, *, wait: bool = True) -> None:
        """Revoke frames immediately; wait=False never joins or releases native.

        The existing worker remains the sole capture cleanup owner. Default
        wait preserves the legacy bounded stop; close() confirms full teardown.
        """
        with self._lifecycle_lock:
            self.running = False
            self.connected = False
            self._epoch += 1
            self._stop_event.set()
            thread, start_done = self._thread, self._start_done
            with self._lock:
                self._latest = None
        if wait and thread is not threading.current_thread():
            timeout = 1.2
            if not start_done.is_set():
                began = time.perf_counter()
                start_done.wait(timeout)
                timeout = max(0.0, timeout - (time.perf_counter() - began))
            if thread is not None and thread.is_alive():
                thread.join(timeout=timeout)
        with self._lifecycle_lock:
            if self._thread is thread and start_done.is_set() and (thread is None or not thread.is_alive()):
                self._thread = None

    def close(self) -> None:
        """Strict wait and explicit failed-release retry, on a teardown worker."""
        if self._thread is threading.current_thread():
            self.stop(wait=False)
            raise RuntimeError("Camera cannot wait for its own worker")
        with self._lifecycle_lock:
            # A close queued before failure reporting is only a waiter. A
            # revision also prevents two queued retries consuming one error.
            retry_revision = self._capture_close_revision if self._capture_close_error_reported else None
            self._close_requests += 1
        try:
            with self._close_lock:
                with self._lifecycle_lock:
                    thread, start_done = self._thread, self._start_done
                self.stop()
                start_done.wait()
                if thread is not None and getattr(thread, "ident", True) is not None:
                    thread.join()
                with self._lifecycle_lock:
                    if self._thread is thread:
                        self._thread = None
                    retry_capture = self._unreleased_capture if (
                        self._capture_close_error is not None and
                        retry_revision == self._capture_close_revision) else None
                if retry_capture is not None:
                    try:
                        release_capture(retry_capture)
                    except Exception as exc:
                        self._record_release_failure(retry_capture, exc)
                    else:
                        with self._lifecycle_lock:
                            self._unreleased_capture = None
                            self._capture_close_error = None
                            self._capture_close_error_reported = False
                with self._lifecycle_lock:
                    error = self._capture_close_error
                    if error is not None:
                        self._capture_close_error_reported = True
                if error is not None:
                    raise RuntimeError(f"Camera capture release failed: {error}") from error
        finally:
            with self._lifecycle_lock:
                self._close_requests -= 1

    def latest_frame(self) -> CapturedFrame | None:
        with self._lock:
            return self._latest

    def _is_current(self, stop_event: threading.Event, epoch: int) -> bool:
        with self._lifecycle_lock:
            return self.running and epoch == self._epoch and not stop_event.is_set()

    def _mark_capture_failure(self, message: str) -> None:
        with self._lifecycle_lock:
            self.last_error = message
            self.open_failures += 1
            # Avoid hot retries of missing devices, broken native backends,
            # and unavailable OS worker slots.
            delay = min(5.0, 0.5 * (2 ** min(4, self.open_failures - 1)))
            self.retry_after = time.perf_counter() + delay

    def _record_release_failure(self, capture: Any, error: Exception) -> None:
        with self._lifecycle_lock:
            self._unreleased_capture = capture
            self._capture_close_error = error
            self._capture_close_error_reported = False
            self._capture_close_revision += 1
            self.last_error = f"Camera capture release failed: {error}"

    def _run(self, stop_event: threading.Event, epoch: int | None = None,
             start_done: threading.Event | None = None,
             config: tuple[int, tuple[int, int]] | None = None) -> None:
        if start_done is not None:
            start_done.wait()
        with self._lifecycle_lock:
            epoch = self._epoch if epoch is None else epoch
            camera_id, size = config if config is not None else (self.camera_id, self.size)
        capture: Any = None
        produced_frame = False
        try:
            if not self._is_current(stop_event, epoch):
                return
            import cv2  # type: ignore

            # Windows must not fall through from DirectShow to MSMF: some
            # drivers claim an open stream then repeatedly crash in read().
            if os.name == "nt" and hasattr(cv2, "CAP_DSHOW"):
                capture = cv2.VideoCapture(camera_id, cv2.CAP_DSHOW)
            else:  # pragma: no cover - Windows is the packaged target
                capture = cv2.VideoCapture(camera_id)
            if not self._is_current(stop_event, epoch):
                return
            opened = capture is not None and capture.isOpened()
            with self._lifecycle_lock:
                if not self._is_current(stop_event, epoch):
                    return
                if not opened:
                    self._mark_capture_failure("摄像头不可用")
                    return
            for prop, value in ((cv2.CAP_PROP_FRAME_WIDTH, size[0]), (cv2.CAP_PROP_FRAME_HEIGHT, size[1])):
                if not self._is_current(stop_event, epoch):
                    return
                capture.set(prop, value)
            with self._lifecycle_lock:
                if not self._is_current(stop_event, epoch):
                    return
                self.connected = True
            consecutive_read_failures = 0
            while self._is_current(stop_event, epoch):
                ok, bgr = capture.read()
                with self._lifecycle_lock:
                    if not self._is_current(stop_event, epoch):
                        return
                    if not ok or bgr is None:
                        consecutive_read_failures += 1
                        self.read_failures += 1
                        if consecutive_read_failures >= 12:
                            self._mark_capture_failure("摄像头连续读取失败")
                            return
                if not ok or bgr is None:
                    stop_event.wait(0.025)
                    continue
                if bgr.shape[1] != size[0] or bgr.shape[0] != size[1]:
                    bgr = cv2.resize(bgr, size, interpolation=cv2.INTER_LINEAR)
                if not self._is_current(stop_event, epoch):
                    return
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                with self._lifecycle_lock:
                    if not self._is_current(stop_event, epoch):
                        return
                    produced_frame = True
                    consecutive_read_failures = 0
                    self.open_failures = 0
                    self.retry_after = 0.0
                    self.last_error = ""
                    self._sequence += 1
                    frame = CapturedFrame(self._sequence, time.time_ns(), int(rgb.shape[1]), int(rgb.shape[0]), rgb, bgr)
                    with self._lock:
                        self._latest = frame
                    callback = self.on_frame
                # An already-admitted callback may finish on this retained
                # worker. Never hold UI revocation across arbitrary callbacks.
                if callback is not None:
                    try:
                        callback(frame)
                    except Exception as exc:
                        with self._lifecycle_lock:
                            if self._is_current(stop_event, epoch):
                                self.last_error = f"摄像头回调失败: {exc}"
        except Exception as exc:  # pragma: no cover - driver dependent
            with self._lifecycle_lock:
                if self._is_current(stop_event, epoch):
                    self._mark_capture_failure(f"摄像头异常: {exc}")
        finally:
            if capture is not None:
                try:
                    release_capture(capture)
                except Exception as exc:
                    self._record_release_failure(capture, exc)
            with self._lifecycle_lock:
                if self._thread is threading.current_thread() or epoch == self._epoch:
                    self.connected = False
                    self.running = False
                    if not produced_frame and not self.last_error and not stop_event.is_set():
                        self._mark_capture_failure("摄像头未输出画面")
            # Keep the thread slot until an observer confirms actual exit.
            # Native destruction can still run as this frame unwinds.

class TongjiVisionBridge:
    """与 WSL/Ubuntu 同济桥接进程通信的最新帧优先 TCP 客户端。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 18301) -> None:
        self.host = host
        self.port = port
        self.running = False
        self.connected = False
        self.last_error = ""
        self.last_result: VisionResult | None = None
        self.results_received = 0
        self.frames_sent = 0
        self._thread: threading.Thread | None = None
        self._unreleased_socket: socket.socket | None = None
        self._socket_close_error: Exception | None = None
        self._socket_close_error_reported = False
        self._lifecycle_lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._closing = False
        self._epoch = 0
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._pending: tuple[int, int, int, int, bytes] | None = None
        self._receive_buffer = bytearray()
        self.on_result: Callable[[VisionResult], None] | None = None

    def configure(self, host: str, port: int) -> None:
        self.host = host
        self.port = max(1, min(65535, int(port)))

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
            self._stop_event = threading.Event()
            thread = threading.Thread(target=self._run, args=(self._epoch,), name="tongji-bridge", daemon=True)
            self._thread = thread
            self.running = True
            try:
                thread.start()
            except Exception:
                self.running = False
                self._epoch += 1
                self._stop_event.set()
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
            self._stop_event.set()
            thread = self._thread
            with self._lock:
                self._pending = None
            self.last_result = None
        if wait and thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def close(self) -> None:
        """Strict wait and explicit retry, only on a teardown worker."""
        if self._thread is threading.current_thread():
            self.stop(wait=False)
            raise RuntimeError("Vision bridge cannot wait for its own worker")
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
                    raise RuntimeError("Vision bridge cannot wait for its own worker")
                if thread is not None:
                    thread.join()
                with self._lifecycle_lock:
                    if self._thread is thread:
                        self._thread = None
                if self._socket_close_error is not None and retry_reported:
                    try:
                        close_resource(self._unreleased_socket)
                        if self._unreleased_socket.fileno() != -1:
                            raise OSError("Socket remained open after close")
                    except Exception as exc:
                        self._socket_close_error = exc
                    else:
                        self._unreleased_socket = None
                        self._socket_close_error = None
                        self._socket_close_error_reported = False
                if self._socket_close_error is not None:
                    self._socket_close_error_reported = True
                    raise RuntimeError(f"Vision bridge socket close failed: {self._socket_close_error}") from self._socket_close_error
            finally:
                with self._lifecycle_lock:
                    self._closing = False

    def submit_bgr(self, frame: CapturedFrame, quality: int = 82) -> None:
        with self._lifecycle_lock:
            if not self.running:
                return
            epoch = self._epoch
        try:
            import cv2  # type: ignore

            ok, encoded = cv2.imencode(".jpg", frame.bgr, [int(cv2.IMWRITE_JPEG_QUALITY), max(35, min(95, int(quality)))])
            if ok:
                jpeg = bytes(encoded)
                with self._lifecycle_lock:
                    if self.running and epoch == self._epoch:
                        self.submit_jpeg(frame.sequence, frame.timestamp_ns, frame.width, frame.height, jpeg)
        except Exception as exc:  # pragma: no cover - depends on optional cv2
            self.last_error = str(exc)

    def submit_jpeg(self, sequence: int, timestamp_ns: int, width: int, height: int, jpeg: bytes) -> None:
        if not jpeg:
            return
        with self._lifecycle_lock:
            if not self.running:
                return
            epoch = self._epoch
        frame = (int(sequence), int(timestamp_ns), int(width), int(height), bytes(jpeg))
        with self._lifecycle_lock:
            if not self.running or epoch != self._epoch:
                return
            with self._lock:
                self._pending = frame

    def latest_result(self, max_age_ms: float = 420.0) -> VisionResult | None:
        result = self.last_result
        if result is None:
            return None
        if (time.time_ns() - result.timestamp_ns) / 1_000_000.0 > max_age_ms:
            return None
        return result

    def _take_pending(self) -> tuple[int, int, int, int, bytes] | None:
        with self._lock:
            frame = self._pending
            self._pending = None
            return frame

    def _run(self, epoch: int | None = None) -> None:
        with self._lifecycle_lock:
            epoch = self._epoch if epoch is None else epoch
            stop_event = self._stop_event
        while self.running and epoch == self._epoch:
            sock: socket.socket | None = None
            try:
                sock = socket.create_connection((self.host, self.port), timeout=0.6)
                sock.settimeout(0.025)
                with self._lifecycle_lock:
                    if not self.running or epoch != self._epoch:
                        return
                    self.connected = True
                    self.last_error = ""
                self._receive_buffer.clear()
                while self.running and epoch == self._epoch:
                    frame = self._take_pending()
                    if frame is not None:
                        sequence, timestamp_ns, width, height, jpeg = frame
                        metadata = json.dumps({"source_width": width, "source_height": height}, separators=(",", ":")).encode("utf-8")
                        self._send_packet(sock, PACKET_FRAME, sequence, timestamp_ns, metadata + b"\n" + jpeg)
                        self.frames_sent += 1
                    try:
                        data = sock.recv(262144)
                        if not data:
                            raise ConnectionError("同济桥接器已关闭连接")
                        if not self.running or epoch != self._epoch:
                            break
                        self._receive_buffer.extend(data)
                        self._parse_packets(epoch)
                    except socket.timeout:
                        pass
            except (OSError, ConnectionError) as exc:
                self.last_error = str(exc)
                self.connected = False
                stop_event.wait(0.20)
            finally:
                self.connected = False
                if sock is not None:
                    try:
                        close_resource(sock)
                        if sock.fileno() != -1:
                            raise OSError("Socket remained open after close")
                    except Exception as exc:
                        with self._lifecycle_lock:
                            self._unreleased_socket = sock
                            self._socket_close_error = exc
                            self.last_error = f"Vision bridge socket close failed: {exc}"
                            self.running = False

    @staticmethod
    def _send_packet(sock: socket.socket, packet_type: int, sequence: int, timestamp_ns: int, payload: bytes) -> None:
        header = PACKET_HEADER.pack(PROTOCOL_MAGIC, PROTOCOL_VERSION, packet_type, sequence, timestamp_ns, len(payload))
        sock.sendall(header + payload)

    def _parse_packets(self, epoch: int | None = None) -> None:
        epoch = self._epoch if epoch is None else epoch
        while len(self._receive_buffer) >= PACKET_HEADER.size:
            magic, version, packet_type, sequence, timestamp_ns, payload_size = PACKET_HEADER.unpack(self._receive_buffer[:PACKET_HEADER.size])
            if magic != PROTOCOL_MAGIC or version != PROTOCOL_VERSION or payload_size > 4_000_000:
                self._receive_buffer.clear()
                self.last_error = "同济桥接协议头无效"
                return
            total = PACKET_HEADER.size + payload_size
            if len(self._receive_buffer) < total:
                return
            payload = bytes(self._receive_buffer[PACKET_HEADER.size:total])
            del self._receive_buffer[:total]
            if packet_type != PACKET_RESULT:
                continue
            try:
                data = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.last_error = "同济桥接结果不是有效 JSON"
                continue
            if not isinstance(data, dict):
                continue
            result = VisionResult.from_payload(sequence, timestamp_ns, data)
            with self._lifecycle_lock:
                if not self.running or epoch != self._epoch:
                    return
                self.last_result = result
                self.results_received += 1
                callback = self.on_result
            # Already-admitted callbacks finish on this retained worker. Never
            # hold the UI revocation lock across arbitrary callback code.
            if callback is not None:
                callback(result)


class MockVisionPipeline:
    """真实自瞄链路接入前使用的移动目标生成器。"""

    def process(self, width: int, height: int, state: RobotState, target_kind: str = "armor") -> VisionTarget:
        if target_kind == "energy":
            return self.process_energy(width, height, state)
        return self.process_armor(width, height, state)

    def process_armor(self, width: int, height: int, state: RobotState) -> VisionTarget:
        now = time.perf_counter() - state.start_time
        target_w = max(76.0, width * 0.08)
        target_h = max(52.0, height * 0.09)
        cx = width * 0.5 + math.sin(now * 0.9) * width * 0.18
        cy = height * 0.45 + math.cos(now * 0.6) * height * 0.08
        return VisionTarget(cx - target_w / 2.0, cy - target_h / 2.0, target_w, target_h, 0.86 + 0.1 * math.sin(now * 1.7), f"Robot {state.target_id}", "armor")

    def process_energy(self, width: int, height: int, state: RobotState) -> VisionTarget:
        now = time.perf_counter() - state.start_time
        target_w = max(92.0, width * 0.075)
        cx = width * 0.5 + math.sin(now * 0.45) * width * 0.12
        cy = height * 0.36 + math.cos(now * 0.72) * height * 0.06
        return VisionTarget(cx - target_w / 2.0, cy - target_w / 2.0, target_w, target_w, 0.78 + 0.14 * math.sin(now * 1.1), f"Energy {state.energy_rune_phase}", "energy")
