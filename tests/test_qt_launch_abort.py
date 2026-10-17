"""Failure-only launch policy with actual Qt ownership and explicit abort ports."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QWidget
from rm_client.ui.polar import qt_application

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class QtLaunchAbortTests(unittest.TestCase):
    def setUp(self):
        self.windows = []
        self.events = []
        self.primary = TypeError("original launch failure")
        self.cleanup = OSError("abort failure")
        self.addCleanup(self.release_windows)

    def release_windows(self):
        for window in self.windows:
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        # A deliberately failed destroyed-signal hookup cannot remove ownership.
        # Retire that test's already-destroyed wrapper explicitly, never a live one.
        for window in self.windows:
            APP._polar_client_windows.pop(id(window), None)

    def window_type(self, *, failure_stage="show", abort_fails=False,
                    retention_fails=False, signal_fails=False):
        case = self
        class Window(QWidget):
            def __init__(self):
                super().__init__()
                case.windows.append(self)
            def show(self):
                case.events.append("show")
                if failure_stage == "show":
                    raise case.primary
            def setAttribute(self, attribute, enabled=True):
                if attribute == Qt.WidgetAttribute.WA_DeleteOnClose:
                    case.events.append("delete-on-close")
                    if retention_fails:
                        raise case.primary if failure_stage == "retention" else case.cleanup
                super().setAttribute(attribute, enabled)
            def __getattribute__(self, name):
                if name == "destroyed" and signal_fails:
                    raise case.cleanup
                return super().__getattribute__(name)
            def _abort_startup(self):
                case.events.append("abort")
                case.assertIs(APP._polar_client_windows[id(self)], self)
                if abort_fails:
                    raise case.cleanup
                self.close()
        return Window

    def launch(self, window_type, *, restore_fails=False):
        def restore(window):
            self.events.append("restore")
            if restore_fails:
                raise self.primary
        with patch("rm_client.ui.cs2_video_settings.restore_startup_window", restore):
            with self.assertRaises(type(self.primary)) as caught:
                qt_application.launch_qt_application(window_type)
        self.assertIs(caught.exception, self.primary)

    def test_show_failure_retains_before_aborting_and_releases_on_destroy(self):
        self.launch(self.window_type())
        self.assertEqual(self.events, ["show", "delete-on-close", "abort"])
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertNotIn(id(self.windows[0]), APP._polar_client_windows)
        self.windows.clear()

    def test_placement_failure_uses_same_explicit_abort_port(self):
        self.launch(self.window_type(failure_stage="restore"), restore_fails=True)
        self.assertEqual(self.events, ["show", "restore", "delete-on-close", "abort"])

    def test_retention_attribute_failure_still_requests_abort_independently(self):
        with self.assertLogs(qt_application.__name__, level="ERROR"):
            self.launch(self.window_type(failure_stage="retention", retention_fails=True))
        self.assertEqual(self.events,
                         ["show", "restore", "delete-on-close", "delete-on-close", "abort"])
        self.assertIn("retention", self.primary.__notes__[0])
        self.assertIn(id(self.windows[0]), APP._polar_client_windows)

    def test_retention_signal_failure_still_requests_abort_independently(self):
        with self.assertLogs(qt_application.__name__, level="ERROR"):
            self.launch(self.window_type(failure_stage="restore", signal_fails=True),
                        restore_fails=True)
        self.assertEqual(self.events, ["show", "restore", "delete-on-close", "abort"])
        self.assertIn("retention", self.primary.__notes__[0])

    def test_abort_failure_keeps_original_exception_and_retained_owner(self):
        with self.assertLogs(qt_application.__name__, level="ERROR"):
            self.launch(self.window_type(abort_fails=True))
        self.assertIn("abort", self.primary.__notes__[0])
        self.assertIn("OSError: abort failure", self.primary.__notes__[0])
        self.assertIn(id(self.windows[0]), APP._polar_client_windows)

    def test_diagnostic_sink_failures_do_not_replace_original_exception(self):
        class BrokenNotes(TypeError):
            def add_note(self, note):
                raise RuntimeError("notes unavailable")
        self.primary = BrokenNotes("original failure with unavailable notes")
        with patch.object(qt_application._LOG, "error", side_effect=RuntimeError("logging unavailable")):
            self.launch(self.window_type(abort_fails=True, retention_fails=True))
        self.assertEqual(self.events, ["show", "delete-on-close", "abort"])
        self.assertIn(id(self.windows[0]), APP._polar_client_windows)

    def test_process_cancellation_also_seals_the_constructed_window(self):
        self.primary = KeyboardInterrupt("launch interrupted")
        self.launch(self.window_type())
        self.assertEqual(self.events, ["show", "delete-on-close", "abort"])


if __name__ == "__main__":
    unittest.main()
