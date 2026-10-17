"""Pure compatibility transforms for historical profile identifiers.

Persisted names are wire identifiers. Translated display labels never enter
this module, and migrations never rewrite the file they were loaded from.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


MODE_ALIASES = {
    "Status Display": {
        "Auto": "LiquidAuto", "Rings": "LegacyOrbit", "Bars": "LegacyBars",
        "ReGlassAuto": "LiquidAuto", "ReGlassBars": "LiquidBars", "ReGlassCards": "LiquidCards",
    },
    "Glass Style": {"ReGlass": "LiquidGlass"},
    "ModuleList Surface": {"ReGlass": "LiquidGlass"},
    "ModuleList Style": {
        "PolarLegacy": "PolarTextList", "RainbowRight": "PolarRainbowRight",
        "DebugWhiteList": "JelloActiveMods",
    },
    "Render Backend": {"GPU Cached": "Unified GPU", "Direct GPU": "Unified GPU"},
}
MODULE_MODE_ALIASES = {
    ("ClickGui", "Mode"): {"VapeUnified": "PolarUnified", "VapeMulti": "PolarMulti"},
    ("AudioFeedback", "Sound Theme"): {"Vape": "Polar UI"},
    ("Armor3DESP", "Style"): {"PolarMechanicalWire": "JelloShadow"},
}


def migrated_mode_value(module_name: str, setting_name: str, value: str) -> str:
    aliases = MODULE_MODE_ALIASES.get((module_name, setting_name), MODE_ALIASES.get(setting_name, {}))
    return aliases.get(value, value)


def _settings(modules: dict[str, Any], name: str) -> dict[str, Any]:
    return modules.get(name, {}).get("settings", {})


def _migrate_startup_wallpaper(modules: dict[str, Any], data: dict[str, Any]) -> None:
    if data.get("startup_wallpaper_revision") == 1:
        return
    settings = _settings(modules, "StartupVisual")
    if settings.get("Wallpaper Profile") not in {"TexasMP4", "VendingMachine4K"}:
        return
    if settings.get("Background Mode", "VideoParallax") not in {"ParticleGrid", "PkgScene"}:
        settings["Wallpaper Profile"] = "BlackHole"


def _migrate_alliance(modules: dict[str, Any]) -> None:
    if "Side" in _settings(modules, "AllianceProfile"):
        return
    old_side = _settings(modules, "MatchMode").get("Alliance")
    if old_side in ("Red", "Blue"):
        profile = modules.setdefault("AllianceProfile", {})
        profile.setdefault("settings", {})["Side"] = old_side


def _migrate_elimination_fx(modules: dict[str, Any]) -> None:
    settings = _settings(modules, "EliminationFX")
    if "EliminationFX" not in modules or "settings" not in modules["EliminationFX"]:
        return
    if "Preset" not in settings and "Style" in settings:
        variant = str(settings.get("Variant", "Prime"))
        settings["Preset"] = {
            "Scrolling": "Default Scrolling", "Combo": "Combo", "Card": "Card",
            "CardBar": "Card", "Battlefield 1": "Battlefield 1", "Battlefield 5": "Battlefield 5",
            "Valorant": f"Valorant {variant if variant != 'Default' else 'Prime'}",
        }.get(str(settings["Style"]), "Default Scrolling")
    if "Sound Volume" not in settings and "Volume" in settings:
        settings["Sound Volume"] = settings["Volume"]
    anchor = settings.get("Anchor Policy")
    if anchor == "Safe Area":
        settings.update({"Anchor Policy": "Combat Focus", "X Offset": 0, "Y Offset": 0})
    elif anchor == "Original Position":
        settings["Anchor Policy"] = "Original Bottom"
    settings.setdefault("Responsive Scale", "Auto")


def migrated_modules(data: dict[str, Any]) -> dict[str, Any]:
    """Return detached module data after validated legacy-schema migrations."""
    modules = deepcopy(data.get("modules", {}))
    _migrate_startup_wallpaper(modules, data)
    _migrate_alliance(modules)
    _migrate_elimination_fx(modules)
    return modules
