"""Production window refresh admits worker stops without joining native owners.

Worker bodies may be held on explicit Events; the retained-socket case executes
its real receive/finally path with an inert native socket. No inference model,
network peer, camera or native GL context is used.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ['CI'] = 'true'
os.environ['POLAR_DISABLE_LOCAL_CAPTURE'] = '1'
os.environ['POLAR_DISABLE_MEDIA_PRELOAD'] = '1'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from rm_client.ui.polar_app import PolarRMWindow
from rm_client.backend.video_source import VideoSourceAdmissionError
from tests.test_video_close_ownership import Process, installed_decoder
from tests.test_vision_stop_admission import FakeSocket, until
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])
_NETWORK_ACTIVE = False
_NETWORK_ATTEMPTS = []


def reject_network(event, arguments):
    if _NETWORK_ACTIVE and event in {'socket.connect', 'socket.bind', 'socket.sendto', 'socket.getaddrinfo'}:
        _NETWORK_ATTEMPTS.append(event)
        raise AssertionError(f'Vision runtime tests must remain offline: {event}')


sys.addaudithook(reject_network)


class WindowVisionRuntimeFixture(unittest.TestCase):
    def setUp(self):
        global _NETWORK_ACTIVE
        _NETWORK_ACTIVE = True
        _NETWORK_ATTEMPTS.clear()
        self.addCleanup(self.finish_network_audit)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': self.directory.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        enumeration = patch('rm_client.serial_ports._scan_serial_ports', return_value=[])
        enumeration.start()
        self.addCleanup(enumeration.stop)
        self.window = PolarRMWindow()
        self.addCleanup(self.close_window)
        settle_window_lifecycle(self.window, APP)
        self.window.timer.stop()
        self.window.ui_audio_enabled = False
        self.window.registry.get('AudioFeedback').enabled = False
        self.owners = (self.window.local_vision, self.window.tongji_receiver, self.window.tongji_bridge)

    def finish_network_audit(self):
        global _NETWORK_ACTIVE
        _NETWORK_ACTIVE = False
        self.assertEqual(_NETWORK_ATTEMPTS, [])

    def close_window(self):
        close_window_safely(self.window, APP)
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def hold_workers(self):
        gates, calls, threads = [], [], []
        for owner in self.owners:
            entered, release = threading.Event(), threading.Event()
            local_calls = []
            def run(*args, owner=owner, entered=entered, release=release, calls=local_calls):
                calls.append((getattr(owner, 'model_path', None), getattr(owner, 'device', None),
                              getattr(owner, 'port', None)))
                entered.set()
                if not release.wait(4):
                    raise AssertionError('Test did not release its inert worker')
            worker = patch.object(owner, '_run', side_effect=run)
            worker.start()
            self.addCleanup(worker.stop)
            self.addCleanup(release.set)
            owner.start()
            self.assertTrue(entered.wait(1))
            gates.append(release)
            calls.append(local_calls)
            threads.append(owner._thread)
        return gates, calls, threads

class WindowVisionRuntimeTests(WindowVisionRuntimeFixture):
    def test_disabled_capture_refresh_is_prompt_retains_workers_and_serves_qt_events(self):
        gates, _calls, threads = self.hold_workers()
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        started = time.perf_counter()
        self.window.refresh_vision_runtime()
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, .1, f'UI refresh blocked for {elapsed:.3f}s')
        self.assertEqual(heartbeat, [])
        for owner, thread in zip(self.owners, threads):
            self.assertFalse(owner.running)
            self.assertTrue(owner.cleanup_pending)
            self.assertIs(owner._thread, thread)
            self.assertTrue(thread.is_alive())
        APP.processEvents()
        self.assertEqual(heartbeat, [True])
        for gate in gates:
            gate.set()

    def test_source_failure_still_revokes_disabled_workers_and_retains_the_original_error(self):
        gates, _calls, threads = self.hold_workers()
        backend = self.window.backend
        self.assertTrue(backend.native_decoder.wait_closed())
        backend.native_decoder, _ = installed_decoder(Process())
        self.window.registry.get('CameraFeed').get_setting('Source').value = 'Synthetic'
        with patch('threading.Thread.start', side_effect=RuntimeError('native admission failed')):
            with self.assertRaises(VideoSourceAdmissionError):
                backend.set_active_video_source('Synthetic')
        expected = backend.video_source_snapshot.failures
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        started = time.perf_counter()
        with patch('threading.Thread.start') as thread_start:
            with self.assertRaises(VideoSourceAdmissionError) as caught:
                self.window.refresh_vision_runtime()
            thread_start.assert_not_called()
        self.assertLess(time.perf_counter() - started, .1)
        self.assertEqual(caught.exception.failures, expected)
        APP.processEvents()
        self.assertEqual(heartbeat, [True])
        self.assertEqual([owner.running for owner in self.owners], [False, False, False])
        self.assertTrue(all(thread.is_alive() for thread in threads))
        for gate in gates:
            gate.set()

    def test_source_error_survives_a_secondary_stop_defect_without_starting_any_owner(self):
        gates, _calls, _threads = self.hold_workers()
        backend = self.window.backend
        self.assertTrue(backend.native_decoder.wait_closed())
        backend.native_decoder, _ = installed_decoder(Process())
        self.window.registry.get('CameraFeed').get_setting('Source').value = 'Synthetic'
        with patch('threading.Thread.start', side_effect=RuntimeError('native admission failed')):
            with self.assertRaises(VideoSourceAdmissionError):
                backend.set_active_video_source('Synthetic')
        source_errors = []
        select_source = backend.set_active_video_source
        def record_source_error(source):
            try:
                select_source(source)
            except VideoSourceAdmissionError as exc:
                source_errors.append(exc)
                raise
        with (
            patch.object(backend, 'set_active_video_source', side_effect=record_source_error),
            patch.object(self.window.local_vision, 'stop', side_effect=TypeError('stop defect')),
            patch('threading.Thread.start') as thread_start,
            self.assertLogs('rm_client.ui.polar_app', level='ERROR') as diagnostics,
        ):
            with self.assertRaises(VideoSourceAdmissionError) as caught:
                self.window.refresh_vision_runtime()
        self.assertIs(caught.exception, source_errors[0])
        self.assertFalse(self.window.tongji_receiver.running)
        self.assertFalse(self.window.tongji_bridge.running)
        thread_start.assert_not_called()
        self.assertIn('TypeError: stop defect', '\n'.join(diagnostics.output))
        self.assertIn('vision.stop.local', self.window._tick_last_error)
        for gate in gates:
            gate.set()

    def test_one_stop_defect_does_not_skip_other_requested_revocations(self):
        gates, _calls, _threads = self.hold_workers()
        defect = TypeError('local revocation defect')
        with (
            patch.object(self.window.local_vision, 'stop', side_effect=defect),
            self.assertLogs('rm_client.ui.polar_app', level='ERROR'),
        ):
            with self.assertRaises(TypeError) as caught:
                self.window.refresh_vision_runtime()
        self.assertIs(caught.exception, defect)
        self.assertFalse(self.window.tongji_receiver.running)
        self.assertFalse(self.window.tongji_bridge.running)
        self.assertIn('vision.stop.local', self.window._tick_last_error)
        self.assertIn('TypeError', self.window._tick_last_error)
        for gate in gates:
            gate.set()

    def test_repeated_enable_disable_and_configure_cannot_replace_a_held_owner(self):
        gates, calls, threads = self.hold_workers()
        model = Path(self.directory.name) / 'model.xml'
        model.write_text('inert model path')
        bridge = self.window.registry.get('TongjiVisionBridge')
        bridge.enabled = True
        source = bridge.get_setting('Vision Source')
        device = bridge.get_setting('Local Device')
        port = bridge.get_setting('UDP Port')
        started = time.perf_counter()
        with (
            patch.dict(os.environ, {'POLAR_DISABLE_LOCAL_CAPTURE': '0'}),
            patch.object(self.window, '_client_asset_path', return_value=model),
            patch('threading.Thread.start') as thread_start,
        ):
            for index in range(6):
                source.value = 'Auto' if index % 2 else 'Mock'
                device.value = 'CPU' if index < 5 else 'GPU'
                port.value = 9900 + index
                self.window.refresh_vision_runtime()
        self.assertLess(time.perf_counter() - started, .1)
        thread_start.assert_not_called()
        for owner, thread, runs in zip(self.owners, threads, calls):
            self.assertIs(owner._thread, thread)
            self.assertEqual(len(runs), 1)
        self.assertEqual(self.window.local_vision.model_path, model)
        self.assertEqual(self.window.local_vision.device, 'GPU')
        self.assertEqual(self.window.tongji_receiver.port, 9905)
        for gate in gates:
            gate.set()
        for thread in threads:
            thread.join(1)
            self.assertFalse(thread.is_alive())
        with (
            patch.dict(os.environ, {'POLAR_DISABLE_LOCAL_CAPTURE': '0'}),
            patch.object(self.window, '_client_asset_path', return_value=model),
        ):
            self.window.refresh_vision_runtime()
        until(lambda: len(calls[0]) == 2 and len(calls[1]) == 2)
        self.assertEqual(calls[0][-1][:2], (model, 'GPU'))
        self.assertEqual(calls[1][-1][2], 9905)
        self.assertEqual(len(calls[2]), 1)

    def test_final_close_keeps_window_alive_until_all_previously_stopped_workers_exit(self):
        gates, _calls, threads = self.hold_workers()
        self.window.refresh_vision_runtime()
        started = time.perf_counter()
        self.assertFalse(self.window.close())
        self.assertLess(time.perf_counter() - started, .1)
        self.assertTrue(self.window._window_lifecycle.closing)
        self.assertTrue(self.window.backend.lifecycle_pending)
        self.assertFalse(self.window._window_close_finalized)
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        self.window.on_tick()
        APP.processEvents()
        self.assertEqual(heartbeat, [True])
        self.assertTrue(all(thread.is_alive() for thread in threads))
        with patch.object(self.window.backend, 'set_active_video_source') as source:
            self.window.refresh_vision_runtime()
            source.assert_not_called()
        for gate in gates:
            gate.set()
        settle_window_lifecycle(self.window, APP)
        self.assertTrue(self.window.backend.shutdown_completion.may_exit)
        self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_runtime_socket_close_failure_blocks_replacement_and_final_close_until_explicit_retry(self):
        receiver = self.window.tongji_receiver
        sock = FakeSocket(mode='raise')
        self.addCleanup(setattr, sock, 'mode', 'ok')
        self.addCleanup(sock.recv_release.set)
        with patch('rm_client.tongji_bridge.socket.socket', return_value=sock) as socket_open:
            receiver.start()
            self.assertTrue(sock.recv_entered.wait(1))
            worker = receiver._thread
            self.window.refresh_vision_runtime()
            self.assertIs(receiver._thread, worker)
            sock.recv_release.set()
            until(lambda: not worker.is_alive())
            self.assertIn('synthetic close failure', receiver.cleanup_error)
            self.assertFalse(receiver.cleanup_pending)
            self.assertIs(receiver._socket, sock)
            bridge = self.window.registry.get('TongjiVisionBridge')
            bridge.enabled = True
            bridge.get_setting('Vision Source').value = 'Tongji'
            with patch.dict(os.environ, {'POLAR_DISABLE_LOCAL_CAPTURE': '0'}):
                for _ in range(5):
                    self.window.refresh_vision_runtime()
            self.assertEqual(socket_open.call_count, 1)
            self.assertFalse(receiver.running)
            self.assertFalse(self.window.close())
            deadline = time.perf_counter() + 2
            while self.window.backend.shutdown_completion is None:
                self.window._poll_backend_lifecycle()
                self.assertLess(time.perf_counter(), deadline)
                time.sleep(.001)
            self.assertFalse(self.window.backend.shutdown_completion.may_exit)
            self.assertFalse(self.window._window_close_finalized)
            self.assertIs(receiver._socket, sock)
            sock.mode = 'ok'
            self.assertFalse(self.window.close())
            settle_window_lifecycle(self.window, APP)
            self.assertTrue(self.window.backend.shutdown_completion.may_exit)
            self.assertTrue(sock.closed)
            self.assertIsNone(receiver._socket)


class WindowCameraRuntimeTests(WindowVisionRuntimeFixture):
    def hold_camera(self):
        worker = self.window.camera_worker
        entered, release = threading.Event(), threading.Event()
        calls = []
        def held_run(*args):
            calls.append((worker.camera_id, worker.size))
            entered.set()
            if not release.wait(4):
                raise AssertionError('Test did not release its inert camera worker')
        body = patch.object(worker, '_run', side_effect=held_run)
        body.start()
        self.addCleanup(body.stop)
        self.addCleanup(release.set)
        self.assertTrue(worker.start())
        self.assertTrue(entered.wait(1))
        worker.connected = True
        self.window.camera_started = True
        self.window._camera_config = (worker.camera_id, *worker.size)
        return release, worker._thread, calls

    def assert_prompt_revocation(self, action):
        release, thread, _calls = self.hold_camera()
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        started = time.perf_counter()
        action()
        self.assertLess(time.perf_counter() - started, .1)
        worker = self.window.camera_worker
        self.assertFalse(worker.running)
        self.assertFalse(worker.connected)
        self.assertFalse(self.window.camera_started)
        self.assertTrue(worker.cleanup_pending)
        self.assertIs(worker._thread, thread)
        self.assertTrue(thread.is_alive())
        APP.processEvents()
        self.assertEqual(heartbeat, [True])
        release.set()

    def test_disabled_capture_revokes_camera_without_waiting(self):
        self.assert_prompt_revocation(self.window.ensure_camera_started)

    def test_non_webcam_source_revokes_camera_without_waiting(self):
        self.window.registry.get('CameraFeed').get_setting('Source').value = 'Synthetic'
        with patch.dict(os.environ, {'POLAR_DISABLE_LOCAL_CAPTURE': '0'}):
            self.assert_prompt_revocation(self.window.ensure_camera_started)

    def test_deploy_acknowledgement_revokes_camera_without_waiting(self):
        self.window._last_deploy_mode = 0
        self.window.state.deploy_mode = 1
        self.window.state.deploy_status_revision += 1
        self.assert_prompt_revocation(self.window.update_hero_deploy_state)

    def test_reconfigure_retains_old_owner_then_starts_only_latest_camera_settings(self):
        release, thread, calls = self.hold_camera()
        camera = self.window.registry.get('CameraFeed')
        camera.get_setting('Source').value = 'Webcam'
        with patch.dict(os.environ, {'POLAR_DISABLE_LOCAL_CAPTURE': '0'}):
            started = time.perf_counter()
            with patch('threading.Thread.start') as thread_start:
                for index in range(5):
                    camera.get_setting('Camera ID').value = index + 1
                    camera.get_setting('Capture Width').value = 640 + index * 16
                    camera.get_setting('Capture Height').value = 360 + index * 16
                    self.window.ensure_camera_started()
                thread_start.assert_not_called()
            self.assertLess(time.perf_counter() - started, .1)
            worker = self.window.camera_worker
            self.assertIs(worker._thread, thread)
            self.assertFalse(worker.running)
            self.assertFalse(self.window.camera_started)
            self.assertEqual(len(calls), 1)
            self.assertEqual((worker.camera_id, worker.size), (5, (704, 424)))
            release.set()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.window.ensure_camera_started()
            until(lambda: len(calls) == 2)
            self.assertEqual(calls[-1], (5, (704, 424)))
            self.assertTrue(self.window.camera_started)
            self.assertEqual(self.window._camera_config, (5, 704, 424))

    def test_closing_window_rejects_late_camera_refresh_and_waits_for_old_owner(self):
        release, thread, _calls = self.hold_camera()
        camera = self.window.camera_worker
        self.assertFalse(self.window.close())
        self.assertTrue(self.window._window_lifecycle.closing)
        with (
            patch.dict(os.environ, {'POLAR_DISABLE_LOCAL_CAPTURE': '0'}),
            patch.object(camera, 'configure', wraps=camera.configure) as configure,
            patch.object(camera, 'start', wraps=camera.start) as start,
        ):
            self.window.ensure_camera_started()
            configure.assert_not_called()
            start.assert_not_called()
        self.window.on_tick()
        APP.processEvents()
        self.assertFalse(self.window._window_close_finalized)
        self.assertTrue(thread.is_alive())
        release.set()
        settle_window_lifecycle(self.window, APP)
        self.assertTrue(self.window.backend.shutdown_completion.may_exit)
        self.assertFalse(thread.is_alive())

    @staticmethod
    def reject_camera_start(original_start):
        def start(thread):
            if thread.name == 'polar-camera':
                raise OSError('camera admission rejected')
            return original_start(thread)
        return start

    def test_camera_admission_failure_is_typed_and_does_not_skip_safety_or_repaint(self):
        window = self.window
        window.home_visible = False
        window.registry.get('CameraFeed').get_setting('Source').value = 'Webcam'
        stage_names = ('backend.tick', 'backend.apply', 'polar.link')
        before = {name: window._ui_tick_stages.get(name, {}).get('count', 0)
                  for name in stage_names}
        now = time.perf_counter()
        window._last_logic_time = now - .1
        window._ui_tick_perf_log_at = now
        window.frame_scheduler.force()
        with (
            patch.dict(os.environ, {'POLAR_DISABLE_LOCAL_CAPTURE': '0'}),
            patch('threading.Thread.start', self.reject_camera_start(threading.Thread.start)),
            patch.object(window, 'update', wraps=window.update) as repaint,
            self.assertLogs('rm_client.ui.polar_app', level='ERROR') as diagnostics,
        ):
            window.on_tick()
            repaint.assert_called()
        for name in stage_names:
            self.assertGreater(window._ui_tick_stages[name]['count'], before[name], name)
        self.assertIn('camera.runtime: OSError: camera admission rejected', window._tick_last_error)
        self.assertTrue(diagnostics.records[0].exc_info)
        self.assertFalse(window.camera_worker.running)
        self.assertGreater(window.camera_worker.retry_after, now)

    def test_first_paint_camera_admission_failure_keeps_cpu_fallback_drawable(self):
        window = self.window
        window.home_visible = False
        window.registry.get('CameraFeed').get_setting('Source').value = 'Webcam'
        image = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            with (
                patch.dict(os.environ, {'POLAR_DISABLE_LOCAL_CAPTURE': '0'}),
                patch('threading.Thread.start', self.reject_camera_start(threading.Thread.start)),
                self.assertLogs('rm_client.ui.polar_app', level='ERROR'),
            ):
                window.draw_video(painter, 640, 360)
        finally:
            painter.end()
        self.assertIn('camera.runtime: OSError: camera admission rejected', window._tick_last_error)
        self.assertTrue(any(image.pixelColor(x, y).alpha()
                            for x in range(0, 640, 40) for y in range(0, 360, 40)))
        self.assertFalse(image.paintingActive())


if __name__ == '__main__':
    unittest.main()
