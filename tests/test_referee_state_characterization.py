"""Frozen RM2026 projection after the independent projectile-baseline fix.

Real codec and state adapter, recording host ports, fixed clock, no I/O. The
fixture captures ordered writes/host effects and partial state on exceptions.
Normal tests only read it; explicit recording requires pinned source digests.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.referee.rm2026 import codec, contract, state_adapter
from rm_client.backend.referee.rm2026.state_adapter import Rm2026StateAdapter
from rm_client.backend.safety import OfficialSafety
from rm_client.model import RobotState

FIXTURE = ROOT / "tests/fixtures/referee_state_baseline.json"
SOURCE_DIGESTS = {'Rm2026StateAdapter': '5250235cae147faca4b6f5464620319cb902c510adc186b63475841b01fed794', 'rm2026.codec': '7edd296d3768c90017ee69b946661f51690d10ecefdb7657181ccbcd542bf5de', 'rm2026.contract': '514c517137c7c17e2658d4768d7244d3c55d9ac73f1f88dd7e06cc8545fbc15c'}
STATE_FIELDS = """
active_penalty air_support_active air_support_cost_coins air_support_free_time_sec
alliance_color backend_frontend_synced backend_stats base_health base_shield base_status
blue_score buff_rows buffs can_remote_ammo can_remote_heal current_buffer_energy
current_chassis_energy current_health current_heat current_round current_stage dart_status
deploy_mode deploy_status_at deploy_status_revision economy_command_status economy_status_revision
encryption_level enemy_base_health enemy_base_shield enemy_base_status enemy_outpost_health
enemy_outpost_status energy_rune_phase energy_rune_progress heat_cooldown_rate injury_stats
injury_stats_initialized last_projectile_damage_delta last_projectile_hit_at last_projectile_killer_id
last_projectile_kind level map_markers map_robots max_buffer_energy max_health max_heat
official_robot_bullets official_robot_health out_of_combat outpost_health outpost_status
path_plan penalties performance_sync power_limit projectile_allowance_17mm projectile_allowance_42mm
projectile_damage_large_initialized projectile_damage_large_total projectile_damage_small_initialized
projectile_damage_small_total projectile_hit_revision radar_detections radar_detections_at red_score
remaining_ammo remaining_economy respawn_can_free respawn_can_pay respawn_gold_cost respawn_pending
respawn_progress robot_dynamics robot_id robot_module_status robot_modules robot_positions robot_rows
robot_statics rune_status sentry_ctrl_result sentry_status special_mechanisms stage_countdown_sec
stage_elapsed_sec tech_core tech_level total_economy_obtained total_projectiles_fired total_rounds
""".split()
STATE_SEED = {
    "current_stage": "MATCH", "current_round": 2, "total_rounds": 3,
    "red_score": 6, "blue_score": 7, "stage_countdown_sec": 123, "stage_elapsed_sec": 45,
    "current_health": 500, "max_health": 600, "current_heat": 12.5,
    "current_buffer_energy": 50, "current_chassis_energy": 70, "remaining_ammo": 88,
    "remaining_economy": 321, "total_economy_obtained": 987, "tech_level": 3,
    "encryption_level": 4, "robot_id": 3, "base_health": 4500, "base_status": "old",
    "enemy_base_health": 3500, "outpost_health": 1000, "enemy_outpost_health": 900,
    "can_remote_heal": True, "can_remote_ammo": True, "out_of_combat": True,
    "official_robot_health": list(range(101, 111)), "official_robot_bullets": [10, 20, 30, 40, 50],
    "economy_status_revision": 7, "economy_command_status": "WAITING",
    "projectile_hit_revision": 9, "projectile_damage_small_total": 50,
    "projectile_damage_large_total": 30, "respawn_can_free": True, "respawn_can_pay": True,
    "respawn_progress": .25, "respawn_gold_cost": 12, "air_support_free_time_sec": 15,
    "air_support_cost_coins": 20, "energy_rune_phase": "ACTIVE", "energy_rune_progress": 80,
    "deploy_status_revision": 3, "deploy_status_at": 11.0, "radar_detections_at": 12.0,
    "backend_stats": {"unrelated": "keep", "respawn_status": {"total_respawn_progress": 100}},
    "robot_statics": {3: {"level": 2, "max_health": 600}},
    "robot_dynamics": {3: {"current_health": 500, "current_heat": 12.5}},
    "robot_modules": {3: {"armor": 1}}, "robot_module_status": {"armor": 1},
    "robot_positions": [{"robot_id": 3, "x": 2., "y": 3., "yaw": 4.}],
    "buffs": [{"robot_id": 3, "buff_type": 2, "buff_left_time": 9}],
    "penalties": [{"penalty_type": 1, "total_penalty_num": 2}],
    "map_markers": [{"label": "OLD", "x": 1., "y": 2.}],
    "radar_detections": [{"target_pos_x": 1, "target_pos_y": 2}],
    "rune_status": {"rune_status": 3, "average_rings": .8},
    "performance_sync": {"shooter": 1, "chassis": 2},
}
HOST_DEFAULTS = {"_last_logged_stage": "NOT_STARTED", "_hero_native_override": False,
                 "_normal_video_source": "udp hevc", "_active_video_source": "udp hevc"}


def normalized(value):
    if isinstance(value, bytes | bytearray):
        return {"hex": value.hex()}
    if isinstance(value, float) and not math.isfinite(value):
        return {"float": repr(value)}
    if isinstance(value, dict):
        return {str(key): normalized(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(normalized(item) for item in value)
    if isinstance(value, list | tuple):
        return [normalized(item) for item in value]
    return value


def canonical(value):
    return json.dumps(normalized(value), sort_keys=True, allow_nan=False)


def varint(value):
    value = int(value) & ((1 << 64) - 1)
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def wire_field(number, kind, value):
    if kind in {"uint32", "uint64", "int32", "bool"}:
        return varint(number << 3) + varint(value)
    if kind == "float":
        return varint((number << 3) | 5) + struct.pack("<f", value)
    payload = value.encode() if kind == "string" else bytes(value)
    return varint((number << 3) | 2) + varint(len(payload)) + payload


def wire_message(name, values):
    result = bytearray()
    for number, (key, kind, repeated) in contract.FIELDS[name].items():
        if key not in values:
            continue
        value = values[key]
        if kind in contract.FIELDS:
            for child in value:
                result.extend(wire_field(number, "bytes", wire_message(kind, child)))
        elif repeated:
            result.extend(wire_field(number, "bytes", b"".join(varint(item) for item in value)))
        else:
            result.extend(wire_field(number, kind, value))
    return bytes(result)


def samples():
    result = {}
    for name in sorted(codec.DOWNLINK_MESSAGES):
        values = {}
        for number, (key, kind, repeated) in contract.FIELDS[name].items():
            if kind in contract.FIELDS:
                value = [{"target_pos_x": 17, "target_pos_y": 18, "is_high_light": 1}]
            else:
                value = (number + .25 if kind == "float" else "event value" if kind == "string"
                         else b"abc" if kind == "bytes" else True if kind == "bool"
                         else -number if kind == "int32" else 2 ** 40 if kind == "uint64" else number + 10)
                if repeated:
                    value = [value, value + 1]
            values[key] = value
        result[name] = values
    result["GameStatus"]["current_stage"] = 4
    result["RobotStaticStatus"]["robot_id"] = 103
    result["DeployModeStatusSync"]["status"] = 1
    return result


def raw(name, data):
    return {"name": name, "data": data}


def wire(name, values):
    return {"name": name, "wire": wire_message(name, values)}


def cases():
    result = {}
    for name, data in samples().items():
        result[name + "_full_wire"] = {"messages": [wire(name, data)]}
        result[name + "_raw_empty"] = {"messages": [raw(name, {})]}
        result[name + "_absent_fields_ignore_decoder_defaults"] = {
            "messages": [raw(name, data | {"__present__": ()})]}
        zeros = {key: ([] if isinstance(value, list) else b"" if isinstance(value, bytes)
                       else "" if isinstance(value, str) else False if isinstance(value, bool) else 0)
                 for key, value in data.items()}
        result[name + "_explicit_zeros_wire"] = {"messages": [wire(name, zeros)]}
    for name in ("GameStatus", "RobotStaticStatus", "RobotDynamicStatus", "RobotInjuryStat",
                 "RobotRespawnStatus", "RuneStatusSync"):
        for key, value in samples()[name].items():
            result[name + "_only_" + key] = {"messages": [wire(name, {key: value})]}
    result.update({
        "unknown_name_no_effect": {"messages": [raw("FutureMessage", {"current_stage": 4})]},
        "unknown_name_present_no_effect": {"messages": [raw("FutureMessage", {"__present__": (1,), "status": 1})]},
        "unknown_name_still_reads_existing_economy_signature": {
            "state": {"remaining_economy": "bad"}, "messages": [raw("FutureMessage", {})]},
        "malformed_presence_none_aborts_before_any_state_write": {
            "messages": [raw("GameStatus", {"__present__": None, "current_stage": 4})]},
        "malformed_presence_unhashable_aborts_before_any_state_write": {
            "messages": [raw("GameStatus", {"__present__": [[]], "current_stage": 4})]},
        "stage_same_value_logs_once": {"messages": [wire("GameStatus", {"current_stage": 4})] * 2},
        "stage_unknown_direct_value_keeps_legacy_semantics": {"messages": [raw("GameStatus", {"current_stage": 99})]},
        "stage_bad_countdown_keeps_preceding_stage_effects": {
            "messages": [raw("GameStatus", {"current_stage": 1, "stage_countdown_sec": "bad"})]},
        "stage_safety_callback_failure_keeps_stage_assignment": {
            "messages": [wire("GameStatus", {"current_stage": 1})], "fail": "safety.stage"},
        "stage_log_failure_keeps_logged_stage_before_countdown": {
            "messages": [wire("GameStatus", {"current_stage": 1, "stage_countdown_sec": 99})], "fail": "log.match_stage"},
        "global_empty_health_preserves_old_health_but_clears_bullets": {
            "messages": [raw("GlobalUnitStatus", {"robot_health": [], "robot_bullets": []})]},
        "global_mixed_health_filters_non_int_values": {
            "messages": [raw("GlobalUnitStatus", {"robot_health": [10, "11", False, 12.5, 0],
                                                   "robot_bullets": [10, -2, 30]})]},
        "hero_bullet_projection": {"state": {"robot_id": 101},
                                    "messages": [wire("GlobalUnitStatus", {"robot_bullets": [12, 20, 30, 40, 50]})]},
        "unknown_role_bullet_projection": {"state": {"robot_id": 109},
                                    "messages": [wire("GlobalUnitStatus", {"robot_bullets": [12, 20, 30, 40, 50]})]},
        "static_claimed_presence_without_value_aborts": {
            "messages": [raw("RobotStaticStatus", {"__present__": (4,)})]},
        "static_no_presence_metadata_keeps_legacy_scalar_behavior": {
            "messages": [raw("RobotStaticStatus", {"robot_id": 103, "level": 9, "max_health": 800})]},
        "static_zero_limits_do_not_overwrite_existing_limits": {
            "messages": [wire("RobotStaticStatus", {"max_health": 0, "max_heat": 0, "heat_cooldown_rate": 0.})]},
        "dynamic_bad_heat_keeps_prior_dictionary_and_health_writes": {
            "messages": [raw("RobotDynamicStatus", {"current_health": 12, "current_heat": "bad"})]},
        "dynamic_zero_health_changes_economy_revision_once": {
            "messages": [wire("RobotDynamicStatus", {"current_health": 0})] * 2},
        "module_bad_conversion_never_updates_either_dictionary": {
            "messages": [raw("RobotModuleStatus", {"armor": "bad", "power_manager": 1})]},
        "position_partial_merges_existing_coordinates": {
            "messages": [wire("RobotPosition", {"yaw": 12.5})]},
        "position_capacity_keeps_last_sixteen": {
            "state": {"robot_positions": [{"robot_id": index, "x": index} for index in range(1, 18)]},
            "messages": [wire("RobotPosition", {"robot_id": 101, "x": 2.})]},
        "buff_replacement_discards_omitted_old_fields": {
            "messages": [wire("Buff", {"robot_id": 3, "buff_type": 2, "buff_level": -2})]},
        "buff_capacity_keeps_last_twenty_four": {
            "state": {"buffs": [{"robot_id": index, "buff_type": 1} for index in range(25)]},
            "messages": [wire("Buff", {"robot_id": 103, "buff_type": 2})]},
        "penalty_replacement_and_active_snapshot": {
            "messages": [wire("PenaltyInfo", {"penalty_type": 1, "penalty_effect_sec": 8})]},
        "penalty_capacity_keeps_last_eight": {
            "state": {"penalties": [{"penalty_type": index} for index in range(9)]},
            "messages": [wire("PenaltyInfo", {"penalty_type": 42})]},
        "map_printable_ascii_and_bounded_history": {
            "state": {"map_markers": [{"label": str(index)} for index in range(17)]},
            "messages": [wire("MapClickInfo", {"ascii": 65, "mode": 2, "map_x": 1.5, "map_y": 2.5})]},
        "map_nonprintable_ascii": {"messages": [wire("MapClickInfo", {"ascii": 127, "mode": 9})]},
        "radar_partial_child_and_timestamp": {
            "messages": [wire("RadarInfoToClient", {"robots": [{"target_pos_x": 5}]})]},
        "radar_clock_failure_keeps_replaced_detections": {
            "messages": [wire("RadarInfoToClient", {"robots": [{"target_pos_x": 5}]})], "fail": "clock"},
        "deploy_repeated_status_refreshes_timestamp_not_revision": {
            "messages": [wire("DeployModeStatusSync", {"status": 1})] * 2},
        "deploy_return_restores_normal_source": {
            "messages": [wire("DeployModeStatusSync", {"status": 1}), wire("DeployModeStatusSync", {"status": 0})]},
        "deploy_return_uses_config_fallback": {"host": {"_hero_native_override": True, "_normal_video_source": ""},
            "state": {"deploy_mode": 1}, "messages": [wire("DeployModeStatusSync", {"status": 0})]},
        "deploy_video_failure_keeps_override_and_state": {
            "messages": [wire("DeployModeStatusSync", {"status": 1})], "fail": "video.source"},
        "deploy_log_failure_keeps_video_transition": {
            "messages": [wire("DeployModeStatusSync", {"status": 1})], "fail": "log.hero_video_source"},
        "rune_bad_progress_keeps_raw_and_phase_writes": {
            "messages": [raw("RuneStatusSync", {"rune_status": 2, "average_rings": "bad"})]},
        "rune_direct_nonfinite_progress_aborts_after_raw_write": {
            "messages": [raw("RuneStatusSync", {"average_rings": float("nan")})]},
        "mechanism_arrays_zip_to_shorter_length": {
            "messages": [wire("GlobalSpecialMechanism", {"mechanism_id": [1, 2, 3], "mechanism_time_sec": [-1, 10]})]},
        "custom_delivery_failure_is_observable": {
            "messages": [wire("CustomByteBlock", {"data": b"abc"})], "fail": "custom.bytes"},
        "injury_partial_first_counters_never_become_hits": {
            "messages": [wire("RobotInjuryStat", {"small_projectile_damage": 20}),
                         wire("RobotInjuryStat", {"large_projectile_damage": 10})]},
        "injury_known_delta_ignores_first_other_counter": {
            "messages": [wire("RobotInjuryStat", {"small_projectile_damage": 20}),
                         wire("RobotInjuryStat", {"small_projectile_damage": 25, "large_projectile_damage": 100})]},
        "injury_both_deltas_duplicate_and_reset": {
            "messages": [wire("RobotInjuryStat", values) for values in (
                {"small_projectile_damage": 20, "large_projectile_damage": 10},
                {"small_projectile_damage": 25, "large_projectile_damage": 17},
                {"small_projectile_damage": 25, "large_projectile_damage": 17},
                {"small_projectile_damage": 0}, {"large_projectile_damage": 100},
                {"large_projectile_damage": 104})]},
        "injury_clock_failure_keeps_revision_before_delta_publication": {
            "messages": [wire("RobotInjuryStat", {"small_projectile_damage": 20}),
                         wire("RobotInjuryStat", {"small_projectile_damage": 25})], "fail": "clock"},
        "respawn_partial_progress_reuses_previous_total": {
            "messages": [wire("RobotRespawnStatus", {"current_respawn_progress": 25})]},
        "respawn_total_zero_uses_one_and_caps_progress": {
            "messages": [wire("RobotRespawnStatus", {"total_respawn_progress": 0, "current_respawn_progress": 2})]},
        "malformed_message_does_not_hide_following_valid_update": {
            "messages": [raw("RobotDynamicStatus", {"current_health": "bad"}),
                         wire("GlobalLogisticsStatus", {"remaining_economy": 123})]},
    })
    malformed_wire = {
        "truncated_stage": ("GameStatus", b"\x28\x04\x80"),
        "unknown_stage": ("GameStatus", b"\x28\x63"),
        "unknown_deploy": ("DeployModeStatusSync", b"\x08\x02"),
        "wrong_wire_type": ("GameStatus", wire_field(5, "float", 4.)),
        "unknown_field_only": ("GameStatus", wire_field(99, "uint32", 4)),
        "nan_heat": ("RobotDynamicStatus", wire_field(2, "float", float("nan"))),
        "inf_rune": ("RuneStatusSync", wire_field(3, "float", float("inf"))),
        "bad_utf8": ("Event", wire_field(2, "bytes", b"\xff")),
        "bad_packed_health": ("GlobalUnitStatus", wire_field(11, "bytes", b"\x80")),
        "oversize_custom": ("CustomByteBlock", wire_field(1, "bytes", bytes(301))),
    }
    for key, (name, payload) in malformed_wire.items():
        result["rejected_wire_" + key] = {"messages": [{"name": name, "wire": payload}]}
    return result


class Trace:
    def __init__(self, fail=None):
        self.events = []
        self.fail = fail
        self.clock_calls = 0

    def record(self, name, *values):
        self.events.append(normalized([name, *values]))
        if name == self.fail:
            raise RuntimeError("injected " + name)

    def now(self):
        self.clock_calls += 1
        value = 100.0 + self.clock_calls
        self.record("clock", value)
        return value


class RecordingDict(dict):
    def __init__(self, value, trace, path):
        self.trace, self.path = trace, path
        super().__init__((key, wrapped(item, trace, path + "." + str(key))) for key, item in value.items())

    def __setitem__(self, key, value):
        self.trace.record(self.path + ".set", key, value)
        super().__setitem__(key, wrapped(value, self.trace, self.path + "." + str(key)))

    def setdefault(self, key, default=None):
        if key not in self:
            self.trace.record(self.path + ".setdefault", key, default)
            dict.__setitem__(self, key, wrapped(default, self.trace, self.path + "." + str(key)))
        return self[key]

    def update(self, values):
        self.trace.record(self.path + ".update", values)
        for key, value in values.items():
            dict.__setitem__(self, key, wrapped(value, self.trace, self.path + "." + str(key)))


def wrapped(value, trace, path):
    if isinstance(value, dict):
        return RecordingDict(value, trace, path)
    if isinstance(value, list):
        return [wrapped(item, trace, path + "[]") for item in value]
    return value


class RecordingState(RobotState):
    def __setattr__(self, key, value):
        trace = getattr(self, "_trace", None)
        if trace is not None and not key.startswith("_"):
            trace.record("state." + key, value)
            value = wrapped(value, trace, "state." + key)
        super().__setattr__(key, value)


class Host:
    def __init__(self, trace, changes):
        self.__dict__.update(deepcopy(HOST_DEFAULTS | changes))
        self.trace = trace
        self.config = SimpleNamespace(connection=SimpleNamespace(video_source="Webcam"))
        self.safety = OfficialSafety()
        original_update = self.safety.update_stage

        def update_stage(stage):
            trace.record("safety.stage", stage)
            original_update(stage)

        self.safety.update_stage = update_stage
        self.field_log = SimpleNamespace(emit=lambda name, **fields: trace.record("log." + name, fields))

    def __setattr__(self, key, value):
        if key in HOST_DEFAULTS and hasattr(self, "trace"):
            self.trace.record("host." + key, value)
        super().__setattr__(key, value)

    def set_active_video_source(self, source, **kwargs):
        self.trace.record("video.source", source, kwargs)
        self._active_video_source = source.lower()

    def _apply_custom_byte_block(self, state, data):
        self.trace.record("custom.bytes", data)


def initial_state():
    state = RobotState(start_time=1.0)
    for name, value in STATE_SEED.items():
        setattr(state, name, deepcopy(value))
    return normalized({name: getattr(state, name) for name in STATE_FIELDS})


def observe(case):
    trace = Trace(case.get("fail"))
    state = RecordingState(start_time=1.0)
    for name, value in (STATE_SEED | case.get("state", {})).items():
        setattr(state, name, deepcopy(value))
    for name in STATE_FIELDS:
        object.__setattr__(state, name, wrapped(getattr(state, name), trace, "state." + name))
    state._trace = trace
    host = Host(trace, case.get("host", {}))
    adapter = Rm2026StateAdapter(host)
    errors = []
    with patch.object(state_adapter, "time", SimpleNamespace(perf_counter=trace.now)):
        for index, message in enumerate(case["messages"]):
            name = message["name"]
            data = (codec.decode_message(name, message["wire"]) if "wire" in message
                    else deepcopy(message["data"]))
            if "wire" in message:
                trace.record("decode", name, data)
                if not data:
                    continue
            try:
                adapter._apply_one(state, name, data)
            except Exception as exc:
                errors.append([index, type(exc).__name__])
    return normalized({
        "state": {name: getattr(state, name) for name in STATE_FIELDS},
        "host": {name: getattr(host, name) for name in HOST_DEFAULTS},
        "safety": {"current_stage": host.safety.current_stage, "stage_seen": host.safety._stage_seen},
        "events": trace.events, "errors": errors,
    })


def source_digests():
    return {name: hashlib.sha256(inspect.getsource(target).encode()).hexdigest()
            for name, target in (("Rm2026StateAdapter", Rm2026StateAdapter),
                                 ("rm2026.codec", codec), ("rm2026.contract", contract))}


def compact(observed):
    result = dict(observed)
    for key, base in (("state", initial_state()), ("host", HOST_DEFAULTS)):
        result[key + "_delta"] = {name: value for name, value in result.pop(key).items()
                                   if canonical(value) != canonical(base[name])}
    return result


def expanded(observed, baseline):
    result = dict(observed)
    for key in ("state", "host"):
        result[key] = baseline["initial_" + key] | result.pop(key + "_delta")
    return result


def record_baseline():
    if not SOURCE_DIGESTS or source_digests() != SOURCE_DIGESTS:
        raise SystemExit("Recording refused: referee sources differ from the pinned corrected baseline")
    inputs = cases()
    metadata = {"source_sha256": SOURCE_DIGESTS,
                "scope": "Post independent projectile baseline fix; codec, ordered projection/effects, partial abort state; fake host ports",
                "initial_state": initial_state(), "initial_host": HOST_DEFAULTS}
    lines = [json.dumps(metadata, indent=2)[:-2] + ",", '  "cases": {']
    for index, (name, case) in enumerate(inputs.items()):
        observed = compact(observe(case))
        lines.extend(["    " + json.dumps(name) + ": {",
                      '      "input": ' + json.dumps(normalized(case), ensure_ascii=False) + ",",
                      '      "observed": {'])
        for key, value in observed.items():
            if key != "events":
                lines.append("        " + json.dumps(key) + ": " + json.dumps(value, ensure_ascii=False) + ",")
        lines.append('        "events": [')
        for offset, event in enumerate(observed["events"]):
            lines.append("          " + json.dumps(event, ensure_ascii=False)
                         + ("," if offset + 1 < len(observed["events"]) else ""))
        lines.extend(["        ]", "      }", "    }" + ("," if index + 1 < len(inputs) else "")])
    lines.extend(["  }", "}", ""])
    FIXTURE.write_text("\n".join(lines), encoding="utf-8")
    print("Recorded", len(inputs), "referee cases")


class RefereeStateCharacterizationTests(unittest.TestCase):
    def test_frozen_ordered_projection_and_partial_failure_state(self):
        baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
        inputs = cases()
        self.assertEqual(canonical(initial_state()), canonical(baseline["initial_state"]))
        self.assertEqual(list(inputs), list(baseline["cases"]))
        for name, case in inputs.items():
            with self.subTest(case=name):
                expected = baseline["cases"][name]
                self.assertEqual(canonical(case), canonical(expected["input"]))
                contract_v2 = expanded(expected["observed"], baseline)
                # Source 2f956792 intentionally adds exactly this ordered write.
                # Keep the original fixture; no other trace/state/error delta
                # is permitted by the canonical scalar-writer contract v2.
                air_support_writes = {
                    "AirSupportStatusSync_full_wire": (12, True),
                    "AirSupportStatusSync_explicit_zeros_wire": (0, False),
                }
                if name in air_support_writes:
                    free_time, eligible = air_support_writes[name]
                    events = contract_v2["events"]
                    anchor = ["state.air_support_free_time_sec", free_time]
                    self.assertEqual(events.count(anchor), 1)
                    self.assertFalse(any(event[0] == "state.air_support_can_free" for event in events))
                    events.insert(events.index(anchor) + 1, ["state.air_support_can_free", eligible])
                self.assertEqual(canonical(observe(case)), canonical(contract_v2))

    def test_every_current_downlink_has_wire_and_presence_cases(self):
        inputs = cases()
        for name in codec.DOWNLINK_MESSAGES:
            self.assertIn(name + "_full_wire", inputs)
            self.assertIn(name + "_absent_fields_ignore_decoder_defaults", inputs)
            observed = observe(inputs[name + "_full_wire"])
            self.assertFalse(observed["errors"], name)
            self.assertTrue(observed["events"][0][2], name)

    def test_rejected_wire_never_reaches_projection(self):
        for name, case in cases().items():
            if name.startswith("rejected_wire_"):
                with self.subTest(case=name):
                    observed = observe(case)
                    self.assertEqual(observed["state"], initial_state())
                    self.assertEqual(len(observed["events"]), 1)
                    self.assertEqual(observed["events"][0][2], {})

    def test_corrected_independent_counter_baselines_are_frozen(self):
        observed = observe(cases()["injury_partial_first_counters_never_become_hits"])
        self.assertEqual(observed["state"]["projectile_hit_revision"], STATE_SEED["projectile_hit_revision"])
        observed = observe(cases()["injury_known_delta_ignores_first_other_counter"])
        self.assertEqual(observed["state"]["last_projectile_damage_delta"], 5)

    def test_every_injected_host_or_clock_failure_is_reached(self):
        for name, case in cases().items():
            if "fail" in case:
                with self.subTest(case=name):
                    observed = observe(case)
                    self.assertTrue(observed["errors"])
                    self.assertIn(case["fail"], [event[0] for event in observed["events"]])

    def test_aborts_capture_prior_effects_instead_of_only_exception_type(self):
        observed = observe(cases()["stage_bad_countdown_keeps_preceding_stage_effects"])
        self.assertEqual(observed["errors"], [[0, "ValueError"]])
        self.assertEqual(observed["state"]["current_stage"], "PREPARE")
        self.assertEqual(observed["safety"]["current_stage"], "1")
        self.assertIn("log.match_stage", [event[0] for event in observed["events"]])
        observed = observe(cases()["injury_clock_failure_keeps_revision_before_delta_publication"])
        self.assertEqual(observed["errors"], [[1, "RuntimeError"]])
        self.assertEqual(observed["state"]["projectile_hit_revision"], STATE_SEED["projectile_hit_revision"] + 1)
        self.assertEqual(observed["state"]["last_projectile_damage_delta"], 0)

    def test_fixture_records_pinned_corrected_source(self):
        baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(baseline["source_sha256"], SOURCE_DIGESTS)
        self.assertEqual(len(SOURCE_DIGESTS), 3)


if __name__ == "__main__":
    if "--record-baseline" in sys.argv:
        record_baseline()
    else:
        unittest.main()
