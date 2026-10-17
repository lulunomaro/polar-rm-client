"""Detached painted-map hit geometry in Qt logical mouse coordinates.

The snapshot knows rectangles, paths and transforms, never a window, registry,
transport or permission. Device pixel ratio belongs to raster sampling; it must
not be applied again to Qt mouse positions.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath, QTransform

from .tactical_map_geometry import MapViewport, NormalizedMapPoint


@dataclass(frozen=True)
class MapHitGeometry:
    """Copied Qt values from one completed official-map drawing operation."""

    _rect: QRectF
    _inverse: QTransform
    _visible: QPainterPath
    _invertible: bool

    @classmethod
    def empty(cls) -> MapHitGeometry:
        """A paint that has not produced a current map owns no hit region."""
        return cls(QRectF(), QTransform(), QPainterPath(), False)

    @classmethod
    def from_paint(cls, rect: QRectF, panel: QRectF, transform: QTransform,
                   clip: QPainterPath | None, *, rounded: bool) -> MapHitGeometry:
        visible = QPainterPath()
        if rounded:
            visible.addRoundedRect(rect, 6., 6.)
            panel_path = QPainterPath()
            panel_path.addRoundedRect(panel, 6., 6.)
            visible = visible.intersected(panel_path)
        else:
            visible.addRect(rect)
        if clip is not None:
            visible = visible.intersected(clip)
        inverse, invertible = transform.inverted()
        return cls(QRectF(rect), QTransform(inverse), QPainterPath(visible), invertible)

    def normalized_point(self, point: QPointF) -> NormalizedMapPoint | None:
        if not self._invertible:
            return None
        local = self._inverse.map(point)
        if not self._visible.contains(local):
            return None
        return _normalized_point(local, self._rect)


def _normalized_point(point: QPointF, rect: QRectF) -> NormalizedMapPoint:
    return MapViewport(rect.left(), rect.bottom(), rect.width(), rect.height()).normalized_point(
        point.x(), point.y())


def map_hit_position(point: QPointF, rect: QRectF,
                     geometry: MapHitGeometry | None) -> NormalizedMapPoint | None:
    """Use painted geometry; retain the historical direct-call rectangle seam."""
    if geometry is not None:
        return geometry.normalized_point(point)
    if rect.isNull() or not rect.contains(point):
        return None
    return _normalized_point(point, rect)
