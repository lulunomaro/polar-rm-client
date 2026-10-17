"""Pure selection of post-commit effects using stable registry identities.

The configuration layer names effects; the UI adapter owns their execution.
Display labels and equal-looking detached Setting objects never select effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from .registry import SettingId


class SettingEffect(Enum):
    SYNC_CONTROLLER_ANGLES = auto()
    CLOSE_OUTPUT_GATE = auto()
    SYNC_CONTROL_BINDINGS = auto()
    INVALIDATE_HUD_PALETTE = auto()
    APPLY_SCENARIO = auto()
    APPLY_MATCH_MODE = auto()
    APPLY_ALLIANCE = auto()
    APPLY_LEGACY_ALLIANCE = auto()
    APPLY_ROBOT_PROFILE = auto()
    APPLY_GUI_MODE = auto()
    APPLY_COMBAT_PRESET = auto()
    ANNOUNCE_RIGHT_DISPLAY = auto()
    SAVE_STARTUP_PREFERENCE = auto()
    RELOAD_PROFILE = auto()
    PRELOAD_ELIMINATION_SOUNDS = auto()
    SYNC_QUICK_ACTION_WHEEL = auto()
    APPLY_SPIN_SETTING = auto()
    ANNOUNCE_RADIO_EGRESS = auto()


@dataclass(frozen=True)
class SettingEffectPlan:
    effects: tuple[SettingEffect, ...] = ()
    auto_save: bool = True


_EXACT_EFFECTS = {
    SettingId("HUD", "Visual Tone"): SettingEffect.INVALIDATE_HUD_PALETTE,
    SettingId("Scenario", "Mode"): SettingEffect.APPLY_SCENARIO,
    SettingId("MatchMode", "Mode"): SettingEffect.APPLY_MATCH_MODE,
    SettingId("AllianceProfile", "Side"): SettingEffect.APPLY_ALLIANCE,
    SettingId("MatchMode", "Alliance"): SettingEffect.APPLY_LEGACY_ALLIANCE,
    SettingId("ClickGui", "Mode"): SettingEffect.APPLY_GUI_MODE,
    SettingId("HUD", "Combat Preset"): SettingEffect.APPLY_COMBAT_PRESET,
    SettingId("HUD", "Right Display"): SettingEffect.ANNOUNCE_RIGHT_DISPLAY,
    SettingId("ConfigManager", "Load On Start"): SettingEffect.SAVE_STARTUP_PREFERENCE,
    SettingId("ConfigManager", "Profile"): SettingEffect.RELOAD_PROFILE,
    SettingId("EliminationFX", "Preset"): SettingEffect.PRELOAD_ELIMINATION_SOUNDS,
    SettingId("RobotLink", "Radio KM Egress"): SettingEffect.ANNOUNCE_RADIO_EGRESS,
}
_MODULE_EFFECTS = {
    "ControllerAngles": SettingEffect.SYNC_CONTROLLER_ANGLES,
    "RobotProfile": SettingEffect.APPLY_ROBOT_PROFILE,
    "QuickActionWheel": SettingEffect.SYNC_QUICK_ACTION_WHEEL,
    "SpinMode": SettingEffect.APPLY_SPIN_SETTING,
}
_OUTPUT_GATES = frozenset({"Allow Drive", "Custom Controller Egress", "Radio KM Egress"})


def effects_for_setting(identity: SettingId | None, value: Any) -> SettingEffectPlan:
    """Choose ordered effects after a setting's value has been committed."""
    if identity is None:
        return SettingEffectPlan()
    effects = []
    if identity.module == "RobotLink":
        if identity.setting in _OUTPUT_GATES and not bool(value):
            effects.append(SettingEffect.CLOSE_OUTPUT_GATE)
        if identity.setting.startswith("Bind "):
            return SettingEffectPlan((SettingEffect.SYNC_CONTROL_BINDINGS,))
    effect = _EXACT_EFFECTS.get(identity, _MODULE_EFFECTS.get(identity.module))
    if effect is not None:
        effects.append(effect)
    return SettingEffectPlan(tuple(effects), auto_save=effect is not SettingEffect.RELOAD_PROFILE)
