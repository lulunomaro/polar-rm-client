"""Exercise the real window adapters against registry and wheel state objects."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.model import build_default_registry
from rm_client.quick_actions import ACTION_LABELS
from rm_client.ui.polar_app import PolarRMWindow
from rm_client.ui.quick_action_wheel import QuickActionWheelState


class WindowWheelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.host = NS(registry=build_default_registry(), quick_action_wheel=QuickActionWheelState())
        self.module = self.host.registry.get("QuickActionWheel")

    def test_actual_settings_adapter_converges_both_radius_projections(self):
        self.module.get_setting("Radius").value = 72
        self.module.get_setting("Dead Zone").value = 96
        PolarRMWindow.sync_quick_action_wheel_settings(self.host)
        self.assertAlmostEqual(self.host.quick_action_wheel.dead_zone, 51.84)
        self.assertAlmostEqual(self.module.get_setting("Dead Zone").value, 51.84)
        self.assertEqual(self.module.get_setting("Radius").value, 72)

    def test_actual_settings_adapter_preserves_custom_labels_and_icons(self):
        wheel = self.host.quick_action_wheel
        wheel.configure(custom_labels=["Custom"], custom_icons=["X"])
        self.module.get_setting("Up").value = ACTION_LABELS["clickgui"]
        PolarRMWindow.sync_quick_action_wheel_settings(self.host)
        self.assertEqual(wheel.actions[0], "clickgui")
        self.assertEqual(wheel.custom_labels[0], "Custom")
        self.assertEqual(wheel.custom_icons[0], "X")

    def test_actual_wheel_adapter_writes_normalized_state_back(self):
        wheel = self.host.quick_action_wheel
        wheel.configure(sector_count=4, radius=100, dead_zone=90, show_labels=False)
        PolarRMWindow.apply_quick_action_wheel_to_settings(self.host)
        self.assertEqual(self.module.get_setting("Sectors").value, "4")
        self.assertEqual(self.module.get_setting("Radius").value, 100)
        self.assertEqual(self.module.get_setting("Dead Zone").value, 72)
        self.assertFalse(self.module.get_setting("Show Labels").value)

    def test_missing_optional_module_leaves_wheel_unchanged(self):
        before = self.host.quick_action_wheel.export_config()
        self.host.registry = NS(get=lambda _: None)
        PolarRMWindow.sync_quick_action_wheel_settings(self.host)
        PolarRMWindow.apply_quick_action_wheel_to_settings(self.host)
        self.assertEqual(self.host.quick_action_wheel.export_config(), before)


if __name__ == "__main__":
    unittest.main()
