"""Real close admission while configuration workers own a staged document.

Offscreen widgets and actual owner-thread completion routing are exercised.
Device boundaries are inert; this does not qualify hardware or native drivers.
"""
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend import teardown_plan
from rm_client.configuration.pending import ConfigurationPhase
import test_configuration_transactions as fixtures
from window_lifecycle_helpers import close_window_safely


class PendingProfileCloseTests(unittest.TestCase):
    setUp = fixtures.WindowConfigurationTransactionTests.setUp
    close_window = fixtures.WindowConfigurationTransactionTests.close_window

    def dirty_source(self):
        window = self.window
        self.profile = window.current_profile()
        self.path = window.config_store.profile_path(self.profile)
        self.disk = self.path.read_bytes()
        window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 1.75
        window.auto_save_config()
        # Isolate explicit close admission from the normal debounce timer.
        window._auto_save_due_at = float("inf")
        self.owner = window._auto_save_owner
        self.previous = deepcopy(window.current_config_data())

    def candidate(self):
        candidate = deepcopy(self.window.current_config_data())
        candidate["modules"]["AllianceProfile"]["settings"]["Side"] = "Blue"
        candidate["modules"]["RobotProfile"]["settings"]["Role"] = "Infantry1"
        candidate["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"] = 2.5
        return candidate

    @contextmanager
    def blocked_teardown(self, *, skip=0):
        gate, entered = threading.Event(), threading.Event()
        original = teardown_plan.wait_for_mqtt
        calls = 0

        def wait(mqtt):
            nonlocal calls
            calls += 1
            if calls <= skip:
                return original(mqtt)
            entered.set()
            if not gate.wait(5):
                raise TimeoutError("test did not release teardown")
            original(mqtt)

        with patch.object(teardown_plan, "wait_for_mqtt", side_effect=wait):
            try:
                yield entered, gate
            finally:
                gate.set()

    def assert_close_deferred(self, pending):
        window = self.window
        with patch.object(window.config_store, "save") as save, \
             patch.object(window.backend, "request_shutdown") as shutdown, \
             patch.object(window, "cancel_pending_configuration_for_close") as cancel, \
             patch.object(window, "toast") as toast:
            self.assertFalse(window.close())
        save.assert_not_called()
        shutdown.assert_not_called()
        cancel.assert_not_called()
        self.assertIs(getattr(window, "_pending_configuration", None), pending)
        self.assertFalse(window._window_lifecycle.closing)
        self.assertTrue(window.isEnabled())
        self.assertFalse(window.backend.lifecycle_closed)
        self.assertEqual(window.backend.safety.control_state.value, "LOCKED")
        self.assertTrue(window._auto_save_pending)
        self.assertEqual(window._auto_save_owner, self.owner)
        self.assertEqual(self.path.read_bytes(), self.disk)
        messages = "\n".join(call.args[0] for call in toast.call_args_list)
        self.assertIn("配置切换尚未完成", messages)
        self.assertIn("稍后重试关闭", messages)

    def test_same_name_reload_retains_pending_snapshot_until_explicit_reload_completes(self):
        self.window.config_store.save(self.window.current_profile(), self.candidate())
        self.dirty_source()
        window = self.window
        with self.blocked_teardown() as (entered, gate):
            window.reload_current_config()
            self.assertTrue(entered.wait(0.5))
            pending = window._pending_configuration
            self.assertEqual(pending.previous.document, self.previous)
            self.assertEqual(window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value, 2.5)
            self.assert_close_deferred(pending)
            gate.set()
            fixtures.settle_configuration(window)
        # Explicit reload accepted the disk document; its old timer must not
        # write either the discarded edits or the newly staged projection.
        self.assertFalse(window._auto_save_pending)
        self.assertFalse(window.configuration_change_pending)
        with patch.object(window.config_store, "save") as save:
            close_window_safely(window, fixtures.APP)
        save.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.disk)

    def test_failed_same_name_activation_preserves_dirty_source_through_close_failure(self):
        self.window.config_store.save(self.window.current_profile(), self.candidate())
        self.dirty_source()
        window = self.window
        original_identity = window.backend.set_client_id

        def fail_candidate(robot_id):
            if str(robot_id) == "103":
                raise OSError("candidate activation failed")
            return original_identity(robot_id)

        with patch.object(window.backend, "set_client_id", side_effect=fail_candidate):
            with self.blocked_teardown() as (entered, gate):
                window.reload_current_config()
                self.assertTrue(entered.wait(0.5))
                self.assert_close_deferred(window._pending_configuration)
                gate.set()
                fixtures.settle_configuration(window)
        self.assertEqual(window.current_config_data(), self.previous)
        self.assertEqual(window._auto_save_owner, self.owner)
        self.assertTrue(window._auto_save_pending)
        self.assertTrue(window.backend.safety.estop_active)
        with patch.object(window.config_store, "save", side_effect=OSError("source remains unavailable")) as save, \
             patch.object(window, "toast") as toast:
            self.assertFalse(window.close())
            window.on_tick()
            window.flush_auto_save(force=True)
        save.assert_called_once()
        self.assertFalse(window._window_lifecycle.closing)
        self.assertTrue(window._auto_save_pending)
        self.assertEqual(window.current_config_data(), self.previous)
        self.assertEqual(self.path.read_bytes(), self.disk)
        self.assertIn("source remains unavailable", "\n".join(call.args[0] for call in toast.call_args_list))
        with patch.object(window.config_store, "save", wraps=window.config_store.save) as save:
            close_window_safely(window, fixtures.APP)
        save.assert_called_once()
        saved = window.config_store.load(self.profile)
        self.assertEqual(saved["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.75)
        self.assertEqual(saved["modules"]["AllianceProfile"]["settings"]["Side"], "Red")

    def test_close_during_async_restore_keeps_restore_point_and_dirty_owner(self):
        self.dirty_source()
        window = self.window
        original_identity = window.backend.set_client_id

        def fail_candidate(robot_id):
            if str(robot_id) == "103":
                raise OSError("candidate activation failed")
            return original_identity(robot_id)

        with patch.object(window.backend, "set_client_id", side_effect=fail_candidate):
            with self.blocked_teardown(skip=1) as (entered, gate):
                window.apply_config_data(self.candidate())
                deadline = time.monotonic() + 3
                while window._pending_configuration.phase is ConfigurationPhase.APPLYING:
                    self.assertLess(time.monotonic(), deadline)
                    window.on_tick()
                    fixtures.APP.processEvents()
                    time.sleep(0.005)
                self.assertTrue(entered.wait(0.5))
                pending = window._pending_configuration
                self.assertEqual(pending.phase, ConfigurationPhase.RESTORING)
                self.assert_close_deferred(pending)
                self.assertEqual(window.current_config_data(), self.previous)
                gate.set()
                fixtures.settle_configuration(window)
        self.assertEqual(window.current_config_data(), self.previous)
        close_window_safely(window, fixtures.APP)
        saved = window.config_store.load(self.profile)
        self.assertEqual(saved["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.75)

    def test_ordinary_runtime_transition_can_finish_before_retrying_dirty_close(self):
        self.dirty_source()
        window = self.window
        with self.blocked_teardown() as (entered, gate):
            window.backend.request_scenario("LabDebug", start=False, registry=window.registry)
            self.assertTrue(entered.wait(0.5))
            self.assert_close_deferred(None)
            gate.set()
            fixtures.settle_configuration(window)
        with patch.object(window.config_store, "save", wraps=window.config_store.save) as save:
            close_window_safely(window, fixtures.APP)
        save.assert_called_once()
        self.assertTrue(window.backend.lifecycle_closed)

    def test_clean_pending_load_can_cancel_before_final_shutdown_without_staged_write(self):
        window = self.window
        previous = deepcopy(window.current_config_data())
        path = window.config_store.profile_path(window.current_profile())
        before_bytes = path.read_bytes()
        with self.blocked_teardown() as (entered, gate), \
             patch.object(window.config_store, "save") as save, \
             patch.object(window.backend, "request_scenario", wraps=window.backend.request_scenario) as request:
            window.apply_config_data(self.candidate())
            self.assertTrue(entered.wait(0.5))
            self.assertFalse(window.configuration_close_requires_save)
            self.assertFalse(window.close())
            self.assertTrue(window._window_lifecycle.closing)
            self.assertFalse(window.configuration_change_pending)
            self.assertEqual(window.current_config_data(), previous)
            gate.set()
            close_window_safely(window, fixtures.APP)
        request.assert_called_once()  # Cancellation must not start a restore runtime.
        save.assert_not_called()
        self.assertEqual(path.read_bytes(), before_bytes)
        self.assertTrue(window.backend.lifecycle_closed)


if __name__ == "__main__":
    unittest.main()
