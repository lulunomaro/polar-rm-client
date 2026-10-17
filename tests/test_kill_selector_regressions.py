"""Offscreen Qt regression checks; no audio devices or robot output."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from types import SimpleNamespace
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QWheelEvent
from PySide6.QtWidgets import QApplication
from rm_client.model import build_default_registry
from rm_client.ui.jello_clickgui import JelloClickGuiRenderer, JelloClickGuiState, _DropdownRequest
from rm_client.ui.clickgui_gpu import ClickGuiDrawList
from rm_client.ui.polar.mixins.input_dispatch_mixin import InputDispatchMixin

APP = QApplication.instance() or QApplication([])

class Host(InputDispatchMixin):
    wheelEvent = InputDispatchMixin.wheelEvent
    def __init__(self):
        self.registry = build_default_registry()
        self.jello_state = JelloClickGuiState()
        self.hover_point = QPointF(-1, -1)
        self.gui_scroll_viewports = []
        self.hotspots = []
        self.student_ui = SimpleNamespace(visible=False)
        self.match_chat = SimpleNamespace(input_open=False)
        self.economy_visible = False
        self.escape_menu_visible = False
        self.frame_scheduler = SimpleNamespace(force=lambda: None)
    def font(self, key, size):
        font = QFont('Sans Serif'); font.setPointSizeF(size); return font
    def add_hotspot(self, rect, action, payload, **kwargs):
        self.hotspots.append((rect, action, payload))
    def active_clickgui_mode(self): return 'Jello'
    def combat_control_input_allowed(self): raise AssertionError('wheel leaked to combat')

class DropdownTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.renderer = JelloClickGuiRenderer(Path('/nonexistent-jello-assets'))
        self.setting = self.host.registry.get('EliminationFX').get_setting('Preset')
        self.host.jello_state.toggle_dropdown('EliminationFX:Preset')
        self.host.jello_state.dropdown_animation.reset(1.0)
    def draw(self, upwards=False):
        self.host.hotspots = []
        self.host.gui_scroll_viewports = [dict(mode='jello_settings', key='EliminationFX', maximum=900, rect=QRectF(0,0,200,240))]
        rect = QRectF(10, 205 if upwards else 10, 123, 27)
        self.renderer._build_dropdown_popup(self.host, self.host.jello_state, ClickGuiDrawList(),
            _DropdownRequest('EliminationFX:Preset', self.setting, rect, QRectF(0,0,200,240), 1, 1, upwards))
        return self.host.gui_scroll_viewports[-1]
    def wheel(self, angle=0, pixel=0):
        rect = self.host.gui_scroll_viewports[-1]['rect']
        event = QWheelEvent(rect.center(), rect.center(), QPoint(0,pixel), QPoint(0,angle), Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
        self.host.wheelEvent(event)
        self.assertTrue(event.isAccepted())
    def test_all_presets_reachable_both_directions(self):
        for upwards in (False, True):
            self.host.jello_state.dropdown_scroll = 0
            viewport = self.draw(upwards)
            self.assertEqual(viewport['mode'], 'jello_dropdown')
            self.assertGreater(viewport['maximum'], 0)
            self.assertEqual(self.host.hotspots[0][2][1], self.setting.options[0])
            self.wheel(angle=-12000)
            self.assertEqual(self.host.jello_state.dropdown_scroll, viewport['maximum'])
            self.draw(upwards)
            self.assertEqual(self.host.hotspots[-1][2][1], self.setting.options[-1])
            for rect, _, _ in self.host.hotspots:
                self.assertEqual(rect.intersected(viewport['rect']), rect)
            self.wheel(angle=12000)
            self.assertEqual(self.host.jello_state.dropdown_scroll, 0)
            self.assertEqual(self.host.jello_state.settings_scroll, {})
    def test_touchpad_and_reopen_reset(self):
        self.draw(); self.wheel(pixel=-41)
        self.assertEqual(self.host.jello_state.dropdown_scroll, 41)
        self.host.jello_state.close_dropdown()
        self.host.jello_state.toggle_dropdown('EliminationFX:Preset')
        self.assertEqual(self.host.jello_state.dropdown_scroll, 0)
    def test_small_list_has_zero_bounds(self):
        self.setting.options = ['one', 'two']
        viewport = self.draw()
        self.assertEqual(viewport['maximum'], 0)
        self.wheel(angle=-120)
        self.assertEqual(self.host.jello_state.dropdown_scroll, 0)
        self.assertEqual(self.host.jello_state.settings_scroll, {})

if __name__ == '__main__': unittest.main()
