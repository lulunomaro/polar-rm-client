"""Catalog inputs and ownership are independent of runtime state and devices."""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client import model, module_settings
from rm_client.module_catalog import CatalogChoices, assemble_registry, default_modules
from rm_client.student_ui import student_modules
from test_default_catalog_characterization import FIXTURE, canonical_defaults, snapshot


def choices():
    return CatalogChoices(
        scenario_modes=tuple(model.SCENARIO_MODES),
        match_modes=tuple(model.MATCH_MODE_KEYS),
        robot_roles=tuple(profile.key for profile in model.RMUC_ROBOTS),
        referee_profile=model.DEFAULT_PROFILE,
        referee_profiles=("fixture-current", "fixture-readonly"),
        angle_rx_ports=("Auto", "Stub", "POLAR_2", "POLAR_2", "SECOND"),
        angle_tx_ports=("Auto", "Stub", "POLAR_3", "POLAR_3", "SECOND"),
        robot_ports=("Auto", "Stub", "POLAR_4", "POLAR_4", "SECOND"),
        dt7_ports=("Auto", "Stub", "DT7_5", "DT7_5", "SECOND"),
        cc_ports=("Auto", "Stub", "POLAR_6", "POLAR_6", "SECOND"),
    )


class CatalogBoundaryTests(unittest.TestCase):
    def test_pure_catalog_matches_all_frozen_defaults_without_discovery(self):
        baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with patch.object(model, "polar_port_choices", side_effect=AssertionError("device discovery")), \
                patch.object(model, "dt7_port_choices", side_effect=AssertionError("device discovery")), \
                patch.object(model, "supported_profiles", side_effect=AssertionError("profile discovery")):
            registry = assemble_registry(default_modules(choices()), student_modules())
        self.assertEqual(canonical_defaults(snapshot(registry)),
                         canonical_defaults(baseline["modules"]))

    def test_environment_candidates_are_explicit_and_leave_defaults_unchanged(self):
        supplied = replace(choices(), robot_ports=("Z", "A", "Z"), dt7_ports=(),
                           angle_rx_ports=("Auto", "P", "Stub", "P"),
                           referee_profiles=("unavailable-profile",))
        registry = assemble_registry(default_modules(supplied))
        self.assertEqual(registry.get("RobotLink").get_setting("Port").options, ["Z", "A", "Z"])
        self.assertEqual(registry.get("RobotLink").get_setting("Port").value, "Auto")
        self.assertEqual(registry.get("RobotLink").get_setting("DT7 Port").options, [])
        self.assertEqual(registry.get("ControllerAngles").get_setting("Angle RX Port").options,
                         ["Stub", "P", "P"])
        profile = registry.get("MQTTLink").get_setting("Referee Profile")
        self.assertEqual(profile.options, ["unavailable-profile"])
        self.assertEqual(profile.value, model.DEFAULT_PROFILE)
        with self.assertRaises(FrozenInstanceError):
            supplied.robot_ports = ("changed",)

    def test_assembly_does_not_rebind_or_mutate_extension_prototypes(self):
        setting = model.Setting("Draft", "mode", {"nested": [1]}, options=["A", "B"])
        prototype = model.Module("Extension", "Strategy", settings=[setting])
        original_registry = model.ModuleRegistry([prototype])
        hook = prototype._enabled_change_hook
        inputs = default_modules(choices())
        first = assemble_registry(inputs, [prototype])
        second = assemble_registry(inputs, [prototype])
        first_extension = first.get("Extension")
        first_extension.enabled = True
        first_extension.settings[0].value["nested"].append(2)
        first_extension.settings[0].options.append("C")
        self.assertIs(prototype._enabled_change_hook, hook)
        self.assertEqual(original_registry.enabled_revision, 0)
        self.assertFalse(prototype.enabled)
        self.assertEqual(setting.value, {"nested": [1]})
        self.assertEqual(setting.options, ["A", "B"])
        self.assertEqual(second.get("Extension").settings[0].value, {"nested": [1]})
        self.assertEqual(second.enabled_revision, 0)
        self.assertEqual(first.enabled_revision, 1)
        slots = [s for s in first.get("MinecraftCombatHUD").settings if s.name.startswith("Slot ")]
        self.assertTrue(all(s.options[-1] == "Extension" for s in slots))

    def test_duplicate_module_and_setting_lookup_semantics_are_preserved(self):
        first = model.Module("Duplicate", "System", settings=[
            model.bool_setting("Same", False), model.bool_setting("Same", True)])
        second = model.Module("Duplicate", "Combat")
        registry = assemble_registry([first, second])
        self.assertEqual([m.category for m in registry.modules], ["System", "Combat"])
        self.assertIs(registry.get("Duplicate"), registry.modules[1])
        self.assertFalse(registry.modules[0].get_setting("Same").value)

    def test_model_exports_are_identity_preserving_compatibility_aliases(self):
        names = (
            "Setting", "Module", "ModuleRegistry", "GuiModeManager", "bool_setting",
            "number_setting", "mode_setting", "action_setting", "action_group_setting",
            "normalize_gui_mode", "normalize_legacy_gui_mode", "CATEGORIES", "GUI_MODES",
            "CONTROL_BIND_OPTIONS", "CONTROL_SLOT_ORDER", "MODULE_BIND_OPTIONS",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(model, name), getattr(module_settings, name))

    def test_catalog_can_import_and_build_without_state_qt_or_device_layers(self):
        program = '''
import importlib.abc
import sys
class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("PySide6", "rm_client.model", "rm_client.backend", "rm_client.serial_ports", "rm_client.ui")
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("unexpected runtime import: " + fullname)
sys.meta_path.insert(0, BlockRuntime())
from rm_client.module_catalog import CatalogChoices, assemble_registry, default_modules
from rm_client.student_ui import student_modules
choices = CatalogChoices(("LabDebug",), ("RMUC7v7",), ("Infantry1",), "test", ("test",),
                         ("Stub",), ("Stub",), ("Auto",), ("Auto",), ("Auto",))
registry = assemble_registry(default_modules(choices), student_modules())
assert len(registry.modules) == 74
assert sum(len(module.settings) for module in registry.modules) == 470
print("PASS: standalone catalog without runtime, Qt, or device imports")
'''
        result = subprocess.run([sys.executable, "-c", program], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: standalone catalog", result.stdout)


if __name__ == "__main__":
    unittest.main()
