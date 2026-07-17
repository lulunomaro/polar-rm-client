from __future__ import annotations

import struct
import time

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QVector4D
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
    QOpenGLVertexArrayObject,
)


class StartupGpuCompositor:
    """启动页的全屏视频纹理层；失败时由调用方回退到 QPainter。"""

    _GL_FLOAT = 0x1406
    _GL_TRIANGLE_STRIP = 0x0005
    _UNIFORM_NAMES = (
        "currentTex",
        "previousTex",
        "currentUv",
        "previousUv",
        "blend",
        "dim",
        "hasPrevious",
    )

    def __init__(self) -> None:
        self.available = False
        self.last_error = ""
        self.program: QOpenGLShaderProgram | None = None
        self.loc: dict[str, int] = {}
        self.vao = QOpenGLVertexArrayObject()
        self.vertex_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.current: QOpenGLTexture | None = None
        self.previous: QOpenGLTexture | None = None
        self.current_key = 0
        self.previous_key = 0
        self.current_size = (0, 0)
        self.previous_size = (0, 0)
        self.frame_started = 0.0
        self.frame_interval = 1.0 / 30.0
        self.current_rect = QRectF()
        self.previous_rect = QRectF()
        self.dim = 0.0

    def initialize(self) -> bool:
        if self.available:
            return True
        program = QOpenGLShaderProgram()
        vertex = """
            #version 330
            layout(location = 0) in vec2 position;
            out vec2 uv;
            void main() {
                // QImage 原点在左上，OpenGL 纹理采样默认左下；翻转 V 避免壁纸上下颠倒。
                uv = vec2(position.x * 0.5 + 0.5, 0.5 - position.y * 0.5);
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
            vec2 mapUv(vec2 value, vec4 bounds) {
                return bounds.xy + value * bounds.zw;
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
        # Core Profile 3.3 必须绑定 VAO，否则 glDrawArrays 无效 → 全屏只剩 glClear 黑色。
        if not self.vao.create():
            self.last_error = "无法创建启动页 VAO（OpenGL Core Profile 需要）"
            return False
        self.vao.bind()
        self.vertex_buffer.create()
        if not self.vertex_buffer.isCreated():
            self.vao.release()
            self.last_error = "无法创建启动页顶点缓冲"
            return False
        vertices = struct.pack("8f", -1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
        self.vertex_buffer.bind()
        self.vertex_buffer.allocate(vertices, len(vertices))
        program.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, self._GL_FLOAT, 0, 2, 2 * 4)
        program.release()
        self.vertex_buffer.release()
        self.vao.release()
        # PySide6 部分版本缺少 setUniformValue(str, float) 重载；统一用 location + 1i/1f。
        self.loc = {name: program.uniformLocation(name) for name in self._UNIFORM_NAMES}
        self.program = program
        self.available = True
        return True

    @staticmethod
    def _prepare_image(image: QImage) -> QImage:
        if image.format() != QImage.Format.Format_RGBA8888:
            return image.convertToFormat(QImage.Format.Format_RGBA8888)
        return image

    @staticmethod
    def _destroy_texture(texture: QOpenGLTexture | None) -> None:
        if texture is not None:
            texture.destroy()

    def _create_texture(self, image: QImage) -> tuple[QOpenGLTexture, tuple[int, int]]:
        """每次新建纹理并只调用一次 setData(QImage)。

        注意：QOpenGLTexture.setData(QImage) 会分配 storage。
        对已分配 storage 的纹理再次 setData(QImage) 会触发
        “Cannot resize/setFormat/setMipLevels” 并导致上传失败。
        """
        prepared = self._prepare_image(image)
        texture = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
        texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        texture.setData(prepared)
        return texture, (prepared.width(), prepared.height())

    def update_frame(self, image: QImage, rect: QRectF, sequence: int, frame_interval: float, dim: float) -> None:
        if image.isNull() or not self.available:
            return
        key = int(image.cacheKey())
        if key != self.current_key or sequence <= 0:
            old_current = self.current
            old_key = self.current_key
            old_rect = self.current_rect
            old_size = self.current_size
            # 交叉淡入：旧 current → previous；更旧 previous 先销毁。
            # 新 current 始终 create+setData 一次，禁止复用对象二次 setData(QImage)。
            self._destroy_texture(self.previous)
            self.current, self.current_size = self._create_texture(image)
            self.current_key = key
            self.current_rect = QRectF(rect)
            if old_current is not None:
                self.previous = old_current
                self.previous_key = old_key
                self.previous_rect = old_rect
                self.previous_size = old_size
            else:
                self.previous = None
                self.previous_key = 0
                self.previous_rect = QRectF()
                self.previous_size = (0, 0)
            self.frame_started = time.perf_counter()
            self.frame_interval = max(1.0 / 60.0, min(1.0 / 8.0, frame_interval))
        else:
            self.current_rect = QRectF(rect)
        self.dim = max(0.0, min(1.0, dim))

    @staticmethod
    def _uv_rect(draw_rect: QRectF, canvas: QRectF) -> tuple[float, float, float, float]:
        if draw_rect.isEmpty():
            return (0.0, 0.0, 1.0, 1.0)
        x = (canvas.left() - draw_rect.left()) / draw_rect.width()
        y = (canvas.top() - draw_rect.top()) / draw_rect.height()
        w = canvas.width() / draw_rect.width()
        h = canvas.height() / draw_rect.height()
        return (x, y, w, h)

    def _drop_previous(self) -> None:
        if self.previous is not None:
            self.previous.destroy()
        self.previous = None
        self.previous_key = 0
        self.previous_rect = QRectF()
        self.previous_size = (0, 0)

    @property
    def has_frame(self) -> bool:
        return self.available and self.current is not None and self.program is not None

    def touch_rect_dim(self, rect: QRectF, dim: float) -> None:
        """无新视频帧时仍可更新视差矩形与暗角，用于页面切换保底绘制。"""
        if not self.has_frame:
            return
        if not rect.isEmpty():
            self.current_rect = QRectF(rect)
        self.dim = max(0.0, min(1.0, dim))

    @staticmethod
    def _reset_gl_state(functions) -> None:
        """把纹理/程序绑定清干净，避免随后 QPainter 花屏一帧。"""
        try:
            functions.glActiveTexture(0x84C0)
            functions.glBindTexture(0x0DE1, 0)
            functions.glActiveTexture(0x84C1)
            functions.glBindTexture(0x0DE1, 0)
            functions.glActiveTexture(0x84C0)
            functions.glUseProgram(0)
            functions.glBindVertexArray(0)
            functions.glDisable(0x0BE2)  # GL_BLEND
            functions.glDisable(0x0B71)  # GL_DEPTH_TEST
            functions.glDisable(0x0B90)  # GL_STENCIL_TEST
            functions.glDisable(0x0C11)  # GL_SCISSOR_TEST
        except Exception:
            pass

    def render(self, functions, canvas: QRectF, *, pixel_width: int | None = None, pixel_height: int | None = None) -> bool:
        if not self.available or self.program is None or self.current is None:
            return False
        if not self.vao.isCreated():
            self.last_error = "启动页 VAO 未创建"
            return False
        has_previous = False
        try:
            # 高 DPI 下必须按物理像素设 viewport，否则只画到角落，其余是清屏色。
            pw = int(pixel_width or canvas.width() or 1)
            ph = int(pixel_height or canvas.height() or 1)
            functions.glViewport(0, 0, max(1, pw), max(1, ph))
            functions.glDisable(0x0BE2)
            functions.glDisable(0x0B71)

            blend = min(1.0, max(0.0, (time.perf_counter() - self.frame_started) / self.frame_interval))
            if blend >= 0.999 and self.previous is not None:
                self._drop_previous()
            self.program.bind()
            self.current.bind(0)
            loc = self.loc
            self.program.setUniformValue1i(loc["currentTex"], 0)
            has_previous = self.previous is not None and not self.previous_rect.isEmpty()
            if has_previous and self.previous is not None:
                self.previous.bind(1)
                self.program.setUniformValue1i(loc["previousTex"], 1)
            current_uv = self._uv_rect(self.current_rect, canvas)
            previous_uv = self._uv_rect(self.previous_rect, canvas) if has_previous else current_uv
            self.program.setUniformValue(loc["currentUv"], QVector4D(*current_uv))
            self.program.setUniformValue(loc["previousUv"], QVector4D(*previous_uv))
            self.program.setUniformValue1f(loc["blend"], float(blend))
            self.program.setUniformValue1f(loc["dim"], float(self.dim))
            self.program.setUniformValue1i(loc["hasPrevious"], 1 if has_previous else 0)
            self.vao.bind()
            functions.glDrawArrays(self._GL_TRIANGLE_STRIP, 0, 4)
            self.vao.release()
            if has_previous and self.previous is not None:
                self.previous.release()
            self.current.release()
            self.program.release()
            self._reset_gl_state(functions)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.available = False
            try:
                if self.vao.isCreated():
                    self.vao.release()
                if has_previous and self.previous is not None:
                    self.previous.release()
                if self.current is not None:
                    self.current.release()
                if self.program is not None:
                    self.program.release()
                self._reset_gl_state(functions)
            except Exception:
                pass
            return False

    def release(self) -> None:
        for texture in (self.current, self.previous):
            if texture is not None:
                texture.destroy()
        self.current = None
        self.previous = None
        self.current_size = (0, 0)
        self.previous_size = (0, 0)
        if self.vertex_buffer.isCreated():
            self.vertex_buffer.destroy()
        if self.vao.isCreated():
            self.vao.destroy()
        if self.program is not None:
            self.program.removeAllShaders()
        self.program = None
        self.loc = {}
        self.available = False
