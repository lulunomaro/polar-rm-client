"""Actual-window hold retirement with inert backends and mock transactions."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import test_window_economy_commands as fixture
OFFER = fixture.OFFER
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from rm_client.ui.overlay_coordinator import OverlayKind


class EconomyHoldCancellation(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.WindowEconomyCommands()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = self.fixture.window
        self.fixture.offers[0] = replace(OFFER, confirmation="hold")

    def press(self):
        with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=100.):
            self.fixture.key(Qt.Key.Key_Return)
        self.assertEqual(self.w.economy_hold_key, OFFER.key)

    def release(self):
        with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=100.8):
            self.fixture.key(Qt.Key.Key_Return, release=True)

    def assert_retired(self):
        self.assertEqual(self.w.economy_hold_key, "")
        self.assertIsNone(self.w._economy_hold_input_owner)
        self.assertTrue(self.w.economy_hold_rect.isNull())

    def test_escape_cancel_prevents_later_mock_transaction(self):
        self.press()
        self.w.set_escape_menu_visible(True)
        self.release()
        self.w.set_escape_menu_visible(False)
        self.release()
        self.assertEqual(len(self.w.economy_bridge.history), 0)
        self.assert_retired()
        # A fresh admitted gesture still goes through the real mock bridge.
        self.press()
        self.release()
        self.assertEqual(len(self.w.economy_bridge.history), 1)
        self.assertTrue(self.w.economy_bridge.latest.request.mock)
        self.assertEqual(self.w.state.remaining_economy, 500)

    def test_modal_claim_focus_and_lock_retire_before_cleanup_callbacks(self):
        for boundary in ("modal", "focus", "lock"):
            with self.subTest(boundary=boundary):
                self.press()
                observed = []
                original = self.w.control.reset_inputs
                def observe():
                    observed.append(True)
                    self.assert_retired()
                    original()
                with patch.object(self.w.control, "reset_inputs", side_effect=observe):
                    if boundary == "modal":
                        self.w.overlay_coordinator.request_modal(OverlayKind.STUDENT)
                        self.w.overlay_coordinator.close_modal()
                    elif boundary == "focus":
                        self.w.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
                    else:
                        self.w.backend.lock_control("test hold cancellation")
                self.assertTrue(observed)
                self.assert_retired()
                self.release()
                self.assertEqual(len(self.w.economy_bridge.history), 0)


if __name__ == "__main__":
    unittest.main()
