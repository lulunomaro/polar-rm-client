"""A start exception after real admission must not discard the cleanup worker."""
import threading
import unittest
from unittest.mock import patch

from rm_client.backend.mqtt_backend import RmMqttBackend
from rm_client.transports import UDPVideoReceiver, _UDPVideoSession
from tests.test_mqtt_cleanup_ownership import Client
from tests.test_udp_stop_nonblocking import Socket
from tests.test_video_close_ownership import Process, installed_decoder


class CleanupWorkerAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.start_thread = threading.Thread.start
        self.admitted = []
        self.addCleanup(lambda: [worker.join(1) for worker in self.admitted])

    def start_then_raise(self, worker):
        self.start_thread(worker)
        self.admitted.append(worker)
        raise RuntimeError('raised after cleanup admission')

    def test_decoder_retains_admitted_stopper_and_does_not_duplicate_it(self):
        proc = Process()
        decoder, _ = installed_decoder(proc)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        wait = proc.wait
        def held_wait(timeout=None):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test process not released')
            return wait(timeout)
        proc.wait = held_wait
        with patch('threading.Thread.start', lambda worker: self.start_then_raise(worker)):
            with self.assertRaisesRegex(RuntimeError, 'after cleanup admission'):
                decoder.request_stop()
            self.assertIs(decoder._stop_worker, self.admitted[0])
            self.assertTrue(entered.wait(1))
            for _ in range(25):
                decoder.request_stop()
            self.assertEqual(len(self.admitted), 1)
        release.set()
        self.assertTrue(decoder.wait_closed())
        self.assertFalse(self.admitted[0].is_alive())

    def test_udp_retains_admitted_stopper_and_does_not_duplicate_it(self):
        receiver = UDPVideoReceiver()
        socket = Socket()
        socket.close_release.clear()
        self.addCleanup(socket.close_release.set)
        receiver._session = _UDPVideoSession(sock=socket)
        receiver._socket = socket
        receiver.running = True
        with patch('threading.Thread.start', lambda worker: self.start_then_raise(worker)):
            with self.assertRaisesRegex(RuntimeError, 'after cleanup admission'):
                receiver.request_stop()
            self.assertIs(receiver._stop_worker, self.admitted[0])
            self.assertTrue(socket.close_entered.wait(1))
            for _ in range(25):
                receiver.request_stop()
            self.assertEqual(len(self.admitted), 1)
        self.assertFalse(receiver.wait_closed(0))
        socket.close_release.set()
        self.assertTrue(receiver.wait_closed(1))
        self.assertFalse(self.admitted[0].is_alive())

    def test_mqtt_retains_admitted_closer_and_does_not_duplicate_it(self):
        backend = RmMqttBackend()
        client = Client()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def loop_stop():
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test loop not released')
        client.loop_stop = loop_stop
        backend.client = client
        backend._started = True
        with patch('threading.Thread.start', lambda worker: self.start_then_raise(worker)):
            backend.stop(wait=False)
            self.assertIs(backend._cleanup_thread, self.admitted[0])
            self.assertTrue(entered.wait(1))
            for _ in range(25):
                backend.stop(wait=False)
            self.assertEqual(len(self.admitted), 1)
        self.assertFalse(backend.wait_stopped(0))
        release.set()
        self.assertTrue(backend.wait_stopped(1))
        self.assertFalse(self.admitted[0].is_alive())


if __name__ == '__main__':
    unittest.main()
