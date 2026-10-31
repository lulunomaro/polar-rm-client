"""Qt semantic/input owner for the retained original-source D3D menu.

The native worker draws only. Existing client routes retain all authorization,
modal, configuration and teardown ownership. Closing holds input until the
authored exit clip has actually concealed the native visual.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication

from rm_client.ui.jello_locale import chinese_enabled
from .actions import UI_SWITCH_ACTION


class EndfieldMenuMixin:
    def advance_endfield_menu(self) -> None:
        menu = getattr(self, "_endfield_menu", None)
        if menu is None:
            return
        dpr = self.devicePixelRatioF()
        size = (round(self.width() * dpr), round(self.height() * dpr))
        # The only native window handle belongs to this client. No global
        # hooks, desktop capture or game process/window discovery is used.
        if self.isVisible() and not menu.attempted:
            self.frameSwapped.connect(menu.backdrop_presented)
            menu.prepare(int(self.winId()), *size)
        action = menu.tick(width=size[0], height=size[1],
                           chinese=chinese_enabled(self.registry),
                           mode=self.match_mode_manager.current.key,
                           paused=QApplication.activeModalWidget() is not None
                           or getattr(self, "_endfield_changelog_pending", False)
                           or getattr(self, "_endfield_settings_pending", False))
        if action in (2, 5, 6, 7, UI_SWITCH_ACTION):
            pending_flag = "_endfield_changelog_pending" if action == 7 else "_endfield_settings_pending"
            if not getattr(self, pending_flag, False):
                setattr(self, pending_flag, True)

                def open_menu_dialog() -> None:
                    try:
                        lifecycle = getattr(self, "_window_lifecycle", None)
                        if (self.escape_menu_visible and menu.requested and not self.home_visible
                                and not (lifecycle and lifecycle.closing)):
                            self.dispatch_endfield_action(action)
                    finally:
                        setattr(self, pending_flag, False)

                # 设置、键位和日志均为Esc子界面，保留菜单；下一轮再进入模态循环。
                QTimer.singleShot(0, self, open_menu_dialog)
        elif action:
            self.set_escape_menu_visible(False)
            self._endfield_pending_action = action if action != 1 else None
        if self.escape_menu_visible and not menu.requested and (menu.phase == 0 or menu.error):
            self._escape_menu_visible = False
            self.hotspots.clear()
            self.invalidate_transient_overlay_scene()
            self.frame_scheduler.force()
            pending = getattr(self, "_endfield_pending_action", None)
            self._endfield_pending_action = None
            if pending is not None:
                try:
                    self.dispatch_endfield_action(pending)
                finally:
                    self._endfield_settings_backdrop = None

    def set_endfield_menu_visible(self, visible: bool) -> None:
        menu = self._endfield_menu
        if visible and self.home_visible:
            return
        if visible == menu.requested:
            return
        self._neutralize_modal_inputs()
        self._control_mouse_point = None
        self.tactical_map_hold = False
        self.drag = None
        self._escape_menu_pressed = None
        self._endfield_pending_action = None
        if visible:
            self._endfield_settings_backdrop = None
        menu.request(visible, wait_for_backdrop=visible)
        # Conceal is asynchronous: never leak keys to the robot under the
        # final 333ms of source exit animation.
        self._escape_menu_visible = visible or bool(menu.phase)
        self.hotspots.clear()
        self.invalidate_transient_overlay_scene()
        self.frame_scheduler.force()
        self.update()

    def handle_endfield_key(self, event) -> bool:
        pending = getattr(self, "_escape_menu_key_releases", set())
        pending.add(event.key())
        self._escape_menu_key_releases = pending
        if not event.isAutoRepeat():
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.set_escape_menu_visible(False)
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self._endfield_menu.pointer(5, 0, 0, 2)
            elif key in (Qt.Key.Key_Up, Qt.Key.Key_Left, Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_Tab):
                backward = key in (Qt.Key.Key_Up, Qt.Key.Key_Left) or (
                    key == Qt.Key.Key_Tab and event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._endfield_menu.pointer(5, 0, 0, -1 if backward else 1)
        event.accept()
        return True

    def handle_endfield_pointer(self, event, phase) -> bool:
        if phase == "move" or event.button() == Qt.MouseButton.LeftButton:
            point, dpr = event.position(), self.devicePixelRatioF()
            self._endfield_menu.pointer({"move": 1, "press": 2, "release": 3}[phase],
                                       point.x() * dpr, point.y() * dpr)
        event.accept()
        return True

    def leaveEvent(self, event) -> None:
        # 菜单直接合成到 Qt 窗口；离开事件也由 Qt 转交，清掉残留悬停。
        menu = getattr(self, "_endfield_menu", None)
        if menu is not None:
            menu.pointer(6, 0, 0)
        super().leaveEvent(event)

    def draw_endfield_loading(self, painter, width, height) -> None:
        menu = self._endfield_menu
        # The source scene is a retained native visual on Qt's presentation
        # window. Existing GPU compositor continues to own the backdrop.
        if not getattr(self, "_gpu_frame_recording", False):
            # Emergency painter path has no GPU blur. Keep it uncluttered and
            # dim rather than copying the desktop or doing CPU image blur.
            painter.fillRect(0, 0, width, height, QColor(13, 16, 16, 150))
        if menu.ready and not menu.error:
            return
        painter.save()
        painter.fillRect(0, 0, width, height, QColor(13, 16, 16, 220))
        painter.setPen(QColor("#eee8bb"))
        painter.setFont(QFont("Microsoft YaHei UI", 16))
        message = "正在初始化原版场景…" if not menu.error else "原版场景不可用 · Esc 返回\n" + menu.error
        painter.drawText(20, 20, width - 40, height - 40,
                         Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, message)
        painter.restore()

    def dispatch_endfield_action(self, action: int) -> None:
        if action == UI_SWITCH_ACTION:
            from rm_client.ui.ui_switch import open_ui_switch
            open_ui_switch(self)
            return
        if action in (2, 6):
            from rm_client.ui.cs2_settings_center import open_settings_center
            open_settings_center(self)
        elif action == 5:
            from rm_client.ui.jello_keybinds import open_keyboard_manager
            open_keyboard_manager(self)
        elif action == 3:
            self.backend.lock_control("return to startup")
            self.home_visible = True
            self.startup_step = "scenario"
            self.finish_startup_page_transition()
            self.reset_startup_page_animation("scenario")
        elif action == 4:
            from rm_client.ui.endfield.dialogs import confirm_exit
            if confirm_exit(self):
                self.close()
        elif action == 7:
            from rm_client.ui.endfield.dialogs import show_changelog
            show_changelog(self)
        elif action == 8:
            self.open_economy_workspace()
        elif action == 10:
            self.open_student_panel("inventory")
        elif action == 14:
            return
        elif action == 19:
            self.open_match_chat()
        else:
            module = {9: "TacticalMap", 12: "MatchReadiness", 13: "HUD",
                      15: "MinecraftCombatHUD", 16: "AudioFeedback", 17: "CameraFeed",
                      18: "MatchTimer", 20: "Scenario"}.get(action)
            self.set_clickgui_visible(True)
            if module and self.registry.get(module):
                self.dispatch("jello_open_settings", module, self.hover_point, 1, self.rect())
