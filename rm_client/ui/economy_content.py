"""Economy content rendering with explicit values and drawing capabilities.

These functions know no window, registry, bridge, match-state owner, input
capture or transport. The adapters choose fresh data, normalize selection,
advance animation/scroll, and register hotspots at the original stages.
Only drawing and Qt value geometry belong here. Fonts and painter primitives
are explicit capabilities so these fragments can be tested without a window.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QPainter, QPainterPath

from ..economy_values import EconomyContext, EconomyOffer, CATEGORY_TITLES, TRANSACTION_LABELS
from .clickgui_gpu import ClickGuiDrawList
from .economy_locale import economy_text
from .economy_presentation import EconomyConfirmation
from .economy_layout import EconomyModuleGeometry, EconomyCompatLayout
from .economy_workspace import EconomyWorkspaceLayout
from .theme import color


@dataclass(frozen=True)
class EconomyContentStyle:
    font: Callable[[str, float], QFont]
    scale: float
    chinese: Callable[[], bool]


@dataclass(frozen=True)
class EconomyPaintPrimitives:
    """Drawing-only callbacks; none is given access to application state."""
    text: Callable
    rounded_rect: Callable
    glass: Callable
    text_width: Callable


@dataclass(frozen=True)
class EconomyTransactionText:
    status: str
    short_title: str
    message: str


def append_gpu_status(commands: ClickGuiDrawList, status_tiles: tuple[QRectF, ...], clip: QRectF, context: EconomyContext,
                      pending_cost: int, alpha: float, style: EconomyContentStyle) -> None:
    pad = 14.0
    hp_ratio = context.current_health / max(1.0, float(context.max_health))
    ammo_ratio = context.current_ammo / max(1.0, float(context.ammo_capacity or context.current_ammo or 1))
    gold_tile, hp_tile, ammo_tile = status_tiles
    commands.text(
        QRectF(gold_tile.left() + pad, gold_tile.top() + 7.0, gold_tile.width() - pad * 2.0, 20.0),
        economy_text("{gold} G", style.chinese(), gold=context.gold), style.font("poppins_bold", 12.0 * style.scale), color("#ffd56a", alpha), clip=clip,
    )
    commands.text(
        QRectF(gold_tile.left() + pad, gold_tile.top() + 31.0, gold_tile.width() - pad * 2.0, 15.0),
        f"累计 {context.total_gold} · 待扣 {pending_cost}", style.font("poppins_medium", 7.0 * style.scale), color("#9aafc0", 0.88 * alpha), clip=clip,
    )
    for tile, (label, value, ratio, accent_hex) in zip(
        (hp_tile, ammo_tile),
        (
            ("HP", f"{context.current_health}/{context.max_health}", hp_ratio, "#64dda1"),
            ("AMMO", f"{context.current_ammo}/{context.ammo_capacity}", ammo_ratio, "#64baff"),
        ),
    ):
        commands.text(
            QRectF(tile.left() + pad, tile.top() + 8.0, tile.width() - pad * 2.0, 14.0),
            economy_text(label, style.chinese()), style.font("poppins_bold", 6.6 * style.scale), color("#aebcc8", 0.82 * alpha), clip=clip,
        )
        commands.text(
            QRectF(tile.left() + pad, tile.top() + 7.0, tile.width() - pad * 2.0, 15.0),
            value, style.font("poppins_medium", 7.2 * style.scale), color("#ffffff", 0.90 * alpha),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            clip,
        )
        bar = QRectF(tile.left() + pad, tile.top() + 34.0, tile.width() - pad * 2.0, 4.0)
        commands.rect(bar, 2.0, color("#ffffff", 0.08 * alpha), clip=clip)
        commands.rect(QRectF(bar.left(), bar.top(), bar.width() * max(0.0, min(1.0, ratio)), bar.height()), 2.0, color(accent_hex, alpha), clip=clip)


def append_gpu_offer(commands: ClickGuiDrawList, offer: EconomyOffer, rect: QRectF, clip: QRectF,
                     title_inset: float, alpha: float, style: EconomyContentStyle) -> None:
    title_color = color("#ffffff", alpha) if offer.enabled else color("#9aa3ac", 0.72 * alpha)
    commands.text(
        QRectF(rect.left() + title_inset, rect.top() + 8.0, rect.width() - title_inset - 92.0, 24.0),
        offer.short_title, style.font("poppins_bold", 10.5 * style.scale), title_color,
        clip=clip,
    )
    price = "仅状态" if offer.status_only else f"{offer.cost} 金币"
    commands.text(
        QRectF(rect.left() + 18.0, rect.top() + 8.0, rect.width() - 32.0, 24.0),
        price, style.font("poppins_bold", 9.5 * style.scale),
        color("#ffd56a", alpha) if offer.enabled else color("#b8a878", 0.55 * alpha),
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        clip=clip,
    )
    secondary = offer.reason if not offer.enabled else offer.description
    commands.text(
        QRectF(rect.left() + title_inset, rect.top() + 32.0, rect.width() - title_inset - 14.0, 20.0),
        secondary, style.font("poppins_medium", 7.8 * style.scale),
        color("#b8c8d8", (0.90 if offer.enabled else 0.55) * alpha),
        clip=clip,
    )


def append_gpu_offer_detail(commands: ClickGuiDrawList, selected: EconomyOffer, rect: QRectF, clip: QRectF,
                            mock_mode: bool, alpha: float, style: EconomyContentStyle) -> None:
    commands.text(
        QRectF(rect.left() + 16.0, rect.top() + 8.0, rect.width() - 16.0 * 2, 26.0),
        selected.title, style.font("poppins_bold", 13.5 * style.scale), color("#ffffff", alpha),
        clip=clip,
    )
    tag = "预计价格" if selected.estimated else ("模拟请求" if mock_mode else "裁判系统")
    commands.text(
        QRectF(rect.left() + 16.0, rect.top() + 36.0, rect.width() - 16.0 * 2, 20.0),
        f"{selected.unit}  ·  {selected.cost} 金币  ·  {tag}",
        style.font("poppins_medium", 9.0 * style.scale), color("#ffd56a", alpha),
        clip=clip,
    )
    commands.text(
        QRectF(rect.left() + 16.0, rect.top() + 60.0, rect.width() - 16.0 * 2, 28.0),
        selected.description, style.font("poppins_medium", 8.4 * style.scale), color("#d0dce8", alpha),
        clip=clip,
    )
    state_text = selected.reason if not selected.enabled else "可以购买"
    state_color = color("#ffb06f", alpha) if not selected.enabled else color("#70e0ae", alpha)
    commands.text(
        QRectF(rect.left() + 16.0, rect.top() + 90.0, rect.width() - 16.0 * 2, 18.0),
        state_text, style.font("poppins_bold", 9.5 * style.scale), state_color,
        clip=clip,
    )


def append_gpu_transaction(commands: ClickGuiDrawList, transaction: EconomyTransactionText, rect: QRectF,
                           clip: QRectF, alpha: float, style: EconomyContentStyle) -> None:
    label = TRANSACTION_LABELS.get(transaction.status, transaction.status)
    status_color = (
        color("#70e0ae", alpha) if transaction.status == "succeeded"
        else color("#ffb96b", alpha) if transaction.status in ("rejected", "timed_out")
        else color("#72bfff", alpha)
    )
    commands.text(
        QRectF(rect.left() + 16.0, rect.top() + 6.0, rect.width() - 16.0 * 2, 15.0),
        f"{label} · {transaction.short_title}",
        style.font("poppins_bold", 8.2 * style.scale), status_color,
        clip=clip,
    )
    commands.text(
        QRectF(rect.left() + 16.0, rect.top() + 22.0, rect.width() - 16.0 * 2, 13.0),
        transaction.message, style.font("poppins_medium", 7.4 * style.scale), color("#a8bac8", 0.90 * alpha),
        clip=clip,
    )


def append_gpu_confirmation(commands: ClickGuiDrawList, rect: QRectF, clip: QRectF, confirmation: EconomyConfirmation,
                            *, show_hold: bool, alpha: float, hover: float, press: float, style: EconomyContentStyle) -> None:
    btn_r = rect.height() * 0.5
    if confirmation.enabled:
        fill_alpha = (0.78 + 0.10 * hover - 0.08 * press) * alpha
        button_fill = color("#0088ff", fill_alpha)
    else:
        button_fill = color("#ffffff", 0.14 * alpha)
    commands.rect(
        rect,
        btn_r,
        button_fill,
        clip=clip,
    )
    if show_hold and confirmation.progress > 0.0:
        progress_track = QRectF(
            rect.left() + 18.0,
            rect.bottom() - 8.0,
            max(0.0, rect.width() - 36.0),
            2.0,
        )
        commands.rect(progress_track, 1.0, color("#ffffff", 0.18 * alpha), clip=clip)
        commands.rect(
            QRectF(
                progress_track.left(), progress_track.top(),
                progress_track.width() * confirmation.progress, progress_track.height(),
            ),
            1.0,
            color("#ffd56a", 0.96 * alpha), clip=clip,
        )
    commands.text(
        rect, confirmation.text, style.font("poppins_bold", 11.0 * style.scale),
        color("#ffffff", alpha) if confirmation.enabled else color("#c5ccd3", 0.72 * alpha),
        Qt.AlignmentFlag.AlignCenter, clip,
    )


def paint_economy_wallet(p: QPainter, rect: QRectF, context: EconomyContext,
                         style: EconomyContentStyle, paint: EconomyPaintPrimitives) -> None:
    pad = 16.0 * style.scale
    gold_font = style.font("poppins_bold", 28.0 * style.scale)
    paint.text(
        p,
        rect.adjusted(pad, 12.0 * style.scale, -pad, -rect.height() + 30.0 * style.scale),
        "当前金币",
        style.font("poppins_bold", 8.2 * style.scale),
        color("#aebcc8"),
    )
    gold_text = f"{context.gold}"
    gold_rect = QRectF(rect.left() + pad, rect.top() + 32.0 * style.scale, rect.width() - pad * 2.0 - 22.0 * style.scale, 40.0 * style.scale)
    paint.text(p, gold_rect, gold_text, gold_font, color("#ffffff"))
    paint.text(
        p,
        QRectF(gold_rect.left() + paint.text_width(p, gold_font, gold_text) + 6.0 * style.scale, gold_rect.top() + 14.0 * style.scale, 24.0 * style.scale, 24.0 * style.scale),
        economy_text("G", style.chinese()),
        style.font("poppins_bold", 11.0 * style.scale),
        color("#ffd56a"),
    )
    economy_ratio = context.gold / max(1.0, float(max(context.total_gold, context.gold)))
    track = QRectF(rect.left() + pad, rect.top() + 80.0 * style.scale, rect.width() - pad * 2.0, 6.0 * style.scale)
    paint.rounded_rect(p, track, track.height() * 0.5, color("#ffffff", 0.08), color("#ffffff", 0.04), 0.5)
    paint.rounded_rect(
        p,
        QRectF(track.left(), track.top(), max(6.0, track.width() * economy_ratio), track.height()),
        track.height() * 0.5,
        color("#ffd56a", 0.92),
    )


def paint_economy_status(p: QPainter, rect: QRectF, context: EconomyContext, style: EconomyContentStyle,
                         paint: EconomyPaintPrimitives) -> None:
    status_y = rect.bottom() - 100.0 * style.scale
    hp_ratio = context.current_health / max(1.0, float(context.max_health))
    ammo_ratio = context.current_ammo / max(1.0, float(context.ammo_capacity or context.current_ammo or 1))
    for row, (label, value, ratio, accent) in enumerate((
        ("血量", f"{context.current_health}/{context.max_health}", hp_ratio, color("#64dda1")),
        ("弹丸", f"{context.current_ammo}/{context.ammo_capacity or '--'}", ammo_ratio, color("#64baff")),
    )):
        y = status_y + row * 46.0 * style.scale
        paint.text(
            p,
            QRectF(rect.left() + 16.0 * style.scale, y, rect.width() - 16.0 * style.scale * 2.0, 16.0 * style.scale),
            f"{label}  {value}",
            style.font("poppins_medium", 7.8 * style.scale),
            color("#e8f0f8"),
        )
        bar = QRectF(rect.left() + 16.0 * style.scale, y + 20.0 * style.scale, rect.width() - 16.0 * style.scale * 2.0, 5.0 * style.scale)
        paint.rounded_rect(p, bar, bar.height() * 0.5, color("#ffffff", 0.08), color("#ffffff", 0.04), 0.5)
        paint.rounded_rect(
            p,
            QRectF(bar.left(), bar.top(), bar.width() * max(0.0, min(1.0, ratio)), bar.height()),
            bar.height() * 0.5,
            accent,
        )


def paint_economy_offer(p: QPainter, offer: EconomyOffer, rect: QRectF, style: EconomyContentStyle,
                        paint: EconomyPaintPrimitives) -> None:
    title_color = color("#ffffff") if offer.enabled else color("#9aa3ac", 0.72)
    price_w = 88.0 * style.scale
    paint.text(
        p,
        rect.adjusted(18.0 * style.scale, 8.0 * style.scale, -price_w - 10.0 * style.scale, -rect.height() + 32.0 * style.scale),
        offer.short_title,
        style.font("poppins_bold", 10.5 * style.scale),
        title_color,
    )
    price = "仅状态" if offer.status_only else f"{offer.cost} 金币"
    paint.text(
        p,
        rect.adjusted(18.0 * style.scale, 8.0 * style.scale, -14.0 * style.scale, -rect.height() + 32.0 * style.scale),
        price,
        style.font("poppins_bold", 9.5 * style.scale),
        color("#ffd56a") if offer.enabled else color("#b8a878", 0.55),
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
    )
    secondary = offer.reason if not offer.enabled else offer.description
    paint.text(
        p,
        rect.adjusted(18.0 * style.scale, 32.0 * style.scale, -14.0 * style.scale, -8.0 * style.scale),
        secondary,
        style.font("poppins_medium", 7.8 * style.scale),
        color("#b8c8d8", 0.90 if offer.enabled else 0.55),
    )


def paint_economy_offer_detail(p: QPainter, selected: EconomyOffer, rect: QRectF, mock_mode: bool,
                               style: EconomyContentStyle, paint: EconomyPaintPrimitives) -> None:
    paint.text(
        p,
        QRectF(rect.left() + 16.0 * style.scale, rect.top() + 14.0 * style.scale, rect.width() - 16.0 * style.scale * 2.0, 28.0 * style.scale),
        selected.title,
        style.font("poppins_bold", 13.5 * style.scale),
        color("#ffffff"),
    )
    tag = "预计价格" if selected.estimated else ("模拟请求" if mock_mode else "裁判系统")
    paint.text(
        p,
        QRectF(rect.left() + 16.0 * style.scale, rect.top() + 44.0 * style.scale, rect.width() - 16.0 * style.scale * 2.0, 22.0 * style.scale),
        f"{selected.unit}  ·  {selected.cost} 金币  ·  {tag}",
        style.font("poppins_medium", 9.0 * style.scale),
        color("#ffd56a"),
    )
    paint.text(
        p,
        QRectF(rect.left() + 16.0 * style.scale, rect.top() + 70.0 * style.scale, rect.width() - 16.0 * style.scale * 2.0, 40.0 * style.scale),
        selected.description,
        style.font("poppins_medium", 8.4 * style.scale),
        color("#d0dce8"),
    )
    state_text = selected.reason if not selected.enabled else "可以购买"
    state_color = color("#ffb06f") if not selected.enabled else color("#70e0ae")
    paint.text(
        p,
        QRectF(rect.left() + 16.0 * style.scale, rect.top() + 112.0 * style.scale, rect.width() - 16.0 * style.scale * 2.0, 22.0 * style.scale),
        state_text,
        style.font("poppins_bold", 9.5 * style.scale),
        state_color,
    )


def paint_economy_transaction(p: QPainter, transaction: EconomyTransactionText, rect: QRectF, y: float,
                              style: EconomyContentStyle, paint: EconomyPaintPrimitives) -> None:
    label = TRANSACTION_LABELS.get(transaction.status, transaction.status)
    status_color = (
        color("#70e0ae")
        if transaction.status == "succeeded"
        else color("#ffb96b")
        if transaction.status in ("rejected", "timed_out")
        else color("#72bfff")
    )
    paint.text(
        p,
        QRectF(rect.left() + 16.0 * style.scale, y, rect.width() - 16.0 * style.scale * 2.0, 16.0 * style.scale),
        f"{label} · {transaction.short_title}",
        style.font("poppins_bold", 8.2 * style.scale),
        status_color,
    )
    paint.text(
        p,
        QRectF(rect.left() + 16.0 * style.scale, y + 16.0 * style.scale, rect.width() - 16.0 * style.scale * 2.0, 14.0 * style.scale),
        transaction.message,
        style.font("poppins_medium", 7.4 * style.scale),
        color("#a8bac8", 0.90),
    )


def paint_economy_confirmation(p: QPainter, button: QRectF, confirmation: EconomyConfirmation,
                               style: EconomyContentStyle, paint: EconomyPaintPrimitives) -> None:
    paint.glass(
        p,
        button,
        button.height() * 0.5,
        edge_width=6.0 * style.scale,
        highlight_intensity=0.38 if confirmation.enabled else 0.10,
        shadow_radius=10.0 * style.scale,
        shadow_alpha=0.12,
        tint_alpha=0.10 if confirmation.enabled else 0.06,
        tint_color="#0a2820" if confirmation.enabled else "#0a1420",
        edge_tint_alpha=0.16 if confirmation.enabled else 0.06,
    )
    if confirmation.enabled:
        paint.rounded_rect(
            p,
            button,
            button.height() * 0.5,
            color("#1a6b58", 0.14),
            color("#5ce7d8", 0.40),
            1.0,
        )
    if confirmation.held_progress is not None:
        progress = confirmation.held_progress
        fill = QRectF(button.left(), button.top(), max(8.0, button.width() * progress), button.height())
        clip = QPainterPath()
        clip.addRoundedRect(button, button.height() * 0.5, button.height() * 0.5)
        p.save()
        p.setClipPath(clip)
        paint.rounded_rect(p, fill, button.height() * 0.5, color("#65e5d7", 0.40))
        p.restore()
    paint.text(
        p,
        button,
        confirmation.text,
        style.font("poppins_bold", 11.0 * style.scale),
        color("#ffffff") if confirmation.enabled else color("#c5ccd3", 0.72),
        Qt.AlignmentFlag.AlignCenter,
    )


def append_gpu_categories(
    commands: ClickGuiDrawList, layout: EconomyWorkspaceLayout, geometry: EconomyModuleGeometry,
    categories: list[str], category: str, style: EconomyContentStyle,
    *, animate_selection: Callable[[float], float], register_category: Callable[[QRectF, str], None],
) -> None:
    # --- Category navigation: bottom tabs at 720/900p; a true third
    # column at 1080p+ as required by the economy interaction document. ---
    tab_bar = layout.tab_bar
    tab_clip = QRectF(tab_bar).intersected(layout.body_clip)
    tab_alpha = layout.tab_alpha
    if layout.three_column:
        category_clip = QRectF(layout.category_pane).intersected(layout.body_clip)
        row_h = 48.0
        row_gap = 8.0
        commands.text(
            QRectF(layout.category_pane.left() + 16.0, layout.category_pane.top() + 12.0, layout.category_pane.width() - 32.0, 24.0),
            "操作分类",
            style.font("poppins_bold", 10.0 * style.scale),
            color("#ffffff", layout.category_alpha),
            clip=category_clip,
        )
        for index, key in enumerate(categories):
            rect = QRectF(
                layout.category_pane.left() + 10.0,
                layout.category_pane.top() + 46.0 + index * (row_h + row_gap),
                layout.category_pane.width() - 20.0,
                row_h,
            )
            active = key == category
            if active:
                commands.rect(rect, 18.0, color("#0088ff", 0.84 * layout.category_alpha), clip=category_clip)
            commands.text(
                rect.adjusted(14.0, 0.0, -12.0, 0.0),
                CATEGORY_TITLES.get(key, key),
                style.font("poppins_bold", 9.0 * style.scale),
                color("#ffffff", layout.category_alpha) if active else color("#d6e2ec", 0.80 * layout.category_alpha),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                category_clip,
            )
            register_category(rect.intersected(category_clip), key)
    elif not tab_clip.isEmpty() and tab_alpha > 0.01:
        selected_category_index = float(categories.index(category))
        animated_category_index = animate_selection(selected_category_index)
        indicator = QRectF(
            geometry.tab_rects[0].left() + animated_category_index * (geometry.tab_width + geometry.tab_gap) + 6.0,
            tab_bar.top() + 6.0,
            max(1.0, geometry.tab_width - 12.0),
            tab_bar.height() - 12.0,
        )
        if indicator.intersects(tab_clip):
            commands.rect(
                indicator,
                indicator.height() * 0.5,
                color("#0088ff", 0.84 * tab_alpha),
                clip=tab_clip,
            )
        for rect, key in zip(geometry.tab_rects, categories):
            if not rect.intersects(tab_clip):
                continue
            active = key == category
            commands.text(
                rect,
                CATEGORY_TITLES.get(key, key), style.font("poppins_bold", 9.0 * style.scale),
                color("#ffffff", tab_alpha) if active else color("#d6e2ec", 0.80 * tab_alpha),
                Qt.AlignmentFlag.AlignCenter,
                tab_clip,
            )
            register_category(rect.intersected(tab_clip), key)


def paint_economy_categories(
    p: QPainter, layout: EconomyCompatLayout, categories: list[str], category: str,
    style: EconomyContentStyle, paint: EconomyPaintPrimitives,
    *, draw_capsule: Callable, register_category: Callable[[QRectF, str], None],
) -> None:
    category_top = layout.category_top
    category_bottom = layout.category_bottom
    category_h = layout.category_height
    cat_gap = 8.0 * style.scale
    paint.text(
        p,
        QRectF(layout.left.left() + 16.0 * style.scale, category_top - 18.0 * style.scale, layout.left.width() - 16.0 * style.scale * 2.0, 16.0 * style.scale),
        "分类",
        style.font("poppins_bold", 7.6 * style.scale),
        color("#8fa3b5"),
    )
    for index, key in enumerate(categories):
        rect = QRectF(layout.left.left() + 16.0 * style.scale, category_top + index * (category_h + cat_gap), layout.left.width() - 16.0 * style.scale * 2.0, category_h)
        if rect.bottom() > category_bottom + 1.0:
            break
        active = key == category
        draw_capsule(p, rect, hover=0.0, active=active)
        paint.text(
            p,
            rect.adjusted(14.0 * style.scale, 0, -10.0 * style.scale, 0),
            CATEGORY_TITLES.get(key, key),
            style.font("poppins_bold", 10.0 * style.scale),
            color("#ffffff") if active else color("#c8d6e4", 0.90),
        )
        register_category(rect, key)
