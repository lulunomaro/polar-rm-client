"""阶段 2b 渲染器提取：目标锁定 / 自瞄 FOV / ESP 线框族（D8）→ EspWireframeMixin。

（REFACTOR_BLUEPRINT §5 阶段 2 表 2b 行 / §5.6 冻结面 / §6 性能红线）

迁移范围（以 grep 重定位后的实际行号为准，蓝图原行号 5283–5429 / 7244–7545 /
7551–8186 已因多轮修复漂移）：
- FOV/锁定判定：``autoaim_logical_size`` / ``autoaim_circle_radius`` /
  ``sync_local_autoaim_fov`` / ``armor_center_in_autoaim_fov`` /
  ``vision_target_in_autoaim_fov`` / ``apply_autoaim_fov_lock`` /
  ``vision_result_for_display`` / ``interpolated_vision_result`` /
  ``vision_result_calibrated``
- ESP 绘制：``draw_target_overlay``、荧光白族 ``draw_fluorescent_white_segments`` /
  ``draw_fluorescent_white_polyline`` / ``rasterize_fluorescent_white_circle`` /
  ``draw_fluorescent_white_ellipse``、``draw_armor_fov_circle``、三种锁框
  ``draw_white_outline_lock`` / ``draw_minimal_bracket_lock`` / ``draw_rune_ring_lock``、
  ``target_overlay_label``、``armor_esp_points`` / ``armor_esp_back_points`` /
  ``draw_3d_esp_box`` / ``constrain_prediction_geometry`` / ``draw_clean_3d_esp_box`` /
  ``draw_polar_mechanical_wire``、GPU 批量线框 ``_vision_wire_key`` /
  ``_append_vision_wire_armor`` / ``build_vision_wire_batch`` /
  ``paint_gpu_vision_layer``、``draw_armor_quad`` / ``smooth_armor_geometry`` /
  ``draw_armor_3d_esp``

纪律与偏差决策（中文记录）：
- 方法体**零修改**（含注释与空行），纯移动进本 Mixin；
- ``has_valid_combat_target`` 属 D8，但被阶段 2a 判定**留在 polar_app.py 原地**
  （跨域使用：CombatTargetHudMixin.paint_combat_target_overlay 经 self 调用）——
  本 Mixin 不承载它；
- ``draw_pill`` 不在蓝图 §2 D8 清单内（属 D5 legacy CPU GUI 家族，被多域复用），
  按边界保守原则留在 polar_app.py；本 Mixin 经 self 继续访问，调用面不变；
- ``alliance_side`` 虽紧邻 D8 区段，但属 D7 同济桥域，同样留在原地；
- ``target_overlay_label`` 仅被 D8 锁框族调用，随域迁入并钉回 qualname；
- 状态字段（``_fluorescent_circle_cache`` / ``_armor_geometry_smooth`` /
  ``_vision_render_result`` / ``_vision_snapshot`` / ``_jello_esp_shadow`` /
  ``_bottom_left_diagnostics`` / ``vision_wire_gpu`` / ``vision_render_interpolator``）
  全部仍在 ``PolarRMWindow.__init__`` 原位置初始化（Mixin 只承载方法，不承载构造）；
- ``rasterize_fluorescent_white_circle`` 的有界缓存淘汰策略是 §6 性能红线的
  现成范本，key 生成逻辑一字未改随方法整体搬迁。

冻结面纪律：与阶段 1（text_metrics.py 底部）/ 阶段 2a（combat_target_hud.py 底部）
一致——api_freeze_test 对方法类成员比对 ``__qualname__``（基线值形如
"PolarRMWindow.<name>"），故在本模块底部把全部迁入方法的 ``__qualname__`` 钉回
``PolarRMWindow.<name>``，使 ``window.<name>`` 签名与迁移前 bit 级一致
（含两个 @staticmethod：constrain_prediction_geometry 被 tests/smoke_test.py 以
``PolarRMWindow.constrain_prediction_geometry`` 直调）。
"""

from __future__ import annotations

import math
import time
from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QConicalGradient,
    QColor,
    QImage,
    QPainter,
    QPen,
    QPolygonF,
)

# 相对导入层级：rm_client/ui/polar/renderers/ → .... = rm_client、... = rm_client.ui
from ....model import Module
from ....vision import VisionResult, VisionTarget
from ...paint import draw_bar, rounded_rect, text_width, with_alpha
# 注意：text_metrics 与本模块同属 ui/polar 子包（阶段 1 迁入），故为单级 ..
from ..text_metrics import painter_device_scale
from ...theme import color
from ...jello_locale import chinese_enabled
from ...hud_locale import hud_template, HudDisplayText
from ...vision_wire_gpu import VisionWireBatch, armor_corner_segments

__all__ = [
    "EspWireframeMixin",
]


def _draw_localized_pill(host, painter, point, raw_text, display_text, tint):
    """Keep the shared pill's original bounds; translate its text only."""
    font = host.font("poppins_medium", 8.0)
    painter.setFont(font)
    width = max(92.0, host.text_advance(font, raw_text) + 18.0)
    rect = QRectF(point.x(), point.y(), width, 20)
    rounded_rect(painter, rect, 7, color("#090a0d", 0.86), tint, 1.1)
    host.draw_text(painter, rect.adjusted(8, 0, -8, 0), display_text, font, tint)


def _distance_label(raw_text, registry):
    return hud_template("DIST {value}", chinese_enabled(registry), value=raw_text[5:])


def _display_armor_points(host, points, result, width, height):
    """Video projection for drawing only; keep the FOV/selector contract intact.

    Normal camera video fills the canvas. Native Hero video is aspect-fit;
    YOLO/bridge quads are already in source pixels, not the model's 640 pad.
    Both CPU logical HUD sizes and GPU canvas sizes use this same mapping.
    """
    native_cut = getattr(host, "hero_deploy_video_cut_active", None)
    if native_cut is None or not native_cut():
        return host.armor_esp_points(points, result, width, height)
    source_w = max(1.0, float(result.source_width))
    source_h = max(1.0, float(result.source_height))
    scale = min(width / source_w, height / source_h)
    left = (width - source_w * scale) * 0.5
    top = (height - source_h * scale) * 0.5
    # Native 0x0310 is drawn unmirrored by draw_hero_native_background;
    # CameraFeed's mirror setting only applies to the normal video path.
    return [QPointF(left + x * scale, top + y * scale)
            for x, y in points]


class EspWireframeMixin:
    """D8 目标锁定 / 自瞄 FOV / ESP 线框绘制族（CPU QPainter + GPU 批量线框）。"""

    def autoaim_logical_size(self) -> tuple[float, float]:
        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        scale = max(0.001, self.get_hud_scale(int(width), int(height)))
        return width / scale, height / scale

    def autoaim_circle_radius(self) -> float:
        """Logical-pixel FOV radius from ArmorAutoAim Circle Size."""
        module = self.registry.get("ArmorAutoAim")
        if module is None or not module.enabled:
            return 0.0
        return max(8.0, self.get_setting_number(module, "Circle Size", 160.0))

    def sync_local_autoaim_fov(self, source_width: int = 0, source_height: int = 0) -> None:
        """Push FOV ellipse into local YOLO selector (source pixel space)."""
        module = self.registry.get("ArmorAutoAim")
        if module is None or not module.enabled:
            self.local_vision.configure_autoaim_fov(None)
            return
        logical_w, logical_h = self.autoaim_logical_size()
        radius = self.autoaim_circle_radius()
        frame_w = max(1, int(source_width) if source_width > 0 else int(round(logical_w)))
        frame_h = max(1, int(source_height) if source_height > 0 else int(round(logical_h)))
        # Map HUD circle into source so the on-screen FOV matches lock gate.
        radius_x = radius * (frame_w / max(1.0, logical_w))
        radius_y = radius * (frame_h / max(1.0, logical_h))
        self.local_vision.configure_autoaim_fov(radius_x, radius_y)

    def armor_center_in_autoaim_fov(self, armor, result: VisionResult) -> bool:
        """True when armor plate center lies inside the fluorescent FOV circle."""
        module = self.registry.get("ArmorAutoAim")
        if module is None or not module.enabled:
            return True
        if armor is None or len(getattr(armor, "points", ())) < 2:
            return False
        logical_w, logical_h = self.autoaim_logical_size()
        radius = self.autoaim_circle_radius()
        points = self.armor_esp_points(armor.points, result, int(round(logical_w)), int(round(logical_h)))
        if not points:
            return False
        cx = sum(point.x() for point in points) / len(points)
        cy = sum(point.y() for point in points) / len(points)
        if math.hypot(cx - logical_w * 0.5, cy - logical_h * 0.5) > radius:
            return False
        # 第二道门槛：ArmorAutoAim 的 `Fov`（角度）。两道取交集（都满足才算）。
        return self.armor_center_in_autoaim_fov_angle(armor, result)

    def autoaim_fov_radius_source_px(self) -> float | None:
        """把 ArmorAutoAim 的 `Fov`（度）换算成源图像像素半径；不能换算时返回 None。

        这个设置项曾经是**纯装饰**：界面上能拖 10~180 度，但全仓没人读它，
        实际锁定范围只由像素半径 `Circle Size` 决定。

        换算用针孔模型的精确关系 `半径 = fx × tan(fov/2)`，fx 来自相机内参
        ——就是 vision.py:254 求 aim_yaw_deg 用的同一个 fx，不是拍脑袋的比例。

        **拿不到内参时返回 None，含义是“不加这道门槛”**，而不是猜一个换算系数。
        未标定就瞎换算，等于用一个编出来的数字去改变瞄准行为，比赛客户端
        不能这么做。返回 None 时行为与接线前完全一致（只有像素圆生效）。
        """

        module = self.registry.get("ArmorAutoAim")
        if module is None or not module.enabled:
            return None
        fov_deg = float(self.get_setting_number(module, "Fov", 0.0) or 0.0)
        # <=0 或 >=360 都不是有意义的视场角（设置项范围 10..180）
        if fov_deg <= 0.0 or fov_deg >= 360.0:
            return None
        try:
            profile = getattr(self.calibration, "profile", None)
            matrix = list(getattr(profile, "camera_matrix", []) or [])
            fx = float(matrix[0]) if matrix else 0.0
        except (TypeError, ValueError, IndexError):
            return None
        if fx <= 0.0:
            return None
        # `Fov` 读作**全宽**角度（上限 180 = 整幅画面），所以取半角。
        return fx * math.tan(math.radians(fov_deg) * 0.5)

    def armor_center_in_autoaim_fov_angle(self, armor, result: VisionResult) -> bool:
        """装甲板中心是否落在 `Fov` 度数的锥形内（在**源像素**空间量）。

        必须在源像素空间算：fx 是对源图像定义的，换算成 HUD 逻辑像素反而
        多一层缩放误差。`armor.points` 本身就是源坐标，所以这里不需要变换。
        """

        radius_px = self.autoaim_fov_radius_source_px()
        if radius_px is None:
            return True  # 不能换算 -> 不加这道门（见上面的说明）
        if armor is None or not getattr(armor, "points", ()):
            return False
        src_w = int(getattr(result, "source_width", 0) or 0)
        src_h = int(getattr(result, "source_height", 0) or 0)
        if src_w <= 0 or src_h <= 0:
            return True
        # 内参只对标定时的分辨率成立；分辨率不匹配时换算无效，宁可不限制。
        try:
            if not self.calibration.is_valid_for(src_w, src_h):
                return True
        except (AttributeError, TypeError):
            return True
        cx = sum(float(point[0]) for point in armor.points) / len(armor.points)
        cy = sum(float(point[1]) for point in armor.points) / len(armor.points)
        return math.hypot(cx - src_w * 0.5, cy - src_h * 0.5) <= radius_px

    def vision_target_in_autoaim_fov(self, target: VisionTarget, width: int, height: int) -> bool:
        module = self.registry.get("ArmorAutoAim")
        if module is None or not module.enabled:
            return True
        radius = self.autoaim_circle_radius()
        cx = target.x + target.w * 0.5
        cy = target.y + target.h * 0.5
        return math.hypot(cx - width * 0.5, cy - height * 0.5) <= radius

    #: 上一选靶实际生效的优先级与降级原因，供诊断/HUD 读取。
    #: 为什么要暴露：操作员在下拉框里选了“血量优先”，但如果根本没有
    #: 裁判系统流量（台架上只连机器人 USB），那个选择兑不了。不告知
    #: 就等于“选了跟没选一样”，与本次要修的孤儿设置项是同一类问题。
    autoaim_priority_actual: str = "Armor"
    autoaim_priority_reason: str = ""

    def _armor_distance_m(self, armor) -> float:
        """取装甲板距离（米）；拿不到时返回一个极大值而不是 0。

        0 会被当成“贴脸”，把缺数据的靶排到最前，那比不优先更坑。
        """
        for attr in ("distance_m", "depth_m"):
            try:
                value = float(getattr(armor, attr, 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if value > 0.0:
                return value
        return float("inf")

    def _armor_health_fraction(self, armor) -> float | None:
        """从 state.robot_rows 查该靶所属机器人的剩余血量比例；查不到返回 None。

        刻意不重抄 official_robot_health 的索引数学：那张表已在
        manager.sync_frontend_state 里算好了（含敌我 id 偏移、存活状态），
        再实现一遍只会漂移。复用它是单一真相。
        """
        target_id = int(getattr(armor, "target_id", -1) or -1)
        if target_id <= 0:
            return None
        try:
            rows = list(getattr(self.state, "robot_rows", []) or [])
        except (AttributeError, TypeError):
            return None
        for row in rows:
            try:
                if int(row.get("id", -1)) != target_id:
                    continue
            except (AttributeError, TypeError, ValueError):
                continue
            if str(row.get("state", "")) == "DEAD":
                return None  # 已阵亡的不当低血量优先目标
            hp = float(row.get("hp", 0) or 0)
            hp_max = float(row.get("max", 1) or 1)
            if hp <= 0.0 or hp_max <= 0.0:
                return None
            return max(0.0, min(1.0, hp / hp_max))
        return None

    def pick_autoaim_armor(self, candidates, priority: str):
        """按 Priority 选装甲板；数据不足时降级并记下原因。

        这个设置项曾经也是纯装饰：界面上有 Armor/Distance/Health/Radar
        四个选项，但选靶代码写死了 `max(..., key=confidence)`，选哪个都一样。

        降级规则（都是“退到原有行为”，不引入新风险）：
          Distance —— 装甲观察量自带 distance_m/depth_m，不依赖裁判系统；
          Health   —— 需要 robot_rows（来自裁判系统），没数据则降级；
          Radar    —— **故意不实现**。雷达给的是地图坐标，与装甲板之间
            需要“地图位置→图像像素”的投影才能对应，那是有真实误差来源的
            独立功能，拿位置猜匹配会让客户端瞄错车，所以直接降级。
        """
        wanted = str(priority or "Armor").strip()
        actual = wanted
        reason = ""

        if not candidates:
            return None

        def by_confidence(armor) -> tuple[float, int]:
            # 同分用 plate_id 定点，避免每帧在等置信度靶之间反复跳变
            return (float(armor.confidence), int(getattr(armor, "plate_id", -1) or -1))

        chosen = None
        if wanted == "Distance":
            actual = "Distance"
            # 距离近者优先。_armor_distance_m 拿不到时返回 inf，会被排到最继，
            # 而不会像 0 那样被当成“贴脸”插到最前。
            chosen = max(
                candidates,
                key=lambda a: (-(self._armor_distance_m(a)), by_confidence(a)[0]),
            )
        elif wanted == "Health":
            scored = [(self._armor_health_fraction(a), a) for a in candidates]
            known = [(frac, a) for frac, a in scored if frac is not None]
            if known:
                actual = "Health"
                # 血量低者优先；同血量再按置信度
                chosen = min(known, key=lambda item: (item[0], -by_confidence(item[1])[0]))[1]
            else:
                reason = "无裁判系统血量数据（未连裁判机或尚未收到血量帧），已按置信度选靶"
        elif wanted == "Radar":
            reason = "雷达上报的是地图坐标，与装甲板需要位置投影才能对应，未实现；已按置信度选靶"
        elif wanted not in ("Armor",):
            reason = f"未知优先级 {wanted!r}，已按置信度选靶"

        if reason:
            actual = "Armor"
        # 两条路径都必须写：上一版在 Distance/Health 命中时提前 return 了，
        # 诊断字段会停在上一帧的旧值，界面会显示错的降级原因。
        self.autoaim_priority_actual = actual
        self.autoaim_priority_reason = reason
        if chosen is not None:
            return chosen
        return max(candidates, key=by_confidence)

    def apply_autoaim_fov_lock(self, result: VisionResult) -> VisionResult:
        """Keep all ESP detections; only plates inside FOV may be aim_armor / locked."""
        module = self.registry.get("ArmorAutoAim")
        if module is None or not module.enabled:
            return result
        aim = result.aim_armor
        if aim is not None and not self.armor_center_in_autoaim_fov(aim, result):
            aim = None
        if aim is None:
            candidates = [
                armor
                for armor in result.detections
                if len(armor.points) == 4 and self.armor_center_in_autoaim_fov(armor, result)
            ]
            if candidates:
                priority_setting = module.get_setting("Priority") if module is not None else None
                priority = str(getattr(priority_setting, "value", "Armor") or "Armor")
                aim = self.pick_autoaim_armor(candidates, priority)
        # Predicted plates of the locked target may still draw; aim face only when in FOV.
        tracking = result.tracking_state
        if aim is None and (result.detections or result.predicted_armors):
            if tracking.lower() in ("locked", "tracking"):
                tracking = "detected"
        return replace(result, aim_armor=aim, tracking_state=tracking)

    def vision_result_for_display(self, result: VisionResult | None) -> VisionResult | None:
        """将灭灯或已确认战亡目标从可视层移除，原始结果继续供 detector 使用。

        Autoaim FOV only strips / reassigns aim_armor; detections stay for full-screen 3D ESP.
        """
        if result is None:
            return None
        detector = self.vision_kill_detector
        hidden_targets = {
            detector.target_identity(armor)
            for armor in result.detections
            if detector.is_extinguished(armor) or detector.is_suppressed(armor)
        }

        def hidden(armor) -> bool:
            return (
                armor.held
                or
                detector.is_extinguished(armor)
                or detector.is_suppressed(armor)
                or detector.target_identity(armor) in hidden_targets
            )

        detections = tuple(armor for armor in result.detections if not hidden(armor))
        predicted = tuple(armor for armor in result.predicted_armors if not hidden(armor))
        aim = result.aim_armor
        if aim is not None and hidden(aim):
            aim = None
        has_visible_target = bool(detections or predicted or aim)
        tracking_state = result.tracking_state if has_visible_target else "lost"
        filtered = replace(
            result,
            detections=detections,
            predicted_armors=predicted,
            aim_armor=aim,
            tracking_state=tracking_state,
        )
        return self.apply_autoaim_fov_lock(filtered)

    def interpolated_vision_result(self, now: float | None = None) -> VisionResult | None:
        """Return geometry sampled on the display clock, never on the camera clock."""
        self._vision_render_result = self.vision_render_interpolator.sample(now)
        return self._vision_render_result

    def vision_result_calibrated(self, result: VisionResult | None) -> bool:
        if result is None or not result.calibrated:
            return False
        return self.calibration.is_valid_for(result.source_width, result.source_height)

    def lock_indicator_visible(self) -> bool:
        """`LockState / Show Lock`：要不要画目标锁定框。

        这个开关以前没人读，关掉后锁定框照旧画。

        保守取向：模块不存在、已关闭、或查不到该设置项时都返回 True，
        等于保持接线前的行为。只有“模块开着 + 开关明确关了”才隐藏，
        避免因为配置缺失把 HUD 元素弄消失。
        """
        module = self.registry.get("LockState")
        if module is None or not module.enabled:
            return True
        setting = module.get_setting("Show Lock")
        if setting is None:
            return True
        return bool(getattr(setting, "value", True))

    def draw_target_overlay(self, p: QPainter, target: VisionTarget, module: Module | None = None) -> None:
        if not self.lock_indicator_visible():
            return
        rect = QRectF(target.x, target.y, target.w, target.h)
        locked = self.state.energy_target_locked if target.kind == "energy" else self.state.target_locked
        setting = module.get_setting("Lock Style") if module else None
        style = str(setting.value) if setting else "Polar Corner"
        main = self.hud_accent() if locked else self.hud_danger()
        if target.kind == "armor" and style == "Polar Corner":
            # Armor locks share the Jello/FOV white family, not team tints.
            self.draw_white_outline_lock(p, rect, target, locked)
            return
        if style == "White Outline":
            self.draw_white_outline_lock(p, rect, target, locked)
            return
        if style == "Minimal Bracket":
            self.draw_minimal_bracket_lock(p, rect, target, locked)
            return
        if style == "Rune Ring":
            self.draw_rune_ring_lock(p, rect, target, module, locked)
            return
        if style == "Heli Gimbal":
            # T1（gd656 移植）：第 5 种 Lock Style。实现放在模块级函数
            # _heli_gimbal_lock_draw —— 不新增 PolarRMWindow 成员，
            # api_freeze_test 冻结面保持不变。
            _heli_gimbal_lock_draw(self, p, rect, target, module, locked)
            return

        p.setPen(QPen(with_alpha(main, 0.92), 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        p.setPen(QPen(main, 3.2))
        corner = min(28.0, rect.width() * 0.28)
        p.drawLine(rect.topLeft(), rect.topLeft() + QPointF(corner, 0))
        p.drawLine(rect.topLeft(), rect.topLeft() + QPointF(0, corner))
        p.drawLine(rect.topRight(), rect.topRight() + QPointF(-corner, 0))
        p.drawLine(rect.topRight(), rect.topRight() + QPointF(0, corner))
        p.drawLine(rect.bottomLeft(), rect.bottomLeft() + QPointF(corner, 0))
        p.drawLine(rect.bottomLeft(), rect.bottomLeft() + QPointF(0, -corner))
        p.drawLine(rect.bottomRight(), rect.bottomRight() + QPointF(-corner, 0))
        p.drawLine(rect.bottomRight(), rect.bottomRight() + QPointF(0, -corner))
        label = self.target_overlay_label(target)
        self.draw_pill(p, QPointF(rect.left(), rect.top() - 27), label, main)

    def draw_fluorescent_white_segments(
        self,
        p: QPainter,
        segments: list[tuple[QPointF, QPointF]],
        *,
        line_width: float = 1.0,
        glow: float = 0.35,
        alpha: float = 1.0,
        dashed: bool = False,
    ) -> None:
        """Debug 3D Box ESP fluorescent white (visual-demo esp-box-white.html).

        Pure #FFFFFF core at ``line_width`` (~1.0 jar). Soft pass:
        alpha = 0.22 * glow, width = line_width + 2.5 * glow, round caps.
        No fill, no pulse, no cyan tint.
        """
        if not segments:
            return
        base_w = max(0.5, float(line_width))
        glow = max(0.0, min(1.0, float(glow)))
        alpha = max(0.0, min(1.0, float(alpha)))
        style = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(Qt.BrushStyle.NoBrush)

        def stroke(a: float, width: float) -> None:
            if a <= 0.001 or width <= 0.01:
                return
            pen = QPen(QColor(255, 255, 255, max(1, min(255, round(255.0 * a)))), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setStyle(style)
            p.setPen(pen)
            for start, end in segments:
                p.drawLine(start, end)

        if glow > 0.01:
            # Multi-pass pure white bloom (CPU/raster-safe; avoid fat single AA pass).
            stroke(0.06 * glow * alpha, base_w + 8.0 * glow)
            stroke(0.12 * glow * alpha, base_w + 4.0 * glow)
            stroke(0.28 * glow * alpha, base_w + 1.8 * glow)
        # Core jar stroke — RGB 1,1,1.
        stroke(1.0 * alpha, base_w)
        stroke(0.55 * alpha, max(0.55, base_w * 0.45))
        p.restore()

    def draw_fluorescent_white_polyline(
        self,
        p: QPainter,
        points: list[QPointF],
        *,
        closed: bool = True,
        line_width: float = 1.0,
        glow: float = 0.35,
        alpha: float = 1.0,
        dashed: bool = False,
    ) -> None:
        if len(points) < 2:
            return
        limit = len(points) if closed else len(points) - 1
        segments = [(points[i], points[(i + 1) % len(points)]) for i in range(limit)]
        self.draw_fluorescent_white_segments(
            p, segments, line_width=line_width, glow=glow, alpha=alpha, dashed=dashed,
        )

    def rasterize_fluorescent_white_circle(
        self,
        radius: float,
        *,
        line_width: float = 1.15,
        glow: float = 0.85,
        alpha: float = 1.0,
        device_scale: float = 1.0,
    ) -> tuple[QImage, float]:
        """CPU fluorescent white ring at device pixels (sharp on 2K).

        Never drawEllipse on OpenGL (white square atlas). Rasterize 1:1 for the
        current painter scale — no Smooth downscale mush.
        """
        glow = max(0.0, min(1.0, float(glow)))
        alpha = max(0.0, min(1.0, float(alpha)))
        px = max(1.0, float(device_scale))
        cache_key = (
            int(round(float(radius) * px * 2.0)),
            int(round(float(line_width) * px * 100.0)),
            int(round(glow * 100.0)),
            int(round(px * 100.0)),
        )
        if alpha >= 0.999 and cache_key in self._fluorescent_circle_cache:
            return self._fluorescent_circle_cache[cache_key]

        r = max(0.5, float(radius) * px)
        base_w = max(0.75, float(line_width) * px)
        pad = base_w * 0.5 + (2.5 * glow + 8.0 * glow) * 0.5 * px + 3.0 * px
        half = int(math.ceil(r + pad))
        size = max(8, half * 2)
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        ip = QPainter(image)
        ip.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ip.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        ip.setBrush(Qt.BrushStyle.NoBrush)
        center = QPointF(size * 0.5, size * 0.5)

        def stroke(a: float, width: float) -> None:
            if a <= 0.001 or width <= 0.05:
                return
            pen = QPen(QColor(255, 255, 255, max(1, min(255, round(255.0 * a)))), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            ip.setPen(pen)
            ip.drawEllipse(center, r, r)

        if glow > 0.01:
            stroke(0.05 * glow * alpha, base_w + 12.0 * glow * px / max(1.0, px))
            stroke(0.10 * glow * alpha, base_w + 7.0 * glow)
            stroke(0.18 * glow * alpha, base_w + 3.8 * glow)
            stroke(0.32 * glow * alpha, base_w + 1.8 * glow)
        stroke(1.00 * alpha, base_w)
        stroke(0.70 * alpha, max(0.65 * px, base_w * 0.48))
        ip.end()
        half_logical = size * 0.5 / px
        if alpha >= 0.999:
            self._fluorescent_circle_cache[cache_key] = (image, half_logical)
            if len(self._fluorescent_circle_cache) > 48:
                oldest = next(iter(self._fluorescent_circle_cache))
                if oldest != cache_key:
                    self._fluorescent_circle_cache.pop(oldest, None)
        return image, half_logical

    def draw_fluorescent_white_ellipse(
        self,
        p: QPainter,
        center: QPointF,
        radius_x: float,
        radius_y: float | None = None,
        *,
        line_width: float = 1.15,
        glow: float = 0.85,
        alpha: float = 1.0,
    ) -> None:
        """Fluorescent white ellipse via device-scale CPU raster + crisp blit."""
        rx = max(0.5, float(radius_x))
        ry = max(0.5, float(radius_x if radius_y is None else radius_y))
        radius = max(rx, ry)
        device_scale = self.painter_device_scale(p)
        image, half = self.rasterize_fluorescent_white_circle(
            radius, line_width=line_width, glow=glow, alpha=alpha, device_scale=device_scale,
        )
        if rx != ry and ry > 0.5:
            target = QRectF(
                center.x() - half * (rx / radius),
                center.y() - half * (ry / radius),
                half * 2.0 * (rx / radius),
                half * 2.0 * (ry / radius),
            )
        else:
            target = QRectF(center.x() - half, center.y() - half, half * 2.0, half * 2.0)
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        # No SmoothPixmapTransform — 2K soft upscale was the blur.
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(target, image)
        p.restore()

    def draw_armor_fov_circle(self, p: QPainter, width: int, height: int, module: Module | None) -> None:
        """Screen-center fluorescent white FOV ring; size from ClickGUI Circle Size.

        CPU-rasterized so the OpenGL paint engine cannot leave a white square
        atlas around the ring.
        """
        if module is None or not module.enabled:
            return
        if not self.setting_bool(module, "Draw Fov", True):
            return
        radius = max(8.0, self.get_setting_number(module, "Circle Size", 160.0))
        center = QPointF(width * 0.5, height * 0.5)
        # Stronger glow than thin canvas default so it reads as jar fluorescent white.
        self.draw_fluorescent_white_ellipse(
            p, center, radius, line_width=1.15, glow=0.90, alpha=1.0,
        )

    def lock_pulse_strength(self, locked: bool) -> float:
        """锁定时的呼吸强度 0..1。`LockState / Pulse Locked` 控制它。

        以前这个开关没人读，且原本也不存在任何脉冲——锁定框的透明度是
        固定 0.94。所以实现取向刻保守：

          * 未锁定 → 0（不脉冲。“锁定脉冲”的含义就是只在已锁定时呼吸）
          * 开关关 → 0，且调用处会回退到原值 0.94，画面与接线前逐值一致
          * 查不到设置项 → 按 True 处理

        返回的是幅度，调用方把它叠到原有 alpha 上，而不是接管颜色：
        颜色已经由 locked/unlocked 在 accent/danger 之间切换，再叠一重
        语义会让告警色和脉冲混在一起。
        """
        if not locked:
            return 0.0
        module = self.registry.get("LockState")
        # 模块整体关闭时不得引入新增动画：脉冲是这个模块提供的，
        # 关掉模块后还多出一套呼吸，等于“关不掉”。
        if module is None or not module.enabled:
            return 0.0
        setting = module.get_setting("Pulse Locked")
        if setting is None or not bool(getattr(setting, "value", True)):
            return 0.0
        wave = 0.5 + 0.5 * math.sin(time.perf_counter() * 4.0)
        return max(0.0, min(1.0, wave))

    def draw_white_outline_lock(self, p: QPainter, rect: QRectF, target: VisionTarget, locked: bool) -> None:
        # 开脉冲时在 0.94 基础上向下呼吸；不开时就是原来的常量 0.94。
        base_alpha = 0.94 if locked else 0.62
        alpha = max(0.30, base_alpha - 0.30 * self.lock_pulse_strength(locked))
        accent = color("#ffffff", alpha) if target.kind == "armor" else (
            self.hud_accent() if locked else self.hud_danger()
        )
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Full fluorescent white box outline (Debug Box ESP recipe).
        corners = [
            rect.topLeft() + QPointF(0.5, 0.5),
            rect.topRight() + QPointF(-0.5, 0.5),
            rect.bottomRight() + QPointF(-0.5, -0.5),
            rect.bottomLeft() + QPointF(0.5, -0.5),
        ]
        self.draw_fluorescent_white_polyline(
            p, corners, closed=True, line_width=1.0, glow=0.85, alpha=alpha,
        )
        if target.kind == "armor":
            corner_segs = armor_corner_segments(corners)
        else:
            # Keep the energy reticle's original corner length/origin.
            corner = min(22.0, rect.width() * 0.22)
            corner_segs = [
                (point, point + delta)
                for point, dx, dy in (
                    (rect.topLeft(), corner, corner),
                    (rect.topRight(), -corner, corner),
                    (rect.bottomLeft(), corner, -corner),
                    (rect.bottomRight(), -corner, -corner),
                )
                for delta in (QPointF(dx, 0), QPointF(0, dy))
            ]
        self.draw_fluorescent_white_segments(
            p, corner_segs, line_width=1.15, glow=0.85, alpha=alpha,
        )
        if target.kind != "armor":
            check_origin = QPointF(rect.right() - 18, rect.top() + 12)
            p.setPen(QPen(accent, 1.8))
            p.drawLine(check_origin + QPointF(-7, 0), check_origin + QPointF(-2, 5))
            p.drawLine(check_origin + QPointF(-2, 5), check_origin + QPointF(8, -7))
        self.draw_pill(
            p,
            QPointF(rect.left(), rect.top() - 27),
            self.target_overlay_label(target),
            color("#ffffff", alpha),
        )
        p.restore()

    def draw_minimal_bracket_lock(self, p: QPainter, rect: QRectF, target: VisionTarget, locked: bool) -> None:
        if target.kind == "armor":
            points = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
            alpha = 0.88
            self.draw_fluorescent_white_segments(
                p, armor_corner_segments(points, limit=30.0),
                line_width=1.0, glow=0.85, alpha=alpha,
            )
            self.draw_pill(p, QPointF(rect.left(), rect.top() - 27), self.target_overlay_label(target), color("#ffffff", alpha))
            return
        main = color("#ffffff", 0.88) if locked else with_alpha(self.hud_danger(), 0.88)
        corner = min(30.0, rect.width() * 0.24)
        inset = 2.0
        p.save()
        p.setPen(QPen(main, 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        tl = rect.topLeft() + QPointF(inset, inset)
        tr = rect.topRight() + QPointF(-inset, inset)
        bl = rect.bottomLeft() + QPointF(inset, -inset)
        br = rect.bottomRight() + QPointF(-inset, -inset)
        for point, hx, vy in ((tl, corner, corner), (tr, -corner, corner), (bl, corner, -corner), (br, -corner, -corner)):
            p.drawLine(point, point + QPointF(hx, 0))
            p.drawLine(point, point + QPointF(0, vy))
        self.draw_pill(p, QPointF(rect.left(), rect.top() - 27), self.target_overlay_label(target), main)
        p.restore()

    def draw_rune_ring_lock(self, p: QPainter, rect: QRectF, target: VisionTarget, module: Module | None, locked: bool) -> None:
        main = self.hud_accent() if locked else color("#ffffff", 0.58)
        center = rect.center()
        radius = min(rect.width(), rect.height()) / 2.0
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(with_alpha(main, 0.82), 1.4))
        p.drawEllipse(center, radius, radius)
        p.setPen(QPen(with_alpha(main, 0.95), 2.3))
        sweep = 16.0
        for angle in (0, 90, 180, 270):
            rad = math.radians(angle)
            outer = QPointF(center.x() + math.cos(rad) * (radius + 7), center.y() + math.sin(rad) * (radius + 7))
            inner = QPointF(center.x() + math.cos(rad) * (radius - sweep), center.y() + math.sin(rad) * (radius - sweep))
            p.drawLine(inner, outer)
        p.setPen(QPen(self.hud_warning() if self.state.energy_rune_rotation == "CCW" else self.hud_accent(), 2.4))
        progress = self.state.energy_rune_progress / 100.0 if target.kind == "energy" else target.confidence
        marker_angle = progress * math.tau - math.pi / 2.0
        marker = QPointF(center.x() + math.cos(marker_angle) * radius, center.y() + math.sin(marker_angle) * radius)
        p.drawEllipse(marker, 4.0, 4.0)
        show_progress = self.setting_bool(module, "Show Progress", True)
        if show_progress and target.kind == "energy":
            bar = QRectF(rect.left(), rect.bottom() + 8, rect.width(), 5)
            draw_bar(p, bar, progress, self.hud_accent(), color("#ffffff", 0.18), 2.5)
        self.draw_pill(p, QPointF(rect.left(), rect.top() - 27), self.target_overlay_label(target), main)
        p.restore()

    def target_overlay_label(self, target: VisionTarget) -> str:
        if target.kind == "energy":
            return f"{target.label}  {target.confidence:.0%}  {self.state.energy_target_distance_m:.1f}m  {self.state.energy_rune_progress}%"
        return f"{target.label}  {target.confidence:.0%}  {self.state.target_distance_m:.1f}m"

    def armor_esp_points(self, points: tuple[tuple[float, float], ...], result: VisionResult, width: int, height: int) -> list[QPointF]:
        camera_module = self.registry.get("CameraFeed")
        mirror = self.setting_bool(camera_module, "Mirror", False)
        x_scale = width / max(1.0, float(result.source_width))
        y_scale = height / max(1.0, float(result.source_height))
        return [QPointF(width - x * x_scale if mirror else x * x_scale, y * y_scale) for x, y in points]

    def armor_esp_back_points(self, armor, result: VisionResult, width: int, height: int, depth: float) -> list[QPointF]:
        if len(armor.back_points) == 4:
            return _display_armor_points(self, armor.back_points, result, width, height)
        front = _display_armor_points(self, armor.points, result, width, height)
        if len(front) != 4:
            return []
        center = QPolygonF(front).boundingRect().center()
        thickness = max(5.0, min(32.0, QPolygonF(front).boundingRect().width() * depth))
        angle = armor.yaw_rad
        offset = QPointF(-math.sin(angle) * thickness, math.cos(angle) * thickness * 0.34)
        return [center + (point - center) * 0.90 + offset for point in front]

    def armor_depth_visible(self) -> bool:
        """`Armor3DESP / Show Depth`：要不要画背面的 8 条深度边。

        这个开关以前没人读。注意它**只能控制深度边**：不能靠传空
        `back` 来实现，因为两个盒子函数开头都是 `len(back) != 4: return`，
        那样会变成整个装甲盒消失，而不是“只去掉深度线”。

        取不到设置项时返回 True（= 保持接线前的 12 边行为）。
        """
        module = self.registry.get("Armor3DESP")
        if module is None:
            return True
        setting = module.get_setting("Show Depth")
        if setting is None:
            return True
        return bool(getattr(setting, "value", True))

    def armor_box_edges(
        self, front: list[QPointF], back: list[QPointF], *, show_depth: bool | None = None
    ) -> list[tuple[QPointF, QPointF]]:
        """拼装甲盒边：开深度时 12 条（前框 4 + 背框 4 + 连接 4），否则只 4 条。

        抽成函数而不是在两个盒子绘制里各写一遍 for 循环：两处必须一致，
        否则会出现"一个样式去了深度线、另一个没去"的不一致。
        """
        if len(front) != 4:
            return []
        depth_on = self.armor_depth_visible() if show_depth is None else bool(show_depth)
        edges: list[tuple[QPointF, QPointF]] = []
        has_back = len(back) == 4
        for i in range(4):
            edges.append((front[i], front[(i + 1) % 4]))
            if depth_on and has_back:
                edges.append((back[i], back[(i + 1) % 4]))
                edges.append((front[i], back[i]))
        return edges

    def draw_3d_esp_box(self, p: QPainter, front: list[QPointF], back: list[QPointF], c: QColor, line_width: float, *, glass: bool, ghost: bool, label: str | None = None) -> None:
        if len(front) != 4 or len(back) != 4:
            return
        front_poly = QPolygonF(front)
        # jar base width 1.0; module Line Width defaults to 2.0 → map to ~1.0.
        base_w = max(0.75, float(line_width) * 0.5)
        alpha = max(0.15, min(1.0, c.alphaF() if isinstance(c, QColor) else 1.0))
        if ghost:
            alpha *= 0.72
        # 开着 Show Depth 时是完整 12 边闭合盒；关掉后只留前框 4 边。
        edges = self.armor_box_edges(front, back)
        self.draw_fluorescent_white_segments(
            p, edges, line_width=base_w, glow=0.85, alpha=alpha, dashed=ghost,
        )
        if label:
            box = front_poly.boundingRect()
            white = color("#ffffff", alpha)
            if ghost:
                self.draw_text(p, QRectF(box.left(), box.top() - 16, 44, 13), label, self.font("poppins_medium", 7.0), white)
            else:
                self.draw_pill(p, QPointF(box.left(), box.top() - 22), label, white)

    @staticmethod
    def constrain_prediction_geometry(
        front: list[QPointF],
        back: list[QPointF],
        reference: list[QPointF],
    ) -> tuple[list[QPointF], list[QPointF]]:
        """限制未标定 PnP 偶发的超大投影，但不改预测板中心位置。"""
        if len(front) != 4 or len(reference) != 4:
            return front, back
        box = QPolygonF(front).boundingRect()
        reference_box = QPolygonF(reference).boundingRect()
        if box.width() <= 0.01 or box.height() <= 0.01 or reference_box.isEmpty():
            return front, back
        max_w = max(18.0, reference_box.width() * 1.35)
        max_h = max(10.0, reference_box.height() * 1.45)
        scale = min(1.0, max_w / box.width(), max_h / box.height())
        if scale >= 0.999:
            return front, back
        center = box.center()

        def scaled(points: list[QPointF]) -> list[QPointF]:
            return [center + (point - center) * scale for point in points]

        return scaled(front), scaled(back) if len(back) == 4 else back

    def draw_clean_3d_esp_box(
        self,
        p: QPainter,
        front: list[QPointF],
        back: list[QPointF],
        c: QColor,
        line_width: float,
        *,
        glass: bool,
        ghost: bool,
        label: str | None = None,
    ) -> None:
        """Outlined 3D armor box — fluorescent white wire only (no solid faces)."""
        if len(front) != 4 or len(back) != 4:
            return
        front_poly = QPolygonF(front)
        base_w = max(0.75, float(line_width) * 0.5)
        alpha = max(0.15, min(1.0, c.alphaF() if isinstance(c, QColor) else 1.0))
        if ghost:
            alpha *= 0.72
        edges = self.armor_box_edges(front, back)
        self.draw_fluorescent_white_segments(
            p, edges, line_width=base_w, glow=0.85, alpha=alpha, dashed=ghost,
        )
        if label:
            box = front_poly.boundingRect()
            white = color("#ffffff", alpha)
            if ghost:
                self.draw_text(p, QRectF(box.left(), box.top() - 16, 44, 13), label, self.font("poppins_medium", 7.0), white)
            else:
                self.draw_pill(p, QPointF(box.left(), box.top() - 22), label, white)

    def draw_polar_mechanical_wire(
        self,
        p: QPainter,
        front: list[QPointF],
        back: list[QPointF],
        accent: QColor,
        line_width: float,
        *,
        predicted: bool,
        label: str | None = None,
    ) -> None:
        """Mechanical armor wireframe — Debug Box ESP fluorescent white 1:1.

        Pure #FFFFFF soft+core strokes only; no fill faces. Matches
        visual-demo/esp-box-white.html (glow=0.35, base width ~1.0).
        """
        if len(front) != 4:
            return

        def lerp(a: QPointF, b: QPointF, amount: float) -> QPointF:
            return QPointF(
                a.x() + (b.x() - a.x()) * amount,
                a.y() + (b.y() - a.y()) * amount,
            )

        front_poly = QPolygonF(front)
        alpha = 0.46 if predicted else 1.0
        # jar glLineWidth(1.0); module default Line Width 2.0 → base 1.0.
        base_w = max(0.75, float(line_width) * 0.5 * (0.85 if predicted else 1.0))
        glow = 0.85

        if len(back) == 4 and self.armor_depth_visible():
            back_edges = [(back[i], back[(i + 1) % 4]) for i in range(4)]
            depth_edges = [(front[i], back[i]) for i in range(4)]
            self.draw_fluorescent_white_segments(
                p, back_edges, line_width=base_w * 0.85, glow=glow, alpha=alpha * 0.55, dashed=True,
            )
            self.draw_fluorescent_white_segments(
                p, depth_edges, line_width=base_w * 0.9, glow=glow, alpha=alpha * 0.65, dashed=True,
            )

        self.draw_fluorescent_white_polyline(
            p, front, closed=True, line_width=base_w, glow=glow, alpha=alpha, dashed=predicted,
        )
        if not predicted:
            self.draw_fluorescent_white_segments(
                p, armor_corner_segments(front), line_width=max(1.0, base_w * 1.2), glow=glow, alpha=alpha,
            )

        edge_lengths = [
            math.hypot(front[(index + 1) % 4].x() - front[index].x(), front[(index + 1) % 4].y() - front[index].y())
            for index in range(4)
        ]
        brace: list[tuple[QPointF, QPointF]] = []
        if edge_lengths[0] + edge_lengths[2] >= edge_lengths[1] + edge_lengths[3]:
            for amount in (0.24, 0.76):
                brace.append((lerp(front[0], front[1], amount), lerp(front[3], front[2], amount)))
        else:
            for amount in (0.24, 0.76):
                brace.append((lerp(front[1], front[2], amount), lerp(front[0], front[3], amount)))
        self.draw_fluorescent_white_segments(
            p, brace, line_width=max(0.65, base_w * 0.72), glow=glow, alpha=alpha * 0.68, dashed=predicted,
        )

        if label:
            box = front_poly.boundingRect()
            label_width = max(42.0, text_width(p, self.font("poppins_medium", 6.4), label) + 4.0)
            self.draw_text(
                p,
                QRectF(box.left(), box.top() - 15, label_width, 13),
                label,
                self.font("poppins_medium", 6.4),
                color("#ffffff", 0.72 if predicted else 0.96),
            )

    @staticmethod
    def _vision_wire_key(armor) -> tuple[int, int, bool]:
        target_id = armor.target_id if armor.target_id >= 0 else armor.armor_id
        plate_id = armor.plate_id if armor.plate_id >= 0 else armor.armor_id
        return target_id, plate_id, bool(armor.predicted)

    def _append_vision_wire_armor(
        self,
        batch: VisionWireBatch,
        front: list[QPointF],
        back: list[QPointF],
        *,
        width: float,
        alpha: float,
        predicted: bool,
        label: str | None = None,
        label_scale: float = 1.0,
    ) -> None:
        """Append fluorescent white linework without any filled geometry.

        Formula matches visual-demo/esp-box-white.html (glow=0.35, pure #FFFFFF).
        """
        if len(front) != 4:
            return
        alpha = max(0.0, min(1.0, float(alpha)))
        # jar lineWidth 1.0; module Line Width default 2.0 → base ~1.0.
        core_width = max(0.75, float(width) * 0.5 * (0.85 if predicted else 1.0))
        glow = 0.85
        if len(back) == 4 and self.armor_depth_visible():
            batch.fluorescent_polyline(
                back,
                width=max(0.6, core_width * 0.85),
                alpha=alpha * 0.55,
                closed=True,
                dashed=True,
                glow=glow,
            )
            for index in range(4):
                batch.fluorescent_line(
                    front[index],
                    back[index],
                    width=max(0.65, core_width * 0.9),
                    alpha=alpha * 0.65,
                    dashed=True,
                    glow=glow,
                )
        batch.fluorescent_polyline(
            front,
            width=core_width,
            alpha=alpha,
            closed=True,
            dashed=predicted,
            glow=glow,
        )
        if not predicted:
            for start, end in armor_corner_segments(front, limit=22.0 * label_scale):
                batch.fluorescent_line(start, end, width=max(1.0, core_width * 1.2), alpha=alpha, glow=glow)

        def lerp(a: QPointF, b: QPointF, amount: float) -> QPointF:
            return QPointF(
                a.x() + (b.x() - a.x()) * amount,
                a.y() + (b.y() - a.y()) * amount,
            )

        lengths = [
            math.hypot(
                front[(index + 1) % 4].x() - front[index].x(),
                front[(index + 1) % 4].y() - front[index].y(),
            )
            for index in range(4)
        ]
        for amount in (0.24, 0.76):
            if lengths[0] + lengths[2] >= lengths[1] + lengths[3]:
                start, end = lerp(front[0], front[1], amount), lerp(front[3], front[2], amount)
            else:
                start, end = lerp(front[1], front[2], amount), lerp(front[0], front[3], amount)
            batch.fluorescent_line(
                start,
                end,
                width=max(0.6, core_width * 0.72),
                alpha=alpha * 0.68,
                dashed=predicted,
                glow=glow,
            )

        if label:
            box = QPolygonF(front).boundingRect()
            font = self.font("poppins_medium", 6.4 * max(1.0, label_scale))
            label_width = max(52.0 * label_scale, len(label) * 6.6 * label_scale)
            batch.labels.text(
                QRectF(
                    box.left(),
                    box.top() - 16.0 * label_scale,
                    label_width,
                    13.0 * label_scale,
                ),
                getattr(label, "display", label),
                font,
                QColor(255, 255, 255, round(245 * alpha)),
            )

    def build_vision_wire_batch(
        self,
        width: int,
        height: int,
        result: VisionResult,
        module: Module,
    ) -> VisionWireBatch:
        batch = VisionWireBatch()
        overlay = self.registry.get("RobotOverlay")
        line_width = self.get_setting_number(module, "Line Width", 2.0)
        opacity = self.get_setting_number(module, "Opacity", 0.82)
        scale = self.get_hud_scale(width, height)
        line_width *= scale
        depth = self.get_setting_number(overlay, "3D Depth", 0.22)
        max_targets = int(self.get_setting_number(overlay, "Max Targets", 6))
        calibrated = self.vision_result_calibrated(result)
        local_pnp = bool(self._vision_snapshot and self._vision_snapshot.source == "Local") and any(
            len(armor.back_points) == 4
            for armor in result.detections + result.predicted_armors
        )
        show_prediction = (
            self.setting_bool(module, "Show Prediction", True)
            and self.setting_bool(overlay, "Predict Plates", True)
            and (calibrated or local_pnp)
        )
        show_labels = self.setting_bool(module, "Show Labels", True)
        prediction_labels = self.setting_bool(module, "Prediction Labels", False)
        observed = [armor for armor in result.detections if len(armor.points) == 4]
        predicted = [armor for armor in result.predicted_armors if len(armor.points) == 4]
        aim = result.aim_armor if result.aim_armor and len(result.aim_armor.points) == 4 else None
        aim_front = _display_armor_points(self, aim.points, result, width, height) if aim is not None else []
        aim_box = QPolygonF(aim_front).boundingRect() if aim_front else QRectF()
        emitted: set[tuple[int, int, bool]] = set()

        def prediction_alpha(armor) -> float:
            armor_depth = armor.depth_m if armor.depth_m > 0 else armor.distance_m
            if armor_depth <= 0:
                return 0.58
            return max(0.24, min(0.62, 6.5 / max(1.0, armor_depth)))

        for armor in (observed + (predicted if show_prediction else []))[:max_targets]:
            key = self._vision_wire_key(armor)
            if key in emitted:
                continue
            front = _display_armor_points(self, armor.points, result, width, height)
            if len(front) != 4:
                continue
            if aim is not None and not armor.predicted:
                box = QPolygonF(front).boundingRect()
                if math.dist(
                    (box.center().x(), box.center().y()),
                    (aim_box.center().x(), aim_box.center().y()),
                ) < max(12.0 * scale, aim_box.width() * 0.45):
                    continue
            back = self.armor_esp_back_points(armor, result, width, height, depth)
            if armor.predicted and aim_front:
                front, back = self.constrain_prediction_geometry(front, back, aim_front)
            label = f"P{armor.plate_id}" if armor.predicted and prediction_labels else None
            self._append_vision_wire_armor(
                batch,
                front,
                back,
                width=line_width,
                alpha=prediction_alpha(armor) if armor.predicted else opacity,
                predicted=bool(armor.predicted),
                label=label,
                label_scale=scale,
            )
            emitted.add(key)

        if aim is not None:
            aim_key = self._vision_wire_key(aim)
            back = self.armor_esp_back_points(aim, result, width, height, depth)
            distance = aim.distance_m if aim.distance_m > 0 else aim.depth_m
            label = None
            if show_labels:
                label = f"DIST {distance:.1f}m" if distance > 0 else "DIST --"
            self._append_vision_wire_armor(
                batch,
                aim_front,
                back,
                width=line_width + 0.35 * scale,
                alpha=1.0,
                predicted=False,
                label=HudDisplayText(label, _distance_label(label, self.registry)) if label else None,
                label_scale=scale,
            )
            emitted.add(aim_key)
        return batch

    def vision_gpu_renderer_allows(self) -> bool:
        """按 `Armor3DESP / Renderer` 判断能不能走 GPU 线框路径。

        这个开关以前完全没人读：选项里有 CPU，但分流只看
        `vision_wire_gpu.available`，所以选 CPU 也没用——GPU 在时永久走 GPU。

        返回 False 时 `native_owner` 为 False，调用侧会算
        `include_vision = not native_vision_owner`（polar_app.py:1726），
        于是 CPU 的 QPainter 路径（`draw_armor_3d_esp`）接管。
        不是“什么都不画”，而是真的降到另一条已存在的渲染路径。

        取不到设置项、模块缺失或取值不认识时返回 True（= 不干预，等价于
        GPU Auto），保持接线前的现行为。只有明确选了 CPU 才强制降级。
        """
        module = self.registry.get("Armor3DESP")
        if module is None:
            return True
        setting = module.get_setting("Renderer")
        if setting is None:
            return True
        value = str(getattr(setting, "value", "") or "").strip().lower()
        # 只有明确选了 CPU 才强制降级；其余取值（包括 "gpu auto"）不干预
        return value != "cpu"

    def paint_gpu_vision_layer(
        self,
        functions,
        width: int,
        height: int,
        ratio: float,
    ) -> bool:
        """Clear and optionally populate the sole mechanical ESP layer."""
        module = self.registry.get("Armor3DESP")
        armor_module = self.registry.get("ArmorAutoAim")
        overlay = self.registry.get("RobotOverlay")
        native_owner = bool(
            not self.home_visible
            and module
            and module.enabled
            and armor_module
            and armor_module.enabled
            and self.setting_mode(module, "Style", "PolarMechanicalWire") == "PolarMechanicalWire"
            and self.setting_mode(overlay, "ESP Style", "3D Wire") != "Polar Outline"
            and self._vision_source != "Mock"
            # `Renderer` 选 CPU 时必须真的降到 QPainter 路径，而不是照走 GPU
            and self.vision_gpu_renderer_allows()
            and self.vision_wire_gpu.available
            and self.clickgui_gpu.available
        )
        result = self.interpolated_vision_result(time.perf_counter()) if native_owner else None
        batch = self.build_vision_wire_batch(width, height, result, module) if result is not None and module is not None else VisionWireBatch()
        if batch.labels.commands:
            self.clickgui_gpu.prepare_frame(batch.labels, ratio)
        def render() -> bool:
            if not native_owner or result is None:
                return True
            if not self.vision_wire_gpu.render(functions, batch, width, height, ratio):
                return False
            if batch.labels.commands and not self.clickgui_gpu.render(
                functions, batch.labels, width, height, ratio, atlas_prepared=True,
            ):
                return False
            return True

        accepted = self.gpu_compositor.render_vision_layer(functions, render)
        return accepted and native_owner

    def robot_armor_boxes_visible(self) -> bool:
        """`RobotOverlay / Armor Boxes`：要不要画装甲线框本体。

        以前这个开关没人读。它与 `ESP Style` 不重叠：`ESP Style` 选的是
        盒子“长什么样”（3D Wire / Glass / Outline / Flat），本项选的是
        “画不画盒子”。关掉后标签仍然按 `Show Labels` / `Robot ID` 自己的规则出，
        因为那是另一组开关管的事。

        查不到设置项时返回 True（= 保持接线前行为）。
        """
        module = self.registry.get("RobotOverlay")
        if module is None:
            return True
        setting = module.get_setting("Armor Boxes")
        if setting is None:
            return True
        return bool(getattr(setting, "value", True))

    def draw_armor_quad(self, p: QPainter, points: list[QPointF], c: QColor, line_width: float, *, dashed: bool = False, aim: bool = False) -> None:
        if len(points) != 4:
            return
        alpha = c.alphaF()
        self.draw_fluorescent_white_polyline(
            p, points, line_width=max(0.75, line_width * 0.5),
            glow=0.85, alpha=alpha, dashed=dashed,
        )
        if aim:
            self.draw_fluorescent_white_segments(
                p, armor_corner_segments(points), line_width=max(1.0, line_width * 0.6), glow=0.85, alpha=alpha,
            )

    def smooth_armor_geometry(self, armor, result: VisionResult):
        """只平滑连续结果的几何位置；没有结果时不保留任何绘制状态。"""
        if len(armor.points) != 4:
            return armor
        key = (armor.armor_id, armor.plate_id, armor.predicted, armor.label)
        if not result.detections and not result.predicted_armors:
            self._armor_geometry_smooth.clear()
            return armor

        previous = self._armor_geometry_smooth.get(key)
        if previous is None:
            self._armor_geometry_smooth[key] = (armor.points, armor.back_points)
            return armor

        # 约 55ms 的响应时间；识别结果落后相机帧时提高追赶速度，但不制造旧目标。
        alpha = 1.0 - math.exp(-max(0.001, min(0.12, self.frame_dt)) / 0.055)
        frame_lag = max(0, self._last_capture_sequence - result.sequence)
        alpha = max(alpha, min(0.72, frame_lag * 0.12))

        def blend_points(
            current: tuple[tuple[float, float], ...],
            old: tuple[tuple[float, float], ...],
        ) -> tuple[tuple[float, float], ...]:
            if len(current) != len(old):
                return current
            return tuple(
                (
                    before[0] + (after[0] - before[0]) * alpha,
                    before[1] + (after[1] - before[1]) * alpha,
                )
                for before, after in zip(old, current)
            )

        points = blend_points(armor.points, previous[0])
        back_points = blend_points(armor.back_points, previous[1]) if len(armor.back_points) == 4 and len(previous[1]) == 4 else armor.back_points
        self._armor_geometry_smooth[key] = (points, back_points)
        return replace(armor, points=points, back_points=back_points)

    def robot_overlay_id_suffix(self, armor) -> str:
        """按 `RobotOverlay / Robot ID` 给出要拼到标签后的机器人编号后缀。

        这个开关以前完全没人读。抽成返回字符串的小函数而不是在绘制里写 if，
        是为了能直接断言“开了就有编号、关了就没有”，不必靠看图验证。

        `target_id` 未知时是 -1，不能拼出个“#-1”让人以为是某个车，
        所以宁可不拼。
        """
        overlay = self.registry.get("RobotOverlay")
        # 模块整体关闭时它的设置不得再生效：这些标签虽然由 Armor3DESP 的路径
        # 绘制，但 `Robot ID` 归 RobotOverlay 管，关了就不应改变别人。
        if overlay is None or not overlay.enabled:
            return ""
        if not self.setting_bool(overlay, "Robot ID", True):
            return ""
        try:
            target_id = int(getattr(armor, "target_id", -1))
        except (TypeError, ValueError):
            return ""
        if target_id <= 0:
            return ""
        return f" #{target_id}"

    def robot_overlay_distance_suffix(self, armor) -> str:
        """按 `RobotOverlay / Distance` 给出要拼到标签里的距离段。

        跟 `Robot ID` 同一个形状：标签里的“12.3m”一直是写死的，
        界面上那个“距离”开关从来没管过它——关掉之后数字照旧在。

        fail-open 口径与 `lock_indicator_visible()` 一致：**只有“模块开着
        + 开关明确关了”才隐藏**。模块不存在或整体关闭时仍给距离：
        接线前就是这个画面（当时根本没人看过模块开关），把“关掉整个
        模块”顺手变成“抽掉距离”是未经授权的视觉改变。

        注意只管已经存在的那一处：`Polar Outline` 样式的标签本来就不带
        距离，本批**不**给它新增——那是改现有外观，不是接线。“开了两处都
        该有距离”这件事需要你们先定，已记进 SETTINGS_GAPS.md。
        """
        overlay = self.registry.get("RobotOverlay")
        if overlay is not None and overlay.enabled and not self.setting_bool(overlay, "Distance", True):
            return ""
        try:
            distance = float(getattr(armor, "distance_m", 0.0) or 0.0)
        except (TypeError, ValueError):
            distance = 0.0
        # 前导空格写在段里：拼接后与接线前的字符串逐字节相同。
        return f" {distance:.1f}m"

    #: `RobotOverlay / Color` 的默认档。**必须是 White**：接线前代码画的就是
    #: 纯白（`primary = color("#ffffff", opacity)`），而设置项原来的默认写的是
    #: `Team`——那个默认值从来没对应过任何真实行为。照旧默认值直接接，
    #: 会把所有人的装甲盒从白变成红蓝（与 `MapMarker / Send All` 同型问题，
    #: 同一处理：把默认改成等于今天的实际行为，再用断言钉住）。
    ROBOT_OVERLAY_COLOR_DEFAULT = "White"

    #: 上一帧实际生效的配色档与降级原因，供诊断/HUD 读取（与 autoaim_priority_* 同型）。
    #: 两个字段每帧都写：只写一边会让诊断停在旧值，显示错误的降级原因。
    robot_overlay_color_actual: str = "White"
    robot_overlay_color_reason: str = ""

    def robot_overlay_color_mode(self) -> tuple[str, str]:
        """返回 `(实际生效的配色档, 降级原因)`。

        `Threat`（威胁度）在本仓**没有数据源**：视觉只给装甲板颜色
        （red/blue/purple/extinguish），裁判给血量但不给“威胁度”。拿血量现算
        一个威胁度等于发明一套没人定义过的评分——所以与
        `ArmorAutoAim / Priority` 的 Radar 选项同一处理：**降级并明说原因**，
        不谎称“已按威胁度着色”。

        查不到模块/设置项、或值不在值域里，都回落 White（= 接线前的画面）。
        """
        overlay = self.registry.get("RobotOverlay")
        wanted = str(self.setting_mode(overlay, "Color", self.ROBOT_OVERLAY_COLOR_DEFAULT) or "").strip()
        if wanted == "Threat":
            return "Team", "threat score has no data source (vision reports plate color only)"
        if wanted not in ("White", "Team", "Accent"):
            return self.ROBOT_OVERLAY_COLOR_DEFAULT, f"unknown color mode {wanted!r}"
        return wanted, ""

    def robot_overlay_armor_color(self, armor, opacity: float) -> QColor:
        """装甲盒主色。`opacity` 沿用调用方算好的值，不另立一套透明度。

        Team 只在看得出装甲板颜色时才用阵营色：`purple` / `extinguish`（灭灯）
        这些认不出阵营的值**不当成某一方**，回落纯白——把认不出的板涂成
        “敌方红”比不涂危险得多。
        """
        mode, _reason = self.robot_overlay_color_mode()
        if mode == "Accent":
            return with_alpha(self.hud_accent(), opacity)
        if mode == "Team":
            plate = str(getattr(armor, "color", "") or "").strip().lower()
            if plate in ("red", "blue"):
                return with_alpha(self.map_team_color(plate), opacity)
        return color("#ffffff", opacity)

    def draw_armor_3d_esp(self, p: QPainter, width: int, height: int, result: VisionResult, module: Module) -> None:
        if not result.detections and not result.predicted_armors:
            self._armor_geometry_smooth.clear()
        overlay = self.registry.get("RobotOverlay")
        overlay_style = self.setting_mode(overlay, "ESP Style", "3D Wire")
        style = self.setting_mode(module, "Style", "PolarMechanicalWire")
        line_width = self.get_setting_number(module, "Line Width", 2.0)
        opacity = self.get_setting_number(module, "Opacity", 0.82)
        calibrated = self.vision_result_calibrated(result)
        local_pnp = bool(self._vision_snapshot and self._vision_snapshot.source == "Local") and any(len(armor.back_points) == 4 for armor in result.detections + result.predicted_armors)
        show_prediction = self.setting_bool(module, "Show Prediction", True) and self.setting_bool(overlay, "Predict Plates", True) and (calibrated or local_pnp)
        show_labels = self.setting_bool(module, "Show Labels", True)
        prediction_labels = self.setting_bool(module, "Prediction Labels", False)
        depth = self.get_setting_number(overlay, "3D Depth", 0.22)
        max_targets = int(self.get_setting_number(overlay, "Max Targets", 6))
        only_secondary = self.setting_bool(overlay, "Only Secondary", True)
        aim_color = color("#ffffff")
        # `RobotOverlay / Color`：三个消费点（观测盒 / Polar Outline 路径 / 主目标面）
        # 必须共用助手 `robot_overlay_armor_color`，否则同一开关会“一处有效一处无效”。
        # 默认 White 时各消费点取值与接线前逐值相同（由第 20 批断言钉住）。
        _color_mode, self.robot_overlay_color_reason = self.robot_overlay_color_mode()
        self.robot_overlay_color_actual = _color_mode

        # VisionRenderInterpolator 已按视觉时间戳在显示帧间插值。这里必须直接使用
        # 插值结果，不能再做一次追赶平滑，否则低频推理会呈现明显拖滞和阶梯感。
        observed = [armor for armor in result.detections if len(armor.points) == 4]
        predicted = [armor for armor in result.predicted_armors if len(armor.points) == 4]
        aim = result.aim_armor if result.aim_armor and len(result.aim_armor.points) == 4 else None
        if _color_mode != "White" and aim is not None:
            # 主目标面的配色必须用 aim 板自己的板色。早期版本传的是 None，
            # Team 档会出现“旁边的板变色、主目标不跟着变”的同型错位。
            aim_color = self.robot_overlay_armor_color(aim, 1.0)

        if style == "JelloShadow":
            # Sigma's outline uses an entity silhouette/stencil. Its HUD
            # shadow sprite is not an armor silhouette: scaling that black
            # billboard covers the camera. Bloom only the actual quad edges,
            # using the same pure-white recipe as FOV/3D ESP, with no texture.
            for armor in observed[:max_targets]:
                points = _display_armor_points(self, armor.points, result, width, height)
                if len(points) != 4:
                    continue
                self.draw_armor_quad(p, points, color("#ffffff", opacity), line_width, aim=armor.aim)
            return

        def prediction_alpha(armor) -> float:
            """按桥接器回传的真实深度衰减预测线框；旧桥接器无深度时保持默认透明度。"""
            depth = armor.depth_m if armor.depth_m > 0 else armor.distance_m
            if depth <= 0:
                return 0.72
            return max(0.28, min(0.86, 7.5 / max(1.0, depth)))

        if overlay_style == "Polar Outline" or style == "PolarOutline2D":
            source = observed if observed or not calibrated else predicted
            for armor in source:
                points = _display_armor_points(self, armor.points, result, width, height)
                xs = [point.x() for point in points]
                ys = [point.y() for point in points]
                rect = QRectF(min(xs), min(ys), max(4.0, max(xs) - min(xs)), max(4.0, max(ys) - min(ys)))
                # 与观测盒同一个助手：aim 板用全不透明（等价接线前的 aim_color），
                # 其余板沿用 opacity（等价接线前的 primary）。
                c = self.robot_overlay_armor_color(armor, 1.0 if armor.aim else opacity)
                self.draw_minimal_bracket_lock(p, rect, VisionTarget(rect.x(), rect.y(), rect.width(), rect.height(), armor.confidence, armor.label), armor.aim)
                if show_labels:
                    self.draw_pill(p, QPointF(rect.left(), rect.top() - 22), f"{armor.label.upper()} {armor.confidence:.0%}{self.robot_overlay_id_suffix(armor)}", c)
            return

        glass = overlay_style == "3D Glass"
        aim_front = _display_armor_points(self, aim.points, result, width, height) if aim is not None else []
        for armor in (observed + (predicted if show_prediction else []))[:max_targets]:
            front = _display_armor_points(self, armor.points, result, width, height)
            if len(front) != 4:
                continue
            box = QPolygonF(front).boundingRect()
            if aim is not None and not armor.predicted:
                aim_box = QPolygonF(aim_front).boundingRect()
                if math.dist((box.center().x(), box.center().y()), (aim_box.center().x(), aim_box.center().y())) < max(12.0, aim_box.width() * 0.45):
                    # 主目标由下方的 Aim Face 单独绘制，不能再作为 RobotOverlay 重复画一次。
                    continue
            ghost = armor.predicted
            display_color = color("#ffffff", prediction_alpha(armor) * 0.72) if ghost else self.robot_overlay_armor_color(armor, opacity)
            if not self.robot_armor_boxes_visible():
                # 不画盒子，也不再为它算背面（避开无谓的投影开销）
                continue
            back = self.armor_esp_back_points(armor, result, width, height, depth)
            if ghost and aim_front:
                front, back = self.constrain_prediction_geometry(front, back, aim_front)
            if style == "PolarMechanicalWire":
                label = f"P{armor.plate_id}" if ghost and prediction_labels else None
                self.draw_polar_mechanical_wire(
                    p,
                    front,
                    back,
                    display_color,
                    line_width,
                    predicted=ghost,
                    label=label,
                )
            else:
                self.draw_clean_3d_esp_box(
                    p,
                    front,
                    back,
                    display_color,
                    line_width,
                    glass=glass,
                    ghost=ghost,
                    label=(
                        (f"P{armor.plate_id}" if ghost
                         else f"{armor.label.upper()}{self.robot_overlay_distance_suffix(armor)}{self.robot_overlay_id_suffix(armor)}")
                        if show_labels else None
                    ),
                )
        if aim is not None:
            points = aim_front
            back = self.armor_esp_back_points(aim, result, width, height, depth)
            if self.robot_armor_boxes_visible():
                if style == "PolarMechanicalWire":
                    self.draw_polar_mechanical_wire(
                        p,
                        points,
                        back,
                        aim_color,
                        line_width + 0.35,
                        predicted=False,
                    )
                else:
                    self.draw_clean_3d_esp_box(p, points, back, aim_color, line_width + 0.35, glass=glass, ghost=False, label=None)
            if show_labels:
                box = QPolygonF(points).boundingRect()
                distance = aim.distance_m if aim.distance_m > 0 else aim.depth_m
                label = f"DIST {distance:.1f}m" if distance > 0 else "DIST --"
                if style == "PolarMechanicalWire":
                    label_width = max(62.0, text_width(p, self.font("poppins_medium", 6.4), label) + 4.0)
                    self.draw_text(
                        p,
                        QRectF(box.left(), box.top() - 16, label_width, 13),
                        _distance_label(label, self.registry),
                        self.font("poppins_medium", 6.4),
                        color("#ffffff", 0.96),
                    )
                else:
                    _draw_localized_pill(self, p, QPointF(box.left(), box.top() - 24), label, _distance_label(label, self.registry), aim_color)
        if local_pnp and not calibrated:
            self._bottom_left_diagnostics.append(("DESKTOP PNP | APPROX FOV", self.hud_accent(), 1))
        elif not calibrated:
            self._bottom_left_diagnostics.append(("UNCALIBRATED | detection only", self.hud_warning(), 1))
        kill_status = getattr(self.state, "vision_kill_status", "IDLE")
        if kill_status not in ("IDLE", "NO_DETECTION"):
            status_color = self.hud_accent() if kill_status in (
                "LIT_TRACKED", "MODEL_CONFIRMED", "REFLECTION_IGNORED",
                "WAITING_FRESH_FRAMES", "VISION_CONFIRMED",
            ) else self.hud_warning()
            self._bottom_left_diagnostics.append((f"KILL VISION | {kill_status}", status_color, 2))



# ---------------------------------------------------------------------------
# T1（gd656 移植）：第 5 种 Lock Style "Heli Gimbal"（直升机吊舱瞄准镜风格）。
#
# 开源侧没有可直接移植的吊舱 reticle 实现（AUDIT_gd656_integration.md §1.4），
# 本样式为 QPainter 程序化新造：雷达扫掠扇形 + 外圈旋转双弧 + 边角向心括号
# （0.25s ease-out-cubic 入场）+ 中央十字 + 测距刻度 + 目标距离读数 + AIR LOCK pill，
# 配色全部走 hud_accent / hud_danger 体系。
#
# 刻意实现为**模块级函数**而非 Mixin 方法：不向 PolarRMWindow 增加 dir() 成员，
# api_freeze_test 冻结面（§5.6）保持 bit 级不变；host 即窗口实例，经参数传入。
# ---------------------------------------------------------------------------

_HELI_GIMBAL_ENTRY_S = 0.25


def _heli_gimbal_entry_progress(host, key: tuple[str, str], now: float) -> float:
    """目标首次出现起的入场进度（ease-out-cubic），复用 kill_fx 同款缓动手感。"""
    entries = getattr(host, "_heli_gimbal_entries", None)
    if entries is None:
        entries = {}
        setattr(host, "_heli_gimbal_entries", entries)
    started = entries.get(key)
    if started is None:
        entries[key] = now
        started = now
    if len(entries) > 24:
        for stale in [item for item, ts in entries.items() if now - ts > 6.0]:
            entries.pop(stale, None)
    t = min(1.0, (now - started) / _HELI_GIMBAL_ENTRY_S)
    return 1.0 - (1.0 - t) ** 3


def _heli_gimbal_lock_draw(host, p: QPainter, rect: QRectF, target: VisionTarget, module: Module | None, locked: bool) -> None:
    now = time.perf_counter()
    main = host.hud_accent() if locked else host.hud_danger()
    center = rect.center()
    radius = max(8.0, min(rect.width(), rect.height()) / 2.0)
    enter = _heli_gimbal_entry_progress(host, (target.kind, target.label), now)

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # 雷达扫掠扇形：低 alpha 锥形渐变楔形绕中心连续旋转；
    # 转速由 confidence 驱动，energy 目标用符文进度做相位偏移。
    confidence = max(0.0, min(1.0, float(target.confidence)))
    sweep_deg = (now * (60.0 + 240.0 * confidence)) % 360.0
    if target.kind == "energy":
        sweep_deg = (sweep_deg + float(getattr(host.state, "energy_rune_progress", 0)) * 3.6) % 360.0
    sweep_radius = radius * 1.45
    scan = QConicalGradient(center, -sweep_deg)
    scan_head = QColor(main)
    scan_head.setAlphaF(0.30 if locked else 0.16)
    scan_tail = QColor(main)
    scan_tail.setAlphaF(0.0)
    scan.setColorAt(0.0, scan_head)
    scan.setColorAt(0.16, scan_tail)
    scan.setColorAt(1.0, scan_tail)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(scan)
    p.drawEllipse(center, sweep_radius, sweep_radius)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # 外圈双弧括号：持续旋转，锁定时减速（近似“停正”的吊舱锁定语言）。
    spin_deg = (now * (36.0 if locked else 140.0)) % 360.0
    arc_radius = radius * 1.22
    arc_rect = QRectF(center.x() - arc_radius, center.y() - arc_radius, arc_radius * 2.0, arc_radius * 2.0)
    p.setPen(QPen(with_alpha(main, 0.95 if locked else 0.62), 2.0))
    arc_start = int(-spin_deg) % 360 * 16
    for offset in (0, 180):
        p.drawArc(arc_rect, arc_start + offset * 16, 70 * 16)

    # 边角括号：入场时自外向心收缩（enter 0→1）。
    corner = min(26.0, rect.width() * 0.24)
    slide = (1.0 - enter) * radius * 0.55
    p.setPen(QPen(main, 2.6))
    for point, hx, vy in (
        (rect.topLeft(), corner, corner),
        (rect.topRight(), -corner, corner),
        (rect.bottomLeft(), corner, -corner),
        (rect.bottomRight(), -corner, -corner),
    ):
        origin = point + QPointF(math.copysign(slide, hx), math.copysign(slide, vy))
        p.drawLine(origin, origin + QPointF(hx, 0))
        p.drawLine(origin, origin + QPointF(0, vy))

    # 中央十字（带中心间隙与驻留点）。
    gap = max(4.0, radius * 0.16)
    arm = max(10.0, radius * 0.55)
    p.setPen(QPen(with_alpha(main, 0.96), 1.7))
    p.drawLine(center + QPointF(gap, 0), center + QPointF(gap + arm, 0))
    p.drawLine(center - QPointF(gap, 0), center - QPointF(gap + arm, 0))
    p.drawLine(center + QPointF(0, gap), center + QPointF(0, gap + arm))
    p.drawLine(center - QPointF(0, gap), center - QPointF(0, gap + arm))
    p.setPen(QPen(main, 1.4))
    p.drawEllipse(center, 1.8, 1.8)

    # 测距刻度：右下侧 mil 分划（长短交替）。
    tick_x = center.x() + radius * 0.78
    p.setPen(QPen(with_alpha(main, 0.72), 1.2))
    for index in range(5):
        y = center.y() + gap + 10.0 + index * 8.0
        half = 6.0 if index % 2 == 0 else 3.5
        p.drawLine(QPointF(tick_x - half, y), QPointF(tick_x + half, y))

    # 目标距离读数（框下居中）。
    distance = float(
        host.state.energy_target_distance_m if target.kind == "energy" else host.state.target_distance_m
    )
    host.draw_text(
        p,
        QRectF(rect.center().x() - 70.0, rect.bottom() + 6.0, 140.0, 14.0),
        hud_template("DIST {value}", chinese_enabled(host.registry), value=f"{distance:.1f}M"),
        host.font("poppins_medium", 7.0),
        color("#ffffff", 0.92 if locked else 0.66),
    )

    # 顶部 AIR LOCK 小 pill（沿用现有 pill 标签槽位）。
    _draw_localized_pill(
        host, p, QPointF(rect.left(), rect.top() - 27),
        f"AIR LOCK · {target.label.upper()} {target.confidence:.0%}",
        hud_template("AIR LOCK · {label} {confidence}", chinese_enabled(host.registry),
                     label=target.label.upper(), confidence=f"{target.confidence:.0%}"),
        main,
    )
    p.restore()


# ---------------------------------------------------------------------------
# API 冻结面 qualname 固定（见模块 docstring「冻结面纪律」；先例：
# ui/polar/text_metrics.py 底部与 renderers/combat_target_hud.py 底部同一手法）。
# ---------------------------------------------------------------------------

_QUALNAME_PINNED_MEMBERS = (
    EspWireframeMixin.autoaim_logical_size,
    EspWireframeMixin.autoaim_circle_radius,
    EspWireframeMixin.sync_local_autoaim_fov,
    EspWireframeMixin.armor_center_in_autoaim_fov,
    EspWireframeMixin.vision_target_in_autoaim_fov,
    EspWireframeMixin.apply_autoaim_fov_lock,
    EspWireframeMixin.vision_result_for_display,
    EspWireframeMixin.interpolated_vision_result,
    EspWireframeMixin.vision_result_calibrated,
    EspWireframeMixin.draw_target_overlay,
    EspWireframeMixin.draw_fluorescent_white_segments,
    EspWireframeMixin.draw_fluorescent_white_polyline,
    EspWireframeMixin.rasterize_fluorescent_white_circle,
    EspWireframeMixin.draw_fluorescent_white_ellipse,
    EspWireframeMixin.draw_armor_fov_circle,
    EspWireframeMixin.draw_white_outline_lock,
    EspWireframeMixin.draw_minimal_bracket_lock,
    EspWireframeMixin.draw_rune_ring_lock,
    EspWireframeMixin.target_overlay_label,
    EspWireframeMixin.armor_esp_points,
    EspWireframeMixin.armor_esp_back_points,
    EspWireframeMixin.draw_3d_esp_box,
    EspWireframeMixin.constrain_prediction_geometry,
    EspWireframeMixin.draw_clean_3d_esp_box,
    EspWireframeMixin.draw_polar_mechanical_wire,
    EspWireframeMixin._vision_wire_key,
    EspWireframeMixin._append_vision_wire_armor,
    EspWireframeMixin.build_vision_wire_batch,
    EspWireframeMixin.paint_gpu_vision_layer,
    EspWireframeMixin.draw_armor_quad,
    EspWireframeMixin.smooth_armor_geometry,
    EspWireframeMixin.draw_armor_3d_esp,
)

for _fn in _QUALNAME_PINNED_MEMBERS:
    _fn.__qualname__ = f"PolarRMWindow.{_fn.__name__}"
del _fn, _QUALNAME_PINNED_MEMBERS
