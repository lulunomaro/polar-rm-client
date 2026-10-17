"""Real offscreen-window configuration transactions with synthetic failures.

Qt widgets and live registry adapters are exercised. Device/network effects
remain stopped or mocked; this is not native OpenGL or hardware evidence.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import weakref
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication

from rm_client import audio_preferences as audio
from rm_client.configuration.transactions import ConfigurationApplyError
from rm_client.configuration.registry import RegistrySnapshot
from rm_client.model import CONTROL_SLOT_ORDER, build_default_registry
from rm_client.ui.cs2_settings_center import RegistrySettingsPage, apply_registry_draft
from rm_client.ui.polar_app import PolarRMWindow
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])


def settle_configuration(window):
    """Exercise the production window's sole completion-routing path."""
    settle_window_lifecycle(window, APP)


class WindowConfigurationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": self.directory.name})
        self.environment.start()
        self.window = PolarRMWindow()
        settle_configuration(self.window)
        for timer in self.window.findChildren(QTimer):
            timer.stop()
        self.window._auto_save_pending = False
        self.addCleanup(self.close_window)

    def close_window(self):
        close_window_safely(self.window, APP)
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.environment.stop()
        self.directory.cleanup()

    def test_settings_cannot_commit_while_an_ordinary_scenario_request_is_pending(self):
        window = self.window
        page = RegistrySettingsPage(window, "游戏", build_default_registry())
        self.addCleanup(page.deleteLater)
        deadline = time.monotonic() + 3
        while not page.builder.ready and time.monotonic() < deadline:
            APP.processEvents()
        self.assertTrue(page.builder.ready)
        side = page.controls[("AllianceProfile", "Side")]
        side.setCurrentIndex(side.findData("Blue"))
        baseline = dict(page._baseline)
        before = deepcopy(window.current_config_data())
        generation = window.backend.request_scenario("LabDebug", start=False, registry=window.registry)
        self.assertFalse(window.configuration_change_pending)
        self.assertTrue(window.backend.lifecycle_pending)
        with patch.object(window, "auto_save_config") as save, \
             patch.object(window.backend, "set_client_id", wraps=window.backend.set_client_id) as identity:
            with self.assertRaisesRegex(ValueError, "尚未完成"):
                page.apply_draft()
            save.assert_not_called()
            identity.assert_not_called()
        self.assertEqual(window.current_config_data(), before)
        self.assertEqual(page._baseline, baseline)
        self.assertEqual(side.currentData(), "Blue")
        self.assertEqual(window.backend.lifecycle_snapshot.generation, generation)
        settle_configuration(window)

    def test_settings_failure_restores_values_options_binds_and_stopped_runtime(self):
        window = self.window
        previous = deepcopy(window.current_config_data())
        previous_binds = dict(window.control.binds)
        role = window.registry.get("RobotProfile").get_setting("Role")
        previous_options = list(role.options)
        window.control.key_state["W"] = True
        original = window.after_setting_changed

        def change_then_fail(setting):
            original(setting)
            if setting.name == "Mouse Sensitivity":
                role.options = ["partial runtime mutation"]
                window.home_visible = False
                window.startup_step = "done"
                raise OSError("synthetic late configuration failure")

        with patch.object(window, "after_setting_changed", side_effect=change_then_fail), \
             patch.object(window.backend, "request_scenario", wraps=window.backend.request_scenario) as scenario, \
             patch.object(window.backend, "arm_match_control") as arm:
            with self.assertRaises(ConfigurationApplyError) as caught:
                apply_registry_draft(window, {
                    ("RobotLink", "Bind W"): "S", ("RobotLink", "Bind S"): "W",
                    ("RobotLink", "Mouse Sensitivity"): 2.0,
                })
        self.assertFalse(caught.exception.restored)
        self.assertTrue(caught.exception.restore_pending)
        self.assertIsNone(caught.exception.safety_error)
        settle_configuration(window)
        self.assertEqual(window.current_config_data(), previous)
        self.assertEqual(window.control.binds, previous_binds)
        self.assertEqual(role.options, previous_options)
        self.assertEqual((window.home_visible, window.startup_step), (True, "scenario"))
        self.assertFalse(any(window.control.key_state.values()))
        self.assertFalse(window._auto_save_pending)
        self.assertFalse(window.config_loading)
        self.assertTrue(window.backend.safety.estop_active)
        self.assertTrue(all(call.kwargs["start"] is False for call in scenario.call_args_list))
        arm.assert_not_called()

    def test_toggle_listener_failure_rolls_back_without_replaying_module_hooks(self):
        window = self.window
        hud = window.registry.get("HUD")
        previous = deepcopy(window.current_config_data())
        events = []

        def fail_toggle(module, enabled, source):
            events.append((module.name, enabled, source))
            raise OSError("synthetic toggle presentation failure")

        window.registry.add_toggle_listener(fail_toggle)
        with self.assertRaises(ConfigurationApplyError):
            apply_registry_draft(window, {}, toggles={"HUD": not hud.enabled})
        settle_configuration(window)
        self.assertEqual(window.current_config_data(), previous)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][2], "settings")
        self.assertTrue(window.backend.safety.estop_active)
        self.assertFalse(window._auto_save_pending)

    def test_failed_profile_rollback_reports_identity_restore_failure(self):
        window = self.window
        previous = deepcopy(window.current_config_data())
        candidate = deepcopy(previous)
        candidate["modules"]["AllianceProfile"]["settings"]["Side"] = "Blue"
        with patch.object(window.backend, "set_client_id", side_effect=OSError("identity unavailable")), \
             patch.object(window, "toast") as toast:
            window.apply_config_data(candidate, announce=False)
            self.assertTrue(window.configuration_change_pending)
            with self.assertRaisesRegex(AssertionError, "Window lifecycle failed"):
                settle_configuration(window)
        self.assertEqual(window.current_config_data(), previous)
        self.assertTrue(window.backend.safety.estop_active)
        self.assertIn("恢复未完成", toast.call_args.args[0])
        self.assertFalse(window.config_loading)

    def test_successful_settings_batch_canonicalizes_the_entire_bind_map_once(self):
        window = self.window
        with patch.object(window, "save_current_config") as save:
            apply_registry_draft(window, {
                ("RobotLink", "Bind SHIFT"): "ctrl", ("RobotLink", "Bind CTRL"): "SHIFT",
                ("RobotLink", "Mouse Sensitivity"): 1.5,
            })
            save.assert_not_called()
        link = window.registry.get("RobotLink")
        self.assertEqual({slot: link.get_setting(f"Bind {slot}").value for slot in CONTROL_SLOT_ORDER},
                         window.control.binds)
        self.assertEqual(window.control.binds["SHIFT"], "Ctrl")
        self.assertEqual(window.control.binds["CTRL"], "Shift")
        self.assertTrue(window._auto_save_pending)
        self.assertFalse(window.config_loading)

    def test_direct_gui_and_settings_center_share_committed_effects(self):
        window = self.window
        cases = (
            (("HUD", "Visual Tone"), "PolarLegacy", "_invalidate_hud_palette_setting"),
            (("HUD", "Combat Preset"), "Minecraft 1.21.11", "_apply_combat_preset_setting"),
            (("QuickActionWheel", "Radius"), 150.0, "_sync_wheel_setting"),
            (("RobotLink", "Mouse Sensitivity"), 1.5, None),
        )
        for (module_name, name), value, effect in cases:
            with self.subTest(module=module_name, setting=name):
                previous = deepcopy(window.current_config_data())
                snapshot = RegistrySnapshot.capture(window.registry)
                setting = window.registry.get(module_name).get_setting(name)
                if setting.value == value and setting.options:
                    value = next(option for option in setting.options if option != value)
                window._hud_palette_cache = object()
                setting.value = value
                window.after_setting_changed(setting)
                direct = deepcopy(window.current_config_data())
                palette_cleared = window._hud_palette_cache is None
                snapshot.restore()
                window._apply_config_data(previous, sync_runtime=False)
                window._hud_palette_cache = object()
                if effect is not None:
                    with patch.object(window, effect, wraps=getattr(window, effect)) as callback:
                        apply_registry_draft(window, {(module_name, name): value})
                    callback.assert_called_once_with(setting)
                else:
                    apply_registry_draft(window, {(module_name, name): value})
                self.assertEqual(window.current_config_data(), direct)
                self.assertEqual(window._hud_palette_cache is None, palette_cleared)

    def test_detached_equal_setting_does_not_dispatch_runtime_effects(self):
        window = self.window
        radius = window.registry.get("QuickActionWheel").get_setting("Radius")
        with patch.object(window, "sync_quick_action_wheel_settings") as sync:
            window.after_setting_changed(deepcopy(radius))
        sync.assert_not_called()

    def test_output_gate_failure_still_clears_pending_input(self):
        window = self.window
        gate = window.registry.get("RobotLink").get_setting("Allow Drive")
        gate.value = False
        with patch.object(window.control, "release_all", side_effect=OSError("release failure")), \
             patch.object(window.control, "clear_pending") as clear, \
             patch.object(window.backend, "lock_control") as lock:
            with self.assertRaisesRegex(OSError, "release failure"):
                window.after_setting_changed(gate)
        lock.assert_called_once_with("RobotLink output gate disabled")
        clear.assert_called_once_with()

    def test_partial_snapshot_failure_cannot_become_the_async_restore_baseline(self):
        window = self.window
        previous = deepcopy(window.current_config_data())
        original_restore = RegistrySnapshot.restore
        attempts = 0
        hud = window.registry.get("HUD")
        enabled = hud.enabled
        hooks = []

        def fail_first_snapshot(snapshot):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                hud.enabled = not enabled
                raise OSError("partial model restore")
            return original_restore(snapshot)

        with patch.object(window, "after_setting_changed", side_effect=OSError("apply failed")), \
             patch.object(RegistrySnapshot, "restore", autospec=True, side_effect=fail_first_snapshot), \
             patch.object(hud, "on_enable", side_effect=lambda: hooks.append("enable")), \
             patch.object(hud, "on_disable", side_effect=lambda: hooks.append("disable")):
            with self.assertRaises(ConfigurationApplyError) as caught:
                apply_registry_draft(window, {("RobotLink", "Mouse Sensitivity"): 2.0})
            self.assertTrue(caught.exception.restore_pending)
            self.assertEqual(len(caught.exception.restore_errors), 1)
            settle_configuration(window)
        self.assertEqual(window.current_config_data(), previous)
        self.assertEqual(hud.enabled, enabled)
        self.assertEqual(hooks, [])
        self.assertTrue(window.backend.safety.estop_active)

    def test_audio_rollback_failure_remains_visible_after_async_runtime_restore(self):
        window = self.window
        previous = deepcopy(window.current_config_data())
        path = Path(self.directory.name) / "audio-transaction.json"

        class FailingPool:
            def apply_audio_preferences(self):
                raise OSError("persistent audio pool failure")

        with patch.object(audio, "audio_path", return_value=path), \
             patch.object(audio, "POOLS", weakref.WeakSet()), \
             patch.object(window, "toast") as toast:
            audio.current_audio.cache_clear()
            audio.save_audio(audio.AudioPreferences(master_volume=66))
            pool = FailingPool()
            audio.POOLS.add(pool)
            with self.assertRaises(ConfigurationApplyError) as caught:
                apply_registry_draft(window, {("RobotLink", "Mouse Sensitivity"): 2.0},
                                     audio_preferences=audio.AudioPreferences(master_volume=23))
            self.assertTrue(caught.exception.restore_pending)
            self.assertEqual(len(caught.exception.restore_errors), 1)
            settle_configuration(window)
            self.assertIn("恢复未完成", toast.call_args.args[0])
            self.assertIn("persistent audio pool failure", toast.call_args.args[0])
            self.assertEqual(audio.current_audio().master_volume, 66)
        audio.current_audio.cache_clear()
        audio.selected_output.cache_clear()
        self.assertEqual(window.current_config_data(), previous)
        self.assertTrue(window.backend.safety.estop_active)


if __name__ == "__main__":
    unittest.main()
