"""Lifecycle orchestration works with explicit ports, without a manager or UI."""
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rm_client.backend.lifecycle import ScenarioRequest
from rm_client.backend.lifecycle_controller import BackendLifecycleController
from rm_client.backend.safety import OfficialSafety
from rm_client.backend.serial_backend import RmSerialBackend
from rm_client.backend.teardown_plan import TeardownResources
from rm_client.lifecycle import TeardownTask


class Audit:
    def __init__(self, record):
        self.record = record
        self.closing = self.closed = False
        self.loss = 0
        self.close_calls = 0
    def emit(self, event, **payload):
        self.record(event)
    def close(self, timeout):
        self.close_calls += 1
        self.record('audit.close')
        self.closing = self.closed = True
        return not self.loss
    def snapshot(self):
        return {'pending_records': 0, 'dropped_records': self.loss, 'write_errors': 0,
                'closed': self.closed, 'closing': self.closing, 'worker_active': False}


class BackendLifecycleControllerTests(unittest.TestCase):
    def setUp(self):
        self.owner = threading.get_ident()
        self.events = []
        self.activated = []
        self.record = lambda event: self.events.append((event, threading.get_ident()))
        self.safety = OfficialSafety()
        self.safety.input_clear_hook = lambda reason: self.record('safety.revoke')
        self.audit = Audit(self.record)
        self.mqtt_failed = False
        self.owners = TeardownResources(
            safety=self.safety, logger=self.audit,
            mqtt=SimpleNamespace(connected=True, mock_output=False, last_error='MQTT close failed',
                publish_estop_zero=lambda: self.record('mqtt.neutral'),
                stop=lambda **kwargs: self.record('mqtt.invalidate'),
                wait_stopped=self.wait_mqtt),
            serial=SimpleNamespace(request_estop=lambda: self.record('serial.revoke'),
                stop=lambda: self.record('serial.close'), cleanup_complete=True),
            polar=SimpleNamespace(request_estop=lambda: self.record('polar.revoke'),
                stop=lambda: self.record('polar.close'), cleanup_complete=True),
            udp=SimpleNamespace(stop=lambda: self.record('udp.close'), wait_closed=lambda: True),
            native_decoder=SimpleNamespace(stop=lambda: self.record('decoder.close'), wait_closed=lambda: True),
            controller_angles=SimpleNamespace(close=lambda: self.record('angles.revoke'),
                wait_closed=lambda: (self.record('angles.close') or True)),
        )
        self.controller = BackendLifecycleController(
            invalidate_runtime=lambda: self.record('runtime.invalidate'),
            stopped_runtime=lambda: self.record('runtime.stopped'),
            activate_scenario=self.activate)

    def wait_mqtt(self):
        self.record('mqtt.close')
        return not self.mqtt_failed

    def activate(self, request):
        self.assertTrue(self.controller.applying)
        self.activated.append((request, threading.get_ident()))

    def scenario(self, key):
        return ScenarioRequest(key, object(), False, '3', 'profile', 'serial', '', 50570, False)

    def test_preflight_rejection_has_no_safety_or_resource_side_effects(self):
        for extra in ((TeardownTask('mqtt', lambda: None),),
                      tuple(TeardownTask(f'extra-{index}', lambda: None) for index in range(11))):
            with self.assertRaises(ValueError):
                self.controller.request_shutdown(self.owners, teardown_tasks=extra)
            self.assertEqual(self.events, [])
            self.assertFalse(self.safety.estop_active)
            self.assertFalse(self.controller.final)

    def test_neutral_admission_precedes_worker_close_and_audit_follows_completion(self):
        self.controller.request_shutdown(self.owners)
        result = self.controller.wait()
        names = [event for event, _ in self.events]
        self.assertEqual(names[:6], ['safety.revoke', 'serial.revoke', 'polar.revoke',
                                    'angles.revoke', 'mqtt.neutral', 'mqtt.invalidate'])
        self.assertEqual(names[6], 'runtime.invalidate')
        native_close = {'mqtt.close', 'udp.close', 'serial.close', 'polar.close', 'decoder.close', 'angles.close'}
        self.assertTrue(all(thread != self.owner for event, thread in self.events if event in native_close))
        self.assertTrue(all(thread == self.owner for event, thread in self.events
                            if event in {'safety.revoke', 'runtime.invalidate', 'runtime.stopped', 'backend_shutdown'}))
        self.assertLess(max(names.index(event) for event in native_close), names.index('runtime.stopped'))
        self.assertLess(names.index('runtime.stopped'), names.index('backend_shutdown'))
        self.assertLess(names.index('backend_shutdown'), names.index('audit.close'))
        self.assertTrue(result.may_exit)
        self.assertTrue(result.succeeded)

    def block_udp(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def close():
            self.record('udp.close')
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test did not release UDP')
        self.owners.udp.stop = close
        return entered, release

    def test_latest_scenario_is_activated_once_only_by_owner_completion(self):
        entered, release = self.block_udp()
        first = self.controller.request_scenario(self.scenario('old'), self.owners)
        self.assertTrue(entered.wait(1))
        latest = self.scenario('latest')
        second = self.controller.request_scenario(latest, self.owners)
        self.assertGreater(second, first)
        self.assertEqual(self.activated, [])
        self.assertEqual([name for name, _ in self.events].count('runtime.invalidate'), 1)
        release.set()
        result = self.controller.wait()
        self.assertEqual(result.generation, second)
        self.assertEqual(self.activated, [(latest, self.owner)])
        self.assertFalse(self.controller.applying)

    def test_final_request_supersedes_pending_activation_without_new_close_owner(self):
        entered, release = self.block_udp()
        self.controller.request_scenario(self.scenario('old'), self.owners)
        self.assertTrue(entered.wait(1))
        self.controller.request_shutdown(self.owners)
        with self.assertRaises(RuntimeError):
            self.controller.request_scenario(self.scenario('forbidden'), self.owners)
        release.set()
        self.assertTrue(self.controller.wait().may_exit)
        self.assertEqual(self.activated, [])
        self.assertEqual([name for name, _ in self.events].count('udp.close'), 1)

    def test_retry_reuses_only_failed_owner_and_preserves_first_completion(self):
        self.mqtt_failed = True
        self.controller.request_shutdown(self.owners)
        first = self.controller.wait()
        self.assertFalse(first.may_exit)
        self.mqtt_failed = False
        self.controller.retry_shutdown()
        result = self.controller.wait()
        self.assertTrue(result.may_exit)
        self.assertTrue(result.succeeded)
        self.assertIs(self.controller.initial_shutdown_completion, first)
        self.assertFalse(first.succeeded)
        self.assertEqual(self.audit.close_calls, 1)
        names = [name for name, _ in self.events]
        self.assertEqual(names.count('mqtt.close'), 2)
        self.assertEqual(names.count('udp.close'), 1)
        self.assertTrue(self.controller.final)

    def test_drained_audit_loss_remains_failure_but_does_not_retain_native_owners(self):
        self.audit.loss = 2
        self.controller.request_shutdown(self.owners)
        result = self.controller.wait()
        self.assertTrue(result.resources_released)
        self.assertTrue(result.audit.drained)
        self.assertFalse(result.audit.intact)
        self.assertTrue(result.may_exit)
        self.assertFalse(result.succeeded)

    def test_activation_exception_is_reported_and_relocks_authority(self):
        def fail(request):
            self.safety.release_estop()
            raise ValueError('bad activation')
        self.controller._activate_scenario = fail
        self.controller.request_scenario(self.scenario('failure'), self.owners)
        result = self.controller.wait()
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failures[0].resource, 'scenario apply')
        self.assertTrue(self.safety.estop_active)
        self.assertTrue(self.controller.blocks_work)
        self.assertFalse(self.controller.applying)

    def test_captured_serial_owner_includes_handle_published_after_teardown_request(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        class Stream:
            is_open = True
            in_waiting = 0
            fail_close = True
            def read(self, size):
                return b""
            def close(self):
                if self.fail_close:
                    raise OSError("late native owner refused close")
                self.is_open = False
        stream = Stream()
        serial = RmSerialBackend(port="TEST", mock_writes=False, safety=self.safety)
        def open_serial(**kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test did not release serial open")
            return stream
        with patch("serial.Serial", side_effect=open_serial):
            starter = threading.Thread(target=serial.start)
            starter.start()
            self.assertTrue(entered.wait(1))
            self.assertIsNone(serial._serial)
            owners = replace(self.owners, serial=serial, serial_starter=starter)
            self.controller.request_shutdown(owners)
            self.assertIsNone(self.controller.poll())
            self.assertTrue(self.controller.pending)
            release.set()
            result = self.controller.wait()
        self.assertFalse(result.may_exit)
        self.assertIs(serial._serial, stream)
        self.assertTrue(stream.is_open)
        stream.fail_close = False
        self.controller.retry_shutdown()
        self.assertTrue(self.controller.wait().may_exit)
        self.assertIsNone(serial._serial)
        self.assertFalse(stream.is_open)

    def test_worker_cannot_admit_owner_callbacks(self):
        errors = []
        def request():
            try:
                self.controller.request_shutdown(self.owners)
            except RuntimeError as exc:
                errors.append(str(exc))
        worker = threading.Thread(target=request)
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
