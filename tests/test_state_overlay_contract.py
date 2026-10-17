"""Offline Polar telemetry boundary tests; no GUI, devices, or listeners.

Run: python tests/test_state_overlay_contract.py
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend import polar_bridge, polar_link
from rm_client.model import RobotState
from rm_client.state_overlay import POLAR_OVERLAY_FIELDS, validated_polar_overlay


class PolarOverlayContractTests(unittest.TestCase):
    def test_all_builtin_bridge_outputs_are_supported_without_value_changes(self):
        bridge = polar_bridge.PolarStateBridge(clock=lambda: 100.0)
        samples = {
            polar_link.SEG_IMU: {"yaw_rad": 0.5, "pitch_rad": 0.2, "roll_rad": -0.1},
            polar_link.SEG_JOINT_STATE: {"yaw_rad": 0.7, "pitch_rad": -0.3},
            polar_link.SEG_ROBOT_MOTION: {"vx_mps": -1.2, "vy_mps": 0.4, "wz_rps": 0.8},
            polar_link.SEG_ROBOT_STATUS: {"robot_id": 103, "current_hp": 320, "maximum_hp": 450},
            polar_link.SEG_BUFF: {"attack_buff": 1, "remaining_energy": 77},
            polar_link.SEG_ROBOT_STATE_INFO: {"chassis_error": True},
            polar_link.SEG_GAME_STATUS: {"stage_remain_time": 123456},
            polar_link.SEG_ALL_ROBOT_HP: {"ally_1_hp": 400, "ally_3_hp": 320, "ally_base_hp": 4500},
            polar_link.SEG_SHOOT_DATA: {"initial_speed_mps": 15.5, "launching_frequency_hz": 10},
            polar_link.SEG_GROUND_ROBOT_POSITION: {"hero_x": 1.5, "hero_y": -2.0},
            polar_link.SEG_RFID_STATUS: {"rfid_status": 3},
            polar_link.SEG_EVENT: {"event_data": 4},
            polar_link.SEG_REFEREE_WARNING: {"level": 1, "offending_robot_id": 3, "count": 2},
            polar_link.SEG_DEBUG: {"packages": {"km_key": 5.0, "voltage": 24.5}},
        }
        # This also catches a newly added handler without a contract fixture.
        self.assertEqual(set(samples), set(polar_bridge._HANDLERS))
        for segment_id, sample in samples.items():
            bridge.apply(polar_link.PolarSegment(segment_id, 1, sample))
        self.assertEqual(bridge.dropped, 0, bridge.last_error)
        self.assertEqual(set(bridge.overlay), POLAR_OVERLAY_FIELDS)
        self.assertEqual(validated_polar_overlay(bridge.overlay), bridge.overlay)

        state = RobotState(polar_link_active=True, polar_overlay=dict(bridge.overlay))
        state.apply_polar_overlay()
        model_fields = {item.name for item in fields(RobotState)}
        self.assertLessEqual(POLAR_OVERLAY_FIELDS, model_fields)
        for name, expected in bridge.overlay.items():
            with self.subTest(field=name):
                self.assertEqual(getattr(state, name), expected)

    def test_unrelated_model_fields_methods_and_class_attributes_cannot_be_injected(self):
        state = RobotState(polar_link_active=True, official_state_at=10.0)
        protected = {
            item.name: getattr(state, item.name)
            for item in fields(RobotState)
            if item.name not in POLAR_OVERLAY_FIELDS | {"polar_overlay"}
        }
        # Include all existing model fields: a future setting is protected by
        # default, even when it has the polar_ prefix.
        overlay = {name: object() for name in protected}
        overlay.update({
            "tick": None,
            "apply_polar_overlay": None,
            "__dict__": {},
            "__class__": dict,
            "effective_capacitor_percent": 123,
            "BUFF_ATK_TYPES": {42},
            "unknown_telemetry": 9,
            "power_limit": 300,
        })
        state.polar_overlay = overlay
        state.apply_polar_overlay()

        for name, previous in protected.items():
            with self.subTest(field=name):
                self.assertIs(getattr(state, name), previous)
        self.assertIs(type(state), RobotState)
        self.assertEqual(state.tick.__func__, RobotState.tick)
        self.assertEqual(state.apply_polar_overlay.__func__, RobotState.apply_polar_overlay)
        self.assertEqual(state.BUFF_ATK_TYPES, frozenset({1}))
        self.assertFalse(hasattr(state, "unknown_telemetry"))
        self.assertIs(state.polar_overlay, overlay)
        self.assertEqual(state.power_limit, 300)

    def test_wrong_scalar_types_nonfinite_values_and_invalid_keys_are_rejected(self):
        invalid_values = {
            "chassis_yaw_deg": [True, "12.5", None, [], {}, float("nan"), float("inf"), -float("inf"), 10 ** 400],
            "power_limit": [True, "300", 3.5, None, [], float("inf")],
            "drive_ready": [0, 1, "False", None, [], {}],
        }
        for field_name, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field_name, value=value):
                    self.assertEqual(validated_polar_overlay({field_name: value, "current_health": 350}), {"current_health": 350})
        self.assertEqual(validated_polar_overlay({None: 1, 42: 1}), {})
        self.assertEqual(validated_polar_overlay(None), {})
        self.assertEqual(validated_polar_overlay([("power_limit", 300)]), {})

    def test_valid_zero_negative_and_large_readings_are_not_coerced_or_clamped(self):
        overlay = {
            "chassis_yaw_deg": -179.5,
            "gimbal_pitch_deg": 0,
            "power_limit": 460,
            "current_health": 0,
            "rfid_status": 0xFFFFFFFF,
            "drive_ready": False,
        }
        self.assertEqual(validated_polar_overlay(overlay), overlay)

    def test_container_shapes_are_validated_without_partial_mapping_updates(self):
        invalid = {
            "polar_ally_hp": [{"3": 400}, {3: "400"}, {3: True}, {True: 400}, [400]],
            "polar_debug_packages": [{3: 1.0}, {"km_key": True}, {"voltage": float("nan")}, {"ok": 1.0, "bad": []}],
            "polar_ground_positions": [{3: [1.0, 2.0]}, {"3": (1.0, 2.0)}, {3: (1.0,)}, {3: (1.0, float("inf"))}, {3: (True, 2.0)}],
        }
        for name, candidates in invalid.items():
            for value in candidates:
                with self.subTest(field=name, value=value):
                    self.assertEqual(validated_polar_overlay({name: value}), {})
        empty = {name: {} for name in invalid}
        self.assertEqual(validated_polar_overlay(empty), empty)

    def test_mutating_bridge_or_model_containers_does_not_bypass_validation(self):
        overlay = {
            "polar_ally_hp": {3: 400},
            "polar_debug_packages": {"voltage": 24.5},
            "polar_ground_positions": {3: (1.0, 2.0)},
        }
        state = RobotState(polar_link_active=True, polar_overlay=overlay)
        state.apply_polar_overlay()
        state.polar_ally_hp[4] = 500
        overlay["polar_ally_hp"][3] = "invalid"
        overlay["polar_debug_packages"]["voltage"] = float("nan")
        overlay["polar_ground_positions"][3] = ["invalid"]
        self.assertEqual(state.polar_ally_hp, {3: 400, 4: 500})
        self.assertNotIn(4, overlay["polar_ally_hp"])
        self.assertEqual(state.polar_debug_packages, {"voltage": 24.5})
        self.assertEqual(state.polar_ground_positions, {3: (1.0, 2.0)})
        state.apply_polar_overlay()
        self.assertEqual(state.polar_ally_hp, {3: 400, 4: 500})
        self.assertEqual(state.polar_debug_packages, {"voltage": 24.5})
        self.assertEqual(state.polar_ground_positions, {3: (1.0, 2.0)})

    def test_generic_protocol_extensions_remain_available_in_original_overlay(self):
        mappings = {
            0x3C: {"pressure": "polar_barrel_pressure", "jam": "polar_barrel_jam"},
            polar_link.SEG_CHASSIS_STATE: {
                "spin_active": "polar_spin_active",
                "chassis_mode_code": "polar_chassis_mode",
                "self_check_flag": "polar_self_check_flag",
            },
        }
        bridge = polar_bridge.PolarStateBridge(clock=lambda: 100.0)
        with patch.object(polar_bridge, "_STATE_FIELDS_CACHE", mappings):
            bridge.apply(polar_link.PolarSegment(0x3C, 1, {"pressure": 2.75, "jam": True}))
            bridge.apply(polar_link.PolarSegment(polar_link.SEG_CHASSIS_STATE, 1, {
                "spin_active": True, "chassis_mode_code": 2, "self_check_flag": False,
            }))
        self.assertEqual(bridge.dropped, 0)
        state = RobotState(polar_link_active=bridge.active, polar_overlay=dict(bridge.overlay))
        original = state.polar_overlay
        state.apply_polar_overlay()
        self.assertIs(state.polar_overlay, original)
        self.assertEqual(state.polar_overlay, {
            "polar_barrel_pressure": 2.75,
            "polar_barrel_jam": True,
            "polar_spin_active": True,
            "polar_chassis_mode": 2,
            "polar_self_check_flag": False,
        })
        self.assertFalse(hasattr(state, "polar_barrel_pressure"))

    def test_generic_mapping_cannot_overwrite_operator_choices_or_authority(self):
        bridge = polar_bridge.PolarStateBridge(clock=lambda: 100.0)
        mapping = {0x3C: {"robot": "robot_id", "armed": "polar_drive_armed", "health": "current_health"}}
        with patch.object(polar_bridge, "_STATE_FIELDS_CACHE", mapping):
            bridge.apply(polar_link.PolarSegment(0x3C, 1, {"robot": 7, "armed": True, "health": 320}))
        state = RobotState(robot_id=3, polar_link_active=True, polar_overlay=dict(bridge.overlay))
        state.apply_polar_overlay()
        self.assertEqual(state.robot_id, 3)
        self.assertFalse(state.polar_drive_armed)
        self.assertEqual(state.current_health, 320)

    def test_live_overlay_still_wins_after_mock_tick_and_releases_when_stale(self):
        state = RobotState(start_time=90.0, polar_link_active=True, polar_overlay={
            "chassis_yaw_deg": 12.5, "small_buff": False, "current_chassis_energy": 77,
        })
        with patch("rm_client.model.time.perf_counter", return_value=100.0):
            state.tick(0.016)
            mock_yaw = state.chassis_yaw_deg
            self.assertNotEqual(mock_yaw, 12.5)
            state.apply_polar_overlay()
            self.assertEqual(state.chassis_yaw_deg, 12.5)
            self.assertFalse(state.small_buff)
            self.assertEqual(state.current_chassis_energy, 77)
            state.polar_link_active = False
            state.tick(0.016)
            state.apply_polar_overlay()
            self.assertEqual(state.chassis_yaw_deg, mock_yaw)

    def test_existing_source_precedence_and_official_owned_fields_are_preserved(self):
        state = RobotState(
            official_state_active=True,
            official_state_at=42.0,
            official_robot_health=[400, 320],
            robot_positions=[{"robot_id": 3}],
            polar_link_active=True,
            polar_overlay={
                "current_health": 300,
                "official_robot_health": {3: 300},
                "robot_positions": {3: (1.0, 2.0)},
                "map_robots": {3: 300},
                "current_buffer_energy": 999,
                "heat_cooldown_rate": 999,
                "official_state_active": False,
                "official_state_at": 999,
            },
        )
        state.tick(0.016)
        state.apply_polar_overlay()
        self.assertEqual(state.current_health, 300)
        self.assertEqual(state.official_robot_health, [400, 320])
        self.assertEqual(state.robot_positions, [{"robot_id": 3}])
        self.assertIsInstance(state.map_robots, list)
        self.assertEqual(state.current_buffer_energy, 41)
        self.assertEqual(state.heat_cooldown_rate, 18.0)
        self.assertTrue(state.official_state_active)
        self.assertEqual(state.official_state_at, 42.0)
        # Once stale, an official update is left untouched by the old overlay.
        state.polar_link_active = False
        state.current_health = 280
        state.apply_polar_overlay()
        self.assertEqual(state.current_health, 280)

    def test_empty_or_inactive_overlay_preserves_timestamp(self):
        for active, overlay in ((False, {"current_health": 1}), (True, {})):
            state = RobotState(polar_link_active=active, polar_overlay=overlay)
            before = state.current_health
            state.apply_polar_overlay()
            self.assertEqual(state.current_health, before)
            self.assertEqual(state.official_state_at, 0.0)


if __name__ == "__main__":
    unittest.main()
