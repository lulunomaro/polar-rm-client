"""Dependency-free veto decisions; these never grant arm or native ownership."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.backend.port_admission import PortReservation, first_port_conflict


class PortAdmissionPolicyTests(unittest.TestCase):
    def test_explicit_matching_and_disjoint_claims(self):
        self.assertEqual(first_port_conflict({'rx', 'tx'}, [PortReservation('CC', 'other')]), '')
        self.assertIn('CC port may conflict', first_port_conflict({'rx', 'tx'}, [PortReservation('CC', 'tx')]))

    def test_unresolved_auto_vetoes_even_without_named_overlap(self):
        self.assertIn('DT7 port may conflict', first_port_conflict(set(), [PortReservation('DT7', unresolved_auto=True)]))
        self.assertEqual(first_port_conflict({'stub'}, [PortReservation('none')]), '')

    def test_first_conflict_does_not_consume_later_observations(self):
        def claims():
            yield PortReservation('first', 'rx')
            raise AssertionError('later alias must not be observed')
        self.assertTrue(first_port_conflict({'rx'}, claims()).startswith('first '))

    def test_late_observation_failure_is_not_hidden_without_an_earlier_conflict(self):
        primary = OSError('alias observation')
        def claims():
            yield PortReservation('disjoint', 'other')
            raise primary
        with self.assertRaises(OSError) as raised:
            first_port_conflict({'rx'}, claims())
        self.assertIs(raised.exception, primary)

    def test_import_does_not_load_transport_model_or_gui(self):
        result = subprocess.run([sys.executable, '-c', '''
import sys
from rm_client.backend.port_admission import PortReservation, first_port_conflict
assert first_port_conflict({'rx'}, [PortReservation('x', 'other')]) == ''
assert not any(name.startswith(('PySide6', 'serial', 'rm_client.model',
    'rm_client.protocol', 'rm_client.backend.controller_angles', 'rm_client.backend.manager'))
    for name in sys.modules)
'''], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
