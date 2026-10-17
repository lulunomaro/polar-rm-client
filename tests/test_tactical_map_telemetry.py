"""Pure map telemetry values, malformed boundaries and detached ownership."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.ui.tactical_map_telemetry import (
    PenaltyNotice, RadarLifetime, RadarMarker, TimelineEntry,
    own_active_buffs, penalty_notices, radar_coordinate_metres, special_mechanisms,
)


class MapTelemetryTests(unittest.TestCase):
    def test_normal_import_executes_without_qt_model_backend_renderer_or_clock(self):
        script = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('PySide6', 'rm_client.model', 'rm_client.backend', 'rm_client.ui.polar', 'rm_client.vision')):
            raise AssertionError('forbidden dependency: ' + fullname)
sys.meta_path.insert(0, Block())
from rm_client.ui.tactical_map_telemetry import RadarMarker, RadarLifetime, own_active_buffs, penalty_notices
assert RadarMarker.from_wire({'target_pos_x': 1400}, 28, 15).x_m == 14
assert RadarLifetime.visible_at(94, 100).opacity == 0
assert penalty_notices({'penalty_type': 2}, ())[0].penalty_type == 2
assert own_active_buffs((), 3) == ()
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_radar_metres_centimetres_and_thresholds_keep_units(self):
        cases = ((0, 28, 0), (-100, 28, -100), (28.5, 28, 28.5), (28.5001, 28, .285001),
                 (1400, 28, 14), (750, 15, 7.5), (.6, 0, .6), (.7, 0, .007))
        for raw, span, expected in cases:
            with self.subTest(raw=raw, span=span):
                self.assertAlmostEqual(radar_coordinate_metres(raw, span), expected)
        with self.assertRaises(TypeError): radar_coordinate_metres(None, 28)
        with self.assertRaises(ValueError): radar_coordinate_metres('bad', 28)

    def test_radar_age_admission_and_fade_preserve_inclusive_expiry(self):
        for age, expected in ((-1., 1.), (0., 1.), (4., 1.), (4.5, .75), (5., .5), (5.999, .0005), (6., 0.)):
            lifetime = RadarLifetime.visible_at(100 - age, 100)
            self.assertIsNotNone(lifetime)
            self.assertAlmostEqual(lifetime.opacity, expected)
            self.assertEqual(lifetime.age_seconds, 100 - (100 - age))
        self.assertIsNone(RadarLifetime.visible_at(93.999, 100))
        self.assertIsNone(RadarLifetime.visible_at(0, 100))
        self.assertIsNone(RadarLifetime.visible_at(-1, -2))
        self.assertEqual(RadarLifetime.visible_at(float('nan'), 100).opacity, 1.)
        self.assertEqual(RadarLifetime.visible_at(100, 101, 2.).opacity, .5)

    def test_radar_row_detaches_values_without_consuming_or_changing_source(self):
        source = {'target_pos_x': '1400', 'target_pos_y': 750, 'is_high_light': '1'}
        before = deepcopy(source)
        marker = RadarMarker.from_wire(source, 28, 15)
        self.assertEqual(marker, RadarMarker(14., 7.5, True))
        self.assertEqual(source, before)
        source['target_pos_x'] = 100
        self.assertEqual(marker.x_m, 14.)
        with self.assertRaises(FrozenInstanceError): marker.x_m = 9
        self.assertEqual(RadarMarker.from_wire({}, 28, 15), RadarMarker(0, 0, False))

    def test_radar_failures_remain_distinct_for_caller_to_handle(self):
        with self.assertRaises(AttributeError): RadarMarker.from_wire(None, 28, 15)
        with self.assertRaises(ValueError): RadarMarker.from_wire({'target_pos_y': 'bad'}, 28, 15)
        with self.assertRaises(TypeError): RadarMarker.from_wire({'target_pos_x': []}, 28, 15)
        with self.assertRaises(OverflowError): RadarMarker.from_wire({'is_high_light': float('inf')}, 28, 15)

    def test_penalty_active_latest_dedup_and_three_limit_are_explicit(self):
        active = {'penalty_type': 2, 'total_penalty_num': 7, 'penalty_effect_sec': 30}
        history = [{'penalty_type': 1}, {'penalty_type': 2, 'total_penalty_num': 99},
                   {'penalty_type': 3}, {'penalty_type': 4}]
        before = deepcopy((active, history))
        self.assertEqual(penalty_notices(active, history), (PenaltyNotice(2, 7, 30), PenaltyNotice(4, 0, 0), PenaltyNotice(3, 0, 0)))
        self.assertEqual((active, history), before)
        self.assertEqual(penalty_notices({}, history), (PenaltyNotice(4, 0, 0), PenaltyNotice(3, 0, 0), PenaltyNotice(2, 99, 0)))
        self.assertEqual(penalty_notices({}, []), ())

    def test_penalty_conversion_skips_invalid_type_and_zeroes_both_invalid_counts(self):
        self.assertEqual(penalty_notices({'penalty_type': 'bad'}, [{'penalty_type': 1, 'total_penalty_num': 9, 'penalty_effect_sec': 'bad'}]),
                         (PenaltyNotice(1, 0, 0),))
        self.assertEqual(penalty_notices({'penalty_type': 0}, []), (PenaltyNotice(0, 0, 0),))
        with self.assertRaises(AttributeError): penalty_notices({}, [None])
        with self.assertRaises(OverflowError): penalty_notices({'penalty_type': float('inf')}, [])
        with self.assertRaises(OverflowError): penalty_notices({'penalty_type': 1, 'penalty_effect_sec': float('inf')}, [])

    def test_buffs_filter_source_identity_before_active_remaining_and_name(self):
        rows = [
            {'name': 'mock', 'robot_id': 3, 'active': True},
            {'buff_type': 1, 'robot_id': 4, 'active': True, 'remaining': float('inf')},
            {'buff_type': 2, 'robot_id': 'bad', 'active': True},
            {'buff_type': 3, 'robot_id': 3, 'active': False, 'remaining': float('inf')},
            {'buff_type': 4, 'robot_id': 3, 'active': True, 'remaining': -2},
            {'buff_type': 5, 'robot_id': 3, 'active': True, 'remaining': 'bad', 'name': 'Speed'},
            {'buff_type': 6, 'robot_id': '3', 'active': True, 'remaining': 18.9, 'name': 'Power'},
        ]
        before = deepcopy(rows)
        entries = own_active_buffs(rows, 3)
        self.assertEqual(entries, (TimelineEntry('buff', 'Buff 4', 0), TimelineEntry('buff', 'Speed', 0), TimelineEntry('buff', 'Power', 18)))
        self.assertEqual(rows, before)
        self.assertEqual([entry.as_row() for entry in entries], [('buff', 'Buff 4', 0), ('buff', 'Speed', 0), ('buff', 'Power', 18)])
        rows[-1]['name'] = 'changed'
        self.assertEqual(entries[-1].name, 'Power')
        with self.assertRaises(FrozenInstanceError): entries[-1].remaining_seconds = 1

    def test_buffs_preserve_overflow_and_bad_mapping_as_failures(self):
        with self.assertRaises(AttributeError): own_active_buffs([None], 3)
        with self.assertRaises(OverflowError): own_active_buffs([{'buff_type': 1, 'robot_id': float('inf')}], 3)
        with self.assertRaises(OverflowError): own_active_buffs([{'buff_type': 1, 'robot_id': 3, 'active': True, 'remaining': float('inf')}], 3)

    def test_mechanisms_skip_bad_mapping_and_nonpositive_ids_keep_order(self):
        rows = [None, {'mechanism_id': 0}, {'mechanism_id': -1}, {'mechanism_id': 'bad'},
                {'mechanism_id': 2, 'time_sec': -1}, {'mechanism_id': 3, 'time_sec': 4.9},
                {'mechanism_id': 4, 'time_sec': 'bad'}]
        self.assertEqual(special_mechanisms(rows), (TimelineEntry('mechanism', 'MECH2', 0), TimelineEntry('mechanism', 'MECH3', 4)))
        with self.assertRaises(OverflowError): special_mechanisms([{'mechanism_id': float('inf')}])


if __name__ == '__main__':
    unittest.main()
