from __future__ import annotations

"""OpenGL 液态玻璃（liquid-glass-react 语义）——全 GPU 采样背景纹理。

在 QOpenGLWidget 的 native painting 中绘制圆角玻璃面板：
多采样模糊 + 边缘折射/色散 + 材料 tint + 高光边。
"""

import struct
from dataclasses import dataclass

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QVector2D, QVector4D
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
    QOpenGLVertexArrayObject,
)


@dataclass
class GlassDrawCmd:
    rect: QRectF
    radius: float
    mode: str = "standard"
    surface: str = "main"
    hover: float = 0.0
    press: float = 0.0
    pointer_x: float = 0.5
    pointer_y: float = 0.5


class GlassGpuCompositor:
    """批量/单面板 GPU 玻璃。"""

    _GL_FLOAT = 0x1406
    _GL_TRIANGLE_STRIP = 0x0005
    _GL_BLEND = 0x0BE2
    _GL_SRC_ALPHA = 0x0302
    _GL_ONE_MINUS_SRC_ALPHA = 0x0303
    _GL_TEXTURE_2D = 0x0DE1

    def __init__(self) -> None:
        self.available = False
        self.last_error = ""
        self.program: QOpenGLShaderProgram | None = None
        self.loc: dict[str, int] = {}
        self.vao = QOpenGLVertexArrayObject()
        self.vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        # 战斗/通用场景纹理（相机或合成图）
        self.scene_tex: QOpenGLTexture | None = None
        self.scene_size = (0, 0)
        self.scene_key = 0
        self.batch: list[GlassDrawCmd] = []

    def initialize(self) -> bool:
        if self.available:
            return True
        program = QOpenGLShaderProgram()
        vertex = """
            #version 330
            layout(location = 0) in vec2 position;
            out vec2 vUv;
            void main() {
                vUv = position * 0.5 + vec2(0.5);
                gl_Position = vec4(position, 0.0, 1.0);
            }
        """
        # 全屏四边形 + 片元内用 gl_FragCoord 判断是否在圆角 rect 内并采样场景
        fragment = """
            #version 330
            in vec2 vUv;
            out vec4 fragColor;
            uniform sampler2D sceneTex;
            uniform vec2 uResolution;   // 物理像素
            uniform vec4 uRect;         // x,y,w,h Qt 顶左
            uniform float uRadius;
            uniform float uDisp;
            uniform float uAberr;
            uniform float uCoreAlpha;
            uniform float uHover;
            uniform float uEdge;
            uniform vec2 uPointer;      // 0-1 相对面板
            uniform vec4 uSceneUv;      // 场景纹理 UV: offset.xy + scale.zw（顶左）
            uniform int uHasScene;
            uniform int uPassMode;      // 0=blit 全屏 1=glass

            float roundedBoxSDF(vec2 p, vec2 b, float r) {
                vec2 q = abs(p) - b + vec2(r);
                return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
            }

            vec2 sceneUV(vec2 qt_px) {
                // qt_px 顶左；纹理采样：我们上传时 flip Y 与壁纸一致，v=0 在顶
                vec2 uv = qt_px / uResolution;
                uv = uSceneUv.xy + uv * uSceneUv.zw;
                return clamp(uv, vec2(0.001), vec2(0.999));
            }

            vec3 sampleBlur(vec2 uv, float px) {
                vec2 texel = vec2(px) / uResolution;
                vec3 c = texture(sceneTex, uv).rgb * 0.227;
                c += texture(sceneTex, uv + vec2(texel.x * 1.5, 0.0)).rgb * 0.194;
                c += texture(sceneTex, uv - vec2(texel.x * 1.5, 0.0)).rgb * 0.194;
                c += texture(sceneTex, uv + vec2(0.0, texel.y * 1.5)).rgb * 0.194;
                c += texture(sceneTex, uv - vec2(0.0, texel.y * 1.5)).rgb * 0.194;
                c += texture(sceneTex, uv + texel * 2.2).rgb * 0.08;
                c += texture(sceneTex, uv - texel * 2.2).rgb * 0.08;
                return c;
            }

            void main() {
                // gl_FragCoord 左下 → Qt 顶左
                vec2 qt = vec2(gl_FragCoord.x, uResolution.y - gl_FragCoord.y);
                if (uPassMode == 0) {
                    vec2 uv = sceneUV(qt);
                    fragColor = vec4(texture(sceneTex, uv).rgb, 1.0);
                    return;
                }
                vec2 center = uRect.xy + 0.5 * uRect.zw;
                vec2 halfSize = 0.5 * uRect.zw;
                float dist = roundedBoxSDF(qt - center, halfSize, uRadius);
                if (dist > 1.5) discard;

                float aa = 1.0 - smoothstep(-1.2, 1.2, dist);
                // 边缘因子：0 中心，1 边（liquid-glass edge mask）
                float edgeFactor = 1.0 - smoothstep(-uEdge * 0.15, uEdge * 0.95, -dist);
                edgeFactor = clamp(edgeFactor, 0.0, 1.0);

                vec3 col = vec3(0.12, 0.14, 0.18);
                if (uHasScene == 1) {
                    vec2 baseUv = sceneUV(qt);
                    // 中心 frosted blur
                    float blurPx = 1.2 + uDisp * 0.8 + uHover * 0.6;
                    vec3 frosted = sampleBlur(baseUv, blurPx);
                    // 边缘折射偏移（pointer + 法线近似）
                    vec2 fromC = (qt - center) / max(halfSize, vec2(1.0));
                    vec2 n = normalize(fromC + 1e-4);
                    vec2 ptr = (uPointer - vec2(0.5)) * 2.0;
                    vec2 refr = n * uDisp * (0.55 + 0.75 * edgeFactor) + ptr * uDisp * 0.25 * uHover;
                    refr *= edgeFactor;
                    vec2 uvR = sceneUV(qt + refr + vec2(uAberr, 0.0));
                    vec2 uvG = sceneUV(qt + refr);
                    vec2 uvB = sceneUV(qt + refr - vec2(uAberr, 0.0));
                    vec3 edgeCol = vec3(texture(sceneTex, uvR).r, texture(sceneTex, uvG).g, texture(sceneTex, uvB).b);
                    edgeCol = mix(edgeCol, sampleBlur(uvG, blurPx * 0.7), 0.35);
                    col = mix(frosted, edgeCol, edgeFactor * 0.85);
                }

                // 材料 tint（iOS 冷白）
                vec3 tint = vec3(0.96, 0.97, 1.0);
                col = mix(col, tint, 0.12 + 0.08 * uHover + 0.10 * (1.0 - edgeFactor));
                // 边缘高光
                float glare = pow(edgeFactor, 1.4) * (0.18 + 0.22 * uHover);
                col += vec3(glare);

                float alpha = aa * (uCoreAlpha + 0.12 * edgeFactor + 0.08 * uHover);
                alpha = clamp(alpha, 0.0, 0.92);
                fragColor = vec4(col * alpha, alpha);
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
        if not self.vao.create():
            self.last_error = "glass VAO failed"
            return False
        self.vao.bind()
        self.vbo.create()
        verts = struct.pack("8f", -1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
        self.vbo.bind()
        self.vbo.allocate(verts, len(verts))
        program.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, self._GL_FLOAT, 0, 2, 8)
        program.release()
        self.vbo.release()
        self.vao.release()
        names = (
            "sceneTex",
            "uResolution",
            "uRect",
            "uRadius",
            "uDisp",
            "uAberr",
            "uCoreAlpha",
            "uHover",
            "uEdge",
            "uPointer",
            "uSceneUv",
            "uHasScene",
            "uPassMode",
        )
        self.loc = {n: program.uniformLocation(n) for n in names}
        self.program = program
        self.available = True
        return True

    def clear_batch(self) -> None:
        self.batch.clear()

    def enqueue(self, cmd: GlassDrawCmd) -> None:
        if cmd.rect.width() >= 2 and cmd.rect.height() >= 2:
            self.batch.append(cmd)

    def upload_scene(self, image: QImage) -> bool:
        if image.isNull() or not self.available:
            return False
        key = int(image.cacheKey())
        prepared = image
        if prepared.format() != QImage.Format.Format_RGBA8888:
            prepared = prepared.convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = prepared.width(), prepared.height()
        if self.scene_tex is not None and self.scene_size == (w, h) and self.scene_key == key:
            return True
        # 同尺寸：优先 SubImage 更新，避免每帧 destroy/create
        if self.scene_tex is not None and self.scene_size == (w, h) and self.scene_tex.isCreated():
            try:
                # QImage bits() 在 PySide6 可用；失败则重建
                ptr = prepared.constBits()
                self.scene_tex.setData(
                    0,
                    0,
                    0,
                    w,
                    h,
                    QOpenGLTexture.PixelFormat.RGBA,
                    QOpenGLTexture.PixelType.UInt8,
                    ptr,
                )
                self.scene_key = key
                return True
            except Exception:
                pass
        if self.scene_tex is not None:
            self.scene_tex.destroy()
            self.scene_tex = None
        tex = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        tex.setMinificationFilter(QOpenGLTexture.Filter.Linear)
        tex.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        tex.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        tex.setData(prepared)
        self.scene_tex = tex
        self.scene_size = (w, h)
        self.scene_key = key
        return True

    def upload_bgr_numpy(self, functions, bgr, sequence: int) -> bool:
        """BGR uint8 HxWx3 → scene_tex（glTexSubImage 复用纹理，GPU 直传热路径）。

        避免每帧 QImage.copy + convertToFormat(RGBA) + 重建纹理。
        """
        if not self.available or bgr is None:
            return False
        try:
            import numpy as np  # type: ignore
        except Exception as exc:
            self.last_error = str(exc)
            return False
        if not isinstance(bgr, np.ndarray) or bgr.ndim != 3 or bgr.shape[2] < 3:
            return False
        arr = np.ascontiguousarray(bgr[:, :, :3], dtype=np.uint8)
        h, w = int(arr.shape[0]), int(arr.shape[1])
        if w < 2 or h < 2:
            return False
        # 持有缓冲到下一次上传，防止 GC
        self._bgr_hold = arr
        GL_TEXTURE_2D = 0x0DE1
        GL_UNPACK_ALIGNMENT = 0x0CF5
        GL_BGR = 0x80E0
        GL_UNSIGNED_BYTE = 0x1401
        GL_RGB = 0x1907
        try:
            if (
                self.scene_tex is None
                or self.scene_size != (w, h)
                or not self.scene_tex.isCreated()
            ):
                if self.scene_tex is not None:
                    self.scene_tex.destroy()
                    self.scene_tex = None
                tex = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
                tex.setFormat(QOpenGLTexture.TextureFormat.RGB8_UNorm)
                tex.setSize(w, h)
                tex.setAutoMipMapGenerationEnabled(False)
                tex.setMinificationFilter(QOpenGLTexture.Filter.Linear)
                tex.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
                tex.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
                tex.allocateStorage()
                self.scene_tex = tex
                self.scene_size = (w, h)
            tid = int(self.scene_tex.textureId())
            if functions is not None:
                functions.glBindTexture(GL_TEXTURE_2D, tid)
                functions.glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
                # 首次/每帧 SubImage 更新
                functions.glTexSubImage2D(
                    GL_TEXTURE_2D,
                    0,
                    0,
                    0,
                    w,
                    h,
                    GL_BGR,
                    GL_UNSIGNED_BYTE,
                    arr.ctypes.data,
                )
                functions.glBindTexture(GL_TEXTURE_2D, 0)
            else:
                # 无 functions 时退回 Qt setData
                self.scene_tex.setData(
                    0,
                    0,
                    0,
                    w,
                    h,
                    QOpenGLTexture.PixelFormat.BGR,
                    QOpenGLTexture.PixelType.UInt8,
                    arr.data,
                )
            self.scene_key = int(sequence) if sequence else id(arr)
            return True
        except Exception as exc:
            self.last_error = f"upload_bgr: {exc}"
            # 最后退路：经 QImage（仍比整页 CPU paint 好）
            try:
                from PySide6.QtGui import QImage

                img = QImage(arr.data, w, h, w * 3, QImage.Format.Format_BGR888).copy()
                return self.upload_scene(img)
            except Exception as exc2:
                self.last_error = f"upload_bgr fallback: {exc2}"
                return False

    def ensure_empty_scene(self, width: int, height: int) -> bool:
        """分配可被 glCopyTexImage2D 写入的场景纹理。"""
        if not self.available:
            return False
        width = max(1, int(width))
        height = max(1, int(height))
        if self.scene_tex is not None and self.scene_size == (width, height):
            return True
        if self.scene_tex is not None:
            self.scene_tex.destroy()
            self.scene_tex = None
        tex = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        tex.setFormat(QOpenGLTexture.TextureFormat.RGBA8_UNorm)
        tex.setSize(width, height)
        tex.setMinificationFilter(QOpenGLTexture.Filter.Linear)
        tex.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        tex.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        tex.allocateStorage()
        self.scene_tex = tex
        self.scene_size = (width, height)
        self.scene_key = -1
        return True

    def copy_framebuffer(self, functions, width: int, height: int) -> bool:
        """把当前 FBO 颜色缓冲拷到 scene_tex，玻璃采样与屏幕一致。"""
        if not self.ensure_empty_scene(width, height) or self.scene_tex is None:
            return False
        try:
            self.scene_tex.bind()
            # GL_RGBA = 0x1908
            functions.glCopyTexImage2D(self._GL_TEXTURE_2D, 0, 0x1908, 0, 0, max(1, width), max(1, height), 0)
            self.scene_tex.release()
            self.scene_key = -2
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def _mode_params(self, mode: str, surface: str, hover: float, press: float) -> tuple[float, float, float, float]:
        # disp, aberr, core_alpha, edge
        table = {
            "standard": (2.2, 1.6, 0.58, 10.0),
            "polar": (3.0, 2.2, 0.60, 12.0),
            "prominent": (3.8, 2.8, 0.62, 14.0),
            "tool": (1.4, 1.0, 0.48, 7.0),
        }
        disp, aberr, alpha, edge = table.get(mode, table["standard"])
        if surface == "sub":
            disp *= 0.75
            aberr *= 0.75
            alpha *= 0.90
        elif surface == "tool":
            disp, aberr, alpha, edge = table["tool"]
        motion = max(hover, press * 0.85)
        disp *= 0.75 + 0.55 * motion
        aberr *= 0.65 + 0.50 * motion
        alpha = min(0.88, alpha + 0.06 * motion)
        return disp, aberr, alpha, edge

    def draw_panel(
        self,
        functions,
        *,
        pixel_w: int,
        pixel_h: int,
        cmd: GlassDrawCmd,
        scene_texture: QOpenGLTexture | None = None,
        scene_uv: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
        dpr: float = 1.0,
    ) -> bool:
        if not self.available or self.program is None:
            return False
        tex = scene_texture or self.scene_tex
        rect = cmd.rect
        # 逻辑像素 → 物理像素
        x = float(rect.x()) * dpr
        y = float(rect.y()) * dpr
        w = float(rect.width()) * dpr
        h = float(rect.height()) * dpr
        radius = float(cmd.radius) * dpr
        disp, aberr, alpha, edge = self._mode_params(cmd.mode, cmd.surface, cmd.hover, cmd.press)
        disp *= dpr
        aberr *= dpr
        edge *= dpr

        try:
            functions.glViewport(0, 0, max(1, pixel_w), max(1, pixel_h))
            functions.glEnable(self._GL_BLEND)
            functions.glBlendFunc(self._GL_SRC_ALPHA, self._GL_ONE_MINUS_SRC_ALPHA)
            functions.glDisable(0x0B71)  # depth
            self.program.bind()
            has = 1 if tex is not None and tex.isCreated() else 0
            if has:
                tex.bind(0)
            self.program.setUniformValue1i(self.loc["sceneTex"], 0)
            self.program.setUniformValue(self.loc["uResolution"], QVector2D(float(pixel_w), float(pixel_h)))
            self.program.setUniformValue(self.loc["uRect"], QVector4D(x, y, w, h))
            self.program.setUniformValue1f(self.loc["uRadius"], float(radius))
            self.program.setUniformValue1f(self.loc["uDisp"], float(disp))
            self.program.setUniformValue1f(self.loc["uAberr"], float(aberr))
            self.program.setUniformValue1f(self.loc["uCoreAlpha"], float(alpha))
            self.program.setUniformValue1f(self.loc["uHover"], float(max(0.0, min(1.0, cmd.hover))))
            self.program.setUniformValue1f(self.loc["uEdge"], float(edge))
            self.program.setUniformValue(self.loc["uPointer"], QVector2D(cmd.pointer_x, cmd.pointer_y))
            self.program.setUniformValue(self.loc["uSceneUv"], QVector4D(*scene_uv))
            self.program.setUniformValue1i(self.loc["uHasScene"], has)
            self.program.setUniformValue1i(self.loc["uPassMode"], 1)
            self.vao.bind()
            functions.glDrawArrays(self._GL_TRIANGLE_STRIP, 0, 4)
            self.vao.release()
            if has and tex is not None:
                tex.release()
            self.program.release()
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def draw_batch(
        self,
        functions,
        *,
        pixel_w: int,
        pixel_h: int,
        scene_texture: QOpenGLTexture | None = None,
        scene_uv: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
        dpr: float = 1.0,
        commands: list[GlassDrawCmd] | None = None,
    ) -> int:
        cmds = commands if commands is not None else self.batch
        drawn = 0
        for cmd in cmds:
            if self.draw_panel(
                functions,
                pixel_w=pixel_w,
                pixel_h=pixel_h,
                cmd=cmd,
                scene_texture=scene_texture,
                scene_uv=scene_uv,
                dpr=dpr,
            ):
                drawn += 1
        return drawn

    def blit_scene_fullscreen(self, functions, *, pixel_w: int, pixel_h: int, scene_texture: QOpenGLTexture | None = None) -> bool:
        """将场景纹理铺满视口（战斗底图 / 回退）。"""
        tex = scene_texture or self.scene_tex
        if not self.available or self.program is None or tex is None or not tex.isCreated():
            return False
        # 复用玻璃 shader：全屏 rect + 无圆角 discard 边界
        cmd = GlassDrawCmd(
            rect=QRectF(0, 0, pixel_w, pixel_h),
            radius=0.0,
            mode="standard",
            surface="main",
            hover=0.0,
            press=0.0,
        )
        # 临时：用几乎不透明中心、零折射的全屏采样
        try:
            functions.glViewport(0, 0, max(1, pixel_w), max(1, pixel_h))
            functions.glDisable(self._GL_BLEND)
            self.program.bind()
            tex.bind(0)
            self.program.setUniformValue1i(self.loc["sceneTex"], 0)
            self.program.setUniformValue(self.loc["uResolution"], QVector2D(float(pixel_w), float(pixel_h)))
            self.program.setUniformValue(self.loc["uRect"], QVector4D(0.0, 0.0, float(pixel_w), float(pixel_h)))
            self.program.setUniformValue1f(self.loc["uRadius"], 0.0)
            self.program.setUniformValue1f(self.loc["uDisp"], 0.0)
            self.program.setUniformValue1f(self.loc["uAberr"], 0.0)
            self.program.setUniformValue1f(self.loc["uCoreAlpha"], 1.0)
            self.program.setUniformValue1f(self.loc["uHover"], 0.0)
            self.program.setUniformValue1f(self.loc["uEdge"], 0.0)
            self.program.setUniformValue(self.loc["uPointer"], QVector2D(0.5, 0.5))
            self.program.setUniformValue(self.loc["uSceneUv"], QVector4D(0.0, 0.0, 1.0, 1.0))
            self.program.setUniformValue1i(self.loc["uHasScene"], 1)
            self.program.setUniformValue1i(self.loc["uPassMode"], 0)
            self.vao.bind()
            functions.glDrawArrays(self._GL_TRIANGLE_STRIP, 0, 4)
            self.vao.release()
            tex.release()
            self.program.release()
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def release(self) -> None:
        if self.scene_tex is not None:
            self.scene_tex.destroy()
            self.scene_tex = None
        if self.vbo.isCreated():
            self.vbo.destroy()
        if self.vao.isCreated():
            self.vao.destroy()
        if self.program is not None:
            self.program.removeAllShaders()
        self.program = None
        self.available = False
        self.batch.clear()
