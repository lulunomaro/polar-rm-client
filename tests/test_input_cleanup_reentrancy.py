"""Nested lock callbacks cannot surrender the outer input cleanup epoch."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import test_modal_input_epoch as fixtures
from PySide6.QtCore import Qt


class InputCleanupReentrancyTests(unittest.TestCase):
    def test_nested_success_cannot_admit_keys_before_outer_release_finishes(self):
        with fixtures.window_fixture() as (case, window, sink):
            nested = False
            def reenter():
                nonlocal nested
                if not nested:
                    nested = True
                    window._clear_control_inputs_for_lock('nested callback')
                    case.key(Qt.Key.Key_W)
            with patch.object(window.control, 'release_direct_dt7', side_effect=reenter):
                window._clear_control_inputs_for_lock('outer lock')
            self.assertFalse(window.control.key_state['W'])
            self.assertFalse(window._modal_input_clear_pending)
            window.control.tick(window.state, window.registry, window.control.last_send_at + 1)
            self.assertFalse(any(frame[1]['keyboard_value'] for frame in sink.frames))

    def test_failed_outer_epoch_remains_blocked_and_can_be_retried(self):
        with fixtures.window_fixture() as (case, window, sink):
            nested = False
            def reenter_then_fail():
                nonlocal nested
                if not nested:
                    nested = True
                    window._clear_control_inputs_for_lock('nested callback')
                    case.key(Qt.Key.Key_W)
                    raise RuntimeError('outer release failed')
            with patch.object(window.control, 'release_direct_dt7', side_effect=reenter_then_fail):
                with self.assertRaisesRegex(RuntimeError, 'outer release failed'):
                    window._clear_control_inputs_for_lock('outer lock')
            self.assertFalse(window.control.key_state['W'])
            self.assertTrue(window._modal_input_clear_pending)
            window._clear_control_inputs_for_lock('operator retry')
            self.assertFalse(window._modal_input_clear_pending)


if __name__ == '__main__':
    unittest.main(verbosity=2)
