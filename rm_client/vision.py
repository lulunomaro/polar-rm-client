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
from .tongji_bridge import TongjiPacket


# PLVR 桥接协议常量。**只有** TongjiVisionBridge 使用，而该类在生产路径上从不
# start()（见它的类文档）。tests/smoke_test.py 仍在验证这套帧格式，保留。
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

    def configure(self, camera_id: int, size: tuple[int, int]) -> None:
        self.camera_id = camera_id
        self.size = (max(160, int(size[0])), max(120, int(size[1])))

    def start(self) -> bool:
        if self.running:
            return True
        if self._thread is not None and self._thread.is_alive():
            return False
        if time.perf_counter() < self.retry_after:
            return False
        stop_event = threading.Event()
        self._stop_event = stop_event
        self.running = True
        self._thread = threading.Thread(
            target=self._run,
            args=(stop_event,),
            name="polar-camera",
            daemon=True,
        )
        try:
            self._thread.start()
        except Exception:
            self.running = False
            self._thread = None
            raise
        return True

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.2)
        if thread is None or not thread.is_alive():
            self._thread = None
        self.connected = False

    def latest_frame(self) -> CapturedFrame | None:
        with self._lock:
            return self._latest

    def _mark_capture_failure(self, message: str) -> None:
        self.last_error = message
        self.open_failures += 1
        # Device probing can enter native multimedia backends.  A bounded
        # exponential retry prevents a missing camera from consuming a core or
        # repeatedly exercising a broken Windows MSMF driver.
        delay = min(5.0, 0.5 * (2 ** min(4, self.open_failures - 1)))
        self.retry_after = time.perf_counter() + delay

    def _run(self, stop_event: threading.Event) -> None:
        capture: Any = None
        produced_frame = False
        try:
            import cv2  # type: ignore
        except Exception as exc:  # pragma: no cover - local camera stack
            self._mark_capture_failure(str(exc))
            self.running = False
            return
        try:
            # Windows is the release target.  Do not fall through to OpenCV's
            # automatic MSMF backend after DirectShow fails: several drivers
            # report an opened stream and then throw native access violations
            # forever from read().  Other platforms retain their native auto
            # backend.
            if os.name == "nt" and hasattr(cv2, "CAP_DSHOW"):
                capture = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
            else:  # pragma: no cover - Windows is the packaged target
                capture = cv2.VideoCapture(self.camera_id)
            if capture is None or not capture.isOpened():
                self._mark_capture_failure("摄像头不可用")
                return
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
            self.connected = True
            consecutive_read_failures = 0
            while self.running and not stop_event.is_set():
                ok, bgr = capture.read()
                if not ok or bgr is None:
                    consecutive_read_failures += 1
                    self.read_failures += 1
                    if consecutive_read_failures >= 12:
                        self._mark_capture_failure("摄像头连续读取失败")
                        break
                    stop_event.wait(0.025)
                    continue
                produced_frame = True
                consecutive_read_failures = 0
                self.open_failures = 0
                self.retry_after = 0.0
                self.last_error = ""
                if bgr.shape[1] != self.size[0] or bgr.shape[0] != self.size[1]:
                    bgr = cv2.resize(bgr, self.size, interpolation=cv2.INTER_LINEAR)
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                self._sequence += 1
                frame = CapturedFrame(self._sequence, time.time_ns(), int(rgb.shape[1]), int(rgb.shape[0]), rgb, bgr)
                with self._lock:
                    self._latest = frame
                callback = self.on_frame
                if callback is not None:
                    try:
                        callback(frame)
                    except Exception as exc:
                        # A UI callback fault may drop one frame, but must not
                        # abandon the only capture owner without diagnostics.
                        self.last_error = f"摄像头回调失败: {exc}"
        except Exception as exc:  # pragma: no cover - driver dependent
            self._mark_capture_failure(f"摄像头异常: {exc}")
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            self.connected = False
            self.running = False
            if not produced_frame and not self.last_error and not stop_event.is_set():
                self._mark_capture_failure("摄像头未输出画面")
            if self._thread is threading.current_thread():
                self._thread = None


class TongjiVisionBridge:
    """与 WSL/Ubuntu 同济桥接进程通信的最新帧优先 TCP 客户端。

    ⚠ **生产路径上的死代码**（本轮实测结论，勿以为它在跑）
    ------------------------------------------------------
    * 类体 136 行 + 一整套 ``PLVR`` 二进制 TCP 帧格式（模块级
      ``PROTOCOL_MAGIC/VERSION/PACKET_HEADER/PACKET_FRAME/PACKET_RESULT``）
      + ``submit_bgr()`` 的整条 JPEG 编码链。
    * 全仓库构造点只有一处：``rm_client/ui/polar_app.py:443``
      ``self.tongji_bridge = TongjiVisionBridge()``。**从未调用 ``start()``** ——
      关闭期 ``polar_app.py:3216`` 与 ``video_backend_mixin.refresh_vision_runtime``
      都只 ``stop()``，而 ``running`` 永远是 False。
    * ``submit_bgr()``（唯一的 JPEG 编码入口）**零调用方**，连 smoke_test 都不用，
      它只走 ``submit_jpeg()``。
    * 唯一调用方是 ``tests/smoke_test.py`` 的 ``test_tongji_bridge_protocol()``。

    实测运行成本为 0：构造它新增 **0 个线程、0 个 socket**（``start()`` 才建线程）。

    为什么还留着
    ------------
    删不掉。``rm_client/ui/polar_app.py`` 在 ``rm_client/ui/**`` 里，属于**视觉
    冻结范围**：它在模块顶层 import 本类（第 96 行的 import 列表里）、在构造期
    实例化（第 443 行）、在关闭期 ``stop()``（第 3216 行）。删类会让冻结文件的
    import 直接炸，而本轮禁止改动冻结文件。真正删除需要先解冻 ``polar_app.py``
    与 ``video_backend_mixin.py``，属于跨组决策。

    本轮能做的都做了：把"死"这件事连同证据写进代码，并加测试钉住它**保持死**
    （构造不产生任何线程/socket），这样哪天有人不小心在生产路径接上它，会被测试
    抓到而不是悄悄多出一个线程。TODO(视觉组)：解冻后连同 PLVR 常量与
    ``submit_bgr`` 一并删除。
    """

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
        self._lock = threading.Lock()
        self._pending: tuple[int, int, int, int, bytes] | None = None
        self._receive_buffer = bytearray()
        self.on_result: Callable[[VisionResult], None] | None = None

    def configure(self, host: str, port: int) -> None:
        self.host = host
        self.port = max(1, min(65535, int(port)))

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, name="tongji-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self.connected = False

    def submit_bgr(self, frame: CapturedFrame, quality: int = 82) -> None:
        try:
            import cv2  # type: ignore

            ok, encoded = cv2.imencode(".jpg", frame.bgr, [int(cv2.IMWRITE_JPEG_QUALITY), max(35, min(95, int(quality)))])
            if ok:
                self.submit_jpeg(frame.sequence, frame.timestamp_ns, frame.width, frame.height, bytes(encoded))
        except Exception as exc:  # pragma: no cover - depends on optional cv2
            self.last_error = str(exc)

    def submit_jpeg(self, sequence: int, timestamp_ns: int, width: int, height: int, jpeg: bytes) -> None:
        if not jpeg:
            return
        with self._lock:
            self._pending = (int(sequence), int(timestamp_ns), int(width), int(height), bytes(jpeg))

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

    def _run(self) -> None:
        while self.running:
            sock: socket.socket | None = None
            try:
                sock = socket.create_connection((self.host, self.port), timeout=0.6)
                sock.settimeout(0.025)
                self.connected = True
                self.last_error = ""
                self._receive_buffer.clear()
                while self.running:
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
                        self._receive_buffer.extend(data)
                        self._parse_packets()
                    except socket.timeout:
                        pass
            except (OSError, ConnectionError) as exc:
                self.last_error = str(exc)
                self.connected = False
                time.sleep(0.20)
            finally:
                self.connected = False
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

    @staticmethod
    def _send_packet(sock: socket.socket, packet_type: int, sequence: int, timestamp_ns: int, payload: bytes) -> None:
        header = PACKET_HEADER.pack(PROTOCOL_MAGIC, PROTOCOL_VERSION, packet_type, sequence, timestamp_ns, len(payload))
        sock.sendall(header + payload)

    def _parse_packets(self) -> None:
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
            self.last_result = result
            self.results_received += 1
            callback = self.on_result
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
