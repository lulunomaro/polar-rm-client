"""Frozen domain/profile/scenario behavior before dependency-boundary changes.

The fixture is recorded only from the pinned pre-extraction files. Tests invoke
real profile dispatch, annual policy, scenario adapters, and economy command
submission. Recording ports replace only host effects; no device/network/Qt.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, fields, replace
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

from rm_client import economy, model, scenarios
from rm_client.backend.referee import registry
from rm_client.backend.referee.rm2026 import state_adapter
from rm_client.backend.referee.student_legacy import state_adapter as student_adapter

FIXTURE = ROOT / "tests/fixtures/referee_domain_baseline.json"
SOURCE_DIGESTS = {'rm_client/backend/referee/registry.py': '17c31581e122922f38cbbc0787bca99b11f5865bf297bef887a00ef19e675851', 'rm_client/backend/referee/rm2026/economy_rules.py': '48aedcc593c3f58178fa4d487db04d762ab1ce028701bbda141ae1ba4f236675', 'rm_client/backend/referee/rm2026/state_adapter.py': 'c975d8be3f345862943f42cf5b13c1ff3f7e7d5b67683ab1c6f146a09e85c074', 'rm_client/backend/referee/student_legacy/state_adapter.py': '934d631bc5ec1724d3772887e0bd41da28634ba103b94fddf7b8622348b274aa', 'rm_client/model.py': 'aadf7507d5bdced31a8f73a619246804f3c8280a1284e9a0bb3d0d9f5a5a12f0', 'rm_client/scenarios.py': 'ab4c2fc487a1558a18e2f24554e53afec5e987f959a1ee2154f2934094ce6c3d', 'rm_client/economy.py': 'fb245ca5c87db8025815c8bf7f04c59e23b2c4c79437a5aae365c1873f4be508'}


def clean(value):
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(item) for item in value]
    if isinstance(value, (frozenset, set)):
        return sorted(clean(item) for item in value)
    return value


def outcome(action):
    try:
        return {"value": clean(action())}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc),
                "cause": None if exc.__cause__ is None else
                [type(exc.__cause__).__name__, clean(exc.__cause__.args)]}


def canonical(value):
    return json.dumps(clean(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


def public_shape():
    types = [economy.EconomyContext, economy.EconomyOffer,
             economy.EconomyRequest, economy.EconomyTransaction,
             registry.RefereeProfile, scenarios.ConnectionProfile,
             scenarios.SafetyProfile, scenarios.ScenarioConfig]
    return {kind.__name__: {"signature": str(inspect.signature(kind)),
                           "fields": [item.name for item in fields(kind)]}
            for kind in types}


def ast_contracts():
    # inspect.getsource follows the real definition after an alias is moved.
    symbols = [economy.EconomyContext, economy.EconomyOffer,
               economy.EconomyRequest, economy.EconomyTransaction,
               economy.EconomyCommandBridge, registry.RefereeProfile,
               scenarios.ConnectionProfile, scenarios.SafetyProfile,
               scenarios.ScenarioConfig, scenarios.ScenarioManager,
               state_adapter.Rm2026StateAdapter, student_adapter.StudentLegacyStateAdapter]
    return {symbol.__name__: hashlib.sha256(
        ast.dump(ast.parse(inspect.getsource(symbol)), include_attributes=False).encode()
    ).hexdigest() for symbol in symbols}


def profile_observations():
    profiles = {}
    for key in registry.supported_profiles():
        profile = registry.get_profile(key)
        values = {item.name: getattr(profile, item.name)
                  for item in fields(profile) if item.name not in {"codec", "uplink_hz"}}
        values["codec"] = profile.codec.__name__
        values["uplink_hz"] = dict(profile.uplink_hz)
        values["downlink"] = sorted(profile.codec.DOWNLINK_MESSAGES)
        values["uplink"] = sorted(profile.codec.UPLINK_MESSAGES)
        values["client_ids"] = [
            [repr(value), outcome(lambda value=value: profile.validate_client_id(value))]
            for value in ["1", "9", "101", "109", " 003 ", 3, "0", "10", "100", "110",
                          "3.0", "-1", "", "３", "٣", None, True]
        ]
        values["publish"] = [
            [topic, repr(qos), outcome(lambda: profile.validate_publish(topic, qos))]
            for topic, qos in itertools.product(
                ["CommonCommand", "AirSupportCommand", "KeyboardMouseControl", "MapClickCmd", "GameStatus", "", None],
                [-1, 0, 1, 2, True, False, "0", None, 0.0, 1.0],
            )
        ]
        host = object()
        adapter = profile.create_state_adapter(host)
        values["adapter"] = {"type": type(adapter).__module__ + "." + type(adapter).__name__,
                             "same_host": adapter.host is host}
        profiles[key] = values
    rejected = {repr(key): outcome(lambda key=key: registry.get_profile(key).profile_id)
                for key in [None, "", "unreviewed", "rm2026-v2", "rm2026-v2.0", "RM2026-V2.0.0", 2026]}
    return {"profiles": profiles, "rejected": rejected}


def offer_cases():
    for index, (mode, robot, alive, mock) in enumerate(itertools.product(
            ["RMUC7v7", "RMUL3v3", "unknown"],
            ["Hero", "Infantry1", "Infantry2", "Sentinel", "Engineer", "Aerial", "Radar"],
            [True, False], [True, False])):
        yield str(index), dict(match_mode=mode, robot_profile=robot,
            current_health=250 if alive else 0, economy_mock_mode=mock,
            remaining_economy=[0, 9, 10, 49, 50, 100, 150, 500][index % 8],
            stage_countdown_sec=[420, 419, 360, 359, 1, 0, -1][index % 7],
            supply_zone_known=index % 3 != 0, in_supply_zone=index % 2 != 0,
            can_remote_heal=index % 3 == 0, can_remote_ammo=index % 4 == 0,
            out_of_combat=index % 4 != 0, respawn_can_free=index % 3 == 0,
            respawn_can_pay=index % 4 == 0, respawn_gold_cost=[0, 80, 100][index % 3],
            air_support_active=index % 3 == 0, air_support_can_free=index % 4 == 0)
    for field, value in [("remaining_economy", "bad"), ("respawn_progress", "bad"),
                         ("stage_countdown_sec", "bad"), ("max_health", None),
                         ("remaining_ammo", float("inf")), ("level", "bad")]:
        yield "invalid_" + field, {field: value, "current_health": 0}


def offer_observation(values, profile_id=registry.DEFAULT_PROFILE):
    state = model.RobotState(**values, referee_profile=profile_id)
    return outcome(lambda: [asdict(offer) for offer in economy.available_actions(state)])


def scenario_observation(key, fail_at=None):
    manager = scenarios.ScenarioManager()
    config = manager.set_scenario(key)
    # Production registry and Setting objects; track their actual assignment
    # order and inject a failing write rather than duplicate scenario logic.
    with patch.object(model, "polar_port_choices", return_value=["Stub"]), \
            patch.object(model, "dt7_port_choices", return_value=["Stub"]):
        settings = model.build_default_registry()
    names = {id(setting): [module.name, setting.name]
             for module in settings.modules for setting in module.settings}
    events = []
    original = model.Setting.__setattr__

    def write(target, name, value):
        if name == "value" and id(target) in names:
            events.append([*names[id(target)], clean(value)])
            if len(events) == fail_at:
                raise OSError("injected setting write")
        return original(target, name, value)

    with patch.object(model.Setting, "__setattr__", write):
        result = outcome(lambda: manager.apply_to_registry(settings))
    values = [[*names[id(setting)], clean(setting.value)] for module in settings.modules
              for setting in module.settings if [module.name, setting.name] in [item[:2] for item in events]]
    return {"config": asdict(config), "events": events, "result": result, "values": values}


def transaction_observation(profile, topic, sender_mode):
    state = model.RobotState(referee_profile=profile)
    bridge = economy.EconomyCommandBridge()
    calls = []

    def sender(request):
        calls.append(asdict(request))
        if sender_mode == "raises":
            raise OSError("injected sender failure")
        return sender_mode != "rejects"

    bridge.sender = sender
    offer = economy.EconomyOffer("chosen", "ammo", "Chosen", "Chosen", "", "one",
                                 10, 10, 1, 10, True, topic=topic)
    with patch.object(economy.time, "time", return_value=1700000000.):
        result = outcome(lambda: asdict(bridge.submit(state, offer, quantity=2,
                                                      mock=sender_mode == "mock", now=11.)))
    return clean({"result": result, "sequence": bridge.sequence,
                  "last": None if bridge.last_request is None else asdict(bridge.last_request),
                  "history": [asdict(item) for item in bridge.history], "calls": calls})


def snapshot():
    return {"public_shape": public_shape(), "ast_contracts": ast_contracts(),
            "profile_observations": profile_observations(),
            "offers": {key: offer_observation(values) for key, values in offer_cases()},
            "scenarios": {f"{key}/{fail}": scenario_observation(key, fail)
                          for key, fail in itertools.product([*scenarios.SCENARIOS, "missing"], [None, 1, 7, 14])},
            "transactions": {f"{profile}/{topic}/{sender}": transaction_observation(profile, topic, sender)
                             for profile, topic, sender in itertools.product(
                                 [registry.DEFAULT_PROFILE, registry.STUDENT_PREVIEW_PROFILE, "unknown"],
                                 ["CommonCommand", "AirSupportCommand"], ["mock", "accepts", "rejects", "raises"])}}


def record():
    for relative, expected in SOURCE_DIGESTS.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"Baseline recording refused: {relative} differs from pinned source")
    FIXTURE.write_text(json.dumps({"source_sha256": SOURCE_DIGESTS, **snapshot()},
                                 ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class RefereeDomainCharacterizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_fixture_has_pinned_source(self):
        self.assertEqual(self.baseline["source_sha256"], SOURCE_DIGESTS)
        self.assertEqual(len(SOURCE_DIGESTS), 7)

    def test_public_constructor_shape_and_field_order(self):
        self.assertEqual(public_shape(), self.baseline["public_shape"])

    def test_moved_types_and_policy_callers_have_identical_ast(self):
        expected = dict(self.baseline["ast_contracts"])
        # Explicit 2026-10-17 bug correction: a present team HP zero must not
        # resurrect retained non-local dynamic HP. Keep the old fixture pinned
        # and approve only this exact adapter AST, not a blanket method waiver.
        # Real-producer/mutation/frozen-delta evidence lives in
        # test_referee_health_source_selection.py and the ownership ledger.
        self.assertEqual(expected["Rm2026StateAdapter"],
            "9835d242b6c63ad847a673aad074404245c12d8d579cc5e02ae4836a0858c263")
        expected["Rm2026StateAdapter"] = (
            "4cc3b3499f58fb51d1ca7843f8b673346607141849f69b1f9d02653ba17bd3f3")
        # Canonical scalar-writer contract v2 (source 2f956792): move the
        # two-line present-left_time eligibility derivation into this adapter.
        # Preserve the historical fixture and the prior health-fix hash above;
        # approve this exact complete AST, including every unrelated method.
        self.assertEqual(expected["Rm2026StateAdapter"],
            "4cc3b3499f58fb51d1ca7843f8b673346607141849f69b1f9d02653ba17bd3f3")
        expected["Rm2026StateAdapter"] = (
            "e0a512c95a7c6a5e57cbbd5dd3ba57fa7c96516075f794734708b55da98c3c04")
        self.assertEqual(ast_contracts(), expected)

    def test_profiles_transport_policy_validation_and_factory_identity(self):
        self.assertEqual(canonical(profile_observations()), canonical(self.baseline["profile_observations"]))

    def test_every_offer_field_and_failure(self):
        for key, values in offer_cases():
            with self.subTest(case=key):
                self.assertEqual(canonical(offer_observation(values)), canonical(self.baseline["offers"][key]))

    def test_scenario_default_and_ordered_partial_writes(self):
        for key, fail in itertools.product([*scenarios.SCENARIOS, "missing"], [None, 1, 7, 14]):
            with self.subTest(key=key, fail=fail):
                self.assertEqual(scenario_observation(key, fail), self.baseline["scenarios"][f"{key}/{fail}"])

    def test_actual_command_bridge_encoding_and_sender_failure_order(self):
        for profile, topic, sender in itertools.product(
                [registry.DEFAULT_PROFILE, registry.STUDENT_PREVIEW_PROFILE, "unknown"],
                ["CommonCommand", "AirSupportCommand"], ["mock", "accepts", "rejects", "raises"]):
            with self.subTest(profile=profile, topic=topic, sender=sender):
                self.assertEqual(transaction_observation(profile, topic, sender),
                                 self.baseline["transactions"][f"{profile}/{topic}/{sender}"])

    def test_readonly_and_unknown_dispatch_keep_existing_precedence(self):
        state = model.RobotState(remaining_economy="invalid")
        self.assertEqual(registry.get_profile(registry.STUDENT_PREVIEW_PROFILE).available_actions(state), [])
        unknown = replace(registry.get_profile(), profile_id="future")
        with self.assertRaises(registry.UnsupportedRefereeProfile):
            unknown.create_state_adapter(object())
        with self.assertRaises(registry.UnsupportedRefereeProfile):
            unknown.available_actions(state)
        self.assertEqual(replace(unknown, read_only=True).available_actions(state), [])
        with self.assertRaises(ValueError):
            replace(registry.get_profile(), read_only=True).available_actions(state)


if __name__ == "__main__":
    if "--record-baseline" in sys.argv:
        record()
    else:
        unittest.main()
