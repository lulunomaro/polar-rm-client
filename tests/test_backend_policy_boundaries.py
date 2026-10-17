"""Fault-injection checks for policy adapters, without transport startup."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.backend.manager import BackendManager
from rm_client.backend.polar_control_policy import ControlGates
from rm_client.backend.robot_link_settings import (
    ABSENT, PolarDriveSettings, PolarGimbalSettings, PolarSpinSettings, RobotLinkSettings,
)


class PolicyAdapterTests(unittest.TestCase):
    def test_radio_authority_is_revoked_before_feedback_can_fail(self):
        backend = BackendManager.__new__(BackendManager)
        bridge = SimpleNamespace(debug_packages={}, debug_key_at=0, debug_key_sequence=0)
        for gates in (ControlGates(True, True, True, True),
                      ControlGates(False, False, True, True),
                      ControlGates(False, True, False, True)):
            control = SimpleNamespace(radio_km_enabled=True)
            with self.subTest(gates=gates), \
                 patch('rm_client.backend.manager.evaluate_radio_km_feedback', side_effect=ValueError('bad telemetry')):
                with self.assertRaises(ValueError):
                    backend._apply_radio_km_feedback(control, bridge, gates, requested=True)
                self.assertFalse(control.radio_km_enabled)

    def test_nonfinite_readback_keeps_baseline_error_without_retaining_authority(self):
        backend = BackendManager.__new__(BackendManager)
        bridge = SimpleNamespace(debug_packages={'km_rx': float('inf')},
                                 debug_key_at=1, debug_key_sequence=0)
        control = SimpleNamespace(radio_km_enabled=True)
        with self.assertRaises(OverflowError):
            backend._apply_radio_km_feedback(control, bridge,
                ControlGates(True, True, True, True), requested=True)
        self.assertFalse(control.radio_km_enabled)
        self.assertEqual(control.radio_km_policy, '?')
        self.assertIn('没报档位', control.radio_km_policy_hint)

    def test_unrequested_gimbal_clears_previous_plan_before_identity_validation(self):
        backend = BackendManager.__new__(BackendManager)
        backend._polar_gimbal_last = (1.0, 2.0)
        backend.polar_arbiter = SimpleNamespace(may_command_gimbal=False)
        backend.polar_bridge = SimpleNamespace(active=True)
        target = SimpleNamespace(robot_id='bad', vision_calibrated=False, target_locked=True)
        self.assertIsNone(backend._plan_polar_gimbal(target, estopped=False))
        self.assertIsNone(backend._polar_gimbal_last)

    def test_settings_snapshot_detaches_registry_and_has_closed_defaults(self):
        missing = RobotLinkSettings.capture(None)
        self.assertFalse(missing.drive.module_enabled)
        self.assertFalse(missing.drive.allow_drive)
        self.assertFalse(missing.gimbal.requested)
        self.assertTrue(missing.gimbal.vision_nuc_occupied)
        self.assertIs(missing.transport.mock_writes, ABSENT)
        values = {'Allow Drive': SimpleNamespace(value=True),
                  'Client Chassis Drive': SimpleNamespace(value=True),
                  'Port': SimpleNamespace(value='COM7')}
        module = SimpleNamespace(enabled=True, get_setting=values.get)
        snapshot = RobotLinkSettings.capture({'RobotLink': module})
        values['Allow Drive'].value = False
        values['Port'].value = 'COM9'
        module.enabled = False
        self.assertTrue(snapshot.drive.module_enabled)
        self.assertTrue(snapshot.drive.allow_drive)
        self.assertEqual(snapshot.transport.explicit_port(), 'COM7')

    def test_incomplete_scale_settings_fail_before_producing_drive_axes(self):
        snapshot = RobotLinkSettings.capture(None)
        with self.assertRaises(AttributeError):
            snapshot.drive.scales()

    def test_spin_and_gimbal_host_recheck_link_freshness_at_their_decision(self):
        backend = BackendManager.__new__(BackendManager)
        backend.polar_bridge = SimpleNamespace(active=False, overlay={})
        commands, requests = [], []
        backend.polar = SimpleNamespace(set_spin_command=commands.append,
                                         set_gimbal_host_request=requests.append)
        backend._spin_ever_on, backend._spin_off_since = False, 0.0
        backend._last_spin_note = ""
        backend.field_log = SimpleNamespace(emit=lambda *args, **kwargs: None)
        target = SimpleNamespace()
        earlier_gate = ControlGates(False, True, True, True)
        with patch('rm_client.backend.manager._LOG.warning'):
            backend._apply_polar_spin(target, PolarSpinSettings(True, True), earlier_gate)
        self.assertEqual(commands, [0])
        control = SimpleNamespace(dt7_enabled=True)
        backend._apply_polar_gimbal_host_request(control,
            PolarGimbalSettings(True, False), earlier_gate)
        self.assertEqual(requests, [False])
        self.assertEqual(control.dt7_mouse_note, "")

    def test_scale_validation_retains_left_to_right_abort_order(self):
        settings = PolarDriveSettings(True, True, True, True, speed_percent='bad')
        with self.assertRaises(ValueError):
            settings.scales()


if __name__ == '__main__':
    unittest.main()
