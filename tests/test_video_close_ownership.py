"""Video resource finalization retains failed native owners and blocks reuse."""
from pathlib import Path
import sys
from queue import Queue
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.backend.lifecycle import close_decoder, close_video
from rm_client.backend.video_backend import ContinuousMjpegDecoder, RmVideoBackend, _DecoderSession
from rm_client.transports import UDPVideoReceiver, _UDPVideoSession


class Socket:
    def __init__(self):
        self.fail_close = True
        self.closed = False
    def bind(self, address):
        pass
    def settimeout(self, timeout):
        pass
    def recvfrom(self, size):
        raise OSError("receiver ended")
    def close(self):
        if self.fail_close:
            raise OSError("socket close failed")
        self.closed = True


class Pipe:
    def __init__(self):
        self.closed = False
        self.fail_close = False
    def close(self):
        if self.fail_close:
            raise OSError("pipe close failed")
        self.closed = True


class Process:
    def __init__(self):
        self.stdin, self.stdout, self.stderr = Pipe(), Pipe(), Pipe()
        self.alive = True
        self.fail_reap = False
    def poll(self):
        return None if self.alive else 0
    def terminate(self):
        if self.fail_reap:
            raise OSError("terminate failed")
        self.alive = False
    def kill(self):
        if self.fail_reap:
            raise OSError("kill failed")
        self.alive = False
    def wait(self, timeout=None):
        if self.fail_reap:
            raise TimeoutError("reap failed")
        self.alive = False
        return 0


def installed_decoder(proc):
    decoder = ContinuousMjpegDecoder()
    session = _DecoderSession(proc, Queue())
    decoder._session, decoder._proc = session, proc
    decoder._running = decoder.available = True
    return decoder, session


class VideoCloseOwnershipTests(unittest.TestCase):
    def test_decoder_pipe_close_failure_retains_session_and_blocks_spawn(self):
        proc = Process()
        proc.stdout.fail_close = True
        decoder, session = installed_decoder(proc)
        with self.assertRaisesRegex(RuntimeError, "pipe close failed"):
            close_decoder(decoder, None)
        self.assertEqual(decoder._retired_sessions, [session])
        self.assertFalse(decoder.cleanup_complete)
        for _ in range(50):
            decoder.stop()
        self.assertEqual(decoder._retired_sessions, [session])
        with patch("rm_client.backend.video_backend.subprocess.Popen") as spawn, patch.dict(
                sys.modules, {"cv2": SimpleNamespace(), "numpy": SimpleNamespace()}):
            self.assertFalse(decoder.start())
            self.assertFalse(decoder.restart())
            spawn.assert_not_called()
        proc.stdout.fail_close = False
        close_decoder(decoder, None)
        self.assertTrue(decoder.cleanup_complete)
        self.assertEqual(decoder._retired_sessions, [])

    def test_decoder_unreaped_process_remains_owned_after_failed_termination(self):
        proc = Process()
        proc.fail_reap = True
        decoder, session = installed_decoder(proc)
        with self.assertRaisesRegex(RuntimeError, "process exit unconfirmed"):
            close_decoder(decoder, None)
        self.assertEqual(decoder._retired_sessions, [session])
        self.assertIs(session.proc, proc)
        self.assertFalse(proc.stdin.closed)
        proc.fail_reap = False
        close_decoder(decoder, None)
        self.assertTrue(decoder.cleanup_complete)

    def test_decoder_wait_retains_live_pipe_worker_beyond_bounded_stop(self):
        proc = Process()
        decoder, session = installed_decoder(proc)
        release = threading.Event()
        reader = threading.Thread(target=lambda: release.wait(3))
        session.threads = [reader]
        reader.start()
        decoder.stop()
        self.assertFalse(decoder.cleanup_complete)
        self.assertEqual(decoder._retired_sessions, [session])
        self.assertFalse(proc.stdout.closed)
        results = []
        waiter = threading.Thread(target=lambda: results.append(decoder.wait_closed()))
        waiter.start()
        try:
            waiter.join(.05)
            self.assertTrue(waiter.is_alive())
            self.assertFalse(proc.stdout.closed)
        finally:
            release.set()
            reader.join(1)
            waiter.join(1)
        self.assertEqual(results, [True])
        self.assertTrue(proc.stdout.closed)
        self.assertTrue(decoder.cleanup_complete)

    def test_canceled_decoder_spawn_retains_unpublished_process_on_pipe_failure(self):
        proc = Process()
        proc.stderr.fail_close = True
        decoder = ContinuousMjpegDecoder()
        entered, release = threading.Event(), threading.Event()
        def spawn(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test spawn not released")
            return proc
        with patch("rm_client.backend.video_backend.resolve_ffmpeg_executable", return_value="ffmpeg"), patch(
                "rm_client.backend.video_backend.subprocess.Popen", side_effect=spawn), patch.dict(
                sys.modules, {"cv2": SimpleNamespace(), "numpy": SimpleNamespace()}):
            starter = threading.Thread(target=decoder.start)
            starter.start()
            self.assertTrue(entered.wait(1))
            decoder.stop()
            release.set()
            starter.join(1)
        self.assertFalse(decoder.cleanup_complete)
        self.assertIs(decoder._retired_sessions[0].proc, proc)
        proc.stderr.fail_close = False
        self.assertTrue(decoder.wait_closed())

    def test_udp_adapter_includes_nested_decoder_close_confirmation(self):
        video = RmVideoBackend()
        proc = Process()
        proc.stdin.fail_close = True
        video.h265_decoder, session = installed_decoder(proc)
        with self.assertRaisesRegex(RuntimeError, "pipe close failed"):
            close_video(video)
        self.assertEqual(video.h265_decoder._retired_sessions, [session])
        proc.stdin.fail_close = False
        close_video(video)
        self.assertTrue(video.h265_decoder.cleanup_complete)

    def test_udp_worker_finalizer_retains_failed_socket_until_explicit_retry(self):
        receiver, sock = UDPVideoReceiver(), Socket()
        with patch("rm_client.transports.socket.socket", return_value=sock) as opener:
            receiver.start()
            receiver._thread.join(1)
            self.assertFalse(receiver.cleanup_complete)
            self.assertIs(receiver._session.sock, sock)
            self.assertIs(receiver._socket, sock)
            receiver.start()
            self.assertEqual(opener.call_count, 1)
        self.assertFalse(receiver.wait_closed())
        sock.fail_close = False
        self.assertTrue(receiver.wait_closed())
        self.assertTrue(sock.closed)
        self.assertIsNone(receiver._session)

    def test_actual_video_close_adapter_reports_retained_socket(self):
        video, sock = RmVideoBackend(), Socket()
        video._session = _UDPVideoSession(sock=sock)
        video._socket = sock
        with self.assertRaisesRegex(RuntimeError, "close unconfirmed"):
            close_video(video)
        self.assertIs(video._session.sock, sock)
        self.assertFalse(video.cleanup_complete)
        sock.fail_close = False
        close_video(video)
        self.assertTrue(video.cleanup_complete)

    def test_udp_partial_open_retains_socket_when_late_close_fails(self):
        receiver, sock = UDPVideoReceiver(), Socket()
        entered, release = threading.Event(), threading.Event()
        def open_socket(*args):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test open not released")
            return sock
        with patch("rm_client.transports.socket.socket", side_effect=open_socket):
            receiver.start()
            worker = receiver._thread
            self.assertTrue(entered.wait(1))
            receiver.stop()
            release.set()
            worker.join(1)
        self.assertIs(receiver._session.sock, sock)
        self.assertFalse(receiver.cleanup_complete)
        sock.fail_close = False
        self.assertTrue(receiver.wait_closed())


if __name__ == "__main__":
    unittest.main()
