"""A single wheel model across stable wire IDs, legacy projections and runtime."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.config_store import ClientConfigStore, apply_registry_config, export_client_config
from rm_client.configuration.wheel import (
    SLOT_NAMES, WheelConfiguration, restore_wheel_profile,
    sync_module_from_wheel, sync_wheel_from_module,
)
from rm_client.model import build_default_registry
from rm_client.quick_actions import ACTION_LABELS, DEFAULT_ACTIONS
from rm_client.ui.quick_action_wheel import QuickActionWheelState


def export(registry, wheel=None, panels=None):
    return export_client_config(registry, gui_mode="JelloSigma", selected_category="System",
                                selected_module="QuickActionWheel", module_expanded=set(),
                                old_panels=panels or {}, new_panels={}, dropdown_panels={},
                                quick_action_wheel=wheel)


def module_values(registry):
    module = registry.get("QuickActionWheel")
    return {setting.name: setting.value for setting in module.settings}


class QuickActionConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_default_registry()
        self.module = self.registry.get("QuickActionWheel")
        self.wheel = QuickActionWheelState()

    def restore(self, data):
        apply_registry_config(self.registry, data)
        restore_wheel_profile(self.module, self.wheel, data.get("quick_action_wheel", {}))

    def assert_consistent(self):
        self.assertEqual(WheelConfiguration().overlay(self.wheel.export_config()).setting_values(),
                         module_values(self.registry))

    def test_legacy_explicit_top_level_values_win_without_mutating_input(self):
        data = {"modules": {"QuickActionWheel": {"settings": {
            "Sectors": "4", "Radius": 90, "Up": "Ammo",
        }}}, "quick_action_wheel": {"sectors": 6, "radius": 144, "actions": ["clickgui", *DEFAULT_ACTIONS[1:]]}}
        previous = deepcopy(data)
        self.restore(data)
        self.assertEqual(data, previous)
        self.assertEqual((self.wheel.sector_count, self.wheel.radius, self.wheel.actions[0]), (6, 144, "clickgui"))
        self.assert_consistent()

    def test_partial_legacy_payload_keeps_saved_module_choices(self):
        self.restore({"modules": {"QuickActionWheel": {"settings": {
            "Sectors": "4", "Radius": 146, "Show Labels": False, "Up": "Ammo",
        }}}, "quick_action_wheel": {"labels": ["custom"]}})
        self.assertEqual((self.wheel.sector_count, self.wheel.radius, self.wheel.show_labels), (4, 146, False))
        self.assertEqual(self.wheel.actions[0], "economy_ammo")
        self.assertEqual(self.wheel.custom_labels[0], "custom")
        self.assert_consistent()

    def test_module_only_and_wheel_only_profiles_both_restore(self):
        self.restore({"modules": {"QuickActionWheel": {"settings": {"Sectors": "4", "Radius": 136}}}})
        self.assertEqual((self.wheel.sector_count, self.wheel.radius), (4, 136))
        self.assert_consistent()
        self.restore({"quick_action_wheel": {"sectors": 6, "radius": 150}})
        self.assertEqual((self.wheel.sector_count, self.wheel.radius), (6, 150))
        self.assert_consistent()

    def test_snapshot_mirrors_runtime_authority_into_both_legacy_representations(self):
        self.wheel.configure(sector_count=6, radius=132, custom_labels=["Economy"], custom_icons=["EC"])
        before = module_values(self.registry)
        data = export(self.registry, self.wheel.export_config())
        self.assertEqual(module_values(self.registry), before)  # Export never mutates live state.
        projected = data["modules"]["QuickActionWheel"]["settings"]
        self.assertEqual(projected, WheelConfiguration().overlay(data["quick_action_wheel"]).setting_values())
        with tempfile.TemporaryDirectory() as directory:
            store = ClientConfigStore(Path(directory))
            store.save("roundtrip", data)
            self.restore(store.load("roundtrip"))
        self.assertEqual(self.wheel.sector_count, 6)
        self.assertEqual(self.wheel.custom_labels[0], "Economy")
        self.assertEqual(self.wheel.custom_icons[0], "EC")
        self.assert_consistent()

    def test_ui_setting_sync_preserves_custom_text_and_effective_geometry(self):
        self.wheel.configure(custom_labels=["label"], custom_icons=["icon"])
        self.module.get_setting("Radius").value = 72
        self.module.get_setting("Dead Zone").value = 96
        self.module.get_setting("Up").value = "Ammo"
        sync_wheel_from_module(self.module, self.wheel)
        self.assertAlmostEqual(self.wheel.dead_zone, 51.84)
        self.assertEqual((self.wheel.custom_labels[0], self.wheel.custom_icons[0]), ("label", "icon"))
        self.assertEqual(self.wheel.actions[0], "economy_ammo")
        self.assert_consistent()

    def test_action_ids_do_not_depend_on_localized_display_labels(self):
        self.restore({"quick_action_wheel": {"actions": ["clickgui", *DEFAULT_ACTIONS[1:]], "labels": ["界面"]}})
        self.assertEqual(self.wheel.actions[0], "clickgui")
        self.assertEqual(self.module.get_setting("Up").value, "ClickGUI")
        self.assertEqual(self.wheel.custom_labels[0], "界面")
        self.assertEqual(tuple(ACTION_LABELS), DEFAULT_ACTIONS)
        self.assertEqual(len(SLOT_NAMES), 8)

    def test_reverse_adapter_uses_the_same_projection(self):
        self.wheel.configure(sector_count=4, radius=140, actions=["match_chat"])
        sync_module_from_wheel(self.module, self.wheel)
        self.assert_consistent()

    def test_snapshot_detaches_nested_layout_containers(self):
        panels = {"System": {"x": 10, "y": 20}}
        data = export(self.registry, panels=panels)
        panels["System"]["x"] = 90
        self.assertEqual(data["old_panels"]["System"]["x"], 10)
        data["old_panels"]["System"]["y"] = 80
        self.assertEqual(panels["System"]["y"], 20)

    def test_malformed_wheel_rejects_before_an_earlier_module_can_change(self):
        before = export(self.registry)
        with self.assertRaises(ValueError):
            apply_registry_config(self.registry, {"modules": {"HUD": {"enabled": False}},
                                                 "quick_action_wheel": {"radius": float("nan")}})
        self.assertEqual(export(self.registry), before)

    def test_state_and_profile_normalization_share_the_same_rules(self):
        payload = {"sectors": 7, "radius": 999, "dead_zone": 999, "actions": ["unknown"],
                   "labels": ["x" * 30], "icons": ["y" * 8], "label_style": "unknown", "icon_style": "unknown"}
        self.wheel.load_config(payload)
        self.assertEqual(self.wheel.export_config(), WheelConfiguration().overlay(payload).to_wire())
        self.assertEqual(self.wheel.sector_count, 6)
        self.assertEqual(self.wheel.custom_labels[0], "x" * 24)
        self.assertEqual(self.wheel.custom_icons[0], "y" * 4)


if __name__ == "__main__":
    unittest.main()
