"""Economy presentation contracts; Qt values/mock submission, no hardware GL."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from economy_render_fixture import EconomyHost, compat_trace, gpu_trace

BASELINE = Path(__file__).with_name("fixtures") / "economy_render_traces.json"
SIZES = ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440))
PROGRESS = (0.0, 0.15, 0.48, 0.62, 0.72, 0.84, 1.0, 1.08)


def digest(trace):
    encoded = json.dumps(trace, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def varied_host(case):
    host = EconomyHost()
    if case.startswith("hold"):
        host.offers[0] = replace(host.offers[0], confirmation="hold")
        if case != "hold_unpressed":
            host.economy_hold_key = host.offers[0].key
            host.economy_hold_started = 99.7
        if case == "hold_pending":
            host.economy_bridge.latest = NS(status="pending", request=NS(cost=10))
        if case == "hold_disabled":
            host.offers[0] = replace(host.offers[0], enabled=False, reason="条件不足")
    elif case == "armed":
        host.economy_armed_key = host.offers[0].key
        host.economy_armed_until = 101.5
    elif case == "status_only":
        host.offers[0] = replace(host.offers[0], status_only=True, reason="状态展示")
    elif case == "history":
        host.economy_bridge.history = [NS(status=status, offer=host.offers[i], message="交易消息")
                                       for i, status in enumerate(("succeeded", "timed_out", "pending"))]
    elif case == "scrolled_reload":
        host.economy_offers_scroll.position = 35.0
        host.economy_offers_scroll.target = 35.0
        host.economy_detail_scroll.position = 40.0
        host.economy_detail_scroll.target = 40.0
        host.student_reload_expanded = True
    elif case == "empty":
        host.offers = []
    elif case == "fallback_selection":
        host.economy_category = "removed-category"
        host.economy_selected_key = "removed-offer"
    return host


def render_baseline():
    result = {}
    for width, height in SIZES:
        for progress in PROGRESS:
            key = f"{width}x{height}:{progress}"
            result["gpu:" + key] = digest(gpu_trace(EconomyHost(progress=progress), width, height))
            result["compat:" + key] = digest(compat_trace(EconomyHost(progress=progress), width, height, progress))
    for case in ("hold_unpressed", "hold_half", "hold_pending", "hold_disabled", "armed", "status_only",
                 "history", "scrolled_reload", "empty", "fallback_selection"):
        result["gpu:" + case] = digest(gpu_trace(varied_host(case), 1600, 900))
        result["compat:" + case] = digest(compat_trace(varied_host(case), 1600, 900, 1.0))
    return result


class AdapterTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_ordered_draw_commands_and_materials_match_frozen_reference(self):
        expected = json.loads(BASELINE.read_text(encoding="utf-8"))
        actual = render_baseline()
        self.assertEqual(actual.keys(), expected["traces"].keys())
        for scenario, trace in actual.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(trace, expected["traces"][scenario])

    def test_compat_row_failure_restores_temporary_hotspot_clip(self):
        from PySide6.QtCore import QRectF
        for previous_clip in (None, QRectF(1., 2., 3., 4.)):
            host = EconomyHost()
            host._hotspot_clip = previous_clip
            painters = []
            def fail_row(painter, *args, **kwargs):
                painters.append(painter)
                raise RuntimeError("injected row-render failure")
            host._economy_glass_row = fail_row
            with self.assertRaisesRegex(RuntimeError, "injected row-render failure"):
                compat_trace(host, 1600, 900, 1.)
            self.assertIs(host._hotspot_clip, previous_clip)
            # The list's own saved painter state is unwound; the outer frame
            # retains ownership of its separate, already-active paint scope.
            self.assertEqual(painters[0].calls[-1], ("restore",))

    def test_compat_restore_failure_does_not_mask_original_drawing_error(self):
        host = EconomyHost()
        def fail_row(painter, *args, **kwargs):
            def fail_restore():
                raise RuntimeError("injected restore failure")
            painter.restore = fail_restore
            raise ValueError("original drawing error")
        host._economy_glass_row = fail_row
        with self.assertLogs("rm_client.ui.polar.mixins.economy_mixin", level="ERROR") as logs:
            with self.assertRaisesRegex(ValueError, "original drawing error"):
                compat_trace(host, 1600, 900, 1.)
        self.assertIsNone(host._hotspot_clip)
        self.assertIn("injected restore failure", "\n".join(logs.output))

    def test_compat_restore_failure_without_drawing_error_is_reported(self):
        host = EconomyHost()
        def draw_row(painter, *args, **kwargs):
            def fail_restore():
                raise RuntimeError("injected restore failure")
            painter.restore = fail_restore
        host._economy_glass_row = draw_row
        with self.assertRaisesRegex(RuntimeError, "injected restore failure"):
            compat_trace(host, 1600, 900, 1.)
        self.assertIsNone(host._hotspot_clip)

    def test_caller_exception_does_not_hide_new_cleanup_failure(self):
        host = EconomyHost()
        def draw_row(painter, *args, **kwargs):
            def fail_restore():
                raise RuntimeError("new restore failure")
            painter.restore = fail_restore
        host._economy_glass_row = draw_row
        try:
            raise LookupError("upstream GPU fallback")
        except LookupError:
            with self.assertRaisesRegex(RuntimeError, "new restore failure"):
                compat_trace(host, 1600, 900, 1.)
        self.assertIsNone(host._hotspot_clip)

    def test_scrolled_gpu_offer_hitboxes_stay_inside_visible_viewport(self):
        host = EconomyHost()
        host.economy_offers_scroll.position = 35.0
        host.economy_offers_scroll.target = 35.0
        gpu_trace(host, 1600, 900)
        layout = host.economy_workspace_layer.layout(1600, 900, 1.0)
        viewport = layout.offers_viewport.translated(0.0, layout.offers_offset).intersected(layout.body_clip)
        offers = [(rect, key) for rect, action, key, *_ in host.hotspots if action == "economy_offer"]
        self.assertTrue(offers)
        for rect, key in offers:
            self.assertTrue(viewport.contains(rect), f"invisible hit area for {key}: {rect} outside {viewport}")

    def test_gpu_reveal_hotspots_match_their_clipped_visual_regions(self):
        for width, height in SIZES:
            for progress in (.72, .78, .84, .92, 1.0, 1.08):
                with self.subTest(size=(width, height), progress=progress):
                    host = EconomyHost(progress=progress)
                    gpu_trace(host, width, height)
                    layout = host.economy_workspace_layer.layout(width, height, progress)
                    clips = {
                        "economy_offer": layout.offers_viewport.translated(0.0, layout.offers_offset),
                        "economy_confirm": layout.detail_pane.translated(0.0, layout.detail_offset),
                        "economy_category": layout.category_pane if layout.three_column else layout.tab_bar,
                    }
                    for rect, action, *_ in host.hotspots:
                        if action in clips:
                            self.assertTrue(clips[action].intersected(layout.body_clip).contains(rect), action)

    def test_closed_and_pending_gpu_views_do_not_expose_purchase_actions(self):
        from types import SimpleNamespace as NS
        for visible, pending, enabled in ((False, False, True), (True, True, True), (True, False, False)):
            host = EconomyHost()
            host.economy_visible = visible
            if pending:
                host.economy_bridge.latest = NS(status="pending", request=NS(cost=10))
            if not enabled:
                host.offers[0] = replace(host.offers[0], enabled=False, reason="条件不足")
            gpu_trace(host, 1600, 900)
            self.assertFalse(any(action.startswith("economy_confirm") for _, action, *_ in host.hotspots))


if __name__ == "__main__":
    unittest.main()
