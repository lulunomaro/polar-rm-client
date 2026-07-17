from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any


@dataclass
class CalibrationProfile:
    name: str
    width: int
    height: int
    board_columns: int
    board_rows: int
    square_size_mm: float
    camera_matrix: list[float]
    distort_coeffs: list[float]
    rms_error_px: float
    sample_count: int


class CameraCalibrationManager:
    """棋盘格采集、标定和同济桌面 YAML 导出。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.profile_dir = self.workspace / "rm_custom_client" / "calibrations"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._object_points: list[Any] = []
        self._image_points: list[Any] = []
        self._image_size: tuple[int, int] | None = None
        self.last_error = ""
        self.last_corner_count = 0
        self.profile: CalibrationProfile | None = None

    def profile_path(self, name: str) -> Path:
        safe = "".join(character if character.isalnum() or character in "-_" else "_" for character in name)
        return self.profile_dir / f"{safe or 'polar_webcam'}.json"

    def load(self, name: str) -> CalibrationProfile | None:
        path = self.profile_path(name)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            profile = CalibrationProfile(**raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.last_error = str(exc)
            return None
        self.profile = profile
        return profile

    def is_valid_for(self, width: int, height: int) -> bool:
        profile = self.profile
        return bool(profile and profile.sample_count >= 8 and profile.rms_error_px <= 2.0 and profile.width == width and profile.height == height)

    def capture(self, bgr: Any, columns: int, rows: int, square_size_mm: float) -> bool:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:  # pragma: no cover - optional local vision stack
            self.last_error = str(exc)
            return False
        if bgr is None:
            self.last_error = "当前没有可用于标定的摄像头帧"
            return False
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        board = (int(columns), int(rows))
        found, corners = cv2.findChessboardCornersSB(gray, board, flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
        if not found or corners is None:
            self.last_error = "未找到完整棋盘格，请调整距离、角度或曝光"
            return False
        width, height = int(gray.shape[1]), int(gray.shape[0])
        if self._image_size is not None and self._image_size != (width, height):
            self._object_points.clear()
            self._image_points.clear()
        self._image_size = (width, height)
        grid = np.zeros((board[0] * board[1], 3), np.float32)
        grid[:, :2] = np.mgrid[0:board[0], 0:board[1]].T.reshape(-1, 2)
        grid *= float(square_size_mm) / 1000.0
        self._object_points.append(grid)
        self._image_points.append(corners)
        self.last_corner_count = len(corners)
        self.last_error = ""
        return True

    def solve(self, name: str, columns: int, rows: int, square_size_mm: float) -> CalibrationProfile | None:
        try:
            import cv2  # type: ignore
        except Exception as exc:  # pragma: no cover - optional local vision stack
            self.last_error = str(exc)
            return None
        if self._image_size is None or len(self._image_points) < 8:
            self.last_error = "至少采集 8 组不同角度的完整棋盘格后才能标定"
            return None
        try:
            rms, matrix, distortion, _rvecs, _tvecs = cv2.calibrateCamera(self._object_points, self._image_points, self._image_size, None, None)
        except Exception as exc:
            self.last_error = str(exc)
            return None
        profile = CalibrationProfile(
            name=name,
            width=self._image_size[0],
            height=self._image_size[1],
            board_columns=int(columns),
            board_rows=int(rows),
            square_size_mm=float(square_size_mm),
            camera_matrix=[float(value) for value in matrix.reshape(-1)],
            distort_coeffs=[float(value) for value in distortion.reshape(-1)],
            rms_error_px=float(rms),
            sample_count=len(self._image_points),
        )
        self.profile = profile
        self.profile_path(name).write_text(json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8")
        self.last_error = ""
        return profile

    def export_tongji_yaml(self, name: str, device: str = "AUTO") -> Path | None:
        profile = self.profile or self.load(name)
        if profile is None:
            self.last_error = "没有可导出的标定 profile"
            return None
        base = self.workspace / "同济自瞄" / "sp_vision_25" / "configs" / "demo.yaml"
        target = self.workspace / "同济自瞄" / "sp_vision_25" / "configs" / f"{name}.yaml"
        if not base.exists():
            self.last_error = f"未找到同济基础配置：{base}"
            return None
        text = base.read_text(encoding="utf-8", errors="replace")
        values = {
            "camera_matrix": profile.camera_matrix,
            "distort_coeffs": profile.distort_coeffs,
            "R_camera2gimbal": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            "t_camera2gimbal": [0.0, 0.0, 0.0],
        }
        for key, value in values.items():
            encoded = ", ".join(f"{item:.12g}" for item in value)
            pattern = rf"(?m)^(\s*{re.escape(key)}\s*:\s*)\[[^\]]*\]"
            text, count = re.subn(pattern, rf"\1[{encoded}]", text, count=1)
            if count == 0:
                text += f"\n{key}: [{encoded}]\n"
        text, count = re.subn(r"(?m)^(\s*device\s*:\s*)\S+", rf"\1{device}", text, count=1)
        if count == 0:
            text += f"\ndevice: {device}\n"
        text += f"\n# POLAR desktop calibration profile: {profile.name}\n"
        text += f"# resolution: {profile.width}x{profile.height}, rms_error_px: {profile.rms_error_px:.4f}\n"
        target.write_text(text, encoding="utf-8")
        self.last_error = ""
        return target

    @property
    def sample_count(self) -> int:
        return len(self._image_points)
