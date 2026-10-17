"""Local slider/panel edits through an explicit live capture port.

The adapter owns event admission, target collection lookup and notifications.
These edits cannot inspect a window, choose a module, publish control output or
schedule rendering. Reads remain live because model setters can replace or
cancel the active capture while an edit is in progress.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, MutableMapping, Protocol


class SliderTarget(Protocol):
    def set_from_ratio(self, ratio: float) -> None: ...


class Point(Protocol):
    def x(self) -> float: ...
    def y(self) -> float: ...


class SliderBounds(Protocol):
    def left(self) -> float: ...
    def width(self) -> float: ...


CaptureReader = Callable[[], Mapping[str, Any]]


def apply_slider_ratio(setting: SliderTarget, rect: SliderBounds, point: Point) -> None:
    """One press/movement ratio policy; the setting owns value clamping."""
    setting.set_from_ratio((point.x() - rect.left()) / max(1.0, rect.width()))


def edit_slider(read_capture: CaptureReader, point: Point) -> SliderTarget:
    """Mutate and return the captured setting for the adapter's notification.

    The two payload reads deliberately resolve the capture separately. Once
    obtained, the original setting is returned even if applying its ratio
    cancels/replaces the capture. An exception stops before notification.
    """
    setting = read_capture()["setting"]
    rect = read_capture()["rect"]
    apply_slider_ratio(setting, rect, point)
    return setting


def move_panel(panel: MutableMapping[str, Any], point: Point, read_capture: CaptureReader,
               *, mark_dragged: bool = False) -> None:
    """Edit only the already-selected panel, preserving live per-axis offsets.

    Do not cache the capture/offsets: marking a panel or writing x may replace
    the current capture. The following offset read must observe that change.
    Failed writes propagate with the already-applied prefix intact.
    """
    if mark_dragged:
        panel["dragged"] = True
    panel["x"] = point.x() - read_capture()["offset_x"]
    panel["y"] = point.y() - read_capture()["offset_y"]
