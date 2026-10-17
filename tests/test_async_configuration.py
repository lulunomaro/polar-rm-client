"""Real owner-thread profile transitions with controlled, synthetic teardown.

Only stub/offline transports are used. Delayed workers exercise responsiveness
and rollback ordering, not native driver or field qualification.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication

from rm_client.backend import teardown_plan
from rm_client.configuration.pending import ConfigurationPhase
from rm_client.lifecycle import LifecycleCompletion
from rm_client.ui.cs2_settings_center import apply_registry_draft
from rm_client.ui.polar_app import PolarRMWindow
from test_configuration_transactions import settle_configuration
from window_lifecycle_helpers import close_window_safely

APP = QApplication.instance() or QApplication([])


class AsyncConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {
            "POLAR_USER_DATA_ROOT": self.directory.name, "POLAR_AUTOSTART": "0",
        })
        self.environment.start()
        self.window = PolarRMWindow()
        for timer in self.window.findChildren(QTimer):
            timer.stop()
        settle_configuration(self.window)
        self.window._auto_save_pending = False
        self.addCleanup(self.close_window)

    def close_window(self):
        close_window_safely(self.window, APP)
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.environment.stop()
        self.directory.cleanup()

    def candidate(self):
        data = deepcopy(self.window.current_config_data())
        data["modules"]["AllianceProfile"]["settings"]["Side"] = "Blue"
        data["modules"]["RobotProfile"]["settings"]["Role"] = "Infantry1"
        data["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"] = 2.0
        return data

    def test_profile_success_waits_for_owner_completion(self):
        window = self.window
        original_robot_id = window.state.robot_id
        gate, entered = threading.Event(), threading.Event()
        original_wait = teardown_plan.wait_for_mqtt

        def delayed(mqtt):
            entered.set()
            gate.wait(3)
            original_wait(mqtt)

        with patch.object(teardown_plan, "wait_for_mqtt", side_effect=delayed), \
             patch.object(window, "toast") as toast, \
             patch.object(window.backend, "set_client_id", wraps=window.backend.set_client_id) as identity, \
             patch.object(window.backend, "arm_match_control") as arm:
            try:
                started = time.monotonic()
                window.apply_config_data(self.candidate())
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertTrue(entered.wait(0.5))
                self.assertTrue(window.configuration_change_pending)
                self.assertEqual(window.state.robot_id, original_robot_id)
                self.assertFalse(any("配置已加载" in call.args[0] for call in toast.call_args_list))
                identity.assert_not_called()
                self.assertFalse(window._auto_save_pending)
            finally:
                gate.set()
            settle_configuration(window)
        self.assertEqual(window.state.robot_id, 103)
        self.assertEqual(window.backend.mqtt.client_id, "103")
        self.assertEqual(identity.call_count, 1)
        self.assertIn("配置已加载", toast.call_args.args[0])
        arm.assert_not_called()

    def test_activation_failure_restores_exact_values_then_stopped_runtime(self):
        window = self.window
        previous = deepcopy(window.current_config_data())
        previous_binds = dict(window.control.binds)
        previous_robot_id = window.state.robot_id
        original_identity = window.backend.set_client_id
        calls = []

        def fail_new_identity(robot_id):
            calls.append(robot_id)
            if str(robot_id) == "103":
                raise OSError("synthetic activation failure")
            return original_identity(robot_id)

        with patch.object(window.backend, "set_client_id", side_effect=fail_new_identity), \
             patch.object(window.backend, "request_scenario", wraps=window.backend.request_scenario) as request, \
             patch.object(window, "toast") as toast:
            window.apply_config_data(self.candidate())
            settle_configuration(window)
        self.assertEqual(window.current_config_data(), previous)
        self.assertEqual(window.control.binds, previous_binds)
        self.assertEqual(window.state.robot_id, previous_robot_id)
        self.assertEqual(calls, ["103", str(previous_robot_id)])
        self.assertEqual(len(request.call_args_list), 2)
        self.assertFalse(request.call_args_list[-1].kwargs["start"])
        self.assertTrue(window.backend.safety.estop_active)
        self.assertFalse(any("配置已加载" in call.args[0] for call in toast.call_args_list))
        self.assertIn("已恢复设置", toast.call_args.args[0])

    def test_pending_generation_rejects_edits_and_defers_writes(self):
        window = self.window
        window.apply_config_data(self.candidate())
        generation = window._pending_configuration.generation
        radius = window.registry.get("QuickActionWheel").get_setting("Radius")
        previous_radius = radius.value
        radius.value = 179
        window.after_setting_changed(radius)
        self.assertEqual(radius.value, previous_radius)
        hud = window.registry.get("HUD")
        previous_enabled = hud.enabled
        window.registry.toggle_module(hud, source="settings")
        self.assertEqual(hud.enabled, previous_enabled)
        with self.assertRaisesRegex(ValueError, "配置切换尚未完成"):
            apply_registry_draft(window, {("RobotLink", "Mouse Sensitivity"): 1.2})
        with patch.object(window.config_store, "save") as save:
            window.save_current_config()
            window.flush_auto_save(force=True)
            save.assert_not_called()
        window.apply_config_data({"modules": {}})
        self.assertEqual(window._pending_configuration.generation, generation)
        settle_configuration(window)

    def test_unexpected_newer_generation_rolls_back_instead_of_committing(self):
        window = self.window
        previous = deepcopy(window.current_config_data())
        window.apply_config_data(self.candidate())
        generation = window._pending_configuration.generation
        window.backend.request_scenario("LabDebug", start=False, registry=window.registry, robot_id=103)
        self.assertGreater(window.backend.lifecycle_snapshot.generation, generation)
        with patch.object(window, "toast") as toast:
            settle_configuration(window)
        self.assertEqual(window.current_config_data(), previous)
        self.assertTrue(window.backend.safety.estop_active)
        self.assertFalse(any("配置已加载" in call.args[0] for call in toast.call_args_list))

    def test_stale_completion_cannot_consume_current_profile(self):
        window = self.window
        window.apply_config_data(self.candidate())
        pending = window._pending_configuration
        stale = LifecycleCompletion(pending.generation - 1, None, False, ())
        self.assertFalse(window.on_backend_lifecycle_completion(stale))
        self.assertIs(window._pending_configuration, pending)
        settle_configuration(window)

    def test_close_cancels_local_candidate_without_runtime_restart(self):
        window = self.window
        previous = deepcopy(window.current_config_data())
        window.apply_config_data(self.candidate())
        with patch.object(window.backend, "request_scenario") as request:
            window.cancel_pending_configuration_for_close()
        self.assertFalse(window.configuration_change_pending)
        self.assertEqual(window.current_config_data(), previous)
        request.assert_not_called()
        # The close owner supersedes the already accepted target with final
        # shutdown. Its completion must not request another stopped scenario.
        close_window_safely(window, APP)
        self.assertTrue(window.backend.lifecycle_closed)

    def test_final_completion_restores_all_projections_without_runtime_restart(self):
        window = self.window
        previous = deepcopy(window.current_config_data())
        previous_binds = dict(window.control.binds)
        candidate = self.candidate()
        candidate["modules"]["RobotLink"]["settings"].update({"Bind W": "S", "Bind S": "W"})
        candidate["quick_action_wheel"]["radius"] = 146
        panel = next(iter(candidate["old_panels"].values()))
        panel["x"] += 100
        window.apply_config_data(candidate)
        window.home_visible = False
        window.startup_step = "done"
        self.assertNotEqual(window.control.binds, previous_binds)
        self.assertEqual(window.quick_action_wheel.radius, 146)
        pending = window._pending_configuration
        final = LifecycleCompletion(pending.generation + 1, None, True, ())
        with patch.object(window.backend, "request_scenario") as request:
            self.assertFalse(window.on_backend_lifecycle_completion(final))
        self.assertFalse(window.configuration_change_pending)
        self.assertEqual(window.current_config_data(), previous)
        self.assertEqual(window.control.binds, previous_binds)
        self.assertEqual((window.home_visible, window.startup_step),
                         (pending.previous.home_visible, pending.previous.startup_step))
        request.assert_not_called()
        close_window_safely(window, APP)

    def test_failed_restore_is_reported_and_stays_estopped(self):
        window = self.window
        previous = deepcopy(window.current_config_data())
        with patch.object(window.backend, "set_client_id", side_effect=OSError("identity unavailable")), \
             patch.object(window, "toast") as toast:
            window.apply_config_data(self.candidate())
            with self.assertRaisesRegex(AssertionError, "Window lifecycle failed"):
                settle_configuration(window)
        self.assertEqual(window.current_config_data(), previous)
        self.assertTrue(window.backend.safety.estop_active)
        self.assertFalse(window.configuration_change_pending)
        self.assertIn("恢复未完成", toast.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
