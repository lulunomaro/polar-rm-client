"""Canonical scalar-writer acceptance after baseline characterization b65d7ec.

Raw receipt freshness/events remain separate from canonical scalar application.
Actual offscreen window/backend/validated queues; transport starts are inert.
No native playback/driver qualification follows from these checks.
"""
from copy import deepcopy
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import test_window_economy_commands as fixtures
from test_official_feed_projection_authority import ProjectionHarness, integer, packed, floating, varint
from rm_client.combat_events import OfficialMatchFeed, _STATE_UPDATE_FIELDS
from rm_client.referee_profiles import DEFAULT_PROFILE, STUDENT_PREVIEW_PROFILE


class OfficialScalarWriterCharacterization(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WindowEconomyCommands()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.w = self.fixture.window
        self.w.state.official_state_active = True
        self.w.state.mock_simulation_enabled = False
        self.w.state.robot_id = 3
        self.w.registry.get("EliminationFX").enabled = False
        self.w.registry.get("MatchChat").enabled = False

    def receive(self, topic, payload):
        mqtt = self.w.mqtt
        invalid = mqtt.invalid_messages
        mqtt._on_message(mqtt.client, None, SimpleNamespace(topic=topic, payload=payload))
        self.assertEqual(mqtt.invalid_messages, invalid, "fixture must pass real codec")

    def raw(self):
        self.w.consume_official_messages()
        self.w.apply_official_state()

    def test_scalar_families_remain_adapter_owned_for_both_profiles(self):
        # Test every scalar key projected by the feed, including explicit zero.
        for profile in (DEFAULT_PROFILE, STUDENT_PREVIEW_PROFILE):
            for name, keys in _STATE_UPDATE_FIELDS.items():
                for value in (0, 1):
                    with self.subTest(profile=profile, topic=name, value=value):
                        host = ProjectionHarness(profile)
                        fields = sorted({number for required in keys.values() for number in required})
                        payload = b"".join(
                            packed(n, [value] * 10) if name == "GlobalUnitStatus" and n == 11
                            else floating(n, value) if name == "RobotDynamicStatus" and n == 2
                            else integer(n, value) for n in fields)
                        before = host.deliver(name, payload)
                        for key in keys:
                            self.assertEqual(getattr(host.state, key), before[key], key)
                            if key == "air_support_can_free":
                                self.assertEqual(before[key], bool(value))

    def test_air_support_derived_boolean_is_canonical_and_explicit_zero_clears(self):
        self.w.state.air_support_can_free = False
        self.receive("AirSupportStatusSync", integer(2, 15))
        self.assertEqual(self.w.backend.apply_updates(self.w.state), 1)
        self.assertEqual(self.w.state.air_support_free_time_sec, 15)
        self.assertTrue(self.w.state.air_support_can_free)
        self.raw()
        self.assertTrue(self.w.state.air_support_can_free)
        self.receive("AirSupportStatusSync", integer(2, 0))
        self.w.backend.apply_updates(self.w.state)
        self.assertFalse(self.w.state.air_support_can_free)
        self.raw()
        self.assertFalse(self.w.state.air_support_can_free)

    def test_omitted_free_time_respects_profile_presence_contract(self):
        for profile in (DEFAULT_PROFILE, STUDENT_PREVIEW_PROFILE):
            with self.subTest(profile=profile):
                host = ProjectionHarness(profile)
                host.deliver("AirSupportStatusSync", integer(2, 15))
                self.assertTrue(host.state.air_support_can_free)
                host.deliver("AirSupportStatusSync", integer(1, 1))
                # Legacy codec supplies proto defaults; its adapter deliberately
                # treats those named fields as present, unlike current profile.
                expected = profile == DEFAULT_PROFILE
                self.assertEqual(host.state.air_support_can_free, expected)

    def test_explicit_direct_apply_compatibility_remains_available(self):
        self.w._official_state_updates = {"remaining_economy": 77}
        self.w.apply_official_state()
        self.assertEqual(self.w.state.remaining_economy, 77)

    def test_partial_respawn_and_economy_revision_are_adapter_owned(self):
        for payload, expected in ((integer(2, 100), None), (integer(3, 25), .25)):
            self.receive("RobotRespawnStatus", payload)
            self.w.backend.apply_updates(self.w.state)
            before = self.w.state.respawn_progress
            self.raw()
            self.assertEqual(self.w.state.respawn_progress, before)
            if expected is not None:
                self.assertEqual(before, expected)
        self.w.state.remaining_economy = 9
        revision = self.w.state.economy_status_revision
        self.w.state.economy_command_status = "pending"
        self.receive("GlobalLogisticsStatus", integer(1, 77))
        self.w.backend.apply_updates(self.w.state)
        self.assertEqual(self.w.state.economy_status_revision, revision + 1)
        self.assertEqual(self.w.state.economy_command_status, "")
        self.raw()
        self.assertEqual(self.w.state.economy_status_revision, revision + 1)

    def test_between_stage_arrival_waits_for_canonical_hp_and_rows(self):
        self.receive("GlobalUnitStatus", packed(11, [100] * 10))
        self.w.backend.apply_updates(self.w.state)
        self.raw()
        rows = deepcopy(self.w.state.robot_rows)
        self.assertEqual(self.w.backend.apply_updates(self.w.state), 0)
        self.receive("GlobalUnitStatus", packed(11, [55] * 10))
        self.raw()  # Arrives after backend stage, before official stage.
        self.assertEqual(self.w.state.official_robot_health, [100] * 10)
        self.assertEqual(self.w.state.robot_rows, rows)
        self.assertEqual(self.w.backend.apply_updates(self.w.state), 1)
        self.assertNotEqual(self.w.state.robot_rows, rows)
        self.assertEqual(self.w.state.official_robot_health, [55] * 10)

    def test_adapter_failure_has_no_raw_scalar_fallback(self):
        self.w.state.remaining_economy = 9
        self.receive("GlobalLogisticsStatus", integer(1, 77))
        with patch.object(self.w.backend.referee_adapter, "_apply_one", side_effect=RuntimeError("injected apply failure")):
            self.assertEqual(self.w.backend.apply_updates(self.w.state), 0)
        self.assertEqual(self.w.state.remaining_economy, 9)
        self.raw()
        self.assertEqual(self.w.state.remaining_economy, 9)
        self.assertTrue(self.w.state.official_events_online)  # Receipt, not apply success.
        self.receive("GlobalLogisticsStatus", integer(1, 88))
        self.assertEqual(self.w.backend.apply_updates(self.w.state), 1)
        self.assertEqual(self.w.state.remaining_economy, 88)

    def test_transition_block_has_no_raw_scalar_fallback(self):
        self.w.state.remaining_economy = 9
        self.receive("GlobalLogisticsStatus", integer(1, 77))
        with patch.object(self.w.backend, "_backend_transition_blocks_work", return_value=True):
            self.assertEqual(self.w.backend.apply_updates(self.w.state), 0)
        self.raw()
        self.assertEqual(self.w.state.remaining_economy, 9)
        self.assertEqual(self.w.backend.apply_updates(self.w.state), 1)
        self.assertEqual(self.w.state.remaining_economy, 77)

    def test_event_delivery_and_public_scalar_return_are_separate_contracts(self):
        count = self.w.state.referee_kill_count
        self.receive("Event", integer(1, 1))
        self.w.backend.apply_updates(self.w.state)
        self.assertEqual(self.w.state.referee_kill_count, count)
        self.raw()
        self.assertEqual(self.w.state.referee_kill_count, count + 1)
        self.assertTrue(self.w.state.official_events_online)
        events, updates = OfficialMatchFeed().consume(
            "GlobalLogisticsStatus", integer(1, 77), alliance="Red", local_robot_id=3)
        self.assertEqual(events, [])
        self.assertEqual(updates["remaining_economy"], 77)
        self.assertGreater(updates["official_feed_at"], 0)

    def test_native_custom_block_is_decoded_only_and_raw_keeps_freshness(self):
        fmt = "<4sHHIQIHHHHIBBH9f"
        block = struct.pack(fmt, b"PDL1", 1, struct.calcsize(fmt), 3, 123456, 77,
                            1280, 720, 6000, 100, 3200, 1, 0, 12,
                            15.5, .4, -3.25, -.2, 16.8, 0., 0., 0., 1.)
        self.receive("CustomByteBlock", varint(10) + varint(len(block)) + block)
        self.assertEqual(self.w.backend.apply_updates(self.w.state), 1)
        self.assertEqual(self.w.state.native_telemetry_frame_seq, 77)
        self.assertEqual(self.w.state.native_bullet_count, 12)
        native_at = self.w.state.native_telemetry_at
        self.raw()
        self.assertEqual(self.w.state.native_telemetry_at, native_at)
        self.assertTrue(self.w.state.official_events_online)
        self.assertGreater(self.w.state.official_feed_at, 0)


if __name__ == "__main__":
    unittest.main()
