"""Legal optional injury packets must not turn first totals into spin requests."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.auto_spin import ProjectileHitSpinController
from rm_client.backend.manager import BackendManager
from rm_client.backend.referee.rm2026.codec import decode_message
from rm_client.backend.referee.rm2026.state_adapter import Rm2026StateAdapter
from rm_client.model import RobotState


def uint_field(number, value):
    """Independent encoder for the actual uint32 wire fields used here."""
    result = bytearray([number << 3])
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


class ProjectileCounterBaselineTests(unittest.TestCase):
    def setUp(self):
        self.state = RobotState(current_stage="MATCH", current_health=500,
                                current_buffer_energy=50)
        self.adapter = Rm2026StateAdapter(SimpleNamespace())
        self.controller = ProjectileHitSpinController()
        # Exercise the real consumer with its existing default gates satisfied.
        self.feature = SimpleNamespace(enabled=True, get_setting=lambda name: None)

    def deliver(self, *, small=None, large=None, killer=None):
        values = ((3, small), (4, large), (10, killer))
        payload = b"".join(uint_field(number, value) for number, value in values if value is not None)
        data = decode_message("RobotInjuryStat", payload)
        self.assertTrue(data)
        self.assertEqual(data["__present__"], frozenset(number for number, value in values if value is not None))
        self.adapter._apply_one(self.state, "RobotInjuryStat", data)
        return self.controller.update(self.state, self.feature, spin_enabled=False,
                                      referee_link=True, now=10.0)

    def test_first_small_then_first_large_never_requests_spin(self):
        self.assertEqual(self.deliver(small=20).action, "none")
        self.assertEqual(self.deliver(large=10).action, "none")
        self.assertEqual(self.state.projectile_hit_revision, 0)
        self.assertEqual((self.state.projectile_damage_small_total,
                          self.state.projectile_damage_large_total), (20, 10))

    def test_first_large_then_first_small_never_requests_spin(self):
        self.assertEqual(self.deliver(large=10).action, "none")
        self.assertEqual(self.deliver(small=20).action, "none")
        self.assertEqual(self.state.projectile_hit_revision, 0)

    def test_missing_counter_is_not_an_observed_zero_baseline(self):
        self.deliver(small=0)
        for _ in range(3):
            self.assertEqual(self.deliver(killer=103).action, "none")
        self.assertEqual(self.deliver(large=100).action, "none")
        self.assertEqual(self.state.projectile_hit_revision, 0)

    def test_explicit_zero_is_a_real_baseline_for_later_positive_delta(self):
        for first, later in (({"small": 0}, {"small": 3}),
                             ({"large": 0}, {"large": 4})):
            with self.subTest(counter=first):
                self.setUp()
                self.assertEqual(self.deliver(**first).action, "none")
                directive = self.deliver(**later)
                self.assertEqual(directive.action, "enable")
                self.assertEqual(directive.damage_delta, next(iter(later.values())))
                self.assertEqual(self.state.projectile_hit_revision, 1)

    def test_known_delta_and_first_other_counter_count_only_proven_delta(self):
        self.deliver(small=20)
        directive = self.deliver(small=25, large=100, killer=103)
        self.assertEqual(directive.action, "enable")
        self.assertEqual(directive.damage_delta, 5)
        self.assertEqual(self.state.last_projectile_kind, "17mm")
        self.assertEqual(self.state.last_projectile_killer_id, 103)
        directive = self.deliver(large=104)
        self.assertEqual(directive.damage_delta, 4)
        self.assertEqual(self.state.last_projectile_kind, "42mm")

    def test_observed_counters_keep_existing_combined_delta_and_duplicate_semantics(self):
        self.assertEqual(self.deliver(small=20, large=10).action, "none")
        directive = self.deliver(small=25, large=17)
        self.assertEqual((directive.action, directive.damage_delta), ("enable", 12))
        self.assertEqual(self.state.last_projectile_kind, "17mm+42mm")
        revision = self.state.projectile_hit_revision
        self.assertEqual(self.deliver(small=25, large=17).action, "none")
        self.assertEqual(self.state.projectile_hit_revision, revision)

    def test_backwards_counter_rebaselines_both_present_counters_without_hit(self):
        self.deliver(small=20, large=10)
        self.assertEqual(self.deliver(small=0, large=100).action, "none")
        self.assertEqual(self.state.projectile_hit_revision, 0)
        directive = self.deliver(small=2, large=101)
        self.assertEqual((directive.action, directive.damage_delta), ("enable", 3))

    def test_partial_counter_reset_invalidates_absent_counter_baseline(self):
        for first, reset, other in (({"small": 20, "large": 10}, {"small": 0}, {"large": 100}),
                                    ({"small": 20, "large": 10}, {"large": 0}, {"small": 100})):
            with self.subTest(reset=reset):
                self.setUp()
                self.deliver(**first)
                self.assertEqual(self.deliver(**reset).action, "none")
                self.assertEqual(self.deliver(**other).action, "none")
                self.assertEqual(self.state.projectile_hit_revision, 0)
                key = next(iter(other))
                directive = self.deliver(**{key: 103})
                self.assertEqual((directive.action, directive.damage_delta), ("enable", 3))

    def test_uint32_wrap_is_a_new_baseline_not_a_modular_hit(self):
        for key in ("small", "large"):
            with self.subTest(counter=key):
                self.setUp()
                self.assertEqual(self.deliver(**{key: 0xFFFFFFFF}).action, "none")
                self.assertEqual(self.deliver(**{key: 0}).action, "none")
                self.assertEqual(self.state.projectile_hit_revision, 0)
                directive = self.deliver(**{key: 2})
                self.assertEqual((directive.action, directive.damage_delta), ("enable", 2))

    def test_scenario_reset_rebaselines_each_counter_and_keeps_revision_monotonic(self):
        self.deliver(small=20, large=10)
        self.deliver(small=25)
        revision = self.state.projectile_hit_revision
        manager = object.__new__(BackendManager)
        manager._clear_mock_frontend_state = lambda state: None
        manager.polar_bridge = SimpleNamespace(reset=lambda: None)
        manager.polar_arbiter = SimpleNamespace(clear_lockout=lambda: None)
        manager.polar_motion = SimpleNamespace()
        manager._reset_state_for_scenario(self.state)
        self.assertFalse(self.state.injury_stats_initialized)
        self.assertEqual(self.state.projectile_hit_revision, revision)
        self.state.current_stage = "MATCH"
        self.assertEqual(self.deliver(large=100).action, "none")
        self.assertEqual(self.deliver(small=200).action, "none")
        self.assertEqual(self.state.projectile_hit_revision, revision)
        directive = self.deliver(small=201)
        self.assertEqual((directive.action, directive.damage_delta), ("enable", 1))
        self.assertEqual(self.state.projectile_hit_revision, revision + 1)


if __name__ == "__main__":
    unittest.main()
