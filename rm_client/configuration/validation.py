"""Validate a complete profile before any file or live-value mutation."""
from __future__ import annotations

import json
import math
from typing import Any

CONFIG_VERSION = 1


class ConfigurationValueError(ValueError):
    """A rejected field, identified by its persisted path rather than UI text."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Invalid configuration value: {path}")


def _require(valid: bool, path: str) -> None:
    if not valid:
        raise ConfigurationValueError(path)


def _finite_number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _validate_json(data: Any) -> None:
    _require(isinstance(data, dict), "root")
    version = data.get("version", 0)
    _require(type(version) is int and 0 <= version <= CONFIG_VERSION, "version")
    try:
        json.dumps(data, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Configuration must contain finite JSON values") from exc


def _validate_selection(data: dict[str, Any]) -> None:
    for key in ("profile", "gui_mode", "selected_category", "selected_module"):
        if key in data:
            _require(isinstance(data[key], str), key)
    if "module_expanded" in data:
        _require(_string_list(data["module_expanded"]), "module_expanded")


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_setting(value: Any, setting: Any, path: str) -> None:
    _require(value is None or type(value) in (str, int, float, bool), path)
    if setting is None:
        return
    if setting.kind == "bool":
        _require(type(value) is bool, path)
    elif setting.kind == "number":
        _require(_finite_number(value), path)
    elif setting.kind == "mode":
        _require(isinstance(value, str), path)


def _validate_modules(data: dict[str, Any], registry: Any) -> None:
    modules = data.get("modules", {})
    _require(isinstance(modules, dict), "modules")
    for name, item in modules.items():
        _require(isinstance(item, dict), f"modules.{name}")
        if "enabled" in item:
            _require(type(item["enabled"]) is bool, f"{name}.enabled")
        if "keybind" in item:
            _require(isinstance(item["keybind"], str), f"{name}.keybind")
        settings = item.get("settings", {})
        _require(isinstance(settings, dict), f"{name}.settings")
        module = registry.get(name) if registry is not None else None
        for key, value in settings.items():
            setting = module.get_setting(key) if module else None
            _validate_setting(value, setting, f"{name}.{key}")


def _validate_panels(data: dict[str, Any]) -> None:
    for key in ("old_panels", "new_panels", "dropdown_panels", "polar_windows", "vape_windows"):
        panels = data.get(key, {})
        _require(isinstance(panels, dict), key)
        for name, panel in panels.items():
            _require(isinstance(panel, dict), f"{key}.{name}")
            for field, value in panel.items():
                valid = type(value) is bool if field in ("expanded", "dragged") else _finite_number(value)
                _require(valid, f"{key}.{name}.{field}")


def _validate_wheel(data: dict[str, Any]) -> None:
    wheel = data.get("quick_action_wheel", {})
    _require(isinstance(wheel, dict), "quick_action_wheel")
    for key in ("sectors", "radius", "dead_zone"):
        if key in wheel:
            _require(_finite_number(wheel[key]), f"quick_action_wheel.{key}")
    if "show_labels" in wheel:
        _require(type(wheel["show_labels"]) is bool, "quick_action_wheel.show_labels")
    for key in ("label_style", "icon_style"):
        if key in wheel:
            _require(isinstance(wheel[key], str), f"quick_action_wheel.{key}")
    for key in ("actions", "labels", "icons"):
        if key in wheel:
            _require(_string_list(wheel[key]), f"quick_action_wheel.{key}")


def validate_client_config(data: Any, registry: Any = None) -> None:
    """Accept compatible unknown fields; reject malformed and nonfinite values.

    Enum migrations and numeric clamping happen after this whole-document
    preflight. In particular, strings never become enabled safety switches.
    """
    _validate_json(data)
    _validate_selection(data)
    _validate_modules(data, registry)
    _validate_panels(data)
    _validate_wheel(data)
