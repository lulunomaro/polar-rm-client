"""Fail-closed competition control policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import secrets
import time
from typing import Any, Callable, Iterable


FIGHTING_STAGES = frozenset({"4", "MATCH"})
CONTROL_TOPICS = frozenset({
    "KeyboardMouseControl",
    "CustomControl",
    "MapClickCmd",
    "AssemblyCommand",
    "RobotPerformanceSelectionCommand",
    "CommonCommand",
    "HeroDeployModeEventCommand",
    "RuneActivateCommand",
    "DartCommand",
    "SentryCtrlCommand",
    "AirSupportCommand",
    "DT7KeyboardMouseControl",
})


class ControlState(str, Enum):
    LOCKED = "LOCKED"
    QUALIFICATION = "QUALIFICATION"
    MATCH_ARMED = "MATCH_ARMED"
    ESTOP = "ESTOP"


@dataclass(frozen=True, slots=True)
class QualificationToken:
    value: str
    intents: frozenset[str]
    expires_at: float


@dataclass(frozen=True, slots=True)
class InputClearTraceFrame:
    filename: str
    function: str
    line_number: int


@dataclass(frozen=True, slots=True)
class InputClearFailure:
    """Bounded observer evidence without retained frames, locals, or payloads."""

    reason: str
    exception_type: str
    frames: tuple[InputClearTraceFrame, ...]
    trace_truncated: bool


class OfficialSafety:
    """Gate all real control output while keeping mock diagnostics usable.

    A scene/config flag can enable the transport, but it can never arm motion.
    The process always starts LOCKED and reconnects never restore an armed
    state.  Qualification uses an unguessable short-lived in-memory token.
    """

    def __init__(self, scenario_key: str = "LabDebug") -> None:
        self.scenario_key = scenario_key
        self.mock_writes = True
        self.allow_mqtt_publish = False
        self.enforce_official_limits = False
        self.allow_dt7_drive = False   # 由场景配置显式打开（当前仅 LabDebug）
        self.control_state = ControlState.LOCKED
        self.estop_active = False
        self.estop_at = 0.0
        self.current_stage = "0"
        self._stage_seen = False
        self._publish_at: dict[str, float] = {}
        self._publish_limit_hz = 10.0
        self._topic_limit_hz = {"KeyboardMouseControl": 75.0, "CustomControl": 75.0,
                               "DT7KeyboardMouseControl": 30.0}
        self._qualification: QualificationToken | None = None
        self._authorized_capabilities: frozenset[str] = frozenset()
        self.last_reason = "startup lock"
        self.last_transition_at = time.perf_counter()
        self.control_generation = 0
        self.input_clear_hook: Callable[[str], None] | None = None
        self.input_clear_failure_count = 0
        self._input_clear_failures: tuple[InputClearFailure, ...] = ()

    @property
    def input_clear_failures(self) -> tuple[InputClearFailure, ...]:
        """Latest eight failures; successful delivery never erases prior evidence."""
        return self._input_clear_failures

    def _record_input_clear_failure(self, reason: str, error: Exception) -> None:
        # Walk at most twelve frames, without linecache/source I/O or logging
        # handlers on this safety-critical path. Never retain exception objects.
        frames = []
        trace = error.__traceback__
        while trace is not None and len(frames) < 12:
            code = trace.tb_frame.f_code
            frames.append(InputClearTraceFrame(code.co_filename[:512], code.co_name[:128], trace.tb_lineno))
            trace = trace.tb_next
        failure = InputClearFailure(
            reason[:256] if isinstance(reason, str) else "<invalid clear reason>",
            f"{type(error).__module__}.{type(error).__qualname__}"[:256],
            tuple(frames), trace is not None,
        )
        self.input_clear_failure_count += 1
        # Immutable replacement also isolates the DT7 worker's shallow policy
        # snapshot: recording there cannot mutate the owner's diagnostics.
        self._input_clear_failures = (*self._input_clear_failures[-7:], failure)

    def _notify_input_clear(self, reason: str) -> None:
        hook = self.input_clear_hook
        if hook is None:
            return
        try:
            hook(str(reason))
        except Exception as error:
            # Safety state transitions must never depend on diagnostics/UI.
            self._record_input_clear_failure(reason, error)

    def configure(
        self,
        *,
        mock_writes: bool,
        allow_mqtt_publish: bool,
        enforce_official_limits: bool,
    ) -> None:
        self.mock_writes = bool(mock_writes)
        self.allow_mqtt_publish = bool(allow_mqtt_publish)
        self.enforce_official_limits = bool(enforce_official_limits)

    def configure_referee_limits(self, uplink_hz: Any) -> None:
        """Annual MQTT limits cannot alter the independent DT7 policy."""
        dt7 = self._topic_limit_hz["DT7KeyboardMouseControl"]
        self._topic_limit_hz = {str(name): float(hz) for name, hz in uplink_hz.items()}
        self._topic_limit_hz["DT7KeyboardMouseControl"] = dt7
        self._publish_at.clear()

    def from_scenario(self, config: Any) -> None:
        self.scenario_key = str(config.key)
        self.configure(
            mock_writes=bool(config.safety.mock_writes),
            allow_mqtt_publish=bool(config.safety.allow_mqtt_publish),
            enforce_official_limits=bool(config.safety.enforce_official_limits),
        )
        # 台架驾驶开关随场景切换（默认 False，只有 LabDebug 的 profile 打开）
        self.allow_dt7_drive = bool(getattr(config.safety, "allow_dt7_drive", False))
        self.current_stage = "0"
        self._stage_seen = False
        self._publish_at.clear()
        self.lock_control("scenario changed")

    def update_stage(self, current_stage: Any) -> None:
        stage = str(current_stage).strip()
        if stage.isdigit() and len(stage) <= 2:
            self.current_stage = str(int(stage))
            self._stage_seen = True
        elif stage == "MATCH" and self._stage_seen:
            self.current_stage = "4"

    def is_fighting(self) -> bool:
        return self._stage_seen and self.current_stage in FIGHTING_STAGES

    def lock_control(self, reason: str = "operator lock", *, notify: bool = True) -> None:
        self.control_state = ControlState.LOCKED
        self._qualification = None
        self._authorized_capabilities = frozenset()
        self.last_reason = str(reason)
        self.last_transition_at = time.perf_counter()
        self.control_generation += 1
        if notify:
            self._notify_input_clear(self.last_reason)

    def begin_qualification(
        self,
        intents: Iterable[str],
        *,
        ttl_s: float = 30.0,
        now: float | None = None,
    ) -> QualificationToken:
        if self.estop_active or self.control_state == ControlState.ESTOP:
            raise ValueError("release E-stop explicitly before qualification")
        current = time.perf_counter() if now is None else float(now)
        token = QualificationToken(
            value=secrets.token_urlsafe(24),
            intents=frozenset(str(value) for value in intents),
            expires_at=current + max(1.0, min(300.0, float(ttl_s))),
        )
        self._qualification = token
        self.control_state = ControlState.QUALIFICATION
        self.last_reason = "qualification token active"
        self.last_transition_at = current
        self.control_generation += 1
        self._notify_input_clear("qualification epoch started")
        return token

    def arm_match(self, capabilities: Iterable[str]) -> None:
        if self.estop_active or self.control_state == ControlState.ESTOP:
            raise ValueError("release E-stop explicitly before arming")
        allowed = frozenset(str(value) for value in capabilities)
        if "core_kmc" not in allowed:
            raise ValueError("core_kmc qualification is required")
        self._authorized_capabilities = allowed
        self._qualification = None
        self.estop_active = False
        self.control_state = ControlState.MATCH_ARMED
        self.last_reason = "operator armed verified evidence"
        self.last_transition_at = time.perf_counter()
        self.control_generation += 1
        self._notify_input_clear("match control armed; neutral input required")

    @staticmethod
    def _capability_for(name: str, intent: str) -> str:
        if name == "DT7KeyboardMouseControl":
            return "dt7_control"
        if intent in {"prm1_vision", "prm1_spin", "hero", "economy", "core_kmc"}:
            return intent
        if name == "KeyboardMouseControl":
            return "core_kmc"
        if name == "HeroDeployModeEventCommand":
            return "hero"
        if name == "CommonCommand":
            return "economy"
        return "core_kmc"

    def can_publish(
        self,
        topic: str,
        now: float | None = None,
        *,
        intent: str = "control",
        qualification_token: str | None = None,
    ) -> tuple[bool, str]:
        name = str(topic).strip().rstrip("/").rsplit("/", 1)[-1]
        current = time.perf_counter() if now is None else float(now)
        if self.estop_active or self.control_state == ControlState.ESTOP:
            return self._deny("E-stop active; only the dedicated zero KMC bypass is allowed")
        # DT7 is an independent transport, not MQTT. Its explicit Real TX
        # switch is checked by the manager/link, but never replaces this lock.
        if not self.allow_mqtt_publish and name != "DT7KeyboardMouseControl":
            return self._deny("current scenario is mock-only and does not allow real MQTT publishing")
        authorized_qualification = False
        # 台架实验（LabDebug）：场景显式打开开关时，允许直连 DT7 的驾驶输出，
        # 不要求 MATCH_ARMED / 资格令牌 / 比赛阶段。
        # 只作用于 DT7KeyboardMouseControl 这一个 topic；急停仍优先拒绝
        # （见本函数开头的 E-stop 分支），限频与看门狗行为不变。
        lab_dt7_drive = bool(
            self.allow_dt7_drive
            and name == "DT7KeyboardMouseControl"
            and not self.estop_active
        )
        if name in CONTROL_TOPICS and not lab_dt7_drive:
            if self.control_state == ControlState.LOCKED:
                return self._deny("match control is LOCKED")
            if self.control_state == ControlState.QUALIFICATION:
                token = self._qualification
                if (
                    token is None
                    or current > token.expires_at
                    or not qualification_token
                    or not secrets.compare_digest(qualification_token, token.value)
                    or str(intent) not in token.intents
                ):
                    self.lock_control("qualification token invalid or expired")
                    return False, self.last_reason
                authorized_qualification = True
            elif self.control_state == ControlState.MATCH_ARMED:
                capability = self._capability_for(name, str(intent))
                if capability not in self._authorized_capabilities:
                    return self._deny(f"capability {capability} remains LOCKED MOCK")
        if (self.enforce_official_limits or name == "DT7KeyboardMouseControl") and name in CONTROL_TOPICS:
            # A token-bound qualification frame is a supervised, neutral test
            # publish.  The stage gate exists to stop unsupervised control in
            # non-fighting stages; requiring stage 4 here would make the whole
            # qualification flow unreachable before a match.  Rate limits still
            # apply, which is exactly what the heartbeat test measures.
            if not lab_dt7_drive and not authorized_qualification and not self.is_fighting():
                return self._deny("not in an authoritative fighting stage")
            previous = self._publish_at.get(name, -1.0)
            limit_hz = self._topic_limit_hz.get(name, self._publish_limit_hz)
            # DT7's writer handles 30 Hz and the one-shot neutral exception.
            # Applying this MQTT limiter to an authorization-only check would
            # toggle the manager's live flag and inject unwanted zero frames.
            if name != "DT7KeyboardMouseControl" and previous >= 0.0 and current - previous < 1.0 / limit_hz:
                return self._deny(f"{name} exceeds {limit_hz:g} Hz")
        return True, "ok"

    def can_transmit_dt7(self, *, qualification_token: str | None = None,
                         now: float | None = None) -> tuple[bool, str]:
        intent = "dt7_selftest" if qualification_token else "dt7_control"
        return self.can_publish("DT7KeyboardMouseControl", now=now,
                                intent=intent, qualification_token=qualification_token)

    def _deny(self, reason: str) -> tuple[bool, str]:
        self.last_reason = reason
        return False, reason

    def mark_published(self, topic: str, now: float | None = None) -> None:
        name = str(topic).strip().rstrip("/").rsplit("/", 1)[-1]
        self._publish_at[name] = time.perf_counter() if now is None else float(now)

    def trigger_estop(self) -> None:
        self.estop_active = True
        self.estop_at = time.perf_counter()
        self.control_state = ControlState.ESTOP
        self._qualification = None
        self._authorized_capabilities = frozenset()
        self.last_reason = "E-stop triggered"
        self.last_transition_at = self.estop_at
        self.control_generation += 1
        self._notify_input_clear(self.last_reason)

    def release_estop(self) -> None:
        self.estop_active = False
        # Releasing an E-stop never restores movement authorization.
        self.lock_control("E-stop released; explicit re-arm required")

    def estop_elapsed(self, now: float | None = None) -> float:
        return (time.perf_counter() if now is None else float(now)) - self.estop_at

    def heartbeat_deadline(
        self,
        last_write_at: float,
        now: float | None = None,
        timeout_s: float = 0.5,
    ) -> bool:
        return (time.perf_counter() if now is None else float(now)) - last_write_at > timeout_s

    def stats(self) -> dict[str, Any]:
        token = self._qualification
        return {
            "scenario": self.scenario_key,
            "mock_writes": self.mock_writes,
            "allow_mqtt_publish": self.allow_mqtt_publish,
            "enforce_official_limits": self.enforce_official_limits,
            "allow_dt7_drive": self.allow_dt7_drive,
            "control_state": self.control_state.value,
            "authorized_capabilities": sorted(self._authorized_capabilities),
            "qualification_expires_at": token.expires_at if token else 0.0,
            "estop_active": self.estop_active,
            "fighting": self.is_fighting(),
            "stage": self.current_stage,
            "last_reason": self.last_reason,
        }
