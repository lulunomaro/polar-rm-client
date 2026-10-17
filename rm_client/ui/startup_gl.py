from __future__ import annotations

import struct
import time
from enum import Enum
from functools import partial

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
    QOpenGLVertexArrayObject,
)

from .gl_resources import GlDrawBindings, GlResourceOwner, destroy_created


class TextureOrigin(str, Enum):
    """声明纹理数据的坐标原点，确保每个来源只翻转一次。"""

    TOP_LEFT_IMAGE = "TopLeftImage"
    BOTTOM_LEFT_FBO = "BottomLeftFbo"
    EXTERNAL_TEXTURE = "ExternalTexture"


def normalize_image_for_texture(image: QImage, origin: TextureOrigin) -> QImage:
    """把图像统一为 RGBA，并仅在上传前完成一次坐标原点转换。"""
    source = image.convertToFormat(QImage.Format.Format_RGBA8888)
    if origin == TextureOrigin.TOP_LEFT_IMAGE:
        return source.mirrored(False, True)
    return source


class StartupGpuCompositor:
    """启动页的全屏视频纹理层；失败时由调用方回退到 QPainter。"""

    _GL_FLOAT = 0x1406
    _GL_TRIANGLE_STRIP = 0x0005

    def __init__(self) -> None:
        self.available = False
        self.last_error = ""
        self.resources = GlResourceOwner("startup")
        self.program: QOpenGLShaderProgram | None = None
        self._uniforms: dict[str, int] = {}
        self._position_attribute = -1
        self.vertex_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.vertex_array = QOpenGLVertexArrayObject()
        self.current: QOpenGLTexture | None = None
        self.previous: QOpenGLTexture | None = None
        self.spare: QOpenGLTexture | None = None
        self._pending_texture: QOpenGLTexture | None = None
        self.texture_size: tuple[int, int] = (0, 0)
        self.current_key = 0
        self.previous_key = 0
        self.frame_started = 0.0
        self.frame_interval = 1.0 / 30.0
        self.current_rect = QRectF()
        self.previous_rect = QRectF()
        self.dim = 0.0
        self.texture_origin = TextureOrigin.TOP_LEFT_IMAGE

    def initialize(self) -> bool:
        if self.available:
            return True
        if self.program is not None or self.resources.release_pending:
            try:
                self.release()
            except Exception:
                # Do not replace a native owner whose previous cleanup failed.
                return False
        self.last_error = ""
        bindings = GlDrawBindings(None, self.vertex_buffer, self.vertex_array)

        self.available = self.resources.run(
            "initialize", partial(self._initialize_resources, bindings), cleanup=bindings.cleanup,
            rejection=lambda: bindings.rejection or self.last_error, on_reject=self._release_actions,
        )
        self.last_error = self.resources.last_error
        return self.available

    def _initialize_resources(self, bindings: GlDrawBindings) -> bool:
        program = QOpenGLShaderProgram()
        self.program = program
        vertex = """
            #version 330
            layout(location = 0) in vec2 position;
            out vec2 uv;
            void main() {
                uv = position * 0.5 + vec2(0.5);
                gl_Position = vec4(position, 0.0, 1.0);
            }
        """
        fragment = """
            #version 330
            in vec2 uv;
            out vec4 fragColor;
            uniform sampler2D currentTex;
            uniform sampler2D previousTex;
            uniform vec4 currentUv;
            uniform vec4 previousUv;
            uniform float blend;
            uniform float dim;
            uniform int hasPrevious;
            uniform int flipY;
            vec2 mapUv(vec2 value, vec4 bounds) {
                float sourceY = flipY == 1 ? 1.0 - value.y : value.y;
                return bounds.xy + vec2(value.x, sourceY) * bounds.zw;
            }
            void main() {
                vec3 currentColor = texture(currentTex, mapUv(uv, currentUv)).rgb;
                vec3 color = currentColor;
                if (hasPrevious == 1) {
                    vec3 previousColor = texture(previousTex, mapUv(uv, previousUv)).rgb;
                    color = mix(previousColor, currentColor, blend);
                }
                color *= (1.0 - dim * 0.24);
                fragColor = vec4(color, 1.0);
            }
        """
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex):
            self.last_error = program.log()
            return False
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment):
            self.last_error = program.log()
            return False
        if not program.link():
            self.last_error = program.log()
            return False
        uniforms = {
            name: program.uniformLocation(name)
            for name in ("currentTex", "previousTex", "currentUv", "previousUv", "blend", "dim", "hasPrevious", "flipY")
        }
        position_attribute = program.attributeLocation("position")
        if position_attribute < 0 or any(location < 0 for location in uniforms.values()):
            self.last_error = "启动页着色器接口不完整"
            return False
        if not self.vertex_array.create():
            self.last_error = "GPU compositor VAO creation failed"
            return False
        if not self.vertex_buffer.create() or not self.vertex_buffer.isCreated():
            self.last_error = "无法创建启动页顶点缓冲"
            return False
        vertices = struct.pack("8f", -1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
        bindings.bind_vertex_array()
        if not bindings.bind_buffer():
            return False
        self.vertex_buffer.allocate(vertices, len(vertices))
        self._uniforms = uniforms
        self._position_attribute = position_attribute
        return True

    @staticmethod
    def _new_texture(image: QImage) -> QOpenGLTexture:
        # 视频帧默认是 RGB888。先固定为连续的 RGBA8888，避免不同驱动
        # 对三通道行对齐的解释不一致而产生横向花屏。
        source = image.convertToFormat(QImage.Format.Format_RGBA8888)
        # Construct directly from the normalised image.  Reusing a manually
        # allocated texture and calling setData(QImage) lets Qt attempt to
        # mutate the format/size on some drivers, which is illegal once the
        # storage exists and manifests as striped/corrupted video frames.
        texture = QOpenGLTexture(source)
        texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
        texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        return texture

    def _allocate_texture(self, width: int, height: int) -> QOpenGLTexture:
        """Allocate immutable RGBA storage; size and format never mutate in-place."""
        texture = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        # Keep ownership even if a wrapper raises after allocating native storage.
        self._pending_texture = texture
        if not texture.create():
            raise RuntimeError("Unable to create video texture")
        texture.setFormat(QOpenGLTexture.TextureFormat.RGBA8_UNorm)
        texture.setSize(width, height)
        texture.setMipLevels(1)
        texture.allocateStorage(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.UInt8)
        texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
        texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        return texture

    def _upload_next_frame(self, image: QImage, origin: TextureOrigin) -> QOpenGLTexture:
        source = normalize_image_for_texture(image, origin)
        candidate = None
        cleanup_candidate = False

        def upload() -> bool:
            nonlocal candidate, cleanup_candidate
            # A failed candidate is never reused or replaced before cleanup.
            # This retry is bounded to one attempt per explicit update request.
            self._release_texture("_pending_texture")
            reusable = self.spare
            if reusable is not None and (reusable.width() != source.width() or reusable.height() != source.height()):
                self._release_texture("spare")
                reusable = None
            cleanup_candidate = True
            if reusable is None:
                reusable = self._allocate_texture(source.width(), source.height())
            self._pending_texture = reusable
            self.spare = None
            # Source normalization supplies the existing one-time origin flip.
            reusable.setData(
                QOpenGLTexture.PixelFormat.RGBA,
                QOpenGLTexture.PixelType.UInt8,
                source.constBits(),
            )
            candidate = reusable
            self._pending_texture = None
            self.texture_size = (source.width(), source.height())
            return True

        def discard_candidate():
            if cleanup_candidate:
                yield "candidate_texture", partial(self._release_texture, "_pending_texture")

        try:
            self.resources.run("upload", upload, on_reject=discard_candidate, reraise=True)
        finally:
            self.last_error = self.resources.last_error
        return candidate

    def update_frame(
        self,
        image: QImage,
        rect: QRectF,
        sequence: int,
        frame_interval: float,
        dim: float,
        *,
        origin: TextureOrigin = TextureOrigin.TOP_LEFT_IMAGE,
    ) -> None:
        if image.isNull() or not self.available:
            return
        key = int(sequence) if sequence > 0 else int(image.cacheKey())
        if key != self.current_key:
            old_current = self.current
            old_previous = self.previous
            old_key = self.current_key
            old_rect = self.current_rect
            self.current = self._upload_next_frame(image, origin)
            self.current_key = key
            self.current_rect = QRectF(rect)
            if old_current is not None:
                self.previous = old_current
                self.previous_key = old_key
                self.previous_rect = old_rect
            if old_previous is not None:
                self.spare = old_previous
            self.frame_started = time.perf_counter()
            self.frame_interval = max(1.0 / 60.0, min(1.0 / 8.0, frame_interval))
        # 视差跟随显示器刷新率，与 24/30 FPS 视频源解码频率无关。
        self.current_rect = QRectF(rect)
        self.dim = max(0.0, min(1.0, dim))
        self.texture_origin = TextureOrigin.BOTTOM_LEFT_FBO

    def discard_interpolation_history(self) -> None:
        """切页时丢弃旧插值帧，当前视频帧与纹理存储保持不变。"""
        if self.previous is not None:
            if self.spare is not None and self.spare is not self.previous:
                self.spare.destroy()
            self.spare = self.previous
        self.previous = None
        self.previous_key = 0
        self.previous_rect = QRectF()
        self.frame_started = time.perf_counter()

    @staticmethod
    def _uv_rect(draw_rect: QRectF, canvas: QRectF) -> tuple[float, float, float, float]:
        if draw_rect.isEmpty():
            return (0.0, 0.0, 1.0, 1.0)
        x = (canvas.left() - draw_rect.left()) / draw_rect.width()
        y = (canvas.top() - draw_rect.top()) / draw_rect.height()
        w = canvas.width() / draw_rect.width()
        h = canvas.height() / draw_rect.height()
        return (x, y, w, h)

    def render(self, functions, canvas: QRectF, *, interpolate: bool = True) -> bool:
        if not self.available or self.program is None or self.current is None:
            return False
        bindings = GlDrawBindings(self.program, self.vertex_buffer, self.vertex_array)

        def draw() -> bool:
            functions.glDisable(0x0B71)
            functions.glDisable(0x0C11)
            bindings.bind_vertex_array()
            if not bindings.bind_program():
                return False
            bindings.bind_texture(self.current, 0)
            functions.glUniform1i(self._uniforms["currentTex"], 0)
            has_previous = interpolate and self.previous is not None and not self.previous_rect.isEmpty()
            if has_previous:
                bindings.bind_texture(self.previous, 1)
                functions.glUniform1i(self._uniforms["previousTex"], 1)
            current_uv = self._uv_rect(self.current_rect, canvas)
            previous_uv = self._uv_rect(self.previous_rect, canvas) if has_previous else current_uv
            functions.glUniform4f(self._uniforms["currentUv"], *current_uv)
            functions.glUniform4f(self._uniforms["previousUv"], *previous_uv)
            blend = min(1.0, max(0.0, (time.perf_counter() - self.frame_started) / self.frame_interval))
            functions.glUniform1f(self._uniforms["blend"], float(blend))
            functions.glUniform1f(self._uniforms["dim"], float(self.dim))
            functions.glUniform1i(self._uniforms["hasPrevious"], 1 if has_previous else 0)
            # OpenGL 纹理坐标以左下为原点，而 QImage/视频解码帧以左上为原点。
            # FBO 纹理已经是 OpenGL 原点，不能再次翻转。
            flip_y = self.texture_origin == TextureOrigin.TOP_LEFT_IMAGE
            functions.glUniform1i(self._uniforms["flipY"], 1 if flip_y else 0)
            if not bindings.bind_buffer():
                return False
            bindings.enable_attribute(self._position_attribute)
            self.program.setAttributeBuffer(self._position_attribute, self._GL_FLOAT, 0, 2, 2 * 4)
            functions.glDrawArrays(self._GL_TRIANGLE_STRIP, 0, 4)
            return True

        accepted = self.resources.run("render", draw, cleanup=bindings.cleanup, rejection=lambda: bindings.rejection)
        self.last_error = self.resources.last_error
        if self.resources.last_result.primary_fault or self.resources.last_result.cleanup_faults:
            self.available = False
        return accepted

    def _release_texture(self, name: str) -> None:
        texture = getattr(self, name)
        if texture is not None:
            texture.destroy()
            setattr(self, name, None)

    def _release_program(self) -> None:
        if self.program is not None:
            self.program.removeAllShaders()
            self.program = None

    def _release_actions(self):
        return (
            *((name, partial(self._release_texture, name)) for name in ("current", "previous", "spare", "_pending_texture")),
            ("buffer", partial(destroy_created, self.vertex_buffer)),
            ("vertex_array", partial(destroy_created, self.vertex_array)),
            ("program", self._release_program),
        )

    def release(self) -> None:
        self.available = False
        self.texture_size = (0, 0)
        self.current_key = 0
        self.previous_key = 0
        self.current_rect = QRectF()
        self.previous_rect = QRectF()
        self._uniforms = {}
        self._position_attribute = -1
        try:
            self.resources.release(self._release_actions())
        finally:
            self.last_error = self.resources.last_error
