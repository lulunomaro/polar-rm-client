"""Closed GUI geometry must not retain wheel input ownership between paints."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_clickgui_background_dismiss import Host, APP
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QWheelEvent


class HiddenGuiWheelTests(unittest.TestCase):
    def setUp(self):
        self.w = Host()
        self.w.resize(640, 400)
        self.w.build()

    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()
        APP.processEvents()

    def wheel(self, rect):
        event = QWheelEvent(rect.center(), rect.center(), QPoint(), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        self.w.wheelEvent(event)

    def test_closed_jello_viewports_cannot_scroll_before_next_paint(self):
        viewport = next(v for v in self.w.gui_scroll_viewports
                        if v["mode"] == "jello_panel" and v["maximum"] > 0)
        self.wheel(viewport["rect"])
        before = self.w.jello_state.panel_scroll[viewport["key"]]
        self.assertEqual(before, 35.)
        self.w.set_clickgui_visible(False)
        self.assertTrue(self.w.gui_scroll_viewports)  # deliberately no intervening build
        self.wheel(viewport["rect"])
        self.assertEqual(self.w.jello_state.panel_scroll[viewport["key"]], before)
        self.w.control.add_wheel.assert_not_called()  # fade-out still owns combat gate

    def test_hidden_cached_geometry_does_not_swallow_allowed_combat_wheel(self):
        viewport = next(v for v in self.w.gui_scroll_viewports
                        if v["mode"] == "jello_panel" and v["maximum"] > 0)
        self.w.set_clickgui_visible(False)
        self.w.combat_control_input_allowed = lambda: True
        self.w.get_setting_number = lambda *args: 1.
        self.wheel(viewport["rect"])
        self.assertEqual(self.w.jello_state.panel_scroll[viewport["key"]], 0.)
        self.w.control.add_wheel.assert_called_once_with(-120.)
        self.w.control.send_from_state.assert_called_once_with(self.w.state, self.w.registry)

    def test_closed_polar_settings_rect_does_not_scroll(self):
        self.w.active_clickgui_mode = lambda: "PolarPanel"
        self.w.panel_modules_rect = QRectF()
        self.w.panel_settings_rect = QRectF(10, 10, 100, 100)
        self.w.selected_module = self.w.registry.get("RobotLink")
        key = self.w.selected_module.name
        self.w.settings_scroll = {key: 0.}
        self.wheel(self.w.panel_settings_rect)
        self.assertEqual(self.w.settings_scroll[key], 28.)
        self.w.set_clickgui_visible(False, play_sound=False)
        self.wheel(self.w.panel_settings_rect)
        self.assertEqual(self.w.settings_scroll[key], 28.)
        self.w.control.add_wheel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
