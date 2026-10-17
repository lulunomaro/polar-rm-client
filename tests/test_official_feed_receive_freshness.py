"""Receipt-time freshness through real validated MQTT queues and UI adapters.

No MQTT start, sockets, hardware, native window or audio output is involved.
"""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
               str(Path(__file__).resolve().parent)]
from rm_client.backend.mqtt_backend import RmMqttBackend
from rm_client.backend.readiness import audit_backend
from rm_client.backend.referee.registry import get_profile
from rm_client.combat_events import OfficialMatchFeed
from rm_client.model import RobotState
from rm_client.referee_profiles import DEFAULT_PROFILE, STUDENT_PREVIEW_PROFILE
from rm_client.ui.polar.mixins.video_backend_mixin import VideoBackendMixin
from test_official_feed_projection_authority import integer, packed


class ReceiveHarness:
    def __init__(self):
        self.state = RobotState(official_state_active=True, mock_simulation_enabled=False)
        self.mqtt = RmMqttBackend()
        self.mqtt.client = object()  # Current callback identity, never a network client.
        self.official_feed = OfficialMatchFeed()
        self._official_state_updates = {}
        self.accepted = []
        self.accept_combat_event = self.accepted.append
        self.alliance_side = lambda: "Red"
        self.adapter = get_profile().create_state_adapter(SimpleNamespace(
            safety=self.mqtt.safety, _last_logged_stage="",
            field_log=SimpleNamespace(emit=lambda *args, **kwargs: None),
        ))

    def receive(self, name, payload, now):
        with patch("time.perf_counter", return_value=now):
            self.mqtt._on_message(self.mqtt.client, None, SimpleNamespace(topic=name, payload=payload))
        if self.mqtt.invalid_messages:
            raise AssertionError("Fixture must pass the real selected codec")

    def consume(self, now):
        with patch("time.perf_counter", return_value=now):
            for _topic, name, data, _received_at in self.mqtt.drain_decoded():
                self.adapter._apply_one(self.state, name, data)
            VideoBackendMixin.consume_official_messages(self)
            VideoBackendMixin.apply_official_state(self)

    def fresh_gate(self, now):
        with patch("time.perf_counter", return_value=now), \
             patch("rm_client.backend.readiness._ffmpeg_available", return_value=False):
            report = audit_backend(None, self.state)
        return next(gate.passed for gate in report.gates if gate.key == "fresh_referee_state")


class OfficialFeedReceiveFreshnessTests(unittest.TestCase):
    def test_delayed_validated_queue_does_not_refresh_or_emit_old_event(self):
        host = ReceiveHarness()
        host.receive("Event", integer(1, 1), 100.0)
        host.consume(104.0)
        self.assertEqual(host.accepted, [])
        self.assertFalse(host.state.official_events_online)
        self.assertEqual(host.state.official_feed_at, 0.0)
        self.assertFalse(host.fresh_gate(104.0))

    def test_valid_event_and_state_keep_actual_queue_receipt_time(self):
        host = ReceiveHarness()
        host.receive("Event", integer(1, 1), 100.0)
        host.consume(101.0)
        self.assertEqual(len(host.accepted), 1)
        self.assertEqual(host.accepted[0].timestamp, 100.0)
        self.assertEqual(host.state.official_feed_at, 100.0)
        self.assertTrue(host.state.official_events_online)
        self.assertTrue(host.fresh_gate(102.0))
        self.assertFalse(host.fresh_gate(102.0001))
        with patch("time.perf_counter", return_value=102.5001):
            self.assertFalse(host.official_feed.is_fresh())
            self.assertFalse(host.official_feed.is_kill_data_fresh())

    def test_receive_age_boundary_keeps_existing_two_point_five_second_window(self):
        for age, admitted in ((2.5, True), (2.5001, False)):
            with self.subTest(age=age):
                host = ReceiveHarness()
                host.receive("Event", integer(1, 1), 100.0)
                host.consume(100.0 + age)
                self.assertEqual(bool(host.accepted), admitted)
                self.assertEqual(host.state.official_events_online, admitted)
                self.assertFalse(host.fresh_gate(100.0 + age))

    def test_invalid_explicit_timestamp_cannot_change_feed_or_project_state(self):
        invalid = (None, True, False, "100", float("nan"), float("inf"),
                   float("-inf"), 0.0, -1.0, 101.0001, 10 ** 400, object())
        for timestamp in invalid:
            with self.subTest(timestamp=timestamp):
                feed = OfficialMatchFeed()
                with patch("time.perf_counter", return_value=101.0):
                    events, updates = feed.consume("RobotDynamicStatus", integer(1, 0),
                        alliance="Red", local_robot_id=3, received_at=timestamp)
                self.assertEqual((events, updates), ([], {}))
                self.assertEqual((feed.last_message_at, feed.last_kill_data_at,
                                  feed.last_official_event_at), (0.0, 0.0, 0.0))

    def test_omitted_timestamp_keeps_direct_caller_compatibility(self):
        feed = OfficialMatchFeed()
        with patch("time.perf_counter", return_value=101.0):
            events, updates = feed.consume("Event", integer(1, 1), alliance="Red", local_robot_id=3)
        self.assertEqual(events[0].timestamp, 101.0)
        self.assertEqual(updates["official_feed_at"], 101.0)

    def test_expired_packet_does_not_extend_existing_freshness(self):
        feed = OfficialMatchFeed()
        with patch("time.perf_counter", return_value=101.0):
            feed.consume("Event", integer(1, 1), alliance="Red", local_robot_id=3,
                         received_at=100.0)
        with patch("time.perf_counter", return_value=104.0):
            result = feed.consume("Event", integer(1, 1), alliance="Red", local_robot_id=3,
                                  received_at=100.5)
            self.assertFalse(feed.is_fresh())
        self.assertEqual(result, ([], {}))
        self.assertEqual(feed.last_message_at, 100.0)

    def test_stale_profile_handoff_still_discards_previous_roster_history(self):
        feed = OfficialMatchFeed()
        with patch("time.perf_counter", return_value=100.0):
            feed.consume("GlobalUnitStatus", packed(11, [100] * 10),
                         alliance="Red", local_robot_id=3, received_at=100.0)
        with patch("time.perf_counter", return_value=104.0):
            result = feed.consume("GlobalUnitStatus", packed(6, [100] * 14),
                alliance="Red", local_robot_id=3, referee_profile=STUDENT_PREVIEW_PROFILE,
                received_at=100.0)
            self.assertEqual(result, ([], {}))
            self.assertFalse(feed.is_fresh())
            health = [100] * 10
            health[5] = 0
            events, updates = feed.consume("GlobalUnitStatus", packed(11, health),
                alliance="Red", local_robot_id=3, referee_profile=DEFAULT_PROFILE,
                received_at=104.0)
        self.assertEqual(events, [])
        self.assertEqual(updates["official_robot_health"], health)

    def test_fresh_explicit_zero_remains_authoritative_through_both_consumers(self):
        host = ReceiveHarness()
        host.state.current_health = 237
        host.receive("RobotDynamicStatus", integer(1, 0), 100.0)
        host.consume(100.0)
        self.assertEqual(host.state.current_health, 0)
        self.assertEqual(host.state.official_feed_at, 100.0)
        self.assertTrue(host.state.official_events_online)

    def test_older_valid_receipt_does_not_regress_latest_clocks_or_retime_event(self):
        feed = OfficialMatchFeed()
        with patch("time.perf_counter", return_value=102.0):
            feed.consume("RobotInjuryStat", integer(9, 1), alliance="Red", local_robot_id=3,
                         received_at=101.5)
            events, updates = feed.consume("Event", integer(1, 1), alliance="Red", local_robot_id=3,
                                          received_at=101.0)
        self.assertEqual(events[0].timestamp, 101.0)
        self.assertEqual((feed.last_message_at, feed.last_kill_data_at,
                          feed.last_official_event_at, updates["official_feed_at"]),
                         (101.5, 101.5, 101.5, 101.5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
