"""Bounded GPU attempts between ClickGUI layout and Qt painting.

A rejected frame selects a compatibility pass for that frame only. The user's
renderer preference and the layout's interaction geometry are not owned here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable, Protocol

from ..render_diagnostics import RenderFault as PresentationFault, RenderFaultDiagnostics


_LOG = logging.getLogger(__name__)


class NativePainter(Protocol):
    def beginNativePainting(self) -> None: ...
    def endNativePainting(self) -> None: ...


class ClickGuiRenderer(Protocol):
    last_error: str

    def prepare_frame(self, commands: Any, pixel_ratio: float) -> None: ...

    def render(
        self, functions: Any, commands: Any, width: int, height: int,
        device_pixel_ratio: float, *, atlas_prepared: bool,
    ) -> bool: ...


@dataclass(frozen=True)
class ClickGuiViewport:
    width: int
    height: int
    pixel_ratio: float


@dataclass
class PresentationDiagnostics(RenderFaultDiagnostics):
    # Faults count exceptions escaping renderer calls. Exceptions caught inside
    # ClickGuiGpuRenderer remain observable in that renderer's diagnostics.
    rejected_frames: int = 0
    successful_frames: int = 0
    consecutive_failed_frames: int = 0

    def presented(self, success: bool) -> None:
        if success:
            self.successful_frames += 1
            self.consecutive_failed_frames = 0
        else:
            self.rejected_frames += 1
            self.consecutive_failed_frames += 1


@dataclass(frozen=True)
class ClickGuiGpuPresenter:
    renderer: ClickGuiRenderer
    restore_texture_state: Callable[..., None]
    diagnostics: PresentationDiagnostics = field(default_factory=PresentationDiagnostics)
    logger: logging.Logger = _LOG
    clock: Callable[[], float] = time.perf_counter

    def present(
        self, painter: NativePainter, functions: Any, commands: Any,
        viewport: ClickGuiViewport, *, atlas_prepared: bool = False,
    ) -> bool:
        """Try the existing atlas, then rebuild it once without rebuilding layout."""
        if functions is not None:
            for attempt in range(2):
                if self._attempt(
                    painter, functions, commands, viewport,
                    prepare=not atlas_prepared or attempt > 0,
                ):
                    self.diagnostics.presented(True)
                    return True
        self.diagnostics.presented(False)
        return False

    def _attempt(
        self, painter: NativePainter, functions: Any, commands: Any,
        viewport: ClickGuiViewport, *, prepare: bool,
    ) -> bool:
        phase = "atlas.prepare"
        native_painting = False
        rendered = False
        try:
            if prepare:
                self.renderer.prepare_frame(commands, viewport.pixel_ratio)
            phase = "painter.begin"
            painter.beginNativePainting()
            native_painting = True
            # Qt uploads can leave a nonzero texture unit active. Reset it in
            # every attempt, after entering the native-painting boundary.
            phase = "texture.restore"
            self.restore_texture_state(functions, clear_bindings=True)
            phase = "commands.render"
            rendered = bool(self.renderer.render(
                functions, commands, viewport.width, viewport.height,
                viewport.pixel_ratio, atlas_prepared=True,
            ))
        except Exception as exc:
            self._record_fault(phase, exc)
        finally:
            if native_painting:
                try:
                    painter.endNativePainting()
                except Exception as exc:
                    rendered = False
                    self._record_fault("painter.end", exc)
        return rendered

    def _record_fault(self, phase: str, exc: Exception) -> None:
        fault = self.diagnostics.record(phase, exc, logger=self.logger, now=self.clock())
        self.renderer.last_error = fault.summary
