"""Offline UI/state regressions with inert device boundaries.

PySide6 is required by the polar package's lifecycle import. The tested owner
methods are extracted from production AST and exercised against explicit inert
test doubles; this suite does not construct a Qt window or test real painting,
cameras, serial devices, or network peers. Small pure modules are loaded directly.
Run with the client environment: python tests/test_ui_state_regressions.py
"""
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_pure(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT))
from rm_client.ui.polar.frame_telemetry import FrameTelemetry
from rm_client.ui.polar.window_lifecycle import FinalContextPorts, OwnerAction, OwnerActionFailure, WindowLifecycleController, run_owner_actions
from rm_client import config_store as store
from rm_client.configuration.transactions import (
    ConfigurationApplyError, ConfigurationRestorePoint, RuntimeRestorePending,
    apply_configuration_transaction, rollback_configuration_transaction,
)
from rm_client.configuration.quiescence import quiesce_configuration_inputs
from rm_client.configuration.registry import RegistrySnapshot
from rm_client.configuration.profile_saves import ProfileSaveState
from rm_client.ui.polar.profile_save_fields import ProfileSaveField
from rm_client.configuration.pending import ProfileIdentity, ConfigurationPhase, CompletionAction, PendingConfigurationChange
profiles = load_pure("ui_regression_profiles", "rm_client/robot_profiles.py")


def extract_class(relative, name, names, namespace=None, base=None):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    selected = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    cls.body = selected
    cls.decorator_list = []
    cls.bases = [ast.Name(id="TestBase", ctx=ast.Load())] if base else []
    cls.keywords = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[])
    scope = dict(namespace or {})
    if base:
        scope["TestBase"] = base
    exec(compile(ast.fix_missing_locations(module), str(ROOT / relative), "exec"), scope)
    return scope[name]


class Setting:
    def __init__(self, name, value, kind="mode", options=None):
        self.name, self.value, self.kind = name, value, kind
        self.options = options or []
        self.min_value = self.max_value = None


class Module:
    def __init__(self, name, *settings, enabled=False):
        self.name, self.settings, self.enabled = name, list(settings), enabled
        self.keybind = "None"

    def get_setting(self, name):
        return next((s for s in self.settings if s.name == name), None)

    def set_enabled(self, value):
        self.enabled = value


class Registry:
    def __init__(self, *modules):
        self.modules = list(modules)

    def get(self, name):
        return next((m for m in self.modules if m.name == name), None)


def registry():
    return Registry(
        Module("ConfigManager", Setting("Profile", "training", options=["default", "training"]), Setting("Load On Start", True, "bool")),
        Module("AllianceProfile", Setting("Side", "Red", options=["Red", "Blue"])),
        Module("MatchMode", Setting("Alliance", "Red", options=["Red", "Blue"])),
        Module("RobotProfile", Setting("Role", "Infantry1"), Setting("Level", 1, "number")),
        Module("MQTTLink", Setting("Host", "127.0.0.1")),
        Module("Scenario", Setting("Mode", "LabDebug"), Setting("Show Startup", True, "bool")),
        Module("EconomyPanel", Setting("Mock Requests", True, "bool")),
        Module("SpinMode", enabled=False),
    )


PERSISTENCE = "rm_client/ui/polar/mixins/persistence_mixin.py"
Persistence = extract_class(PERSISTENCE, "PersistenceMixin", {
    "apply_robot_profile", "current_robot_profile_key", "load_startup_config", "current_profile", "save_current_config", "flush_auto_save_if_due", "sync_runtime_from_registry", "apply_config_data",
    "capture_configuration_restore_point", "_quiesce_config_reload",
    "restore_configuration_runtime", "fail_closed_configuration", "configuration_change_pending",
    "ensure_configuration_idle", "reject_pending_configuration_edit", "_announce_configuration_failure",
    "record_configuration_failure",
    "on_backend_lifecycle_completion", "_rollback_pending_configuration", "_finish_configuration_restore",
    "_request_registry_runtime", "_complete_registry_runtime", "_restore_configuration_view",
    "cancel_pending_configuration_for_close",
    "_accepted_profile_identity", "_set_profile_selection", "_restorable_profile_name", "_accept_profile_selection", "_save_profile", "flush_auto_save",
}, {
    "quiesce_configuration_inputs": quiesce_configuration_inputs,
    "time": time, "deepcopy": deepcopy, "os": os, "replace": replace, "logging": logging,
    "validate_client_config": store.validate_client_config,
    "ProfileSaveError": store.ProfileSaveError,
    "ConfigurationApplyError": ConfigurationApplyError,
    "RegistrySnapshot": RegistrySnapshot, "ConfigurationPhase": ConfigurationPhase,
    "CompletionAction": CompletionAction, "PendingConfigurationChange": PendingConfigurationChange,
    "ProfileIdentity": ProfileIdentity,
    "RuntimeRestorePending": RuntimeRestorePending,
    "rollback_configuration_transaction": rollback_configuration_transaction,
    "ConfigurationRestorePoint": ConfigurationRestorePoint,
    "apply_configuration_transaction": apply_configuration_transaction,
    "profiles_for_match": profiles.profiles_for_match,
    "profile_by_key": profiles.profile_by_key,
    "robot_id_for_alliance": profiles.robot_id_for_alliance,
    "apply_profile_to_state": profiles.apply_profile_to_state,
})


class Harness(Persistence):
    _accepted_profile = ProfileSaveField("accepted")
    _unsaved_profile_name = ProfileSaveField("draft")
    _auto_save_pending = ProfileSaveField("pending")
    _auto_save_owner = ProfileSaveField("pending_owner")
    _auto_save_due_at = ProfileSaveField("due_at")
    _close_save_retry_required = ProfileSaveField("close_retry_required")
    _config_save_error = ProfileSaveField("error_reported")

    def __init__(self):
        self._profile_saves = ProfileSaveState()
        self.registry = registry()
        # These model-only cases need name normalization, not disk access.
        self.config_store = NS(clean_profile_name=lambda value: store.ClientConfigStore.clean_profile_name(None, value))
        self.events = []
        self._auto_save_pending = True
        self._auto_save_due_at = 0.0
        self.home_visible = True
        self.startup_step = "scenario"
        self.config_loading = False
        self.match_mode_manager = NS(current=NS(key="RMUC7v7"))
        def set_mode(key):
            self.match_mode_manager.current = NS(key=key)
            return self.match_mode_manager.current
        self.match_mode_manager.set_mode = set_mode
        self.seed_match_state = lambda config: None
        self.sync_control_binds = lambda: None
        self.invalidate_gui_cache = lambda: None
        self.scenario_manager = NS(current=NS(key="LabDebug"))
        self.scenario_manager.set_scenario = lambda key: setattr(self.scenario_manager.current, "key", key)
        self.request_generation = 0
        self.state = NS(robot_id=3, alliance_color="Red", robot_profile="Infantry1")
        self.backend = NS(lifecycle_pending=False, mqtt=NS(client_id="3", mock_output=True), lock_control=lambda reason: self.events.append("lock"))
        self.backend.trigger_estop = lambda: self.events.append("estop")
        self.backend.set_client_id = self.set_client_id
        self.backend.request_scenario = self.request_scenario
        self.control = NS(binds={"W": "W", "S": "S"}, release_all=lambda *_: self.events.append("release"), clear_pending=lambda: self.events.append("clear"))

    def request_scenario(self, key, **kwargs):
        self.request_generation += 1
        return self.request_generation

    def complete_configuration(self):
        pending = self._pending_configuration
        profile = profiles.profile_by_key(self.match_mode_manager.current.key, self.current_robot_profile_key())
        self.backend.mqtt.client_id = str(profiles.robot_id_for_alliance(profile, self.alliance_side()))
        self.on_backend_lifecycle_completion(NS(generation=pending.generation, succeeded=True, final=False, failures=()))

    def set_client_id(self, value):
        self.events.append(("identity", value, self.state.robot_id))
        self.backend.mqtt.client_id = str(value)

    def current_robot_performance(self, key):
        return profiles.resolve_performance("RMUC7v7", key)

    def get_setting_number(self, module, name, default):
        item = module.get_setting(name) if module else None
        return item.value if item else default

    def alliance_side(self):
        return self.registry.get("AllianceProfile").get_setting("Side").value

    def setting_bool(self, module, name, default):
        item = module.get_setting(name) if module else None
        return item.value if item else default

    def setting_mode(self, module, name, default):
        return self.setting_bool(module, name, default)

    def current_config_data(self):
        return store.export_client_config(self.registry, gui_mode="JelloSigma", selected_category="System", selected_module="ConfigManager", module_expanded=set(), old_panels={}, new_panels={}, dropdown_panels={})

    def toast(self, message, **kwargs):
        self.events.append(("toast", message))

    def refresh_config_profiles(self):
        pass


class ConfigRegressionTests(unittest.TestCase):
    def test_bundled_profile_schema(self):
        store.validate_client_config(json.loads((ROOT / "configs/default.json").read_text()))

    def test_bad_candidates_rejected_before_any_registry_mutation(self):
        for bad in (
            {"quick_action_wheel": {"sectors": None}},
            {"quick_action_wheel": {"actions": [[]]}},
            {"quick_action_wheel": {"show_labels": "false"}},
            {"quick_action_wheel": {"radius": float("nan")}},
            {"quick_action_wheel": {"sectors": 10 ** 1000}},
            {"old_panels": {"System": {"x": "oops"}}},
            {"modules": {"EconomyPanel": {"settings": {"Mock Requests": "false"}}}},
            {"modules": {"SpinMode": {"enabled": "false"}}},
        ):
            with self.subTest(bad=repr(bad)[:90]):
                reg = registry()
                bad = deepcopy(bad)
                bad.setdefault("modules", {})["MQTTLink"] = {"settings": {"Host": "should-never-apply"}}
                with self.assertRaises(ValueError):
                    store.apply_registry_config(reg, bad)
                self.assertEqual(reg.get("MQTTLink").get_setting("Host").value, "127.0.0.1")

    def test_import_preserves_existing_on_bad_json_and_bad_wheel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = store.ClientConfigStore(root / "profiles")
            cfg.save("default", {"modules": {}})
            target = cfg.profile_path("default")
            original = target.read_bytes()
            source = root / "default.json"
            for payload in ("{", json.dumps({"quick_action_wheel": {"sectors": None}})):
                source.write_text(payload)
                with self.assertRaises(ValueError):
                    cfg.import_file(source, registry=registry())
                self.assertEqual(target.read_bytes(), original)

    def test_valid_import_has_recovery_backup_and_normalized_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = store.ClientConfigStore(root / "profiles")
            cfg.save("default", {"selected_category": "old"})
            old = cfg.profile_path("default").read_bytes()
            source = root / "default.json"
            source.write_text(json.dumps({"selected_category": "new", "profile": "elsewhere"}))
            self.assertEqual(cfg.import_file(source), "default")
            self.assertEqual(cfg.profile_path("default").with_suffix(".json.bak").read_bytes(), old)
            self.assertEqual(cfg.load("default")["selected_category"], "new")
            self.assertEqual(cfg.load("default")["profile"], "default")

    def test_failed_atomic_replace_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = store.ClientConfigStore(Path(temp))
            cfg.save("default", {"selected_category": "old"})
            old = cfg.profile_path("default").read_bytes()
            with patch.object(store.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    cfg.save("default", {"selected_category": "new"})
            self.assertEqual(cfg.profile_path("default").read_bytes(), old)
            self.assertFalse(list(Path(temp).glob("*.tmp")))

    def test_embedded_profile_cannot_redirect_save(self):
        reg = registry()
        store.apply_registry_config(reg, {"modules": {"ConfigManager": {"settings": {"Profile": "default"}}}})
        self.assertEqual(reg.get("ConfigManager").get_setting("Profile").value, "training")
        fixture = Harness()
        self.assertNotIn("Profile", fixture.current_config_data()["modules"]["ConfigManager"]["settings"])

    def test_alliance_new_source_wins_and_legacy_migrates(self):
        for data in (
            {"modules": {"AllianceProfile": {"settings": {"Side": "Blue"}}, "MatchMode": {"settings": {"Alliance": "Red"}}}},
            {"modules": {"MatchMode": {"settings": {"Alliance": "Blue"}}}},
        ):
            reg = registry()
            store.apply_registry_config(reg, data)
            self.assertEqual(reg.get("AllianceProfile").get_setting("Side").value, "Blue")
            self.assertEqual(reg.get("MatchMode").get_setting("Alliance").value, "Blue")

    def test_startup_false_prevents_profile_application(self):
        with tempfile.TemporaryDirectory() as temp:
            h = Harness()
            h.config_store = store.ClientConfigStore(Path(temp))
            h.config_store.save("training", {"modules": {"MQTTLink": {"settings": {"Host": "saved-host"}}}})
            h.config_store.save_startup_preferences("training", False)
            h.apply_config_data = lambda *_args, **_kwargs: self.fail("must not apply disabled profile")
            h.load_startup_config()
            self.assertFalse(h.registry.get("ConfigManager").get_setting("Load On Start").value)
            self.assertEqual(h.config_store.profile_names(), ["training"])

    def test_legacy_false_prevents_first_migrated_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            h = Harness()
            h.config_store = store.ClientConfigStore(Path(temp))
            h.config_store.save("training", {"modules": {"ConfigManager": {"settings": {"Load On Start": False}}}})
            h.apply_config_data = lambda *_args, **_kwargs: self.fail("legacy false must work before apply")
            h.load_startup_config()

    def test_startup_selected_profile_is_used(self):
        with tempfile.TemporaryDirectory() as temp:
            h = Harness()
            h.config_store = store.ClientConfigStore(Path(temp))
            h.config_store.save("other", {"selected_category": "chosen"})
            h.config_store.save_startup_preferences("other", True)
            applied = []
            h.apply_config_data = lambda data, **_: applied.append(data)
            h.load_startup_config()
            self.assertEqual(applied[0]["selected_category"], "chosen")
            self.assertEqual(h.current_profile(), "other")

    def test_save_failure_retains_pending_and_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as temp:
            h = Harness()
            h.config_store = store.ClientConfigStore(Path(temp))
            with patch.object(h.config_store, "save", side_effect=OSError("read-only disk")):
                h.save_current_config(announce=False)
            self.assertTrue(h._auto_save_pending)
            self.assertGreater(h._auto_save_due_at, time.perf_counter())
            h.save_current_config(announce=False)
            self.assertFalse(h._auto_save_pending)
            self.assertEqual(h.config_store.load_startup_preferences()["profile"], "training")

    def test_invalid_ui_apply_does_not_revoke_or_partially_apply(self):
        h = Harness()
        h._apply_config_data = lambda *_: self.fail("invalid candidate reached apply")
        h.apply_config_data({"quick_action_wheel": {"actions": [{}]}})
        self.assertEqual([event for event in h.events if event == "lock"], [])
        self.assertTrue(h.events[-1][1].startswith("配置加载失败"))

    def test_spin_reload_notifies_only_disable_before_lock(self):
        h = Harness()
        h.registry.get("SpinMode").enabled = True
        h.publish_spin_control_state = lambda enabled, **_: h.events.append(("spin", enabled))
        h._apply_config_data = lambda *_args, **_kwargs: None
        h.apply_config_data({"modules": {"SpinMode": {"enabled": False}}}, announce=False)
        self.assertEqual(h.events[0], ("spin", False))
        self.assertEqual(h.events[1], "lock")
        h.complete_configuration()
        h.events.clear()
        h.apply_config_data({"modules": {"SpinMode": {"enabled": True}}}, announce=False)
        self.assertNotIn(("spin", True), h.events)

    def test_runtime_load_preserves_endpoints_and_mock_choice(self):
        h = Harness()
        h.registry.get("MQTTLink").get_setting("Host").value = "saved-host"
        h.registry.get("AllianceProfile").get_setting("Side").value = "Blue"
        h.sync_control_binds = lambda: None
        h.scenario_manager = NS(current=NS(key="LabDebug"))
        h.scenario_manager.set_scenario = lambda key: setattr(h.scenario_manager.current, "key", key)
        h.scenario_manager.apply_to_registry = lambda _: self.fail("profile load reset scenario defaults")
        calls = []
        h.backend.request_scenario = lambda key, **kw: calls.append((key, kw)) or 1
        h.sync_runtime_from_registry()
        self.assertIs(calls[0][1]["registry"], h.registry)
        self.assertFalse(calls[0][1]["start"])
        self.assertEqual(calls[0][1]["robot_id"], 103)
        self.assertEqual(h.registry.get("MQTTLink").get_setting("Host").value, "saved-host")
        self.assertTrue(h.registry.get("EconomyPanel").get_setting("Mock Requests").value)

    def test_runtime_failure_restores_values_without_starting_outputs(self):
        h = Harness()
        previous = h.current_config_data()
        h.scenario_manager = NS(current=NS(key="LabDebug"))
        h.scenario_manager.set_scenario = lambda key: setattr(h.scenario_manager.current, "key", key)
        calls = []
        h.backend.request_scenario = lambda key, **kw: calls.append((key, kw)) or 1
        attempts = []
        def apply(data, **kwargs):
            attempts.append(kwargs)
            store.apply_registry_config(h.registry, data)
            if len(attempts) == 1:
                h.scenario_manager.current.key = "OfficialEvent"
                raise OSError("runtime unavailable")
        h._apply_config_data = apply
        h.apply_config_data({"modules": {"MQTTLink": {"settings": {"Host": "new-host"}}, "Scenario": {"settings": {"Mode": "OfficialEvent"}}}})
        self.assertEqual(h.current_config_data(), previous)
        self.assertEqual(calls[0][0], "LabDebug")
        self.assertFalse(calls[0][1]["start"])
        self.assertEqual(h.events[-2], "lock")
        self.assertIn("正在恢复", h.events[-1][1])
        self.assertTrue(h.configuration_change_pending)
        h.complete_configuration()
        self.assertIn("已恢复设置", h.events[-1][1])

    def test_cross_match_failure_restores_role_options_binds_and_startup(self):
        h = Harness()
        h.registry.get("MatchMode").settings.append(Setting("Mode", "RMUC7v7"))
        role = h.registry.get("RobotProfile").get_setting("Role")
        role.value = "Engineer"
        role.options = [profile.key for profile in profiles.profiles_for_match("RMUC7v7")]
        h.state.robot_id, h.state.robot_profile = 2, "Engineer"
        h.backend.mqtt.client_id = "2"
        h.scenario_manager = NS(current=NS(key="LabDebug"))
        h.scenario_manager.set_scenario = lambda key: setattr(h.scenario_manager.current, "key", key)
        calls = []
        h.backend.request_scenario = lambda key, **kw: calls.append((key, kw)) or 1
        attempts = []
        def apply(data, **kwargs):
            attempts.append(kwargs)
            store.apply_registry_config(h.registry, data)
            if len(attempts) == 1:
                h.match_mode_manager.set_mode("RMUL3v3")
                role.options = [profile.key for profile in profiles.profiles_for_match("RMUL3v3")]
                h.control.binds = {"W": "S", "S": "W"}
                h.home_visible, h.startup_step = False, "done"
                raise OSError("late failure after mode change")
        h._apply_config_data = apply
        h.apply_config_data({"modules": {"MatchMode": {"settings": {"Mode": "RMUL3v3"}}, "RobotProfile": {"settings": {"Role": "Hero"}}}})
        self.assertTrue(h.configuration_change_pending)
        h.complete_configuration()
        self.assertEqual(h.match_mode_manager.current.key, "RMUC7v7")
        self.assertEqual((role.value, h.state.robot_id), ("Engineer", 2))
        self.assertIn("Engineer", role.options)
        self.assertEqual(h.control.binds, {"W": "W", "S": "S"})
        self.assertEqual((h.home_visible, h.startup_step), (True, "scenario"))
        self.assertEqual(calls[0][1]["robot_id"], 2)
        self.assertFalse(calls[0][1]["start"])


class IdentityAndDisplayTests(unittest.TestCase):
    def test_blue_identity_locked_before_network_or_state_change(self):
        h = Harness()
        h.registry.get("AllianceProfile").get_setting("Side").value = "Blue"
        h.apply_robot_profile("Infantry1", close_home=False, announce=False)
        self.assertEqual(h.events[:4], ["lock", "release", "clear", ("identity", 103, 3)])
        self.assertEqual((h.state.robot_id, h.backend.mqtt.client_id, h.state.alliance_color), (103, "103", "Blue"))
        self.assertEqual(h.registry.get("MatchMode").get_setting("Alliance").value, "Blue")

    def test_identity_reconnect_failure_leaves_old_state_locked(self):
        h = Harness()
        h.registry.get("AllianceProfile").get_setting("Side").value = "Blue"
        h.backend.set_client_id = lambda _: (_ for _ in ()).throw(OSError("reconnect"))
        h.apply_robot_profile("Infantry1", close_home=False, announce=False)
        self.assertEqual(h.state.robot_id, 3)
        self.assertEqual(h.state.alliance_color, "Red")
        self.assertEqual(h.alliance_side(), "Red")
        self.assertEqual(h.events[0], "lock")

    def test_reconnect_failure_after_id_change_keeps_all_views_aligned(self):
        h = Harness()
        h.registry.get("AllianceProfile").get_setting("Side").value = "Blue"
        def reconnect(value):
            h.backend.mqtt.client_id = str(value)
            raise OSError("connection refused after setting identity")
        h.backend.set_client_id = reconnect
        h.apply_robot_profile("Hero", close_home=False, announce=False)
        self.assertEqual((h.state.robot_id, h.backend.mqtt.client_id, h.state.alliance_color), (101, "101", "Blue"))
        self.assertEqual(h.registry.get("MatchMode").get_setting("Alliance").value, "Blue")
        self.assertEqual(h.events[-2], "lock")

    def test_running_identity_change_latches_estop_before_new_identity(self):
        h = Harness()
        h.backend.mqtt._started = True
        h.registry.get("AllianceProfile").get_setting("Side").value = "Blue"
        h.apply_robot_profile("Infantry1", close_home=False, announce=False)
        self.assertLess(h.events.index("estop"), h.events.index(("identity", 103, 3)))
        self.assertEqual(h.state.robot_id, 103)

    def test_window_reserved_keys_are_not_offered_as_control_binds(self):
        from rm_client.model import CONTROL_BIND_OPTIONS, CONTROL_RESERVED_KEYS

        self.assertFalse(CONTROL_RESERVED_KEYS & {key.lower() for key in CONTROL_BIND_OPTIONS})

    def test_lock_hook_discards_last_command_before_releasing_writers(self):
        from PySide6.QtCore import QRectF
        from rm_client.ui.polar.mixins.input_dispatch_mixin import _cancel_economy_hold_input
        cls = extract_class(
            "rm_client/ui/polar/mixins/input_dispatch_mixin.py", "InputDispatchMixin",
            {"_clear_control_inputs_for_lock"},
            {"_cancel_economy_hold_input": _cancel_economy_hold_input, "logging": logging},
        )
        obj = cls()
        from rm_client.ui.polar.pointer_interaction import PointerInteraction
        obj.pointer_interaction = PointerInteraction()
        seen = []
        obj.economy_hold_key = "pending-offer"
        obj._economy_hold_input_owner = ("mouse", 1)
        obj.economy_hold_rect = QRectF(1, 2, 30, 40)
        retired_at_reset = []
        def reset_inputs():
            retired_at_reset.append((obj.economy_hold_key, obj._economy_hold_input_owner,
                                     obj.economy_hold_rect.isNull()))
        obj.control = NS(last_command=NS(vx=1.0), reset_inputs=reset_inputs, clear_pending=lambda: None)
        obj.control.release_direct_dt7 = lambda: seen.append(obj.control.last_command)
        obj.control.release_custom_controller = lambda: seen.append(obj.control.last_command)
        obj._clear_control_inputs_for_lock()
        self.assertEqual(seen, [None, None])
        self.assertEqual(retired_at_reset, [("", None, True)])
        self.assertIsNone(obj._economy_qualification_token)

    def test_module_installation_fault_is_not_online(self):
        cls = extract_class("rm_client/model.py", "RobotState", {"robot_module_indicator_state"})
        state = cls()
        state.MODULE_INDICATOR_FIELDS = (("ARM", ("armor",)), ("SHT", ("small_shooter", "big_shooter")))
        for status, expected in ((0, False), (1, True), (2, False), (None, False), ("2", False)):
            state.robot_module_status = {"armor": status, "big_shooter": 1}
            self.assertEqual(state.robot_module_indicator_state(), (("ARM", expected), ("SHT", True)))
        state.robot_module_status = {}
        self.assertEqual(state.robot_module_indicator_state(), ())

    def test_gold_uses_unified_economy_not_stale_polar_field(self):
        cls = extract_class("rm_client/ui/minecraft_hud.py", "MinecraftCombatHudRenderer", {"scoreboard_lines"}, {"datetime": datetime, "QColor": lambda value: value})
        cls._team_rows = lambda _: ((), ())
        lines = cls.scoreboard_lines(NS(remaining_economy=500, polar_gold_coin=9, polar_link_active=False))
        self.assertIn("§fGold: §6500", [line for line, _ in lines])

    def test_held_observation_survives_tongji_adapter(self):
        tree = ast.parse((ROOT / "rm_client/vision.py").read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in {"ArmorObservation", "_observation_from_tongji"}]
        scope = {"dataclass": dataclass}
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), "vision-adapter", "exec"), scope)
        self.assertTrue(scope["_observation_from_tongji"]({"held": True}).held)
        self.assertFalse(scope["_observation_from_tongji"]({}).held)


class WindowPathTests(unittest.TestCase):
    def make_window(self):
        phases = NS(CLOSING="closing", CLOSED="closed")
        class Base:
            def closeEvent(self, _event):
                self.calls.append("super")
        cls = extract_class("rm_client/ui/polar_app.py", "PolarRMWindow", {"paintGL", "paint_gpu_frame", "_paint_gpu_frame_cached", "_paint_compatibility_frame", "closeEvent", "_finish_window_shutdown", "_window_close_finalized", "toast"}, {"time": time, "math": math, "EconomyScenePhase": phases, "FinalContextPorts": FinalContextPorts, "OwnerAction": OwnerAction, "OwnerActionFailure": OwnerActionFailure, "run_owner_actions": run_owner_actions}, base=Base)
        economy = extract_class("rm_client/ui/polar/mixins/economy_mixin.py", "EconomyMixin", {"_finalize_economy_restore"}, {"EconomyScenePhase": phases, "OverlayKind": NS(ECONOMY="economy")})
        cls._finalize_economy_restore = economy._finalize_economy_restore
        window = cls()
        window.calls = []
        window._window_lifecycle = WindowLifecycleController(None)
        window._window_lifecycle.ready_to_close = True
        window._window_close_finalized = False
        window.gpu_compositor = NS(available=False)
        window._gpu_warm_pending = False
        window._economy_restore_pending_generation = 2
        window._economy_generation = 2
        window._economy_closing_generation = 2
        window.economy_phase = "closing"
        window.economy_island = NS(progress=0.0, target=0.0, velocity=0.0)
        window._economy_pending_overlay = None
        window.home_visible = False
        window.paint_overlay = lambda *_args, **_kw: window.calls.append("paint") or True
        window.invalidate_transient_overlay_scene = lambda: window.calls.append("invalidate")
        window.overlay_coordinator = NS(close_modal=lambda _: window.calls.append("modal-close"))
        window.frame_scheduler = NS(force=lambda: None)
        now = time.perf_counter()
        window._frame_telemetry = FrameTelemetry(last_presented_at=now, last_reported_at=now)
        window.state = NS(push_event=lambda message: window.calls.append(("event", message)))
        window._show_shutdown_cleanup_warning = lambda message: window.calls.append(("warning", message))
        return window

    def test_cpu_fallback_closes_modal_through_actual_paintgl_path(self):
        w = self.make_window()
        w.paintGL()
        self.assertEqual(w.economy_phase, "closed")
        self.assertEqual(w.calls, ["paint", "invalidate", "modal-close"])

    def test_failed_cpu_paint_retains_modal(self):
        w = self.make_window()
        w.paint_overlay = lambda *_args, **_kwargs: False
        w.paintGL()
        self.assertEqual(w.economy_phase, "closing")
        self.assertNotIn("modal-close", w.calls)

    def test_stale_generation_cannot_close_reopened_modal(self):
        w = self.make_window()
        def paint(*_args, **_kwargs):
            w._economy_generation = 3
            return True
        w.paint_overlay = paint
        w.paintGL()
        self.assertEqual(w.economy_phase, "closing")
        self.assertNotIn("modal-close", w.calls)

    def test_warning_toast_has_supported_signature(self):
        w = self.make_window()
        w.toasts = []
        w.toast("recoverable", kind="warning")
        self.assertEqual(w.toasts[0]["kind"], "warning")

    def test_save_and_gpu_errors_never_skip_remaining_owner_cleanup(self):
        w = self.make_window()
        def action(name, fail=False):
            def invoke(*_args, **_kwargs):
                w.calls.append(name)
                if fail:
                    raise OSError(name)
            return invoke
        w.timer = NS(stop=action("timer"))
        w._gpu_prewarm_timer = NS(stop=action("warmup timer"))
        w.registry = object()
        w.control = NS(release_all=action("release"))
        w.backend = NS(mqtt=NS(mock_output=True), lock_control=action("lock"), trigger_estop=action("estop"), shutdown=action("shutdown", True))
        w.flush_auto_save = action("save", True)
        w.context = lambda: object()
        w.makeCurrent = action("makeCurrent")
        w.doneCurrent = action("doneCurrent")
        for name in ("clickgui_gpu", "vision_wire_gpu", "island_icon_gpu", "quick_action_wheel_gpu", "gpu_compositor", "startup_gpu", "hud_video_gpu"):
            setattr(w, name, NS(release=action(name, name == "clickgui_gpu")))
        w.startup_wallpaper = NS(release=action("wallpaper"))
        for name in ("camera_worker", "local_vision", "tongji_receiver", "tongji_bridge"):
            setattr(w, name, NS(stop=action(name)))
        w.closeEvent(None)
        self.assertTrue(w._window_close_finalized)
        self.assertNotIn("shutdown", w.calls, "native owners already completed before this accepted close")
        for expected in ("timer", "warmup timer", "save", "island_icon_gpu", "quick_action_wheel_gpu", "doneCurrent", "super"):
            self.assertIn(expected, w.calls)
        warnings = [entry[1] for entry in w.calls if isinstance(entry, tuple) and entry[0] == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("configuration: OSError: save", warnings[0])
        self.assertIn("clickgui_gpu: OSError: clickgui_gpu", warnings[0])
        self.assertEqual(w.calls[-1], "super")


if __name__ == "__main__":
    unittest.main(verbosity=2)
