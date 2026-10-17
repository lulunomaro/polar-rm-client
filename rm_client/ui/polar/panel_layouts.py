"""Independent default panel geometry, expressed in logical pixels."""
from dataclasses import dataclass
from collections.abc import Sequence


@dataclass
class PanelLayouts:
    old: dict[str, dict[str, float | bool]]
    new: dict[str, dict[str, float]]
    dropdown: dict[str, dict[str, float | bool]]
    polar: dict[str, dict[str, float]]


def create_panel_layouts(categories: Sequence[str]) -> PanelLayouts:
    return PanelLayouts(
        old={category: {"x": 20.0 + index * 160.0, "y": 20.0,
                        "expanded": category in ("Combat", "Vision", "Render"), "dragged": False}
             for index, category in enumerate(categories)},
        new={category: {"scroll": 0.0} for category in categories},
        dropdown={category: {"expanded": True, "scroll": 0.0} for category in categories},
        polar={category: {"x": 80.0 + (index % 4) * 230.0,
                          "y": 100.0 + (index // 4) * 330.0, "scroll": 0.0}
               for index, category in enumerate((*categories, "Profile"))},
    )
