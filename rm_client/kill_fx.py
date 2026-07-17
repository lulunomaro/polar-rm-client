from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
import random
import time
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QTransform

from .combat_events import CombatEvent
from .ui_audio import ReliableAudioPool


KILL_TYPES = ("NORMAL", "HEADSHOT", "EXPLOSION", "CRIT", "ASSIST", "DESTROY_VEHICLE")
TYPE_TEXTURE = {
    "NORMAL": "default", "HEADSHOT": "headshot", "EXPLOSION": "explosion",
    "CRIT": "crit", "ASSIST": "assist", "DESTROY_VEHICLE": "destroyvehicle",
}


@dataclass(frozen=True)
class TriggerContext:
    kill_type: str
    combo: int
    victim_id: int
    victim_name: str
    distance_m: float
    score_delta: int
    tags: tuple[str, ...]
    timestamp: float


@dataclass(frozen=True)
class KillFxLayout:
    anchor: QPointF
    scale: float
    policy: str
    offset: QPointF

    @staticmethod
    def responsive_factor(width: int, height: int, device_pixel_ratio: float) -> float:
        physical_area = max(1.0, width * height * max(1.0, device_pixel_ratio) ** 2)
        reference_area = 1920.0 * 1080.0
        linear_ratio = math.sqrt(physical_area / reference_area)
        if linear_ratio <= 1.0:
            return max(0.85, linear_ratio)
        # 2048x1152 也属于用户常说的 2K 档，必须达到完整的 1.5 倍，而不是仅放大约 10%。
        two_k_threshold = 2048.0 / 1920.0
        if linear_ratio <= 4.0 / 3.0:
            progress = min(1.0, (linear_ratio - 1.0) / (two_k_threshold - 1.0))
            return 1.0 + 0.5 * progress
        return min(1.60, 1.50 + (linear_ratio - 4.0 / 3.0) * 0.15)

    @classmethod
    def create(
        cls,
        width: int,
        height: int,
        *,
        policy: str,
        user_scale: float,
        responsive: bool,
        device_pixel_ratio: float,
        x_offset: float,
        y_offset: float,
    ) -> "KillFxLayout":
        factor = cls.responsive_factor(width, height, device_pixel_ratio) if responsive else 1.0
        scale = max(0.35, min(3.2, user_scale * factor))
        offset = QPointF(x_offset, y_offset)
        if policy == "Combat Focus":
            anchor = QPointF(width * 0.5 + x_offset, height * 0.74 + y_offset)
        else:
            anchor = QPointF(width * 0.5 + (x_offset if policy == "Manual" else 0.0), height + (y_offset if policy == "Manual" else 0.0))
        return cls(anchor, scale, policy, offset)

    def primary(self, original_bottom_offset: float) -> QPointF:
        if self.policy == "Combat Focus":
            return self.anchor
        return QPointF(self.anchor.x(), self.anchor.y() - original_bottom_offset * self.scale)


@dataclass(frozen=True)
class ValorantStyle:
    name: str
    token: str
    accent: str
    sound_prefix: str
    headshot_y: float = -12.0
    emblem_only_motion: bool = False
    blade: str = ""


def _valorant_styles() -> dict[str, ValorantStyle]:
    rows = [
        ("Prime", "prime", "#908CCD", "valorant_primekill_", -16, False, ""),
        ("Glitchpop", "glitchpop", "#2697F5", "valorant_glitchpopkill_", -16, False, ""),
        ("Singularity V1", "singularity_v1", "#DF7E49", "valorant_singularitykill_", -10, False, ""),
        ("Singularity V2", "singularity_v2", "#DCC971", "valorant_singularitykill_", -10, False, ""),
        ("Singularity V3", "singularity_v3", "#7E9EDC", "valorant_singularitykill_", -10, False, ""),
        ("Gaia", "gaia", "#F9545E", "valorant_gaiavengeancekill_", -20, False, ""),
        ("Gaia V1", "gaia_v1", "#287EF3", "valorant_gaiavengeancekill_", -20, False, ""),
        ("Gaia V2", "gaia_v2", "#27B748", "valorant_gaiavengeancekill_", -20, False, ""),
        ("Gaia V3", "gaia_v3", "#F77124", "valorant_gaiavengeancekill_", -20, False, ""),
        ("Bubblegum", "bubblegum_deathwish", "#C94FB9", "valorant_bubblegumdeathwishkill_", -12, True, "killicon_valorant_bubblegum_deathwish_blade.png"),
        ("Bubblegum V1", "bubblegum_deathwish_v1", "#C98E4C", "valorant_bubblegumdeathwishkill_", -12, True, "killicon_valorant_bubblegum_deathwish_blade.png"),
        ("Bubblegum V2", "bubblegum_deathwish_v2", "#9D332F", "valorant_bubblegumdeathwishkill_", -12, True, "killicon_valorant_bubblegum_deathwish_blade.png"),
        ("Bubblegum V3", "bubblegum_deathwish_v3", "#6EB037", "valorant_bubblegumdeathwishkill_", -12, True, "killicon_valorant_bubblegum_deathwish_blade.png"),
        ("Champions 2021", "champions_2021", "#947046", "valorant_champions2021kill_", -12, False, ""),
        ("Prelude V1", "prelude_to_chaos_v1", "#F46E57", "valorant_preludetochaoskill_", -12, False, ""),
        ("Prelude V2", "prelude_to_chaos_v2", "#10C110", "valorant_preludetochaoskill_", -12, False, ""),
        ("Prelude V3", "prelude_to_chaos_v3", "#1168C1", "valorant_preludetochaoskill_", -12, False, ""),
        ("Primordium", "primordium", "#8F3E31", "valorant_primordiumkill_", -14, False, ""),
        ("Primordium V1", "primordium_v1", "#387A51", "valorant_primordiumkill_", -14, False, ""),
        ("Primordium V2", "primordium_v2", "#316884", "valorant_primordiumkill_", -14, False, ""),
        ("Primordium V3", "primordium_v3", "#8D6F43", "valorant_primordiumkill_", -14, False, ""),
        ("Radiant Crisis 001", "radiant_crisis_001", "#73C0C4", "valorant_radiantcrisis001kill_", -12, False, ""),
        ("RGX", "rgx_11z_pro", "#C1F341", "valorant_rgx11zprokill_", -12, False, ""),
        ("RGX V1", "rgx_11z_pro_v1", "#F3414A", "valorant_rgx11zprokill_", -12, False, ""),
        ("RGX V2", "rgx_11z_pro_v2", "#41BAF3", "valorant_rgx11zprokill_", -12, False, ""),
        ("RGX V3", "rgx_11z_pro_v3", "#F3A741", "valorant_rgx11zprokill_", -12, False, ""),
    ]
    return {row[0]: ValorantStyle(*row) for row in rows}


VALORANT_STYLES = _valorant_styles()
PRESET_ELEMENTS = {
    "Default Scrolling": ("kill_feed", "score", "bonus", "scrolling"),
    "Combo": ("kill_feed", "score", "bonus", "combo"),
    "Card": ("card_bar", "card"),
    "Battlefield 1": ("score", "bonus", "battlefield1"),
    "Minimal": ("score", "bonus"),
    "PUBG Subtitle": ("combo_subtitle", "kill_feed"),
    "Battlefield 5": ("kill_feed", "score", "bonus", "scrolling_bf5"),
    "Total Battlefield": ("score", "bonus", "scrolling"),
}


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def _ease_in_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t ** 3


def _segmented_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * 1.8 if t < 0.5 else 0.9 + (1.0 - (1.0 - (t - 0.5) * 2.0) ** 3) * 0.1


def _segmented_in(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return (t * 2.0) ** 2 * 0.2 if t < 0.5 else 0.2 + (t - 0.5) * 1.6


class AssetBank:
    def __init__(self, root: Path) -> None:
        self.texture_root = root / "textures"
        self.sound_root = root / "sounds"
        self.images: dict[str, QImage] = {}
        self.frames: dict[tuple[str, int, int], QImage] = {}

    def image(self, name: str) -> QImage:
        if name not in self.images:
            self.images[name] = QImage(str(self.texture_root / name))
        return self.images[name]

    def draw_centered(self, p: QPainter, name: str, center: QPointF, size: float, alpha: float = 1.0, rotation: float = 0.0, scale: float = 1.0, additive: bool = False, width_ratio: float = 1.0, height_ratio: float = 1.0) -> None:
        image = self.image(name)
        if image.isNull() or alpha <= 0.001:
            return
        aspect = image.width() / max(1.0, image.height())
        w, h = size * width_ratio, size / max(0.01, aspect) * height_ratio
        p.save()
        p.setOpacity(max(0.0, min(1.0, alpha)))
        if additive:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.translate(center)
        p.rotate(rotation)
        p.scale(scale, scale)
        p.drawImage(QRectF(-w / 2.0, -h / 2.0, w, h), image)
        p.restore()

    def draw_vertical_frame(self, p: QPainter, name: str, frame: int, count: int, center: QPointF, size: float, alpha: float, scale: float = 1.0, additive: bool = True) -> None:
        image = self.image(name)
        if image.isNull() or count <= 0:
            return
        frame = max(0, min(count - 1, frame))
        key = (name, frame, count)
        if key not in self.frames:
            frame_h = image.height() // count
            self.frames[key] = image.copy(0, frame * frame_h, image.width(), frame_h)
        frame_image = self.frames[key]
        p.save()
        p.setOpacity(max(0.0, min(1.0, alpha)))
        if additive:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        aspect = frame_image.width() / max(1.0, frame_image.height())
        w, h = size, size / max(0.01, aspect)
        p.translate(center)
        p.scale(scale, scale)
        p.drawImage(QRectF(-w / 2.0, -h / 2.0, w, h), frame_image)
        p.restore()

    def draw_bottom_anchored(self, p: QPainter, name: str, anchor: QPointF, width: float, alpha: float, rotation: float, scale: float) -> None:
        image = self.image(name)
        if image.isNull() or alpha <= 0.001:
            return
        aspect = image.width() / max(1.0, image.height())
        height = width / max(0.01, aspect)
        p.save()
        p.setOpacity(max(0.0, min(1.0, alpha)))
        p.translate(anchor)
        p.rotate(rotation)
        p.scale(scale, scale)
        p.drawImage(QRectF(-width / 2.0, -height, width, height), image)
        p.restore()


class SoundPool(ReliableAudioPool):
    def __init__(self, root: Path, count: int = 8) -> None:
        super().__init__(root, voices_per_sound=2, fallback_voices=count)


def _draw_ring(p: QPainter, center: QPointF, age: float, kill_type: str, scale: float) -> None:
    if age < 0.10:
        return
    t = (age - 0.10) / 0.30
    if t < 0.0 or t > 1.0 or kill_type not in ("HEADSHOT", "EXPLOSION", "CRIT"):
        return
    colors = {"HEADSHOT": QColor("#D4B800"), "EXPLOSION": QColor("#F77F00"), "CRIT": QColor("#9CCC65")}
    radius = (10.0 + 32.0 * _ease_out_cubic(t)) * scale
    c = colors[kill_type]
    c.setAlphaF((1.0 - t) ** 2)
    p.save()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(c, max(0.4, 3.0 * (1.0 - t) * scale)))
    p.drawEllipse(center, radius, radius)
    if kill_type == "EXPLOSION" and age >= 0.20:
        t2 = min(1.0, (age - 0.20) / 0.30)
        c.setAlphaF((1.0 - t2) ** 2)
        p.setPen(QPen(c, max(0.4, 5.4 * (1.0 - t2) * scale)))
        r2 = (10.0 + 32.0 * _ease_out_cubic(t2)) * scale
        p.drawEllipse(center, r2, r2)
    p.restore()


@dataclass
class IconState:
    context: TriggerContext
    started: float
    previous_x: float = 0.0
    target_x: float = 0.0
    move_started: float = 0.0
    forced_fade: float = -1.0


class ScrollingRenderer:
    def __init__(self, assets: AssetBank) -> None:
        self.assets = assets
        self.icons: list[IconState] = []
        self.pending: list[TriggerContext] = []
        self.last_display = -1.0

    def trigger(self, context: TriggerContext, now: float) -> None:
        if self.last_display < 0 or now - self.last_display >= 0.10:
            self._add(context, now)
        elif len(self.pending) < 30:
            self.pending.append(context)

    def _add(self, context: TriggerContext, now: float) -> None:
        self.last_display = now
        self.icons.append(IconState(context, now, move_started=now))
        if len(self.icons) > 7 and self.icons[-8].forced_fade < 0:
            self.icons[-8].forced_fade = now

    def draw(self, p: QPainter, center: QPointF, now: float, scale: float, bf5: bool = False) -> None:
        while self.pending and now - self.last_display >= 0.10:
            self._add(self.pending.pop(0), self.last_display + 0.10)
        self.icons = [i for i in self.icons if now - i.started < 3.55 and not (i.forced_fade > 0 and now - i.forced_fade > 0.30)]
        visible = self.icons[-7:]
        spacing = (64.0 + 4.0) * scale
        for index, state in enumerate(visible):
            target = center.x() - (index - (len(visible) - 1) / 2.0) * spacing
            if abs(target - state.target_x) > 0.1:
                state.previous_x = state.target_x or target
                state.target_x = target
                state.move_started = now
            mt = _ease_out_cubic((now - state.move_started) / 0.30)
            x = state.previous_x + (state.target_x - state.previous_x) * mt
            age = now - state.started
            alpha = _ease_out_cubic(age / 0.30)
            if age > 3.25:
                alpha *= max(0.0, 1.0 - (age - 3.25) / 0.30)
            if state.forced_fade > 0:
                alpha *= max(0.0, 1.0 - (now - state.forced_fade) / 0.30)
            draw_scale = (2.0 + (1.0 - 2.0) * _ease_out_cubic(age / 0.30)) * scale
            key = TYPE_TEXTURE.get(state.context.kill_type, "default")
            if bf5:
                if key not in ("headshot", "assist", "destroyvehicle"):
                    key = "default"
                name = f"killicon_battlefield5_{key}.png"
            else:
                name = f"killicon_scrolling_{key}.png"
            point = QPointF(x, center.y())
            self.assets.draw_centered(p, name, point, 64.0, alpha, scale=draw_scale)
            _draw_ring(p, point, age, state.context.kill_type, scale)


class ComboRenderer:
    def __init__(self, assets: AssetBank) -> None:
        self.assets, self.context, self.started = assets, None, -1.0

    def trigger(self, context: TriggerContext, now: float) -> None:
        if context.combo <= 0 or context.kill_type in ("ASSIST", "DESTROY_VEHICLE"):
            return
        self.context, self.started = context, now

    def draw(self, p: QPainter, center: QPointF, now: float, scale: float) -> None:
        if self.context is None:
            return
        age = now - self.started
        if age > 1.80:
            self.context = None
            return
        alpha = _ease_out_cubic(age / 0.30) * (1.0 if age < 1.50 else max(0.0, 1.0 - (age - 1.50) / 0.30))
        size_scale = 2.0 + (1.0 - 2.0) * _ease_out_cubic(age / 0.30)
        self.assets.draw_centered(p, f"killicon_combo_{max(1, min(6, self.context.combo))}.png", center, 64.0, alpha, scale=size_scale * scale)
        _draw_ring(p, center, age, self.context.kill_type, scale)


@dataclass
class CardState:
    context: TriggerContext
    started: float
    angle_start: float = 0.0
    angle_target: float = 0.0
    layout_started: float = 0.0
    exiting: float = -1.0


class CardRenderer:
    def __init__(self, assets: AssetBank) -> None:
        self.assets, self.cards = assets, []

    def trigger(self, context: TriggerContext, now: float) -> None:
        if context.kill_type in ("ASSIST", "DESTROY_VEHICLE"):
            return
        if context.combo == 1:
            for card in self.cards:
                card.exiting = now
        self.cards.append(CardState(context, now, layout_started=now))
        active = [card for card in self.cards if card.exiting < 0]
        if len(active) > 6:
            active[0].exiting = now
            active = active[1:]
        count = len(active)
        for index, card in enumerate(active):
            card.angle_start = card.angle_target
            card.angle_target = -55.0 + (110.0 * index / max(1, count - 1))
            card.layout_started = now

    def draw(self, p: QPainter, origin: QPointF, now: float, scale: float, team: str) -> None:
        if self.cards and now - self.cards[-1].started > 5.0:
            for card in self.cards:
                if card.exiting < 0:
                    card.exiting = now
        self.cards = [card for card in self.cards if card.exiting < 0 or now - card.exiting <= 0.20]
        for card in self.cards:
            age = now - card.started
            exit_age = now - card.exiting if card.exiting >= 0 else -1.0
            progress = _segmented_out(age / 0.20) if exit_age < 0 else 1.0 - _segmented_in(exit_age / 0.20)
            dist = 256.0 * 0.15 * 0.9 * progress * scale
            lt = _segmented_out((now - card.layout_started) / 0.20)
            angle = card.angle_start + (card.angle_target - card.angle_start) * lt
            rad = math.radians(angle)
            center = QPointF(origin.x() + math.sin(rad) * dist, origin.y() - math.cos(rad) * dist)
            alpha = min(1.0, age / 0.067)
            if exit_age >= 0 and exit_age / 0.20 > 0.66:
                alpha *= max(0.0, 1.0 - (exit_age / 0.20 - 0.66) / 0.34)
            suffix = "ct" if team == "ct" else "t"
            key = TYPE_TEXTURE.get(card.context.kill_type, "default")
            name = f"killicon_card_{key}_{suffix}.png"
            light = f"killicon_card_light_{suffix}.png"
            light_t = min(1.0, age / 1.0)
            if age < 0.067:
                light_scale = 0.5 + 0.5 * _ease_out_cubic(age / 0.067)
            else:
                light_scale = 1.0 - 0.4 * min(1.0, (age - 0.067) / 0.933)
            card_half = 256.0 * 0.15 * 0.5 * scale
            angle_rad = math.radians(angle)
            light_anchor = QPointF(center.x() - math.sin(angle_rad) * card_half, center.y() + math.cos(angle_rad) * card_half)
            self.assets.draw_bottom_anchored(p, light, light_anchor, 256.0 * 0.15, alpha * (1.0 - light_t * light_t), angle, light_scale * scale)
            self.assets.draw_centered(p, name, center, 256.0 * 0.15, alpha, angle, scale)
            if age < 0.90:
                self.assets.draw_centered(p, name, center, 256.0 * 0.15, alpha * max(0.0, 1.0 - age / 0.90), angle, scale, True)
            font = QFont("Poppins", max(7, int(10 * scale)))
            font.setBold(True)
            p.setFont(font)
            p.setPen(QColor("#9cc1eb") if team == "ct" else QColor("#d9ac5b"))
            p.drawText(QRectF(center.x() - 30, center.y() - 9, 60, 18), Qt.AlignmentFlag.AlignCenter, str(card.context.combo))


@dataclass
class Wave:
    started: float
    direction: int


class CardBarRenderer:
    def __init__(self, assets: AssetBank) -> None:
        self.assets, self.context, self.started, self.waves = assets, None, -1.0, []

    def trigger(self, context: TriggerContext, now: float) -> None:
        if context.kill_type == "ASSIST":
            return
        self.context, self.started = context, now
        count = 1 if context.combo >= 6 else min(5, max(1, context.combo))
        self.waves.extend(Wave(now + i * 0.055, direction) for i in range(count) for direction in (-1, 1))

    def draw(self, p: QPainter, center: QPointF, now: float, scale: float, team: str) -> None:
        if self.context is None:
            return
        age = now - self.started
        if age > 1.1:
            self.context = None
            return
        base = QColor("#9cc1eb") if team == "ct" else QColor("#d9ac5b")
        flash = 1.0 if age < 0.033 else max(0.0, 1.0 - (age - 0.033) / 0.266)
        mixed = QColor(
            int(base.red() + (255 - base.red()) * flash),
            int(base.green() + (255 - base.green()) * flash),
            int(base.blue() + (255 - base.blue()) * flash),
        )
        gradient = mixed
        p.save()
        pen = QPen(gradient, max(1.0, scale))
        p.setPen(pen)
        p.drawLine(QPointF(center.x() - 175 * scale, center.y()), QPointF(center.x() + 175 * scale, center.y()))
        for wave in self.waves:
            wt = (now - wave.started) / 0.20
            if 0 <= wt <= 1:
                c = QColor(base)
                c.setAlphaF((1.0 - wt) ** 2)
                p.setPen(QPen(c, max(0.7, 1.5 * (1.0 - wt) * scale)))
                x = center.x() + wave.direction * (25.0 + 150.0 * _ease_out_cubic(wt)) * scale
                p.drawLine(QPointF(center.x(), center.y()), QPointF(x, center.y()))
        p.restore()
        suffix = "ct" if team == "ct" else "t"
        self.assets.draw_centered(p, f"killicon_card_bar_{suffix}.png", center, 40.0, 1.0, scale=scale)
        self.assets.draw_centered(p, f"killicon_card_bar_{suffix}.png", center, 40.0, flash, scale=scale, additive=True)
        _draw_ring(p, center, age, self.context.kill_type, scale)


class BattlefieldRenderer:
    def __init__(self, assets: AssetBank) -> None:
        self.assets, self.queue, self.context, self.started = assets, [], None, -1.0

    def trigger(self, context: TriggerContext, now: float) -> None:
        if context.kill_type != "ASSIST" and len(self.queue) < 5:
            self.queue.append(context)
        if self.context is None:
            self.context, self.started = self.queue.pop(0), now

    def draw(self, p: QPainter, center: QPointF, now: float, scale: float) -> None:
        if self.context is None:
            return
        age = now - self.started
        if age > 4.5:
            self.context = self.queue.pop(0) if self.queue else None
            self.started = now
            return
        alpha = min(1.0, age / 0.20, max(0.0, (4.5 - age) / 0.20))
        global_scale = 0.7 + 0.3 * _ease_out_cubic(age / 0.20)
        icon_key = TYPE_TEXTURE.get(self.context.kill_type, "default")
        name = f"killicon_battlefield1_{icon_key}.png"
        icon_center = QPointF(center.x() - 118 * scale, center.y())
        self.assets.draw_centered(p, name, icon_center, 25.0, alpha, scale=scale * global_scale)
        p.save()
        p.setOpacity(alpha)
        font = QFont("Poppins", max(7, int(9 * scale)))
        p.setFont(font)
        p.setPen(QColor("#FFFFFF"))
        weapon = "17MM PROJECTILE"
        victim = self.context.victim_name or f"ROBOT {self.context.victim_id or '?'}"
        p.drawText(QRectF(center.x() - 94 * scale, center.y() - 19 * scale, 125 * scale, 18 * scale), Qt.AlignmentFlag.AlignLeft, weapon)
        font.setPointSizeF(max(8, 10.8 * scale))
        p.setFont(font)
        p.setPen(QColor("#FF4A4A"))
        p.drawText(QRectF(center.x() - 94 * scale, center.y() + 1 * scale, 170 * scale, 20 * scale), Qt.AlignmentFlag.AlignLeft, victim)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(center.x() + 85 * scale, center.y() - 9 * scale, 50 * scale, 20 * scale), Qt.AlignmentFlag.AlignCenter, "0")
        p.restore()


class ValorantRenderer:
    BAR_LAYOUT = ((0,), (-55, 55), (-70, 0, 70), (-90, -30, 30, 90), (-105, -52, 0, 52, 105), (-120, -72, -24, 24, 72, 120))

    def __init__(self, assets: AssetBank) -> None:
        self.assets, self.context, self.started, self.spin = assets, None, -1.0, 1.0
        self.particles: list[tuple[float, float, float]] = []

    def trigger(self, context: TriggerContext, now: float) -> None:
        if context.combo <= 0 or context.kill_type in ("ASSIST", "DESTROY_VEHICLE"):
            return
        self.context, self.started = context, now
        self.spin = random.choice((-1.0, 1.0))
        rng = random.Random(context.combo * 97 + context.victim_id)
        self.particles = [(rng.uniform(0, math.tau), rng.uniform(24, 64), rng.uniform(1.2, 3.0)) for _ in range(22)]

    def draw(self, p: QPainter, center: QPointF, now: float, scale: float, style: ValorantStyle) -> None:
        if self.context is None:
            return
        age = now - self.started
        duration = 2.60
        if age > duration:
            self.context = None
            return
        alpha = min(1.0, age / 0.10, max(0.0, (duration - age) / 0.22))
        accent = QColor(style.accent)
        token = style.token
        singularity = token.startswith("singularity")
        gaia = token.startswith("gaia")
        bubblegum = token.startswith("bubblegum")
        prelude = token.startswith("prelude")
        primordium = token.startswith("primordium")
        rgx = token.startswith("rgx")
        emblem_tune = 0.9 if singularity or gaia else 0.55 if bubblegum else 0.6 if token == "champions_2021" else 0.5 if prelude or token == "radiant_crisis_001" else 0.4 if primordium else 0.35 if rgx else 1.0
        frame_width_ratio = 0.8 if singularity or token == "champions_2021" or prelude or token == "radiant_crisis_001" or rgx else 1.0
        bar_radius_offset = 4.0 if gaia else 9.0 if prelude else 0.0
        particle_y = 55.0 if bubblegum else 45.0
        particle_scale = 1.2 if bubblegum else 0.9 if primordium else 1.0
        halo_radius = 25.0 if token == "glitchpop" or primordium or token == "radiant_crisis_001" or rgx else 30.0
        icon_y = -16.0 * (1.0 - _ease_out_cubic(age / 0.10))
        root = QPointF(center.x(), center.y() + 6.0)
        icon_center = QPointF(root.x(), root.y() + (icon_y if style.emblem_only_motion else 0.0) * scale)
        frame_center = QPointF(root.x(), root.y() + (0.0 if style.emblem_only_motion else icon_y * scale))
        particle_t = min(1.0, age / 0.62)
        base_frame = min(48, int(age * 1000.0 / 25.0))
        base_texture = f"killicon_valorant_particle_base_t{1 + ((self.context.combo - 1) % 3)}.png"
        base_count = 42 if base_texture.endswith("t3.png") else 49
        self.assets.draw_vertical_frame(
            p, base_texture, min(base_count - 1, base_frame), base_count,
            QPointF(root.x(), root.y() + particle_y * scale), 112.0, alpha * (1.0 - particle_t * 0.35), particle_scale * scale,
        )
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for angle, radius, size in self.particles:
            r = radius * _ease_out_cubic(particle_t) * scale
            point = QPointF(root.x() + math.cos(angle) * r, root.y() + math.sin(angle) * r)
            c = QColor(accent)
            c.setAlphaF(alpha * (1.0 - particle_t) * 0.72)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(point, size * scale, size * scale)
        p.restore()
        halo_t = min(1.0, age / 0.62)
        hc = QColor(accent)
        hc.setAlphaF(alpha * (1.0 - halo_t) * 0.55)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(hc, 1.5 * scale))
        halo_value = (halo_radius * 0.5 + halo_radius * 0.5 * _ease_out_cubic(halo_t)) * scale
        p.drawEllipse(root, halo_value, halo_value)
        combo = max(1, min(6, self.context.combo))
        bounce = 0.0
        if 0.02 <= age < 0.18:
            bounce = 9.0 * _ease_out_cubic((age - 0.02) / 0.16)
        elif age < 0.62:
            bounce = 9.0 * max(0.0, 1.0 - (age - 0.27) / 0.35)
        rotation = 0.0
        if age >= 0.75:
            spin_t = min(1.0, (age - 0.75) / (1.0 if combo >= 5 else 0.70))
            rotation = (360.0 if combo >= 5 else 180.0) * _ease_out_cubic(spin_t) * self.spin
        bar_name = f"killicon_valorant_{style.token}_bar.png"
        if self.assets.image(bar_name).isNull():
            bar_name = "killicon_valorant_bar.png"
        for angle in self.BAR_LAYOUT[combo - 1]:
            rad = math.radians(angle + rotation)
            bar_center = QPointF(root.x() + math.sin(rad) * (36 + bar_radius_offset + bounce) * scale, root.y() - math.cos(rad) * (36 + bar_radius_offset + bounce) * scale)
            entry_scale = 1.6 + (1.0 - 1.6) * _ease_out_cubic(age / 0.18)
            self.assets.draw_centered(p, bar_name, bar_center, 32.0, alpha, angle + rotation, entry_scale * scale)
        if style.token.startswith("bubblegum_deathwish"):
            frame_name = "killicon_valorant_bubblegum_deathwish_frame.png"
        elif style.token.startswith("primordium"):
            frame_name = "killicon_valorant_primordium_frame.png"
        elif style.token.startswith("rgx_11z_pro"):
            frame_name = "killicon_valorant_rgx_11z_pro_frame.png"
        else:
            frame_name = f"killicon_valorant_{style.token}_frame.png"
        if self.assets.image(frame_name).isNull():
            frame_name = "killicon_valorant_base_frame.png"
        self.assets.draw_centered(p, frame_name, frame_center, 116.0, alpha, scale=0.95 * scale, width_ratio=frame_width_ratio)
        if style.blade:
            blade_rotation = self.spin * 360.0 * _ease_out_cubic(min(1.0, age / 2.0))
            self.assets.draw_centered(p, style.blade, frame_center, 116.0 * 0.60, alpha, blade_rotation, 0.95 * scale)
        emblem_token = {
            "bubblegum_deathwish_v1": "bubblegum_deathwish_v3",
            "bubblegum_deathwish_v3": "bubblegum_deathwish_v1",
        }.get(style.token, style.token)
        emblem_name = f"killicon_valorant_{emblem_token}_emblem.png"
        self.assets.draw_centered(p, emblem_name, icon_center, 116.0, alpha, scale=0.90 * 0.95 * emblem_tune * scale)
        flash_phase = int(age / 0.08)
        if flash_phase < 4:
            flash_alpha = 0.9 if flash_phase % 2 == 0 else 0.0
            self.assets.draw_centered(p, emblem_name, icon_center, 116.0, alpha * flash_alpha, scale=0.90 * 0.95 * emblem_tune * scale, additive=True)
        if self.context.kill_type == "HEADSHOT":
            hs_scale = 1.8 + (1.0 - 1.8) * _ease_out_cubic(age / 0.25)
            hs_center = QPointF(icon_center.x(), icon_center.y() + style.headshot_y * scale)
            self.assets.draw_centered(p, "killicon_valorant_headshot.png", hs_center, 116.0 * 0.16, alpha, scale=hs_scale * scale)
        hero_frame = min(19, int(age * 1000.0 / 29.0))
        if token not in ("prime", "glitchpop") and not singularity:
            self.assets.draw_vertical_frame(p, "killicon_valorant_particle_hero_flame.png", hero_frame, 20, QPointF(icon_center.x(), icon_center.y() - 30.0 * scale), 116.0, alpha, 0.58 * scale)
        if combo >= 5:
            spark_frame = min(51, int(age * 1000.0 / 25.0))
            large_scale = 2.2 if bubblegum or prelude or primordium or token == "radiant_crisis_001" or rgx else 1.0
            self.assets.draw_vertical_frame(p, "killicon_valorant_particle_large_sparks.png", spark_frame, 52, QPointF(icon_center.x(), icon_center.y() + 2.0 * scale), 116.0, alpha, 0.46 * large_scale * scale)
        if self.context.kill_type == "HEADSHOT":
            x_frame = min(28, int(age * 1000.0 / 25.0))
            self.assets.draw_vertical_frame(p, "killicon_valorant_particle_x_sparks.png", x_frame, 29, QPointF(icon_center.x(), icon_center.y() - 8.0 * scale), 116.0, alpha, 0.32 * scale)


@dataclass
class FeedLine:
    context: TriggerContext
    started: float


class CompanionHud:
    def __init__(self) -> None:
        self.lines: list[FeedLine] = []

    def trigger(self, context: TriggerContext, now: float) -> None:
        self.lines.append(FeedLine(context, now))
        self.lines = self.lines[-5:]

    def draw(self, p: QPainter, width: int, height: int, now: float, scale: float, elements: tuple[str, ...], offset: QPointF, anchor: QPointF | None = None) -> None:
        self.lines = [line for line in self.lines if now - line.started < 3.2]
        center_x = anchor.x() if anchor is not None else width * 0.5 + offset.x()
        feed_y = anchor.y() - 92 * scale if anchor is not None else height * 0.63 + offset.y()
        combo_y = anchor.y() - 48 * scale if anchor is not None else height * 0.70 + offset.y()
        score_y = anchor.y() + 38 * scale if anchor is not None else height - 158 * scale + offset.y()
        bonus_y = anchor.y() + 62 * scale if anchor is not None else height - 132 * scale + offset.y()
        ace_y = anchor.y() - 190 * scale if anchor is not None else height * 0.23 + offset.y()
        if "kill_feed" in elements:
            for index, line in enumerate(reversed(self.lines)):
                age = now - line.started
                alpha = min(1.0, age / 0.15, max(0.0, (3.2 - age) / 0.25))
                y = feed_y - index * 22 * scale
                p.setOpacity(alpha)
                p.setFont(QFont("Poppins", max(7, int(9 * scale))))
                p.setPen(QColor("#FFFFFF"))
                victim = line.context.victim_name or f"ROBOT {line.context.victim_id or '?'}"
                p.drawText(QRectF(center_x - 180 * scale, y, 360 * scale, 18 * scale), Qt.AlignmentFlag.AlignCenter, f"ELIMINATED  {victim}")
            p.setOpacity(1.0)
        latest = self.lines[-1].context if self.lines else None
        if latest and "combo_subtitle" in elements and latest.combo > 1:
            p.setFont(QFont("Poppins", max(11, int(16 * scale)), QFont.Weight.Bold))
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(center_x - 200 * scale, combo_y, 400 * scale, 28 * scale), Qt.AlignmentFlag.AlignCenter, f"{latest.combo} KILL COMBO")
        if latest and "score" in elements:
            p.setFont(QFont("Poppins", max(8, int(10 * scale)), QFont.Weight.Bold))
            p.setPen(QColor("#FFFFFF"))
            score = latest.score_delta or 100
            p.drawText(QRectF(center_x + 58 * scale, score_y, 120 * scale, 22 * scale), Qt.AlignmentFlag.AlignLeft, f"+{score}")
        if latest and "bonus" in elements:
            tags = latest.tags or (("ROBOT DESTROYED",) if latest.kill_type == "DESTROY_VEHICLE" else ("ELIMINATION",))
            p.setFont(QFont("Poppins", max(6, int(8 * scale))))
            p.setPen(QColor("#E8EDF5"))
            for i, tag in enumerate(tags[:4]):
                p.drawText(QRectF(center_x + 58 * scale, bonus_y + i * 17 * scale, 220 * scale, 18 * scale), Qt.AlignmentFlag.AlignLeft, str(tag))
        if latest and latest.combo >= 6:
            p.setFont(QFont("Poppins", max(18, int(28 * scale)), QFont.Weight.Bold))
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(center_x - 110 * scale, ace_y, 220 * scale, 40 * scale), Qt.AlignmentFlag.AlignCenter, "ACE")


class KillFxRenderer:
    """按 GD656 原始 renderer 职责和时序实现的击杀 HUD 调度器。"""

    def __init__(self, asset_root: Path) -> None:
        self.assets = AssetBank(asset_root)
        self.texture_root = self.assets.texture_root
        self.sound_root = self.assets.sound_root
        self.sounds = SoundPool(self.assets.sound_root)
        self.scrolling = ScrollingRenderer(self.assets)
        self.combo = ComboRenderer(self.assets)
        self.card = CardRenderer(self.assets)
        self.card_bar = CardBarRenderer(self.assets)
        self.battlefield = BattlefieldRenderer(self.assets)
        self.valorant = ValorantRenderer(self.assets)
        self.companion = CompanionHud()
        self.combo_count = 0
        self.last_kill_at = 0.0
        self.last_context: TriggerContext | None = None
        self._layout_anchor: QPointF | None = None

    def clear(self) -> None:
        self.__init__(self.assets.texture_root.parent)

    @staticmethod
    def elements_for_preset(preset: str, visibility: str) -> tuple[str, ...]:
        if preset.startswith("Valorant "):
            elements = ("valorant",)
        else:
            elements = PRESET_ELEMENTS.get(preset, PRESET_ELEMENTS["Default Scrolling"])
        if visibility == "Icons Only":
            return tuple(item for item in elements if item in ("scrolling", "scrolling_bf5", "combo", "card", "card_bar", "battlefield1", "valorant"))
        if visibility == "Full HUD":
            companions = ("kill_feed", "combo_subtitle", "score", "bonus")
            return tuple(dict.fromkeys((*elements, *companions)))
        return elements

    def trigger(self, event: CombatEvent, *, combo_window: float, sound_enabled: bool, volume: float, preset: str, visibility: str, now_override: float | None = None) -> None:
        if event.kind not in ("elimination", "death"):
            return
        now = time.perf_counter() if now_override is None else now_override
        if event.kind == "death":
            self.combo_count = 0
            return
        self.combo_count = self.combo_count + 1 if now - self.last_kill_at <= combo_window else 1
        self.last_kill_at = now
        combo = event.combo if event.combo > 0 else self.combo_count
        context = TriggerContext(
            event.kill_type if event.kill_type in KILL_TYPES else "NORMAL", combo,
            event.victim_id, event.victim_name, event.distance_m, event.score_delta, event.tags, now,
        )
        self.last_context = context
        elements = self.elements_for_preset(preset, visibility)
        if "scrolling" in elements or "scrolling_bf5" in elements:
            self.scrolling.trigger(context, now)
        if "combo" in elements:
            self.combo.trigger(context, now)
        if "card" in elements:
            self.card.trigger(context, now)
        if "card_bar" in elements:
            self.card_bar.trigger(context, now)
        if "battlefield1" in elements:
            self.battlefield.trigger(context, now)
        if "valorant" in elements:
            self.valorant.trigger(context, now)
        self.companion.trigger(context, now)
        if sound_enabled:
            self._play_sound(context, preset, volume)

    def _play_sound(self, context: TriggerContext, preset: str, volume: float) -> None:
        self.sounds.play(self._sound_name(context.kill_type, context.combo, preset), volume)

    def _sound_name(self, kill_type: str, combo: int, preset: str) -> str:
        key = TYPE_TEXTURE.get(kill_type, "default")
        if preset == "Combo":
            name = f"combokillsound_{max(1, min(6, combo))}_cf.ogg"
        elif preset == "Card":
            name = f"cardkillsound_{'armorheadshot' if key == 'headshot' else key}_cs.ogg"
        elif preset == "Battlefield 1":
            name = "headshotkillsound_bf1.ogg" if key == "headshot" else "killsound_bf1.ogg"
        elif preset == "Battlefield 5":
            name = "vehiclekillsound_bf5.ogg" if key == "destroyvehicle" else "headshotkillsound_bf5.ogg" if key == "headshot" else "killsound_bf5.ogg"
        elif preset.startswith("Valorant "):
            style = VALORANT_STYLES.get(preset.removeprefix("Valorant "), VALORANT_STYLES["Prime"])
            name = "valorant_headshot.ogg" if key == "headshot" else f"{style.sound_prefix}{max(1, min(5, combo))}.ogg"
        else:
            name = {"headshot": "headshotkillsound_df.ogg", "explosion": "explosionkillsound_df.ogg", "crit": "critkillsound_df.ogg"}.get(key, "killsound_df.ogg")
        return name

    def preload_preset_sounds(self, preset: str) -> None:
        names = {
            self._sound_name(kill_type, combo, preset)
            for kill_type in KILL_TYPES
            for combo in range(1, 7)
        }
        self.sounds.preload(names)

    def draw(self, painter: QPainter, width: int, height: int, *, preset: str, visibility: str, team: str, scale: float, anchor_policy: str, x_offset: float, y_offset: float, responsive_scale: bool = True, device_pixel_ratio: float = 1.0, now_override: float | None = None) -> None:
        now = time.perf_counter() if now_override is None else now_override
        elements = self.elements_for_preset(preset, visibility)
        layout = KillFxLayout.create(
            width, height,
            policy=anchor_policy,
            user_scale=scale,
            responsive=responsive_scale,
            device_pixel_ratio=device_pixel_ratio,
            x_offset=x_offset,
            y_offset=y_offset,
        )
        if self._layout_anchor is None:
            self._layout_anchor = QPointF(layout.anchor)
        else:
            factor = 1.0 - math.exp(-(1.0 / 60.0) / 0.16)
            self._layout_anchor = QPointF(
                self._layout_anchor.x() + (layout.anchor.x() - self._layout_anchor.x()) * factor,
                self._layout_anchor.y() + (layout.anchor.y() - self._layout_anchor.y()) * factor,
            )
        layout = KillFxLayout(self._layout_anchor, layout.scale, layout.policy, layout.offset)
        offset = QPointF(x_offset, y_offset) if anchor_policy == "Manual" else QPointF(0.0, 0.0)
        team_key = "ct" if team in ("Alliance", "CT Blue") else "t"
        bottom = layout.primary(120.0)
        if "scrolling" in elements:
            self.scrolling.draw(painter, bottom, now, layout.scale, False)
        if "scrolling_bf5" in elements:
            self.scrolling.draw(painter, bottom, now, layout.scale, True)
        if "combo" in elements:
            self.combo.draw(painter, layout.primary(120.0), now, 0.60 * layout.scale)
        if "card" in elements:
            self.card.draw(painter, layout.primary(35.0), now, layout.scale, team_key)
        if "card_bar" in elements:
            self.card_bar.draw(painter, layout.primary(40.0), now, layout.scale, team_key)
        if "battlefield1" in elements:
            self.battlefield.draw(painter, layout.primary(100.0), now, layout.scale)
        if "valorant" in elements:
            style = VALORANT_STYLES.get(preset.removeprefix("Valorant "), VALORANT_STYLES["Prime"])
            self.valorant.draw(painter, layout.primary(80.0), now, 0.85 * layout.scale, style)
        companion_anchor = layout.anchor if anchor_policy == "Combat Focus" else None
        self.companion.draw(painter, width, height, now, layout.scale, elements, offset, companion_anchor)
