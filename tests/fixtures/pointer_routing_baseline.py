"""Frozen pointer-routing reference from 1b2f580.

Source: rm_client/ui/polar/mixins/input_dispatch_mixin.py
Method bodies are unchanged source slices; do not regenerate to accept changes.
"""
from __future__ import annotations
import math
import time
from PySide6.QtCore import QPointF, Qt
from rm_client.ui.tactical_map_interaction import map_hit_position
from rm_client.ui.cs2_feedback import notify_buy_hover

class PointerRoutingBaseline:
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._lifecycle_owns_input():
            event.accept()
            return
        self.pointer_interaction.begin_press(left_button=event.button() == Qt.MouseButton.LeftButton)
        if self.handle_escape_menu_mouse(event, "press"):
            return
        if self.handle_student_mouse(event, "press"):
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self.pointer_interaction.binding_module is not None:
            return
        button = self.qt_button(event)
        point = event.position()
        if (not self.gui_visible
                and getattr(getattr(self, "gui_timeline", None), "progress", 0.0) > 0.0):
            # The exit animation may render old GUI hit targets again. They
            # are display-only once closed; consume clicks until it finishes.
            if button == 1:
                self.pointer_interaction.own_release()
            event.accept()
            return
        if self.quick_action_wheel.active or self.overlay_coordinator.radial_active:
            if button == 2:
                self.cancel_quick_action_wheel()
            else:
                self.quick_action_wheel.update_pointer(point.x(), point.y())
                self.frame_scheduler.force()
            event.accept()
            return
        if button == 1:
            self.liquid_press_point = QPointF(point)
            self.liquid_press_until = time.perf_counter() + 0.12
            if (not self.gui_visible and self.tactical_map_hold
                    and getattr(self, "tactical_map_field_layout", None) is not None
                    and map_hit_position(point, self.tactical_map_field_rect,
                        getattr(self, "_tactical_map_hit_geometry", None)) is not None):
                # Hotspots still dispatch first, but a map press owns its
                # release even when that dispatch or command publication fails.
                self.pointer_interaction.own_release()
                self._control_mouse_point = None
        for hotspot in reversed(self.hotspots):
            if hotspot.button not in (None, button):
                continue
            if hotspot.rect.contains(point):
                self.dispatch(hotspot.action, hotspot.payload, point, button, hotspot.rect)
                return
        # Opening/rebuilding a GUI may leave no current hit targets for one
        # frame. The modal still owns that click; never publish it to the map.
        if self.gui_visible:
            if button == 1:
                self.pointer_interaction.own_release()
            event.accept()
            return
        if button == 1 and self.publish_map_click_from_screen(point):
            event.accept()
            return
        if self.combat_control_input_allowed():
            self.control.mock_output = bool(self.backend.mqtt.mock_output)
            self.control.set_mouse_button(button, True, self.state, self.registry)
            event.accept()
        if button == 1:
            self.search_focus = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._lifecycle_owns_input():
            event.accept()
            return
        if self.pointer_interaction.track_owned_pointer(event.position()):
            event.accept()
            return
        if self.handle_escape_menu_mouse(event, "move"):
            return
        if self.handle_student_mouse(event, "move"):
            return
        if self.quick_action_wheel.active or self.overlay_coordinator.radial_active:
            point = event.position()
            previous = self.quick_action_wheel.selected_index
            selected = self.quick_action_wheel.update_pointer(point.x(), point.y())
            if selected != previous:
                self.frame_scheduler.force()
            event.accept()
            return
        previous_tooltip = self.hover_tooltip
        previous_point = self.hover_point
        self.hover_point = event.position()
        if (
            self.combat_control_input_allowed()
            and self._control_mouse_point is not None
        ):
            delta = self.hover_point - self._control_mouse_point
            link = self.registry.get("RobotLink")
            self.control.add_mouse_motion(
                delta.x(), delta.y(),
                self.get_setting_number(link, "Mouse Sensitivity", 1.0),
                self.setting_bool(link, "Invert Mouse Y", False),
            )
        self._control_mouse_point = (
            QPointF(self.hover_point)
            if self.combat_control_input_allowed()
            else None
        )
        self.hover_tooltip = ""
        hovered_action = ""
        hovered_offer = ""
        for hotspot in reversed(self.hotspots):
            if hotspot.rect.contains(self.hover_point):
                self.hover_tooltip = hotspot.tooltip
                hovered_action = hotspot.action
                if hovered_action=="economy_offer":
                    hovered_offer = str(hotspot.payload)
                break
        if self.economy_visible:
            notify_buy_hover(self,hovered_offer,time.perf_counter())
        # 启动页可见时渲染时钟已经按显示器刷新率运行；不再为每个原始
        # 鼠标事件强制整帧，仅在跨越热点、拖拽或提示变化时唤醒一帧。
        if (
            self.pointer_interaction.drag is not None
            or hovered_action != self._startup_last_hover_action
            or self.hover_tooltip != previous_tooltip
            or (not self.home_visible and (self.hover_point - previous_point).manhattanLength() > 6.0)
        ):
            self.frame_scheduler.force()
        self._startup_last_hover_action = hovered_action
        if self.pointer_interaction.drag is None:
            return
        point = event.position()
        if self.pointer_interaction.drag["type"] == "slider":
            setting: Setting = self.pointer_interaction.drag["setting"]
            rect: QRectF = self.pointer_interaction.drag["rect"]
            setting.set_from_ratio((point.x() - rect.left()) / max(1.0, rect.width()))
            self.after_setting_changed(setting)
        elif self.pointer_interaction.drag["type"] == "jello_module":
            start = self.pointer_interaction.drag.get("start", point)
            self.pointer_interaction.drag["point"] = QPointF(point)
            if isinstance(start, QPointF) and math.dist((start.x(), start.y()), (point.x(), point.y())) >= 6.0:
                self.pointer_interaction.drag["moved"] = True
            self.invalidate_gui_cache()
            self.frame_scheduler.force()
        elif self.pointer_interaction.drag["type"] == "old_panel":
            panel = self.old_panels[self.pointer_interaction.drag["category"]]
            panel["dragged"] = True
            panel["x"] = point.x() - self.pointer_interaction.drag["offset_x"]
            panel["y"] = point.y() - self.pointer_interaction.drag["offset_y"]
        elif self.pointer_interaction.drag["type"] == "polar_window":
            panel = self.polar_windows[self.pointer_interaction.drag["category"]]
            panel["x"] = point.x() - self.pointer_interaction.drag["offset_x"]
            panel["y"] = point.y() - self.pointer_interaction.drag["offset_y"]

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._lifecycle_owns_input():
            event.accept()
            return
        interaction = self.pointer_interaction
        if interaction.background_release_owned and event.button() == Qt.MouseButton.LeftButton:
            point = event.position()
            target = next((h for h in reversed(self.hotspots)
                           if h.button in (None, 1) and h.rect.contains(point)), None)
            release = interaction.release_background(point, left_button=True,
                gui_visible=self.gui_visible,
                background_target=target is not None and target.action == "jello_dismiss_background")
            if release.dismiss:
                self.set_clickgui_visible(False)
            event.accept()
            return
        if self.handle_escape_menu_mouse(event, "release"):
            return
        if self.handle_student_mouse(event, "release"):
            return
        if self.quick_action_wheel.active or self.overlay_coordinator.radial_active:
            self.quick_action_wheel.update_pointer(event.position().x(), event.position().y())
            event.accept()
            return
        if self.economy_hold_key:
            self.finish_economy_hold(event.position())
        if self.pointer_interaction.drag and self.pointer_interaction.drag.get("type") == "jello_module":
            drag = self.pointer_interaction.drag
            name = str(drag.get("module", ""))
            if drag.get("moved"):
                function_bar = self.registry.get("MinecraftCombatHUD")
                override = self.setting_mode(function_bar, "GUI Scale", "Auto")
                multiplier = self.get_setting_number(function_bar, "Scale Multiplier", 1.0)
                layout = self.minecraft_hud.layout(
                    self.width(),
                    self.height(),
                    override,
                    scale_multiplier=multiplier,
                )
                slot = layout.slot_at(event.position())
                if slot is None:
                    self.toast("Drop a module onto a Minecraft hotbar slot")
                else:
                    self.assign_minecraft_function_slot(slot, name)
            else:
                module = self.registry.get(name)
                if module is not None:
                    if module.name == "HeroLob" and module.enabled:
                        self.open_hero_deploy_interface()
                    else:
                        self.registry.toggle_module(module, source="clickgui")
                        self.selected_module = module
                        self.toast(f"{module.name} {'enabled' if module.enabled else 'disabled'}")
                        self.auto_save_config()
            self.invalidate_gui_cache()
            self.frame_scheduler.force()
        if self.pointer_interaction.drag and self.pointer_interaction.drag.get("type") in {"old_panel", "polar_window"}:
            self.auto_save_config()
        self.pointer_interaction.drag = None
        if self.combat_control_input_allowed():
            self.control.mock_output = bool(self.backend.mqtt.mock_output)
            self.control.set_mouse_button(self.qt_button(event), False, self.state, self.registry)
            event.accept()
