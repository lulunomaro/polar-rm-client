from __future__ import annotations

from dataclasses import dataclass, field
import math
import struct

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)

from functools import partial

from .gl_resources import GlDrawBindings, GlResourceOwner, destroy_created

from .clickgui_gpu import ClickGuiDrawList


_GL_BLEND = 0x0BE2
_GL_CULL_FACE = 0x0B44
_GL_DEPTH_TEST = 0x0B71
_GL_FLOAT = 0x1406
_GL_FUNC_ADD = 0x8006
_GL_ONE = 1
_GL_ONE_MINUS_SRC_ALPHA = 0x0303
_GL_SCISSOR_TEST = 0x0C11
_GL_TRIANGLES = 0x0004


def armor_corner_segments(
    points: list[QPointF] | tuple[QPointF, ...], *, limit: float = 22.0,
) -> list[tuple[QPointF, QPointF]]:
    """Short corner ticks along the actual quad, never its axis-aligned bounds."""
    if len(points) != 4:
        return []
    segments = []
    for index, origin in enumerate(points):
        for neighbor in (points[(index - 1) % 4], points[(index + 1) % 4]):
            delta = neighbor - origin
            length = math.hypot(delta.x(), delta.y())
            if length > 0.01:
                segments.append((origin, origin + delta * (min(limit, length * 0.24) / length)))
    return segments


@dataclass(frozen=True)
class VisionWireLine:
    start: QPointF
    end: QPointF
    color: QColor
    width: float
    dash_length: float = 0.0
    gap_length: float = 0.0


@dataclass
class VisionWireBatch:
    """Line-only vision commands.

    There is intentionally no polygon or rectangle fill API. Empty armor
    interiors therefore remain transparent by construction.
    """

    lines: list[VisionWireLine] = field(default_factory=list)
    labels: ClickGuiDrawList = field(default_factory=ClickGuiDrawList)

    def line(
        self,
        start: QPointF,
        end: QPointF,
        color: QColor,
        width: float,
        *,
        dash_length: float = 0.0,
        gap_length: float = 0.0,
    ) -> None:
        if math.hypot(end.x() - start.x(), end.y() - start.y()) < 0.01:
            return
        self.lines.append(
            VisionWireLine(
                QPointF(start),
                QPointF(end),
                QColor(color),
                max(0.35, float(width)),
                max(0.0, float(dash_length)),
                max(0.0, float(gap_length)),
            )
        )

    def polyline(
        self,
        points: list[QPointF] | tuple[QPointF, ...],
        color: QColor,
        width: float,
        *,
        closed: bool = False,
        dash_length: float = 0.0,
        gap_length: float = 0.0,
    ) -> None:
        if len(points) < 2:
            return
        limit = len(points) if closed else len(points) - 1
        for index in range(limit):
            self.line(
                points[index],
                points[(index + 1) % len(points)],
                color,
                width,
                dash_length=dash_length,
                gap_length=gap_length,
            )

    def fluorescent_polyline(
        self,
        points: list[QPointF] | tuple[QPointF, ...],
        *,
        width: float,
        alpha: float = 1.0,
        closed: bool = False,
        dashed: bool = False,
        glow: float = 0.85,
    ) -> None:
        """Debug Box ESP fluorescent white (esp-box-white.html drawBoxEdges).

        Pure RGB 1,1,1 core; multi-pass soft bloom (no fill). Default glow 0.85
        so wireframes read bright on dark camera frames.
        """
        alpha = max(0.0, min(1.0, float(alpha)))
        glow = max(0.0, min(1.0, float(glow)))
        if alpha <= 0.0:
            return
        base_w = max(0.5, float(width))
        dash = 7.0 if dashed else 0.0
        gap = 5.0 if dashed else 0.0
        if glow > 0.01:
            self.polyline(
                points,
                QColor(255, 255, 255, max(1, round(255 * 0.06 * glow * alpha))),
                base_w + 8.0 * glow,
                closed=closed,
                dash_length=dash,
                gap_length=gap,
            )
            self.polyline(
                points,
                QColor(255, 255, 255, max(1, round(255 * 0.14 * glow * alpha))),
                base_w + 3.5 * glow,
                closed=closed,
                dash_length=dash,
                gap_length=gap,
            )
            self.polyline(
                points,
                QColor(255, 255, 255, max(1, round(255 * 0.30 * glow * alpha))),
                base_w + 1.5 * glow,
                closed=closed,
                dash_length=dash,
                gap_length=gap,
            )
        self.polyline(
            points,
            QColor(255, 255, 255, max(1, round(255 * alpha))),
            base_w,
            closed=closed,
            dash_length=dash,
            gap_length=gap,
        )

    def fluorescent_line(
        self,
        start: QPointF,
        end: QPointF,
        *,
        width: float,
        alpha: float = 1.0,
        dashed: bool = False,
        glow: float = 0.85,
    ) -> None:
        """Single-edge fluorescent white (same formula as fluorescent_polyline)."""
        self.fluorescent_polyline(
            (start, end), width=width, alpha=alpha, dashed=dashed, glow=glow,
        )


class VisionWireGpuRenderer:
    """Premultiplied GPU line renderer for armor and prediction geometry."""

    def __init__(self) -> None:
        self.available = False
        self.last_error = ""
        self.resources = GlResourceOwner("vision")
        self.program: QOpenGLShaderProgram | None = None
        self.buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.vertex_array = QOpenGLVertexArrayObject()
        self.draw_calls = 0
        self.last_vertex_count = 0

    def initialize(self) -> bool:
        if self.available:
            return True
        if self.program is not None or self.resources.release_pending:
            try:
                self.release()
            except Exception:
                # The retained owner must finish cleanup before replacement.
                return False
        self.last_error = ""

        def create() -> bool:
            program = self._build_program()
            if program is None:
                return False
            self.program = program
            if not self.vertex_array.create():
                self.last_error = "vision wire VAO creation rejected"
                return False
            if not self.buffer.create():
                self.last_error = "vision wire VBO creation rejected"
                return False
            return True

        self.available = self.resources.run(
            "initialize", create, rejection=lambda: self.last_error, on_reject=self._release_actions,
        )
        self.last_error = self.resources.last_error
        return self.available

    @staticmethod
    def _rgba(color: QColor) -> tuple[float, float, float, float]:
        return color.redF(), color.greenF(), color.blueF(), color.alphaF()

    @staticmethod
    def _split_dashes(line: VisionWireLine) -> list[tuple[QPointF, QPointF]]:
        dx = line.end.x() - line.start.x()
        dy = line.end.y() - line.start.y()
        length = math.hypot(dx, dy)
        if length < 0.01:
            return []
        if line.dash_length <= 0.0:
            return [(line.start, line.end)]
        cycle = max(0.5, line.dash_length + max(0.0, line.gap_length))
        ux, uy = dx / length, dy / length
        output: list[tuple[QPointF, QPointF]] = []
        cursor = 0.0
        while cursor < length:
            segment_end = min(length, cursor + line.dash_length)
            if segment_end > cursor:
                output.append(
                    (
                        QPointF(line.start.x() + ux * cursor, line.start.y() + uy * cursor),
                        QPointF(line.start.x() + ux * segment_end, line.start.y() + uy * segment_end),
                    )
                )
            cursor += cycle
        return output

    def _vertices(self, batch: VisionWireBatch, ratio: float) -> bytes:
        packed = bytearray()
        for line in batch.lines:
            for start, end in self._split_dashes(line):
                x0, y0 = start.x() * ratio, start.y() * ratio
                x1, y1 = end.x() * ratio, end.y() * ratio
                dx, dy = x1 - x0, y1 - y0
                length = math.hypot(dx, dy)
                if length < 0.01:
                    continue
                half_core = max(0.25, line.width * ratio * 0.5)
                half_outer = half_core + 1.0
                nx, ny = -dy / length * half_outer, dx / length * half_outer
                edge_start = max(0.05, min(0.98, half_core / half_outer))
                rgba = self._rgba(line.color)
                vertices = (
                    (x0 + nx, y0 + ny, 1.0),
                    (x0 - nx, y0 - ny, -1.0),
                    (x1 + nx, y1 + ny, 1.0),
                    (x1 + nx, y1 + ny, 1.0),
                    (x0 - nx, y0 - ny, -1.0),
                    (x1 - nx, y1 - ny, -1.0),
                )
                for x, y, side in vertices:
                    packed.extend(struct.pack("8f", x, y, *rgba, side, edge_start))
        return bytes(packed)

    def render(
        self,
        functions,
        batch: VisionWireBatch,
        width: int,
        height: int,
        device_pixel_ratio: float,
    ) -> bool:
        if not self.available or self.program is None:
            return False
        ratio = max(1.0, float(device_pixel_ratio))
        payload = self._vertices(batch, ratio)
        self.last_vertex_count = len(payload) // (8 * 4)
        self.draw_calls = 0
        if not payload:
            return True
        bindings = GlDrawBindings(self.program, self.buffer, self.vertex_array, restore=(
            ("disable_blend", partial(functions.glDisable, _GL_BLEND)),
            ("disable_scissor", partial(functions.glDisable, _GL_SCISSOR_TEST)),
            ("disable_depth", partial(functions.glDisable, _GL_DEPTH_TEST)),
            ("disable_cull", partial(functions.glDisable, _GL_CULL_FACE)),
        ))

        def draw() -> bool:
            physical_width = max(1, round(width * ratio))
            physical_height = max(1, round(height * ratio))
            functions.glViewport(0, 0, physical_width, physical_height)
            functions.glDisable(_GL_DEPTH_TEST)
            functions.glDisable(_GL_CULL_FACE)
            functions.glDisable(_GL_SCISSOR_TEST)
            functions.glEnable(_GL_BLEND)
            functions.glBlendEquation(_GL_FUNC_ADD)
            functions.glBlendFunc(_GL_ONE, _GL_ONE_MINUS_SRC_ALPHA)

            if not bindings.bind_program():
                return False
            functions.glUniform2f(
                self.program.uniformLocation("viewport"),
                float(physical_width),
                float(physical_height),
            )
            bindings.bind_vertex_array()
            if not bindings.bind_buffer():
                return False
            self.buffer.allocate(payload, len(payload))
            stride = 8 * 4
            for location, size, offset in ((0, 2, 0), (1, 4, 8), (2, 1, 24), (3, 1, 28)):
                bindings.enable_attribute(location)
                self.program.setAttributeBuffer(location, _GL_FLOAT, offset, size, stride)
            functions.glDrawArrays(_GL_TRIANGLES, 0, self.last_vertex_count)
            return True

        accepted = self.resources.run("render", draw, cleanup=bindings.cleanup, rejection=lambda: bindings.rejection)
        self.last_error = self.resources.last_error
        self.draw_calls = int(accepted)
        return accepted

    def _build_program(self) -> QOpenGLShaderProgram | None:
        vertex = """
            #version 330
            layout(location=0) in vec2 position;
            layout(location=1) in vec4 lineColor;
            layout(location=2) in float sideDistance;
            layout(location=3) in float edgeStart;
            uniform vec2 viewport;
            out vec4 color;
            out float side;
            out float edge;
            void main() {
                vec2 ndc = vec2(
                    position.x / viewport.x * 2.0 - 1.0,
                    1.0 - position.y / viewport.y * 2.0
                );
                gl_Position = vec4(ndc, 0.0, 1.0);
                color = lineColor;
                side = sideDistance;
                edge = edgeStart;
            }
        """
        fragment = """
            #version 330
            in vec4 color;
            in float side;
            in float edge;
            out vec4 fragColor;
            void main() {
                float coverage = 1.0 - smoothstep(edge, 1.0, abs(side));
                float alpha = color.a * coverage;
                fragColor = vec4(color.rgb * alpha, alpha);
            }
        """
        program = QOpenGLShaderProgram()
        self.program = program
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex):
            self.last_error = program.log()
            return None
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment):
            self.last_error = program.log()
            return None
        if not program.link():
            self.last_error = program.log()
            return None
        return program

    def _release_program(self) -> None:
        if self.program is not None:
            self.program.removeAllShaders()
            self.program = None

    def _release_actions(self):
        return (
            ("buffer", partial(destroy_created, self.buffer)),
            ("vertex_array", partial(destroy_created, self.vertex_array)),
            ("program", self._release_program),
        )

    def release(self) -> None:
        self.available = False
        try:
            self.resources.release(self._release_actions())
        finally:
            self.last_error = self.resources.last_error
