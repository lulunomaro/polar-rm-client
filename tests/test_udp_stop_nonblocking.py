"""UDP stop admission never closes sockets or joins receivers on the caller."""
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.transports import UDPVideoReceiver


class Socket:
    def __init__(self):
        self.is_open = True
        self.fail_close = False
        self.read_entered = threading.Event()
        self.read_release = threading.Event()
        self.close_entered = threading.Event()
        self.close_release = threading.Event()
        self.close_release.set()
        self.close_threads = []
    def bind(self, address):
        pass
    def settimeout(self, value):
        pass
    def recvfrom(self, size):
        self.read_entered.set()
        if not self.read_release.wait(2):
            raise TimeoutError('test receiver not released')
        raise OSError('socket receiver ended')
    def close(self):
        self.close_threads.append(threading.get_ident())
        self.close_entered.set()
        if not self.close_release.wait(2):
            raise TimeoutError('test native close not released')
        self.read_release.set()
        if self.fail_close:
            raise OSError('native socket refused close')
        self.is_open = False


class UdpStopRequestTests(unittest.TestCase):
    def socket(self):
        sock = Socket()
        self.addCleanup(sock.read_release.set)
        self.addCleanup(sock.close_release.set)
        return sock

    def test_request_is_prompt_coalesces_and_wait_includes_native_closer(self):
        receiver, sock = UDPVideoReceiver(), self.socket()
        sock.close_release.clear()
        with patch('rm_client.transports.socket.socket', return_value=sock) as opener:
            receiver.start()
            self.assertTrue(sock.read_entered.wait(1))
            before = time.perf_counter()
            receiver.request_stop()
            self.assertLess(time.perf_counter() - before, .1)
            self.assertTrue(sock.close_entered.wait(1))
            worker = receiver._stop_worker
            self.assertFalse(receiver.running)
            self.assertTrue(receiver._session.stopped.is_set())
            for _ in range(100):
                receiver.request_stop()
                receiver.start()
            self.assertIs(receiver._stop_worker, worker)
            self.assertEqual(opener.call_count, 1)
            self.assertFalse(receiver.cleanup_complete)
            before = time.perf_counter()
            self.assertFalse(receiver.wait_closed(0))
            self.assertLess(time.perf_counter() - before, .1)
            sock.close_release.set()
            self.assertTrue(receiver.wait_closed(1))
        self.assertTrue(receiver.cleanup_complete)
        self.assertFalse(worker.is_alive())
        self.assertTrue(all(thread != threading.get_ident() for thread in sock.close_threads))

    def test_late_socket_remains_owned_on_failure_and_explicit_request_retries(self):
        receiver, sock = UDPVideoReceiver(), self.socket()
        sock.fail_close = True
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def open_socket(*args):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test socket creation not released')
            return sock
        with patch('rm_client.transports.socket.socket', side_effect=open_socket) as opener:
            receiver.start()
            self.assertTrue(entered.wait(1))
            receiver.request_stop()
            self.assertFalse(receiver.cleanup_complete)
            self.assertFalse(receiver.wait_closed(0))
            release.set()
            receiver._stop_worker.join(1)
            self.assertFalse(receiver._stop_worker.is_alive())
            self.assertIs(receiver._session.sock, sock)
            self.assertFalse(receiver.cleanup_complete)
            receiver.start()
            self.assertEqual(opener.call_count, 1)
            sock.fail_close = False
            receiver.request_stop()
            self.assertTrue(receiver.wait_closed(1))
        self.assertFalse(sock.is_open)

    def test_failed_worker_admission_keeps_session_for_synchronous_stop(self):
        receiver, sock = UDPVideoReceiver(), self.socket()
        with patch('rm_client.transports.socket.socket', return_value=sock):
            receiver.start()
            self.assertTrue(sock.read_entered.wait(1))
            with patch('threading.Thread.start', side_effect=RuntimeError('stop worker not admitted')):
                with self.assertRaisesRegex(RuntimeError, 'stop worker not admitted'):
                    receiver.request_stop()
            self.assertFalse(receiver.running)
            self.assertTrue(receiver._session.stopped.is_set())
            self.assertIs(receiver._session.sock, sock)
            self.assertFalse(receiver.cleanup_complete)
            receiver.stop()
            self.assertTrue(receiver.wait_closed(1))


if __name__ == '__main__':
    unittest.main()
