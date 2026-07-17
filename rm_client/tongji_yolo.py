from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import threading
import time
from typing import Any

from .armor_vehicle import MultiArmorPrediction


ARMOR_NAMES = ("sentry", "one", "two", "three", "four", "five", "outpost", "base", "not_armor")
ARMOR_COLORS = ("blue", "red", "extinguish", "purple")


def resolve_tongji_yolo_xml(workspace: Path) -> Path:
    """定位 yolov5.xml：优先 sp_vision_py/assets（联接原仓库），再回退同济 C++ assets。"""
    candidates = [
        workspace / "sp_vision_py" / "assets" / "yolov5.xml",
        workspace.parent / "sp_vision_py" / "assets" / "yolov5.xml",
        workspace / "同济自瞄" / "sp_vision_25" / "assets" / "yolov5.xml",
        workspace.parent / "同济自瞄" / "sp_vision_25" / "assets" / "yolov5.xml",
        workspace / "rm_custom_client" / ".." / "sp_vision_py" / "assets" / "yolov5.xml",
        workspace / "rm_custom_client" / ".." / "同济自瞄" / "sp_vision_25" / "assets" / "yolov5.xml",
    ]
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    if workspace.is_dir():
        for child in workspace.iterdir():
            if not child.is_dir():
                continue
            for rel in (
                Path("assets") / "yolov5.xml",
                Path("sp_vision_25") / "assets" / "yolov5.xml",
            ):
                hit = child / rel
                if hit.is_file():
                    return hit.resolve()
    return candidates[0].resolve()


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value)) if value >= 0 else math.exp(value) / (1.0 + math.exp(value))


@dataclass(frozen=True)
class LocalDetection:
    name: str
    color: str
    confidence: float
    box: tuple[float, float, float, float]
    points: tuple[tuple[float, float], ...]
    light_strength: float = 0.0

    def payload(self, index: int, *, locked: bool = False) -> dict[str, Any]:
        return {
            "id": index,
            "name": self.name,
            "color": self.color,
            "confidence": self.confidence,
            "box": list(self.box),
            "points": [list(point) for point in self.points],
            "locked": locked,
            "light_strength": self.light_strength,
            "light_state": "extinguished" if self.color == "extinguish" else "lit",
        }


class TongjiYoloV5:
    """同济 YOLOv5 OpenVINO IR 的 Python 对齐封装，复刻其 letterbox 与四点解析。"""

    def __init__(self, model_path: Path, device: str = "AUTO") -> None:
        import openvino as ov  # type: ignore

        self.model_path = model_path
        self.device = device
        self.core = ov.Core()
        model = self.core.read_model(str(model_path))
        try:
            self.compiled = self.core.compile_model(model, device)
            self.active_device = device
        except Exception:
            self.compiled = self.core.compile_model(model, "CPU")
            self.active_device = "CPU fallback"
        self.input = self.compiled.input(0)
        self.output = self.compiled.output(0)

    def detect(self, bgr: Any, score_threshold: float = 0.45, min_confidence: float = 0.50) -> list[LocalDetection]:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        height, width = bgr.shape[:2]
        scale = min(640.0 / max(1, height), 640.0 / max(1, width))
        resized_w = max(1, int(width * scale))
        resized_h = max(1, int(height * scale))
        canvas = np.zeros((640, 640, 3), dtype=np.uint8)
        canvas[:resized_h, :resized_w] = cv2.resize(bgr, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        tensor = np.transpose(rgb.astype(np.float32) / 255.0, (2, 0, 1))[None, ...]
        output = self.compiled({self.input.any_name: tensor})[self.output]
        rows = np.asarray(output, dtype=np.float32).reshape(-1, 22)

        boxes: list[list[int]] = []
        confidences: list[float] = []
        candidates: list[LocalDetection] = []
        for row in rows:
            confidence = _sigmoid(float(row[8]))
            if confidence < score_threshold or confidence < min_confidence:
                continue
            color_id = int(np.argmax(row[9:13]))
            number_id = int(np.argmax(row[13:22]))
            if number_id >= len(ARMOR_NAMES):
                continue
            name = ARMOR_NAMES[number_id]
            if name == "not_armor":
                continue
            points = ((float(row[0] / scale), float(row[1] / scale)), (float(row[6] / scale), float(row[7] / scale)), (float(row[4] / scale), float(row[5] / scale)), (float(row[2] / scale), float(row[3] / scale)))
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            x, y = max(0.0, min(xs)), max(0.0, min(ys))
            box_w, box_h = max(xs) - x, max(ys) - y
            if box_w < 2.0 or box_h < 2.0:
                continue
            boxes.append([int(x), int(y), int(box_w), int(box_h)])
            confidences.append(confidence)
            light_strength = self._sample_light_strength(bgr, points)
            candidates.append(LocalDetection(name, ARMOR_COLORS[min(color_id, len(ARMOR_COLORS) - 1)], confidence, (x, y, box_w, box_h), points, light_strength))
        keep = cv2.dnn.NMSBoxes(boxes, confidences, score_threshold, 0.30)
        indices = [int(index) for index in keep.flatten()] if len(keep) else []
        return [candidates[index] for index in indices]

    @staticmethod
    def _sample_light_strength(bgr: Any, points: tuple[tuple[float, float], ...]) -> float:
        """采样装甲两侧灯条亮度，作为 extinguish 分类的二次防抖证据。"""
        import numpy as np  # type: ignore

        if len(points) != 4:
            return 0.0
        height, width = bgr.shape[:2]
        centers = (
            ((points[0][0] + points[3][0]) * 0.5, (points[0][1] + points[3][1]) * 0.5),
            ((points[1][0] + points[2][0]) * 0.5, (points[1][1] + points[2][1]) * 0.5),
        )
        samples: list[float] = []
        for x, y in centers:
            ix, iy = int(round(x)), int(round(y))
            patch = bgr[max(0, iy - 4):min(height, iy + 5), max(0, ix - 4):min(width, ix + 5)]
            if patch.size:
                samples.append(float(np.mean(np.max(patch, axis=2))) / 255.0)
        return sum(samples) / len(samples) if samples else 0.0


class LocalAlignedVision:
    """与客户端摄像头同帧的最新帧推理工作器，不占用第二个摄像头句柄。"""

    def __init__(self, model_path: Path, device: str = "AUTO") -> None:
        self.model_path = model_path
        self.device = device
        self.active_device = "initializing"
        self.running = False
        self.last_error = ""
        self.inference_fps = 0.0
        self.latency_ms = 0.0
        self._detector: TongjiYoloV5 | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending: tuple[int, int, int, Any] | None = None
        self._latest: tuple[int, int, int, int, list[dict[str, Any]], float, float] | None = None
        self._prediction = MultiArmorPrediction()
        self._last_nonempty_payload: list[dict[str, Any]] = []
        self._last_nonempty_ns = 0
        self.armor_count = 4
        self.radius_m = 0.23
        self.horizon_s = 0.06

    def configure(self, model_path: Path, device: str) -> None:
        if model_path == self.model_path and device == self.device:
            return
        self.stop()
        self.model_path = model_path
        self.device = device

    def configure_prediction(self, armor_count: int, radius_m: float, horizon_s: float) -> None:
        self.armor_count = max(2, min(4, int(armor_count)))
        self.radius_m = max(0.10, min(0.45, float(radius_m)))
        self.horizon_s = max(0.0, min(0.30, float(horizon_s)))

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, name="polar-tongji-local", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.8)
        self._thread = None
        self._detector = None

    def submit(self, sequence: int, width: int, height: int, bgr: Any) -> None:
        if not self.running:
            return
        with self._lock:
            self._pending = (sequence, width, height, bgr.copy())

    def latest(self, timeout_ms: float = 350.0) -> tuple[int, int, int, int, list[dict[str, Any]], float, float] | None:
        with self._lock:
            result = self._latest
        if result is None or time.time_ns() - result[0] > int(timeout_ms * 1e6):
            return None
        return result

    def _run(self) -> None:
        try:
            self._detector = TongjiYoloV5(self.model_path, self.device)
            self.active_device = self._detector.active_device
            while self.running:
                with self._lock:
                    pending = self._pending
                    self._pending = None
                if pending is None:
                    time.sleep(0.003)
                    continue
                sequence, width, height, bgr = pending
                started = time.perf_counter()
                detections = self._detector.detect(bgr)
                elapsed = max(0.0001, time.perf_counter() - started)
                payload = [item.payload(index, locked=index == 0) for index, item in enumerate(detections)]
                if detections:
                    primary = detections[0]
                    is_big = primary.name in ("one", "base")
                    plates = self._prediction.build(
                        primary.points, width, height, is_big=is_big,
                        armor_count=self.armor_count, radius_m=self.radius_m, horizon_s=self.horizon_s,
                    )
                    if plates:
                        payload = []
                        for plate in plates:
                            payload.append({
                                "id": plate.plate_id,
                                "name": primary.name,
                                "color": primary.color,
                                "confidence": primary.confidence if not plate.predicted else primary.confidence * 0.68,
                                "box": [min(point[0] for point in plate.points), min(point[1] for point in plate.points), max(point[0] for point in plate.points) - min(point[0] for point in plate.points), max(point[1] for point in plate.points) - min(point[1] for point in plate.points)],
                                "points": [list(point) for point in plate.points],
                                "back_points": [list(point) for point in plate.back_points],
                                "predicted": plate.predicted,
                                "plate_id": plate.plate_id,
                                "distance_m": plate.distance_m,
                                "depth_m": plate.depth_m,
                                "yaw_rad": plate.yaw_rad,
                                "locked": not plate.predicted,
                                "light_strength": primary.light_strength,
                                "light_state": "extinguished" if primary.color == "extinguish" else "lit",
                            })
                if payload:
                    self._last_nonempty_payload = payload
                    self._last_nonempty_ns = time.time_ns()
                elif self._last_nonempty_payload and time.time_ns() - self._last_nonempty_ns <= 300_000_000:
                    # 短暂漏检不让整车预测面瞬间消失；超过 300ms 仍会安全清空。
                    payload = self._last_nonempty_payload
                self.inference_fps = 1.0 / elapsed
                self.latency_ms = elapsed * 1000.0
                with self._lock:
                    self._latest = (time.time_ns(), sequence, width, height, payload, self.inference_fps, self.latency_ms)
        except Exception as error:
            self.last_error = str(error)
            self.active_device = "unavailable"
        finally:
            self.running = False
