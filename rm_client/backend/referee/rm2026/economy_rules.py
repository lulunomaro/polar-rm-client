"""RM2026/RMUL3v3 economy offer policy, separate from UI and MQTT.

Changing annual prices/command meanings must not change the transaction lifecycle.
"""
from __future__ import annotations
import math
from typing import Any
from ....economy_values import EconomyContext, EconomyOffer

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

