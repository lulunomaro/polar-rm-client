"""Recovery admission evidence distinguishes a never-started thread from an owner."""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.backend.manager import BackendManager
from tests.test_video_close_ownership import Process, installed_decoder


class VideoRecoveryAdmissionTests(unittest.TestCase):
    def backend(self, source):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root.name}), patch.object(
                BackendManager, '_build_commit', return_value='TEST'):
            manager = BackendManager(workspace=root.name)
        self.addCleanup(manager.shutdown)
        manager.native_decoder.wait_closed()
        manager.udp.wait_closed()
        proc = Process()
        decoder, session = installed_decoder(proc)
        decoder.started_at = time.perf_counter() - 10
        if source == 'native':
            manager.native_decoder = decoder
            manager._active_video_source = 'native 0x0310'
        else:
            manager.udp.h265_decoder = decoder
            manager.udp.running = manager.udp.decode_h265 = True
            manager._active_video_source = 'udp hevc'
        return manager, decoder, proc

    def recovery(self, manager, source):
        return (manager._native_restart_thread if source == 'native'
                else manager.udp._h265_restart_thread)

    def settle(self, manager):
        deadline = time.monotonic() + 2
        result = None
        while manager.lifecycle_pending:
            completion = manager.poll_lifecycle()
            if completion is not None:
                result = completion
            if time.monotonic() > deadline:
                self.fail('recovery admission stranded lifecycle completion')
            time.sleep(.001)
        return result

    def test_never_admitted_recovery_has_no_join_owner_and_close_can_finish(self):
        for source in ('native', 'hevc'):
            with self.subTest(source=source):
                manager, decoder, _ = self.backend(source)
                with patch('threading.Thread.start', side_effect=RuntimeError('not admitted')) as start:
                    with self.assertRaisesRegex(RuntimeError, 'not admitted'):
                        manager._recover_video_decoders()
                    self.assertIsNone(self.recovery(manager, source))
                    # Admission failure uses backoff; polling cannot spin on it.
                    for _ in range(25):
                        manager._recover_video_decoders()
                    self.assertEqual(start.call_count, 1)
                error = decoder.last_error if source == 'native' else manager.udp.last_error
                self.assertIn('RuntimeError: not admitted', error)
                manager.request_shutdown()
                result = self.settle(manager)
                self.assertTrue(result.may_exit, result.failures)
                self.assertTrue(result.succeeded, result.failures)

    def test_failed_native_close_can_retry_after_never_admitted_recovery(self):
        for source in ('native', 'hevc'):
            with self.subTest(source=source):
                manager, decoder, proc = self.backend(source)
                with patch('threading.Thread.start', side_effect=RuntimeError('not admitted')):
                    with self.assertRaises(RuntimeError):
                        manager._recover_video_decoders()
                proc.stdout.fail_close = True
                manager.request_shutdown()
                first = self.settle(manager)
                self.assertFalse(first.may_exit)
                self.assertFalse(decoder.cleanup_complete)
                self.assertNotIn('cannot join', repr(first.failures))
                proc.stdout.fail_close = False
                manager.retry_shutdown()
                second = self.settle(manager)
                self.assertTrue(second.may_exit, second.failures)
                self.assertTrue(decoder.cleanup_complete)
                self.assertFalse(first.may_exit)

    def test_started_then_raised_recovery_remains_owned_until_strict_close(self):
        original_start = threading.Thread.start
        for source in ('native', 'hevc'):
            with self.subTest(source=source):
                manager, decoder, _ = self.backend(source)
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                close = decoder._close_session
                def held_close(session, **kwargs):
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError('test close not released')
                    return close(session, **kwargs)
                def admitted_then_raise(worker):
                    original_start(worker)
                    self.assertTrue(entered.wait(1))
                    raise RuntimeError('raised after admission')
                with patch.object(decoder, '_close_session', side_effect=held_close):
                    with patch('threading.Thread.start', admitted_then_raise):
                        with self.assertRaisesRegex(RuntimeError, 'raised after admission'):
                            manager._recover_video_decoders()
                    worker = self.recovery(manager, source)
                    self.assertIsNotNone(worker)
                    self.assertIsNotNone(worker.ident)
                    self.assertTrue(worker.is_alive())
                    manager.request_shutdown()
                    self.assertIsNone(manager.poll_lifecycle())
                    self.assertTrue(manager.lifecycle_pending)
                    self.assertIsNone(manager.shutdown_completion)
                    release.set()
                    result = self.settle(manager)
                self.assertTrue(result.may_exit, result.failures)
                self.assertFalse(worker.is_alive())

    def test_completed_then_raised_recovery_is_still_a_joinable_owner(self):
        original_start = threading.Thread.start
        for source in ('native', 'hevc'):
            with self.subTest(source=source):
                manager, decoder, _ = self.backend(source)
                def completed_then_raise(worker):
                    original_start(worker)
                    worker.join(1)
                    self.assertFalse(worker.is_alive())
                    raise RuntimeError('raised after completion')
                with patch.object(decoder, 'restart', return_value=False), \
                     patch('threading.Thread.start', completed_then_raise):
                    with self.assertRaisesRegex(RuntimeError, 'raised after completion'):
                        manager._recover_video_decoders()
                worker = self.recovery(manager, source)
                self.assertIsNotNone(worker)
                self.assertIsNotNone(worker.ident)
                self.assertFalse(worker.is_alive())
                manager.request_shutdown()
                result = self.settle(manager)
                self.assertTrue(result.may_exit, result.failures)


if __name__ == '__main__':
    unittest.main()
