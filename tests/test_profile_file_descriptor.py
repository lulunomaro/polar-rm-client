"""Profile text-writer ownership before stream admission, using real descriptors."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client import config_store
from rm_client.configuration.files import FileWriteError


class ProfileDescriptorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = config_store.ClientConfigStore(Path(self.directory.name))
        self.path = self.store.profile_path("profile")
        self.path.write_bytes(b'{ "old": "keep exact bytes" }\n')
        self.original = self.path.read_bytes()
        self.opened = []
        original_mkstemp = tempfile.mkstemp

        def track(*args, **kwargs):
            result = original_mkstemp(*args, **kwargs)
            self.opened.append(result)
            return result

        patcher = patch.object(config_store.tempfile, "mkstemp", track)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.close_remaining)

    def close_remaining(self):
        for descriptor, _ in self.opened:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def assert_closed(self):
        for descriptor, _ in self.opened:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_profile_fdopen_rejection_closes_descriptor_and_preserves_original(self):
        primary = OSError("text stream admission rejected")
        with patch.object(config_store.os, "fdopen", side_effect=primary), self.assertRaises(OSError) as caught:
            self.store.save("profile", {"selected_category": "new"})
        self.assertIs(caught.exception, primary)
        self.assertEqual(len(self.opened), 1)
        self.assert_closed()
        self.assertFalse(Path(self.opened[0][1]).exists())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_startup_writer_has_the_same_admission_ownership(self):
        with patch.object(config_store.os, "fdopen", side_effect=OSError("startup admission")):
            with self.assertRaises(OSError):
                self.store.save_startup_preferences("profile", True)
        self.assert_closed()
        self.assertFalse((self.store.config_dir / ".startup.json").exists())

    def test_success_keeps_utf8_newlines_and_stream_closes_without_raw_double_close(self):
        real_close = os.close
        with patch.object(config_store.os, "close", wraps=real_close) as close:
            self.store.save("profile", {"selected_category": "界面"})
        close.assert_not_called()
        self.assert_closed()
        contents = self.path.read_bytes()
        self.assertIn("界面".encode(), contents)
        self.assertIn(b'\n', contents)
        self.assertNotIn(b'\r\n', contents)
        self.assertEqual(self.store.load("profile")["selected_category"], "界面")

    def test_failed_raw_close_keeps_both_errors_and_still_unlinks(self):
        primary, cleanup = OSError("fdopen"), OSError("close")
        with patch.object(config_store.os, "fdopen", side_effect=primary), \
             patch.object(config_store.os, "close", side_effect=cleanup):
            with self.assertRaises(FileWriteError) as caught:
                self.store.save("profile", {})
        self.assertIs(caught.exception.cause, primary)
        self.assertIs(caught.exception.cleanup_error, cleanup)
        self.assertFalse(Path(self.opened[0][1]).exists())
        os.fstat(self.opened[0][0])  # The rejecting close leaves ownership to this fixture.
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_replace_and_unlink_failures_are_both_observable(self):
        primary, cleanup = OSError("replace"), PermissionError("unlink")
        with patch.object(config_store.os, "replace", side_effect=primary), \
             patch.object(Path, "unlink", side_effect=cleanup):
            with self.assertRaises(FileWriteError) as caught:
                self.store.save("profile", {})
        self.assertIs(caught.exception.cause, primary)
        self.assertIs(caught.exception.cleanup_error, cleanup)
        self.assert_closed()
        self.assertTrue(Path(self.opened[0][1]).exists())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_admission_close_and_unlink_failures_are_all_retained(self):
        primary, close_error, unlink_error = OSError("fdopen"), OSError("close"), OSError("unlink")
        with patch.object(config_store.os, "fdopen", side_effect=primary), \
             patch.object(config_store.os, "close", side_effect=close_error), \
             patch.object(Path, "unlink", side_effect=unlink_error):
            with self.assertRaises(FileWriteError) as caught:
                self.store.save("profile", {})
        self.assertIs(caught.exception.cleanup_error, unlink_error)
        self.assertIs(caught.exception.cause.cause, primary)
        self.assertIs(caught.exception.cause.cleanup_error, close_error)
        self.assertTrue(Path(self.opened[0][1]).exists())

    def test_failed_first_stream_admission_does_not_break_bundle_rollback(self):
        startup = self.store.config_dir / ".startup.json"
        startup.write_bytes(b'{ "profile": "old", "load_on_start": false }\n')
        original_startup = startup.read_bytes()
        real_fdopen = os.fdopen
        primary = OSError("first admission rejected")
        calls = 0

        def reject_first(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise primary
            return real_fdopen(*args, **kwargs)

        with patch.object(config_store.os, "fdopen", side_effect=reject_first):
            with self.assertRaises(config_store.ProfileSaveError) as caught:
                self.store.save_with_startup("profile", {}, load_on_start=True)
        self.assertIs(caught.exception.cause, primary)
        self.assertTrue(caught.exception.restored)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(startup.read_bytes(), original_startup)
        self.assert_closed()


if __name__ == "__main__":
    unittest.main()
