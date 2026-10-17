"""Frame-local overlay decisions and the ordered final composition boundary.

Policy consumes values, not a window. The final pass knows only its compositor
and an immutable plan, so ownership and failure ordering can be tested without
allocating a framebuffer or reproducing the widget's entire mutable state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..gpu_frame_compositor import GlassOwner


@dataclass(frozen=True)
class OverlayScene:
    economy_active: bool
    clickgui_active: bool
    student_active: bool
    escape_active: bool

    @classmethod
    def resolve(
        cls, *, startup_scene: bool, economy_visible: bool,
        economy_progress: float, clickgui_visible: bool,
        clickgui_progress: float, student_visible: bool, escape_visible: bool,
    ) -> OverlayScene:
        if startup_scene:
            return cls(False, False, False, False)
        return cls(
            economy_progress > 0.01 or economy_visible,
            clickgui_progress > 0.01 or clickgui_visible,
            student_visible, escape_visible,
        )

    @property
    def modal_backdrop_active(self) -> bool:
        return self.economy_active or self.clickgui_active or self.student_active or self.escape_active

    def glass_owners(self, *, radial_visible: bool, hero_visible: bool) -> frozenset[GlassOwner]:
        return frozenset(owner for active, owner in (
            (self.economy_active, GlassOwner.ECONOMY),
            (self.clickgui_active, GlassOwner.CLICKGUI),
            (radial_visible, GlassOwner.RADIAL),
            (hero_visible, GlassOwner.HERO_DEPLOY),
        ) if active)


class BackdropCompositor(Protocol):
    def set_backdrop_treatment(self, **kwargs: Any) -> bool: ...


@dataclass(frozen=True)
class BackdropTreatment:
    active: bool
    progress: float
    blur_radius_px: float
    dim_alpha: float
    include_overlay: bool
    blur_kernel: str

    @classmethod
    def resolve(
        cls, scene: OverlayScene, *, economy_progress: float,
        clickgui_progress: float, jello_blur_radius: float | None,
        student_panel: str, student_progress: float,
    ) -> BackdropTreatment:
        economy_reveal = max(0.0, min(1.0, economy_progress)) if scene.economy_active else 0.0
        clickgui_reveal = max(0.0, min(1.0, clickgui_progress)) if scene.clickgui_active else 0.0
        if scene.economy_active:
            return cls(economy_reveal > 0.0, economy_reveal, 4.0 * economy_reveal, .40, False, "compact_gaussian")
        if scene.clickgui_active and jello_blur_radius is not None:
            # Sigma's own black overlay must not receive a second dim pass.
            active = clickgui_reveal > 0.0
            return cls(active, 1.0 if active else 0.0, jello_blur_radius, 0.0, True, "jello_box20")
        if scene.escape_active:
            return cls(True, 1.0, 20.0, 0.0, True, "jello_box20")
        if scene.student_active:
            blur = 0.0 if student_panel == "damage" else 20.0 * min(1.0, student_progress * 4.0)
            return cls(True, 1.0, blur, 0.0, True, "jello_box20")
        return cls(clickgui_reveal > 0.0, clickgui_reveal, 3.0, .26, True, "compact_gaussian")

    def apply(self, compositor: BackdropCompositor) -> bool:
        return compositor.set_backdrop_treatment(
            active=self.active, progress=self.progress,
            blur_radius_px=self.blur_radius_px, dim_alpha=self.dim_alpha,
            include_overlay=self.include_overlay, blur_kernel=self.blur_kernel,
        )


def motion_layer_needs_refresh(
    *, radial_visible: bool, chat_open: bool, student_visible: bool,
    escape_visible: bool, ready: bool, now: float, updated_at: float,
) -> bool:
    priority_overlay = radial_visible or chat_open or student_visible or escape_visible
    # Wheel/student views freeze the base. Chat and Escape refresh it at 4 Hz
    # while their interaction layer continues at the display refresh rate.
    interval = .25 if (chat_open or escape_visible) and not radial_visible else float("inf")
    return not priority_overlay or not ready or now - updated_at >= interval


@dataclass(frozen=True)
class FrameCompositionPlan:
    background_changed: bool
    control_changed: bool
    treatment_changed: bool
    static_ui_rebuilt: bool
    overlay_owners: frozenset[GlassOwner]
    target_framebuffer: int

    @property
    def base_blur_dirty(self) -> bool:
        return self.background_changed or self.control_changed

    @property
    def treated_backdrop_dirty(self) -> bool:
        return self.base_blur_dirty or self.treatment_changed or self.static_ui_rebuilt


class FrameCompositor(Protocol):
    def prepare_blur(self, functions: Any, *, background_changed: bool) -> bool: ...
    def render_glass(self, functions: Any, **kwargs: Any) -> bool: ...
    def prepare_treated_backdrop(self, functions: Any, *, background_changed: bool) -> bool: ...
    def present(self, functions: Any, target_framebuffer: int) -> bool: ...


@dataclass(frozen=True)
class FinalFrameCompositor:
    compositor: FrameCompositor

    def present(self, functions: Any, plan: FrameCompositionPlan) -> bool:
        if not self.compositor.prepare_blur(functions, background_changed=plan.base_blur_dirty):
            return False
        # HUD lenses sample scene/control blur, never the treated modal image.
        if not self.compositor.render_glass(
            functions, include_static=True, owners={GlassOwner.HUD},
            destination="hud", apply_backdrop_treatment=False,
        ):
            return False
        if not self.compositor.prepare_treated_backdrop(
            functions, background_changed=plan.treated_backdrop_dirty,
        ):
            return False
        if not self.compositor.render_glass(
            functions, include_static=True, owners=set(plan.overlay_owners),
            destination="overlay",
        ):
            return False
        return self.compositor.present(functions, plan.target_framebuffer)
