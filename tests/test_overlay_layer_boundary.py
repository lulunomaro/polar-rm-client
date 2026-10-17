"""Explicit frame value and ownership boundaries, without a window/context.

The executor tests use production compositor paint sessions and Qt images;
only native GL allocation/submission and application-specific drawing differ.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.ui import gpu_frame_compositor as compositor
from rm_client.ui.clickgui_gpu import ClickGuiDrawList
from rm_client.ui.polar.clickgui_frame import ClickGuiFrame
from rm_client.ui.polar.compositor_plan import OverlayScene
from rm_client.ui.polar.overlay_layers import (
    FrameViewport, LayerCacheSnapshot, MotionLayerInputs, OverlayDrawPorts,
    OverlayLayerPlan, OverlayLayerRenderer, StaticLayerMode,
)
from cached_frame_trace_support import RecordedFrame


class FrameValueTests(unittest.TestCase):
    def plan(self, **changes):
        values = dict(
            viewport=FrameViewport(800, 600, 1.25), storage_generation=1,
            scene=OverlayScene(False, False, False, False),
            static_mode=StaticLayerMode.HUD_REBUILD, static_key=("hud", 800, 600, 1.25),
            startup_key=None, economy_gpu=False, prepare_clickgui=False,
            motion=MotionLayerInputs(False, False, False, False),
        )
        return OverlayLayerPlan(**dict(values, **changes))

    def test_viewport_rejects_invalid_logical_or_physical_dimensions(self):
        for width, height, ratio in (
            (0, 600, 1), (-1, 600, 1), (True, 600, 1), (800., 600, 1),
            (800, 0, 1), (800, False, 1), (800, 600, 0), (800, 600, -1),
            (800, 600, True), (800, 600, float("nan")),
            (800, 600, float("inf")), (800, 600, "1.5"),
            (800, 600, 1e308), (2_147_483_648, 600, 1),
            (2_147_483_648, 600, .5),
            (10 ** 400, 600, 1.), (800, 600, 10 ** 400),
        ):
            with self.subTest(width=width, height=height, ratio=ratio), self.assertRaises(ValueError):
                FrameViewport(width, height, ratio)
        self.assertEqual(FrameViewport(800, 600, 1.5).pixel_ratio, 1.5)

    def test_scene_cache_and_frame_containers_are_immutable(self):
        values = (
            (FrameViewport(800, 600, 1), "width", 900),
            (LayerCacheSnapshot(("hud",), None, True, 2.), "motion_ready", False),
            (MotionLayerInputs(False, False, True, False), "student_visible", False),
            (self.plan(), "static_key", ("changed",)),
        )
        for instance, attribute, replacement in values:
            with self.subTest(instance=type(instance).__name__), self.assertRaises(FrozenInstanceError):
                setattr(instance, attribute, replacement)

    def test_cache_rejects_mutable_key_payloads_and_nonfinite_timestamps(self):
        for key in (["hud"], ([],), ({"state": 1},), (NS(value=1),), (float("nan"),)):
            with self.subTest(key=key), self.assertRaises((TypeError, ValueError)):
                LayerCacheSnapshot(key, None, True, 1.)
        for timestamp in (float("nan"), float("inf"), True, "1", None, 10 ** 400):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                LayerCacheSnapshot(("hud",), None, True, timestamp)
        with self.assertRaises(TypeError):
            LayerCacheSnapshot(("hud",), None, 1, 1.)

    def test_plan_requires_explicit_validated_value_types_and_ownership(self):
        changes = (
            dict(storage_generation=-1), dict(storage_generation=True),
            dict(viewport=NS(width=800, height=600, pixel_ratio=1)),
            dict(scene=NS(economy_active=True)), dict(motion=NS(priority_active=False)),
            dict(scene=OverlayScene([], False, False, False)),
            dict(static_mode="hud_rebuild"), dict(static_key=None),
            dict(static_mode=StaticLayerMode.STARTUP_STABLE),
            dict(startup_key=("unexpected",)),
            dict(static_mode=StaticLayerMode.STARTUP_TRANSITION, prepare_clickgui=True),
            dict(static_mode=StaticLayerMode.STARTUP_TRANSITION,
                 scene=OverlayScene(False, True, False, False)),
            dict(static_mode=StaticLayerMode.ECONOMY),
            dict(scene=OverlayScene(True, False, False, False)),
            dict(static_mode=StaticLayerMode.ECONOMY,
                 scene=OverlayScene(True, False, False, False), prepare_clickgui=True),
            dict(economy_gpu=True), dict(prepare_clickgui=1), dict(prepare_clickgui=True),
            dict(scene=OverlayScene(True, False, False, False), economy_gpu=True, prepare_clickgui=True),
        )
        for values in changes:
            with self.subTest(values=values), self.assertRaises((TypeError, ValueError)):
                self.plan(**values)
        with self.assertRaises(TypeError):
            MotionLayerInputs(False, 1, False, False)


class OverlayLayerOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RecordedFrame()
        self.addCleanup(self.fixture.close)
        self.c = self.fixture.compositor
        self.assertTrue(self.c.ensure_size(800, 600, 1.))
        self.frame = ClickGuiFrame(ClickGuiDrawList(), (), ())
        self.renderer = NS(prepare_frame=Mock(), render=Mock(return_value=True))
        self.draws = OverlayDrawPorts(
            clear_startup_interactions=Mock(), startup_commands=Mock(return_value=ClickGuiDrawList()),
            startup_interaction=Mock(return_value=True), static_hud=Mock(),
            economy_commands=Mock(return_value=ClickGuiDrawList()), clickgui_frame=Mock(return_value=self.frame),
            motion_hud=lambda session, prepared: True,
            priority_interaction=Mock(return_value=True), target_hud=Mock(return_value=True),
        )
        self.plan = OverlayLayerPlan(
            viewport=FrameViewport(800, 600, 1.), storage_generation=self.c.fbo_generation,
            scene=OverlayScene(False, False, False, False),
            static_mode=StaticLayerMode.HUD_REBUILD, static_key=("hud", 800, 600, 1.),
            startup_key=None, economy_gpu=False, prepare_clickgui=False,
            motion=MotionLayerInputs(False, False, False, False),
        )
        self.cache = LayerCacheSnapshot(None, None, False, 0.)
        self.executor = OverlayLayerRenderer(self.c, self.renderer, clock=lambda: 10.)

    def render(self, **changes):
        self.c.begin_frame()
        with patch.object(compositor, "_LOG"):
            return self.executor.render(None, replace(self.plan, **changes), self.cache, self.draws)

    def test_executor_accepts_only_explicit_ports_and_returns_replacement_metadata(self):
        result = self.render()
        self.assertEqual(result, LayerCacheSnapshot(self.plan.static_key, None, True, 10.))
        self.assertEqual(self.cache, LayerCacheSnapshot(None, None, False, 0.))
        self.draws.static_hud.assert_called_once()
        self.draws.target_hud.assert_called_once()
        self.assertEqual(self.c._glass_owner_stack, [compositor.GlassOwner.HUD])
        self.assertFalse(self.c._recording_static_glass)
        self.assertEqual(set(self.executor.__dict__), {"compositor", "command_renderer", "clock"})

    def test_stale_storage_is_rejected_before_any_draw(self):
        self.assertIsNone(self.render(storage_generation=self.c.fbo_generation - 1))
        self.draws.static_hud.assert_not_called()
        self.draws.target_hud.assert_not_called()
        self.renderer.prepare_frame.assert_not_called()
        self.assertEqual(self.cache, LayerCacheSnapshot(None, None, False, 0.))

    def test_storage_replaced_during_atlas_preparation_never_publishes_cache(self):
        self.renderer.prepare_frame.side_effect = lambda *args: self.c.ensure_size(900, 600, 1.)
        motion_draw = Mock(return_value=True)
        self.draws = replace(self.draws, motion_hud=motion_draw)
        result = self.render(prepare_clickgui=True, scene=OverlayScene(False, True, False, False))
        self.assertIsNone(result)
        self.assertEqual(self.cache, LayerCacheSnapshot(None, None, False, 0.))
        self.renderer.prepare_frame.assert_called_once_with(self.frame.commands, 1.)
        motion_draw.assert_not_called()
        self.draws.target_hud.assert_not_called()

    def test_painter_failure_retains_original_and_independent_cleanup_faults(self):
        active = []
        def fail(painter):
            active.append(painter)
            raise TypeError("layer draw failed")
        self.draws = replace(self.draws, static_hud=fail)
        self.c.release_layer = Mock(side_effect=OSError("framebuffer release failed"))
        self.assertIsNone(self.render())
        self.assertFalse(active[0].isActive())
        self.assertEqual(self.c._glass_owner_stack, [compositor.GlassOwner.HUD])
        self.assertFalse(self.c._recording_static_glass)
        self.assertEqual(self.c.last_failed_frame.render_fault.error_type, "TypeError")
        self.assertEqual([error.error_type for error in self.c.last_failed_frame.cleanup_faults], ["OSError"])
        self.assertIn("layer draw failed", self.c.last_error)
        self.assertEqual(self.cache, LayerCacheSnapshot(None, None, False, 0.))

    def test_clickgui_layout_is_prepared_once_and_delivered_without_publication(self):
        received = []
        self.draws = replace(self.draws, motion_hud=lambda session, frame: received.append(frame) or True)
        result = self.render(prepare_clickgui=True, scene=OverlayScene(False, True, False, False))
        self.assertIsNotNone(result)
        self.draws.clickgui_frame.assert_called_once()
        self.renderer.prepare_frame.assert_called_once_with(self.frame.commands, 1.)
        self.assertIs(received[0].frame, self.frame)
        self.assertIs(received[0].commands, self.frame.commands)
        self.assertTrue(received[0].atlas_prepared)

    def test_dynamic_release_false_cannot_publish_motion_readiness(self):
        self.c.release_layer = Mock(side_effect=lambda layer: layer != "dynamic")
        self.assertIsNone(self.render())
        self.assertFalse(self.cache.motion_ready)
        self.assertEqual(self.cache.motion_updated_at, 0.)
        self.draws.target_hud.assert_not_called()
        self.assertEqual(self.c.diagnostics.faults, 0)

    def test_window_invalid_viewport_does_not_allocate_or_mutate_saved_metadata(self):
        self.fixture.events.clear()
        self.fixture.window.ratio = float("nan")
        events, result = self.fixture.run()
        self.assertFalse(result["accepted"])
        self.assertNotIn("ensure_size", [event[0] for event in events])
        self.assertNotIn("present", [event[0] for event in events])
        self.assertEqual(result["faults"], 0)

    def test_window_rejects_replaced_storage_without_present_or_restore_commit(self):
        runner = RecordedFrame("restore")
        try:
            original = runner.window.paint_hud_motion_overlay
            def replace_storage(*args, **kwargs):
                original(*args, **kwargs)
                runner.compositor.fbo_generation += 1
            runner.window.paint_hud_motion_overlay = replace_storage
            events, result = runner.run()
            self.assertFalse(result["accepted"])
            self.assertNotIn("present", [event[0] for event in events])
            self.assertNotIn("restore.commit", [event[0] for event in events])
            self.assertEqual(result["restore"], 7)
            self.assertIsNone(result["static_key"])
            self.assertFalse(result["motion_ready"])
            self.assertFalse(result["frame_recording"])
            self.assertIn("schedule.force", [event[0] for event in events])
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
