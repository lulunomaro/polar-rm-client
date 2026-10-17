"""Freeze normal-video source/timeline observations on actual offscreen windows.

The five production adapters, real registry, QImage/QPixmap and numpy storage
remain live. Only native starts and explicit failure/reentrant callback ports
are replaced. These are baseline contracts, including inconsistent empty-source
and NaN behavior; they are not desktop GL, camera, inference or network evidence.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import copy, deepcopy
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ["CI"] = "true"
os.environ["POLAR_DISABLE_LOCAL_CAPTURE"] = "1"
os.environ["POLAR_DISABLE_MEDIA_PRELOAD"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication
from rm_client.backend.mqtt_backend import RmMqttBackend
from rm_client.backend.serial_backend import RmSerialBackend
from rm_client.backend.video_backend import RmVideoBackend
from rm_client.ui.polar.mixins import video_backend_mixin as video
from rm_client.ui.polar_app import PolarRMWindow
from rm_client.vision import CapturedFrame
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])
_NETWORK_AUDIT_ACTIVE = False
_NETWORK_ATTEMPTS = []


def reject_python_network(event, arguments):
    if _NETWORK_AUDIT_ACTIVE and event in {
        "socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo",
    }:
        _NETWORK_ATTEMPTS.append(event)
        raise AssertionError(f"Video timeline contract must remain offline: {event}")


sys.addaudithook(reject_python_network)


class NormalVideoTimelineContractTests(unittest.TestCase):
    def setUp(self):
        global _NETWORK_AUDIT_ACTIVE
        _NETWORK_AUDIT_ACTIVE = True
        _NETWORK_ATTEMPTS.clear()
        self.addCleanup(self.finish_network_audit)
        stack = ExitStack()
        self.addCleanup(stack.close)
        directory = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(patch.dict(os.environ, {
            "POLAR_USER_DATA_ROOT": directory, "POLAR_AUTOSTART": "0",
        }))
        # Follow the actual-window construction fixture: no connection starts.
        for owner in (RmMqttBackend, RmSerialBackend, RmVideoBackend):
            stack.enter_context(patch.object(owner, "start"))
        self.window = PolarRMWindow()
        self.addCleanup(self.close_window)
        self.window.timer.stop()
        settle_window_lifecycle(self.window, APP)
        for timer in self.window.findChildren(QTimer):
            timer.stop()
        self.window.resize(160, 90)
        self.window.ui_audio_enabled = False
        self.window.registry.get("AudioFeedback").enabled = False
        self.camera = self.window.registry.get("CameraFeed")
        self.source = self.camera.get_setting("Source")
        self.camera.get_setting("Mirror").value = False
        self.window.state.deploy_mode = 0
        self.window.hero_deploy_ui.pending_mode = None
        self.window._vision_source = "Mock"
        self.bgr = np.zeros((2, 4, 3), dtype=np.uint8)
        self.bgr[:, 0, 2] = 255
        self.bgr[:, -1, 0] = 255
        self.rgb = self.bgr[:, :, ::-1].copy()
        self.frame = CapturedFrame(8, 100, 4, 2, self.rgb, self.bgr)
        self.seed()

    def test_live_frame_adapters_and_legacy_fields_share_one_timeline(self):
        window = self.window
        owner = window._normal_video_timeline
        fields = (
            ("_last_capture_sequence", "capture_sequence", 14),
            ("_display_frame_sequence", "display_sequence", 28),
            ("_last_backend_video_at", "backend_admitted_at", 13.25),
            ("_normal_backend_mode", "requested_mode", "udp hevc"),
            ("_normal_frame_source", "frame_source", "backend"),
        )
        for alias, field, value in fields:
            with self.subTest(alias=alias):
                previous = getattr(owner, field)
                setattr(window, alias, value)
                self.assertIs(getattr(owner, field), value)
                setattr(owner, field, previous)
                self.assertIs(getattr(window, alias), previous)
        with patch.object(window.camera_worker, "latest_frame", return_value=self.frame):
            window.consume_capture_frame()
        self.assertIs(window._normal_video_timeline, owner)
        self.assertEqual(owner.capture_sequence, self.frame.sequence)
        self.assertEqual(owner.display_sequence, 31)
        self.assertEqual(owner.frame_source, "webcam")

    def finish_network_audit(self):
        global _NETWORK_AUDIT_ACTIVE
        _NETWORK_AUDIT_ACTIVE = False
        self.assertEqual(_NETWORK_ATTEMPTS, [])

    def close_window(self):
        close_window_safely(self.window, APP)
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def seed(self, *, mode="webcam", source="webcam", at=0.0):
        window = self.window
        window._normal_backend_mode = mode
        window._normal_frame_source = source
        window._last_backend_video_at = at
        window._last_capture_sequence = 7
        window._display_frame_sequence = 30
        window._backend_vision_sequence = 4
        window._vision_scene_luminance = .75
        window.state.vision_scene_luminance = .75
        window.frame_image = QImage(3, 2, QImage.Format.Format_RGB888)
        window.frame_image.fill(0)
        window.frame_pixmap = QPixmap.fromImage(window.frame_image)
        window._gpu_background_key = ("background",)
        window._gpu_blur_source_key = ("blur",)
        window._gpu_background_changed = False
        self.source.value = mode

    def snapshot(self):
        window = self.window
        return (window._normal_backend_mode, window._normal_frame_source,
                window._last_backend_video_at, window._last_capture_sequence,
                window._display_frame_sequence, window._backend_vision_sequence,
                id(window.frame_image), id(window.frame_pixmap),
                window._vision_scene_luminance, window.state.vision_scene_luminance)

    def test_normalized_modes_keep_original_backend_argument_and_empty_quirk(self):
        for raw, route, ready in (
            ("Webcam", "capture", True), (" WEbCaM \t", "capture", True),
            ("Synthetic", "synthetic", False), (" sYnThEtIc ", "synthetic", False),
            ("UDP HEVC", "backend", True), (" Native 0x0310 ", "backend", True),
            ("unknown input", "backend", True), ("", "backend", False),
            (" \t", "backend", False),
        ):
            with self.subTest(raw=raw):
                self.seed(mode="prior", source="backend", at=4.0)
                self.source.value = raw
                with patch.object(self.window.camera_worker, "latest_frame", return_value=self.frame) as capture, \
                     patch.object(self.window.backend, "latest_video_frame", return_value=(self.bgr, 10.0, "ignored")) as backend:
                    self.window.consume_capture_frame()
                self.assertEqual(self.window._normal_backend_mode, raw.strip().lower())
                self.assertEqual(capture.call_count, int(route == "capture"))
                self.assertEqual(backend.call_count, int(route == "backend"))
                if route == "backend":
                    backend.assert_called_once_with(self.window.NORMAL_BACKEND_VIDEO_STALE_S, raw)
                    self.assertEqual(self.window._normal_frame_source, "backend")
                if route == "synthetic":
                    self.assertIsNone(self.window.frame_image)
                self.assertEqual(self.window.normal_video_frame_ready(10.1), ready)

    def test_switch_clear_observes_old_mode_then_backend_observes_new_mode(self):
        window = self.window
        self.source.value = " UDP HEVC "
        before = self.snapshot()
        trace = []
        original_discard = window.liquid_glass.discard
        original_mode = window.setting_mode

        def read(module, name, default):
            value = original_mode(module, name, default)
            trace.append(("source", value, self.snapshot()))
            return value

        def discard(key):
            trace.append(("discard", key, self.snapshot()))
            return original_discard(key)

        def latest(timeout, mode):
            trace.append(("latest", mode, self.snapshot()))
            return self.bgr, 10.0, "udp"

        with patch.object(window, "setting_mode", side_effect=read), \
             patch.object(window.liquid_glass, "discard", side_effect=discard), \
             patch.object(window.backend, "latest_video_frame", side_effect=latest):
            window.consume_capture_frame()
        self.assertEqual([item[0] for item in trace], ["source", "discard", "source", "latest"])
        self.assertEqual(trace[1][2], before)
        self.assertEqual(trace[2][2][:6], ("udp hevc", "none", 0.0, 7, 30, 4))
        self.assertEqual(trace[3][2], trace[2][2])

    def test_switch_clear_failure_does_not_publish_mode_or_query_producer(self):
        for method in ("consume_capture_frame", "consume_backend_video_frame"):
            with self.subTest(method=method):
                self.seed()
                self.source.value = "UDP HEVC"
                before = self.snapshot()
                with patch.object(self.window.liquid_glass, "discard", side_effect=OSError("glass clear")), \
                     patch.object(self.window.backend, "latest_video_frame") as latest, \
                     patch.object(self.window.camera_worker, "latest_frame") as capture:
                    with self.assertRaisesRegex(OSError, "glass clear"):
                        getattr(self.window, method)()
                self.assertEqual(self.snapshot(), before)
                latest.assert_not_called()
                capture.assert_not_called()

    def test_reentrant_clear_changes_second_source_read_and_worker_before_query(self):
        window = self.window
        self.source.value = "UDP HEVC"
        original_discard = window.liquid_glass.discard
        original_worker = window.camera_worker
        replacement = SimpleNamespace(latest_frame=lambda: self.frame)
        modes = []
        clears = []

        def discard(key):
            clears.append(window._normal_backend_mode)
            self.source.value = "Native 0x0310"
            window.camera_worker = replacement
            return original_discard(key)

        def latest(timeout, mode):
            modes.append((mode, window._normal_backend_mode))
            return self.bgr, 10.0, "udp"

        try:
            with patch.object(window.liquid_glass, "discard", side_effect=discard), \
                 patch.object(window.backend, "latest_video_frame", side_effect=latest), \
                 patch.object(original_worker, "latest_frame") as old_capture:
                window.consume_capture_frame()
            self.assertEqual(clears, ["webcam", "udp hevc"])
            self.assertEqual(modes, [("Native 0x0310", "native 0x0310")])
            self.assertIs(window.camera_worker, replacement)
            old_capture.assert_not_called()
        finally:
            window.camera_worker = original_worker

    def test_capture_uses_worker_replaced_during_clear_but_not_source_reread(self):
        window = self.window
        self.seed(mode="udp hevc", source="backend", at=5.0)
        self.source.value = "Webcam"
        original_worker = window.camera_worker
        original_discard = window.liquid_glass.discard
        observed = []

        def latest():
            observed.append((window._normal_backend_mode, self.source.value))
            self.source.value = "Synthetic"
            window.camera_worker = original_worker
            return self.frame

        replacement = SimpleNamespace(latest_frame=latest)

        def discard(key):
            window.camera_worker = replacement
            return original_discard(key)

        try:
            with patch.object(window.liquid_glass, "discard", side_effect=discard), \
                 patch.object(original_worker, "latest_frame") as old_capture:
                window.consume_capture_frame()
            old_capture.assert_not_called()
            self.assertEqual(observed, [("webcam", "Webcam")])
            self.assertEqual(self.snapshot()[:6], ("webcam", "webcam", 0.0, 8, 31, 4))
            self.assertEqual(self.source.value, "Synthetic")
            self.assertFalse(window.normal_video_frame_ready(10.0))
        finally:
            window.camera_worker = original_worker

    def test_capture_retains_initial_camera_module_but_backend_rereads_after_producer(self):
        window = self.window
        replacement_camera = copy(self.camera)
        replacement_camera.settings = deepcopy(self.camera.settings)
        replacement_camera.get_setting("Mirror").value = True
        original_get = window.registry.get
        original_mode = window.setting_mode
        original_bool = window.setting_bool
        for method, mode, expected_mirror in (
            ("consume_capture_frame", "webcam", False),
            ("consume_backend_video_frame", "udp hevc", True),
        ):
            with self.subTest(method=method):
                self.seed(mode=mode)
                replaced = False
                observations = []

                def get(name):
                    if name == "CameraFeed" and replaced:
                        return replacement_camera
                    return original_get(name)

                def source(module, name, default):
                    observations.append(("source", module is self.camera))
                    return original_mode(module, name, default)

                def mirror(module, name, default):
                    observations.append(("mirror", module is replacement_camera))
                    return original_bool(module, name, default)

                def latest(*args):
                    nonlocal replaced
                    observations.append(("latest",))
                    replaced = True
                    window._vision_source = "Mock"
                    return self.frame if mode == "webcam" else (self.bgr, 6.0, "udp")

                with patch.object(window.registry, "get", side_effect=get), \
                     patch.object(window, "setting_mode", side_effect=source), \
                     patch.object(window, "setting_bool", side_effect=mirror), \
                     patch.object(window.camera_worker, "latest_frame", side_effect=latest), \
                     patch.object(window.backend, "latest_video_frame", side_effect=latest):
                    getattr(window, method)()
                self.assertEqual(observations, [("source", True), ("latest",), ("mirror", expected_mirror)])
                self.assertEqual(window.frame_image.pixelColor(0, 0).blue(), 255 if expected_mirror else 0)

    def test_capture_none_same_and_older_sequence_leave_all_fields_unchanged(self):
        for sequence in (None, 7, 6):
            with self.subTest(sequence=sequence):
                frame = None if sequence is None else CapturedFrame(sequence, 100, 4, 2, self.rgb, self.bgr)
                before = self.snapshot()
                with patch.object(self.window.camera_worker, "latest_frame", return_value=frame), \
                     patch.object(self.window, "update_hud_liquid_source") as glass:
                    self.window.consume_capture_frame()
                self.assertEqual(self.snapshot(), before)
                glass.assert_not_called()

    def test_capture_sequence_and_luminance_precede_image_and_mirror_failure(self):
        window = self.window
        for stage in ("construct", "copy", "mirror"):
            with self.subTest(stage=stage):
                self.seed()
                self.camera.get_setting("Mirror").value = True
                before = self.snapshot()
                observed = []
                case = self

                class FaultImage(QImage):
                    def __init__(self, *args):
                        observed.append(("construct", case.snapshot()))
                        if stage == "construct":
                            raise TypeError("image construct")
                        super().__init__(*args)

                    def copy(self, *args):
                        observed.append(("copy", case.snapshot()))
                        if stage == "copy":
                            raise TypeError("image copy")
                        return FaultImage(super().copy(*args))

                    def mirrored(self, *args):
                        observed.append(("mirror", case.snapshot()))
                        raise TypeError("image mirror")

                with patch.object(window.camera_worker, "latest_frame", return_value=self.frame), \
                     patch.object(video, "QImage", FaultImage):
                    with self.assertRaisesRegex(TypeError, f"image {stage}"):
                        window.consume_capture_frame()
                after = self.snapshot()
                self.assertEqual(after[:6], ("webcam", "webcam", 0.0, 8, 30, 4))
                self.assertEqual(after[6:8], before[6:8])
                self.assertEqual(after[8:], (1.0 / 3.0, 1.0 / 3.0))
                self.assertTrue(observed)
                self.assertTrue(all(state == after for _, state in observed))
                with patch.object(window.camera_worker, "latest_frame", return_value=self.frame), \
                     patch.object(window, "update_hud_liquid_source") as retry:
                    window.consume_capture_frame()
                retry.assert_not_called()

    def test_capture_luminance_failure_is_ignored_and_real_image_is_mirrored(self):
        class BadLuminance:
            def __getitem__(self, key):
                raise ValueError("luminance unavailable")

        frame = CapturedFrame(8, 100, 4, 2, self.rgb, BadLuminance())
        self.camera.get_setting("Mirror").value = True
        with patch.object(self.window.camera_worker, "latest_frame", return_value=frame):
            self.window.consume_capture_frame()
        self.assertEqual(self.snapshot()[:6], ("webcam", "webcam", 0.0, 8, 31, 4))
        self.assertEqual(self.snapshot()[8:], (.75, .75))
        self.assertEqual(self.window.frame_image.pixelColor(0, 0).blue(), 255)
        self.assertIn("hud", self.window.liquid_glass.snapshots)

    def test_capture_pixmap_failure_keeps_published_image_source_and_counter(self):
        window = self.window
        self.seed(source="none")
        old_pixmap = window.frame_pixmap
        with patch.object(window.camera_worker, "latest_frame", return_value=self.frame), \
             patch.object(video.QPixmap, "fromImage", side_effect=TypeError("pixmap failure")), \
             patch.object(window, "update_hud_liquid_source") as glass:
            with self.assertRaisesRegex(TypeError, "pixmap failure"):
                window.consume_capture_frame()
        self.assertEqual(self.snapshot()[:6], ("webcam", "webcam", 0.0, 8, 31, 4))
        self.assertIs(window.frame_pixmap, old_pixmap)
        self.assertEqual(window.frame_image.size().width(), 4)
        glass.assert_not_called()

    def test_backend_stale_discards_udp_before_glass_and_preserves_counters(self):
        window = self.window
        for bgr, at in ((None, 12.0), (self.bgr, 0.0), (self.bgr, -1.0)):
            with self.subTest(at=at, absent=bgr is None):
                self.seed(mode="udp hevc", source="backend", at=5.0)
                trace = []
                original_discard = window.liquid_glass.discard

                def stale(timeout):
                    trace.append(("udp", self.snapshot()))

                def discard(key):
                    trace.append(("glass", self.snapshot()))
                    return original_discard(key)

                before = self.snapshot()
                with patch.object(window.backend, "latest_video_frame", return_value=(bgr, at, "udp")), \
                     patch.object(window.backend.udp, "clear_stale_decoded_frame", side_effect=stale), \
                     patch.object(window.liquid_glass, "discard", side_effect=discard):
                    window.consume_backend_video_frame()
                self.assertEqual(trace, [("udp", before), ("glass", before)])
                self.assertEqual(self.snapshot()[:6], ("udp hevc", "none", 0.0, 7, 30, 4))
                self.assertIsNone(window.frame_image)
                self.assertIsNone(window.frame_pixmap)
                self.assertIsNone(window._gpu_background_key)
                self.assertIsNone(window._gpu_blur_source_key)
                self.assertTrue(window._gpu_background_changed)

    def test_stale_udp_and_glass_failures_are_separate_short_circuit_boundaries(self):
        window = self.window
        for stage in ("udp", "glass"):
            with self.subTest(stage=stage):
                self.seed(mode="udp hevc", source="backend", at=5.0)
                before = self.snapshot()
                with patch.object(window.backend, "latest_video_frame", return_value=(None, 0.0, "none")), \
                     patch.object(window.backend.udp, "clear_stale_decoded_frame", side_effect=OSError("udp") if stage == "udp" else None) as udp, \
                     patch.object(window.liquid_glass, "discard", side_effect=OSError("glass")) as glass:
                    with self.assertRaisesRegex(OSError, stage):
                        window.consume_backend_video_frame()
                self.assertEqual(self.snapshot(), before)
                udp.assert_called_once_with(window.NORMAL_BACKEND_VIDEO_STALE_S)
                self.assertEqual(glass.call_count, int(stage == "glass"))

    def test_backend_same_older_and_malformed_frames_do_not_advance_timeline(self):
        class CopyFails:
            def copy(self):
                raise OSError("copy failed")

        cases = ((self.bgr, 5.0), (self.bgr, 4.0),
                 (CopyFails(), 5.0), (CopyFails(), 4.0), (CopyFails(), 6.0),
                 (np.zeros((2, 4), dtype=np.uint8), 6.0),
                 (np.zeros((2, 4, 2), dtype=np.uint8), 6.0),
                 (np.zeros((0, 4, 3), dtype=np.uint8), 6.0),
                 (np.zeros((2, 0, 3), dtype=np.uint8), 6.0))
        for array, at in cases:
            with self.subTest(shape=getattr(array, "shape", "copy failure"), at=at):
                self.seed(mode="udp hevc", source="backend", at=5.0)
                before = self.snapshot()
                with patch.object(self.window.backend, "latest_video_frame", return_value=(array, at, "udp")), \
                     patch.object(self.window.backend.udp, "clear_stale_decoded_frame") as clear:
                    self.window.consume_backend_video_frame()
                self.assertEqual(self.snapshot(), before)
                clear.assert_not_called()

    def test_malformed_backend_timestamp_and_shape_errors_propagate_before_publication(self):
        malformed_shape = SimpleNamespace(copy=lambda: SimpleNamespace(ndim=3, shape=(2, 4, "bad")))
        for array, at, error in ((self.bgr, None, TypeError), (self.bgr, "bad", TypeError),
                                 (malformed_shape, 6.0, ValueError)):
            with self.subTest(at=at, error=error):
                self.seed(mode="udp hevc", source="backend", at=5.0)
                before = self.snapshot()
                with patch.object(self.window.backend, "latest_video_frame", return_value=(array, at, "udp")):
                    with self.assertRaises(error):
                        self.window.consume_backend_video_frame()
                self.assertEqual(self.snapshot(), before)

    def test_backend_luminance_failure_still_publishes_image_and_glass(self):
        class BadLuminance(np.ndarray):
            def mean(self, *args, **kwargs):
                raise ValueError("luminance failed")

        array = self.bgr.view(BadLuminance)
        self.seed(mode="udp hevc", source="backend", at=5.0)
        with patch.object(self.window.backend, "latest_video_frame", return_value=(array, 6.0, "udp")):
            self.window.consume_backend_video_frame()
        self.assertEqual(self.snapshot()[:6], ("udp hevc", "backend", 6.0, 7, 31, 4))
        self.assertEqual(self.snapshot()[8:], (.75, .75))
        self.assertIn("hud", self.window.liquid_glass.snapshots)

    def test_backend_copy_failure_does_not_consume_timestamp_retry_can_succeed(self):
        window = self.window
        self.seed(mode="udp hevc", source="backend", at=5.0)
        frame = SimpleNamespace(copy=lambda: None)
        with patch.object(window.backend, "latest_video_frame", side_effect=[(frame, 6.0, "udp"), (self.bgr, 6.0, "udp")]):
            window.consume_backend_video_frame()
            self.assertEqual(window._last_backend_video_at, 5.0)
            window.consume_backend_video_frame()
        self.assertEqual(self.snapshot()[:6], ("udp hevc", "backend", 6.0, 7, 31, 4))

    def test_backend_qimage_or_mirror_failure_does_not_consume_timestamp(self):
        window = self.window
        for stage in ("copy", "mirror"):
            with self.subTest(stage=stage):
                self.seed(mode="udp hevc", source="backend", at=5.0)
                self.camera.get_setting("Mirror").value = True
                before = self.snapshot()

                class FaultImage(QImage):
                    def copy(self, *args):
                        if stage == "copy":
                            raise TypeError("backend image copy")
                        return FaultImage(super().copy(*args))

                    def mirrored(self, *args):
                        raise TypeError("backend image mirror")

                with patch.object(window.backend, "latest_video_frame", return_value=(self.bgr, 6.0, "udp")), \
                     patch.object(video, "QImage", FaultImage):
                    with self.assertRaisesRegex(TypeError, f"backend image {stage}"):
                        window.consume_backend_video_frame()
                self.assertEqual(self.snapshot(), before)

    def test_backend_timestamp_and_display_precede_submit_but_image_and_luminance_follow(self):
        window = self.window
        self.seed(mode="udp hevc", source="webcam", at=5.0)
        before = self.snapshot()
        window._vision_source = "Local"
        observed = []

        def submit(sequence, width, height, bgr):
            observed.append(self.snapshot())
            self.assertEqual((sequence, width, height), (5, 4, 2))
            self.assertFalse(np.shares_memory(bgr, self.bgr))
            self.assertTrue(np.array_equal(bgr, self.bgr))
            raise RuntimeError("submit failed")

        with patch.object(window.local_vision, "running", True), \
             patch.object(window.local_vision, "submit", side_effect=submit), \
             patch.object(window.backend, "latest_video_frame", return_value=(self.bgr, 6.0, "udp")):
            with self.assertRaisesRegex(RuntimeError, "submit failed"):
                window.consume_backend_video_frame()
            self.assertEqual(observed[0][:6], ("udp hevc", "webcam", 6.0, 7, 31, 5))
            self.assertEqual(observed[0][6:], before[6:])
            self.assertEqual(self.snapshot(), observed[0])
            window.consume_backend_video_frame()
        self.assertEqual(len(observed), 1)

    def test_backend_vision_gate_is_observed_after_decode_and_accepts_only_exact_modes(self):
        window = self.window
        for source, running, submitted in (("Local", True, True), ("Auto", True, True),
                                           ("local", True, False), ("Mock", True, False),
                                           ("Local", False, False)):
            with self.subTest(source=source, running=running):
                self.seed(mode="udp hevc", source="backend", at=5.0)
                window._vision_source = "Mock"

                def latest(*args):
                    window._vision_source = source
                    return self.bgr, 6.0, "udp"

                with patch.object(window.local_vision, "running", running), \
                     patch.object(window.local_vision, "submit") as submit, \
                     patch.object(window.backend, "latest_video_frame", side_effect=latest):
                    window.consume_backend_video_frame()
                self.assertEqual(submit.call_count, int(submitted))
                self.assertEqual(self.snapshot()[:6], ("udp hevc", "backend", 6.0, 7, 31, 5 if submitted else 4))

    def test_backend_submit_uses_worker_and_source_observed_after_decode_callbacks(self):
        window = self.window
        self.seed(mode="udp hevc", source="webcam", at=5.0)
        window._vision_source = "Local"
        original_worker = window.local_vision
        trace = []
        replacement = SimpleNamespace(submit=lambda *args: trace.append(("submit", self.snapshot(), args)))

        class ChangingWorker:
            @property
            def running(self):
                trace.append(("running", window._last_backend_video_at, window._display_frame_sequence))
                window.local_vision = replacement
                return True

        def latest(timeout, mode):
            self.source.value = "Synthetic"
            self.camera.get_setting("Mirror").value = True
            window.local_vision = ChangingWorker()
            return self.bgr, 6.0, "udp"

        try:
            with patch.object(window.backend, "latest_video_frame", side_effect=latest):
                window.consume_backend_video_frame()
            self.assertEqual(trace[0], ("running", 6.0, 31))
            self.assertEqual(trace[1][0], "submit")
            self.assertEqual(trace[1][1][:6], ("udp hevc", "webcam", 6.0, 7, 31, 5))
            self.assertIs(window.local_vision, replacement)
            self.assertEqual(window.frame_image.pixelColor(0, 0).blue(), 255)
            self.assertEqual(window._normal_backend_mode, "udp hevc")
            self.assertFalse(window.normal_video_frame_ready(6.1))
        finally:
            window.local_vision = original_worker

    def test_backend_reentrant_submit_can_clear_then_adapter_still_publishes_image(self):
        window = self.window
        self.seed(mode="udp hevc", source="backend", at=5.0)
        window._vision_source = "Auto"
        observed = []

        def submit(*args):
            observed.append(self.snapshot()[:6])
            self.source.value = "Synthetic"
            self.assertTrue(window.clear_normal_backend_video_frame())

        with patch.object(window.local_vision, "running", True), \
             patch.object(window.local_vision, "submit", side_effect=submit), \
             patch.object(window.backend, "latest_video_frame", return_value=(self.bgr, 6.0, "udp")):
            window.consume_backend_video_frame()
        self.assertEqual(observed, [("udp hevc", "backend", 6.0, 7, 31, 5)])
        self.assertEqual(self.snapshot()[:6], ("udp hevc", "backend", 0.0, 7, 31, 5))
        self.assertFalse(window.frame_image.isNull())
        self.assertFalse(window.normal_backend_video_fresh(6.1))

    def test_backend_pixmap_and_glass_failures_preserve_exact_partial_publication(self):
        window = self.window
        for stage in ("pixmap", "glass"):
            with self.subTest(stage=stage):
                self.seed(mode="udp hevc", source="webcam", at=5.0)
                before = self.snapshot()
                with ExitStack() as stack:
                    stack.enter_context(patch.object(window.backend, "latest_video_frame", return_value=(self.bgr, 6.0, "udp")))
                    if stage == "pixmap":
                        stack.enter_context(patch.object(video.QPixmap, "fromImage", side_effect=TypeError("pixmap")))
                    else:
                        stack.enter_context(patch.object(window, "update_hud_liquid_source", side_effect=TypeError("glass")))
                    with self.assertRaisesRegex(TypeError, stage):
                        window.consume_backend_video_frame()
                self.assertEqual(self.snapshot()[:6], ("udp hevc", "backend", 6.0, 7, 31, 4))
                self.assertNotEqual(id(window.frame_image), before[6])
                if stage == "pixmap":
                    self.assertEqual(id(window.frame_pixmap), before[7])
                    self.assertEqual(self.snapshot()[8:], (.75, .75))
                else:
                    self.assertNotEqual(id(window.frame_pixmap), before[7])
                    self.assertEqual(self.snapshot()[8:], (1.0 / 3.0, 1.0 / 3.0))

    def test_backend_nan_publishes_but_is_not_fresh_and_does_not_block_next_frame(self):
        window = self.window
        self.seed(mode="udp hevc", source="backend", at=5.0)
        with patch.object(window.backend, "latest_video_frame", side_effect=[(self.bgr, float("nan"), "udp"), (self.bgr, 1.0, "udp")]):
            window.consume_backend_video_frame()
            self.assertTrue(math.isnan(window._last_backend_video_at))
            self.assertEqual(window._display_frame_sequence, 31)
            self.assertEqual(window._normal_frame_source, "backend")
            self.assertFalse(window.normal_backend_video_fresh(6.0))
            window.consume_backend_video_frame()
        self.assertEqual(window._last_backend_video_at, 1.0)
        self.assertEqual(window._display_frame_sequence, 32)

    def test_freshness_boundary_future_nan_and_owner_guards(self):
        window = self.window
        self.seed(mode="udp hevc", source="backend", at=10.0)
        stale = window.NORMAL_BACKEND_VIDEO_STALE_S
        self.assertTrue(window.normal_backend_video_fresh(10.0 + stale))
        self.assertFalse(window.normal_backend_video_fresh(10.0 + stale + 1e-6))
        self.assertTrue(window.normal_backend_video_fresh(9.0))
        self.assertFalse(window.normal_backend_video_fresh(float("nan")))
        for mode, source, at in (("native 0x0310", "backend", 10.0),
                                 ("udp hevc", "webcam", 10.0),
                                 ("udp hevc", "backend", 0.0),
                                 ("udp hevc", "backend", -1.0),
                                 ("udp hevc", "backend", float("nan"))):
            with self.subTest(mode=mode, source=source, at=at):
                self.seed(mode=mode, source=source, at=at)
                self.source.value = "UDP HEVC"
                self.assertFalse(window.normal_backend_video_fresh(10.1))

    def test_freshness_keeps_two_independent_source_observations(self):
        window = self.window
        for reads, expected in ((("UDP HEVC", "Webcam"), False),
                                (("UDP HEVC", "Native 0x0310"), True),
                                (("Native 0x0310", "UDP HEVC"), False),
                                (("UDP HEVC", ""), False)):
            with self.subTest(reads=reads):
                self.seed(mode="udp hevc", source="backend", at=10.0)
                with patch.object(window, "setting_mode", side_effect=reads) as read:
                    self.assertEqual(window.normal_backend_video_fresh(10.1), expected)
                self.assertEqual(read.call_count, 2)
                self.assertTrue(all(call.args == (self.camera, "Source", "Webcam") for call in read.call_args_list))

    def test_ready_backend_path_rereads_source_twice_but_webcam_and_synthetic_once(self):
        window = self.window
        self.seed(mode="udp hevc", source="backend", at=10.0)
        with patch.object(window, "setting_mode", side_effect=("UDP HEVC", "UDP HEVC", "Native 0x0310")) as reads:
            self.assertTrue(window.normal_video_frame_ready(10.1))
        self.assertEqual(reads.call_count, 3)
        for raw, expected in (("Webcam", True), ("", True), ("Synthetic", False)):
            with self.subTest(raw=raw):
                self.seed()
                with patch.object(window, "setting_mode", return_value=raw) as reads:
                    self.assertEqual(window.normal_video_frame_ready(10.1), expected)
                reads.assert_called_once_with(self.camera, "Source", "Webcam")
        window._normal_backend_mode = "udp hevc"
        self.source.value = "Webcam"
        self.assertFalse(window.normal_video_frame_ready())
        window._normal_backend_mode = "webcam"
        window._normal_frame_source = "none"
        self.assertFalse(window.normal_video_frame_ready())

    def test_clear_noop_still_discards_glass_but_leaves_images_and_cache_untouched(self):
        window = self.window
        self.seed(mode="synthetic", source="none", at=0.0)
        window.update_hud_liquid_source(window.frame_image)
        before = self.snapshot()
        self.assertIn("hud", window.liquid_glass.snapshots)
        self.assertFalse(window.clear_normal_backend_video_frame())
        self.assertNotIn("hud", window.liquid_glass.snapshots)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(window._gpu_background_key, ("background",))
        self.assertEqual(window._gpu_blur_source_key, ("blur",))
        self.assertFalse(window._gpu_background_changed)

    def test_clear_rechecks_timeline_after_discard_reentrancy(self):
        window = self.window
        self.seed(mode="udp hevc", source="backend", at=5.0)
        old_image = window.frame_image
        original = window.liquid_glass.discard

        def discard(key):
            window._last_backend_video_at = 0.0
            window._normal_frame_source = "none"
            return original(key)

        with patch.object(window.liquid_glass, "discard", side_effect=discard):
            self.assertFalse(window.clear_normal_backend_video_frame())
        self.assertIs(window.frame_image, old_image)
        self.assertEqual(window._gpu_background_key, ("background",))

    def test_switch_away_back_keeps_capture_display_and_backend_vision_counters_independent(self):
        window = self.window
        with patch.object(window.camera_worker, "latest_frame", return_value=self.frame), \
             patch.object(window.backend, "latest_video_frame", side_effect=[(self.bgr, 10.0, "udp"), (self.bgr, 11.0, "udp")]):
            window.consume_capture_frame()
            self.assertEqual((window._last_capture_sequence, window._display_frame_sequence), (8, 31))
            self.source.value = "UDP HEVC"
            window.consume_capture_frame()
            window.consume_capture_frame()
            self.assertEqual((window._last_capture_sequence, window._display_frame_sequence), (8, 33))
            self.source.value = "Synthetic"
            window.consume_capture_frame()
            self.source.value = "Webcam"
            window.consume_capture_frame()
            self.assertIsNone(window.frame_image)
            self.assertEqual((window._last_capture_sequence, window._display_frame_sequence), (8, 33))
        frame = CapturedFrame(9, 101, 4, 2, self.rgb, self.bgr)
        with patch.object(window.camera_worker, "latest_frame", return_value=frame):
            window.consume_capture_frame()
        self.assertEqual(self.snapshot()[:6], ("webcam", "webcam", 0.0, 9, 34, 4))

    def test_authoritative_deploy_state_is_rechecked_after_native_callback(self):
        window = self.window
        window.hero_deploy_ui.pending_mode = 1
        before = self.snapshot()

        def native():
            window.state.deploy_mode = 1

        with patch.object(window, "consume_hero_native_video_frame", side_effect=native) as native_call, \
             patch.object(window, "setting_mode") as source, \
             patch.object(window.camera_worker, "latest_frame") as capture:
            window.consume_capture_frame()
        native_call.assert_called_once_with()
        source.assert_not_called()
        capture.assert_not_called()
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
