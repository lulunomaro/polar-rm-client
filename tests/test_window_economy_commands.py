"""Actual-window economy commands with inert devices and isolated runtime files.

These tests exercise input and transaction wiring, not broker/robot output or
native OpenGL presentation. The existing economy bridge remains authoritative.
"""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("CI", "true")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter
from PySide6.QtWidgets import QApplication
from rm_client.backend.mqtt_backend import RmMqttBackend
from rm_client.backend.serial_backend import RmSerialBackend
from rm_client.backend.safety import ControlState
from rm_client.backend.video_backend import RmVideoBackend
from rm_client.economy import EconomyOffer, EconomyRequest
from rm_client.scenarios import SCENARIOS
from rm_client.ui.polar_app import PolarRMWindow
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])
OFFER = EconomyOffer("small", "ammo", "Buy ammo", "Small ammo", "Rules", "item", 1, 30, 1, 1, True)


class WindowEconomyCommands(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="polar-economy-")
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": directory.name, "POLAR_AUTOSTART": "0"})
        environment.start()
        self.addCleanup(environment.stop)
        for backend in (RmMqttBackend, RmSerialBackend, RmVideoBackend):
            start = patch.object(backend, "start")
            start.start()
            self.addCleanup(start.stop)
        self.window = PolarRMWindow()
        self.addCleanup(self.close_window)
        settle_window_lifecycle(self.window, APP)
        self.window.timer.stop()
        self.window.ui_audio_enabled = False
        self.window.home_visible = False
        self.window.gui_visible = False
        self.window.economy_visible = True
        self.window.economy_category = "ammo"
        self.window.economy_selected_key = "small"
        self.window.economy_island.progress = 1.
        self.window.state.remaining_economy = 500
        self.offers = [OFFER, replace(OFFER, key="large")]
        offers = patch.object(self.window, "economy_offers", side_effect=lambda category=None: [
            item for item in self.offers if category is None or item.category == category])
        offers.start()
        self.addCleanup(offers.stop)

    def close_window(self):
        close_window_safely(self.window, APP)
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def key(self, code, *, repeat=False, release=False):
        event = QKeyEvent(QEvent.Type.KeyRelease if release else QEvent.Type.KeyPress,
                          code, Qt.KeyboardModifier.NoModifier, "", repeat)
        if release:
            self.window.keyReleaseEvent(event)
        else:
            self.window.keyPressEvent(event)
        return event

    def test_double_confirmation_same_selection_and_pending_never_duplicate_request(self):
        window = self.window
        with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=100.):
            window.activate_economy_offer(OFFER)
            self.assertIsNone(window.economy_bridge.latest)
            window.select_economy_offer("small")
            self.assertEqual(window.economy_armed_key, "small")
            window.activate_economy_offer(OFFER)
            transaction = window.economy_bridge.latest
            self.assertIsNotNone(transaction)
            self.assertEqual(transaction.status, "pending")
            self.assertEqual(len(window.economy_bridge.history), 1)
            window.submit_economy_offer(OFFER)
            self.assertEqual(len(window.economy_bridge.history), 1)
            self.assertEqual(window.state.remaining_economy, 500)
            window.select_economy_offer("large")
            self.assertEqual(window.economy_armed_key, "")

    def test_key_repeat_cannot_arm_and_hold_release_rechecks_latest_offer(self):
        window = self.window
        self.offers[0] = replace(OFFER, confirmation="hold")
        with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=100.):
            self.key(Qt.Key.Key_Return, repeat=True)
            self.assertEqual(window.economy_hold_key, "")
            self.key(Qt.Key.Key_Return)
            self.assertEqual(window.economy_hold_key, "small")
        self.offers[0] = replace(self.offers[0], enabled=False, reason="availability changed")
        with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=100.7):
            self.key(Qt.Key.Key_Return, release=True)
        self.assertEqual(window.economy_hold_key, "")
        self.assertIsNone(window.economy_bridge.latest)
        self.assertEqual(window.state.remaining_economy, 500)

    def test_pointer_release_outside_does_not_submit_but_full_hold_does(self):
        window = self.window
        offer = self.offers[0] = replace(OFFER, confirmation="hold")
        with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=100.):
            window.begin_economy_hold(offer, QRectF(10., 10., 100., 30.))
        with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=100.7):
            window.finish_economy_hold(QPointF(5., 5.))
        self.assertIsNone(window.economy_bridge.latest)
        with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=101.):
            window.begin_economy_hold(offer, QRectF(10., 10., 100., 30.))
        with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=101.7):
            window.finish_economy_hold(QPointF(20., 20.))
        self.assertEqual(len(window.economy_bridge.history), 1)
        self.assertEqual(window.economy_bridge.latest.offer.key, "small")
        self.assertEqual(window.state.remaining_economy, 500)
        window.finish_economy_hold()
        self.assertEqual(len(window.economy_bridge.history), 1)

    def test_pending_configuration_rejects_keyboard_before_arm_or_hold(self):
        window = self.window
        with patch.object(window, "_pending_configuration", object(), create=True):
            self.key(Qt.Key.Key_Return)
            self.assertEqual(window.economy_armed_key, "")
            self.assertEqual(window.economy_hold_key, "")
            self.assertIsNone(window.economy_bridge.latest)

    def test_publication_exception_consumes_real_qualification_and_locks_control(self):
        window = self.window
        request = EconomyRequest(1, 100., "RMUC7v7", "Infantry1", "small", 1, 30, 1, 1,
                                 "CommonCommand", b"test", False)
        with patch.object(window.scenario_manager, "current", SCENARIOS["OfficialEvent"]):
            token = window.backend.begin_qualification({"economy"})
            window._economy_qualification_token = token.value
            self.assertIs(window.backend.safety.control_state, ControlState.QUALIFICATION)
            observed = []
            def fail_publication(payload, *, qualification_token):
                observed.append((payload, qualification_token, window._economy_qualification_token))
                raise OSError("publication interrupted")
            with patch.object(window.mqtt, "publish_common_command", side_effect=fail_publication):
                with self.assertRaisesRegex(OSError, "publication interrupted"):
                    window.send_economy_request(request)
            self.assertEqual(observed, [(b"test", token.value, None)])
            self.assertIsNone(window._economy_qualification_token)
            self.assertIsNone(window.backend.safety._qualification)
            self.assertIs(window.backend.safety.control_state, ControlState.LOCKED)
            self.assertEqual(window.state.remaining_economy, 500)

    def test_actual_window_gpu_commands_and_raster_pixels_match_original_scene(self):
        from economy_render_fixture import record
        from economy_scene_failure_fixture import REFERENCE
        window = self.window
        window.registry.get("EconomyPanel").get_setting("Shop Layout").value = "Control Center"
        window._collect_gpu_hud_commands = False
        window.student_reload_expanded = True

        def reset(held):
            window.hotspots.clear()
            window.gpu_compositor.begin_frame()
            window.economy_category = "ammo"
            window.economy_selected_key = "stale"
            window.economy_selected = 17
            window.economy_hold_key = "small" if held else ""
            window.economy_hold_started = 99.7
            self.offers[0] = replace(OFFER, confirmation="hold" if held else "double")
            for scroll in (window.economy_offers_scroll, window.economy_detail_scroll):
                scroll.position = 30.
                scroll.target = 30.
                scroll.velocity = 0.
            window._hotspot_clip = None

        def state():
            return record((window.hotspots, window.economy_selected_key, window.economy_selected,
                           window.economy_offers_scroll.position, window.economy_detail_scroll.position))

        with patch.object(window.anim, "value", side_effect=lambda key, target, *a, **k: target), \
             patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=100.), \
             patch.object(window.economy_bridge, "submit", side_effect=AssertionError("render attempted purchase")):
            for width, height in ((1280, 720), (1920, 1080)):
                for held in (False, True):
                    with self.subTest(size=(width, height), held=held):
                        reset(held)
                        expected = REFERENCE.build_gpu_economy_workspace(window, width, height)
                        expected_commands, expected_state = record(expected.commands), state()
                        reset(held)
                        actual = window.build_gpu_economy_workspace(width, height)
                        self.assertEqual(record(actual.commands), expected_commands)
                        self.assertEqual(state(), expected_state)
                        images = []
                        snapshots = []
                        for render in (REFERENCE.draw_economy_overlay_compat, type(window).draw_economy_overlay_compat):
                            reset(held)
                            image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
                            image.fill(QColor("#253750"))
                            painter = QPainter(image)
                            try:
                                render(window, painter, width, height, 1.)
                            finally:
                                painter.end()
                            images.append(bytes(image.constBits()))
                            snapshots.append(state())
                        self.assertEqual(images[0], images[1])
                        self.assertEqual(snapshots[0], snapshots[1])
            self.assertEqual(window.state.remaining_economy, 500)
            self.assertIsNone(window.economy_bridge.latest)

    def test_repeated_queries_do_not_commit_missing_or_existing_selection(self):
        window = self.window
        for key, index in (("stale", 12), ("large", 7)):
            window.economy_selected_key = key
            window.economy_selected = index
            expected = self.offers[1] if key == "large" else self.offers[0]
            for _ in range(3):
                self.assertIs(window.selected_economy_offer(), expected)
                self.assertEqual((window.economy_selected_key, window.economy_selected), (key, index))
        window.economy_selected_key = "stale"
        self.assertIs(window.reconcile_economy_selection(), self.offers[0])
        self.assertEqual((window.economy_selected_key, window.economy_selected), ("small", 0))

    def test_legacy_selected_offer_keeps_fallback_and_empty_category_semantics(self):
        window = self.window
        window.economy_selected_key = "stale"
        window.economy_selected = 12
        self.assertIs(window.economy_selected_offer(), self.offers[0])
        self.assertEqual((window.economy_selected_key, window.economy_selected), ("small", 0))
        window.economy_selected = 12
        self.assertIs(window.economy_selected_offer(), self.offers[0])
        self.assertEqual(window.economy_selected, 12)
        window.economy_category = "missing"
        self.assertIsNone(window.economy_selected_offer())
        self.assertEqual((window.economy_selected_key, window.economy_selected), ("small", 12))


if __name__ == "__main__":
    unittest.main()
