"""RM2026 decoded fields -> stable RobotState / frontend projections.

Only host effects (stage logging, video ownership, custom byte delivery) cross
this boundary. Socket lifecycle and Native decoder internals remain year-neutral.
"""
from __future__ import annotations
import time
from typing import Any
from ....model import RobotState
from .projection_policy import (
    ProjectileDamageBaseline, ProjectileDamageSample, assess_projectile_damage,
    select_present_fields, select_projected_health,
)

_RUNE_PHASE = {1: "INACTIVE", 2: "ACTIVATING", 3: "ACTIVE"}
# Robot IDs: red 1-9, blue 101-109 (same role + 100).
ROBOT_ID_NAMES = {1: "Hero", 2: "Engineer", 3: "Infantry", 4: "Infantry", 5: "Infantry",
                  6: "Aerial", 7: "Sentry", 8: "Dart", 9: "Radar"}
# Official section 2.2.14. Negative defense means vulnerability.
BUFF_TYPE_NAMES = {1: "Attack Boost", 2: "Defense Boost", 3: "Heat Cooling",
                   4: "Chassis Power", 5: "Health Regen", 6: "Exchange Ammo", 7: "Terrain"}
DEFAULT_MAX_HEALTH = 600
_STAGE_STR = {0: "NOT_STARTED", 1: "PREPARE", 2: "SELF_CHECK", 3: "COUNTDOWN", 4: "MATCH", 5: "SETTLEMENT"}
_HEALTH_ROLE_ORDER = (1, 2, 3, 4, 7)

class Rm2026StateAdapter:
    def __init__(self, host: Any) -> None:
        self.host = host

    @staticmethod
    def _sync_local_projectile_allowance(state: RobotState) -> None:
        """Map GlobalUnitStatus.robot_bullets onto this robot's HUD calibre.

        The referee array is own-team-only and ordered by roles 1/2/3/4/7,
        independent of alliance colour.  Hero consumes 42 mm; the remaining
        listed roles use the 17 mm HUD slot (Engineer normally reports zero).
        Unknown robot IDs or short/missing arrays remain fail-unknown instead
        of reusing a stale LabDebug value.
        """

        bullets = getattr(state, "official_robot_bullets", None) or []
        role_id = int(getattr(state, "robot_id", 0)) % 100
        try:
            index = _HEALTH_ROLE_ORDER.index(role_id)
        except ValueError:
            state.projectile_allowance_17mm = 0
            state.projectile_allowance_42mm = 0
            return
        if index >= len(bullets):
            state.projectile_allowance_17mm = 0
            state.projectile_allowance_42mm = 0
            return
        allowance = max(0, int(bullets[index]))
        if role_id == 1:
            state.projectile_allowance_17mm = 0
            state.projectile_allowance_42mm = allowance
        else:
            state.projectile_allowance_17mm = allowance
            state.projectile_allowance_42mm = 0

    def sync_frontend_state(self, state: RobotState) -> None:
        """Project authoritative rows, map positions and buffs onto the HUD."""
        has_real = (
            bool(state.official_robot_health)
            or bool(state.robot_positions)
            or bool(state.robot_statics)
            or bool(state.robot_dynamics)
            or bool(state.robot_modules)
        )
        if not has_real:
            return
        state.backend_frontend_synced = True

        health = [int(v) for v in state.official_robot_health[:10]]
        own_blue = int(state.robot_id) >= 100 or str(state.alliance_color).lower() == "blue"
        ally_ids = tuple(role + (100 if own_blue else 0) for role in _HEALTH_ROLE_ORDER)
        enemy_ids = tuple(role + (0 if own_blue else 100) for role in _HEALTH_ROLE_ORDER)
        health_by_id = {
            robot_id: health[index]
            for index, robot_id in enumerate(ally_ids + enemy_ids)
            if index < len(health)
        }
        observed_ids = {
            *(int(key) for key in state.robot_statics),
            *(int(key) for key in state.robot_dynamics),
            *(int(key) for key in state.robot_modules),
            *(int(item.get("robot_id", 0)) for item in state.robot_positions),
        }
        robot_ids = list(ally_ids + enemy_ids) if health else []
        robot_ids.extend(sorted(robot_id for robot_id in observed_ids if robot_id > 0 and robot_id not in robot_ids))
        rows: list[dict[str, Any]] = []
        for robot_id in robot_ids:
            base = robot_id if robot_id < 100 else robot_id - 100
            statics = state.robot_statics.get(robot_id, {})
            dynamics = state.robot_dynamics.get(robot_id, {})
            global_hp = max(0, int(health_by_id.get(robot_id, 0)))
            dynamic_hp = int(dynamics.get("current_health", 0))
            # Local 10 Hz telemetry wins over the older 1 Hz team snapshot,
            # including a valid zero at death (not a fallback to stale HP).
            hp = select_projected_health(
                local=robot_id == int(state.robot_id),
                global_hp=global_hp if robot_id in health_by_id else None,
                dynamic_hp=dynamic_hp if "current_health" in dynamics else None,
            )
            hp_max = int(statics.get("max_health", 0)) or DEFAULT_MAX_HEALTH
            alive_state = int(statics.get("alive_state", 1))
            if alive_state == 2 or hp <= 0:
                row_state = "DEAD"
            elif robot_id == int(state.robot_id):
                row_state = "LOCAL"
            else:
                row_state = "ALIVE"
            rows.append({
                "id": robot_id,
                "name": ROBOT_ID_NAMES.get(base, "Robot"),
                "hp": max(0, hp),
                "max": max(1, hp_max),
                "state": row_state,
                "x": None,
                "y": None,
                "modules": state.robot_modules.get(robot_id, {}),
            })
        positions = {int(p.get("robot_id", -1)): p for p in state.robot_positions}
        for row in rows:
            pos = positions.get(int(row["id"]))
            if pos:
                row["x"] = float(pos.get("x", 0.0))
                row["y"] = float(pos.get("y", 0.0))
                row["yaw"] = float(pos.get("yaw", 0.0))
        state.robot_rows = rows

        if positions:
            map_robots: list[dict[str, Any]] = []
            for robot_id, pos in positions.items():
                base = robot_id if robot_id < 100 else robot_id - 100
                global_hp = max(0, int(health_by_id.get(robot_id, 0)))
                dynamic_hp = int(state.robot_dynamics.get(robot_id, {}).get("current_health", 0))
                local_dynamics = state.robot_dynamics.get(robot_id, {})
                hp = select_projected_health(
                    local=robot_id == int(state.robot_id),
                    global_hp=global_hp if robot_id in health_by_id else None,
                    dynamic_hp=dynamic_hp if "current_health" in local_dynamics else None,
                )
                hp_max = int(state.robot_statics.get(robot_id, {}).get("max_health", 0)) or DEFAULT_MAX_HEALTH
                map_robots.append({
                    "id": robot_id,
                    "team": "ally" if robot_id in ally_ids else "enemy",
                    "name": ROBOT_ID_NAMES.get(base, "Robot"),
                    "x": float(pos.get("x", 0.0)),
                    "y": float(pos.get("y", 0.0)),
                    "hp": max(0, hp),
                    "max": max(1, hp_max),
                    "highlight": False,
                })
            state.map_robots = map_robots

        if state.buffs:
            buff_rows: list[dict[str, Any]] = []
            for buff in state.buffs:
                buff_type = int(buff.get("buff_type", 0))
                remaining = max(0, int(buff.get("buff_left_time", 0)))
                buff_rows.append({
                    "name": BUFF_TYPE_NAMES.get(buff_type, f"Buff {buff_type}"),
                    "active": remaining > 0,
                    "remaining": remaining,
                    "buff_type": buff_type,
                    "robot_id": int(buff.get("robot_id", 0)),
                })
            state.buff_rows = buff_rows

    def _apply_one(self, state: RobotState, name: str, data: dict[str, Any]) -> None:
        data, present = select_present_fields(name, data)
        economy_before = self._economy_signature(state)
        if name == "GameStatus":
            state.current_round = int(data.get("current_round", state.current_round))
            state.total_rounds = int(data.get("total_rounds", state.total_rounds))
            state.red_score = int(data.get("red_score", state.red_score))
            state.blue_score = int(data.get("blue_score", state.blue_score))
            stage = int(data["current_stage"]) if "current_stage" in data else None
            if stage is not None:
                state.current_stage = _STAGE_STR.get(stage, str(stage))
                self.host.safety.update_stage(stage)
            if stage is not None and state.current_stage != self.host._last_logged_stage:
                self.host._last_logged_stage = state.current_stage
                self.host.field_log.emit(
                    "match_stage",
                    raw_stage=stage,
                    stage=state.current_stage,
                    round=state.current_round,
                )
            state.stage_countdown_sec = max(0, int(data.get("stage_countdown_sec", state.stage_countdown_sec)))
            state.stage_elapsed_sec = max(0, int(data.get("stage_elapsed_sec", state.stage_elapsed_sec)))
        elif name == "GlobalUnitStatus":
            state.base_health = int(data.get("base_health", state.base_health))
            state.base_status = str(data.get("base_status", state.base_status))
            state.base_shield = int(data.get("base_shield", state.base_shield))
            state.outpost_health = int(data.get("outpost_health", state.outpost_health))
            state.outpost_status = str(data.get("outpost_status", state.outpost_status))
            state.enemy_base_health = int(data.get("enemy_base_health", state.enemy_base_health))
            state.enemy_base_status = str(data.get("enemy_base_status", state.enemy_base_status))
            state.enemy_base_shield = int(data.get("enemy_base_shield", state.enemy_base_shield))
            state.enemy_outpost_health = int(data.get("enemy_outpost_health", state.enemy_outpost_health))
            state.enemy_outpost_status = str(data.get("enemy_outpost_status", state.enemy_outpost_status))
            health = [int(v) for v in data.get("robot_health", ()) if isinstance(v, int)]
            if health:
                state.official_robot_health = health
            if 12 in present or "robot_bullets" in data:
                bullets = [int(v) for v in data.get("robot_bullets", ()) if isinstance(v, int)]
                state.official_robot_bullets = bullets
                state.backend_stats["robot_bullets"] = list(bullets)
                self._sync_local_projectile_allowance(state)
        elif name == "GlobalLogisticsStatus":
            state.remaining_economy = int(data.get("remaining_economy", state.remaining_economy))
            state.total_economy_obtained = int(data.get("total_economy_obtained", state.total_economy_obtained))
            state.tech_level = int(data.get("tech_level", state.tech_level))
            state.encryption_level = int(data.get("encryption_level", state.encryption_level))
        elif name == "RobotStaticStatus":
            if 4 in present and data["robot_id"]:
                state.robot_id = int(data["robot_id"])
                self._sync_local_projectile_allowance(state)
            state.robot_statics.setdefault(state.robot_id, {}).update(self._clean(data))
            if 8 in present and data["level"]:
                state.level = int(data["level"])
            if 9 in present and data["max_health"]:
                state.max_health = int(data["max_health"])
                state.current_health = min(state.current_health, state.max_health)
            if 10 in present and data["max_heat"]:
                state.max_heat = float(data["max_heat"])
            if 11 in present and data["heat_cooldown_rate"]:
                state.heat_cooldown_rate = float(data["heat_cooldown_rate"])
            if 12 in present and data["max_power"]:
                state.power_limit = int(data["max_power"])
            # 缓冲能量上限属于 RobotStaticStatus（协议字段 13），不在动态报文里。
            # 上一版把它误写进 RobotDynamicStatus 分支，那里根本没这个键，
            # data.get 永远回退到现值 —— 看着接上了，实际一行都不生效。
            # 不写它，effective_capacitor_percent 只能按默认上限 100 算比例。
            if 13 in present and data["max_buffer_energy"]:
                state.max_buffer_energy = int(data["max_buffer_energy"])
            if 6 in present:
                state.performance_sync["shooter"] = int(data["performance_system_shooter"])
            if 7 in present:
                state.performance_sync["chassis"] = int(data["performance_system_chassis"])
        elif name == "RobotDynamicStatus":
            state.robot_dynamics.setdefault(state.robot_id, {}).update(self._clean(data))
            state.current_health = int(data.get("current_health", state.current_health))
            state.current_heat = float(data.get("current_heat", state.current_heat))
            state.current_chassis_energy = int(data.get("current_chassis_energy", state.current_chassis_energy))
            state.current_buffer_energy = int(data.get("current_buffer_energy", state.current_buffer_energy))
            state.total_projectiles_fired = int(data.get("total_projectiles_fired", state.total_projectiles_fired))
            state.remaining_ammo = int(data.get("remaining_ammo", state.remaining_ammo))
            state.out_of_combat = bool(data.get("is_out_of_combat", state.out_of_combat))
            state.can_remote_heal = bool(data.get("can_remote_heal", state.can_remote_heal))
            state.can_remote_ammo = bool(data.get("can_remote_ammo", state.can_remote_ammo))
        elif name == "RobotModuleStatus":
            clean = self._clean(data)
            state.robot_module_status.update({k: int(v) for k, v in clean.items()})
            state.robot_modules.setdefault(state.robot_id, {}).update(clean)
        elif name == "RobotPosition":
            self._upsert_positions(state, data)
        elif name == "Buff":
            self._upsert_buff(state, data)
        elif name == "PenaltyInfo":
            self._upsert_penalty(state, data)
        elif name == "RobotPathPlanInfo":
            state.path_plan = self._clean(data)
        elif name == "MapClickInfo":
            clean = self._clean(data)
            marker = {
                "label": chr(int(clean.get("ascii", 0))) if 32 <= int(clean.get("ascii", 0)) <= 126 else "MARK",
                "x": float(clean.get("map_x", 0.0)),
                "y": float(clean.get("map_y", 0.0)),
                "kind": {1: "attack", 2: "defense", 3: "warning", 4: "custom"}.get(
                    int(clean.get("mode", 0)), "marker"),
                "pulse": True,
                **clean,
            }
            state.map_markers = [*state.map_markers[-15:], marker]
        elif name == "RadarInfoToClient":
            state.radar_detections = [dict(r) for r in data.get("robots", ())]
            state.radar_detections_at = time.perf_counter()
        elif name == "TechCoreMotionStateSync":
            state.tech_core.update(self._clean(data))
        elif name == "RobotPerformanceSelectionSync":
            clean = self._clean(data)
            state.performance_sync.update({k: int(v) for k, v in clean.items()})
        elif name == "DeployModeStatusSync":
            if "status" not in data:
                return
            next_status = 1 if int(data.get("status", 0)) else 0
            if next_status != int(state.deploy_mode):
                state.deploy_status_revision += 1
            state.deploy_mode = next_status
            state.deploy_status_at = time.perf_counter()
            if bool(next_status) != self.host._hero_native_override:
                self.host._hero_native_override = bool(next_status)
                if self.host._hero_native_override:
                    self.host.set_active_video_source("Native 0x0310", _hero_transition=True)
                else:
                    self.host.set_active_video_source(
                        self.host._normal_video_source or self.host.config.connection.video_source,
                        _hero_transition=True,
                    )
                self.host.field_log.emit(
                    "hero_video_source",
                    deployed=bool(next_status),
                    active_source=self.host._active_video_source,
                )
        elif name == "RuneStatusSync":
            state.rune_status.update(self._clean(data))
            if "rune_status" in data:
                state.energy_rune_phase = _RUNE_PHASE.get(int(data["rune_status"]), "INACTIVE")
            if "average_rings" in data:
                state.energy_rune_progress = int(float(data["average_rings"]) * 100)
        elif name == "SentryStatusSync":
            state.sentry_status.update(self._clean(data))
        elif name == "DartSelectTargetStatusSync":
            state.dart_status.update(self._clean(data))
        elif name == "SentryCtrlResult":
            state.sentry_ctrl_result = self._clean(data)
        elif name == "AirSupportStatusSync":
            if "airsupport_status" in data:
                state.air_support_active = int(data["airsupport_status"]) == 1
            state.air_support_free_time_sec = int(data.get("left_time", state.air_support_free_time_sec))
            if "left_time" in data:
                state.air_support_can_free = state.air_support_free_time_sec > 0
            state.air_support_cost_coins = int(data.get("cost_coins", state.air_support_cost_coins))
        elif name == "GlobalSpecialMechanism":
            ids = [int(v) for v in data.get("mechanism_id", ())]
            times = [int(v) for v in data.get("mechanism_time_sec", ())]
            state.special_mechanisms = [
                {"mechanism_id": i, "time_sec": t}
                for i, t in zip(ids, times)
            ]
        elif name == "Event":
            state.backend_stats["last_event"] = {
                "event_id": int(data.get("event_id", 0)),
                "param": data.get("param", ""),
            }
        elif name == "CustomByteBlock":
            self.host._apply_custom_byte_block(state, data)
        elif name == "RobotInjuryStat":
            clean = self._clean(data)
            killer_id = int(data.get("killer_id", 0))
            state.injury_stats.setdefault(killer_id, {}).update(clean)
            small_present = "small_projectile_damage" in data
            large_present = "large_projectile_damage" in data
            if not small_present and not large_present:
                return
            small_total = max(0, int(data.get("small_projectile_damage", state.projectile_damage_small_total)))
            large_total = max(0, int(data.get("large_projectile_damage", state.projectile_damage_large_total)))
            decision = assess_projectile_damage(
                ProjectileDamageBaseline(
                    state.injury_stats_initialized,
                    state.projectile_damage_small_initialized,
                    state.projectile_damage_large_initialized,
                    state.projectile_damage_small_total,
                    state.projectile_damage_large_total,
                ),
                ProjectileDamageSample(small_present, large_present, small_total, large_total),
            )
            damage_delta = decision.damage_delta
            if damage_delta > 0:
                state.projectile_hit_revision += 1
                state.last_projectile_hit_at = time.perf_counter()
                state.last_projectile_damage_delta = damage_delta
                state.last_projectile_kind = decision.projectile_kind
                state.last_projectile_killer_id = killer_id
            next_baseline = decision.next_baseline
            state.injury_stats_initialized = next_baseline.initialized
            state.projectile_damage_small_initialized = next_baseline.small_initialized
            state.projectile_damage_large_initialized = next_baseline.large_initialized
            state.projectile_damage_small_total = next_baseline.small_total
            state.projectile_damage_large_total = next_baseline.large_total
        elif name == "RobotRespawnStatus":
            state.respawn_pending = bool(data.get("is_pending_respawn", state.respawn_pending))
            snapshot = state.backend_stats.setdefault("respawn_status", {})
            snapshot.update(self._clean(data))
            if "total_respawn_progress" in snapshot and "current_respawn_progress" in snapshot:
                total = max(1, int(snapshot["total_respawn_progress"]))
                current = int(snapshot["current_respawn_progress"])
                state.respawn_progress = min(1.0, current / total)
            state.respawn_can_free = bool(data.get("can_free_respawn", state.respawn_can_free))
            state.respawn_gold_cost = int(data.get("gold_cost_for_respawn", state.respawn_gold_cost))
            state.respawn_can_pay = bool(data.get("can_pay_for_respawn", state.respawn_can_pay))

        # Economy confirmation is driven by an actual relevant field change,
        # not by the arrival count of unrelated team messages.  This prevents
        # a teammate's purchase from acknowledging our pending request.
        if self._economy_signature(state) != economy_before:
            state.economy_status_revision = int(state.economy_status_revision) + 1
            state.economy_command_status = ""

    @staticmethod
    def _economy_signature(state: RobotState) -> tuple[Any, ...]:
        return (
            int(state.remaining_economy),
            int(state.remaining_ammo),
            int(state.current_health),
            bool(state.respawn_pending),
            bool(state.air_support_active),
        )

    def _upsert_positions(self, state: RobotState, data: dict[str, Any]) -> None:
        robot_id = int(data.get("robot_id", 0)) or int(state.robot_id)
        positions = list(state.robot_positions)
        clean = self._clean(data)
        clean["robot_id"] = robot_id
        for index, item in enumerate(positions):
            if int(item.get("robot_id", -1)) == robot_id:
                positions[index] = {**item, **clean}
                break
        else:
            positions.append(clean)
        state.robot_positions = positions[-16:]

    def _upsert_buff(self, state: RobotState, data: dict[str, Any]) -> None:
        key = (int(data.get("robot_id", 0)), int(data.get("buff_type", 0)))
        buffs = list(state.buffs)
        clean = self._clean(data)
        for index, item in enumerate(buffs):
            if (int(item.get("robot_id", 0)), int(item.get("buff_type", 0))) == key:
                buffs[index] = clean
                break
        else:
            buffs.append(clean)
        state.buffs = buffs[-24:]

    def _upsert_penalty(self, state: RobotState, data: dict[str, Any]) -> None:
        penalty_type = int(data.get("penalty_type", 0))
        penalties = list(state.penalties)
        clean = self._clean(data)
        for index, item in enumerate(penalties):
            if int(item.get("penalty_type", -1)) == penalty_type:
                penalties[index] = clean
                break
        else:
            penalties.append(clean)
        state.penalties = penalties[-8:]
        state.active_penalty = clean

    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, Any]:
        """去掉协议层附加的 __present__ 元数据后再存入 state。"""
        return {k: v for k, v in data.items() if k != "__present__"}
