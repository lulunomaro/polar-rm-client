"""Read-only video recovery presentation and generation-bound retry intent."""
from __future__ import annotations

from dataclasses import dataclass

from ...backend.video_source import VideoSourceSnapshot
from ...lifecycle import LifecyclePhase, LifecycleSnapshot

VIDEO_RETRY_ACTION = "retry_video_source_cleanup"
VIDEO_WAIT_ACTION = "wait_video_source_cleanup"


@dataclass(frozen=True)
class VideoRetryToken:
    source_generation: int
    lifecycle_generation: int


@dataclass(frozen=True)
class VideoRecoveryNotice:
    requested_source: str
    pending_resources: tuple[str, ...]
    errors: tuple[str, ...]
    inflight_resources: tuple[str, ...]
    retry_token: VideoRetryToken | None


def video_recovery_notice(
    source: VideoSourceSnapshot,
    lifecycle: LifecycleSnapshot,
    *,
    visible: bool,
    lifecycle_pending: bool,
    configuration_pending: bool,
    profile_pending: bool,
) -> VideoRecoveryNotice | None:
    """A retired owner is not necessarily busy; only live cleanup disables retry."""
    if (not visible or lifecycle_pending or configuration_pending or profile_pending
            or lifecycle.final or lifecycle.failures or lifecycle.phase != LifecyclePhase.IDLE):
        return None
    errors = tuple(f"{failure.phase}: {failure.error}"
                   for failure in (*source.failures, *source.close_failures))
    if not source.pending_resources and not errors:
        return None
    token = (VideoRetryToken(source.generation, lifecycle.generation)
             if source.retryable else None)
    return VideoRecoveryNotice(source.requested_source, source.pending_resources,
                               errors, source.inflight_resources, token)
