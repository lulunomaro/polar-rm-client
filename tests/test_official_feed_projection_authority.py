"""Real codec -> decoded adapter -> raw-feed UI projection, without native owners."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import struct
import sys
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.referee.registry import get_profile
from rm_client.backend.referee.rm2026.state_adapter import Rm2026StateAdapter
from rm_client.combat_events import OfficialMatchFeed
from rm_client.model import RobotState
from rm_client.referee_profiles import DEFAULT_PROFILE, STUDENT_PREVIEW_PROFILE
from rm_client.ui.polar.mixins.video_backend_mixin import VideoBackendMixin


def varint(value):
    value &= (1 << 64) - 1
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def integer(number, value):
    return varint(number << 3) + varint(value)


def packed(number, values):
    payload = b''.join(varint(value) for value in values)
    return varint((number << 3) | 2) + varint(len(payload)) + payload


def floating(number, value):
    return varint((number << 3) | 5) + struct.pack('<f', value)


class ProjectionHarness:
    """No sockets/window: invoke production methods on their inert collaborators."""
    def __init__(self, profile_id=DEFAULT_PROFILE):
        self.state = RobotState(referee_profile=profile_id, official_state_active=True,
                                mock_simulation_enabled=False)
        self.profile = get_profile(profile_id)
        self.adapter = self.profile.create_state_adapter(SimpleNamespace(
            safety=SimpleNamespace(update_stage=lambda value: None),
            _last_logged_stage=4,
            field_log=SimpleNamespace(emit=lambda *args, **kwargs: None),
        ))
        self.official_feed = OfficialMatchFeed()
        self._official_state_updates = {}
        self.accepted = []
        self.accept_combat_event = self.accepted.append
        self.alliance_side = lambda: 'Red'
        self.mqtt = SimpleNamespace(drain_received_messages=lambda: [])

    def deliver(self, name, payload):
        decoded = self.profile.codec.decode_message(name, payload)
        if not decoded:
            raise AssertionError('Test input was rejected by the real selected codec')
        self.adapter._apply_one(self.state, name, decoded)
        before_raw = deepcopy(vars(self.state))
        # Projection fixtures represent freshly queued messages. Delayed and
        # invalid receipt times have their own real-queue freshness regression.
        received_at = time.perf_counter()
        self.mqtt.drain_received_messages = lambda: [(name, payload, received_at)]
        VideoBackendMixin.consume_official_messages(self)
        VideoBackendMixin.apply_official_state(self)
        return before_raw


class OfficialFeedProjectionAuthorityTests(unittest.TestCase):
    def test_partial_messages_preserve_fields_omitted_from_valid_payload(self):
        cases = (
            ('GameStatus', integer(3, 7), ('current_stage', 'stage_countdown_sec', 'current_round')),
            ('GlobalLogisticsStatus', integer(3, 2), ('remaining_economy', 'total_economy_obtained')),
            ('GlobalUnitStatus', integer(6, 1234), ('base_health', 'outpost_health', 'official_robot_health')),
            ('RobotDynamicStatus', integer(9, 42), ('current_health', 'current_heat', 'can_remote_heal')),
            ('RobotRespawnStatus', integer(5, 12), ('respawn_pending', 'respawn_progress', 'respawn_can_free')),
            ('AirSupportStatusSync', integer(3, 25), ('air_support_active', 'air_support_free_time_sec', 'air_support_can_free')),
        )
        for name, payload, fields in cases:
            with self.subTest(name=name):
                host = ProjectionHarness()
                host.state.current_stage = 'MATCH'
                host.state.stage_countdown_sec = 123
                host.state.respawn_pending = True
                host.state.respawn_progress = .6
                host.state.respawn_can_free = True
                host.state.air_support_active = True
                host.state.air_support_free_time_sec = 10
                host.state.air_support_can_free = True
                host.state.official_robot_health = [100] * 10
                before = host.deliver(name, payload)
                self.assertEqual({key: getattr(host.state, key) for key in fields},
                                 {key: before[key] for key in fields})

    def test_partial_unrelated_update_does_not_fabricate_economy_confirmation(self):
        host = ProjectionHarness()
        host.state.economy_command_status = 'WAITING'
        before = host.deliver('GlobalLogisticsStatus', integer(3, 2))
        self.assertEqual(host.state.economy_status_revision, before['economy_status_revision'])
        self.assertEqual(host.state.economy_command_status, 'WAITING')

    def test_explicit_zero_and_false_are_still_authoritative(self):
        host = ProjectionHarness()
        payload = (integer(1, 0) + floating(2, 0.0) + integer(9, 0)
                   + integer(10, 0) + integer(12, 0) + integer(13, 0))
        host.deliver('RobotDynamicStatus', payload)
        self.assertEqual((host.state.current_health, host.state.current_heat,
                          host.state.remaining_ammo), (0, 0.0, 0))
        self.assertFalse(host.state.out_of_combat)
        self.assertFalse(host.state.can_remote_heal)
        self.assertFalse(host.state.can_remote_ammo)
        host.deliver('GameStatus', integer(5, 0) + integer(6, 0))
        self.assertEqual((host.state.current_stage, host.state.stage_countdown_sec),
                         ('NOT_STARTED', 0))

    def test_current_enemy_base_field_never_becomes_roster_or_elimination(self):
        host = ProjectionHarness()
        expected = [100] * 10
        host.state.official_robot_health = list(expected)
        for health in (1234, 0):
            host.deliver('GlobalUnitStatus', integer(6, health))
            self.assertEqual(host.state.enemy_base_health, health)
            self.assertEqual(host.state.official_robot_health, expected)
        self.assertEqual(host.accepted, [])

    def test_real_current_roster_zero_transition_still_emits_elimination(self):
        host = ProjectionHarness()
        health = [100] * 10
        host.deliver('GlobalUnitStatus', packed(11, health))
        health[5] = 0
        host.deliver('GlobalUnitStatus', packed(11, health))
        self.assertEqual(host.state.official_robot_health, health)
        self.assertEqual([(event.kind, event.victim_id) for event in host.accepted],
                         [('elimination', 101)])

    def test_legacy_roster_remains_diagnostic_only_through_real_ui_consumer(self):
        host = ProjectionHarness(STUDENT_PREVIEW_PROFILE)
        health = [100] * 14
        host.deliver('GlobalUnitStatus', packed(6, health))
        health[7] = 0
        host.deliver('GlobalUnitStatus', packed(6, health))
        self.assertEqual(host.state.official_robot_health, [])
        self.assertEqual(host.accepted, [])
        self.assertEqual(host.state.backend_stats['student_legacy_roster']['robot_health'], health)

    def test_profile_handoff_cannot_compare_new_roster_to_old_profile_history(self):
        feed = OfficialMatchFeed()
        feed.consume('GlobalUnitStatus', packed(11, [100] * 10),
                     alliance='Red', local_robot_id=3)
        feed.consume('GlobalUnitStatus', packed(6, [100] * 14),
                     alliance='Red', local_robot_id=3,
                     referee_profile=STUDENT_PREVIEW_PROFILE)
        health = [100] * 10
        health[5] = 0
        events, updates = feed.consume('GlobalUnitStatus', packed(11, health),
                                       alliance='Red', local_robot_id=3)
        self.assertEqual(events, [])
        self.assertEqual(updates['official_robot_health'], health)

    def test_signed_negative_countdown_keeps_decoded_clamping(self):
        host = ProjectionHarness()
        before = host.deliver('GameStatus', integer(6, -1) + integer(7, -3))
        self.assertEqual(before['stage_countdown_sec'], 0)
        self.assertEqual((host.state.stage_countdown_sec, host.state.stage_elapsed_sec), (0, 0))

    def test_partial_respawn_preserves_adapter_accumulation_and_zero_denominator(self):
        host = ProjectionHarness()
        host.deliver('RobotRespawnStatus', integer(2, 100) + integer(3, 25))
        before = host.deliver('RobotRespawnStatus', integer(3, 50))
        self.assertEqual(before['respawn_progress'], .5)
        self.assertEqual(host.state.respawn_progress, .5)
        host.deliver('RobotRespawnStatus', integer(2, 0) + integer(3, 1))
        self.assertEqual(host.state.respawn_progress, 1.0)

    def test_later_live_polar_overlay_wins_only_within_allowlist(self):
        host = ProjectionHarness()
        host.state.current_health = 321
        host.deliver('RobotDynamicStatus', integer(9, 42))
        self.assertEqual(host.state.current_health, 321)
        host.state.polar_link_active = True
        host.state.polar_overlay = {'current_health': 250, 'remaining_ammo': 999,
                                    'official_robot_health': [999]}
        host.state.apply_polar_overlay()
        self.assertEqual((host.state.current_health, host.state.remaining_ammo), (250, 42))
        self.assertEqual(host.state.official_robot_health, [])
        host.state.polar_link_active = False
        host.deliver('RobotDynamicStatus', integer(1, 300))
        host.state.apply_polar_overlay()
        self.assertEqual(host.state.current_health, 300)


if __name__ == '__main__':
    unittest.main(verbosity=2)
