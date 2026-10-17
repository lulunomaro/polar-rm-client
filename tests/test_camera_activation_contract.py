"""Actual camera adapter effects and partial state, frozen before extraction."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_window_vision_runtime import WindowVisionRuntimeFixture


class CameraActivationContractTests(WindowVisionRuntimeFixture):
    def exercise(self, *, started=True, running=True, source="Webcam", disabled="0",
                 requested=(5, 640, 480), previous=(0, 960, 540), failure_at=None,
                 accepted=True, closing=False, replace_sink=False):
        window, events = self.window, []
        worker = window.camera_worker
        failure = ValueError("camera activation stage failed")
        window.camera_started, window._camera_config = started, previous
        worker.running, worker.last_error = running, "previous capture error"
        window.registry.get("CameraFeed").get_setting("Source").value = source
        callback = None
        new_sink = lambda frame: None
        original_configure, original_stop = worker.configure, worker.stop
        def record(name):
            events.append((name, window.camera_started, window._camera_config))
            if name == failure_at:
                raise failure
        def number(module, name, default):
            record(name)
            return dict(zip(("Camera ID", "Capture Width", "Capture Height"), requested))[name]
        def stop(*, wait):
            self.assertIs(wait, False)
            record("stop")
            original_stop(wait=wait)
        def configure(camera_id, size):
            record("configure")
            self.assertEqual((camera_id, *size), requested)
            original_configure(camera_id, size)
            if replace_sink:
                window.on_captured_camera_frame = new_sink
        def bind(worker, value):
            nonlocal callback
            record("bind")
            self.assertEqual(value, window.on_captured_camera_frame)
            callback = value
        def start():
            record("start")
            self.assertEqual(callback, new_sink if replace_sink else window.on_captured_camera_frame)
            return accepted
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"POLAR_DISABLE_LOCAL_CAPTURE": disabled}))
            stack.enter_context(patch.object(window._window_lifecycle, "closing", closing))
            stack.enter_context(patch.object(window, "get_setting_number", number))
            stack.enter_context(patch.object(window, "clear_normal_backend_video_frame", lambda: record("clear")))
            stack.enter_context(patch.object(worker, "stop", stop))
            stack.enter_context(patch.object(worker, "configure", configure))
            stack.enter_context(patch.object(worker, "start", start))
            stack.enter_context(patch.object(type(worker), "on_frame",
                                            property(lambda worker: callback, bind), create=True))
            if failure_at is not None:
                with self.assertRaises(ValueError) as caught:
                    window.ensure_camera_started()
                self.assertIs(caught.exception, failure)
            else:
                window.ensure_camera_started()
        return events

    def test_real_window_exposes_the_same_capture_and_admission_owner(self):
        owner = self.window._camera_activation
        self.assertIs(self.window.camera_worker, owner.worker)
        self.assertFalse(owner.started)
        self.assertIsNone(owner.configuration)
        self.exercise()
        self.assertTrue(owner.started)
        self.assertEqual(owner.configuration, (5, 640, 480))
        for name in ("camera_worker", "camera_started", "_camera_config"):
            self.assertNotIn(name, self.window.__dict__)

    def test_reconfiguration_stops_then_clears_configures_binds_and_admits(self):
        events = self.exercise()
        self.assertEqual([row[0] for row in events],
                         ["Camera ID", "Capture Width", "Capture Height", "stop", "clear", "configure", "bind", "start"])
        self.assertTrue(all(row[1] for row in events[:4]))
        self.assertTrue(all(not row[1] for row in events[4:]))
        self.assertTrue(all(row[2] == (0, 960, 540) for row in events))
        self.assertTrue(self.window.camera_started)
        self.assertEqual(self.window._camera_config, (5, 640, 480))

    def test_unchanged_running_camera_reads_settings_but_does_not_restart(self):
        events = self.exercise(requested=(0, 960, 540))
        self.assertEqual([row[0] for row in events], ["Camera ID", "Capture Width", "Capture Height"])
        self.assertTrue(self.window.camera_started)

    def test_dead_worker_retries_without_redundant_stop_or_frame_clear(self):
        events = self.exercise(running=False)
        self.assertEqual([row[0] for row in events],
                         ["Camera ID", "Capture Width", "Capture Height", "configure", "bind", "start"])
        self.assertFalse(events[3][1])
        self.assertEqual(self.window._camera_config, (5, 640, 480))

    def test_rejected_start_preserves_last_accepted_configuration(self):
        self.exercise(accepted=False)
        self.assertFalse(self.window.camera_started)
        self.assertEqual(self.window._camera_config, (0, 960, 540))

    def test_disabled_capture_stops_before_clearing_flag_and_setting_reason(self):
        for disabled in ("1", "TRUE", "yes"):
            with self.subTest(disabled=disabled):
                events = self.exercise(disabled=disabled)
                self.assertEqual(events, [("stop", True, (0, 960, 540))])
                self.assertFalse(self.window.camera_started)
                self.assertEqual(self.window.camera_worker.last_error, "local capture disabled")

    def test_non_webcam_source_does_not_read_configuration_or_change_diagnostics(self):
        for source in ("Synthetic", "UDP", "webcam", ""):
            with self.subTest(source=source):
                events = self.exercise(source=source)
                self.assertEqual(events, [("stop", True, (0, 960, 540))])
                self.assertFalse(self.window.camera_started)
                self.assertEqual(self.window.camera_worker.last_error, "previous capture error")

    def test_whitespace_does_not_expand_capture_disable_spellings(self):
        events = self.exercise(disabled=" true ")
        self.assertEqual(events[-1][0], "start")
        self.assertTrue(self.window.camera_started)

    def test_closing_window_does_not_touch_camera_or_read_configuration(self):
        self.assertEqual(self.exercise(closing=True), [])
        self.assertTrue(self.window.camera_started)
        self.assertEqual(self.window.camera_worker.last_error, "previous capture error")

    def test_parameter_and_stop_failures_preserve_previous_admission(self):
        for stage in ("Camera ID", "Capture Width", "Capture Height", "stop"):
            with self.subTest(stage=stage):
                events = self.exercise(failure_at=stage)
                self.assertEqual(events[-1][0], stage)
                self.assertTrue(self.window.camera_started)
                self.assertEqual(self.window._camera_config, (0, 960, 540))

    def test_failed_clear_configure_bind_or_start_keeps_flag_revoked_and_old_config(self):
        for stage in ("clear", "configure", "bind", "start"):
            with self.subTest(stage=stage):
                events = self.exercise(failure_at=stage)
                self.assertEqual(events[-1][0], stage)
                self.assertFalse(self.window.camera_started)
                self.assertEqual(self.window._camera_config, (0, 960, 540))

    def test_failed_disable_stop_does_not_claim_revocation_or_replace_diagnostic(self):
        self.exercise(disabled="1", failure_at="stop")
        self.assertTrue(self.window.camera_started)
        self.assertEqual(self.window.camera_worker.last_error, "previous capture error")

    def test_configure_callback_replacement_is_observed_before_binding(self):
        events = self.exercise(replace_sink=True)
        self.assertEqual([row[0] for row in events[-3:]], ["configure", "bind", "start"])


if __name__ == "__main__":
    unittest.main()
