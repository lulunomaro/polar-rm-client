"""Stable-identity effect plans do not need Qt, a backend, or a window host."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.configuration.effects import SettingEffect as Effect, effects_for_setting
from rm_client.configuration.registry import SettingId, setting_identity
from rm_client.model import build_default_registry


class SettingEffectTests(unittest.TestCase):
    def test_exact_setting_effects_use_persisted_names(self):
        expected = {
            ("HUD", "Visual Tone"): Effect.INVALIDATE_HUD_PALETTE,
            ("Scenario", "Mode"): Effect.APPLY_SCENARIO,
            ("MatchMode", "Mode"): Effect.APPLY_MATCH_MODE,
            ("AllianceProfile", "Side"): Effect.APPLY_ALLIANCE,
            ("MatchMode", "Alliance"): Effect.APPLY_LEGACY_ALLIANCE,
            ("ClickGui", "Mode"): Effect.APPLY_GUI_MODE,
            ("HUD", "Combat Preset"): Effect.APPLY_COMBAT_PRESET,
            ("HUD", "Right Display"): Effect.ANNOUNCE_RIGHT_DISPLAY,
            ("ConfigManager", "Load On Start"): Effect.SAVE_STARTUP_PREFERENCE,
            ("EliminationFX", "Preset"): Effect.PRELOAD_ELIMINATION_SOUNDS,
        }
        for identity, effect in expected.items():
            with self.subTest(identity=identity):
                plan = effects_for_setting(SettingId(*identity), True)
                self.assertEqual(plan.effects, (effect,))
                self.assertTrue(plan.auto_save)

    def test_module_effects_accept_dynamic_setting_ids(self):
        expected = {
            "ControllerAngles": Effect.SYNC_CONTROLLER_ANGLES,
            "RobotProfile": Effect.APPLY_ROBOT_PROFILE,
            "QuickActionWheel": Effect.SYNC_QUICK_ACTION_WHEEL,
            "SpinMode": Effect.APPLY_SPIN_SETTING,
        }
        for module, effect in expected.items():
            self.assertEqual(effects_for_setting(SettingId(module, "future option"), 1).effects, (effect,))

    def test_radio_falling_edge_locks_before_presentation(self):
        identity = SettingId("RobotLink", "Radio KM Egress")
        self.assertEqual(effects_for_setting(identity, False).effects,
                         (Effect.CLOSE_OUTPUT_GATE, Effect.ANNOUNCE_RADIO_EGRESS))
        self.assertEqual(effects_for_setting(identity, True).effects, (Effect.ANNOUNCE_RADIO_EGRESS,))
        for name in ("Allow Drive", "Custom Controller Egress"):
            self.assertEqual(effects_for_setting(SettingId("RobotLink", name), False).effects,
                             (Effect.CLOSE_OUTPUT_GATE,))
            self.assertEqual(effects_for_setting(SettingId("RobotLink", name), True).effects, ())

    def test_control_binding_effect_is_scoped_to_robot_link(self):
        self.assertEqual(effects_for_setting(SettingId("RobotLink", "Bind W"), "I").effects,
                         (Effect.SYNC_CONTROL_BINDINGS,))
        self.assertEqual(effects_for_setting(SettingId("Other", "Bind W"), "I").effects, ())

    def test_profile_selection_reload_does_not_schedule_save(self):
        plan = effects_for_setting(SettingId("ConfigManager", "Profile"), "Competition")
        self.assertEqual(plan.effects, (Effect.RELOAD_PROFILE,))
        self.assertFalse(plan.auto_save)

    def test_equal_detached_setting_has_no_effect_authority(self):
        registry = build_default_registry()
        setting = registry.get("QuickActionWheel").get_setting("Radius")
        detached = deepcopy(setting)
        self.assertEqual(setting, detached)
        self.assertEqual(setting_identity(registry, setting), SettingId("QuickActionWheel", "Radius"))
        self.assertIsNone(setting_identity(registry, detached))
        self.assertEqual(effects_for_setting(setting_identity(registry, detached), detached.value).effects, ())
        self.assertEqual(effects_for_setting(SettingId("显示", "视觉风格"), "LiquidClean").effects, ())


if __name__ == "__main__":
    unittest.main()
