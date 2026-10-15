"""Private-FBO cache ownership regression, with real Qt raster painting.

GL allocation/draw calls are inert doubles. These tests execute the production
allocation and frame-control methods; they do not verify driver pixels or DWM.
"""
from __future__ import annotations
import ast
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from rm_client.ui import gpu_frame_compositor as compositor

APP = QApplication.instance() or QApplication([])


def window_methods():
    source = ROOT / "rm_client/ui/polar_app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PolarRMWindow")
    node.bases = []; node.decorator_list = []
    node.body = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name in {
        "_paint_gpu_frame_cached", "invalidate_transient_overlay_scene", "paint_gpu_control_backdrop",
    }]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    scope = {"time": time, "QPainter": QPainter, "GlassOwner": compositor.GlassOwner,
             "EconomyScenePhase": NS(CLOSING="closing"), "PRIMARY_GUI_MODE": "JelloSigma"}
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), scope)
    return scope["PolarRMWindow"]


class Fbo:
    class Attachment:
        NoAttachment = 0
        CombinedDepthStencil = 1

    def __init__(self, size, fmt):
        self.image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        self.image.fill(0xFFCA00CA)  # Detect a newly allocated, never-drawn target.

    def isValid(self):
        return True


class FboFormat:
    def setAttachment(self, value): pass
    def setInternalTextureFormat(self, value): pass


class FramebufferGenerationTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("QOpenGLFramebufferObject", Fbo), ("QOpenGLFramebufferObjectFormat", FboFormat)):
            patcher = patch.object(compositor, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        self.c = c = compositor.GpuFrameCompositor()
        c.available = True
        self.paints = []
        self.current = None

        def paint_device(layer):
            self.paints.append(layer)
            self.current = getattr(c, layer + "_fbo").image
            return self.current

        def clear_current(functions, *, transparent):
            self.current.fill(0 if transparent else 0xFF000000)

        c.paint_device = paint_device
        c.clear_current = clear_current
        c.release_layer = Mock()
        for name in ("clear_target_layer", "clear_vision_layer", "prepare_blur", "render_glass",
                     "prepare_treated_backdrop", "present", "clear_scene_layers"):
            setattr(c, name, Mock(return_value=True))
        c.set_backdrop_treatment = Mock(return_value=False)
        self.w = w = window_methods()()
        w.gpu_compositor = c
        w.context = lambda: NS(extraFunctions=lambda: None)
        w.logical_size = (800, 600); w.ratio = 1.0
        w.width = lambda: w.logical_size[0]; w.height = lambda: w.logical_size[1]
        w.devicePixelRatioF = lambda: w.ratio
        w._economy_restore_pending_generation = None; w._economy_generation = 0; w.economy_phase = "closed"
        w.home_visible = False; w.wallpaper_picker_visible = False; w.clickgui_gpu = NS(available=True)
        w.gpu_static_ui_key = lambda width, height, ratio: ("battle-ui", width, height, ratio)
        w._gpu_static_ui_key = None; w._should_defer_hud_static_rebuild = lambda key: False
        w.paint_gpu_background = lambda *args: True
        w.paint_gpu_control_backdrop = lambda *args, **kwargs: False
        w.economy_island = NS(progress=0); w.economy_visible = False
        w.gui_timeline = NS(progress=0); w.gui_visible = False
        w.student_ui = NS(visible=False, panel="damage", progress=1.0)
        w.escape_menu_visible = False; w.quick_action_wheel = NS(visible=False); w.match_chat = NS(input_open=False)
        w.paint_gpu_vision_layer = lambda *args: False
        w.registry = NS(get=lambda name: None)
        w._gpu_motion_layer_ready = False; w._gpu_motion_layer_updated_at = 0
        w.menu_overlay_active = lambda: False; w._gpu_background_changed = True
        w.hero_deploy_ui = NS(render_visible=False)
        w.defaultFramebufferObject = lambda: 1; w.frame_scheduler = NS(force=Mock())
        for name in ("paint_overlay", "begin_gpu_hud_command_collection", "begin_gpu_island_icon_collection",
                     "begin_gpu_quick_action_wheel_collection", "paint_clickgui_overlay", "flush_gpu_hud_commands",
                     "flush_gpu_island_icons", "flush_gpu_quick_action_wheel"):
            setattr(w, name, Mock())
        w.paint_hud_motion_overlay = lambda painter, *args, **kwargs: painter.fillRect(0, 0, 30, 30, 0xFFFFFFFF)
        for name in ("paint_gpu_target_hud_layer", "paint_priority_interaction_layer"):
            setattr(w, name, Mock(return_value=True))

    def frame(self):
        self.assertTrue(self.w._paint_gpu_frame_cached(), self.c.last_error)

    def warm_priority(self, kind="radial"):
        self.frame()
        if kind == "radial": self.w.quick_action_wheel.visible = True
        if kind == "student": self.w.student_ui.visible = True
        if kind == "chat": self.w.match_chat.input_open = True
        if kind == "escape": self.w.escape_menu_visible = True
        self.w._gpu_motion_layer_updated_at = time.perf_counter() + 60  # No cadence expiry.
        self.frame()
        self.paints.clear()

    def assert_rebuilt(self):
        self.frame()
        self.assertIn("dynamic", self.paints, "replacement dynamic FBO was reused before painting")
        self.assertTrue(self.w._gpu_motion_layer_ready)
        self.assertNotEqual(self.c.dynamic_fbo.image.pixel(0, 0), 0xFFCA00CA)

    def test_dpr_change_while_radial_open_rebuilds_before_present(self):
        self.warm_priority(); self.w.ratio = 2.0
        self.assert_rebuilt()
        self.assertEqual(self.c._size, QSize(1600, 1200))

    def test_dpr_change_while_student_open_rebuilds_before_present(self):
        self.warm_priority("student"); self.w.ratio = 1.5
        self.assert_rebuilt()

    def test_dpr_change_while_chat_open_ignores_old_refresh_deadline(self):
        self.warm_priority("chat"); self.w.ratio = 1.25
        self.assert_rebuilt()

    def test_dpr_change_while_escape_open_ignores_old_refresh_deadline(self):
        self.warm_priority("escape"); self.w.ratio = 2.0
        self.assert_rebuilt()

    def test_new_logical_size_rebuilds_even_without_resize_event(self):
        self.warm_priority(); self.w.logical_size = (1000, 700)
        self.assert_rebuilt()

    def test_same_size_reallocation_cannot_keep_old_scene_keys(self):
        self.warm_priority()
        self.w._startup_static_page_key = ("stale",)
        self.c._destroy_fbos()
        self.assert_rebuilt()
        self.assertIsNone(self.w._startup_static_page_key)

    def test_changed_dpr_rebuilds_even_when_physical_storage_size_is_equal(self):
        self.warm_priority()
        old = self.c.dynamic_fbo
        self.w.logical_size = (400, 300); self.w.ratio = 2.0
        self.assert_rebuilt()
        self.assertIs(self.c.dynamic_fbo, old)
        self.assertEqual(self.c._size, QSize(800, 600))

    def test_unchanged_storage_retains_motion_cache(self):
        self.warm_priority()
        old = self.c.dynamic_fbo
        self.frame()
        self.assertIs(self.c.dynamic_fbo, old)
        self.assertNotIn("dynamic", self.paints)

    def test_first_priority_frame_populates_new_dynamic_storage(self):
        self.w.quick_action_wheel.visible = True
        self.assert_rebuilt()

    def test_generation_changes_only_for_successful_allocations(self):
        c = self.c
        before = c.fbo_generation
        self.assertTrue(c.ensure_size(800, 600, 1.0))
        self.assertEqual(c.fbo_generation, before + 1)
        self.assertTrue(c.ensure_size(800, 600, 1.0))
        self.assertEqual(c.fbo_generation, before + 1)
        self.assertTrue(c.ensure_size(800, 600, 2.0))
        self.assertEqual(c.fbo_generation, before + 2)
        with patch.object(Fbo, "isValid", return_value=False):
            self.assertFalse(c.ensure_size(900, 600, 2.0))
        self.assertEqual(c.fbo_generation, before + 2)

    def test_control_bind_failure_rejects_frame_and_retries_cleanly(self):
        # Use the actual control method, rather than the default no-op stub.
        del self.w.paint_gpu_control_backdrop
        normal_paint_device = self.c.paint_device
        self.c.paint_device = lambda layer: None if layer == "control" else normal_paint_device(layer)
        self.assertFalse(self.w._paint_gpu_frame_cached())
        self.c.present.assert_not_called()
        self.assertIn("control backdrop", self.c.last_error)
        self.assertIsNone(self.w._gpu_control_backdrop_key)
        self.assertIsNone(self.w._gpu_static_ui_key)
        self.assertFalse(self.w._gpu_motion_layer_ready)
        self.w.frame_scheduler.force.assert_called_once()

        self.c.paint_device = normal_paint_device
        self.frame()
        self.assertIn("control", self.paints)
        self.assertEqual(self.c.control_fbo.image.pixel(0, 0), 0)
        self.c.present.assert_called_once()

    def test_unchanged_control_backdrop_remains_a_successful_cached_frame(self):
        del self.w.paint_gpu_control_backdrop
        self.frame()
        self.paints.clear()
        self.frame()
        self.assertNotIn("control", self.paints)
        self.assertEqual(self.c.present.call_count, 2)


if __name__ == "__main__": unittest.main()
