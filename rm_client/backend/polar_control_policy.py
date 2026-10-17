"""Pure authorization decisions for the independent 0x5A control channel.

Inputs are immutable values. This module cannot read settings, mutate a
RobotState, touch a transport, or acquire authority by itself. The manager
adapts these decisions to the existing arbiter and transport contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import robot_roles
from .polar_link import SPIN_CMD_NONE, SPIN_CMD_OFF, SPIN_CMD_ON


@dataclass(frozen=True)
class RobotRoleSelection:
    """Resolve capabilities from reported identity, preserving UI diagnostics."""

    selected_id: int
    reported_id: int
    limits: robot_roles.RobotRoleLimits
    drive_block: str
    gimbal_block: str
    mismatch: str

    @classmethod
    def resolve(cls, selected_id: int, reported_id: int) -> RobotRoleSelection:
        role_id = reported_id or selected_id
        limits = robot_roles.limits_for(role_id)
        mismatch = ""
        if selected_id and reported_id and selected_id != reported_id:
            mismatch = (
                f"界面选的兵种是 {selected_id}，但机器人自报编号是 {reported_id}；"
                f"限幅与驾驶权限已按实际车辆（{limits.label}）执行，请核对界面选择"
            )
        return cls(selected_id, reported_id, limits,
                   robot_roles.drive_block_reason(role_id),
                   robot_roles.gimbal_block_reason(role_id), mismatch)

    @property
    def effective_id(self) -> int:
        return self.reported_id or self.selected_id

    def diagnostics(self) -> dict[str, Any]:
        return {
            "role_id": self.effective_id,
            "role_label": self.limits.label,
            "role_hud_code": self.limits.hud_code,
            "role_chassis": self.limits.chassis,
            "role_max_linear_mps": round(self.limits.max_linear_mps, 3),
            "role_max_yaw_rps": round(self.limits.max_yaw_rps, 3),
            "role_known": self.limits.known,
            "role_drive_block": self.drive_block,
            "role_gimbal_block": self.gimbal_block,
            "role_selected_id": self.selected_id,
            "role_reported_id": self.reported_id,
            "role_mismatch": self.mismatch,
        }


@dataclass(frozen=True)
class ControlGates:
    """The independent permissions observed during one manager tick.

    Radio keyboard/mouse does not require a USB readback. Spin and the
    firmware gimbal-host request have their own gates and do not imply
    permission to drive the chassis. These distinctions are intentional.
    """

    estopped: bool
    module_enabled: bool
    allow_drive: bool
    link_active: bool

    @property
    def human_drive_authorized(self) -> bool:
        return not self.estopped and self.module_enabled and self.allow_drive

    def chassis_authorized(self, *, requested: bool, role_block: str) -> bool:
        return bool(self.human_drive_authorized and self.link_active
                    and requested and not role_block)

    def radio_authorized(self, *, requested: bool) -> bool:
        return requested and self.human_drive_authorized

    def spin_authorized(self, *, allowed: bool, module_enabled: bool) -> bool:
        return allowed and not self.estopped and self.link_active and module_enabled

    def gimbal_host_requested(self, *, requested: bool) -> bool:
        return requested and self.link_active and not self.estopped

    def radio_block_reason(self, *, requested: bool) -> str:
        if not requested or self.human_drive_authorized:
            return ""
        cause = ("急停激活" if self.estopped else
                 "模块 RobotLink 未启用" if not self.module_enabled else
                 "未授予 Allow Drive")
        return "无线键鼠出口未生效：" + cause


def may_plan_gimbal(*, estopped: bool, arbiter_authorized: bool,
                    link_active: bool, calibrated: bool, target_locked: bool,
                    selected_robot_id: Any) -> bool:
    """Preserve the selected-role gimbal gate; drive limits use reported ID."""
    return bool(not estopped and arbiter_authorized and link_active and calibrated
                and target_locked and not robot_roles.gimbal_block_reason(int(selected_robot_id or 0)))


def gimbal_egress_owner(*, polar_active: bool, polar_authorized: bool,
                        polar_has_target: bool, official_enabled: bool,
                        official_mock: bool) -> str:
    """Choose one angle publisher; an active POLAR plan has precedence."""
    if polar_active and polar_authorized and polar_has_target:
        return "polar"
    if official_enabled and not official_mock:
        return "official"
    return "none"


@dataclass(frozen=True)
class SpinCommandState:
    """Ownership retained while asserting ON or awaiting bounded OFF release."""

    ever_on: bool = False
    off_since: float = 0.0
    command: int = SPIN_CMD_NONE

    def advance(self, *, desired: bool, reported: Any, now: float,
                off_assert_s: float) -> SpinCommandState:
        if desired:
            return SpinCommandState(True, 0.0, SPIN_CMD_ON)
        if not self.ever_on:
            # An unrelated physical-controller spin is not ours to turn off.
            return SpinCommandState(False, self.off_since, SPIN_CMD_NONE)
        if reported is not None:
            return (SpinCommandState(True, 0.0, SPIN_CMD_OFF)
                    if bool(reported) else SpinCommandState())
        # Firmware without 0x1F cannot confirm a stop. Bound suppression rather
        # than indefinitely preventing the driver from reclaiming spin control.
        off_since = self.off_since if self.off_since > 0.0 else now
        if now - off_since >= off_assert_s:
            return SpinCommandState()
        return SpinCommandState(True, off_since, SPIN_CMD_OFF)
