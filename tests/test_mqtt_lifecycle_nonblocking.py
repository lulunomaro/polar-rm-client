"""Deterministic MQTT lifecycle ownership tests; no network or broker required."""
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.backend.mqtt_backend import RmMqttBackend
from rm_client.backend.referee.registry import STUDENT_PREVIEW_PROFILE


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.identity = kwargs.get('client_id')
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.disconnects = self.stops = self.starts = 0
        self.publications, self.subscriptions = [], []
        self.close_error = False
        self.start_result = 0

    def reconnect_delay_set(self, **kwargs):
        pass

    def connect_async(self, host, port, **kwargs):
        self.endpoint = (host, port)

    def loop_start(self):
        self.starts += 1
        return self.start_result

    def disconnect(self):
        self.disconnects += 1

    def loop_stop(self):
        self.stops += 1
        self.entered.set()
        if not self.release.wait(3):
            raise TimeoutError('test did not release cleanup')
        if self.close_error:
            raise OSError('network owner still alive')

    def subscribe(self, topics):
        self.subscriptions.append(topics)

    def publish(self, topic, payload, **kwargs):
        self.publications.append((topic, bytes(payload)))
        return SimpleNamespace(rc=0)


def until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('lifecycle did not settle')
        time.sleep(.002)


class MqttLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clients = []
        def make_client(*args, **kwargs):
            client = FakeClient(*args, **kwargs)
            self.clients.append(client)
            return client
        paho, mqtt, client = (ModuleType(name) for name in ('paho', 'paho.mqtt', 'paho.mqtt.client'))
        paho.mqtt, mqtt.client = mqtt, client
        client.Client = make_client
        client.CallbackAPIVersion = SimpleNamespace(VERSION2=2)
        client.MQTTv311, client.MQTT_ERR_SUCCESS = 4, 0
        self.modules = patch.dict(sys.modules, {'paho': paho, 'paho.mqtt': mqtt, 'paho.mqtt.client': client})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.backend = RmMqttBackend(mock_output=False)
        self.backend.start()
        self.old = self.backend.client
        self.backend.connected = True
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for client in self.clients:
            client.release.set()
            client.close_error = False
        self.backend.stop()

    def block_and_reconnect(self):
        self.old.release.clear()
        self.backend._schedule_reconnect('test disconnect')
        self.backend._next_reconnect_at = 0
        before = time.perf_counter()
        self.backend.poll_connection()
        self.assertLess(time.perf_counter() - before, .05)
        self.assertTrue(self.old.entered.wait(1))

    def test_poll_does_not_wait_and_repeated_frames_keep_one_cleanup_owner(self):
        self.backend._decoded.append(('old', 'old', {}, 1.0))
        self.block_and_reconnect()
        worker = self.backend._cleanup_thread
        before = time.perf_counter()
        for _ in range(1000):
            self.backend.poll_connection()
        self.assertLess(time.perf_counter() - before, .05)
        self.assertIs(self.backend._cleanup_thread, worker)
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(self.old.stops, 1)
        self.assertIsNone(self.backend.client)
        self.assertFalse(self.backend.connected)
        self.assertEqual(self.backend.drain_decoded(), [])
        self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')
        self.old.release.set()
        until(lambda: self.backend.client is not None)
        self.assertIsNot(self.backend.client, self.old)
        self.assertEqual(len(self.clients), 2)
        self.assertTrue(self.backend._zero_after_reconnect)

    def test_latest_endpoint_and_profile_coalesce_while_old_loop_stops(self):
        self.block_and_reconnect()
        worker = self.backend._cleanup_thread
        before = time.perf_counter()
        for index in range(100):
            self.backend.set_address(f'broker-{index}.invalid', 3333 + index)
        self.backend.set_referee_profile(STUDENT_PREVIEW_PROFILE)
        self.assertLess(time.perf_counter() - before, .1)
        self.assertIs(self.backend._cleanup_thread, worker)
        self.assertEqual(len(self.clients), 1)
        self.old.release.set()
        until(lambda: self.backend.client is not None)
        self.assertEqual(self.backend.client.endpoint, ('broker-99.invalid', 3432))
        self.assertEqual(self.backend.referee.profile_id, STUDENT_PREVIEW_PROFILE)
        self.assertEqual(self.old.stops, 1)
        self.assertEqual(len(self.clients), 2)

    def test_stop_cancels_pending_restart_and_rejects_old_callbacks(self):
        self.block_and_reconnect()
        self.backend.stop(wait=False)
        generation = self.backend.receive_generation
        self.backend._on_connect(self.old, None, {}, 0)
        self.backend._on_connect_fail(self.old, None)
        self.backend._on_disconnect(self.old, None)
        self.backend._on_message(self.old, None, SimpleNamespace(topic='GameStatus', payload=b'\x28\x04'))
        self.assertEqual(self.backend.receive_generation, generation)
        self.assertFalse(self.backend.connected)
        self.assertFalse(self.backend._reconnect_required)
        self.assertFalse(self.backend.publish_estop_zero()[0])
        self.assertEqual(self.old.publications, [])
        self.assertEqual(self.old.subscriptions, [])
        self.old.release.set()
        until(lambda: self.backend._cleanup_thread is None)
        self.assertIsNone(self.backend.client)
        self.assertFalse(self.backend._started)
        self.assertEqual(len(self.clients), 1)

    def test_old_callbacks_cannot_change_replacement_or_replay_motion(self):
        token = self.backend.safety.begin_qualification({'core_kmc'})
        self.block_and_reconnect()
        self.old.release.set()
        until(lambda: self.backend.client is not None)
        current = self.backend.client
        self.backend._on_connect(current, None, {}, 0)
        self.assertTrue(self.backend.connected)
        self.assertEqual(current.publications, [('KeyboardMouseControl', self.backend.referee.codec.encode_keyboard_mouse_control())])
        generation = self.backend.receive_generation
        self.backend._on_disconnect(self.old, None)
        self.backend._on_connect_fail(self.old, None)
        self.backend._on_connect(self.old, None, {}, 0)
        self.backend._on_message(self.old, None, SimpleNamespace(topic='GameStatus', payload=b'\x28\x04'))
        self.assertTrue(self.backend.connected)
        self.assertEqual(self.backend.receive_generation, generation)
        self.assertFalse(self.backend.safety.is_fighting())
        self.assertFalse(self.backend._reconnect_required)
        payload = self.backend.referee.codec.encode_keyboard_mouse_control(keyboard_value=1)
        self.assertFalse(self.backend.publish_topic('KeyboardMouseControl', payload, qualification_token=token.value)[0])
        self.assertEqual(len(current.publications), 1)
        self.assertEqual(self.old.publications, [])

    def test_cleanup_failure_retains_owner_and_never_starts_a_replacement(self):
        self.old.close_error = True
        self.block_and_reconnect()
        self.old.release.set()
        until(lambda: self.backend._cleanup_thread is None)
        self.assertIn('network owner still alive', self.backend.last_error)
        self.assertIs(self.backend._cleanup_client, self.old)
        self.assertFalse(self.backend._started)
        for _ in range(100):
            self.backend.poll_connection()
            self.backend.start()
        self.assertEqual(self.old.stops, 1)
        self.assertEqual(len(self.clients), 1)
        self.assertFalse(self.backend.publish_estop_zero()[0])
        self.old.close_error = False
        self.backend.stop()  # Explicit cleanup retry; never automatic.
        self.assertIsNone(self.backend._cleanup_client)
        self.backend.start()
        self.assertEqual(len(self.clients), 2)

    def test_stop_clears_qualification_and_keeps_operator_estop_latched(self):
        self.backend.safety.begin_qualification({'core_kmc'})
        self.backend.stop()
        self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')
        self.assertIsNone(self.backend.safety._qualification)
        self.backend.start()
        self.backend.safety.trigger_estop()
        self.backend.stop()
        self.assertTrue(self.backend.safety.estop_active)
        self.assertEqual(self.backend.safety.control_state.value, 'ESTOP')

    def test_failed_cleanup_thread_start_is_fail_closed(self):
        with patch('threading.Thread.start', side_effect=RuntimeError('no thread available')):
            self.backend.stop(wait=False)
        self.assertIsNone(self.backend.client)
        self.assertIs(self.backend._cleanup_client, self.old)
        self.assertIsNone(self.backend._cleanup_thread)
        self.assertIn('no thread available', self.backend.last_error)
        self.backend.start()
        self.assertEqual(len(self.clients), 1)
        self.assertFalse(self.backend._started)

    def test_inflight_connect_callback_cannot_publish_into_new_session(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def subscribe(topics):
            entered.set()
            release.wait(2)
        self.old.subscribe = subscribe
        callback = threading.Thread(target=lambda: self.backend._on_connect(self.old, None, {}, 0))
        callback.start()
        self.assertTrue(entered.wait(1))
        self.block_and_reconnect()
        self.old.release.set()
        until(lambda: self.backend.client is not None)
        current = self.backend.client
        release.set()
        callback.join(1)
        self.assertFalse(callback.is_alive())
        self.assertEqual(current.publications, [])
        self.assertTrue(self.backend._zero_after_reconnect)
        self.assertFalse(self.backend.connected)

    def test_inflight_decode_is_discarded_after_detach(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        codec = self.backend.referee.codec
        decode = codec.decode_message
        def blocked_decode(*args):
            entered.set()
            release.wait(2)
            return decode(*args)
        with patch.object(codec, 'decode_message', side_effect=blocked_decode):
            callback = threading.Thread(target=lambda: self.backend._on_message(
                self.old, None, SimpleNamespace(topic='GameStatus', payload=b'\x28\x04')))
            callback.start()
            self.assertTrue(entered.wait(1))
            self.block_and_reconnect()
            self.old.release.set()
            until(lambda: self.backend.client is not None)
            release.set()
            callback.join(1)
        self.assertFalse(callback.is_alive())
        self.assertEqual(self.backend.drain_decoded(), [])
        self.assertEqual(self.backend.drain_received_messages(), [])
        self.assertFalse(self.backend.safety.is_fighting())

    def test_failed_replacement_start_detaches_partial_client(self):
        self.block_and_reconnect()
        # The close worker starts a replacement asynchronously. Simulate a
        # Paho startup failure after construction; partial ownership must end.
        with patch.object(FakeClient, 'loop_start', return_value=1):
            self.old.release.set()
            until(lambda: len(self.clients) == 2 and self.backend._cleanup_thread is None)
        self.assertFalse(self.backend._started)
        self.assertIsNone(self.backend.client)
        self.assertIsNone(self.backend._cleanup_client)
        self.assertEqual(self.clients[-1].stops, 1)
        self.assertIn('failed to start', self.backend.last_error)

    def test_admitted_old_command_cannot_publish_to_locked_replacement(self):
        self.backend.safety.allow_mqtt_publish = True
        token = self.backend.safety.begin_qualification({'core_kmc'})
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        record = self.backend._publish_record
        def blocked_record(*args):
            result = record(*args)
            entered.set()
            release.wait(2)
            return result
        results = []
        payload = self.backend.referee.codec.encode_keyboard_mouse_control(keyboard_value=1)
        with patch.object(self.backend, '_publish_record', side_effect=blocked_record):
            publisher = threading.Thread(target=lambda: results.append(self.backend.publish_topic(
                'KeyboardMouseControl', payload, intent='core_kmc', qualification_token=token.value)))
            publisher.start()
            self.assertTrue(entered.wait(1))
            self.backend.stop()
            self.backend.start()
            current = self.backend.client
            self.backend._on_connect(current, None, {}, 0)
            release.set()
            publisher.join(1)
        self.assertFalse(publisher.is_alive())
        self.assertFalse(results[0][0])
        self.assertIn('session changed', results[0][1])
        self.assertEqual(current.publications, [])
        self.assertEqual(self.old.publications, [])
        self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')

    def test_admitted_command_is_dropped_on_same_session_authority_change(self):
        # Constant timestamps prove that epoch safety does not rely on clock
        # precision. Returning to the same armed state must not revive input.
        for transition in ('estop', 'lock', 'rearm', 'qualification'):
            with self.subTest(transition=transition), patch(
                    'rm_client.backend.safety.time.perf_counter', return_value=100.0):
                self.backend.safety.release_estop()
                self.backend.safety.allow_mqtt_publish = True
                self.backend.safety.arm_match({'core_kmc'})
                admitted_generation = self.backend.safety.control_generation
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                record = self.backend._publish_record
                def blocked_record(*args):
                    result = record(*args)
                    entered.set()
                    release.wait(2)
                    return result
                results = []
                payload = self.backend.referee.codec.encode_keyboard_mouse_control(keyboard_value=1)
                with patch.object(self.backend, '_publish_record', side_effect=blocked_record):
                    publisher = threading.Thread(target=lambda: results.append(
                        self.backend.publish_topic('KeyboardMouseControl', payload)))
                    publisher.start()
                    self.assertTrue(entered.wait(1))
                    if transition == 'estop':
                        self.backend.safety.trigger_estop()
                    elif transition == 'qualification':
                        self.backend.safety.begin_qualification({'core_kmc'})
                    else:
                        self.backend.safety.lock_control('operator lock')
                        if transition == 'rearm':
                            self.backend.safety.arm_match({'core_kmc'})
                    self.assertGreater(self.backend.safety.control_generation, admitted_generation)
                    release.set()
                    publisher.join(1)
                self.assertFalse(publisher.is_alive())
                self.assertIs(self.backend.client, self.old)
                self.assertTrue(self.backend.connected)
                self.assertFalse(results[0][0])
                self.assertIn('control authority changed', results[0][1])
                self.assertEqual(self.old.publications, [])

    def test_fixed_zero_remains_allowed_during_same_session_estop(self):
        self.backend.safety.trigger_estop()
        self.assertTrue(self.backend.publish_estop_zero()[0])
        self.assertEqual(self.old.publications, [
            ('KeyboardMouseControl', self.backend.referee.codec.encode_keyboard_mouse_control())])

    def test_delayed_neutral_is_bound_to_original_session(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        codec = self.backend.referee.codec
        encode = codec.encode_keyboard_mouse_control
        def blocked_encode(*args, **kwargs):
            entered.set()
            release.wait(2)
            return encode(*args, **kwargs)
        results = []
        with patch.object(codec, 'encode_keyboard_mouse_control', side_effect=blocked_encode):
            publisher = threading.Thread(target=lambda: results.append(self.backend.publish_estop_zero()))
            publisher.start()
            self.assertTrue(entered.wait(1))
            self.backend.stop()
            self.backend.start()
            current = self.backend.client
            self.backend.connected = True
            release.set()
            publisher.join(1)
        self.assertFalse(publisher.is_alive())
        self.assertFalse(results[0][0])
        self.assertEqual(current.publications, [])
        self.assertEqual(self.old.publications, [])

    def test_observers_never_hold_the_lifecycle_lock(self):
        for route in ('callback', 'poll', 'address', 'profile'):
            with self.subTest(route=route):
                self.backend.start()
                client = self.backend.client
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                owner = []
                def observer(event, **data):
                    if threading.current_thread() is owner[0]:
                        entered.set()
                        release.wait(2)
                self.backend.event_hook = observer
                if route == 'poll':
                    self.backend._reconnect_required = True
                    self.backend._next_reconnect_at = 0
                operation = {
                    'callback': lambda: self.backend._on_connect_fail(client, None),
                    'poll': self.backend.poll_connection,
                    'address': lambda: self.backend.set_address('changed.invalid', 3334),
                    'profile': lambda: self.backend.set_referee_profile(STUDENT_PREVIEW_PROFILE),
                }[route]
                worker = threading.Thread(target=operation)
                owner.append(worker)
                worker.start()
                self.assertTrue(entered.wait(1))
                before = time.perf_counter()
                self.backend.stop(wait=False)
                self.assertLess(time.perf_counter() - before, .05)
                release.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.backend.event_hook = None
                self.backend.stop()

    def test_input_clear_observer_runs_once_after_authority_is_revoked(self):
        reasons = []
        def observe(reason):
            self.assertIsNone(self.backend.client)
            self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')
            reasons.append(reason)
        self.backend.safety.input_clear_hook = observe
        self.backend.stop()
        self.assertEqual(reasons, ['MQTT stopped; explicit re-arm required'])
        reasons.clear()
        self.backend.safety.lock_control('ordinary caller')
        self.assertEqual(reasons, ['ordinary caller'])
        self.backend.safety.input_clear_hook = None

    def test_callback_input_clear_waits_for_owner_even_during_backoff(self):
        owner = threading.get_ident()
        observed = []
        self.backend.safety.input_clear_hook = lambda reason: observed.append(
            (threading.get_ident(), reason))
        callback = threading.Thread(target=lambda: self.backend._on_disconnect(self.old, None))
        callback.start()
        callback.join(1)
        self.assertFalse(callback.is_alive())
        self.assertEqual(observed, [])
        self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')
        self.assertTrue(self.backend._reconnect_required)
        self.backend._next_reconnect_at = time.perf_counter() + 100
        self.backend.poll_connection()
        self.assertEqual(observed, [(owner, 'MQTT disconnected: None')])
        self.assertIs(self.backend.client, self.old)
        self.backend.poll_connection()
        self.assertEqual(len(observed), 1)
        self.backend.safety.input_clear_hook = None

    def test_cleanup_worker_cannot_steal_pending_owner_notification(self):
        observed = []
        self.backend.safety.input_clear_hook = lambda reason: observed.append(threading.get_ident())
        worker = threading.Thread(target=lambda: self.backend.stop(wait=False))
        worker.start()
        worker.join(1)
        self.assertTrue(self.backend.wait_stopped(1))
        self.assertEqual(observed, [])
        self.backend.poll_connection()  # No reconnect pending: still drains.
        self.assertEqual(observed, [threading.get_ident()])
        self.backend.safety.input_clear_hook = None

    def test_invalidation_errors_cannot_skip_native_cleanup(self):
        for method in ('clear_received_epoch', '_lock_safety_locked', '_flush_input_clear', '_emit_event'):
            with self.subTest(method=method):
                self.backend.start()
                client = self.backend.client
                self.backend.safety.release_estop()
                with patch.object(self.backend, method, side_effect=OSError(method)):
                    with self.assertRaises(OSError):
                        self.backend.stop(wait=False)
                self.assertTrue(self.backend.wait_stopped(1))
                self.assertEqual((client.disconnects, client.stops), (1, 1))
                self.assertIsNone(self.backend.client)
                self.assertIsNone(self.backend._cleanup_client)

    def test_stop_from_network_callback_cannot_join_itself_indirectly(self):
        finished = threading.Event()
        callback = threading.Thread(target=lambda: (self.backend.stop(), finished.set()))
        self.old._thread = callback
        original_stop = self.old.loop_stop
        def join_callback():
            original_stop()
            callback.join(1)
            self.assertFalse(callback.is_alive())
        self.old.loop_stop = join_callback
        callback.start()
        self.assertTrue(finished.wait(.5))
        callback.join(1)
        until(lambda: self.backend._cleanup_thread is None)
        self.assertIsNone(self.backend.client)

    def test_final_stop_waits_outside_lifecycle_lock(self):
        self.block_and_reconnect()
        finished = threading.Event()
        final = threading.Thread(target=lambda: (self.backend.stop(), finished.set()))
        final.start()
        self.assertFalse(finished.wait(.03))
        # A callback from the old client cannot deadlock on the final waiter.
        self.backend._on_disconnect(self.old, None)
        self.old.release.set()
        self.assertTrue(finished.wait(1))
        final.join(1)
        self.assertIsNone(self.backend.client)
        self.assertEqual(len(self.clients), 1)


if __name__ == '__main__':
    unittest.main()
