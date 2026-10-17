from __future__ import annotations

from dataclasses import dataclass
import struct

from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)

from functools import partial

from .gl_resources import GlDrawBindings, GlResourceOwner, destroy_created


_GL_BLEND = 0x0BE2
_GL_CULL_FACE = 0x0B44
_GL_DEPTH_TEST = 0x0B71
_GL_FLOAT = 0x1406
_GL_FUNC_ADD = 0x8006
_GL_ONE = 1
_GL_ONE_MINUS_SRC_ALPHA = 0x0303
_GL_SCISSOR_TEST = 0x0C11
_GL_TEXTURE0 = 0x84C0
_GL_TEXTURE_2D = 0x0DE1
_GL_TRIANGLES = 0x0004


@dataclass(frozen=True)
class QuickActionWheelCommand:
    center_x: float
    center_y: float
    radius: float
    inner_radius: float
    sector_count: int
    selected_index: int
    opacity: float


class QuickActionWheelGpuRenderer:
    """Analytic radial renderer with no rectangular UI primitives."""

    def __init__(self) -> None:
        self.available = False
        self.last_error = ""
        self.resources = GlResourceOwner("wheel")
        self.program: QOpenGLShaderProgram | None = None
        self.buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.vertex_array = QOpenGLVertexArrayObject()
        self.draw_calls = 0

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
                self.last_error = "quick action wheel VAO creation rejected"
                return False
            if not self.buffer.create():
                self.last_error = "quick action wheel VBO creation rejected"
                return False
            return True

        self.available = self.resources.run(
            "initialize", create, rejection=lambda: self.last_error, on_reject=self._release_actions,
        )
        self.last_error = self.resources.last_error
        return self.available

    @staticmethod
    def _vertices(command: QuickActionWheelCommand, ratio: float) -> bytes:
        radius = max(1.0, command.radius) * ratio
        cx = command.center_x * ratio
        cy = command.center_y * ratio
        x0, y0 = cx - radius, cy - radius
        x1, y1 = cx + radius, cy + radius
        vertices = (
            (x0, y0, -1.0, -1.0),
            (x1, y0, 1.0, -1.0),
            (x0, y1, -1.0, 1.0),
            (x0, y1, -1.0, 1.0),
            (x1, y0, 1.0, -1.0),
            (x1, y1, 1.0, 1.0),
        )
        return b"".join(struct.pack("4f", *vertex) for vertex in vertices)

    def render(
        self,
        functions,
        command: QuickActionWheelCommand,
        width: int,
        height: int,
        device_pixel_ratio: float,
        *,
        background_texture: int,
        blur_texture: int,
    ) -> bool:
        if not self.available or self.program is None:
            return False
        ratio = max(1.0, float(device_pixel_ratio))
        payload = self._vertices(command, ratio)
        self.draw_calls = 0
        bindings = GlDrawBindings(self.program, self.buffer, self.vertex_array, restore=(
            ("active_texture_1", partial(functions.glActiveTexture, _GL_TEXTURE0 + 1)),
            ("unbind_texture_1", partial(functions.glBindTexture, _GL_TEXTURE_2D, 0)),
            ("active_texture_0", partial(functions.glActiveTexture, _GL_TEXTURE0)),
            ("unbind_texture_0", partial(functions.glBindTexture, _GL_TEXTURE_2D, 0)),
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
            functions.glUniform1f(
                self.program.uniformLocation("innerRatio"),
                max(0.08, min(0.82, command.inner_radius / max(1.0, command.radius))),
            )
            functions.glUniform1i(
                self.program.uniformLocation("sectorCount"),
                max(4, min(8, int(command.sector_count))),
            )
            functions.glUniform1i(
                self.program.uniformLocation("selectedIndex"),
                int(command.selected_index),
            )
            functions.glUniform1f(
                self.program.uniformLocation("wheelOpacity"),
                max(0.0, min(1.0, float(command.opacity))),
            )
            functions.glActiveTexture(_GL_TEXTURE0)
            functions.glBindTexture(_GL_TEXTURE_2D, int(background_texture))
            functions.glUniform1i(self.program.uniformLocation("backgroundTex"), 0)
            functions.glActiveTexture(_GL_TEXTURE0 + 1)
            functions.glBindTexture(_GL_TEXTURE_2D, int(blur_texture))
            functions.glUniform1i(self.program.uniformLocation("blurTex"), 1)

            bindings.bind_vertex_array()
            if not bindings.bind_buffer():
                return False
            self.buffer.allocate(payload, len(payload))
            stride = 4 * 4
            for location, offset in ((0, 0), (1, 8)):
                bindings.enable_attribute(location)
                self.program.setAttributeBuffer(location, _GL_FLOAT, offset, 2, stride)
            functions.glDrawArrays(_GL_TRIANGLES, 0, 6)
            return True

        accepted = self.resources.run("render", draw, cleanup=bindings.cleanup, rejection=lambda: bindings.rejection)
        self.last_error = self.resources.last_error
        self.draw_calls = int(accepted)
        return accepted

    def _build_program(self) -> QOpenGLShaderProgram | None:
        vertex = """
            #version 330
            layout(location=0) in vec2 position;
            layout(location=1) in vec2 localPosition;
            uniform vec2 viewport;
            out vec2 local;
            void main() {
                vec2 ndc = vec2(
                    position.x / viewport.x * 2.0 - 1.0,
                    1.0 - position.y / viewport.y * 2.0
                );
                gl_Position = vec4(ndc, 0.0, 1.0);
                local = localPosition;
            }
        """
        fragment = """
            #version 330
            const float PI = 3.14159265358979323846;
            const float TAU = 6.28318530717958647692;

            in vec2 local;
            uniform vec2 viewport;
            uniform sampler2D backgroundTex;
            uniform sampler2D blurTex;
            uniform float innerRatio;
            uniform int sectorCount;
            uniform int selectedIndex;
            uniform float wheelOpacity;
            out vec4 fragColor;

            vec3 sampleChromatic(vec2 uv, vec2 offset) {
                vec3 a = texture(backgroundTex, clamp(uv + offset * 1.34, 0.0, 1.0)).rgb;
                vec3 b = texture(backgroundTex, clamp(uv + offset, 0.0, 1.0)).rgb;
                vec3 c = texture(backgroundTex, clamp(uv + offset * 0.68, 0.0, 1.0)).rgb;
                return vec3(a.r, b.g, c.b);
            }

            void main() {
                float radial = length(local);
                float aa = max(fwidth(radial) * 1.35, 0.0025);
                float outerMask = 1.0 - smoothstep(1.0 - aa, 1.0 + aa, radial);
                if (outerMask <= 0.0) discard;

                vec2 uv = gl_FragCoord.xy / viewport;
                vec2 normal = radial > 0.0001 ? normalize(vec2(local.x, -local.y)) : vec2(0.0);
                float outerLens = smoothstep(0.60, 0.96, radial);
                float innerLens = 1.0 - smoothstep(innerRatio - 0.05, innerRatio + 0.10, radial);
                float lensAmount = max(outerLens, innerLens * 0.42);
                vec2 offset = normal * (8.0 + 7.0 * outerLens) * lensAmount / viewport;
                vec3 sharp = sampleChromatic(uv, -offset);
                vec3 soft = texture(blurTex, uv).rgb;
                vec3 lens = mix(sharp, soft, 0.10 + innerLens * 0.08);

                float angle = mod(atan(local.x, -local.y) + TAU, TAU);
                float span = TAU / float(sectorCount);
                float sectorValue = floor((angle + span * 0.5) / span);
                int sector = int(mod(sectorValue, float(sectorCount)));
                float boundary = abs(mod(angle + span * 0.5, span) - span * 0.5);
                float separator = 1.0 - smoothstep(0.010, 0.024, boundary);
                float annulus = smoothstep(innerRatio - aa, innerRatio + aa, radial);
                float selected = sector == selectedIndex ? 1.0 : 0.0;

                vec3 tint = mix(vec3(0.025, 0.045, 0.070), vec3(0.10, 0.47, 0.96), selected);
                float tintAlpha = mix(0.16, 0.34, selected) * annulus;
                lens = mix(lens, tint, tintAlpha);
                lens += vec3(separator * annulus * 0.12);

                float outerRim = smoothstep(0.935, 0.982, radial)
                    * (1.0 - smoothstep(0.982, 1.015, radial));
                float innerDistance = abs(radial - innerRatio);
                float innerRim = 1.0 - smoothstep(0.008, 0.022, innerDistance);
                float rim = max(outerRim, innerRim * 0.46);
                lens += vec3(rim * mix(0.20, 0.34, selected));

                float centerShade = (1.0 - annulus) * 0.20;
                lens = mix(lens, vec3(0.018, 0.028, 0.044), centerShade);
                float alpha = outerMask * wheelOpacity * 0.92;
                fragColor = vec4(lens * alpha, alpha);
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
