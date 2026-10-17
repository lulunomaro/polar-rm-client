"""Nonblocking video stop retains every already-admitted native owner."""
from queue import Queue
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.backend.lifecycle import close_decoder
from rm_client.backend.video_backend import ContinuousMjpegDecoder, _DecoderSession
from tests.test_video_close_ownership import Process


class DecoderStopRequestTests(unittest.TestCase):
    def decoder(self, proc):
        decoder = ContinuousMjpegDecoder()
        session = _DecoderSession(proc, Queue())
        decoder._session, decoder._proc = session, proc
        decoder._running = decoder.available = True
        return decoder, session

    def blocking_process(self):
        proc = Process()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        close_threads = []
        wait = proc.wait
        def held_wait(timeout=None):
            close_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test process wait not released')
            return wait(timeout)
        proc.wait = held_wait
        return proc, entered, release, close_threads

    def test_request_revokes_immediately_and_keeps_one_background_close(self):
        proc, entered, release, threads = self.blocking_process()
        decoder, session = self.decoder(proc)
        before = time.perf_counter()
        decoder.request_stop()
        self.assertLess(time.perf_counter() - before, .1)
        self.assertTrue(entered.wait(1))
        first = decoder._stop_worker
        self.assertFalse(decoder.available)
        self.assertIsNone(decoder._session)
        self.assertIn(session, decoder._retired_sessions)
        for _ in range(100):
            decoder.request_stop()
        self.assertIs(decoder._stop_worker, first)
        self.assertFalse(decoder.cleanup_complete)
        with patch('rm_client.backend.video_backend.subprocess.Popen') as spawn:
            self.assertFalse(decoder.start())
            self.assertFalse(decoder.restart())
            spawn.assert_not_called()
        release.set()
        self.assertTrue(decoder.wait_closed())
        self.assertTrue(decoder.cleanup_complete)
        self.assertTrue(all(thread != threading.get_ident() for thread in threads))

    def test_strict_final_close_waits_for_already_admitted_stop_worker(self):
        proc, entered, release, _ = self.blocking_process()
        decoder, _ = self.decoder(proc)
        decoder.request_stop()
        self.assertTrue(entered.wait(1))
        done = threading.Event()
        errors = []
        def strict_close():
            try:
                close_decoder(decoder, None)
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        finalizer = threading.Thread(target=strict_close)
        finalizer.start()
        self.assertFalse(done.wait(.03))
        release.set()
        finalizer.join(1)
        self.assertFalse(finalizer.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(decoder.cleanup_complete)
        self.assertFalse(decoder._stop_worker.is_alive())

    def test_failed_close_retains_owner_and_explicit_stop_request_retries(self):
        proc = Process()
        proc.stdout.fail_close = True
        decoder, session = self.decoder(proc)
        decoder.request_stop()
        decoder._stop_worker.join(1)
        self.assertIn(session, decoder._retired_sessions)
        self.assertFalse(decoder.cleanup_complete)
        self.assertIn('pipe close failed', decoder.last_error)
        with patch('rm_client.backend.video_backend.subprocess.Popen') as spawn:
            self.assertFalse(decoder.start())
            spawn.assert_not_called()
        proc.stdout.fail_close = False
        decoder.request_stop()
        self.assertTrue(decoder.wait_closed())
        self.assertEqual(decoder._retired_sessions, [])

    def test_empty_stop_invalidates_epoch_without_admitting_an_unneeded_worker(self):
        decoder = ContinuousMjpegDecoder()
        generation = decoder.lifecycle_generation
        with patch('threading.Thread.start') as start:
            decoder.request_stop()
        start.assert_not_called()
        self.assertGreater(decoder.lifecycle_generation, generation)
        self.assertTrue(decoder.cleanup_complete)
        self.assertIsNone(decoder._stop_worker)

    def test_stop_worker_admission_failure_keeps_native_owner_for_strict_close(self):
        proc = Process()
        decoder, session = self.decoder(proc)
        with patch('threading.Thread.start', side_effect=RuntimeError('worker admission failed')):
            with self.assertRaisesRegex(RuntimeError, 'worker admission failed'):
                decoder.request_stop()
        self.assertFalse(decoder.available)
        self.assertIn(session, decoder._retired_sessions)
        self.assertFalse(decoder.cleanup_complete)
        self.assertIsNone(decoder._stop_worker)
        self.assertTrue(decoder.wait_closed())
        self.assertFalse(proc.alive)

    def test_failed_stop_admission_still_reports_unpublished_spawn_pending(self):
        proc = Process()
        decoder = ContinuousMjpegDecoder()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def spawn(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test spawn not released')
            return proc
        starts = []
        with patch('rm_client.backend.video_backend.subprocess.Popen', side_effect=spawn), \
             patch('rm_client.backend.video_backend.resolve_ffmpeg_executable', return_value='ffmpeg'):
            starter = threading.Thread(target=lambda: starts.append(decoder.start()))
            starter.start()
            self.assertTrue(entered.wait(1))
            with patch('threading.Thread.start', side_effect=RuntimeError('not admitted')):
                with self.assertRaises(RuntimeError):
                    decoder.request_stop()
            self.assertEqual(decoder._retired_sessions, [])
            self.assertIsNone(decoder._stop_worker)
            self.assertTrue(decoder.stop_pending)
            release.set()
            starter.join(1)
            self.assertFalse(starter.is_alive())
            self.assertEqual(starts, [False])
            self.assertTrue(decoder.wait_closed())
        self.assertFalse(proc.alive)
        self.assertFalse(decoder.stop_pending)

    def test_unpublished_spawn_is_invalidated_and_included_in_strict_wait(self):
        proc = Process()
        decoder = ContinuousMjpegDecoder()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def spawn(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test spawn not released')
            return proc
        starts = []
        with patch('rm_client.backend.video_backend.subprocess.Popen', side_effect=spawn), \
             patch('rm_client.backend.video_backend.resolve_ffmpeg_executable', return_value='ffmpeg'):
            starter = threading.Thread(target=lambda: starts.append(decoder.start()))
            starter.start()
            self.assertTrue(entered.wait(1))
            decoder.request_stop()
            self.assertFalse(decoder.cleanup_complete)
            self.assertTrue(decoder._stop_worker.is_alive())
            release.set()
            starter.join(1)
            self.assertEqual(starts, [False])
            self.assertTrue(decoder.wait_closed())
        self.assertFalse(proc.alive)
        self.assertTrue(proc.stdin.closed and proc.stdout.closed and proc.stderr.closed)
        self.assertIsNone(decoder._session)


if __name__ == '__main__':
    unittest.main()
