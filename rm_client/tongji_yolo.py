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


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value)) if value >= 0 else math.exp(value) / (1.0 + math.exp(value))


def _sigmoid_array(values: Any) -> Any:
    """:func:`_sigmoid` 的向量化版，公式与分支逐条照抄。

    存在的理由：YOLOv5 一次前向给出 3×(80²+40²+20²) = **25200** 行，
    而原实现对每一行都调一次标量 ``_sigmoid``（``math.exp``），即使一个目标
    都没有也要 5.91 ms/帧 —— 而这段跑在单槽推理线程上，每 1 ms 都直接
    转化成帧龄。向量化后同一段是 0.26 ms（**23×**）。

    数值上必须逐位相同，所以：
      * 两个分支（``v >= 0`` 用 1/(1+e^-v)，否则用 e^v/(1+e^v)）原样保留，
        不为了快换成会溢出的单式；
      * 先转 float64 再算，与标量版 ``float(row[8])`` 的精度对齐；
      * 实测 30 万组（含 ±1e30、±709、±745 等极端值）与标量版**逐位相同**，
        200 万组上 0.70/0.80 两个判定阈值的结论翻转数为 0
        （见 tests/tongji_postprocess_parity_test.py）。
    """
    import numpy as np  # type: ignore

    vals = np.asarray(values, dtype=np.float64)
    out = np.empty(vals.shape, dtype=np.float64)
    positive = vals >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-vals[positive]))
    exp_neg = np.exp(vals[~positive])
    out[~positive] = exp_neg / (1.0 + exp_neg)
    return out


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


class LocalTargetSelector:
    """保持同一画面目标，避免检测列表顺序变化导致主目标来回跳。

    可选 ``fov_radius_x/y``：仅在画面中心椭圆内的检测可被选为自瞄主目标；
    圆外目标仍可由上层原样绘制 ESP，只是不会 lock。
    """

    def __init__(self) -> None:
        self.center: tuple[float, float] | None = None
        self.name = ""
        self.color = ""
        self.track_id = 0
        self.missed_frames = 0
        self.last_seen_at = 0.0
        self.fov_radius_x: float | None = None
        self.fov_radius_y: float | None = None

    def reset(self) -> None:
        self.center = None
        self.name = ""
        self.color = ""
        self.missed_frames = 0
        self.last_seen_at = 0.0

    def set_fov_radius(self, radius_x: float | None, radius_y: float | None = None) -> None:
        """Source-pixel FOV ellipse half-axes; None disables the FOV gate."""
        if radius_x is None or radius_x <= 0.0:
            self.fov_radius_x = None
            self.fov_radius_y = None
            return
        self.fov_radius_x = float(radius_x)
        self.fov_radius_y = float(radius_y if radius_y is not None and radius_y > 0.0 else radius_x)

    def _hold_limits(self) -> tuple[float, int]:
        """灭灯/过暗时延长关联，避免 track_id 跳变导致视觉击杀丢 lit 历史。"""
        if self.color == "extinguish" or (self.color and self.color not in ("red", "blue", "purple")):
            return 0.70, 8
        return 0.55, 6

    def mark_missed(self, *, max_seconds: float | None = None, max_frames: int | None = None) -> None:
        """只保留内部关联窗口；调用方仍然可以立即发布空检测列表。"""
        if self.center is None:
            return
        hold_s, hold_frames = self._hold_limits()
        if max_seconds is None:
            max_seconds = hold_s
        if max_frames is None:
            max_frames = hold_frames
        self.missed_frames += 1
        if self.missed_frames > max_frames or time.perf_counter() - self.last_seen_at > max_seconds:
            self.reset()

    def keep_alive(self) -> None:
        if self.center is not None:
            self.missed_frames = 0
            self.last_seen_at = time.perf_counter()

    def _in_fov(self, center: tuple[float, float], width: int, height: int) -> bool:
        rx = self.fov_radius_x
        ry = self.fov_radius_y
        if rx is None or ry is None:
            return True
        dx = (center[0] - width * 0.5) / max(1e-3, rx)
        dy = (center[1] - height * 0.5) / max(1e-3, ry)
        return (dx * dx + dy * dy) <= 1.0

    def select(self, detections: list[LocalDetection], width: int, height: int, *, keep_missing: bool = False) -> tuple[int, int] | None:
        if not detections:
            if not keep_missing:
                self.mark_missed()
            return None

        now = time.perf_counter()
        hold_s, hold_frames = self._hold_limits()
        if self.center is not None and (self.missed_frames > hold_frames or now - self.last_seen_at > hold_s):
            self.reset()

        centers = [
            (item.box[0] + item.box[2] * 0.5, item.box[1] + item.box[3] * 0.5)
            for item in detections
        ]
        in_fov = [index for index, center in enumerate(centers) if self._in_fov(center, width, height)]
        if not in_fov:
            # 圆外仍可有检测，但本帧不建立/保持自瞄锁定。
            if not keep_missing:
                self.mark_missed()
            return None

        diagonal = math.hypot(float(width), float(height))
        gate = max(48.0, diagonal * 0.16)
        candidate_index: int | None = None

        if self.center is not None:
            compatible = [
                index for index in in_fov
                for item in (detections[index],)
                if item.name == self.name
                and (
                    item.color == self.color
                    or item.color == "extinguish"
                    or self.color == "extinguish"
                    or item.light_strength <= 0.22
                    or (self.color in ("red", "blue") and item.color in ("red", "blue"))
                )
            ]
            nearby = [
                index for index in compatible
                if math.dist(centers[index], self.center) <= gate
            ]
            if nearby:
                candidate_index = min(nearby, key=lambda index: math.dist(centers[index], self.center))

        if candidate_index is None:
            # 初次锁定或旧目标离开 FOV：仅在圆内按置信度选主目标。
            candidate_index = max(in_fov, key=lambda index: detections[index].confidence)
            if self.center is not None:
                self.track_id += 1

        selected = detections[candidate_index]
        selected_center = centers[candidate_index]
        if self.center is None:
            self.track_id += 1
        # 亮→灭同目标：禁止因颜色切换递增 track_id（上面已匹配成功时 candidate 非 None）
        previous_center = self.center or selected_center
        self.center = (
            selected_center[0] * 0.38 + previous_center[0] * 0.62,
            selected_center[1] * 0.38 + previous_center[1] * 0.62,
        )
        self.name = selected.name
        self.color = selected.color
        self.missed_frames = 0
        self.last_seen_at = now
        return candidate_index, self.track_id


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

    def detect(self, bgr: Any, score_threshold: float = 0.70, min_confidence: float = 0.80) -> list[LocalDetection]:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        height, width = bgr.shape[:2]
        scale = min(640.0 / max(1, height), 640.0 / max(1, width))
        resized_w = max(1, int(width * scale))
        resized_h = max(1, int(height * scale))
        canvas = np.zeros((640, 640, 3), dtype=np.uint8)
        canvas[:resized_h, :resized_w] = cv2.resize(bgr, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        # 直接写进 C 连续的 NCHW 缓冲。
        #
        # 原来是 np.transpose(rgb.astype(np.float32)/255.0, (2,0,1))[None,...]，
        # 产出的张量 strides 是 (0, 4, 7680, 12) —— **非连续**。OpenVINO 拿到
        # 非连续输入要先 copy 一份再转成自己的布局，实测整条预处理链
        # 4.20 ms/帧（30fps 下占单核 12.6%）。改成预分配 C 连续缓冲直写后
        # 降到 0.665 ms（6.32×），像素值逐位不变（除法是 IEEE 精确舍入，
        # 换的只是数据摆放方式）。见 tests/tongji_postprocess_parity_test.py。
        tensor = np.empty((1, 3, 640, 640), dtype=np.float32)
        for channel in range(3):
            np.divide(rgb[:, :, channel], 255.0, out=tensor[0, channel])
        output = self.compiled({self.input.any_name: tensor})[self.output]
        rows = np.asarray(output, dtype=np.float32).reshape(-1, 22)

        # --- 先用向量化把 25200 行筛到个位数，再进 Python 循环 ---------------
        #
        # 原实现对每一行都做标量 sigmoid 才判断阈值，等于每帧 5.91 ms 花在
        # 25200 个几乎必然被丢掉的行上。改成：整批向量化算置信度 → 一次性
        # 取出过阈值的行 → 只对这几行做 argmax 与几何计算。
        #
        # 语义等价性：原来的 `confidence < score_threshold or
        # confidence < min_confidence` 就是 `confidence < max(...)`；
        # np.flatnonzero 返回的是**升序下标**，与原 for 循环的遍历顺序一致，
        # 因此 NMS 之前 boxes/confidences/candidates 的排列逐项不变。
        threshold = score_threshold if score_threshold > min_confidence else min_confidence
        all_confidences = _sigmoid_array(rows[:, 8])
        selected = np.flatnonzero(all_confidences >= threshold)
        if selected.size == 0:
            return []
        selected_rows = rows[selected]
        color_ids = np.argmax(selected_rows[:, 9:13], axis=1)
        number_ids = np.argmax(selected_rows[:, 13:22], axis=1)

        boxes: list[list[int]] = []
        confidences: list[float] = []
        candidates: list[LocalDetection] = []
        for position, row_index in enumerate(selected):
            row = selected_rows[position]
            confidence = float(all_confidences[row_index])
            color_id = int(color_ids[position])
            number_id = int(number_ids[position])
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
        if not boxes:
            return []
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


@dataclass
class _LocalLossLightProbe:
    target_id: int = -1
    label: str = "armor"
    points: tuple[tuple[float, float], ...] = ()
    baseline_strength: float = 0.0
    last_center: tuple[float, float] | None = None
    previous_center: tuple[float, float] | None = None
    last_seen_at: float = 0.0
    previous_seen_at: float = 0.0

    def reset(self) -> None:
        self.target_id = -1
        self.label = "armor"
        self.points = ()
        self.baseline_strength = 0.0
        self.last_center = None
        self.previous_center = None
        self.last_seen_at = 0.0
        self.previous_seen_at = 0.0

    def update(self, detection: LocalDetection, target_id: int) -> None:
        if target_id != self.target_id:
            self.reset()
            self.target_id = target_id
        now = time.perf_counter()
        center = (detection.box[0] + detection.box[2] * 0.5, detection.box[1] + detection.box[3] * 0.5)
        self.previous_center, self.previous_seen_at = self.last_center, self.last_seen_at
        self.last_center, self.last_seen_at = center, now
        self.points, self.label = detection.points, detection.name
        if detection.color in ("red", "blue") and detection.light_strength > 1e-6:
            self.baseline_strength = max(detection.light_strength, self.baseline_strength * 0.985)

    def sample_loss(self, bgr: Any, width: int, height: int, sequence: int) -> dict[str, Any] | None:
        if self.target_id < 0 or len(self.points) != 4 or self.last_center is None:
            return None
        if time.perf_counter() - self.last_seen_at > 8.0:
            self.reset()
            return None
        strength = TongjiYoloV5._sample_light_strength(bgr, self.points)
        margin_x, margin_y = width * 0.10, height * 0.10
        near_edge = self.last_center[0] <= margin_x or self.last_center[0] >= width - margin_x or self.last_center[1] <= margin_y or self.last_center[1] >= height - margin_y
        moving_fast = False
        if self.previous_center is not None and self.previous_seen_at > 0.0:
            elapsed = max(0.001, self.last_seen_at - self.previous_seen_at)
            moving_fast = math.dist(self.last_center, self.previous_center) / elapsed >= math.hypot(float(width), float(height)) * 0.28
        threshold = max(0.08, self.baseline_strength * 0.45)
        return {
            "target_id": self.target_id,
            "label": self.label,
            "light_strength": strength,
            "baseline_strength": self.baseline_strength,
            "probe_dark": self.baseline_strength > 0.0 and strength <= threshold,
            "moving_fast": moving_fast,
            "near_edge": near_edge,
            "sequence": sequence,
        }


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
        self._latest: tuple[int, int, int, int, list[dict[str, Any]], float, float, dict[str, Any] | None] | None = None
        self._prediction = MultiArmorPrediction()
        self._target_selector = LocalTargetSelector()
        self._loss_probe = _LocalLossLightProbe()
        self._held_payload: list[dict[str, Any]] = []
        self.armor_count = 4
        self.radius_m = 0.23
        self.horizon_s = 0.06
        self.retry_after = 0.0
        self._stop_event = threading.Event()
        self._pending_event = threading.Event()

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

    def configure_autoaim_fov(self, radius_x: float | None, radius_y: float | None = None) -> None:
        """Limit autoaim lock to a center ellipse in source pixels; ESP still lists all detections."""
        self._target_selector.set_fov_radius(radius_x, radius_y)

    def start(self) -> None:
        if self.running:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        if time.perf_counter() < self.retry_after:
            return
        self._stop_event = threading.Event()
        self._pending_event = threading.Event()
        self.running = True
        self._thread = threading.Thread(
            target=self._run,
            args=(self._stop_event, self._pending_event),
            name="polar-tongji-local",
            daemon=True,
        )
        try:
            self._thread.start()
        except Exception:
            self.running = False
            self._thread = None
            raise

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()
        self._pending_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.2)
        if thread is None or not thread.is_alive():
            self._thread = None
            self._detector = None
            with self._lock:
                self._pending = None

    def submit(self, sequence: int, width: int, height: int, bgr: Any) -> None:
        if not self.running:
            return
        with self._lock:
            self._pending = (sequence, width, height, bgr.copy())
        self._pending_event.set()

    def latest(self, timeout_ms: float = 350.0) -> tuple[int, int, int, int, list[dict[str, Any]], float, float, dict[str, Any] | None] | None:
        with self._lock:
            result = self._latest
        if result is None or time.time_ns() - result[0] > int(timeout_ms * 1e6):
            return None
        return result

    def _run(self, stop_event: threading.Event, pending_event: threading.Event) -> None:
        try:
            self._detector = TongjiYoloV5(self.model_path, self.device)
            self.active_device = self._detector.active_device
            while self.running and not stop_event.is_set():
                # Waiting for a latest-frame notification avoids the previous
                # 3 ms busy loop, which multiplied into 100+ hot threads when
                # a camera was missing or several UI smoke windows were built.
                if not pending_event.wait(0.25):
                    continue
                pending_event.clear()
                if stop_event.is_set():
                    break
                with self._lock:
                    pending = self._pending
                    self._pending = None
                if pending is None:
                    continue
                sequence, width, height, bgr = pending
                started = time.perf_counter()
                detections = self._detector.detect(bgr)
                elapsed = max(0.0001, time.perf_counter() - started)
                payload: list[dict[str, Any]] = []
                loss_evidence: dict[str, Any] | None = None
                pending_loss = self._loss_probe.sample_loss(bgr, width, height, sequence) if not detections else None
                keep_visible = bool(
                    pending_loss
                    and not pending_loss["probe_dark"]
                    and not pending_loss["moving_fast"]
                    and not pending_loss["near_edge"]
                )
                selected_info = self._target_selector.select(detections, width, height, keep_missing=keep_visible)
                if selected_info is not None:
                    selected_index, track_id = selected_info
                    primary = detections[selected_index]
                    self._loss_probe.update(primary, track_id)
                    primary_payload = primary.payload(0, locked=True)
                    primary_payload["id"] = track_id
                    primary_payload["track_id"] = track_id
                    primary_payload["target_id"] = track_id
                    payload.append(primary_payload)
                    for index, item in enumerate(detections):
                        if index != selected_index:
                            payload.append(item.payload(index, locked=False))
                    is_big = primary.name in ("one", "base")
                    plates = self._prediction.build(
                        primary.points, width, height, is_big=is_big,
                        armor_count=self.armor_count, radius_m=self.radius_m, horizon_s=self.horizon_s,
                        target_key=track_id,
                    )
                    if plates:
                        for plate in plates:
                            plate_payload = {
                                "id": track_id if not plate.predicted else plate.plate_id,
                                "target_id": track_id,
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
                            }
                            if plate.predicted:
                                payload.append(plate_payload)
                            else:
                                # 用滤波后的实体板替换原始角点，避免主板重复且让 Aim Face 也稳定。
                                payload[0].update(plate_payload)
                elif detections:
                    # FOV 内无主目标：仍发布全部检测供 3D ESP，全部 unlocked。
                    for index, item in enumerate(detections):
                        payload.append(item.payload(index, locked=False))
                    self._held_payload = []
                    self._prediction.reset()
                if not payload:
                    # 短暂漏检不让整车预测面瞬间消失；超过 300ms 仍会安全清空。
                    loss_evidence = pending_loss or self._loss_probe.sample_loss(bgr, width, height, sequence)
                    if keep_visible and self._held_payload:
                        payload = [dict(item) for item in self._held_payload]
                        payload[0]["light_strength"] = float(loss_evidence["light_strength"])
                        payload[0]["held"] = True
                        self._target_selector.keep_alive()
                    else:
                        self._held_payload = []
                        self._prediction.reset()
                if payload and selected_info is not None:
                    if primary.color in ("red", "blue"):
                        self._held_payload = [
                            dict(item)
                            for item in payload
                            if int(item.get("target_id", item.get("id", -1))) == track_id
                        ]
                    else:
                        self._held_payload = []
                self.inference_fps = 1.0 / elapsed
                self.latency_ms = elapsed * 1000.0
                with self._lock:
                    self._latest = (time.time_ns(), sequence, width, height, payload, self.inference_fps, self.latency_ms, loss_evidence)
        except Exception as error:
            self.last_error = str(error)
            self.active_device = "unavailable"
            # A missing/corrupt model must not spawn a new loader at every
            # 60 Hz UI tick. Retry slowly so a repaired file is still picked up.
            self.retry_after = time.perf_counter() + 5.0
        finally:
            self.running = False
            self._detector = None
            if self._thread is threading.current_thread():
                self._thread = None
