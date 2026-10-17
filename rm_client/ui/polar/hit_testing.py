"""Hit-target precedence shared by pointer press, hover and owned release.

This policy selects a target only. Modal ownership, capture, dispatch and robot
control admission remain with the event route. In particular a matching target
is not permission to publish a control command.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Reversible

if TYPE_CHECKING:
    from PySide6.QtCore import QPointF
    from .primitives import Hotspot


def topmost_hotspot(
    hotspots: Reversible[Hotspot], point: QPointF | Callable[[], QPointF], *,
    button: int | None = None,
) -> Hotspot | None:
    """Return the last-painted eligible hit without reading later candidates.

    A point supplier preserves live hover coordinates, resolved after each
    candidate geometry lookup. Press/release can pass their stable local point.
    Hover (button=None) deliberately ignores button restrictions. Press/release
    test button eligibility before geometry. Do not prefilter or copy: legacy
    hosts can observe/mutate hit targets during a read, and exceptions must leave
    exactly the same effect prefix as the event loop being adapted.
    """
    for hotspot in reversed(hotspots):
        if button is not None and hotspot.button not in (None, button):
            continue
        if hotspot.rect.contains(point() if callable(point) else point):
            return hotspot
    return None
