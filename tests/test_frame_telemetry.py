"""Pure owner contracts for bounded presentation measurements."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.frame_telemetry import FrameTelemetry, FrameTelemetryField


class FrameTelemetryTests(unittest.TestCase):
    def test_instances_never_share_mutable_sample_windows(self):
        first, second = FrameTelemetry(), FrameTelemetry()
        first.record_frame(.998, 1.0)
        self.assertEqual(second.fps_samples, [])
        self.assertEqual(second.frame_times_ms, [])
        self.assertEqual(second.ema_ms, 0)
        self.assertEqual(second.last_presented_at, 0)

    def test_due_report_is_immutable_and_does_not_consume_before_acknowledgement(self):
        owner = FrameTelemetry()
        frame = owner.record_frame(4.998, 5.0)
        report = frame.report
        self.assertIsNotNone(report)
        self.assertEqual(len(owner.frame_times_ms), 1)
        self.assertEqual(owner.last_reported_at, 0)
        with self.assertRaises(FrozenInstanceError):
            report.samples = 5
        metrics = report.metrics()
        metrics["samples"] = 100
        self.assertEqual(report.metrics()["samples"], 1)
        owner.acknowledge_report(report)
        self.assertEqual(owner.frame_times_ms, [])
        self.assertEqual(owner.last_reported_at, 5)
        self.assertEqual(owner.fps_samples, [4.0])
        self.assertAlmostEqual(owner.ema_ms, 2)

    def test_unacknowledged_report_is_resampled_with_the_next_completed_frame(self):
        owner = FrameTelemetry()
        failed = owner.record_frame(4.998, 5.0).report
        retry = owner.record_frame(5.099, 5.1).report
        self.assertEqual(failed.samples, 1)
        self.assertEqual(retry.samples, 2)
        self.assertAlmostEqual(retry.ema_ms, 1.9)
        self.assertEqual(owner.last_reported_at, 0)
        owner.acknowledge_report(retry)
        self.assertEqual(owner.last_reported_at, 5.1)
        self.assertEqual(len(owner.fps_samples), 2)

    def test_reporting_clock_starts_at_the_supplied_initialization_boundary(self):
        owner = FrameTelemetry(last_presented_at=10, last_reported_at=10)
        self.assertIsNone(owner.record_frame(14.997, 14.999).report)
        report = owner.record_frame(14.998, 15).report
        self.assertEqual(report.samples, 2)
        owner.acknowledge_report(report)
        self.assertIsNone(owner.record_frame(19.997, 19.999).report)
        self.assertEqual(owner.record_frame(19.998, 20).report.samples, 2)

    def test_explicit_compatibility_aliases_refer_to_the_single_owner(self):
        class Host(SimpleNamespace):
            fps_samples = FrameTelemetryField("fps_samples")
            render_frame_ms_ema = FrameTelemetryField("ema_ms")
        host = Host(_frame_telemetry=FrameTelemetry())
        host.fps_samples = [60.0]
        host.render_frame_ms_ema = 5.0
        self.assertIs(host.fps_samples, host._frame_telemetry.fps_samples)
        self.assertEqual(host._frame_telemetry.ema_ms, 5.0)
        host._frame_telemetry.record_frame(.998, 1.0)
        self.assertAlmostEqual(host.render_frame_ms_ema, 4.7)
        self.assertNotIn("fps_samples", vars(host))
        with self.assertRaisesRegex(ValueError, "Unknown frame telemetry"):
            FrameTelemetryField("window")


if __name__ == "__main__":
    unittest.main()
