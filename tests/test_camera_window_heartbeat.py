"""Real window close stays responsive while its real camera worker owns release.

Only cv2's native boundary is synthetic. This adds held-release/event-loop
coverage to the caller's inert-worker tests, without camera or GPU access.
"""
from __future__ import annotations

from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PySide6.QtCore import QEventLoop, QTimer
from tests.test_camera_stop_review import CaptureBoundary
from tests.test_window_vision_runtime import WindowVisionRuntimeFixture


class CameraWindowHeartbeatTests(WindowVisionRuntimeFixture):
    def test_qt_progress_during_80_ms_native_release_hold(self):
        self.check_held_release(80)

    def test_qt_progress_during_240_ms_native_release_hold(self):
        self.check_held_release(240)

    def check_held_release(self, hold_ms):
        window, camera = self.window, self.window.camera_worker
        capture = CaptureBoundary()
        capture.release_permission.clear()
        timers, heartbeats, errors = [], [], []
        loop = QEventLoop()
        main_ident = threading.get_ident()
        with patch.dict(sys.modules, {"cv2": capture.cv2()}):
            self.assertTrue(camera.start())
            worker = camera._thread
            original_join = worker.join
            def guarded_join(*args, **kwargs):
                if threading.get_ident() == main_ident:
                    raise AssertionError("window event handling joined camera")
                return original_join(*args, **kwargs)
            def tick():
                try:
                    if not capture.release_permission.is_set():
                        self.assertTrue(worker.is_alive())
                        self.assertTrue(camera.cleanup_pending)
                        self.assertFalse(window._window_close_finalized)
                        heartbeats.append(True)
                    window.on_tick()
                    window.ensure_camera_started()  # The real closing guard.
                    if window._window_close_finalized:
                        loop.quit()
                except BaseException as error:
                    errors.append(repr(error))
                    loop.quit()
            try:
                self.assertTrue(capture.read_entered.wait(1))
                window.camera_started = True
                window._camera_config = (camera.camera_id, *camera.size)
                with patch.object(worker, "join", guarded_join):
                    # The real disabled-capture caller must choose no-wait
                    # admission even before the worker reaches native release.
                    window.ensure_camera_started()
                    self.assertFalse(camera.running)
                    self.assertFalse(window.camera_started)
                    self.assertIs(camera._thread, worker)
                    capture.read_release.set()
                    self.assertTrue(capture.release_entered.wait(1))
                    self.assertFalse(window.close())
                    self.assertTrue(window._window_lifecycle.closing)
                    self.assertFalse(window._window_close_finalized)
                    for interval, callback, single in (
                        (10, tick, False),
                        (hold_ms, capture.release_permission.set, True),
                        (3000, loop.quit, True),
                    ):
                        timer = QTimer()
                        timer.setSingleShot(single)
                        timer.timeout.connect(callback)
                        timer.start(interval)
                        timers.append(timer)
                    loop.exec()
                self.assertEqual(errors, [])
                self.assertTrue(heartbeats, "Qt made no progress during native release")
                self.assertTrue(capture.release_permission.is_set())
                self.assertTrue(window._window_close_finalized)
                self.assertTrue(window.backend.shutdown_completion.may_exit)
                self.assertFalse(worker.is_alive())
                self.assertIsNone(camera._thread)
                self.assertFalse(camera.cleanup_pending)
                self.assertFalse(capture.is_open)
                self.assertEqual(capture.release_threads, [worker.ident])
            finally:
                for timer in timers:
                    timer.stop()
                capture.read_release.set()
                capture.release_permission.set()


if __name__ == "__main__":
    unittest.main()
