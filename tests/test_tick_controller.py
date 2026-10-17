"""Clock and failure policies execute without Qt, backend devices or a window."""
from pathlib import Path
import sys
import logging
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.tick_controller import (
    FrameTiming, RenderCadence, RuntimeTickPlan, TickDiagnosticField, TickDiagnostics, TickErrors, TickStageRunner,
)


class TickClockTests(unittest.TestCase):
    def test_ui_dt_handles_clock_reversal_and_suspend(self):
        for now, previous, elapsed, dt in ((1., 2., 0., .001), (2., 2., 0., .001), (2.12, 2., .12, .12), (20., 2., 18., 1.)):
            with self.subTest(now=now, previous=previous):
                frame = FrameTiming.since(now, previous)
                self.assertAlmostEqual(frame.elapsed, elapsed)
                self.assertAlmostEqual(frame.ui_dt, dt)
                self.assertAlmostEqual(frame.gap_ms, elapsed * 1000)

    def test_control_and_logic_due_boundaries_preserve_early_allowance(self):
        control = (1 / 75) * .92
        logic = (1 / 60) * .92
        self.assertFalse(RuntimeTickPlan.at(control - 1e-9, 0, 0).control_due)
        self.assertTrue(RuntimeTickPlan.at(control, 0, 0).control_due)
        self.assertIsNone(RuntimeTickPlan.at(logic - 1e-9, 0, 0).logic_dt)
        self.assertEqual(RuntimeTickPlan.at(logic, 0, 0).logic_dt, logic)
        self.assertEqual(RuntimeTickPlan.at(3600, 0, 0).logic_dt, .05)

    def test_scheduler_gates_can_be_pinned_ahead_for_golden_capture(self):
        self.assertEqual(RuntimeTickPlan.at(2, 10, 10), RuntimeTickPlan(False, None))

    def test_video_preferences_control_paint_rate_but_not_event_clock(self):
        preference = Mock(return_value=30.)
        cadence = RenderCadence.resolve(144, 240., preference)
        preference.assert_called_once_with(144., 240.)
        self.assertEqual(cadence, RenderCadence(30., 8))

    def test_refresh_input_coercion_and_cap_are_stable(self):
        for requested, cap, target, interval in (("240", 240., 240., 4), ("bad", 240., 144., 7), (None, 120., 120., 8), (30, 240., 120., 8)):
            self.assertEqual(RenderCadence.resolve(requested, cap, lambda value, limit: value), RenderCadence(target, interval))


class TickDiagnosticsTests(unittest.TestCase):
    def test_instances_never_share_mutable_error_or_timing_state(self):
        first, second = TickDiagnostics(), TickDiagnostics()
        first.last_logged_at["backend"] = 8.0
        first.stages["backend"] = {"count": 1, "total_ms": 4.0, "max_ms": 4.0}
        first.record_wakeup(21.0)
        self.assertEqual(second.last_logged_at, {})
        self.assertEqual(second.stages, {})
        self.assertEqual(second.gap_ms, 0.0)

    def test_report_window_consumes_peaks_and_stages_but_keeps_latest_samples(self):
        owner = TickDiagnostics(reported_at=10.0)
        owner.record_wakeup(40.0)
        self.assertIsNone(owner.finish_tick(11.0, 11.008))
        owner.record_wakeup(3.0)
        self.assertIsNone(owner.finish_tick(14.999, 15.0))
        report = owner.finish_tick(15.0, 15.002)
        self.assertAlmostEqual(report.tick_ms, 2.0)
        self.assertAlmostEqual(report.tick_max_ms, 8.0)
        self.assertEqual((report.wakeup_gap_ms, report.wakeup_gap_max_ms), (3.0, 40.0))
        self.assertAlmostEqual(owner.last_ms, 2.0)
        self.assertEqual((owner.gap_ms, owner.max_ms, owner.gap_max_ms), (3.0, 0.0, 0.0))
        self.assertEqual(owner.reported_at, 15.0)
        self.assertIsNone(owner.finish_tick(19.999, 20.0))
        self.assertIsNotNone(owner.finish_tick(20.0, 20.001))

    def test_report_is_immutable_and_each_sink_gets_fresh_nested_metrics(self):
        owner = TickDiagnostics()
        stage = {"count": 2, "total_ms": 8.0, "max_ms": 5.0}
        owner.stages["backend"] = stage
        report = owner.finish_tick(10.0, 10.002)
        stage["count"] = 99
        first = report.metrics()
        first["stages"]["backend"]["count"] = 20
        self.assertEqual(report.metrics()["stages"]["backend"]["count"], 2)
        self.assertEqual(owner.stages, {})
        with self.assertRaises(AttributeError):
            report.tick_ms = 100.0

    def test_stage_fault_throttle_survives_reporting_and_successful_stages(self):
        owner = TickDiagnostics()
        stats, emit, logger = {}, Mock(), Mock(spec=logging.Logger)
        clock = Mock(return_value=10.0)
        runner = TickStageRunner(stats, emit, logger, clock)
        fail = Mock(side_effect=TypeError("packet"))
        for now in (10.0, 10.2):
            self.assertFalse(owner.run_stage(runner, "backend", fail, now=now))
        self.assertIsNotNone(owner.finish_tick(10.2, 10.201))
        self.assertTrue(owner.run_stage(runner, "backend", lambda: None, now=10.3))
        self.assertFalse(owner.run_stage(runner, "backend", fail, now=10.9))
        self.assertFalse(owner.run_stage(runner, "backend", fail, now=11.0))
        self.assertEqual(owner.count, 4)
        self.assertEqual(owner.last_error, "backend: TypeError: packet")
        self.assertEqual(emit.call_count, 2)
        self.assertEqual(logger.exception.call_count, 2)
        self.assertEqual(owner.stages["backend"]["count"], 3)
        self.assertEqual(owner.last_logged_at, {"backend": 11.0})

    def test_compatibility_field_assignments_reach_the_same_owner(self):
        class Host:
            errors = TickDiagnosticField("count")
            stages = TickDiagnosticField("stages")
            def __init__(self):
                self._tick_diagnostics = TickDiagnostics()
        host = Host()
        host.errors = 9
        host.stages = {"backend": {"count": 4}}
        self.assertEqual(host._tick_diagnostics.count, 9)
        self.assertIs(host.stages, host._tick_diagnostics.stages)
        host._tick_diagnostics.count += 1
        self.assertEqual(host.errors, 10)
        with self.assertRaises(ValueError):
            TickDiagnosticField("window")


class TickFailureTests(unittest.TestCase):
    def setUp(self):
        self.stats = {}
        self.emitter = Mock()
        self.logger = Mock(spec=logging.Logger)
        self.runner = TickStageRunner(self.stats, self.emitter, self.logger, Mock(side_effect=[10., 10.004]))
        self.errors = TickErrors(0, "", {})
        self.timings = {}

    def test_success_collects_duration_without_emitting_error(self):
        callback = Mock()
        self.assertTrue(self.runner.run("control", callback, errors=self.errors, timings=self.timings, now=10.))
        callback.assert_called_once_with()
        self.emitter.assert_not_called()
        self.assertAlmostEqual(self.timings["control"]["total_ms"], 4.)
        self.assertEqual(self.timings["control"]["count"], 1)
        self.assertEqual(self.errors.count, 0)

    def test_failure_counts_once_and_records_original_error(self):
        callback = Mock(side_effect=ValueError("packet"))
        self.assertFalse(self.runner.run("backend", callback, errors=self.errors, timings=self.timings, now=10.))
        self.assertEqual(self.errors.last_error, "backend: ValueError: packet")
        self.assertEqual(self.stats["ui_tick"], {"errors": 1, "last_error": self.errors.last_error})
        self.emitter.assert_called_once_with("thread_exception", stage="backend", error_type="ValueError", error="packet", count=1)
        self.assertEqual(self.timings["backend"]["count"], 1)

    def test_failed_error_sink_is_contained_and_original_error_survives(self):
        self.emitter.side_effect = OSError("log")
        self.assertFalse(self.runner.run("backend", Mock(side_effect=RuntimeError("packet")), errors=self.errors, now=10.))
        self.assertEqual(self.errors.last_error, "backend: RuntimeError: packet")
        self.logger.warning.assert_called_once()

    def test_base_exceptions_are_not_swallowed_but_timing_is_collected(self):
        with self.assertRaises(KeyboardInterrupt):
            self.runner.run("shutdown", Mock(side_effect=KeyboardInterrupt()), errors=self.errors, timings=self.timings, now=10.)
        self.assertEqual(self.errors.count, 0)
        self.assertEqual(self.timings["shutdown"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
