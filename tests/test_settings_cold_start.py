"""First-open work ownership, bounded loading and real Qt draft regressions."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from test_cs2_settings_center import APP, Host, ready_page
from rm_client.ui.cs2_settings_center import build_settings_center, CrosshairSettingsPage
from rm_client.ui.settings_preparation import SettingsBuildQueue
from rm_client.ui.fonts import FontBook
from rm_client.ui import jello_widgets


class ColdSettingsTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.hide(); dialog.deleteLater()
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def dialog(self):
        dialog = build_settings_center(self.host)
        self.dialogs.append(dialog)
        return dialog

    def test_first_open_constructs_only_visible_video_and_empty_shells(self):
        dialog = self.dialog()
        self.assertLess(len(dialog.findChildren(QWidget)), 220)
        for page in dialog.pages.values():
            self.assertFalse(page.controls)
            self.assertFalse(page.builder.timer.isActive())
            self.assertFalse(page.builder.ready)
        self.assertIsNone(dialog.crosshair.editor)
        dialog.show(); QTest.qWait(550)
        self.assertTrue(all(not page.controls for page in dialog.pages.values()))
        self.assertTrue(all(page.builder.slices == 0 for page in dialog.pages.values()))

    def test_rows_yield_and_f12_works_during_loading(self):
        dialog = self.dialog(); dialog.show(); QTest.qWait(480)
        page = dialog.pages['界面 / HUD']
        dialog.tabs.setCurrentWidget(page)
        self.assertFalse(dialog.apply_button.isEnabled())
        QTest.qWait(35)
        self.assertGreater(page.builder.slices, 0)
        self.assertFalse(page.builder.ready)
        QTest.keyClick(dialog.search, Qt.Key.Key_F12)
        self.host.backend.trigger_estop.assert_called_once()
        page = ready_page(dialog, '界面 / HUD')
        self.assertGreater(page.builder.slices, 15)
        self.assertTrue(dialog.apply_button.isEnabled())
        self.host.auto_save_config.assert_not_called()
        self.host.backend.arm_match.assert_not_called()

    def test_hidden_page_pauses_and_revisit_keeps_staged_edits(self):
        dialog = self.dialog(); dialog.show(); QTest.qWait(480)
        page = dialog.pages['界面 / HUD']; dialog.tabs.setCurrentWidget(page)
        QTest.qWait(35)
        dialog.tabs.setCurrentIndex(0)
        count = len(page.controls); slices = page.builder.slices
        QTest.qWait(80)
        self.assertEqual((len(page.controls), page.builder.slices), (count, slices))
        page = ready_page(dialog, '界面 / HUD')
        control = page.controls[('ClickGui', 'Scale')]
        control.spin.setValue(1.25)
        dialog.tabs.setCurrentIndex(0); dialog.tabs.setCurrentWidget(page)
        self.assertIs(page.controls[('ClickGui', 'Scale')], control)
        self.assertEqual(control.spin.value(), 1.25)
        self.assertNotEqual(self.host.registry.get('ClickGui').get_setting('Scale').value, 1.25)

    def test_search_before_load_and_cancel_do_not_apply_or_reuse_drafts(self):
        dialog = self.dialog(); dialog.search.setText('鼠标灵敏度')
        page = ready_page(dialog, '键盘 / 鼠标')
        sensitivity = page.controls[('RobotLink', 'Mouse Sensitivity')]
        sensitivity.spin.setValue(1.75)
        self.assertFalse(sensitivity.isHidden())
        self.assertTrue(page.controls[('RobotLink', 'Bind W')].isHidden())
        dialog.reject(); QTest.qWait(180)
        self.assertTrue(all(page.builder.cancelled for page in dialog.pages.values()))
        fresh = ready_page(self.dialog(), '键盘 / 鼠标')
        self.assertEqual(fresh.controls[('RobotLink', 'Mouse Sensitivity')].spin.value(), 1.0)
        self.host.auto_save_config.assert_not_called()

    def test_crosshair_stages_keep_draft_and_only_explicit_apply_writes(self):
        dialog = self.dialog()
        page = ready_page(dialog, '准星')
        self.assertGreaterEqual(page.builder.slices, 2)
        editor = page.editor
        self.assertIsNotNone(editor)
        with patch.object(editor, 'apply') as apply:
            dialog.tabs.setCurrentIndex(0); dialog.tabs.setCurrentWidget(page)
            self.assertIs(page.editor, editor)
            apply.assert_not_called()
            dialog.apply_button.click()
            apply.assert_called_once()

    def test_cancel_during_load_stops_future_rows_and_keeps_live_values(self):
        dialog = self.dialog(); dialog.show(); QTest.qWait(480)
        page = dialog.pages['界面 / HUD']; dialog.tabs.setCurrentWidget(page)
        QTest.qWait(30); dialog.reject(); QTest.qWait(180)
        count = len(page.controls)
        QTest.qWait(100)
        self.assertEqual(len(page.controls), count)
        self.assertFalse(page.builder.timer.isActive())
        self.assertFalse(page.builder.ready)
        self.host.auto_save_config.assert_not_called()


class QueueTests(unittest.TestCase):
    def test_at_most_four_steps_per_turn_and_pause_keeps_iterator(self):
        owner = QWidget(); owner.show(); APP.processEvents()
        values = []
        def steps():
            for i in range(12):
                values.append(i); yield
        queue = SettingsBuildQueue(owner, steps()); queue.active = True
        with patch('rm_client.ui.settings_preparation.time.perf_counter', return_value=1):
            queue._run_slice()
        queue.set_active(False)
        self.assertEqual(values, list(range(4)))
        queue._run_slice(); self.assertEqual(len(values), 4)
        queue.active = True
        queue._run_slice(); queue.cancel()
        self.assertGreater(len(values), 4)
        self.assertFalse(queue.timer.isActive())
        owner.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_explicit_stage_boundary_yields_even_on_a_fast_clock(self):
        owner = QWidget(); owner.show(); APP.processEvents()
        values = []
        def steps():
            for i in range(3):
                values.append(i); yield True
        queue = SettingsBuildQueue(owner, steps()); queue.active = True
        with patch('rm_client.ui.settings_preparation.time.perf_counter', return_value=1):
            for expected in range(1, 4):
                queue._run_slice()
                self.assertEqual(len(values), expected)
        queue.cancel(); owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_hidden_owner_pauses_without_consuming_a_step(self):
        owner = QWidget(); values = []
        def steps():
            values.append(1); yield
        queue = SettingsBuildQueue(owner, steps()); queue.active = True
        queue._run_slice()
        self.assertEqual(values, [])
        self.assertEqual(queue.slices, 0)
        queue.cancel(); owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_crosshair_cancel_before_attachment_releases_temporary_editor(self):
        from shiboken6 import isValid
        host = Host(); page = CrosshairSettingsPage(host)
        next(page.builder.steps)  # Real editor exists but is not adopted yet.
        editor = page.editor
        self.assertTrue(isValid(editor))
        self.assertIs(editor.parent(), host)
        page.builder.cancel(); page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(editor))
        host.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_failure_stops_queue_and_reports_error(self):
        owner = QWidget(); owner.show(); APP.processEvents()
        def steps():
            yield
            raise RuntimeError('test construction failure')
        queue = SettingsBuildQueue(owner, steps()); queue.active = True
        queue._run_slice()
        self.assertFalse(queue.ready)
        self.assertIn('test construction failure', queue.error)
        self.assertTrue(queue.cancelled)
        self.assertFalse(queue.timer.isActive())
        owner.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class FontDiscoveryTests(unittest.TestCase):
    def test_missing_font_never_walks_unrelated_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root/'recordings').mkdir(); (root/'recordings'/'missing.ttf').write_bytes(b'not a font')
            font_root = root/'assets'/'fonts'; font_root.mkdir(parents=True)
            (font_root/'zenicon-Regular.ttf').write_bytes(b'not a font')
            book = FontBook()
            with patch.object(Path, 'rglob', side_effect=AssertionError('unbounded scan')):
                self.assertFalse(book._find_font(root, 'missing.ttf').exists())
                self.assertEqual(book._find_font(root, '*icon-Regular.ttf'), font_root/'zenicon-Regular.ttf')

    def test_native_settings_reuses_loaded_window_bank(self):
        book = FontBook(); book.loaded['jello_light'] = 'Already registered Jello'
        with patch.object(jello_widgets, '_book', None), patch.object(FontBook, 'load_from_project_assets', side_effect=AssertionError('duplicate font registration')):
            jello_widgets.use_jello_font_book(book)
            font = jello_widgets.jello_font()
            self.assertEqual(font.family(), 'Already registered Jello')
            self.assertIs(jello_widgets._book, book)


if __name__ == '__main__':
    unittest.main()
