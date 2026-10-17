"""Value-only economy presentation decisions shared by both render adapters.

Inputs are a selected immutable offer and an explicit interaction snapshot.
This module does not read clocks, mutate the window, send requests, or import Qt.
The adapters retain animation, paint/GPU submission, and hotspot registration.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..economy_values import EconomyOffer
from .economy_interaction import HOLD_SECONDS


@dataclass(frozen=True)
class EconomyConfirmation:
    text: str
    enabled: bool
    action: str = "economy_confirm"
    progress: float = 0.0
    # The compatibility painter has historically retained the held fill after
    # availability changes, including a minimum fill at zero progress. Preserve
    # that separate visual input without treating it as purchase eligibility.
    held_progress: float | None = None


@dataclass(frozen=True)
class EconomyConfirmationState:
    selected: EconomyOffer | None
    pending: bool
    hold_key: str
    hold_started: float
    armed_key: str
    armed_until: float

    def present(self, *, now: float) -> EconomyConfirmation:
        selected = self.selected
        held_progress = None
        if selected is not None and selected.confirmation == "hold" and self.hold_key == selected.key:
            held_progress = max(0.0, min(1.0, (now - self.hold_started) / HOLD_SECONDS))

        if selected is None:
            return EconomyConfirmation("请先选择商品", False)
        if self.pending:
            return EconomyConfirmation("请求处理中…", False, held_progress=held_progress)
        if not selected.enabled or selected.status_only:
            return EconomyConfirmation(selected.reason or "当前不可用", False, held_progress=held_progress)
        if selected.confirmation == "hold":
            progress = held_progress if held_progress is not None else 0.0
            text = "按住 0.6 秒确认" if progress <= 0.0 else f"确认中 {round(progress * 100)}%"
            return EconomyConfirmation(text, True, "economy_confirm_hold", progress, held_progress)
        if self.armed_key == selected.key and now <= self.armed_until:
            remaining = max(0.0, self.armed_until - now)
            text = f"再点一次确认（{remaining:.1f}s）"
        else:
            text = f"确认购买 · {selected.cost} 金币"
        return EconomyConfirmation(text, True)
