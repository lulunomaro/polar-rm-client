"""The telemetry-only boundary between the Polar bridge and ``RobotState``.

An overlay is a partial update, not permission to assign arbitrary state
attributes. These fields are the existing outputs of the bridge's built-in
handlers. Operator choices, control authority, source freshness, methods, and
official-owned containers deliberately do not belong to this contract.

Protocol-defined extensions remain in ``RobotState.polar_overlay`` for their
consumers, including the chassis spin readback. Adding a generic protocol
segment does not grant it write access to unrelated model fields.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any


_FLOAT_FIELDS = frozenset({
    "chassis_yaw_deg",
    "chassis_yaw_rate_rps",
    "current_heat",
    "gimbal_pitch_deg",
    "gimbal_yaw_deg",
    "imu_pitch_deg",
    "imu_roll_deg",
    "max_heat",
    "measured_vx_mps",
    "measured_vy_mps",
    "measured_wz_rps",
    "native_bullet_speed",
    "polar_self_angle_deg",
    "polar_self_x",
    "polar_self_y",
    "polar_stage_remain_s",
    "relative_yaw_deg",
})

_INT_FIELDS = frozenset({
    "armor_id",
    "base_health",
    "bullet_type",
    "current_chassis_energy",
    "current_health",
    "launch_frequency_hz",
    "max_health",
    "outpost_health",
    "polar_event_bitmap",
    "polar_fortress_allowance",
    "polar_game_progress",
    "polar_game_type",
    "polar_gold_coin",
    "polar_reported_robot_id",
    "polar_warning_count",
    "polar_warning_level",
    "polar_warning_robot_id",
    "power_limit",
    "projectile_allowance_17mm",
    "projectile_allowance_42mm",
    "recovery_buff",
    "report_cooling_buff",
    "rfid_status",
    "vulnerability_buff",
})

_BOOL_FIELDS = frozenset({
    "chassis_error",
    "drive_ready",
    "gimbal_error",
    "large_buff",
    "shoot_error",
    "small_buff",
    "wheel_online",
})

_MAPPING_FIELDS = frozenset({
    "polar_ally_hp",
    "polar_debug_packages",
    "polar_ground_positions",
})

POLAR_OVERLAY_FIELDS = _FLOAT_FIELDS | _INT_FIELDS | _BOOL_FIELDS | _MAPPING_FIELDS


def _finite_number(value: Any) -> bool:
    # Booleans and strings are not telemetry numbers. Do not coerce arbitrary
    # objects; the decoder/bridge already produces Python numeric scalars.
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _copy_mapping(name: str, value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    if name == "polar_ally_hp":
        valid = all(type(key) is int and type(hp) is int for key, hp in value.items())
    elif name == "polar_debug_packages":
        valid = all(type(key) is str and _finite_number(number) for key, number in value.items())
    else:  # polar_ground_positions: dict[int, tuple[float, float]]
        valid = all(
            type(key) is int
            and type(position) is tuple
            and len(position) == 2
            and all(_finite_number(number) for number in position)
            for key, position in value.items()
        )
    # All nested values are immutable after validation. A shallow copy now
    # prevents later bridge/source mutations from bypassing this boundary.
    return dict(value) if valid else None


def validated_polar_overlay(overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Return valid, detached model updates without changing the source overlay.

    Reject unsupported fields or malformed values individually, preserving
    valid readings in the same update. No range clamping, unit conversion, or
    source selection occurs here; those remain the bridge/manager's job.
    """

    if not isinstance(overlay, Mapping):
        return {}
    updates: dict[str, Any] = {}
    for name, value in overlay.items():
        if type(name) is not str:
            continue
        if name in _FLOAT_FIELDS:
            if _finite_number(value):
                updates[name] = value
        elif name in _INT_FIELDS:
            if type(value) is int:
                updates[name] = value
        elif name in _BOOL_FIELDS:
            if type(value) is bool:
                updates[name] = value
        elif name in _MAPPING_FIELDS:
            copied = _copy_mapping(name, value)
            if copied is not None:
                updates[name] = copied
    return updates
