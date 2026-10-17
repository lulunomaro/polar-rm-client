"""Pointer capture and binding state, independent of a window or backend."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Protocol

from PySide6.QtCore import QPointF


class BindingTarget(Protocol):
    name: str
    keybind: str


@dataclass(frozen=True)
class BackgroundRelease:
    consumed: bool = False
    dismiss: bool = False


@dataclass(frozen=True)
class ModuleDragRelease:
    """One consumed module gesture, detached from mutable capture storage."""
    name: str
    moved: bool


@dataclass
class PointerInteraction:
    drag: dict[str, Any] | None = None
    binding_module: BindingTarget | None = None
    background_press: QPointF | None = None
    background_dragged: bool = False
    background_release_owned: bool = False

    def begin_press(self, *, left_button: bool) -> None:
        self.background_press = None
        if left_button:
            self.background_release_owned = False

    def own_background_press(self, point: QPointF) -> None:
        self.background_press = QPointF(point)
        self.background_dragged = False
        self.background_release_owned = True

    def own_release(self) -> None:
        self.background_release_owned = True

    def cancel_background_press(self) -> None:
        # Keyboard navigation cancels dismissal, but the eventual mouse release
        # remains owned by the GUI and must not escape to combat controls.
        self.background_press = None

    def track_owned_pointer(self, point: QPointF) -> bool:
        if not self.background_release_owned:
            return False
        if self.background_press is not None and (point - self.background_press).manhattanLength() > 6.0:
            self.background_dragged = True
        return True

    def release_background(self, point: QPointF, *, left_button: bool,
                           gui_visible: bool, background_target: bool) -> BackgroundRelease:
        if not self.background_release_owned or not left_button:
            return BackgroundRelease()
        press = self.background_press
        self.background_release_owned = False
        self.background_press = None
        dismiss = bool(press is not None and gui_visible and background_target
                       and not self.background_dragged and (point - press).manhattanLength() <= 6.0)
        return BackgroundRelease(consumed=True, dismiss=dismiss)

    def cancel_epoch(self) -> None:
        self.background_release_owned = bool(
            self.background_release_owned or self.drag or self.background_press is not None)
        self.drag = None
        self.binding_module = None
        self.background_press = None

    def track_module_drag(self, point: QPointF) -> None:
        """Update an admitted module capture without rendering/window effects.

        Keep live writes: a compatibility capture can replace self.drag while
        storing the point, and the threshold update must see that successor.
        """
        start = self.drag.get("start", point)
        self.drag["point"] = QPointF(point)
        if isinstance(start, QPointF) and math.dist((start.x(), start.y()), (point.x(), point.y())) >= 6.0:
            self.drag["moved"] = True

    def take_module_drag(self) -> ModuleDragRelease:
        """Consume an admitted module capture before interpreting its payload.

        Legacy dictionaries are normalized only here. Payload read failures
        cannot resurrect the command, and callbacks receive an immutable value
        rather than retaining access to live capture state.
        """
        drag = self.take_drag()
        return ModuleDragRelease(str(drag.get("module", "")), bool(drag.get("moved")))

    def take_drag(self) -> dict[str, Any] | None:
        """Transfer the captured command before running fallible UI effects.

        A repeated/reentrant release must not replay a completed module toggle
        or slot assignment. New captures created by those effects are separate
        commands and remain owned by this interaction.
        """
        drag = self.drag
        self.drag = None
        return drag

    def finish_binding(self, key: str) -> BindingTarget | None:
        module = self.binding_module
        if module is not None:
            module.keybind = key
            self.binding_module = None
        return module


class PointerField:
    """Explicit legacy window field forwarding; the state remains single-owned.

    Old renderers and extension hosts may still assign the historical fields.
    Input production paths use PointerInteraction's grouped capture actions.
    This adapter never scans or copies a window's attributes.
    """
    def __init__(self, field_name: str):
        if field_name not in PointerInteraction.__dataclass_fields__:
            raise ValueError(f"Unknown pointer field: {field_name}")
        self.field_name = field_name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance.pointer_interaction, self.field_name)

    def __set__(self, instance, value):
        setattr(instance.pointer_interaction, self.field_name, value)
