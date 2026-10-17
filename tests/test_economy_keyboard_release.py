"""Keyboard release ownership using real Qt events and economy hold completion."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QKeyEvent
from economy_interaction_fixture import OFFER
from keyboard_routing_harness import BASELINE, Case, Harness
from rm_client.ui.polar.mixins.economy_mixin import EconomyMixin
from rm_client.ui.polar.mixins.input_dispatch_mixin import InputDispatchMixin


def release(code=Qt.Key.Key_Return, *, repeat=False):
    return QKeyEvent(QEvent.Type.KeyRelease, code, Qt.KeyboardModifier.NoModifier,
                     '', repeat, 1)


def held_offer():
    host = Harness(Case(economy=True, confirmation='hold'))
    host.key_name = lambda event: Qt.Key(event.key()).name.removeprefix('Key_')
    offer = replace(OFFER, confirmation='hold')
    host.economy_hold_key = offer.key
    host.economy_hold_started = 100.0
    host.economy_hold_rect = QRectF()
    host.economy_offers = lambda *args: [offer]
    host.begin_economy_hold = lambda offer: EconomyMixin.begin_economy_hold(host, offer)
    host.finish_economy_hold = lambda: EconomyMixin.finish_economy_hold(host)
    host.submit_economy_offer = host.effect('submit')
    return host


class FrozenRepeatReleaseEvidence(unittest.TestCase):
    def test_baseline_repeat_release_cancels_short_hold_or_submits_long_hold(self):
        for elapsed in (.3, .7):
            with self.subTest(elapsed=elapsed):
                host = held_offer()
                with patch('rm_client.ui.polar.mixins.economy_mixin.time.perf_counter',
                           return_value=100.0 + elapsed):
                    BASELINE.keyReleaseEvent(host, release(repeat=True))
                self.assertEqual(host.economy_hold_key, '')
                names = [entry[0] for entry in host.trace]
                self.assertIn('submit' if elapsed >= .6 else 'toast', names)


class EconomyPhysicalReleaseTests(unittest.TestCase):
    def test_physical_press_repeat_and_release_use_real_hold_lifecycle(self):
        host = held_offer()
        host.economy_hold_key = ''
        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                          Qt.KeyboardModifier.NoModifier)
        with patch('rm_client.ui.polar.mixins.economy_mixin.time.perf_counter', return_value=100.0):
            InputDispatchMixin.keyPressEvent(host, press)
        self.assertEqual(host.economy_hold_key, OFFER.key)
        self.assertEqual(host.economy_hold_started, 100.0)
        InputDispatchMixin.keyReleaseEvent(host, release(repeat=True))
        self.assertEqual(host.economy_hold_key, OFFER.key)
        with patch('rm_client.ui.polar.mixins.economy_mixin.time.perf_counter', return_value=100.8):
            InputDispatchMixin.keyReleaseEvent(host, release())
        self.assertEqual(sum(row[0] == 'submit' for row in host.trace), 1)

    def test_repeat_release_retains_hold_before_and_after_threshold(self):
        for code in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            for elapsed in (.3, .7, 10):
                with self.subTest(code=code, elapsed=elapsed):
                    host = held_offer()
                    with patch('rm_client.ui.polar.mixins.economy_mixin.time.perf_counter',
                               side_effect=AssertionError('repeat consulted hold clock')):
                        InputDispatchMixin.keyReleaseEvent(host, release(code, repeat=True))
                    self.assertEqual(host.economy_hold_key, OFFER.key)
                    self.assertFalse(any(row[0] in ('submit', 'toast', 'frame.force') for row in host.trace))

    def test_repeat_sequence_then_physical_release_submits_once(self):
        for code in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            host = held_offer()
            with patch('rm_client.ui.polar.mixins.economy_mixin.time.perf_counter', return_value=100.7):
                for _ in range(4):
                    InputDispatchMixin.keyReleaseEvent(host, release(code, repeat=True))
                self.assertEqual(host.economy_hold_key, OFFER.key)
                InputDispatchMixin.keyReleaseEvent(host, release(code))
                InputDispatchMixin.keyReleaseEvent(host, release(code))
            self.assertEqual(host.economy_hold_key, '')
            self.assertEqual(sum(row[0] == 'submit' for row in host.trace), 1)

    def test_short_physical_release_still_cancels_with_feedback(self):
        host = held_offer()
        with patch('rm_client.ui.polar.mixins.economy_mixin.time.perf_counter', return_value=100.3):
            InputDispatchMixin.keyReleaseEvent(host, release())
        self.assertEqual(host.economy_hold_key, '')
        self.assertTrue(any(row[0] == 'toast' for row in host.trace))
        self.assertFalse(any(row[0] == 'submit' for row in host.trace))

    def test_higher_priority_owners_keep_release_before_economy(self):
        for owner in ('closing', 'escape', 'student', 'search', 'chat', 'wheel'):
            for repeat in (False, True):
                with self.subTest(owner=owner, repeat=repeat):
                    host = held_offer()
                    if owner == 'closing': host._window_lifecycle.closing = True
                    elif owner == 'escape': host.escape_menu_visible = True
                    elif owner == 'student': host.student_ui.visible = True
                    elif owner == 'search': host.search_focus = True
                    elif owner == 'chat': host.match_chat.input_open = True
                    else: host.quick_action_wheel.active = True
                    with patch('rm_client.ui.polar.mixins.economy_mixin.time.perf_counter',
                               side_effect=AssertionError('modal leaked release')):
                        InputDispatchMixin.keyReleaseEvent(host, release(repeat=repeat))
                    self.assertEqual(host.economy_hold_key, OFFER.key)


if __name__ == '__main__':
    unittest.main()
