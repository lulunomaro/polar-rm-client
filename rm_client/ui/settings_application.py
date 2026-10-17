"""Shared preference access and application adapter for settings and keybind UI.

PAGES is the single capability allowlist, evaluated against the live registry.
Validation delegates to the configuration layer. Applying a prepared draft still
requires the window host's control, runtime restoration, cache and save hooks;
this adapter does not claim to make those runtime effects window-independent.
Neither this module nor the emergency shortcut adapter imports either dialog.
"""
from __future__ import annotations

from ..configuration.preferences import PreferenceCapabilities, prepare_preference_draft
from ..configuration.quiescence import quiesce_configuration_inputs
from ..configuration.registry import SettingId
from ..configuration.transactions import ConfigurationApplyError, apply_configuration_transaction
from ..model import CONTROL_SLOT_ORDER


# None means all scalar preferences, never action/action_group controls.
PAGES = {
    "音频": (
        ("AudioFeedback", None, True),
        ("EliminationFX", ("Sound Enabled", "Sound Volume"), False),
        ("HUD", ("Jello List Sound",), False),
    ),
    "游戏": (
        ("MatchMode", None, False), ("RobotProfile", None, False),
        ("AllianceProfile", None, False), ("MatchChat", None, True),
        ("EconomyPanel", ("Shop Layout", "CS2 Feedback", "Confirm", "Hold Seconds"), False),
        ("QuickActionWheel", None, True),
    ),
    "键盘 / 鼠标": (
        ("RobotLink", ("Mouse Sensitivity", "Invert Mouse Y", "Mouse Wheel Scale",
                       *(f"Bind {slot}" for slot in CONTROL_SLOT_ORDER)), False),
    ),
    "界面 / HUD": (
        ("ClickGui", ("Scale", "Language", "Save Layout"), False),
        ("HUD", None, True), ("UIScale", None, True),
        ("TopMatchBar", None, True), ("MinecraftCombatHUD", None, False),
        ("TacticalMap", None, True), ("CombatTargetHUD", None, True),
        ("EliminationFX", ("Preset", "Element Visibility", "Scale", "Responsive Scale",
                           "Anchor Policy", "X Offset", "Y Offset", "Kill Feed", "Card Icons"), True),
    ),
}


def settings_specs(registry, page):
    """Explicit capability allowlist; cannot expose TX/Mock/Arm gates by accident."""
    for name, allowed, toggle in PAGES[page]:
        module = registry.get(name)
        if module is not None:
            settings = [s for s in module.settings if s.kind in ("bool", "mode", "number")
                        and (allowed is None or s.name in allowed)]
            yield module, settings, toggle


def preference_capabilities(registry):
    specs = [spec for page in PAGES for spec in settings_specs(registry, page)]
    return PreferenceCapabilities(
        settings=frozenset(SettingId(module.name, setting.name)
                           for module, settings, _ in specs for setting in settings),
        toggles=frozenset(module.name for module, _, toggle in specs if toggle),
    )


def validate_draft(host, draft, module_binds=None):
    """Compatibility entry point; validation needs only registry and bindings."""
    prepared = prepare_preference_draft(
        host.registry, host.control.binds, draft, preference_capabilities(host.registry),
        module_binds=module_binds,
    )
    return dict(prepared.control_binds)


def apply_registry_draft(host, draft, toggles=None, module_binds=None, *, audio_preferences=None):
    host.ensure_configuration_idle()
    prepared = prepare_preference_draft(
        host.registry, host.control.binds, draft, preference_capabilities(host.registry),
        toggles=toggles, module_binds=module_binds,
    )
    participants = ()
    if audio_preferences is not None:
        from ..audio_preferences import prepare_audio_change
        participants = (prepare_audio_change(audio_preferences),)
    previous = host.capture_configuration_restore_point()

    def quiesce():
        quiesce_configuration_inputs(
            revoke=lambda: host.backend.lock_control("settings applied"),
            release=lambda: host.control.release_all(host.state, host.registry),
            clear_pending=lambda: host.control.clear_pending(),
        )

    def update():
        if not host.control.set_binds(dict(prepared.control_binds)):
            raise ValueError("控制绑定无效")
        link = host.registry.get("RobotLink")
        for slot, physical in prepared.control_binds:
            setting = link.get_setting(f"Bind {slot}") if link else None
            if setting is not None:
                setting.value = physical
        for assignment in prepared.settings:
            assignment.setting.value = assignment.value
        for assignment in prepared.settings:
            host.after_setting_changed(assignment.setting)
        for module, value in prepared.toggles:
            if module.enabled != value:
                host.registry.toggle_module(module, source="settings")
        for module, value in prepared.module_binds:
            module.keybind = value
        host.invalidate_gui_cache()
        host.invalidate_transient_overlay_scene()
        host.frame_scheduler.force()

    was_loading = host.config_loading
    host.config_loading = True
    try:
        apply_configuration_transaction(
            host.registry, quiesce=quiesce, update=update,
            restore_runtime=lambda: host.restore_configuration_runtime(previous),
            fail_closed=lambda: host.fail_closed_configuration(previous, "settings apply failed"),
            participants=participants,
        )
    except ConfigurationApplyError as error:
        host.record_configuration_failure(error)
        raise
    finally:
        host.config_loading = was_loading
    host.auto_save_config()
