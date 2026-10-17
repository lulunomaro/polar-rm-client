"""One quick-action configuration model for wire data and settings projections.

Legacy profiles may contain both module settings and a top-level wheel payload.
Explicit top-level fields retain their historical precedence. Missing fields
come from module settings, and every new snapshot mirrors the resolved values
into both representations. Labels/icons have no duplicate module setting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..quick_actions import ACTION_LABELS, DEFAULT_ACTIONS
from .registry import ModulePort, RegistryPort, profile_setting_value
from .validation import validate_client_config

SLOT_NAMES = ("Up", "Upper Right", "Right", "Lower Right", "Down", "Lower Left", "Left", "Upper Left")
ACTION_BY_SETTING_VALUE = {label: action for action, label in ACTION_LABELS.items()}
FIELD_SETTINGS = {
    "radius": "Radius", "dead_zone": "Dead Zone", "show_labels": "Show Labels",
    "label_style": "Label Style", "icon_style": "Icon Style",
}


class WheelPort(Protocol):
    def export_config(self) -> dict[str, Any]: ...
    def configure(self, **values: Any) -> None: ...


@dataclass(frozen=True)
class WheelConfiguration:
    sectors: int = 8
    radius: float = 118.0
    dead_zone: float = 38.0
    show_labels: bool = True
    label_style: str = "Action"
    icon_style: str = "Initials"
    actions: tuple[str, ...] = DEFAULT_ACTIONS
    labels: tuple[str, ...] = ("",) * 8
    icons: tuple[str, ...] = ("",) * 8

    def to_wire(self) -> dict[str, Any]:
        return {
            "sectors": self.sectors, "radius": round(self.radius, 2),
            "dead_zone": round(self.dead_zone, 2), "show_labels": self.show_labels,
            "label_style": self.label_style, "icon_style": self.icon_style,
            "actions": list(self.actions), "labels": list(self.labels), "icons": list(self.icons),
        }

    def overlay(self, data: Mapping[str, Any]) -> WheelConfiguration:
        """Resolve one validated partial wire payload over this configuration."""
        validate_client_config({"quick_action_wheel": data})
        values = {**self.to_wire(), **data}
        sectors = min((4, 6, 8), key=lambda count: abs(count - int(values["sectors"])))
        radius = max(72.0, min(180.0, float(values["radius"])))
        dead_zone = max(18.0, min(radius * 0.72, float(values["dead_zone"])))
        label_style = values["label_style"] if values["label_style"] in {"Action", "Compact", "Off"} else "Action"
        icon_style = values["icon_style"] if values["icon_style"] in {"Initials", "Dot", "Off"} else "Initials"
        actions = [action if action in ACTION_LABELS else DEFAULT_ACTIONS[index % 8]
                   for index, action in enumerate(values["actions"])]
        # Preserve the historical padding order for short imported lists.
        actions = tuple((actions + list(DEFAULT_ACTIONS))[:8])
        labels = tuple(([label[:24] for label in values["labels"]] + [""] * 8)[:8])
        icons = tuple(([icon[:4] for icon in values["icons"]] + [""] * 8)[:8])
        return WheelConfiguration(sectors, radius, dead_zone, values["show_labels"],
                                  label_style, icon_style, actions, labels, icons)

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> WheelConfiguration:
        values = {field: settings[name] for field, name in FIELD_SETTINGS.items() if name in settings}
        if "Sectors" in settings:
            values["sectors"] = int(settings["Sectors"])
        values["actions"] = [ACTION_BY_SETTING_VALUE.get(settings.get(name), DEFAULT_ACTIONS[index])
                             for index, name in enumerate(SLOT_NAMES)]
        return cls().overlay(values)

    @classmethod
    def from_module(cls, module: ModulePort) -> WheelConfiguration:
        return cls.from_settings({setting.name: setting.value for setting in module.settings})

    def setting_values(self) -> dict[str, Any]:
        wire = self.to_wire()
        values = {name: wire[field] for field, name in FIELD_SETTINGS.items()}
        values["Sectors"] = str(self.sectors)
        values.update({name: ACTION_LABELS[self.actions[index]] for index, name in enumerate(SLOT_NAMES)})
        return values

    def write_module(self, module: ModulePort) -> None:
        for name, value in self.setting_values().items():
            setting = module.get_setting(name)
            if setting is not None:
                setting.value = value

    def configure_wheel(self, wheel: WheelPort) -> None:
        wheel.configure(
            sector_count=self.sectors, radius=self.radius, dead_zone=self.dead_zone,
            show_labels=self.show_labels, label_style=self.label_style, icon_style=self.icon_style,
            actions=self.actions, custom_labels=self.labels, custom_icons=self.icons,
        )


def sync_wheel_from_module(module: ModulePort | None, wheel: WheelPort) -> None:
    if module is None:
        return
    current = wheel.export_config()
    config = WheelConfiguration.from_module(module).overlay({
        "labels": current.get("labels", []), "icons": current.get("icons", []),
    })
    config.configure_wheel(wheel)
    config.write_module(module)


def sync_module_from_wheel(module: ModulePort | None, wheel: WheelPort) -> None:
    if module is not None:
        WheelConfiguration().overlay(wheel.export_config()).write_module(module)


def restore_wheel_profile(module: ModulePort | None, wheel: WheelPort, data: Mapping[str, Any]) -> None:
    """Restore saved fields after registry loading, preserving custom omissions."""
    if module is None:
        return
    current = wheel.export_config()
    base = WheelConfiguration.from_module(module).overlay({
        "labels": current.get("labels", []), "icons": current.get("icons", []),
    })
    config = base.overlay(data)
    config.configure_wheel(wheel)
    config.write_module(module)


def prepare_wheel_profile(
    registry: RegistryPort, data: Mapping[str, Any], modules: Mapping[str, Any],
) -> WheelConfiguration | None:
    """Resolve both saved projections completely before any registry mutation."""
    module = registry.get("QuickActionWheel")
    wheel = data.get("quick_action_wheel", {})
    if module is None or (not wheel and "QuickActionWheel" not in modules):
        return None
    values = {setting.name: setting.value for setting in module.settings}
    for name, value in modules.get("QuickActionWheel", {}).get("settings", {}).items():
        setting = module.get_setting(name)
        if setting is not None:
            values[name] = profile_setting_value(module.name, setting, value)
    return WheelConfiguration.from_settings(values).overlay(wheel)


def mirror_wheel_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Return a snapshot with matching module and top-level wheel values."""
    modules = data.get("modules", {})
    module = modules.get("QuickActionWheel")
    if module is None:
        return data
    config = WheelConfiguration.from_settings(module.get("settings", {})).overlay(data.get("quick_action_wheel", {}))
    module = {**module, "settings": {**module.get("settings", {}), **config.setting_values()}}
    return {**data, "modules": {**modules, "QuickActionWheel": module}, "quick_action_wheel": config.to_wire()}
