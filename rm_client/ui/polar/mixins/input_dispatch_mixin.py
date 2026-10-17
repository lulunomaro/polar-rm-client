"""Qt input routing and ClickGUI action dispatch.

PointerInteraction owns pointer capture, dismissal and binding lifetimes.
Event adapters and registered action handlers still receive the window and use
its registry, control and rendering services. The action table centralizes
routing; it does not make these handlers independent domain components.
"""

from __future__ import annotations

import math
import logging
import time
from typing import Any

from ....emergency_actions import request_emergency_stop

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QCursor, QKeyEvent, QKeySequence, QMouseEvent, QWheelEvent

# 相对导入层级：rm_client/ui/polar/mixins/ → ... = rm_client.ui、.... = rm_client
from ...animation import Easings
from ...tactical_map_interaction import map_hit_position
from ...cs2_buy_menu import grid_enabled, grid_layout
from ...cs2_feedback import notify_buy_hover
from ...jello_locale import LANGUAGE_CHINESE, LANGUAGE_ENGLISH, LANGUAGE_SETTING
from ....model import Setting
from ..primitives import InertialScroll
from ..pointer_interaction import PointerField, PointerInteraction
from ..hit_testing import topmost_hotspot
from ..drag_editing import apply_slider_ratio, edit_slider, move_panel
from ..scroll_routing import ScrollFamily, scroll_target_at
from ..text_editing import ChatEdit, SearchEdit, TextKey, chat_edit, search_edit

__all__ = [
    "InputDispatchMixin",
]



def _begin_economy_hold_from_input(host, offer, owner, rect=None):
    # Keep the public hold API compatible while transferring the event source
    # only when begin_economy_hold actually admits the offer.
    previous = getattr(host, "_economy_hold_input_source", None)
    host._economy_hold_input_source = owner
    try:
        if rect is None:
            host.begin_economy_hold(offer)
        else:
            host.begin_economy_hold(offer, rect)
    finally:
        host._economy_hold_input_source = previous


def _cancel_economy_hold_input(host):
    # Retire confirmation intent before cleanup can call out or reenter Qt.
    host.economy_hold_key = ""
    host._economy_hold_input_owner = None
    host.economy_hold_rect = QRectF()


def _owns_economy_hold_release(host, owner):
    admitted = getattr(host, "_economy_hold_input_owner", None)
    return admitted is None or admitted == owner


# Compatibility action routing. Handlers retain access to the complete window;
# button-specific admission belongs to each handler, while dispatch applies
# runtime-transition and GUI-cache guards before invoking it.

_GUI_CACHE_INVALIDATE_ACTIONS = frozenset({
    "module_toggle", "module_select", "module_expand", "setting_toggle", "setting_slider",
    "setting_mode_next", "select_category", "gui_mode", "old_expand", "dropdown_expand", "polar_drag",
    "jello_open_settings", "jello_close_settings", "jello_toggle_old_group",
    "jello_mode_dropdown", "jello_mode_select", "jello_open_legacy", "jello_return", "jello_module_press",
    "jello_toggle_language",
})


_QT_POINTER_BUTTONS = {
    Qt.MouseButton.LeftButton: 1,
    Qt.MouseButton.MiddleButton: 2,
    Qt.MouseButton.RightButton: 3,
}


def _pointer_button(event: QMouseEvent) -> int:
    """Unsupported/compound Qt buttons carry no recognized pointer intent."""
    return _QT_POINTER_BUTTONS.get(event.button(), 0)


_TEXT_KEYS = {
    Qt.Key.Key_Escape: TextKey.CANCEL,
    Qt.Key.Key_Return: TextKey.SUBMIT,
    Qt.Key.Key_Enter: TextKey.SUBMIT,
    Qt.Key.Key_Backspace: TextKey.BACKSPACE,
    Qt.Key.Key_Up: TextKey.UP,
    Qt.Key.Key_Down: TextKey.DOWN,
    Qt.Key.Key_PageUp: TextKey.PAGE_UP,
    Qt.Key.Key_PageDown: TextKey.PAGE_DOWN,
}


def _chat_key_edit(event: QKeyEvent) -> ChatEdit:
    key = _TEXT_KEYS.get(event.key(), TextKey.OTHER)
    # Special keys never consult the text payload in the existing Qt route.
    return chat_edit(key, event.text() if key is TextKey.OTHER else "")


def _search_key_edit(event: QKeyEvent) -> SearchEdit:
    key = _TEXT_KEYS.get(event.key(), TextKey.OTHER)
    edits_text = key not in (TextKey.BACKSPACE, TextKey.SUBMIT, TextKey.CANCEL)
    return search_edit(key, event.text() if edits_text else "")


def _on_action_jello_module_press(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    if button != 1:
        return
    module = self.registry.get(str(payload))
    if module is not None:
        self.pointer_interaction.drag = {
            "type": "jello_module",
            "module": module.name,
            "start": QPointF(point),
            "point": QPointF(point),
            "moved": False,
        }
        self.selected_module = module


def _on_action_module_toggle(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    module = self.registry.get(str(payload))
    if module:
        self.registry.toggle_module(module, source="clickgui")
        self.selected_module = module
        self.toast(f"{module.name} {'已启用' if module.enabled else '已关闭'}")
        self.auto_save_config()


def _on_action_module_select(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    module = self.registry.get(str(payload))
    if module:
        self.selected_module = module


def _on_action_module_expand(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    name = str(payload)
    if name in self.module_expanded:
        self.module_expanded.remove(name)
    else:
        self.module_expanded.add(name)
    self.auto_save_config()


def _on_action_module_bind(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    module = self.registry.get(str(payload))
    if module:
        from ...jello_keybinds import open_module_keyboard
        self.pointer_interaction.binding_module = None
        open_module_keyboard(self,module.name)


def _on_action_setting_toggle(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    setting: Setting = payload
    setting.value = not bool(setting.value)
    self.after_setting_changed(setting)


def _on_action_setting_slider(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    setting = payload
    apply_slider_ratio(setting, rect, point)
    self.pointer_interaction.drag = {"type": "slider", "setting": setting, "rect": rect}
    self.after_setting_changed(setting)


def _on_action_setting_mode_next(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    setting = payload
    setting.cycle(-1 if button == 3 else 1)
    self.after_setting_changed(setting)


def _on_action_select_category(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.selected_category = str(payload)


def _on_action_search_focus(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.search_focus = True


def _on_action_gui_mode(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    # Compatibility for stale hotspots/tests: a historical mode is a
    # transient legacy launch, never a persisted primary mode.
    self.open_legacy_clickgui(str(payload))


def _on_action_jello_toggle_language(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    click_gui = self.registry.get("ClickGui")
    language = click_gui.get_setting(LANGUAGE_SETTING) if click_gui is not None else None
    if language is not None:
        language.value = (
            LANGUAGE_ENGLISH
            if str(language.value) == LANGUAGE_CHINESE
            else LANGUAGE_CHINESE
        )
        self.jello_state.close_dropdown()
        self.prewarm_jello_module_resources(
            self.registry.get(self.jello_state.settings_module or "") or click_gui
        )
        self.after_setting_changed(language)
        self.toast("ClickGUI：简体中文" if language.value == LANGUAGE_CHINESE else "ClickGUI: English")
        self.frame_scheduler.force()


def _on_action_jello_open_settings(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    module = self.registry.get(str(payload))
    if module is not None:
        self.selected_module = module
        self.prewarm_jello_module_resources(module)
        self.jello_state.open_settings(module.name)
        self.frame_scheduler.force()


def _on_action_jello_close_settings(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.jello_state.close_settings()
    self.frame_scheduler.force()


def _on_action_jello_toggle_old_group(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.jello_state.toggle_old_group(str(payload))
    self.frame_scheduler.force()


def _on_action_jello_mode_dropdown(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    key, _setting = payload
    self.jello_state.toggle_dropdown(str(key))
    self.frame_scheduler.force()


def _on_action_jello_mode_select(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    setting, value = payload
    if value in setting.options:
        setting.value = value
        self.after_setting_changed(setting)
    self.jello_state.close_dropdown()
    self.frame_scheduler.force()


def _on_action_jello_open_legacy(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.open_legacy_clickgui(str(payload))


def _on_action_jello_return(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.return_to_jello_clickgui()


def _on_action_jello_dismiss_background(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    # Commit only on a matching release. A drag starting outside a panel must
    # not dismiss it, and the closing click must never reach combat controls.
    if button == 1 and self.gui_visible and not self.pointer_interaction.drag:
        self.pointer_interaction.own_background_press(point)


def _on_action_jello_noop(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    return


def _on_action_hero_deploy_open(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.open_hero_deploy_interface()


def _on_action_hero_deploy_enter(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    now = time.perf_counter()
    if self.hero_deploy_ui.arm_confirmation(1, now):
        self.send_hero_deploy_request(1)
    else:
        self.toast("Confirm ENTER deploy mode within 3 seconds")
    self.frame_scheduler.force()


def _on_action_hero_deploy_exit(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    now = time.perf_counter()
    if self.hero_deploy_ui.arm_confirmation(0, now):
        self.send_hero_deploy_request(0)
    else:
        self.toast("Confirm EXIT deploy mode within 3 seconds")
    self.frame_scheduler.force()


def _on_action_hero_deploy_close(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.close_hero_deploy_interface()


def _on_action_hero_lob_preview(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.handle_config_action("Preview Lob")


def _on_action_hero_lob_arm(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.handle_config_action("Arm Assisted Lob")


def _on_action_hero_lob_disarm(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.handle_config_action("Disarm Assisted Lob")


def _on_action_old_expand(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.old_panels[str(payload)]["expanded"] = not bool(self.old_panels[str(payload)]["expanded"])
    self.auto_save_config()


def _on_action_old_drag(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    if button != 1:
        return
    panel = self.old_panels[str(payload)]
    panel["dragged"] = True
    self.pointer_interaction.drag = {
        "type": "old_panel",
        "category": str(payload),
        "offset_x": point.x() - float(panel["x"]),
        "offset_y": point.y() - float(panel["y"]),
    }


def _on_action_polar_drag(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    if button != 1:
        return
    panel = self.polar_windows[str(payload)]
    self.pointer_interaction.drag = {
        "type": "polar_window",
        "category": str(payload),
        "offset_x": point.x() - float(panel["x"]),
        "offset_y": point.y() - float(panel["y"]),
    }


def _on_action_dropdown_expand(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.dropdown_panels[str(payload)]["expanded"] = not bool(self.dropdown_panels[str(payload)]["expanded"])
    self.auto_save_config()


def _on_action_select_scenario(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.apply_scenario(str(payload), close_home=False)
    # 下一页跟着 `MatchMode / Second Step`：以前四个跳转点全硬写 "match"，
    # 那个开关无论开关都必须过二级页，等于界面承诺了一个关不掉的东西。
    next_page = self.startup_page_after_scenario()
    self.reset_startup_page_animation(next_page)
    self.begin_startup_page_transition(next_page)
    self.auto_save_config()


def _on_action_select_match_mode(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.apply_match_mode(str(payload), close_home=False)
    self.reset_startup_page_animation("robot")
    self.begin_startup_page_transition("robot")
    self.auto_save_config()


def _on_action_select_robot_profile(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.apply_robot_profile(str(payload), close_home=True)
    self.auto_save_config()


def _on_action_config_action(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.handle_config_action(str(payload))


def _on_action_startup_back(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.reset_startup_page_animation("scenario")
    self.begin_startup_page_transition("scenario")


def _on_action_startup_back_match(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    # 这是机器人页上的"返回"按钮，回退目标必须与 Backspace 一致，
    # 否则关掉二级页时两个入口会跳到不同页面。
    back_page = self.startup_page_before_robot()
    self.reset_startup_page_animation(back_page)
    self.begin_startup_page_transition(back_page)


def _on_action_startup_open_settings(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    # Reuse the genuine modal preferences without entering a scenario/HUD.
    # The settings lifecycle preserves the current launcher page and choices.
    from ...cs2_settings_center import open_settings_center
    open_settings_center(self)


def _on_action_startup_open_gui(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    if self.reject_pending_configuration_edit():
        return
    # 启动页快捷入口与正常场景入口必须启动同一套真实后端。
    generation = self.backend.request_scenario(self.scenario_manager.current.key, registry=self.registry)
    self.capture_pending_scenario_configuration(generation)
    self.home_visible = False
    self.startup_step = "done"
    self.set_clickgui_visible(True, announce=False)
    self.toast("ClickGUI opened from launcher")


def _on_action_startup_open_config(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    if self.reject_pending_configuration_edit():
        return
    generation = self.backend.request_scenario(self.scenario_manager.current.key, registry=self.registry)
    self.capture_pending_scenario_configuration(generation)
    config_module = self.registry.get("ConfigManager")
    self.selected_category = "System"
    if config_module:
        self.selected_module = config_module
    self.home_visible = False
    self.startup_step = "done"
    self.set_clickgui_visible(True, announce=False)
    if config_module:
        self.jello_state.open_settings(config_module.name)
    self.toast("ConfigManager opened from launcher")


def _on_action_startup_open_wallpaper(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    profiles = self.startup_wallpaper.profile_names()
    visual_module = self.registry.get("StartupVisual")
    current = self.setting_mode(visual_module, "Wallpaper Profile", "BlackHole")
    self.wallpaper_picker_page = profiles.index(current) // 4 if current in profiles else 0
    self.set_wallpaper_picker_visible(True)


def _on_action_wallpaper_select(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.apply_wallpaper_profile(str(payload))
    self.frame_scheduler.force()


def _on_action_wallpaper_import(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.import_wallpaper_from_dialog()
    self.frame_scheduler.force()


def _on_action_wallpaper_remove(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.remove_wallpaper_profile(str(payload))
    self.frame_scheduler.force()


def _on_action_wallpaper_reset(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.reset_wallpaper_profile()
    self.frame_scheduler.force()


def _on_action_wallpaper_page(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    count = max(1, math.ceil(len(self.startup_wallpaper.profile_names()) / 4))
    self.wallpaper_picker_page = max(0, min(count - 1, self.wallpaper_picker_page + int(payload)))
    self.frame_scheduler.force()


def _on_action_wallpaper_close(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.set_wallpaper_picker_visible(False)


def _on_action_wallpaper_block(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    pass


def _on_action_economy_category(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    categories = self.economy_categories()
    category = str(payload)
    if category in categories:
        self.economy_category = category
        self.economy_selected = 0
        self.economy_selected_key = ""
        self.economy_armed_key = ""
        self.economy_offers_scroll.position = 0.0
        self.economy_offers_scroll.target = 0.0
        self.economy_offers_scroll.velocity = 0.0
        self.reconcile_economy_selection()
        self.frame_scheduler.force()


def _on_action_economy_offer(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    if grid_enabled(self):
        offer = next((o for o in self.economy_offers() if o.key==str(payload)),None)
        if offer is None:
            return
        self.economy_category = offer.category
    self.select_economy_offer(str(payload))


def _on_action_economy_confirm(self, action: str, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    # Hard gate: reject transactions if the workspace animation has not settled.
    if self.economy_island.progress < 0.48:
        return
    key = str(payload)
    offer = next((item for item in self.economy_offers() if item.key == key), None)
    if offer is not None:
        # Keep selection synced, but do not wipe double-confirm arm
        # (select_economy_offer only clears arm when the key changes).
        if self.economy_selected_key != key:
            self.select_economy_offer(key)
        else:
            self.economy_selected_key = key
        if action == "economy_confirm_hold":
            _begin_economy_hold_from_input(self, offer, ("mouse", button), rect)
        else:
            self.activate_economy_offer(offer)


def _on_action_economy_close(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.close_economy_workspace()


def _on_action_retry_video_source_cleanup(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    if button == 1:
        # Even a stale/busy recovery target owns its complete click. Its
        # rejected release must never reach an otherwise eligible control path.
        self.pointer_interaction.own_release()
        self._control_mouse_point = None
        self.retry_video_source_cleanup(payload)


def _on_action_wait_video_source_cleanup(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    if button == 1:
        self.pointer_interaction.own_release()
        self._control_mouse_point = None


def _on_action_startup_exit(self, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
    self.close()


_DISPATCH_HANDLERS = {
    "retry_video_source_cleanup": _on_action_retry_video_source_cleanup,
    "wait_video_source_cleanup": _on_action_wait_video_source_cleanup,
    "jello_module_press": _on_action_jello_module_press,
    "module_toggle": _on_action_module_toggle,
    "module_select": _on_action_module_select,
    "module_expand": _on_action_module_expand,
    "module_bind": _on_action_module_bind,
    "setting_toggle": _on_action_setting_toggle,
    "setting_slider": _on_action_setting_slider,
    "setting_mode_next": _on_action_setting_mode_next,
    "select_category": _on_action_select_category,
    "search_focus": _on_action_search_focus,
    "gui_mode": _on_action_gui_mode,
    "jello_toggle_language": _on_action_jello_toggle_language,
    "jello_open_settings": _on_action_jello_open_settings,
    "jello_close_settings": _on_action_jello_close_settings,
    "jello_toggle_old_group": _on_action_jello_toggle_old_group,
    "jello_mode_dropdown": _on_action_jello_mode_dropdown,
    "jello_mode_select": _on_action_jello_mode_select,
    "jello_open_legacy": _on_action_jello_open_legacy,
    "jello_return": _on_action_jello_return,
    "jello_noop": _on_action_jello_noop,
    "jello_dismiss_background": _on_action_jello_dismiss_background,
    "hero_deploy_open": _on_action_hero_deploy_open,
    "hero_deploy_enter": _on_action_hero_deploy_enter,
    "hero_deploy_exit": _on_action_hero_deploy_exit,
    "hero_deploy_close": _on_action_hero_deploy_close,
    "hero_lob_preview": _on_action_hero_lob_preview,
    "hero_lob_arm": _on_action_hero_lob_arm,
    "hero_lob_disarm": _on_action_hero_lob_disarm,
    "old_expand": _on_action_old_expand,
    "old_drag": _on_action_old_drag,
    "polar_drag": _on_action_polar_drag,
    "dropdown_expand": _on_action_dropdown_expand,
    "select_scenario": _on_action_select_scenario,
    "select_match_mode": _on_action_select_match_mode,
    "select_robot_profile": _on_action_select_robot_profile,
    "config_action": _on_action_config_action,
    "startup_back": _on_action_startup_back,
    "startup_back_match": _on_action_startup_back_match,
    "startup_open_settings": _on_action_startup_open_settings,
    "startup_open_gui": _on_action_startup_open_gui,
    "startup_open_config": _on_action_startup_open_config,
    "startup_open_wallpaper": _on_action_startup_open_wallpaper,
    "wallpaper_select": _on_action_wallpaper_select,
    "wallpaper_import": _on_action_wallpaper_import,
    "wallpaper_remove": _on_action_wallpaper_remove,
    "wallpaper_reset": _on_action_wallpaper_reset,
    "wallpaper_page": _on_action_wallpaper_page,
    "wallpaper_close": _on_action_wallpaper_close,
    "wallpaper_block": _on_action_wallpaper_block,
    "economy_category": _on_action_economy_category,
    "economy_offer": _on_action_economy_offer,
    "economy_confirm": lambda self, payload, point, button, rect: _on_action_economy_confirm(
        self, "economy_confirm", payload, point, button, rect),
    "economy_confirm_hold": lambda self, payload, point, button, rect: _on_action_economy_confirm(
        self, "economy_confirm_hold", payload, point, button, rect),
    "economy_close": _on_action_economy_close,
    "startup_exit": _on_action_startup_exit,
}



class InputDispatchMixin:
    """Qt event adapters and legacy field compatibility for pointer state."""
    # Historical fields remain writable for renderer/extension compatibility;
    # this single state owner holds the pointer epoch and binding lifecycle.
    drag = PointerField("drag")
    binding_module = PointerField("binding_module")
    _jello_background_press = PointerField("background_press")
    _jello_background_dragged = PointerField("background_dragged")
    _jello_background_release_owned = PointerField("background_release_owned")

    @property
    def pointer_interaction(self) -> PointerInteraction:
        # PolarRMWindow assembles this explicitly before installing callbacks.
        # Legacy lightweight mixin hosts have no window constructor; preserve
        # that extension seam with a per-instance owner, never class state.
        state = getattr(self, "_pointer_interaction", None)
        if state is None:
            state = self._pointer_interaction = PointerInteraction()
        return state

    def _modal_input_blocked(self) -> bool:
        return bool(getattr(self, "_modal_input_clearing", False)
                    or getattr(self, "_modal_input_clear_pending", False))

    def _neutralize_modal_inputs(self) -> None:
        """Retire held input at ownership transfer, including between UI ticks.

        Claim the boundary before publishing: an output callback may reenter Qt.
        Each independent egress gets a neutral attempt even if another fails.
        A failed cleanup blocks control until a successful explicit lock reset.
        """
        _cancel_economy_hold_input(self)
        if self._modal_input_blocked():
            return
        self._modal_input_clearing = True
        errors = []

        def attempt(action):
            try:
                action()
                return True
            except Exception as error:
                errors.append(error)
                logging.getLogger(__name__).exception("Modal input neutralization failed")
                return False

        try:
            mode_ready = attempt(lambda: setattr(
                self.control, "mock_output", bool(self.backend.mqtt.mock_output)))
            inputs_reset = attempt(lambda: self.control.reset_inputs())
            if mode_ready and inputs_reset:
                attempt(lambda: self.control.send_from_state(self.state, self.registry))
            attempt(lambda: self.control.release_direct_dt7())
            attempt(lambda: self.control.release_custom_controller())
            self._control_mouse_point = None
            self._combat_input_was_allowed = False
            if errors:
                self._modal_input_clear_pending = True
                attempt(lambda: self.control.clear_pending())
                attempt(lambda: self.backend.lock_control("modal input cleanup failed"))
                # Lock callbacks can fail after partially clearing state.
                self._modal_input_clear_pending = True
        finally:
            self._modal_input_clearing = False

    def _lifecycle_owns_input(self) -> bool:
        """A retiring runtime cannot accept edits or new control samples."""
        return bool(
            self._modal_input_blocked()
            or getattr(getattr(self, "backend", None), "lifecycle_pending", False)
            or getattr(self, "configuration_change_pending", False)
            or getattr(getattr(self, "_window_lifecycle", None), "closing", False)
        )

    def _clear_control_inputs_for_lock(self, _reason: str = "") -> None:
        """清空本地键鼠样本，并给"没有过期兜底"的出口补一帧中立的松手帧。

        以前这个钩子只清本地、把补帧推给"调用方"，但调用方从来没补过：
        DT7/选手端会把最后一帧重复到永远，锁定比赛控制反而成了踩油门。
        0x5A 与 0x0311 的链路中立化由后端负责；这里不能保留可重放的旧意图。
        """

        _cancel_economy_hold_input(self)
        # A reentrant callback joins the existing cleanup owner. It must not
        # reset that owner's pending latch or recursively publish neutral.
        if getattr(self, "_lock_input_clearing", False):
            return
        self._lock_input_clearing = True
        # Claim input before any output callback can reenter event dispatch.
        self._modal_input_clear_pending = True
        failures = []

        def attempt(action):
            try:
                action()
            except Exception as error:
                failures.append(error)
                logging.getLogger(__name__).exception("Locked input cleanup failed")

        try:
            attempt(lambda: self.control.reset_inputs())
            attempt(lambda: setattr(self.control, "last_command", None))
            attempt(lambda: self.pointer_interaction.cancel_epoch())
            # These links do not expire held input: each needs its own neutral
            # attempt even when another link or local reset raises.
            attempt(lambda: self.control.release_direct_dt7())
            attempt(lambda: self.control.release_custom_controller())
            attempt(lambda: self.control.clear_pending())
            self._economy_qualification_token = None
            self._control_mouse_point = None
            self._combat_input_was_allowed = False
            if failures:
                # Preserve the primary exception for the backend failure latch.
                # Every secondary failure has already been recorded above.
                raise failures[0]
            self._modal_input_clear_pending = False
        finally:
            self._lock_input_clearing = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._lifecycle_owns_input():
            event.accept()
            return
        if not _pointer_button(event):
            # Back/forward/extra buttons must not act as left clicks or consume
            # a capture owned by one of the three supported buttons.
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
        hotspot = topmost_hotspot(self.hotspots, point, button=button)
        if hotspot is not None:
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
        hotspot = topmost_hotspot(self.hotspots, lambda: self.hover_point)
        if hotspot is not None:
            self.hover_tooltip = hotspot.tooltip
            hovered_action = hotspot.action
            if hovered_action == "economy_offer":
                hovered_offer = str(hotspot.payload)
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
            setting = edit_slider(lambda: self.pointer_interaction.drag, point)
            self.after_setting_changed(setting)
        elif self.pointer_interaction.drag["type"] == "jello_module":
            self.pointer_interaction.track_module_drag(point)
            self.invalidate_gui_cache()
            self.frame_scheduler.force()
        elif self.pointer_interaction.drag["type"] == "old_panel":
            panel = self.old_panels[self.pointer_interaction.drag["category"]]
            move_panel(panel, point, lambda: self.pointer_interaction.drag, mark_dragged=True)
        elif self.pointer_interaction.drag["type"] == "polar_window":
            panel = self.polar_windows[self.pointer_interaction.drag["category"]]
            move_panel(panel, point, lambda: self.pointer_interaction.drag)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._lifecycle_owns_input():
            event.accept()
            return
        if not _pointer_button(event):
            # Back/forward/extra buttons must not act as left clicks or consume
            # a capture owned by one of the three supported buttons.
            event.accept()
            return
        interaction = self.pointer_interaction
        if interaction.background_release_owned and event.button() == Qt.MouseButton.LeftButton:
            point = event.position()
            target = topmost_hotspot(self.hotspots, point, button=1)
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
        if self.economy_hold_key and _owns_economy_hold_release(self, ("mouse", self.qt_button(event))):
            self.finish_economy_hold(event.position())
        drag = self.pointer_interaction.drag
        if drag and drag.get("type") == "jello_module":
            # Module captures are acquired only by a left press. Releasing a
            # different held button must neither commit nor discard that drag.
            if event.button() != Qt.MouseButton.LeftButton:
                event.accept()
                return
            # Claim exactly once before callbacks: even a later toast/save
            # failure or nested release must not replay a toggle or drop.
            command = self.pointer_interaction.take_module_drag()
            name = command.name
            if command.moved:
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
        elif drag and drag.get("type") in {"old_panel", "polar_window"}:
            self.auto_save_config()
        # A callback may have started a new capture. Retire only the command
        # associated with this release, never its successor.
        if self.pointer_interaction.drag is drag:
            self.pointer_interaction.drag = None
        if self.combat_control_input_allowed():
            self.control.mock_output = bool(self.backend.mqtt.mock_output)
            self.control.set_mouse_button(self.qt_button(event), False, self.state, self.registry)
            event.accept()

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
        # Legacy duck hosts omit visibility; real windows always provide it.
        gui_visible = getattr(self, "gui_visible", True)
        if gui_visible and active_gui_mode == "PolarPanel" and self.panel_modules_rect.contains(point):
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
        if gui_visible and active_gui_mode == "PolarPanel" and self.panel_settings_rect.contains(point):
            key = self.selected_module.name
            self.settings_scroll[key] = max(0.0, self.settings_scroll.get(key, 0.0) + delta)
            self.frame_scheduler.force()
            event.accept()
            return
        # Cached geometry can outlive closing until the next render.
        scroll = scroll_target_at(self.gui_scroll_viewports, point) if gui_visible else None
        if scroll is not None:
            key, maximum = scroll.key, scroll.maximum
            if scroll.is_jello:
                # Sigma's literal 35-pixel step differs from retained Polar's
                # 28. Preserve the second live angle read on this path.
                jello_delta = -float(pixel_delta) if pixel_delta else -event.angleDelta().y() / 120.0 * 35.0
            if scroll.family is ScrollFamily.JELLO_DROPDOWN:
                self.jello_state.dropdown_scroll = max(
                    0.0, min(maximum, self.jello_state.dropdown_scroll + jello_delta)
                )
            elif scroll.family is ScrollFamily.JELLO_PANEL:
                current = float(self.jello_state.panel_scroll.get(key, 0.0))
                self.jello_state.panel_scroll[key] = max(0.0, min(maximum, current + jello_delta))
                self.jello_state.mark_scroll(f"panel:{key}")
            elif scroll.family is ScrollFamily.JELLO_SETTINGS:
                self.jello_state.scroll_settings_by(key, jello_delta, maximum)
                self.jello_state.mark_scroll(f"settings:{key}")
            elif scroll.family is ScrollFamily.LEGACY_PANEL:
                target = getattr(self, scroll.panel_attribute)[key]
                target["scroll"] = max(0.0, min(maximum, float(target.get("scroll", 0.0)) + delta))
            elif scroll.family is ScrollFamily.SETTINGS_MOTION:
                motion = self.polar_settings_scroll_motion.setdefault(key, InertialScroll())
                motion.set_bounds(maximum)
                motion.add_wheel(delta)
            else:
                self.polar_unified_scroll_motion.set_bounds(maximum)
                self.polar_unified_scroll_motion.add_wheel(delta)
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

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # A keyboard navigation/close action supersedes a pending mouse click,
        # but the GUI still owns that click's eventual motion and release.
        self.pointer_interaction.cancel_background_press()
        if not self.escape_menu_visible and not event.isAutoRepeat():
            # A new physical press supersedes any menu key-up lost on focus
            # change; its later release must reach the normal control owner.
            getattr(self, "_escape_menu_key_releases", set()).discard(event.key())
        if event.key() == Qt.Key.Key_F12 and not event.isAutoRepeat():
            modifiers = event.modifiers()
            release = bool(
                modifiers & Qt.KeyboardModifier.ControlModifier
                and modifiers & Qt.KeyboardModifier.ShiftModifier
            )
            if release and self._lifecycle_owns_input():
                event.accept()
                return
            if release:
                self.backend.release_estop()
                self.toast("E-STOP RELEASED — verify robot before control")
            else:
                result = request_emergency_stop(self.backend, self.control)
                if result.failures:
                    self.toast(result.message, kind="warning")
                else:
                    self.toast(result.message)
            event.accept()
            return
        if self._lifecycle_owns_input():
            event.accept()
            return
        if self.handle_escape_menu_key(event):
            return
        if self.pointer_interaction.binding_module is not None:
            self.handle_keybind_event(event)
            return
        if self.home_visible and self.wallpaper_picker_visible:
            if event.key() == Qt.Key.Key_Escape:
                self.set_wallpaper_picker_visible(False)
            return
        if self.home_visible:
            if self.handle_startup_key(event):
                return
        key_name = self.key_name(event)
        hero_module = self.registry.get("HeroLob")
        hero_key = bool(
            hero_module
            and hero_module.keybind != "None"
            and self.keybind_matches(hero_module.keybind, key_name, event)
        )
        if self.hero_deploy_ui.visible:
            if event.key() == Qt.Key.Key_F8 or self.is_right_shift_event(event):
                self.close_hero_deploy_interface(force=True)
                self.set_clickgui_visible(True)
            elif event.key() == Qt.Key.Key_Escape or (hero_key and not event.isAutoRepeat()):
                self.close_hero_deploy_interface()
            event.accept()
            return
        if hero_key and not event.isAutoRepeat() and not self.home_visible:
            if int(self.state.deploy_mode) == 1:
                self.open_hero_deploy_interface()
            elif hero_module is not None and not hero_module.enabled:
                self.registry.toggle_module(hero_module, source="keybind")
                self.auto_save_config()
            else:
                self.open_hero_deploy_interface()
            event.accept()
            return
        if self.handle_student_key(event, True):
            event.accept()
            return
        chat_module = self.registry.get("MatchChat")
        quick_wheel_module = self.registry.get("QuickActionWheel")
        chat_key = bool(
            chat_module
            and chat_module.enabled
            and chat_module.keybind != "None"
            and self.keybind_matches(chat_module.keybind, key_name, event)
        )
        wheel_key = bool(
            quick_wheel_module
            and quick_wheel_module.enabled
            and quick_wheel_module.keybind != "None"
            and self.keybind_matches(quick_wheel_module.keybind, key_name, event)
        )
        if self.match_chat.input_open:
            edit = _chat_key_edit(event)
            edit.apply(self.match_chat, close=lambda: self.close_match_chat(clear=False))
            self.frame_scheduler.force()
            event.accept()
            return
        if self.quick_action_wheel.active or self.overlay_coordinator.radial_active:
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_quick_action_wheel()
            event.accept()
            return
        if chat_key and not event.isAutoRepeat() and not self.home_visible:
            self.open_match_chat()
            event.accept()
            return
        if wheel_key and not event.isAutoRepeat() and not self.home_visible:
            local_point = self.mapFromGlobal(QCursor.pos())
            self.begin_quick_action_wheel(QPointF(local_point))
            event.accept()
            return
        economy_module = self.registry.get("EconomyPanel")
        projectile_module = self.registry.get("ProjectileShop")
        economy_key = bool(
            economy_module
            and economy_module.enabled
            and economy_module.keybind != "None"
            and self.keybind_matches(economy_module.keybind, key_name, event)
        )
        projectile_key = bool(
            projectile_module
            and projectile_module.enabled
            and projectile_module.keybind != "None"
            and self.keybind_matches(projectile_module.keybind, key_name, event)
        )
        if event.key() == Qt.Key.Key_Escape and self.economy_visible:
            self.close_economy_workspace()
            return
        if economy_key and not event.isAutoRepeat() and not self.home_visible:
            if self.economy_visible:
                self.close_economy_workspace()
            else:
                self.open_economy_workspace()
            return
        if projectile_key and not event.isAutoRepeat() and not self.home_visible:
            self.open_economy_workspace("ammo")
            return
        if self.economy_visible:
            categories = self.economy_categories()
            offers = self.economy_offers(self.economy_category)
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and categories:
                index = categories.index(self.economy_category) if self.economy_category in categories else 0
                index += -1 if event.key() == Qt.Key.Key_Left else 1
                self.economy_category = categories[index % len(categories)]
                self.economy_selected_key = ""
                self.reconcile_economy_selection()
                self.economy_offers_scroll.position = 0.0
                self.economy_offers_scroll.target = 0.0
                self.frame_scheduler.force()
                return
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and offers:
                selected_index = next((index for index, offer in enumerate(offers) if offer.key == self.economy_selected_key), 0)
                selected_index += -1 if event.key() == Qt.Key.Key_Up else 1
                self.select_economy_offer(offers[selected_index % len(offers)].key)
                return
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                offer = self.reconcile_economy_selection()
                if offer is not None and not event.isAutoRepeat():
                    if offer.confirmation == "hold":
                        _begin_economy_hold_from_input(self, offer, ("key", event.key()))
                    else:
                        self.activate_economy_offer(offer)
                return
        if self.search_focus:
            edit = _search_key_edit(event)
            if edit.finish:
                self.search_focus = False
            elif edit.changes_query:
                self.search_query = edit.apply(self.search_query)
            return
        if event.key() == Qt.Key.Key_F8 or self.is_right_shift_event(event):
            self.set_clickgui_visible(not self.gui_visible)
            return
        if event.key() == Qt.Key.Key_F9:
            # Jello Sigma is the only public ClickGUI. Legacy renderers are
            # deliberately reachable only from ClickGui -> Old ClickGUIs.
            event.accept()
            return
        if event.key() == Qt.Key.Key_F7 and not event.isAutoRepeat() and not self.home_visible:
            # Referee event commands (rune/dart/sentry/assembly/performance).
            # MapClickCmd has its own gesture: click inside the Tab-expanded
            # tactical map.
            self.open_event_command_menu()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.gui_visible:
            if self.jello_state.settings_module is not None:
                self.jello_state.close_settings()
                self.frame_scheduler.force()
                event.accept()
                return
            if self.jello_state.legacy_mode is not None:
                self.return_to_jello_clickgui()
                event.accept()
                return
            self.set_clickgui_visible(False)
            event.accept()
            return
        if self.overlay_coordinator.blocks_combat_input:
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and not self.home_visible:
            if not event.isAutoRepeat():
                self.set_escape_menu_visible(True)
            event.accept()
            return
        if self.minecraft_hud_active() and not event.isAutoRepeat():
            number_keys = {
                Qt.Key.Key_1: 0,
                Qt.Key.Key_2: 1,
                Qt.Key.Key_3: 2,
                Qt.Key.Key_4: 3,
                Qt.Key.Key_5: 4,
                Qt.Key.Key_6: 5,
                Qt.Key.Key_7: 6,
                Qt.Key.Key_8: 7,
                Qt.Key.Key_9: 8,
            }
            slot = number_keys.get(event.key())
            if slot is not None and self.activate_minecraft_function_slot(slot):
                event.accept()
                return
        if self.handle_tactical_map_hold_event(event, True):
            return
        if self.handle_control_key_event(event, True):
            # Keys such as X/G/V are valid official KMC bits and are also
            # configurable local module binds.  Preserve both semantics rather
            # than swallowing the module toggle after the KMC frame is sent.
            self.toggle_bound_module_for_event(event, key_name)
            return
        if self.toggle_bound_module_for_event(event, key_name):
            return

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if self._lifecycle_owns_input():
            event.accept()
            return
        pending = getattr(self, "_escape_menu_key_releases", set())
        if event.key() in pending:
            if not event.isAutoRepeat():
                pending.discard(event.key())
            event.accept()
            return
        if self.escape_menu_visible or event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        if self.handle_student_key(event, False):
            event.accept()
            return
        if self.pointer_interaction.binding_module is not None or self.search_focus:
            return
        quick_wheel_module = self.registry.get("QuickActionWheel")
        key_name = self.key_name(event)
        wheel_key = bool(
            quick_wheel_module
            and quick_wheel_module.enabled
            and quick_wheel_module.keybind != "None"
            and self.keybind_matches(quick_wheel_module.keybind, key_name, event)
        )
        if self.quick_action_wheel.active or self.overlay_coordinator.radial_active:
            if wheel_key and not event.isAutoRepeat():
                self.finish_quick_action_wheel()
            event.accept()
            return
        if self.match_chat.input_open:
            event.accept()
            return
        if self.economy_visible and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Auto-repeat may include synthetic key-up events while the physical
            # key is still held. Only its real release completes confirmation.
            if not event.isAutoRepeat() and _owns_economy_hold_release(self, ("key", event.key())):
                self.finish_economy_hold()
            return
        if self.overlay_coordinator.blocks_combat_input:
            event.accept()
            return
        if self.handle_tactical_map_hold_event(event, False):
            return
        self.handle_control_key_event(event, False)

    def handle_tactical_map_hold_event(self, event: QKeyEvent, pressed: bool) -> bool:
        module = self.registry.get("TacticalMap")
        if module is None:
            return False
        key_name = self.key_name(event)
        is_tab_view_key = event.key() == Qt.Key.Key_Tab
        is_bound_view_key = module.keybind != "None" and self.keybind_matches(module.keybind, key_name, event)
        if not is_tab_view_key and not is_bound_view_key:
            return False
        if not event.isAutoRepeat():
            self.tactical_map_hold = pressed and module.enabled
        return True

    def handle_control_key_event(self, event: QKeyEvent, pressed: bool) -> bool:
        if not self.combat_control_input_allowed():
            return False
        if event.isAutoRepeat():
            return False
        key = self.key_name(event)
        self.control.mock_output = bool(self.backend.mqtt.mock_output)
        # 走绑定层：用户可能把“前进”改绑到↑等任意键。上位机下发的永远是
        # 协议槽位，所以改绑不影响固件侧语义，也不影响驾驶键掩码。
        slots = self.control.resolve_slot(key) or ()
        if not slots:
            return False
        command = None
        for slot in slots:
            result = self.control.set_key(slot, pressed, self.state, self.registry)
            if result is not None:
                command = result
        if command is None:
            return False
        if pressed and time.perf_counter() - self.last_control_toast > 1.2:
            self.toast("WASD -> 模拟底盘命令" if command.mock_output else "WASD -> 底盘命令已发送")
            self.last_control_toast = time.perf_counter()
        return True

    def combat_control_input_allowed(self) -> bool:
        """Only the unobstructed combat viewport owns robot key/mouse input."""

        return bool(
            not self._lifecycle_owns_input()
            and not self.escape_menu_visible
            and not self.student_ui.visible
            and not self.home_visible
            and not self.gui_visible
            and getattr(getattr(self, "gui_timeline", None), "progress", 0.0) <= 0.0
            and not self.economy_visible
            and not self.hero_deploy_ui.visible
            and not self.match_chat.input_open
            and not self.quick_action_wheel.active
            and not self.overlay_coordinator.blocks_combat_input
        )

    def toggle_bound_module_for_event(self, event: QKeyEvent, key_name: str) -> bool:
        if self._lifecycle_owns_input():
            return False
        if event.isAutoRepeat():
            return False
        for module in self.registry.modules:
            if module.keybind != "None" and self.keybind_matches(module.keybind, key_name, event):
                self.registry.toggle_module(module, source="keybind")
                self.selected_module = module
                self.toast(f"{module.name} {'已启用' if module.enabled else '已关闭'}")
                self.auto_save_config()
                return True
        return False

    def publish_spin_control_state(
        self,
        enabled: bool,
        *,
        source: str,
        force_live_disable: bool = False,
    ) -> tuple[bool, str]:
        """Publish one fail-closed team spin state, never a fire command."""
        spin = self.registry.get("SpinMode")
        if spin is None:
            return False, "SpinMode unavailable"
        mock = self.setting_bool(spin, "Mock Output", True)
        if mock and not force_live_disable:
            result = (True, "mock")
        else:
            self._spin_control_sequence = (self._spin_control_sequence + 1) & 0xFFFF
            result = self.backend.publish_spin_control(
                sequence=self._spin_control_sequence,
                enabled=bool(enabled),
                direction=self.setting_mode(spin, "Direction", "Auto"),
                speed_dps=self.get_setting_number(spin, "Spin Speed", 420.0),
                power_guard=self.setting_bool(spin, "Power Guard", True),
            )
        ok, reason = result
        self.state.backend_stats["spin_control"] = {
            "published": bool(ok and not mock),
            "requested_enabled": bool(enabled),
            "mock": bool(mock and not force_live_disable),
            "reason": str(reason),
            "source": str(source),
            "fire": False,
        }
        if not ok:
            self.state.events.appendleft(f"Spin output blocked: {reason}")
        return result

    def handle_keybind_event(self, event: QKeyEvent) -> None:
        module = self.pointer_interaction.binding_module
        if module is None:
            return
        if event.key() == Qt.Key.Key_Escape:
            self.pointer_interaction.finish_binding("None")
            self.anim.value("keybind.overlay", 0.0, self.anim_duration(0.16), Easings.ease_out_quad)
            self.toast(f"{module.name} 按键绑定已清空")
            self.auto_save_config()
            return
        name = self.key_name(event)
        if not name:
            return
        self.pointer_interaction.finish_binding(name)
        self.toast(f"{module.name} 已绑定到 {name}")
        self.auto_save_config()

    # Runtime ownership is checked before student actions and cache invalidation.
    # Unknown actions intentionally do nothing. This remains a window adapter,
    # not a pure command service: handlers may mutate model and UI state.
    def dispatch(self, action: str, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
        if self._lifecycle_owns_input():
            # Scenario requests may explicitly coalesce while native owners
            # retire. An accepted profile or final close still owns the model.
            changing_profile = bool(getattr(self, "configuration_change_pending", False))
            closing = bool(getattr(getattr(self, "_window_lifecycle", None), "closing", False))
            if action != "select_scenario" or changing_profile or closing:
                return
        if self.handle_student_action(action, payload, point):
            return
        if action in _GUI_CACHE_INVALIDATE_ACTIONS:
            self.invalidate_gui_cache()
        handler = _DISPATCH_HANDLERS.get(action)
        if handler is None:
            return
        handler(self, payload, point, button, rect)

    def qt_button(self, event: QMouseEvent) -> int:
        return _pointer_button(event)

    def key_name(self, event: QKeyEvent) -> str:
        key = event.key()
        special = {
            Qt.Key.Key_Space: "Space",
            Qt.Key.Key_Tab: "Tab",
            Qt.Key.Key_Backspace: "Backspace",
            Qt.Key.Key_Return: "Enter",
            Qt.Key.Key_Enter: "Enter",
            Qt.Key.Key_Escape: "Escape",
            Qt.Key.Key_Shift: "RightShift" if self.is_right_shift_event(event) else "Shift",
            Qt.Key.Key_Control: "Ctrl",
            Qt.Key.Key_Alt: "Alt",
        }
        if key in special:
            return special[key]
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35:
            return f"F{key - Qt.Key.Key_F1 + 1}"
        text = event.text()
        if text and text.isprintable():
            return text.upper()
        seq = QKeySequence(key).toString()
        return seq or ""

    def keybind_matches(self, bind: str, key_name: str, event: QKeyEvent) -> bool:
        normalized_bind = bind.strip().lower()
        normalized_key = key_name.strip().lower()
        if normalized_bind == normalized_key:
            return True
        if normalized_bind == "tab" and event.key() == Qt.Key.Key_Tab:
            return True
        if normalized_bind == "rightshift" and self.is_right_shift_event(event):
            return True
        return False

    def is_right_shift_event(self, event: QKeyEvent) -> bool:
        if event.key() != Qt.Key.Key_Shift:
            return False
        if event.nativeVirtualKey() == 161:
            return True
        # PySide/Qt 可能把左右 Shift 都上报为 VK_SHIFT(16)。
        # 在 Windows 上，右 Shift 通常仍会保留 0x36 硬件扫描码。
        if event.nativeScanCode() == 0x36:
            return True
        return False


# ----------------------------------------------------------------------
# __qualname__ 钉回（api_freeze_test 对方法记录 __qualname__ 并参与比对，
# 见 tests/api_freeze_test.py _member_entry）：全部迁入方法钉回
# PolarRMWindow.<name>，冻结面签名不变。
# ----------------------------------------------------------------------

_QUALNAME_PINNED_MEMBERS = (
    InputDispatchMixin._clear_control_inputs_for_lock,
    InputDispatchMixin.mousePressEvent,
    InputDispatchMixin.mouseMoveEvent,
    InputDispatchMixin.mouseReleaseEvent,
    InputDispatchMixin.wheelEvent,
    InputDispatchMixin.keyPressEvent,
    InputDispatchMixin.keyReleaseEvent,
    InputDispatchMixin.handle_tactical_map_hold_event,
    InputDispatchMixin.handle_control_key_event,
    InputDispatchMixin.combat_control_input_allowed,
    InputDispatchMixin.toggle_bound_module_for_event,
    InputDispatchMixin.publish_spin_control_state,
    InputDispatchMixin.handle_keybind_event,
    InputDispatchMixin.dispatch,
    InputDispatchMixin.qt_button,
    InputDispatchMixin.key_name,
    InputDispatchMixin.keybind_matches,
    InputDispatchMixin.is_right_shift_event,
)

for _fn in _QUALNAME_PINNED_MEMBERS:
    _fn.__qualname__ = f"PolarRMWindow.{_fn.__name__}"
del _fn, _QUALNAME_PINNED_MEMBERS
