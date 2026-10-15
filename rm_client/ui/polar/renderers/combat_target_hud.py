"""阶段 2a 渲染器提取：战斗目标 HUD 族（D9）→ CombatTargetHudMixin。

（REFACTOR_BLUEPRINT §5 阶段 2 表 2a 行 / §5.6 冻结面 / §6 性能红线）

迁移范围（以 grep 重定位后的实际行号为准，蓝图原行号已因多轮修复漂移）：
- ``paint_combat_target_overlay`` / ``paint_gpu_target_hud_layer``（原 ~3102–3206）
- ``combat_target_visual`` / ``draw_combat_target_arc`` / ``draw_combat_target_hud`` /
  ``draw_combat_target_hud_layer`` / ``combat_health_color`` / ``combat_heat_color`` /
  ``draw_combat_target_card``（原 ~8187–8473）
- ``draw_target_hud_by_style`` / ``draw_target_round_compact`` / ``draw_target_bar_only`` /
  ``target_ratio`` / ``target_short_name`` / ``target_compact_info`` / ``draw_target_card``
  （原 ~11906–11993）

纪律：
- 方法体**零修改**（含注释与空行），纯移动进本 Mixin；
- ``combat_hud_animator = CombatHudAnimator()`` 的接线仍在
  ``PolarRMWindow.__init__`` 原位置（Mixin 只承载方法，不承载构造）；
  本模块只引入动画器所需的纯函数 ``ring_end`` 与常量 ``VALID_COMBAT_HUD_VISUALS``；
- D9 的 hud_* 风格 getter（hud_accent/hud_clean/hud_surface/hud_panel_outline/
  hud_density/target_hud_style 等）被 D6/D10/D12 跨域调用（蓝图 §4 依赖要点 4，
  属"主题服务"），**不在本阶段范围**，仍留在 polar_app.py；本 Mixin 经 ``self``
  继续访问它们，调用面不变；
- ``_combat_hud_last_time`` / ``_combat_hud_frame_index`` 状态字段仍在
  ``PolarRMWindow.__init__`` 中初始化（原语句位置不动）。

冻结面纪律：与阶段 1（text_metrics.py 底部先例）一致——api_freeze_test 对方法类
成员比对 ``__qualname__``（基线值形如 "PolarRMWindow.<name>"），故在本模块底部把
全部迁入方法的 ``__qualname__`` 钉回 ``PolarRMWindow.<name>``，使 ``window.<name>``
签名与迁移前 bit 级一致。
"""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPicture,
    QPen,
    QRadialGradient,
)

# 相对导入层级：rm_client/ui/polar/renderers/ → .... = rm_client、... = rm_client.ui
from ....combat_hud import VALID_COMBAT_HUD_VISUALS, ring_end
from ....model import Module
from ....vision import VisionResult, VisionTarget
from ...animation import Easings
from ...layout import HudAnchorStack
from ...paint import draw_bar, glow_rect, rounded_rect
from ...theme import THEME, color
from ...jello_locale import chinese_enabled
from ...hud_locale import hud_text
from ...vision_motion import TargetHudPresenceFrame

__all__ = [
    "CombatTargetHudMixin",
]


class CombatTargetHudMixin:
    """D9 战斗目标 HUD 族绘制（CPU QPainter）+ combat_hud_animator 接线方法。"""

    def paint_combat_target_overlay(
        self,
        p: QPainter,
        width: int,
        height: int,
        display_result: VisionResult | None,
        now: float,
    ) -> None:
        """Draw the center target HUD once, directly into its destination FBO."""
        valid_target = self.has_valid_combat_target(display_result)
        presence = self.target_hud_presence.update(now, valid_target)
        combat_target_hud = self.registry.get("CombatTargetHUD")
        if combat_target_hud and combat_target_hud.enabled and presence.visible:
            self.draw_combat_target_hud_layer(
                p,
                width,
                height,
                combat_target_hud,
                presence=presence,
            )
            return
        crosshair = self.registry.get("Crosshair")
        if crosshair and crosshair.enabled:
            self.draw_crosshair(p, width, height)

    def paint_gpu_target_hud_layer(
        self,
        functions,
        width: int,
        height: int,
        ratio: float,
    ) -> bool:
        """Rasterize the center ring into a tight CPU patch, then sub-image upload.

        Design (same as ``upload_dynamic_image``): keep TargetHUD on its isolated
        ``target_fbo``, never full-screen CPU frames.  Qt's OpenGL paint engine
        can leave a semi-transparent rectangular atlas around antialiased arcs;
        exact ``glTexSubImage2D`` replace on a small ROI removes that residue
        without tanking frame rate.

        While ClickGUI / economy is open the topmost targetTex is cleared and
        the ring is painted into the dynamic layer *under* the menu instead
        (see paint_hud_motion_overlay include_target_hud). targetTex is always
        composited above dynamic, so leaving it live would cover panel text.
        """
        if self.menu_overlay_active():
            return self.gpu_compositor.clear_target_layer(functions)

        if not self.gpu_compositor.clear_target_layer(functions):
            return False

        scale = self.get_hud_scale(width, height)
        logical_width = max(1, int(width / scale))
        logical_height = max(1, int(height / scale))
        module = self.registry.get("CombatTargetHUD")
        visual = self.combat_target_visual(module)
        show_bars = self.setting_bool(module, "Show Bars", False) if module else False
        combat_scale = 1.65 if visual == "Classic" else 2.35
        # Ring outer + stroke + clear/enhanced radial plate (blueR + redW/2 + 10).
        ring_extent = 16.0 * combat_scale + 5.0 * combat_scale + 14.0
        if visual != "Classic":
            plate_extent = 16.0 * combat_scale + (5.0 * combat_scale * 0.92) * 0.5 + 12.0
            ring_extent = max(ring_extent, plate_extent)
        if visual == "Classic":
            half_logical = max(ring_extent, 78.0)
        elif show_bars:
            half_logical = max(ring_extent, 130.0)
        else:
            half_logical = ring_extent
        # Presence scale can breathe slightly above 1.0 while fading in.
        half_logical *= 1.2

        pixel_scale = max(0.001, float(scale) * float(ratio))
        roi_px = int(math.ceil(half_logical * 2.0 * pixel_scale))
        roi_px = max(64, min(roi_px, 512))
        roi_px = (roi_px + 3) // 4 * 4

        # Resolve presence once before selecting the rendering path. Only CS
        # primitives are recorded; target HUD text retains direct Qt rasterization.
        now = time.perf_counter()
        result = self.interpolated_vision_result(now)
        presence = self.target_hud_presence.update(now, self.has_valid_combat_target(result))
        target_visible = bool(module and module.enabled and presence.visible)
        crosshair = self.registry.get("Crosshair")
        style = crosshair.get_setting("Style") if crosshair is not None else None
        if not target_visible and crosshair is not None and crosshair.enabled and style is not None and style.value == "CS2":
            # The ring-sized central ROI clips large CS profiles. Record one
            # CS primitive draw in physical coordinates.
            # QPicture stores vector commands, not a viewport-sized pixel image.
            fb_w = max(1, int(round(float(width) * float(ratio))))
            fb_h = max(1, int(round(float(height) * float(ratio))))
            picture = QPicture()
            painter = QPainter(picture)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.translate(fb_w * 0.5, fb_h * 0.5)
            painter.scale(pixel_scale, pixel_scale)
            painter.translate(-logical_width * 0.5, -logical_height * 0.5)
            self.draw_crosshair(painter, logical_width, logical_height)
            painter.end()
            bounds = picture.boundingRect()
            if bounds.isEmpty():
                return True
            bounds = bounds.adjusted(-2, -2, 2, 2).intersected(QRect(0, 0, fb_w, fb_h))
            # One normal crosshair is a tiny patch. Extreme profiles use bounded
            # tiles, never an unbounded/full-frame QImage or a new GPU texture.
            # Two guard pixels prevent Qt's aliased curve clipper from
            # changing a boundary pixel on adjacent independently drawn tiles.
            for y in range(bounds.top(), bounds.bottom() + 1, 508):
                for x in range(bounds.left(), bounds.right() + 1, 508):
                    tile_w = min(508, bounds.right() - x + 1)
                    tile_h = min(508, bounds.bottom() - y + 1)
                    image = QImage(tile_w + 4, tile_h + 4, QImage.Format.Format_ARGB32_Premultiplied)
                    if image.isNull():
                        return False
                    image.fill(0)
                    painter = QPainter(image)
                    painter.drawPicture(QPointF(2 - x, 2 - y), picture)
                    painter.end()
                    # A huge gap/circle may put all primitives offscreen, or
                    # leave only a thin arm in a tile. Avoid uploading and
                    # sanitizing transparent full-frame-equivalent patches.
                    pixels = np.frombuffer(image.constBits(), dtype=np.uint32)
                    pixels = pixels.reshape(image.height(), image.bytesPerLine() // 4)
                    core = pixels[2:tile_h + 2, 2:tile_w + 2]
                    rows = np.flatnonzero(core.any(axis=1))
                    if not rows.size:
                        continue
                    cols = np.flatnonzero(core.any(axis=0))
                    left, right = max(0, int(cols[0]) - 2), min(tile_w, int(cols[-1]) + 3)
                    top, bottom = max(0, int(rows[0]) - 2), min(tile_h, int(rows[-1]) + 3)
                    tile = image.copy(left + 2, top + 2, right - left, bottom - top)
                    if tile.isNull() or not self.gpu_compositor.upload_target_region(functions, tile, x + left, y + top):
                        return False
            return True
        image = QImage(roi_px, roi_px, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        # Map full logical HUD space so the screen center lands on the patch center.
        painter.translate(roi_px * 0.5, roi_px * 0.5)
        painter.scale(pixel_scale, pixel_scale)
        painter.translate(-logical_width * 0.5, -logical_height * 0.5)
        if target_visible:
            self.draw_combat_target_hud_layer(
                painter, logical_width, logical_height, module, presence=presence,
            )
        elif crosshair is not None and crosshair.enabled:
            self.draw_crosshair(painter, logical_width, logical_height)
        painter.end()

        fb_w = max(1, int(round(float(width) * float(ratio))))
        fb_h = max(1, int(round(float(height) * float(ratio))))
        upload_x = int(round(fb_w * 0.5 - roi_px * 0.5))
        upload_y = int(round(fb_h * 0.5 - roi_px * 0.5))
        upload_x = max(0, min(upload_x, max(0, fb_w - roi_px)))
        upload_y = max(0, min(upload_y, max(0, fb_h - roi_px)))
        return self.gpu_compositor.upload_target_region(functions, image, upload_x, upload_y)
    def combat_target_visual(self, module: Module | None = None) -> str:
        module = module or self.registry.get("CombatTargetHUD")
        setting = module.get_setting("Visual") if module else None
        value = str(setting.value) if setting else "Clear"
        return value if value in VALID_COMBAT_HUD_VISUALS else "Clear"

    def draw_combat_target_arc(
        self,
        p: QPainter,
        center: QPointF,
        radius: float,
        start_deg: float,
        end_deg: float,
        arc_color: QColor,
        width: float,
        *,
        glow: float = 0.0,
        alpha: float | None = None,
    ) -> None:
        """Jar-style progress arc (visual-demo drawArc).

        Main stroke uses butt/FlatCap so 15/16 red/blue stay interleaved.
        Enhanced glow is a softer round-cap underlay (demo: width+glow @ α0.2).
        """
        if end_deg - start_deg < 0.05:
            return
        stroke = QColor(arc_color)
        if alpha is not None:
            stroke.setAlphaF(max(0.0, min(1.0, float(alpha) * stroke.alphaF())))
        rect = QRectF(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0)
        span = -int(round((end_deg - start_deg) * 16.0))
        start16 = int(round(start_deg * 16.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if glow > 0.0:
            glow_color = QColor(stroke)
            glow_color.setAlphaF(max(0.0, min(1.0, glow_color.alphaF() * 0.20)))
            glow_pen = QPen(glow_color, width + float(glow))
            glow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(glow_pen)
            p.drawArc(rect, start16, span)
        pen = QPen(stroke, width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawArc(rect, start16, span)

    def draw_combat_target_hud(
        self,
        p: QPainter,
        width: int,
        height: int,
        module: Module | None = None,
        *,
        presence: TargetHudPresenceFrame | None = None,
    ) -> None:
        if presence is not None and not presence.visible:
            return
        visual = self.combat_target_visual(module)
        # Prefer display frame_dt (stable 60–144 Hz) over paint-to-paint delta so
        # approachJar / heatGhost match visual-demo rAF pacing. Fall back only
        # when frame_dt is unavailable (offline raster tools).
        now = float(self.gui_motion.now)
        frame_dt = float(getattr(self, "frame_dt", 0.0) or 0.0)
        frame_index = int(getattr(self, "_ui_frame_index", 0))
        if frame_index == self._combat_hud_frame_index:
            dt = 0.0
        elif frame_dt > 1e-6:
            dt = max(0.0, min(0.05, frame_dt))
        else:
            dt = max(0.0, min(0.05, now - self._combat_hud_last_time))
        self._combat_hud_frame_index = frame_index
        self._combat_hud_last_time = now
        aim_override = None
        if presence is not None and presence.phase in {"Grace10s", "Fading"}:
            aim_override = 0.0
        frame = self.combat_hud_animator.update(
            self.state,
            dt,
            visual,
            aim_override=aim_override,
        )
        show_bars = self.setting_bool(module, "Show Bars", False)
        show_status_line = self.setting_bool(module, "Show Status Line", True)
        scale = 1.65 if visual == "Classic" else 2.35
        enhanced = visual == "Enhanced"
        center = QPointF(width / 2.0 + 1.0, height / 2.0 + 1.0)
        red_radius = 15.0 * scale
        blue_radius = 16.0 * scale
        red_width = 5.0 * scale * (0.92 if enhanced else 1.0)
        blue_width = 3.0 * scale * (0.92 if enhanced else 1.0)
        health = max(0.0, min(100.0, frame.self_anim))
        heat = max(0.0, min(100.0, frame.heat_anim))
        health_color = self.combat_health_color(health, visual)
        heat_color = self.combat_heat_color(heat, visual)

        p.save()
        if presence is not None:
            p.setOpacity(p.opacity() * presence.opacity)
            p.translate(center)
            p.scale(presence.scale, presence.scale)
            p.translate(-center)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if visual != "Classic":
            # visual-demo clear/enhanced: circular radial plate + one gray track.
            # Circle only (no rect fill) so the GPU ROI stays free of square edges.
            plate_r = blue_radius + red_width * 0.5 + 10.0
            plate = QRadialGradient(center, plate_r)
            plate.setColorAt(0.0, color("#140e0a", 0.55))
            plate.setColorAt(0.72, color("#140e0a", 0.18))
            plate.setColorAt(1.0, color("#140e0a", 0.0))
            plate_path = QPainterPath()
            plate_path.addEllipse(center, plate_r, plate_r)
            p.fillPath(plate_path, QBrush(plate))
            track_pen = QPen(color("#ffffff", 0.10), max(red_width, blue_width) + 1.0)
            track_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(track_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            track_radius = (red_radius + blue_radius) * 0.5
            p.drawEllipse(center, track_radius, track_radius)

        # visual-demo drawRmHud order: ghost → red solid → blue solid.
        # Cap end at 180+360 so a full ring never overshoots (demo Math.min).
        health_end = min(ring_end(health), 180.0 + 360.0)
        heat_end = min(ring_end(heat), 180.0 + 360.0)
        if enhanced and health > 0.5 and frame.self_ghost > health + 0.5:
            self.draw_combat_target_arc(
                p, center, red_radius, health_end, min(ring_end(frame.self_ghost), 180.0 + 360.0),
                color("#ffdcb4", 0.45), red_width, alpha=0.6,
            )
        if enhanced and heat > 0.5 and frame.heat_ghost > heat + 0.5:
            self.draw_combat_target_arc(
                p, center, blue_radius, heat_end, min(ring_end(frame.heat_ghost), 180.0 + 360.0),
                color("#b4d2ff", 0.45), blue_width, alpha=0.6,
            )
        if health > 0.5:
            self.draw_combat_target_arc(
                p, center, red_radius, 180.0, health_end, health_color, red_width,
                glow=3.0 if enhanced else 0.0,
            )
        if heat > 0.5:
            self.draw_combat_target_arc(
                p, center, blue_radius, 180.0, heat_end, heat_color, blue_width,
                glow=3.0 if enhanced else 0.0,
            )

        # Ring-center reticle only: white = idle (no aimbot), red = TRACK/LOCK.
        aim_on = frame.aim_anim >= 40.0
        crosshair_color = color("#ff1a1a") if aim_on else color("#ffffff", 0.85)
        crosshair_pen = QPen(crosshair_color, 1.35)
        crosshair_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(crosshair_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for start, end in (
            ((-9.0, 0.0), (-3.0, 0.0)),
            ((3.0, 0.0), (9.0, 0.0)),
            ((0.0, -9.0), (0.0, -3.0)),
            ((0.0, 3.0), (0.0, 9.0)),
        ):
            p.drawLine(center + QPointF(*start), center + QPointF(*end))

        if visual == "Classic":
            health_text = f"HP {float(self.state.current_health):.1f}"
            heat_text = f"HEAT {heat:.0f}%"
            self.draw_text(p, QRectF(center.x() - 70, center.y() + 22, 140, 14), hud_text(health_text, chinese_enabled(self.registry)), self.font("poppins_medium", 7.2), color("#ffffff"), Qt.AlignmentFlag.AlignCenter)
            self.draw_text(p, QRectF(center.x() - 70, center.y() + 34, 140, 14), hud_text(heat_text, chinese_enabled(self.registry)), self.font("poppins_medium", 6.8), heat_color, Qt.AlignmentFlag.AlignCenter)
        elif show_bars:
            self.draw_combat_target_card(
                p,
                width,
                height,
                visual,
                frame,
                health,
                heat,
                show_status_line=show_status_line,
            )
        p.restore()

    def draw_combat_target_hud_layer(
        self,
        p: QPainter,
        width: int,
        height: int,
        module: Module,
        *,
        presence: TargetHudPresenceFrame | None = None,
    ) -> None:
        """Compatibility path: draw vectors directly, never an image quad."""
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.draw_combat_target_hud(
            p,
            width,
            height,
            module,
            presence=presence,
        )
        p.restore()

    def combat_health_color(self, value: float, visual: str) -> QColor:
        if visual == "Classic":
            return color("#ff0000")
        if value > 55.0:
            return color("#ff4d5a")
        if value > 28.0:
            return color("#ff8a2b")
        return color("#ff1a1a")

    def combat_heat_color(self, value: float, visual: str) -> QColor:
        """Outer heat ring tint by heat %: 青 → 中蓝 → 深蓝 → 橙.

        Classic stays pure jar blue. Clear/Enhanced use four steps so low heat
        reads cyan/blue and only high heat warms to orange (never HP-red).
        """
        if visual == "Classic":
            return color("#0000ff")
        # 0–25 青 · 25–50 中蓝 · 50–75 深蓝 · 75–100 橙
        if value < 25.0:
            return color("#5ec8ff")  # 青
        if value < 50.0:
            return color("#3d9eff")  # 中蓝
        if value < 75.0:
            return color("#2563eb")  # 深蓝
        return color("#ffb347")  # 橙（高温警示，区别于 HP 红）

    def draw_combat_target_card(
        self,
        p: QPainter,
        width: int,
        height: int,
        visual: str,
        frame: Any,
        health: float,
        heat: float,
        *,
        show_status_line: bool,
    ) -> None:
        enhanced = visual == "Enhanced"
        card_w = 170.0
        card_h = 72.0 if show_status_line else 64.0
        rect = QRectF(width / 2.0 - card_w / 2.0, height / 2.0 + 52.0, card_w, card_h)
        if enhanced:
            for spread, alpha in ((5.0, 0.08), (2.5, 0.14)):
                p.setPen(QPen(color("#ff9f43", alpha), spread))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect, 10.0, 10.0)
        card_path = QPainterPath()
        card_path.addRoundedRect(rect, 10.0, 10.0)
        p.fillPath(card_path, color("#120e0c", 0.90))
        p.setPen(QPen(color("#ff9f43", 0.55) if enhanced else color("#ffffff", 0.12), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(card_path)
        pad = 10.0
        aim_label = "LOCK" if frame.aim_anim >= 80.0 else ("TRACK" if frame.aim_anim >= 40.0 else "IDLE")
        self.draw_text(p, QRectF(rect.left() + pad, rect.top() + 5, 95, 15), hud_text(f"AIM  {aim_label}", chinese_enabled(self.registry)), self.font("poppins_medium", 7.2), color("#ffffff"))
        self.draw_text(p, QRectF(rect.right() - 50, rect.top() + 5, 40, 15), hud_text(f"{frame.aim_anim:.0f}%", chinese_enabled(self.registry)), self.font("poppins_medium", 6.8), color("#ffc896", 0.90), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        aim_bar = QRectF(rect.left() + pad, rect.top() + 24, rect.width() - pad * 2.0, 8.0)
        rounded_rect(p, aim_bar, 3.0, color("#000000", 0.45))
        aim_fill = QRectF(aim_bar)
        aim_fill.setWidth(aim_bar.width() * max(0.0, min(100.0, frame.aim_anim)) / 100.0)
        if aim_fill.width() > 0.5:
            aim_gradient = QLinearGradient(aim_fill.topLeft(), aim_fill.topRight())
            aim_gradient.setColorAt(0.0, color("#ffb347"))
            aim_gradient.setColorAt(1.0, color("#ff6b35"))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(aim_gradient))
            p.drawRoundedRect(aim_fill, 3.0, 3.0)

        ammo = max(0, int(self.state.remaining_ammo))
        ammo_max = max(0, int(self.state.ammo_capacity))
        self.draw_text(p, QRectF(rect.left() + pad, rect.top() + 35, rect.width() - pad * 2.0, 15), hud_text(f"AMMO  {ammo}/{ammo_max}", chinese_enabled(self.registry)), self.font("poppins_medium", 7.2), color("#ffffff"))
        ammo_bar = QRectF(rect.left() + pad, rect.top() + 49, rect.width() - pad * 2.0, 8.0)
        rounded_rect(p, ammo_bar, 3.0, color("#000000", 0.45))
        if enhanced and frame.ammo_ghost > frame.ammo_anim + 0.5:
            ghost = QRectF(ammo_bar)
            ghost.setWidth(ammo_bar.width() * max(0.0, min(100.0, frame.ammo_ghost)) / 100.0)
            rounded_rect(p, ghost, 3.0, color("#ffdcb4", 0.40))
        ammo_fill = QRectF(ammo_bar)
        ammo_fill.setWidth(ammo_bar.width() * max(0.0, min(100.0, frame.ammo_anim)) / 100.0)
        if ammo_fill.width() > 0.5:
            rounded_rect(p, ammo_fill, 3.0, color("#ff5555") if ammo <= 0 else color("#5ec8ff"))
        if show_status_line:
            status = f"HEAT {heat:.0f}%  |  HP {int(self.state.current_health)}/{int(self.state.max_health)}"
            self.draw_text(p, QRectF(rect.left() + pad, rect.bottom() - 11, rect.width() - pad * 2.0, 10), hud_text(status, chinese_enabled(self.registry)), self.font("poppins_regular", 5.8), color("#ffffff", 0.55))
    def draw_target_hud_by_style(
        self,
        p: QPainter,
        target: VisionTarget,
        title: str,
        stack: HudAnchorStack,
    ) -> None:
        style = self.target_hud_style()
        if self.hud_density() == "DebugFull" or style == "FullCard":
            self.draw_target_card(p, stack.take(224.0, 82.0), target, title)
        elif style == "BarOnly":
            self.draw_target_bar_only(p, stack.take(156.0, 24.0), target)
        else:
            self.draw_target_round_compact(p, stack.take(154.0, 42.0), target)

    def draw_target_round_compact(self, p: QPainter, rect: QRectF, target: VisionTarget) -> None:
        hp_ratio = self.target_ratio(target)
        lag_ratio = self.anim.value(f"hud.target.lag.{target.kind}", hp_ratio, self.anim_duration(1.15), Easings.ease_out_pow5, initial=hp_ratio)
        main_ratio = self.anim.value(f"hud.target.main.{target.kind}", hp_ratio, self.anim_duration(0.34), Easings.ease_out_pow4, initial=hp_ratio)
        active = self.anim.value(f"hud.target.round.{target.kind}", 1.0, self.anim_duration(0.24), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        if self.hud_clean():
            self.draw_hud_liquid_glass(p, rect, 6, surface="SubGlass")
        else:
            rounded_rect(p, rect, 6, self.hud_surface(0.52), self.hud_panel_outline(0.11))
        name = self.target_short_name(target)
        value = f"{hp_ratio * 100:.0f}%"
        self.draw_glass_text(p, QRectF(rect.left() + 8, rect.top() + 4, rect.width() - 55, 14), hud_text(name, chinese_enabled(self.registry)), self.font("poppins_medium", 8.4), THEME.text)
        self.draw_glass_text(p, QRectF(rect.right() - 46, rect.top() + 4, 38, 14), hud_text(value, chinese_enabled(self.registry)), self.font("poppins_medium", 8.2), self.hud_accent(), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar = QRectF(rect.left() + 8, rect.top() + 23, rect.width() - 16, 4.2)
        rounded_rect(p, bar, 2.1, color("#ffffff", 0.12))
        lag = QRectF(bar)
        lag.setWidth(max(bar.height(), bar.width() * max(0.0, min(1.0, lag_ratio))))
        rounded_rect(p, lag, 2.1, color("#ffffff", 0.20))
        filled = QRectF(bar)
        filled.setWidth(max(bar.height(), bar.width() * max(0.0, min(1.0, main_ratio))))
        draw_bar(p, bar, main_ratio, self.hud_accent(), color("#ffffff", 0.0), 2.1)
        info = self.target_compact_info(target)
        self.draw_glass_text(p, QRectF(rect.left() + 8, rect.bottom() - 13, rect.width() - 16, 10), hud_text(info, chinese_enabled(self.registry)), self.font("poppins_regular", 6.8), THEME.muted)
        p.restore()

    def draw_target_bar_only(self, p: QPainter, rect: QRectF, target: VisionTarget) -> None:
        hp_ratio = self.target_ratio(target)
        main_ratio = self.anim.value(f"hud.target.bar.{target.kind}", hp_ratio, self.anim_duration(0.28), Easings.ease_out_pow4, initial=hp_ratio)
        active = self.anim.value(f"hud.target.bar.active.{target.kind}", 1.0, self.anim_duration(0.20), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        self.draw_text(p, QRectF(rect.left(), rect.top(), rect.width(), 11), hud_text(self.target_short_name(target), chinese_enabled(self.registry)), self.font("poppins_medium", 7.4), THEME.text)
        draw_bar(p, QRectF(rect.left(), rect.bottom() - 7, rect.width(), 4.0), main_ratio, self.hud_accent(), color("#ffffff", 0.13), 2.0)
        self.draw_text(p, QRectF(rect.right() - 42, rect.top(), 42, 11), hud_text(f"{hp_ratio * 100:.0f}%", chinese_enabled(self.registry)), self.font("poppins_medium", 7.4), self.hud_accent(), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p.restore()

    def target_ratio(self, target: VisionTarget) -> float:
        if target.kind == "energy":
            return max(0.0, min(1.0, self.state.energy_rune_progress / 100.0))
        return max(0.0, min(1.0, self.state.target_health / max(1, self.state.target_max_health)))

    def target_short_name(self, target: VisionTarget) -> str:
        if target.kind == "energy":
            return f"RUNE {self.state.energy_rune_phase}"
        return f"ID {self.state.target_id}"

    def target_compact_info(self, target: VisionTarget) -> str:
        if target.kind == "energy":
            return f"{self.state.energy_target_distance_m:.1f}m | {self.state.energy_rune_rotation} | LOCK {str(self.state.energy_target_locked).upper()}"
        return f"{self.state.target_distance_m:.1f}m | LOCK {str(self.state.target_locked).upper()}"

    def draw_target_card(self, p: QPainter, rect: QRectF, target: VisionTarget, title: str) -> None:
        active = self.anim.value(f"hud.target.{title}", 1.0, self.anim_duration(0.24), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        glow_rect(p, rect, 9, 0.07 if self.hud_clean() else 0.18)
        rounded_rect(p, rect, 8, self.hud_surface(0.76), self.hud_panel_outline(0.10))
        self.draw_text(p, rect.adjusted(13, 9, -13, -54), hud_text(title, chinese_enabled(self.registry)), self.font("poppins_bold", 10), THEME.text)
        if target.kind == "energy":
            ratio = self.state.energy_rune_progress / 100.0
            draw_bar(p, QRectF(rect.left() + 13, rect.top() + 43, rect.width() - 26, 7), ratio, self.hud_accent(), color("#ffffff", 0.14))
            info = f"{self.state.energy_rune_phase} | {self.state.energy_target_distance_m:.1f}m | {self.state.energy_rune_rotation} | LOCK {str(self.state.energy_target_locked).upper()}"
            value = f"{self.state.energy_rune_progress}%"
        else:
            hp_ratio = self.state.target_health / max(1, self.state.target_max_health)
            draw_bar(p, QRectF(rect.left() + 13, rect.top() + 43, rect.width() - 26, 7), hp_ratio, self.hud_accent(), color("#ffffff", 0.14))
            info = f"ID {self.state.target_id} | {self.state.target_distance_m:.1f}m | LOCK {str(self.state.target_locked).upper()}"
            value = f"{target.confidence:.0%}"
        self.draw_text(p, rect.adjusted(13, 56, -13, -8), hud_text(info, chinese_enabled(self.registry)), self.font("poppins_regular", 8.2), THEME.muted)
        self.draw_text(p, QRectF(rect.right() - 73, rect.top() + 10, 58, 18), hud_text(value, chinese_enabled(self.registry)), self.font("poppins_medium", 9), self.hud_accent(), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p.restore()


# ---------------------------------------------------------------------------
# API 冻结面 qualname 固定（见模块 docstring「冻结面纪律」；阶段 1 先例：
# ui/polar/text_metrics.py 底部同一手法）。
# ---------------------------------------------------------------------------

_QUALNAME_PINNED_MEMBERS = (
    CombatTargetHudMixin.paint_combat_target_overlay,
    CombatTargetHudMixin.paint_gpu_target_hud_layer,
    CombatTargetHudMixin.combat_target_visual,
    CombatTargetHudMixin.draw_combat_target_arc,
    CombatTargetHudMixin.draw_combat_target_hud,
    CombatTargetHudMixin.draw_combat_target_hud_layer,
    CombatTargetHudMixin.combat_health_color,
    CombatTargetHudMixin.combat_heat_color,
    CombatTargetHudMixin.draw_combat_target_card,
    CombatTargetHudMixin.draw_target_hud_by_style,
    CombatTargetHudMixin.draw_target_round_compact,
    CombatTargetHudMixin.draw_target_bar_only,
    CombatTargetHudMixin.target_ratio,
    CombatTargetHudMixin.target_short_name,
    CombatTargetHudMixin.target_compact_info,
    CombatTargetHudMixin.draw_target_card,
)

for _fn in _QUALNAME_PINNED_MEMBERS:
    _fn.__qualname__ = f"PolarRMWindow.{_fn.__name__}"
del _fn, _QUALNAME_PINNED_MEMBERS
