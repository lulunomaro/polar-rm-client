"""Rejected native command batches cannot certify a complete cached frame.

Calls real window flush/frame methods and real renderer fault handling. Native
GL calls remain inert; this establishes result ownership, not driver pixels.
"""
from __future__ import annotations

import ast
import hashlib
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

from rm_client.ui.clickgui_gpu import ClickGuiDrawList
from rm_client.ui.gpu_frame_compositor import GpuFrameCompositor
from rm_client.ui.polar.primitives import EconomyScenePhase
from rm_client.ui.polar_app import PolarRMWindow
import test_clickgui_renderer_faults as renderer_faults
from test_cached_window_layers import RasterWindowFrame


class CommandRejectionTests(unittest.TestCase):
    def test_original_flush_reproducer_is_preserved(self):
        runner = RasterWindowFrame("hud", original=True)
        try:
            runner.fail_commands = True
            result = runner.frame()
            self.assertTrue(runner.failed)
            self.assertTrue(result["accepted"], "frozen baseline must retain its demonstrated defect")
            self.assertTrue(result["motion_ready"])
            self.assertIn("present", [call[0] for call in result["calls"]])
        finally:
            runner.close()

    def test_actual_hud_false_rejects_cache_present_and_restore_then_recovers(self):
        runner = RasterWindowFrame("hud")
        try:
            w = runner.window
            w.economy_phase = EconomyScenePhase.CLOSING
            w._economy_generation = 3
            w._economy_restore_pending_generation = 3
            w._economy_closing_generation = 3
            preference = w.registry.get("ClickGui").get_setting("Render Backend")
            saved_choice = preference.value
            runner.fail_commands = True
            result = runner.frame()
            self.assertTrue(runner.failed)
            self.assertFalse(result["accepted"])
            self.assertFalse(result["motion_ready"])
            self.assertEqual(result["motion_at"], 0.)
            self.assertIsNone(result["static_key"])
            self.assertNotIn("present", [call[0] for call in result["calls"]])
            self.assertEqual(result["restore_pending"], 3)
            self.assertEqual(result["economy_phase"], EconomyScenePhase.CLOSING)
            self.assertEqual(result["faults"], 0)
            self.assertEqual(preference.value, saved_choice)
            runner.fail_commands = False
            result = runner.frame()
            self.assertTrue(result["accepted"])
            self.assertTrue(result["motion_ready"])
            self.assertIsNone(result["restore_pending"])
            self.assertEqual(result["economy_phase"], EconomyScenePhase.CLOSED)
            self.assertEqual(preference.value, saved_choice)
        finally:
            runner.close()

    def test_real_renderer_internal_fault_keeps_original_diagnostics_and_rejects_frame(self):
        fixture = renderer_faults.ActualRendererFaultTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        renderer = fixture.renderer
        renderer._draw_ordered_commands.side_effect = TypeError("malformed HUD command")
        compositor = GpuFrameCompositor()
        commands = ClickGuiDrawList()
        commands.commands.append(object())
        window = NS(_gpu_hud_commands=commands, _collect_gpu_hud_commands=True,
                    clickgui_gpu=renderer, gpu_compositor=compositor)
        # Preparation is an external native-atlas boundary in this fault test;
        # render/_draw_ordered_commands is the actual exception-catching path.
        renderer.prepare_frame = Mock()
        self.assertIsNone(PolarRMWindow.flush_gpu_hud_commands(window, fixture.functions, 800, 600, 1.))
        self.assertTrue(compositor.frame_rejected)
        self.assertFalse(window._collect_gpu_hud_commands)
        self.assertFalse(window._gpu_hud_commands.commands)
        self.assertEqual(renderer.diagnostics.faults, 1)
        self.assertEqual(renderer.diagnostics.last_fault.phase, "commands.render")
        self.assertEqual(renderer.diagnostics.last_fault.error_type, "TypeError")
        self.assertIn("malformed HUD command", renderer.last_error)
        recovery = compositor.recover_rejected_frame()
        self.assertIn("malformed HUD command", recovery.reason)
        self.assertEqual(compositor.diagnostics.faults, 0)
        self.assertEqual(renderer.diagnostics.faults, 1)

    def test_specialized_flush_false_results_mark_frame_and_clear_collections(self):
        for stage in ("island", "wheel", "wheel_labels"):
            with self.subTest(stage=stage):
                compositor = GpuFrameCompositor()
                compositor.background_fbo = NS(texture=lambda: 17)
                compositor.blur_fbo = None
                commands = ClickGuiDrawList()
                commands.commands.append(object())
                window = NS(
                    gpu_compositor=compositor,
                    _gpu_island_icon_commands=[object()], _collect_gpu_island_icons=True,
                    island_icon_gpu=NS(available=True, render=Mock(return_value=False), last_error="icon unavailable"),
                    _gpu_quick_action_wheel_command=object(), _gpu_quick_action_wheel_labels=commands,
                    _collect_gpu_quick_action_wheel=True,
                    quick_action_wheel_gpu=NS(available=True, render=Mock(return_value=stage != "wheel"), last_error="wheel unavailable"),
                    clickgui_gpu=NS(available=True, prepare_frame=Mock(), render=Mock(return_value=False), last_error="label unavailable"),
                )
                if stage == "island":
                    result = PolarRMWindow.flush_gpu_island_icons(window, None, 800, 600, 1.)
                    self.assertFalse(window._gpu_island_icon_commands)
                    self.assertFalse(window._collect_gpu_island_icons)
                else:
                    result = PolarRMWindow.flush_gpu_quick_action_wheel(window, None, 800, 600, 1.)
                    self.assertIsNone(window._gpu_quick_action_wheel_command)
                    self.assertFalse(window._gpu_quick_action_wheel_labels.commands)
                    self.assertFalse(window._collect_gpu_quick_action_wheel)
                    if stage == "wheel":
                        window.clickgui_gpu.render.assert_not_called()
                self.assertIsNone(result, "historical flush return contract remains None")
                self.assertTrue(compositor.frame_rejected)
                self.assertEqual(compositor.diagnostics.faults, 0)

    def test_first_child_rejection_remains_visible_until_the_next_frame(self):
        compositor = GpuFrameCompositor()
        compositor.reject_frame("HUD command render rejected", detail="missing atlas")
        compositor.reject_frame("island icon render rejected", detail="later rejection")
        self.assertTrue(compositor.frame_rejected)
        recovery = compositor.recover_rejected_frame()
        self.assertEqual(recovery.reason, "HUD command render rejected: missing atlas")
        self.assertEqual(compositor.diagnostics.faults, 0)
        compositor.begin_frame()
        self.assertFalse(compositor.frame_rejected)
        self.assertIs(compositor.last_rejected_frame, recovery)

    def test_child_rejection_survives_real_layer_release_rejection(self):
        runner = RasterWindowFrame("hud")
        try:
            runner.fail_commands = True
            acquire = runner.c.paint_device
            release = None
            def acquire_with_rejected_release(layer):
                nonlocal release
                device = acquire(layer)
                if layer == "dynamic":
                    release = Mock(return_value=False)
                    runner.c.dynamic_fbo.release = release
                return device
            with patch.object(runner.c, "paint_device", side_effect=acquire_with_rejected_release):
                result = runner.frame()
            self.assertFalse(result["accepted"])
            self.assertFalse(result["motion_ready"])
            self.assertNotIn("present", [call[0] for call in result["calls"]])
            self.assertGreaterEqual(release.call_count, 2)
            recovery = runner.c.last_rejected_frame
            self.assertEqual(recovery.reason, "HUD command render rejected")
            self.assertEqual(recovery.rejected_layers, ("dynamic",))
            self.assertEqual(result["faults"], 0)
            release.return_value = True
            runner.fail_commands = False
            self.assertTrue(runner.frame()["accepted"])
        finally:
            runner.close()

    def test_vision_rejection_preserves_first_child_cause_and_cleanup_result(self):
        for prior_child in (False, True):
            with self.subTest(prior_child=prior_child):
                compositor = GpuFrameCompositor()
                compositor.vision_fbo = NS(bind=Mock(return_value=False), release=Mock(return_value=False))
                if prior_child:
                    compositor.reject_frame("earlier child rejected")
                self.assertFalse(compositor.begin_vision_layer(None))
                recovery = compositor.recover_rejected_frame()
                expected = "earlier child rejected" if prior_child else "vision framebuffer begin rejected"
                self.assertEqual(recovery.reason, expected)
                self.assertEqual(recovery.rejected_layers, ("vision",))
                self.assertEqual(compositor.diagnostics.faults, 0)

    def test_flush_reference_bodies_are_frozen(self):
        source = (ROOT / "tests/fixtures/cached_frame_baseline.py").read_text()
        expected = {'flush_gpu_hud_commands': '760b3c4a25203b9f5d42e9e8e126f2a0bc898cb495f4173a33cb6cfddecc25dd', 'flush_gpu_island_icons': 'a3108b0fe7586ce2c81ff580442711bd49735a6764e207cc38d6cde9c09a84f5', 'flush_gpu_quick_action_wheel': '76ebb114502743e1acd3ac4c623425b227844a6123375823a3746b54b2aa1720'}
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name in expected:
                body = "\n".join(source.splitlines()[node.lineno-1:node.end_lineno]) + "\n"
                self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), expected.pop(node.name))
        self.assertEqual(expected, {})


if __name__ == "__main__":
    unittest.main()
