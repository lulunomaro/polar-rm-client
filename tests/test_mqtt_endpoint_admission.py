"""Live MQTT setting admission, using production lifecycle and no network.

Malformed values below are direct registry/API writes. The separate slider and
profile tests establish what those ordinary producers can actually supply.
"""
from pathlib import Path
import math
import os
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client import serial_ports
from rm_client.backend.manager import BackendManager
from rm_client.backend.mqtt_endpoint import admit_mqtt_endpoint
from rm_client.configuration.registry import apply_profile_modules, prepare_registry_modules
from rm_client.model import build_default_registry


class OfflineClient:
    """Replace only the Paho boundary, retaining actual backend start/stop."""

    def __init__(self):
        self.endpoint = None
        self.started = False
        self.stops = 0
        self.disconnects = 0
        self.close_error = False
        self.closing = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def reconnect_delay_set(self, **kwargs):
        pass

    def connect_async(self, host, port, **kwargs):
        self.endpoint = (host, port)

    def loop_start(self):
        self.started = True
        return 0

    def disconnect(self):
        self.disconnects += 1

    def loop_stop(self):
        self.stops += 1
        self.closing.set()
        if not self.release.wait(2):
            raise TimeoutError('test did not release old network owner')
        if self.close_error:
            raise OSError('old network owner still held')
        self.started = False


def until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('MQTT lifecycle did not settle')
        time.sleep(.002)


class MqttEndpointAdmissionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.clients = []
        def make_client(*args, **kwargs):
            client = OfflineClient()
            self.clients.append(client)
            return client
        paho, mqtt, client = (ModuleType(name) for name in ('paho', 'paho.mqtt', 'paho.mqtt.client'))
        paho.mqtt, mqtt.client = mqtt, client
        client.Client = make_client
        client.CallbackAPIVersion = SimpleNamespace(VERSION2=2)
        client.MQTTv311 = 4
        modules = patch.dict(sys.modules, {'paho': paho, 'paho.mqtt': mqtt, 'paho.mqtt.client': client})
        modules.start()
        self.addCleanup(modules.stop)
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': directory.name}), patch.object(
                BackendManager, '_build_commit', return_value='TEST'):
            self.manager = BackendManager(workspace=directory.name)
        self.addCleanup(self.cleanup)
        with patch.object(serial_ports, 'port_snapshot', return_value=[]):
            self.registry = build_default_registry()
        self.module = self.registry.get('MQTTLink')
        self.host = self.module.get_setting('Host')
        self.port = self.module.get_setting('Port')
        self.backend = self.manager.mqtt

    def cleanup(self):
        self.manager.input_clear_hook = None
        for client in self.clients:
            client.release.set()
            client.close_error = False
        self.manager.shutdown()

    def sync(self):
        return self.manager.sync_mqtt_link(self.registry)

    def endpoint(self):
        return self.backend.host, self.backend.port

    def test_reported_malformed_ports_are_repaired_once_and_repeat_safely(self):
        for raw in ('not-a-port', '3333.0', 3333.5, float('inf')):
            with self.subTest(raw=raw):
                self.port.value = raw
                generation = self.backend.receive_generation
                self.assertIn('MQTT 端口非法', self.sync())
                self.assertEqual(self.endpoint(), ('127.0.0.1', 3333))
                self.assertIs(type(self.port.value), int)
                self.assertEqual(self.port.value, 3333)
                self.assertEqual(self.backend.receive_generation, generation)
                for _ in range(20):
                    self.assertEqual(self.sync(), '')
                self.assertEqual(self.clients, [])
                self.assertFalse(self.backend._started)

    def test_invalid_port_matrix_never_truncates_or_coerces_other_value_types(self):
        invalid = (None, True, False, {}, [], object(), complex(3333), b'3333',
                   '', '1e3', 'nan', 'inf', '3333.5', -1, 0, 999, 10000,
                   10 ** 1000, 3333.0000001, 9999.9, float('nan'),
                   float('inf'), float('-inf'), 1e100)
        for raw in invalid:
            with self.subTest(raw=raw):
                self.port.value = raw
                self.assertIn('MQTT 端口非法', self.sync())
                self.assertEqual(self.endpoint(), ('127.0.0.1', 3333))
                self.assertEqual(self.port.value, 3333)
                self.assertIs(type(self.port.value), int)
        self.assertEqual(self.clients, [])

    def test_healthy_integer_forms_are_canonical_and_keep_live_ui_range(self):
        for raw, expected in ((1000, 1000), (9999, 9999), (3334.0, 3334),
                              ('3335', 3335), (' 3336 ', 3336), ('+3337', 3337),
                              ('3_338', 3338)):
            with self.subTest(raw=raw):
                self.port.value = raw
                self.assertEqual(self.sync(), '')
                self.assertEqual(self.backend.port, expected)
                self.assertEqual(self.port.value, expected)
                self.assertIs(type(self.port.value), int)
        self.assertEqual(self.clients, [])

    def test_host_whitespace_is_rejected_and_edges_are_trimmed(self):
        for raw in ('', '   ', '\t\n', 'broker name', 'broker\tname',
                    'broker\nname', 'broker\u2003name', None, False, [], {}):
            with self.subTest(raw=raw):
                self.host.value = raw
                self.assertIn('MQTT 地址', self.sync())
                self.assertEqual(self.backend.host, '127.0.0.1')
                self.assertEqual(self.host.value, self.backend.host)
        for raw in (' broker.invalid ', '\t127.0.0.1\n', '::1'):
            self.host.value = raw
            self.assertEqual(self.sync(), '')
            self.assertEqual(self.host.value, raw.strip())
            self.assertEqual(self.backend.host, self.host.value)
        self.assertEqual(self.clients, [])

    def test_valid_field_applies_while_other_field_keeps_current_default(self):
        self.host.value, self.port.value = 'broker.invalid', 'bad'
        self.assertIn('MQTT 端口非法', self.sync())
        self.assertEqual(self.endpoint(), ('broker.invalid', 3333))
        self.host.value, self.port.value = ' ', 4444.0
        self.assertIn('MQTT 地址', self.sync())
        self.assertEqual(self.endpoint(), ('broker.invalid', 4444))
        self.assertEqual((self.host.value, self.port.value), self.endpoint())

    def test_ordinary_slider_produces_finite_integral_values_within_catalog_range(self):
        self.assertEqual((self.port.min_value, self.port.max_value, self.port.step), (1000, 9999, 1))
        for ratio in (-1, 0, .0001, .123456, .5, .99999, 1, 2):
            self.port.set_from_ratio(ratio)
            raw = self.port.value
            self.assertTrue(math.isfinite(raw))
            self.assertEqual(raw, int(raw))
            self.assertTrue(1000 <= raw <= 9999)
            self.assertEqual(self.sync(), '')
            self.assertEqual(self.port.display_value(), str(self.backend.port))

    def test_profile_loader_preserves_integral_floats_but_fraction_needs_endpoint_admission(self):
        for raw in (4444, 4444.0, 4444.5):
            data = {'modules': {'MQTTLink': {'settings': {'Port': raw}}}}
            modules = prepare_registry_modules(self.registry, data)
            apply_profile_modules(self.registry, modules)
            self.assertEqual(self.port.value, float(raw))
            previous = self.backend.port
            note = self.sync()
            if raw == 4444.5:
                self.assertIn('MQTT 端口非法', note)
                self.assertEqual(self.backend.port, previous)
            else:
                self.assertEqual(note, '')
                self.assertEqual(self.backend.port, 4444)
            self.assertIs(type(self.port.value), int)
        for raw in ('3333.0', 'not-a-port', float('inf'), float('nan')):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                prepare_registry_modules(self.registry, {'modules': {'MQTTLink': {'settings': {'Port': raw}}}})

    def test_reentrant_setting_edit_is_preserved_until_next_sync(self):
        self.host.value, self.port.value = 'first.invalid', 4444.0
        def edit_later_request(reason):
            self.host.value, self.port.value = 'latest.invalid', 5555.0
            self.manager.input_clear_hook = None
        self.manager.input_clear_hook = edit_later_request
        self.sync()
        self.assertEqual(self.endpoint(), ('first.invalid', 4444))
        self.assertEqual((self.host.value, self.port.value), ('latest.invalid', 5555.0))
        self.sync()
        self.assertEqual(self.endpoint(), ('latest.invalid', 5555))
        self.assertEqual((self.host.value, self.port.value), self.endpoint())
        self.assertIs(type(self.port.value), int)

    def test_nested_sync_owns_latest_endpoint_and_validation_note(self):
        self.host.value, self.port.value = 'first.invalid', 4444
        notes = []
        def admit_later_request(reason):
            self.manager.input_clear_hook = None
            self.host.value, self.port.value = 'latest.invalid', 'bad'
            notes.append(self.sync())
        self.manager.input_clear_hook = admit_later_request
        self.sync()
        self.assertEqual(self.endpoint(), ('latest.invalid', 4444))
        self.assertEqual((self.host.value, self.port.value), self.endpoint())
        self.assertIn('MQTT 端口非法', notes[0])
        self.assertEqual(self.backend.last_error, notes[0])

    def test_invalid_live_input_keeps_owner_and_recovery_coalesces_behind_close(self):
        self.backend.start()
        old = self.backend.client
        self.backend.connected = True
        generation = self.backend.receive_generation
        for _ in range(20):
            self.port.value = float('inf')
            self.assertIn('MQTT 端口非法', self.sync())
        self.assertIs(self.backend.client, old)
        self.assertEqual(self.backend.receive_generation, generation)
        self.assertEqual(old.stops, 0)
        old.release.clear()
        self.backend._decoded.append(('old', 'old', {}, 1.0))
        self.host.value, self.port.value = 'first.invalid', 4444.0
        self.sync()
        self.assertTrue(old.closing.wait(1))
        self.assertIsNone(self.backend.client)
        self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')
        self.assertEqual(self.backend.drain_decoded(), [])
        self.port.value = float('nan')
        self.assertIn('MQTT 端口非法', self.sync())
        self.host.value, self.port.value = 'latest.invalid', '5555'
        for _ in range(20):
            self.sync()
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(old.stops, 1)
        old.release.set()
        until(lambda: self.backend.client is not None)
        self.assertEqual(self.backend.client.endpoint, ('latest.invalid', 5555))
        self.assertEqual(len(self.clients), 2)
        self.assertFalse(old.started)
        self.assertEqual((self.host.value, self.port.value), self.backend.client.endpoint)

    def test_failed_close_owner_requires_explicit_retry_after_repaired_setting(self):
        self.backend.start()
        old = self.backend.client
        old.close_error = True
        self.port.value = 4444
        self.sync()
        until(lambda: self.backend._cleanup_thread is None)
        for _ in range(20):
            self.port.value = float('inf')
            self.sync()
            self.backend.start()
        self.assertIs(self.backend._cleanup_client, old)
        self.assertIsNone(self.backend.client)
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(old.stops, 1)
        self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')
        old.close_error = False
        self.backend.stop()
        self.backend.start()
        self.assertEqual(self.backend.client.endpoint, ('127.0.0.1', 4444))
        self.assertEqual(len(self.clients), 2)

    def test_equal_value_replacement_is_a_later_request_not_writeback_permission(self):
        initial_host = ''.join(('same', '.invalid'))
        later_host = ''.join(('same.', 'invalid'))
        initial_port, later_port = float('4444'), float('4444')
        self.assertIsNot(initial_host, later_host)
        self.assertIsNot(initial_port, later_port)
        self.host.value, self.port.value = initial_host, initial_port
        def edit_equal_values(reason):
            self.manager.input_clear_hook = None
            self.host.value, self.port.value = later_host, later_port
        self.manager.input_clear_hook = edit_equal_values
        self.sync()
        self.assertIs(self.host.value, later_host)
        self.assertIs(self.port.value, later_port)
        self.assertEqual(self.endpoint(), ('same.invalid', 4444))
        self.sync()
        self.assertIs(type(self.port.value), int)
        self.assertEqual(self.port.value, 4444)

    def test_callback_can_leave_malformed_later_request_without_breaking_outer_writeback(self):
        self.host.value, self.port.value = 'first.invalid', 4444
        later = float('nan')
        def edit_later_request(reason):
            self.manager.input_clear_hook = None
            self.port.value = later
        self.manager.input_clear_hook = edit_later_request
        self.sync()
        self.assertIs(self.port.value, later)
        self.assertEqual(self.endpoint(), ('first.invalid', 4444))
        self.assertIn('MQTT 端口非法', self.sync())
        self.assertEqual(self.port.value, 4444)

    def test_superseded_outer_request_returns_note_without_overwriting_shared_diagnostic(self):
        self.backend.last_error = 'existing diagnostic'
        self.host.value, self.port.value = 'first.invalid', 'bad'
        def edit_later_request(reason):
            self.manager.input_clear_hook = None
            self.host.value, self.port.value = 'latest.invalid', 5555.0
        self.manager.input_clear_hook = edit_later_request
        self.assertIn('MQTT 端口非法', self.sync())
        self.assertEqual(self.backend.last_error, 'existing diagnostic')
        self.assertEqual((self.host.value, self.port.value), ('latest.invalid', 5555.0))
        self.assertEqual(self.sync(), '')
        self.assertEqual(self.backend.last_error, '')
        self.assertEqual(self.endpoint(), ('latest.invalid', 5555))

    def test_failed_owner_observer_stays_blocked_until_clear_is_repaired(self):
        def failed_observer(reason):
            raise RuntimeError('injected observer failure')
        self.manager.input_clear_hook = failed_observer
        self.host.value, self.port.value = 'first.invalid', 4444.0
        self.assertEqual(self.sync(), '')  # Safety records observer errors; it does not throw them.
        self.assertEqual(self.manager.safety.input_clear_failure_count, 1)
        self.assertIn('injected observer failure', self.manager._input_clear_error)
        self.assertEqual(self.endpoint(), ('first.invalid', 4444))
        self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')
        self.assertTrue(self.manager._input_clear_incomplete())
        self.port.value = float('inf')
        self.assertEqual(self.sync(), 'Backend lifecycle transition pending or failed')
        self.assertEqual(self.port.value, float('inf'))
        self.manager.input_clear_hook = None
        self.manager.poll_lifecycle()
        self.assertFalse(self.manager._input_clear_incomplete())
        self.assertIn('MQTT 端口非法', self.sync())
        self.assertEqual(self.port.value, 4444)
        self.assertEqual(self.clients, [])

    def test_unsupported_profile_is_not_hidden_by_endpoint_admission(self):
        self.module.get_setting('Referee Profile').value = 'unsupported-profile'
        self.port.value = float('inf')
        note = self.sync()
        self.assertIn('unsupported-profile', note)
        self.assertEqual(self.port.value, float('inf'))
        self.assertEqual(self.endpoint(), ('127.0.0.1', 3333))
        self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')
        self.assertEqual(self.clients, [])

    def test_tcp_api_and_scenario_ports_outside_slider_range_keep_their_contract(self):
        for port in (18830, 65535):
            with self.subTest(port=port):
                self.backend.stop()
                self.host.value, self.port.value = 'api.invalid', port
                config = self.manager._config_from_registry(self.manager.config, self.registry)
                self.assertEqual(config.connection.mqtt_port, port)
                self.assertTrue(self.backend.set_address('api.invalid', port))
                self.backend.start()
                current = self.backend.client
                self.assertEqual(current.endpoint, ('api.invalid', port))
                generation = self.backend.receive_generation
                self.port.value = 'bad'
                self.assertIn('MQTT 端口非法', self.sync())
                self.assertEqual(self.endpoint(), ('api.invalid', port))
                self.assertEqual(self.port.value, port)
                self.assertIs(self.backend.client, current)
                self.assertEqual(current.stops, 0)
                self.assertEqual(self.backend.receive_generation, generation)
                # The pre-existing live-setting range still reports this
                # value; it must never replace a valid API-owned endpoint.
                self.assertIn('MQTT 端口非法', self.sync())
                self.assertIs(self.backend.client, current)
                self.assertEqual(self.endpoint(), ('api.invalid', port))

    def test_missing_registry_module_or_endpoint_setting_remains_noop(self):
        self.assertEqual(self.manager.sync_mqtt_link(None), '')
        self.assertEqual(self.manager.sync_mqtt_link({}), '')
        self.module.settings.remove(self.port)
        self.host.value = 'unused.invalid'
        self.assertEqual(self.sync(), '')
        self.assertEqual(self.endpoint(), ('127.0.0.1', 3333))
        self.assertEqual(self.clients, [])

    def test_transition_gate_does_not_parse_or_repair_settings_while_blocked(self):
        self.manager.request_shutdown()
        self.port.value = float('inf')
        self.assertEqual(self.sync(), 'Backend lifecycle transition pending or failed')
        self.assertEqual(self.port.value, float('inf'))
        self.assertEqual(self.clients, [])


class MqttEndpointValueTests(unittest.TestCase):
    def test_rejection_keeps_current_owner_endpoint_even_outside_live_slider_range(self):
        result = admit_mqtt_endpoint('', float('nan'), current_host='current.invalid',
                                     current_port=65535)
        self.assertEqual((result.host, result.port), ('current.invalid', 65535))
        self.assertIn('MQTT 地址', result.note)
        self.assertIn('MQTT 端口非法', result.note)

    def test_foreign_objects_are_not_asked_to_convert_or_compare(self):
        class ForeignValue:
            def __str__(self):
                raise AssertionError('foreign string conversion')
            def __int__(self):
                raise AssertionError('foreign integer conversion')
            def __eq__(self, other):
                raise AssertionError('foreign equality')
            def __bool__(self):
                raise AssertionError('foreign truth test')
        result = admit_mqtt_endpoint(ForeignValue(), ForeignValue(),
                                     current_host='current.invalid', current_port=3333)
        self.assertEqual((result.host, result.port), ('current.invalid', 3333))
        self.assertIn('MQTT 端口非法', result.note)

    def test_port_numeric_subclasses_do_not_supply_conversion_hooks(self):
        class ForeignFloat(float):
            def __int__(self):
                raise AssertionError('foreign integer conversion')
        result = admit_mqtt_endpoint('current.invalid', ForeignFloat(3333),
                                     current_host='current.invalid', current_port=4444)
        self.assertEqual(result.port, 4444)
        self.assertIn('MQTT 端口非法', result.note)


if __name__ == '__main__':
    unittest.main()
