"""Real owner-thread recovery UI; native process/socket terminals stay inert."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ['CI'] = 'true'
os.environ['POLAR_DISABLE_LOCAL_CAPTURE'] = '1'
os.environ['POLAR_DISABLE_MEDIA_PRELOAD'] = '1'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QMouseEvent, QPainter
from rm_client.backend.video_source import VideoSourceAdmissionError
from rm_client.ui import polar_app
from rm_client.ui.polar_app import PolarRMWindow
from rm_client.ui.polar.video_recovery import VIDEO_RETRY_ACTION, VIDEO_WAIT_ACTION
from tests.test_video_close_ownership import Process, installed_decoder
from tests.test_video_source_retry_admission import PipeHeldProcess
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])
_NETWORK_ACTIVE = False
_NETWORK_ATTEMPTS = []


def reject_network(event, arguments):
    if _NETWORK_ACTIVE and event in {'socket.connect', 'socket.bind', 'socket.sendto', 'socket.getaddrinfo'}:
        _NETWORK_ATTEMPTS.append(event)
        raise AssertionError(f'Video recovery tests must remain offline: {event}')


sys.addaudithook(reject_network)


class WindowVideoRecoveryTests(unittest.TestCase):
    def setUp(self):
        global _NETWORK_ACTIVE
        _NETWORK_ACTIVE = True
        _NETWORK_ATTEMPTS.clear()
        self.addCleanup(self.finish_network_audit)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        # Device enumeration is unrelated to video cleanup; keep its real
        # cache/worker scheduler but replace the physical discovery terminal.
        enumeration = patch('rm_client.serial_ports._scan_serial_ports', return_value=[])
        enumeration.start()
        self.addCleanup(enumeration.stop)
        self.window = PolarRMWindow()
        self.addCleanup(self.close_window)
        settle_window_lifecycle(self.window, APP)
        self.window.timer.stop()
        self.window.ui_audio_enabled = False
        self.window.registry.get('AudioFeedback').enabled = False
        self.window.home_visible = False
        self.window.resize(640, 360)

    def finish_network_audit(self):
        global _NETWORK_ACTIVE
        _NETWORK_ACTIVE = False
        self.assertEqual(_NETWORK_ATTEMPTS, [])

    def close_window(self):
        close_window_safely(self.window, APP)
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def fail_source_admission(self):
        window = self.window
        self.assertTrue(window.backend.native_decoder.wait_closed())
        window.backend.native_decoder, session = installed_decoder(Process())
        window.registry.get('CameraFeed').get_setting('Source').value = 'Synthetic'
        with patch('threading.Thread.start', side_effect=RuntimeError('test closer not admitted')):
            with self.assertRaises(VideoSourceAdmissionError):
                window.backend.set_active_video_source('Synthetic')
        self.assertIn(session, window.backend.native_decoder._retired_sessions)
        self.assertTrue(window.backend.video_source_snapshot.failures)
        return session

    def wait_for_cleanup_attempt(self):
        deadline = time.perf_counter() + 2
        while self.window.backend.video_source_snapshot.inflight_resources:
            if time.perf_counter() >= deadline:
                self.fail("Inert cleanup worker did not finish")
            time.sleep(.001)
        return self.window.backend.video_source_snapshot

    def failed_native_cleanup(self):
        window = self.window
        self.assertTrue(window.backend.native_decoder.wait_closed())
        window.backend.set_active_video_source('Native 0x0310', start=False)
        self.assertTrue(window.backend.udp.wait_closed())
        proc = Process()
        proc.stdout.fail_close = True
        self.addCleanup(setattr, proc.stdout, 'fail_close', False)
        window.backend.native_decoder, session = installed_decoder(proc)
        window.registry.get('CameraFeed').get_setting('Source').value = 'Synthetic'
        window.backend.set_active_video_source('Synthetic')
        status = self.wait_for_cleanup_attempt()
        self.assertIn(session, window.backend.native_decoder._retired_sessions)
        self.assertTrue(status.close_failures)
        self.assertTrue(status.retryable)
        return proc, session

    def paint_status(self):
        window = self.window
        image = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        class ImagePainter(QPainter):
            def __init__(self, device):
                super().__init__(image if device is window else device)
        with (
            patch.object(polar_app, 'QPainter', ImagePainter),
            patch.object(window, 'draw_text', wraps=window.draw_text) as text,
        ):
            window.paintGL()
        self.assertFalse(image.paintingActive())
        self.assertTrue(any(image.pixelColor(x, y).alpha() for x in range(0, 640, 40)
                            for y in range(0, 360, 40)))
        return ([target for target in window.hotspots if target.action == VIDEO_RETRY_ACTION],
                [call.args[2] for call in text.call_args_list])

    def click(self, target, release_point=None):
        point = target.rect.center()
        press = QMouseEvent(QEvent.Type.MouseButtonPress, point, point, Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        released_at = point if release_point is None else release_point
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, released_at, released_at, Qt.MouseButton.LeftButton,
                              Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.window.mousePressEvent(press)
        self.window.mouseReleaseEvent(release)

    def assert_owned_drag_reanchors_motion(self, target):
        window = self.window
        self.assertTrue(window.combat_control_input_allowed())
        window._control_mouse_point = QPointF(10, 10)
        def event(kind, point, button, buttons):
            return QMouseEvent(kind, point, point, button, buttons, Qt.KeyboardModifier.NoModifier)
        with (
            patch.object(window.control, 'add_mouse_motion', wraps=window.control.add_mouse_motion) as motion,
            patch.object(window.control, 'set_mouse_button', wraps=window.control.set_mouse_button) as buttons,
        ):
            window.mousePressEvent(event(QEvent.Type.MouseButtonPress, target.rect.center(),
                                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
            window.mouseMoveEvent(event(QEvent.Type.MouseMove, QPointF(200, 200),
                                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
            window.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, QPointF(200, 200),
                                           Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
            window.mouseMoveEvent(event(QEvent.Type.MouseMove, QPointF(201, 201),
                                        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton))
            motion.assert_not_called()
            buttons.assert_not_called()
            # The next free movement resumes ordinary control from the fresh
            # anchor, without replaying any movement owned by the UI.
            window.mouseMoveEvent(event(QEvent.Type.MouseMove, QPointF(203, 204),
                                        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton))
            motion.assert_called_once()
            self.assertEqual(motion.call_args.args[:2], (2.0, 3.0))

    def test_native_close_failure_is_visible_and_one_real_click_retries_without_control_output(self):
        proc, session = self.failed_native_cleanup()
        window = self.window
        targets, texts = self.paint_status()
        self.assertEqual(len(targets), 1)
        self.assertTrue(any('pipe close failed' in text for text in texts))
        self.assertIn('pipe close failed', targets[0].tooltip)
        token = targets[0].payload
        proc.stdout.fail_close = False
        with (
            patch.object(window.backend, 'retry_active_video_source', wraps=window.backend.retry_active_video_source) as retry,
            patch.object(window.control, 'set_mouse_button', wraps=window.control.set_mouse_button) as control,
            patch.object(window, 'combat_control_input_allowed', return_value=True),
        ):
            self.assert_owned_drag_reanchors_motion(targets[0])
        retry.assert_called_once_with(expected_generation=token.source_generation)
        control.assert_not_called()
        status = self.wait_for_cleanup_attempt()
        self.assertNotIn(session, window.backend.native_decoder._retired_sessions)
        self.assertFalse(status.retryable)
        self.assertFalse(status.close_failures)
        targets, _ = self.paint_status()
        self.assertEqual(targets, [])

    def test_admission_failure_has_a_visible_action_and_periodic_ticks_do_not_retry_it(self):
        self.fail_source_admission()
        window = self.window
        targets, texts = self.paint_status()
        self.assertEqual(len(targets), 1)
        self.assertTrue(any('test closer not admitted' in text for text in texts))
        with patch.object(window.backend, 'retry_active_video_source', wraps=window.backend.retry_active_video_source) as retry:
            for _ in range(3):
                window._last_logic_time = time.perf_counter() - .1
                window.on_tick()
            retry.assert_not_called()
            self.click(targets[0])
            retry.assert_called_once()
        self.wait_for_cleanup_attempt()
        self.assertEqual(self.paint_status()[0], [])

    def test_running_native_close_shows_waiting_and_keeps_qt_heartbeat_alive(self):
        window = self.window
        self.assertTrue(window.backend.native_decoder.wait_closed())
        window.backend.set_active_video_source('Native 0x0310', start=False)
        self.assertTrue(window.backend.udp.wait_closed())
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        proc = Process()
        original_wait = proc.wait
        def held_wait(timeout=None):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('inert native process was not released')
            return original_wait(timeout)
        proc.wait = held_wait
        window.backend.native_decoder, _ = installed_decoder(proc)
        window.registry.get('CameraFeed').get_setting('Source').value = 'Synthetic'
        window.backend.set_active_video_source('Synthetic')
        self.assertTrue(entered.wait(1))
        self.assertTrue(window.backend.video_source_snapshot.inflight_resources)
        self.assertFalse(window.backend.video_source_snapshot.retryable)
        targets, texts = self.paint_status()
        self.assertEqual(targets, [])
        self.assertTrue(any('等待' in text or 'Waiting' in text for text in texts))
        # A disabled button still owns a complete click. Use its painted
        # geometry rather than a retry target, since busy has no retry intent.
        self.assertTrue(window.combat_control_input_allowed())
        panel_width = min(560.0, window.width() - 32.0)
        left = (window.width() - panel_width) * .5
        top = max(8.0, min(window.height() - 112.0, window.height() * .5 + 24.0))
        busy_button = SimpleNamespace(rect=QRectF(left + 12, top + 65, panel_width - 24, 28))
        with (
            patch.object(window.backend, 'retry_active_video_source') as retry,
            patch.object(window.control, 'set_mouse_button', wraps=window.control.set_mouse_button) as control,
        ):
            self.click(busy_button)
            self.click(busy_button, release_point=QPointF(5, 5))
            self.assert_owned_drag_reanchors_motion(busy_button)
            retry.assert_not_called()
            control.assert_not_called()
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        window._last_logic_time = time.perf_counter() - .1
        window.on_tick()
        APP.processEvents()
        self.assertEqual(heartbeat, [True])
        release.set()
        self.wait_for_cleanup_attempt()
        self.paint_status()
        self.assertFalse(any(target.action in (VIDEO_RETRY_ACTION, VIDEO_WAIT_ACTION)
                             for target in window.hotspots))

    def test_live_data_readers_expose_retry_then_busy_button_owns_the_next_click(self):
        backend = self.window.backend
        self.assertTrue(backend.native_decoder.wait_closed())
        backend.set_active_video_source('Native 0x0310', start=False)
        self.assertTrue(backend.udp.wait_closed())
        decoder = backend.native_decoder
        process = PipeHeldProcess()
        with (
            patch('rm_client.backend.video_backend.subprocess.Popen', return_value=process),
            patch('rm_client.backend.video_backend.resolve_ffmpeg_executable', return_value='ffmpeg'),
            patch('rm_client.backend.video_backend.os.read', side_effect=process.read),
        ):
            try:
                self.assertTrue(decoder.start())
                self.assertTrue(process.reader_entered.wait(1))
                self.assertTrue(process.stderr_entered.wait(1))
                self.window.registry.get('CameraFeed').get_setting('Source').value = 'Synthetic'
                with patch('threading.Thread.start', side_effect=RuntimeError('closer rejected')):
                    with self.assertRaises(VideoSourceAdmissionError):
                        backend.set_active_video_source('Synthetic')
                targets, _texts = self.paint_status()
                self.assertEqual(len(targets), 1)
                self.assertTrue(backend.video_source_snapshot.retryable)
                self.assertTrue(self.window.combat_control_input_allowed())
                with (
                    patch.object(backend, 'retry_active_video_source', wraps=backend.retry_active_video_source) as retry,
                    patch.object(self.window.control, 'set_mouse_button', wraps=self.window.control.set_mouse_button) as control,
                ):
                    self.click(targets[0])
                    self.assertTrue(process.terminate_entered.wait(1))
                    closer = decoder._stop_worker
                    retry.assert_called_once()
                    waiting_targets, texts = self.paint_status()
                    self.assertEqual(waiting_targets, [])
                    self.assertTrue(any('Waiting' in text or '等待' in text for text in texts))
                    self.click(targets[0])
                    retry.assert_called_once()
                    self.assertIs(decoder._stop_worker, closer)
                    control.assert_not_called()
                heartbeat = []
                QTimer.singleShot(0, lambda: heartbeat.append(True))
                APP.processEvents()
                self.assertEqual(heartbeat, [True])
                process.release()
                self.assertTrue(decoder.wait_closed())
                self.assertIsNone(self.window.video_source_recovery_notice())
                self.assertEqual(self.paint_status()[0], [])
            finally:
                # Only termination closes the pipe writers. Never release the
                # data reads separately: that would hide a missing closer.
                process.release()
                decoder.stop()
                self.assertTrue(decoder.wait_closed())

    def test_old_click_is_rejected_after_source_a_b_a_round_trip(self):
        self.failed_native_cleanup()
        window = self.window
        first = self.paint_status()[0][0]
        window.backend.set_active_video_source('Native 0x0310', start=False)
        window.backend.set_active_video_source('Synthetic')
        self.wait_for_cleanup_attempt()
        self.assertNotEqual(first.payload, window.video_source_recovery_notice().retry_token)
        # Deliberately do not repaint: the published hit target is now stale.
        # An eligible combat path proves refusal still consumes the release.
        with (
            patch.object(window.backend, 'retry_active_video_source') as retry,
            patch.object(window, 'combat_control_input_allowed', return_value=True),
            patch.object(window.control, 'set_mouse_button', wraps=window.control.set_mouse_button) as control,
        ):
            self.click(first)
            self.assert_owned_drag_reanchors_motion(first)
            retry.assert_not_called()
            control.assert_not_called()

    def test_right_click_on_retry_does_not_take_left_action_or_change_combat_anchor(self):
        self.failed_native_cleanup()
        target = self.paint_status()[0][0]
        window = self.window
        self.assertTrue(window.combat_control_input_allowed())
        anchor = QPointF(10, 10)
        window._control_mouse_point = anchor
        point = target.rect.center()
        with patch.object(window.backend, 'retry_active_video_source') as retry:
            window.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, point, point,
                Qt.MouseButton.RightButton, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier))
            window.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, point, point,
                Qt.MouseButton.RightButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
            retry.assert_not_called()
        self.assertIs(window._control_mouse_point, anchor)
        self.assertFalse(window.pointer_interaction.background_release_owned)

    def test_failed_retry_is_typed_visible_and_not_reported_as_ready(self):
        self.fail_source_admission()
        window = self.window
        target = self.paint_status()[0][0]
        with (
            patch('threading.Thread.start', side_effect=OSError('retry admission failed')),
            patch.object(window, 'toast') as toast,
            self.assertLogs('rm_client.ui.polar.mixins.video_backend_mixin', level='ERROR') as logs,
        ):
            self.assertFalse(window.retry_video_source_cleanup(target.payload))
        self.assertTrue(logs.records[0].exc_info)
        self.assertIn('VideoSourceAdmissionError', toast.call_args.args[0])
        self.assertIn('retry admission failed', toast.call_args.args[0])
        self.assertEqual(toast.call_args.kwargs['kind'], 'warning')
        self.assertEqual(window.backend.video_source_snapshot.active_source, '')
        self.assertEqual(len(self.paint_status()[0]), 1)

    def test_whole_runtime_transition_and_final_close_reject_stale_recovery_intent(self):
        proc, _ = self.failed_native_cleanup()
        window = self.window
        token = self.paint_status()[0][0].payload
        proc.stdout.fail_close = False
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original_stop = window.backend.udp.stop
        def held_stop():
            entered.set()
            if not release.wait(2):
                raise TimeoutError('inert scenario teardown was not released')
            original_stop()
        with patch.object(window.backend.udp, 'stop', side_effect=held_stop):
            window.backend.request_scenario('LabDebug', start=False, registry=window.registry)
            self.assertTrue(entered.wait(1))
            with patch.object(window.backend, 'retry_active_video_source') as retry:
                self.assertIsNone(window.video_source_recovery_notice())
                self.assertFalse(window.retry_video_source_cleanup(token))
                retry.assert_not_called()
            release.set()
            settle_window_lifecycle(window, APP)
        with patch.object(window.backend, 'retry_active_video_source') as retry:
            self.assertFalse(window.close())
            self.assertIsNone(window.video_source_recovery_notice())
            self.assertFalse(window.retry_video_source_cleanup(token))
            retry.assert_not_called()
        settle_window_lifecycle(window, APP)

    def test_retained_source_error_does_not_skip_later_runtime_safety_or_repaint(self):
        self.fail_source_admission()
        window = self.window
        before = time.perf_counter()
        window._last_logic_time = window._last_control_tick_time = before - .1
        window._ui_tick_perf_log_at = before
        status = window.backend.video_source_snapshot
        with (
            patch.object(window.backend, 'tick', wraps=window.backend.tick) as backend_tick,
            patch.object(window.backend, 'apply_updates', wraps=window.backend.apply_updates) as apply,
            patch.object(window, 'update', wraps=window.update) as repaint,
            patch('threading.Thread.start', autospec=True, side_effect=threading.Thread.start) as start,
            self.assertLogs('rm_client.ui.polar_app', level='ERROR') as logs,
        ):
            for _ in range(3):
                window._last_logic_time = window._last_control_tick_time = time.perf_counter() - .1
                window.frame_scheduler.force()
                window.on_tick()
        self.assertEqual(backend_tick.call_count, 3)
        self.assertEqual(apply.call_count, 3)
        self.assertEqual(repaint.call_count, 3)
        self.assertEqual(len(logs.records), 1, "repeated failures retain the one-second log throttle")
        self.assertEqual(window._tick_error_count, 3)
        # The shared discovery TTL may expire during a real runtime tick.
        # It owns no video session and must not be mistaken for a close retry.
        started_workers = [call.args[0].name for call in start.call_args_list]
        self.assertEqual([name for name in started_workers if name != 'serial-discovery'], [])
        self.assertIn('vision.runtime', window._tick_last_error)
        self.assertIn('VideoSourceAdmissionError', window._tick_last_error)
        self.assertEqual(window.backend.video_source_snapshot, status)
        self.assertGreater(window._ui_tick_stages['polar.link']['count'], 0)


if __name__ == '__main__':
    unittest.main()
