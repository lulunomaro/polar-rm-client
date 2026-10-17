"""Pure RM2026 field-presence and cumulative-projectile decisions.

Inputs are decoded values and detached counter snapshots. This module neither
reads RobotState nor owns clocks, host callbacks, telemetry or device resources.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contract import ALIASES, FIELDS


def select_present_fields(name: str, data: dict[str, Any]) -> tuple[dict[str, Any], frozenset[int]]:
    """Distinguish missing optional fields from explicitly received zeros.

    Direct compatibility callers without metadata retain their legacy mapping;
    codec results are filtered through the year-specific field-number contract.
    """
    present = frozenset(data.get("__present__", ()))
    if "__present__" not in data:
        return data, present
    keys = {
        ALIASES.get((name, field_name), field_name)
        for number, (field_name, _kind, _repeated) in FIELDS.get(name, {}).items()
        if number in present
    }
    return {key: value for key, value in data.items() if key in keys or key == "__present__"}, present


@dataclass(frozen=True, slots=True)
class ProjectileDamageBaseline:
    initialized: bool
    small_initialized: bool
    large_initialized: bool
    small_total: int
    large_total: int


@dataclass(frozen=True, slots=True)
class ProjectileDamageSample:
    """Converted totals plus their original optional-field presence.

    The adapter substitutes the previous total for an absent field before
    conversion. Keeping this step there preserves its existing failure order.
    At least one counter is present; metadata-only records never reach policy.
    """

    small_present: bool
    large_present: bool
    small_total: int
    large_total: int


@dataclass(frozen=True, slots=True)
class ProjectileDamageDecision:
    next_baseline: ProjectileDamageBaseline
    small_delta: int
    large_delta: int

    @property
    def damage_delta(self) -> int:
        return self.small_delta + self.large_delta

    @property
    def projectile_kind(self) -> str:
        if self.small_delta > 0 and self.large_delta > 0:
            return "17mm+42mm"
        return "42mm" if self.large_delta > 0 else "17mm"


def assess_projectile_damage(
    baseline: ProjectileDamageBaseline, sample: ProjectileDamageSample,
) -> ProjectileDamageDecision:
    """Only increases from observed counters constitute a new projectile hit.

    A backwards jump or wrap invalidates both previous baselines. A missing
    counterpart cannot prove a hit until it has its own post-reset observation.
    The caller publishes a hit only for a positive combined delta.
    """
    small_known = baseline.initialized and baseline.small_initialized
    large_known = baseline.initialized and baseline.large_initialized
    monotonic = bool(
        (not small_known or sample.small_total >= baseline.small_total)
        and (not large_known or sample.large_total >= baseline.large_total)
    )
    if not monotonic:
        small_known = large_known = False
    small_delta = sample.small_total - baseline.small_total if small_known else 0
    large_delta = sample.large_total - baseline.large_total if large_known else 0
    next_baseline = ProjectileDamageBaseline(
        initialized=True,
        small_initialized=sample.small_present or small_known,
        large_initialized=sample.large_present or large_known,
        small_total=sample.small_total,
        large_total=sample.large_total,
    )
    return ProjectileDamageDecision(next_baseline, small_delta, large_delta)


def select_projected_health(*, local: bool, global_hp: int | None,
                            dynamic_hp: int | None) -> int:
    """Choose one HP observation; None means absent, while zero means dead.

    Current-local dynamic telemetry retains its existing priority over the
    slower team snapshot. For every other robot, an observed global value wins
    over retained dynamic history, including zero. Missing global slots may
    still use dynamic history. This chooses a source, not freshness or authority
    for control; callers retain their existing conversion/clamping boundaries.
    """
    if local and dynamic_hp is not None:
        return dynamic_hp
    if global_hp is not None:
        return global_hp
    return dynamic_hp if dynamic_hp is not None else 0
