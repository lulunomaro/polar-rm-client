"""下拉列表延迟显示、选择及取消；兼容 offscreen 和真实 Windows Qt。"""
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QTimer, QEvent, QPointF
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QVBoxLayout, QWidget
from rm_client.ui.endfield.settings.widgets import EndfieldComboBox, SettingsTabBar

APP = QApplication.instance() or QApplication([])


class DropdownTests(unittest.TestCase):
    def setUp(self):
        self.host = QWidget()
        self.host.resize(500, 300)
        layout = QVBoxLayout(self.host)
        self.combo = EndfieldComboBox()
        self.combo.addItems(("First", "Second", "Third"))
        layout.addWidget(self.combo)
        layout.addStretch()
        self.host.show()
        QTest.qWait(30)

    def tearDown(self):
        self.host.hide()
        self.host.deleteLater()
        APP.processEvents()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def assert_open(self):
        popup = self.combo.view().window()
        self.wait_until(lambda: popup.isVisible() and popup.windowOpacity() > .99
                        and not self.combo._popup_timer.isActive())
        self.assertGreater(popup.height(), 30)
        return popup

    def test_delayed_native_show_starts_animation_when_list_becomes_visible(self):
        popup = self.combo.view().window()
        popup.resize(300, 120)
        # 用真实的异步 Show 重现 Qt 原生效果，不能只验证 isVisible。
        with patch.object(QComboBox, "showPopup",
                          side_effect=lambda: QTimer.singleShot(60, popup, popup.show)):
            self.combo.showPopup()
        self.assertFalse(popup.isVisible())
        self.assert_open()
        self.combo.hidePopup()
        self.wait_until(lambda: not popup.isVisible())
        self.assertFalse(self.combo._popup_timer.isActive())

    def test_mouse_selection_and_escape_allow_reopening(self):
        for _ in range(2):
            QTest.mouseClick(self.combo, Qt.MouseButton.LeftButton)
            popup = self.assert_open()
            # 等待 Qt 防止展开点击穿透到列表的双击保护期。
            QTest.qWait(APP.doubleClickInterval())
            view = self.combo.view()
            index = self.combo.model().index(1, 0)
            QTest.mouseMove(view.viewport(), view.visualRect(index).center())
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                             pos=view.visualRect(index).center())
            self.wait_until(lambda: not popup.isVisible())
            self.assertEqual(self.combo.currentIndex(), 1)
            self.assertFalse(self.combo._popup_timer.isActive())
            QTest.mouseClick(self.combo, Qt.MouseButton.LeftButton)
            self.assert_open()
            QTest.keyClick(view, Qt.Key.Key_Escape)
            self.wait_until(lambda: not popup.isVisible())
            self.assertEqual(self.combo.currentIndex(), 1)

    def test_hiding_during_entrance_restores_next_open(self):
        self.combo.showPopup()
        popup = self.combo.view().window()
        self.wait_until(popup.isVisible)
        self.combo.hidePopup()
        self.wait_until(lambda: not popup.isVisible())
        self.assertFalse(self.combo._popup_timer.isActive())
        self.combo.showPopup()
        self.assert_open()

    def test_open_uses_one_animation_and_restores_native_effect_setting(self):
        original = APP.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo)
        try:
            for native_effect in (True, False):
                with self.subTest(native_effect=native_effect):
                    APP.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, native_effect)
                    enabled = APP.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo)
                    self.combo.showPopup()
                    popup = self.combo.view().window()
                    self.assertEqual(APP.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo), enabled)
                    self.assertTrue(popup.isVisible())
                    self.assertFalse(any(w.metaObject().className() == "QRollEffect"
                                         and w.isVisible() for w in APP.topLevelWidgets()))
                    self.assertLess(popup.windowOpacity(), .1)
                    self.assertLess(popup.height(), self.combo._popup_rect.height())
                    self.assert_open()
                    self.combo.hidePopup()
                    self.wait_until(lambda: not popup.isVisible())
        finally:
            APP.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, original)

    def test_popup_grab_does_not_consume_first_tab_click(self):
        bar = SettingsTabBar(self.host)
        bar.addTab("Video")
        bar.addTab("Audio")
        self.host.layout().insertWidget(0, bar)
        QTest.qWait(30)
        self.combo.showPopup()
        popup = self.assert_open()
        changed = []
        bar.currentChanged.connect(changed.append)
        global_point = bar.mapToGlobal(bar.tabRect(1).center())
        # 原生 Qt.Popup 会把弹窗外的点击发给弹窗，而不是直接发给分类栏。
        press = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPointF(popup.mapFromGlobal(global_point)), QPointF(global_point),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        APP.sendEvent(popup, press)
        self.assertEqual(bar.currentIndex(), 1)
        self.assertEqual(changed, [1])
        self.assertFalse(popup.isVisible())
        self.assertIsNone(APP.activePopupWidget())


if __name__ == "__main__":
    unittest.main()
