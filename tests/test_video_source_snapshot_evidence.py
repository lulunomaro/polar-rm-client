"""Independent current-owner snapshot checks across busy and failed epochs."""
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.backend.video_source import VideoSourceAdmissionError
from tests import test_backend_video_source_nonblocking as source_fixtures
from tests.test_udp_stop_nonblocking import Socket


class VideoSourceOwnerEvidenceTests(unittest.TestCase):
    setUp = source_fixtures.BackendVideoSourceTests.setUp
    install_three_owners = source_fixtures.BackendVideoSourceTests.install_three_owners

    def join_closers(self):
        for worker in (self.backend.udp._stop_worker,
                       self.backend.udp.h265_decoder._stop_worker,
                       self.backend.native_decoder._stop_worker):
            if worker is not None:
                worker.join(1)
                self.assertFalse(worker.is_alive())

    def test_retry_keeps_current_errors_visible_until_native_release_is_confirmed(self):
        native, hevc, sock = self.install_three_owners()
        def repair():
            native.proc.stdout.fail_close = hevc.proc.stderr.fail_close = sock.fail_close = False
            sock.close_release.set()
        self.addCleanup(repair)
        native.proc.stdout.fail_close = hevc.proc.stderr.fail_close = sock.fail_close = True
        with patch('rm_client.transports.socket.socket', side_effect=AssertionError('real socket')), \
             patch('rm_client.backend.video_backend.subprocess.Popen', side_effect=AssertionError('real process')):
            self.backend.set_active_video_source('Synthetic')
            self.join_closers()
            failed = self.backend.video_source_snapshot
            self.assertTrue(failed.retryable)
            self.assertEqual(len(failed.close_failures), 3)
            # Diagnostics cannot replace or invent current owner evidence.
            self.backend.native_decoder.last_error = 'unrelated old native diagnostic'
            self.backend.udp.h265_decoder.last_error = 'unrelated old HEVC diagnostic'
            self.backend.udp.last_error = 'unrelated old UDP diagnostic'
            self.assertEqual(self.backend.video_source_snapshot.close_failures, failed.close_failures)
            sock.close_entered.clear()
            sock.close_release.clear()
            self.backend.retry_active_video_source(expected_generation=failed.generation)
            self.assertTrue(sock.close_entered.wait(1))
            busy = self.backend.video_source_snapshot
            self.assertGreater(busy.generation, failed.generation)
            self.assertIn('UDP/HEVC', busy.inflight_resources)
            self.assertTrue(any('socket refused close' in f.error for f in busy.close_failures))
            self.assertFalse(busy.retryable)
            owners = (self.backend.udp._stop_worker, self.backend.udp.h265_decoder._stop_worker,
                      self.backend.native_decoder._stop_worker)
            for _ in range(25):
                before = time.perf_counter()
                with self.assertRaisesRegex(RuntimeError, 'still in flight'):
                    self.backend.retry_active_video_source(expected_generation=busy.generation)
                self.assertLess(time.perf_counter() - before, .1)
            self.assertEqual(owners, (self.backend.udp._stop_worker,
                                     self.backend.udp.h265_decoder._stop_worker,
                                     self.backend.native_decoder._stop_worker))
            self.assertEqual(self.backend.video_source_snapshot.generation, busy.generation)
            repair()
            self.join_closers()
            # Native/HEVC may have already failed their second close before repair.
            if self.backend.video_source_snapshot.retryable:
                self.backend.retry_active_video_source(expected_generation=busy.generation)
                self.join_closers()
            self.assertTrue(self.backend.udp.wait_closed())
            self.assertTrue(self.backend.native_decoder.wait_closed())
            closed = self.backend.video_source_snapshot
            self.assertEqual(closed.pending_resources, ())
            self.assertEqual(closed.close_failures, ())
            self.assertFalse(closed.retryable)
            self.assertEqual(len(failed.close_failures), 3)  # immutable old evidence
            with self.assertRaisesRegex(RuntimeError, 'stale'):
                self.backend.retry_active_video_source(expected_generation=failed.generation)

    def test_live_retired_pipe_workers_can_admit_the_closer_needed_to_end_them(self):
        native, hevc, sock = self.install_three_owners()
        release = threading.Event()
        workers = []
        self.addCleanup(release.set)
        for name, session in (('native', native), ('hevc', hevc)):
            worker = threading.Thread(target=lambda: release.wait(2), name='review-' + name)
            session.threads = [worker]
            workers.append(worker)
            worker.start()
        try:
            with patch('threading.Thread.start', side_effect=RuntimeError('no closer admission')):
                with self.assertRaises(VideoSourceAdmissionError):
                    self.backend.set_active_video_source('Synthetic')
            self.assertIsNone(self.backend.native_decoder._stop_worker)
            self.assertIsNone(self.backend.udp.h265_decoder._stop_worker)
            before = time.perf_counter()
            pending = self.backend.video_source_snapshot
            self.assertLess(time.perf_counter() - before, .1)
            self.assertEqual(pending.pending_resources, ('UDP/HEVC', 'Native 0x0310'))
            self.assertEqual(pending.inflight_resources, ())
            self.assertEqual(pending.close_failures, ())
            self.assertTrue(pending.retryable)
            # Data workers need a closer; their existence is no evidence that
            # one was admitted. Only the new actual closer makes retry busy.
            self.backend.retry_active_video_source(expected_generation=pending.generation)
            busy = self.backend.video_source_snapshot
            self.assertEqual(busy.inflight_resources, ('UDP/HEVC', 'Native 0x0310'))
            self.assertFalse(busy.retryable)
            with self.assertRaisesRegex(RuntimeError, 'still in flight'):
                self.backend.retry_active_video_source(expected_generation=busy.generation)
        finally:
            release.set()
            for worker in workers:
                worker.join(1)
        self.join_closers()
        self.assertFalse(sock.is_open)
        self.assertTrue(self.backend.native_decoder.cleanup_complete)
        self.assertTrue(self.backend.udp.cleanup_complete)

    def test_late_receiver_can_admit_closer_then_reports_its_retained_socket(self):
        sock = Socket()
        sock.fail_close = True
        entered, release = threading.Event(), threading.Event()
        def repair():
            sock.fail_close = False
            release.set()
            sock.read_release.set()
            sock.close_release.set()
        self.addCleanup(repair)
        def open_socket(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test socket release missing')
            return sock
        with patch('rm_client.transports.socket.socket', side_effect=open_socket):
            self.backend.set_active_video_source('UDP')
            self.assertTrue(entered.wait(1))
            receiver = self.backend.udp._thread
            with patch('threading.Thread.start', side_effect=RuntimeError('no closer admission')):
                with self.assertRaises(VideoSourceAdmissionError):
                    self.backend.set_active_video_source('Synthetic')
            self.assertIsNone(self.backend.udp._stop_worker)
            self.assertIsNone(self.backend.udp._session.sock)
            pending = self.backend.video_source_snapshot
            self.assertIn('UDP/HEVC', pending.pending_resources)
            self.assertEqual(pending.inflight_resources, ())
            self.assertTrue(pending.retryable)
            self.backend.retry_active_video_source(expected_generation=pending.generation)
            busy = self.backend.video_source_snapshot
            self.assertIn('UDP/HEVC', busy.inflight_resources)
            self.assertFalse(busy.retryable)
            with self.assertRaisesRegex(RuntimeError, 'still in flight'):
                self.backend.retry_active_video_source(expected_generation=busy.generation)
            release.set()
            receiver.join(1)
            self.backend.udp._stop_worker.join(1)
            self.assertFalse(receiver.is_alive())
            self.assertFalse(self.backend.udp._stop_worker.is_alive())
            self.assertIs(self.backend.udp._session.sock, sock)
            failed = self.backend.video_source_snapshot
            self.assertEqual(failed.generation, busy.generation)
            self.assertEqual(failed.inflight_resources, ())
            self.assertTrue(failed.retryable)
            self.assertEqual(len(failed.close_failures), 1)
            self.assertIn('socket refused close', failed.close_failures[0].error)
            repair()
            self.backend.retry_active_video_source(expected_generation=failed.generation)
            self.assertTrue(self.backend.udp.wait_closed())
        self.assertEqual(self.backend.video_source_snapshot.close_failures, ())
        self.assertFalse(self.backend.video_source_snapshot.retryable)


if __name__ == '__main__':
    unittest.main()
