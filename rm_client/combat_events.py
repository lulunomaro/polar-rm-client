from __future__ import annotations

from dataclasses import dataclass
import json
import math
import struct
import time
from typing import Any, Iterable


@dataclass(frozen=True)
class CombatEvent:
    kind: str
    source: str
    confirmed: bool
    timestamp: float
    victim_id: int = 0
    killer_id: int = 0
    detail: str = ""
    kill_type: str = "NORMAL"
    combo: int = 0
    victim_name: str = ""
    distance_m: float = 0.0
    score_delta: int = 0
    tags: tuple[str, ...] = ()


def _read_varint(data: bytes, index: int) -> tuple[int, int] | None:
    value = 0
    shift = 0
    while index < len(data) and shift <= 63:
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7
    return None


def decode_proto_fields(data: bytes) -> dict[int, list[int | bytes]]:
    """仅解析本客户端需要的 protobuf 基础字段，避免额外运行时依赖。"""
    values: dict[int, list[int | bytes]] = {}
    index = 0
    while index < len(data):
        key = _read_varint(data, index)
        if key is None:
            break
        tag, index = key
        field, wire_type = tag >> 3, tag & 0x07
        if field <= 0:
            break
        if wire_type == 0:
            item = _read_varint(data, index)
            if item is None:
                break
            value, index = item
        elif wire_type == 1:
            if index + 8 > len(data):
                break
            value = data[index:index + 8]
            index += 8
        elif wire_type == 2:
            length_info = _read_varint(data, index)
            if length_info is None:
                break
            length, index = length_info
            if length < 0 or index + length > len(data):
                break
            value = data[index:index + length]
            index += length
        elif wire_type == 5:
            if index + 4 > len(data):
                break
            value = data[index:index + 4]
            index += 4
        else:
            break
        values.setdefault(field, []).append(value)
    return values


def _field_int(fields: dict[int, list[int | bytes]], field: int, default: int = 0) -> int:
    values = fields.get(field, ())
    if not values:
        return default
    value = values[-1]
    return int(value) if isinstance(value, int) else default


def _field_bytes(fields: dict[int, list[int | bytes]], field: int) -> bytes:
    values = fields.get(field, ())
    if not values:
        return b""
    value = values[-1]
    return bytes(value) if isinstance(value, bytes) else b""


def _field_float(fields: dict[int, list[int | bytes]], field: int, default: float = 0.0) -> float:
    values = fields.get(field, ())
    if not values:
        return default
    value = values[-1]
    if isinstance(value, bytes) and len(value) == 4:
        return float(struct.unpack("<f", value)[0])
    return float(value) if isinstance(value, int) else default


def _packed_uints(value: int | bytes) -> list[int]:
    if isinstance(value, int):
        return [value]
    values: list[int] = []
    index = 0
    while index < len(value):
        item = _read_varint(value, index)
        if item is None:
            break
        number, index = item
        values.append(number)
    return values


def _topic_name(topic: str) -> str:
    return str(topic).strip().rstrip("/").rsplit("/", 1)[-1]


# 官方下行消息名：只有这些才计入“裁判数据在线”，避免 retain/自定义 topic 假阳性
OFFICIAL_DOWNLINK_MESSAGES = frozenset({
    "GameStatus", "GlobalUnitStatus", "GlobalLogisticsStatus", "GlobalSpecialMechanism",
    "Event", "RobotInjuryStat", "RobotRespawnStatus", "RobotStaticStatus",
    "RobotDynamicStatus", "RobotModuleStatus", "RobotPosition", "Buff", "PenaltyInfo",
    "RobotPathPlanInfo", "RadarInfoToClient", "CustomByteBlock", "TechCoreMotionStateSync",
    "RobotPerformanceSelectionSync", "DeployModeStatusSync", "RuneStatusSync",
    "SentryStatusSync", "DartSelectTargetStatusSync", "SentryCtrlResult",
    "AirSupportStatusSync",
})


def _parse_event_param(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        # 官方 Event.param 是逗号分隔字符串；击杀事件示例为
        # "victim_id,killer_id"，不是 JSON。
        parts = [part.strip() for part in text.split(",")]
        if len(parts) >= 2:
            try:
                return {
                    "victim_id": int(parts[0]),
                    "killer_id": int(parts[1]),
                    "detail": text,
                }
            except ValueError:
                pass
        return {"detail": text}
    if not isinstance(value, dict):
        return {"detail": text}
    return value


class OfficialMatchFeed:
    """将官方 MQTT topic 归一化为状态更新和去重后的战斗事件。"""

    ENEMY_ORDER = (1, 2, 3, 4, 7)

    def __init__(self) -> None:
        self.last_message_at = 0.0
        self.last_kill_data_at = 0.0
        self.last_official_event_at = 0.0
        self._robot_health: list[int] = []
        # (kind, victim_id, killer_id) -> (时间戳, killer_id)
        # killer_id 随记录一起存，是为了做"双来源合并"时判断双方是否都知道
        # 击杀者（见 _dedupe 的副键说明）。
        self._seen: dict[tuple[Any, ...], tuple[float, int]] = {}
        # RobotInjuryStat 是**累计 1 Hz** 报文（backend/manager.py:1781 原文
        # "RobotInjuryStat is a cumulative 1 Hz report"）。None = 还没有基线，
        # 首个样本只建基线，绝不当作一次新击杀 —— 与 manager 的
        # ``injury_stats_initialized`` 同一条取向。
        self._injury_kill_damage: int | None = None

    def is_fresh(self, timeout_s: float = 2.5) -> bool:
        return self.last_message_at > 0.0 and time.perf_counter() - self.last_message_at <= timeout_s

    def is_kill_data_fresh(self, timeout_s: float = 2.5) -> bool:
        """只有可能携带击杀/战亡信息的官方消息才会屏蔽视觉击杀。"""
        return self.last_kill_data_at > 0.0 and time.perf_counter() - self.last_kill_data_at <= timeout_s

    def consume(self, topic: str, payload: bytes, *, alliance: str, local_robot_id: int) -> tuple[list[CombatEvent], dict[str, Any]]:
        now = time.perf_counter()
        name = _topic_name(topic)
        if name in OFFICIAL_DOWNLINK_MESSAGES:
            self.last_message_at = now
        if name in {"Event", "GlobalUnitStatus", "RobotInjuryStat", "RobotStaticStatus", "CustomByteBlock"}:
            self.last_kill_data_at = now
        fields = decode_proto_fields(payload)
        updates: dict[str, Any] = {"official_feed_at": now}
        events: list[CombatEvent] = []

        if name == "GameStatus":
            updates.update({
                "current_round": _field_int(fields, 1, 1),
                "total_rounds": _field_int(fields, 2, 1),
                "red_score": _field_int(fields, 3),
                "blue_score": _field_int(fields, 4),
                "current_stage": {
                    0: "NOT_STARTED", 1: "PREPARE", 2: "SELF_CHECK",
                    3: "COUNTDOWN", 4: "MATCH", 5: "SETTLEMENT",
                }.get(_field_int(fields, 5), str(_field_int(fields, 5))),
                "stage_countdown_sec": _field_int(fields, 6),
                "stage_elapsed_sec": _field_int(fields, 7),
            })
        elif name == "GlobalLogisticsStatus":
            updates.update({
                "remaining_economy": _field_int(fields, 1),
                "total_economy_obtained": _field_int(fields, 2),
                "tech_level": _field_int(fields, 3),
                "encryption_level": _field_int(fields, 4),
            })
        elif name == "GlobalUnitStatus":
            # 字段 11（robot_health）是机器人血量的**唯一**来源。
            #
            # 这里原来写的是 `fields.get(11) or fields.get(6)`，而字段 6 是
            # **敌方基地血量**（backend/protocol.py:313 的 enemy_base_health，
            # 一个标量），不是任何机器人的血量。于是报文缺字段 11 时
            # （被截断 / 协议版本差异 / 解析丢字段）会把基地血量当成一张
            # 长度为 1 的机器人血量表：
            #
            #   health = [enemy_base_health]
            #   own_count = min(5, len(health) // 2) = min(5, 0) = 0
            #     → 第 0 个被当成**敌方**机器人
            #   基地血量从 5000 掉到 0 时 before=5000 > 0 且 current == 0
            #     → 凭空发出 victim_id=101 的"敌方机器人被淘汰"
            #
            # 也就是说：**敌方基地一被摧毁，客户端就报一条根本没发生的淘汰**，
            # 同时把 state.official_robot_health 写成 [5000] 这种离谱值
            # （manager 之后会拿它做 [:10] 切片喂给 HUD）。
            #
            # 拿不到机器人血量就不给，不拿别的字段顶替 —— 与 AGENT.md
            # "不要静默产生假数据"是同一条取向。
            health_values = fields.get(11) or []
            health = [number for value in health_values for number in _packed_uints(value)]
            updates.update({
                "base_health": _field_int(fields, 1),
                "base_status": str(_field_int(fields, 2)),
                "base_shield": _field_int(fields, 3),
                "outpost_health": _field_int(fields, 4),
                "outpost_status": str(_field_int(fields, 5)),
            })
            if health:
                events.extend(self._health_transitions(health, alliance, now))
                updates["official_robot_health"] = health
        elif name == "RobotInjuryStat":
            # 字段 9（server_kill_damage）是**本局累计**伤害，每秒重发同一个
            # 累计值。原来的写法是 `server_kill_damage > 0`，等于拿累计量当边沿：
            # 只要本局打死过一次，客户端就会在每次去重窗口（2.0 s）过期后
            # 重放一条 death，直到重连。实测累计值恒为 500 的 1 Hz 报文，
            # 9 秒内产出 5 条 death。
            #
            # 现在按**增量**判边沿：累计值变大才是一次新的服务端确认击杀。
            # * 首个样本（None 基线）只建基线，不发事件 —— 中途连上时累计值
            #   本来就可能非 0，把它当击杀就是凭空多算一次自己的死。
            # * 累计值回退（复活 / 重连后计数器清零）视为重建基线，同样静默，
            #   与 manager.py:1762 的 counters_monotonic 同一处理。
            # * 报文缺字段 9（截断 / 协议差异）时完全不碰基线。否则一次坏帧
            #   会把基线打成 0，下一帧正常报文就变成"巨额新增"，伪造一条击杀。
            if 9 in fields:
                server_kill_damage = max(0, _field_int(fields, 9))
                baseline = self._injury_kill_damage
                self._injury_kill_damage = server_kill_damage
                if baseline is not None and server_kill_damage > baseline:
                    events.append(CombatEvent(
                        "death", "referee", True, now,
                        killer_id=_field_int(fields, 10), detail="server_kill",
                    ))
        elif name == "RobotStaticStatus":
            robot_id = _field_int(fields, 4)
            alive_state = _field_int(fields, 3)
            if alive_state == 2:
                kind = "death" if robot_id == local_robot_id else "elimination"
                events.append(CombatEvent(kind, "referee", True, now, victim_id=robot_id, detail="alive_state"))
        elif name == "RobotRespawnStatus":
            total = max(0, _field_int(fields, 2))
            current = max(0, _field_int(fields, 3))
            updates.update({
                "respawn_pending": bool(_field_int(fields, 1)),
                "respawn_progress": min(1.0, current / total) if total > 0 else 0.0,
                "respawn_can_free": bool(_field_int(fields, 4)),
                "respawn_gold_cost": _field_int(fields, 5),
                "respawn_can_pay": bool(_field_int(fields, 6)),
            })
        elif name == "RobotDynamicStatus":
            updates.update({
                "current_health": _field_int(fields, 1),
                "current_heat": _field_float(fields, 2),
                "current_chassis_energy": _field_int(fields, 4),
                "current_buffer_energy": _field_int(fields, 5),
                "total_projectiles_fired": _field_int(fields, 8),
                "remaining_ammo": _field_int(fields, 9),
                "out_of_combat": bool(_field_int(fields, 10)),
                "can_remote_heal": bool(_field_int(fields, 12)),
                "can_remote_ammo": bool(_field_int(fields, 13)),
            })
        elif name == "AirSupportStatusSync":
            free_time = _field_int(fields, 2)
            updates.update({
                "air_support_active": _field_int(fields, 1) == 1,
                "air_support_free_time_sec": free_time,
                "air_support_can_free": free_time > 0,
                "air_support_cost_coins": _field_int(fields, 3),
            })
        elif name == "Event":
            event_id = _field_int(fields, 1)
            event_data = _parse_event_param(_field_bytes(fields, 2))
            victim_id = int(event_data.get("victim_id", 0))
            killer_id = int(event_data.get("killer_id", 0))
            detail = str(event_data.get("detail", ""))
            if event_id == 1:
                tags = event_data.get("tags", ())
                events.append(CombatEvent(
                    "elimination", "referee", True, now, victim_id, killer_id, detail or "official_event",
                    kill_type=str(event_data.get("kill_type", "NORMAL")).upper(),
                    combo=int(event_data.get("combo", 0)),
                    victim_name=str(event_data.get("victim_name", "")),
                    distance_m=float(event_data.get("distance_m", 0.0)),
                    score_delta=int(event_data.get("score_delta", 0)),
                    tags=tuple(str(tag) for tag in tags) if isinstance(tags, (list, tuple)) else (),
                ))
            elif event_id in (2, 3):
                events.append(CombatEvent("objective", "referee", True, now, victim_id, killer_id, detail or "objective"))
        elif name == "CustomByteBlock":
            events.extend(self._custom_events(_field_bytes(fields, 1), now))

        filtered = self._dedupe(events)
        if filtered:
            self.last_official_event_at = now
        return filtered, updates

    def _health_transitions(self, health: list[int], alliance: str, now: float) -> list[CombatEvent]:
        previous = self._robot_health
        self._robot_health = health
        if not previous:
            return []
        events: list[CombatEvent] = []
        own_count = min(5, len(health) // 2)
        for index, current in enumerate(health):
            before = previous[index] if index < len(previous) else current
            if before <= 0 or current != 0:
                continue
            if index < own_count:
                continue
            type_id = self.ENEMY_ORDER[(index - own_count) % len(self.ENEMY_ORDER)]
            victim_id = 100 + type_id if alliance == "Red" else type_id
            events.append(CombatEvent("elimination", "referee_health", True, now, victim_id=victim_id, detail="health_zero"))
        return events

    @staticmethod
    def _custom_events(data: bytes, now: float) -> list[CombatEvent]:
        if not data:
            return []
        text = data.decode("utf-8", errors="ignore").strip()
        if text.startswith("POLAR_KILL/1|"):
            parts = text.split("|")
            if len(parts) >= 5:
                try:
                    return [CombatEvent(parts[1].lower() or "elimination", "team_byte_block", True, now, int(parts[3]), int(parts[2]), parts[4])]
                except ValueError:
                    return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(value, dict) or value.get("protocol") != "polar.kill.v1":
            return []
        tags = value.get("tags", ())
        return [CombatEvent(
            str(value.get("kind", "elimination")),
            "team_byte_block",
            bool(value.get("confirmed", True)),
            now,
            int(value.get("victim_id", 0)),
            int(value.get("killer_id", 0)),
            str(value.get("detail", "")),
            str(value.get("kill_type", "NORMAL")).upper(),
            int(value.get("combo", 0)),
            str(value.get("victim_name", "")),
            float(value.get("distance_m", 0.0)),
            int(value.get("score_delta", 0)),
            tuple(str(tag) for tag in tags) if isinstance(tags, (list, tuple)) else (),
        )]

    def _dedupe(self, events: Iterable[CombatEvent]) -> list[CombatEvent]:
        """折叠同一次击杀的多来源副本。

        主键仍是 ``(kind, victim_id, killer_id)``，语义完全不变。问题在于
        **同一次击杀会有三个裁判来源**：``GlobalUnitStatus`` 血量归零、
        ``RobotStaticStatus`` alive_state=2、以及 ``Event`` topic。前两个
        拿不到击杀者（killer_id=0），只有 ``Event`` 有，于是主键必然不同 ——
        实测一次击杀产出 2 条 CombatEvent（相隔 250 ms）。

        副键 ``(kind, victim_id)`` 只在**至少一方不知道 killer_id**时合并：
        血量/存活两个来源没有击杀者信息，它们补上的 0 是"缺失"而不是
        "另一个人"，理应被 Event topic 那条覆盖掉。反过来，两个来源都给出
        非 0 且**不同**的 killer_id 时一律放行 —— 那确实是两次不同的记录。

        副键同样受 2.0 s 去重窗口约束，所以跨窗口的新击杀不会被误吞。
        """
        now = time.perf_counter()
        self._seen = {
            key: entry for key, entry in self._seen.items() if now - entry[0] < 2.0
        }
        accepted: list[CombatEvent] = []
        for event in events:
            key = (event.kind, event.victim_id, event.killer_id)
            if key in self._seen:
                continue
            if event.victim_id:
                sibling = self._seen.get((event.kind, event.victim_id))
                if sibling is not None and (sibling[1] == 0 or event.killer_id == 0):
                    continue
            self._seen[key] = (now, event.killer_id)
            if event.victim_id:
                self._seen.setdefault((event.kind, event.victim_id), (now, event.killer_id))
            accepted.append(event)
        return accepted


@dataclass
class _VisionTrack:
    lit_at: float = 0.0
    extinguish_start: float = 0.0
    extinguish_frames: int = 0
    dim_start: float = 0.0
    dim_frames: int = 0
    miss_start: float = 0.0
    miss_frames: int = 0
    triggered: bool = False
    last_seen_at: float = 0.0
    last_frame_token: object | None = None
    last_color: str = ""
    last_light_strength: float = 0.0
    last_victim_id: int = 0
    target_identity: str = ""
    seen_this_frame: bool = False
    lit_strength_baseline: float = 0.0
    last_center: tuple[float, float] | None = None
    stable_frames: int = 0
    last_in_safe_area: bool = True


class VisionKillDetector:
    """敌方亮灯后的灭灯 / 过暗 / 短时丢检 → 视觉击杀确认。"""

    # 经典 extinguish 路径
    EXTINGUISH_MIN_FRAMES = 3
    EXTINGUISH_MIN_DURATION = 0.20
    # 灯条过暗辅助（颜色仍可能是 red/blue）
    DIM_STRENGTH = 0.22
    LIT_STRENGTH = 0.40
    DIM_BASELINE_RATIO = 0.45
    DIM_FLOOR = 0.08
    DIM_MIN_FRAMES = 3
    DIM_MIN_DURATION = 0.20
    # 亮灯后短时丢检（灯灭后 YOLO 常直接丢框）
    MISS_MIN_FRAMES = 4
    MISS_MIN_DURATION = 0.28
    LIT_MIN_HISTORY = 0.15
    # 亮灯过久再丢检就不做 miss-kill（设计稿里的"偏遮挡"取舍）。注意：这一条
    # 目前**只**由下面 LOSS_PROBE_DARK 路径的下界 LIT_MIN_HISTORY 起作用，
    # 常量本身在活代码里没有引用点（原本唯一的引用在已删除的裸丢检块里）。
    # 保留它是因为 设计稿_vs_rm_client_击杀特效实现对照.txt:117 把它列为可调
    # 参数；要真正启用需要在 LOSS_PROBE_DARK 确认前加 lit_age 上界，那是
    # 行为变更，不在本次范围内。
    MISS_MAX_AFTER_LIT = 2.5
    # 曝光：暂停累计，不清零
    EXPOSURE_DELTA = 0.28
    EXPOSURE_HOLD = 0.20
    TRACK_TTL = 8.0
    CONF_MIN = 0.70
    EDGE_MARGIN_RATIO = 0.10
    BALANCED_MIN_STABLE_FRAMES = 3

    def __init__(self) -> None:
        self._tracks: dict[str, _VisionTrack] = {}
        # 已确认战亡的目标身份。视觉结果仍会继续进入 detector，
        # 这样目标重新亮灯时可以解除屏蔽，但不会再次触发连杀。
        self._eliminated: set[str] = set()
        self._last_luminance: float | None = None
        self._last_scene_frame_token: object | None = None
        self._exposure_hold_until = 0.0
        self.last_status = "IDLE"

    def reset(self) -> None:
        self._tracks.clear()
        self._eliminated.clear()
        self._last_luminance = None
        self._last_scene_frame_token = None
        self._exposure_hold_until = 0.0
        self.last_status = "IDLE"

    @staticmethod
    def target_identity(armor: Any) -> str:
        """返回跨实体板、预测板和主瞄准板稳定的机器人身份。"""
        target_id = getattr(armor, "target_id", -1)
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            target_id = -1
        if target_id >= 0:
            return str(target_id)
        armor_id = getattr(armor, "armor_id", -1)
        try:
            armor_id = int(armor_id)
        except (TypeError, ValueError):
            armor_id = -1
        return str(armor_id)

    def _track_key(self, armor: Any) -> str:
        return f"{self.target_identity(armor)}:{getattr(armor, 'label', 'armor')}"

    @staticmethod
    def is_extinguished(armor: Any) -> bool:
        color = str(getattr(armor, "color", "unknown")).lower()
        light_state = str(getattr(armor, "light_state", "unknown")).lower()
        return color == "extinguish" or light_state in ("extinguish", "extinguished", "off", "dead")

    def is_suppressed(self, armor: Any) -> bool:
        """供渲染层查询：确认战亡后隐藏该机器人所有实体板和预测板。"""
        return self.target_identity(armor) in self._eliminated

    def _set_status(self, status: str) -> None:
        priority = {
            "NO_DETECTION": 0,
            "INVALID_LIGHT_STATE": 1,
            "OWN_TEAM_FILTERED": 1,
            "LOW_CONFIDENCE": 2,
            "LIT_TRACKED": 3,
            "NO_LIT_HISTORY": 3,
            "HYSTERESIS": 3,
            "EXPOSURE_HOLD": 4,
            "DIM_TRACKED": 5,
            # MISS_UNSTABLE 仍然是**活**的：它由下面 LOSS_* 主路径里的
            # Balanced 稳定性闸发出。死掉的只有 MISS_TRACKED / MISS_EDGE_GUARD。
            "MISS_UNSTABLE": 5,
            "LOSS_PROBE_DARK": 5,
            "LOSS_NEEDS_DIM_HISTORY": 5,
            "LOSS_UNCERTAIN": 5,
            "LOSS_EDGE_GUARD": 5,
            "EXITING_BRIGHT": 5,
            "ELIMINATED_SUPPRESSED": 5,
            "MODEL_CONFIRMED": 5,
            "WAITING_FRESH_FRAMES": 5,
            "REFLECTION_IGNORED": 6,
            "VISION_CONFIRMED": 6,
        }
        if priority.get(status, 0) >= priority.get(self.last_status, 0):
            self.last_status = status

    def _reset_extinguish_progress(self, track: _VisionTrack) -> None:
        track.extinguish_start = 0.0
        track.extinguish_frames = 0
        track.dim_start = 0.0
        track.dim_frames = 0
        track.miss_start = 0.0
        track.miss_frames = 0

    def _mark_lit(self, track: _VisionTrack, *, target_identity: str, now: float, frame_token: object | None, victim_id: int, color: str, strength: float) -> None:
        rearmed = track.triggered or target_identity in self._eliminated
        self._eliminated.discard(target_identity)
        track.lit_at = now
        track.triggered = False
        track.target_identity = target_identity
        track.last_victim_id = victim_id
        track.last_color = color
        track.last_light_strength = strength
        track.last_frame_token = frame_token
        if strength > 1e-6:
            if rearmed or track.lit_strength_baseline <= 0.0:
                track.lit_strength_baseline = strength
            else:
                # 基线快速抬升、极慢衰减，避免暗场把仍亮着的目标直接判为 dim。
                track.lit_strength_baseline = max(strength, track.lit_strength_baseline * 0.985)
        self._reset_extinguish_progress(track)
        self._set_status("LIT_TRACKED")

    def _update_geometry(self, track: _VisionTrack, armor: Any, frame_size: tuple[int, int] | None) -> None:
        points = tuple(getattr(armor, "points", ()) or ())
        if len(points) < 2:
            return
        try:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
        except (TypeError, ValueError, IndexError):
            return
        center = ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5)
        if track.last_center is None:
            track.stable_frames = 1
        else:
            motion = math.dist(center, track.last_center)
            if frame_size is not None:
                diagonal = math.hypot(float(frame_size[0]), float(frame_size[1]))
                stable_limit = max(12.0, diagonal * 0.035)
            else:
                stable_limit = 32.0
            track.stable_frames = track.stable_frames + 1 if motion <= stable_limit else 1
        track.last_center = center
        if frame_size is not None:
            width, height = frame_size
            margin_x = width * self.EDGE_MARGIN_RATIO
            margin_y = height * self.EDGE_MARGIN_RATIO
            track.last_in_safe_area = margin_x <= center[0] <= width - margin_x and margin_y <= center[1] <= height - margin_y

    def _count_progress(
        self,
        track: _VisionTrack,
        *,
        now: float,
        frame_token: object | None,
        exposure_hold: bool,
        kind: str,
    ) -> None:
        """kind: extinguish | dim。曝光 hold 时暂停累计，不清零。"""
        if exposure_hold:
            self._set_status("EXPOSURE_HOLD")
            return
        fresh_frame = frame_token is None or track.last_frame_token != frame_token
        if kind == "extinguish":
            if track.extinguish_start <= 0.0:
                track.extinguish_start = now
            if fresh_frame:
                track.last_frame_token = frame_token
                track.extinguish_frames += 1
            # 灭灯时清掉纯 dim 进度，避免双计；miss 在未见时另计
            track.dim_start = 0.0
            track.dim_frames = 0
            track.miss_start = 0.0
            track.miss_frames = 0
        else:
            if track.dim_start <= 0.0:
                track.dim_start = now
            if fresh_frame:
                track.last_frame_token = frame_token
                track.dim_frames += 1
            track.miss_start = 0.0
            track.miss_frames = 0

    def _emit(
        self,
        track: _VisionTrack,
        *,
        now: float,
        detail: str,
    ) -> CombatEvent:
        track.triggered = True
        identity = track.target_identity or "0"
        self._eliminated.add(identity)
        self._set_status("VISION_CONFIRMED")
        return CombatEvent(
            "elimination",
            "vision",
            True,
            now,
            victim_id=track.last_victim_id,
            detail=detail,
        )

    def _finalize_tracks(
        self,
        now: float,
        *,
        vision_frame_advanced: bool,
        exposure_hold: bool,
        sensitivity: str,
        loss_evidence: Any | None,
    ) -> list[CombatEvent]:
        events: list[CombatEvent] = []
        for track in self._tracks.values():
            if track.triggered or track.lit_at <= 0.0:
                continue
            identity = track.target_identity
            if identity and identity in self._eliminated:
                continue

            lit_age = now - track.lit_at
            if lit_age < self.LIT_MIN_HISTORY:
                continue
            if exposure_hold:
                # 自动曝光可能同时造成灭灯分类、过暗和丢检，所有路径统一暂停。
                self._set_status("EXPOSURE_HOLD")
                continue

            # 经典：连续 extinguish 观测
            if (
                track.extinguish_frames >= self.EXTINGUISH_MIN_FRAMES
                and track.extinguish_start > 0.0
                and now - track.extinguish_start >= self.EXTINGUISH_MIN_DURATION
            ):
                events.append(self._emit(track, now=now, detail="vision_confirmed_lamp_off"))
                continue

            # 辅助：颜色仍可能是红/蓝，但灯条持续过暗
            if (
                track.dim_frames >= self.DIM_MIN_FRAMES
                and track.dim_start > 0.0
                and now - track.dim_start >= self.DIM_MIN_DURATION
            ):
                events.append(self._emit(track, now=now, detail="vision_confirmed_dim_lamp"))
                continue

            # 主路径：曾亮灯后短时丢检。仅实验室/平衡模式启用，且必须是新视觉帧。
            if not track.seen_this_frame:
                evidence_identity = ""
                if loss_evidence is not None:
                    try:
                        evidence_identity = str(int(getattr(loss_evidence, "target_id", -1)))
                    except (TypeError, ValueError):
                        evidence_identity = ""
                if sensitivity == "Match Reliable":
                    track.miss_start = 0.0
                    track.miss_frames = 0
                    continue
                if not vision_frame_advanced:
                    self._set_status("WAITING_FRESH_FRAMES")
                    continue
                if evidence_identity != identity:
                    track.miss_start = 0.0
                    track.miss_frames = 0
                    self._set_status("LOSS_UNCERTAIN")
                    continue
                if bool(getattr(loss_evidence, "moving_fast", False)):
                    track.miss_start = 0.0
                    track.miss_frames = 0
                    self._set_status("EXITING_BRIGHT")
                    continue
                if bool(getattr(loss_evidence, "near_edge", False)) or not track.last_in_safe_area:
                    track.miss_start = 0.0
                    track.miss_frames = 0
                    self._set_status("LOSS_EDGE_GUARD")
                    continue
                if track.dim_frames < 2:
                    track.miss_start = 0.0
                    track.miss_frames = 0
                    self._set_status("LOSS_NEEDS_DIM_HISTORY")
                    continue
                if not bool(getattr(loss_evidence, "probe_dark", False)):
                    track.miss_start = 0.0
                    track.miss_frames = 0
                    self._set_status("EXITING_BRIGHT")
                    continue
                if sensitivity == "Balanced" and track.stable_frames < self.BALANCED_MIN_STABLE_FRAMES:
                    track.miss_start = 0.0
                    track.miss_frames = 0
                    self._set_status("MISS_UNSTABLE")
                    continue
                if not vision_frame_advanced:
                    self._set_status("WAITING_FRESH_FRAMES")
                    continue
                if track.miss_start <= 0.0:
                    track.miss_start = now
                track.miss_frames += 1
                self._set_status("LOSS_PROBE_DARK")
                if track.miss_frames >= self.MISS_MIN_FRAMES and now - track.miss_start >= self.MISS_MIN_DURATION:
                    events.append(self._emit(track, now=now, detail="vision_confirmed_dark_loss_probe"))
                continue

            if sensitivity == "Match Reliable":
                continue
            # 走到这里必然 track.seen_this_frame 为真：上面那个
            # `if not track.seen_this_frame:` 块内每条路径都以 continue 收尾。
            # 本帧见过则清空 miss（灭灯/dim 进度保留）。
            #
            # 这里原来还挂着一个同样以 `if not track.seen_this_frame:` 开头的
            # "裸丢检" 块（产出 MISS_TRACKED / vision_confirmed_miss_after_lit，
            # 以及只在死块里出现过的 MISS_EDGE_GUARD）。它是加
            # _LocalLossLightProbe 之前的旧一代实现，被上面那条带 probe_dark /
            # moving_fast / near_edge / stable_frames 四道闸的 LOSS_* 路径取代
            # 后忘记删除，从而**永远不可达**（sys.settrace 跨 960 组场景实测
            # 从未执行；tests/combat_events_test.py 用 23760 组场景的逐位摘要
            # 证明删除前后输出完全一致）。因此
            # README_战果判定与阵营.md:33 举例的 MISS_TRACKED 状态和
            # vision_confirmed_miss_after_lit 事件从来不可能出现。
            #
            # 这里选择**删除而不是改可达**：把旧块接回去等于让"目标只是走出
            # 画面 / 被遮挡 / 并没有变暗"重新被判成击杀确认，在一个专门防幻影
            # 击杀的系统里是安全倒退。现在生效的是 LOSS_PROBE_DARK ->
            # vision_confirmed_dark_loss_probe。文档示例待同步（见交付报告）。
            track.miss_start = 0.0
            track.miss_frames = 0
        return events

    def process(
        self,
        observations: Iterable[Any],
        *,
        alliance: str,
        frame_luminance: float | None = None,
        frame_token: object | None = None,
        frame_size: tuple[int, int] | None = None,
        sensitivity: str = "Lab Responsive",
        loss_evidence: Any | None = None,
        now: float | None = None,
    ) -> list[CombatEvent]:
        now = time.perf_counter() if now is None else now
        if sensitivity not in ("Lab Responsive", "Balanced", "Match Reliable"):
            sensitivity = "Lab Responsive"
        self.last_status = "NO_DETECTION"
        self._tracks = {
            key: track
            for key, track in self._tracks.items()
            if now - track.last_seen_at <= self.TRACK_TTL
        }
        for track in self._tracks.values():
            track.seen_this_frame = False

        vision_frame_advanced = frame_token is None or frame_token != self._last_scene_frame_token
        if frame_luminance is not None and vision_frame_advanced:
            if self._last_luminance is not None and abs(frame_luminance - self._last_luminance) > self.EXPOSURE_DELTA:
                self._exposure_hold_until = now + self.EXPOSURE_HOLD
            self._last_luminance = frame_luminance
            self._last_scene_frame_token = frame_token
        elif vision_frame_advanced:
            self._last_scene_frame_token = frame_token
        exposure_hold = now < self._exposure_hold_until

        alliance_l = alliance.lower()
        for armor in observations:
            if bool(getattr(armor, "predicted", False)):
                continue
            color = str(getattr(armor, "color", "unknown")).lower()
            light_strength = float(getattr(armor, "light_strength", 0.0))
            light_state = str(getattr(armor, "light_state", "unknown")).lower()
            extinguished = self.is_extinguished(armor)
            lit_color = color in ("red", "blue") and not extinguished

            if not lit_color and not extinguished:
                self._set_status("INVALID_LIGHT_STATE")
                continue
            if lit_color and color == alliance_l:
                # 友军：解屏蔽，不参与敌方击杀轨道
                self._eliminated.discard(self.target_identity(armor))
                self._set_status("OWN_TEAM_FILTERED")
                continue

            target_identity = self.target_identity(armor)
            track_id = self._track_key(armor)
            confidence = float(getattr(armor, "confidence", 0.0))
            if confidence < self.CONF_MIN:
                # 低置信度不应被当成“目标消失”，否则遮挡一帧会推进 miss-to-kill。
                track = self._tracks.get(track_id)
                if track is not None:
                    track.last_seen_at = now
                    track.seen_this_frame = True
                    self._update_geometry(track, armor, frame_size)
                self._set_status("LOW_CONFIDENCE")
                continue

            track = self._tracks.setdefault(track_id, _VisionTrack())
            victim_id = int(getattr(armor, "armor_id", 0) or 0)
            track.last_seen_at = now
            track.seen_this_frame = True
            track.target_identity = target_identity
            track.last_victim_id = victim_id
            track.last_color = color
            track.last_light_strength = light_strength
            self._update_geometry(track, armor, frame_size)

            # strength==0 常见于外部桥接未采样；此时红/蓝类仍可建立亮灯历史。
            strength_known = light_strength > 1e-6
            confirmed_lit = lit_color and (
                light_strength >= self.LIT_STRENGTH
                or (not strength_known and light_state not in ("extinguish", "extinguished", "off", "dead"))
            )

            if confirmed_lit:
                self._mark_lit(
                    track,
                    target_identity=target_identity,
                    now=now,
                    frame_token=frame_token,
                    victim_id=victim_id,
                    color=color,
                    strength=light_strength,
                )
                continue

            if target_identity in self._eliminated:
                self._set_status("ELIMINATED_SUPPRESSED")
                continue

            if track.lit_at <= 0.0:
                # 无亮灯历史：过暗/灭灯都不确认（防开局噪声）
                self._set_status("NO_LIT_HISTORY")
                self._reset_extinguish_progress(track)
                continue

            dim_threshold = max(self.DIM_FLOOR, track.lit_strength_baseline * self.DIM_BASELINE_RATIO)
            dim_by_strength = lit_color and strength_known and track.lit_strength_baseline > 0.0 and light_strength <= dim_threshold
            hysteresis = lit_color and not dim_by_strength and not confirmed_lit

            if hysteresis:
                # 中间亮度：保持 lit 历史，不重置灭灯进度，也不当确认亮灯
                self._set_status("HYSTERESIS")
                continue

            if extinguished:
                model_extinguish = color == "extinguish"
                reflection_ignored = model_extinguish and light_strength > 0.38
                self._count_progress(track, now=now, frame_token=frame_token, exposure_hold=exposure_hold, kind="extinguish")
                if exposure_hold:
                    pass
                elif reflection_ignored:
                    self._set_status("REFLECTION_IGNORED")
                elif model_extinguish:
                    self._set_status("MODEL_CONFIRMED")
                elif track.extinguish_frames < self.EXTINGUISH_MIN_FRAMES:
                    self._set_status("WAITING_FRESH_FRAMES")
                continue

            if dim_by_strength:
                self._count_progress(track, now=now, frame_token=frame_token, exposure_hold=exposure_hold, kind="dim")
                if not exposure_hold:
                    self._set_status("DIM_TRACKED")
                continue

            # 其它不可达分支保守忽略
            self._set_status("INVALID_LIGHT_STATE")

        return self._finalize_tracks(
            now,
            vision_frame_advanced=vision_frame_advanced,
            exposure_hold=exposure_hold,
            sensitivity=sensitivity,
            loss_evidence=loss_evidence,
        )
