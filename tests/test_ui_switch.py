"""界面偏好、两种确认壳、取消及异步关机后的重启门禁。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget

from rm_client.ui import ui_switch, ui_theme
from rm_client.ui.jello_widgets import JelloDialog

APP = QApplication.instance() or QApplication([])


class UISwitchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "ui_theme.json"
        self.path_patch = patch.object(ui_theme, "preference_path", return_value=self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.host = QWidget()
        self.host.backend = Mock()
        self.host._neutralize_modal_inputs = Mock()
        self.host.frame_scheduler = Mock()
        self.host._window_lifecycle = NS(closing=False, ready_to_close=False, finalized=False)
        self.host.paths = NS(app_root=Path(__file__).resolve().parents[1])
        self.host.close = Mock()
        self.addCleanup(self.host.deleteLater)
        APP._polar_ui_restart = None

    def tearDown(self):
        APP._polar_ui_restart = None
        APP.processEvents()

    def test_missing_invalid_and_valid_preferences(self):
        self.assertEqual(ui_theme.read_theme(), "endfield")
        for value in ("broken", "[]", '{"theme":"unknown"}'):
            self.path.write_text(value, encoding="utf-8")
            self.assertEqual(ui_theme.read_theme(), "endfield")
        ui_theme.save_theme("jello")
        self.assertEqual(ui_theme.read_theme(), "jello")
        self.assertFalse(self.path.with_suffix(".tmp").exists())
        with self.assertRaises(ValueError):
            ui_theme.save_theme("unknown")
        self.assertEqual(ui_theme.read_theme(), "jello")

    def test_both_confirmation_shells_cancel_with_escape_and_buttons(self):
        for jello in (False, True):
            with self.subTest(jello=jello), patch.object(ui_switch, "is_jello", return_value=jello):
                dialog = ui_switch.build_switch_dialog(self.host)
                self.assertEqual(isinstance(dialog, JelloDialog), jello)
                self.assertEqual(dialog.target_theme, "endfield" if jello else "jello")
                QTimer.singleShot(50, lambda: QTest.keyClick(dialog, Qt.Key_Escape))
                self.assertEqual(dialog.exec(), QDialog.Rejected)
                dialog.deleteLater()
                dialog = ui_switch.build_switch_dialog(self.host)
                button = dialog.findChild(QPushButton, "UISwitchConfirm")
                QTimer.singleShot(50, button.click)
                self.assertEqual(dialog.exec(), QDialog.Accepted)
                dialog.deleteLater()

    def test_cancel_never_saves_or_restarts(self):
        dialog = Mock(target_theme="jello")
        dialog.exec.return_value = QDialog.Rejected
        with patch.object(ui_switch, "build_switch_dialog", return_value=dialog), \
                patch.object(ui_switch, "request_ui_restart") as restart:
            ui_switch.open_ui_switch(self.host)
        restart.assert_not_called()
        self.assertFalse(self.path.exists())
        self.host.close.assert_not_called()
        self.assertEqual(self.host._neutralize_modal_inputs.call_count, 2)

    def test_confirmation_requests_normal_close_and_defers_launch(self):
        self.host.close.side_effect = lambda: setattr(self.host._window_lifecycle, "closing", True)
        with patch.object(ui_switch.QProcess, "startDetached", return_value=(True, 42)) as launch:
            ui_switch.request_ui_restart(self.host, "jello")
            self.assertEqual(ui_theme.read_theme(), "jello")
            self.host.close.assert_called_once()
            launch.assert_not_called()
            self.host._window_lifecycle.ready_to_close = True
            self.host._window_lifecycle.finalized = True
            self.assertTrue(ui_switch.finish_ui_restart(APP))
            executable = Path(ui_switch.sys.executable)
            windowed = executable.with_name("pythonw.exe")
            if ui_switch.sys.platform == "win32" and windowed.is_file():
                executable = windowed
            launch.assert_called_once_with(
                str(executable), [str(self.host.paths.app_root / "main.py")],
                str(self.host.paths.app_root))
            self.assertFalse(ui_switch.finish_ui_restart(APP))

    def test_close_refusal_restores_preference_and_cancels_restart(self):
        ui_theme.save_theme("endfield")
        ui_switch.request_ui_restart(self.host, "jello")
        self.assertEqual(ui_theme.read_theme(), "endfield")
        self.assertFalse(ui_switch.finish_ui_restart(APP))

    def test_save_failure_keeps_terminal_open(self):
        with patch.object(ui_switch, "save_theme", side_effect=OSError("disk full")), \
                patch.object(ui_switch.QMessageBox, "warning") as warning:
            ui_switch.request_ui_restart(self.host, "jello")
        warning.assert_called_once()
        self.host.close.assert_not_called()
        self.assertIsNone(APP._polar_ui_restart)

    def test_failed_launch_is_visible_and_not_retried(self):
        self.host._window_lifecycle.ready_to_close = True
        self.host._window_lifecycle.finalized = True
        APP._polar_ui_restart = (self.host.paths, self.host._window_lifecycle)
        with patch.object(ui_switch.QProcess, "startDetached", return_value=(False, 0)), \
                patch.object(ui_switch.QMessageBox, "critical") as error:
            self.assertFalse(ui_switch.finish_ui_restart(APP))
        error.assert_called_once()
        self.assertIsNone(APP._polar_ui_restart)

    def test_event_loop_exit_before_resource_retirement_cannot_restart(self):
        self.host.close.side_effect = lambda: setattr(self.host._window_lifecycle, "closing", True)
        with patch.object(ui_switch.QProcess, "startDetached") as launch:
            ui_switch.request_ui_restart(self.host, "jello")
            self.assertFalse(ui_switch.finish_ui_restart(APP))
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
