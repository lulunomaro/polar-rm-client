"""Execute real normal window ticks and CPU paint entry without native GL.

All frame stages and drawing methods are production implementations. The paint
smoke redirects only the final QPainter device from an unavailable offscreen
QOpenGLWidget to a real QImage. It does not qualify GL presentation, sound,
network peers, cameras, serial devices, or physical robot output.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

# Set before optional inference/Qt imports, including standalone execution.
os.environ["CI"] = "true"
os.environ["POLAR_DISABLE_LOCAL_CAPTURE"] = "1"
os.environ["POLAR_DISABLE_MEDIA_PRELOAD"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from rm_client.ui import polar_app
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])
NETWORK_ATTEMPTS = []
NETWORK_AUDIT_ACTIVE = False
RUNTIME_STAGES = ("control.75hz", "backend.tick", "backend.apply", "registry", "polar.link")


def reject_python_network(event, arguments):
    if NETWORK_AUDIT_ACTIVE and event in {"socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo"}:
        NETWORK_ATTEMPTS.append(event)
        raise AssertionError(f"Normal-frame smoke must remain offline: {event}")


def disable_network_audit():
    global NETWORK_AUDIT_ACTIVE
    NETWORK_AUDIT_ACTIVE = False


# The installed hook is inert outside each fixture, so importing this suite
# cannot change another suite's policy. Native Qt audio probes are outside
# Python's audit API and are not claimed as tested devices.
sys.addaudithook(reject_python_network)


class NormalWindowFrameTests(unittest.TestCase):
    def setUp(self):
        global NETWORK_AUDIT_ACTIVE
        NETWORK_ATTEMPTS.clear()
        NETWORK_AUDIT_ACTIVE = True
        self.addCleanup(disable_network_audit)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.window = polar_app.PolarRMWindow()
        self.addCleanup(self.close_window)
        # Drive callbacks explicitly so a Qt exception hook cannot swallow an
        # error from the normal frame and leave the unittest falsely green.
        self.window.timer.stop()
        settle_window_lifecycle(self.window, APP)
        for timer in self.window.findChildren(QTimer):
            timer.stop()
        self.window.resize(640, 360)
        self.window.ui_audio_enabled = False
        self.window.registry.get("AudioFeedback").enabled = False
        self.assertFalse(self.window.backend.lifecycle_pending)
        self.assertFalse(self.window.configuration_change_pending)
        self.assertFalse(self.window._window_lifecycle.closing)
        self.assertTrue(self.window.backend.mqtt.mock_output)
        self.assertFalse(any(self.window.control.key_state.values()))

    def close_window(self):
        close_window_safely(self.window, APP)
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(NETWORK_ATTEMPTS, [])

    def normal_tick(self):
        window = self.window
        now = time.perf_counter()
        window._last_control_tick_time = now - .1
        window._last_logic_time = now - .1
        window._ui_tick_perf_log_at = now
        before_frame = window._ui_frame_index
        before_errors = window._tick_error_count
        before_stages = {stage: window._ui_tick_stages.get(stage, {}).get("count", 0)
                         for stage in RUNTIME_STAGES}
        window.on_tick()
        self.assertEqual(window._ui_frame_index, before_frame + 1)
        self.assertEqual(window._tick_error_count, before_errors, window._tick_last_error)
        self.assertGreaterEqual(window._last_control_tick_time, now)
        self.assertGreaterEqual(window._last_logic_time, now)
        for stage in RUNTIME_STAGES:
            self.assertGreater(window._ui_tick_stages[stage]["count"], before_stages[stage], stage)
        self.assertTrue(math.isfinite(window.frame_dt))
        self.assertGreater(window.render_fps_target, 0)
        self.assertEqual(NETWORK_ATTEMPTS, [])

    def test_settled_normal_ticks_execute_real_runtime_and_presentation_stages(self):
        for frame in range(3):
            with self.subTest(frame=frame):
                self.normal_tick()
        self.assertFalse(self.window.backend.lifecycle_pending)
        self.assertFalse(self.window.configuration_change_pending)

    def test_normal_frames_pause_during_pending_transition_and_resume_after_settlement(self):
        self.normal_tick()
        window = self.window
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original_stop = window.backend.udp.stop

        def delayed_stop():
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Normal-frame test did not release its inert teardown")
            original_stop()

        with patch.object(window.backend.udp, "stop", side_effect=delayed_stop):
            try:
                # The public backend request uses real worker/owner completion;
                # start=False keeps this test away from transport startup.
                window.backend.request_scenario("LabDebug", start=False, registry=window.registry)
                self.assertTrue(entered.wait(1))
                self.assertTrue(window.backend.lifecycle_pending)
                self.assertFalse(window.configuration_change_pending)
                before = (window._ui_frame_index, window._last_control_tick_time,
                          window._last_logic_time, window._tick_error_count)
                before_stages = {stage: window._ui_tick_stages[stage]["count"]
                                 for stage in RUNTIME_STAGES}
                window.on_tick()
                self.assertEqual((window._ui_frame_index, window._last_control_tick_time,
                                  window._last_logic_time, window._tick_error_count), before)
                self.assertEqual({stage: window._ui_tick_stages[stage]["count"]
                                  for stage in RUNTIME_STAGES}, before_stages)
                self.assertFalse(any(window.control.key_state.values()))
            finally:
                release.set()
            settle_window_lifecycle(window, APP)
        self.normal_tick()
        self.assertFalse(window.backend.lifecycle_pending)

    def test_normal_paint_entry_renders_launcher_hud_and_clickgui_to_real_images(self):
        window = self.window
        self.assertFalse(window.gpu_compositor.available)
        preference = window.registry.get("ClickGui").get_setting("Render Backend")
        saved_preference = preference.value
        for surface in ("launcher", "hud", "clickgui"):
            with self.subTest(surface=surface):
                window.home_visible = surface == "launcher"
                if surface == "clickgui":
                    window.set_clickgui_visible(True, announce=False, play_sound=False)
                    window.gui_timeline.update(1.0)
                self.normal_tick()
                image = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                paint_devices = []

                class ImagePainter(QPainter):
                    def __init__(self, device):
                        paint_devices.append(device)
                        super().__init__(image if device is window else device)

                samples = len(window.fps_samples)
                with patch.object(polar_app, "QPainter", ImagePainter):
                    window.paintGL()
                self.assertEqual(sum(device is window for device in paint_devices), 1)
                self.assertEqual(len(window.fps_samples), samples + 1)
                self.assertTrue(math.isfinite(window.state.fps))
                self.assertGreater(window.state.fps, 0)
                self.assertFalse(image.paintingActive())
                self.assertTrue(any(image.pixelColor(x, y).alpha()
                                    for x in range(0, 640, 40) for y in range(0, 360, 40)))
                self.assertEqual(preference.value, saved_preference)
                if surface in {"launcher", "clickgui"}:
                    self.assertGreater(len(window.hotspots), 0)
                self.assertEqual(NETWORK_ATTEMPTS, [])


if __name__ == "__main__":
    unittest.main()
