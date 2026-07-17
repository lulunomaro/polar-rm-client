from __future__ import annotations

from dataclasses import dataclass
import json
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


def _parse_event_param(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"detail": text}
    if not isinstance(value, dict):
        return {"detail": text}
    return value


class OfficialMatchFeed:
    """将官方 MQTT topic 归一化为状态更新和去重后的战斗事件。"""

    ENEMY_ORDER = (1, 2, 3, 4, 7)

    def __init__(self) -> None:
        self.last_message_at = 0.0
        self.last_official_event_at = 0.0
        self._robot_health: list[int] = []
        self._seen: dict[tuple[str, int, int], float] = {}

    def is_fresh(self, timeout_s: float = 2.5) -> bool:
        return self.last_message_at > 0.0 and time.perf_counter() - self.last_message_at <= timeout_s

    def consume(self, topic: str, payload: bytes, *, alliance: str, local_robot_id: int) -> tuple[list[CombatEvent], dict[str, Any]]:
        now = time.perf_counter()
        self.last_message_at = now
        name = _topic_name(topic)
        fields = decode_proto_fields(payload)
        updates: dict[str, Any] = {"official_feed_at": now}
        events: list[CombatEvent] = []

        if name == "GameStatus":
            updates.update({
                "current_round": _field_int(fields, 1, 1),
                "total_rounds": _field_int(fields, 2, 1),
                "red_score": _field_int(fields, 3),
                "blue_score": _field_int(fields, 4),
                "current_stage": str(_field_int(fields, 5)),
                "stage_countdown_sec": _field_int(fields, 6),
                "stage_elapsed_sec": _field_int(fields, 7),
            })
        elif name == "GlobalUnitStatus":
            health_values = fields.get(11) or fields.get(6) or []
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
            killer_id = _field_int(fields, 10)
            server_kill_damage = _field_int(fields, 9)
            if server_kill_damage > 0:
                events.append(CombatEvent("death", "referee", True, now, killer_id=killer_id, detail="server_kill"))
        elif name == "RobotStaticStatus":
            robot_id = _field_int(fields, 4)
            alive_state = _field_int(fields, 3)
            if alive_state == 2:
                kind = "death" if robot_id == local_robot_id else "elimination"
                events.append(CombatEvent(kind, "referee", True, now, victim_id=robot_id, detail="alive_state"))
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
        now = time.perf_counter()
        self._seen = {key: timestamp for key, timestamp in self._seen.items() if now - timestamp < 2.0}
        accepted: list[CombatEvent] = []
        for event in events:
            key = (event.kind, event.victim_id, event.killer_id)
            if key in self._seen:
                continue
            self._seen[key] = now
            accepted.append(event)
        return accepted


@dataclass
class _VisionTrack:
    lit_at: float = 0.0
    extinguish_start: float = 0.0
    extinguish_frames: int = 0
    triggered: bool = False


class VisionKillDetector:
    """在没有官方链路时，以同一敌方装甲的亮灯到灭灯转变判定击杀。"""

    def __init__(self) -> None:
        self._tracks: dict[str, _VisionTrack] = {}
        self._last_luminance: float | None = None
        self._exposure_hold_until = 0.0

    def reset(self) -> None:
        self._tracks.clear()
        self._last_luminance = None
        self._exposure_hold_until = 0.0

    def process(self, observations: Iterable[Any], *, alliance: str, frame_luminance: float | None = None, now: float | None = None) -> list[CombatEvent]:
        now = time.perf_counter() if now is None else now
        if frame_luminance is not None:
            if self._last_luminance is not None and abs(frame_luminance - self._last_luminance) > 0.20:
                self._exposure_hold_until = now + 0.40
            self._last_luminance = frame_luminance
        events: list[CombatEvent] = []
        for armor in observations:
            if bool(getattr(armor, "predicted", False)):
                continue
            color = str(getattr(armor, "color", "unknown")).lower()
            if color not in ("red", "blue", "extinguish"):
                continue
            if color in ("red", "blue") and color == alliance.lower():
                continue
            confidence = float(getattr(armor, "confidence", 0.0))
            if confidence < 0.70:
                continue
            track_id = f"{getattr(armor, 'armor_id', -1)}:{getattr(armor, 'label', 'armor')}"
            track = self._tracks.setdefault(track_id, _VisionTrack())
            if color in ("red", "blue"):
                track.lit_at = now
                track.extinguish_start = 0.0
                track.extinguish_frames = 0
                track.triggered = False
                continue
            light_strength = float(getattr(armor, "light_strength", 0.0))
            if track.lit_at <= 0.0 or now - track.lit_at > 2.0 or now < self._exposure_hold_until or light_strength > 0.38:
                track.extinguish_start = 0.0
                track.extinguish_frames = 0
                continue
            if track.extinguish_start <= 0.0:
                track.extinguish_start = now
            track.extinguish_frames += 1
            if not track.triggered and track.extinguish_frames >= 5 and now - track.extinguish_start >= 0.45:
                track.triggered = True
                events.append(CombatEvent(
                    "elimination", "vision", True, now,
                    victim_id=int(getattr(armor, "armor_id", 0)),
                    detail="vision_confirmed_lamp_off",
                ))
        return events
