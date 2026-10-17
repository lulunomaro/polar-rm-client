"""Fullscreen input/geometry interruptions with real Qt and inert outputs.

Run with QT_QPA_PLATFORM=offscreen python tests/test_fullscreen_input_extended.py.
QT_SCALE_FACTOR may be 1, 1.25, 1.5, 2 or 2.5. Native Windows calls are mocked;
this does not qualify physical-display DWM/GPU presentation or robot hardware.
"""
import ast
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import shiboken6
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import QComboBox, QDialog, QInputDialog, QWidget
from PySide6.QtTest import QTest
from test_startup_settings_fullscreen import APP, Host
from rm_client.ui import fullscreen_composition as composition
from rm_client.ui.cs2_settings_center import build_settings_center
from rm_client.ui.cs2_video_settings import VideoPreferences, apply_window_settings

ROOT = Path(__file__).resolve().parents[1]
# Reuse the production focus-loss body in a QWidget harness. The GL window
# cannot be shown by Qt's offscreen plugin; input-clear hooks remain real.
source = ROOT / 'rm_client/ui/polar_app.py'
tree = ast.parse(source.read_text())
window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PolarRMWindow')
focus_method = next(n for n in window.body if isinstance(n, ast.FunctionDef) and n.name == 'focusOutEvent')
cls = ast.ClassDef(name='FocusHost', bases=[ast.Name(id='Host',ctx=ast.Load())], keywords=[],body=[focus_method],decorator_list=[])
from rm_client.emergency_actions import request_focus_loss
scope={'Host':Host, 'request_focus_loss': request_focus_loss}
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),str(source),'exec'),scope)
FocusHost=scope['FocusHost']

class InputLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.host = FocusHost()
        self.host.hotspots=[]
        self.host.backend.mqtt.publish_estop_zero=Mock(return_value=(True,''))
        self.host.backend.trigger_estop.side_effect=lambda: (self.host._clear_control_inputs_for_lock('E-stop'), (True,''))[1]
        self.host.backend.lock_control.side_effect=self.host._clear_control_inputs_for_lock
        self.host.control.release_direct_dt7=Mock()
        self.host.control.release_custom_controller=Mock()
        self.host._economy_qualification_token=object()
        self.children=[]
    def tearDown(self):
        for child in self.children:
            if shiboken6.isValid(child): child.hide(); child.deleteLater()
        self.host.hide(); self.host.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete); APP.processEvents()
    def hold(self):
        c=self.host.control
        c.key_state['W']=True; c.key_state['SHIFT']=True
        c.left_button_down=c.right_button_down=c.mid_button_down=True
        c.mouse_x=23; c.mouse_y=-41; c.mouse_z=120; c._mouse_remainder=(.2,.3)
        self.host._control_mouse_point=QPointF(20,30)
        self.host.tactical_map_hold=True
    def neutral(self):
        c=self.host.control
        self.assertFalse(any(c.key_state.values()))
        self.assertEqual((c.mouse_x,c.mouse_y,c.mouse_z),(0,0,0))
        self.assertEqual(c._mouse_remainder,(0,0))
        self.assertFalse(c.left_button_down or c.right_button_down or c.mid_button_down)
        self.assertIsNone(self.host._control_mouse_point)
        self.host.backend.arm_match.assert_not_called()
    def key(self, key=Qt.Key.Key_F11, widget=None):
        QTest.keyClick(widget or self.host,key); APP.processEvents()
    def test_100_fast_pairs_retain_geometry_and_native_latest_state(self):
        original=self.host.geometry(); observed=[]
        with patch.object(composition.sys,'platform','win32'), patch.object(composition,'_ensure_native_border',side_effect=lambda hwnd:observed.append((hwnd,self.host.isFullScreen(),self.host.isMinimized()))):
            for _ in range(100):
                self.hold(); QTest.keyClick(self.host,Qt.Key.Key_F11); self.neutral()
                QTest.keyClick(self.host,Qt.Key.Key_F11); self.neutral()
            APP.processEvents()
        self.assertFalse(self.host.isFullScreen()); self.assertEqual(self.host.geometry(),original)
        self.assertEqual(len(observed),100)
        self.assertTrue(all(hwnd and full and not minimized for hwnd,full,minimized in observed))
        self.assertIsNone(self.host._economy_qualification_token)
    def test_minimize_restore_and_fullscreen_repeat_keep_restore_geometry(self):
        original=self.host.geometry()
        for maximized in (False,True):
            if maximized: self.host.showMaximized(); APP.processEvents()
            for _ in range(5):
                self.key(); self.assertTrue(self.host.isFullScreen())
                self.host.showMinimized(); APP.processEvents(); self.assertTrue(self.host.isMinimized())
                self.host.showFullScreen(); APP.processEvents()
                self.hold(); self.key(); self.neutral()
                self.assertEqual(self.host.isMaximized(),maximized)
            self.host.showNormal(); APP.processEvents()
            self.assertEqual(self.host.geometry(),original)
    def test_f11_does_not_replay_held_input_after_focus_loss_or_repeat(self):
        self.hold(); self.key(); self.neutral()
        self.hold(); APP.sendEvent(self.host,QFocusEvent(QEvent.Type.FocusOut)); self.neutral()
        self.assertFalse(self.host.control._dirty)
        self.assertGreaterEqual(self.host.control.release_direct_dt7.call_count,2)
        self.assertGreaterEqual(self.host.control.release_custom_controller.call_count,2)
        self.host.backend.mqtt.publish_estop_zero.assert_called_once()
        calls=self.host.backend.lock_control.call_count
        for kind in (QEvent.Type.ShortcutOverride,QEvent.Type.KeyPress,QEvent.Type.KeyRelease):
            APP.sendEvent(self.host,QKeyEvent(kind,Qt.Key.Key_F11,Qt.KeyboardModifier.NoModifier,'',True))
        self.assertEqual(self.host.backend.lock_control.call_count,calls)
        self.neutral()
    def test_escape_menu_f11_preserves_menu_and_keys_do_not_leak_after_close(self):
        self.host.home_visible=False
        self.hold(); self.host.set_escape_menu_visible(True); self.neutral()
        QTest.keyPress(self.host,Qt.Key.Key_W)
        self.key(); self.assertTrue(self.host.escape_menu_visible); self.neutral()
        self.key(Qt.Key.Key_Escape); self.assertFalse(self.host.escape_menu_visible)
        self.host.handle_control_key_event.reset_mock()
        QTest.keyRelease(self.host,Qt.Key.Key_W)
        self.host.handle_control_key_event.assert_not_called(); self.neutral()
    def test_lazy_page_f11_nested_modal_f12_and_cancel_preserve_safety(self):
        dialog=build_settings_center(self.host); self.children.append(dialog)
        dialog.show(); QTest.qWait(480)
        page=dialog.pages['界面 / HUD']; dialog.tabs.setCurrentWidget(page); QTest.qWait(30)
        self.assertGreater(page.builder.slices,0); self.assertFalse(page.builder.ready)
        self.hold(); self.key(widget=dialog.search); self.neutral(); self.assertTrue(dialog.isVisible())
        nested=QInputDialog(dialog); self.children.append(nested); errors=[]
        def interact():
            try:
                self.key(widget=nested); self.assertFalse(self.host.isFullScreen())
                self.hold(); self.key(Qt.Key.Key_F12,nested); self.neutral()
                self.host.backend.trigger_estop.assert_called_once()
            except BaseException as e: errors.append(e)
            finally: nested.reject()
        QTimer.singleShot(0,interact); nested.exec()
        if errors: raise errors[0]
        self.assertTrue(dialog.isVisible())
        dialog.reject(); QTest.qWait(200); count=len(page.controls); QTest.qWait(80)
        self.assertEqual(len(page.controls),count); self.assertTrue(page.builder.cancelled)
        self.host.auto_save_config.assert_not_called(); self.neutral()
    def test_f11_child_combo_popup_and_non_owned_dialog(self):
        combo=QComboBox(self.host); combo.addItems(['one','two']); combo.show(); self.children.append(combo)
        combo.showPopup(); APP.processEvents(); self.key(widget=combo.view())
        self.assertTrue(self.host.isFullScreen()); combo.hidePopup()
        other=QDialog(); other.show(); self.children.append(other)
        self.key(widget=other); self.assertTrue(self.host.isFullScreen())
        self.key(); self.assertFalse(self.host.isFullScreen())
    def test_explicit_physical_resolution_respects_current_dpr(self):
        self.host.showMaximized(); APP.processEvents(); self.key()
        apply_window_settings(self.host, VideoPreferences(resolution='1280×720'))
        APP.processEvents()
        ratio=max(1,self.host.devicePixelRatioF())
        self.assertEqual((self.host.width(),self.host.height()),
                         (round(1280/ratio),round(720/ratio)))
        self.assertFalse(self.host.isFullScreen()); self.assertFalse(self.host.isMaximized())

    def test_pending_guard_uses_new_handle_and_no_mutation_after_delete(self):
        host=QWidget(); host.showFullScreen(); APP.processEvents()
        guard=composition._FullscreenCompositionGuard(host); hwnd=[123]
        host.internalWinId=lambda:hwnd[0]
        with patch.object(composition,'_ensure_native_border') as apply:
            guard.eventFilter(host,QEvent(QEvent.Type.WinIdChange)); hwnd[0]=456; APP.processEvents()
            apply.assert_called_once_with(456)
            guard.eventFilter(host,QEvent(QEvent.Type.WinIdChange)); shiboken6.delete(host); APP.processEvents()
            apply.assert_called_once_with(456)

class GestureInterruptionTests(unittest.TestCase):
    def setUp(self):
        from test_clickgui_background_dismiss import Host as GuiHost
        from rm_client.ui.window_shortcuts import WindowShortcuts
        self.host = GuiHost()
        self.shortcuts = WindowShortcuts(self.host)
        self.host.build(); APP.processEvents()
    def tearDown(self):
        self.host.hide(); self.host.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete); APP.processEvents()
    def toggle(self):
        QTest.keyClick(self.host,Qt.Key.Key_F11); APP.processEvents()
        self.host.build()
    def test_f11_cancels_pending_background_dismiss_but_owns_release(self):
        from PySide6.QtCore import QPoint
        QTest.mousePress(self.host,Qt.MouseButton.LeftButton,pos=QPoint(2,2))
        self.toggle()
        self.assertIsNone(self.host._jello_background_press)
        self.assertTrue(self.host._jello_background_release_owned)
        QTest.mouseRelease(self.host,Qt.MouseButton.LeftButton,pos=QPoint(2,2))
        self.assertTrue(self.host.gui_visible)
        self.assertFalse(self.host._jello_background_release_owned)
        self.host.control.set_mouse_button.assert_not_called()
        self.host.publish_map_click_from_screen.assert_not_called()
        # Cancellation owns one release, not the next intentional click.
        QTest.mouseClick(self.host,Qt.MouseButton.LeftButton,pos=QPoint(2,2))
        self.assertFalse(self.host.gui_visible)
    def test_f11_cancels_module_press_without_toggling_or_autosaving(self):
        from PySide6.QtCore import QPoint
        row=next(h for h in self.host.hotspots if h.action=='jello_module_press' and h.payload != 'HeroLob')
        module=self.host.registry.get(row.payload); before=module.enabled
        QTest.mousePress(self.host,Qt.MouseButton.LeftButton,pos=row.rect.center().toPoint())
        self.toggle()
        self.assertIsNone(self.host.drag)
        QTest.mouseRelease(self.host,Qt.MouseButton.LeftButton,pos=QPoint(2,2))
        self.assertEqual(module.enabled,before)
        self.host.auto_save_config.assert_not_called()
        self.host.control.set_mouse_button.assert_not_called()
    def test_f11_cancels_slider_stale_geometry_and_preserves_last_value(self):
        from PySide6.QtCore import QPoint
        self.host.jello_state.open_settings('ClickGui')
        self.host.jello_state.settings_panel_animation.reset(1)
        self.host.jello_state.settings_background_animation.reset(1)
        self.host.build()
        slider=next(h for h in self.host.hotspots if h.action=='setting_slider')
        QTest.mousePress(self.host,Qt.MouseButton.LeftButton,pos=slider.rect.center().toPoint())
        value=slider.payload.value
        self.toggle()
        self.assertIsNone(self.host.drag)
        QTest.mouseMove(self.host,QPoint(2,2))
        QTest.mouseRelease(self.host,Qt.MouseButton.LeftButton,pos=QPoint(2,2))
        self.assertEqual(slider.payload.value,value)
        self.assertTrue(self.host.gui_visible)
        self.host.control.set_mouse_button.assert_not_called()
    def test_f11_cancels_exit_confirmation_press_across_layout_change(self):
        self.host.gui_visible=False
        self.host.set_escape_menu_visible(True)
        self.host.activate_escape_menu_choice(3)
        old=self.host.escape_menu_layout(self.host.width(),self.host.height())[1][1]
        QTest.mousePress(self.host,Qt.MouseButton.LeftButton,pos=old.center().toPoint())
        self.toggle()
        self.assertIsNone(self.host._escape_menu_pressed)
        new=self.host.escape_menu_layout(self.host.width(),self.host.height())[1][1]
        QTest.mouseRelease(self.host,Qt.MouseButton.LeftButton,pos=new.center().toPoint())
        self.host.close.assert_not_called()
        self.assertTrue(self.host.escape_menu_visible)
        self.host.control.set_mouse_button.assert_not_called()

if __name__=='__main__':
    print('AUDIT_REPO',ROOT,'DPR',APP.primaryScreen().devicePixelRatio(),flush=True)
    unittest.main()
