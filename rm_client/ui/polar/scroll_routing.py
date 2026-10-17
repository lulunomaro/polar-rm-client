"""Select a live GUI scroll target without window state or scroll effects.

The result describes which existing owner receives a wheel gesture. It does
not resolve owners or precompute deltas: the Qt adapter preserves live event
reads, model writes, frame scheduling and event acceptance in their order.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Reversible


class ScrollFamily(Enum):
    JELLO_DROPDOWN = auto()
    JELLO_PANEL = auto()
    JELLO_SETTINGS = auto()
    LEGACY_PANEL = auto()
    SETTINGS_MOTION = auto()
    UNIFIED_MOTION = auto()


# Only these known owner attributes can be selected by viewport metadata.
_MODE_ROUTES = (
    ("jello_dropdown", ScrollFamily.JELLO_DROPDOWN, None),
    ("jello_panel", ScrollFamily.JELLO_PANEL, None),
    ("jello_settings", ScrollFamily.JELLO_SETTINGS, None),
    ("old", ScrollFamily.LEGACY_PANEL, "old_panels"),
    ("new", ScrollFamily.LEGACY_PANEL, "new_panels"),
    ("dropdown", ScrollFamily.LEGACY_PANEL, "dropdown_panels"),
    ("polar_multi", ScrollFamily.LEGACY_PANEL, "polar_windows"),
    ("polar_settings", ScrollFamily.SETTINGS_MOTION, None),
)
_JELLO_FAMILIES = frozenset({ScrollFamily.JELLO_DROPDOWN, ScrollFamily.JELLO_PANEL,
                            ScrollFamily.JELLO_SETTINGS})


@dataclass(frozen=True)
class ViewportScroll:
    family: ScrollFamily
    key: Any
    maximum: float
    panel_attribute: str | None = None

    @property
    def is_jello(self) -> bool:
        return self.family in _JELLO_FAMILIES


def scroll_target_at(viewports: Reversible[dict[str, Any]], point: Any) -> ViewportScroll | None:
    """Select the last matching viewport, reading metadata only on a hit.

    Keep the live reverse iteration and mode/key/maximum read order. Unknown
    modes intentionally retain the existing unified-inertia fallback, never a
    fallthrough to combat wheel output. No validation here changes legacy
    exception behavior or clamps the registered maximum in advance.
    """
    for viewport in reversed(viewports):
        if not viewport["rect"].contains(point):
            continue
        mode, key, maximum = viewport["mode"], viewport["key"], float(viewport["maximum"])
        for name, family, panel_attribute in _MODE_ROUTES:
            if mode == name:
                return ViewportScroll(family, key, maximum, panel_attribute)
        return ViewportScroll(ScrollFamily.UNIFIED_MOTION, key, maximum)
    return None
