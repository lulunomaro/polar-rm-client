"""Real-window profile selection and autosave ownership; no device I/O."""
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


class ProfileSaveIdentityTests(unittest.TestCase):
    setUp = fixtures.WindowConfigurationTransactionTests.setUp
    close_window = fixtures.WindowConfigurationTransactionTests.close_window

    def create_target(self):
        candidate = deepcopy(self.window.current_config_data())
        candidate["modules"]["AllianceProfile"]["settings"]["Side"] = "Blue"
        candidate["modules"]["RobotProfile"]["settings"]["Role"] = "Infantry1"
        candidate["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"] = 2.0
        path = self.window.config_store.save("auditB", candidate)
        self.window.refresh_config_profiles()
        return path

    def select_target(self, name="auditB"):
        setting = self.window.registry.get("ConfigManager").get_setting("Profile")
        setting.value = name
        self.window.after_setting_changed(setting)

    def sensitivity(self):
        return self.window.registry.get("RobotLink").get_setting("Mouse Sensitivity")

    def queue_old_edit(self):
        self.sensitivity().value = 1.25
        self.window.auto_save_config()
        self.assertTrue(self.window._auto_save_pending)

    def test_failed_selection_never_redirects_old_pending_data_into_target(self):
        window = self.window
        old_profile = window.current_profile()
        target = self.create_target()
        target_bytes = target.read_bytes()
        self.queue_old_edit()
        original_identity = window.backend.set_client_id
        rejected = []

        def fail_new_identity(robot_id):
            if str(robot_id) == "103":
                rejected.append(robot_id)
                raise OSError("audit activation failure")
            original_identity(robot_id)

        with patch.object(window.backend, "set_client_id", side_effect=fail_new_identity):
            self.select_target()
            fixtures.settle_configuration(window)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(self.sensitivity().value, 1.25)
        window.flush_auto_save(force=True)
        self.assertEqual(target.read_bytes(), target_bytes)
        self.assertEqual(window.current_profile(), old_profile)
        saved_old = window.config_store.load(old_profile)
        self.assertEqual(saved_old["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.25)
        self.assertTrue(window.backend.safety.estop_active)

    def test_successful_switch_saves_old_pending_data_only_to_old_profile(self):
        window = self.window
        old_profile = window.current_profile()
        target = self.create_target()
        target_bytes = target.read_bytes()
        self.queue_old_edit()
        self.select_target()
        self.assertTrue(window.configuration_change_pending)
        fixtures.settle_configuration(window)
        self.assertEqual(window.current_profile(), "auditB")
        self.assertEqual(self.sensitivity().value, 2.0)
        window.flush_auto_save(force=True)
        self.assertEqual(target.read_bytes(), target_bytes)
        old = window.config_store.load(old_profile)
        self.assertEqual(old["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.25)
        self.sensitivity().value = 2.25
        window.auto_save_config()
        window.flush_auto_save(force=True)
        self.assertEqual(window.config_store.load("auditB")["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 2.25)
        self.assertEqual(window.config_store.load(old_profile), old)

    def test_old_save_failure_cancels_switch_and_keeps_original_retry(self):
        window = self.window
        old_profile = window.current_profile()
        target = self.create_target()
        target_bytes = target.read_bytes()
        self.queue_old_edit()
        with patch.object(window.config_store, "save", side_effect=OSError("disk unavailable")), \
             patch.object(window.backend, "request_scenario") as request:
            self.select_target()
            request.assert_not_called()
        self.assertEqual(window.current_profile(), old_profile)
        self.assertEqual(self.sensitivity().value, 1.25)
        self.assertTrue(window._auto_save_pending)
        self.assertEqual(target.read_bytes(), target_bytes)
        window.flush_auto_save(force=True)
        self.assertFalse(window._auto_save_pending)
        self.assertEqual(target.read_bytes(), target_bytes)

    def test_missing_target_restores_selection_and_preserves_pending_edit(self):
        window = self.window
        old_profile = window.current_profile()
        self.queue_old_edit()
        self.select_target("missing-profile")
        self.assertEqual(window.current_profile(), old_profile)
        self.assertTrue(window._auto_save_pending)
        window.flush_auto_save(force=True)
        self.assertFalse(window.config_store.profile_path("missing-profile").exists())

    def test_explicit_save_as_accepts_new_identity_without_overwriting_source(self):
        window = self.window
        old_profile = window.current_profile()
        window.save_current_config(announce=False)
        old_bytes = window.config_store.profile_path(old_profile).read_bytes()
        self.queue_old_edit()
        window.registry.get("ConfigManager").get_setting("Profile").value = "saved-copy"
        window.save_current_config()
        self.assertEqual(window.current_profile(), "saved-copy")
        self.sensitivity().value = 1.5
        window.auto_save_config()
        window.flush_auto_save(force=True)
        self.assertEqual(window.config_store.profile_path(old_profile).read_bytes(), old_bytes)
        self.assertEqual(window.config_store.load("saved-copy")["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.5)

    def test_stale_autosave_owner_cannot_write_the_new_live_model(self):
        window = self.window
        self.create_target()
        self.queue_old_edit()
        old_owner = window._auto_save_owner
        self.select_target()
        fixtures.settle_configuration(window)
        target = window.config_store.profile_path("auditB")
        target_bytes = target.read_bytes()
        window._auto_save_pending = True
        window._auto_save_due_at = 0.0
        window._auto_save_owner = old_owner
        with patch.object(window.config_store, "save", wraps=window.config_store.save) as save:
            window.flush_auto_save(force=True)
            save.assert_not_called()
        self.assertEqual(target.read_bytes(), target_bytes)

    def test_same_profile_reload_invalidates_the_previous_autosave_generation(self):
        window = self.window
        source = window.config_store.profile_path(window.current_profile())
        source_bytes = source.read_bytes()
        self.queue_old_edit()
        old_owner = window._auto_save_owner
        window.reload_current_config()
        fixtures.settle_configuration(window)
        self.assertEqual(window._accepted_profile.name, old_owner.name)
        self.assertGreater(window._accepted_profile.generation, old_owner.generation)
        self.sensitivity().value = 1.75
        window._auto_save_pending = True
        window._auto_save_due_at = 0.0
        window._auto_save_owner = old_owner
        window.flush_auto_save(force=True)
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_invalid_profile_preflight_restores_accepted_selection(self):
        window = self.window
        old_profile = window.current_profile()
        target = self.create_target()
        target_bytes = target.read_bytes()
        self.queue_old_edit()
        window.registry.get("ConfigManager").get_setting("Profile").value = "auditB"
        window.apply_config_data({"modules": {"HUD": {"enabled": "invalid"}}})
        self.assertEqual(window.current_profile(), old_profile)
        self.assertEqual(self.sensitivity().value, 1.25)
        self.assertTrue(window._auto_save_pending)
        window.flush_auto_save(force=True)
        self.assertEqual(target.read_bytes(), target_bytes)

    def test_failed_explicit_save_as_cannot_fall_back_to_overwriting_source(self):
        window = self.window
        source = window.config_store.profile_path(window.current_profile())
        source_bytes = source.read_bytes()
        self.queue_old_edit()
        window.registry.get("ConfigManager").get_setting("Profile").value = "saved-copy"
        with patch.object(window.config_store, "save", side_effect=OSError("copy unavailable")), \
             patch.object(window, "toast") as toast:
            window.save_current_config()
        self.assertIn("另存失败", toast.call_args.args[0])
        self.assertEqual(window.current_profile(), "saved-copy")
        self.assertEqual(self.sensitivity().value, 1.25)
        window.auto_save_config()
        window.flush_auto_save(force=True)
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertFalse(window.config_store.profile_path("saved-copy").exists())
        window.save_current_config()
        self.assertTrue(window.config_store.profile_path("saved-copy").exists())
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_failed_save_as_remains_protected_after_missing_reload_and_invalid_load(self):
        window = self.window
        source = window.config_store.profile_path(window.current_profile())
        source_bytes = source.read_bytes()
        self.queue_old_edit()
        window.registry.get("ConfigManager").get_setting("Profile").value = "unsaved-copy"
        with patch.object(window.config_store, "save", side_effect=OSError("copy unavailable")):
            window.save_current_config()
        window.reload_current_config()
        self.sensitivity().value = 1.5
        window.auto_save_config()
        window.flush_auto_save(force=True)
        self.assertEqual(source.read_bytes(), source_bytes)
        window.registry.get("ConfigManager").get_setting("Profile").value = "default"
        window.apply_config_data({"modules": {"HUD": {"enabled": "invalid"}}})
        window.auto_save_config()
        window.flush_auto_save(force=True)
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertEqual(window.current_profile(), "unsaved-copy")
        window.save_current_config()
        self.assertEqual(window.current_profile(), "unsaved-copy")
        self.assertEqual(window.config_store.load("unsaved-copy")["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.5)
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_failed_save_as_allows_explicit_third_target_without_writing_source(self):
        window = self.window
        source = window.config_store.profile_path(window.current_profile())
        source_bytes = source.read_bytes()
        self.queue_old_edit()
        window.registry.get("ConfigManager").get_setting("Profile").value = "failed-copy"
        with patch.object(window.config_store, "save", side_effect=OSError("copy unavailable")):
            window.save_current_config()
        window.registry.get("ConfigManager").get_setting("Profile").value = "third-copy"
        window.save_current_config()
        self.assertEqual(window.current_profile(), "third-copy")
        self.assertFalse(window.config_store.profile_path("failed-copy").exists())
        self.assertTrue(window.config_store.profile_path("third-copy").exists())
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertIsNone(window._unsaved_profile_name)

    def test_successful_load_releases_failed_save_as_draft_protection(self):
        window = self.window
        self.create_target()
        self.queue_old_edit()
        window.registry.get("ConfigManager").get_setting("Profile").value = "failed-copy"
        with patch.object(window.config_store, "save", side_effect=OSError("copy unavailable")):
            window.save_current_config()
        self.select_target()
        fixtures.settle_configuration(window)
        self.assertIsNone(window._unsaved_profile_name)
        self.assertEqual(window.current_profile(), "auditB")
        self.sensitivity().value = 2.25
        window.auto_save_config()
        window.flush_auto_save(force=True)
        self.assertEqual(window.config_store.load("auditB")["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 2.25)

    def test_failed_same_name_import_preserves_import_and_latest_source_backup(self):
        window = self.window
        source = Path(self.directory.name) / "incoming" / (window.current_profile() + ".json")
        source.parent.mkdir()
        candidate = deepcopy(window.current_config_data())
        candidate["modules"]["AllianceProfile"]["settings"]["Side"] = "Blue"
        candidate["modules"]["RobotProfile"]["settings"]["Role"] = "Infantry1"
        candidate["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"] = 2.0
        import json
        source.write_text(json.dumps(candidate), encoding="utf-8")
        self.queue_old_edit()
        original_identity = window.backend.set_client_id
        rejected = []

        def fail_new_identity(robot_id):
            if str(robot_id) == "103":
                rejected.append(robot_id)
                raise OSError("same-name import activation failure")
            original_identity(robot_id)

        with patch("rm_client.ui.polar.mixins.persistence_mixin.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch.object(window.backend, "set_client_id", side_effect=fail_new_identity):
            window.import_config_from_dialog()
            target = window.config_store.profile_path(window.current_profile())
            imported_bytes = target.read_bytes()
            fixtures.settle_configuration(window)
        self.assertEqual(len(rejected), 1)
        window.flush_auto_save(force=True)
        self.assertEqual(target.read_bytes(), imported_bytes)
        backup = json.loads(target.with_suffix(".json.bak").read_text(encoding="utf-8"))
        self.assertEqual(backup["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.25)
        self.assertEqual(self.sensitivity().value, 1.25)

    def test_explicit_save_back_to_source_releases_failed_copy_protection(self):
        window = self.window
        source_name = window.current_profile()
        self.queue_old_edit()
        window.registry.get("ConfigManager").get_setting("Profile").value = "failed-copy"
        with patch.object(window.config_store, "save", side_effect=OSError("copy unavailable")):
            window.save_current_config()
        window.registry.get("ConfigManager").get_setting("Profile").value = source_name
        self.sensitivity().value = 1.5
        window.save_current_config()
        self.assertIsNone(window._unsaved_profile_name)
        self.sensitivity().value = 1.75
        window.auto_save_config()
        window.flush_auto_save(force=True)
        self.assertEqual(window.config_store.load(source_name)["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.75)
        self.assertFalse(window.config_store.profile_path("failed-copy").exists())

    def test_failed_source_retry_keeps_its_explicit_draft_target(self):
        window = self.window
        source_name = window.current_profile()
        source = window.config_store.profile_path(source_name)
        source_bytes = source.read_bytes()
        self.queue_old_edit()
        window.registry.get("ConfigManager").get_setting("Profile").value = "failed-copy"
        with patch.object(window.config_store, "save", side_effect=OSError("copy unavailable")):
            window.save_current_config()
            window.registry.get("ConfigManager").get_setting("Profile").value = source_name
            window.save_current_config()
        self.assertEqual(window._unsaved_profile_name, source_name)
        self.assertFalse(window._auto_save_pending)
        window.flush_auto_save(force=True)
        self.assertEqual(source.read_bytes(), source_bytes)
        window.save_current_config()
        self.assertIsNone(window._unsaved_profile_name)

    def test_startup_write_failure_preserves_both_files_and_retries_live_draft(self):
        window = self.window
        window.save_current_config(announce=False)
        source = window.config_store.profile_path(window.current_profile())
        window.config_store.save_startup_preferences(window.current_profile(), False)
        startup = window.config_store.config_dir / ".startup.json"
        previous = source.read_bytes(), startup.read_bytes()
        self.sensitivity().value = 1.5
        with patch.object(window.config_store, "save_startup_preferences", side_effect=OSError("startup disk failure")), \
             patch.object(window, "toast") as toast:
            window.save_current_config(announce=False)
        self.assertIn("startup disk failure", toast.call_args.args[0])
        self.assertEqual((source.read_bytes(), startup.read_bytes()), previous)
        self.assertTrue(window._auto_save_pending)
        self.assertEqual(self.sensitivity().value, 1.5)
        window.flush_auto_save(force=True)
        self.assertFalse(window._auto_save_pending)
        self.assertEqual(window.config_store.load(window.current_profile())["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.5)
        self.assertTrue(window.config_store.load_startup_preferences()["load_on_start"])

    def test_save_as_second_file_failure_preserves_source_and_target_absence(self):
        window = self.window
        source = window.config_store.profile_path(window.current_profile())
        source_bytes = source.read_bytes()
        self.queue_old_edit()
        window.registry.get("ConfigManager").get_setting("Profile").value = "new-copy"
        with patch.object(window.config_store, "save_startup_preferences", side_effect=OSError("startup disk failure")):
            window.save_current_config()
        window.flush_auto_save(force=True)
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertFalse(window.config_store.profile_path("new-copy").exists())
        self.assertEqual(window.current_profile(), "new-copy")
        window.save_current_config()
        self.assertTrue(window.config_store.profile_path("new-copy").exists())
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_later_restore_failure_is_visible_even_after_prior_save_error(self):
        from rm_client.configuration.files import FileSnapshot
        window = self.window
        self.sensitivity().value = 1.5
        with patch.object(window.config_store, "save", side_effect=OSError("first save failure")):
            window.save_current_config(announce=False)
        self.assertTrue(window._config_save_error)
        with patch.object(window.config_store, "save_startup_preferences", side_effect=OSError("later startup failure")), \
             patch.object(FileSnapshot, "restore", side_effect=OSError("restoration denied")), \
             patch.object(window, "toast") as toast, \
             self.assertLogs("rm_client.ui.polar.mixins.persistence_mixin", level="ERROR") as logs:
            window.save_current_config(announce=False)
        self.assertEqual(toast.call_count, 1)
        self.assertIn("restoration denied", toast.call_args.args[0])
        self.assertIn("restoration denied", "\n".join(logs.output))
        self.assertTrue(window._auto_save_pending)
        evidence = window._last_profile_recovery_error
        window.flush_auto_save(force=True)
        self.assertFalse(window._config_save_error)
        self.assertEqual(window._last_profile_recovery_error, evidence)

    def test_close_cancels_target_without_overwriting_its_file(self):
        window = self.window
        old_profile = window.current_profile()
        target = self.create_target()
        target_bytes = target.read_bytes()
        self.queue_old_edit()
        self.select_target()
        window.cancel_pending_configuration_for_close()
        self.assertEqual(window.current_profile(), old_profile)
        self.assertEqual(self.sensitivity().value, 1.25)
        window.flush_auto_save(force=True)
        self.assertEqual(target.read_bytes(), target_bytes)
        # Let the ordinary teardown request finish before fixture shutdown.
        fixtures.settle_configuration(window)


if __name__ == "__main__":
    unittest.main()
