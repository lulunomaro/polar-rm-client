"""Interpret radio keyboard/mouse readback without implying control authority."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..control import describe_km_policy


@dataclass(frozen=True)
class RadioKmPolicy:
    name: str
    participates: bool | None
    hint: str


def decode_radio_km_policy(value: Any) -> RadioKmPolicy:
    return RadioKmPolicy(*describe_km_policy(value))


@dataclass(frozen=True)
class RadioKmReadback:
    received: Any = None
    key: Any = None
    policy: Any = None
    key_at: float = 0.0
    key_sequence: int = 0


@dataclass(frozen=True)
class RadioKmFeedback:
    policy: str
    policy_hint: str
    robot_key: int
    robot_key_at: float
    robot_key_sequence: int
    robot_rx: int
    robot_rx_growing: bool
    link_state: str
    link_note: str
    next_received: int | None


def evaluate_radio_km_feedback(readback: RadioKmReadback, *, requested: bool,
                               authorized: bool, previous_received: int | None,
                               now: float, policy: RadioKmPolicy | None = None) -> RadioKmFeedback:
    """Return the complete UI observation and next counter, with no mutation.

    Receipt of frames and firmware participation are separate evidence. Stale
    key samples become unknown; a missing counter preserves the last observed
    counter for the next tick, matching the existing reconnect diagnostics.
    """
    policy = policy if policy is not None else decode_radio_km_policy(readback.policy)
    received = _read_counter(readback.received)
    key = _read_counter(readback.key)
    if key is None or not (readback.key_at > 0 and 0 <= now - readback.key_at <= 0.75):
        key = -1
    growing = received is not None and previous_received is not None and received > previous_received
    state, note = _link_observation(requested=requested, authorized=authorized,
                                    received=received, growing=growing,
                                    policy=policy.name, participates=policy.participates)
    return RadioKmFeedback(policy.name, policy.hint, key, readback.key_at, readback.key_sequence,
                           -1 if received is None else received, growing, state, note,
                           received if received is not None else previous_received)


def _read_counter(value: Any) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _link_observation(*, requested: bool, authorized: bool, received: int | None,
                      growing: bool, policy: str, participates: bool | None) -> tuple[str, str]:
    if not requested:
        return "off", "未开启"
    if not authorized:
        return "blocked", "闸门未过"
    if received is None:
        return "no-readback", "车上计数读不到（插一下 USB 0x5A 就能看到）"
    if growing:
        note = (f"车上已收到 {received} 帧 0x0311，但档位 {policy} 不参与"
                if participates is False else f"车上已收到 {received} 帧 0x0311（档位 {policy}）")
        return "live", note
    if received == 0:
        return "silent", (
            "车上一个 0x0311 都没收到。注意：官方要求图传发送端串口 921600"
            "（规程表 1-1），而本固件只配了 USART1/USART3/USART6，"
            "全仓没有 921600 那一路 —— 所以光把线接到 C 板也不会收到。"
            "两条可行做法：① 用 C 路（直连 DT7，不依赖图传接线）；"
            "② 车载电脑收 0x0311 后经 HostKmFeed 喂进 C 板。"
        )
    return "stalled", f"计数停在 {received}，不再增长：链路中途断了"
