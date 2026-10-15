"""Offline cache contracts. No app startup, GL context, devices or services.

Run with PySide6: python tests/test_render_performance_regressions.py
Optional raster-only comparison: /usr/bin/python3 ... --qt5
The explicit Qt5 option is not full PySide6/OpenGL application validation.
"""
from __future__ import annotations

import ast
from importlib import import_module
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def isolated_class(root, relative, name, methods=None, namespace=None):
    """Load only reviewed class bodies, not app/backend module imports."""
    tree = ast.parse((Path(root) / relative).read_text(encoding="utf-8-sig"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    if methods is not None:
        node.body = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    env = dict(namespace or {})
    exec(compile(ast.fix_missing_locations(module), str(Path(root) / relative), "exec"), env)
    return env[name]


def atlas_renderer(root=ROOT):
    cls = isolated_class(root, "rm_client/ui/clickgui_gpu.py", "ClickGuiGpuRenderer", {
        "_font_key", "_ensure_atlas", "_ensure_shaped_atlas",
    })
    renderer = cls()
    renderer._pixel_ratio = 1.0
    renderer._atlas_chars = {}
    renderer._shaped_texts = {}
    renderer.atlases = {}
    renderer.shaped_atlases = {}
    renderer.atlas_hits = renderer.atlas_misses = 0
    renderer.atlas_generation = 7
    # Missing resources must stop before trying to create/upload a texture.
    renderer._atlas_locked = True
    return renderer


def fake_font(family="Jello", size=15.0, weight=300):
    return SimpleNamespace(family=lambda: family, pointSizeF=lambda: size, weight=lambda: weight)


class AtlasCacheTests(unittest.TestCase):
    def setUp(self):
        self.renderer = atlas_renderer()
        self.font = fake_font()
        self.key = self.renderer._font_key(self.font)
        self.page = object()

    def test_warm_glyph_hit_keeps_page_and_generation_during_render_lock(self):
        r = self.renderer
        chars = set("FPS 144 目标状态")
        r._atlas_chars[self.key] = chars
        r.atlases[self.key] = self.page
        for text in ("FPS 144", "目标", "", "4444"):
            self.assertIs(r._ensure_atlas(self.font, text), self.page)
        self.assertIs(r._atlas_chars[self.key], chars)
        self.assertEqual(chars, set("FPS 144 目标状态"))
        self.assertEqual((r.atlas_hits, r.atlas_misses, r.atlas_generation), (4, 0, 7))

    def test_warm_shaped_hit_keeps_page_and_generation(self):
        r = self.renderer
        known = {"Robot Status", "目标状态", "FPS 144"}
        r._shaped_texts[self.key] = known
        r.shaped_atlases[self.key] = self.page
        for texts in (("目标状态", "Robot Status"), ("FPS 144", "FPS 144", ""), ()):
            self.assertIs(r._ensure_shaped_atlas(self.font, texts), self.page)
        self.assertIs(r._shaped_texts[self.key], known)
        self.assertEqual(known, {"Robot Status", "目标状态", "FPS 144"})
        self.assertEqual((r.atlas_hits, r.atlas_misses, r.atlas_generation), (3, 0, 7))

    def test_new_character_still_hits_render_pass_guard(self):
        r = self.renderer
        r._atlas_chars[self.key] = set("ABC")
        r.atlases[self.key] = self.page
        with self.assertRaisesRegex(RuntimeError, "glyph atlas mutation"):
            r._ensure_atlas(self.font, "AB新")
        self.assertEqual(r._atlas_chars[self.key], set("ABC"))
        self.assertEqual(r.atlas_hits, 0)
        self.assertIs(r.atlases[self.key], self.page)

    def test_new_shaped_line_still_hits_render_pass_guard(self):
        r = self.renderer
        r._shaped_texts[self.key] = {"old"}
        r.shaped_atlases[self.key] = self.page
        with self.assertRaisesRegex(RuntimeError, "shaped text atlas mutation"):
            r._ensure_shaped_atlas(self.font, ["new"])
        self.assertEqual(r._shaped_texts[self.key], {"old"})
        self.assertEqual(r.atlas_hits, 0)

    def test_known_text_without_published_texture_is_not_a_hit(self):
        r = self.renderer
        r._atlas_chars[self.key] = set("ABC")
        r._shaped_texts[self.key] = {"ABC"}
        for method, value in ((r._ensure_atlas, "ABC"), (r._ensure_shaped_atlas, ["ABC"])):
            with self.assertRaisesRegex(RuntimeError, "atlas mutation"):
                method(self.font, value)
        self.assertEqual(r.atlas_hits, 0)

    def test_oversized_glyph_page_does_not_evict_during_render_pass(self):
        r = self.renderer
        chars = {chr(0x400 + index) for index in range(1026)}
        r._atlas_chars[self.key] = chars
        r.atlases[self.key] = self.page
        with self.assertRaisesRegex(RuntimeError, "atlas mutation"):
            r._ensure_atlas(self.font, "新")
        self.assertEqual(chars, {chr(0x400 + index) for index in range(1026)})
        self.assertEqual(r.atlas_hits, 0)

    def test_oversized_shaped_page_does_not_evict_during_render_pass(self):
        r = self.renderer
        known = {f"line {index:04}" for index in range(2050)}
        r._shaped_texts[self.key] = known
        r.shaped_atlases[self.key] = self.page
        with self.assertRaisesRegex(RuntimeError, "atlas mutation"):
            r._ensure_shaped_atlas(self.font, ["new line"])
        self.assertEqual(len(known), 2050)
        self.assertIn("line 2049", known)
        self.assertEqual(r.atlas_hits, 0)

    def test_font_dpr_raster_scale_and_weight_changes_still_miss(self):
        r = self.renderer
        r._atlas_chars[self.key] = set("ABC")
        r.atlases[self.key] = self.page
        cases = ((fake_font(size=16.0), {}), (fake_font(weight=400), {}),
                 (fake_font(family="Other"), {}), (self.font, {"raster_scale": 2.0}),
                 (self.font, {"preserve_weight": True}))
        for font, kwargs in cases:
            with self.assertRaisesRegex(RuntimeError, "atlas mutation"):
                r._ensure_atlas(font, "ABC", **kwargs)
        r._pixel_ratio = 2.0
        with self.assertRaisesRegex(RuntimeError, "atlas mutation"):
            r._ensure_atlas(self.font, "ABC")
        self.assertEqual(r.atlas_hits, 0)


QT_GUI = None
QT_APP = None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if "--qt5" in sys.argv:
    sys.argv.remove("--qt5")
    from PyQt5 import QtGui as QT_GUI
else:
    try:
        from PySide6 import QtGui as QT_GUI
    except ImportError:
        pass
def initialize_qt():
    global QT_APP
    if QT_GUI is None:
        return None
    # QApplication also supports font-only tests and can safely coexist with
    # widget test suites in the same process. No window is created here.
    widgets = import_module(QT_GUI.__name__.split(".")[0] + ".QtWidgets")
    QT_APP = widgets.QApplication.instance() or widgets.QApplication([])
    return QT_APP


@unittest.skipIf(QT_GUI is None, "PySide6 unavailable; opt into --qt5 for raster-only tests")
class FontCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_qt()

    def setUp(self):
        cls = isolated_class(ROOT, "rm_client/ui/fonts.py", "FontBook", namespace={
            "Path": Path, "QFont": QT_GUI.QFont, "QFontDatabase": QT_GUI.QFontDatabase,
        })
        self.book = cls()

    def test_cached_font_has_independent_mutable_value(self):
        first = self.book.font("jello_light", 15.0)
        expected = QT_GUI.QFont(first)
        first.setPixelSize(44)
        first.setBold(True)
        first.setItalic(True)
        first.setFamily("Other")
        second = self.book.font("jello_light", 15.0)
        self.assertEqual(second, expected)
        second.setLetterSpacing(QT_GUI.QFont.SpacingType.AbsoluteSpacing, 10)
        self.assertEqual(self.book.font("jello_light", 15.0), expected)

    def test_resolved_family_changes_invalidate_fallback(self):
        fallback = self.book.font("jello_light", 15.0)
        self.book.loaded["jello_light"] = "Loaded face"
        loaded = self.book.font("jello_light", 15.0)
        self.assertNotEqual(loaded.family(), fallback.family())
        self.assertEqual(loaded.family(), "Loaded face")
        self.book.loaded["jello_light"] = "Replacement face"
        self.assertEqual(self.book.font("jello_light", 15.0).family(), "Replacement face")

    def test_reload_invalidates_cache_even_when_family_name_is_unchanged(self):
        self.book.loaded["jello_light"] = "Reloaded face"
        expected = self.book.font("jello_light", 15.0)
        self.assertTrue(self.book._font_cache)
        self.book._find_font = lambda *_: SimpleNamespace(exists=lambda: True)
        database = SimpleNamespace(addApplicationFont=lambda _: 1,
                                   applicationFontFamilies=lambda _: ["Reloaded face"])
        with patch.dict(self.book.load_from_project_assets.__globals__, QFontDatabase=database):
            self.book.load_from_project_assets(ROOT)
        self.assertEqual(self.book._font_cache, {})
        self.assertEqual(self.book.font("jello_light", 15.0), expected)

    def test_exact_fractional_size_and_weight_are_not_quantized(self):
        for size in (9.0, 12.0001, 12.0002, 12.25, 15.0, 18.75, 31.125):
            for weight in (None, QT_GUI.QFont.Weight.Light, QT_GUI.QFont.Weight.Bold):
                first = self.book.font("jello_medium", size, weight)
                second = self.book.font("jello_medium", size, weight)
                self.assertEqual(first, second)
                self.assertEqual(second.pointSizeF(), size)
                self.assertEqual(second.weight(), QT_GUI.QFont.Weight.Medium if weight is None else weight)

    def test_tahoma_stays_plain_and_not_antialiased(self):
        for _ in range(2):
            font = self.book.font("tahoma", 16.0, QT_GUI.QFont.Weight.Bold)
            self.assertFalse(font.bold())
            self.assertEqual(font.styleStrategy(), QT_GUI.QFont.StyleStrategy.NoAntialias)

    def test_fonts_do_not_leak_between_books(self):
        other = type(self.book)()
        self.book.loaded["pingfang"] = "First"
        other.loaded["pingfang"] = "Second"
        self.assertEqual(self.book.font("pingfang", 12).family(), "First")
        self.assertEqual(other.font("pingfang", 12).family(), "Second")

    def test_cache_capacity_and_recreated_evicted_value(self):
        expected = self.book.font("jello_light", 11.25)
        for i in range(650):
            self.book.font("jello_light", 12 + i / 1000)
        self.assertLessEqual(len(self.book._font_cache), 512)
        self.assertEqual(self.book.font("jello_light", 11.25), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
