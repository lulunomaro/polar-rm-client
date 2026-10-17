"""Actual transport close failures must retain owners and deny replacement."""
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.backend.controller_angles import AngleRelayConfig, ControllerAngleRelay
from rm_client.backend.lifecycle import close_controller_angles, close_polar, close_serial
from rm_client.backend.manager import BackendManager
from rm_client.backend.polar_transport import PolarLinkTransport
from rm_client.backend.resource_close import close_resource, release_capture
from rm_client.backend.serial_backend import RmSerialBackend
from rm_client.backend.serial_stream import TcpByteStream


def until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("resource did not settle")
        time.sleep(.001)


class FailingCloseStream:
    def __init__(self):
        self.is_open = True
        self.fail_close = True
        self.close_calls = 0
        self.in_waiting = 0
    def read(self, _size):
        return b""
    def write(self, data):
        return len(data)
    def reset_input_buffer(self):
        pass
    def close(self):
        self.close_calls += 1
        if self.fail_close:
            raise OSError("driver refused close")
        self.is_open = False


class ResourceCloseTests(unittest.TestCase):
    def test_close_adapter_rejects_false_and_still_open_results(self):
        for stream in (SimpleNamespace(close=lambda: False),
                       SimpleNamespace(close=lambda: None, is_open=True)):
            with self.subTest(stream=stream), self.assertRaises(OSError):
                close_resource(stream)
        close_resource(SimpleNamespace(close=lambda: None, is_open=False))
        close_resource(SimpleNamespace(close=lambda: None))

    def test_capture_release_rejects_false_or_still_open(self):
        for capture in (SimpleNamespace(release=lambda: False, isOpened=lambda: False),
                        SimpleNamespace(release=lambda: None, isOpened=lambda: True)):
            with self.subTest(capture=capture), self.assertRaises(OSError):
                release_capture(capture)
        release_capture(SimpleNamespace(release=lambda: None, isOpened=lambda: False))

    def test_polar_worker_exit_cannot_discard_failed_stream_close(self):
        polar = PolarLinkTransport(port="TEST")
        stream = FailingCloseStream()
        polar._serial, polar.connected, polar._running = stream, True, True
        polar._stop.set()
        polar._loop()
        self.assertIs(polar._serial, stream)
        self.assertFalse(polar.cleanup_complete)
        with self.assertRaisesRegex(RuntimeError, "close unconfirmed"):
            close_polar(polar)
        stream.fail_close = False
        close_polar(polar)
        self.assertTrue(polar.cleanup_complete)

    def test_polar_worker_and_stop_serialize_blocked_native_close(self):
        entered, release = threading.Event(), threading.Event()
        stream = FailingCloseStream()
        original_close = stream.close
        close_entries = []
        def blocked_close():
            close_entries.append(threading.get_ident())
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test close not released")
            original_close()
        stream.close = blocked_close
        polar = PolarLinkTransport(port="TEST")
        polar._serial, polar.connected, polar._running = stream, True, True
        polar._stop.set()
        worker = threading.Thread(target=polar._loop)
        polar._thread = worker
        worker.start()
        self.assertTrue(entered.wait(1))
        stopper = threading.Thread(target=polar.stop)
        stopper.start()
        try:
            # stop's bounded join expires, but it cannot enter native close
            # while the worker still owns the dedicated close lock.
            time.sleep(1.1)
            self.assertTrue(stopper.is_alive())
            self.assertEqual(len(close_entries), 1)
            self.assertIs(polar._serial, stream)
            self.assertFalse(polar.cleanup_complete)
        finally:
            release.set()
            worker.join(2)
            stopper.join(2)
        self.assertIs(polar._serial, stream)
        self.assertFalse(polar.cleanup_complete)
        stream.fail_close = False
        close_polar(polar)
        self.assertTrue(polar.cleanup_complete)

    def test_tcp_wrapper_retains_failed_socket_and_polar_reports_it(self):
        sock = FailingCloseStream()
        sock.shutdown = lambda how: None
        stream = TcpByteStream("TEST", 1)
        stream._sock, stream.is_open = sock, True
        polar = PolarLinkTransport(port="TEST")
        polar._serial, polar.connected = stream, True
        with self.assertRaisesRegex(RuntimeError, "close unconfirmed"):
            close_polar(polar)
        self.assertIs(stream._sock, sock)
        self.assertIs(polar._serial, stream)
        self.assertFalse(stream.is_open)
        with self.assertRaises(OSError):
            stream.open()
        sock.fail_close = False
        close_polar(polar)
        self.assertIsNone(stream._sock)
        self.assertTrue(polar.cleanup_complete)

    def test_tcp_open_setup_failure_preserves_nested_socket_owner(self):
        sock = FailingCloseStream()
        sock.shutdown = lambda how: None
        sock.settimeout = lambda seconds: (_ for _ in ()).throw(OSError("setup failed"))
        polar = PolarLinkTransport(port="TEST")
        polar.transport, polar.host = "tcp", "TEST"
        with patch("rm_client.backend.serial_stream.socket.create_connection", return_value=sock):
            polar.start()
        self.assertIsNotNone(polar._serial)
        self.assertIs(polar._serial._sock, sock)
        self.assertFalse(polar.cleanup_complete)
        sock.fail_close = False
        close_polar(polar)
        self.assertTrue(polar.cleanup_complete)

    def test_serial_close_failure_retains_actual_handle_until_explicit_retry(self):
        serial = RmSerialBackend(port="TEST")
        stream = FailingCloseStream()
        serial._serial, serial.connected = stream, True
        with self.assertRaisesRegex(RuntimeError, "close unconfirmed"):
            close_serial(serial, None)
        self.assertIs(serial._serial, stream)
        self.assertFalse(serial.cleanup_complete)
        self.assertFalse(serial.connected)
        with patch.dict(sys.modules, {"serial": SimpleNamespace(Serial=lambda **kw: self.fail("replacement opened"))}):
            serial.start()
        stream.fail_close = False
        close_serial(serial, None)
        self.assertIsNone(serial._serial)
        self.assertTrue(serial.cleanup_complete)
        self.assertFalse(stream.is_open)

    def test_serial_failed_open_cleanup_cannot_discard_ambiguous_stream(self):
        serial = RmSerialBackend(port="TEST")
        stream = FailingCloseStream()
        stream.is_open = False
        with patch.dict(sys.modules, {"serial": SimpleNamespace(Serial=lambda **kw: stream)}):
            serial.start()
        self.assertIs(serial._serial, stream)
        self.assertFalse(serial.cleanup_complete)
        stream.fail_close = False
        close_serial(serial, None)

    def test_polar_close_failure_retains_actual_handle_and_blocks_new_open(self):
        polar = PolarLinkTransport(port="TEST")
        stream = FailingCloseStream()
        polar._serial, polar.connected = stream, True
        with self.assertRaisesRegex(RuntimeError, "close unconfirmed"):
            close_polar(polar)
        self.assertIs(polar._serial, stream)
        self.assertFalse(polar.cleanup_complete)
        with patch("rm_client.backend.serial_stream.open_stream", side_effect=AssertionError("replacement opened")):
            polar.start()
            polar.request_start()
        self.assertIsNone(polar._connector_thread)
        stream.fail_close = False
        close_polar(polar)
        self.assertTrue(polar.cleanup_complete)

    def test_rejected_late_polar_open_retains_handle_when_close_fails(self):
        entered, release = threading.Event(), threading.Event()
        stream = FailingCloseStream()
        self.addCleanup(lambda: setattr(stream, "fail_close", False))
        self.addCleanup(release.set)
        polar = PolarLinkTransport(port="TEST")
        def open_stream(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test connector not released")
            return stream
        with patch("rm_client.backend.polar_transport.resolve_polar_port", return_value="TEST"), patch(
                "rm_client.backend.serial_stream.open_stream", side_effect=open_stream):
            polar.request_start()
            self.assertTrue(entered.wait(1))
            polar.stop()
            release.set()
            until(lambda: polar._connector_thread is None)
            self.assertIs(polar._serial, stream)
            self.assertFalse(polar.cleanup_complete)
            self.assertEqual(polar.last_error_code, "STREAM_CLOSE_FAILED")
            polar.request_start()
            self.assertIsNone(polar._connector_thread)
        stream.fail_close = False
        close_polar(polar)
        self.assertTrue(polar.cleanup_complete)

    def test_angle_close_failure_keeps_port_reservation_until_worker_retry(self):
        stream = FailingCloseStream()
        opened = []
        def open_serial(**kwargs):
            opened.append(kwargs["port"])
            return stream
        relay = ControllerAngleRelay(serial_factory=open_serial)
        relay.configure(AngleRelayConfig(enabled=True, rx_port="RX"), authorized=False)
        until(lambda: relay.connected)
        relay.close()
        with self.assertRaisesRegex(RuntimeError, "close unconfirmed"):
            close_controller_angles(relay)
        self.assertEqual(relay.owned_ports, ("RX",))
        self.assertEqual(relay._retained_streams, (stream,))
        relay.configure(AngleRelayConfig(enabled=True, rx_port="OTHER"), authorized=False)
        self.assertEqual(opened, ["RX"])
        stream.fail_close = False
        close_controller_angles(relay)
        self.assertEqual(relay.owned_ports, ())
        self.assertFalse(stream.is_open)

    def test_real_serial_failure_fails_manager_transition_and_explicit_retry_recovers(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": root}):
            manager = BackendManager(workspace=root)
            stream = FailingCloseStream()
            manager.serial._serial, manager.serial.connected = stream, True
            try:
                manager.request_scenario("OfficialEvent", start=False)
                completions = []
                def complete():
                    result = manager.poll_lifecycle()
                    if result is not None:
                        completions.append(result)
                    return not manager.lifecycle_pending
                until(complete)
                self.assertFalse(completions[-1].succeeded)
                self.assertIn("serial", [item.resource for item in completions[-1].failures])
                self.assertEqual(manager.scenario_key, "LabDebug")
                self.assertIs(manager.serial._serial, stream)
                self.assertTrue(manager.safety.estop_active)
                stream.fail_close = False
                manager.request_scenario("OfficialEvent", start=False)
                until(complete)
                self.assertTrue(completions[-1].succeeded)
                self.assertEqual(manager.scenario_key, "OfficialEvent")
                self.assertIsNone(manager.serial._serial)
            finally:
                stream.fail_close = False
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
