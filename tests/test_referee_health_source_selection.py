"""Authoritative zero HP is an observation, not a missing-source sentinel.

Real annual codec -> adapter producers -> table/map projection; no transports,
GUI, network, hardware, or application startup required.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend.referee.rm2026 import state_adapter
from rm_client.backend.referee.rm2026.projection_policy import select_projected_health
from rm_client.backend.referee.rm2026.codec import decode_message
from rm_client.backend.referee.rm2026.state_adapter import Rm2026StateAdapter
from rm_client.backend.referee.wire import encode_float, encode_packed_uints, encode_uint
from rm_client.model import RobotState


class RefereeHealthSourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.state = RobotState()
        self.adapter = Rm2026StateAdapter(SimpleNamespace())

    def apply(self, name, payload):
        self.adapter._apply_one(self.state, name, decode_message(name, payload))

    def health_for(self, robot_id):
        self.adapter.sync_frontend_state(self.state)
        row = next(row for row in self.state.robot_rows if row['id'] == robot_id)
        marker = next(row for row in self.state.map_robots if row['id'] == robot_id)
        return row, marker

    def seed_identity_change(self, *, blue=False):
        old_id, new_id = (103, 104) if blue else (3, 4)
        self.apply('RobotStaticStatus', encode_uint(4, old_id))
        self.apply('RobotDynamicStatus', encode_uint(1, 275))
        self.apply('RobotPosition', encode_uint(5, old_id) + encode_float(1, 2.0) + encode_float(2, 4.0))
        self.apply('RobotStaticStatus', encode_uint(4, new_id))
        self.assertEqual(self.state.robot_dynamics[old_id]['current_health'], 275)
        self.assertEqual(self.state.robot_id, new_id)
        return old_id, new_id

    def test_present_global_death_overrides_previous_local_robot_dynamic_hp(self):
        for blue in (False, True):
            with self.subTest(blue=blue):
                self.setUp()
                old_id, _ = self.seed_identity_change(blue=blue)
                self.apply('GlobalUnitStatus', encode_packed_uints(11, [500, 500, 0, 500, 500] * 2))
                before = deepcopy(self.state.robot_dynamics)
                row, marker = self.health_for(old_id)
                self.assertEqual((row['hp'], row['state'], marker['hp']), (0, 'DEAD', 0))
                self.assertEqual(self.state.robot_dynamics, before)

    def test_absent_global_slot_preserves_dynamic_fallback(self):
        old_id, _ = self.seed_identity_change()
        self.apply('GlobalUnitStatus', encode_packed_uints(11, [500, 500]))
        row, marker = self.health_for(old_id)
        self.assertEqual((row['hp'], row['state'], marker['hp']), (275, 'ALIVE', 275))

    def test_current_local_dynamic_zero_keeps_priority_over_positive_global(self):
        self.apply('RobotDynamicStatus', encode_uint(1, 0))
        self.apply('RobotPosition', encode_uint(5, 3))
        self.apply('GlobalUnitStatus', encode_packed_uints(11, [500] * 10))
        row, marker = self.health_for(3)
        self.assertEqual((row['hp'], row['state'], marker['hp']), (0, 'DEAD', 0))

    def test_current_local_dynamic_positive_keeps_priority_over_global_zero(self):
        self.apply('RobotDynamicStatus', encode_uint(1, 275))
        self.apply('RobotPosition', encode_uint(5, 3))
        self.apply('GlobalUnitStatus', encode_packed_uints(11, [0] * 10))
        row, marker = self.health_for(3)
        self.assertEqual((row['hp'], row['state'], marker['hp']), (275, 'LOCAL', 275))

    def test_missing_dynamic_health_does_not_invent_local_zero(self):
        self.apply('RobotDynamicStatus', encode_uint(6, 10))
        self.apply('RobotPosition', encode_uint(5, 3))
        self.apply('GlobalUnitStatus', encode_packed_uints(11, [500] * 10))
        self.assertNotIn('current_health', self.state.robot_dynamics[3])
        row, marker = self.health_for(3)
        self.assertEqual((row['hp'], row['state'], marker['hp']), (500, 'LOCAL', 500))


class HealthObservationPolicyTests(unittest.TestCase):
    def test_presence_table_preserves_zero_and_source_priority(self):
        cases = (
            # local, global, dynamic, expected
            (True, 500, 0, 0), (True, 0, 275, 275),
            (True, 500, None, 500), (True, None, 275, 275),
            (False, 0, 275, 0), (False, 500, 275, 500),
            (False, None, 275, 275), (False, None, 0, 0),
            (False, 0, None, 0), (False, None, None, 0),
        )
        for local, global_hp, dynamic_hp, expected in cases:
            with self.subTest(local=local, global_hp=global_hp, dynamic_hp=dynamic_hp):
                self.assertEqual(select_projected_health(local=local,
                    global_hp=global_hp, dynamic_hp=dynamic_hp), expected)

    def test_frozen_oracle_differs_only_for_documented_nonlocal_death_correction(self):
        from test_referee_frontend_projection_contract import frozen
        producer = RefereeHealthSourceSelectionTests()
        producer.setUp()
        old_id, _ = producer.seed_identity_change()
        producer.apply('GlobalUnitStatus', encode_packed_uints(11, [500, 500, 0, 500, 500] * 2))
        historical = deepcopy(producer.state)
        frozen()(producer.adapter, historical)
        producer.adapter.sync_frontend_state(producer.state)
        old_row = next(row for row in historical.robot_rows if row['id'] == old_id)
        old_marker = next(row for row in historical.map_robots if row['id'] == old_id)
        self.assertEqual((old_row['hp'], old_row['state'], old_marker['hp']), (275, 'ALIVE', 275))
        # Explicit bug exception, not a rewritten frozen fixture/oracle. Every
        # other model field, row field, publication shape and source stays equal.
        old_row.update(hp=0, state='DEAD')
        old_marker['hp'] = 0
        self.assertEqual(producer.state.__dict__, historical.__dict__)

    def test_real_producer_regressions_reject_source_selection_mutants(self):
        def truthiness_fallback(*, local, global_hp, dynamic_hp):
            return dynamic_hp if local and dynamic_hp is not None else global_hp or dynamic_hp or 0
        def global_always(*, local, global_hp, dynamic_hp):
            return global_hp if global_hp is not None else dynamic_hp or 0
        def dynamic_zero_missing(*, local, global_hp, dynamic_hp):
            return dynamic_hp if local and dynamic_hp else global_hp or 0
        def no_history_fallback(*, local, global_hp, dynamic_hp):
            return dynamic_hp if local and dynamic_hp is not None else global_hp or 0
        mutants = (
            (truthiness_fallback, 'test_present_global_death_overrides_previous_local_robot_dynamic_hp'),
            (global_always, 'test_current_local_dynamic_positive_keeps_priority_over_global_zero'),
            (dynamic_zero_missing, 'test_current_local_dynamic_zero_keeps_priority_over_positive_global'),
            (no_history_fallback, 'test_absent_global_slot_preserves_dynamic_fallback'),
        )
        for mutant, method in mutants:
            with self.subTest(mutant=mutant.__name__):
                result = unittest.TestResult()
                with patch.object(state_adapter, 'select_projected_health', mutant):
                    RefereeHealthSourceSelectionTests(method).run(result)
                self.assertFalse(result.wasSuccessful(), f'{mutant.__name__} survived producer regression')
                self.assertTrue(result.failures, result.errors)


if __name__ == '__main__':
    unittest.main()
