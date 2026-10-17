"""Fresh diagnostic-only captures; no transport, model, GUI, or device needed."""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.backend.polar_health import capture_polar_link_health


class PolarHealthTests(unittest.TestCase):
    def sources(self):
        counters = SimpleNamespace(frames_ok=1, crc8_errors=2, crc16_errors=3,
                                   unknown_segments=4, length_errors=5, buffer_overflows=6)
        base = {'extra': {'preserved': True}, 'link_alive': 'old', 'segment_counts': 'old',
                'dropped_segments': 'old', 'decode': 'old'}
        transport = SimpleNamespace(stats=lambda: base, codec=SimpleNamespace(stats=counters))
        bridge = SimpleNamespace(active=True, segment_counts={0xAF: '3', 1: 2}, dropped=9)
        return transport, bridge, base

    def test_collision_overrides_formatting_and_unowned_tail(self):
        transport, bridge, base = self.sources()
        result = capture_polar_link_health(lambda: transport, lambda: bridge)
        self.assertEqual(list(result['segment_counts']), ['0x01', '0xAF'])
        self.assertEqual(result['segment_counts'], {'0x01': 2, '0xAF': 3})
        self.assertEqual(result['decode'], {'frames_ok': 1, 'crc8_errors': 2, 'crc16_errors': 3,
                         'unknown_segments': 4, 'length_errors': 5, 'buffer_overflows': 6})
        self.assertIs(result['link_alive'], True)
        self.assertEqual(result['dropped_segments'], 9)
        self.assertIs(result['extra'], base['extra'])
        self.assertEqual(base['decode'], 'old')
        self.assertFalse({'authority', 'motion', 'timestamp_roll_detected'} & result.keys())

    def test_source_is_reobserved_at_each_leaf_and_each_capture(self):
        reads = {'transport': 0, 'bridge': 0}
        def transport():
            reads['transport'] += 1
            value = reads['transport']
            counters = {key: value for key in ('frames_ok', 'crc8_errors', 'crc16_errors',
                        'unknown_segments', 'length_errors', 'buffer_overflows')}
            return SimpleNamespace(stats=lambda: {}, codec=SimpleNamespace(stats=SimpleNamespace(**counters)))
        def bridge():
            reads['bridge'] += 1
            value = reads['bridge']
            return SimpleNamespace(active=True, segment_counts={1: value}, dropped=value)
        first = capture_polar_link_health(transport, bridge)
        self.assertEqual(list(first['decode'].values()), [2, 3, 4, 5, 6, 7])
        self.assertEqual(first['segment_counts'], {'0x01': 2})
        self.assertEqual(first['dropped_segments'], 3)
        second = capture_polar_link_health(transport, bridge)
        self.assertEqual(list(second['decode'].values()), [9, 10, 11, 12, 13, 14])
        self.assertEqual(second['segment_counts'], {'0x01': 5})
        self.assertEqual(second['dropped_segments'], 6)
        self.assertEqual(first['dropped_segments'], 3)

    def test_malformed_counts_keep_original_conversion_failures(self):
        transport, bridge, _ = self.sources()
        for counts, error in (({1: 'bad'}, ValueError), ({1: 1, 'bad': 2}, TypeError)):
            with self.subTest(counts=counts):
                bridge.segment_counts = counts
                with self.assertRaises(error):
                    capture_polar_link_health(lambda: transport, lambda: bridge)

    def test_module_import_has_no_backend_or_frontend_runtime_dependencies(self):
        result = subprocess.run([sys.executable, '-c', '''
import sys
from rm_client.backend.polar_health import capture_polar_link_health
assert not any(name.startswith(('PySide6', 'serial', 'rm_client.model',
 'rm_client.protocol', 'rm_client.backend.manager', 'rm_client.backend.polar_transport',
 'rm_client.backend.polar_bridge')) for name in sys.modules)
'''], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
