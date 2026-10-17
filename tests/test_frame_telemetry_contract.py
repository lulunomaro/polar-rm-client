"""Characterize completed-frame telemetry independently of Qt/GL rendering.

The real paintGL facade runs with explicit terminal rendering ports. Pixel
production has separate real-QPainter and driver-limited regression suites.
"""
from __future__ import annotations

import ast
import math
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.ui.polar.tick_controller import TickDiagnostics
from rm_client.ui.polar.frame_telemetry import FrameTelemetry, FrameTelemetryField


def frame_method():
    tree = ast.parse((ROOT / "rm_client/ui/polar_app.py").read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PolarRMWindow")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "paintGL")
    module = ast.Module(body=[method], type_ignores=[])
    scope = {"time": time, "math": math}
    exec(compile(ast.fix_missing_locations(module), "production_paintGL", "exec"), scope)
    return scope["paintGL"]


PAINT = frame_method()


class FrameHost(NS):
    fps_samples = FrameTelemetryField("fps_samples")
    render_frame_ms_ema = FrameTelemetryField("ema_ms")
    _last_present_time = FrameTelemetryField("last_presented_at")
    _field_frame_times_ms = FrameTelemetryField("frame_times_ms")
    _last_field_frame_log_at = FrameTelemetryField("last_reported_at")


class FrameTelemetryContractTests(unittest.TestCase):
    def host(self):
        return FrameHost(
            _frame_telemetry=FrameTelemetry(),
            _window_lifecycle=NS(closing=False),
            paint_gpu_frame=Mock(return_value=True), _paint_compatibility_frame=Mock(),
            _gpu_warm_pending=False, _gpu_prewarm_jobs_pending=None,
            state=NS(fps=0.0),
            render_fps_target=144.0, backend=NS(field_log=NS(emit=Mock())),
            gpu_compositor=NS(last_background_ms=1.0, last_blur_ms=2.0,
                              last_glass_ms=3.0, last_overlay_ms=4.0,
                              last_dynamic_ms=5.0, last_present_ms=6.0, last_error=""),
            _tick_diagnostics=TickDiagnostics(last_ms=7.0, gap_ms=8.0),
            _gpu_prewarm_last_ms=9.0, _gpu_prewarm_max_ms=10.0,
        )

    def frame(self, host, started, finished):
        with patch.object(time, "perf_counter", side_effect=[started, finished]):
            PAINT(host)

    def test_fps_delta_clamps_reversal_and_long_pause(self):
        host = self.host()
        host._last_present_time = 1.0
        self.frame(host, .998, .999)
        self.assertEqual(host.state.fps, 1000.0)
        self.frame(host, 1.998, 2.0)
        self.assertEqual(host.fps_samples, [1000.0, 4.0])
        self.assertEqual(host.state.fps, 502.0)

    def test_fps_window_retains_only_the_latest_ninety_completed_frames(self):
        host = self.host()
        host.fps_samples = [1.0] * 90
        host._last_present_time = .99
        self.frame(host, .999, 1.0)
        self.assertEqual(len(host.fps_samples), 90)
        self.assertAlmostEqual(host.fps_samples[-1], 100.0)
        self.assertAlmostEqual(host.state.fps, (89.0 + 100.0) / 90.0)

    def test_ema_uses_ninety_ten_weight_and_also_tracks_compatibility_frames(self):
        host = self.host()
        self.frame(host, .998, 1.0)
        self.assertAlmostEqual(host.render_frame_ms_ema, 2.0)
        host.paint_gpu_frame.return_value = False
        self.frame(host, 1.99, 2.0)
        host._paint_compatibility_frame.assert_called_once()
        self.assertAlmostEqual(host.render_frame_ms_ema, 2.8)
        self.assertEqual(len(host._field_frame_times_ms), 2)

    def test_five_second_report_is_nearest_rank_p95_and_consumes_its_window(self):
        host = self.host()
        self.frame(host, 4.997, 4.999)
        host.backend.field_log.emit.assert_not_called()
        self.frame(host, 4.990, 5.0)
        call = host.backend.field_log.emit.call_args
        self.assertEqual(call.args, ("frame_timing",))
        metrics = call.kwargs
        self.assertEqual(metrics["samples"], 2)
        self.assertAlmostEqual(metrics["frame_ms_p95"], 10.0)
        self.assertAlmostEqual(metrics["frame_ms_max"], 10.0)
        self.assertAlmostEqual(metrics["frame_ms_ema"], 2.8)
        self.assertEqual(metrics["compositor_ms"], 6.0)
        self.assertEqual(metrics["last_pass_ms"]["present"], 6.0)
        self.assertEqual(metrics["ui_tick_ms"], 7.0)
        self.assertEqual(metrics["ui_wakeup_gap_ms"], 8.0)
        self.assertEqual(metrics["render_path"], "unified_gpu")
        self.assertEqual(host._field_frame_times_ms, [])
        self.assertEqual(host._last_field_frame_log_at, 5.0)

    def test_frame_duration_storage_is_bounded_to_seven_hundred_twenty_samples(self):
        host = self.host()
        host._field_frame_times_ms = list(range(1000))
        self.frame(host, 4.998, 5.0)
        metrics = host.backend.field_log.emit.call_args.kwargs
        self.assertEqual(metrics["samples"], 720)
        expected = sorted([*range(281, 1000), 2.0])
        self.assertEqual(metrics["frame_ms_p95"], expected[math.ceil(720 * .95) - 1])
        self.assertEqual(metrics["frame_ms_max"], 999)

    def test_failed_sink_retains_samples_for_a_later_successful_report(self):
        host = self.host()
        host.backend.field_log.emit.side_effect = OSError("telemetry sink unavailable")
        with self.assertRaisesRegex(OSError, "telemetry sink"):
            self.frame(host, 4.998, 5.0)
        self.assertEqual(len(host._field_frame_times_ms), 1)
        self.assertEqual(host._last_field_frame_log_at, 0.0)
        host.backend.field_log.emit.side_effect = None
        self.frame(host, 5.099, 5.1)
        self.assertEqual(host.backend.field_log.emit.call_args.kwargs["samples"], 2)
        self.assertEqual(host._field_frame_times_ms, [])
        self.assertEqual(host._last_field_frame_log_at, 5.1)


if __name__ == "__main__":
    unittest.main()
