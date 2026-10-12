"""Minecraft 1.21.11 combat-HUD preset built from the local game sprites.

The renderer is isolated from POLAR's liquid-glass paths.  It uses the exact
vanilla hotbar/status sprites with nearest-neighbour sampling and a cached
bitmap-font compositor, so selecting this preset cannot mutate shared glass
materials or lower the normal HUD's frame rate.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import math
import re
from pathlib import Path
import time
from typing import Any, Iterable

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF
from .minecraft_presentation import MinecraftItemAtlas, MinecraftStyledFont
from .jello_locale import chinese_enabled
from .hud_locale import hud_text, hud_template, hud_module_name, HudDisplayText


FUNCTION_SLOT_NAMES = tuple(f"Slot {index}" for index in range(1, 10))
# Unihex glyphs have 16 physical rows for an 8-logical-pixel character.
# Keep that detail through the intermediate HUD cache; a 1x cache irreversibly
# drops CJK strokes before the final nearest-neighbour screen composition.
HUD_LAYER_DPR = 2

DEFAULT_FUNCTION_SLOTS = (
    "ArmorAutoAim",
    "ArmorLock",
    "FrictionWheel",
    "SpinMode",
    "EconomyPanel",
    "ProjectileShop",
    "TacticalMap",
    "MatchChat",
    "ClickGui",
)


def vanilla_gui_scale(width: int, height: int, override: str = "Auto") -> int:
    """Return Minecraft's integer GUI scale (minimum 320x240 logical area)."""

    if str(override) != "Auto":
        try:
            return max(1, min(8, int(str(override).rstrip("xX"))))
        except ValueError:
            pass
    scale = 1
    while scale < 8 and width // (scale + 1) >= 320 and height // (scale + 1) >= 240:
        scale += 1
    return scale


@dataclass(frozen=True, slots=True)
class MinecraftHudLayout:
    scale: float
    hotbar: QRectF
    slots: tuple[QRectF, ...]
    hearts: tuple[QRectF, ...]
    armor: tuple[QRectF, ...]
    food: tuple[QRectF, ...]
    boss_bars: tuple[QRectF, ...]
    scoreboard: QRectF
    effects_origin: QPointF
    base_scale: int = 1
    multiplier: float = 1.0
    logical_size: tuple[int, int] = (1, 1)
    origin: tuple[float, float] = (0.0, 0.0)

    def slot_at(self, point: QPointF) -> int | None:
        for index, rect in enumerate(self.slots):
            if rect.contains(point):
                return index
        return None


class MinecraftBitmapFont:
    """Small cached renderer for Mojang's exact ``ascii.png`` glyph sheet."""

    def __init__(self, texture: Path, cache_limit: int = 320) -> None:
        self.texture_path = Path(texture)
        self.texture = QImage(str(self.texture_path)).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.cache_limit = max(32, int(cache_limit))
        self._cache: OrderedDict[tuple[str, int, int, bool], QImage] = OrderedDict()
        self._widths = self._scan_widths()

    @property
    def available(self) -> bool:
        return not self.texture.isNull()

    def _scan_widths(self) -> tuple[int, ...]:
        widths: list[int] = []
        if self.texture.isNull():
            return tuple(6 for _ in range(256))
        for code in range(256):
            sx = (code & 15) * 8
            sy = (code >> 4) * 8
            right = -1
            for x in range(8):
                if any(self.texture.pixelColor(sx + x, sy + y).alpha() > 8 for y in range(8)):
                    right = x
            widths.append(4 if code == 32 else max(2, min(8, right + 2)))
        return tuple(widths)

    @staticmethod
    def _code(char: str) -> int:
        value = ord(char)
        return value if 0 <= value <= 255 else ord("?")

    def logical_width(self, text: str) -> int:
        if not text:
            return 0
        return sum(self._widths[self._code(char)] for char in str(text))

    def _plain_layer(self, text: str, scale: int, packed_color: int) -> QImage:
        width = max(1, self.logical_width(text) * scale)
        layer = QImage(width, 8 * scale, QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(Qt.GlobalColor.transparent)
        painter = QPainter(layer)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        x = 0
        for char in str(text):
            code = self._code(char)
            source = QRect((code & 15) * 8, (code >> 4) * 8, 8, 8)
            painter.drawImage(QRect(x, 0, 8 * scale, 8 * scale), self.texture, source)
            x += self._widths[code] * scale
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(layer.rect(), QColor.fromRgba(packed_color))
        painter.end()
        return layer

    def image(self, text: str, color: QColor, scale: int, shadow: bool = True) -> QImage:
        scale = max(1, int(scale))
        key = (str(text), int(color.rgba()), scale, bool(shadow))
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        main = self._plain_layer(key[0], scale, key[1])
        if shadow:
            out = QImage(main.width() + scale, main.height() + scale, QImage.Format.Format_ARGB32_Premultiplied)
            out.fill(Qt.GlobalColor.transparent)
            painter = QPainter(out)
            shadow_color = QColor(max(0, color.red() // 4), max(0, color.green() // 4), max(0, color.blue() // 4), color.alpha())
            painter.drawImage(scale, scale, self._plain_layer(key[0], scale, int(shadow_color.rgba())))
            painter.drawImage(0, 0, main)
            painter.end()
        else:
            out = main
        self._cache[key] = out
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_limit:
            self._cache.popitem(last=False)
        return out

    def draw(self, painter: QPainter, x: float, y: float, text: str, color: QColor, scale: int, *, shadow: bool = True) -> float:
        image = self.image(str(text), color, scale, shadow)
        painter.drawImage(int(round(x)), int(round(y)), image)
        return float(image.width())


def _scoreboard_display(text: str, chinese: bool) -> str:
    # Formatting controls are not vocabulary and must remain byte-for-byte.
    parts = re.split(r"(§.)", text)
    translated = "".join(part if part.startswith("§") else hud_text(part, chinese) for part in parts)
    return translated.replace("§aA §f", "§a我 §f").replace("§cE §f", "§c敌 §f") if chinese else translated


def _draw_display_text(renderer, painter, x, y, raw, tint, scale):
    """Use the already bundled Unihex font for Chinese, within raw text bounds."""
    display = getattr(raw, "display", raw)
    if display == raw:
        return renderer.font.draw(painter, x, y, raw, tint, scale)
    rect = QRectF(x, y, renderer.font.logical_width(raw) * scale + scale, 9 * scale)
    renderer.styled_font.draw(painter, rect, display, scale, base=tint)
    return rect.width()


class MinecraftCombatHudRenderer:
    SPRITES = (
        "hotbar",
        "hotbar_selection",
        "heart_container",
        "heart_full",
        "heart_half",
        "armor_empty",
        "armor_half",
        "armor_full",
        "food_empty",
        "food_half",
        "food_full",
        "effect_background",
    )

    def __init__(self, asset_root: Path) -> None:
        self.asset_root = Path(asset_root)
        self.images = {
            name: QImage(str(self.asset_root / f"{name}.png")).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            for name in self.SPRITES
        }
        self.font = MinecraftBitmapFont(self.asset_root / "ascii.png")
        self.styled_font = MinecraftStyledFont(self.font,self.asset_root)
        self.items = MinecraftItemAtlas(self.asset_root)
        self.selected_slot = 0
        self._layer_image = QImage()
        self._layer_key: tuple[Any, ...] | None = None
        self._layer_priority_key: tuple[Any, ...] | None = None
        self._layer_next_refresh_at = 0.0
        self.layer_rebuilds = 0

    @property
    def available(self) -> bool:
        return self.font.available and all(not image.isNull() for image in self.images.values())

    def _logical_layout(
        self,
        width: int,
        height: int,
        scoreboard_lines: Iterable[str] = (),
    ) -> MinecraftHudLayout:
        scale = 1.0
        hotbar = QRectF((width - 182 * scale) // 2, height - 22 * scale, 182 * scale, 22 * scale)
        slots = tuple(
            QRectF(hotbar.left() + (3 + index * 20) * scale, hotbar.top() + 3 * scale, 16 * scale, 16 * scale)
            for index in range(9)
        )
        hearts_y = hotbar.top() - 11 * scale
        hearts = tuple(QRectF(hotbar.left() + index * 8 * scale, hearts_y, 9 * scale, 9 * scale) for index in range(10))
        armor_y = hearts_y - 10 * scale
        armor = tuple(QRectF(hotbar.left() + index * 8 * scale, armor_y, 9 * scale, 9 * scale) for index in range(10))
        food = tuple(
            QRectF(hotbar.right() - (index + 1) * 8 * scale - scale, hearts_y, 9 * scale, 9 * scale)
            for index in range(10)
        )
        # Retain the layout field for consumers, but reserve no boss-bar space.
        boss: tuple[QRectF, ...] = ()
        lines = tuple(str(line) for line in scoreboard_lines)
        logical_score_w = max((self.styled_font.width(line) for line in lines), default=104)
        score_w = max(112, logical_score_w + 8) * scale
        score_h = max(12, len(lines) * 9 + 5) * scale
        # Hypixel-style sidebars keep the upper-right clear for ArrayList/mod
        # overlays.  Anchor match information above the status/hotbar band,
        # rather than vertically centring a tall diagnostics dump.
        score_y = max(40 * scale, min((height-score_h)*.5,height-score_h-52*scale))
        scoreboard = QRectF(width - score_w - 4 * scale, score_y, score_w, score_h)
        # Preserve the existing effect-tile position independently of top HUD chrome.
        return MinecraftHudLayout(
            scale,
            hotbar,
            slots,
            hearts,
            armor,
            food,
            boss,
            scoreboard,
            QPointF(4 * scale, 58 * scale),
        )

    @staticmethod
    def _map_rect(rect: QRectF, scale: float, origin_x: float, origin_y: float) -> QRectF:
        return QRectF(
            origin_x + rect.left() * scale,
            origin_y + rect.top() * scale,
            rect.width() * scale,
            rect.height() * scale,
        )

    def layout(
        self,
        width: int,
        height: int,
        scale_override: str = "Auto",
        scoreboard_lines: Iterable[str] = (),
        scale_multiplier: float = 1.0,
    ) -> MinecraftHudLayout:
        """Return the exact screen transform used by draw and hit testing."""

        width = max(1, int(width))
        height = max(1, int(height))
        lines = tuple(str(line) for line in scoreboard_lines)
        base_scale = vanilla_gui_scale(width, height, scale_override)
        multiplier = max(0.50, min(2.00, float(scale_multiplier)))
        requested = max(0.25, base_scale * multiplier)

        logical_score_width = max((self.styled_font.width(line) for line in lines), default=112)
        score_width = max(112, logical_score_width + 8)
        score_height = max(12, len(lines) * 9 + 5)
        # Content-aware safety cap, not Minecraft's conservative 320x240 cap:
        # user scaling remains useful while the actual hotbar/effects/score
        # extents are guaranteed to stay inside the framebuffer.
        widest = max(182.0, 154.0, float(score_width + 8))
        tallest = max(82.0, float(score_height + 92))
        safe_scale = min(
            max(0.05, (width - 8.0) / widest),
            max(0.05, (height - 8.0) / tallest),
        )
        # The user-facing multiplier never goes below 0.50, but an unusually
        # small host window may require a lower emergency fit scale.  Hit
        # testing still uses this exact transform, so safety never creates a
        # mismatched interaction region.
        effective = max(0.05, min(requested, safe_scale))
        logical_width = max(1, int(math.ceil(width / effective)))
        logical_height = max(1, int(math.ceil(height / effective)))
        origin_x = (width - logical_width * effective) * 0.5
        origin_y = (height - logical_height * effective) * 0.5
        logical = self._logical_layout(logical_width, logical_height, lines)
        map_rect = lambda value: self._map_rect(value, effective, origin_x, origin_y)
        return MinecraftHudLayout(
            effective,
            map_rect(logical.hotbar),
            tuple(map_rect(rect) for rect in logical.slots),
            tuple(map_rect(rect) for rect in logical.hearts),
            tuple(map_rect(rect) for rect in logical.armor),
            tuple(map_rect(rect) for rect in logical.food),
            tuple(map_rect(rect) for rect in logical.boss_bars),
            map_rect(logical.scoreboard),
            QPointF(
                origin_x + logical.effects_origin.x() * effective,
                origin_y + logical.effects_origin.y() * effective,
            ),
            base_scale,
            multiplier,
            (logical_width, logical_height),
            (origin_x, origin_y),
        )

    @staticmethod
    def assignments(module: Any) -> tuple[str, ...]:
        result: list[str] = []
        for index, fallback in enumerate(DEFAULT_FUNCTION_SLOTS, 1):
            setting = module.get_setting(f"Slot {index}") if module is not None else None
            result.append(str(setting.value) if setting is not None else fallback)
        return tuple(result)

    @staticmethod
    def abbreviation(name: str) -> str:
        if not name or name == "Empty":
            return "--"
        capitals = "".join(char for char in name if char.isupper() or char.isdigit())
        return (capitals or name[:3]).upper()[:3]

    @staticmethod
    def _team_rows(state: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return ally/enemy status rows in local-team order."""

        local_id = int(getattr(state, "robot_id", 0) or 0)
        local_red = local_id < 100
        rows = [dict(row) for row in getattr(state, "robot_rows", ()) if int(row.get("id", 0) or 0) > 0]
        # Official sync provides both teams in robot_rows.  The simulator's
        # legacy robot_rows contains allies only, so supplement its opponent
        # side from the tactical-map feed without duplicating robot IDs.
        by_id = {int(row.get("id", 0)): row for row in rows}
        for row in getattr(state, "map_robots", ()):
            robot_id = int(row.get("id", 0) or 0)
            if robot_id > 0 and robot_id not in by_id:
                copy = dict(row)
                copy.setdefault("state", "ALIVE" if int(copy.get("hp", 0)) > 0 else "DEAD")
                by_id[robot_id] = copy
        allies = [
            row for robot_id, row in by_id.items()
            if (robot_id < 100) == local_red
        ]
        enemies = [
            row for robot_id, row in by_id.items()
            if (robot_id < 100) != local_red
        ]
        allies.sort(key=lambda row: (int(row.get("id", 0)) != local_id, int(row.get("id", 0))))
        enemies.sort(key=lambda row: (int(row.get("hp", 0)) <= 0, int(row.get("id", 0))))
        return allies, enemies

    @classmethod
    def _robot_status_line(cls, prefix: str, row: dict[str, Any]) -> str:
        robot_id = int(row.get("id", 0) or 0)
        hp = max(0, int(row.get("hp", 0) or 0))
        # Tactical-map fallback rows may omit max HP.  Never display nonsense
        # such as 580/1; the current sample is the safest bounded fallback.
        maximum = max(1, hp, int(row.get("max", 1) or 1))
        state = str(row.get("state", "ALIVE" if hp > 0 else "DEAD")).upper()
        name = cls.abbreviation(str(row.get("name", "ROBOT")))
        return f"{prefix}{robot_id:03d} {name} {hp}/{maximum} {state[:5]}"

    @staticmethod
    def _compact_robot_status(prefix: str, row: dict[str, Any], local_id: int) -> str:
        """Return one glanceable sidebar token, never a telemetry sentence."""

        robot_id = int(row.get("id", 0) or 0)
        hp = max(0, int(row.get("hp", 0) or 0))
        state = str(row.get("state", "ALIVE" if hp > 0 else "DEAD")).upper()
        if hp <= 0 or state in {"DOWN", "DEAD", "OFFLINE"}:
            value = "DOWN"
        else:
            value = str(hp)
        marker = ">" if robot_id == local_id else ""
        # RM robot IDs encode team in the hundreds digit.  The team column is
        # already explicit, so the two trailing digits scan much faster.
        return f"{marker}{prefix}{robot_id % 100:02d} {value}"

    @staticmethod
    def scoreboard_lines(state: Any) -> tuple[tuple[str, QColor], ...]:
        if getattr(state, "mujoco_operator", False):
            valid = getattr(state, "mujoco_telemetry_valid", False)
            if not valid:
                texts = ("§e§lSIM OPERATOR", "§cDISCONNECTED / STALE", "§fHP: §7?", "§fAmmo: §7?", "§fHeat: §7?", "§fTime: §7?", "§7Virtual input disabled")
            else:
                seconds = max(0, int(state.stage_countdown_sec))
                texts = ("§e§lSIM OPERATOR", "§7" + str(state.current_stage),
                    f"§fTime left: §a{seconds//60}:{seconds%60:02d}", "",
                    f"§fRobot: §a{state.robot_id}",
                    f"§fHP: §a{int(state.current_health)}/{int(state.max_health)}",
                    f"§fAmmo: §a{int(state.remaining_ammo)}",
                    f"§fHeat: §a{int(state.current_heat)}/{int(state.max_heat)}", "",
                    f"§fAlly VP: §a{state.ally_victory_points}",
                    f"§fEnemy VP: §c{state.enemy_victory_points}",
                    "§7Map: simulator truth", "§7Physical TX: disabled")
            return tuple((text, QColor("#ffffff")) for text in texts)
        seconds = max(0, int(getattr(state, "stage_countdown_sec", 0)))
        allies, enemies = MinecraftCombatHudRenderer._team_rows(state)
        ally_alive = sum(1 for row in allies if int(row.get("hp", 0)) > 0 and str(row.get("state", "")).upper() not in {"DOWN", "DEAD"})
        enemy_alive = sum(1 for row in enemies if int(row.get("hp", 0)) > 0 and str(row.get("state", "")).upper() not in {"DOWN", "DEAD"})
        stage=str(getattr(state,"current_stage","NOT_STARTED"))
        label={"NOT_STARTED":"WAITING","MATCH":"MATCH","SETTLEMENT":"RESULTS","COUNTDOWN":"COUNTDOWN"}.get(stage,stage)
        # Bed Wars sidebar hierarchy: title / metadata / timer / teams / stats.
        # Empty score lines create genuine spacing. No fake Hypixel address,
        # objective, ping, red decorative scores or full roster dump.
        texts=[
            "§e§l"+str(getattr(state,"match_title","ROBOMASTER")).upper(),
            "§7"+datetime.now().strftime("%m/%d/%y")+" §8"+label,
            "",
            f"§fTime left: §a{seconds//60}:{seconds%60:02d}",
            "",
            f"§aA §fAlly: §a{ally_alive} §7YOU",
            f"§cE §fEnemy: §c{enemy_alive}",
            f"§fBase: §a{int(getattr(state,'base_health',0))} §7/ §c{int(getattr(state,'enemy_base_health',0))}",
            f"§fShield: §a{int(getattr(state,'base_shield',0))}% §7/ §c{int(getattr(state,'enemy_base_shield',0))}%",
            f"§fPost: §a{int(getattr(state,'outpost_health',0))} §7/ §c{int(getattr(state,'enemy_outpost_health',0))}",
            "",
            f"§fKills: §a{int(getattr(state,'referee_kill_count',0))}",
            f"§fAmmo: §a{int(getattr(state,'remaining_ammo',0))}",
            f"§fGold: §6{int(getattr(state,'remaining_economy',0))}",
            "§epolar-rm-client",
        ]
        return tuple((text,QColor("#ffffff")) for text in texts)

    def draw(self, painter: QPainter, width: int, height: int, state: Any, registry: Any, module: Any) -> MinecraftHudLayout:
        lines = self.scoreboard_lines(state)
        override_setting = module.get_setting("GUI Scale") if module is not None else None
        override = str(override_setting.value) if override_setting is not None else "Auto"
        multiplier_setting = module.get_setting("Scale Multiplier") if module is not None else None
        multiplier = float(multiplier_setting.value) if multiplier_setting is not None else 1.0
        screen_layout = self.layout(
            width,
            height,
            override,
            (text for text, _ in lines),
            multiplier,
        )
        logical_width, logical_height = screen_layout.logical_size
        layout = self._logical_layout(logical_width, logical_height, (text for text, _ in lines))
        scale = layout.scale
        chinese = chinese_enabled(registry)
        display_lines = tuple(
            (text if index in (0, len(lines) - 1) else _scoreboard_display(text, chinese), tint)
            for index, (text, tint) in enumerate(lines)
        )
        assignments = self.assignments(module)
        def assignment_state(name: str) -> tuple[str, bool]:
            target = registry.get(name) if registry is not None and name != "Empty" else None
            return name, bool(target and target.enabled)

        assignment_states = tuple(assignment_state(name) for name in assignments)
        hp_ratio = self._ratio(getattr(state, "current_health", 0), getattr(state, "max_health", 1))
        thermal_margin = 1.0 - self._ratio(getattr(state, "current_heat", 0.0), getattr(state, "max_heat", 1.0))
        chassis_ratio = self._ratio(
            getattr(state, "chassis_power_w", 0),
            getattr(state, "power_limit", 1),
        )
        label_scale = max(1, scale // 2)
        thermal_text = f"COOL {int(round(thermal_margin * 100.0))}%  -{int(getattr(state, 'heat_cooldown_rate', 0))}/S"
        power_text = f"PWR {int(getattr(state, 'chassis_power_w', 0))}/{int(getattr(state, 'power_limit', 0))}W"
        if getattr(state, "mujoco_operator", False):
            power_text = "PWR ?"
            chassis_ratio = 0.0
            thermal_text = f"HEAT ROOM {int(round(thermal_margin * 100.0))}%" if getattr(state, "mujoco_telemetry_valid", False) else "HEAT ROOM ?"
        thermal_display = HudDisplayText(thermal_text, hud_text(thermal_text, chinese))
        power_display = HudDisplayText(power_text, hud_text(power_text, chinese))
        # Buff 指示：优先裁判 Buff 解码（buff_indicator_state），
        # LabDebug mock 行无 buff_type 时回退旧演示布尔。
        buff_fn = getattr(state, "buff_indicator_state", None)
        try:
            atk_buf, def_buf = (bool(v) for v in buff_fn()[:2]) if callable(buff_fn) else (False, False)
        except Exception:
            atk_buf = bool(getattr(state, "small_buff", False))
            def_buf = bool(getattr(state, "large_buff", False))
        effects = (
            ("JUP", bool(getattr(state, "jump_ready", False))),
            ("PTE", bool(getattr(state, "pte_ready", False))),
            ("WHE", bool(getattr(state, "wheel_online", False))),
            ("LES", bool(getattr(state, "les_online", False))),
            ("SBF", atk_buf),
            ("LBF", def_buf),
        )
        tactical_map = registry.get("TacticalMap") if registry is not None else None
        tactical_map_enabled = bool(tactical_map and tactical_map.enabled)
        layer_key: tuple[Any, ...] = (
            logical_width,
            logical_height,
            chinese,
            tuple((text, int(text_color.rgba())) for text, text_color in lines),
            int(self.selected_slot),
            assignment_states,
            int(round(hp_ratio * 20.0)),
            int(round(thermal_margin * 20.0)),
            int(round(chassis_ratio * 20.0)),
            thermal_text,
            power_text,
            chassis_ratio >= 0.85,
            effects,
            tactical_map_enabled,
        )
        # Minecraft normally updates gameplay state at its tick cadence while
        # the display can present much faster.  Keep slot/config changes
        # immediate, but do not repaint and re-upload the complete pixel layer
        # 60-144 times per second for sub-pixel telemetry changes.  The cached
        # image remains composited every display frame with nearest sampling.
        priority_key: tuple[Any, ...] = (
            chinese,
            logical_width,
            logical_height,
            int(self.selected_slot),
            assignment_states,
            tactical_map_enabled,
        )
        now = time.perf_counter()
        layer_size = QSize(logical_width * HUD_LAYER_DPR, logical_height * HUD_LAYER_DPR)
        image_size_changed = (self._layer_image.size() != layer_size
                              or self._layer_image.devicePixelRatio() != HUD_LAYER_DPR)
        priority_changed = priority_key != self._layer_priority_key
        telemetry_due = now >= self._layer_next_refresh_at

        if image_size_changed or priority_changed or (layer_key != self._layer_key and telemetry_due):
            if image_size_changed:
                self._layer_image = QImage(
                    layer_size,
                    QImage.Format.Format_ARGB32_Premultiplied,
                )
                self._layer_image.setDevicePixelRatio(HUD_LAYER_DPR)
            self._layer_image.fill(Qt.GlobalColor.transparent)
            layer_painter = QPainter(self._layer_image)
            layer_painter.save()
            layer_painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            layer_painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, False)
            layer_painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

            self._sprite(layer_painter, "hotbar", layout.hotbar)
            selection = QRectF(
                layout.hotbar.left() + (-1 + self.selected_slot * 20) * scale,
                layout.hotbar.top() - scale,
                24 * scale,
                23 * scale,
            )
            self._sprite(layer_painter, "hotbar_selection", selection)
            for index, (rect, (name, enabled)) in enumerate(zip(layout.slots, assignment_states)):
                if name!="Empty":
                    target=registry.get(name) if registry else None
                    layer_painter.drawImage(rect,self.items.image(name,target.category if target else "System"))
                    if enabled:layer_painter.fillRect(QRectF(rect.left()+2,rect.bottom()-1,12,1),QColor("#55ff55"))
                self.font.draw(layer_painter, rect.left(), rect.top(), str(index + 1), QColor("#ffff55") if index == self.selected_slot else QColor("#aaaaaa"), max(1, scale // 2), shadow=True)

            self._draw_meter(layer_painter, layout.hearts, hp_ratio, "heart_container", "heart_half", "heart_full")
            # Keep the vanilla pixel grammar without pretending heat is armour or
            # chassis power is food.  Coolant cells and electric pips preserve the
            # familiar two-row read while giving RM telemetry its own semantics.
            self._draw_coolant_cells(layer_painter, layout.armor, thermal_margin)
            self._draw_power_pips(layer_painter, layout.food, chassis_ratio)
            _draw_display_text(self, layer_painter, layout.armor[0].left(), layout.armor[0].top() - 7 * label_scale, thermal_display, QColor("#55ffff"), label_scale)
            power_w = self.font.logical_width(power_text) * label_scale
            _draw_display_text(self, layer_painter, layout.hotbar.right() - power_w, layout.food[-1].top() - 7 * label_scale, power_display, QColor("#55aaff") if chassis_ratio < 0.85 else QColor("#ffff55"), label_scale)

            self._draw_scoreboard(layer_painter, layout.scoreboard, display_lines, scale)
            if not tactical_map_enabled:
                self._draw_effects(layer_painter, layout.effects_origin, tuple((HudDisplayText(label, hud_text(label, chinese)), active) for label, active in effects), scale)
            layer_painter.restore()
            layer_painter.end()
            self._layer_key = layer_key
            self._layer_priority_key = priority_key
            self._layer_next_refresh_at = now + (1.0 / 30.0)
            self.layer_rebuilds += 1

        destination_painter = painter
        destination_painter.save()
        destination_painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        destination_painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, False)
        destination_painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        origin_x, origin_y = screen_layout.origin
        destination_painter.drawImage(
            QRectF(
                origin_x,
                origin_y,
                logical_width * screen_layout.scale,
                logical_height * screen_layout.scale,
            ),
            self._layer_image,
            QRectF(self._layer_image.rect()),
        )
        destination_painter.restore()
        return screen_layout

    @staticmethod
    def _ratio(value: float, maximum: float) -> float:
        return max(0.0, min(1.0, float(value) / max(1.0, float(maximum))))

    def _sprite(self, painter: QPainter, name: str, destination: QRectF, source: QRect | None = None) -> None:
        image = self.images.get(name)
        if image is None or image.isNull() or destination.isEmpty():
            return
        painter.drawImage(destination.toRect(), image, source or image.rect())

    def _draw_meter(self, painter: QPainter, rects: tuple[QRectF, ...], ratio: float, empty: str, half: str, full: str) -> None:
        units = max(0, min(20, int(round(ratio * 20.0))))
        for index, rect in enumerate(rects):
            self._sprite(painter, empty, rect)
            fill = units - index * 2
            if fill >= 2:
                self._sprite(painter, full, rect)
            elif fill == 1:
                self._sprite(painter, half, rect)

    @staticmethod
    def _draw_coolant_cells(painter: QPainter, rects: tuple[QRectF, ...], ratio: float) -> None:
        units = max(0, min(20, int(round(ratio * 20.0))))
        for index, rect in enumerate(rects):
            body = QRectF(rect.left() + 1.0, rect.top() + 2.0, 7.0, 6.0)
            painter.fillRect(body, QColor("#17242d"))
            painter.fillRect(QRectF(body.left() + 1.0, body.top() + 1.0, 5.0, 4.0), QColor("#30434f"))
            fill = units - index * 2
            if fill <= 0:
                continue
            danger = ratio < 0.30
            active = QColor("#ff5555") if danger else QColor("#55ffff")
            width = 5.0 if fill >= 2 else 3.0
            painter.fillRect(QRectF(body.left() + 1.0, body.top() + 1.0, width, 4.0), active)
            painter.fillRect(QRectF(body.left() + 2.0, body.top(), max(1.0, width - 2.0), 1.0), active.lighter(125))

    @staticmethod
    def _draw_power_pips(painter: QPainter, rects: tuple[QRectF, ...], ratio: float) -> None:
        units = max(0, min(20, int(round(ratio * 20.0))))
        for index, rect in enumerate(rects):
            # The food rect tuple runs right-to-left.  A tiny stepped bolt is
            # legible at 1x and remains perfectly crisp after nearest scaling.
            cx = rect.center().x()
            y = rect.top() + 1.0
            outline = QPolygonF(
                [
                    QPointF(cx + 1.0, y),
                    QPointF(cx - 2.0, y + 4.0),
                    QPointF(cx, y + 4.0),
                    QPointF(cx - 1.0, y + 8.0),
                    QPointF(cx + 3.0, y + 3.0),
                    QPointF(cx + 1.0, y + 3.0),
                ]
            )
            painter.setBrush(QColor("#273548"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(outline)
            fill = units - index * 2
            if fill <= 0:
                continue
            active = QColor("#ffff55") if ratio >= 0.85 else QColor("#55aaff")
            painter.setBrush(active if fill >= 2 else QColor(active.red(), active.green(), active.blue(), 150))
            painter.drawPolygon(outline)

    def _draw_scoreboard(self, painter: QPainter, rect: QRectF, lines: tuple[tuple[str, QColor], ...], scale: int) -> None:
        row_h = 9 * scale
        y = rect.top() + 2 * scale
        for index, (text, text_color) in enumerate(lines):
            row = QRectF(rect.left(), y + index * row_h, rect.width(), row_h)
            painter.fillRect(row, QColor(0, 0, 0, 105 if index else 150))
            self.styled_font.draw(painter,row.adjusted(3*scale,0,-3*scale,0),text,scale,
                                  Qt.AlignHCenter if index==0 else Qt.AlignLeft)

    def _draw_effects(self, painter: QPainter, origin: QPointF, effects: tuple[tuple[str, bool], ...], scale: int) -> None:
        for index, (label, active) in enumerate(effects):
            rect = QRectF(origin.x() + index * 26 * scale, origin.y(), 24 * scale, 24 * scale)
            self._sprite(painter, "effect_background", rect)
            label_w = self.font.logical_width(label) * scale
            _draw_display_text(self, painter, rect.center().x() - label_w * 0.5, rect.top() + 8 * scale, label, QColor("#55ff55") if active else QColor("#ff5555"), scale)

    def append_drop_overlay(
        self,
        commands: Any,
        width: int,
        height: int,
        dragged_name: str,
        point: QPointF,
        assignments: tuple[str, ...],
        font: Any,
        scale_override: str = "Auto",
        scale_multiplier: float = 1.0,
        *,
        registry: Any = None,
    ) -> MinecraftHudLayout:
        """Append an exact hotbar drop target to either GPU/compat draw list."""

        layout = self.layout(
            width,
            height,
            scale_override,
            scale_multiplier=scale_multiplier,
        )
        scale = layout.scale
        commands.image(
            layout.hotbar,
            self.asset_root / "hotbar.png",
            QColor(255, 255, 255, 255),
            nearest=True,
        )
        hovered = layout.slot_at(point)
        if hovered is not None:
            selection = QRectF(layout.hotbar.left() + (-1 + hovered * 20) * scale, layout.hotbar.top() - scale, 24 * scale, 23 * scale)
            commands.image(
                selection,
                self.asset_root / "hotbar_selection.png",
                QColor(255, 255, 255, 255),
                nearest=True,
            )
        for index, (slot, name) in enumerate(zip(layout.slots, assignments)):
            commands.text(slot, self.abbreviation(name), font, QColor("#ffffff"), Qt.AlignmentFlag.AlignCenter)
            commands.text(QRectF(slot.left(), slot.top(), 8 * scale, 8 * scale), str(index + 1), font, QColor("#ffff55"), Qt.AlignmentFlag.AlignCenter)
        ghost = QRectF(point.x() + 14.0, point.y() + 10.0, 190.0, 28.0)
        commands.rect(ghost, 3.0, QColor(0, 0, 0, 175), QColor(255, 255, 255, 90), 1.0)
        commands.text(ghost.adjusted(8.0, 0.0, -8.0, 0.0), hud_template("DROP {name}", chinese_enabled(registry), name=hud_module_name(dragged_name, chinese_enabled(registry))), font, QColor("#ffffff"), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return layout
