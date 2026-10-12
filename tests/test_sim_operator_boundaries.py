"""Independent, offline operator safety/presentation boundary regressions.

All HTTP, image decoding and hardware-facing actions below use inert doubles.
The direct motion-layer check exercises dispatch, not desktop/OpenGL rendering.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from rm_client.model import RobotState
from rm_client.sim_operator_runtime import OperatorRuntime
from rm_client.sim_operator_transport import SCHEMA, SimOperatorTransport
from rm_client.top_match_bar import LifeState
from rm_client.ui.minecraft_hud import MinecraftCombatHudRenderer
from rm_client.ui.polar_app import PolarRMWindow
from rm_client.ui.polar.mixins.config_actions_mixin import ConfigActionsMixin
from rm_client.ui.sim_operator_window import OperatorWindow


FIXTURE = Path(__file__).parent / "fixtures" / "sim_operator" / "session.fixture.json"


def recorded_contract():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def initialized_telemetry():
    own = dict(
        robot_id="red_infantry", team="red", role="infantry", hp=200,
        max_hp=300, ammo=20, heat=10, heat_max=230, xy=[-3.0, 1.0],
        chassis_yaw=0.0, gimbal_yaw=0.2, pitch=0.1, alive=True,
    )
    return dict(
        schema=SCHEMA, session_id="synthetic-contract-fixture", epoch=0,
        robot_id="red_infantry", server_time_ms=0.0, sim_time=1.0,
        phase="running", remaining_seconds=299.0, countdown=None,
        vp={"red": 200, "blue": 190}, own=own, robots=[dict(own)],
        map_source="simulator_truth_display_only", simulation_only=True,
        hardware_transport=False, events=[],
    )


class BoundedLoop:
    """Run exactly the requested worker iterations without threads or sleeps."""
    def __init__(self, iterations):
        self.iterations = iterations
        self.completed = 0

    def is_set(self):
        return self.completed >= self.iterations

    def wait(self, _seconds):
        self.completed += 1


class OperatorBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.transport = SimOperatorTransport(clock=lambda: self.now[0])
        self.transport.install_session(recorded_contract())
        self.transport.install_telemetry(initialized_telemetry(), self.transport.session)
        self.transport.video = np.zeros((3, 4, 3), dtype=np.uint8)
        self.transport.video_at = self.now[0]
        self.transport.video_sequence = 1
        self.transport.video_sim_time = 1.0
        self.runtime = OperatorRuntime(transport=self.transport)
        self.state = RobotState()
        self.runtime.create_backend(self.state)
        self.control = self.runtime.create_control()
        self.window = SimpleNamespace(
            control=self.control, isActiveWindow=lambda: True,
            combat_control_input_allowed=lambda: True,
        )
        self.runtime.heartbeat(self.window)

    def tearDown(self):
        # No workers or sockets are started by these fixtures.
        self.runtime.disarm("test complete")
        self.runtime._tmp.cleanup()

    def frame_headers(self, sequence=2):
        return {
            "Content-Type": "image/jpeg",
            "X-Sim-Session-Id": self.transport.session["session_id"],
            "X-Sim-Epoch": "0", "X-Robot-Id": "red_infantry",
            "X-Frame-Seq": str(sequence), "X-Sim-Time": "1.1",
            "X-Camera-Name": "red_infantry__aim", "X-Camera-Overlays": "false",
            "X-Frame-Width": "4", "X-Frame-Height": "3",
        }

    def test_bad_then_good_camera_before_ui_poll_still_requires_f2(self):
        self.assertTrue(self.runtime.arm())
        self.runtime.heartbeat(self.window)
        self.transport.submit_input(
            vx_mps=1, vy_mps=0, yaw_rate_rps=0, pitch_rate_rps=0, fire=True,
        )
        bad = self.frame_headers()
        bad["X-Sim-Session-Id"] = "different-session"
        responses = [(b"fake-jpeg", bad), (b"fake-jpeg", self.frame_headers())]
        generation = self.transport._generation
        self.transport._stop = BoundedLoop(2)
        with patch.object(self.transport, "request", side_effect=responses), patch(
            "cv2.imdecode", return_value=np.zeros((3, 4, 3), dtype=np.uint8)
        ):
            self.transport._video_loop()
        # Both worker responses arrived before the GUI was polled again.
        self.assertIsNotNone(self.transport.video)
        self.assertGreater(self.transport._generation, generation)
        self.runtime.heartbeat(self.window)
        self.assertFalse(self.runtime.armed)
        payload = self.transport._control_payload()
        self.assertFalse(payload["deadman"])
        self.assertFalse(payload["fire"])
        self.assertEqual(payload["vx_mps"], 0)

    def test_duplicate_camera_does_not_refresh_age_or_restore_control(self):
        self.assertTrue(self.runtime.arm())
        self.runtime.heartbeat(self.window)
        old_stamp = self.transport.video_at
        self.now[0] += 0.1
        self.transport._stop = BoundedLoop(1)
        with patch.object(self.transport, "request", return_value=(b"fake", self.frame_headers(1))), patch(
            "cv2.imdecode", return_value=np.zeros((3, 4, 3), dtype=np.uint8)
        ):
            self.transport._video_loop()
        self.assertEqual(self.transport.video_at, old_stamp)
        self.now[0] = old_stamp + 1.6
        self.transport.install_telemetry(initialized_telemetry(), self.transport.session)
        self.runtime.heartbeat(self.window)
        self.assertFalse(self.runtime.armed)
        # Fresh image recovery alone never re-arms the operator.
        self.transport.video_at = self.now[0]
        self.runtime.heartbeat(self.window)
        self.assertFalse(self.runtime.armed)
        self.assertFalse(self.transport._control_payload()["deadman"])

    def test_disconnected_direct_gpu_motion_dispatch_skips_combat_renderer(self):
        painter = Mock()
        owner = SimpleNamespace(
            state=SimpleNamespace(mujoco_telemetry_valid=False),
            get_hud_scale=Mock(return_value=2.0), draw_watermark=Mock(),
            top_match_bar=SimpleNamespace(paint=Mock()),
            minecraft_hud=SimpleNamespace(draw=Mock(side_effect=AssertionError("fake dead HUD"))),
        )
        with patch.object(
            PolarRMWindow, "paint_hud_motion_overlay",
            side_effect=AssertionError("disconnected combat motion renderer"),
        ):
            OperatorWindow.paint_hud_motion_overlay(owner, painter, 1280, 720)
        owner.minecraft_hud.draw.assert_not_called()
        owner.top_match_bar.paint.assert_called_once()
        self.assertEqual(owner.top_match_bar.paint.call_args.args[1:3], (640, 360))
        painter.save.assert_called_once()
        painter.restore.assert_called_once()

    def test_unknown_other_robot_hp_is_not_drawn_as_zero_on_map(self):
        data = initialized_telemetry()
        other = dict(data["own"], robot_id="blue_hero", team="blue", role="hero",
                     hp=None, max_hp=None, xy=[3.0, -1.0])
        data["robots"].append(other)
        self.transport.install_telemetry(data, self.transport.session)
        self.runtime.apply_state(self.state)
        self.assertEqual([row["id"] for row in self.state.map_robots], [3])
        # The inherited optional RobotListHud also casts HP/max to int.
        self.assertEqual([row["id"] for row in self.state.robot_rows], [3])
        card = next(card for card in self.runtime.match_bar().right.cards if card.robot_id == 101)
        self.assertEqual(card.life, LifeState.UNKNOWN)
        self.assertIsNone(card.health)
        self.assertIsNone(card.max_health)

    def test_legacy_qualification_and_rescan_dispatch_never_open_io(self):
        owner = SimpleNamespace(
            _simulation_runtime=self.runtime, toast=Mock(),
            handle_student_config_action=lambda _action: False,
        )
        for name in ("handle_qualification_action", "rescan_serial_ports",
                     "run_dt7_link_selftest", "run_radio_km_selftest"):
            setattr(owner, name, MethodType(getattr(OperatorWindow, name), owner))
        with patch("socket.socket", side_effect=AssertionError("unexpected socket")), patch(
            "rm_client.serial_ports.port_snapshot", side_effect=AssertionError("device scan")
        ), patch("rm_client.serial_ports._scan_serial_ports", side_effect=AssertionError("driver scan")), patch(
            "rm_client.preflight.run_preflight", side_effect=AssertionError("physical preflight")
        ):
            for action in ("Run Preflight", "Run Video Test", "Run KMC Test", "Arm Match Control",
                           "Rescan Serial Ports", "Run DT7 Link Selftest", "Run Wireless KM Selftest"):
                with self.subTest(action=action):
                    ConfigActionsMixin.handle_config_action(owner, action)
        self.assertFalse(self.runtime.armed)

    def test_sim_scoreboard_uses_real_fields_and_explicit_unknowns(self):
        self.runtime.apply_state(self.state)
        lines = [text for text, _ in MinecraftCombatHudRenderer.scoreboard_lines(self.state)]
        for unsupported in ("Base:", "Shield:", "Post:", "Gold:", "Kills:"):
            self.assertFalse(any(unsupported in line for line in lines))
        self.assertTrue(any("Ally VP:" in line and "200" in line for line in lines))
        self.assertTrue(any("HP:" in line and "200/300" in line for line in lines))
        self.now[0] += 0.6
        self.runtime.apply_state(self.state)
        lines = [text for text, _ in MinecraftCombatHudRenderer.scoreboard_lines(self.state)]
        self.assertTrue(any("DISCONNECTED / STALE" in line for line in lines))
        for label in ("HP:", "Ammo:", "Heat:", "Time:"):
            self.assertTrue(any(label in line and "?" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
