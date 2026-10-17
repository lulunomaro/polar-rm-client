"""Stable quick-action identifiers and compatibility labels.

The English labels are historical setting values, not localized UI text.
Display translation belongs to the presentation layer; persisted action IDs
remain independent of whichever language the operator selects.
"""
from __future__ import annotations


ACTION_LABELS: dict[str, str] = {
    "economy": "Economy",
    "economy_ammo": "Ammo",
    "toggle_armor_autoaim": "Auto Aim",
    "toggle_armor_lock": "Armor Lock",
    "match_chat": "Match Chat",
    "toggle_spin": "Spin Mode",
    "toggle_friction": "Friction Wheel",
    "clickgui": "ClickGUI",
}

ACTION_ICONS: dict[str, str] = {
    "economy": "EC",
    "economy_ammo": "AM",
    "toggle_armor_autoaim": "AI",
    "toggle_armor_lock": "LK",
    "match_chat": "CH",
    "toggle_spin": "SP",
    "toggle_friction": "FW",
    "clickgui": "UI",
}

DEFAULT_ACTIONS = (
    "economy",
    "economy_ammo",
    "toggle_armor_autoaim",
    "toggle_armor_lock",
    "match_chat",
    "toggle_spin",
    "toggle_friction",
    "clickgui",
)

SECTOR_INDEXES = {
    4: (0, 2, 4, 6),
    6: (0, 1, 2, 4, 5, 6),
    8: tuple(range(8)),
}
