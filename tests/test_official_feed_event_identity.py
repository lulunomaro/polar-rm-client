"""Real codec/adapter and actual offscreen window combat counters; inert devices.

These assertions preserve the existing alive_state==2 interpretation, without
claiming independent authority for that enum's meaning. No physical output.
"""
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import test_window_economy_commands as window_fixture
from rm_client.backend.referee.registry import get_profile
from rm_client.backend.referee.wire import encode_uint, encode_packed_uints
from rm_client.combat_events import OfficialMatchFeed


class OfficialFeedEventIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = window_fixture.WindowEconomyCommands()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = self.fixture.window
        self.w.registry.get("EliminationFX").enabled = False
        self.w.registry.get("MatchChat").enabled = False
        self.profile = get_profile()
        self.w.state.referee_profile = self.profile.profile_id
        self.adapter = self.profile.create_state_adapter(SimpleNamespace(
            safety=SimpleNamespace(update_stage=lambda value: None),
            _last_logged_stage="", field_log=SimpleNamespace(emit=lambda *a, **k: None)))
        self.events = []
        accept = self.w.accept_combat_event
        def record(event):
            self.events.append(event)
            accept(event)  # Actual window's counter/feedback handling, not a substitute.
        observer = patch.object(self.w, "accept_combat_event", side_effect=record)
        observer.start()
        self.addCleanup(observer.stop)

    def reset(self, alliance="Red", robot_id=None):
        self.w.official_feed = OfficialMatchFeed()
        self.w._official_state_updates = {}
        self.w.state.alliance_color = alliance
        self.w.alliance_side = lambda: alliance
        self.w.state.robot_id = robot_id or (3 if alliance == "Red" else 103)
        self.w.state.referee_kill_count = 0
        self.w.state.kill_streak = 4
        self.events.clear()

    def deliver(self, name, payload):
        data = self.profile.codec.decode_message(name, payload)
        self.assertTrue(data, "Counterexample must pass canonical codec validation")
        self.adapter._apply_one(self.w.state, name, data)
        with patch.object(self.w.mqtt, "drain_received_messages",
                          return_value=[(name, payload, time.perf_counter())]):
            self.w.consume_official_messages()
        self.w.apply_official_state()

    def health(self, values):
        self.deliver("GlobalUnitStatus", encode_packed_uints(11, values))

    def test_missing_and_unsupported_static_identity_cannot_create_event(self):
        for robot_id in (None, 0, 10, 100, 110, 99999):
            with self.subTest(robot_id=robot_id):
                self.reset()
                payload = encode_uint(3, 2)
                if robot_id is not None:
                    payload += encode_uint(4, robot_id)
                self.deliver("RobotStaticStatus", payload)
                self.assertEqual(self.events, [])
                self.assertEqual(self.w.state.referee_kill_count, 0)
                self.assertEqual(self.w.state.kill_streak, 4)

    def test_explicit_supported_local_ids_keep_existing_death_behavior(self):
        for offset in (0, 100):
            for role in range(1, 10):
                with self.subTest(robot_id=offset + role):
                    self.reset("Blue" if offset else "Red", offset + role)
                    self.deliver("RobotStaticStatus", encode_uint(3, 2) + encode_uint(4, offset + role))
                    self.assertEqual([(e.kind, e.victim_id) for e in self.events], [("death", offset + role)])
                    self.assertEqual(self.w.state.referee_kill_count, 0)
                    self.assertEqual(self.w.state.kill_streak, 0)

    def test_fixed_enemy_order_for_full_and_partial_vectors(self):
        for alliance, offset in (("Red", 100), ("Blue", 0)):
            for role_index, role in enumerate((1, 2, 3, 4, 7)):
                for length in (6 + role_index, 10):
                    with self.subTest(alliance=alliance, role=role, length=length):
                        self.reset(alliance)
                        health = [100] * length
                        self.health(health)
                        health[5 + role_index] = 0
                        self.health(health)
                        self.assertEqual([(e.kind, e.victim_id) for e in self.events], [("elimination", offset + role)])
                        self.assertEqual(self.w.state.referee_kill_count, 1)
                        self.assertEqual(self.w.state.kill_streak, 5)

    def test_short_allied_vectors_and_excess_tail_cannot_invent_enemy(self):
        for alliance in ("Red", "Blue"):
            for length, index in ((1, 0), (5, 2), (5, 4), (11, 10), (15, 14)):
                with self.subTest(alliance=alliance, length=length, index=index):
                    self.reset(alliance)
                    health = [100] * length
                    self.health(health)
                    health[index] = 0
                    self.health(health)
                    self.assertEqual(self.events, [])
                    self.assertEqual(self.w.state.referee_kill_count, 0)
                    self.assertEqual(self.w.state.kill_streak, 4)

    def test_missing_enemy_observation_does_not_become_zero_or_stale_transition(self):
        for alliance in ("Red", "Blue"):
            with self.subTest(alliance=alliance):
                self.reset(alliance)
                self.health([100] * 10)
                self.health([100] * 5)
                self.health([100] * 5 + [0] * 5)
                self.assertEqual(self.events, [])
                self.assertEqual(self.w.state.referee_kill_count, 0)


if __name__ == "__main__":
    unittest.main()
