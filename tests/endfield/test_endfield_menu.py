"""Modal/semantic contract; inert renderer, no robot, game or GPU required."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent
from test_escape_menu import APP, Host


class NativeMenu:
    def device_info(self):
        return {}

    def __init__(self):
        self.requested = False
        self.phase = 0
        self.attempted = self.ready = True
        self.error = ""
        self.action = 0
        self.pointer = Mock()

    def request(self, value, **_kwargs):
        self.requested = value
        self.phase = 1 if value else 2

    def tick(self, **_kwargs):
        action, self.action = self.action, 0
        return action


class EndfieldMenuTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.menu = self.host._endfield_menu = NativeMenu()
        self.host.match_mode_manager = NS(current=NS(key="RMUL3v3"))
        self.host.open_student_panel = Mock()

    def tearDown(self):
        self.host.hide()
        self.host.deleteLater()
        APP.processEvents()

    def test_close_holds_input_until_actual_conceal_ack(self):
        self.host.set_escape_menu_visible(True)
        self.host.set_escape_menu_visible(False)
        self.assertTrue(self.host.escape_menu_visible)
        self.host.advance_endfield_menu()
        self.assertTrue(self.host.escape_menu_visible)
        self.menu.phase = 0
        self.host.advance_endfield_menu()
        self.assertFalse(self.host.escape_menu_visible)
        self.host.handle_control_key_event.assert_not_called()

    def test_action_waits_for_exit_and_is_dispatched_once(self):
        self.host.set_escape_menu_visible(True)
        self.menu.action = 10
        self.host.advance_endfield_menu()
        self.host.open_student_panel.assert_not_called()
        self.menu.phase = 0
        self.host.advance_endfield_menu()
        self.host.advance_endfield_menu()
        self.host.open_student_panel.assert_called_once_with("inventory")

    def test_reopen_cancels_a_retired_route(self):
        self.host.set_escape_menu_visible(True)
        self.menu.action = 10
        self.host.advance_endfield_menu()
        self.host.set_escape_menu_visible(True)
        self.host.advance_endfield_menu()
        self.assertTrue(self.host.escape_menu_visible)
        self.host.open_student_panel.assert_not_called()

    def test_pending_visibility_is_modal_and_failure_can_escape(self):
        self.host.set_escape_menu_visible(True)
        self.menu.phase = 3  # Native request not yet acknowledged.
        self.host.set_escape_menu_visible(False)
        self.assertTrue(self.host.escape_menu_visible)
        self.menu.error = "No device"
        self.host.advance_endfield_menu()
        self.assertFalse(self.host.escape_menu_visible)

    def test_startup_never_opens_menu(self):
        self.host.home_visible = True
        self.host.set_escape_menu_visible(True)
        self.assertFalse(self.menu.requested)

    def test_pointer_uses_the_same_physical_dpi_transform(self):
        self.host.set_escape_menu_visible(True)
        self.host.devicePixelRatioF = lambda: 1.5
        event = NS(position=lambda: QPointF(30, 60), button=lambda: Qt.MouseButton.LeftButton, accept=Mock())
        self.host.handle_escape_menu_mouse(event, "release")
        self.menu.pointer.assert_called_once_with(3, 45.0, 90.0)
        event.accept.assert_called_once()

    def test_qt_window_leave_retires_native_hover(self):
        self.host.set_escape_menu_visible(True)
        self.host.leaveEvent(QEvent(QEvent.Type.Leave))
        self.menu.pointer.assert_called_once_with(6, 0, 0)

    def test_menu_keys_never_become_combat_input(self):
        self.host.set_escape_menu_visible(True)
        event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(self.host.handle_escape_menu_key(event))
        self.menu.pointer.assert_called_once_with(5, 0, 0, 1)
        self.host.handle_control_key_event.assert_not_called()

    def test_retired_hero_action_has_no_effect(self):
        self.host.set_clickgui_visible = Mock()
        self.host.dispatch_endfield_action(14)
        self.host.set_clickgui_visible.assert_not_called()

    def test_ui_switch_defers_dialog_and_keeps_escape_open(self):
        from rm_client.ui.endfield.menu.actions import UI_SWITCH_ACTION
        self.host.set_escape_menu_visible(True)
        self.menu.action = UI_SWITCH_ACTION
        self.host.dispatch_endfield_action = Mock()
        self.host.advance_endfield_menu()
        self.assertTrue(self.host.escape_menu_visible)
        self.assertTrue(self.menu.requested)
        self.host.dispatch_endfield_action.assert_not_called()
        APP.processEvents()
        self.host.dispatch_endfield_action.assert_called_once_with(UI_SWITCH_ACTION)
        self.assertIsNone(self.host._endfield_pending_action)

    def test_ui_switch_direct_dispatch_opens_confirmation(self):
        from rm_client.ui.endfield.menu.actions import UI_SWITCH_ACTION
        self.host.set_escape_menu_visible(True)
        self.host.set_clickgui_visible = Mock()
        with patch("rm_client.ui.ui_switch.open_ui_switch") as switch:
            self.host.dispatch_endfield_action(UI_SWITCH_ACTION)
        switch.assert_called_once_with(self.host)
        self.host.set_clickgui_visible.assert_not_called()
        self.host.open_student_panel.assert_not_called()
        self.assertTrue(self.host.escape_menu_visible)

    def test_changelog_keeps_escape_open_and_defers_modal_until_after_tick(self):
        self.host.set_escape_menu_visible(True)
        states = []
        with patch("rm_client.ui.endfield.dialogs.show_changelog", side_effect=lambda host: states.append(
                (host.escape_menu_visible, self.menu.requested, self.menu.phase))) as show:
            self.menu.action = 7
            self.host.advance_endfield_menu()
            show.assert_not_called()
            self.assertTrue(self.menu.requested)
            self.assertTrue(self.host.escape_menu_visible)
            APP.processEvents()
            show.assert_called_once_with(self.host)
        self.assertEqual(states, [(True, True, 1)])
        self.assertTrue(self.host.escape_menu_visible)
        self.assertTrue(self.menu.requested)
        self.assertFalse(self.host._endfield_changelog_pending)

    def test_dismissed_escape_cancels_deferred_changelog(self):
        self.host.set_escape_menu_visible(True)
        self.menu.action = 7
        with patch("rm_client.ui.endfield.dialogs.show_changelog") as show:
            self.host.advance_endfield_menu()
            self.host.set_escape_menu_visible(False)
            APP.processEvents()
            show.assert_not_called()
        self.assertFalse(self.host._endfield_changelog_pending)

    def test_settings_and_keybinds_are_deferred_children_of_escape(self):
        for action in (2,5,6):
            with self.subTest(action=action):
                self.host.set_escape_menu_visible(True)
                with patch.object(self.host,"dispatch_endfield_action") as dispatch:
                    self.menu.action = action
                    self.host.advance_endfield_menu()
                    dispatch.assert_not_called()
                    self.assertTrue(self.menu.requested)
                    self.assertTrue(self.host.escape_menu_visible)
                    self.menu.action = action
                    with patch.object(self.menu,"tick",wraps=self.menu.tick) as tick:
                        self.host.advance_endfield_menu()
                        self.assertTrue(tick.call_args.kwargs["paused"])
                    APP.processEvents()
                    dispatch.assert_called_once_with(action)
                self.assertFalse(self.host._endfield_settings_pending)
                self.assertTrue(self.menu.requested)
                self.assertTrue(self.host.escape_menu_visible)

    def test_dismissed_escape_cancels_deferred_settings(self):
        self.host.set_escape_menu_visible(True)
        self.menu.action = 2
        with patch.object(self.host,"dispatch_endfield_action") as dispatch:
            self.host.advance_endfield_menu()
            self.host.set_escape_menu_visible(False)
            APP.processEvents()
            dispatch.assert_not_called()
        self.assertFalse(self.host._endfield_settings_pending)


if __name__ == "__main__":
    unittest.main()
