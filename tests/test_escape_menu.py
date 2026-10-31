"""Real offscreen Qt events + production mixins, with inert hardware adapters."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt, QTimer, QPoint, QPointF
from PySide6.QtGui import QPainter, QColor, QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
from rm_client.ui.polar.mixins.escape_menu_mixin import EscapeMenuMixin
from rm_client.ui.polar.mixins.input_dispatch_mixin import InputDispatchMixin

APP = QApplication.instance() or QApplication([])

class Host(InputDispatchMixin, EscapeMenuMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.resize(1000, 620)
        self.home_visible = self.gui_visible = self.economy_visible = self.search_focus = False
        self.binding_module = None
        self.student_ui = NS(visible=False)
        self.hero_deploy_ui = NS(visible=False)
        self.match_chat = NS(input_open=False)
        self.quick_action_wheel = NS(active=False)
        self.overlay_coordinator = NS(radial_active=False, blocks_combat_input=False)
        self.registry = NS(get=lambda name: None, modules=[])
        self.backend = Mock(mqtt=NS(mock_output=True), lifecycle_pending=False)
        self.backend.trigger_estop.return_value = (True, "")
        self.control = Mock()
        self.state = NS()
        self.hotspots = []
        self.frame_scheduler = Mock()
        self.jello_state = NS(settings_module=None, legacy_mode=None)
        self.invalidate_transient_overlay_scene = Mock()
        self.finish_startup_page_transition = Mock()
        self.reset_startup_page_animation = Mock()
        self.close = Mock()
        self.toast = Mock()
        self.handle_student_key = lambda e, pressed: False
        self.minecraft_hud_active = lambda: False
        self.handle_control_key_event = Mock(return_value=False)
        self.toggle_bound_module_for_event = Mock(return_value=False)
        self.close_economy_workspace = lambda: setattr(self, 'economy_visible', False)
        self.close_match_chat = lambda **kw: setattr(self.match_chat, 'input_open', False)
        self.set_clickgui_visible = lambda value: setattr(self, 'gui_visible', value)
        self.handle_keybind_event = lambda e: setattr(self, 'binding_module', None)
        self.close_hero_deploy_interface = lambda **kw: setattr(self.hero_deploy_ui, 'visible', False)
        self.tactical_map_hold = True
        self.show()
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#15232c'))
        p.setPen(QColor('#849aa8'))
        p.drawText(24, 32, 'NON-HARDWARE PREVIEW · offscreen Qt · inert background / no GPU blur')
        self.draw_escape_menu(p, self.width(), self.height())
        p.end()

class EscapeMenuTests(unittest.TestCase):
    def setUp(self):
        self.w = Host()
    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()
        APP.processEvents()
    def key(self, key):
        QTest.keyClick(self.w, key)
    def open(self):
        self.key(Qt.Key.Key_Escape)
        self.assertTrue(self.w.escape_menu_visible)
    def test_repeat_cycles_clear_inputs_without_pausing_backend(self):
        ticks = []
        timer = QTimer(self.w)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(1)
        for _ in range(5):
            self.open()
            QTest.qWait(10)
            self.assertFalse(self.w.combat_control_input_allowed())
            self.key(Qt.Key.Key_Escape)
            self.assertFalse(self.w.escape_menu_visible)
        self.assertGreater(len(ticks), 0)
        for action in ("reset_inputs", "send_from_state", "release_direct_dt7", "release_custom_controller"):
            self.assertEqual(getattr(self.w.control, action).call_count, 10)
        self.assertFalse(self.w.tactical_map_hold)
        self.w.backend.shutdown.assert_not_called()
    def test_input_isolation_and_autorepeat(self):
        self.open()
        for key in (Qt.Key.Key_W, Qt.Key.Key_1, Qt.Key.Key_F8, Qt.Key.Key_B): self.key(key)
        for typ in (QKeyEvent.Type.KeyPress, QKeyEvent.Type.KeyRelease):
            APP.sendEvent(self.w, QKeyEvent(typ, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier, '', True))
        self.assertTrue(self.w.escape_menu_visible)
        QTest.mouseMove(self.w, QPoint(10, 10))
        QTest.mouseClick(self.w, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        wheel = QWheelEvent(QPointF(10,10), QPointF(10,10), QPoint(), QPoint(0,120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        APP.sendEvent(self.w, wheel)
        self.w.handle_control_key_event.assert_not_called()
        self.w.toggle_bound_module_for_event.assert_not_called()
        self.w.control.set_mouse_button.assert_not_called()
        self.w.control.add_mouse_motion.assert_not_called()
        self.w.control.add_wheel.assert_not_called()
    def test_existing_modal_esc_priority(self):
        for target, attr in ((self.w,'gui_visible'), (self.w,'economy_visible'), (self.w,'search_focus'), (self.w,'binding_module'), (self.w.match_chat,'input_open')):
            setattr(target, attr, True)
            self.key(Qt.Key.Key_Escape)
            self.assertFalse(self.w.escape_menu_visible, attr)
            self.assertFalse(getattr(target,attr), attr)
        self.open()
    def test_nested_settings_student_and_radial_precedence(self):
        self.w.gui_visible = True
        self.w.jello_state.settings_module = object()
        self.w.jello_state.close_settings = lambda: setattr(self.w.jello_state, 'settings_module', None)
        self.key(Qt.Key.Key_Escape)
        self.assertTrue(self.w.gui_visible)
        self.assertFalse(self.w.escape_menu_visible)
        self.key(Qt.Key.Key_Escape)
        self.assertFalse(self.w.gui_visible)
        self.w.student_ui.visible = True
        def student(event, pressed):
            if self.w.student_ui.visible:
                if pressed and event.key() == Qt.Key.Key_Escape:
                    self.w.student_ui.visible = False
                return True
            return False
        self.w.handle_student_key = student
        self.key(Qt.Key.Key_Escape)
        self.assertFalse(self.w.escape_menu_visible)
        self.w.quick_action_wheel.active = True
        self.w.cancel_quick_action_wheel = lambda: setattr(self.w.quick_action_wheel, 'active', False)
        self.key(Qt.Key.Key_Escape)
        self.assertFalse(self.w.escape_menu_visible)
        self.open()

    def test_exit_confirmation_cancel_and_commit(self):
        self.open()
        self.key(Qt.Key.Key_Up)
        self.key(Qt.Key.Key_Up)
        self.key(Qt.Key.Key_Return)
        self.assertTrue(self.w._escape_menu_confirm_exit)
        self.w.close.assert_not_called()
        self.key(Qt.Key.Key_Return) # Safe default Cancel.
        self.assertFalse(self.w._escape_menu_confirm_exit)
        self.w.activate_escape_menu_choice(3)
        self.key(Qt.Key.Key_Escape)
        self.assertTrue(self.w.escape_menu_visible)
        self.assertFalse(self.w._escape_menu_confirm_exit)
        self.w.activate_escape_menu_choice(3)
        self.key(Qt.Key.Key_Down)
        self.key(Qt.Key.Key_Return)
        self.w.close.assert_called_once()
    def test_return_startup_locks_first(self):
        self.open()
        events = []
        self.w.backend.lock_control.side_effect = lambda reason: events.append(('lock', self.w.home_visible))
        self.w.activate_escape_menu_choice(2)
        self.assertEqual(events, [('lock',False)])
        self.assertTrue(self.w.home_visible)
        self.assertEqual(self.w.startup_step, 'scenario')
        self.assertFalse(self.w.escape_menu_visible)
        self.w.backend.shutdown.assert_not_called()

    def test_real_settings_entry_lazy_dispatch_preserves_menu_and_safe_state(self):
        import sys
        self.open()
        settings = Mock()
        module = NS(open_settings_center=settings)
        with patch.dict(sys.modules, {'rm_client.ui.cs2_settings_center': module}):
            self.w.activate_escape_menu_choice(1)
        settings.assert_called_once_with(self.w)
        self.assertTrue(self.w.escape_menu_visible)
        self.assertFalse(self.w.home_visible)
        self.assertFalse(self.w._escape_menu_confirm_exit)
        self.w.backend.shutdown.assert_not_called()
        self.w.backend.arm_control.assert_not_called()

    def test_cs_navigation_rows_are_horizontal_and_contained(self):
        self.open()
        panel, rows, scale = self.w.escape_menu_layout(1000, 620)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(panel.contains(rect) for rect in rows))
        self.assertEqual(len({rect.y() for rect in rows}), 1)
        self.assertEqual(rows[0].height(), 64 * scale)
        self.assertTrue(all(a.right() < b.left() for a, b in zip(rows, rows[1:])))
    def test_mouse_capture_and_resize(self):
        self.open()
        for size in ((1000,620),(640,400),(2560,1440)):
            self.w.resize(*size)
            panel, rows, _ = self.w.escape_menu_layout(*size)
            self.assertTrue(self.w.rect().contains(panel.toRect()))
            QTest.mouseRelease(self.w, Qt.MouseButton.LeftButton, pos=rows[3].center().toPoint())
            self.assertFalse(self.w._escape_menu_confirm_exit)
        QTest.mouseClick(self.w, Qt.MouseButton.LeftButton, pos=rows[3].center().toPoint())
        self.assertTrue(self.w._escape_menu_confirm_exit)
    def test_held_control_released_without_replay(self):
        from rm_client.control import KeyboardControlBridge
        bridge = KeyboardControlBridge(mock_output=False)
        snapshots = []
        bridge.send_from_state = lambda *args: snapshots.append((dict(bridge.key_state), bridge.left_button_down, bridge.mock_output))
        bridge.release_direct_dt7 = Mock()
        bridge.release_custom_controller = Mock()
        self.w.backend.mqtt.mock_output = False
        self.w.control = bridge
        bridge.set_key("W", True, self.w.state, self.w.registry)
        bridge.set_mouse_button(1, True, self.w.state, self.w.registry)
        self.open()
        self.assertFalse(any(bridge.key_state.values()))
        self.assertFalse(bridge.left_button_down)
        QTest.keyRelease(self.w, Qt.Key.Key_W)
        QTest.mouseRelease(self.w, Qt.MouseButton.LeftButton, pos=QPoint(1,1))
        self.key(Qt.Key.Key_Escape)
        self.assertTrue(all(not any(keys.values()) and not mouse and not mock for keys,mouse,mock in snapshots[2:]))
        self.assertEqual(bridge.release_direct_dt7.call_count, 2)
        self.assertEqual(bridge.release_custom_controller.call_count, 2)
        self.assertTrue(bridge._dirty)  # A failed/inert neutral send must remain retryable.
        self.assertFalse(any(bridge.key_state.values()))
        self.w.handle_control_key_event.assert_not_called()

    def test_missing_menu_keyup_does_not_swallow_new_control_release(self):
        self.open()
        QTest.keyPress(self.w, Qt.Key.Key_W)
        self.w.set_escape_menu_visible(False)
        self.key(Qt.Key.Key_W)
        self.assertEqual(self.w.handle_control_key_event.call_count, 2)
        self.assertEqual([call.args[1] for call in self.w.handle_control_key_event.call_args_list], [True, False])

    def test_continue_enter_release_does_not_leak(self):
        self.open()
        self.key(Qt.Key.Key_Return)
        self.assertFalse(self.w.escape_menu_visible)
        self.w.handle_control_key_event.assert_not_called()

    def test_estop_still_available(self):
        self.open()
        self.key(Qt.Key.Key_F12)
        self.w.backend.trigger_estop.assert_called_once()
        self.assertTrue(self.w.escape_menu_visible)

if __name__ == '__main__':
    if '--preview' in sys.argv:
        w = Host()
        w.set_escape_menu_visible(True)
        APP.processEvents()
        w.grab().save('/tmp/polar-jello-menu-offscreen.png')
        print('/tmp/polar-jello-menu-offscreen.png')
    else:
        unittest.main()
