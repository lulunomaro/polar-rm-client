"""Values and failure collection for bounded video-source admission.

Acceptance selects a target; it never certifies that a native closer has finished.
The manager owns retry policy, while each transport retains its own native owners.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class VideoSourceFailure:
    phase: str
    error: str


@dataclass(frozen=True)
class VideoSourceRequest:
    source: str
    start: bool


@dataclass(frozen=True)
class VideoStopStatus:
    """Nonblocking owner evidence for presentation, not final release proof."""

    pending: bool = False
    inflight: bool = False
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class VideoSourceSnapshot:
    requested_source: str
    active_source: str
    pending_resources: tuple[str, ...]
    failures: tuple[VideoSourceFailure, ...]
    generation: int = 0
    inflight_resources: tuple[str, ...] = ()
    close_failures: tuple[VideoSourceFailure, ...] = ()

    @property
    def retryable(self) -> bool:
        return bool(self.requested_source and not self.inflight_resources
                    and (self.failures or self.pending_resources or self.close_failures))


class VideoSourceAdmissionError(RuntimeError):
    def __init__(self, failures: tuple[VideoSourceFailure, ...]) -> None:
        self.failures = failures
        super().__init__("; ".join(f"{failure.phase}: {failure.error}" for failure in failures))


class VideoSourceAdmission:
    """Attempt independent revocations even if an earlier admission failed."""

    def __init__(self) -> None:
        self.failures: list[VideoSourceFailure] = []
        self.first_error: Exception | None = None

    def attempt(self, phase: str, operation: Callable[[], object]) -> None:
        try:
            operation()
        except Exception as exc:
            if self.first_error is None:
                self.first_error = exc
            if isinstance(exc, VideoSourceAdmissionError):
                self.failures.extend(VideoSourceFailure(f"{phase}/{failure.phase}", failure.error)
                                     for failure in exc.failures)
            else:
                self.failures.append(VideoSourceFailure(phase, f"{type(exc).__name__}: {exc}"))

    def raise_if_failed(self) -> None:
        if self.failures:
            raise VideoSourceAdmissionError(tuple(self.failures)) from self.first_error
