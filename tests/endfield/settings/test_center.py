"""所有分类、源包完整性、小窗口及嵌套键位草稿的真实Qt验证。"""
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from PySide6.QtCore import Qt, QEventLoop, QTimer, QPoint, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox
from test_cs2_settings_center import APP, Host, ready_page
from rm_client.ui.cs2_settings_center import build_settings_center
from rm_client.ui.endfield.settings.resources import ASSETS, source
from rm_client.ui.endfield.settings.shell import EndfieldSettingsDialog
from rm_client.ui.endfield.settings.widgets import EndfieldCheckBox, EndfieldComboBox, SettingsRow
from rm_client.ui.jello_keybinds import build_keyboard_manager
from rm_client.ui.jello_widgets import JelloCheckBox
from tools.endfield.settings.check_assets import validate


class EndfieldSettingsTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.dialog = build_settings_center(self.host)

    def tearDown(self):
        self.dialog.hide()
        self.dialog.deleteLater()
        self.host.deleteLater()
        APP.processEvents()

    def test_every_category_uses_endfield_controls_and_retains_live_identity(self):
        self.assertIsInstance(self.dialog, EndfieldSettingsDialog)
        for name in ("音频", "游戏", "键盘 / 鼠标", "准星", "界面 / HUD"):
            page = ready_page(self.dialog, name)
            self.assertTrue(page.findChildren(SettingsRow), name)
            for combo in page.findChildren(QComboBox):
                self.assertIsInstance(combo, EndfieldComboBox)
            self.assertFalse(page.findChildren(JelloCheckBox))
        self.host.auto_save_config.assert_not_called()
        self.host.backend.arm_match.assert_not_called()

    def test_video_rows_fill_wide_page_and_remain_readable_in_small_window(self):
        self.dialog.show()
        for width, height in ((1920,1080), (1000,620), (1280,720)):
            self.dialog.resize(width,height)
            QTest.qWait(500)
            video = self.dialog.tabs.widget(0)
            rows = video.findChildren(SettingsRow)
            self.assertGreater(len(rows), 8)
            for row in rows:
                self.assertGreater(row.width(), width*.75)
                self.assertGreater(row.label.width(), 100)
                self.assertLessEqual(row.control.geometry().right(), row.width())

    def test_first_modal_open_inside_running_loop_keeps_snapshot_and_controls_aligned(self):
        """与生产入口一样，先进入主循环，再构造并exec，不能预先resize或清理DeferredDelete。"""
        self.host.resize(1225,704)
        self.host.show()
        loop = QEventLoop()
        failures, samples = [], []

        def enter():
            dialog = build_settings_center(self.host)
            def inspect(close=False):
                try:
                    if close:
                        from time import monotonic
                        deadline = monotonic()+3
                        while dialog._surface is not None and monotonic()<deadline:
                            QTest.qWait(10)
                    video = dialog.tabs.widget(0)
                    rows = video.findChildren(SettingsRow)
                    self.assertEqual(len(rows),12)  # Includes the new rendering-adapter row.
                    self.assertTrue(any(row.control.objectName()=='RenderAdapter' for row in rows))
                    self.assertEqual(dialog.size(),self.host.size())
                    for row in rows:
                        self.assertGreater(row.width(),video.width()*.9,row.label.text())
                        self.assertTrue(row.rect().contains(row.control.geometry()),row.label.text())
                        self.assertGreater(row.label.width(),100)
                    close_rect = QRect(dialog.close_button.mapTo(dialog,QPoint()),dialog.close_button.size())
                    self.assertTrue(dialog.rect().contains(close_rect))
                    samples.append(dialog._surface is not None)
                except BaseException as error:
                    failures.append(error)
                finally:
                    if close:
                        dialog.reject()
            QTimer.singleShot(100,dialog,inspect)
            QTimer.singleShot(600,dialog,lambda: inspect(True))
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()
                loop.quit()
        QTimer.singleShot(0,enter)
        loop.exec()
        if failures:
            raise failures[0]
        self.assertEqual(samples,[True,False])

    def test_search_hides_whole_rows_including_material_background(self):
        page = ready_page(self.dialog,"键盘 / 鼠标")
        self.dialog.search.setText("鼠标灵敏度")
        found = page.controls[("RobotLink","Mouse Sensitivity")]
        hidden = page.controls[("RobotLink","Bind W")]
        self.assertFalse(found.parentWidget().isHidden())
        self.assertTrue(hidden.parentWidget().isHidden())

    def test_new_key_dialog_stages_without_saving_or_arming_and_f12_still_works(self):
        page = ready_page(self.dialog,"键盘 / 鼠标")
        editor = build_keyboard_manager(self.host,page,parent=self.dialog)
        try:
            self.assertIsInstance(editor,EndfieldSettingsDialog)
            editor.show()
            combo = editor.binding_controls[("module","TacticalMap")]
            combo.setCurrentText("F10")
            self.assertEqual(editor.session.values[("module","TacticalMap")],"F10")
            self.assertEqual(self.host.registry.get("TacticalMap").keybind,"Tab")
            QTest.keyClick(editor,Qt.Key.Key_F12)
            self.host.backend.trigger_estop.assert_called_once()
            editor.session.apply()
            self.assertEqual(page.module_bind_controls["TacticalMap"].value,"F10")
            self.assertEqual(self.host.registry.get("TacticalMap").keybind,"Tab")
            self.host.auto_save_config.assert_not_called()
            self.host.backend.arm_match.assert_not_called()
        finally:
            editor.hide()
            editor.deleteLater()

    def test_packed_assets_validate_without_game_or_extraction_dependencies(self):
        self.assertGreaterEqual(validate(ASSETS),18)
        import json
        provenance = json.loads((ASSETS/"PROVENANCE.json").read_text(encoding="utf-8"))
        self.assertEqual(len(provenance["verified_game_bundles"]),20)
        self.assertEqual(len(source()["clips"]),10)


if __name__ == "__main__":
    unittest.main()
