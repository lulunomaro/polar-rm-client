"""Native MQTT cleanup evidence, including the installed Paho close path."""
import threading
import unittest
from types import SimpleNamespace
from paho.mqtt.client import MQTT_ERR_INVAL, MQTT_ERR_NO_CONN

from rm_client.backend.mqtt_cleanup import MqttClientResources
from rm_client.backend.mqtt_backend import RmMqttBackend


class Socket:
    def __init__(self, *, fail=False):
        self.fd = 17
        self.fail = fail
        self.closes = 0
    def fileno(self):
        return self.fd
    def close(self):
        self.closes += 1
        if self.fail:
            raise OSError('native socket close failure')
        self.fd = -1


class Client:
    def __init__(self, sock=None):
        self._sock = sock
        self._thread = None
        self._sockpairR = self._sockpairW = None
        self.disconnect_error = False
        self.stops = 0
    def disconnect(self):
        if self.disconnect_error:
            raise OSError('disconnect failure')
        return MQTT_ERR_NO_CONN
    def loop_stop(self):
        self.stops += 1
        return MQTT_ERR_INVAL  # No loop thread exists.


class MqttCleanupOwnershipTests(unittest.TestCase):
    def test_invalid_loop_stop_is_benign_only_with_confirmed_socket_release(self):
        socket = Socket(fail=True)
        client = Client(socket)
        owner = MqttClientResources(client)
        with self.assertRaisesRegex(OSError, 'native socket'):
            owner.close()
        self.assertIs(client._sock, socket)
        socket.fail = False
        owner.close()
        self.assertIsNone(client._sock)
        self.assertEqual(socket.fileno(), -1)

    def test_other_nonzero_loop_result_still_requires_actual_release_evidence(self):
        socket = Socket()
        client = Client(socket)
        client.loop_stop = lambda: 99  # Future/unknown code is not success proof.
        owner = MqttClientResources(client)
        owner.close()
        self.assertEqual(socket.fileno(), -1)
        self.assertIsNone(client._sock)

    def test_disconnect_error_still_joins_loop_and_closes_native_handles(self):
        socket, wake_reader, wake_writer = Socket(), Socket(), Socket()
        client = Client(socket)
        client._sockpairR, client._sockpairW = wake_reader, wake_writer
        client.disconnect_error = True
        owner = MqttClientResources(client)
        with self.assertRaisesRegex(OSError, 'disconnect failure'):
            owner.close()
        self.assertEqual(client.stops, 1)
        self.assertTrue(all(handle.fileno() == -1 for handle in (socket, wake_reader, wake_writer)))
        client.disconnect_error = False
        owner.close()
        self.assertEqual([handle.closes for handle in (socket, wake_reader, wake_writer)], [1, 1, 1])

    def test_live_original_thread_cannot_be_erased_to_certify_release(self):
        release = threading.Event()
        worker = threading.Thread(target=release.wait)
        worker.start()
        self.addCleanup(lambda: (release.set(), worker.join(1)))
        socket = Socket()
        client = Client(socket)
        client._thread = worker
        def detach_thread():
            client._thread = None
            return 0
        client.loop_stop = detach_thread
        owner = MqttClientResources(client)
        with self.assertRaisesRegex(OSError, 'thread remained alive'):
            owner.close()
        self.assertEqual(socket.closes, 0)
        with self.assertRaisesRegex(OSError, 'thread remained alive'):
            owner.close()  # Explicit retry must also remember the erased thread.
        self.assertEqual(socket.closes, 0)
        release.set()
        worker.join(1)
        owner.close()
        self.assertEqual(socket.fileno(), -1)

    def test_socket_created_during_stop_is_retained_after_vendor_detaches_it(self):
        socket = Socket(fail=True)
        client = Client()
        owner = MqttClientResources(client)
        owner.attach_socket_observers()
        def stop():
            client.on_socket_open(client, None, socket)
            client.on_socket_close(client, None, socket)
            return 0
        client.loop_stop = stop
        with self.assertRaisesRegex(OSError, 'native socket'):
            owner.close()
        self.assertIsNone(client._sock)
        socket.fail = False
        client.loop_stop = lambda: MQTT_ERR_INVAL
        owner.close()
        self.assertEqual(socket.fileno(), -1)

    def test_wakeup_socket_failure_blocks_backend_replacement_until_explicit_retry(self):
        socket = Socket(fail=True)
        client = Client()
        client._sockpairR = socket
        backend = RmMqttBackend()
        backend.client = client
        backend._started = True
        backend.stop()
        self.assertFalse(backend.wait_stopped(1))
        self.assertIs(backend._cleanup_client, client)
        backend.start()
        self.assertIsNone(backend.client)
        socket.fail = False
        backend.stop()
        self.assertTrue(backend.wait_stopped(1))
        self.assertEqual(socket.fileno(), -1)

    def test_actual_paho_detach_before_native_close_does_not_lose_socket(self):
        import paho.mqtt.client as paho
        client = paho.Client(paho.CallbackAPIVersion.VERSION2, reconnect_on_failure=False)
        owner = MqttClientResources(client)
        owner.attach_socket_observers()
        socket = Socket(fail=True)
        client._sock = socket
        with self.assertRaisesRegex(OSError, 'native socket'):
            client._sock_close()
        self.assertIsNone(client._sock)  # Real vendor path erased its field.
        with self.assertRaisesRegex(OSError, 'native socket'):
            owner.close()  # Real disconnect/loop_stop; no network thread exists.
        socket.fail = False
        owner.close()
        self.assertEqual(socket.fileno(), -1)

    def test_failed_old_handle_inspection_cannot_drop_new_callback_owner(self):
        broken = SimpleNamespace(fileno=lambda: (_ for _ in ()).throw(OSError('inspection failure')),
                                 close=lambda: None)
        socket = Socket(fail=True)
        owner = MqttClientResources(Client())
        owner.observe_socket(None, None, broken)
        owner.observe_socket(None, None, socket)
        with self.assertRaisesRegex(OSError, 'inspection failure'):
            owner.close()
        self.assertEqual(socket.closes, 1)
        self.assertIn(id(socket), owner._sockets)


if __name__ == '__main__':
    unittest.main()
