"""The retry UI reads current native ownership, not historical diagnostics."""
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.backend.video_backend import ContinuousMjpegDecoder
from rm_client.backend.video_source import VideoSourceAdmissionError
from tests import test_backend_video_source_nonblocking as source_fixtures
from tests.test_video_close_ownership import Process, installed_decoder


class VideoSourceSnapshotTests(unittest.TestCase):
    setUp = source_fixtures.BackendVideoSourceTests.setUp
    held_process = source_fixtures.BackendVideoSourceTests.held_process
    install_three_owners = source_fixtures.BackendVideoSourceTests.install_three_owners

    def test_actual_blocked_closers_are_busy_then_failed_owners_become_retryable(self):
        native, hevc, sock = self.install_three_owners()
        sock.fail_close = True
        sock.close_release.clear()
        self.addCleanup(sock.close_release.set)
        native.proc.stdout.fail_close = True
        hevc.proc.stderr.fail_close = True
        try:
            self.backend.set_active_video_source('Synthetic')
            self.assertTrue(sock.close_entered.wait(1))
            before = time.perf_counter()
            busy = self.backend.video_source_snapshot
            self.assertLess(time.perf_counter() - before, .1)
            self.assertIn('UDP/HEVC', busy.inflight_resources)
            self.assertFalse(busy.retryable)
            with self.assertRaisesRegex(RuntimeError, 'still in flight'):
                self.backend.retry_active_video_source(expected_generation=busy.generation)
            sock.close_release.set()
            self.backend.udp._stop_worker.join(1)
            self.backend.udp.h265_decoder._stop_worker.join(1)
            self.backend.native_decoder._stop_worker.join(1)
            failed = self.backend.video_source_snapshot
            self.assertEqual(failed.generation, busy.generation)
            self.assertEqual(failed.inflight_resources, ())
            self.assertTrue(failed.retryable)
            self.assertEqual(failed.failures, ())
            self.assertEqual(len(failed.close_failures), 3)
            self.assertTrue(any('socket refused close' in f.error for f in failed.close_failures))
            self.assertTrue(any('HEVC:' in f.error for f in failed.close_failures))
            # Neither polling nor repeated accepted-target refresh gets a retry.
            owners = (self.backend.udp._stop_worker, self.backend.udp.h265_decoder._stop_worker,
                      self.backend.native_decoder._stop_worker)
            for _ in range(25):
                self.backend.set_active_video_source('Synthetic')
                self.assertEqual(self.backend.video_source_snapshot.generation, failed.generation)
            self.assertEqual(owners, (self.backend.udp._stop_worker,
                                     self.backend.udp.h265_decoder._stop_worker,
                                     self.backend.native_decoder._stop_worker))
        finally:
            sock.close_release.set()
            sock.fail_close = native.proc.stdout.fail_close = hevc.proc.stderr.fail_close = False
        self.backend.retry_active_video_source(expected_generation=failed.generation)
        self.assertTrue(self.backend.udp.wait_closed())
        self.assertTrue(self.backend.native_decoder.wait_closed())
        closed = self.backend.video_source_snapshot
        self.assertGreater(closed.generation, failed.generation)
        self.assertEqual(closed.pending_resources, ())
        self.assertEqual(closed.close_failures, ())
        self.assertFalse(closed.retryable)
        # Old diagnostic text remains available but is not native ownership.
        self.assertTrue(self.backend.native_decoder.last_error)

    def test_same_target_refresh_does_not_retry_ended_native_close_for_udp_target(self):
        self.backend.native_decoder, native = installed_decoder(Process())
        native.proc.stdout.fail_close = True
        try:
            self.backend.set_active_video_source('UDP', start=False)
            self.backend.native_decoder._stop_worker.join(1)
            first = self.backend.native_decoder._stop_worker
            generation = self.backend.video_source_snapshot.generation
            for _ in range(25):
                self.backend.set_active_video_source('UDP', start=False)
            self.assertIs(self.backend.native_decoder._stop_worker, first)
            snapshot = self.backend.video_source_snapshot
            self.assertEqual(snapshot.generation, generation)
            self.assertTrue(snapshot.retryable)
        finally:
            native.proc.stdout.fail_close = False
        self.backend.retry_active_video_source(expected_generation=generation)
        self.assertTrue(self.backend.native_decoder.wait_closed())

    def test_request_epoch_rejects_a_to_b_to_a_stale_click_before_any_admission(self):
        self.backend.set_active_video_source('Synthetic')
        first = self.backend.video_source_snapshot.generation
        self.backend.set_active_video_source('Native')
        second = self.backend.video_source_snapshot.generation
        self.backend.set_active_video_source('Synthetic')
        third = self.backend.video_source_snapshot.generation
        self.assertLess(first, second)
        self.assertLess(second, third)
        with patch.object(self.backend.udp, 'request_stop') as udp, patch.object(
                self.backend.native_decoder, 'request_stop') as native:
            with self.assertRaisesRegex(RuntimeError, 'stale'):
                self.backend.retry_active_video_source(expected_generation=first)
        udp.assert_not_called()
        native.assert_not_called()
        self.assertEqual(self.backend.video_source_snapshot.generation, third)

    def test_failed_admission_has_retry_budget_but_regular_refresh_cannot_spend_it(self):
        self.install_three_owners()
        with patch('threading.Thread.start', side_effect=RuntimeError('not admitted')) as start:
            with self.assertRaises(VideoSourceAdmissionError):
                self.backend.set_active_video_source('Synthetic')
            failed = self.backend.video_source_snapshot
            self.assertEqual(failed.inflight_resources, ())
            self.assertTrue(failed.retryable)
            self.assertEqual(failed.close_failures, ())
            for _ in range(10):
                with self.assertRaises(VideoSourceAdmissionError):
                    self.backend.set_active_video_source('Synthetic')
                self.assertEqual(self.backend.video_source_snapshot.generation, failed.generation)
            self.assertEqual(start.call_count, 3)
        self.backend.retry_active_video_source(expected_generation=failed.generation)
        self.assertTrue(self.backend.native_decoder.wait_closed())
        self.assertTrue(self.backend.udp.wait_closed())

    def test_unpublished_popen_with_failed_stop_admission_is_inflight(self):
        proc, decoder = Process(), ContinuousMjpegDecoder()
        self.backend.native_decoder = decoder
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def spawn(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test spawn not released')
            return proc
        with patch('rm_client.backend.video_backend.subprocess.Popen', side_effect=spawn), \
             patch('rm_client.backend.video_backend.resolve_ffmpeg_executable', return_value='ffmpeg'):
            starter = threading.Thread(target=decoder.start)
            starter.start()
            try:
                self.assertTrue(entered.wait(1))
                self.backend._active_video_source = 'native 0x0310'
                with patch('threading.Thread.start', side_effect=RuntimeError('not admitted')):
                    with self.assertRaises(VideoSourceAdmissionError):
                        self.backend.set_active_video_source('Synthetic')
                self.assertEqual(decoder._retired_sessions, [])
                snapshot = self.backend.video_source_snapshot
                self.assertIn('Native 0x0310', snapshot.inflight_resources)
                self.assertFalse(snapshot.retryable)
            finally:
                release.set()
                starter.join(1)
            self.assertTrue(decoder.wait_closed())


if __name__ == '__main__':
    unittest.main()
