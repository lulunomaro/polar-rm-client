"""New control shortcuts cannot silently shadow existing module shortcuts."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.configuration.preferences import prepare_preference_draft
from rm_client.control import KeyboardControlBridge
from rm_client.model import build_default_registry
from rm_client.ui.settings_application import preference_capabilities


class PreferenceBindingConflictTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_default_registry()
        self.control = KeyboardControlBridge()
        self.access = preference_capabilities(self.registry)
        prepared = self.prepare({}, {"TacticalMap": "F10"})
        for module, value in prepared.module_binds:
            module.keybind = value

    def prepare(self, values, module_binds=None):
        return prepare_preference_draft(self.registry, self.control.binds, values,
                                        self.access, module_binds=module_binds)

    def test_new_control_bind_rejects_existing_module_shortcut(self):
        for module_binds in (None, {m.name: m.keybind for m in self.registry.modules}):
            with self.subTest(module_binds=module_binds), self.assertRaisesRegex(ValueError, "TacticalMap"):
                self.prepare({("RobotLink", "Bind W"): "F10"}, module_binds)
        self.assertEqual(self.control.binds["W"], "W")
        self.assertEqual(self.registry.get("RobotLink").get_setting("Bind W").value, "W")

    def test_releasing_module_shortcut_in_same_draft_allows_control_assignment(self):
        for replacement in ("None", "F5"):
            prepared = self.prepare({("RobotLink", "Bind W"): "F10"}, {"TacticalMap": replacement})
            self.assertEqual(dict(prepared.control_binds)["W"], "F10")

    def test_existing_historical_overlaps_and_case_only_edits_remain_valid(self):
        prepared = self.prepare({("RobotLink", "Bind Q"): "q", ("RobotLink", "Bind SHIFT"): "shift"},
                                {m.name: m.keybind for m in self.registry.modules})
        self.assertEqual(dict(prepared.control_binds)["Q"], "Q")
        self.assertEqual(dict(prepared.control_binds)["SHIFT"], "Shift")

    def test_unoccupied_control_permutation_is_still_valid(self):
        prepared = self.prepare({("RobotLink", "Bind W"): "S", ("RobotLink", "Bind S"): "W"})
        self.assertEqual(dict(prepared.control_binds)["W"], "S")

    def test_control_and_module_can_exchange_shortcuts_in_one_draft(self):
        self.control.binds["W"] = "F5"
        prepared = self.prepare({("RobotLink", "Bind W"): "F10"}, {"TacticalMap": "F5"})
        self.assertEqual(dict(prepared.control_binds)["W"], "F10")
        self.assertEqual([(module.name, value) for module, value in prepared.module_binds],
                         [("TacticalMap", "F5")])

    def test_historically_shared_physical_key_can_move_between_control_slots(self):
        prepared = self.prepare({("RobotLink", "Bind A"): "D",
                                 ("RobotLink", "Bind D"): "Q",
                                 ("RobotLink", "Bind Q"): "A"})
        self.assertEqual(dict(prepared.control_binds)["D"], "Q")
        self.assertEqual(self.registry.get("QuickActionWheel").keybind, "Q")

    def test_reintroducing_a_released_shared_key_is_a_new_conflict(self):
        self.control.binds["Q"] = "None"
        with self.assertRaisesRegex(ValueError, "QuickActionWheel"):
            self.prepare({("RobotLink", "Bind D"): "Q"})


if __name__ == "__main__":
    unittest.main()
