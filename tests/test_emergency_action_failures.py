"""Failure-path regressions for operator stop commands; never live output."""
from pathlib import Path
import sys
from unittest.mock import Mock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_configuration_commands import CommandHost
import test_configuration_transactions as configuration_tests


class EmergencyCommandFailureTests(unittest.TestCase):
    def test_reset_failure_cannot_skip_estop_and_pending_clear(self):
        host = CommandHost()
        events = []
        host.backend.trigger_estop.side_effect = lambda: (events.append("estop") or (True, ""))
        host.control.reset_inputs.side_effect = TypeError("synthetic reset failure")
        host.control.clear_pending.side_effect = lambda: events.append("clear")
        host.handle_config_action("Trigger E-Stop")
        self.assertEqual(events, ["estop", "clear"])
        self.assertIn("synthetic reset failure", host.toast.call_args.args[0])
        self.assertEqual(host.toast.call_args.kwargs, {"kind": "warning"})

    def test_backend_failure_attempts_fallback_lock_before_fallible_input_cleanup(self):
        host = CommandHost()
        events = []
        host.backend.trigger_estop.side_effect = RuntimeError("synthetic stop failure")
        host.backend.lock_control.side_effect = lambda reason: events.append("lock")
        host.control.reset_inputs.side_effect = lambda: events.append("reset")
        host.control.clear_pending.side_effect = OSError("synthetic pending failure")
        host.handle_config_action("Trigger E-Stop")
        self.assertEqual(events, ["lock", "reset"])
        message = host.toast.call_args.args[0]
        self.assertIn("synthetic stop failure", message)
        self.assertIn("synthetic pending failure", message)
        self.assertNotIn("E-STOP ACTIVE", message)

    def test_fallback_lock_failure_still_runs_both_input_cleanup_actions(self):
        host = CommandHost()
        host.backend.trigger_estop.side_effect = RuntimeError("stop unavailable")
        host.backend.lock_control.side_effect = OSError("lock unavailable")
        host.control.reset_inputs.side_effect = TypeError("reset unavailable")
        host.handle_config_action("Trigger E-Stop")
        host.control.clear_pending.assert_called_once_with()
        for message in ("stop unavailable", "lock unavailable", "reset unavailable"):
            self.assertIn(message, host.toast.call_args.args[0])
        self.assertNotIn("E-STOP ACTIVE", host.toast.call_args.args[0])

    def test_control_lock_rejected_zero_publication_is_reported_without_skipping_cleanup(self):
        host = CommandHost()
        host.backend.mqtt.publish_estop_zero.return_value = (False, "transport not connected")
        host.handle_qualification_action("Lock Match Control")
        host.backend.lock_control.assert_called_once_with("operator lock")
        host.control.reset_inputs.assert_called_once_with()
        host.control.clear_pending.assert_called_once_with()
        self.assertIn("transport not connected", host.toast.call_args.args[0])
        self.assertEqual(host.toast.call_args.kwargs, {"kind": "warning"})

    def test_qualification_lock_revokes_before_reset_and_attempts_zero_independently(self):
        host = CommandHost()
        events = []
        host.backend.lock_control.side_effect = lambda reason: events.append(reason)
        host.backend.mqtt.publish_estop_zero.side_effect = lambda: (events.append("zero") or (True, ""))
        host.control.reset_inputs.side_effect = TypeError("synthetic reset failure")
        host.handle_qualification_action("Lock Match Control")
        self.assertEqual(events, ["operator lock", "zero"])
        host.control.clear_pending.assert_called_once_with()
        self.assertIn("synthetic reset failure", host.toast.call_args.args[0])
        host.backend.arm_match_control.assert_not_called()


class RealWindowEmergencyFailureTests(unittest.TestCase):
    setUp = configuration_tests.WindowConfigurationTransactionTests.setUp
    close_window = configuration_tests.WindowConfigurationTransactionTests.close_window

    def test_settings_dialog_f12_revokes_real_authority_even_if_reset_fails(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QDialog
        from rm_client.ui.cs2_settings_center import install_estop_shortcut

        window = self.window
        window.backend.begin_qualification({"kmc_heartbeat"})
        dialog = QDialog()
        self.addCleanup(dialog.deleteLater)
        install_estop_shortcut(dialog, window)
        dialog.show()
        try:
            with patch.object(window.control, "reset_inputs", side_effect=TypeError("shortcut reset failure")), \
                 patch.object(window, "toast") as toast:
                QTest.keyClick(dialog, Qt.Key.Key_F12)
            self.assertTrue(window.backend.safety.estop_active)
            self.assertNotEqual(window.backend.safety.control_state.value, "QUALIFICATION")
            self.assertIn("shortcut reset failure", toast.call_args.args[0])
        finally:
            dialog.accept()

    def test_real_qualification_authority_is_revoked_when_input_reset_raises(self):
        window = self.window
        token = window.backend.begin_qualification({"kmc_heartbeat"})
        self.assertIsNotNone(token)
        self.assertFalse(window.backend.safety.estop_active)
        with patch.object(window.control, "reset_inputs", side_effect=TypeError("synthetic reset failure")), \
             patch.object(window, "toast") as toast:
            window.handle_config_action("Trigger E-Stop")
        self.assertTrue(window.backend.safety.estop_active)
        self.assertNotEqual(window.backend.safety.control_state.value, "QUALIFICATION")
        self.assertIn("synthetic reset failure", toast.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
