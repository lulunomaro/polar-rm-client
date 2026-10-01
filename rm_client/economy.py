from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import time
from typing import Any, Callable, Protocol


CATEGORY_TITLES = {
    "ammo": "弹丸",
    "recovery": "恢复",
    "revive": "复活",
    "support": "支援",
}

TRANSACTION_LABELS = {
    "confirming": "等待确认",
    "pending": "等待裁判系统",
    "succeeded": "操作成功",
    "rejected": "请求被拒绝",
    "timed_out": "确认超时",
}

#: Confirmation deadline used for real referee traffic.  The referee system
#: answers a CommonCommand in well under a second on a healthy link, so a 5 s
#: silence means the frame is lost, delayed, or was refused without an answer.
CONFIRM_TIMEOUT_S = 5.0
#: The deterministic mock settles far faster than a real referee round-trip.
MOCK_CONFIRM_TIMEOUT_S = 2.0
MOCK_SETTLE_DELAY_S = 0.36

#: Refusal strings.  ``submit`` must never collapse distinct refusals into one
#: sentence: "match control is LOCKED", "CommonCommand exceeds 10 Hz" and
#: "not connected" call for three different operator actions.
NO_SENDER_REASON = "未配置经济请求发送通道"
SEND_FAILED_FALLBACK = "MQTT 请求未发送"
DUPLICATE_INFLIGHT_REASON = "同一笔经济请求仍在处理中，已阻止重复扣款"
#: After a timeout the referee's answer is *unknown*, not negative: the QoS1
#: frame may still be queued in the broker or be redelivered after a reconnect.
#: Re-sending the identical frame inside this window is the one way a human
#: double-tap turns into a second charge, so the bridge refuses it.
AMBIGUOUS_RESEND_REASON = "上一笔同内容请求仍未收到裁判系统答复，暂不重发以免重复扣款"


@dataclass(frozen=True)
class EconomyContext:
    match_mode: str
    robot_profile: str
    projectile_type: str
    gold: int
    total_gold: int
    current_ammo: int
    ammo_capacity: int
    current_health: int
    max_health: int
    in_supply_zone: bool
    supply_zone_known: bool
    out_of_combat: bool
    can_remote_heal: bool
    can_remote_ammo: bool
    respawn_pending: bool
    respawn_progress: float
    respawn_can_free: bool
    respawn_gold_cost: int
    respawn_can_pay: bool
    air_support_active: bool
    air_support_can_free: bool
    mock_mode: bool

    @property
    def alive(self) -> bool:
        return self.current_health > 0

    @classmethod
    def from_state(cls, state: Any) -> "EconomyContext":
        return cls(
            match_mode=str(getattr(state, "match_mode", "RMUC7v7")),
            robot_profile=str(getattr(state, "robot_profile", getattr(state, "robot_type", "Infantry1"))),
            projectile_type=str(getattr(state, "projectile_type", "17mm")),
            gold=max(0, int(getattr(state, "remaining_economy", 0))),
            total_gold=max(0, int(getattr(state, "total_economy_obtained", 0))),
            current_ammo=max(0, int(getattr(state, "remaining_ammo", 0))),
            ammo_capacity=max(0, int(getattr(state, "ammo_capacity", 0))),
            current_health=max(0, int(getattr(state, "current_health", 0))),
            max_health=max(1, int(getattr(state, "max_health", 1))),
            in_supply_zone=bool(getattr(state, "in_supply_zone", False)),
            supply_zone_known=bool(getattr(state, "supply_zone_known", False)),
            out_of_combat=bool(getattr(state, "out_of_combat", True)),
            can_remote_heal=bool(getattr(state, "can_remote_heal", True)),
            can_remote_ammo=bool(getattr(state, "can_remote_ammo", True)),
            respawn_pending=bool(getattr(state, "respawn_pending", False)),
            respawn_progress=max(0.0, min(1.0, float(getattr(state, "respawn_progress", 0.0)))),
            respawn_can_free=bool(getattr(state, "respawn_can_free", False)),
            respawn_gold_cost=max(0, int(getattr(state, "respawn_gold_cost", 0))),
            respawn_can_pay=bool(getattr(state, "respawn_can_pay", False)),
            air_support_active=bool(getattr(state, "air_support_active", False)),
            air_support_can_free=bool(getattr(state, "air_support_can_free", False)),
            mock_mode=bool(getattr(state, "economy_mock_mode", True)),
        )


@dataclass(frozen=True)
class EconomyOffer:
    key: str
    category: str
    title: str
    short_title: str
    description: str
    unit: str
    minimum: int
    cost: int
    command_type: int
    command_param: int
    enabled: bool
    reason: str = ""
    confirmation: str = "double"
    topic: str = "CommonCommand"
    estimated: bool = False
    status_only: bool = False

    @property
    def quantity(self) -> int:
        return self.minimum


# Keep the public name used by existing callers while the workspace migrates to
# the more precise EconomyOffer terminology.
EconomyAction = EconomyOffer


@dataclass(frozen=True)
class EconomyRequest:
    sequence: int
    timestamp: float
    match_mode: str
    robot_profile: str
    action: str
    quantity: int
    cost: int
    command_type: int
    command_param: int
    topic: str
    payload: bytes
    mock: bool

    @property
    def identity(self) -> tuple[str, bytes]:
        """Byte-exact wire identity of the frame this request produces.

        Two requests with the same identity are the *same* referee command.
        CommonCommand is QoS1 and the referee system charges it per accepted
        frame, so re-emitting an identical frame while the first one is still
        unconfirmed is the one way this module can double-charge a robot.
        The key is the encoded payload rather than ``action``/``quantity``
        because several distinct offers collapse onto the same frame (every
        air-support command ignores quantity, and ``command_param == 0`` for
        heal / respawn), and because the payload is what the referee
        deduplicates on, not our own label for it.
        """

        return (self.topic, self.payload)


@dataclass
class EconomyTransaction:
    request: EconomyRequest
    offer: EconomyOffer
    status: str
    message: str
    submitted_at: float
    deadline_at: float
    mock_complete_at: float
    baseline_gold: int
    baseline_ammo: int
    baseline_health: int
    baseline_revision: int
    baseline_respawn_pending: bool
    baseline_support_active: bool
    completed_at: float = 0.0

    @property
    def sequence(self) -> int:
        return self.request.sequence


class EconomyTransport(Protocol):
    """Transport boundary used by the workspace without owning MQTT state."""

    def send(self, request: EconomyRequest) -> bool:
        ...


# Attributes a transport may expose to hand its refusal reason back to us.
# ``OfficialSafety`` already computes the actionable text ("match control is
# LOCKED", "capability economy remains LOCKED MOCK", "CommonCommand exceeds
# 10 Hz", "not in an authoritative fighting stage", ...) and ``MqttBackend``
# copies it into ``last_error`` on every failure path.  A bare ``bool`` sender
# therefore throws away the only diagnosis the operator can act on.
_REFUSAL_ATTRIBUTES = ("last_error", "last_reason", "last_publish_error", "last_send_error")


def _transport_refusal_reason(sender: Any) -> str:
    """Best-effort, read-only read of the refusal a transport just computed.

    Walks the sender and the object it is bound to, plus the one nested
    transport handle the workspace is known to expose, looking for the
    diagnostic the transport set on its way to returning a falsy value.  Every
    access is ``getattr``-guarded so a transport that exposes nothing (a plain
    lambda in tests, a stub) simply falls back to the legacy sentence instead
    of raising inside the money path.
    """

    if sender is None:
        return ""
    owners = [sender, getattr(sender, "__self__", None)]
    for owner in owners:
        if owner is None:
            continue
        holders = [owner]
        for attribute in ("mqtt", "transport", "backend"):
            try:
                holder = getattr(owner, attribute, None)
            except Exception:  # pragma: no cover - defensive
                holder = None
            if holder is not None:
                holders.append(holder)
        for holder in holders:
            for attribute in _REFUSAL_ATTRIBUTES:
                try:
                    value = str(getattr(holder, attribute, "") or "").strip()
                except Exception:  # pragma: no cover - defensive
                    continue
                if value:
                    return value
    return ""


def _normalize_send_outcome(outcome: Any, sender: Any) -> tuple[bool, str]:
    """Normalise a sender result into ``(accepted, reason)``.

    Accepted forms, widest first:

    * ``bool`` / ``None``  -- the historical contract, reason is recovered from
      the transport diagnostic instead of being invented.
    * ``str``              -- a refusal reason; any non-empty string is a
      refusal, because a successful send has nothing to explain.
    * ``(ok, reason)``     -- the explicit two-value form a transport should
      return when it knows why.
    * any object with ``.ok`` / ``.reason`` attributes.
    """

    reason = ""
    if outcome is None or isinstance(outcome, bool):
        accepted = bool(outcome)
    elif isinstance(outcome, str):
        accepted, reason = False, outcome.strip()
    elif isinstance(outcome, tuple):
        if len(outcome) >= 2:
            accepted, reason = bool(outcome[0]), str(outcome[1]).strip()
        elif len(outcome) == 1:
            accepted = bool(outcome[0])
        else:
            accepted = False
    else:
        if hasattr(outcome, "ok") or hasattr(outcome, "reason"):
            accepted = bool(getattr(outcome, "ok", False))
            reason = str(getattr(outcome, "reason", "") or "").strip()
        else:
            # ``numpy.bool_`` / ``int`` and friends are not ``bool`` subclasses
            # but are still perfectly good "did it send" answers.  Reading them
            # as refusals would be a fail-closed mistake in the wrong
            # direction: a real charge would be reported as a refusal.
            accepted = bool(outcome)
    if accepted:
        return True, ""
    if not reason:
        reason = _transport_refusal_reason(sender)
    return False, reason or SEND_FAILED_FALLBACK


def _encode_varint(value: int) -> bytes:
    value = max(0, int(value))
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            encoded.append(byte | 0x80)
        else:
            encoded.append(byte)
            return bytes(encoded)


def encode_common_command(command_type: int, param: int) -> bytes:
    """Encode RM 2026 CommonCommand without requiring generated protobuf code."""

    return b"\x08" + _encode_varint(command_type) + b"\x10" + _encode_varint(param)


def encode_air_support_command(command_id: int) -> bytes:
    """Encode RM 2026 AirSupportCommand.command_id."""

    return b"\x08" + _encode_varint(command_id)


def _rmuc_time_step(state: Any) -> int:
    remaining = max(0, int(getattr(state, "stage_countdown_sec", 420)))
    return max(0, math.ceil((420 - remaining) / 60.0))


def _enabled(gold: int, cost: int, condition: bool, reason: str) -> tuple[bool, str]:
    if not condition:
        return False, reason
    if gold < cost:
        return False, f"金币不足，还需要 {cost - gold}"
    return True, ""


def _offer(
    *,
    key: str,
    category: str,
    title: str,
    short_title: str,
    description: str,
    unit: str,
    quantity: int,
    cost: int,
    command_type: int,
    command_param: int,
    enabled: bool,
    reason: str = "",
    confirmation: str = "double",
    topic: str = "CommonCommand",
    estimated: bool = False,
    status_only: bool = False,
) -> EconomyOffer:
    return EconomyOffer(
        key=key,
        category=category,
        title=title,
        short_title=short_title,
        description=description,
        unit=unit,
        minimum=quantity,
        cost=cost,
        command_type=command_type,
        command_param=command_param,
        enabled=enabled,
        reason=reason,
        confirmation=confirmation,
        topic=topic,
        estimated=estimated,
        status_only=status_only,
    )


def _local_ammo_offer(
    context: EconomyContext,
    *,
    key: str,
    projectile: str,
    quantity: int,
    cost: int,
) -> EconomyOffer:
    # CommonCommand is validated by the referee server. Because the documented
    # protobuf feed omits the local RFID card bit, an unknown estimate must not
    # disable a valid command (and must not claim the robot is inside either).
    zone_allowed = context.in_supply_zone if context.supply_zone_known else True
    enabled, reason = _enabled(context.gold, cost, zone_allowed, "需要进入补给区")
    command_type = 1 if projectile == "17mm" else 2
    return _offer(
        key=key,
        category="ammo",
        title=f"购买 {quantity} 颗 {projectile} 弹丸",
        short_title=f"{quantity} × {projectile}",
        description="补给区兑换，二次确认后发送",
        unit=f"{quantity} 颗",
        quantity=quantity,
        cost=cost,
        command_type=command_type,
        command_param=quantity,
        enabled=enabled,
        reason=reason,
        confirmation="double",
        # ``estimated`` describes price uncertainty in the existing UI.  Zone
        # eligibility is instead left to the authoritative server when the
        # custom-client feed cannot observe the RFID state.
        estimated=False,
    )


def _remote_ammo_offer(
    context: EconomyContext,
    *,
    key: str,
    projectile: str,
    quantity: int,
) -> EconomyOffer:
    cost = 150
    enabled, reason = _enabled(
        context.gold,
        cost,
        context.can_remote_ammo or context.mock_mode,
        "裁判系统暂未开放远程补弹",
    )
    return _offer(
        key=key,
        category="ammo",
        title=f"远程兑换 {quantity} 颗 {projectile}",
        short_title=f"远程 {quantity} × {projectile}",
        description="远程兑换属于高花费操作，需要长按确认",
        unit=f"{quantity} 颗",
        quantity=quantity,
        cost=cost,
        command_type=5,
        command_param=quantity,
        enabled=enabled,
        reason=reason,
        confirmation="hold",
    )


def available_actions(state: Any) -> list[EconomyOffer]:
    """Build the visible offer matrix for the selected match and robot."""

    context = EconomyContext.from_state(state)
    profile = context.robot_profile
    offers: list[EconomyOffer] = []

    if context.match_mode == "RMUL3v3":
        if profile == "Hero":
            offers.extend(
                [
                    _local_ammo_offer(context, key="rmul_hero_42_1", projectile="42mm", quantity=1, cost=10),
                    _local_ammo_offer(context, key="rmul_hero_42_10", projectile="42mm", quantity=10, cost=100),
                ]
            )
        elif profile.startswith("Infantry"):
            offers.extend(
                [
                    _local_ammo_offer(context, key="rmul_infantry_17_10", projectile="17mm", quantity=10, cost=10),
                    _local_ammo_offer(context, key="rmul_infantry_17_100", projectile="17mm", quantity=100, cost=100),
                ]
            )
        elif profile == "Sentinel":
            offers.append(
                _offer(
                    key="rmul_sentinel_ammo_status",
                    category="ammo",
                    title="哨兵初始弹量",
                    short_title="仅显示弹量",
                    description="联盟赛哨兵不能额外购买弹丸",
                    unit=f"剩余 {context.current_ammo}",
                    quantity=0,
                    cost=0,
                    command_type=0,
                    command_param=0,
                    enabled=False,
                    reason="联盟赛哨兵不开放弹丸购买",
                    status_only=True,
                )
            )
        if not context.alive or context.mock_mode:
            offers.append(
                _offer(
                    key="rmul_auto_respawn",
                    category="revive",
                    title="自动复活进度",
                    short_title=f"{round(context.respawn_progress * 100)}%" if not context.alive else "待命",
                    description="联盟赛使用自动复活，不支持金币立即复活" if not context.alive else "当前存活中，复活选项不可用",
                    unit="自动",
                    quantity=0,
                    cost=0,
                    command_type=0,
                    command_param=0,
                    enabled=False,
                    reason="当前存活中" if context.alive else "等待自动复活",
                    status_only=True,
                )
            )
        return offers

    if context.match_mode != "RMUC7v7":
        return offers

    if profile == "Hero":
        offers.extend(
            [
                _local_ammo_offer(context, key="rmuc_hero_42_1", projectile="42mm", quantity=1, cost=10),
                _local_ammo_offer(context, key="rmuc_hero_42_10", projectile="42mm", quantity=10, cost=100),
                _remote_ammo_offer(context, key="rmuc_hero_remote_42_10", projectile="42mm", quantity=10),
            ]
        )
    elif profile.startswith("Infantry") or profile == "Sentinel":
        prefix = "sentinel" if profile == "Sentinel" else "infantry"
        offers.extend(
            [
                _local_ammo_offer(context, key=f"rmuc_{prefix}_17_10", projectile="17mm", quantity=10, cost=10),
                _local_ammo_offer(context, key=f"rmuc_{prefix}_17_100", projectile="17mm", quantity=100, cost=100),
                _remote_ammo_offer(context, key=f"rmuc_{prefix}_remote_17_100", projectile="17mm", quantity=100),
            ]
        )

    combat_profiles = profile == "Hero" or profile.startswith("Infantry") or profile == "Sentinel"
    if combat_profiles and context.alive:
        heal_cost = 50 + _rmuc_time_step(state) * 20
        enabled, reason = _enabled(
            context.gold,
            heal_cost,
            context.out_of_combat and (context.can_remote_heal or context.mock_mode),
            "需要脱离战斗并等待裁判系统开放远程补血",
        )
        offers.append(
            _offer(
                key="rmuc_remote_heal",
                category="recovery",
                title="远程补血",
                short_title="恢复 60% HP",
                description="远程补血需要长按确认，实际结果以裁判系统为准",
                unit="60% 最大血量",
                quantity=1,
                cost=heal_cost,
                command_type=6,
                command_param=0,
                enabled=enabled,
                reason=reason,
                confirmation="hold",
            )
        )

    ground_profiles = combat_profiles or profile == "Engineer"
    if ground_profiles and (not context.alive or context.mock_mode):
        confirm_enabled = (context.respawn_can_free or context.mock_mode) and not context.alive
        offers.append(
            _offer(
                key="rmuc_confirm_respawn",
                category="revive",
                title="确认复活",
                short_title="确认复活",
                description="确认当前可用的免费或普通复活流程" if not context.alive else "当前存活中，复活选项不可用",
                unit="一次",
                quantity=1,
                cost=0,
                command_type=3,
                command_param=0,
                enabled=confirm_enabled,
                reason="当前存活中" if context.alive else ("裁判系统尚未开放确认复活" if not confirm_enabled else ""),
                confirmation="hold",
            )
        )
        estimated = False
        revive_cost = context.respawn_gold_cost
        if revive_cost <= 0 and context.mock_mode:
            revive_cost = _rmuc_time_step(state) * 80 + max(1, int(getattr(state, "level", 1))) * 20
            estimated = True
        can_pay = ((context.respawn_can_pay or context.mock_mode) and revive_cost > 0) and not context.alive
        enabled, reason = _enabled(context.gold, revive_cost, can_pay, "当前存活中" if context.alive else "等待裁判系统下发复活价格")
        offers.append(
            _offer(
                key="rmuc_gold_respawn",
                category="revive",
                title="金币立即复活",
                short_title="立即复活",
                description="价格优先使用裁判系统下发值" if not estimated else "实验室预计价格，不代表正式比赛结果",
                unit="一次",
                quantity=1,
                cost=revive_cost,
                command_type=4,
                command_param=0,
                enabled=enabled,
                reason=reason,
                confirmation="hold",
                estimated=estimated,
            )
        )

    if profile == "Aerial":
        if context.air_support_active:
            offers.append(
                _offer(
                    key="rmuc_air_support_cancel",
                    category="support",
                    title="结束空中支援",
                    short_title="结束支援",
                    description="停止当前空中支援计费",
                    unit="立即",
                    quantity=1,
                    cost=0,
                    command_type=0,
                    command_param=0,
                    enabled=True,
                    confirmation="hold",
                    topic="AirSupportCommand",
                )
            )
        else:
            offers.append(
                _offer(
                    key="rmuc_air_support_paid",
                    category="support",
                    title="开启付费空中支援",
                    short_title="1 金币 / 秒",
                    description="开启后按比赛规则持续计费，需长按确认",
                    unit="按秒计费",
                    quantity=1,
                    cost=1,
                    command_type=2,
                    command_param=0,
                    enabled=context.gold >= 1,
                    reason="金币不足" if context.gold < 1 else "",
                    confirmation="hold",
                    topic="AirSupportCommand",
                )
            )
            if context.air_support_can_free:
                offers.append(
                    _offer(
                        key="rmuc_air_support_free",
                        category="support",
                        title="开启免费空中支援",
                        short_title="免费支援",
                        description="使用当前可用的免费空中支援时段",
                        unit="免费时段",
                        quantity=1,
                        cost=0,
                        command_type=1,
                        command_param=0,
                        enabled=True,
                        confirmation="hold",
                        topic="AirSupportCommand",
                    )
                )
    return offers


class EconomyCommandBridge:
    """Transaction boundary shared by official MQTT and deterministic mock.

    Money invariant owned here (the UI must not be the only thing enforcing
    it): a byte-identical referee frame is emitted **once**.  CommonCommand is
    QoS1, so the referee system charges it per accepted frame; a second
    identical frame inside the first one's unconfirmed window is a duplicate
    charge, not a retry.  ``submit`` therefore refuses a duplicate while the
    first is in flight, and refuses a re-send for ``ambiguous_settle_s``
    *after* a timeout, because a timeout means "the referee's answer is
    unknown", not "the referee said no".
    """

    def __init__(self, *, ambiguous_settle_s: float = CONFIRM_TIMEOUT_S) -> None:
        self.sequence = 0
        self.last_request: EconomyRequest | None = None
        self.history: list[EconomyTransaction] = []
        self.sender: Callable[[EconomyRequest], Any] | None = None
        #: How long an unanswered frame keeps blocking an identical re-send.
        self.ambiguous_settle_s = max(0.0, float(ambiguous_settle_s))
        #: Optional observer for every refusal, so a caller that owns richer
        #: diagnostics than the transaction message can react to them.
        self.on_refusal: Callable[[EconomyRequest, str], None] | None = None
        # sequence -> transaction, for every request still awaiting a verdict.
        # ``update`` walks this instead of ``history[-1]``: a transaction that
        # a later submit displaced must still reach its own deadline, or it
        # stays "pending" forever and never times out.
        self._inflight: dict[int, EconomyTransaction] = {}
        # request identity -> wall time at which the block on re-sending it
        # expires.  Only timed-out (not refused, not confirmed) requests land
        # here: those are the only ones whose money fate is genuinely unknown.
        self._ambiguous: dict[tuple[str, bytes], float] = {}
        self._mock_state: Any | None = None
        self._mock_anchor_gold = 0
        self._mock_anchor_ammo = 0
        self._mock_anchor_income = 0
        self._mock_anchor_clock = 0.0
        self._mock_anchor_stage = 0
        self._mock_extra_ammo = 0

    @property
    def latest(self) -> EconomyTransaction | None:
        return self.history[-1] if self.history else None

    @property
    def in_flight(self) -> int:
        """Number of requests still awaiting a referee verdict."""

        return len(self._inflight)

    def submit(
        self,
        state: Any,
        offer: EconomyOffer,
        quantity: int = 1,
        *,
        mock: bool | None = None,
        now: float | None = None,
    ) -> EconomyTransaction:
        if not offer.enabled or offer.status_only:
            raise ValueError(offer.reason or "当前操作不可用")
        now = time.perf_counter() if now is None else float(now)
        quantity = max(1, int(quantity))
        mock = self.sender is None if mock is None else bool(mock)
        command_param = offer.command_param * quantity if offer.command_param > 0 else 0
        payload = (
            encode_air_support_command(offer.command_type)
            if offer.topic == "AirSupportCommand"
            else encode_common_command(offer.command_type, command_param)
        )
        self._expire_ambiguous(now)
        self.sequence += 1
        request = EconomyRequest(
            sequence=self.sequence,
            timestamp=time.time(),
            match_mode=str(getattr(state, "match_mode", "")),
            robot_profile=str(getattr(state, "robot_profile", getattr(state, "robot_type", ""))),
            action=offer.key,
            quantity=quantity,
            cost=offer.cost * quantity,
            command_type=offer.command_type,
            command_param=command_param,
            topic=offer.topic,
            payload=payload,
            mock=mock,
        )
        transaction = EconomyTransaction(
            request=request,
            offer=offer,
            status="pending",
            message="MOCK 正在处理" if mock else "已发送，等待裁判系统确认",
            submitted_at=now,
            deadline_at=now + (MOCK_CONFIRM_TIMEOUT_S if mock else CONFIRM_TIMEOUT_S),
            mock_complete_at=now + MOCK_SETTLE_DELAY_S if mock else 0.0,
            baseline_gold=int(getattr(state, "remaining_economy", 0)),
            baseline_ammo=int(getattr(state, "remaining_ammo", 0)),
            baseline_health=int(getattr(state, "current_health", 0)),
            baseline_revision=int(getattr(state, "economy_status_revision", 0)),
            baseline_respawn_pending=bool(getattr(state, "respawn_pending", False)),
            baseline_support_active=bool(getattr(state, "air_support_active", False)),
        )
        self.last_request = request
        self.history.append(transaction)
        self.history = self.history[-32:]
        if mock:
            # A mock request never reaches a transport, so there is nothing to
            # refuse; it settles from local state on the next update.
            self._inflight[request.sequence] = transaction
            return transaction
        blocked = self._admission_block(request, now)
        if blocked:
            # Fail-closed: the frame is NOT handed to the sender at all.  The
            # transaction still exists so the operator sees why nothing moved.
            self._reject(transaction, blocked, now)
            return transaction
        accepted, reason = self._dispatch(request)
        if not accepted:
            self._reject(transaction, reason, now)
            return transaction
        self._inflight[request.sequence] = transaction
        return transaction

    def _admission_block(self, request: EconomyRequest, now: float) -> str:
        """Refuse a frame that is already in flight or whose fate is unknown."""

        identity = request.identity
        for transaction in self._inflight.values():
            if transaction.request.identity == identity:
                return DUPLICATE_INFLIGHT_REASON
        expiry = self._ambiguous.get(identity)
        if expiry is not None:
            if expiry > now:
                return AMBIGUOUS_RESEND_REASON
            del self._ambiguous[identity]
        return ""

    def _expire_ambiguous(self, now: float) -> None:
        if not self._ambiguous:
            return
        for identity, expiry in list(self._ambiguous.items()):
            if expiry <= now:
                del self._ambiguous[identity]

    def _reject(self, transaction: EconomyTransaction, reason: str, now: float) -> None:
        transaction.status = "rejected"
        transaction.message = reason or SEND_FAILED_FALLBACK
        transaction.completed_at = now
        self._inflight.pop(transaction.request.sequence, None)
        observer = self.on_refusal
        if observer is None:
            return
        try:
            observer(transaction.request, transaction.message)
        except Exception:  # pragma: no cover - diagnostics must never fail a send
            pass

    def _dispatch(self, request: EconomyRequest) -> tuple[bool, str]:
        sender = self.sender
        if sender is None:
            return False, NO_SENDER_REASON
        try:
            outcome = sender(request)
        except Exception as exc:
            # A raising sender is a refusal, not a silent success.  Leaving the
            # transaction "pending" here would hide a real money-path failure
            # behind a timeout five seconds later.
            return False, f"经济请求发送异常：{exc}"
        return _normalize_send_outcome(outcome, sender)

    def update(self, state: Any, now: float | None = None) -> EconomyTransaction | None:
        """Advance **every** in-flight transaction, not just the newest one.

        Returns the highest-sequence transaction that changed state in this
        pass, or the newest transaction when nothing changed -- identical to
        the single-slot behaviour whenever only one request is outstanding.
        """

        latest = self.latest
        now = time.perf_counter() if now is None else float(now)
        changed: EconomyTransaction | None = None
        if self._inflight:
            for sequence in sorted(self._inflight):
                transaction = self._inflight.get(sequence)
                if transaction is None:
                    continue
                if self._settle(state, transaction, now):
                    changed = transaction
        self._maintain_mock_feed(state, now)
        return changed if changed is not None else latest

    def _settle(self, state: Any, transaction: EconomyTransaction, now: float) -> bool:
        """Try to reach a terminal status for one transaction."""

        if transaction.status != "pending":
            return False
        if transaction.request.mock and now >= transaction.mock_complete_at:
            self._apply_mock(state, transaction, now)
            transaction.status = "succeeded"
            transaction.message = "MOCK 操作成功"
            transaction.completed_at = now
            self._finish(transaction)
            self._rebase_rest(state, transaction)
            return True
        explicit = str(getattr(state, "economy_command_status", "")).lower()
        revision_changed = int(getattr(state, "economy_status_revision", 0)) > transaction.baseline_revision
        succeeded = False
        if revision_changed and explicit in {"succeeded", "accepted", "success"}:
            transaction.status = "succeeded"
            transaction.message = "裁判系统已确认"
            succeeded = True
        elif revision_changed and explicit in {"rejected", "denied", "failed"}:
            transaction.status = "rejected"
            transaction.message = "裁判系统拒绝请求"
        elif not transaction.request.mock and self._official_result_observed(state, transaction):
            transaction.status = "succeeded"
            transaction.message = "裁判系统已确认"
            succeeded = True
        elif now >= transaction.deadline_at:
            transaction.status = "timed_out"
            transaction.message = "未确认；不会自动重试，避免 QoS1 重复扣款"
        else:
            return False
        transaction.completed_at = now
        self._finish(transaction)
        if succeeded:
            # One observation credits at most one transaction.  A teammate's
            # purchase, or a referee feed that batched two commands, must not
            # be able to mark a second outstanding request as "confirmed" on
            # the strength of the first one's evidence.
            self._rebase_rest(state, transaction)
        return True

    def _finish(self, transaction: EconomyTransaction) -> None:
        self._inflight.pop(transaction.request.sequence, None)
        if transaction.status != "timed_out" or transaction.request.mock:
            return
        # A timed-out request may still be sitting in the broker's QoS1 queue.
        # Block an identical re-send until the settle window closes.
        self._ambiguous[transaction.request.identity] = (
            transaction.completed_at + self.ambiguous_settle_s
        )

    def _rebase_rest(self, state: Any, settled: EconomyTransaction) -> None:
        """Move every other in-flight baseline onto the state as it is now."""

        if not self._inflight:
            return
        gold = int(getattr(state, "remaining_economy", settled.baseline_gold))
        ammo = int(getattr(state, "remaining_ammo", settled.baseline_ammo))
        health = int(getattr(state, "current_health", settled.baseline_health))
        revision = int(getattr(state, "economy_status_revision", settled.baseline_revision))
        pending = bool(getattr(state, "respawn_pending", settled.baseline_respawn_pending))
        support = bool(getattr(state, "air_support_active", settled.baseline_support_active))
        for transaction in self._inflight.values():
            transaction.baseline_gold = gold
            transaction.baseline_ammo = ammo
            transaction.baseline_health = health
            transaction.baseline_revision = revision
            transaction.baseline_respawn_pending = pending
            transaction.baseline_support_active = support

    @staticmethod
    def _official_result_observed(state: Any, transaction: EconomyTransaction) -> bool:
        revision = int(getattr(state, "economy_status_revision", 0))
        if revision <= transaction.baseline_revision:
            return False
        key = transaction.offer.key
        gold = int(getattr(state, "remaining_economy", transaction.baseline_gold))
        ammo = int(getattr(state, "remaining_ammo", transaction.baseline_ammo))
        health = int(getattr(state, "current_health", transaction.baseline_health))
        if "ammo" in key or "_17_" in key or "_42_" in key:
            # Team gold is shared.  A teammate spending coins is not an ACK for
            # this robot.  Only the local RobotDynamicStatus ammo delta can
            # confirm a projectile request.
            return ammo > transaction.baseline_ammo
        if "heal" in key:
            return health > transaction.baseline_health
        if "respawn" in key:
            pending = bool(getattr(state, "respawn_pending", True))
            return health > transaction.baseline_health or (transaction.baseline_respawn_pending and not pending)
        if "support" in key:
            desired = "cancel" not in key
            active = bool(getattr(state, "air_support_active", transaction.baseline_support_active))
            return active == desired and active != transaction.baseline_support_active
        return False

    def _apply_mock(self, state: Any, transaction: EconomyTransaction, now: float) -> None:
        request = transaction.request
        offer = transaction.offer
        state.economy_managed = True
        state.remaining_economy = max(0, int(getattr(state, "remaining_economy", 0)) - request.cost)
        capacity_before = int(getattr(state, "ammo_capacity", 0))
        if offer.category == "ammo":
            added = max(0, offer.minimum * request.quantity)
            state.remaining_ammo = max(0, int(getattr(state, "remaining_ammo", 0))) + added
            state.ammo_capacity = max(capacity_before, state.remaining_ammo)
            # The capacity bump above already carries the purchase into the
            # generator, so only the remainder needs a separate credit --
            # otherwise the same rounds would be counted twice.
            self._mock_extra_ammo += max(0, added - max(0, state.ammo_capacity - capacity_before))
        elif offer.key == "rmuc_remote_heal":
            maximum = max(1, int(getattr(state, "max_health", 1)))
            state.current_health = min(maximum, int(getattr(state, "current_health", 0)) + round(maximum * 0.60))
        elif "respawn" in offer.key:
            state.current_health = max(1, int(getattr(state, "max_health", 1)))
            state.respawn_pending = False
            state.respawn_progress = 1.0
        elif offer.topic == "AirSupportCommand":
            state.air_support_active = offer.command_type != 0
        state.economy_status_revision = int(getattr(state, "economy_status_revision", 0)) + 1
        state.economy_command_status = "succeeded"
        # Re-anchor the live feed on the values this purchase produced.  The
        # anchor is what keeps the value identical on the settling frame (the
        # drift term is exactly zero at this instant) and what makes every
        # later frame continue from "post-purchase level + referee movement"
        # instead of snapping back to an absolute generator value.
        self._mock_state = state
        self._mock_anchor_gold = int(getattr(state, "remaining_economy", 0))
        self._mock_anchor_ammo = int(getattr(state, "remaining_ammo", 0))
        self._mock_anchor_income = int(getattr(state, "total_economy_obtained", 0))
        # The anchor uses the *same* clock ``update`` was called with, so the
        # drift term is exactly zero on the settling frame and the value the
        # operator just paid for is reproduced bit-for-bit.  The absolute
        # phase of the generator cancels out of the difference, so the
        # ``start_time`` offset the model applies never matters.
        self._mock_anchor_clock = float(now)
        self._mock_anchor_stage = int(getattr(state, "stage_elapsed_sec", 0))

    # -- live mock referee feed -------------------------------------------
    #
    # ``_apply_mock`` used to raise ``state.economy_managed`` once and never
    # clear it.  ``RobotState.tick`` skips its gold / ammo / total-income
    # generator while that flag is set, so a single mock purchase silently
    # killed the desktop referee feed for the rest of the session: measured on
    # the shipped code, 3600 ticks (60 s) after one 10-gold / 10-round mock
    # purchase, gold and ammo had not moved by a single unit.
    #
    # The bridge now keeps ownership -- the flag is still what tells
    # ``RobotState.tick`` to stay out of the way -- but *drives* those three
    # fields itself, as "level at the last settlement + the movement the
    # referee generator would have produced since".  The generator formulas
    # below are a verbatim copy of ``RobotState.tick``; ``tests/
    # economy_safety_test.py`` re-reads model.py and fails if the two drift.
    #
    # Ownership is handed back the moment the authoritative feed owns those
    # fields again, so a mock delta can never be re-applied on top of a real
    # referee value.

    @staticmethod
    def _mock_gold_generator(clock: float) -> int:
        """``int(260 + 80 * sin(elapsed * 0.16))`` from ``RobotState.tick``."""

        return int(260 + 80 * math.sin(clock * 0.16))

    @staticmethod
    def _mock_ammo_generator(stage_elapsed: int, capacity: int) -> int:
        """``max(0, cap - (stage * 0.18) % max(1, min(80, cap)))`` -- verbatim."""

        return max(0, int(capacity - (stage_elapsed * 0.18) % max(1, min(80, capacity))))

    @staticmethod
    def _mock_income_generator(stage_elapsed: int) -> int:
        """``620 + int(stage * 1.7)`` from ``RobotState.tick``."""

        return 620 + int(stage_elapsed * 1.7)

    @staticmethod
    def _mock_feed_owns(state: Any) -> bool:
        """True while the mock referee generator is the one writing these fields."""

        if not bool(getattr(state, "mock_simulation_enabled", True)):
            return False
        if bool(getattr(state, "official_state_active", False)):
            return False
        return bool(getattr(state, "economy_mock_mode", True))

    def _maintain_mock_feed(self, state: Any, now: float) -> None:
        owned = self._mock_state
        if owned is None:
            return
        if owned is not state or not self._mock_feed_owns(state):
            # The authoritative feed owns gold / ammo again.  Release the flag
            # and drop every accumulated mock delta so a real referee value
            # can never be shifted by a mock purchase.
            self._release_mock_feed()
            return
        stage = max(0, int(getattr(state, "stage_elapsed_sec", 0)))
        capacity = max(0, int(getattr(state, "ammo_capacity", 0)))
        gold_drift = self._mock_gold_generator(now) - self._mock_gold_generator(self._mock_anchor_clock)
        ammo_drift = self._mock_ammo_generator(stage, capacity) - self._mock_ammo_generator(
            self._mock_anchor_stage, capacity
        )
        income_drift = self._mock_income_generator(stage) - self._mock_income_generator(self._mock_anchor_stage)
        state.remaining_economy = max(0, self._mock_anchor_gold + gold_drift)
        state.remaining_ammo = min(capacity, max(0, self._mock_anchor_ammo + ammo_drift + self._mock_extra_ammo))
        state.total_economy_obtained = max(0, self._mock_anchor_income + income_drift)

    def _release_mock_feed(self) -> None:
        owned = self._mock_state
        self._mock_state = None
        self._mock_anchor_gold = 0
        self._mock_anchor_ammo = 0
        self._mock_anchor_income = 0
        self._mock_anchor_clock = 0.0
        self._mock_anchor_stage = 0
        self._mock_extra_ammo = 0
        if owned is not None:
            owned.economy_managed = False


def request_payload(request: EconomyRequest) -> dict[str, Any]:
    data = asdict(request)
    data["payload"] = request.payload.hex()
    return {"protocol": "rm.economy.request.v2", **data}
