"""A late-raising start wrapper cannot publish a second resource result."""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.lifecycle import LifecycleCoordinator, TeardownTask
from rm_client.backend.manager import BackendManager
from rm_client.backend.lifecycle import close_decoder
from tests.test_video_close_ownership import Process, installed_decoder


class CoordinatorLateAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.start_thread = threading.Thread.start

    def start_then_raise(self, worker):
        self.start_thread(worker)
        raise RuntimeError('wrapper raised after admission')

    def test_live_owner_has_one_result_and_cannot_be_retried(self):
        coordinator = LifecycleCoordinator(max_resources=1)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def close():
            calls.append(True)
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test owner not released')
        with patch('threading.Thread.start', lambda worker: self.start_then_raise(worker)):
            coordinator.request('final', [TeardownTask('driver', close)], final=True)
        self.assertTrue(entered.wait(1))
        self.assertIsNone(coordinator.poll())
        self.assertEqual(coordinator.snapshot().pending_resources, ('driver',))
        self.assertEqual(coordinator.snapshot().failures, ())
        self.assertEqual(len(coordinator.snapshot().warnings), 1)
        with self.assertRaises(RuntimeError):
            coordinator.retry_failed()
        release.set()
        result = coordinator.wait(1)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.failures, ())
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(calls, [True])
        self.assertTrue(coordinator._mailbox.empty())
        with self.assertRaises(ValueError):
            coordinator.retry_failed()

    def test_finished_then_raised_worker_does_not_overfill_one_result_mailbox(self):
        coordinator = LifecycleCoordinator(max_resources=1)
        def completed_then_raise(worker):
            self.start_thread(worker)
            worker.join(1)
            self.assertFalse(worker.is_alive())
            raise RuntimeError('wrapper raised after completion')
        with patch('threading.Thread.start', completed_then_raise):
            coordinator.request('final', [TeardownTask('driver', lambda: None)], final=True)
        result = coordinator.wait(1)
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.warnings), 1)
        self.assertTrue(coordinator._mailbox.empty())

    def test_real_close_failure_can_retry_only_after_its_result_and_preserves_warning(self):
        coordinator = LifecycleCoordinator()
        fail = [True]
        def close():
            if fail[0]:
                raise OSError('native resource still open')
        with patch('threading.Thread.start', lambda worker: self.start_then_raise(worker)):
            coordinator.request('final', [TeardownTask('driver', close)], final=True)
        first = coordinator.wait(1)
        self.assertEqual([failure.resource for failure in first.failures], ['driver'])
        fail[0] = False
        coordinator.retry_failed()
        second = coordinator.wait(1)
        self.assertTrue(second.succeeded)
        self.assertEqual(second.warnings, first.warnings)
        self.assertFalse(first.succeeded)

    def test_actual_manager_waits_for_native_owner_then_exits_with_visible_warning(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root.name}), patch.object(
                BackendManager, '_build_commit', return_value='TEST'):
            manager = BackendManager(workspace=root.name)
        self.addCleanup(manager.shutdown)
        proc = Process()
        manager.native_decoder, _ = installed_decoder(proc)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        wait = proc.wait
        def held_wait(timeout=None):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test process not released')
            return wait(timeout)
        proc.wait = held_wait
        def late_native_raise(worker):
            self.start_thread(worker)
            if worker.name == 'lifecycle-native-decoder':
                self.assertTrue(entered.wait(1))
                raise RuntimeError('native closer already admitted')
        with patch('rm_client.backend.teardown_plan.close_decoder', wraps=close_decoder) as calls:
            with patch('threading.Thread.start', late_native_raise):
                manager.request_shutdown()
            self.assertIsNone(manager.poll_lifecycle())
            self.assertTrue(manager.lifecycle_pending)
            self.assertIsNone(manager.shutdown_completion)
            with self.assertRaises(RuntimeError):
                manager.retry_shutdown()
            self.assertEqual(calls.call_count, 1)
            release.set()
            deadline = time.monotonic() + 2
            while manager.lifecycle_pending:
                manager.poll_lifecycle()
                if time.monotonic() > deadline:
                    self.fail('native close failed to settle')
                time.sleep(.001)
            result = manager.shutdown_completion
            self.assertTrue(result.resources_released)
            self.assertTrue(result.audit.drained)
            self.assertTrue(result.may_exit)
            self.assertFalse(result.succeeded)
            self.assertEqual(result.failures, result.warnings)
            self.assertIn('native closer already admitted', result.warnings[0].error)
            with self.assertRaises(ValueError):
                manager.retry_shutdown()
            self.assertEqual(calls.call_count, 1)


if __name__ == '__main__':
    unittest.main()
