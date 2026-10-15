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
from .kill_audio import KillSoundPool
from .ui.cs2_assets import local_cs2_assets
from .ui.cs2_kill_feedback import Cs2KillFeedback


KILL_TYPES = ("NORMAL", "HEADSHOT", "EXPLOSION", "CRIT", "ASSIST", "DESTROY_VEHICLE")
TYPE_TEXTURE = {
    "NORMAL": "default", "HEADSHOT": "headshot", "EXPLOSION": "explosion",
    "CRIT": "crit", "ASSIST": "assist", "DESTROY_VEHICLE": "destroyvehicle",
}

# gd656 LOCKED_TARGET「锁定目标」加分项（ServerCombatEngine.java:856-863：
# 对同一目标持续交战 ≥ 窗口时间后的击杀追加分）。窗口取开源 10s 的一半节奏
# （审计 T2 建议 5s）。标签写入 TriggerContext.tags，不改 CombatEvent 字段。
LOCKED_TARGET_TAG = "locked_target"
LOCK_TARGET_WINDOW_S = 5.0

# ── gd656 T6 工程一致性：原写死参数提升为模块常量（审计报告 §四 T6）──────────
# 卡牌阵营双色（原 CardRenderer/CardBarRenderer 各写一份 #9cc1eb / #d9ac5b）
TEAM_COLORS = {"ct": "#9cc1eb", "t": "#d9ac5b"}
# honor 徽章标签金色（原 HonorRenderer 写死 #F5E9C8）
HONOR_LABEL_COLOR = "#F5E9C8"
# 击杀扩散环三色（原 _draw_ring 写死 HEADSHOT/EXPLOSION/CRIT 三值）
RING_COLORS = {"HEADSHOT": "#D4B800", "EXPLOSION": "#F77F00", "CRIT": "#9CCC65"}
# 滚动图标尺寸/间距（原 ScrollingRenderer 写死 64.0 与 64+4 步进）
SCROLLING_ICON_SIZE_PX = 64.0
SCROLLING_ICON_GAP_PX = 4.0
# BF1 横幅武器名（原写死 "17MM PROJECTILE"）；按击杀类型映射，RM 语义：
# 爆炸/毁车归 42MM 弹道，其余归 17MM，助攻单独标注。
BATTLEFIELD_WEAPON_LABELS = {
    "NORMAL": "17MM PROJECTILE",
    "HEADSHOT": "17MM PROJECTILE",
    "CRIT": "17MM PROJECTILE",
    "EXPLOSION": "42MM PROJECTILE",
    "DESTROY_VEHICLE": "42MM PROJECTILE",
    "ASSIST": "ASSIST FIRE",
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
    "CS2 Native": ("cs2",),
    "Battlefield 1": ("score", "bonus", "battlefield1", "match_progress"),
    "Minimal": ("score", "bonus"),
    "PUBG Subtitle": ("combo_subtitle", "kill_feed"),
    "Battlefield 5": ("kill_feed", "score", "bonus", "scrolling_bf5", "match_progress"),
    # 上游 00037 官方预设：BF6 = 滚动图标 + 击杀信息流 + 加分标签 + 分数
    # + 命中信息 + 荣誉徽章；本客户端另挂基地推进条
    "Battlefield 6": ("kill_feed", "score", "bonus", "scrolling_bf6", "hit_info", "honor", "match_progress"),
    "Total Battlefield": ("score", "bonus", "scrolling", "match_progress"),
}

# 滚动图标风格 -> (纹理前缀, 允许的类型键)。battlefield5 原版只有
# headshot/assist/destroyvehicle 三种专用图标，其余回落 default；
# battlefield6 是 GD656 1.1.0 新增的全类型图标集。
SCROLLING_STYLES = {
    "default": ("killicon_scrolling_", None),
    "bf5": ("killicon_battlefield5_", frozenset({"headshot", "assist", "destroyvehicle"})),
    "bf6": ("killicon_battlefield6_", None),
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


# ── gd656 T4/T5 特效基础件（移植自 gd656killicon client/render/effect/）────────
# T4 来源: DigitalScrollEffect.java —— 数字滚动逼近目标；
#          B 版新增 setTargetValue 断点续滚（中途改目标不跳变、不重置）。
# T5 来源: TextScrambleEffect.java（乱码翻牌定格）/ TextFadeEffect.java（字符级渐显）。
# 均为纯数学/纯绘制，无 FBO、无定时器，与既有逐帧重绘架构一致。


class DigitalScroll:
    """数字滚动（gd656 DigitalScrollEffect 客户端移植）。

    显示值在 ``duration_s`` 内以 ease-out 逼近目标值；:meth:`update_target`
    可逐帧安全调用（目标未变化时是 no-op），:meth:`set_target` 改目标时把
    新起点取为当前动画瞬时值——即上游 B 版 setTargetValue 的断点续滚语义，
    连杀快速刷新分数时数字平滑接力而不是跳变。
    """

    def __init__(self, duration_s: float = 0.45, initial: float = 0.0) -> None:
        self.duration_s = max(0.01, float(duration_s))
        self._from = float(initial)
        self._target = float(initial)
        # 远古时间戳 ⇒ value() 直接落在目标值（未启动时不播动画）。
        self._started = -1.0e9

    def set_target(self, target: float, now: float) -> None:
        """改目标并从当前显示值断点续滚。"""
        self._from = float(self.value(now))
        self._target = float(target)
        self._started = now

    def update_target(self, target: float, now: float) -> bool:
        """逐帧路径专用：目标真的变化才滚动，返回是否触发了一次续滚。"""
        target = float(target)
        if target == self._target:
            return False
        self.set_target(target, now)
        return True

    def reset(self, value: float = 0.0) -> None:
        """归零（不播动画）。"""
        self._from = self._target = float(value)
        self._started = -1.0e9

    def value(self, now: float) -> int:
        elapsed = now - self._started
        t = 1.0 if elapsed >= self.duration_s else max(0.0, elapsed) / self.duration_s
        return int(round(self._from + (self._target - self._from) * _ease_out_cubic(t)))


_SCRAMBLE_CHARSET = "!<>-_\\/[]{}=+*^?#______"


def _scramble_reveal(text: str, progress: float, tick: int, *, charset: str = _SCRAMBLE_CHARSET) -> str:
    """T5 TextScrambleEffect：字符从乱码逐渐向左定格为真实文本。

    ``progress`` ∈ [0,1] 为揭示进度；空格保持占位使宽度稳定（居中排版不抖动）；
    未定格位置每帧换随机字符（``tick`` 为帧序号，约 30fps 一换），
    progress ≥ 1 时精确返回原文。
    """
    if progress >= 1.0:
        return text
    n = len(text)
    if n == 0:
        return text
    solid = int(n * max(0.0, min(1.0, progress)))
    rng = random.Random((tick * 2654435761 + n * 97) & 0xFFFFFFFF)
    return "".join(
        ch if (i < solid or ch == " ") else charset[rng.randrange(len(charset))]
        for i, ch in enumerate(text)
    )


def _draw_fade_in_text(
    p: QPainter,
    rect: QRectF,
    text: str,
    progress: float,
    flags: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignCenter,
) -> None:
    """T5 TextFadeEffect：字符级渐显（波前从左往右扫过，先到的字先亮）。

    progress ∈ [0,1]；≥1 时等价普通 drawText。按连续同透明度的字符段
    合并绘制，控制 drawText 次数；整段基于总宽预先定位以支持居中。
    """
    n = len(text)
    if n == 0:
        return
    if progress >= 1.0:
        p.drawText(rect, flags, text)
        return
    from PySide6.QtGui import QFontMetrics  # 局部导入：仅特效路径使用

    metrics = QFontMetrics(p.font())
    advances = [metrics.horizontalAdvance(ch) for ch in text]
    total_w = sum(advances)
    lead = max(0.0, min(1.0, progress)) * (n + 3)  # 波前位置（字符索引空间）
    h = metrics.height()
    if flags & Qt.AlignmentFlag.AlignRight:
        x = rect.right() - total_w
    elif flags & Qt.AlignmentFlag.AlignCenter:
        x = rect.center().x() - total_w / 2.0
    else:
        x = rect.left()
    baseline_top = rect.center().y() - h / 2.0
    index = 0
    pen_color = p.pen().color()
    while index < n:
        alpha = max(0.0, min(1.0, lead - index))
        run_end = index
        while run_end < n and abs(max(0.0, min(1.0, lead - run_end)) - alpha) < 1e-3:
            run_end += 1
        chunk = text[index:run_end]
        width = sum(advances[index:run_end])
        if alpha > 0.01:
            c = QColor(pen_color)
            c.setAlphaF(alpha * pen_color.alphaF())
            p.setPen(QColor(c))
            p.drawText(QRectF(x, baseline_top, width + 1.0, h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, chunk)
        x += width
        index = run_end


class AssetBank:
    def __init__(self, root: Path) -> None:
        self.texture_root = root / "textures"
        self.sound_root = root / "sounds"
        self.images: dict[str, QImage] = {}
        self.frames: dict[tuple[str, int, int], QImage] = {}
        # Fixed power-of-two levels, not animation-size keys. Retaining the QImage
        # and its cacheKey lets Qt reuse uploads instead of replacing them per frame.
        self._minified: dict[tuple[int, int], QImage] = {}

    def image(self, name: str) -> QImage:
        if name not in self.images:
            self.images[name] = QImage(str(self.texture_root / name))
        return self.images[name]

    def _filtered_image(self, p: QPainter, image: QImage, width: float, height: float) -> QImage:
        """Choose an area-filtered level at least as large as its physical target.

        deviceTransform includes painter scaling, rotation and device pixel ratio.
        A final smooth draw handles the remaining <2x reduction. Each level is
        computed once, from the original (never repeatedly blurred). All levels
        together cost at most ~1/3 of the original pixels for square textures;
        the 12-level cap also bounds pathological/extremely small animations.
        """
        transform = p.deviceTransform()
        target_w = abs(width) * math.hypot(transform.m11(), transform.m12())
        target_h = abs(height) * math.hypot(transform.m21(), transform.m22())
        reduction = min(image.width() / max(1.0, target_w), image.height() / max(1.0, target_h))
        level = min(12, max(0, int(math.floor(math.log2(max(1.0, reduction))))))
        if level == 0:
            return image
        key = (image.cacheKey(), level)
        if key not in self._minified:
            divisor = 1 << level
            self._minified[key] = image.scaled(
                max(1, image.width() // divisor), max(1, image.height() // divisor),
                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
        return self._minified[key]

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
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(QRectF(-w / 2.0, -h / 2.0, w, h), self._filtered_image(p, image, w, h))
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
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(QRectF(-w / 2.0, -h / 2.0, w, h), self._filtered_image(p, frame_image, w, h))
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
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(QRectF(-width / 2.0, -height, width, height), self._filtered_image(p, image, width, height))
        p.restore()


class SoundPool(KillSoundPool):
    def __init__(self, root: Path, count: int = 8) -> None:
        super().__init__(root, count=count)


class LockedTargetTracker:
    """LOCKED_TARGET「锁定目标」加分判定（gd656 ServerCombatEngine 客户端近似）。

    上游语义：对同一目标持续交战 ≥ 窗口时间后的击杀加分。本客户端的裁判/视觉
    数据源都不持续下发“当前正在交战哪台车”（视觉链路只在击杀瞬间给出
    victim_id），因此用两条本地证据近似：
    ① 同一 victim_id 在窗口内重复出现在事件流（目标事件/助攻后再击杀）；
    ② 击杀前存在持续命中链（notify_hit 脉冲间隔 ≤ 链间隙、链长 ≥ 窗口）。
    不改 CombatEvent 字段与 KillFx 对外接口；LocalTargetSelector 的实时
    track 稳定期接线需动 tongji_yolo.py / combat_events.py（本轮禁改文件，
    见移植报告限制说明）。
    """

    CHAIN_GAP_S = 1.6

    def __init__(self, window_s: float = LOCK_TARGET_WINDOW_S) -> None:
        self.window_s = float(window_s)
        self._sightings: dict[int, float] = {}
        self._hit_pulses: list[float] = []

    def observe_target(self, victim_id: int, now: float) -> bool:
        """记录一次目标出现；返回该目标是否已在窗口内形成“重复出现”稳定期。"""
        if victim_id <= 0:
            return False
        self._sightings = {
            vid: ts for vid, ts in self._sightings.items() if now - ts <= self.window_s
        }
        seen_before = victim_id in self._sightings
        self._sightings[victim_id] = now
        return seen_before

    def observe_hit(self, now: float) -> None:
        """命中脉冲（notify_hit 边沿）；用于持续交战链判定。"""
        self._hit_pulses.append(now)
        horizon = now - max(self.window_s * 2.0, self.window_s + self.CHAIN_GAP_S)
        self._hit_pulses = [ts for ts in self._hit_pulses if ts >= horizon]

    def reset_engagement(self) -> None:
        """己方阵亡即断开交战链（对齐上游换目标即清零的语义）。"""
        self._hit_pulses.clear()

    def is_locked_kill(self, victim_id: int, stable_sighting: bool, now: float) -> bool:
        """击杀瞬间判定：稳定期 sighting 或窗口内持续命中链任一成立即算锁定。"""
        if stable_sighting:
            return True
        pulses = [ts for ts in self._hit_pulses if now - self.window_s <= ts <= now]
        if not pulses:
            return False
        chain_start = pulses[-1]
        for ts in reversed(pulses):
            if chain_start - ts <= self.CHAIN_GAP_S:
                chain_start = ts
            else:
                break
        return (pulses[-1] - chain_start) >= self.window_s


def _draw_ring(p: QPainter, center: QPointF, age: float, kill_type: str, scale: float) -> None:
    if age < 0.10:
        return
    t = (age - 0.10) / 0.30
    if t < 0.0 or t > 1.0 or kill_type not in ("HEADSHOT", "EXPLOSION", "CRIT"):
        return
    color_key = RING_COLORS.get(kill_type)
    if color_key is None:
        return
    colors = {kill_type: QColor(color_key)}
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

    def draw(self, p: QPainter, center: QPointF, now: float, scale: float, style: str = "default") -> None:
        while self.pending and now - self.last_display >= 0.10:
            self._add(self.pending.pop(0), self.last_display + 0.10)
        self.icons = [i for i in self.icons if now - i.started < 3.55 and not (i.forced_fade > 0 and now - i.forced_fade > 0.30)]
        visible = self.icons[-7:]
        spacing = (SCROLLING_ICON_SIZE_PX + SCROLLING_ICON_GAP_PX) * scale
        prefix, allowed_keys = SCROLLING_STYLES.get(style, SCROLLING_STYLES["default"])
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
            if allowed_keys is not None and key not in allowed_keys:
                key = "default"
            name = f"{prefix}{key}.png"
            point = QPointF(x, center.y())
            self.assets.draw_centered(p, name, point, SCROLLING_ICON_SIZE_PX, alpha, scale=draw_scale)
            _draw_ring(p, point, age, state.context.kill_type, scale)


# gd656 加分项 -> honor 徽章映射。只在实际命中特殊条件时触发，
# 普通击杀不亮徽章，保持上游"加分项才有荣誉图标"的语义。
#
# 空中载具荣誉（T3，gd656 HonorRegistry：ace_pilot / ballistics / bail_rocket /
# flight_dispatcher，贴图早已在 assets\kill_fx\gd656\textures\honor\）：
# RM 裁判/视觉数据源没有空中载具字段（无搭乘/旋翼判定），因此——
# - ballistics（徒步摧毁载具）以 DESTROY_VEHICLE 近似：RM 载具击毁均由地面单位完成；
# - ace_pilot / flight_dispatcher / bail_rocket 仅当自定义协议
#   （polar.kill.v1 / CustomByteBlock JSON 的 tags）显式携带对应 tag，
#   或 victim_name 命中空中单位关键词时点亮（数据源不支持时的最小可行接线）。
AERIAL_VICTIM_KEYWORDS = ("uav", "drone", "aerial", "dart", "飞镖", "无人机")
HONOR_RULES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("honor_tank_destroyer.png", {"kill_types": {"DESTROY_VEHICLE"}}),
    ("honor_support.png", {"kill_types": {"ASSIST"}}),
    ("honor_headhunter.png", {"kill_types": {"HEADSHOT"}}),
    ("honor_warhead.png", {"kill_types": {"EXPLOSION"}}),
    ("honor_sharp_shooter.png", {"kill_types": {"CRIT"}}),
    ("honor_ace_pilot.png", {"tags_any": {"ace_pilot", "aerial_kill", "air_kill"}}),
    ("honor_ace_pilot.png", {"victim_name_any": AERIAL_VICTIM_KEYWORDS, "kill_types": {"DESTROY_VEHICLE"}}),
    ("honor_flight_dispatcher.png", {"tags_any": {"flight_dispatcher", "airborne_victim", "airborne_kill"}}),
    ("honor_bail_rocket.png", {"tags_any": {"bail_rocket"}}),
    ("honor_ballistics.png", {"kill_types": {"DESTROY_VEHICLE"}, "tags_none": {"ace_pilot", "aerial_kill", "air_kill"}}),
    ("honor_long_shot.png", {"min_distance_m": 8.0}),
    ("honor_marksman.png", {"min_distance_m": 5.0}),
    ("honor_frenzy.png", {"min_combo": 3}),
    ("honor_unstoppable.png", {"min_combo": 5}),
    ("honor_top.png", {"min_combo": 7}),
)


def honor_badges_for(context: TriggerContext) -> tuple[str, ...]:
    tags_lower = {str(tag).lower() for tag in context.tags}
    name_lower = context.victim_name.lower()
    badges: list[str] = []
    for name, rule in HONOR_RULES:
        if "kill_types" in rule and context.kill_type not in rule["kill_types"]:
            continue
        if "min_distance_m" in rule and context.distance_m < rule["min_distance_m"]:
            continue
        if "min_combo" in rule and context.combo < rule["min_combo"]:
            continue
        if "tags_any" in rule and not (tags_lower & set(rule["tags_any"])):
            continue
        if "tags_none" in rule and (tags_lower & set(rule["tags_none"])):
            continue
        if "victim_name_any" in rule and not any(
            keyword in name_lower for keyword in rule["victim_name_any"]
        ):
            continue
        badges.append(name)
        if len(badges) >= 2:
            break
    return tuple(badges)


class HonorRenderer:
    """gd656 kill_icon/honor：战况触发的荣誉徽章弹出。"""

    def __init__(self, assets: AssetBank) -> None:
        self.assets = assets
        self.active: list[tuple[str, float, str]] = []

    def trigger(self, context: TriggerContext, now: float) -> None:
        for badge in honor_badges_for(context):
            label = badge.removeprefix("honor_").removesuffix(".png").replace("_", " ").upper()
            self.active.append((badge, now, label))
        self.active = self.active[-3:]

    def draw(self, p: QPainter, center: QPointF, now: float, scale: float) -> None:
        self.active = [item for item in self.active if now - item[1] < 2.2]
        for index, (badge, started, label) in enumerate(self.active):
            age = now - started
            alpha = _ease_out_cubic(age / 0.25) * (1.0 if age < 1.8 else max(0.0, 1.0 - (age - 1.8) / 0.4))
            pop = 1.0 + 0.35 * (1.0 - _ease_out_cubic(age / 0.30))
            point = QPointF(center.x(), center.y() - (54.0 + index * 46.0) * scale)
            self.assets.draw_centered(p, f"honor/{badge}", point, 46.0, alpha, scale=pop * scale)
            font = QFont("Poppins", max(6, int(8 * scale)), QFont.Weight.Bold)
            p.setOpacity(alpha)
            p.setFont(font)
            p.setPen(QColor(HONOR_LABEL_COLOR))
            p.drawText(
                QRectF(point.x() - 110 * scale, point.y() + 16 * scale, 220 * scale, 16 * scale),
                Qt.AlignmentFlag.AlignCenter, label,
            )
            p.setOpacity(1.0)


class HitInfoRenderer:
    """gd656 subtitle/hit_info：受击/命中伤害提示与命中音效。"""

    def __init__(self, assets: AssetBank) -> None:
        self.assets = assets
        self.sounds: SoundPool | None = None
        self.lines: list[tuple[int, str, float]] = []
        self.last_hit_at = -1.0

    def attach_sounds(self, sounds: SoundPool) -> None:
        self.sounds = sounds

    def trigger(self, damage: int, kind: str, now: float, *, volume: float, enabled: bool) -> None:
        if damage <= 0:
            return
        label = {"42mm": "42MM HIT", "17mm+42mm": "COMBINED HIT"}.get(kind, "17MM HIT")
        self.lines.append((damage, label, now))
        self.lines = self.lines[-4:]
        self.last_hit_at = now
        if enabled and self.sounds is not None:
            self.sounds.play("hitsound_bf6.ogg", volume)

    def draw(self, p: QPainter, center: QPointF, now: float, scale: float, *, anchor: QPointF | None = None) -> None:
        """anchor 传入时命中弹字随特效锚点系绘制（T6 一致性修复：
        原先永远画在屏幕中心系，不随 Anchor Policy/偏移移动）。"""
        self.lines = [line for line in self.lines if now - line[2] < 1.4]
        origin_x, origin_y = (anchor.x(), anchor.y()) if anchor is not None else (center.x(), center.y())
        for index, (damage, label, started) in enumerate(reversed(self.lines)):
            age = now - started
            alpha = min(1.0, age / 0.10, max(0.0, (1.4 - age) / 0.30))
            rise = 26.0 * _ease_out_cubic(age / 0.35) * scale
            point = QPointF(origin_x + 40 * scale, origin_y - 90 * scale - rise - index * 20 * scale)
            font = QFont("Poppins", max(7, int(9 * scale)), QFont.Weight.Bold)
            p.setFont(font)
            p.setOpacity(alpha)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(point.x(), point.y(), 150 * scale, 16 * scale), Qt.AlignmentFlag.AlignLeft, f"+{damage}")
            p.setOpacity(alpha * 0.75)
            small = QFont("Poppins", max(5, int(7 * scale)))
            p.setFont(small)
            p.drawText(QRectF(point.x() + 44 * scale, point.y() + 1, 150 * scale, 14 * scale), Qt.AlignmentFlag.AlignLeft, label)
            p.setOpacity(1.0)


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
            p.setPen(QColor(TEAM_COLORS["ct"] if team == "ct" else TEAM_COLORS["t"]))
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
        base = QColor(TEAM_COLORS["ct"] if team == "ct" else TEAM_COLORS["t"])
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
    # T6：武器名按击杀类型映射（原写死 "17MM PROJECTILE"），可整体替换。
    WEAPON_LABELS = dict(BATTLEFIELD_WEAPON_LABELS)

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
        weapon = self.WEAPON_LABELS.get(self.context.kill_type, "17MM PROJECTILE")
        victim = self.context.victim_name or f"ROBOT {self.context.victim_id or '?'}"
        # T5 TextScrambleEffect：横幅文案前 0.35s 乱码翻牌入场。
        victim = _scramble_reveal(victim, age / 0.35, int(age * 30))
        p.drawText(QRectF(center.x() - 94 * scale, center.y() - 19 * scale, 125 * scale, 18 * scale), Qt.AlignmentFlag.AlignLeft, weapon)
        font.setPointSizeF(max(8, 10.8 * scale))
        p.setFont(font)
        p.setPen(QColor("#FF4A4A"))
        p.drawText(QRectF(center.x() - 94 * scale, center.y() + 1 * scale, 170 * scale, 20 * scale), Qt.AlignmentFlag.AlignLeft, victim)
        p.setPen(QColor("#FFFFFF"))
        # T6：分数不再写死 "0"，优先展示真实加分（与 CompanionHud 的
        # `score_delta or 100` 约定一致），无数据时回落 100。
        score_text = str(self.context.score_delta) if self.context.score_delta > 0 else "100"
        p.drawText(QRectF(center.x() + 85 * scale, center.y() - 9 * scale, 50 * scale, 20 * scale), Qt.AlignmentFlag.AlignCenter, score_text)
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
        # T4 DigitalScroll：+分数弹字从 0 滚到本次加分；连杀快速刷新时
        # set_target 断点续滚（gd656 B 版 setTargetValue 语义）。
        self.score_scroll = DigitalScroll(duration_s=0.45)

    def trigger(self, context: TriggerContext, now: float) -> None:
        self.lines.append(FeedLine(context, now))
        self.lines = self.lines[-5:]
        self.score_scroll.set_target(context.score_delta or 100, now)

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
                # T5 TextScrambleEffect：信息流行前 0.22s 乱码翻牌定格。
                text = _scramble_reveal(f"ELIMINATED  {victim}", age / 0.22, int(age * 30))
                p.drawText(QRectF(center_x - 180 * scale, y, 360 * scale, 18 * scale), Qt.AlignmentFlag.AlignCenter, text)
            p.setOpacity(1.0)
        latest = self.lines[-1].context if self.lines else None
        if latest and "combo_subtitle" in elements and latest.combo > 1:
            p.setFont(QFont("Poppins", max(11, int(16 * scale)), QFont.Weight.Bold))
            p.setPen(QColor("#FFFFFF"))
            subtitle_age = now - self.lines[-1].started
            # T5 TextFadeEffect：连杀字幕字符级渐显（前 0.28s 波前扫过）。
            _draw_fade_in_text(
                p,
                QRectF(center_x - 200 * scale, combo_y, 400 * scale, 28 * scale),
                f"{latest.combo} KILL COMBO",
                subtitle_age / 0.28,
            )
        if latest and "score" in elements:
            p.setFont(QFont("Poppins", max(8, int(10 * scale)), QFont.Weight.Bold))
            p.setPen(QColor("#FFFFFF"))
            # T4：数字滚动显示（替代瞬时跳变的 +score）。
            score = self.score_scroll.value(now)
            p.drawText(QRectF(center_x + 58 * scale, score_y, 120 * scale, 22 * scale), Qt.AlignmentFlag.AlignLeft, f"+{score}")
        if latest and "bonus" in elements:
            tags = latest.tags or (("ROBOT DESTROYED",) if latest.kill_type == "DESTROY_VEHICLE" else ("ELIMINATION",))
            tags = tuple(tags)
            locked = [tag for tag in tags if str(tag).lower() == LOCKED_TARGET_TAG]
            others = [tag for tag in tags if str(tag).lower() != LOCKED_TARGET_TAG]
            p.setFont(QFont("Poppins", max(6, int(8 * scale))))
            row_y = bonus_y
            if locked:
                # LOCKED_TARGET「锁定目标 +分数」：gd656 BonusTextBox 盒装简化版。
                text = f"锁定目标 +{latest.score_delta or 100}"
                box = QRectF(
                    center_x + 54 * scale,
                    row_y - 3 * scale,
                    (len(text) * 8.6 + 18.0) * scale,
                    20 * scale,
                )
                background = QColor("#0E1420")
                background.setAlphaF(0.72)
                p.setPen(QPen(QColor("#F5D98A"), 1.2))
                p.setBrush(background)
                p.drawRoundedRect(box, 3.0, 3.0)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QColor("#F5D98A"))
                p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
                row_y += 23 * scale
            p.setPen(QColor("#E8EDF5"))
            for i, tag in enumerate(others[:4]):
                p.drawText(QRectF(center_x + 58 * scale, row_y + i * 17 * scale, 220 * scale, 18 * scale), Qt.AlignmentFlag.AlignLeft, str(tag))
        if latest and latest.combo >= 6:
            p.setFont(QFont("Poppins", max(18, int(28 * scale)), QFont.Weight.Bold))
            p.setPen(QColor("#FFFFFF"))
            ace_age = now - self.lines[-1].started
            # T5 TextScrambleEffect：ACE 大字前 0.45s 乱码翻牌入场。
            ace_text = _scramble_reveal("ACE", ace_age / 0.45, int(ace_age * 30))
            p.drawText(QRectF(center_x - 110 * scale, ace_y, 220 * scale, 40 * scale), Qt.AlignmentFlag.AlignCenter, ace_text)


class MatchProgressRenderer:
    """战地风格比分推进条：敌方/我方基地血量消耗进度 + 红蓝比分。

    保留既有左右映射：左=己方基地已损失比例，右=敌方基地已损失比例。
    这不是比赛时间进度、胜利点或 CS 回合数。比例为 1 - hp/max；比分数字取
    GameStatus 的 red_score/blue_score。条的位置跟随灵动岛下边缘
    （anchor_bottom 传入时），岛展开/收起时一起灵动。
    """

    def __init__(self) -> None:
        self.shown_own = 0.0
        self.shown_enemy = 0.0
        self.shown_y: float | None = None
        # T4 DigitalScroll：比分数字滚动（比分跳变时平滑接力，不闪跳）。
        self.own_score_scroll = DigitalScroll(duration_s=0.60)
        self.enemy_score_scroll = DigitalScroll(duration_s=0.60)

    def draw(
        self,
        p: QPainter,
        width: int,
        now: float,
        scale: float,
        *,
        own_base_health: float | None,
        own_base_max: float,
        enemy_base_health: float | None,
        enemy_base_max: float,
        red_score: int | None,
        blue_score: int | None,
        own_blue: bool,
        anchor_bottom: float | None = None,
        minimum_y: float | None = None,
    ) -> None:
        own_known = (own_base_health is not None and math.isfinite(own_base_health)
                     and math.isfinite(own_base_max) and 0 <= own_base_health <= own_base_max and own_base_max > 0)
        enemy_known = (enemy_base_health is not None and math.isfinite(enemy_base_health)
                       and math.isfinite(enemy_base_max) and 0 <= enemy_base_health <= enemy_base_max and enemy_base_max > 0)
        own_progress = 1.0 - max(0.0, min(1.0, own_base_health / own_base_max)) if own_known else 0.0
        enemy_progress = 1.0 - max(0.0, min(1.0, enemy_base_health / enemy_base_max)) if enemy_known else 0.0
        if not own_known:
            self.shown_own = 0.0
        if not enemy_known:
            self.shown_enemy = 0.0
        # 平滑推进，血量跳变时条不闪跳
        factor = 1.0 - math.exp(-(1.0 / 60.0) / 0.35)
        self.shown_own += (own_progress - self.shown_own) * factor
        self.shown_enemy += (enemy_progress - self.shown_enemy) * factor
        # 跟随灵动岛：目标 y 取岛底 + 间隙；无岛时回落到固定槽位。
        target_y = (anchor_bottom + 12.0 * scale) if anchor_bottom is not None else 64.0 * scale
        if self.shown_y is None:
            self.shown_y = target_y
        else:
            # 与灵动岛形变同节奏的轻微跟随阻尼
            y_factor = 1.0 - math.exp(-(1.0 / 60.0) / 0.22)
            self.shown_y += (target_y - self.shown_y) * y_factor

        # The integrated CS row owns the space above this bound. Clamp during
        # expanding-island/layout transitions rather than easing through cards.
        if minimum_y is not None:
            self.shown_y = max(self.shown_y, minimum_y)
        bar_w = min(430.0, width * 0.42) * scale
        bar_h = 7.0 * scale
        y = self.shown_y
        gap = 26.0 * scale
        cx = width * 0.5
        left_rect = QRectF(cx - gap - bar_w, y, bar_w, bar_h)
        right_rect = QRectF(cx + gap, y, bar_w, bar_h)

        own_color = QColor("#4FA3FF") if own_blue else QColor("#FF5A4F")
        enemy_color = QColor("#FF5A4F") if own_blue else QColor("#4FA3FF")
        # 历史实现的左条为己方基地损失，右条为敌方基地损失（不反转数据）。
        own_rect, enemy_rect = (left_rect, right_rect)
        for rect, color, progress, known in ((own_rect, own_color, self.shown_own, own_known), (enemy_rect, enemy_color, self.shown_enemy, enemy_known)):
            track = QColor("#FFFFFF")
            track.setAlphaF(0.10)
            p.fillRect(rect, track)
            fill = QRectF(rect)
            fill.setWidth(rect.width() * max(0.0, min(1.0, progress)))
            p.fillRect(fill, color)
            if not known:
                p.setPen(QPen(QColor(190, 198, 204, 150), 1.0, Qt.PenStyle.DotLine))
                p.drawLine(rect.topLeft(), rect.bottomRight())

        row_y = y + bar_h + 4 * scale
        p.setFont(QFont("Poppins", max(7, int(10 * scale)), QFont.Weight.Bold))
        own_score = blue_score if own_blue else red_score
        enemy_score = red_score if own_blue else blue_score
        # T4：比分逐帧喂目标（目标未变时 no-op），显示值走数字滚动。
        if own_score is not None:
            self.own_score_scroll.update_target(own_score, now)
        else:
            self.own_score_scroll.reset()
        if enemy_score is not None:
            self.enemy_score_scroll.update_target(enemy_score, now)
        else:
            self.enemy_score_scroll.reset()
        p.setPen(own_color)
        p.drawText(QRectF(cx - gap - bar_w, row_y, bar_w, 18 * scale), Qt.AlignmentFlag.AlignRight, str(self.own_score_scroll.value(now)) if own_score is not None else "—")
        p.setPen(enemy_color)
        p.drawText(QRectF(cx + gap, row_y, bar_w, 18 * scale), Qt.AlignmentFlag.AlignLeft, str(self.enemy_score_scroll.value(now)) if enemy_score is not None else "—")
        p.setPen(QColor("#FFFFFF"))
        small = QFont("Poppins", max(5, int(7 * scale)))
        p.setFont(small)
        p.setOpacity(0.85)
        # BASE RACE 标签放在比分行中央，不再占用条上方空间（会撞灵动岛）
        p.drawText(QRectF(cx - gap, row_y, gap * 2, 18 * scale), Qt.AlignmentFlag.AlignCenter, "BASE RACE")
        p.setOpacity(1.0)


class KillFxRenderer:
    """按 GD656 原始 renderer 职责和时序实现的击杀 HUD 调度器。"""

    def __init__(self, asset_root: Path) -> None:
        self.assets = AssetBank(asset_root)
        self.texture_root = self.assets.texture_root
        self.sound_root = self.assets.sound_root
        self.sounds = SoundPool(self.assets.sound_root)
        self.cs2 = Cs2KillFeedback(local_cs2_assets())
        self.scrolling = ScrollingRenderer(self.assets)
        self.combo = ComboRenderer(self.assets)
        self.card = CardRenderer(self.assets)
        self.card_bar = CardBarRenderer(self.assets)
        self.battlefield = BattlefieldRenderer(self.assets)
        self.valorant = ValorantRenderer(self.assets)
        self.honor = HonorRenderer(self.assets)
        self.hit_info = HitInfoRenderer(self.assets)
        self.hit_info.attach_sounds(self.sounds)
        self.match_progress = MatchProgressRenderer()
        self.companion = CompanionHud()
        self.locked_targets = LockedTargetTracker()
        self.combo_count = 0
        self.last_kill_at = 0.0
        self.last_context: TriggerContext | None = None
        self._layout_anchor: QPointF | None = None
        self._last_hit_revision = -1

    def clear(self) -> None:
        """清空全部运行态。

        T6 修复：原先用 ``self.__init__(root)`` 重置——会连带重建 AssetBank
        （丢纹理缓存）与 SoundPool（丢预加载音效、可能产生音频设备抖动），
        且绕过字段声明属脆弱写法。改为逐字段显式重置；AssetBank/SoundPool
        与其缓存全部保留。
        """
        self.scrolling.icons.clear()
        self.cs2.clear()
        self.scrolling.pending.clear()
        self.scrolling.last_display = -1.0
        self.combo.context = None
        self.combo.started = -1.0
        self.card.cards.clear()
        self.card_bar.context = None
        self.card_bar.started = -1.0
        self.card_bar.waves.clear()
        self.battlefield.queue.clear()
        self.battlefield.context = None
        self.battlefield.started = -1.0
        self.valorant.context = None
        self.valorant.started = -1.0
        self.valorant.particles.clear()
        self.honor.active.clear()
        self.hit_info.lines.clear()
        self.hit_info.last_hit_at = -1.0
        self.match_progress = MatchProgressRenderer()
        self.companion.lines.clear()
        self.companion.score_scroll.reset()
        # LOCKED_TARGET 交战链一并断开（换局/清屏语义）。
        self.locked_targets = LockedTargetTracker()
        self.combo_count = 0
        self.last_kill_at = 0.0
        self.last_context = None
        self._layout_anchor = None
        self._last_hit_revision = -1

    # ClickGUI 二级界面逐元素开关；缺省全开，保持预设原有行为。
    ELEMENT_TOGGLES = {
        "kill_feed": "Kill Feed",
        "score": "Score Popup",
        "bonus": "Bonus Tags",
        "combo_subtitle": "Combo Subtitle",
        "scrolling": "Scrolling Icons",
        "scrolling_bf5": "Scrolling Icons",
        "scrolling_bf6": "Scrolling Icons",
        "combo": "Combo Icons",
        "card": "Card Icons",
        "card_bar": "Card Bars",
        "battlefield1": "Battlefield 1 Icons",
        "valorant": "Valorant Icons",
        "honor": "Honor Badges",
        "hit_info": "Hit Info",
        "match_progress": "Match Progress",
    }

    @classmethod
    def _filter_elements(cls, elements: tuple[str, ...], toggles: dict[str, bool] | None) -> tuple[str, ...]:
        if not toggles:
            return elements
        return tuple(item for item in elements if toggles.get(item, True))

    # 叠加式元素：开关打开时对任何预设生效（不依赖预设是否包含），
    # 开关关闭时即使预设包含也不出现。滚动/卡牌等元素仍按预设走，
    # 开关只做移除。
    ADDITIVE_ELEMENTS = ("honor", "hit_info", "match_progress")

    @classmethod
    def effective_elements(cls, preset: str, visibility: str, toggles: dict[str, bool] | None) -> tuple[str, ...]:
        elements = cls.elements_for_preset(preset, visibility)
        if not toggles:
            return elements
        result = [item for item in elements if toggles.get(item, True)]
        for key in cls.ADDITIVE_ELEMENTS:
            # Icons Only 只保留图标类元素，字幕/进度条类不叠加
            if visibility == "Icons Only" and key != "honor":
                continue
            if toggles.get(key, True) and key not in result:
                result.append(key)
        return tuple(result)

    @staticmethod
    def elements_for_preset(preset: str, visibility: str) -> tuple[str, ...]:
        if preset.startswith("Valorant "):
            elements = ("valorant",)
        else:
            elements = PRESET_ELEMENTS.get(preset, PRESET_ELEMENTS["Default Scrolling"])
        if visibility == "Icons Only":
            return tuple(item for item in elements if item in ("cs2", "scrolling", "scrolling_bf5", "scrolling_bf6", "combo", "card", "card_bar", "battlefield1", "valorant", "honor"))
        if visibility == "Full HUD":
            companions = ("kill_feed", "combo_subtitle", "score", "bonus")
            return tuple(dict.fromkeys((*elements, *companions)))
        return elements

    def trigger(self, event: CombatEvent, *, combo_window: float, sound_enabled: bool, volume: float, preset: str, visibility: str, now_override: float | None = None, element_toggles: dict[str, bool] | None = None) -> None:
        now = time.perf_counter() if now_override is None else now_override
        # LOCKED_TARGET 交战观测：所有带 victim_id 的事件都喂给追踪器，
        # 击杀瞬间再判定是否构成“同一目标持续交战 ≥ 窗口”。
        stable_sighting = self.locked_targets.observe_target(event.victim_id, now)
        if event.kind not in ("elimination", "death"):
            return
        if event.kind == "death":
            self.combo_count = 0
            self.cs2.clear()
            self.locked_targets.reset_engagement()
            return
        self.combo_count = self.combo_count + 1 if now - self.last_kill_at <= combo_window else 1
        self.last_kill_at = now
        combo = event.combo if event.combo > 0 else self.combo_count
        tags = event.tags
        lock_bonus_on = True if element_toggles is None else element_toggles.get("locked_target_bonus", True)
        if (
            event.kind == "elimination"
            and lock_bonus_on
            and self.locked_targets.is_locked_kill(event.victim_id, stable_sighting, now)
            and not any(str(tag).lower() == LOCKED_TARGET_TAG for tag in tags)
        ):
            tags = (*tags, LOCKED_TARGET_TAG)
        context = TriggerContext(
            event.kill_type if event.kill_type in KILL_TYPES else "NORMAL", combo,
            event.victim_id, event.victim_name, event.distance_m, event.score_delta, tags, now,
        )
        self.last_context = context
        elements = self.effective_elements(preset, visibility, element_toggles)
        if "cs2" in elements:
            self.cs2.trigger(context, now)
        if "honor" in elements:
            self.honor.trigger(context, now)
        if "scrolling" in elements or "scrolling_bf5" in elements or "scrolling_bf6" in elements:
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
        if preset == "CS2 Native" and self.cs2.assets.play("kill", volume / 100.0):
            return
        self.sounds.play(self._sound_name(context.kill_type, context.combo, preset), volume)
        if any(str(tag).lower() == LOCKED_TARGET_TAG for tag in context.tags):
            # The quiet score bonus is an extra layer, not a replacement for
            # the selected kill cue. Repeated victims can get this tag from
            # the second kill onward; replacing the cue makes them seem silent.
            self.sounds.play("addscore_df.ogg", volume)

    def _sound_name(self, kill_type: str, combo: int, preset: str) -> str:
        key = TYPE_TEXTURE.get(kill_type, "default")
        if preset == "Combo":
            name = f"combokillsound_{max(1, min(6, combo))}_cf.ogg"
        elif preset == "Card":
            card_key = key if key in {"default", "headshot", "explosion", "crit"} else "default"
            name = f"cardkillsound_{'armorheadshot' if card_key == 'headshot' else card_key}_cs.ogg"
        elif preset == "Battlefield 1":
            name = "headshotkillsound_bf1.ogg" if key == "headshot" else "killsound_bf1.ogg"
        elif preset == "Battlefield 5":
            name = "vehiclekillsound_bf5.ogg" if key == "destroyvehicle" else "headshotkillsound_bf5.ogg" if key == "headshot" else "killsound_bf5.ogg"
        elif preset == "Battlefield 6":
            if key == "headshot":
                name = "headshotkillsound_bf6.ogg"
            elif key == "assist":
                name = "assistsound_bf6.ogg"
            else:
                name = "killsound_bf6.ogg"
        elif preset.startswith("Valorant "):
            style = VALORANT_STYLES.get(preset.removeprefix("Valorant "), VALORANT_STYLES["Prime"])
            name = "valorant_headshot.ogg" if key == "headshot" else f"{style.sound_prefix}{max(1, min(5, combo))}.ogg"
        else:
            name = {"headshot": "headshotkillsound_df.ogg", "explosion": "explosionkillsound_df.ogg", "crit": "critkillsound_df.ogg"}.get(key, "killsound_df.ogg")
        return name

    def preload_preset_sounds(self, preset: str) -> None:
        self.cs2.assets.preload_audio()
        names = {
            self._sound_name(kill_type, combo, preset)
            for kill_type in KILL_TYPES
            for combo in range(1, 7)
        }
        names.update(("addscore_df.ogg", "hitsound_bf6.ogg"))
        self.sounds.preload(names)

    def notify_hit(self, damage: int, kind: str, *, preset: str, visibility: str, sound_enabled: bool, volume: float, element_toggles: dict[str, bool] | None = None, now_override: float | None = None) -> None:
        """RobotInjuryStat 命中回调：hit_info 弹字 + 命中音效。"""
        now = time.perf_counter() if now_override is None else now_override
        # 命中脉冲先于元素开关喂给 LOCKED_TARGET 追踪器（持续交战链证据）。
        self.locked_targets.observe_hit(now)
        elements = self.effective_elements(preset, visibility, element_toggles)
        if "hit_info" not in elements:
            return
        self.hit_info.trigger(damage, kind, now, volume=volume, enabled=sound_enabled)

    def draw(self, painter: QPainter, width: int, height: int, *, preset: str, visibility: str, team: str, scale: float, anchor_policy: str, x_offset: float, y_offset: float, responsive_scale: bool = True, device_pixel_ratio: float = 1.0, now_override: float | None = None, element_toggles: dict[str, bool] | None = None, state: Any = None, island_anchor_bottom: float | None = None) -> None:
        now = time.perf_counter() if now_override is None else now_override
        elements = self.effective_elements(preset, visibility, element_toggles)
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
        if "cs2" in elements:
            self.cs2.draw(painter, bottom, now, layout.scale)
        if "scrolling" in elements:
            self.scrolling.draw(painter, bottom, now, layout.scale, "default")
        if "scrolling_bf5" in elements:
            self.scrolling.draw(painter, bottom, now, layout.scale, "bf5")
        if "scrolling_bf6" in elements:
            self.scrolling.draw(painter, bottom, now, layout.scale, "bf6")
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
        if "honor" in elements:
            self.honor.draw(painter, layout.primary(210.0), now, layout.scale)
        # T6 一致性：命中弹字在 Combat Focus 锚点策略下随特效锚点系绘制，
        # 其余策略保持原屏幕中心行为（Original Bottom/Manual 无锚点语义）。
        companion_anchor = layout.anchor if anchor_policy == "Combat Focus" else None
        if "hit_info" in elements:
            self.hit_info.draw(painter, QPointF(width * 0.5, height * 0.5), now, layout.scale, anchor=companion_anchor)
        if "match_progress" in elements and state is not None:
            own_blue = int(getattr(state, "robot_id", 3)) >= 100 or str(getattr(state, "alliance_color", "")).lower() == "blue"
            self.match_progress.draw(
                painter, width, now, layout.scale,
                own_base_health=float(getattr(state, "base_health", 0)),
                own_base_max=float(getattr(state, "base_max_health", 0)),
                enemy_base_health=float(getattr(state, "enemy_base_health", 0)),
                enemy_base_max=float(getattr(state, "enemy_base_max_health", 0)),
                red_score=int(getattr(state, "red_score", 0)),
                blue_score=int(getattr(state, "blue_score", 0)),
                own_blue=own_blue,
                anchor_bottom=island_anchor_bottom,
            )
        self.companion.draw(painter, width, height, now, layout.scale, elements, offset, companion_anchor)
