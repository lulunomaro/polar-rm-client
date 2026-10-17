"""Vision stop admission with held inert workers and synthetic native owners.

No model, camera, network connection, or GPU is used. Scheduling assertions
measure this controlled workload, not native-device latency or presentation.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.tongji_bridge import TongjiVisionReceiver, build_tongji_packet
from rm_client.tongji_yolo import LocalAlignedVision
from rm_client.vision import (
    PACKET_HEADER, PACKET_RESULT, PROTOCOL_MAGIC, PROTOCOL_VERSION,
    TongjiVisionBridge,
)


def owners():
    return (LocalAlignedVision(Path("no-model.xml")), TongjiVisionReceiver(), TongjiVisionBridge())


def until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("synthetic worker did not settle")
        time.sleep(.001)


def held(entered, release):
    def run(*_args, **_kwargs):
        entered.set()
        if not release.wait(4):
            raise AssertionError("test did not release held worker")
    return run


class FakeSocket:
    def __init__(self, packet=b"", mode="ok"):
        self.packet = packet
        self.mode = mode
        self.closed = False
        self.recv_entered = threading.Event()
        self.recv_release = threading.Event()
        self.close_entered = threading.Event()
        self.close_release = threading.Event()
        self.close_release.set()
        self.close_threads = []

    def setsockopt(self, *_args):
        pass

    def bind(self, *_args):
        pass

    def settimeout(self, *_args):
        pass

    def recv(self, *_args):
        self.recv_entered.set()
        if not self.recv_release.wait(4):
            raise AssertionError("test did not release recv")
        return self.packet

    def recvfrom(self, *_args):
        return self.recv(), ("TEST", 1)

    def fileno(self):
        return -1 if self.closed else 77

    def close(self):
        self.close_threads.append(threading.get_ident())
        self.close_entered.set()
        if not self.close_release.wait(4):
            raise AssertionError("test did not release socket close")
        if self.mode == "raise":
            raise OSError("synthetic close failure")
        if self.mode == "false":
            return False
        if self.mode == "open":
            return None
        self.closed = True


def tcp_packet():
    payload = json.dumps({"source_width": 640, "source_height": 360}).encode()
    return PACKET_HEADER.pack(PROTOCOL_MAGIC, PROTOCOL_VERSION, PACKET_RESULT,
                              1, time.time_ns(), len(payload)) + payload


class VisionStopAdmissionTests(unittest.TestCase):
    def test_no_wait_revokes_without_join_and_preserves_one_live_owner(self):
        for owner in owners():
            with self.subTest(owner=type(owner).__name__):
                entered, release = threading.Event(), threading.Event()
                with patch.object(owner, "_run", side_effect=held(entered, release)) as run:
                    owner.start()
                    self.assertTrue(entered.wait(1))
                    worker = owner._thread
                    try:
                        started = time.perf_counter()
                        with patch.object(worker, "join", side_effect=AssertionError("UI joined worker")):
                            owner.stop(wait=False)
                            owner.stop(wait=False)
                            owner.start()
                        self.assertLess(time.perf_counter() - started, .15)
                        self.assertFalse(owner.running)
                        self.assertTrue(owner.cleanup_pending)
                        self.assertIs(owner._thread, worker)
                        self.assertEqual(run.call_count, 1)
                    finally:
                        release.set()
                        owner.close()
                    self.assertFalse(worker.is_alive())
                    self.assertIsNone(owner._thread)
                    self.assertFalse(owner.cleanup_pending)

    def test_no_wait_clears_results_and_queued_inputs_immediately(self):
        local, receiver, bridge = owners()
        local._latest = local._pending = object()
        receiver._latest = object()
        bridge.last_result = bridge._pending = object()
        for owner in (local, receiver, bridge):
            owner.stop(wait=False)
        self.assertIsNone(local.latest())
        self.assertIsNone(local._pending)
        self.assertIsNone(receiver.latest(100))
        self.assertIsNone(bridge.latest_result())
        self.assertIsNone(bridge._pending)

    def test_no_wait_configure_coalesces_latest_target_without_replacement(self):
        for owner, configure, expected in (
            (LocalAlignedVision(Path("old.xml")),
             lambda item, number: item.configure(Path(f"new{number}.xml"), "CPU", wait=False),
             lambda item: item.model_path == Path("new2.xml")),
            (TongjiVisionReceiver(1), lambda item, number: item.configure(number, wait=False),
             lambda item: item.port == 2),
        ):
            entered, release = threading.Event(), threading.Event()
            with self.subTest(owner=type(owner).__name__), \
                 patch.object(owner, "_run", side_effect=held(entered, release)) as run:
                owner.start()
                self.assertTrue(entered.wait(1))
                worker = owner._thread
                try:
                    with patch.object(worker, "join", side_effect=AssertionError("configure joined")):
                        configure(owner, 3)
                        owner.start()
                        configure(owner, 2)
                        owner.start()
                    self.assertTrue(expected(owner))
                    self.assertIs(owner._thread, worker)
                    self.assertEqual(run.call_count, 1)
                finally:
                    release.set()
                    owner.close()

    def test_start_refusal_drops_only_unstarted_slot_and_allows_retry(self):
        for owner in owners():
            with self.subTest(owner=type(owner).__name__):
                with patch.object(threading.Thread, "start", side_effect=RuntimeError("thread quota")):
                    with self.assertRaisesRegex(RuntimeError, "thread quota"):
                        owner.start()
                self.assertIsNone(owner._thread)
                self.assertFalse(owner.running)
                owner.close()  # Must not join an unstarted placeholder.
                with patch.object(owner, "_run", return_value=None):
                    owner.start()
                    owner.close()

    def test_started_then_raised_owner_is_retained_until_strict_close(self):
        original_start = threading.Thread.start
        for owner in owners():
            with self.subTest(owner=type(owner).__name__):
                entered, release = threading.Event(), threading.Event()
                def start_then_raise(thread):
                    original_start(thread)
                    raise RuntimeError("wrapper after admission")
                with patch.object(owner, "_run", side_effect=held(entered, release)) as run:
                    with patch.object(threading.Thread, "start", start_then_raise):
                        with self.assertRaisesRegex(RuntimeError, "after admission"):
                            owner.start()
                    self.assertTrue(entered.wait(1))
                    worker = owner._thread
                    try:
                        self.assertIsNotNone(worker.ident)
                        self.assertFalse(owner.running)
                        owner.start()
                        self.assertIs(owner._thread, worker)
                        self.assertEqual(run.call_count, 1)
                    finally:
                        release.set()
                        owner.close()
                    self.assertFalse(worker.is_alive())
                    self.assertIsNone(owner._thread)

    def test_strict_close_stays_pending_and_rejects_start_until_real_exit(self):
        for owner in owners():
            with self.subTest(owner=type(owner).__name__):
                entered, release = threading.Event(), threading.Event()
                closed = threading.Event()
                with patch.object(owner, "_run", side_effect=held(entered, release)) as run:
                    owner.start()
                    self.assertTrue(entered.wait(1))
                    worker = owner._thread
                    closer = threading.Thread(target=lambda: (owner.close(), closed.set()))
                    closer.start()
                    try:
                        until(lambda: not owner.running)
                        owner.start()
                        self.assertFalse(closed.is_set())
                        self.assertIs(owner._thread, worker)
                        self.assertEqual(run.call_count, 1)
                    finally:
                        release.set()
                        closer.join(2)
                    self.assertTrue(closed.is_set())
                    self.assertFalse(worker.is_alive())

    def test_late_local_inference_does_not_republish_after_stop(self):
        entered, release = threading.Event(), threading.Event()
        detector = NS(active_device="TEST", detect=held(entered, release))
        local = LocalAlignedVision(Path("no-model.xml"))
        with patch("rm_client.tongji_yolo.TongjiYoloV5", return_value=detector):
            local.start()
            local.submit(1, 640, 360, NS(copy=lambda: object()))
            self.assertTrue(entered.wait(1))
            worker = local._thread
            try:
                local.stop(wait=False)
                self.assertIs(local._detector, detector)
                local.start()
                self.assertIs(local._thread, worker)
            finally:
                release.set()
                local.close()
        self.assertIsNone(local.latest())
        self.assertIsNone(local._detector)

    def test_late_model_load_destroys_on_owner_without_ui_lock(self):
        loading, loaded = threading.Event(), threading.Event()
        destroying, destroyed = threading.Event(), threading.Event()
        destruction_threads = []
        class Detector:
            active_device = "TEST"
            def __del__(self):
                destruction_threads.append(threading.get_ident())
                destroying.set()
                destroyed.wait(4)
        def load(*_args):
            loading.set()
            loaded.wait(4)
            return Detector()
        local = LocalAlignedVision(Path("no-model.xml"))
        with patch("rm_client.tongji_yolo.TongjiYoloV5", side_effect=load):
            local.start()
            self.assertTrue(loading.wait(1))
            worker = local._thread
            try:
                local.stop(wait=False)
                loaded.set()
                self.assertTrue(destroying.wait(1))
                # Native destructor is held; revocation/start still return.
                local.stop(wait=False)
                local.start()
                self.assertIs(local._thread, worker)
                self.assertTrue(local.cleanup_pending)
                self.assertIsNone(local._detector)
                self.assertEqual(destruction_threads, [worker.ident])
                self.assertNotEqual(worker.ident, threading.get_ident())
            finally:
                loaded.set()
                destroyed.set()
                local.close()
        self.assertIsNone(local._thread)

    def test_slow_frame_copy_cannot_publish_into_a_new_epoch(self):
        local = LocalAlignedVision(Path("no-model.xml"))
        copying, copied = threading.Event(), threading.Event()
        local.running = True
        producer = threading.Thread(target=lambda: local.submit(1, 640, 360,
                                    NS(copy=held(copying, copied))))
        producer.start()
        self.assertTrue(copying.wait(1))
        local.stop(wait=False)
        local.running = True  # Simulates a later admitted generation.
        copied.set()
        producer.join(1)
        self.assertIsNone(local._pending)
        local.stop(wait=False)

    def test_late_udp_packet_and_tcp_result_are_not_published(self):
        for owner, factory, packet in (
            (TongjiVisionReceiver(), "rm_client.tongji_bridge.socket.socket", build_tongji_packet(640, 360, [])),
            (TongjiVisionBridge(), "rm_client.vision.socket.create_connection", tcp_packet()),
        ):
            with self.subTest(owner=type(owner).__name__):
                sock = FakeSocket(packet)
                callback = Mock()
                owner.on_result = callback
                with patch(factory, return_value=sock):
                    owner.start()
                    self.assertTrue(sock.recv_entered.wait(1))
                    worker = owner._thread
                    owner.stop(wait=False)
                    sock.recv_release.set()
                    owner.close()
                self.assertFalse(owner.connected)
                self.assertIsNone(owner.latest(1000) if isinstance(owner, TongjiVisionReceiver) else owner.latest_result())
                callback.assert_not_called()
                self.assertEqual(sock.close_threads, [worker.ident])

    def test_late_socket_open_and_blocked_native_close_keep_single_owner(self):
        for owner, factory in (
            (TongjiVisionReceiver(), "rm_client.tongji_bridge.socket.socket"),
            (TongjiVisionBridge(), "rm_client.vision.socket.create_connection"),
        ):
            with self.subTest(owner=type(owner).__name__):
                opening, opened = threading.Event(), threading.Event()
                sock = FakeSocket()
                sock.close_release.clear()
                def open_socket(*_args, **_kwargs):
                    opening.set()
                    opened.wait(4)
                    return sock
                with patch(factory, side_effect=open_socket) as create:
                    owner.start()
                    self.assertTrue(opening.wait(1))
                    worker = owner._thread
                    try:
                        owner.stop(wait=False)
                        opened.set()
                        self.assertTrue(sock.close_entered.wait(1))
                        self.assertFalse(owner.connected)
                        with patch.object(worker, "join", side_effect=AssertionError("UI joined close")):
                            owner.stop(wait=False)
                            owner.start()
                        self.assertTrue(owner.cleanup_pending)
                        self.assertIs(owner._thread, worker)
                        self.assertEqual(create.call_count, 1)
                        self.assertEqual(sock.close_threads, [worker.ident])
                    finally:
                        opened.set()
                        sock.close_release.set()
                        owner.close()
                self.assertTrue(sock.closed)

    def test_socket_close_failures_remain_owned_until_explicit_second_close(self):
        for cls, factory, handle in (
            (TongjiVisionReceiver, "rm_client.tongji_bridge.socket.socket", "_socket"),
            (TongjiVisionBridge, "rm_client.vision.socket.create_connection", "_unreleased_socket"),
        ):
            for mode in ("raise", "false", "open"):
                with self.subTest(owner=cls.__name__, mode=mode):
                    owner = cls()
                    sock = FakeSocket(mode=mode)
                    with patch(factory, return_value=sock) as create:
                        owner.start()
                        self.assertTrue(sock.recv_entered.wait(1))
                        owner.stop(wait=False)
                        sock.recv_release.set()
                        with self.assertRaises(RuntimeError):
                            owner.close()
                        self.assertIs(getattr(owner, handle), sock)
                        self.assertTrue(owner.cleanup_error)
                        self.assertFalse(owner.cleanup_pending)
                        for _ in range(3):
                            owner.stop(wait=False)
                            owner.start()
                        self.assertEqual(create.call_count, 1)
                        self.assertEqual(len(sock.close_threads), 1)
                        sock.mode = "ok"
                        owner.close()
                    self.assertTrue(sock.closed)
                    self.assertIsNone(getattr(owner, handle))
                    self.assertEqual(owner.cleanup_error, "")
                    self.assertEqual(len(sock.close_threads), 2)


    def test_started_and_finished_then_raised_thread_remains_joinable(self):
        original_start = threading.Thread.start
        for owner in owners():
            with self.subTest(owner=type(owner).__name__):
                def start_finish_raise(thread):
                    original_start(thread)
                    thread.join(1)
                    self.assertFalse(thread.is_alive())
                    raise RuntimeError("finished before wrapper error")
                with patch.object(owner, "_run", return_value=None), \
                     patch.object(threading.Thread, "start", start_finish_raise):
                    with self.assertRaisesRegex(RuntimeError, "finished before"):
                        owner.start()
                self.assertIsNotNone(owner._thread)
                self.assertIsNotNone(owner._thread.ident)
                owner.close()
                self.assertIsNone(owner._thread)

    def test_thread_constructor_refusal_does_not_admit_running_state(self):
        for owner in owners():
            with self.subTest(owner=type(owner).__name__), \
                 patch("threading.Thread", side_effect=RuntimeError("constructor quota")):
                with self.assertRaisesRegex(RuntimeError, "constructor quota"):
                    owner.start()
                self.assertFalse(owner.running)
                self.assertIsNone(owner._thread)
                owner.close()

    def test_revocation_during_packet_parsing_discards_completed_result(self):
        from rm_client.tongji_bridge import parse_tongji_packet
        from rm_client.vision import VisionResult
        for owner, factory, packet, parser, original in (
            (TongjiVisionReceiver(), "rm_client.tongji_bridge.socket.socket", build_tongji_packet(640, 360, []),
             "rm_client.tongji_bridge.parse_tongji_packet", parse_tongji_packet),
            (TongjiVisionBridge(), "rm_client.vision.socket.create_connection", tcp_packet(),
             "rm_client.vision.VisionResult.from_payload", VisionResult.from_payload),
        ):
            with self.subTest(owner=type(owner).__name__):
                parsing, parsed = threading.Event(), threading.Event()
                sock = FakeSocket(packet)
                sock.recv_release.set()
                def parse(*args):
                    parsing.set()
                    parsed.wait(4)
                    return original(*args)
                with patch(factory, return_value=sock), patch(parser, side_effect=parse):
                    owner.start()
                    self.assertTrue(parsing.wait(1))
                    try:
                        owner.stop(wait=False)
                    finally:
                        parsed.set()
                        owner.close()
                self.assertIsNone(owner.latest(1000) if isinstance(owner, TongjiVisionReceiver) else owner.latest_result())
                self.assertFalse(owner.connected)

    def test_native_retry_is_serialized_and_start_remains_blocked(self):
        for owner, handle in ((TongjiVisionReceiver(), "_socket"), (TongjiVisionBridge(), "_unreleased_socket")):
            with self.subTest(owner=type(owner).__name__):
                sock = FakeSocket()
                sock.close_release.clear()
                setattr(owner, handle, sock)
                owner._socket_close_error = OSError("already reported")
                owner._socket_close_error_reported = True
                finished = threading.Event()
                closer = threading.Thread(target=lambda: (owner.close(), finished.set()))
                closer.start()
                self.assertTrue(sock.close_entered.wait(1))
                try:
                    owner.stop(wait=False)
                    with patch("threading.Thread") as constructor:
                        owner.start()
                    constructor.assert_not_called()
                    self.assertTrue(owner.cleanup_pending)
                    self.assertFalse(finished.is_set())
                finally:
                    sock.close_release.set()
                    closer.join(1)
                self.assertTrue(finished.is_set())
                self.assertIsNone(getattr(owner, handle))
                self.assertFalse(owner.cleanup_pending)
                self.assertEqual(len(sock.close_threads), 1)

    def test_local_revocation_during_postprocess_discards_completed_result(self):
        entered, release = threading.Event(), threading.Event()
        local = LocalAlignedVision(Path("no-model.xml"))
        detector = NS(active_device="TEST", detect=lambda _: [])
        with patch("rm_client.tongji_yolo.TongjiYoloV5", return_value=detector), \
             patch.object(local._loss_probe, "sample_loss", return_value=None), \
             patch.object(local._target_selector, "select", side_effect=held(entered, release)):
            local.start()
            local.submit(1, 640, 360, NS(copy=lambda: object()))
            self.assertTrue(entered.wait(1))
            try:
                local.stop(wait=False)
            finally:
                release.set()
                local.close()
        self.assertIsNone(local.latest())

    def test_slow_jpeg_encoding_cannot_publish_into_new_epoch(self):
        bridge = TongjiVisionBridge()
        encoding, encoded = threading.Event(), threading.Event()
        def encode(*_args):
            encoding.set()
            encoded.wait(4)
            return True, b"old frame"
        bridge.running = True
        fake_cv2 = NS(IMWRITE_JPEG_QUALITY=1, imencode=encode)
        with patch.dict(sys.modules, {"cv2": fake_cv2}):
            producer = threading.Thread(target=lambda: bridge.submit_bgr(
                NS(sequence=1, timestamp_ns=1, width=640, height=360, bgr=object())))
            producer.start()
            self.assertTrue(encoding.wait(1))
            bridge.stop(wait=False)
            bridge.running = True
            encoded.set()
            producer.join(1)
        self.assertIsNone(bridge._pending)
        bridge.stop(wait=False)

    def test_inflight_callback_cannot_hold_ui_revocation_lock(self):
        bridge = TongjiVisionBridge()
        sock = FakeSocket(tcp_packet())
        sock.recv_release.set()
        entered, release = threading.Event(), threading.Event()
        bridge.on_result = held(entered, release)
        with patch("rm_client.vision.socket.create_connection", return_value=sock):
            bridge.start()
            self.assertTrue(entered.wait(1))
            worker = bridge._thread
            try:
                bridge.stop(wait=False)
                bridge.start()
                self.assertIs(bridge._thread, worker)
                self.assertIsNone(bridge.latest_result())
                self.assertTrue(bridge.cleanup_pending)
            finally:
                release.set()
                bridge.close()
        self.assertEqual(sock.close_threads, [worker.ident])

    def test_own_worker_close_is_rejected_even_when_external_close_is_waiting(self):
        for owner in owners():
            entered, finish = threading.Event(), threading.Event()
            errors = []
            def work(*_args):
                entered.set()
                finish.wait(4)
                try:
                    owner.close()
                except RuntimeError as exc:
                    errors.append(str(exc))
            with self.subTest(owner=type(owner).__name__), patch.object(owner, "_run", side_effect=work):
                owner.start()
                self.assertTrue(entered.wait(1))
                closer = threading.Thread(target=owner.close)
                closer.start()
                try:
                    until(lambda: owner._closing)
                    finish.set()
                    closer.join(1)
                    self.assertFalse(closer.is_alive())
                    self.assertEqual(len(errors), 1)
                    self.assertIn("own worker", errors[0])
                finally:
                    finish.set()
                    closer.join(2)


    def test_close_queued_before_failure_report_does_not_implicitly_retry(self):
        class ObservedLock:
            def __init__(self):
                self.lock = threading.Lock()
                self.entries = 0
                self.second_entered = threading.Event()
            def __enter__(self):
                self.entries += 1
                if self.entries == 2:
                    self.second_entered.set()
                self.lock.acquire()
            def __exit__(self, *_args):
                self.lock.release()
        for owner, factory, handle in (
            (TongjiVisionReceiver(), "rm_client.tongji_bridge.socket.socket", "_socket"),
            (TongjiVisionBridge(), "rm_client.vision.socket.create_connection", "_unreleased_socket"),
        ):
            with self.subTest(owner=type(owner).__name__):
                sock = FakeSocket(mode="raise")
                sock.close_release.clear()
                lock = owner._close_lock = ObservedLock()
                failures = []
                def close():
                    try:
                        owner.close()
                    except RuntimeError as error:
                        failures.append(str(error))
                with patch(factory, return_value=sock):
                    owner.start()
                    self.assertTrue(sock.recv_entered.wait(1))
                    owner.stop(wait=False)
                    sock.recv_release.set()
                    self.assertTrue(sock.close_entered.wait(1))
                    first = threading.Thread(target=close)
                    second = threading.Thread(target=close)
                    first.start()
                    until(lambda: owner._closing)
                    second.start()
                    try:
                        self.assertTrue(lock.second_entered.wait(1))
                    finally:
                        sock.close_release.set()
                        first.join(2)
                        second.join(2)
                    self.assertEqual(len(failures), 2)
                    self.assertEqual(len(sock.close_threads), 1)
                    self.assertIs(getattr(owner, handle), sock)
                    sock.mode = "ok"
                    owner.close()
                    self.assertEqual(len(sock.close_threads), 2)
                    self.assertIsNone(getattr(owner, handle))


if __name__ == "__main__":
    unittest.main()
