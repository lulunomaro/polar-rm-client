"""The compositor's framebuffer set, with one ordered lifetime definition.

No context is created here. The caller must retire storage with its owning GL
context current and contains allocation/validation faults at the render boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class FramebufferSpec:
    name: str
    painter: bool = False
    required_for_reuse: bool = True


# Order is intentional: both construction and native-wrapper retirement follow
# the established compositor lifecycle. Scratch buffers historically do not
# participate in same-size reuse admission; keep that policy independent of
# validation, which checks every freshly allocated buffer.
FRAMEBUFFERS = (
    FramebufferSpec("background_fbo", painter=True),
    FramebufferSpec("control_fbo", painter=True),
    FramebufferSpec("blur_temp_fbo", required_for_reuse=False),
    FramebufferSpec("blur_fbo"),
    FramebufferSpec("treated_blur_temp_fbo", required_for_reuse=False),
    FramebufferSpec("treated_blur_fbo"),
    FramebufferSpec("hud_glass_fbo"),
    FramebufferSpec("glass_fbo"),
    FramebufferSpec("overlay_fbo", painter=True),
    FramebufferSpec("vision_fbo"),
    FramebufferSpec("dynamic_fbo", painter=True),
    # Isolate the rasterized TargetHUD ring from Qt's shared dynamic atlas.
    FramebufferSpec("target_fbo", painter=True),
)


class FramebufferStorage:
    """Own every successfully constructed wrapper, including a partial set."""

    def __init__(self) -> None:
        self.handles = {spec.name: None for spec in FRAMEBUFFERS}

    def reusable(self) -> bool:
        return all(
            self.handles[spec.name] is not None
            for spec in FRAMEBUFFERS if spec.required_for_reuse
        )

    def allocate(self, size, *, create: Callable, shader_format, painter_format) -> None:
        # Publish ownership immediately, not after the whole batch succeeds.
        # If creation/validation raises, the compositor's rejection cleanup
        # can retire exactly the prefix already constructed.
        for spec in FRAMEBUFFERS:
            self.handles[spec.name] = create(
                size, painter_format if spec.painter else shader_format,
            )
        if not all(self.handles[spec.name].isValid() for spec in FRAMEBUFFERS):
            raise RuntimeError("GPU compositor FBO creation failed")

    def clear(self) -> None:
        # Do not replace the dictionary: explicitly retire in declaration order.
        for spec in FRAMEBUFFERS:
            self.handles[spec.name] = None


def framebuffer_slot(name: str) -> property:
    """Keep the compositor's existing read/write FBO API backed by one owner."""
    def get(compositor):
        return compositor._framebuffers.handles[name]

    def set_(compositor, value):
        compositor._framebuffers.handles[name] = value

    return property(get, set_)
