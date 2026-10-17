"""Frozen wheel route at 8dba917; do not regenerate to accept behavior changes.
Source: rm_client/ui/polar/mixins/input_dispatch_mixin.py
"""
from __future__ import annotations
from rm_client.ui.cs2_buy_menu import grid_enabled, grid_layout
from rm_client.ui.polar.primitives import InertialScroll
class WheelRoutingBaseline:
    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._lifecycle_owns_input():
            event.accept()
            return
        if self.escape_menu_visible:
            event.accept()
            return
        if self.student_ui.visible:
            if self.student_ui.panel == "inventory":
                self.handle_student_action("student_page", -1 if event.angleDelta().y() > 0 else 1)
            event.accept()
            return
        point = event.position()
        pixel_delta = event.pixelDelta().y()
        delta = -float(pixel_delta) if pixel_delta else -event.angleDelta().y() / 120.0 * 28.0
        if self.match_chat.input_open:
            self.match_chat.scroll(-1 if event.angleDelta().y() > 0 else 1)
            self.frame_scheduler.force()
            event.accept()
            return
        if self.economy_visible:
            layout = (grid_layout(self.economy_workspace_layer,self.width(),self.height(),self.economy_island.progress)
                      if grid_enabled(self) else self.economy_workspace_layer.layout(self.width(),self.height(),self.economy_island.progress))
            if layout.offers_viewport.contains(point):
                self.economy_offers_scroll.add_wheel(delta)
            elif layout.detail_viewport.contains(point):
                self.economy_detail_scroll.add_wheel(delta)
            # Outside all three viewports: ignore scroll (no fallback).
            self.frame_scheduler.force()
            event.accept()
            return
        active_gui_mode = self.active_clickgui_mode()
        if active_gui_mode == "PolarPanel" and self.panel_modules_rect.contains(point):
            modules = self.panel_modules()
            scale = self.get_gui_scale_for_bounds(self.width(), self.height(), 600.0, 440.0, 76.0, 82.0)
            row_h = 22.0 * scale
            clip_height = self.panel_modules_rect.height() - 36.0 * scale
            maximum = max(0.0, len(modules) * row_h - clip_height)
            key = self.selected_category
            current = self.panel_module_scroll.get(key, 0.0)
            self.panel_module_scroll[key] = max(0.0, min(maximum, current + delta))
            self.frame_scheduler.force()
            event.accept()
            return
        if active_gui_mode == "PolarPanel" and self.panel_settings_rect.contains(point):
            key = self.selected_module.name
            self.settings_scroll[key] = max(0.0, self.settings_scroll.get(key, 0.0) + delta)
            self.frame_scheduler.force()
            event.accept()
            return
        for viewport in reversed(self.gui_scroll_viewports):
            if not viewport["rect"].contains(point):
                continue
            mode, key, maximum = viewport["mode"], viewport["key"], float(viewport["maximum"])
            if mode == "jello_dropdown":
                jello_delta = -float(pixel_delta) if pixel_delta else -event.angleDelta().y() / 120.0 * 35.0
                self.jello_state.dropdown_scroll = max(
                    0.0, min(maximum, self.jello_state.dropdown_scroll + jello_delta)
                )
                self.frame_scheduler.force()
                event.accept()
                return
            elif mode == "jello_panel":
                # ScrollableContentPanel.scrollStep is the literal 35 in
                # Sigma 5.1.0; retained Polar interfaces continue using 28.
                jello_delta = -float(pixel_delta) if pixel_delta else -event.angleDelta().y() / 120.0 * 35.0
                current = float(self.jello_state.panel_scroll.get(key, 0.0))
                self.jello_state.panel_scroll[key] = max(0.0, min(maximum, current + jello_delta))
                self.jello_state.mark_scroll(f"panel:{key}")
                self.frame_scheduler.force()
                event.accept()
                return
            elif mode == "jello_settings":
                jello_delta = -float(pixel_delta) if pixel_delta else -event.angleDelta().y() / 120.0 * 35.0
                self.jello_state.scroll_settings_by(key, jello_delta, maximum)
                self.jello_state.mark_scroll(f"settings:{key}")
                self.frame_scheduler.force()
                event.accept()
                return
            elif mode == "old":
                target = self.old_panels[key]
            elif mode == "new":
                target = self.new_panels[key]
            elif mode == "dropdown":
                target = self.dropdown_panels[key]
            elif mode == "polar_multi":
                target = self.polar_windows[key]
            elif mode == "polar_settings":
                motion = self.polar_settings_scroll_motion.setdefault(key, InertialScroll())
                motion.set_bounds(maximum)
                motion.add_wheel(delta)
                self.frame_scheduler.force()
                event.accept()
                return
            else:
                self.polar_unified_scroll_motion.set_bounds(maximum)
                self.polar_unified_scroll_motion.add_wheel(delta)
                self.frame_scheduler.force()
                event.accept()
                return
            target["scroll"] = max(0.0, min(maximum, float(target.get("scroll", 0.0)) + delta))
            self.frame_scheduler.force()
            event.accept()
            return
        if self.combat_control_input_allowed():
            link = self.registry.get("RobotLink")
            self.control.add_wheel(event.angleDelta().y() *
                                   self.get_setting_number(link, "Mouse Wheel Scale", 1.0))
            self.control.mock_output = bool(self.backend.mqtt.mock_output)
            self.control.send_from_state(self.state, self.registry)
            event.accept()
