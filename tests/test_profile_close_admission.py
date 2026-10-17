"""Unsaved-copy close admission; actual window/model with inert device effects."""
from copy import deepcopy
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_configuration_transactions as fixtures


class ProfileCloseAdmissionTests(unittest.TestCase):
    setUp = fixtures.WindowConfigurationTransactionTests.setUp
    close_window = fixtures.WindowConfigurationTransactionTests.close_window

    def failed_copy(self):
        window = self.window
        self.source = window.config_store.profile_path(window.current_profile())
        self.source_bytes = self.source.read_bytes()
        window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 1.75
        window.registry.get("ConfigManager").get_setting("Profile").value = "unsaved-copy"
        with patch.object(window.config_store, "save", side_effect=OSError("transient copy failure")):
            window.save_current_config()
        self.assertEqual(window._unsaved_profile_name, "unsaved-copy")
        self.target = window.config_store.profile_path("unsaved-copy")

    def test_transient_failure_is_retried_once_at_the_explicit_target(self):
        self.failed_copy()
        window = self.window
        with patch.object(window.config_store, "save", wraps=window.config_store.save) as save:
            self.assertTrue(window.prepare_configuration_for_close())
        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.args[0], "unsaved-copy")
        self.assertIsNone(window._unsaved_profile_name)
        self.assertEqual(window.current_profile(), "unsaved-copy")
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        saved = window.config_store.load("unsaved-copy")
        self.assertEqual(saved["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.75)

    def test_persistent_failure_refuses_close_without_runtime_or_input_changes(self):
        self.failed_copy()
        window = self.window
        before = deepcopy(window.current_config_data())
        generation = window.backend.lifecycle_snapshot.generation
        with patch.object(window.config_store, "save", side_effect=OSError("disk still unavailable")) as save, \
             patch.object(window.backend, "request_shutdown") as shutdown, \
             patch.object(window.control, "clear_pending") as clear, \
             patch.object(window, "toast") as toast:
            self.assertFalse(window.prepare_configuration_for_close())
        self.assertEqual(save.call_count, 1)
        self.assertIn("disk still unavailable", toast.call_args.args[0])
        shutdown.assert_not_called()
        clear.assert_not_called()
        self.assertEqual(window.current_config_data(), before)
        self.assertEqual(window.current_profile(), "unsaved-copy")
        self.assertEqual(window.backend.lifecycle_snapshot.generation, generation)
        self.assertFalse(window.backend.lifecycle_pending)
        self.assertFalse(window.backend.lifecycle_closed)
        self.assertTrue(window.isEnabled())
        self.assertFalse(self.target.exists())
        self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def test_cancelled_close_keeps_editable_draft_without_background_retry(self):
        self.failed_copy()
        window = self.window
        with patch.object(window.config_store, "save", side_effect=OSError("disk unavailable")) as save:
            self.assertFalse(window.prepare_configuration_for_close())
            window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 2.0
            window.auto_save_config()
            window.flush_auto_save(force=True)
            window.flush_auto_save_if_due(float("inf"))
        self.assertEqual(save.call_count, 1)
        self.assertEqual(window._unsaved_profile_name, "unsaved-copy")
        window.save_current_config()
        with patch.object(window.config_store, "save") as save:
            self.assertTrue(window.prepare_configuration_for_close())
            save.assert_not_called()
        self.assertEqual(window.config_store.load("unsaved-copy")["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 2.0)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def test_unaccepted_selector_change_cannot_redirect_close_retry(self):
        self.failed_copy()
        window = self.window
        window.registry.get("ConfigManager").get_setting("Profile").value = "unaccepted-third-name"
        self.assertTrue(window.prepare_configuration_for_close())
        self.assertTrue(self.target.exists())
        self.assertFalse(window.config_store.profile_path("unaccepted-third-name").exists())
        self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def test_pending_profile_is_not_cancelled_or_saved_by_close_preflight(self):
        self.failed_copy()
        window = self.window
        candidate = deepcopy(window.current_config_data())
        candidate["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"] = 2.5
        window.config_store.save("loaded-target", candidate)
        window.refresh_config_profiles()
        window.registry.get("ConfigManager").get_setting("Profile").value = "loaded-target"
        window.reload_current_config()
        pending = window._pending_configuration
        with patch.object(window.config_store, "save") as save:
            self.assertFalse(window.prepare_configuration_for_close())
            save.assert_not_called()
        self.assertIs(window._pending_configuration, pending)
        self.assertFalse(self.target.exists())
        fixtures.settle_configuration(window)
        self.assertTrue(window.prepare_configuration_for_close())
        self.assertEqual(window.current_profile(), "loaded-target")
        self.assertFalse(self.target.exists())

    def test_pending_ordinary_runtime_defers_only_the_unsaved_copy_retry(self):
        self.failed_copy()
        window = self.window
        window.backend.request_scenario("LabDebug", start=False, registry=window.registry)
        with patch.object(window.config_store, "save") as save:
            self.assertFalse(window.prepare_configuration_for_close())
            save.assert_not_called()
        fixtures.settle_configuration(window)
        self.assertTrue(window.prepare_configuration_for_close())

    def test_without_failed_copy_the_existing_close_protocol_is_unchanged(self):
        window = self.window
        window.backend.request_scenario("LabDebug", start=False, registry=window.registry)
        with patch.object(window.config_store, "save") as save:
            self.assertTrue(window.prepare_configuration_for_close())
            save.assert_not_called()
        fixtures.settle_configuration(window)


class AcceptedProfileCloseAdmissionTests(unittest.TestCase):
    setUp = fixtures.WindowConfigurationTransactionTests.setUp
    close_window = fixtures.WindowConfigurationTransactionTests.close_window

    def dirty_source(self):
        window = self.window
        self.profile = window.current_profile()
        self.path = window.config_store.profile_path(self.profile)
        self.before_bytes = self.path.read_bytes()
        window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 1.75
        window.auto_save_config()
        self.owner = window._auto_save_owner

    def test_idle_dirty_profile_is_saved_once_before_close_is_admitted(self):
        self.dirty_source()
        window = self.window
        with patch.object(window.config_store, "save", wraps=window.config_store.save) as save:
            self.assertTrue(window.prepare_configuration_for_close())
        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.args[0], self.profile)
        self.assertFalse(window._auto_save_pending)
        self.assertEqual(window.config_store.load(self.profile)["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.75)

    def test_persistent_failure_retains_dirty_values_owner_and_visible_evidence(self):
        self.dirty_source()
        window = self.window
        window._config_save_error = True  # An earlier autosave was announced.
        with patch.object(window.config_store, "save", side_effect=OSError("close disk unavailable")) as save, \
             patch.object(window.backend, "request_shutdown") as shutdown, \
             patch.object(window, "toast") as toast:
            self.assertFalse(window.prepare_configuration_for_close())
        self.assertEqual(save.call_count, 1)
        shutdown.assert_not_called()
        self.assertEqual(window._auto_save_owner, self.owner)
        self.assertTrue(window._auto_save_pending)
        self.assertTrue(window.isEnabled())
        self.assertFalse(window.backend.lifecycle_closed)
        self.assertEqual(window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value, 1.75)
        self.assertEqual(self.path.read_bytes(), self.before_bytes)
        self.assertIn("close disk unavailable", toast.call_args.args[0])

    def test_transient_save_failure_can_recover_on_one_close_retry(self):
        self.dirty_source()
        window = self.window
        with patch.object(window.config_store, "save", side_effect=OSError("transient disk failure")):
            window.save_current_config(announce=False)
        with patch.object(window.config_store, "save", wraps=window.config_store.save) as save:
            self.assertTrue(window.prepare_configuration_for_close())
        self.assertEqual(save.call_count, 1)
        self.assertFalse(window._auto_save_pending)

    def test_refused_close_can_be_cancelled_then_manually_saved(self):
        self.dirty_source()
        window = self.window
        with patch.object(window.config_store, "save", side_effect=OSError("disk failure")):
            self.assertFalse(window.prepare_configuration_for_close())
        window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 2.0
        window.auto_save_config()
        window.save_current_config()
        with patch.object(window.config_store, "save") as save:
            self.assertTrue(window.prepare_configuration_for_close())
            save.assert_not_called()
        self.assertEqual(window.config_store.load(self.profile)["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 2.0)

    def test_refused_close_pauses_automatic_writes_until_explicit_retry(self):
        self.dirty_source()
        window = self.window
        with patch.object(window.config_store, "save", side_effect=OSError("persistent close failure")) as save:
            self.assertFalse(window.prepare_configuration_for_close())
            window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 2.0
            window.auto_save_config()
            window.flush_auto_save(force=True)
            window.flush_auto_save_if_due(float("inf"))
            self.assertEqual(save.call_count, 1)
        self.assertTrue(window._auto_save_pending)
        self.assertTrue(window._close_save_retry_required)
        window.save_current_config()
        self.assertFalse(window._close_save_retry_required)
        self.assertFalse(window._auto_save_pending)
        self.assertEqual(window.config_store.load(self.profile)["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 2.0)
        window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 2.25
        window.auto_save_config()
        window.flush_auto_save(force=True)
        self.assertEqual(window.config_store.load(self.profile)["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 2.25)

    def test_dirty_owner_cannot_be_redirected_by_an_unaccepted_selector(self):
        self.dirty_source()
        window = self.window
        other = window.config_store.save("other-profile", {"selected_category": "keep"})
        other_bytes = other.read_bytes()
        window.registry.get("ConfigManager").get_setting("Profile").value = "other-profile"
        self.assertTrue(window.prepare_configuration_for_close())
        self.assertEqual(other.read_bytes(), other_bytes)
        self.assertEqual(window.config_store.load(self.profile)["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.75)


if __name__ == "__main__":
    unittest.main()
