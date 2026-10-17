"""Economy input decisions, independent of Qt and application state ownership.

Offers are already qualified by the economy rules. These decisions only express
operator confirmation intent; they do not authorize transport or apply a match
transaction. The window applies writes, notifications and bridge calls in order.
The injected confirmation queries preserve short-circuit observation order:
clock, armed key, and (only for the matching key) expiry. No query is consulted
for an unavailable or hold-only activation.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ..economy_values import EconomyOffer

HOLD_SECONDS = 0.60
DOUBLE_CONFIRM_SECONDS = 2.8


class ActivationAction(Enum):
    REJECT = "reject"
    ARM = "arm"
    SUBMIT = "submit"


@dataclass(frozen=True)
class EconomyActivation:
    action: ActivationAction
    message: str = ""
    armed_until: float = 0.0


class HoldCompletion(Enum):
    SUBMIT = "submit"
    TOO_SHORT = "too_short"
    CANCEL = "cancel"


def unavailable_offer_reason(offer: EconomyOffer) -> str | None:
    """One UI admission check, also rechecked by the transaction bridge."""
    if not offer.enabled or offer.status_only:
        return offer.reason or "当前操作不可用"
    return None


def plan_economy_activation(
    offer: EconomyOffer, *, read_armed_key: Callable[[], str], read_armed_until: Callable[[], float],
    clock: Callable[[], float],
) -> EconomyActivation:
    reason = unavailable_offer_reason(offer)
    if reason is not None:
        return EconomyActivation(ActivationAction.REJECT, reason)
    if offer.confirmation == "hold":
        return EconomyActivation(ActivationAction.REJECT, "请按住确认按钮 0.6 秒")
    now = clock()
    if read_armed_key() == offer.key and now <= read_armed_until():
        return EconomyActivation(ActivationAction.SUBMIT)
    return EconomyActivation(ActivationAction.ARM, armed_until=now + DOUBLE_CONFIRM_SECONDS)


def complete_economy_hold(*, offer_exists: bool, inside: bool, elapsed: float) -> HoldCompletion:
    """Resolve release intent after capture is cleared and offers refreshed.

    A successful intent still goes through submit_economy_offer and the bridge;
    availability and pending-transaction checks cannot be bypassed by this value.
    """
    if offer_exists and inside and elapsed >= HOLD_SECONDS:
        return HoldCompletion.SUBMIT
    if elapsed < HOLD_SECONDS:
        return HoldCompletion.TOO_SHORT
    return HoldCompletion.CANCEL
