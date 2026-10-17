"""Deterministic lifecycle races: no devices, codecs or external processes needed."""
from __future__ import annotations

from pathlib import Path
from queue import Queue
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.manager import BackendManager
from rm_client.backend.video_backend import (
    AnnexBRecoveryGate, ContinuousMjpegDecoder, RmVideoBackend, _DecoderSession,
)
from rm_client.transports import UDPVideoReceiver


class FakePipe:
    def __init__(self):
        self.closed = False
        self.writes = []

    def fileno(self):
        return 99

    def write(self, payload):
        self.writes.append(payload)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.stdin, self.stdout, self.stderr = FakePipe(), FakePipe(), FakePipe()
        self.terminated = False
        self.killed = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


class BlockingDecoder:
    def __init__(self):
        self.available = False
        self.lifecycle_generation = 0
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls = 0

    def health(self):
        return {"stale": True}

    def restart(self, *, clear_latest, expected_generation):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(3):
            raise TimeoutError("test release was not signalled")
        self.available = expected_generation == self.lifecycle_generation
        return self.available

    def stop(self):
        self.lifecycle_generation += 1
        self.available = False

    request_stop = stop


def manager_stub(decoder):
    manager = object.__new__(BackendManager)
    manager.state = SimpleNamespace(deploy_mode=0)
    manager.native_decoder = decoder
    manager.udp = SimpleNamespace(
        decode_h265=False, running=False, request_decode_enabled=lambda enabled: None,
        request_stop=lambda: None,
    )
    manager._active_video_source = "native 0x0310"
    manager._video_source_request = None
    manager._video_source_generation = 0
    manager._video_source_failures = ()
    manager._hero_native_override = False
    manager._native_restart_results = Queue(maxsize=1)
    manager._native_restart_thread = None
    manager._native_restart_generation = 0
    manager._native_next_restart_at = 0.0
    manager._native_restart_backoff_s = 0.5
    manager._native_failure_epoch_handled = False
    manager._native_0310_decoder_started_once = True
    manager._native_0310_bytes_since_reset = 10
    manager._native_0310_decoder_restarts = 0
    manager._native_recovery = AnnexBRecoveryGate("h264")
    manager.events = []
    manager.field_log = SimpleNamespace(
        emit=lambda *args, **kwargs: manager.events.append(threading.get_ident()),
    )
    return manager


def install_session(decoder, session):
    with decoder._lock:
        if decoder._session is not None:
            decoder._session.stopped.set()
        decoder._session = session
        decoder._proc = session.proc
        decoder._input_queue = session.input_queue
        decoder._running = decoder.available = True


class DecoderLifecycleQualityTests(unittest.TestCase):
    def test_native_recovery_is_nonblocking_single_flight_and_ui_applied(self):
        decoder = BlockingDecoder()
        manager = manager_stub(decoder)
        try:
            started = time.perf_counter()
            manager._recover_video_decoders()
            self.assertLess(time.perf_counter() - started, 0.1)
            self.assertTrue(decoder.entered.wait(1))
            for _ in range(100):
                manager._recover_video_decoders()
            self.assertEqual(decoder.calls, 1)
            decoder.release.set()
            manager._native_restart_thread.join(1)
            self.assertEqual(manager._native_0310_decoder_restarts, 0)
            self.assertEqual(manager.events, [])
            manager._poll_native_decoder_restart()
            self.assertEqual(manager._native_0310_decoder_restarts, 1)
            self.assertEqual(manager.events, [threading.get_ident()])
            self.assertTrue(manager._native_recovery.blocked)
        finally:
            decoder.release.set()

    def test_source_switch_cancels_pending_native_restart_completion(self):
        decoder = BlockingDecoder()
        manager = manager_stub(decoder)
        try:
            manager._recover_video_decoders()
            self.assertTrue(decoder.entered.wait(1))
            manager.set_active_video_source("Synthetic")
            decoder.release.set()
            manager._native_restart_thread.join(1)
            manager._poll_native_decoder_restart()
            self.assertFalse(decoder.available)
            self.assertEqual(manager._native_0310_decoder_restarts, 0)
            self.assertEqual(manager.events, [])
        finally:
            decoder.release.set()

    def test_failed_native_recovery_has_backoff(self):
        decoder = BlockingDecoder()
        manager = manager_stub(decoder)
        decoder.lifecycle_generation = 2
        with patch.object(decoder, "restart", return_value=False):
            manager._recover_video_decoders()
            manager._native_restart_thread.join(1)
            manager._poll_native_decoder_restart()
            self.assertGreater(manager._native_next_restart_at, time.perf_counter())
            manager._recover_video_decoders()
            self.assertIsNone(manager._native_restart_thread)
            self.assertEqual(manager._native_restart_backoff_s, 1.0)

    def test_stop_during_restart_teardown_cannot_resurrect_process(self):
        decoder = ContinuousMjpegDecoder("h264")
        old = _DecoderSession(FakeProcess(), Queue())
        install_session(decoder, old)
        entered, release = threading.Event(), threading.Event()
        closed = []

        def close(session, **kwargs):
            closed.append(session)
            if session is old:
                entered.set()
                release.wait(3)
            return True

        with patch.object(decoder, "_close_session", side_effect=close), patch(
            "rm_client.backend.video_backend.subprocess.Popen"
        ) as popen, patch.dict(sys.modules, {"cv2": SimpleNamespace(), "numpy": SimpleNamespace()}):
            generation = decoder.lifecycle_generation
            worker = threading.Thread(target=lambda: decoder.restart(expected_generation=generation))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                decoder.stop()
                release.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                popen.assert_not_called()
                self.assertFalse(decoder.available)
                self.assertIsNone(decoder._session)
            finally:
                release.set()
                worker.join(1)
        self.assertIn(old, closed)

    def test_stop_during_spawn_reaps_unpublished_process(self):
        decoder = ContinuousMjpegDecoder("h264")
        proc = FakeProcess()
        entered, release = threading.Event(), threading.Event()

        def spawn(*args, **kwargs):
            entered.set()
            release.wait(3)
            return proc

        with patch("rm_client.backend.video_backend.resolve_ffmpeg_executable", return_value="ffmpeg"), patch(
            "rm_client.backend.video_backend.subprocess.Popen", side_effect=spawn
        ), patch.dict(sys.modules, {"cv2": SimpleNamespace(), "numpy": SimpleNamespace()}):
            worker = threading.Thread(target=decoder.start)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                decoder.stop()
                release.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertTrue(proc.terminated)
                self.assertTrue(proc.stdout.closed)
                self.assertFalse(decoder.available)
                self.assertIsNone(decoder._proc)
            finally:
                release.set()
                worker.join(1)

    def test_cancelled_restart_request_never_spawns(self):
        decoder = ContinuousMjpegDecoder()
        generation = decoder.lifecycle_generation
        decoder.stop()
        with patch("rm_client.backend.video_backend.subprocess.Popen") as popen:
            self.assertFalse(decoder.restart(expected_generation=generation))
        popen.assert_not_called()
        self.assertEqual(decoder.restart_count, 0)

    def test_shutdown_reaps_after_kill_and_closes_all_pipes(self):
        proc = FakeProcess()
        session = _DecoderSession(proc, Queue())
        with patch.object(proc, "wait", side_effect=[TimeoutError("slow exit"), 0]) as wait:
            ContinuousMjpegDecoder._close_session(session)
        self.assertTrue(proc.terminated)
        self.assertTrue(proc.killed)
        self.assertEqual(wait.call_count, 2)
        self.assertTrue(all(pipe.closed for pipe in (proc.stdin, proc.stdout, proc.stderr)))

    def test_crashed_start_never_reaps_on_caller_thread(self):
        decoder = ContinuousMjpegDecoder()
        old = _DecoderSession(FakeProcess(), Queue())
        install_session(decoder, old)
        decoder._running = decoder.available = False
        with patch.object(decoder, "_close_session") as close, patch.dict(
            sys.modules, {"cv2": SimpleNamespace(), "numpy": SimpleNamespace()}
        ):
            self.assertFalse(decoder.start())
            close.assert_not_called()

    def test_stale_reader_exit_and_stderr_cannot_mutate_replacement(self):
        for loop in ("_read_loop", "_stderr_loop"):
            with self.subTest(loop=loop):
                decoder = ContinuousMjpegDecoder()
                old = _DecoderSession(FakeProcess(), Queue())
                new = _DecoderSession(FakeProcess(), Queue())
                install_session(decoder, old)
                entered, release = threading.Event(), threading.Event()

                def read(*args):
                    entered.set()
                    release.wait(3)
                    return b"late error" if loop == "_stderr_loop" else b""

                with patch("rm_client.backend.video_backend.os.read", side_effect=read):
                    worker = threading.Thread(target=getattr(decoder, loop), args=(old,))
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(1))
                        install_session(decoder, new)
                        release.set()
                        worker.join(1)
                        self.assertFalse(worker.is_alive())
                        self.assertTrue(decoder.available)
                        self.assertIs(decoder._proc, new.proc)
                        self.assertEqual(decoder.last_error, "")
                        self.assertEqual(decoder.stderr_tail(), "")
                    finally:
                        release.set()
                        worker.join(1)

    def test_stale_writer_keeps_old_queue_and_statistics(self):
        decoder = ContinuousMjpegDecoder()
        old = _DecoderSession(FakeProcess(), Queue())
        new = _DecoderSession(FakeProcess(), Queue())
        install_session(decoder, old)
        entered, release = threading.Event(), threading.Event()

        def write(payload):
            entered.set()
            release.wait(3)

        old.proc.stdin.write = write
        old.input_queue.put(b"old")
        worker = threading.Thread(target=decoder._write_loop, args=(old,))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            install_session(decoder, new)
            decoder.write(b"new")
            release.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(new.input_queue.get_nowait(), b"new")
            self.assertEqual(decoder.input_chunks_written, 0)
            self.assertEqual(new.proc.stdin.writes, [])
        finally:
            release.set()
            worker.join(1)

    def test_stale_image_decode_cannot_replace_latest_frame(self):
        decoder = ContinuousMjpegDecoder()
        old = _DecoderSession(FakeProcess(), Queue())
        new = _DecoderSession(FakeProcess(), Queue())
        install_session(decoder, old)
        entered, release = threading.Event(), threading.Event()

        def decode(*args):
            entered.set()
            release.wait(3)
            return SimpleNamespace(shape=(10, 20, 3))

        modules = {
            "cv2": SimpleNamespace(imdecode=decode, IMREAD_COLOR=1),
            "numpy": SimpleNamespace(frombuffer=lambda frame, **kwargs: frame, uint8=object()),
        }
        with patch.dict(sys.modules, modules):
            worker = threading.Thread(target=decoder._decode_frame, args=(b"image", old))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                install_session(decoder, new)
                release.set()
                worker.join(1)
                self.assertIsNone(decoder._latest)
                self.assertEqual(decoder.frames_decoded, 0)
            finally:
                release.set()
                worker.join(1)

    def test_udp_restart_request_never_waits_behind_process_teardown(self):
        backend = RmVideoBackend()
        decoder = BlockingDecoder()
        backend.h265_decoder = decoder
        backend.running = True
        try:
            backend._request_h265_restart()
            self.assertTrue(decoder.entered.wait(1))
            started = time.perf_counter()
            for _ in range(100):
                backend._request_h265_restart()
            self.assertLess(time.perf_counter() - started, 0.1)
            backend.stop()
            decoder.release.set()
            backend._h265_restart_thread.join(1)
            self.assertFalse(decoder.available)
            self.assertEqual(backend.h265_decoder_restarts, 0)
        finally:
            decoder.release.set()

    def test_udp_old_finalizer_cannot_close_new_socket(self):
        class Socket:
            def __init__(self):
                self.entered, self.release = threading.Event(), threading.Event()
                self.closed = False

            def bind(self, address):
                pass

            def settimeout(self, timeout):
                pass

            def recvfrom(self, size):
                self.entered.set()
                self.release.wait(3)
                raise OSError("closed")

            def close(self):
                self.closed = True

        first, second = Socket(), Socket()
        receiver = UDPVideoReceiver()
        with patch("rm_client.transports.socket.socket", side_effect=[first, second]):
            receiver.start()
            old_thread = receiver._thread
            try:
                self.assertTrue(first.entered.wait(1))
                receiver.stop()
                receiver.start()
                self.assertFalse(second.entered.is_set())
                first.release.set()
                old_thread.join(1)
                receiver.start()
                self.assertTrue(second.entered.wait(1))
                self.assertTrue(receiver.running)
                self.assertIs(receiver._socket, second)
                self.assertFalse(second.closed)
                self.assertEqual(receiver.last_error, "")
            finally:
                first.release.set()
                second.release.set()
                receiver.stop()
                old_thread.join(1)

    def test_udp_bind_failure_closes_local_socket(self):
        sock = SimpleNamespace(
            bind=lambda address: (_ for _ in ()).throw(OSError("occupied")),
            close=lambda: closed.append(True),
        )
        closed = []
        receiver = UDPVideoReceiver()
        with patch("rm_client.transports.socket.socket", return_value=sock):
            receiver.start()
            receiver._thread.join(1)
        self.assertTrue(closed)
        self.assertFalse(receiver.running)
        self.assertIsNone(receiver._socket)
        self.assertEqual(receiver.last_error, "occupied")


if __name__ == "__main__":
    unittest.main()
