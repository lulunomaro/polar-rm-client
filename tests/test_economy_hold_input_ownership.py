"""Real input adapters and hold lifecycle; submission is an inert recorder."""
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from economy_interaction_fixture import OFFER
from keyboard_routing_harness import Case, Harness
from rm_client.ui.polar.mixins.economy_mixin import EconomyMixin
from rm_client.ui.polar.mixins.input_dispatch_mixin import _on_action_economy_confirm


def host():
    h = Harness(Case(economy=True, confirmation="hold"))
    offer = replace(OFFER, confirmation="hold")
    h.pointer_interaction.background_release_owned = False
    h.pointer_interaction.background_press = None
    h.economy_hold_key = ""
    h.economy_hold_rect = QRectF()
    h.economy_island = NS(progress=1.)
    h.economy_selected_key = offer.key
    h.economy_offers = lambda *args: [offer]
    h.reconcile_economy_selection = lambda: offer
    h.key_name = lambda event: Qt.Key(event.key()).name.removeprefix("Key_")
    h.begin_economy_hold = lambda offer, rect=None: EconomyMixin.begin_economy_hold(h, offer, rect)
    h.finish_economy_hold = lambda point=None: EconomyMixin.finish_economy_hold(h, point)
    h.handle_escape_menu_mouse = lambda *args: False
    h.handle_student_mouse = lambda *args: False
    h.combat_control_input_allowed = lambda: False
    h.submitted = []
    h.submit_economy_offer = lambda offer: h.submitted.append(offer.key)
    return h


def press(h, owner):
    with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=100.):
        if owner[0] == "key":
            h.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, owner[1], Qt.KeyboardModifier.NoModifier))
        else:
            _on_action_economy_confirm(h, "economy_confirm_hold", OFFER.key,
                                      QPointF(10, 10), owner[1], QRectF(0, 0, 50, 50))


def release(h, owner):
    with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=100.8):
        if owner[0] == "key":
            h.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease, owner[1], Qt.KeyboardModifier.NoModifier))
        else:
            button = {1: Qt.MouseButton.LeftButton, 2: Qt.MouseButton.MiddleButton,
                      3: Qt.MouseButton.RightButton}[owner[1]]
            h.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(10, 10),
                                            QPointF(10, 10), button, Qt.MouseButton.NoButton,
                                            Qt.KeyboardModifier.NoModifier))


OWNERS = (("key", Qt.Key.Key_Return), ("key", Qt.Key.Key_Enter),
          ("mouse", 1), ("mouse", 2), ("mouse", 3))


class EconomyHoldInputOwnershipTests(unittest.TestCase):
    def test_only_initiating_input_can_complete_hold(self):
        for owner in OWNERS:
            for other in OWNERS:
                if other == owner:
                    continue
                with self.subTest(owner=owner, other=other):
                    h = host()
                    press(h, owner)
                    release(h, other)
                    self.assertEqual(h.submitted, [])
                    self.assertEqual(h.economy_hold_key, OFFER.key)
                    release(h, owner)
                    release(h, owner)
                    self.assertEqual(h.submitted, [OFFER.key])

    def test_new_press_replaces_old_input_ownership(self):
        for first, second in ((OWNERS[0], OWNERS[2]), (OWNERS[2], OWNERS[1]),
                              (OWNERS[2], OWNERS[4])):
            with self.subTest(first=first, second=second):
                h = host()
                press(h, first)
                press(h, second)
                release(h, first)
                self.assertEqual(h.submitted, [])
                release(h, second)
                self.assertEqual(h.submitted, [OFFER.key])

    def test_rejected_press_does_not_steal_existing_hold(self):
        h = host()
        press(h, OWNERS[0])
        unavailable = replace(OFFER, confirmation="hold", enabled=False, reason="unavailable")
        h.reconcile_economy_selection = lambda: unavailable
        press(h, OWNERS[1])
        release(h, OWNERS[1])
        self.assertEqual(h.submitted, [])
        release(h, OWNERS[0])
        self.assertEqual(h.submitted, [OFFER.key])
        self.assertIsNone(h._economy_hold_input_source)

    def test_admitted_owner_survives_frame_callback_failure(self):
        h = host()
        with patch.object(h.frame_scheduler, "force", side_effect=RuntimeError("frame")):
            with self.assertRaises(RuntimeError):
                press(h, OWNERS[0])
        self.assertIsNone(h._economy_hold_input_source)
        release(h, OWNERS[2])
        self.assertEqual(h.submitted, [])
        release(h, OWNERS[0])
        self.assertEqual(h.submitted, [OFFER.key])

    def test_consumed_hold_cannot_replay_after_submission_error(self):
        h = host()
        press(h, OWNERS[0])
        def fail(offer):
            h.submitted.append(offer.key)
            raise OSError("submission observer failed")
        h.submit_economy_offer = fail
        with self.assertRaises(OSError):
            release(h, OWNERS[0])
        for owner in OWNERS:
            release(h, owner)
        self.assertEqual(h.submitted, [OFFER.key])


if __name__ == "__main__":
    unittest.main()
