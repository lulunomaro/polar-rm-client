"""Safety revocation stays immediate while control/UI observers stay owner-only."""
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rm_client.backend.cc_link import AsyncCustomControllerLink
from rm_client.backend.dt7_link import DEFAULT_PROFILE, Dt7KeyMouseLink, decode_dt7_frame
from rm_client.backend.dt7_worker import AsyncDt7KeyMouseLink
from rm_client.backend.manager import BackendManager
from rm_client.control import KeyboardControlBridge


def until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('neutral did not settle')
        time.sleep(.001)


class BackendInputOwnerTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root.name}), patch.object(
                BackendManager, '_build_commit', return_value='TEST'):
            self.manager = BackendManager(workspace=root.name)
        self.addCleanup(self.manager.shutdown)
        self.owner = threading.get_ident()
        self.observed = []
        self.manager.input_clear_hook = lambda reason: self.observed.append(
            (threading.get_ident(), reason))
        self.control = KeyboardControlBridge(publisher=None, mock_output=True)
        self.control.key_state['W'] = True
        self.control.last_command = object()
        self.control.last_payload = b'held W'
        self.manager._dt7_control_ref = self.control

    def on_worker(self, action):
        errors = []
        def run():
            try:
                action()
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=run)
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_worker_revocation_is_immediate_and_owner_delivery_is_bounded(self):
        self.on_worker(lambda: [self.manager.safety.lock_control('driver fault') for _ in range(1000)])
        self.assertEqual(self.manager.safety.control_state.value, 'LOCKED')
        self.assertEqual(self.manager._input_clear_requests.qsize(), 1)
        self.assertEqual(self.observed, [])
        self.assertTrue(self.control.key_state['W'])  # Owner snapshot untouched.
        self.manager.poll_lifecycle()
        self.assertEqual(self.observed, [(self.owner, 'driver fault')])
        self.assertFalse(self.control.key_state['W'])
        self.assertIsNone(self.control.last_command)
        self.assertEqual(self.control.last_payload, b'')
        self.manager.poll_lifecycle()
        self.assertEqual(len(self.observed), 1)

    def test_real_writers_receive_neutral_before_owner_poll_without_callbacks(self):
        dt7_writes, cc_writes = [], []
        inner = Dt7KeyMouseLink(profile=dict(DEFAULT_PROFILE), mock_output=False, wire_neutral=False,
            write_bytes=lambda frame: (dt7_writes.append((threading.get_ident(), frame)) or len(frame)))
        dt7 = AsyncDt7KeyMouseLink(link=inner, profile=DEFAULT_PROFILE, mock_output=False,
                                  safety=self.manager.safety)
        cc = AsyncCustomControllerLink(mock_output=False,
            write_bytes=lambda frame: (cc_writes.append((threading.get_ident(), frame)) or len(frame)))
        with cc._condition:
            cc.wire_neutral = False
        self.manager._dt7_link, self.manager._cc_link = dt7, cc
        with dt7._cv:
            dt7._fault = 'undelivered DT7 fault'
        self.on_worker(lambda: self.manager.safety.lock_control('external callback'))
        until(lambda: dt7.wire_neutral and cc.wire_neutral)
        self.assertTrue(dt7_writes)
        self.assertTrue(cc_writes)
        self.assertTrue(all(thread != self.owner for thread, _ in dt7_writes + cc_writes))
        self.assertEqual(int.from_bytes(decode_dt7_frame(dt7_writes[-1][1])['payload'][8:10], 'little'), 0)
        self.assertEqual(self.observed, [])
        self.assertFalse(dt7._fault_reported)
        self.manager.poll_lifecycle()
        self.assertEqual(self.observed, [(self.owner, 'external callback')])
        dt7.poll()
        self.assertTrue(dt7._fault_reported)
        self.assertTrue(all(thread == self.owner for thread, _ in self.observed))

    def test_new_qualification_drains_old_worker_snapshot_before_authorizing(self):
        self.on_worker(lambda: self.manager.safety.lock_control('old epoch ended'))
        token = self.manager.begin_qualification({'core_kmc'})
        self.assertIsNotNone(token)
        self.assertFalse(self.control.key_state['W'])
        self.assertIsNone(self.control.last_command)
        self.assertEqual([reason for _, reason in self.observed],
                         ['old epoch ended', 'qualification epoch started'])
        self.assertTrue(all(thread == self.owner for thread, _ in self.observed))
        self.assertEqual(self.manager._input_clear_requests.qsize(), 0)

    def test_pending_clear_drains_even_while_shutdown_worker_is_blocked(self):
        from rm_client.lifecycle import TeardownTask
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def close():
            entered.set()
            release.wait(2)
        self.manager.request_shutdown(teardown_tasks=(TeardownTask('blocked test resource', close),))
        self.assertTrue(entered.wait(1))
        self.observed.clear()
        self.on_worker(lambda: self.manager.safety.lock_control('late callback'))
        self.manager.poll_lifecycle()
        self.assertEqual(self.observed, [(self.owner, 'late callback')])
        self.assertTrue(self.manager.lifecycle_pending)
        release.set()

    def test_failed_clear_survives_two_qualification_attempts_and_recovers_explicitly(self):
        self.on_worker(lambda: self.manager.safety.lock_control('driver fault'))
        with patch.object(self.control, 'reset_inputs', side_effect=TypeError('persistent input failure')):
            for _ in range(2):
                with self.assertRaisesRegex(TypeError, 'persistent input failure'):
                    self.manager.begin_qualification({'core_kmc'})
                self.assertEqual(self.manager.safety.control_state.value, 'LOCKED')
                self.assertIsNone(self.manager.safety._qualification)
                self.assertEqual(self.manager._input_clear_requests.qsize(), 1)
                self.assertTrue(self.manager._backend_transition_blocks_work())
                self.assertTrue(self.control.key_state['W'])
            with self.assertRaisesRegex(TypeError, 'persistent input failure'):
                self.manager.tick_polar_link(control=self.control)
        self.manager.poll_lifecycle()
        self.assertFalse(self.control.key_state['W'])
        self.assertIsNone(self.control.last_command)
        self.assertFalse(self.manager._input_clear_incomplete())
        self.assertEqual(self.manager.safety.control_state.value, 'LOCKED')
        self.assertIsNotNone(self.manager.begin_qualification({'core_kmc'}))

    def test_owner_qualification_notification_failure_cannot_return_authority(self):
        with patch.object(self.control, 'reset_inputs', side_effect=TypeError('owner input failure')):
            with self.assertRaisesRegex(RuntimeError, 'Input clear must complete'):
                self.manager.begin_qualification({'core_kmc'})
            self.assertEqual(self.manager.safety.control_state.value, 'LOCKED')
            self.assertIsNone(self.manager.safety._qualification)
            self.assertEqual(self.manager._input_clear_requests.qsize(), 1)
            with self.assertRaisesRegex(TypeError, 'owner input failure'):
                self.manager.begin_qualification({'core_kmc'})
        self.manager.poll_lifecycle()
        self.assertIsNotNone(self.manager.begin_qualification({'core_kmc'}))

    def test_failed_clear_remains_gated_when_new_worker_request_coalesces(self):
        self.on_worker(lambda: self.manager.safety.lock_control('first fault'))
        invoked = []
        def fail_with_concurrent_request():
            if not invoked:
                invoked.append(True)
                self.on_worker(lambda: self.manager.safety.lock_control('second fault'))
            raise TypeError('input failure during coalescing')
        with patch.object(self.control, 'reset_inputs', side_effect=fail_with_concurrent_request):
            for _ in range(2):
                with self.assertRaisesRegex(TypeError, 'input failure during coalescing'):
                    self.manager.begin_qualification({'core_kmc'})
                self.assertEqual(self.manager._input_clear_requests.qsize(), 1)
                self.assertTrue(self.manager._backend_transition_blocks_work())
                self.assertEqual(self.manager.safety.control_state.value, 'LOCKED')
        self.manager.poll_lifecycle()
        self.assertEqual(self.manager._input_clear_requests.qsize(), 0)
        self.assertIsNotNone(self.manager.begin_qualification({'core_kmc'}))

    def test_two_arm_attempts_cannot_bypass_owner_clear_failure(self):
        self.manager.mqtt.mock_output = False
        self.manager.mqtt.connected = True
        self.manager.safety.allow_mqtt_publish = True
        self.manager.qualification.session = SimpleNamespace(robot_id='3', match_mode='RMUL')
        with patch.object(self.manager.qualification, 'verified_core_ready', return_value=(True, 'ok')), \
                patch.object(self.manager.qualification, 'identity_matches', return_value=(True, 'ok')), \
                patch.object(self.manager.qualification, 'capabilities', return_value={'core_kmc'}):
            with patch.object(self.control, 'reset_inputs', side_effect=TypeError('arm input failure')):
                for _ in range(2):
                    ok, why = self.manager.arm_match_control(firmware_hash='test')
                    self.assertFalse(ok)
                    self.assertIn('Input clear', why)
                    self.assertEqual(self.manager.safety.control_state.value, 'LOCKED')
                    self.assertTrue(self.manager._input_clear_incomplete())
            self.manager.poll_lifecycle()
            self.assertTrue(self.manager.arm_match_control(firmware_hash='test')[0])
            self.assertFalse(self.control.key_state['W'])

    def test_broken_input_adapter_cannot_skip_owner_neutral_admission(self):
        writes = []
        inner = Dt7KeyMouseLink(profile=dict(DEFAULT_PROFILE), mock_output=False, wire_neutral=False,
            write_bytes=lambda frame: (writes.append(frame) or len(frame)))
        dt7 = AsyncDt7KeyMouseLink(link=inner, profile=DEFAULT_PROFILE, mock_output=False,
                                  safety=self.manager.safety)
        self.manager._dt7_link = dt7
        with patch.object(self.control, 'reset_inputs', side_effect=TypeError('broken control')):
            with self.assertRaisesRegex(RuntimeError, 'Input clear must complete'):
                self.manager.begin_qualification({'core_kmc'})
            until(lambda: dt7.wire_neutral)
            self.assertTrue(writes)
            self.assertEqual(int.from_bytes(decode_dt7_frame(writes[-1])['payload'][8:10], 'little'), 0)
            self.assertEqual(self.manager.safety.control_state.value, 'LOCKED')
        self.manager.poll_lifecycle()

    def test_ui_observer_failure_is_retryable_and_revokes_new_authority(self):
        self.manager.input_clear_hook = lambda reason: (_ for _ in ()).throw(ValueError('UI clear failed'))
        with self.assertRaisesRegex(RuntimeError, 'Input clear must complete'):
            self.manager.begin_qualification({'core_kmc'})
        self.assertEqual(self.manager.safety.control_state.value, 'LOCKED')
        self.assertEqual(self.manager._input_clear_requests.qsize(), 1)
        with self.assertRaisesRegex(ValueError, 'UI clear failed'):
            self.manager.poll_lifecycle()
        self.manager.input_clear_hook = lambda reason: self.observed.append((threading.get_ident(), reason))
        self.manager.poll_lifecycle()
        self.assertEqual(self.manager._input_clear_requests.qsize(), 0)
        self.assertIsNotNone(self.manager.begin_qualification({'core_kmc'}))

    def test_wrong_thread_cannot_drain_ui_observers(self):
        errors = []
        def attempt():
            try:
                self.manager.poll_lifecycle()
            except RuntimeError as exc:
                errors.append(str(exc))
        self.on_worker(attempt)
        self.assertEqual(errors, ['Input observers must be drained on their owner thread'])
        self.assertEqual(self.observed, [])


if __name__ == '__main__':
    unittest.main()
