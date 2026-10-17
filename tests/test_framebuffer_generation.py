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
from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from rm_client.ui import gpu_frame_compositor as compositor
from rm_client.ui.clickgui_gpu import ClickGuiDrawList
from rm_client.ui.cs2_video_settings import smooth_video
from rm_client.ui.polar.compositor_plan import (
    BackdropTreatment, FinalFrameCompositor, FrameCompositionPlan,
    OverlayScene, motion_layer_needs_refresh,
)

from rm_client.ui.polar.overlay_layers import (
    FrameViewport, LayerCacheSnapshot, MotionLayerInputs, OverlayDrawPorts,
    OverlayLayerPlan, OverlayLayerRenderer, StaticLayerMode,
)

APP = QApplication.instance() or QApplication([])


def window_methods():
    source = ROOT / "rm_client/ui/polar_app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PolarRMWindow")
    node.bases = []; node.decorator_list = []
    node.body = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name in {
        "_paint_gpu_frame_cached", "_discard_gpu_command_collections", "invalidate_transient_overlay_scene", "paint_gpu_control_backdrop",
        "paint_gpu_startup_interaction_layer", "paint_priority_interaction_layer", "paint_gpu_background",
    }]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    scope = {"time": time, "QPainter": QPainter, "QRectF": QRectF, "smooth_video": smooth_video, "GlassOwner": compositor.GlassOwner,
             "EconomyScenePhase": NS(CLOSING="closing"), "PRIMARY_GUI_MODE": "JelloSigma",
             "BackdropTreatment": BackdropTreatment, "FinalFrameCompositor": FinalFrameCompositor,
             "FrameCompositionPlan": FrameCompositionPlan, "OverlayScene": OverlayScene,
             "motion_layer_needs_refresh": motion_layer_needs_refresh,
             "FrameViewport": FrameViewport, "LayerCacheSnapshot": LayerCacheSnapshot,
             "MotionLayerInputs": MotionLayerInputs, "OverlayDrawPorts": OverlayDrawPorts,
             "OverlayLayerPlan": OverlayLayerPlan, "OverlayLayerRenderer": OverlayLayerRenderer,
             "StaticLayerMode": StaticLayerMode}
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

    def release(self):
        pass


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
        w._startup_static_page_key = None
        w.hotspots = []
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
        w._gpu_hud_commands = ClickGuiDrawList()
        w._gpu_island_icon_commands = []
        w._gpu_quick_action_wheel_command = None
        w._gpu_quick_action_wheel_labels = ClickGuiDrawList()
        w._collect_gpu_hud_commands = w._collect_gpu_island_icons = w._collect_gpu_quick_action_wheel = False
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
        self.assertEqual(self.c.last_rejected_frame.reason, "control paint pass rejected")
        self.assertEqual(self.c.diagnostics.faults, 0)
        self.assertIsNone(self.w._gpu_control_backdrop_key)
        self.assertIsNone(self.w._gpu_static_ui_key)
        self.assertFalse(self.w._gpu_motion_layer_ready)
        self.w.frame_scheduler.force.assert_called_once()

        self.c.paint_device = normal_paint_device
        self.frame()
        self.assertIn("control", self.paints)
        self.assertEqual(self.c.control_fbo.image.pixel(0, 0), 0)
        self.c.present.assert_called_once()

    def test_failed_first_release_keeps_other_cleanup_and_restore_pending(self):
        self.w._economy_restore_pending_generation = 4
        self.w._economy_generation = 4
        self.w.economy_phase = "closing"
        self.w._finalize_economy_restore = Mock()
        self.w.paint_gpu_background = Mock(side_effect=TypeError("invalid texture"))
        released = []

        def release(layer):
            released.append(layer)
            if layer == "background":
                raise RuntimeError("background release failed")

        self.c.release_layer.side_effect = release
        self.c.end_vision_layer = lambda: released.append("vision")
        with patch.object(compositor, "_LOG") as logger:
            self.assertFalse(self.w._paint_gpu_frame_cached())

        self.assertEqual(released, ["background", "control", "overlay", "vision", "dynamic", "target"])
        recovery = self.c.last_failed_frame
        self.assertEqual(recovery.render_fault.phase, "frame.compose")
        self.assertEqual(recovery.render_fault.error_type, "TypeError")
        self.assertEqual([f.phase for f in recovery.cleanup_faults], ["frame.cleanup.background"])
        self.assertEqual(recovery.cleanup_faults[0].cause_type, "TypeError")
        self.assertEqual(self.c.diagnostics.faults, 2)
        self.assertEqual(logger.error.call_count, 2)
        self.assertIn("TypeError: invalid texture", self.c.last_error)
        self.assertNotIn("release failed", self.c.last_error)
        self.assertEqual(self.w._economy_restore_pending_generation, 4)
        self.w._finalize_economy_restore.assert_not_called()
        self.c.present.assert_not_called()
        self.assertFalse(self.w._gpu_frame_recording)
        self.assertFalse(self.w._gpu_motion_layer_ready)
        self.w.frame_scheduler.force.assert_called_once()

    def test_multiple_cleanup_defects_are_retained_without_skipping_later_layers(self):
        self.w.paint_gpu_background = Mock(side_effect=AttributeError("missing image"))
        self.c.release_layer.side_effect = RuntimeError("release failed")
        self.c.end_vision_layer = Mock(side_effect=ValueError("vision release failed"))
        with patch.object(compositor, "_LOG"):
            self.assertFalse(self.w._paint_gpu_frame_cached())
        self.assertEqual([call.args[0] for call in self.c.release_layer.call_args_list],
                         ["background", "control", "overlay", "dynamic", "target"])
        self.c.end_vision_layer.assert_called_once()
        self.assertEqual(len(self.c.last_failed_frame.cleanup_faults), 6)
        self.assertEqual(self.c.diagnostics.faults, 7)
        self.assertEqual(self.c.last_failed_frame.render_fault.error_type, "AttributeError")

    def test_actual_release_false_rejects_frame_without_committing_cache_or_restore(self):
        del self.c.release_layer
        self.w._economy_restore_pending_generation = 8
        self.w._economy_generation = 8
        self.w.economy_phase = "closing"
        self.w._finalize_economy_restore = Mock()
        with patch.object(Fbo, "release", return_value=False) as release, \
             patch.object(compositor, "_LOG") as logger:
            self.assertFalse(self.w._paint_gpu_frame_cached())
        self.assertGreaterEqual(release.call_count, 7)
        self.c.present.assert_not_called()
        self.assertIsNone(self.w._gpu_static_ui_key)
        self.assertFalse(self.w._gpu_motion_layer_ready)
        self.assertFalse(self.w._gpu_frame_recording)
        self.assertEqual(self.w._economy_restore_pending_generation, 8)
        self.w._finalize_economy_restore.assert_not_called()
        self.assertEqual(self.c.last_rejected_frame.rejected_layers,
                         ("background", "control", "overlay", "vision", "dynamic", "target"))
        self.assertEqual(self.c.diagnostics.faults, 0)
        logger.error.assert_not_called()
        self.w.frame_scheduler.force.assert_called_once()
        self.frame()
        self.c.present.assert_called_once()
        self.w._finalize_economy_restore.assert_called_once_with(8)

    def test_control_release_false_rejects_before_any_overlay_or_cache_commit(self):
        del self.c.release_layer
        del self.w.paint_gpu_control_backdrop
        self.assertTrue(self.c.ensure_size(800, 600, 1.0))
        self.c.control_fbo.release = Mock(return_value=False)
        self.w.invalidate_transient_overlay_scene()
        self.assertFalse(self.w._paint_gpu_frame_cached())
        self.assertEqual(self.paints, ["control"])
        self.assertIsNone(self.w._gpu_control_backdrop_key)
        self.c.present.assert_not_called()
        self.assertIn("control", self.c.last_rejected_frame.rejected_layers)
        self.assertEqual(self.c.diagnostics.faults, 0)

    def test_false_dynamic_release_prevents_motion_cache_commit_and_final_composition(self):
        del self.c.release_layer
        self.assertTrue(self.c.ensure_size(800, 600, 1.0))
        self.c.dynamic_fbo.release = Mock(return_value=False)
        self.assertFalse(self.w._paint_gpu_frame_cached())
        self.assertIn("dynamic", self.paints)
        self.assertFalse(self.w._gpu_motion_layer_ready)
        self.assertEqual(self.w._gpu_motion_layer_updated_at, 0)
        self.c.prepare_blur.assert_not_called()
        self.c.present.assert_not_called()
        self.assertIn("dynamic", self.c.last_rejected_frame.rejected_layers)

    def test_startup_interaction_finally_propagates_real_dynamic_release_rejection(self):
        del self.c.release_layer
        self.assertTrue(self.c.ensure_size(800, 600, 1.0))
        self.c.dynamic_fbo.release = Mock(return_value=False)
        self.w.hover_tooltip = ""
        self.w.build_gpu_startup_transition = Mock(return_value=object())
        self.w.build_gpu_toasts = Mock()
        self.w.hotspots = []
        self.w.clickgui_gpu = NS(prepare_frame=Mock(), render=Mock(return_value=True))
        self.assertFalse(self.w.paint_gpu_startup_interaction_layer(None, 800, 600, 1.))
        self.w.clickgui_gpu.render.assert_called_once()
        self.c.dynamic_fbo.release.assert_called_once()
        self.assertTrue(self.c.frame_rejected)

    def test_priority_interaction_finally_propagates_real_target_release_rejection(self):
        del self.c.release_layer
        del self.w.paint_priority_interaction_layer
        self.assertTrue(self.c.ensure_size(800, 600, 1.0))
        self.c.target_fbo.release = Mock(return_value=False)
        self.w.draw_student_frontend = Mock()
        self.w.draw_escape_menu = Mock()
        self.assertFalse(self.w.paint_priority_interaction_layer(None, 800, 600, 1.))
        self.w.draw_student_frontend.assert_called_once()
        self.w.draw_escape_menu.assert_called_once()
        self.c.target_fbo.release.assert_called_once()
        self.assertTrue(self.c.frame_rejected)

    def test_renderer_false_unwinds_layers_and_invalidates_partial_metadata_without_an_exception(self):
        self.w.paint_gpu_background = lambda *args: False
        self.assertFalse(self.w._paint_gpu_frame_cached())
        self.assertEqual([call.args[0] for call in self.c.release_layer.call_args_list],
                         ["background", "control", "overlay", "dynamic", "target"])
        self.assertIsNone(self.w._gpu_static_ui_key)
        self.assertFalse(self.w._gpu_motion_layer_ready)
        self.assertEqual(self.c.last_rejected_frame.reason, "frame rejected")
        self.assertEqual(self.c.diagnostics.faults, 0)
        self.c.present.assert_not_called()

    def test_real_background_and_control_painters_end_before_rejected_pass_returns(self):
        self.assertTrue(self.c.ensure_size(800, 600, 1.0))
        painters = []
        def capture(device):
            painter = QPainter(device)
            painters.append(painter)
            return painter
        del self.w.paint_gpu_background
        self.w.hero_deploy_video_cut_active = lambda: False
        self.w.backend_video_source_selected = lambda: False
        self.w.normal_video_frame_ready = lambda: False
        self.w.high_res_optimize = lambda *args: False
        self.w.draw_synthetic_video = Mock(side_effect=TypeError("background paint defect"))
        self.w.invalidate_transient_overlay_scene()
        with patch.object(compositor, "QPainter", side_effect=capture), patch.object(compositor, "_LOG"):
            self.assertFalse(self.w.paint_gpu_background(None, 800, 600, 1.))
        self.assertEqual(len(painters), 1)
        self.assertFalse(painters[-1].isActive())
        self.assertIsNone(self.w._gpu_background_key)
        self.assertEqual(self.c.last_failed_frame.render_fault.error_type, "TypeError")
        self.c.release_layer.assert_called_once_with("background")

        del self.w.paint_gpu_control_backdrop
        self.w.startup_step = "scenario"
        self.w._startup_page_epoch = 0
        self.w.get_auto_ui_scale = Mock(side_effect=AttributeError("control geometry defect"))
        self.c.release_layer.reset_mock()
        with patch.object(compositor, "QPainter", side_effect=capture), patch.object(compositor, "_LOG"):
            self.assertFalse(self.w.paint_gpu_control_backdrop(None, 800, 600, 1., startup_scene=True))
        self.assertEqual(len(painters), 2)
        self.assertFalse(painters[-1].isActive())
        self.assertIsNone(self.w._gpu_control_backdrop_key)
        self.assertEqual(self.c.last_failed_frame.render_fault.error_type, "AttributeError")
        self.c.release_layer.assert_called_once_with("control")

    def test_paint_pass_attempts_painter_state_and_framebuffer_cleanup_independently(self):
        self.assertTrue(self.c.ensure_size(800, 600, 1.0))
        events, painters = [], []
        class FailingEndPainter(QPainter):
            def end(self):
                events.append("painter")
                super().end()
                raise ValueError("painter end failed")
        def draw(session):
            painters.append(session.begin_painter())
            session.defer_cleanup("static_glass", Mock(side_effect=OSError("state cleanup failed")))
            session.defer_cleanup("later", lambda: events.append("later"))
            raise TypeError("bad paint")
        self.c.release_layer.side_effect = lambda layer: events.append(layer)
        with patch.object(compositor, "QPainter", FailingEndPainter), patch.object(compositor, "_LOG"):
            self.assertFalse(self.c.paint_layer(None, "dynamic", 1., draw))
        self.assertEqual(events, ["painter", "later", "dynamic"])
        self.assertFalse(painters[0].isActive())
        fault = self.c.last_failed_frame
        self.assertEqual(fault.render_fault.error_type, "TypeError")
        self.assertEqual([f.error_type for f in fault.cleanup_faults], ["ValueError", "OSError"])
        self.assertTrue(all("cleanup" in f.phase for f in fault.cleanup_faults))
        self.assertIn("TypeError: bad paint", self.c.last_error)
        self.assertTrue(self.c.frame_rejected)

    def test_painter_end_false_rejects_pass_without_fake_exception_or_native_flush(self):
        self.assertTrue(self.c.ensure_size(800, 600, 1.0))
        flush = Mock()
        class RejectingPainter(QPainter):
            def end(self):
                super().end()
                return False
        def draw(session):
            session.begin_painter()
            if not session.end_painter():
                return False
            flush()
            return True
        with patch.object(compositor, "QPainter", RejectingPainter), patch.object(compositor, "_LOG") as logger:
            self.assertFalse(self.c.paint_layer(None, "dynamic", 1., draw))
        self.c.release_layer.assert_called_once_with("dynamic")
        flush.assert_not_called()
        self.assertTrue(self.c.frame_rejected)
        self.assertEqual(self.c.diagnostics.faults, 0)
        logger.error.assert_not_called()

    def test_post_present_commit_failure_invalidates_metadata_and_keeps_restore_pending(self):
        self.w._economy_restore_pending_generation = 8
        self.w._economy_generation = 8
        self.w.economy_phase = "closing"
        self.w._finalize_economy_restore = Mock(side_effect=TypeError("commit failed"))
        with patch.object(compositor, "_LOG"):
            self.assertFalse(self.w._paint_gpu_frame_cached())
        self.c.present.assert_called_once()
        self.assertIsNone(self.w._gpu_static_ui_key)
        self.assertFalse(self.w._gpu_motion_layer_ready)
        self.assertFalse(self.w._gpu_frame_recording)
        self.assertEqual(self.w._economy_restore_pending_generation, 8)
        self.w.frame_scheduler.force.assert_called_once()
        self.assertEqual(self.c.last_failed_frame.render_fault.error_type, "TypeError")

    def test_startup_render_root_is_not_replaced_by_release_failure(self):
        self.assertTrue(self.c.ensure_size(800, 600, 1.0))
        self.w.hover_tooltip = ""
        self.w.build_gpu_startup_transition = Mock(return_value=object())
        self.w.build_gpu_toasts = Mock()
        self.w.hotspots = []
        self.w.clickgui_gpu = NS(prepare_frame=Mock(), render=Mock(side_effect=TypeError("bad startup draw")))
        self.c.release_layer.side_effect = OSError("release failed")
        with patch.object(compositor, "_LOG"):
            self.assertFalse(self.w.paint_gpu_startup_interaction_layer(None, 800, 600, 1.))
        failure = self.c.last_failed_frame
        self.assertEqual(failure.render_fault.error_type, "TypeError")
        self.assertIn("bad startup draw", self.c.last_error)
        self.assertEqual([f.error_type for f in failure.cleanup_faults], ["OSError"])

    def test_priority_painter_ends_before_fbo_release_on_actual_draw_failure(self):
        del self.w.paint_priority_interaction_layer
        self.assertTrue(self.c.ensure_size(800, 600, 1.0))
        painters = []
        def fail(painter, *_):
            painters.append(painter)
            raise TypeError("bad priority draw")
        self.w.draw_student_frontend = fail
        self.w.draw_escape_menu = Mock()
        def release(layer):
            self.assertFalse(painters[0].isActive())
            raise OSError("release failed")
        self.c.release_layer.side_effect = release
        with patch.object(compositor, "_LOG"):
            try:
                self.assertFalse(self.w.paint_priority_interaction_layer(None, 800, 600, 1.))
                self.assertFalse(painters[0].isActive())
                self.assertEqual(self.c.last_failed_frame.render_fault.error_type, "TypeError")
                self.assertEqual([f.error_type for f in self.c.last_failed_frame.cleanup_faults], ["OSError"])
            finally:
                for painter in painters:
                    if painter.isActive():
                        painter.end()

    def test_static_and_dynamic_painters_end_on_draw_failure_before_frame_recovery(self):
        for stage in ("static", "dynamic"):
            with self.subTest(stage=stage):
                self.w.invalidate_transient_overlay_scene()
                painters = []
                def fail(*args, **kwargs):
                    painters.append(kwargs.get("painter", args[0] if args else None))
                    raise TypeError("bad " + stage + " draw")
                old_static, old_dynamic = self.w.paint_overlay, self.w.paint_hud_motion_overlay
                if stage == "static":
                    self.w.paint_overlay = fail
                else:
                    self.w.paint_hud_motion_overlay = fail
                try:
                    with patch.object(compositor, "_LOG"):
                        self.assertFalse(self.w._paint_gpu_frame_cached())
                    self.assertEqual(len(painters), 1)
                    self.assertFalse(painters[0].isActive())
                    self.assertFalse(self.c._recording_static_glass)
                    self.assertEqual(self.c._glass_owner_stack, [compositor.GlassOwner.HUD])
                    self.assertFalse(self.w._gpu_motion_layer_ready)
                finally:
                    for painter in painters:
                        if painter.isActive(): painter.end()
                    self.w.paint_overlay, self.w.paint_hud_motion_overlay = old_static, old_dynamic

    def test_unchanged_control_backdrop_remains_a_successful_cached_frame(self):
        del self.w.paint_gpu_control_backdrop
        self.frame()
        self.paints.clear()
        self.frame()
        self.assertNotIn("control", self.paints)
        self.assertEqual(self.c.present.call_count, 2)


if __name__ == "__main__": unittest.main()
