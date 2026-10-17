"""One-turn GPU preparation queue with explicit context/upload/error ports.

This owner knows neither Qt widgets nor renderers or backend logging. The UI
adapter admits visible/idle/context-valid turns. Each admitted turn executes at
most one already-queued job, keeps its soft budget honest and records actual
elapsed work. No timer, thread or upload starts during construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator, Protocol

WarmupJob = tuple[str, tuple[Any, ...], dict[str, Any]]


class WarmupUploads(Protocol):
    def prewarm(self, *args: Any, **kwargs: Any) -> object: ...
    def prewarm_images(self, *args: Any) -> object: ...


@dataclass
class GpuWarmupQueue:
    pending: Iterator[WarmupJob] | None = None
    last_ms: float = 0.0
    max_ms: float = 0.0

    def replace(self, jobs: Iterator[WarmupJob]) -> None:
        """Replace queued work without consuming or running either iterator."""
        self.pending = jobs

    def run_slice(
        self,
        jobs: Iterator[WarmupJob],
        *,
        uploads: WarmupUploads,
        acquire_context: Callable[[], None],
        release_context: Callable[[], None],
        report_error: Callable[[Exception], object],
        clock: Callable[[], float],
    ) -> bool:
        """Run the admitted iterator snapshot; return whether work is pending.

        The caller captures ``jobs`` before checking presentation/context gates.
        A gate or upload can replace the live queue without changing the job
        already admitted for this turn. Completion/failure retains the existing
        cancellation semantics. Context-release and error-sink failures remain
        visible to the caller instead of being converted into warmup success.
        """
        started = clock()
        try:
            acquire_context()
            # A driver upload cannot be preempted. One job may exceed 3 ms;
            # report its actual duration rather than the scheduling target.
            while clock() - started < 0.003:
                kind, args, kwargs = next(jobs)
                if kind == "text":
                    uploads.prewarm(*args, **kwargs)
                elif kind == "image":
                    uploads.prewarm_images(*args)
                break
        except StopIteration:
            self.pending = None
        except Exception as exc:
            self.pending = None
            report_error(exc)
        finally:
            release_context()
            self.last_ms = (clock() - started) * 1000.0
            self.max_ms = max(self.max_ms, self.last_ms)
        return self.pending is not None


class GpuWarmupField:
    """Fixed legacy queue/measurement aliases, never arbitrary window state."""
    def __init__(self, field_name: str) -> None:
        if field_name not in GpuWarmupQueue.__dataclass_fields__:
            raise ValueError(f"Unknown GPU warmup field: {field_name}")
        self.field_name = field_name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance._gpu_warmup, self.field_name)

    def __set__(self, instance, value):
        setattr(instance._gpu_warmup, self.field_name, value)
