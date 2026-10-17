"""Pure projection decisions are detached from runtime state and host effects."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.referee.rm2026 import projection_policy
from rm_client.backend.referee.rm2026.projection_policy import (
    ProjectileDamageBaseline, ProjectileDamageSample, assess_projectile_damage,
    select_present_fields,
)


class RefereeProjectionPolicyTests(unittest.TestCase):
    def test_legacy_mapping_without_metadata_keeps_identity(self):
        data = {"status": 1, "extension": "unchanged"}
        selected, present = select_present_fields("DeployModeStatusSync", data)
        self.assertIs(selected, data)
        self.assertEqual(present, frozenset())

    def test_absent_fields_differ_from_explicit_zero(self):
        data = {"current_health": 0, "current_heat": 0., "__present__": (1,)}
        selected, present = select_present_fields("RobotDynamicStatus", data)
        self.assertEqual(selected, {"current_health": 0, "__present__": (1,)})
        self.assertEqual(present, frozenset({1}))
        self.assertIn("current_heat", data)
        self.assertIsNot(selected, data)

    def test_presence_uses_protocol_aliases_without_copying_payloads(self):
        payload = [1, 2]
        data = {"local_id": payload, "robot_id": 9, "__present__": (4, 999)}
        with patch.dict(projection_policy.ALIASES, {("RobotStaticStatus", "robot_id"): "local_id"}):
            selected, present = select_present_fields("RobotStaticStatus", data)
        self.assertEqual(set(selected), {"local_id", "__present__"})
        self.assertIs(selected["local_id"], payload)
        self.assertEqual(present, frozenset({4, 999}))

    def test_unknown_names_and_malformed_presence_keep_original_boundary(self):
        selected, present = select_present_fields("FutureMessage", {"status": 1, "__present__": (1,)})
        self.assertEqual(selected, {"__present__": (1,)})
        self.assertEqual(present, frozenset({1}))
        for malformed in (None, [[]]):
            with self.subTest(metadata=malformed), self.assertRaises(TypeError):
                select_present_fields("GameStatus", {"__present__": malformed})

    def test_first_counter_values_are_baselines_even_when_totals_are_large(self):
        baseline = ProjectileDamageBaseline(False, False, False, 0, 0)
        first = assess_projectile_damage(baseline, ProjectileDamageSample(True, False, 500, 0))
        second = assess_projectile_damage(first.next_baseline, ProjectileDamageSample(False, True, 500, 1000))
        self.assertEqual(first.damage_delta, 0)
        self.assertEqual(second.damage_delta, 0)
        self.assertTrue(second.next_baseline.small_initialized)
        self.assertTrue(second.next_baseline.large_initialized)
        self.assertEqual(baseline, ProjectileDamageBaseline(False, False, False, 0, 0))

    def test_known_delta_and_first_counter_are_independent(self):
        baseline = ProjectileDamageBaseline(True, True, False, 20, 0)
        result = assess_projectile_damage(baseline, ProjectileDamageSample(True, True, 25, 100))
        self.assertEqual((result.small_delta, result.large_delta, result.damage_delta), (5, 0, 5))
        self.assertEqual(result.projectile_kind, "17mm")
        result = assess_projectile_damage(result.next_baseline, ProjectileDamageSample(False, True, 25, 103))
        self.assertEqual((result.damage_delta, result.projectile_kind), (3, "42mm"))

    def test_counter_reset_invalidates_absent_counter_until_reobserved(self):
        baseline = ProjectileDamageBaseline(True, True, True, 20, 10)
        reset = assess_projectile_damage(baseline, ProjectileDamageSample(True, False, 0, 10))
        self.assertEqual(reset.damage_delta, 0)
        self.assertTrue(reset.next_baseline.small_initialized)
        self.assertFalse(reset.next_baseline.large_initialized)
        first_large = assess_projectile_damage(reset.next_baseline, ProjectileDamageSample(False, True, 0, 100))
        self.assertEqual(first_large.damage_delta, 0)
        next_large = assess_projectile_damage(first_large.next_baseline, ProjectileDamageSample(False, True, 0, 103))
        self.assertEqual(next_large.damage_delta, 3)

    def test_mixed_reset_suppresses_all_deltas_and_valid_pair_combines(self):
        baseline = ProjectileDamageBaseline(True, True, True, 20, 10)
        reset = assess_projectile_damage(baseline, ProjectileDamageSample(True, True, 0, 100))
        self.assertEqual(reset.damage_delta, 0)
        result = assess_projectile_damage(reset.next_baseline, ProjectileDamageSample(True, True, 2, 101))
        self.assertEqual((result.damage_delta, result.projectile_kind), (3, "17mm+42mm"))

    def test_snapshot_sample_and_decision_are_immutable_values(self):
        baseline = ProjectileDamageBaseline(False, False, False, 0, 0)
        sample = ProjectileDamageSample(True, False, 10, 0)
        result = assess_projectile_damage(baseline, sample)
        for value, name in ((baseline, "small_total"), (sample, "small_total"), (result, "small_delta")):
            with self.subTest(type=type(value).__name__), self.assertRaises(FrozenInstanceError):
                setattr(value, name, 99)
            self.assertFalse(hasattr(value, "__dict__"))

    def test_import_needs_neither_qt_runtime_state_nor_host_devices(self):
        code = '''
import builtins, importlib
original = builtins.__import__
def blocked(name, *args, **kwargs):
    forbidden = ("PySide6", "PyQt6", "rm_client.model", "rm_client.ui",
                 "rm_client.backend.manager", "rm_client.backend.safety", "serial")
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden):
        raise AssertionError("projection imported " + name)
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
module = importlib.import_module("rm_client.backend.referee.rm2026.projection_policy")
assert module.select_present_fields("DeployModeStatusSync", {"status": 0})[0] == {"status": 0}
print("PASS: isolated projection import")
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: isolated projection import", result.stdout)


if __name__ == "__main__":
    unittest.main()
