"""Read-only student-client presentation adapters. No transport or control code.

Unknown official fields are displayed as unknown, not invented from simulator
defaults. ``student_ui_data`` is a presentation seam for future backend work.
Local skill/gate drafts never masquerade as acknowledged robot state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any


PANEL_MODULES = {
    "InjuryStatistics": "damage",
    "MatchSettlement": "settlement",
    "DartPanel": "darts",
    "SkillConfiguration": "skills",
    "MinecraftInventory": "inventory",
    "ReloadingPanel": "reload",
}
DEVICE_MODULES = {
    "ResourceIsland": ("资源岛", None),
    "GainZone": ("增益点", None),
    "UWBStatus": ("UWB 定位", "uwb"),
    "ArmorStatus": ("装甲模块", "armor"),
    "WirelessCharging": ("无线充电", None),
}
DAMAGE_FIELDS = (
    ("total_damage", "总受伤害"),
    ("small_projectile_damage", "17 mm 弹丸"),
    ("large_projectile_damage", "42 mm 弹丸"),
    ("collision_damage", "碰撞"),
    ("dart_splash_damage", "飞镖溅射"),
    ("module_offline_damage", "模块离线"),
    ("offline_damage", "机器人离线"),
    ("penalty_damage", "违规罚扣"),
    ("server_kill_damage", "裁判判定战亡"),
)
BUFF_LABELS = {1: "攻击增益", 2: "防御 / 易伤", 3: "冷却增益", 4: "底盘功率增益", 5: "恢复增益", 6: "发弹量增益", 7: "地形增益"}


def number(value: Any, fallback: float | None = None) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def value_text(value: Any, suffix: str = "") -> str:
    result = number(value)
    return "—" if result is None else f"{result:g}{suffix}"


def presentation_data(state: Any) -> dict[str, Any]:
    data = getattr(state, "student_ui_data", {})
    return data if isinstance(data, dict) else {}


def damage_rows(state: Any) -> tuple[tuple[str, str], ...]:
    # Reports are cumulative snapshots. Summing the killer-keyed cache would
    # multiply total damage every time the last attacker changes.
    reports = getattr(state, "injury_stats", {}) or {}
    report = presentation_data(state).get("injury_snapshot")
    if not isinstance(report, dict):
        report = next(reversed(reports.values()), {}) if isinstance(reports, dict) else {}
    report = report if isinstance(report, dict) else {}
    return tuple((label, value_text(report.get(key))) for key, label in DAMAGE_FIELDS)


def rune_summary(state: Any) -> str | None:
    data = getattr(state, "rune_status", {}) or {}
    if not isinstance(data, dict) or not data:
        return None
    average = number(data.get("average_rings"))
    arms = number(data.get("activated_arms"))
    # V1.3.1 exposes average_rings/activated_arms, not an authoritative total.
    total = f"  ·  推算总环 {average * arms:g}" if average is not None and arms is not None else ""
    return f"平均环数 {value_text(average)} / 灯臂 {value_text(arms)}{total}"


def device_rows(state: Any, registry: Any) -> tuple[tuple[str, str], ...]:
    result = []
    devices = presentation_data(state).get("devices", {})
    modules = getattr(state, "robot_module_status", {}) or {}
    for key, (label, field_name) in DEVICE_MODULES.items():
        module = registry.get(key)
        if module is None or not module.enabled or not module.get_setting("Show Status").value:
            continue
        raw = modules.get(field_name) if field_name else None
        if field_name and raw is not None:
            text = {0: "离线", 1: "在线", 2: "安装异常 / 视为离线"}.get(number(raw), "状态未知")
        else:
            text = str(devices.get(key, "等待官方状态")) if isinstance(devices, dict) else "等待官方状态"
        result.append((label, text))
    return tuple(result)


def active_buffs(state: Any) -> tuple[dict[str, Any], ...]:
    result = []
    local_id = getattr(state, "robot_id", 0)
    for row in getattr(state, "buffs", ()) or ():
        if not isinstance(row, dict) or row.get("robot_id") != local_id:
            continue
        left = number(row.get("buff_left_time"), 0.0) or 0.0
        if left <= 0:
            continue
        kind = int(number(row.get("buff_type"), 0) or 0)
        level = number(row.get("buff_level"))
        # A negative defence level is explicitly vulnerability, not a bonus.
        name = "易伤增益" if kind == 2 and level is not None and level < 0 else BUFF_LABELS.get(kind, f"增益类型 {kind}")
        result.append({"key": f"buff:{kind}:{level}", "kind": kind, "name": name,
                       "level": value_text(level), "left": left})
    # Optional detailed UI data must still have an explicit active lifetime.
    # Do not infer a rune buff or double vulnerability from a local boolean.
    extra = presentation_data(state).get("buffs", ())
    for row in extra if isinstance(extra, (list, tuple)) else ():
        if isinstance(row, dict) and row.get("robot_id",local_id)==local_id and (number(row.get("left"), 0) or 0) > 0:
            result.append(dict(row))
    return tuple(result[:12])


def settlement_rows(state: Any) -> tuple[tuple[str, str], ...]:
    data = presentation_data(state).get("settlement", {})
    data = data if isinstance(data, dict) else {}
    return (
        ("比赛用时", value_text(getattr(state, "stage_elapsed_sec", None), " s")),
        ("基地血量", value_text(getattr(state, "base_health", None))),
        ("前哨站血量", value_text(getattr(state, "outpost_health", None))),
        ("机器人血量", value_text(getattr(state, "current_health", None))),
        ("哨兵血量", value_text(data.get("sentry_health"))),
        ("官方击杀数", value_text(getattr(state, "referee_kill_count", None))),
        ("造成伤害", value_text(data.get("damage_dealt"))),
        ("胜利点", value_text(data.get("victory_points"))),
        ("破甲次数", value_text(data.get("armor_breaks"))),
        ("能量机关", rune_summary(state) or "—"),
    )


@dataclass
class StudentUiState:
    panel: str | None = None
    opened: bool = False
    progress: float = 0.0
    page: int = 0
    category: str = "Combat"
    selected: str = ""
    dragging: str = ""
    selected_slot: int = 0
    gate: str = "Left"
    quantity: int = 50
    dart_target: str = "前哨站"
    note: str = ""
    previous_stage: str = ""
    settlement: tuple[tuple[str, str], ...] = ()
    revision: int = 0

    @property
    def visible(self) -> bool:
        return self.opened or self.progress > 0.001

    def open(self, panel: str) -> None:
        if self.panel != panel:
            self.progress = 0.0
            self.page = 0
            self.dragging = ""
            self.note = ""
        self.panel, self.opened = panel, True
        self.revision += 1

    def close(self, immediate: bool = False) -> None:
        self.opened = False
        self.dragging = ""
        if immediate:
            self.progress, self.panel = 0.0, None
        self.revision += 1

    def update(self, dt: float) -> bool:
        before = self.progress
        dt = max(0.0, number(dt, 0.0) or 0.0)
        if self.opened:
            self.progress = min(1.0, self.progress + dt / 0.450)
        else:
            self.progress = max(0.0, self.progress - dt / 0.125)
            if self.progress <= 0.001:
                self.panel = None
        return before != self.progress


def student_modules() -> list[Any]:
    # Catalog extensions depend on settings, not robot runtime state.
    from .module_settings import Module, action_setting, bool_setting, mode_setting, number_setting
    modules = []
    descriptions = {
        "InjuryStatistics": "按住 ~ 查看 Minecraft 服务器式受伤害计分板；裁判累计数据，不重复相加。",
        "MatchSettlement": "Jello 结算面板：血量、击杀、伤害、能量机关及破甲；未知字段不伪造。",
        "DartPanel": "Jello 飞镖目标、闸门、初速度和冷却面板；操作只保存前端草稿。",
        "SkillConfiguration": "性能点 / 技能配置；草稿不下发，不改变已同步的性能选择。",
        "MinecraftInventory": "E 打开 Minecraft 背包；模块拖入九格栏，合成台为技能 / 性能配置。",
        "ReloadingPanel": "O / I 进入买弹区：实体储备、允许发弹量、左右闸口和补弹草稿。",
    }
    actions = {"InjuryStatistics": "Open Injury Statistics", "MatchSettlement": "Open Settlement",
               "DartPanel": "Open Dart Panel", "SkillConfiguration": "Open Skill Configuration",
               "MinecraftInventory": "Open Minecraft Inventory", "ReloadingPanel": "Open Reloading Panel"}
    for name in PANEL_MODULES:
        settings = [action_setting(actions[name])]
        if name == "SkillConfiguration":
            settings += [number_setting("Chassis Points", 0, 0, 100, 1), number_setting("Shooter Points", 0, 0, 100, 1),
                         mode_setting("Shooter Draft", "Cooling", ["Cooling", "Rate", "Burst"]),
                         mode_setting("Chassis Draft", "Power", ["Power", "Energy"])]
        if name == "MatchSettlement":
            settings += [bool_setting("Auto Open", True)]
        if name == "ReloadingPanel":
            settings += [mode_setting("Gate Draft", "Left", ["Left", "Right"]), number_setting("Quantity Draft", 50, 1, 500, 1)]
        modules.append(Module(name, "Strategy" if name != "MinecraftInventory" else "Render", "None", settings,
                              enabled=True, description=descriptions[name]))
    modules.append(Module("RuneRingStatus", "Strategy", "None", [bool_setting("Show in Island", True)], enabled=True,
                          description="官方平均环数 / 灯臂数显示在现有灵动岛；总环数仅作标注推算。"))
    for name, (label, _) in DEVICE_MODULES.items():
        modules.append(Module(name, "Vision" if name in {"UWBStatus", "ArmorStatus"} else "Strategy", "None",
                              [bool_setting("Show Status", True)], enabled=False, description=f"{label}状态覆盖层；等待官方数据时不显示假在线。"))
    modules.append(Module("MinecraftBuffs", "Render", "None", [number_setting("Icon Scale", 1.0, 0.5, 2.0, 0.1)],
                          enabled=True, description="Minecraft 状态效果图标与持续时间；只显示本机有效官方 Buff。"))
    return modules


class StudentNoticeAdapter:
    """Server-style broadcasts from existing snapshots/events, never a socket."""
    def __init__(self, chat: Any) -> None:
        self.chat = chat
        self._penalty = None
        self._stage = ""
        self._first_blood_eligible = False
        self._first_blood = False
        self._combos: dict[int, tuple[float, int]] = {}
        self._base_hp: float | None = None
        self._alive: dict[bool, int] = {}

    def combat(self, event: Any, combo_window: float = 5.0) -> None:
        if event.kind != "elimination":
            return
        if not event.confirmed or event.source not in {"referee", "official"} or event.killer_id <= 0:
            return  # Visual elimination must not invent official match awards.
        if self._first_blood_eligible and not self._first_blood:
            self._first_blood = True
            self.chat.add("§6§lFIRST BLOOD! §r§e首血 · 机器人 " + str(event.killer_id), source="referee", level="success", now=event.timestamp)
        previous, count = self._combos.get(event.killer_id, (-1e9, 0))
        count = count + 1 if event.timestamp - previous <= combo_window else 1
        self._combos[event.killer_id] = (event.timestamp, count)
        self._combos = {key: val for key, val in self._combos.items() if event.timestamp - val[0] <= combo_window}
        count = max(count, int(event.combo or 0))
        if count > 1:
            label = {2: "DOUBLE KILL · 双杀", 3: "TRIPLE KILL · 三杀", 4: "QUADRA KILL · 四杀"}.get(count, "PENTA KILL · 五杀")
            self.chat.add(f"§6§l{label} §r§e机器人 {event.killer_id}", source="referee", level="success", now=event.timestamp)

    def poll(self, state: Any, now: float) -> None:
        stage = str(getattr(state, "current_stage", ""))
        if stage == "MATCH" and self._stage in {"COUNTDOWN", "PREPARE", "SELF_CHECK"}:
            self._first_blood_eligible, self._first_blood = True, False
            self._combos.clear(); self._alive.clear(); self._base_hp=None
        elif stage not in {"MATCH", "SETTLEMENT"}:
            self._first_blood_eligible = False
        self._stage = stage
        penalty = getattr(state, "active_penalty", {}) or {}
        if penalty:
            kind=int(number(penalty.get("penalty_type"),0) or 0)
            count=int(number(penalty.get("total_penalty_num"),0) or 0)
            key=(kind,count)
            if key != self._penalty:
                self._penalty=key
                label={1:"黄牌警告",2:"双方黄牌",3:"红牌 / 罚下",4:"超功率违规",5:"超热量违规",6:"超射速违规"}.get(kind,f"判罚类型 {kind}")
                warning=" · 红牌机器人无法复活" if kind==3 else ""
                self.chat.add(f"§c[裁判] §e{label} · 累计 {count} · 效果 {value_text(penalty.get('penalty_effect_sec'),' s')}{warning}", source="referee",level="danger" if kind==3 else "warning",now=now)
        # No topology or warning threshold is inferred from the language pack.
        # Only announce actual downward changes and a nonzero→zero team roster.
        if not getattr(state,"official_state_active",False):return
        hp=number(getattr(state,"base_health",None))
        if hp is not None and self._base_hp is not None and hp<self._base_hp:
            self.chat.add("§c[警报] §f我方基地正在被攻击！",source="referee",level="danger",dedupe_key="base-under-attack",now=now)
        self._base_hp=hp
        teams={True:[],False:[]}
        for row in getattr(state,"robot_rows",()):
            robot_id=number(row.get("id"))
            health=number(row.get("hp"))
            if robot_id is not None and health is not None:teams[robot_id<100].append(health)
        local_red=getattr(state,"robot_id",0)<100
        for red,healths in teams.items():
            if not healths:continue
            alive=sum(h>0 for h in healths)
            if self._alive.get(red,0)>0 and alive==0:
                self.chat.add("§6§lTEAM WIPE! §r"+("§c我方团灭" if red==local_red else "§a敌方团灭"),source="referee",level="danger" if red==local_red else "success",now=now)
            self._alive[red]=alive
