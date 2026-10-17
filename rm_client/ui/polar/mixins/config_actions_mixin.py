"""UI adapters for configuration effects, module actions, and qualifications.

Setting changes share a stable-identity effect plan across direct GUI controls
and the settings center. Runtime effects stay explicit here; validation and
transaction ownership live in the configuration package.
"""

from __future__ import annotations

import json
from enum import Enum
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QInputDialog

from ...jello_locale import chinese_enabled, ui_text

# 相对导入层级：rm_client/ui/polar/mixins/ → ... = rm_client.ui、.... = rm_client
from ....combat_events import CombatEvent
from ....configuration.commands import (
    ConfigCommand, ConfigCommandKind, QualificationCommand,
    resolve_config_command, resolve_qualification_command,
)
from ....configuration.effects import SettingEffect, effects_for_setting
from ....configuration.registry import setting_identity
from ....control import (build_dt7_selftest_report, evaluate_dt7_hold,
                         evaluate_dt7_selftest, evaluate_radio_km_selftest,
                         KEYBOARD_BITS)
from ....emergency_actions import request_control_lock, request_emergency_stop
from ....hero_lob import HeroLobPlanner
from ....model import PRIMARY_GUI_MODE, Module, Setting
from ....qualification_observations import (
    QualificationEvidence, economy_evidence, estop_evidence, firmware_zero_evidence,
    hero_evidence, recovery_evidence, soak_evidence, video_evidence,
)
from ...overlay_coordinator import OverlayKind

__all__ = [
    "ConfigActionsMixin",
]


class QualificationRefresh(Enum):
    """Explicit UI completion behavior, including legacy cancellation paths."""
    SKIP = "skip"
    REQUIRED = "required"


class ConfigActionsMixin:
    """D16 动作处理 / 配置动作 / 资格赛（设置变更回调 + 配置动作 + KMC 资格赛）。"""

    def after_setting_changed(self, setting: Setting) -> None:
        """Execute the same post-commit effects for every settings editor."""
        if self.reject_pending_configuration_edit():
            return
        plan = effects_for_setting(setting_identity(self.registry, setting), setting.value)
        self.invalidate_gui_cache()
        handlers = {
            SettingEffect.SYNC_CONTROLLER_ANGLES: self._sync_controller_angle_setting,
            SettingEffect.CLOSE_OUTPUT_GATE: self._close_disabled_output_gate,
            SettingEffect.SYNC_CONTROL_BINDINGS: self._sync_control_binding_setting,
            SettingEffect.INVALIDATE_HUD_PALETTE: self._invalidate_hud_palette_setting,
            SettingEffect.APPLY_SCENARIO: self._apply_scenario_setting,
            SettingEffect.APPLY_MATCH_MODE: self._apply_match_mode_setting,
            SettingEffect.APPLY_ALLIANCE: self._apply_alliance_setting,
            SettingEffect.APPLY_LEGACY_ALLIANCE: self._apply_legacy_alliance_setting,
            SettingEffect.APPLY_ROBOT_PROFILE: self._apply_robot_profile_setting,
            SettingEffect.APPLY_GUI_MODE: self._apply_gui_mode_setting,
            SettingEffect.APPLY_COMBAT_PRESET: self._apply_combat_preset_setting,
            SettingEffect.ANNOUNCE_RIGHT_DISPLAY: self._announce_right_display_setting,
            SettingEffect.SAVE_STARTUP_PREFERENCE: self._save_startup_preference_setting,
            SettingEffect.RELOAD_PROFILE: self._reload_profile_setting,
            SettingEffect.PRELOAD_ELIMINATION_SOUNDS: self._preload_elimination_setting,
            SettingEffect.SYNC_QUICK_ACTION_WHEEL: self._sync_wheel_setting,
            SettingEffect.APPLY_SPIN_SETTING: self._apply_spin_setting,
            SettingEffect.ANNOUNCE_RADIO_EGRESS: self._announce_radio_egress_setting,
        }
        for effect in plan.effects:
            handlers[effect](setting)
        if plan.auto_save:
            self.auto_save_config()

    def _sync_controller_angle_setting(self, setting: Setting) -> None:
        self.backend._sync_controller_angles(self.registry)

    def _close_disabled_output_gate(self, setting: Setting) -> None:
        # A falling gate neutralizes pending inputs even outside a modal menu.
        self.backend.lock_control("RobotLink output gate disabled")
        try:
            self.control.release_all(self.state, self.registry)
        finally:
            self.control.clear_pending()

    def _sync_control_binding_setting(self, setting: Setting) -> None:
        self.sync_control_binds()

    def _invalidate_hud_palette_setting(self, setting: Setting) -> None:
        self._hud_palette_cache = None

    def _apply_scenario_setting(self, setting: Setting) -> None:
        self.apply_scenario(str(setting.value), close_home=False)

    def _apply_match_mode_setting(self, setting: Setting) -> None:
        self.apply_match_mode(str(setting.value), close_home=False)

    def _apply_robot_profile_setting(self, setting: Setting) -> None:
        self.apply_robot_profile(self.current_robot_profile_key(), close_home=False, announce=False)

    def _apply_alliance_setting(self, setting: Setting) -> None:
        self._apply_robot_profile_setting(setting)
        self.vision_kill_detector.reset()
        self.toast(f"阵营：{setting.value}")

    def _apply_legacy_alliance_setting(self, setting: Setting) -> None:
        alliance = self.registry.get("AllianceProfile")
        side = alliance.get_setting("Side") if alliance else None
        if side is not None:
            side.value = setting.value
        self._apply_robot_profile_setting(setting)
        self.vision_kill_detector.reset()

    def _apply_gui_mode_setting(self, setting: Setting) -> None:
        self.mode_manager.set_mode(str(setting.value))
        self.module_expanded.clear()
        self.play_gui_transition_sound("expand")
        self.toast(f"GUI 模式：{setting.value}")

    def _apply_combat_preset_setting(self, setting: Setting) -> None:
        minecraft = self.registry.get("MinecraftCombatHUD")
        selected = str(setting.value) == "Minecraft 1.21.11"
        if minecraft is not None and minecraft.enabled != selected:
            self.registry.toggle_module(minecraft, source="preset")
        self.invalidate_transient_overlay_scene()

    def _announce_right_display_setting(self, setting: Setting) -> None:
        self.toast(f"右侧 HUD：{setting.value}")

    def _save_startup_preference_setting(self, setting: Setting) -> None:
        try:
            self.config_store.save_startup_preferences(self.current_profile(), bool(setting.value))
        except OSError as exc:
            self.toast(f"启动设置保存失败：{exc}", kind="warning")

    def _reload_profile_setting(self, setting: Setting) -> None:
        self.reload_current_config()

    def _preload_elimination_setting(self, setting: Setting) -> None:
        self.kill_fx.preload_preset_sounds(str(setting.value))

    def _sync_wheel_setting(self, setting: Setting) -> None:
        self.sync_quick_action_wheel_settings()

    def _apply_spin_setting(self, setting: Setting) -> None:
        spin = self.registry.get("SpinMode")
        mock_setting = spin.get_setting("Mock Output")
        if setting is mock_setting and not bool(setting.value) and spin.enabled:
            # Arming live transport requires a second explicit enable action.
            self.registry.toggle_module(spin, source="live_output_armed")
            self.toast("Spin live output armed; toggle SpinMode again to enable")
            return
        # Switching to Mock clears the live robot-side latch once.
        force_disable = setting is mock_setting and bool(setting.value)
        self.publish_spin_control_state(
            False if force_disable else spin.enabled,
            source=f"setting:{setting.name}",
            force_live_disable=force_disable,
        )

    def _announce_radio_egress_setting(self, setting: Setting) -> None:
        link = self.registry.get("RobotLink")
        missing = []
        if not link.enabled:
            missing.append("启用模块 RobotLink")
        drive = link.get_setting("Allow Drive")
        if drive is None or not bool(drive.value):
            missing.append("勾选 Allow Drive")
        if bool(setting.value) and missing:
            self.toast("无线键鼠出口已开，但还差：" + "、".join(missing))
        elif bool(setting.value):
            self.toast("无线键鼠出口已开（图传 0x0311）；"
                       "记得把键鼠来源选成\u201c自定义客户端\u201d，"
                       "官方选手端同时开着时只认一个来源")
        else:
            self.toast("无线键鼠出口已关")

    def handle_config_action(self, action_name: str) -> None:
        command = resolve_config_command(action_name)
        # Editors historically bypass student extensions; all other commands,
        # including unknown extension IDs, remain available to that boundary.
        if command is not None and command.kind is ConfigCommandKind.EDITOR:
            self._open_configuration_editor(command.name)
            return
        if self.handle_student_config_action(action_name) or command is None:
            return
        self._execute_config_command(command)

    def _execute_config_command(self, command: ConfigCommand) -> None:
        handlers = {
            ConfigCommandKind.QUALIFICATION: self.handle_qualification_action,
            ConfigCommandKind.SAFETY: self._apply_safety_command,
            ConfigCommandKind.DIAGNOSTIC: self._run_link_diagnostic,
            ConfigCommandKind.ANGLES: self._apply_angle_command,
            ConfigCommandKind.PROFILE: self._apply_profile_command,
            ConfigCommandKind.CALIBRATION: self.handle_calibration_action,
            ConfigCommandKind.ELIMINATION: self._preview_elimination_command,
            ConfigCommandKind.DEPLOY: self._open_deploy_command,
            ConfigCommandKind.LOB: self._apply_lob_command,
        }
        if command.kind is ConfigCommandKind.SOUND_IMPORT:
            self.import_custom_ui_sound(command.sound_slot)
        elif command.kind is ConfigCommandKind.SOUND_PREVIEW:
            self._preview_ui_sound(command.sound_slot)
        else:
            handlers[command.kind](command.name)

    def _open_configuration_editor(self, action_name: str) -> None:
        if action_name == "Open Keybind Manager":
            from ...jello_keybinds import open_keyboard_manager
            open_keyboard_manager(self)
            return
        from ....cs2_crosshair import import_dialog, open_crosshair_editor, show_import_details
        if action_name == "Open Crosshair Editor":
            open_crosshair_editor(self)
        elif action_name == "Crosshair Import Details":
            show_import_details(self)
        else:
            import_dialog(self, file=action_name == "Import Crosshair File")

    def _apply_safety_command(self, action_name: str) -> None:
        if action_name == "Trigger E-Stop":
            result = request_emergency_stop(self.backend, self.control)
            if result.failures:
                self.toast(result.message, kind="warning")
            else:
                self.toast(result.message)
        else:
            self.backend.release_estop()
            self.toast("E-STOP RELEASED — verify robot before control")

    def _run_link_diagnostic(self, action_name: str) -> None:
        handlers = {
            "Run DT7 Link Selftest": self.run_dt7_link_selftest,
            "Rescan Serial Ports": self.rescan_serial_ports,
            "Run Wireless KM Selftest": self.run_radio_km_selftest,
        }
        handlers[action_name]()

    def _apply_angle_command(self, action_name: str) -> None:
        status = self.backend._sync_controller_angles(self.registry)
        link = self.backend.controller_angles
        if action_name == "Disarm Angle Forward":
            link.disarm()
            self.toast("角度转发已锁定；车端独立超时仍必须有效")
        elif action_name == "Arm Angle Forward":
            if not link.config.mock_output:
                answer, accepted = QInputDialog.getText(
                    self, "实体控制器角度转发", "确认机械臂已支撑隔离、0x0302 接收与独立超时已验证。\n"
                    "本次会向官方选手端发送真实关节目标。输入 ARM 授权本次进程：")
                if not accepted or answer.strip() != "ARM":
                    return
            # The dialog never authorizes cached input. Recheck gates and wait
            # for another fresh source frame after the operator's confirmation.
            self.backend._sync_controller_angles(self.registry)
            ok, why = link.arm()
            self.toast(("角度转发已授权：" if ok else "角度转发未授权：") + why)
        else:
            self.toast(f"角度有效={status['valid']} 已授权={status['armed']} "
                       f"真发={status['forwarded']} mock={status['mock_forwarded']}；"
                       f"{status['last_error']}")

    def _apply_profile_command(self, action_name: str) -> None:
        handlers = {
            "Save Current": self.save_current_config,
            "Reload Profile": self.reload_current_config,
            "Import Config": self.import_config_from_dialog,
            "Import Wallpaper": self.import_wallpaper_from_dialog,
            "Reset Wallpaper": self.reset_wallpaper_profile,
            "Restore Compact HUD": self.restore_compact_hud,
        }
        handlers[action_name]()

    def _preview_elimination_command(self, action_name: str) -> None:
        self.preview_elimination_fx(single=action_name == "Test Elimination")

    def _open_deploy_command(self, action_name: str) -> None:
        self.open_hero_deploy_interface()

    def _preview_ui_sound(self, slot: str) -> None:
        audio = self.registry.get("AudioFeedback")
        self.ui_audio.preview(
            slot, self.get_setting_number(audio, "UI Volume", 45.0),
            self.setting_mode(audio, "Sound Theme", "Polar UI"),
            self.custom_ui_sound_selection(audio),
        )

    def _apply_lob_command(self, action_name: str) -> None:
        module = self.registry.get("HeroLob")
        timeout = self.get_setting_number(module, "Telemetry Timeout", 0.50)
        telemetry_fresh = bool(
            self.state.native_gimbal_online and self.state.native_telemetry_at > 0.0
            and time.perf_counter() - self.state.native_telemetry_at <= timeout
        )
        if action_name == "Calibrate Lob Yaw":
            self._calibrate_lob_yaw(module, telemetry_fresh)
        elif action_name == "Calibrate Lob Pitch":
            self._calibrate_lob_pitch(module, telemetry_fresh)
        elif action_name == "Confirm Relocalization":
            self._confirm_lob_relocalization(telemetry_fresh)
        elif action_name == "Invalidate Relocalization":
            self.state.hero_lob_relocalization_valid = False
            self.state.hero_lob_armed = False
            self.state.hero_lob_status = "BLOCKED: relocalization invalid"
            self.toast(self.state.hero_lob_status)
        elif action_name == "Preview Lob":
            self.hero_lob_preview()
        elif action_name == "Arm Assisted Lob":
            self._arm_lob_preview(module)
        else:
            self.state.hero_lob_armed = False
            self.state.hero_lob_status = "DISARMED"
            self.toast("Hero lob disarmed")
        self.frame_scheduler.force()

    @staticmethod
    def _lob_offset_in_range(offset: Setting | None, candidate: float) -> bool:
        return bool(offset is not None and offset.min_value is not None and offset.max_value is not None
                    and float(offset.min_value) <= candidate <= float(offset.max_value))

    def _calibrate_lob_yaw(self, module: Module | None, telemetry_fresh: bool) -> None:
        if not telemetry_fresh:
            self.toast("Yaw calibration blocked: fresh gimbal telemetry required")
            return
        bearing = self.get_setting_number(module, "Bearing", 0.0)
        offset = module.get_setting("Yaw Offset") if module else None
        current_offset = float(offset.value) if offset is not None else 0.0
        correction = HeroLobPlanner.normalize_degrees(self.state.native_yaw_deg - (bearing + current_offset))
        candidate = current_offset + correction
        if not self._lob_offset_in_range(offset, candidate):
            self.state.hero_lob_yaw_calibrated = False
            self.toast("Yaw calibration blocked: correction exceeds offset range")
            return
        offset.value = candidate
        tolerance = self.get_setting_number(module, "Yaw Calibration Tolerance", 1.0)
        residual = abs(HeroLobPlanner.normalize_degrees(bearing + float(offset.value) - self.state.native_yaw_deg))
        self.state.hero_lob_yaw_calibrated = residual <= tolerance
        self.toast(f"Hero lob yaw calibrated {float(offset.value):+.2f} deg"
                   if self.state.hero_lob_yaw_calibrated else f"Yaw calibration residual {residual:.2f} deg")

    def _calibrate_lob_pitch(self, module: Module | None, telemetry_fresh: bool) -> None:
        if not telemetry_fresh:
            self.toast("Pitch calibration blocked: fresh gimbal telemetry required")
            return
        solution, _ = HeroLobPlanner.preview_from_state(
            self.state, module, hero_range_profile=self.hero_range_profile_selected())
        offset = module.get_setting("Pitch Offset") if module else None
        current_offset = float(offset.value) if offset is not None else 0.0
        candidate = current_offset + (self.state.native_pitch_deg - solution.pitch_deg)
        if not solution.reachable or not self._lob_offset_in_range(offset, candidate):
            self.state.hero_lob_pitch_calibrated = False
            self.toast("Pitch calibration blocked: unreachable or offset out of range")
            return
        offset.value = candidate
        tolerance = self.get_setting_number(module, "Pitch Calibration Tolerance", 1.5)
        corrected, _ = HeroLobPlanner.preview_from_state(
            self.state, module, hero_range_profile=self.hero_range_profile_selected())
        residual = abs(self.state.native_pitch_deg - corrected.pitch_deg)
        self.state.hero_lob_pitch_calibrated = residual <= tolerance
        self.toast(f"Hero lob pitch calibrated {float(offset.value):+.2f} deg"
                   if self.state.hero_lob_pitch_calibrated else f"Pitch calibration residual {residual:.2f} deg")

    def _confirm_lob_relocalization(self, telemetry_fresh: bool) -> None:
        if telemetry_fresh and self.state.hero_lob_yaw_calibrated and self.state.hero_lob_pitch_calibrated:
            self.state.hero_lob_relocalization_valid = True
            self.toast("Hero lob relocalization confirmed")
        else:
            self.toast("Relocalization blocked: fresh telemetry and both calibrations required")

    def _arm_lob_preview(self, module: Module | None) -> None:
        solution, readiness = self.hero_lob_preview(announce=False)
        if module is not None and module.enabled and solution.reachable and readiness.ready:
            self.state.hero_lob_armed = True
            self.state.hero_lob_status = "ARMED PREVIEW ONLY"
            self.toast("Hero lob armed (decision support only; no automatic fire)")
        else:
            self.state.hero_lob_armed = False
            self.state.hero_lob_status = readiness.summary
            self.toast(self.state.hero_lob_status)

    def handle_qualification_action(self, action_name: str) -> None:
        """Run qualified commands; failure always revokes control authority."""
        try:
            refresh = self._execute_qualification_command(resolve_qualification_command(action_name))
        except Exception as exc:
            self.backend.lock_control(f"qualification error: {exc}")
            self.toast(f"Qualification blocked: {exc}")
            refresh = QualificationRefresh.REQUIRED
        if refresh is QualificationRefresh.REQUIRED:
            self.update_backend_readiness(force=True)
            self.frame_scheduler.force()

    def _execute_qualification_command(self, command: QualificationCommand | None) -> QualificationRefresh:
        if command in {
            QualificationCommand.ESTOP, QualificationCommand.FIRMWARE_ZERO,
            QualificationCommand.LOSS_RECOVERY, QualificationCommand.HERO, QualificationCommand.ECONOMY,
        }:
            self._record_runtime_qualification(command)
            return QualificationRefresh.REQUIRED
        if command in {
            QualificationCommand.PRM1_VISION_SCHEMA, QualificationCommand.PRM1_VISION_BENCH,
            QualificationCommand.PRM1_SPIN_SCHEMA, QualificationCommand.PRM1_SPIN_BENCH,
        }:
            return self._record_prm1_qualification(command)
        handlers = {
            QualificationCommand.NEW_SESSION: self._new_qualification_session,
            QualificationCommand.RESET_SESSION: self._reset_qualification_session,
            QualificationCommand.PREFLIGHT: self._record_qualification_preflight,
            QualificationCommand.VIDEO: self._record_qualification_video,
            QualificationCommand.KMC: self._start_qualification_kmc,
            QualificationCommand.DT7_SAFETY: self._record_dt7_safety_observation,
            QualificationCommand.ECONOMY_SEND: self._start_qualification_economy,
            QualificationCommand.OBSERVE_ZERO: self._observe_qualification_firmware_zero,
            QualificationCommand.START_SOAK: self._start_qualification_soak,
            QualificationCommand.STOP_SOAK: self._stop_qualification_soak,
            QualificationCommand.EXPORT: self._export_qualification_evidence,
            QualificationCommand.SIGN_OFF: self._sign_off_qualification,
            QualificationCommand.ARM: self._arm_qualified_match_control,
            QualificationCommand.LOCK: self._lock_qualified_match_control,
        }
        # Direct callers historically refresh even for an unknown command.
        return handlers[command]() if command is not None else QualificationRefresh.REQUIRED

    def _new_qualification_session(self) -> QualificationRefresh:
        operator, ok = QInputDialog.getText(self, ui_text("Field qualification", chinese_enabled(self.registry)),
                                            ui_text("Operator name:", chinese_enabled(self.registry)))
        if not ok:
            return QualificationRefresh.SKIP
        firmware_hash, ok = QInputDialog.getText(
            self, ui_text("Field qualification", chinese_enabled(self.registry)),
            ui_text("Robot firmware SHA-256:", chinese_enabled(self.registry)))
        if not ok:
            return QualificationRefresh.SKIP
        self.backend.qualification.new_session(
            robot_id=str(self.state.robot_id), firmware_hash=firmware_hash,
            match_mode=self.match_mode_manager.current.key,
            video_source=self.readiness_video_source() or "UNDECLARED", operator=operator)
        self.backend.lock_control("new qualification session")
        self._qualification_runtime_baseline = self._capture_qualification_runtime_baseline()
        self.toast("Qualification session created; control remains LOCKED")
        return QualificationRefresh.REQUIRED

    def _reset_qualification_session(self) -> QualificationRefresh:
        self.backend.qualification.reset()
        self.backend.lock_control("qualification reset")
        self._qualification_runtime_baseline = self._capture_qualification_runtime_baseline()
        self.toast("Qualification evidence reset")
        return QualificationRefresh.REQUIRED

    def _record_qualification_preflight(self) -> QualificationRefresh:
        from ....preflight import run_preflight

        result = run_preflight(
            self.paths, official=self.scenario_manager.current.key == "OfficialEvent",
            codec_selftest=True, active_udp_owned_by_client=bool(self.backend.udp.running))
        self.backend.qualification.record_automatic(
            "preflight", result.passed, measured=result.as_dict(), threshold="all startup checks pass")
        self.toast("Preflight PASS" if result.passed else "Preflight BLOCKED")
        return QualificationRefresh.REQUIRED

    def _record_qualification_video(self) -> QualificationRefresh:
        source = self.readiness_video_source()
        source_key = "native_0310" if source == "native_0310" else "udp"
        section = self.backend.stats().get(source_key, {})
        baseline = self._qualification_baseline()
        session = self.backend.qualification.session
        evidence = video_evidence(
            source=source, session_exists=session is not None,
            session_age=time.time() - session.created_at if session is not None else 0.0,
            decoded_frames=int(section.get("decoded_frames", 0) or 0),
            baseline_frames=int(baseline.get("native_frames" if source == "native_0310" else "udp_frames", 0)),
            fresh=bool(section.get("decoded_connected", False)),
            continuous_seconds=float(section.get("continuous_decoded_seconds", 0.0) or 0.0))
        self._record_automatic_evidence(evidence)
        self.toast("Video field test PASS" if evidence.passed else "Video test needs a 60 s field capture")
        return QualificationRefresh.REQUIRED

    def _record_automatic_evidence(self, evidence: QualificationEvidence) -> None:
        self.backend.qualification.record_automatic(
            evidence.key, evidence.passed, measured=evidence.measured, threshold=evidence.threshold)

    def _start_qualification_kmc(self) -> QualificationRefresh:
        self.start_kmc_qualification_test()
        return QualificationRefresh.SKIP

    def _start_qualification_economy(self) -> QualificationRefresh:
        self.start_economy_qualification_test()
        return QualificationRefresh.SKIP

    def _record_dt7_safety_observation(self) -> QualificationRefresh:
        from ....backend.dt7_link import profile_digest

        qualification = self.backend.qualification
        writer = getattr(self.backend, "_dt7_link", None)
        session = qualification.session
        if writer is None or writer.mock_output or session is None:
            self.toast("需要当前真实 DT7 出口、资格会话及已完成的车辆回读探针")
            return QualificationRefresh.SKIP
        digest = profile_digest(writer.profile)
        if not qualification.test_passed("dt7_link_test"):
            self.toast("先完成 RobotLink → Run DT7 Link Selftest；诊断 PASS 不等于电机安全")
            return QualificationRefresh.SKIP
        self.backend.lock_control("DT7 physical safety observation; no automatic arm")
        observer, ok = QInputDialog.getText(self, "DT7 物理安全观察", "实际在场观察者姓名：")
        if not ok or not observer.strip():
            return QualificationRefresh.SKIP
        phrase, ok = QInputDialog.getText(self, "DT7 物理安全观察",
            "由现场人员实际测量物理急停、DR16/DBUS 失联归零，并确认重连无运动。\n"
            "键位回读不代表电机停机；未实测请取消。确认后输入：\nESTOP LOSS RECONNECT OBSERVED")
        if not ok or phrase.strip() != "ESTOP LOSS RECONNECT OBSERVED":
            return QualificationRefresh.SKIP
        estop_ms, ok = QInputDialog.getDouble(self, "DT7 物理测量", "实测物理急停到执行器归零（ms）：", 1000, 0, 10000, 1)
        if not ok:
            return QualificationRefresh.SKIP
        loss_ms, ok = QInputDialog.getDouble(self, "DT7 物理测量", "实测 DR16/DBUS 失联到执行器归零（ms）：", 1000, 0, 10000, 1)
        if not ok:
            return QualificationRefresh.SKIP
        if (qualification.session is not session or getattr(self.backend, "_dt7_link", None) is not writer
                or profile_digest(writer.profile) != digest):
            self.toast("测试期间会话或 DT7 配置已改变；不记录过期测量")
            return QualificationRefresh.SKIP
        result = qualification.record_supervised_dt7_safety(
            observer=observer, firmware_hash=session.firmware_hash, profile_sha256=digest,
            measured={"physical_estop_observed": True, "physical_loss_zero_observed": True,
                      "reconnect_stayed_neutral": True, "estop_zero_ms": estop_ms, "loss_zero_ms": loss_ms})
        self.toast(f"DT7 物理记录 {result.status}；仍需核心资格、重新双人签字及人工 Arm")
        return QualificationRefresh.SKIP

    def _observe_qualification_firmware_zero(self) -> QualificationRefresh:
        baseline = self._qualification_baseline()
        if self.backend.safety.estop_at <= float(baseline.get("estop_at", 0.0)):
            self.toast("Trigger a new F12 E-stop in this qualification session first")
            return QualificationRefresh.SKIP
        phrase, accepted = QInputDialog.getText(
            self, ui_text("Physical firmware watchdog observation", chinese_enabled(self.registry)),
            ui_text("After physically observing neutral output, type ZERO OBSERVED:", chinese_enabled(self.registry)))
        if not accepted or phrase.strip() != "ZERO OBSERVED":
            self.toast("Firmware zero observation cancelled")
            return QualificationRefresh.SKIP
        elapsed_ms = self.backend.record_firmware_zero_observation()
        self.toast(f"Physical neutral output observed at {elapsed_ms:.1f} ms")
        return QualificationRefresh.SKIP

    def _record_runtime_qualification(self, command: QualificationCommand) -> None:
        observers = {
            QualificationCommand.ESTOP: self._observe_estop_evidence,
            QualificationCommand.FIRMWARE_ZERO: self._observe_firmware_zero_evidence,
            QualificationCommand.LOSS_RECOVERY: self._observe_recovery_evidence,
            QualificationCommand.HERO: self._observe_hero_evidence,
            QualificationCommand.ECONOMY: self._observe_economy_evidence,
        }
        evidence = observers[command](self._qualification_baseline())
        self._record_automatic_evidence(evidence)
        self.toast(f"{evidence.key}: {'PASS' if evidence.passed else 'BLOCKED'}")

    def _observe_estop_evidence(self, baseline: dict) -> QualificationEvidence:
        safety = self.backend.stats().get("safety_evidence", {})
        return estop_evidence(estop_at=self.backend.safety.estop_at,
                              baseline_estop_at=float(baseline.get("estop_at", 0.0)),
                              zero_published=bool(safety.get("estop_zero_publish_ok", False)))

    def _observe_firmware_zero_evidence(self, baseline: dict) -> QualificationEvidence:
        return firmware_zero_evidence(estop_at=self.backend.safety.estop_at,
            baseline_estop_at=float(baseline.get("estop_at", 0.0)),
            observed_ms=float(self.state.backend_stats.get("firmware_zero_observed_ms", 0.0)))

    def _observe_recovery_evidence(self, baseline: dict) -> QualificationEvidence:
        safety = self.backend.stats().get("safety_evidence", {})
        return recovery_evidence(reconnect_count=self.backend.mqtt.reconnect_count,
            baseline_reconnect_count=int(baseline.get("mqtt_reconnect_count", 0)),
            disconnect_lock_at=float(safety.get("mqtt_disconnect_lock_at", 0.0)),
            reconnected_locked_at=float(safety.get("mqtt_reconnected_locked_at", 0.0)),
            baseline_at=float(baseline.get("captured_at_perf", 0.0)),
            control_locked=self.backend.safety.control_state.value == "LOCKED")

    def _observe_hero_evidence(self, baseline: dict) -> QualificationEvidence:
        return hero_evidence(revision=self.state.deploy_status_revision,
            baseline_revision=int(baseline.get("deploy_revision", 0)), observed_at=self.state.deploy_status_at,
            baseline_at=float(baseline.get("captured_at_perf", 0.0)), deploy_mode=int(self.state.deploy_mode))

    def _observe_economy_evidence(self, baseline: dict) -> QualificationEvidence:
        latest = self.economy_bridge.latest
        return economy_evidence(succeeded=latest is not None and latest.status == "succeeded",
            request_is_mock=latest is None or latest.request.mock,
            request_at=latest.request.timestamp if latest is not None else 0.0,
            baseline_at=float(baseline.get("captured_at_wall", 0.0)), revision=self.state.economy_status_revision,
            baseline_revision=int(baseline.get("economy_revision", 0)),
            transport_is_mock=self.economy_uses_mock_transport())

    def _record_prm1_qualification(self, command: QualificationCommand) -> QualificationRefresh:
        keys = {
            QualificationCommand.PRM1_VISION_SCHEMA: "prm1_vision_schema_review",
            QualificationCommand.PRM1_VISION_BENCH: "prm1_vision_bench_test",
            QualificationCommand.PRM1_SPIN_SCHEMA: "prm1_spin_schema_review",
            QualificationCommand.PRM1_SPIN_BENCH: "prm1_spin_bench_test",
        }
        reviewer, accepted = QInputDialog.getText(
            self, ui_text("PRM1 capability evidence", chinese_enabled(self.registry)),
            ui_text("Named schema reviewer / bench safety observer:", chinese_enabled(self.registry)))
        if not accepted or not reviewer.strip():
            return QualificationRefresh.SKIP
        phrase, accepted = QInputDialog.getText(
            self, ui_text("PRM1 capability evidence", chinese_enabled(self.registry)),
            ui_text("Type OBSERVED ON FIXTURE to confirm supervised evidence:", chinese_enabled(self.registry)))
        passed = bool(accepted and phrase.strip() == "OBSERVED ON FIXTURE")
        self.backend.qualification.record_supervised_prm1(
            keys[command], passed, reviewer=reviewer,
            measured={"active_and_watchdog_observed": passed},
            threshold="same robot + firmware hash; supervised active/disable/watchdog evidence")
        self.backend.lock_control("PRM1 evidence recorded; explicit re-arm required")
        self.toast(f"{keys[command]}: {'PASS' if passed else 'BLOCKED'} · LOCKED")
        return QualificationRefresh.REQUIRED

    def _start_qualification_soak(self) -> QualificationRefresh:
        self._qualification_soak_started_at = time.perf_counter()
        self.toast("Match-duration soak started")
        return QualificationRefresh.REQUIRED

    def _stop_qualification_soak(self) -> QualificationRefresh:
        evidence = soak_evidence(started_at=float(getattr(self, "_qualification_soak_started_at", 0.0)),
            now=time.perf_counter(), match_duration=float(self.state.match_duration_sec or 0.0))
        self._record_automatic_evidence(evidence)
        self.toast("Soak PASS" if evidence.passed else "Soak stopped too early")
        return QualificationRefresh.REQUIRED

    def _export_qualification_evidence(self) -> QualificationRefresh:
        destination = QFileDialog.getExistingDirectory(
            self, ui_text("Export field evidence", chinese_enabled(self.registry)))
        if destination:
            target = self.backend.qualification.store.export_current(Path(destination))
            self.toast(f"Evidence exported: {target.name}")
        return QualificationRefresh.REQUIRED

    def _sign_off_qualification(self) -> QualificationRefresh:
        lead, ok = QInputDialog.getText(self, ui_text("Dual sign-off", chinese_enabled(self.registry)),
                                       ui_text("Safety lead name:", chinese_enabled(self.registry)))
        if ok:
            self.backend.qualification.sign_off(lead)
            self.toast("Dual sign-off recorded")
        return QualificationRefresh.REQUIRED

    def _arm_qualified_match_control(self) -> QualificationRefresh:
        session = self.backend.qualification.session
        ok, reason = self.backend.arm_match_control(
            robot_id=str(self.state.robot_id), firmware_hash=session.firmware_hash if session is not None else "",
            match_mode=self.match_mode_manager.current.key, video_source=self.readiness_video_source())
        self.toast("MATCH CONTROL ARMED" if ok else f"Arm blocked: {reason}")
        return QualificationRefresh.REQUIRED

    def _lock_qualified_match_control(self) -> QualificationRefresh:
        result = request_control_lock(self.backend, self.control, self.backend.mqtt.publish_estop_zero)
        if result.failures:
            self.toast(result.message, kind="warning")
        else:
            self.toast(result.message)
        return QualificationRefresh.REQUIRED

    RADIO_KM_SELFTEST_MS = 2500

    def run_radio_km_selftest(self) -> None:
        """端到端自检：看**车上**的 0x0311 到达计数涨不涨，不是客户端自证。

        刻意不发任何按键、也不新造帧：出口本来就以 75Hz 在流，
        自检只做"记两个时刻的车上计数"这件事，所以车不会因为自检而动。
        判据抽成 control.evaluate_radio_km_selftest()，那边有独立断言。
        """
        control = self.control
        link = self.registry.get("RobotLink")
        setting = link.get_setting("Radio KM Egress") if link is not None else None
        if setting is None:
            self.toast("没有 Radio KM Egress 开关，自检无从做起")
            return
        if not bool(setting.value):
            self.toast("自检前请先打开 Radio KM Egress（开关本身要参与）")
            return
        if not control.radio_km_enabled:
            self.toast(f"出口未生效：{control.radio_km_gate_reason or '闸门未过'}")
            return
        if getattr(self, "_radio_km_selftest_running", False):
            self.toast("无线自检已在进行中")
            return
        rx0 = int(getattr(control, "radio_km_robot_rx", -1))
        if rx0 < 0:
            self.toast("读不到车上计数：插一下 C 板 USB(0x5A) 才能做这个自检")
            return
        self._radio_km_selftest_running = True
        sent0 = int(control.radio_km_sent)
        self.toast(f"无线自检开始：车上已计 {rx0} 帧，观察 2.5 秒")

        def finish() -> None:
            self._radio_km_selftest_running = False
            ok, detail = evaluate_radio_km_selftest(
                rx0,
                int(getattr(control, "radio_km_robot_rx", -1)),
                sent0,
                int(control.radio_km_sent),
            )
            self.toast(("无线自检 PASS · " if ok else "无线自检 FAIL · ") + detail)

        QTimer.singleShot(self.RADIO_KM_SELFTEST_MS, finish)

    DT7_SELFTEST_PRESS_MS = 900
    DT7_SELFTEST_HOLD_MS = 1600
    DT7_SELFTEST_TICK_MS = 40

    def rescan_serial_ports(self) -> None:
        """重扫三个串口下拉的候选口（不重启程序也能认出新插上的 DT7）。

        真正的逻辑在 rm_client/serial_ports.refresh_port_options()：那边是纯逻辑、
        可离线测；这里只负责提示与落盘。
        """
        import rm_client.serial_ports as serial_ports

        serial_ports.invalidate_port_cache()
        serial_ports.port_snapshot()  # schedules the scan; never waits here
        self.toast("正在后台重扫串口…")

        def finish():
            if serial_ports.discovery_pending():
                QTimer.singleShot(50, finish)
                return
            module = self.registry.get("RobotLink")
            diffs = serial_ports.refresh_port_options(self.registry, nonblocking=True)
            setting = module.get_setting("DT7 Port") if module is not None else None
            seen = [str(x) for x in (getattr(setting, "options", None) or [])
                    if str(x) and str(x) != "Auto"]
            # An explicit rescan can retry neutral on the original target.
            # It never clears a failure latch or re-enables real motion.
            for name in ("dt7_link", "cc_link"):
                link = getattr(self.control, name, None)
                if getattr(link, "release_pending", False) and hasattr(link, "retry_release"):
                    link.retry_release()
            self.toast("串口已重扫：" + ("；".join(diffs) if diffs else "候选口没有变化")
                       + "｜DT7 可选 " + (", ".join(seen) if seen else "（没认出 DJI 口）"))
            self.auto_save_config()

        QTimer.singleShot(50, finish)

    def run_dt7_link_selftest(self) -> None:
        """Fresh baseline -> neutral/X probe -> no-send hold; token-bound."""
        from rm_client.backend.dt7_qualification import Dt7Probe, PROBE_BITS
        from rm_client.backend.dt7_link import profile_digest
        import os
        import tempfile
        control = self.control
        module = self.registry.get("RobotLink")
        if getattr(self, "_dt7_selftest_running", False):
            self.toast("C 路自检已在进行中")
            return
        if module is None or not module.enabled or not all(
                self.setting_bool(module, name, False) for name in
                ("Direct DT7 Egress", "DT7 Real TX", "Allow Drive")):
            self.toast("DT7 自检需要 RobotLink、Direct DT7、Real TX、Allow Drive 显式开启")
            return
        if any(self.setting_bool(module, name, False) for name in
               ("Radio KM Egress", "Custom Controller Egress")):
            self.toast("自检前关闭其他键鼠出口，不能同时产生两个来源")
            return
        session = self.backend.qualification.session
        if session is None or self.backend.safety.estop_active:
            self.toast("先创建资格会话并确认物理安全；急停期间不能运行自检")
            return
        # Never promise that X is harmless on an unknown vehicle firmware.
        confirmation, accepted = QInputDialog.getText(self, "DT7 现场探针确认",
            "先停用发射机构、架空车辆，并核实当前固件 X 键无动作。\n确认已完成后输入 X：")
        if not accepted or confirmation.strip().upper() != "X":
            return
        # Configure while locked first: creating/changing a transport revokes
        # every prior token. Never create the writer after issuing our token.
        self.backend.tick_polar_link(self.state, self.registry, control)
        token = self.backend.begin_qualification({"dt7_selftest"}, ttl_s=15.0)
        control.dt7_qualification_token = token.value
        control.dt7_forced_keys = 0
        control.dt7_send_paused = False
        self.backend.tick_polar_link(self.state, self.registry, control)
        writer = control.dt7_link
        if writer is None or writer.mock_output or not control.dt7_enabled:
            self.backend.lock_control("DT7 selftest transport/gate unavailable")
            self.toast(control.dt7_last_error or "DT7 出口不可用")
            return
        epoch = self.backend._input_clear_generation
        digest = profile_digest(writer.profile)
        probe = Dt7Probe(time.perf_counter(), press_s=self.DT7_SELFTEST_PRESS_MS/1000,
                         hold_s=self.DT7_SELFTEST_HOLD_MS/1000)
        self._dt7_selftest_running = True
        finished = False

        def poll() -> None:
            nonlocal finished
            if finished:
                return
            now = time.perf_counter()
            if (self.backend._input_clear_generation != epoch
                    or control.dt7_qualification_token != token.value
                    or self.backend.qualification.session is not session
                    or control.dt7_link is not writer or not control.dt7_enabled
                    or profile_digest(writer.profile) != digest):
                probe.abort()
            else:
                allowed, why = self.backend.safety.can_transmit_dt7(
                    qualification_token=token.value, now=now)
                if not allowed:
                    probe.abort(detail=why)
                else:
                    probe.observe(control.radio_km_robot_key, control.radio_km_robot_key_at,
                                  control.radio_km_robot_key_sequence, now)
                    phase = probe.advance(now, control.dt7_sent)
                    control.dt7_forced_keys = PROBE_BITS if phase == "press" else 0
                    if phase == "hold" and hasattr(writer, "begin_qualification_hold"):
                        if not writer.begin_qualification_hold(token.value, token.expires_at):
                            probe.abort(detail="DT7 no-send hold authorization failed")
                    control.dt7_send_paused = phase == "hold"
                    control.dt7_paused_until = token.expires_at
            if probe.result is None:
                QTimer.singleShot(self.DT7_SELFTEST_TICK_MS, poll)
                return
            finished = True
            self._dt7_selftest_running = False
            # Abort callbacks from an old epoch must never clear a newer token.
            if control.dt7_qualification_token == token.value:
                self.backend.lock_control("DT7 probe finished; explicit re-arm required")
            result = probe.result
            self.toast(f"C 路 {result['verdict']} · {result['detail']}")
            self.toast(f"停发 {result['hold_verdict']} · {result['hold_detail']}（不是电机急停认证）")
            try:
                if self.backend.qualification.session is session:
                    self.backend.qualification.record_dt7_probe(probe,
                        profile_sha256=digest, real_transport=not writer.mock_output)
                report = dict(result, tool="dt7_link_selftest(ui)", profile_sha256=digest,
                              robot_id=session.robot_id, firmware_hash=session.firmware_hash,
                              build_commit=session.build_commit, qualification_only=True,
                              match_ready=False)
                root = self.backend.paths.evidence_root
                root.mkdir(parents=True, exist_ok=True)
                out = root/f"dt7-selftest-{time.time_ns()}.json"
                fd, temporary = tempfile.mkstemp(prefix=".dt7-", suffix=".json", dir=root)
                try:
                    with os.fdopen(fd,"w",encoding="utf-8") as file:
                        json.dump(report,file,ensure_ascii=False,indent=2)
                        file.flush();os.fsync(file.fileno())
                    os.replace(temporary,out)
                finally:
                    if os.path.exists(temporary):os.unlink(temporary)
                self.toast(f"探针记录 {out.name}；仍需车辆物理安全测试与重新签字")
            except Exception as exc:
                self.toast(f"探针记录失败：{exc}")
        poll()

    def start_kmc_qualification_test(self) -> None:
        """Schedule 750 neutral KMC frames without blocking the render thread."""

        if getattr(self, "_qualification_kmc_timer", None) is not None:
            self.toast("KMC qualification is already running")
            return
        # A mock bridge reports every frame as delivered, which would mint
        # fake heartbeat evidence. Qualification must run on the real link.
        if self.backend.mqtt.mock_output or not self.backend.mqtt.connected:
            self.toast("KMC qualification requires a live real MQTT connection")
            return
        token = self.backend.begin_qualification({"core_kmc"}, ttl_s=15.0)
        started = time.perf_counter()
        samples: list[float] = []
        sent = 0
        next_due = started
        timer = QTimer(self)
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        # Poll at 2 ms but emit at most once every exact 1/75 second. A 13 ms
        # timer is 76.9 Hz and was correctly rejected by the official limiter.
        timer.setInterval(2)
        self._qualification_kmc_timer = timer

        def finish() -> None:
            nonlocal sent
            timer.stop()
            timer.deleteLater()
            self._qualification_kmc_timer = None
            elapsed = time.perf_counter() - started
            intervals = [
                (samples[index] - samples[index - 1]) * 1000.0
                for index in range(1, len(samples))
            ]
            ordered = sorted(intervals)
            p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 9999.0
            maximum = max(intervals, default=9999.0)
            passed = sent == 750 and p95 < 20.0 and maximum < 100.0
            try:
                self.backend.qualification.record_automatic(
                    "control_heartbeat_test",
                    passed,
                    measured={
                        "sent": sent,
                        "seconds": elapsed,
                        "p95_interval_ms": p95,
                        "max_interval_ms": maximum,
                    },
                    threshold={"sent": 750, "p95_interval_ms": 20, "max_interval_ms": 100},
                )
            finally:
                self.backend.lock_control("qualification KMC test complete")
            self.toast(f"KMC {'PASS' if passed else 'FAIL'} · P95 {p95:.1f} ms · LOCKED")
            self.update_backend_readiness(force=True)

        def send_neutral() -> None:
            nonlocal sent, next_due
            now = time.perf_counter()
            if now < next_due:
                return
            ok, _ = self.backend.publish_keyboard_mouse(qualification_token=token.value)
            if ok:
                sent += 1
                samples.append(now)
                next_due = now + 1.0 / 75.0
            if len(samples) >= 750 or now - started > 12.0:
                finish()

        timer.timeout.connect(send_neutral)
        timer.start()
        self.toast("KMC neutral 75 Hz qualification started")

    # ------------------------------------------------------------------
    # 阶段 3d 大域 mixin 化：start_economy_qualification_test（D6 资格赛窗口）
    # 已机械迁至 EconomyMixin；_economy_qualification_token 由同迁的
    # send_economy_request 消费，一次性授权语义零修改随行搬运。
    # ------------------------------------------------------------------

    def restore_compact_hud(self) -> None:
        """Restore compact visuals without changing behavior or connection settings."""
        hud = self.registry.get("HUD")
        tactical_map = self.registry.get("TacticalMap")
        values = (
            (hud, "Density", "CombatCompact"),
            (hud, "Visual Tone", "LiquidClean"),
            (hud, "Target Style", "RoundCompact"),
            (tactical_map, "Map Size", "Compact"),
            (tactical_map, "Chrome", "Minimal"),
            (tactical_map, "Status Display", "LiquidAuto"),
        )
        for module, name, value in values:
            setting = module.get_setting(name) if module else None
            if setting is not None and (not setting.options or value in setting.options):
                setting.value = value
        self.invalidate_gui_cache()
        self.tactical_map_cached_rect = QRectF()
        self.toast("Compact HUD restored")
        self.auto_save_config()

    def on_module_toggled(self, module: Module, enabled: bool, source: str) -> None:
        if self.reject_pending_configuration_edit():
            return
        if module.name in {"ControllerAngles", "RobotLink"}:
            self.backend._sync_controller_angles(self.registry)
        if module.name == "RobotLink" and not enabled:
            self.backend.lock_control("RobotLink disabled")
            self.control.release_all(self.state, self.registry)
            self.control.clear_pending()
        jello_click = bool(
            source == "clickgui"
            and self.gui_visible
            and self.active_clickgui_mode() == PRIMARY_GUI_MODE
        )
        self.invalidate_gui_cache()
        # The ActiveMods fade starts on this event, but the row targets are
        # only updated during the next paint (after begin_frame). Seed the
        # static-HUD deferral window here so the toggle frame itself does not
        # repaint the whole snapshot mid-fade.
        self._last_module_toggle_at = time.perf_counter()
        self._hud_static_defer_since = self._last_module_toggle_at
        if module.name == "MinecraftCombatHUD":
            hud = self.registry.get("HUD")
            preset = hud.get_setting("Combat Preset") if hud else None
            if preset is not None:
                if enabled:
                    preset.value = "Minecraft 1.21.11"
                elif str(preset.value) == "Minecraft 1.21.11":
                    preset.value = "Polar"
            self.invalidate_transient_overlay_scene()
        elif module.name == "HeroLob":
            if enabled and not self.home_visible:
                self.open_hero_deploy_interface()
            elif not enabled:
                self.state.hero_lob_armed = False
                if int(self.state.deploy_mode) == 1:
                    # Deployment is an acknowledged referee state. Never hide
                    # its explicit EXIT control merely because the helper
                    # module was switched off.
                    self.hero_deploy_ui.open(time.perf_counter())
                    self.overlay_coordinator.request_modal(OverlayKind.HERO_DEPLOY)
                    self.toast("HeroLob disabled; explicitly EXIT deploy mode")
                else:
                    self.close_hero_deploy_interface(force=True)
        elif module.name == "SpinMode":
            if not str(source).startswith("auto_hit"):
                self.auto_spin.operator_override()
            self.publish_spin_control_state(enabled, source=source)
        audio = self.registry.get("AudioFeedback")
        active = bool(
            audio
            and (audio.enabled or module is audio)
            and self.setting_bool(audio, "Enabled", True)
            and self.setting_bool(audio, "Module Toggle Sounds", True)
        )
        if jello_click:
            hud = self.registry.get("HUD")
            self.ui_audio.jello_module_toggled(
                enabled,
                # Sigma routes module toggles through ActiveMods -> Sound.
                # Keep that independent switch while still respecting the
                # application's global audio/master-toggle controls.
                active=active and self.setting_bool(hud, "Jello List Sound", True),
                volume=self.get_setting_number(audio, "UI Volume", 45.0),
            )
            return
        self.ui_audio.module_toggled(
            enabled,
            source,
            active=active,
            volume=self.get_setting_number(audio, "UI Volume", 45.0),
            theme=self.setting_mode(audio, "Sound Theme", "Polar UI"),
            custom_sounds=self.custom_ui_sound_selection(audio),
        )

    def play_gui_transition_sound(self, kind: str) -> None:
        audio = self.registry.get("AudioFeedback")
        if not audio or not self.setting_bool(audio, "Enabled", True):
            return
        self.ui_audio.gui_feedback(
            kind,
            self.get_setting_number(audio, "UI Volume", 45.0),
            self.setting_mode(audio, "Sound Theme", "Polar UI"),
        )

    def preview_elimination_fx(self, *, single: bool = False) -> None:
        module = self.registry.get("EliminationFX")
        sequence = "Single" if single else self.setting_mode(module, "Preview Sequence", "Normal 1-6")
        if sequence == "All Kill Types":
            types = ["NORMAL", "HEADSHOT", "EXPLOSION", "CRIT", "ASSIST", "DESTROY_VEHICLE"]
        elif sequence == "Single":
            types = ["NORMAL"]
        else:
            types = ["NORMAL"] * 6
        for index, kill_type in enumerate(types):
            event = CombatEvent(
                "elimination", "preview", True, time.perf_counter(),
                victim_id=101 + index,
                detail="local_preview",
                kill_type=kill_type,
                combo=index + 1,
                victim_name=f"ROBOT {101 + index}",
                distance_m=8.0 + index,
                score_delta=100,
                tags=("ELIMINATION", "ARMOR HIT") if kill_type != "DESTROY_VEHICLE" else ("ROBOT DESTROYED",),
            )
            QTimer.singleShot(index * 650, lambda queued=event: self.accept_combat_event(queued))

    def handle_calibration_action(self, action_name: str) -> None:
        module = self.registry.get("CameraCalibration")
        name = self.setting_mode(module, "Profile", "polar_webcam")
        columns = int(self.get_setting_number(module, "Board Columns", 9))
        rows = int(self.get_setting_number(module, "Board Rows", 6))
        square_size = self.get_setting_number(module, "Square Size", 25.0)
        if action_name == "Capture Chessboard":
            frame = self.camera_worker.latest_frame()
            if frame is not None and self.calibration.capture(frame.bgr, columns, rows, square_size):
                self.toast(f"Calibration sample {self.calibration.sample_count}: corners locked")
            else:
                self.toast(self.calibration.last_error or "Chessboard capture failed", kind="warning")
            return
        if action_name == "Solve Calibration":
            profile = self.calibration.solve(name, columns, rows, square_size)
            if profile is None:
                self.toast(self.calibration.last_error or "Calibration solve failed", kind="warning")
            else:
                self.toast(f"Calibration solved: {profile.rms_error_px:.2f}px RMS")
            return
        bridge_module = self.registry.get("TongjiVisionBridge")
        device = self.setting_mode(bridge_module, "Inference Device", "AUTO")
        path = self.calibration.export_tongji_yaml(name, device)
        if path is None:
            self.toast(self.calibration.last_error or "Tongji YAML export failed", kind="warning")
        else:
            self.toast(f"Tongji YAML exported: {path.name}")


# ----------------------------------------------------------------------
# __qualname__ 钉回（api_freeze_test 对方法记录 __qualname__ 并参与比对，
# 见 tests/api_freeze_test.py _member_entry）：全部迁入方法钉回
# PolarRMWindow.<name>，冻结面签名不变。
# ----------------------------------------------------------------------

_QUALNAME_PINNED_MEMBERS = (
    ConfigActionsMixin.after_setting_changed,
    ConfigActionsMixin.handle_config_action,
    ConfigActionsMixin.handle_qualification_action,
    ConfigActionsMixin.start_kmc_qualification_test,
    ConfigActionsMixin.restore_compact_hud,
    ConfigActionsMixin.on_module_toggled,
    ConfigActionsMixin.play_gui_transition_sound,
    ConfigActionsMixin.preview_elimination_fx,
    ConfigActionsMixin.handle_calibration_action,
)

for _fn in _QUALNAME_PINNED_MEMBERS:
    _fn.__qualname__ = f"PolarRMWindow.{_fn.__name__}"
del _fn, _QUALNAME_PINNED_MEMBERS
