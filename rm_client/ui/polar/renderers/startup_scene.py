"""阶段 2f 渲染器提取：启动场景渲染族（D4）→ StartupSceneMixin。

（REFACTOR_BLUEPRINT §5 阶段 2 表 2f 行 / §2 D4 节 / §5.6 冻结面 / §6 性能红线）

迁移范围（以 grep 重定位后的实际行号为准，蓝图原行号已因多轮修复漂移；
行号对应阶段 2e 完成时的 polar_app.py 快照）：
- ``paint_gpu_startup`` / ``begin_startup_page_transition`` /
  ``finish_startup_page_transition`` / ``build_gpu_startup_transition``（原 ~1275–1418）
- ``draw_startup_page_content`` / ``draw_startup_page_transition``（原 ~2391–2457）
- ``startup_needs_high_rate``（原 ~4544–4552）
- startup 绘制主体与 liquid 控件族 / 壁纸选择器 / meta 与页面动画复位
  （原 ~5414–6519 连续块）：``draw_startup_background`` / ``draw_particle_grid_background`` /
  ``draw_startup_screen`` / ``draw_robot_profile_screen`` / ``draw_match_mode_screen`` /
  ``draw_startup_scene_launcher`` / ``draw_startup_match_launcher`` /
  ``draw_startup_vignette`` / ``draw_liquid_startup_scene_launcher`` /
  ``draw_liquid_startup_match_launcher`` / ``liquid_menu_layout`` /
  ``startup_stagger_value`` / ``startup_menu_accent``(static) /
  ``startup_glass_settings`` / ``draw_liquid_glass_panel`` / ``draw_legacy_glass_panel`` /
  ``draw_liquid_icon`` / ``draw_liquid_menu_header`` / ``draw_liquid_main_button`` /
  ``draw_liquid_bottom_bar`` / ``set_wallpaper_picker_visible`` / ``draw_wallpaper_picker`` /
  ``draw_wallpaper_card`` / ``draw_image_cover``(static) /
  ``draw_liquid_small_action_button`` / ``draw_liquid_status_panel`` /
  ``draw_startup_icon_card`` / ``draw_startup_card_shadow`` /
  ``startup_scenario_meta`` / ``startup_match_meta`` / ``reset_startup_page_animation``
- ``handle_startup_key`` / ``apply_scenario``（原 ~11143–11203）

边界决策（保守，以调用面分析为准）：
- ``draw_synthetic_video_scene`` **不随 D4 迁移**：虽是启动场景背景绘制，但调用面为
  VisionViewport.paintGL（D0 顶层类）、paint_gpu_background（D2 GPU 帧编排回退）与
  draw_synthetic_video(+cached)（D7 视频管线），蓝图 §2 将其归入 D7——留在 polar_app.py；
- ``liquid_press_amount`` 不随迁：被 D7 draw_hud_liquid_glass、D6 build_gpu_economy_workspace、
  D5 build_gpu_toggle 跨域复用，属共享按压量辅助——留在 polar_app.py；
- ``draw_startup_vignette`` 随 D4 迁移：仅被四个 startup launcher 调用，纯 D4 域内成员；
- ``draw_startup_gpu_background`` / ``paint_gpu_startup_interaction_layer`` 留在 polar_app.py
  （蓝图 §2 归入 D2 GPU 帧编排）；``build_gpu_startup_launcher/_button/_session/
  _bottom_bar/_hover_layer`` / ``build_gpu_robot_profile_launcher`` 留在 polar_app.py
  （蓝图 §2 归入 D5 ClickGUI builders）。

纪律：
- 方法体**零修改**（含注释、空行与装饰器），纯移动进本 Mixin；三组历史重复定义
  （startup_scenario_meta / startup_match_meta / reset_startup_page_animation）已在
  阶段 0 去重，本次移动的即去重后的唯一版本；
- 状态字段（startup_cache / startup_wallpaper / startup_transition_* 等）仍由
  PolarRMWindow.__init__ 初始化，Mixin 只承载方法；
- dispatch（D15）/ keyPressEvent（D15）/ on_tick（D1）对 handle_startup_key /
  apply_scenario / startup_needs_high_rate 的调用面经继承保持不变。

冻结面纪律：与前序阶段（combat_target_hud.py 等）一致——api_freeze_test 对方法类
成员比对 ``__qualname__``（基线值形如 "PolarRMWindow.<name>"），故在本模块底部把
全部迁入方法的 ``__qualname__`` 钉回 ``PolarRMWindow.<name>``，使 ``window.<name>``
签名与迁移前 bit 级一致。
"""

from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)

# 相对导入层级：rm_client/ui/polar/renderers/ → .... = rm_client、... = rm_client.ui
from ....match_modes import MATCH_MODES, MATCH_MODE_KEYS
from ....robot_profiles import profiles_for_match, robot_id_for_alliance
from ....scenarios import SCENARIOS
from ...animation import Easings
from ...clickgui_gpu import ClickGuiDrawList
from ...gpu_frame_compositor import LensMaterial
from ...startup_gl import TextureOrigin
from ...paint import accent_color, glow_rect, glow_text, rounded_rect, text_width, with_alpha
from ...theme import THEME, color
from ...jello_locale import chinese_enabled, ui_text

__all__ = [
    "StartupSceneMixin",
]


class StartupSceneMixin:
    """D4 启动场景渲染：startup 页面/转场、liquid 控件族、壁纸选择器、场景元数据。"""

    def paint_gpu_startup(self) -> bool:
        """GPU-only launcher path. No QPainter touches the window framebuffer."""
        if not self.home_visible or self.wallpaper_picker_visible or not self.clickgui_gpu.available or not self.startup_gpu.available:
            return False
        context = self.context()
        if context is None:
            return False
        module = self.registry.get("StartupVisual")
        mode = self.setting_mode(module, "Background Mode", "VideoParallax")
        profile = self.setting_mode(module, "Wallpaper Profile", "BlackHole")
        if mode in ("ParticleGrid", "PkgScene") or profile in ("ParticleGrid", "Default3000925581Pkg"):
            return False
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return False
        snapshot = self.startup_wallpaper.prepare_gpu_video(
            QRectF(0, 0, width, height),
            self.hover_point,
            self.frame_dt,
            mode=mode,
            profile=profile,
            parallax=self.get_setting_number(module, "Parallax Strength", 22.0),
            zoom=self.get_setting_number(module, "Background Zoom", 1.0),
            high_res=self.high_res_optimize(width, height),
            motion_quality=self.setting_mode(module, "Motion Quality", "Smooth60"),
        )
        if snapshot is None:
            return False
        functions = context.extraFunctions()
        try:
            ratio = self.devicePixelRatioF()
            self.hotspots.clear()
            commands = self.build_gpu_startup_transition(width, height)
            if self.hover_tooltip:
                self.build_gpu_tooltip(commands, self.hover_point, self.hover_tooltip)
            self.build_gpu_toasts(commands, width, height)
            self.clickgui_gpu.prepare_frame(commands, ratio)
            functions.glViewport(0, 0, max(1, round(width * ratio)), max(1, round(height * ratio)))
            functions.glClearColor(0.0, 0.0, 0.0, 1.0)
            functions.glClear(0x00004000)
            self.startup_gpu.update_frame(
                snapshot.image,
                self.startup_wallpaper.last_draw_rect,
                snapshot.sequence,
                snapshot.frame_interval,
                self.get_setting_number(module, "Dim Overlay", 0.28),
                origin=TextureOrigin.TOP_LEFT_IMAGE,
            )
            if not self.startup_gpu.render(functions, QRectF(0, 0, width, height)):
                return False
            return self.clickgui_gpu.render(
                functions,
                commands,
                width,
                height,
                ratio,
                atlas_prepared=True,
            )
        except Exception:
            return False

    def startup_second_step_enabled(self) -> bool:
        """`MatchMode / Second Step`：场景选完后要不要插一级 RMUC/RMUL 选择页。

        这个开关以前完全没人读：四个跳转点全部硬写 "match"，所以无论
        开还是关都必须经二级页。没有该设置项时按 True（保持现行为）。
        """
        module = self.registry.get("MatchMode")
        setting = module.get_setting("Second Step") if module is not None else None
        if setting is None:
            return True
        return bool(getattr(setting, "value", True))

    def startup_page_after_scenario(self) -> str:
        """场景选完后的下一页。"""
        return "match" if self.startup_second_step_enabled() else "robot"

    def startup_page_before_robot(self) -> str:
        """机器人页回退（Backspace）应该回到的上一页。

        必须跟着 `Second Step` 变：否则关掉二级页后，从机器人页
        按退格会跳到一个本来被跳过的页面，流程看起来像坏了。
        """
        return "match" if self.startup_second_step_enabled() else "scenario"

    def begin_startup_page_transition(self, page: str) -> None:
        """Switch launcher pages through one reversible GPU-only timeline."""
        if page not in ("scenario", "match", "robot") or page == self.startup_step:
            return
        self.startup_gpu.discard_interpolation_history()
        self.startup_transition_from = self.startup_step
        self.startup_transition_to = page
        self.startup_transition_started = time.perf_counter()
        self.startup_page_morph.reset(0.0)
        self.startup_page_morph.set_target(
            1.0,
            self.anim_duration(0.42),
            now=self.startup_transition_started,
        )
        self.startup_step = page
        self._startup_page_epoch += 1
        self._startup_static_page_key = None
        self._startup_static_ready_at = self.startup_transition_started + self.anim_duration(0.42)
        self.frame_scheduler.force()

    def finish_startup_page_transition(self) -> None:
        """原子提交新启动页，禁止旧页面纹理继续参与后续合成。"""
        if self.startup_transition_from is not None or self.startup_transition_to is not None:
            self.startup_transition_from = None
            self.startup_transition_to = None
            self._startup_page_epoch += 1
            self._gpu_static_ui_key = None
            self._startup_static_page_key = None
            self.gpu_compositor.clear_static_glass()
            self.gpu_compositor.clear_frame_glass()

    def build_gpu_startup_transition(self, width: int, height: int) -> ClickGuiDrawList:
        """Compose launcher pages in one draw list, never between GL passes.

        The first half contracts the outgoing scene into a DynamicIsland-like
        capsule.  The second half reveals the incoming page.  Both scene lists
        are generated before one renderer submission, so page changes cannot
        expose an uninitialised framebuffer on high-DPR displays.
        """
        outgoing = self.startup_transition_from
        incoming = self.startup_transition_to
        progress = self.startup_page_morph.update(now=time.perf_counter())
        if not outgoing or not incoming or progress >= 1.0:
            self.finish_startup_page_transition()
            return self.build_gpu_startup_launcher(width, height, page=self.startup_step)

        commands = ClickGuiDrawList()
        # Keep the old draw list visual-only: its hotspots must never win a
        # click while the incoming page is already interactive.
        old_commands = self.build_gpu_startup_launcher(
            width,
            height,
            page=outgoing,
            visual_alpha=(1.0 - Easings.ease_out_expo(min(1.0, progress / 0.40))),
        )
        self.hotspots.clear()
        # Leave a short island-only interval instead of cross-fading two full
        # glass pages.  Overlapping their translucent panels was perceived as
        # a bright flash on 2K displays.
        reveal = Easings.ease_out_expo(max(0.0, min(1.0, (progress - 0.58) / 0.42)))
        new_commands = self.build_gpu_startup_launcher(width, height, page=incoming, visual_alpha=reveal)
        commands.extend(old_commands)
        commands.extend(new_commands)

        # A restrained island replaces the old full-page flash.  It stays in
        # the same top anchor used by the launcher title and is GPU geometry.
        island_t = 1.0 - abs(progress * 2.0 - 1.0)
        island_w = 108.0 + 268.0 * Easings.ease_out_expo(island_t)
        island_h = 14.0 + 25.0 * Easings.ease_out_expo(island_t)
        island = QRectF(width / 2.0 - island_w / 2.0, 28.0, island_w, island_h)
        if self._gpu_frame_recording:
            self.gpu_compositor.add_glass(
                island,
                island_h / 2.0,
                strength=1.02,
                surface="clear",
                hover=0.0,
                opacity=1.0,
                material=LensMaterial.ISLAND,
            )
        if island_t > 0.24:
            commands.text(island, "POLAR-RM", self.font("poppins_bold", 7.2), color("#ffffff", island_t), Qt.AlignmentFlag.AlignCenter)
        return commands
    def draw_startup_page_content(self, p: QPainter, width: int, height: int, page: str) -> None:
        if page == "robot":
            self.draw_robot_profile_screen(p, width, height)
        elif page == "match":
            self.draw_match_mode_screen(p, width, height)
        else:
            self.draw_startup_screen(p, width, height)

    def draw_startup_page_transition(self, p: QPainter, width: int, height: int) -> None:
        """用单时间线把启动页面收进顶部胶囊，再从胶囊展开新页面。"""
        outgoing = self.startup_transition_from
        incoming = self.startup_transition_to
        duration = self.anim_duration(0.42)
        elapsed = time.perf_counter() - self.startup_transition_started
        progress = max(0.0, min(1.0, elapsed / max(0.001, duration)))
        if not outgoing or not incoming or progress >= 1.0:
            self.finish_startup_page_transition()
            self.draw_startup_page_content(p, width, height, self.startup_step)
            return

        collapse = Easings.ease_out_expo(min(1.0, progress / 0.43))
        reveal = Easings.ease_out_expo(max(0.0, min(1.0, (progress - 0.57) / 0.43)))

        p.save()
        p.setOpacity(1.0 - collapse)
        old_scale = 1.0 - collapse * 0.018
        p.translate(width / 2.0, 34.0)
        p.scale(old_scale, old_scale)
        p.translate(-width / 2.0, -34.0 - collapse * 8.0)
        self.draw_startup_page_content(p, width, height, outgoing)
        p.restore()

        # 过渡期间只保留新页面热点，避免两层透明卡片抢点击。
        self.hotspots.clear()
        if reveal > 0.001:
            p.save()
            p.setOpacity(reveal)
            new_scale = 0.982 + reveal * 0.018
            p.translate(width / 2.0, 34.0)
            p.scale(new_scale, new_scale)
            p.translate(-width / 2.0, -34.0 + (1.0 - reveal) * 8.0)
            self.draw_startup_page_content(p, width, height, incoming)
            p.restore()

        island_phase = 1.0 - abs(progress * 2.0 - 1.0)
        island_amount = Easings.ease_out_expo(island_phase)
        island_w = 116.0 + 250.0 * island_amount
        island_h = 22.0 + 18.0 * island_amount
        island = QRectF(width / 2.0 - island_w / 2.0, 24.0, island_w, island_h)
        radius = island.height() / 2.0
        self.draw_liquid_glass_panel(
            p,
            island,
            radius,
            accent=THEME.cyan,
            hover=0.0,
            surface="ModalGlass",
        )
        if island_amount > 0.12:
            self.draw_glass_text(
                p,
                island,
                "POLAR-RM",
                self.font("poppins_medium", 7.6),
                color("#ffffff", min(1.0, island_amount * 1.4)),
                Qt.AlignmentFlag.AlignCenter,
            )
    def startup_needs_high_rate(self) -> bool:
        module = self.registry.get("StartupVisual")
        mode = self.setting_mode(module, "Background Mode", "VideoParallax")
        profile = self.setting_mode(module, "Wallpaper Profile", "BlackHole")
        if mode in ("ParticleGrid", "VideoParallax"):
            return True
        if profile not in ("ParticleGrid", "Default3000925581Pkg"):
            return True
        return time.perf_counter() - self.startup_transition_started < 0.85
    def draw_startup_background(self, p: QPainter, width: int, height: int) -> None:
        module = self.registry.get("StartupVisual")
        mode = self.setting_mode(module, "Background Mode", "VideoParallax")
        profile = self.setting_mode(module, "Wallpaper Profile", "BlackHole")
        parallax = self.get_setting_number(module, "Parallax Strength", 22.0)
        zoom = self.get_setting_number(module, "Background Zoom", 1.00)
        dim = self.get_setting_number(module, "Dim Overlay", 0.28)
        motion_quality = self.setting_mode(module, "Motion Quality", "Smooth60")
        if mode != "ParticleGrid":
            drawn = self.startup_wallpaper.draw(
                p,
                QRectF(0, 0, width, height),
                self.hover_point,
                self.frame_dt,
                mode=mode,
                profile=profile,
                parallax=parallax,
                zoom=zoom,
                dim=dim,
                high_res=self.high_res_optimize(width, height),
                motion_quality=motion_quality,
            )
            if drawn:
                if not self._gpu_frame_recording:
                    self.liquid_glass.update(
                        "startup",
                        self.startup_wallpaper.last_frame,
                        self.startup_wallpaper.last_draw_rect,
                        soft=self.startup_wallpaper.soft_glass_frame,
                        lens=self.startup_wallpaper.glass_frame,
                    )
                return
        if profile == "BlackHole":
            self.liquid_glass.discard("startup")
        self.draw_particle_grid_background(p, width, height)

    def draw_particle_grid_background(self, p: QPainter, width: int, height: int) -> None:
        now = time.perf_counter()
        lite = self.high_res_optimize(width, height)
        bg = QLinearGradient(0, 0, 0, height)
        bg.setColorAt(0.0, color("#060910"))
        bg.setColorAt(0.52, color("#0c111b"))
        bg.setColorAt(1.0, color("#05070c"))
        p.fillRect(QRectF(0, 0, width, height), bg)

        horizon = height * 0.58
        mouse = self.hover_point
        if self.home_visible:
            target_x = mouse.x() if mouse.x() > 1 or mouse.y() > 1 else width * 0.5
            target_y = mouse.y() if mouse.x() > 1 or mouse.y() > 1 else height * 0.45
            mouse = QPointF(
                self.anim.value("startup.bg.mouse.x", target_x, self.anim_duration(0.12), Easings.ease_out_sine, initial=width * 0.5),
                self.anim.value("startup.bg.mouse.y", target_y, self.anim_duration(0.12), Easings.ease_out_sine, initial=height * 0.45),
            )
        spacing = max(52.0, min(88.0, width / (15.5 if lite else 18.0)))
        points: list[QPointF] = []
        row_count = 7 if lite else 9
        for row in range(0, row_count):
            depth = row / max(1.0, row_count - 1.0)
            y = horizon + depth * depth * height * 0.46
            row_spacing = spacing * (0.72 + depth * 1.15)
            offset = (now * 18.0 + row * 31.0) % row_spacing
            x = -row_spacing + offset
            while x < width + row_spacing:
                dx = x - mouse.x()
                dy = y - mouse.y()
                dist = math.sqrt(dx * dx + dy * dy)
                pull = max(0.0, 1.0 - dist / 260.0)
                wave = math.sin(now * 1.7 + x * 0.013 + row * 0.8) * 5.0
                px = x + dx * pull * 0.035
                py = y + wave + dy * pull * 0.025
                points.append(QPointF(px, py))
                x += row_spacing

        glow_band = QLinearGradient(0, horizon - 70, 0, horizon + 120)
        glow_band.setColorAt(0.0, color("#56fff0", 0.00))
        glow_band.setColorAt(0.48, color("#56fff0", 0.10))
        glow_band.setColorAt(1.0, color("#ff65df", 0.035))
        p.fillRect(QRectF(0, horizon - 70, width, 190), glow_band)

        p.setPen(QPen(color("#2f6173", 0.62), 1.0))
        for point in points:
            p.drawLine(QPointF(point.x(), horizon), point)
        for index, a in enumerate((0.34, 0.20, 0.11)):
            p.setPen(QPen(color("#56fff0", a), 1.0 + index * 0.45))
            for i, point in enumerate(points):
                if i + 1 < len(points) and abs(points[i + 1].y() - point.y()) < spacing * 0.75:
                    p.drawLine(point, points[i + 1])

        p.setPen(Qt.PenStyle.NoPen)
        star_count = 28 if lite else 42
        for i in range(star_count):
            drift = now * (10.0 + i % 5) + i * 97.0
            px = (drift + math.sin(now * 0.7 + i) * 42.0) % max(1, width)
            py = height * (0.10 + ((i * 0.071 + now * 0.006) % 0.42))
            pulse = 0.5 + 0.5 * math.sin(now * 2.1 + i * 0.53)
            p.setBrush(color("#56fff0", 0.10 + pulse * 0.13))
            p.drawEllipse(QPointF(px, py), 0.8 + pulse * 1.0, 0.8 + pulse * 1.0)

        for i, point in enumerate(points):
            dx = point.x() - mouse.x()
            dy = point.y() - mouse.y()
            dist = math.sqrt(dx * dx + dy * dy)
            pulse = 0.5 + 0.5 * math.sin(now * 3.2 + i * 0.37)
            near = max(0.0, 1.0 - dist / 230.0)
            radius = 1.8 + 2.8 * near + 1.0 * pulse
            c = QColor.fromHsvF((0.48 + i * 0.006 + now * 0.015) % 1.0, 0.62, 1.0, 0.34 + 0.48 * near)
            p.setBrush(c)
            p.drawEllipse(point, radius, radius)

        orb = QLinearGradient(0, 0, width, height)
        orb.setColorAt(0.0, color("#56fff0", 0.12))
        orb.setColorAt(0.55, color("#ff65df", 0.065))
        orb.setColorAt(1.0, color("#000000", 0.0))
        p.fillRect(QRectF(0, 0, width, height), orb)

    def draw_startup_screen(self, p: QPainter, width: int, height: int) -> None:
        active = self.anim.value("startup.open", 1.0, self.anim_duration(0.34), Easings.ease_out_pow4, initial=0.0)
        scenario_module = self.registry.get("Scenario")
        style_setting = scenario_module.get_setting("Startup Style") if scenario_module else None
        if style_setting is not None and str(style_setting.value) == "PolarLauncher":
            self.draw_startup_scene_launcher(p, width, height, active)
            self.draw_liquid_small_action_button(p, QRectF(48, height - 55, 110, 34),
                                                "Settings", "startup_open_settings", None,
                                                THEME.cyan, 1.0, "Settings")
        else:
            self.draw_liquid_startup_scene_launcher(p, width, height, active)
        return
        p.save()
        p.setOpacity(active)
        p.fillRect(QRectF(0, 0, width, height), color("#020205", 0.42))

        scale = max(0.86, min(1.85, self.get_auto_ui_scale(width, height, strength=0.65)))
        title_font = self.font("museo", 30 * scale, QFont.Weight.Bold)
        body_font = self.font("poppins_medium", 10.5 * scale)
        small_font = self.font("poppins_regular", 8.0 * scale)
        menu_w = min(430.0 * scale, width - 88.0)
        row_h = 62.0 * scale
        gap = 11.0 * scale
        total_h = row_h * 3 + gap * 2
        x = width / 2 - menu_w / 2
        y = height / 2 - total_h / 2 + 22.0 * scale

        logo_y = max(72.0 * scale, y - 92.0 * scale)
        glow_text(p, "POLAR-RM", x, logo_y, title_font, THEME.text, THEME.cyan, 8 * scale)
        self.draw_text(p, QRectF(x, logo_y + 10 * scale, menu_w, 44 * scale), ui_text("CUSTOM CLIENT", chinese_enabled(self.registry)), body_font, THEME.muted, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        order = ["OfficialEvent", "CampusSimulation", "LabDebug"]
        for index, key in enumerate(order):
            config = SCENARIOS[key]
            rect = QRectF(x, y + index * (row_h + gap), menu_w, row_h)
            hovered = rect.contains(self.hover_point)
            hover_v = self.anim.value(f"startup.hover.{key}", 1.0 if hovered else 0.0, self.anim_duration(0.18), Easings.ease_out_quad)
            slide = (1.0 - active) * 28.0 * scale + (1.0 - hover_v) * 0.0
            draw_rect = rect.adjusted(slide, 0, -slide, 0)
            accent = self.startup_menu_accent(index)
            glow_rect(p, draw_rect, 9 * scale, 0.12 + 0.10 * hover_v)
            rounded_rect(p, draw_rect, 8 * scale, color("#09090d", 0.88 + 0.05 * hover_v), color("#ffffff", 0.10 + 0.16 * hover_v))
            # 死代码区段（函数头部无条件 return）：原左缘方角 accent 条已删（圆角卫生项）
            self.draw_text(p, draw_rect.adjusted(18 * scale, 9 * scale, -104 * scale, -28 * scale), ui_text(config.title, chinese_enabled(self.registry)), self.font("poppins_bold", 13.2 * scale), THEME.text)
            self.draw_text(p, draw_rect.adjusted(18 * scale, 31 * scale, -18 * scale, -8 * scale), ui_text(config.subtitle, chinese_enabled(self.registry)), small_font, THEME.muted)
            safety = "MOCK" if config.safety.mock_writes else "LIVE"
            chip = QRectF(draw_rect.right() - 82 * scale, draw_rect.center().y() - 11 * scale, 64 * scale, 22 * scale)
            rounded_rect(p, chip, 7 * scale, color("#ffffff", 0.055), color("#ffffff", 0.10 + 0.12 * hover_v))
            self.draw_text(p, chip, ui_text(safety, chinese_enabled(self.registry)), self.font("poppins_medium", 7.3 * scale), accent, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(draw_rect, "select_scenario", key, tooltip=ui_text(config.description, chinese_enabled(self.registry)))

        current = self.scenario_manager.current
        footer = f"{current.title} | MQTT {current.connection.mqtt_host}:{current.connection.mqtt_port} | UDP {current.connection.udp_port}"
        self.draw_text(p, QRectF(x, y + total_h + 22 * scale, menu_w, 18 * scale), ui_text(f"Step 1/3 | {footer}", chinese_enabled(self.registry)), small_font, color("#888890"), Qt.AlignmentFlag.AlignCenter)
        p.restore()

    def draw_robot_profile_screen(self, p: QPainter, width: int, height: int) -> None:
        scale = max(0.72, min(1.12, self.get_auto_ui_scale(width, height, strength=0.42)))
        profiles = profiles_for_match(self.match_mode_manager.current.key)
        selected = self.current_robot_profile_key()
        top = 112 * scale
        margin = 48 * scale
        columns = 4 if width >= 1500 else 3
        gap = 14 * scale
        usable_w = width - margin * 2
        card_w = (usable_w - gap * (columns - 1)) / columns
        card_h = min(135 * scale, (height - top - 90 * scale) / max(1, math.ceil(len(profiles) / columns)) - gap)
        self.draw_text(p, QRectF(0, 38 * scale, width, 30 * scale), ui_text("ROBOT PROFILE · STEP 3/3", chinese_enabled(self.registry)), self.font("poppins_bold", 12 * scale), THEME.text, Qt.AlignmentFlag.AlignCenter)
        for index, profile in enumerate(profiles):
            rect = QRectF(margin + (index % columns) * (card_w + gap), top + (index // columns) * (card_h + gap), card_w, card_h)
            accent = self.startup_menu_accent(index + 1)
            self.draw_liquid_glass_panel(p, rect, 12 * scale, accent=accent, hover=0.55 if profile.key == selected else 0.0, scale=scale)
            self.draw_text(p, rect.adjusted(18 * scale, 14 * scale, -18 * scale, -rect.height() + 40 * scale), ui_text(f"{profile.short_title}  ·  ID {robot_id_for_alliance(profile, self.alliance_side())}", chinese_enabled(self.registry)), self.font("poppins_bold", 10 * scale), THEME.text)
            self.draw_text(p, rect.adjusted(18 * scale, 49 * scale, -18 * scale, -12 * scale), ui_text(profile.description, chinese_enabled(self.registry)), self.font("poppins_regular", 7.4 * scale), THEME.muted, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.add_hotspot(rect, "select_robot_profile", profile.key)
        back = QRectF(margin, height - 55 * scale, 110 * scale, 34 * scale)
        self.draw_liquid_glass_panel(p, back, 11 * scale, accent=THEME.cyan, hover=0.0, scale=scale)
        self.draw_text(p, back, ui_text("<  BACK", chinese_enabled(self.registry)), self.font("poppins_bold", 7.5 * scale), THEME.text, Qt.AlignmentFlag.AlignCenter)
        self.add_hotspot(back, "startup_back_match")
        settings = QRectF(back.right() + 14 * scale, back.top(), 110 * scale, back.height())
        self.draw_liquid_small_action_button(p, settings, "Settings", "startup_open_settings", None,
                                            THEME.cyan, scale, "Settings")

    def draw_match_mode_screen(self, p: QPainter, width: int, height: int) -> None:
        active = self.anim.value("startup.match.open", 1.0, self.anim_duration(0.34), Easings.ease_out_pow4, initial=0.0)
        scenario_module = self.registry.get("Scenario")
        style_setting = scenario_module.get_setting("Startup Style") if scenario_module else None
        if style_setting is not None and str(style_setting.value) == "PolarLauncher":
            self.draw_startup_match_launcher(p, width, height, active)
            self.draw_liquid_small_action_button(p, QRectF(48, height - 55, 110, 34),
                                                "Settings", "startup_open_settings", None,
                                                THEME.cyan, 1.0, "Settings")
        else:
            self.draw_liquid_startup_match_launcher(p, width, height, active)
        return
        p.save()
        p.setOpacity(active)
        p.fillRect(QRectF(0, 0, width, height), color("#020205", 0.44))

        scale = max(0.86, min(1.85, self.get_auto_ui_scale(width, height, strength=0.65)))
        title_font = self.font("museo", 28 * scale, QFont.Weight.Bold)
        body_font = self.font("poppins_medium", 10.5 * scale)
        small_font = self.font("poppins_regular", 8.0 * scale)
        menu_w = min(470.0 * scale, width - 88.0)
        row_h = 76.0 * scale
        gap = 12.0 * scale
        total_h = row_h * len(MATCH_MODE_KEYS) + gap * (len(MATCH_MODE_KEYS) - 1)
        x = width / 2 - menu_w / 2
        y = height / 2 - total_h / 2 + 38.0 * scale

        logo_y = max(74.0 * scale, y - 104.0 * scale)
        glow_text(p, "MATCH MODE", x, logo_y, title_font, THEME.text, THEME.cyan, 8 * scale)
        self.draw_text(p, QRectF(x, logo_y + 10 * scale, menu_w, 44 * scale), ui_text(self.scenario_manager.current.title.upper(), chinese_enabled(self.registry)), body_font, THEME.muted, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        for index, key in enumerate(MATCH_MODE_KEYS):
            config = MATCH_MODES[key]
            rect = QRectF(x, y + index * (row_h + gap), menu_w, row_h)
            hovered = rect.contains(self.hover_point)
            hover_v = self.anim.value(f"startup.match.hover.{key}", 1.0 if hovered else 0.0, self.anim_duration(0.18), Easings.ease_out_quad)
            draw_rect = rect.adjusted((1.0 - active) * 20.0 * scale, 0, 0, 0)
            accent = self.startup_menu_accent(index + 2)
            glow_rect(p, draw_rect, 10 * scale, 0.14 + 0.10 * hover_v)
            rounded_rect(p, draw_rect, 8 * scale, color("#09090d", 0.90 + 0.05 * hover_v), color("#ffffff", 0.10 + 0.16 * hover_v))
            # 死代码区段（函数头部无条件 return）：原左缘方角 accent 条已删（圆角卫生项）
            self.draw_text(p, draw_rect.adjusted(18 * scale, 8 * scale, -140 * scale, -45 * scale), ui_text(config.title, chinese_enabled(self.registry)), self.font("poppins_bold", 13.6 * scale), THEME.text)
            details = f"{config.field.width_m:g}m x {config.field.height_m:g}m | official map asset | {config.duration_sec // 60}:{config.duration_sec % 60:02d}"
            self.draw_text(p, draw_rect.adjusted(18 * scale, 34 * scale, -18 * scale, -24 * scale), ui_text(config.subtitle, chinese_enabled(self.registry)), small_font, THEME.muted)
            self.draw_text(p, draw_rect.adjusted(18 * scale, 52 * scale, -18 * scale, -6 * scale), ui_text(details, chinese_enabled(self.registry)), small_font, color("#9fa7ad"))
            chip = QRectF(draw_rect.right() - 110 * scale, draw_rect.center().y() - 12 * scale, 88 * scale, 24 * scale)
            rounded_rect(p, chip, 7 * scale, color("#ffffff", 0.055), color("#ffffff", 0.10 + 0.12 * hover_v))
            self.draw_text(p, chip, ui_text("NEXT", chinese_enabled(self.registry)), self.font("poppins_medium", 7.3 * scale), accent, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(draw_rect, "select_match_mode", key, tooltip=ui_text(f"载入 {config.title} 官方地图和默认 HUD。", chinese_enabled(self.registry)))

        footer = "Step 2/3 | Backspace: scenario | Enter: robot selection"
        self.draw_text(p, QRectF(x, y + total_h + 22 * scale, menu_w, 18 * scale), ui_text(footer, chinese_enabled(self.registry)), small_font, color("#888890"), Qt.AlignmentFlag.AlignCenter)
        p.restore()

    def draw_startup_scene_launcher(self, p: QPainter, width: int, height: int, active: float) -> None:
        p.save()
        p.setOpacity(active)
        p.fillRect(QRectF(0, 0, width, height), color("#020205", 0.34))
        self.draw_startup_vignette(p, width, height)

        scale = max(0.82, min(1.65, self.get_auto_ui_scale(width, height, strength=0.65)))
        title_font = self.font("museo", 34 * scale, QFont.Weight.Bold)
        body_font = self.font("poppins_medium", 10.5 * scale)
        small_font = self.font("poppins_regular", 8.0 * scale)
        gap = 18.0 * scale
        # 四个场景：卡片宽度按 4 列计算，保证 1280宽也不会重叠或溢出
        card_w = min(292.0 * scale, (width - 112.0 * scale - gap * 3.0) / 4.0)
        card_h = 166.0 * scale
        total_w = card_w * 4.0 + gap * 3.0
        x = width / 2.0 - total_w / 2.0
        y = height / 2.0 - card_h / 2.0 + 26.0 * scale

        logo_y = max(78.0 * scale, y - 112.0 * scale)
        glow_text(p, "POLAR-RM", x, logo_y, title_font, THEME.text, THEME.cyan, 9 * scale)
        self.draw_text(p, QRectF(x, logo_y + 11 * scale, total_w, 42 * scale), ui_text("LAUNCHER / SCENE SELECT", chinese_enabled(self.registry)), body_font, THEME.muted, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.draw_text(p, QRectF(x, logo_y + 46 * scale, total_w, 18 * scale), ui_text("选择运行环境", chinese_enabled(self.registry)), self.font("poppins_medium", 8.5 * scale), color("#c9d6dc", 0.90))

        for index, key in enumerate(("OfficialEvent", "CampusSimulation", "LabDebug")):
            config = SCENARIOS[key]
            meta = self.startup_scenario_meta(key)
            rect = QRectF(x + index * (card_w + gap), y, card_w, card_h)
            detail = f"MQTT {config.connection.mqtt_host}:{config.connection.mqtt_port}"
            chip = "MOCK" if config.safety.mock_writes else "LIVE"
            self.draw_startup_icon_card(
                p,
                rect,
                index,
                key,
                meta["icon"],
                meta["title"],
                meta["subtitle"],
                detail,
                chip,
                "select_scenario",
                key,
                meta["tooltip"],
                self.startup_menu_accent(index),
                scale,
                active,
            )

        current_meta = self.startup_scenario_meta(self.scenario_manager.current.key)
        footer = ui_text("Step 1/3 | 当前: {title} | 按 1/2/3 快速选择", chinese_enabled(self.registry), title=ui_text(current_meta["title"], chinese_enabled(self.registry)))
        self.draw_text(p, QRectF(x, y + card_h + 27 * scale, total_w, 18 * scale), ui_text(footer, chinese_enabled(self.registry)), small_font, color("#aeb7bd", 0.88), Qt.AlignmentFlag.AlignCenter)
        p.restore()

    def draw_startup_match_launcher(self, p: QPainter, width: int, height: int, active: float) -> None:
        p.save()
        p.setOpacity(active)
        p.fillRect(QRectF(0, 0, width, height), color("#020205", 0.36))
        self.draw_startup_vignette(p, width, height)

        scale = max(0.82, min(1.65, self.get_auto_ui_scale(width, height, strength=0.65)))
        title_font = self.font("museo", 32 * scale, QFont.Weight.Bold)
        body_font = self.font("poppins_medium", 10.5 * scale)
        small_font = self.font("poppins_regular", 8.0 * scale)
        gap = 24.0 * scale
        card_w = min(370.0 * scale, (width - 112.0 * scale - gap) / 2.0)
        card_h = 186.0 * scale
        total_w = card_w * 2.0 + gap
        x = width / 2.0 - total_w / 2.0
        y = height / 2.0 - card_h / 2.0 + 30.0 * scale

        scenario_meta = self.startup_scenario_meta(self.scenario_manager.current.key)
        logo_y = max(78.0 * scale, y - 116.0 * scale)
        glow_text(p, "MATCH MODE", x, logo_y, title_font, THEME.text, THEME.cyan, 9 * scale)
        self.draw_text(p, QRectF(x, logo_y + 11 * scale, total_w, 42 * scale), ui_text(scenario_meta["title"].upper(), chinese_enabled(self.registry)), body_font, THEME.muted, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.draw_text(p, QRectF(x, logo_y + 46 * scale, total_w, 18 * scale), ui_text("选择比赛制式", chinese_enabled(self.registry)), self.font("poppins_medium", 8.5 * scale), color("#c9d6dc", 0.90))

        for index, key in enumerate(MATCH_MODE_KEYS):
            config = MATCH_MODES[key]
            meta = self.startup_match_meta(key)
            rect = QRectF(x + index * (card_w + gap), y, card_w, card_h)
            detail = f"{config.field.width_m:g}m x {config.field.height_m:g}m | {config.duration_sec // 60}:{config.duration_sec % 60:02d}"
            self.draw_startup_icon_card(
                p,
                rect,
                index,
                f"match.{key}",
                meta["icon"],
                meta["title"],
                meta["subtitle"],
                detail,
                "NEXT",
                "select_match_mode",
                key,
                meta["tooltip"],
                self.startup_menu_accent(index + 2),
                scale,
                active,
            )

        footer = "Step 2/3 | Backspace 返回 | Enter 选择机器人"
        self.draw_text(p, QRectF(x, y + card_h + 27 * scale, total_w, 18 * scale), ui_text(footer, chinese_enabled(self.registry)), small_font, color("#aeb7bd", 0.88), Qt.AlignmentFlag.AlignCenter)
        p.restore()

    def draw_startup_vignette(self, p: QPainter, width: int, height: int) -> None:
        p.drawImage(QRectF(0, 0, width, height), self.startup_cache.vignette(width, height))

    def draw_liquid_startup_scene_launcher(self, p: QPainter, width: int, height: int, active: float) -> None:
        p.save()
        p.setOpacity(active)
        p.fillRect(QRectF(0, 0, width, height), color("#071018", 0.035))
        self.draw_startup_vignette(p, width, height)

        scale = max(0.74, min(1.14, self.get_auto_ui_scale(width, height, strength=0.45)))
        menu_x, menu_y, button_w, button_h, gap = self.liquid_menu_layout(width, height, 3, scale)
        self.draw_liquid_menu_header(p, width, height, scale, "SCENE SELECT", "Choose runtime environment", active)

        for index, key in enumerate(("OfficialEvent", "CampusSimulation", "LabDebug")):
            config = SCENARIOS[key]
            meta = self.startup_scenario_meta(key)
            rect = QRectF(menu_x, menu_y + index * (button_h + gap), button_w, button_h)
            detail = f"MQTT {config.connection.mqtt_host}:{config.connection.mqtt_port}  UDP {config.connection.udp_port}"
            chip = "LIVE" if not config.safety.mock_writes else "MOCK"
            self.draw_liquid_main_button(
                p,
                rect,
                index,
                f"scene.{key}",
                meta["icon"],
                meta["title"],
                meta["subtitle"],
                detail,
                chip,
                "select_scenario",
                key,
                meta["tooltip"],
                self.startup_menu_accent(index),
                scale,
                active,
            )

        self.draw_liquid_status_panel(p, menu_x, menu_y, button_w, height, scale, "scenario", active)
        self.draw_liquid_bottom_bar(p, width, height, scale, active)
        p.restore()

    def draw_liquid_startup_match_launcher(self, p: QPainter, width: int, height: int, active: float) -> None:
        p.save()
        p.setOpacity(active)
        p.fillRect(QRectF(0, 0, width, height), color("#071018", 0.035))
        self.draw_startup_vignette(p, width, height)

        scale = max(0.74, min(1.14, self.get_auto_ui_scale(width, height, strength=0.45)))
        menu_x, menu_y, button_w, button_h, gap = self.liquid_menu_layout(width, height, 3, scale)
        scenario_meta = self.startup_scenario_meta(self.scenario_manager.current.key)
        self.draw_liquid_menu_header(p, width, height, scale, "MATCH MODE", ui_text("{title} profile", chinese_enabled(self.registry), title=ui_text(scenario_meta["title"], chinese_enabled(self.registry))), active)

        for index, key in enumerate(MATCH_MODE_KEYS):
            config = MATCH_MODES[key]
            meta = self.startup_match_meta(key)
            rect = QRectF(menu_x, menu_y + index * (button_h + gap), button_w, button_h)
            detail = f"{config.field.width_m:g}m x {config.field.height_m:g}m  {config.duration_sec // 60}:{config.duration_sec % 60:02d}  official map"
            self.draw_liquid_main_button(
                p,
                rect,
                index,
                f"match.{key}",
                meta["icon"],
                meta["title"],
                meta["subtitle"],
                detail,
                "NEXT",
                "select_match_mode",
                key,
                meta["tooltip"],
                self.startup_menu_accent(index + 2),
                scale,
                active,
            )

        back_rect = QRectF(menu_x, menu_y + 2 * (button_h + gap), button_w, button_h)
        self.draw_liquid_main_button(
            p,
            back_rect,
            2,
            "match.back",
            "<",
            "Back",
            "Return to scene select",
            "Backspace also works",
            "BACK",
            "startup_back",
            None,
            "Return to the scene launcher.",
            THEME.pink,
            scale,
            active,
        )

        self.draw_liquid_status_panel(p, menu_x, menu_y, button_w, height, scale, "match", active)
        self.draw_liquid_bottom_bar(p, width, height, scale, active)
        p.restore()

    def liquid_menu_layout(self, width: int, height: int, count: int, scale: float) -> tuple[float, float, float, float, float]:
        margin = 50.0 * scale
        button_w = min(590.0 * scale, max(430.0 * scale, width * 0.50))
        button_w = min(button_w, width - margin * 2.0)
        button_h = 118.0 * scale
        gap = 18.0 * scale
        total_h = button_h * count + gap * (count - 1)
        y = max(112.0 * scale, height / 2.0 - total_h / 2.0 + 16.0 * scale)
        max_y = max(94.0 * scale, height - total_h - 105.0 * scale)
        y = min(y, max_y)
        return margin, y, button_w, button_h, gap

    def startup_stagger_value(self, index: int, duration: float = 0.40, delay: float = 0.10) -> float:
        elapsed = time.perf_counter() - getattr(self, "startup_transition_started", time.perf_counter())
        elapsed -= max(0, index) * self.anim_duration(delay)
        if elapsed <= 0.0:
            return 0.0
        return Easings.back_out(elapsed / self.anim_duration(duration))

    @staticmethod
    def startup_menu_accent(index: int) -> QColor:
        """启动页使用稳定语义色，不用时间驱动色相漂移。"""
        palette = ("#66a8ff", "#5fd3aa", "#f0bf68", "#9f92ff")
        return color(palette[index % len(palette)])

    def startup_glass_settings(self) -> tuple[str, float]:
        module = self.registry.get("StartupVisual")
        style = self.setting_mode(module, "Glass Style", "LiquidGlass")
        strength = max(0.40, min(1.40, self.get_setting_number(module, "Glass Strength", 1.0)))
        return style, strength

    def draw_liquid_glass_panel(
        self,
        p: QPainter,
        rect: QRectF,
        radius: float,
        *,
        accent: QColor | None = None,
        hover: float = 0.0,
        scale: float = 1.0,
        sample_background: bool = True,
        surface: str = "MainGlass",
    ) -> None:
        accent = accent or THEME.cyan
        hover = max(0.0, min(1.0, hover))
        glass_style, strength = self.startup_glass_settings()
        if glass_style == "Legacy":
            self.draw_legacy_glass_panel(p, rect, radius, accent, hover, scale, sample_background)
            return
        if self._gpu_frame_recording and self.gpu_compositor.available:
            mapped = p.transform().mapRect(rect)
            is_island = rect.height() <= 58.0 and radius >= rect.height() * 0.44
            # ModalGlass / ProminentGlass = clear STRONG so center shows Combined Backdrop
            # (README LensStrong: no fog). SubGlass stays soft REGULAR for chips.
            glass_surface = (
                "sub" if surface == "SubGlass"
                else "clear" if surface in ("ModalGlass", "ProminentGlass") or is_island
                else "main"
            )
            glass_material = (
                LensMaterial.ISLAND if is_island or surface == "LensIsland"
                else LensMaterial.STRONG if surface in ("ModalGlass", "ProminentGlass")
                else LensMaterial.REGULAR
            )
            self.gpu_compositor.add_glass(
                mapped,
                radius * max(abs(p.transform().m11()), abs(p.transform().m22()), 0.01),
                strength=strength * (1.18 if surface == "ModalGlass" else 1.08 if surface == "ProminentGlass" else 0.78 if surface == "SubGlass" else 1.0),
                surface=glass_surface,
                hover=hover,
                opacity=p.opacity(),
                material=glass_material,
                # Slightly taller edge band on large modals so refraction reads on 1K/2K.
                refraction_height_px=(14.0 if surface == "ModalGlass" else None),
                refraction_amount_px=(18.0 if surface == "ModalGlass" else None),
            )
            return
        mode = "prominent" if surface == "ModalGlass" or hover > 0.34 else "standard"
        self.liquid_glass.draw(
            p, rect, radius, source="startup", mode=mode,
            surface="sub" if surface == "SubGlass" else "main",
            hover=hover * strength, press=self.liquid_press_amount(rect), pointer=self.hover_point,
            lite=self.high_res_optimize(),
        )

    def draw_legacy_glass_panel(
        self,
        p: QPainter,
        rect: QRectF,
        radius: float,
        accent: QColor,
        hover: float,
        scale: float,
        sample_background: bool,
    ) -> None:
        # Legacy 是普通半透明面板，不再复用旧镜片采样链路。
        rounded_rect(p, rect, radius, color("#16212b", 0.34), color("#ffffff", 0.18 + 0.12 * hover), 1.0 * scale)
        if hover > 0.001:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(with_alpha(accent, 0.28 * hover), 1.1 * scale))
            p.drawRoundedRect(rect.adjusted(-1.0 * scale, -1.0 * scale, 1.0 * scale, 1.0 * scale), radius + scale, radius + scale)

    def draw_liquid_icon(self, p: QPainter, rect: QRectF, icon: str, c: QColor, scale: float) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(c, max(1.8, 2.4 * scale), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx = rect.center().x()
        cy = rect.center().y()
        r = min(rect.width(), rect.height()) * 0.34
        key = icon.lower()
        if key in ("official", "dji"):
            shield = QPolygonF(
                [
                    QPointF(cx, cy - r),
                    QPointF(cx + r * 0.82, cy - r * 0.56),
                    QPointF(cx + r * 0.65, cy + r * 0.58),
                    QPointF(cx, cy + r),
                    QPointF(cx - r * 0.65, cy + r * 0.58),
                    QPointF(cx - r * 0.82, cy - r * 0.56),
                ]
            )
            p.drawPolygon(shield)
            p.drawLine(QPointF(cx - r * 0.42, cy), QPointF(cx + r * 0.42, cy))
            p.drawLine(QPointF(cx, cy - r * 0.48), QPointF(cx, cy + r * 0.58))
        elif key in ("sim", "campus"):
            nodes = [
                QPointF(cx - r * 0.72, cy + r * 0.22),
                QPointF(cx, cy - r * 0.58),
                QPointF(cx + r * 0.78, cy + r * 0.18),
                QPointF(cx + r * 0.08, cy + r * 0.72),
            ]
            for a, b in ((0, 1), (1, 2), (0, 3), (2, 3)):
                p.drawLine(nodes[a], nodes[b])
            for point in nodes:
                p.drawEllipse(point, r * 0.18, r * 0.18)
        elif key in ("lab", "debug"):
            p.drawLine(QPointF(cx - r * 0.30, cy - r * 0.82), QPointF(cx + r * 0.30, cy - r * 0.82))
            p.drawLine(QPointF(cx - r * 0.14, cy - r * 0.76), QPointF(cx - r * 0.14, cy - r * 0.18))
            p.drawLine(QPointF(cx + r * 0.14, cy - r * 0.76), QPointF(cx + r * 0.14, cy - r * 0.18))
            flask = QPolygonF(
                [
                    QPointF(cx - r * 0.14, cy - r * 0.18),
                    QPointF(cx - r * 0.72, cy + r * 0.72),
                    QPointF(cx + r * 0.72, cy + r * 0.72),
                    QPointF(cx + r * 0.14, cy - r * 0.18),
                ]
            )
            p.drawPolygon(flask)
            p.drawLine(QPointF(cx - r * 0.46, cy + r * 0.30), QPointF(cx + r * 0.48, cy + r * 0.30))
        elif key in ("rmuc", "rmul", "7v7", "3v3"):
            label = "7" if "7" in key or key == "rmuc" else "3"
            # 比赛模式已经由外层圆形图标承载，不再叠加一个小方框。
            self.draw_text(p, rect, label, self.font("poppins_bold", 24.0 * scale), c, Qt.AlignmentFlag.AlignCenter)
            p.setPen(QPen(with_alpha(c, 0.64), max(1.0, 1.2 * scale), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx - r * 0.42, cy + r * 0.53), QPointF(cx + r * 0.42, cy + r * 0.53))
        else:
            p.drawLine(QPointF(cx + r * 0.55, cy), QPointF(cx - r * 0.35, cy - r * 0.55))
            p.drawLine(QPointF(cx - r * 0.35, cy - r * 0.55), QPointF(cx - r * 0.35, cy + r * 0.55))
            p.drawLine(QPointF(cx - r * 0.35, cy + r * 0.55), QPointF(cx + r * 0.55, cy))
        p.restore()

    def draw_liquid_menu_header(self, p: QPainter, width: int, height: int, scale: float, title: str, subtitle: str, active: float) -> None:
        enter = self.startup_stagger_value(0, 0.70, 0.0)
        y = 42.0 * scale - (1.0 - enter) * 95.0 * scale
        x = 50.0 * scale
        p.save()
        p.setOpacity(active * max(0.0, min(1.0, enter)))
        glow_text(p, "POLAR-RM", x, y, self.font("museo", 28.0 * scale, QFont.Weight.Bold), THEME.text, THEME.cyan, 8.0 * scale)
        self.draw_text(p, QRectF(x, y + 11.0 * scale, 330.0 * scale, 24.0 * scale), ui_text("CUSTOM CLIENT", chinese_enabled(self.registry)), self.font("poppins_medium", 8.5 * scale), THEME.muted)

        notice_w = min(420.0 * scale, width * 0.36)
        notice = QRectF(width / 2.0 - notice_w / 2.0, 28.0 * scale, notice_w, 40.0 * scale)
        self.draw_liquid_glass_panel(p, notice, 15.0 * scale, accent=THEME.cyan, hover=0.0, scale=scale)
        self.draw_glass_text(p, notice.adjusted(18.0 * scale, 4.0 * scale, -18.0 * scale, -19.0 * scale), ui_text(title, chinese_enabled(self.registry)), self.font("poppins_bold", 9.0 * scale), THEME.text, Qt.AlignmentFlag.AlignCenter)
        self.draw_glass_text(p, notice.adjusted(18.0 * scale, 19.0 * scale, -18.0 * scale, -4.0 * scale), ui_text(subtitle, chinese_enabled(self.registry)), self.font("poppins_regular", 7.0 * scale), THEME.muted, Qt.AlignmentFlag.AlignCenter)

        profile = self.current_profile()
        profile_w = min(230.0 * scale, width * 0.22)
        profile_rect = QRectF(width - profile_w - 50.0 * scale, 34.0 * scale, profile_w, 28.0 * scale)
        self.draw_liquid_glass_panel(p, profile_rect, 10.0 * scale, accent=THEME.cyan, hover=0.0, scale=scale)
        self.draw_glass_text(p, profile_rect.adjusted(12.0 * scale, 0, -12.0 * scale, 0), ui_text("PROFILE  {profile}", chinese_enabled(self.registry), profile=profile), self.font("poppins_medium", 7.2 * scale), color("#d8e5ec", 0.92), Qt.AlignmentFlag.AlignCenter)
        p.restore()

    def draw_liquid_main_button(
        self,
        p: QPainter,
        rect: QRectF,
        index: int,
        anim_key: str,
        icon: str,
        title: str,
        subtitle: str,
        detail: str,
        chip_text: str,
        action: str,
        payload: Any,
        tooltip: str,
        accent: QColor,
        scale: float,
        active: float,
    ) -> None:
        enter = self.startup_stagger_value(index, 0.42, 0.10)
        hovered = rect.contains(self.hover_point)
        hover_v = self.anim.value(f"startup.lb.hover.{anim_key}", 1.0 if hovered else 0.0, self.anim_duration(0.16), Easings.ease_out_expo)
        draw_rect = QRectF(rect)
        draw_rect.translate(0.0, 10.0 * (1.0 - enter) * scale - 3.0 * hover_v * scale)
        opacity = active * max(0.0, min(1.0, enter))
        radius = 5.0 * scale

        p.save()
        p.setOpacity(opacity)
        self.draw_liquid_glass_panel(p, draw_rect, radius, accent=accent, hover=hover_v, scale=scale)

        if hover_v > 0.001:
            p.setPen(QPen(with_alpha(accent, 0.18 * hover_v), 1.0))
            p.setBrush(with_alpha(accent, 0.045 * hover_v))
            p.drawRoundedRect(draw_rect, radius, radius)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(with_alpha(accent, 0.86))
        # 与面板 r=radius 的圆角轮廓取交集：4px 窄条自身圆角被钳到 ~2s，
        # 不约束会在面板左上/左下弧角外泄。路径交集是刻意选择——正确性不依赖引擎 clip 行为，且管线内实验（clip_audit_out）证明本窗口各绘制面 clip 均生效，
        # 旧实验的『静默忽略』结论系脱管线方法论偏差，详见 tmp_corner_verify/clip_audit_out。
        bar_shape = QPainterPath()
        bar_shape.addRoundedRect(
            QRectF(draw_rect.left(), draw_rect.top(), 4.0 * scale, draw_rect.height()),
            radius,
            radius,
        )
        host_shape = QPainterPath()
        host_shape.addRoundedRect(draw_rect, radius, radius)
        p.drawPath(bar_shape.intersected(host_shape))

        icon_size = min(90.0 * scale, draw_rect.height() - 28.0 * scale)
        icon_rect = QRectF(draw_rect.left() + 26.0 * scale, draw_rect.center().y() - icon_size / 2.0, icon_size, icon_size)
        icon_fill = self.mix_color(accent, THEME.text, hover_v)
        icon_text = self.mix_color(THEME.text, accent, hover_v)
        rounded_rect(p, icon_rect, icon_size * 0.50, with_alpha(icon_fill, 0.92), color("#ffffff", 0.10 + 0.13 * hover_v), 1.0)
        self.draw_liquid_icon(p, icon_rect.adjusted(14.0 * scale, 14.0 * scale, -14.0 * scale, -14.0 * scale), icon, icon_text, scale)

        text_left = icon_rect.right() + 24.0 * scale
        title_rect = QRectF(text_left, draw_rect.top() + 21.0 * scale, draw_rect.width() - (text_left - draw_rect.left()) - 126.0 * scale, 28.0 * scale)
        self.draw_glass_text(p, title_rect, ui_text(title, chinese_enabled(self.registry)), self.font("poppins_bold", 18.0 * scale), THEME.text)
        self.draw_glass_text(p, QRectF(text_left, draw_rect.top() + 50.0 * scale, draw_rect.width() - (text_left - draw_rect.left()) - 42.0 * scale, 20.0 * scale), ui_text(subtitle, chinese_enabled(self.registry)), self.font("poppins_regular", 9.0 * scale), color("#e1e8ee", 0.88))
        self.draw_glass_text(p, QRectF(text_left, draw_rect.bottom() - 31.0 * scale, draw_rect.width() - (text_left - draw_rect.left()) - 42.0 * scale, 18.0 * scale), ui_text(detail, chinese_enabled(self.registry)), self.font("poppins_regular", 7.3 * scale), color("#aeb7bd", 0.90))

        shortcut = QRectF(draw_rect.right() - 78.0 * scale, draw_rect.top() + 20.0 * scale, 26.0 * scale, 22.0 * scale)
        rounded_rect(p, shortcut, 6.0 * scale, color("#ffffff", 0.08), color("#ffffff", 0.11 + 0.11 * hover_v))
        self.draw_text(p, shortcut, ui_text(str(index + 1) if action != "startup_back" else "B", chinese_enabled(self.registry)), self.font("poppins_bold", 7.3 * scale), icon_text if hover_v > 0.55 else accent, Qt.AlignmentFlag.AlignCenter)

        chip_w = min(98.0 * scale, max(62.0 * scale, text_width(p, self.font("poppins_medium", 7.2 * scale), chip_text) + 22.0 * scale))
        chip = QRectF(draw_rect.right() - chip_w - 26.0 * scale, draw_rect.bottom() - 34.0 * scale, chip_w, 22.0 * scale)
        rounded_rect(p, chip, 7.0 * scale, color("#ffffff", 0.055 + 0.04 * hover_v), with_alpha(accent, 0.25 + 0.25 * hover_v))
        self.draw_text(p, chip, ui_text(chip_text, chinese_enabled(self.registry)), self.font("poppins_medium", 7.2 * scale), icon_text if hover_v > 0.72 else accent, Qt.AlignmentFlag.AlignCenter)

        self.draw_text(p, QRectF(draw_rect.right() - 43.0 * scale, draw_rect.center().y() - 13.0 * scale, 18.0 * scale, 26.0 * scale), ui_text(">", chinese_enabled(self.registry)), self.font("poppins_bold", 15.0 * scale), THEME.text if hovered else THEME.muted, Qt.AlignmentFlag.AlignCenter)
        p.restore()

        self.add_hotspot(draw_rect.adjusted(-3.0 * scale, -3.0 * scale, 3.0 * scale, 3.0 * scale), action, payload, tooltip=ui_text(tooltip, chinese_enabled(self.registry)))

    def draw_liquid_bottom_bar(self, p: QPainter, width: int, height: int, scale: float, active: float) -> None:
        enter = self.startup_stagger_value(0, 0.70, 0.22)
        actions: list[tuple[str, str, Any, QColor, str]] = [
            ("Settings", "startup_open_settings", None, THEME.cyan, "Settings"),
            ("Config", "startup_open_config", None, THEME.cyan, "Open ConfigManager in ClickGUI."),
            ("Import", "config_action", "Import Config", THEME.pink, "Import a config json."),
            ("Wallpaper", "startup_open_wallpaper", None, color("#4677ff"), "Open StartupVisual wallpaper settings."),
            ("ClickGUI", "startup_open_gui", None, accent_color(4, 0.0), "Enter HUD and open ClickGUI."),
            ("Exit", "startup_exit", None, color("#ff6464"), "Close POLAR-RM."),
        ]
        if self.startup_step == "match":
            actions.insert(0, ("Back", "startup_back", None, color("#ffd766"), "Return to scene select."))
        button_h = 42.0 * scale
        gap = 10.0 * scale
        widths = [max(72.0 * scale, text_width(p, self.font("poppins_medium", 8.2 * scale), item[0]) + 32.0 * scale) for item in actions]
        total_w = sum(widths) + gap * (len(actions) - 1) + 28.0 * scale
        rect = QRectF(50.0 * scale, height - 64.0 * scale + (1.0 - enter) * 95.0 * scale, min(total_w, width - 100.0 * scale), button_h + 18.0 * scale)
        p.save()
        p.setOpacity(active * max(0.0, min(1.0, enter)))
        self.draw_liquid_glass_panel(p, rect, 8.0 * scale, accent=THEME.cyan, hover=0.0, scale=scale)
        x = rect.left() + 14.0 * scale
        for index, (label, action, payload, accent, tooltip) in enumerate(actions):
            button_w = min(widths[index], rect.right() - x - 14.0 * scale)
            if button_w <= 18.0 * scale:
                break
            self.draw_liquid_small_action_button(p, QRectF(x, rect.top() + 9.0 * scale, button_w, button_h), label, action, payload, accent, scale, tooltip)
            x += button_w + gap
        p.restore()

    def set_wallpaper_picker_visible(self, visible: bool) -> None:
        """Open/close the wallpaper library and invalidate launcher static FBOs.

        Opening paints the picker into the overlay FBO (overwriting the
        launcher). Closing must drop both the startup-page and battle-ui static
        keys so the launcher is rebuilt; otherwise the picker stays on screen
        and Close appears broken even though ``wallpaper_picker_visible`` is
        already False.
        """
        visible = bool(visible)
        if self.wallpaper_picker_visible == visible:
            if visible:
                self.frame_scheduler.force()
            return
        self.wallpaper_picker_visible = visible
        if visible:
            self.anim.reset("startup.wallpaper.open", 0.0)
        self._startup_static_page_key = None
        self._gpu_static_ui_key = None
        try:
            self.gpu_compositor.clear_static_glass()
            self.gpu_compositor.clear_frame_glass()
        except Exception:
            pass
        self.frame_scheduler.force()

    def draw_wallpaper_picker(self, p: QPainter, width: int, height: int) -> None:
        open_v = self.anim.value("startup.wallpaper.open", 1.0, self.anim_duration(0.24), Easings.ease_out_pow4, initial=0.0)
        full = QRectF(0, 0, width, height)
        p.fillRect(full, color("#02050a", 0.54 * open_v))
        # Backdrop close is registered first; the panel blocks it; the footer
        # Close button is registered last so it always wins hit-testing.
        self.add_hotspot(full, "wallpaper_close", tooltip=ui_text("关闭壁纸库", chinese_enabled(self.registry)))

        scale = max(0.76, min(1.18, self.get_auto_ui_scale(width, height, strength=0.38)))
        panel_w = min(width - 64.0 * scale, 1120.0 * scale)
        panel_h = min(height - 64.0 * scale, 525.0 * scale)
        panel = QRectF(width / 2.0 - panel_w / 2.0, height / 2.0 - panel_h / 2.0 + (1.0 - open_v) * 28.0 * scale, panel_w, panel_h)
        p.save()
        p.setOpacity(open_v)
        self.draw_liquid_glass_panel(p, panel, 18.0 * scale, accent=color("#54c7ff"), hover=0.0, scale=scale, surface="ModalGlass")
        self.add_hotspot(panel, "wallpaper_block")

        self.draw_text(
            p,
            QRectF(panel.left() + 28.0 * scale, panel.top() + 19.0 * scale, panel.width() - 56.0 * scale, 28.0 * scale),
            ui_text("WALLPAPER LIBRARY", chinese_enabled(self.registry)),
            self.font("poppins_bold", 15.0 * scale),
            THEME.text,
        )
        self.draw_text(
            p,
            QRectF(panel.left() + 28.0 * scale, panel.top() + 47.0 * scale, panel.width() - 56.0 * scale, 18.0 * scale),
            ui_text("选择内置壁纸，或导入自己的 MP4 / GIF / 图片", chinese_enabled(self.registry)),
            self.font("poppins_regular", 8.2 * scale),
            color("#c8d5de", 0.78),
        )

        module = self.registry.get("StartupVisual")
        current = self.setting_mode(module, "Wallpaper Profile", "BlackHole")
        profiles = self.startup_wallpaper.profile_names()
        per_page = 4
        page_count = max(1, math.ceil(len(profiles) / per_page))
        self.wallpaper_picker_page = max(0, min(page_count - 1, self.wallpaper_picker_page))
        visible = profiles[self.wallpaper_picker_page * per_page : (self.wallpaper_picker_page + 1) * per_page]

        gap = 14.0 * scale
        cards_left = panel.left() + 26.0 * scale
        cards_top = panel.top() + 84.0 * scale
        cards_width = panel.width() - 52.0 * scale
        card_w = (cards_width - gap * (per_page - 1)) / per_page
        card_h = min(270.0 * scale, panel.height() - 176.0 * scale)
        for index, profile in enumerate(visible):
            rect = QRectF(cards_left + index * (card_w + gap), cards_top, card_w, card_h)
            self.draw_wallpaper_card(p, rect, profile, profile == current, scale)

        footer_y = panel.bottom() - 67.0 * scale
        button_h = 38.0 * scale
        x = panel.left() + 26.0 * scale
        actions = [
            ("Import", "wallpaper_import", None, color("#54c7ff"), "导入视频、GIF 或图片壁纸。"),
            ("Reset", "wallpaper_reset", None, THEME.cyan, "恢复默认黑洞壁纸。"),
        ]
        if self.startup_wallpaper.can_remove(current):
            actions.append(("Remove", "wallpaper_remove", current, color("#ff6b70"), "删除当前用户壁纸。"))
        for label, action, payload, accent, tooltip in actions:
            button_w = (88.0 if label != "Remove" else 96.0) * scale
            self.draw_liquid_small_action_button(p, QRectF(x, footer_y, button_w, button_h), label, action, payload, accent, scale, tooltip)
            x += button_w + 10.0 * scale

        nav_right = panel.right() - 26.0 * scale
        close_w = 82.0 * scale
        self.draw_liquid_small_action_button(
            p,
            QRectF(nav_right - close_w, footer_y, close_w, button_h),
            "Close",
            "wallpaper_close",
            None,
            color("#ffffff", 0.72),
            scale,
            "关闭壁纸库。",
        )
        nav_right -= close_w + 12.0 * scale
        if page_count > 1:
            nav_w = 48.0 * scale
            self.draw_liquid_small_action_button(p, QRectF(nav_right - nav_w * 2.0 - 8.0 * scale, footer_y, nav_w, button_h), "<", "wallpaper_page", -1, THEME.cyan, scale, "上一页")
            self.draw_liquid_small_action_button(p, QRectF(nav_right - nav_w, footer_y, nav_w, button_h), ">", "wallpaper_page", 1, THEME.cyan, scale, "下一页")
            self.draw_text(
                p,
                QRectF(nav_right - nav_w * 2.0 - 8.0 * scale, footer_y - 21.0 * scale, nav_w * 2.0 + 8.0 * scale, 16.0 * scale),
                ui_text(f"{self.wallpaper_picker_page + 1} / {page_count}", chinese_enabled(self.registry)),
                self.font("poppins_medium", 7.5 * scale),
                THEME.muted,
                Qt.AlignmentFlag.AlignCenter,
            )
        p.restore()

    def draw_wallpaper_card(self, p: QPainter, rect: QRectF, profile: str, selected: bool, scale: float) -> None:
        hovered = rect.contains(self.hover_point)
        hover_v = self.anim.value(f"startup.wallpaper.hover.{profile}", 1.0 if hovered else 0.0, self.anim_duration(0.18), Easings.ease_out_expo)
        draw_rect = rect.translated(0, -3.0 * scale * hover_v)
        accent = color("#54c7ff") if selected else color("#9eb3c4")
        self.draw_liquid_glass_panel(p, draw_rect, 12.0 * scale, accent=accent, hover=max(hover_v, 0.42 if selected else 0.0), scale=scale, sample_background=False, surface="SubGlass")

        preview = QRectF(draw_rect.left() + 8.0 * scale, draw_rect.top() + 8.0 * scale, draw_rect.width() - 16.0 * scale, max(70.0 * scale, draw_rect.height() - 80.0 * scale))
        clip = QPainterPath()
        clip.addRoundedRect(preview, 8.0 * scale, 8.0 * scale)
        p.save()
        p.setClipPath(clip)
        image = self.startup_wallpaper.thumbnail(profile)
        if not image.isNull():
            self.draw_image_cover(p, preview, image)
        else:
            fallback = QLinearGradient(preview.topLeft(), preview.bottomRight())
            fallback.setColorAt(0.0, color("#16354a", 0.92))
            fallback.setColorAt(1.0, color("#071018", 0.96))
            p.fillRect(preview, fallback)
            self.draw_text(p, preview, ui_text("LOADING" if profile != "ParticleGrid" else "GRID", chinese_enabled(self.registry)), self.font("poppins_bold", 9.0 * scale), color("#bdefff", 0.66), Qt.AlignmentFlag.AlignCenter)
        p.fillRect(preview, color("#02050a", 0.10 if hovered else 0.20))
        p.restore()

        if selected:
            p.setBrush(color("#54c7ff", 0.94))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(draw_rect.right() - 18.0 * scale, draw_rect.top() + 18.0 * scale), 5.0 * scale, 5.0 * scale)
        label = self.startup_wallpaper.profile_label(profile)
        kind = self.startup_wallpaper.profile_kind(profile)
        self.draw_text(p, QRectF(draw_rect.left() + 11.0 * scale, preview.bottom() + 8.0 * scale, draw_rect.width() - 22.0 * scale, 20.0 * scale), label, self.font("poppins_bold", 9.0 * scale), THEME.text)
        self.draw_text(p, QRectF(draw_rect.left() + 11.0 * scale, preview.bottom() + 29.0 * scale, draw_rect.width() - 22.0 * scale, 16.0 * scale), ui_text(kind, chinese_enabled(self.registry)), self.font("poppins_medium", 6.9 * scale), accent if selected else THEME.muted)
        self.add_hotspot(draw_rect, "wallpaper_select", profile, tooltip=ui_text(f"切换到 {label}", chinese_enabled(self.registry)))

    @staticmethod
    def draw_image_cover(p: QPainter, rect: QRectF, image: QImage) -> None:
        image_aspect = image.width() / max(1.0, image.height())
        rect_aspect = rect.width() / max(1.0, rect.height())
        if image_aspect > rect_aspect:
            source_h = image.height()
            source_w = source_h * rect_aspect
            source = QRectF((image.width() - source_w) / 2.0, 0, source_w, source_h)
        else:
            source_w = image.width()
            source_h = source_w / max(0.01, rect_aspect)
            source = QRectF(0, (image.height() - source_h) / 2.0, source_w, source_h)
        p.drawImage(rect, image, source)

    def draw_liquid_small_action_button(
        self,
        p: QPainter,
        rect: QRectF,
        label: str,
        action: str,
        payload: Any,
        accent: QColor,
        scale: float,
        tooltip: str,
    ) -> None:
        hovered = rect.contains(self.hover_point)
        hover_v = self.anim.value(f"startup.lb.small.hover.{label}", 1.0 if hovered else 0.0, self.anim_duration(0.16), Easings.ease_out_expo)
        self.draw_liquid_glass_panel(p, rect, 6.0 * scale, accent=accent, hover=hover_v, scale=scale, sample_background=False, surface="SubGlass")
        fill = QRectF(rect.left(), rect.top(), rect.width() * hover_v, rect.height())
        if fill.width() > 1.0:
            grad = QLinearGradient(rect.topLeft(), rect.topRight())
            grad.setColorAt(0.0, with_alpha(accent, 0.44))
            grad.setColorAt(1.0, with_alpha(THEME.cyan, 0.20))
            p.save()
            p.setClipRect(fill)
            p.setBrush(QBrush(grad))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, 6.0 * scale, 6.0 * scale)
            p.restore()
        self.draw_glass_text(p, rect.adjusted(10.0 * scale, 0, -10.0 * scale, 0), ui_text(label, chinese_enabled(self.registry)), self.font("poppins_medium", 8.2 * scale), THEME.text if hover_v > 0.35 else color("#d2d9df", 0.90), Qt.AlignmentFlag.AlignCenter)
        self.add_hotspot(rect, action, payload, tooltip=ui_text(tooltip, chinese_enabled(self.registry)))

    def draw_liquid_status_panel(self, p: QPainter, menu_x: float, menu_y: float, button_w: float, height: int, scale: float, page: str, active: float) -> None:
        right_x = menu_x + button_w + 48.0 * scale
        right_w = self.width() - right_x - 50.0 * scale
        if right_w < 245.0 * scale:
            return
        enter = self.startup_stagger_value(2, 0.50, 0.08)
        panel = QRectF(right_x + (1.0 - enter) * 90.0 * scale, menu_y + 4.0 * scale, min(390.0 * scale, right_w), min(300.0 * scale, height - menu_y - 108.0 * scale))
        p.save()
        p.setOpacity(active * max(0.0, min(1.0, enter)))
        self.draw_liquid_glass_panel(p, panel, 7.0 * scale, accent=THEME.cyan, hover=0.0, scale=scale)
        top_line = QRectF(panel.left() + 14.0 * scale, panel.top() + 12.0 * scale, panel.width() - 28.0 * scale, 2.0 * scale)
        grad = QLinearGradient(top_line.topLeft(), top_line.topRight())
        grad.setColorAt(0.0, color("#4677ff", 0.90))
        grad.setColorAt(0.55, THEME.cyan)
        grad.setColorAt(1.0, color("#ffffff", 0.28))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(top_line, 1.0 * scale, 1.0 * scale)
        self.draw_glass_text(p, QRectF(panel.left() + 18.0 * scale, panel.top() + 26.0 * scale, panel.width() - 36.0 * scale, 23.0 * scale), ui_text("SESSION", chinese_enabled(self.registry)), self.font("poppins_bold", 11.0 * scale), THEME.text)

        scenario = self.startup_scenario_meta(self.scenario_manager.current.key)
        match = self.startup_match_meta(self.match_mode_manager.current.key)
        rows = [
            ("ENV", scenario["title"]),
            ("MATCH", match["title"]),
            ("CONFIG", self.current_profile()),
            ("MQTT", "mock" if not self.mqtt.connected else "live"),
            ("SERIAL", "mock" if not self.serial.connected else "live"),
        ]
        row_y = panel.top() + 62.0 * scale
        for index, (label, value) in enumerate(rows):
            c = accent_color(index, 0.08)
            row = QRectF(panel.left() + 18.0 * scale, row_y + index * 34.0 * scale, panel.width() - 36.0 * scale, 24.0 * scale)
            self.draw_liquid_glass_panel(p, row, 6.0 * scale, accent=c, hover=0.0, scale=scale, sample_background=False, surface="SubGlass")
            self.draw_glass_text(p, QRectF(row.left() + 10.0 * scale, row.top(), 58.0 * scale, row.height()), ui_text(label, chinese_enabled(self.registry)), self.font("poppins_bold", 7.0 * scale), c, Qt.AlignmentFlag.AlignVCenter)
            self.draw_glass_text(p, row.adjusted(72.0 * scale, 0, -10.0 * scale, 0), (value if label == "CONFIG" else ui_text(value, chinese_enabled(self.registry))), self.font("poppins_medium", 8.1 * scale), color("#dce7ed", 0.92), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        tip_y = panel.bottom() - 56.0 * scale
        if tip_y > row_y + len(rows) * 34.0 * scale:
            tip = QRectF(panel.left() + 18.0 * scale, tip_y, panel.width() - 36.0 * scale, 37.0 * scale)
            self.draw_liquid_glass_panel(p, tip, 10.0 * scale, accent=THEME.cyan, hover=0.0, scale=scale, sample_background=False, surface="SubGlass")
            msg = "Press 1/2/3 to select. Enter continues." if page == "scenario" else "Press 1/2 to load. Backspace returns."
            self.draw_glass_text(p, tip.adjusted(12.0 * scale, 0, -12.0 * scale, 0), ui_text(msg, chinese_enabled(self.registry)), self.font("poppins_regular", 7.2 * scale), THEME.muted, Qt.AlignmentFlag.AlignCenter)
        p.restore()

    def draw_startup_icon_card(
        self,
        p: QPainter,
        rect: QRectF,
        index: int,
        anim_key: str,
        icon: str,
        title: str,
        subtitle: str,
        detail: str,
        chip_text: str,
        action: str,
        payload: Any,
        tooltip: str,
        accent: QColor,
        scale: float,
        active: float,
    ) -> None:
        enter = self.anim.value(f"startup.card.enter.{anim_key}", 1.0, self.anim_duration(0.42 + index * 0.045), Easings.ease_out_pow4, initial=0.0)
        hovered = rect.contains(self.hover_point)
        hover_v = self.anim.value(f"startup.card.hover.{anim_key}", 1.0 if hovered else 0.0, self.anim_duration(0.18), Easings.ease_out_quad)
        lift = (1.0 - enter) * (24.0 + index * 7.0) * scale - hover_v * 7.0 * scale
        expand = hover_v * 4.0 * scale
        draw_rect = rect.adjusted(-expand, -expand, expand, expand)
        draw_rect.translate(0, lift)
        opacity = max(0.0, min(1.0, active * (0.12 + enter * 0.88)))

        p.save()
        p.setOpacity(opacity)
        self.draw_startup_card_shadow(p, draw_rect, accent, hover_v, scale)

        bg = QLinearGradient(draw_rect.topLeft(), draw_rect.bottomRight())
        bg.setColorAt(0.0, color("#11141a", 0.92 + 0.04 * hover_v))
        bg.setColorAt(0.55, color("#07080c", 0.90 + 0.05 * hover_v))
        bg.setColorAt(1.0, color("#151018", 0.86 + 0.06 * hover_v))
        p.setBrush(QBrush(bg))
        p.setPen(QPen(color("#ffffff", 0.13 + 0.15 * hover_v), 1.0))
        p.drawRoundedRect(draw_rect, 16 * scale, 16 * scale)

        top_line = QRectF(draw_rect.left() + 14 * scale, draw_rect.top() + 10 * scale, draw_rect.width() - 28 * scale, 3 * scale)
        line_grad = QLinearGradient(top_line.topLeft(), top_line.topRight())
        line_grad.setColorAt(0.0, THEME.pink)
        line_grad.setColorAt(0.52, accent)
        line_grad.setColorAt(1.0, THEME.cyan)
        p.setBrush(QBrush(line_grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(top_line, 2 * scale, 2 * scale)

        icon_size = min(58.0 * scale, draw_rect.height() * 0.38)
        icon_rect = QRectF(draw_rect.left() + 18 * scale, draw_rect.top() + 31 * scale, icon_size, icon_size)
        pulse = 0.5 + 0.5 * math.sin(time.perf_counter() * 3.0 + index)
        p.setBrush(with_alpha(accent, 0.10 + 0.12 * hover_v + 0.04 * pulse))
        p.setPen(QPen(with_alpha(accent, 0.52 + 0.24 * hover_v), 1.2 * scale))
        p.drawEllipse(icon_rect.adjusted(-4 * scale, -4 * scale, 4 * scale, 4 * scale))
        rounded_rect(p, icon_rect, icon_size * 0.50, color("#050609", 0.72), with_alpha(accent, 0.78 + 0.16 * hover_v), 1.2 * scale)
        self.draw_text(p, icon_rect, ui_text(icon, chinese_enabled(self.registry)), self.font("poppins_bold", min(17.5 * scale, icon_size * 0.34), QFont.Weight.Bold), THEME.text, Qt.AlignmentFlag.AlignCenter)

        shortcut = QRectF(draw_rect.right() - 38 * scale, draw_rect.top() + 26 * scale, 22 * scale, 20 * scale)
        rounded_rect(p, shortcut, 7 * scale, color("#ffffff", 0.06), color("#ffffff", 0.11 + 0.12 * hover_v))
        self.draw_text(p, shortcut, ui_text(str(index + 1), chinese_enabled(self.registry)), self.font("poppins_bold", 7.4 * scale), accent, Qt.AlignmentFlag.AlignCenter)

        text_left = icon_rect.right() + 13 * scale
        text_w = draw_rect.right() - text_left - 46 * scale
        self.draw_text(p, QRectF(text_left, draw_rect.top() + 31 * scale, text_w, 26 * scale), ui_text(title, chinese_enabled(self.registry)), self.font("poppins_bold", 14.0 * scale), THEME.text)
        self.draw_text(p, QRectF(text_left, draw_rect.top() + 57 * scale, draw_rect.right() - text_left - 18 * scale, 19 * scale), ui_text(subtitle, chinese_enabled(self.registry)), self.font("poppins_regular", 8.0 * scale), color("#d0d5db", 0.86))

        detail_rect = QRectF(draw_rect.left() + 18 * scale, draw_rect.bottom() - 49 * scale, draw_rect.width() - 36 * scale, 17 * scale)
        self.draw_text(p, detail_rect, ui_text(detail, chinese_enabled(self.registry)), self.font("poppins_regular", 7.6 * scale), color("#aeb7bd", 0.84))

        chip = QRectF(draw_rect.left() + 18 * scale, draw_rect.bottom() - 28 * scale, min(88.0 * scale, draw_rect.width() * 0.36), 18 * scale)
        rounded_rect(p, chip, 7 * scale, color("#ffffff", 0.055), with_alpha(accent, 0.25 + 0.25 * hover_v))
        self.draw_text(p, chip, ui_text(chip_text, chinese_enabled(self.registry)), self.font("poppins_medium", 6.9 * scale), accent, Qt.AlignmentFlag.AlignCenter)

        arrow = QRectF(draw_rect.right() - 38 * scale, draw_rect.bottom() - 30 * scale, 22 * scale, 20 * scale)
        self.draw_text(p, arrow, ui_text(">", chinese_enabled(self.registry)), self.font("poppins_bold", 12.0 * scale), THEME.text if hovered else THEME.muted, Qt.AlignmentFlag.AlignCenter)
        p.restore()

        self.add_hotspot(rect.adjusted(-4 * scale, -5 * scale, 4 * scale, 6 * scale), action, payload, tooltip="")

    def draw_startup_card_shadow(self, p: QPainter, rect: QRectF, accent: QColor, hover_v: float, scale: float) -> None:
        shadow_rect = rect.adjusted(-6 * scale, -6 * scale, 6 * scale, 8 * scale)
        rounded_rect(p, shadow_rect, 18 * scale, color("#000000", 0.18 + 0.08 * hover_v))
        if hover_v > 0.01:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(with_alpha(accent, 0.22 * hover_v), 1.4 * scale))
            p.drawRoundedRect(rect.adjusted(-3 * scale, -3 * scale, 3 * scale, 3 * scale), 18 * scale, 18 * scale)
            p.setPen(QPen(color("#ffffff", 0.11 * hover_v), 1.0))
            p.drawRoundedRect(rect.adjusted(-7 * scale, -7 * scale, 7 * scale, 7 * scale), 22 * scale, 22 * scale)

    def startup_scenario_meta(self, key: str) -> dict[str, str]:
        return {
            "OfficialEvent": {
                "icon": "official",
                "title": "DJI Official",
                "subtitle": "Official limits and referee links",
                "tooltip": "Load official safety limits, referee protocol defaults, and event HUD presets.",
            },
            "CampusSimulation": {
                "icon": "sim",
                "title": "Campus Sim",
                "subtitle": "Shark bridge and local match mock",
                "tooltip": "Load campus simulation defaults for SharkDataServer, mock sending, and local video.",
            },
            "LabDebug": {
                "icon": "lab",
                "title": "Lab Debug",
                "subtitle": "Camera preview and protocol stubs",
                "tooltip": "Load lab debug defaults for webcam preview, protocol testing, and mock outputs.",
            },
        }.get(key, {"icon": "RM", "title": key, "subtitle": "POLAR profile", "tooltip": key})

    def startup_match_meta(self, key: str) -> dict[str, str]:
        return {
            "RMUC7v7": {
                "icon": "7v7",
                "title": "RMUC 7v7",
                "subtitle": "Super combat map and full HUD",
                "tooltip": "Load RMUC 7v7 official map, tactical HUD, base, outpost, gold, ammo, and buff modules.",
            },
            "RMUL3v3": {
                "icon": "3v3",
                "title": "RMUL 3v3",
                "subtitle": "Alliance map and control zone HUD",
                "tooltip": "Load RMUL 3v3 official map, control zone, victory points, robot status, and compact HUD.",
            },
        }.get(key, {"icon": "RM", "title": key, "subtitle": "Match profile", "tooltip": key})

    def reset_startup_page_animation(self, page: str) -> None:
        self.anim.reset_prefix("startup.card.enter.", 0.0)
        self.anim.reset_prefix("startup.lb.hover.", 0.0)
        self.anim.reset_prefix("startup.lb.small.hover.", 0.0)
        self.startup_transition_started = time.perf_counter()
        key = "startup.robot.open" if page == "robot" else ("startup.match.open" if page == "match" else "startup.open")
        self.anim.reset(key, 0.0)
        # 页面切换不应继续混合上一页遗留的视频纹理。保留当前帧，只丢弃
        # 插值历史，避免部分驱动在纹理池轮换时短暂显示上下颠倒的旧帧。
        self.startup_gpu.discard_interpolation_history()
    def handle_startup_key(self, event: QKeyEvent) -> bool:
        if self.startup_step == "robot":
            if event.key() == Qt.Key.Key_Backspace:
                # 回退目标跟着 `Second Step`：关掉二级页时不能退回一个被跳过的页
                back_page = self.startup_page_before_robot()
                self.reset_startup_page_animation(back_page)
                self.begin_startup_page_transition(back_page)
                return True
            profiles = profiles_for_match(self.match_mode_manager.current.key)
            mapping = {getattr(Qt.Key, f"Key_{index + 1}"): profile.key for index, profile in enumerate(profiles[:9])}
            mapping[Qt.Key.Key_Return] = self.current_robot_profile_key()
            mapping[Qt.Key.Key_Enter] = self.current_robot_profile_key()
            key = mapping.get(event.key())
            if key is None:
                return False
            self.apply_robot_profile(str(key), close_home=True)
            self.auto_save_config()
            return True
        if self.startup_step == "match":
            if event.key() == Qt.Key.Key_Backspace:
                self.reset_startup_page_animation("scenario")
                self.begin_startup_page_transition("scenario")
                return True
            mapping = {
                Qt.Key.Key_1: "RMUC7v7",
                Qt.Key.Key_2: "RMUL3v3",
                Qt.Key.Key_Return: self.match_mode_manager.current.key,
                Qt.Key.Key_Enter: self.match_mode_manager.current.key,
            }
            key = mapping.get(event.key())
            if key is None:
                return False
            self.apply_match_mode(str(key), close_home=False)
            self.reset_startup_page_animation("robot")
            self.begin_startup_page_transition("robot")
            self.auto_save_config()
            return True
        mapping = {
            Qt.Key.Key_1: "OfficialEvent",
            Qt.Key.Key_2: "CampusSimulation",
            Qt.Key.Key_3: "LabDebug",
            Qt.Key.Key_Return: self.scenario_manager.current.key,
            Qt.Key.Key_Enter: self.scenario_manager.current.key,
        }
        key = mapping.get(event.key())
        if key is None:
            return False
        self.apply_scenario(str(key), close_home=False)
        next_page = self.startup_page_after_scenario()
        self.reset_startup_page_animation(next_page)
        self.begin_startup_page_transition(next_page)
        self.auto_save_config()
        return True

    def apply_scenario(self, key: str, close_home: bool) -> None:
        config = self.scenario_manager.set_scenario(key)
        self.scenario_manager.apply_to_registry(self.registry)
        # 后端按三种模式启动/停止 MQTT、UDP、串口（仅连接层，不动 UI）
        self.backend.apply_scenario(key, registry=self.registry)
        self.control.mock_output = bool(self.backend.mqtt.mock_output)
        if close_home:
            self.home_visible = False
            self.gui_visible = False
        self.toast(f"Scenario: {config.title}")


# ---------------------------------------------------------------------------
# API 冻结面 qualname 固定（见模块 docstring「冻结面纪律」；先例：
# ui/polar/text_metrics.py 底部同一手法）。
# ---------------------------------------------------------------------------

_QUALNAME_PINNED_MEMBERS = (
    StartupSceneMixin.paint_gpu_startup,
    StartupSceneMixin.begin_startup_page_transition,
    StartupSceneMixin.finish_startup_page_transition,
    StartupSceneMixin.build_gpu_startup_transition,
    StartupSceneMixin.draw_startup_page_content,
    StartupSceneMixin.draw_startup_page_transition,
    StartupSceneMixin.startup_needs_high_rate,
    StartupSceneMixin.draw_startup_background,
    StartupSceneMixin.draw_particle_grid_background,
    StartupSceneMixin.draw_startup_screen,
    StartupSceneMixin.draw_robot_profile_screen,
    StartupSceneMixin.draw_match_mode_screen,
    StartupSceneMixin.draw_startup_scene_launcher,
    StartupSceneMixin.draw_startup_match_launcher,
    StartupSceneMixin.draw_startup_vignette,
    StartupSceneMixin.draw_liquid_startup_scene_launcher,
    StartupSceneMixin.draw_liquid_startup_match_launcher,
    StartupSceneMixin.liquid_menu_layout,
    StartupSceneMixin.startup_stagger_value,
    StartupSceneMixin.startup_menu_accent,
    StartupSceneMixin.startup_glass_settings,
    StartupSceneMixin.draw_liquid_glass_panel,
    StartupSceneMixin.draw_legacy_glass_panel,
    StartupSceneMixin.draw_liquid_icon,
    StartupSceneMixin.draw_liquid_menu_header,
    StartupSceneMixin.draw_liquid_main_button,
    StartupSceneMixin.draw_liquid_bottom_bar,
    StartupSceneMixin.set_wallpaper_picker_visible,
    StartupSceneMixin.draw_wallpaper_picker,
    StartupSceneMixin.draw_wallpaper_card,
    StartupSceneMixin.draw_image_cover,
    StartupSceneMixin.draw_liquid_small_action_button,
    StartupSceneMixin.draw_liquid_status_panel,
    StartupSceneMixin.draw_startup_icon_card,
    StartupSceneMixin.draw_startup_card_shadow,
    StartupSceneMixin.startup_scenario_meta,
    StartupSceneMixin.startup_match_meta,
    StartupSceneMixin.reset_startup_page_animation,
    StartupSceneMixin.handle_startup_key,
    StartupSceneMixin.apply_scenario,
)

for _fn in _QUALNAME_PINNED_MEMBERS:
    _fn.__qualname__ = f"PolarRMWindow.{_fn.__name__}"
del _fn, _QUALNAME_PINNED_MEMBERS
