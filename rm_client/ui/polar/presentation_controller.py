"""Advance modal presentation using explicit animation models and view intent.

No transport, persistence, widget or input ownership crosses this boundary.
Economy lifecycle commits and framebuffer restoration remain the window's job.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class GuiTimeline(Protocol):
    progress: float
    def set_target(self, target: float) -> None: ...
    def update(self, dt: float) -> object: ...


class JelloPresentation(Protocol):
    reset_pending: bool
    def update(self, dt: float) -> object: ...
    def finish_reset(self) -> None: ...


class PanelMorph(Protocol):
    def set_target(self, target: float, duration: float, *, now: float) -> None: ...
    def update(self, *, now: float) -> object: ...


@dataclass(frozen=True)
class PanelIntent:
    gui_visible: bool
    gui_duration: float
    economy_visible: bool
    economy_duration: float


@dataclass(frozen=True)
class PanelPresentationController:
    gui: GuiTimeline
    jello: JelloPresentation
    unified: PanelMorph
    economy: PanelMorph
    resolve_gui_mode: Callable[[], str]

    def advance(self, now: float, dt: float, intent: PanelIntent) -> None:
        self._advance_gui(dt, intent.gui_visible)
        self.unified.set_target(
            1.0 if intent.gui_visible and self.resolve_gui_mode() == "PolarUnified" else 0.0,
            intent.gui_duration, now=now,
        )
        self.unified.update(now=now)
        self.economy.set_target(
            1.0 if intent.economy_visible else 0.0,
            intent.economy_duration, now=now,
        )
        self.economy.update(now=now)

    def _advance_gui(self, dt: float, visible: bool) -> None:
        self.gui.set_target(1.0 if visible else 0.0)
        self.gui.update(dt)
        self.jello.update(dt)
        if self.jello.reset_pending and not visible and self.gui.progress <= 0.001:
            self.jello.finish_reset()


def dynamic_content_duration(*, active: bool, kind: str) -> float:
    if not active:
        return 0.22
    return 0.30 if kind == "module" else 0.80
