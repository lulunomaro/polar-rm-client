"""Fail-closed projectile-hit to chassis-spin assistance.

The referee ``RobotInjuryStat`` message contains cumulative damage counters.
This module deliberately consumes only a *new positive delta* published by the
backend; health changes, vision detections and the first cumulative sample are
never treated as projectile hits.  The controller only requests changes to the
existing ``SpinMode`` module and never emits a fire command; the UI listener
owns the separately gated team CustomControl output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .settings_access import read_bool_strict, read_number_guarded


@dataclass(frozen=True, slots=True)
class SpinDirective:
    """One idempotent state change requested by the guard."""

    action: str = "none"  # none | enable | disable
    reason: str = ""
    damage_delta: int = 0

    @property
    def changed(self) -> bool:
        return self.action in {"enable", "disable"}


def _bool_setting(module: Any, name: str, default: bool) -> bool:
    # 语义搬到 settings_access.read_bool_strict，**逐位不变**（包括不捕获类型错）。
    # 两种语义（strict / guarded）现在都有名字了，见 rm_client/settings_access.py 顶部。
    return read_bool_strict(module, name, default)


def _number_setting(module: Any, name: str, default: float) -> float:
    return read_number_guarded(module, name, default)


class ProjectileHitSpinController:
    """Turn ``SpinMode`` on for a bounded period after a referee-confirmed hit.

    Ownership matters: if the operator already had spin enabled, or manually
    changes it while the timer is active, expiry must not turn their state off.
    """

    def __init__(self) -> None:
        self.seen_revision = 0
        self.active_until = 0.0
        self.cooldown_until = 0.0
        self.owns_spin = False
        self.last_reason = "IDLE"

    @property
    def active(self) -> bool:
        return self.active_until > 0.0

    def reset(self, revision: int = 0) -> None:
        self.seen_revision = max(0, int(revision))
        self.active_until = 0.0
        self.cooldown_until = 0.0
        self.owns_spin = False
        self.last_reason = "IDLE"

    def operator_override(self) -> None:
        """Release ownership without changing the operator's current state."""

        self.owns_spin = False
        if self.active_until > 0.0:
            self.last_reason = "OPERATOR OVERRIDE"

    def update(
        self,
        state: Any,
        feature_module: Any,
        *,
        spin_enabled: bool,
        referee_link: bool,
        now: float,
    ) -> SpinDirective:
        revision = max(0, int(getattr(state, "projectile_hit_revision", 0)))

        if feature_module is None or not bool(getattr(feature_module, "enabled", False)):
            self.seen_revision = revision
            self.active_until = 0.0
            self.cooldown_until = 0.0
            if self.owns_spin and spin_enabled:
                self.owns_spin = False
                self.last_reason = "FEATURE DISABLED"
                return SpinDirective("disable", self.last_reason)
            self.owns_spin = False
            self.last_reason = "OFF"
            return SpinDirective()

        auto_disable = _bool_setting(feature_module, "Auto Disable", True)
        if self.active_until > 0.0 and now >= self.active_until:
            self.active_until = 0.0
            self.cooldown_until = max(
                self.cooldown_until,
                now + max(0.0, _number_setting(feature_module, "Cooldown", 1.0)),
            )
            if auto_disable and self.owns_spin and spin_enabled:
                self.owns_spin = False
                self.last_reason = "HIT WINDOW COMPLETE"
                return SpinDirective("disable", self.last_reason)
            self.owns_spin = False
            self.last_reason = "HIT WINDOW COMPLETE"

        if revision <= self.seen_revision:
            # A manual disable while we owned the temporary state cancels the
            # ownership latch; a later expiry must not re-toggle anything.
            if self.owns_spin and not spin_enabled:
                self.owns_spin = False
                self.active_until = 0.0
                self.last_reason = "OPERATOR OVERRIDE"
            return SpinDirective()

        self.seen_revision = revision
        damage = max(0, int(getattr(state, "last_projectile_damage_delta", 0)))
        minimum_damage = max(1, int(round(_number_setting(feature_module, "Minimum Damage", 1.0))))
        if damage < minimum_damage:
            self.last_reason = "DAMAGE BELOW THRESHOLD"
            return SpinDirective(reason=self.last_reason, damage_delta=damage)
        if now < self.cooldown_until:
            self.last_reason = "COOLDOWN"
            return SpinDirective(reason=self.last_reason, damage_delta=damage)
        if int(getattr(state, "current_health", 0)) <= 0:
            self.last_reason = "ROBOT DOWN"
            return SpinDirective(reason=self.last_reason, damage_delta=damage)
        if _bool_setting(feature_module, "Only During Match", True):
            if str(getattr(state, "current_stage", "")).upper() not in {"4", "MATCH"}:
                self.last_reason = "NOT IN MATCH"
                return SpinDirective(reason=self.last_reason, damage_delta=damage)
        if _bool_setting(feature_module, "Require Referee Link", True) and not referee_link:
            self.last_reason = "REFEREE LINK OFFLINE"
            return SpinDirective(reason=self.last_reason, damage_delta=damage)
        if _bool_setting(feature_module, "Power Guard", True):
            minimum_buffer = _number_setting(feature_module, "Minimum Buffer", 10.0)
            if float(getattr(state, "current_buffer_energy", 0.0)) < minimum_buffer:
                self.last_reason = "LOW BUFFER ENERGY"
                return SpinDirective(reason=self.last_reason, damage_delta=damage)

        duration = max(0.20, _number_setting(feature_module, "Duration", 2.5))
        self.active_until = max(self.active_until, now + duration)
        self.last_reason = f"PROJECTILE HIT +{damage}"
        if spin_enabled:
            # Preserve ownership when this is a repeated hit during the
            # automation's own active window; otherwise the second hit would
            # leave SpinMode stuck on after expiry.  An actual operator toggle
            # calls ``operator_override`` from the module registry callback.
            return SpinDirective(reason=self.last_reason, damage_delta=damage)
        self.owns_spin = True
        return SpinDirective("enable", self.last_reason, damage)
