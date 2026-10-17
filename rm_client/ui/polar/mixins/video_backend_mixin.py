"""Window adapters for video, vision, combat feedback and legacy HUD actions.

The window owns the registry, frontend state, capture workers and renderers.
These methods translate between those collaborators, select video sources and
present explicit recovery actions. Worker stop admission and owner-thread
lifecycle completion are separate from native cleanup confirmation.

This legacy mixin also retains hero deployment, Minecraft slots and combat-event
presentation. Its methods share the full window through ``self``; moving them
here did not create independently owned domain services. Compatibility is checked
by the explicit window contract and behavior tests, not implementing qualnames.
"""

from __future__ import annotations

import logging
import math
import os
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)

# 相对导入层级：rm_client/ui/polar/mixins/ → .... = rm_client、... = rm_client.ui
from ....backend import BackendManager
from ....combat_events import CombatEvent
from ....hero_lob import HeroLobPlanner
from ....model import Module
from ....vision import (
    CapturedFrame,
    VisionResult,
    VisionSnapshot,
    VisionTarget,
    snapshot_from_local_result,
    snapshot_from_tongji_packet,
)
from ..primitives import EconomyScenePhase
from ...clickgui_gpu import ClickGuiDrawList
from ...gpu_frame_compositor import GlassOwner
from ...hero_locale import hero_text
from ...jello_locale import chinese_enabled
from ...overlay_coordinator import OverlayKind
from ...paint import glow_rect, rounded_rect
from ...theme import color
from ..video_recovery import (
    VIDEO_RETRY_ACTION, VIDEO_WAIT_ACTION, VideoRecoveryNotice, VideoRetryToken, video_recovery_notice,
)

_LOG = logging.getLogger(__name__)

__all__ = [
    "VideoBackendMixin",
]


class VideoBackendMixin:
    """D7 视频管线/后端接线/英雄部署 + D18 官方事件/击杀特效 + D14 Minecraft 功能槽。"""

    def append_minecraft_drop_overlay(self, commands: ClickGuiDrawList) -> None:
        drag = self.drag
        if not drag or drag.get("type") != "jello_module" or not drag.get("moved"):
            return
        module = self.registry.get("MinecraftCombatHUD")
        if module is None or not self.minecraft_hud.available:
            return
        point = drag.get("point")
        if not isinstance(point, QPointF):
            return
        override = self.setting_mode(module, "GUI Scale", "Auto")
        multiplier = self.get_setting_number(module, "Scale Multiplier", 1.0)
        scale = self.minecraft_hud.layout(
            self.width(),
            self.height(),
            override,
            scale_multiplier=multiplier,
        ).scale
        self.minecraft_hud.append_drop_overlay(
            commands,
            self.width(),
            self.height(),
            str(drag.get("module", "")),
            point,
            self.minecraft_hud.assignments(module),
            self.font("jello_medium", max(6.0, 4.5 * scale)),
            override,
            multiplier,
            registry=self.registry,
        )

    def assign_minecraft_function_slot(self, index: int, module_name: str) -> bool:
        function_bar = self.registry.get("MinecraftCombatHUD")
        target = self.registry.get(str(module_name))
        if function_bar is None or target is None or target is function_bar:
            return False
        if not 0 <= int(index) < 9:
            return False
        setting = function_bar.get_setting(f"Slot {int(index) + 1}")
        if setting is None:
            return False
        if module_name not in setting.options:
            setting.options.append(module_name)
        setting.value = module_name
        self.minecraft_hud.selected_slot = int(index)
        self.toast(f"MC slot {int(index) + 1}: {module_name}")
        self.auto_save_config()
        self.invalidate_gui_cache()
        return True

    def activate_minecraft_function_slot(self, index: int) -> bool:
        function_bar = self.registry.get("MinecraftCombatHUD")
        if function_bar is None or not 0 <= int(index) < 9:
            return False
        assignments = self.minecraft_hud.assignments(function_bar)
        name = assignments[int(index)]
        self.minecraft_hud.selected_slot = int(index)
        if name == "Empty":
            self.toast(f"MC slot {int(index) + 1} is empty")
            return True
        if name == "ClickGui":
            self.set_clickgui_visible(True)
            return True
        if name == "EconomyPanel":
            self.open_economy_workspace()
            return True
        if name == "ProjectileShop":
            self.open_economy_workspace("ammo")
            return True
        if name == "MatchChat":
            self.open_match_chat()
            return True
        from ....student_ui import PANEL_MODULES
        if name in PANEL_MODULES:
            self.open_student_panel(PANEL_MODULES[name])
            return True
        target = self.registry.get(name)
        if target is None:
            self.toast(f"MC slot target missing: {name}")
            return True
        self.registry.toggle_module(target, source="keybind")
        self.selected_module = target
        self.toast(f"{target.name} {'enabled' if target.enabled else 'disabled'}")
        self.auto_save_config()
        return True

    def hero_range_profile_selected(self) -> bool:
        profile = self.registry.get("RobotProfile")
        return bool(
            self.state.robot_profile == "Hero"
            and self.setting_mode(profile, "Hero Type", "Melee") == "Range"
        )

    def hero_deploy_video_cut_active(self) -> bool:
        """Only acknowledged deploy status cuts video; a pending request does not."""

        return int(self.state.deploy_mode) == 1

    def hero_deploy_uses_mock_transport(self) -> bool:
        module = self.registry.get("HeroLob")
        return bool(
            self.scenario_manager.current.key != "OfficialEvent"
            or self.setting_bool(module, "Mock Output", True)
        )

    def open_hero_deploy_interface(self) -> bool:
        self.close_student_panel(immediate=True)
        if self.home_visible:
            return False
        module = self.registry.get("HeroLob")
        if module is None:
            return False
        if not module.enabled and int(self.state.deploy_mode) != 1:
            self.toast("Enable HeroLob before opening the deploy console")
            return False
        if self.economy_phase != EconomyScenePhase.CLOSED:
            self._economy_pending_overlay = OverlayKind.HERO_DEPLOY
            if self.economy_phase != EconomyScenePhase.CLOSING:
                self.close_economy_workspace(play_sound=False)
            return True
        if self.gui_visible:
            self.set_clickgui_visible(False, announce=False, play_sound=False)
        if self.economy_visible:
            self.close_economy_workspace(play_sound=False)
        self.close_match_chat(clear=False)
        self.cancel_quick_action_wheel()
        self.overlay_coordinator.request_modal(OverlayKind.HERO_DEPLOY)
        self.hero_deploy_ui.open(time.perf_counter())
        self.invalidate_transient_overlay_scene()
        self.play_gui_transition_sound("expand")
        self.frame_scheduler.force()
        self.update()
        return True

    def close_hero_deploy_interface(self, *, force: bool = False) -> bool:
        if self.hero_deploy_video_cut_active():
            self.toast("Exit deploy mode and wait for DeployModeStatusSync before closing")
            return False
        if not self.hero_deploy_ui.visible:
            if force and self.hero_deploy_ui.render_visible:
                self.hero_deploy_ui.close(time.perf_counter(), immediate=True)
                self.invalidate_transient_overlay_scene()
            self.overlay_coordinator.close_modal(OverlayKind.HERO_DEPLOY)
            return True
        if self.hero_deploy_ui.pending_mode is not None and not force:
            self.toast("Wait for DeployModeStatusSync before closing the console")
            return False
        self.hero_deploy_ui.close(time.perf_counter(), immediate=force)
        self.overlay_coordinator.close_modal(OverlayKind.HERO_DEPLOY)
        self.invalidate_transient_overlay_scene()
        self.play_gui_transition_sound("collapse")
        self.frame_scheduler.force()
        self.update()
        return True

    def send_hero_deploy_request(self, mode: int) -> bool:
        mode = 1 if int(mode) else 0
        module = self.registry.get("HeroLob")
        if self.hero_deploy_ui.pending_mode is not None:
            self.toast("A deploy-mode request is already pending")
            return False
        if mode == int(self.state.deploy_mode):
            self.toast("Deploy mode is already in the requested state")
            return False
        if mode == 1:
            if module is None or not module.enabled:
                self.toast("HeroLob is disabled")
                return False
            if not self.hero_range_profile_selected():
                self.toast("Deploy mode requires the Hero / Range profile")
                return False
            if str(self.state.current_stage).upper() not in {"4", "MATCH"}:
                self.toast("Deploy mode entry is available only during the match stage")
                return False

        now = time.perf_counter()
        mock = self.hero_deploy_uses_mock_transport()
        if not mock:
            accepted, reason = self.backend.publish_hero_deploy(mode)
            if not accepted:
                self.hero_deploy_ui.last_error = str(reason)
                self.toast(f"Deploy command blocked: {reason}")
                return False
        self.hero_deploy_ui.begin_request(mode, now, mock=mock)
        verb = "ENTER" if mode else "EXIT"
        self.state.hero_lob_status = f"DEPLOY {verb} PENDING"
        self.toast(f"{verb} deploy mode requested ({'mock' if mock else 'official'})")
        self.frame_scheduler.force()
        return True

    def update_hero_deploy_state(self, now: float | None = None) -> None:
        now = time.perf_counter() if now is None else float(now)
        previous_mode = int(self._last_deploy_mode)
        mock_status = self.hero_deploy_ui.sync(self.state.deploy_mode, now)
        if mock_status is not None:
            if int(mock_status) != int(self.state.deploy_mode):
                self.state.deploy_status_revision += 1
            self.state.deploy_mode = int(mock_status)
            self.state.deploy_status_at = now
        revision = int(self.state.deploy_status_revision)
        current_mode = int(self.state.deploy_mode)
        status_changed = current_mode != previous_mode
        if revision != self._last_deploy_status_revision or status_changed:
            self._last_deploy_status_revision = revision
            self.invalidate_transient_overlay_scene()
        if status_changed and current_mode == 1:
            # The judge-system acknowledgement is authoritative.  Stop the
            # normal webcam sampler and make the Apple-island/
            # AndroidLiquidGlass console the locked foreground workspace.
            self._camera_activation.pause_admitted_capture()
            self._hero_native_wait_started = now
            if self.economy_phase != EconomyScenePhase.CLOSED:
                self._economy_generation += 1
                self.economy_visible = False
                self.economy_phase = EconomyScenePhase.CLOSED
                self._economy_closing_generation = None
                self._economy_restore_pending_generation = None
                self._economy_close_finalized = True
                self.economy_island.progress = 0.0
                self.economy_island.target = 0.0
                self.economy_island.velocity = 0.0
                self._economy_pending_overlay = None
            if self.gui_visible:
                self.set_clickgui_visible(False, announce=False, play_sound=False)
            self.close_match_chat(clear=False)
            self.cancel_quick_action_wheel()
            self.overlay_coordinator.request_modal(OverlayKind.HERO_DEPLOY)
            self.hero_deploy_ui.open(now)
            self.consume_hero_native_video_frame()
            self.play_gui_transition_sound("expand")
            self.frame_scheduler.force()
        elif status_changed and current_mode == 0:
            # Preserve the normal frame cache but resume its producer only
            # after the authoritative 1->0 acknowledgement.
            self.hero_deploy_ui.close(now)
            self.overlay_coordinator.close_modal(OverlayKind.HERO_DEPLOY)
            self.ensure_camera_started()
            self.play_gui_transition_sound("collapse")
            self.frame_scheduler.force()
        self._last_deploy_mode = current_mode
        if self.hero_deploy_ui.pending_mode is None:
            if self.hero_deploy_video_cut_active():
                self.state.hero_lob_status = "DEPLOYED / VIDEO CUT"
            elif self.state.hero_lob_status.startswith("DEPLOY "):
                self.state.hero_lob_status = "DEPLOY MODE EXITED"
        elif self.hero_deploy_ui.last_error:
            self.state.hero_lob_status = f"BLOCKED: {self.hero_deploy_ui.last_error}"

    def update_auto_spin_guard(self, now: float | None = None) -> None:
        now = time.perf_counter() if now is None else float(now)
        feature = self.registry.get("AutoSpinOnHit")
        spin = self.registry.get("SpinMode")
        directive = self.auto_spin.update(
            self.state,
            feature,
            spin_enabled=bool(spin and spin.enabled),
            referee_link=bool(self.state.mqtt_connected or self.state.official_events_online),
            now=now,
        )
        if not directive.changed or spin is None:
            return
        requested = directive.action == "enable"
        if spin.enabled != requested:
            self.registry.toggle_module(
                spin,
                source="auto_hit" if requested else "auto_hit_release",
            )
        # The registry listener publishes the separately gated PRM1 kind=2
        # state. KeyboardMouseControl remains the physical key/mouse stream;
        # 0x0311 is never written to the 115200 referee serial link.
        message = (
            f"AutoSpin ON: {directive.reason}"
            if requested
            else "AutoSpin OFF: hit window complete"
        )
        self.state.events.appendleft(message)
        self.toast(message)

    def draw_hero_deploy_interface(self, p: QPainter, width: int, height: int, now: float) -> None:
        if not self.hero_deploy_ui.render_visible:
            if self.hero_deploy_video_cut_active():
                banner = QRectF(width * 0.5 - 150.0, 12.0, 300.0, 32.0)
                self.draw_hud_liquid_glass(p, banner, 16.0, surface="LensIsland")
                rounded_rect(p, banner, 16.0, color("#000000", 0.08), color("#ffffff", 0.13), 0.8)
                icon = QRectF(banner.left() + 5.0, banner.top() + 4.0, 24.0, 24.0)
                self.draw_dynamic_island_icon(p, icon, "success", QColor("#62e6ad"), 1.0)
                self.draw_glass_text(
                    p,
                    banner.adjusted(34.0, 0.0, -10.0, 0.0),
                    hero_text("DEPLOYED · VIDEO CUT · K TO OPEN", chinese_enabled(self.registry)),
                    self.font("poppins_bold", 7.5),
                    QColor("#f4fbff"),
                    Qt.AlignmentFlag.AlignCenter,
                )
                self.add_hotspot(banner, "hero_deploy_open", tooltip=hero_text("Open Hero deploy console", chinese_enabled(self.registry)))
            return
        module = self.registry.get("HeroLob")
        solution, readiness = HeroLobPlanner.preview_from_state(
            self.state,
            module,
            hero_range_profile=self.hero_range_profile_selected(),
        )
        self.gpu_compositor.push_glass_owner(GlassOwner.HERO_DEPLOY)
        try:
            targets = self.hero_deploy_renderer.draw(
                p,
                width,
                height,
                state=self.state,
                module=module,
                interface=self.hero_deploy_ui,
                solution=solution,
                readiness=readiness,
                now=now,
                font_factory=self.font,
                glass_drawer=self.draw_hud_liquid_glass,
                text_drawer=self.draw_glass_text,
                island_icon_drawer=self.draw_dynamic_island_icon,
                pointer=self.hover_point,
                registry=self.registry,
            )
        finally:
            self.gpu_compositor.pop_glass_owner()
        for target in targets:
            if target.enabled:
                self.add_hotspot(target.rect, target.action, tooltip=target.tooltip)
        layout = self.hero_deploy_renderer.layout(width, height, self.hero_deploy_ui.progress)
        if (
            self.hero_deploy_ui.visible
            and layout.interactive
            and not self.hero_deploy_video_cut_active()
            and self.hero_deploy_ui.pending_mode is None
        ):
            self.add_hotspot(
                layout.close_button,
                "hero_deploy_close",
                tooltip=hero_text("Close Hero deploy controls", chinese_enabled(self.registry)),
            )

    def hero_lob_preview(self, *, announce: bool = True) -> tuple[Any, Any]:
        module = self.registry.get("HeroLob")
        solution, readiness = HeroLobPlanner.preview_from_state(
            self.state,
            module,
            hero_range_profile=self.hero_range_profile_selected(),
        )
        if solution.reachable:
            self.state.hero_lob_preview_yaw_deg = solution.yaw_deg
            self.state.hero_lob_preview_pitch_deg = solution.pitch_deg
            self.state.hero_lob_flight_time_s = solution.flight_time_s
            self.state.hero_lob_status = (
                f"PREVIEW Y{solution.yaw_deg:+.1f} P{solution.pitch_deg:+.1f} T{solution.flight_time_s:.2f}"
                if readiness.ready
                else readiness.summary
            )
        else:
            self.state.hero_lob_status = f"BLOCKED: {solution.reason}"
        if announce:
            self.toast(self.state.hero_lob_status)
        return solution, readiness

    def update_hero_lob_guard(self) -> None:
        module = self.registry.get("HeroLob")
        if module is None or not module.enabled:
            self.state.hero_lob_armed = False
            if module is None or self.state.hero_lob_status.startswith("ARMED"):
                self.state.hero_lob_status = "OFF"
            return
        if not self.state.hero_lob_armed:
            return
        _solution, readiness = self.hero_lob_preview(announce=False)
        if not readiness.ready:
            self.state.hero_lob_armed = False
            self.state.hero_lob_status = readiness.summary

    def ensure_camera_started(self) -> None:
        if self._window_lifecycle.closing:
            return
        if os.environ.get("POLAR_DISABLE_LOCAL_CAPTURE", "").lower() in ("1", "true", "yes"):
            self._camera_activation.suspend(reason="local capture disabled")
            return
        source = self.registry.get("CameraFeed")
        source_setting = source.get_setting("Source") if source else None
        if source_setting is not None and source_setting.value != "Webcam":
            # A hidden webcam must not keep sampling or feeding local vision
            # after UDP/Native/Synthetic takes ownership of CameraFeed.
            self._camera_activation.suspend()
            return
        camera_id = int(self.get_setting_number(source, "Camera ID", 0))
        capture_width = int(self.get_setting_number(source, "Capture Width", 960))
        capture_height = int(self.get_setting_number(source, "Capture Height", 540))
        self._camera_activation.ensure(
            (camera_id, capture_width, capture_height),
            bind_frame_sink=self._bind_camera_frame_sink,
            discard_frame=lambda: self.clear_normal_backend_video_frame(),
        )

    def _bind_camera_frame_sink(self) -> None:
        self.camera_worker.on_frame = self.on_captured_camera_frame

    def draw_video(self, p: QPainter, width: int, height: int) -> None:
        if self.hero_deploy_video_cut_active():
            self.draw_hero_native_background(p, width, height)
            return
        source = self.registry.get("CameraFeed")
        source_setting = source.get_setting("Source") if source else None
        use_camera = source_setting is None or source_setting.value == "Webcam"
        normalized_source = (
            "webcam"
            if source_setting is None
            else str(source_setting.value).strip().lower()
        )
        if normalized_source == "synthetic":
            self.draw_synthetic_video(p, width, height)
            return
        if use_camera:
            self._run_tick_stage("camera.runtime", self.ensure_camera_started)
        backend_source = bool(
            not use_camera
            and source_setting is not None
            and str(source_setting.value).strip().lower() != "synthetic"
        )
        if backend_source and not self.normal_backend_video_fresh():
            self.clear_normal_backend_video_frame()
            self.draw_backend_video_lost(p, width, height)
            return
        # ?? UDP MJPEG / ??????? frame_pixmap?QPainter ?????????
        if self.normal_video_frame_ready() and self.frame_pixmap is not None and not self.frame_pixmap.isNull():
            p.drawPixmap(QRectF(0, 0, width, height), self.frame_pixmap, QRectF(self.frame_pixmap.rect()))
            return
        self.draw_synthetic_video(p, width, height)

    def _stop_vision_workers(self, owners) -> Exception | None:
        """Revoke every requested owner before reporting any stop failure.

        Native release stays with each worker. This adapter never joins it;
        final window shutdown uses the separate strict close contract.
        """
        return self._vision_selection.stop_workers(owners, report_failure=self._report_vision_stop_failure)

    def _report_vision_stop_failure(self, name: str, error: Exception) -> None:
        def report_failure():
            raise error
        try:
            self._run_tick_stage(f"vision.stop.{name}", report_failure)
        except Exception:
            # All revocations have already been attempted. Even a broken
            # diagnostic adapter must not replace the operational cause.
            _LOG.error("Vision stop diagnostic failed: %s", name, exc_info=True)

    def refresh_vision_runtime(self) -> None:
        if self._window_lifecycle.closing:
            return
        selection = self._vision_selection
        owners = selection.workers()
        try:
            self.backend.set_active_video_source(
                self.setting_mode(self.registry.get("CameraFeed"), "Source", "")
            )
        except Exception:
            # A failed source transition must not leave old inference or input
            # workers active. Preserve the source error for profile rollback;
            # independent stop faults are reported without hiding that cause.
            self._stop_vision_workers(owners)
            raise
        if os.environ.get("POLAR_DISABLE_LOCAL_CAPTURE", "").lower() in ("1", "true", "yes"):
            selection.disable_capture()
            failure = self._stop_vision_workers(owners)
            if failure is not None:
                raise failure
            return
        bridge_module = self.registry.get("TongjiVisionBridge")
        selection.select_source(os.environ.get(
            "POLAR_VISION_SOURCE", self.setting_mode(bridge_module, "Vision Source", "Local"),
        ))
        bridge_enabled = bool(bridge_module and bridge_module.enabled)
        selection.set_bridge_enabled(bridge_enabled)
        listen = self.setting_bool(bridge_module, "Listen", True)
        receiver_requested = selection.receiver_requested(listen)
        local_enabled = selection.local_enabled(bridge_enabled)
        model_path = self._client_asset_path("同济自瞄", "sp_vision_25", "assets", "yolov5.xml")
        local_requested = local_enabled and model_path.exists()
        # Revoke disabled owners before admitting any replacement. The legacy
        # TCP bridge remains compatibility-only and is always revoked here.
        stops = selection.unrequested_workers(
            receiver_requested=receiver_requested, local_requested=local_requested,
        )
        failure = self._stop_vision_workers(stops)
        if failure is not None:
            raise failure
        if receiver_requested:
            self.tongji_receiver.configure(
                int(self.get_setting_number(bridge_module, "UDP Port", 9871)), wait=False,
            )
            self.tongji_receiver.start()
        if local_requested:
            device = self.setting_mode(bridge_module, "Local Device", "AUTO")
            self.local_vision.configure(model_path, device, wait=False)
            overlay = self.registry.get("RobotOverlay")
            self.local_vision.configure_prediction(
                int(self.get_setting_number(overlay, "Armor Count", 4)),
                self.get_setting_number(overlay, "Radius M", 0.23),
                self.get_setting_number(overlay, "Predict Horizon", 0.06),
            )
            self.local_vision.start()

    def on_captured_camera_frame(self, frame: CapturedFrame) -> None:
        if self.hero_deploy_video_cut_active():
            return
        if self._vision_source in ("Local", "Auto"):
            self.local_vision.submit(frame.sequence, frame.width, frame.height, frame.bgr)

    def consume_hero_native_video_frame(self) -> bool:
        """Read only the judge-system CustomByteBlock/0x0310 decoder."""

        bgr, frame_at, frame_source = self.backend.latest_video_frame(
            0.65,
            "Native 0x0310",
        )
        if frame_at <= self._hero_native_video_at:
            return False
        try:
            source = bgr
            bgr = source.copy() if source is not None else None
        except Exception:
            bgr = None
        if bgr is None or getattr(bgr, "ndim", 0) != 3 or int(bgr.shape[2]) < 3:
            return False
        height, width = int(bgr.shape[0]), int(bgr.shape[1])
        if width <= 0 or height <= 0:
            return False
        image = QImage(
            bgr.data,
            width,
            height,
            int(bgr.strides[0]),
            QImage.Format.Format_BGR888,
        ).copy()
        self._hero_native_video_at = float(frame_at)
        self._hero_native_sequence += 1
        self._hero_native_source = str(frame_source)
        self.hero_native_image = image
        self.hero_native_pixmap = QPixmap.fromImage(image)
        self._gpu_background_key = None
        self._gpu_blur_source_key = None
        self._gpu_background_changed = True
        return True

    def hero_native_frame_state(self, now: float | None = None) -> str:
        now = time.perf_counter() if now is None else float(now)
        if self.hero_native_image is None or self.hero_native_image.isNull():
            return "waiting"
        if self._hero_native_video_at <= 0.0 or now - self._hero_native_video_at > 0.65:
            return "lost"
        return "live"

    def draw_hero_native_background(
        self,
        painter: QPainter,
        width: int,
        height: int,
        *,
        now: float | None = None,
    ) -> str:
        """Draw the sole deployed video source with explicit loss states."""

        state = self.hero_native_frame_state(now)
        canvas = QRectF(0.0, 0.0, float(width), float(height))
        painter.fillRect(canvas, QColor("#02070b"))
        image = self.hero_native_image
        if state == "live" and image is not None and not image.isNull():
            target = self._aspect_fit_image_rect(image, width, height)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(target, image, QRectF(image.rect()))
            if not self.gpu_compositor.available:
                self.liquid_glass.update("hud", image, target)
            return state

        painter.setPen(QColor("#b9d7e5"))
        status = (
            "WAITING FOR NATIVE 0x0310 COMPRESSED VIDEO"
            if state == "waiting"
            else "NATIVE 0x0310 VIDEO LOST · NO CAMERA FALLBACK"
        )
        painter.setFont(self.font("poppins_bold", 10.0))
        painter.drawText(canvas, Qt.AlignmentFlag.AlignCenter, hero_text(status, chinese_enabled(self.registry)))
        return state

    def consume_capture_frame(self) -> None:
        if (
            self.hero_deploy_video_cut_active()
            or self.hero_deploy_ui.render_visible
            or self.hero_deploy_ui.pending_mode is not None
        ):
            self.consume_hero_native_video_frame()
        if self.hero_deploy_video_cut_active():
            # Authoritative DeployModeStatusSync=1 owns the sole video path.
            # Do not even query webcam/UDP/synthetic normal-feed sources.
            return
        camera_module = self.registry.get("CameraFeed")
        source_mode = self.setting_mode(camera_module, "Source", "Webcam")
        normalized_mode = source_mode.strip().lower()
        if self._normal_video_timeline.mode_changed(normalized_mode):
            self.clear_normal_backend_video_frame()
            self._normal_video_timeline.select_mode(normalized_mode)
        if normalized_mode == "synthetic":
            return
        if normalized_mode != "webcam":
            self.consume_backend_video_frame()
            return
        frame = self.camera_worker.latest_frame()
        if frame is None or not self._normal_video_timeline.consume_capture(frame.sequence):
            return
        try:
            self._vision_scene_luminance = float(frame.bgr[::32, ::32].mean()) / 255.0
            self.state.vision_scene_luminance = self._vision_scene_luminance
        except Exception:
            pass
        image = QImage(frame.rgb.data, frame.width, frame.height, frame.width * 3, QImage.Format.Format_RGB888).copy()
        if self.setting_bool(camera_module, "Mirror", False):
            image = image.mirrored(True, False)
        self.frame_image = image
        self._normal_video_timeline.publish_capture_frame()
        # QPixmap 会由 Qt OpenGL Paint Engine 缓存为纹理；同一摄像头帧在
        # 120/144Hz HUD 重绘期间不再反复上传 QImage。
        self.frame_pixmap = QPixmap.fromImage(image)
        self.update_hud_liquid_source(image)

    def consume_backend_video_frame(self) -> None:
        """Publish a newly decoded backend frame to the normal HUD path."""

        camera_module = self.registry.get("CameraFeed")
        source_mode = self.setting_mode(camera_module, "Source", "Webcam")
        normalized_mode = source_mode.strip().lower()
        if self._normal_video_timeline.mode_changed(normalized_mode):
            self.clear_normal_backend_video_frame()
            self._normal_video_timeline.select_mode(normalized_mode)
        bgr, frame_at, _frame_source = self.backend.latest_video_frame(
            self.NORMAL_BACKEND_VIDEO_STALE_S,
            source_mode,
        )
        if bgr is None or frame_at <= 0.0:
            self.backend.udp.clear_stale_decoded_frame(self.NORMAL_BACKEND_VIDEO_STALE_S)
            self.clear_normal_backend_video_frame()
            return
        if not self._normal_video_timeline.backend_frame_is_new(frame_at):
            return
        try:
            source = bgr
            bgr = source.copy() if source is not None else None
        except Exception:
            bgr = None
        if bgr is None or getattr(bgr, "ndim", 0) != 3 or int(bgr.shape[2]) < 3:
            return
        height, width = int(bgr.shape[0]), int(bgr.shape[1])
        if width <= 0 or height <= 0:
            return
        image = QImage(
            bgr.data,
            width,
            height,
            int(bgr.strides[0]),
            QImage.Format.Format_BGR888,
        ).copy()
        camera_module = self.registry.get("CameraFeed")
        if self.setting_bool(camera_module, "Mirror", False):
            image = image.mirrored(True, False)
        self._normal_video_timeline.admit_backend_frame(frame_at)
        # OfficialEvent stops the local webcam while the judge feed owns the
        # screen; the decoded backend frame is the only live image, so local
        # vision must consume it or detection/ESP silently lose all input.
        if self._vision_source in ("Local", "Auto") and self.local_vision.running:
            self._backend_vision_sequence = int(getattr(self, "_backend_vision_sequence", 0)) + 1
            self.local_vision.submit(self._backend_vision_sequence, width, height, bgr)
        self.frame_image = image
        self._normal_video_timeline.publish_backend_frame()
        self.frame_pixmap = QPixmap.fromImage(image)
        try:
            self._vision_scene_luminance = float(bgr[::32, ::32].mean()) / 255.0
            self.state.vision_scene_luminance = self._vision_scene_luminance
        except Exception:
            pass
        self.update_hud_liquid_source(image)

    def normal_backend_video_fresh(self, now: float | None = None) -> bool:
        now = time.perf_counter() if now is None else float(now)
        source = self.setting_mode(self.registry.get("CameraFeed"), "Source", "Webcam")
        normalized_source = source.strip().lower()
        return bool(
            self.backend_video_source_selected()
            and self._normal_video_timeline.backend_is_fresh(
                normalized_source, now, self.NORMAL_BACKEND_VIDEO_STALE_S,
            )
        )

    def normal_video_frame_ready(self, now: float | None = None) -> bool:
        source = self.setting_mode(self.registry.get("CameraFeed"), "Source", "Webcam")
        normalized_source = source.strip().lower()
        ready = self._normal_video_timeline.readiness_without_backend(normalized_source)
        if ready is not None:
            return ready
        return self.normal_backend_video_fresh(now)

    def clear_normal_backend_video_frame(self) -> bool:
        # Compatibility Painter keeps a CPU glass snapshot separate from the
        # main frame fields. Invalidate it even when the selected source was
        # Synthetic and therefore had no ``_normal_frame_source`` owner.
        self.liquid_glass.discard("hud")
        if not self._normal_video_timeline.clear_frame():
            return False
        self.frame_image = None
        self.frame_pixmap = None
        self._gpu_background_key = None
        self._gpu_blur_source_key = None
        self._gpu_background_changed = True
        return True

    def backend_video_source_selected(self) -> bool:
        source = self.setting_mode(self.registry.get("CameraFeed"), "Source", "Webcam")
        return source.strip().lower() not in {"webcam", "synthetic", ""}

    def draw_backend_video_lost(self, painter: QPainter, width: int, height: int) -> None:
        canvas = QRectF(0.0, 0.0, float(width), float(height))
        painter.fillRect(canvas, QColor("#02070b"))
        painter.setPen(QColor("#b9d7e5"))
        painter.setFont(self.font("poppins_bold", 10.0))
        painter.drawText(
            canvas,
            Qt.AlignmentFlag.AlignCenter,
            hero_text("VIDEO SIGNAL LOST · WAITING FOR FRESH DECODED FRAME", chinese_enabled(self.registry)),
        )

    def video_source_recovery_notice(self) -> VideoRecoveryNotice | None:
        visible = not (
            self.home_visible or self.menu_overlay_active()
            or self.student_ui.visible or self.quick_action_wheel.visible
            or self.match_chat.input_open or self._window_lifecycle.closing
            or self.gui_timeline.progress > 0.01
        )
        return video_recovery_notice(
            self.backend.video_source_snapshot, self.backend.lifecycle_snapshot,
            visible=visible,
            lifecycle_pending=self.backend.lifecycle_pending,
            configuration_pending=self.configuration_change_pending,
            profile_pending=not self.backend.referee_profile_change.completed,
        )

    def draw_video_source_recovery(self, painter: QPainter, width: int, height: int) -> None:
        # This is live interaction geometry, never part of the cached video
        # background. Remove our previous target when cleanup or visibility changes.
        self.hotspots[:] = [target for target in self.hotspots
                            if target.action not in (VIDEO_RETRY_ACTION, VIDEO_WAIT_ACTION)]
        notice = self.video_source_recovery_notice()
        if notice is None or width < 100 or height < 120:
            return
        panel_width = min(560.0, width - 32.0)
        left = (width - panel_width) * 0.5
        top = max(8.0, min(height - 112.0, height * 0.5 + 24.0))
        panel = QRectF(left, top, panel_width, 104.0)
        painter.fillRect(panel, QColor("#13212d"))
        chinese = chinese_enabled(self.registry)
        title = ("视频清理尚未完成" if chinese else "Video cleanup incomplete")
        title += f" · {notice.requested_source}"
        details = (notice.errors[0] if notice.errors else
                   ("等待释放：" if chinese else "Waiting for release: ")
                   + ", ".join(notice.inflight_resources or notice.pending_resources))
        self.draw_text(painter, QRectF(left + 12, top + 7, panel_width - 24, 22),
                       title, self.font("poppins_bold", 9.0), QColor("#f2b86c"))
        self.draw_text(painter, QRectF(left + 12, top + 31, panel_width - 24, 22),
                       details, self.font("poppins_medium", 8.0), QColor("#e2eaf0"))
        button = QRectF(left + 12, top + 65, panel_width - 24, 28)
        enabled = notice.retry_token is not None
        painter.fillRect(button, QColor("#27587b" if enabled else "#24313d"))
        label = (("重试视频清理" if chinese else "Retry video cleanup") if enabled else
                 ("正在等待资源释放…" if chinese else "Waiting for resource cleanup…"))
        self.draw_text(painter, button, label, self.font("poppins_medium", 8.5),
                       QColor("#ffffff" if enabled else "#a2b1bc"), Qt.AlignmentFlag.AlignCenter)
        if enabled:
            self.add_hotspot(button, VIDEO_RETRY_ACTION, notice.retry_token, button=1,
                             tooltip="; ".join(notice.errors) or ", ".join(notice.pending_resources))
        else:
            # Disabled describes the action, not pointer transparency. A click
            # on the visible waiting button must not become combat input.
            self.add_hotspot(button, VIDEO_WAIT_ACTION, button=1, tooltip=details)

    def retry_video_source_cleanup(self, token: VideoRetryToken) -> bool:
        notice = self.video_source_recovery_notice()
        chinese = chinese_enabled(self.registry)
        if notice is None or notice.retry_token is None or token != notice.retry_token:
            message = ("视频状态已变化或正在关闭，请等待后重试。" if chinese else
                       "Video state changed or shutdown is pending. Wait before retrying.")
            self.toast(message, kind="warning")
            return False
        try:
            self.backend.retry_active_video_source(expected_generation=token.source_generation)
        except Exception as exc:
            _LOG.error("Explicit video cleanup retry failed", exc_info=True)
            prefix = "视频清理重试失败" if chinese else "Video cleanup retry failed"
            self.toast(f"{prefix}: {type(exc).__name__}: {exc}", kind="warning")
            self.frame_scheduler.force()
            return False
        self.clear_normal_backend_video_frame()
        self.frame_scheduler.force()
        self.toast("已请求重试视频清理；等待资源释放后恢复。" if chinese else
                   "Video cleanup retry requested; waiting for resource release.")
        return True

    def active_tongji_result(self) -> VisionResult | None:
        bridge_module = self.registry.get("TongjiVisionBridge")
        timeout = self.get_setting_number(bridge_module, "UDP Timeout", 250.0)
        local_snapshot: VisionSnapshot | None = None
        local_result = self.local_vision.latest(timeout)
        if local_result is not None:
            timestamp_ns, sequence, frame_width, frame_height, armors, fps, latency_ms, loss_evidence = local_result
            self.sync_local_autoaim_fov(frame_width, frame_height)
            calibration_valid = self.calibration.is_valid_for(frame_width, frame_height)
            calibration_matrix = self.calibration.profile.camera_matrix if calibration_valid and self.calibration.profile else None
            local_snapshot = snapshot_from_local_result(
                timestamp_ns,
                sequence,
                frame_width,
                frame_height,
                armors,
                fps,
                latency_ms,
                self.local_vision.active_device,
                loss_evidence,
                calibrated=calibration_valid,
                camera_matrix=calibration_matrix,
            )
        else:
            self.sync_local_autoaim_fov()
        tongji_snapshot: VisionSnapshot | None = None
        packet = self.tongji_receiver.latest(timeout)
        if packet is not None:
            tongji_snapshot = snapshot_from_tongji_packet(packet, inference_device=self.setting_mode(bridge_module, "External Device", "AUTO"))
            self.gimbal_command_bridge.cache(tongji_snapshot.gimbal_command)
        snapshot = self.composite_vision.choose(self._vision_source, local_snapshot, tongji_snapshot)
        self._vision_snapshot = snapshot
        if snapshot is not None:
            self._last_vision_result = snapshot.result
            return snapshot.result
        return None


    def alliance_side(self) -> str:
        profile = self.registry.get("AllianceProfile")
        side = self.setting_mode(profile, "Side", "") if profile else ""
        if side not in ("Red", "Blue"):
            side = self.setting_mode(self.registry.get("MatchMode"), "Alliance", "Red")
        return side if side in ("Red", "Blue") else "Red"

    def consume_official_messages(self) -> None:
        for topic, payload, received_at in self.mqtt.drain_received_messages():
            events, updates = self.official_feed.consume(
                topic, payload, alliance=self.alliance_side(),
                local_robot_id=self.state.robot_id,
                referee_profile=self.state.referee_profile,
                received_at=received_at,
            )
            # Decoded backend application owns scalar telemetry. A raw packet
            # arriving between tick stages must not publish it ahead of the
            # canonical adapter (or bypass an apply failure/transition block).
            # Receipt freshness is not a transactional state-application claim.
            if "official_feed_at" in updates:
                self._official_state_updates["official_feed_at"] = updates["official_feed_at"]
            for event in events:
                self.accept_combat_event(event)

    def apply_official_state(self) -> None:
        if not self._official_state_updates:
            self.state.official_events_online = self.official_feed.is_fresh()
            return
        updates = self._official_state_updates
        self._official_state_updates = {}
        economy_before = BackendManager._economy_signature(self.state)
        for key, value in updates.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
        if BackendManager._economy_signature(self.state) != economy_before:
            self.state.economy_status_revision += 1
            self.state.economy_command_status = ""
        self.state.official_events_online = self.official_feed.is_fresh()
        self.state.official_feed_at = float(updates.get("official_feed_at", self.state.official_feed_at))

    def process_vision_kill_events(self) -> None:
        effect = self.registry.get("EliminationFX")
        result = self._vision_snapshot.result if self._vision_snapshot else None
        if effect is None or result is None:
            self.state.vision_kill_status = "NO_DETECTION"
            return
        evidence = self.setting_mode(effect, "Evidence Mode", "Auto")
        # 校内模拟和实验室常有 MQTT mock；Auto 只在官方赛事场景交由裁判系统优先裁决。
        official_online = (
            self.scenario_manager.current.key == "OfficialEvent"
            and self.official_feed.is_kill_data_fresh()
        )
        if evidence == "Referee Only" or (evidence == "Auto" and official_online):
            self.state.vision_kill_status = "OFFICIAL_SUPPRESSED" if evidence == "Auto" else "REFEREE_ONLY"
            return
        events = self.vision_kill_detector.process(
            result.detections,
            alliance=self.alliance_side(),
            frame_luminance=self._vision_scene_luminance,
            frame_token=(result.timestamp_ns, result.sequence),
            frame_size=(result.source_width, result.source_height),
            sensitivity=self.setting_mode(self.registry.get("TongjiVisionBridge"), "Vision Kill Sensitivity", "Lab Responsive"),
            loss_evidence=result.loss_evidence,
        )
        self.state.vision_kill_status = self.vision_kill_detector.last_status
        for event in events:
            self.accept_combat_event(event)

    def accept_combat_event(self, event: CombatEvent) -> None:
        effect = self.registry.get("EliminationFX")
        if effect is None:
            return
        evidence = self.setting_mode(effect, "Evidence Mode", "Auto")
        if event.source == "vision" and evidence == "Referee Only":
            return
        if event.source not in ("vision", "preview") and evidence == "Vision Only":
            return
        if event.kind == "elimination":
            if event.source == "vision":
                self.state.visual_kill_count += 1
            else:
                self.state.referee_kill_count += 1
            self.state.kill_streak += 1
        elif event.kind == "death":
            self.state.kill_streak = 0
        self.state.push_event("VISION CONFIRMED" if event.source == "vision" else "Referee elimination")
        self.dynamic_island_last_event = "VISION CONFIRMED" if event.source == "vision" else "Official combat event"
        self.dynamic_island_event_started = time.perf_counter()
        match_chat = self.registry.get("MatchChat")
        if (
            match_chat is not None
            and match_chat.enabled
            and self.setting_bool(match_chat, "Referee Notices", True)
        ):
            self.chat_event_adapter.combat(event)
            self.student_notice_adapter.combat(event, self.get_setting_number(effect, "Combo Window", 5.0))
        if effect.enabled:
            self.kill_fx.trigger(
                event,
                combo_window=self.get_setting_number(effect, "Combo Window", 5.0),
                sound_enabled=self.setting_bool(effect, "Sound Enabled", True),
                volume=self.get_setting_number(effect, "Sound Volume", 55.0),
                preset=self.setting_mode(effect, "Preset", "Default Scrolling"),
                visibility=self.setting_mode(effect, "Element Visibility", "Preset"),
                element_toggles=self.elimination_element_toggles(effect),
            )

    def elimination_element_toggles(self, effect: Module) -> dict[str, bool]:
        """ClickGUI 二级界面的逐元素开关 -> kill_fx 元素键。"""
        return {
            "kill_feed": self.setting_bool(effect, "Kill Feed", True),
            "score": self.setting_bool(effect, "Score Popup", True),
            "bonus": self.setting_bool(effect, "Bonus Tags", True),
            # 加分标签行的子项。`kill_fx.trigger()` 早就在读这个键
            # （`element_toggles.get("locked_target_bonus", True)`），只是这个
            # 字典从来没给它供值，于是“锁定目标”标签与提示音无法关闭。
            "locked_target_bonus": self.setting_bool(effect, "Lock Target Bonus", True),
            "combo_subtitle": self.setting_bool(effect, "Combo Subtitle", True),
            "scrolling": self.setting_bool(effect, "Scrolling Icons", True),
            "scrolling_bf5": self.setting_bool(effect, "Scrolling Icons", True),
            "scrolling_bf6": self.setting_bool(effect, "Scrolling Icons", True),
            "combo": self.setting_bool(effect, "Combo Icons", True),
            "card": self.setting_bool(effect, "Card Icons", True),
            "card_bar": self.setting_bool(effect, "Card Icons", True),
            "battlefield1": self.setting_bool(effect, "Battlefield Icons", True),
            "valorant": self.setting_bool(effect, "Valorant Icons", True),
            "honor": self.setting_bool(effect, "Honor Badges", True),
            "hit_info": self.setting_bool(effect, "Hit Info", True),
            "match_progress": self.setting_bool(effect, "Match Progress", True),
        }

    def update_elimination_hit_info(self) -> None:
        """把 RobotInjuryStat 命中边沿转成 hit_info 弹字/音效。"""
        effect = self.registry.get("EliminationFX")
        if effect is None or not effect.enabled:
            return
        revision = int(self.state.projectile_hit_revision)
        if revision == self.kill_fx._last_hit_revision:
            return
        self.kill_fx._last_hit_revision = revision
        if not self.setting_bool(effect, "Hit Info", True):
            return
        self.kill_fx.notify_hit(
            int(self.state.last_projectile_damage_delta),
            str(self.state.last_projectile_kind),
            preset=self.setting_mode(effect, "Preset", "Default Scrolling"),
            visibility=self.setting_mode(effect, "Element Visibility", "Preset"),
            sound_enabled=self.setting_bool(effect, "Sound Enabled", True),
            volume=self.get_setting_number(effect, "Sound Volume", 55.0),
            element_toggles=self.elimination_element_toggles(effect),
        )

    def draw_elimination_fx(self, p: QPainter, width: int, height: int) -> None:
        effect = self.registry.get("EliminationFX")
        if effect is None or not effect.enabled:
            return
        team_theme = self.setting_mode(effect, "Team Theme", "Alliance")
        if team_theme == "Alliance":
            team_theme = "CT Blue" if self.alliance_side() == "Blue" else "T Gold"
        toggles = self.elimination_element_toggles(effect)
        top_bar = getattr(self, "top_match_bar", None)
        if top_bar is not None and top_bar.owns_progress():
            toggles["match_progress"] = False
        self.kill_fx.draw(
            p, width, height,
            preset=self.setting_mode(effect, "Preset", "Default Scrolling"),
            visibility=self.setting_mode(effect, "Element Visibility", "Preset"),
            element_toggles=toggles,
            state=self.state,
            island_anchor_bottom=self.dynamic_island_bottom_anchor(width, height),
            team=team_theme,
            scale=self.get_setting_number(effect, "Scale", 1.0),
            anchor_policy=self.setting_mode(effect, "Anchor Policy", "Combat Focus"),
            x_offset=self.get_setting_number(effect, "X Offset", 0.0),
            y_offset=self.get_setting_number(effect, "Y Offset", 0.0),
            responsive_scale=self.setting_mode(effect, "Responsive Scale", "Auto") == "Auto",
            device_pixel_ratio=self.devicePixelRatioF(),
        )

    def sync_tongji_vision_state(self) -> None:
        result = self.active_tongji_result()
        self.state.vision_backend = self._vision_snapshot.source if self._vision_snapshot else self._vision_source
        self.state.vision_connected = bool(self._vision_snapshot) if self._vision_source != "Mock" else False
        if result is None:
            self.vision_render_interpolator.clear()
            self._vision_render_result = None
            self.state.vision_tracking_state = "mock" if self._vision_source == "Mock" else "disconnected"
            self.state.vision_calibrated = False
            self.state.vision_detection_count = 0
            self.state.vision_prediction_count = 0
            self.state.target_locked = False
            self.state.lock_state = False
            return
        display_result = self.vision_result_for_display(result)
        if display_result is not None:
            source = self._vision_snapshot.source if self._vision_snapshot else self._vision_source
            self.vision_render_interpolator.push(display_result, source=source)
        calibrated = self.vision_result_calibrated(result)
        self.state.vision_calibrated = calibrated
        # Lock / aim telemetry follows FOV-filtered display result; ESP counts stay full.
        lock_result = display_result if display_result is not None else result
        self.state.vision_tracking_state = lock_result.tracking_state
        self.state.vision_inference_device = result.inference_device
        self.state.vision_inference_fps = result.inference_fps
        self.state.vision_latency_ms = result.latency_ms
        self.state.vision_sequence = result.sequence
        self.state.vision_detection_count = len(result.detections)
        self.state.vision_prediction_count = len(result.predicted_armors)
        self.state.vision_aim_yaw_deg = result.aim_yaw_deg
        self.state.vision_aim_pitch_deg = result.aim_pitch_deg
        target = lock_result.aim_armor
        if target is not None:
            self.state.target_id = target.armor_id if target.armor_id >= 0 else self.state.target_id
            self.state.target_distance_m = target.distance_m or self.state.target_distance_m
            terminal = (
                self.vision_kill_detector.is_extinguished(target)
                or self.vision_kill_detector.is_suppressed(target)
            )
            # Only FOV-admitted aim plates may report LOCK.
            self.state.target_locked = bool(calibrated and lock_result.is_tracking and not terminal)
            self.state.lock_state = self.state.target_locked
        else:
            self.state.target_locked = False
            self.state.lock_state = False
        self.publish_vision_control_if_armed(result)

    def publish_vision_control_if_armed(self, result: VisionResult) -> None:
        """Fail-closed optional team CustomControl gimbal path; never fires."""

        module = self.registry.get("ArmorAutoAim")
        live = bool(
            module is not None
            and module.enabled
            and not self.setting_bool(module, "Mock Output", True)
            and self.state.target_locked
            and self.state.vision_calibrated
        )
        if not live:
            return
        # 单一裁决：队内 0x5A 已拿到云台控制权时，PRM1 不得再发。
        # 否则机器人会同时收到两套云台角（MQTT 一套、USB 一套），
        # 互相覆盖表现为云台抖动/乱转；而 0x5A 侧的抢环锁出看不见这条。
        owner = self.backend.gimbal_egress_owner(self.state, self.registry)
        if owner == "polar":
            self.state.backend_stats["vision_control"] = {
                "published": False,
                "reason": "gimbal owned by team 0x5A link",
                "sequence": result.sequence,
                "fire": False,
            }
            return
        ok, reason = self.backend.publish_vision_control(
            sequence=result.sequence,
            yaw_deg=result.aim_yaw_deg,
            pitch_deg=result.aim_pitch_deg,
            target_id=self.state.target_id,
            tracking=True,
        )
        self.state.backend_stats["vision_control"] = {
            "published": bool(ok),
            "reason": str(reason),
            "sequence": result.sequence,
            "fire": False,
        }

    def tongji_target(self, width: int, height: int, result: VisionResult) -> VisionTarget | None:
        observation = result.aim_armor or next((armor for armor in result.detections if len(armor.points) == 4), None)
        if observation is None or len(observation.points) < 2:
            return None
        x_scale = width / max(1.0, float(result.source_width))
        y_scale = height / max(1.0, float(result.source_height))
        camera_module = self.registry.get("CameraFeed")
        mirror = self.setting_bool(camera_module, "Mirror", False)
        points = [(width - x * x_scale if mirror else x * x_scale, y * y_scale) for x, y in observation.points]
        xs, ys = zip(*points)
        return VisionTarget(
            x=min(xs),
            y=min(ys),
            w=max(4.0, max(xs) - min(xs)),
            h=max(4.0, max(ys) - min(ys)),
            confidence=observation.confidence,
            label=observation.label,
            kind="armor",
        )

    def draw_synthetic_video(self, p: QPainter, width: int, height: int) -> None:
        if self.high_res_optimize(width, height):
            self.draw_synthetic_video_cached(p, width, height)
            return
        self.draw_synthetic_video_scene(p, width, height, lite=False)
        # 1080p 下主画面直接绘制，为 HUD 玻璃额外保留一张轻量图传快照。
        now = time.perf_counter()
        cache_w = min(360, max(220, width // 4))
        cache_h = max(124, int(cache_w * height / max(1, width)))
        key = (cache_w, cache_h)
        if self.synthetic_video_cache is None or self.synthetic_video_cache_key != key or now - self.synthetic_video_cache_time > 0.10:
            image = QImage(cache_w, cache_h, QImage.Format.Format_ARGB32)
            image.fill(color("#101721"))
            cache_p = QPainter(image)
            cache_p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self.draw_synthetic_video_scene(cache_p, cache_w, cache_h, lite=True)
            cache_p.end()
            self.synthetic_video_cache = image
            self.synthetic_video_cache_key = key
            self.synthetic_video_cache_time = now
        if self.synthetic_video_cache is not None:
            self.update_hud_liquid_source(self.synthetic_video_cache)

    def draw_synthetic_video_cached(self, p: QPainter, width: int, height: int) -> None:
        now = time.perf_counter()
        cache_w = min(1280, max(640, width // 2))
        cache_h = max(360, int(cache_w * height / max(1, width)))
        key = (cache_w, cache_h)
        if self.synthetic_video_cache is None or self.synthetic_video_cache_key != key or now - self.synthetic_video_cache_time > 0.08:
            image = QImage(cache_w, cache_h, QImage.Format.Format_ARGB32)
            image.fill(color("#101721"))
            cache_p = QPainter(image)
            cache_p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            cache_p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            self.draw_synthetic_video_scene(cache_p, cache_w, cache_h, lite=True)
            cache_p.end()
            self.synthetic_video_cache = image
            self.synthetic_video_cache_key = key
            self.synthetic_video_cache_time = now
        if self.synthetic_video_cache is not None:
            self.update_hud_liquid_source(self.synthetic_video_cache)
            p.drawImage(QRectF(0, 0, width, height), self.synthetic_video_cache)

    def update_hud_liquid_source(self, image: QImage) -> None:
        if not self.gpu_compositor.available:
            self.liquid_glass.update("hud", image, QRectF(0, 0, self.width(), self.height()))
        return
        # Unified GPU 直接从背景 FBO 采样。CPU 快照只服务于无 OpenGL 的
        # Compatibility Painter，避免摄像头每个源帧在 UI 线程额外缩放两次。
        if self.gpu_compositor.available:
            return
        self.liquid_glass.update("hud", image, QRectF(0, 0, self.width(), self.height()))

    def draw_synthetic_video_scene(self, p: QPainter, width: int, height: int, lite: bool) -> None:
        sky = QLinearGradient(0, 0, 0, height)
        sky.setColorAt(0.0, color("#171d28"))
        sky.setColorAt(0.56, color("#121923"))
        sky.setColorAt(1.0, color("#0a1015"))
        p.fillRect(QRectF(0, 0, width, height), sky)

        horizon = height * 0.57
        p.fillRect(QRectF(0, horizon, width, height - horizon), color("#0c141a"))
        pen = QPen(color("#335063", 0.66), 1.0)
        p.setPen(pen)
        grid_x = 108 if lite else 78
        grid_y = 46 if lite else 34
        for x in range(-width, width * 2, grid_x):
            p.drawLine(QPointF(x, horizon), QPointF(x - width * 0.36, height))
        for y in range(int(horizon), height, grid_y):
            p.drawLine(QPointF(0, y), QPointF(width, y))

        now = time.perf_counter()
        p.setPen(Qt.PenStyle.NoPen)
        particle_count = 24 if lite else 38
        for i in range(particle_count):
            phase = now * 0.38 + i * 1.73
            px = (i * 127.0 + math.sin(phase) * 34.0) % max(1, width)
            py = horizon + ((i * 41.0 + now * 18.0) % max(1.0, height - horizon))
            near = (py - horizon) / max(1.0, height - horizon)
            radius = 1.0 + near * 2.2
            p.setBrush(color("#56fff0", 0.18 + near * 0.18))
            p.drawEllipse(QPointF(px, py), radius, radius)

        p.setPen(QPen(color("#56fff0", 0.18), 1.2))
        p.drawLine(QPointF(0, horizon), QPointF(width, horizon))

        cx = width * 0.5 + math.sin(now * 0.82) * width * 0.17
        base = QRectF(cx - 68, horizon - 112, 136, 94)
        glow_rect(p, base, 6, 0.26 if lite else 0.48)
        rounded_rect(p, base, 5, color("#252d38"), color("#7c8d9f", 0.68), 1.4)
        p.setPen(QPen(color("#56fff0", 0.86), 2))
        p.drawLine(QPointF(base.left() + 16, base.top() + 22), QPointF(base.right() - 16, base.top() + 22))
        self.draw_text(
            p,
            QRectF(0, height - 32, width, 18),
            "合成机器人图传 - 摄像头不可用",
            self.font("poppins_regular", 8.5),
            color("#9aa6ac"),
            Qt.AlignmentFlag.AlignCenter,
        )



# ---------------------------------------------------------------------------
# API 冻结面 qualname 固定（见模块 docstring「冻结面纪律」；先例：
# ui/polar/text_metrics.py 与 renderers/*、mixins/persistence_mixin.py 底部同一手法）。
# ---------------------------------------------------------------------------

_QUALNAME_PINNED_MEMBERS = (
    VideoBackendMixin.append_minecraft_drop_overlay,
    VideoBackendMixin.assign_minecraft_function_slot,
    VideoBackendMixin.activate_minecraft_function_slot,
    VideoBackendMixin.hero_range_profile_selected,
    VideoBackendMixin.hero_deploy_video_cut_active,
    VideoBackendMixin.hero_deploy_uses_mock_transport,
    VideoBackendMixin.open_hero_deploy_interface,
    VideoBackendMixin.close_hero_deploy_interface,
    VideoBackendMixin.send_hero_deploy_request,
    VideoBackendMixin.update_hero_deploy_state,
    VideoBackendMixin.update_auto_spin_guard,
    VideoBackendMixin.draw_hero_deploy_interface,
    VideoBackendMixin.hero_lob_preview,
    VideoBackendMixin.update_hero_lob_guard,
    VideoBackendMixin.ensure_camera_started,
    VideoBackendMixin.draw_video,
    VideoBackendMixin.refresh_vision_runtime,
    VideoBackendMixin.on_captured_camera_frame,
    VideoBackendMixin.consume_hero_native_video_frame,
    VideoBackendMixin.hero_native_frame_state,
    VideoBackendMixin.draw_hero_native_background,
    VideoBackendMixin.consume_capture_frame,
    VideoBackendMixin.consume_backend_video_frame,
    VideoBackendMixin.normal_backend_video_fresh,
    VideoBackendMixin.normal_video_frame_ready,
    VideoBackendMixin.clear_normal_backend_video_frame,
    VideoBackendMixin.backend_video_source_selected,
    VideoBackendMixin.draw_backend_video_lost,
    VideoBackendMixin.active_tongji_result,
    VideoBackendMixin.alliance_side,
    VideoBackendMixin.sync_tongji_vision_state,
    VideoBackendMixin.publish_vision_control_if_armed,
    VideoBackendMixin.tongji_target,
    VideoBackendMixin.draw_synthetic_video,
    VideoBackendMixin.draw_synthetic_video_cached,
    VideoBackendMixin.update_hud_liquid_source,
    VideoBackendMixin.draw_synthetic_video_scene,
    VideoBackendMixin.consume_official_messages,
    VideoBackendMixin.apply_official_state,
    VideoBackendMixin.process_vision_kill_events,
    VideoBackendMixin.accept_combat_event,
    VideoBackendMixin.elimination_element_toggles,
    VideoBackendMixin.update_elimination_hit_info,
    VideoBackendMixin.draw_elimination_fx,
)

for _fn in _QUALNAME_PINNED_MEMBERS:
    _fn.__qualname__ = f"PolarRMWindow.{_fn.__name__}"
del _fn, _QUALNAME_PINNED_MEMBERS
