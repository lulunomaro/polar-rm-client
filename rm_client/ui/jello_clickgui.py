from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import time
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QTextLayout,
    QTextOption,
)

from ..model import CATEGORIES, LEGACY_GUI_MODES, Module, Setting
from .animation import Easings, step_damped_spring
from .clickgui_gpu import ClickGuiDrawList, GpuImage, GpuRect, GpuText
from .fonts import ui_font_key, ui_text_layout
from .jello_locale import (
    action_label,
    category_label,
    chinese_enabled,
    description_label,
    module_label,
    option_label,
    profile_language_badge,
    setting_label,
)


JELLO_MAIN_MODE = "JelloSigma"

# Values below are the actual logical coordinates used by Sigma 5.1.0's
# ClickGuiScreen/PanelGroup/ModListView classes.  In particular, PanelGroup is
# constructed at 200x350 but settles at 200x320; the constructor has already
# used the former height to place the second row at y=360.
JELLO_PANEL_WIDTH = 200.0
JELLO_PANEL_HEIGHT = 320.0
JELLO_PANEL_GAP = 10.0
JELLO_PANEL_HEADER = 60.0
JELLO_MODULE_ROW = 30.0

DEEP_TEAL = 0xFF010101
LIGHT_GREYISH_BLUE = 0xFFFEFEFE
DISABLED_PRIMARY = 0x70F5F5F5
DISABLED_SECONDARY = 0x00CACACA
ENABLED_PRIMARY = 0xFF29A6FF
ENABLED_SECONDARY = 0xFF29B8FF
CHECKBOX_GREY = 0xFFC0C0C0
SLIDER_BLUE = 0xFF3B99FD

_LEGACY_LABELS = {
    "PolarOld": "Polar Old",
    "PolarPanel": "Polar Panel",
    "PolarNew": "Polar New",
    "PolarDropdown": "Polar Dropdown",
    "PolarUnified": "Polar One",
    "PolarMulti": "Polar Multi",
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass
class JelloReversibleAnimation:
    """Sigma's duration based, direction-reversible Animation primitive."""

    forward_duration: float
    reverse_duration: float
    progress: float = 0.0
    target: float = 0.0

    def set_target(self, target: float) -> None:
        # Sigma's changeDirection adjusts the start time rather than resetting
        # the percentage. Keeping progress untouched is the equivalent here.
        self.target = 1.0 if float(target) >= 0.5 else 0.0

    def update(self, dt: float) -> float:
        dt = max(0.0, float(dt))
        if self.target > self.progress:
            self.progress = min(1.0, self.progress + dt / max(0.001, self.forward_duration))
        elif self.target < self.progress:
            self.progress = max(0.0, self.progress - dt / max(0.001, self.reverse_duration))
        return self.progress

    def reset(self, value: float = 0.0) -> None:
        self.progress = _clamp01(value)
        self.target = self.progress

    def active(self) -> bool:
        return abs(self.target - self.progress) > 0.0001


class JelloMainTimeline(JelloReversibleAnimation):
    """The exact 450 ms open / 125 ms close ClickGuiScreen timeline."""

    def __init__(self) -> None:
        super().__init__(0.450, 0.125)

    @staticmethod
    def elastic(percent: float, period: float = 0.8) -> float:
        percent = _clamp01(percent)
        if percent >= 1.0:
            return 1.0
        return math.pow(2.0, -10.0 * percent) * math.sin(
            (percent - period / 4.0) * (2.0 * math.pi) / period
        ) + 1.0

    def visual_factor(self) -> float:
        if self.target >= 0.5:
            return 0.5 + 0.5 * self.elastic(self.progress, 0.8)
        # QuadraticEasing.easeOutQuad(raw, 0, 1, 1).
        raw = _clamp01(self.progress)
        return 2.0 * raw - raw * raw

    def blur_radius(self) -> float:
        # RenderUtil2.setShaderParamsRounded(min(1, raw * 4)); Sigma's blur
        # shader has a 20 px base radius. Java Math.round is floor(x + 0.5)
        # for this non-negative input; retaining the integer steps matters to
        # the original shader's loop bounds during the first 112.5 ms.
        raw_radius = 20.0 * min(1.0, _clamp01(self.progress) * 4.0)
        return float(math.floor(raw_radius + 0.5))


@dataclass
class JelloClickGuiState:
    """All non-persistent state owned by one Jello ClickGUI session."""

    settings_module: str | None = None
    settings_closing: bool = False
    old_clickguis_expanded: bool = False
    expanded_action_group: str | None = None
    legacy_mode: str | None = None
    panel_scroll: dict[str, float] = field(
        default_factory=lambda: {category: 0.0 for category in CATEGORIES}
    )
    settings_scroll: dict[str, float] = field(default_factory=dict)
    opened_at: float = 0.0
    reset_pending: bool = False
    settings_panel_animation: JelloReversibleAnimation = field(
        default_factory=lambda: JelloReversibleAnimation(0.200, 0.120)
    )
    settings_background_animation: JelloReversibleAnimation = field(
        default_factory=lambda: JelloReversibleAnimation(0.240, 0.200)
    )
    old_group_animation: JelloReversibleAnimation = field(
        default_factory=lambda: JelloReversibleAnimation(0.220, 0.220)
    )
    dropdown_key: str | None = None
    dropdown_opens_up: bool = False
    dropdown_scroll: float = 0.0
    dropdown_animation: JelloReversibleAnimation = field(
        default_factory=lambda: JelloReversibleAnimation(0.220, 0.220)
    )
    hovered_setting_name: str = ""
    hovered_setting_description: str = ""
    _setting_hovered_this_frame: bool = False
    _setting_hint_hovered: bool = False
    _setting_hint_rect: QRectF = field(default_factory=QRectF)
    setting_hint_animation: JelloReversibleAnimation = field(
        default_factory=lambda: JelloReversibleAnimation(0.114, 0.114)
    )
    _checkbox_animations: dict[str, JelloReversibleAnimation] = field(default_factory=dict)
    _hover_animations: dict[str, JelloReversibleAnimation] = field(default_factory=dict)
    _button_hover_progress: dict[str, float] = field(default_factory=dict)
    _scrollbar_fades: dict[str, float] = field(default_factory=dict)
    _scrollbar_last_activity: dict[str, float] = field(default_factory=dict)
    _settings_scroll_targets: dict[str, float] = field(default_factory=dict)
    _settings_scroll_velocities: dict[str, float] = field(default_factory=dict)
    _settings_scroll_limits: dict[str, float] = field(default_factory=dict)
    _frame_dt: float = 1.0 / 60.0

    @property
    def settings_active(self) -> bool:
        return self.settings_module is not None

    def begin_session(self, now: float | None = None) -> None:
        self.settings_module = None
        self.settings_closing = False
        self.old_clickguis_expanded = False
        self.expanded_action_group = None
        self.legacy_mode = None
        self.dropdown_key = None
        self.dropdown_opens_up = False
        self.opened_at = time.perf_counter() if now is None else float(now)
        self.reset_pending = False
        self.settings_panel_animation.reset(0.0)
        self.settings_background_animation.reset(0.0)
        self.old_group_animation.reset(0.0)
        self.dropdown_animation.reset(0.0)
        self.setting_hint_animation.reset(0.0)
        self.hovered_setting_name = ""
        self.hovered_setting_description = ""
        self._setting_hovered_this_frame = False
        self._setting_hint_hovered = False
        self._setting_hint_rect = QRectF()
        self._checkbox_animations.clear()
        self._hover_animations.clear()
        self._button_hover_progress.clear()
        self._scrollbar_fades.clear()
        self._scrollbar_last_activity.clear()
        self._settings_scroll_targets.clear()
        self._settings_scroll_velocities.clear()
        self._settings_scroll_limits.clear()

    def request_reset(self) -> None:
        self.reset_pending = True
        self.close_settings()

    def finish_reset(self) -> None:
        self.begin_session(self.opened_at)
        self.reset_pending = False

    def open_settings(self, module_name: str) -> None:
        if self.settings_module != str(module_name):
            self.settings_panel_animation.reset(0.0)
            self.settings_background_animation.reset(0.0)
            self.setting_hint_animation.reset(0.0)
            self.hovered_setting_name = ""
            self.hovered_setting_description = ""
            self._setting_hint_rect = QRectF()
            self._setting_hint_hovered = False
        self.settings_module = str(module_name)
        self.settings_closing = False
        self.settings_panel_animation.set_target(1.0)
        self.settings_background_animation.set_target(1.0)
        self.close_dropdown(immediate=True)
        self.old_clickguis_expanded = False
        self.expanded_action_group = None
        self.old_group_animation.reset(0.0)

    def close_settings(self, *, immediate: bool = False) -> None:
        if self.settings_module is None:
            return
        self.settings_closing = True
        self._setting_hint_hovered = False
        self._setting_hint_rect = QRectF()
        self.setting_hint_animation.set_target(0.0)
        self.settings_panel_animation.set_target(0.0)
        self.settings_background_animation.set_target(0.0)
        self.old_clickguis_expanded = False
        self.expanded_action_group = None
        self.old_group_animation.set_target(0.0)
        self.close_dropdown(immediate=immediate)
        self._settings_scroll_targets.clear()
        self._settings_scroll_velocities.clear()
        self._settings_scroll_limits.clear()
        if immediate:
            self.settings_panel_animation.reset(0.0)
            self.settings_background_animation.reset(0.0)
            self.settings_module = None
            self.settings_closing = False

    def toggle_old_group(self, key: str = "ClickGui:Old ClickGUIs") -> None:
        key = str(key)
        self.expanded_action_group = None if self.expanded_action_group == key else key
        self.old_clickguis_expanded = self.expanded_action_group is not None
        self.old_group_animation.set_target(1.0 if self.old_clickguis_expanded else 0.0)
        self.close_dropdown()

    def toggle_dropdown(self, key: str) -> None:
        key = str(key)
        if self.dropdown_key == key and self.dropdown_animation.target > 0.5:
            self.close_dropdown()
            return
        if self.dropdown_key != key:
            self.dropdown_key = key
            self.dropdown_opens_up = False
            self.dropdown_animation.reset(0.0)
        self.dropdown_scroll = 0.0
        self.dropdown_animation.set_target(1.0)

    def close_dropdown(self, *, immediate: bool = False) -> None:
        self.dropdown_animation.set_target(0.0)
        if immediate:
            self.dropdown_animation.reset(0.0)
            self.dropdown_key = None
            self.dropdown_opens_up = False

    def settings_scroll_value(self, key: str, maximum: float) -> float:
        key = str(key)
        maximum = max(0.0, float(maximum))
        self._settings_scroll_limits[key] = maximum
        value = max(0.0, min(maximum, float(self.settings_scroll.get(key, 0.0))))
        self.settings_scroll[key] = value
        if key in self._settings_scroll_targets:
            self._settings_scroll_targets[key] = max(
                0.0,
                min(maximum, self._settings_scroll_targets[key]),
            )
        return value

    def ensure_settings_scroll(self, key: str, target: float, maximum: float) -> None:
        """Move only as far as needed to reveal a dropdown, without a jump."""

        key = str(key)
        current = self.settings_scroll_value(key, maximum)
        existing = self._settings_scroll_targets.get(key, current)
        self._settings_scroll_targets[key] = max(
            existing,
            max(0.0, min(float(maximum), float(target))),
        )
        self._settings_scroll_velocities.setdefault(key, 0.0)

    def scroll_settings_by(self, key: str, delta: float, maximum: float) -> None:
        key = str(key)
        current = self.settings_scroll_value(key, maximum)
        base = self._settings_scroll_targets.get(key, current)
        self._settings_scroll_targets[key] = max(
            0.0,
            min(float(maximum), base + float(delta)),
        )
        self._settings_scroll_velocities.setdefault(key, 0.0)

    def settings_scroll_target(self, key: str) -> float:
        key = str(key)
        return float(self._settings_scroll_targets.get(key, self.settings_scroll.get(key, 0.0)))

    def settings_scroll_active(self, key: str | None = None) -> bool:
        if key is None:
            return bool(self._settings_scroll_targets)
        return str(key) in self._settings_scroll_targets

    def checkbox_progress(self, key: str, checked: bool) -> float:
        animation = self._checkbox_animations.get(key)
        if animation is None:
            # Checkbox uses 70 ms when becoming unchecked and 90 ms when
            # becoming checked (its bytecode stores the inverse percentage).
            initial = 1.0 if checked else 0.0
            animation = JelloReversibleAnimation(0.090, 0.070, initial, initial)
            self._checkbox_animations[key] = animation
        animation.set_target(1.0 if checked else 0.0)
        return animation.progress

    def hover_progress(self, key: str, hovered: bool, duration: float = 0.114) -> float:
        animation = self._hover_animations.get(key)
        if animation is None:
            animation = JelloReversibleAnimation(duration, duration)
            self._hover_animations[key] = animation
        animation.set_target(1.0 if hovered else 0.0)
        return animation.progress

    def button_hover_progress(self, key: str, hovered: bool) -> float:
        """Advance Sigma Button.field20584 by its literal 0.1-per-frame step."""

        value = float(self._button_hover_progress.get(key, 0.0))
        # Sigma's literal 0.1/frame is a 150 ms ramp at its 60 Hz reference.
        # Express it in seconds so 90/120/144 Hz all retain that exact timing.
        value += (6.0 * self._frame_dt) if hovered else (-6.0 * self._frame_dt)
        value = _clamp01(value)
        self._button_hover_progress[key] = value
        return value

    def mark_scroll(self, key: str, now: float | None = None) -> None:
        self._scrollbar_last_activity[str(key)] = (
            time.perf_counter() if now is None else float(now)
        )

    def scrollbar_fade(
        self,
        key: str,
        *,
        scrollable: bool,
        hovered: bool,
        now: float | None = None,
    ) -> float:
        """Port VerticalScrollBar.field20794's literal 0.05/frame fade."""

        key = str(key)
        value = float(self._scrollbar_fades.get(key, 0.0))
        if not scrollable:
            # The original subtracts 1.0 in a single update when the thumb is
            # as tall as the track.
            value = 0.0
        else:
            current = time.perf_counter() if now is None else float(now)
            recently_scrolled = (
                current - float(self._scrollbar_last_activity.get(key, -1.0e9)) < 0.500
            )
            value += (3.0 * self._frame_dt) if (hovered or recently_scrolled) else (-3.0 * self._frame_dt)
            value = _clamp01(value)
        self._scrollbar_fades[key] = value
        return value

    def update(self, dt: float) -> None:
        self._frame_dt = max(0.0, min(0.05, float(dt)))
        self.settings_panel_animation.update(dt)
        self.settings_background_animation.update(dt)
        self.old_group_animation.update(dt)
        self.dropdown_animation.update(dt)
        self.setting_hint_animation.update(dt)
        for animation in self._checkbox_animations.values():
            animation.update(dt)
        for animation in self._hover_animations.values():
            animation.update(dt)
        for key in tuple(self._settings_scroll_targets):
            maximum = max(0.0, float(self._settings_scroll_limits.get(key, 0.0)))
            target = max(0.0, min(maximum, self._settings_scroll_targets[key]))
            current = max(0.0, min(maximum, float(self.settings_scroll.get(key, 0.0))))
            current, velocity = step_damped_spring(
                current,
                self._settings_scroll_velocities.get(key, 0.0),
                target,
                dt,
                stiffness=300.0,
                damping_ratio=1.0,
            )
            current = max(0.0, min(maximum, current))
            if abs(target - current) < 0.02 and abs(velocity) < 0.08:
                current = target
                velocity = 0.0
                self._settings_scroll_targets.pop(key, None)
                self._settings_scroll_velocities.pop(key, None)
                self._settings_scroll_limits.pop(key, None)
            else:
                self._settings_scroll_velocities[key] = velocity
            self.settings_scroll[key] = current
        if self.dropdown_key is not None and self.dropdown_animation.progress <= 0.0001 and self.dropdown_animation.target <= 0.0:
            self.dropdown_key = None
            self.dropdown_opens_up = False
        if (
            self.settings_closing
            and self.settings_panel_animation.progress <= 0.0001
            and self.settings_background_animation.progress <= 0.0001
        ):
            self.settings_module = None
            self.settings_closing = False
            self.old_clickguis_expanded = False
            self.hovered_setting_name = ""
            self.hovered_setting_description = ""
            self._setting_hovered_this_frame = False

    def open_legacy(self, mode: str) -> bool:
        if mode not in LEGACY_GUI_MODES:
            return False
        self.legacy_mode = mode
        self.close_settings(immediate=True)
        return True

    def return_to_jello(self) -> None:
        self.legacy_mode = None
        self.close_settings(immediate=True)
        self.old_clickguis_expanded = False


@dataclass(frozen=True)
class JelloLayout:
    scale: float
    panels: dict[str, QRectF]
    options: QRectF


@dataclass(frozen=True)
class _DropdownRequest:
    key: str
    setting: Setting
    rect: QRectF
    clip: QRectF
    alpha: float
    scale: float
    opens_up: bool = False


class JelloClickGuiRenderer:
    """A coordinate/colour/timeline port of Sigma Jello 5.1.0 ClickGuiScreen."""

    _SHADOW_NAMES = (
        "shadow_corner.png",
        "shadow_corner_2.png",
        "shadow_corner_3.png",
        "shadow_corner_4.png",
        "shadow_left.png",
        "shadow_right.png",
        "shadow_top.png",
        "shadow_bottom.png",
    )

    def __init__(self, asset_root: Path) -> None:
        self.asset_root = Path(asset_root)
        self.options_image = self.asset_root / "options.png"
        self.check_image = self.asset_root / "check.png"
        self.scrollbar_top_image = self.asset_root / "verticalscrollbartop.png"
        self.scrollbar_bottom_image = self.asset_root / "verticalscrollbarbottom.png"
        self.shadow_assets = {
            name.removesuffix(".png"): self.asset_root / name for name in self._SHADOW_NAMES
        }
        # These bundled assets are immutable for the lifetime of the renderer.
        # Re-statting 49 shadow/options paths on every Jello frame cost several
        # milliseconds on Windows at 2K, so validate them once during preload.
        self._available_images = frozenset(
            path for path in self.image_assets if path.is_file()
        )
        self._compat_images: dict[str, QImage] = {}
        self._font_cache: dict[tuple[int, str, float], QFont] = {}
        self._wrapped_description_cache = {}
        self._elided_name_cache: dict[tuple[str, float, int, str, float], str] = {}
        self._module_cache_key: tuple[int, int] | None = None
        self._modules_by_category: dict[str, tuple[Module, ...]] = {}

    @property
    def image_assets(self) -> tuple[Path, ...]:
        return (
            self.options_image,
            self.check_image,
            self.scrollbar_top_image,
            self.scrollbar_bottom_image,
            *(self.shadow_assets[name.removesuffix(".png")] for name in self._SHADOW_NAMES),
        )

    def _image_available(self, path: Path) -> bool:
        return path in self._available_images

    @staticmethod
    def _argb(value: int, alpha: float = 1.0) -> QColor:
        value &= 0xFFFFFFFF
        base_alpha = (value >> 24) & 0xFF
        return QColor(
            (value >> 16) & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
            # RenderUtil2.applyAlpha uses JVM f2i (truncate, never round).
            max(0, min(255, int(base_alpha * _clamp01(alpha)))),
        )

    @classmethod
    def _shift(cls, first: int, second: int, first_weight: float, alpha: float = 1.0) -> QColor:
        # RenderUtil2.shiftTowardsOther interpolates every channel, but Jello's
        # Button immediately calls applyAlpha with the *primary* colour's
        # alpha. Reproduce that overwrite so the transparent secondary colour
        # of disabled module rows changes RGB without fading the row away.
        weight = float(first_weight)
        a = first & 0xFFFFFFFF
        b = second & 0xFFFFFFFF
        channels: list[int] = []
        for shift in (16, 8, 0):
            ca = (a >> shift) & 0xFF
            cb = (b >> shift) & 0xFF
            channels.append(max(0, min(255, int(ca * weight + cb * (1.0 - weight)))))
        base_alpha = (a >> 24) & 0xFF
        out_alpha = max(0, min(255, int(base_alpha * _clamp01(alpha))))
        return QColor(channels[0], channels[1], channels[2], out_alpha)

    @staticmethod
    def _dropdown_height_factor(animation: JelloReversibleAnimation) -> float:
        raw = _clamp01(animation.progress)
        if animation.target >= 0.5:
            # MathHelper.easeOutCubic(raw, 0, 1, 1).
            return 1.0 - math.pow(1.0 - raw, 3.0)
        # QuadraticEasing.easeInQuad(raw, 0, 1, 1).
        return raw * raw

    def _font(self, host: Any, key: str, pixel_size: float) -> QFont:
        # Sigma's FontRenderer sizes are logical pixels. FontBook accepts Qt
        # points, so convert at the canonical 96-DPI ratio; devicePixelRatio
        # is applied later by the renderer and must not inflate layout metrics.
        cache_key = (id(getattr(host, "fonts", host)), str(key), round(float(pixel_size), 3))
        cached = self._font_cache.get(cache_key)
        if cached is None:
            cached = host.font(key, max(1.0, float(pixel_size) * 72.0 / 96.0))
            self._font_cache[cache_key] = cached
        return cached

    def _modules_for(self, host: Any, category: str) -> tuple[Module, ...]:
        registry = host.registry
        cache_key = (id(registry), len(registry.modules))
        if cache_key != self._module_cache_key:
            self._module_cache_key = cache_key
            self._modules_by_category = {
                name: tuple(module for module in registry.modules if module.category == name)
                for name in CATEGORIES
            }
        return self._modules_by_category.get(category, ())

    def _elided_module_name(self, font: QFont, name: str, logical_width: float) -> str:
        key = (
            font.family(),
            round(font.pointSizeF(), 3),
            int(font.weight()),
            str(name),
            round(float(logical_width), 2),
        )
        cached = self._elided_name_cache.get(key)
        if cached is None:
            cached = QFontMetricsF(font).elidedText(
                str(name),
                Qt.TextElideMode.ElideRight,
                float(logical_width),
            )
            self._elided_name_cache[key] = cached
        return cached

    @staticmethod
    def _scale_about_screen(rect: QRectF, scale: float, width: int, height: int) -> QRectF:
        cx = float(width) * 0.5
        cy = float(height) * 0.5
        return QRectF(
            cx + (rect.left() - cx) * scale,
            cy + (rect.top() - cy) * scale,
            rect.width() * scale,
            rect.height() * scale,
        )

    @staticmethod
    def _child_rect(rect: QRectF, visual: float, width: int, height: int) -> tuple[QRectF, float]:
        center = rect.center()
        # ClickGuiScreen casts both per-child translation values with JVM f2i
        # before drawing.  Preserve that truncation (towards zero), including
        # the sub-pixel snap visible during the elastic entrance.
        offset_x = int((center.x() - float(width) * 0.5) * (1.0 - visual) * 0.5)
        offset_y = int((center.y() - float(height) * 0.5) * (1.0 - visual) * 0.5)
        shifted_x = center.x() + offset_x
        shifted_y = center.y() + offset_y
        child_scale = 1.5 - visual * 0.5
        return (
            QRectF(
                shifted_x - rect.width() * child_scale * 0.5,
                shifted_y - rect.height() * child_scale * 0.5,
                rect.width() * child_scale,
                rect.height() * child_scale,
            ),
            child_scale,
        )

    def layout(self, width: int, height: int, requested_scale: float = 1.0) -> JelloLayout:
        requested = max(0.50, min(2.00, float(requested_scale)))
        # 1280x720 is Sigma's canonical 1:1 coordinate space.  Larger
        # viewports may use the full 2x setting; smaller windows cap the
        # effective scale instead of clipping the fourth panel or bottom row.
        viewport_cap = min(2.0, float(width) / 1280.0, float(height) / 720.0)
        scale = max(0.25, min(requested, viewport_cap))
        panels: dict[str, QRectF] = {}
        for index, category in enumerate(CATEGORIES):
            column = index % 4
            row = index // 4
            panels[category] = QRectF(
                (30.0 + column * (JELLO_PANEL_WIDTH + JELLO_PANEL_GAP)) * scale,
                (30.0 + row * 330.0) * scale,
                JELLO_PANEL_WIDTH * scale,
                JELLO_PANEL_HEIGHT * scale,
            )
        options = QRectF(
            float(width) - 69.0 * scale,
            # Kept as a screen-relative child exactly like SmallImage("more").
            float(height) - 55.0 * scale,
            55.0 * scale,
            41.0 * scale,
        )
        return JelloLayout(scale, panels, options)

    @staticmethod
    def raster_scale_for(scale: float) -> float:
        """Bound Jello glyph pages while keeping scaled text at native density."""

        value = max(0.50, min(2.00, float(scale)))
        return max(0.50, min(2.00, math.floor(value * 4.0 + 0.5) / 4.0))

    def build(
        self,
        host: Any,
        state: JelloClickGuiState,
        width: int,
        height: int,
        alpha: float,
    ) -> ClickGuiDrawList:
        host._hotspot_clip = None
        hotspot_start = len(host.hotspots)
        click_gui = host.registry.get("ClickGui")
        scale_setting = click_gui.get_setting("Scale") if click_gui else None
        requested_scale = float(scale_setting.value) if scale_setting else 1.0
        layout = self.layout(width, height, requested_scale)
        commands = ClickGuiDrawList(
            default_text_shadow=False,
            default_text_raster_scale=self.raster_scale_for(layout.scale),
            # Sigma's Helvetica Neue Light must stay Light.  The shared GPU
            # renderer's compact-UI policy deliberately thickens other fonts,
            # which visibly changes Jello's original typography.
            default_text_preserve_weight=True,
            default_text_profile="jello",
            copy_payloads=False,
        )

        timeline = getattr(host, "gui_timeline", None)
        if timeline is not None and hasattr(timeline, "visual_factor"):
            main_visual = float(timeline.visual_factor())
        else:
            raw = _clamp01(alpha)
            target = float(getattr(timeline, "target", 1.0)) if timeline is not None else 1.0
            if target >= 0.5:
                main_visual = 0.5 + 0.5 * JelloMainTimeline.elastic(raw, 0.8)
            else:
                main_visual = 2.0 * raw - raw * raw
        main_settled = bool(
            timeline is None
            or (
                abs(float(getattr(timeline, "progress", 1.0)) - float(getattr(timeline, "target", 1.0)))
                <= 0.0001
            )
        )
        root_animations = (
            state.settings_panel_animation,
            state.settings_background_animation,
        )
        commands.default_text_device_snap = main_settled and all(
            abs(animation.progress - animation.target) <= 0.0001
            for animation in root_animations
        )

        # ClickGuiScreen draws this before it changes the child transform for
        # an open SettingGroup.
        commands.rect(
            QRectF(0.0, 0.0, float(width), float(height)),
            0.0,
            self._argb(DEEP_TEAL, 0.20 * main_visual),
        )

        # Lowest-priority hit target: panel/control targets added below mask it.
        host.add_hotspot(QRectF(0.0, 0.0, float(width), float(height)), "jello_dismiss_background")

        settings_raw = state.settings_background_animation.progress if state.settings_active else 0.0
        settings_ease = 0.0
        if state.settings_active:
            if state.settings_background_animation.target >= 0.5:
                settings_ease = Easings.back_out(settings_raw)
            else:
                settings_ease = settings_raw * settings_raw * settings_raw
        child_visual = main_visual * (1.0 + settings_ease * 0.20)
        child_opacity = 1.0 - settings_raw * 0.10
        child_alpha = min(1.0, max(0.0, child_visual)) * child_opacity

        # ProfileManager's current profile is drawn at the raw screen
        # coordinate before the transformed child list.
        profile = str(host.current_profile()) if hasattr(host, "current_profile") else "default"
        chinese = chinese_enabled(host.registry)
        profile_display = f"{profile} · {profile_language_badge(chinese)}"
        profile_font = self._font(host, "jello_light", 20.0)
        profile_metrics = QFontMetricsF(profile_font)
        profile_width = profile_metrics.horizontalAdvance(profile_display)
        profile_x = float(width) - profile_width - 80.0
        profile_y = float(height) - 47.0
        profile_rect = QRectF(profile_x, profile_y, profile_width + 4.0, profile_metrics.height() + 4.0)
        commands.text(
            profile_rect,
            profile_display,
            profile_font,
            self._argb(LIGHT_GREYISH_BLUE, 0.5 * _clamp01(child_visual)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            raster_scale=1.0,
        )
        host.add_hotspot(
            profile_rect.adjusted(-7.0, -5.0, 7.0, 5.0),
            "jello_toggle_language",
            tooltip="切换为英语" if chinese else "切换为简体中文",
        )

        for category in CATEGORIES:
            panel, child_scale = self._child_rect(layout.panels[category], child_visual, width, height)
            self._build_panel(
                host,
                state,
                commands,
                category,
                panel,
                layout.scale * child_scale,
                child_alpha,
            )

        options, _ = self._child_rect(layout.options, child_visual, width, height)
        if self._image_available(self.options_image):
            commands.image(
                options,
                self.options_image,
                self._argb(LIGHT_GREYISH_BLUE, 0.30 * child_alpha),
            )
        host.add_hotspot(options, "jello_open_settings", "ClickGui")

        if state.settings_active:
            module = host.registry.get(state.settings_module or "")
            if module is not None:
                previous_snap = commands.default_text_device_snap
                commands.default_text_device_snap = bool(
                    main_settled
                    and abs(state.settings_panel_animation.progress - state.settings_panel_animation.target) <= 0.0001
                    and abs(state.settings_background_animation.progress - state.settings_background_animation.target) <= 0.0001
                    and abs(state.old_group_animation.progress - state.old_group_animation.target) <= 0.0001
                    and not state.settings_scroll_active(module.name)
                )
                self._build_settings_modal(host, state, commands, module, width, height, layout.scale)
                commands.default_text_device_snap = previous_snap
        if hasattr(host, "append_minecraft_drop_overlay"):
            host.append_minecraft_drop_overlay(commands)
        if not getattr(host, "gui_visible", True):
            # Closing animation is display-only, even if its last frame stays
            # cached after the timer reaches zero and before the next paint.
            del host.hotspots[hotspot_start:]
            host.gui_scroll_viewports = []
        return commands

    def _build_shadow(
        self,
        commands: ClickGuiDrawList,
        rect: QRectF,
        radius: float,
        alpha: float,
        clip: QRectF | None = None,
    ) -> None:
        if radius <= 0.0 or alpha <= 0.0:
            return
        tint = self._argb(LIGHT_GREYISH_BLUE, alpha)
        x, y, w, h, r = rect.x(), rect.y(), rect.width(), rect.height(), radius
        pieces = (
            ("shadow_corner", QRectF(x - r, y - r, r, r)),
            ("shadow_corner_2", QRectF(x + w, y - r, r, r)),
            ("shadow_corner_3", QRectF(x - r, y + h, r, r)),
            ("shadow_corner_4", QRectF(x + w, y + h, r, r)),
            ("shadow_left", QRectF(x - r, y, r, h)),
            ("shadow_right", QRectF(x + w, y, r, h)),
            ("shadow_top", QRectF(x, y - r, w, r)),
            ("shadow_bottom", QRectF(x, y + h, w, r)),
        )
        for name, piece in pieces:
            asset = self.shadow_assets[name]
            if self._image_available(asset):
                commands.image(piece, asset, tint, clip)

    def _build_panel(
        self,
        host: Any,
        state: JelloClickGuiState,
        commands: ClickGuiDrawList,
        category: str,
        panel: QRectF,
        scale: float,
        alpha: float,
    ) -> None:
        # Empty panel padding and headers are still inside the configuration UI.
        host.add_hotspot(panel, "jello_noop")
        self._build_shadow(commands, panel, 20.0 * scale, alpha)

        # PanelGroup uses separate top/body primitives. Compositing the body as
        # an extra layer over the header preserves both exact opacities while
        # retaining the 10 px outer corner radius.
        header_alpha = min(1.0, alpha * 0.9)
        commands.rect(panel, 10.0 * scale, self._argb(LIGHT_GREYISH_BLUE, header_alpha))
        if alpha > header_alpha + 0.0001 and header_alpha < 0.9999:
            extra = (alpha - header_alpha) / max(0.0001, 1.0 - header_alpha)
            body_clip = QRectF(panel.left(), panel.top() + 60.0 * scale, panel.width(), panel.height() - 60.0 * scale)
            body_shape = QRectF(
                panel.left(),
                panel.top() + 50.0 * scale,
                panel.width(),
                panel.height() - 50.0 * scale,
            )
            commands.rect(body_shape, 10.0 * scale, self._argb(LIGHT_GREYISH_BLUE, extra), clip=body_clip)

        title_font = self._font(host, "jello_light", 25.0)
        commands.text(
            QRectF(
                panel.left() + 20.0 * scale,
                panel.top(),
                panel.width() - 40.0 * scale,
                60.0 * scale,
            ),
            category_label(category, chinese_enabled(host.registry)),
            title_font,
            self._argb(DEEP_TEAL, 0.5 * alpha),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            render_scale=scale,
        )

        viewport = QRectF(
            panel.left(),
            panel.top() + JELLO_PANEL_HEADER * scale,
            panel.width(),
            panel.height() - JELLO_PANEL_HEADER * scale,
        )
        modules = self._modules_for(host, category)
        row_h = JELLO_MODULE_ROW * scale
        maximum = max(0.0, len(modules) * row_h - viewport.height())
        scroll = max(0.0, min(maximum, float(state.panel_scroll.get(category, 0.0))))
        state.panel_scroll[category] = scroll
        host.gui_scroll_viewports.append(
            {"mode": "jello_panel", "key": category, "maximum": maximum, "rect": QRectF(viewport)}
        )
        row_font = self._font(host, "jello_light", 20.0)
        row_alpha = alpha * alpha
        for row_index, module in enumerate(modules):
            row = QRectF(
                panel.left(),
                viewport.top() + row_index * row_h - scroll,
                panel.width(),
                row_h,
            )
            visible = row.intersected(viewport)
            if visible.isEmpty():
                continue
            hovered = row.contains(host.hover_point)
            hover_progress = state.button_hover_progress(
                f"module:{category}:{module.name}", hovered
            )
            # Button.draw feeds 1 - (parentAlpha * field20584) into
            # shiftTowardsOther. At rest this is the primary colour; after ten
            # hovered frames it reaches the secondary colour.
            first_weight = 1.0 - row_alpha * hover_progress
            if module.enabled:
                fill = self._shift(ENABLED_PRIMARY, ENABLED_SECONDARY, first_weight, row_alpha)
                text_color = self._argb(LIGHT_GREYISH_BLUE, row_alpha)
                text_x = 30.0
            else:
                fill = self._shift(DISABLED_PRIMARY, DISABLED_SECONDARY, first_weight, row_alpha)
                text_color = self._argb(DEEP_TEAL, row_alpha)
                text_x = 22.0
            # Sigma's own module names fit its fixed 200 px cards.  RoboMaster
            # names can be longer, so preserve the exact font/row geometry and
            # use Qt's terminal ellipsis rather than letting glyphs be cut at
            # the card edge.  The full name remains available through the
            # hotspot tooltip and the settings title.
            logical_text_width = max(
                1.0,
                panel.width() / max(0.001, scale) - text_x - 10.0,
            )
            localized_name = module_label(module.name, chinese_enabled(host.registry))
            display_name = self._elided_module_name(
                row_font,
                localized_name,
                logical_text_width,
            )
            # 缺陷 B 修复：视口底边与面板底边重合，整宽 radius=0 行若仅靠
            # 矩形裁剪，会在面板底部两角切出方角。这里几何先与视口求交
            # （保住顶部滚动裁切），再交给带面板圆角 SDF 的裁剪（GPU 走
            # clickgui_gpu 的 clip_radius；CPU 兼容路径走 paint_compat 的
            # 圆角 setClipPath），使行像素永远落在面板 10·scale 圆角剪影内。
            commands.rect(visible, 0.0, fill, clip=QRectF(panel), clip_radius=10.0 * scale)
            commands.text(
                row.adjusted(text_x * scale, 0.0, -10.0 * scale, 0.0),
                display_name,
                row_font,
                text_color,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                viewport,
                render_scale=scale,
            )
            host.add_hotspot(
                visible,
                "jello_module_press",
                module.name,
                button=1,
                tooltip=localized_name,
            )
            host.add_hotspot(
                visible,
                "jello_open_settings",
                module.name,
                button=3,
                tooltip=localized_name,
            )

        self._build_scrollbar(
            host,
            state,
            commands,
            f"panel:{category}",
            viewport,
            len(modules) * row_h,
            scroll,
            row_alpha,
            scale,
        )

        if scroll > 0.0:
            shadow_bottom = self.shadow_assets["shadow_bottom"]
            if self._image_available(shadow_bottom):
                commands.image(
                    QRectF(viewport.left(), viewport.top(), viewport.width(), 18.0 * scale),
                    shadow_bottom,
                    self._argb(LIGHT_GREYISH_BLUE, alpha * 0.5),
                )

    def _build_scrollbar(
        self,
        host: Any,
        state: JelloClickGuiState,
        commands: ClickGuiDrawList,
        key: str,
        viewport: QRectF,
        content_height: float,
        offset: float,
        alpha: float,
        scale: float,
    ) -> None:
        """Draw Sigma's exact 11 px VerticalScrollBar and 5 px end textures."""

        bar = QRectF(
            viewport.right() - 16.0 * scale,
            viewport.top() + 5.0 * scale,
            11.0 * scale,
            max(0.0, viewport.height() - 10.0 * scale),
        )
        scrollable = content_height > viewport.height() and bar.height() > 0.0
        fade = state.scrollbar_fade(
            key,
            scrollable=scrollable,
            hovered=bar.contains(host.hover_point),
        )
        if not scrollable or fade <= 0.0:
            return

        cap_h = 5.0 * scale
        track_alpha = _clamp01(alpha) * fade
        white = self._argb(LIGHT_GREYISH_BLUE, 0.45 * track_alpha)
        if self._image_available(self.scrollbar_top_image):
            commands.image(
                QRectF(bar.left(), bar.top(), bar.width(), cap_h),
                self.scrollbar_top_image,
                white,
                viewport,
            )
        if self._image_available(self.scrollbar_bottom_image):
            commands.image(
                QRectF(bar.left(), bar.bottom() - cap_h, bar.width(), cap_h),
                self.scrollbar_bottom_image,
                white,
                viewport,
            )
        commands.rect(
            QRectF(bar.left(), bar.top() + cap_h, bar.width(), max(0.0, bar.height() - 2.0 * cap_h)),
            0.0,
            self._argb(DEEP_TEAL, 0.20 * track_alpha),
            clip=viewport,
        )

        ratio = viewport.height() / max(1.0, content_height)
        thumb_h = min(bar.height(), max(20.0 * scale, bar.height() * ratio))
        maximum = max(1.0, content_height - viewport.height())
        thumb_y = bar.top() + (bar.height() - thumb_h) * _clamp01(offset / maximum)
        thumb = QRectF(bar.left(), thumb_y, bar.width(), thumb_h)
        thumb_factor = 0.7 if thumb.contains(host.hover_point) else 0.3
        thumb_alpha = track_alpha * thumb_factor
        thumb_tint = self._argb(LIGHT_GREYISH_BLUE, thumb_alpha)
        if self._image_available(self.scrollbar_top_image):
            commands.image(
                QRectF(thumb.left(), thumb.top(), thumb.width(), cap_h),
                self.scrollbar_top_image,
                thumb_tint,
                viewport,
            )
        if self._image_available(self.scrollbar_bottom_image):
            commands.image(
                QRectF(thumb.left(), thumb.bottom() - cap_h, thumb.width(), cap_h),
                self.scrollbar_bottom_image,
                thumb_tint,
                viewport,
            )
        commands.rect(
            QRectF(
                thumb.left(),
                thumb.top() + cap_h,
                thumb.width(),
                max(0.0, thumb.height() - 2.0 * cap_h),
            ),
            0.0,
            self._argb(DEEP_TEAL, 0.45 * thumb_alpha),
            clip=viewport,
        )

    @staticmethod
    def _description_line_height(font: QFont, texts, minimum: float) -> float:
        heights = [float(minimum)]
        for text in texts:
            layout = ui_text_layout(font, text)
            if layout.lineCount():
                heights.append(layout.lineAt(0).height() + 2.0)
        return max(heights)

    def wrapped_description(self, font: QFont, text: str, width: float) -> tuple[str, ...]:
        """Lossless, grapheme-safe lines shared by presentation and prewarm.

        Qt returns UTF-16 offsets. Never slice Python strings with those offsets,
        or a supplementary character can disappear or split a following word.
        Only line breaks change; the original font and render density stay intact.
        """
        text = str(text or "")
        width = max(1.0, float(width))
        key = (ui_font_key(font), text, width)
        cached = self._wrapped_description_cache.get(key)
        if cached is not None:
            return cached
        lines = []
        for paragraph in text.splitlines(keepends=True) or [""]:
            body = paragraph.rstrip("\r\n\u2028\u2029")
            ending = paragraph[len(body):]
            if not body:
                lines.append(ending)
                continue
            layout = QTextLayout(body, font)
            # Match the renderer's Regular CJK fallback while retaining Latin
            # Light shaping. The source layout is shared; this one is private.
            layout.setFormats(ui_text_layout(font, body).formats())
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            option.setUseDesignMetrics(False)
            layout.setTextOption(option)
            encoded = body.encode("utf-16-le")
            layout.beginLayout()
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(width)
                start, stop = line.textStart(), line.textStart() + line.textLength()
                lines.append(encoded[start * 2:stop * 2].decode("utf-16-le"))
            layout.endLayout()
            lines[-1] += ending
        result = tuple(lines)
        if len(self._wrapped_description_cache) >= 1024:
            self._wrapped_description_cache.pop(next(iter(self._wrapped_description_cache)))
        self._wrapped_description_cache[key] = result
        return result

    @staticmethod
    def _setting_height(setting: Setting, old_group_progress: float) -> float:
        if setting.kind in {"bool", "number"}:
            return 44.0
        if setting.kind == "action_group":
            return 47.0 + len(setting.options) * 30.0 * _clamp01(old_group_progress)
        return 47.0

    def _build_settings_modal(
        self,
        host: Any,
        state: JelloClickGuiState,
        commands: ClickGuiDrawList,
        module: Module,
        width: int,
        height: int,
        gui_scale: float,
    ) -> None:
        raw = _clamp01(state.settings_panel_animation.progress)
        if raw <= 0.0 and state.settings_closing:
            return
        if state.settings_panel_animation.target >= 0.5:
            eased = Easings.back_out(raw)
        else:
            eased = 2.0 * raw - raw * raw
        own_scale = 0.8 + 0.2 * eased

        gui_scale = max(0.25, min(2.0, float(gui_scale)))
        modal_w = 500.0 * gui_scale
        modal_h = min(600.0 * gui_scale, float(height) * 0.7)
        base_panel = QRectF(
            (float(width) - modal_w) * 0.5,
            (float(height) - modal_h) * 0.5 + 20.0 * gui_scale,
            modal_w,
            modal_h,
        )
        panel = self._scale_about_screen(base_panel, own_scale, width, height)
        scale = gui_scale * own_scale

        backdrop = QRectF(0.0, 0.0, float(width), float(height))
        commands.rect(backdrop, 0.0, self._argb(DEEP_TEAL, 0.45 * raw))
        host.add_hotspot(backdrop, "jello_dismiss_background")
        host.add_hotspot(panel, "jello_noop")

        commands.rect(panel, 10.0 * scale, self._argb(LIGHT_GREYISH_BLUE, raw))
        title = self._scale_about_screen(
            QRectF(base_panel.left(), base_panel.top() - 60.0 * gui_scale, modal_w, 60.0 * gui_scale),
            own_scale,
            width,
            height,
        )
        host.add_hotspot(title, "jello_noop")
        commands.text(
            title,
            self._elided_module_name(
                self._font(host, "jello_medium", 40.0),
                module_label(module.name, chinese_enabled(host.registry)),
                490.0,
            ),
            self._font(host, "jello_medium", 40.0),
            self._argb(LIGHT_GREYISH_BLUE, raw),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            render_scale=scale,
        )

        description_font = self._font(host, "jello_light", 20.0)
        description_lines = self.wrapped_description(
            description_font,
            description_label(module.description or "", chinese_enabled(host.registry)),
            440.0,
        )
        description_line_height = self._description_line_height(description_font, description_lines, 30.0)
        description_height = len(description_lines) * description_line_height
        settings_start = description_height + 19.0
        # Include every description line in the ordinary scroll range. A long
        # header must not squeeze controls out of the viewport or hide its tail.
        base_content = QRectF(
            base_panel.left() + 10.0 * gui_scale,
            base_panel.top() + 30.0 * gui_scale,
            480.0 * gui_scale,
            modal_h - 40.0 * gui_scale,
        )
        content = self._scale_about_screen(base_content, own_scale, width, height)
        old_progress = (
            self._dropdown_height_factor(state.old_group_animation)
            if module.name == "ClickGui"
            else 0.0
        )
        # SettingPanel starts at y=20 and appends its literal 20 px
        # "extentionhack" spacer after the final control.
        total = settings_start + 20.0 + sum(self._setting_height(setting, old_progress) for setting in module.settings)
        maximum = max(0.0, total * scale - content.height())
        scroll = state.settings_scroll_value(module.name, maximum)
        # Sigma normally reveals mode options below their header.  Move the
        # settings viewport first so a low row remains fully visible; only a
        # physically too-short viewport is allowed to use the upward fallback.
        if state.dropdown_key is not None and state.dropdown_animation.target >= 0.5:
            active_y = settings_start
            for candidate in module.settings:
                candidate_key = f"{module.name}:{candidate.name}"
                if candidate_key == state.dropdown_key and candidate.kind == "mode":
                    # Long mode lists get their own viewport. Never scroll
                    # their header above the panel just to reveal every item.
                    popup_height = min(
                        (27.0 * len(candidate.options) + 1.0) * scale,
                        max(0.0, content.height() - 32.0 * scale),
                    )
                    header_bottom = (active_y + 32.0) * scale
                    required_scroll = header_bottom + popup_height - content.height()
                    if required_scroll > scroll:
                        state.ensure_settings_scroll(module.name, required_scroll, maximum)

                    # Decide the direction from the eventual fully revealed
                    # position. The current scroll may still be following its
                    # critical spring; recomputing from that intermediate value
                    # would flip down/up for one frame and make the panel twitch.
                    full_required = header_bottom + popup_height - content.height()
                    eventual_scroll = max(
                        scroll,
                        state.settings_scroll_target(module.name),
                        max(0.0, min(maximum, full_required)),
                    )
                    down_space = max(0.0, content.height() - header_bottom + eventual_scroll)
                    header_top = (active_y + 5.0) * scale
                    up_space = max(0.0, header_top - eventual_scroll)
                    state.dropdown_opens_up = bool(
                        down_space + 0.5 < popup_height and up_space > down_space
                    )
                    break
                active_y += self._setting_height(candidate, old_progress)
        host.gui_scroll_viewports.append(
            {"mode": "jello_settings", "key": module.name, "maximum": maximum, "rect": QRectF(content)}
        )

        for index, text in enumerate(description_lines):
            line_rect = self._scale_about_screen(
                QRectF(base_panel.left() + 30.0 * gui_scale,
                       base_content.top() + index * description_line_height * gui_scale,
                       440.0 * gui_scale, description_line_height * gui_scale),
                own_scale, width, height,
            )
            line_rect.translate(0.0, -scroll)
            commands.text(line_rect, text, description_font,
                          self._argb(DEEP_TEAL, 0.7 * raw),
                          Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                          content, render_scale=scale)

        # SettingPanel retains the last hovered strings while its 114 ms
        # animation reverses.  Clearing them here would make the hint vanish
        # immediately instead of fading out like Sigma.
        previous_hint = getattr(state, "_setting_hint_rect", QRectF())
        state._setting_hint_hovered = bool(
            not state.settings_closing
            and state.setting_hint_animation.progress > 0.001
            and previous_hint.contains(host.hover_point)
        )
        state._setting_hovered_this_frame = False
        dropdown: _DropdownRequest | None = None
        logical_y = settings_start
        for setting in module.settings:
            row_h = self._setting_height(setting, old_progress)
            logical_row = QRectF(
                base_content.left(),
                base_content.top() + logical_y * gui_scale,
                base_content.width(),
                row_h * gui_scale,
            )
            row = self._scale_about_screen(logical_row, own_scale, width, height)
            row.translate(0.0, -scroll)
            request = self._build_setting(
                host,
                state,
                commands,
                module,
                setting,
                row,
                content,
                raw,
                scale,
            )
            if request is not None:
                dropdown = request
            logical_y += row_h

        self._build_scrollbar(
            host,
            state,
            commands,
            f"settings:{module.name}",
            content,
            total * scale,
            scroll,
            raw,
            scale,
        )

        has_hint = not state.settings_closing and (state._setting_hovered_this_frame or state._setting_hint_hovered)
        state.setting_hint_animation.set_target(1.0 if has_hint else 0.0)
        hint_alpha = state.setting_hint_animation.progress
        if hint_alpha > 0.001:
            hint_font = self._font(host, "jello_light", 14.0)
            name = state.hovered_setting_name
            card_width = min(480.0 * scale, max(1.0, width - 16.0 * scale))
            inner_width = max(1.0, card_width / scale - 20.0)
            hint_lines = self.wrapped_description(hint_font, state.hovered_setting_description, inner_width)
            line_height = self._description_line_height(hint_font, hint_lines, 18.0)
            title_height = self._description_line_height(hint_font, (name,), 22.0)
            body_height = len(hint_lines) * line_height * scale
            card_height = min((len(hint_lines) * line_height + title_height + 22.0) * scale,
                              max(1.0, height - 16.0 * scale))
            left = max(8.0 * scale, min(panel.left() + 10.0 * scale,
                                      width - 8.0 * scale - card_width))
            top = max(8.0 * scale, min(content.bottom() + 18.0 * scale,
                                     height - 8.0 * scale - card_height))
            card = QRectF(left, top, card_width, card_height)
            state._setting_hint_rect = card
            commands.rect(card, 6.0 * scale, self._argb(DEEP_TEAL, 0.94 * hint_alpha))
            host.add_hotspot(card, "jello_noop")
            hint_color = self._argb(LIGHT_GREYISH_BLUE, 0.8 * hint_alpha)
            title_rect = QRectF(left + 10.0 * scale, top + 8.0 * scale,
                                card_width - 20.0 * scale, title_height * scale)
            for dx in (0.0, 1.0 * scale):
                commands.text(title_rect.translated(dx, 0.0), name, hint_font, hint_color,
                              Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                              card, render_scale=scale)
            hint_view = card.adjusted(10.0 * scale, (title_height + 12.0) * scale, -10.0 * scale, -10.0 * scale)
            hint_maximum = max(0.0, body_height - hint_view.height())
            hint_key = f"hint:{module.name}:{name}"
            hint_scroll = state.settings_scroll_value(hint_key, hint_maximum)
            # The whole topmost card owns wheel input, even when its body
            # fits. Otherwise its title/padding can scroll obscured controls.
            # Reuse the settings spring for unusually long, hover-held help.
            host.gui_scroll_viewports.append(
                {"mode": "jello_settings", "key": hint_key,
                 "maximum": hint_maximum, "rect": QRectF(card)}
            )
            for index, text in enumerate(hint_lines):
                rect = QRectF(hint_view.left(), hint_view.top() + index * line_height * scale - hint_scroll,
                              hint_view.width(), line_height * scale)
                commands.text(rect, text, hint_font, hint_color,
                              Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                              hint_view, render_scale=scale)
        else:
            state._setting_hint_rect = QRectF()

        if dropdown is not None:
            self._build_dropdown_popup(host, state, commands, dropdown)

    def _label(
        self,
        host: Any,
        state: JelloClickGuiState,
        commands: ClickGuiDrawList,
        setting: Setting,
        rect: QRectF,
        clip: QRectF,
        alpha: float,
        scale: float,
    ) -> None:
        visible = rect.intersected(clip)
        if visible.isEmpty():
            return
        localized_name = setting_label(setting.name, chinese_enabled(host.registry))
        localized_name = self._elided_module_name(
            self._font(host, "jello_light", 25.0),
            localized_name,
            max(1.0, rect.width() / max(0.001, scale) - 4.0),
        )
        commands.text(
            rect,
            localized_name,
            self._font(host, "jello_light", 25.0),
            self._argb(DEEP_TEAL, alpha),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            clip,
            render_scale=scale,
        )
        if not getattr(state, "_setting_hint_hovered", False) and rect.contains(host.hover_point):
            state._setting_hovered_this_frame = True
            state.hovered_setting_name = localized_name
            state.hovered_setting_description = description_label(setting.description or "", chinese_enabled(host.registry))

    def _build_setting(
        self,
        host: Any,
        state: JelloClickGuiState,
        commands: ClickGuiDrawList,
        module: Module,
        setting: Setting,
        row: QRectF,
        clip: QRectF,
        alpha: float,
        scale: float,
    ) -> _DropdownRequest | None:
        key = f"{module.name}:{setting.name}"
        label_h = (24.0 if setting.kind in {"bool", "number"} else 27.0) * scale
        label_y = row.top() + (2.0 * scale if setting.kind in {"mode", "action", "action_group"} else 0.0)
        label = QRectF(row.left() + 20.0 * scale, label_y, 200.0 * scale, label_h)
        self._label(host, state, commands, setting, label, clip, alpha, scale)

        if setting.kind == "bool":
            checkbox = QRectF(row.left() + 436.0 * scale, row.top() + 6.0 * scale, 24.0 * scale, 24.0 * scale)
            checked = bool(setting.value)
            active = state.checkbox_progress(key, checked)
            hovered = checkbox.contains(host.hover_point)
            off_alpha = (0.60 if hovered else 0.43) * (1.0 - active) * alpha
            commands.rect(checkbox, 10.0 * scale, self._argb(CHECKBOX_GREY, off_alpha), clip=clip)
            if active > 0.001:
                active_color = self._shift(ENABLED_PRIMARY, DEEP_TEAL, 0.9 if hovered else 1.0, active * alpha)
                commands.rect(checkbox, 10.0 * scale, active_color, clip=clip)
                check_scale = 1.5 - 0.5 * active
                check_rect = QRectF(
                    checkbox.center().x() - checkbox.width() * check_scale * 0.5,
                    checkbox.center().y() - checkbox.height() * check_scale * 0.5,
                    checkbox.width() * check_scale,
                    checkbox.height() * check_scale,
                )
                if self._image_available(self.check_image):
                    commands.image(check_rect, self.check_image, self._argb(LIGHT_GREYISH_BLUE, active * alpha), clip)
            host.add_hotspot(checkbox.intersected(clip), "setting_toggle", setting)
            return None

        if setting.kind == "number":
            slider = QRectF(row.left() + 334.0 * scale, row.top() + 6.0 * scale, 126.0 * scale, 24.0 * scale)
            ratio = _clamp01(setting.ratio())
            # Slider.method13140 positions its 24 px button with
            # (int) ((126 - 24) * ratio + .5F).  Keeping this in logical
            # pixels is observable at every half-pixel threshold and also
            # controls both track segments and the value-label correction.
            knob_offset = int(102.0 * ratio + 0.5)
            track_h = 6.0 * scale
            track_x = slider.left() + 9.0 * scale
            track_y = slider.top() + 9.0 * scale
            knob_x = slider.left() + knob_offset * scale
            active_w = (knob_offset + 6.0) * scale
            inactive_w = max(0.0, (105.0 - knob_offset) * scale)
            track_alpha = alpha * alpha * alpha
            commands.rect(
                QRectF(track_x, track_y, active_w, track_h),
                3.0 * scale,
                self._argb(SLIDER_BLUE, track_alpha),
                clip=clip,
            )
            # adjustColorTowardsWhite(#3B99FD, .8F), including JVM f2i
            # truncation, yields #D7EAFE rather than a rounded approximation.
            inactive = QColor(215, 234, 254, int(255.0 * track_alpha))
            commands.rect(
                QRectF(track_x + active_w, track_y, inactive_w, track_h),
                3.0 * scale,
                inactive,
                clip=clip,
            )
            knob = QRectF(knob_x, slider.top(), 24.0 * scale, 24.0 * scale)
            inner = knob.adjusted(5.0 * scale, 5.0 * scale, -5.0 * scale, -5.0 * scale)
            self._build_shadow(commands, inner, 10.0 * scale, 0.8 * alpha, clip)
            commands.rect(knob, 12.0 * scale, self._argb(LIGHT_GREYISH_BLUE, alpha), clip=clip)

            hovered = slider.contains(host.hover_point)
            value_reveal = state.hover_progress(f"slider:{key}", hovered)
            if value_reveal > 0.001:
                value_font = self._font(host, "jello_light", 14.0)
                value_text = setting.display_value()
                value_width = QFontMetricsF(value_font).horizontalAdvance(value_text) * scale
                correction = max(0.0, (9.0 - knob_offset) * scale)
                value_x = track_x - value_width - 10.0 * scale - correction
                commands.text(
                    QRectF(value_x, track_y - 5.0 * scale, value_width + 3.0 * scale, 20.0 * scale),
                    value_text,
                    value_font,
                    self._argb(DEEP_TEAL, 0.5 * value_reveal * alpha),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    clip,
                    render_scale=scale,
                    # Slider values can change every pointer event.  They use
                    # the prewarmed numeric glyph page so dragging never
                    # rebuilds a whole-line atlas during the reveal animation.
                    text_profile="",
                    device_pixel_snap=False,
                )
            drag_rect = QRectF(track_x, slider.top(), 111.0 * scale, slider.height()).intersected(clip)
            host.add_hotspot(drag_rect, "setting_slider", setting)
            return None

        if setting.kind == "mode":
            dropdown = QRectF(row.left() + 337.0 * scale, row.top() + 5.0 * scale, 123.0 * scale, 27.0 * scale)
            is_open = state.dropdown_key == key
            arrow_reveal = state.dropdown_animation.progress if is_open else 0.0
            text_font = self._font(host, "jello_light", 18.0)
            animated = is_open
            value_text = str(setting.value)
            localized_value = option_label(value_text, chinese_enabled(host.registry))
            display_value = self._elided_module_name(text_font, localized_value, 82.0)
            if not animated or state.dropdown_animation.progress <= 0.001:
                commands.text(
                    dropdown.adjusted(10.0 * scale, 0.0, -27.0 * scale, 0.0),
                    display_value,
                    text_font,
                    self._argb(0xFF131313, 0.7 * alpha),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    clip,
                    render_scale=scale,
                )
                commands.text(
                    QRectF(
                        dropdown.left() + 103.0 * scale,
                        dropdown.top() + 1.0 * scale,
                        18.0 * scale,
                        dropdown.height(),
                    ),
                    ">",
                    text_font,
                    self._argb(0xFF131313, (1.0 if dropdown.contains(host.hover_point) else 0.5) * 0.7 * alpha),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    clip,
                    rotation_degrees=90.0 * arrow_reveal,
                    render_scale=scale,
                    rotation_origin=QPointF(
                        dropdown.left() + 109.0 * scale,
                        dropdown.top() + 15.0 * scale,
                    ),
                )
            host.add_hotspot(
                dropdown.intersected(clip),
                "jello_mode_dropdown",
                (key, setting),
                tooltip=localized_value,
            )
            # The global reversible animation belongs exclusively to its
            # retained dropdown_key.  Returning a request for every mode row
            # made the final HUD setting steal the popup and its arrow.
            if animated and state.dropdown_animation.progress > 0.001:
                return _DropdownRequest(
                    key,
                    setting,
                    dropdown,
                    clip,
                    alpha,
                    scale,
                    state.dropdown_opens_up,
                )
            return None

        if setting.kind == "action":
            action = QRectF(row.left() + 337.0 * scale, row.top() + 5.0 * scale, 123.0 * scale, 27.0 * scale)
            hovered = action.contains(host.hover_point)
            if hovered:
                commands.rect(action, 6.0 * scale, self._argb(ENABLED_PRIMARY, alpha), clip=clip)
            commands.text(
                action.adjusted(10.0 * scale, 0.0, -10.0 * scale, 0.0),
                action_label("Run", chinese_enabled(host.registry)),
                self._font(host, "jello_light", 18.0),
                self._argb(LIGHT_GREYISH_BLUE if hovered else 0xFF131313, (1.0 if hovered else 0.7) * alpha),
                Qt.AlignmentFlag.AlignCenter,
                clip,
                render_scale=scale,
            )
            host.add_hotspot(action.intersected(clip), "config_action", setting.name)
            return None

        if setting.kind == "action_group":
            header = QRectF(row.left() + 337.0 * scale, row.top() + 5.0 * scale, 123.0 * scale, 27.0 * scale)
            expanded = state.expanded_action_group == key
            commands.text(
                header.adjusted(10.0 * scale, 0.0, -27.0 * scale, 0.0),
                action_label("Hide" if expanded else "Show all", chinese_enabled(host.registry)),
                self._font(host, "jello_light", 18.0),
                self._argb(0xFF131313, 0.7 * alpha),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                clip,
                render_scale=scale,
            )
            commands.text(
                QRectF(
                    header.left() + 103.0 * scale,
                    header.top() + 1.0 * scale,
                    18.0 * scale,
                    header.height(),
                ),
                ">",
                self._font(host, "jello_light", 18.0),
                self._argb(0xFF131313, 0.7 * alpha),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                clip,
                rotation_degrees=90.0 * state.old_group_animation.progress,
                render_scale=scale,
                rotation_origin=QPointF(
                    header.left() + 109.0 * scale,
                    header.top() + 15.0 * scale,
                ),
            )
            host.add_hotspot(header.intersected(clip), "jello_toggle_old_group", key)
            raw_reveal = _clamp01(state.old_group_animation.progress)
            height_reveal = self._dropdown_height_factor(state.old_group_animation)
            if height_reveal > 0.001:
                # This group is the only POLAR extension to Sigma's setting
                # widgets. Keep every retained renderer name readable inside
                # the original 123 px mode-control column.
                option_font = self._font(host, "jello_light", 14.0)
                options_height = len(setting.options) * 30.0 * height_reveal * scale
                options_clip = QRectF(
                    row.left(),
                    row.top() + 47.0 * scale,
                    row.width(),
                    options_height,
                ).intersected(clip)
                for index, mode in enumerate(setting.options):
                    option = QRectF(
                        row.left() + 337.0 * scale,
                        row.top() + (47.0 + index * 30.0) * scale,
                        123.0 * scale,
                        27.0 * scale,
                    )
                    if option.intersected(options_clip).isEmpty():
                        continue
                    hovered = option.contains(host.hover_point)
                    hover_progress = state.button_hover_progress(f"legacy:{mode}", hovered)
                    background = self._shift(
                        LIGHT_GREYISH_BLUE,
                        0xFFEAEAEA,
                        1.0 - hover_progress,
                        alpha,
                    )
                    commands.rect(option, 0.0, background, clip=options_clip)
                    is_legacy = setting.name == "Old ClickGUIs"
                    legacy_label = _LEGACY_LABELS.get(mode, mode) if is_legacy else action_label(mode, chinese_enabled(host.registry))
                    if chinese_enabled(host.registry) and is_legacy:
                        legacy_label = f"旧版 {legacy_label}"
                    commands.text(
                        option.adjusted(8.0 * scale, 0.0, -8.0 * scale, 0.0),
                        self._elided_module_name(option_font, legacy_label, 107.0),
                        option_font,
                        self._argb(DEEP_TEAL, alpha * raw_reveal),
                        Qt.AlignmentFlag.AlignCenter,
                        options_clip,
                        render_scale=scale,
                    )
                    host.add_hotspot(
                        option.intersected(options_clip),
                        "jello_open_legacy" if is_legacy else "config_action",
                        mode,
                    )
            return None

        return None

    def _build_dropdown_popup(
        self,
        host: Any,
        state: JelloClickGuiState,
        commands: ClickGuiDrawList,
        request: _DropdownRequest,
    ) -> None:
        raw = _clamp01(state.dropdown_animation.progress)
        if raw <= 0.001:
            return
        options = list(request.setting.options)
        height_factor = self._dropdown_height_factor(state.dropdown_animation)
        full_h = (27.0 * len(options) + 1.0) * request.scale
        available = (request.rect.top() - request.clip.top() if request.opens_up
                     else request.clip.bottom() - request.rect.bottom())
        viewport_h = min(full_h, max(0.0, available))
        visible_h = viewport_h * height_factor
        popup_top = (request.rect.top() - viewport_h if request.opens_up
                     else request.rect.bottom())
        popup_clip = QRectF(
            request.rect.left(),
            request.rect.top() - visible_h if request.opens_up else popup_top,
            request.rect.width(), visible_h,
        ).intersected(request.clip)
        maximum = max(0.0, full_h - viewport_h)
        state.dropdown_scroll = max(0.0, min(maximum, state.dropdown_scroll))
        # Last registered viewport wins: a popup must consume wheel input
        # before its settings panel, including when already at either limit.
        if not popup_clip.isEmpty():
            host.gui_scroll_viewports.append({
                "mode": "jello_dropdown", "key": request.key,
                "maximum": maximum, "rect": QRectF(popup_clip),
            })

        # Dropdown.draw casts two exact Jello shadows around the header plus
        # currently revealed option height, then fades a white rounded header
        # with the raw 220 ms animation percentage.
        if request.opens_up:
            container = QRectF(
                request.rect.left(),
                request.rect.top() - visible_h + request.scale,
                request.rect.width(),
                max(0.0, request.rect.height() + visible_h - request.scale),
            )
        else:
            container = QRectF(
                request.rect.left(),
                request.rect.top(),
                request.rect.width(),
                max(0.0, request.rect.height() + visible_h - request.scale),
            )
        self._build_shadow(
            commands,
            container,
            6.0 * request.scale,
            0.10 * request.alpha * raw,
            request.clip,
        )
        self._build_shadow(
            commands,
            container,
            20.0 * request.scale,
            0.20 * request.alpha * raw,
            request.clip,
        )
        # Seal the option stack with one continuous opaque surface before
        # drawing individual hover rows. Adjacent zero-radius GPU rectangles
        # each anti-alias their edge; without this backplate their shared
        # one-device-pixel seam exposes the blue checkbox/slider row below.
        seam = max(0.75, 1.0 * request.scale)
        if request.opens_up:
            seal_rect = QRectF(
                request.rect.left(),
                popup_top,
                request.rect.width(),
                full_h + seam,
            )
            seal_clip = QRectF(popup_clip)
            seal_clip.setBottom(min(request.clip.bottom(), popup_clip.bottom() + seam))
        else:
            seal_rect = QRectF(
                request.rect.left(),
                popup_top - seam,
                request.rect.width(),
                full_h + seam,
            )
            seal_clip = QRectF(popup_clip)
            seal_clip.setTop(max(request.clip.top(), popup_clip.top() - seam))
        commands.rect(
            seal_rect,
            0.0,
            self._argb(LIGHT_GREYISH_BLUE, request.alpha),
            clip=seal_clip,
        )
        commands.rect(
            request.rect,
            6.0 * request.scale,
            self._argb(LIGHT_GREYISH_BLUE, request.alpha * raw),
            clip=request.clip,
        )
        option_font = self._font(host, "jello_light", 18.0)
        current_value = str(request.setting.value)
        localized_current = option_label(current_value, chinese_enabled(host.registry))
        commands.text(
            request.rect.adjusted(10.0 * request.scale, 0.0, -27.0 * request.scale, 0.0),
            self._elided_module_name(option_font, localized_current, 82.0),
            option_font,
            self._argb(0xFF131313, 0.7 * request.alpha),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            request.clip,
            render_scale=request.scale,
        )
        commands.text(
            QRectF(
                request.rect.left() + 103.0 * request.scale,
                request.rect.top() + 1.0 * request.scale,
                18.0 * request.scale,
                request.rect.height(),
            ),
            ">",
            option_font,
            self._argb(
                0xFF131313,
                (1.0 if request.rect.contains(host.hover_point) else 0.5)
                * 0.7
                * request.alpha,
            ),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            request.clip,
            rotation_degrees=90.0 * raw,
            render_scale=request.scale,
            rotation_origin=QPointF(
                request.rect.left() + 109.0 * request.scale,
                request.rect.top() + 15.0 * request.scale,
            ),
            device_pixel_snap=False,
        )
        if popup_clip.isEmpty():
            return
        for index, value in enumerate(options):
            option = QRectF(
                request.rect.left(),
                popup_top + index * 27.0 * request.scale - state.dropdown_scroll,
                request.rect.width(),
                27.0 * request.scale,
            )
            visible = option.intersected(popup_clip)
            if visible.isEmpty():
                continue
            hovered = option.contains(host.hover_point)
            hover_progress = state.button_hover_progress(f"dropdown:{request.key}:{index}", hovered)
            background = self._shift(
                LIGHT_GREYISH_BLUE,
                0xFFEAEAEA,
                1.0 - hover_progress,
                request.alpha,
            )
            commands.rect(option, 0.0, background, clip=popup_clip)
            localized_option = option_label(value, chinese_enabled(host.registry))
            commands.text(
                option.adjusted(10.0 * request.scale, 0.0, -10.0 * request.scale, 0.0),
                self._elided_module_name(option_font, localized_option, 103.0),
                option_font,
                self._argb(DEEP_TEAL, request.alpha),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                popup_clip,
                render_scale=request.scale,
            )
            host.add_hotspot(
                visible,
                "jello_mode_select",
                (request.setting, value),
                tooltip=localized_option,
            )

    def paint_compat(
        self,
        host: Any,
        state: JelloClickGuiState,
        painter: QPainter,
        width: int,
        height: int,
        alpha: float,
    ) -> None:
        draw_list = self.build(host, state, width, height, alpha)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for command in draw_list.commands:
            payload = command.payload
            painter.save()
            painter.setOpacity(command.opacity)
            clip = getattr(payload, "clip", None)
            if clip is not None:
                painter.setClipRect(clip)
            if isinstance(payload, GpuRect):
                path = QPainterPath()
                path.addRoundedRect(payload.rect, payload.radius, payload.radius)
                # 缺陷 B 修复：与 GPU 管线的 clip_radius 语义保持一致。
                # 这里用路径交集而非 setClipPath 实现圆角裁剪，是刻意选择而
                # 非 clip 不可用——管线内实验（tmp_corner_verify/clip_audit_out）
                # 证明本窗口三种绘制面的 QPainter clip 均生效；交集方案的优势
                # 是正确性不依赖引擎 clip 行为（raster/GL 一致），且把圆角语义
                # 烘焙进几何、与 GPU SDF mask 的结果逐像素对齐。
                clip_radius = float(getattr(payload, "clip_radius", 0.0) or 0.0)
                if clip is not None and clip_radius > 0.0:
                    rounded_clip = min(clip_radius, min(clip.width(), clip.height()) * 0.5)
                    clip_path = QPainterPath()
                    clip_path.addRoundedRect(clip, rounded_clip, rounded_clip)
                    path = path.intersected(clip_path)
                if payload.gradient is not None:
                    brush = QLinearGradient(payload.rect.left(), 0.0, payload.rect.right(), 0.0)
                    brush.setColorAt(0.0, payload.fill)
                    brush.setColorAt(1.0, payload.gradient)
                    painter.fillPath(path, brush)
                else:
                    painter.fillPath(path, payload.fill)
                if payload.outline_width > 0.0 and payload.outline.alpha() > 0:
                    painter.setPen(QPen(payload.outline, payload.outline_width))
                    painter.drawPath(path)
            elif isinstance(payload, GpuText):
                draw_font = QFont(payload.font)
                if abs(payload.render_scale - 1.0) > 0.001:
                    if draw_font.pointSizeF() > 0.0:
                        draw_font.setPointSizeF(draw_font.pointSizeF() * payload.render_scale)
                    elif draw_font.pixelSize() > 0:
                        draw_font.setPixelSize(max(1, int(round(draw_font.pixelSize() * payload.render_scale))))
                painter.setFont(draw_font)
                painter.setPen(payload.color)
                if abs(payload.rotation_degrees) > 0.001:
                    center = (
                        QPointF(payload.rotation_origin)
                        if payload.rotation_origin is not None
                        else payload.rect.center()
                    )
                    painter.translate(center)
                    painter.rotate(payload.rotation_degrees)
                    local = QRectF(payload.rect)
                    local.translate(-center)
                    painter.drawText(local, int(payload.alignment), payload.text)
                else:
                    painter.drawText(payload.rect, int(payload.alignment), payload.text)
            elif isinstance(payload, GpuImage):
                image = self._compat_images.get(payload.source)
                if image is None:
                    image = QImage(payload.source)
                    self._compat_images[payload.source] = image
                if not image.isNull():
                    painter.setRenderHint(
                        QPainter.RenderHint.SmoothPixmapTransform,
                        not payload.nearest,
                    )
                    painter.setOpacity(command.opacity * payload.tint.alphaF())
                    painter.drawImage(payload.rect, image)
            painter.restore()
