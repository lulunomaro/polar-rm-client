"""Independent SIM truth regressions for inherited configuration/UI routes.

These tests use synthetic telemetry and inert transport/audio. They neither
launch MuJoCo nor connect a physical or network service.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from rm_client.config_store import apply_registry_config
from rm_client.sim_operator_features import UNAVAILABLE
from rm_client.sim_operator_runtime import OperatorRuntime
from rm_client.sim_operator_transport import SimOperatorTransport
from rm_client.ui.sim_operator_window import OperatorWindow
from tests.test_sim_operator import session, telemetry


class IndependentSimulationTruthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.start = patch.object(SimOperatorTransport, "start")
        self.stop = patch.object(SimOperatorTransport, "stop")
        self.audio = patch("rm_client.ui.polar_app.UiAudioManager")
        self.start.start()
        self.stop.start()
        self.audio.start()
        self.runtime = OperatorRuntime()
        self.window = OperatorWindow(self.runtime)
        self.window.timer.stop()
        self.runtime.transport.install_session(session())
        self.runtime.transport.install_telemetry(telemetry(), self.runtime.transport.session)
        self.runtime.apply_state(self.window.state)

    def tearDown(self):
        self.window.close()
        self.runtime._tmp.cleanup()
        self.audio.stop()
        self.stop.stop()
        self.start.stop()

    def incompatible_config(self):
        data = self.window.current_config_data()
        data["modules"]["MatchMode"]["settings"]["Mode"] = "RMUC7v7"
        data["modules"]["RobotProfile"]["settings"]["Role"] = "Hero"
        data["modules"]["AllianceProfile"]["settings"]["Side"] = "Blue"
        data["modules"]["CameraFeed"]["settings"]["Source"] = "Webcam"
        for name in UNAVAILABLE:
            data["modules"][name]["enabled"] = True
        return data

    def assert_authoritative_simulation_configuration(self):
        window = self.window
        config = window.match_mode_manager.current
        self.assertEqual(config.key, "RMUL3v3")
        self.assertEqual((config.field.width_m, config.field.height_m), (12, 8))
        self.assertFalse(config.field.asset_path)
        self.assertFalse(config.field.zones)
        self.assertEqual(window.registry.get("MatchMode").get_setting("Mode").value, "RMUL3v3")
        self.assertEqual(window.registry.get("RobotProfile").get_setting("Role").value, "Infantry1")
        self.assertEqual(window.registry.get("AllianceProfile").get_setting("Side").value, "Red")
        self.assertEqual(window.registry.get("CameraFeed").get_setting("Source").value, "Simulation")
        # No seeded mock values may leak even before the next GUI tick.
        self.assertEqual(window.state.robot_id, 3)
        self.assertEqual(window.state.current_health, 200)
        self.assertEqual(window.state.current_heat, 10)
        self.assertEqual(window.state.remaining_ammo, 20)
        self.assertFalse(self.runtime.armed)
        for name in UNAVAILABLE:
            self.assertFalse(window.registry.get(name).enabled, name)

    def test_reload_cannot_replace_simulator_field_or_known_state(self):
        with patch.object(self.window.config_store, "load", return_value=self.incompatible_config()):
            self.window.reload_current_config()
        self.assert_authoritative_simulation_configuration()

    def test_direct_config_application_has_same_truth_gate_as_reload(self):
        self.window.apply_config_data(self.incompatible_config(), announce=False)
        self.assert_authoritative_simulation_configuration()

    def test_config_enabled_bits_cannot_bypass_module_property_gate(self):
        apply_registry_config(self.window.registry, self.incompatible_config())
        for name in UNAVAILABLE:
            module = self.window.registry.get(name)
            with self.subTest(module=name):
                self.assertFalse(module.enabled)
                module.enabled = True
                self.assertFalse(module.enabled)
                module.set_enabled(True)
                self.assertFalse(module.enabled)

    def test_special_mc_slots_cannot_open_unimplemented_command_panels(self):
        bar = self.window.registry.get("MinecraftCombatHUD")
        names = ("EconomyPanel", "ProjectileShop", "ReloadingPanel", "SkillConfiguration",
                 "DartPanel", "InjuryStatistics", "MatchSettlement")
        for name in names:
            with self.subTest(slot_target=name):
                bar.get_setting("Slot 1").value = name
                self.window.activate_minecraft_function_slot(0)
                self.assertFalse(self.window.registry.get(name).enabled)
                self.assertFalse(self.window.economy_visible, name)
                self.assertFalse(self.window.student_ui.visible, name)
        # The inherited mock economy shortcut must not acknowledge a command
        # that has no simulator consumer, even when called without its panel.
        self.runtime.armed = True
        self.assertFalse(self.window.send_economy_request(SimpleNamespace(mock=True)))
        self.assertFalse(self.runtime.armed)
        self.assertIn("SIM unavailable", self.runtime.note)

    def test_dead_robot_revive_hotkeys_do_not_open_unimplemented_economy(self):
        data = telemetry()
        data["own"]["hp"] = 0
        data["own"]["alive"] = False
        self.runtime.transport.install_telemetry(data, self.runtime.transport.session)
        self.runtime.apply_state(self.window.state)
        for key in (Qt.Key.Key_F1, Qt.Key.Key_Y):
            with self.subTest(key=key):
                self.window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
                self.assertFalse(self.window.economy_visible)
                self.assertFalse(self.window.student_ui.visible)

    def test_control_progress_unknown_is_not_zero_percent_in_any_status_style(self):
        self.window.registry.get("VictoryPointStatus").enabled = True
        self.assertIn("control_progress", self.window.state.mujoco_unknown_fields)
        groups = {
            "map_status_groups": self.window.map_status_groups()[0],
            "match_status_rows": self.window.match_status_rows(),
            "match_status_ring_rows": self.window.match_status_ring_rows(),
        }
        for name, rows in groups.items():
            with self.subTest(style=name):
                self.assertTrue(any(
                    "VP" in str(row[0]) or row[0] in ("ALLY", "ENEMY")
                    or (len(row) > 4 and row[4] in ("red_vp", "blue_vp"))
                    for row in rows
                ))
                for row in rows:
                    if row[0] in ("CTRL", "CONTROL"):
                        self.assertNotIn("%", str(row[1]), "No control-progress percentage is observed")
                        if len(row) > 5:
                            self.assertNotIn("%", str(row[5]))


if __name__ == "__main__":
    unittest.main()
