"""Native Jello coordinates/timelines, keyboard draft ownership and input safety."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD","1")
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from rm_client.ui.jello_widgets import JelloDialog, JelloCheckBox, JelloComboBox, ease_out_back, jello_font
from rm_client.ui.jello_clickgui import JelloMainTimeline, JelloReversibleAnimation
from rm_client.ui.jello_keybinds import (ALL_KEYS, NATIVE_KEYS, BindingSession, KeyboardCanvas,
                                        BindingPicker, build_keyboard_manager, open_keyboard_manager)
from rm_client.ui.cs2_settings_center import build_settings_center
from test_cs2_settings_center import Host, APP, ready_page


class JelloKeyboardTests(unittest.TestCase):
    def setUp(self):
        self.host=Host();self.addCleanup(self.host.deleteLater)

    def close_dialog(self, dialog):
        dialog.reject();QTest.qWait(160);dialog.deleteLater();APP.processEvents()

    def test_native_61_keys_exact_nonuniform_widths_and_coordinates(self):
        self.assertEqual(len(NATIVE_KEYS),61)
        keys={k.name:k for k in NATIVE_KEYS}
        self.assertEqual((keys['Q'].x,keys['Q'].y,keys['Q'].width),(108,74,63))
        self.assertEqual((keys['Space'].x,keys['Space'].y,keys['Space'].width),(290,296,427))
        self.assertEqual((keys['RightShift'].x,keys['RightShift'].width),(888,172))
        self.assertEqual((keys['Backspace'].x,keys['Backspace'].width),(943,117))
        self.assertEqual(max(k.x+k.width for k in NATIVE_KEYS),1060)
        self.assertTrue(all(k.height==63 for k in NATIVE_KEYS))
        self.assertEqual(len(ALL_KEYS),84)
        self.assertFalse(next(k for k in ALL_KEYS if k.name=='F12').native)

    def test_original_time_curves_without_fps_dependence(self):
        self.assertEqual((JelloMainTimeline().forward_duration,JelloMainTimeline().reverse_duration),(.450,.125))
        self.assertAlmostEqual(ease_out_back(0),0);self.assertAlmostEqual(ease_out_back(1),1)
        self.assertGreater(ease_out_back(.65),1)
        for fps in (30,60,75,90,144):
            anim=JelloReversibleAnimation(.2,.125);anim.set_target(1)
            elapsed=0
            while elapsed<.2:
                dt=min(1/fps,.2-elapsed);anim.update(dt);elapsed+=dt
            self.assertAlmostEqual(anim.progress,1)
            anim.set_target(0);anim.update(.125);self.assertEqual(anim.progress,0)

    def test_reserved_keys_cannot_bind_or_clear(self):
        session=BindingSession(self.host)
        target=next(e for e in session.entries if e.identity==('module','CombatTargetHUD'))
        for key in ('F12','RightShift','Tab','Escape','F8','F9'):
            with self.assertRaises(ValueError):session.assign(target,key)
        click=next(e for e in session.entries if e.identity==('module','ClickGui'))
        with self.assertRaises(ValueError):session.clear(click)
        self.assertEqual(self.host.registry.get('TacticalMap').keybind,'Tab')

    def test_existing_key_conflict_never_evicts_without_clear(self):
        session=BindingSession(self.host)
        target=next(e for e in session.entries if e.identity==('module','TacticalMap'))
        hud=next(e for e in session.entries if e.identity==('module','HUD'))
        with self.assertRaises(ValueError):session.assign(target,'H')
        self.assertEqual(session.values[hud.identity],'H')
        session.clear(hud);session.assign(target,'H')
        self.assertEqual(self.host.registry.get('HUD').keybind,'H')
        self.assertEqual(self.host.registry.get('TacticalMap').keybind,'Tab')
        session.apply()
        self.assertEqual(self.host.registry.get('HUD').keybind,'None')
        self.assertEqual(self.host.registry.get('TacticalMap').keybind,'H')
        self.host.backend.arm_match.assert_not_called()

    def test_driver_conflict_and_unsupported_key_rejected(self):
        session=BindingSession(self.host)
        w=next(e for e in session.entries if e.identity==('RobotLink','Bind W'))
        s=next(e for e in session.entries if e.identity==('RobotLink','Bind S'))
        with self.assertRaises(ValueError):session.assign(w,'S')
        with self.assertRaises(ValueError):session.assign(w,'MetaLeft')
        session.clear(s);session.assign(w,'S')
        self.assertEqual(self.host.control.binds['W'],'W')

    def test_keyboard_edits_only_settings_page_draft(self):
        dialog=build_settings_center(self.host)
        page=ready_page(dialog,'键盘 / 鼠标')
        session=BindingSession(self.host,page)
        target=next(e for e in session.entries if e.identity==('module','TacticalMap'))
        session.assign(target,'F10');session.apply()
        self.assertEqual(page.module_bind_controls['TacticalMap'].value,'F10')
        self.assertEqual(self.host.registry.get('TacticalMap').keybind,'Tab')
        self.host.auto_save_config.assert_not_called()
        dialog.deleteLater();APP.processEvents()

    def test_keyboard_cancel_has_no_registry_writes_or_arm(self):
        dialog=Mock();dialog.exec.return_value=QDialog.DialogCode.Rejected
        with patch('rm_client.ui.jello_keybinds.build_keyboard_manager',return_value=dialog):
            open_keyboard_manager(self.host,target=('module','TacticalMap'))
        dialog.session.apply.assert_not_called()
        self.host.backend.lock_control.assert_called_once_with('keyboard manager opened')
        self.host.backend.arm_match.assert_not_called()

    def test_mapping_matches_dpr_independent_geometry_all_sizes(self):
        dialog=build_keyboard_manager(self.host)
        canvas=dialog.canvas
        for width,height in ((600,430),(1280,720),(1600,900),(1920,1080),(2560,1440)):
            canvas.resize(width,height)
            x,y,scale=canvas.mapping()
            for key in ALL_KEYS:
                logical=canvas.key_rect(key).center()
                physical=QPointF(x+logical.x()*scale,y+logical.y()*scale)
                self.assertEqual(canvas.key_at(canvas.logical_point(physical)),key)
        dialog.deleteLater();APP.processEvents()

    def test_popover_250x330_flips_and_stays_inside_logical_viewport(self):
        dialog=build_keyboard_manager(self.host)
        for name in ('W','Space','RightShift','F10','Right'):
            dialog.canvas.selected=name;rect=dialog.canvas.pop_rect()
            self.assertEqual((rect.width(),rect.height()),(250,330))
            self.assertGreaterEqual(rect.left(),0);self.assertLessEqual(rect.right(),1200)
            self.assertGreaterEqual(rect.top(),0);self.assertLessEqual(rect.bottom(),750)
        dialog.deleteLater();APP.processEvents()

    def test_real_keyboard_and_nested_picker_f12_single_dispatch(self):
        dialog=build_keyboard_manager(self.host)
        def keys():
            QTest.keyClick(dialog.canvas,Qt.Key.Key_F12)
            APP.sendEvent(dialog.canvas,QKeyEvent(QKeyEvent.Type.KeyPress,Qt.Key.Key_F12,
                                                Qt.KeyboardModifier.NoModifier,'',True))
            picker=BindingPicker(dialog,'F10')
            def child_keys():
                QTest.keyClick(picker.search,Qt.Key.Key_F12);picker.reject()
            QTimer.singleShot(30,child_keys);picker.exec();picker.deleteLater()
            dialog.reject()
        QTimer.singleShot(250,keys);dialog.exec()
        self.assertEqual(self.host.backend.trigger_estop.call_count,2)
        self.host.backend.arm_match.assert_not_called()
        dialog.deleteLater();APP.processEvents()

    def test_surface_entrance_and_reverse_only_capture_once_each(self):
        dialog=JelloDialog(self.host);dialog.resize(500,400);dialog.show()
        QTest.qWait(80);self.assertFalse(dialog.panel.isVisible())
        self.assertEqual(dialog.capture_count,1)
        dialog.reject();QTest.qWait(160)
        self.assertFalse(dialog.isVisible());self.assertEqual(dialog.capture_count,1)
        dialog.show();QTest.qWait(490)
        self.assertTrue(dialog.panel.isVisible());self.assertEqual(dialog.capture_count,2)
        dialog.reject();QTest.qWait(160);self.assertEqual(dialog.capture_count,3)
        dialog.deleteLater();APP.processEvents()

    def test_jello_checkbox_90_70ms_color_and_rounding(self):
        box=JelloCheckBox();box.resize(100,40);box.show()
        box.setChecked(True);self.assertEqual((box.motion.forward_duration,box.motion.reverse_duration),(.09,.07))
        QTest.qWait(120);self.assertEqual(box.motion.progress,1)
        image=box.grab().toImage();self.assertTrue(any(image.pixelColor(x,20).blue()>180 and image.pixelColor(x,20).red()<100 for x in range(30)))
        box.setChecked(False);QTest.qWait(100);self.assertEqual(box.motion.progress,0)
        box.deleteLater();APP.processEvents()

    def test_binding_button_opens_keyboard_not_old_one_key_overlay(self):
        dialog=build_settings_center(self.host)
        button=ready_page(dialog,'键盘 / 鼠标').module_bind_controls['TacticalMap']
        with patch('rm_client.ui.jello_keybinds.open_keyboard_manager') as open_:
            button.button.click()
        open_.assert_called_once_with(self.host,dialog.pages['键盘 / 鼠标'],('module','TacticalMap'))
        dialog.deleteLater();APP.processEvents()

    def test_dropdown_220ms_reopen_does_not_inherit_short_close(self):
        combo=JelloComboBox();combo.addItems(['First','Second','Third']);combo.resize(200,40);combo.show()
        # Drive the real Qt animation's clock, not the host scheduler: a busy
        # event loop may deliver the final timer tick after qWait(240) returns.
        def finish_motion():
            combo._drop_motion.setCurrentTime(combo._drop_motion.duration())
            APP.processEvents()
        combo.showPopup();self.assertEqual(combo._drop_motion.duration(),220);finish_motion()
        self.assertEqual(combo._drop_progress,1)
        combo.hidePopup();finish_motion()
        self.assertIsNone(combo._popup)
        combo.showPopup();combo._drop_motion.setCurrentTime(40);combo.hidePopup();finish_motion()
        combo.showPopup();self.assertEqual(combo._drop_motion.duration(),220)
        combo.hidePopup();finish_motion();combo.deleteLater();APP.processEvents()

    def test_white_card_corners_and_margin_keep_elastic_overshoot_inside_window(self):
        dialog=JelloDialog(self.host);dialog.resize(1240,840);dialog.show();QTest.qWait(490)
        self.assertEqual(dialog.grab().toImage().pixelColor(0,0).alpha(),0)
        rect=dialog.panel.geometry()
        peak=max(JelloMainTimeline.elastic(i/1000)*.5+.5 for i in range(1001))
        self.assertLessEqual(rect.width()*peak+40,dialog.width())
        self.assertLessEqual(rect.height()*peak+40,dialog.height())
        self.close_dialog(dialog)


if __name__=='__main__':unittest.main()
