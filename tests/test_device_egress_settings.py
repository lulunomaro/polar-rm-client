"""Device permission/endpoint snapshots do not depend on live settings or I/O."""
from dataclasses import FrozenInstanceError
import itertools
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.device_egress_settings import (
    CustomControllerEndpoint, CustomControllerSettings, DeviceEgressSettings,
    Dt7EgressSettings, Dt7Endpoint, EgressPermissions,
)


class DeviceEgressSettingsTests(unittest.TestCase):
    def test_permission_truth_table_is_pure(self):
        for enabled, allow, estopped in itertools.product((False, True), repeat=3):
            decision = EgressPermissions(enabled, allow, estopped)
            self.assertEqual(decision.may_drive, enabled and allow and not estopped)

    def test_capture_detaches_scalar_values_from_live_settings_and_module(self):
        values = {name: SimpleNamespace(value=value) for name, value in {
            "Direct DT7 Egress": True, "DT7 Port": "DT7", "DT7 Real TX": True,
            "Allow Drive": True, "Custom Controller Egress": True, "CC Port": "CC",
            "CC Hz": "12.5", "CC Mock Writes": False, "Allow Spin Key": True,
            "DT7 Allow Fire Keys": True,
        }.items()}
        module = SimpleNamespace(enabled=True, get_setting=values.get)
        settings = DeviceEgressSettings.capture(SimpleNamespace(get=lambda name: module))
        module.enabled = False
        for setting in values.values():
            setting.value = None
        self.assertTrue(settings.module_enabled)
        self.assertTrue(settings.allow_drive_value)
        self.assertEqual(settings.dt7.endpoint(), Dt7Endpoint("DT7", True))
        self.assertEqual(settings.cc.endpoint(), CustomControllerEndpoint("CC", 12.5, False))
        self.assertTrue(settings.dt7.requested and settings.dt7.spin_allowed and settings.dt7.fire_allowed)
        self.assertTrue(settings.cc.requested and settings.cc.spin_allowed)

    def test_missing_registry_and_settings_keep_safe_defaults(self):
        settings = DeviceEgressSettings.capture(None)
        self.assertFalse(settings.module_enabled or settings.allow_drive_value)
        self.assertFalse(settings.dt7.requested or settings.cc.requested)
        self.assertEqual(settings.dt7.endpoint(), Dt7Endpoint("Auto", False))
        self.assertEqual(settings.cc.endpoint(), CustomControllerEndpoint("Auto", 30.0, True))
        empty = SimpleNamespace(enabled=True, get_setting=lambda _: None)
        settings = DeviceEgressSettings.capture(SimpleNamespace(get=lambda _: empty))
        self.assertTrue(settings.module_enabled)
        self.assertFalse(settings.allow_drive_value)

    def test_endpoint_validation_stays_lazy_and_preserves_falsy_defaults(self):
        settings = CustomControllerSettings(hz_value="bad")
        self.assertFalse(settings.requested)
        self.assertEqual(settings.conflict_port, "Auto")
        with self.assertRaises(ValueError):
            settings.endpoint()
        for value in (None, "", 0, False):
            self.assertEqual(CustomControllerSettings(hz_value=value).endpoint().max_hz, 30.0)
            self.assertEqual(Dt7EgressSettings(port_value=value).endpoint().port, "Auto")
        self.assertEqual(CustomControllerSettings(hz_value="0").endpoint().max_hz, 0.0)
        self.assertEqual(CustomControllerSettings(port_value=None).conflict_port, "None")
        self.assertEqual(CustomControllerSettings(port_value=None).endpoint().port, "Auto")
        self.assertEqual(CustomControllerSettings(port_value=" CC ").endpoint().port, " CC ")

    def test_blocked_replacement_does_not_validate_frequency_before_old_neutral(self):
        from test_device_egress_characterization import cases, observe
        case = cases()["cc_conflict_failed_neutral"]
        case["settings"]["CC Hz"] = "invalid, but replacement is blocked"
        observed = observe(case)
        self.assertEqual(observed["errors"], [])
        self.assertIn(["old_cc.release"], observed["events"])
        self.assertFalse(any(event[0] == "factory.cc" for event in observed["events"]))
        self.assertTrue(observed["control"]["cc_release_pending"])

    def test_existing_truthy_string_semantics_are_not_silently_reinterpreted(self):
        settings = Dt7EgressSettings(requested_value="false", real_tx_value="false", fire_value="false")
        self.assertTrue(settings.requested)
        self.assertTrue(settings.endpoint().real_tx)
        self.assertTrue(settings.fire_allowed)
        self.assertTrue(CustomControllerSettings(mock_value="false").mock_output)

    def test_endpoint_and_permission_values_are_immutable(self):
        for value, field, replacement in (
            (Dt7Endpoint("P", False), "port", "new"),
            (CustomControllerEndpoint("P", 30.0, True), "max_hz", 50.0),
            (EgressPermissions(True, True, False), "allow_drive", False),
            (DeviceEgressSettings.capture(None), "module_enabled", True),
        ):
            with self.subTest(value=value), self.assertRaises(FrozenInstanceError):
                setattr(value, field, replacement)

    def test_capture_reads_each_named_setting_once(self):
        calls = []
        module = SimpleNamespace(enabled=True, get_setting=lambda name: calls.append(name))
        DeviceEgressSettings.capture(SimpleNamespace(get=lambda _: module))
        self.assertEqual(len(calls), 10)
        self.assertEqual(len(set(calls)), 10)

    def test_import_is_independent_of_qt_ui_registry_and_device_layers(self):
        program = '''
import importlib.abc
import sys
class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("PySide6", "rm_client.model", "rm_client.control", "rm_client.ui",
                   "rm_client.serial_ports", "rm_client.backend.manager",
                   "rm_client.backend.dt7_link", "rm_client.backend.cc_link")
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("unexpected runtime import: " + fullname)
sys.meta_path.insert(0, BlockRuntime())
from rm_client.backend.device_egress_settings import DeviceEgressSettings
assert not DeviceEgressSettings.capture(None).dt7.endpoint().real_tx
'''
        result = subprocess.run([sys.executable, "-c", program], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
