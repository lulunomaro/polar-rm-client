"""Policy tests require no manager, registry, clocks, devices, or network."""
from dataclasses import FrozenInstanceError
import itertools
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.backend.polar_control_policy import (
    ControlGates, RobotRoleSelection, SpinCommandState, gimbal_egress_owner,
    may_plan_gimbal,
)
from rm_client.backend.radio_km_feedback import RadioKmReadback, evaluate_radio_km_feedback


class ControlGateTests(unittest.TestCase):
    def test_all_chassis_and_radio_gate_combinations(self):
        for estop, enabled, allow, active, requested, blocked in itertools.product((False, True), repeat=6):
            gates = ControlGates(estop, enabled, allow, active)
            with self.subTest(gates=gates, requested=requested, blocked=blocked):
                self.assertEqual(gates.chassis_authorized(requested=requested,
                                 role_block='blocked' if blocked else ''),
                                 not estop and enabled and allow and active and requested and not blocked)
                self.assertEqual(gates.radio_authorized(requested=requested),
                                 requested and not estop and enabled and allow)
                self.assertEqual(bool(gates.radio_block_reason(requested=requested)),
                                 requested and not (not estop and enabled and allow))

    def test_spin_and_gimbal_request_do_not_inherit_chassis_authority(self):
        for estop, enabled, allow, active, requested, spin_enabled in itertools.product((False, True), repeat=6):
            gates = ControlGates(estop, enabled, allow, active)
            with self.subTest(gates=gates, requested=requested, spin_enabled=spin_enabled):
                self.assertEqual(gates.spin_authorized(allowed=requested, module_enabled=spin_enabled),
                                 requested and not estop and active and spin_enabled)
                self.assertEqual(gates.gimbal_host_requested(requested=requested),
                                 requested and active and not estop)

    def test_gimbal_requires_every_gate_and_selected_role(self):
        for estop, authorized, active, calibrated, locked in itertools.product((False, True), repeat=5):
            for role in (3, 7):
                with self.subTest(flags=(estop, authorized, active, calibrated, locked), role=role):
                    self.assertEqual(may_plan_gimbal(estopped=estop, arbiter_authorized=authorized,
                        link_active=active, calibrated=calibrated, target_locked=locked,
                        selected_robot_id=role),
                        not estop and authorized and active and calibrated and locked and role == 3)

    def test_gimbal_egress_has_exactly_one_owner(self):
        for active, authorized, plan, official, mock in itertools.product((False, True), repeat=5):
            expected = 'polar' if active and authorized and plan else 'official' if official and not mock else 'none'
            self.assertEqual(gimbal_egress_owner(polar_active=active, polar_authorized=authorized,
                polar_has_target=plan, official_enabled=official, official_mock=mock), expected)

    def test_role_limits_follow_robot_readback_without_hiding_mismatch(self):
        role = RobotRoleSelection.resolve(3, 1)
        self.assertEqual(role.effective_id, 1)
        self.assertEqual(role.limits.max_linear_mps, 2.0)
        self.assertIn('3', role.mismatch)
        self.assertIn('1', role.mismatch)
        blocked = RobotRoleSelection.resolve(3, 7)
        self.assertTrue(blocked.drive_block)
        self.assertTrue(blocked.gimbal_block)
        self.assertEqual(RobotRoleSelection.resolve(3, 0).effective_id, 3)

    def test_value_objects_are_immutable(self):
        values = ((ControlGates(False, True, True, True), 'estopped', True),
                  (SpinCommandState(), 'command', 1),
                  (RadioKmReadback(), 'key', 1))
        for obj, field, value in values:
            with self.subTest(type=type(obj).__name__), self.assertRaises(FrozenInstanceError):
                setattr(obj, field, value)


class SpinCommandTests(unittest.TestCase):
    def test_advancing_does_not_mutate_previous_state(self):
        original = SpinCommandState()
        on = original.advance(desired=True, reported=None, now=10, off_assert_s=1)
        off = on.advance(desired=False, reported=None, now=11, off_assert_s=1)
        expired = off.advance(desired=False, reported=None, now=12, off_assert_s=1)
        self.assertEqual(original, SpinCommandState())
        self.assertEqual(on, SpinCommandState(True, 0, 1))
        self.assertEqual(off, SpinCommandState(True, 11, 2))
        self.assertEqual(expired, SpinCommandState())

    def test_unowned_spin_never_generates_off(self):
        for reported in (None, False, True):
            self.assertEqual(SpinCommandState().advance(desired=False, reported=reported,
                now=100, off_assert_s=1).command, 0)

    def test_true_readback_keeps_asserting_off_until_explicit_stop(self):
        state = SpinCommandState(True, 9.0, 2)
        state = state.advance(desired=False, reported=True, now=100, off_assert_s=1)
        self.assertEqual(state, SpinCommandState(True, 0, 2))
        self.assertEqual(state.advance(desired=False, reported=False,
                         now=101, off_assert_s=1), SpinCommandState())


class RadioFeedbackTests(unittest.TestCase):
    def test_missing_readback_is_unknown_and_preserves_previous_counter(self):
        result = evaluate_radio_km_feedback(RadioKmReadback(), requested=True,
            authorized=True, previous_received=42, now=100)
        self.assertEqual((result.robot_rx, result.robot_key), (-1, -1))
        self.assertEqual(result.next_received, 42)
        self.assertEqual(result.link_state, 'no-readback')

    def test_received_frames_do_not_prove_firmware_participation(self):
        result = evaluate_radio_km_feedback(RadioKmReadback(4, 2, 1, 100, 9),
            requested=True, authorized=True, previous_received=3, now=100)
        self.assertEqual(result.link_state, 'live')
        self.assertTrue(result.robot_rx_growing)
        self.assertIn('不参与', result.link_note)
        self.assertEqual((result.robot_key, result.robot_key_at, result.robot_key_sequence), (2, 100, 9))

    def test_key_freshness_is_closed_at_both_boundaries(self):
        for age, expected in ((-0.001, -1), (0, 4), (0.75, 4), (0.75001, -1)):
            with self.subTest(age=age):
                result = evaluate_radio_km_feedback(RadioKmReadback(4, 4, 3, 100 - age, 9),
                    requested=True, authorized=True, previous_received=3, now=100)
                self.assertEqual(result.robot_key, expected)

    def test_falling_or_invalid_counters_do_not_report_growth(self):
        for value, expected in ((3, 3), (2, 2), (None, 3), ('bad', 3)):
            result = evaluate_radio_km_feedback(RadioKmReadback(value), requested=True,
                authorized=True, previous_received=3, now=100)
            self.assertFalse(result.robot_rx_growing)
            self.assertEqual(result.next_received, expected)


if __name__ == '__main__':
    unittest.main()
