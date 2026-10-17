"""Installed Paho loop admission retains and closes both wakeup sockets."""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.backend.manager import BackendManager
from tests.test_mqtt_cleanup_ownership import Socket


class PahoLoopAdmissionTests(unittest.TestCase):
    def backend(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root.name}), patch.object(
                BackendManager, '_build_commit', return_value='TEST'):
            manager = BackendManager(workspace=root.name)
        self.addCleanup(manager.shutdown)
        manager.mqtt.mock_output = False
        return manager

    def settle(self, manager):
        result = None
        deadline = time.monotonic() + 2
        while manager.lifecycle_pending:
            completion = manager.poll_lifecycle()
            if completion is not None:
                result = completion
            if time.monotonic() > deadline:
                self.fail('Paho admission stranded teardown')
            time.sleep(.001)
        return result

    def reject_network_start(self, worker):
        if worker.name.startswith('paho-mqtt-client-'):
            raise RuntimeError('Paho loop not admitted')
        return self.start_thread(worker)

    def sockets(self, *, fail_first=False):
        pair = Socket(fail=fail_first), Socket()
        def release_fakes():
            for handle in pair:
                handle.fail = False
                if handle.fileno() != -1:
                    handle.close()
        self.addCleanup(release_fakes)
        return pair

    def setUp(self):
        self.start_thread = threading.Thread.start

    def test_failed_actual_loop_start_releases_both_wakeup_sockets_before_final_exit(self):
        manager = self.backend()
        pair = self.sockets()
        with patch('paho.mqtt.client._socketpair_compat', return_value=pair), \
             patch('threading.Thread.start', lambda worker: self.reject_network_start(worker)):
            manager.mqtt.start()
            self.assertTrue(manager.mqtt.wait_stopped(1), manager.mqtt.last_error)
        self.assertTrue(all(handle.fileno() == -1 for handle in pair))
        self.assertEqual([handle.closes for handle in pair], [1, 1])
        manager.request_shutdown()
        result = self.settle(manager)
        self.assertTrue(result.may_exit, result.failures)

    def test_failed_wakeup_close_is_retained_and_final_retry_can_release_it(self):
        manager = self.backend()
        pair = self.sockets(fail_first=True)
        with patch('paho.mqtt.client._socketpair_compat', return_value=pair), \
             patch('threading.Thread.start', lambda worker: self.reject_network_start(worker)):
            manager.mqtt.start()
            self.assertFalse(manager.mqtt.wait_stopped(1))
        owner = manager.mqtt._cleanup_client
        self.assertIsNotNone(owner)
        self.assertIsNone(owner._thread)
        self.assertIs(owner._sockpairR, pair[0])
        self.assertIsNone(owner._sockpairW)
        manager.request_shutdown()
        first = self.settle(manager)
        self.assertFalse(first.may_exit)
        self.assertNotIn('cannot join', repr(first.failures))
        pair[0].fail = False
        manager.retry_shutdown()
        second = self.settle(manager)
        self.assertTrue(second.may_exit, second.failures)
        self.assertTrue(all(handle.fileno() == -1 for handle in pair))
        self.assertFalse(first.may_exit)

    def test_started_then_raised_network_loop_stays_owned_until_it_finishes(self):
        manager = self.backend()
        pair = self.sockets()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def loop_forever(client, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test loop not released')
        def started_then_raise(worker):
            self.start_thread(worker)
            if worker.name.startswith('paho-mqtt-client-'):
                self.assertTrue(entered.wait(1))
                raise RuntimeError('after network admission')
        with patch('paho.mqtt.client._socketpair_compat', return_value=pair), \
             patch('paho.mqtt.client.Client.loop_forever', loop_forever), \
             patch('threading.Thread.start', started_then_raise):
            manager.mqtt.start()
        owner = manager.mqtt._cleanup_client
        self.assertIsNotNone(owner)
        worker = owner._thread
        self.assertIsNotNone(worker.ident)
        self.assertTrue(worker.is_alive())
        manager.request_shutdown()
        self.assertIsNone(manager.poll_lifecycle())
        self.assertTrue(manager.lifecycle_pending)
        self.assertTrue(all(handle.fileno() != -1 for handle in pair))
        release.set()
        result = self.settle(manager)
        self.assertTrue(result.may_exit, result.failures)
        self.assertFalse(worker.is_alive())
        self.assertTrue(all(handle.fileno() == -1 for handle in pair))

    def test_completed_then_raised_network_loop_still_closes_its_wakeup_sockets(self):
        manager = self.backend()
        pair = self.sockets()
        def completed_then_raise(worker):
            self.start_thread(worker)
            if worker.name.startswith('paho-mqtt-client-'):
                worker.join(1)
                self.assertFalse(worker.is_alive())
                raise RuntimeError('after network completion')
        with patch('paho.mqtt.client._socketpair_compat', return_value=pair), \
             patch('paho.mqtt.client.Client.loop_forever', return_value=None), \
             patch('threading.Thread.start', completed_then_raise):
            manager.mqtt.start()
            self.assertTrue(manager.mqtt.wait_stopped(1), manager.mqtt.last_error)
        self.assertTrue(all(handle.fileno() == -1 for handle in pair))
        manager.request_shutdown()
        result = self.settle(manager)
        self.assertTrue(result.may_exit, result.failures)


if __name__ == '__main__':
    unittest.main()
