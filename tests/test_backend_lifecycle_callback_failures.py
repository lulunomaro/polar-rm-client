"""Cache/observer failures cannot abandon resource cleanup or audit completion."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from rm_client.backend.manager import BackendManager


class LifecycleCallbackFailureTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root.name}), patch.object(
                BackendManager, '_build_commit', return_value='TEST'):
            self.backend = BackendManager(workspace=root.name)
        self.addCleanup(self.backend.shutdown)

    def settle(self):
        completion = None
        deadline = time.monotonic() + 2
        while self.backend.lifecycle_pending:
            result = self.backend.poll_lifecycle()
            if result is not None:
                completion = result
            if time.monotonic() >= deadline:
                self.fail('lifecycle completion was abandoned')
            time.sleep(.001)
        return completion

    def test_preparation_cache_failure_still_seals_and_closes_every_resource(self):
        methods = [(self.backend.mqtt, 'wait_stopped'), (self.backend.udp, 'stop'),
                   (self.backend.serial, 'stop'), (self.backend.polar, 'stop'),
                   (self.backend.native_decoder, 'stop'), (self.backend.controller_angles, 'wait_closed')]
        patches, calls = [], []
        for resource, name in methods:
            instrument = patch.object(resource, name, wraps=getattr(resource, name))
            patches.append(instrument)
            calls.append(instrument.start())
        try:
            with patch.object(self.backend.polar_bridge, 'reset', side_effect=TypeError('prepare cache reset failure')):
                self.backend.request_shutdown()
                with self.assertRaises(RuntimeError):
                    self.backend.request_scenario('LabDebug', start=False)
                result = self.settle()
            self.assertTrue(all(call.called for call in calls))
            self.assertTrue(result.resources_released)
            self.assertTrue(result.may_exit)
            self.assertFalse(result.succeeded)
            self.assertIn('runtime invalidation', [failure.resource for failure in result.failures])
            self.assertTrue(self.backend.field_log.snapshot()['closed'])
        finally:
            for instrument in reversed(patches):
                instrument.stop()

    def test_stopped_cache_failure_keeps_final_completion_and_audit_drain(self):
        with patch.object(self.backend._native_recovery, 'reset',
                          side_effect=[None, TypeError('stopped cache reset failure')]):
            self.backend.request_shutdown()
            result = self.settle()
        self.assertTrue(self.backend.lifecycle_closed)
        self.assertIs(self.backend.shutdown_completion, result)
        self.assertTrue(result.resources_released)
        self.assertTrue(result.may_exit)
        self.assertFalse(result.succeeded)
        self.assertIn('stopped runtime', [failure.resource for failure in result.failures])
        self.assertTrue(result.audit.drained)
        self.assertIsNone(self.backend.poll_lifecycle())

    def test_stopped_cache_failure_blocks_scenario_activation_then_can_be_repaired(self):
        with patch.object(self.backend._native_recovery, 'reset',
                          side_effect=[None, TypeError('stopped cache reset failure')]):
            self.backend.request_scenario('OfficialEvent', start=False)
            result = self.settle()
        self.assertFalse(result.succeeded)
        self.assertEqual(self.backend.scenario_key, 'LabDebug')
        self.assertTrue(self.backend.safety.estop_active)
        self.backend.request_scenario('OfficialEvent', start=False)
        result = self.settle()
        self.assertTrue(result.succeeded)
        self.assertEqual(self.backend.scenario_key, 'OfficialEvent')


if __name__ == '__main__':
    unittest.main()
