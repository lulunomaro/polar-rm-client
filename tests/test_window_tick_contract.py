"""Characterize the UI timer boundary without creating a window or GL context.

The harness invokes the real window facade. Its explicit fake collaborators
record scheduling, animation clocks, safety release, and backend stage order.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.ui import polar_app
from rm_client.ui.polar.primitives import EconomyScenePhase
from rm_client.ui.polar.tick_controller import TickDiagnosticField, TickDiagnostics
from rm_client.lifecycle import LifecyclePhase, LifecycleSnapshot


class Motion:
    def __init__(self, name, calls, *, progress=0.0):
        self.name = name
        self.calls = calls
        self.progress = progress
        self.active = False
        self.visible = False
        self.pending_mode = None
        self.reset_pending = False

    def update(self, dt=None, **kwargs):
        self.calls.append((self.name + ".update", dt, kwargs))
        return 12.5

    def set_target(self, *args, **kwargs):
        self.calls.append((self.name + ".target", args, kwargs))

    def set_content_target(self, *args):
        self.calls.append((self.name + ".content", args))

    def finish_reset(self):
        self.calls.append((self.name + ".reset",))
        self.reset_pending = False

    def update_presentation(self, now):
        self.calls.append((self.name + ".presentation", now))


class WindowHarness:
    """Only dependencies actually used by the timer, never a broad QWidget mock."""

    _ui_tick_last_ms = TickDiagnosticField("last_ms")
    _ui_tick_max_ms = TickDiagnosticField("max_ms")
    _ui_tick_gap_ms = TickDiagnosticField("gap_ms")
    _ui_tick_gap_max_ms = TickDiagnosticField("gap_max_ms")
    _ui_tick_stages = TickDiagnosticField("stages")
    _ui_tick_perf_log_at = TickDiagnosticField("reported_at")
    _tick_error_count = TickDiagnosticField("count")
    _tick_last_error = TickDiagnosticField("last_error")
    _tick_last_log_at = TickDiagnosticField("last_logged_at")

    def __init__(self, now=10.0):
        self._tick_diagnostics = TickDiagnostics()
        self.calls = []
        record = lambda name: lambda *args, **kwargs: self.calls.append((name, *args))
        self.last_frame_time = now - .02
        self._last_control_tick_time = now - .02
        self._last_logic_time = now - .02
        self._ui_frame_index = 0
        self._ui_tick_gap_max_ms = self._ui_tick_max_ms = 0.0
        self._ui_tick_stages = {}
        self._ui_tick_perf_log_at = now
        self._gpu_prewarm_last_ms = self._gpu_prewarm_max_ms = 0.0
        self._tick_error_count = 0
        self._tick_last_error = ""
        self._tick_last_log_at = {}
        self.render_fps_cap = 240.0
        self.render_fps_target = 144.0
        self._render_timer_interval_ms = 7
        self.refresh_value = 144.0
        self.registry = NS(
            get=lambda name: NS(get_setting=lambda _: NS(value=self.refresh_value)) if name == "UIScale" else None,
            tick=record("registry"),
        )
        self.state = NS(backend_stats={}, tick=record("state.tick"))
        self.timer = NS(setInterval=record("timer.interval"))
        self.frame_scheduler = NS(force=record("frame.force"), should_request=lambda *args: True)
        self.gui_motion = NS(begin_frame=record("gui.begin"))
        for name in ("polar_unified_scroll_motion", "economy_category_scroll", "economy_offers_scroll", "economy_detail_scroll", "quick_action_wheel", "gui_timeline", "jello_state", "polar_unified_morph", "economy_island", "hero_deploy_ui", "tactical_map_state", "dynamic_island_state"):
            setattr(self, name, Motion(name, self.calls))
        self.polar_settings_scroll_motion = {"setting": Motion("setting", self.calls)}
        self.polar_unified_scroll = 0.0
        self.gui_visible = False
        self.home_visible = False
        self.economy_visible = False
        self.economy_phase = EconomyScenePhase.CLOSED
        self._economy_generation = 3
        self._economy_closing_generation = None
        self._economy_restore_pending_generation = None
        self.economy_bridge = NS(update=lambda *args: None)
        self.economy_last_status_sequence = 0
        self.match_chat = NS(input_open=False)
        self._combat_input_was_allowed = False
        self.input_allowed = True
        self._control_mouse_point = object()
        self.backend = NS(
            mqtt=NS(mock_output=False), tick=record("backend.tick"),
            apply_updates=record("backend.apply"), sync_mqtt_link=record("mqtt.link"),
            tick_polar_link=record("polar.link"), field_log=NS(emit=record("log")),
        )
        self.backend.lifecycle_pending = False
        self.backend.lifecycle_closed = False
        self.backend.shutdown_completion = None
        self.backend.lifecycle_snapshot = LifecycleSnapshot(LifecyclePhase.IDLE, 0, (), (), 0, False)
        self.backend.poll_lifecycle = lambda: None
        self._window_lifecycle = polar_app.WindowLifecycleController(self.backend)
        self.on_backend_lifecycle_completion = lambda completion: False
        self.control = NS(
            tick=record("control.75hz"), reset_inputs=record("control.reset"),
            send_from_state=record("control.release"),
            release_direct_dt7=record("control.dt7_release"),
            release_custom_controller=record("control.custom_release"), mock_output=True,
        )
        self.camera_worker = NS(connected=True)
        self.mqtt = NS(connected=True)
        self.udp = NS(decoded_frame_fresh=lambda age: True)
        self.serial = NS(connected=False)
        self.NORMAL_BACKEND_VIDEO_STALE_S = 2.0
        self.startup_wallpaper = NS(set_active=record("wallpaper.active"))
        for name in ("flush_auto_save_if_due", "update_student_frontend", "invalidate_transient_overlay_scene", "update", "ensure_camera_started", "refresh_vision_runtime", "consume_capture_frame", "toast"):
            setattr(self, name, record(name))
        for name, stage in (("update_backend_readiness", "backend.readiness"), ("update_hero_deploy_state", "hero.deploy"), ("update_hero_lob_guard", "hero.lob"), ("update_auto_spin_guard", "auto.spin"), ("consume_official_messages", "official.consume"), ("apply_official_state", "official.apply"), ("update_elimination_hit_info", "fx.hit_info"), ("sync_tongji_vision_state", "vision.sync"), ("process_vision_kill_events", "vision.events")):
            setattr(self, name, record(stage))
        self.anim_duration = lambda value: value
        self.combat_control_input_allowed = lambda: self.input_allowed
        self.active_clickgui_mode = lambda: "PolarUnified"
        self.economy_uses_mock_transport = lambda: True
        self.dynamic_payload = {"kind": "module", "active": True}
        self.dynamic_island_payload = lambda: self.dynamic_payload
        self.hero_deploy_video_cut_active = lambda: False
        self.startup_needs_high_rate = lambda: False

    def __getattr__(self, name):
        method = getattr(polar_app.PolarRMWindow, name, None)
        if callable(method):
            return MethodType(method, self)
        raise AttributeError(name)

    def tick(self, now=10.0):
        with patch.object(polar_app.time, "perf_counter", return_value=now), patch.object(polar_app, "render_target", side_effect=lambda target, cap: target):
            self.on_tick()

    def named(self, name):
        return [entry for entry in self.calls if entry[0] == name]


class WindowClockContractTests(unittest.TestCase):
    def test_pending_lifecycle_skips_every_runtime_and_economy_stage(self):
        window = WindowHarness()
        window.backend.lifecycle_pending = True
        window.backend.lifecycle_snapshot = LifecycleSnapshot(LifecyclePhase.STOPPING, 1, ("camera",), (), 0, False)
        window.tick()
        self.assertEqual(window.calls, [("update",)])
        self.assertEqual(window._ui_frame_index, 0)

    def test_control_and_logic_have_independent_rates_and_no_catchup_burst(self):
        window = WindowHarness()
        window._last_control_tick_time = 10.0 - .014
        window._last_logic_time = 10.0 - .014
        window.tick()
        self.assertEqual(len(window.named("control.75hz")), 1)
        self.assertEqual(window.named("backend.tick"), [])
        window.calls.clear()
        window.tick(100.0)
        self.assertEqual(len(window.named("control.75hz")), 1)
        self.assertEqual(len(window.named("backend.tick")), 1)
        self.assertEqual(window._last_control_tick_time, 100.0)
        self.assertEqual(window._last_logic_time, 100.0)

    def test_ui_elapsed_and_simulation_elapsed_preserve_different_caps(self):
        for gap, expected_ui, expected_logic in ((0.0, .001, None), (.12, .12, .05), (12.0, 1.0, .05)):
            with self.subTest(gap=gap):
                window = WindowHarness()
                window.last_frame_time = window._last_logic_time = 10.0 - gap
                window.tick()
                self.assertAlmostEqual(window.frame_dt, expected_ui)
                self.assertAlmostEqual(window.named("gui_timeline.update")[0][1], expected_ui)
                ticks = window.named("state.tick")
                if expected_logic is None:
                    self.assertEqual(ticks, [])
                else:
                    self.assertAlmostEqual(ticks[0][1], expected_logic)
                self.assertEqual(window.last_frame_time, 10.0)
                self.assertEqual(window._ui_frame_index, 1)

    def test_backend_order_preserves_polar_overlay_as_final_stage(self):
        window = WindowHarness()
        window.tick()
        names = [entry[0] for entry in window.calls]
        stages = ("control.75hz", "state.tick", "backend.tick", "backend.apply", "backend.readiness", "hero.deploy", "hero.lob", "auto.spin", "official.consume", "official.apply", "fx.hit_info", "vision.sync", "vision.events", "registry", "mqtt.link", "polar.link")
        self.assertEqual([name for name in names if name in stages], list(stages))
        self.assertTrue(window.state.camera_connected)
        self.assertTrue(window.state.mqtt_connected)
        self.assertTrue(window.state.udp_video_connected)
        self.assertFalse(window.state.serial_connected)

    def test_ui_modal_releases_control_before_next_control_tick(self):
        window = WindowHarness()
        window._combat_input_was_allowed = True
        window.input_allowed = False
        window.tick()
        names = [entry[0] for entry in window.calls]
        self.assertLess(names.index("control.reset"), names.index("control.release"))
        for action in ("control.release", "control.dt7_release", "control.custom_release"):
            self.assertLess(names.index(action), names.index("control.75hz"))
        self.assertFalse(window.control.mock_output)
        self.assertIsNone(window._control_mouse_point)
        window.tick(10.02)
        self.assertEqual(len(window.named("control.release")), 1)

    def test_refresh_change_updates_timer_and_invalidates_scheduler_once(self):
        window = WindowHarness()
        window.refresh_value = 240.0
        window.tick()
        self.assertEqual(window.named("timer.interval"), [("timer.interval", 4)])
        self.assertEqual(len(window.named("frame.force")), 1)
        window.tick(10.02)
        self.assertEqual(len(window.named("timer.interval")), 1)

    def test_invalid_refresh_setting_falls_back_without_lowering_logic_clock(self):
        for refresh in (None, "invalid", 30):
            window = WindowHarness()
            window.refresh_value = refresh
            window.tick()
            self.assertGreaterEqual(window.render_fps_target, 120.0)
            self.assertLessEqual(window._render_timer_interval_ms, 8)


class WindowPresentationContractTests(unittest.TestCase):
    def test_jello_reset_waits_for_closed_presentation(self):
        for visible, progress, expected in ((True, 0.0, 0), (False, .01, 0), (False, .001, 1)):
            window = WindowHarness()
            window.gui_visible = visible
            window.gui_timeline.progress = progress
            window.jello_state.reset_pending = True
            window.tick()
            self.assertEqual(len(window.named("jello_state.reset")), expected)

    def test_gui_mode_is_resolved_after_the_jello_timeline_advance(self):
        window = WindowHarness()
        window.gui_visible = True
        modes = ["PolarUnified"]
        window.active_clickgui_mode = lambda: modes[0]
        window.jello_state.update = lambda dt: modes.__setitem__(0, "JelloSigma")
        window.tick()
        self.assertEqual(window.named("polar_unified_morph.target")[0][1][0], 0.0)

    def test_economy_open_and_matching_close_generation_keep_original_thresholds(self):
        window = WindowHarness()
        window.economy_phase = EconomyScenePhase.OPENING
        window.economy_island.progress = .999
        window.tick()
        self.assertEqual(window.economy_phase, EconomyScenePhase.OPEN)
        for matching in (False, True):
            window = WindowHarness()
            window.economy_phase = EconomyScenePhase.CLOSING
            window.economy_island.progress = .001
            window._economy_closing_generation = 3 if matching else 2
            window.tick()
            self.assertEqual(window._economy_restore_pending_generation, 3 if matching else None)
            self.assertEqual(len(window.named("invalidate_transient_overlay_scene")), int(matching))

    def test_dynamic_island_duration_distinguishes_payload_and_hidden_state(self):
        for kind, active, duration in (("module", True, .30), ("notice", True, .80), ("module", False, .22)):
            window = WindowHarness()
            window.dynamic_payload = {"kind": kind, "active": active}
            window.tick()
            self.assertEqual(window.named("dynamic_island_state.content"), [("dynamic_island_state.content", (float(active), duration))])


class WindowTickFaultContractTests(unittest.TestCase):
    def test_stage_failure_isolated_but_later_overlay_stage_still_runs(self):
        window = WindowHarness()
        def failed_backend():
            raise ValueError("broken packet")
        window.backend.tick = failed_backend
        with self.assertLogs("rm_client.ui.polar_app", level="ERROR"):
            window.tick()
        self.assertEqual(window._tick_error_count, 1)
        self.assertEqual(window._tick_last_error, "backend.tick: ValueError: broken packet")
        self.assertEqual(len(window.named("polar.link")), 1)
        self.assertEqual(window.state.backend_stats["ui_tick"]["errors"], 1)
        self.assertEqual(window._ui_tick_stages["backend.tick"]["count"], 1)

    def test_broken_diagnostic_sink_cannot_abort_remaining_safety_stages(self):
        window = WindowHarness()
        def failed_backend():
            raise ValueError("broken packet")
        def failed_log(*args, **kwargs):
            raise OSError("disk unavailable")
        window.backend.tick = failed_backend
        window.backend.field_log.emit = failed_log
        with self.assertLogs("rm_client.ui.polar_app", level="WARNING"):
            window.tick()
        self.assertEqual(window._tick_error_count, 1)
        self.assertEqual(len(window.named("polar.link")), 1)
        self.assertEqual(window._ui_tick_stages["backend.tick"]["count"], 1)

    def test_fault_logs_are_throttled_per_stage_but_counters_are_not(self):
        window = WindowHarness()
        def fail():
            raise RuntimeError("packet")
        with self.assertLogs("rm_client.ui.polar_app", level="ERROR") as logs:
            for name, now in (("one", 10.0), ("one", 10.2), ("two", 10.3), ("one", 11.0)):
                self.assertFalse(window._run_tick_stage(name, fail, now))
        self.assertEqual(len(logs.output), 3)
        self.assertEqual(window._tick_error_count, 4)
        self.assertEqual(window._ui_tick_stages["one"]["count"], 3)

    def test_performance_window_is_five_seconds_and_never_resets_lifetime_errors(self):
        window = WindowHarness(now=10.0)
        window._ui_tick_gap_ms = 2.0
        window._tick_error_count = 7
        window._tick_last_error = "backend: TypeError: packet"
        window._tick_last_log_at = {"backend": 12.0}
        window._ui_tick_stages["backend"] = {"count": 2, "total_ms": 4.0, "max_ms": 3.0}
        with patch.object(polar_app.time, "perf_counter", return_value=15.001):
            window._publish_tick_performance(14.999)
            self.assertNotIn("ui_tick_performance", window.state.backend_stats)
            window._publish_tick_performance(15.0)
        report = window.state.backend_stats["ui_tick_performance"]
        self.assertEqual(report["stages"]["backend"]["count"], 2)
        self.assertEqual(window._ui_tick_perf_log_at, 15.0)
        self.assertEqual(window._ui_tick_stages, {})
        self.assertEqual(window._tick_error_count, 7)
        self.assertEqual(window._tick_last_error, "backend: TypeError: packet")
        self.assertEqual(window._tick_last_log_at, {"backend": 12.0})
        window._ui_tick_stages["backend"] = {"count": 99}
        self.assertEqual(report["stages"]["backend"]["count"], 2)

    def test_failed_performance_sink_still_consumes_one_reporting_window(self):
        window = WindowHarness(now=10.0)
        window._ui_tick_gap_ms = 2.0
        window.backend.field_log.emit = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("timing disk"))
        window._ui_tick_stages["backend"] = {"count": 2, "total_ms": 4.0, "max_ms": 3.0}
        with patch.object(polar_app.time, "perf_counter", return_value=15.002):
            with self.assertLogs("rm_client.ui.polar_app", level="WARNING"):
                window._publish_tick_performance(15.0)
            window._publish_tick_performance(15.1)
        self.assertEqual(window._ui_tick_perf_log_at, 15.0)
        self.assertEqual(window._ui_tick_stages, {})
        self.assertEqual(window.state.backend_stats["ui_tick_performance"]["stages"]["backend"]["count"], 2)

    def test_performance_publication_copies_stage_stats_before_reset(self):
        window = WindowHarness()
        window._ui_tick_perf_log_at = 0.0
        window.tick()
        metrics = window.state.backend_stats["ui_tick_performance"]
        self.assertEqual(metrics["stages"]["polar.link"]["count"], 1)
        self.assertEqual(window._ui_tick_stages, {})
        self.assertEqual(window._ui_tick_max_ms, 0.0)
        self.assertEqual(window._ui_tick_gap_max_ms, 0.0)


if __name__ == "__main__":
    unittest.main()
