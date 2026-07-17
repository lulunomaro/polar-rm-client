"""【Legacy】本机 Python 简化同济管线（Lab 回退）。

默认请用 sp_vision_py 完整管线（tools/tongji_desktop_runner → polar_lab_runner）。
仅当 POLAR_TONGJI_PIPELINE=legacy 时使用本模块。

相对完整 sp_vision_py / C++ sp_vision_25：
  ✓ YOLO 检测 + Gestalt 灯条辅助
  ✓ PnP + 多板预测（MultiArmorPrediction）
  ✓ 简化整车跟踪（非 11 维 EKF）
  ✓ 弹道公式对齐 trajectory.cpp
  ✗ 完整 Target EKF / Aimer 选板 / Shooter

输出与 client 协议兼容的 command（err + yaw/pitch）。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

from .armor_vehicle import BIG_ARMOR_WIDTH_M, MultiArmorPrediction, SMALL_ARMOR_WIDTH_M

G = 9.7833  # 与同济 trajectory.cpp 一致
BULLET_SPEED = 23.0  # m/s Lab 默认


def _trajectory_pitch(v0: float, d: float, h: float) -> tuple[bool, float, float]:
    """无阻力弹道（对齐 sp_vision_25/tools/trajectory.cpp）：(ok, pitch_rad 抬头为正, fly_time_s)。"""
    if d < 0.05 or v0 < 5.0:
        return False, 0.0, 0.0
    a = G * d * d / (2.0 * v0 * v0)
    b = -d
    c = a + h
    delta = b * b - 4.0 * a * c
    if delta < 0:
        return False, 0.0, 0.0
    sqrt_d = math.sqrt(delta)
    tan1 = (-b + sqrt_d) / (2.0 * a)
    tan2 = (-b - sqrt_d) / (2.0 * a)
    p1 = math.atan(tan1)
    p2 = math.atan(tan2)
    c1 = math.cos(p1)
    c2 = math.cos(p2)
    if abs(c1) < 1e-6 or abs(c2) < 1e-6:
        return False, 0.0, 0.0
    t1 = d / (v0 * c1)
    t2 = d / (v0 * c2)
    if t1 < t2:
        pitch, fly = p1, t1
    else:
        pitch, fly = p2, t2
    if fly <= 0 or fly > 2.5:
        return False, 0.0, 0.0
    return True, pitch, fly


@dataclass
class TrackedTarget:
    x: float
    y: float
    z: float
    yaw: float
    yaw_rate: float
    vx: float
    vy: float
    vz: float
    last_t: float
    hits: int
    name: str
    is_big: bool
    color: str
    last_err_x: float = 0.0
    last_err_y: float = 0.0


class SimpleVehicleTracker:
    """单目标简化跟踪：xyz 低通 + yaw 转速估计 + 短时丢检预测。"""

    def __init__(self) -> None:
        self.target: TrackedTarget | None = None

    def update(
        self,
        xyz: tuple[float, float, float],
        yaw: float,
        name: str,
        is_big: bool,
        now: float,
        *,
        color: str = "unknown",
    ) -> TrackedTarget:
        x, y, z = xyz
        if self.target is None or now - self.target.last_t > 0.55:
            self.target = TrackedTarget(x, y, z, yaw, 0.0, 0.0, 0.0, 0.0, now, 1, name, is_big, color)
            return self.target
        t = self.target
        dt = max(0.001, now - t.last_t)
        a = 0.42
        nx = t.x * (1 - a) + x * a
        ny = t.y * (1 - a) + y * a
        nz = t.z * (1 - a) + z * a
        # 速度 EMA
        ivx, ivy, ivz = (nx - t.x) / dt, (ny - t.y) / dt, (nz - t.z) / dt
        t.vx = t.vx * 0.65 + ivx * 0.35
        t.vy = t.vy * 0.65 + ivy * 0.35
        t.vz = t.vz * 0.65 + ivz * 0.35
        t.x, t.y, t.z = nx, ny, nz
        dyaw = (yaw - t.yaw + math.pi) % (2 * math.pi) - math.pi
        inst = dyaw / dt
        t.yaw_rate = t.yaw_rate * 0.72 + inst * 0.28
        t.yaw = (t.yaw + dyaw + math.pi) % (2 * math.pi) - math.pi
        t.last_t = now
        t.hits = min(200, t.hits + 1)
        t.name = name
        t.is_big = is_big
        t.color = color
        return t

    def coast(self, now: float) -> TrackedTarget | None:
        """无检测时外推；超时返回 None。"""
        if self.target is None:
            return None
        t = self.target
        if now - t.last_t > 0.35:
            return None
        dt = now - t.last_t
        return TrackedTarget(
            t.x + t.vx * dt,
            t.y + t.vy * dt,
            t.z + t.vz * dt,
            t.yaw + t.yaw_rate * dt,
            t.yaw_rate,
            t.vx,
            t.vy,
            t.vz,
            t.last_t,
            t.hits,
            t.name,
            t.is_big,
            t.color,
            t.last_err_x,
            t.last_err_y,
        )

    def predict_xyz(self, dt: float) -> tuple[float, float, float] | None:
        if self.target is None:
            return None
        t = self.target
        x = t.x + t.vx * dt
        y = t.y + t.vy * dt
        z = t.z + t.vz * dt
        if abs(t.yaw_rate) > 0.6:
            r = 0.23 if not t.is_big else 0.28
            # 装甲绕车体竖直轴：切向位移
            x += -r * math.sin(t.yaw) * t.yaw_rate * dt * 0.55
            z += -r * math.cos(t.yaw) * t.yaw_rate * dt * 0.55
        return x, y, z


class TongjiLabPipeline:
    """检测结果 → PnP/跟踪/弹道 → 瞄准误差。"""

    def __init__(self, bullet_speed: float = BULLET_SPEED, enemy_color: str = "any") -> None:
        self.predictor = MultiArmorPrediction()
        self.tracker = SimpleVehicleTracker()
        self.bullet_speed = float(bullet_speed)
        self.latency_s = 0.045  # OBS+推理延迟估计
        self.enemy_color = enemy_color  # any | red | blue
        self._lock_name: str | None = None
        self._lock_until = 0.0

    def process(
        self,
        armors: list[dict[str, Any]],
        frame_w: int,
        frame_h: int,
        *,
        aa_on: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any], list[dict[str, Any]]]:
        """
        返回 (primary, command, armors_out)。
        armors_out 可含 predicted 板供 ESP。
        """
        now = time.perf_counter()
        filtered = self._filter_enemy(armors)
        if not filtered:
            coast = self.tracker.coast(now)
            if coast is not None and aa_on:
                return self._command_from_xyz(
                    (coast.x, coast.y, coast.z),
                    coast,
                    frame_w,
                    frame_h,
                    aa_on,
                    name=coast.name,
                    is_big=coast.is_big,
                    armors_out=[],
                    synthetic=True,
                )
            return None, {"control": False, "lab_aim": False, "fire": False, "err_x": 0.0, "err_y": 0.0}, []

        primary = self._select_primary(filtered, frame_w, frame_h, now)
        pts = primary.get("points") or []
        if len(pts) < 4:
            return primary, {"control": False, "lab_aim": False, "fire": False, "err_x": 0.0, "err_y": 0.0}, armors

        points = tuple((float(p[0]), float(p[1])) for p in pts[:4])
        name = str(primary.get("name", "three"))
        is_big = name in ("one", "base")
        color = str(primary.get("color", "unknown"))
        plates = self.predictor.build(
            points, frame_w, frame_h, is_big=is_big, armor_count=4, radius_m=0.23, horizon_s=0.10
        )
        xyz = self._pnp_xyz(points, frame_w, frame_h, is_big)
        if xyz is None:
            # 像素中心回退 + 轻微预瞄
            cx = sum(p[0] for p in points) / 4.0
            cy = sum(p[1] for p in points) / 4.0
            err_x = ((cx / frame_w) - 0.5) * 2.0
            err_y = ((cy / frame_h) - 0.5) * 2.0
            cmd = self._cmd(aa_on, err_x, err_y, 0.0, 0.0)
            primary = dict(primary)
            primary["locked"] = True
            primary["aim"] = True
            return primary, cmd, armors

        yaw0 = plates[0].yaw_rad if plates else 0.0
        tracked = self.tracker.update(xyz, yaw0, name, is_big, now, color=color)
        return self._command_from_xyz(
            xyz,
            tracked,
            frame_w,
            frame_h,
            aa_on,
            name=name,
            is_big=is_big,
            armors_out=armors,
            plates=plates,
            primary_seed=primary,
        )

    def _command_from_xyz(
        self,
        xyz: tuple[float, float, float],
        tracked: TrackedTarget,
        frame_w: int,
        frame_h: int,
        aa_on: bool,
        *,
        name: str,
        is_big: bool,
        armors_out: list[dict[str, Any]],
        plates: list[Any] | None = None,
        primary_seed: dict[str, Any] | None = None,
        synthetic: bool = False,
    ) -> tuple[dict[str, Any] | None, dict[str, Any], list[dict[str, Any]]]:
        # 弹道迭代：预估飞行时间 → 预测位置 → 再解弹道
        aim_xyz = xyz
        fly = 0.06
        pitch_ball = 0.0
        for _ in range(4):
            pred = self.tracker.predict_xyz(self.latency_s + fly)
            if pred is None:
                break
            ax, ay, az = pred
            d = math.hypot(ax, az)
            h = -ay
            ok, pitch_ball, fly_new = _trajectory_pitch(self.bullet_speed, d, h)
            if not ok:
                fly = d / max(8.0, self.bullet_speed * 0.9)
            else:
                fly = fly_new
            aim_xyz = (ax, ay, az)

        ax, ay, az = aim_xyz
        # 相机光轴误差：yaw 右正，pitch 抬头正
        yaw = math.atan2(ax, max(0.05, az))
        # 弹道 pitch 修正：需要抬头到 pitch_ball，当前视线 pitch_line
        pitch_line = math.atan2(-ay, math.hypot(ax, az))
        err_pitch = pitch_ball - 0.0 if abs(pitch_ball) > 1e-6 else pitch_line
        # 实际控制：先对齐视线再加弹道差
        if abs(pitch_ball) > 1e-6:
            err_pitch = pitch_ball  # 目标在相机系，理想瞄准 pitch
            # 当前枪口假设对准光轴 0 → 需要 pitch_ball；同时目标不在中心时叠加视线
            err_pitch = pitch_line + (pitch_ball - pitch_line) * 0.85
        else:
            err_pitch = pitch_line
        err_yaw = yaw

        f = frame_w / (2.0 * math.tan(math.radians(70.0) / 2.0))
        u = frame_w * 0.5 + f * (ax / max(0.05, az))
        v = frame_h * 0.5 + f * (ay / max(0.05, az))
        # 弹道：pitch 抬头 → 像素向上（v 减小）
        v_ball = v - f * math.tan(pitch_ball - pitch_line) if abs(pitch_ball) > 1e-6 else v
        err_x_px = ((u / frame_w) - 0.5) * 2.0
        err_y_px = ((v_ball / frame_h) - 0.5) * 2.0
        # 角误差归一（约 ±20° / ±14° 饱和）
        err_x = 0.40 * err_x_px + 0.60 * max(-1.5, min(1.5, err_yaw / 0.35))
        err_y = 0.40 * err_y_px + 0.60 * max(-1.5, min(1.5, err_pitch / 0.25))
        # 软限幅，避免过冲
        err_x = max(-1.2, min(1.2, err_x))
        err_y = max(-1.2, min(1.2, err_y))
        tracked.last_err_x, tracked.last_err_y = err_x, err_y

        cmd = self._cmd(aa_on, err_x, err_y, err_yaw, err_pitch)
        if primary_seed is not None:
            primary = dict(primary_seed)
        else:
            primary = {
                "id": 0,
                "name": name,
                "color": tracked.color,
                "confidence": 0.55,
                "box": [frame_w * 0.45, frame_h * 0.45, frame_w * 0.1, frame_h * 0.08],
                "points": [],
                "source": "coast",
            }
        primary["locked"] = True
        primary["aim"] = True
        primary["distance_m"] = float(math.sqrt(ax * ax + ay * ay + az * az))
        primary["depth_m"] = float(az)
        primary["yaw_rad"] = float(tracked.yaw)
        if synthetic:
            primary["predicted"] = True

        out = list(armors_out)
        if plates:
            for plate in plates[1:]:
                out.append(
                    {
                        "id": plate.plate_id,
                        "name": name,
                        "color": "predicted",
                        "confidence": 0.9,
                        "points": [list(p) for p in plate.points],
                        "back_points": [list(p) for p in plate.back_points],
                        "locked": False,
                        "predicted": True,
                        "aim": False,
                        "distance_m": plate.distance_m,
                        "depth_m": plate.depth_m,
                    }
                )
        return primary, cmd, out

    def _filter_enemy(self, armors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.enemy_color in ("", "any", "all"):
            return [a for a in armors if str(a.get("color", "")) != "extinguish"]
        want = self.enemy_color.lower()
        kept = [a for a in armors if str(a.get("color", "")).lower() == want]
        return kept if kept else [a for a in armors if str(a.get("color", "")) != "extinguish"]

    def _select_primary(self, armors: list[dict[str, Any]], fw: int, fh: int, now: float) -> dict[str, Any]:
        def score(a: dict[str, Any]) -> float:
            conf = float(a.get("confidence", 0.5) or 0.5)
            box = a.get("box") or [0, 0, 10, 10]
            area = max(1.0, float(box[2]) * float(box[3]))
            cx = float(box[0]) + float(box[2]) * 0.5
            cy = float(box[1]) + float(box[3]) * 0.5
            # 靠近画面中心优先
            center_bias = 1.0 - 0.35 * math.hypot((cx / fw) - 0.5, (cy / fh) - 0.5)
            name = str(a.get("name", ""))
            lock_bonus = 1.35 if self._lock_name and name == self._lock_name and now < self._lock_until else 1.0
            src = str(a.get("source", ""))
            src_bonus = 1.15 if "lightbar" in src else 1.0
            return conf * math.sqrt(area) * center_bias * lock_bonus * src_bonus

        primary = max(armors, key=score)
        self._lock_name = str(primary.get("name", ""))
        self._lock_until = now + 0.45
        return primary

    def _pnp_xyz(self, points: tuple, fw: int, fh: int, is_big: bool) -> tuple[float, float, float] | None:
        width_m = BIG_ARMOR_WIDTH_M if is_big else SMALL_ARMOR_WIDTH_M
        matrix = MultiArmorPrediction._camera_matrix(fw, fh)
        solved = self.predictor._solve(points, width_m, matrix)
        if solved is None:
            return None
        rvec, tvec, err = solved
        if err > 12.0:
            return None
        t = tvec.reshape(3)
        return float(t[0]), float(t[1]), float(t[2])

    @staticmethod
    def _cmd(aa_on: bool, err_x: float, err_y: float, yaw: float, pitch: float) -> dict[str, Any]:
        active = bool(aa_on)
        return {
            "control": active,
            "lab_aim": active,
            "fire": False,
            "yaw_rad": float(yaw),
            "pitch_rad": float(pitch),
            "err_x": float(err_x),
            "err_y": float(err_y),
            "pipeline": "tongji_lab_v1",
        }
