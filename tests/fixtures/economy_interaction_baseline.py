"""Frozen interaction methods from 71ebb0f570441c12eb0d15b0a992c9cc3e805aeb.

Source: rm_client/ui/polar/mixins/economy_mixin.py. Preserve this reference;
production refactors must retain ordered effects, partial writes and failures.
"""
from __future__ import annotations
import time
from PySide6.QtCore import QPointF, QRectF
from rm_client.economy import EconomyOffer, EconomyRequest
from rm_client.ui.cs2_feedback import play_buy_sound

class EconomyInteractionBaseline:
    def economy_selected_offer(self) -> EconomyOffer | None:
        offers = self.economy_offers(self.economy_category)
        if not offers:
            return None
        for offer in offers:
            if offer.key == self.economy_selected_key:
                return offer
        self.economy_selected_key = offers[0].key
        self.economy_selected = 0
        return offers[0]


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
        if not offer.enabled or offer.status_only:
            self.toast(offer.reason or "当前操作不可用")
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
        self.toast(f"{transaction.message} · {offer.short_title}")
        self.frame_scheduler.force()


    def activate_economy_offer(self, offer: EconomyOffer) -> None:
        if not offer.enabled or offer.status_only:
            self.toast(offer.reason or "当前操作不可用")
            return
        if offer.confirmation == "hold":
            self.toast("请按住确认按钮 0.6 秒")
            return
        now = time.perf_counter()
        if self.economy_armed_key == offer.key and now <= self.economy_armed_until:
            self.submit_economy_offer(offer)
            return
        self.economy_armed_key = offer.key
        self.economy_armed_until = now + 2.8
        self.toast(f"再点一次确认购买：{offer.short_title}（{offer.cost} 金币）")
        self.frame_scheduler.force()


    def begin_economy_hold(self, offer: EconomyOffer, rect: QRectF | None = None) -> None:
        if not offer.enabled or offer.status_only:
            self.toast(offer.reason or "当前操作不可用")
            return
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
        self.economy_hold_rect = QRectF()
        offer = next((item for item in self.economy_offers() if item.key == key), None)
        if offer is not None and inside and elapsed >= 0.60:
            self.submit_economy_offer(offer)
        elif elapsed < 0.60:
            self.toast("需要按住 0.6 秒确认")
        self.frame_scheduler.force()


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


