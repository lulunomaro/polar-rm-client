"""ClickGUI builders for Jello and retained GPU/CPU interfaces.

The public ``build_gpu_clickgui`` method keeps its historical draw-list return
and host interaction-registration API for direct callers. Compositor paths use
``polar.clickgui_frame.build_clickgui_frame`` to capture those registrations as
an explicit draw/interaction result, then publish the matching geometry once.
No layout, animation, typography, or backend-selection policy lives in that
adapter; the builders below remain the single source of visual geometry.

Shared text/scale helpers are inherited. Input clipping and ``add_hotspot`` stay
on PolarRMWindow. Existing methods retain their frozen compatibility qualnames.
"""

from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
)

# 相对导入层级：rm_client/ui/polar/mixins/ → .... = rm_client、... = rm_client.ui
from ...animation import Easings
from ...clickgui_gpu import ClickGuiDrawList
from ...gpu_frame_compositor import LensMaterial
from ...overlay_coordinator import OverlayKind
from ...paint import (
    accent_color,
    cap_center_y,
    draw_bar,
    glow_rect,
    glow_text,
    rounded_rect,
    with_alpha,
)
from ...theme import THEME, color
from ...jello_locale import chinese_enabled, ui_text, toast_text, description_label
from ....match_modes import MATCH_MODE_KEYS
from ....model import (
    CATEGORIES,
    GUI_MODES,
    LEGACY_GUI_MODES,
    PRIMARY_GUI_MODE,
    Module,
    Setting,
    normalize_legacy_gui_mode,
)
from ..primitives import EconomyScenePhase, InertialScroll

__all__ = [
    "ClickGuiBuildersMixin",
]


class ClickGuiBuildersMixin:
    """D5 ClickGUI 构建：GPU builders + 打开/关闭 + legacy CPU GUI。"""

    def open_legacy_clickgui(self, mode: str) -> bool:
        legacy = normalize_legacy_gui_mode(mode)
        if legacy is None:
            if str(mode) == PRIMARY_GUI_MODE:
                self.return_to_jello_clickgui()
                return True
            return False
        if not self.jello_state.open_legacy(legacy):
            return False
        self.module_expanded.clear()
        self.invalidate_gui_cache()
        self.toast(f"Old ClickGUI：{self.gui_mode_label(legacy)}")
        self.frame_scheduler.force()
        self.update()
        return True

    def return_to_jello_clickgui(self) -> None:
        self.jello_state.return_to_jello()
        self.mode_manager.set_mode(PRIMARY_GUI_MODE)
        self.invalidate_gui_cache()
        self.toast("Back to Jello Sigma")
        self.frame_scheduler.force()
        self.update()

    def set_clickgui_visible(
        self,
        visible: bool,
        *,
        announce: bool = True,
        play_sound: bool = True,
    ) -> None:
        visible = bool(visible)
        self._jello_background_press = None
        if visible:
            self.close_student_panel(immediate=True)
        if visible and self.hero_deploy_video_cut_active():
            self.open_hero_deploy_interface()
            self.toast("Hero deploy console is locked until exit sync")
            return
        if visible and self.economy_phase != EconomyScenePhase.CLOSED:
            self._economy_pending_overlay = OverlayKind.CLICKGUI
            if self.economy_phase != EconomyScenePhase.CLOSING:
                self.close_economy_workspace(play_sound=False)
            return
        returned_to_primary = bool(
            visible and self.gui_visible and self.jello_state.legacy_mode is not None
        )
        if visible and (not self.gui_visible or returned_to_primary):
            # Any public/open command is a Jello command, even if a retained
            # legacy preview happened to be visible already.
            self.jello_state.begin_session()
            self.mode_manager.set_mode(PRIMARY_GUI_MODE)
        elif not visible:
            self.jello_state.request_reset()
        if visible:
            if self.hero_deploy_ui.visible:
                self.close_hero_deploy_interface(force=True)
            if self.economy_visible:
                self.close_economy_workspace(play_sound=False)
            self.close_match_chat(clear=False)
            self.cancel_quick_action_wheel()
            self.overlay_coordinator.request_modal(OverlayKind.CLICKGUI)
        else:
            self.overlay_coordinator.close_modal(OverlayKind.CLICKGUI)
        if self.gui_visible == visible:
            if returned_to_primary:
                self.invalidate_gui_cache()
                self.frame_scheduler.force()
                self.update()
            return
        self.gui_visible = visible
        self.hotspots = []
        self.gui_timeline.set_target(1.0 if visible else 0.0)
        self.invalidate_transient_overlay_scene()
        # Sigma's ClickGuiScreen itself has no open/close cue.  Preserve the
        # historical Polar transition only when a legacy renderer is active.
        if play_sound and self.active_clickgui_mode() != PRIMARY_GUI_MODE:
            self.play_gui_transition_sound("expand" if visible else "collapse")
        if announce:
            self.toast("ClickGUI opened" if visible else "ClickGUI closed")
        self.frame_scheduler.force()
        self.update()

    def build_gpu_clickgui(self, width: int, height: int, alpha: float) -> ClickGuiDrawList:
        """Compatibility entrypoint: build draws and register host hit targets.

        Use ``build_clickgui_frame`` when layout and presentation are separate.
        """
        started = time.perf_counter()
        hotspot_start = len(self.hotspots)
        self.gui_scroll_viewports = []
        mode = self.active_clickgui_mode()
        if mode == PRIMARY_GUI_MODE:
            commands = self.jello_clickgui.build(
                self, self.jello_state, width, height, alpha
            )
        else:
            commands = ClickGuiDrawList()
        if mode == "PolarPanel":
            self.build_gpu_panel_gui(commands, width, height, alpha)
        elif mode == "PolarOld":
            self.build_gpu_old_gui(commands, width, height, alpha)
        elif mode == "PolarNew":
            self.build_gpu_new_gui(commands, width, height, alpha)
        elif mode == "PolarDropdown":
            self.build_gpu_dropdown_gui(commands, width, height, alpha)
        elif mode == "PolarUnified":
            self.build_gpu_polar_unified(commands, width, height, alpha)
        elif mode == "PolarMulti":
            self.build_gpu_polar_multi(commands, width, height, alpha)
        if mode in LEGACY_GUI_MODES:
            self.build_gpu_legacy_return(commands, width, height, alpha)
            self.build_gpu_toasts(commands, width, height)
            if self.hover_tooltip:
                self.build_gpu_tooltip(commands, self.hover_point, self.hover_tooltip)
            if self.binding_module is not None:
                self.build_gpu_keybind_overlay(commands, width, height)
        if not self.gui_visible:
            del self.hotspots[hotspot_start:]
            self.gui_scroll_viewports = []
        self.clickgui_gpu_layout_ms = (time.perf_counter() - started) * 1000.0
        return commands

    def build_gpu_glass_rect(
        self,
        commands: ClickGuiDrawList,
        rect: QRectF,
        radius: float,
        alpha: float,
        accent: QColor,
        *,
        hover: float = 0.0,
        material: LensMaterial | None = None,
        refraction_height_px: float | None = None,
        refraction_amount_px: float | None = None,
    ) -> None:
        """提交 Combined Backdrop 镜片，材质由用途显式决定。"""
        if self._gpu_frame_recording:
            island_shape = radius >= rect.height() * 0.44
            surface = "clear" if island_shape else "sub" if rect.height() <= 48.0 else "main"
            lens_material = material or (LensMaterial.ISLAND if island_shape else LensMaterial.REGULAR)
            self.gpu_compositor.add_glass(
                rect,
                radius,
                strength=0.92 + hover * 0.18,
                surface=surface,
                hover=hover,
                opacity=alpha,
                material=lens_material,
                refraction_height_px=refraction_height_px,
                refraction_amount_px=refraction_amount_px,
            )

    def build_gpu_startup_button(self, commands: ClickGuiDrawList, rect: QRectF, index: int, entry: tuple, scale: float, active: float, *, settled: bool = False) -> None:
        key, icon, title, subtitle, detail, chip_text, action, payload, tooltip, accent = entry
        enter = 1.0 if settled else self.startup_stagger_value(index, 0.42, 0.10)
        hovered = rect.contains(self.hover_point)
        hover = 0.0 if settled else self.anim.value(f"startup.gpu.hover.{key}", 1.0 if hovered else 0.0, self.anim_duration(0.16), Easings.ease_out_expo)
        draw_rect = QRectF(rect)
        draw_rect.translate(0.0, 10.0 * (1.0 - enter) * scale - 3.0 * hover * scale)
        opacity = active * max(0.0, min(1.0, enter))
        self.build_gpu_glass_rect(
            commands,
            draw_rect,
            18.0 * scale,
            opacity,
            accent,
            hover=hover,
            material=LensMaterial.STRONG,
            refraction_height_px=draw_rect.height() * 0.375,
            refraction_amount_px=draw_rect.height() * 0.50,
        )
        # Accent 条继承宿主玻璃的圆角半径做 SDF 裁剪：
        # 4px 窄条的圆角被钳到 ~2s，若不裁剪会在玻璃 r=18s 的弧角外泄 ~7px。
        commands.rect(
            QRectF(draw_rect.left(), draw_rect.top(), 4.0 * scale, draw_rect.height()),
            2.0 * scale,
            self.gpu_alpha(with_alpha(accent, 0.90), opacity),
            clip=QRectF(draw_rect),
            clip_radius=18.0 * scale,
        )
        commands.rect(
            QRectF(draw_rect.left() + 4.0 * scale, draw_rect.top(), 8.0 * scale, draw_rect.height()),
            8.0 * scale,
            color("#000000", 0.0),
            color("#000000", 0.0),
            0.0,
                self.gpu_alpha(with_alpha(accent, 0.10 + hover * 0.08), opacity),
            clip=QRectF(draw_rect),
            clip_radius=18.0 * scale,
        )
        icon_size = min(90.0 * scale, draw_rect.height() - 28.0 * scale)
        icon_rect = QRectF(draw_rect.left() + 26.0 * scale, draw_rect.center().y() - icon_size / 2.0, icon_size, icon_size)
        icon_fill = self.mix_color(accent, THEME.text, hover)
        icon_text = self.mix_color(THEME.text, accent, hover)
        commands.rect(icon_rect.adjusted(-4.0 * scale, -4.0 * scale, 4.0 * scale, 4.0 * scale), icon_size * 0.54, self.gpu_alpha(with_alpha(accent, 0.10 + hover * 0.10), opacity), self.gpu_alpha(with_alpha(accent, 0.38 + hover * 0.30), opacity), 1.0)
        commands.rect(icon_rect, icon_size * 0.50, self.gpu_alpha(with_alpha(icon_fill, 0.92), opacity), self.gpu_alpha(color("#ffffff", 0.16 + hover * 0.15), opacity), 1.0)
        icon_glyph = {"official": "\ue9e0", "sim": "\ue9f4", "lab": "\uea4b", "rmuc": "7", "rmul": "3", "back": "<"}.get(str(icon).lower(), str(icon)[:1].upper())
        commands.text(icon_rect, icon_glyph, self.font("material" if len(icon_glyph) == 1 and ord(icon_glyph) >= 0xE000 else "poppins_bold", min(25.0 * scale, icon_size * 0.42), QFont.Weight.Bold), self.gpu_alpha(icon_text, opacity), Qt.AlignmentFlag.AlignCenter)
        text_left = icon_rect.right() + 24.0 * scale
        title_rect = QRectF(text_left, draw_rect.top() + 21.0 * scale, draw_rect.width() - (text_left - draw_rect.left()) - 126.0 * scale, 28.0 * scale)
        commands.text(title_rect, ui_text(title, chinese_enabled(self.registry)), self.font("poppins_bold", 18.0 * scale), self.gpu_alpha(THEME.text, opacity))
        commands.text(QRectF(text_left, draw_rect.top() + 50.0 * scale, draw_rect.width() - (text_left - draw_rect.left()) - 42.0 * scale, 20.0 * scale), ui_text(subtitle, chinese_enabled(self.registry)), self.font("poppins_regular", 9.0 * scale), self.gpu_alpha(color("#e1e8ee", 0.88), opacity))
        commands.text(QRectF(text_left, draw_rect.bottom() - 31.0 * scale, draw_rect.width() - (text_left - draw_rect.left()) - 42.0 * scale, 18.0 * scale), ui_text(detail, chinese_enabled(self.registry)), self.font("poppins_regular", 7.3 * scale), self.gpu_alpha(color("#aeb7bd", 0.90), opacity))
        shortcut = QRectF(draw_rect.right() - 78.0 * scale, draw_rect.top() + 20.0 * scale, 26.0 * scale, 22.0 * scale)
        commands.rect(shortcut, 6.0 * scale, self.gpu_alpha(color("#ffffff", 0.08), opacity), self.gpu_alpha(color("#ffffff", 0.16), opacity), 0.7)
        commands.text(shortcut, ui_text(str(index + 1) if action != "startup_back" else "B", chinese_enabled(self.registry)), self.font("poppins_bold", 7.3 * scale), self.gpu_alpha(icon_text if hover > 0.55 else accent, opacity), Qt.AlignmentFlag.AlignCenter)
        chip = QRectF(draw_rect.right() - 98.0 * scale - 26.0 * scale, draw_rect.bottom() - 34.0 * scale, 98.0 * scale, 22.0 * scale)
        commands.rect(chip, 7.0 * scale, self.gpu_alpha(color("#ffffff", 0.07 + hover * 0.04), opacity), self.gpu_alpha(with_alpha(accent, 0.30 + hover * 0.20), opacity), 0.7)
        commands.text(chip, ui_text(chip_text, chinese_enabled(self.registry)), self.font("poppins_medium", 7.2 * scale), self.gpu_alpha(icon_text if hover > 0.72 else accent, opacity), Qt.AlignmentFlag.AlignCenter)
        commands.text(QRectF(draw_rect.right() - 43.0 * scale, draw_rect.center().y() - 13.0 * scale, 18.0 * scale, 26.0 * scale), ui_text(">", chinese_enabled(self.registry)), self.font("poppins_bold", 15.0 * scale), self.gpu_alpha(THEME.text if hovered else THEME.muted, opacity), Qt.AlignmentFlag.AlignCenter)
        self.add_hotspot(draw_rect.adjusted(-3.0 * scale, -3.0 * scale, 3.0 * scale, 3.0 * scale), action, payload, tooltip=ui_text(tooltip, chinese_enabled(self.registry)))

    def build_gpu_startup_session(self, commands: ClickGuiDrawList, menu_x: float, menu_y: float, button_w: float, height: int, scale: float, active: float, is_match: bool) -> None:
        right_x = menu_x + button_w + 48.0 * scale
        right_w = self.width() - right_x - 50.0 * scale
        if right_w < 245.0 * scale:
            return
        panel = QRectF(right_x, menu_y + 4.0 * scale, min(390.0 * scale, right_w), min(300.0 * scale, height - menu_y - 108.0 * scale))
        self.build_gpu_glass_rect(
            commands,
            panel,
            18.0 * scale,
            active,
            THEME.cyan,
            material=LensMaterial.REGULAR,
            refraction_height_px=min(18.0 * scale, panel.height() * 0.12),
            refraction_amount_px=min(24.0 * scale, panel.height() * 0.16),
        )
        commands.rect(QRectF(panel.left() + 14.0 * scale, panel.top() + 12.0 * scale, panel.width() - 28.0 * scale, 2.0 * scale), 1.0, self.gpu_alpha(THEME.cyan, active), None, 0.0, self.gpu_alpha(color("#ffffff", 0.24), active))
        commands.text(QRectF(panel.left() + 18.0 * scale, panel.top() + 26.0 * scale, panel.width() - 36.0 * scale, 23.0 * scale), ui_text("SESSION", chinese_enabled(self.registry)), self.font("poppins_bold", 11.0 * scale), self.gpu_alpha(THEME.text, active))
        scenario = self.startup_scenario_meta(self.scenario_manager.current.key)
        match = self.startup_match_meta(self.match_mode_manager.current.key)
        rows = [("ENV", scenario["title"]), ("MATCH", match["title"]), ("CONFIG", self.current_profile()), ("MQTT", "mock" if not self.mqtt.connected else "live"), ("SERIAL", "mock" if not self.serial.connected else "live")]
        row_y = panel.top() + 62.0 * scale
        for index, (label, value) in enumerate(rows):
            accent = accent_color(index, 0.08)
            row = QRectF(panel.left() + 18.0 * scale, row_y + index * 34.0 * scale, panel.width() - 36.0 * scale, 24.0 * scale)
            # SESSION 只有外层是镜片；状态行属于普通内容层，避免玻璃套玻璃。
            commands.rect(
                row,
                6.0 * scale,
                self.gpu_alpha(color("#ffffff", 0.045), active),
                self.gpu_alpha(color("#ffffff", 0.10), active),
                0.65,
            )
            commands.text(QRectF(row.left() + 10.0 * scale, row.top(), 58.0 * scale, row.height()), ui_text(label, chinese_enabled(self.registry)), self.font("poppins_bold", 7.0 * scale), self.gpu_alpha(accent, active), Qt.AlignmentFlag.AlignVCenter)
            commands.text(row.adjusted(72.0 * scale, 0, -10.0 * scale, 0), (value if label == "CONFIG" else ui_text(value, chinese_enabled(self.registry))), self.font("poppins_medium", 8.1 * scale), self.gpu_alpha(color("#dce7ed", 0.92), active), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        tip = QRectF(panel.left() + 18.0 * scale, panel.bottom() - 56.0 * scale, panel.width() - 36.0 * scale, 37.0 * scale)
        commands.rect(
            tip,
            10.0 * scale,
            self.gpu_alpha(color("#ffffff", 0.035), active),
            self.gpu_alpha(color("#ffffff", 0.10), active),
            0.65,
        )
        commands.text(tip.adjusted(12.0 * scale, 0, -12.0 * scale, 0), ui_text("Press 1/2 to load. Backspace returns." if is_match else "Press 1/2/3 to select. Enter continues.", chinese_enabled(self.registry)), self.font("poppins_regular", 7.2 * scale), self.gpu_alpha(THEME.muted, active), Qt.AlignmentFlag.AlignCenter)

    def build_gpu_startup_bottom_bar(self, commands: ClickGuiDrawList, width: int, height: int, scale: float, active: float, is_match: bool, *, settled: bool = False) -> None:
        actions = [("Settings", "startup_open_settings", None, THEME.cyan), ("Config", "startup_open_config", None, THEME.cyan), ("Import", "config_action", "Import Config", THEME.pink), ("Wallpaper", "startup_open_wallpaper", None, color("#4677ff")), ("ClickGUI", "startup_open_gui", None, color("#a98aff")), ("Exit", "startup_exit", None, color("#ff6464"))]
        if is_match:
            actions.insert(0, ("Back", "startup_back", None, color("#ffd766")))
        button_h, gap, button_w = 42.0 * scale, 10.0 * scale, 82.0 * scale
        total_w = len(actions) * button_w + max(0, len(actions) - 1) * gap + 28.0 * scale
        bar = QRectF(50.0 * scale, height - 64.0 * scale, min(total_w, width - 100.0 * scale), button_h + 18.0 * scale)
        self.build_gpu_glass_rect(commands, bar, 8.0 * scale, active, THEME.cyan)
        x = bar.left() + 14.0 * scale
        for label, action, payload, accent in actions:
            rect = QRectF(x, bar.top() + 9.0 * scale, button_w, button_h)
            hovered = rect.contains(self.hover_point)
            hover = 0.0 if settled else self.anim.value(f"startup.gpu.bottom.{label}", 1.0 if hovered else 0.0, self.anim_duration(0.16), Easings.ease_out_expo)
            commands.rect(rect, 6.0 * scale, self.gpu_alpha(with_alpha(accent, 0.08 + hover * 0.12), active), self.gpu_alpha(with_alpha(accent, 0.20 + hover * 0.25), active), 0.8)
            commands.text(rect, ui_text(label, chinese_enabled(self.registry)), self.font("poppins_medium", 8.2 * scale), self.gpu_alpha(THEME.text, active), Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(rect, action, payload, tooltip=ui_text(label, chinese_enabled(self.registry)))
            x += button_w + gap

    def build_gpu_startup_hover_layer(self, width: int, height: int) -> ClickGuiDrawList:
        """稳定页只绘制 hover 折射层，避免鼠标移动重建所有文字与图标。"""
        commands = ClickGuiDrawList()
        if self.startup_step == "robot":
            return commands
        scale = max(0.74, min(1.14, self.get_auto_ui_scale(width, height, strength=0.45)))
        menu_x, menu_y, button_w, button_h, gap = self.liquid_menu_layout(width, height, 3, scale)
        is_match = self.startup_step == "match"
        if is_match:
            entries = [
                (key, self.startup_menu_accent(index + 2))
                for index, key in enumerate((*MATCH_MODE_KEYS, "back"))
            ]
        else:
            entries = [
                (key, self.startup_menu_accent(index))
                for index, key in enumerate(("OfficialEvent", "CampusSimulation", "LabDebug"))
            ]
        for index, (key, accent) in enumerate(entries):
            rect = QRectF(menu_x, menu_y + index * (button_h + gap), button_w, button_h)
            hovered = rect.contains(self.hover_point)
            hover = self.anim.value(f"startup.gpu.hover.{key}", 1.0 if hovered else 0.0, self.anim_duration(0.16), Easings.ease_out_expo)
            if hover <= 0.002:
                continue
            lift = 4.0 * hover * scale
            overlay = QRectF(rect)
            overlay.translate(0.0, -lift)
            self.build_gpu_glass_rect(commands, overlay, 5.0 * scale, 0.24 * hover, accent, hover=hover)
            commands.rect(
                overlay,
                5.0 * scale,
                color("#000000", 0.0),
                None,
                0.0,
                self.gpu_alpha(with_alpha(accent, 0.055), hover),
            )
            commands.rect(
                QRectF(overlay.left(), overlay.top(), 3.0 * scale, overlay.height()),
                1.5 * scale,
                self.gpu_alpha(with_alpha(accent, 0.96), hover),
                clip=QRectF(overlay),
                clip_radius=5.0 * scale,
            )

        actions = [("Settings", THEME.cyan), ("Config", THEME.cyan), ("Import", THEME.pink), ("Wallpaper", color("#4677ff")), ("ClickGUI", color("#a98aff")), ("Exit", color("#ff6464"))]
        if is_match:
            actions.insert(0, ("Back", color("#ffd766")))
        button_h, action_gap, action_w = 42.0 * scale, 10.0 * scale, 82.0 * scale
        total_w = len(actions) * action_w + max(0, len(actions) - 1) * action_gap + 28.0 * scale
        bar = QRectF(50.0 * scale, height - 64.0 * scale, min(total_w, width - 100.0 * scale), button_h + 18.0 * scale)
        x = bar.left() + 14.0 * scale
        for label, accent in actions:
            rect = QRectF(x, bar.top() + 9.0 * scale, action_w, button_h)
            hover = self.anim.value(f"startup.gpu.bottom.{label}", 1.0 if rect.contains(self.hover_point) else 0.0, self.anim_duration(0.16), Easings.ease_out_expo)
            if hover > 0.002:
                commands.rect(rect, 6.0 * scale, self.gpu_alpha(with_alpha(accent, 0.12), hover), self.gpu_alpha(with_alpha(accent, 0.44), hover), 0.8)
            x += action_w + action_gap
        return commands

    def gpu_alpha(self, source: QColor, alpha: float) -> QColor:
        return with_alpha(source, alpha)

    def gui_primary_text(self, alpha: float = 1.0) -> QColor:
        """High-contrast primary label on dark PolarPanel glass (near pure white)."""
        return self.gpu_alpha(color("#ffffff"), alpha)

    def gui_secondary_text(self, alpha: float = 1.0) -> QColor:
        """Secondary/meta text — intentionally brighter than THEME.muted."""
        return self.gpu_alpha(color("#e2e4ea"), alpha)

    def gui_disabled_text(self, alpha: float = 1.0) -> QColor:
        """Disabled module name — still scannable without squinting."""
        return self.gpu_alpha(color("#c6c8d0"), alpha)

    def build_gpu_panel_gui(self, commands: ClickGuiDrawList, width: int, height: int, alpha: float) -> None:
        scale = self.get_gui_scale_for_bounds(width, height, 600.0, 440.0, 76.0, 82.0) * (0.98 + 0.02 * alpha)
        panel = QRectF(width / 2 - 300 * scale, height / 2 - 200 * scale, 600 * scale, 400 * scale)
        commands.rect(panel, 8 * scale, color("#101014", 0.96 * alpha), color("#ffffff", 0.13 * alpha), 1.0)
        # Match draw_profile_widget: title + Chinese profile/scale subtitle.
        commands.text(QRectF(panel.left() + 14 * scale, panel.top() + 10 * scale, 200 * scale, 18 * scale), "POLAR-RM", self.font("poppins_bold", 14 * scale), self.gui_primary_text(alpha))
        commands.text(
            QRectF(panel.left() + 14 * scale, panel.top() + 28 * scale, 240 * scale, 14 * scale),
            f"配置 {self.current_profile()} | 缩放 {self.get_gui_scale():.2f}",
            self.font("poppins_medium", 8.2 * scale),
            self.gui_secondary_text(alpha),
        )
        category_bar = QRectF(panel.left() + 414 * scale, panel.top() + 18 * scale, 166 * scale, 28 * scale)
        step = category_bar.width() / len(CATEGORIES)
        target = float(CATEGORIES.index(self.selected_category)) if self.selected_category in CATEGORIES else 0.0
        current = self.gui_motion.value("gpu.panel.category", target, self.anim_duration(0.22), Easings.ease_out_expo, initial=target)
        commands.rect(QRectF(category_bar.left() + current * step + 2 * scale, category_bar.top() + 2 * scale, step - 4 * scale, category_bar.height() - 4 * scale), 8 * scale, color("#ffffff", 0.08 * alpha), color("#ffffff", 0.20 * alpha), 1.0)
        for index, category in enumerate(CATEGORIES):
            box = QRectF(category_bar.left() + index * step, category_bar.top(), step, category_bar.height())
            commands.text(
                box,
                self.category_icon(category),
                self.font("material", 12.0 * scale, QFont.Weight.Bold),
                self.gui_primary_text(alpha) if category == self.selected_category else self.gui_secondary_text(alpha * 0.92),
                Qt.AlignmentFlag.AlignCenter,
            )
            self.add_hotspot(box, "select_category", category, tooltip=ui_text(category, chinese_enabled(self.registry)))

        module_rect = QRectF(panel.left() + 12 * scale, panel.top() + 43 * scale, 160 * scale, 316 * scale)
        settings_rect = QRectF(panel.left() + 187 * scale, panel.top() + 43 * scale, 401 * scale, 316 * scale)
        self.panel_modules_rect = module_rect
        self.panel_settings_rect = settings_rect
        commands.rect(module_rect, 4 * scale, color("#ffffff", 0.055 * alpha), color("#ffffff", 0.035 * alpha), 1.0)
        commands.text(
            module_rect.adjusted(10 * scale, 7 * scale, -10 * scale, -module_rect.height() + 30 * scale),
            ui_text(self.selected_category, chinese_enabled(self.registry)),
            self.font("poppins_bold", 12.4 * scale),
            self.gui_primary_text(alpha),
        )
        modules = self.panel_modules()
        top = module_rect.top() + 38 * scale
        row_h = 23 * scale
        module_clip = module_rect.adjusted(0, 32 * scale, 0, -4 * scale)
        max_scroll = max(0.0, len(modules) * row_h - module_clip.height())
        target_scroll = max(0.0, min(max_scroll, self.panel_module_scroll.get(self.selected_category, 0.0)))
        scroll = self.gui_motion.value(
            f"gpu.panel.modules.scroll.{self.selected_category}",
            target_scroll,
            self.anim_duration(0.16),
            Easings.ease_out_cubic,
            initial=target_scroll,
        )
        top -= scroll
        selected_index = float(next((i for i, item in enumerate(modules) if item is self.selected_module), 0))
        selected_y = self.gui_motion.value("gpu.panel.module", selected_index, self.anim_duration(0.18), Easings.ease_out_expo, initial=selected_index)
        if modules:
            commands.rect(QRectF(module_rect.left() + 6 * scale, top + selected_y * row_h, module_rect.width() - 12 * scale, 20 * scale), 5 * scale, color("#ffffff", 0.10 * alpha), clip=module_clip)
        for index, module in enumerate(modules):
            row = QRectF(module_rect.left() + 6 * scale, top + index * row_h, module_rect.width() - 12 * scale, 20 * scale)
            self.build_gpu_module_row(commands, row, module, scale, alpha, module_clip, select_only=True)

        commands.rect(settings_rect, 4 * scale, color("#ffffff", 0.075 * alpha), color("#ffffff", 0.08 * alpha), 1.0)
        commands.text(
            settings_rect.adjusted(12 * scale, 9 * scale, -72 * scale, -settings_rect.height() + 38 * scale),
            ui_text(self.selected_module.name, chinese_enabled(self.registry)),
            self.font("poppins_bold", 13.5 * scale),
            self.gui_primary_text(alpha),
        )
        self.build_gpu_toggle(commands, QRectF(settings_rect.right() - 57 * scale, settings_rect.top() + 13 * scale, 36 * scale, 16 * scale), self.selected_module.enabled, alpha, ("module_toggle", self.selected_module.name))
        content = settings_rect.adjusted(10 * scale, 43 * scale, -10 * scale, -10 * scale)
        scroll = self.gui_motion.value(f"gpu.settings.scroll.{self.selected_module.name}", self.settings_scroll.get(self.selected_module.name, 0.0), self.anim_duration(0.12), Easings.ease_out_cubic, initial=self.settings_scroll.get(self.selected_module.name, 0.0))
        self.build_gpu_settings(commands, self.selected_module, content, scale, alpha, scroll)
        search = QRectF(width / 2 - 104 * scale, min(height - 60.0, panel.bottom() + 15 * scale), 208 * scale, 23 * scale)
        commands.rect(search, search.height() / 2, color("#16161b", 0.94 * alpha), color("#ffffff", 0.16 * alpha), 1.0)
        commands.text(
            search.adjusted(12 * scale, 0, -12 * scale, 0),
            self.search_query or ui_text("Search modules...", chinese_enabled(self.registry)),
            self.font("poppins_medium", 8.2 * scale),
            self.gui_primary_text(alpha) if self.search_query else self.gui_secondary_text(alpha * 0.9),
        )
        self.add_hotspot(search, "search_focus")

    def build_gpu_old_gui(self, commands: ClickGuiDrawList, width: int, height: int, alpha: float) -> None:
        scale = self.get_gui_scale_for_bounds(width, height, 6 * 140.0 + 5 * 20.0, 520.0, 20.0, 54.0)
        panel_w, row_h = 140 * scale, 20 * scale
        for index, category in enumerate(CATEGORIES):
            data = self.old_panels[category]
            if not data.get("dragged", False):
                data["x"], data["y"] = 20.0 * scale + index * 160.0 * scale, 20.0 * scale
            x, y = float(data["x"]), float(data["y"])
            header = QRectF(x, y, panel_w, row_h)
            commands.rect(header, 3 * scale, color("#101014", 0.94 * alpha), color("#ffffff", 0.12 * alpha), 1.0)
            commands.text(header.adjusted(8 * scale, 0, -8 * scale, 0), f"{self.category_icon(category)}  {ui_text(category, chinese_enabled(self.registry))}", self.font("poppins_bold", 8.2 * scale), self.gpu_alpha(THEME.text, alpha))
            self.add_hotspot(header, "old_drag", category, button=1, tooltip=ui_text("拖动面板", chinese_enabled(self.registry)))
            self.add_hotspot(header, "old_expand", category, button=3, tooltip=ui_text("展开分类", chinese_enabled(self.registry)))
            open_v = self.gui_motion.value(f"old.panel.{category}", 1.0 if data["expanded"] else 0.0, self.anim_duration(0.34), Easings.ease_out_expo)
            modules = self.registry.by_category(category)
            body_top = y + row_h
            content_h = sum(self.module_draw_height(module, scale, True) for module in modules)
            body_h = min(content_h, max(80.0 * scale, height - body_top - 22.0 * scale))
            maximum = max(0.0, content_h - body_h)
            data["scroll"] = max(0.0, min(float(data.get("scroll", 0.0)), maximum))
            scroll = self.gui_motion.value(f"gpu.old.scroll.{category}", float(data["scroll"]), self.anim_duration(0.12), Easings.ease_out_cubic, initial=float(data["scroll"]))
            clip = QRectF(x, body_top, panel_w, body_h * open_v)
            self.register_gui_scroll_viewport("old", category, clip, content_h, body_h)
            yy = body_top - scroll
            for item_index, module in enumerate(modules):
                reveal = self.gui_motion.waterfall(open_v, item_index, len(modules))
                slot = self.module_draw_height(module, scale, True)
                row = QRectF(x + (1.0 - reveal) * 5 * scale, yy, panel_w - (1.0 - reveal) * 5 * scale, row_h)
                if reveal > 0.01:
                    self.build_gpu_module_row(commands, row, module, scale, alpha * reveal, clip, compact=True, style="old")
                    self.build_gpu_inline_settings(commands, module, row.bottom(), row.left() + 5 * scale, row.width() - 10 * scale, scale, alpha * reveal, clip, f"old.expand.{module.name}", 6)
                yy += slot

    def build_gpu_new_gui(self, commands: ClickGuiDrawList, width: int, height: int, alpha: float) -> None:
        base_w = len(CATEGORIES) * 120.0 + (len(CATEGORIES) - 1) * 8.0
        scale = self.get_gui_scale_for_bounds(width, height, base_w, 430.0, 56.0, 118.0)
        gap, panel_w = 8 * scale, 120 * scale
        start_x = width / 2 - (len(CATEGORIES) * panel_w + (len(CATEGORIES) - 1) * gap) / 2
        # NewClickGui lays its six 120px columns at y=36 from centerX-380.
        # Only clamp on genuinely small windows; 1080p/2K stay source-faithful.
        y = 36.0 * scale if height >= 420.0 * scale else max(12.0 * scale, height * 0.08)
        max_h = min(430 * scale, max(180 * scale, height - y - 32 * scale))
        for index, category in enumerate(CATEGORIES):
            modules = self.registry.by_category(category)
            panel_h = min(max_h, max(72 * scale, 33 * scale + sum(self.module_draw_height(module, scale, True) for module in modules)))
            enter = self.gui_motion.value(f"new.panel.{category}", 1.0, self.anim_duration(0.30 + index * 0.035), Easings.ease_out_expo, initial=0.0)
            rect = QRectF(start_x + index * (panel_w + gap), y + (1.0 - enter) * 9 * scale, panel_w, panel_h)
            # Polar New uses a neutral #171717 surface with a narrow
            # teal accent line; it is not the blue-grey POLAR panel surface.
            commands.rect(rect, 7 * scale, color("#171717", 0.96 * alpha * enter), color("#ffffff", 0.08 * alpha * enter), 1.0)
            commands.rect(QRectF(rect.left(), rect.top(), rect.width(), max(1.0, scale)), 0, color("#2de8ca", 0.88 * alpha * enter), clip=rect)
            commands.text(rect.adjusted(8 * scale, 3 * scale, -8 * scale, -rect.height() + 25 * scale), ui_text(category, chinese_enabled(self.registry)), self.font("poppins_bold", 8 * scale), self.gpu_alpha(THEME.text, alpha * enter))
            self.add_hotspot(QRectF(rect.left(), rect.top(), rect.width(), 24 * scale), "select_category", category, tooltip=ui_text(category, chinese_enabled(self.registry)))
            clip = rect.adjusted(0, 26 * scale, 0, -4 * scale)
            content_h = sum(self.module_draw_height(module, scale, True) for module in modules)
            maximum = max(0.0, content_h - clip.height())
            self.new_panels[category]["scroll"] = max(0.0, min(float(self.new_panels[category].get("scroll", 0.0)), maximum))
            scroll = self.gui_motion.value(f"gpu.new.scroll.{category}", float(self.new_panels[category]["scroll"]), self.anim_duration(0.12), Easings.ease_out_cubic, initial=float(self.new_panels[category]["scroll"]))
            self.register_gui_scroll_viewport("new", category, clip, content_h, clip.height())
            yy = rect.top() + 27 * scale - scroll
            for module in modules:
                row = QRectF(rect.left() + 4 * scale, yy, rect.width() - 8 * scale, 21 * scale)
                self.build_gpu_module_row(commands, row, module, scale, alpha * enter, clip, compact=True, style="new")
                self.build_gpu_inline_settings(commands, module, row.bottom(), row.left() + 4 * scale, row.width() - 8 * scale, scale, alpha * enter, clip, f"new.expand.{module.name}", 5)
                yy += self.module_draw_height(module, scale, True)

    def build_gpu_dropdown_gui(self, commands: ClickGuiDrawList, width: int, height: int, alpha: float) -> None:
        # TestClient ClickGuiScreen: x=10, y=10, category width=120,
        # header=28 and a 10px column gap.  Never stretch it into the former
        # centred 226px approximation.
        scale = self.get_gui_scale_for_bounds(width, height, 6 * 120.0 + 5 * 10.0 + 20.0, 470.0, 10.0, 38.0)
        gap = 10 * scale
        panel_w = 120.0 * scale
        x0, y0 = 10.0 * scale, 10.0 * scale
        for index, category in enumerate(CATEGORIES):
            modules = self.registry.by_category(category)
            open_v = self.gui_motion.value(f"dropdown.panel.{category}", 1.0 if self.dropdown_panels[category]["expanded"] else 0.0, self.anim_duration(0.26), Easings.ease_out_expo)
            header_h = 28 * scale
            content_h = sum(self.module_draw_height(module, scale, False) for module in modules) + 8 * scale
            body_h = min(430 * scale, max(60 * scale, height - y0 - header_h - 22 * scale), content_h)
            panel = self.dropdown_panels[category]
            maximum = max(0.0, content_h - body_h)
            panel["scroll"] = max(0.0, min(float(panel.get("scroll", 0.0)), maximum))
            scroll = self.gui_motion.value(f"gpu.dropdown.scroll.{category}", float(panel["scroll"]), self.anim_duration(0.12), Easings.ease_out_cubic, initial=float(panel["scroll"]))
            rect = QRectF(x0 + index * (panel_w + gap), y0, panel_w, header_h + body_h * open_v)
            commands.rect(rect, 5 * scale, color("#0c0c0c", 0.57 * alpha), color("#ffffff", 0.07 * alpha), 1.0)
            commands.rect(QRectF(rect.left(), rect.top(), rect.width(), header_h), 5 * scale, color("#2a2a2a", 0.76 * alpha), clip=rect)
            commands.text(QRectF(rect.left() + 12 * scale, rect.top(), rect.width() - 24 * scale, header_h), f"{self.category_icon(category)}  {ui_text(category, chinese_enabled(self.registry))}", self.font("poppins_bold", 10 * scale), self.gpu_alpha(THEME.text, alpha))
            self.add_hotspot(QRectF(rect.left(), rect.top(), rect.width(), header_h), "dropdown_expand", category, button=3, tooltip=ui_text("展开分类", chinese_enabled(self.registry)))
            clip = QRectF(rect.left(), rect.top() + header_h, rect.width(), body_h * open_v)
            self.register_gui_scroll_viewport("dropdown", category, clip, content_h, body_h)
            yy = rect.top() + header_h - scroll
            for item_index, module in enumerate(modules):
                reveal = self.gui_motion.waterfall(open_v, item_index, len(modules))
                row = QRectF(rect.left() + (1 - reveal) * 6 * scale, yy, rect.width() - (1 - reveal) * 6 * scale, 24 * scale)
                if reveal > 0.01:
                    self.build_gpu_module_row(commands, row, module, scale, alpha * reveal, clip, style="dropdown", row_index=item_index)
                    self.build_gpu_inline_settings(commands, module, row.bottom(), row.left() + 9 * scale, row.width() - 18 * scale, scale, alpha * reveal, clip, f"dropdown.expand.{module.name}", 6)
                yy += self.module_draw_height(module, scale, False)

    def register_gui_scroll_viewport(self, mode: str, key: str, rect: QRectF, content_height: float, viewport_height: float) -> None:
        if rect.width() <= 1.0 or rect.height() <= 1.0:
            return
        self.gui_scroll_viewports.append({
            "mode": mode,
            "key": key,
            "rect": QRectF(rect),
            "maximum": max(0.0, float(content_height) - float(viewport_height)),
        })

    def polar_view_modules(self, category: str) -> list[Module]:
        if category != "Profile":
            return self.registry.by_category(category)
        names = ("ConfigManager", "UIScale", "ClickGui", "AudioFeedback")
        return [module for name in names if (module := self.registry.get(name)) is not None]

    def build_gpu_polar_glass(self, commands: ClickGuiDrawList, rect: QRectF, radius: float, alpha: float, *, prominent: bool = False, hover: float = 0.0) -> None:
        """POLAR 视图使用正式镜片，不用半透明色块伪装玻璃。"""
        if self._gpu_frame_recording:
            refraction_height = min(42.0, rect.height() * (0.22 if prominent else 0.12))
            refraction_amount = min(58.0, rect.height() * (0.30 if prominent else 0.16))
            self.gpu_compositor.add_glass(
                rect,
                radius,
                strength=1.08 if prominent else 0.98,
                surface="clear" if prominent else "main",
                hover=hover,
                opacity=alpha,
                material=LensMaterial.STRONG if prominent else LensMaterial.REGULAR,
                refraction_height_px=refraction_height,
                refraction_amount_px=refraction_amount,
            )
        # 强镜片会直接替换组合背景；再次绘制整块描边会在圆角外形成方形光晕。
        if not prominent:
            commands.rect(
                rect,
                radius,
                color("#ffffff", 0.0),
                color("#d8ffe8", 0.10 * alpha),
                0.55,
            )

    def build_gpu_polar_unified(self, commands: ClickGuiDrawList, width: int, height: int, alpha: float) -> None:
        scale = self.get_gui_scale_for_bounds(width, height, 800.0, 540.0, 74.0, 86.0)
        progress = self.polar_unified_morph.progress
        island_w = 236.0 * scale + (800.0 * scale - 236.0 * scale) * progress
        island_h = 36.0 * scale + (540.0 * scale - 36.0 * scale) * progress
        panel = QRectF(width / 2.0 - island_w / 2.0, height / 2.0 - island_h / 2.0, island_w, island_h)
        self.build_gpu_polar_glass(commands, panel, (24.0 - 6.0 * progress) * scale, progress, prominent=True)
        if progress < 0.08:
            return
        reveal = max(0.0, min(1.0, (progress - 0.16) / 0.84)) * alpha
        header = QRectF(panel.left(), panel.top(), panel.width(), 42.0 * scale)
        commands.text(header.adjusted(17 * scale, 0, -120 * scale, 0), "POLAR-RM", self.font("poppins_bold", 13.0 * scale), self.gpu_alpha(color("#f2fff6"), reveal))
        commands.text(QRectF(header.right() - 98 * scale, header.top(), 82 * scale, header.height()), ui_text("POLAR VIEW", chinese_enabled(self.registry)), self.font("poppins_bold", 7.0 * scale), self.gpu_alpha(color("#73f9ad"), reveal), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        commands.rect(QRectF(panel.left() + 12 * scale, header.bottom() - 1, panel.width() - 24 * scale, 1), 0.5, color("#9bffca", 0.26 * reveal))

        sidebar = QRectF(panel.left() + 12 * scale, header.bottom() + 10 * scale, 54 * scale, panel.height() - header.height() - 22 * scale)
        content = QRectF(sidebar.right() + 12 * scale, sidebar.top(), panel.width() - sidebar.width() - 36 * scale, sidebar.height())
        categories = [*CATEGORIES, "Profile"]
        button_h = min(45.0 * scale, (sidebar.height() - 8 * scale) / len(categories))
        for index, category in enumerate(categories):
            item = QRectF(sidebar.left(), sidebar.top() + index * button_h, sidebar.width(), button_h - 4 * scale)
            selected = category == self.selected_category
            hover = self.gui_motion.value(f"polar.unified.hover.{category}", 1.0 if item.contains(self.hover_point) else 0.0, self.anim_duration(0.16), Easings.ease_out_cubic)
            if selected or hover > 0.01:
                commands.rect(item, 8 * scale, color("#75f7ac", (0.20 + 0.10 * hover) * reveal), color("#ccffe0", (0.24 + 0.20 * hover) * reveal), 0.8)
            commands.text(item, self.category_icon(category) if category != "Profile" else "@", self.font("poppins_bold", 10 * scale), self.gpu_alpha(color("#e9fff1") if selected else color("#b8c9bf"), reveal), Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(item, "select_category", category, tooltip=ui_text(category, chinese_enabled(self.registry)))

        modules = self.polar_view_modules(self.selected_category)
        list_w = content.width() * 0.43
        module_panel = QRectF(content.left(), content.top(), list_w, content.height())
        setting_panel = QRectF(module_panel.right() + 10 * scale, content.top(), content.width() - list_w - 10 * scale, content.height())
        self.build_gpu_polar_glass(commands, module_panel, 16 * scale, reveal)
        self.build_gpu_polar_glass(commands, setting_panel, 16 * scale, reveal)
        commands.text(module_panel.adjusted(11 * scale, 8 * scale, -11 * scale, -module_panel.height() + 27 * scale), ui_text(self.selected_category, chinese_enabled(self.registry)), self.font("poppins_bold", 10 * scale), self.gpu_alpha(color("#f4fff7"), reveal))
        list_clip = module_panel.adjusted(6 * scale, 33 * scale, -6 * scale, -6 * scale)
        row_h = 32 * scale
        max_scroll = max(0.0, len(modules) * row_h - list_clip.height())
        self.polar_unified_scroll_motion.set_bounds(max_scroll)
        scroll = self.polar_unified_scroll_motion.position
        self.polar_unified_scroll = scroll
        self.register_gui_scroll_viewport("polar_unified", "unified", list_clip, len(modules) * row_h, list_clip.height())
        y = list_clip.top() - scroll
        for index, module in enumerate(modules):
            row = QRectF(list_clip.left(), y, list_clip.width(), row_h - 4 * scale)
            self.build_gpu_polar_module_row(commands, row, module, scale, reveal, list_clip, index, select_only=True)
            y += row_h
        if max_scroll > 0.5:
            self.build_gpu_scroll_island(commands, list_clip, scroll, max_scroll, reveal, scale)

        commands.text(setting_panel.adjusted(12 * scale, 8 * scale, -60 * scale, -setting_panel.height() + 29 * scale), ui_text(self.selected_module.name, chinese_enabled(self.registry)), self.font("poppins_bold", 11 * scale), self.gpu_alpha(color("#f4fff7"), reveal))
        toggle = QRectF(setting_panel.right() - 52 * scale, setting_panel.top() + 10 * scale, 34 * scale, 15 * scale)
        self.build_gpu_toggle(commands, toggle, self.selected_module.enabled, reveal, ("module_toggle", self.selected_module.name))
        settings_clip = setting_panel.adjusted(10 * scale, 36 * scale, -10 * scale, -8 * scale)
        settings_content_h = (1 + len(self.selected_module.settings)) * 30.0 * scale
        settings_max = max(0.0, settings_content_h - settings_clip.height())
        settings_motion = self.polar_settings_scroll_motion.setdefault(self.selected_module.name, InertialScroll())
        settings_motion.set_bounds(settings_max)
        self.register_gui_scroll_viewport(
            "polar_settings",
            self.selected_module.name,
            settings_clip,
            settings_content_h,
            settings_clip.height(),
        )
        self.build_gpu_settings(commands, self.selected_module, settings_clip, scale, reveal, settings_motion.position)
        if settings_max > 0.5:
            self.build_gpu_scroll_island(commands, settings_clip, settings_motion.position, settings_max, reveal, scale)

    def build_gpu_scroll_island(
        self,
        commands: ClickGuiDrawList,
        viewport: QRectF,
        position: float,
        maximum: float,
        alpha: float,
        scale: float,
    ) -> None:
        """绘制随惯性滚动平滑移动的贴边灵动岛胶囊。"""
        if maximum <= 0.0:
            return
        track_h = max(1.0, viewport.height() - 12.0 * scale)
        content_ratio = viewport.height() / max(viewport.height() + maximum, 1.0)
        thumb_h = max(24.0 * scale, track_h * content_ratio)
        travel = max(0.0, track_h - thumb_h)
        ratio = max(0.0, min(1.0, position / maximum))
        thumb = QRectF(
            viewport.right() - 4.0 * scale,
            viewport.top() + 6.0 * scale + travel * ratio,
            3.0 * scale,
            thumb_h,
        )
        commands.rect(
            thumb.adjusted(-1.0 * scale, -1.0 * scale, 1.0 * scale, 1.0 * scale),
            2.5 * scale,
            color("#d8ffe8", 0.10 * alpha),
            clip=viewport,
        )
        commands.rect(thumb, 1.5 * scale, color("#72f7ad", 0.72 * alpha), clip=viewport)

    def build_gpu_polar_multi(self, commands: ClickGuiDrawList, width: int, height: int, alpha: float) -> None:
        scale = self.get_gui_scale_for_bounds(width, height, 980.0, 650.0, 34.0, 62.0)
        categories = [*CATEGORIES, "Profile"]
        panel_w, header_h = 210 * scale, 31 * scale
        for index, category in enumerate(categories):
            data = self.polar_windows.setdefault(category, {"x": 70.0 + (index % 4) * 228.0, "y": 94.0 + (index // 4) * 290.0, "scroll": 0.0})
            x = max(6.0, min(width - panel_w - 6.0, float(data["x"])))
            y = max(20.0, min(height - 85.0, float(data["y"])))
            data["x"], data["y"] = x, y
            modules = self.polar_view_modules(category)
            content_h = len(modules) * 28 * scale + 8 * scale
            body_h = min(300 * scale, max(78 * scale, height - y - header_h - 24 * scale), content_h)
            data["scroll"] = max(0.0, min(float(data.get("scroll", 0.0)), max(0.0, content_h - body_h)))
            scroll = self.gui_motion.value(f"polar.multi.scroll.{category}", float(data["scroll"]), self.anim_duration(0.12), Easings.ease_out_cubic, initial=float(data["scroll"]))
            panel = QRectF(x, y, panel_w, header_h + body_h)
            self.build_gpu_polar_glass(commands, panel, 16 * scale, alpha)
            header = QRectF(x, y, panel_w, header_h)
            commands.text(header.adjusted(10 * scale, 0, -10 * scale, 0), f"{self.category_icon(category) if category != 'Profile' else '@'}  {ui_text(category, chinese_enabled(self.registry))}", self.font("poppins_bold", 9 * scale), self.gpu_alpha(color("#f1fff5"), alpha))
            self.add_hotspot(header, "polar_drag", category, button=1, tooltip=ui_text("Drag category window", chinese_enabled(self.registry)))
            clip = QRectF(x + 5 * scale, y + header_h, panel_w - 10 * scale, body_h)
            self.register_gui_scroll_viewport("polar_multi", category, clip, content_h, body_h)
            yy = clip.top() - scroll
            for row_index, module in enumerate(modules):
                row = QRectF(clip.left(), yy, clip.width(), 24 * scale)
                self.build_gpu_polar_module_row(commands, row, module, scale, alpha, clip, row_index)
                yy += 28 * scale

    def build_gpu_polar_module_row(self, commands: ClickGuiDrawList, row: QRectF, module: Module, scale: float, alpha: float, clip: QRectF, row_index: int, *, select_only: bool = False) -> None:
        selected = module is self.selected_module
        hover = self.gui_motion.value(f"polar.row.{module.name}", 1.0 if row.contains(self.hover_point) else 0.0, self.anim_duration(0.14), Easings.ease_out_cubic)
        enabled = self.gui_motion.value(f"polar.enabled.{module.name}", 1.0 if module.enabled else 0.0, self.anim_duration(0.18), Easings.ease_out_cubic)
        fill = color("#81fbb3", (0.15 * enabled + 0.07 * hover + (0.06 if selected else 0.0)) * alpha)
        commands.rect(row, 5 * scale, fill, color("#effff4", (0.07 + 0.13 * hover) * alpha), 0.8, clip=clip)
        # 启用指示条贴行左缘通高：行圆角 r=5s 处弧线内收，方角条会在角部外伸 ~2.2s px，补同半径 SDF 裁剪。
        commands.rect(QRectF(row.left(), row.top(), 2 * scale, row.height()), 1 * scale, color("#72f7a9", (0.25 + 0.65 * enabled) * alpha), clip=clip, clip_radius=5 * scale)
        commands.text(row.adjusted(8 * scale, 0, -50 * scale, 0), self.elide_for_gpu(self.font("poppins_medium", 7.6 * scale), ui_text(module.name, chinese_enabled(self.registry)), row.width() - 56 * scale), self.font("poppins_medium", 7.6 * scale), self.gpu_alpha(color("#f3fff7"), alpha), clip=clip)
        key = QRectF(row.right() - 39 * scale, row.top() + 4 * scale, 33 * scale, row.height() - 8 * scale)
        commands.rect(key, 4 * scale, color("#b7ffd2", 0.10 * alpha), clip=clip)
        commands.text(key, self.compact_keybind(module.keybind), self.font("poppins_bold", 6.0 * scale), self.gpu_alpha(color("#d9ffe7"), alpha), Qt.AlignmentFlag.AlignCenter, clip)
        self._hotspot_clip = clip
        self.add_hotspot(row.adjusted(0, 0, -42 * scale, 0), "module_select" if select_only else "module_toggle", module.name, tooltip=description_label(module.description, chinese_enabled(self.registry)))
        self.add_hotspot(key, "module_bind", module.name, tooltip=ui_text("Bind key", chinese_enabled(self.registry)))
        self._hotspot_clip = None

    def build_gpu_module_row(
        self,
        commands: ClickGuiDrawList,
        row: QRectF,
        module: Module,
        scale: float,
        alpha: float,
        clip: QRectF | None,
        compact: bool = False,
        select_only: bool = False,
        style: str = "panel",
        row_index: int = 0,
    ) -> None:
        enabled = self.gui_motion.value(f"gpu.enabled.{module.name}", 1.0 if module.enabled else 0.0, self.anim_duration(0.18), Easings.ease_out_cubic)
        gradient = None
        if style == "old":
            # Polar Old：近黑色行底与偏亮的启用文字。
            fill = color("#151515", (0.78 + 0.08 * enabled) * alpha)
            outline = color("#ffffff", 0.035 * alpha)
            text_color = color("#b4f8ff") if module.enabled else color("#e5e5e8")
            radius = 0.0
        elif style == "new":
            # NewClickGui keeps rows transparent; enabled state is text/glow.
            fill = color("#202020", (0.28 + 0.12 * enabled) * alpha)
            outline = color("#ffffff", 0.025 * alpha)
            text_color = color("#2de8ca") if module.enabled else color("#ffffff", 0.60)
            radius = 0.0
        elif style == "dropdown":
            # TestClient only fills the active module with its Aura gradient.
            if module.enabled:
                fill = accent_color(row_index, time.perf_counter())
                fill.setAlphaF(0.55 * alpha * enabled)
                gradient = accent_color(row_index + 1, time.perf_counter())
                gradient.setAlphaF(0.55 * alpha * enabled)
            else:
                fill = color("#0c0c0c", 0.57 * alpha)
            outline = color("#5a5a5a", 0.24 * alpha)
            text_color = THEME.text
            radius = 0.0
        else:
            # PanelClickGui: glassy rows + high-contrast names (scan without squinting).
            fill = color("#ffffff", (0.03 + 0.07 * enabled) * alpha)
            outline = color("#ffffff", 0.05 * alpha)
            text_color = color("#ffffff") if module.enabled else color("#c6c8d0")
            radius = 5 * scale if compact else 0.0
        commands.rect(row, radius, fill, outline, 1.0, gradient=gradient, clip=clip)
        # Panel: slightly larger medium weight so labels pop on dark glass.
        if style == "panel":
            name_font = self.font("poppins_medium", 9.3 * scale)
            key_w = 30 * scale
            name_rect = row.adjusted(6 * scale, 0, -(key_w + 12 * scale), 0)
        else:
            name_font = self.font("poppins_medium", (7.6 if compact else 8.6) * scale)
            key_w = 20 * scale
            name_rect = row.adjusted(7 * scale, 0, -32 * scale, 0)
        label = self.elide_for_gpu(name_font, ui_text(module.name, chinese_enabled(self.registry)), name_rect.width())
        commands.text(name_rect, label, name_font, self.gpu_alpha(text_color, alpha), clip=clip)
        if style == "panel":
            key = QRectF(row.right() - 34 * scale, row.top() + 2 * scale, 30 * scale, max(10 * scale, row.height() - 4 * scale))
        else:
            key = QRectF(row.right() - 25 * scale, row.top() + 3 * scale, 20 * scale, max(10 * scale, row.height() - 6 * scale))
        commands.rect(key, 4 * scale, color("#ffffff", 0.07 * alpha), color("#ffffff", 0.12 * alpha), 1.0, clip=clip)
        # PanelClickGui always labels the bind chip "KEY" (see draw_panel_module_list).
        # Other GPU modes keep the compact bind / placeholder.
        if style == "panel":
            key_label = ui_text("KEY", chinese_enabled(self.registry))
        else:
            key_label = self.compact_keybind(module.keybind) if module.keybind and module.keybind != "None" else "--"
        commands.text(key, key_label, self.font("poppins_medium", 6.8 * scale), self.gui_secondary_text(alpha), Qt.AlignmentFlag.AlignCenter, clip)
        self._hotspot_clip = clip
        if style == "panel":
            self.add_hotspot(row.adjusted(0, 0, -36 * scale, 0), "module_toggle" if not select_only else "module_select", module.name, button=1, tooltip=description_label(module.description, chinese_enabled(self.registry)))
            self.add_hotspot(row.adjusted(0, 0, -36 * scale, 0), "module_expand", module.name, button=3, tooltip=ui_text("展开设置", chinese_enabled(self.registry)))
        else:
            self.add_hotspot(row.adjusted(0, 0, -25 * scale, 0), "module_toggle" if not select_only else "module_select", module.name, button=1, tooltip=description_label(module.description, chinese_enabled(self.registry)))
            self.add_hotspot(row.adjusted(0, 0, -25 * scale, 0), "module_expand", module.name, button=3, tooltip=ui_text("展开设置", chinese_enabled(self.registry)))
        self.add_hotspot(key, "module_bind", module.name, tooltip=ui_text("绑定按键", chinese_enabled(self.registry)))

        self._hotspot_clip = None

    def build_gpu_settings(self, commands: ClickGuiDrawList, module: Module, content: QRectF, scale: float, alpha: float, scroll: float) -> None:
        y = content.top() - scroll
        self._hotspot_clip = content
        bind = QRectF(content.left(), y, content.width(), 28 * scale)
        commands.rect(bind.adjusted(0, 2 * scale, 0, -3 * scale), 5 * scale, color("#ffffff", 0.05 * alpha), color("#ffffff", 0.08 * alpha), 1.0, clip=content)
        commands.text(bind.adjusted(8 * scale, 0, -112 * scale, 0), ui_text("Keybind", chinese_enabled(self.registry)), self.font("poppins_medium", 8.8 * scale), self.gui_secondary_text(alpha), clip=content)
        key = QRectF(bind.right() - 103 * scale, bind.top() + 5 * scale, 96 * scale, 18 * scale)
        commands.rect(key, 5 * scale, color("#ffffff", 0.08 * alpha), color("#ffffff", 0.14 * alpha), 1.0, clip=content)
        commands.text(key, module.keybind, self.font("poppins_medium", 8.2 * scale), self.gui_primary_text(alpha), Qt.AlignmentFlag.AlignCenter, content)
        self.add_hotspot(key, "module_bind", module.name, tooltip=ui_text("打开按键绑定", chinese_enabled(self.registry)))
        y = bind.bottom()
        for setting in module.settings:
            row = QRectF(content.left(), y, content.width(), 31 * scale)
            self.build_gpu_setting_row(commands, setting, row, scale, alpha, content)
            y = row.bottom()
        self._hotspot_clip = None

    def build_gpu_setting_row(self, commands: ClickGuiDrawList, setting: Setting, row: QRectF, scale: float, alpha: float, clip: QRectF | None) -> None:
        body = row.adjusted(0, 2 * scale, 0, -3 * scale)
        commands.rect(body, 5 * scale, color("#ffffff", 0.045 * alpha), clip=clip)
        # Setting names are the densest text — keep them bright and a bit larger.
        commands.text(
            body.adjusted(8 * scale, 0, -120 * scale, 0),
            ui_text(setting.name, chinese_enabled(self.registry)),
            self.font("poppins_medium", 8.8 * scale),
            self.gui_primary_text(alpha),
            clip=clip,
        )
        if setting.kind == "bool":
            self.build_gpu_toggle(commands, QRectF(body.right() - 39 * scale, body.top() + 6 * scale, 30 * scale, 13 * scale), bool(setting.value), alpha, ("setting_toggle", setting), clip)
        elif setting.kind == "number":
            commands.text(
                QRectF(body.right() - 78 * scale, body.top(), 70 * scale, body.height() * 0.48),
                ui_text(setting.display_value(), chinese_enabled(self.registry)),
                self.font("poppins_medium", 8.2 * scale),
                self.gui_primary_text(alpha),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                clip,
            )
            # Liquid glass slider (AndroidLiquidGlass-kmp LiquidSlider style)
            bar = QRectF(body.left() + body.width() * 0.42, body.bottom() - 9 * scale, body.width() * 0.52, 5 * scale)
            ratio = max(0.0, min(1.0, setting.ratio()))
            # Track: capsule background
            commands.rect(bar, 2.5 * scale, color("#787880", 0.36 * alpha), clip=clip)
            # Accent fill
            commands.rect(QRectF(bar.left(), bar.top(), max(bar.height(), bar.width() * ratio), bar.height()), 2.5 * scale, color("#0091ff", 0.92 * alpha), clip=clip)
            # Glass thumb at current value position
            thumb_w = 14.0 * scale
            thumb_h = 10.0 * scale
            thumb_x = bar.left() + max(0.0, bar.width() - thumb_w) * ratio
            thumb_rect = QRectF(thumb_x, bar.center().y() - thumb_h * 0.5, thumb_w, thumb_h)
            press = self.liquid_press_amount(thumb_rect.adjusted(-6, -6, 6, 6))
            if self.gpu_compositor.available and alpha > 0.3:
                self.gpu_compositor.add_glass(
                    thumb_rect,
                    thumb_rect.height() * 0.5,
                    strength=0.72 + 0.28 * press,
                    surface="clear",
                    opacity=alpha,
                    material=LensMaterial.STRONG,
                    refraction_height_px=10.0 * press,
                    refraction_amount_px=-14.0 * press,
                    chromatic_aberration=0.32 * press,
                    frost_amount=0.28 * (1.0 - press),
                )
            commands.rect(thumb_rect, thumb_rect.height() * 0.5, color("#ffffff", 0.92 * (1.0 - press * 0.85) * alpha), clip=clip)
            self.add_hotspot(bar.adjusted(-4 * scale, -7 * scale, 4 * scale, 7 * scale), "setting_slider", setting)
        elif setting.kind == "mode":
            box = QRectF(body.right() - 112 * scale, body.top() + 5 * scale, 103 * scale, body.height() - 10 * scale)
            commands.rect(box, 5 * scale, color("#303036", 0.95 * alpha), color("#ffffff", 0.12 * alpha), 1.0, clip=clip)
            commands.text(box.adjusted(5 * scale, 0, -5 * scale, 0), ui_text(str(setting.value), chinese_enabled(self.registry)), self.font("poppins_medium", 7.6 * scale), self.gui_primary_text(alpha), Qt.AlignmentFlag.AlignCenter, clip)
            self.add_hotspot(box, "setting_mode_next", setting, tooltip=ui_text("左键/右键循环切换", chinese_enabled(self.registry)))
        elif setting.kind == "action":
            button = body.adjusted(8 * scale, 4 * scale, -8 * scale, -4 * scale)
            commands.rect(button, 5 * scale, color("#ffffff", 0.94 * alpha), color("#56fff0", 0.40 * alpha), 1.0, clip=clip)
            commands.text(button, ui_text(setting.name, chinese_enabled(self.registry)), self.font("poppins_medium", 8.0 * scale), self.gpu_alpha(color("#090a0d"), alpha), Qt.AlignmentFlag.AlignCenter, clip)
            self.add_hotspot(button, "config_action", setting.name, tooltip=description_label(setting.description, chinese_enabled(self.registry)))

    def build_gpu_inline_settings(self, commands: ClickGuiDrawList, module: Module, y: float, x: float, width: float, scale: float, alpha: float, clip: QRectF, key: str, max_rows: int) -> None:
        expand = self.gui_motion.value(key, 1.0 if module.name in self.module_expanded else 0.0, self.anim_duration(0.34), Easings.ease_out_expo)
        if expand <= 0.01:
            return
        self._hotspot_clip = clip
        rows = [None, *module.settings[:max_rows]]
        for index, setting in enumerate(rows):
            reveal = self.gui_motion.waterfall(expand, index, len(rows))
            if reveal <= 0.01:
                continue
            h = self.inline_bind_height(scale, True) if setting is None else self.inline_setting_height(setting, scale, True)
            row = QRectF(x + (1 - reveal) * 7 * scale, y, width - (1 - reveal) * 7 * scale, h)
            if setting is None:
                commands.rect(row, 5 * scale, color("#ffffff", 0.035 * alpha * reveal), clip=clip)
                commands.text(row.adjusted(7 * scale, 0, -68 * scale, 0), ui_text("Keybind", chinese_enabled(self.registry)), self.font("poppins_regular", 7 * scale), self.gpu_alpha(THEME.muted, alpha * reveal), clip=clip)
                box = QRectF(row.right() - 62 * scale, row.top() + 4 * scale, 55 * scale, row.height() - 8 * scale)
                commands.rect(box, 4 * scale, color("#ffffff", 0.06 * alpha * reveal), clip=clip)
                commands.text(box, self.compact_keybind(module.keybind), self.font("poppins_medium", 6.7 * scale), self.gpu_alpha(THEME.text, alpha * reveal), Qt.AlignmentFlag.AlignCenter, clip)
                self.add_hotspot(box, "module_bind", module.name)
            else:
                self.build_gpu_setting_row(commands, setting, row, scale, alpha * reveal, clip)
            y += h
        self._hotspot_clip = None

    def build_gpu_toggle(self, commands: ClickGuiDrawList, rect: QRectF, enabled: bool, alpha: float, action: tuple[str, Any], clip: QRectF | None = None) -> None:
        """Liquid glass toggle inspired by AndroidLiquidGlass-kmp LiquidToggle.

        Track: capsule with color lerp (gray -> accent green).
        Thumb: capsule glass lens with chromatic aberration on press,
        white surface that fades out when pressed, spring-animated position.
        """
        value = self.gui_motion.value(f"gpu.toggle.{id(action[1])}", 1.0 if enabled else 0.0, self.anim_duration(0.22), Easings.ease_out_expo)
        # Track: capsule, color lerps from dark gray to accent green
        track_r = rect.height() * 0.5
        track_color = color("#30d158", 0.88 * alpha * value) if value > 0.01 else color("#787880", 0.36 * alpha)
        if 0.01 < value < 0.99:
            track_color = color("#787880", 0.36 * alpha * (1.0 - value))
            commands.rect(rect, track_r, track_color, clip=clip)
            track_color = color("#30d158", 0.88 * alpha * value)
        commands.rect(rect, track_r, track_color, clip=clip)
        # Thumb geometry: capsule that slides along the track
        pad = 2.0
        thumb_w = rect.height() - pad * 2 + 6.0  # slightly wider than tall (40:24 ratio)
        thumb_h = rect.height() - pad * 2
        drag_width = rect.width() - thumb_w - pad * 2
        thumb_x = rect.left() + pad + drag_width * value
        thumb_rect = QRectF(thumb_x, rect.top() + pad, thumb_w, thumb_h)
        # Press feedback: lens + chromatic aberration on the thumb
        press = self.liquid_press_amount(thumb_rect.adjusted(-6, -6, 6, 6))
        if self.gpu_compositor.available and alpha > 0.3:
            self.gpu_compositor.add_glass(
                thumb_rect,
                thumb_rect.height() * 0.5,
                strength=0.72 + 0.28 * press,
                surface="clear",
                opacity=alpha,
                material=LensMaterial.STRONG,
                refraction_height_px=5.0 * press,
                refraction_amount_px=-10.0 * press,
                chromatic_aberration=0.32 * press,
                frost_amount=0.28 * (1.0 - press),
            )
        # White surface overlay (fades out on press like the source)
        surface_alpha = (1.0 - press * 0.85) * alpha
        commands.rect(thumb_rect, thumb_rect.height() * 0.5, color("#ffffff", 0.92 * surface_alpha), clip=clip)
        self.add_hotspot(rect.adjusted(-4, -6, 4, 6), action[0], action[1])

    def build_gpu_mode_switcher(self, commands: ClickGuiDrawList, width: int, height: int) -> None:
        gap, margin = 6.0, 18.0
        chip_w = min(128.0, (width - margin * 2 - gap * (len(GUI_MODES) - 1)) / len(GUI_MODES))
        total, x, y = len(GUI_MODES) * chip_w + (len(GUI_MODES) - 1) * gap, max(margin, width - (len(GUI_MODES) * chip_w + (len(GUI_MODES) - 1) * gap) - margin), max(12.0, height - 38.0)
        commands.rect(QRectF(x - 8, y - 6, total + 16, 30), 8, color("#08080a", 0.82), color("#ffffff", 0.10), 1.0)
        for index, mode in enumerate(GUI_MODES):
            rect = QRectF(x + index * (chip_w + gap), y, chip_w, 18)
            active = mode == self.mode_manager.mode
            commands.rect(rect, 5, color("#ffffff", 0.92) if active else color("#1a1c22", 0.95), color("#ffffff", 0.10), 1.0)
            commands.text(rect, ui_text(self.gui_mode_label(mode), chinese_enabled(self.registry)), self.font("poppins_medium", 7.1), color("#08080a") if active else THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(rect, "gui_mode", mode)

    def build_gpu_legacy_return(
        self,
        commands: ClickGuiDrawList,
        width: int,
        height: int,
        alpha: float,
    ) -> None:
        del width, height
        rect = QRectF(18.0, 18.0, 184.0, 34.0)
        commands.rect(
            rect.adjusted(-4.0, 3.0, 4.0, 7.0),
            12.0,
            color("#000000", 0.24 * alpha),
        )
        commands.rect(
            rect,
            10.0,
            color("#fefefe", 0.96 * alpha),
            color("#d7d7d7", 0.72 * alpha),
            0.8,
        )
        commands.text(
            rect.adjusted(12.0, 0.0, -12.0, 0.0),
            ui_text("←  Back to Jello Sigma", chinese_enabled(self.registry)),
            self.font("jello_medium", 9.5),
            color("#010101", 0.72 * alpha),
            Qt.AlignmentFlag.AlignCenter,
        )
        self.add_hotspot(rect, "jello_return", tooltip=ui_text("Return to the main ClickGUI", chinese_enabled(self.registry)))

    def build_gpu_toasts(self, commands: ClickGuiDrawList, width: int, height: int) -> None:
        now = time.perf_counter()
        self.toasts = [toast for toast in self.toasts if now - toast["created"] < 3.0]
        for index, toast in enumerate(self.toasts[-5:]):
            age = now - toast["created"]
            fade = 1.0 if age < 2.35 else max(0.0, 1.0 - (age - 2.35) / 0.65)
            message = toast_text(str(toast["message"]), chinese_enabled(self.registry))
            rect = QRectF(width / 2 - 150, height - 82 - index * 42, 300, 36)
            commands.rect(rect, 10, color("#050506", 0.74 * fade), color("#ffffff", 0.08 * fade), 1.0)
            commands.text(rect.adjusted(14, 4, -14, -16), message, self.font("poppins_regular", 7.3), self.gpu_alpha(THEME.text, fade), Qt.AlignmentFlag.AlignCenter)
            commands.rect(QRectF(rect.left() + 8, rect.bottom() - 4, (rect.width() - 16) * max(0.0, 1.0 - age / 3.0), 2), 1, color("#56fff0", fade))

    def build_gpu_tooltip(self, commands: ClickGuiDrawList, point: QPointF, text: str) -> None:
        font = self.font("poppins_regular", 8.0)
        metrics = QFontMetricsF(font)
        label = self.elide_for_gpu(font, text, 340)
        width = metrics.horizontalAdvance(label) + 18
        rect = QRectF(min(point.x() + 14, self.width() - width - 10), min(point.y() + 18, self.height() - 30), width, 24)
        commands.rect(rect, 6, color("#050506", 0.92), color("#ffffff", 0.14), 1.0)
        commands.text(rect.adjusted(9, 0, -9, 0), label, font, THEME.text)

    def elide_for_gpu(self, font: QFont, text: str, width: float) -> str:
        return QFontMetricsF(font).elidedText(text, Qt.TextElideMode.ElideRight, int(width))

    def build_gpu_keybind_overlay(self, commands: ClickGuiDrawList, width: int, height: int) -> None:
        module = self.binding_module
        if module is None:
            return
        active = self.gui_motion.value("gpu.keybind", 1.0, self.anim_duration(0.18), Easings.ease_out_expo, initial=0.0)
        commands.rect(QRectF(0, 0, width, height), 0, color("#000000", 0.58 * active))
        scale = 0.96 + 0.04 * active
        rect = QRectF(width / 2 - 200 * scale, height / 2 - 90 * scale, 400 * scale, 180 * scale)
        commands.rect(rect, 11 * scale, color("#0b0b0f", 0.97 * active), color("#ffffff", 0.16 * active), 1.0)
        commands.text(QRectF(rect.left(), rect.top() + 28 * scale, rect.width(), 24 * scale), ui_text("KEYBIND", chinese_enabled(self.registry)), self.font("poppins_bold", 18 * scale), self.gpu_alpha(THEME.text, active), Qt.AlignmentFlag.AlignCenter)
        commands.text(QRectF(rect.left(), rect.top() + 67 * scale, rect.width(), 21 * scale), ui_text(module.name, chinese_enabled(self.registry)), self.font("poppins_medium", 11 * scale), self.gpu_alpha(THEME.cyan, active), Qt.AlignmentFlag.AlignCenter)
        commands.text(QRectF(rect.left(), rect.top() + 98 * scale, rect.width(), 20 * scale), ui_text("Press any key to bind", chinese_enabled(self.registry)), self.font("poppins_regular", 9 * scale), self.gpu_alpha(THEME.muted, active), Qt.AlignmentFlag.AlignCenter)

    def draw_panel_gui(self, p: QPainter, width: int, height: int, alpha: float) -> None:
        scale_setting = self.get_gui_scale_for_bounds(width, height, 600.0, 440.0, 76.0, 82.0)
        open_scale = 0.98 + 0.02 * alpha
        scale = scale_setting * open_scale
        panel_w = 600.0 * scale
        panel_h = 400.0 * scale
        x = width / 2 - panel_w / 2
        y = height / 2 - panel_h / 2
        panel = QRectF(x, y, panel_w, panel_h)
        self.draw_gui_cached_layer(
            p,
            panel.adjusted(-22 * scale, -22 * scale, 22 * scale, 22 * scale),
            ("panel.shell", round(scale, 3)),
            lambda layer, _rect: (
                glow_rect(layer, panel, 11 * scale, 0.34),
                rounded_rect(layer, panel, 8 * scale, color("#101014", 0.96), color("#ffffff", 0.13)),
            ),
        )

        self.draw_profile_widget(p, x + 14 * scale, y + 13 * scale, scale)
        self.draw_panel_category_bar(p, QRectF(x + 414 * scale, y + 18 * scale, 166 * scale, 28 * scale), scale)
        self.panel_modules_rect = QRectF(x + 12 * scale, y + 43 * scale, 160 * scale, 316 * scale)
        settings_rect = QRectF(x + 187 * scale, y + 43 * scale, 401 * scale, 316 * scale)
        self.panel_settings_rect = settings_rect
        self.draw_panel_module_list(p, self.panel_modules_rect, scale)
        self.draw_settings_area(p, settings_rect, self.selected_module, scale)

        search_y = min(height - 60.0, y + panel_h + 15.0 * scale)
        self.draw_search_bar(p, QRectF(width / 2 - 104 * scale, search_y, 208 * scale, 23 * scale), scale)

    def draw_profile_widget(self, p: QPainter, x: float, y: float, scale: float) -> None:
        self.draw_text(p, QRectF(x, y, 160 * scale, 20 * scale), "POLAR-RM", self.font("poppins_bold", 14 * scale), THEME.text)
        sub = f"配置 Default | 缩放 {self.get_gui_scale():.2f}"
        self.draw_text(p, QRectF(x, y + 20 * scale, 210 * scale, 16 * scale), sub, self.font("poppins_regular", 7.8 * scale), THEME.muted)

    def draw_panel_category_bar(self, p: QPainter, rect: QRectF, scale: float) -> None:
        step = rect.width() / len(CATEGORIES)
        selected_index = float(CATEGORIES.index(self.selected_category)) if self.selected_category in CATEGORIES else 0.0
        initial_index = selected_index
        indicator_index = self.gui_motion.value(
            "panel.category.indicator",
            selected_index,
            self.anim_duration(0.22),
            Easings.ease_out_expo,
            initial=initial_index,
        )
        indicator = QRectF(
            rect.left() + indicator_index * step + 2 * scale,
            rect.top() + 2 * scale,
            max(1.0, step - 4 * scale),
            max(1.0, rect.height() - 4 * scale),
        )
        rounded_rect(p, indicator, 9 * scale, color("#ffffff", 0.055), color("#ffffff", 0.20))
        for i, category in enumerate(CATEGORIES):
            box = QRectF(rect.left() + i * step, rect.top(), step, rect.height())
            active = category == self.selected_category
            self.draw_text(
                p,
                box,
                self.category_icon(category),
                self.font("material", 12.5 * scale, QFont.Weight.Bold),
                THEME.text if active else color("#7e7e85"),
                Qt.AlignmentFlag.AlignCenter,
            )
            self.add_hotspot(box, "select_category", category, tooltip=ui_text(category, chinese_enabled(self.registry)))

    def draw_panel_module_list(self, p: QPainter, rect: QRectF, scale: float) -> None:
        self.draw_gui_cached_layer(
            p,
            rect,
            ("panel.module-list.shell", round(scale, 3)),
            lambda layer, shell: rounded_rect(layer, shell, 4 * scale, color("#ffffff", 0.055), color("#ffffff", 0.035)),
        )
        self.draw_text(p, rect.adjusted(10 * scale, 7 * scale, -10 * scale, -rect.height() + 30 * scale), ui_text(self.selected_category, chinese_enabled(self.registry)), self.font("poppins_bold", 12.5 * scale), color("#f4f5f8"))
        modules = self.panel_modules()
        top = rect.top() + 38 * scale
        row_h = 23 * scale
        module_clip = rect.adjusted(0, 32 * scale, 0, -4 * scale)
        max_scroll = max(0.0, len(modules) * row_h - module_clip.height())
        target_scroll = max(0.0, min(max_scroll, self.panel_module_scroll.get(self.selected_category, 0.0)))
        scroll = self.gui_motion.value(
            f"panel.modules.scroll.{self.selected_category}",
            target_scroll,
            self.anim_duration(0.16),
            Easings.ease_out_cubic,
            initial=target_scroll,
        )
        top -= scroll
        selected_index = float(next((i for i, module in enumerate(modules) if module is self.selected_module), 0))
        indicator_index = self.gui_motion.value(
            "panel.module.selection",
            selected_index,
            self.anim_duration(0.18),
            Easings.ease_out_expo,
            initial=selected_index,
        )
        p.save()
        p.setClipRect(module_clip)
        if modules:
            selection = QRectF(rect.left() + 6 * scale, top + indicator_index * row_h, rect.width() - 12 * scale, 20 * scale)
            rounded_rect(p, selection, 5 * scale, color("#ffffff", 0.08), color("#ffffff", 0.06))
        self._hotspot_clip = module_clip
        for i, module in enumerate(modules):
            row = QRectF(rect.left() + 6 * scale, top + i * row_h, rect.width() - 12 * scale, 20 * scale)
            enabled_v = self.gui_motion.value(f"panel.enabled.{module.name}", 1.0 if module.enabled else 0.0, self.anim_duration(0.22), Easings.ease_out_cubic)
            if enabled_v > 0.01:
                rounded_rect(p, row, 5 * scale, color("#ffffff", 0.05 * enabled_v))
            label_color = color("#ffffff") if module.enabled else color("#c6c8d0")
            label_font = self.font("poppins_medium", 9.3 * scale)
            label = self.elide(p, label_font, ui_text(module.name, chinese_enabled(self.registry)), row.width() - 42 * scale)
            if enabled_v > 0.01:
                glow_text(p, label, row.left() + 6 * scale, cap_center_y(p, label_font, row), label_font, label_color, accent_color(i), 3 * enabled_v)
            else:
                self.draw_text(p, row.adjusted(6 * scale, 0, -42 * scale, 0), label, label_font, label_color)
            key_box = QRectF(row.right() - 34 * scale, row.top() + 2 * scale, 30 * scale, row.height() - 4 * scale)
            rounded_rect(p, key_box, 4 * scale, color("#ffffff", 0.06), color("#ffffff", 0.10))
            self.draw_text(p, key_box, ui_text("KEY", chinese_enabled(self.registry)), self.font("poppins_medium", 6.8 * scale), color("#d0d2da"), Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(row.adjusted(0, 0, -36 * scale, 0), "module_select", module.name, button=1, tooltip=description_label(module.description, chinese_enabled(self.registry)))
            self.add_hotspot(row.adjusted(0, 0, -36 * scale, 0), "module_bind", module.name, button=3, tooltip=ui_text("绑定按键", chinese_enabled(self.registry)))
            self.add_hotspot(key_box, "module_bind", module.name, tooltip=ui_text("绑定按键", chinese_enabled(self.registry)))
        p.restore()

    def draw_settings_area(self, p: QPainter, rect: QRectF, module: Module, scale: float) -> None:
        self._hotspot_clip = None
        self.draw_gui_cached_layer(
            p,
            rect,
            ("panel.settings.shell", round(scale, 3)),
            lambda layer, shell: rounded_rect(layer, shell, 4 * scale, color("#1a1a20", 0.92), color("#ffffff", 0.08)),
        )
        header = QRectF(rect.left() + 12 * scale, rect.top() + 9 * scale, rect.width() - 24 * scale, 29 * scale)
        self.draw_text(p, header.adjusted(0, 0, -72 * scale, 0), ui_text(module.name, chinese_enabled(self.registry)), self.font("poppins_bold", 13.8 * scale), color("#f4f5f8"))
        self.draw_toggle_switch(p, QRectF(rect.right() - 57 * scale, rect.top() + 13 * scale, 36 * scale, 16 * scale), module.enabled, ("module_toggle", module.name))

        content = rect.adjusted(10 * scale, 43 * scale, -10 * scale, -10 * scale)
        p.save()
        p.setClipRect(content)
        scroll = self.gui_motion.value(
            f"panel.settings-scroll.{module.name}",
            self.settings_scroll.get(module.name, 0.0),
            self.anim_duration(0.12),
            Easings.ease_out_cubic,
            initial=self.settings_scroll.get(module.name, 0.0),
        )
        y = content.top() - scroll
        y = self.draw_bind_row(p, QRectF(content.left(), y, content.width(), 28 * scale), module, scale, content)
        for setting in module.settings:
            y = self.draw_setting_row(p, setting, QRectF(content.left(), y, content.width(), 31 * scale), scale)
        if not module.settings:
            self.draw_text(p, QRectF(content.left(), y, content.width(), 44 * scale), description_label(module.description, chinese_enabled(self.registry)) if module.description else ui_text("No settings", chinese_enabled(self.registry)), self.font("poppins_medium", 8.8 * scale), color("#d0d2da"))
        p.restore()

    def draw_bind_row(self, p: QPainter, rect: QRectF, module: Module, scale: float, clip_rect: QRectF | None = None, compact: bool = False) -> float:
        visible = clip_rect is None or self.rect_visible(rect, clip_rect)
        if visible:
            content_rect = rect.adjusted(0, 2 * scale, 0, -3 * scale)
            rounded_rect(p, content_rect, 5 * scale, color("#ffffff", 0.04 if compact else 0.05), color("#ffffff", 0.08))
            if compact or rect.width() < 160 * scale:
                box_w = min(74.0 * scale, max(48.0 * scale, content_rect.width() * 0.52))
                box = QRectF(content_rect.right() - box_w - 6 * scale, content_rect.top() + 4 * scale, box_w, max(13.0 * scale, content_rect.height() - 8 * scale))
                label_rect = QRectF(content_rect.left() + 7 * scale, content_rect.top(), max(18.0 * scale, box.left() - content_rect.left() - 11 * scale), content_rect.height())
                self.draw_text(p, label_rect, ui_text("Keybind", chinese_enabled(self.registry)), self.font("poppins_medium", 7.6 * scale), color("#d0d2da"))
                key_text = self.compact_keybind(module.keybind)
            else:
                self.draw_text(p, rect.adjusted(8 * scale, 0, -112 * scale, 0), ui_text("Keybind", chinese_enabled(self.registry)), self.font("poppins_medium", 8.8 * scale), color("#d0d2da"))
                box = QRectF(rect.right() - 103 * scale, rect.top() + 5 * scale, 96 * scale, 18 * scale)
                key_text = module.keybind
            rounded_rect(p, box, 5 * scale, color("#ffffff", 0.07), color("#ffffff", 0.14))
            self.draw_text(p, box, key_text, self.font("poppins_medium", (7.4 if compact else 8.2) * scale), color("#f4f5f8"), Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(box, "module_bind", module.name, tooltip=ui_text("打开按键绑定覆盖层", chinese_enabled(self.registry)))
        return rect.bottom()

    def draw_setting_row(self, p: QPainter, setting: Setting, rect: QRectF, scale: float, compact: bool = False) -> float:
        if compact:
            return self.draw_compact_setting_row(p, setting, rect, scale)
        row_h = rect.height()
        content_rect = rect.adjusted(0, 2 * scale, 0, -3 * scale)
        rounded_rect(p, content_rect, 5 * scale, color("#ffffff", 0.04 if not compact else 0.03))
        label_font = self.font("poppins_medium", (8.8 if not compact else 7.8) * scale)
        value_font = self.font("poppins_medium", (8.2 if not compact else 7.4) * scale)
        self.draw_text(p, content_rect.adjusted(8 * scale, 0, -120 * scale, 0), ui_text(setting.name, chinese_enabled(self.registry)), label_font, color("#f4f5f8"))
        if setting.kind == "bool":
            self.draw_toggle_switch(p, QRectF(content_rect.right() - 39 * scale, content_rect.top() + 6 * scale, 30 * scale, 13 * scale), bool(setting.value), ("setting_toggle", setting))
        elif setting.kind == "number":
            value = setting.display_value()
            self.draw_text(p, QRectF(content_rect.right() - 78 * scale, content_rect.top(), 70 * scale, content_rect.height() * 0.48), value, value_font, color("#f4f5f8"), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            bar = QRectF(content_rect.left() + content_rect.width() * 0.42, content_rect.bottom() - 9 * scale, content_rect.width() * 0.52, 5 * scale)
            draw_bar(p, bar, setting.ratio(), THEME.pink, color("#ffffff", 0.22), 2.5 * scale)
            self.add_hotspot(bar.adjusted(-4 * scale, -7 * scale, 4 * scale, 7 * scale), "setting_slider", setting)
        elif setting.kind == "mode":
            box = QRectF(content_rect.right() - 112 * scale, content_rect.top() + 5 * scale, 103 * scale, content_rect.height() - 10 * scale)
            rounded_rect(p, box, 5 * scale, color("#303036", 0.95), color("#ffffff", 0.12))
            self.draw_text(p, box.adjusted(6 * scale, 0, -6 * scale, 0), ui_text(str(setting.value), chinese_enabled(self.registry)), value_font, color("#f4f5f8"), Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(box, "setting_mode_next", setting, tooltip=ui_text("左键/右键循环切换", chinese_enabled(self.registry)))
        elif setting.kind == "action":
            button = content_rect.adjusted(8 * scale, 4 * scale, -8 * scale, -4 * scale)
            rounded_rect(p, button, 5 * scale, color("#ffffff", 0.92), color("#56fff0", 0.35))
            self.draw_text(p, button, ui_text(setting.name, chinese_enabled(self.registry)), value_font, color("#090a0d"), Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(button, "config_action", setting.name, tooltip=description_label(setting.description, chinese_enabled(self.registry)))
        return rect.bottom()

    def draw_compact_setting_row(self, p: QPainter, setting: Setting, rect: QRectF, scale: float) -> float:
        content_rect = rect.adjusted(0, 1.5 * scale, 0, -2.0 * scale)
        if content_rect.height() < 5 * scale:
            return rect.bottom()
        rounded_rect(p, content_rect, 5 * scale, color("#ffffff", 0.022), color("#ffffff", 0.040))
        label_font = self.font("poppins_regular", 7.0 * scale)
        value_font = self.font("poppins_medium", 6.9 * scale)

        if setting.kind == "bool":
            switch = QRectF(content_rect.right() - 37 * scale, content_rect.top() + 5 * scale, 29 * scale, 13 * scale)
            label_rect = QRectF(content_rect.left() + 7 * scale, content_rect.top(), max(16.0 * scale, switch.left() - content_rect.left() - 11 * scale), content_rect.height())
            self.draw_text(p, label_rect, ui_text(setting.name, chinese_enabled(self.registry)), label_font, color("#d7d7dc"))
            self.draw_toggle_switch(p, switch, bool(setting.value), ("setting_toggle", setting))
        elif setting.kind == "number":
            value = setting.display_value()
            bar = QRectF(content_rect.left() + 7 * scale, content_rect.bottom() - 6 * scale, content_rect.width() - 14 * scale, 4.2 * scale)
            value_w = min(58.0 * scale, max(36.0 * scale, content_rect.width() * 0.36))
            label_rect = QRectF(content_rect.left() + 7 * scale, content_rect.top() - 1 * scale, max(20.0 * scale, content_rect.width() - value_w - 18 * scale), content_rect.height() * 0.58)
            value_rect = QRectF(content_rect.right() - value_w - 7 * scale, content_rect.top() - 1 * scale, value_w, content_rect.height() * 0.58)
            self.draw_text(p, label_rect, ui_text(setting.name, chinese_enabled(self.registry)), label_font, color("#d7d7dc"))
            self.draw_text(p, value_rect, value, value_font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            draw_bar(p, bar, setting.ratio(), THEME.pink, color("#ffffff", 0.17), 2.1 * scale)
            self.add_hotspot(bar.adjusted(-5 * scale, -8 * scale, 5 * scale, 8 * scale), "setting_slider", setting)
        elif setting.kind == "mode":
            box_w = min(78.0 * scale, max(50.0 * scale, content_rect.width() * 0.55))
            box = QRectF(content_rect.right() - box_w - 6 * scale, content_rect.top() + 4 * scale, box_w, max(13.0 * scale, content_rect.height() - 8 * scale))
            label_rect = QRectF(content_rect.left() + 7 * scale, content_rect.top(), max(18.0 * scale, box.left() - content_rect.left() - 11 * scale), content_rect.height())
            self.draw_text(p, label_rect, ui_text(setting.name, chinese_enabled(self.registry)), label_font, color("#d7d7dc"))
            rounded_rect(p, box, 5 * scale, color("#303036", 0.90), color("#ffffff", 0.08))
            self.draw_text(p, box.adjusted(4 * scale, 0, -4 * scale, 0), ui_text(str(setting.value), chinese_enabled(self.registry)), value_font, THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(box, "setting_mode_next", setting, tooltip=ui_text("左键/右键循环切换", chinese_enabled(self.registry)))
        elif setting.kind == "action":
            button = content_rect.adjusted(6 * scale, 4 * scale, -6 * scale, -4 * scale)
            rounded_rect(p, button, 5 * scale, color("#ffffff", 0.90), color("#56fff0", 0.28))
            self.draw_text(p, button, ui_text(setting.name, chinese_enabled(self.registry)), value_font, color("#090a0d"), Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(button, "config_action", setting.name, tooltip=description_label(setting.description, chinese_enabled(self.registry)))
        return rect.bottom()

    def draw_search_bar(self, p: QPainter, rect: QRectF, scale: float) -> None:
        focus_v = self.anim.value("panel.search.focus", 1.0 if self.search_focus else 0.0, self.anim_duration(0.18), Easings.ease_out_quad)
        rounded_rect(p, rect, rect.height() / 2, color("#16161b", 0.92 + 0.06 * focus_v), color("#ffffff", 0.12 + 0.18 * focus_v))
        text = self.search_query if self.search_query else ui_text("Search modules...", chinese_enabled(self.registry))
        self.draw_text(p, rect.adjusted(12 * scale, 0, -12 * scale, 0), text, self.font("poppins_regular", 7.8 * scale), THEME.text if self.search_query else color("#77777d"))
        self.add_hotspot(rect, "search_focus", None)

    def draw_old_gui(self, p: QPainter, width: int, height: int, _alpha: float) -> None:
        scale = self.get_gui_scale_for_bounds(width, height, 6 * 140.0 + 5 * 20.0, 520.0, 20.0, 54.0)
        panel_w = 140.0 * scale
        row_h = 20.0 * scale
        for i, category in enumerate(CATEGORIES):
            panel_data = self.old_panels[category]
            if not bool(panel_data.get("dragged", False)):
                panel_data["x"] = 20.0 * scale + i * 160.0 * scale
                panel_data["y"] = 20.0 * scale
            x = panel_data["x"]
            y = panel_data["y"]
            header = QRectF(x, y, panel_w, row_h)
            self.draw_gui_cached_layer(
                p,
                header.adjusted(-12 * scale, -12 * scale, 12 * scale, 12 * scale),
                ("old.header", category, round(scale, 3)),
                lambda layer, _shell: (
                    glow_rect(layer, header, 4 * scale, 0.12),
                    rounded_rect(layer, header, 3 * scale, color("#101014", 0.94), color("#ffffff", 0.12)),
                    self.draw_text(layer, header.adjusted(8 * scale, 0, -8 * scale, 0), f"{self.category_icon(category)}  {ui_text(category, chinese_enabled(self.registry))}", self.font("poppins_bold", 8.2 * scale), THEME.text),
                ),
            )
            self.add_hotspot(header, "old_drag", category, button=1, tooltip=ui_text("拖动面板", chinese_enabled(self.registry)))
            self.add_hotspot(header, "old_expand", category, button=3, tooltip=ui_text("展开分类", chinese_enabled(self.registry)))

            expanded = bool(panel_data["expanded"])
            open_v = self.gui_motion.value(f"old.panel.{category}", 1.0 if expanded else 0.0, self.anim_duration(0.34), Easings.ease_out_expo)
            if open_v <= 0.01:
                continue
            yy = y + row_h
            p.save()
            modules = self.registry.by_category(category)
            total_height = sum(self.module_draw_height(module, scale, compact=True) for module in modules)
            p.setClipRect(QRectF(x, yy, panel_w, total_height * open_v).adjusted(-2 * scale, 0, 2 * scale, 2 * scale))
            for idx, module in enumerate(modules):
                row_reveal = self.gui_motion.waterfall(open_v, idx, len(modules))
                if row_reveal <= 0.025:
                    yy += self.module_draw_height(module, scale, compact=True)
                    continue
                slot_h = self.module_draw_height(module, scale, compact=True)
                row_clip = QRectF(x, yy, panel_w, row_h * row_reveal)
                row = QRectF(x + (1.0 - row_reveal) * 5.0 * scale, yy, panel_w - (1.0 - row_reveal) * 5.0 * scale, row_h)
                p.save()
                p.setClipRect(row_clip.adjusted(-2 * scale, -2 * scale, 2 * scale, 2 * scale))
                p.setOpacity(p.opacity() * row_reveal)
                enabled_v = self.anim.value(f"old.enabled.{module.name}", 1.0 if module.enabled else 0.0, self.anim_duration(0.18), Easings.ease_out_cubic)
                # Match Polar Old's row treatment: enabled modules gain
                # a cool text tint, rather than being inverted into white bars.
                fill = color("#151515", 0.78 + 0.08 * enabled_v)
                rounded_rect(p, row, 0, fill, color("#ffffff", 0.035))
                fg = color("#b4f8ff") if module.enabled else color("#e5e5e8")
                self.draw_text(p, row.adjusted(8 * scale, 0, -32 * scale, 0), ui_text(module.name, chinese_enabled(self.registry)), self.font("poppins_regular", 7.4 * scale), fg)
                key_box = QRectF(row.right() - 28 * scale, row.top() + 3 * scale, 23 * scale, row.height() - 6 * scale)
                self.draw_text(p, key_box, self.compact_keybind(module.keybind) if module.keybind and module.keybind != "None" else "--", self.font("poppins_bold", 6.5 * scale), THEME.muted, Qt.AlignmentFlag.AlignCenter)
                self.add_hotspot(row.adjusted(0, 0, -28 * scale, 0), "module_toggle", module.name, button=1, tooltip=description_label(module.description, chinese_enabled(self.registry)))
                self.add_hotspot(row.adjusted(0, 0, -28 * scale, 0), "module_expand", module.name, button=3, tooltip=ui_text("展开设置", chinese_enabled(self.registry)))
                self.add_hotspot(key_box, "module_bind", module.name, tooltip=ui_text("绑定按键", chinese_enabled(self.registry)))
                p.restore()
                exp = self.gui_motion.value(f"old.expand.{module.name}", 1.0 if module.name in self.module_expanded else 0.0, self.anim_duration(0.36), Easings.ease_out_expo)
                if exp > 0.01:
                    self.draw_inline_settings(p, module, QRectF(x + 5 * scale, row.bottom(), panel_w - 10 * scale, 0), scale, compact=True, max_rows=6, reveal=min(exp, row_reveal))
                yy += slot_h
            p.restore()

    def draw_new_gui(self, p: QPainter, width: int, height: int, _alpha: float) -> None:
        base_w = len(CATEGORIES) * 120.0 + (len(CATEGORIES) - 1) * 8.0
        scale = self.get_gui_scale_for_bounds(width, height, base_w, 430.0, 56.0, 118.0)
        gap = 8.0 * scale
        panel_w = 120.0 * scale
        total = len(CATEGORIES) * panel_w + (len(CATEGORIES) - 1) * gap
        start_x = width / 2 - total / 2
        y = 36.0 * scale if height >= 420.0 * scale else max(12.0 * scale, height * 0.08)
        max_h = min(430.0 * scale, max(180.0 * scale, height - y - 32.0 * scale))
        for i, category in enumerate(CATEGORIES):
            x = start_x + i * (panel_w + gap)
            enter = self.gui_motion.value(
                f"new.panel.{category}",
                1.0,
                self.anim_duration(0.30 + i * 0.035),
                Easings.ease_out_expo,
                initial=0.0,
            )
            modules = self.registry.by_category(category)
            content_h = 25 * scale + sum(self.module_draw_height(module, scale, compact=True) for module in modules)
            panel_h = min(max_h, max(72.0 * scale, content_h + 8 * scale))
            rect = QRectF(x, y + (1.0 - enter) * 9.0 * scale, panel_w, panel_h)
            p.save()
            p.setOpacity(p.opacity() * enter)
            self.draw_gui_cached_layer(
                p,
                rect.adjusted(-16 * scale, -16 * scale, 16 * scale, 16 * scale),
                ("new.panel.shell", category, round(scale, 3), round(panel_h, 2)),
                lambda layer, _shell: self.draw_new_panel_shell(layer, rect, category, scale),
            )
            p.restore()
            self.add_hotspot(rect.adjusted(0, 0, 0, -rect.height() + 24 * scale), "select_category", category, tooltip=ui_text(category, chinese_enabled(self.registry)))
            p.save()
            clip = rect.adjusted(0, 26 * scale, 0, -4 * scale)
            p.setClipRect(clip)
            scroll = self.gui_motion.value(
                f"new.scroll.{category}",
                float(self.new_panels[category]["scroll"]),
                self.anim_duration(0.12),
                Easings.ease_out_cubic,
                initial=float(self.new_panels[category]["scroll"]),
            )
            yy = rect.top() + 27 * scale - scroll
            for module in modules:
                yy = self.draw_new_module_block(p, module, QRectF(rect.left(), yy, rect.width(), 0), scale)
            p.restore()

    def draw_new_panel_shell(self, p: QPainter, rect: QRectF, category: str, scale: float) -> None:
        glow_rect(p, rect, 8 * scale, 0.16)
        rounded_rect(p, rect, 7 * scale, color("#171717", 0.96), color("#ffffff", 0.08))
        top_line = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
        top_line.setColorAt(0.0, color("#2de8ca"))
        top_line.setColorAt(1.0, color("#166f62"))
        p.fillRect(QRectF(rect.left() + 3 * scale, rect.top() + 1 * scale, rect.width() - 6 * scale, 2 * scale), top_line)
        self.draw_text(p, QRectF(rect.left() + 8 * scale, rect.top() + 3 * scale, rect.width() - 16 * scale, 22 * scale), ui_text(category, chinese_enabled(self.registry)), self.font("poppins_bold", 8.0 * scale), THEME.text)

    def draw_new_module_block(self, p: QPainter, module: Module, origin: QRectF, scale: float) -> float:
        row = QRectF(origin.left() + 4 * scale, origin.top(), origin.width() - 8 * scale, 21 * scale)
        enabled_v = self.gui_motion.value(f"new.enabled.{module.name}", 1.0 if module.enabled else 0.0, self.anim_duration(0.18), Easings.ease_out_cubic)
        fill = color("#202020", 0.28 + 0.12 * enabled_v)
        rounded_rect(p, row, 0, fill, color("#ffffff", 0.025))
        self.draw_text(p, row.adjusted(6 * scale, 0, -28 * scale, 0), ui_text(module.name, chinese_enabled(self.registry)), self.font("poppins_regular", 7.2 * scale), color("#2de8ca") if module.enabled else color("#ffffff", 0.60))
        key_box = QRectF(row.right() - 24 * scale, row.top() + 3 * scale, 19 * scale, row.height() - 6 * scale)
        rounded_rect(p, key_box, 4 * scale, color("#ffffff", 0.04), color("#ffffff", 0.06))
        self.draw_text(p, key_box, self.compact_keybind(module.keybind) if module.keybind and module.keybind != "None" else "--", self.font("poppins_bold", 6.2 * scale), THEME.muted, Qt.AlignmentFlag.AlignCenter)
        self.add_hotspot(row.adjusted(0, 0, -24 * scale, 0), "module_toggle", module.name, button=1, tooltip=description_label(module.description, chinese_enabled(self.registry)))
        self.add_hotspot(row.adjusted(0, 0, -24 * scale, 0), "module_expand", module.name, button=3, tooltip=ui_text("展开设置", chinese_enabled(self.registry)))
        self.add_hotspot(key_box, "module_bind", module.name, tooltip=ui_text("绑定按键", chinese_enabled(self.registry)))
        yy = row.bottom()
        exp = self.gui_motion.value(f"new.expand.{module.name}", 1.0 if module.name in self.module_expanded else 0.0, self.anim_duration(0.34), Easings.ease_out_expo)
        if exp > 0.01:
            yy = self.draw_inline_settings(p, module, QRectF(row.left() + 4 * scale, yy + 2 * scale, row.width() - 8 * scale, 0), scale, compact=True, max_rows=5, reveal=exp)
        return yy + 2 * scale

    def draw_dropdown_gui(self, p: QPainter, width: int, height: int, _alpha: float) -> None:
        scale = self.get_gui_scale_for_bounds(width, height, 6 * 120.0 + 5 * 10.0 + 20.0, 470.0, 10.0, 38.0)
        gap = 10.0 * scale
        panel_w = 120.0 * scale
        x0 = 10.0 * scale
        y0 = 10.0 * scale
        for i, category in enumerate(CATEGORIES):
            x = x0 + i * (panel_w + gap)
            self.draw_dropdown_panel(p, category, QRectF(x, y0, panel_w, 0), scale)

    def draw_dropdown_panel(self, p: QPainter, category: str, origin: QRectF, scale: float) -> None:
        modules = self.registry.by_category(category)
        expanded = bool(self.dropdown_panels[category]["expanded"])
        open_v = self.gui_motion.value(f"dropdown.panel.{category}", 1.0 if expanded else 0.0, self.anim_duration(0.26), Easings.ease_out_expo)
        header_h = 28.0 * scale
        full_body_h = min(430.0 * scale, sum(self.module_draw_height(module, scale, compact=False) for module in modules) + 8 * scale)
        body_h = open_v * full_body_h
        rect = QRectF(origin.left(), origin.top(), origin.width(), header_h + body_h)
        shell = QRectF(origin.left(), origin.top(), origin.width(), header_h + full_body_h)
        p.save()
        p.setClipRect(rect.adjusted(-18 * scale, -18 * scale, 18 * scale, 18 * scale))
        self.draw_gui_cached_layer(
            p,
            shell.adjusted(-16 * scale, -16 * scale, 16 * scale, 16 * scale),
            ("dropdown.shell", category, round(scale, 3), round(full_body_h, 2)),
            lambda layer, _shell: (
                glow_rect(layer, shell, 8 * scale, 0.13),
                rounded_rect(layer, shell, 5 * scale, color("#0c0c0c", 0.57), color("#ffffff", 0.07)),
                rounded_rect(layer, QRectF(shell.left(), shell.top(), shell.width(), header_h), 5 * scale, color("#2a2a2a", 0.76)),
                self.draw_text(layer, QRectF(shell.left() + 12 * scale, shell.top(), shell.width() - 24 * scale, header_h), f"{self.category_icon(category)}  {ui_text(category, chinese_enabled(self.registry))}", self.font("poppins_bold", 11.0 * scale), THEME.text),
            ),
        )
        p.restore()
        self.add_hotspot(QRectF(rect.left(), rect.top(), rect.width(), header_h), "dropdown_expand", category, button=3, tooltip=ui_text("展开分类", chinese_enabled(self.registry)))
        if open_v <= 0.01:
            return
        p.save()
        p.setClipRect(QRectF(shell.left(), shell.top() + header_h, shell.width(), body_h))
        yy = rect.top() + header_h
        for idx, module in enumerate(modules):
            row_reveal = self.gui_motion.waterfall(open_v, idx, len(modules))
            if row_reveal <= 0.025:
                yy += self.module_draw_height(module, scale, compact=False)
                continue
            slot_h = self.module_draw_height(module, scale, compact=False)
            row_clip = QRectF(rect.left(), yy, rect.width(), 24 * scale * row_reveal)
            row = QRectF(rect.left() + (1.0 - row_reveal) * 6.0 * scale, yy, rect.width() - (1.0 - row_reveal) * 6.0 * scale, 24 * scale)
            p.save()
            p.setClipRect(row_clip.adjusted(0, -1 * scale, 0, 2 * scale))
            p.setOpacity(p.opacity() * row_reveal)
            if module.enabled:
                grad = QLinearGradient(row.left(), row.top(), row.right(), row.top())
                grad.setColorAt(0.0, accent_color(idx, time.perf_counter()))
                grad.setColorAt(1.0, accent_color(idx + 4, time.perf_counter()))
                p.fillRect(row, grad)
            else:
                rounded_rect(p, row, 0, color("#101412", 0.98), color("#ffffff", 0.06))
            self.draw_text(p, row.adjusted(12 * scale, 0, -34 * scale, 0), ui_text(module.name, chinese_enabled(self.registry)), self.font("poppins_medium", 8.3 * scale), THEME.text)
            key_box = QRectF(row.right() - 29 * scale, row.top() + 4 * scale, 23 * scale, row.height() - 8 * scale)
            self.draw_text(p, key_box, self.compact_keybind(module.keybind) if module.keybind and module.keybind != "None" else "--", self.font("poppins_bold", 6.8 * scale), THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(row.adjusted(0, 0, -31 * scale, 0), "module_toggle", module.name, button=1, tooltip=description_label(module.description, chinese_enabled(self.registry)))
            self.add_hotspot(row.adjusted(0, 0, -31 * scale, 0), "module_expand", module.name, button=3, tooltip=ui_text("展开设置", chinese_enabled(self.registry)))
            self.add_hotspot(key_box, "module_bind", module.name, tooltip=ui_text("绑定按键", chinese_enabled(self.registry)))
            p.restore()
            exp = self.gui_motion.value(f"dropdown.expand.{module.name}", 1.0 if module.name in self.module_expanded else 0.0, self.anim_duration(0.34), Easings.ease_out_expo)
            if exp > 0.01:
                self.draw_inline_settings(p, module, QRectF(row.left() + 9 * scale, row.bottom(), row.width() - 18 * scale, 0), scale, compact=False, max_rows=6, reveal=min(exp, row_reveal))
            yy += slot_h
        p.restore()

    def draw_inline_settings(self, p: QPainter, module: Module, origin: QRectF, scale: float, compact: bool, max_rows: int, reveal: float = 1.0) -> float:
        yy = origin.top()
        rows: list[tuple[str, Setting | None, float]] = [("bind", None, self.inline_bind_height(scale, compact))]
        rows.extend(("setting", setting, self.inline_setting_height(setting, scale, compact)) for setting in module.settings[:max_rows])
        for index, (kind, setting, row_h) in enumerate(rows):
            row_v = self.waterfall_reveal(reveal, index, len(rows))
            if row_v <= 0.025:
                continue
            clip_h = row_h * row_v
            slide = (1.0 - row_v) * (7.0 if compact else 9.0) * scale
            row_rect = QRectF(origin.left() + slide, yy, origin.width() - slide, row_h)
            clip = QRectF(origin.left(), yy, origin.width(), clip_h)
            p.save()
            p.setClipRect(clip.adjusted(-2 * scale, -2 * scale, 2 * scale, 3 * scale))
            p.setOpacity(p.opacity() * row_v)
            if kind == "bind":
                self.draw_bind_row(p, row_rect, module, scale, compact=compact)
            elif setting is not None:
                self.draw_setting_row(p, setting, row_rect, scale, compact=compact)
            p.restore()
            yy += clip_h
        return yy

    def module_draw_height(self, module: Module, scale: float, compact: bool) -> float:
        row_h = 21.0 * scale if compact else 24.0 * scale
        exp = self.gui_motion.value(f"height.expand.{module.name}", 1.0 if module.name in self.module_expanded else 0.0, self.anim_duration(0.36), Easings.ease_out_expo)
        settings_total = self.inline_bind_height(scale, compact) + sum(self.inline_setting_height(setting, scale, compact) for setting in module.settings[:6])
        return row_h + exp * settings_total + 2 * scale

    def inline_bind_height(self, scale: float, compact: bool) -> float:
        return (21.0 if compact else 24.0) * scale

    def inline_setting_height(self, setting: Setting, scale: float, compact: bool) -> float:
        if not compact:
            return 24.0 * scale
        if setting.kind == "number":
            return 26.0 * scale
        if setting.kind == "mode":
            return 23.0 * scale
        return 22.0 * scale

    def waterfall_reveal(self, reveal: float, index: int, total: int) -> float:
        return self.gui_motion.waterfall(reveal, index, total)

    def compact_keybind(self, keybind: str) -> str:
        aliases = {
            "RightShift": "RShift",
            "LeftShift": "LShift",
            "Control": "Ctrl",
            "Escape": "Esc",
        }
        return aliases.get(keybind, keybind)

    def draw_mode_switcher(self, p: QPainter, width: int, height: int) -> None:
        gap = 6.0
        margin = 18.0
        chip_w = min(128.0, (width - margin * 2 - gap * (len(GUI_MODES) - 1)) / len(GUI_MODES))
        total = len(GUI_MODES) * chip_w + (len(GUI_MODES) - 1) * gap
        x = max(margin, width - total - margin)
        y = max(12.0, height - 38.0)
        bg = QRectF(x - 8, y - 6, total + 16, 30)
        rounded_rect(p, bg, 8, color("#08080a", 0.82), color("#ffffff", 0.10))
        for i, mode in enumerate(GUI_MODES):
            rect = QRectF(x + i * (chip_w + gap), y, chip_w, 18)
            active = mode == self.mode_manager.mode
            active_v = self.anim.value(f"gui.mode.{mode}", 1.0 if active else 0.0, self.anim_duration(0.16), Easings.ease_out_quad)
            fill = color("#ffffff", 0.92 * active_v) if active_v > 0.01 else color("#1a1c22", 0.95)
            rounded_rect(p, rect, 5, fill, color("#ffffff", 0.10))
            text = ui_text(self.gui_mode_label(mode), chinese_enabled(self.registry))
            self.draw_text(p, rect.adjusted(5, 0, -5, 0), text, self.font("poppins_medium", 7.1), color("#08080a") if active else THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(rect, "gui_mode", mode)

    def draw_legacy_return(self, p: QPainter, width: int, height: int, alpha: float) -> None:
        del width, height
        rect = QRectF(18.0, 18.0, 184.0, 34.0)
        rounded_rect(
            p,
            rect.adjusted(-4.0, 3.0, 4.0, 7.0),
            12.0,
            color("#000000", 0.24 * alpha),
        )
        rounded_rect(
            p,
            rect,
            10.0,
            color("#fefefe", 0.96 * alpha),
            color("#d7d7d7", 0.72 * alpha),
            0.8,
        )
        self.draw_text(
            p,
            rect.adjusted(12.0, 0.0, -12.0, 0.0),
            ui_text("←  Back to Jello Sigma", chinese_enabled(self.registry)),
            self.font("jello_medium", 9.5),
            color("#010101", 0.72 * alpha),
            Qt.AlignmentFlag.AlignCenter,
        )
        self.add_hotspot(rect, "jello_return", tooltip=ui_text("Return to the main ClickGUI", chinese_enabled(self.registry)))

    def draw_toasts(self, p: QPainter, width: int, _height: int) -> None:
        now = time.perf_counter()
        self.toasts = [toast for toast in self.toasts if now - toast["created"] < 3.0]
        for i, toast in enumerate(self.toasts[-5:]):
            age = now - toast["created"]
            fade = 1.0 if age < 2.35 else max(0.0, 1.0 - (age - 2.35) / 0.65)
            slide = Easings.ease_out_expo(min(1.0, age / self.anim_duration(0.22)))
            msg = toast["message"]
            kind, main = self.dynamic_message_kind(msg)
            title = ui_text(self.dynamic_message_title(msg, kind), chinese_enabled(self.registry))
            msg = toast_text(str(msg), chinese_enabled(self.registry))
            title_font = self.font("poppins_bold", 7.7)
            body_font = self.font("poppins_regular", 7.3)
            tw = max(210.0, min(430.0, max(self.text_advance(title_font, title), self.text_advance(body_font, msg)) + 58.0))
            if self.gui_visible:
                y = self.height() - 82.0 - i * 42.0 + (1.0 - slide) * 18.0
            else:
                y = 74.0 + i * 42.0 - (1.0 - slide) * 18.0
            rect = QRectF(width / 2 - tw / 2, y, tw, 36.0)
            p.save()
            p.setOpacity(fade)
            rounded_rect(p, rect.adjusted(-10, -10, 10, 10), 16, color("#000000", 0.035), None)
            rounded_rect(p, rect, 10, color("#050506", 0.74), color("#ffffff", 0.08))
            icon_rect = QRectF(rect.left() + 7, rect.top() + 6, 24, 24)
            icon_v = self.anim.value(f"toast.icon.{id(toast)}", 1.0 if age < 2.7 else 0.0, self.anim_duration(0.24), Easings.ease_out_expo, initial=0.0)
            self.draw_dynamic_island_icon(p, icon_rect, kind, main, icon_v)
            self.draw_text(p, QRectF(rect.left() + 39, rect.top() + 5, rect.width() - 48, 13), title, title_font, THEME.text)
            self.draw_text(p, QRectF(rect.left() + 39, rect.top() + 18, rect.width() - 48, 13), msg, body_font, THEME.muted)
            progress = max(0.0, 1.0 - age / 3.0)
            bar = QRectF(rect.left() + 8, rect.bottom() - 4.0, rect.width() - 16, 2.0)
            draw_bar(p, bar, progress, main, color("#ffffff", 0.10), 1.0)
            p.restore()

    def draw_keybind_overlay(self, p: QPainter, width: int, height: int) -> None:
        module = self.binding_module
        if module is None:
            return
        active = self.anim.value("keybind.overlay", 1.0, self.anim_duration(0.18), Easings.ease_out_pow3, initial=0.0)
        p.save()
        p.setOpacity(active)
        p.fillRect(QRectF(0, 0, width, height), color("#000000", 0.58))
        scale = 0.96 + 0.04 * active
        rect = QRectF(width / 2 - 200 * scale, height / 2 - 90 * scale, 400 * scale, 180 * scale)
        glow_rect(p, rect, 12 * scale, 0.46)
        rounded_rect(p, rect, 11 * scale, color("#0b0b0f", 0.97), color("#ffffff", 0.16))
        self.draw_text(p, QRectF(rect.left(), rect.top() + 28 * scale, rect.width(), 24 * scale), ui_text("KEYBIND", chinese_enabled(self.registry)), self.font("poppins_bold", 18 * scale), THEME.text, Qt.AlignmentFlag.AlignCenter)
        self.draw_text(p, QRectF(rect.left(), rect.top() + 67 * scale, rect.width(), 21 * scale), ui_text(module.name, chinese_enabled(self.registry)), self.font("poppins_medium", 11 * scale), THEME.cyan, Qt.AlignmentFlag.AlignCenter)
        self.draw_text(p, QRectF(rect.left(), rect.top() + 98 * scale, rect.width(), 20 * scale), ui_text("Press any key to bind", chinese_enabled(self.registry)), self.font("poppins_regular", 9 * scale), THEME.muted, Qt.AlignmentFlag.AlignCenter)
        phase = time.perf_counter() * 4.6
        for i in range(3):
            dot_alpha = 0.24 + 0.64 * (0.5 + 0.5 * math.sin(phase + i * 0.7))
            p.setBrush(color("#ffffff", dot_alpha))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(rect.center().x() - 16 * scale + i * 16 * scale, rect.bottom() - 37 * scale), 3.3 * scale, 3.3 * scale)
        self.draw_text(p, QRectF(rect.left(), rect.bottom() - 24 * scale, rect.width(), 14 * scale), ui_text("ESC 清空绑定", chinese_enabled(self.registry)), self.font("poppins_regular", 7.4 * scale), color("#888890"), Qt.AlignmentFlag.AlignCenter)
        p.restore()

    def draw_toggle_switch(self, p: QPainter, rect: QRectF, enabled: bool, action: tuple[str, Any] | None = None) -> None:
        t = self.anim.value(f"toggle.{id(action[1]) if action else id(rect)}", 1.0 if enabled else 0.0, self.anim_duration(0.18), Easings.ease_out_cubic)
        fill = QColor(
            round(THEME.gray.red() + (THEME.green.red() - THEME.gray.red()) * t),
            round(THEME.gray.green() + (THEME.green.green() - THEME.gray.green()) * t),
            round(THEME.gray.blue() + (THEME.green.blue() - THEME.gray.blue()) * t),
            220,
        )
        rounded_rect(p, rect, rect.height() / 2, fill)
        knob = rect.height() - 4
        kx = rect.left() + 2 + (rect.width() - knob - 4) * t
        rounded_rect(p, QRectF(kx, rect.top() + 2, knob, knob), knob / 2, THEME.text)
        if action:
            self.add_hotspot(rect.adjusted(-4, -4, 4, 4), action[0], action[1])

    def draw_pill(self, p: QPainter, point: QPointF, text: str, c: QColor) -> None:
        font = self.font("poppins_medium", 8.0)
        p.setFont(font)
        w = max(92.0, self.text_advance(font, text) + 18.0)
        rect = QRectF(point.x(), point.y(), w, 20)
        rounded_rect(p, rect, 7, color("#090a0d", 0.86), c, 1.1)
        self.draw_text(p, rect.adjusted(8, 0, -8, 0), text, font, c)

    def draw_tooltip(self, p: QPainter, point: QPointF, text: str) -> None:
        if not text:
            return
        font = self.font("poppins_regular", 8.0)
        p.setFont(font)
        text = self.elide(p, font, text, 340)
        tw = self.text_advance(font, text) + 18
        x = min(point.x() + 14, self.width() - tw - 10)
        y = min(point.y() + 18, self.height() - 30)
        rect = QRectF(x, y, tw, 24)
        rounded_rect(p, rect, 6, color("#050506", 0.92), color("#ffffff", 0.14))
        self.draw_text(p, rect.adjusted(9, 0, -9, 0), text, font, THEME.text)

    def active_clickgui_mode(self) -> str:
        legacy = normalize_legacy_gui_mode(self.jello_state.legacy_mode or "")
        return legacy or PRIMARY_GUI_MODE

    @staticmethod
    def gui_mode_label(mode: str) -> str:
        return {
            "JelloSigma": "Jello Sigma",
            "PolarOld": "P Old",
            "PolarPanel": "P Panel",
            "PolarNew": "P New",
            "PolarDropdown": "P Drop",
            "PolarUnified": "Polar One",
            "PolarMulti": "Polar Multi",
        }.get(mode, mode)



# ---------------------------------------------------------------------------
# API 冻结面 qualname 固定（见模块 docstring「__qualname__ 钉回」；先例：
# ui/polar/text_metrics.py、renderers/*、mixins/persistence_mixin.py 与
# mixins/video_backend_mixin.py 底部同一手法）。api_freeze_test 对方法类
# 成员比对 __qualname__（基线值形如 "PolarRMWindow.<name>"），钉回后
# window.<member> 签名与迁移前 bit 级一致。
# ---------------------------------------------------------------------------

_QUALNAME_PINNED_MEMBERS = (
    ClickGuiBuildersMixin.open_legacy_clickgui,
    ClickGuiBuildersMixin.return_to_jello_clickgui,
    ClickGuiBuildersMixin.set_clickgui_visible,
    ClickGuiBuildersMixin.build_gpu_clickgui,
    ClickGuiBuildersMixin.build_gpu_glass_rect,
    ClickGuiBuildersMixin.build_gpu_startup_button,
    ClickGuiBuildersMixin.build_gpu_startup_session,
    ClickGuiBuildersMixin.build_gpu_startup_bottom_bar,
    ClickGuiBuildersMixin.build_gpu_startup_hover_layer,
    ClickGuiBuildersMixin.gpu_alpha,
    ClickGuiBuildersMixin.gui_primary_text,
    ClickGuiBuildersMixin.gui_secondary_text,
    ClickGuiBuildersMixin.gui_disabled_text,
    ClickGuiBuildersMixin.build_gpu_panel_gui,
    ClickGuiBuildersMixin.build_gpu_old_gui,
    ClickGuiBuildersMixin.build_gpu_new_gui,
    ClickGuiBuildersMixin.build_gpu_dropdown_gui,
    ClickGuiBuildersMixin.register_gui_scroll_viewport,
    ClickGuiBuildersMixin.polar_view_modules,
    ClickGuiBuildersMixin.build_gpu_polar_glass,
    ClickGuiBuildersMixin.build_gpu_polar_unified,
    ClickGuiBuildersMixin.build_gpu_scroll_island,
    ClickGuiBuildersMixin.build_gpu_polar_multi,
    ClickGuiBuildersMixin.build_gpu_polar_module_row,
    ClickGuiBuildersMixin.build_gpu_module_row,
    ClickGuiBuildersMixin.build_gpu_settings,
    ClickGuiBuildersMixin.build_gpu_setting_row,
    ClickGuiBuildersMixin.build_gpu_inline_settings,
    ClickGuiBuildersMixin.build_gpu_toggle,
    ClickGuiBuildersMixin.build_gpu_mode_switcher,
    ClickGuiBuildersMixin.build_gpu_legacy_return,
    ClickGuiBuildersMixin.build_gpu_toasts,
    ClickGuiBuildersMixin.build_gpu_tooltip,
    ClickGuiBuildersMixin.elide_for_gpu,
    ClickGuiBuildersMixin.build_gpu_keybind_overlay,
    ClickGuiBuildersMixin.draw_panel_gui,
    ClickGuiBuildersMixin.draw_profile_widget,
    ClickGuiBuildersMixin.draw_panel_category_bar,
    ClickGuiBuildersMixin.draw_panel_module_list,
    ClickGuiBuildersMixin.draw_settings_area,
    ClickGuiBuildersMixin.draw_bind_row,
    ClickGuiBuildersMixin.draw_setting_row,
    ClickGuiBuildersMixin.draw_compact_setting_row,
    ClickGuiBuildersMixin.draw_search_bar,
    ClickGuiBuildersMixin.draw_old_gui,
    ClickGuiBuildersMixin.draw_new_gui,
    ClickGuiBuildersMixin.draw_new_panel_shell,
    ClickGuiBuildersMixin.draw_new_module_block,
    ClickGuiBuildersMixin.draw_dropdown_gui,
    ClickGuiBuildersMixin.draw_dropdown_panel,
    ClickGuiBuildersMixin.draw_inline_settings,
    ClickGuiBuildersMixin.module_draw_height,
    ClickGuiBuildersMixin.inline_bind_height,
    ClickGuiBuildersMixin.inline_setting_height,
    ClickGuiBuildersMixin.waterfall_reveal,
    ClickGuiBuildersMixin.compact_keybind,
    ClickGuiBuildersMixin.draw_mode_switcher,
    ClickGuiBuildersMixin.draw_legacy_return,
    ClickGuiBuildersMixin.draw_toasts,
    ClickGuiBuildersMixin.draw_keybind_overlay,
    ClickGuiBuildersMixin.draw_toggle_switch,
    ClickGuiBuildersMixin.draw_pill,
    ClickGuiBuildersMixin.draw_tooltip,
    ClickGuiBuildersMixin.active_clickgui_mode,
    ClickGuiBuildersMixin.gui_mode_label,
)

for _fn in _QUALNAME_PINNED_MEMBERS:
    _fn.__qualname__ = f"PolarRMWindow.{_fn.__name__}"
del _fn, _QUALNAME_PINNED_MEMBERS
