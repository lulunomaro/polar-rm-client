"""Camera lifecycle contracts with Event-held, synthetic native boundaries.

No camera, codec, network, or GPU is opened. Timing checks apply only to these
controlled waits and do not claim a bound for native drivers or presentation.
"""
from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.vision import CameraCaptureWorker


REAL_START = threading.Thread.start


def until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("controlled camera lifecycle did not settle")
        time.sleep(.001)


def held(entered, release):
    def run(*_args):
        entered.set()
        if not release.wait(5):
            raise AssertionError("test did not release controlled worker")
    return run


class CountedLock:
    def __init__(self):
        self.lock = threading.Lock()
        self.count_lock = threading.Lock()
        self.count = 0
        self.second = threading.Event()

    def __enter__(self):
        with self.count_lock:
            self.count += 1
            if self.count == 2:
                self.second.set()
        self.lock.acquire()
        return self

    def __exit__(self, *_args):
        self.lock.release()


class Capture:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.failure = OSError("synthetic camera release")
        self.opened = True
        self.read_entered = threading.Event()
        self.allow_read = threading.Event()
        self.release_entered = threading.Event()
        self.allow_release = threading.Event()
        self.allow_release.set()
        self.release_threads = []
        self.set_calls = []
        self.read_calls = 0
        self.frame = NS(shape=(120, 160, 3))

    def isOpened(self):
        return self.opened

    def set(self, *args):
        self.set_calls.append(args)

    def read(self):
        self.read_calls += 1
        self.read_entered.set()
        if not self.allow_read.wait(5):
            raise AssertionError("test did not release controlled read")
        return True, self.frame

    def release(self):
        self.release_threads.append(threading.get_ident())
        self.release_entered.set()
        if not self.allow_release.wait(5):
            raise AssertionError("test did not release controlled native release")
        if self.mode == "raise":
            raise self.failure
        if self.mode == "false":
            return False
        if self.mode == "open":
            return None
        self.opened = False


def fake_cv2(capture, **overrides):
    values = dict(VideoCapture=Mock(return_value=capture), CAP_PROP_FRAME_WIDTH=1,
                  CAP_PROP_FRAME_HEIGHT=2, COLOR_BGR2RGB=3, INTER_LINEAR=4,
                  cvtColor=lambda frame, *_: frame, resize=lambda frame, *_args, **_kw: frame)
    values.update(overrides)
    return NS(**values)


class CameraStopAdmissionTests(unittest.TestCase):
    def make_camera(self):
        return CameraCaptureWorker(size=(160, 120))

    def test_no_wait_clears_visible_frame_without_join_and_preserves_owner(self):
        camera = self.make_camera()
        entered, release = threading.Event(), threading.Event()
        with patch.object(camera, "_run", side_effect=held(entered, release)) as run:
            self.assertTrue(camera.start())
            self.assertTrue(entered.wait(1))
            worker = camera._thread
            camera._latest, camera.connected = object(), True
            try:
                started = time.perf_counter()
                with patch.object(worker, "join", side_effect=AssertionError("UI joined camera")):
                    camera.stop(wait=False)
                    camera.stop(wait=False)
                    self.assertFalse(camera.start())
                self.assertLess(time.perf_counter() - started, .15)
                self.assertFalse(camera.running)
                self.assertFalse(camera.connected)
                self.assertIsNone(camera.latest_frame())
                self.assertIs(camera._thread, worker)
                self.assertTrue(camera.cleanup_pending)
                self.assertEqual(run.call_count, 1)
            finally:
                release.set()
                camera.close()
            self.assertFalse(worker.is_alive())
            self.assertFalse(camera.cleanup_pending)
            self.assertIsNone(camera._thread)

    def test_late_native_open_cannot_connect_configure_read_or_publish(self):
        camera, capture = self.make_camera(), Capture()
        opened, allow_open = threading.Event(), threading.Event()
        def open_capture(*_args):
            held(opened, allow_open)()
            return capture
        cv2 = fake_cv2(capture, VideoCapture=Mock(side_effect=open_capture))
        camera.on_frame = Mock()
        with patch.dict(sys.modules, {"cv2": cv2}):
            camera.start()
            self.assertTrue(opened.wait(1))
            worker = camera._thread
            try:
                camera.stop(wait=False)
                self.assertFalse(camera.start())
            finally:
                allow_open.set()
                camera.close()
        self.assertFalse(camera.connected)
        self.assertFalse(camera.running)
        self.assertIsNone(camera.latest_frame())
        self.assertEqual(capture.set_calls, [])
        self.assertEqual(capture.read_calls, 0)
        camera.on_frame.assert_not_called()
        self.assertEqual(capture.release_threads, [worker.ident])

    def test_late_read_or_conversion_cannot_republish_a_revoked_frame(self):
        for stage in ("read", "conversion"):
            with self.subTest(stage=stage):
                camera, capture = self.make_camera(), Capture()
                converting, allow_conversion = threading.Event(), threading.Event()
                def convert(frame, *_args):
                    held(converting, allow_conversion)()
                    return frame
                cv2 = fake_cv2(capture, cvtColor=convert)
                camera.on_frame = Mock()
                with patch.dict(sys.modules, {"cv2": cv2}):
                    camera.start()
                    self.assertTrue(capture.read_entered.wait(1))
                    if stage == "conversion":
                        capture.allow_read.set()
                        self.assertTrue(converting.wait(1))
                    worker = camera._thread
                    try:
                        camera.stop(wait=False)
                        self.assertFalse(camera.start())
                    finally:
                        capture.allow_read.set()
                        allow_conversion.set()
                        camera.close()
                self.assertIsNone(camera.latest_frame())
                self.assertFalse(camera.connected)
                camera.on_frame.assert_not_called()
                self.assertEqual(camera._sequence, 0)
                self.assertEqual(capture.release_threads, [worker.ident])

    def test_native_release_is_worker_owned_and_strict_close_waits(self):
        camera, capture = self.make_camera(), Capture()
        capture.allow_release.clear()
        closed = threading.Event()
        with patch.dict(sys.modules, {"cv2": fake_cv2(capture)}):
            camera.start()
            self.assertTrue(capture.read_entered.wait(1))
            worker = camera._thread
            camera.stop(wait=False)
            capture.allow_read.set()
            self.assertTrue(capture.release_entered.wait(1))
            closer = threading.Thread(target=lambda: (camera.close(), closed.set()))
            closer.start()
            try:
                camera.stop(wait=False)
                self.assertFalse(camera.start())
                self.assertTrue(camera.cleanup_pending)
                self.assertIs(camera._thread, worker)
                self.assertFalse(closed.is_set())
                self.assertEqual(capture.release_threads, [worker.ident])
            finally:
                capture.allow_release.set()
                closer.join(2)
        self.assertTrue(closed.is_set())
        self.assertFalse(camera.cleanup_pending)
        self.assertIsNone(camera._thread)

    def test_failed_release_is_retained_reported_then_explicitly_retried(self):
        for mode in ("raise", "false", "open"):
            with self.subTest(mode=mode):
                camera, capture = self.make_camera(), Capture(mode)
                with patch.dict(sys.modules, {"cv2": fake_cv2(capture)}):
                    camera.start()
                    self.assertTrue(capture.read_entered.wait(1))
                    worker = camera._thread
                    camera.stop(wait=False)
                    capture.allow_read.set()
                    worker.join(2)
                    self.assertIs(camera._unreleased_capture, capture)
                    self.assertTrue(camera.cleanup_error)
                    self.assertFalse(camera.start())
                    with self.assertRaisesRegex(RuntimeError, "Camera capture release failed"):
                        camera.close()
                    self.assertEqual(capture.release_threads, [worker.ident])
                    capture.mode = "ok"
                    camera.close()
                    self.assertEqual(len(capture.release_threads), 2)
                    self.assertIsNone(camera._unreleased_capture)
                    self.assertEqual(camera.cleanup_error, "")

    def test_concurrent_close_queued_before_failure_cannot_implicitly_retry(self):
        camera, capture = self.make_camera(), Capture("raise")
        camera._close_lock = CountedLock()
        capture.allow_release.clear()
        outcomes = []
        def close():
            try:
                camera.close()
            except RuntimeError as exc:
                outcomes.append(str(exc))
            else:
                outcomes.append("success")
        with patch.dict(sys.modules, {"cv2": fake_cv2(capture)}):
            camera.start()
            self.assertTrue(capture.read_entered.wait(1))
            worker = camera._thread
            camera.stop(wait=False)
            capture.allow_read.set()
            self.assertTrue(capture.release_entered.wait(1))
            closers = [threading.Thread(target=close) for _ in range(2)]
            try:
                for closer in closers:
                    closer.start()
                self.assertTrue(camera._close_lock.second.wait(1))
                self.assertFalse(camera.start())
            finally:
                capture.allow_release.set()
                for closer in closers:
                    closer.join(2)
        self.assertEqual(len(outcomes), 2)
        self.assertNotIn("success", outcomes)
        self.assertEqual(capture.release_threads, [worker.ident])
        self.assertIs(camera._unreleased_capture, capture)
        capture.mode = "ok"
        camera.close()
        self.assertEqual(len(capture.release_threads), 2)

    def test_queued_retries_cannot_consume_a_new_failure_revision(self):
        camera, capture = self.make_camera(), Capture("raise")
        outcomes = []
        def close():
            try:
                camera.close()
            except RuntimeError as exc:
                outcomes.append(str(exc))
            else:
                outcomes.append("success")
        with patch.dict(sys.modules, {"cv2": fake_cv2(capture)}):
            camera.start()
            self.assertTrue(capture.read_entered.wait(1))
            worker = camera._thread
            camera.stop(wait=False)
            capture.allow_read.set()
            worker.join(2)
            with self.assertRaises(RuntimeError):
                camera.close()
        camera._close_lock = CountedLock()
        capture.release_entered.clear()
        capture.allow_release.clear()
        closers = [threading.Thread(target=close) for _ in range(2)]
        try:
            closers[0].start()
            self.assertTrue(capture.release_entered.wait(1))
            closers[1].start()
            self.assertTrue(camera._close_lock.second.wait(1))
            self.assertEqual(len(capture.release_threads), 2)
        finally:
            capture.allow_release.set()
            for closer in closers:
                closer.join(2)
        self.assertEqual(len(outcomes), 2)
        self.assertNotIn("success", outcomes)
        self.assertEqual(len(capture.release_threads), 2)
        self.assertIs(camera._capture_close_error, capture.failure)
        self.assertIs(camera._unreleased_capture, capture)
        capture.mode = "ok"
        camera.close()
        self.assertEqual(len(capture.release_threads), 3)
        self.assertIsNone(camera._unreleased_capture)

    def test_config_changes_coalesce_while_old_owner_is_still_reading(self):
        camera, capture = self.make_camera(), Capture()
        cv2 = fake_cv2(capture)
        with patch.dict(sys.modules, {"cv2": cv2}):
            camera.start()
            self.assertTrue(capture.read_entered.wait(1))
            worker = camera._thread
            try:
                for camera_id, size in ((3, (320, 240)), (5, (800, 600))):
                    camera.stop(wait=False)
                    camera.configure(camera_id, size)
                    self.assertFalse(camera.start())
                self.assertEqual(cv2.VideoCapture.call_count, 1)
                self.assertIs(camera._thread, worker)
                self.assertIsNone(camera.latest_frame())
            finally:
                capture.allow_read.set()
                camera.close()
            replacement = Capture()
            with patch.object(cv2, "VideoCapture", return_value=replacement) as opened:
                camera.start()
                self.assertTrue(replacement.read_entered.wait(1))
                camera.stop(wait=False)
                replacement.allow_read.set()
                camera.close()
            self.assertEqual(opened.call_args.args[0], 5)
            self.assertEqual(replacement.set_calls, [(1, 800), (2, 600)])

    def test_admitted_callback_is_worker_owned_without_blocking_revocation(self):
        camera, capture = self.make_camera(), Capture()
        callback_entered, allow_callback = threading.Event(), threading.Event()
        closed = threading.Event()
        frames, callback_threads = [], []
        def callback(frame):
            frames.append(frame)
            callback_threads.append(threading.get_ident())
            held(callback_entered, allow_callback)()
            raise RuntimeError("late callback diagnostic must be stale")
        camera.on_frame = callback
        with patch.dict(sys.modules, {"cv2": fake_cv2(capture)}):
            camera.start()
            self.assertTrue(capture.read_entered.wait(1))
            capture.allow_read.set()
            self.assertTrue(callback_entered.wait(1))
            worker = camera._thread
            self.assertIs(camera.latest_frame(), frames[0])
            self.assertEqual(frames[0].sequence, 1)
            self.assertEqual(callback_threads, [worker.ident])
            camera.stop(wait=False)
            self.assertIsNone(camera.latest_frame())
            closer = threading.Thread(target=lambda: (camera.close(), closed.set()))
            closer.start()
            try:
                self.assertFalse(closed.is_set())
                self.assertFalse(camera.start())
                self.assertTrue(camera.cleanup_pending)
                self.assertEqual(capture.release_threads, [])
            finally:
                allow_callback.set()
                closer.join(2)
        self.assertTrue(closed.is_set())
        self.assertEqual(capture.release_threads, [worker.ident])
        self.assertEqual(camera.last_error, "")
        self.assertEqual(len(frames), 1)

    def test_thread_construction_failure_does_not_leave_pending_admission(self):
        camera = self.make_camera()
        with patch("rm_client.vision.threading.Thread", side_effect=RuntimeError("thread construction")):
            with self.assertRaisesRegex(RuntimeError, "thread construction"):
                camera.start()
            self.assertFalse(camera.start())
        self.assertIn("thread construction", camera.last_error)
        self.assertFalse(camera.running)
        self.assertFalse(camera.cleanup_pending)
        self.assertIsNone(camera._thread)
        camera.close()

    def test_real_start_failure_is_retry_throttled_and_never_joined(self):
        camera = self.make_camera()
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("thread quota")) as start:
            with self.assertRaisesRegex(RuntimeError, "thread quota"):
                camera.start()
            self.assertFalse(camera.start())
            self.assertEqual(start.call_count, 1)
        self.assertIsNone(camera._thread)
        self.assertFalse(camera.running)
        self.assertTrue(camera._stop_event.is_set())
        self.assertGreater(camera.retry_after, time.perf_counter())
        self.assertIn("thread quota", camera.last_error)
        camera.close()

    def test_started_then_raised_real_worker_never_enters_native_factory(self):
        camera, capture = self.make_camera(), Capture()
        cv2 = fake_cv2(capture)
        def start_then_raise(thread):
            REAL_START(thread)
            raise RuntimeError("after thread admission")
        with patch.dict(sys.modules, {"cv2": cv2}), patch.object(threading.Thread, "start", start_then_raise):
            with self.assertRaisesRegex(RuntimeError, "after thread admission"):
                camera.start()
        worker = camera._thread
        self.assertIsNotNone(worker)
        self.assertIsNotNone(worker.ident)
        self.assertFalse(camera.running)
        camera.close()
        self.assertFalse(worker.is_alive())
        cv2.VideoCapture.assert_not_called()

    def test_finished_then_raised_worker_is_joinable_and_preserved(self):
        camera = self.make_camera()
        def finish_then_raise(thread):
            REAL_START(thread)
            thread.join(1)
            self.assertFalse(thread.is_alive())
            raise RuntimeError("finished before error")
        with patch.object(camera, "_run", return_value=None), patch.object(threading.Thread, "start", finish_then_raise):
            with self.assertRaisesRegex(RuntimeError, "finished before error"):
                camera.start()
        worker = camera._thread
        self.assertIsNotNone(worker)
        self.assertIsNotNone(worker.ident)
        self.assertFalse(camera.running)
        camera.close()
        self.assertIsNone(camera._thread)

    def test_delayed_thread_start_does_not_block_revocation_or_allow_replacement(self):
        for admitted, failure in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(admitted=admitted, failure=failure):
                camera, capture = self.make_camera(), Capture()
                starting, allow_start = threading.Event(), threading.Event()
                completed, closed = threading.Event(), threading.Event()
                outcomes = []
                closer = None
                def delayed_start(thread):
                    if admitted:
                        REAL_START(thread)
                    held(starting, allow_start)()
                    if failure:
                        raise RuntimeError("delayed thread quota")
                    if not admitted:
                        REAL_START(thread)
                def start_camera():
                    try:
                        outcomes.append(camera.start())
                    except RuntimeError as exc:
                        outcomes.append(str(exc))
                    finally:
                        completed.set()
                cv2 = fake_cv2(capture)
                with patch.dict(sys.modules, {"cv2": cv2}), patch.object(threading.Thread, "start", delayed_start):
                    starter = threading.Thread(target=start_camera)
                    REAL_START(starter)
                    self.assertTrue(starting.wait(1))
                    worker = camera._thread
                    self.assertEqual(worker.ident is not None, admitted)
                    cv2.VideoCapture.assert_not_called()
                    # Rescue only bounds a failing test; correct stop returns
                    # while Thread.start has not resolved its admission.
                    rescue = threading.Timer(2, allow_start.set)
                    REAL_START(rescue)
                    try:
                        before = time.perf_counter()
                        camera.stop(wait=False)
                        self.assertLess(time.perf_counter() - before, .15)
                        self.assertFalse(completed.is_set())
                        self.assertTrue(camera.cleanup_pending)
                        self.assertFalse(camera.start())
                        self.assertIs(camera._thread, worker)
                        closer = threading.Thread(target=lambda: (camera.close(), closed.set()))
                        REAL_START(closer)
                        until(lambda: camera.cleanup_pending)
                        self.assertFalse(closed.is_set())
                    finally:
                        allow_start.set()
                        starter.join(2)
                        if closer is not None:
                            closer.join(2)
                        rescue.cancel()
                        rescue.join(2)
                self.assertTrue(completed.is_set())
                self.assertEqual(outcomes, ["delayed thread quota"] if failure else [False])
                self.assertTrue(closed.is_set())
                self.assertFalse(camera.running)
                self.assertIsNone(camera._thread)
                self.assertFalse(camera.cleanup_pending)
                cv2.VideoCapture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
