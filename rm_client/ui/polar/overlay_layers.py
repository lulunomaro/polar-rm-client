"""Cached UI layers over an explicit frame snapshot and borrowed GPU owners.

The window owns context admission, source-specific background/control/vision,
restore generations and final presentation. This module owns only the overlay
and dynamic layer schedule. It cannot read a window or mutate its cache fields.
Draw callbacks adapt retained layout/painter implementations; their documented
outputs are commands or drawing, never a replacement application-state proxy.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
import time
from typing import Any, Callable, Protocol

from ..clickgui_gpu import ClickGuiDrawList
from ..gpu_frame_compositor import GlassOwner, LayerPaintSession
from .clickgui_frame import ClickGuiFrame
from .compositor_plan import OverlayScene, motion_layer_needs_refresh


CacheKey = tuple[str | int | float | bool | None, ...]


def _validate_key(key: CacheKey | None) -> None:
    if key is None:
        return
    if not isinstance(key, tuple):
        raise TypeError("frame cache key must be an immutable tuple")
    for value in key:
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise TypeError("frame cache key values must be immutable scalars")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("frame cache key values must be finite")


@dataclass(frozen=True)
class FrameViewport:
    width: int
    height: int
    pixel_ratio: float

    def __post_init__(self) -> None:
        if any(type(value) is not int or not 0 < value <= 2_147_483_647
               for value in (self.width, self.height)):
            raise ValueError("frame dimensions must be positive Qt integers")
        if (isinstance(self.pixel_ratio, bool)
                or not isinstance(self.pixel_ratio, (int, float)) or self.pixel_ratio <= 0):
            raise ValueError("frame pixel ratio must be finite and positive")
        try:
            if not math.isfinite(self.pixel_ratio):
                raise ValueError("frame pixel ratio must be finite and positive")
            for logical in (self.width, self.height):
                physical = logical * self.pixel_ratio
                if not math.isfinite(physical) or round(physical) > 2_147_483_647:
                    raise ValueError("frame physical dimensions exceed Qt integer storage")
        except OverflowError as error:
            raise ValueError("frame physical dimensions exceed Qt integer storage") from error


@dataclass(frozen=True)
class LayerCacheSnapshot:
    """Published metadata; a render attempt works on a replacement value."""

    static_key: CacheKey | None
    startup_key: CacheKey | None
    motion_ready: bool
    motion_updated_at: float

    def __post_init__(self) -> None:
        _validate_key(self.static_key)
        _validate_key(self.startup_key)
        if type(self.motion_ready) is not bool:
            raise TypeError("motion cache readiness must be boolean")
        if (isinstance(self.motion_updated_at, bool)
                or not isinstance(self.motion_updated_at, (int, float))):
            raise ValueError("motion cache timestamp must be finite")
        try:
            if not math.isfinite(self.motion_updated_at):
                raise ValueError("motion cache timestamp must be finite")
        except OverflowError as error:
            raise ValueError("motion cache timestamp must be finite") from error


@dataclass(frozen=True)
class MotionLayerInputs:
    radial_visible: bool
    chat_open: bool
    student_visible: bool
    escape_visible: bool

    def __post_init__(self) -> None:
        values = (self.radial_visible, self.chat_open, self.student_visible, self.escape_visible)
        if any(type(value) is not bool for value in values):
            raise TypeError("motion layer visibility must be boolean")

    @property
    def priority_active(self) -> bool:
        return self.radial_visible or self.chat_open or self.student_visible or self.escape_visible

    def needs_refresh(self, cache: LayerCacheSnapshot, now: float) -> bool:
        return motion_layer_needs_refresh(
            radial_visible=self.radial_visible, chat_open=self.chat_open,
            student_visible=self.student_visible, escape_visible=self.escape_visible,
            ready=cache.motion_ready, now=now, updated_at=cache.motion_updated_at,
        )


class StaticLayerMode(str, Enum):
    STARTUP_STABLE = "startup_stable"
    STARTUP_TRANSITION = "startup_transition"
    ECONOMY = "economy"
    HUD_REBUILD = "hud_rebuild"
    REUSE = "reuse"


@dataclass(frozen=True)
class OverlayLayerPlan:
    viewport: FrameViewport
    storage_generation: int
    scene: OverlayScene
    static_mode: StaticLayerMode
    static_key: CacheKey
    startup_key: CacheKey | None
    economy_gpu: bool
    prepare_clickgui: bool
    motion: MotionLayerInputs

    def __post_init__(self) -> None:
        if type(self.storage_generation) is not int or self.storage_generation < 0:
            raise ValueError("frame storage generation must be a non-negative integer")
        if not isinstance(self.viewport, FrameViewport):
            raise TypeError("frame viewport must be validated")
        if not isinstance(self.scene, OverlayScene) or not isinstance(self.motion, MotionLayerInputs):
            raise TypeError("frame scene and motion inputs must be immutable snapshots")
        scene_flags = (self.scene.economy_active, self.scene.clickgui_active,
                       self.scene.student_active, self.scene.escape_active)
        if any(type(value) is not bool for value in scene_flags):
            raise TypeError("frame scene ownership must be boolean")
        if not isinstance(self.static_mode, StaticLayerMode):
            raise TypeError("frame static mode must be explicit")
        if type(self.economy_gpu) is not bool or type(self.prepare_clickgui) is not bool:
            raise TypeError("frame renderer selection must be boolean")
        if self.static_key is None:
            raise ValueError("frame requires its static cache key")
        _validate_key(self.static_key)
        _validate_key(self.startup_key)
        startup = self.static_mode in (StaticLayerMode.STARTUP_STABLE, StaticLayerMode.STARTUP_TRANSITION)
        if startup and (any(scene_flags) or self.prepare_clickgui):
            raise ValueError("startup owns the frame without modal overlay layers")
        if (self.static_mode is StaticLayerMode.ECONOMY) != self.scene.economy_active:
            raise ValueError("economy scene and static-layer ownership must agree")
        if self.static_mode is StaticLayerMode.STARTUP_STABLE and self.startup_key is None:
            raise ValueError("stable startup frame requires its page cache key")
        if self.static_mode is not StaticLayerMode.STARTUP_STABLE and self.startup_key is not None:
            raise ValueError("only stable startup owns a page cache key")
        if self.economy_gpu and not self.scene.economy_active:
            raise ValueError("economy commands require economy layer ownership")
        if self.scene.economy_active and self.prepare_clickgui:
            raise ValueError("exclusive economy frame cannot prepare ClickGUI commands")
        if self.prepare_clickgui and not self.scene.clickgui_active:
            raise ValueError("ClickGUI preparation requires an active ClickGUI scene")


@dataclass(frozen=True)
class PreparedClickGui:
    frame: ClickGuiFrame | None = None

    @property
    def commands(self) -> ClickGuiDrawList | None:
        return None if self.frame is None else self.frame.commands

    @property
    def atlas_prepared(self) -> bool:
        return self.frame is not None


@dataclass(frozen=True)
class OverlayDrawPorts:
    """The retained layouts and painters, with no access to cache metadata.

    Startup layout retains its historical hotspot publication. ClickGUI layout
    returns its isolated command/interaction frame; only its paint adapter may
    publish that frame. Atlas and FBO resource lifetimes stay with the executor.
    """

    clear_startup_interactions: Callable[[], None]
    startup_commands: Callable[[], ClickGuiDrawList]
    startup_interaction: Callable[[bool], bool]
    static_hud: Callable[[Any], None]
    economy_commands: Callable[[], ClickGuiDrawList]
    clickgui_frame: Callable[[], ClickGuiFrame]
    motion_hud: Callable[[LayerPaintSession, PreparedClickGui], bool]
    priority_interaction: Callable[[], bool]
    target_hud: Callable[[], bool]


class LayerCompositor(Protocol):
    fbo_generation: int
    frame_rejected: bool
    last_dynamic_ms: float

    def clear_static_glass(self) -> None: ...
    def begin_static_glass(self) -> None: ...
    def end_static_glass(self) -> None: ...
    def push_glass_owner(self, owner: GlassOwner) -> None: ...
    def pop_glass_owner(self) -> None: ...
    def paint_device(self, layer: str) -> Any: ...
    def clear_current(self, functions: Any, *, transparent: bool) -> None: ...
    def release_layer(self, layer: str) -> bool: ...
    def clear_target_layer(self, functions: Any) -> bool: ...
    def paint_layer(
        self, functions: Any, layer: str, ratio: float,
        render: Callable[[LayerPaintSession], bool],
    ) -> bool: ...


class CommandRenderer(Protocol):
    def prepare_frame(self, commands: ClickGuiDrawList, ratio: float) -> None: ...
    def render(
        self, functions: Any, commands: ClickGuiDrawList, width: int,
        height: int, ratio: float, *, atlas_prepared: bool,
    ) -> bool: ...


@dataclass(frozen=True)
class OverlayLayerRenderer:
    """Borrow frame resources without retaining a window or prepared frame.

    A result exists only after all requested layers accepted and the storage
    generation still matches. The caller publishes that replacement metadata,
    then presents; any later rejection invalidates it at the frame boundary.
    Existing compositor recovery owns exceptional whole-frame unwinding.
    """

    compositor: LayerCompositor
    command_renderer: CommandRenderer
    clock: Callable[[], float] = time.perf_counter

    def render(
        self, functions: Any, plan: OverlayLayerPlan, cache: LayerCacheSnapshot,
        draws: OverlayDrawPorts,
    ) -> LayerCacheSnapshot | None:
        if self.compositor.fbo_generation != plan.storage_generation:
            return None
        updated = self._static_layer(functions, plan, cache, draws)
        if updated is None or self.compositor.fbo_generation != plan.storage_generation:
            return None
        if plan.static_mode in (StaticLayerMode.STARTUP_STABLE, StaticLayerMode.STARTUP_TRANSITION):
            accepted = draws.startup_interaction(plan.static_mode is StaticLayerMode.STARTUP_STABLE)
        elif plan.economy_gpu:
            accepted = self._economy_layer(functions, plan, draws)
        else:
            updated = self._motion_layers(functions, plan, updated, draws)
            accepted = updated is not None
        if (not accepted or self.compositor.frame_rejected
                or self.compositor.fbo_generation != plan.storage_generation):
            return None
        return updated

    def _static_layer(
        self, functions: Any, plan: OverlayLayerPlan, cache: LayerCacheSnapshot,
        draws: OverlayDrawPorts,
    ) -> LayerCacheSnapshot | None:
        mode = plan.static_mode
        if mode is StaticLayerMode.STARTUP_STABLE:
            if plan.startup_key != cache.startup_key:
                draws.clear_startup_interactions()
                self.compositor.clear_static_glass()
                self.compositor.begin_static_glass()
                commands = draws.startup_commands()
                self.compositor.end_static_glass()
                self.command_renderer.prepare_frame(commands, plan.viewport.pixel_ratio)
                if self.compositor.fbo_generation != plan.storage_generation:
                    return None
                if not self._command_layer(functions, "overlay", plan.viewport, commands, set_ratio=False):
                    return None
                cache = replace(cache, startup_key=plan.startup_key)
            return replace(cache, static_key=plan.static_key)
        if mode is StaticLayerMode.STARTUP_TRANSITION:
            self.compositor.clear_static_glass()
            if not self._clear_overlay(functions, plan.viewport, set_ratio=False):
                return None
            return replace(cache, startup_key=None, static_key=plan.static_key)
        if mode is StaticLayerMode.ECONOMY:
            if not self._clear_overlay(functions, plan.viewport, set_ratio=True):
                return None
            return replace(cache, static_key=plan.static_key)
        if mode is StaticLayerMode.HUD_REBUILD:
            def draw_static(session: LayerPaintSession) -> bool:
                self.compositor.push_glass_owner(GlassOwner.HUD)
                session.defer_cleanup("glass_owner", self.compositor.pop_glass_owner)
                self.compositor.begin_static_glass()
                session.defer_cleanup("static_glass", self.compositor.end_static_glass)
                draws.static_hud(session.begin_painter())
                return True

            if not self.compositor.paint_layer(functions, "overlay", plan.viewport.pixel_ratio, draw_static):
                return None
            return replace(cache, static_key=plan.static_key)
        return cache

    def _clear_overlay(self, functions: Any, viewport: FrameViewport, *, set_ratio: bool) -> bool:
        device = self.compositor.paint_device("overlay")
        if device is None:
            return False
        if set_ratio:
            device.setDevicePixelRatio(viewport.pixel_ratio)
        self.compositor.clear_current(functions, transparent=True)
        return self.compositor.release_layer("overlay") is not False

    def _command_layer(
        self, functions: Any, layer: str, viewport: FrameViewport,
        commands: ClickGuiDrawList, *, set_ratio: bool,
    ) -> bool:
        device = self.compositor.paint_device(layer)
        if device is None:
            return False
        if set_ratio:
            device.setDevicePixelRatio(viewport.pixel_ratio)
        self.compositor.clear_current(functions, transparent=True)
        accepted = self.command_renderer.render(
            functions, commands, viewport.width, viewport.height,
            viewport.pixel_ratio, atlas_prepared=True,
        )
        # Preserve the startup pass's rejection boundary: whole-frame recovery
        # releases it; economy releases its dynamic pass before reporting False.
        if not accepted and layer == "overlay":
            return False
        released = self.compositor.release_layer(layer) is not False
        return bool(accepted and released)

    def _economy_layer(
        self, functions: Any, plan: OverlayLayerPlan, draws: OverlayDrawPorts,
    ) -> bool:
        viewport = plan.viewport
        self.compositor.push_glass_owner(GlassOwner.ECONOMY)
        try:
            commands = draws.economy_commands()
        finally:
            self.compositor.pop_glass_owner()
        self.command_renderer.prepare_frame(commands, viewport.pixel_ratio)
        if self.compositor.fbo_generation != plan.storage_generation:
            return False
        if not self._command_layer(functions, "dynamic", viewport, commands, set_ratio=True):
            return False
        return self.compositor.clear_target_layer(functions)

    def _prepare_clickgui(self, plan: OverlayLayerPlan, draws: OverlayDrawPorts) -> PreparedClickGui:
        if not plan.prepare_clickgui:
            return PreparedClickGui()
        self.compositor.push_glass_owner(GlassOwner.CLICKGUI)
        try:
            frame = draws.clickgui_frame()
        finally:
            self.compositor.pop_glass_owner()
        self.command_renderer.prepare_frame(frame.commands, plan.viewport.pixel_ratio)
        return PreparedClickGui(frame)

    def _motion_layers(
        self, functions: Any, plan: OverlayLayerPlan, cache: LayerCacheSnapshot,
        draws: OverlayDrawPorts,
    ) -> LayerCacheSnapshot | None:
        prepared = self._prepare_clickgui(plan, draws)
        if self.compositor.fbo_generation != plan.storage_generation:
            return None
        now = self.clock()
        if plan.motion.needs_refresh(cache, now):
            started = self.clock()
            if not self.compositor.paint_layer(
                functions, "dynamic", plan.viewport.pixel_ratio,
                lambda session: draws.motion_hud(session, prepared),
            ):
                return None
            cache = replace(cache, motion_ready=True, motion_updated_at=now)
            self.compositor.last_dynamic_ms = (self.clock() - started) * 1000.0
        if self.compositor.fbo_generation != plan.storage_generation:
            return None
        if plan.motion.priority_active:
            accepted = draws.priority_interaction()
        elif plan.scene.modal_backdrop_active:
            accepted = self.compositor.clear_target_layer(functions)
        else:
            accepted = draws.target_hud()
        return cache if accepted else None
