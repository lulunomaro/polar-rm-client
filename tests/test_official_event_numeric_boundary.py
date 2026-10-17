"""Malformed supported JSON extensions cannot discard a later valid raw message.

Real MQTT validation/queues, decoded backend and offscreen window handlers;
backend starts remain inert. Numeric strings retain existing conversion semantics.
"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import test_official_feed_event_identity as fixture
from rm_client.backend.referee.wire import encode_uint, encode_string, encode_bytes


def envelope(topic, value):
    text = json.dumps(value, separators=(",", ":"))
    if topic == "Event":
        return encode_uint(1, 1) + encode_string(2, text)
    return encode_bytes(1, text.encode())


class OfficialEventNumericBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.OfficialFeedEventIdentityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = self.fixture.w
        identity = patch.object(self.w.mqtt, "client", object())
        identity.start()
        self.addCleanup(identity.stop)

    def batch(self, messages):
        self.fixture.reset()
        invalid = self.w.mqtt.invalid_messages
        for topic, payload in messages:
            self.w.mqtt._on_message(self.w.mqtt.client, None,
                                    SimpleNamespace(topic=topic, payload=payload))
        self.assertEqual(self.w.mqtt.invalid_messages, invalid,
                         "Inputs must pass canonical protobuf validation")
        self.w.backend.apply_updates(self.w.state)
        self.assertTrue(self.w._run_tick_stage("official.consume", self.w.consume_official_messages))
        self.w.apply_official_state()
        self.assertEqual(self.w.mqtt.drain_received_messages(), [])
        return self.fixture.events

    def test_bad_numeric_extensions_reject_event_but_preserve_later_valid_message(self):
        good = encode_uint(1, 1) + encode_string(2, "103,3")
        for topic in ("Event", "CustomByteBlock"):
            for field in ("victim_id", "killer_id", "combo", "distance_m", "score_delta"):
                for bad in (None, "not-a-number", [], {}, float("inf"), float("nan"), "1e309"):
                    with self.subTest(topic=topic, field=field, bad=bad):
                        value = {"victim_id": 104, "killer_id": 3, field: bad}
                        if topic == "CustomByteBlock":
                            value["protocol"] = "polar.kill.v1"
                        events = self.batch([(topic, envelope(topic, value)), ("Event", good)])
                        self.assertEqual([(e.kind, e.victim_id) for e in events], [("elimination", 103)])
                        self.assertEqual(self.w.state.referee_kill_count, 1)
                        self.assertEqual(self.w.state.kill_streak, 5)

    def test_float_overflow_preserves_valid_events_on_both_sides(self):
        bad = envelope("Event", {"victim_id": 104, "distance_m": 10 ** 400})
        events = self.batch([
            ("Event", encode_uint(1, 1) + encode_string(2, "101,3")),
            ("Event", bad),
            ("Event", encode_uint(1, 1) + encode_string(2, "103,3")),
        ])
        self.assertEqual([e.victim_id for e in events], [101, 103])
        self.assertEqual(self.w.state.referee_kill_count, 2)

    def test_runtime_integer_token_limit_rejects_event_without_losing_next_message(self):
        limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
        if not limit:
            self.skipTest("Runtime integer-token limit is disabled or unavailable")
        text = '{"victim_id":' + "9" * (limit + 1) + '}'
        events = self.batch([
            ("Event", encode_uint(1, 1) + encode_string(2, text)),
            ("Event", encode_uint(1, 1) + encode_string(2, "103,3")),
        ])
        self.assertEqual([e.victim_id for e in events], [103])
        self.assertEqual(self.w.state.referee_kill_count, 1)

    def test_nested_json_parser_limit_preserves_later_valid_message(self):
        depth = 10000  # 20 KiB: bounded independently of interpreter limits.
        # Valid JSON/protobuf, bounded size; never change interpreter limits.
        text = '{"victim_id":' + "[" * depth + "0" + "]" * depth + '}'
        events = self.batch([
            ("Event", encode_uint(1, 1) + encode_string(2, text)),
            ("Event", encode_uint(1, 1) + encode_string(2, "103,3")),
        ])
        self.assertEqual([e.victim_id for e in events], [103])
        self.assertEqual(self.w.state.referee_kill_count, 1)

    def test_finite_numeric_strings_and_supported_formats_remain_accepted(self):
        numeric = {"victim_id": "103", "killer_id": "3", "combo": "2",
                   "distance_m": "12.5", "score_delta": "100"}
        for topic in ("Event", "CustomByteBlock"):
            with self.subTest(topic=topic):
                value = dict(numeric)
                if topic == "CustomByteBlock":
                    value["protocol"] = "polar.kill.v1"
                events = self.batch([(topic, envelope(topic, value))])
                self.assertEqual(len(events), 1)
                event = events[0]
                self.assertEqual((event.victim_id, event.killer_id, event.combo,
                                  event.distance_m, event.score_delta), (103, 3, 2, 12.5, 100))
        events = self.batch([("CustomByteBlock", encode_bytes(1, b"POLAR_KILL/1|elimination|3|103|hit"))])
        self.assertEqual([(e.victim_id, e.killer_id) for e in events], [(103, 3)])

    def test_missing_fields_keep_existing_defaults_and_objective_ignores_unused_extensions(self):
        events = self.batch([("Event", encode_uint(1, 1))])
        self.assertEqual([(e.victim_id, e.killer_id, e.combo) for e in events], [(0, 0, 0)])
        payload = encode_uint(1, 2) + encode_string(2, '{"victim_id":"103","combo":null}')
        events = self.batch([("Event", payload)])
        self.assertEqual([(e.kind, e.victim_id) for e in events], [("objective", 103)])


if __name__ == "__main__":
    unittest.main()
