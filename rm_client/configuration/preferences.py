"""Preflight interactive preference edits without window or widget dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..control import validate_control_binds
from ..model import CONTROL_SLOT_ORDER, MODULE_BIND_OPTIONS, MODULE_RESERVED_KEYS
from .registry import ModulePort, RegistryPort, SettingId, SettingPort
from .validation import _finite_number


@dataclass(frozen=True)
class PreferenceCapabilities:
    settings: frozenset[SettingId]
    toggles: frozenset[str]


@dataclass(frozen=True)
class SettingAssignment:
    identity: SettingId
    setting: SettingPort
    value: Any


@dataclass(frozen=True)
class PreferenceDraft:
    control_binds: tuple[tuple[str, str], ...]
    settings: tuple[SettingAssignment, ...]
    toggles: tuple[tuple[ModulePort, bool], ...]
    module_binds: tuple[tuple[ModulePort, str], ...]


def _validate_value(identity: SettingId, setting: SettingPort, value: Any) -> None:
    if setting.kind == "bool" and type(value) is not bool:
        raise ValueError("无效开关值：" + identity.setting)
    if setting.kind == "mode" and value not in setting.options:
        legacy_binding = identity.module == "RobotLink" and identity.setting.startswith("Bind ")
        if not (legacy_binding and isinstance(value, str)
                and value.lower() in {option.lower() for option in setting.options}):
            raise ValueError("无效选项：" + identity.setting)
    if setting.kind == "number":
        if not _finite_number(value):
            raise ValueError("无效数值：" + identity.setting)
        below = setting.min_value is not None and value < setting.min_value
        above = setting.max_value is not None and value > setting.max_value
        if below or above:
            raise ValueError("数值超出范围：" + identity.setting)


def _validate_settings(registry: RegistryPort, values: Mapping[SettingId, Any], access: PreferenceCapabilities) -> None:
    for identity, value in values.items():
        module = registry.get(identity.module)
        setting = module.get_setting(identity.setting) if module else None
        if setting is None:
            raise ValueError(f"设置已不存在：{identity.module}/{identity.setting}")
        if identity not in access.settings:
            raise ValueError(f"此页面不能更改设置：{identity.module}/{identity.setting}")
        _validate_value(identity, setting, value)


def _control_binds(current: Mapping[str, str], values: Mapping[SettingId, Any]) -> dict[str, str]:
    candidate = dict(current)
    for slot in CONTROL_SLOT_ORDER:
        identity = SettingId("RobotLink", f"Bind {slot}")
        if identity in values:
            candidate[slot] = values[identity]
    return validate_control_binds(candidate)


def _validate_module_binds(registry: RegistryPort, binds: Mapping[str, str], control: Mapping[str, str]) -> None:
    for name, value in binds.items():
        if registry.get(name) is None or not isinstance(value, str):
            raise ValueError(f"无效模块绑定：{name}")
    occupied = {physical.lower(): slot for slot, physical in control.items() if physical != "None"}
    for name, value in binds.items():
        module = registry.get(name)
        if value == module.keybind:
            continue  # Historical priority overlaps remain unchanged.
        if name in ("ClickGui", "MatchReadiness"):
            raise ValueError(f"此页面不能更改模块绑定：{name}")
        _validate_new_module_bind(registry, name, value, binds, occupied)


def _validate_new_module_bind(registry: RegistryPort, name: str, value: str,
                              binds: Mapping[str, str], occupied: Mapping[str, str]) -> None:
    low = value.lower()
    if low == "none":
        return
    canonical_map_tab = name == "TacticalMap" and low == "tab"
    if not canonical_map_tab and (value not in MODULE_BIND_OPTIONS or low in MODULE_RESERVED_KEYS):
        raise ValueError(f"{value} 是保留键或不是可绑定按键")
    if low in occupied:
        raise ValueError(f"{value} 已被驾驶协议槽 {occupied[low]} 使用")
    for other in registry.modules:
        if other.name != name and binds.get(other.name, other.keybind).lower() == low:
            raise ValueError(f"{value} 已被模块 {other.name} 使用")


def _validate_new_control_binds(registry: RegistryPort, current: Mapping[str, str],
                                control: Mapping[str, str], module_binds: Mapping[str, str]) -> None:
    occupied = {physical.strip().lower() for physical in current.values()}
    for physical in control.values():
        low = physical.lower()
        if low == "none" or low in occupied:
            # Historical overlap belongs to the physical key, not its current
            # protocol slot. Permutations must not create a new conflict.
            continue
        for module in registry.modules:
            # Validate the final draft so moving a module shortcut out of the
            # way in this same change is permitted.
            if module_binds.get(module.name, module.keybind).lower() == low:
                raise ValueError(f"{physical} 已被模块 {module.name} 使用")


def prepare_preference_draft(
    registry: RegistryPort,
    current_control_binds: Mapping[str, str],
    values: Mapping[tuple[str, str], Any],
    access: PreferenceCapabilities,
    *,
    toggles: Mapping[str, bool] | None = None,
    module_binds: Mapping[str, str] | None = None,
) -> PreferenceDraft:
    """Validate all requested capabilities and complete binding permutations.

    Stored identifiers and canonical physical key names are returned. Widget
    labels, presentation locale, and translated option text are never inputs.
    """
    values = {SettingId(*identity): value for identity, value in values.items()}
    toggles, module_binds = dict(toggles or {}), dict(module_binds or {})
    _validate_settings(registry, values, access)
    binds = _control_binds(current_control_binds, values)
    _validate_module_binds(registry, module_binds, binds)
    _validate_new_control_binds(registry, current_control_binds, binds, module_binds)
    for name, value in toggles.items():
        if name not in access.toggles or registry.get(name) is None or type(value) is not bool:
            raise ValueError(f"此页面不能更改模块开关：{name}")
    for slot, physical in binds.items():
        identity = SettingId("RobotLink", f"Bind {slot}")
        if identity in values:
            values[identity] = physical
    settings = []
    for identity, value in values.items():
        setting = registry.get(identity.module).get_setting(identity.setting)
        if setting.value != value:
            settings.append(SettingAssignment(identity, setting, value))
    return PreferenceDraft(
        control_binds=tuple(binds.items()), settings=tuple(settings),
        toggles=tuple((registry.get(name), value) for name, value in toggles.items()),
        module_binds=tuple((registry.get(name), value) for name, value in module_binds.items()),
    )
