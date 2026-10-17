"""Characterize construction order at live owner boundaries before extraction."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ["CI"] = "true"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter
from rm_client.backend.manager import BackendManager
from rm_client.backend.mqtt_backend import RmMqttBackend
from rm_client.backend.serial_backend import RmSerialBackend
from rm_client.backend.video_backend import RmVideoBackend
from rm_client.ui import polar_app
from rm_client.ui.polar_app import PolarRMWindow
from rm_client.ui.polar.frame_telemetry import FrameTelemetry
from rm_client.ui.polar.tick_controller import TickDiagnostics
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])
_NETWORK_AUDIT_ACTIVE = False
_NETWORK_ATTEMPTS = []


def reject_python_network(event, arguments):
    if _NETWORK_AUDIT_ACTIVE and event in {"socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo"}:
        _NETWORK_ATTEMPTS.append(event)
        raise AssertionError(f"Construction tests must remain offline: {event}")


sys.addaudithook(reject_python_network)


class WindowConstructionTests(unittest.TestCase):
    def construct(self, *, autostart: bool, profile_present: bool = True):
        global _NETWORK_AUDIT_ACTIVE
        _NETWORK_AUDIT_ACTIVE = True
        _NETWORK_ATTEMPTS.clear()
        self.addCleanup(self.finish_network_audit)
        # Only native connection starts are inert. Construction, configuration,
        # request coalescing, owner-thread completion and final close stay real.
        self.starts = {}
        for name, owner in (("mqtt", RmMqttBackend), ("serial", RmSerialBackend), ("video", RmVideoBackend)):
            start_patch = patch.object(owner, "start")
            self.starts[name] = start_patch.start()
            self.addCleanup(start_patch.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(os.environ, {
            "POLAR_USER_DATA_ROOT": self.directory.name,
            "POLAR_AUTOSTART": "1" if autostart else "0",
        })
        environment.start()
        self.addCleanup(environment.stop)
        phases = []
        original_clear = PolarRMWindow._clear_control_inputs_for_lock
        original_load = PolarRMWindow.load_startup_config
        original_request = BackendManager.request_scenario

        def clear_inputs(window, reason):
            if reason == "startup locked":
                phases.append("control-neutralized")
                self.assertIs(window.backend.input_clear_hook.__self__, window)
                self.assertIs(window.control.publisher, window.backend)
                self.assertNotIn("timer", window.__dict__)
                self.assertIsInstance(window._tick_diagnostics, TickDiagnostics)
                self.assertEqual(window._tick_diagnostics.count, 0)
            return original_clear(window, reason)

        def load_profile(window):
            phases.append("profile-load")
            self.assertEqual(phases[0], "control-neutralized")
            self.assertNotIn("timer", window.__dict__)
            self.assertIs(window.mqtt, window.backend.mqtt)
            self.assertIs(window.udp, window.backend.udp)
            self.assertIs(window.serial, window.backend.serial)
            self.assertIs(window.backend.referee_view_observer.__self__, window.top_match_bar)
            self.assertIs(window.economy_bridge.sender.__self__, window)
            self.assertIsInstance(window._frame_telemetry, FrameTelemetry)
            self.assertEqual(window._frame_telemetry.fps_samples, [])
            self.assertFalse(window.camera_started)
            self.assertFalse(window._gpu_prewarm_timer.isActive())
            for name in ("config_store", "calibration", "startup_wallpaper", "local_vision",
                         "fonts", "gui_motion", "clickgui_gpu", "gpu_compositor",
                         "overlay_coordinator", "quick_action_wheel", "match_chat"):
                self.assertIsNotNone(getattr(window, name))
            if profile_present:
                original_load(window)
                self.assertTrue(window.configuration_change_pending)
                phases.append("profile-requested")
            else:
                phases.append("profile-absent")

        def request(manager, *args, **kwargs):
            phases.append("runtime-request")
            return original_request(manager, *args, **kwargs)

        with patch.object(PolarRMWindow, "_clear_control_inputs_for_lock", clear_inputs), \
             patch.object(PolarRMWindow, "load_startup_config", load_profile), \
             patch.object(BackendManager, "request_scenario", request):
            window = PolarRMWindow()
        self.addCleanup(self.close_window, window)
        return window, phases

    def finish_network_audit(self):
        global _NETWORK_AUDIT_ACTIVE
        _NETWORK_AUDIT_ACTIVE = False
        self.assertEqual(_NETWORK_ATTEMPTS, [])

    @staticmethod
    def close_window(window):
        close_window_safely(window, APP)
        window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_live_tick_and_legacy_diagnostic_fields_share_the_constructor_owner(self):
        window, _ = self.construct(autostart=False)
        settle_window_lifecycle(window, APP)
        window.timer.stop()
        owner = window._tick_diagnostics
        window._ui_tick_stages = {}
        self.assertIs(window._ui_tick_stages, owner.stages)
        window._ui_tick_perf_log_at = time.perf_counter()
        now = time.perf_counter()
        window._last_control_tick_time = window._last_logic_time = now - .1
        with (
            patch.object(owner, "record_wakeup", wraps=owner.record_wakeup) as wakeup,
            patch.object(owner, "run_stage", wraps=owner.run_stage) as stage,
            patch.object(owner, "finish_tick", wraps=owner.finish_tick) as finish,
        ):
            window.on_tick()
        wakeup.assert_called_once()
        finish.assert_called_once()
        self.assertGreater(stage.call_count, 0)
        self.assertGreater(owner.stages["backend.tick"]["count"], 0)
        self.assertEqual(window._ui_tick_gap_ms, owner.gap_ms)
        def fail():
            raise TypeError("owned diagnostic defect")
        with self.assertLogs("rm_client.ui.polar_app", level="ERROR"):
            self.assertFalse(window._run_tick_stage("probe", fail, now + 1))
        self.assertEqual(window._tick_error_count, owner.count)
        self.assertEqual(owner.count, 1)
        self.assertIn("owned diagnostic defect", window._tick_last_error)
        self.assertIs(window._tick_last_log_at, owner.last_logged_at)
        self.assertIs(window._tick_diagnostics, owner)

    def test_real_cpu_paint_records_and_acknowledges_the_constructor_owner(self):
        window, _ = self.construct(autostart=False)
        settle_window_lifecycle(window, APP)
        window.timer.stop()
        window.resize(640, 360)
        window.ui_audio_enabled = False
        owner = window._frame_telemetry
        owner.last_reported_at = 0.0
        image = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)

        class ImagePainter(QPainter):
            def __init__(self, device):
                super().__init__(image if device is window else device)

        with (
            patch.object(polar_app, "QPainter", ImagePainter),
            patch.object(owner, "record_frame", wraps=owner.record_frame) as record,
            patch.object(owner, "acknowledge_report", wraps=owner.acknowledge_report) as acknowledge,
        ):
            window.paintGL()
        record.assert_called_once()
        acknowledge.assert_called_once()
        self.assertIs(window.fps_samples, owner.fps_samples)
        self.assertIs(window._field_frame_times_ms, owner.frame_times_ms)
        self.assertEqual(owner.frame_times_ms, [])
        self.assertGreater(owner.last_reported_at, 0)
        self.assertEqual(window.render_frame_ms_ema, owner.ema_ms)
        self.assertGreater(owner.ema_ms, 0)
        self.assertGreater(window.state.fps, 0)
        self.assertFalse(image.paintingActive())
        self.assertIs(window._frame_telemetry, owner)

    def test_retired_frame_telemetry_aliases_still_roundtrip(self):
        window, _ = self.construct(autostart=False)
        settle_window_lifecycle(window, APP)
        window.timer.stop()
        owner = window._frame_telemetry
        original_samples = owner.frame_times_ms
        original_reported_at = owner.last_reported_at
        try:
            samples = [1.25, 3.5]
            window._field_frame_times_ms = samples
            self.assertIs(owner.frame_times_ms, samples)
            self.assertIs(window._field_frame_times_ms, samples)
            owner.frame_times_ms = [9.0]
            self.assertIs(window._field_frame_times_ms, owner.frame_times_ms)
            window._last_field_frame_log_at = 17.5
            self.assertEqual(owner.last_reported_at, 17.5)
            owner.last_reported_at = 22.25
            self.assertEqual(window._last_field_frame_log_at, 22.25)
            self.assertNotIn("_field_frame_times_ms", window.__dict__)
            self.assertNotIn("_last_field_frame_log_at", window.__dict__)
            self.assertIs(window._frame_telemetry, owner)
        finally:
            owner.frame_times_ms = original_samples
            owner.last_reported_at = original_reported_at

    def test_sources_start_only_after_services_hooks_and_profile_are_ready(self):
        window, phases = self.construct(autostart=False)
        self.assertEqual(phases, ["control-neutralized", "profile-load", "runtime-request", "profile-requested"])
        self.assertTrue(window.timer.isActive())
        self.assertEqual(window.timer.timerType(), Qt.TimerType.PreciseTimer)
        self.assertEqual(window.timer.interval(), window._render_timer_interval_ms)
        self.assertTrue(window.home_visible)
        settle_window_lifecycle(window, APP)
        self.assertFalse(window.backend.lifecycle_pending)
        self.assertFalse(window.configuration_change_pending)
        self.starts["mqtt"].assert_not_called()

    def test_autostart_does_not_supersede_the_profile_generation(self):
        window, phases = self.construct(autostart=True)
        self.assertEqual(phases.count("runtime-request"), 1)
        self.assertFalse(window.home_visible)
        self.assertEqual(window.startup_step, "done")
        self.assertTrue(window.configuration_change_pending)
        settle_window_lifecycle(window, APP)
        self.assertFalse(window.configuration_change_pending)
        self.starts["mqtt"].assert_called_once_with()


    def test_autostart_without_profile_captures_the_ordinary_request_projection(self):
        window, phases = self.construct(autostart=True, profile_present=False)
        self.assertEqual(phases, ["control-neutralized", "profile-load", "profile-absent", "runtime-request"])
        self.assertFalse(window.configuration_change_pending)
        self.assertIsNotNone(window._pending_scenario_configuration)
        radius = window.registry.get("QuickActionWheel").get_setting("Radius")
        previous = radius.value
        radius.value = previous + 1
        window.after_setting_changed(radius)
        self.assertEqual(radius.value, previous)
        settle_window_lifecycle(window, APP)
        self.assertIsNone(window._pending_scenario_configuration)
        self.starts["mqtt"].assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
