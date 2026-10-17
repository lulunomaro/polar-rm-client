"""Atomic binding permutations through real settings, persistence and fresh Qt windows.

Run: QT_QPA_PLATFORM=offscreen python tests/test_binding_restore_quality.py
No hardware, network endpoint, or live control output is used.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from rm_client.config_store import ClientConfigStore, apply_registry_config, export_client_config
from rm_client.configuration.transactions import ConfigurationRestorePoint
from rm_client.control import DEFAULT_CONTROL_BINDS, KeyboardControlBridge, validate_control_binds
from rm_client.model import (
    CONTROL_BIND_OPTIONS, CONTROL_RESERVED_KEYS, CONTROL_SLOT_ORDER,
    MODULE_BIND_OPTIONS, MODULE_RESERVED_KEYS, RobotState, build_default_registry,
)
from rm_client.ui.cs2_settings_center import apply_registry_draft, validate_draft
from rm_client.ui.polar.mixins.persistence_mixin import PersistenceMixin
from test_configuration_transactions import settle_configuration
from window_lifecycle_helpers import close_window_safely

APP = QApplication.instance() or QApplication([])


class Host(PersistenceMixin):
    """Production persistence method, real registry/bridge, inert output adapters."""
    def __init__(self):
        self.registry = build_default_registry()
        self.control = KeyboardControlBridge()
        self.state = RobotState()
        self.backend = Mock(mqtt=NS(mock_output=True), lifecycle_pending=False)
        self.frame_scheduler = Mock()
        self.toast = Mock()
        self.config_loading = False
        self.auto_save_config = Mock()
        self.invalidate_gui_cache = Mock()
        self.invalidate_transient_overlay_scene = Mock()

    def capture_configuration_restore_point(self):
        return ConfigurationRestorePoint({}, dict(self.control.binds), True, "scenario")

    def restore_configuration_runtime(self, previous):
        pass  # This test host has no device or scene runtime to rebuild.

    def fail_closed_configuration(self, previous, reason):
        self.control.binds = dict(previous.control_binds)
        self.backend.trigger_estop()
        self.backend.lock_control(reason)

    def after_setting_changed(self, setting):
        if setting.name.startswith("Bind "):
            self.sync_control_binds()

    def bindings(self):
        link = self.registry.get("RobotLink")
        return {slot: link.get_setting(f"Bind {slot}").value for slot in CONTROL_SLOT_ORDER}

    def set_registry_bindings(self, **bindings):
        link = self.registry.get("RobotLink")
        for slot, value in bindings.items():
            link.get_setting(f"Bind {slot}").value = value


class BindingMapTests(unittest.TestCase):
    def test_swap_cycle_and_multiple_unbound_slots_are_atomic(self):
        bridge = KeyboardControlBridge()
        for updates in ({"W": "S", "S": "W"},
                        {"W": "S", "S": "A", "A": "W"},
                        {"W": "None", "S": "none"}):
            with self.subTest(updates=updates):
                candidate = dict(DEFAULT_CONTROL_BINDS, **updates)
                self.assertTrue(bridge.set_binds(candidate))
                self.assertEqual(bridge.binds, validate_control_binds(candidate))
                candidate["W"] = "F12"
                self.assertNotEqual(bridge.binds["W"], "F12")

    def test_rejected_map_leaves_all_bindings_and_input_unchanged(self):
        bridge = KeyboardControlBridge()
        bridge.key_state["W"] = True
        previous = bridge.binds
        invalid = [dict(previous, W="S"), dict(previous, W="s"),
                   dict(previous, W=True), dict(previous, W=None),
                   dict(previous, W="NotAKey"), dict(previous, UNKNOWN="Up"),
                   {"W": "Up"}, None, []]
        invalid += [dict(previous, W=key) for key in CONTROL_RESERVED_KEYS]
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                self.assertFalse(bridge.set_binds(candidate))
                self.assertIs(bridge.binds, previous)
                self.assertTrue(bridge.key_state["W"])

    def test_single_slot_api_keeps_conflict_rejection_and_reserved_keys(self):
        bridge = KeyboardControlBridge()
        self.assertTrue(bridge.set_bind("W", "Up"))
        self.assertFalse(bridge.set_bind("S", "up"))
        for key in CONTROL_RESERVED_KEYS:
            self.assertFalse(bridge.set_bind("W", key))
        self.assertTrue(bridge.set_bind("A", "None"))
        self.assertTrue(bridge.set_bind("D", " "))
        self.assertEqual(bridge.binds["D"], "None")
        bridge.reset_binds()
        self.assertEqual(bridge.binds, DEFAULT_CONTROL_BINDS)

    def test_all_unconditional_window_keys_are_unavailable_for_driving(self):
        expected = {"rightshift", "tab", "escape", "f7", "f8", "f9", "f11", "f12"}
        self.assertEqual(CONTROL_RESERVED_KEYS, expected)
        self.assertFalse(expected & {name.lower() for name in CONTROL_BIND_OPTIONS})
        host = Host()
        for key in ("F7", "Escape"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_draft(host, {("RobotLink", "Bind W"): key})

    def test_driver_only_reservations_do_not_change_module_hotkey_policy(self):
        from rm_client.ui.jello_keybinds import BindingSession

        self.assertIn("F7", MODULE_BIND_OPTIONS)
        self.assertIn("Escape", MODULE_BIND_OPTIONS)  # Historical option set.
        self.assertNotIn("f7", MODULE_RESERVED_KEYS)
        host = Host()
        session = BindingSession(host)
        driver = next(item for item in session.entries if item.identity == ("RobotLink", "Bind W"))
        hero = next(item for item in session.entries if item.identity == ("module", "HeroLob"))
        for key in ("F7", "Escape"):
            with self.assertRaises(ValueError):
                session.assign(driver, key)
        session.assign(hero, "F7")
        session.apply()
        self.assertEqual(host.registry.get("HeroLob").keybind, "F7")
        with self.assertRaises(ValueError):
            session.assign(hero, "Escape")


class RegistryBindingTests(unittest.TestCase):
    def test_restore_accepts_swaps_and_cycles_and_clears_old_held_input(self):
        for bindings in ({"W": "S", "S": "W"}, {"W": "S", "S": "A", "A": "W"}):
            host = Host()
            host.control.key_state["W"] = True
            host.set_registry_bindings(**bindings)
            host.sync_control_binds()
            self.assertEqual(host.bindings(), host.control.binds)
            for slot, physical in bindings.items():
                self.assertEqual(host.control.resolve_slot(physical), (slot,))
            self.assertFalse(any(host.control.key_state.values()))
            host.backend.lock_control.assert_called_once()
            host.backend.arm_match.assert_not_called()

    def test_invalid_restore_rolls_back_all_displayed_bindings(self):
        for updates in ({"W": "S", "A": "Up"}, {"W": "F12", "A": "Up"}):
            host = Host()
            previous = dict(host.control.binds)
            host.set_registry_bindings(**updates)
            host.sync_control_binds()
            self.assertEqual(host.control.binds, previous)
            self.assertEqual(host.bindings(), previous)
            host.backend.lock_control.assert_not_called()
            host.toast.assert_called_once()

    def test_invalid_profile_binding_raises_into_existing_config_rollback(self):
        host = Host()
        previous = dict(host.control.binds)
        host.config_loading = True
        host.set_registry_bindings(W="S", A="Up")
        with self.assertRaises(ValueError):
            host.sync_control_binds()
        self.assertEqual(host.control.binds, previous)
        self.assertEqual(host.bindings(), previous)

    def test_pending_backend_rejects_binding_session_without_consuming_the_draft(self):
        from rm_client.ui.jello_keybinds import BindingSession

        host = Host()
        session = BindingSession(host)
        entry = next(item for item in session.entries if item.identity == ("module", "HeroLob"))
        previous = host.registry.get("HeroLob").keybind
        session.assign(entry, "F7")
        host.backend.lifecycle_pending = True
        with self.assertRaisesRegex(ValueError, "尚未完成"):
            session.apply()
        self.assertEqual(host.registry.get("HeroLob").keybind, previous)
        host.auto_save_config.assert_not_called()
        host.backend.lock_control.assert_not_called()
        host.backend.lifecycle_pending = False
        session.apply()
        self.assertEqual(host.registry.get("HeroLob").keybind, "F7")

    def test_settings_callbacks_observe_the_whole_permutation(self):
        host = Host()
        host.control.key_state["W"] = True
        apply_registry_draft(host, {("RobotLink", "Bind W"): "S",
                                    ("RobotLink", "Bind S"): "A",
                                    ("RobotLink", "Bind A"): "W"})
        self.assertEqual(host.bindings(), host.control.binds)
        self.assertEqual([host.control.binds[slot] for slot in ("W", "S", "A")], ["S", "A", "W"])
        self.assertFalse(any(host.control.key_state.values()))
        host.auto_save_config.assert_called_once()

    def test_save_reload_fresh_registry_restores_canonical_modifier_swap(self):
        host = Host()
        apply_registry_draft(host, {("RobotLink", "Bind SHIFT"): "ctrl",
                                    ("RobotLink", "Bind CTRL"): "SHIFT"})
        data = export_client_config(host.registry, gui_mode="JelloSigma", selected_category="System",
                                    selected_module="RobotLink", module_expanded=set(),
                                    old_panels={}, new_panels={}, dropdown_panels={})
        with tempfile.TemporaryDirectory() as directory:
            store = ClientConfigStore(Path(directory))
            store.save("swapped", data)
            fresh = Host()
            apply_registry_config(fresh.registry, store.load("swapped"))
            fresh.sync_control_binds()
        self.assertEqual(fresh.bindings(), fresh.control.binds)
        self.assertEqual(fresh.control.binds, host.control.binds)
        self.assertEqual(fresh.control.resolve_slot("Ctrl"), ("SHIFT",))
        self.assertEqual(fresh.control.resolve_slot("Shift"), ("CTRL",))

    def test_invalid_settings_draft_never_partially_applies(self):
        for draft in ({("RobotLink", "Bind W"): "S", ("RobotLink", "Mouse Sensitivity"): 2.0},
                      {("RobotLink", "Bind W"): "F12"},
                      {("RobotLink", "Allow Drive"): True},
                      {("EconomyPanel", "Mock Requests"): False}):
            host = Host()
            previous = dict(host.control.binds)
            with self.assertRaises(ValueError):
                apply_registry_draft(host, draft)
            self.assertEqual(host.control.binds, previous)
            self.assertEqual(host.registry.get("RobotLink").get_setting("Mouse Sensitivity").value, 1.0)
            host.backend.lock_control.assert_not_called()
            host.auto_save_config.assert_not_called()

    def test_invalid_toggle_or_module_binding_validated_before_mutation(self):
        for toggles, module_binds in (({"RobotLink": True}, {}), ({"HUD": "false"}, {}),
                                      ({"Missing": True}, {}), ({}, {"Missing": "F10"}),
                                      ({}, {"TacticalMap": None}), ({}, {"ClickGui": "F10"}),
                                      ({}, {"TacticalMap": "F10", "CombatTargetHUD": None})):
            host = Host()
            with self.assertRaises(ValueError):
                apply_registry_draft(host, {("RobotLink", "Mouse Sensitivity"): 2.0}, toggles, module_binds)
            self.assertEqual(host.registry.get("RobotLink").get_setting("Mouse Sensitivity").value, 1.0)
            host.backend.lock_control.assert_not_called()


class FreshWindowBindingTests(unittest.TestCase):
    def test_menu_keys_are_consumed_and_old_profiles_fail_closed_explicitly(self):
        from rm_client.ui.polar_app import PolarRMWindow

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": directory}):
            window = PolarRMWindow()
            settle_configuration(window)
            try:
                for timer in window.findChildren(QTimer):
                    timer.stop()
                window.home_visible = False
                with patch.object(window, "open_event_command_menu") as event_menu, \
                     patch.object(window, "set_escape_menu_visible") as escape_menu, \
                     patch.object(window, "handle_control_key_event") as driving:
                    window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F7, Qt.KeyboardModifier.NoModifier))
                    window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
                    event_menu.assert_called_once()
                    escape_menu.assert_called_once_with(True)
                    driving.assert_not_called()
                window.home_visible = True
                previous = deepcopy(window.current_config_data())
                previous_binds = dict(window.control.binds)
                for key in ("F7", "Escape"):
                    with self.subTest(key=key):
                        legacy = deepcopy(previous)
                        legacy["modules"]["RobotLink"]["settings"].update({"Bind W": key, "Mouse Sensitivity": 2.0})
                        source_store = ClientConfigStore(Path(directory) / "legacy")
                        source = source_store.save(key, legacy)
                        original_bytes = source.read_bytes()
                        profile = window.config_store.import_file(source, registry=window.registry)
                        with patch.object(window, "toast") as toast:
                            window.apply_config_data(window.config_store.load(profile), announce=False)
                        self.assertEqual(window.current_config_data(), previous)
                        self.assertEqual(window.control.binds, previous_binds)
                        self.assertTrue(window.backend.safety.estop_active)
                        self.assertEqual(source.read_bytes(), original_bytes)
                        self.assertEqual(window.config_store.profile_path(profile).read_bytes(), original_bytes)
                        message = toast.call_args.args[0]
                        self.assertIn("配置加载失败", message)
                        self.assertIn(key, message)
                        self.assertIn("W 槽", message)
                        self.assertIn("键盘设置", message)
                        settle_configuration(window)
            finally:
                close_window_safely(window, APP)
                window.deleteLater()
                APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_settings_save_then_new_window_startup_restores_swap_and_cycles(self):
        from rm_client.ui.polar_app import PolarRMWindow

        windows = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": directory}):
            try:
                first = PolarRMWindow()
                settle_configuration(first)
                windows.append(first)
                for timer in first.findChildren(QTimer):
                    timer.stop()
                apply_registry_draft(first, {("RobotLink", "Bind W"): "S",
                                              ("RobotLink", "Bind S"): "W",
                                              ("RobotLink", "Bind A"): "D",
                                              ("RobotLink", "Bind D"): "Q",
                                              ("RobotLink", "Bind Q"): "A"})
                first.save_current_config(announce=False)
                expected = dict(first.control.binds)
                fresh = PolarRMWindow()
                settle_configuration(fresh)
                windows.append(fresh)
                for timer in fresh.findChildren(QTimer):
                    timer.stop()
                self.assertEqual(fresh.control.binds, expected)
                link = fresh.registry.get("RobotLink")
                self.assertEqual({slot: link.get_setting(f"Bind {slot}").value for slot in CONTROL_SLOT_ORDER}, expected)
                self.assertEqual(fresh.control.resolve_slot("W"), ("S",))
                self.assertEqual(fresh.control.resolve_slot("A"), ("Q",))
                self.assertFalse(any(fresh.control.key_state.values()))
                self.assertTrue(fresh.home_visible)
                self.assertTrue(fresh.backend.mqtt.mock_output)
                # A bad restore must also undo unrelated preferences through
                # the real window's existing rollback, without arming output.
                previous = deepcopy(fresh.current_config_data())
                bad = deepcopy(previous)
                bad_settings = bad["modules"]["RobotLink"]["settings"]
                bad_settings["Bind W"] = "W"  # S already owns W after the swap.
                bad_settings["Mouse Sensitivity"] = 2.0
                fresh.apply_config_data(bad, announce=False)
                settle_configuration(fresh)
                self.assertEqual(fresh.current_config_data(), previous)
                self.assertEqual(fresh.control.binds, expected)
                self.assertTrue(fresh.backend.safety.estop_active)
                # Old files used noncanonical modifier names. They must also
                # survive generic enum validation and remain unmodified on disk.
                legacy = deepcopy(previous)
                legacy_settings = legacy["modules"]["RobotLink"]["settings"]
                legacy_settings.update({"Bind SHIFT": "CTRL", "Bind CTRL": "SHIFT"})
                fresh.apply_config_data(legacy, announce=False)
                settle_configuration(fresh)
                self.assertEqual(fresh.control.resolve_slot("Ctrl"), ("SHIFT",))
                self.assertEqual(fresh.control.resolve_slot("Shift"), ("CTRL",))
                self.assertEqual(legacy_settings["Bind SHIFT"], "CTRL")
            finally:
                for window in reversed(windows):
                    close_window_safely(window, APP)
                    window.deleteLater()
                APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
