"""Qt-free module settings, mutation behavior, and stable option identifiers.

These types own live operator values and registry notifications. They do not
construct the default catalog, discover devices, or depend on robot runtime
state. ``rm_client.model`` continues to reexport their established public API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .model import RobotState


CATEGORIES = ["Combat", "Movement", "Vision", "Strategy", "System", "Render"]

# 键鼠/驾驶协议槽的固定顺序，与 KeyboardMouseControl 位序一致。
CONTROL_SLOT_ORDER = (
    "W", "S", "A", "D", "SHIFT", "CTRL", "Q", "E",
    "R", "F", "G", "Z", "X", "C", "V", "B",
)

# 可绑定的物理键候选（与 control.BINDABLE_KEY_NAMES 保持同步）。
CONTROL_BIND_OPTIONS = [
    "W", "A", "S", "D", "Q", "E", "R", "F", "G", "Z", "X", "C", "V", "B",
    "Shift", "RightShift", "Ctrl", "Space", "Tab", "Enter", "Escape",
    "Backspace", "Delete", "Insert", "Home", "End", "PageUp", "PageDown",
    "Up", "Down", "Left", "Right", "F1", "F2", "F3", "F4", "F5",
    "F6", "F7", "F8", "F9", "F10", "F11", "F12", "None",
]
# These keys are consumed by the window before control dispatch. Do not offer
# a binding which can never reach its slot (F12 must remain the E-stop).
CONTROL_RESERVED_KEYS = frozenset({"rightshift", "tab", "escape", "f7", "f8", "f9", "f11", "f12"})
# F7 opens the event-command menu before driving dispatch, but certain module
# shortcuts run earlier. Preserve their existing binding policy separately.
MODULE_RESERVED_KEYS = CONTROL_RESERVED_KEYS - {"f7"}
# Module shortcuts use the window's printable-key dispatcher, not the limited
# driver-slot enum. Keep these distinct so a visual keyboard cannot invent an
# unsupported physical-to-firmware control mapping.
MODULE_BIND_OPTIONS = [key for key in CONTROL_BIND_OPTIONS
                       if key.lower() not in MODULE_RESERVED_KEYS - {"escape"}]
MODULE_BIND_OPTIONS = list(dict.fromkeys([*MODULE_BIND_OPTIONS, *list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
                                         *list("0123456789"), "`", "-", "=", "[", "]", "\\", ";", "'", ",", ".", "/", "Alt"]))
CONTROL_BIND_OPTIONS = [key for key in CONTROL_BIND_OPTIONS if key.lower() not in CONTROL_RESERVED_KEYS]

PRIMARY_GUI_MODE = "JelloSigma"

LEGACY_GUI_MODES = [
    "PolarOld",
    "PolarPanel",
    "PolarNew",
    "PolarDropdown",
    "PolarUnified",
    "PolarMulti",
]

# The public ClickGUI surface intentionally contains one entry. Legacy
# renderers remain available through ClickGui -> Old ClickGUIs, but they are
# not normal modes and are never persisted as the startup GUI.
GUI_MODES = [PRIMARY_GUI_MODE]

GUI_MODE_MIGRATIONS = {
    "VapeUnified": "PolarUnified",
    "VapeMulti": "PolarMulti",
}


def normalize_gui_mode(mode: str) -> str:
    del mode
    return PRIMARY_GUI_MODE


def normalize_legacy_gui_mode(mode: str) -> str | None:
    value = GUI_MODE_MIGRATIONS.get(str(mode), str(mode))
    return value if value in LEGACY_GUI_MODES else None

HUD_MODES = [
    "ModuleListHud",
    "TacticalMapHud",
    "KeyBindsHud",
    "RobotListHud",
    "TargetHud",
    "StatusCardsHud",
    "RMIndicatorsHud",
]

MODULE_LIST_STYLES = [
    "PolarTextList",
    "PolarRainbowRight",
    "GlowRows",
    "Segmented",
    "DynamicIsland",
    "KeyBindsRows",
    "StatusBars",
    "RobotCards",
    "LiquidArrayList",
    "LiquidArrayTags",
    "LiquidArrayCompact",
    "LiquidArrayDock",
    # Exact Sigma Jello ActiveMods renderer.  DebugWhiteList is accepted only
    # by config migration and is no longer exposed as a visual style.
    "JelloActiveMods",
]

# 每种可见样式都必须拥有独立视觉签名，避免配置项互相覆盖。
MODULE_LIST_RENDERER_KEYS = {
    "PolarTextList": "polar_text",
    "PolarRainbowRight": "polar_rainbow_right",
    "GlowRows": "glow_rows",
    "Segmented": "segmented",
    "DynamicIsland": "dynamic_island",
    "KeyBindsRows": "keybind_rows",
    "StatusBars": "status_bars",
    "RobotCards": "robot_cards",
    "LiquidArrayList": "liquid_array:list",
    "LiquidArrayTags": "liquid_array:tags",
    "LiquidArrayCompact": "liquid_array:compact",
    "LiquidArrayDock": "liquid_array:dock",
    "JelloActiveMods": "jello_active_mods",
}

TACTICAL_MAP_STATUS_DISPLAYS = [
    "LiquidAuto",
    "LiquidBars",
    "LiquidCards",
    "LegacyOrbit",
    "LegacyBars",
    "Off",
]


@dataclass
class Setting:
    name: str
    kind: str
    value: Any
    min_value: float | None = None
    max_value: float | None = None
    step: float = 1.0
    options: list[str] = field(default_factory=list)
    unit: str = ""
    description: str = ""

    def ratio(self) -> float:
        if self.kind != "number" or self.min_value is None or self.max_value is None:
            return 0.0
        span = self.max_value - self.min_value
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (float(self.value) - self.min_value) / span))

    def set_from_ratio(self, ratio: float) -> None:
        if self.kind != "number" or self.min_value is None or self.max_value is None:
            return
        ratio = max(0.0, min(1.0, ratio))
        raw = self.min_value + (self.max_value - self.min_value) * ratio
        if self.step > 0:
            raw = round(raw / self.step) * self.step
        self.value = max(self.min_value, min(self.max_value, raw))

    def cycle(self, direction: int = 1) -> None:
        if self.kind != "mode" or not self.options:
            return
        try:
            idx = self.options.index(str(self.value))
        except ValueError:
            idx = 0
        self.value = self.options[(idx + direction) % len(self.options)]

    def display_value(self) -> str:
        if self.kind == "number":
            number = float(self.value)
            if self.step >= 1:
                text = str(int(round(number)))
            else:
                text = f"{number:.2f}".rstrip("0").rstrip(".")
            return f"{text}{self.unit}"
        if self.kind == "bool":
            return "ON" if self.value else "OFF"
        return str(self.value)


@dataclass
class Module:
    name: str
    category: str
    keybind: str = "None"
    settings: list[Setting] = field(default_factory=list)
    enabled: bool = False
    description: str = ""
    bind_capture_hint: str = "右键模块或使用绑定行"
    _enabled_change_hook: Callable[["Module", bool], None] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __setattr__(self, name: str, value: Any) -> None:
        """Keep registry cache generations correct for every enabled write.

        Production code normally uses ``toggle_module``/``set_enabled``, but
        configuration migration and diagnostic tools also assign ``enabled``
        directly.  The static HUD cache must observe all three paths without
        rebuilding an enabled-state tuple every display tick.
        """
        if name != "enabled":
            object.__setattr__(self, name, value)
            return
        existed = "enabled" in self.__dict__
        previous = self.__dict__.get("enabled")
        object.__setattr__(self, name, value)
        if existed and previous != value:
            hook = self.__dict__.get("_enabled_change_hook")
            if hook is not None:
                hook(self, bool(value))

    def toggle(self) -> None:
        self.enabled = not self.enabled
        if self.enabled:
            self.on_enable()
        else:
            self.on_disable()

    def set_enabled(self, enabled: bool) -> None:
        if self.enabled == enabled:
            return
        self.toggle()

    def get_setting(self, name: str) -> Setting | None:
        for setting in self.settings:
            if setting.name == name:
                return setting
        return None

    def on_enable(self) -> None:
        pass

    def on_disable(self) -> None:
        pass

    def on_tick(self, state: "RobotState", dt: float) -> None:
        pass

    def on_frame(self, frame: Any) -> None:
        pass

    def on_message(self, topic: str, payload: bytes) -> None:
        pass


class ModuleRegistry:
    def __init__(self, modules: list[Module]) -> None:
        self.modules = modules
        self._by_name = {module.name: module for module in modules}
        self._toggle_listeners: list[Callable[[Module, bool, str], None]] = []
        self.enabled_revision = 0
        for module in modules:
            module._enabled_change_hook = self._on_module_enabled_changed

    def _on_module_enabled_changed(self, _module: Module, _enabled: bool) -> None:
        self.enabled_revision += 1

    def get(self, name: str) -> Module | None:
        return self._by_name.get(name)

    def by_category(self, category: str) -> list[Module]:
        return sorted(
            [module for module in self.modules if module.category == category],
            key=lambda module: module.name.lower(),
        )

    def enabled_modules(self) -> list[Module]:
        return [module for module in self.modules if module.enabled]

    def add_toggle_listener(self, listener: Callable[[Module, bool, str], None]) -> None:
        if listener not in self._toggle_listeners:
            self._toggle_listeners.append(listener)

    def toggle_module(self, module: Module | str, source: str = "internal") -> Module | None:
        target = self.get(module) if isinstance(module, str) else module
        if target is None:
            return None
        target.toggle()
        for listener in tuple(self._toggle_listeners):
            listener(target, target.enabled, source)
        return target

    def tick(self, state: "RobotState", dt: float) -> None:
        for module in self.modules:
            if module.enabled:
                module.on_tick(state, dt)


class GuiModeManager:
    def __init__(self, initial: str = PRIMARY_GUI_MODE) -> None:
        self.mode = normalize_gui_mode(initial)

    def set_mode(self, mode: str) -> None:
        self.mode = normalize_gui_mode(mode)

    def next_mode(self) -> str:
        # Jello Sigma is the only public mode. F9 must not reveal retained
        # legacy renderers.
        self.mode = PRIMARY_GUI_MODE
        return self.mode


def bool_setting(name: str, value: bool, description: str = "") -> Setting:
    return Setting(name=name, kind="bool", value=value, description=description)


def number_setting(
    name: str,
    value: float,
    min_value: float,
    max_value: float,
    step: float,
    unit: str = "",
    description: str = "",
) -> Setting:
    return Setting(
        name=name,
        kind="number",
        value=value,
        min_value=min_value,
        max_value=max_value,
        step=step,
        unit=unit,
        description=description,
    )


def mode_setting(
    name: str,
    value: str,
    options: list[str],
    description: str = "",
) -> Setting:
    return Setting(
        name=name,
        kind="mode",
        value=value,
        options=options,
        description=description,
    )


def action_setting(name: str, description: str = "") -> Setting:
    return Setting(name=name, kind="action", value=name, description=description)


def action_group_setting(name: str, options: list[str], description: str = "") -> Setting:
    """Create a transient group of action buttons.

    The selected action and expansion state are intentionally owned by the UI
    session rather than ``value``, so profiles cannot make a legacy GUI the
    next startup surface.
    """

    return Setting(
        name=name,
        kind="action_group",
        value=None,
        options=list(options),
        description=description,
    )
