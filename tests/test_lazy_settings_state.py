"""Real-Qt lazy loading, interruption and independent draft regression tests."""
import os, unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('POLAR_DISABLE_MEDIA_PRELOAD','1')
from PySide6.QtCore import Qt,QCoreApplication,QEvent,QTimer
from PySide6.QtWidgets import QPushButton,QMessageBox,QInputDialog
from PySide6.QtTest import QTest
from shiboken6 import isValid
from test_cs2_settings_center import APP,Host,ready_page
from rm_client.ui import cs2_settings_center as settings


def flush():
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    APP.processEvents()


class Base(unittest.TestCase):
    def setUp(self):
        self.host=Host();self.dialogs=[]
    def tearDown(self):
        for d in self.dialogs:
            if isValid(d): d.hide();d.deleteLater()
        self.host.deleteLater();flush();flush()
    def dialog(self):
        d=settings.build_settings_center(self.host);self.dialogs.append(d);return d
    def wait_ready(self,d,name): return ready_page(d,name)
    def reset_button(self,d): return next(b for b in d.findChildren(QPushButton) if b.text()=='恢复当前页默认值')


class LifecycleAudit(Base):
    def test_every_incomplete_page_rejects_programmatic_apply(self):
        d=self.dialog()
        for p in [*d.pages.values(),d.crosshair]:
            with self.subTest(page=p):
                with self.assertRaises(ValueError): p.apply_draft()
                p.reset_draft()
        self.host.auto_save_config.assert_not_called()
        self.host.backend.lock_control.assert_not_called()

    def test_rapid_tabs_only_current_owns_work_and_buttons(self):
        d=self.dialog();d.show();QTest.qWait(480)
        order=[5,1,3,4,2,0,3,2,5,4,0]*3
        for index in order:
            d.tabs.setCurrentIndex(index);QTest.qWait(2)
            current=d.tabs.currentWidget()
            for p in [*d.pages.values(),d.crosshair]:
                self.assertEqual(p.builder.active,p is current)
                if p is not current:self.assertFalse(p.builder.timer.isActive())
            builder=getattr(current,'builder',None)
            self.assertEqual(d.apply_button.isEnabled(),builder is None or builder.ready)
            self.assertEqual(self.reset_button(d).isEnabled(),builder is None or builder.ready)
        for name in ['音频','游戏','键盘 / 鼠标','准星','界面 / HUD']:
            self.wait_ready(d,name)
            self.assertTrue(d.apply_button.isEnabled())
        self.host.auto_save_config.assert_not_called()

    def test_current_apply_never_applies_hidden_draft(self):
        d=self.dialog();hud=self.wait_ready(d,'界面 / HUD')
        hud.controls[('ClickGui','Scale')].spin.setValue(1.25)
        keys=self.wait_ready(d,'键盘 / 鼠标')
        keys.controls[('RobotLink','Mouse Sensitivity')].spin.setValue(1.75)
        d.apply_button.click()
        self.assertEqual(self.host.registry.get('RobotLink').get_setting('Mouse Sensitivity').value,1.75)
        self.assertEqual(self.host.registry.get('ClickGui').get_setting('Scale').value,1)
        d.tabs.setCurrentWidget(hud)
        self.assertEqual(hud.controls[('ClickGui','Scale')].spin.value(),1.25)
        d.apply_button.click()
        self.assertEqual(self.host.registry.get('ClickGui').get_setting('Scale').value,1.25)
        self.assertEqual(self.host.auto_save_config.call_count,2)

    def test_reset_draft_preserved_without_live_writes_until_apply(self):
        self.host.registry.get('RobotLink').get_setting('Mouse Sensitivity').value=1.8
        d=self.dialog();p=self.wait_ready(d,'键盘 / 鼠标')
        p.controls[('RobotLink','Mouse Sensitivity')].spin.setValue(2.5)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):self.reset_button(d).click()
        self.assertEqual(p.controls[('RobotLink','Mouse Sensitivity')].spin.value(),1)
        self.assertEqual(self.host.registry.get('RobotLink').get_setting('Mouse Sensitivity').value,1.8)
        self.host.auto_save_config.assert_not_called()
        d.tabs.setCurrentIndex(0);d.tabs.setCurrentWidget(p)
        self.assertEqual(p.controls[('RobotLink','Mouse Sensitivity')].spin.value(),1)
        d.apply_button.click()
        self.assertEqual(self.host.registry.get('RobotLink').get_setting('Mouse Sensitivity').value,1)

    def test_latest_search_wins_mid_build_and_across_pauses(self):
        d=self.dialog();d.show();QTest.qWait(480)
        d.search.setText('no matching field');p=d.pages['界面 / HUD'];d.tabs.setCurrentWidget(p);QTest.qWait(30)
        d.search.setText('Scale');d.tabs.setCurrentIndex(0);d.search.setText('Jello List Sound')
        self.wait_ready(d,'界面 / HUD')
        self.assertFalse(p.controls[('HUD','Jello List Sound')].isHidden())
        self.assertTrue(p.controls[('ClickGui','Scale')].isHidden())
        d.search.clear()
        self.assertTrue(all(not w.isHidden() for w in p.controls.values()))

    def test_dismiss_before_entrance_cancels_all_work_and_reopen_is_fresh(self):
        d=self.dialog();d.tabs.setCurrentWidget(d.pages['界面 / HUD']);d.show();d.reject();QTest.qWait(200)
        self.assertFalse(d.isVisible())
        for p in [*d.pages.values(),d.crosshair]:
            self.assertTrue(p.builder.cancelled);self.assertFalse(p.builder.timer.isActive())
        fresh=self.dialog();p=self.wait_ready(fresh,'界面 / HUD')
        self.assertTrue(p.builder.ready);self.assertFalse(p.builder.cancelled)
        self.host.auto_save_config.assert_not_called()

    def test_repeated_cancel_reopen_has_one_f12_route(self):
        for i in range(5):
            d=self.dialog();d.show()
            QTest.keyClick(d.search,Qt.Key.Key_F12)
            d.reject();QTest.qWait(180)
            self.assertEqual(self.host.backend.trigger_estop.call_count,i+1)
        fresh=self.dialog();fresh.show();QTest.qWait(480)
        QTest.keyClick(fresh.search,Qt.Key.Key_F12)
        self.assertEqual(self.host.backend.trigger_estop.call_count,6)
        self.host.backend.arm_match.assert_not_called()

    def test_registry_preparation_failure_isolated_and_reopen_recovers(self):
        d=self.dialog();d.show();QTest.qWait(480)
        p=d.pages['界面 / HUD']
        with patch.object(settings,'NumberControl',side_effect=RuntimeError('injected row failure')):
            d.tabs.setCurrentWidget(p);QTest.qWait(100)
        self.assertIn('injected row failure',p.builder.error)
        self.assertFalse(d.apply_button.isEnabled());self.assertFalse(p.content.isEnabled())
        self.assertFalse(p.builder.timer.isActive());self.assertTrue(p.builder.cancelled)
        self.assertIn('加载失败',p.loading.text())
        QTest.keyClick(d.search,Qt.Key.Key_F12)
        self.host.backend.trigger_estop.assert_called_once()
        d.tabs.setCurrentIndex(0);self.assertTrue(d.apply_button.isEnabled())
        self.host.auto_save_config.assert_not_called()
        d.reject();QTest.qWait(180)
        fresh=self.dialog();self.wait_ready(fresh,'界面 / HUD')

    def test_crosshair_factory_failure_isolated(self):
        d=self.dialog();d.show();QTest.qWait(480)
        with patch('rm_client.cs2_crosshair.build_crosshair_editor',side_effect=RuntimeError('injected editor failure')):
            d.tabs.setCurrentWidget(d.crosshair);QTest.qWait(100)
        p=d.crosshair
        self.assertIn('injected editor failure',p.builder.error)
        self.assertIsNone(p.editor);self.assertFalse(d.apply_button.isEnabled())
        QTest.keyClick(d.search,Qt.Key.Key_F12);self.host.backend.trigger_estop.assert_called_once()
        self.host.auto_save_config.assert_not_called()
        d.tabs.setCurrentIndex(0);self.assertTrue(d.apply_button.isEnabled())

    def test_crosshair_styling_failure_releases_temporary_editor(self):
        d=self.dialog();d.show();QTest.qWait(480)
        original=settings.apply_jello_widgets
        def fail_editor(w,*a,**kw):
            if w.objectName()=='cs2_crosshair_editor':raise RuntimeError('injected style failure')
            return original(w,*a,**kw)
        with patch.object(settings,'apply_jello_widgets',side_effect=fail_editor):
            d.tabs.setCurrentWidget(d.crosshair);QTest.qWait(200)
        p=d.crosshair;editor=p.editor
        self.assertIn('injected style failure',p.builder.error)
        self.assertTrue(isValid(editor));self.assertFalse(editor.isVisible());self.assertIs(editor.parent(),self.host)
        self.assertFalse(d.apply_button.isEnabled())
        d.reject();QTest.qWait(180);d.deleteLater();flush();flush()
        self.assertFalse(isValid(editor))
        self.host.auto_save_config.assert_not_called()

    def test_close_cancel_return_each_stop_partial_loading(self):
        for method in ['close','reject','accept']:
            with self.subTest(method=method):
                d=self.dialog();d.show();QTest.qWait(480)
                p=d.pages['界面 / HUD'];d.tabs.setCurrentWidget(p);QTest.qWait(25)
                getattr(d,method)();QTest.qWait(180);count=len(p.controls)
                QTest.qWait(40)
                self.assertFalse(d.isVisible());self.assertTrue(p.builder.cancelled)
                self.assertFalse(p.builder.timer.isActive());self.assertEqual(len(p.controls),count)
        self.host.auto_save_config.assert_not_called()

    def test_delete_during_loading_removes_timer_and_partial_editor(self):
        for name in ['界面 / HUD','准星']:
            d=self.dialog();d.show();QTest.qWait(480)
            p=d.pages[name] if name in d.pages else d.crosshair
            d.tabs.setCurrentWidget(p);QTest.qWait(15)
            timer=p.builder.timer;editor=getattr(p,'editor',None)
            d.deleteLater();flush();flush();QTest.qWait(30)
            self.assertFalse(isValid(timer))
            if editor:self.assertFalse(isValid(editor))
        self.host.auto_save_config.assert_not_called()


class DraftIsolationTests(Base):
    def test_unedited_hud_duplicate_must_not_overwrite_audio_change(self):
        d=self.dialog();hud=self.wait_ready(d,'界面 / HUD');audio=self.wait_ready(d,'音频')
        audio.controls[('HUD','Jello List Sound')].setChecked(False)
        with patch('rm_client.audio_preferences.save_audio'):d.apply_button.click()
        self.assertFalse(self.host.registry.get('HUD').get_setting('Jello List Sound').value)
        d.tabs.setCurrentWidget(hud);hud.controls[('ClickGui','Scale')].spin.setValue(1.25);d.apply_button.click()
        self.assertFalse(self.host.registry.get('HUD').get_setting('Jello List Sound').value)

    def test_crosshair_reset_includes_display_scale(self):
        self.host.registry.get('Crosshair').get_setting('Crosshair Scale').value=3
        d=self.dialog();p=self.wait_ready(d,'准星');p.reset_draft()
        self.assertEqual(p.editor.display_scale.value(),1)
        self.assertEqual(self.host.registry.get('Crosshair').get_setting('Crosshair Scale').value,3)

    def test_dirty_shared_field_survives_revisit(self):
        d=self.dialog();hud=self.wait_ready(d,'界面 / HUD')
        hud.controls[('HUD','Jello List Sound')].setChecked(False)
        self.wait_ready(d,'音频');d.tabs.setCurrentWidget(hud)
        self.assertFalse(hud.controls[('HUD','Jello List Sound')].isChecked())
        d.apply_button.click()
        self.assertFalse(self.host.registry.get('HUD').get_setting('Jello List Sound').value)

    def test_clean_control_refresh_does_not_remove_unrelated_draft(self):
        d=self.dialog();hud=self.wait_ready(d,'界面 / HUD')
        hud.controls[('ClickGui','Scale')].spin.setValue(1.25)
        audio=self.wait_ready(d,'音频');audio.controls[('HUD','Jello List Sound')].setChecked(False)
        with patch('rm_client.audio_preferences.save_audio'):d.apply_button.click()
        d.tabs.setCurrentWidget(hud)
        self.assertFalse(hud.controls[('HUD','Jello List Sound')].isChecked())
        self.assertEqual(hud.controls[('ClickGui','Scale')].spin.value(),1.25)

    def test_apply_only_dirty_settings_toggles_and_module_binds(self):
        d=self.dialog();hud=self.wait_ready(d,'界面 / HUD')
        self.host.registry.get('HUD').enabled=False
        self.host.registry.get('ClickGui').get_setting('Language').value='English'
        hud.controls[('ClickGui','Scale')].spin.setValue(1.25)
        hud.apply_draft()
        self.assertFalse(self.host.registry.get('HUD').enabled)
        self.assertEqual(self.host.registry.get('ClickGui').get_setting('Language').value,'English')
        keys=self.wait_ready(d,'键盘 / 鼠标')
        self.host.registry.get('TacticalMap').keybind='F10'
        keys.controls[('RobotLink','Mouse Sensitivity')].spin.setValue(1.75)
        keys.apply_draft()
        self.assertEqual(self.host.registry.get('TacticalMap').keybind,'F10')

    def test_applied_draft_becomes_clean_for_later_live_change(self):
        d=self.dialog();hud=self.wait_ready(d,'界面 / HUD')
        hud.controls[('ClickGui','Scale')].spin.setValue(1.25);hud.apply_draft()
        self.host.registry.get('ClickGui').get_setting('Scale').value=1.5
        d.tabs.setCurrentIndex(0);d.tabs.setCurrentWidget(hud)
        self.assertEqual(hud.controls[('ClickGui','Scale')].spin.value(),1.5)

    def test_reset_explicitly_overwrites_later_live_change_even_if_default_equals_snapshot(self):
        d=self.dialog();hud=self.wait_ready(d,'界面 / HUD')
        self.host.registry.get('ClickGui').get_setting('Scale').value=1.5
        hud.reset_draft()
        d.tabs.setCurrentIndex(0);d.tabs.setCurrentWidget(hud)
        self.assertEqual(hud.controls[('ClickGui','Scale')].spin.value(),1)
        self.assertEqual(self.host.registry.get('ClickGui').get_setting('Scale').value,1.5)
        hud.apply_draft()
        self.assertEqual(self.host.registry.get('ClickGui').get_setting('Scale').value,1)

    def test_audio_merges_dirty_fields_with_latest_preferences(self):
        from rm_client.audio_preferences import AudioPreferences
        from dataclasses import replace
        prefs=AudioPreferences(master_volume=70)
        with patch('rm_client.audio_preferences.current_audio',return_value=prefs) as current:
            d=self.dialog();audio=self.wait_ready(d,'音频')
            audio.audio_controls['master_volume'].spin.setValue(40)
            current.return_value=replace(prefs,mute_unfocused=True)
            with patch('rm_client.audio_preferences.save_audio') as save:
                audio.apply_draft()
            self.assertEqual(save.call_args.args[0].master_volume,40)
            self.assertTrue(save.call_args.args[0].mute_unfocused)

    def test_cancelled_reset_does_not_change_live_values(self):
        self.host.registry.get('Crosshair').get_setting('Crosshair Scale').value=3
        d=self.dialog();p=self.wait_ready(d,'准星');p.reset_draft();d.reject();QTest.qWait(180)
        self.assertEqual(self.host.registry.get('Crosshair').get_setting('Crosshair Scale').value,3)
        self.host.auto_save_config.assert_not_called()


if __name__=='__main__':unittest.main()
