"""Frozen native telemetry/video decisions with ordered effects and abort state.

Recording is an explicit, source-digest-guarded operation. Normal tests never
rewrite the oracle. No transport, worker, Qt application, or decoder is started:
the real strict parsers and Annex-B recovery gate run against recording fakes.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import inspect
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.manager import BackendManager
from rm_client.backend.video_backend import AnnexBRecoveryGate
from rm_client import native_viewer_telemetry

FIXTURE = ROOT / "tests/fixtures/custom_byte_block_baseline.json"
SOURCE_DIGESTS = {'BackendManager._apply_custom_byte_block': '07d3a732c797385e1517f34340dd8e550fa0268319b5426fca5fd057def8bdc7', 'native_viewer_telemetry': 'eed1f0f6293d27c40139b82ae9ec1106c3a48c44613870f579a3d05e3eb5724b', 'AnnexBRecoveryGate': '8660c1ef86a6de9e62267cd5b01cc9870ac3cedb0671d8eb1e7dda9928ef64cb'}
TELEMETRY = struct.Struct("<4sHHIQIHHHHIBBH9f")
RECOVERY = b"\x00\x00\x00\x01\x67sps\x00\x00\x01\x68pps\x00\x00\x01\x65idr"
CONTINUATION = b"\x00\x00\x01\x41next"
STATE_DEFAULTS = {
    "native_telemetry_at": 8.0, "native_telemetry_unix_ms": 9,
    "native_telemetry_frame_seq": 10, "native_camera_online": False,
    "native_gimbal_online": False, "native_gimbal_mode": "OLD",
    "native_bullet_count": 11, "native_bullet_speed": 12.0,
    "native_yaw_deg": 13.0, "native_yaw_velocity": 14.0,
    "native_pitch_deg": 15.0, "native_pitch_velocity": 16.0,
    "native_quaternion": (0.0, 0.0, 0.0, 1.0), "native_status_text": "old status",
    "hero_lob_relocalization_valid": True, "hero_lob_armed": True,
    "hero_lob_status": "ARMED", "deploy_mode": 0,
}
MANAGER_DEFAULTS = {
    "_active_video_source": "native", "_native_0310_last_sequence": None,
    "_native_0310_bytes_since_reset": 17, "_native_0310_blocked_until_sps": True,
    "_native_0310_dropped_chunks": 2, "_native_0310_out_of_order_chunks": 3,
    "_native_0310_recovery_dropped_chunks": 4, "_native_0310_decoder_restarts": 5,
    "_native_0310_decoder_started_once": False, "_native_failure_epoch_handled": True,
}


def normalized(value):
    if isinstance(value, bytes | bytearray):
        return {"hex": value.hex()}
    if isinstance(value, dict):
        return {str(key): normalized(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [normalized(item) for item in value]
    return value


def telemetry_packet(sequence=11, status=b"READY", *, flags=3, mode=1):
    return TELEMETRY.pack(b"PDL1", 1, TELEMETRY.size + len(status), flags,
                          123456789, sequence, 1280, 720, 3000, 100, 1200,
                          mode, 0, 42, 1.25, 2.5, -3.25, -4.5, 15.0,
                          0.0, 0.0, 0.0, 1.0) + status


def video_packet(sequence=10, payload=RECOVERY, *, reset=False):
    size = len(payload)
    return bytes((int(reset) | ((size >> 8) << 1), sequence, size & 255)) + payload + bytes(297 - size)


def changed_packet(packet, offset, replacement):
    return packet[:offset] + replacement + packet[offset + len(replacement):]


def cases():
    result = {
        "telemetry_ready": {"packets": [telemetry_packet()]},
        "telemetry_flags_unknown_mode": {"packets": [telemetry_packet(flags=0, mode=255)]},
        "telemetry_status_nul_and_lossy_utf8": {"packets": [telemetry_packet(status=b" \xffready\x00MAP RESET")]},
    }
    for previous, current in ((0, 0), (10, 10), (10, 9), (0xFFFFFFFF, 0),
                               (0x80000000, 0), (0x7FFFFFFF, 0), (1, 0)):
        result[f"telemetry_sequence_{previous}_{current}"] = {
            "state": {"native_telemetry_frame_seq": previous},
            "packets": [telemetry_packet(current)],
        }
    for marker in ("LOCALIZATION LOST", "LOCALISATION LOST", "RELOCALIZING",
                   "RELOCALISING", "POSE RESET", "MAP RESET"):
        result["telemetry_revokes_" + marker.lower().replace(" ", "_")] = {
            "packets": [telemetry_packet(status=("prefix " + marker.lower() + " suffix").encode())],
        }
    result["telemetry_never_rearms"] = {
        "state": {"hero_lob_relocalization_valid": False, "hero_lob_armed": False},
        "packets": [telemetry_packet()],
    }
    result["telemetry_bad_previous_aborts_before_updates"] = {
        "state": {"native_telemetry_frame_seq": "invalid"}, "packets": [telemetry_packet()],
    }
    malformed = {
        "magic": changed_packet(telemetry_packet(), 0, b"BAD!"),
        "version": changed_packet(telemetry_packet(), 4, struct.pack("<H", 2)),
        "declared_74": changed_packet(telemetry_packet(), 6, struct.pack("<H", 74)),
        "declared_75": changed_packet(telemetry_packet(), 6, struct.pack("<H", 75)),
        "declared_too_long": changed_packet(telemetry_packet(), 6, struct.pack("<H", 300)),
        "video_reserved": changed_packet(video_packet(), 0, b"\x04"),
        "video_length_298": changed_packet(video_packet(), 0, b"\x02\x01\x2a"),
    }
    for size in (0, 1, 73, 74, 75, 76, 299, 301):
        malformed[f"length_{size}"] = bytes(size)
    for index in range(9):
        for name, value in (("nan", float("nan")), ("inf", float("inf"))):
            malformed[f"telemetry_float_{index}_{name}"] = changed_packet(
                telemetry_packet(), 40 + 4 * index, struct.pack("<f", value))
    for name, packet in malformed.items():
        result["malformed_" + name] = {"packets": [packet]}
    for name, value in (("none", None), ("text", "bad"), ("bad_list", [256])):
        result["invalid_data_" + name] = {"packets": [value]}
    for source in ("native", "native 0x0310", "0x0310", "auto", "shark udp", "Native", " native "):
        result["video_source_" + source.replace(" ", "_")] = {
            "manager": {"_active_video_source": source}, "packets": [video_packet()],
        }
    for deploy in (0, 1, "1", "bad", None):
        result["video_deploy_" + str(deploy)] = {
            "manager": {"_active_video_source": "udp"}, "state": {"deploy_mode": deploy},
            "packets": [video_packet()],
        }
    result["video_native_short_circuits_bad_deploy"] = {
        "state": {"deploy_mode": "bad"}, "packets": [video_packet()],
    }
    for last, current in ((9, 10), (255, 0), (10, 10), (10, 9), (10, 12),
                          (10, 138), (10, 139), (254, 1)):
        result[f"video_sequence_{last}_{current}"] = {
            "manager": {"_native_0310_last_sequence": last}, "packets": [video_packet(current)],
        }
    result.update({
        "video_reset_clears_epoch": {"manager": {"_native_0310_last_sequence": 100},
                                     "packets": [video_packet(2, reset=True)]},
        "video_empty_payload": {"packets": [video_packet(payload=b"")]},
        "video_max_payload": {"packets": [video_packet(payload=RECOVERY + b"x" * (297 - len(RECOVERY)))]},
        "video_recovery_spans_chunks": {"packets": [video_packet(0, RECOVERY[:7], reset=True),
                                                     video_packet(1, RECOVERY[7:]), video_packet(2, CONTINUATION)]},
        "video_gap_waits_for_fresh_keyframe": {"packets": [video_packet(0, reset=True),
                                                          video_packet(2, CONTINUATION), video_packet(3)]},
        "video_duplicate_leaves_recovery_intact": {"packets": [video_packet(0, RECOVERY[:7]),
                                                              video_packet(0, b"bad"), video_packet(1, RECOVERY[7:])]},
        "video_decoder_already_available": {"decoder": {"available": True}, "packets": [video_packet()]},
        "video_decoder_start_fails": {"decoder": {"start": False}, "packets": [video_packet()]},
        "video_decoder_crash_unhandled": {"manager": {"_native_0310_decoder_started_once": True,
                                                       "_native_failure_epoch_handled": False},
                                           "packets": [video_packet()]},
        "video_decoder_crash_handled": {"manager": {"_native_0310_decoder_started_once": True},
                                         "packets": [video_packet()]},
        "video_restart_pending": {"restart_pending": True, "packets": [video_packet()]},
        "video_write_retry_succeeds": {"decoder": {"writes": [False, False, True]}, "packets": [video_packet()]},
        "video_write_retry_expires": {"decoder": {"writes": [False]}, "packets": [video_packet()]},
        "video_availability_changes_before_write": {
            "decoder": {"availability": [True, False, False]}, "packets": [video_packet()]},
        "video_unselected_clears_partial_recovery": {
            "packets": [video_packet(0, RECOVERY[:7]), video_packet(1, RECOVERY[7:])],
            "before_second": {"_active_video_source": "udp"}},
    })
    for operation in ("recovery.reset", "recovery.feed", "decoder.available", "decoder.start",
                       "decoder.write", "decoder.input_queue_depth"):
        result["failure_" + operation.replace(".", "_")] = {
            "fault": operation, "packets": [video_packet(reset=True)],
        }
    result["failure_revocation_retains_prior_invalidated_flag"] = {
        "fault": "state.hero_lob_armed", "packets": [telemetry_packet(1)],
    }
    return result


class Trace:
    def __init__(self, fault=None):
        self.events = []
        self.fault = fault
        self.counts = Counter()

    def record(self, operation, *values):
        self.events.append([operation, *normalized(values)])
        self.counts[operation] += 1
        if operation == self.fault and self.counts[operation] == 1:
            raise RuntimeError("injected " + operation)


class RecordingStats(dict):
    def __init__(self, trace):
        super().__init__()
        self.trace = trace

    def __setitem__(self, key, value):
        self.trace.record("stats." + key, value)
        super().__setitem__(key, value)


class RecordingState(SimpleNamespace):
    def __setattr__(self, key, value):
        if key in STATE_DEFAULTS and hasattr(self, "trace"):
            self.trace.record("state." + key, value)
        super().__setattr__(key, value)


class RecordingManager(BackendManager):
    def __setattr__(self, key, value):
        if key in MANAGER_DEFAULTS and hasattr(self, "trace"):
            self.trace.record("manager." + key, value)
        super().__setattr__(key, value)


class RecordingRecovery(AnnexBRecoveryGate):
    def __init__(self, trace):
        super().__init__("h264")
        self.trace = trace

    def reset(self):
        self.trace.record("recovery.reset")
        super().reset()

    def feed(self, payload):
        self.trace.record("recovery.feed", payload)
        return super().feed(payload)


class RecordingDecoder:
    def __init__(self, trace, config):
        self.trace = trace
        self.current_available = config.get("available", False)
        self.availability = list(config.get("availability", []))
        self.start_result = config.get("start", True)
        self.writes = list(config.get("writes", [True]))
        self.input_chunks_dropped = 7
        self.frames_decoded = 8

    @property
    def available(self):
        if self.availability:
            self.current_available = self.availability.pop(0)
        self.trace.record("decoder.available", self.current_available)
        return self.current_available

    def start(self):
        self.trace.record("decoder.start", self.start_result)
        self.current_available = self.start_result
        return self.start_result

    def write(self, payload):
        result = self.writes[0]
        if len(self.writes) > 1:
            self.writes.pop(0)
        self.trace.record("decoder.write", payload, result)
        return result

    def input_queue_depth(self):
        self.trace.record("decoder.input_queue_depth")
        return 6


class RecordingClock:
    def __init__(self, trace):
        self.trace = trace
        self.now = 100.0

    def perf_counter(self):
        result = self.now
        self.now += 0.002
        self.trace.record("clock.perf_counter", round(result, 6))
        return round(result, 6)

    def sleep(self, duration):
        self.trace.record("clock.sleep", duration)


def observe(case):
    trace = Trace(case.get("fault"))
    state = RecordingState(**(STATE_DEFAULTS | case.get("state", {})))
    state.backend_stats = RecordingStats(trace)
    state.trace = trace
    manager = object.__new__(RecordingManager)
    manager._video_source_failures = ()
    for name, value in (MANAGER_DEFAULTS | case.get("manager", {})).items():
        setattr(manager, name, value)
    manager._native_recovery = RecordingRecovery(trace)
    manager._native_restart_thread = object() if case.get("restart_pending") else None
    manager.native_decoder = RecordingDecoder(trace, case.get("decoder", {}))
    manager.trace = trace
    errors = []
    with patch("rm_client.backend.manager.time", RecordingClock(trace)):
        for index, packet in enumerate(case["packets"]):
            if index == 1:
                for name, value in case.get("before_second", {}).items():
                    setattr(manager, name, value)
            try:
                manager._apply_custom_byte_block(state, {"data": packet})
            except Exception as exc:
                errors.append([index, type(exc).__name__])
    gate = manager._native_recovery
    return normalized({
        "errors": errors, "events": trace.events,
        "state": {name: getattr(state, name) for name in STATE_DEFAULTS},
        "manager": {name: getattr(manager, name) for name in MANAGER_DEFAULTS},
        "stats": dict(state.backend_stats),
        "recovery": {"blocked": gate.blocked, "parameters": gate.parameters, "tail": gate._tail,
                     "pending_type": gate._pending_parameter_type, "pending": gate._pending_parameter},
    })


def source_digests():
    return {name: hashlib.sha256(inspect.getsource(target).encode()).hexdigest()
            for name, target in (
                ("BackendManager._apply_custom_byte_block", BackendManager._apply_custom_byte_block),
                ("native_viewer_telemetry", native_viewer_telemetry),
                ("AnnexBRecoveryGate", AnnexBRecoveryGate),
            )}


def compact_observation(observed):
    result = dict(observed)
    for name, defaults in (("state", STATE_DEFAULTS), ("manager", MANAGER_DEFAULTS)):
        result[name + "_delta"] = {
            key: value for key, value in result.pop(name).items()
            if value != normalized(defaults[key])
        }
    return result


def expanded_observation(record, baseline):
    result = dict(record)
    for name in ("state", "manager"):
        result[name] = baseline["initial_" + name] | result.pop(name + "_delta")
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def record_baseline():
    if not SOURCE_DIGESTS or source_digests() != SOURCE_DIGESTS:
        raise SystemExit("Recording refused: custom block sources differ from the pinned pre-extraction source")
    case_inputs = cases()
    baseline = {"source_sha256": SOURCE_DIGESTS,
                "scope": "Ordered state/effects and partial abort state; real parsers/recovery, fake I/O/clock",
                "initial_state": normalized(STATE_DEFAULTS),
                "initial_manager": normalized(MANAGER_DEFAULTS),
                "cases": {name: {"input": normalized(case), "observed": compact_observation(observe(case))}
                          for name, case in case_inputs.items()}}
    lines = [json.dumps({key: value for key, value in baseline.items() if key != "cases"},
                       indent=2)[:-2] + ",", '  "cases": {']
    for index, (name, record) in enumerate(baseline["cases"].items()):
        comma = "," if index + 1 < len(case_inputs) else ""
        lines.append("    " + json.dumps(name) + ": {")
        lines.append('      "input": ' + json.dumps(record["input"], ensure_ascii=False) + ",")
        lines.append('      "observed": {')
        observed = record["observed"]
        for key, value in observed.items():
            if key != "events":
                lines.append("        " + json.dumps(key) + ": " + json.dumps(value, ensure_ascii=False) + ",")
        lines.append('        "events": [')
        for offset, event in enumerate(observed["events"]):
            event_comma = "," if offset + 1 < len(observed["events"]) else ""
            lines.append("          " + json.dumps(event, ensure_ascii=False) + event_comma)
        lines.extend(["        ]", "      }", "    }" + comma])
    lines.extend(["  }", "}", ""])
    FIXTURE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Recorded {len(case_inputs)} cases with exact bytes and partial failure effects")


class CustomByteBlockCharacterizationTests(unittest.TestCase):
    def test_frozen_inputs_and_ordered_effects(self):
        baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
        case_inputs = cases()
        self.assertEqual(canonical(normalized(STATE_DEFAULTS)), canonical(baseline["initial_state"]))
        self.assertEqual(canonical(normalized(MANAGER_DEFAULTS)), canonical(baseline["initial_manager"]))
        self.assertEqual(list(case_inputs), list(baseline["cases"]))
        for name, case in case_inputs.items():
            with self.subTest(case=name):
                expected = baseline["cases"][name]
                self.assertEqual(canonical(normalized(case)), canonical(expected["input"]))
                self.assertEqual(canonical(observe(case)),
                                 canonical(expanded_observation(expected["observed"], baseline)))

    def test_malformed_records_do_not_refresh_telemetry_or_touch_decoder(self):
        for name, case in cases().items():
            if not name.startswith("malformed_"):
                continue
            with self.subTest(case=name):
                observed = observe(case)
                self.assertEqual(observed["state"], normalized(STATE_DEFAULTS))
                self.assertEqual(list(observed["stats"]), ["last_custom_block"])
                self.assertFalse(any(event[0].startswith("decoder.") for event in observed["events"]))

    def test_reset_revocation_precedes_telemetry_diagnostics(self):
        observed = observe(cases()["telemetry_revokes_pose_reset"])
        operations = [event[0] for event in observed["events"]]
        self.assertLess(operations.index("state.hero_lob_relocalization_valid"),
                        operations.index("state.hero_lob_armed"))
        self.assertLess(operations.index("state.hero_lob_armed"),
                        operations.index("stats.native_telemetry"))
        self.assertFalse(observed["state"]["hero_lob_armed"])

    def test_faults_preserve_partial_effects_in_the_oracle(self):
        observed = observe(cases()["failure_revocation_retains_prior_invalidated_flag"])
        self.assertEqual(observed["errors"], [[0, "RuntimeError"]])
        self.assertFalse(observed["state"]["hero_lob_relocalization_valid"])
        self.assertTrue(observed["state"]["hero_lob_armed"])
        self.assertEqual(observed["state"]["native_telemetry_frame_seq"], 1)
        self.assertNotIn("native_telemetry", observed["stats"])

    def test_frozen_source_provenance_is_explicit(self):
        baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(baseline["source_sha256"], SOURCE_DIGESTS)
        self.assertEqual(len(SOURCE_DIGESTS), 3)


if __name__ == "__main__":
    if "--record-baseline" in sys.argv:
        record_baseline()
    else:
        unittest.main()
