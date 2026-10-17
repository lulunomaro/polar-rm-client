"""Vision source selection and worker revocation through explicit ports.

Selection describes the requested pipeline, not a claim that a worker started.
Workers remain responsible for their native handles, retirement and refusing a
replacement while an old owner is live. Registry/environment/model-path reads
stay in the caller so each fallible setting is observed at its established
point between source selection, revocation, configuration and activation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol


class StoppableVisionWorker(Protocol):
    running: bool

    def stop(self, *, wait: bool = True) -> None: ...


NamedVisionWorker = tuple[str, StoppableVisionWorker]


@dataclass
class VisionSelection:
    local: StoppableVisionWorker
    receiver: StoppableVisionWorker
    bridge: StoppableVisionWorker
    source: str = "Mock"
    tongji_enabled: bool = False

    def workers(self) -> tuple[NamedVisionWorker, ...]:
        """Capture the exact current owners before a fallible source request."""
        return (("local", self.local), ("receiver", self.receiver), ("bridge", self.bridge))

    def disable_capture(self) -> None:
        self.source = "Mock"
        self.tongji_enabled = False

    def select_source(self, source: str) -> None:
        self.source = source
        if self.source not in ("Local", "Tongji", "Auto", "Mock"):
            self.source = "Local"

    def set_bridge_enabled(self, enabled: bool) -> None:
        self.tongji_enabled = self.source in ("Tongji", "Auto") and enabled

    def receiver_requested(self, listen: bool) -> bool:
        return self.tongji_enabled and listen

    def local_enabled(self, bridge_enabled: bool) -> bool:
        return self.source in ("Local", "Auto") and bridge_enabled

    def unrequested_workers(self, *, receiver_requested: bool,
                            local_requested: bool) -> list[NamedVisionWorker]:
        # The legacy TCP bridge is compatibility-only and is always revoked.
        stops = [("bridge", self.bridge)]
        if not receiver_requested:
            stops.append(("receiver", self.receiver))
        if not local_requested:
            stops.append(("local", self.local))
        return stops

    @staticmethod
    def stop_workers(owners: Iterable[NamedVisionWorker], *,
                     report_failure: Callable[[str, Exception], None]) -> Exception | None:
        """Attempt every requested revocation before reporting the first cause."""
        failures = []
        for name, owner in owners:
            if not owner.running:
                continue
            try:
                owner.stop(wait=False)
            except Exception as exc:
                failures.append((name, exc))
        for name, exc in failures:
            report_failure(name, exc)
        return failures[0][1] if failures else None


class VisionSelectionField:
    """Five named compatibility fields; no arbitrary window forwarding."""
    def __init__(self, field_name: str) -> None:
        if field_name not in VisionSelection.__dataclass_fields__:
            raise ValueError(f"Unknown vision selection field: {field_name}")
        self.field_name = field_name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance._vision_selection, self.field_name)

    def __set__(self, instance, value):
        setattr(instance._vision_selection, self.field_name, value)
