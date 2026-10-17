"""Live data readers may need an explicitly admitted closer to make progress."""
import io
import os
import threading
import unittest
from unittest.mock import patch

from rm_client.backend.video_source import VideoSourceAdmissionError
from tests import test_backend_video_source_nonblocking as source_fixtures
from tests.test_udp_stop_nonblocking import Socket


class PipeHeldProcess:
    """Real os.read waits for EOF; only process termination closes writers."""

    def __init__(self):
        self.stdin = io.BytesIO()
        out_read, self.out_write = os.pipe()
        err_read, self.err_write = os.pipe()
        self._out_fd, self._err_fd = out_read, err_read
        self.stdout = os.fdopen(out_read, 'rb', buffering=0)
        self.stderr = os.fdopen(err_read, 'rb', buffering=0)
        self.reader_entered = threading.Event()
        self.stderr_entered = threading.Event()
        self.terminate_entered = threading.Event()
        self.terminate_release = threading.Event()
        self.terminated = threading.Event()
        self.terminate_calls = 0
        self._os_read = os.read

    def read(self, fd, size):
        if fd == self._out_fd:
            self.reader_entered.set()
        if fd == self._err_fd:
            self.stderr_entered.set()
        return self._os_read(fd, size)

    def poll(self):
        return 0 if self.terminated.is_set() else None

    def terminate(self):
        self.terminate_calls += 1
        self.terminate_entered.set()
        if not self.terminate_release.wait(2):
            raise TimeoutError('test termination was not released')
        for attr in ('out_write', 'err_write'):
            descriptor = getattr(self, attr)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, attr, None)
        self.terminated.set()

    kill = terminate

    def wait(self, timeout=None):
        if not self.terminated.wait(timeout):
            raise TimeoutError('process still alive')
        return 0

    def release(self):
        self.terminate_release.set()


class VideoSourceRetryAdmissionTests(unittest.TestCase):
    setUp = source_fixtures.BackendVideoSourceTests.setUp
    settle = source_fixtures.BackendVideoSourceTests.settle

    def test_live_decoder_reader_needs_retry_after_closer_admission_was_rejected(self):
        for source in ('native', 'hevc'):
            with self.subTest(source=source):
                self.check_decoder_retry(source)
        self.backend.request_shutdown()
        self.assertTrue(self.settle().may_exit)

    def check_decoder_retry(self, source):
        decoder = self.backend.native_decoder if source == 'native' else self.backend.udp.h265_decoder
        self.backend.set_active_video_source('Native' if source == 'native' else 'UDP', start=False)
        resource = 'Native 0x0310' if source == 'native' else 'UDP/HEVC'
        proc = PipeHeldProcess()
        self.addCleanup(proc.release)
        with patch('rm_client.backend.video_backend.subprocess.Popen', return_value=proc), \
             patch('rm_client.backend.video_backend.resolve_ffmpeg_executable', return_value='ffmpeg'), \
             patch('rm_client.backend.video_backend.os.read', side_effect=proc.read):
            try:
                self.assertTrue(decoder.start())
                self.assertTrue(proc.reader_entered.wait(1))
                self.assertTrue(proc.stderr_entered.wait(1))
                session = decoder._session
                with patch('threading.Thread.start', side_effect=RuntimeError('closer rejected')):
                    with self.assertRaises(VideoSourceAdmissionError):
                        self.backend.set_active_video_source('Synthetic')
                self.assertIsNone(decoder._stop_worker)
                self.assertFalse(session.close_lock.locked())
                self.assertTrue(any(worker.is_alive() for worker in session.threads))
                self.assertFalse(proc.terminate_entered.is_set())
                failed = self.backend.video_source_snapshot
                self.assertIn(resource, failed.pending_resources)
                self.assertEqual(failed.inflight_resources, ())
                self.assertTrue(failed.retryable)
                for _ in range(25):
                    with self.assertRaises(VideoSourceAdmissionError):
                        self.backend.set_active_video_source('Synthetic')
                self.assertIsNone(decoder._stop_worker)
                self.backend.retry_active_video_source(expected_generation=failed.generation)
                self.assertTrue(proc.terminate_entered.wait(1))
                closer = decoder._stop_worker
                busy = self.backend.video_source_snapshot
                self.assertIn(resource, busy.inflight_resources)
                for _ in range(25):
                    with self.assertRaisesRegex(RuntimeError, 'still in flight'):
                        self.backend.retry_active_video_source(expected_generation=busy.generation)
                self.assertIs(decoder._stop_worker, closer)
                self.assertEqual(proc.terminate_calls, 1)
                proc.terminate_release.set()
                self.assertTrue(decoder.wait_closed())
                self.assertTrue(all(not worker.is_alive() for worker in session.threads))
                self.assertTrue(proc.stdin.closed and proc.stdout.closed and proc.stderr.closed)
            finally:
                proc.release()
                decoder.stop()
                decoder.wait_closed()

    def test_live_udp_reader_needs_retry_after_closer_admission_was_rejected(self):
        sock = Socket()
        self.addCleanup(sock.close_release.set)
        self.addCleanup(sock.read_release.set)
        with patch('rm_client.transports.socket.socket', return_value=sock):
            try:
                self.backend.set_active_video_source('UDP')
                self.assertTrue(sock.read_entered.wait(1))
                receiver = self.backend.udp._thread
                with patch('threading.Thread.start', side_effect=RuntimeError('closer rejected')):
                    with self.assertRaises(VideoSourceAdmissionError):
                        self.backend.set_active_video_source('Synthetic')
                self.assertIsNone(self.backend.udp._stop_worker)
                self.assertTrue(receiver.is_alive())
                failed = self.backend.video_source_snapshot
                self.assertIn('UDP/HEVC', failed.pending_resources)
                self.assertEqual(failed.inflight_resources, ())
                self.assertTrue(failed.retryable)
                sock.close_release.clear()
                self.backend.retry_active_video_source(expected_generation=failed.generation)
                self.assertTrue(sock.close_entered.wait(1))
                closer = self.backend.udp._stop_worker
                busy = self.backend.video_source_snapshot
                self.assertIn('UDP/HEVC', busy.inflight_resources)
                with self.assertRaisesRegex(RuntimeError, 'still in flight'):
                    self.backend.retry_active_video_source(expected_generation=busy.generation)
                self.assertIs(self.backend.udp._stop_worker, closer)
                sock.close_release.set()
                self.assertTrue(self.backend.udp.wait_closed())
                self.assertFalse(receiver.is_alive())
                self.assertFalse(sock.is_open)
            finally:
                sock.close_release.set()
                sock.read_release.set()
                self.backend.udp.stop()
                self.backend.udp.wait_closed()
        self.backend.request_shutdown()
        self.assertTrue(self.settle().may_exit)


if __name__ == '__main__':
    unittest.main()
