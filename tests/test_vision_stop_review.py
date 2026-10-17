"""Independent real-worker ownership probes; all native boundaries are synthetic.

These checks use the production start/run/stop/close methods. Events expose
specific lifecycle orderings, rather than claiming hardware latency coverage.
"""
from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.tongji_bridge import TongjiVisionReceiver
from rm_client.tongji_yolo import LocalAlignedVision
from rm_client.vision import TongjiVisionBridge


def settle(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("controlled lifecycle did not settle")
        time.sleep(.001)


class CountedLock:
    """Observe admission to an ordinary lock without changing its exclusion."""
    def __init__(self):
        self.lock = threading.Lock()
        self.counter_lock = threading.Lock()
        self.attempts = 0
        self.second_attempt = threading.Event()

    def __enter__(self):
        with self.counter_lock:
            self.attempts += 1
            if self.attempts >= 2:
                self.second_attempt.set()
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


class ControlledSocket:
    def __init__(self, *, fail_first_close=False):
        self.recv_entered = threading.Event()
        self.recv_release = threading.Event()
        self.close_entered = threading.Event()
        self.close_release = threading.Event()
        self.fail_first_close = fail_first_close
        self.close_threads = []
        self.closed = False

    def setsockopt(self, *args):
        pass

    def bind(self, *args):
        pass

    def settimeout(self, *args):
        pass

    def recv(self, *args):
        self.recv_entered.set()
        if not self.recv_release.wait(5):
            raise AssertionError("controlled recv was not released")
        raise OSError("controlled receive exit")

    def recvfrom(self, *args):
        return self.recv(*args), ("synthetic", 0)

    def close(self):
        self.close_threads.append(threading.get_ident())
        self.close_entered.set()
        if not self.close_release.wait(5):
            raise AssertionError("controlled native close was not released")
        if self.fail_first_close and len(self.close_threads) == 1:
            raise OSError("first native close failed")
        self.closed = True

    def fileno(self):
        return -1 if self.closed else 91


def socket_owners():
    return (
        (TongjiVisionReceiver(), "rm_client.tongji_bridge.socket.socket", "_socket"),
        (TongjiVisionBridge(), "rm_client.vision.socket.create_connection", "_unreleased_socket"),
    )


class VisionStopIndependentReviewTests(unittest.TestCase):
    def test_concurrent_close_queued_before_failure_is_not_explicit_retry(self):
        for owner, factory, handle_attr in socket_owners():
            with self.subTest(owner=type(owner).__name__):
                sock = ControlledSocket(fail_first_close=True)
                lock = CountedLock()
                owner._close_lock = lock
                outcomes = []
                def close():
                    try:
                        owner.close()
                    except RuntimeError as exc:
                        outcomes.append(str(exc))
                    else:
                        outcomes.append("success")
                with patch(factory, return_value=sock):
                    owner.start()
                    self.assertTrue(sock.recv_entered.wait(1))
                    worker = owner._thread
                    owner.stop(wait=False)
                    sock.recv_release.set()
                    self.assertTrue(sock.close_entered.wait(1))
                    closers = [threading.Thread(target=close) for _ in range(2)]
                    try:
                        for closer in closers:
                            closer.start()
                        self.assertTrue(lock.second_attempt.wait(1))
                        self.assertEqual(sock.close_threads, [worker.ident])
                        self.assertTrue(owner.cleanup_pending)
                    finally:
                        sock.close_release.set()
                        for closer in closers:
                            closer.join(2)
                    self.assertFalse(any(closer.is_alive() for closer in closers))
                    self.assertEqual(len(outcomes), 2)
                    self.assertNotIn("success", outcomes)
                    self.assertEqual(sock.close_threads, [worker.ident])
                    self.assertIs(getattr(owner, handle_attr), sock)
                    self.assertTrue(owner.cleanup_error)
                    self.assertFalse(owner.cleanup_pending)
                    owner.close()  # A fresh request made after failure reporting.
                    self.assertTrue(sock.closed)
                    self.assertIsNone(getattr(owner, handle_attr))
                    self.assertEqual(len(sock.close_threads), 2)
                    self.assertEqual(owner.cleanup_error, "")

    def test_concurrent_successful_close_confirms_one_worker_only_release(self):
        for owner, factory, handle_attr in socket_owners():
            with self.subTest(owner=type(owner).__name__):
                sock = ControlledSocket()
                owner._close_lock = CountedLock()
                completed = []
                with patch(factory, return_value=sock):
                    owner.start()
                    self.assertTrue(sock.recv_entered.wait(1))
                    worker = owner._thread
                    owner.stop(wait=False)
                    sock.recv_release.set()
                    self.assertTrue(sock.close_entered.wait(1))
                    closers = [threading.Thread(target=lambda: (owner.close(), completed.append(True)))
                               for _ in range(2)]
                    try:
                        for closer in closers:
                            closer.start()
                        self.assertTrue(owner._close_lock.second_attempt.wait(1))
                        self.assertEqual(completed, [])
                        owner.stop(wait=False)
                        owner.start()
                        self.assertIs(owner._thread, worker)
                        self.assertTrue(owner.cleanup_pending)
                    finally:
                        sock.close_release.set()
                        for closer in closers:
                            closer.join(2)
                    self.assertEqual(completed, [True, True])
                    self.assertEqual(sock.close_threads, [worker.ident])
                    self.assertTrue(sock.closed)
                    self.assertIsNone(owner._thread)
                    self.assertIsNone(getattr(owner, handle_attr))
                    self.assertFalse(owner.cleanup_pending)

    def test_real_worker_start_then_raise_revokes_before_native_admission(self):
        real_start = threading.Thread.start
        entries = (
            (LocalAlignedVision(Path("synthetic.xml")), "rm_client.tongji_yolo.TongjiYoloV5"),
            (TongjiVisionReceiver(), "rm_client.tongji_bridge.socket.socket"),
            (TongjiVisionBridge(), "rm_client.vision.socket.create_connection"),
        )
        for owner, factory in entries:
            def start_then_raise(thread):
                real_start(thread)
                raise RuntimeError("after OS thread admission")
            with self.subTest(owner=type(owner).__name__), patch(factory) as native:
                with patch.object(threading.Thread, "start", start_then_raise):
                    with self.assertRaisesRegex(RuntimeError, "after OS"):
                        owner.start()
                worker = owner._thread
                self.assertIsNotNone(worker)
                self.assertIsNotNone(worker.ident)
                self.assertFalse(owner.running)
                owner.close()
                native.assert_not_called()
                self.assertFalse(worker.is_alive())
                self.assertIsNone(owner._thread)

    def test_loaded_detector_destructor_is_worker_owned_and_strictly_awaited(self):
        local = LocalAlignedVision(Path("synthetic.xml"))
        destroying, allow_destroy = threading.Event(), threading.Event()
        destroyed_by = []
        closed = threading.Event()
        class Detector:
            active_device = "SYNTHETIC"
            def __del__(self):
                destroyed_by.append(threading.get_ident())
                destroying.set()
                allow_destroy.wait(5)
        # Do not keep a test-owned detector reference: the production worker
        # must carry the final native reference after successful publication.
        with patch("rm_client.tongji_yolo.TongjiYoloV5", side_effect=lambda *args: Detector()):
            local.start()
            settle(lambda: local.active_device == "SYNTHETIC")
            worker = local._thread
            local.stop(wait=False)
            self.assertTrue(destroying.wait(1))
            closer = threading.Thread(target=lambda: (local.close(), closed.set()))
            closer.start()
            try:
                # Both operations have to remain usable while destructor is
                # held. A timer is only an escape hatch, not a speed assertion.
                local.stop(wait=False)
                local.start()
                self.assertIs(local._thread, worker)
                self.assertFalse(local.running)
                self.assertTrue(local.cleanup_pending)
                self.assertFalse(closed.is_set())
                self.assertEqual(destroyed_by, [worker.ident])
                self.assertNotEqual(worker.ident, threading.get_ident())
            finally:
                allow_destroy.set()
                closer.join(2)
            self.assertTrue(closed.is_set())
            self.assertFalse(worker.is_alive())
            self.assertIsNone(local._thread)
            self.assertFalse(local.cleanup_pending)


if __name__ == "__main__":
    unittest.main()
