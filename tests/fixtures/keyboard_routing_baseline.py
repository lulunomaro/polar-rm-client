"""Frozen keyboard-routing reference, copied before editor-policy extraction.

Source: 8cb9667cf2c156d7e6634551f691b2b487ba855e
Path: rm_client/ui/polar/mixins/input_dispatch_mixin.py
The two method bodies below are byte-for-byte source slices. Do not regenerate
this reference from production code to make a behavior change pass.
"""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QCursor, QKeyEvent
from rm_client.emergency_actions import request_emergency_stop


class KeyboardRoutingBaseline:
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
            if event.key() == Qt.Key.Key_Escape:
                self.close_match_chat(clear=False)
            elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.match_chat.send_input()
                self.close_match_chat(clear=False)
            elif event.key() == Qt.Key.Key_Backspace:
                self.match_chat.backspace()
            elif event.key() == Qt.Key.Key_Up:
                self.match_chat.history_older()
            elif event.key() == Qt.Key.Key_Down:
                self.match_chat.history_newer()
            elif event.key() == Qt.Key.Key_PageUp:
                self.match_chat.scroll(5)
            elif event.key() == Qt.Key.Key_PageDown:
                self.match_chat.scroll(-5)
            elif event.text() and event.text().isprintable():
                self.match_chat.insert(event.text())
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
                self.economy_selected_offer()
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
                offer = self.economy_selected_offer()
                if offer is not None and not event.isAutoRepeat():
                    if offer.confirmation == "hold":
                        self.begin_economy_hold(offer)
                    else:
                        self.activate_economy_offer(offer)
                return
        if self.search_focus:
            if event.key() == Qt.Key.Key_Backspace:
                self.search_query = self.search_query[:-1]
            elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.search_focus = False
            elif event.key() == Qt.Key.Key_Escape:
                self.search_focus = False
            elif event.text() and event.text().isprintable():
                self.search_query += event.text()
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
            self.finish_economy_hold()
            return
        if self.overlay_coordinator.blocks_combat_input:
            event.accept()
            return
        if self.handle_tactical_map_hold_event(event, False):
            return
        self.handle_control_key_event(event, False)
