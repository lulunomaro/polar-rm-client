from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import time
from typing import Any, Callable, Protocol

from .economy_values import CATEGORY_TITLES, TRANSACTION_LABELS, EconomyContext, EconomyOffer


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


def encode_common_command(command_type: int, param: int) -> bytes:
    """Compatibility API for callers that explicitly target the default year."""
    from .backend.referee.registry import get_profile
    return get_profile().codec.encode_common_command(command_type, param)


def encode_air_support_command(command_id: int) -> bytes:
    from .backend.referee.registry import get_profile
    return get_profile().codec.encode_air_support_command(command_id)


def available_actions(state: Any) -> list[EconomyOffer]:
    """Dispatch annual rules without silently accepting an unsupported year."""
    from .backend.referee.registry import DEFAULT_PROFILE, get_profile
    return get_profile(getattr(state, "referee_profile", DEFAULT_PROFILE)).available_actions(state)



class EconomyCommandBridge:
    """Transaction boundary shared by official MQTT and deterministic mock."""

    def __init__(self) -> None:
        self.sequence = 0
        self.last_request: EconomyRequest | None = None
        self.history: list[EconomyTransaction] = []
        self.sender: Callable[[EconomyRequest], bool] | None = None

    @property
    def latest(self) -> EconomyTransaction | None:
        return self.history[-1] if self.history else None

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
        from .backend.referee.registry import DEFAULT_PROFILE, get_profile
        codec = get_profile(getattr(state, "referee_profile", DEFAULT_PROFILE)).codec
        payload = (
            codec.encode_air_support_command(offer.command_type)
            if offer.topic == "AirSupportCommand"
            else codec.encode_common_command(offer.command_type, command_param)
        )
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
            deadline_at=now + (2.0 if mock else 5.0),
            mock_complete_at=now + 0.36 if mock else 0.0,
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
        if not mock:
            accepted = bool(self.sender and self.sender(request))
            if not accepted:
                transaction.status = "rejected"
                transaction.message = "MQTT 请求未发送"
                transaction.completed_at = now
        return transaction

    def update(self, state: Any, now: float | None = None) -> EconomyTransaction | None:
        transaction = self.latest
        if transaction is None or transaction.status != "pending":
            return transaction
        now = time.perf_counter() if now is None else float(now)
        if transaction.request.mock and now >= transaction.mock_complete_at:
            self._apply_mock(state, transaction)
            transaction.status = "succeeded"
            transaction.message = "MOCK 操作成功"
            transaction.completed_at = now
            return transaction
        explicit = str(getattr(state, "economy_command_status", "")).lower()
        revision_changed = int(getattr(state, "economy_status_revision", 0)) > transaction.baseline_revision
        if revision_changed and explicit in {"succeeded", "accepted", "success"}:
            transaction.status = "succeeded"
            transaction.message = "裁判系统已确认"
            transaction.completed_at = now
        elif revision_changed and explicit in {"rejected", "denied", "failed"}:
            transaction.status = "rejected"
            transaction.message = "裁判系统拒绝请求"
            transaction.completed_at = now
        elif not transaction.request.mock and self._official_result_observed(state, transaction):
            transaction.status = "succeeded"
            transaction.message = "裁判系统已确认"
            transaction.completed_at = now
        elif now >= transaction.deadline_at:
            transaction.status = "timed_out"
            transaction.message = "未确认；不会自动重试，避免 QoS1 重复扣款"
            transaction.completed_at = now
        return transaction

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

    @staticmethod
    def _apply_mock(state: Any, transaction: EconomyTransaction) -> None:
        request = transaction.request
        offer = transaction.offer
        state.economy_managed = True
        state.remaining_economy = max(0, int(getattr(state, "remaining_economy", 0)) - request.cost)
        if offer.category == "ammo":
            added = max(0, offer.minimum * request.quantity)
            state.remaining_ammo = max(0, int(getattr(state, "remaining_ammo", 0))) + added
            state.ammo_capacity = max(int(getattr(state, "ammo_capacity", 0)), state.remaining_ammo)
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


def request_payload(request: EconomyRequest) -> dict[str, Any]:
    data = asdict(request)
    data["payload"] = request.payload.hex()
    return {"protocol": "rm.economy.request.v2", **data}
