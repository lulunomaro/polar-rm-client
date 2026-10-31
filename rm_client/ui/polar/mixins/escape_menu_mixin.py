"""Windows uses the retained original-source Endfield menu.

The CS-style painter below is retained for non-Windows and inert CI hosts.
No fake pause, device work or CPU backdrop copies.

Visual reference: CRC-verified local copies of Valve panorama/styles/mainmenu
.vcss_c (pausemenu-navbar-container / pausemenu-navbar__btn-small), 2026-10-14.
Its centered horizontal strip, 96px reference top margin, 64px button height
and Stratum2 typography inform this independent Qt adaptation. Valve assets
remain private in user_data/cs2_ui_pack; no Valve source is redistributed.
Not 1:1: only real RM actions, rounded-corner safety and CJK fallback remain.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from ...jello_locale import chinese_enabled, ui_label
from ...fonts import draw_ui_text, has_cjk
from ...cs2_assets import local_cs2_assets
from rm_client.ui.endfield.menu.mixin import EndfieldMenuMixin


def _menu_font(size, *, bold=False):
    pack = local_cs2_assets()
    family = pack.families.get("font_condensed" if bold else "font_regular", "Arial")
    font = QFont(family, size)
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    return font


def _menu_text(painter, rect, text):
    # Stratum2 has no CJK glyphs; retain explicit Regular fallback, not an
    # implicit/synthetic font substitution which produced thin/broken runs.
    if has_cjk(text):
        draw_ui_text(painter, rect, text, painter.font(), Qt.AlignmentFlag.AlignCenter)
    else:
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


class EscapeMenuMixin(EndfieldMenuMixin):
    @property
    def escape_menu_visible(self) -> bool:
        return bool(getattr(self, "_escape_menu_visible", False))

    def set_escape_menu_visible(self, visible: bool) -> None:
        if getattr(self, "_endfield_menu", None) is not None:
            self.set_endfield_menu_visible(visible)
            return
        if visible == self.escape_menu_visible or (visible and self.home_visible):
            return
        # Neutralize held input immediately; backend tick/telemetry/estop keep
        # running. Closing never restores a captured key/button or arms control.
        self._escape_menu_visible = visible
        self._escape_menu_pressed = None
        self._neutralize_modal_inputs()
        # Preserve failed neutral-send retry state for the normal backend tick.
        self._control_mouse_point = None
        self.tactical_map_hold = False
        self.drag = None
        self._escape_menu_visible = visible
        self._escape_menu_confirm_exit = False
        self._escape_menu_selected = 0
        self.hotspots.clear()
        self.invalidate_transient_overlay_scene()
        self.frame_scheduler.force()
        self.update()

    def escape_menu_choices(self):
        labels = ("Cancel", "Exit client") if getattr(self, "_escape_menu_confirm_exit", False) else (
            "Continue", "Settings", "Return to startup", "Exit client", "UI 切换")
        chinese = chinese_enabled(getattr(self, "registry", None))
        return tuple(ui_label(label, chinese) for label in labels)

    def activate_escape_menu_choice(self, index: int) -> None:
        if not self.escape_menu_visible:
            return
        # A page action retires its old mouse capture before callbacks or layout changes.
        self._escape_menu_pressed = None
        if getattr(self, "_escape_menu_confirm_exit", False):
            if index == 0:
                self._escape_menu_confirm_exit = False
                self._escape_menu_selected = 0
            elif index == 1:
                # Existing closeEvent owns neutral/lock/estop/resource teardown.
                self.close()
        elif index == 0:
            self.set_escape_menu_visible(False)
        elif index == 1:
            # Genuine settings only. The modal center owns its own lifecycle;
            # it does not pause the match, restore held input or arm control.
            from ...cs2_settings_center import open_settings_center
            open_settings_center(self)
        elif index == 2:
            # The startup route is fail closed even if the scene is re-entered.
            self.backend.lock_control("return to startup")
            self.set_escape_menu_visible(False)
            self.home_visible = True
            self.startup_step = "scenario"
            self.finish_startup_page_transition()
            self.reset_startup_page_animation("scenario")
            self.invalidate_transient_overlay_scene()
        elif index == 3:
            self._escape_menu_confirm_exit = True
            self._escape_menu_selected = 0  # Safe default: Cancel.
        elif index == 4:
            from ...ui_switch import open_ui_switch
            open_ui_switch(self)
        self.frame_scheduler.force()
        self.update()

    def handle_escape_menu_key(self, event) -> bool:
        if not self.escape_menu_visible:
            return False
        if getattr(self, "_endfield_menu", None) is not None:
            return self.handle_endfield_key(event)
        pending = getattr(self, "_escape_menu_key_releases", set())
        pending.add(event.key())
        self._escape_menu_key_releases = pending
        if not event.isAutoRepeat():
            if event.key() == Qt.Key.Key_Escape:
                self._escape_menu_pressed = None
                if getattr(self, "_escape_menu_confirm_exit", False):
                    self._escape_menu_confirm_exit = False
                    self._escape_menu_selected = 0
                else:
                    self.set_escape_menu_visible(False)
            elif event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Tab):
                self._escape_menu_pressed = None
                step = -1 if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Left) or (event.key() == Qt.Key.Key_Tab and event.modifiers() & Qt.KeyboardModifier.ShiftModifier) else 1
                self._escape_menu_selected = (self._escape_menu_selected + step) % len(self.escape_menu_choices())
            elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self.activate_escape_menu_choice(self._escape_menu_selected)
        self.frame_scheduler.force()
        event.accept()
        return True

    def escape_menu_layout(self, width, height):
        scale = min(1.25, width / 1100.0, height / 600.0)
        panel = QRectF((width - 1000 * scale) / 2, 96 * height / 1080, 1000 * scale, 238 * scale)
        count = len(self.escape_menu_choices())
        row_width = (panel.width() - (56 + (count - 1) * 8) * scale) / count
        rows = [QRectF(panel.x() + (28 + i * (row_width / scale + 8)) * scale,
                       panel.y() + 111 * scale, row_width, 64 * scale) for i in range(count)]
        return panel, rows, scale

    def handle_escape_menu_mouse(self, event, phase) -> bool:
        if not self.escape_menu_visible:
            return False
        if getattr(self, "_endfield_menu", None) is not None:
            return self.handle_endfield_pointer(event, phase)
        _, rows, _ = self.escape_menu_layout(self.width(), self.height())
        index = next((i for i, rect in enumerate(rows) if rect.contains(event.position())), None)
        if index is not None:
            self._escape_menu_selected = index
        if phase == "press":
            self._escape_menu_pressed = index if event.button() == Qt.MouseButton.LeftButton else None
        elif phase == "release":
            pressed = self._escape_menu_pressed
            self._escape_menu_pressed = None
            if event.button() == Qt.MouseButton.LeftButton and index is not None and index == pressed:
                self.activate_escape_menu_choice(index)
        self.frame_scheduler.force()
        event.accept()
        return True

    def draw_escape_menu(self, painter, width, height) -> None:
        if not self.escape_menu_visible:
            return
        if getattr(self, "_endfield_menu", None) is not None:
            self.draw_endfield_loading(painter, width, height)
            return
        panel, rows, scale = self.escape_menu_layout(width, height)
        painter.save()
        # The priority FBO uses a fresh QPainter, unlike the static HUD's
        # painter. Own our AA/blend state so curved strokes use Qt's path
        # renderer (the compositor supplies stencil) and Clear/Source state
        # from another overlay cannot erase our translucent surfaces.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillRect(QRectF(0, 0, width, height), QColor(0, 0, 0, 100))
        # Unified GPU uses the compositor's existing Jello blur. Emergency
        # compatibility draws this translucent card without CPU blur/readback.
        painter.setBrush(QColor(24, 24, 24, 220))
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1))
        painter.drawRoundedRect(panel, 12 * scale, 12 * scale)
        font = _menu_font(max(10, round(27 * scale)), bold=True)
        painter.setFont(font)
        painter.setPen(QColor(254, 254, 254))
        confirm = getattr(self, "_escape_menu_confirm_exit", False)
        chinese = chinese_enabled(getattr(self, "registry", None))
        _menu_text(painter, QRectF(panel.x(), panel.y() + 24 * scale, panel.width(), 45 * scale), ui_label("Exit client?", chinese) if confirm else "POLAR · CS2")
        font = _menu_font(max(8, round(10 * scale)))
        painter.setFont(font)
        painter.setPen(QColor(195, 202, 213))
        _menu_text(painter, QRectF(panel.x(), panel.y() + 75 * scale, panel.width(), 24 * scale), ui_label("Controls will lock before shutdown" if confirm else "Telemetry and safety monitoring remain active", chinese))
        for index, (rect, label) in enumerate(zip(rows, self.escape_menu_choices())):
            selected = index == self._escape_menu_selected
            painter.setPen(QPen(QColor(255, 255, 255, 95 if selected else 25), 1))
            painter.setBrush(QColor(255, 255, 255, 185 if selected else 12))
            painter.drawRoundedRect(rect, 5 * scale, 5 * scale)
            font = _menu_font(max(9, round(14 * scale)), bold=True)
            painter.setFont(font)
            painter.setPen(QColor(24, 24, 24) if selected else QColor(254, 254, 254))
            _menu_text(painter, rect, label)
        font = _menu_font(max(8, round(10 * scale)))
        painter.setFont(font)
        painter.setPen(QColor(180, 188, 200))
        _menu_text(painter, QRectF(panel.x(), panel.bottom() - 36 * scale, panel.width(), 22 * scale), ui_label("Esc  Back     ↑ ↓  Select     Enter  Confirm", chinese))
        painter.restore()
