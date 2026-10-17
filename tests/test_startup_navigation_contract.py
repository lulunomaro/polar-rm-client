"""Freeze live startup transition effects, including interrupted partial state."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
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
from rm_client.ui.polar.renderers import startup_scene
from rm_client.ui.polar_app import PolarRMWindow
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])


class StartupNavigationContractTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {
            "POLAR_USER_DATA_ROOT": directory, "POLAR_AUTOSTART": "0",
        }))
        self.starts = [self.stack.enter_context(patch.object(owner, "start"))
                       for owner in (RmMqttBackend, RmSerialBackend, RmVideoBackend)]
        self.window = PolarRMWindow()
        self.addCleanup(self.close_window)
        settle_window_lifecycle(self.window, APP)
        self.window.timer.stop()
        for start in self.starts:
            start.assert_not_called()

    def close_window(self):
        close_window_safely(self.window, APP)
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def reset_transition(self):
        window = self.window
        window.home_visible = True
        window.startup_step = "scenario"
        window.startup_transition_from = None
        window.startup_transition_to = None
        window.startup_transition_started = 10.0
        window._startup_page_epoch = 4
        window._startup_static_ready_at = 11.0
        window._startup_static_page_key = ("page",)
        window._gpu_static_ui_key = ("ui",)

    def state(self):
        window = self.window
        return (window.startup_step, window.startup_transition_from,
                window.startup_transition_to, window.startup_transition_started,
                window._startup_page_epoch, window._startup_static_ready_at,
                window._startup_static_page_key, window._gpu_static_ui_key)

    def begin_trace(self, page="match", failure_stage=None):
        events = []
        failure = TypeError("injected transition failure")
        def effect(name):
            events.append((name, self.state()))
            if name == failure_stage:
                raise failure
        durations = iter((.42, .84))
        def duration(base):
            self.assertEqual(base, .42)
            value = next(durations)
            effect("duration-1" if value == .42 else "duration-2")
            return value
        def clock():
            effect("clock")
            return 30.0
        def reset(value):
            self.assertEqual(value, 0.)
            effect("reset-morph")
        def target(value, duration, *, now):
            self.assertEqual((value, duration, now), (1., .42, 30.))
            effect("target-morph")
        with (
            patch.object(self.window.startup_gpu, "discard_interpolation_history",
                         lambda: effect("discard-history")),
            patch.object(startup_scene.time, "perf_counter", clock),
            patch.object(self.window.startup_page_morph, "reset", reset),
            patch.object(self.window.startup_page_morph, "set_target", target),
            patch.object(self.window, "anim_duration", duration),
            patch.object(self.window.frame_scheduler, "force", lambda: effect("force-frame")),
        ):
            if failure_stage:
                with self.assertRaises(TypeError) as caught:
                    self.window.begin_startup_page_transition(page)
                self.assertIs(caught.exception, failure)
            else:
                self.window.begin_startup_page_transition(page)
        return events

    def test_real_window_compatibility_fields_share_one_navigation_owner(self):
        window = self.window
        owner = window._startup_navigation
        fields = {
            "home_visible": "visible", "startup_step": "page",
            "startup_transition_from": "transition_from",
            "startup_transition_to": "transition_to",
            "startup_transition_started": "transition_started",
            "_startup_page_epoch": "page_epoch", "_startup_static_ready_at": "static_ready_at",
        }
        self.reset_transition()
        for legacy_name, owned_name in fields.items():
            with self.subTest(legacy_name=legacy_name):
                self.assertEqual(getattr(window, legacy_name), getattr(owner, owned_name))
                self.assertNotIn(legacy_name, window.__dict__)
        window.begin_startup_page_transition("match")
        self.assertEqual(owner.page, "match")
        self.assertEqual(owner.transition_from, "scenario")
        self.assertEqual(owner.transition_to, "match")
        window.finish_startup_page_transition()
        self.assertIsNone(owner.transition_from)
        self.assertIsNone(owner.transition_to)
        self.assertIs(window._startup_navigation, owner)

    def test_begin_transition_preserves_published_state_at_each_effect(self):
        self.reset_transition()
        initial = ("scenario", None, None, 10., 4, 11., ("page",), ("ui",))
        announced = ("scenario", "scenario", "match", 10., 4, 11., ("page",), ("ui",))
        timed = ("scenario", "scenario", "match", 30., 4, 11., ("page",), ("ui",))
        active = ("match", "scenario", "match", 30., 5, 11., None, ("ui",))
        ready = ("match", "scenario", "match", 30., 5, 30.84, None, ("ui",))
        self.assertEqual(self.begin_trace(), [
            ("discard-history", initial), ("clock", announced),
            ("reset-morph", timed), ("duration-1", timed), ("target-morph", timed),
            ("duration-2", active), ("force-frame", ready),
        ])
        self.assertEqual(self.state(), ready)

    def test_each_begin_failure_preserves_exact_partial_state_and_stops_later_effects(self):
        expected = [
            ("discard-history", ("scenario", None, None, 10., 4, 11., ("page",), ("ui",))),
            ("clock", ("scenario", "scenario", "match", 10., 4, 11., ("page",), ("ui",))),
            ("reset-morph", ("scenario", "scenario", "match", 30., 4, 11., ("page",), ("ui",))),
            ("duration-1", ("scenario", "scenario", "match", 30., 4, 11., ("page",), ("ui",))),
            ("target-morph", ("scenario", "scenario", "match", 30., 4, 11., ("page",), ("ui",))),
            ("duration-2", ("match", "scenario", "match", 30., 5, 11., None, ("ui",))),
            ("force-frame", ("match", "scenario", "match", 30., 5, 30.84, None, ("ui",))),
        ]
        for index, (stage, state) in enumerate(expected):
            with self.subTest(stage=stage):
                self.reset_transition()
                events = self.begin_trace(failure_stage=stage)
                self.assertEqual([name for name, _ in events], [name for name, _ in expected[:index + 1]])
                self.assertEqual(self.state(), state)

    def test_invalid_and_duplicate_destinations_have_no_effects(self):
        for page in ("scenario", "done", "invalid", ""):
            with self.subTest(page=page):
                self.reset_transition()
                previous = self.state()
                self.assertEqual(self.begin_trace(page), [])
                self.assertEqual(self.state(), previous)

    def test_reverse_transition_starts_from_current_interactive_page(self):
        self.reset_transition()
        self.begin_trace()
        self.begin_trace("scenario")
        self.assertEqual(self.state(), ("scenario", "match", "scenario", 30., 6, 30.84, None, ("ui",)))

    def test_finish_commits_before_gl_cleanup_and_does_not_repeat_cleanup(self):
        for fail_at in (None, "static", "frame"):
            with self.subTest(fail_at=fail_at):
                self.reset_transition()
                self.window.startup_transition_from = "scenario"
                self.window.startup_transition_to = "match"
                events = []
                failure = RuntimeError("injected glass cleanup failure")
                def cleanup(name):
                    events.append((name, self.state()))
                    if name == fail_at:
                        raise failure
                with (
                    patch.object(self.window.gpu_compositor, "clear_static_glass", lambda: cleanup("static")),
                    patch.object(self.window.gpu_compositor, "clear_frame_glass", lambda: cleanup("frame")),
                ):
                    if fail_at:
                        with self.assertRaises(RuntimeError) as caught:
                            self.window.finish_startup_page_transition()
                        self.assertIs(caught.exception, failure)
                    else:
                        self.window.finish_startup_page_transition()
                    committed = ("scenario", None, None, 10., 5, 11., None, None)
                    names = ["static"] if fail_at == "static" else ["static", "frame"]
                    self.assertEqual(events, [(name, committed) for name in names])
                    self.assertEqual(self.state(), committed)
                    self.window.finish_startup_page_transition()
                    self.assertEqual(len(events), len(names))


if __name__ == "__main__":
    unittest.main()
