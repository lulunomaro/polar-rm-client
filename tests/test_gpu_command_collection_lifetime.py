"""Actual failed-window frames must stop collecting before raster fallback.

Native GL resource/submission edges are controlled. The original window frame,
real QImage painter, real draw_glass_text, and layer cleanup all execute; these
checks establish lifecycle/result ownership, not native GPU image quality.
"""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from rm_client.ui import gpu_frame_compositor as compositor
from test_cached_window_layers import RasterWindowFrame


def paint_fallback_text(window):
    image = QImage(240, 60, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    try:
        with patch.object(window, "minecraft_hud_active", return_value=True), patch.object(window, "menu_overlay_active", return_value=False):
            window.draw_glass_text(painter, QRectF(2, 2, 230, 55), "Fallback HUD", QFont("sans-serif", 16), QColor("white"))
    finally:
        painter.end()
    return bytes(image.constBits())


class CommandCollectionLifetimeTests(unittest.TestCase):
    def assert_collections_closed(self, window):
        self.assertFalse(window._collect_gpu_hud_commands)
        self.assertFalse(window._collect_gpu_island_icons)
        self.assertFalse(window._collect_gpu_quick_action_wheel)
        self.assertFalse(window._gpu_hud_commands.commands)
        self.assertFalse(window._gpu_island_icon_commands)
        self.assertIsNone(window._gpu_quick_action_wheel_command)
        self.assertFalse(window._gpu_quick_action_wheel_labels.commands)

    def test_frozen_original_leaves_collection_open_and_swallows_fallback_text(self):
        runner = RasterWindowFrame("hud", original=True)
        try:
            window = runner.window
            window.island_icon_gpu.available = True
            with patch.object(window, "paint_hud_motion_overlay", side_effect=TypeError("interrupted HUD painter")):
                result = runner.frame()
            self.assertFalse(result["accepted"])
            self.assertTrue(window._collect_gpu_hud_commands)
            self.assertTrue(window._collect_gpu_island_icons)
            pixels = paint_fallback_text(window)
            self.assertFalse(any(pixels), "the frozen baseline retains its demonstrated missing-text defect")
            self.assertTrue(window._gpu_hud_commands.commands)
        finally:
            runner.close()

    def test_failed_collection_stages_discard_pending_commands_before_fallback_and_retry(self):
        stages = ("begin_hud", "begin_island", "begin_wheel", "painter_begin", "hud_paint", "gui_paint",
                  "painter_end_false", "hud_prepare", "hud_render", "island_render", "wheel_render")
        for stage in stages:
            with self.subTest(stage=stage):
                runner = RasterWindowFrame("hud")
                try:
                    window = runner.window
                    window.island_icon_gpu.available = True
                    window.quick_action_wheel_gpu.available = True
                    failed = False
                    def fault():
                        nonlocal failed
                        failed = True
                        raise TypeError("interrupted " + stage)
                    with ExitStack() as stack:
                        island = stack.enter_context(patch.object(window.island_icon_gpu, "render", return_value=True))
                        wheel = stack.enter_context(patch.object(window.quick_action_wheel_gpu, "render", return_value=True))
                        draw_hud = window.paint_hud_motion_overlay
                        def draw_pending(*args, **kwargs):
                            draw_hud(*args, **kwargs)
                            window._gpu_island_icon_commands.append(object())
                            window._gpu_quick_action_wheel_command = object()
                            window._gpu_quick_action_wheel_labels.rect(QRectF(1, 2, 3, 4), 0., QColor("white"))
                            if stage == "hud_paint" and not failed:
                                fault()
                        stack.enter_context(patch.object(window, "paint_hud_motion_overlay", side_effect=draw_pending))
                        with ExitStack() as injection:
                            begin_methods = {"begin_hud": "begin_gpu_hud_command_collection", "begin_island": "begin_gpu_island_icon_collection", "begin_wheel": "begin_gpu_quick_action_wheel_collection"}
                            if stage in begin_methods:
                                begin = getattr(window, begin_methods[stage])
                                def interrupted_begin():
                                    begin()
                                    fault()
                                injection.enter_context(patch.object(window, begin_methods[stage], side_effect=interrupted_begin))
                            elif stage == "painter_begin":
                                begin_painter = compositor.LayerPaintSession.begin_painter
                                def interrupted_painter(session):
                                    painter = begin_painter(session)
                                    if session.device is runner.c.dynamic_fbo.image:
                                        fault()
                                    return painter
                                injection.enter_context(patch.object(compositor.LayerPaintSession, "begin_painter", interrupted_painter))
                            elif stage == "gui_paint":
                                injection.enter_context(patch.object(window, "paint_clickgui_overlay", side_effect=lambda *a, **k: fault()))
                            elif stage == "painter_end_false":
                                end_painter = compositor.LayerPaintSession.end_painter
                                def rejected_end(session):
                                    nonlocal failed
                                    result = end_painter(session)
                                    if session.device is runner.c.dynamic_fbo.image:
                                        failed = True
                                        return False
                                    return result
                                injection.enter_context(patch.object(compositor.LayerPaintSession, "end_painter", rejected_end))
                            elif stage in ("hud_prepare", "hud_render"):
                                method = "prepare_frame" if stage == "hud_prepare" else "render"
                                injection.enter_context(patch.object(window.clickgui_gpu, method, side_effect=lambda *a, **k: fault()))
                            elif stage == "island_render":
                                island.side_effect = lambda *a, **k: fault()
                            elif stage == "wheel_render":
                                wheel.side_effect = lambda *a, **k: fault()
                            result = runner.frame()
                        self.assertTrue(failed)
                        self.assertFalse(result["accepted"])
                        self.assertFalse(result["motion_ready"])
                        self.assertIsNone(result["static_key"])
                        self.assertNotIn("present", [call[0] for call in result["calls"]])
                        self.assert_collections_closed(window)
                        fallback = paint_fallback_text(window)
                        self.assertTrue(any(fallback), "fallback must draw pixels instead of collecting new GPU text")
                        self.assert_collections_closed(window)
                        if stage == "painter_end_false":
                            self.assertEqual(result["faults"], 0)
                        else:
                            self.assertIn("interrupted " + stage, runner.c.last_error)
                            self.assertEqual(runner.c.last_failed_frame.render_fault.error_type, "TypeError")
                        island.side_effect = wheel.side_effect = None
                        self.assertTrue(runner.frame()["accepted"])
                        self.assert_collections_closed(window)
                finally:
                    runner.close()

    def test_collection_cleanup_fault_disables_every_capture_flag_before_fallback(self):
        runner = RasterWindowFrame("hud")
        try:
            window = runner.window
            window.island_icon_gpu.available = True
            window.quick_action_wheel_gpu.available = True
            class FailingClear(list):
                def clear(self):
                    raise OSError("command discard")
            def draw(*args, **kwargs):
                window._gpu_island_icon_commands = FailingClear([object()])
                raise TypeError("original collection draw")
            release = runner.c.release_layer
            with patch.object(window, "paint_hud_motion_overlay", side_effect=draw), patch.object(runner.c, "release_layer", wraps=release) as releases:
                result = runner.frame()
            self.assertFalse(result["accepted"])
            self.assertFalse(window._collect_gpu_hud_commands)
            self.assertFalse(window._collect_gpu_island_icons)
            self.assertFalse(window._collect_gpu_quick_action_wheel)
            recovery = runner.c.last_failed_frame
            self.assertEqual(recovery.render_fault.error_type, "TypeError")
            self.assertIn("original collection draw", runner.c.last_error)
            self.assertEqual([fault.error_type for fault in recovery.cleanup_faults], ["OSError"])
            self.assertIn("gpu-commands", recovery.cleanup_faults[0].phase)
            self.assertIn(unittest.mock.call("target"), releases.call_args_list)
            self.assertTrue(any(paint_fallback_text(window)))
            window._gpu_island_icon_commands = []
        finally:
            runner.close()

    def test_original_draw_fault_survives_painter_and_framebuffer_cleanup_faults(self):
        runner = RasterWindowFrame("hud")
        try:
            window = runner.window
            window.island_icon_gpu.available = True
            window.quick_action_wheel_gpu.available = True
            end_painter = compositor.LayerPaintSession._end_painter
            def failing_end(session):
                accepted = end_painter(session)
                if session.device is runner.c.dynamic_fbo.image:
                    raise OSError("painter unwind")
                return accepted
            release_layer = runner.c.release_layer
            releases = []
            def failing_release(layer):
                releases.append(layer)
                accepted = release_layer(layer)
                if layer == "dynamic":
                    raise RuntimeError("framebuffer unwind")
                return accepted
            with patch.object(window, "paint_hud_motion_overlay", side_effect=TypeError("original draw")), patch.object(compositor.LayerPaintSession, "_end_painter", failing_end), patch.object(runner.c, "release_layer", side_effect=failing_release):
                result = runner.frame()
            self.assertFalse(result["accepted"])
            recovery = runner.c.last_failed_frame
            self.assertEqual(recovery.render_fault.error_type, "TypeError")
            self.assertIn("original draw", runner.c.last_error)
            self.assertEqual([fault.error_type for fault in recovery.cleanup_faults], ["OSError", "RuntimeError"])
            self.assertIn("target", releases, "later independent owners must still release")
            self.assert_collections_closed(window)
            self.assertTrue(any(paint_fallback_text(window)))
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
