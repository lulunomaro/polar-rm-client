"""Caught-I/O rollback for profile/startup files, not crash atomicity."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client import config_store
from rm_client.configuration.files import FileSnapshot


class ProfileFileTransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = config_store.ClientConfigStore(Path(self.directory.name))
        self.profile = self.store.profile_path("source")
        self.startup = self.store.config_dir / ".startup.json"
        # Exact old bytes include formatting and may be invalid legacy JSON.
        self.profile.write_bytes(b'{ "version": 1, "old": "keep formatting" }\n')
        self.startup.write_bytes(b'{ "profile": "source", "load_on_start": false }\n')
        self.original = (self.profile.read_bytes(), self.startup.read_bytes())
        self.unrelated = self.store.profile_path("another-user-profile")
        self.unrelated.write_bytes(b"user bytes must never be removed")

    def save(self, profile="source"):
        return self.store.save_with_startup(profile, {"selected_category": "new"}, load_on_start=True)

    def assert_original(self):
        self.assertEqual((self.profile.read_bytes(), self.startup.read_bytes()), self.original)
        self.assertEqual(self.unrelated.read_bytes(), b"user bytes must never be removed")

    def test_second_write_failure_restores_both_existing_files_exactly(self):
        with patch.object(self.store, "save_startup_preferences", side_effect=OSError("startup disk failure")):
            with self.assertRaisesRegex(OSError, "startup disk failure") as caught:
                self.save()
        self.assertTrue(caught.exception.restored)
        self.assert_original()

    def test_second_write_failure_after_replacement_restores_both_files(self):
        original = self.store.save_startup_preferences

        def write_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("failure after startup replacement")

        with patch.object(self.store, "save_startup_preferences", side_effect=write_then_fail):
            with self.assertRaisesRegex(OSError, "failure after startup replacement"):
                self.save()
        self.assert_original()

    def test_first_write_failure_after_replacement_also_rolls_back(self):
        original = self.store.save

        def write_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("failure after profile replacement")

        with patch.object(self.store, "save", side_effect=write_then_fail), \
             patch.object(self.store, "save_startup_preferences") as startup:
            with self.assertRaisesRegex(OSError, "failure after profile replacement"):
                self.save()
            startup.assert_not_called()
        self.assert_original()

    def test_missing_profile_is_removed_only_when_created_by_failed_save(self):
        target = self.store.profile_path("new-copy")
        with patch.object(self.store, "save_startup_preferences", side_effect=OSError("startup failed")):
            with self.assertRaises(OSError):
                self.save("new-copy")
        self.assertFalse(target.exists())
        self.assert_original()

    def test_missing_startup_file_remains_missing_after_failed_replacement(self):
        self.startup.unlink()
        original = self.store.save_startup_preferences

        def write_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("startup failed after create")

        with patch.object(self.store, "save_startup_preferences", side_effect=write_then_fail):
            with self.assertRaises(OSError):
                self.save()
        self.assertFalse(self.startup.exists())
        self.assertEqual(self.profile.read_bytes(), self.original[0])
        self.assertTrue(self.unrelated.exists())

    def test_invalid_existing_bytes_are_preserved_when_save_fails(self):
        self.profile.write_bytes(b"legacy-corrupt\x00\xff bytes")
        self.original = (self.profile.read_bytes(), self.startup.read_bytes())
        with patch.object(self.store, "save_startup_preferences", side_effect=OSError("startup failed")):
            with self.assertRaises(OSError):
                self.save()
        self.assert_original()

    def test_rollback_failure_is_observable_and_does_not_skip_other_restore(self):
        original_write = self.store.save_startup_preferences
        original_restore = FileSnapshot.restore
        attempts = []

        def write_then_fail(*args, **kwargs):
            original_write(*args, **kwargs)
            raise OSError("original save failure")

        def restore(snapshot):
            attempts.append(snapshot.path)
            if snapshot.path == self.startup:
                raise OSError("startup restoration denied")
            original_restore(snapshot)

        with patch.object(self.store, "save_startup_preferences", side_effect=write_then_fail), \
             patch.object(FileSnapshot, "restore", restore):
            with self.assertRaises(OSError) as caught:
                self.save()
        self.assertFalse(caught.exception.restored)
        self.assertEqual(attempts, [self.startup, self.profile])
        self.assertIn("original save failure", str(caught.exception))
        self.assertIn("startup restoration denied", str(caught.exception))
        self.assertIn(".startup.json", str(caught.exception))
        self.assertEqual(self.profile.read_bytes(), self.original[0])
        self.assertTrue(self.store.load_startup_preferences()["load_on_start"])
        self.assertTrue(self.unrelated.exists())

    def test_all_failed_restorations_are_retained(self):
        with patch.object(self.store, "save_startup_preferences", side_effect=OSError("save failed")), \
             patch.object(FileSnapshot, "restore", side_effect=OSError("restore unavailable")) as restore:
            with self.assertRaises(OSError) as caught:
                self.save()
        self.assertEqual(restore.call_count, 2)
        self.assertEqual(len(caught.exception.restore_errors), 2)
        self.assertIn(".startup.json", str(caught.exception))
        self.assertIn("source.json", str(caught.exception))
        self.assertFalse(caught.exception.restored)
        self.assertTrue(self.profile.exists())
        self.assertTrue(self.unrelated.exists())

    def test_invalid_startup_flag_is_rejected_before_any_write(self):
        with patch.object(self.store, "save") as save:
            with self.assertRaisesRegex(ValueError, "boolean"):
                self.store.save_with_startup("source", {}, load_on_start="yes")
            save.assert_not_called()
        self.assert_original()

    def test_success_updates_both_files_without_touching_another_profile(self):
        self.assertEqual(self.save(), self.profile)
        self.assertEqual(self.store.load("source")["selected_category"], "new")
        self.assertEqual(self.store.load_startup_preferences(), {"profile": "source", "load_on_start": True})
        self.assertEqual(self.unrelated.read_bytes(), b"user bytes must never be removed")
        self.assertFalse(list(self.store.config_dir.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
