"""Gestalt 游戏装甲灯条辅助检测。

同济 yolov5 在真实 RM 上训练，对 Gestalt 画面置信度常 <0.15。
本模块用红/蓝高亮灯条配对补全框与四点，供 Lab 第 1 层瞄准使用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LightbarArmor:
    name: str
    color: str
    confidence: float
    box: tuple[float, float, float, float]
    points: tuple[tuple[float, float], ...]
    light_strength: float

    def payload(self, index: int, *, locked: bool = False) -> dict[str, Any]:
        return {
            "id": index,
            "name": self.name,
            "color": self.color,
            "confidence": self.confidence,
            "box": list(self.box),
            "points": [list(p) for p in self.points],
            "locked": locked,
            "light_strength": self.light_strength,
            "light_state": "lit",
            "source": "lightbar",
        }


def _color_mask(bgr: Any, color: str) -> Any:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    if color == "blue":
        return cv2.inRange(hsv, (90, 80, 90), (130, 255, 255))
    # red wraps hue
    m1 = cv2.inRange(hsv, (0, 70, 90), (12, 255, 255))
    m2 = cv2.inRange(hsv, (165, 70, 90), (180, 255, 255))
    return cv2.bitwise_or(m1, m2)


def _bar_candidates(mask: Any, min_h: int = 8) -> list[tuple[float, float, float, float, float]]:
    """返回 (cx, cy, w, h, area) 竖长灯条。"""
    import cv2  # type: ignore

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bars: list[tuple[float, float, float, float, float]] = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if h < min_h or w < 1:
            continue
        ratio = h / max(1.0, float(w))
        if ratio < 1.2:  # 偏竖
            continue
        if h > mask.shape[0] * 0.55:
            continue
        area = float(cv2.contourArea(cnt))
        if area < 12:
            continue
        bars.append((x + w * 0.5, y + h * 0.5, float(w), float(h), area))
    bars.sort(key=lambda b: b[0])
    return bars


def detect_lightbar_armors(
    bgr: Any,
    *,
    colors: tuple[str, ...] = ("red", "blue"),
    max_pair_gap_ratio: float = 0.28,
) -> list[LightbarArmor]:
    """配对左右灯条 → 装甲四点。"""
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    if bgr is None or getattr(bgr, "size", 0) == 0:
        return []
    h_img, w_img = bgr.shape[:2]
    results: list[LightbarArmor] = []
    for color in colors:
        mask = _color_mask(bgr, color)
        # 也并入高亮通道（游戏灯条常过曝偏白）
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        bright = cv2.inRange(gray, 200, 255)
        mask = cv2.bitwise_or(mask, cv2.bitwise_and(bright, _color_mask(bgr, color)))
        bars = _bar_candidates(mask, min_h=max(8, h_img // 60))
        used = set()
        for i, left in enumerate(bars):
            if i in used:
                continue
            lx, ly, lw, lh, la = left
            best_j = -1
            best_score = -1.0
            for j in range(i + 1, len(bars)):
                if j in used:
                    continue
                rx, ry, rw, rh, ra = bars[j]
                gap = rx - lx
                if gap < max(lh, rh) * 0.6:
                    continue
                if gap > w_img * max_pair_gap_ratio:
                    continue
                # 高度/垂直对齐
                h_ratio = min(lh, rh) / max(lh, rh)
                if h_ratio < 0.45:
                    continue
                dy = abs(ly - ry) / max(lh, rh)
                if dy > 0.85:
                    continue
                # 装甲宽高比约 1.5~4
                armor_w = gap
                armor_h = (lh + rh) * 0.5
                ar = armor_w / max(1.0, armor_h)
                if ar < 1.1 or ar > 5.5:
                    continue
                score = h_ratio * (1.0 - min(1.0, dy)) * min(la, ra) ** 0.5
                if score > best_score:
                    best_score = score
                    best_j = j
            if best_j < 0:
                continue
            used.add(i)
            used.add(best_j)
            rx, ry, rw, rh, ra = bars[best_j]
            # 四点：左上 右上 右下 左下（与同济约定接近）
            top = min(ly - lh * 0.5, ry - rh * 0.5)
            bot = max(ly + lh * 0.5, ry + rh * 0.5)
            left_x = lx - lw * 0.35
            right_x = rx + rw * 0.35
            # 略向内收，对准灯条内侧板面
            inset = (right_x - left_x) * 0.06
            left_x += inset
            right_x -= inset
            points = (
                (float(left_x), float(top)),
                (float(right_x), float(top)),
                (float(right_x), float(bot)),
                (float(left_x), float(bot)),
            )
            x0 = min(p[0] for p in points)
            y0 = min(p[1] for p in points)
            bw = max(p[0] for p in points) - x0
            bh = max(p[1] for p in points) - y0
            if bw < 10 or bh < 6:
                continue
            # 置信：灯条面积与对齐
            conf = float(min(0.92, 0.35 + 0.0015 * (la + ra) + 0.25 * min(1.0, best_score / 80.0)))
            light = float(min(1.0, (la + ra) / 400.0))
            results.append(
                LightbarArmor(
                    name="three",  # 灯条无法读号，默认 infantry
                    color=color,
                    confidence=conf,
                    box=(x0, y0, bw, bh),
                    points=points,
                    light_strength=light,
                )
            )
    # NMS by IoU
    results = _nms(results, 0.35)
    return results


def refine_yolo_with_lightbar(bgr: Any, box: tuple[float, float, float, float], color: str) -> tuple[tuple[float, float], ...] | None:
    """在 YOLO 框附近用灯条重估四点；失败返回 None。"""
    import cv2  # type: ignore

    x, y, w, h = box
    pad = max(w, h) * 0.55
    x0 = max(0, int(x - pad))
    y0 = max(0, int(y - pad))
    x1 = min(bgr.shape[1], int(x + w + pad))
    y1 = min(bgr.shape[0], int(y + h + pad))
    roi = bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    local = detect_lightbar_armors(roi, colors=(color if color in ("red", "blue") else "red",))
    if not local:
        # 尝试另一色
        local = detect_lightbar_armors(roi, colors=("red", "blue"))
    if not local:
        return None
    best = max(local, key=lambda a: a.confidence * a.box[2] * a.box[3])
    return tuple((p[0] + x0, p[1] + y0) for p in best.points)


def _nms(items: list[LightbarArmor], iou_thr: float) -> list[LightbarArmor]:
    if not items:
        return []
    ordered = sorted(items, key=lambda a: a.confidence, reverse=True)
    kept: list[LightbarArmor] = []

    def iou(a: LightbarArmor, b: LightbarArmor) -> float:
        ax, ay, aw, ah = a.box
        bx, by, bw, bh = b.box
        x1, y1 = max(ax, bx), max(ay, by)
        x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        union = aw * ah + bw * bh - inter
        return inter / union if union > 1e-6 else 0.0

    for item in ordered:
        if all(iou(item, k) < iou_thr for k in kept):
            kept.append(item)
    return kept


def _iou_box(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 1e-6 else 0.0


def merge_detections(
    yolo_payloads: list[dict[str, Any]],
    lightbar: list[LightbarArmor],
    bgr: Any,
    *,
    yolo_min_conf: float = 0.08,
) -> list[dict[str, Any]]:
    """合并 YOLO + 灯条；YOLO 命中则尽量灯条 refine 角点并抬置信度。"""
    out: list[dict[str, Any]] = []
    used_lb: set[int] = set()
    for det in yolo_payloads:
        conf = float(det.get("confidence", 0.0) or 0.0)
        if conf < yolo_min_conf:
            continue
        item = dict(det)
        box = item.get("box") or [0, 0, 10, 10]
        box_t = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
        color = str(item.get("color", "red"))
        refined = refine_yolo_with_lightbar(bgr, box_t, color)
        # 全局灯条 IoU 匹配（ROI refine 失败时仍可借角点）
        best_j, best_iou = -1, 0.0
        for j, lb in enumerate(lightbar):
            iou = _iou_box(box_t, lb.box)
            if iou > best_iou:
                best_iou, best_j = iou, j
        if refined is not None:
            item["points"] = [list(p) for p in refined]
            xs = [p[0] for p in refined]
            ys = [p[1] for p in refined]
            item["box"] = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
            item["confidence"] = min(0.95, max(conf + 0.35, 0.55))
            item["source"] = "yolo+lightbar"
            if best_j >= 0:
                used_lb.add(best_j)
        elif best_j >= 0 and best_iou > 0.15:
            lb = lightbar[best_j]
            item["points"] = [list(p) for p in lb.points]
            item["box"] = list(lb.box)
            item["confidence"] = min(0.95, max(conf + 0.40, float(lb.confidence) * 0.9))
            item["light_strength"] = lb.light_strength
            item["source"] = "yolo+lightbar"
            used_lb.add(best_j)
        else:
            # 域偏移：低置信 YOLO 仍可用，略抬高便于跟踪粘性
            item["confidence"] = min(0.55, conf + 0.12)
            item["source"] = "yolo"
        out.append(item)
    for j, lb in enumerate(lightbar):
        if j in used_lb:
            continue
        skip = any(
            _iou_box(lb.box, (float(eb[0]), float(eb[1]), float(eb[2]), float(eb[3]))) > 0.3
            for existing in out
            for eb in [existing.get("box") or [0, 0, 1, 1]]
        )
        if not skip:
            out.append(lb.payload(100 + j))
    return out
