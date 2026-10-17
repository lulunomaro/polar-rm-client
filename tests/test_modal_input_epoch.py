"""Real window input ownership with inert transports, never hardware output."""
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_window_economy_commands as window_tests
from test_mouse_button_admission import Sink
from PySide6.QtCore import QPointF, Qt
from rm_client.control import KeyboardControlBridge

@contextmanager
def window_fixture():
    case = window_tests.WindowEconomyCommands()
    case.setUp()
    try:
        w = case.window
        w.economy_visible = False
        for name in ('MatchChat', 'HeroLob', 'QuickActionWheel'):
            w.registry.get(name).enabled = True
        sink = Sink()
        w.control = KeyboardControlBridge(sink, mock_output=False)
        w.backend.mqtt.mock_output = False
        w._combat_input_was_allowed = True
        yield case, w, sink
    finally:
        case.doCleanups()

class ModalInputEpochTests(unittest.TestCase):
    def test_repeated_owner_request_does_not_repeat_neutral_egress(self):
        from rm_client.ui.overlay_coordinator import OverlayCoordinator, OverlayKind
        from unittest.mock import Mock
        callback = Mock()
        coordinator = OverlayCoordinator(on_input_claimed=callback)
        for _ in range(10):
            coordinator.request_modal(OverlayKind.HERO_DEPLOY)
        callback.assert_called_once()
        coordinator.close_modal()
        coordinator.request_modal(OverlayKind.HERO_DEPLOY)
        self.assertEqual(callback.call_count, 2)
        for _ in range(10):
            coordinator.begin_chat()
        self.assertEqual(callback.call_count, 3)
        coordinator.end_chat()
        for _ in range(10):
            coordinator.begin_radial()
        self.assertEqual(callback.call_count, 4)

    def test_transient_chat_releases_motion_before_next_frame(self):
        with window_fixture() as (case, w, sink):
            case.key(Qt.Key.Key_W)
            self.assertTrue(w.control.key_state['W'])
            w.open_match_chat()
            self.assertFalse(w.control.key_state['W'])
            self.assertEqual(sink.frames[-1][1]['keyboard_value'], 0)
            case.key(Qt.Key.Key_W, release=True)
            w.close_match_chat()
            case.key(Qt.Key.Key_W, repeat=True)
            w.control.tick(w.state, w.registry, w.control.last_send_at + 1)
            self.assertFalse(w.control.key_state['W'])
            case.key(Qt.Key.Key_W)
            self.assertTrue(w.control.key_state['W'])

    def test_each_overlay_clears_held_inputs(self):
        actions = (
            lambda w: w.open_match_chat(),
            lambda w: w.set_clickgui_visible(True, announce=False, play_sound=False),
            lambda w: w.begin_quick_action_wheel(QPointF(30, 30)),
            lambda w: w.open_hero_deploy_interface(),
            lambda w: w.open_economy_workspace(),
            lambda w: w.open_student_panel('damage'),
            lambda w: w.set_escape_menu_visible(True),
        )
        for index, action in enumerate(actions):
            with self.subTest(overlay=index), window_fixture() as (case, w, sink):
                case.key(Qt.Key.Key_W)
                w.control.set_mouse_button(1, True, w.state, w.registry)
                w.control.add_mouse_motion(4, 5)
                w.control.add_wheel(2)
                action(w)
                self.assertFalse(any(w.control.key_state.values()))
                self.assertFalse(w.control.left_button_down)
                self.assertEqual((w.control.mouse_x, w.control.mouse_y, w.control.mouse_z), (0, 0, 0))
                self.assertEqual(sink.frames[-1][1]['keyboard_value'], 0)

    def test_failure_blocks_replay_and_attempts_other_egresses(self):
        with window_fixture() as (case, w, sink):
            case.key(Qt.Key.Key_W)
            with patch.object(w.control, 'reset_inputs', side_effect=RuntimeError('reset')), \
                 patch.object(w.control, 'release_direct_dt7', side_effect=RuntimeError('dt7')) as dt7, \
                 patch.object(w.control, 'release_custom_controller') as custom, \
                 patch.object(w.backend, 'lock_control', side_effect=RuntimeError('lock')) as lock:
                w.open_match_chat()
                dt7.assert_called_once()
                custom.assert_called_once()
                lock.assert_called_once()
                w.close_match_chat()
                self.assertFalse(w.combat_control_input_allowed())
                with patch.object(w.control, 'tick') as tick:
                    now = w._last_control_tick_time + 1
                    w._last_logic_time = now
                    w._advance_runtime_tick(now)
                    tick.assert_not_called()
                for _ in range(3):
                    w._advance_input_and_scroll(.01)
                lock.assert_called_once()
            w.backend.lock_control('operator recovery')
            self.assertFalse(w._modal_input_clear_pending)
            self.assertFalse(any(w.control.key_state.values()))

    def test_publication_reentry_cannot_inject_new_motion(self):
        with window_fixture() as (case, w, sink):
            case.key(Qt.Key.Key_W)
            original = sink.publish_keyboard_mouse
            nested = []
            def publish(*args, **kwargs):
                if kwargs['keyboard_value'] == 0 and not nested:
                    nested.append(True)
                    w.close_match_chat()
                    case.key(Qt.Key.Key_W)
                    w.open_hero_deploy_interface()
                return original(*args, **kwargs)
            with patch.object(sink, 'publish_keyboard_mouse', side_effect=publish):
                w.open_match_chat()
            self.assertEqual(nested, [True])
            self.assertFalse(w.control.key_state['W'])
            self.assertTrue(all(frame[1]['keyboard_value'] == 0 for frame in sink.frames[1:]))

if __name__ == '__main__':
    unittest.main(verbosity=2)
