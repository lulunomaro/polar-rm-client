from __future__ import annotations

"""液态玻璃渲染器。

参数与语义对齐开源库 liquid-glass-react（Apple Liquid Glass for React）：
- displacementScale / blurAmount / saturation / aberrationIntensity / elasticity
- mode: standard | polar | prominent
- overLight: 亮底上更高模糊与更低对比
- 边缘折射 + 中心相对干净；边缘 chromatic-ish 叠色

实现为 QPainter 近似（非 SVG feDisplacementMap），但保持可辨认的 liquid-glass 观感。
"""

from dataclasses import dataclass, field
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QRadialGradient, QLinearGradient


@dataclass
class LiquidGlassSnapshot:
    image: QImage = field(default_factory=QImage)
    soft: QImage = field(default_factory=QImage)
    lens: QImage = field(default_factory=QImage)
    luminance: QImage = field(default_factory=QImage)
    draw_rect: QRectF = field(default_factory=QRectF)
    key: int = 0


@dataclass(frozen=True)
class GlassParams:
    """对齐 liquid-glass-react props 的归一化参数。"""

    displacement_scale: float = 48.0  # react 默认 ~25–70
    blur_amount: float = 0.08  # react 0.0625–0.1，越大越霜
    saturation: float = 1.25  # 140% ≈ 1.4，绘制时用略饱和叠色近似
    aberration_intensity: float = 2.0  # react 默认 2
    elasticity: float = 0.18  # 0=刚体，越高越“液”
    over_light: bool = False


# mode → base GlassParams 微调（对应 react mode）
_MODE_PRESETS: dict[str, GlassParams] = {
    "standard": GlassParams(displacement_scale=42.0, blur_amount=0.075, aberration_intensity=1.8, elasticity=0.15),
    "polar": GlassParams(displacement_scale=58.0, blur_amount=0.09, aberration_intensity=2.4, elasticity=0.22),
    "prominent": GlassParams(displacement_scale=70.0, blur_amount=0.10, aberration_intensity=2.8, elasticity=0.28),
    "tool": GlassParams(displacement_scale=28.0, blur_amount=0.055, aberration_intensity=1.2, elasticity=0.10),
}


class LiquidGlassRenderer:
    """liquid-glass-react 风格的稳定镜片渲染器。

    优先走 OpenGL（glass_gl.GlassGpuCompositor）；失败时回退 QPainter CPU。
    """

    def __init__(self) -> None:
        self.snapshots: dict[str, LiquidGlassSnapshot] = {}
        self.light_state: dict[tuple[str, int, int, int, int], float] = {}
        self.light_time: dict[tuple[str, int, int, int, int], float] = {}
        # GPU 路径由 PolarRMWindow 注入
        self.gpu = None  # GlassGpuCompositor | None
        self.gpu_scene_texture = None  # QOpenGLTexture | None
        self.gpu_scene_uv: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
        self.gpu_pixel_size: tuple[int, int] = (1, 1)
        self.gpu_dpr: float = 1.0
        self.prefer_gpu: bool = True

    def update(
        self,
        source: str,
        image: QImage,
        draw_rect: QRectF,
        *,
        soft: QImage | None = None,
        lens: QImage | None = None,
        quality: str = "full",
    ) -> None:
        if image.isNull() or draw_rect.isEmpty():
            return
        key = hash((
            int(image.cacheKey()),
            int(soft.cacheKey()) if soft and not soft.isNull() else 0,
            int(lens.cacheKey()) if lens and not lens.isNull() else 0,
            quality,
        ))
        previous = self.snapshots.get(source)
        if previous is not None and previous.key == key and previous.draw_rect == draw_rect:
            return
        # full 更高 soft 分辨率，贴近 react blurAmount 霜感
        if quality == "lite":
            base_w = min(280, max(140, image.width() // 4))
            lens_div = 2
        elif quality == "balanced":
            base_w = min(360, max(180, image.width() // 3))
            lens_div = 2
        else:
            base_w = min(520, max(220, image.width() // 2))
            lens_div = 2
        soft_image = soft if soft is not None and not soft.isNull() else image.scaledToWidth(
            base_w, Qt.TransformationMode.SmoothTransformation
        )
        lens_image = lens if lens is not None and not lens.isNull() else soft_image.scaledToWidth(
            max(128, soft_image.width() // lens_div), Qt.TransformationMode.SmoothTransformation
        )
        luminance = image.scaled(56, 32, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        self.snapshots[source] = LiquidGlassSnapshot(
            QImage(), soft_image.copy(), lens_image.copy(), luminance.copy(), QRectF(draw_rect), key
        )

    def draw(
        self,
        painter: QPainter,
        rect: QRectF,
        radius: float,
        *,
        source: str,
        mode: str = "standard",
        surface: str = "main",
        hover: float = 0.0,
        press: float = 0.0,
        pointer: QPointF | None = None,
        lite: bool = False,
        quality: str = "full",
    ) -> None:
        hover = max(0.0, min(1.0, hover))
        press = max(0.0, min(1.0, press))
        # ---- GPU 路径（OpenGL）----
        if (
            self.prefer_gpu
            and self.gpu is not None
            and getattr(self.gpu, "available", False)
            and quality != "lite"
        ):
            try:
                from .glass_gl import GlassDrawCmd

                ptr = pointer or rect.center()
                px = (ptr.x() - rect.left()) / max(1.0, rect.width())
                py = (ptr.y() - rect.top()) / max(1.0, rect.height())
                cmd = GlassDrawCmd(
                    rect=QRectF(rect),
                    radius=float(radius),
                    mode=mode if mode in _MODE_PRESETS else "standard",
                    surface=surface,
                    hover=hover,
                    press=press,
                    pointer_x=max(0.0, min(1.0, px)),
                    pointer_y=max(0.0, min(1.0, py)),
                )
                painter.beginNativePainting()
                try:
                    from PySide6.QtGui import QOpenGLContext

                    ctx = QOpenGLContext.currentContext()
                    functions = ctx.functions() if ctx is not None else None
                    if functions is not None:
                        pw, ph = self.gpu_pixel_size
                        ok = self.gpu.draw_panel(
                            functions,
                            pixel_w=pw,
                            pixel_h=ph,
                            cmd=cmd,
                            scene_texture=self.gpu_scene_texture,
                            scene_uv=self.gpu_scene_uv,
                            dpr=self.gpu_dpr,
                        )
                        if ok:
                            return
                finally:
                    painter.endNativePainting()
            except Exception:
                pass  # 回退 CPU

        snapshot = self.snapshots.get(source)
        params = _MODE_PRESETS.get(mode, _MODE_PRESETS["standard"])
        if surface == "tool":
            params = _MODE_PRESETS["tool"]
        elif surface == "sub":
            params = GlassParams(
                displacement_scale=params.displacement_scale * 0.72,
                blur_amount=params.blur_amount * 0.85,
                saturation=params.saturation * 0.92,
                aberration_intensity=params.aberration_intensity * 0.75,
                elasticity=params.elasticity * 0.8,
                over_light=params.over_light,
            )

        # 亮背景：提高霜感（react overLight）
        light = self._light_amount(source, snapshot, rect)
        over_light = light > 0.45
        blur_boost = 1.35 if over_light else 1.0
        core_alpha = 0.42 if over_light else 0.55
        if surface == "sub":
            core_alpha *= 0.88
        elif surface == "tool":
            core_alpha *= 0.78

        # 边缘厚度（react：边缘 mask 在外圈，中心干净）
        edge_ratio = 0.10 if mode == "prominent" else 0.08 if mode == "polar" else 0.065
        if surface == "tool":
            edge_ratio = 0.05
        edge = max(2.5, min(min(rect.width(), rect.height()) * edge_ratio, 14.0))

        # displacement：像素尺度，对齐 displacementScale 的“弯边”感；静止也保留基础量
        base_disp = params.displacement_scale * 0.035  # 48 → ~1.7px
        if quality == "lite" or lite:
            base_disp *= 0.65
        motion = max(hover, press * 0.9)
        elasticity = params.elasticity
        displacement = base_disp * (0.72 + 0.55 * motion)  # 静止就有折射，hover 加强
        aberration = params.aberration_intensity * (0.55 + 0.55 * motion)

        outer = QPainterPath()
        outer.addRoundedRect(rect, radius, radius)
        inner_rect = rect.adjusted(edge, edge, -edge, -edge)
        inner = QPainterPath()
        inner.addRoundedRect(inner_rect, max(1.0, radius - edge), max(1.0, radius - edge))
        edge_path = outer.subtracted(inner)

        pointer = pointer or rect.center()
        nx = max(-1.0, min(1.0, (pointer.x() - rect.center().x()) / max(1.0, rect.width() * 0.5)))
        ny = max(-1.0, min(1.0, (pointer.y() - rect.center().y()) / max(1.0, rect.height() * 0.5)))

        # elasticity：liquid 形变（react active/hover scale）
        visual_rect = QRectF(rect)
        el = elasticity * (0.35 + 0.65 * motion)
        if motion > 0.0:
            visual_rect.adjust(-0.6 * el * 8, -0.6 * el * 8, 0.6 * el * 8, 0.6 * el * 8)
            visual_rect.translate(nx * el * 6.0, ny * el * 4.5)
        if press > 0.0:
            visual_rect.setWidth(visual_rect.width() * (1.0 - 0.014 * press))
            visual_rect.setHeight(visual_rect.height() * (1.0 - 0.014 * press))
            visual_rect.moveCenter(rect.center())

        # --- 阴影（Apple material 感）---
        if surface != "tool" or hover > 0.2:
            painter.save()
            shadow = QPainterPath()
            shadow_rect = rect.adjusted(0, 1.5, 0, 3.0)
            shadow.addRoundedRect(shadow_rect, radius, radius)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, int(22 + 10 * hover) if surface == "main" else 14))
            painter.drawPath(shadow)
            painter.restore()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if snapshot is not None and not snapshot.soft.isNull():
            # 中心：frost 层（blur + 半透明）
            source_rect = self._source_rect(snapshot, visual_rect, snapshot.soft)
            painter.setClipPath(outer)
            # 系统材料 fill：冷白半透明叠在 soft 上
            painter.setOpacity(painter.opacity() * core_alpha * blur_boost)
            painter.drawImage(visual_rect, snapshot.soft, source_rect)
            # 饱和/霜面叠色（近似 saturate）
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SoftLight)
            tint = QColor(255, 255, 255, int(18 + params.saturation * 8 + light * 12))
            if over_light:
                tint = QColor(255, 255, 255, int(28 + light * 18))
            painter.fillRect(visual_rect, tint)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            # 边缘：折射 + 色散（react 边缘 mask 区）
            use_lens = not snapshot.lens.isNull() and not source_rect.isEmpty()
            if use_lens and quality != "lite":
                lens_source = self._source_rect(snapshot, visual_rect, snapshot.lens)
                # 基础弯边
                warp = max(1.0, displacement)
                warped = lens_source.adjusted(-warp, -warp, warp, warp)
                painter.setClipPath(edge_path)
                painter.setOpacity(painter.opacity() * (0.38 + 0.14 * hover + 0.10 * light))
                painter.drawImage(visual_rect, snapshot.lens, warped)

                # chromatic-ish：RGB 轻微分离叠 Screen（对齐 feBlend screen）
                ab = max(0.6, aberration * 0.55)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
                painter.setOpacity(0.14 + 0.04 * hover)
                painter.drawImage(
                    visual_rect.adjusted(-ab, 0, -ab, 0),
                    snapshot.lens,
                    warped.adjusted(-ab * 0.4, 0, ab * 0.4, 0),
                )
                painter.setOpacity(0.12 + 0.04 * hover)
                painter.drawImage(
                    visual_rect.adjusted(ab, 0, ab, 0),
                    snapshot.lens,
                    warped.adjusted(ab * 0.4, 0, -ab * 0.4, 0),
                )
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

                # 边缘高光环
                painter.setClipPath(edge_path)
                painter.setBrush(QColor(255, 255, 255, int(10 + aberration * 6 + light * 14 + hover * 12)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPath(edge_path)
            elif use_lens:
                # lite：仍画一层轻 edge 折射，不全删
                lens_source = self._source_rect(snapshot, visual_rect, snapshot.lens)
                warped = lens_source.adjusted(-1.0, -1.0, 1.0, 1.0)
                painter.setClipPath(edge_path)
                painter.setOpacity(painter.opacity() * 0.28)
                painter.drawImage(visual_rect, snapshot.lens, warped)
        else:
            painter.setClipPath(outer)
            # 无背景采样时的 fallback 材料（iOS 风格）
            painter.fillRect(rect, QColor(255, 255, 255, 28 if surface == "main" else 18))
        painter.restore()

        # overLight 雾化（亮底上更 Apple）
        if light > 0.08:
            fog_size = max(16.0, min(rect.width(), rect.height()) * 0.30)
            fog_center = self._edge_glint_center(rect, nx, ny, edge)
            fog = QRadialGradient(fog_center, fog_size)
            fog.setColorAt(0.0, QColor(255, 255, 255, int(10 + light * 28)))
            fog.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.save()
            painter.setClipPath(outer)
            painter.setBrush(fog)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(fog_center.x() - fog_size, fog_center.y() - fog_size, fog_size * 2.0, fog_size * 2.0))
            painter.restore()

        # 对角 glare（liquid-glass 边缘高光随光/鼠标）
        if surface != "tool" or hover > 0.15:
            glint_center = self._edge_glint_center(rect, nx if hover > 0.05 else -0.55, ny if hover > 0.05 else -0.55, edge)
            glint_radius = max(12.0, min(30.0, edge * (3.0 + hover * 1.5)))
            glint = QRadialGradient(glint_center, glint_radius)
            glint.setColorAt(0.0, QColor(255, 255, 255, int(22 + light * 20 + hover * 24)))
            glint.setColorAt(0.45, QColor(255, 255, 255, int(8 + light * 10 + hover * 10)))
            glint.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.save()
            painter.setClipPath(edge_path if not edge_path.isEmpty() else outer)
            painter.setBrush(glint)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QRectF(glint_center.x() - glint_radius, glint_center.y() - glint_radius, glint_radius * 2.0, glint_radius * 2.0)
            )
            painter.restore()

        # 双描边：外亮内暗（iOS / liquid-glass 边）
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, int(48 + light * 30 + hover * 22)), 0.9))
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QPen(QColor(0, 0, 0, int(22 + light * 10)), 0.55))
        painter.drawRoundedRect(rect.adjusted(0.8, 0.8, -0.8, -0.8), max(1.0, radius - 0.8), max(1.0, radius - 0.8))

    @staticmethod
    def _edge_glint_center(rect: QRectF, nx: float, ny: float, edge: float) -> QPointF:
        if abs(nx) >= abs(ny):
            x = rect.right() - edge * 0.45 if nx >= 0.0 else rect.left() + edge * 0.45
            y = rect.center().y() + ny * rect.height() * 0.27
        else:
            x = rect.center().x() + nx * rect.width() * 0.27
            y = rect.bottom() - edge * 0.45 if ny >= 0.0 else rect.top() + edge * 0.45
        return QPointF(x, y)

    def _source_rect(self, snapshot: LiquidGlassSnapshot, rect: QRectF, image: QImage) -> QRectF:
        draw = snapshot.draw_rect
        return QRectF(
            (rect.left() - draw.left()) / max(1.0, draw.width()) * image.width(),
            (rect.top() - draw.top()) / max(1.0, draw.height()) * image.height(),
            rect.width() / max(1.0, draw.width()) * image.width(),
            rect.height() / max(1.0, draw.height()) * image.height(),
        ).intersected(QRectF(0, 0, image.width(), image.height()))

    def _light_amount(self, source: str, snapshot: LiquidGlassSnapshot | None, rect: QRectF) -> float:
        if snapshot is None or snapshot.luminance.isNull() or snapshot.draw_rect.isEmpty():
            return 0.0
        draw = snapshot.draw_rect
        px = int(max(0, min(snapshot.luminance.width() - 1, (rect.center().x() - draw.left()) / max(1.0, draw.width()) * snapshot.luminance.width())))
        py = int(max(0, min(snapshot.luminance.height() - 1, (rect.center().y() - draw.top()) / max(1.0, draw.height()) * snapshot.luminance.height())))
        total = 0.0
        count = 0
        for y in range(max(0, py - 1), min(snapshot.luminance.height(), py + 2)):
            for x in range(max(0, px - 1), min(snapshot.luminance.width(), px + 2)):
                c = snapshot.luminance.pixelColor(x, y)
                total += (0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()) / 255.0
                count += 1
        luminance = total / max(1, count)
        key = (source, int(rect.x() // 24), int(rect.y() // 24), int(rect.width() // 24), int(rect.height() // 24))
        previous = self.light_state.get(key, 0.0)
        threshold = 0.58 if previous < 0.5 else 0.50
        target = max(0.0, min(1.0, (luminance - threshold) / max(0.01, 1.0 - threshold)))
        now = time.perf_counter()
        dt = min(0.15, max(0.0, now - self.light_time.get(key, now)))
        amount = previous + (target - previous) * (1.0 - math.exp(-dt / 0.15))
        self.light_state[key] = amount
        self.light_time[key] = now
        return amount
