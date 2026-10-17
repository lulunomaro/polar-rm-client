"""Ordered frontend publication contracts, frozen before any projection extraction."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rm_client.backend.referee.rm2026 import state_adapter
from rm_client.backend.referee.rm2026.state_adapter import Rm2026StateAdapter

FIXTURE = Path(__file__).parent / 'fixtures/referee_frontend_projection_baseline.py'
FIELDS = ('official_robot_health', 'robot_positions', 'robot_statics', 'robot_dynamics',
          'robot_modules', 'robot_id', 'alliance_color', 'backend_frontend_synced',
          'robot_rows', 'map_robots', 'buffs', 'buff_rows')


def frozen():
    source = FIXTURE.read_bytes()
    assert hashlib.sha256(source).hexdigest() == BASELINE_SHA256
    # The function fixture alone is insufficient: its semantic globals must
    # also be historical values, never fetched from the candidate under test.
    # Literal values below were independently checked at 1b2f580.
    namespace = {
        'RobotState': object, 'Any': object,
        '_HEALTH_ROLE_ORDER': (1, 2, 3, 4, 7),
        'DEFAULT_MAX_HEALTH': 600,
        'ROBOT_ID_NAMES': {
            1: 'Hero', 2: 'Engineer', 3: 'Infantry', 4: 'Infantry',
            5: 'Infantry', 6: 'Aerial', 7: 'Sentry', 8: 'Dart', 9: 'Radar',
        },
        'BUFF_TYPE_NAMES': {
            1: 'Attack Boost', 2: 'Defense Boost', 3: 'Heat Cooling',
            4: 'Chassis Power', 5: 'Health Regen', 6: 'Exchange Ammo', 7: 'Terrain',
        },
    }
    exec(compile(source, str(FIXTURE), 'exec'), namespace)
    return namespace['sync_frontend_state']


def seed():
    return dict(official_robot_health=[100] * 10, robot_positions=[{'robot_id': 3, 'x': 2., 'y': 4.}],
                robot_statics={3: {'max_health': 600}}, robot_dynamics={3: {'current_health': 0}},
                robot_modules={3: {'armor': 1}}, robot_id=3, alliance_color='red',
                backend_frontend_synced=False, robot_rows=[{'old': 'rows'}],
                map_robots=[{'old': 'map'}], buffs=[], buff_rows=[{'old': 'buffs'}])


def observe(method, changes=None, *, fail_at=None, mutate_after_rows=False):
    trace = []
    def event(*entry):
        trace.append(deepcopy(entry))
        if len(trace) == fail_at:
            raise RuntimeError('injected observation failure')
    class State:
        def __getattribute__(self, name):
            if name in FIELDS:
                event('read', name)
            return object.__getattribute__(self, name)
        def __setattr__(self, name, value):
            if name in FIELDS:
                event('write', name, value)
            object.__setattr__(self, name, value)
            if name == 'robot_rows' and mutate_after_rows:
                object.__getattribute__(self, 'robot_dynamics')[3]['current_health'] = 77
    state = State()
    values = seed()
    values.update(deepcopy(changes or {}))
    for key, value in values.items():
        object.__setattr__(state, key, value)
    try:
        result = ('value', method(Rm2026StateAdapter(SimpleNamespace()), state))
    except Exception as exc:
        result = ('error', type(exc).__name__, str(exc))
    final = {key: deepcopy(object.__getattribute__(state, key)) for key in FIELDS}
    return trace, result, final, state


class RefereeFrontendProjectionContract(unittest.TestCase):
    def compare(self, changes=None, **kwargs):
        actual = observe(Rm2026StateAdapter.sync_frontend_state, changes, **kwargs)
        expected = observe(frozen(), changes, **kwargs)
        self.assertEqual(actual[:3], expected[:3])
        return actual

    def test_frozen_metadata_does_not_follow_candidate_changes(self):
        changes = {'buffs': [{'buff_type': 1, 'buff_left_time': 5}]}
        expected = observe(frozen(), changes)[:3]
        mutations = {
            '_HEALTH_ROLE_ORDER': (7, 4, 3, 2, 1),
            'DEFAULT_MAX_HEALTH': 999,
            'ROBOT_ID_NAMES': {1: 'Wrong hero'},
            'BUFF_TYPE_NAMES': {1: 'Wrong buff'},
        }
        for name, value in mutations.items():
            with self.subTest(metadata=name), patch.object(state_adapter, name, value):
                self.assertEqual(observe(frozen(), changes)[:3], expected)
                self.assertNotEqual(
                    observe(Rm2026StateAdapter.sync_frontend_state, changes)[:3], expected)
                with self.assertRaises(AssertionError):
                    self.compare(changes)

    def test_empty_authority_does_not_publish_or_clear_previous_projection(self):
        result = self.compare(dict(official_robot_health=[], robot_positions=[], robot_statics={},
                                   robot_dynamics={}, robot_modules={}))
        self.assertFalse(result[2]['backend_frontend_synced'])
        self.assertFalse(any(e[0] == 'write' for e in result[0]))

    def test_local_zero_beats_global_but_nonlocal_retains_fallback(self):
        result = self.compare({'robot_dynamics': {3: {'current_health': 0}, 4: {'current_health': 80}}})
        rows = {r['id']: r for r in result[2]['robot_rows']}
        self.assertEqual((rows[3]['hp'], rows[3]['state']), (0, 'DEAD'))
        self.assertEqual(rows[4]['hp'], 100)
        self.assertEqual(result[2]['map_robots'][0]['hp'], 0)
        result = self.compare({'robot_dynamics': {3: {'current_heat': 0}}})
        self.assertEqual(next(r['hp'] for r in result[2]['robot_rows'] if r['id'] == 3), 100)

    def test_blue_mapping_short_arrays_and_extra_ids_keep_order(self):
        result = self.compare(dict(robot_id=103, alliance_color='red', official_robot_health=[7, 8],
                                   robot_dynamics={103: {'current_health': 0}, 109: {'current_health': 9}},
                                   robot_positions=[{'robot_id': 109, 'x': 1}], robot_modules={}))
        rows = result[2]['robot_rows']
        self.assertEqual([r['id'] for r in rows], [101, 102, 103, 104, 107, 1, 2, 3, 4, 7, 109])
        self.assertEqual([r['hp'] for r in rows[:3]], [7, 8, 0])
        self.assertEqual(result[2]['map_robots'][0]['team'], 'enemy')

    def test_duplicate_positions_use_last_value_with_first_key_order(self):
        result = self.compare({'robot_positions': [{'robot_id': 3, 'x': 1}, {'robot_id': 4, 'x': 2},
                                                  {'robot_id': 3, 'x': 9}]})
        self.assertEqual([(r['id'], r['x']) for r in result[2]['map_robots']], [(3, 9.), (4, 2.)])

    def test_row_modules_preserve_reference_identity(self):
        _, _, _, state = self.compare()
        rows = object.__getattribute__(state, 'robot_rows')
        modules = object.__getattribute__(state, 'robot_modules')
        self.assertIs(next(r['modules'] for r in rows if r['id'] == 3), modules[3])

    def test_rows_publication_precedes_map_failure(self):
        _, result, state, _ = self.compare({'robot_positions': [{'robot_id': -1, 'x': 'bad'}]})
        self.assertEqual(result[:2], ('error', 'ValueError'))
        self.assertTrue(state['backend_frontend_synced'])
        self.assertNotEqual(state['robot_rows'], [{'old': 'rows'}])
        self.assertEqual(state['map_robots'], [{'old': 'map'}])
        self.assertEqual(state['buff_rows'], [{'old': 'buffs'}])

    def test_map_publication_precedes_buff_failure(self):
        _, result, state, _ = self.compare({'buffs': [{'buff_type': 1, 'buff_left_time': 'bad'}]})
        self.assertEqual(result[:2], ('error', 'ValueError'))
        self.assertNotEqual(state['robot_rows'], [{'old': 'rows'}])
        self.assertNotEqual(state['map_robots'], [{'old': 'map'}])
        self.assertEqual(state['buff_rows'], [{'old': 'buffs'}])

    def test_map_reobserves_authority_after_row_publication(self):
        _, _, state, _ = self.compare(mutate_after_rows=True)
        self.assertEqual(next(r['hp'] for r in state['robot_rows'] if r['id'] == 3), 0)
        self.assertEqual(state['map_robots'][0]['hp'], 77)

    def test_empty_positions_and_buffs_retain_their_previous_projections(self):
        _, _, state, _ = self.compare({'robot_positions': [], 'buffs': []})
        self.assertEqual(state['map_robots'], [{'old': 'map'}])
        self.assertEqual(state['buff_rows'], [{'old': 'buffs'}])

    def test_every_observed_failure_preserves_prefix_and_partial_state(self):
        for changes in (None, {'buffs': [{'buff_type': 2, 'buff_left_time': 5}]},
                        {'robot_id': 103, 'alliance_color': 'blue'},
                        {'official_robot_health': [], 'robot_dynamics': {}, 'robot_modules': {}}):
            trace = observe(frozen(), changes)[0]
            for failure in range(1, len(trace) + 1):
                with self.subTest(changes=changes, failure=failure):
                    self.compare(changes, fail_at=failure)

    def test_malformed_numeric_sources_preserve_failure_stage(self):
        for changes in ({'official_robot_health': ['bad']}, {'robot_id': 'bad'},
                        {'robot_statics': {'bad': {}}}, {'robot_dynamics': {3: {'current_health': 'bad'}}},
                        {'robot_statics': {3: {'max_health': 'bad'}}},
                        {'robot_positions': [{'robot_id': 3, 'yaw': 'bad'}]},
                        {'buffs': [{'robot_id': 'bad', 'buff_type': 1, 'buff_left_time': 4}]}):
            self.compare(changes)


BASELINE_SHA256 = 'a3f81559145b25b3387d6d22217b7538ab2b92faa565264ef81f71074a02311a'

if __name__ == '__main__':
    unittest.main()
