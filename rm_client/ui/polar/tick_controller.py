"""Clock policy and fault isolation for the UI's independent runtime ticks.

These components know neither Qt nor PolarRMWindow. The window supplies clock
snapshots and collaborators; domain work remains ordered by its runtime adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable, MutableMapping


@dataclass(frozen=True)
class FrameTiming:
    now: float
    elapsed: float
    ui_dt: float

    @classmethod
    def since(cls, now: float, previous: float) -> FrameTiming:
        elapsed = max(0.0, now - previous)
        # Presentation follows elapsed display time, including delayed uploads.
        # Simulation has a separate, conservative cap in RuntimeTickPlan.
        return cls(now, elapsed, max(0.001, min(1.0, elapsed)))

    @property
    def gap_ms(self) -> float:
        return self.elapsed * 1000.0


@dataclass(frozen=True)
class RuntimeTickPlan:
    control_due: bool
    logic_dt: float | None

    @classmethod
    def at(cls, now: float, last_control: float, last_logic: float) -> RuntimeTickPlan:
        control_due = now - last_control >= (1.0 / 75.0) * 0.92
        logic_elapsed = now - last_logic
        logic_dt = (
            max(0.001, min(0.05, logic_elapsed))
            if logic_elapsed >= (1.0 / 60.0) * 0.92 else None
        )
        # Never replay a backlog after suspend. One current sample per clock
        # is safer than a burst of stale control or simulated physics steps.
        return cls(control_due, logic_dt)


@dataclass(frozen=True)
class RenderCadence:
    target_hz: float
    timer_interval_ms: int

    @classmethod
    def resolve(
        cls, requested: object, cap: float,
        apply_preferences: Callable[[float, float], float],
    ) -> RenderCadence:
        try:
            requested_hz = float(requested)
        except (TypeError, ValueError):
            requested_hz = 144.0
        target = apply_preferences(max(120.0, min(cap, requested_hz)), cap)
        # A user paint cap never lowers the event/backend/KMC polling clock.
        interval = max(4, int(round(1000.0 / max(120.0, target))))
        return cls(target, interval)


@dataclass
class TickErrors:
    count: int = 0
    last_error: str = ""
    last_logged_at: MutableMapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class TickPerformanceReport:
    tick_ms: float
    tick_max_ms: float
    wakeup_gap_ms: float
    wakeup_gap_max_ms: float
    stages: tuple[tuple[str, tuple[tuple[str, float], ...]], ...]

    def metrics(self) -> dict[str, Any]:
        """Return a detached copy; callers can request independent sink payloads."""
        return {
            "tick_ms": self.tick_ms,
            "tick_max_ms": self.tick_max_ms,
            "wakeup_gap_ms": self.wakeup_gap_ms,
            "wakeup_gap_max_ms": self.wakeup_gap_max_ms,
            "stages": {name: dict(values) for name, values in self.stages},
        }


@dataclass
class TickDiagnostics(TickErrors):
    """Own per-window accumulation and lifetime fault/throttle history.

    Reporting consumes only the five-second timing window. Error counts and
    their per-stage log deadlines survive successful frames and sink failures.
    No window, backend or clock is retained by this owner.
    """
    last_ms: float = 0.0
    max_ms: float = 0.0
    gap_ms: float = 0.0
    gap_max_ms: float = 0.0
    stages: dict[str, dict[str, float]] = field(default_factory=dict)
    reported_at: float = 0.0

    def record_wakeup(self, gap_ms: float) -> None:
        self.gap_ms = gap_ms
        self.gap_max_ms = max(self.gap_max_ms, gap_ms)

    def run_stage(self, runner: TickStageRunner, name: str, callback: Callable[[], Any],
                  *, now: float | None = None) -> bool:
        return runner.run(name, callback, errors=self, timings=self.stages, now=now)

    def finish_tick(self, started_at: float, finished_at: float) -> TickPerformanceReport | None:
        self.last_ms = (finished_at - started_at) * 1000.0
        self.max_ms = max(self.max_ms, self.last_ms)
        if started_at - self.reported_at < 5.0:
            return None
        report = TickPerformanceReport(
            self.last_ms, self.max_ms, self.gap_ms, self.gap_max_ms,
            tuple((name, tuple(stats.items())) for name, stats in self.stages.items()),
        )
        self.reported_at = started_at
        self.stages.clear()
        self.max_ms = self.gap_max_ms = 0.0
        return report


class TickDiagnosticField:
    """Bounded compatibility fields; runtime adapters call the owner actions."""
    def __init__(self, field_name: str) -> None:
        if field_name not in TickDiagnostics.__dataclass_fields__:
            raise ValueError(f"Unknown tick diagnostic field: {field_name}")
        self.field_name = field_name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance._tick_diagnostics, self.field_name)

    def __set__(self, instance, value):
        setattr(instance._tick_diagnostics, self.field_name, value)


@dataclass(frozen=True)
class TickStageRunner:
    """Isolate one stage and its diagnostics from every later safety stage."""

    backend_stats: MutableMapping[str, Any]
    emit: Callable[..., Any]
    logger: logging.Logger
    clock: Callable[[], float] = time.perf_counter

    def run(
        self, name: str, callback: Callable[[], Any], *, errors: TickErrors,
        timings: MutableMapping[str, dict[str, float]] | None = None,
        now: float | None = None,
    ) -> bool:
        started = self.clock()
        try:
            callback()
            return True
        except Exception as exc:
            fault_at = self.clock() if now is None else float(now)
            self._record_error(name, exc, errors, fault_at)
            return False
        finally:
            if timings is not None:
                self._record_duration(timings, name, self.clock() - started)

    def _record_error(self, name: str, exc: Exception, errors: TickErrors, now: float) -> None:
        errors.count += 1
        errors.last_error = f"{name}: {type(exc).__name__}: {exc}"
        self.backend_stats["ui_tick"] = {
            "errors": errors.count,
            "last_error": errors.last_error,
        }
        if now - errors.last_logged_at.get(name, 0.0) < 1.0:
            return
        errors.last_logged_at[name] = now
        self.logger.exception("UI tick stage failed: %s", name)
        try:
            self.emit(
                "thread_exception", stage=name, error_type=type(exc).__name__,
                error=str(exc), count=errors.count,
            )
        except Exception:
            # Reporting a broken backend must not prevent polar overlay or the
            # next safety stage from running when its log sink is broken too.
            self.logger.warning("UI tick failure diagnostic could not be written", exc_info=True)

    @staticmethod
    def _record_duration(
        timings: MutableMapping[str, dict[str, float]], name: str, elapsed: float,
    ) -> None:
        elapsed_ms = elapsed * 1000.0
        stats = timings.setdefault(name, {"count": 0, "total_ms": 0.0, "max_ms": 0.0})
        stats["count"] += 1
        stats["total_ms"] += elapsed_ms
        stats["max_ms"] = max(stats["max_ms"], elapsed_ms)
