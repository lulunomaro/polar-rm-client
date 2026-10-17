"""Owner-thread camera admission policy, independent of Qt and configuration.

The worker retains native capture/thread cleanup. This controller owns only the
admitted flag and last accepted configuration. A no-wait stop is a revocation
request, never evidence that native release has completed; worker.start remains
the authority that can reject replacement while an old capture is retiring.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

CameraConfiguration = tuple[int, int, int]


class CameraWorker(Protocol):
    running: bool
    last_error: str

    def configure(self, camera_id: int, size: tuple[int, int]) -> None: ...
    def start(self) -> bool: ...
    def stop(self, *, wait: bool = True) -> None: ...


@dataclass
class CameraActivation:
    worker: CameraWorker
    started: bool = False
    configuration: CameraConfiguration | None = None

    def suspend(self, *, reason: str | None = None) -> None:
        """Stop a live sampler before publishing disabled/source-away state."""
        if self.worker.running:
            self.worker.stop(wait=False)
        self.started = False
        if reason is not None:
            self.worker.last_error = reason

    def pause_admitted_capture(self) -> None:
        """A deployment acknowledgement revokes only an admitted capture."""
        if self.started:
            self.worker.stop(wait=False)
            self.started = False

    def ensure(
        self,
        requested: CameraConfiguration,
        *,
        bind_frame_sink: Callable[[], None],
        discard_frame: Callable[[], None],
    ) -> None:
        if self.started and not self.worker.running:
            self.started = False
        if self.started and self.configuration != requested:
            self.worker.stop(wait=False)
            self.started = False
            discard_frame()
        if not self.started:
            camera_id, width, height = requested
            self.worker.configure(camera_id, (width, height))
            # Bind after configure, so a reentrant adapter change selects the
            # current sink. No callback or native start runs in construction.
            bind_frame_sink()
            self.started = self.worker.start()
            if self.started:
                self.configuration = requested


class CameraActivationField:
    """Three named legacy aliases to the same capture admission owner."""
    def __init__(self, field_name: str) -> None:
        if field_name not in CameraActivation.__dataclass_fields__:
            raise ValueError(f"Unknown camera activation field: {field_name}")
        self.field_name = field_name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance._camera_activation, self.field_name)

    def __set__(self, instance, value):
        setattr(instance._camera_activation, self.field_name, value)
