"""Inventory destination-DPI text and display-only crosshair size regressions."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('POLAR_DISABLE_MEDIA_PRELOAD', '1')
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget
from rm_client.config_store import apply_registry_config
from rm_client.cs2_crosshair import (apply_profile, build_crosshair_editor, decode_share_code,
    display_scale_from_module, encode_share_code, paint_crosshair, profile_from_module)
from rm_client.model import RobotState, build_default_registry
from rm_client.student_ui import StudentUiState
from rm_client.ui.fonts import cjk_ui_font, ui_text_layout
from rm_client.ui.minecraft_hud import MinecraftCombatHudRenderer
from rm_client.ui.student_panels import StudentPanelRenderer

ROOT = Path(__file__).resolve().parents[1]
APP = QApplication.instance() or QApplication([])
if 'Microsoft YaHei UI' not in QFontDatabase.families():
    path = Path('C:/Windows/Fonts/msyh.ttc')
    if path.is_file(): QFontDatabase.addApplicationFont(str(path))


def canvas(width, height, dpr=1):
    image = QImage(round(width*dpr), round(height*dpr), QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(Qt.transparent)
    return image


class InventoryHost:
    def __init__(self, dpr=1):
        self.registry = build_default_registry()
        self.registry.get('ClickGui').get_setting('Language').value = '简体中文'
        self.state = RobotState()
        self.student_ui = StudentUiState(panel='inventory', opened=True, progress=1)
        self.minecraft_hud = MinecraftCombatHudRenderer(ROOT/'assets/minecraft_hud')
        self.hover_point = QPointF(-100, -100)
        self.hotspots = []
        self.dpr = dpr

    def devicePixelRatioF(self): return self.dpr
    def setting_mode(self, module, key, default):
        return module.get_setting(key).value
    def get_setting_number(self, module, key, default):
        return module.get_setting(key).value
    def add_hotspot(self, rect, action, payload=None, **kwargs):
        self.hotspots.append(NS(rect=rect, action=action, payload=payload, tooltip=kwargs.get('tooltip', '')))


class InventoryReadabilityTests(unittest.TestCase):
    def setUp(self): self.renderer = StudentPanelRenderer(ROOT/'assets/minecraft_hud')

    def test_cjk_is_whole_regular_shaped_line_not_reduced_mc_bitmap(self):
        host = InventoryHost()
        image = canvas(400, 40, 1.25)
        painter = QPainter(image)
        try:
            with patch.object(host.minecraft_hud.styled_font, 'draw') as bitmap:
                self.renderer.text(host, painter, QRectF(0, 0, 400, 40), '技能 / 性能点', 4, 2)
                bitmap.assert_not_called()
                self.renderer.text(host, painter, QRectF(0, 0, 400, 40), 'E / Esc', 4, 2)
                bitmap.assert_called_once()
            font = self.renderer.inventory_font(4, 2)
            self.assertEqual(font.pixelSize(), 16)
            layout = ui_text_layout(font, '技能/性能', image)
            runs = layout.glyphRuns()
            self.assertTrue(runs)
            self.assertTrue(all(g != 0 for run in runs for g in run.glyphIndexes()))
            # The shared shaper prefers installed YaHei/DengXian on Windows
            # and allows the system CJK fallback elsewhere (e.g. Noto on
            # Linux). Verify the actual Chinese runs, not just a font request.
            cjk_runs = [*layout.glyphRuns(0, 2), *layout.glyphRuns(3, 2)]
            installed_cjk = set(QFontDatabase.families(QFontDatabase.WritingSystem.SimplifiedChinese))
            preferred_cjk = set(cjk_ui_font(font).families()) & installed_cjk
            self.assertEqual(sum(len(run.glyphIndexes()) for run in cjk_runs), 4)
            self.assertTrue(all(run.rawFont().familyName() in (preferred_cjk or installed_cjk)
                                for run in cjk_runs))
            self.assertTrue(all(run.rawFont().styleName() == 'Regular' for run in runs))
        finally: painter.end()

    def test_editor_readability_floor_is_bounded_and_does_not_change_hud_preferences(self):
        host = InventoryHost()
        module = host.registry.get('MinecraftCombatHUD')
        module.get_setting('GUI Scale').value = '1x'
        module.get_setting('Scale Multiplier').value = .5
        for width, height in ((640, 480), (1024, 608), (1280, 720), (1600, 900), (1920, 1080), (2560, 1440)):
            rect, scale = self.renderer.geometry(host, width, height)
            self.assertTrue(QRectF(0, 0, width, height).contains(rect))
            self.assertGreaterEqual(scale, 2)
        self.assertEqual(module.get_setting('Scale Multiplier').value, .5)

    def test_native_cache_blit_no_resampling_and_hotspots_follow_common_transform(self):
        for width, height in ((1024, 608), (1280, 720), (1600, 900), (1920, 1080), (2560, 1440)):
            for dpr in (1, 1.25, 1.5, 2):
                with self.subTest(resolution=(width, height), dpr=dpr):
                    host = InventoryHost(dpr)
                    host.registry.get('MinecraftCombatHUD').get_setting('Scale Multiplier').value = 1.05
                    image = canvas(width, height, dpr)
                    painter = QPainter(image)
                    self.renderer.draw(host, painter, width, height)
                    painter.end()
                    panel, scale = self.renderer.geometry(host, width, height)
                    origin = QPointF(round(panel.x()*dpr)/dpr, round(panel.y()*dpr)/dpr)
                    # Opaque crafting label background/text must be byte-identical
                    # to its native cache, rather than a scaled image of text.
                    crop = QRectF(101*scale*dpr, 40*scale*dpr, 65*scale*dpr, 10*scale*dpr).toAlignedRect()
                    actual = crop.translated(round(origin.x()*dpr), round(origin.y()*dpr))
                    self.assertEqual(image.copy(actual), self.renderer._cache_image.copy(crop))
                    slots = [h for h in host.hotspots if h.action == 'student_slot']
                    self.assertEqual(len(slots), 9)
                    for hit, local in zip(slots, [h for h in self.renderer._cache_hits if h.action == 'student_slot']):
                        self.assertEqual(hit.rect, local.rect.translated(origin))
                    builds = self.renderer.rebuilds
                    painter = QPainter(image)
                    self.renderer.draw(host, painter, width, height)
                    painter.end()
                    self.assertEqual(self.renderer.rebuilds, builds)

    def test_tooltip_wraps_stays_in_screen_and_reuses_bounded_layout(self):
        image = canvas(640, 480, 1.5)
        painter = QPainter(image)
        text = '发射性能点（草稿） · 拖拽到下方九格功能栏槽位，不启用模块；右键清空功能栏槽位'
        try:
            for point in (QPointF(0, 0), QPointF(635, 475), QPointF(400, 200)):
                box = self.renderer.inventory_tooltip(painter, text, point, 640, 480)
                self.assertTrue(QRectF(0, 0, 640, 480).contains(box))
            self.assertEqual(len(self.renderer._inventory_tooltips), 1)
            layout, _, _ = next(iter(self.renderer._inventory_tooltips.values()))
            self.assertGreater(layout.lineCount(), 1)
            self.assertEqual(sum(layout.lineAt(i).textLength() for i in range(layout.lineCount())), len(text))
            self.assertTrue(all(g != 0 for run in layout.glyphRuns() for g in run.glyphIndexes()))
            for i in range(40): self.renderer.inventory_tooltip(painter, f'提示 {i}', QPointF(), 640, 480)
            self.assertLessEqual(len(self.renderer._inventory_tooltips), 32)
        finally: painter.end()


class CrosshairDisplayScaleTests(unittest.TestCase):
    def setUp(self): self.module = build_default_registry().get('Crosshair')

    def painted_bounds(self, multiplier, dpr=1):
        self.module.get_setting('Crosshair Scale').value = multiplier
        image = canvas(400, 400, dpr)
        painter = QPainter(image)
        self.assertTrue(paint_crosshair(painter, self.module, 400, 400))
        painter.end()
        points = [(x, y) for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y).alpha()]
        return min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points), max(y for _, y in points)

    def test_length_gap_thickness_outline_and_dot_scale_about_same_center(self):
        for name, value in {'Reference Height': 400, 'Length': 8, 'Gap': 3, 'Thickness': 2,
                            'Center Dot': True, 'Outline Width': 1}.items():
            self.module.get_setting(name).value = value
        for dpr in (1, 1.25, 1.5, 2):
            one = self.painted_bounds(1, dpr)
            two = self.painted_bounds(2, dpr)
            self.assertAlmostEqual(two[2]-two[0]+1, 2*(one[2]-one[0]+1), delta=2)
            self.assertAlmostEqual((two[0]+two[2])/2, 200*dpr-.5, delta=1)

    def test_import_and_export_codes_leave_local_scale_alone(self):
        code = 'CSGO-Cn37R-YE7vo-pLCAL-aURmZ-z6zkG'
        self.module.get_setting('Crosshair Scale').value = 3.5
        apply_profile(self.module, decode_share_code(code))
        self.assertEqual(display_scale_from_module(self.module), 3.5)
        profile = profile_from_module(self.module)
        self.assertNotIn('Crosshair Scale', profile)
        self.assertEqual(encode_share_code(profile), code)
        self.module.get_setting('Crosshair Scale').value = 8
        self.assertEqual(encode_share_code(profile_from_module(self.module)), code)

    def test_preference_persists_and_old_configs_default_to_one(self):
        self.module.get_setting('Crosshair Scale').value = 2.5
        data = {'modules': {'Crosshair': {'settings': {s.name: s.value for s in self.module.settings}}}}
        fresh = build_default_registry()
        apply_registry_config(fresh, json.loads(json.dumps(data)))
        self.assertEqual(display_scale_from_module(fresh.get('Crosshair')), 2.5)
        del data['modules']['Crosshair']['settings']['Crosshair Scale']
        old = build_default_registry()
        apply_registry_config(old, data)
        self.assertEqual(display_scale_from_module(old.get('Crosshair')), 1)
        for bad in (None, 'big', math.nan, math.inf, True, 10**1000):
            self.module.get_setting('Crosshair Scale').value = bad
            self.assertEqual(display_scale_from_module(self.module), 1)

    def test_editor_scale_is_separate_from_preview_zoom_and_protocol_draft(self):
        host = QWidget()
        host.registry = build_default_registry()
        for name in ('invalidate_transient_overlay_scene', 'invalidate_gui_cache', 'auto_save_config', 'toast'):
            setattr(host, name, Mock())
        host.frame_scheduler = Mock()
        editor = build_crosshair_editor(host)
        try:
            original = editor.draft()
            zoom = editor.zoom.value()
            editor.display_scale.setValue(3)
            self.assertEqual(editor.draft(), original)
            self.assertEqual(editor.zoom.value(), zoom)
            editor.import_value('CSGO-LibdP-VCVEd-ESayK-rSivi-2UBtG')
            self.assertEqual(editor.display_scale.value(), 3)
            editor.apply()
            self.assertEqual(display_scale_from_module(host.registry.get('Crosshair')), 3)
            host.auto_save_config.assert_called_once()
        finally:
            editor.deleteLater()
            host.deleteLater()


if __name__ == '__main__': unittest.main()
