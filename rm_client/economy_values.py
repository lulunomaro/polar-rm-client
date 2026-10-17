"""Shared economy observations, offers and vocabulary, independent of dispatch.

Annual rules produce these immutable values; transaction and presentation
callers consume them. This module does not resolve a referee profile, encode
commands, own a sender, or import the transaction implementation. Display label
dictionaries retain their historical identity and in-place mutation behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
