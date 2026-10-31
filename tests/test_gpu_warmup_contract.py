"""Real window warmup wrappers preserve context, queue and failure ordering."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ["CI"] = "true"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from rm_client.backend.mqtt_backend import RmMqttBackend
from rm_client.backend.serial_backend import RmSerialBackend
from rm_client.backend.video_backend import RmVideoBackend
from rm_client.ui import polar_app
from rm_client.ui.polar_app import PolarRMWindow
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])


class GpuWarmupContractTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {
            "POLAR_USER_DATA_ROOT": directory, "POLAR_AUTOSTART": "0",
        }))
        for owner in (RmMqttBackend, RmSerialBackend, RmVideoBackend):
            self.stack.enter_context(patch.object(owner, "start"))
        self.window = PolarRMWindow()
        self.addCleanup(self.close_window)
        settle_window_lifecycle(self.window, APP)
        self.window.timer.stop()

    def close_window(self):
        close_window_safely(self.window, APP)
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def run_slice(self, *, kind="text", failure_at=None, slow_before_upload=False,
                  job_exception=None, paused=None, exhausted=False):
        window = self.window
        events = []
        failure = RuntimeError("warmup boundary failed")
        def record(name):
            events.append((name, window._gpu_prewarm_jobs_pending is jobs,
                           window._gpu_prewarm_last_ms, window._gpu_prewarm_max_ms))
            if name == failure_at:
                raise failure
        def pending_jobs():
            record("next-job")
            if job_exception:
                raise job_exception
            if exhausted:
                return
            yield kind, ("payload",), {"option": "value"}
            record("second-job")
            yield "text", ("second",), {}
        jobs = pending_jobs()
        window._gpu_prewarm_jobs_pending = jobs
        window._gpu_prewarm_last_ms, window._gpu_prewarm_max_ms = 2., 4.
        clock_values = iter((100., 100.004 if slow_before_upload else 100.001, 100.005))
        clock_calls = 0
        def clock():
            nonlocal clock_calls
            clock_calls += 1
            record(f"clock-{clock_calls}")
            return next(clock_values)
        def text_upload(*args, **kwargs):
            self.assertEqual(args, ("payload",))
            self.assertEqual(kwargs, {"option": "value"})
            record("text")
        def image_upload(*args):
            self.assertEqual(args, ("payload",))
            record("image")
        def report(event, **fields):
            self.assertEqual(event, "gpu_prewarm_error")
            self.assertEqual(fields, {"error": str(job_exception or failure)})
            record("report")
        def timer(interval):
            self.assertEqual(interval, 16)
            record("timer")
        with ExitStack() as patches:
            # This suite exercises the legacy indivisible-upload port; the
            # incremental glyph port has its own resumable-owner tests.
            patches.enter_context(patch.object(window.clickgui_gpu, "prewarm_steps", None))
            patches.enter_context(patch.object(window._window_lifecycle, "closing", paused == "closing"))
            patches.enter_context(patch.object(window.gpu_compositor, "available", paused != "unavailable"))
            patches.enter_context(patch.object(window, "isVisible", lambda: paused != "hidden"))
            patches.enter_context(patch.object(window, "context", lambda: None if paused == "no-context"
                                               else SimpleNamespace(isValid=lambda: paused != "invalid-context")))
            patches.enter_context(patch.object(type(window), "escape_menu_visible", property(lambda self: paused == "escape")))
            patches.enter_context(patch.object(QApplication, "activeModalWidget", lambda: object() if paused == "modal" else None))
            patches.enter_context(patch.object(polar_app.time, "perf_counter", clock))
            patches.enter_context(patch.object(window, "makeCurrent", lambda: record("acquire")))
            patches.enter_context(patch.object(window, "doneCurrent", lambda: record("release")))
            patches.enter_context(patch.object(window.clickgui_gpu, "prewarm", text_upload))
            patches.enter_context(patch.object(window.clickgui_gpu, "prewarm_images", image_upload))
            patches.enter_context(patch.object(window.backend.field_log, "emit", report))
            patches.enter_context(patch.object(window._gpu_prewarm_timer, "start", timer))
            if failure_at in {"clock-1", "clock-3", "report", "release", "timer"}:
                with self.assertRaises(RuntimeError) as caught:
                    window._run_gpu_prewarm_slice()
                self.assertIs(caught.exception, failure)
            else:
                window._run_gpu_prewarm_slice()
        return events, jobs

    def test_live_window_queue_and_legacy_fields_share_the_constructor_owner(self):
        owner = self.window._gpu_warmup
        self.assertIsNone(owner.pending)
        self.assertEqual((owner.last_ms, owner.max_ms), (0., 0.))
        for name in ("_gpu_prewarm_jobs_pending", "_gpu_prewarm_last_ms", "_gpu_prewarm_max_ms"):
            self.assertNotIn(name, self.window.__dict__)
        self.run_slice()
        self.assertIs(self.window._gpu_warmup, owner)
        self.assertIs(self.window._gpu_prewarm_jobs_pending, owner.pending)
        self.assertEqual(self.window._gpu_prewarm_last_ms, owner.last_ms)
        self.assertEqual(self.window._gpu_prewarm_max_ms, owner.max_ms)

    def test_one_text_upload_releases_context_before_timing_and_reschedule(self):
        events, jobs = self.run_slice()
        self.assertEqual([row[0] for row in events],
                         ["clock-1", "acquire", "clock-2", "next-job", "text", "release", "clock-3", "timer"])
        self.assertTrue(all(row[1] for row in events))
        self.assertTrue(all(row[2:] == (2., 4.) for row in events[:-1]))
        self.assertAlmostEqual(events[-1][2], 5.)
        self.assertAlmostEqual(events[-1][3], 5.)
        self.assertIs(self.window._gpu_prewarm_jobs_pending, jobs)

    def test_image_and_unknown_kinds_consume_one_job_with_existing_dispatch_rules(self):
        for kind in ("image", "unknown"):
            with self.subTest(kind=kind):
                events, _ = self.run_slice(kind=kind)
                names = [row[0] for row in events]
                self.assertEqual(names.count("next-job"), 1)
                self.assertNotIn("second-job", names)
                self.assertEqual(names.count("image"), int(kind == "image"))
                self.assertNotIn("text", names)
                self.assertNotIn("report", names)

    def test_expired_soft_budget_does_not_consume_the_iterator(self):
        events, jobs = self.run_slice(slow_before_upload=True)
        self.assertEqual([row[0] for row in events], ["clock-1", "acquire", "clock-2", "release", "clock-3", "timer"])
        self.assertEqual(next(jobs)[0], "text")

    def test_presentation_gates_leave_queue_and_measurements_intact(self):
        for reason in ("closing", "unavailable", "hidden", "no-context", "invalid-context", "escape", "modal"):
            with self.subTest(reason=reason):
                events, jobs = self.run_slice(paused=reason)
                self.assertEqual(events, [])
                self.assertIs(self.window._gpu_prewarm_jobs_pending, jobs)
                self.assertEqual((self.window._gpu_prewarm_last_ms, self.window._gpu_prewarm_max_ms), (2., 4.))

    def test_acquire_clock_and_upload_failures_cancel_before_reporting_and_release(self):
        for stage in ("acquire", "clock-2", "text"):
            with self.subTest(stage=stage):
                events, _ = self.run_slice(failure_at=stage)
                names = [row[0] for row in events]
                self.assertEqual(names[-3:], ["report", "release", "clock-3" if stage == "text" else "clock-2" if stage == "acquire" else "clock-3"])
                self.assertFalse(next(row for row in events if row[0] == "report")[1])
                self.assertIsNone(self.window._gpu_prewarm_jobs_pending)
                self.assertNotIn("timer", names)

    def test_release_failure_preserves_pending_queue_and_previous_measurements(self):
        events, jobs = self.run_slice(failure_at="release")
        self.assertEqual(events[-1], ("release", True, 2., 4.))
        self.assertIs(self.window._gpu_prewarm_jobs_pending, jobs)
        self.assertEqual((self.window._gpu_prewarm_last_ms, self.window._gpu_prewarm_max_ms), (2., 4.))

    def test_clock_before_try_propagates_without_taking_context(self):
        events, jobs = self.run_slice(failure_at="clock-1")
        self.assertEqual(events, [("clock-1", True, 2., 4.)])
        self.assertIs(self.window._gpu_prewarm_jobs_pending, jobs)

    def test_exhausted_iterator_clears_pending_without_reporting_or_rescheduling(self):
        events, _ = self.run_slice(exhausted=True)
        self.assertEqual([row[0] for row in events],
                         ["clock-1", "acquire", "clock-2", "next-job", "release", "clock-3"])
        self.assertFalse(events[-2][1])
        self.assertIsNone(self.window._gpu_prewarm_jobs_pending)

    def test_generator_failure_and_reporter_failure_both_release_context(self):
        error = TypeError("bad generator")
        for report_failure in (False, True):
            with self.subTest(report_failure=report_failure):
                events, _ = self.run_slice(job_exception=error, failure_at="report" if report_failure else None)
                names = [row[0] for row in events]
                self.assertEqual(names[-3:], ["report", "release", "clock-3"])
                self.assertIsNone(self.window._gpu_prewarm_jobs_pending)
                self.assertNotIn("timer", names)


if __name__ == "__main__":
    unittest.main()
