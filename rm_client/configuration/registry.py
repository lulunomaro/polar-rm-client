"""Explicit registry contracts, stable identities, and restorable value snapshots."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol, Sequence

from .migrations import migrated_mode_value, migrated_modules
from .validation import validate_client_config


class SettingId(NamedTuple):
    module: str
    setting: str


class SettingPort(Protocol):
    name: str
    kind: str
    value: Any
    options: list[str]
    min_value: float | None
    max_value: float | None


class ModulePort(Protocol):
    name: str
    enabled: bool
    keybind: str
    settings: Sequence[SettingPort]

    def get_setting(self, name: str) -> SettingPort | None: ...
    def set_enabled(self, enabled: bool) -> None: ...


class RegistryPort(Protocol):
    modules: Sequence[ModulePort]

    def get(self, name: str) -> ModulePort | None: ...


def setting_identity(registry: RegistryPort, setting: SettingPort) -> SettingId | None:
    """Resolve by object identity; equal values do not confer module ownership."""
    for module in registry.modules:
        if any(setting is candidate for candidate in module.settings):
            return SettingId(module.name, setting.name)
    return None


@dataclass(frozen=True)
class SettingSnapshot:
    setting: SettingPort
    value: Any
    options: tuple[str, ...]

    def restore(self) -> None:
        self.setting.value = deepcopy(self.value)
        self.setting.options = list(self.options)


@dataclass(frozen=True)
class ModuleSnapshot:
    module: ModulePort
    enabled: bool
    keybind: str
    settings: tuple[SettingSnapshot, ...]

    def restore(self) -> None:
        self.module.enabled = self.enabled
        self.module.keybind = self.keybind
        for setting in self.settings:
            setting.restore()


@dataclass(frozen=True)
class RegistrySnapshot:
    """Exact operator values, including dynamic enums and shortcut bindings.

    Restoration deliberately does not call the registry's toggle listeners:
    reverting a value must never replay an enable/output command.
    """
    modules: tuple[ModuleSnapshot, ...]

    @classmethod
    def capture(cls, registry: RegistryPort) -> RegistrySnapshot:
        return cls(tuple(
            ModuleSnapshot(module, module.enabled, module.keybind, tuple(
                SettingSnapshot(setting, deepcopy(setting.value), tuple(setting.options))
                for setting in module.settings
            ))
            for module in registry.modules
        ))

    def restore(self) -> None:
        for module in self.modules:
            module.restore()


def profile_setting_value(module_name: str, setting: SettingPort, value: Any) -> Any:
    """Resolve a validated persisted value without touching its live setting."""
    if setting.kind == "action_group":
        return setting.value
    if setting.kind == "number":
        number = float(value)
        if setting.min_value is not None:
            number = max(setting.min_value, number)
        if setting.max_value is not None:
            number = min(setting.max_value, number)
        return number
    if setting.kind == "mode":
        value = migrated_mode_value(module_name, setting.name, str(value))
        # Preserve an unknown annual schema so backend validation can fail
        # closed instead of silently replacing it with this year's protocol.
        protocol_profile = (module_name, setting.name) == ("MQTTLink", "Referee Profile")
        if protocol_profile or not setting.options or value in setting.options:
            return value
        return setting.value
    return value


def _apply_module(module: ModulePort, data: dict[str, Any]) -> None:
    if "enabled" in data:
        module.set_enabled(data["enabled"])
    if "keybind" in data:
        keybind = data["keybind"]
        # Older HeroLob profiles need an entry key for the dedicated console.
        module.keybind = "K" if module.name == "HeroLob" and keybind in {"", "None"} else keybind or "None"
    for name, value in data.get("settings", {}).items():
        if (module.name, name) == ("ConfigManager", "Profile"):
            continue  # The opened file owns its identity, not embedded data.
        setting = module.get_setting(str(name))
        if setting is not None and setting.kind != "action_group":
            setting.value = profile_setting_value(module.name, setting, value)


def _synchronize_legacy_alliance(registry: RegistryPort) -> None:
    profile = registry.get("AllianceProfile")
    side = profile.get_setting("Side") if profile else None
    match = registry.get("MatchMode")
    legacy_side = match.get_setting("Alliance") if match else None
    if side is not None and legacy_side is not None:
        legacy_side.value = side.value


def apply_profile_modules(registry: RegistryPort, modules: dict[str, Any]) -> None:
    """Apply validated data, retaining normal module enable/disable hooks."""
    for name, data in modules.items():
        module = registry.get(str(name))
        if module is not None:
            _apply_module(module, data)
    _synchronize_legacy_alliance(registry)


def prepare_registry_modules(registry: RegistryPort | None, data: Any) -> dict[str, Any] | None:
    """Preflight both original and migrated fields before any registry write."""
    validate_client_config(data, registry)
    if "modules" not in data:
        return None
    modules = migrated_modules(data)
    validate_client_config({**data, "modules": modules}, registry)
    return modules
