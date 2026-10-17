"""Independent camera admission/retry probes using actual workers and fake native IO."""
from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.vision import CameraCaptureWorker

REAL_START = threading.Thread.start


class CountedLock:
    def __init__(self):
        self.lock = threading.Lock()
        self.counter_lock = threading.Lock()
        self.count = 0
        self.second = threading.Event()

    def __enter__(self):
        with self.counter_lock:
            self.count += 1
            if self.count == 2:
                self.second.set()
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


class CaptureBoundary:
    def __init__(self, held_stage=None):
        self.held_stage = held_stage
        self.stage_entered, self.stage_release = threading.Event(), threading.Event()
        self.read_entered, self.read_release = threading.Event(), threading.Event()
        self.release_entered, self.release_permission = threading.Event(), threading.Event()
        self.release_permission.set()
        self.release_threads = []
        self.is_open = True
        self.fail_release = False
        self.open_queries = 0
        self.set_calls = []
        self.resize_calls = 0
        self.conversion_calls = 0

    def pause(self, stage):
        if stage == self.held_stage:
            self.stage_entered.set()
            if not self.stage_release.wait(5):
                raise AssertionError(f"native boundary {stage} was not released")

    def isOpened(self):
        self.open_queries += 1
        self.pause("is_open" if self.is_open else "release_confirmation")
        return self.is_open

    def set(self, prop, value):
        self.set_calls.append((prop, value))
        self.pause("set")

    def read(self):
        self.read_entered.set()
        if not self.read_release.wait(5):
            raise AssertionError("native read was not released")
        return True, SimpleNamespace(shape=(240, 320, 3))

    def resize(self, frame, size, **kwargs):
        self.resize_calls += 1
        self.pause("resize")
        return SimpleNamespace(shape=(size[1], size[0], 3))

    def convert(self, frame, *args):
        self.conversion_calls += 1
        return frame

    def release(self):
        self.release_threads.append(threading.get_ident())
        self.release_entered.set()
        if not self.release_permission.wait(5):
            raise AssertionError("native release was not permitted")
        if self.fail_release:
            raise OSError("controlled release failure")
        self.is_open = False

    def cv2(self):
        return SimpleNamespace(
            VideoCapture=Mock(return_value=self), CAP_PROP_FRAME_WIDTH=1,
            CAP_PROP_FRAME_HEIGHT=2, COLOR_BGR2RGB=3, INTER_LINEAR=4,
            resize=self.resize, cvtColor=self.convert,
        )


class CameraStopIndependentReviewTests(unittest.TestCase):
    def test_extra_native_boundaries_cannot_hold_revocation_or_publish_late(self):
        for stage in ("is_open", "set", "resize", "release_confirmation"):
            with self.subTest(stage=stage):
                camera = CameraCaptureWorker(size=(160, 120))
                capture = CaptureBoundary(stage)
                if stage == "resize":
                    capture.read_release.set()
                closed = threading.Event()
                errors = []
                camera.on_frame = Mock()
                def close():
                    try:
                        camera.close()
                    except BaseException as error:
                        errors.append(error)
                    finally:
                        closed.set()
                closer = None
                with patch.dict(sys.modules, {"cv2": capture.cv2()}):
                    try:
                        self.assertTrue(camera.start())
                        worker = camera._thread
                        if stage == "release_confirmation":
                            self.assertTrue(capture.read_entered.wait(1))
                            camera.stop(wait=False)
                            capture.read_release.set()
                        self.assertTrue(capture.stage_entered.wait(1))
                        with patch.object(worker, "join", side_effect=AssertionError("stop joined camera")):
                            camera.stop(wait=False)
                            self.assertFalse(camera.start())
                        self.assertFalse(camera.connected)
                        self.assertIsNone(camera.latest_frame())
                        self.assertTrue(camera.cleanup_pending)
                        closer = threading.Thread(target=close)
                        closer.start()
                        self.assertFalse(closed.is_set())
                        camera.configure(3, (640, 480))
                        self.assertFalse(camera.start())
                    finally:
                        capture.stage_release.set()
                        capture.read_release.set()
                        if closer is not None:
                            closer.join(2)
                        camera.close()
                self.assertTrue(closed.is_set())
                self.assertEqual(errors, [])
                self.assertEqual(capture.release_threads, [worker.ident])
                self.assertEqual(camera._sequence, 0)
                camera.on_frame.assert_not_called()
                self.assertEqual(capture.conversion_calls, 0)
                if stage == "is_open":
                    self.assertEqual(capture.set_calls, [])
                if stage == "set":
                    self.assertEqual(len(capture.set_calls), 1)
                self.assertIsNone(camera._thread)
                self.assertFalse(camera.cleanup_pending)

    def test_two_queued_retries_cannot_consume_a_new_failure_revision(self):
        camera, capture = CameraCaptureWorker(size=(160, 120)), CaptureBoundary()
        capture.fail_release = True
        with patch.dict(sys.modules, {"cv2": capture.cv2()}):
            camera.start()
            self.assertTrue(capture.read_entered.wait(1))
            worker = camera._thread
            camera.stop(wait=False)
            capture.read_release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            with self.assertRaisesRegex(RuntimeError, "controlled release failure"):
                camera.close()
            first_revision = camera._capture_close_revision
            self.assertTrue(camera._capture_close_error_reported)
            capture.release_entered.clear()
            capture.release_permission.clear()
            camera._close_lock = CountedLock()
            outcomes = []
            def retry():
                try:
                    camera.close()
                except RuntimeError as error:
                    outcomes.append(str(error))
                else:
                    outcomes.append("success")
            retries = [threading.Thread(target=retry) for _ in range(2)]
            try:
                retries[0].start()
                self.assertTrue(capture.release_entered.wait(1))
                retries[1].start()
                self.assertTrue(camera._close_lock.second.wait(1))
                self.assertTrue(camera.cleanup_pending)
                self.assertFalse(camera.start())
                self.assertEqual(len(capture.release_threads), 2)
            finally:
                capture.release_permission.set()
                for thread in retries:
                    thread.join(2)
            self.assertEqual(len(outcomes), 2)
            self.assertNotIn("success", outcomes)
            self.assertEqual(len(capture.release_threads), 2)
            self.assertEqual(camera._capture_close_revision, first_revision + 1)
            self.assertIs(camera._unreleased_capture, capture)
            self.assertFalse(camera.cleanup_pending)
            self.assertTrue(camera.cleanup_error)
            capture.fail_release = False
            camera.close()
        self.assertEqual(len(capture.release_threads), 3)
        self.assertIsNone(camera._unreleased_capture)
        self.assertEqual(camera.cleanup_error, "")

    def test_start_completion_gates_are_distinct_across_cancelled_restart(self):
        camera, capture = CameraCaptureWorker(size=(160, 120)), CaptureBoundary()
        cv2 = capture.cv2()
        entered = [threading.Event(), threading.Event()]
        decision = [threading.Event(), threading.Event()]
        call_count = 0
        outcomes = []
        def delayed_start(thread):
            nonlocal call_count
            index = call_count
            call_count += 1
            REAL_START(thread)
            entered[index].set()
            if not decision[index].wait(5):
                raise AssertionError("start decision was not released")
        def start_camera():
            outcomes.append(camera.start())
        with patch.dict(sys.modules, {"cv2": cv2}), patch.object(threading.Thread, "start", delayed_start):
            starters = []
            try:
                first = threading.Thread(target=start_camera)
                starters.append(first)
                REAL_START(first)
                self.assertTrue(entered[0].wait(1))
                first_gate, first_worker = camera._start_done, camera._thread
                camera.stop(wait=False)
                camera.configure(7, (640, 480))
                self.assertFalse(camera.start())
                cv2.VideoCapture.assert_not_called()
                decision[0].set()
                first.join(2)
                camera.close()
                self.assertTrue(first_gate.is_set())
                self.assertFalse(first_worker.is_alive())
                self.assertEqual(outcomes, [False])
                second = threading.Thread(target=start_camera)
                starters.append(second)
                REAL_START(second)
                self.assertTrue(entered[1].wait(1))
                self.assertIsNot(camera._start_done, first_gate)
                self.assertFalse(camera._start_done.is_set())
                self.assertTrue(first_gate.is_set())
                cv2.VideoCapture.assert_not_called()
                decision[1].set()
                second.join(2)
                self.assertTrue(capture.read_entered.wait(1))
                self.assertEqual(outcomes, [False, True])
                self.assertEqual(cv2.VideoCapture.call_args.args[0], 7)
                self.assertEqual(capture.set_calls, [(1, 640), (2, 480)])
            finally:
                for event in decision:
                    event.set()
                for starter in starters:
                    starter.join(2)
                camera.stop(wait=False)
                capture.read_release.set()
                camera.close()
        self.assertEqual(cv2.VideoCapture.call_count, 1)
        self.assertEqual(len(capture.release_threads), 1)

    def test_constructor_refusal_preserves_completed_owner_and_old_admission_event(self):
        camera, capture = CameraCaptureWorker(size=(160, 120)), CaptureBoundary()
        with patch.dict(sys.modules, {"cv2": capture.cv2()}):
            camera.start()
            self.assertTrue(capture.read_entered.wait(1))
            worker, previous_done = camera._thread, camera._start_done
            camera.stop(wait=False)
            capture.read_release.set()
            worker.join(2)
            self.assertIs(camera._thread, worker)
            with patch("rm_client.vision.threading.Thread", side_effect=RuntimeError("constructor rejected")) as constructor:
                with self.assertRaisesRegex(RuntimeError, "constructor rejected"):
                    camera.start()
                self.assertFalse(camera.start())
                self.assertEqual(constructor.call_count, 1)
                self.assertIs(camera._thread, worker)
                self.assertIs(camera._start_done, previous_done)
                self.assertTrue(previous_done.is_set())
                self.assertFalse(camera.running)
                self.assertFalse(camera.cleanup_pending)
                self.assertIn("constructor rejected", camera.last_error)
                self.assertGreater(camera.retry_after, time.perf_counter())
                camera.close()
        self.assertIsNone(camera._thread)
        self.assertEqual(capture.release_threads, [worker.ident])

    def test_default_stop_is_bounded_while_start_decision_remains_pending(self):
        camera, capture = CameraCaptureWorker(size=(160, 120)), CaptureBoundary()
        cv2 = capture.cv2()
        entered, decision, stopped = threading.Event(), threading.Event(), threading.Event()
        outcomes = []
        def delayed_start(thread):
            REAL_START(thread)
            entered.set()
            if not decision.wait(5):
                raise AssertionError("start decision not released")
        with patch.dict(sys.modules, {"cv2": cv2}), patch.object(threading.Thread, "start", delayed_start):
            starter = threading.Thread(target=lambda: outcomes.append(camera.start()))
            REAL_START(starter)
            self.assertTrue(entered.wait(1))
            worker = camera._thread
            stopper = threading.Thread(target=lambda: (camera.stop(), stopped.set()))
            REAL_START(stopper)
            try:
                self.assertTrue(stopped.wait(2), "legacy bounded stop waited for full admission")
                self.assertFalse(decision.is_set())
                self.assertIs(camera._thread, worker)
                self.assertTrue(worker.is_alive())
                self.assertTrue(camera.cleanup_pending)
                self.assertFalse(camera.start())
                cv2.VideoCapture.assert_not_called()
            finally:
                decision.set()
                starter.join(2)
                stopper.join(2)
                camera.close()
        self.assertEqual(outcomes, [False])
        self.assertFalse(camera.cleanup_pending)
        self.assertIsNone(camera._thread)


if __name__ == "__main__":
    unittest.main()
