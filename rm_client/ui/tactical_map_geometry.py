"""Qt-free tactical-map projection, container targets and raster sampling values.

Coordinates are logical pixels or field metres as named. These values do not
read settings, animate, publish commands, allocate images or own a painter.
The adapter decides when to observe each input and commit each resulting value.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MapExtent:
    width: float
    height: float

    @classmethod
    def collapsed(cls, width: int, height: int, mode: str, density: str, minecraft: bool) -> MapExtent:
        """Choose the stable collapsed target independently of Tab progress."""
        if minecraft:
            return cls(min(216.0, max(168.0, width * 0.19)), min(154.0, max(116.0, height * 0.19)))
        if density == 'CombatCompact':
            return cls(170.0, 118.0)
        if mode == 'Compact':
            return cls(220.0, 170.0)
        if mode == 'Full Tactical':
            return cls(360.0, 270.0)
        return cls(280.0, 212.0)

    def target(self, width: int, height: int, expanded: bool) -> MapExtent:
        expanded_width = min(520.0, width * 0.48)
        expanded_height = min(380.0, height * 0.56)
        target_width = expanded_width if expanded else self.width
        target_height = expanded_height if expanded else self.height
        return MapExtent(min(target_width, max(190.0, width - 36.0)),
                         min(target_height, max(150.0, height - 96.0)))


def normalized_field_coordinate(value_m: float, span_m: float) -> float:
    """Clamp a field coordinate, retaining the historical tiny-span behavior."""
    return max(0.0, min(1.0, value_m / max(0.1, span_m)))


@dataclass(frozen=True)
class NormalizedMapPoint:
    x: float
    y: float

    def mirrored(self) -> NormalizedMapPoint:
        return NormalizedMapPoint(1.0 - self.x, 1.0 - self.y)

    def field_metres(self, width_m: float, height_m: float) -> tuple[float, float]:
        return (max(0.0, min(1.0, self.x)) * width_m,
                max(0.0, min(1.0, self.y)) * height_m)


@dataclass(frozen=True)
class MapViewport:
    """Logical-pixel viewport with a bottom-origin field Y coordinate."""
    left: float
    bottom: float
    width: float
    height: float

    def screen_point(self, point: NormalizedMapPoint) -> tuple[float, float]:
        return self.left + self.width * point.x, self.bottom - self.height * point.y

    def normalized_point(self, x: float, y: float) -> NormalizedMapPoint:
        # A sub-pixel viewport historically uses a 1px inverse denominator.
        return NormalizedMapPoint((x - self.left) / max(1.0, self.width),
                                  (self.bottom - y) / max(1.0, self.height))


def fit_cuts_panel_corners(gaps_x: tuple[float, float], gaps_y: tuple[float, float], radius: float) -> bool:
    """Include the 1px antialiasing margin when deciding whether masking is needed."""
    margin = radius + 1.0
    return any(gap_x < margin and gap_y < margin for gap_x in gaps_x for gap_y in gaps_y)


@dataclass(frozen=True)
class RoundedMapSampling:
    """Immutable mask sampling geometry; the adapter owns native image/cache state."""
    width: int
    height: int
    scale_x: float
    scale_y: float
    radius_px: float

    @classmethod
    def for_extent(cls, width: float, height: float, scale: float, radius: float) -> RoundedMapSampling:
        scale = max(0.5, min(scale, 4.0))
        pixel_width = max(1, int(math.ceil(width * scale)))
        pixel_height = max(1, int(math.ceil(height * scale)))
        return cls(pixel_width, pixel_height, pixel_width / max(0.001, width),
                   pixel_height / max(0.001, height), radius * scale)

    def panel_offset(self, offset_x: float, offset_y: float) -> tuple[float, float]:
        return round(offset_x * self.scale_x, 2), round(offset_y * self.scale_y, 2)

    def cache_key(self, source_key: int, panel_offset: tuple[float, float]) -> tuple:
        return source_key, self.width, self.height, round(self.radius_px, 2), panel_offset
