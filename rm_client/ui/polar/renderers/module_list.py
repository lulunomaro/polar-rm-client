"""阶段 2c 渲染器提取：ModuleList 模块列表族（D12）→ ModuleListMixin。

（REFACTOR_BLUEPRINT §5 阶段 2 表 2c 行 / §2 D12 节 / §5.6 冻结面 / §6 性能红线）

迁移范围（以 grep 重定位后的实际行号为准，蓝图原行号 10864–12087 已因多轮
修复漂移；实际区段为 polar_app.py 9156–10283 的连续块，仅被 _client_asset_path
打断一次）：共 29 个方法——
- 布局/入口：``module_list_layout`` / ``draw_minecraft_module_list`` /
  ``draw_module_list_hud`` / ``draw_module_list_fast_rows``
- 数据面：``module_list_preferences`` / ``module_list_tag``(staticmethod) /
  ``module_list_surface`` / ``module_list_entries``
- 字体/阴影工具：``debug_tahoma_font`` / ``debug_tahoma16_font`` /
  ``measure_debug_tahoma16`` / ``arraylist_shadow_image`` /
  ``draw_string_with_shadow_mc``
- 十种样式变体：``draw_module_list_debug_white`` / ``_liquid_array`` /
  ``_polar_text`` / ``_rainbow_right`` / ``_glow_rows`` / ``_segmented`` /
  ``_dynamic_island`` / ``_keybind_rows`` / ``_status_bars`` / ``_robot_cards``
- 周边 HUD：``draw_keybinds_strip`` / ``draw_keybinds_hud`` /
  ``draw_robot_list_hud`` / ``draw_status_cards_hud`` / ``draw_rm_indicators_hud``
- GPU 面：``build_gpu_module_list``

纪律与偏差决策（中文记录）：
- 方法体**零修改**（含注释与空行），纯移动进本 Mixin；
- ``_client_asset_path`` 位于原区段中间但属 D19 共享辅助（esp_wireframe 等
  多域经 self 调用），按边界保守原则**留在 polar_app.py**，本 Mixin 经 self
  继续访问（debug_tahoma_font / arraylist_shadow_image /
  draw_module_list_debug_white 内部调用面不变）；
- 排序缓存字段 ``_module_list_sort_cache_key`` / ``_module_list_sort_widths``
  是 PolarRMWindow.__init__ 状态 schema 成员 → **保持 __init__ 原位**，
  本 Mixin 只承载方法（§6 缓存纪律：key 生成逻辑一字未改随方法整体搬迁）；
- ``module_list_animation_in_flight``（D2 帧编排）、``queue_gpu_module_list`` /
  ``draw_module_list_for_tactical_map``（D10 战术地图入口）名字虽含 modlist
  但属其他域 → 不迁；
- jello_clickgui.py 内另有一套 jello 模块列表绘制属 D5 ClickGUI 域 → 不触碰；
- ``ModuleListLayout`` 已在阶段 1 迁至 ui/polar/primitives.py → 从 ..primitives
  导入使用，不重复定义；
- 方法体内 lazy 属性（``_jello_active_mods_*`` / ``_arraylist_shadow_baked`` /
  ``_debug_tahoma_font_id`` / ``_jello_hud_shadow`` 等）全部经 getattr/self
  访问，继承机制天然覆盖，构造接线不动。

冻结面纪律：与阶段 1/2a/2b 一致——api_freeze_test 对方法类成员比对
``__qualname__``（基线值形如 "PolarRMWindow.<name>"），故在本模块底部把全部
迁入方法的 ``__qualname__`` 钉回 ``PolarRMWindow.<name>``，使 ``window.<name>``
签名与迁移前 bit 级一致（含一个 @staticmethod：module_list_tag）。
"""

from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QTransform,
)

# 相对导入层级：rm_client/ui/polar/renderers/ → .... = rm_client、... = rm_client.ui
from ....model import HUD_MODES, MODULE_LIST_STYLES, Module
from ...animation import Easings
from ...clickgui_gpu import ClickGuiDrawList
from ...paint import (
    accent_color,
    draw_bar,
    glow_rect,
    rainbow_color_index,
    rounded_rect,
    text_width,
    with_alpha,
)
from ...theme import THEME, color
from ...jello_locale import chinese_enabled, category_label
from ...hud_locale import hud_text, hud_template, hud_module_name
# 注意：primitives 与本模块同属 ui/polar 子包（阶段 1 迁入），故为单级 ..
from ..primitives import ModuleListLayout

__all__ = [
    "ModuleListMixin",
]



class ModuleListMixin:
    """D12 ModuleList 模块列表绘制族（布局/数据面/十样式变体/周边 HUD/GPU 面）。"""

    def module_list_layout(self, width: int, height: int, start_y: float | None = None) -> ModuleListLayout:
        right_margin = 18.0
        top_margin = 18.0 if start_y is None else max(18.0, float(start_y))
        bottom_margin = 18.0
        clip = QRectF(
            0.0,
            top_margin,
            max(1.0, float(width)),
            max(1.0, float(height) - top_margin - bottom_margin),
        )
        return ModuleListLayout(
            anchor_x=max(1.0, float(width) - right_margin),
            anchor_y=top_margin,
            available_height=clip.height(),
            row_height=18.0,
            row_gap=3.0,
            right_margin=right_margin,
            top_margin=top_margin,
            clip_rect=clip,
        )

    def draw_minecraft_module_list(
        self,
        p: QPainter,
        width: int,
        height: int,
        hud_scale: float = 1.0,
    ) -> None:
        """Draw Sigma ActiveMods in MC's reserved upper-right column.

        This deliberately ignores the normal HUD alignment/style *for this
        frame only*.  It mirrors Jello's Minecraft composition and guarantees
        that a left-aligned PolarTextList cannot merge with the tactical map.
        """

        start_y = 8.0
        bottom = float(height) - 54.0
        layout = self._last_minecraft_hud_layout
        if layout is not None and hasattr(layout, "scoreboard"):
            scale = max(0.001, float(hud_scale))
            bottom = min(bottom, float(layout.scoreboard.top()) / scale - 8.0)
        available = max(36.0, bottom - start_y)
        self.draw_module_list_debug_white(
            p,
            width,
            start_y,
            available,
            gameplay_only=True,
            max_rows=14,
        )

    def draw_module_list_hud(
        self,
        p: QPainter,
        width: int,
        height: int,
        start_y: float | None = None,
        layout: ModuleListLayout | None = None,
    ) -> None:
        layout = layout or self.module_list_layout(width, height, start_y)
        hud = self.registry.get("HUD")
        setting = hud.get_setting("ModuleList Style") if hud else None
        style = str(setting.value) if setting else MODULE_LIST_STYLES[0]
        if style not in MODULE_LIST_STYLES:
            style = MODULE_LIST_STYLES[0]
        if style in ("JelloActiveMods", "DebugWhiteList"):
            # Sigma's ActiveMods owns the absolute top-right 10/6 px anchor;
            # do not feed it POLAR's 18 px safe-area clip.
            self.draw_module_list_debug_white(p, width, 6.0, float(height) - 6.0)
            return
        p.save()
        clip_rect = QRectF(layout.clip_rect)
        if style == "PolarTextList":
            chat_height = self.match_chat_reserved_height(now=time.perf_counter())
            if chat_height > 0.0:
                chat_top = float(height) - 40.0 - chat_height
                clip_rect.setBottom(
                    max(clip_rect.top(), min(clip_rect.bottom(), chat_top))
                )
            p.setClipRect(clip_rect)
            # 裸文字列表位于左侧，必须避开水印和左上战术地图。
            # 其他右对齐样式继续使用统一的右侧安全栏。
            polar_start = max(layout.anchor_y, 64.0)
            tactical_map = self.registry.get("TacticalMap")
            if tactical_map and tactical_map.enabled:
                polar_start = max(
                    polar_start,
                    self.tactical_map_rect(width, height).bottom() + 8.0,
                )
            self.draw_module_list_polar_text(p, polar_start)
        else:
            p.setClipRect(clip_rect)
        if style == "PolarRainbowRight":
            self.draw_module_list_rainbow_right(p, width, layout.anchor_y)
        elif style == "GlowRows":
            self.draw_module_list_glow_rows(p, width, layout.anchor_y)
        elif style == "Segmented":
            self.draw_module_list_segmented(p, width, layout.anchor_y)
        elif style == "DynamicIsland":
            self.draw_module_list_dynamic_island(p, width, layout.anchor_y)
        elif style == "KeyBindsRows":
            self.draw_module_list_keybind_rows(p, width, layout.anchor_y)
        elif style == "StatusBars":
            self.draw_module_list_status_bars(p, width, layout.anchor_y)
        elif style == "RobotCards":
            self.draw_module_list_robot_cards(p, width, layout.anchor_y)
        elif style in ("LiquidArrayList", "LiquidArrayTags", "LiquidArrayCompact", "LiquidArrayDock"):
            self.draw_module_list_liquid_array(p, width, style, layout.anchor_y, layout.available_height)
        elif style != "PolarTextList":
            self.draw_module_list_polar_text(p, max(layout.anchor_y, 64.0))
        p.restore()

    def draw_module_list_fast_rows(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        font = self.font("poppins_medium", 8.4)
        category_font = self.font("poppins_bold", 6.1)
        entries = self.module_list_entries(p, font, right_aligned=True)
        x = width - 18.0
        y = 18.0 if start_y is None else start_y
        max_rows = 26
        for idx, entry in enumerate(entries[:max_rows]):
            module = entry["module"]
            row_w = max(96.0, text_width(p, font, module.name) + 50.0)
            row_h = 17.0 * entry["height"]
            if row_h <= 0.1:
                continue
            rect = QRectF(x - row_w + entry["slide"], y, row_w, row_h)
            accent = rainbow_color_index(idx, speed=10, step=8)
            p.save()
            p.setOpacity(entry["alpha"])
            rounded_rect(p, rect, 6, color("#050507", 0.52), with_alpha(accent, 0.14), 0.8)
            index_rect = QRectF(rect.left(), rect.top(), 20.0, rect.height())
            rounded_rect(p, index_rect, 6, with_alpha(accent, 0.82))
            self.draw_text(p, index_rect, str(idx + 1), self.font("poppins_bold", 6.6), THEME.text, Qt.AlignmentFlag.AlignCenter)
            cat = module.category[:3].upper()
            self.draw_text(p, QRectF(rect.right() - 27, rect.top(), 24, rect.height()), hud_text(cat, chinese_enabled(self.registry)), category_font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.draw_text(p, rect.adjusted(25, 0, -32, 0), getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))), font, THEME.text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            p.restore()
            y += row_h + 3.0 * entry["height"]

    def module_list_preferences(self) -> tuple[bool, str, str, str]:
        hud = self.registry.get("HUD")
        alignment = self.setting_mode(hud, "ModuleList Alignment", "Right")
        order = self.setting_mode(hud, "ModuleList Order", "Descending")
        tags = self.setting_mode(hud, "Module Tags", "Category")
        animation = self.setting_mode(hud, "Module Animation", "FlyFlip")
        return alignment != "Left", order, tags, animation

    @staticmethod
    def module_list_tag(module: Module, tag_style: str) -> str:
        if tag_style == "Keybind":
            return module.keybind if module.keybind != "None" else "ON"
        if tag_style == "Category":
            return module.category[:3].upper()
        return ""

    def module_list_surface(self) -> str:
        hud = self.registry.get("HUD")
        return self.setting_mode(hud, "ModuleList Surface", "LiquidGlass")

    def module_list_entries(
        self,
        p: QPainter,
        font: QFont,
        right_aligned: bool | None = True,
        *,
        use_preferences: bool = False,
    ) -> list[dict[str, Any]]:
        if use_preferences:
            right_aligned, order_style, tag_style, animation_style = self.module_list_preferences()
        else:
            order_style, tag_style, animation_style = "Descending", "Off", "FlyFlip"
        assert right_aligned is not None
        visible = [m for m in self.registry.enabled_modules() if m.name not in ("HUD", "ClickGui")]
        sort_labels = tuple(
            (module.name, self.module_list_tag(module, tag_style))
            for module in visible
        )
        device = p.device()
        logical_dpi = int(device.logicalDpiX()) if device is not None else 96
        sort_cache_key = (sort_labels, font.key(), logical_dpi)
        if sort_cache_key != self._module_list_sort_cache_key:
            self._module_list_sort_cache_key = sort_cache_key
            self._module_list_sort_widths = {
                module.name: text_width(
                    p,
                    font,
                    f"{module.name}  {self.module_list_tag(module, tag_style)}",
                )
                for module in visible
            }
        visible.sort(
            key=lambda module: self._module_list_sort_widths.get(module.name, 0.0),
            reverse=order_style != "Ascending",
        )
        visible_names = {module.name for module in visible}
        for order, module in enumerate(visible):
            row = self.module_list_rows.get(module.name)
            if row is None:
                row = {"module": module, "order": order, "previous_order": order, "target": 1.0, "new": True}
                self.module_list_rows[module.name] = row
            elif row.get("order", order) != order:
                row["previous_order"] = row.get("order", order)
            row["module"] = module
            row["order"] = order
            row["target"] = 1.0
        for name, row in list(self.module_list_rows.items()):
            if name not in visible_names:
                row["target"] = 0.0
                row["order"] = row.get("order", len(self.module_list_rows))

        entries: list[dict[str, Any]] = []
        row_duration = self.anim_duration(0.22)
        order_duration = self.anim_duration(0.20)
        for name, row in list(self.module_list_rows.items()):
            target = float(row.get("target", 0.0))
            new = bool(row.pop("new", False))
            slide_distance = 34.0 if right_aligned else -34.0
            if animation_style == "Fade":
                slide_distance = 0.0
            alpha = self.anim.value(f"hud.modulelist.alpha.{name}", target, row_duration, Easings.ease_out_pow3, initial=0.0 if new else target)
            height = self.anim.value(f"hud.modulelist.height.{name}", target, row_duration, Easings.ease_out_pow3, initial=0.0 if new else target)
            slide = self.anim.value(
                f"hud.modulelist.slide.{name}",
                0.0 if target > 0 else slide_distance,
                row_duration,
                Easings.ease_out_pow3 if target > 0 else Easings.ease_in_pow3,
                initial=slide_distance if new else 0.0,
            )
            order_initial = float(row.get("previous_order", row.get("order", 0)))
            order_animated = self.anim.value(
                f"hud.modulelist.order.{name}",
                float(row.get("order", 0)),
                order_duration,
                Easings.ease_out_cubic,
                initial=order_initial,
            )
            if target <= 0.0 and alpha <= 0.025 and height <= 0.025:
                self.module_list_rows.pop(name, None)
                continue
            entries.append(
                {
                    "module": row["module"],
                    "alpha": alpha,
                    "height": height,
                    "slide": slide,
                    "order": int(row.get("order", 0)),
                    "order_animated": order_animated,
                    "tag": self.module_list_tag(row["module"], tag_style),
                }
            )
        entries.sort(key=lambda item: item["order"])
        return entries

    def debug_tahoma_font(self, pixel_size: int = 16) -> QFont:
        """jar tahoma from assets/fonts/tahoma.ttf (PLAIN).

        pixel_size is device pixels. jar createFont antiAlias=false → NoAntialias
        so 1K stays crisp (PreferAntialias made 1080p look soft/milky).
        """
        from PySide6.QtGui import QFontDatabase

        path = self._client_asset_path("assets", "fonts", "tahoma.ttf")
        font_id = getattr(self, "_debug_tahoma_font_id", -1)
        if font_id < 0 and path.exists():
            font_id = QFontDatabase.addApplicationFont(str(path))
            self._debug_tahoma_font_id = font_id
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        family = families[0] if families else "Tahoma"
        font = QFont(family)
        font.setPixelSize(max(8, int(pixel_size)))
        font.setBold(False)
        font.setItalic(False)
        font.setWeight(QFont.Weight.Normal)
        font.setKerning(False)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        # jar tahoma16 antiAlias=false — sharp stems on 1K/2K.
        font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        return font

    def debug_tahoma16_font(self) -> QFont:
        return self.debug_tahoma_font(16)

    def measure_debug_tahoma16(self, text: str) -> float:
        return float(QFontMetricsF(self.debug_tahoma16_font()).horizontalAdvance(text))

    def arraylist_shadow_image(self) -> QImage | None:
        """arraylistshadow.png baked for jar draw: black soft plate @ effective α.

        Source max A≈57 is nearly invisible on game HUD after stretch. Bake a
        soft plate that keeps the same 87×37 shape but peaks near α≈0.55 so the
        white text still reads as fluorescent (dark cushion under full white).
        """
        cached = getattr(self, "_arraylist_shadow_baked", None)
        if cached is not None:
            return cached if not cached.isNull() else None
        path = self._client_asset_path("assets", "hud", "arraylistshadow.png")
        if not path.exists():
            self._arraylist_shadow_baked = QImage()
            return None
        src = QImage(str(path)).convertToFormat(QImage.Format.Format_ARGB32)
        if src.isNull():
            self._arraylist_shadow_baked = QImage()
            return None
        peak = 1
        for yy in range(src.height()):
            for xx in range(src.width()):
                peak = max(peak, src.pixelColor(xx, yy).alpha())
        # Source peak A≈57 is nearly invisible after stretch on HUD. Keep the
        # same soft footprint but raise peak so white text still "pops" (demo look).
        # Still far from a solid black bar (no fill strip).
        target_peak = 190.0
        scale = target_peak / float(peak)
        out = QImage(src.size(), QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(0)
        for yy in range(src.height()):
            for xx in range(src.width()):
                a = src.pixelColor(xx, yy).alpha()
                if a <= 0:
                    continue
                aa = max(0, min(255, int(round(a * scale))))
                # Premultiplied black (RGB 0, alpha aa).
                out.setPixel(xx, yy, (aa << 24))
        self._arraylist_shadow_baked = out
        self._arraylist_shadow_image = out
        return out

    def draw_string_with_shadow_mc(
        self,
        p: QPainter,
        text: str,
        x: float,
        y: float,
        *,
        font: QFont | None = None,
        main: QColor | None = None,
        text_w: float | None = None,
        row_h: float = 16.0,
        shadow_px: float = 1.0,
    ) -> None:
        """MC 1.8 drawStringWithShadow — integer pixels, no AA (jar crisp white)."""
        main = main or QColor(255, 255, 255, 255)
        shadow = QColor(
            (main.red() & 0xFC) >> 2,
            (main.green() & 0xFC) >> 2,
            (main.blue() & 0xFC) >> 2,
            main.alpha(),
        )
        font = font or self.debug_tahoma16_font()
        metrics = QFontMetricsF(font)
        tw = float(text_w if text_w is not None else metrics.horizontalAdvance(text))
        # Snap to pixel grid — subpixel drawText is the main 1K blur source.
        ix = float(int(round(x)))
        iy = float(int(round(y)))
        sp = float(max(1, int(round(shadow_px))))
        rh = float(max(int(round(row_h)), metrics.height()))
        box = QRectF(ix, iy, tw + sp + 2.0, rh)
        # AlignTop matches canvas textBaseline=top (not VCenter which smears 16px rows).
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        p.setFont(font)
        p.setPen(shadow)
        p.drawText(box.translated(sp, sp), flags, text)
        p.setPen(main)
        p.drawText(box, flags, text)

    def draw_module_list_debug_white(
        self,
        p: QPainter,
        width: int,
        start_y: float | None = None,
        available_height: float | None = None,
        *,
        gameplay_only: bool = False,
        max_rows: int | None = None,
    ) -> None:
        """Exact Sigma 5.1.0 ``ActiveMods`` geometry and 150 ms motion.

        The retained method name is a migration alias for old profiles; the
        former Tahoma/solid-plate implementation is intentionally gone.
        """

        hud = self.registry.get("HUD")
        size = self.setting_mode(hud, "Jello List Size", "Normal")
        pixel_size = {"Normal": 20.0, "Small": 18.0, "Tiny": 14.0}.get(size, 20.0)
        font = self.font("jello_light", pixel_size * 72.0 / 96.0)
        # ActiveMods' comparator is hard-wired to JelloLightFont20 even when
        # Small/Tiny is selected.  Python's stable sort preserves the source
        # order when that comparator returns zero.
        sort_font = self.font("jello_light", 20.0 * 72.0 / 96.0)
        # DirectWrite metrics queries are ~1.5 ms/frame when repeated; the
        # heights are constant per font, and the metrics objects below are
        # only needed on a width-cache miss.
        font_height = math.ceil(self.font_line_height(font))
        metrics: QFontMetricsF | None = None
        sort_metrics: QFontMetricsF | None = None
        animations = self.setting_bool(hud, "Jello List Animations", True)
        now = float(self.gui_motion.now)
        frame_index = int(getattr(self, "_ui_frame_index", 0))
        last = float(getattr(self, "_jello_active_mods_last", now))
        if frame_index == self._jello_active_mods_frame_index:
            dt = 0.0
        else:
            frame_dt = float(getattr(self, "frame_dt", 0.0) or 0.0)
            dt = (
                max(0.0, min(0.05, frame_dt))
                if frame_dt > 1.0e-6
                else max(0.0, min(0.05, now - last))
            )
        self._jello_active_mods_frame_index = frame_index
        self._jello_active_mods_last = now
        rows: dict[str, dict[str, Any]] = getattr(self, "_jello_active_mods_rows", {})
        self._jello_active_mods_rows = rows

        # Sigma omits every module in its GUI category.  Render is the host
        # client's equivalent category, so ClickGui/HUD visual modules never
        # contaminate the list.
        visible = [module for module in self.registry.enabled_modules() if module.category != "Render"]
        if gameplay_only:
            gameplay_names = {
                "ArmorAutoAim", "EnergyAutoAim", "ArmorLock", "HeroLob",
                "FrictionWheel", "LockState", "SpinMode", "AutoSpinOnHit",
                "ChassisAssist", "PowerGuard", "Armor3DESP", "RobotOverlay",
                "RadarOverlay", "MapMarker",
            }
            visible = [module for module in visible if module.name in gameplay_names]
        visible_names = {module.name for module in visible}
        for module in visible:
            row = rows.setdefault(module.name, {"module": module, "progress": 0.0})
            row["module"] = module
            row["target"] = 1.0
        for name, row in tuple(rows.items()):
            if name not in visible_names:
                row["target"] = 0.0

        speed = 1.0 / 0.150
        for name, row in tuple(rows.items()):
            target = float(row.get("target", 0.0))
            progress = float(row.get("progress", target))
            if animations:
                step = dt * speed
                progress = min(target, progress + step) if target > progress else max(target, progress - step)
            else:
                progress = target
            row["progress"] = progress
            if target <= 0.0 and progress <= 0.0001:
                rows.pop(name, None)

        render_rows: list[tuple[Module, float, float]] = []
        width_cache: dict[tuple[str, str, str], tuple[float, float]] = getattr(
            self,
            "_jello_active_mods_width_cache",
            {},
        )
        self._jello_active_mods_width_cache = width_cache
        font_key = font.key()
        sort_font_key = sort_font.key()
        for row in rows.values():
            module = row["module"]
            measure_label = getattr(self, "module_display_label", lambda m, label: label)(module, module.name)
            cache_key = (font_key, sort_font_key, measure_label)
            widths = width_cache.get(cache_key)
            if widths is None:
                if metrics is None:
                    metrics = QFontMetricsF(font)
                if sort_metrics is None:
                    sort_metrics = QFontMetricsF(sort_font)
                widths = (
                    float(metrics.horizontalAdvance(measure_label)),
                    float(sort_metrics.horizontalAdvance(measure_label)),
                )
                width_cache[cache_key] = widths
            text_w, sort_w = widths
            render_rows.append(
                (module, text_w, sort_w, max(0.0, min(1.0, float(row["progress"]))))
            )
        render_rows.sort(key=lambda item: -item[2])
        if max_rows is not None:
            render_rows = render_rows[:max(1, int(max_rows))]
        if not render_rows:
            return

        shadow_path = self._client_asset_path("assets", "jello", "hud_shadow.png")
        gpu_commands = (
            self._gpu_hud_commands
            if self._collect_gpu_hud_commands and self.clickgui_gpu.available
            else None
        )
        shadow = None
        if gpu_commands is None:
            shadow = getattr(self, "_jello_hud_shadow", None)
            if shadow is None:
                shadow = QImage(str(shadow_path)).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                self._jello_hud_shadow = shadow

        y = 6.0 if start_y is None else max(6.0, float(start_y))
        margin = 7.0 if size == "Tiny" else 10.0
        height_limit = float(self.height()) if available_height is None else y + max(0.0, float(available_height))
        base_transform = QTransform(p.transform())
        base_scale = max(0.01, math.hypot(base_transform.m11(), base_transform.m12()))
        base_opacity = max(0.0, min(1.0, float(p.opacity())))
        p.save()
        if gpu_commands is None:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for module, text_w, _sort_w, progress in render_rows:
            if y + font_height > height_limit:
                break
            scale = 0.86 + 0.14 * progress
            x = float(width) - margin - text_w
            shadow_alpha = 0.36 * progress * math.sqrt(min(1.2, text_w / 63.0))
            shadow_rect = QRectF(
                float(width) - 1.5 * text_w - margin - 20.0,
                y - 20.0,
                3.0 * text_w,
                font_height + 41.0,
            )
            if shadow_alpha > 0.001:
                if gpu_commands is not None:
                    tint = QColor(255, 255, 255, round(255.0 * shadow_alpha * base_opacity))
                    gpu_commands.image(
                        base_transform.mapRect(shadow_rect),
                        shadow_path,
                        tint,
                    )
                elif shadow is not None and not shadow.isNull():
                    p.save()
                    p.setOpacity(shadow_alpha)
                    p.drawImage(shadow_rect, shadow)
                    p.restore()
            pivot = QPointF(float(width) - margin - text_w * 0.5, y + 12.0)
            text_rect = QRectF(x, y, text_w + 4.0, font_height + 2.0)
            if gpu_commands is not None:
                row_transform = QTransform(base_transform)
                row_transform.translate(pivot.x(), pivot.y())
                row_transform.scale(scale, scale)
                row_transform.translate(-pivot.x(), -pivot.y())
                gpu_commands.text(
                    row_transform.mapRect(text_rect),
                    getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))),
                    font,
                    QColor(254, 254, 254, round(255.0 * 0.95 * progress * base_opacity)),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    shadow=False,
                    render_scale=base_scale * scale,
                    raster_scale=1.0,
                    preserve_weight=True,
                    text_profile="jello",
                    device_pixel_snap=progress >= 0.999,
                )
            else:
                p.save()
                p.setOpacity(0.95 * progress)
                p.translate(pivot)
                p.scale(scale, scale)
                p.translate(-pivot)
                p.setFont(font)
                p.setPen(QColor(254, 254, 254, 255))
                p.drawText(
                    text_rect,
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                    getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))),
                )
                p.restore()
            ease_height = (
                2.0 * progress * progress
                if progress < 0.5
                else 1.0 - math.pow(-2.0 * progress + 2.0, 2.0) * 0.5
            )
            # The Java field is an int; compound assignment performs JVM f2i
            # truncation after every row rather than accumulating fractions.
            y = float(int(y + (font_height + 1.0) * ease_height))
        p.restore()

    def draw_module_list_liquid_array(
        self,
        p: QPainter,
        width: int,
        style: str,
        start_y: float | None = None,
        available_height: float | None = None,
    ) -> None:
        """LiquidBounce ArrayList 的宽度排序、对齐、标签和 fly/flip 行为的 PySide6 实现。"""
        compact = style == "LiquidArrayCompact"
        dock = style == "LiquidArrayDock"
        tags_variant = style == "LiquidArrayTags"
        name_font = self.font("poppins_medium", 7.2 if compact else 8.2)
        tag_font = self.font("poppins_medium", 5.8 if compact else 6.4)
        right_aligned, _order, tag_style, animation_style = self.module_list_preferences()
        entries = self.module_list_entries(p, name_font, right_aligned=None, use_preferences=True)
        if not entries:
            return
        row_h = 16.0 if compact else 21.0
        gap = 2.0 if compact else 4.0
        margin = 18.0
        y0 = 18.0 if start_y is None else start_y
        height_limit = self.height() if available_height is None else y0 + available_height
        max_rows = max(1, int((height_limit - y0 - 18.0) // (row_h + gap)))
        entries = entries[:max_rows]
        surface = self.module_list_surface()
        max_label = max(text_width(p, name_font, entry["module"].name) for entry in entries)
        max_tag = max((text_width(p, tag_font, entry["tag"]) for entry in entries), default=0.0)
        row_w = max(116.0 if compact else 144.0, min(238.0, max_label + max_tag + (38.0 if compact else 50.0)))
        x = width - margin - row_w if right_aligned else margin
        dock_rect = QRectF(x - 5.0, y0 - 5.0, row_w + 10.0, len(entries) * (row_h + gap) - gap + 10.0)
        if dock:
            if surface == "LiquidGlass":
                self.draw_hud_liquid_glass(p, dock_rect, 6.0, surface="MainGlass")
            else:
                rounded_rect(p, dock_rect, 6.0, color("#03070d", 0.66), color("#ffffff", 0.075), 0.8)

        for index, entry in enumerate(entries):
            module = entry["module"]
            order_y = entry["order_animated"] if animation_style == "FlyFlip" else float(index)
            y = y0 + order_y * (row_h + gap)
            h = row_h * entry["height"]
            if h <= 0.1:
                continue
            rect = QRectF(x + entry["slide"], y, row_w, h)
            hovered = rect.contains(self.hover_point)
            hover = self.anim.value(f"hud.liquid_array.hover.{module.name}", 1.0 if hovered else 0.0, self.anim_duration(0.15), Easings.ease_out_cubic)
            # OpenZen-style per-row RGB for rail / name / tags.
            row_rgb = rainbow_color_index(index, speed=10, step=8)
            p.save()
            p.setOpacity(p.opacity() * entry["alpha"])
            if surface == "LiquidGlass":
                self.draw_hud_liquid_glass(p, rect, 4.0 if compact else 5.0, surface="SubGlass" if compact else "MainGlass", hover=hover)
            elif not compact or hovered:
                rounded_rect(p, rect, 4.0 if compact else 5.0, color("#03060b", 0.48 + 0.16 * hover), with_alpha(row_rgb, 0.12 + 0.10 * hover), 0.7)
            rail = QRectF(rect.right() - 4.0, rect.top() + 2.0, 3.0, max(2.0, rect.height() - 4.0)) if right_aligned else QRectF(rect.left() + 1.0, rect.top() + 2.0, 3.0, max(2.0, rect.height() - 4.0))
            rounded_rect(p, rail, 1.2, with_alpha(row_rgb, 0.88 + 0.12 * hover))
            text_rect = rect.adjusted(8.0 if not right_aligned else 7.0, 0.0, -8.0 if right_aligned else -7.0, 0.0)
            if not right_aligned:
                text_rect.adjust(4.0, 0.0, 0.0, 0.0)
            else:
                text_rect.adjust(0.0, 0.0, -4.0, 0.0)
            tag = entry["tag"]
            if tag:
                tag_w = max(21.0, min(44.0, text_width(p, tag_font, tag) + 10.0))
                tag_x = rect.left() + 6.0 if right_aligned else rect.right() - tag_w - 6.0
                tag_rect = QRectF(tag_x, rect.top() + max(1.0, (rect.height() - 13.0) / 2.0), tag_w, min(13.0, max(4.0, rect.height() - 2.0)))
                if tags_variant:
                    if surface == "LiquidGlass" and not compact:
                        self.draw_hud_liquid_glass(p, tag_rect, 3.5, surface="SubGlass", hover=hover)
                    else:
                        rounded_rect(p, tag_rect, 3.5, with_alpha(row_rgb, 0.14 + 0.08 * hover), with_alpha(row_rgb, 0.28), 0.6)
                if right_aligned:
                    text_rect.setLeft(tag_rect.right() + 5.0)
                else:
                    text_rect.setRight(tag_rect.left() - 5.0)
                self.draw_text(p, tag_rect, (hud_text(tag, chinese_enabled(self.registry)) if tag_style == "Category" or (tag_style == "Keybind" and module.keybind == "None") else tag), tag_font, THEME.text if tags_variant else THEME.muted, Qt.AlignmentFlag.AlignCenter)
            align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter if right_aligned else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            # Name uses full RGB like OpenZen ModuleListHud text.
            self.draw_text(p, text_rect, getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))), name_font, with_alpha(row_rgb, 0.96), align)
            if dock:
                progress = self.anim.value(f"hud.liquid_array.progress.{module.name}", 1.0 if module.enabled else 0.0, self.anim_duration(0.20), Easings.ease_out_cubic, initial=1.0)
                bar = QRectF(rect.left() + 7.0, rect.bottom() - 2.3, max(2.0, (rect.width() - 14.0) * progress), 1.1)
                rounded_rect(p, bar, 0.55, with_alpha(row_rgb, 0.72))
            self.add_hotspot(rect, "module_toggle", module.name, tooltip=f"{getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry)))} | {category_label(module.category, chinese_enabled(self.registry))} | {module.keybind}")
            p.restore()

    def draw_module_list_polar_text(self, p: QPainter, start_y: float | None = None) -> None:
        """OpenZen ModuleListHud-style left bare text with animated RGB."""
        font = self.font("pingfang", 9.2)
        entries = self.module_list_entries(p, font, right_aligned=False)
        x = 18.0
        row_y = 18.0 if start_y is None else start_y
        for idx, entry in enumerate(entries):
            module = entry["module"]
            alpha = entry["alpha"]
            h = 12.0 * entry["height"]
            if h <= 0.1:
                continue
            p.save()
            p.setOpacity(alpha)
            # OpenZen: getRainbowColor(10, index * 8) + text shadow.
            c = rainbow_color_index(idx, speed=10, step=8)
            text_rect = QRectF(x + entry["slide"], row_y, 260, h)
            self.draw_text(p, text_rect.translated(1.0, 1.0), getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))), font, color("#000000", 0.78))
            self.draw_text(p, text_rect, getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))), font, c)
            p.restore()
            row_y += h + 1.0 * entry["height"]

    def draw_module_list_rainbow_right(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        """Right-aligned OpenZen RGB ArrayList with soft shadow plate."""
        font = self.font("poppins_medium", 9.6)
        entries = self.module_list_entries(p, font, right_aligned=True)
        x = width - 14.0
        y = 18.0 if start_y is None else start_y
        shadow_img = self.arraylist_shadow_image()
        for idx, entry in enumerate(entries):
            module = entry["module"]
            label = module.name
            tw = text_width(p, font, label)
            row_w = tw + 18.0
            row_h = 17.0 * entry["height"]
            if row_h <= 0.1:
                continue
            row = QRectF(x - row_w + entry["slide"], y, row_w, row_h)
            rgb = rainbow_color_index(idx, speed=10, step=8)
            p.save()
            p.setOpacity(entry["alpha"])
            # Soft underlay so rainbow glyphs stay readable on bright scene.
            plate = row.adjusted(-4.0, -1.0, 4.0, 1.0)
            if shadow_img is not None and not shadow_img.isNull():
                p.drawImage(plate.toRect(), shadow_img)
            else:
                rounded_rect(p, plate, 3.0, color("#000000", 0.38))
            # Right RGB accent bar (classic client ArrayList rail).
            bar = QRectF(row.right() - 2.5, row.top() + 2.0, 2.5, max(2.0, row.height() - 4.0))
            rounded_rect(p, bar, 1.0, rgb)
            align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            text_box = row.adjusted(0.0, 0.0, -6.0, 0.0)
            self.draw_text(p, text_box.translated(1.0, 1.0), hud_module_name(label, chinese_enabled(self.registry)), font, color("#000000", 0.70), align)
            self.draw_text(p, text_box, hud_module_name(label, chinese_enabled(self.registry)), font, rgb, align)
            p.restore()
            y += row_h + 1.5 * entry["height"]

    def draw_module_list_glow_rows(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        font = self.font("poppins_medium", 8.8)
        entries = self.module_list_entries(p, font, right_aligned=True)
        x = width - 18.0
        y = 18.0 if start_y is None else start_y
        for idx, entry in enumerate(entries):
            module = entry["module"]
            label = module.name
            row_w = max(118.0, text_width(p, font, label) + 42.0)
            row_h = 22.0 * entry["height"]
            if row_h <= 0.1:
                continue
            rect = QRectF(x - row_w + entry["slide"], y, row_w, row_h)
            c = rainbow_color_index(idx, speed=10, step=8)
            p.save()
            p.setOpacity(entry["alpha"])
            glow_rect(p, rect, 7, 0.08)
            rounded_rect(p, rect, 6, color("#050506", 0.68), with_alpha(c, 0.22))
            rounded_rect(p, QRectF(rect.right() - 4, rect.top() + 3, 2.5, max(3, rect.height() - 6)), 1, c)
            self.draw_text(
                p,
                rect.adjusted(10, 0, -12, 0),
                hud_module_name(label, chinese_enabled(self.registry)),
                font,
                c,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
            p.restore()
            y += row_h + 3.0 * entry["height"]

    def draw_module_list_segmented(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        font = self.font("pingfang", 8.2)
        title_font = self.font("museo", 9.6, QFont.Weight.Bold)
        entries = self.module_list_entries(p, font, right_aligned=True)
        x = width - 18.0
        y = 18.0 if start_y is None else start_y
        gap = 5.0
        header_w = 90.0
        header = QRectF(x - header_w, y, header_w, 24)
        rounded_rect(p, header, 5, color("#242424", 0.62), color("#2ab4ff", 0.25))
        self.draw_text(p, header.adjusted(9, 0, -9, 0), "POLAR", title_font, THEME.text)
        self.draw_text(p, header.adjusted(37, 0, -8, 0), hud_text("modules", chinese_enabled(self.registry)), font, THEME.cyan, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        y += 30
        for idx, entry in enumerate(entries):
            module = entry["module"]
            text_w = text_width(p, font, module.name)
            row_w = max(86.0, text_w + 31.0)
            row_h = 23.0 * entry["height"]
            if row_h <= 0.1:
                continue
            rect = QRectF(x - row_w + entry["slide"], y, row_w, row_h)
            p.save()
            p.setOpacity(entry["alpha"])
            rounded_rect(p, rect, 5, color("#242424", 0.50), color("#ffffff", 0.07))
            self.draw_text(p, QRectF(rect.left() + 8, rect.top(), 16, rect.height()), "*", self.font("material", 9.5), THEME.cyan, Qt.AlignmentFlag.AlignCenter)
            self.draw_text(p, rect.adjusted(25, 0, -8, 0), getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))), font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            p.restore()
            y += row_h + gap * entry["height"]

    def draw_module_list_dynamic_island(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        font = self.font("poppins_medium", 8.6)
        entries = self.module_list_entries(p, font, right_aligned=False)
        shown = entries[:5]
        if not shown:
            return
        labels = [entry["module"].name for entry in shown]
        text = "  |  ".join(labels)
        target_w = max(250.0, min(620.0, self.text_advance(font, text) + 58.0))
        active = max(entry["alpha"] for entry in shown)
        y = 18.0 if start_y is None else start_y
        rect = QRectF(width / 2 - target_w / 2, y, target_w, 30)
        p.save()
        p.setOpacity(active)
        glow_rect(p, rect, 15, 0.16)
        rounded_rect(p, rect, 15, color("#050506", 0.84), color("#ffffff", 0.12))
        self.draw_text(p, QRectF(rect.left() + 18, rect.top(), 58, rect.height()), "POLAR", self.font("museo", 10, QFont.Weight.Bold), THEME.text, Qt.AlignmentFlag.AlignCenter)
        x = rect.left() + 78
        for idx, entry in enumerate(shown):
            module = entry["module"]
            label_w = text_width(p, font, module.name)
            self.draw_text(
                p,
                QRectF(x, rect.top(), label_w + 6, rect.height()),
                getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))),
                font,
                rainbow_color_index(idx, speed=10, step=8),
                Qt.AlignmentFlag.AlignCenter,
            )
            x += label_w + 24
        p.restore()

    def draw_module_list_keybind_rows(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        name_font = self.font("poppins_medium", 8.3)
        key_font = self.font("poppins_regular", 8.0)
        entries = self.module_list_entries(p, name_font, right_aligned=True)
        header_h = 24.0
        row_h_base = 21.0
        panel_w = 188.0
        x = width - panel_w - 18
        y = 18.0 if start_y is None else start_y
        rounded_rect(p, QRectF(x, y, panel_w, header_h), 5, color("#000000", 0.74), color("#ffffff", 0.10))
        self.draw_text(p, QRectF(x + 9, y, panel_w - 18, header_h), hud_text("Hotkeys", chinese_enabled(self.registry)), self.font("poppins_medium", 8.7), THEME.text)
        row_y = y + header_h + 3
        for idx, entry in enumerate(entries):
            module = entry["module"]
            row_h = row_h_base * entry["height"]
            if row_h <= 0.1:
                continue
            key = module.keybind if module.keybind != "None" else "ON"
            name_w = min(120.0, text_width(p, name_font, module.name) + 10.0)
            key_w = 24.0 if key == "ON" else min(42.0, max(24.0, text_width(p, key_font, key) + 10.0))
            base_x = width - 18 - name_w - 3 - key_w + entry["slide"]
            name_rect = QRectF(base_x, row_y, name_w, row_h)
            key_rect = QRectF(base_x + name_w + 3, row_y, key_w, row_h)
            p.save()
            p.setOpacity(entry["alpha"])
            rounded_rect(p, name_rect, 5, color("#000000", 0.36), color("#ffffff", 0.08))
            rounded_rect(p, key_rect, 5, color("#000000", 0.36), color("#ffffff", 0.08))
            row_rgb = rainbow_color_index(idx, speed=10, step=8)
            self.draw_text(p, name_rect.adjusted(6, 0, -6, 0), getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))), name_font, row_rgb, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.draw_text(p, key_rect, (hud_text("ON", chinese_enabled(self.registry)) if module.keybind == "None" else key), key_font, row_rgb, Qt.AlignmentFlag.AlignCenter)
            p.restore()
            row_y += row_h + 3 * entry["height"]

    def draw_module_list_status_bars(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        name_font = self.font("pingfang", 8.3)
        value_font = self.font("axiforma_bold", 7.8)
        entries = self.module_list_entries(p, name_font, right_aligned=True)
        x = width - 18.0
        y = 18.0 if start_y is None else start_y
        for idx, entry in enumerate(entries):
            module = entry["module"]
            category = module.category[:3].upper()
            total_w = max(
                132.0,
                text_width(p, name_font, module.name) + text_width(p, value_font, category) + 58.0,
            )
            total_w = min(max(132.0, total_w), max(132.0, width - 36.0))
            row_h = 18.0 * entry["height"]
            if row_h <= 0.1:
                continue
            rect = QRectF(x - total_w + entry["slide"], y, total_w, row_h)
            icon_w = 21.0
            c = rainbow_color_index(idx, speed=10, step=8)
            ratio = 0.52 + (idx % 5) * 0.075
            p.save()
            p.setOpacity(entry["alpha"])
            rounded_rect(p, QRectF(rect.left() + icon_w, rect.top(), rect.width() - icon_w, row_h), 5, color("#1e1e23", 0.46), with_alpha(c, 0.14))
            rounded_rect(p, QRectF(rect.left() + icon_w, rect.top(), max(4.0, (rect.width() - icon_w) * ratio), row_h), 5, with_alpha(c, 0.42))
            rounded_rect(p, QRectF(rect.left(), rect.top(), icon_w, row_h), 5, with_alpha(c, 0.78))
            self.draw_text(p, QRectF(rect.left(), rect.top(), icon_w, row_h), str(idx + 1), value_font, THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.draw_text(p, QRectF(rect.left() + icon_w + 6, rect.top(), rect.width() - icon_w - 46, row_h), getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))), name_font, c)
            self.draw_text(p, QRectF(rect.right() - 42, rect.top(), 36, row_h), hud_text(category, chinese_enabled(self.registry)), value_font, with_alpha(c, 0.90), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            p.restore()
            y += row_h + 2.0 * entry["height"]

    def draw_module_list_robot_cards(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        font = self.font("pingfang", 8.4)
        sub_font = self.font("poppins_regular", 7.2)
        entries = self.module_list_entries(p, font, right_aligned=True)
        x = width - 18.0
        y = 18.0 if start_y is None else start_y
        for idx, entry in enumerate(entries):
            module = entry["module"]
            row_w = max(158.0, text_width(p, font, module.name) + 86.0)
            row_h = 30.0 * entry["height"]
            if row_h <= 0.1:
                continue
            rect = QRectF(x - row_w + entry["slide"], y, row_w, row_h)
            c = rainbow_color_index(idx, speed=10, step=8)
            p.save()
            p.setOpacity(entry["alpha"])
            glow_rect(p, rect, 9, 0.07)
            rounded_rect(p, rect, 8, color("#000000", 0.38), with_alpha(c, 0.18))
            badge = QRectF(rect.left() + 5, rect.top() + 5, 20, max(4.0, rect.height() - 10))
            rounded_rect(p, badge, 7, with_alpha(c, 0.70), color("#ffffff", 0.10))
            self.draw_text(p, badge, category_label(module.category, chinese_enabled(self.registry))[0], self.font("poppins_bold", 8.2), THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.draw_text(p, rect.adjusted(32, 3, -54, -13), getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))), font, c)
            self.draw_text(p, rect.adjusted(32, 15, -54, -2), category_label(module.category, chinese_enabled(self.registry)), sub_font, THEME.muted)
            chip = QRectF(rect.right() - 46, rect.top() + 6, 38, max(4.0, rect.height() - 12))
            rounded_rect(p, chip, 6, with_alpha(c, 0.14), with_alpha(c, 0.28))
            self.draw_text(p, chip, module.keybind if module.keybind != "None" else hud_text("ON", chinese_enabled(self.registry)), sub_font, c, Qt.AlignmentFlag.AlignCenter)
            p.restore()
            y += row_h + 5.0 * entry["height"]

    def draw_keybinds_strip(self, p: QPainter, width: int, height: int) -> None:
        rows = [m for m in self.registry.modules if m.enabled and m.keybind != "None"][:5]
        if not rows:
            return
        font = self.font("poppins_medium", 8.0)
        row_w = 136.0
        row_h = 18.0
        panel_h = 24 + len(rows) * row_h
        x = width - row_w - 18
        hud = self.registry.get("HUD")
        mode_setting = hud.get_setting("Right Display") if hud else None
        right_mode = str(mode_setting.value) if mode_setting else HUD_MODES[0]
        if right_mode == "ModuleListHud":
            y = min(height - panel_h - 62.0, max(320.0, height * 0.44))
        elif right_mode == "TacticalMapHud":
            map_rect = self.tactical_map_rect(width, height)
            if self.setting_bool(hud, "Module List", True):
                x = max(18.0, map_rect.left() - row_w - 12.0)
                y = min(height - panel_h - 20.0, max(map_rect.bottom() + 12.0, height * 0.58))
            else:
                y = min(height - panel_h - 20.0, max(map_rect.bottom() + 12.0, 78.0))
        else:
            y = max(78.0, height * 0.23)
        active = self.anim.value("hud.keybind.strip", 1.0, self.anim_duration(0.22), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        rounded_rect(p, QRectF(x, y, row_w, panel_h), 7, self.hud_surface(0.64), self.hud_panel_outline(0.09))
        self.draw_text(p, QRectF(x + 9, y + 4, row_w - 18, 14), hud_text("KeyBinds", chinese_enabled(self.registry)), font, THEME.text)
        for i, module in enumerate(rows):
            yy = y + 22 + i * row_h
            self.draw_text(p, QRectF(x + 9, yy, row_w - 56, row_h), getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))), font, THEME.muted)
            self.draw_text(p, QRectF(x + 65, yy, row_w - 74, row_h), module.keybind, font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p.restore()

    def draw_keybinds_hud(self, p: QPainter, width: int) -> None:
        modules = [m for m in self.registry.modules if m.keybind != "None"]
        modules.sort(key=lambda module: module.name.lower())
        rect = QRectF(width - 226, 78, 202, 36 + min(11, len(modules)) * 22)
        active = self.anim.value("hud.keybinds.full", 1.0, self.anim_duration(0.26), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        glow_rect(p, rect, 8, 0.07 if self.hud_clean() else 0.18)
        rounded_rect(p, rect, 8, self.hud_surface(0.72), self.hud_panel_outline(0.09))
        self.draw_text(p, rect.adjusted(12, 7, -12, -rect.height() + 27), hud_text("KeyBinds", chinese_enabled(self.registry)), self.font("poppins_bold", 10), THEME.text)
        for i, module in enumerate(modules[:11]):
            y = rect.top() + 32 + i * 22
            row = QRectF(rect.left() + 8, y, rect.width() - 16, 19)
            row_active = self.anim.value(f"hud.bind.row.{module.name}", 1.0 if module.enabled else 0.42, self.anim_duration(0.18), Easings.ease_out_quad, initial=0.0)
            rounded_rect(p, row, 5, color("#ffffff", 0.035 + 0.035 * row_active))
            self.draw_text(p, row.adjusted(8, 0, -56, 0), getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))), self.font("poppins_regular", 8.2), THEME.text if module.enabled else THEME.muted)
            self.draw_text(p, row.adjusted(72, 0, -8, 0), module.keybind, self.font("poppins_medium", 8.2), self.hud_accent(), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p.restore()

    def draw_robot_list_hud(self, p: QPainter, width: int) -> None:
        rows = self.state.robot_rows or []
        rect = QRectF(width - 254, 78, 230, 38 + len(rows) * 27)
        active = self.anim.value("hud.robot_list", 1.0, self.anim_duration(0.26), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        glow_rect(p, rect, 8, 0.07 if self.hud_clean() else 0.18)
        rounded_rect(p, rect, 8, self.hud_surface(0.72), self.hud_panel_outline(0.09))
        self.draw_text(p, rect.adjusted(12, 8, -12, -rect.height() + 29), hud_text("RobotListHud", chinese_enabled(self.registry)), self.font("poppins_bold", 10), THEME.text)
        for i, row_data in enumerate(rows):
            y = rect.top() + 35 + i * 27
            row = QRectF(rect.left() + 9, y, rect.width() - 18, 22)
            rounded_rect(p, row, 5, color("#ffffff", 0.04))
            name = f"{row_data['id']} {row_data['name']}"
            hp = int(row_data["hp"])
            hp_max = max(1, int(row_data["max"]))
            self.draw_text(p, row.adjusted(7, 0, -88, 0), name, self.font("poppins_regular", 8), THEME.text)
            draw_bar(p, QRectF(row.right() - 76, row.top() + 7, 48, 5), hp / hp_max, self.hud_accent(), color("#ffffff", 0.12))
            self.draw_text(p, QRectF(row.right() - 25, row.top(), 21, row.height()), str(hp), self.font("poppins_regular", 7.5), THEME.muted, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p.restore()

    #: 状态卡片 -> 控制它是否出现的模块与设置项名。
    #: 这三个开关此前只在 tactical_map 的两个构造器里被读，而这里是**第三个
    #: 消费点**、原先无条件建 4 张卡——同一个设置项在一个面板有效、另一个无效，
    #: 是本会话反复踩到的那类错。现在三处共用同一判据。
    STATUS_CARD_SETTING: dict[str, tuple[str, str]] = {
        "GIMPOWER": ("GimbalPower", "Show Voltage"),
        "POWERLIM": ("PowerLimit", "Show Limit"),
        "SPEEDLIM": ("SpeedLimit", "Show Speed"),
        "CAP": ("Capacitor", "Show Bar"),
    }

    def status_card_visible(self, label: str) -> bool:
        """这张卡该不该出现。查不到模块/设置项时返回 True（保持现行为）。"""
        pair = self.STATUS_CARD_SETTING.get(label)
        if pair is None:
            return True
        module = self.registry.get(pair[0])
        if module is None or not module.enabled:
            return False
        setting = module.get_setting(pair[1])
        return True if setting is None else bool(getattr(setting, "value", True))

    def status_card_warning(self, label: str, ratio: float) -> bool:
        """这张卡该不该用告警色。

        - GIMPOWER：`GimbalPower / Warn Below`（伏特）。电压**越低越险**，
          所以是 `gimbal_power < Warn Below`，不能拿 ratio>阈值去判。
        - CAP：复用 `Capacitor / Warn`，与 tactical_map 两处构造器同一判据。
        - POWERLIM / SPEEDLIM：保留原有 ratio>0.78 规则，不改既有观感。
        """
        try:
            value = float(ratio)
        except (TypeError, ValueError):
            return False
        if label == "GIMPOWER":
            module = self.registry.get("GimbalPower")
            if module is None or not module.enabled:
                return False
            setting = module.get_setting("Warn Below")
            if setting is None:
                return False
            try:
                floor = float(setting.value)
            except (TypeError, ValueError):
                return False
            if floor <= 0.0:
                return False  # 0 = 关闭告警
            return float(self.state.gimbal_power) < floor
        if label == "CAP":
            return self.capacitor_below_warn()
        return label in ("POWERLIM", "SPEEDLIM") and value > 0.78

    def status_card_glow(self, label: str, warning: bool, clean: bool) -> float:
        """卡片外发光量。

        - 默认（无告警）与改动前完全一致：clean 0.05 / 否则 0.10。
        - CAP 卡受 `Capacitor / Use Glow` 控制（关掉后为 0）。
        - GIMPOWER 卡告警时按 `GimbalPower / Glow Alert` 加强发光：
          这正是该设置项承诺的效果；关掉它则保持原发光量。
        """
        base = 0.05 if clean else 0.10
        if label == "CAP":
            module = self.registry.get("Capacitor")
            setting = module.get_setting("Use Glow") if module is not None else None
            if setting is not None and not bool(getattr(setting, "value", True)):
                return 0.0
        if label == "GIMPOWER" and warning:
            module = self.registry.get("GimbalPower")
            if module is not None:
                setting = module.get_setting("Glow Alert")
                if setting is None or bool(getattr(setting, "value", True)):
                    return base + 0.22
        return base

    def draw_status_cards_hud(self, p: QPainter, width: int) -> None:
        cards = [
            card for card in (
                ("GIMPOWER", f"{self.state.gimbal_power}V", self.state.gimbal_power / 30.0),
                ("POWERLIM", f"{self.state.power_limit}W", self.state.power_limit / 120.0),
                ("SPEEDLIM", f"{self.state.speed_limit}dps", self.state.speed_limit / 720.0),
                ("CAP", f"{self.state.effective_capacitor_percent}%", self.state.effective_capacitor_percent / 100.0),
            )
            if self.status_card_visible(card[0])
        ]
        x = width - 194
        y = 78.0
        for i, (label, value, ratio) in enumerate(cards):
            active = self.anim.value(f"hud.card.{label}", 1.0, self.anim_duration(0.25 + i * 0.02), Easings.ease_out_pow4, initial=0.0)
            rect = QRectF(x + (1.0 - active) * 30, y + i * 55, 170, 44)
            clean = self.hud_clean()
            warning = self.status_card_warning(label, ratio)
            p.save()
            p.setOpacity(active)
            glow_rect(p, rect, 8, self.status_card_glow(label, warning, clean))
            rounded_rect(p, rect, 8, self.hud_surface(0.72), self.hud_panel_outline(0.09))
            self.draw_text(p, rect.adjusted(12, 6, -12, -20), hud_text(label, chinese_enabled(self.registry)), self.font("poppins_medium", 8), THEME.muted)
            self.draw_text(p, rect.adjusted(12, 15, -12, -5), value, self.font("poppins_bold", 11), THEME.text)
            draw_bar(p, QRectF(rect.left() + 82, rect.top() + 27, 72, 5), ratio, self.hud_state_color("warning" if warning else "info", ratio), color("#ffffff", 0.12))
            p.restore()

    #: RM 指示灯 -> 控制它的模块与设置项名。
    #: 这三行以前全是装饰：`Show State` / `Anti Rotation` / `Show Lock`
    #: 在界面上能关，但灯永远亮着（`Show Lock` 上一批只接了装甲锁定框，
    #: 没接这颗灯 —— 同一个设置名管两个视觉元素时，两处都得接，否则
    #: 用户看到的就是"关了一半"）。
    #: 没列出的灯不受模块级开关控制。
    #:
    #: ATKBUF/DEFBUF 与 Small/Large Buff 的对应不是猜的，证据在本仓库自己的
    #: 代码里：`RobotState.buff_indicator_state()` 在无类型数据（LabDebug mock）
    #: 时的回退分支字面返回 `(bool(self.small_buff), bool(self.large_buff), "")` ——
    #: 即第一返回值（ATKBUF）就是 small_buff，第二返回值（DEFBUF）就是 large_buff。
    #: 这也符合 RM 术语：小写增益=攻击增益，大写增益=防御增益。
    RM_INDICATOR_SETTING: dict[str, tuple[str, str]] = {
        "FRIC": ("FrictionWheel", "Show State"),
        "LOCK": ("LockState", "Show Lock"),
        "ANTIRO": ("BuffStatus", "Anti Rotation"),
        "ATKBUF": ("BuffStatus", "Small Buff"),
        "DEFBUF": ("BuffStatus", "Large Buff"),
    }

    def rm_indicator_visible(self, key: str) -> bool:
        """这颗灯该不该占位。查不到模块或设置项时返回 True（不改现有行为）。

        返回 False 是**整颗灯不出现**，而不是画成熄灭：指示灯数量决定面板
        行数（rows = ceil(len/3)），所以隐藏会正确地把面板收窄。
        画成"熄灭"会和"状态真的是关"混淆——操作员分不清是模块关了还是
        功能没开，那是更糟的歧义。
        """
        pair = self.RM_INDICATOR_SETTING.get(key)
        if pair is None:
            return True
        module = self.registry.get(pair[0])
        if module is None:
            return True
        setting = module.get_setting(pair[1])
        if setting is None:
            return True
        return bool(getattr(setting, "value", True))

    def draw_rm_indicators_hud(self, p: QPainter, width: int) -> None:
        # Buff 指示：优先裁判 Buff 解码（RobotState.buff_indicator_state），
        # LabDebug mock 行无 buff_type 时自动回退旧演示布尔。
        atk_buf, def_buf, buff_detail = self.state.buff_indicator_state()
        indicators = [
            (label, on)
            for label, on in (
                ("FRIC", bool(self.state.friction_wheel)),
                ("LOCK", bool(self.state.lock_state)),
                ("ATKBUF", atk_buf),
                ("DEFBUF", def_buf),
                ("ANTIRO", bool(self.state.anti_rotation)),
                ("PTE", bool(self.state.pte_ready)),
                ("JUP", bool(self.state.jump_ready)),
                ("WHE", bool(self.state.wheel_online)),
                ("LES", bool(self.state.les_online)),
            )
            if self.rm_indicator_visible(label)
        ]
        # 官方模块在线状态（RobotModuleStatus 已解码进 robot_module_status）：
        # 有真实数据时追加一行模块芯片；LabDebug/无数据时不占位、面板不拉高。
        indicators += list(self.state.robot_module_indicator_state())
        # 队内 0x5A 链路芯片：仅在链路参与工作时出现，以免改变官方/实验室
        # 的既有视觉基线。LNK=下位机在供数，DRV=底盘驾驶已授权，
        # GIM=云台控制权在客户端（视觉 NUC 占用或抢环锁出时为关）。
        if bool(getattr(self.state, "polar_link_active", False)):
            indicators += [
                ("LNK", True),
                ("DRV", bool(getattr(self.state, "polar_drive_armed", False))),
                ("GIM", bool(getattr(self.state, "polar_gimbal_client", False))),
                # MOT 亮 = 已交叉验证“给了速度且车确实在动”；不亮时要区分
                # 是没给速度（IDLE）还是发了但没动（STUCK），看下方诊断行。
                ("MOT", str(getattr(self.state, "polar_motion_state", "IDLE")) == "OK"),
            ]
        rows = -(-len(indicators) // 3)
        chips_bottom = 36 + rows * 28
        # 链路诊断：连上时要显示端口/段数/仲裁态；连不上时必须把原因摊开，
        # 否则操作员只看到 LNK 灯灭，分不清是没插、选错口还是固件没回。仅
        # 在台架/实车场景或链路已活跃时占位，不影响官方/实验室视觉基线。
        polar_stats = dict((getattr(self.state, "backend_stats", {}) or {}).get("polar_link") or {})
        polar_wanted = bool(
            polar_stats
            and (
                bool(getattr(self.state, "polar_link_active", False))
                or str(polar_stats.get("port") or "")
                in ("Auto",)
                # 不能再把场景名当条件：队内 0x5A 链路是独立于场景的并行通道，
                # 在大疆官方场景下配了 COM 口也要显示诊断面板。
                or bool(str(polar_stats.get("port") or "").strip())
                and str(polar_stats.get("port") or "").strip() != "Stub"
            )
        )
        link_extra = 20 if polar_wanted else 0
        # 链路活着时多给一行：排错时最需要的是“我指令 X / 车实测 Y”并排看到。
        if bool(getattr(self.state, "polar_link_active", False)):
            link_extra += 20
        rect = QRectF(width - 244, 78, 220, max(172, chips_bottom + 30 + link_extra))
        active = self.anim.value("hud.rm_indicators", 1.0, self.anim_duration(0.26), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        glow_rect(p, rect, 8, 0.06 if self.hud_clean() else 0.16)
        rounded_rect(p, rect, 8, self.hud_surface(0.72), self.hud_panel_outline(0.09))
        self.draw_text(p, rect.adjusted(12, 8, -12, -rect.height() + 29), hud_text("RMIndicatorsHud", chinese_enabled(self.registry)), self.font("poppins_bold", 10), THEME.text)
        chip_w = 62
        for i, (label, enabled) in enumerate(indicators):
            col = i % 3
            row = i // 3
            chip = QRectF(rect.left() + 12 + col * (chip_w + 6), rect.top() + 36 + row * 28, chip_w, 21)
            enabled_anim = self.anim.value(f"hud.ind.{label}", 1.0 if enabled else 0.0, self.anim_duration(0.18), Easings.ease_out_quad, initial=0.0)
            fill = with_alpha(self.hud_success() if enabled else self.hud_accent(), 0.12 + 0.44 * enabled_anim)
            rounded_rect(p, chip, 6, fill, color("#ffffff", 0.08))
            self.draw_text(p, chip, hud_text(label, chinese_enabled(self.registry)), self.font("poppins_medium", 7.3), THEME.text if enabled else THEME.muted, Qt.AlignmentFlag.AlignCenter)
            if label in ("ATKBUF", "DEFBUF") and buff_detail:
                kind = "攻击增益" if label == "ATKBUF" else "防御/回复增益"
                state_text = "生效" if enabled else "无"
                self.add_hotspot(chip, "buff_indicator", label, tooltip=f"{kind}：{state_text} | {buff_detail}")
        ammo = f"17mm {self.state.projectile_allowance_17mm} | 42mm {self.state.projectile_allowance_42mm}"
        ammo_rect = QRectF(rect.left() + 12, rect.top() + chips_bottom + 4, rect.width() - 24, 18)
        self.draw_text(p, ammo_rect, ammo, self.font("poppins_regular", 8.2), THEME.muted, Qt.AlignmentFlag.AlignVCenter)
        if polar_wanted:
            decode = dict(polar_stats.get("decode") or {})
            authority_info = polar_stats.get("authority")
            if not isinstance(authority_info, dict):
                authority_info = {}
            authority = str(authority_info.get("state") or "-")
            link_alive = bool(polar_stats.get("link_alive"))
            motion = dict(polar_stats.get("motion") or {})
            motion_state = str(motion.get("state") or "IDLE")
            if link_alive:
                # 兵种上屏：限幅与驾驶权限现在跟“实际连的那台车”走，
                # 操作员必须能看到现在算的是哪台、限速多少，否则选错车无从发现。
                # Role hud_code remains a diagnostic identifier in either language.
                role_tag = str(polar_stats.get("role_hud_code") or "?")
                role_id = int(polar_stats.get("role_id") or 0)
                role_limit = float(polar_stats.get("role_max_linear_mps") or 0.0)
                base = hud_template(
                    "ROBOT {port} · {segments} seg · {role}#{id} {limit}m/s",
                    chinese_enabled(self.registry),
                    port=polar_stats.get('resolved_port') or polar_stats.get('port'),
                    segments=int(polar_stats.get('segments_received') or 0),
                    role=role_tag, id=role_id, limit=f"{role_limit:.2f}",
                )
                # 选错车是事故级信息（限速不同、给哨兵下驾驶指令会破坏自动巡线），
                # 优先于其他一切状态上屏。
                if str(polar_stats.get("role_mismatch") or ""):
                    link_text = (
                        f"{base} · " + hud_template(
                            "ROLE MISMATCH UI#{selected}!=ROBOT#{reported}", chinese_enabled(self.registry),
                            selected=int(polar_stats.get('role_selected_id') or 0),
                            reported=int(polar_stats.get('role_reported_id') or 0),
                        )
                    )
                    link_color = self.hud_warning()
                elif str(polar_stats.get("role_drive_block") or ""):
                    link_text = f"{base} · {hud_text('DRIVE BLOCKED', chinese_enabled(self.registry))}"
                    link_color = self.hud_warning()
                # 车辆不响应是比仲裁态更紧急的信息，优先上屏。
                elif motion_state in ("STUCK", "NO_FEEDBACK", "STALE_FEEDBACK", "NO_MOTION"):
                    link_text = f"{base} · {hud_text(motion_state, chinese_enabled(self.registry))}"
                    link_color = self.hud_warning()
                else:
                    link_text = f"{base} · {hud_text(authority, chinese_enabled(self.registry))}"
                    link_color = self.hud_success() if motion_state == "OK" else self.hud_accent()
            else:
                # Error codes and port names remain exact diagnostic identifiers.
                code = str(polar_stats.get("last_error_code") or "") or "NO_STREAM"
                port = str(polar_stats.get("port") or "-")
                link_text = f"{hud_text('LINK OFF', chinese_enabled(self.registry))} · {code} · {port}"
                link_color = self.hud_warning()
            link_rect = QRectF(rect.left() + 12, rect.top() + chips_bottom + 22, rect.width() - 24, 16)
            self.draw_text(p, link_rect, link_text, self.font("poppins_medium", 7.6), link_color, Qt.AlignmentFlag.AlignVCenter)
            detail = (
                f"兵种 {polar_stats.get('role_label') or '?'}（编号 {int(polar_stats.get('role_id') or 0)}）"
                f"· {polar_stats.get('role_chassis') or '?'} · 限速 "
                f"{float(polar_stats.get('role_max_linear_mps') or 0.0):.2f} m/s / "
                f"{float(polar_stats.get('role_max_yaw_rps') or 0.0):.2f} rad/s\n"
            )
            if not polar_stats.get("role_known", True):
                detail += "⚠ 该兵种的固件参数未能查证，已按保守限速执行，请核对 robot_param_*.h\n"
            mismatch_text = str(polar_stats.get("role_mismatch") or "")
            if mismatch_text:
                detail += f"⚠ {mismatch_text}\n"
            drive_block_text = str(polar_stats.get("role_drive_block") or "")
            if drive_block_text:
                detail += f"⚠ {drive_block_text}\n"
            detail += (
                f"端口 {polar_stats.get('port')} → {polar_stats.get('resolved_port') or '未解析'}\n"
                f"状态码 {polar_stats.get('last_error_code') or 'OK'}\n"
                f"原因 {polar_stats.get('last_error') or '无'}\n"
                f"已收段 {polar_stats.get('segment_counts')}\n"
                f"CRC8 错 {decode.get('crc8_errors', 0)} / CRC16 错 {decode.get('crc16_errors', 0)} / "
                f"长度错 {decode.get('length_errors', 0)} / 未知段 {decode.get('unknown_segments', 0)}\n"
                f"仲裁 {authority}：{str(authority_info.get('reason') or '')}\n"
                f"车辆响应 {motion_state}：{str(motion.get('detail') or '')}\n"
                f"指令 {float(motion.get('commanded_mps') or 0.0):.2f} m/s / 实测 {float(motion.get('measured_mps') or 0.0):.2f} m/s\n"
                f"已探测到：{(polar_stats.get('detected_ports') or ['无'])[:4]}\n"
                f"发送 {int(polar_stats.get('frames_sent') or 0)} 帧（含 {int(polar_stats.get('zero_frames_sent') or 0)} 帧归零）"
            )
            self.add_hotspot(link_rect, "polar_link", "diagnostics", tooltip=detail[:500])
            if link_alive:
                # 第 3 行：实测速度 + 云台姿态。全 ASCII（HUD 字体无 CJK 字形）。
                overlay = getattr(self.state, "polar_overlay", {}) or {}
                spd = float(overlay.get("measured_vx_mps", 0.0) or 0.0)
                lat = float(overlay.get("measured_vy_mps", 0.0) or 0.0)
                yaw_rate = float(overlay.get("measured_wz_rps", 0.0) or 0.0)
                gimbal_yaw = float(overlay.get("gimbal_yaw_deg", 0.0) or 0.0)
                gimbal_pitch = float(overlay.get("gimbal_pitch_deg", 0.0) or 0.0)
                telem_text = (
                    f"v {spd:+.2f}/{lat:+.2f}  w {yaw_rate:+.2f}  G {gimbal_yaw:+.0f}/{gimbal_pitch:+.0f}"
                )
                telem_rect = QRectF(rect.left() + 12, rect.top() + chips_bottom + 38, rect.width() - 24, 16)
                self.draw_text(
                    p, telem_rect, telem_text, self.font("poppins_medium", 7.4),
                    THEME.muted, Qt.AlignmentFlag.AlignVCenter,
                )
                self.add_hotspot(
                    telem_rect,
                    "polar_link",
                    "telemetry",
                    tooltip=(
                        f"实测速度 vx {spd:+.3f} / vy {lat:+.3f} m/s，角速度 {yaw_rate:+.3f} rad/s\n"
                        f"云台 yaw {gimbal_yaw:+.2f}° / pitch {gimbal_pitch:+.2f}°\n"
                        f"底盘驾驶授权：{'开' if getattr(self.state, 'polar_drive_armed', False) else '关'}"
                        f"　云台控制权：{getattr(self.state, 'polar_authority_state', 'PASSIVE')}\n"
                        f"{getattr(self.state, 'polar_authority_reason', '')}"
                    )[:500],
                )
        p.restore()

    def build_gpu_module_list(
        self,
        entries: list[dict[str, Any]],
        layout: ModuleListLayout,
        hud_scale: float,
        width: int,
        height: int,
        style: str,
    ) -> ClickGuiDrawList:
        """Emit the compact right-side ModuleList as one GPU draw list.

        The old high-resolution fallback used this same compact density, but
        still paid a QPainter round-rect/text pass for every row. Keeping the
        geometry in logical HUD pixels makes the list stable under HUD Scale and
        lets the renderer apply DPR only once at submission time.
        """
        commands = ClickGuiDrawList()
        scale = max(0.55, min(2.0, float(hud_scale)))
        base_font = self.font("pingfang", 8.3)
        category_font = self.font("poppins_bold", 6.1)
        gpu_font = self.font("pingfang", 8.3 * scale)
        gpu_category_font = self.font("poppins_bold", 6.1 * scale)
        gpu_index_font = self.font("axiforma_bold", 7.8 * scale)
        metrics = QFontMetricsF(base_font)
        category_metrics = QFontMetricsF(category_font)
        clip = QRectF(
            layout.clip_rect.left() * scale,
            layout.clip_rect.top() * scale,
            layout.clip_rect.width() * scale,
            layout.clip_rect.height() * scale,
        )
        y = layout.anchor_y
        max_rows = max(1, int(layout.available_height / max(1.0, layout.row_height + layout.row_gap)) + 2)
        for index, entry in enumerate(entries[:max_rows]):
            module = entry["module"]
            row_h = 17.0 * float(entry["height"])
            if row_h <= 0.1:
                continue
            category = module.category[:3].upper()
            row_w = max(
                132.0,
                metrics.horizontalAdvance(module.name) + category_metrics.horizontalAdvance(category) + 58.0,
            )
            row_w = min(max(132.0, row_w), max(132.0, width - 36.0))
            rect = QRectF(
                (layout.anchor_x - row_w + float(entry["slide"])) * scale,
                y * scale,
                row_w * scale,
                row_h * scale,
            )
            alpha = max(0.0, min(1.0, float(entry["alpha"])))
            accent = accent_color(index, 0.0)
            icon_w = 21.0 * scale
            body = QRectF(rect.left() + icon_w, rect.top(), rect.width() - icon_w, rect.height())
            commands.rect(
                body,
                5.0 * scale,
                color("#090c12", 0.72 * alpha),
                color("#ffffff", 0.09 * alpha),
                0.8 * scale,
                clip=clip,
            )
            fill_ratio = 0.52 + (index % 5) * 0.075
            commands.rect(
                QRectF(body.left(), body.top(), max(4.0, body.width() * fill_ratio), body.height()),
                5.0 * scale,
                with_alpha(accent, 0.24 * alpha),
                clip=clip,
            )
            badge = QRectF(rect.left(), rect.top(), icon_w, rect.height())
            commands.rect(badge, 5.0 * scale, with_alpha(accent, 0.62 * alpha), color("#ffffff", 0.08 * alpha), 0.6 * scale, clip=clip)
            commands.text(badge, str(index + 1), gpu_index_font, color("#ffffff", alpha), Qt.AlignmentFlag.AlignCenter, clip)
            commands.text(
                QRectF(body.left() + 6.0 * scale, rect.top(), max(2.0, body.width() - 49.0 * scale), rect.height()),
                getattr(self, "module_display_label", lambda m, label: label)(module, hud_module_name(module.name, chinese_enabled(self.registry))),
                gpu_font,
                color("#ffffff", alpha),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                clip,
            )
            commands.text(
                QRectF(rect.right() - 42.0 * scale, rect.top(), 36.0 * scale, rect.height()),
                hud_text(category, chinese_enabled(self.registry)),
                gpu_category_font,
                color("#ffffff", 0.88 * alpha),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                clip,
            )
            y += row_h + 3.0 * float(entry["height"])
            if y > layout.clip_rect.bottom() + 24.0:
                break
        return commands



# ---------------------------------------------------------------------------
# API 冻结面 qualname 固定（见模块 docstring「冻结面纪律」；先例：
# ui/polar/text_metrics.py 底部、renderers/combat_target_hud.py 底部与
# renderers/esp_wireframe.py 底部同一手法）。
# ---------------------------------------------------------------------------

_QUALNAME_PINNED_MEMBERS = (
    ModuleListMixin.module_list_layout,
    ModuleListMixin.draw_minecraft_module_list,
    ModuleListMixin.draw_module_list_hud,
    ModuleListMixin.draw_module_list_fast_rows,
    ModuleListMixin.module_list_preferences,
    ModuleListMixin.module_list_tag,
    ModuleListMixin.module_list_surface,
    ModuleListMixin.module_list_entries,
    ModuleListMixin.debug_tahoma_font,
    ModuleListMixin.debug_tahoma16_font,
    ModuleListMixin.measure_debug_tahoma16,
    ModuleListMixin.arraylist_shadow_image,
    ModuleListMixin.draw_string_with_shadow_mc,
    ModuleListMixin.draw_module_list_debug_white,
    ModuleListMixin.draw_module_list_liquid_array,
    ModuleListMixin.draw_module_list_polar_text,
    ModuleListMixin.draw_module_list_rainbow_right,
    ModuleListMixin.draw_module_list_glow_rows,
    ModuleListMixin.draw_module_list_segmented,
    ModuleListMixin.draw_module_list_dynamic_island,
    ModuleListMixin.draw_module_list_keybind_rows,
    ModuleListMixin.draw_module_list_status_bars,
    ModuleListMixin.draw_module_list_robot_cards,
    ModuleListMixin.draw_keybinds_strip,
    ModuleListMixin.draw_keybinds_hud,
    ModuleListMixin.draw_robot_list_hud,
    ModuleListMixin.draw_status_cards_hud,
    ModuleListMixin.draw_rm_indicators_hud,
    ModuleListMixin.build_gpu_module_list,
)

for _fn in _QUALNAME_PINNED_MEMBERS:
    _fn.__qualname__ = f"PolarRMWindow.{_fn.__name__}"
del _fn, _QUALNAME_PINNED_MEMBERS
