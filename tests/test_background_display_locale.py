"""Production background-cache locale coverage with an offscreen raster FBO.

Compile the actual background compositor and video-loss drawing methods without
constructing a window, transport, media player, or OpenGL context.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from rm_client.model import build_default_registry
from rm_client.ui.hero_locale import hero_text
from rm_client.ui.jello_locale import chinese_enabled
from rm_client.ui.cs2_video_settings import smooth_video
from rm_client.ui import gpu_frame_compositor as gpu


def production_methods(texts):
    methods = []
    for path, class_name, names in (
        (ROOT / "rm_client/ui/polar_app.py", "PolarRMWindow", {"paint_gpu_background", "gpu_static_ui_key", "_should_defer_hud_static_rebuild"}),
        (ROOT / "rm_client/ui/polar/mixins/video_backend_mixin.py", "VideoBackendMixin",
         {"draw_hero_native_background", "draw_backend_video_lost"}),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        owner = next(node for node in tree.body
                     if isinstance(node, ast.ClassDef) and node.name == class_name)
        selected = [node for node in owner.body
                    if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in selected} == names
        methods.extend(selected)

    class RecordingPainter:
        RenderHint = QPainter.RenderHint

        def __init__(self, device):
            self.painter = QPainter(device)

        def __getattr__(self, name):
            return getattr(self.painter, name)

        def drawText(self, *args):
            texts.append((args[0].getRect(), int(args[1]), args[2]))
            return self.painter.drawText(*args)

    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        ast.ClassDef(name="WindowMethods", bases=[], keywords=[], body=methods, decorator_list=[]),
    ], type_ignores=[])
    scope = dict(QRectF=QRectF, Qt=Qt, QColor=QColor, QPainter=RecordingPainter,
                 time=time, chinese_enabled=chinese_enabled, hero_text=hero_text, smooth_video=smooth_video)
    exec(compile(ast.fix_missing_locations(module), "background_display_methods", "exec"), scope)
    scope["WindowMethods"].painter_type = RecordingPainter
    return scope["WindowMethods"]


class BackgroundDisplayLocaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_static_hud_and_startup_keys_invalidate_without_new_telemetry(self):
        window = production_methods([])()
        window.registry = build_default_registry()
        window.startup_step = 'scenario'; window.wallpaper_picker_visible = False
        window._startup_page_epoch = 1; window.clickgui_gpu = NS(available=True)
        window.match_mode_manager = NS(current=NS(key='RMUC7v7'))
        window.state = NS(current_stage='MATCH', stage_countdown_sec=60, last_event='',
                          deploy_mode=0, deploy_status_revision=1)
        window.economy_visible = False; window.economy_island = NS(progress=0)
        window._economy_scene_epoch = 1; window.gui_visible = True
        window.gui_timeline = NS(progress=1); window.student_ui = NS(visible=False)
        for home in (False, True):
            window.home_visible = home
            keys=[]
            for locale in ('English', '简体中文', 'English'):
                window.registry.get('ClickGui').get_setting('Language').value=locale
                key=window.gpu_static_ui_key(1280,720,1)
                self.assertEqual(key,window.gpu_static_ui_key(1280,720,1))
                keys.append(key)
            self.assertEqual(keys[0],keys[2]);self.assertNotEqual(keys[0],keys[1])
            self.assertEqual(window.state.stage_countdown_sec,60)
            # The deferral guard relies on the original kind/width/height/DPR
            # prefix. Locale must not shift DPR outside its geometry slice.
            self.assertEqual(keys[0][:4], ("startup-ui" if home else "battle-ui", 1280, 720, 1))
            window._gpu_static_ui_key = keys[0]
            window._hud_static_defer_since = 0.0
            window.module_list_animation_in_flight = lambda: True
            window._last_module_toggle_at = 0.0
            self.assertFalse(window._should_defer_hud_static_rebuild(window.gpu_static_ui_key(1280,720,2)))
            self.assertFalse(window._should_defer_hud_static_rebuild(window.gpu_static_ui_key(1440,720,1)))
            self.assertFalse(window._should_defer_hud_static_rebuild(window.gpu_static_ui_key(1280,900,1)))
            self.assertTrue(window._should_defer_hud_static_rebuild(keys[0]))
    def test_text_bearing_background_and_blur_caches_follow_language(self):
        cases = (
            (False, "lost", "VIDEO SIGNAL LOST · WAITING FOR FRESH DECODED FRAME"),
            (True, "waiting", "WAITING FOR NATIVE 0x0310 COMPRESSED VIDEO"),
            (True, "lost", "NATIVE 0x0310 VIDEO LOST · NO CAMERA FALLBACK"),
        )
        for hero, phase, english in cases:
            with self.subTest(hero=hero, phase=phase):
                texts = []
                window = production_methods(texts)()
                window.registry = build_default_registry()
                language = window.registry.get("ClickGui").get_setting("Language")
                window.home_visible = False
                window.state = NS(deploy_status_revision=17)
                window._hero_native_sequence = 23
                window.hero_native_image = None
                window.hero_deploy_video_cut_active = lambda: hero
                window.hero_native_frame_state = lambda now=None: phase
                window.backend_video_source_selected = lambda: True
                window.normal_backend_video_fresh = lambda: False
                window.setting_mode = lambda *args: "Native 0x0310"
                window.high_res_optimize = lambda *args: False
                window.font = lambda *args: QFont("Sans Serif", 10)
                window._gpu_background_key = None
                window._gpu_blur_source_key = None
                image = QImage(1280, 720, QImage.Format_ARGB32_Premultiplied)
                image.fill(Qt.transparent)
                builds = []

                def paint_device(layer):
                    builds.append(layer)
                    return image

                # Exercise the real pass/session and cleanup boundary. Only
                # the final FBO device is raster-backed; native GL is inert.
                window.gpu_compositor = gpu.GpuFrameCompositor()
                window.gpu_compositor.background_fbo = object()
                window.gpu_compositor.paint_device = paint_device
                window.gpu_compositor.clear_current = lambda *args, **kwargs: image.fill(Qt.transparent)
                window.gpu_compositor.release_layer = lambda *args: None
                recorder = patch.object(gpu, "QPainter", window.painter_type)
                recorder.start()
                self.addCleanup(recorder.stop)
                background_keys = []
                blur_keys = []
                images = []
                for index, (locale, expected) in enumerate((
                    ("English", english), ("简体中文", hero_text(english, True)),
                    ("English", english),
                ), 1):
                    language.value = locale
                    self.assertTrue(window.paint_gpu_background(None, 1280, 720, 1.0))
                    self.assertEqual(len(builds), index)
                    self.assertEqual(texts[-1][2], expected)
                    self.assertTrue(window._gpu_background_changed)
                    background_keys.append(window._gpu_background_key)
                    blur_keys.append(window._gpu_blur_source_key)
                    images.append(image.copy())
                    # Same locale/state must retain the existing cache fast path.
                    self.assertTrue(window.paint_gpu_background(None, 1280, 720, 1.0))
                    self.assertEqual(len(builds), index)
                    self.assertFalse(window._gpu_background_changed)

                self.assertEqual(background_keys[0], background_keys[2])
                self.assertNotEqual(background_keys[0], background_keys[1])
                self.assertEqual(blur_keys[0], blur_keys[2])
                self.assertNotEqual(blur_keys[0], blur_keys[1])
                self.assertEqual(images[0], images[2])
                self.assertNotEqual(images[0], images[1])
                self.assertEqual([text[:2] for text in texts], [texts[0][:2]] * 3)
                self.assertEqual(window.state.deploy_status_revision, 17)
                self.assertEqual(window._hero_native_sequence, 23)
                self.assertEqual(window.hero_native_frame_state(), phase)


if __name__ == "__main__":
    unittest.main()
