"""Student-client frontend input/scene integration; deliberately no publishers."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt

from ....student_ui import settlement_rows
from ...overlay_coordinator import OverlayKind


class StudentFrontendMixin:
    def open_student_panel(self, panel: str) -> None:
        if panel not in {"reload", "damage", "settlement", "darts", "skills", "inventory"}:
            return
        if self.home_visible:
            self.toast("先进入战斗界面，再打开学生面板")
            return
        if panel == "reload":
            module=self.registry.get("ReloadingPanel")
            self.student_ui.gate=self.setting_mode(module,"Gate Draft","Left")
            self.student_ui.quantity=int(self.get_setting_number(module,"Quantity Draft",50))
            self.open_economy_workspace("ammo")
            self.student_reload_expanded = True
            return
        if self.hero_deploy_video_cut_active():
            self.open_hero_deploy_interface()
            return
        if self.economy_phase.value != "closed":
            self._student_pending_panel = panel
            self._economy_pending_overlay = OverlayKind.STUDENT
            if self.economy_phase.value != "closing":
                self.close_economy_workspace(play_sound=False)
            return
        if self.hero_deploy_ui.visible:
            self.close_hero_deploy_interface(force=True)
        if self.gui_visible:
            self.set_clickgui_visible(False, announce=False, play_sound=False)
        self.close_match_chat(clear=False)
        self.cancel_quick_action_wheel()
        # Release live inputs on the opening event, not a later Qt key-up.
        self.overlay_coordinator.request_modal(OverlayKind.STUDENT)
        self.student_ui.open(panel)
        if panel == "settlement":
            self.student_ui.settlement = settlement_rows(self.state)
        self.hotspots.clear()
        self.invalidate_transient_overlay_scene()
        self.frame_scheduler.force()
        self.update()

    def close_student_panel(self, immediate: bool = False) -> None:
        self._student_pending_panel = None
        if self._economy_pending_overlay == OverlayKind.STUDENT:
            self._economy_pending_overlay = None
        if not self.student_ui.visible and not self.student_ui.panel:
            return
        self.student_ui.close(immediate)
        self.hotspots.clear()
        if immediate:
            self.overlay_coordinator.close_modal(OverlayKind.STUDENT)
        self.invalidate_transient_overlay_scene()
        self.frame_scheduler.force()
        self.update()

    def update_student_frontend(self, dt: float) -> None:
        state = self.student_ui
        if self.home_visible and state.visible:
            self.close_student_panel(immediate=True)
        # The backend cache is keyed by killer, not reception time. Observe
        # replaced report objects so returning attackers don't show stale rows.
        reports = self.state.injury_stats
        previous = getattr(self, "_student_injury_reports", {})
        changed = [report for key, report in reports.items() if isinstance(report,dict) and previous.get(key) is not report]
        if changed:
            matching = [r for r in changed if
                r.get("small_projectile_damage") == self.state.projectile_damage_small_total and
                r.get("large_projectile_damage") == self.state.projectile_damage_large_total]
            if len(matching or changed) == 1:
                self.state.student_ui_data["injury_snapshot"] = dict((matching or changed)[0])
            else:
                # No reception ordering is available for ambiguous batches.
                self.state.student_ui_data["injury_snapshot"] = {}
        if not reports:
            self.state.student_ui_data.pop("injury_snapshot", None)
        self._student_injury_reports = dict(reports)
        was_visible = state.visible
        state.update(dt)
        if was_visible and not state.visible:
            self.overlay_coordinator.close_modal(OverlayKind.STUDENT)
            self.hotspots.clear()
            self.invalidate_transient_overlay_scene()
        if state.opened and self.overlay_coordinator.modal != OverlayKind.STUDENT:
            self.close_student_panel(immediate=True)
        stage = str(self.state.current_stage)
        module = self.registry.get("MatchSettlement")
        if stage == "SETTLEMENT" and state.previous_stage == "MATCH" and module and module.enabled and self.setting_bool(module, "Auto Open", True):
            self.open_student_panel("settlement")
        if stage != "SETTLEMENT" and state.previous_stage == "SETTLEMENT" and state.panel != "settlement":
            state.settlement = ()
        state.previous_stage = stage

    def handle_student_config_action(self, name: str) -> bool:
        actions = {"Open Injury Statistics": "damage", "Open Settlement": "settlement", "Open Dart Panel": "darts",
                   "Open Skill Configuration": "skills", "Open Minecraft Inventory": "inventory", "Open Reloading Panel": "reload"}
        if name not in actions:
            return False
        self.open_student_panel(actions[name])
        return True

    def handle_student_key(self, event, pressed: bool) -> bool:
        if self.home_visible:
            return False
        state = self.student_ui
        grave = event.key() in (Qt.Key.Key_QuoteLeft, Qt.Key.Key_AsciiTilde)
        if not pressed:
            if grave and event.isAutoRepeat():
                return state.visible
            if grave and self._student_pending_panel == "damage":
                self._student_pending_panel = None
                self._economy_pending_overlay = None
                return True
            if grave and state.panel == "damage":
                self.close_student_panel()
                return True
            return state.visible
        if state.visible:
            if not event.isAutoRepeat():
                if event.key() == Qt.Key.Key_Escape or (event.key() == Qt.Key.Key_E and state.panel == "inventory"):
                    self.close_student_panel()
                elif event.key() == Qt.Key.Key_F8 or self.is_right_shift_event(event):
                    self.close_student_panel(immediate=True)
                    self.set_clickgui_visible(True)
                elif state.panel == "darts":
                    if event.key() == Qt.Key.Key_J:
                        targets=("前哨站","基地","随机固定目标")
                        state.dart_target=targets[(targets.index(state.dart_target)+1)%len(targets)]
                    elif event.key() in (Qt.Key.Key_F,Qt.Key.Key_L):
                        state.note="已保存飞镖操作草稿；本轮仅前端，未向裁判或机器人发送"
                elif state.panel == "inventory" and Qt.Key.Key_1 <= event.key() <= Qt.Key.Key_9:
                    state.selected_slot = event.key() - Qt.Key.Key_1
            self.frame_scheduler.force()
            return True
        if self.overlay_coordinator.blocks_combat_input or self.gui_timeline.progress > 0.01:
            return False
        if event.isAutoRepeat():
            return False
        if grave and self.registry.get("InjuryStatistics").enabled:
            self.open_student_panel("damage")
            return True
        if event.key()==Qt.Key.Key_E and self.minecraft_hud_active() and self.registry.get("MinecraftInventory").enabled:
            self.open_student_panel("inventory")
            return True
        if event.key() in (Qt.Key.Key_O,Qt.Key.Key_I) and self.registry.get("ReloadingPanel").enabled:
            self.open_student_panel("reload")
            return True
        if event.key()==Qt.Key.Key_F and self.state.robot_profile=="Dart" and self.registry.get("DartPanel").enabled:
            self.open_student_panel("darts")
            return True
        if event.key() in (Qt.Key.Key_F1,Qt.Key.Key_Y) and self.state.current_health<=0:
            self.open_economy_workspace("revive")
            # Hotkeys select the offer; they never bypass hold/double-confirm.
            offers=self.economy_offers("revive")
            free=event.key()==Qt.Key.Key_Y
            offer_key="rmuc_confirm_respawn" if free else "rmuc_gold_respawn"
            selected=next((o for o in offers if o.key==offer_key and not o.status_only),None)
            if selected:self.select_economy_offer(selected.key)
            return True
        return False

    def handle_student_mouse(self, event, phase: str) -> bool:
        state=self.student_ui
        if not state.visible:
            return False
        point=event.position()
        self.hover_point=QPointF(point)
        if phase=="move":
            self.hover_tooltip=next((h.tooltip for h in reversed(self.hotspots) if h.rect.contains(point)),"")
        elif phase=="press" and state.opened:
            button=self.qt_button(event)
            hit=next((h for h in reversed(self.hotspots) if h.action.startswith("student_") and h.rect.contains(point) and h.button in (None,button)),None)
            if hit is not None:
                if button == 1 or hit.action == "student_clear_slot":
                    self.handle_student_action(hit.action,hit.payload,point)
        elif phase=="release" and state.dragging and event.button()==Qt.MouseButton.LeftButton:
            hit=next((h for h in self.hotspots if h.action=="student_slot" and h.rect.contains(point)),None)
            if hit:self.assign_minecraft_function_slot(int(hit.payload),state.dragging)
            state.dragging=""
        self.frame_scheduler.force()
        event.accept()
        return True

    def handle_student_action(self, action: str, payload, point=None) -> bool:
        if not action.startswith("student_"):
            return False
        state=self.student_ui
        if action=="student_close":self.close_student_panel()
        elif action=="student_open":self.open_student_panel(str(payload))
        elif action=="student_inventory_item":
            state.selected=str(payload);state.dragging=str(payload)
        elif action=="student_slot":
            state.selected_slot=int(payload)
            if state.selected:self.assign_minecraft_function_slot(int(payload),state.selected)
        elif action=="student_clear_slot":
            setting=self.registry.get("MinecraftCombatHUD").get_setting(f"Slot {int(payload)+1}")
            setting.value="Empty";self.after_setting_changed(setting)
        elif action=="student_category":state.category=str(payload);state.page=0
        elif action=="student_page":
            maximum=max(0,(len(self.registry.by_category(state.category))-1)//27)
            state.page=max(0,min(maximum,state.page+int(payload)))
        elif action=="student_dart_target":state.dart_target=str(payload)
        elif action=="student_draft":
            state.note="已保存操作草稿；本轮仅前端，未下发任何命令"
            self.toast(state.note)
        elif action=="student_skill":
            name,delta=payload;setting=self.registry.get("SkillConfiguration").get_setting(name)
            if setting.kind=="number":setting.value=max(setting.min_value,min(setting.max_value,float(setting.value)+delta))
            else:setting.cycle(delta)
            self.after_setting_changed(setting)
            state.note="性能草稿已保存到配置；已同步状态未改变"
        elif action=="student_reload_toggle":self.student_reload_expanded=not self.student_reload_expanded
        elif action=="student_gate":
            state.gate=str(payload);setting=self.registry.get("ReloadingPanel").get_setting("Gate Draft")
            setting.value=state.gate;self.after_setting_changed(setting)
        elif action=="student_quantity":
            state.quantity=max(1,min(500,state.quantity+int(payload)))
            setting=self.registry.get("ReloadingPanel").get_setting("Quantity Draft")
            setting.value=state.quantity;self.after_setting_changed(setting)
        self.frame_scheduler.force()
        return True

    def draw_student_frontend(self, p, width: int, height: int) -> None:
        self.student_renderer.draw(self,p,width,height)

    def draw_student_status(self, p, width: int, height: int) -> None:
        self.student_renderer.draw_status(self,p,width,height)
