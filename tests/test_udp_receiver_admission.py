"""An admitted UDP receiver keeps its late socket owner after a start error."""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.backend.manager import BackendManager
from rm_client.backend.video_source import VideoSourceAdmissionError
from rm_client.transports import UDPVideoReceiver
from tests.test_udp_stop_nonblocking import Socket


class UdpReceiverAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.start_thread = threading.Thread.start

    def test_never_admitted_receiver_discards_only_empty_placeholders(self):
        receiver = UDPVideoReceiver()
        with patch('rm_client.transports.socket.socket') as socket, \
             patch('threading.Thread.start', side_effect=RuntimeError('not admitted')):
            with self.assertRaisesRegex(RuntimeError, 'not admitted'):
                receiver.start()
        socket.assert_not_called()
        self.assertIsNone(receiver._session)
        self.assertIsNone(receiver._thread)
        self.assertFalse(receiver.running)
        self.assertTrue(receiver.cleanup_complete)
        self.assertIn('not admitted', receiver.last_error)

    def test_completed_receiver_then_raise_still_has_a_joinable_owner(self):
        receiver = UDPVideoReceiver()
        def completed_then_raise(worker):
            self.start_thread(worker)
            worker.join(1)
            self.assertFalse(worker.is_alive())
            raise RuntimeError('after completion')
        with patch.object(receiver, '_run'), patch('threading.Thread.start', completed_then_raise):
            with self.assertRaisesRegex(RuntimeError, 'after completion'):
                receiver.start()
        self.assertIsNotNone(receiver._thread)
        self.assertIsNotNone(receiver._thread.ident)
        self.assertIsNotNone(receiver._session)
        self.assertTrue(receiver.wait_closed(1))

    def test_actual_manager_final_close_waits_for_late_socket_and_retains_failed_close(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root.name}), patch.object(
                BackendManager, '_build_commit', return_value='TEST'):
            manager = BackendManager(workspace=root.name)
        self.addCleanup(manager.shutdown)
        socket = Socket()
        socket.fail_close = True
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def release_fake():
            socket.fail_close = False
            socket.close_release.set()
            socket.read_release.set()
        self.addCleanup(release_fake)
        def late_socket(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test socket not released')
            return socket
        workers = []
        def admitted_then_raise(worker):
            self.start_thread(worker)
            if worker.name == 'udp-video':
                workers.append(worker)
                self.assertTrue(entered.wait(1))
                raise RuntimeError('after UDP receiver admission')
        with patch('rm_client.transports.socket.socket', side_effect=late_socket) as opener:
            with patch('threading.Thread.start', admitted_then_raise):
                with self.assertRaises(VideoSourceAdmissionError):
                    manager.set_active_video_source('UDP HEVC')
            self.assertIs(manager.udp._thread, workers[0])
            self.assertTrue(workers[0].is_alive())
            self.assertIsNotNone(manager.udp._session)
            self.assertTrue(manager.udp._session.stopped.is_set())
            self.assertFalse(manager.udp.cleanup_complete)
            for _ in range(25):
                manager.udp.start()
            self.assertEqual(opener.call_count, 1)
            manager.request_shutdown()
            self.assertIsNone(manager.poll_lifecycle())
            self.assertTrue(manager.lifecycle_pending)
            self.assertIsNone(manager.shutdown_completion)
            release.set()
            first = self.settle(manager)
        self.assertFalse(first.may_exit)
        self.assertFalse(workers[0].is_alive())
        self.assertIs(manager.udp._session.sock, socket)
        self.assertTrue(socket.is_open)
        socket.fail_close = False
        manager.retry_shutdown()
        second = self.settle(manager)
        self.assertTrue(second.may_exit, second.failures)
        self.assertFalse(socket.is_open)
        self.assertIsNone(manager.udp._session)
        self.assertFalse(first.may_exit)

    def settle(self, manager):
        result = None
        deadline = time.monotonic() + 2
        while manager.lifecycle_pending:
            completion = manager.poll_lifecycle()
            if completion is not None:
                result = completion
            if time.monotonic() > deadline:
                self.fail('UDP owner did not settle')
            time.sleep(.001)
        return result


if __name__ == '__main__':
    unittest.main()
