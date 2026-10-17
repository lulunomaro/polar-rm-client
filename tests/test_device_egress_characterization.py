"""Frozen configuration, permission and retained-writer effects before extraction.

Recorded after the explicit CC conflict-neutral correction. All writers and
qualification gates are recording fakes; no device, worker, or app is created.
Normal tests never regenerate the source-digest-pinned fixture.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import itertools
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.manager import BackendManager
from rm_client.control import KeyboardControlBridge

FIXTURE = ROOT / "tests/fixtures/device_egress_baseline.json"
SOURCE_DIGESTS = {'BackendManager._configure_device_egress': '57d93054fea71b45c5bb3cef3b6d316584196c2f378ab7cc78a47693c811a666', 'BackendManager._configure_cc_egress': '716983d5739867f0ea7f163f7797f83e283689bd3a9b969315618d6061896d71', 'BackendManager._cc_link_for': 'ef1dff93344ae7d71fd55958a7191917b4ed1e815f7cd27003b0f3318063c9dc', 'KeyboardControlBridge.release_custom_controller': 'c343c8372dfbb4fee268556b7ff51f2d61bf6b8c57c9766b753194cd0332632d', 'KeyboardControlBridge.release_direct_dt7': 'b8daa67683e1ee26f9056c91c0d686cf3bac2f43bd14803c91f818ceac7d5f0a'}
PROFILE = {"name": "fixture", "_profile_error": ""}
SETTINGS = {
    "Direct DT7 Egress": True, "DT7 Port": "DT7_NEW", "DT7 Real TX": False,
    "DT7 Allow Fire Keys": False, "Custom Controller Egress": True,
    "CC Port": "CC_NEW", "CC Hz": 30, "CC Mock Writes": True,
    "Allow Spin Key": False, "Allow Drive": True,
}
CONTROL_DEFAULTS = {
    "dt7_selected": False, "dt7_enabled": False, "dt7_release_pending": False,
    "dt7_allow_fire": False, "dt7_key_mask": 15, "dt7_last_error": "old DT7 error",
    "cc_enabled": False, "cc_release_pending": False, "cc_key_mask": 15,
    "cc_last_error": "old CC error", "dt7_qualification_token": "fixture-token",
}
MANAGER_DEFAULTS = {
    "_dt7_selected": False, "_dt7_was_enabled": False, "_cc_was_enabled": False,
    "_dt7_change_key": None, "_cc_change_key": None, "_cc_reconfigure_blocked": False,
}


def normalized(value):
    if isinstance(value, Link):
        return {"link": value.name}
    if isinstance(value, Safety):
        return "safety"
    if callable(value):
        return {"callable": value.__name__}
    if isinstance(value, dict):
        return {str(key): normalized(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [normalized(item) for item in value]
    return value


def canonical(value):
    return json.dumps(normalized(value), sort_keys=True, allow_nan=False)


def cases():
    result = {}
    for enabled, allow, estop, real, qualified in itertools.product((False, True), repeat=5):
        key = "gates_" + "".join(str(int(value)) for value in (enabled, allow, estop, real, qualified))
        result[key] = {"enabled": enabled, "estop": estop, "qualified": qualified,
                       "settings": {"Allow Drive": allow, "DT7 Real TX": real}}
    for name in SETTINGS:
        result["missing_" + name.lower().replace(" ", "_")] = {"missing": [name]}
    for mode in ("no_registry", "no_module", "no_control"):
        result[mode] = {mode: True, "manager": {"_dt7_was_enabled": True, "_cc_was_enabled": True}}
    for name, settings in {
        "both_routes_off": {"Direct DT7 Egress": False, "Custom Controller Egress": False},
        "spin_requested": {"Allow Spin Key": True},
        "fire_requested": {"DT7 Allow Fire Keys": True},
        "spin_and_fire": {"Allow Spin Key": True, "DT7 Allow Fire Keys": True},
        "truthy_persisted_strings": {"Allow Spin Key": "false", "DT7 Allow Fire Keys": "false"},
        "cc_frequency_bad": {"CC Hz": "bad"},
        "cc_frequency_none": {"CC Hz": None},
        "cc_frequency_zero": {"CC Hz": 0},
        "cc_frequency_string_zero": {"CC Hz": "0"},
        "cc_frequency_fraction": {"CC Hz": "12.5"},
        "empty_ports": {"CC Port": "", "DT7 Port": ""},
        "none_ports": {"CC Port": None, "DT7 Port": None},
        "spaced_ports": {"CC Port": " CC_NEW ", "DT7 Port": " DT7_NEW "},
        "real_cc": {"CC Mock Writes": False},
    }.items():
        result[name] = {"settings": settings}
    for kind in ("cc", "dt7"):
        for name, writer in {
            "same_endpoint": {"same": True},
            "different_neutral": {},
            "different_moving_release_confirmed": {"wire_neutral": False},
            "different_moving_release_pending": {"wire_neutral": False, "release": False},
            "different_moving_release_exception": {"wire_neutral": False, "release_raise": True},
            "close_pending": {"close_confirmed": False},
            "close_exception": {"close_raise": True, "close_confirmed": False},
            "already_closed": {"same": True, "closed": True},
        }.items():
            result[kind + "_" + name] = {"old_" + kind: writer}
        for edge in ("egress", "module", "allow_drive", "estop", "qualification"):
            settings = {"DT7 Real TX": kind == "dt7"}
            settings[{"cc": "Custom Controller Egress", "dt7": "Direct DT7 Egress"}[kind]] = edge != "egress"
            settings["Allow Drive"] = edge != "allow_drive"
            result[kind + "_falling_" + edge] = {
                "old_" + kind: {"same": True, "wire_neutral": False},
                "manager": {"_" + kind + "_was_enabled": True},
                "control": {kind + "_enabled": True}, "settings": settings,
                "enabled": edge != "module", "estop": edge == "estop",
                "qualified": edge != "qualification",
            }
    for port in ("CONFLICT", " conflict ", "Auto", "OTHER", None):
        result["cc_owned_port_" + str(port)] = {
            "owned_ports": ["CONFLICT"], "settings": {"CC Port": port, "CC Mock Writes": False},
            "old_cc": {"wire_neutral": False},
            "manager": {"_cc_was_enabled": True}, "control": {"cc_enabled": True},
        }
    result.update({
        "cc_conflict_failed_neutral": {"owned_ports": ["CC_NEW"], "settings": {"CC Mock Writes": False},
                                       "old_cc": {"wire_neutral": False, "release": False},
                                       "manager": {"_cc_was_enabled": True}, "control": {"cc_enabled": True}},
        "cc_conflict_pending_retry": {"owned_ports": ["CC_NEW"], "settings": {"CC Mock Writes": False},
                                      "old_cc": {"wire_neutral": False, "release": False},
                                      "control": {"cc_release_pending": True}, "repeat": 2},
        "both_old_targets_blocked": {"old_cc": {"wire_neutral": False, "release": False},
                                     "old_dt7": {"wire_neutral": False, "release": False}},
        "cc_failure_still_processes_dt7_falling_edge": {
            "old_cc": {"wire_neutral": False, "release": False}, "old_dt7": {"same": True, "wire_neutral": False},
            "settings": {"Direct DT7 Egress": False}, "manager": {"_dt7_was_enabled": True}},
        "dt7_failure_preserves_cc_falling_edge": {
            "old_dt7": {"wire_neutral": False, "release": False}, "old_cc": {"same": True, "wire_neutral": False},
            "settings": {"Custom Controller Egress": False}, "manager": {"_cc_was_enabled": True}},
        "repeated_cc_reconfigure_locks_once": {"old_cc": {"wire_neutral": False, "release": False}, "repeat": 2},
        "repeated_dt7_reconfigure_locks_once": {"old_dt7": {"wire_neutral": False, "release": False}, "repeat": 2},
        "profile_error": {"profile": {"name": "fixture", "_profile_error": "invalid profile"}},
    })
    for operation in ("profile.load", "factory.cc", "factory.dt7", "safety.can_transmit_dt7"):
        result["failure_" + operation.replace(".", "_")] = {
            "fault": operation, "settings": {"DT7 Real TX": True},
        }
    return result


class Trace:
    def __init__(self, fault=None):
        self.events = []
        self.fault = fault

    def record(self, operation, *values):
        self.events.append([operation, *normalized(values)])
        if operation == self.fault:
            raise RuntimeError("injected " + operation)


class Module:
    def __init__(self, enabled, values):
        self.enabled = enabled
        self.values = {name: SimpleNamespace(value=value) for name, value in values.items()}

    def get_setting(self, name):
        return self.values.get(name)


class Safety:
    def __init__(self, trace, case):
        self.trace = trace
        self.estop_active = case.get("estop", False)
        self.qualified = case.get("qualified", True)
        self.locked = False

    def lock_control(self, reason):
        self.trace.record("safety.lock_control", reason)
        self.locked = True

    def can_transmit_dt7(self, **kwargs):
        self.trace.record("safety.can_transmit_dt7", kwargs)
        allowed = self.qualified and not self.locked
        return allowed, "fixture allowed" if allowed else "fixture denied"


class Link:
    def __init__(self, name, trace, *, port, profile=None, max_hz=30, mock_output=True, behavior=None):
        self.name = name
        self.port, self.profile = port, deepcopy(profile)
        self.max_hz, self.mock_output = max_hz, mock_output
        self.behavior = behavior or {}
        self.wire_neutral = self.behavior.get("wire_neutral", True)
        self.closed = self.behavior.get("closed", False)
        self.asynchronous = True
        self.last_error = self.behavior.get("last_error", "")
        self.trace = trace

    def __setattr__(self, key, value):
        if key in {"safety", "authorization_guard"} and hasattr(self, "trace"):
            self.trace.record(self.name + "." + key, value)
        super().__setattr__(key, value)

    def release(self):
        self.trace.record(self.name + ".release")
        if self.behavior.get("release_raise"):
            raise OSError("fake release failed")
        result = self.behavior.get("release", True)
        if result:
            self.wire_neutral = True
        return result, "neutral" if result else "fake neutral pending"

    release_all = release

    def close(self):
        self.trace.record(self.name + ".close")
        if self.behavior.get("close_raise"):
            raise OSError("fake close failed")
        self.closed = self.behavior.get("close_confirmed", True)

    def observation(self):
        return normalized({name: getattr(self, name) for name in (
            "name", "port", "profile", "max_hz", "mock_output", "wire_neutral", "closed", "last_error",
        )})


class Control(SimpleNamespace):
    def __setattr__(self, key, value):
        if key in CONTROL_DEFAULTS or key in {"dt7_link", "cc_link"}:
            if hasattr(self, "trace"):
                self.trace.record("control." + key, value)
        super().__setattr__(key, value)

    def release_direct_dt7(self):
        self.trace.record("control.release_direct_dt7")
        KeyboardControlBridge.release_direct_dt7(self)

    def release_custom_controller(self):
        self.trace.record("control.release_custom_controller")
        KeyboardControlBridge.release_custom_controller(self)


class Manager(BackendManager):
    def __setattr__(self, key, value):
        if key in MANAGER_DEFAULTS or key in {"_dt7_link", "_cc_link"}:
            if hasattr(self, "trace"):
                self.trace.record("manager." + key, value)
        super().__setattr__(key, value)


def observe(case):
    trace = Trace(case.get("fault"))
    values = SETTINGS | case.get("settings", {})
    values = {name: value for name, value in values.items() if name not in case.get("missing", [])}
    module = None if case.get("no_module") else Module(case.get("enabled", True), values)
    registry = None if case.get("no_registry") else SimpleNamespace(get=lambda _: module)
    control = Control(**(CONTROL_DEFAULTS | case.get("control", {})))
    control.cc_link = control.dt7_link = None
    manager = object.__new__(Manager)
    for name, value in (MANAGER_DEFAULTS | case.get("manager", {})).items():
        setattr(manager, name, value)
    manager.safety = Safety(trace, case)
    manager.polar_bridge = SimpleNamespace(active=True)
    manager.controller_angles = SimpleNamespace(owned_ports=tuple(case.get("owned_ports", [])))
    profile = deepcopy(case.get("profile", PROFILE))
    links = []
    for kind in ("cc", "dt7"):
        if "old_" + kind not in case:
            continue
        behavior = case["old_" + kind]
        same = behavior.get("same", False)
        port = str(values.get("CC Port" if kind == "cc" else "DT7 Port", "Auto") or "Auto") if same else "OLD"
        mock = bool(values.get("CC Mock Writes", True)) if kind == "cc" else not bool(values.get("DT7 Real TX", False))
        old = Link("old_" + kind, trace, port=port, profile=profile if kind == "dt7" else None,
                   max_hz=float(values.get("CC Hz", 30) or 30) if kind == "cc" and same else 30,
                   mock_output=mock, behavior=behavior)
        setattr(manager, "_" + kind + "_link", old)
        setattr(control, kind + "_link", old)
        links.append(old)
    manager.trace = control.trace = trace

    def factory(kind, **kwargs):
        # Ownership publication is a separate constructor seam, not endpoint
        # configuration. The frozen configuration/effect fixture is unchanged;
        # real admission-failure retention is tested with actual writers.
        retain_owner = kwargs.pop("retain_owner", None)
        trace.record("factory." + kind, kwargs)
        kwargs.pop("safety", None)
        link = Link("new_" + kind + "_" + str(len(links)), trace, **kwargs)
        links.append(link)
        if retain_owner is not None:
            retain_owner(link)
        return link

    def load_profile():
        trace.record("profile.load")
        return deepcopy(profile)

    outcomes = []
    errors = []
    with patch("rm_client.backend.manager.dt7_link.load_profile", load_profile), \
            patch("rm_client.backend.manager.dt7_link.Dt7KeyMouseLink", side_effect=lambda **kw: factory("dt7", **kw)), \
            patch("rm_client.backend.manager.AsyncDt7KeyMouseLink", side_effect=lambda **kw: factory("dt7", **kw)), \
            patch("rm_client.backend.manager.cc_link.CustomControllerLink", side_effect=lambda **kw: factory("cc", **kw)), \
            patch("rm_client.backend.manager.cc_link.AsyncCustomControllerLink", side_effect=lambda **kw: factory("cc", **kw)):
        for index in range(case.get("repeat", 1)):
            try:
                outcomes.append(manager._configure_device_egress(registry, None if case.get("no_control") else control))
            except Exception as exc:
                errors.append([index, type(exc).__name__])
    return normalized({
        "outcomes": outcomes, "errors": errors, "events": trace.events,
        "control": {name: getattr(control, name) for name in (*CONTROL_DEFAULTS, "cc_link", "dt7_link")},
        "manager": {name: getattr(manager, name, None) for name in (*MANAGER_DEFAULTS, "_cc_link", "_dt7_link")},
        "links": [link.observation() for link in links], "safety_locked": manager.safety.locked,
    })


def source_digests():
    methods = [getattr(BackendManager, name) for name in (
        "_configure_device_egress", "_configure_cc_egress", "_cc_link_for",
    )] + [KeyboardControlBridge.release_custom_controller, KeyboardControlBridge.release_direct_dt7]
    return {method.__qualname__: hashlib.sha256(inspect.getsource(method).encode()).hexdigest()
            for method in methods}


def initial_snapshots():
    return {
        "control": CONTROL_DEFAULTS | {"cc_link": None, "dt7_link": None},
        "manager": MANAGER_DEFAULTS | {"_cc_link": None, "_dt7_link": None},
    }


def compact_observation(observed):
    result = dict(observed)
    for name, initial in initial_snapshots().items():
        result[name + "_delta"] = {key: value for key, value in result.pop(name).items()
                                    if canonical(value) != canonical(initial[key])}
    return result


def expanded_observation(observed, baseline):
    result = dict(observed)
    for name in ("control", "manager"):
        result[name] = baseline["initial"][name] | result.pop(name + "_delta")
    return result


def record_baseline():
    if not SOURCE_DIGESTS or source_digests() != SOURCE_DIGESTS:
        raise SystemExit("Recording refused: device egress sources differ from the pinned pre-extraction source")
    records = {name: {"input": normalized(case), "observed": compact_observation(observe(case))} for name, case in cases().items()}
    metadata = {"source_sha256": SOURCE_DIGESTS,
                "scope": "After CC conflict-neutral correction; ordered effects and abort state; fake writers and safety",
                "initial": initial_snapshots()}
    lines = [json.dumps(metadata, indent=2)[:-2] + ",", '  "cases": {']
    for index, (name, record) in enumerate(records.items()):
        lines.extend(["    " + json.dumps(name) + ": {", '      "input": ' + json.dumps(record["input"]) + ",",
                      '      "observed": {'])
        for key, value in record["observed"].items():
            if key != "events":
                lines.append("        " + json.dumps(key) + ": " + json.dumps(value, ensure_ascii=False) + ",")
        lines.append('        "events": [')
        events = record["observed"]["events"]
        for offset, event in enumerate(events):
            lines.append("          " + json.dumps(event, ensure_ascii=False) + ("," if offset + 1 < len(events) else ""))
        lines.extend(["        ]", "      }", "    }" + ("," if index + 1 < len(records) else "")])
    lines.extend(["  }", "}", ""])
    FIXTURE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Recorded {len(records)} device-egress cases")


class DeviceEgressCharacterizationTests(unittest.TestCase):
    def test_frozen_configuration_and_ordered_effects(self):
        baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
        all_cases = cases()
        self.assertEqual(canonical(initial_snapshots()), canonical(baseline["initial"]))
        self.assertEqual(list(all_cases), list(baseline["cases"]))
        for name, case in all_cases.items():
            with self.subTest(case=name):
                expected = baseline["cases"][name]
                self.assertEqual(canonical(case), canonical(expected["input"]))
                self.assertEqual(canonical(observe(case)),
                                 canonical(expanded_observation(expected["observed"], baseline)))

    def test_cc_falling_edge_precedes_blocked_dt7_replacement(self):
        observed = observe(cases()["dt7_failure_preserves_cc_falling_edge"])
        operations = [event[0] for event in observed["events"]]
        self.assertLess(operations.index("old_cc.release"), operations.index("old_dt7.release"))
        self.assertFalse(observed["control"]["cc_enabled"])
        self.assertFalse(observed["control"]["dt7_enabled"])

    def test_unconfirmed_cc_neutral_keeps_old_target_and_blocks_replacement(self):
        observed = observe(cases()["cc_different_moving_release_pending"])
        self.assertEqual(observed["manager"]["_cc_link"], {"link": "old_cc"})
        self.assertEqual(observed["control"]["cc_link"], {"link": "old_cc"})
        self.assertTrue(observed["manager"]["_cc_reconfigure_blocked"])
        self.assertFalse(observed["control"]["cc_enabled"])
        self.assertFalse(any(event[0] == "factory.cc" for event in observed["events"]))

    def test_malformed_frequency_aborts_before_dt7_selection(self):
        observed = observe(cases()["cc_frequency_bad"])
        self.assertEqual(observed["errors"], [[0, "ValueError"]])
        self.assertEqual(observed["events"], [])
        self.assertFalse(observed["control"]["dt7_selected"])

    def test_corrected_conflict_neutralization_is_part_of_this_baseline(self):
        observed = observe(cases()["cc_conflict_failed_neutral"])
        self.assertIn(["old_cc.release"], observed["events"])
        self.assertTrue(observed["control"]["cc_release_pending"])
        self.assertFalse(observed["control"]["cc_enabled"])
        self.assertEqual(observed["manager"]["_cc_link"], {"link": "old_cc"})

    def test_frozen_source_provenance_is_explicit(self):
        baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(baseline["source_sha256"], SOURCE_DIGESTS)
        self.assertEqual(len(SOURCE_DIGESTS), 5)


if __name__ == "__main__":
    if "--record-baseline" in sys.argv:
        record_baseline()
    else:
        unittest.main()
