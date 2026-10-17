from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Callable


class OverlayKind(str, Enum):
    CLICKGUI = "clickgui"
    ECONOMY = "economy"
    HERO_DEPLOY = "hero_deploy"
    STUDENT = "student"
    CHAT = "chat"
    RADIAL = "radial"


@dataclass(frozen=True)
class TopHudSafeArea:
    """A centered HUD-logical corridor, shared by CS cards and BF6 progress."""

    top: float
    inset: float
    bottom: float


@dataclass
class OverlayCoordinator:
    """Own the input priority and exclusivity of interactive HUD overlays."""

    modal: OverlayKind | None = None
    chat_input: bool = False
    radial_active: bool = False
    on_input_claimed: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def _notify_input_claimed(self) -> None:
        if self.on_input_claimed is not None:
            self.on_input_claimed()

    @staticmethod
    def top_hud_safe_area(
        width: float, height: float, *, top: float, inset: float,
        minecraft_layout: Any = None, hud_scale: float = 1.0,
        dynamic_island: bool = True, tactical_map: bool = True,
    ) -> TopHudSafeArea:
        """Reserve MC chrome without changing its pixels, cache or hit targets.

        MC returns viewport coordinates before the HUD painter's transform.
        Convert once by HUD scale; devicePixelRatio is already handled by Qt.
        Keeping both side insets equal preserves the viewport-centered clock.
        """
        values = (width, height, top, inset, hud_scale)
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or
               not math.isfinite(v) for v in values):
            raise ValueError("top HUD geometry must be finite")
        if min(width, height, hud_scale) <= 0 or min(top, inset) < 0:
            raise ValueError("invalid top HUD geometry")
        bottom = min(height - 12.0, height * .48)
        if minecraft_layout is None:
            return TopHudSafeArea(top, inset, bottom)
        status_top = min(rect.top() for rect in minecraft_layout.armor)
        bottom = min(bottom, (status_top - 8.0 * minecraft_layout.scale) / hud_scale)
        score = minecraft_layout.scoreboard
        if score.bottom() / hud_scale > top and score.top() / hud_scale < bottom:
            inset = max(inset, width - score.left() / hud_scale + 10.0)
        if not tactical_map:
            # The six vanilla effect tiles are drawn only without TacticalMap.
            effects = minecraft_layout.effects_origin
            if (effects.y() + 24.0 * minecraft_layout.scale) / hud_scale > top and effects.y() / hud_scale < bottom:
                inset = max(inset, (effects.x() + 154.0 * minecraft_layout.scale) / hud_scale + 10.0)
        return TopHudSafeArea(top, inset, bottom)

    def request_modal(self, kind: OverlayKind) -> OverlayKind | None:
        previous = self.modal
        changed = self.modal != kind or self.chat_input or self.radial_active
        self.modal = kind
        self.chat_input = False
        self.radial_active = False
        if changed:
            self._notify_input_claimed()
        return previous

    def close_modal(self, kind: OverlayKind | None = None) -> bool:
        if self.modal is None or (kind is not None and self.modal != kind):
            return False
        self.modal = None
        return True

    def begin_chat(self) -> OverlayKind | None:
        previous = self.modal
        changed = not self.chat_input or self.modal is not None or self.radial_active
        self.modal = None
        self.radial_active = False
        self.chat_input = True
        if changed:
            self._notify_input_claimed()
        return previous

    def end_chat(self) -> None:
        self.chat_input = False

    def begin_radial(self) -> bool:
        if self.chat_input or self.modal is not None:
            return False
        changed = not self.radial_active
        self.radial_active = True
        if changed:
            self._notify_input_claimed()
        return True

    def end_radial(self) -> None:
        self.radial_active = False

    @property
    def blocks_combat_input(self) -> bool:
        return self.chat_input or self.modal is not None or self.radial_active
