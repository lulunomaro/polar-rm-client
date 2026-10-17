"""Independent failed-owner retries compose without weakening final exit evidence."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import paho.mqtt.client as paho

from rm_client.backend.manager import BackendManager
from rm_client.backend.mqtt_cleanup import MqttClientResources
from rm_client.transports import _UDPVideoSession
from tests.test_mqtt_cleanup_ownership import Socket as MqttSocket
from tests.test_udp_stop_nonblocking import Socket as UdpSocket
from tests.test_video_close_ownership import Process, installed_decoder


class CompositeShutdownRetryTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root.name}), patch.object(
                BackendManager, '_build_commit', return_value='TEST'):
            self.backend = BackendManager(workspace=root.name)
        self.addCleanup(self.backend.shutdown)
        self.assertTrue(self.backend.native_decoder.wait_closed())
        self.assertTrue(self.backend.udp.wait_closed())

    def settle(self):
        deadline = time.monotonic() + 3
        result = None
        while self.backend.lifecycle_pending:
            completion = self.backend.poll_lifecycle()
            if completion is not None:
                result = completion
            if time.monotonic() >= deadline:
                self.fail('synthetic multi-owner teardown did not settle')
            time.sleep(.001)
        self.assertIsNotNone(result)
        return result

    def test_partial_retries_keep_unresolved_owners_and_do_not_replay_successes(self):
        backend = self.backend
        native, hevc = Process(), Process()
        native.stdout.fail_close = hevc.stdout.fail_close = True
        backend.native_decoder, native_session = installed_decoder(native)
        backend.udp.h265_decoder, hevc_session = installed_decoder(hevc)
        udp = UdpSocket()
        udp.fail_close = True
        backend.udp._session = _UDPVideoSession(sock=udp)
        backend.udp._socket = udp
        backend.udp.running = backend.udp.decode_h265 = True
        backend._active_video_source = 'udp hevc'

        client = paho.Client(paho.CallbackAPIVersion.VERSION2, reconnect_on_failure=False)
        owner = MqttClientResources(client)
        owner.attach_socket_observers()
        mqtt = MqttSocket(fail=True)
        client._sock = mqtt
        backend.mqtt.client = client
        backend.mqtt._client_resources = owner
        backend.mqtt._started = True

        def release_synthetic_failures():
            native.stdout.fail_close = hevc.stdout.fail_close = False
            udp.fail_close = mqtt.fail = False
            udp.close_release.set()
            udp.read_release.set()
            if backend.lifecycle_closed and backend.shutdown_completion is not None:
                if not backend.shutdown_completion.may_exit:
                    backend.retry_shutdown()
                    self.settle()
        self.addCleanup(release_synthetic_failures)

        # No transport worker or subprocess may be opened by this test. The
        # installed Paho client uses its actual disconnect/loop_stop close path.
        with patch('socket.socket', side_effect=AssertionError('network access forbidden')), \
             patch('rm_client.backend.video_backend.subprocess.Popen',
                   side_effect=AssertionError('process creation forbidden')):
            backend.set_active_video_source('Synthetic')
            backend.request_shutdown()
            first = self.settle()
            self.assertFalse(first.may_exit)
            self.assertFalse(first.resources_released)
            self.assertEqual({failure.resource for failure in first.failures},
                             {'mqtt', 'udp', 'native-decoder'})
            self.assertIs(backend.mqtt._cleanup_client, client)
            self.assertIs(backend.udp._session.sock, udp)
            self.assertIn(native_session, backend.native_decoder._retired_sessions)
            self.assertIn(hevc_session, backend.udp.h265_decoder._retired_sessions)

            native.stdout.fail_close = mqtt.fail = False
            backend.retry_shutdown()
            second = self.settle()
            self.assertFalse(second.may_exit)
            self.assertEqual({failure.resource for failure in second.failures}, {'udp'})
            self.assertIsNone(backend.mqtt._cleanup_client)
            self.assertTrue(backend.native_decoder.cleanup_complete)
            self.assertIs(backend.udp._session.sock, udp)
            self.assertIn(hevc_session, backend.udp.h265_decoder._retired_sessions)

            hevc.stdout.fail_close = udp.fail_close = False
            with patch.object(backend.mqtt, 'wait_stopped', wraps=backend.mqtt.wait_stopped) as mqtt_wait, \
                 patch.object(backend.native_decoder, 'stop', wraps=backend.native_decoder.stop) as native_stop:
                backend.retry_shutdown()
                final = self.settle()
            mqtt_wait.assert_not_called()
            native_stop.assert_not_called()
            self.assertTrue(final.may_exit, final.failures)
            self.assertTrue(final.succeeded, final.failures)
            self.assertTrue(backend.udp.cleanup_complete)
            self.assertEqual(mqtt.fileno(), -1)
            self.assertFalse(udp.is_open)
            self.assertIs(backend.initial_shutdown_completion, first)
            self.assertFalse(first.may_exit)
            self.assertEqual((first.attempt, second.attempt, final.attempt), (1, 2, 3))


if __name__ == '__main__':
    unittest.main()
