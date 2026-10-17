"""Failed owner observers block authority, never the resource/audit state machine."""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.backend.manager import BackendManager
from rm_client.control import KeyboardControlBridge


class LifecycleInputFailureTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root.name}), patch.object(
                BackendManager, '_build_commit', return_value='TEST'):
            self.backend = BackendManager(workspace=root.name)
        self.addCleanup(self.backend.shutdown)
        self.control = KeyboardControlBridge(publisher=None, mock_output=True)
        self.control.key_state['W'] = True
        self.control.last_command = object()
        self.backend._dt7_control_ref = self.control

    def settle(self):
        result = None
        deadline = time.monotonic() + 2
        while self.backend.lifecycle_pending:
            completion = self.backend.poll_lifecycle()
            if completion is not None:
                result = completion
            if time.monotonic() >= deadline:
                self.fail('input observer failure stranded lifecycle completion')
            time.sleep(.001)
        return result

    def assert_failed_but_released(self, result):
        self.assertTrue(result.resources_released)
        self.assertTrue(result.audit.drained)
        self.assertTrue(result.may_exit)
        self.assertFalse(result.succeeded)
        failures = [failure for failure in result.failures if failure.resource == 'input clear']
        self.assertEqual(len(failures), 1)
        self.assertIn('TypeError: broken reset', failures[0].error)
        self.assertTrue(self.backend._input_clear_incomplete())
        self.assertTrue(self.backend.safety.estop_active)
        self.assertTrue(self.backend._backend_transition_blocks_work())

    def test_persistent_reset_failure_allows_final_poll_and_audit_drain(self):
        with patch.object(self.control, 'reset_inputs', side_effect=TypeError('broken reset')):
            self.backend.request_shutdown()
            result = self.settle()
            self.assert_failed_but_released(result)
            for _ in range(2):
                with self.assertRaises(TypeError):
                    self.backend.begin_qualification({'core_kmc'})
            self.assertTrue(self.control.key_state['W'])
            self.assertIs(self.backend.shutdown_completion, result)
            self.assertIsNone(self.backend.poll_lifecycle())

    def test_synchronous_shutdown_also_completes_with_observer_failure(self):
        with patch.object(self.control, 'reset_inputs', side_effect=TypeError('broken reset')):
            self.backend.shutdown()
            self.assert_failed_but_released(self.backend.shutdown_completion)

    def test_failed_clear_blocks_scenario_then_repaired_explicit_request_succeeds(self):
        with patch.object(self.control, 'reset_inputs', side_effect=TypeError('broken reset')):
            self.backend.request_scenario('OfficialEvent', start=False)
            result = self.settle()
            self.assertFalse(result.succeeded)
            self.assertEqual(self.backend.scenario_key, 'LabDebug')
            self.assertTrue(self.backend._input_clear_incomplete())
            self.assertTrue(self.backend.safety.estop_active)
        self.backend.request_scenario('OfficialEvent', start=False)
        result = self.settle()
        self.assertTrue(result.succeeded)
        self.assertEqual(self.backend.scenario_key, 'OfficialEvent')
        self.assertFalse(self.control.key_state['W'])
        self.assertFalse(self.backend._input_clear_incomplete())

    def test_first_clear_failure_during_activation_cannot_report_success_or_start(self):
        reset = self.control.reset_inputs
        def fail_on_new_scenario():
            if self.backend.scenario_key == 'OfficialEvent':
                raise TypeError('activation reset failed')
            return reset()
        with patch.object(self.control, 'reset_inputs', side_effect=fail_on_new_scenario), \
             patch.object(self.backend.mqtt, 'start') as mqtt_start, \
             patch.object(self.backend.serial, 'start') as serial_start, \
             patch.object(self.backend.polar, 'request_start') as polar_start:
            self.backend.request_scenario('OfficialEvent', start=True)
            result = self.settle()
            self.assertFalse(result.succeeded)
            self.assertIn('scenario apply', [failure.resource for failure in result.failures])
            self.assertTrue(self.backend._input_clear_incomplete())
            self.assertTrue(self.backend.safety.estop_active)
            mqtt_start.assert_not_called()
            serial_start.assert_not_called()
            polar_start.assert_not_called()
        self.backend.request_scenario('OfficialEvent', start=False)
        self.assertTrue(self.settle().succeeded)
        self.assertFalse(self.backend._input_clear_incomplete())

    def test_failure_first_seen_during_audit_is_in_final_completion(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        close = self.backend.field_log.close
        def held_close(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test did not release audit')
            return close(*args, **kwargs)
        with patch.object(self.backend.field_log, 'close', side_effect=held_close):
            self.backend.request_shutdown()
            deadline = time.monotonic() + 2
            while not entered.is_set():
                self.backend.poll_lifecycle()
                if time.monotonic() > deadline:
                    self.fail('audit close did not start')
                time.sleep(.001)
            self.backend._admit_input_clear('late worker observer')
            with patch.object(self.control, 'reset_inputs', side_effect=TypeError('broken reset')):
                self.assertIsNone(self.backend.poll_lifecycle())
                release.set()
                self.assert_failed_but_released(self.settle())


if __name__ == '__main__':
    unittest.main()
