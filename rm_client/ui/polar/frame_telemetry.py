"""Completed-frame sampling without Qt, renderer, logger or window ownership."""
from __future__ import annotations

from dataclasses import dataclass, field
import math


@dataclass(frozen=True)
class FrameTimingReport:
    presented_at: float
    samples: int
    p95_ms: float
    max_ms: float
    ema_ms: float
    fps: float

    def metrics(self) -> dict[str, float | int]:
        return {
            "samples": self.samples,
            "frame_ms_p95": self.p95_ms,
            "frame_ms_max": self.max_ms,
            "frame_ms_ema": self.ema_ms,
            "fps": self.fps,
        }


@dataclass(frozen=True)
class CompletedFrame:
    fps: float
    report: FrameTimingReport | None


@dataclass
class FrameTelemetry:
    """Keep bounded samples and acknowledge only successfully published windows.

    The owner-thread adapter records a completed frame, publishes its optional
    report synchronously, then acknowledges it. A failed sink leaves the timing
    window available for the next frame, while FPS and EMA remain continuous.
    Clock values and render-context metrics are supplied by the adapter.
    """
    last_presented_at: float = 0.0
    fps_samples: list[float] = field(default_factory=list)
    ema_ms: float = 0.0
    frame_times_ms: list[float] = field(default_factory=list)
    last_reported_at: float = 0.0

    def record_frame(self, started_at: float, presented_at: float) -> CompletedFrame:
        present_dt = max(0.001, min(0.25, presented_at - self.last_presented_at))
        self.last_presented_at = presented_at
        self.fps_samples.append(1.0 / present_dt)
        if len(self.fps_samples) > 90:
            self.fps_samples.pop(0)
        fps = sum(self.fps_samples) / len(self.fps_samples)

        elapsed_ms = (presented_at - started_at) * 1000.0
        self.ema_ms = elapsed_ms if self.ema_ms <= 0.0 else self.ema_ms * 0.90 + elapsed_ms * 0.10
        self.frame_times_ms.append(elapsed_ms)
        if len(self.frame_times_ms) > 720:
            del self.frame_times_ms[:-720]
        return CompletedFrame(fps, self._report_if_due(presented_at, fps))

    def _report_if_due(self, presented_at: float, fps: float) -> FrameTimingReport | None:
        if presented_at - self.last_reported_at < 5.0 or not self.frame_times_ms:
            return None
        ordered = sorted(self.frame_times_ms)
        p95_index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
        return FrameTimingReport(
            presented_at, len(ordered), ordered[p95_index], ordered[-1], self.ema_ms, fps,
        )

    def acknowledge_report(self, report: FrameTimingReport) -> None:
        """Consume the synchronous report after its sink accepted it."""
        self.frame_times_ms.clear()
        self.last_reported_at = report.presented_at


class FrameTelemetryField:
    """Explicit compatibility aliases; production frames call the owner actions."""
    def __init__(self, field_name: str) -> None:
        if field_name not in FrameTelemetry.__dataclass_fields__:
            raise ValueError(f"Unknown frame telemetry field: {field_name}")
        self.field_name = field_name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance._frame_telemetry, self.field_name)

    def __set__(self, instance, value):
        setattr(instance._frame_telemetry, self.field_name, value)
