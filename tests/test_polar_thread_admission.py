"""Deterministic thread admission failures; fake streams, no native endpoint."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.backend.polar_transport import PolarLinkTransport


class Handle:
    is_open = True
    def close(self):
        self.is_open = False
    def read(self, size):
        return b''
    def write(self, data):
        return len(data)


class PolarThreadAdmissionTests(unittest.TestCase):
    def test_connector_constructor_failure_releases_reservation(self):
        transport = PolarLinkTransport(port='fake')
        with patch('threading.Thread', side_effect=RuntimeError('constructor rejected')):
            transport.request_start()
        self.assertFalse(transport._connecting)
        self.assertIsNone(transport._connector_thread)
        self.assertTrue(transport.cleanup_complete)
        self.assertEqual(transport.last_error_code, 'THREAD_START_FAILED')

    def test_stopper_constructor_failure_releases_reservation_but_keeps_estop(self):
        transport = PolarLinkTransport(port='fake')
        with patch('threading.Thread', side_effect=RuntimeError('constructor rejected')):
            transport.request_stop()
        self.assertFalse(transport._stopping)
        self.assertIsNone(transport._stopper_thread)
        self.assertTrue(transport._estop_active)
        self.assertEqual(transport.last_error_code, 'THREAD_START_FAILED')

    def test_writer_constructor_failure_closes_published_stream(self):
        transport, handle = PolarLinkTransport(port='fake'), Handle()
        with patch('rm_client.backend.serial_stream.open_stream', return_value=handle), \
                patch('threading.Thread', side_effect=RuntimeError('constructor rejected')):
            transport.start()
        self.assertFalse(handle.is_open)
        self.assertFalse(transport.connected)
        self.assertTrue(transport.cleanup_complete)
        self.assertEqual(transport.last_error_code, 'THREAD_START_FAILED')

    def test_rejected_start_without_native_admission_is_retryable_for_each_role(self):
        for role in ('connector', 'stopper', 'writer'):
            with self.subTest(role=role):
                transport, handle = PolarLinkTransport(port='fake'), Handle()
                with patch.object(threading.Thread, 'start', side_effect=RuntimeError('start rejected')), \
                        patch('rm_client.backend.serial_stream.open_stream', return_value=handle):
                    if role == 'connector':
                        transport.request_start()
                        self.assertIsNone(transport._connector_thread)
                    elif role == 'stopper':
                        transport.request_stop()
                        self.assertIsNone(transport._stopper_thread)
                        self.assertTrue(transport._estop_active)
                    else:
                        transport.start()
                        self.assertIsNone(transport._thread)
                        self.assertFalse(handle.is_open)
                self.assertFalse(transport._connecting)
                self.assertFalse(transport._stopping)
                self.assertTrue(transport.cleanup_complete)
                self.assertEqual(transport.last_error_code, 'THREAD_START_FAILED')

    def test_late_connector_start_failure_retains_reservation_until_rejected_open_closes(self):
        transport, handle = PolarLinkTransport(port='fake'), Handle()
        entered, release = threading.Event(), threading.Event()
        workers = []
        original = threading.Thread.start
        def open_stream(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return handle
        def late_start(worker):
            workers.append(worker)
            original(worker)
            self.assertTrue(entered.wait(1))
            raise RuntimeError('late start failure')
        try:
            with patch('rm_client.backend.serial_stream.open_stream', side_effect=open_stream) as opener, \
                    patch.object(threading.Thread, 'start', new=late_start):
                transport.request_start()
                self.assertTrue(transport._connecting)
                self.assertIs(transport._connector_thread, workers[0])
                transport.request_start()
                self.assertEqual(opener.call_count, 1)
                release.set()
                workers[0].join(2)
            self.assertFalse(handle.is_open)
            self.assertFalse(transport.connected)
            self.assertIsNone(transport._thread)
            self.assertTrue(transport.cleanup_complete)
        finally:
            release.set()
            for worker in workers:
                worker.join(2)
            transport.stop()

    def test_late_connector_failure_with_concurrent_stop_closes_once(self):
        transport = PolarLinkTransport(port='fake')
        opened, stop_requested = threading.Event(), threading.Event()
        workers, closes = [], []
        class Stream(Handle):
            def close(self):
                closes.append(threading.get_ident())
                super().close()
        handle = Stream()
        original = threading.Thread.start
        def acquire(*args, **kwargs):
            opened.set()
            return handle
        def late_start(worker):
            workers.append(worker)
            original(worker)
            if worker.name == 'polar-connect':
                self.assertTrue(opened.wait(1))
                self.assertTrue(stop_requested.wait(1))
                raise RuntimeError('late connector start')
        def stop():
            if opened.wait(2):
                transport.request_stop()
                stop_requested.set()
        controller = threading.Thread(target=stop)
        original(controller)
        try:
            with patch('rm_client.backend.serial_stream.open_stream', side_effect=acquire), \
                    patch.object(threading.Thread, 'start', new=late_start):
                transport.request_start()
                controller.join(2)
                for worker in workers:
                    worker.join(2)
            self.assertFalse(controller.is_alive())
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(len(closes), 1)
            self.assertFalse(handle.is_open)
            self.assertIsNone(transport._thread)
            self.assertFalse(transport.connected)
            self.assertTrue(transport.cleanup_complete)
        finally:
            opened.set()
            controller.join(2)
            for worker in workers:
                worker.join(2)
            transport.stop()

    def test_late_stopper_start_failure_keeps_owner_until_cleanup_finishes(self):
        transport = PolarLinkTransport(port='fake')
        workers = []
        original = threading.Thread.start
        def late_start(worker):
            workers.append(worker)
            original(worker)
            raise RuntimeError('late start failure')
        try:
            # Stopper cannot finish while this lock is held, so the owner
            # observation is deterministic rather than a scheduling race.
            with transport._lifecycle_lock:
                with patch.object(threading.Thread, 'start', new=late_start):
                    transport.request_stop()
                    self.assertTrue(transport._stopping)
                    self.assertIs(transport._stopper_thread, workers[0])
                    transport.request_stop()
                    self.assertEqual(len(workers), 1)
            workers[0].join(2)
            self.assertFalse(transport._stopping)
            self.assertTrue(transport.cleanup_complete)
        finally:
            for worker in workers:
                worker.join(2)

    def test_late_writer_start_failure_retains_joinable_owner_and_cancels_loop(self):
        transport, handle = PolarLinkTransport(port='fake'), Handle()
        entered, release = threading.Event(), threading.Event()
        workers = []
        original = threading.Thread.start
        def loop():
            entered.set()
            self.assertTrue(release.wait(2))
        def late_start(worker):
            workers.append(worker)
            original(worker)
            self.assertTrue(entered.wait(1))
            raise RuntimeError('late start failure')
        try:
            with patch.object(transport, '_loop', side_effect=loop), \
                    patch('rm_client.backend.serial_stream.open_stream', return_value=handle), \
                    patch.object(threading.Thread, 'start', new=late_start):
                transport.start()
            self.assertIs(transport._thread, workers[0])
            self.assertTrue(transport._stop.is_set())
            self.assertFalse(transport._running)
            self.assertFalse(transport.cleanup_complete)
            release.set()
            workers[0].join(2)
            transport.stop()
            self.assertTrue(transport.cleanup_complete)
        finally:
            release.set()
            for worker in workers:
                worker.join(2)
            transport.stop()


class AdmittedWriterCleanupTests(unittest.TestCase):
    def test_failed_admission_defers_close_until_actual_read_exits(self):
        for read_error, close_error in ((False, False), (True, False), (True, True)):
            with self.subTest(read_error=read_error, close_error=close_error):
                transport = PolarLinkTransport(port='fake')
                entered, release = threading.Event(), threading.Event()
                observations, workers = [], []
                class Stream(Handle):
                    reading = False
                    fail_close = close_error
                    def read(self, size):
                        self.reading = True
                        entered.set()
                        try:
                            if not release.wait(2):
                                raise TimeoutError('test did not release read')
                            if read_error:
                                raise OSError('read failed')
                            return b''
                        finally:
                            self.reading = False
                    def close(self):
                        observations.append((self.reading, threading.get_ident()))
                        if self.fail_close:
                            raise OSError('close refused')
                        super().close()
                handle = Stream()
                original = threading.Thread.start
                def late_start(worker):
                    workers.append(worker)
                    original(worker)
                    self.assertTrue(entered.wait(1))
                    raise RuntimeError('late writer start')
                try:
                    with patch('rm_client.backend.serial_stream.open_stream', return_value=handle), \
                            patch.object(threading.Thread, 'start', new=late_start):
                        transport.start()
                    self.assertEqual(observations, [])
                    self.assertFalse(transport.connected)
                    self.assertFalse(transport.cleanup_complete)
                    self.assertTrue(handle.is_open)
                    release.set()
                    workers[0].join(2)
                    self.assertFalse(workers[0].is_alive())
                    self.assertEqual(observations, [(False, workers[0].ident)])
                    if close_error:
                        self.assertIs(transport._serial, handle)
                        self.assertFalse(transport.cleanup_complete)
                        handle.fail_close = False
                        transport.stop()
                    self.assertFalse(handle.is_open)
                    self.assertTrue(transport.cleanup_complete)
                finally:
                    release.set()
                    for worker in workers:
                        worker.join(2)
                    handle.fail_close = False
                    transport.stop()

    def test_writer_finished_before_start_raises_does_not_leak_or_close_twice(self):
        transport = PolarLinkTransport(port='fake')
        observations, workers = [], []
        class Stream(Handle):
            def read(self, size):
                raise OSError('read failed immediately')
            def close(self):
                observations.append(threading.get_ident())
                super().close()
        handle = Stream()
        original = threading.Thread.start
        def late_start(worker):
            workers.append(worker)
            original(worker)
            worker.join(2)
            self.assertFalse(worker.is_alive())
            raise RuntimeError('completed before start raised')
        with patch('rm_client.backend.serial_stream.open_stream', return_value=handle), \
                patch.object(threading.Thread, 'start', new=late_start):
            transport.start()
        self.assertEqual(observations, [workers[0].ident])
        self.assertFalse(handle.is_open)
        self.assertFalse(transport.connected)
        self.assertTrue(transport.cleanup_complete)


if __name__ == '__main__':
    unittest.main()
