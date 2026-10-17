"""Constructor rejection is distinct from Thread.start rejection.

Production owners and real Thread instances; serial/pipe/socket boundaries inert.
No physical device, socket bind/connect, codec or display is exercised.
"""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.backend.lifecycle import close_decoder, close_serial
from rm_client.backend.serial_backend import RmSerialBackend
from rm_client.backend.mqtt_backend import RmMqttBackend
from rm_client.backend.video_backend import ContinuousMjpegDecoder
from tests import test_serial_starter_admission as serial_starter_fixture
from tests.test_serial_reader_admission import Handle
from tests.test_mqtt_cleanup_ownership import Client, Socket
from tests.test_video_close_ownership import Process


class SerialStarterConstructorTests(unittest.TestCase):
    setUp = serial_starter_fixture.SerialStarterAdmissionTests.setUp
    cleanup = serial_starter_fixture.SerialStarterAdmissionTests.cleanup
    settle = serial_starter_fixture.SerialStarterAdmissionTests.settle
    open_serial = serial_starter_fixture.SerialStarterAdmissionTests.open_serial

    def test_rejected_starter_constructor_admits_no_open_and_final_shutdown_finishes(self):
        real_thread = threading.Thread
        failure = RuntimeError('serial starter constructor rejected')
        def construct(*args, **kwargs):
            if kwargs.get('name') == 'backend-start-serial':
                raise failure
            return real_thread(*args, **kwargs)
        with patch.object(threading, 'Thread', construct):
            self.manager.request_scenario('LabDebug', start=True)
            result = self.settle()
        self.assertFalse(result.succeeded)
        self.assertIn(str(failure), result.failures[0].error)
        self.assertEqual(self.starts, [])
        self.assertFalse(self.open_entered.is_set())
        self.assertIsNone(self.manager._serial_start_thread)
        self.assertTrue(self.manager.safety.estop_active)
        self.manager.request_shutdown()
        final = self.settle()
        self.assertTrue(final.may_exit, final.failures)
        self.assertTrue(final.succeeded)


class NativeConstructorMatrixTests(unittest.TestCase):
    def test_serial_reader_constructor_closes_or_retains_actual_open_handle(self):
        for failed_close in (False, True):
            with self.subTest(failed_close=failed_close):
                backend = RmSerialBackend(port='FAKE')
                handle = Handle()
                handle.fail_close = failed_close
                try:
                    with patch('serial.Serial', return_value=handle), patch.object(
                            threading, 'Thread', side_effect=RuntimeError('reader constructor rejected')):
                        backend.start()
                    self.assertFalse(backend.connected)
                    self.assertFalse(backend._running)
                    self.assertIsNone(backend._reader)
                    self.assertEqual(handle.is_open, failed_close)
                    if failed_close:
                        self.assertIs(backend._serial, handle)
                        with patch('serial.Serial') as opener:
                            backend.start()
                        opener.assert_not_called()
                        with self.assertRaises(RuntimeError):
                            close_serial(backend, None)
                    else:
                        self.assertIsNone(backend._serial)
                        self.assertIn('reader constructor rejected', backend.last_error)
                finally:
                    handle.fail_close = False
                    close_serial(backend, None)
                self.assertFalse(handle.is_open)
                self.assertIsNone(backend._serial)

    def test_each_decoder_pipe_constructor_releases_or_retains_process(self):
        real_thread = threading.Thread
        for target in ('mjpeg-in-h264', 'mjpeg-dec-h264', 'mjpeg-err-h264'):
            for failed_close in (False, True):
                with self.subTest(target=target, failed_close=failed_close):
                    decoder = ContinuousMjpegDecoder('h264')
                    process = Process()
                    process.stdout.fail_close = failed_close
                    constructed = []
                    def construct(*args, **kwargs):
                        if kwargs.get('name') == target:
                            raise RuntimeError('pipe constructor rejected: ' + target)
                        worker = real_thread(*args, **kwargs)
                        constructed.append(worker)
                        return worker
                    try:
                        with patch('rm_client.backend.video_backend.resolve_ffmpeg_executable', return_value='ffmpeg'), \
                                patch('rm_client.backend.video_backend.subprocess.Popen', return_value=process), \
                                patch.object(threading, 'Thread', construct):
                            self.assertFalse(decoder.start())
                        self.assertFalse(decoder.available)
                        self.assertTrue(all(worker.ident is None for worker in constructed))
                        self.assertEqual(decoder.cleanup_complete, not failed_close)
                        if failed_close:
                            self.assertIs(decoder._retired_sessions[0].proc, process)
                            with patch('rm_client.backend.video_backend.subprocess.Popen') as spawn:
                                self.assertFalse(decoder.start())
                            spawn.assert_not_called()
                            with self.assertRaisesRegex(RuntimeError, 'pipe close failed'):
                                close_decoder(decoder, None)
                    finally:
                        process.stdout.fail_close = False
                        close_decoder(decoder, None)
                    self.assertTrue(decoder.cleanup_complete)
                    self.assertFalse(process.alive)
                    self.assertTrue(all(pipe.closed for pipe in (process.stdin, process.stdout, process.stderr)))

    def test_actual_paho_network_thread_constructor_releases_wakeup_pair(self):
        backend = RmMqttBackend(mock_output=False)
        pair = Socket(), Socket()
        real_thread = threading.Thread
        rejected = []
        class ConstructorGuard(real_thread):
            def __init__(self, *args, **kwargs):
                if kwargs.get('name', '').startswith('paho-mqtt-client-'):
                    rejected.append(kwargs['name'])
                    raise RuntimeError('Paho constructor rejected')
                super().__init__(*args, **kwargs)
        try:
            with patch('paho.mqtt.client._socketpair_compat', return_value=pair), \
                    patch.object(threading, 'Thread', ConstructorGuard):
                backend.start()
                self.assertTrue(backend.wait_stopped(1), backend.last_error)
            self.assertEqual(len(rejected), 1)
            self.assertIsNone(backend.client)
            self.assertIsNone(backend._cleanup_client)
            self.assertTrue(all(handle.fileno() == -1 for handle in pair))
            self.assertEqual([handle.closes for handle in pair], [1, 1])
            self.assertIn('Paho constructor rejected', backend.last_error)
        finally:
            backend.stop()

    def test_mqtt_closer_constructor_retains_client_until_explicit_stop_retry(self):
        backend = RmMqttBackend()
        client = Client()
        backend.client = client
        backend._started = True
        failure = RuntimeError('MQTT cleanup constructor rejected')
        try:
            with patch.object(threading, 'Thread', side_effect=failure):
                with self.assertRaises(RuntimeError) as raised:
                    backend.stop(wait=False)
            self.assertIs(raised.exception, failure)
            self.assertIsNone(backend.client)
            self.assertIs(backend._cleanup_client, client)
            self.assertIsNone(backend._cleanup_thread)
            self.assertFalse(backend.connected)
            self.assertFalse(backend.wait_stopped(0))
            with patch.object(backend, '_start_client_locked') as start:
                backend.start()
            start.assert_not_called()
            self.assertIs(backend._cleanup_client, client)
        finally:
            backend.stop()
        self.assertTrue(backend.wait_stopped(1))
        self.assertIsNone(backend._cleanup_client)


if __name__ == '__main__':
    unittest.main()
