"""Frozen defaults and observable assembly behavior, recorded before extraction.

Normal tests only read the fixture. Explicit recording requires the exact
pre-extraction source digests and uses fake discovery, never a serial device.
"""
from contextlib import contextmanager
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client import model, student_ui

FIXTURE = ROOT / "tests/fixtures/default_catalog_baseline.json"
SOURCE_DIGESTS = {
    "rm_client/model.py": "d662ee61059837e73f6031d7d634ded4cdd6e2b2e726a2964fed4194d19081c4",
    "rm_client/student_ui.py": "4baa4a95c4bca57fac80470538764a2b7a99715e9fb934cc11a6bd701ebc5294",
    "rm_client/scenarios.py": "ab4c2fc487a1558a18e2f24554e53afec5e987f959a1ee2154f2934094ce6c3d",
    "rm_client/match_modes.py": "069e275d055b08a7ba33735b575814768bcb29845a92f52ba46bb0ac45bb1019",
    "rm_client/robot_profiles.py": "6519c60f875470b49405e871d04756d499b1e318d009e976af9988185f25b8f4",
}


@contextmanager
def fake_catalog_environment():
    calls = []
    student_factory = student_ui.student_modules

    def profiles():
        calls.append(["profiles", {}])
        return ("fixture-current", "fixture-readonly")

    def ports(kind, kwargs):
        calls.append([kind, kwargs])
        label = f"{kind.upper()}_{len(calls)}"
        return ["Auto", "Stub", label, label, "SECOND"]

    def students():
        calls.append(["student_modules", {}])
        return student_factory()

    with patch.object(model, "supported_profiles", side_effect=profiles), \
            patch.object(model, "polar_port_choices", side_effect=lambda **kw: ports("polar", kw)), \
            patch.object(model, "dt7_port_choices", side_effect=lambda **kw: ports("dt7", kw)), \
            patch.object(student_ui, "student_modules", side_effect=students):
        yield calls


def snapshot(registry):
    result = []
    for module in registry.modules:
        record = {field.name: getattr(module, field.name) for field in fields(module)
                  if field.name not in {"settings", "_enabled_change_hook"}}
        record["settings"] = [
            {field.name: getattr(setting, field.name) for field in fields(setting)}
            for setting in module.settings
        ]
        result.append(record)
    return result


def canonical_defaults(value):
    """Keep JSON scalar types distinct: Python equates True, 1, and 1.0."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def shared_option_ids(first, second):
    return [f"{left.name}.{setting.name}"
            for left, right in zip(first.modules, second.modules)
            for setting, counterpart in zip(left.settings, right.settings)
            if setting.options is counterpart.options]


def record_baseline():
    for relative, expected in SOURCE_DIGESTS.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"Baseline recording refused: {relative} is not the pre-extraction source")
    with fake_catalog_environment() as calls:
        registry = model.build_default_registry()
        observation = snapshot(registry)
        observed_calls = list(calls)
    with fake_catalog_environment():
        other = model.build_default_registry()
    metadata = {
        "source_sha256": SOURCE_DIGESTS,
        "scope": "All default dataclass fields and order; fake profile/port discovery; no hardware",
        "discovery_calls": observed_calls,
        "legacy_shared_options": shared_option_ids(registry, other),
    }
    # One complete setting per line is readable without a 7,000-line expansion.
    lines = [json.dumps(metadata, ensure_ascii=False, indent=2)[:-2] + ",", '  "modules": [']
    for index, module in enumerate(observation):
        header = {name: value for name, value in module.items() if name != "settings"}
        lines.append("    " + json.dumps(header, ensure_ascii=False)[:-1] + ', "settings": [')
        for offset, setting in enumerate(module["settings"]):
            comma = "," if offset + 1 < len(module["settings"]) else ""
            lines.append("      " + json.dumps(setting, ensure_ascii=False) + comma)
        lines.append("    ]}" + ("," if index + 1 < len(observation) else ""))
    lines.extend(["  ]", "}", ""])
    encoded = "\n".join(lines)
    json.loads(encoded)
    FIXTURE.write_text(encoded, encoding="utf-8")
    print(f"Recorded {len(observation)} modules and {sum(len(m['settings']) for m in observation)} settings")


class DefaultCatalogCharacterizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def build(self):
        with fake_catalog_environment() as calls:
            registry = model.build_default_registry()
        return registry, calls

    def test_all_default_fields_and_order_match_frozen_source(self):
        registry, _ = self.build()
        actual = snapshot(registry)
        self.assertEqual([m["name"] for m in actual], [m["name"] for m in self.baseline["modules"]])
        for expected, observed in zip(self.baseline["modules"], actual):
            with self.subTest(module=expected["name"]):
                self.assertEqual(canonical_defaults(observed), canonical_defaults(expected))

    def test_default_comparison_distinguishes_equal_python_scalar_types(self):
        values = ({"value": True}, {"value": 1}, {"value": 1.0})
        self.assertEqual(len({canonical_defaults(value) for value in values}), 3)

    def test_discovery_call_order_flags_and_counts_are_unchanged(self):
        _, calls = self.build()
        self.assertEqual(calls, self.baseline["discovery_calls"])

    def test_modules_settings_and_registry_hooks_are_per_instance(self):
        first, _ = self.build()
        second, _ = self.build()
        for left, right in zip(first.modules, second.modules):
            self.assertIsNot(left, right)
            self.assertIsNot(left.settings, right.settings)
            for setting, counterpart in zip(left.settings, right.settings):
                self.assertIsNot(setting, counterpart)
        first.modules[0].enabled = not first.modules[0].enabled
        self.assertEqual(first.enabled_revision, 1)
        self.assertEqual(second.enabled_revision, 0)

    def test_recorded_legacy_aliases_are_now_owned_by_each_registry(self):
        first, _ = self.build()
        second, _ = self.build()
        self.assertEqual(len(self.baseline["legacy_shared_options"]), 21)
        self.assertEqual(shared_option_ids(first, second), [])
        settings = [setting for module in first.modules for setting in module.settings]
        self.assertEqual(len({id(setting.options) for setting in settings}), len(settings))
        for setting in settings:
            setting.options.append("mutated first registry only")
        third, _ = self.build()
        self.assertEqual(canonical_defaults(snapshot(second)),
                         canonical_defaults(self.baseline["modules"]))
        self.assertEqual(canonical_defaults(snapshot(third)),
                         canonical_defaults(self.baseline["modules"]))

    def test_function_bar_choices_follow_complete_catalog_order(self):
        registry, _ = self.build()
        module = registry.get("MinecraftCombatHUD")
        expected = ["Empty", *(item.name for item in registry.modules if item is not module)]
        slots = [setting for setting in module.settings if setting.name.startswith("Slot ")]
        self.assertTrue(slots)
        for setting in slots:
            self.assertEqual(setting.options, expected)
        self.assertEqual(len({id(setting.options) for setting in slots}), len(slots))

    def test_setting_helpers_keep_mutation_and_formatting_semantics(self):
        numeric = model.number_setting("number", 5, 0, 10, 2, "%")
        self.assertEqual(numeric.ratio(), 0.5)
        numeric.set_from_ratio(0.7)
        self.assertEqual((numeric.value, numeric.display_value()), (8, "8%"))
        numeric.set_from_ratio(2)
        self.assertEqual(numeric.value, 10)
        options = ["A", "B", "C"]
        choice = model.mode_setting("mode", "missing", options)
        self.assertIs(choice.options, options)
        choice.cycle(-1)
        self.assertEqual(choice.value, "C")
        group = model.action_group_setting("group", options)
        self.assertIsNot(group.options, options)
        self.assertIsNone(group.value)
        self.assertEqual(model.bool_setting("bool", False).display_value(), "OFF")
        self.assertEqual(model.action_setting("Run").value, "Run")

    def test_registry_notifications_preserve_source_order_and_direct_writes(self):
        first = model.Module("First", "System")
        second = model.Module("Second", "System", enabled=True)
        registry = model.ModuleRegistry([first, second])
        events = []
        registry.add_toggle_listener(lambda module, enabled, source: events.append(
            (module.name, enabled, source, registry.enabled_revision)))
        first.enabled = True
        self.assertEqual(events, [])
        registry.toggle_module("First", "test")
        self.assertEqual(events, [("First", False, "test", 2)])
        self.assertEqual(registry.enabled_modules(), [second])
        self.assertIsNone(registry.toggle_module("missing"))


if __name__ == "__main__":
    if "--record-baseline" in sys.argv:
        record_baseline()
    else:
        unittest.main()
