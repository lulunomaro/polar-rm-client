"""Deterministic economy geometry and lens parameters, using Qt value types only.

These functions have no widget, compositor, registry, animation store, or clock.
They leave command order, resource ownership, clipping submission, and input
registration to the GPU/QPainter adapters. QRectF values are frame-local copies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF

from .animation import Easings
from .economy_workspace import _lerp_rect

if TYPE_CHECKING:
    from .economy_workspace import EconomyWorkspaceLayout


@dataclass(frozen=True)
class EconomyOfferListGeometry:
    viewport: QRectF
    card_height: float
    card_gap: float
    card_width: float
    card_left: float

    def scroll_maximum(self, count: int) -> float:
        return max(0.0, count * (self.card_height + self.card_gap) - self.card_gap - self.viewport.height())

    def card(self, index: float, scroll: float) -> QRectF:
        return QRectF(
            self.card_left,
            self.viewport.top() + index * (self.card_height + self.card_gap) - scroll,
            self.card_width,
            self.card_height,
        )


@dataclass(frozen=True)
class EconomyCompatLayout:
    panel: QRectF
    radius: float
    header: QRectF
    morph: float
    content_alpha: float
    compact: bool
    left: QRectF
    offers_pane: QRectF
    detail_pane: QRectF
    offers: EconomyOfferListGeometry
    action_button: QRectF
    category_top: float
    category_bottom: float
    category_height: float


def compat_economy_layout(
    width: int, height: int, *, scale: float, alpha: float, category_count: int,
) -> EconomyCompatLayout:
    """Preserve the top-anchored compatibility sheet's responsive geometry."""
    morph = Easings.ease_out_expo(max(0.0, min(1.0, alpha)))
    content_alpha = Easings.ease_out_expo(max(0.0, min(1.0, (morph - 0.12) / 0.88)))
    compact = width < 1480 or height < 860
    margin_x = 16.0 * scale if compact else 28.0 * scale
    top_margin = 18.0 * scale if compact else 24.0 * scale
    bottom_margin = 18.0 * scale if compact else 28.0 * scale
    target_w = min(width - margin_x * 2.0, 1510.0 * scale)
    target_h = min(height - top_margin - bottom_margin, 740.0 * scale)
    target = QRectF((width - target_w) * 0.5, top_margin, target_w, target_h)
    island_h = 44.0 * scale
    island_w = min(max(280.0 * scale, 360.0 * scale), width * 0.40)
    collapsed = QRectF(width * 0.5 - island_w * 0.5, top_margin, island_w, island_h)
    expand_r = min(40.0 * scale, min(target_w, target_h) * 0.08)
    panel = _lerp_rect(collapsed, target, morph)
    radius = island_h * 0.5 * (1.0 - morph) + expand_r * morph
    header_h = 58.0 * scale
    header = QRectF(panel.left() + 26.0 * scale, panel.top() + 16.0 * scale, panel.width() - 52.0 * scale, header_h - 14.0 * scale)

    col_gap = 12.0 * scale
    inner = panel.adjusted(20.0 * scale, header_h + 6.0 * scale, -20.0 * scale, -18.0 * scale)
    left_w = max(172.0 * scale, min(228.0 * scale, inner.width() * 0.20))
    rest = max(40.0, inner.width() - left_w - col_gap)
    left = QRectF(inner.left(), inner.top(), left_w, inner.height())
    if compact:
        content = QRectF(left.right() + col_gap, inner.top(), rest, inner.height())
        list_h = max(150.0 * scale, content.height() * 0.44)
        offers_pane = QRectF(content.left(), content.top(), content.width(), list_h)
        detail = QRectF(content.left(), offers_pane.bottom() + col_gap, content.width(), content.bottom() - offers_pane.bottom() - col_gap)
    else:
        list_w = max(280.0 * scale, min(rest * 0.48, rest - 250.0 * scale - col_gap))
        offers_pane = QRectF(left.right() + col_gap, inner.top(), list_w, inner.height())
        detail = QRectF(offers_pane.right() + col_gap, inner.top(), inner.right() - offers_pane.right() - col_gap, inner.height())
    viewport = offers_pane.adjusted(14.0 * scale, 34.0 * scale, -14.0 * scale, -14.0 * scale)
    card_h = max(54.0 * scale, min(68.0 * scale, viewport.height() * 0.26 if compact else 62.0 * scale))
    offers = EconomyOfferListGeometry(viewport, card_h, 8.0 * scale, viewport.width(), viewport.left())
    button = QRectF(detail.left() + 16.0 * scale, detail.bottom() - 46.0 * scale - 14.0 * scale,
                    max(40.0, detail.width() - 32.0 * scale), 46.0 * scale)
    category_top = left.top() + 124.0 * scale
    category_bottom = left.bottom() - 100.0 * scale - 10.0 * scale
    cat_count = max(1, category_count)
    cat_avail = max(40.0 * scale, category_bottom - category_top)
    category_h = min(40.0 * scale, max(32.0 * scale, (cat_avail - 8.0 * scale * (cat_count - 1)) / cat_count))
    return EconomyCompatLayout(panel, radius, header, morph, content_alpha, compact, left, offers_pane,
                               detail, offers, button, category_top, category_bottom, category_h)


@dataclass(frozen=True)
class EconomyModuleGeometry:
    status_tiles: tuple[QRectF, ...]
    tab_rects: tuple[QRectF, ...]
    tab_width: float
    tab_gap: float
    offers_pane: QRectF
    offers_clip: QRectF
    offers: EconomyOfferListGeometry
    viewport_clip: QRectF
    detail_pane: QRectF
    detail_clip: QRectF
    detail_viewport: QRectF
    detail_viewport_clip: QRectF
    action_button: QRectF


def economy_header_tile(layout: EconomyWorkspaceLayout) -> QRectF:
    header = layout.header
    return QRectF(header.left() - 16.0, layout.panel.top(), header.width() + 32.0, min(layout.panel.height(), 68.0))


def economy_module_geometry(layout: EconomyWorkspaceLayout, *, category_count: int) -> EconomyModuleGeometry:
    """Lay out independent GPU modules after the body has begun to reveal."""
    strip = layout.status_strip
    status_gap = 8.0
    source_widths = (304.0, 152.0, 152.0)
    source_total = sum(source_widths) + status_gap * 2.0
    status_scale = min(1.0, strip.width() / max(1.0, source_total))
    status_widths = tuple(value * status_scale for value in source_widths)
    status_total = sum(status_widths) + status_gap * 2.0
    status_left = strip.center().x() - status_total * 0.5
    status_tiles = (
        QRectF(status_left, strip.top(), status_widths[0], strip.height()),
        QRectF(status_left + status_widths[0] + status_gap, strip.top(), status_widths[1], strip.height()),
        QRectF(status_left + status_widths[0] + status_gap * 2.0 + status_widths[1], strip.top(), status_widths[2], strip.height()),
    )
    tab_gap = 8.0
    tab_width = 0.0
    tab_rects: tuple[QRectF, ...] = ()
    if not layout.three_column:
        tab_bar = layout.tab_bar
        tab_count = max(1, category_count)
        tab_width = min(152.0, max(1.0, (tab_bar.width() - tab_gap * (tab_count - 1)) / tab_count))
        tabs_total = tab_width * tab_count + tab_gap * (tab_count - 1)
        tabs_left = tab_bar.center().x() - tabs_total * 0.5
        tab_rects = tuple(QRectF(tabs_left + index * (tab_width + tab_gap), tab_bar.top(), tab_width, tab_bar.height())
                          for index in range(tab_count))
    offers_pane = layout.offers_pane.translated(0.0, layout.offers_offset)
    viewport = layout.offers_viewport.translated(0.0, layout.offers_offset)
    card_w = min(520.0, viewport.width())
    offers = EconomyOfferListGeometry(viewport, 68.0, 8.0, card_w, viewport.center().x() - card_w * 0.5)
    detail = layout.detail_pane.translated(0.0, layout.detail_offset)
    detail_vp = layout.detail_viewport.translated(0.0, layout.detail_offset)
    return EconomyModuleGeometry(
        status_tiles, tab_rects, tab_width, tab_gap,
        offers_pane, offers_pane.intersected(layout.body_clip), offers, viewport.intersected(layout.body_clip),
        detail, detail.intersected(layout.body_clip), detail_vp, detail_vp.intersected(layout.body_clip),
        layout.action_button.translated(0.0, layout.detail_offset),
    )


@dataclass(frozen=True)
class EconomyLensPlan:
    rect: QRectF
    visible: QRectF
    clip: QRectF | None
    opacity: float
    radius: float
    refraction_height: float
    refraction_amount: float


def economy_lens_plan(rect: QRectF, opacity: float, *, clip: QRectF | None = None) -> EconomyLensPlan | None:
    """Calibrate lens travel/depth together; empty or invisible tiles submit nothing."""
    tile = QRectF(rect)
    visible = tile.intersected(clip) if clip is not None else QRectF(tile)
    alpha = max(0.0, min(1.0, float(opacity)))
    if visible.isEmpty() or alpha <= 0.01:
        return None
    # The reference ControlCenterContent is calibrated on a 68dp minimum tile.
    lens_scale = min(1.0, max(0.20, min(tile.width(), tile.height()) / 68.0))
    return EconomyLensPlan(
        tile, visible, QRectF(clip) if clip is not None else None, alpha,
        min(34.0, tile.height() * 0.5), max(1.0, 24.0 * lens_scale * alpha),
        -max(1.0, 48.0 * lens_scale * alpha),
    )


def economy_detail_scroll_maximum(*, viewport_height: float, reload_height: float, history_count: int) -> float:
    content_height = 142.0 + reload_height
    content_height += 24.0 + max(1, history_count) * 52.0 if history_count else 52.0
    return max(0.0, content_height - viewport_height)
