from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any


ARMOR_THICKNESS_M = 0.035
SMALL_ARMOR_WIDTH_M = 0.135
BIG_ARMOR_WIDTH_M = 0.230
ARMOR_HEIGHT_M = 0.056


@dataclass(frozen=True)
class ArmorPlate3D:
    points: tuple[tuple[float, float], ...]
    back_points: tuple[tuple[float, float], ...]
    predicted: bool
    plate_id: int
    distance_m: float
    depth_m: float
    yaw_rad: float


class MultiArmorPrediction:
    """桌面相机坐标系下的 PnP 装甲厚度与整车多板预测。"""

    def __init__(self) -> None:
        self._last_yaw = 0.0
        self._last_time = 0.0
        self._yaw_rate = 0.0
        self._stable_frames = 0

    @staticmethod
    def _camera_matrix(width: int, height: int) -> Any:
        import numpy as np  # type: ignore

        focal = width / (2.0 * math.tan(math.radians(70.0) / 2.0))
        return np.array([[focal, 0.0, width / 2.0], [0.0, focal, height / 2.0], [0.0, 0.0, 1.0]], dtype=np.float64)

    @staticmethod
    def _object_points(width_m: float, thickness_m: float = 0.0) -> Any:
        import numpy as np  # type: ignore

        return np.array(
            [
                [-width_m / 2.0, -ARMOR_HEIGHT_M / 2.0, thickness_m],
                [width_m / 2.0, -ARMOR_HEIGHT_M / 2.0, thickness_m],
                [width_m / 2.0, ARMOR_HEIGHT_M / 2.0, thickness_m],
                [-width_m / 2.0, ARMOR_HEIGHT_M / 2.0, thickness_m],
            ],
            dtype=np.float64,
        )

    @staticmethod
    def _project(points: Any, rvec: Any, tvec: Any, matrix: Any) -> tuple[tuple[float, float], ...]:
        import cv2  # type: ignore

        projected, _ = cv2.projectPoints(points, rvec, tvec, matrix, None)
        return tuple((float(point[0][0]), float(point[0][1])) for point in projected)

    def _solve(self, points: tuple[tuple[float, float], ...], width_m: float, matrix: Any) -> tuple[Any, Any, float] | None:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        image = np.asarray(points, dtype=np.float64)
        obj = self._object_points(width_m)
        candidates: list[tuple[Any, Any, float]] = []
        for order in ((0, 1, 2, 3), (1, 2, 3, 0), (3, 0, 1, 2), (0, 3, 2, 1)):
            image_ordered = image[list(order)]
            for flag in (cv2.SOLVEPNP_IPPE, cv2.SOLVEPNP_ITERATIVE):
                try:
                    ok, rvec, tvec = cv2.solvePnP(obj, image_ordered, matrix, None, flags=flag)
                except cv2.error:
                    continue
                depth = float(tvec.reshape(3)[2])
                if not ok or depth < 0.2 or depth > 15.0:
                    continue
                projected = self._project(obj, rvec, tvec, matrix)
                error = sum(math.dist(projected[index], tuple(image_ordered[index])) for index in range(4)) / 4.0
                candidates.append((rvec, tvec, error))
        return min(candidates, key=lambda item: item[2]) if candidates else None

    def build(
        self,
        points: tuple[tuple[float, float], ...],
        frame_width: int,
        frame_height: int,
        *,
        is_big: bool = False,
        armor_count: int = 4,
        radius_m: float = 0.23,
        horizon_s: float = 0.06,
        force_multi: bool = False,
    ) -> list[ArmorPlate3D]:
        """PnP 单板 → 厚度后脸 + 绕车体竖直轴的多装甲板预测（同济整车几何）。

        force_multi=True：client 即时 ESP 扩板，不等待稳定帧。
        """
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        width_m = BIG_ARMOR_WIDTH_M if is_big else SMALL_ARMOR_WIDTH_M
        matrix = self._camera_matrix(frame_width, frame_height)
        solved = self._solve(points, width_m, matrix)
        if solved is None:
            self._stable_frames = 0
            return []
        rvec, tvec, error = solved
        if error > 8.0:
            self._stable_frames = 0
            return []
        rotation, _ = cv2.Rodrigues(rvec)
        normal = rotation[:, 2]
        if normal[2] > 0:
            normal = -normal
        yaw = math.atan2(float(normal[0]), float(-normal[2]))
        now = time.perf_counter()
        if self._last_time:
            delta = (yaw - self._last_yaw + math.pi) % (2.0 * math.pi) - math.pi
            instant_rate = delta / max(0.001, now - self._last_time)
            self._yaw_rate = self._yaw_rate * 0.72 + instant_rate * 0.28
        self._last_yaw, self._last_time = yaw, now
        self._stable_frames = min(12, self._stable_frames + 1)

        front_projected = self._project(self._object_points(width_m), rvec, tvec, matrix)
        back_projected = self._project(self._object_points(width_m, -ARMOR_THICKNESS_M), rvec, tvec, matrix)
        # 检测前脸比 PnP 回投影稳定，将后脸做同中心平移与它对齐。
        detected_center = np.mean(np.asarray(points), axis=0)
        projected_center = np.mean(np.asarray(front_projected), axis=0)
        shift = detected_center - projected_center
        back_visible = tuple((point[0] + float(shift[0]), point[1] + float(shift[1])) for point in back_projected)
        distance = float(np.linalg.norm(tvec))
        plates = [ArmorPlate3D(points, back_visible, False, 0, distance, float(tvec.reshape(3)[2]), yaw)]
        if (not force_multi and self._stable_frames < 3) or armor_count < 2:
            return plates

        center = tvec.reshape(3) - radius_m * normal.reshape(3)
        for index in range(1, armor_count):
            angle = index * 2.0 * math.pi / armor_count + self._yaw_rate * horizon_s
            rotate_y, _ = cv2.Rodrigues(np.array([0.0, angle, 0.0], dtype=np.float64))
            plate_rotation = rotate_y @ rotation
            plate_position = center + rotate_y @ (tvec.reshape(3) - center)
            plate_rvec, _ = cv2.Rodrigues(plate_rotation)
            plate_tvec = plate_position.reshape(3, 1)
            front = self._project(self._object_points(width_m), plate_rvec, plate_tvec, matrix)
            back = self._project(self._object_points(width_m, -ARMOR_THICKNESS_M), plate_rvec, plate_tvec, matrix)
            if not all(-frame_width * 0.35 < point[0] < frame_width * 1.35 and -frame_height * 0.35 < point[1] < frame_height * 1.35 for point in front):
                continue
            plate_normal = plate_rotation[:, 2]
            plate_yaw = math.atan2(float(plate_normal[0]), float(-plate_normal[2]))
            plates.append(ArmorPlate3D(front, back, True, index, float(np.linalg.norm(plate_tvec)), float(plate_tvec.reshape(3)[2]), plate_yaw))
        return plates
