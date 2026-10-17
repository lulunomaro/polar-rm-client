"""Executable dependency and caller contracts for referee domain boundaries.

Import blockers run in clean interpreters; they reject deferred imports too.
The registry remains the reviewed backend composition root, while defaults and
annual value policies run without transaction/factory implementation imports.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import importlib
import json
from pathlib import Path
import pickle
import subprocess
import sys
import textwrap
import typing
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client import economy, economy_values, model, referee_profiles, scenarios
from rm_client.backend.referee import registry
from rm_client.backend.referee.rm2026 import state_adapter
from tools.quality.metrics import measure_tree


def isolated(script, blocked=()):
    setup = f'''
import importlib.abc
import sys
sys.path.insert(0, {str(ROOT)!r})
BLOCKED = {tuple(blocked)!r}
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in BLOCKED):
            raise AssertionError("Forbidden dependency: " + fullname)
sys.meta_path.insert(0, BlockImports())
'''
    return subprocess.run([sys.executable, "-c", textwrap.dedent(setup) + textwrap.dedent(script)],
                          cwd=ROOT, capture_output=True, text=True, timeout=30)


class RefereeDependencyTests(unittest.TestCase):
    def assert_isolated(self, script, blocked=()):
        result = isolated(script, blocked)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_domain_defaults_and_actual_catalog_assembly_do_not_load_backend(self):
        self.assert_isolated('''
from unittest.mock import patch
from rm_client import model, scenarios, referee_profiles
state = model.RobotState()
assert state.referee_profile == referee_profiles.DEFAULT_PROFILE
assert scenarios.SCENARIOS["OfficialEvent"].connection.mqtt_host == "192.168.12.1"
with patch.object(model, "polar_port_choices", return_value=["Stub"]), \\
     patch.object(model, "dt7_port_choices", return_value=["Stub"]):
    modules = model.build_default_registry()
profile = modules.get("MQTTLink").get_setting("Referee Profile")
assert tuple(profile.options) == referee_profiles.supported_profiles()
scenarios.ScenarioManager("OfficialEvent").apply_to_registry(modules)
assert modules.get("MQTTLink").get_setting("Port").value == 3333
assert "rm_client.backend" not in sys.modules
''', ("rm_client.backend", "rm_client.economy", "PySide6", "cv2", "serial.tools"))

    def test_annual_policy_runs_without_transaction_module_model_or_gui(self):
        self.assert_isolated('''
from types import SimpleNamespace
from rm_client.economy_values import EconomyContext, EconomyOffer
from rm_client.backend.referee.rm2026.economy_rules import available_actions
state = SimpleNamespace(match_mode="RMUC7v7", robot_profile="Infantry1", current_health=500,
                        remaining_economy=100, economy_mock_mode=False)
offers = available_actions(state)
assert [offer.key for offer in offers][:2] == ["rmuc_infantry_17_10", "rmuc_infantry_17_100"]
assert all(type(offer) is EconomyOffer for offer in offers)
assert EconomyContext.from_state(state).gold == 100
assert "rm_client.economy" not in sys.modules
''', ("rm_client.economy", "rm_client.model", "rm_client.backend.referee.registry", "PySide6", "cv2"))

    def test_registry_metadata_does_not_construct_adapters_or_transactions(self):
        self.assert_isolated('''
from rm_client.backend.referee.registry import get_profile, supported_profiles
assert len(supported_profiles()) == 2
profile = get_profile()
assert profile.codec.encode_common_command(1, 10) == b"\\x08\\x01\\x10\\x0a"
assert profile.validate_publish("CommonCommand", 1) == (True, "ok")
''', ("rm_client.model", "rm_client.economy", "rm_client.economy_values",
      "rm_client.backend.referee.rm2026.state_adapter",
      "rm_client.backend.referee.student_legacy.state_adapter",
      "rm_client.backend.referee.rm2026.economy_rules", "PySide6", "cv2"))

    def test_metadata_is_single_authority_for_scenario_and_backend_assembly(self):
        self.assert_isolated('''
from dataclasses import replace
from types import MappingProxyType
from rm_client import referee_profiles
key = referee_profiles.DEFAULT_PROFILE
changed = replace(referee_profiles.get_profile_definition(), mqtt_host="fixture-host",
                  mqtt_port=4000, udp_port=4001, max_qos=0)
referee_profiles._DEFINITIONS = MappingProxyType({
    **referee_profiles._DEFINITIONS, key: changed})
from rm_client import scenarios
from rm_client.backend.referee.registry import get_profile
profile = get_profile()
assert (profile.mqtt_host, profile.mqtt_port, profile.udp_port, profile.max_qos) == ("fixture-host", 4000, 4001, 0)
assert profile.validate_publish("CommonCommand", 1)[0] is False
assert scenarios.SCENARIOS["OfficialEvent"].connection.mqtt_host == "fixture-host"
for config in scenarios.SCENARIOS.values():
    assert config.connection.mqtt_port == 4000
    assert config.connection.udp_port == 4001
''')

    def test_domain_cycle_is_absent_in_full_deferred_import_graph(self):
        report = measure_tree(ROOT)
        involved = {"rm_client.model", "rm_client.scenarios", "rm_client.economy",
                    "rm_client.economy_values", "rm_client.referee_profiles"}
        self.assertFalse([cycle for cycle in report["all_import_cycles"]
                          if involved.intersection(cycle) or
                          any(item.startswith("rm_client.backend.referee") for item in cycle)])
        self.assertEqual(report["runtime_cycles"], [])
        # Both annual factories/rules remain explicit imports for source-based
        # dependency discovery. This is not a Windows PyInstaller-build claim.
        edges = report["modules"]["rm_client.backend.referee.registry"]["imports"]
        targets = {item["target"] for item in edges}
        self.assertTrue({"rm_client.backend.referee.rm2026.state_adapter",
                         "rm_client.backend.referee.student_legacy.state_adapter",
                         "rm_client.backend.referee.rm2026.economy_rules"} <= targets)


class RefereeCompatibilityTests(unittest.TestCase):
    def test_all_metadata_fields_are_shared_by_composed_profiles(self):
        self.assertIs(registry.supported_profiles, referee_profiles.supported_profiles)
        self.assertIs(registry.UnsupportedRefereeProfile, referee_profiles.UnsupportedRefereeProfile)
        self.assertEqual(tuple(registry._PROFILES), referee_profiles.supported_profiles())
        for profile_id in registry.supported_profiles():
            definition = referee_profiles.get_profile_definition(profile_id)
            profile = registry.get_profile(profile_id)
            for item in fields(definition):
                with self.subTest(profile=profile_id, field=item.name):
                    self.assertEqual(getattr(definition, item.name), getattr(profile, item.name))
            self.assertIs(registry.get_profile(profile_id), profile)
            with self.assertRaises(FrozenInstanceError):
                definition.mqtt_port = 1000
            with self.assertRaises(TypeError):
                profile.uplink_hz["CommonCommand"] = 1000

    def test_legacy_value_imports_are_identical_and_old_pickle_globals_resolve(self):
        self.assertIs(economy.EconomyContext, economy_values.EconomyContext)
        self.assertIs(economy.EconomyOffer, economy_values.EconomyOffer)
        self.assertIs(economy.EconomyAction, economy_values.EconomyOffer)
        for name in ("EconomyContext", "EconomyOffer", "EconomyAction"):
            historical_global = f"crm_client.economy\n{name}\n.".encode()
            self.assertIs(pickle.loads(historical_global), getattr(economy, name))
        old_error = b"crm_client.backend.referee.registry\nUnsupportedRefereeProfile\n."
        self.assertIs(pickle.loads(old_error), registry.UnsupportedRefereeProfile)
        offer = economy.available_actions(model.RobotState())[0]
        self.assertEqual(pickle.loads(pickle.dumps(offer)), offer)

    def test_state_adapter_annotations_still_resolve_actual_robot_state(self):
        # No TYPE_CHECKING-only shortcut: runtime introspection remains valid.
        hints = typing.get_type_hints(state_adapter.Rm2026StateAdapter.sync_frontend_state)
        self.assertIs(hints["state"], model.RobotState)

    def test_both_factory_constructor_failures_propagate_without_fallback(self):
        for profile_id, module_name, class_name in [
                (registry.DEFAULT_PROFILE, "rm_client.backend.referee.rm2026.state_adapter", "Rm2026StateAdapter"),
                (registry.STUDENT_PREVIEW_PROFILE, "rm_client.backend.referee.student_legacy.state_adapter", "StudentLegacyStateAdapter")]:
            with self.subTest(profile=profile_id):
                module = importlib.import_module(module_name)
                profile = registry.get_profile(profile_id)
                host = object()
                failure = TypeError("injected adapter constructor failure")
                with patch.object(module, class_name, side_effect=failure) as constructor:
                    with self.assertRaises(TypeError) as result:
                        profile.create_state_adapter(host)
                    self.assertIs(result.exception, failure)
                    constructor.assert_called_once_with(host)
                self.assertIs(registry.get_profile(profile_id), profile)
                self.assertIs(profile.create_state_adapter(host).host, host)

    def test_annual_rule_failure_propagates_then_real_dispatch_recovers(self):
        module = importlib.import_module("rm_client.backend.referee.rm2026.economy_rules")
        state = model.RobotState()
        failure = ValueError("injected annual rules failure")
        with patch.object(module, "available_actions", side_effect=failure) as rules:
            with self.assertRaises(ValueError) as result:
                economy.available_actions(state)
            self.assertIs(result.exception, failure)
            rules.assert_called_once_with(state)
        self.assertTrue(economy.available_actions(state))
        self.assertEqual(economy.available_actions(model.RobotState(
            referee_profile=registry.STUDENT_PREVIEW_PROFILE)), [])


if __name__ == "__main__":
    unittest.main()
