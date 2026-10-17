"""Shared settings services preserve access policy without importing either UI.

Fresh-process import guards cover deferred imports as well as import-time ones.
No hardware, network endpoint or OpenGL surface is exercised.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.model import Module, ModuleRegistry, Setting, build_default_registry
from rm_client.configuration.registry import SettingId
from rm_client.ui import settings_application as application


# Frozen from the pre-extraction d3c0321 page, including order and toggle rights.
EXPECTED_PAGES = {
    "音频": (
        ("AudioFeedback", None, True),
        ("EliminationFX", ("Sound Enabled", "Sound Volume"), False),
        ("HUD", ("Jello List Sound",), False),
    ),
    "游戏": (
        ("MatchMode", None, False), ("RobotProfile", None, False),
        ("AllianceProfile", None, False), ("MatchChat", None, True),
        ("EconomyPanel", ("Shop Layout", "CS2 Feedback", "Confirm", "Hold Seconds"), False),
        ("QuickActionWheel", None, True),
    ),
    "键盘 / 鼠标": (
        ("RobotLink", ("Mouse Sensitivity", "Invert Mouse Y", "Mouse Wheel Scale",
                       "Bind W", "Bind S", "Bind A", "Bind D", "Bind SHIFT", "Bind CTRL",
                       "Bind Q", "Bind E", "Bind R", "Bind F", "Bind G", "Bind Z",
                       "Bind X", "Bind C", "Bind V", "Bind B"), False),
    ),
    "界面 / HUD": (
        ("ClickGui", ("Scale", "Language", "Save Layout"), False),
        ("HUD", None, True), ("UIScale", None, True),
        ("TopMatchBar", None, True), ("MinecraftCombatHUD", None, False),
        ("TacticalMap", None, True), ("CombatTargetHUD", None, True),
        ("EliminationFX", ("Preset", "Element Visibility", "Scale", "Responsive Scale",
                           "Anchor Policy", "X Offset", "Y Offset", "Kill Feed", "Card Icons"), True),
    ),
}


def import_targets(path):
    """Include imports nested inside functions, classes and exception handlers."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = importlib.util.resolve_name("." * node.level + (node.module or ""),
                                               "rm_client.ui") if node.level else node.module
            yield base
            yield from (base + "." + item.name for item in node.names)


class SettingsServiceBoundaryTests(unittest.TestCase):
    def run_isolated(self, program):
        result = subprocess.run([sys.executable, "-c", textwrap.dedent(program)], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)

    def test_public_page_exports_remain_identity_preserving_import_aliases(self):
        from rm_client.ui import cs2_settings_center as page
        from rm_client.ui.emergency_shortcut import install_estop_shortcut
        for name in ("PAGES", "settings_specs", "preference_capabilities",
                     "validate_draft", "apply_registry_draft"):
            with self.subTest(name=name):
                self.assertIs(getattr(page, name), getattr(application, name))
        self.assertIs(page.install_estop_shortcut, install_estop_shortcut)
        for name, signature in {
            "settings_specs": "(registry, page)",
            "preference_capabilities": "(registry)",
            "validate_draft": "(host, draft, module_binds=None)",
            "apply_registry_draft": "(host, draft, toggles=None, module_binds=None, *, audio_preferences=None)",
            "install_estop_shortcut": "(dialog, host)",
        }.items():
            self.assertEqual(str(inspect.signature(getattr(page, name))), signature)
        self.assertEqual(list(page.PAGES.items()), list(EXPECTED_PAGES.items()))

    def test_allowlist_tracks_live_settings_without_admitting_actions_or_output_gates(self):
        registry = build_default_registry()
        hud = registry.get("HUD")
        scalar = Setting("Synthetic Preference", "bool", False)
        hud.settings.extend((scalar, Setting("Synthetic Action", "action", "ignored"),
                             Setting("Synthetic Group", "action_group", "ignored")))
        access = application.preference_capabilities(registry)
        self.assertIn(SettingId("HUD", scalar.name), access.settings)
        self.assertNotIn(SettingId("HUD", "Synthetic Action"), access.settings)
        self.assertNotIn(SettingId("HUD", "Synthetic Group"), access.settings)
        self.assertNotIn(SettingId("RobotLink", "Allow Drive"), access.settings)
        self.assertNotIn(SettingId("RobotLink", "Mock Writes"), access.settings)
        self.assertNotIn("RobotLink", access.toggles)
        hud.settings.remove(scalar)
        self.assertNotIn(SettingId("HUD", scalar.name), application.preference_capabilities(registry).settings)
        self.assertEqual(list(application.settings_specs(ModuleRegistry([]), "游戏")), [])

    def test_in_place_legacy_page_allowlist_changes_are_shared(self):
        from rm_client.ui import cs2_settings_center as page
        registry = ModuleRegistry([Module("Synthetic", "System", settings=[Setting("Value", "bool", True)])])
        with patch.dict(page.PAGES, {"synthetic": (("Synthetic", ("Value",), False),)}):
            self.assertIn(SettingId("Synthetic", "Value"), application.preference_capabilities(registry).settings)
            self.assertEqual(list(page.settings_specs(registry, "synthetic"))[0][1][0].name, "Value")
        self.assertEqual(application.PAGES, EXPECTED_PAGES)

    def test_dependency_direction_includes_function_local_imports(self):
        page = "rm_client.ui.cs2_settings_center"
        keyboard = "rm_client.ui.jello_keybinds"
        for filename, forbidden in (
            ("jello_keybinds.py", (page,)),
            ("settings_application.py", (page, keyboard)),
            ("emergency_shortcut.py", (page, keyboard)),
        ):
            for target in import_targets(ROOT / "rm_client/ui" / filename):
                self.assertFalse(any(target == name or target.startswith(name + ".") for name in forbidden),
                                 f"{filename} imports {target}")
        # Opening the keyboard UI from a settings control remains one-way.
        self.assertIn(keyboard, set(import_targets(ROOT / "rm_client/ui/cs2_settings_center.py")))

    def test_shared_application_import_and_validation_need_no_pages_or_qt(self):
        self.run_isolated('''
            import importlib.abc
            import sys
            from types import SimpleNamespace
            class BlockPagesAndQt(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    blocked = ("PySide6", "rm_client.ui.cs2_settings_center", "rm_client.ui.jello_keybinds")
                    if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
                        raise AssertionError("unexpected import: " + fullname)
            sys.meta_path.insert(0, BlockPagesAndQt())
            from rm_client.ui.settings_application import validate_draft
            from rm_client.control import KeyboardControlBridge
            from rm_client.model import build_default_registry
            host = SimpleNamespace(registry=build_default_registry(), control=KeyboardControlBridge())
            binds = validate_draft(host, {("RobotLink", "Bind W"): "S", ("RobotLink", "Bind S"): "W"})
            assert binds["W"] == "S" and binds["S"] == "W"
            assert host.control.binds["W"] == "W"
            print("PASS: shared validation without pages or Qt")
        ''')

    def test_keyboard_can_build_validate_commit_and_stop_with_settings_page_blocked(self):
        self.run_isolated('''
            import importlib.abc
            import sys
            from unittest.mock import Mock
            class BlockSettingsPage(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "rm_client.ui.cs2_settings_center":
                        raise AssertionError("keyboard imported settings page")
            sys.meta_path.insert(0, BlockSettingsPage())
            from PySide6.QtCore import Qt
            from PySide6.QtTest import QTest
            from PySide6.QtWidgets import QApplication, QWidget
            from rm_client.control import KeyboardControlBridge
            from rm_client.configuration.transactions import ConfigurationRestorePoint
            from rm_client.model import RobotState, build_default_registry
            from rm_client.ui.jello_keybinds import build_keyboard_manager
            app = QApplication([])
            host = QWidget()
            host.resize(1000, 700)
            host.registry, host.state, host.control = build_default_registry(), RobotState(), KeyboardControlBridge()
            host.config_loading = False
            host.backend = Mock()
            host.backend.trigger_estop.return_value = (True, "")
            for name in ("ensure_configuration_idle", "restore_configuration_runtime", "fail_closed_configuration",
                         "record_configuration_failure", "after_setting_changed", "invalidate_gui_cache",
                         "invalidate_transient_overlay_scene", "auto_save_config", "toast", "frame_scheduler"):
                setattr(host, name, Mock())
            host.capture_configuration_restore_point = lambda: ConfigurationRestorePoint({}, dict(host.control.binds), True, "scenario")
            dialog = build_keyboard_manager(host)
            entry = next(entry for entry in dialog.session.entries if entry.identity == ("module", "TacticalMap"))
            dialog.session.assign(entry, "F10")
            assert host.registry.get("TacticalMap").keybind == "Tab"
            dialog.session.apply()
            assert host.registry.get("TacticalMap").keybind == "F10"
            host.auto_save_config.assert_called_once_with()
            dialog.show()
            QTest.keyClick(dialog.canvas, Qt.Key.Key_F12)
            host.backend.trigger_estop.assert_called_once_with()
            assert "rm_client.ui.cs2_settings_center" not in sys.modules
            dialog.hide()
            dialog.reject()
            dialog.deleteLater()
            host.deleteLater()
            app.processEvents()
            print("PASS: independent keyboard construction, validation, commit and F12")
        ''')


if __name__ == "__main__":
    unittest.main()
