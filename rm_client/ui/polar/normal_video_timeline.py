"""Normal HUD video provenance and sequence/freshness policy without Qt.

Image creation, inference submission and display/cache effects stay in the UI
adapter. Admission and publication are separate phases: a consumed source frame
can advance its counter before a later adapter effect fails. This owner does not
claim that those effects form an atomic frame transaction.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NormalVideoTimeline:
    capture_sequence: int = 0
    display_sequence: int = 0
    backend_admitted_at: float = 0.0
    requested_mode: str = ""
    frame_source: str = "none"

    def mode_changed(self, normalized_mode: str) -> bool:
        return normalized_mode != self.requested_mode

    def select_mode(self, normalized_mode: str) -> None:
        """Publish selection only after the adapter has discarded the old frame."""
        self.requested_mode = normalized_mode

    def consume_capture(self, sequence: int) -> bool:
        if sequence <= self.capture_sequence:
            return False
        self.capture_sequence = sequence
        return True

    def backend_frame_is_new(self, frame_at: float) -> bool:
        # Negating <= intentionally retains the previous NaN comparison policy.
        return not frame_at <= self.backend_admitted_at

    def admit_backend_frame(self, frame_at: float) -> None:
        """Record consumption before the adapter submits the image to inference."""
        self.backend_admitted_at = frame_at
        self.display_sequence += 1

    def publish_capture_frame(self) -> None:
        self.frame_source = "webcam"
        self.display_sequence += 1

    def publish_backend_frame(self) -> None:
        self.frame_source = "backend"

    def backend_is_fresh(self, normalized_source: str, now: float, max_age: float) -> bool:
        return bool(
            self.requested_mode == normalized_source
            and self.frame_source == "backend"
            and self.backend_admitted_at > 0.0
            and now - self.backend_admitted_at <= max_age
        )

    def readiness_without_backend(self, normalized_source: str) -> bool | None:
        """Return None when the adapter must perform its separate backend query."""
        if normalized_source in {"", "webcam"}:
            return bool(self.requested_mode in {"", "webcam"} and self.frame_source == "webcam")
        if normalized_source == "synthetic":
            return False
        return None

    def clear_frame(self) -> bool:
        if self.backend_admitted_at <= 0.0 and self.frame_source == "none":
            return False
        self.backend_admitted_at = 0.0
        self.frame_source = "none"
        return True


class NormalVideoField:
    """The five fixed compatibility aliases for this timeline, never a proxy."""
    def __init__(self, field_name: str) -> None:
        if field_name not in NormalVideoTimeline.__dataclass_fields__:
            raise ValueError(f"Unknown normal-video field: {field_name}")
        self.field_name = field_name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance._normal_video_timeline, self.field_name)

    def __set__(self, instance, value):
        setattr(instance._normal_video_timeline, self.field_name, value)
