"""Final retry composes real transport owners without reopening authority."""
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
from rm_client.backend.cc_link import AsyncCustomControllerLink
from rm_client.backend.dt7_link import DEFAULT_PROFILE, Dt7KeyMouseLink
from rm_client.backend.dt7_worker import AsyncDt7KeyMouseLink
from rm_client.backend.manager import BackendManager


def until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("shutdown did not settle")
        time.sleep(.001)


class Handle:
    def __init__(self):
        self.is_open = True
        self.fail_close = True
        self.close_threads = []
    def write(self, frame):
        return len(frame)
    def close(self):
        self.close_threads.append(threading.get_ident())
        if self.fail_close:
            raise OSError("test native close failure")
        self.is_open = False


class BackendShutdownRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": self.temp.name}):
            self.manager = BackendManager(workspace=self.temp.name)
        self.addCleanup(self.manager.shutdown)
        self.owner = threading.get_ident()

    def settle(self):
        results = []
        def poll():
            result = self.manager.poll_lifecycle()
            if result is not None:
                results.append(result)
            return not self.manager.lifecycle_pending
        until(poll)
        return results[-1]

    def check_writer(self, name):
        handle = Handle()
        if name == "DT7":
            sync = Dt7KeyMouseLink(port="TEST", profile=dict(DEFAULT_PROFILE), mock_output=False,
                _handle=handle, resolved_port="TEST", _handle_requested_port="TEST")
            writer = AsyncDt7KeyMouseLink(link=sync, profile=DEFAULT_PROFILE, mock_output=False)
            self.manager._dt7_link = writer
        else:
            writer = AsyncCustomControllerLink(mock_output=False, release_attempts=1)
            with writer._condition:
                writer._serial = handle
            self.manager._cc_link = writer
        try:
            self.manager.request_shutdown()
            first = self.settle()
            self.assertFalse(first.resources_released)
            self.assertIn(name, [failure.resource for failure in first.failures])
            self.assertFalse(first.may_exit)
            self.assertTrue(handle.is_open)
            before = dict(self.manager.field_log.snapshot())
            handle.fail_close = False
            self.manager.retry_shutdown()
            final = self.settle()
            self.assertTrue(final.resources_released)
            self.assertTrue(final.may_exit)
            self.assertTrue(final.succeeded)
            self.assertTrue(writer.closed)
            self.assertFalse(handle.is_open)
            self.assertTrue(all(thread != self.owner for thread in handle.close_threads))
            self.assertEqual(self.manager.field_log.snapshot()["dropped_records"], before["dropped_records"])
            self.assertIs(self.manager.initial_shutdown_completion, first)
        finally:
            handle.fail_close = False
            writer.close()
            writer.wait_closed(2)

    def test_actual_dt7_failed_finalizer_recovers_only_on_explicit_manager_retry(self):
        self.check_writer("DT7")

    def test_actual_cc_failed_finalizer_recovers_only_on_explicit_manager_retry(self):
        self.check_writer("CC")

    def test_mqtt_epoch_failure_keeps_owner_until_real_cleanup_completes(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def disconnect():
            calls.append("disconnect")
        def loop_stop():
            calls.append("loop_stop")
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test did not release MQTT cleanup")
        client = SimpleNamespace(disconnect=disconnect, loop_stop=loop_stop)
        self.manager.mqtt.client = client
        self.manager.mqtt._started = True
        with patch.object(self.manager.mqtt, "clear_received_epoch", side_effect=OSError("epoch cleanup failure")):
            self.manager.request_shutdown()
        self.assertTrue(entered.wait(1))
        self.assertIs(self.manager.mqtt._cleanup_client, client)
        self.assertIsNone(self.manager.mqtt.client)
        self.assertIsNone(self.manager.poll_lifecycle())
        self.assertIsNone(self.manager.shutdown_completion)
        self.assertTrue(self.manager.lifecycle_pending)
        release.set()
        result = self.settle()
        self.assertEqual(calls, ["disconnect", "loop_stop"])
        self.assertTrue(result.resources_released)
        self.assertTrue(result.may_exit)
        self.assertFalse(result.succeeded)
        self.assertIn("MQTT invalidate", [failure.resource for failure in result.failures])

    def test_real_paho_failed_native_close_blocks_exit_until_explicit_retry(self):
        import paho.mqtt.client as paho
        from rm_client.backend.mqtt_cleanup import MqttClientResources
        from tests.test_mqtt_cleanup_ownership import Socket
        client = paho.Client(paho.CallbackAPIVersion.VERSION2, reconnect_on_failure=False)
        owner = MqttClientResources(client)
        owner.attach_socket_observers()
        socket = Socket(fail=True)
        client._sock = socket
        self.manager.mqtt.client = client
        self.manager.mqtt._client_resources = owner
        self.manager.mqtt._started = True
        with self.assertRaises(OSError):
            client._sock_close()  # Vendor discards _sock before native close.
        self.assertIsNone(client._sock)
        self.manager.request_shutdown()
        first = self.settle()
        self.assertFalse(first.resources_released)
        self.assertFalse(first.may_exit)
        self.assertIs(self.manager.mqtt._cleanup_client, client)
        self.assertNotEqual(socket.fileno(), -1)
        socket.fail = False
        self.manager.retry_shutdown()
        result = self.settle()
        self.assertTrue(result.may_exit)
        self.assertTrue(result.succeeded)
        self.assertEqual(socket.fileno(), -1)
        self.assertFalse(first.succeeded)

    def test_actual_mqtt_failed_cleanup_retries_without_touching_sealed_audit(self):
        attempts = []
        failure = [True]
        def loop_stop():
            attempts.append(threading.get_ident())
            if failure[0]:
                raise OSError("test Paho owner retained")
        client = SimpleNamespace(disconnect=lambda: None, loop_stop=loop_stop)
        self.manager.mqtt.client = client
        self.manager.mqtt._started = True
        self.manager.request_shutdown()
        first = self.settle()
        self.assertFalse(first.resources_released)
        self.assertIs(self.manager.mqtt._cleanup_client, client)
        before = dict(self.manager.field_log.snapshot())
        failure[0] = False
        self.manager.retry_shutdown()
        result = self.settle()
        self.assertTrue(result.may_exit)
        self.assertTrue(result.succeeded)
        self.assertIsNone(self.manager.mqtt._cleanup_client)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(thread != self.owner for thread in attempts))
        self.assertEqual(self.manager.field_log.snapshot()["dropped_records"], before["dropped_records"])


if __name__ == "__main__":
    unittest.main()
