"""Ordinary scenario switches reject direct post-mutation callbacks as well.

The relay is real, its serial factory is inert, and UDP teardown is deliberately
held after angle shutdown. This catches reopening a retired resource through a
settings callback without accessing hardware or allowing live transmission.
"""
from contextlib import contextmanager
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.lifecycle import LifecycleCompletion
import test_configuration_transactions as configuration_tests


class InertSerial:
    def __init__(self, **kwargs):
        self.is_open = True
        self.in_waiting = 0

    def reset_input_buffer(self):
        pass

    def read(self, size):
        return b""

    def close(self):
        self.is_open = False


class ScenarioConfigurationGuardTests(unittest.TestCase):
    setUp = configuration_tests.WindowConfigurationTransactionTests.setUp
    close_window = configuration_tests.WindowConfigurationTransactionTests.close_window

    @contextmanager
    def retired_angle_during_pending_scenario(self):
        window = self.window
        angles = window.backend.controller_angles
        opened = Mock(side_effect=InertSerial)
        angles.serial_factory = opened
        window.registry.get("RobotLink").get_setting("Port").value = "Stub"
        blocked, release, retired = threading.Event(), threading.Event(), threading.Event()
        original_stop, original_wait = window.backend.udp.stop, angles.wait_closed

        def delayed_stop():
            blocked.set()
            if not release.wait(5):
                raise TimeoutError("test UDP teardown was not released")
            original_stop()

        def observed_wait(timeout=None):
            result = original_wait(timeout)
            retired.set()
            return result

        with patch.object(window.backend.udp, "stop", side_effect=delayed_stop), \
             patch.object(angles, "wait_closed", side_effect=observed_wait):
            generation = window.backend.request_scenario("LabDebug", start=False)
            try:
                self.assertTrue(blocked.wait(1))
                self.assertTrue(retired.wait(1))
                self.assertTrue(window.backend.lifecycle_pending)
                self.assertFalse(window.configuration_change_pending)
                yield generation, angles, opened
            finally:
                # A failing pre-fix callback can create a new RX owner after
                # the coordinator already retired this resource. Close it
                # explicitly so the negative regression cannot leak a thread.
                angles.close()
                self.assertTrue(original_wait(2))
                release.set()
                configuration_tests.settle_configuration(window)

    def test_direct_callback_cannot_reopen_retired_angle_even_without_ui_capture(self):
        window = self.window
        with self.retired_angle_during_pending_scenario() as (_, angles, opened):
            module = window.registry.get("ControllerAngles")
            module.enabled = True
            setting = module.get_setting("Angle RX Port")
            setting.value = "REVIEW_RX"
            with patch.object(window, "auto_save_config") as save:
                window.after_setting_changed(setting)
            self.assertEqual(angles.owned_ports, ())
            opened.assert_not_called()
            save.assert_not_called()

    def test_captured_ordinary_registry_restores_values_toggles_and_skips_runtime_effects(self):
        window = self.window
        with self.retired_angle_during_pending_scenario() as (generation, angles, opened):
            window.capture_pending_scenario_configuration(generation)
            module = window.registry.get("ControllerAngles")
            setting = module.get_setting("Angle RX Port")
            previous = setting.value
            module.enabled = True
            setting.value = "REVIEW_RX"
            window.after_setting_changed(setting)
            self.assertEqual(setting.value, previous)
            self.assertFalse(module.enabled)
            module.enabled = True
            window.on_module_toggled(module, True, "clickgui")
            self.assertFalse(module.enabled)
            self.assertEqual(angles.owned_ports, ())
            opened.assert_not_called()
        self.assertIsNone(window._pending_scenario_configuration)

    def test_older_projection_cannot_overwrite_a_newer_programmatic_request(self):
        window = self.window
        with self.retired_angle_during_pending_scenario() as (generation, _, _):
            window.capture_pending_scenario_configuration(generation)
            radius = window.registry.get("QuickActionWheel").get_setting("Radius")
            accepted_new_value = radius.value + 10
            radius.value = accepted_new_value
            newer = window.backend.request_scenario("LabDebug", start=False, registry=window.registry)
            self.assertGreater(newer, generation)
            with patch.object(window, "_sync_wheel_setting") as effect:
                window.after_setting_changed(radius)
            effect.assert_not_called()
            self.assertEqual(radius.value, accepted_new_value)

    def test_stale_completion_cannot_discard_newer_ordinary_snapshot(self):
        window = self.window
        with self.retired_angle_during_pending_scenario() as (generation, _, _):
            window.capture_pending_scenario_configuration(generation)
            snapshot = window._pending_scenario_configuration
            old = LifecycleCompletion(generation - 1, None, False, ())
            self.assertFalse(window.on_backend_lifecycle_completion(old))
            self.assertIs(window._pending_scenario_configuration, snapshot)
            radius = window.registry.get("QuickActionWheel").get_setting("Radius")
            previous = radius.value
            radius.value = previous + 1
            window.after_setting_changed(radius)
            self.assertEqual(radius.value, previous)
        self.assertIsNone(window._pending_scenario_configuration)


if __name__ == "__main__":
    unittest.main()
