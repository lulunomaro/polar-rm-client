"""Frozen cached-frame reference before the explicit layer boundary.

Source: 71ebb0f570441c12eb0d15b0a992c9cc3e805aeb
Path: rm_client/ui/polar_app.py
The method body is an exact source slice; do not refresh it to accept drift.
Only external raster/GL resources are substituted by the test harness.
"""
from __future__ import annotations
import time
from rm_client.model import PRIMARY_GUI_MODE
from rm_client.ui.clickgui_gpu import ClickGuiDrawList
from rm_client.ui.gpu_frame_compositor import GlassOwner
from rm_client.ui.polar.clickgui_frame import ClickGuiFrame, build_clickgui_frame
from rm_client.ui.polar.compositor_plan import (
    BackdropTreatment, FinalFrameCompositor, FrameCompositionPlan,
    OverlayScene, motion_layer_needs_refresh,
)
from rm_client.ui.polar_app import EconomyScenePhase, chinese_enabled


class CachedFrameBaseline:
    def _paint_gpu_frame_cached(self) -> bool:
        """在稳定启动页复用静态 GPU 层，hover 只提交一张小动态图层。"""
        if not self.gpu_compositor.available:
            return False
        context = self.context()
        if context is None:
            return False
        width, height = self.width(), self.height()
        ratio = self.devicePixelRatioF()
        storage_generation = self.gpu_compositor.fbo_generation
        if width <= 0 or height <= 0 or not self.gpu_compositor.ensure_size(width, height, ratio):
            return False
        if self.gpu_compositor.fbo_generation != storage_generation:
            # Qt handles DPR-only changes without resizeEvent. Storage or its
            # DPR interpretation changed, including a priority overlay's
            # frozen motion layer. Forget readiness before any cache decision.
            self.invalidate_transient_overlay_scene()
        functions = context.extraFunctions()
        presented = False
        committed = False
        recovered = False
        try:
            restore_generation = self._economy_restore_pending_generation
            restore_attempt = bool(
                restore_generation is not None
                and restore_generation == self._economy_generation
                and self.economy_phase == EconomyScenePhase.CLOSING
            )
            if restore_attempt:
                if not self.gpu_compositor.clear_scene_layers(
                    functions,
                    ("overlay", "dynamic", "target"),
                ):
                    return False
                # Atomically invalidate every metadata key in the same GL
                # frame as the physical FBO clear.  The normal branches below
                # must now repopulate static and motion layers before present.
                self.invalidate_transient_overlay_scene()
            startup_scene = self.home_visible and not self.wallpaper_picker_visible and self.clickgui_gpu.available
            static_key = self.gpu_static_ui_key(width, height, ratio)
            rebuild_hud_static = not startup_scene and static_key != self._gpu_static_ui_key
            if rebuild_hud_static and self._should_defer_hud_static_rebuild(static_key):
                # A module toggle repaints the whole static HUD snapshot and
                # the treated backdrop on the exact frame the 150 ms ActiveMods
                # fade starts, which read as a hitch. The stale snapshot is
                # invisible under the live motion layer, so wait for the row
                # animation to settle (bounded) before rebuilding.
                rebuild_hud_static = False
            self.gpu_compositor.begin_frame(rebuild_static_ui=rebuild_hud_static)
            if not self.gpu_compositor.clear_target_layer(functions):
                return False
            self._gpu_frame_recording = True
            if not self.paint_gpu_background(functions, width, height, ratio):
                return False
            scene = OverlayScene.resolve(
                startup_scene=startup_scene,
                economy_visible=self.economy_visible,
                economy_progress=self.economy_island.progress,
                clickgui_visible=self.gui_visible,
                clickgui_progress=self.gui_timeline.progress,
                student_visible=self.student_ui.visible,
                escape_visible=self.escape_menu_visible,
            )
            economy_active = scene.economy_active
            clickgui_active = scene.clickgui_active
            modal_backdrop_active = scene.modal_backdrop_active
            jello_blur_radius = (
                self.gui_timeline.blur_radius()
                if not economy_active and clickgui_active and self.active_clickgui_mode() == PRIMARY_GUI_MODE
                else None
            )
            treatment = BackdropTreatment.resolve(
                scene, economy_progress=self.economy_island.progress,
                clickgui_progress=self.gui_timeline.progress,
                jello_blur_radius=jello_blur_radius,
                student_panel=self.student_ui.panel,
                student_progress=self.student_ui.progress,
            )
            treatment_changed = treatment.apply(self.gpu_compositor)
            control_changed = self.paint_gpu_control_backdrop(
                functions,
                width,
                height,
                ratio,
                startup_scene=startup_scene,
                economy_active=economy_active,
            )
            if self.gpu_compositor.frame_rejected:
                return False
            # Modal vision must never punch crisply through the treated
            # backdrop. Economy additionally clears the complete HUD layer;
            # Jello keeps the cached HUD under Sigma's blur.
            if modal_backdrop_active:
                if not self.gpu_compositor.clear_vision_layer(functions):
                    return False
                native_vision_owner = False
            else:
                native_vision_owner = self.paint_gpu_vision_layer(functions, width, height, ratio)
                if self.gpu_compositor.frame_rejected:
                    return False

            if startup_scene:
                transition_active = (
                    self.startup_transition_from is not None
                    and self.startup_transition_to is not None
                    and time.perf_counter() - self.startup_transition_started < self.anim_duration(0.42)
                )
                # The stable page can be cached as soon as a transition ends.
                # Waiting for a timer here left a transparent dynamic layer on
                # 2K displays and exposed the previous page for one more frame.
                cache_ready = not transition_active
                if cache_ready:
                    launcher_key = (*static_key, self.scenario_manager.current.key, self.match_mode_manager.current.key, chinese_enabled(self.registry))
                    if launcher_key != self._startup_static_page_key:
                        self.hotspots.clear()
                        self.gpu_compositor.clear_static_glass()
                        self.gpu_compositor.begin_static_glass()
                        commands = self.build_gpu_startup_launcher(width, height, page=self.startup_step, settled=True)
                        self.gpu_compositor.end_static_glass()
                        self.clickgui_gpu.prepare_frame(commands, ratio)
                        device = self.gpu_compositor.paint_device("overlay")
                        if device is None:
                            return False
                        self.gpu_compositor.clear_current(functions, transparent=True)
                        if not self.clickgui_gpu.render(
                            functions,
                            commands,
                            width,
                            height,
                            ratio,
                            atlas_prepared=True,
                        ):
                            return False
                        if self.gpu_compositor.release_layer("overlay") is False:
                            return False
                        self._startup_static_page_key = launcher_key
                    if not self.paint_gpu_startup_interaction_layer(functions, width, height, ratio, cached_static=True):
                        return False
                else:
                    self.gpu_compositor.clear_static_glass()
                    self._startup_static_page_key = None
                    device = self.gpu_compositor.paint_device("overlay")
                    if device is None:
                        return False
                    self.gpu_compositor.clear_current(functions, transparent=True)
                    if self.gpu_compositor.release_layer("overlay") is False:
                        return False
                    if not self.paint_gpu_startup_interaction_layer(functions, width, height, ratio):
                        return False
                self._gpu_static_ui_key = static_key
            else:
                # The liquid-glass specification makes Economy an exclusive
                # scene. Jello and retained GUIs use the normal HUD snapshot
                # path whenever Economy is inactive.
                if economy_active:
                    # Economy is an exclusive scene: camera + economy control
                    # backdrop only.  Clearing the cached host HUD here fixes
                    # the map/ModuleList ghosting that contradicted the sole
                    # liquid-glass specification, without changing any shared
                    # HUD or glass renderer.
                    device = self.gpu_compositor.paint_device("overlay")
                    if device is None:
                        return False
                    device.setDevicePixelRatio(ratio)
                    self.gpu_compositor.clear_current(functions, transparent=True)
                    if self.gpu_compositor.release_layer("overlay") is False:
                        return False
                    self._gpu_static_ui_key = static_key
                elif rebuild_hud_static:
                    def draw_static(session) -> bool:
                        self.gpu_compositor.push_glass_owner(GlassOwner.HUD)
                        session.defer_cleanup("glass_owner", self.gpu_compositor.pop_glass_owner)
                        self.gpu_compositor.begin_static_glass()
                        session.defer_cleanup("static_glass", self.gpu_compositor.end_static_glass)
                        painter = session.begin_painter()
                        self.paint_overlay(
                            None,
                            startup_background_ready=True,
                            hud_background_ready=True,
                            painter=painter,
                            logical_size=(width, height),
                            include_interactive=False,
                            split_motion_layer=not modal_backdrop_active,
                            capture_module_list=modal_backdrop_active,
                        )
                        return True

                    if not self.gpu_compositor.paint_layer(functions, "overlay", ratio, draw_static):
                        return False
                    self._gpu_static_ui_key = static_key

                if economy_active and self.clickgui_gpu.available:
                    # Economy panel is the complete dynamic scene.  Do not draw HUD
                    # motion here: its only backdrop is the live camera texture plus
                    # economy-owned control rails.
                    self.gpu_compositor.push_glass_owner(GlassOwner.ECONOMY)
                    try:
                        commands = self.build_gpu_economy_workspace(width, height)
                    finally:
                        self.gpu_compositor.pop_glass_owner()
                    self.clickgui_gpu.prepare_frame(commands, ratio)

                    device = self.gpu_compositor.paint_device("dynamic")
                    if device is None:
                        return False
                    device.setDevicePixelRatio(ratio)
                    self.gpu_compositor.clear_current(functions, transparent=True)
                    if not self.clickgui_gpu.render(
                        functions, commands, width, height, ratio, atlas_prepared=True,
                    ):
                        if self.gpu_compositor.release_layer("dynamic") is False:
                            return False
                        return False
                    if self.gpu_compositor.release_layer("dynamic") is False:
                        return False
                    if not self.gpu_compositor.clear_target_layer(functions):
                        return False
                else:
                    prepared_clickgui_commands: ClickGuiDrawList | None = None
                    prepared_clickgui_frame: ClickGuiFrame | None = None
                    prepared_clickgui_atlas = False
                    click_gui = self.registry.get("ClickGui")
                    gui_alpha = self.gui_timeline.progress
                    if (
                        (self.gui_visible or gui_alpha > 0.01)
                        and self.setting_mode(click_gui, "Render Backend", "Unified GPU") == "Unified GPU"
                        and self.clickgui_gpu.available
                    ):
                        self.gpu_compositor.push_glass_owner(GlassOwner.CLICKGUI)
                        try:
                            prepared_clickgui_frame = build_clickgui_frame(self, width, height, gui_alpha)
                            prepared_clickgui_commands = prepared_clickgui_frame.commands
                        finally:
                            self.gpu_compositor.pop_glass_owner()
                        # Geometry travels with its draw list; preparation does not
                        # publish pointer targets before the modal layer is painted.
                        self.clickgui_gpu.prepare_frame(prepared_clickgui_commands, ratio)
                        prepared_clickgui_atlas = True

                    priority_overlay_active = bool(
                        self.quick_action_wheel.visible
                        or self.match_chat.input_open
                        or self.student_ui.visible
                        or self.escape_menu_visible
                    )
                    now = time.perf_counter()
                    refresh_motion_layer = motion_layer_needs_refresh(
                        radial_visible=self.quick_action_wheel.visible,
                        chat_open=self.match_chat.input_open,
                        student_visible=self.student_ui.visible,
                        escape_visible=self.escape_menu_visible,
                        ready=self._gpu_motion_layer_ready,
                        now=now, updated_at=self._gpu_motion_layer_updated_at,
                    )

                    if refresh_motion_layer:
                        dynamic_started = time.perf_counter()
                        def draw_motion(session) -> bool:
                            self.begin_gpu_hud_command_collection()
                            self.begin_gpu_island_icon_collection()
                            self.begin_gpu_quick_action_wheel_collection()
                            painter = session.begin_painter()
                            if not self.home_visible and not clickgui_active:
                                # Priority overlays own target_fbo and must not be
                                # baked into this reusable base frame.
                                self.paint_hud_motion_overlay(
                                    painter,
                                    width,
                                    height,
                                    include_vision=not native_vision_owner,
                                    include_target_hud=(
                                        self.menu_overlay_active()
                                        and not priority_overlay_active
                                    ),
                                    include_chat=not self.match_chat.input_open,
                                    include_radial=not self.quick_action_wheel.visible,
                                )
                            if not self.home_visible:
                                self.paint_clickgui_overlay(
                                    painter,
                                    width,
                                    height,
                                    gpu_commands=prepared_clickgui_commands,
                                    gpu_frame=prepared_clickgui_frame,
                                    gpu_atlas_prepared=prepared_clickgui_atlas,
                                )
                            if not session.end_painter():
                                return False
                            self.flush_gpu_hud_commands(functions, width, height, ratio)
                            self.flush_gpu_island_icons(functions, width, height, ratio)
                            self.flush_gpu_quick_action_wheel(functions, width, height, ratio)
                            return True

                        if not self.gpu_compositor.paint_layer(functions, "dynamic", ratio, draw_motion):
                            return False
                        self._gpu_motion_layer_ready = True
                        self._gpu_motion_layer_updated_at = now
                        self.gpu_compositor.last_dynamic_ms = (
                            time.perf_counter() - dynamic_started
                        ) * 1000.0

                    if priority_overlay_active:
                        if not self.paint_priority_interaction_layer(
                            functions,
                            width,
                            height,
                            ratio,
                        ):
                            return False
                    elif modal_backdrop_active:
                        if not self.gpu_compositor.clear_target_layer(functions):
                            return False
                    elif not self.paint_gpu_target_hud_layer(
                        functions,
                        width,
                        height,
                        ratio,
                    ):
                        return False

            if self.gpu_compositor.frame_rejected:
                return False
            composition = FrameCompositionPlan(
                background_changed=self._gpu_background_changed,
                control_changed=control_changed,
                treatment_changed=treatment_changed,
                static_ui_rebuilt=rebuild_hud_static,
                overlay_owners=scene.glass_owners(
                    radial_visible=self.quick_action_wheel.visible,
                    hero_visible=self.hero_deploy_ui.render_visible,
                ),
                target_framebuffer=self.defaultFramebufferObject(),
            )
            presented = FinalFrameCompositor(self.gpu_compositor).present(functions, composition)
            if presented and restore_attempt and restore_generation is not None:
                self._finalize_economy_restore(restore_generation)
            committed = presented
            return committed
        except Exception as exc:
            self.gpu_compositor.recover_failed_frame(exc)
            recovered = True
            # Never preserve metadata for a partially rendered modal frame.
            # The compatibility fallback below remains visible for the current
            # present; the next GL frame then performs a complete, deterministic
            # rebuild.  Economy restore generations intentionally stay pending.
            return False
        finally:
            if not committed:
                if not recovered:
                    self.gpu_compositor.recover_rejected_frame()
                self.invalidate_transient_overlay_scene()
                self.frame_scheduler.force()
            self._gpu_frame_recording = False

    def flush_gpu_hud_commands(
        self,
        functions,
        width: int,
        height: int,
        ratio: float,
    ) -> None:
        self._collect_gpu_hud_commands = False
        commands = self._gpu_hud_commands
        self._gpu_hud_commands = ClickGuiDrawList()
        if not commands.commands or not self.clickgui_gpu.available:
            return
        self.clickgui_gpu.prepare_frame(commands, ratio)
        self.clickgui_gpu.render(
            functions,
            commands,
            width,
            height,
            ratio,
            atlas_prepared=True,
        )

    def flush_gpu_island_icons(self, functions, width: int, height: int, ratio: float) -> None:
        self._collect_gpu_island_icons = False
        if not self._gpu_island_icon_commands or not self.island_icon_gpu.available:
            self._gpu_island_icon_commands.clear()
            return
        self.island_icon_gpu.render(
            functions,
            self._gpu_island_icon_commands,
            width,
            height,
            ratio,
        )
        self._gpu_island_icon_commands.clear()

    def flush_gpu_quick_action_wheel(
        self,
        functions,
        width: int,
        height: int,
        ratio: float,
    ) -> None:
        self._collect_gpu_quick_action_wheel = False
        command = self._gpu_quick_action_wheel_command
        labels = self._gpu_quick_action_wheel_labels
        self._gpu_quick_action_wheel_command = None
        self._gpu_quick_action_wheel_labels = ClickGuiDrawList()
        if command is None or not self.quick_action_wheel_gpu.available:
            return
        background = self.gpu_compositor.background_fbo
        blur = self.gpu_compositor.blur_fbo
        if background is None:
            return
        blur_texture = (
            blur.texture()
            if blur is not None and getattr(self.gpu_compositor, "_blur_valid", False)
            else background.texture()
        )
        if not self.quick_action_wheel_gpu.render(
            functions,
            command,
            width,
            height,
            ratio,
            background_texture=background.texture(),
            blur_texture=blur_texture,
        ):
            return
        if labels.commands and self.clickgui_gpu.available:
            self.clickgui_gpu.prepare_frame(labels, ratio)
            self.clickgui_gpu.render(
                functions,
                labels,
                width,
                height,
                ratio,
                atlas_prepared=True,
            )
