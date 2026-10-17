"""Real Qt font/layout/raster regressions; no window, backend or GL startup.

Run: .venv/Scripts/python.exe -X utf8 -m unittest test_ui_cjk_font_readability -v
     (PYTHONIOENCODING=utf-8, PYTHONPATH=.;tests)
Use the native Windows font database, not the offscreen plugin's empty one.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt, QTextBoundaryFinder
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter,
    QTextCharFormat, QTextLayout, QTextOption,
)
from PySide6.QtWidgets import QApplication

from rm_client.ui import fonts as ui
from rm_client.ui.clickgui_gpu import ClickGuiDrawList, ClickGuiGpuRenderer
from rm_client.ui.polar.text_metrics import TextMetricsMixin


def canvas(width=320, height=80, *, dpi=96, dpr=1.0):
    image = QImage(round(width * dpr), round(height * dpr), QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.setDotsPerMeterX(round(dpi / .0254))
    image.setDotsPerMeterY(round(dpi / .0254))
    image.fill(Qt.transparent)
    return image


def qt_line(font, text, device=None, spans=()):
    """Independent Qt shaping: explicit test spans, no production layout cache."""
    layout = QTextLayout(text, font, device)
    option = QTextOption()
    option.setWrapMode(QTextOption.NoWrap)
    option.setUseDesignMetrics(False)
    layout.setTextOption(option)
    ranges = []
    for start, length in spans:
        item = QTextLayout.FormatRange()
        item.start, item.length = start, length
        item.format = QTextCharFormat()
        item.format.setFont(ui.cjk_ui_font(font))
        ranges.append(item)
    layout.setFormats(ranges)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(1e7)
    layout.endLayout()
    return layout


def run_signature(run):
    raw = run.rawFont()
    return raw.familyName(), raw.styleName(), raw.weight(), tuple(run.glyphIndexes())


class Host(TextMetricsMixin):
    def __init__(self, fonts, *, gpu=False):
        self.fonts = fonts
        self._collect_gpu_hud_commands = gpu
        self.clickgui_gpu = NS(available=gpu)
        self._gpu_hud_commands = ClickGuiDrawList()

    def minecraft_hud_active(self):
        return True

    def menu_overlay_active(self):
        return False


class UiCjkFontReadabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # An explicitly requested offscreen test still needs real installed
        # Windows glyphs. Register locally, without downloading anything.
        if os.name == "nt" and "Microsoft YaHei UI" not in QFontDatabase.families():
            path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        ui._ui_fonts._cjk_families_cache = ()
        cls.fonts = ui.FontBook()
        cls.fonts.load_from_project_assets(ROOT)
        cls.font = cls.fonts.font("jello_light", 9)
        if not QFontMetricsF(ui.cjk_ui_font(cls.font)).inFontUcs4(ord("受")):
            raise unittest.SkipTest("No real CJK font: glyph readability not verified")

    def setUp(self):
        ui._ui_layout_cache.clear()
        ui._ui_elide_cache.clear()
        TextMetricsMixin._text_metric_cache.clear()
        TextMetricsMixin._font_height_cache.clear()
        TextMetricsMixin._elide_cache.clear()
        self.host = Host(self.fonts)

    def test_registered_latin_light_and_medium_are_not_replaced(self):
        for key, style, weight in (("jello_light", "Light", QFont.Light),
                                   ("jello_medium", "Medium", QFont.Medium)):
            with self.subTest(key=key):
                self.assertIn(key, self.fonts.loaded)
                font = self.fonts.font(key, 9.125)
                self.assertEqual(font.family(), self.fonts.loaded[key])
                self.assertEqual((font.styleName(), font.weight(), font.pointSizeF()), (style, weight, 9.125))
                original = QFont(font)
                original.setFamily(self.fonts.loaded[key])
                text = "AVATAR ffi HUD 55/70W"
                self.assertEqual([run_signature(r) for r in ui.ui_text_layout(font, text).glyphRuns()],
                                 [run_signature(r) for r in qt_line(original, text).glyphRuns()])
                self.assertEqual(self.host.text_advance(font, text), QFontMetricsF(original).horizontalAdvance(text))

    def test_fallback_families_are_installed_and_font_copies_are_isolated(self):
        book = ui.FontBook()  # No PingFang asset exists in this project's fonts.
        font = book.font("pingfang", 9)
        available = set(QFontDatabase.families())
        preferred = [f for f in ("Microsoft YaHei UI", "Microsoft YaHei", "DengXian") if f in available]
        if preferred:
            self.assertEqual(font.family(), preferred[0])
        self.assertEqual(font.families(), list(dict.fromkeys(font.families())))
        self.assertTrue(QFontMetricsF(font).inFontUcs4(ord("血")))
        copy = self.fonts.font("jello_light", 9)
        copy.setFamilies(["__missing_font__"])
        copy.setWeight(QFont.Bold)
        copy.setPixelSize(30)
        again = self.fonts.font("jello_light", 9)
        self.assertEqual(ui.ui_font_key(again), ui.ui_font_key(self.font))
        for key in ("material", "polaricon"):
            self.assertEqual(len(self.fonts.font(key, 9).families()), 1)

    def test_mixed_runs_keep_latin_light_and_use_regular_cjk_on_one_baseline(self):
        suffix = " AVATAR ffi 55/70W"
        layout = ui.ui_text_layout(self.font, "功率" + suffix)
        reference = qt_line(self.font, "功率" + suffix, spans=((0, 2),))
        self.assertEqual(ui.ui_text_advance(self.font, "功率" + suffix), reference.lineAt(0).naturalTextWidth())
        latin = layout.glyphRuns(2, len(suffix))
        original = qt_line(self.font, suffix).glyphRuns()
        self.assertEqual([run_signature(r) for r in latin], [run_signature(r) for r in original])
        for mixed, plain in zip(latin, original):
            start = mixed.positions()[0].x() - plain.positions()[0].x()
            for a, b in zip(mixed.positions(), plain.positions()):
                self.assertAlmostEqual(a.x() - b.x(), start)
        cjk = layout.glyphRuns(0, 2)
        self.assertTrue(cjk)
        installed_cjk = set(QFontDatabase.families(QFontDatabase.WritingSystem.SimplifiedChinese))
        preferred_cjk = set(ui.cjk_ui_font(self.font).families()) & installed_cjk
        self.assertEqual(sum(len(run.glyphIndexes()) for run in cjk), 2)
        for run in cjk:
            self.assertEqual(run.rawFont().weight(), QFont.Normal)
            # Generic system fallback names can resolve to a different real
            # installed face (e.g. Noto CJK on Linux). Require CJK coverage,
            # rather than comparing the raw face to an unresolved font alias.
            self.assertIn(run.rawFont().familyName(), preferred_cjk or installed_cjk)
        for run in layout.glyphRuns():
            self.assertNotIn(0, run.glyphIndexes())
            for position in run.positions():
                self.assertAlmostEqual(position.y(), layout.lineAt(0).ascent())

    def test_format_ranges_use_utf16_after_emoji_and_supplementary_han(self):
        layout = ui.ui_text_layout(self.font, "A😀𠀀汉AV（功率）")
        self.assertEqual([(r.start, r.length) for r in layout.formats()], [(3, 3), (8, 4)])
        self.assertTrue(ui.has_cjk("𠀀"))
        self.assertTrue(ui.has_cjk("\U00030000"))
        self.assertFalse(ui.has_cjk("AV😀"))

    def test_cache_key_handles_qt_flags_and_all_omitted_font_properties(self):
        base = ui.ui_font_key(self.font)
        for change in (
                lambda f: f.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality),
                lambda f: f.setHintingPreference(QFont.PreferFullHinting),
                lambda f: f.setKerning(False), lambda f: f.setOverline(True),
                lambda f: f.setFamilies([*f.families(), "__other_fallback__"])):
            font = QFont(self.font)
            change(font)
            key = ui.ui_font_key(font)
            self.assertNotEqual(base, key)
            hash(key)  # Actual PySide6 Flag values, never int(StyleStrategy).

    def test_kerning_changes_mixed_and_latin_metrics_without_stale_layout(self):
        other = QFont(self.font)
        other.setKerning(False)
        for text, spans in (("AVATAR", ()), ("功率 AVATAR", ((0, 2),))):
            with self.subTest(text=text):
                first = ui.ui_text_layout(self.font, text)
                second = ui.ui_text_layout(other, text)
                self.assertIsNot(first, second)
                self.assertNotAlmostEqual(first.lineAt(0).naturalTextWidth(), second.lineAt(0).naturalTextWidth())
                # QTextLine borrows its layout's storage. Keep the owner alive
                # while measuring; a temporary layout can segfault in Qt 6.8.
                reference = qt_line(other, text, spans=spans)
                expected = reference.lineAt(0).naturalTextWidth()
                self.assertEqual(ui.ui_text_advance(other, text), expected)
                metric = expected if spans else QFontMetricsF(other).horizontalAdvance(text)
                self.host.text_advance(self.font, text)
                self.assertEqual(self.host.text_advance(other, text), metric)
        decorated = QFont(self.font)
        decorated.setOverline(True)
        self.assertIsNot(ui.ui_text_layout(self.font, "功率 AV"), ui.ui_text_layout(decorated, "功率 AV"))

    def test_point_font_dpi_and_fractional_dpr_use_device_matched_layouts(self):
        text = "功率 55/70W"
        widths = {}
        keys = set()
        for dpi in (96, 120, 144):
            for dpr in (1.0, 1.25, 1.5, 2.0):
                with self.subTest(dpi=dpi, dpr=dpr):
                    image = canvas(dpi=dpi, dpr=dpr)
                    layout = ui.ui_text_layout(self.font, text, image)
                    expected = qt_line(self.font, text, image, ((0, 2),))
                    width = ui.ui_text_advance(self.font, text, image)
                    self.assertEqual(width, expected.lineAt(0).naturalTextWidth())
                    self.assertEqual({r.rawFont().pixelSize() for r in layout.glyphRuns()},
                                     {round(self.font.pointSizeF() * image.logicalDpiY() / 72)})
                    self.assertIs(ui.ui_text_layout(self.font, text, image), layout)
                    if dpi in widths:
                        self.assertEqual(widths[dpi], width)  # DPR does not double logical advances.
                    widths[dpi] = width
                    keys.add(ui.ui_font_key(self.font, image))
        self.assertEqual(len(keys), 12)
        # Full hinting can round each glyph differently at each pixel size.
        # Verify actual device shaping above, not an invalid linear-width law.
        self.assertLess(widths[96], widths[120])
        self.assertLess(widths[120], widths[144])

    def test_pixel_font_dpi_does_not_change_logical_size(self):
        font = QFont(self.font)
        font.setPixelSize(12)
        widths = [ui.ui_text_advance(font, "血量 AV", canvas(dpi=dpi, dpr=1.5)) for dpi in (96, 120, 144)]
        self.assertEqual(widths, [widths[0]] * 3)

    def test_shared_and_gpu_shapers_agree_at_the_same_prepared_raster_size(self):
        renderer = ClickGuiGpuRenderer()  # Layout only; no GL init or textures.
        text = "装甲3D透视HUD（锁定）"
        for key in ("pingfang", "jello_light"):
            font = self.fonts.font(key, 15)
            for dpr in (1.0, 1.25, 1.5, 2.0):
                with self.subTest(key=key, dpr=dpr):
                    physical = 20 * dpr
                    sample = renderer.small_text_policy.sample_scale_for(physical, preserve_weight=True)
                    raster = renderer.small_text_policy.prepare_font(font, physical, sample, preserve_weight=True)
                    gpu, advance, height, ascent = renderer._jello_text_layout(text, raster)
                    shared = ui.ui_text_layout(raster, text)
                    line = shared.lineAt(0)
                    self.assertEqual((line.naturalTextWidth(), line.height(), line.ascent()),
                                     (advance, height, ascent))
                    # glyphRuns() is not promised in visual/source order.
                    def positioned_runs(layout):
                        return sorted((run_signature(r), tuple((p.x(), p.y()) for p in r.positions()))
                                      for r in layout.glyphRuns())
                    self.assertEqual(positioned_runs(shared), positioned_runs(gpu))
                    gpu_ink = QRectF()
                    for run in gpu.glyphRuns():
                        for glyph, pos in zip(run.glyphIndexes(), run.positions()):
                            gpu_ink = gpu_ink.united(run.rawFont().boundingRect(glyph).translated(pos))
                    self.assertEqual(shared._ui_ink_bounds, gpu_ink)

    def test_elision_cache_separates_kerning_and_device_dpi(self):
        text = "功率 AVATAR 55/70W"
        other = QFont(self.font)
        other.setKerning(False)
        image = canvas()
        threshold = ui.ui_text_advance(self.font, "功率 AVA…", image)
        self.assertNotEqual(ui.elide_ui_text(self.font, text, threshold, image),
                            ui.elide_ui_text(other, text, threshold, image))
        for dpi in (96, 120, 144, 96):
            image = canvas(dpi=dpi, dpr=1.5)
            p = QPainter(image)
            try:
                result = self.host.elide(p, self.font, text, threshold)
                self.assertEqual(result, ui.elide_ui_text(self.font, text, threshold, image))
                self.assertLessEqual(ui.ui_text_advance(self.font, result, image), threshold)
            finally:
                p.end()

    def test_elision_uses_exact_float_width_and_never_splits_graphemes(self):
        text = "汉e\u0301👩\u200d🚀𠀀功率AVATAR"
        image = canvas(dpr=1.5)
        finder = QTextBoundaryFinder(QTextBoundaryFinder.Grapheme, text)
        offsets = {0: 0}
        offset = 0
        for index, char in enumerate(text):
            offset += 2 if ord(char) > 0xffff else 1
            offsets[offset] = index + 1
        boundaries = {0}
        while (offset := finder.toNextBoundary()) >= 0:
            boundaries.add(offsets[offset])
        ellipsis = ui.ui_text_advance(self.font, "…", image)
        full = ui.ui_text_advance(self.font, text, image)
        for width in (-1, 0, ellipsis - .01, ellipsis, 20.25, 40.75, full - .01, full):
            with self.subTest(width=width):
                result = ui.elide_ui_text(self.font, text, width, image)
                self.assertLessEqual(ui.ui_text_advance(self.font, result, image), max(0, width))
                if result and result != text:
                    self.assertTrue(result.endswith("…"))
                    self.assertIn(len(result[:-1]), boundaries)
                    self.assertTrue(text.startswith(result[:-1]))
        self.assertEqual(ui.elide_ui_text(self.font, text, full, image), text)
        threshold = ui.ui_text_advance(self.font, "汉e\u0301…", image)
        self.assertNotEqual(ui.elide_ui_text(self.font, text, threshold - .01, image),
                            ui.elide_ui_text(self.font, text, threshold + .01, image))

    def test_warm_layout_and_elision_do_not_reshape_or_enumerate_fonts(self):
        image = canvas(dpr=1.25)
        text = "血量 AVATAR 55/70W"
        layout = ui.ui_text_layout(self.font, text, image)
        expected = ui.elide_ui_text(self.font, text, 45.5, image)
        painter = QPainter(image)
        try:
            mixed = self.host.elide(painter, self.font, text, 45.5)
            with patch.object(ui, "QTextLayout", side_effect=AssertionError("warm layout reshaped")), \
                    patch.object(ui, "_elide_ui_text", side_effect=AssertionError("warm elision recomputed")), \
                    patch.object(QFontDatabase, "families", side_effect=AssertionError("hot-path font scan")):
                self.assertIs(ui.ui_text_layout(self.font, text, image), layout)
                self.assertEqual(ui.elide_ui_text(self.font, text, 45.5, image), expected)
                self.assertEqual(self.host.elide(painter, self.font, text, 45.5), mixed)
                ui.draw_ui_text(painter, QRectF(5, 5, 200, 30), text, self.font, Qt.AlignCenter)
        finally:
            painter.end()

    def test_baseline_snap_uses_dpr_and_current_transform_not_cached_origin(self):
        for dpr in (1.0, 1.25, 1.5, 2.0):
            for scale in (.72, 1.0, 1.35):
                with self.subTest(dpr=dpr, scale=scale):
                    image = canvas(dpr=dpr)
                    painter = QPainter(image)
                    painter.translate(4.2, 3.3)
                    painter.scale(scale, scale)
                    text, rect = "功率 AVATAR", QRectF(8, 9, 160, 24)
                    layout = ui.ui_text_layout(self.font, text, image)
                    before = (painter.transform(), painter.clipBoundingRect(), painter.font(), painter.pen())
                    try:
                        with patch.object(layout, "draw", wraps=layout.draw) as draw:
                            ui.draw_ui_text(painter, rect, text, self.font, Qt.AlignCenter)
                        origin = draw.call_args.args[1]
                        baseline = painter.deviceTransform().map(origin + QPointF(0, layout.lineAt(0).ascent())).y()
                        self.assertAlmostEqual(baseline, round(baseline))
                        self.assertEqual(before, (painter.transform(), painter.clipBoundingRect(), painter.font(), painter.pen()))
                    finally:
                        painter.end()
                    self.assertTrue(any(bytes(image.constBits())))

    def test_small_row_pixels_equal_uncropped_qt_line_with_ink_center(self):
        # Scoreboard-like 6/8/12px labels, without importing the changing topbar.
        for size, text, row_height in ((6, "蓝方", 6), (8, "裁判比分", 10), (12, "步兵1 103", 13)):
            for dpr in (1.0, 1.5, 2.0):
                with self.subTest(size=size, text=text, dpr=dpr):
                    font = QFont(self.font)
                    font.setPixelSize(size)
                    rect = QRectF(8, 10, 100, row_height)
                    image, expected = canvas(128, 40, dpr=dpr), canvas(128, 40, dpr=dpr)
                    spans = ((0, 2),) if size in (6, 12) else ((0, 4),)
                    layout = qt_line(font, text, expected, spans)
                    line, ink = layout.lineAt(0), QRectF()
                    for run in layout.glyphRuns():
                        for glyph, pos in zip(run.glyphIndexes(), run.positions()):
                            ink = ink.united(run.rawFont().boundingRect(glyph).translated(pos))
                    x = rect.center().x() - line.naturalTextWidth() / 2
                    y = rect.center().y() - ink.center().y()
                    y += (round((y + line.ascent()) * dpr) - (y + line.ascent()) * dpr) / dpr
                    p, reference = QPainter(image), QPainter(expected)
                    try:
                        p.setPen(QColor("white"))
                        reference.setPen(QColor("white"))
                        ui.draw_ui_text(p, rect, text, font, Qt.AlignCenter, ink_center=True)
                        layout.draw(reference, QPointF(x, y))
                        self.assertFalse(p.hasClipping())
                    finally:
                        p.end()
                        reference.end()
                    self.assertEqual(image, expected)  # All strokes, not merely nonempty output.
                    self.assertTrue(any(bytes(image.constBits())))

    def test_mixin_latin_pixels_and_elision_remain_original_qpainter(self):
        original = QFont(self.font)
        original.setFamily(self.fonts.loaded["jello_light"])
        for dpr in (1.0, 1.5, 2.0):
            image, expected = canvas(dpr=dpr), canvas(dpr=dpr)
            p, reference = QPainter(image), QPainter(expected)
            rect, text = QRectF(8, 10, 120, 24), "AVATAR ffi HUD 55/70W 123456789"
            try:
                self.host.draw_text(p, rect, text, self.font, QColor("white"), Qt.AlignCenter)
                reference.setFont(original)
                reference.setPen(QColor("white"))
                clipped = reference.fontMetrics().elidedText(text, Qt.ElideRight, int(rect.width()))
                reference.drawText(rect, int(Qt.AlignCenter), clipped)
            finally:
                p.end()
                reference.end()
            self.assertEqual(image, expected)

    def test_mixin_cjk_draw_and_elision_share_the_same_device_layout(self):
        for dpi in (96, 120, 144):
            for dpr in (1.0, 1.5, 2.0):
                image, expected = canvas(dpi=dpi, dpr=dpr), canvas(dpi=dpi, dpr=dpr)
                p, reference = QPainter(image), QPainter(expected)
                text, rect = "底盘功率 AVATAR 55/70W", QRectF(8, 10, 120.75, 24)
                try:
                    self.host.draw_text(p, rect, text, self.font, QColor("white"), Qt.AlignCenter)
                    clipped = ui.elide_ui_text(self.font, text, rect.width(), expected)
                    self.assertLessEqual(ui.ui_text_advance(self.font, clipped, expected), rect.width())
                    reference.setPen(QColor("white"))
                    ui.draw_ui_text(reference, rect, clipped, self.font, Qt.AlignCenter)
                finally:
                    p.end()
                    reference.end()
                self.assertEqual(image, expected)

    def test_glass_commands_keep_latin_face_and_select_only_existing_cjk_profile(self):
        host = Host(self.fonts, gpu=True)
        image = canvas(dpr=1.5)
        p = QPainter(image)
        try:
            p.scale(1.25, 1.25)
            p.setOpacity(.6)
            for text, profile in (("功率 AV 55/70W", "jello"), ("AV 55/70W", "")):
                host._gpu_hud_commands = ClickGuiDrawList()
                host.draw_glass_text(p, QRectF(8, 10, 120, 24), text, self.font, QColor("white"))
                commands = host._gpu_hud_commands.texts
                self.assertEqual(len(commands), 2)
                for command in commands:
                    self.assertEqual(command.font, self.font)
                    self.assertEqual(command.font.styleName(), "Light")
                    self.assertEqual(command.text_profile, profile)
                    self.assertTrue(command.preserve_weight)
                    self.assertTrue(command.device_pixel_snap)
                    self.assertFalse(command.shadow)
                    self.assertEqual(command.render_scale, 1.25)
                    self.assertEqual(command.raster_scale, 1.0)
                self.assertAlmostEqual(commands[1].color.alphaF(), .6, delta=1/255)
        finally:
            p.end()


if __name__ == "__main__":
    unittest.main()
