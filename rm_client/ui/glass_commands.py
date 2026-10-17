"""Glass command lifetime and per-command geometry, independent of GL state.

The buffer owns collection membership and recording scope. Commands retain their
original identity: QRectF payloads remain live until each draw consumes them.
Native submission, painter cleanup and cache-commit decisions stay with callers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtCore import QSize
    from .gpu_frame_compositor import GlassCommand, GlassOwner


class GlassCommandBuffer:
    def __init__(self, default_owner: GlassOwner) -> None:
        self._default_owner = default_owner
        self.static: list[GlassCommand] = []
        self.frame: list[GlassCommand] = []
        self.recording_static = False
        self.owners = [default_owner]

    @property
    def owner(self) -> GlassOwner:
        return self.owners[-1]

    def begin_frame(self, *, rebuild_static_ui: bool) -> None:
        self.frame.clear()
        self._reset_recording_scope()
        if rebuild_static_ui:
            self.static.clear()

    def invalidate(self) -> None:
        self.static.clear()
        self.frame.clear()
        self._reset_recording_scope()

    def _reset_recording_scope(self) -> None:
        self.recording_static = False
        self.owners[:] = [self._default_owner]

    def clear_static(self) -> None:
        self.static.clear()

    def clear_frame(self) -> None:
        self.frame.clear()

    def begin_static(self) -> None:
        self.recording_static = True

    def end_static(self) -> None:
        self.recording_static = False

    def push_owner(self, owner: GlassOwner) -> None:
        self.owners.append(owner)

    def pop_owner(self) -> None:
        if len(self.owners) > 1:
            self.owners.pop()

    def append(self, command: GlassCommand) -> None:
        target = self.static if self.recording_static else self.frame
        target.append(command)

    def select(
        self, *, include_static: bool,
        owners: set[GlassOwner] | frozenset[GlassOwner] | None,
    ) -> tuple[GlassCommand, ...]:
        # Freeze only membership, at the caller's original selection point.
        # Copying the commands/rectangles would hide edits made by earlier draws.
        commands = (*self.static, *self.frame) if include_static else tuple(self.frame)
        if owners is not None:
            commands = tuple(command for command in commands if command.owner in owners)
        return commands


@dataclass(frozen=True)
class GlassDrawGeometry:
    rect: tuple[float, float, float, float]
    scissor: tuple[int, int, int, int]


def glass_draw_geometry(command: GlassCommand, size: QSize, ratio: float) -> GlassDrawGeometry | None:
    """Evaluate one current command immediately before its scissor submission.

    Preserve the compositor's existing threshold, clip and integer rounding
    equations, including out-of-viewport behavior. This is not validation or a
    whole-frame snapshot; material uniforms are still read at submission time.
    """
    x = command.rect.left() * ratio
    y = size.height() - command.rect.bottom() * ratio
    w = command.rect.width() * ratio
    h = command.rect.height() * ratio
    if w <= 0.5 or h <= 0.5:
        return None
    clip = command.rect
    if command.clip_rect is not None:
        clip = clip.intersected(command.clip_rect)
    if clip.isEmpty():
        return None
    clip_x = clip.left() * ratio
    clip_y = size.height() - clip.bottom() * ratio
    clip_w = clip.width() * ratio
    clip_h = clip.height() * ratio
    sx = max(0, int(clip_x))
    sy = max(0, int(clip_y))
    sw = min(size.width() - sx, max(1, int(clip_w + 0.999)))
    sh = min(size.height() - sy, max(1, int(clip_h + 0.999)))
    return GlassDrawGeometry((x, y, w, h), (sx, sy, sw, sh))
