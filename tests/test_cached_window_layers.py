"""Actual window layouts through cached-frame orchestration and raster FBOs.

Only native GL resources/submission are replaced. Real window construction,
layout, painter methods, interaction geometry, cache logic, and pass cleanup
execute. Frozen-source comparisons are CPU/command evidence, never desktop GPU
pixel, performance, device, or release qualification.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import os
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRect, QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from rm_client.ui import gpu_frame_compositor as compositor
from rm_client.ui.polar.primitives import EconomyScenePhase
from cached_frame_trace_support import baseline
import test_window_normal_frame as normal_window
from test_framebuffer_generation import FboFormat


def record(value):
    if isinstance(value, (QRectF, QRect)):
        return tuple(value.getRect())
    if isinstance(value, QPointF):
        return value.x(), value.y()
    if isinstance(value, QColor):
        return value.getRgb()
    if isinstance(value, QFont):
        return value.family(), value.pointSizeF(), int(value.weight()), value.italic()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, bool, int, float, type(None))):
        return value
    if isinstance(value, dict):
        return tuple((key, record(item)) for key, item in sorted(value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(record(item) for item in value)
    if is_dataclass(value):
        return type(value).__name__, tuple((field.name, record(getattr(value, field.name))) for field in fields(value))
    # Hit targets retain live Module/Setting identity. Compare their stable
    # semantic name rather than object addresses from two independent windows.
    return type(value).__name__, getattr(value, "name", None)


class RasterWindowFrame:
    def __init__(self, scenario, *, original=False, ratio=1., size=(800, 600)):
        self.fixture = normal_window.NormalWindowFrameTests()
        self.fixture.setUp()
        self.stack = ExitStack()
        self.window = w = self.fixture.window
        self.c = c = w.gpu_compositor
        self.original = original
        if original:
            # The frozen source predates Hero extraction. Restore its inert
            # display-state seam, not a production alias or active controller.
            w.hero_deploy_ui = NS(render_visible=False)
            owners = baseline.OverlayScene.glass_owners
            def retired_hero_owners(scene, *, radial_visible=False, hero_visible=False):
                if hero_visible:
                    raise AssertionError('Frozen non-Hero fixture cannot enable a retired feature')
                return owners(scene, radial_visible=radial_visible)
            self.stack.enter_context(patch.object(baseline.OverlayScene, 'glass_owners', retired_hero_owners))
            for name in ("flush_gpu_hud_commands", "flush_gpu_island_icons", "flush_gpu_quick_action_wheel"):
                method = getattr(baseline.CachedFrameBaseline, name)
                self.stack.enter_context(patch.object(w, name, MethodType(method, w)))
        self.calls = []
        self.current = None
        self.failed = False
        self.fail_commands = False
        self.fail_present = False
        owner = self

        class RasterFbo:
            class Attachment:
                NoAttachment = 0
                CombinedDepthStencil = 1

            def __init__(self, size, fmt):
                self.image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
                self.image.fill(0xFFCA00CA)

            def bind(self):
                owner.current = self.image
                return True

            def isValid(self):
                return True

            def texture(self):
                return 17  # Inert native handle for specialized submission edges.

            def release(self):
                return True

        self.stack.enter_context(patch.object(compositor, "QOpenGLFramebufferObject", RasterFbo))
        self.stack.enter_context(patch.object(compositor, "QOpenGLFramebufferObjectFormat", FboFormat))
        for name in ("prepare_blur", "render_glass", "prepare_treated_backdrop", "present"):
            self.stack.enter_context(patch.object(c, name, side_effect=self.final_pass(name)))
        self.stack.enter_context(patch.object(c, "paint_device", side_effect=self.paint_device))
        self.stack.enter_context(patch.object(c, "clear_current", side_effect=self.clear_current))
        self.stack.enter_context(patch.object(c, "_upload_image_to_fbo", side_effect=self.upload))
        self.stack.enter_context(patch.object(w.clickgui_gpu, "prepare_frame", side_effect=self.prepare))
        self.stack.enter_context(patch.object(w.clickgui_gpu, "render", side_effect=self.render_commands))
        functions = NS(glActiveTexture=lambda *args: None, glBindTexture=lambda *args: None)
        self.stack.enter_context(patch.object(w, "context", return_value=NS(extraFunctions=lambda: functions)))
        self.stack.enter_context(patch.object(w, "defaultFramebufferObject", return_value=7))
        self.stack.enter_context(patch.object(w, "devicePixelRatioF", return_value=ratio))
        w.resize(*size)
        # Fix the presentation clock and its already-created origins, not the
        # backend/thread clock used by fixture setup and shutdown.
        w.state.start_time = 0.
        w._dynamic_hud_started_at = 0.
        w._combat_hud_last_time = 100.
        w.anim.begin_frame(100.)
        w.gui_motion.begin_frame(100.)
        w.toasts.clear()
        c.available = True
        w.clickgui_gpu.available = not scenario.endswith("compat")
        w.island_icon_gpu.available = False
        w.quick_action_wheel_gpu.available = False
        w.vision_wire_gpu.available = False
        w.startup_gpu.available = False
        w.hud_video_gpu.available = False
        w.registry.get("StartupVisual").get_setting("Background Mode").value = "ParticleGrid"
        w.home_visible = scenario.startswith("startup")
        w.startup_step = "scenario"
        w.startup_transition_from = None
        w.startup_transition_to = None
        w.startup_transition_started = 0.
        w.gui_visible = scenario.startswith("clickgui")
        w.gui_timeline.progress = 1. if w.gui_visible else 0.
        w.gui_timeline.target = w.gui_timeline.progress
        w.economy_visible = scenario.startswith("economy")
        w.economy_island.progress = 1. if w.economy_visible else 0.
        w.economy_phase = EconomyScenePhase.OPEN if w.economy_visible else EconomyScenePhase.CLOSED
        if scenario == "startup_transition":
            w.startup_transition_from = "home"
            w.startup_transition_to = "scenario"
            w.startup_transition_started = 99.9
        if scenario == "radial":
            w.quick_action_wheel.open(400, 300, 800, 600, now=99.)
            w.quick_action_wheel.progress = 1.
        if scenario == "chat":
            w.match_chat.open_input()
        if scenario == "student":
            w.student_ui.open("damage")
            w.student_ui.progress = 1.
        if scenario == "escape":
            w.set_escape_menu_visible(True)

    def close(self):
        # Leave no inert GL resources attached to the production lifecycle.
        self.c._destroy_fbos()
        self.c.available = False
        self.window.clickgui_gpu.available = False
        self.stack.close()
        self.fixture.doCleanups()

    def final_pass(self, name):
        def execute(*args, **kwargs):
            self.calls.append((name, record(kwargs)))
            return not (name == "present" and self.fail_present)
        return execute

    def paint_device(self, layer):
        fbo = getattr(self.c, layer + "_fbo")
        self.calls.append(("paint", layer))
        fbo.bind()
        return fbo.image

    def clear_current(self, functions, *, transparent):
        self.current.fill(0 if transparent else 0xFF000000)

    def upload(self, functions, fbo, image, x, y, **kwargs):
        self.calls.append(("upload", x, y, image.width(), image.height(), record(kwargs)))
        painter = QPainter(fbo.image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(x, y, image)
        return painter.end()

    def prepare(self, commands, ratio):
        self.calls.append(("prepare", ratio, record(commands.commands)))

    def render_commands(self, functions, commands, width, height, ratio, **kwargs):
        self.calls.append(("commands", width, height, ratio, record(commands.commands), record(kwargs)))
        if self.fail_commands:
            self.failed = True
            return False
        return True

    def frame(self, *, now=100.):
        with patch("time.perf_counter", return_value=now), patch.object(compositor, "_LOG"):
            if self.original:
                accepted = baseline.CachedFrameBaseline._paint_gpu_frame_cached(self.window)
            else:
                accepted = self.window._paint_gpu_frame_cached()
        w, c = self.window, self.c
        pixels = {}
        for layer in ("background", "control", "overlay", "vision", "dynamic", "target"):
            image = getattr(c, layer + "_fbo").image
            if image.paintingActive():
                raise AssertionError(layer + " retained an active painter")
            pixels[layer] = hashlib.sha256(bytes(image.constBits())).hexdigest()
        result = dict(
            accepted=accepted, calls=tuple(self.calls), pixels=pixels,
            hotspots=record(w.hotspots), viewports=record(w.gui_scroll_viewports),
            static_key=w._gpu_static_ui_key, startup_key=w._startup_static_page_key,
            motion_ready=w._gpu_motion_layer_ready, motion_at=w._gpu_motion_layer_updated_at,
            static_glass=record(c._static_glass_commands), frame_glass=record(c._frame_glass_commands),
            recording=w._gpu_frame_recording, faults=c.diagnostics.faults,
            restore_pending=w._economy_restore_pending_generation,
            economy_phase=w.economy_phase,
        )
        self.calls.clear()
        return result


class ActualCachedWindowTests(unittest.TestCase):
    def test_real_layouts_commands_interactions_and_raster_layers_match_frozen_entry(self):
        scenarios = ("hud", "clickgui", "clickgui_compat", "economy", "economy_compat",
                     "startup", "startup_transition", "radial", "chat", "student")
        # Esc is intentionally exclusive now; test_endfield_backdrop checks
        # its actual empty combat layers and full HUD restoration separately.
        for size, ratio in (((800, 600), 1.), ((1280, 720), 1.5)):
            for scenario in scenarios:
                with self.subTest(scenario=scenario, size=size, ratio=ratio):
                    outcomes = []
                    for original in (True, False):
                        runner = RasterWindowFrame(scenario, original=original, size=size, ratio=ratio)
                        try:
                            outcomes.append((runner.frame(), runner.frame()))
                        finally:
                            runner.close()
                    self.assertEqual(outcomes[0], outcomes[1])
                    self.assertTrue(all(result["accepted"] for result in outcomes[1]))
                    self.assertTrue(all(not result["recording"] for result in outcomes[1]))

    def test_real_restore_commit_requires_accepted_present_and_matching_generation(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                runner = RasterWindowFrame("hud")
                try:
                    w = runner.window
                    w.economy_phase = EconomyScenePhase.CLOSING
                    w._economy_generation = 4 if stale else 3
                    w._economy_restore_pending_generation = 3
                    w._economy_closing_generation = 3
                    finalize = w._finalize_economy_restore
                    def commit(generation):
                        runner.calls.append(("restore.commit", generation))
                        finalize(generation)
                    with patch.object(w, "_finalize_economy_restore", side_effect=commit):
                        runner.fail_present = True
                        rejected = runner.frame()
                        self.assertFalse(rejected["accepted"])
                        self.assertEqual(rejected["restore_pending"], 3)
                        self.assertEqual(rejected["economy_phase"], EconomyScenePhase.CLOSING)
                        self.assertNotIn("restore.commit", [call[0] for call in rejected["calls"]])
                        runner.fail_present = False
                        accepted = runner.frame()
                        self.assertTrue(accepted["accepted"])
                        names = [call[0] for call in accepted["calls"]]
                        if stale:
                            self.assertEqual(accepted["restore_pending"], 3)
                            self.assertEqual(accepted["economy_phase"], EconomyScenePhase.CLOSING)
                            self.assertNotIn("restore.commit", names)
                        else:
                            self.assertIsNone(accepted["restore_pending"])
                            self.assertEqual(accepted["economy_phase"], EconomyScenePhase.CLOSED)
                            self.assertLess(names.index("present"), names.index("restore.commit"))
                            self.assertIsNone(w._economy_closing_generation)
                            self.assertTrue(w._economy_close_finalized)
                finally:
                    runner.close()

    def test_actual_economy_layout_rejection_then_retry_preserves_settings_and_cache(self):
        runner = RasterWindowFrame("economy")
        try:
            preference = runner.window.registry.get("ClickGui").get_setting("Render Backend")
            choice = preference.value
            runner.fail_commands = True
            failed = runner.frame()
            self.assertTrue(runner.failed)
            self.assertFalse(failed["accepted"])
            self.assertIsNone(failed["static_key"])
            self.assertFalse(failed["motion_ready"])
            self.assertFalse(failed["recording"])
            self.assertNotIn("present", [call[0] for call in failed["calls"]])
            self.assertEqual(preference.value, choice)
            runner.fail_commands = False
            succeeded = runner.frame()
            self.assertTrue(succeeded["accepted"])
            self.assertTrue(succeeded["hotspots"])
            self.assertIn("present", [call[0] for call in succeeded["calls"]])
            self.assertEqual(preference.value, choice)
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
