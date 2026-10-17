"""Narrow, Qt-free ownership boundaries for specialized native render passes.

The renderer owns the native objects and their GL context. This module owns
only pass-local admissions, independent unwind attempts and bounded evidence.
It never creates a context or retries a failed driver operation on a frame.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import logging
import time
from typing import Callable, Iterable

from .render_diagnostics import RenderFault, RenderFaultDiagnostics

Cleanup = tuple[str, Callable[[], object]]
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class GlOperationResult:
    accepted: bool
    rejection: str = ""
    primary_fault: RenderFault | None = None
    cleanup_faults: tuple[RenderFault, ...] = ()

    @property
    def error(self) -> str:
        primary = self.primary_fault or next(iter(self.cleanup_faults), None)
        return primary.summary if primary is not None else self.rejection


class GlResourceOwner:
    """Keep the latest result and throttled typed traces, never frame history."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.diagnostics = RenderFaultDiagnostics()
        self.last_result = GlOperationResult(True)
        self.release_pending = False

    @property
    def last_error(self) -> str:
        return self.last_result.error

    def _record(self, phase: str, error: Exception) -> RenderFault:
        # Names come from fixed renderer stages. Bound even dynamically supplied
        # exception types so long-running recovery cannot grow this cache.
        cache = self.diagnostics.last_reported_at
        key = (phase, type(error).__name__)
        if key not in cache and len(cache) >= 64:
            del cache[min(cache, key=cache.get)]
        return self.diagnostics.record(phase, error, logger=_LOG, now=time.perf_counter())

    def _clean(self, phase: str, actions: Iterable[Cleanup]) -> tuple[tuple[RenderFault, ...], tuple[Exception, ...]]:
        faults, errors = [], []
        for name, action in actions:
            try:
                # These are Qt void release/destroy/state APIs. None is success;
                # they are not FBO bool-returning bind/release contracts.
                action()
            except Exception as error:
                faults.append(self._record(f"{self.name}.{phase}.cleanup.{name}", error))
                errors.append(error)
        return tuple(faults), tuple(errors)

    def run(
        self, phase: str, action: Callable[[], bool], *,
        cleanup: Callable[[], Iterable[Cleanup]] = tuple,
        rejection: Callable[[], str] = str,
        on_reject: Callable[[], Iterable[Cleanup]] = tuple,
        reraise: bool = False,
    ) -> bool:
        primary = None
        operation_error = None
        try:
            accepted = bool(action())
        except Exception as error:
            accepted = False
            operation_error = error
            primary = self._record(f"{self.name}.{phase}", error)
        reason = (rejection() or f"{self.name}.{phase} rejected") if not accepted and primary is None else ""
        cleanup_faults, cleanup_errors = self._clean(phase, cleanup())
        if not accepted or cleanup_faults:
            rejected_actions = tuple(on_reject())
            rejected_faults, rejected_errors = self._clean(phase, rejected_actions)
            cleanup_faults += rejected_faults
            cleanup_errors += rejected_errors
            if rejected_actions:
                self.release_pending = bool(rejected_faults)
        self.last_result = GlOperationResult(accepted and not cleanup_faults, reason, primary, cleanup_faults)
        if reraise:
            if operation_error is not None:
                raise operation_error
            if cleanup_errors:
                raise cleanup_errors[0]
        return self.last_result.accepted

    def release(self, actions: Iterable[Cleanup]) -> None:
        """Attempt all owners, then preserve the first original raised defect."""
        faults, errors = self._clean("release", actions)
        self.release_pending = bool(errors)
        self.last_result = GlOperationResult(not faults, cleanup_faults=faults)
        if errors:
            raise errors[0]


def destroy_created(resource) -> None:
    """Destroy a reusable Qt buffer/VAO wrapper only if its native object exists."""
    if resource.isCreated():
        resource.destroy()


class GlDrawBindings:
    """Own successful bindings until one pass ends, in existing release order."""

    def __init__(self, program, buffer, vertex_array, *, restore: tuple[Cleanup, ...] = ()) -> None:
        self.program = program
        self.buffer = buffer
        self.vertex_array = vertex_array
        self.restore = restore
        self.rejection = ""
        self._program_bound = False
        self._buffer_bound = False
        self._array_bound = False
        self._attributes: list[int] = []
        self._textures: list[tuple[object, int]] = []

    def bind_program(self) -> bool:
        # A wrapper may raise after native side effects. Admit the cleanup
        # attempt first; an ordinary bool rejection explicitly cancels it.
        self._program_bound = True
        self._program_bound = bool(self.program.bind())
        if not self._program_bound:
            self.rejection = "program.bind rejected"
        return self._program_bound

    def bind_buffer(self) -> bool:
        self._buffer_bound = True
        self._buffer_bound = bool(self.buffer.bind())
        if not self._buffer_bound:
            self.rejection = "buffer.bind rejected"
        return self._buffer_bound

    def bind_vertex_array(self) -> None:
        self._array_bound = True
        self.vertex_array.bind()

    def bind_texture(self, texture, unit: int) -> None:
        self._textures.append((texture, unit))
        texture.bind(unit)

    def enable_attribute(self, location: int) -> None:
        self._attributes.append(location)
        self.program.enableAttributeArray(location)

    def cleanup(self) -> Iterable[Cleanup]:
        for location in self._attributes:
            yield f"attribute.{location}", partial(self.program.disableAttributeArray, location)
        if self._buffer_bound:
            yield "buffer", self.buffer.release
        if self._array_bound:
            yield "vertex_array", self.vertex_array.release
        for index, (texture, unit) in reversed(tuple(enumerate(self._textures))):
            # QOpenGLTexture.release() without a unit only clears the currently
            # active unit, which may belong to a later texture in this pass.
            yield f"texture.{index}", partial(texture.release, unit)
        if self._program_bound:
            yield "program", self.program.release
        yield from self.restore
