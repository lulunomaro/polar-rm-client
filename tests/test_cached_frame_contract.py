"""Frozen pre-refactor order and state for the real window frame entry.

Raster FBOs and production pass sessions execute here. Driver allocations,
source/device drawing, and command submission are controlled external edges.
Native GL pixels, performance and physical-device qualification are excluded.
"""
from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cached_frame_trace_support import RecordedFrame


SCENARIOS = (
    "hud", "startup", "startup_transition", "economy", "economy_fade",
    "economy_compat", "clickgui", "clickgui_fade", "clickgui_compat",
    "radial", "chat", "student", "escape", "restore", "stale_restore",
    "defer_static",
)


class CachedFrameCharacterizationTests(unittest.TestCase):
    def compare(self, scenario, *, fail_at=None, rejection=False, repeat=1):
        outputs = []
        for original in (True, False):
            runner = RecordedFrame(scenario, original=original,
                                   fail_at=fail_at, rejection=rejection)
            try:
                outputs.append([runner.run() for _ in range(repeat)])
            finally:
                runner.close()
        self.assertEqual(outputs[0], outputs[1], (scenario, fail_at, rejection))
        return outputs[1]

    def test_first_and_cached_frames_keep_order_geometry_and_metadata(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                outputs = self.compare(scenario, repeat=3)
                self.assertTrue(all(result["accepted"] is True for _, result in outputs))

    def test_faulted_frames_retain_partial_order_cleanup_and_pending_generation(self):
        for scenario, stage in (
            ("hud", "background"), ("hud", "control"), ("hud", "vision"),
            ("hud", "hud.static"), ("hud", "hud.motion"),
            ("hud", "flush_gpu_hud_commands"), ("hud", "flush_gpu_island_icons"),
            ("hud", "flush_gpu_quick_action_wheel"), ("hud", "target"),
            ("hud", "prepare_blur"), ("hud", "render_glass"),
            ("hud", "prepare_treated_backdrop"), ("hud", "present"),
            ("clickgui", "clickgui.layout"), ("clickgui", "atlas.prepare"),
            ("clickgui", "clickgui.paint"), ("radial", "priority"),
            ("economy", "economy.layout"), ("economy", "commands.render"),
            ("startup", "startup.layout"), ("startup", "commands.render"),
            ("startup", "startup.interaction"), ("restore", "restore.commit"),
        ):
            with self.subTest(scenario=scenario, stage=stage):
                outputs = self.compare(scenario, fail_at=stage, repeat=2)
                self.assertFalse(outputs[0][1]["accepted"])
                self.assertTrue(outputs[1][1]["accepted"])

    def test_false_results_reject_without_present_or_fabricated_exception(self):
        for scenario, stage in (
            ("hud", "clear_target_layer"), ("hud", "background"),
            ("hud", "target"), ("hud", "prepare_blur"),
            ("hud", "render_glass"), ("hud", "prepare_treated_backdrop"),
            ("hud", "present"), ("economy", "commands.render"),
            ("startup", "commands.render"), ("startup", "startup.interaction"),
            ("radial", "priority"), ("restore", "clear_scene_layers"),
        ):
            with self.subTest(scenario=scenario, stage=stage):
                outputs = self.compare(scenario, fail_at=stage, rejection=True, repeat=2)
                self.assertFalse(outputs[0][1]["accepted"])
                self.assertEqual(outputs[0][1]["faults"], 0)
                self.assertTrue(outputs[1][1]["accepted"])

    def test_reference_method_source_is_frozen(self):
        source = (ROOT / "tests/fixtures/cached_frame_baseline.py").read_text()
        tree = ast.parse(source)
        method = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "_paint_gpu_frame_cached")
        body = "\n".join(source.splitlines()[method.lineno-1:method.end_lineno]) + "\n"
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), "848078e60c8b38def230959323f53363db4cd067746a555b9e090f68185e0de4")


if __name__ == "__main__":
    unittest.main()
