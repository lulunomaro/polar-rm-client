"""Explicit draw/interaction result at the legacy ClickGUI builder boundary.

Jello and the retained builders still register geometry through their host.
Keep that compatibility protocol inside one synchronous adapter: preparing a
frame must not publish half-built hit targets or overwrite a different layer's
targets. The compositor publishes the matching geometry when it uses the draw
list. This isolates interaction output, not animation or compositor state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..clickgui_gpu import ClickGuiDrawList
    from .primitives import Hotspot


@dataclass(frozen=True)
class ClickGuiFrame:
    """One layout pass and its ordered pointer/wheel targets.

    Tuple containers keep later list clear/append operations from changing a
    prepared frame. Payloads deliberately retain identity (e.g. Setting), just
    like draw-list payloads; this is not a deep copy of application state.
    """

    commands: ClickGuiDrawList
    hotspots: tuple[Hotspot, ...]
    scroll_viewports: tuple[dict[str, Any], ...]

    def publish_interactions(self, host: Any) -> None:
        """Replace targets for the modal layer; a closing GUI is display-only."""

        visible = bool(host.gui_visible)
        host.hotspots[:] = self.hotspots if visible else ()
        host.gui_scroll_viewports = list(self.scroll_viewports) if visible else []


_MISSING = object()


def build_clickgui_frame(host: Any, width: int, height: int, alpha: float) -> ClickGuiFrame:
    """Capture legacy builder writes without publishing interaction geometry.

    Call on the UI thread, outside an event-loop yield. Existing direct calls
    to ``host.build_gpu_clickgui`` remain supported and retain their mutation
    semantics. New frame consumers need neither a global hotspot snapshot nor
    a second layout pass to reconstruct interaction targets.
    """

    previous_hotspots = host.hotspots
    previous_viewports = host.gui_scroll_viewports
    previous_clip = getattr(host, "_hotspot_clip", _MISSING)
    host.hotspots = []
    host.gui_scroll_viewports = []
    host._hotspot_clip = None
    try:
        commands = host.build_gpu_clickgui(width, height, alpha)
        # Builders also enforce this, but capture the closed-frame invariant at
        # the boundary so a compatibility builder cannot leak stale targets.
        visible = bool(host.gui_visible)
        return ClickGuiFrame(
            commands=commands,
            hotspots=tuple(host.hotspots) if visible else (),
            scroll_viewports=tuple(host.gui_scroll_viewports) if visible else (),
        )
    finally:
        host.hotspots = previous_hotspots
        host.gui_scroll_viewports = previous_viewports
        if previous_clip is _MISSING:
            if hasattr(host, "_hotspot_clip"):
                del host._hotspot_clip
        else:
            host._hotspot_clip = previous_clip
