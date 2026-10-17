"""阶段 2d 渲染器提取：战术地图 / 官方地图 / 状态坞（D10）→ TacticalMapMixin。

（REFACTOR_BLUEPRINT §5 阶段 2 表 2d 行 / §2 D10 节方法清单 / §5.6 冻结面 / §6 缓存纪律）

迁移范围（以 grep 重定位后的实际行号为准，蓝图原行号 9104–10863 已因多轮修复
与并行编辑漂移；实际区段为 polar_app.py 7409–9160 的**连续块**，中间无外域方法）：
共 55 个方法——
- 右侧 HUD 模式分发/入口：``draw_right_hud_mode`` / ``queue_gpu_module_list`` /
  ``draw_module_list_for_tactical_map`` / ``draw_match_header``
- 战术地图入口/交互态：``draw_tactical_map_hud`` / ``tactical_map_rect`` /
  ``tactical_map_morph_progress`` / ``tactical_map_hold_amount`` /
  ``publish_map_click_from_screen`` / ``open_event_command_menu``
- chrome/status 助手：``tactical_map_chrome`` / ``tactical_map_status_display`` /
  ``effective_tactical_map_status_display`` / ``tactical_map_status_kind`` /
  ``tactical_map_footer_height`` / ``liquid_status_footer_height``
- 液态地图：``draw_liquid_tactical_map_hud`` / ``official_map_fit_rect`` /
  ``draw_liquid_map_robots`` / ``draw_liquid_mini_status_overlay`` /
  ``draw_liquid_mini_status_chip`` / ``liquid_entity_color`` / ``map_team_color`` /
  ``map_control_color`` / ``draw_liquid_radar_pointer``
- 状态坞：``draw_liquid_status_dock`` / ``draw_liquid_status_row`` /
  ``draw_liquid_status_item`` / ``liquid_status_color`` / ``draw_liquid_status_bar`` /
  ``draw_liquid_status_card``
- 官方地图（含缺陷 D 修复区，零修改迁移）：``draw_official_map`` /
  ``_map_fit_cuts_panel_corners`` / ``_rounded_map_layer`` / ``official_map_image`` /
  ``official_map_display_image`` / ``map_point`` / ``draw_map_zones`` /
  ``draw_map_robots`` / ``draw_map_markers``
- 轨道坞/黑洞盘：``draw_map_status_orbit_dock`` / ``draw_blackhole_disc_row`` /
  ``animated_status_ratio`` / ``draw_blackhole_status_disc`` /
  ``draw_status_label_capsule``
- 状态行数据：``compact_map_status_label`` / ``map_status_groups`` /
  ``map_telemetry_status_rows`` / ``draw_match_status_lines`` /
  ``draw_match_status_stack`` / ``draw_match_status_rings`` /
  ``match_status_ring_rows`` / ``match_status_rows``
- 遥测条：``draw_telemetry_strip`` / ``draw_compact_telemetry_strip``

纪律与偏差决策（中文记录）：
- 方法体**零修改**（含注释与空行），纯移动进本 Mixin；尤其 ``draw_official_map``
  与其两个直接辅助 ``_map_fit_cuts_panel_corners`` / ``_rounded_map_layer`` 是刚
  完成的缺陷 D 修复（含 clip_radius 语义注释），逐字保留；
- ``_map_round_cache`` 是惰性创建字段（方法内 getattr 创建）——保持惰性创建不动，
  **未触碰 PolarRMWindow.__init__**；
- ``queue_gpu_module_list`` / ``draw_module_list_for_tactical_map`` 名字虽含 modlist
  但按蓝图归属 D10（阶段 2c 已确认），本次一并迁入；二者内部经 self 调用的
  ``module_list_layout`` / ``draw_module_list_fast_rows`` 由 ModuleListMixin 继承
  提供（PolarRMWindow MRO 中 TacticalMapMixin 排在其后），调用面不变；
- ``fit_aspect`` / ``format_time`` / ``short_ring_number`` / ``short_outpost_value``
  已在阶段 1 迁至 ui/polar/text_metrics.py，本域方法均经 ``self.`` 调用 → 经
  TextMetricsMixin 继承访问，本模块无需导入这些符号；
- ``open_event_command_menu`` 方法体内使用 QInputDialog 模态对话框（零修改迁移
  所必需）→ 从 PySide6.QtWidgets 显式导入；
- 边界保守决策：``handle_tactical_map_hold_event`` 属 D15 输入/dispatch 域 → 不迁；
  ``liquid_map_enabled`` / ``liquid_map_setting`` / ``liquid_map_bool`` 位于 HUD
  风格助手族（蓝图 §2 归 D9"主题服务"）→ 留在 polar_app.py，本 Mixin 经 self
  继续访问；``_client_asset_path``（D19 共享辅助）仍留在 polar_app.py 原位；
  ``tactical_map_hold`` / ``tactical_map_state`` / ``map_display_cache`` 等
  __init__ 状态 schema 字段全部原位不动（§5.6 冻结面 + §6 缓存纪律）。

冻结面纪律：与阶段 2a/2b/2c 一致——api_freeze_test 对方法类成员比对
``__qualname__``（基线值形如 "PolarRMWindow.<name>"），故在本模块底部把全部
迁入方法的 ``__qualname__`` 钉回 ``PolarRMWindow.<name>``，使 ``window.<name>``
签名与迁移前 bit 级一致。
"""

from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
# 零修改迁移所需：open_event_command_menu 的模态输入对话框。
from PySide6.QtWidgets import QInputDialog

# 相对导入层级：rm_client/ui/polar/renderers/ → .... = rm_client、... = rm_client.ui
from ....match_modes import FieldLayout, MatchModeConfig
from ....model import Module
from ....vision import VisionTarget
from ...animation import Easings
from ...paint import draw_bar, glow_rect, rounded_rect, text_width, with_alpha
from ...theme import THEME, color
from ...jello_locale import chinese_enabled
from ...hud_locale import hud_text, hud_template, hud_buff_name

__all__ = [
    "TacticalMapMixin",
]

# ---------------------------------------------------------------------------
# 模块级渲染助手（刻意不做成 Mixin 方法：api_freeze 冻结 PolarRMWindow 成员面，
# 新增成员会破坏冻结基线；这些助手只被本 Mixin 的既有方法调用）。
# ---------------------------------------------------------------------------

# RadarInfoToClient 断供后标记保留时长；期间线性淡出，超时整体隐藏。
_RADAR_TTL_SEC = 6.0


def _radar_coord_to_m(value: float, span_m: float) -> float:
    """官方雷达坐标为厘米（RMUC 场地 2800x1500cm）；兼容米制输入。"""
    v = float(value)
    return v / 100.0 if v > max(0.1, span_m) + 0.5 else v


def draw_radar_overlay(
    host: Any,
    p: QPainter,
    rect: QRectF,
    field: FieldLayout,
    *,
    compact: bool = False,
    detail_progress: float | None = None,
) -> int:
    """把雷达检测（RadarInfoToClient → state.radar_detections）叠加到小地图。

    - 菱形标记与机器人圆点区分；``is_high_light``（雷达照射/双倍易伤）用
      警告色脉冲环强调；
    - 坐标按厘米→米归一后经 ``host.map_point`` 复用蓝方镜像逻辑；
    - ``state.radar_detections_at`` 驱动 TTL：断供 6s 内线性淡出，之后隐藏；
    - 返回实际绘制的标记数（0 = 无数据/过期，调用方无需特殊处理）。
    """
    detections = getattr(host.state, "radar_detections", None) or []
    if not detections:
        return 0
    seen_at = float(getattr(host.state, "radar_detections_at", 0.0) or 0.0)
    now = time.perf_counter()
    if seen_at <= 0.0:
        return 0
    age = now - seen_at
    if age > _RADAR_TTL_SEC:
        return 0
    detail = (0.0 if compact else 1.0) if detail_progress is None else max(0.0, min(1.0, detail_progress))
    # `RadarOverlay / Map`：经典面板与液态玻璃面板都走这一个门（本函数是
    # 雷达标记的唯一消费者），同一个开关不会“一处有效一处无效”。
    # Both（默认）时永远为 True——画面与接线前逐像素相同。
    if not host.radar_overlay_map_visible(detail):
        return 0
    fade = max(0.0, min(1.0, (_RADAR_TTL_SEC - age) / 2.0))
    base_opacity = p.opacity()
    warn_color = host.hud_warning()
    accent_color = host.hud_accent()
    font = host.font("poppins_medium", 6.2)
    drawn = 0
    for index, det in enumerate(detections):
        try:
            x_m = _radar_coord_to_m(det.get("target_pos_x", 0.0), field.width_m)
            y_m = _radar_coord_to_m(det.get("target_pos_y", 0.0), field.height_m)
            highlighted = (
                int(det.get("is_high_light", 0) or 0) != 0
                and host.radar_high_light_enabled()
            )
        except (TypeError, ValueError):
            continue
        if x_m <= 0.0 and y_m <= 0.0:
            continue
        point = host.map_point(rect, field, x_m, y_m)
        tone = warn_color if highlighted else accent_color
        p.save()
        p.setOpacity(base_opacity * (0.30 + 0.45 * detail) * fade)
        half = 3.4 if compact else 4.4
        diamond = QPolygonF([
            QPointF(point.x(), point.y() - half),
            QPointF(point.x() + half, point.y()),
            QPointF(point.x(), point.y() + half),
            QPointF(point.x() - half, point.y()),
        ])
        p.setPen(QPen(color("#ffffff", 0.80), 1.0))
        p.setBrush(tone)
        p.drawPolygon(diamond)
        if highlighted:
            pulse_r = (7.0 if compact else 9.0) + 3.0 * (0.5 + 0.5 * math.sin(now * 5.0 + index))
            p.setPen(QPen(with_alpha(warn_color, 0.55), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(point, pulse_r, pulse_r)
        p.restore()
        host.add_hotspot(
            QRectF(point.x() - 10, point.y() - 10, 20, 20),
            "radar_blip",
            index,
            tooltip=f"雷达标记 {'· 双倍易伤' if highlighted else '侦测'} ({x_m:.1f}, {y_m:.1f})m",
        )
        drawn += 1
    if drawn:
        legend_w = 52.0 if compact else 62.0
        legend = QRectF(rect.right() - legend_w - 4.0, rect.top() + 4.0, legend_w, 14.0)
        p.save()
        p.setOpacity(base_opacity * (0.55 + 0.35 * detail) * fade)
        rounded_rect(p, legend, 4, color("#000000", 0.30), with_alpha(accent_color, 0.30))
        host.draw_text(p, legend, hud_text(f"RADAR {drawn}", chinese_enabled(host.registry)), font, accent_color, Qt.AlignmentFlag.AlignCenter)
        p.restore()
    return drawn


def draw_penalty_strip(host: Any, p: QPainter, field_rect: QRectF, *, compact: bool = False) -> int:
    """把裁判判罚（PenaltyInfo → state.penalties/active_penalty）画成地图左上胶囊条。

    最新判罚（active_penalty）置前，同类型去重，最多显示 3 条；无数据零占位。
    返回渲染条数。
    """
    penalties = getattr(host.state, "penalties", None) or []
    active = getattr(host.state, "active_penalty", None) or {}
    entries: list[tuple[int, int, int]] = []  # (penalty_type, total_num, effect_sec)

    def _push(item: dict) -> None:
        try:
            ptype = int(item.get("penalty_type", 0))
        except (TypeError, ValueError):
            return
        if any(existing[0] == ptype for existing in entries):
            return
        try:
            total = int(item.get("total_penalty_num", 0) or 0)
            effect = int(item.get("penalty_effect_sec", 0) or 0)
        except (TypeError, ValueError):
            total, effect = 0, 0
        entries.append((ptype, total, effect))

    if active:
        _push(active)
    for item in reversed(penalties):
        _push(item)
        if len(entries) >= 3:
            break
    if not entries:
        return 0

    base_opacity = p.opacity()
    warn_color = host.hud_warning()
    font = host.font("poppins_medium", 6.8)
    y = field_rect.top() + (6.0 if compact else 30.0)
    for ptype, total, effect in entries:
        text = f"判罚 T{ptype}" + (f" ×{total}" if total > 1 else "") + (f" · {effect}s" if effect > 0 else "")
        strip_w = max(64.0, host.text_advance(font, text) + 18.0)
        capsule = QRectF(field_rect.left() + 6.0, y, min(strip_w, field_rect.width() - 12.0), 15.0)
        p.save()
        p.setOpacity(base_opacity * (0.88 if compact else 1.0))
        rounded_rect(p, capsule, 5, with_alpha(warn_color, 0.22), color("#ffffff", 0.12))
        host.draw_text(p, capsule, hud_text(text, chinese_enabled(host.registry)), font, THEME.text, Qt.AlignmentFlag.AlignCenter)
        p.restore()
        host.add_hotspot(capsule, "match_penalty", ptype, tooltip=f"最近判罚：类型 {ptype} · 累计 {total} 次 · 效果 {effect}s")
        y += 18.0
    return len(entries)


class TacticalMapMixin:
    """D10 战术地图 / 官方地图 / 状态坞绘制族 + 右侧 HUD 模式分发。"""

    def draw_right_hud_mode(
        self,
        p: QPainter,
        width: int,
        height: int,
        mode: str,
        target: VisionTarget,
        hud_scale: float = 1.0,
        *,
        draw_module_list: bool = True,
    ) -> None:
        hud = self.registry.get("HUD")
        if draw_module_list and mode == "ModuleListHud" and self.setting_bool(hud, "Module List", True):
            if not self.queue_gpu_module_list(p, width, height, hud_scale):
                self.draw_module_list_hud(p, width, height)
        elif mode == "TacticalMapHud":
            # Map itself is painted on the motion layer; ModuleList co-located
            # with it must also tick on the motion layer for RGB animation.
            if draw_module_list and self.setting_bool(hud, "Module List", True):
                if not self.queue_gpu_module_list(p, width, height, hud_scale):
                    self.draw_module_list_hud(p, width, height)
        elif mode == "KeyBindsHud" and self.setting_bool(hud, "KeyBinds", True):
            self.draw_keybinds_hud(p, width)
        elif mode == "RobotListHud" and self.setting_bool(hud, "Robot List", True):
            self.draw_robot_list_hud(p, width)
        elif mode == "TargetHud" and self.setting_bool(hud, "Target HUD", True):
            self.draw_target_card(p, QRectF(width - 248, 86, 224, 82), target, "TargetHud")
        elif mode == "StatusCardsHud" and self.setting_bool(hud, "Status Cards", True):
            self.draw_status_cards_hud(p, width)
        elif mode == "RMIndicatorsHud" and self.setting_bool(hud, "RM Indicators", True):
            self.draw_rm_indicators_hud(p, width)
        right_list_active = mode in ("ModuleListHud", "TacticalMapHud") and self.setting_bool(hud, "Module List", True)
        if (
            draw_module_list
            and mode != "KeyBindsHud"
            and not right_list_active
            and self.setting_bool(hud, "KeyBinds", True)
        ):
            self.draw_keybinds_strip(p, width, height)

    def queue_gpu_module_list(self, p: QPainter, width: int, height: int, hud_scale: float) -> bool:
        """保留各预设自己的外观，并由静态 UI FBO 缓存最终绘制结果。"""
        # 统一 GPU 快速模板会破坏各套界面各自的行高、配色、标签和
        # 进入动画。原始绘制结果已经缓存到静态 UI FBO，不需要再用错误模板
        # 替换；返回 False 后由对应样式自己的 renderer 绘制。
        return False

    def draw_module_list_for_tactical_map(self, p: QPainter, width: int, height: int) -> None:
        map_rect = self.tactical_map_rect(width, height)
        # BuffTimeline 占的就是地图下面这条带；它在时模块列表必须从带子
        # 下面开始，否则两块直接叠在一起。共用 `buff_timeline_band()`
        # 算高度，不各处自己写数字。
        band = self.buff_timeline_band(width, height)
        below_start = band.bottom() + 14 if band is not None else map_rect.bottom() + 14
        if height - below_start >= 180:
            self.draw_module_list_hud(p, width, height, start_y=below_start)
            return
        left_space_right_edge = int(map_rect.left() - 12)
        if left_space_right_edge >= 420:
            self.draw_module_list_hud(p, left_space_right_edge, height)
            return
        self.draw_module_list_hud(p, width, height, start_y=below_start)

    #: `MatchTimer / Pulse Last 30s` 的“末段”窗口宽度（秒）。
    MATCH_PULSE_WINDOW_S = 30.0

    def match_header_text(self) -> str:
        """拼顶栏文字。`Show Stage` / `Show Scores` 分别控制两段。

        这两个开关以前完全被无视：`draw_match_header` 无条件把阶段、
        倒计时、分数全拼进去。

        抽成纯函数而不是直接写在绘制里：否则“关了就不出现”这种
        行为只能靠看图验证。现在可以直接对字符串做断言。
        """
        config = self.match_mode_manager.current
        parts = [str(config.title)]
        if self._bool_on("MatchTimer", "Show Stage"):
            parts.append(str(self.state.current_stage))
            parts.append(self.format_time(self.state.stage_countdown_sec))
        if self._bool_on("MatchTimer", "Show Scores"):
            if config.key == "RMUL3v3":
                parts.append(
                    f"VP {self.state.ally_victory_points}:{self.state.enemy_victory_points}"
                )
            else:
                parts.append(
                    f"SCORE {self.state.red_score}:{self.state.blue_score}"
                )
        return "  ".join(parts)

    def match_header_pulse_strength(self) -> float:
        """顶栏末段脉冲强度 0..1。关掉 `Pulse Last 30s` 或不在窗口内时为 0。

        方向注意：倒计时是**递减**的，“最后 30 秒”意味着
        `0 <= countdown <= 30`。必须排除负数（超时/未开始会给 0 或负值），
        否则倒计时未走时反而会一直脉冲。
        """
        if not self._bool_on("MatchTimer", "Pulse Last 30s"):
            return 0.0
        module = self.registry.get("MatchTimer")
        if module is None or not module.enabled:
            return 0.0
        try:
            left = float(self.state.stage_countdown_sec)
        except (TypeError, ValueError):
            return 0.0
        if left <= 0.0 or left > self.MATCH_PULSE_WINDOW_S:
            return 0.0
        # 越接近 0 越强；再用正弦做实际闪烁
        closeness = 1.0 - (left / self.MATCH_PULSE_WINDOW_S)
        wave = 0.5 + 0.5 * math.sin(time.perf_counter() * 4.5)
        return max(0.0, min(1.0, closeness * wave))

    def draw_match_header(self, p: QPainter, width: int) -> None:
        text = self.match_header_text()
        font = self.font("poppins_medium", 8.6)
        p.setFont(font)
        rect_w = max(280.0, min(520.0, self.text_advance(font, text) + 44.0))
        hud = self.registry.get("HUD")
        dynamic_enabled = self.setting_bool(hud, "Dynamic Island", True)
        y_offset = self.anim.value("hud.match_header.dynamic_offset", 12.0 if dynamic_enabled else 0.0, self.anim_duration(0.24), Easings.ease_out_expo)
        rect = QRectF(width / 2 - rect_w / 2, 58 + y_offset, rect_w, 24)
        active = self.anim.value("hud.match_header", 1.0, self.anim_duration(0.24), Easings.ease_out_expo, initial=0.0)
        p.save()
        p.setOpacity(active)
        if self.hud_clean():
            self.draw_hud_liquid_glass(p, rect, 12, surface="SubGlass")
        else:
            rounded_rect(p, rect.adjusted(-12, -12, 12, 12), 18, color("#000000", 0.035), None)
            rounded_rect(p, rect, 12, self.hud_surface(0.62), self.hud_panel_outline(0.08))
        progress = max(0.0, min(1.0, self.state.stage_countdown_sec / max(1.0, float(getattr(self.state, "match_duration_sec", 300)))))
        line = QRectF(rect.left() + 12, rect.bottom() - 3.0, rect.width() - 24, 1.2)
        # `Pulse Last 30s` 的真实效果：最后 30 秒内进度条向告警色兼并加粗。
        # 强度为 0 时与原行为一致（同一个 accent 色、同一条宽），
        # 所以关掉开关或不在窗口内时，画面不会发生任何变化。
        pulse = self.match_header_pulse_strength()
        bar_color = self.hud_warning() if pulse > 0.5 else self.hud_accent()
        bar_thickness = 0.6 + 1.4 * pulse
        draw_bar(p, line, progress, bar_color, color("#ffffff", 0.10), bar_thickness)
        self.draw_glass_text(p, rect.adjusted(14, 0, -14, 0), str(self.match_mode_manager.current.title) + hud_text(text[len(str(self.match_mode_manager.current.title)):], chinese_enabled(self.registry)), font, THEME.text, Qt.AlignmentFlag.AlignCenter)
        p.restore()

    #: `RadarOverlay / Map` 的默认档。**必须是 Both**：接线前雷达叠加在
    #: 缩略态与展开态都画（`MapMarker / Send All`、`RobotOverlay / Color`
    #: 的同型问题，同一处理：把默认改成等于今天的实际行为，再用断言钉住）。
    RADAR_MAP_MODE_DEFAULT = "Both"

    #: 展开判定用 `draw_radar_overlay` 自己已经在算的 detail 插值：
    #: 0=缩略、1=长按展开。取 0.5 作拐点，两处面板共用一个真相源，
    #: 不在两个渲染路径里各猜一套坐标阈值。
    RADAR_MAP_FULL_EXPANSION = 0.5

    #: 上一帧实际生效的档位与降级原因，供诊断/HUD 读取（与 autoaim_priority_* 同型）。
    radar_map_actual: str = "Both"
    radar_map_reason: str = ""

    def radar_overlay_map_visible(self, detail: float) -> bool:
        """`RadarOverlay / Map`：雷达叠加要不要画进当前形态的地图。

        取值语义（Both 与接线前逐值相同）：
          * Both   —— 缩略/展开都画（默认 = 现行画面）；
          * Mini   —— 只画缩略态；
          * Full   —— 只画长按展开态；
          * Hidden —— 都不画（显示开关，不影响数据接收与 TTL）。

        模块查不到、设置项缺失或值不认识：一律回落 Both 并记下原因——
        “显示开关出错”不该演变成“情报凭空消失”。
        """
        expanded = detail >= self.RADAR_MAP_FULL_EXPANSION
        module = self.registry.get("RadarOverlay")
        wanted = ""
        if module is not None:
            setting = module.get_setting("Map")
            wanted = str(getattr(setting, "value", "") or "").strip() if setting is not None else ""
        if wanted == "Hidden":
            self.radar_map_actual, self.radar_map_reason = "Hidden", ""
            return False
        if wanted == "Mini":
            self.radar_map_actual, self.radar_map_reason = "Mini", ""
            return not expanded
        if wanted == "Full":
            self.radar_map_actual, self.radar_map_reason = "Full", ""
            return expanded
        if wanted not in ("", "Both"):
            self.radar_map_actual = self.RADAR_MAP_MODE_DEFAULT
            self.radar_map_reason = f"unknown map mode {wanted!r}"
        else:
            self.radar_map_actual = self.RADAR_MAP_MODE_DEFAULT
            self.radar_map_reason = ""
        return True

    def radar_high_light_enabled(self) -> bool:
        """`RadarOverlay / High Light`：要不要把“雷达照射 / 双倍易伤”标出来。

        这个开关以前没人读：只要报文里 `is_high_light` 置位，标记就一定
        换成告警色并画脉冲环，完全关不掉。

        关掉后是**取消高亮强调**（回到普通色、不画脉冲环），而不是把
        标记删掉：数据本身仍应该可见。否则一个“显示样式”开关会变成
        “隐藏情报”，那是两回事。

        查不到模块或设置项时返回 True（保持现行为）。
        """
        module = self.registry.get("RadarOverlay")
        if module is None:
            return True
        setting = module.get_setting("High Light")
        if setting is None:
            return True
        return bool(getattr(setting, "value", True))

    #: BuffTimeline 的消费者落在“战术地图面板下方”这条实测出来的空带上
    #: （1600x900 / 1920x1080 / 1280x720 下地图底部到屏幕底部都有 535px 以上余量）。
    #: 模块列表在同一条带上有自己的落点（右侧放不下时才用带下方），
    #: 所以两处都走 `buff_timeline_band()` 这一个真相源，重叠在结构上不可能。
    BUFF_TIMELINE_BAND_HEIGHT = 22.0
    BUFF_TIMELINE_GAP = 6.0
    BUFF_TIMELINE_MAX_ITEMS = 4

    def buff_timeline_rows(self) -> list[tuple[str, str, int]]:
        """要显示的标签：`(类别, 名称, 剩余秒数)`。三个开关各管一件事。

        * `Buffs` → 本机增益行；
        * `Special Mechanism` → 全局特殊机制行；
        * `Timeline` → 名称后面带不带剩余秒数（由绘制层读）。

        只拿**真实且属于本机**的数据：
          * LabDebug 的 mock 行没有 `buff_type`，直接过滤掉——否则实验室
            演示会凭空多出一排假标签；
          * `robot_id` 不是本机的不显示（与 `buff_indicator_state()` 同一口径），
            绝不把队友/敌方增益显示成本机状态。

        文本全用 ASCII：HUD 字体没有 CJK 字形（仓库内已有同样的处理注释）。
        """
        module = self.registry.get("BuffTimeline")
        if module is None or not module.enabled:
            return []
        rows: list[tuple[str, str, int]] = []
        if self._bool_on("BuffTimeline", "Buffs"):
            try:
                own_id = int(getattr(self.state, "robot_id", 0) or 0)
            except (TypeError, ValueError):
                own_id = 0
            for row in getattr(self.state, "buff_rows", ()) or ():
                if row.get("buff_type") is None:
                    continue
                try:
                    if int(row.get("robot_id", 0) or 0) != own_id:
                        continue
                except (TypeError, ValueError):
                    continue
                if not row.get("active"):
                    continue
                name = str(row.get("name") or f"Buff {row.get('buff_type')}")
                try:
                    remaining = max(0, int(row.get("remaining", 0) or 0))
                except (TypeError, ValueError):
                    remaining = 0
                rows.append(("buff", name, remaining))
        if self._bool_on("BuffTimeline", "Special Mechanism"):
            for item in getattr(self.state, "special_mechanisms", ()) or ():
                try:
                    mechanism_id = int(item.get("mechanism_id", 0) or 0)
                    seconds = max(0, int(item.get("time_sec", 0) or 0))
                except (AttributeError, TypeError, ValueError):
                    continue
                if mechanism_id <= 0:
                    continue
                rows.append(("mechanism", f"MECH{mechanism_id}", seconds))
        return rows[: self.BUFF_TIMELINE_MAX_ITEMS]

    def buff_timeline_band(self, width: int, height: int) -> QRectF | None:
        """标签带到底占哪一块；没内容时返回 None。

        没内容就**不占位也不拉高任何面板**，所以默认画面与接线前完全一致：
        只有裁判系统真的推了本机增益 / 特殊机制，这条带才会出现。
        """
        if not self.buff_timeline_rows():
            return None
        tactical = self.registry.get("TacticalMap")
        if tactical is not None and not tactical.enabled:
            return None          # 地图没画，挂在地图下面的带也不能凭空浮在视频上
        map_rect = self.tactical_map_rect(width, height)
        return QRectF(
            map_rect.left(),
            map_rect.bottom() + self.BUFF_TIMELINE_GAP,
            map_rect.width(),
            self.BUFF_TIMELINE_BAND_HEIGHT,
        )

    def draw_buff_timeline_hud(self, p: QPainter, width: int, height: int) -> None:
        """`BuffTimeline` 的三个开关在此落地。

        这个模块以前**没有任何消费者**：界面上开了它、三个子开关全勾着，
        屏幕上什么都不会多出来——增益与特殊机制的时间其实已经解码到了
        `state.buff_rows` / `state.special_mechanisms`，只是没人读。

        这里只做“把已有数据标出来”，不发明比例条：裁判报文只给剩余秒数、
        不给总时长，拿一个猜出来的满量程画条就是在骗人。所以 `Timeline`
        的含义定为“标签上带不带剩余秒数”。
        """
        band = self.buff_timeline_band(width, height)
        if band is None:
            return
        show_time = self._bool_on("BuffTimeline", "Timeline")
        font = self.font("poppins_medium", 7.4)
        accent = self.hud_accent()
        x = band.left()
        for kind, name, remaining in self.buff_timeline_rows():
            # Minecraft effects replace the legacy buff chips only while that
            # preset is active. Global mechanism notices and normal HUD stay.
            mc_effects = self.registry.get("MinecraftBuffs")
            if kind == "buff" and mc_effects and mc_effects.enabled and getattr(self, "minecraft_hud_active", lambda: False)():
                continue
            text = f"{name} {remaining}s" if show_time and remaining > 0 else name
            chip_w = max(28.0, self.text_advance(font, text) + 12.0)
            if x + chip_w > band.right():
                break                      # 放不下就截断，不画到边界外
            rect = QRectF(x, band.top(), chip_w, band.height())
            tint = accent if kind == "buff" else self.hud_warning()
            rounded_rect(p, rect, 4, color("#000000", 0.30), with_alpha(tint, 0.30))
            self.draw_text(p, rect, (f"{hud_buff_name(name, chinese_enabled(self.registry))} {remaining}s" if show_time and remaining > 0 else hud_buff_name(name, chinese_enabled(self.registry))), font, tint, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(
                rect, "buff_timeline", f"{kind}:{name}",
                tooltip=f"{'本机增益' if kind == 'buff' else '特殊机制'}：{hud_buff_name(name, chinese_enabled(self.registry))} 剩余 {remaining}s",
            )
            x += chip_w + 4.0

    def draw_tactical_map_hud(self, p: QPainter, width: int, height: int) -> None:
        module = self.registry.get("TacticalMap")
        if module is not None and not module.enabled:
            return
        config = self.match_mode_manager.current
        rect = self.tactical_map_rect(width, height)
        if self.liquid_map_enabled():
            self.draw_liquid_tactical_map_hud(p, width, height, module, config, rect)
            return
        chrome = self.tactical_map_chrome()
        hold_amount = self.tactical_map_hold_amount()
        minimal = chrome == "Minimal" and self.hud_density() == "CombatCompact" and hold_amount < 0.08
        thin = chrome == "Thin" or (chrome == "Minimal" and hold_amount >= 0.08)
        active = self.anim.value("hud.tactical_map", 1.0, self.anim_duration(0.18), Easings.ease_out_expo, initial=0.0)
        slide = (1.0 - active) * 20.0
        rect = rect.translated(slide, 0)
        p.save()
        p.setOpacity(active)
        scale = 0.985 + 0.015 * active
        p.translate(rect.center())
        p.scale(scale, scale)
        p.translate(-rect.center())
        if not minimal:
            glow_rect(p, rect, 11, 0.04 if self.hud_clean() else 0.08 if thin else 0.17)
        rounded_rect(p, rect, 6 if minimal else 8, self.hud_surface(0.50 if minimal else 0.64 if thin else 0.68), self.hud_panel_outline(0.06 if minimal else 0.09))
        top_line = QLinearGradient(rect.left() + 10, rect.top() + 4, rect.right() - 10, rect.top() + 4)
        top_line.setColorAt(0.0, with_alpha(self.hud_accent(), 0.54 if minimal else 0.78))
        top_line.setColorAt(1.0, with_alpha(self.hud_accent(), 0.76 if minimal else 0.96))
        line_reveal = self.anim.value("hud.tactical_map.line_reveal", 1.0, self.anim_duration(0.18), Easings.ease_out_expo, initial=0.0)
        p.fillRect(QRectF(rect.left() + 10, rect.top() + 4, (rect.width() - 20) * line_reveal, 1.4 if minimal else 2.0 if thin else 2.3), top_line)
        title = f"{config.title} Tactical Map"
        compact = minimal or rect.height() < 220.0
        status_kind = self.tactical_map_status_kind(minimal)
        header_h = 8.0 if minimal else 24.0 if thin else 30.0 if compact else 44.0
        footer_h = self.tactical_map_footer_height(status_kind, minimal, thin, compact)
        detail_alpha = self.anim.value("hud.tactical_map.detail_alpha", 0.0 if minimal else 1.0, self.anim_duration(0.14), Easings.ease_out_expo)
        p.save()
        p.setClipRect(rect.adjusted(1.0, 1.0, -1.0, -1.0))
        if not minimal:
            p.save()
            p.setOpacity(p.opacity() * detail_alpha)
            self.draw_text(p, QRectF(rect.left() + 12, rect.top() + 8, rect.width() - 24, 14), hud_template("{title} Tactical Map", chinese_enabled(self.registry), title=config.title), self.font("poppins_bold", 7.0 if compact else 8.4), THEME.text)
            p.restore()
        if not compact and not thin:
            p.save()
            p.setOpacity(p.opacity() * detail_alpha)
            self.draw_text(
                p,
                QRectF(rect.left() + 12, rect.top() + 27, rect.width() - 24, 14),
                hud_template("{width}m x {height}m official field", chinese_enabled(self.registry), width=f"{config.field.width_m:g}", height=f"{config.field.height_m:g}"),
                self.font("poppins_regular", 7.2),
                THEME.muted,
            )
            p.restore()
        map_rect = rect.adjusted(6 if minimal else 8 if compact else 10, header_h, -6 if minimal else -8 if compact else -10, -footer_h)
        field_rect = self.draw_official_map(p, map_rect, config)
        self.tactical_map_field_rect = QRectF(field_rect)
        self.tactical_map_field_layout = config.field
        alliance_profile = self.registry.get("AllianceProfile")
        self.tactical_map_field_mirrored = bool(
            config.field.mirror_for_blue
            and self.alliance_side() == "Blue"
            and self.setting_bool(module, "Mirror Blue", True)
            and self.setting_bool(alliance_profile, "Mirror Tactical Map", True)
        )
        map_content_opacity = self.anim.value("hud.tactical_map.content_alpha", 1.0, self.anim_duration(0.16), Easings.ease_out_expo, initial=0.0)
        p.save()
        p.setOpacity(p.opacity() * map_content_opacity)
        self.draw_map_zones(p, field_rect, config.field, compact=compact)
        if self.setting_bool(module, "Map Marks", True):
            self.draw_map_markers(p, field_rect, config.field, compact=compact)
        draw_radar_overlay(self, p, field_rect, config.field, compact=compact)
        if self.setting_bool(module, "Robot Dots", True):
            self.draw_map_robots(p, field_rect, config.field, compact=compact)
        draw_penalty_strip(self, p, field_rect, compact=compact)
        p.restore()
        if not compact:
            title_chip = QRectF(field_rect.left() + 6, field_rect.top() + 6, min(188.0, field_rect.width() - 12), 20)
            p.save()
            p.setOpacity(p.opacity() * detail_alpha)
            rounded_rect(p, title_chip, 6, self.hud_surface(0.72), self.hud_panel_outline(0.10))
            self.draw_text(p, title_chip.adjusted(7, 0, -7, 0), hud_template("{title} official map", chinese_enabled(self.registry), title=config.title), self.font("poppins_medium", 6.9), THEME.text)
            p.restore()
        p.restore()
        stack_top = field_rect.bottom() + (3 if minimal else 5 if compact else 8)
        if status_kind == "orbit":
            self.draw_map_status_orbit_dock(p, rect, field_rect, width, minimal=minimal, compact=compact)
        elif status_kind == "lines":
            self.draw_match_status_lines(p, QRectF(field_rect.left(), stack_top, field_rect.width(), max(8.0, rect.bottom() - 5.0 - stack_top)))
        elif status_kind == "rings":
            ring_h = max(30.0 if compact else 42.0, min(56.0, rect.bottom() - 7.0 - stack_top))
            self.draw_match_status_rings(p, QRectF(field_rect.left(), stack_top, field_rect.width(), ring_h), compact=compact)
        elif status_kind == "bars":
            stack_h = max(20.0 if thin else 32.0 if compact else 42.0, min(58.0, rect.bottom() - 8.0 - stack_top))
            self.draw_match_status_stack(p, QRectF(field_rect.left(), stack_top, field_rect.width(), stack_h), compact=compact)
        p.restore()

    def tactical_map_rect(self, width: int, height: int) -> QRectF:
        module = self.registry.get("TacticalMap")
        setting = module.get_setting("Map Size") if module else None
        mode = str(setting.value) if setting else "Wide"
        density = self.hud_density()
        # CombatCompact 始终以缩略尺寸作为 morph 起点。不能在按键按下时
        # 先切换 base size，否则会在连续动画开始前产生一次明显跳变。
        if self.minecraft_hud_active():
            # MC composition owns a dedicated upper-left safe area.  Do not
            # mutate Map Size: leaving the preset restores the user's exact
            # POLAR geometry on the next frame.
            base_w = min(216.0, max(168.0, width * 0.19))
            base_h = min(154.0, max(116.0, height * 0.19))
        elif density == "CombatCompact":
            base_w, base_h = 170.0, 118.0
        elif mode == "Compact":
            base_w, base_h = 220.0, 170.0
        elif mode == "Full Tactical":
            base_w, base_h = 360.0, 270.0
        else:
            base_w, base_h = 280.0, 212.0
        expanded_w = min(520.0, width * 0.48)
        expanded_h = min(380.0, height * 0.56)
        target_w = expanded_w if self.tactical_map_hold else base_w
        target_h = expanded_h if self.tactical_map_hold else base_h
        target_w = min(target_w, max(190.0, width - 36.0))
        target_h = min(target_h, max(150.0, height - 96.0))
        map_y = 48.0 if self.minecraft_hud_active() else 58.0
        self.tactical_map_state.set_target(12.0, map_y, target_w, target_h, self.tactical_map_hold)
        state = self.tactical_map_state.container
        return QRectF(state.x, state.y, state.width, state.height)

    def tactical_map_morph_progress(self) -> float:
        """Tab 展开、底图、标题和状态区共享的唯一可逆进度。"""
        return self.tactical_map_state.progress

    def tactical_map_hold_amount(self) -> float:
        return self.tactical_map_morph_progress()

    #: `Mark Type` 选项 -> MapClickCmd 字段 3（type）的取值。
    #: 按选项顺序 1..4 映射；默认 "Attack"=1，与接线前硬写的 mark_type=1 一致，
    #: 字段 6 是 mode（此处固定为 1，地图标记）；后端按官方字段序编码。
    MAP_MARKER_TYPE_CODES: dict[str, int] = {
        "Attack": 1,
        "Defense": 2,
        "Warning": 3,
        "Custom": 4,
    }

    def map_click_command_values(self) -> tuple[int, int]:
        """返回本次地图标记要用的 (is_send_all, mark_type)。

        取不到模块或设置项时，回落到接线前那对硬编码值 (1, 1)，
        保证不会因为读设置失败而改变外发内容。
        """
        module = self.registry.get("MapMarker")
        if module is None:
            return 1, 1
        send_all_setting = module.get_setting("Send All")
        type_setting = module.get_setting("Mark Type")
        is_send_all = 1 if (
            send_all_setting is None or bool(getattr(send_all_setting, "value", True))
        ) else 0
        label = str(getattr(type_setting, "value", "Attack") if type_setting else "Attack")
        return is_send_all, self.MAP_MARKER_TYPE_CODES.get(label.strip(), 1)

    def publish_map_click_from_screen(self, point: QPointF) -> bool:
        """Send an official MapClickCmd for a click inside the expanded map.

        Only active while the Tab hold has the tactical map expanded; during
        normal combat every left click belongs to KMC and must not leak a
        referee event command. The backend safety layer still enforces
        MATCH_ARMED capability and a conservative three-second mark interval.
        """

        if not self.tactical_map_hold:
            return False
        field = self.tactical_map_field_layout
        rect = self.tactical_map_field_rect
        if field is None or rect.isNull() or not rect.contains(point):
            return False
        nx = (point.x() - rect.left()) / max(1.0, rect.width())
        ny = (rect.bottom() - point.y()) / max(1.0, rect.height())
        if self.tactical_map_field_mirrored:
            nx, ny = 1.0 - nx, 1.0 - ny
        map_x = max(0.0, min(1.0, nx)) * field.width_m
        map_y = max(0.0, min(1.0, ny)) * field.height_m
        is_send_all, mark_type = self.map_click_command_values()
        ok, reason = self.backend.publish_map_click_cmd(
            is_send_all=is_send_all,
            mode=1,
            mark_type=mark_type,
            map_x=round(map_x, 3),
            map_y=round(map_y, 3),
        )
        self.toast(f"Map click ({map_x:.1f}, {map_y:.1f})m {'sent' if ok else reason}")
        return True

    def open_event_command_menu(self) -> None:
        """Dialog launcher for referee event commands without a dedicated HUD.

        Everything funnels through the backend publishers, so MATCH_ARMED
        capability checks, rate limits, and mock gating still apply.
        """

        commands = (
            "Rune Activate (energy mechanism)",
            "Dart Command",
            "Sentry Control",
            "Assembly Command",
            "Performance Selection (from RobotProfile)",
        )
        chinese = chinese_enabled(self.registry)
        display_commands = tuple(hud_text(value, chinese) for value in commands)
        choice, ok = QInputDialog.getItem(
            self, hud_text("Event commands", chinese), hud_text("Referee event command:", chinese), display_commands, 0, False
        )
        if not ok or choice not in display_commands:
            return
        # Only the displayed choice is localized; dispatch uses the original key.
        choice = commands[display_commands.index(choice)]
        try:
            if choice.startswith("Rune"):
                value, accepted = QInputDialog.getItem(
                    self, hud_text("RuneActivateCommand", chinese), hud_text("Activate value:", chinese), [hud_text("1 = activate", chinese), hud_text("0 = cancel", chinese)], 0, False
                )
                if accepted:
                    activate = 1 if value.startswith("1") else 0
                    ok_pub, reason = self.backend.publish_rune_activate(activate)
                    self.toast(f"Rune {'sent' if ok_pub else reason}")
            elif choice.startswith("Dart"):
                target, accepted = QInputDialog.getInt(self, hud_text("DartCommand", chinese), hud_text("Target robot ID:", chinese), 1, 1, 209)
                if accepted:
                    open_gate, accepted2 = QInputDialog.getItem(
                        self, hud_text("DartCommand", chinese), hud_text("Open gate:", chinese), [hud_text("False", chinese), hud_text("True", chinese)], 0, False
                    )
                    if accepted2:
                        ok_pub, reason = self.backend.publish_dart_command(
                            target, open_gate == hud_text("True", chinese), False
                        )
                        self.toast(f"Dart {'sent' if ok_pub else reason}")
            elif choice.startswith("Sentry"):
                command_id, accepted = QInputDialog.getInt(
                    self, hud_text("SentryCtrlCommand", chinese), hud_text("Command ID (consult the selected referee protocol):", chinese), 1, 0, 2
                )
                if accepted:
                    ok_pub, reason = self.backend.publish_sentry_command(command_id)
                    self.toast(f"Sentry {'sent' if ok_pub else reason}")
            elif choice.startswith("Assembly"):
                operation, accepted = QInputDialog.getInt(
                    self, hud_text("AssemblyCommand", chinese), hud_text("Operation (1=exchange ore,2=submit,3=stash):", chinese), 1, 1, 3
                )
                if accepted:
                    difficulty, accepted2 = QInputDialog.getInt(
                        self, hud_text("AssemblyCommand", chinese), hud_text("Difficulty (1-3):", chinese), 1, 1, 3
                    )
                    if accepted2:
                        ok_pub, reason = self.backend.publish_assembly_command(operation, difficulty)
                        self.toast(f"Assembly {'sent' if ok_pub else reason}")
            elif choice.startswith("Performance"):
                ok_pub, reason = self.backend.publish_performance_from_registry(self.registry)
                self.toast(f"Performance selection {'sent' if ok_pub else reason}")
        except Exception as exc:
            self.toast(f"Event command failed: {exc}")


    def tactical_map_chrome(self) -> str:
        module = self.registry.get("TacticalMap")
        setting = module.get_setting("Chrome") if module else None
        value = str(setting.value) if setting else "Minimal"
        if self.hud_density() == "DebugFull":
            return "Full"
        return value if value in ("Minimal", "Thin", "Full") else "Minimal"

    def tactical_map_status_display(self) -> str:
        module = self.registry.get("TacticalMap")
        setting = module.get_setting("Status Display") if module else None
        value = str(setting.value) if setting else "LiquidAuto"
        value = {
            "Auto": "LiquidAuto",
            "Rings": "LegacyOrbit",
            "Bars": "LegacyBars",
        }.get(value, value)
        valid = (
            "LiquidAuto", "LiquidBars", "LiquidCards",
            "LegacyOrbit", "LegacyBars", "Off",
        )
        return value if value in valid else "LiquidAuto"

    def effective_tactical_map_status_display(self) -> str:
        """Return the render-only map status policy for the active preset."""

        return "Off" if self.minecraft_hud_active() else self.tactical_map_status_display()

    def tactical_map_status_kind(self, minimal: bool) -> str:
        value = self.effective_tactical_map_status_display()
        if value == "Off":
            return "off"
        if value == "LegacyBars":
            return "bars"
        if value == "LegacyOrbit":
            return "orbit"
        return "orbit"

    def tactical_map_footer_height(self, status_kind: str, minimal: bool, thin: bool, compact: bool) -> float:
        if status_kind == "off":
            return 4.0 if minimal else 8.0
        if status_kind == "orbit":
            return 48.0 if minimal else 62.0 if compact else 66.0
        if status_kind == "lines":
            return 14.0 if minimal else 18.0
        if status_kind == "rings":
            return 38.0 if minimal else 46.0 if compact else 50.0 if thin else 56.0
        return 14.0 if minimal else 24.0 if thin else 38.0 if compact else 76.0

    def liquid_status_footer_height(self, display: str, minimal: bool, compact: bool) -> float:
        if display == "Off":
            return 5.0 if minimal else 9.0
        if display == "LegacyOrbit":
            return 48.0 if minimal else 62.0 if compact else 66.0
        if display == "LegacyBars":
            return 18.0 if minimal else 28.0 if compact else 44.0
        if display == "LiquidCards":
            return 38.0 if minimal else 54.0 if compact else 60.0
        return 32.0 if minimal else 44.0 if compact else 48.0

    def draw_liquid_tactical_map_hud(
        self,
        p: QPainter,
        width: int,
        height: int,
        module: Module | None,
        config: MatchModeConfig,
        rect: QRectF,
    ) -> None:
        hold_amount = self.tactical_map_hold_amount()
        compact_density = self.hud_density() == "CombatCompact" or self.minecraft_hud_active()
        compact = compact_density
        # Base/outpost and COOL/PWR already have dedicated MC surfaces.  Keep
        # the tactical map as a map instead of repeating those cards in the
        # upper-left; the configured status display returns unchanged as soon
        # as the MC preset is left.
        display = self.effective_tactical_map_status_display()
        active = self.anim.value("hud.tactical_map.liquid", 1.0, self.anim_duration(0.17), Easings.ease_out_expo, initial=0.0)
        slide = (1.0 - active) * 16.0
        rect = rect.translated(slide, 0)
        p.save()
        p.setOpacity(p.opacity() * active)
        scale = 0.992 + 0.008 * active
        p.translate(rect.center())
        p.scale(scale, scale)
        p.translate(-rect.center())

        radius = 5.0
        if self.hud_clean():
            self.draw_hud_liquid_glass(p, rect, radius, surface="MainGlass")
        else:
            rounded_rect(p, rect, radius, color("#000000", 0.72), color("#ffffff", 0.105 if compact_density else 0.085), 0.9)
        left_line = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        left_line.setColorAt(0.0, with_alpha(self.hud_accent(), 0.70))
        left_line.setColorAt(1.0, with_alpha(self.hud_accent(), 0.28))
        p.fillRect(QRectF(rect.left(), rect.top() + 6.0, 2.0, rect.height() - 12.0), left_line)

        title = f"{config.title} Tactical Map"
        collapsed_header = 13.0 if compact_density else (22.0 if compact else 27.0)
        header_h = collapsed_header + (27.0 - collapsed_header) * hold_amount
        collapsed_footer = self.liquid_status_footer_height(display, compact_density, True)
        expanded_footer = self.liquid_status_footer_height(display, False, False)
        footer_h = collapsed_footer + (expanded_footer - collapsed_footer) * hold_amount
        collapsed_title_size = 6.0 if compact_density else 7.2
        title_font = self.font("poppins_medium", collapsed_title_size + (8.0 - collapsed_title_size) * hold_amount)
        title_rect = QRectF(rect.left() + 8.0, rect.top() + 2.0, rect.width() - 16.0, header_h - 2.0)
        self.draw_glass_text(p, title_rect, hud_template("{title} Tactical Map", chinese_enabled(self.registry), title=config.title), title_font, THEME.text)

        map_rect = rect.adjusted(6.0, header_h, -6.0, -footer_h)
        p.save()
        p.setClipRect(rect.adjusted(1.0, 1.0, -1.0, -1.0))
        field_rect = self.draw_official_map(p, map_rect, config)
        content_alpha = self.anim.value("hud.tactical_map.liquid.content", 1.0, self.anim_duration(0.20), Easings.ease_out_cubic, initial=0.0)
        p.save()
        p.setOpacity(p.opacity() * content_alpha)
        self.draw_map_zones(p, field_rect, config.field, compact=compact, detail_progress=hold_amount)
        if self.setting_bool(module, "Map Marks", True):
            self.draw_map_markers(p, field_rect, config.field, compact=compact, detail_progress=hold_amount)
        draw_radar_overlay(self, p, field_rect, config.field, compact=compact, detail_progress=hold_amount)
        if self.setting_bool(module, "Robot Dots", True):
            self.draw_liquid_map_robots(p, field_rect, config.field, compact=compact, detail_progress=hold_amount)
        draw_penalty_strip(self, p, field_rect, compact=compact)
        p.restore()
        p.restore()

        if display == "LegacyOrbit":
            self.draw_map_status_orbit_dock(p, rect, field_rect, width, minimal=compact_density, compact=compact)
        elif display == "LegacyBars":
            stack_top = field_rect.bottom() + 3.0 + 3.0 * hold_amount
            stack_h = max(20.0 if compact else 34.0, min(54.0, rect.bottom() - 6.0 - stack_top))
            self.draw_match_status_stack(p, QRectF(field_rect.left(), stack_top, field_rect.width(), stack_h), compact=compact)
        elif display != "Off":
            self.draw_liquid_status_dock(
                p,
                rect,
                field_rect,
                width,
                display,
                minimal=compact_density,
                compact=compact,
                morph_progress=hold_amount,
            )
        p.restore()

    def official_map_fit_rect(self, rect: QRectF, config: MatchModeConfig) -> QRectF:
        image = self.official_map_image(config)
        if image is None or image.isNull():
            aspect = config.field.width_m / max(0.1, config.field.height_m)
        else:
            aspect = image.width() / max(1.0, image.height())
        return self.fit_aspect(rect, aspect)

    def draw_liquid_map_robots(
        self,
        p: QPainter,
        rect: QRectF,
        field: FieldLayout,
        compact: bool = False,
        detail_progress: float | None = None,
    ) -> None:
        robots = list(self.state.map_robots)
        if not robots:
            return
        own = next((robot for robot in robots if int(robot.get("id", -1)) == int(self.state.robot_id)), None)
        own_x = float(own.get("x", field.width_m * 0.5)) if own else field.width_m * 0.5
        own_y = float(own.get("y", field.height_m * 0.5)) if own else field.height_m * 0.5
        own_point = self.map_point(rect, field, own_x, own_y)
        style = self.liquid_map_setting("Pointer Style", "Triangle")
        show_labels = self.liquid_map_bool("Show Labels", True)
        show_values = self.liquid_map_bool("Show Values", True)
        color_mode = self.liquid_map_setting("Entity Color", "Team")
        max_distance = max(1.0, math.hypot(field.width_m, field.height_m))
        now = time.perf_counter()
        detail = (0.0 if compact else 1.0) if detail_progress is None else max(0.0, min(1.0, detail_progress))
        font = self.font("poppins_medium", 5.8 + 0.9 * detail)

        for index, robot in enumerate(robots):
            x_m = float(robot.get("x", 0.0))
            y_m = float(robot.get("y", 0.0))
            point = self.map_point(rect, field, x_m, y_m)
            team = str(robot.get("team", "ally"))
            hp = int(robot.get("hp", 0))
            hp_max = max(1, int(robot.get("max", 1)))
            hp_ratio = max(0.0, min(1.0, hp / hp_max))
            distance = math.hypot(x_m - own_x, y_m - own_y)
            distance_ratio = max(0.0, min(1.0, distance / max_distance))
            alpha = 0.42 + (1.0 - distance_ratio) * 0.50
            base = self.liquid_entity_color(team, hp_ratio, distance_ratio, color_mode)
            highlight = bool(robot.get("highlight", False))
            if highlight and self.setting_bool(self.registry.get("TacticalMap"), "Radar Pulse", True):
                pulse_r = (7.0 + 2.0 * detail) + (3.0 + 1.6 * detail) * (0.5 + 0.5 * math.sin(now * 4.4 + index))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(with_alpha(base, 0.28 + 0.16 * math.sin(now * 3.2)), 1.0))
                p.drawEllipse(point, pulse_r, pulse_r)
            angle = math.degrees(math.atan2(point.y() - own_point.y(), point.x() - own_point.x())) + 90.0
            size = 5.0 + 1.6 * detail
            self.draw_liquid_radar_pointer(p, point, angle, with_alpha(base, alpha), style, size, highlight)
            if show_labels and (detail > 0.001 or highlight):
                label = str(robot.get("id", "?"))
                show_distance = show_values and (detail > 0.35 or team == "enemy" or distance >= 1.0)
                value = f"{int(distance)}m" if show_distance else ""
                text = f"{label} {value}".strip()
                label_rect = QRectF(point.x() + 6.0, point.y() - 8.0, 44.0, 12.0)
                p.save()
                p.setOpacity(p.opacity() * max(detail, 0.72 if highlight else 0.0))
                self.draw_text(p, label_rect.translated(0.5, 0.7), text, font, color("#000000", 0.72))
                self.draw_text(p, label_rect, text, font, THEME.text)
                p.restore()
            if show_values:
                bar_w = 15.0 + 6.0 * detail
                draw_bar(
                    p,
                    QRectF(point.x() - bar_w * 0.5, point.y() + 5.0 + 2.0 * detail, bar_w, 2.0),
                    hp_ratio,
                    with_alpha(base, 0.82),
                    color("#ffffff", 0.12),
                    1.0,
                )
            tooltip = f"{robot.get('name', hud_text('Robot', chinese_enabled(self.registry)))} {robot.get('id', '?')} | {hp}/{hp_max} {hud_text('HP', chinese_enabled(self.registry))} | {distance:.1f}m"
            self.add_hotspot(QRectF(point.x() - 11.0, point.y() - 11.0, 22.0, 22.0), "map_robot", robot.get("id"), tooltip=tooltip)

    def draw_liquid_mini_status_overlay(self, p: QPainter, field_rect: QRectF, display: str) -> None:
        match_rows, telemetry_rows = self.map_status_groups()
        match_rows = match_rows[:3]
        telemetry_rows = telemetry_rows[:2]
        if not match_rows and not telemetry_rows:
            return
        bottom_h = 16.0
        bottom_gap = 4.0
        if match_rows:
            total_gap = bottom_gap * max(0, len(match_rows) - 1)
            slot_w = (field_rect.width() - 10.0 - total_gap) / len(match_rows)
            y = field_rect.bottom() - bottom_h - 4.0
            for index, row in enumerate(match_rows):
                x = field_rect.left() + 5.0 + index * (slot_w + bottom_gap)
                self.draw_liquid_mini_status_chip(p, QRectF(x, y, slot_w, bottom_h), row, f"mini.match.{index}", display)
        if telemetry_rows:
            side_w = min(54.0, max(38.0, field_rect.width() * 0.30))
            y = field_rect.top() + 5.0
            for index, row in enumerate(telemetry_rows):
                rect = QRectF(field_rect.right() - side_w - 5.0, y + index * (bottom_h + 4.0), side_w, bottom_h)
                self.draw_liquid_mini_status_chip(p, rect, row, f"mini.telemetry.{index}", display)

    def draw_liquid_mini_status_chip(
        self,
        p: QPainter,
        rect: QRectF,
        row: tuple[str, str, float, QColor, str, str],
        key_prefix: str,
        display: str,
    ) -> None:
        label, value, ratio, c, key, tooltip = row
        ratio = max(0.0, min(1.0, ratio))
        main = self.liquid_status_color(label, ratio, c)
        fast_ratio, _lag_ratio = self.animated_status_ratio(f"{key_prefix}.{key}", ratio)
        hovered = rect.contains(self.hover_point)
        hover = self.anim.value(f"liquid.status.mini.hover.{key_prefix}.{key}", 1.0 if hovered else 0.0, self.anim_duration(0.12), Easings.ease_out_cubic)
        rounded_rect(p, rect, 5.0, color("#081421", 0.46 + 0.08 * hover), color("#eef5ff", 0.16 + 0.08 * hover), 0.8)
        p.fillRect(QRectF(rect.left() + 1.0, rect.top() + 3.0, 1.5, rect.height() - 6.0), with_alpha(main, 0.74))
        fill = QRectF(rect.left() + 2.0, rect.bottom() - 3.0, max(3.0, (rect.width() - 4.0) * fast_ratio), 2.2)
        rounded_rect(p, fill, 1.1, with_alpha(main, 0.82))
        font = self.font("poppins_medium", 5.7 if display != "LiquidCards" else 5.4)
        text = self.compact_map_status_label(label)
        if self.liquid_map_bool("Show Values", True):
            text = f"{text} {value}"
        self.draw_text(p, rect.adjusted(3.0, 0.0, -3.0, -1.0), hud_text(text, chinese_enabled(self.registry)), font, THEME.text, Qt.AlignmentFlag.AlignCenter)
        self.add_hotspot(rect.adjusted(-2.0, -2.0, 2.0, 2.0), "map_status", key, tooltip=hud_text(tooltip, chinese_enabled(self.registry)))

    def liquid_entity_color(self, team: str, hp_ratio: float, distance_ratio: float, color_mode: str) -> QColor:
        if color_mode == "Health":
            if hp_ratio < 0.30:
                return self.hud_danger()
            if hp_ratio < 0.58:
                return self.hud_warning()
            return self.hud_accent() if team == "enemy" else self.hud_success()
        if color_mode == "Distance":
            if distance_ratio < 0.22:
                return self.hud_danger()
            if distance_ratio < 0.48:
                return self.hud_warning()
            return self.hud_accent()
        if self.setting_mode(self.registry.get("TacticalMap"), "Palette", "RM Tactical") == "RM Tactical":
            return self.map_team_color(team)
        return self.hud_danger() if team == "enemy" else self.hud_accent()

    def map_team_color(self, relation: str) -> QColor:
        side = self.alliance_side()
        if relation.lower() in ("enemy", "foe"):
            side = "Blue" if side == "Red" else "Red"
        elif relation.lower() in ("red", "blue"):
            side = relation.title()
        # macOS 风格的低饱和阵营色：仍然保持红/蓝语义，但不再用高亮纯色铺满状态条。
        return color("#ff7882") if side == "Red" else color("#78a7ff")

    def map_control_color(self) -> QColor:
        owner = str(self.state.control_owner).upper()
        if owner == "ALLY":
            return self.map_team_color("ally")
        if owner == "ENEMY":
            return self.map_team_color("enemy")
        if owner == "RED":
            return self.map_team_color("red")
        if owner == "BLUE":
            return self.map_team_color("blue")
        return color("#9aa8ba")

    def draw_liquid_radar_pointer(
        self,
        p: QPainter,
        center: QPointF,
        angle_deg: float,
        c: QColor,
        style: str,
        size: float,
        highlight: bool,
    ) -> None:
        p.save()
        p.translate(center)
        p.rotate(angle_deg)
        p.setPen(QPen(color("#ffffff", 0.64 if highlight else 0.36), 0.8))
        p.setBrush(c)
        if style == "Dot":
            p.drawEllipse(QPointF(0.0, 0.0), size * 0.58, size * 0.58)
        elif style == "Wedge":
            polygon = QPolygonF([
                QPointF(0.0, -size),
                QPointF(-size * 0.70, size * 0.38),
                QPointF(0.0, size * 0.10),
                QPointF(size * 0.70, size * 0.38),
            ])
            p.drawPolygon(polygon)
        else:
            polygon = QPolygonF([
                QPointF(0.0, -size),
                QPointF(-size * 0.62, size * 0.62),
                QPointF(size * 0.62, size * 0.62),
            ])
            p.drawPolygon(polygon)
        p.restore()

    def draw_liquid_status_dock(
        self,
        p: QPainter,
        rect: QRectF,
        field_rect: QRectF,
        width: int,
        display: str,
        minimal: bool,
        compact: bool,
        morph_progress: float = 0.0,
    ) -> None:
        match_rows, telemetry_rows = self.map_status_groups()
        morph_progress = max(0.0, min(1.0, morph_progress))
        bottom_rows = match_rows[:4]
        side_rows = telemetry_rows[:4]
        if not bottom_rows and not side_rows:
            return
        liquid_surface = self.hud_clean()
        if display == "LiquidCards":
            card_blend = 1.0
        elif display == "LiquidBars":
            card_blend = 0.0
        else:
            blend_t = max(0.0, min(1.0, (morph_progress - 0.18) / 0.64))
            card_blend = blend_t * blend_t * (3.0 - 2.0 * blend_t)
        bottom_top = field_rect.bottom() + 3.0 + 2.0 * morph_progress
        bottom_rect = QRectF(rect.left() + 7.0, bottom_top, rect.width() - 14.0, max(22.0, rect.bottom() - bottom_top - 5.0))
        if bottom_rows:
            if liquid_surface:
                self.draw_hud_liquid_glass(p, bottom_rect, 5.0, surface="MainGlass")
            else:
                rounded_rect(p, bottom_rect, 5.0, color("#000000", 0.34 if minimal else 0.28), color("#ffffff", 0.055 if minimal else 0.035), 0.6)
            self.draw_liquid_status_row(
                p,
                bottom_rect,
                bottom_rows,
                card_blend=card_blend,
                vertical=False,
                minimal=minimal,
                dock_key="match",
                liquid_glass=liquid_surface,
                morph_progress=morph_progress,
                collapsed_count=3 if minimal else len(bottom_rows),
            )

        if not side_rows:
            return
        collapsed_row_h = 18.0 if minimal else 24.0
        expanded_row_h = 24.0 if compact else 26.0
        row_h = collapsed_row_h + (expanded_row_h - collapsed_row_h) * morph_progress
        side_gap = 4.0 + morph_progress
        collapsed_side_w = 74.0 if minimal else 104.0
        side_w = collapsed_side_w + (118.0 - collapsed_side_w) * morph_progress
        side_h = len(side_rows) * row_h + max(0, len(side_rows) - 1) * side_gap
        side_x = rect.right() + 6.0 + 3.0 * morph_progress
        side_y = min(rect.bottom() - side_h, max(rect.top() + 14.0, field_rect.top()))
        safe_right = width - 218.0
        if side_x + side_w <= safe_right:
            side_rect = QRectF(side_x, side_y, side_w, side_h)
            shell = side_rect.adjusted(-4.0, -4.0, 4.0, 4.0)
            if liquid_surface:
                self.draw_hud_liquid_glass(p, shell, 6.0, surface="MainGlass")
            else:
                rounded_rect(p, shell, 6.0, color("#000000", 0.30), color("#ffffff", 0.040), 0.6)
            self.draw_liquid_status_row(
                p,
                side_rect,
                side_rows,
                card_blend=card_blend,
                vertical=True,
                minimal=minimal,
                dock_key="telemetry",
                liquid_glass=liquid_surface,
                morph_progress=morph_progress,
                collapsed_count=2 if minimal else len(side_rows),
            )
            return
        fold_rect = QRectF(rect.left() + 7.0, rect.bottom() + 5.0, min(rect.width() - 14.0, 250.0), row_h + 2.0)
        if liquid_surface:
            self.draw_hud_liquid_glass(p, fold_rect, 5.0, surface="MainGlass")
        else:
            rounded_rect(p, fold_rect, 5.0, color("#000000", 0.30), color("#ffffff", 0.040), 0.6)
        self.draw_liquid_status_row(
            p,
            fold_rect,
            side_rows,
            card_blend=card_blend,
            vertical=False,
            minimal=minimal,
            dock_key="telemetry_fold",
            liquid_glass=liquid_surface,
            morph_progress=morph_progress,
            collapsed_count=2 if minimal else len(side_rows),
        )

    def draw_liquid_status_row(
        self,
        p: QPainter,
        rect: QRectF,
        rows: list[tuple[str, str, float, QColor, str, str]],
        *,
        card_blend: float,
        vertical: bool,
        minimal: bool,
        dock_key: str,
        liquid_glass: bool = False,
        morph_progress: float = 1.0,
        collapsed_count: int | None = None,
    ) -> None:
        if not rows:
            return
        gap = 4.0 if minimal else 5.0
        enter = self.anim.value(f"liquid.status.dock.{dock_key}", 1.0, self.anim_duration(0.20), Easings.ease_out_cubic, initial=0.0)
        p.save()
        p.setOpacity(p.opacity() * enter)
        count = len(rows)
        collapsed_count = max(1, min(count, collapsed_count or count))
        morph_progress = max(0.0, min(1.0, morph_progress))
        extra_t = max(0.0, min(1.0, (morph_progress - 0.10) / 0.62))
        extra_reveal = extra_t * extra_t * (3.0 - 2.0 * extra_t)
        if vertical:
            collapsed_h = (rect.height() - gap * (collapsed_count - 1)) / collapsed_count
            expanded_h = (rect.height() - gap * (count - 1)) / count
            for index, row in enumerate(rows):
                collapsed_y = rect.top() + min(index, collapsed_count - 1) * (collapsed_h + gap)
                expanded_y = rect.top() + index * (expanded_h + gap)
                y = collapsed_y + (expanded_y - collapsed_y) * morph_progress
                h = collapsed_h + (expanded_h - collapsed_h) * morph_progress
                slot = QRectF(rect.left(), y, rect.width(), h)
                item_alpha = 1.0 if index < collapsed_count else extra_reveal
                self.draw_liquid_status_item(p, slot, row, card_blend=card_blend, vertical=True, minimal=minimal, dock_key=dock_key, index=index, liquid_glass=liquid_glass, item_alpha=item_alpha)
        else:
            collapsed_w = (rect.width() - gap * (collapsed_count - 1)) / collapsed_count
            expanded_w = (rect.width() - gap * (count - 1)) / count
            for index, row in enumerate(rows):
                collapsed_x = rect.left() + min(index, collapsed_count - 1) * (collapsed_w + gap)
                expanded_x = rect.left() + index * (expanded_w + gap)
                x = collapsed_x + (expanded_x - collapsed_x) * morph_progress
                w = collapsed_w + (expanded_w - collapsed_w) * morph_progress
                slot = QRectF(x, rect.top(), w, rect.height())
                item_alpha = 1.0 if index < collapsed_count else extra_reveal
                self.draw_liquid_status_item(p, slot, row, card_blend=card_blend, vertical=False, minimal=minimal, dock_key=dock_key, index=index, liquid_glass=liquid_glass, item_alpha=item_alpha)
        p.restore()

    def draw_liquid_status_item(
        self,
        p: QPainter,
        rect: QRectF,
        row: tuple[str, str, float, QColor, str, str],
        *,
        card_blend: float,
        vertical: bool,
        minimal: bool,
        dock_key: str,
        index: int,
        liquid_glass: bool = False,
        item_alpha: float = 1.0,
    ) -> None:
        label, value, ratio, c, key, tooltip = row
        animation = self.liquid_map_setting("Dock Animation", "Fly")
        enter = self.anim.value(
            f"liquid.status.item.{dock_key}.{key}",
            1.0,
            self.anim_duration(0.18 + index * 0.025),
            Easings.ease_out_cubic,
            initial=0.0,
        )
        item_rect = QRectF(rect)
        p.save()
        p.setOpacity(p.opacity() * enter * max(0.0, min(1.0, item_alpha)))
        if animation == "Fly":
            item_rect = item_rect.translated((1.0 - enter) * (14.0 if vertical else 0.0), (1.0 - enter) * (8.0 if not vertical else 0.0))
        elif animation == "Flip":
            p.translate(item_rect.center())
            p.scale(0.92 + 0.08 * enter, 0.96 + 0.04 * enter)
            p.translate(-item_rect.center())
        card_blend = max(0.0, min(1.0, card_blend))
        if card_blend < 0.999:
            p.save()
            p.setOpacity(p.opacity() * (1.0 - card_blend))
            self.draw_liquid_status_bar(p, item_rect, label, value, ratio, c, key, tooltip, minimal=minimal, liquid_glass=liquid_glass)
            p.restore()
        if card_blend > 0.001:
            p.save()
            p.setOpacity(p.opacity() * card_blend)
            self.draw_liquid_status_card(p, item_rect, label, value, ratio, c, key, tooltip, minimal=minimal, liquid_glass=liquid_glass)
            p.restore()
        p.restore()

    def liquid_status_color(self, label: str, ratio: float, fallback: QColor) -> QColor:
        label = label.upper()
        ratio = max(0.0, min(1.0, ratio))
        if "BASE" in label:
            return self.map_team_color("ally" if "YOU" in label else "enemy")
        if "OUTPOST" in label:
            return self.hud_warning() if "YOU" in label else self.hud_danger()
        if label in ("RED", "RED YOU"):
            return self.map_team_color("red")
        if label in ("BLUE", "BLUE YOU"):
            return self.map_team_color("blue")
        if label in ("ENEMY", "ENMY", "EBASE", "OUT", "OUTPOST"):
            return self.hud_danger()
        if label == "CTRL":
            return self.map_control_color()
        if label == "GOLD":
            return color("#e6bd68") if self.hud_clean() else self.hud_warning()
        if label in ("AMMO",):
            return self.hud_success()
        if label == "HEAT":
            # 热量单独走：除了比例分档，还要听 `PowerGuard / Heat Warn`。
            return self.heat_display_color(ratio)
        if label in ("POWER", "PWR"):
            if ratio >= 0.92:
                return self.hud_danger()
            if ratio >= 0.78:
                return self.hud_warning()
            return self.hud_accent()
        if label in ("SPEED", "SPD", "CAP"):
            return color("#79b7ff") if self.hud_clean() else fallback
        return self.hud_accent() if self.hud_clean() else fallback

    def draw_liquid_status_bar(
        self,
        p: QPainter,
        rect: QRectF,
        label: str,
        value: str,
        ratio: float,
        c: QColor,
        key: str,
        tooltip: str,
        *,
        minimal: bool,
        liquid_glass: bool = False,
    ) -> None:
        fast_ratio, lag_ratio = self.animated_status_ratio(f"liquid.{key}", ratio)
        main = self.liquid_status_color(label, ratio, c)
        hovered = rect.contains(self.hover_point)
        hover = self.anim.value(f"liquid.status.hover.{key}", 1.0 if hovered else 0.0, self.anim_duration(0.14), Easings.ease_out_cubic)
        h = min(rect.height(), 18.0 if minimal else 22.0)
        bar_rect = QRectF(rect.left(), rect.center().y() - h / 2.0, rect.width(), h)
        if liquid_glass:
            self.draw_hud_liquid_glass(p, bar_rect, 5.0, surface="SubGlass", hover=hover)
        else:
            rounded_rect(p, bar_rect, 5.0, color("#0a1420", 0.42 + 0.08 * hover), color("#eef5ff", 0.12 + 0.08 * hover), 0.8)
        progress_bg = QRectF(bar_rect.left() + 5.0, bar_rect.bottom() - 3.0, bar_rect.width() - 10.0, 2.0)
        rounded_rect(p, progress_bg, 1.0, color("#eef5ff", 0.12))
        lag_rect = QRectF(progress_bg.left(), progress_bg.top(), max(2.0, progress_bg.width() * lag_ratio), progress_bg.height())
        rounded_rect(p, lag_rect, 1.0, with_alpha(main, 0.18 + 0.06 * hover))
        fill_rect = QRectF(progress_bg.left(), progress_bg.top(), max(2.0, progress_bg.width() * fast_ratio), progress_bg.height())
        rounded_rect(p, fill_rect, 1.0, with_alpha(main, 0.82 + 0.08 * hover))
        label_font = self.font("poppins_medium", 5.4 if minimal else 6.4)
        value_font = self.font("poppins_medium", 5.5 if minimal else 6.5)
        short_label = self.compact_map_status_label(label) if minimal else label
        text_left = bar_rect.adjusted(5.0, 0.0, -5.0, 0.0)
        show_value = self.liquid_map_bool("Show Values", True)
        value_width = text_width(p, value_font, value) if show_value else 0.0
        label_width = text_width(p, label_font, short_label)
        if show_value and label_width + value_width + 7.0 > text_left.width():
            short_label = self.compact_map_status_label(label)
            value_area_w = min(max(18.0, value_width + 1.0), text_left.width() * 0.48)
            label_area = QRectF(text_left.left(), text_left.top(), max(1.0, text_left.width() - value_area_w - 4.0), text_left.height())
            value_area = QRectF(text_left.right() - value_area_w, text_left.top(), value_area_w, text_left.height())
            short_label = self.elide(p, label_font, hud_text(short_label, chinese_enabled(self.registry)), label_area.width())
            display_value = self.elide(p, value_font, hud_text(value, chinese_enabled(self.registry)), value_area.width())
        else:
            label_area = QRectF(text_left)
            value_area = QRectF(text_left)
            display_value = value
        self.draw_text(p, label_area.translated(0.5, 0.7), hud_text(short_label, chinese_enabled(self.registry)), label_font, color("#000000", 0.72))
        self.draw_text(p, label_area, hud_text(short_label, chinese_enabled(self.registry)), label_font, THEME.text)
        if show_value:
            align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            self.draw_text(p, value_area.translated(0.5, 0.7), hud_text(display_value, chinese_enabled(self.registry)), value_font, color("#000000", 0.72), align)
            self.draw_text(p, value_area, hud_text(display_value, chinese_enabled(self.registry)), value_font, THEME.text, align)
        self.add_hotspot(bar_rect.adjusted(-3.0, -3.0, 3.0, 3.0), "map_status", key, tooltip=hud_text(tooltip, chinese_enabled(self.registry)))

    def draw_liquid_status_card(
        self,
        p: QPainter,
        rect: QRectF,
        label: str,
        value: str,
        ratio: float,
        c: QColor,
        key: str,
        tooltip: str,
        *,
        minimal: bool,
        liquid_glass: bool = False,
    ) -> None:
        fast_ratio, lag_ratio = self.animated_status_ratio(f"liquid.card.{key}", ratio)
        main = self.liquid_status_color(label, ratio, c)
        hovered = rect.contains(self.hover_point)
        hover = self.anim.value(f"liquid.status.card.hover.{key}", 1.0 if hovered else 0.0, self.anim_duration(0.14), Easings.ease_out_cubic)
        card_rect = rect.adjusted(0.0, 1.0, 0.0, -1.0)
        if liquid_glass:
            self.draw_hud_liquid_glass(p, card_rect, 5.0, surface="SubGlass", hover=hover)
        else:
            rounded_rect(p, card_rect, 5.0, color("#0a1420", 0.42 + 0.08 * hover), color("#eef5ff", 0.12 + 0.08 * hover), 0.8)
        p.fillRect(QRectF(card_rect.left(), card_rect.top() + 4.0, 2.0, card_rect.height() - 8.0), with_alpha(main, 0.82))
        label_font = self.font("poppins_medium", 5.2 if minimal else 6.1)
        value_font = self.font("poppins_bold", 5.5 if minimal else 6.7)
        label_text = self.compact_map_status_label(label) if minimal else label
        text_rect = card_rect.adjusted(6.0, 1.0, -5.0, -7.0)
        show_value = self.liquid_map_bool("Show Values", True)
        combined = text_width(p, label_font, label_text) + text_width(p, value_font, value) + 6.0
        stacked = show_value and (card_rect.width() < 82.0 or combined > text_rect.width())
        if stacked:
            label_text = self.compact_map_status_label(label)
            split_h = max(7.0, text_rect.height() * 0.48)
            label_rect = QRectF(text_rect.left(), text_rect.top(), text_rect.width(), split_h)
            value_rect = QRectF(text_rect.left(), label_rect.bottom() - 1.0, text_rect.width(), max(6.0, text_rect.bottom() - label_rect.bottom() + 1.0))
            label_text = self.elide(p, label_font, hud_text(label_text, chinese_enabled(self.registry)), label_rect.width())
            display_value = self.elide(p, value_font, hud_text(value, chinese_enabled(self.registry)), value_rect.width())
            value_align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        else:
            value_w = min(text_rect.width() * 0.46, text_width(p, value_font, value) + 2.0) if show_value else 0.0
            label_rect = QRectF(text_rect.left(), text_rect.top(), max(1.0, text_rect.width() - value_w - 4.0), text_rect.height())
            value_rect = QRectF(text_rect.right() - value_w, text_rect.top(), value_w, text_rect.height())
            label_text = self.elide(p, label_font, hud_text(label_text, chinese_enabled(self.registry)), label_rect.width())
            display_value = self.elide(p, value_font, hud_text(value, chinese_enabled(self.registry)), value_rect.width())
            value_align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        self.draw_text(p, label_rect, hud_text(label_text, chinese_enabled(self.registry)), label_font, THEME.muted)
        if show_value:
            self.draw_text(p, value_rect, hud_text(display_value, chinese_enabled(self.registry)), value_font, THEME.text, value_align)
        bar = QRectF(card_rect.left() + 6.0, card_rect.bottom() - 5.0, card_rect.width() - 12.0, 2.0)
        draw_bar(p, bar, lag_ratio, with_alpha(main, 0.18), color("#ffffff", 0.10), 1.0)
        draw_bar(p, bar, fast_ratio, with_alpha(main, 0.78), color("#ffffff", 0.00), 1.0)
        self.add_hotspot(card_rect.adjusted(-3.0, -3.0, 3.0, 3.0), "map_status", key, tooltip=hud_text(tooltip, chinese_enabled(self.registry)))

    def draw_official_map(self, p: QPainter, rect: QRectF, config: MatchModeConfig) -> QRectF:
        image = self.official_map_image(config)
        opacity_setting = self.registry.get("TacticalMap").get_setting("Opacity") if self.registry.get("TacticalMap") else None
        image_opacity = float(opacity_setting.value) if opacity_setting else 0.72
        rounded_rect(p, rect, 6, color("#050506", 0.72), color("#ffffff", 0.08))
        if image is None or image.isNull():
            self.draw_text(p, rect, hud_text("official map asset missing", chinese_enabled(self.registry)), self.font("poppins_regular", 8.0), self.hud_danger(), Qt.AlignmentFlag.AlignCenter)
            fit = self.fit_aspect(rect, config.field.width_m / max(0.1, config.field.height_m))
        else:
            fit = self.fit_aspect(rect, image.width() / max(1.0, image.height()))
            # 缺陷 D：fit 贴满时方角贴图会盖过面板 r=6 圆弧缺口。GL 绘制引擎对
            # clip 的支持在本工程内证据冲突（HANDOFF 第四章 control FBO 实测
            # setClipPath/setClipRect 被静默忽略；本机 overlay 层双臂差分实验显示
            # 当前生效），不能依赖。drawImage 没有路径交集变体，因此用引擎无关的
            # 圆角预裁剪缓存：仅当 fit 方角真的侵入圆弧缺口（折叠动画等比穿越点、
            # 展开贴满态）时才预合成带透明角的位图，其余帧保持原直绘零开销。
            masked = None
            if self._map_fit_cuts_panel_corners(rect, fit, 6.0):
                masked = self._rounded_map_layer(p, image, fit, rect, 6.0)
            p.save()
            p.setOpacity(max(0.35, min(1.0, image_opacity)))
            # Source maps are high resolution scans. Resample only when the map
            # panel itself changes size (for example while Tab expands it), not
            # for every camera/HUD repaint.
            # 地图形变期间直接让 Qt/OpenGL 缩放源纹理，避免每个动画尺寸都在 CPU
            # 生成一张新位图并清空缓存。
            if masked is not None:
                p.drawImage(fit, masked)
            else:
                p.drawImage(fit, image)
            p.restore()
            if masked is not None:
                # 压暗层同步按圆角收缩：fillPath 是直接路径填充，不依赖 clip，
                # 在 raster/GL 两种引擎上都可靠（同 drawPath 交集修复的先例）。
                # 掩码取 rounded(fit) ∩ rounded(rect)：贴图边界与面板轮廓弧重合，
                # 不给弧外留任何亚像素 AA 碰撞余量。
                dim_path = QPainterPath()
                dim_path.addRoundedRect(fit, 6.0, 6.0)
                panel_path = QPainterPath()
                panel_path.addRoundedRect(rect, 6.0, 6.0)
                p.fillPath(dim_path.intersected(panel_path),
                           color("#07111d", 0.16 if self.hud_clean() else 0.28))
            else:
                p.fillRect(fit, color("#07111d", 0.16 if self.hud_clean() else 0.28))
        rounded_rect(p, fit, 4, color("#000000", 0.0), with_alpha(self.hud_accent(), 0.30 if self.hud_clean() else 0.22), 1.0)
        return fit

    def _map_fit_cuts_panel_corners(self, rect: QRectF, fit: QRectF, radius: float) -> bool:
        """判定 fit 的方角是否会进入面板四角的圆弧缺口。

        只有当某一角在水平、垂直两个方向上与面板边缘的距离同时小于圆角半径时，
        直角才可能落在弧外（此时才需要预裁剪）；+1px 余量覆盖 AA 过渡带。
        """
        gaps_x = (fit.left() - rect.left(), rect.right() - fit.right())
        gaps_y = (fit.top() - rect.top(), rect.bottom() - fit.bottom())
        margin = radius + 1.0
        return any(gx < margin and gy < margin for gx in gaps_x for gy in gaps_y)

    def _rounded_map_layer(self, p: QPainter, image: QImage, fit: QRectF, rect: QRectF, radius: float) -> QImage | None:
        """把源图按圆角掩码预合成进带透明角的缓存位图（缺陷 D）。

        - 掩码 = rounded(fit, r) ∩ rounded(rect, r)（映射到图层坐标）：贴图可见
          区域与面板 silhouette 精确重合，弧外零泄漏、AA 带与面板弧重合；
        - 以设备像素密度（dpr × painter 变换）合成，drawImage 映射回 fit 时纹素
          约 1:1，清晰度与 GPU 直绘一致；
        - key 含源图 cacheKey()：注入/更换地图资产时自动失效（gui_cache 的 key 纪律）；
        - 有界缓存：稳态下 key 不变 => 直接复用，无每帧 QImage 分配。
        """
        cache = getattr(self, "_map_round_cache", None)
        if cache is None:
            cache = self._map_round_cache = {}
        scale = abs(p.transform().m11()) or 1.0
        device = p.device()
        if device is not None:
            try:
                scale *= float(device.devicePixelRatioF())
            except Exception:
                pass
        scale = max(0.5, min(scale, 4.0))
        width = max(1, int(math.ceil(fit.width() * scale)))
        height = max(1, int(math.ceil(fit.height() * scale)))
        sx = width / max(0.001, fit.width())
        sy = height / max(0.001, fit.height())
        radius_px = radius * scale
        # key 必须完整决定掩码形状：除尺寸/半径外还要含 fit 相对面板的偏移
        # （同尺寸不同偏移 => rounded(fit)∩rounded(rect) 的角部掩码不同）。
        offset_key = (
            round((fit.left() - rect.left()) * sx, 2),
            round((fit.top() - rect.top()) * sy, 2),
        )
        key = (image.cacheKey(), width, height, round(radius_px, 2), offset_key)
        cached = cache.get(key)
        if cached is not None:
            return cached
        try:
            layer = QImage(width, height, QImage.Format.Format_RGBA8888_Premultiplied)
        except Exception:
            return None
        layer.fill(Qt.GlobalColor.transparent)
        painter = QPainter(layer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        # QImage 走 raster 引擎，clip 语义可靠；掩码外交汇处保持透明。
        mask = QPainterPath()
        mask.addRoundedRect(QRectF(0.0, 0.0, float(width), float(height)), radius * sx, radius * sy)
        panel = QPainterPath()
        panel.addRoundedRect(
            QRectF((rect.left() - fit.left()) * sx, (rect.top() - fit.top()) * sy,
                   rect.width() * sx, rect.height() * sy),
            radius * sx, radius * sy,
        )
        painter.setClipPath(mask.intersected(panel))
        painter.drawImage(QRectF(0.0, 0.0, float(width), float(height)), image)
        painter.end()
        if len(cache) >= 8:
            cache.clear()
        cache[key] = layer
        return layer

    def official_map_image(self, config: MatchModeConfig) -> QImage | None:
        path = self.paths.resource_root / config.field.asset_path
        key = str(path)
        if key not in self.map_images:
            self.map_images[key] = QImage(key)
        image = self.map_images[key]
        return None if image.isNull() else image

    def official_map_display_image(self, config: MatchModeConfig, source: QImage, fit: QRectF) -> QImage:
        width = max(1, int(round(fit.width())))
        height = max(1, int(round(fit.height())))
        source_key = str(self.paths.resource_root / config.field.asset_path)
        key = (source_key, width, height)
        cached = self.map_display_cache.get(key)
        if cached is not None and not cached.isNull():
            return cached
        cached = source.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if len(self.map_display_cache) >= 24:
            # Exact-size map entries are a bounded LRU-like cache; clearing this
            # tiny cache on a resize is cheaper than holding every animation size.
            self.map_display_cache.clear()
        self.map_display_cache[key] = cached
        return cached

    def map_point(self, rect: QRectF, field: FieldLayout, x_m: float, y_m: float) -> QPointF:
        nx = max(0.0, min(1.0, x_m / max(0.1, field.width_m)))
        ny = max(0.0, min(1.0, y_m / max(0.1, field.height_m)))
        tactical = self.registry.get("TacticalMap")
        profile = self.registry.get("AllianceProfile")
        mirror = (
            field.mirror_for_blue
            and self.alliance_side() == "Blue"
            and self.setting_bool(tactical, "Mirror Blue", True)
            and self.setting_bool(profile, "Mirror Tactical Map", True)
        )
        if mirror:
            nx = 1.0 - nx
            ny = 1.0 - ny
        return QPointF(rect.left() + rect.width() * nx, rect.bottom() - rect.height() * ny)

    def draw_map_zones(
        self,
        p: QPainter,
        rect: QRectF,
        field: FieldLayout,
        compact: bool = False,
        detail_progress: float | None = None,
    ) -> None:
        now = time.perf_counter()
        detail = (0.0 if compact else 1.0) if detail_progress is None else max(0.0, min(1.0, detail_progress))
        zone_colors = {
            "base": color("#ffffff", 0.13),
            "outpost": with_alpha(self.map_team_color("enemy"), 0.16),
            "rune": with_alpha(self.hud_accent(), 0.16),
            "supply": with_alpha(self.hud_success(), 0.13),
            "control": with_alpha(self.map_control_color(), 0.18),
            "highland": color("#ffffff", 0.10),
        }
        for index, zone in enumerate(field.zones):
            p1 = self.map_point(rect, field, zone.x, zone.y)
            p2 = self.map_point(rect, field, zone.x + zone.w, zone.y + zone.h)
            zone_rect = QRectF(min(p1.x(), p2.x()), min(p1.y(), p2.y()), abs(p2.x() - p1.x()), abs(p2.y() - p1.y()))
            pulse = 0.5 + 0.5 * math.sin(now * 2.0 + index)
            fill = zone_colors.get(zone.kind, color("#ffffff", 0.10))
            border_alpha = 0.06 + 0.04 * detail
            fill_alpha = 0.40 + (0.20 + 0.15 * detail) * pulse
            rounded_rect(p, zone_rect, 3, with_alpha(fill, fill_alpha), color("#ffffff", border_alpha))

    def draw_map_robots(self, p: QPainter, rect: QRectF, field: FieldLayout, compact: bool = False) -> None:
        now = time.perf_counter()
        font = self.font("poppins_medium", 6.8)
        for index, robot in enumerate(self.state.map_robots):
            point = self.map_point(rect, field, float(robot.get("x", 0.0)), float(robot.get("y", 0.0)))
            team = str(robot.get("team", "ally"))
            highlight = bool(robot.get("highlight", False))
            base_color = self.map_team_color(team)
            if highlight:
                radius = (7.0 if compact else 9.5) + (2.8 if compact else 4.0) * (0.5 + 0.5 * math.sin(now * 4.0 + index))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(with_alpha(base_color, 0.40), 1.1))
                p.drawEllipse(point, radius, radius)
            p.setBrush(base_color)
            p.setPen(QPen(color("#ffffff", 0.86), 1.0))
            dot_radius = 3.4 if compact else 4.2
            p.drawEllipse(point, dot_radius, dot_radius)
            if not compact:
                label = str(robot.get("id", "?"))
                label_rect = QRectF(point.x() + 6, point.y() - 9, 34, 14)
                self.draw_text(p, label_rect, label, font, THEME.text)
            hp = int(robot.get("hp", 0))
            hp_max = max(1, int(robot.get("max", 1)))
            bar_w = 15 if compact else 20
            bar = QRectF(point.x() - bar_w / 2, point.y() + (5 if compact else 7), bar_w, 2.4 if compact else 3)
            draw_bar(p, bar, hp / hp_max, base_color, color("#ffffff", 0.16), 1.5)
            tooltip = f"{robot.get('name', hud_text('Robot', chinese_enabled(self.registry)))} {robot.get('id', '?')} | {hp}/{hp_max} {hud_text('HP', chinese_enabled(self.registry))}"
            self.add_hotspot(QRectF(point.x() - 10, point.y() - 10, 20, 20), "map_robot", robot.get("id"), tooltip=tooltip)

    def draw_map_markers(
        self,
        p: QPainter,
        rect: QRectF,
        field: FieldLayout,
        compact: bool = False,
        detail_progress: float | None = None,
    ) -> None:
        now = time.perf_counter()
        detail = (0.0 if compact else 1.0) if detail_progress is None else max(0.0, min(1.0, detail_progress))
        font = self.font("poppins_medium", 6.5)
        for index, marker in enumerate(self.state.map_markers):
            point = self.map_point(rect, field, float(marker.get("x", 0.0)), float(marker.get("y", 0.0)))
            pulse = bool(marker.get("pulse", False))
            kind = str(marker.get("kind", "marker"))
            c = self.hud_warning() if kind == "control" else self.hud_success() if kind == "supply" else self.hud_accent()
            if pulse:
                p.setPen(QPen(with_alpha(c, 0.44), 1.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                pulse_r = (5.5 + 2.5 * detail) + (2.5 + 1.5 * detail) * (0.5 + 0.5 * math.sin(now * 4.6))
                p.drawEllipse(point, pulse_r, pulse_r)
            p.setPen(QPen(c, 1.2))
            half = 4.0 + 2.0 * detail
            p.drawLine(point + QPointF(-half, 0), point + QPointF(half, 0))
            p.drawLine(point + QPointF(0, -half), point + QPointF(0, half))
            if detail > 0.001:
                p.save()
                p.setOpacity(p.opacity() * detail)
                self.draw_text(p, QRectF(point.x() + 7, point.y() - 8, 48, 13), str(marker.get("label", "")), font, c)
                p.restore()

    def draw_map_status_orbit_dock(self, p: QPainter, rect: QRectF, field_rect: QRectF, width: int, minimal: bool, compact: bool) -> None:
        match_rows, telemetry_rows = self.map_status_groups()
        bottom_rows = match_rows[: 3 if minimal else 4]
        side_rows = telemetry_rows[: 2 if minimal else 4]
        if not bottom_rows and not side_rows:
            return

        margin = 9.0 if minimal else 12.0
        bottom_h = max(29.0 if minimal else 42.0, rect.bottom() - field_rect.bottom() - (5.0 if minimal else 8.0))
        bottom_rect = QRectF(rect.left() + margin, rect.bottom() - bottom_h - 4.0, rect.width() - margin * 2.0, bottom_h)
        mode_key = "mini" if minimal else "full"
        if bottom_rows:
            self.draw_blackhole_disc_row(p, bottom_rect, bottom_rows, minimal=minimal, vertical=False, dock_key=f"match.{mode_key}")

        if not side_rows:
            return

        side_d = 23.0 if minimal else 34.0 if compact else 38.0
        side_gap = 6.0 if minimal else 8.0
        side_label_w = 39.0 if minimal else 54.0
        side_w = side_d + 14.0 + side_label_w
        side_h = len(side_rows) * side_d + max(0, len(side_rows) - 1) * side_gap + 10.0
        side_x = rect.right() + (7.0 if minimal else 9.0)
        side_y = min(rect.bottom() - side_h - 4.0, max(rect.top() + 18.0, field_rect.top() + 4.0))
        safe_right = width - 218.0
        can_use_side = side_x + side_w <= safe_right and side_y >= 24.0

        if can_use_side:
            side_rect = QRectF(side_x, side_y, side_w, side_h)
            self.draw_blackhole_disc_row(p, side_rect, side_rows, minimal=minimal, vertical=True, dock_key=f"telemetry.{mode_key}")
            return

        fold_w = min(rect.width() - margin * 2.0, len(side_rows) * (side_d + (27.0 if minimal else 38.0)))
        fold_h = side_d + (17.0 if minimal else 24.0)
        fold_rect = QRectF(rect.left() + margin, rect.bottom() + 5.0, fold_w, fold_h)
        self.draw_blackhole_disc_row(p, fold_rect, side_rows, minimal=minimal, vertical=False, dock_key=f"telemetry_fold.{mode_key}")

    def draw_blackhole_disc_row(
        self,
        p: QPainter,
        rect: QRectF,
        rows: list[tuple[str, str, float, QColor, str, str]],
        minimal: bool,
        vertical: bool,
        dock_key: str = "status",
    ) -> None:
        if not rows:
            return
        enter = self.anim.value(f"map.status.dock.{dock_key}", 1.0, self.anim_duration(0.26), Easings.ease_out_expo, initial=0.0)
        p.save()
        p.setOpacity(p.opacity() * enter)
        rounded_rect(p, rect, 10 if not minimal else 7, self.hud_surface(0.24 if minimal else 0.34), self.hud_panel_outline(0.040 if self.hud_clean() else 0.050))
        accent = QLinearGradient(rect.topLeft(), rect.topRight() if not vertical else rect.bottomLeft())
        accent.setColorAt(0.0, with_alpha(self.hud_accent(), 0.42 if minimal else 0.58))
        accent.setColorAt(1.0, with_alpha(self.hud_accent(), 0.60 if minimal else 0.76))
        if vertical:
            p.fillRect(QRectF(rect.left() + 4.0, rect.top() + 6.0, 1.2, rect.height() - 12.0), accent)
        else:
            p.fillRect(QRectF(rect.left() + 7.0, rect.top() + 3.0, rect.width() - 14.0, 1.0), accent)
        if vertical:
            slot_h = rect.height() / len(rows)
            for index, row in enumerate(rows):
                label, value, ratio, c, key, tooltip = row
                row_enter = self.anim.value(f"map.status.item.{dock_key}.{key}", 1.0, self.anim_duration(0.18 + index * 0.045), Easings.ease_out_expo, initial=0.0)
                diameter = max(18.0, min(23.0 if minimal else 38.0, slot_h - 5.0, rect.width() - (48.0 if minimal else 62.0)))
                center = QPointF(rect.left() + 7.0 + diameter / 2.0 - (1.0 - row_enter) * 10.0, rect.top() + slot_h * (index + 0.5))
                p.save()
                p.setOpacity(p.opacity() * row_enter)
                self.draw_blackhole_status_disc(p, center, diameter, label, value, ratio, c, key, tooltip, show_label=True, label_side="right")
                p.restore()
            p.restore()
            return

        slot_w = rect.width() / len(rows)
        for index, row in enumerate(rows):
            label, value, ratio, c, key, tooltip = row
            row_enter = self.anim.value(f"map.status.item.{dock_key}.{key}", 1.0, self.anim_duration(0.18 + index * 0.045), Easings.ease_out_expo, initial=0.0)
            label_space = 13.0 if minimal else 16.0
            diameter = max(18.0, min(23.0 if minimal else 38.0, rect.height() - 10.0 - label_space, slot_w - 14.0))
            center = QPointF(rect.left() + slot_w * (index + 0.5), rect.top() + 6.0 + diameter / 2.0 + (1.0 - row_enter) * 8.0)
            p.save()
            p.setOpacity(p.opacity() * row_enter)
            self.draw_blackhole_status_disc(p, center, diameter, label, value, ratio, c, key, tooltip, show_label=True, label_side="bottom")
            p.restore()
        p.restore()

    def animated_status_ratio(self, key: str, target_ratio: float) -> tuple[float, float]:
        target_ratio = max(0.0, min(1.0, target_ratio))
        fast = self.anim.value(
            f"map.status.ratio.fast.{key}",
            target_ratio,
            self.anim_duration(0.18),
            Easings.ease_out_cubic,
            initial=target_ratio,
        )
        lag = self.anim.value(
            f"map.status.ratio.lag.{key}",
            target_ratio,
            self.anim_duration(0.62),
            Easings.ease_out_pow5,
            initial=target_ratio,
        )
        return fast, lag

    def draw_blackhole_status_disc(
        self,
        p: QPainter,
        center: QPointF,
        diameter: float,
        label: str,
        value: str,
        ratio: float,
        c: QColor,
        key: str,
        tooltip: str,
        show_label: bool,
        label_side: str,
    ) -> None:
        fast_ratio, lag_ratio = self.animated_status_ratio(key, ratio)
        radius = max(6.0, diameter / 2.0)
        disc_rect = QRectF(center.x() - radius - 6.0, center.y() - radius - 6.0, diameter + 12.0, diameter + 12.0)
        label_probe = disc_rect.adjusted(-4.0, -4.0, 50.0 if label_side == "right" else 24.0, 16.0 if label_side == "bottom" else 4.0)
        hover_v = self.anim.value(
            f"map.status.hover.{key}",
            1.0 if label_probe.contains(self.hover_point) else 0.0,
            self.anim_duration(0.16),
            Easings.ease_out_cubic,
        )
        danger = (label in ("HEAT", "POWER", "SPEED") and fast_ratio > 0.82) or (
            label == "HEAT" and self.heat_over_warn()
        )
        if self.hud_clean():
            if danger or label in ("ENEMY", "EBASE"):
                main = self.hud_danger()
            elif label in ("GOLD", "POWER", "OUT"):
                main = self.hud_warning()
            elif label in ("AMMO",):
                main = self.hud_success()
            else:
                main = c
        else:
            main = THEME.pink if danger else c
        lite = self.high_res_optimize()
        pulse = 0.5 + 0.5 * math.sin(time.perf_counter() * (3.0 if not danger else 5.2) + len(label))
        draw_r = radius + hover_v * 2.0
        arc_rect = QRectF(center.x() - draw_r, center.y() - draw_r, draw_r * 2.0, draw_r * 2.0)

        p.save()
        p.setBrush(with_alpha(main, (0.04 if self.hud_clean() else 0.06 if lite else 0.10 if not danger else 0.18) + (0.025 if self.hud_clean() or lite else 0.08) * pulse + 0.05 * hover_v))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(center, draw_r + (3.0 if lite else 6.0), draw_r + (3.0 if lite else 6.0))
        p.setBrush(color("#000000", 0.78))
        p.drawEllipse(center, draw_r + 2.0, draw_r + 2.0)

        p.setBrush(Qt.BrushStyle.NoBrush)
        stroke = max(1.7, diameter * 0.095)
        p.setPen(QPen(color("#ffffff", 0.11), stroke))
        p.drawEllipse(arc_rect)
        p.setPen(QPen(with_alpha(main, 0.22 + 0.10 * pulse + 0.10 * hover_v), stroke + 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect.adjusted(-1.0, -1.0, 1.0, 1.0), 90 * 16, -int(360 * 16 * lag_ratio))
        p.setPen(QPen(with_alpha(main, 0.78 + 0.18 * pulse), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect, 90 * 16, -int(360 * 16 * fast_ratio))
        sheen_angle = int((time.perf_counter() * 80.0 + len(label) * 13.0) % 360)
        p.setPen(QPen(color("#ffffff", 0.16 + 0.10 * hover_v), max(0.7, stroke * 0.42), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect.adjusted(3.0, 3.0, -3.0, -3.0), sheen_angle * 16, -38 * 16)

        p.setBrush(color("#050509", 0.96))
        p.setPen(QPen(color("#ffffff", 0.08), 0.7))
        p.drawEllipse(center, draw_r * 0.58, draw_r * 0.58)
        p.setBrush(with_alpha(main, 0.58))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(center.x() - draw_r * 0.15, center.y() - draw_r * 0.16), max(1.3, draw_r * 0.09), max(1.3, draw_r * 0.09))

        font_size = max(4.6, min(7.4, diameter * 0.22))
        value_font = self.font("poppins_bold", font_size)
        value_rect = QRectF(center.x() - diameter * 0.64, center.y() - diameter * 0.22, diameter * 1.28, diameter * 0.44)
        self.draw_text(p, value_rect.translated(0.6, 0.7), hud_text(value, chinese_enabled(self.registry)), value_font, color("#000000", 0.72), Qt.AlignmentFlag.AlignCenter)
        self.draw_text(p, value_rect, hud_text(value, chinese_enabled(self.registry)), value_font, THEME.text, Qt.AlignmentFlag.AlignCenter)
        p.restore()

        if show_label:
            self.draw_status_label_capsule(p, center, draw_r, diameter, label, tooltip, main, hover_v, label_side)

        self.add_hotspot(label_probe.adjusted(-4.0, -4.0, 8.0, 8.0), "map_status", key, tooltip=hud_text(tooltip, chinese_enabled(self.registry)))

    def draw_status_label_capsule(
        self,
        p: QPainter,
        center: QPointF,
        radius: float,
        diameter: float,
        label: str,
        tooltip: str,
        main: QColor,
        hover_v: float,
        label_side: str,
    ) -> None:
        short_text = self.compact_map_status_label(label)
        detail_text = tooltip
        label_font = self.font("poppins_medium", max(4.8, min(6.5, diameter * 0.18)))
        short_w = text_width(p, label_font, short_text) + 9.0
        detail_w = text_width(p, label_font, detail_text) + 13.0
        max_w = 92.0 if label_side == "right" else 118.0
        target_w = min(max_w, max(short_w, detail_w))
        capsule_w = short_w + (target_w - short_w) * hover_v
        capsule_h = max(10.0, min(13.0, diameter * 0.34 + 4.0))
        if label_side == "right":
            capsule_rect = QRectF(center.x() + radius + 5.0, center.y() - capsule_h / 2.0, capsule_w, capsule_h)
            text_rect = capsule_rect.adjusted(5.0, 0.0, -4.0, 0.0)
            align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        else:
            capsule_rect = QRectF(center.x() - capsule_w / 2.0, center.y() + radius + 2.5, capsule_w, capsule_h)
            text_rect = capsule_rect.adjusted(3.0, 0.0, -3.0, 0.0)
            align = Qt.AlignmentFlag.AlignCenter

        p.save()
        rounded_rect(
            p,
            capsule_rect,
            capsule_h / 2.0,
            color("#020306", 0.58 + hover_v * 0.16),
            with_alpha(main, 0.18 + hover_v * 0.30),
            0.7,
        )
        shine = QLinearGradient(capsule_rect.topLeft(), capsule_rect.topRight())
        alt = self.hud_accent() if self.hud_clean() else THEME.pink if main == THEME.cyan else THEME.cyan
        shine.setColorAt(0.0, with_alpha(main, 0.36 + 0.16 * hover_v))
        shine.setColorAt(1.0, with_alpha(alt, 0.28 + 0.10 * hover_v))
        if label_side == "right":
            p.fillRect(QRectF(capsule_rect.left() + 1.4, capsule_rect.top() + 2.2, 1.0, capsule_rect.height() - 4.4), shine)
        else:
            p.fillRect(QRectF(capsule_rect.left() + 4.5, capsule_rect.top() + 1.4, capsule_rect.width() - 9.0, 1.0), shine)
        text = detail_text if hover_v > 0.58 and capsule_w > short_w + 22.0 else short_text
        self.draw_text(p, text_rect.translated(0.5, 0.6), hud_text(text, chinese_enabled(self.registry)), label_font, color("#000000", 0.68), align)
        self.draw_text(p, text_rect, hud_text(text, chinese_enabled(self.registry)), label_font, with_alpha(main, 0.94), align)
        p.restore()

    def compact_map_status_label(self, label: str) -> str:
        upper = " ".join(str(label).upper().split())
        if "BASE" in upper:
            return "A BASE" if "YOU" in upper or upper.startswith("ALLY") else "E BASE"
        if "OUTPOST" in upper:
            return "POST"
        return {
            "ALLY": "ALLY",
            "ENEMY": "ENMY",
            "RED YOU": "RED",
            "BLUE YOU": "BLUE",
            "CTRL": "CTRL",
            "GOLD": "GOLD",
            "BASE": "BASE",
            "POWER": "PWR",
            "SPEED": "SPD",
            "EBASE": "EBASE",
            "OUTPOST": "OUT",
            "AMMO": "AMMO",
        }.get(upper, upper[:5])

    def map_status_groups(self) -> tuple[list[tuple[str, str, float, QColor, str, str]], list[tuple[str, str, float, QColor, str, str]]]:
        config = self.match_mode_manager.current
        match_rows: list[tuple[str, str, float, QColor, str, str]] = []
        own_side = self.alliance_side()
        enemy_side = "Blue" if own_side == "Red" else "Red"
        red_vp = self.state.ally_victory_points if own_side == "Red" else self.state.enemy_victory_points
        blue_vp = self.state.enemy_victory_points if own_side == "Red" else self.state.ally_victory_points
        if config.key == "RMUL3v3":
            # `Team Status` 控制标签上不标“YOU”。关掉后两边看起来完全对称，
            # 这是一个内容开关而不是行开关，所以不能走 hud_row_visible。
            tag_side = self._bool_on("VictoryPointStatus", "Team Status")
            red_label = "RED" + (" YOU" if tag_side and own_side == "Red" else "")
            blue_label = "BLUE" + (" YOU" if tag_side and own_side == "Blue" else "")
            if self.hud_row_visible("VictoryPointStatus", "Victory Points"):
                match_rows.extend(
                    [
                        (red_label, str(red_vp), red_vp / 200.0, self.map_team_color("red"), "red_vp", f"RED VP {red_vp}"),
                        (blue_label, str(blue_vp), blue_vp / 200.0, self.map_team_color("blue"), "blue_vp", f"BLUE VP {blue_vp}"),
                    ]
                )
            if self.hud_row_visible("VictoryPointStatus", "Control Zone"):
                match_rows.append(
                    ("CTRL", f"{int(self.state.control_progress)}%", self.state.control_progress / 100.0, self.map_control_color(), "control", f"CONTROL {self.state.control_owner} {self.state.control_progress}%")
                )
            if self.hud_row_visible("EconomyPanel", "Gold"):
                match_rows.append(("GOLD", str(int(self.state.remaining_economy)), min(1.0, self.state.remaining_economy / 400.0), self.hud_state_color("gold"), "gold", self.economy_gold_text()))
        else:
            show_shield = self._bool_on("BaseOutpostStatus", "Shield")
            if self.hud_row_visible("BaseOutpostStatus", "Own Base"):
                match_rows.append(
                    (f"{own_side.upper()} BASE YOU", self.short_ring_number(self.state.base_health), self.state.base_health / max(1, self.state.base_max_health), self.map_team_color("ally"), "base", self.base_row_text(own_side, self.state.base_health, self.state.base_max_health, self.state.base_shield, show_shield))
                )
            if self.hud_row_visible("BaseOutpostStatus", "Enemy Base"):
                match_rows.append(
                    (f"{enemy_side.upper()} BASE", self.short_ring_number(self.state.enemy_base_health), self.state.enemy_base_health / max(1, self.state.enemy_base_max_health), self.map_team_color("enemy"), "enemy_base", self.base_row_text(enemy_side, self.state.enemy_base_health, self.state.enemy_base_max_health, self.state.enemy_base_shield, show_shield))
                )
            if self.hud_row_visible("BaseOutpostStatus", "Outposts"):
                match_rows.append(
                    ("OUTPOST", self.short_outpost_value(), self.state.enemy_outpost_health / max(1, self.state.enemy_outpost_max_health), self.map_team_color("enemy"), "outpost", f"{enemy_side} OUTPOST {self.state.enemy_outpost_status}")
                )
            if self.hud_row_visible("EconomyPanel", "Gold"):
                match_rows.append(("GOLD", str(int(self.state.remaining_economy)), min(1.0, self.state.remaining_economy / 400.0), self.hud_state_color("gold"), "gold", self.economy_gold_text()))
            if self.registry.get("ProjectileShop") and self.registry.get("ProjectileShop").enabled:
                match_rows.append(self.projectile_ammo_row())
        return match_rows[:5], self.map_telemetry_status_rows()

    def base_row_text(self, side: str, hp: int, hp_max: int, shield: int, show_shield: bool) -> str:
        """基地行提示。`BaseOutpostStatus / Shield` 控制带不带护盾。"""
        text = f"{side} BASE {int(hp)}/{int(hp_max)}"
        if show_shield:
            text += f" SHIELD {int(shield)}"
        return text

    def economy_gold_text(self) -> str:
        """金币行提示。`EconomyPanel / Tech Level` 控制带不带科技等级。"""
        text = f"GOLD {int(self.state.remaining_economy)}"
        if self._bool_on("EconomyPanel", "Tech Level"):
            text += f" / T{self.state.tech_level}"
        return text

    def projectile_ammo_value(self) -> tuple[str, float]:
        """按 `ProjectileShop / Ammo Type` 选主显示哪种弹，返回 (显示值, 比例)。"""
        module = self.registry.get("ProjectileShop")
        setting = module.get_setting("Ammo Type") if module is not None else None
        prefer_large = str(getattr(setting, "value", "17mm") if setting is not None else "17mm").strip() == "42mm"
        if prefer_large:
            count = int(self.state.projectile_allowance_42mm)
            return f"42mm {count}", min(1.0, count / 30.0)
        count = int(self.state.projectile_allowance_17mm)
        return f"17mm {count}", min(1.0, count / 180.0)

    def projectile_ammo_row(self) -> tuple[str, str, float, QColor, str, str]:
        """AMMO 行（6 元组版）。`Ammo Type` 选主显示，`Show Cost` 控制带不带价格。

        告警色判据与 `match_status_rows` 的 4 元组版共用同一个
        `projectile_below_warn`：两个构造器不一致会比统一不接更坑。
        """
        value, ratio = self.projectile_ammo_value()
        detail = f"AMMO {value} | 42mm {int(self.state.projectile_allowance_42mm)}"
        if self._bool_on("ProjectileShop", "Show Cost"):
            detail += f" | PRICE {int(self.state.projectile_shop_price)}"
        color_value = (
            self.hud_danger() if self.projectile_below_warn(ratio)
            else self.hud_state_color("ammo")
        )
        return ("AMMO", value, ratio, color_value, "ammo", detail)

    #: 弹药“算低”的比例。`ProjectileAllowance / Warn Low` 是个布尔开关，
    #: 没有配套的阈值设置项，所以这个数是我定的：**需你们确认**。
    #: 写成常量而不是散在代码里的魔数，是为了看得见、改得动。
    AMMO_LOW_RATIO = 0.25

    def projectile_below_warn(self, ratio: float) -> bool:
        """主显示弹种的允许量是否已低到需要告警。

        `Warn Low` 以前没人读：弹药见底时 AMMO 行颜色完全不变。
        开关关闭时一律返回 False（不告警），而不是“默认告警”。
        """
        module = self.registry.get("ProjectileAllowance")
        if module is None or not module.enabled:
            return False
        setting = module.get_setting("Warn Low")
        if setting is None or not bool(getattr(setting, "value", True)):
            return False
        try:
            return 0.0 <= float(ratio) <= self.AMMO_LOW_RATIO
        except (TypeError, ValueError):
            return False

    def telemetry_buffer_text(self) -> str:
        """`ThermalPowerPanel / Buffer`：该面板要不要带缓冲能量。

        模块描述本身就写着“热量、底盘功率、**缓冲能量**和弹丸数量显示”，
        但代码只发了 HEAT/PWR，缓冲能量从没出现过。电容百分比就是那个量。
        """
        module = self.registry.get("ThermalPowerPanel")
        if module is None or not module.enabled:
            return ""
        setting = module.get_setting("Buffer")
        if setting is None or not bool(getattr(setting, "value", True)):
            return ""
        return f"  BUF {self.state.effective_capacitor_percent}%"

    def hud_row_visible(self, module_name: str, show_setting: str | None = None) -> bool:
        """状态行到底画不画：模块开着 **且** 该行自己的开关也开着。

        以前只判 `module.enabled`，于是 `Own Base` / `Enemy Base` / `Outposts` /
        `Victory Points` / `Control Zone` / `Gold` / `Show Speed` 这些开关
        界面上能关，但 HUD 照旧画。

        传 `show_setting=None` 等价于只判模块开关；查不到该设置项时返回 True
        ——不因规格缺失而默认隐藏面板。

        为什么收模块名而不是模块对象：三个构造器（`map_status_groups` /
        `match_status_ring_rows` / `match_status_rows`）都要用同一判据，
        传名字让它们能共用同一份实现而不各自拿 registry 取一遍。
        """
        module = self.registry.get(module_name)
        if module is None or not module.enabled:
            return False
        if show_setting is None:
            return True
        setting = module.get_setting(show_setting)
        if setting is None:
            return True
        return bool(getattr(setting, "value", True))

    def _bool_on(self, module_name: str, setting_name: str, default: bool = True) -> bool:
        """读一个布尔开关（包括模块未开的情况），用于控制行内容而不是行存在。"""
        module = self.registry.get(module_name)
        if module is None:
            return default
        setting = module.get_setting(setting_name)
        if setting is None:
            return default
        return bool(getattr(setting, "value", default))

    def telemetry_panel_visible(self, module, show_setting: str) -> bool:
        """面板到底画不画：模块开着 **且** 它自己的“显示”开关也开着。

        以前只判 `module.enabled`，所以 `Capacitor / Show Bar` 与
        `PowerLimit / Show Limit` 界面上能关，但 HUD 照旧画。
        没有这个设置项时返回 True——不因规格缺失而默认隐藏面板。
        """
        if module is None or not module.enabled:
            return False
        setting = module.get_setting(show_setting)
        if setting is None:
            return True
        return bool(getattr(setting, "value", True))

    def capacitor_below_warn(self) -> bool:
        """电容是否已低到告警阈值。

        阈值有两个可能来源：`Capacitor / Warn` 与 `PowerGuard / Energy Warn`。
        以前两个都是装饰：能拖 0~100%，但 CAP 行永远按固定比例上色。

        两个都在时**取更严（阈值更高、告警更早）的那个**：告警宁早不晚，
        而“哪个说了算”若反过来（取小）则“多开一个保护模块”会变成
        “告警被推后”，那不是保护模块该有的语义。
        两个模块都关着时不告警（= 接线前的行为）。

        注意方向：电容是**越低越危险**，而 `hud_state_color(name, ratio)`
        按“比例越高越危险”处理（功率/温度那些是高为险）。所以不能直接
        把 ratio 传进去，必须显式判一次，否则调高阈值反而不告警。
        """
        thresholds: list[float] = []
        for module_name, setting_name in (("Capacitor", "Warn"), ("PowerGuard", "Energy Warn")):
            module = self.registry.get(module_name)
            if module is None or not module.enabled:
                continue
            try:
                value = float(self.get_setting_number(module, setting_name, 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if value > 0.0:                     # 0 = 该模块主动关掉告警
                thresholds.append(value)
        if not thresholds:
            return False
        try:
            return float(self.state.effective_capacitor_percent) <= max(thresholds)
        except (TypeError, ValueError):
            return False

    #: 热量告警档（与 `hud_state_color("heat", ...)` 的 warning 区间起点一致）。
    #: 用“把比例顶到该档”而不是直接返一个告警色：两条调色路径
    #: （清爽风格与遗留固定色）都能走通，不会混进一套外来的颜色。
    HEAT_WARN_RATIO_FLOOR = 0.78

    def heat_over_warn(self) -> bool:
        """本机热量是否已越过 `PowerGuard / Heat Warn` 的绝对阈值。

        这个开关以前完全没人读：`PowerGuard` 模块打开后，界面上那个
        “热量警告 180”什么都不会发生（模块名字就叫“功率保护”，却没有任何
        可见效果）。接上后：热量达到该绝对值时，热量行至少进入告警色。

        三个关键取向：
          * **只有模块开着才参与**。`PowerGuard` 默认是关的，所以接线本身
            不改变任何人的画面；但一旦有人把它打开，它就必须真的生效。
          * 方向与电容相反：热量是**越高越险**，所以用 `>=`。
          * 只能加严不能放松（见 `heat_display_color`）。
        """
        module = self.registry.get("PowerGuard")
        if module is None or not module.enabled:
            return False
        try:
            threshold = float(self.get_setting_number(module, "Heat Warn", 0.0) or 0.0)
        except (TypeError, ValueError):
            return False
        if threshold <= 0.0:
            return False                        # 0 / 非法值：不强行告警
        try:
            return float(getattr(self.state, "current_heat", 0.0) or 0.0) >= threshold
        except (TypeError, ValueError):
            return False

    def heat_display_color(self, ratio: float) -> QColor:
        """热量行的最终颜色：原有比例分档 + `PowerGuard / Heat Warn` 的绝对阈值。

        用 `max(ratio, 0.78)` 而不是返另一个颜色：比例只能被顶高，所以结果
        永远不比对阈值时更不显眼（已在 danger 档就留在 danger 档）。
        """
        if not self.heat_over_warn():
            return self.hud_state_color("heat", ratio)
        return self.hud_state_color("heat", max(ratio, self.HEAT_WARN_RATIO_FLOOR))

    def map_telemetry_status_rows(self) -> list[tuple[str, str, float, QColor, str, str]]:
        rows: list[tuple[str, str, float, QColor, str, str]] = []
        thermal = self.registry.get("ThermalPowerPanel")
        if thermal and thermal.enabled:
            ratio = self.state.current_heat / max(1.0, self.state.max_heat)
            rows.append(("HEAT", str(int(self.state.current_heat)), ratio, self.heat_display_color(ratio), "heat", f"HEAT {int(self.state.current_heat)}/{int(self.state.max_heat)}"))
        power = self.registry.get("PowerLimit")
        if self.telemetry_panel_visible(power, "Show Limit"):
            limit = max(1.0, self.get_setting_number(power, "Limit", float(self.state.power_limit)))
            ratio = self.state.chassis_power_w / limit
            rows.append(("POWER", str(int(self.state.chassis_power_w)), ratio, self.hud_state_color("power", ratio), "power", f"POWER {int(self.state.chassis_power_w)} / {int(limit)}W"))
        speed = self.registry.get("SpeedLimit")
        if self.telemetry_panel_visible(speed, "Show Speed"):
            limit = max(1.0, self.get_setting_number(speed, "Limit", float(self.state.speed_limit)))
            unit_setting = speed.get_setting("Unit")
            unit = str(unit_setting.value) if unit_setting else "dps"
            ratio = self.state.speed_limit / limit
            rows.append(("SPEED", str(int(self.state.speed_limit)), ratio, self.hud_state_color("speed", ratio), "speed", f"SPEED {int(self.state.speed_limit)} {unit}"))
        capacitor = self.registry.get("Capacitor")
        if self.telemetry_panel_visible(capacitor, "Show Bar"):
            percent = self.state.effective_capacitor_percent
            ratio = percent / 100.0
            # 低于 Warn 阈值时强制告警色，而不是跟着比例走
            cap_color = self.hud_danger() if self.capacitor_below_warn() else self.hud_state_color("cap", ratio)
            rows.append(("CAP", f"{percent}%", ratio, cap_color, "cap", f"CAP {percent}%"))
        return rows[:4]

    def draw_match_status_lines(self, p: QPainter, rect: QRectF) -> None:
        rows = self.match_status_rows(compact=True)[:3]
        if not rows:
            return
        gap = 2.0
        line_h = max(1.4, min(2.4, (rect.height() - gap * (len(rows) - 1)) / max(1, len(rows))))
        y = rect.top()
        for _label, _value, ratio, c in rows:
            draw_bar(p, QRectF(rect.left(), y, rect.width(), line_h), ratio, c, color("#ffffff", 0.12), line_h / 2.0)
            y += line_h + gap

    def draw_match_status_stack(self, p: QPainter, rect: QRectF, compact: bool = False) -> None:
        rows = self.match_status_rows(compact=compact)
        if not rows:
            return
        if self.hud_clean():
            self.draw_hud_liquid_glass(p, rect, 7, surface="SubGlass")
        else:
            rounded_rect(p, rect, 7, self.hud_surface(0.40 if compact else 0.34), self.hud_panel_outline(0.07))
        row_h = rect.height() / max(1, len(rows))
        for index, (label, value, ratio, c) in enumerate(rows):
            y = rect.top() + index * row_h
            label_font = self.font("poppins_medium", 5.6 if compact else 6.8)
            value_font = self.font("poppins_medium", 5.6 if compact else 6.9)
            left_pad = 6 if compact else 8
            main = self.liquid_status_color(label, ratio, c)
            self.draw_text(p, QRectF(rect.left() + left_pad, y, rect.width() * 0.38, row_h), hud_text(label, chinese_enabled(self.registry)), label_font, with_alpha(main, 0.88))
            self.draw_text(p, QRectF(rect.left() + rect.width() * 0.43, y, rect.width() * 0.52, row_h), hud_text(value, chinese_enabled(self.registry)), value_font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            draw_bar(p, QRectF(rect.left() + left_pad, y + row_h - (3.0 if compact else 4.0), rect.width() - left_pad * 2, 1.8 if compact else 2.4), ratio, c, color("#ffffff", 0.10), 1.2)

    def draw_match_status_rings(self, p: QPainter, rect: QRectF, compact: bool = False) -> None:
        max_items = max(1, min(4, int(rect.width() // (46.0 if compact else 58.0))))
        rows = self.match_status_ring_rows()[:max_items]
        if not rows:
            return
        rounded_rect(p, rect, 8, self.hud_surface(0.24 if compact else 0.28), self.hud_panel_outline(0.05))
        slot_w = rect.width() / len(rows)
        ring_d = max(18.0, min(28.0 if compact else 34.0, rect.height() - (12.0 if compact else 15.0), slot_w - 14.0))
        radius = ring_d / 2.0
        top = rect.top() + 5.0
        pulse = 0.5 + 0.5 * math.sin(time.perf_counter() * 3.0)
        for index, (label, value, ratio, c) in enumerate(rows):
            ratio = max(0.0, min(1.0, ratio))
            cx = rect.left() + slot_w * (index + 0.5)
            center = QPointF(cx, top + radius)
            arc_rect = QRectF(center.x() - radius, center.y() - radius, ring_d, ring_d)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color("#ffffff", 0.12), max(1.4, ring_d * 0.085)))
            p.drawEllipse(arc_rect)
            p.setPen(QPen(with_alpha(c, 0.72 + 0.18 * pulse), max(1.6, ring_d * 0.095), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc_rect, 90 * 16, -int(360 * 16 * ratio))
            p.setBrush(color("#050507", 0.80))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(center, max(5.6, ring_d * 0.27), max(5.6, ring_d * 0.27))
            self.draw_text(p, QRectF(center.x() - slot_w / 2.0, center.y() - 6.0, slot_w, 12.0), hud_text(value, chinese_enabled(self.registry)), self.font("poppins_bold", 6.2 if compact else 7.0), THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.draw_text(p, QRectF(center.x() - slot_w / 2.0, rect.bottom() - (11.0 if compact else 13.0), slot_w, 10.0), hud_text(label, chinese_enabled(self.registry)), self.font("poppins_medium", 5.6 if compact else 6.3), with_alpha(c, 0.92), Qt.AlignmentFlag.AlignCenter)

    def match_status_ring_rows(self) -> list[tuple[str, str, float, QColor]]:
        config = self.match_mode_manager.current
        rows: list[tuple[str, str, float, QColor]] = []
        if config.key == "RMUL3v3":
            if self.hud_row_visible("VictoryPointStatus", "Victory Points"):
                rows.extend(
                    [
                        ("ALLY", str(self.state.ally_victory_points), self.state.ally_victory_points / 200.0, self.hud_state_color("ally")),
                        ("ENEMY", str(self.state.enemy_victory_points), self.state.enemy_victory_points / 200.0, self.hud_state_color("enemy")),
                    ]
                )
            if self.hud_row_visible("VictoryPointStatus", "Control Zone"):
                rows.append(
                    ("CTRL", f"{int(self.state.control_progress)}%", self.state.control_progress / 100.0, self.hud_state_color("control"))
                )
            if self.hud_row_visible("ThermalPowerPanel", "Heat"):
                ratio = self.state.current_heat / max(1.0, self.state.max_heat)
                rows.append(("HEAT", f"{int(self.state.current_heat)}", ratio, self.heat_display_color(ratio)))
            return rows[:4]

        if self.hud_row_visible("BaseOutpostStatus", "Own Base"):
            rows.append(
                ("BASE", self.short_ring_number(self.state.base_health), self.state.base_health / max(1, self.state.base_max_health), self.hud_state_color("ally"))
            )
        if self.hud_row_visible("BaseOutpostStatus", "Enemy Base"):
            rows.append(
                ("EBASE", self.short_ring_number(self.state.enemy_base_health), self.state.enemy_base_health / max(1, self.state.enemy_base_max_health), self.hud_state_color("enemy"))
            )
        if self.hud_row_visible("BaseOutpostStatus", "Outposts"):
            rows.append(
                ("OUT", self.short_outpost_value(), self.state.enemy_outpost_health / max(1, self.state.enemy_outpost_max_health), self.hud_state_color("warning"))
            )
        if self.hud_row_visible("EconomyPanel", "Gold"):
            rows.append(("GOLD", str(int(self.state.remaining_economy)), min(1.0, self.state.remaining_economy / 400.0), self.hud_state_color("gold")))
        if len(rows) < 4 and self.registry.get("ProjectileShop") and self.registry.get("ProjectileShop").enabled:
            value, ratio = self.projectile_ammo_value()
            rows.append(("AMMO", value, ratio, self.hud_state_color("ammo")))
        if len(rows) < 4 and self.hud_row_visible("ThermalPowerPanel", "Heat"):
            ratio = self.state.current_heat / max(1.0, self.state.max_heat)
            rows.append(("HEAT", f"{int(self.state.current_heat)}", ratio, self.heat_display_color(ratio)))
        return rows[:4]

    def match_status_rows(self, compact: bool = False) -> list[tuple[str, str, float, QColor]]:
        config = self.match_mode_manager.current
        rows: list[tuple[str, str, float, QColor]] = []
        if config.key == "RMUL3v3":
            if self.hud_row_visible("VictoryPointStatus", "Victory Points"):
                rows.extend(
                    [
                        ("ALLY VP", str(self.state.ally_victory_points), self.state.ally_victory_points / 200.0, self.hud_state_color("ally")),
                        ("ENEMY VP", str(self.state.enemy_victory_points), self.state.enemy_victory_points / 200.0, self.hud_state_color("enemy")),
                    ]
                )
            if self.hud_row_visible("VictoryPointStatus", "Control Zone"):
                rows.append(
                    ("CONTROL", f"{self.state.control_owner} {self.state.control_progress}%", self.state.control_progress / 100.0, self.hud_state_color("control"))
                )
        else:
            if self.hud_row_visible("BaseOutpostStatus", "Own Base"):
                rows.append(
                    ("OWN BASE", f"{self.state.base_health}/{self.state.base_max_health}", self.state.base_health / max(1, self.state.base_max_health), self.hud_state_color("ally"))
                )
            if self.hud_row_visible("BaseOutpostStatus", "Enemy Base"):
                rows.append(
                    ("ENEMY BASE", f"{self.state.enemy_base_health}/{self.state.enemy_base_max_health}", self.state.enemy_base_health / max(1, self.state.enemy_base_max_health), self.hud_state_color("enemy"))
                )
            if self.hud_row_visible("BaseOutpostStatus", "Outposts"):
                rows.append(
                    ("OUTPOST", f"{self.state.enemy_outpost_status}", self.state.enemy_outpost_health / max(1, self.state.enemy_outpost_max_health), self.hud_state_color("warning"))
                )
        if self.hud_row_visible("EconomyPanel", "Gold"):
            rows.append(("GOLD", self.economy_gold_text(), min(1.0, self.state.remaining_economy / 400.0), self.hud_state_color("gold")))
        if self.registry.get("ProjectileShop") and self.registry.get("ProjectileShop").enabled:
            value, ammo_ratio = self.projectile_ammo_value()
            ammo_color = (
                self.hud_danger() if self.projectile_below_warn(ammo_ratio)
                else self.hud_state_color("ammo")
            )
            rows.append(("AMMO", f"{value} | 42 {self.state.projectile_allowance_42mm}", ammo_ratio, ammo_color))
        if self.hud_row_visible("ThermalPowerPanel", "Heat"):
            ratio = self.state.current_heat / max(1.0, self.state.max_heat)
            rows.append(("HEAT/PWR", f"{int(self.state.current_heat)}/{int(self.state.max_heat)}  {int(self.state.chassis_power_w)}W{self.telemetry_buffer_text()}", ratio, self.heat_display_color(ratio)))
        if compact:
            compact_labels = {
                "ALLY VP": "ALLY",
                "ENEMY VP": "ENEMY",
                "CONTROL": "CTRL",
                "OWN BASE": "BASE",
                "ENEMY BASE": "E.BASE",
                "OUTPOST": "OUT",
                "GOLD": "GOLD",
                "HEAT/PWR": "HEAT",
            }
            rows = [(compact_labels.get(label, label), value, ratio, c) for label, value, ratio, c in rows[:3]]
        else:
            rows = rows[:4]
        return rows

    def draw_telemetry_strip(self, p: QPainter, width: int, height: int) -> None:
        cards: list[tuple[str, str, float, QColor, str]] = []
        thermal = self.registry.get("ThermalPowerPanel")
        if thermal and thermal.enabled:
            heat = self.animated_scalar("telemetry.heat", self.state.current_heat, 20.0)
            heat_ratio = heat / max(1.0, self.state.max_heat)
            cards.append(("HEAT", f"{int(round(heat))}/{int(self.state.max_heat)}", heat_ratio, self.heat_display_color(heat_ratio), "ThermalPowerPanel"))
        power = self.registry.get("PowerLimit")
        if self.telemetry_panel_visible(power, "Show Limit"):
            limit = max(1.0, self.get_setting_number(power, "Limit", float(self.state.power_limit)))
            power_value = self.animated_scalar("telemetry.power", self.state.chassis_power_w, 18.0)
            ratio = power_value / limit
            cards.append(("POWER", f"{int(round(power_value))} / {int(limit)}W", ratio, self.hud_state_color("power", ratio), "PowerLimit"))
        speed = self.registry.get("SpeedLimit")
        if self.telemetry_panel_visible(speed, "Show Speed"):
            limit = max(1.0, self.get_setting_number(speed, "Limit", float(self.state.speed_limit)))
            unit_setting = speed.get_setting("Unit")
            unit = str(unit_setting.value) if unit_setting else "dps"
            speed_value = self.animated_scalar("telemetry.speed", self.state.speed_limit, 16.0)
            ratio = speed_value / max(1.0, limit)
            cards.append(("SPEED", f"{int(round(speed_value))} {unit}", ratio, self.hud_state_color("speed", ratio), "SpeedLimit"))
        capacitor = self.registry.get("Capacitor")
        if self.telemetry_panel_visible(capacitor, "Show Bar"):
            cap_value = self.animated_scalar("telemetry.cap", self.state.effective_capacitor_percent, 18.0)
            ratio = cap_value / 100.0
            # 与 map_telemetry_status_rows 保持同一套开关与告警判据：
            # 两处构造器不一致会比统一不接更坑（同一个滑块在一处有效一处无效）。
            cap_color = self.hud_danger() if self.capacitor_below_warn() else self.hud_state_color("cap", ratio)
            cards.append(("CAP", f"{int(round(cap_value))}%", ratio, cap_color, "Capacitor"))
        cards = cards[:4]
        if not cards:
            return
        if self.hud_density() != "DebugFull":
            self.draw_compact_telemetry_strip(p, width, height, cards)
            return

        card_w = 118.0
        card_h = 34.0
        gap = 8.0
        total_w = len(cards) * card_w + (len(cards) - 1) * gap
        x = max(260.0, min(width - total_w - 270.0, width / 2 - total_w / 2))
        y = height - 62.0
        for index, (label, value, ratio, c, key) in enumerate(cards):
            active = self.anim.value(f"hud.telemetry.{key}", 1.0, self.anim_duration(0.20 + index * 0.02), Easings.ease_out_pow4, initial=0.0)
            # 脉必须用**钳位前**的原始比例算：超限时 ratio > 1，而下面传给
            # draw_bar 的是 max(0,min(1,ratio))——拿钳位后的值去判“超限”，
            # 永远只能拿到 1.0，脉冲就永远不会触发。紧凑路径同理。
            pulse = self.telemetry_pulse_strength(key, ratio)
            bar_color = self.hud_danger() if pulse > 0.5 else c
            rect = QRectF(x + index * (card_w + gap), y + (1.0 - active) * 12.0, card_w, card_h)
            p.save()
            p.setOpacity(active)
            if self.hud_clean():
                self.draw_hud_liquid_glass(p, rect, 7, surface="SubGlass")
            else:
                glow_rect(p, rect, 8, self.telemetry_glow_amount(key))
                rounded_rect(p, rect, 7, self.hud_surface(0.68), self.hud_panel_outline(0.09))
            self.draw_text(p, QRectF(rect.left() + 9, rect.top() + 5, 48, 12), hud_text(label, chinese_enabled(self.registry)), self.font("poppins_medium", 7.0), THEME.muted)
            self.draw_text(p, QRectF(rect.left() + 46, rect.top() + 4, rect.width() - 55, 13), hud_text(value, chinese_enabled(self.registry)), self.font("poppins_bold", 7.3), THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            draw_bar(p, QRectF(rect.left() + 9, rect.bottom() - 10, rect.width() - 18, 4.2), max(0.0, min(1.0, ratio)), bar_color, color("#ffffff", 0.13), 2.1 + 1.2 * pulse)
            p.restore()

    TELEMETRY_GLOW_BASE = 0.11

    def telemetry_glow_amount(self, key: str) -> float:
        """卡片外发光量。`Capacitor / Use Glow` 只管 CAP 卡，不抢别人的卡。

        局限（如实记录，不自接效果）：只有 DebugFull 卡片路径会画每卡通光；
        紧凑路径本来就不画，所以在紧凑下这个开关看不出差别。这不是“没接”，
        而是没有可作用的表面——不去编一个“关了就调暗”的效果来假称接好了。
        """
        if key == "Capacitor" and not self._bool_on("Capacitor", "Use Glow"):
            return 0.0
        return self.TELEMETRY_GLOW_BASE

    def telemetry_pulse_strength(self, key: str, ratio: float) -> float:
        """超限脉冲强度 0..1。`PowerLimit / Pulse Overrun` 只在功率真的超限时动。

        方向：功率是**越高越险**，所以超限（ratio > 1）才脉冲；
        未超限、开关关闭、或非功率卡时一律回 0（画面与接线前一致）。
        """
        if key != "PowerLimit":
            return 0.0
        try:
            over = float(ratio) - 1.0
        except (TypeError, ValueError):
            return 0.0
        if over <= 0.0:
            return 0.0
        if not self._bool_on("PowerLimit", "Pulse Overrun"):
            return 0.0
        # 超限 25% 到达满幅；再乘正弦做实际闪烁
        magnitude = min(1.0, over / 0.25)
        wave = 0.5 + 0.5 * math.sin(time.perf_counter() * 4.5)
        return max(0.0, min(1.0, magnitude * wave))

    def draw_compact_telemetry_strip(self, p: QPainter, width: int, height: int, cards: list[tuple[str, str, float, QColor, str]]) -> None:
        strip_w = min(420.0, max(300.0, width * 0.34))
        strip_h = 28.0
        x = width / 2.0 - strip_w / 2.0
        y = height - 42.0
        active = self.anim.value("hud.telemetry.compact", 1.0, self.anim_duration(0.22), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        strip_rect = QRectF(x, y, strip_w, strip_h)
        if self.hud_clean():
            self.draw_hud_liquid_glass(p, strip_rect, 8, surface="SubGlass")
        else:
            rounded_rect(p, strip_rect, 8, self.hud_surface(0.50), self.hud_panel_outline(0.07))
        gap = 9.0
        seg_w = (strip_w - 18.0 - gap * (len(cards) - 1)) / max(1, len(cards))
        seg_x = x + 9.0
        for label, value, ratio, c, key in cards:
            # 同上：脉冲必须用钳位前的原始 ratio，否则超限判不出来。
            pulse = self.telemetry_pulse_strength(key, ratio)
            bar_color = self.hud_danger() if pulse > 0.5 else c
            ratio = max(0.0, min(1.0, ratio))
            row_active = self.anim.value(f"hud.telemetry.compact.{key}", 1.0, self.anim_duration(0.16), Easings.ease_out_pow4, initial=0.0)
            p.setOpacity(active * row_active)
            self.draw_text(p, QRectF(seg_x, y + 4, seg_w * 0.44, 10), hud_text(label, chinese_enabled(self.registry)), self.font("poppins_medium", 6.4), THEME.muted)
            self.draw_text(p, QRectF(seg_x + seg_w * 0.38, y + 4, seg_w * 0.62, 10), hud_text(value.replace(" / ", "/"), chinese_enabled(self.registry)), self.font("poppins_medium", 6.5), THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # `Pulse Overrun` 在两条路径上行为必须一致：紧凑模式是默认视图，
            # 只改 DebugFull 等于开关在大多数时候看不出效果。
            draw_bar(p, QRectF(seg_x, y + 18, seg_w, 3.2), ratio, bar_color, color("#ffffff", 0.12), 1.6 + 1.0 * pulse)
            seg_x += seg_w + gap
        p.restore()


# ---------------------------------------------------------------------------
# API 冻结面 qualname 固定（见模块 docstring「冻结面纪律」；阶段 2a 先例：
# renderers/combat_target_hud.py 底部同一手法）。
# ---------------------------------------------------------------------------

_QUALNAME_PINNED_MEMBERS = (
    TacticalMapMixin.draw_right_hud_mode,
    TacticalMapMixin.queue_gpu_module_list,
    TacticalMapMixin.draw_module_list_for_tactical_map,
    TacticalMapMixin.draw_match_header,
    TacticalMapMixin.draw_tactical_map_hud,
    TacticalMapMixin.tactical_map_rect,
    TacticalMapMixin.tactical_map_morph_progress,
    TacticalMapMixin.tactical_map_hold_amount,
    TacticalMapMixin.publish_map_click_from_screen,
    TacticalMapMixin.open_event_command_menu,
    TacticalMapMixin.tactical_map_chrome,
    TacticalMapMixin.tactical_map_status_display,
    TacticalMapMixin.effective_tactical_map_status_display,
    TacticalMapMixin.tactical_map_status_kind,
    TacticalMapMixin.tactical_map_footer_height,
    TacticalMapMixin.liquid_status_footer_height,
    TacticalMapMixin.draw_liquid_tactical_map_hud,
    TacticalMapMixin.official_map_fit_rect,
    TacticalMapMixin.draw_liquid_map_robots,
    TacticalMapMixin.draw_liquid_mini_status_overlay,
    TacticalMapMixin.draw_liquid_mini_status_chip,
    TacticalMapMixin.liquid_entity_color,
    TacticalMapMixin.map_team_color,
    TacticalMapMixin.map_control_color,
    TacticalMapMixin.draw_liquid_radar_pointer,
    TacticalMapMixin.draw_liquid_status_dock,
    TacticalMapMixin.draw_liquid_status_row,
    TacticalMapMixin.draw_liquid_status_item,
    TacticalMapMixin.liquid_status_color,
    TacticalMapMixin.draw_liquid_status_bar,
    TacticalMapMixin.draw_liquid_status_card,
    TacticalMapMixin.draw_official_map,
    TacticalMapMixin._map_fit_cuts_panel_corners,
    TacticalMapMixin._rounded_map_layer,
    TacticalMapMixin.official_map_image,
    TacticalMapMixin.official_map_display_image,
    TacticalMapMixin.map_point,
    TacticalMapMixin.draw_map_zones,
    TacticalMapMixin.draw_map_robots,
    TacticalMapMixin.draw_map_markers,
    TacticalMapMixin.draw_map_status_orbit_dock,
    TacticalMapMixin.draw_blackhole_disc_row,
    TacticalMapMixin.animated_status_ratio,
    TacticalMapMixin.draw_blackhole_status_disc,
    TacticalMapMixin.draw_status_label_capsule,
    TacticalMapMixin.compact_map_status_label,
    TacticalMapMixin.map_status_groups,
    TacticalMapMixin.map_telemetry_status_rows,
    TacticalMapMixin.draw_match_status_lines,
    TacticalMapMixin.draw_match_status_stack,
    TacticalMapMixin.draw_match_status_rings,
    TacticalMapMixin.match_status_ring_rows,
    TacticalMapMixin.match_status_rows,
    TacticalMapMixin.draw_telemetry_strip,
    TacticalMapMixin.draw_compact_telemetry_strip,
)

for _fn in _QUALNAME_PINNED_MEMBERS:
    _fn.__qualname__ = f"PolarRMWindow.{_fn.__name__}"
del _fn, _QUALNAME_PINNED_MEMBERS
