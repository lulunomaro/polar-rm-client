"""Real offscreen window revokes qualification despite focus cleanup faults."""
from pathlib import Path
import sys
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_configuration_transactions as configuration_tests
from PySide6.QtCore import QEvent
from PySide6.QtGui import QFocusEvent


class WindowFocusLossTests(unittest.TestCase):
    setUp = configuration_tests.WindowConfigurationTransactionTests.setUp
    close_window = configuration_tests.WindowConfigurationTransactionTests.close_window

    def test_failed_reset_revokes_real_qualification_and_attempts_both_neutral_links(self):
        window = self.window
        window.backend.begin_qualification({"kmc_heartbeat"})
        window.control.key_state["W"] = True
        with (
            patch.object(window.control, "reset_inputs", side_effect=TypeError("reset fault")),
            patch.object(window.control, "send_from_state") as ordinary,
            patch.object(window.control, "release_direct_dt7") as dt7,
            patch.object(window.control, "release_custom_controller") as cc,
            patch.object(window.control, "clear_pending", wraps=window.control.clear_pending) as clear,
            patch.object(window.backend.mqtt, "publish_estop_zero", return_value=(True, "")) as zero,
            patch.object(window.backend, "arm_match_control") as arm,
            patch.object(window, "toast") as toast,
        ):
            window.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        self.assertEqual(window.backend.safety.control_state.value, "LOCKED")
        self.assertEqual(window.backend.safety.last_reason, "input clear failed; explicit re-arm required")
        self.assertIsNone(window.backend.safety._qualification)
        self.assertFalse(window.control._dirty)
        ordinary.assert_not_called()
        # The lock observer, release_all, and focus-loss fallback independently
        # attempt neutral even when reset fails. Calls are idempotent, bounded.
        self.assertEqual(dt7.call_args_list, [unittest.mock.call()] * 3)
        self.assertEqual(cc.call_args_list, [unittest.mock.call()] * 3)
        zero.assert_called_once_with()
        self.assertEqual(clear.call_args_list, [unittest.mock.call(), unittest.mock.call()])
        arm.assert_not_called()
        self.assertIn("reset fault", toast.call_args.args[0])

    def test_failed_ordinary_neutral_preserves_lock_and_pending_clear(self):
        window = self.window
        window.backend.begin_qualification({"kmc_heartbeat"})
        window.control.key_state["W"] = True
        with (
            patch.object(window.control, "send_from_state", side_effect=RuntimeError("publish fault")) as ordinary,
            patch.object(window.control, "release_direct_dt7") as dt7,
            patch.object(window.control, "release_custom_controller") as cc,
            patch.object(window.backend.mqtt, "publish_estop_zero", return_value=(False, "disconnected")),
            patch.object(window.backend, "arm_match_control") as arm,
            patch.object(window, "toast") as toast,
        ):
            window.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        self.assertEqual(window.backend.safety.control_state.value, "LOCKED")
        self.assertFalse(any(window.control.key_state.values()))
        self.assertFalse(window.control._dirty)
        self.assertIsNone(window.backend.safety._qualification)
        ordinary.assert_called_once_with(window.state, window.registry)
        self.assertGreaterEqual(dt7.call_count, 1)
        self.assertGreaterEqual(cc.call_count, 1)
        arm.assert_not_called()
        self.assertIn("publish fault", toast.call_args.args[0])
        self.assertIn("disconnected", toast.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
