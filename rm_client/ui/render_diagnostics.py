"""Typed, bounded diagnostics shared by renderer and presentation boundaries."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging


@dataclass(frozen=True)
class RenderFault:
    phase: str
    error_type: str
    message: str
    cause_type: str | None
    cause_message: str | None

    @property
    def summary(self) -> str:
        return f"{self.phase}: {self.error_type}: {self.message}"


@dataclass
class RenderFaultDiagnostics:
    faults: int = 0
    last_fault: RenderFault | None = None
    last_reported_at: dict[tuple[str, str], float] = field(default_factory=dict)

    def record(
        self, phase: str, exc: Exception, *, logger: logging.Logger, now: float,
    ) -> RenderFault:
        cause = exc.__cause__ or exc.__context__
        fault = RenderFault(
            phase, type(exc).__name__, str(exc),
            type(cause).__name__ if cause is not None else None,
            str(cause) if cause is not None else None,
        )
        self.faults += 1
        self.last_fault = fault
        key = (phase, fault.error_type)
        previous = self.last_reported_at.get(key)
        if previous is None or now - previous >= 1.0:
            self.last_reported_at[key] = now
            # Preserve the original exception and cause chain in the trace.
            # Repeated faults retain counters without flooding the log.
            logger.error(
                "Render stage %s failed: %s: %s", phase, fault.error_type, fault.message,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        return fault
