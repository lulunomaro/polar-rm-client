"""Fault isolation for input retirement; all outputs are inert test doubles."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, PropertyMock
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import test_modal_input_epoch as fixtures
from PySide6.QtCore import Qt

class InputCleanupFailureIsolationTests(unittest.TestCase):
    def test_failed_reset_does_not_skip_other_egresses_or_pending_clear(self):
        with fixtures.window_fixture() as (case, w, sink):
            error = RuntimeError('reset rejected')
            with patch.object(w.control, 'reset_inputs', side_effect=error), \
                 patch.object(w.control, 'release_direct_dt7') as dt7, \
                 patch.object(w.control, 'release_custom_controller') as custom, \
                 patch.object(w.control, 'clear_pending') as pending:
                with self.assertRaises(RuntimeError) as raised:
                    w._clear_control_inputs_for_lock('test')
                self.assertIs(raised.exception, error)
                dt7.assert_called_once()
                custom.assert_called_once()
                pending.assert_called_once()
                self.assertTrue(w._modal_input_clear_pending)

    def test_failed_dt7_does_not_skip_custom_and_requires_successful_reset(self):
        with fixtures.window_fixture() as (case, w, sink):
            case.key(Qt.Key.Key_W)
            error = RuntimeError('DT7 rejected')
            with patch.object(w.control, 'release_direct_dt7', side_effect=error), \
                 patch.object(w.control, 'release_custom_controller') as custom:
                with self.assertRaises(RuntimeError) as raised:
                    w._clear_control_inputs_for_lock('test')
                self.assertIs(raised.exception, error)
                custom.assert_called_once()
                self.assertFalse(w.control._dirty)
                self.assertTrue(w._modal_input_clear_pending)
            w._clear_control_inputs_for_lock('operator recovery')
            self.assertFalse(w._modal_input_clear_pending)
            self.assertFalse(any(w.control.key_state.values()))

    def test_failed_mode_lookup_still_neutralizes_and_fails_closed(self):
        with fixtures.window_fixture() as (case, w, sink):
            case.key(Qt.Key.Key_W)
            class BrokenMode:
                @property
                def mock_output(self):
                    raise RuntimeError('mode unavailable')
            with patch.object(w.backend, 'mqtt', BrokenMode()), \
                 patch.object(w.control, 'release_direct_dt7') as dt7, \
                 patch.object(w.control, 'release_custom_controller') as custom, \
                 patch.object(w.backend, 'lock_control') as lock:
                w.open_match_chat()
                self.assertFalse(any(w.control.key_state.values()))
                dt7.assert_called_once()
                custom.assert_called_once()
                lock.assert_called_once()
                self.assertTrue(w._modal_input_clear_pending)

    def test_lock_cleanup_blocks_reentrant_key_admission(self):
        with fixtures.window_fixture() as (case, w, sink):
            case.key(Qt.Key.Key_W)
            def reenter():
                case.key(Qt.Key.Key_W)
            with patch.object(w.control, 'release_direct_dt7', side_effect=reenter):
                w._clear_control_inputs_for_lock('test')
            self.assertFalse(any(w.control.key_state.values()))

if __name__ == '__main__':
    unittest.main(verbosity=2)
