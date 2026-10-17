"""Frozen original scene methods: complete and interrupted drawing effects."""
import hashlib
from itertools import product
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from PySide6.QtWidgets import QApplication
from economy_scene_failure_fixture import BASELINE_PATH, run_scene

FIXTURE_SHA256 = "31b798cb502ad718020c5c17aca5a01b27bb408739b6035bd6fdebbd6681b99f"


class EconomySceneFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_frozen_scene_source_integrity(self):
        self.assertEqual(hashlib.sha256(BASELINE_PATH.read_bytes()).hexdigest(), FIXTURE_SHA256)

    def test_complete_draw_and_hotspot_order_with_selection_scroll_and_hold_state(self):
        for renderer, case, size, progress in product(
            ("gpu", "compat"), ("normal", "fallback_selection", "scrolled_reload", "hold_half", "hold_pending", "history"),
            ((1280, 720), (1920, 1080)), (.48, .84, 1.),
        ):
            args = dict(renderer=renderer, case=case, width=size[0], height=size[1], progress=progress)
            with self.subTest(**args):
                expected = run_scene(**args, frozen=True)
                self.assertIsNone(expected[-1])
                self.assertEqual(run_scene(**args), expected)

    def test_each_draw_font_animation_and_hotspot_failure_preserves_original_prefix(self):
        for renderer, case, size in product(("gpu", "compat"), ("fallback_selection", "scrolled_reload", "hold_half", "history"),
                                            ((1280, 720), (1920, 1080))):
            args = dict(renderer=renderer, case=case, width=size[0], height=size[1])
            original = run_scene(**args, frozen=True)
            self.assertIsNone(original[-1])
            for fail_at in range(1, len(original[0]) + 1):
                with self.subTest(**args, fail_at=fail_at):
                    self.assertEqual(run_scene(**args, fail_at=fail_at),
                                     run_scene(**args, fail_at=fail_at, frozen=True))


if __name__ == "__main__":
    unittest.main()
