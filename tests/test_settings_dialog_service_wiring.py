"""Actual preference dialogs use the shared transaction and F12 services.

Real Qt widgets, registry, input bridge and window restoration are exercised with
synthetic failures. Offscreen tests do not qualify native rendering or hardware.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog
from shiboken6 import isValid

from rm_client.ui import cs2_settings_center as settings
from rm_client.ui import jello_keybinds as keyboard
import test_configuration_transactions as transaction_tests
from test_configuration_transactions import APP, settle_configuration
from test_cs2_settings_center import ready_page


class DialogServiceWiringTests(unittest.TestCase):
    close_window = transaction_tests.WindowConfigurationTransactionTests.close_window

    def setUp(self):
        transaction_tests.WindowConfigurationTransactionTests.setUp(self)
        self.dialogs = []
        self.addCleanup(self.close_dialogs)

    def own(self, dialog):
        self.dialogs.append(dialog)
        return dialog

    def close_dialogs(self):
        for dialog in reversed(self.dialogs):
            if isValid(dialog):
                dialog.hide()
                dialog.reject()
                dialog.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_settings_apply_button_rolls_back_and_preserves_draft_for_retry(self):
        window = self.window
        dialog = self.own(settings.build_settings_center(window))
        page = ready_page(dialog, "键盘 / 鼠标")
        before = deepcopy(window.current_config_data())
        baseline = dict(page._baseline)
        control = page.controls[("RobotLink", "Mouse Sensitivity")]
        control.spin.setValue(1.75)
        window.control.key_state["W"] = True
        with patch.object(window, "after_setting_changed", side_effect=OSError("dialog apply failure")), \
             patch.object(window.backend, "arm_match_control") as arm:
            dialog.apply_button.click()
            self.assertIn("未完整应用", dialog.status.text())
            self.assertIn("dialog apply failure", dialog.status.text())
            settle_configuration(window)
        arm.assert_not_called()
        self.assertEqual(window.current_config_data(), before)
        self.assertEqual(page._baseline, baseline)
        self.assertEqual(control.spin.value(), 1.75)
        self.assertFalse(window._auto_save_pending)
        self.assertFalse(window.config_loading)
        self.assertFalse(any(window.control.key_state.values()))
        self.assertTrue(window.backend.safety.estop_active)
        dialog.apply_button.click()
        self.assertIn("当前页已应用", dialog.status.text())
        self.assertEqual(window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value, 1.75)
        self.assertEqual(page._baseline, page._draft_values())
        self.assertTrue(window._auto_save_pending)
        self.assertTrue(window.backend.safety.estop_active)

    def test_settings_binding_button_accepts_only_the_owned_draft_until_page_apply(self):
        window = self.window
        dialog = self.own(settings.build_settings_center(window))
        page = ready_page(dialog, "键盘 / 鼠标")
        before = deepcopy(window.current_config_data())
        original_build = keyboard.build_keyboard_manager

        def stage_and_accept(host, manager, target, parent):
            self.assertIs(manager, page)
            self.assertIs(parent, dialog)
            editor = self.own(original_build(host, manager, target, parent))
            entry = next(item for item in editor.session.entries if item.identity == target)
            editor.session.assign(entry, "F10")
            QTimer.singleShot(0, editor.accept)
            return editor

        with patch.object(keyboard, "build_keyboard_manager", side_effect=stage_and_accept), \
             patch.object(window.backend, "arm_match_control") as arm:
            page.module_bind_controls["TacticalMap"].button.click()
        arm.assert_not_called()
        self.assertEqual(page.module_bind_controls["TacticalMap"].value, "F10")
        self.assertEqual(window.current_config_data(), before)
        self.assertFalse(window._auto_save_pending)
        dialog.apply_button.click()
        self.assertEqual(window.registry.get("TacticalMap").keybind, "F10")
        self.assertTrue(window._auto_save_pending)

    def test_standalone_keyboard_accept_failure_uses_real_rollback_and_error_prefix(self):
        window = self.window
        dialog = self.own(keyboard.build_keyboard_manager(window))
        before = deepcopy(window.current_config_data())
        binds = dict(window.control.binds)
        entries = {item.identity: item for item in dialog.session.entries}
        dialog.session.clear(entries[("RobotLink", "Bind S")])
        dialog.session.assign(entries[("RobotLink", "Bind W")], "S")
        dialog.session.assign(entries[("RobotLink", "Bind S")], "W")
        QTimer.singleShot(0, dialog.accept)
        with patch.object(keyboard, "build_keyboard_manager", return_value=dialog), \
             patch.object(window, "after_setting_changed", side_effect=OSError("keyboard apply failure")), \
             patch.object(window, "toast") as toast, \
             patch.object(window.backend, "arm_match_control") as arm:
            keyboard.open_keyboard_manager(window)
            self.assertTrue(toast.call_args.args[0].startswith("绑定未应用："))
            self.assertIn("keyboard apply failure", toast.call_args.args[0])
            self.assertEqual(toast.call_args.kwargs, {"kind": "warning"})
            settle_configuration(window)
        arm.assert_not_called()
        self.assertEqual(window.current_config_data(), before)
        self.assertEqual(window.control.binds, binds)
        self.assertFalse(any(window.control.key_state.values()))
        self.assertIsNone(window._control_mouse_point)
        self.assertFalse(window._auto_save_pending)
        self.assertFalse(window.config_loading)
        self.assertTrue(window.backend.safety.estop_active)

    def test_nested_keyboard_and_parent_settings_have_one_f12_route_each(self):
        window = self.window
        dialog = self.own(settings.build_settings_center(window))
        page = ready_page(dialog, "键盘 / 鼠标")
        editor = self.own(keyboard.build_keyboard_manager(window, page, parent=dialog))
        editor.show()
        with patch.object(window.backend, "trigger_estop", wraps=window.backend.trigger_estop) as stop, \
             patch.object(window.backend, "arm_match_control") as arm:
            QTest.keyClick(editor.canvas, Qt.Key.Key_F12)
            stop.assert_called_once_with()
            editor.reject()
            editor.timeline.update(1)
            editor._advance()
            self.assertFalse(editor.isVisible())
            self.assertTrue(dialog.isVisible())
            QTest.keyClick(dialog.search, Qt.Key.Key_F12)
            self.assertEqual(stop.call_count, 2)
        arm.assert_not_called()
        self.assertTrue(window.backend.safety.estop_active)

    def assert_nested_f12_failure_wiring(self, builder):
        window = self.window
        self.assertIsNotNone(window.backend.begin_qualification({"kmc_heartbeat"}))
        dialog = self.own(builder(window))
        dialog.show()
        child = QInputDialog(dialog)
        self.addCleanup(child.deleteLater)
        keypress = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F12, Qt.KeyboardModifier.NoModifier, "", True)
        failures = []

        def press_child_keys():
            # Catch callback assertions so unittest sees failures, rather than Qt
            # swallowing them while the nested event loop keeps running.
            try:
                QTest.keyClick(child, Qt.Key.Key_F12)
                APP.sendEvent(child, keypress)
            except Exception as error:
                failures.append(error)
            finally:
                child.reject()

        with patch.object(window.control, "reset_inputs", side_effect=TypeError("F12 input reset failure")), \
             patch.object(window.control, "clear_pending", wraps=window.control.clear_pending) as clear, \
             patch.object(window.backend, "trigger_estop", wraps=window.backend.trigger_estop) as stop, \
             patch.object(window, "toast") as toast, \
             patch.object(window.backend, "arm_match_control") as arm:
            QTimer.singleShot(0, press_child_keys)
            child.exec()
        self.assertEqual(failures, [])
        stop.assert_called_once_with()
        # The failed lock observer now clears pending independently; the
        # F12 adapter retains its own bounded fallback cleanup as well.
        self.assertEqual(clear.call_args_list, [unittest.mock.call(), unittest.mock.call()])
        arm.assert_not_called()
        self.assertTrue(window.backend.safety.estop_active)
        self.assertNotEqual(window.backend.safety.control_state.value, "QUALIFICATION")
        self.assertIn("F12 input reset failure", toast.call_args.args[0])
        self.assertEqual(toast.call_args.kwargs, {"kind": "warning"})
        # A backend failure still revokes authority independently and reports all
        # fallible cleanup operations through this dialog's installed adapter.
        with patch.object(window.backend, "trigger_estop", side_effect=OSError("F12 stop failure")), \
             patch.object(window.backend, "lock_control", wraps=window.backend.lock_control) as lock, \
             patch.object(window.control, "reset_inputs", side_effect=TypeError("F12 reset failure")), \
             patch.object(window.control, "clear_pending", side_effect=RuntimeError("F12 clear failure")), \
             patch.object(window, "toast") as toast:
            QTest.keyClick(dialog, Qt.Key.Key_F12)
        lock.assert_called_once_with("E-stop request failed")
        message = toast.call_args.args[0]
        for detail in ("F12 stop failure", "F12 reset failure", "F12 clear failure"):
            self.assertIn(detail, message)
        self.assertIn("E-STOP REQUEST INCOMPLETE", message)
        self.assertNotIn("E-STOP ACTIVE", message)
        self.assertEqual(toast.call_args.kwargs, {"kind": "warning"})
        with patch.object(APP, "removeEventFilter", wraps=APP.removeEventFilter) as remove:
            dialog.reject()
            dialog.timeline.update(1)
            dialog._advance()
        self.assertFalse(dialog.isVisible())
        remove.assert_called_once_with(dialog.estop_shortcut)

    def test_settings_dialog_nested_f12_revokes_before_input_failure(self):
        self.assert_nested_f12_failure_wiring(settings.build_settings_center)

    def test_keyboard_dialog_nested_f12_revokes_before_input_failure(self):
        self.assert_nested_f12_failure_wiring(keyboard.build_keyboard_manager)


if __name__ == "__main__":
    unittest.main()
