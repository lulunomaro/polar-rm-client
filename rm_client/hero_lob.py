"""Fail-closed ballistic decision support for the RoboMaster Hero robot.

This planner deliberately never emits a fire or gimbal command.  It produces
preview angles and an explicit arm/readiness state so an operator can validate
radar localisation, yaw alignment and pitch calibration before using the
existing lower-controller path.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

from .settings_access import raw_value


@dataclass(frozen=True, slots=True)
class LobSolution:
    reachable: bool
    arc: str
    distance_m: float
    height_delta_m: float
    muzzle_speed_mps: float
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    flight_time_s: float = 0.0
    iterations: int = 0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LobReadiness:
    ready: bool
    reasons: tuple[str, ...]
    telemetry_age_s: float

    @property
    def summary(self) -> str:
        return "READY" if self.ready else "BLOCKED: " + ", ".join(self.reasons)


class HeroLobPlanner:
    """Closed-form vacuum model plus RoboMaster-specific safety gates."""

    @staticmethod
    def normalize_degrees(value: float) -> float:
        """Normalize an angular error to ``[-180, 180)``."""

        return (float(value) + 180.0) % 360.0 - 180.0

    @staticmethod
    def quaternion_roll_deg(quaternion: Any) -> float:
        """Return roll for the PDL1 ``(x, y, z, w)`` quaternion."""

        try:
            x, y, z, w = (float(value) for value in quaternion)
        except (TypeError, ValueError):
            return math.inf
        values = (x, y, z, w)
        if not all(math.isfinite(value) for value in values):
            return math.inf
        sinr_cosp = 2.0 * (w * x + y * z)
        cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
        return math.degrees(math.atan2(sinr_cosp, cosr_cosp))

    @staticmethod
    def solve(
        distance_m: float,
        height_delta_m: float,
        muzzle_speed_mps: float,
        *,
        arc: str = "High",
        gravity_mps2: float = 9.80665,
        bearing_deg: float = 0.0,
        yaw_offset_deg: float = 0.0,
        pitch_offset_deg: float = 0.0,
        muzzle_length_m: float = 0.0,
        max_iterations: int = 12,
    ) -> LobSolution:
        x = float(distance_m)
        y = float(height_delta_m)
        speed = float(muzzle_speed_mps)
        gravity = float(gravity_mps2)
        arc_name = "Low" if str(arc).lower().startswith("low") else "High"
        muzzle_length = max(0.0, float(muzzle_length_m))
        values = (
            x,
            y,
            speed,
            gravity,
            bearing_deg,
            yaw_offset_deg,
            pitch_offset_deg,
            muzzle_length,
        )
        if not all(math.isfinite(value) for value in values):
            return LobSolution(False, arc_name, x, y, speed, reason="non-finite input")
        if x <= 0.05:
            return LobSolution(False, arc_name, x, y, speed, reason="distance too small")
        if speed <= 0.1 or gravity <= 0.1:
            return LobSolution(False, arc_name, x, y, speed, reason="invalid speed/gravity")
        speed2 = speed * speed

        def solve_theta(horizontal: float, vertical: float) -> float | None:
            if horizontal <= 0.05:
                return None
            discriminant = speed2 * speed2 - gravity * (
                gravity * horizontal * horizontal + 2.0 * vertical * speed2
            )
            if discriminant < 0.0:
                return None
            root = math.sqrt(max(0.0, discriminant))
            tangent = (
                (speed2 + root) / (gravity * horizontal)
                if arc_name == "High"
                else (speed2 - root) / (gravity * horizontal)
            )
            return math.atan(tangent)

        theta = solve_theta(x, y)
        if theta is None:
            return LobSolution(False, arc_name, x, y, speed, reason="target is outside ballistic envelope")

        # ITL's Hero-lob calibration notes call out that changing pitch moves
        # the muzzle exit itself.  Solve that coupling iteratively instead of
        # treating the projectile as if it always left the gimbal pivot.
        iterations = 0
        effective_x = x
        effective_y = y
        for iterations in range(1, max(1, int(max_iterations)) + 1):
            effective_x = x - muzzle_length * math.cos(theta)
            effective_y = y - muzzle_length * math.sin(theta)
            refined = solve_theta(effective_x, effective_y)
            if refined is None:
                return LobSolution(
                    False,
                    arc_name,
                    x,
                    y,
                    speed,
                    iterations=iterations,
                    reason="muzzle-offset iteration left ballistic envelope",
                )
            if abs(refined - theta) <= 1.0e-6:
                theta = refined
                break
            theta = refined
        cosine = math.cos(theta)
        if abs(cosine) < 1.0e-6:
            return LobSolution(False, arc_name, x, y, speed, reason="near-vertical solution")
        flight_time = effective_x / (speed * cosine)
        if not math.isfinite(flight_time) or flight_time <= 0.0 or flight_time > 12.0:
            return LobSolution(False, arc_name, x, y, speed, reason="unsafe flight time")
        return LobSolution(
            True,
            arc_name,
            x,
            y,
            speed,
            yaw_deg=float(bearing_deg) + float(yaw_offset_deg),
            pitch_deg=math.degrees(theta) + float(pitch_offset_deg),
            flight_time_s=flight_time,
            iterations=iterations,
        )

    @classmethod
    def readiness(
        cls,
        state: Any,
        *,
        require_fresh_telemetry: bool = True,
        max_telemetry_age_s: float = 0.50,
        max_heat_ratio: float = 0.88,
        max_roll_deg: float = 8.0,
        hero_range_profile: bool = False,
        now: float | None = None,
    ) -> LobReadiness:
        now = time.perf_counter() if now is None else float(now)
        telemetry_at = float(getattr(state, "native_telemetry_at", 0.0) or 0.0)
        age = max(0.0, now - telemetry_at) if telemetry_at > 0.0 else math.inf
        reasons: list[str] = []
        if str(getattr(state, "robot_profile", "")) != "Hero":
            reasons.append("Hero profile required")
        if not hero_range_profile:
            reasons.append("Hero Range preset required")
        if require_fresh_telemetry:
            if not bool(getattr(state, "native_gimbal_online", False)):
                reasons.append("gimbal offline")
            if age > max(0.05, float(max_telemetry_age_s)):
                reasons.append("telemetry stale")
        if not bool(getattr(state, "hero_lob_yaw_calibrated", False)):
            reasons.append("yaw not calibrated")
        if not bool(getattr(state, "hero_lob_pitch_calibrated", False)):
            reasons.append("pitch not calibrated")
        if not bool(getattr(state, "hero_lob_relocalization_valid", False)):
            reasons.append("relocalization invalid")
        roll = cls.quaternion_roll_deg(
            getattr(state, "native_quaternion", (0.0, 0.0, 0.0, 1.0))
        )
        if not math.isfinite(roll) or abs(roll) > max(0.1, float(max_roll_deg)):
            reasons.append("roll/yaw coupling guard")
        if str(getattr(state, "current_stage", "")).upper() not in {"MATCH", "4"}:
            reasons.append("match stage required")
        if int(getattr(state, "remaining_ammo", 0)) <= 0 and int(getattr(state, "native_bullet_count", 0)) <= 0:
            reasons.append("no projectile")
        heat = max(0.0, float(getattr(state, "current_heat", 0.0)))
        heat_max = max(1.0, float(getattr(state, "max_heat", 1.0)))
        if heat / heat_max >= max(0.05, min(1.0, float(max_heat_ratio))):
            reasons.append("heat guard")
        return LobReadiness(not reasons, tuple(reasons), age)

    @classmethod
    def preview_from_state(cls, state: Any, module: Any, *, hero_range_profile: bool) -> tuple[LobSolution, LobReadiness]:
        def setting(name: str, fallback: Any) -> Any:
            # 语义搬到 settings_access.raw_value，**逐位不变**（不转型、不捕获）。
            return raw_value(module, name, fallback)

        telemetry_speed = float(getattr(state, "native_bullet_speed", 0.0) or 0.0)
        fallback_speed = float(setting("Muzzle Speed", 16.0))
        speed = telemetry_speed if telemetry_speed > 1.0 else fallback_speed
        solution = cls.solve(
            float(setting("Distance", getattr(state, "target_distance_m", 12.0))),
            float(setting("Height Delta", 0.0)),
            speed,
            arc=str(setting("Arc", "High")),
            gravity_mps2=float(setting("Gravity", 9.80665)),
            bearing_deg=float(setting("Bearing", 0.0)),
            yaw_offset_deg=float(setting("Yaw Offset", 0.0)),
            pitch_offset_deg=float(setting("Pitch Offset", 0.0)),
            muzzle_length_m=float(setting("Muzzle Length", 0.35)),
        )
        readiness = cls.readiness(
            state,
            require_fresh_telemetry=bool(setting("Require Fresh Telemetry", True)),
            max_telemetry_age_s=float(setting("Telemetry Timeout", 0.50)),
            max_heat_ratio=float(setting("Heat Guard", 0.88)),
            max_roll_deg=float(setting("Max Roll", 8.0)),
            hero_range_profile=hero_range_profile,
        )
        if not solution.reachable:
            readiness = LobReadiness(False, (*readiness.reasons, solution.reason), readiness.telemetry_age_s)
        return solution, readiness
