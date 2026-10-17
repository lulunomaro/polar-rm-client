"""Qt-free profile boundaries: stable IDs, migrations, validation, and snapshots.

Synthetic registries/profiles only. No hardware or network authorization is
exercised or implied by these checks.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.config_store import (
    CONFIG_VERSION, ClientConfigStore, apply_registry_config, export_client_config,
    validate_client_config,
)
from rm_client.configuration.migrations import migrated_modules
from rm_client.configuration.registry import RegistrySnapshot, SettingId, setting_identity
from rm_client.configuration.validation import ConfigurationValueError
from rm_client.configuration.transactions import ConfigurationApplyError, apply_configuration_transaction
from rm_client.model import build_default_registry


def exported(registry):
    return export_client_config(
        registry, gui_mode="JelloSigma", selected_category="System",
        selected_module="ConfigManager", module_expanded=set(), old_panels={},
        new_panels={}, dropdown_panels={},
    )


class ProfileBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_default_registry()

    def test_validation_rejects_complete_document_before_mutating_values(self):
        before = exported(self.registry)
        candidate = {
            "modules": {
                "HUD": {"settings": {"Right Display": "TacticalMapHud"}},
                "RobotLink": {"settings": {"Allow Drive": "false"}},
            },
        }
        with self.assertRaises(ConfigurationValueError) as failure:
            apply_registry_config(self.registry, candidate)
        self.assertEqual(failure.exception.path, "RobotLink.Allow Drive")
        self.assertEqual(exported(self.registry), before)

    def test_unknown_fields_are_compatible_but_nonfinite_json_is_rejected(self):
        candidate = {"future_metadata": {"source": "synthetic"},
                     "modules": {"FutureModule": {"settings": {"New Choice": "future"}}}}
        apply_registry_config(self.registry, candidate)
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_client_config({"future_metadata": value})

    def test_migrations_do_not_mutate_the_source_document(self):
        candidate = {
            "modules": {
                "StartupVisual": {"settings": {"Wallpaper Profile": "TexasMP4"}},
                "MatchMode": {"settings": {"Alliance": "Blue"}},
                "EliminationFX": {"settings": {"Style": "Valorant", "Variant": "Default",
                                                "Anchor Policy": "Safe Area", "Volume": 43}},
            },
        }
        before = deepcopy(candidate)
        modules = migrated_modules(candidate)
        self.assertEqual(candidate, before)
        self.assertEqual(modules["StartupVisual"]["settings"]["Wallpaper Profile"], "BlackHole")
        self.assertEqual(modules["AllianceProfile"]["settings"]["Side"], "Blue")
        self.assertEqual(modules["EliminationFX"]["settings"]["Preset"], "Valorant Prime")
        self.assertEqual(modules["EliminationFX"]["settings"]["Sound Volume"], 43)
        self.assertEqual(modules["EliminationFX"]["settings"]["X Offset"], 0)
        modules["EliminationFX"]["settings"]["Volume"] = 10
        self.assertEqual(candidate, before)

    def test_legacy_alliance_keeps_other_explicit_profile_fields(self):
        candidate = {"modules": {
            "MatchMode": {"settings": {"Alliance": "Blue"}},
            "AllianceProfile": {"enabled": False, "keybind": "F10", "settings": {"Future": "keep"}},
        }}
        migrated = migrated_modules(candidate)["AllianceProfile"]
        self.assertEqual(migrated, {"enabled": False, "keybind": "F10",
                                    "settings": {"Future": "keep", "Side": "Blue"}})

    def test_explicit_alliance_wins_over_legacy_side(self):
        apply_registry_config(self.registry, {"modules": {
            "MatchMode": {"settings": {"Alliance": "Blue"}},
            "AllianceProfile": {"settings": {"Side": "Red"}},
        }})
        self.assertEqual(self.registry.get("AllianceProfile").get_setting("Side").value, "Red")
        self.assertEqual(self.registry.get("MatchMode").get_setting("Alliance").value, "Red")

    def test_mode_aliases_are_wire_ids_and_unknown_referee_profile_survives(self):
        apply_registry_config(self.registry, {"modules": {
            "HUD": {"settings": {"ModuleList Surface": "ReGlass", "ModuleList Style": "DebugWhiteList"}},
            "TacticalMap": {"settings": {"Status Display": "ReGlassCards"}},
            "MQTTLink": {"settings": {"Referee Profile": "unknown-future-year"}},
            "AudioFeedback": {"settings": {"Sound Theme": "Vape"}},
        }})
        self.assertEqual(self.registry.get("HUD").get_setting("ModuleList Surface").value, "LiquidGlass")
        self.assertEqual(self.registry.get("HUD").get_setting("ModuleList Style").value, "JelloActiveMods")
        self.assertEqual(self.registry.get("TacticalMap").get_setting("Status Display").value, "LiquidCards")
        self.assertEqual(self.registry.get("MQTTLink").get_setting("Referee Profile").value, "unknown-future-year")
        self.assertEqual(self.registry.get("AudioFeedback").get_setting("Sound Theme").value, "Polar UI")

    def test_invalid_migrated_field_is_rejected_before_any_prior_module_changes(self):
        before = exported(self.registry)
        candidate = {"modules": {
            "HUD": {"enabled": not self.registry.get("HUD").enabled},
            "EliminationFX": {"settings": {"Volume": "bad legacy number"}},
        }}
        for operation in (validate_client_config, apply_registry_config):
            with self.subTest(operation=operation.__name__), self.assertRaises(ConfigurationValueError):
                if operation is validate_client_config:
                    operation(candidate, self.registry)
                else:
                    operation(self.registry, candidate)
            self.assertEqual(exported(self.registry), before)

    def test_absent_modules_is_noop_but_explicit_empty_modules_syncs_legacy_side(self):
        legacy = self.registry.get("MatchMode").get_setting("Alliance")
        legacy.value = "Blue"
        apply_registry_config(self.registry, {"version": 1})
        self.assertEqual(legacy.value, "Blue")
        apply_registry_config(self.registry, {"modules": {}})
        self.assertEqual(legacy.value, "Red")

    def test_save_fresh_load_preserves_profile_identity_and_schema(self):
        self.registry.get("ConfigManager").get_setting("Profile").value = "other"
        self.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 1.75
        payload = exported(self.registry)
        with tempfile.TemporaryDirectory() as directory:
            store = ClientConfigStore(Path(directory))
            path = store.save("training", payload)
            data = store.load("training")
            self.assertEqual(data, json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual(data["version"], CONFIG_VERSION)
            self.assertEqual(data["profile"], "training")
            self.assertNotIn("Profile", data["modules"]["ConfigManager"]["settings"])
            fresh = build_default_registry()
            apply_registry_config(fresh, data)
        self.assertEqual(fresh.get("ConfigManager").get_setting("Profile").value, "default")
        self.assertEqual(fresh.get("RobotLink").get_setting("Mouse Sensitivity").value, 1.75)


class RegistrySnapshotTests(unittest.TestCase):
    def test_restore_covers_options_shortcuts_and_values_without_toggle_listeners(self):
        registry = build_default_registry()
        module = registry.get("RobotProfile")
        role = module.get_setting("Role")
        before = exported(registry)
        options = list(role.options)
        snapshot = RegistrySnapshot.capture(registry)
        notifications = []
        registry.add_toggle_listener(lambda *event: notifications.append(event))
        module.enabled = not module.enabled
        module.keybind = "F10"
        role.value = "changed"
        role.options = ["changed"]
        snapshot.restore()
        self.assertEqual(exported(registry), before)
        self.assertEqual(role.options, options)
        self.assertEqual(notifications, [])
        role.options.append("later")
        snapshot.restore()
        self.assertEqual(role.options, options)

    def test_restore_does_not_call_module_enable_or_disable_hooks(self):
        registry = build_default_registry()
        module = registry.get("HUD")
        commands = []
        module.on_enable = lambda: commands.append("enable")
        module.on_disable = lambda: commands.append("disable")
        snapshot = RegistrySnapshot.capture(registry)
        before_revision = registry.enabled_revision
        module.enabled = not module.enabled
        snapshot.restore()
        self.assertEqual(commands, [])
        self.assertGreater(registry.enabled_revision, before_revision)

    def test_setting_identity_requires_the_registered_instance(self):
        registry = build_default_registry()
        setting = registry.get("RobotLink").get_setting("Mouse Sensitivity")
        self.assertEqual(setting_identity(registry, setting), SettingId("RobotLink", "Mouse Sensitivity"))
        self.assertIsNone(setting_identity(registry, deepcopy(setting)))


class ConfigurationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_default_registry()
        self.setting = self.registry.get("HUD").get_setting("Right Display")
        self.previous = self.setting.value
        self.events = []

    def apply(self, update, restore=lambda: None, fail_closed=lambda: None, quiesce=lambda: None, participants=()):
        apply_configuration_transaction(
            self.registry, quiesce=quiesce, update=update,
            restore_runtime=restore, fail_closed=fail_closed, participants=participants,
        )

    def test_success_does_not_call_rollback_or_estop(self):
        self.apply(lambda: setattr(self.setting, "value", "TacticalMapHud"),
                   restore=lambda: self.fail("unexpected rollback"),
                   fail_closed=lambda: self.fail("unexpected estop"))
        self.assertEqual(self.setting.value, "TacticalMapHud")

    def test_failure_restores_values_before_runtime_and_again_after_runtime(self):
        def update():
            self.setting.value = "TacticalMapHud"
            raise OSError("unavailable driver")
        def restore():
            self.assertEqual(self.setting.value, self.previous)
            self.setting.value = "runtime normalized this"
            self.events.append("restore")
        with self.assertRaises(ConfigurationApplyError) as caught:
            self.apply(update, restore=restore, fail_closed=lambda: self.events.append("estop"))
        self.assertEqual(self.setting.value, self.previous)
        self.assertTrue(caught.exception.restored)
        self.assertIsInstance(caught.exception.cause, OSError)
        self.assertEqual(self.events, ["restore", "estop"])

    def test_rollback_error_does_not_skip_exact_values_or_fail_closed_cleanup(self):
        def update():
            self.setting.value = "changed"
            raise OSError("apply")
        def restore():
            self.setting.value = "partial rollback"
            raise OSError("restore")
        with self.assertRaises(ConfigurationApplyError) as caught:
            self.apply(update, restore=restore, fail_closed=lambda: self.events.append("estop"))
        self.assertEqual(self.setting.value, self.previous)
        self.assertFalse(caught.exception.restored)
        self.assertEqual(str(caught.exception.restore_errors[0]), "restore")
        self.assertEqual(self.events, ["estop"])

    def test_quiesce_and_safety_failures_are_reported_separately(self):
        def quiesce():
            raise OSError("neutralization failed")
        def fail_closed():
            raise OSError("E-stop transport failed")
        with self.assertRaises(ConfigurationApplyError) as caught:
            self.apply(lambda: self.fail("update after failed quiesce"),
                       quiesce=quiesce, fail_closed=fail_closed)
        self.assertEqual(str(caught.exception.cause), "neutralization failed")
        self.assertEqual(str(caught.exception.safety_error), "E-stop transport failed")

    def test_participants_commit_after_model_and_roll_back_in_reverse_order(self):
        def commit_second():
            self.events.append("commit second")
            raise OSError("partial second participant")
        first = SimpleNamespace(commit=lambda: self.events.append("commit first"),
                                rollback=lambda: self.events.append("restore first"))
        second = SimpleNamespace(commit=commit_second,
                                 rollback=lambda: self.events.append("restore second"))
        with self.assertRaises(ConfigurationApplyError) as caught:
            self.apply(lambda: self.events.append("model"),
                       restore=lambda: self.events.append("runtime"),
                       fail_closed=lambda: self.events.append("estop"), participants=(first, second))
        self.assertTrue(caught.exception.restored)
        self.assertEqual(self.events, ["model", "commit first", "commit second", "restore second",
                                       "restore first", "runtime", "estop"])

    def test_participant_restore_failure_cannot_skip_runtime_or_safety(self):
        def fail_restore():
            self.events.append("participant")
            raise OSError("participant restore failed")
        def fail_commit():
            raise OSError("commit failed")
        participant = SimpleNamespace(commit=fail_commit, rollback=fail_restore)
        with self.assertRaises(ConfigurationApplyError) as caught:
            self.apply(lambda: None, restore=lambda: self.events.append("runtime"),
                       fail_closed=lambda: self.events.append("estop"), participants=(participant,))
        self.assertFalse(caught.exception.restored)
        self.assertEqual(str(caught.exception.restore_errors[0]), "participant restore failed")
        self.assertEqual(self.events, ["participant", "runtime", "estop"])


if __name__ == "__main__":
    unittest.main()
