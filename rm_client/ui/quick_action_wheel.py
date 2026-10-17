from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from typing import Any

from .animation import step_damped_spring


from ..quick_actions import ACTION_LABELS, ACTION_ICONS, DEFAULT_ACTIONS, SECTOR_INDEXES
from ..configuration.wheel import WheelConfiguration


@dataclass(frozen=True)
class QuickActionSector:
    index: int
    action: str
    label: str
    icon: str
    start_angle: float
    span_angle: float


@dataclass
class QuickActionWheelState:
    center_x: float = 0.0
    center_y: float = 0.0
    radius: float = 118.0
    dead_zone: float = 38.0
    sector_count: int = 8
    show_labels: bool = True
    label_style: str = "Action"
    icon_style: str = "Initials"
    actions: list[str] = field(default_factory=lambda: list(DEFAULT_ACTIONS))
    custom_labels: list[str] = field(default_factory=lambda: [""] * 8)
    custom_icons: list[str] = field(default_factory=lambda: [""] * 8)
    active: bool = False
    selected_index: int = -1
    progress: float = 0.0
    velocity: float = 0.0
    pointer_x: float = 0.0
    pointer_y: float = 0.0
    opened_at: float = 0.0

    def configure(
        self,
        *,
        sector_count: int | None = None,
        radius: float | None = None,
        dead_zone: float | None = None,
        show_labels: bool | None = None,
        label_style: str | None = None,
        icon_style: str | None = None,
        actions: list[str] | tuple[str, ...] | None = None,
        custom_labels: list[str] | tuple[str, ...] | None = None,
        custom_icons: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        values = {
            "sectors": sector_count, "radius": radius, "dead_zone": dead_zone,
            "show_labels": show_labels, "label_style": label_style, "icon_style": icon_style,
            "actions": list(actions) if actions is not None else None,
            "labels": list(custom_labels) if custom_labels is not None else None,
            "icons": list(custom_icons) if custom_icons is not None else None,
        }
        base = WheelConfiguration().overlay(self.export_config())
        self._apply_configuration(base.overlay({key: value for key, value in values.items() if value is not None}))

    def _apply_configuration(self, config: WheelConfiguration) -> None:
        self.sector_count = config.sectors
        self.radius = config.radius
        self.dead_zone = config.dead_zone
        self.show_labels = config.show_labels
        self.label_style = config.label_style
        self.icon_style = config.icon_style
        self.actions = list(config.actions)
        self.custom_labels = list(config.labels)
        self.custom_icons = list(config.icons)

    def open(
        self,
        pointer_x: float,
        pointer_y: float,
        width: float,
        height: float,
        now: float | None = None,
    ) -> None:
        padding = self.radius + 18.0
        self.center_x = max(padding, min(max(padding, width - padding), float(pointer_x)))
        self.center_y = max(padding, min(max(padding, height - padding), float(pointer_y)))
        self.pointer_x = float(pointer_x)
        self.pointer_y = float(pointer_y)
        self.active = True
        self.selected_index = -1
        self.opened_at = time.perf_counter() if now is None else float(now)

    def cancel(self) -> None:
        self.active = False
        self.selected_index = -1

    def update_pointer(self, pointer_x: float, pointer_y: float) -> int:
        self.pointer_x = float(pointer_x)
        self.pointer_y = float(pointer_y)
        dx = self.pointer_x - self.center_x
        dy = self.pointer_y - self.center_y
        if math.hypot(dx, dy) < self.dead_zone:
            self.selected_index = -1
            return -1
        angle = (math.atan2(dx, -dy) + math.tau) % math.tau
        span = math.tau / self.sector_count
        self.selected_index = int((angle + span * 0.5) // span) % self.sector_count
        return self.selected_index

    def release(self) -> str | None:
        action = None
        sectors = self.sectors()
        if 0 <= self.selected_index < len(sectors):
            action = sectors[self.selected_index].action
        self.active = False
        self.selected_index = -1
        return action

    def update(self, dt: float) -> float:
        target = 1.0 if self.active else 0.0
        stiffness = 300.0
        ratio = 0.62 if self.active else 1.0
        self.progress, self.velocity = step_damped_spring(
            self.progress,
            self.velocity,
            target,
            dt,
            stiffness=stiffness,
            damping_ratio=ratio,
        )
        self.progress = max(0.0, min(1.08, self.progress))
        if abs(target - self.progress) < 0.001 and abs(self.velocity) < 0.01:
            self.progress = target
            self.velocity = 0.0
        return self.progress

    @property
    def visible(self) -> bool:
        return self.active or self.progress > 0.005

    def sectors(self) -> tuple[QuickActionSector, ...]:
        indexes = SECTOR_INDEXES[self.sector_count]
        span = math.tau / self.sector_count
        result = []
        for sector_index, action_index in enumerate(indexes):
            action = self.actions[action_index]
            default_label = ACTION_LABELS.get(action, action)
            if self.label_style == "Compact":
                default_label = default_label.replace(" ", "\n")
            label = "" if self.label_style == "Off" else self.custom_labels[action_index] or default_label
            icon = self.custom_icons[action_index] or ACTION_ICONS.get(action, "")
            if self.icon_style == "Dot":
                icon = ""
            elif self.icon_style == "Off":
                icon = ""
            result.append(
                QuickActionSector(
                    index=sector_index,
                    action=action,
                    label=label,
                    icon=icon,
                    start_angle=-math.pi * 0.5 - span * 0.5 + sector_index * span,
                    span_angle=span,
                )
            )
        return tuple(result)

    def export_config(self) -> dict[str, Any]:
        return {
            "sectors": self.sector_count,
            "radius": round(self.radius, 2),
            "dead_zone": round(self.dead_zone, 2),
            "show_labels": self.show_labels,
            "label_style": self.label_style,
            "icon_style": self.icon_style,
            "actions": list(self.actions),
            "labels": list(self.custom_labels),
            "icons": list(self.custom_icons),
        }

    def load_config(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        base = WheelConfiguration().overlay(self.export_config())
        self._apply_configuration(base.overlay(data))
