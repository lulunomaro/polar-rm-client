"""Gestalt HUD AttrId → RobotState 字段映射（与 Workshop UMD 枚举对齐）。

数据根源：游戏 attribute_update 中的 battle / global / base / playerBattle。
"""

from __future__ import annotations

from typing import Any, Mapping


PROTOCOL = "polar.gestalt.attrs.v1"
DEFAULT_RELAY_PORT = 9840

# ERobotBridgeDemoAttributeId（节选，与 gestalt-hud-sdk.workshop.umd.js 一致）
class Attr:
    Health = 10000003
    BufferEnergy = 10000004
    CapacityEnergy = 10000005
    ChassisEnergy = 10000006
    ChassisPower = 10000007
    ChassisRealPower = 10000008
    FiringHeat1 = 10000011
    FiringHeat2 = 10000012
    Shield = 10000013
    FiringHeatCoolingRate1 = 10000017
    FiringHeatCoolingRate2 = 10000018
    Ammo17mmCount = 10000033
    Ammo42mmCount = 10000034
    PlayerID = 10000035
    TeamID = 10000036
    TeamNumber = 10000037
    # 世界坐标（cm），与 Gestalt / sp_vision bridge 一致
    WorldPosX = 10000107
    WorldPosY = 10000108
    WorldPosZ = 10000109
    ChassisYaw = 10000110
    TurretYaw = 10000111
    TurretPitch = 10000112
    Class = 60000002
    Level = 60000003
    HealthMax = 60000004
    BufferEnergyMax = 60000005
    CapacityEnergyMax = 60000006
    ChassisEnergyMax = 60000007
    ChassisPowerMax = 60000008
    AmmoCountMax = 60000010
    FiringHeatMax1 = 60000011
    FiringHeatMax2 = 60000013
    LevelMax = 60000018
    BulletType = 62000000
    G_MaxGameTime = 80000001
    G_CurGameTime = 80000002
    G_CurMapId = 80000003
    G_CurMatchStatus = 80000005
    G_GameStartCountDown = 80000006
    TM_Coins = 74000003
    TM_LevelMax = 74000004
    G_ControlZone_TeamID = 74000009
    TM_BaseDamageCount = 74000010
    TM_OutPostRebuildCount = 74000011
    G_BlueOutpostZone_TeamID = 74000014
    G_RedOutpostZone_TeamID = 74000015
    G_BlueBaseCountdown = 74000016
    G_RedBaseCountdown = 74000017
    Overheated = 50000003


# 真源接通后：若包内未写入这些展示字段，置为 -1，UI 显示 "—"（避免残留 5000 演示）
GESTALT_STATUS_FIELDS = (
    "base_health",
    "base_max_health",
    "enemy_base_health",
    "enemy_base_max_health",
    "outpost_health",
    "outpost_max_health",
    "enemy_outpost_health",
    "enemy_outpost_max_health",
    "ally_victory_points",
    "enemy_victory_points",
    "red_score",
    "blue_score",
    "control_progress",
)

# Gestalt Class → 显示名
_CLASS_NAME = {
    1001: "Hero",
    1002: "Engineer",
    1003: "Infantry",
    1004: "Sentry",
    1005: "Aerial",
    1006: "Dart",
    1007: "Radar",
    1000: "Base",
    2001: "Base",
}


def world_cm_to_field_m(
    x_cm: float,
    y_cm: float,
    *,
    field_w_m: float = 28.0,
    field_h_m: float = 15.0,
    origin: str = "center",
    swap_xy: bool = False,
    flip_x: bool = False,
    flip_y: bool = False,
) -> tuple[float, float]:
    """Gestalt WorldPos(cm) → 小地图场地坐标(m)。

    默认 origin=center：世界原点约在场地中心，映射到 [0,W]×[0,H]。
    （与 bridge 红前哨约 (-3.81,-2.83)m 的世界系一致。）
    """
    x_m = float(x_cm) / 100.0
    y_m = float(y_cm) / 100.0
    if swap_xy:
        x_m, y_m = y_m, x_m
    if origin == "center":
        fx = x_m + field_w_m * 0.5
        fy = y_m + field_h_m * 0.5
    else:
        fx, fy = x_m, y_m
    if flip_x:
        fx = field_w_m - fx
    if flip_y:
        fy = field_h_m - fy
    return fx, fy


def _num(map_: Mapping[Any, Any], key: int, default: float | None = None) -> float | None:
    if key not in map_ and str(key) not in map_:
        return default
    raw = map_.get(key, map_.get(str(key)))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _int(map_: Mapping[Any, Any], key: int, default: int | None = None) -> int | None:
    value = _num(map_, key, None)
    if value is None:
        return default
    return int(value)


def apply_gestalt_attrs_to_state(state: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    """把 polar.gestalt.attrs.v1 快照写入 RobotState，返回实际写入的字段名。"""
    battle = payload.get("battle") or {}
    global_ = payload.get("global") or {}
    base = payload.get("base") or {}
    player_battle = payload.get("playerBattle") or payload.get("player_battle") or {}
    if not isinstance(battle, dict):
        battle = {}
    if not isinstance(global_, dict):
        global_ = {}
    if not isinstance(base, dict):
        base = {}
    if not isinstance(player_battle, dict):
        player_battle = {}

    written: dict[str, Any] = {}

    def set_field(name: str, value: Any) -> None:
        if value is None:
            return
        if hasattr(state, name):
            setattr(state, name, value)
            written[name] = value

    # 多桶查找：battle 优先，其次 player / playerBattle 本车
    def first_map_with(*keys: int) -> Mapping[Any, Any]:
        for m in (battle, player_battle if isinstance(player_battle, dict) else {}, payload.get("player") or {}):
            if not isinstance(m, dict):
                continue
            # playerBattle 可能是 id→attrs
            if m is player_battle and m and not any(k in m or str(k) in m for k in keys):
                for _pid, sub in m.items():
                    if isinstance(sub, dict) and any(k in sub or str(k) in sub for k in keys):
                        return sub
                continue
            if any(k in m or str(k) in m for k in keys):
                return m
        return battle

    heat_src = first_map_with(Attr.FiringHeat1, Attr.FiringHeat2, Attr.FiringHeatMax1, Attr.FiringHeatCoolingRate1)

    # 本车 battle / 热源桶
    set_field("current_health", _int(battle, Attr.Health))
    set_field("max_health", _int(battle, Attr.HealthMax))
    heat1 = _num(heat_src, Attr.FiringHeat1)
    heat2 = _num(heat_src, Attr.FiringHeat2)
    if heat1 is not None or heat2 is not None:
        set_field("current_heat", float(heat1 or 0.0) + float(heat2 or 0.0))
    hmax1 = _num(heat_src, Attr.FiringHeatMax1)
    hmax2 = _num(heat_src, Attr.FiringHeatMax2)
    if hmax1 is not None or hmax2 is not None:
        set_field("max_heat", max(float(hmax1 or 0.0), float(hmax2 or 0.0), 1.0))
    cool1 = _num(heat_src, Attr.FiringHeatCoolingRate1)
    cool2 = _num(heat_src, Attr.FiringHeatCoolingRate2)
    cool_vals = [c for c in (cool1, cool2) if c is not None]
    if cool_vals:
        # 双管：取有效冷却率中较大者（与 RM 显示习惯一致）
        set_field("heat_cooldown_rate", max(cool_vals))
    set_field("current_buffer_energy", _int(battle, Attr.BufferEnergy))
    set_field("max_buffer_energy", _int(battle, Attr.BufferEnergyMax))
    set_field("current_chassis_energy", _int(battle, Attr.ChassisEnergy))
    set_field("max_chassis_energy", _int(battle, Attr.ChassisEnergyMax))
    cap = _num(battle, Attr.CapacityEnergy)
    cap_max = _num(battle, Attr.CapacityEnergyMax)
    if cap is not None and cap_max and cap_max > 0:
        set_field("capacitor_percent", int(max(0, min(100, (cap / cap_max) * 100))))
    power = _num(battle, Attr.ChassisRealPower)
    if power is None:
        power = _num(battle, Attr.ChassisPower)
    if power is not None:
        set_field("chassis_power_w", power)
    pmax = _int(battle, Attr.ChassisPowerMax)
    if pmax is not None:
        set_field("power_limit", pmax)

    bullet_type = _int(battle, Attr.BulletType)
    ammo17 = _int(battle, Attr.Ammo17mmCount)
    ammo42 = _int(battle, Attr.Ammo42mmCount)
    if bullet_type == 0 and ammo42 is not None:
        set_field("remaining_ammo", ammo42)
        set_field("projectile_allowance_42mm", ammo42)
    elif ammo17 is not None:
        set_field("remaining_ammo", ammo17)
        set_field("projectile_allowance_17mm", ammo17)
    if ammo42 is not None:
        set_field("projectile_allowance_42mm", ammo42)
    if ammo17 is not None:
        set_field("projectile_allowance_17mm", ammo17)

    set_field("level", _int(battle, Attr.Level))
    pid = _int(battle, Attr.PlayerID)
    if pid is not None:
        set_field("robot_id", pid)
    team = _int(battle, Attr.TeamID)
    if team == 0:
        set_field("alliance_color", "Red")
    elif team == 1:
        set_field("alliance_color", "Blue")

    # 全局时间（毫秒）
    max_t = _num(global_, Attr.G_MaxGameTime)
    cur_t = _num(global_, Attr.G_CurGameTime)
    if max_t is not None and cur_t is not None and max_t > 0:
        remaining_ms = max(0.0, max_t - cur_t)
        set_field("stage_countdown_sec", int(remaining_ms / 1000.0))
        set_field("stage_elapsed_sec", int(cur_t / 1000.0))
        set_field("match_duration_sec", int(max_t / 1000.0))
    match_status = _int(global_, Attr.G_CurMatchStatus)
    if match_status is not None:
        set_field("current_stage", str(match_status))
    coins = _int(global_, Attr.TM_Coins)
    if coins is None:
        coins = _int(battle, Attr.TM_Coins)
    if coins is not None:
        set_field("remaining_economy", coins)

    # 控制区归属：0=红 1=蓝（与 TeamID 一致）
    cz = _int(global_, Attr.G_ControlZone_TeamID)
    local_team = _int(battle, Attr.TeamID)
    if cz is not None:
        if local_team is not None and cz == local_team:
            set_field("control_owner", "ALLY")
        elif cz in (0, 1):
            set_field("control_owner", "ENEMY" if local_team is not None else ("RED" if cz == 0 else "BLUE"))
        else:
            set_field("control_owner", "NEUTRAL")

    # base / outpost 实体：按 TeamID 区分己方敌方，避免 sort 前二乱序
    base_items: list[tuple[str, dict[str, Any]]] = []
    for key, value in base.items():
        if isinstance(value, dict):
            base_items.append((str(key), value))

    def _entity_team(ent: Mapping[Any, Any]) -> int | None:
        return _int(ent, Attr.TeamID)

    def _is_outpost_key(key: str) -> bool:
        low = key.lower()
        return "outpost" in low or "out_post" in low or "前哨" in key

    own_bases: list[dict[str, Any]] = []
    foe_bases: list[dict[str, Any]] = []
    own_outposts: list[dict[str, Any]] = []
    foe_outposts: list[dict[str, Any]] = []
    for key, ent in base_items:
        t = _entity_team(ent)
        outp = _is_outpost_key(key)
        # 无 TeamID 时按 key 数字奇偶弱推断
        if t is None:
            try:
                t = int(key) % 2
            except ValueError:
                t = None
        ally = local_team is not None and t == local_team
        foe = local_team is not None and t is not None and t != local_team
        if outp:
            if ally:
                own_outposts.append(ent)
            elif foe:
                foe_outposts.append(ent)
            else:
                foe_outposts.append(ent)
        else:
            if ally:
                own_bases.append(ent)
            elif foe:
                foe_bases.append(ent)
            else:
                # 未知队：按出现顺序先己后敌
                if not own_bases:
                    own_bases.append(ent)
                else:
                    foe_bases.append(ent)

    def _apply_hp(prefix: str, ent: Mapping[Any, Any] | None) -> None:
        if not ent:
            return
        hp = _int(ent, Attr.Health)
        hp_max = _int(ent, Attr.HealthMax)
        if hp is not None:
            set_field(f"{prefix}_health", hp)
        if hp_max is not None:
            set_field(f"{prefix}_max_health", hp_max)
        elif hp is not None:
            set_field(f"{prefix}_max_health", max(hp, 1))
        sh = _int(ent, Attr.Shield)
        if sh is not None and prefix == "base":
            set_field("base_shield", sh)
            set_field("base_status", "SHIELDED" if sh > 0 else "EXPOSED")
        elif sh is not None and prefix == "enemy_base":
            set_field("enemy_base_shield", sh)
            set_field("enemy_base_status", "SHIELDED" if sh > 0 else "EXPOSED")

    _apply_hp("base", own_bases[0] if own_bases else None)
    _apply_hp("enemy_base", foe_bases[0] if foe_bases else None)
    _apply_hp("outpost", own_outposts[0] if own_outposts else None)
    _apply_hp("enemy_outpost", foe_outposts[0] if foe_outposts else None)
    if own_outposts:
        oh = _int(own_outposts[0], Attr.Health, 0) or 0
        set_field("outpost_status", "ONLINE" if oh > 0 else "DOWN")
    if foe_outposts:
        oh = _int(foe_outposts[0], Attr.Health, 0) or 0
        set_field("enemy_outpost_status", "ONLINE" if oh > 0 else "DOWN")

    # 本车姿态（真源）
    pose_src = first_map_with(Attr.WorldPosX, Attr.ChassisYaw, Attr.TurretYaw)
    cyaw = _num(pose_src, Attr.ChassisYaw)
    tyaw = _num(pose_src, Attr.TurretYaw)
    if cyaw is not None:
        set_field("chassis_yaw_deg", float(cyaw))
    if tyaw is not None:
        set_field("gimbal_yaw_deg", float(tyaw))
    if cyaw is not None and tyaw is not None:
        set_field("relative_yaw_deg", float(tyaw) - float(cyaw))

    # 场地尺寸（用于 WorldPos → 小地图）
    mode = str(getattr(state, "match_mode", "RMUC7v7") or "RMUC7v7")
    if mode.upper().startswith("RMUL") or "3V3" in mode.upper():
        field_w, field_h = 12.0, 8.0
    else:
        field_w, field_h = 28.0, 15.0

    # 多玩家 / 本车 battle 快照 → robot_rows + map_robots（优先 WorldPos 真坐标）
    entity_maps: list[tuple[Any, dict[str, Any]]] = []
    if isinstance(player_battle, dict):
        for key, value in player_battle.items():
            if isinstance(value, dict):
                entity_maps.append((key, value))
    # 若 playerBattle 空，用 battle 扁平本车 attrs 补一条
    if not entity_maps and isinstance(battle, dict) and (
        Attr.Health in battle or str(Attr.Health) in battle or Attr.WorldPosX in battle or str(Attr.WorldPosX) in battle
    ):
        entity_maps.append(("local", battle))

    rows: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    local_id = int(getattr(state, "robot_id", 0) or 0)
    for key, value in entity_maps:
        hp = _int(value, Attr.Health, 0) or 0
        hp_max = _int(value, Attr.HealthMax, 600) or 600
        rid = _int(value, Attr.PlayerID)
        if rid is None:
            try:
                rid = int(key)
            except (TypeError, ValueError):
                rid = local_id
        t = _int(value, Attr.TeamID)
        team_str = "ally"
        if local_team is not None and t is not None:
            team_str = "ally" if t == local_team else "enemy"
        elif t == 1:
            team_str = "enemy"
        elif t == 0:
            team_str = "ally"
        cls = _int(value, Attr.Class)
        name = _CLASS_NAME.get(cls or -1, f"R{rid}")
        row = {
            "id": rid,
            "name": name,
            "hp": hp,
            "max": hp_max,
            "state": "ALIVE" if hp > 0 else "DEAD",
            "team": team_str,
            "class": cls,
        }
        rows.append(row)

        wx = _num(value, Attr.WorldPosX)
        wy = _num(value, Attr.WorldPosY)
        wz = _num(value, Attr.WorldPosZ)
        if wx is None or wy is None:
            # 无坐标：不画假点（避免「位置对不上」）
            continue
        fx, fy = world_cm_to_field_m(wx, wy, field_w_m=field_w, field_h_m=field_h, origin="center")
        slots.append({
            "id": rid,
            "team": team_str,
            "name": name,
            "x": fx,
            "y": fy,
            "hp": hp,
            "max": hp_max,
            "highlight": int(rid) == local_id or (local_id == 0 and len(slots) == 0),
            "world_x_cm": float(wx),
            "world_y_cm": float(wy),
            "world_z_cm": float(wz) if wz is not None else 0.0,
            "position_known": True,
        })

    if rows:
        rows.sort(key=lambda row: int(row.get("id", 0)))
        state.robot_rows = rows
        written["robot_rows"] = rows
        for row in rows:
            if int(row.get("id", -1)) == local_id:
                set_field("current_health", int(row["hp"]))
                set_field("max_health", int(row["max"]))
                break

    if slots:
        # 本车高亮
        if local_id:
            for s in slots:
                s["highlight"] = int(s.get("id", -1)) == local_id
        state.map_robots = slots
        written["map_robots"] = slots
        # 同步本车目标距离占位：用地图相对场中心的近似
        for s in slots:
            if s.get("highlight"):
                # 保留已有 target_distance 若来自视觉；否则不瞎写
                break

    # 真源包：未出现的状态栏字段清成 -1，避免 5000 演示残留
    for name in GESTALT_STATUS_FIELDS:
        if name not in written and hasattr(state, name):
            setattr(state, name, -1)
            written[name] = -1

    state.telemetry_source = "gestalt"
    written["telemetry_source"] = "gestalt"
    return written
