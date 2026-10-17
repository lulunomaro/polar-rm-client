"""Client angle integration checks; no devices, GUI, or firmware execution."""
from __future__ import annotations
import ast
from dataclasses import replace
from pathlib import Path
import math
import struct
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.backend.controller_angles import (
    AngleRelayConfig, ControllerAngleRelay, angle_payload, ANGLE_BOUNDS,
    explicit_port, port_key,
)
from rm_client.backend.cc_link import CustomControllerLink, decode_cc_frame
from rm_client.backend.device_egress_settings import CustomControllerSettings, EgressPermissions
from rm_client.backend.port_admission import PortReservation, first_port_conflict
from rm_client.model import build_default_registry
from rm_client.serial_ports import refresh_port_options


def manager_method(name="_sync_controller_angles"):
    tree = ast.parse((ROOT / "rm_client/backend/manager.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BackendManager")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == name)
    env = dict(Any=object, AngleRelayConfig=AngleRelayConfig, replace=replace,
               PortReservation=PortReservation, first_port_conflict=first_port_conflict,
               explicit_port=explicit_port, port_key=port_key,
               CustomControllerSettings=CustomControllerSettings, EgressPermissions=EgressPermissions)
    exec(compile(ast.Module(body=[method], type_ignores=[]), "manager-angle-method", "exec"), env)
    return env[name]


class ControllerAngleIntegrationTests(unittest.TestCase):
    def setUp(self):
        with patch("rm_client.model.polar_port_choices", return_value=["Auto", "Stub", "RX", "TX"]), \
                patch("rm_client.model.dt7_port_choices", return_value=["Auto"]):
            self.registry = build_default_registry()
        self.angles = self.registry.get("ControllerAngles")
        self.robot = self.registry.get("RobotLink")
        self.link = ControllerAngleRelay(start_workers=False)
        self.manager = SimpleNamespace(controller_angles=self.link,
            _backend_transition_blocks_work=lambda: False,
            safety=SimpleNamespace(estop_active=False), state=SimpleNamespace(backend_stats={}))
        self.sync = manager_method()

    def tearDown(self):
        self.link.close()

    def setting(self, module, name, value):
        module.get_setting(name).value = value

    def enable(self, *, mock=False):
        self.angles.enabled = True
        self.robot.enabled = True
        for name, value in (("Angle RX Port", "RX"), ("Angle TX Port", "TX"),
                            ("Forward Angles", True), ("Angle Mock Writes", mock)):
            self.setting(self.angles, name, value)
        self.setting(self.robot, "Port", "Stub")
        self.setting(self.robot, "Allow Drive", True)
        return self.sync(self.manager, self.registry)

    def test_defaults_off_mock_no_auto_input(self):
        self.assertFalse(self.angles.enabled)
        self.assertTrue(self.angles.get_setting("Angle Mock Writes").value)
        self.assertFalse(self.angles.get_setting("Forward Angles").value)
        self.assertNotIn("Auto", self.angles.get_setting("Angle RX Port").options)
        status = self.sync(self.manager, self.registry)
        self.assertFalse(status["enabled"])
        self.assertFalse(status["armed"])

    def test_actual_source_boundaries_float32_rounding(self):
        for axis, (lo, hi) in enumerate(ANGLE_BOUNDS):
            for boundary in (lo, hi):
                values = [0.0] * 6
                values[axis] = boundary
                decoded, data = angle_payload(struct.pack("<6f", *values) + b"abcdef")
                self.assertEqual(data[24:], bytes(6))
                self.assertTrue(math.isfinite(decoded[axis]))
            for invalid in (lo - 0.01, hi + 0.01):
                values = [0.0] * 6
                values[axis] = invalid
                with self.assertRaises(ValueError):
                    angle_payload(struct.pack("<6f", *values) + bytes(6))

    def test_enabled_reception_is_not_arm(self):
        status = self.enable()
        self.assertTrue(status["enabled"])
        self.assertFalse(status["armed"])
        self.assertFalse(self.link.arm()[0])
        self.assertIn("controller_angles", self.manager.state.backend_stats)

    def test_existing_human_gates_all_required(self):
        self.enable()
        for field in ("module", "drive", "estop"):
            with self.subTest(field=field):
                self.robot.enabled = field != "module"
                self.setting(self.robot, "Allow Drive", field != "drive")
                self.manager.safety.estop_active = field == "estop"
                self.sync(self.manager, self.registry)
                self.assertFalse(self.link._authorized)
                self.assertFalse(self.link.arm()[0])

    def test_physical_serial_route_does_not_require_mqtt(self):
        self.enable()
        self.assertTrue(self.link._authorized)
        self.assertFalse(hasattr(self.manager, "mqtt"))

    def test_conflicting_keymouse_port_blocks_angles_only(self):
        self.enable()
        self.setting(self.robot, "Custom Controller Egress", True)
        self.setting(self.robot, "CC Mock Writes", False)
        self.setting(self.robot, "CC Port", "tx")
        status = self.sync(self.manager, self.registry)
        self.assertFalse(status["enabled"])
        self.assertIn("CC keymouse", status["last_error"])
        self.assertTrue(self.robot.get_setting("Custom Controller Egress").value)
        self.setting(self.robot, "CC Port", "CC_OTHER")
        self.assertTrue(self.sync(self.manager, self.registry)["enabled"])

    def test_unresolved_auto_owner_cannot_steal_angle_rx(self):
        self.enable()
        self.setting(self.robot, "Port", "Auto")
        status = self.sync(self.manager, self.registry)
        self.assertFalse(status["enabled"])
        self.assertIn("RobotLink port may conflict", status["last_error"])

    def test_previous_nonmock_cc_writer_blocks_ownership_overlap(self):
        self.enable()
        self.manager._cc_link = SimpleNamespace(mock_output=False, port="TX", resolved_port="TX")
        status = self.sync(self.manager, self.registry)
        self.assertFalse(status["enabled"])
        self.assertIn("CC prior writer", status["last_error"])

    def test_dt7_and_referee_port_conflicts(self):
        self.enable()
        self.setting(self.robot, "Direct DT7 Egress", True)
        self.setting(self.robot, "DT7 Real TX", True)
        self.setting(self.robot, "DT7 Port", "RX")
        self.assertFalse(self.sync(self.manager, self.registry)["enabled"])
        self.setting(self.robot, "Direct DT7 Egress", False)
        self.manager.serial = SimpleNamespace(running=True, port="RX")
        self.assertFalse(self.sync(self.manager, self.registry)["enabled"])

    def test_rescan_preserves_config_without_auto_select(self):
        self.setting(self.angles, "Angle RX Port", "ABSENT_RX")
        with patch("rm_client.serial_ports.port_choices", return_value=["Auto", "Stub", "NEW"]), \
                patch("rm_client.serial_ports.rc_port_choices", return_value=["Auto"]):
            refresh_port_options(self.registry)
        setting = self.angles.get_setting("Angle RX Port")
        self.assertEqual(setting.value, "ABSENT_RX")
        self.assertIn("NEW", setting.options)
        self.assertIn("ABSENT_RX", setting.options)
        self.assertNotIn("Auto", setting.options)

    def test_existing_keymouse_emits_0306_unchanged(self):
        cc = CustomControllerLink(mock_output=True)
        ok, _ = cc.send_keymouse(pressed=["W"], dx=4, dy=5, now=0.0)
        self.assertTrue(ok)
        decoded = decode_cc_frame(cc.last_frame)
        self.assertEqual(decoded["cmd_id"], 0x0306)
        self.assertEqual(len(decoded["data"]), 8)

    def test_keymouse_waits_for_old_angle_port_owner_to_close(self):
        self.manager.controller_angles = SimpleNamespace(owned_ports=("RX", "TX"))
        self.manager._cc_link_for = lambda _: self.fail("must not create a competing CC writer")
        settings = CustomControllerSettings(requested_value=True, port_value="TX", mock_value=False)
        control = SimpleNamespace(cc_enabled=True, cc_last_error="")
        manager_method("_configure_cc_egress")(self.manager, control, settings,
                                               EgressPermissions(bool(self.robot.enabled), True, False))
        self.assertFalse(control.cc_enabled)
        self.assertIn("串口", control.cc_last_error)

    def test_angle_sync_precedes_egress_even_when_polar_is_busy_or_closing(self):
        # Integration of PR 9's nonblocking UI path and PR 11's independent
        # angle relay: an occupied POLAR lock must not bypass angle revocation.
        tick = manager_method("tick_polar_link")
        for closing in (False, True):
            with self.subTest(closing=closing):
                self.enable(mock=True)
                self.angles.enabled = False
                events = []
                lock = threading.Lock()
                self.manager.polar = SimpleNamespace(_stopping=closing, _intent_lock=lock)
                self.manager.polar_bridge = SimpleNamespace(active=False)
                self.manager._drain_input_clear = lambda: None
                self.manager._backend_transition_blocks_work = lambda: False
                self.manager._poll_device_writers = lambda control: events.append("poll")

                def sync(registry):
                    events.append("angles")
                    return self.sync(self.manager, registry)

                self.manager._sync_controller_angles = sync
                self.manager._configure_device_egress = lambda registry, control: events.append("egress")
                self.manager._tick_polar_link_ready = lambda *args: self.fail("busy transport was entered")
                lock.acquire()
                try:
                    tick(self.manager, registry=self.registry)
                finally:
                    lock.release()
                self.assertEqual(events, ["poll", "angles", "egress"])
                self.assertFalse(self.link.stats()["enabled"])

    def test_ui_action_dispatch_arms_and_disarms_real_relay_in_mock(self):
        # Exercise the actual UI method without creating a Qt window.
        path = ROOT / "rm_client/ui/polar/mixins/config_actions_mixin.py"
        tree = ast.parse(path.read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ConfigActionsMixin")
        from rm_client.configuration.commands import ConfigCommandKind, resolve_config_command
        env = dict(ConfigCommandKind=ConfigCommandKind, resolve_config_command=resolve_config_command)
        # Compile the real adapter class so its explicit helper boundary remains
        # exercised, while retaining this suite's Qt-free import contract.
        future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[future, cls], type_ignores=[])),
                     "angle-ui-action", "exec"), env)
        self.enable(mock=True)
        from rm_client.backend.cc_link import encode_cc_frame
        self.link.feed(encode_cc_frame(0x0302, bytes(30), 1))
        self.manager._sync_controller_angles = lambda registry: self.sync(self.manager, registry)
        messages = []
        window = env["ConfigActionsMixin"]()
        window.backend, window.registry, window.toast = self.manager, self.registry, messages.append
        window.handle_student_config_action = lambda action: False
        window.handle_config_action("Arm Angle Forward")
        self.assertTrue(self.link.armed)
        window.handle_config_action("Show Angle Status")
        self.assertIn("真发=0", messages[-1])
        window.handle_config_action("Disarm Angle Forward")
        self.assertFalse(self.link.armed)


if __name__ == "__main__":
    unittest.main()
