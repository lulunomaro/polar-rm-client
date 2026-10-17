"""Temporary descriptor ownership before fdopen accepts it; real local files."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.configuration import files


class TemporaryDescriptorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "preferences.json"
        self.opened = []
        original = files.tempfile.mkstemp
        def track(*args, **kwargs):
            result = original(*args, **kwargs)
            self.opened.append(result)
            return result
        patcher = patch.object(files.tempfile, "mkstemp", track)
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
        self.assertEqual(len(self.opened), 1)
        with self.assertRaises(OSError):
            os.fstat(self.opened[0][0])

    def test_fdopen_rejection_closes_unadopted_descriptor_and_preserves_cause(self):
        error = OSError("fdopen rejected ownership")
        with patch.object(files.os, "fdopen", side_effect=error), self.assertRaises(OSError) as caught:
            files.atomic_write_bytes(self.path, b"new")
        self.assertIs(caught.exception, error)
        self.assert_closed()
        self.assertFalse(Path(self.opened[0][1]).exists())
        self.assertFalse(self.path.exists())

    def test_adopted_stream_is_not_closed_twice_by_descriptor_cleanup(self):
        original_close = os.close
        with patch.object(files.os, "close", wraps=original_close) as close:
            files.atomic_write_bytes(self.path, b"new")
        close.assert_not_called()
        self.assert_closed()
        self.assertEqual(self.path.read_bytes(), b"new")

    def test_failed_descriptor_close_retains_both_errors_and_still_unlinks(self):
        primary, close_error = OSError("fdopen"), OSError("descriptor close")
        with patch.object(files.os, "fdopen", side_effect=primary), \
                patch.object(files.os, "close", side_effect=close_error), self.assertRaises(OSError) as caught:
            files.atomic_write_bytes(self.path, b"new")
        self.assertIs(getattr(caught.exception, "cause", None), primary)
        self.assertIs(getattr(caught.exception, "cleanup_error", None), close_error)
        self.assertFalse(Path(self.opened[0][1]).exists())
        # This injected close rejected ownership release; the fixture owns cleanup.
        os.fstat(self.opened[0][0])

    def test_fdopen_close_and_unlink_failures_remain_independent(self):
        primary, close_error, unlink_error = OSError("fdopen"), OSError("descriptor close"), PermissionError("unlink")
        with patch.object(files.os, "fdopen", side_effect=primary), \
                patch.object(files.os, "close", side_effect=close_error), \
                patch.object(Path, "unlink", side_effect=unlink_error), self.assertRaises(OSError) as caught:
            files.atomic_write_bytes(self.path, b"new")
        self.assertIs(caught.exception.cleanup_error, unlink_error)
        nested = caught.exception.cause
        self.assertIs(getattr(nested, "cause", None), primary)
        self.assertIs(getattr(nested, "cleanup_error", None), close_error)
        self.assertTrue(Path(self.opened[0][1]).exists())


if __name__ == "__main__":
    unittest.main()
