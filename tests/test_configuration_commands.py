"""Characterize command adapters with inert transports and patched dialogs.

These call production mixin methods, including their cancellation/refresh and
fail-closed ordering. They do not qualify a robot or open a transport.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.model import RobotState, build_default_registry
from rm_client.ui.polar.mixins.config_actions_mixin import ConfigActionsMixin

MODULE = "rm_client.ui.polar.mixins.config_actions_mixin"


class CommandHost(ConfigActionsMixin):
    def __init__(self):
        self.registry = build_default_registry()
        self.state = RobotState()
        self.state.robot_id = 3
        self.control = Mock()
        self.backend = Mock()
        self.backend.trigger_estop.return_value = (True, "")
        self.backend.mqtt.publish_estop_zero.return_value = (True, "")
        self.backend.arm_match_control.return_value = (False, "missing evidence")
        self.backend.qualification.session = NS(created_at=100.0, firmware_hash="a" * 64)
        self.backend.safety.estop_at = 15.0
        self.backend.safety.control_state.value = "LOCKED"
        self.backend.mqtt.reconnect_count = 2
        self.backend.stats.return_value = {
            "udp": {"decoded_frames": 20, "decoded_connected": True, "continuous_decoded_seconds": 60},
            "safety_evidence": {"estop_zero_publish_ok": True, "mqtt_disconnect_lock_at": 11,
                                "mqtt_reconnected_locked_at": 12},
        }
        self.backend.controller_angles.config.mock_output = True
        self.backend.controller_angles.arm.return_value = (True, "fresh input required")
        self.backend._sync_controller_angles.return_value = {
            "valid": True, "armed": False, "forwarded": 2, "mock_forwarded": 3, "last_error": "none"}
        self.match_mode_manager = NS(current=NS(key="RMUC7v7"))
        self.scenario_manager = NS(current=NS(key="LabDebug"))
        self.economy_bridge = NS(latest=None)
        self.paths = Path("/inert")
        self.frame_scheduler = Mock()
        self.ui_audio = Mock()
        self.toast = Mock()
        self.update_backend_readiness = Mock()
        self.handle_student_config_action = Mock(return_value=False)
        self.readiness_video_source = Mock(return_value="udp")
        self._qualification_baseline = Mock(return_value={"udp_frames": 10, "estop_at": 10,
            "captured_at_perf": 10, "captured_at_wall": 100, "mqtt_reconnect_count": 1,
            "deploy_revision": 2, "economy_revision": 2})
        self._capture_qualification_runtime_baseline = Mock(return_value={"captured": True})
        self.economy_uses_mock_transport = Mock(return_value=False)
        self.hero_range_profile_selected = Mock(return_value=None)
        self.custom_ui_sound_selection = Mock(return_value={})
        self.hero_lob_preview = Mock(return_value=(NS(reachable=True), NS(ready=True, summary="ready")))
        self.save_current_config = Mock()
        self.reload_current_config = Mock()
        self.import_config_from_dialog = Mock()
        self.import_wallpaper_from_dialog = Mock()
        self.reset_wallpaper_profile = Mock()
        self.restore_compact_hud = Mock()
        self.run_dt7_link_selftest = Mock()
        self.rescan_serial_ports = Mock()
        self.run_radio_km_selftest = Mock()
        self.handle_calibration_action = Mock()
        self.preview_elimination_fx = Mock()
        self.open_hero_deploy_interface = Mock()
        self.import_custom_ui_sound = Mock()
        self.start_kmc_qualification_test = Mock()
        self.start_economy_qualification_test = Mock()

    @staticmethod
    def get_setting_number(module, name, default):
        setting = module.get_setting(name) if module else None
        return float(setting.value) if setting is not None else default

    @staticmethod
    def setting_mode(module, name, default):
        setting = module.get_setting(name) if module else None
        return str(setting.value) if setting is not None else default


class ConfigurationCommandTests(unittest.TestCase):
    def setUp(self):
        self.host = CommandHost()

    def test_exact_simple_commands_delegate_without_implicit_save_or_refresh(self):
        routes = {
            "Save Current": "save_current_config", "Reload Profile": "reload_current_config",
            "Import Config": "import_config_from_dialog", "Import Wallpaper": "import_wallpaper_from_dialog",
            "Reset Wallpaper": "reset_wallpaper_profile", "Restore Compact HUD": "restore_compact_hud",
            "Run DT7 Link Selftest": "run_dt7_link_selftest", "Rescan Serial Ports": "rescan_serial_ports",
            "Run Wireless KM Selftest": "run_radio_km_selftest", "Open Deploy Interface": "open_hero_deploy_interface",
        }
        for command, method in routes.items():
            with self.subTest(command=command):
                host = CommandHost()
                host.handle_config_action(command)
                getattr(host, method).assert_called_once_with()
                host.handle_student_config_action.assert_called_once_with(command)
                host.update_backend_readiness.assert_not_called()
                host.frame_scheduler.force.assert_not_called()
        for command in ("Capture Chessboard", "Solve Calibration", "Export Tongji YAML"):
            self.host.handle_config_action(command)
            self.host.handle_calibration_action.assert_called_with(command)
        for command, single in (("Preview FX", False), ("Test Elimination", True)):
            self.host.handle_config_action(command)
            self.host.preview_elimination_fx.assert_called_with(single=single)

    def test_editor_commands_precede_student_extension(self):
        routes = {
            "Open Keybind Manager": ("rm_client.ui.jello_keybinds.open_keyboard_manager", {}),
            "Open Crosshair Editor": ("rm_client.cs2_crosshair.open_crosshair_editor", {}),
            "Crosshair Import Details": ("rm_client.cs2_crosshair.show_import_details", {}),
            "Import Crosshair Code": ("rm_client.cs2_crosshair.import_dialog", {"file": False}),
            "Import Crosshair File": ("rm_client.cs2_crosshair.import_dialog", {"file": True}),
        }
        self.host.handle_student_config_action.return_value = True
        for command, (target, kwargs) in routes.items():
            with self.subTest(command=command), patch(target) as handler:
                self.host.handle_config_action(command)
                handler.assert_called_once_with(self.host, **kwargs)
        self.host.handle_student_config_action.assert_not_called()

    def test_student_extension_can_consume_builtin_or_unknown_command(self):
        self.host.handle_student_config_action.return_value = True
        for command in ("Save Current", "Trigger E-Stop", "unregistered extension"):
            self.host.handle_config_action(command)
        self.host.save_current_config.assert_not_called()
        self.host.backend.trigger_estop.assert_not_called()
        self.host.toast.assert_not_called()

    def test_unknown_and_near_match_commands_are_silent(self):
        for command in ("", "save current", " Save Current", "Preview Arbitrary", "Import Fire Sound"):
            self.host.handle_config_action(command)
        self.host.toast.assert_not_called()
        self.host.ui_audio.preview.assert_not_called()
        self.host.import_custom_ui_sound.assert_not_called()
        self.host.update_backend_readiness.assert_not_called()

    def test_exact_qualification_names_route_through_the_public_boundary(self):
        commands = (
            "New Qualification Session", "Reset Qualification Session", "Run Preflight", "Run Video Test",
            "Run KMC Test", "Record DT7 Safety Test", "Run Economy Send Test", "Record E-Stop Test",
            "Observe Firmware Zero", "Record Firmware Zero Test", "Run Loss Recovery Test", "Record Hero Test",
            "Record Economy Test", "Review PRM1 Vision Schema", "Run PRM1 Vision Bench",
            "Review PRM1 Spin Schema", "Run PRM1 Spin Bench", "Start Match Soak", "Stop Match Soak",
            "Export Evidence", "Field Sign-off", "Arm Match Control", "Lock Match Control",
        )
        with patch.object(self.host, "handle_qualification_action") as qualification:
            for command in commands:
                self.host.handle_config_action(command)
                qualification.assert_called_with(command)
            self.assertEqual(qualification.call_count, 23)

    def test_lob_pitch_recomputes_residual_after_installing_bounded_offset(self):
        state = self.host.state
        state.native_gimbal_online, state.native_telemetry_at = True, 100.0
        state.native_pitch_deg = 8.0
        offset = self.host.registry.get("HeroLob").get_setting("Pitch Offset")
        previous = float(offset.value)
        with patch(f"{MODULE}.time.perf_counter", return_value=100.1), \
             patch(f"{MODULE}.HeroLobPlanner.preview_from_state", side_effect=[
                 (NS(reachable=True, pitch_deg=5.0), None), (NS(pitch_deg=8.0), None)]):
            self.host.handle_config_action("Calibrate Lob Pitch")
        self.assertEqual(offset.value, previous + 3)
        self.assertTrue(state.hero_lob_pitch_calibrated)
        with patch(f"{MODULE}.time.perf_counter", return_value=100.1), \
             patch(f"{MODULE}.HeroLobPlanner.preview_from_state", return_value=(NS(reachable=False, pitch_deg=5), None)):
            self.host.handle_config_action("Calibrate Lob Pitch")
        self.assertEqual(offset.value, previous + 3)
        self.assertFalse(state.hero_lob_pitch_calibrated)

    def test_sound_commands_use_only_three_exact_slots(self):
        for slot in ("enable", "disable", "invalid"):
            self.host.handle_config_action(f"Import {slot.title()} Sound")
            self.host.import_custom_ui_sound.assert_called_with(slot)
            self.host.handle_config_action(f"Preview {slot.title()}")
            self.assertEqual(self.host.ui_audio.preview.call_args.args[0], slot)
            self.assertEqual(self.host.ui_audio.preview.call_args.args[1:], (45.0, "Polar UI", {}))

    def test_estop_revokes_before_input_cleanup_and_reports_failures(self):
        events = []
        self.host.control.reset_inputs.side_effect = lambda: events.append("reset")
        self.host.backend.trigger_estop.side_effect = lambda: (events.append("estop") or (False, "transport"))
        self.host.control.clear_pending.side_effect = lambda: events.append("clear")
        self.host.handle_config_action("Trigger E-Stop")
        self.assertEqual(events, ["estop", "reset", "clear"])
        self.host.toast.assert_called_once_with("E-STOP ACTIVE (transport)")
        self.host.backend.trigger_estop.side_effect = OSError("synthetic error")
        self.host.handle_config_action("Trigger E-Stop")
        self.assertIn("synthetic error", self.host.toast.call_args.args[0])
        self.assertEqual(self.host.toast.call_args.kwargs, {"kind": "warning"})
        self.host.handle_config_action("Release E-Stop")
        self.host.backend.release_estop.assert_called_once_with()

    def test_live_angle_arm_requires_exact_phrase_and_rechecks_after_dialog(self):
        link = self.host.backend.controller_angles
        link.config.mock_output = False
        for answer in (("arm", True), ("ARM", False)):
            with patch(f"{MODULE}.QInputDialog.getText", return_value=answer):
                self.host.handle_config_action("Arm Angle Forward")
        link.arm.assert_not_called()
        self.host.backend._sync_controller_angles.reset_mock()
        with patch(f"{MODULE}.QInputDialog.getText", return_value=(" ARM ", True)):
            self.host.handle_config_action("Arm Angle Forward")
        self.assertEqual(self.host.backend._sync_controller_angles.call_count, 2)
        link.arm.assert_called_once_with()
        self.host.handle_config_action("Disarm Angle Forward")
        link.disarm.assert_called_once_with()
        self.host.handle_config_action("Show Angle Status")
        self.assertIn("真发=2 mock=3", self.host.toast.call_args.args[0])

    def test_lob_stale_calibration_preserves_prior_flag_and_always_repaints(self):
        self.host.state.hero_lob_yaw_calibrated = True
        self.host.handle_config_action("Calibrate Lob Yaw")
        self.assertTrue(self.host.state.hero_lob_yaw_calibrated)
        self.host.toast.assert_called_with("Yaw calibration blocked: fresh gimbal telemetry required")
        self.host.frame_scheduler.force.assert_called_once_with()

    def test_lob_yaw_accepts_bounded_correction_and_invalidates_out_of_range(self):
        state = self.host.state
        state.native_gimbal_online, state.native_telemetry_at = True, 100.0
        offset = self.host.registry.get("HeroLob").get_setting("Yaw Offset")
        state.native_yaw_deg = 3.0
        with patch(f"{MODULE}.time.perf_counter", return_value=100.1):
            self.host.handle_config_action("Calibrate Lob Yaw")
            self.assertAlmostEqual(offset.value, 3.0)
            self.assertTrue(state.hero_lob_yaw_calibrated)
            state.native_yaw_deg = float(offset.max_value) + 1
            self.host.handle_config_action("Calibrate Lob Yaw")
        self.assertEqual(offset.value, 3.0)
        self.assertFalse(state.hero_lob_yaw_calibrated)

    def test_assisted_lob_arm_is_preview_only_and_missing_gates_block(self):
        module = self.host.registry.get("HeroLob")
        module.enabled = True
        self.host.handle_config_action("Arm Assisted Lob")
        self.assertTrue(self.host.state.hero_lob_armed)
        self.assertEqual(self.host.state.hero_lob_status, "ARMED PREVIEW ONLY")
        self.host.hero_lob_preview.assert_called_once_with(announce=False)
        self.host.backend.arm_match_control.assert_not_called()
        module.enabled = False
        self.host.handle_config_action("Arm Assisted Lob")
        self.assertFalse(self.host.state.hero_lob_armed)
        self.host.handle_config_action("Invalidate Relocalization")
        self.assertFalse(self.host.state.hero_lob_relocalization_valid)
        self.host.handle_config_action("Disarm Assisted Lob")
        self.assertEqual(self.host.state.hero_lob_status, "DISARMED")


class QualificationCommandTests(unittest.TestCase):
    def setUp(self):
        self.host = CommandHost()
        self.qualification = self.host.backend.qualification

    def assert_refreshed(self):
        self.host.update_backend_readiness.assert_called_once_with(force=True)
        self.host.frame_scheduler.force.assert_called_once_with()

    def test_unknown_qualification_still_refreshes_but_does_not_record(self):
        self.host.handle_qualification_action("unknown")
        self.assert_refreshed()
        self.qualification.record_automatic.assert_not_called()

    def test_new_session_cancellation_has_no_mutation_or_refresh(self):
        for answers in ([("", False)], [("operator", True), ("", False)]):
            host = CommandHost()
            with patch(f"{MODULE}.QInputDialog.getText", side_effect=answers):
                host.handle_qualification_action("New Qualification Session")
            host.backend.qualification.new_session.assert_not_called()
            host.backend.lock_control.assert_not_called()
            host.update_backend_readiness.assert_not_called()
            host.frame_scheduler.force.assert_not_called()

    def test_new_session_keeps_raw_parameters_for_authoritative_validation(self):
        with patch(f"{MODULE}.QInputDialog.getText", side_effect=[(" operator ", True), ("hash", True)]):
            self.host.handle_qualification_action("New Qualification Session")
        self.qualification.new_session.assert_called_once_with(robot_id="3", firmware_hash="hash",
            match_mode="RMUC7v7", video_source="udp", operator=" operator ")
        self.host.backend.lock_control.assert_called_once_with("new qualification session")
        self.assertEqual(self.host._qualification_runtime_baseline, {"captured": True})
        self.assert_refreshed()

    def test_qualification_exception_locks_reports_and_refreshes(self):
        self.qualification.reset.side_effect = ValueError("disk unavailable")
        self.host.handle_qualification_action("Reset Qualification Session")
        self.host.backend.lock_control.assert_called_once_with("qualification error: disk unavailable")
        self.host.toast.assert_called_once_with("Qualification blocked: disk unavailable")
        self.assert_refreshed()

    def test_video_evidence_requires_full_duration_fresh_frames_and_session_age(self):
        cases = ((60, 160, 20, True, "udp", True), (59.9, 160, 20, True, "udp", False),
                 (60, 159.9, 20, True, "udp", False), (60, 160, 10, True, "udp", False),
                 (60, 160, 20, False, "udp", False), (60, 160, 20, True, "camera", False))
        for duration, now, frames, fresh, source, passed in cases:
            with self.subTest(duration=duration, now=now, frames=frames, source=source):
                host = CommandHost()
                host.readiness_video_source.return_value = source
                host.backend.stats.return_value["udp"].update(continuous_decoded_seconds=duration,
                                                             decoded_frames=frames, decoded_connected=fresh)
                with patch(f"{MODULE}.time.time", return_value=now):
                    host.handle_qualification_action("Run Video Test")
                record = host.backend.qualification.record_automatic.call_args
                self.assertEqual(record.args, ("codec_field_test", passed))
                self.assertEqual(record.kwargs["threshold"], {"seconds": 60, "fresh": True, "new_frames": True})

    def test_runtime_evidence_is_baseline_bound_and_never_arms(self):
        self.host.state.backend_stats["firmware_zero_observed_ms"] = 500.0
        self.host.state.deploy_status_revision = 4
        self.host.state.deploy_status_at = 11
        self.host.state.deploy_mode = 0
        self.host.state.economy_status_revision = 3
        self.host.economy_bridge.latest = NS(status="succeeded", request=NS(mock=False, timestamp=101))
        cases = {
            "Record E-Stop Test": "estop_robot_test", "Record Firmware Zero Test": "firmware_loss_zero_test",
            "Run Loss Recovery Test": "loss_recovery_test", "Record Hero Test": "hero_deploy_status_test",
            "Record Economy Test": "economy_live_test",
        }
        for action, key in cases.items():
            self.host.handle_qualification_action(action)
            self.assertEqual(self.qualification.record_automatic.call_args.args, (key, True))
        self.host.backend.safety.estop_at = 10
        self.host.handle_qualification_action("Record E-Stop Test")
        self.assertEqual(self.qualification.record_automatic.call_args.args, ("estop_robot_test", False))
        self.host.backend.arm_match_control.assert_not_called()

    def test_firmware_observation_requires_new_estop_and_exact_manual_phrase(self):
        for answer in (("zero observed", True), ("ZERO OBSERVED", False)):
            with patch(f"{MODULE}.QInputDialog.getText", return_value=answer):
                self.host.handle_qualification_action("Observe Firmware Zero")
        self.host.backend.record_firmware_zero_observation.assert_not_called()
        self.host.backend.record_firmware_zero_observation.return_value = 17.0
        with patch(f"{MODULE}.QInputDialog.getText", return_value=(" ZERO OBSERVED ", True)):
            self.host.handle_qualification_action("Observe Firmware Zero")
        self.host.backend.record_firmware_zero_observation.assert_called_once_with()
        self.host.update_backend_readiness.assert_not_called()
        self.host.frame_scheduler.force.assert_not_called()

    def test_prm1_second_prompt_cancel_records_failed_evidence_and_locks(self):
        for command, key in (("Review PRM1 Vision Schema", "prm1_vision_schema_review"),
                             ("Run PRM1 Vision Bench", "prm1_vision_bench_test"),
                             ("Review PRM1 Spin Schema", "prm1_spin_schema_review"),
                             ("Run PRM1 Spin Bench", "prm1_spin_bench_test")):
            with patch(f"{MODULE}.QInputDialog.getText", side_effect=[("reviewer", True), ("", False)]):
                self.host.handle_qualification_action(command)
            record = self.qualification.record_supervised_prm1.call_args
            self.assertEqual(record.args, (key, False))
            self.assertEqual(record.kwargs["measured"], {"active_and_watchdog_observed": False})
            self.host.backend.lock_control.assert_called_with("PRM1 evidence recorded; explicit re-arm required")

    def test_dt7_observation_requires_live_writer_probe_and_unchanged_identity(self):
        self.host.backend._dt7_link = None
        self.host.handle_qualification_action("Record DT7 Safety Test")
        self.qualification.record_supervised_dt7_safety.assert_not_called()
        writer = NS(mock_output=False, profile=object())
        self.host.backend._dt7_link = writer
        self.qualification.test_passed.return_value = True
        self.qualification.record_supervised_dt7_safety.return_value = NS(status="PASS")
        with patch("rm_client.backend.dt7_link.profile_digest", return_value="digest"), \
             patch(f"{MODULE}.QInputDialog.getText", side_effect=[("observer", True),
                   ("ESTOP LOSS RECONNECT OBSERVED", True)]), \
             patch(f"{MODULE}.QInputDialog.getDouble", side_effect=[(15, True), (30, True)]):
            self.host.handle_qualification_action("Record DT7 Safety Test")
        self.qualification.record_supervised_dt7_safety.assert_called_once_with(observer="observer",
            firmware_hash="a" * 64, profile_sha256="digest",
            measured={"physical_estop_observed": True, "physical_loss_zero_observed": True,
                      "reconnect_stayed_neutral": True, "estop_zero_ms": 15, "loss_zero_ms": 30})
        self.host.backend.lock_control.assert_called_with("DT7 physical safety observation; no automatic arm")
        self.host.update_backend_readiness.assert_not_called()
        self.qualification.record_supervised_dt7_safety.reset_mock()
        with patch("rm_client.backend.dt7_link.profile_digest", side_effect=["before", "changed"]), \
             patch(f"{MODULE}.QInputDialog.getText", side_effect=[("observer", True),
                   ("ESTOP LOSS RECONNECT OBSERVED", True)]), \
             patch(f"{MODULE}.QInputDialog.getDouble", side_effect=[(15, True), (30, True)]):
            self.host.handle_qualification_action("Record DT7 Safety Test")
        self.qualification.record_supervised_dt7_safety.assert_not_called()
        self.assertIn("不记录过期测量", self.host.toast.call_args.args[0])

    def test_preflight_pass_comes_from_runner_and_retains_complete_measurements(self):
        result = NS(passed=False, as_dict=Mock(return_value={"failure": "codec"}))
        self.host.backend.udp.running = True
        with patch("rm_client.preflight.run_preflight", return_value=result) as run:
            self.host.handle_qualification_action("Run Preflight")
        run.assert_called_once_with(self.host.paths, official=False, codec_selftest=True,
                                    active_udp_owned_by_client=True)
        self.qualification.record_automatic.assert_called_once_with("preflight", False,
            measured={"failure": "codec"}, threshold="all startup checks pass")
        self.assert_refreshed()

    def test_export_and_signoff_cancel_skip_write_but_keep_generic_refresh(self):
        with patch(f"{MODULE}.QFileDialog.getExistingDirectory", return_value=""):
            self.host.handle_qualification_action("Export Evidence")
        self.qualification.store.export_current.assert_not_called()
        with patch(f"{MODULE}.QInputDialog.getText", return_value=("", False)):
            self.host.handle_qualification_action("Field Sign-off")
        self.qualification.sign_off.assert_not_called()
        self.assertEqual(self.host.update_backend_readiness.call_count, 2)
        with patch(f"{MODULE}.QInputDialog.getText", return_value=("lead", True)):
            self.host.handle_qualification_action("Field Sign-off")
        self.qualification.sign_off.assert_called_once_with("lead")
        self.qualification.store.export_current.return_value = Path("/inert/evidence.json")
        with patch(f"{MODULE}.QFileDialog.getExistingDirectory", return_value="/inert"):
            self.host.handle_qualification_action("Export Evidence")
        self.qualification.store.export_current.assert_called_once_with(Path("/inert"))
        self.host.toast.assert_called_with("Evidence exported: evidence.json")

    def test_async_test_delegates_do_not_run_generic_refresh(self):
        self.host.handle_qualification_action("Run KMC Test")
        self.host.handle_qualification_action("Run Economy Send Test")
        self.host.start_kmc_qualification_test.assert_called_once_with()
        self.host.start_economy_qualification_test.assert_called_once_with()
        self.host.update_backend_readiness.assert_not_called()

    def test_soak_requires_at_least_seven_minutes_and_actual_started_timer(self):
        self.host.state.match_duration_sec = 300
        with patch(f"{MODULE}.time.perf_counter", return_value=100):
            self.host.handle_qualification_action("Start Match Soak")
        with patch(f"{MODULE}.time.perf_counter", return_value=519.9):
            self.host.handle_qualification_action("Stop Match Soak")
        self.assertEqual(self.qualification.record_automatic.call_args.args, ("latency_soak_test", False))
        with patch(f"{MODULE}.time.perf_counter", return_value=520):
            self.host.handle_qualification_action("Stop Match Soak")
        self.assertEqual(self.qualification.record_automatic.call_args.args, ("latency_soak_test", True))
        self.assertEqual(self.qualification.record_automatic.call_args.kwargs["threshold"], {"minimum_seconds": 420.0})

    def test_arm_uses_current_identity_and_lock_revokes_before_fallible_cleanup(self):
        self.host.handle_qualification_action("Arm Match Control")
        self.host.backend.arm_match_control.assert_called_once_with(robot_id="3", firmware_hash="a" * 64,
            match_mode="RMUC7v7", video_source="udp")
        self.host.toast.assert_called_with("Arm blocked: missing evidence")
        events = []
        self.host.control.reset_inputs.side_effect = lambda: events.append("inputs")
        self.host.backend.mqtt.publish_estop_zero.side_effect = lambda: (events.append("zero") or (True, ""))
        self.host.backend.lock_control.side_effect = lambda reason: events.append(reason)
        self.host.handle_qualification_action("Lock Match Control")
        self.assertEqual(events, ["operator lock", "zero", "inputs"])


if __name__ == "__main__":
    unittest.main()
