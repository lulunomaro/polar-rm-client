from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


CONFIG_VERSION = 1
DEFAULT_PROFILE = "default"


class ClientConfigStore:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def profile_names(self) -> list[str]:
        names = []
        for path in sorted(self.config_dir.glob("*.json")):
            if path.is_file():
                names.append(path.stem)
        return names or [DEFAULT_PROFILE]

    def profile_path(self, profile: str) -> Path:
        return self.config_dir / f"{self.clean_profile_name(profile)}.json"

    def load(self, profile: str) -> dict[str, Any] | None:
        path = self.profile_path(profile)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or int(data.get("version", 0)) > CONFIG_VERSION:
            return None
        return data

    def save(self, profile: str, data: dict[str, Any]) -> Path:
        path = self.profile_path(profile)
        payload = dict(data)
        payload["version"] = CONFIG_VERSION
        payload["profile"] = self.clean_profile_name(profile)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def ensure_default(self, data: dict[str, Any]) -> Path:
        path = self.profile_path(DEFAULT_PROFILE)
        if not path.exists():
            self.save(DEFAULT_PROFILE, data)
        return path

    def import_file(self, source: Path) -> str:
        source = Path(source)
        if source.suffix.lower() != ".json" or not source.exists():
            raise ValueError("只能导入已经存在的 .json 配置文件。")
        name = self.clean_profile_name(source.stem)
        target = self.profile_path(name)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return name

    def clean_profile_name(self, profile: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(profile).strip())
        return safe or DEFAULT_PROFILE


def export_client_config(
    registry: Any,
    *,
    gui_mode: str,
    selected_category: str,
    selected_module: str,
    module_expanded: set[str],
    old_panels: dict[str, Any],
    new_panels: dict[str, Any],
    dropdown_panels: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "gui_mode": gui_mode,
        "selected_category": selected_category,
        "selected_module": selected_module,
        "module_expanded": sorted(module_expanded),
        "old_panels": old_panels,
        "new_panels": new_panels,
        "dropdown_panels": dropdown_panels,
        "modules": {
            module.name: {
                "enabled": module.enabled,
                "keybind": module.keybind,
                "settings": {setting.name: setting.value for setting in module.settings},
            }
            for module in registry.modules
        },
    }


def apply_registry_config(registry: Any, data: dict[str, Any]) -> None:
    modules = data.get("modules")
    if not isinstance(modules, dict):
        return
    # 旧配置把阵营挂在 MatchMode；新模块独立管理，但仍能无感加载历史档案。
    alliance_profile = modules.get("AllianceProfile")
    legacy_match = modules.get("MatchMode")
    has_explicit_side = isinstance(alliance_profile, dict) and isinstance(alliance_profile.get("settings"), dict) and "Side" in alliance_profile["settings"]
    if not has_explicit_side and isinstance(legacy_match, dict):
        legacy_settings = legacy_match.get("settings")
        if isinstance(legacy_settings, dict) and legacy_settings.get("Alliance") in ("Red", "Blue"):
            modules = dict(modules)
            modules["AllianceProfile"] = {"settings": {"Side": legacy_settings["Alliance"]}}
    legacy_fx = modules.get("EliminationFX")
    if isinstance(legacy_fx, dict) and isinstance(legacy_fx.get("settings"), dict):
        fx_settings = dict(legacy_fx["settings"])
        if "Preset" not in fx_settings and "Style" in fx_settings:
            old_style = str(fx_settings.get("Style", "Scrolling"))
            old_variant = str(fx_settings.get("Variant", "Prime"))
            fx_settings["Preset"] = {
                "Scrolling": "Default Scrolling", "Combo": "Combo", "Card": "Card",
                "CardBar": "Card", "Battlefield 1": "Battlefield 1", "Battlefield 5": "Battlefield 5",
                "Valorant": f"Valorant {old_variant if old_variant != 'Default' else 'Prime'}",
            }.get(old_style, "Default Scrolling")
        if "Sound Volume" not in fx_settings and "Volume" in fx_settings:
            fx_settings["Sound Volume"] = fx_settings["Volume"]
        old_anchor = str(fx_settings.get("Anchor Policy", ""))
        if old_anchor == "Safe Area":
            fx_settings["Anchor Policy"] = "Combat Focus"
            fx_settings["X Offset"] = 0
            fx_settings["Y Offset"] = 0
        elif old_anchor == "Original Position":
            fx_settings["Anchor Policy"] = "Original Bottom"
        fx_settings.setdefault("Responsive Scale", "Auto")
        legacy_fx = dict(legacy_fx)
        legacy_fx["settings"] = fx_settings
        modules = dict(modules)
        modules["EliminationFX"] = legacy_fx
    for name, module_data in modules.items():
        module = registry.get(str(name))
        if module is None or not isinstance(module_data, dict):
            continue
        if "enabled" in module_data:
            module.set_enabled(bool(module_data["enabled"]))
        keybind = module_data.get("keybind")
        if isinstance(keybind, str):
            module.keybind = keybind or "None"
        settings = module_data.get("settings")
        if not isinstance(settings, dict):
            continue
        for setting_name, value in settings.items():
            setting = module.get_setting(str(setting_name))
            if setting is None:
                continue
            if setting.kind == "bool":
                setting.value = bool(value)
            elif setting.kind == "number":
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if setting.min_value is not None:
                    number = max(setting.min_value, number)
                if setting.max_value is not None:
                    number = min(setting.max_value, number)
                setting.value = number
            elif setting.kind == "mode":
                text_value = str(value)
                if setting.name == "Status Display":
                    text_value = {
                        "Auto": "LiquidAuto",
                        "Rings": "LegacyOrbit",
                        "Bars": "LegacyBars",
                        "ReGlassAuto": "LiquidAuto",
                        "ReGlassBars": "LiquidBars",
                        "ReGlassCards": "LiquidCards",
                    }.get(text_value, text_value)
                elif setting.name == "Glass Style":
                    text_value = {"ReGlass": "LiquidGlass"}.get(text_value, text_value)
                elif setting.name == "ModuleList Surface":
                    text_value = {"ReGlass": "LiquidGlass"}.get(text_value, text_value)
                if not setting.options or text_value in setting.options:
                    setting.value = text_value
            else:
                setting.value = value
