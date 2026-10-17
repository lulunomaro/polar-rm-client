"""Launcher page/transition ownership without Qt, rendering, clocks or devices.

The UI adapter sequences fallible presentation effects around these small state
transitions. Announcement precedes the clock/morph work; publication follows it.
Keeping the stages explicit preserves the visible partial state when an effect
raises, rather than silently turning an interrupted transition into success.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StartupNavigation:
    visible: bool
    page: str = field(init=False)
    transition_started: float = 0.0
    transition_from: str | None = None
    transition_to: str | None = None
    page_epoch: int = 0
    static_ready_at: float = 0.0

    def __post_init__(self) -> None:
        self.page = "scenario" if self.visible else "done"

    def allows_destination(self, page: str) -> bool:
        return page in ("scenario", "match", "robot") and page != self.page

    def announce_destination(self, page: str) -> None:
        """Record the outgoing/incoming pair after old video is discarded."""
        self.transition_from = self.page
        self.transition_to = page

    def activate_destination(self, page: str) -> None:
        """Publish the interactive page after the morph accepted its target."""
        self.page = page
        self.page_epoch += 1

    def defer_static(self, duration: float) -> None:
        self.static_ready_at = self.transition_started + duration

    def finish_transition(self) -> bool:
        """Retire the pair once; the adapter then clears its GL scene caches."""
        if self.transition_from is None and self.transition_to is None:
            return False
        self.transition_from = None
        self.transition_to = None
        self.page_epoch += 1
        return True

    def apply_autostart(self, environment_value: str) -> None:
        if environment_value.lower() in ("1", "true", "yes"):
            self.visible = False
            self.page = "done"

    @staticmethod
    def page_after_scenario(second_step_enabled: bool) -> str:
        return "match" if second_step_enabled else "robot"

    @staticmethod
    def page_before_robot(second_step_enabled: bool) -> str:
        return "match" if second_step_enabled else "scenario"


class StartupNavigationField:
    """Fixed compatibility fields backed by this single navigation owner."""
    def __init__(self, field_name: str) -> None:
        if field_name not in StartupNavigation.__dataclass_fields__:
            raise ValueError(f"Unknown startup navigation field: {field_name}")
        self.field_name = field_name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance._startup_navigation, self.field_name)

    def __set__(self, instance, value):
        setattr(instance._startup_navigation, self.field_name, value)
