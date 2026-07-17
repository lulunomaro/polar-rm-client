from __future__ import annotations

import math
from typing import Iterable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen


def with_alpha(c: QColor, alpha: float) -> QColor:
    out = QColor(c)
    out.setAlphaF(max(0.0, min(1.0, out.alphaF() * alpha)))
    return out


def lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def rounded_rect(p: QPainter, rect: QRectF, radius: float, fill: QColor, outline: QColor | None = None, width: float = 1.0) -> None:
    p.setBrush(fill)
    if outline is None or outline.alpha() == 0:
        p.setPen(Qt.PenStyle.NoPen)
    else:
        p.setPen(QPen(outline, width))
    p.drawRoundedRect(rect, radius, radius)


def glow_rect(p: QPainter, rect: QRectF, radius: float, alpha: float = 1.0) -> None:
    # 少一层、更淡：避免处处霓虹。
    for i, a in enumerate((0.10, 0.05)):
        pad = 12 - i * 5
        rounded_rect(
            p,
            rect.adjusted(-pad, -pad, pad, pad),
            radius + pad,
            QColor(255, 255, 255, int(255 * a * alpha)),
        )
    rounded_rect(p, rect, radius, QColor(16, 16, 20, int(238 * alpha)), QColor(255, 255, 255, int(22 * alpha)))


def glow_text(
    p: QPainter,
    text: str,
    x: float,
    y: float,
    font: QFont,
    color: QColor,
    glow: QColor,
    radius: float = 6.0,
) -> None:
    p.setFont(font)
    # 仅轻微外晕，主字清晰。
    for dx, dy, a in ((0, 0, 0.28), (1, 0, 0.12), (0, 1, 0.10)):
        c = QColor(glow)
        c.setAlphaF(min(1.0, glow.alphaF() * a))
        p.setPen(c)
        p.drawText(QPointF(x + dx * radius * 0.12, y + dy * radius * 0.12), text)
    p.setPen(color)
    p.drawText(QPointF(x, y), text)


def text_width(p: QPainter, font: QFont, text: str) -> float:
    p.setFont(font)
    return QFontMetricsF(font).horizontalAdvance(text)


def cap_center_y(p: QPainter, font: QFont, rect: QRectF) -> float:
    p.setFont(font)
    metrics = QFontMetricsF(font)
    return rect.center().y() + metrics.capHeight() / 2.0 - 1.0


def vertical_gradient(p: QPainter, rect: QRectF, top: QColor, bottom: QColor, radius: float = 0.0) -> None:
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, top)
    grad.setColorAt(1.0, bottom)
    p.setBrush(grad)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(rect, radius, radius)


def draw_bar(p: QPainter, rect: QRectF, ratio: float, color: QColor, bg: QColor, radius: float | None = None) -> None:
    ratio = max(0.0, min(1.0, ratio))
    r = rect.height() / 2.0 if radius is None else radius
    rounded_rect(p, rect, r, bg)
    if ratio > 0.001:
        filled = QRectF(rect)
        filled.setWidth(max(rect.height(), rect.width() * ratio))
        rounded_rect(p, filled, r, color)


def path_from_points(points: Iterable[tuple[float, float]]) -> QPainterPath:
    pts = list(points)
    path = QPainterPath()
    if not pts:
        return path
    path.moveTo(*pts[0])
    for point in pts[1:]:
        path.lineTo(*point)
    return path


def accent_color(index: int, phase: float = 0.0) -> QColor:
    hue = (index * 0.095 + phase * 0.04) % 1.0
    return QColor.fromHsvF(hue, 0.68, 1.0, 1.0)
