"""Economy workspace adapters and interaction/transport entry points.

Confirmation values live in economy_presentation; selection resolution lives
in economy_selection. The drawing-only economy_content functions receive
explicit values, geometry, and narrow font/paint/locale/category capabilities.
This adapter retains state reads and writes, selection reconciliation, clocks,
scroll/animation updates, hotspot registration, and the transaction bridge.
Responsive geometry and lens calibration remain in economy_layout.
It does not grant confirmation or transport permission from presentation state.

The close/restore generation protocol still coordinates with the main window:
only a successfully presented normal HUD finalizes an economy close. Public
window entry-point signatures and qualnames remain compatible.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPainterPath

# 相对导入层级：rm_client/ui/polar/mixins/ → .. = polar、... = rm_client.ui、.... = rm_client
from ...animation import Easings
from ...jello_locale import chinese_enabled
from ...economy_locale import economy_text
from ...clickgui_gpu import ClickGuiDrawList
from ...gpu_frame_compositor import LensMaterial
from ...overlay_coordinator import OverlayKind
from ...paint import rounded_rect, text_width
from ...theme import THEME, color
from ...student_reload import append_reload_details, reload_detail_height, paint_reload_commands
from ...cs2_feedback import BUY_HOVER_SECONDS, _css_ease_in_out, append_buy_feedback, buy_enabled, play_buy_sound
from ...cs2_buy_menu import build_grid, grid_enabled
from ...economy_glass import draw_economy_glass, draw_economy_glass_capsule, draw_economy_glass_well
from ....economy_values import CATEGORY_TITLES, EconomyContext, EconomyOffer
from ....economy import EconomyRequest, available_actions
from ....robot_profiles import profile_by_key
from ..primitives import EconomyScenePhase
from ...economy_presentation import EconomyConfirmationState
from ...economy_selection import selected_offer
from ...economy_interaction import (
    ActivationAction, HoldCompletion, complete_economy_hold,
    plan_economy_activation, unavailable_offer_reason,
)
from ...economy_content import (
    EconomyContentStyle,
    EconomyPaintPrimitives,
    EconomyTransactionText,
    append_gpu_status,
    append_gpu_categories,
    append_gpu_offer,
    append_gpu_offer_detail,
    append_gpu_transaction,
    append_gpu_confirmation,
    paint_economy_wallet,
    paint_economy_status,
    paint_economy_categories,
    paint_economy_offer,
    paint_economy_offer_detail,
    paint_economy_transaction,
    paint_economy_confirmation,
)
from ...economy_layout import (
    compat_economy_layout,
    economy_detail_scroll_maximum,
    economy_header_tile,
    economy_lens_plan,
    economy_module_geometry,
)

__all__ = [
    "EconomyMixin",
]


class EconomyMixin:
    """D6 经济工作区（数据/传输 + legacy/compat 绘制 + GPU 工作区构建 + 开合状态机/交互）。"""

    def draw_economy_overlay_legacy(self, p: QPainter, width: int, height: int, alpha: float) -> None:
        actions = available_actions(self.state)
        if not actions:
            return
        self.economy_selected = max(0, min(len(actions) - 1, self.economy_selected))
        scale = max(0.78, min(1.18, self.get_auto_ui_scale(width, height, strength=0.34)))
        p.save()
        p.fillRect(QRectF(0, 0, width, height), color("#02050a", 0.34 * alpha))

        island_w = min(620 * scale, width * 0.54)
        collapsed_w = 210 * scale
        morph = Easings.ease_out_expo(max(0.0, min(1.0, alpha)))
        current_w = collapsed_w + (island_w - collapsed_w) * morph
        island = QRectF(width / 2 - current_w / 2, 34 * scale, current_w, 52 * scale)
        self.draw_liquid_glass_panel(p, island, 22 * scale, accent=THEME.cyan, hover=0.25, scale=scale, surface="ModalGlass")
        self.draw_glass_text(p, island.adjusted(22 * scale, 0, -22 * scale, 0), economy_text("ECONOMY  ·  {gold} GOLD", chinese_enabled(self.registry), gold=self.state.remaining_economy), self.font("poppins_bold", 10 * scale), THEME.text, Qt.AlignmentFlag.AlignCenter)

        panel_w = min(1080 * scale, width - 70 * scale)
        panel_h = min(520 * scale, height - 150 * scale)
        panel = QRectF(width / 2 - panel_w / 2, 108 * scale + (1.0 - morph) * 24 * scale, panel_w, panel_h)
        self.draw_liquid_glass_panel(p, panel, 20 * scale, accent=THEME.cyan, hover=0.12, scale=scale, surface="ModalGlass")
        self.draw_glass_text(p, panel.adjusted(28 * scale, 20 * scale, -28 * scale, -panel.height() + 54 * scale), "战斗经济操作", self.font("poppins_bold", 16 * scale), THEME.text)
        profile = profile_by_key(self.match_mode_manager.current.key, self.current_robot_profile_key())
        self.draw_glass_text(p, panel.adjusted(28 * scale, 54 * scale, -28 * scale, -panel.height() + 76 * scale), economy_text("{match} · {profile} · 默认 MOCK", chinese_enabled(self.registry), match=self.match_mode_manager.current.title, profile=profile.title), self.font("poppins_regular", 8 * scale), THEME.muted)

        count = len(actions)
        columns = min(3, max(1, count))
        gap = 14 * scale
        cards_left = panel.left() + 26 * scale
        cards_top = panel.top() + 96 * scale
        cards_w = panel.width() - 52 * scale
        card_w = (cards_w - gap * (columns - 1)) / columns
        card_h = 128 * scale
        for index, action in enumerate(actions):
            row, column = divmod(index, columns)
            rect = QRectF(cards_left + column * (card_w + gap), cards_top + row * (card_h + gap), card_w, card_h)
            selected = index == self.economy_selected
            hovered = rect.contains(self.hover_point)
            hover = self.anim.value(f"economy.hover.{action.key}", 1.0 if hovered else 0.0, self.anim_duration(0.14), Easings.ease_out_expo)
            accent = THEME.cyan if action.enabled else color("#9aa3aa", 0.62)
            self.draw_liquid_glass_panel(p, rect.translated(0, -3 * scale * hover), 13 * scale, accent=accent, hover=max(hover, 0.48 if selected else 0.0), scale=scale, surface="SubGlass")
            title_color = THEME.text if action.enabled else color("#b8bec3", 0.74)
            self.draw_glass_text(p, rect.adjusted(16 * scale, 14 * scale, -16 * scale, -rect.height() + 38 * scale), action.short_title, self.font("poppins_bold", 11 * scale), title_color)
            self.draw_glass_text(p, rect.adjusted(16 * scale, 42 * scale, -16 * scale, -rect.height() + 65 * scale), action.title, self.font("poppins_medium", 8 * scale), title_color)
            self.draw_glass_text(p, rect.adjusted(16 * scale, 76 * scale, -16 * scale, -15 * scale), economy_text("{cost} GOLD  ·  {unit}", chinese_enabled(self.registry), cost=action.cost, unit=action.unit), self.font("poppins_bold", 8 * scale), accent)
            if not action.enabled:
                self.draw_glass_text(p, rect.adjusted(16 * scale, 98 * scale, -16 * scale, -5 * scale), action.reason or "当前不可用", self.font("poppins_regular", 6.5 * scale), color("#ffb66e", 0.82))
            self.add_hotspot(rect, "economy_select", index, tooltip=action.reason or action.title)

        footer = QRectF(panel.left() + 26 * scale, panel.bottom() - 66 * scale, panel.width() - 52 * scale, 42 * scale)
        submit = QRectF(footer.right() - 184 * scale, footer.top(), 184 * scale, footer.height())
        close = QRectF(footer.left(), footer.top(), 104 * scale, footer.height())
        self.draw_liquid_glass_panel(p, close, 13 * scale, accent=color("#ffffff", 0.66), hover=0.0, scale=scale, surface="SubGlass")
        self.draw_glass_text(p, close, economy_text("CLOSE  [B]", chinese_enabled(self.registry)), self.font("poppins_bold", 7.5 * scale), THEME.text, Qt.AlignmentFlag.AlignCenter)
        selected_action = actions[self.economy_selected]
        submit_accent = THEME.cyan if selected_action.enabled else color("#8a9298", 0.58)
        self.draw_liquid_glass_panel(p, submit, 13 * scale, accent=submit_accent, hover=0.35 if selected_action.enabled else 0.0, scale=scale, surface="ProminentGlass")
        self.draw_glass_text(p, submit, economy_text("SEND MOCK REQUEST", chinese_enabled(self.registry)), self.font("poppins_bold", 8 * scale), THEME.text if selected_action.enabled else THEME.muted, Qt.AlignmentFlag.AlignCenter)
        self.add_hotspot(close, "economy_close")
        self.add_hotspot(submit, "economy_submit", self.economy_selected)
        p.restore()

    @staticmethod
    def economy_scale_steps() -> tuple[float, ...]:
        """Stable font-page scales used by the responsive economy workspace."""

        return (0.82, 0.90, 1.00, 1.05, 1.10)

    def economy_ui_scale(self, width: int, height: int) -> float:
        requested = max(
            0.82,
            min(
                1.10,
                self.get_auto_ui_scale(
                    max(1, width),
                    max(1, height),
                    strength=0.20,
                ),
            ),
        )
        return min(
            self.economy_scale_steps(),
            key=lambda candidate: abs(candidate - requested),
        )

    def economy_uses_mock_transport(self) -> bool:
        module = self.registry.get("EconomyPanel")
        force_mock = self.setting_bool(module, "Mock Requests", True) if module else True
        return force_mock or self.scenario_manager.current.key != "OfficialEvent"

    def send_economy_request(self, request: EconomyRequest) -> bool:
        """Send a confirmed economy request without mutating local match state."""
        if request.mock:
            return True
        if self.scenario_manager.current.key != "OfficialEvent":
            self.mqtt.last_error = "仅官方赛事场景允许发送正式经济请求"
            return False
        qualification_token = getattr(self, "_economy_qualification_token", None)
        if qualification_token is not None:
            # A qualification capability authorises one supervised attempt,
            # not a 30-second burst of economy commands. Consume the UI copy
            # before publishing and destroy the backend capability immediately
            # afterwards, irrespective of broker acceptance.
            self._economy_qualification_token = None
        try:
            if request.topic == "AirSupportCommand":
                return self.mqtt.publish_air_support_command(
                    request.payload,
                    qualification_token=qualification_token,
                )
            return self.mqtt.publish_common_command(
                request.payload,
                qualification_token=qualification_token,
            )
        finally:
            if qualification_token is not None:
                self.backend.lock_control("economy qualification command attempted")

    def economy_offers(self, category: str | None = None) -> list[EconomyOffer]:
        offers = available_actions(self.state)
        labels = {"rmuc_confirm_respawn": "免费复活 [Y]", "rmuc_gold_respawn": "买活 [F1]"}
        offers = [replace(offer, title=labels[offer.key], short_title=labels[offer.key])
                  if offer.key in labels else offer for offer in offers]
        if category is None:
            return offers
        return [offer for offer in offers if offer.category == category]

    def economy_categories(self, offers: list[EconomyOffer] | None = None) -> list[str]:
        offers = self.economy_offers() if offers is None else offers
        present = {offer.category for offer in offers}
        return [key for key in CATEGORY_TITLES if key in present]

    def selected_economy_offer(self) -> EconomyOffer | None:
        """Read the effective offer without normalizing any window fields."""
        offers = self.economy_offers(self.economy_category)
        return selected_offer(offers, self.economy_selected_key)

    def reconcile_economy_selection(self) -> EconomyOffer | None:
        """Commit a missing selection's fallback, preserving a valid index."""
        offer = self.selected_economy_offer()
        if offer is not None and offer.key != self.economy_selected_key:
            self.economy_selected_key = offer.key
            self.economy_selected = 0
        return offer

    def economy_selected_offer(self) -> EconomyOffer | None:
        """Legacy read-and-normalize API; new callers use the explicit command.

        Keep the historical fallback mutation for external/dynamic callers.
        Use selected_economy_offer for an observation-only query.
        """
        return self.reconcile_economy_selection()

    def _finalize_economy_restore(self, generation: int) -> None:
        """Commit a close only after the rebuilt normal HUD was presented."""

        if (
            self.economy_phase != EconomyScenePhase.CLOSING
            or generation != self._economy_generation
            or self._economy_restore_pending_generation != generation
        ):
            return
        self._economy_restore_pending_generation = None
        self._economy_closing_generation = None
        self.economy_phase = EconomyScenePhase.CLOSED
        self.economy_visible = False
        self._economy_close_finalized = True
        self.economy_island.progress = 0.0
        self.economy_island.target = 0.0
        self.economy_island.velocity = 0.0
        self.overlay_coordinator.close_modal(OverlayKind.ECONOMY)
        self.frame_scheduler.force()
        pending_overlay = self._economy_pending_overlay
        self._economy_pending_overlay = None
        if pending_overlay is not None:
            def open_pending_overlay() -> None:
                if pending_overlay == OverlayKind.CLICKGUI:
                    self.set_clickgui_visible(True)
                elif pending_overlay == OverlayKind.CHAT:
                    self.open_match_chat()
                elif pending_overlay == OverlayKind.HERO_DEPLOY:
                    self.open_hero_deploy_interface()
                elif pending_overlay == OverlayKind.STUDENT:
                    panel = self._student_pending_panel
                    self._student_pending_panel = None
                    if panel:
                        self.open_student_panel(panel)

            # Present/restore is still on the GL stack here. Defer the next
            # modal request to the event queue so ownership never overlaps.
            QTimer.singleShot(0, open_pending_overlay)

    def open_economy_workspace(self, category: str | None = None) -> None:
        self.close_student_panel(immediate=True)
        if self.hero_deploy_video_cut_active():
            self.open_hero_deploy_interface()
            self.toast("Exit Hero deploy mode before opening Economy")
            return
        now = time.perf_counter()
        if self.hero_deploy_ui.visible:
            self.close_hero_deploy_interface(force=True)
        offers = self.economy_offers()
        categories = self.economy_categories(offers)
        if category in categories:
            self.economy_category = str(category)
        elif self.economy_category not in categories and categories:
            self.economy_category = categories[0]
        if self.gui_visible:
            self.set_clickgui_visible(False, announce=False, play_sound=False)
        self.close_match_chat(clear=False)
        self.cancel_quick_action_wheel()
        self._economy_generation += 1
        self._economy_closing_generation = None
        self._economy_restore_pending_generation = None
        self._economy_pending_overlay = None
        self.overlay_coordinator.request_modal(OverlayKind.ECONOMY)
        self.economy_visible = True
        self._economy_close_finalized = False
        self.economy_phase = EconomyScenePhase.OPENING
        self.economy_opened_at = now
        self.economy_island.set_target(
            1.0,
            self.anim_duration(0.62),
            now=now,
        )
        self._economy_scene_epoch += 1
        self.economy_armed_key = ""
        self.economy_hold_key = ""
        self._economy_hold_input_owner = None
        self.economy_category_scroll.position = 0.0
        self.economy_category_scroll.target = 0.0
        self.economy_category_scroll.velocity = 0.0
        self.economy_offers_scroll.position = 0.0
        self.economy_offers_scroll.target = 0.0
        self.economy_offers_scroll.velocity = 0.0
        self.economy_detail_scroll.position = 0.0
        self.economy_detail_scroll.target = 0.0
        self.economy_detail_scroll.velocity = 0.0
        selected = self.reconcile_economy_selection()
        if selected is not None:
            self.economy_selected_key = selected.key
        # Capture a fresh, complete HUD snapshot for the backdrop treatment.
        self.invalidate_transient_overlay_scene()
        self.play_gui_transition_sound("expand")
        self.frame_scheduler.force()
        self.update()

    def close_economy_workspace(self, *, play_sound: bool = True) -> None:
        if self.economy_phase == EconomyScenePhase.CLOSED:
            self.overlay_coordinator.close_modal(OverlayKind.ECONOMY)
            return
        if self.economy_phase == EconomyScenePhase.CLOSING:
            return
        now = time.perf_counter()
        self._economy_generation += 1
        self._economy_closing_generation = self._economy_generation
        self._economy_restore_pending_generation = None
        self.economy_visible = False
        self._economy_close_finalized = False
        self.economy_phase = EconomyScenePhase.CLOSING
        # Keep the Economy modal until a normal HUD frame has actually been
        # rebuilt and presented.  Releasing it here caused click-through and
        # let a stale transparent FBO become the new cache.
        self.overlay_coordinator.request_modal(OverlayKind.ECONOMY)
        self.economy_island.set_target(
            0.0,
            self.anim_duration(0.48),
            now=now,
        )
        self._economy_scene_epoch += 1
        self.economy_armed_key = ""
        self.economy_hold_key = ""
        self._economy_hold_input_owner = None
        self.economy_hold_rect = QRectF()
        self.invalidate_transient_overlay_scene()
        self.hotspots = [
            hotspot
            for hotspot in self.hotspots
            if not str(hotspot.action).startswith("economy")
        ]
        if play_sound:
            self.play_gui_transition_sound("collapse")
        self.frame_scheduler.force()
        self.update()

    def select_economy_offer(self, key: str) -> None:
        offers = self.economy_offers(self.economy_category)
        for index, offer in enumerate(offers):
            if offer.key == key:
                # Only wipe double-confirm arm when the selection *changes*.
                # Clearing on re-select broke the second click on “再次确认”.
                if key != self.economy_selected_key:
                    play_buy_sound(self, "buy_select")
                    self.economy_armed_key = ""
                    self.economy_armed_until = 0.0
                self.economy_selected = index
                self.economy_selected_key = key
                self.frame_scheduler.force()
                return

    def submit_economy_offer(self, offer: EconomyOffer) -> None:
        unavailable = unavailable_offer_reason(offer)
        if unavailable is not None:
            self.toast(unavailable)
            return
        latest = self.economy_bridge.latest
        if latest is not None and latest.status == "pending":
            self.toast("上一笔请求仍在等待确认")
            return
        try:
            transaction = self.economy_bridge.submit(
                self.state,
                offer,
                1,
                mock=self.economy_uses_mock_transport(),
            )
        except ValueError as exc:
            self.toast(str(exc))
            return
        self.economy_armed_key = ""
        self.economy_armed_until = 0.0
        self.economy_hold_key = ""
        self._economy_hold_input_owner = None
        self.toast(f"{transaction.message} · {offer.short_title}")
        self.frame_scheduler.force()

    def activate_economy_offer(self, offer: EconomyOffer) -> None:
        activation = plan_economy_activation(
            offer, read_armed_key=lambda: self.economy_armed_key,
            read_armed_until=lambda: self.economy_armed_until,
            clock=time.perf_counter,
        )
        if activation.action is ActivationAction.REJECT:
            self.toast(activation.message)
            return
        if activation.action is ActivationAction.SUBMIT:
            self.submit_economy_offer(offer)
            return
        self.economy_armed_key = offer.key
        self.economy_armed_until = activation.armed_until
        self.toast(f"再点一次确认购买：{offer.short_title}（{offer.cost} 金币）")
        self.frame_scheduler.force()

    def begin_economy_hold(self, offer: EconomyOffer, rect: QRectF | None = None) -> None:
        unavailable = unavailable_offer_reason(offer)
        if unavailable is not None:
            self.toast(unavailable)
            return
        self._economy_hold_input_owner = getattr(self, "_economy_hold_input_source", None)
        self.economy_hold_key = offer.key
        self.economy_hold_started = time.perf_counter()
        self.economy_hold_rect = QRectF(rect) if rect is not None else QRectF()
        self.frame_scheduler.force()

    def finish_economy_hold(self, point: QPointF | None = None) -> None:
        if not self.economy_hold_key:
            return
        key = self.economy_hold_key
        elapsed = time.perf_counter() - self.economy_hold_started
        inside = point is None or self.economy_hold_rect.isNull() or self.economy_hold_rect.contains(point)
        self.economy_hold_key = ""
        self._economy_hold_input_owner = None
        self.economy_hold_rect = QRectF()
        offer = next((item for item in self.economy_offers() if item.key == key), None)
        completion = complete_economy_hold(offer_exists=offer is not None, inside=inside, elapsed=elapsed)
        if completion is HoldCompletion.SUBMIT:
            self.submit_economy_offer(offer)
        elif completion is HoldCompletion.TOO_SHORT:
            self.toast("需要按住 0.6 秒确认")
        self.frame_scheduler.force()

    def _economy_glass_row(
        self,
        p: QPainter,
        rect: QRectF,
        *,
        hover: float = 0.0,
        active: bool = False,
    ) -> None:
        """Island capsule row — AndroidLiquidGlass-kmp style, no white overlay."""
        draw_economy_glass_capsule(p, rect, hover=hover, active=active)

    def _economy_dim_around(
        self,
        p: QPainter,
        width: int,
        height: int,
        panel: QRectF,
        dim_a: float,
        radius: float,
    ) -> None:
        """Dim only outside the glass shell so Combined Backdrop stays bright under the lens."""
        if dim_a <= 0.001:
            return
        dim = color("#02060c", dim_a)
        path = QPainterPath()
        path.addRect(QRectF(0, 0, float(width), float(height)))
        hole = QPainterPath()
        hole.addRoundedRect(panel, radius, radius)
        p.fillPath(path.subtracted(hole), dim)

    def _economy_island_well(self, p: QPainter, rect: QRectF, radius: float) -> None:
        """Island floating well — AndroidLiquidGlass-kmp recessed glass."""
        draw_economy_glass_well(p, rect, radius)

    def draw_economy_overlay_compat(self, p: QPainter, width: int, height: int, alpha: float) -> None:
        """Economy workspace: liquid glass + Dynamic Island morph + island chrome.

        - Collapse = top LensIsland capsule (radius = half height)
        - Expand = one highly-rounded LensStrong shell (not a hard rectangle)
        - Columns = floating island wells with gaps (no spreadsheet hairlines)
        - Rows / chips / CTA = full capsules (radius = half height)
        """
        all_offers = self.economy_offers()
        categories = self.economy_categories(all_offers)
        if not categories:
            return
        if self.economy_category not in categories:
            self.economy_category = categories[0]
        offers = [offer for offer in all_offers if offer.category == self.economy_category]
        selected = self.reconcile_economy_selection()
        context = EconomyContext.from_state(self.state)
        scale = self.economy_ui_scale(width, height)
        content_style = EconomyContentStyle(self.font, scale, lambda: chinese_enabled(self.registry))
        paint = EconomyPaintPrimitives(self.draw_glass_text, rounded_rect, draw_economy_glass, text_width)
        layout = compat_economy_layout(width, height, scale=scale, alpha=alpha, category_count=len(categories))
        morph = layout.morph
        content_alpha = layout.content_alpha
        panel = layout.panel
        radius = layout.radius

        p.save()
        # Compatibility scene dim. The caller owns battle-HUD exclusion.
        p.fillRect(QRectF(0, 0, width, height), color("#02060c", 0.36 * alpha))
        # Extra punch around shell so glass still samples a bright Combined Backdrop.
        self._economy_dim_around(p, width, height, panel, 0.10 * alpha, radius)

        draw_economy_glass(
            p,
            panel,
            radius,
            edge_width=12.0 * scale,
            highlight_intensity=0.30 + 0.08 * morph,
            shadow_radius=20.0 * scale,
            shadow_alpha=0.16 * alpha,
            tint_alpha=0.07,
            edge_tint_alpha=0.12,
        )
        if morph > 0.35:
            top_hi = QRectF(
                panel.left() + radius * 0.55,
                panel.top() + 11.0 * scale,
                panel.width() - radius * 1.1,
                1.0,
            )
            rounded_rect(p, top_hi, 0.5, color("#ffffff", 0.14 * content_alpha))

        if morph < 0.88:
            island_label = economy_text("经济  ·  {gold} G", chinese_enabled(self.registry), gold=context.gold)
            if morph < 0.52:
                self.draw_glass_text(
                    p,
                    panel,
                    island_label,
                    self.font("poppins_bold", 10.5 * scale),
                    color("#ffffff", max(0.0, 1.0 - morph * 1.75)),
                    Qt.AlignmentFlag.AlignCenter,
                )

        header = layout.header
        if content_alpha > 0.02:
            p.save()
            p.setOpacity(p.opacity() * content_alpha)
            self.draw_glass_text(p, header, "战斗经济", self.font("poppins_bold", 17.0 * scale), color("#ffffff"))
            link = "模拟" if context.mock_mode else "裁判"
            profile_title = profile_by_key(context.match_mode, context.robot_profile).title
            subtitle = f"{self.match_mode_manager.current.title}  ·  {profile_title}  ·  {link}"
            self.draw_glass_text(
                p,
                header.adjusted(0, 24.0 * scale, -120.0 * scale, 0),
                subtitle,
                self.font("poppins_medium", 8.2 * scale),
                color("#c8d8e8", 0.88),
            )
            close = QRectF(header.right() - 36.0 * scale, header.top() + 2.0 * scale, 34.0 * scale, 34.0 * scale)
            gold_chip_w = 100.0 * scale
            gold_chip = QRectF(close.left() - 10.0 * scale - gold_chip_w, header.top() + 4.0 * scale, gold_chip_w, 30.0 * scale)
            rounded_rect(p, gold_chip, gold_chip.height() * 0.5, color("#000000", 0.20), color("#ffd56a", 0.40), 1.0)
            self.draw_glass_text(
                p,
                gold_chip,
                economy_text("{gold} G", chinese_enabled(self.registry), gold=context.gold),
                self.font("poppins_bold", 10.0 * scale),
                color("#ffd56a"),
                Qt.AlignmentFlag.AlignCenter,
            )
            rounded_rect(p, close, close.height() * 0.5, color("#000000", 0.18), color("#ffffff", 0.20), 1.0)
            self.draw_glass_text(p, close, "×", self.font("poppins_bold", 15.0 * scale), color("#ffffff"), Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(close, "economy_close", tooltip="关闭经济工作区 [Esc]")
            p.restore()

        if content_alpha <= 0.01 or panel.height() < 180.0 * scale:
            p.restore()
            return
        p.setOpacity(p.opacity() * content_alpha)

        left = layout.left
        list_rect = layout.offers_pane
        detail = layout.detail_pane
        for well in (left, list_rect, detail):
            self._economy_island_well(p, well, 22.0 * scale)

        paint_economy_wallet(p, left, context, content_style, paint)
        pending_cost = self.economy_bridge.latest.request.cost if self.economy_bridge.latest and self.economy_bridge.latest.status == "pending" else 0
        pad = 16.0 * scale
        self.draw_glass_text(
            p,
            left.adjusted(pad, 92.0 * scale, -pad, -left.height() + 112.0 * scale),
            f"累计 {context.total_gold} · 待扣 {pending_cost}",
            self.font("poppins_medium", 7.4 * scale),
            color("#9aafc0", 0.92),
        )

        paint_economy_categories(
            p, layout, categories, self.economy_category, content_style, paint,
            draw_capsule=draw_economy_glass_capsule,
            register_category=lambda rect, key: self.add_hotspot(rect, "economy_category", key),
        )

        paint_economy_status(p, left, context, content_style, paint)

        list_pad = 14.0 * scale
        title_h = 28.0 * scale
        self.draw_glass_text(
            p,
            list_rect.adjusted(list_pad, 12.0 * scale, -list_pad, -list_rect.height() + title_h + 4.0 * scale),
            f"可选操作 · {CATEGORY_TITLES.get(self.economy_category, self.economy_category)}",
            self.font("poppins_bold", 10.5 * scale),
            color("#ffffff"),
        )
        viewport = layout.offers.viewport
        self.economy_offers_scroll.set_bounds(layout.offers.scroll_maximum(len(offers)))
        previous_clip = getattr(self, "_hotspot_clip", None)
        p.save()
        drawing_failed = False
        try:
            p.setClipRect(viewport)
            self._hotspot_clip = viewport
            scroll = self.economy_offers_scroll.position
            for index, offer in enumerate(offers):
                rect = layout.offers.card(index, scroll)
                if not rect.intersects(viewport):
                    continue
                active = selected is not None and offer.key == selected.key
                hovered = rect.contains(self.hover_point)
                hover_v = self.anim.value(
                    f"economy.hover.{offer.key}",
                    1.0 if hovered or active else 0.0,
                    self.anim_duration(0.14),
                    Easings.ease_out_expo,
                )
                self._economy_glass_row(p, rect, hover=hover_v, active=active)
                paint_economy_offer(p, offer, rect, content_style, paint)

                self.add_hotspot(rect, "economy_offer", offer.key, tooltip=offer.reason or offer.description)
        except BaseException:
            drawing_failed = True
            raise
        finally:
            self._hotspot_clip = previous_clip
            try:
                p.restore()
            except Exception:
                if not drawing_failed:
                    raise
                logging.getLogger(__name__).exception(
                    "Economy list painter restore failed while handling a drawing error"
                )

        detail_pad = 16.0 * scale
        button = layout.action_button
        content_bottom = button.top() - 10.0 * scale
        if selected is not None and detail.height() > 80.0 * scale:
            paint_economy_offer_detail(p, selected, detail, context.mock_mode, content_style, paint)

        history_top = detail.top() + 144.0 * scale
        history_max_bottom = content_bottom - 4.0 * scale
        if self.economy_category == "ammo":
            reload_clip = QRectF(detail.left()+detail_pad, history_top,
                                detail.width()-2*detail_pad, max(0.0, history_max_bottom-history_top))
            reload_commands = ClickGuiDrawList()
            append_reload_details(self, reload_commands, reload_clip, reload_clip, content_alpha, scale, self.economy_visible)
            paint_reload_commands(self, p, reload_commands)
            history_top += reload_detail_height(self)*scale
        if history_top + 40.0 * scale < history_max_bottom:
            self.draw_glass_text(
                p,
                QRectF(detail.left() + detail_pad, history_top, detail.width() - detail_pad * 2.0, 20.0 * scale),
                "最近交易",
                self.font("poppins_bold", 8.6 * scale),
                color("#8fa3b5"),
            )
            history = list(reversed(self.economy_bridge.history[-3:]))
            if not history:
                if history_top + 40.0 * scale < history_max_bottom:
                    self.draw_glass_text(
                        p,
                        QRectF(detail.left() + detail_pad, history_top + 24.0 * scale, detail.width() - detail_pad * 2.0, 18.0 * scale),
                        "暂无记录",
                        self.font("poppins_medium", 8.0 * scale),
                        color("#7a8c9c"),
                    )
            for index, transaction in enumerate(history):
                y = history_top + 24.0 * scale + index * 36.0 * scale
                if y + 32.0 * scale > history_max_bottom:
                    break
                paint_economy_transaction(
                    p, EconomyTransactionText(transaction.status, transaction.offer.short_title, transaction.message),
                    detail, y, content_style, paint,
                )

        confirmation = EconomyConfirmationState(
            selected=selected,
            pending=self.economy_bridge.latest is not None and self.economy_bridge.latest.status == "pending",
            hold_key=self.economy_hold_key,
            hold_started=self.economy_hold_started,
            armed_key=self.economy_armed_key,
            armed_until=self.economy_armed_until,
        ).present(now=time.perf_counter())
        button_text = confirmation.text
        button_enabled = confirmation.enabled
        action = confirmation.action

        paint_economy_confirmation(p, button, confirmation, content_style, paint)

        if selected is not None and button_enabled:
            self.add_hotspot(button, action, selected.key, tooltip=button_text)
        p.restore()

    def build_gpu_economy_workspace(self, width: int, height: int) -> ClickGuiDrawList:
        """Build the exclusive economy GPU scene.

        The scene is deliberately a pull-down, not a modal: a compact top
        island becomes a single downward sheet, then clips its controls into
        view.  Economy rules and command routing remain outside this renderer.
        """
        if grid_enabled(self):
            return build_grid(self, width, height)
        commands = ClickGuiDrawList()
        self.hotspots.clear()
        progress = self.economy_island.progress
        layout = self.economy_workspace_layer.layout(width, height, progress)
        if progress < 0.02:
            return commands

        all_offers = self.economy_offers()
        categories = self.economy_categories(all_offers)
        if not categories:
            return commands
        if self.economy_category not in categories:
            self.economy_category = categories[0]
        offers = [o for o in all_offers if o.category == self.economy_category]
        selected = self.reconcile_economy_selection()
        context = EconomyContext.from_state(self.state)
        header_alpha = layout.header_alpha
        content_alpha = layout.content_alpha
        category_alpha = layout.category_alpha
        offers_alpha = layout.offers_alpha
        detail_alpha = layout.detail_alpha
        def add_control_center_tile(
            rect: QRectF,
            opacity: float,
            *,
            material: LensMaterial = LensMaterial.STRONG,
            clip: QRectF | None = None,
        ) -> QRectF:
            lens = economy_lens_plan(rect, opacity, clip=clip)
            if lens is None:
                return QRectF()
            self.gpu_compositor.add_glass(
                lens.rect,
                lens.radius,
                strength=1.0,
                surface="clear",
                opacity=lens.opacity,
                material=material,
                refraction_height_px=lens.refraction_height,
                refraction_amount_px=lens.refraction_amount,
                chromatic_aberration=0.0,
                depth_effect=1.0,
                frost_amount=0.0,
                vibrancy_amount=lens.opacity,
                clip_rect=lens.clip,
            )
            # ControlCenterContent uses a five-percent black container color.
            commands.rect(lens.rect, lens.radius, color("#000000", 0.05 * lens.opacity), clip=lens.clip)
            return lens.visible

        # Control Center is a group of independent glass modules over one
        # treated backdrop. It never places one giant frosted card behind all
        # controls.
        header = layout.header
        add_control_center_tile(economy_header_tile(layout), header_alpha, material=LensMaterial.STRONG)

        # --- Island summary: visible before the body begins to reveal. ---
        scale = self.economy_ui_scale(width, height)
        content_style = EconomyContentStyle(self.font, scale, lambda: chinese_enabled(self.registry))
        # This handle belongs to the same root lens. It gives the expanded
        # sheet a clear pull-down affordance without adding another panel.
        handle_alpha = max(0.0, min(0.52, layout.body_progress * 0.52))
        commands.rect(
            layout.grab_handle,
            layout.grab_handle.height() * 0.5,
            color("#ffffff", handle_alpha),
        )
        commands.text(
            QRectF(header.left(), header.top(), header.width() - 160.0, 28.0),
            "战斗经济", self.font("poppins_bold", 17.0 * scale),
            color("#ffffff", header_alpha),
        )
        link = "模拟" if context.mock_mode else "裁判"
        profile_title = profile_by_key(context.match_mode, context.robot_profile).title
        subtitle = f"{self.match_mode_manager.current.title}  ·  {profile_title}  ·  {link}"
        commands.text(
            QRectF(header.left(), header.top() + 26.0, header.width() - 200.0, 18.0),
            subtitle, self.font("poppins_medium", 8.2 * scale),
            color("#c8d8e8", 0.88 * header_alpha),
        )

        # --- Gold chip ---
        gold_chip = layout.link_chip
        commands.rect(
            gold_chip,
            gold_chip.height() * 0.5,
            color("#ffffff", 0.08 * header_alpha),
        )
        commands.text(gold_chip, economy_text("{gold} G", chinese_enabled(self.registry), gold=context.gold), self.font("poppins_bold", 10.0 * scale), color("#ffd56a", header_alpha), Qt.AlignmentFlag.AlignCenter)

        # --- Close button (circle) ---
        close = layout.close_button
        commands.rect(
            close,
            close.height() * 0.5,
            color("#ffffff", 0.10 * header_alpha),
        )
        commands.text(close, "×", self.font("poppins_bold", 15.0 * scale), color("#ffffff", header_alpha), Qt.AlignmentFlag.AlignCenter)
        if self.economy_visible and header_alpha >= 0.38:
            self.add_hotspot(close, "economy_close", tooltip="关闭经济工作区 [Esc]")

        if content_alpha < 0.02 or layout.body_clip.isEmpty():
            return commands

        geometry = economy_module_geometry(layout, category_count=len(categories))

        def body_clip(rect: QRectF) -> QRectF:
            return QRectF(rect).intersected(layout.body_clip)

        strip = layout.status_strip
        status_tiles = geometry.status_tiles
        for status_tile in status_tiles:
            add_control_center_tile(
                status_tile,
                category_alpha,
                material=LensMaterial.REGULAR,
                clip=layout.body_clip,
            )
        tab_rects = geometry.tab_rects
        if layout.three_column:
            add_control_center_tile(
                layout.category_pane,
                category_alpha,
                material=LensMaterial.STRONG,
                clip=layout.body_clip,
            )
        else:
            for tab_rect in tab_rects:
                add_control_center_tile(
                    tab_rect,
                    layout.tab_alpha,
                    material=LensMaterial.STRONG,
                    clip=layout.body_clip,
                )

        # --- Status strip (compact gold/HP/ammo info) ---
        strip_clip = body_clip(strip)
        if not strip_clip.isEmpty():
            pending_cost = self.economy_bridge.latest.request.cost if self.economy_bridge.latest and self.economy_bridge.latest.status == "pending" else 0
            append_gpu_status(commands, status_tiles, strip_clip, context, pending_cost, category_alpha, content_style)

        append_gpu_categories(
            commands, layout, geometry, categories, self.economy_category, content_style,
            animate_selection=lambda index: self.anim.value(
                "economy.category.selection", index, self.anim_duration(0.22),
                Easings.ease_out_expo, initial=index,
            ),
            register_category=lambda rect, key: self.add_hotspot(rect, "economy_category", key)
            if self.economy_visible and layout.interactive else None,
        )

        # --- Offers pane ---
        offers_pane = geometry.offers_pane
        offers_clip = geometry.offers_clip
        viewport_clip = geometry.viewport_clip
        card_w = geometry.offers.card_width
        card_left = geometry.offers.card_left
        commands.text(
            QRectF(card_left, offers_pane.top() + 12.0, card_w, 24.0),
            f"可选操作 · {CATEGORY_TITLES.get(self.economy_category, self.economy_category)}",
            self.font("poppins_bold", 10.5 * scale), color("#ffffff", offers_alpha), clip=offers_clip,
        )
        self.economy_offers_scroll.set_bounds(geometry.offers.scroll_maximum(len(offers)))
        scroll = self.economy_offers_scroll.position
        if selected is not None:
            selected_offer_index = next(
                (index for index, offer in enumerate(offers) if offer.key == selected.key),
                0,
            )
            animated_offer_index = self.anim.value(
                "economy.offer.selection",
                float(selected_offer_index),
                self.anim_duration(0.20),
                Easings.ease_out_expo,
                initial=float(selected_offer_index),
            )
            selection_rect = geometry.offers.card(animated_offer_index, scroll)
            commands.rect(
                selection_rect,
                min(34.0, selection_rect.height() * 0.5),
                color("#64e0c1", 0.10 * offers_alpha),
                color("#64e0c1", 0.48 * offers_alpha),
                1.0,
                clip=viewport_clip,
            )
        for index, offer in enumerate(offers):
            rect = geometry.offers.card(index, scroll)
            if not rect.intersects(viewport_clip):
                continue
            add_control_center_tile(
                rect,
                offers_alpha,
                material=LensMaterial.REGULAR,
                clip=viewport_clip,
            )
            active = selected is not None and offer.key == selected.key
            hovered = rect.contains(self.hover_point)
            hover = self.anim.value(
                f"economy.offer.hover.{offer.key}",
                1.0 if hovered or active else 0.0,
                self.anim_duration(BUY_HOVER_SECONDS if buy_enabled(self) else 0.14),
                _css_ease_in_out if buy_enabled(self) else Easings.ease_out_expo,
            )
            draw_rect = rect if buy_enabled(self) else rect.translated(0.0, -1.4 * hover)
            append_buy_feedback(commands, self, offer.key, rect, viewport_clip,
                                hover, offers_alpha, time.perf_counter())
            title_inset = 48.0 if buy_enabled(self) else 18.0
            append_gpu_offer(commands, offer, draw_rect, viewport_clip, title_inset, offers_alpha, content_style)

            if self.economy_visible and layout.interactive:
                self.add_hotspot(rect.intersected(viewport_clip), "economy_offer", offer.key, tooltip=offer.reason or offer.description)

        # --- Detail pane ---
        detail = geometry.detail_pane
        detail_clip = geometry.detail_clip
        detail_pad = 16.0
        detail_vp = geometry.detail_viewport
        detail_vp_clip = geometry.detail_viewport_clip
        history = list(reversed(self.economy_bridge.history[-3:]))
        self.economy_detail_scroll.set_bounds(economy_detail_scroll_maximum(
            viewport_height=detail_vp.height(), reload_height=reload_detail_height(self), history_count=len(history),
        ))
        detail_scroll = self.economy_detail_scroll.position
        summary_rect = QRectF(
            detail_vp.left(),
            detail_vp.top() - detail_scroll,
            detail_vp.width(),
            116.0,
        )
        if selected is not None:
            add_control_center_tile(
                summary_rect,
                detail_alpha,
                material=LensMaterial.REGULAR,
                clip=detail_vp_clip,
            )
            append_gpu_offer_detail(commands, selected, summary_rect, detail_vp_clip, context.mock_mode, detail_alpha, content_style)

        # Transaction history
        history_top = detail_vp.top() + 128.0 - detail_scroll
        append_reload_details(self, commands,
                              QRectF(detail_vp.left(), history_top, detail_vp.width(), reload_detail_height(self)),
                              detail_vp_clip, detail_alpha, interactive=self.economy_visible and layout.interactive)
        history_top += reload_detail_height(self)
        button = geometry.action_button
        if history_top + 36.0 < detail_vp_clip.bottom():
            commands.text(
                QRectF(detail.left() + detail_pad, history_top, detail.width() - detail_pad * 2, 18.0),
                "最近交易", self.font("poppins_bold", 8.6 * scale), color("#8fa3b5", detail_alpha),
                clip=detail_vp_clip,
            )
            if not history:
                empty_rect = QRectF(
                    detail_vp.left(),
                    history_top + 22.0,
                    detail_vp.width(),
                    44.0,
                )
                add_control_center_tile(
                    empty_rect,
                    detail_alpha,
                    material=LensMaterial.REGULAR,
                    clip=detail_vp_clip,
                )
                commands.text(
                    empty_rect.adjusted(detail_pad, 0.0, -detail_pad, 0.0),
                    "暂无记录", self.font("poppins_medium", 8.0 * scale), color("#7a8c9c", detail_alpha),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    detail_vp_clip,
                )
            for index, transaction in enumerate(history):
                y = history_top + 22.0 + index * 52.0
                if y + 44.0 > detail_vp_clip.bottom():
                    break
                history_rect = QRectF(detail_vp.left(), y, detail_vp.width(), 44.0)
                add_control_center_tile(
                    history_rect,
                    detail_alpha,
                    material=LensMaterial.REGULAR,
                    clip=detail_vp_clip,
                )
                append_gpu_transaction(
                    commands, EconomyTransactionText(transaction.status, transaction.offer.short_title, transaction.message),
                    history_rect, detail_vp_clip, detail_alpha, content_style,
                )

        # --- Action button ---
        confirmation = EconomyConfirmationState(
            selected=selected,
            pending=self.economy_bridge.latest is not None and self.economy_bridge.latest.status == "pending",
            hold_key=self.economy_hold_key,
            hold_started=self.economy_hold_started,
            armed_key=self.economy_armed_key,
            armed_until=self.economy_armed_until,
        ).present(now=time.perf_counter())
        button_text = confirmation.text
        button_enabled = confirmation.enabled
        action = confirmation.action

        # Press feedback only changes the dynamic geometry. It never rebuilds
        # the combined backdrop, text atlas, or static workspace layout.
        press = self.liquid_press_amount(button)
        hover = self.anim.value(
            "economy.action.hover",
            1.0 if button.contains(self.hover_point) and button_enabled else 0.0,
            self.anim_duration(0.14),
            Easings.ease_out_expo,
        )
        inset = 1.55 * press
        button_draw = button.adjusted(inset, inset, -inset, -inset).translated(0.0, -0.9 * hover)
        button_draw = button_draw.intersected(detail_clip)
        if button_draw.isEmpty():
            return commands
        append_gpu_confirmation(
            commands, button_draw, detail_clip, confirmation,
            show_hold=selected is not None and selected.confirmation == "hold",
            alpha=detail_alpha, hover=hover, press=press, style=content_style,
        )

        if selected is not None and button_enabled and self.economy_visible and layout.interactive:
            self.add_hotspot(button.intersected(detail_clip), action, selected.key, tooltip=button_text)

        return commands

    def start_economy_qualification_test(self) -> None:
        """Authorize exactly one supervised economy command for 30 seconds.

        This does not mark the capability PASS. The operator still has to use
        the normal Economy workspace, observe authoritative local-state
        confirmation, and then record the lifecycle result.
        """

        if self.scenario_manager.current.key != "OfficialEvent":
            self.toast("Economy qualification requires OfficialEvent")
            return
        if self.backend.qualification.session is None:
            self.toast("Create a qualification session first")
            return
        token = self.backend.begin_qualification({"economy"}, ttl_s=30.0)
        self._economy_qualification_token = token.value
        self.backend.field_log.emit(
            "economy_qualification_window",
            expires_at=token.expires_at,
            session_id=self.backend.qualification.session.session_id,
        )
        self.toast("Economy test window active for 30 s; send one supervised request")


_QUALNAME_PINNED_MEMBERS = (
    EconomyMixin.draw_economy_overlay_legacy,
    EconomyMixin.economy_scale_steps,
    EconomyMixin.economy_ui_scale,
    EconomyMixin.economy_uses_mock_transport,
    EconomyMixin.send_economy_request,
    EconomyMixin.economy_offers,
    EconomyMixin.economy_categories,
    EconomyMixin.economy_selected_offer,
    EconomyMixin._finalize_economy_restore,
    EconomyMixin.open_economy_workspace,
    EconomyMixin.close_economy_workspace,
    EconomyMixin.select_economy_offer,
    EconomyMixin.submit_economy_offer,
    EconomyMixin.activate_economy_offer,
    EconomyMixin.begin_economy_hold,
    EconomyMixin.finish_economy_hold,
    EconomyMixin._economy_glass_row,
    EconomyMixin._economy_dim_around,
    EconomyMixin._economy_island_well,
    EconomyMixin.draw_economy_overlay_compat,
    EconomyMixin.build_gpu_economy_workspace,
    EconomyMixin.start_economy_qualification_test,
)

for _fn in _QUALNAME_PINNED_MEMBERS:
    _fn.__qualname__ = f"PolarRMWindow.{_fn.__name__}"
del _fn, _QUALNAME_PINNED_MEMBERS
