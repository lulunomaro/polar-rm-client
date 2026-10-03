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
        self.control_state = ControlState.LOCKED
        self.estop_active = False
        self.estop_at = 0.0
        self.current_stage = "0"
        self._stage_seen = False
        self._publish_at: dict[str, float] = {}
        self._publish_limit_hz = 10.0
        self._topic_limit_hz = {"KeyboardMouseControl": 75.0, "CustomControl": 75.0}
        self._qualification: QualificationToken | None = None
        self._authorized_capabilities: frozenset[str] = frozenset()
        self.last_reason = "startup lock"
        self.last_transition_at = time.perf_counter()
        self.input_clear_hook: Callable[[str], None] | None = None
        # 第 53 轮新增：那个 hook 是唯一能松开卡住按键的机制，它抛异常时
        # **不能只 `pass`** —— 见 `_notify_input_clear` 的注释。
        #
        # ⚠ 这里**只记账，不改变控制流**：状态照落（那是安全方向），
        # 但失败要**看得见**，否则现场只看到一个安静的 ESTOP。
        self.input_clear_failures = 0
        self.last_input_clear_error: str | None = None

    def _notify_input_clear(self, reason: str) -> None:
        hook = self.input_clear_hook
        if hook is None:
            return
        try:
            hook(str(reason))
        except Exception as exc:  # noqa: BLE001
            # Safety state transitions must never depend on diagnostics/UI.
            #
            # 第 53 轮：这里**原来真的是光秃秃的 `pass`**。
            # 那个 hook 是**唯一**能在 LOCKED / ESTOP 时松开卡住的按键的机制
            # （下游 `input_dispatch_mixin._clear_control_inputs_for_lock`
            # 的文档写着：DT7 与官方客户端**没有**陈旧帧超时，
            # 所以松不开就等于**那个键一直被按住**）。
            #
            # 而状态是在**通知之前**就写进去的（见 `lock_control` /
            # `arm_match` / `trigger_estop`）—— 于是 hook 一抛，
            # 状态机报着 ESTOP，而车还按着最后一个键。
            #
            # **但"通知失败"不等于"可以不看"**：状态必须照落（那是安全方向），
            # 所以**不改变控制流**；这里只把失败**记成看得见的**，
            # 让现场能看出"这次没能松键"，而不是只看到一个安静的 ESTOP。
            # ⚠ **绝不动 `last_reason`** —— 它记的是「这次状态转移成了什么」，
            # 是现场看的第一句话。既有套件
            # `manager_safety_branch_audit_test` 里那条
            # 「清零钩子抛异常：`last_reason` 照样写下来了」**就是在钉这一条**：
            # 第 53 轮第一版把失败原因写进了 `last_reason`，结果把「为什么变成 ESTOP」
            # 顶掉了 —— **那条测试当场把它抓住**。
            #
            # 所以失败**另开**两个字段记，不挤占既有诊断。
            self.input_clear_failures += 1
            self.last_input_clear_error = f"{type(exc).__name__}: {reason}"

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

    def from_scenario(self, config: Any) -> None:
        self.scenario_key = str(config.key)
        self.configure(
            mock_writes=bool(config.safety.mock_writes),
            allow_mqtt_publish=bool(config.safety.allow_mqtt_publish),
            enforce_official_limits=bool(config.safety.enforce_official_limits),
        )
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

    def lock_control(self, reason: str = "operator lock") -> None:
        self.control_state = ControlState.LOCKED
        self._qualification = None
        self._authorized_capabilities = frozenset()
        self.last_reason = str(reason)
        self.last_transition_at = time.perf_counter()
        self._notify_input_clear(self.last_reason)

    def begin_qualification(
        self,
        intents: Iterable[str],
        *,
        ttl_s: float = 30.0,
        now: float | None = None,
    ) -> QualificationToken:
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
        self._notify_input_clear("qualification epoch started")
        return token

    def arm_match(self, capabilities: Iterable[str]) -> None:
        allowed = frozenset(str(value) for value in capabilities)
        if "core_kmc" not in allowed:
            raise ValueError("core_kmc qualification is required")
        self._authorized_capabilities = allowed
        self._qualification = None
        self.estop_active = False
        self.control_state = ControlState.MATCH_ARMED
        self.last_reason = "operator armed verified evidence"
        self.last_transition_at = time.perf_counter()
        self._notify_input_clear("match control armed; neutral input required")

    @staticmethod
    def _capability_for(name: str, intent: str) -> str:
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
        if not self.allow_mqtt_publish:
            return self._deny("current scenario is mock-only and does not allow real MQTT publishing")
        authorized_qualification = False
        if name in CONTROL_TOPICS:
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
        if self.enforce_official_limits and name in CONTROL_TOPICS:
            # A token-bound qualification frame is a supervised, neutral test
            # publish.  The stage gate exists to stop unsupervised control in
            # non-fighting stages; requiring stage 4 here would make the whole
            # qualification flow unreachable before a match.  Rate limits still
            # apply, which is exactly what the heartbeat test measures.
            if not authorized_qualification and not self.is_fighting():
                return self._deny("not in an authoritative fighting stage")
            previous = self._publish_at.get(name, -1.0)
            limit_hz = self._topic_limit_hz.get(name, self._publish_limit_hz)
            if previous >= 0.0 and current - previous < 1.0 / limit_hz:
                return self._deny(f"{name} exceeds {limit_hz:g} Hz")
        return True, "ok"

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
            "control_state": self.control_state.value,
            "authorized_capabilities": sorted(self._authorized_capabilities),
            "qualification_expires_at": token.expires_at if token else 0.0,
            "estop_active": self.estop_active,
            "fighting": self.is_fighting(),
            "stage": self.current_stage,
            "last_reason": self.last_reason,
        }
