"""Window adapters for profile persistence and conservative runtime restoration.

Validation, migration, registry snapshots, delayed-save ownership and transaction
failure handling live in rm_client.configuration. This mixin owns the window-specific effects and
keeps saved preferences separate from permission to actuate a robot.
"""

from __future__ import annotations

import logging
import math
import os
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QFileDialog

from ...jello_locale import chinese_enabled, ui_text

# 相对导入层级：rm_client/ui/polar/mixins/ → .... = rm_client、... = rm_client.ui
from ....config_store import ProfileSaveError, apply_registry_config, export_client_config, validate_client_config
from ....configuration.wheel import restore_wheel_profile
from ....configuration.registry import RegistrySnapshot
from ....configuration.quiescence import quiesce_configuration_inputs
from ....configuration.pending import (
    ConfigurationPhase, CompletionAction, PendingConfigurationChange, PendingScenarioConfiguration, ProfileIdentity,
)
from ....configuration.transactions import (
    ConfigurationApplyError, ConfigurationRestorePoint, RuntimeRestorePending,
    apply_configuration_transaction, rollback_configuration_transaction,
)
from ....control import validate_control_binds
from ....match_modes import MatchModeConfig
from ....model import CONTROL_SLOT_ORDER, PRIMARY_GUI_MODE, Module, normalize_gui_mode
from ....robot_profiles import (
    apply_profile_to_state,
    profile_by_key,
    profiles_for_match,
    resolve_performance,
    robot_id_for_alliance,
)
from ....ui_audio import UiAudioManager

__all__ = [
    "PersistenceMixin",
]


class PersistenceMixin:
    """D17 配置 / 档案 / 壁纸持久化（config_store / match profile / wallpaper / auto-save）。"""

    def current_robot_profile_key(self) -> str:
        module = self.registry.get("RobotProfile")
        setting = module.get_setting("Role") if module else None
        valid = {profile.key for profile in profiles_for_match(self.match_mode_manager.current.key)}
        value = str(setting.value) if setting else "Infantry1"
        return value if value in valid else profiles_for_match(self.match_mode_manager.current.key)[0].key

    def current_robot_performance(self, role: str | None = None):
        module = self.registry.get("RobotProfile")
        return resolve_performance(
            self.match_mode_manager.current.key,
            role or self.current_robot_profile_key(),
            level=int(self.get_setting_number(module, "Level", 1)),
            hero_type=self.setting_mode(module, "Hero Type", "Melee"),
            infantry_chassis=self.setting_mode(module, "Infantry Chassis", "Power"),
            infantry_shooter=self.setting_mode(module, "Infantry Shooter", "Burst"),
            sentinel_control=self.setting_mode(module, "Sentinel Control", "Automatic"),
        )

    def apply_match_mode(self, key: str, close_home: bool, announce: bool = True) -> None:
        if self.reject_pending_configuration_edit():
            return
        quiesce_configuration_inputs(
            revoke=lambda: self.backend.lock_control("match mode changed"),
            release=lambda: self.control.release_all(self.state, self.registry),
            clear_pending=lambda: self.control.clear_pending(),
        )
        config = self.match_mode_manager.set_mode(key)
        self.match_mode_manager.apply_to_registry(self.registry)
        self.seed_match_state(config)
        profile_module = self.registry.get("RobotProfile")
        role_setting = profile_module.get_setting("Role") if profile_module else None
        if role_setting is not None:
            role_setting.options = [profile.key for profile in profiles_for_match(config.key)]
            if str(role_setting.value) not in role_setting.options:
                role_setting.value = role_setting.options[0]
        self.apply_robot_profile(self.current_robot_profile_key(), close_home=False, announce=False)
        if close_home:
            self.home_visible = False
            self.gui_visible = False
            self.startup_step = "done"
        if announce:
            self.toast(f"Match mode: {config.title}")

    def apply_robot_profile(self, key: str, close_home: bool, announce: bool = True) -> None:
        if self.reject_pending_configuration_edit():
            return
        module = self.registry.get("RobotProfile")
        profiles = profiles_for_match(self.match_mode_manager.current.key)
        valid_keys = [profile.key for profile in profiles]
        profile = profile_by_key(self.match_mode_manager.current.key, key)
        role = module.get_setting("Role") if module else None
        if role is not None:
            role.options = valid_keys
            role.value = profile.key
        performance = self.current_robot_performance(profile.key)
        level = int(self.get_setting_number(module, "Level", 1))
        side = self.alliance_side()
        robot_id = robot_id_for_alliance(profile, side)
        if int(self.state.robot_id) != robot_id or str(self.backend.mqtt.client_id) != str(robot_id):
            # Revoke the old epoch before changing either identity. Never
            # inherit armed outputs merely because the operator chose Blue.
            def release_inputs() -> None:
                self.control.mock_output = bool(self.backend.mqtt.mock_output)
                self.control.release_all(self.state, self.registry)

            quiesce_configuration_inputs(
                revoke=lambda: self.backend.lock_control("robot profile/alliance changed"),
                release=release_inputs,
                clear_pending=lambda: self.control.clear_pending(),
            )
            if any(
                bool(getattr(transport, flag, False))
                for transport in (self.backend.mqtt, getattr(self.backend, "polar", None), getattr(self.backend, "serial", None))
                for flag in ("_started", "_running", "connected")
            ):
                # Private spin/gimbal intent is independent of the official
                # match lock. A running identity switch therefore latches the
                # existing all-output E-stop until the operator releases it.
                self.backend.trigger_estop()
                self.toast("身份已切换到停用状态；核对机器人后需操作员解除急停", kind="warning")
        try:
            self.backend.set_client_id(robot_id)
        except Exception as exc:
            self.backend.lock_control("robot identity update failed")
            if str(self.backend.mqtt.client_id) == str(robot_id):
                # A reconnect can fail after the configured identity changed.
                # Keep the local identity aligned, but do not unlock outputs.
                self.state.alliance_color = side
                apply_profile_to_state(self.state, profile, performance, level)
            else:
                side = self.state.alliance_color
                selected_side = self.registry.get("AllianceProfile")
                selected_side = selected_side.get_setting("Side") if selected_side else None
                if selected_side is not None:
                    selected_side.value = side
                if role is not None:
                    role.value = getattr(self.state, "robot_profile", role.value)
            match = self.registry.get("MatchMode")
            legacy_side = match.get_setting("Alliance") if match else None
            if legacy_side is not None:
                legacy_side.value = side
            self.toast(f"身份更新失败，输出保持锁定：{exc}", kind="warning")
            if self.config_loading:
                raise
            return
        self.state.alliance_color = side
        match = self.registry.get("MatchMode")
        legacy_side = match.get_setting("Alliance") if match else None
        if legacy_side is not None:
            legacy_side.value = side
        apply_profile_to_state(self.state, profile, performance, level)
        if close_home:
            self.home_visible = False
            self.gui_visible = False
            self.startup_step = "done"
            self.refresh_vision_runtime()
        if announce:
            self.toast(f"Robot profile: {profile.title}")

    def seed_match_state(self, config: MatchModeConfig) -> None:
        self.state.match_mode = config.key
        self.state.match_title = config.title
        self.state.match_duration_sec = config.duration_sec
        self.state.stage_countdown_sec = config.duration_sec
        self.state.stage_elapsed_sec = 0
        self.state.start_time = time.perf_counter()
        # 赛制选择只初始化展示数据，不能冒充权威裁判的比赛阶段。
        self.state.current_stage = "NOT_STARTED"
        self.state.alliance_color = self.alliance_side()
        if config.key == "RMUL3v3":
            self.state.ally_victory_points = 200
            self.state.enemy_victory_points = 200
            self.state.control_owner = "NEUTRAL"
            self.state.control_progress = 0
            self.state.base_health = 0
            self.state.enemy_base_health = 0
            self.state.outpost_health = 0
            self.state.enemy_outpost_health = 0
        else:
            self.state.base_health = self.state.base_max_health
            self.state.enemy_base_health = self.state.enemy_base_max_health
            self.state.outpost_health = self.state.outpost_max_health
            self.state.enemy_outpost_health = self.state.enemy_outpost_max_health

    def refresh_config_profiles(self) -> None:
        module = self.registry.get("ConfigManager")
        setting = module.get_setting("Profile") if module else None
        if setting is None:
            return
        names = self.config_store.profile_names()
        current = str(setting.value)
        setting.options = names
        setting.value = current if current in names else names[0]

    def refresh_wallpaper_profiles(self) -> None:
        module = self.registry.get("StartupVisual")
        setting = module.get_setting("Wallpaper Profile") if module else None
        if setting is None:
            return
        names = self.startup_wallpaper.profile_names()
        current = str(setting.value)
        setting.options = names
        setting.value = current if current in names else names[0]

    def refresh_custom_ui_sound_options(self) -> None:
        module = self.registry.get("AudioFeedback")
        if module is None:
            return
        options = [UiAudioManager.NO_SOUND, *self.ui_audio.custom_sound_names()]
        for name in ("Custom Enable Sound", "Custom Disable Sound", "Custom Invalid Sound"):
            setting = module.get_setting(name)
            if setting is None:
                continue
            current = str(setting.value)
            setting.options = options
            setting.value = current if current in options else UiAudioManager.NO_SOUND

    def custom_ui_sound_selection(self, module: Module | None = None) -> dict[str, str]:
        audio = module or self.registry.get("AudioFeedback")
        return {
            kind: self.setting_mode(audio, f"Custom {kind.title()} Sound", UiAudioManager.NO_SOUND)
            for kind in ("enable", "disable", "invalid")
        }

    def import_custom_ui_sound(self, kind: str) -> None:
        if kind not in UiAudioManager.CUSTOM_KINDS:
            return
        start = str(self.ui_audio.custom_dir)
        filters = ui_text("Audio files (*.wav *.ogg *.mp3 *.flac *.m4a *.aac)", chinese_enabled(self.registry))
        path, _ = QFileDialog.getOpenFileName(self, ui_text(f"Import {kind} sound", chinese_enabled(self.registry)), start, filters)
        if not path:
            return
        try:
            selected = self.ui_audio.import_custom_sound(Path(path), kind)
        except (OSError, ValueError) as exc:
            self.toast(f"Sound import failed: {exc}", kind="warning")
            return
        self.refresh_custom_ui_sound_options()
        audio = self.registry.get("AudioFeedback")
        setting = audio.get_setting(f"Custom {kind.title()} Sound") if audio else None
        if setting is not None:
            setting.value = selected
        self.toast(f"Custom {kind} sound ready")
        self.auto_save_config()

    def import_wallpaper_from_dialog(self) -> None:
        start = str(self.paths.asset("wallpapers"))
        filters = ui_text("Wallpaper media (*.mp4 *.webm *.mov *.avi *.mkv *.gif *.png *.jpg *.jpeg)", chinese_enabled(self.registry))
        path, _ = QFileDialog.getOpenFileName(self, ui_text("Import startup wallpaper", chinese_enabled(self.registry)), start, filters)
        if not path:
            return
        try:
            profile = self.startup_wallpaper.import_wallpaper(Path(path))
        except (OSError, ValueError) as exc:
            self.toast(f"Wallpaper import failed: {exc}")
            return
        self.refresh_wallpaper_profiles()
        module = self.registry.get("StartupVisual")
        if module:
            profile_setting = module.get_setting("Wallpaper Profile")
            mode_setting = module.get_setting("Background Mode")
            if profile_setting:
                profile_setting.value = profile
            if mode_setting:
                mode_setting.value = "VideoParallax"
        profiles = self.startup_wallpaper.profile_names()
        self.wallpaper_picker_page = profiles.index(profile) // 4 if profile in profiles else 0
        self.toast(f"Wallpaper loaded: {profile}")
        self.auto_save_config()

    def apply_wallpaper_profile(self, profile: str) -> None:
        if profile not in self.startup_wallpaper.profile_names():
            self.toast("壁纸资源不存在，已保留当前壁纸")
            return
        module = self.registry.get("StartupVisual")
        if module is None:
            return
        profile_setting = module.get_setting("Wallpaper Profile")
        mode_setting = module.get_setting("Background Mode")
        if profile_setting:
            profile_setting.value = profile
        if mode_setting:
            if profile == "ParticleGrid":
                mode_setting.value = "ParticleGrid"
            elif profile == "Default3000925581Pkg":
                mode_setting.value = "PkgScene"
            else:
                mode_setting.value = "VideoParallax"
        self.toast(f"壁纸已切换：{self.startup_wallpaper.profile_label(profile)}")
        self.auto_save_config()

    def remove_wallpaper_profile(self, profile: str) -> None:
        if not self.startup_wallpaper.can_remove(profile):
            self.toast("内置壁纸不能删除")
            return
        if not self.startup_wallpaper.remove_wallpaper(profile):
            self.toast("删除壁纸失败")
            return
        self.refresh_wallpaper_profiles()
        self.apply_wallpaper_profile("BlackHole")
        count = max(1, math.ceil(len(self.startup_wallpaper.profile_names()) / 4))
        self.wallpaper_picker_page = min(self.wallpaper_picker_page, count - 1)
        self.toast("用户壁纸已删除")

    def reset_wallpaper_profile(self) -> None:
        self.apply_wallpaper_profile("BlackHole")
        self.wallpaper_picker_page = 0

    def current_profile(self) -> str:
        module = self.registry.get("ConfigManager")
        setting = module.get_setting("Profile") if module else None
        return self.config_store.clean_profile_name(str(setting.value if setting else "default"))

    def _accepted_profile_identity(self) -> ProfileIdentity:
        return self._profile_saves.identity(lambda: self.current_profile())

    def _set_profile_selection(self, profile: str) -> None:
        module = self.registry.get("ConfigManager")
        setting = module.get_setting("Profile") if module else None
        if setting is not None:
            setting.value = profile

    def _restorable_profile_name(self) -> str:
        return getattr(self, "_unsaved_profile_name", None) or self._accepted_profile_identity().name

    def _accept_profile_selection(self, profile: str) -> None:
        previous = self._accepted_profile_identity()
        self._profile_saves.accept_profile(profile, previous)
        self._set_profile_selection(profile)
        # Selection must succeed before retiring the previous delayed save.
        self._profile_saves.cancel_pending()

    def current_config_data(self) -> dict[str, Any]:
        return export_client_config(
            self.registry,
            gui_mode=PRIMARY_GUI_MODE,
            selected_category=self.selected_category,
            selected_module=self.selected_module.name if self.selected_module else "",
            module_expanded=self.module_expanded,
            old_panels=self.old_panels,
            new_panels=self.new_panels,
            dropdown_panels=self.dropdown_panels,
            polar_windows=self.polar_windows,
            quick_action_wheel=self.quick_action_wheel.export_config(),
        )

    def load_startup_config(self) -> None:
        self._accepted_profile_identity()
        module = self.registry.get("ConfigManager")
        preferences = self.config_store.load_startup_preferences()
        profile_setting = module.get_setting("Profile") if module else None
        load_setting = module.get_setting("Load On Start") if module else None
        if preferences is not None:
            if profile_setting is not None:
                profile_setting.value = preferences["profile"]
            if load_setting is not None:
                load_setting.value = preferences["load_on_start"]
            if not preferences["load_on_start"]:
                self._accept_profile_selection(self.current_profile())
                return
        data = self.config_store.load(self.current_profile())
        if preferences is None and data is not None:
            # Migrate the old in-profile preference without applying any of
            # the profile first. False must work on the very next launch.
            saved = data.get("modules", {}).get("ConfigManager", {}).get("settings", {})
            if saved.get("Load On Start") is False:
                if load_setting is not None:
                    load_setting.value = False
                return
        if not self.setting_bool(module, "Load On Start", True):
            return
        if data:
            self.apply_config_data(data, announce=False)

    @property
    def configuration_change_pending(self) -> bool:
        return getattr(self, "_pending_configuration", None) is not None

    @property
    def configuration_close_requires_save(self) -> bool:
        return self._profile_saves.close_requires_save

    def prepare_configuration_for_close(self) -> bool:
        """Try an owned dirty save once before the window enters shutdown.

        A refusal leaves the window and runtime untouched. Never cancel a
        staged load here: it must still be able to complete if close is refused.
        This is separate from the ordinary debounce/autosave path.
        """
        if not self.configuration_close_requires_save:
            return True
        if self.configuration_change_pending or self.backend.lifecycle_pending:
            self.toast("配置切换尚未完成，未保存更改仍保留，请稍后重试关闭", kind="warning")
            return False
        draft = getattr(self, "_unsaved_profile_name", None)
        accepted = self._accepted_profile_identity()
        intent = self._profile_saves.close_intent(draft, accepted)
        if intent is None:
            return True  # A superseded timer owns no part of the live model.
        self._save_profile(intent.profile, announce=True, explicit=intent.explicit)
        return not self.configuration_close_requires_save

    def cancel_pending_configuration_for_close(self) -> None:
        """Discard an uncommitted profile before final save; never restart runtime."""
        pending = getattr(self, "_pending_configuration", None)
        if pending is None:
            return
        try:
            pending.original.restore()
            self._restore_configuration_view(pending.previous)
        finally:
            pending.original.restore()
            self.control.binds = dict(pending.previous.control_binds)
            self.home_visible = pending.previous.home_visible
            self.startup_step = pending.previous.startup_step
            self._pending_configuration = None

    def ensure_configuration_idle(self) -> None:
        if self.configuration_change_pending or self.backend.lifecycle_pending:
            raise ValueError("配置切换尚未完成，请稍后重试")

    def capture_pending_scenario_configuration(self, generation: int) -> None:
        """Capture after intended scenario edits, before returning to UI input.

        Ordinary scenario requests do not own a profile rollback transaction.
        They still need an accepted registry projection so post-mutation direct
        callbacks cannot reopen retired resources or leave rejected UI values.
        """
        self._pending_scenario_configuration = PendingScenarioConfiguration(
            generation, RegistrySnapshot.capture(self.registry))

    def reject_pending_configuration_edit(self) -> bool:
        """Reject runtime effects and restore the accepted pending projection."""
        if self.config_loading:
            return False
        pending = getattr(self, "_pending_configuration", None)
        if pending is not None:
            pending.staged.restore()
        elif self.backend.lifecycle_pending:
            ordinary = getattr(self, "_pending_scenario_configuration", None)
            if ordinary is not None and ordinary.generation == self.backend.lifecycle_snapshot.generation:
                ordinary.staged.restore()
            # Even a programmatic backend request without the window capture
            # hook must not reopen resources through a late direct callback.
        else:
            return False
        self.invalidate_gui_cache()
        self.toast("配置切换尚未完成，当前编辑未应用", kind="warning")
        return True

    def apply_config_data(self, data: dict[str, Any], announce: bool = True) -> None:
        if self.reject_pending_configuration_edit():
            return
        target_profile = self.current_profile()
        accepted = self._accepted_profile_identity()
        # The GUI mutates its selection before calling us. Restore the accepted
        # identity before capturing rollback state or flushing its pending edits.
        self._set_profile_selection(self._restorable_profile_name())
        try:
            validate_client_config(data, self.registry)
        except ValueError as exc:
            self.toast(f"配置加载失败，原配置未更改：{exc}", kind="warning")
            return
        if target_profile != accepted.name:
            self.flush_auto_save(force=True)
            if self._auto_save_pending:
                self.toast("旧配置尚未保存，未切换配置", kind="warning")
                return
        previous = self.capture_configuration_restore_point()
        original = previous.registry_snapshot or RegistrySnapshot.capture(self.registry)
        generation = None

        def update():
            nonlocal generation
            was_loading = self.config_loading
            self.config_loading = True
            try:
                self._set_profile_selection(target_profile)
                self._apply_config_data(data, sync_runtime=False)
                generation = self.sync_runtime_from_registry()
            finally:
                self.config_loading = was_loading

        try:
            apply_configuration_transaction(
                self.registry,
                quiesce=lambda: self._quiesce_config_reload(data), update=update,
                restore_runtime=lambda: self.restore_configuration_runtime(previous),
                fail_closed=lambda: self.fail_closed_configuration(previous, "configuration reload failed"),
            )
        except ConfigurationApplyError as exc:
            self._announce_configuration_failure(exc)
            return
        self._pending_configuration = PendingConfigurationChange(
            generation, ConfigurationPhase.APPLYING, previous, original,
            RegistrySnapshot.capture(self.registry), announce, target_profile=target_profile,
        )

    def record_configuration_failure(self, error: ConfigurationApplyError) -> None:
        """Retain local rollback failures until pending runtime recovery ends."""
        pending = getattr(self, "_pending_configuration", None)
        if pending is not None and pending.phase is ConfigurationPhase.RESTORING:
            self._pending_configuration = replace(pending,
                restore_errors=tuple(str(item) for item in error.restore_errors),
                safety_error=str(error.safety_error) if error.safety_error is not None else None)

    def _announce_configuration_failure(self, error: ConfigurationApplyError) -> None:
        self.record_configuration_failure(error)
        if error.safety_error is not None:
            self.toast(f"配置加载失败，安全清理未完成，请使用物理急停：{error}", kind="warning")
        elif error.restore_pending:
            self.toast(f"配置加载失败，正在恢复停用运行时；输出保持锁定：{error.cause}", kind="warning")
        elif error.restored:
            self.toast(f"配置加载失败，已恢复设置并锁定输出；需操作员解除急停：{error.cause}", kind="warning")
        else:
            self.toast(f"配置加载失败，输出已锁定；恢复未完成：{error}", kind="warning")

    def on_backend_lifecycle_completion(self, completion) -> bool:
        """Called only by the window's single lifecycle poller, on its thread."""
        ordinary = getattr(self, "_pending_scenario_configuration", None)
        if ordinary is not None and completion.generation >= ordinary.generation:
            self._pending_scenario_configuration = None
        pending = getattr(self, "_pending_configuration", None)
        if pending is None:
            return False
        action = pending.completion_action(completion.generation,
            succeeded=completion.succeeded, final=completion.final)
        if action is CompletionAction.IGNORE:
            return False
        if action is CompletionAction.CANCEL:
            try:
                self.cancel_pending_configuration_for_close()
            except Exception as error:
                self.toast(f"关闭前配置恢复未完成：{error}", kind="warning")
            return False
        if action is CompletionAction.COMMIT:
            try:
                self._complete_registry_runtime()
            except Exception as error:
                self._rollback_pending_configuration(pending, error)
            else:
                self._accept_profile_selection(pending.target_profile or self.current_profile())
                self._pending_configuration = None
                if pending.announce:
                    self.toast(f"配置已加载：{self.current_profile()}")
            return True
        if action is CompletionAction.ROLLBACK:
            detail = "; ".join(failure.error for failure in completion.failures)
            error = RuntimeError(detail or "configuration lifecycle generation was superseded")
            self._rollback_pending_configuration(pending, error)
            return True
        self._finish_configuration_restore(pending, completion)
        return True

    def _rollback_pending_configuration(self, pending: PendingConfigurationChange, cause: Exception) -> None:
        self._pending_configuration = None
        failure = rollback_configuration_transaction(
            pending.original, cause=cause,
            restore_runtime=lambda: self.restore_configuration_runtime(pending.previous),
            fail_closed=lambda: self.fail_closed_configuration(pending.previous, "configuration activation failed"),
        )
        self._announce_configuration_failure(failure)

    def _finish_configuration_restore(self, pending: PendingConfigurationChange, completion) -> None:
        errors = [*pending.restore_errors, *(failure.error for failure in completion.failures)]
        if completion.succeeded:
            try:
                self._complete_registry_runtime()
            except Exception as error:
                errors.append(str(error))
        try:
            pending.original.restore()
        except Exception as error:
            errors.append(str(error))
        safety_error = pending.safety_error
        try:
            self.fail_closed_configuration(pending.previous, "configuration restored locked")
        except Exception as error:
            safety_error = error
        self._pending_configuration = None
        if safety_error is not None:
            self.toast(f"配置恢复时安全清理未完成，请使用物理急停：{safety_error}", kind="warning")
        elif errors:
            self.toast("输出保持锁定；配置恢复未完成：" + "; ".join(errors), kind="warning")
        else:
            self.toast("已恢复设置并锁定输出；需操作员解除急停", kind="warning")

    def capture_configuration_restore_point(self) -> ConfigurationRestorePoint:
        return ConfigurationRestorePoint(
            document=deepcopy(self.current_config_data()),
            control_binds=dict(self.control.binds),
            home_visible=self.home_visible,
            startup_step=self.startup_step,
            registry_snapshot=RegistrySnapshot.capture(self.registry),
        )

    def _quiesce_config_reload(self, data: dict[str, Any]) -> None:
        spin = self.registry.get("SpinMode")
        candidate_spin = data.get("modules", {}).get("SpinMode", {})
        spin_stops = candidate_spin.get("enabled") is False or (
            candidate_spin.get("settings", {}).get("Mock Output") is True
            and not self.setting_bool(spin, "Mock Output", True)
        )
        if spin is not None and spin.enabled and spin_stops:
            # Only the falling edge is published on the old transport/identity.
            self.publish_spin_control_state(False, source="config_reload")
        quiesce_configuration_inputs(
            revoke=lambda: self.backend.lock_control("configuration reload"),
            release=lambda: self.control.release_all(self.state, self.registry),
            clear_pending=lambda: self.control.clear_pending(),
        )

    def _restore_configuration_view(self, previous: ConfigurationRestorePoint) -> None:
        # RegistrySnapshot already restored exact values without enable hooks.
        # Restore only view projections here; replaying module data could invoke
        # hooks if a partially failing model restore left a different value.
        view = {key: value for key, value in previous.document.items() if key != "modules"}
        self._apply_config_data(view, sync_runtime=False)

    def restore_configuration_runtime(self, previous: ConfigurationRestorePoint) -> RuntimeRestorePending:
        """Request prior runtime with outputs stopped; completion owns success."""
        original = previous.registry_snapshot or RegistrySnapshot.capture(self.registry)
        was_loading = self.config_loading
        self.config_loading = True
        try:
            original.restore()
            self._restore_configuration_view(previous)
            generation = self._request_registry_runtime(start=False)
            self._pending_configuration = PendingConfigurationChange(
                generation, ConfigurationPhase.RESTORING, previous, original, original,
            )
            return RuntimeRestorePending(generation)
        finally:
            self.config_loading = was_loading

    def fail_closed_configuration(self, previous: ConfigurationRestorePoint, reason: str) -> None:
        # These local restoration steps must run even if a driver failed while
        # rebuilding stopped runtime state. Held inputs intentionally stay clear.
        self.control.binds = dict(previous.control_binds)
        self.control.mock_output = bool(self.backend.mqtt.mock_output)
        self.home_visible = previous.home_visible
        self.startup_step = previous.startup_step
        try:
            self.backend.trigger_estop()
        finally:
            self.backend.lock_control(reason)

    def _apply_config_data(self, data: dict[str, Any], *, sync_runtime: bool = True) -> None:
        was_loading = self.config_loading
        self.config_loading = True
        try:
            self.refresh_wallpaper_profiles()
            self.refresh_custom_ui_sound_options()
            # Normalize legacy key casing before generic mode-option loading
            # can discard it. Validate the complete saved permutation, so an
            # invalid key cannot silently leave a different partial mapping.
            modules = data.get("modules", {})
            link_data = modules.get("RobotLink", {})
            saved_settings = link_data.get("settings", {})
            saved_binds = {slot: saved_settings[f"Bind {slot}"] for slot in CONTROL_SLOT_ORDER
                           if f"Bind {slot}" in saved_settings}
            if saved_binds:
                binds = validate_control_binds({**self.control.binds, **saved_binds})
                settings = {**saved_settings, **{f"Bind {slot}": binds[slot] for slot in saved_binds}}
                data = {**data, "modules": {**modules, "RobotLink": {**link_data, "settings": settings}}}
            apply_registry_config(self.registry, data)
            # Runtime profile reload can change HUD tone between two display
            # ticks; discard the frame-local palette immediately.
            self._hud_palette_cache = None
            gui_mode = normalize_gui_mode(str(data.get("gui_mode", self.mode_manager.mode)))
            self.mode_manager.set_mode(gui_mode)
            self.selected_category = str(data.get("selected_category", self.selected_category))
            selected_name = str(data.get("selected_module", self.selected_module.name if self.selected_module else ""))
            selected = self.registry.get(selected_name)
            if selected:
                self.selected_module = selected
            expanded = data.get("module_expanded")
            if isinstance(expanded, list):
                self.module_expanded = {str(name) for name in expanded}
            for target, key in ((self.old_panels, "old_panels"), (self.new_panels, "new_panels"), (self.dropdown_panels, "dropdown_panels")):
                value = data.get(key)
                if isinstance(value, dict):
                    for name, panel_data in value.items():
                        if name in target and isinstance(panel_data, dict):
                            target[name].update(panel_data)
            polar_data = data.get("polar_windows", data.get("vape_windows"))
            if isinstance(polar_data, dict):
                for name, panel_data in polar_data.items():
                    if name in self.polar_windows and isinstance(panel_data, dict):
                        self.polar_windows[name].update(panel_data)
            restore_wheel_profile(
                self.registry.get("QuickActionWheel"), self.quick_action_wheel,
                data.get("quick_action_wheel", {}),
            )
            if sync_runtime:
                self.sync_runtime_from_registry()
            self.sync_mode_setting()
            self.refresh_config_profiles()
            self.refresh_wallpaper_profiles()
        finally:
            self.config_loading = was_loading

    def sync_control_binds(self) -> None:
        """把 ClickGUI 里的“Bind <槽位>”同步到键盘控制桥。

        前端可自定义驾驶键，而后端发的永远是协议槽位；这里就是两者的唯一汇合点。
        先校验完整映射，再原子替换，支持交换或轮换绑定；非法映射整体回滚。
        """

        link = self.registry.get("RobotLink")
        if link is None or self.control is None:
            return
        settings = {slot: link.get_setting(f"Bind {slot}") for slot in CONTROL_SLOT_ORDER}
        candidate = dict(self.control.binds)
        candidate.update({slot: setting.value for slot, setting in settings.items() if setting is not None})
        try:
            candidate = validate_control_binds(candidate)
        except ValueError as exc:
            for slot, setting in settings.items():
                if setting is not None:
                    setting.value = self.control.binds.get(slot, slot)
            if self.config_loading:
                # Let apply_config_data restore its full snapshot and keep
                # output locked rather than report a partially loaded profile.
                raise
            self.toast(f"控制绑定未更改：{exc}", kind="warning")
            return
        if candidate != self.control.binds:
            quiesce_configuration_inputs(
                revoke=lambda: self.backend.lock_control("control bindings changed"),
                release=lambda: self.control.release_all(self.state, self.registry),
                clear_pending=lambda: self.control.clear_pending(),
            )
            self.control.set_binds(candidate)
        for slot, setting in settings.items():
            if setting is not None:
                setting.value = candidate[slot]

    def sync_runtime_from_registry(self) -> int:
        """Request saved runtime without waiting for resource teardown on Qt."""
        return self._request_registry_runtime()

    def _request_registry_runtime(self, *, start: bool | None = None) -> int:
        self.sync_control_binds()
        match = self.registry.get("MatchMode")
        self.match_mode_manager.set_mode(self.setting_mode(match, "Mode", "RMUC7v7"))
        scenario = self.registry.get("Scenario")
        self.scenario_manager.set_scenario(self.setting_mode(scenario, "Mode", "LabDebug"))
        profile = profile_by_key(self.match_mode_manager.current.key, self.current_robot_profile_key())
        module = self.registry.get("RobotProfile")
        role = module.get_setting("Role") if module else None
        if role is not None:
            role.options = [item.key for item in profiles_for_match(self.match_mode_manager.current.key)]
            role.value = profile.key
        robot_id = robot_id_for_alliance(profile, self.alliance_side())
        if start is None:
            autostart = os.environ.get("POLAR_AUTOSTART", "").lower() in ("1", "true", "yes")
            start = autostart or not self.setting_bool(scenario, "Show Startup", True)
            self.home_visible = not start
            self.startup_step = "scenario" if self.home_visible else "done"
        # Defaults belong to explicit scenario switches. Capture saved endpoint,
        # mock, referee and identity values together before asynchronous teardown.
        return self.backend.request_scenario(
            self.scenario_manager.current.key, start=start,
            registry=self.registry, robot_id=robot_id,
        )

    def _complete_registry_runtime(self) -> None:
        """Commit display identity after backend activation; never arm or start."""
        self.control.mock_output = bool(self.backend.mqtt.mock_output)
        self.seed_match_state(self.match_mode_manager.current)
        profile = profile_by_key(self.match_mode_manager.current.key, self.current_robot_profile_key())
        module = self.registry.get("RobotProfile")
        level = int(self.get_setting_number(module, "Level", 1))
        side = self.alliance_side()
        self.state.alliance_color = side
        match = self.registry.get("MatchMode")
        legacy_side = match.get_setting("Alliance") if match else None
        if legacy_side is not None:
            legacy_side.value = side
        apply_profile_to_state(self.state, profile, self.current_robot_performance(profile.key), level)

    def save_current_config(self, announce: bool = True) -> None:
        """Explicit save accepts the selected name, including save-as callers."""
        if self.configuration_change_pending:
            if announce:
                self.toast("配置切换尚未完成，未保存", kind="warning")
            return
        self._save_profile(self.current_profile(), announce=announce, explicit=True)

    def _save_profile(self, profile: str, *, announce: bool, explicit: bool = False) -> None:
        accepted = self._accepted_profile_identity()
        try:
            module = self.registry.get("ConfigManager")
            path = self.config_store.save_with_startup(
                profile, self.current_config_data(),
                load_on_start=self.setting_bool(module, "Load On Start", True),
            )
        except (OSError, ValueError) as exc:
            recovery_failed = isinstance(exc, ProfileSaveError) and not exc.restored
            if recovery_failed:
                self._last_profile_recovery_error = f"{profile}: {exc}"
                logging.getLogger(__name__).error("Profile file recovery incomplete: %s",
                                                 self._last_profile_recovery_error, exc_info=True)
            if profile != accepted.name or getattr(self, "_unsaved_profile_name", None) is not None:
                # A failed explicit save-as must not become an automatic write
                # to its source. Keep the chosen name and draft for manual retry.
                self._profile_saves.begin_draft(profile)
                self._set_profile_selection(profile)
                self._profile_saves.cancel_pending()
                operation = "另存" if profile != accepted.name else "草稿保存"
                self.toast(f"{operation}失败，当前更改尚未保存，请重试保存：{exc}", kind="warning")
                return
            self._set_profile_selection(accepted.name)
            self._profile_saves.defer_save(lambda: accepted, lambda: time.perf_counter(), 5.0)
            retry = "请手动保存或重试关闭" if getattr(self, "_close_save_retry_required", False) else "将重试"
            if recovery_failed:
                self.toast(f"配置保存失败，文件恢复未完成，{retry}：{exc}", kind="warning")
            elif announce or not getattr(self, "_config_save_error", False):
                self.toast(f"配置保存失败，{retry}：{exc}", kind="warning")
            self._config_save_error = True
            return
        if explicit or profile != accepted.name:
            self._accept_profile_selection(profile)
        self._profile_saves.complete_save()
        self.refresh_config_profiles()
        if announce:
            self.toast(f"配置已保存：{path.name}")

    def reload_current_config(self) -> None:
        data = self.config_store.load(self.current_profile())
        if data is None:
            self._set_profile_selection(self._restorable_profile_name())
            self.toast("配置重新加载失败")
            return
        self.apply_config_data(data)

    def import_config_from_dialog(self) -> None:
        if self.reject_pending_configuration_edit():
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入 POLAR-RM 配置", str(self.config_store.config_dir), "JSON 配置 (*.json)")
        if not path:
            return
        # Import may replace the accepted file itself. Save its pending edits
        # first so the import backup contains them and no old timer rewrites
        # the imported bytes if activation subsequently fails.
        self._set_profile_selection(self._restorable_profile_name())
        self.flush_auto_save(force=True)
        if self._auto_save_pending:
            self.toast("旧配置尚未保存，未导入配置", kind="warning")
            return
        try:
            profile = self.config_store.import_file(Path(path), registry=self.registry)
        except (OSError, ValueError) as exc:
            self.toast(f"Import failed: {exc}")
            return
        self.refresh_config_profiles()
        module = self.registry.get("ConfigManager")
        setting = module.get_setting("Profile") if module else None
        if setting:
            setting.value = profile
        self.reload_current_config()

    def auto_save_config(self) -> None:
        module = self.registry.get("ConfigManager")
        if self.config_loading or self.configuration_change_pending or not self.setting_bool(module, "Auto Save", True):
            return
        if getattr(self, "_unsaved_profile_name", None) is not None:
            return  # Failed save-as drafts require explicit retry or another load.
        if self.current_profile() != self._accepted_profile_identity().name:
            return
        self._profile_saves.defer_save(
            lambda: self._accepted_profile_identity(), lambda: time.perf_counter(), 0.25,
        )

    def flush_auto_save_if_due(self, now: float | None = None) -> None:
        if not self._auto_save_pending or self.configuration_change_pending:
            return
        if self._profile_saves.automatic_save_blocked:
            return
        now = time.perf_counter() if now is None else float(now)
        if not self._profile_saves.is_due(now):
            return
        accepted = self._accepted_profile_identity()
        owner = self._profile_saves.save_identity(accepted)
        if owner is None:
            return  # A superseded callback cannot save the current registry.
        self._save_profile(owner.name, announce=False)

    def flush_auto_save(self, *, force: bool = False) -> None:
        if not self._auto_save_pending:
            return
        if force:
            self._auto_save_due_at = 0.0
        self.flush_auto_save_if_due()

    def sync_mode_setting(self) -> None:
        click_gui = self.registry.get("ClickGui")
        if click_gui:
            mode = click_gui.get_setting("Mode")
            if mode:
                mode.value = self.mode_manager.mode

    def panel_modules(self) -> list[Module]:
        if self.search_query:
            query = self.search_query.lower()
            return sorted([m for m in self.registry.modules if query in m.name.lower()], key=lambda module: module.name.lower())
        return self.registry.by_category(self.selected_category)

    def get_setting_number(self, module: Module | None, name: str, default: float) -> float:
        setting = module.get_setting(name) if module else None
        if setting is None:
            return default
        try:
            return float(setting.value)
        except (TypeError, ValueError):
            return default

    def get_auto_ui_scale(self, width: int | None = None, height: int | None = None, strength: float = 1.0) -> float:
        module = self.registry.get("UIScale")
        if not module or not module.enabled or not self.setting_bool(module, "Auto Scale", True):
            return 1.0
        width = self.width() if width is None else width
        height = self.height() if height is None else height
        ref_width = max(320.0, self.get_setting_number(module, "Reference Width", 1280.0))
        ref_height = max(240.0, self.get_setting_number(module, "Reference Height", 720.0))
        raw = math.sqrt(max(1.0, width * height) / (ref_width * ref_height))
        raw = max(0.82, min(1.85, raw))
        if raw < 1.0:
            return raw
        softened = 1.0 + (raw - 1.0) * max(0.0, min(1.0, strength))
        return max(0.82, min(1.85, softened))

    def get_hud_scale(self, width: int | None = None, height: int | None = None) -> float:
        module = self.registry.get("UIScale")
        manual = self.get_setting_number(module, "HUD Scale", 1.0)
        auto_scale = self.get_auto_ui_scale(width, height, strength=0.80)
        if self.high_res_optimize(width, height):
            auto_scale = min(auto_scale, 1.34)
        return max(0.55, min(2.4, manual * auto_scale))

    def high_res_optimize(self, width: int | None = None, height: int | None = None) -> bool:
        module = self.registry.get("UIScale")
        if not self.setting_bool(module, "High Res Optimize", True):
            return False
        width = self.width() if width is None else width
        height = self.height() if height is None else height
        return width * height >= 3_000_000

    def video_sample_size(self, width: int, height: int) -> tuple[int, int]:
        from ...cs2_video_settings import sampled_video_size
        override = sampled_video_size(width,height)
        if override is not None:
            return override
        if not self.high_res_optimize(width, height):
            return (width, height)
        max_pixels = 1920 * 1080
        pixels = max(1, width * height)
        if pixels <= max_pixels:
            return (width, height)
        scale = math.sqrt(max_pixels / pixels)
        return (max(320, int(width * scale)), max(180, int(height * scale)))

    def get_gui_scale(self) -> float:
        module = self.registry.get("ClickGui")
        setting = module.get_setting("Scale") if module else None
        ui_module = self.registry.get("UIScale")
        gui_scale = self.get_setting_number(ui_module, "GUI Scale", 1.0)
        base = float(setting.value if setting else 1.0)
        return max(0.55, min(1.85, base * gui_scale * self.get_auto_ui_scale(strength=0.52)))

# ---------------------------------------------------------------------------
# API 冻结面 qualname 固定（见模块 docstring「冻结面纪律」；先例：
# ui/polar/text_metrics.py 与 renderers/combat_target_hud.py 底部同一手法）。
# ---------------------------------------------------------------------------

_QUALNAME_PINNED_MEMBERS = (
    PersistenceMixin.current_robot_profile_key,
    PersistenceMixin.current_robot_performance,
    PersistenceMixin.apply_match_mode,
    PersistenceMixin.apply_robot_profile,
    PersistenceMixin.seed_match_state,
    PersistenceMixin.refresh_config_profiles,
    PersistenceMixin.refresh_wallpaper_profiles,
    PersistenceMixin.refresh_custom_ui_sound_options,
    PersistenceMixin.custom_ui_sound_selection,
    PersistenceMixin.import_custom_ui_sound,
    PersistenceMixin.import_wallpaper_from_dialog,
    PersistenceMixin.apply_wallpaper_profile,
    PersistenceMixin.remove_wallpaper_profile,
    PersistenceMixin.reset_wallpaper_profile,
    PersistenceMixin.current_profile,
    PersistenceMixin.current_config_data,
    PersistenceMixin.load_startup_config,
    PersistenceMixin.apply_config_data,
    PersistenceMixin._apply_config_data,
    PersistenceMixin.sync_runtime_from_registry,
    PersistenceMixin.save_current_config,
    PersistenceMixin.reload_current_config,
    PersistenceMixin.import_config_from_dialog,
    PersistenceMixin.auto_save_config,
    PersistenceMixin.flush_auto_save_if_due,
    PersistenceMixin.flush_auto_save,
    PersistenceMixin.sync_mode_setting,
    PersistenceMixin.panel_modules,
    PersistenceMixin.get_setting_number,
    PersistenceMixin.get_auto_ui_scale,
    PersistenceMixin.get_hud_scale,
    PersistenceMixin.high_res_optimize,
    PersistenceMixin.video_sample_size,
    PersistenceMixin.get_gui_scale,
)

for _fn in _QUALNAME_PINNED_MEMBERS:
    _fn.__qualname__ = f"PolarRMWindow.{_fn.__name__}"
del _fn, _QUALNAME_PINNED_MEMBERS
