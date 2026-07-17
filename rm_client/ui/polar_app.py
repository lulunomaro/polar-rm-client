from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import sys
import time
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QBrush,
    QCloseEvent,
    QFont,
    QFontMetricsF,
    QImage,
    QKeyEvent,
    QKeySequence,
    QLinearGradient,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QSurfaceFormat,
    QWheelEvent,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from ..calibration import CameraCalibrationManager
from ..combat_events import CombatEvent, OfficialMatchFeed, VisionKillDetector
from ..config_store import ClientConfigStore, apply_registry_config, export_client_config
from ..control import KeyboardControlBridge
from ..gestalt_input import GestaltInputInjector
from ..gestalt_telemetry import GestaltAttrReceiver
from ..model import (
    CATEGORIES,
    GUI_MODES,
    HUD_MODES,
    MODULE_LIST_STYLES,
    GuiModeManager,
    Module,
    RobotState,
    Setting,
    build_default_registry,
    normalize_angle_deg,
)
from ..match_modes import MATCH_MODES, MATCH_MODE_KEYS, FieldLayout, MatchModeConfig, MatchModeManager
from ..kill_fx import KillFxRenderer
from ..scenarios import SCENARIOS, ScenarioManager
from ..startup_wallpaper import StartupWallpaperManager
from ..tongji_bridge import ArmorAutoAimStatePublisher, GimbalCommandBridge, TongjiVisionReceiver
from ..tongji_yolo import LocalAlignedVision, resolve_tongji_yolo_xml
from ..transports import OfficialMqttBridge, SerialBridgeStub, UDPVideoReceiver, VideoPipeline
from ..ui_audio import UiAudioManager
from ..vision import (
    CameraCaptureWorker,
    CapturedFrame,
    CompositeVisionPipeline,
    MockVisionPipeline,
    TongjiVisionBridge,
    VisionResult,
    VisionSnapshot,
    VisionTarget,
    expand_multi_plate_esp,
    snapshot_from_local_result,
    snapshot_from_tongji_packet,
)
from .animation import AnimationBank, Easings, MotionTokens
from .fonts import FontBook
from .glass_gl import GlassGpuCompositor
from .liquid_glass import LiquidGlassRenderer
from .startup_gl import StartupGpuCompositor
from .paint import accent_color, cap_center_y, draw_bar, glow_rect, glow_text, rounded_rect, text_width, with_alpha
from .theme import THEME, color


@dataclass
class Hotspot:
    rect: QRectF
    action: str
    payload: Any = None
    button: int | None = None
    tooltip: str = ""


class StartupLayerCache:
    def __init__(self) -> None:
        self._vignettes: dict[tuple[int, int], QImage] = {}

    def vignette(self, width: int, height: int) -> QImage:
        key = (width, height)
        image = self._vignettes.get(key)
        if image is not None:
            return image
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.fillRect(QRectF(0, 0, width, height), color("#56fff0", 0.018))
        center_glow = QLinearGradient(width * 0.5, height * 0.18, width * 0.5, height * 0.78)
        center_glow.setColorAt(0.0, color("#56fff0", 0.10))
        center_glow.setColorAt(0.45, color("#ff65df", 0.045))
        center_glow.setColorAt(1.0, color("#000000", 0.0))
        painter.fillRect(QRectF(0, 0, width, height), center_glow)
        painter.fillRect(QRectF(0, 0, width, height), color("#000000", 0.08))
        painter.end()
        if len(self._vignettes) > 4:
            self._vignettes.clear()
        self._vignettes[key] = image
        return image


class FrameScheduler:
    def __init__(self) -> None:
        self.last_request = 0.0

    def should_request(self, now: float, high_rate: bool, target_fps: float | None = None) -> bool:
        if target_fps is not None and target_fps > 0:
            fps = max(12.0, min(60.0, target_fps))
        else:
            fps = 60.0 if high_rate else 15.0
        interval = 1.0 / fps
        if now - self.last_request < interval * 0.86:
            return False
        self.last_request = now
        return True

    def force(self) -> None:
        self.last_request = 0.0


class VisionViewport(QOpenGLWidget):
    """战斗视频与预测装甲的 GPU 合成层；上下文不可用时由主窗口回退到 CPU。"""

    def __init__(self, owner: "PolarRMWindow") -> None:
        super().__init__(owner)
        self.owner = owner
        self.gpu_ready = False
        self.frame: QImage | None = None
        self.result: VisionResult | None = None
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_payload(self, frame: QImage | None, result: VisionResult | None) -> None:
        self.frame = frame
        self.result = result
        self.update()

    def initializeGL(self) -> None:
        self.gpu_ready = self.context() is not None

    def paintGL(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), color("#0b121b"))
        if self.frame is not None and not self.frame.isNull():
            painter.drawImage(QRectF(0, 0, self.width(), self.height()), self.frame)
        else:
            self.owner.draw_synthetic_video_scene(painter, self.width(), self.height(), lite=True)
        armor = self.owner.registry.get("ArmorAutoAim")
        esp = self.owner.registry.get("Armor3DESP")
        if armor and armor.enabled and esp and esp.enabled and self.result is not None:
            self.owner.draw_armor_3d_esp(painter, self.width(), self.height(), self.result, esp)
        painter.end()


class PolarHudOverlay(QWidget):
    """覆盖 GPU 战斗底图的交互与 HUD 图层。"""

    def __init__(self, owner: "PolarRMWindow") -> None:
        super().__init__(owner)
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        # 焦点交给主窗口，并转发键盘，避免覆盖层吃掉 RightShift/R/V
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFocusProxy(owner)

    def paintEvent(self, event: QPaintEvent) -> None:
        self.owner.paint_overlay(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.owner.setFocus(Qt.FocusReason.MouseFocusReason)
        self.owner.mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.owner.mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.owner.mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.owner.wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        self.owner.keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        self.owner.keyReleaseEvent(event)


class PolarRMWindow(QOpenGLWidget):
    """主窗口直接使用 Qt OpenGL 表面，所有 QPainter UI 都由 OpenGL 合成呈现。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("POLAR-RM Custom Client")
        self.resize(1280, 720)
        self.setMinimumSize(1000, 620)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_KeyCompression, False)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)
        # 应用级键盘钩子：即使子控件/焦点异常，client 快捷键仍进 POLAR
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.workspace = Path(__file__).resolve().parents[3]
        self.registry = build_default_registry()
        self.state = RobotState()
        self.scenario_manager = ScenarioManager("LabDebug")
        self.match_mode_manager = MatchModeManager("RMUC7v7")
        self.mode_manager = GuiModeManager("PolarPanel")
        self.vision = MockVisionPipeline()
        self.camera_worker = CameraCaptureWorker()
        self.camera_started = False
        self._camera_config: tuple[int, int, int] | None = None
        self.mqtt = OfficialMqttBridge()
        self.official_feed = OfficialMatchFeed()
        self.vision_kill_detector = VisionKillDetector()
        self._official_state_updates: dict[str, Any] = {}
        self.kill_fx = KillFxRenderer(self.workspace / "rm_custom_client" / "assets" / "kill_fx" / "gd656")
        self.ui_audio = UiAudioManager(
            self.workspace / "rm_custom_client" / "assets" / "ui_sounds"
        )
        self.registry.add_toggle_listener(self.on_module_toggled)
        self._vision_scene_luminance = 0.0
        self.udp = UDPVideoReceiver()
        self.video_pipeline = VideoPipeline()
        self.serial = SerialBridgeStub()
        self.control = KeyboardControlBridge(self.serial, mock_output=True)
        self.gestalt_injector = GestaltInputInjector()
        self.control.gestalt_injector = self.gestalt_injector
        self.gestalt_attrs = GestaltAttrReceiver()
        self._last_mouse_inject_pos: QPointF | None = None
        self._mouse_look_warp_skip = False
        self._gestalt_control_toast_at = 0.0
        self._inject_paused_for_gui = False
        self._mouse_look_active = False
        self._global_hotkeys_registered = False
        self.config_store = ClientConfigStore(self.workspace / "rm_custom_client" / "configs")
        self.calibration = CameraCalibrationManager(self.workspace)
        self.calibration.load("polar_webcam")
        self.tongji_receiver = TongjiVisionReceiver()
        self.gimbal_command_bridge = GimbalCommandBridge()
        self.aa_state_publisher = ArmorAutoAimStatePublisher()
        self.composite_vision = CompositeVisionPipeline()
        self.local_vision = LocalAlignedVision(resolve_tongji_yolo_xml(self.workspace))
        self.tongji_bridge = TongjiVisionBridge()
        self._vision_source = "Tongji"
        self._tongji_enabled = False
        # Lab 简易跟枪：归一化误差 ±1 → 鼠标像素；完整同济不会走这条
        self._lab_aim_gain_x = 28.0
        self._lab_aim_gain_y = 22.0
        self._lab_aim_smooth_x = 0.0
        self._lab_aim_smooth_y = 0.0
        self._lab_aim_ema = 0.45
        self._vision_snapshot: VisionSnapshot | None = None
        self._last_capture_sequence = 0
        self._last_vision_result: VisionResult | None = None
        self.startup_wallpaper = StartupWallpaperManager(self.workspace)
        self.scenario_manager.apply_to_registry(self.registry)
        self.scenario_manager.apply_to_runtime(self.mqtt, self.udp, self.control)
        self.match_mode_manager.apply_to_registry(self.registry)
        self.fonts = FontBook()
        self.fonts.load_from_project_assets(self.workspace)
        self.anim = AnimationBank()
        self.startup_cache = StartupLayerCache()
        self.frame_scheduler = FrameScheduler()
        self.liquid_glass = LiquidGlassRenderer()
        self.startup_gpu = StartupGpuCompositor()
        self.glass_gpu = GlassGpuCompositor()
        self.liquid_glass.gpu = self.glass_gpu
        self._gpu_scene_ready = False
        self._gpu_scene_uv = (0.0, 0.0, 1.0, 1.0)
        self.hud_angle_values: dict[str, float] = {}
        self.dynamic_smooth_values: dict[str, float] = {}
        self.dynamic_island_last_event = ""
        self.dynamic_island_event_started = time.perf_counter()
        self.frame_dt = 1.0 / 60.0

        scenario_module = self.registry.get("Scenario")
        self.home_visible = self.setting_bool(scenario_module, "Show Startup", True)
        self.startup_step = "scenario" if self.home_visible else "done"
        self.startup_transition_started = time.perf_counter()
        self.gui_visible = False
        self.selected_category = "Combat"
        self.selected_module = self.registry.get("ArmorAutoAim") or self.registry.modules[0]
        self.search_query = ""
        self.search_focus = False
        self.hotspots: list[Hotspot] = []
        self.hover_tooltip = ""
        self.hover_point = QPointF(0.0, 0.0)
        self.liquid_press_point = QPointF(-1000.0, -1000.0)
        self.liquid_press_until = 0.0
        # 交互窗口内临时抬帧，静止回低帧 → 体感更丝滑。
        self.ui_interactive_until = 0.0
        self._hud_glass_last_update = 0.0
        self._hud_glass_last_key = 0
        self._combat_aa_enabled = True
        self.drag: dict[str, Any] | None = None
        self.binding_module: Module | None = None
        self.toasts: list[dict[str, Any]] = []
        self.module_expanded: set[str] = {"ArmorAutoAim", "EnergyAutoAim", "RobotOverlay", "HUD"}
        self.module_list_rows: dict[str, dict[str, Any]] = {}
        self.settings_scroll: dict[str, float] = {}
        self.panel_settings_rect = QRectF()
        self.panel_modules_rect = QRectF()
        self.map_images: dict[str, QImage] = {}
        self.synthetic_video_cache: QImage | None = None
        self.synthetic_video_cache_key: tuple[int, int] | None = None
        self.synthetic_video_cache_time = 0.0
        self.frame_image: QImage | None = None
        # GPU 直传：持有最新 BGR numpy，paintGL 里 glTexSubImage，避免每帧 QImage.copy
        self._video_bgr = None
        self._video_bgr_seq = 0
        self._video_gpu_seq_uploaded = -1
        # 主 HUD 独立保存图传的低分辨率玻璃快照，绝不读取启动页壁纸。
        self.fps_samples: list[float] = []
        self.last_frame_time = time.perf_counter()
        self.last_control_toast = 0.0
        self.tactical_map_hold = False
        self.config_loading = False
        self.wallpaper_picker_visible = False
        self.wallpaper_picker_page = 0
        self.gpu_viewport_enabled = True

        self.old_panels = {
            category: {"x": 20.0 + i * 158.0, "y": 24.0, "expanded": category in ("Combat", "Vision", "Render"), "dragged": False}
            for i, category in enumerate(CATEGORIES)
        }
        self.new_panels = {category: {"scroll": 0.0} for category in CATEGORIES}
        self.dropdown_panels = {category: {"expanded": True, "scroll": 0.0} for category in CATEGORIES}

        self.apply_match_mode(self.match_mode_manager.current.key, close_home=False, announce=False)
        self.refresh_config_profiles()
        self.refresh_wallpaper_profiles()
        self.config_store.ensure_default(self.current_config_data())
        self.load_startup_config()
        elimination_fx = self.registry.get("EliminationFX")
        self.kill_fx.preload_preset_sounds(
            self.setting_mode(elimination_fx, "Preset", "Default Scrolling")
        )
        if os.environ.get("POLAR_AUTOSTART", "").lower() in ("1", "true", "yes"):
            self.home_visible = False
            self.startup_step = "done"
        self.sync_mode_setting()
        if not self.home_visible:
            self.refresh_vision_runtime()
        self.toast("ClickGUI 已就绪：PolarPanel")

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.on_tick)
        self.timer.start(16)

    def on_tick(self) -> None:
        now = time.perf_counter()
        dt = max(0.001, min(0.05, now - self.last_frame_time))
        self.last_frame_time = now
        self.frame_dt = dt
        self.fps_samples.append(1.0 / dt)
        if len(self.fps_samples) > 60:
            self.fps_samples.pop(0)
        self.state.fps = sum(self.fps_samples) / len(self.fps_samples)
        if not self.home_visible:
            self.ensure_camera_started()
        self.startup_wallpaper.set_active(self.home_visible)
        if not self.home_visible:
            self.refresh_vision_runtime()
        self.consume_capture_frame()
        self.state.camera_connected = self.camera_worker.connected
        self.state.mqtt_connected = self.mqtt.connected
        self.state.udp_video_connected = self.udp.frames_received > 0
        self.state.serial_connected = self.serial.connected
        self.state.tick(dt)
        self.consume_official_messages()
        self.apply_official_state()
        self.refresh_gestalt_control_runtime()
        self.consume_hook_client_keys()
        self.consume_gestalt_attrs()
        self.sync_tongji_vision_state()
        self.publish_aa_state()
        self.apply_lab_auto_aim_mouse()
        self.process_vision_kill_events()
        self.registry.tick(self.state, dt)
        if self.home_visible:
            target_fps = self.startup_target_fps()
            if self.frame_scheduler.should_request(now, True, target_fps=target_fps):
                self.update()
        else:
            target_fps = self.combat_target_fps()
            if self.frame_scheduler.should_request(now, True, target_fps=target_fps):
                self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

    def initializeGL(self) -> None:
        try:
            self.startup_gpu.initialize()
        except Exception as exc:
            self.startup_gpu.last_error = str(exc)
        try:
            self.glass_gpu.initialize()
            self.liquid_glass.gpu = self.glass_gpu
        except Exception as exc:
            self.glass_gpu.last_error = str(exc)

    def _gpu_pixel_size(self) -> tuple[int, int, float]:
        dpr = float(self.devicePixelRatioF())
        pw = max(1, int(round(self.width() * dpr)))
        ph = max(1, int(round(self.height() * dpr)))
        return pw, ph, dpr

    def _bind_glass_gpu_scene(self, *, texture=None, scene_uv: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)) -> None:
        pw, ph, dpr = self._gpu_pixel_size()
        self.liquid_glass.gpu = self.glass_gpu
        self.liquid_glass.gpu_scene_texture = texture
        self.liquid_glass.gpu_scene_uv = scene_uv
        self.liquid_glass.gpu_pixel_size = (pw, ph)
        self.liquid_glass.gpu_dpr = dpr
        self.liquid_glass.prefer_gpu = self.glass_quality() != "lite" and self.glass_gpu.available

    def paintGL(self) -> None:
        context = self.context()
        functions = context.functions() if context is not None else None
        if functions is not None:
            # 稳定清屏：失败/切换时也不要闪默认脏色。
            if self.home_visible:
                functions.glClearColor(0.024, 0.035, 0.063, 1.0)  # #060910
            else:
                functions.glClearColor(0.043, 0.071, 0.106, 1.0)  # #0b121b
            functions.glClear(0x00004000)  # GL_COLOR_BUFFER_BIT

        startup_background_ready = False
        combat_background_ready = False
        self._gpu_scene_ready = False
        pw, ph, dpr = self._gpu_pixel_size()

        if self.home_visible and self.startup_gpu.available:
            # 裸 GL 与 QPainter 之间必须 beginNativePainting，否则二级页切换易花屏一帧。
            bridge = QPainter(self)
            bridge.beginNativePainting()
            try:
                startup_background_ready = self.draw_startup_gpu_background()
                if startup_background_ready and functions is not None:
                    # 拷屏到 scene_tex，GPU 玻璃采样与壁纸像素一致
                    if self.glass_gpu.copy_framebuffer(functions, pw, ph):
                        self._bind_glass_gpu_scene(
                            texture=self.glass_gpu.scene_tex,
                            scene_uv=(0.0, 0.0, 1.0, 1.0),
                        )
                        self._gpu_scene_ready = True
            finally:
                bridge.endNativePainting()
                bridge.end()
        elif not self.home_visible and self.glass_gpu.available:
            bridge = QPainter(self)
            bridge.beginNativePainting()
            try:
                combat_background_ready = self.draw_combat_gpu_background(functions, pw, ph)
                if combat_background_ready and functions is not None:
                    # blit 后再 copy，玻璃采到的是最终底图
                    if self.glass_gpu.copy_framebuffer(functions, pw, ph):
                        self._bind_glass_gpu_scene(
                            texture=self.glass_gpu.scene_tex,
                            scene_uv=(0.0, 0.0, 1.0, 1.0),
                        )
                        self._gpu_scene_ready = True
            finally:
                bridge.endNativePainting()
                bridge.end()

        if not self._gpu_scene_ready:
            self.liquid_glass.prefer_gpu = False
            self.liquid_glass.gpu_scene_texture = None

        self.paint_overlay(
            None,
            startup_background_ready=startup_background_ready,
            combat_background_ready=combat_background_ready,
        )

    def paint_overlay(
        self,
        _event: QPaintEvent | None,
        *,
        startup_background_ready: bool = False,
        combat_background_ready: bool = False,
    ) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.hotspots.clear()
        width = self.width()
        height = self.height()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not self.high_res_optimize(width, height))

        if self.home_visible:
            if not startup_background_ready:
                self.draw_startup_background(p, width, height)
            if self.wallpaper_picker_visible:
                self.draw_wallpaper_picker(p, width, height)
            else:
                if self.startup_step == "match":
                    self.draw_match_mode_screen(p, width, height)
                else:
                    self.draw_startup_screen(p, width, height)
            if self.hover_tooltip:
                self.draw_tooltip(p, self.hover_point, self.hover_tooltip)
            return

        # GPU 已铺底时不要再清成实色，否则盖掉图传纹理。
        if not combat_background_ready:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(QRectF(0, 0, width, height), color("#0b121b", 1.0))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        lite = self.hud_perf_lite(width, height)
        self._combat_aa_enabled = not lite
        p.setRenderHint(QPainter.RenderHint.Antialiasing, not lite)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not lite)

        tongji_result = self.active_tongji_result()
        if not combat_background_ready:
            self.draw_video(p, width, height)
        else:
            # 仍更新玻璃采样源（CPU 侧 fallback / 亮度），但不重绘底图
            if self.frame_image is not None and not self.frame_image.isNull():
                self.update_hud_liquid_source(self.frame_image, force=False)
            elif self.synthetic_video_cache is not None:
                self.update_hud_liquid_source(self.synthetic_video_cache, force=False)
        armor_target = self.tongji_target(width, height, tongji_result) if tongji_result is not None else None
        if armor_target is None:
            # 选择真实桥接后绝不拿 mock 框伪装成检测结果；断链状态只保留 HUD 提示。
            armor_target = (
                VisionTarget(-160.0, -160.0, 1.0, 1.0, 0.0, "BRIDGE OFFLINE")
                if self._vision_source != "Mock"
                else self.vision.process(width, height, self.state, "armor")
            )
        # 能量 mock 仅在启用时计算，避免每帧双 process。
        energy_module = self.registry.get("EnergyAutoAim")
        if energy_module and energy_module.enabled and self._vision_source == "Mock":
            energy_target = self.vision.process(width, height, self.state, "energy")
        else:
            energy_target = armor_target
        self.draw_rm_hud(p, width, height, armor_target, energy_target, tongji_result)
        self.draw_elimination_fx(p, width, height)
        if self.hover_tooltip and not self.gui_visible:
            self.draw_tooltip(p, self.hover_point, self.hover_tooltip)

        gui_alpha = self.anim.motion(
            "clickgui.open",
            1.0 if self.gui_visible else 0.0,
            MotionTokens.OPEN,
            initial=1.0 if self.gui_visible else 0.0,
            duration_scale=self.motion_scale(),
        )
        if gui_alpha > 0.01:
            p.fillRect(QRectF(0, 0, width, height), with_alpha(THEME.dim, gui_alpha))
            p.save()
            p.setOpacity(gui_alpha)
            mode = self.mode_manager.mode
            if mode == "PolarOld":
                self.draw_old_gui(p, width, height, gui_alpha)
            elif mode == "PolarPanel":
                self.draw_panel_gui(p, width, height, gui_alpha)
            elif mode == "PolarNew":
                self.draw_new_gui(p, width, height, gui_alpha)
            else:
                self.draw_dropdown_gui(p, width, height, gui_alpha)
            self.draw_mode_switcher(p, width, height)
            self.draw_toasts(p, width, height)
            if self.hover_tooltip:
                self.draw_tooltip(p, self.hover_point, self.hover_tooltip)
            p.restore()

        if self.binding_module is not None:
            self.draw_keybind_overlay(p, width, height)

    def draw_startup_gpu_background(self) -> bool:
        if not self.home_visible or not self.startup_gpu.available:
            return False
        try:
            module = self.registry.get("StartupVisual")
            mode = self.setting_mode(module, "Background Mode", "VideoParallax")
            profile = self.setting_mode(module, "Wallpaper Profile", "TexasMP4")
            dim = self.get_setting_number(module, "Dim Overlay", 0.28)
            canvas = QRectF(0, 0, self.width(), self.height())
            snapshot = self.startup_wallpaper.prepare_gpu_video(
                canvas,
                self.hover_point,
                self.frame_dt,
                mode=mode,
                profile=profile,
                parallax=self.get_setting_number(module, "Parallax Strength", 22.0),
                zoom=self.get_setting_number(module, "Background Zoom", 1.0),
                high_res=self.high_res_optimize(),
                motion_quality=self.setting_mode(module, "Motion Quality", "Balanced30"),
            )
            if snapshot is None:
                # 页面切换/解码间隙：保留上一帧 GPU 纹理，避免闪黑花屏。
                if not self.startup_gpu.has_frame:
                    return False
                draw_rect = self.startup_wallpaper.last_draw_rect
                if draw_rect.isEmpty():
                    draw_rect = canvas
                self.startup_gpu.touch_rect_dim(draw_rect, dim)
            else:
                self.startup_gpu.update_frame(
                    snapshot.image,
                    self.startup_wallpaper.last_draw_rect,
                    snapshot.sequence,
                    snapshot.frame_interval,
                    dim,
                )
                # 仅在视频序列变化时刷新玻璃采样，避免同帧重复 copy。
                last_seq = getattr(self, "_startup_glass_sequence", -1)
                if snapshot.sequence != last_seq:
                    self._startup_glass_sequence = snapshot.sequence
                    self.liquid_glass.update(
                        "startup",
                        snapshot.image,
                        self.startup_wallpaper.last_draw_rect,
                        soft=self.startup_wallpaper.soft_glass_frame,
                        lens=self.startup_wallpaper.glass_frame,
                        quality=self.glass_quality(),
                    )
                else:
                    snap = self.liquid_glass.snapshots.get("startup")
                    if snap is not None:
                        snap.draw_rect = QRectF(self.startup_wallpaper.last_draw_rect)
            context = self.context()
            if context is None or not self.startup_gpu.has_frame:
                return False
            dpr = float(self.devicePixelRatioF())
            pixel_w = max(1, int(round(self.width() * dpr)))
            pixel_h = max(1, int(round(self.height() * dpr)))
            ok = bool(
                self.startup_gpu.render(
                    context.functions(),
                    canvas,
                    pixel_width=pixel_w,
                    pixel_height=pixel_h,
                )
            )
            # GPU 失败时返回 False，让 paint_overlay 走 CPU 全屏壁纸，避免「只有玻璃有图、背景全黑」。
            return ok
        except Exception as exc:
            self.startup_gpu.last_error = str(exc)
            # 不永久关掉 GPU：纹理错误常可在下一帧 destroy+create 恢复；本帧回退 CPU。
            return False

    def mark_ui_interactive(self, hold: float = 0.45) -> None:
        self.ui_interactive_until = max(self.ui_interactive_until, time.perf_counter() + hold)

    def combat_busy(self) -> bool:
        """击杀特效 / ClickGUI / 拖拽 / 刚交互 → 需要更高刷新。"""
        now = time.perf_counter()
        if now < self.ui_interactive_until or self.gui_visible or self.drag is not None:
            return True
        if getattr(self.kill_fx, "last_kill_at", 0.0) and now - float(self.kill_fx.last_kill_at) < 2.2:
            return True
        return bool(self.hover_tooltip)

    def combat_target_fps(self) -> float:
        """战斗 UI 帧率：与 SHM 显示限帧对齐，避免无脑 60 吃满 CPU。"""
        # 图传已限 ~30fps 时，HUD 再高也只能重复画同一帧
        if self.camera_source_uses_shm() and not self.gui_visible:
            if self.combat_busy():
                return 36.0
            return 30.0
        if self.combat_busy():
            return 58.0 if self.gui_visible else 52.0
        density = self.hud_density()
        if density == "DebugFull":
            return 42.0
        if density == "Balanced":
            return 34.0
        return 30.0

    def glass_quality(self) -> str:
        """液态玻璃质量：full | balanced | lite。默认 full（对齐 liquid-glass-react 完整观感）。"""
        module = self.registry.get("UIScale")
        raw = self.setting_mode(module, "Glass Quality", "Full")
        key = raw.strip().lower()
        if key in ("lite", "low"):
            return "lite"
        if key in ("balanced", "medium"):
            return "balanced"
        return "full"

    def hud_perf_lite(self, width: int | None = None, height: int | None = None) -> bool:
        """仅影响 AA / 假图传分辨率等，不再用来“关掉”液态玻璃。"""
        if self.glass_quality() == "lite":
            return True
        if self.high_res_optimize(width, height):
            return True
        return False

    def startup_target_fps(self) -> float:
        """启动页刷新率：静止偏低，交互/转场时抬高。"""
        module = self.registry.get("StartupVisual")
        mode = self.setting_mode(module, "Background Mode", "VideoParallax")
        quality = self.setting_mode(module, "Motion Quality", "Balanced30")
        interactive = (
            time.perf_counter() < self.ui_interactive_until
            or bool(self.hover_tooltip)
            or (time.perf_counter() - self.startup_transition_started < 0.55)
        )
        if mode == "ParticleGrid":
            base = 45.0 if quality == "Smooth60" else 30.0
            return 58.0 if interactive else base
        if mode == "Static":
            return 48.0 if interactive else 20.0
        if quality == "Smooth60":
            return 58.0 if interactive else 42.0
        if quality == "Source":
            return 52.0 if interactive else 34.0
        return 56.0 if interactive else 30.0

    def startup_needs_high_rate(self) -> bool:
        # 保留兼容入口；实际刷新由 startup_target_fps 控制。
        return self.startup_target_fps() >= 30.0

    def draw_combat_gpu_background(self, functions, pixel_w: int, pixel_h: int) -> bool:
        """战斗底图：优先 BGR numpy → GPU 纹理 SubImage（直传），否则 QImage 回退。"""
        if not self.glass_gpu.available:
            return False
        # 热路径：SHM/摄像头 BGR → glTexSubImage2D
        if self._video_bgr is not None and self.camera_source_is_live():
            seq = int(self._video_bgr_seq)
            if seq != self._video_gpu_seq_uploaded or self.glass_gpu.scene_key != seq:
                if self.glass_gpu.upload_bgr_numpy(functions, self._video_bgr, seq):
                    self._video_gpu_seq_uploaded = seq
                else:
                    # GPU 上传失败再走 QImage
                    pass
            if self.glass_gpu.scene_tex is not None and self.glass_gpu.scene_tex.isCreated():
                return bool(
                    self.glass_gpu.blit_scene_fullscreen(
                        functions, pixel_w=pixel_w, pixel_h=pixel_h
                    )
                )
        image: QImage | None = None
        if self.frame_image is not None and not self.frame_image.isNull():
            image = self.frame_image
        elif self.synthetic_video_cache is not None and not self.synthetic_video_cache.isNull():
            image = self.synthetic_video_cache
        else:
            self.draw_synthetic_video_cached_prepare()
            if self.synthetic_video_cache is not None and not self.synthetic_video_cache.isNull():
                image = self.synthetic_video_cache
        if image is None or image.isNull():
            return False
        if not self.glass_gpu.upload_scene(image):
            return False
        return bool(self.glass_gpu.blit_scene_fullscreen(functions, pixel_w=pixel_w, pixel_h=pixel_h))

    def draw_synthetic_video_cached_prepare(self) -> None:
        """只更新合成缓存，不往 QPainter 画。"""
        width, height = self.width(), self.height()
        now = time.perf_counter()
        lite = self.hud_perf_lite(width, height)
        cache_w = min(960 if lite else 1120, max(480, width // 2 if lite else int(width * 0.65)))
        cache_h = max(270, int(cache_w * height / max(1, width)))
        key = (cache_w, cache_h)
        interval = 0.12 if self.combat_busy() else 0.18
        if self.synthetic_video_cache is None or self.synthetic_video_cache_key != key or now - self.synthetic_video_cache_time > interval:
            image = QImage(cache_w, cache_h, QImage.Format.Format_ARGB32)
            image.fill(color("#101721"))
            cache_p = QPainter(image)
            cache_p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            self.draw_synthetic_video_scene(cache_p, cache_w, cache_h, lite=True)
            cache_p.end()
            self.synthetic_video_cache = image
            self.synthetic_video_cache_key = key
            self.synthetic_video_cache_time = now

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.context() is not None:
            self.makeCurrent()
            self.startup_gpu.release()
            self.glass_gpu.release()
            self.doneCurrent()
        self.startup_wallpaper.release()
        self.camera_worker.stop()
        self.local_vision.stop()
        self.tongji_receiver.stop()
        self.tongji_bridge.stop()
        try:
            self.aa_state_publisher.set_enabled(False)
            self.aa_state_publisher.close()
        except Exception:
            pass
        self.mqtt.stop()
        self.udp.stop()
        self.serial.stop()
        self.gestalt_attrs.stop()
        try:
            self.gestalt_injector.configure(enabled=False, inject_mouse=False)
        except Exception:
            pass
        self.gestalt_injector.release_all()
        super().closeEvent(event)

    def camera_feed_source(self) -> str:
        source = self.registry.get("CameraFeed")
        return self.setting_mode(source, "Source", "Gestalt SHM")

    def camera_source_uses_device(self) -> bool:
        """Webcam / OBS 虚拟摄像头均走本机 VideoCapture（仅图传显示）。"""
        return self.camera_feed_source() in ("Webcam", "OBS VirtualCam")

    def camera_source_uses_shm(self) -> bool:
        """Gestalt VisionBridge 共享内存图传。"""
        return self.camera_feed_source() in ("Gestalt SHM", "Gestalt VisionBridge", "SHM")

    def camera_source_is_live(self) -> bool:
        return self.camera_source_uses_device() or self.camera_source_uses_shm()

    def ensure_camera_started(self) -> None:
        if not self.camera_source_is_live():
            return
        source = self.registry.get("CameraFeed")
        camera_id = int(self.get_setting_number(source, "Camera ID", 0))
        capture_width = int(self.get_setting_number(source, "Capture Width", 960))
        capture_height = int(self.get_setting_number(source, "Capture Height", 540))
        ws_port = int(self.get_setting_number(source, "WS Port", 0))
        game_pid = int(self.get_setting_number(source, "Game PID", 0))
        display_fps = float(self.get_setting_number(source, "Display Max FPS", 30))
        display_max_w = int(self.get_setting_number(source, "Display Max Width", 960))
        shm = self.camera_source_uses_shm()
        # SHM：默认不强制拉满原生分辨率到 UI；用 Display Max Width 限宽更流畅
        native = self.setting_bool(source, "SHM Native Size", False)
        kind = "shm" if shm else "device"
        env_ws = os.environ.get("POLAR_WS_PORT", "").strip()
        if env_ws.isdigit() and int(env_ws) > 0:
            ws_port = int(env_ws)
        requested_config = (
            kind,
            camera_id,
            capture_width,
            capture_height,
            ws_port,
            game_pid,
            native,
            display_fps,
            display_max_w,
        )
        if self.camera_started and self._camera_config != requested_config:
            self.camera_worker.stop()
            self.camera_started = False
            self.frame_image = None
        if not self.camera_started:
            # SHM + 非 native：按 Capture 尺寸；native 时仍受 Display Max Width 约束
            resize = (not shm) or (not native)
            self.camera_worker.configure(
                camera_id,
                (capture_width, capture_height),
                source_kind=kind,
                ws_port=ws_port,
                game_pid=game_pid,
                resize_to_setting=resize,
                max_display_fps=display_fps if shm else 0.0,
                display_max_width=display_max_w if shm else 0,
            )
            self.camera_worker.on_frame = self.on_captured_camera_frame
            self.camera_worker.start()
            self.camera_started = True
            self._camera_config = requested_config

    def draw_video(self, p: QPainter, width: int, height: int) -> None:
        if self.camera_source_is_live():
            self.ensure_camera_started()
            if self.frame_image is not None:
                p.drawImage(QRectF(0, 0, width, height), self.frame_image)
                return
            # waiting for SHM / camera: dark field + status, not fake synthetic match
            if self.camera_source_uses_shm():
                p.fillRect(0, 0, width, height, color("#050505", 1.0))
                status = getattr(self.camera_worker, "status_text", "shm")
                err = getattr(self.camera_worker, "last_error", "") or ""
                msg = f"GESTALT SHM  {status}"
                if err:
                    msg += f"\n{err}"
                self.draw_text(
                    p,
                    QRectF(24, height * 0.42, width - 48, 80),
                    msg,
                    self.font("mono", 12.0),
                    color("#ff6a00", 0.9),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                )
                return
        self.draw_synthetic_video(p, width, height)

    def refresh_vision_runtime(self) -> None:
        """显示向视觉入站。自瞄控制/解算只应在同济外挂 NUC，不得改这里的显示语义。

        - Local：仅用图传同帧做**显示用**检测快照（可选），不发云台。
        - Tongji/Auto：收 NUC UDP 检测结果上屏。
        - ArmorAutoAim 只控制**是否画框**（draw_rm_hud），不启停本管线、不清快照。
        """
        bridge_module = self.registry.get("TongjiVisionBridge")
        self._vision_source = os.environ.get("POLAR_VISION_SOURCE", self.setting_mode(bridge_module, "Vision Source", "Tongji"))
        if self._vision_source not in ("Local", "Tongji", "Auto", "Mock"):
            self._vision_source = "Tongji"
        bridge_enabled = bool(bridge_module and bridge_module.enabled)
        # 外挂 NUC / 外部检测器 UDP → client 只收显示数据
        self._tongji_enabled = self._vision_source in ("Tongji", "Auto") and bridge_enabled
        listen = self.setting_bool(bridge_module, "Listen", True)
        if self._tongji_enabled and listen:
            self.tongji_receiver.configure(int(self.get_setting_number(bridge_module, "UDP Port", 9871)))
            self.tongji_receiver.start()
        elif self.tongji_receiver.running:
            self.tongji_receiver.stop()

        # Local 同帧检测仅服务 HUD 框；与 ArmorAutoAim 开关解耦（开关只影响绘制）
        local_enabled = (
            bridge_enabled
            and self._vision_source in ("Local", "Auto")
            and self.camera_source_is_live()
        )
        model_path = resolve_tongji_yolo_xml(self.workspace)
        device = self.setting_mode(bridge_module, "Local Device", "AUTO")
        if local_enabled and model_path.is_file():
            self.local_vision.configure(model_path, device)
            overlay = self.registry.get("RobotOverlay")
            self.local_vision.configure_prediction(
                int(self.get_setting_number(overlay, "Armor Count", 4)),
                self.get_setting_number(overlay, "Radius M", 0.23),
                self.get_setting_number(overlay, "Predict Horizon", 0.06),
            )
            self.local_vision.start()
        elif self.local_vision.running:
            self.local_vision.stop()

        # 旧 TCP 桥接器只保留给历史配置兼容，默认视觉路径不再抢占摄像头或等待 WSL。
        if self.tongji_bridge.running:
            self.tongji_bridge.stop()

    def on_captured_camera_frame(self, frame: CapturedFrame) -> None:
        # 图传帧可给 Local 显示检测；不在此做自瞄控制。ArmorAutoAim 不拦截提交。
        if self._vision_source in ("Local", "Auto"):
            self.local_vision.submit(frame.sequence, frame.width, frame.height, frame.bgr)

    def consume_capture_frame(self) -> None:
        frame = self.camera_worker.latest_frame()
        if frame is None or frame.sequence <= self._last_capture_sequence:
            return
        self._last_capture_sequence = frame.sequence
        # GPU 直传持有 BGR（worker 已 copy 拥有权）；paintGL 上传纹理
        if frame.bgr is not None:
            cam = self.registry.get("CameraFeed")
            bgr = frame.bgr
            if self.setting_bool(cam, "Mirror", False):
                try:
                    bgr = bgr[:, ::-1, :].copy()  # 水平镜像，保持 C 连续
                except Exception:
                    pass
            self._video_bgr = bgr
            self._video_bgr_seq = int(frame.sequence)
        # 亮度采样降频
        try:
            if frame.sequence % 6 == 0:
                sample = frame.bgr[::64, ::64]
                self._vision_scene_luminance = float(sample.mean()) / 255.0
                self.state.vision_scene_luminance = self._vision_scene_luminance
        except Exception:
            pass
        # CPU QImage 仅作回退 / 玻璃 / 非 GPU 路径；降频构建
        need_cpu_image = (
            not self.glass_gpu.available
            or self.glass_quality() == "lite"
            or frame.sequence % 3 == 0
        )
        if need_cpu_image and frame.bgr is not None:
            bytes_per_line = int(frame.width) * 3
            src = frame.bgr
            fmt = (
                QImage.Format.Format_BGR888
                if hasattr(QImage.Format, "Format_BGR888")
                else QImage.Format.Format_RGB888
            )
            if fmt == QImage.Format.Format_RGB888:
                try:
                    import cv2  # type: ignore

                    src = cv2.cvtColor(frame.bgr, cv2.COLOR_BGR2RGB)
                except Exception:
                    src = frame.rgb
            image = QImage(
                src.data,
                int(frame.width),
                int(frame.height),
                bytes_per_line,
                fmt,
            ).copy()
            camera_module = self.registry.get("CameraFeed")
            if self.setting_bool(camera_module, "Mirror", False):
                image = image.mirrored(True, False)
            self.frame_image = image
            if frame.sequence % 3 == 0:
                self.update_hud_liquid_source(image, force=False)
        # 有新视频帧时强制下一帧 GL 刷新
        self.frame_scheduler.force()

    def active_tongji_result(self) -> VisionResult | None:
        bridge_module = self.registry.get("TongjiVisionBridge")
        timeout = self.get_setting_number(bridge_module, "UDP Timeout", 250.0)
        local_snapshot: VisionSnapshot | None = None
        local_result = self.local_vision.latest(timeout)
        if local_result is not None:
            timestamp_ns, sequence, frame_width, frame_height, armors, fps, latency_ms = local_result
            local_snapshot = snapshot_from_local_result(
                timestamp_ns, sequence, frame_width, frame_height, armors, fps, latency_ms, self.local_vision.active_device
            )
        tongji_snapshot: VisionSnapshot | None = None
        packet = self.tongji_receiver.latest(timeout)
        if packet is not None:
            tongji_snapshot = snapshot_from_tongji_packet(packet, inference_device=self.setting_mode(bridge_module, "External Device", "AUTO"))
            self.gimbal_command_bridge.cache(tongji_snapshot.gimbal_command)
        snapshot = self.composite_vision.choose(self._vision_source, local_snapshot, tongji_snapshot)
        self._vision_snapshot = snapshot
        if snapshot is not None:
            self._last_vision_result = snapshot.result
            return snapshot.result
        return None

    def vision_result_calibrated(self, result: VisionResult | None) -> bool:
        """本地棋盘格标定是否有效（用于桌面相机可信度角标）。"""
        if result is None or not result.calibrated:
            return False
        return self.calibration.is_valid_for(result.source_width, result.source_height)

    def vision_can_show_multi_plate(self, result: VisionResult | None) -> bool:
        """是否允许画四装甲板预测：本地棋盘标定 / Local PnP / 外挂已给多板或几何。"""
        if result is None:
            return False
        if self.vision_result_calibrated(result):
            return True
        if len(result.predicted_armors) > 0:
            return True
        if any(len(a.back_points) == 4 for a in result.detections + result.predicted_armors):
            return True
        # 外挂声明 calibrated（同济/sp_vision 内参），不强制 client 再棋盘标定
        if result.calibrated and (
            result.aim_armor is not None or any(len(a.points) == 4 for a in result.detections)
        ):
            return True
        return False

    def alliance_side(self) -> str:
        profile = self.registry.get("AllianceProfile")
        side = self.setting_mode(profile, "Side", "") if profile else ""
        if side not in ("Red", "Blue"):
            side = self.setting_mode(self.registry.get("MatchMode"), "Alliance", "Red")
        return side if side in ("Red", "Blue") else "Red"

    def consume_official_messages(self) -> None:
        for topic, payload, _received_at in self.mqtt.drain_received_messages():
            events, updates = self.official_feed.consume(topic, payload, alliance=self.alliance_side(), local_robot_id=self.state.robot_id)
            self._official_state_updates.update(updates)
            for event in events:
                self.accept_combat_event(event)

    def apply_official_state(self) -> None:
        if not self._official_state_updates:
            if not self.state.gestalt_telemetry_fresh():
                self.state.official_events_online = False
            return
        for key, value in self._official_state_updates.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
        self.state.official_events_online = self.official_feed.is_fresh()
        self.state.official_feed_at = float(self._official_state_updates.get("official_feed_at", 0.0))
        if self.state.official_events_online:
            self.state.telemetry_source = "gestalt"
            self.state.gestalt_attrs_at = time.perf_counter()

    def refresh_gestalt_control_runtime(self) -> None:
        module = self.registry.get("GestaltControl")
        inject = bool(module and module.enabled and self.setting_bool(module, "Inject Enabled", False))
        inject_mouse = bool(module and self.setting_bool(module, "Inject Mouse", False))
        # ClickGUI / 启动页：暂停注入与钩子
        if self.gui_visible or self.home_visible or self._inject_paused_for_gui:
            inject = False
            inject_mouse = False
        try:
            self.gestalt_injector.set_client_hwnd(int(self.winId()))
        except Exception:
            pass

        # 同步 client 保留键（模块绑定 R/V…）；其余键默认进游戏（含 P）
        try:
            self.gestalt_injector.set_client_reserved_keys(self.client_hotkey_names())
        except Exception:
            pass

        # Launcher 可通过环境变量强制：POLAR_USE_INPUT_BRIDGE / POLAR_BRIDGE_EXCLUSIVE
        env_bridge = os.environ.get("POLAR_USE_INPUT_BRIDGE", "").strip().lower()
        env_excl = os.environ.get("POLAR_BRIDGE_EXCLUSIVE", "").strip().lower()
        use_bridge = bool(module and self.setting_bool(module, "Use Input Bridge DLL", True))
        if env_bridge in ("0", "false", "off", "no"):
            use_bridge = False
        elif env_bridge in ("1", "true", "on", "yes"):
            use_bridge = True
        exclusive = bool(module and self.setting_bool(module, "Exclusive Capture", False))
        if env_excl in ("1", "true", "on", "yes"):
            exclusive = True
        elif env_excl in ("0", "false", "off", "no"):
            exclusive = False
        # ClickGUI 打开时禁止独占，避免锁死
        if self.gui_visible or self.home_visible:
            exclusive = False

        self.gestalt_injector.configure(
            enabled=inject,
            inject_mouse=bool(inject and inject_mouse),
            keep_client_focus=True,
            mouse_sensitivity=self.get_setting_number(module, "Mouse Sensitivity", 1.0) if module else 1.0,
            use_input_bridge=use_bridge,
            bridge_exclusive=bool(inject and exclusive),
        )
        self._mouse_look_active = bool(inject and inject_mouse)
        # SpinMode 已开时，注入恢复后同步游戏小陀螺 R
        spin = self.registry.get("SpinMode")
        if inject and spin is not None:
            self.gestalt_injector.set_game_spin(bool(spin.enabled))
        elif not inject:
            self.gestalt_injector.set_game_spin(False)

        if inject and not getattr(self, "_game_first_inject_toast", False):
            st = self.gestalt_injector.status
            self.toast(f"夺舍注入 ON [{st}] 游戏键/鼠标→DLL | 模块键→client | 自瞄=RBExtAim")
            self._game_first_inject_toast = True

        if self.gui_visible:
            self.state.control_inject_status = "paused_gui"
        else:
            self.state.control_inject_status = self.gestalt_injector.status if inject else "disabled"
        tele = self.registry.get("GestaltTelemetry")
        if tele and tele.enabled and self.setting_bool(tele, "Listen Attr Relay", True):
            self.gestalt_attrs.configure(int(self.get_setting_number(tele, "Relay Port", 9840)))
            self.gestalt_attrs.start()
        elif self.gestalt_attrs.running:
            self.gestalt_attrs.stop()

    def consume_hook_client_keys(self) -> None:
        """游戏前台时 LL 钩子截获的 client 保留键 → POLAR 热键。"""
        if not hasattr(self.gestalt_injector, "drain_client_keys"):
            return
        events = self.gestalt_injector.drain_client_keys()
        if not events:
            return
        for key_name, pressed, vk in events:
            self._dispatch_hook_key(key_name, pressed=pressed, vk=vk)

    def _dispatch_hook_key(self, key_name: str, *, pressed: bool = True, vk: int = 0) -> None:
        """钩子截获的 client 保留键。游戏键不会进入此路径。"""
        kn = (key_name or "").strip()
        if not kn:
            return
        low = kn.lower().replace(" ", "")
        # 释放事件：目前 client 热键多为 toggle 按一下，忽略 release
        if not pressed:
            if low == "tab" or vk == 0x09:
                self.tactical_map_hold = False
            return

        if low == "f7":
            self.toggle_gestalt_inject()
            return
        if low == "f8" or low == "rightshift" or vk == 0xA1:
            self.set_gui_visible(not self.gui_visible)
            return
        if low == "f9":
            mode = self.mode_manager.next_mode()
            self.sync_mode_setting()
            self.toast(f"GUI 模式：{mode}")
            return
        if low == "escape":
            if self.gui_visible:
                self.set_gui_visible(False)
            return
        # Tab：按住放大 / 松开缩小，不 toggle 地图模块
        if low == "tab" or vk == 0x09:
            tactical = self.registry.get("TacticalMap")
            self.tactical_map_hold = bool(pressed and tactical and tactical.enabled)
            return

        # 模块绑定 R=自瞄 V=小陀螺…（跳过 TacticalMap）
        for module in self.registry.modules:
            if module.name == "TacticalMap":
                continue
            if not module.keybind or module.keybind == "None":
                continue
            bind = module.keybind.strip().lower().replace(" ", "")
            if bind == low or bind == kn.lower():
                self.registry.toggle_module(module, source="keybind")
                self.selected_module = module
                self.toast(f"{module.name} {'已启用' if module.enabled else '已关闭'}")
                self.auto_save_config()
                return

    def consume_gestalt_attrs(self) -> None:
        if self.gestalt_attrs.apply_latest(self.state):
            return

    def process_vision_kill_events(self) -> None:
        effect = self.registry.get("EliminationFX")
        result = self._vision_snapshot.result if self._vision_snapshot else None
        if effect is None or result is None:
            return
        evidence = self.setting_mode(effect, "Evidence Mode", "Auto")
        official_online = self.official_feed.is_fresh()
        if evidence == "Referee Only" or (evidence == "Auto" and official_online):
            return
        events = self.vision_kill_detector.process(
            result.detections,
            alliance=self.alliance_side(),
            frame_luminance=self._vision_scene_luminance,
        )
        for event in events:
            self.accept_combat_event(event)

    def accept_combat_event(self, event: CombatEvent) -> None:
        effect = self.registry.get("EliminationFX")
        if effect is None:
            return
        evidence = self.setting_mode(effect, "Evidence Mode", "Auto")
        if event.source == "vision" and evidence == "Referee Only":
            return
        if event.source not in ("vision", "preview") and evidence == "Vision Only":
            return
        if event.kind == "elimination":
            if event.source == "vision":
                self.state.visual_kill_count += 1
            else:
                self.state.referee_kill_count += 1
            self.state.kill_streak += 1
        elif event.kind == "death":
            self.state.kill_streak = 0
        self.state.push_event("VISION CONFIRMED" if event.source == "vision" else "Referee elimination")
        self.dynamic_island_last_event = "VISION CONFIRMED" if event.source == "vision" else "Official combat event"
        self.dynamic_island_event_started = time.perf_counter()
        if effect.enabled:
            self.kill_fx.trigger(
                event,
                combo_window=self.get_setting_number(effect, "Combo Window", 5.0),
                sound_enabled=self.setting_bool(effect, "Sound Enabled", True),
                volume=self.get_setting_number(effect, "Sound Volume", 55.0),
                preset=self.setting_mode(effect, "Preset", "Default Scrolling"),
                visibility=self.setting_mode(effect, "Element Visibility", "Preset"),
            )

    def draw_elimination_fx(self, p: QPainter, width: int, height: int) -> None:
        effect = self.registry.get("EliminationFX")
        if effect is None or not effect.enabled:
            return
        team_theme = self.setting_mode(effect, "Team Theme", "Alliance")
        if team_theme == "Alliance":
            team_theme = "CT Blue" if self.alliance_side() == "Blue" else "T Gold"
        self.kill_fx.draw(
            p, width, height,
            preset=self.setting_mode(effect, "Preset", "Default Scrolling"),
            visibility=self.setting_mode(effect, "Element Visibility", "Preset"),
            team=team_theme,
            scale=self.get_setting_number(effect, "Scale", 1.0),
            anchor_policy=self.setting_mode(effect, "Anchor Policy", "Combat Focus"),
            x_offset=self.get_setting_number(effect, "X Offset", 0.0),
            y_offset=self.get_setting_number(effect, "Y Offset", 0.0),
            responsive_scale=self.setting_mode(effect, "Responsive Scale", "Auto") == "Auto",
            device_pixel_ratio=self.devicePixelRatioF(),
        )

    def publish_aa_state(self) -> None:
        armor = self.registry.get("ArmorAutoAim")
        on = bool(armor and armor.enabled)
        if on != self.aa_state_publisher.enabled:
            self.aa_state_publisher.set_enabled(on)
        else:
            self.aa_state_publisher.publish(force=False)

    def apply_lab_auto_aim_mouse(self) -> None:
        """[已废弃默认路径] OS 鼠标跟枪。

        SHM Lab 默认只用 GameLink **RBExtAim 闭环**（外挂侧绝对角），不碰鼠标。
        仅当环境变量 ``POLAR_ALLOW_MOUSE_AIM=1`` 且外挂显式 ``lab_aim=true`` /
        ``aim_mode=lab_aim|mouse`` 时才注入——调试遗留，正式联调不要开。
        """
        import os as _os

        if _os.environ.get("POLAR_ALLOW_MOUSE_AIM", "").strip() not in ("1", "true", "TRUE", "yes"):
            self._lab_aim_smooth_x = self._lab_aim_smooth_y = 0.0
            return
        cmd = self.gimbal_command_bridge.last_command or {}
        mode = str(cmd.get("aim_mode", "") or "")
        if mode == "rbextaim" or str(cmd.get("pipeline", "")).startswith("gestalt_shm"):
            self._lab_aim_smooth_x = self._lab_aim_smooth_y = 0.0
            return
        if not cmd.get("lab_aim") and mode not in ("lab_aim", "mouse"):
            self._lab_aim_smooth_x = self._lab_aim_smooth_y = 0.0
            return
        if self.home_visible or self.gui_visible:
            self._lab_aim_smooth_x = self._lab_aim_smooth_y = 0.0
            return
        armor = self.registry.get("ArmorAutoAim")
        if not armor or not armor.enabled:
            self._lab_aim_smooth_x = self._lab_aim_smooth_y = 0.0
            return
        if not self.gestalt_injector.enabled:
            return
        delta = self.gimbal_command_bridge.lab_aim_delta()
        if delta is None:
            self._lab_aim_smooth_x *= 0.6
            self._lab_aim_smooth_y *= 0.6
            return
        err_x, err_y = delta
        if abs(err_x) < 0.015:
            err_x = 0.0
        if abs(err_y) < 0.015:
            err_y = 0.0
        if err_x == 0.0 and err_y == 0.0:
            self._lab_aim_smooth_x *= 0.5
            self._lab_aim_smooth_y *= 0.5
            return
        ema = float(self._lab_aim_ema)
        self._lab_aim_smooth_x = self._lab_aim_smooth_x * (1.0 - ema) + err_x * ema
        self._lab_aim_smooth_y = self._lab_aim_smooth_y * (1.0 - ema) + err_y * ema
        sx, sy = self._lab_aim_smooth_x, self._lab_aim_smooth_y

        def shaped(e: float, gain: float) -> float:
            sign = 1.0 if e >= 0 else -1.0
            mag = min(1.0, abs(e))
            shaped_mag = 0.55 * mag + 0.45 * (mag * mag)
            return sign * shaped_mag * gain

        dx = max(-28.0, min(28.0, shaped(sx, self._lab_aim_gain_x)))
        dy = max(-28.0, min(28.0, shaped(sy, self._lab_aim_gain_y)))
        if abs(dx) < 0.25 and abs(dy) < 0.25:
            return
        self.gestalt_injector.mouse_move(dx, dy, force=True)
        self.state.control_inject_status = self.gestalt_injector.status

    def sync_tongji_vision_state(self) -> None:
        result = self.active_tongji_result()
        src = self._vision_snapshot.source if self._vision_snapshot else self._vision_source
        self.state.vision_backend = src
        if src == "Local":
            self.state.vision_backend = "Local-display-only"
        self.state.vision_connected = bool(self._vision_snapshot) if self._vision_source != "Mock" else False
        if result is None:
            self.state.vision_tracking_state = "mock" if self._vision_source == "Mock" else "disconnected"
            self.state.vision_calibrated = False
            self.state.vision_detection_count = 0
            self.state.vision_prediction_count = 0
            return
        calibrated = self.vision_result_calibrated(result)
        self.state.vision_calibrated = calibrated
        self.state.vision_tracking_state = result.tracking_state
        self.state.vision_inference_device = result.inference_device
        self.state.vision_inference_fps = result.inference_fps
        self.state.vision_latency_ms = result.latency_ms
        self.state.vision_sequence = result.sequence
        self.state.vision_detection_count = len(result.detections)
        self.state.vision_prediction_count = len(result.predicted_armors)
        self.state.vision_aim_yaw_deg = result.aim_yaw_deg
        self.state.vision_aim_pitch_deg = result.aim_pitch_deg
        target = result.aim_armor or next(iter(result.detections), None)
        if target is not None:
            self.state.target_id = target.armor_id if target.armor_id >= 0 else self.state.target_id
            self.state.target_distance_m = target.distance_m or self.state.target_distance_m
            self.state.target_locked = bool(calibrated and result.is_tracking)
            self.state.lock_state = self.state.target_locked

    def tongji_target(self, width: int, height: int, result: VisionResult) -> VisionTarget | None:
        observation = result.aim_armor or next((armor for armor in result.detections if len(armor.points) == 4), None)
        if observation is None or len(observation.points) < 2:
            return None
        x_scale = width / max(1.0, float(result.source_width))
        y_scale = height / max(1.0, float(result.source_height))
        camera_module = self.registry.get("CameraFeed")
        mirror = self.setting_bool(camera_module, "Mirror", False)
        points = [(width - x * x_scale if mirror else x * x_scale, y * y_scale) for x, y in observation.points]
        xs, ys = zip(*points)
        return VisionTarget(
            x=min(xs),
            y=min(ys),
            w=max(4.0, max(xs) - min(xs)),
            h=max(4.0, max(ys) - min(ys)),
            confidence=observation.confidence,
            label=observation.label,
            kind="armor",
        )

    def draw_synthetic_video(self, p: QPainter, width: int, height: int) -> None:
        lite = self.hud_perf_lite(width, height)
        # 战斗默认走缓存合成：避免每帧重绘整屏假图传。
        if lite or self.high_res_optimize(width, height) or width * height >= 1_200_000:
            self.draw_synthetic_video_cached(p, width, height)
            return
        self.draw_synthetic_video_scene(p, width, height, lite=False)
        now = time.perf_counter()
        cache_w = min(280, max(180, width // 5))
        cache_h = max(100, int(cache_w * height / max(1, width)))
        key = (cache_w, cache_h)
        interval = 0.16 if lite else 0.12
        if self.synthetic_video_cache is None or self.synthetic_video_cache_key != key or now - self.synthetic_video_cache_time > interval:
            image = QImage(cache_w, cache_h, QImage.Format.Format_ARGB32)
            image.fill(color("#101721"))
            cache_p = QPainter(image)
            cache_p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            self.draw_synthetic_video_scene(cache_p, cache_w, cache_h, lite=True)
            cache_p.end()
            self.synthetic_video_cache = image
            self.synthetic_video_cache_key = key
            self.synthetic_video_cache_time = now
        if self.synthetic_video_cache is not None:
            self.update_hud_liquid_source(self.synthetic_video_cache, force=False)

    def draw_synthetic_video_cached(self, p: QPainter, width: int, height: int) -> None:
        now = time.perf_counter()
        lite = self.hud_perf_lite(width, height)
        # 主画面缓存分辨率再砍一档：视觉够用，CPU 明显下降。
        cache_w = min(960 if lite else 1120, max(480, width // 2 if lite else int(width * 0.65)))
        cache_h = max(270, int(cache_w * height / max(1, width)))
        key = (cache_w, cache_h)
        interval = 0.12 if self.combat_busy() else 0.18
        if self.synthetic_video_cache is None or self.synthetic_video_cache_key != key or now - self.synthetic_video_cache_time > interval:
            image = QImage(cache_w, cache_h, QImage.Format.Format_ARGB32)
            image.fill(color("#101721"))
            cache_p = QPainter(image)
            cache_p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            cache_p.setRenderHint(QPainter.RenderHint.TextAntialiasing, False)
            self.draw_synthetic_video_scene(cache_p, cache_w, cache_h, lite=True)
            cache_p.end()
            self.synthetic_video_cache = image
            self.synthetic_video_cache_key = key
            self.synthetic_video_cache_time = now
        if self.synthetic_video_cache is not None:
            self.update_hud_liquid_source(self.synthetic_video_cache, force=False)
            p.drawImage(QRectF(0, 0, width, height), self.synthetic_video_cache)

    def update_hud_liquid_source(self, image: QImage, *, force: bool = False) -> None:
        """节流 HUD 玻璃采样：降采样 + 间隔更新，避免每相机帧三次 copy/blur 语义成本。"""
        if image.isNull():
            return
        now = time.perf_counter()
        key = int(image.cacheKey())
        interval = 0.09 if self.combat_busy() else 0.14
        if not force and key == self._hud_glass_last_key and now - self._hud_glass_last_update < interval:
            # 只同步 draw_rect（窗口尺寸变化时仍正确采样）。
            snap = self.liquid_glass.snapshots.get("hud")
            if snap is not None:
                snap.draw_rect = QRectF(0, 0, self.width(), self.height())
            return
        quality = self.glass_quality()
        max_w = {"full": 480, "balanced": 320, "lite": 220}.get(quality, 320)
        sample = image
        if image.width() > max_w:
            sample = image.scaledToWidth(max_w, Qt.TransformationMode.SmoothTransformation if quality == "full" else Qt.TransformationMode.FastTransformation)
        self.liquid_glass.update(
            "hud",
            sample,
            QRectF(0, 0, self.width(), self.height()),
            quality=quality,
        )
        self._hud_glass_last_update = now
        self._hud_glass_last_key = key

    def draw_hud_liquid_glass(
        self,
        p: QPainter,
        rect: QRectF,
        radius: float,
        *,
        surface: str = "MainGlass",
        hover: float = 0.0,
    ) -> None:
        """战斗 HUD 液态玻璃：默认完整 liquid-glass-react 语义，不因性能强制关掉。"""
        quality = self.glass_quality()
        if surface in ("SubGlass", "ToolGlass"):
            glass_surface = "tool" if surface == "ToolGlass" else "sub"
            mode = "tool" if surface == "ToolGlass" else "standard"
        elif surface == "ProminentGlass":
            glass_surface = "main"
            mode = "prominent"
        elif surface == "PolarGlass":
            glass_surface = "main"
            mode = "polar"
        else:
            glass_surface = "main"
            mode = "standard"
        self.liquid_glass.draw(
            p,
            rect,
            radius,
            source="hud",
            mode=mode,
            surface=glass_surface,
            hover=hover,
            press=self.liquid_press_amount(rect),
            pointer=self.hover_point,
            lite=quality == "lite",
            quality=quality,
        )

    def liquid_press_amount(self, rect: QRectF) -> float:
        if not rect.contains(self.liquid_press_point):
            return 0.0
        remaining = self.liquid_press_until - time.perf_counter()
        return max(0.0, min(1.0, remaining / 0.12))

    def draw_synthetic_video_scene(self, p: QPainter, width: int, height: int, lite: bool) -> None:
        sky = QLinearGradient(0, 0, 0, height)
        sky.setColorAt(0.0, color("#171d28"))
        sky.setColorAt(0.56, color("#121923"))
        sky.setColorAt(1.0, color("#0a1015"))
        p.fillRect(QRectF(0, 0, width, height), sky)

        horizon = height * 0.57
        p.fillRect(QRectF(0, horizon, width, height - horizon), color("#0c141a"))
        pen = QPen(color("#335063", 0.66), 1.0)
        p.setPen(pen)
        grid_x = 108 if lite else 78
        grid_y = 46 if lite else 34
        for x in range(-width, width * 2, grid_x):
            p.drawLine(QPointF(x, horizon), QPointF(x - width * 0.36, height))
        for y in range(int(horizon), height, grid_y):
            p.drawLine(QPointF(0, y), QPointF(width, y))

        now = time.perf_counter()
        p.setPen(Qt.PenStyle.NoPen)
        particle_count = 24 if lite else 38
        for i in range(particle_count):
            phase = now * 0.38 + i * 1.73
            px = (i * 127.0 + math.sin(phase) * 34.0) % max(1, width)
            py = horizon + ((i * 41.0 + now * 18.0) % max(1.0, height - horizon))
            near = (py - horizon) / max(1.0, height - horizon)
            radius = 1.0 + near * 2.2
            p.setBrush(color("#56fff0", 0.18 + near * 0.18))
            p.drawEllipse(QPointF(px, py), radius, radius)

        p.setPen(QPen(color("#56fff0", 0.18), 1.2))
        p.drawLine(QPointF(0, horizon), QPointF(width, horizon))

        cx = width * 0.5 + math.sin(now * 0.82) * width * 0.17
        base = QRectF(cx - 68, horizon - 112, 136, 94)
        glow_rect(p, base, 6, 0.26 if lite else 0.48)
        rounded_rect(p, base, 5, color("#252d38"), color("#7c8d9f", 0.68), 1.4)
        p.setPen(QPen(color("#56fff0", 0.86), 2))
        p.drawLine(QPointF(base.left() + 16, base.top() + 22), QPointF(base.right() - 16, base.top() + 22))
        self.draw_text(
            p,
            QRectF(0, height - 32, width, 18),
            "合成机器人图传 - 摄像头不可用",
            self.font("poppins_regular", 8.5),
            color("#9aa6ac"),
            Qt.AlignmentFlag.AlignCenter,
        )

    def draw_startup_background(self, p: QPainter, width: int, height: int) -> None:
        module = self.registry.get("StartupVisual")
        mode = self.setting_mode(module, "Background Mode", "VideoParallax")
        profile = self.setting_mode(module, "Wallpaper Profile", "TexasMP4")
        parallax = self.get_setting_number(module, "Parallax Strength", 22.0)
        zoom = self.get_setting_number(module, "Background Zoom", 1.00)
        dim = self.get_setting_number(module, "Dim Overlay", 0.28)
        motion_quality = self.setting_mode(module, "Motion Quality", "Balanced30")
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
                sequence = int(self.startup_wallpaper.glass_sequence)
                last_seq = getattr(self, "_startup_glass_sequence", -1)
                if sequence != last_seq:
                    self._startup_glass_sequence = sequence
                    self.liquid_glass.update(
                        "startup",
                        self.startup_wallpaper.last_frame,
                        self.startup_wallpaper.last_draw_rect,
                        soft=self.startup_wallpaper.soft_glass_frame,
                        lens=self.startup_wallpaper.glass_frame,
                        quality=self.glass_quality(),
                    )
                else:
                    snap = self.liquid_glass.snapshots.get("startup")
                    if snap is not None:
                        snap.draw_rect = QRectF(self.startup_wallpaper.last_draw_rect)
                return
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
                self.anim.motion("startup.bg.mouse.x", target_x, MotionTokens.PARALLAX, initial=width * 0.5, duration_scale=self.motion_scale()),
                self.anim.motion("startup.bg.mouse.y", target_y, MotionTokens.PARALLAX, initial=height * 0.45, duration_scale=self.motion_scale()),
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
        active = self.anim.motion("startup.open", 1.0, MotionTokens.PAGE, initial=0.0, duration_scale=self.motion_scale())
        scenario_module = self.registry.get("Scenario")
        style_setting = scenario_module.get_setting("Startup Style") if scenario_module else None
        if style_setting is not None and str(style_setting.value) == "PolarLauncher":
            self.draw_startup_scene_launcher(p, width, height, active)
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
        self.draw_text(p, QRectF(x, logo_y + 10 * scale, menu_w, 44 * scale), "CUSTOM CLIENT", body_font, THEME.muted, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

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
            p.fillRect(QRectF(draw_rect.left() + 3 * scale, draw_rect.top() + 3 * scale, 3 * scale, draw_rect.height() - 6 * scale), accent)
            self.draw_text(p, draw_rect.adjusted(18 * scale, 9 * scale, -104 * scale, -28 * scale), config.title, self.font("poppins_bold", 13.2 * scale), THEME.text)
            self.draw_text(p, draw_rect.adjusted(18 * scale, 31 * scale, -18 * scale, -8 * scale), config.subtitle, small_font, THEME.muted)
            safety = "MOCK" if config.safety.mock_writes else "LIVE"
            chip = QRectF(draw_rect.right() - 82 * scale, draw_rect.center().y() - 11 * scale, 64 * scale, 22 * scale)
            rounded_rect(p, chip, 7 * scale, color("#ffffff", 0.055), color("#ffffff", 0.10 + 0.12 * hover_v))
            self.draw_text(p, chip, safety, self.font("poppins_medium", 7.3 * scale), accent, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(draw_rect, "select_scenario", key, tooltip=config.description)

        current = self.scenario_manager.current
        footer = f"{current.title} | MQTT {current.connection.mqtt_host}:{current.connection.mqtt_port} | UDP {current.connection.udp_port}"
        self.draw_text(p, QRectF(x, y + total_h + 22 * scale, menu_w, 18 * scale), f"Step 1/2 | {footer}", small_font, color("#888890"), Qt.AlignmentFlag.AlignCenter)
        p.restore()

    def draw_match_mode_screen(self, p: QPainter, width: int, height: int) -> None:
        active = self.anim.motion("startup.match.open", 1.0, MotionTokens.PAGE, initial=0.0, duration_scale=self.motion_scale())
        scenario_module = self.registry.get("Scenario")
        style_setting = scenario_module.get_setting("Startup Style") if scenario_module else None
        if style_setting is not None and str(style_setting.value) == "PolarLauncher":
            self.draw_startup_match_launcher(p, width, height, active)
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
        self.draw_text(p, QRectF(x, logo_y + 10 * scale, menu_w, 44 * scale), self.scenario_manager.current.title.upper(), body_font, THEME.muted, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        for index, key in enumerate(MATCH_MODE_KEYS):
            config = MATCH_MODES[key]
            rect = QRectF(x, y + index * (row_h + gap), menu_w, row_h)
            hovered = rect.contains(self.hover_point)
            hover_v = self.anim.value(f"startup.match.hover.{key}", 1.0 if hovered else 0.0, self.anim_duration(0.18), Easings.ease_out_quad)
            draw_rect = rect.adjusted((1.0 - active) * 20.0 * scale, 0, 0, 0)
            accent = self.startup_menu_accent(index + 2)
            glow_rect(p, draw_rect, 10 * scale, 0.14 + 0.10 * hover_v)
            rounded_rect(p, draw_rect, 8 * scale, color("#09090d", 0.90 + 0.05 * hover_v), color("#ffffff", 0.10 + 0.16 * hover_v))
            p.fillRect(QRectF(draw_rect.left() + 3 * scale, draw_rect.top() + 3 * scale, 3 * scale, draw_rect.height() - 6 * scale), accent)
            self.draw_text(p, draw_rect.adjusted(18 * scale, 8 * scale, -140 * scale, -45 * scale), config.title, self.font("poppins_bold", 13.6 * scale), THEME.text)
            details = f"{config.field.width_m:g}m x {config.field.height_m:g}m | official map asset | {config.duration_sec // 60}:{config.duration_sec % 60:02d}"
            self.draw_text(p, draw_rect.adjusted(18 * scale, 34 * scale, -18 * scale, -24 * scale), config.subtitle, small_font, THEME.muted)
            self.draw_text(p, draw_rect.adjusted(18 * scale, 52 * scale, -18 * scale, -6 * scale), details, small_font, color("#9fa7ad"))
            chip = QRectF(draw_rect.right() - 110 * scale, draw_rect.center().y() - 12 * scale, 88 * scale, 24 * scale)
            rounded_rect(p, chip, 7 * scale, color("#ffffff", 0.055), color("#ffffff", 0.10 + 0.12 * hover_v))
            self.draw_text(p, chip, "LOAD HUD", self.font("poppins_medium", 7.3 * scale), accent, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(draw_rect, "select_match_mode", key, tooltip=f"载入 {config.title} 官方地图和默认 HUD。")

        footer = "Step 2/2 | Backspace: scenario | Enter: current mode"
        self.draw_text(p, QRectF(x, y + total_h + 22 * scale, menu_w, 18 * scale), footer, small_font, color("#888890"), Qt.AlignmentFlag.AlignCenter)
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
        card_w = min(292.0 * scale, (width - 112.0 * scale - gap * 2.0) / 3.0)
        card_h = 166.0 * scale
        total_w = card_w * 3.0 + gap * 2.0
        x = width / 2.0 - total_w / 2.0
        y = height / 2.0 - card_h / 2.0 + 26.0 * scale

        logo_y = max(78.0 * scale, y - 112.0 * scale)
        glow_text(p, "POLAR-RM", x, logo_y, title_font, THEME.text, THEME.cyan, 9 * scale)
        self.draw_text(p, QRectF(x, logo_y + 11 * scale, total_w, 42 * scale), "LAUNCHER / SCENE SELECT", body_font, THEME.muted, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.draw_text(p, QRectF(x, logo_y + 46 * scale, total_w, 18 * scale), "选择运行环境", self.font("poppins_medium", 8.5 * scale), color("#c9d6dc", 0.90))

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
        footer = f"Step 1/2 | 当前: {current_meta['title']} | 按 1/2/3 快速选择"
        self.draw_text(p, QRectF(x, y + card_h + 27 * scale, total_w, 18 * scale), footer, small_font, color("#aeb7bd", 0.88), Qt.AlignmentFlag.AlignCenter)
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
        self.draw_text(p, QRectF(x, logo_y + 11 * scale, total_w, 42 * scale), scenario_meta["title"].upper(), body_font, THEME.muted, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.draw_text(p, QRectF(x, logo_y + 46 * scale, total_w, 18 * scale), "选择比赛制式", self.font("poppins_medium", 8.5 * scale), color("#c9d6dc", 0.90))

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
                "LOAD HUD",
                "select_match_mode",
                key,
                meta["tooltip"],
                self.startup_menu_accent(index + 2),
                scale,
                active,
            )

        footer = "Step 2/2 | Backspace 返回 | Enter 进入当前模式"
        self.draw_text(p, QRectF(x, y + card_h + 27 * scale, total_w, 18 * scale), footer, small_font, color("#aeb7bd", 0.88), Qt.AlignmentFlag.AlignCenter)
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
        self.draw_liquid_menu_header(p, width, height, scale, "MATCH MODE", f"{scenario_meta['title']} profile", active)

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
                "LOAD HUD",
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

    def startup_stagger_value(
        self,
        index: int,
        duration: float | None = None,
        delay: float | None = None,
    ) -> float:
        duration = MotionTokens.STAGGER_DURATION if duration is None else duration
        delay = MotionTokens.STAGGER_DELAY if delay is None else delay
        elapsed = time.perf_counter() - getattr(self, "startup_transition_started", time.perf_counter())
        elapsed -= max(0, index) * self.anim_duration(delay)
        if elapsed <= 0.0:
            return 0.0
        return Easings.soft_back_out(elapsed / self.anim_duration(duration))

    def motion_scale(self) -> float:
        module = self.registry.get("UIScale")
        speed = self.get_setting_number(module, "Animation Speed", 1.0)
        return 1.0 / max(0.2, speed)

    def mix_color(self, a: QColor, b: QColor, t: float) -> QColor:
        t = max(0.0, min(1.0, t))
        return QColor(
            round(a.red() + (b.red() - a.red()) * t),
            round(a.green() + (b.green() - a.green()) * t),
            round(a.blue() + (b.blue() - a.blue()) * t),
            round(a.alpha() + (b.alpha() - a.alpha()) * t),
        )

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
        quality = self.glass_quality()
        if surface in ("SubGlass", "ToolGlass"):
            glass_surface = "tool" if surface == "ToolGlass" else "sub"
            mode = "tool" if surface == "ToolGlass" else "standard"
        elif surface == "ModalGlass" or hover > 0.34:
            glass_surface = "main"
            mode = "prominent"
        else:
            glass_surface = "main"
            mode = "polar" if strength >= 1.0 else "standard"
        self.liquid_glass.draw(
            p,
            rect,
            radius,
            source="startup",
            mode=mode,
            surface=glass_surface,
            hover=hover * strength,
            press=self.liquid_press_amount(rect),
            pointer=self.hover_point,
            lite=quality == "lite",
            quality=quality,
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
            p.drawRoundedRect(rect.adjusted(r * 0.15, r * 0.30, -r * 0.15, -r * 0.30), 4.0 * scale, 4.0 * scale)
            p.drawLine(QPointF(cx - r * 0.55, cy), QPointF(cx + r * 0.55, cy))
            label = "7" if "7" in key or key == "rmuc" else "3"
            self.draw_text(p, rect, label, self.font("poppins_bold", 20.0 * scale), c, Qt.AlignmentFlag.AlignCenter)
        else:
            p.drawLine(QPointF(cx + r * 0.55, cy), QPointF(cx - r * 0.35, cy - r * 0.55))
            p.drawLine(QPointF(cx - r * 0.35, cy - r * 0.55), QPointF(cx - r * 0.35, cy + r * 0.55))
            p.drawLine(QPointF(cx - r * 0.35, cy + r * 0.55), QPointF(cx + r * 0.55, cy))
        p.restore()

    def draw_liquid_menu_header(self, p: QPainter, width: int, height: int, scale: float, title: str, subtitle: str, active: float) -> None:
        enter = self.startup_stagger_value(0, MotionTokens.STAGGER_DURATION + 0.08, 0.0)
        y = 42.0 * scale - (1.0 - enter) * 48.0 * scale
        x = 50.0 * scale
        p.save()
        p.setOpacity(active * max(0.0, min(1.0, enter)))
        # 品牌标题轻 glow；中部页面标题用交叉淡入感的 notice。
        glow_text(p, "POLAR-RM", x, y, self.font("museo", 28.0 * scale, QFont.Weight.Bold), THEME.text, THEME.cyan, 4.0 * scale)
        self.draw_text(p, QRectF(x, y + 11.0 * scale, 330.0 * scale, 24.0 * scale), "CUSTOM CLIENT", self.font("poppins_medium", 8.5 * scale), THEME.muted)

        notice_w = min(420.0 * scale, width * 0.36)
        notice = QRectF(width / 2.0 - notice_w / 2.0, 28.0 * scale, notice_w, 40.0 * scale)
        self.draw_liquid_glass_panel(p, notice, THEME.radius_panel * scale, accent=THEME.blue, hover=0.0, scale=scale, surface="MainGlass")
        self.draw_glass_text(p, notice.adjusted(18.0 * scale, 4.0 * scale, -18.0 * scale, -19.0 * scale), title, self.font("poppins_bold", 9.0 * scale), THEME.text, Qt.AlignmentFlag.AlignCenter)
        self.draw_glass_text(p, notice.adjusted(18.0 * scale, 19.0 * scale, -18.0 * scale, -4.0 * scale), subtitle, self.font("poppins_regular", 7.0 * scale), THEME.muted, Qt.AlignmentFlag.AlignCenter)

        # 步骤点：场景=1 赛事=2
        step_index = 1 if self.startup_step == "match" else 0
        dots_y = notice.bottom() + 8.0 * scale
        dots_x = notice.center().x() - 10.0 * scale
        for i in range(2):
            r = 3.2 * scale if i == step_index else 2.4 * scale
            cx = dots_x + i * 14.0 * scale
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(with_alpha(THEME.cyan if i == step_index else THEME.muted, 0.95 if i == step_index else 0.45))
            p.drawEllipse(QPointF(cx, dots_y), r, r)

        profile = self.current_profile()
        profile_w = min(230.0 * scale, width * 0.22)
        profile_rect = QRectF(width - profile_w - 50.0 * scale, 34.0 * scale, profile_w, 28.0 * scale)
        self.draw_liquid_glass_panel(p, profile_rect, THEME.radius_chip * scale, accent=THEME.cyan, hover=0.0, scale=scale, surface="ToolGlass")
        self.draw_glass_text(p, profile_rect.adjusted(12.0 * scale, 0, -12.0 * scale, 0), f"PROFILE  {profile}", self.font("poppins_medium", 7.2 * scale), color("#d8e5ec", 0.88), Qt.AlignmentFlag.AlignCenter)
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
        enter = self.startup_stagger_value(index)
        hovered = rect.contains(self.hover_point)
        hover_v = self.anim.motion(
            f"startup.lb.hover.{anim_key}",
            1.0 if hovered else 0.0,
            MotionTokens.HOVER,
            duration_scale=self.motion_scale(),
        )
        press_v = self.liquid_press_amount(rect)
        draw_rect = QRectF(rect)
        # 入场位移收短；hover 上浮更轻；按下略缩。
        draw_rect.translate(-120.0 * (1.0 - enter) * scale, (-4.0 * hover_v + 1.5 * press_v) * scale)
        if press_v > 0.0:
            cx, cy = draw_rect.center().x(), draw_rect.center().y()
            draw_rect.setWidth(draw_rect.width() * (1.0 - 0.012 * press_v))
            draw_rect.setHeight(draw_rect.height() * (1.0 - 0.012 * press_v))
            draw_rect.moveCenter(QPointF(cx, cy))
        opacity = active * max(0.0, min(1.0, 0.15 + 0.85 * enter))
        # continuous 圆角，贴近 liquid-glass-react / iOS
        radius = THEME.radius_card * scale

        p.save()
        p.setOpacity(opacity)
        self.draw_liquid_glass_panel(p, draw_rect, radius, accent=accent, hover=hover_v, scale=scale, surface="MainGlass")

        # 主 CTA 左侧 accent 条 + 可控的 hover 渐变（不再扫满整行）
        slide_w = draw_rect.width() * (0.08 + 0.42 * hover_v)
        grad = QLinearGradient(draw_rect.topLeft(), QPointF(draw_rect.left() + slide_w, draw_rect.top()))
        grad.setColorAt(0.0, with_alpha(accent, 0.42 + 0.18 * hover_v))
        grad.setColorAt(0.70, with_alpha(THEME.cyan, 0.10 + 0.10 * hover_v))
        grad.setColorAt(1.0, color("#ffffff", 0.0))
        p.save()
        p.setClipRect(QRectF(draw_rect.left(), draw_rect.top(), slide_w, draw_rect.height()))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(draw_rect, radius, radius)
        p.restore()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(with_alpha(accent, 0.90))
        p.drawRoundedRect(QRectF(draw_rect.left(), draw_rect.top(), 3.5 * scale, draw_rect.height()), radius, radius)

        icon_size = min(90.0 * scale, draw_rect.height() - 28.0 * scale)
        icon_rect = QRectF(draw_rect.left() + 26.0 * scale, draw_rect.center().y() - icon_size / 2.0, icon_size, icon_size)
        icon_fill = self.mix_color(accent, THEME.text, hover_v)
        icon_text = self.mix_color(THEME.text, accent, hover_v)
        rounded_rect(p, icon_rect, icon_size * 0.50, with_alpha(icon_fill, 0.92), color("#ffffff", 0.10 + 0.13 * hover_v), 1.0)
        self.draw_liquid_icon(p, icon_rect.adjusted(14.0 * scale, 14.0 * scale, -14.0 * scale, -14.0 * scale), icon, icon_text, scale)

        text_left = icon_rect.right() + 24.0 * scale
        title_rect = QRectF(text_left, draw_rect.top() + 21.0 * scale, draw_rect.width() - (text_left - draw_rect.left()) - 126.0 * scale, 28.0 * scale)
        self.draw_glass_text(p, title_rect, title, self.font("poppins_bold", 18.0 * scale), THEME.text)
        self.draw_glass_text(p, QRectF(text_left, draw_rect.top() + 50.0 * scale, draw_rect.width() - (text_left - draw_rect.left()) - 42.0 * scale, 20.0 * scale), subtitle, self.font("poppins_regular", 9.0 * scale), color("#e1e8ee", 0.88))
        self.draw_glass_text(p, QRectF(text_left, draw_rect.bottom() - 31.0 * scale, draw_rect.width() - (text_left - draw_rect.left()) - 42.0 * scale, 18.0 * scale), detail, self.font("poppins_regular", 7.3 * scale), color("#aeb7bd", 0.90))

        shortcut = QRectF(draw_rect.right() - 78.0 * scale, draw_rect.top() + 20.0 * scale, 26.0 * scale, 22.0 * scale)
        rounded_rect(p, shortcut, 6.0 * scale, color("#ffffff", 0.08), color("#ffffff", 0.11 + 0.11 * hover_v))
        self.draw_text(p, shortcut, str(index + 1) if action != "startup_back" else "B", self.font("poppins_bold", 7.3 * scale), icon_text if hover_v > 0.55 else accent, Qt.AlignmentFlag.AlignCenter)

        chip_w = min(98.0 * scale, max(62.0 * scale, text_width(p, self.font("poppins_medium", 7.2 * scale), chip_text) + 22.0 * scale))
        chip = QRectF(draw_rect.right() - chip_w - 26.0 * scale, draw_rect.bottom() - 34.0 * scale, chip_w, 22.0 * scale)
        rounded_rect(p, chip, 7.0 * scale, color("#ffffff", 0.055 + 0.04 * hover_v), with_alpha(accent, 0.25 + 0.25 * hover_v))
        self.draw_text(p, chip, chip_text, self.font("poppins_medium", 7.2 * scale), icon_text if hover_v > 0.72 else accent, Qt.AlignmentFlag.AlignCenter)

        self.draw_text(p, QRectF(draw_rect.right() - 43.0 * scale, draw_rect.center().y() - 13.0 * scale, 18.0 * scale, 26.0 * scale), ">", self.font("poppins_bold", 15.0 * scale), THEME.text if hovered else THEME.muted, Qt.AlignmentFlag.AlignCenter)
        p.restore()

        self.add_hotspot(draw_rect.adjusted(-3.0 * scale, -3.0 * scale, 3.0 * scale, 3.0 * scale), action, payload, tooltip=tooltip)

    def draw_liquid_bottom_bar(self, p: QPainter, width: int, height: int, scale: float, active: float) -> None:
        enter = self.startup_stagger_value(0, MotionTokens.STAGGER_DURATION + 0.06, 0.12)
        actions: list[tuple[str, str, Any, QColor, str]] = [
            ("Config", "startup_open_config", None, THEME.cyan, "Open ConfigManager in ClickGUI."),
            ("Import", "config_action", "Import Config", THEME.pink, "Import a config json."),
            ("Wallpaper", "startup_open_wallpaper", None, color("#4677ff"), "Open StartupVisual wallpaper settings."),
            ("ClickGUI", "startup_open_gui", None, accent_color(4, 0.0), "Enter HUD and open ClickGUI."),
            ("Exit", "startup_exit", None, color("#ff6464"), "Close POLAR-RM."),
        ]
        if self.startup_step == "match":
            actions.insert(0, ("Back", "startup_back", None, color("#ffd766"), "Return to scene select."))
        # 底栏工具：更矮、更淡，不与主 CTA 抢戏。
        button_h = 34.0 * scale
        gap = 8.0 * scale
        widths = [max(64.0 * scale, text_width(p, self.font("poppins_medium", 7.6 * scale), item[0]) + 26.0 * scale) for item in actions]
        total_w = sum(widths) + gap * (len(actions) - 1) + 22.0 * scale
        rect = QRectF(50.0 * scale, height - 56.0 * scale + (1.0 - enter) * 36.0 * scale, min(total_w, width - 100.0 * scale), button_h + 12.0 * scale)
        p.save()
        p.setOpacity(active * max(0.0, min(1.0, enter)) * 0.88)
        self.draw_liquid_glass_panel(p, rect, THEME.radius_chip * scale, accent=THEME.cyan, hover=0.0, scale=scale, surface="ToolGlass")
        x = rect.left() + 11.0 * scale
        for index, (label, action, payload, accent, tooltip) in enumerate(actions):
            button_w = min(widths[index], rect.right() - x - 11.0 * scale)
            if button_w <= 18.0 * scale:
                break
            self.draw_liquid_small_action_button(p, QRectF(x, rect.top() + 6.0 * scale, button_w, button_h), label, action, payload, accent, scale, tooltip)
            x += button_w + gap
        p.restore()

    def draw_wallpaper_picker(self, p: QPainter, width: int, height: int) -> None:
        open_v = self.anim.value("startup.wallpaper.open", 1.0, self.anim_duration(0.24), Easings.ease_out_pow4, initial=0.0)
        full = QRectF(0, 0, width, height)
        p.fillRect(full, color("#02050a", 0.54 * open_v))
        self.add_hotspot(full, "wallpaper_close")

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
            "WALLPAPER LIBRARY",
            self.font("poppins_bold", 15.0 * scale),
            THEME.text,
        )
        self.draw_text(
            p,
            QRectF(panel.left() + 28.0 * scale, panel.top() + 47.0 * scale, panel.width() - 56.0 * scale, 18.0 * scale),
            "选择内置壁纸，或导入自己的 MP4 / GIF / 图片",
            self.font("poppins_regular", 8.2 * scale),
            color("#c8d5de", 0.78),
        )

        module = self.registry.get("StartupVisual")
        current = self.setting_mode(module, "Wallpaper Profile", "TexasMP4")
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
            ("Reset", "wallpaper_reset", None, THEME.cyan, "恢复德克萨斯默认壁纸。"),
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
                f"{self.wallpaper_picker_page + 1} / {page_count}",
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
            self.draw_text(p, preview, "LOADING" if profile != "ParticleGrid" else "GRID", self.font("poppins_bold", 9.0 * scale), color("#bdefff", 0.66), Qt.AlignmentFlag.AlignCenter)
        p.fillRect(preview, color("#02050a", 0.10 if hovered else 0.20))
        p.restore()

        if selected:
            p.setBrush(color("#54c7ff", 0.94))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(draw_rect.right() - 18.0 * scale, draw_rect.top() + 18.0 * scale), 5.0 * scale, 5.0 * scale)
        label = self.startup_wallpaper.profile_label(profile)
        kind = self.startup_wallpaper.profile_kind(profile)
        self.draw_text(p, QRectF(draw_rect.left() + 11.0 * scale, preview.bottom() + 8.0 * scale, draw_rect.width() - 22.0 * scale, 20.0 * scale), label, self.font("poppins_bold", 9.0 * scale), THEME.text)
        self.draw_text(p, QRectF(draw_rect.left() + 11.0 * scale, preview.bottom() + 29.0 * scale, draw_rect.width() - 22.0 * scale, 16.0 * scale), kind, self.font("poppins_medium", 6.9 * scale), accent if selected else THEME.muted)
        self.add_hotspot(draw_rect, "wallpaper_select", profile, tooltip=f"切换到 {label}")

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
        hover_v = self.anim.motion(
            f"startup.lb.small.hover.{label}",
            1.0 if hovered else 0.0,
            MotionTokens.HOVER,
            duration_scale=self.motion_scale(),
        )
        # Exit 仅在 hover 时显示危险色倾向。
        use_accent = accent if (label != "Exit" or hover_v > 0.2) else THEME.muted
        self.draw_liquid_glass_panel(p, rect, THEME.radius_chip * 0.85 * scale, accent=use_accent, hover=hover_v * 0.55, scale=scale, sample_background=False, surface="ToolGlass")
        fill = QRectF(rect.left(), rect.top(), rect.width() * (0.12 + 0.55 * hover_v), rect.height())
        if fill.width() > 1.0 and hover_v > 0.02:
            grad = QLinearGradient(rect.topLeft(), rect.topRight())
            grad.setColorAt(0.0, with_alpha(use_accent, 0.28 + 0.18 * hover_v))
            grad.setColorAt(1.0, with_alpha(THEME.cyan, 0.06))
            p.save()
            p.setClipRect(fill)
            p.setBrush(QBrush(grad))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, THEME.radius_chip * 0.85 * scale, THEME.radius_chip * 0.85 * scale)
            p.restore()
        self.draw_glass_text(
            p,
            rect.adjusted(8.0 * scale, 0, -8.0 * scale, 0),
            label,
            self.font("poppins_medium", 7.6 * scale),
            THEME.text if hover_v > 0.40 else color("#c5d0d8", 0.86),
            Qt.AlignmentFlag.AlignCenter,
        )
        self.add_hotspot(rect, action, payload, tooltip=tooltip)

    def draw_liquid_status_panel(self, p: QPainter, menu_x: float, menu_y: float, button_w: float, height: int, scale: float, page: str, active: float) -> None:
        right_x = menu_x + button_w + 48.0 * scale
        right_w = self.width() - right_x - 50.0 * scale
        if right_w < 245.0 * scale:
            return
        enter = self.startup_stagger_value(2, MotionTokens.STAGGER_DURATION + 0.04, MotionTokens.STAGGER_DELAY)
        panel = QRectF(right_x + (1.0 - enter) * 36.0 * scale, menu_y + 4.0 * scale, min(360.0 * scale, right_w), min(280.0 * scale, height - menu_y - 108.0 * scale))
        p.save()
        p.setOpacity(active * max(0.0, min(1.0, enter)) * 0.96)
        self.draw_liquid_glass_panel(p, panel, THEME.radius_card * scale, accent=THEME.blue, hover=0.0, scale=scale, surface="MainGlass")
        top_line = QRectF(panel.left() + 14.0 * scale, panel.top() + 12.0 * scale, panel.width() - 28.0 * scale, 2.0 * scale)
        grad = QLinearGradient(top_line.topLeft(), top_line.topRight())
        grad.setColorAt(0.0, color("#4677ff", 0.90))
        grad.setColorAt(0.55, THEME.cyan)
        grad.setColorAt(1.0, color("#ffffff", 0.28))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(top_line, 1.0 * scale, 1.0 * scale)
        self.draw_glass_text(p, QRectF(panel.left() + 18.0 * scale, panel.top() + 26.0 * scale, panel.width() - 36.0 * scale, 23.0 * scale), "SESSION", self.font("poppins_bold", 11.0 * scale), THEME.text)

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
            self.draw_glass_text(p, QRectF(row.left() + 10.0 * scale, row.top(), 58.0 * scale, row.height()), label, self.font("poppins_bold", 7.0 * scale), c, Qt.AlignmentFlag.AlignVCenter)
            self.draw_glass_text(p, row.adjusted(72.0 * scale, 0, -10.0 * scale, 0), value, self.font("poppins_medium", 8.1 * scale), color("#dce7ed", 0.92), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        tip_y = panel.bottom() - 56.0 * scale
        if tip_y > row_y + len(rows) * 34.0 * scale:
            tip = QRectF(panel.left() + 18.0 * scale, tip_y, panel.width() - 36.0 * scale, 37.0 * scale)
            self.draw_liquid_glass_panel(p, tip, 10.0 * scale, accent=THEME.cyan, hover=0.0, scale=scale, sample_background=False, surface="SubGlass")
            msg = "Press 1/2/3 to select. Enter continues." if page == "scenario" else "Press 1/2 to load. Backspace returns."
            self.draw_glass_text(p, tip.adjusted(12.0 * scale, 0, -12.0 * scale, 0), msg, self.font("poppins_regular", 7.2 * scale), THEME.muted, Qt.AlignmentFlag.AlignCenter)
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
        self.draw_text(p, icon_rect, icon, self.font("poppins_bold", min(17.5 * scale, icon_size * 0.34), QFont.Weight.Bold), THEME.text, Qt.AlignmentFlag.AlignCenter)

        shortcut = QRectF(draw_rect.right() - 38 * scale, draw_rect.top() + 26 * scale, 22 * scale, 20 * scale)
        rounded_rect(p, shortcut, 7 * scale, color("#ffffff", 0.06), color("#ffffff", 0.11 + 0.12 * hover_v))
        self.draw_text(p, shortcut, str(index + 1), self.font("poppins_bold", 7.4 * scale), accent, Qt.AlignmentFlag.AlignCenter)

        text_left = icon_rect.right() + 13 * scale
        text_w = draw_rect.right() - text_left - 46 * scale
        self.draw_text(p, QRectF(text_left, draw_rect.top() + 31 * scale, text_w, 26 * scale), title, self.font("poppins_bold", 14.0 * scale), THEME.text)
        self.draw_text(p, QRectF(text_left, draw_rect.top() + 57 * scale, draw_rect.right() - text_left - 18 * scale, 19 * scale), subtitle, self.font("poppins_regular", 8.0 * scale), color("#d0d5db", 0.86))

        detail_rect = QRectF(draw_rect.left() + 18 * scale, draw_rect.bottom() - 49 * scale, draw_rect.width() - 36 * scale, 17 * scale)
        self.draw_text(p, detail_rect, detail, self.font("poppins_regular", 7.6 * scale), color("#aeb7bd", 0.84))

        chip = QRectF(draw_rect.left() + 18 * scale, draw_rect.bottom() - 28 * scale, min(88.0 * scale, draw_rect.width() * 0.36), 18 * scale)
        rounded_rect(p, chip, 7 * scale, color("#ffffff", 0.055), with_alpha(accent, 0.25 + 0.25 * hover_v))
        self.draw_text(p, chip, chip_text, self.font("poppins_medium", 6.9 * scale), accent, Qt.AlignmentFlag.AlignCenter)

        arrow = QRectF(draw_rect.right() - 38 * scale, draw_rect.bottom() - 30 * scale, 22 * scale, 20 * scale)
        self.draw_text(p, arrow, ">", self.font("poppins_bold", 12.0 * scale), THEME.text if hovered else THEME.muted, Qt.AlignmentFlag.AlignCenter)
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
                "icon": "DJI",
                "title": "大疆官方",
                "subtitle": "官方参数 / 赛事限制",
                "tooltip": "载入官方链路参数和赛事安全限制。",
            },
            "CampusSimulation": {
                "icon": "SIM",
                "title": "校内模拟",
                "subtitle": "Shark / 本地图传",
                "tooltip": "载入校内训练和 SharkDataServer 模拟参数。",
            },
            "LabDebug": {
                "icon": "LAB",
                "title": "实验室调试",
                "subtitle": "摄像头 / Mock 输出",
                "tooltip": "载入本地摄像头预览、协议测试和 mock 输出。",
            },
        }.get(key, {"icon": "RM", "title": key, "subtitle": "POLAR profile", "tooltip": key})

    def startup_match_meta(self, key: str) -> dict[str, str]:
        return {
            "RMUC7v7": {
                "icon": "7V7",
                "title": "RMUC 7v7",
                "subtitle": "超级对抗赛 / 官方大地图",
                "tooltip": "载入 7v7 官方地图、小地图和默认战斗 HUD。",
            },
            "RMUL3v3": {
                "icon": "3V3",
                "title": "RMUL 3v3",
                "subtitle": "高校联盟赛 / 区域控制",
                "tooltip": "载入 3v3 官方地图、控制区和默认战斗 HUD。",
            },
        }.get(key, {"icon": "RM", "title": key, "subtitle": "Match profile", "tooltip": key})

    def reset_startup_page_animation(self, page: str) -> None:
        self.anim.reset_prefix("startup.card.enter.", 0.0)
        self.anim.reset_prefix("startup.lb.hover.", 0.0)
        self.anim.reset_prefix("startup.lb.small.hover.", 0.0)
        self.startup_transition_started = time.perf_counter()
        # 共享背景不动；UI 从非零 opacity 交叉入场，避免空闪。
        self.anim.reset("startup.match.open" if page == "match" else "startup.open", MotionTokens.PAGE_ENTER_FROM)
        self.mark_ui_interactive(0.70)

    def request_startup_repaint(self) -> None:
        """一级/二级切换后立刻重画，不把切换帧推迟到下一 tick。"""
        self.mark_ui_interactive(0.55)
        self.frame_scheduler.force()
        self.update()

    def draw_rm_hud(
        self,
        p: QPainter,
        width: int,
        height: int,
        armor_target: VisionTarget,
        energy_target: VisionTarget,
        tongji_result: VisionResult | None = None,
    ) -> None:
        hud = self.registry.get("HUD")
        armor_module = self.registry.get("ArmorAutoAim")
        energy_module = self.registry.get("EnergyAutoAim")
        armor_enabled = bool(armor_module and armor_module.enabled)
        energy_enabled = bool(energy_module and energy_module.enabled)
        armor_3d = self.registry.get("Armor3DESP")
        if armor_enabled and tongji_result is not None and armor_3d and armor_3d.enabled:
            self.draw_armor_3d_esp(p, width, height, tongji_result, armor_3d)
        elif armor_enabled:
            self.draw_target_overlay(p, armor_target, armor_module)
        # 真实装甲视觉接入后不允许旧 Mock 能量框叠到图传上。
        draw_energy = energy_enabled and self._vision_source == "Mock"
        if draw_energy:
            self.draw_target_overlay(p, energy_target, energy_module)
        if self.registry.get("Crosshair") and self.registry.get("Crosshair").enabled:
            self.draw_crosshair(p, width, height)
        primary_target = energy_target if draw_energy else armor_target
        hud_scale = self.get_hud_scale(width, height)
        scaled_width = max(1, int(width / hud_scale))
        scaled_height = max(1, int(height / hud_scale))
        p.save()
        p.scale(hud_scale, hud_scale)
        if self.setting_bool(hud, "Watermark", True):
            self.draw_watermark(p)
        if self.registry.get("MatchTimer") and self.registry.get("MatchTimer").enabled:
            self.draw_match_header(p, scaled_width)
        if self.setting_bool(hud, "Target HUD", True):
            title = "EnergyTargetHUD" if primary_target.kind == "energy" else "RobotTargetHUD"
            self.draw_target_hud_by_style(p, scaled_width, scaled_height, primary_target, title)
        if self.setting_bool(hud, "Dynamic Island", True) and not self.gui_visible:
            self.draw_dynamic_island(p, scaled_width)
        tactical_map = self.registry.get("TacticalMap")
        if tactical_map and tactical_map.enabled:
            self.draw_tactical_map_hud(p, scaled_width, scaled_height)
        if hud:
            mode_setting = hud.get_setting("Right Display")
            mode = str(mode_setting.value) if mode_setting else HUD_MODES[0]
            self.draw_right_hud_mode(p, scaled_width, scaled_height, mode, primary_target)
        self.draw_chassis_gimbal_indicator(p, scaled_width, scaled_height)
        self.draw_telemetry_strip(p, scaled_width, scaled_height)
        p.restore()

    def hud_density(self) -> str:
        hud = self.registry.get("HUD")
        setting = hud.get_setting("Density") if hud else None
        value = str(setting.value) if setting else "CombatCompact"
        return value if value in ("CombatCompact", "Balanced", "DebugFull") else "CombatCompact"

    def target_hud_style(self) -> str:
        hud = self.registry.get("HUD")
        setting = hud.get_setting("Target Style") if hud else None
        value = str(setting.value) if setting else "RoundCompact"
        return value if value in ("RoundCompact", "BarOnly", "FullCard") else "RoundCompact"

    def hud_visual_tone(self) -> str:
        hud = self.registry.get("HUD")
        setting = hud.get_setting("Visual Tone") if hud else None
        value = str(setting.value) if setting else "LiquidClean"
        return value if value in ("LiquidClean", "PolarLegacy") else "LiquidClean"

    def hud_clean(self) -> bool:
        return self.hud_visual_tone() == "LiquidClean"

    def hud_accent(self) -> QColor:
        return color("#5d8cff") if self.hud_clean() else THEME.cyan

    def hud_surface(self, alpha: float = 0.68) -> QColor:
        return color("#000000", alpha if self.hud_clean() else min(0.86, alpha + 0.08))

    def hud_panel_outline(self, alpha: float = 0.10) -> QColor:
        return color("#ffffff", alpha if self.hud_clean() else min(0.20, alpha + 0.04))

    def hud_warning(self) -> QColor:
        return color("#efbf04") if self.hud_clean() else color("#ffd166")

    def hud_danger(self) -> QColor:
        return color("#fc4130") if self.hud_clean() else THEME.red

    def hud_success(self) -> QColor:
        return color("#4dac68") if self.hud_clean() else THEME.green

    def hud_state_color(self, kind: str = "info", ratio: float = 0.0) -> QColor:
        if not self.hud_clean():
            return {
                "ally": THEME.cyan,
                "enemy": THEME.red,
                "danger": THEME.red,
                "warning": color("#ffd166"),
                "success": THEME.green,
                "gold": color("#ffd166"),
                "ammo": THEME.green,
                "control": THEME.pink,
                "spin": THEME.pink,
            }.get(kind, THEME.cyan)
        if kind in ("enemy", "danger"):
            return self.hud_danger()
        if kind in ("warning", "gold", "power"):
            if ratio >= 0.95:
                return self.hud_danger()
            return self.hud_warning() if ratio >= 0.78 or kind in ("warning", "gold", "power") else self.hud_accent()
        if kind in ("success", "ammo", "cap"):
            if kind == "cap" and ratio < 0.18:
                return self.hud_danger()
            if kind == "cap" and ratio < 0.34:
                return self.hud_warning()
            return self.hud_success() if kind in ("success", "ammo") else self.hud_accent()
        if kind in ("heat", "speed"):
            if ratio >= 0.92:
                return self.hud_danger()
            if ratio >= 0.78:
                return self.hud_warning()
            return self.hud_accent()
        return self.hud_accent()

    def liquid_map_enabled(self) -> bool:
        module = self.registry.get("LiquidTacticalMap")
        return bool(module and module.enabled)

    def liquid_map_setting(self, name: str, fallback: str) -> str:
        module = self.registry.get("LiquidTacticalMap")
        setting = module.get_setting(name) if module else None
        return str(setting.value) if setting else fallback

    def liquid_map_bool(self, name: str, fallback: bool = True) -> bool:
        module = self.registry.get("LiquidTacticalMap")
        return self.setting_bool(module, name, fallback)

    def draw_watermark(self, p: QPainter) -> None:
        z_font = self.font("museo", 18, QFont.Weight.Bold)
        body = self.font("poppins_medium", 10.5)
        glow_text(p, "POLAR-RM", 12, 28, z_font, THEME.text, self.hud_accent(), 3 if self.hud_clean() else 5)
        title_w = text_width(p, z_font, "POLAR-RM")
        self.draw_text(p, QRectF(22 + title_w, 12, 90, 24), f"{self.state.fps:.0f} FPS", body, THEME.text)
        status = "CAM" if self.state.camera_connected else "SYN"
        scene = self.scenario_manager.current.title
        match = self.match_mode_manager.current.title
        mqtt = "MQTT mock" if getattr(self.mqtt, "mock_output", True) else "MQTT live"
        tele = f"TEL {self.state.telemetry_source}"
        inj = f"CTL {self.state.control_inject_status}"
        armor = self.registry.get("ArmorAutoAim")
        aa = "AA on" if armor and armor.enabled else "AA off"
        vis = f"VIS {self.state.vision_backend or self._vision_source}"
        self.draw_text(
            p,
            QRectF(12, 34, 720, 16),
            f"{status} | {scene} | {match} | {tele} | {inj} | {aa} | {vis} | {mqtt}",
            self.font("poppins_regular", 8),
            THEME.muted,
        )

    def draw_target_overlay(self, p: QPainter, target: VisionTarget, module: Module | None = None) -> None:
        rect = QRectF(target.x, target.y, target.w, target.h)
        locked = self.state.energy_target_locked if target.kind == "energy" else self.state.target_locked
        setting = module.get_setting("Lock Style") if module else None
        style = str(setting.value) if setting else "Polar Corner"
        main = self.hud_accent() if locked else self.hud_danger()
        if style == "White Outline":
            self.draw_white_outline_lock(p, rect, target, locked)
            return
        if style == "Minimal Bracket":
            self.draw_minimal_bracket_lock(p, rect, target, locked)
            return
        if style == "Rune Ring":
            self.draw_rune_ring_lock(p, rect, target, module, locked)
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

    def draw_white_outline_lock(self, p: QPainter, rect: QRectF, target: VisionTarget, locked: bool) -> None:
        main = color("#ffffff", 0.94 if locked else 0.62)
        accent = self.hud_accent() if locked else self.hud_danger()
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(main, 1.25))
        p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        corner = min(22.0, rect.width() * 0.22)
        p.setPen(QPen(main, 2.1))
        for start, dx, dy in (
            (rect.topLeft(), corner, 0),
            (rect.topLeft(), 0, corner),
            (rect.topRight(), -corner, 0),
            (rect.topRight(), 0, corner),
            (rect.bottomLeft(), corner, 0),
            (rect.bottomLeft(), 0, -corner),
            (rect.bottomRight(), -corner, 0),
            (rect.bottomRight(), 0, -corner),
        ):
            p.drawLine(start, start + QPointF(dx, dy))
        check_origin = QPointF(rect.right() - 18, rect.top() + 12)
        p.setPen(QPen(accent, 1.8))
        p.drawLine(check_origin + QPointF(-7, 0), check_origin + QPointF(-2, 5))
        p.drawLine(check_origin + QPointF(-2, 5), check_origin + QPointF(8, -7))
        self.draw_pill(p, QPointF(rect.left(), rect.top() - 27), self.target_overlay_label(target), main)
        p.restore()

    def draw_minimal_bracket_lock(self, p: QPainter, rect: QRectF, target: VisionTarget, locked: bool) -> None:
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
            return self.armor_esp_points(armor.back_points, result, width, height)
        front = self.armor_esp_points(armor.points, result, width, height)
        if len(front) != 4:
            return []
        center = QPolygonF(front).boundingRect().center()
        thickness = max(5.0, min(32.0, QPolygonF(front).boundingRect().width() * depth))
        angle = armor.yaw_rad
        offset = QPointF(-math.sin(angle) * thickness, math.cos(angle) * thickness * 0.34)
        return [center + (point - center) * 0.90 + offset for point in front]

    def draw_3d_esp_box(self, p: QPainter, front: list[QPointF], back: list[QPointF], c: QColor, line_width: float, *, glass: bool, ghost: bool, label: str | None = None) -> None:
        if len(front) != 4 or len(back) != 4:
            return
        front_poly, back_poly = QPolygonF(front), QPolygonF(back)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dash = Qt.PenStyle.DashLine if ghost else Qt.PenStyle.SolidLine
        if glass and not ghost:
            # Glass 仅保留两块最可见侧面，避免四个平面都叠在一起发脏。
            for index in (0, 1):
                following = (index + 1) % 4
                side = QPolygonF([front[index], front[following], back[following], back[index]])
                gradient = QLinearGradient(side.boundingRect().topLeft(), side.boundingRect().bottomRight())
                gradient.setColorAt(0.0, with_alpha(c, 0.035))
                gradient.setColorAt(1.0, with_alpha(c, 0.15))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(gradient))
                p.drawPolygon(side)
            gradient = QLinearGradient(front_poly.boundingRect().topLeft(), front_poly.boundingRect().bottomRight())
            gradient.setColorAt(0.0, with_alpha(c, 0.12))
            gradient.setColorAt(1.0, with_alpha(c, 0.015))
            p.setBrush(QBrush(gradient))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(front_poly)

        # LiquidBounce/OpenZen 的线框层级：弱背框、少量深度棱、强前角标。
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(with_alpha(c, 0.22 if ghost else 0.34), max(0.75, line_width * 0.55), dash, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolygon(back_poly)
        p.setPen(QPen(with_alpha(c, 0.42 if ghost else 0.56), max(0.75, line_width * 0.65), dash, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for index in (0, 2):
            p.drawLine(front[index], back[index])
        p.setPen(QPen(c, max(0.9, line_width * (0.70 if ghost else 0.82)), dash, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if ghost:
            p.drawPolygon(front_poly)
        else:
            # 实体板只留四个角标，不再把一整张前脸描两遍。
            for index, point in enumerate(front):
                previous = front[(index - 1) % 4]
                following = front[(index + 1) % 4]
                for neighbor in (previous, following):
                    vector = neighbor - point
                    length = math.hypot(vector.x(), vector.y())
                    if length <= 0.01:
                        continue
                    segment = min(10.0, max(3.0, length * 0.22)) / length
                    p.drawLine(point, point + QPointF(vector.x() * segment, vector.y() * segment))
        p.restore()
        if label:
            box = front_poly.boundingRect()
            if ghost:
                self.draw_text(p, QRectF(box.left(), box.top() - 16, 44, 13), label, self.font("poppins_medium", 7.0), c)
            else:
                self.draw_pill(p, QPointF(box.left(), box.top() - 22), label, c)

    def draw_armor_quad(self, p: QPainter, points: list[QPointF], c: QColor, line_width: float, *, dashed: bool = False, aim: bool = False) -> None:
        if len(points) != 4:
            return
        polygon = QPolygonF(points)
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color("#000000", 0.72), line_width + 2.0, Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolygon(polygon)
        p.setPen(QPen(c, line_width, Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolygon(polygon)
        if aim:
            center = polygon.boundingRect().center()
            p.setPen(QPen(c, max(1.0, line_width), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(center + QPointF(-7, 0), center + QPointF(7, 0))
            p.drawLine(center + QPointF(0, -7), center + QPointF(0, 7))
        p.restore()

    def draw_armor_3d_esp(self, p: QPainter, width: int, height: int, result: VisionResult, module: Module) -> None:
        overlay = self.registry.get("RobotOverlay")
        overlay_style = self.setting_mode(overlay, "ESP Style", "3D Wire")
        style = self.setting_mode(module, "Style", "Wireframe3D")
        line_width = self.get_setting_number(module, "Line Width", 2.0)
        opacity = self.get_setting_number(module, "Opacity", 0.82)
        chessboard_ok = self.vision_result_calibrated(result)
        multi_ok = self.vision_can_show_multi_plate(result)
        local_pnp = bool(self._vision_snapshot and self._vision_snapshot.source == "Local") and any(
            len(armor.back_points) == 4 for armor in result.detections + result.predicted_armors
        )
        show_prediction = (
            self.setting_bool(module, "Show Prediction", True)
            and self.setting_bool(overlay, "Predict Plates", True)
            and multi_ok
        )
        show_labels = self.setting_bool(module, "Show Labels", True)
        depth = self.get_setting_number(overlay, "3D Depth", 0.22)
        max_targets = int(self.get_setting_number(overlay, "Max Targets", 6))
        armor_count = int(self.get_setting_number(overlay, "Armor Count", 4))
        radius_m = float(self.get_setting_number(overlay, "Radius M", 0.23))
        horizon_s = float(self.get_setting_number(overlay, "Predict Horizon", 0.06))
        only_secondary = self.setting_bool(overlay, "Only Secondary", True)
        primary = color("#ffffff", opacity)
        aim_color = self.hud_accent()

        # 确保 3D ESP + 四板：外挂未给全时用同济几何 MultiArmor 扩板
        draw_result = result
        if show_prediction and len(result.predicted_armors) < max(1, armor_count - 1):
            draw_result = expand_multi_plate_esp(
                result, armor_count=armor_count, radius_m=radius_m, horizon_s=horizon_s
            )

        observed = [armor for armor in draw_result.detections if len(armor.points) == 4]
        predicted = [armor for armor in draw_result.predicted_armors if len(armor.points) == 4]
        aim = draw_result.aim_armor if draw_result.aim_armor and len(draw_result.aim_armor.points) == 4 else None
        # 兼容旧变量名（下方 Zen/uncalibrated 角标）
        calibrated = multi_ok
        result = draw_result

        def prediction_alpha(armor) -> float:
            """按桥接器回传的真实深度衰减预测线框；旧桥接器无深度时保持默认透明度。"""
            depth = armor.depth_m if armor.depth_m > 0 else armor.distance_m
            if depth <= 0:
                return 0.72
            return max(0.28, min(0.86, 7.5 / max(1.0, depth)))

        if overlay_style == "Zen Outline" or style == "Zen2D":
            source = observed if observed or not calibrated else predicted
            for armor in source:
                points = self.armor_esp_points(armor.points, result, width, height)
                xs = [point.x() for point in points]
                ys = [point.y() for point in points]
                rect = QRectF(min(xs), min(ys), max(4.0, max(xs) - min(xs)), max(4.0, max(ys) - min(ys)))
                c = aim_color if armor.aim else primary
                self.draw_minimal_bracket_lock(p, rect, VisionTarget(rect.x(), rect.y(), rect.width(), rect.height(), armor.confidence, armor.label), armor.aim)
                if show_labels:
                    self.draw_pill(p, QPointF(rect.left(), rect.top() - 22), f"{armor.label.upper()} {armor.confidence:.0%}", c)
            return

        centers: list[QPointF] = []
        glass = overlay_style == "3D Glass"
        for armor in (observed + (predicted if show_prediction else []))[:max_targets]:
            front = self.armor_esp_points(armor.points, result, width, height)
            if len(front) != 4:
                continue
            box = QPolygonF(front).boundingRect()
            if aim is not None and not armor.predicted:
                aim_box = QPolygonF(self.armor_esp_points(aim.points, result, width, height)).boundingRect()
                if math.dist((box.center().x(), box.center().y()), (aim_box.center().x(), aim_box.center().y())) < max(12.0, aim_box.width() * 0.45):
                    # 主目标由下方的 Aim Face 单独绘制，不能再作为 RobotOverlay 重复画一次。
                    continue
            ghost = armor.predicted
            display_color = color("#3dff9a", prediction_alpha(armor)) if ghost else primary
            back = self.armor_esp_back_points(armor, result, width, height, depth)
            self.draw_3d_esp_box(p, front, back, display_color, line_width, glass=glass, ghost=ghost, label=(f"P{armor.plate_id}" if ghost else f"{armor.label.upper()} {armor.distance_m:.1f}m") if show_labels else None)
            if ghost:
                centers.append(box.center())

        if len(centers) >= 2:
            p.save()
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color("#000000", 0.62), line_width + 1.8))
            p.drawPolyline(QPolygonF(centers + [centers[0]]))
            p.setPen(QPen(with_alpha(aim_color, 0.56), max(1.0, line_width * 0.7), Qt.PenStyle.DashLine))
            p.drawPolyline(QPolygonF(centers + [centers[0]]))
            p.restore()
        if aim is not None:
            points = self.armor_esp_points(aim.points, result, width, height)
            back = self.armor_esp_back_points(aim, result, width, height, depth)
            self.draw_3d_esp_box(p, points, back, aim_color, line_width + 0.35, glass=glass, ghost=False, label=None)
            if show_labels:
                box = QPolygonF(points).boundingRect()
                self.draw_pill(p, QPointF(box.left(), box.top() - 24), f"AIM {result.aim_yaw_deg:+.1f}° {result.aim_pitch_deg:+.1f}°", aim_color)
        if local_pnp and not chessboard_ok:
            self.draw_pill(p, QPointF(20, height - 132), "3D ESP + 4-PLATE | LOCAL PNP", self.hud_accent())
        elif multi_ok and not chessboard_ok:
            n_pred = len(predicted)
            self.draw_pill(
                p,
                QPointF(20, height - 132),
                f"3D ESP + 4-PLATE | TONGJI n={1 + n_pred}",
                self.hud_accent(),
            )
        elif not multi_ok:
            self.draw_pill(p, QPointF(20, height - 132), "UNCALIBRATED | detection only", self.hud_warning())

    def draw_crosshair(self, p: QPainter, width: int, height: int) -> None:
        module = self.registry.get("Crosshair")
        size_setting = module.get_setting("Size") if module else None
        size = float(size_setting.value if size_setting else 18)
        cx = width / 2
        cy = height / 2
        heat_ratio = self.state.current_heat / max(1.0, self.state.max_heat)
        main = self.hud_state_color("heat", heat_ratio)
        p.setPen(QPen(main, 1.7))
        p.setBrush(main)
        p.drawEllipse(QPointF(cx, cy), 2.4, 2.4)
        p.drawLine(QPointF(cx - size, cy), QPointF(cx - 6, cy))
        p.drawLine(QPointF(cx + 6, cy), QPointF(cx + size, cy))
        p.drawLine(QPointF(cx, cy - size), QPointF(cx, cy - 6))
        p.drawLine(QPointF(cx, cy + 6), QPointF(cx, cy + size))

    def draw_chassis_gimbal_indicator(self, p: QPainter, width: int, height: int) -> None:
        module = self.registry.get("ChassisGimbalIndicator")
        if module is None or not module.enabled:
            return
        style_setting = module.get_setting("Style")
        style = str(style_setting.value) if style_setting else "Minimal"
        size = self.get_setting_number(module, "Size", 74.0)
        smooth = max(0.05, min(1.0, self.get_setting_number(module, "Smooth", 0.35)))
        show_angle = self.setting_bool(module, "Show Angle", True)
        show_mode = self.setting_bool(module, "Show Mode", True)
        chassis_yaw = self.animated_angle("hud.chassis_gimbal.chassis", self.state.chassis_yaw_deg, smooth)
        gimbal_yaw = self.animated_angle("hud.chassis_gimbal.gimbal", self.state.gimbal_yaw_deg, smooth)
        relative_yaw = normalize_angle_deg(gimbal_yaw - chassis_yaw)
        spin = bool(self.registry.get("SpinMode") and self.registry.get("SpinMode").enabled)
        mode_text = "SPIN" if spin else self.chassis_follow_mode()
        alert = min(1.0, abs(relative_yaw) / 90.0)
        pulse = 0.5 + 0.5 * math.sin(time.perf_counter() * 4.0)
        micro = self.hud_density() == "CombatCompact"

        if micro:
            size = min(size, 56.0)
            center = QPointF(width / 2.0, height - 98.0)
            active = self.anim.value("hud.chassis_gimbal.micro", 1.0, self.anim_duration(0.20), Easings.ease_out_pow4, initial=0.0)
            p.save()
            p.setOpacity(active)
            self.draw_chassis_gimbal_compass_base(p, center, size * 0.43, style, spin, pulse)
            self.draw_chassis_body_direction(p, center, size, chassis_yaw, style, spin, pulse)
            self.draw_gimbal_turret_direction(p, center, size, gimbal_yaw, alert)
            if show_angle or alert > 0.66:
                self.draw_text(p, QRectF(center.x() - 36, center.y() + size * 0.42, 72, 13), f"{relative_yaw:+.0f}°", self.font("poppins_medium", 7.2), self.hud_accent() if alert < 0.66 else self.hud_warning(), Qt.AlignmentFlag.AlignCenter)
            if show_mode:
                self.draw_text(p, QRectF(center.x() - 36, center.y() - size * 0.62, 72, 11), mode_text, self.font("poppins_medium", 6.5), THEME.muted, Qt.AlignmentFlag.AlignCenter)
            p.restore()
            return

        panel_w = max(142.0, size * 2.05)
        panel_h = size + 42.0
        x = width / 2.0 - panel_w / 2.0
        y = max(112.0, height - 64.0 - panel_h - 18.0)
        rect = QRectF(x, y, panel_w, panel_h)
        active = self.anim.value("hud.chassis_gimbal.panel", 1.0, self.anim_duration(0.24), Easings.ease_out_pow4, initial=0.0)

        p.save()
        p.setOpacity(active)
        if self.hud_clean():
            self.draw_hud_liquid_glass(p, rect, 9, surface="MainGlass")
        else:
            glow_rect(p, rect, 10, 0.06 * alert)
            rounded_rect(p, rect, 9, self.hud_surface(0.64), self.hud_panel_outline(0.08 + 0.08 * alert))
        top_line = QLinearGradient(rect.left() + 14, rect.top() + 5, rect.right() - 14, rect.top() + 5)
        top_line.setColorAt(0.0, with_alpha(self.hud_accent(), 0.25 + 0.30 * alert))
        top_line.setColorAt(1.0, with_alpha(self.hud_warning() if alert > 0.55 else self.hud_accent(), 0.18 + 0.34 * alert))
        p.setPen(QPen(QBrush(top_line), 1.6))
        p.drawLine(QPointF(rect.left() + 16, rect.top() + 5), QPointF(rect.right() - 16, rect.top() + 5))

        center = QPointF(rect.center().x(), rect.top() + 18.0 + size * 0.48)
        radius = size * 0.43
        self.draw_chassis_gimbal_compass_base(p, center, radius, style, spin, pulse)
        self.draw_chassis_body_direction(p, center, size, chassis_yaw, style, spin, pulse)
        self.draw_gimbal_turret_direction(p, center, size, gimbal_yaw, alert)

        if show_angle:
            angle_text = f"REL {relative_yaw:+.0f}°"
            self.draw_text(p, QRectF(rect.left() + 11, rect.bottom() - 24, rect.width() * 0.56, 16), angle_text, self.font("poppins_medium", 7.6), THEME.text)
        if show_mode:
            self.draw_text(p, QRectF(rect.right() - 64, rect.bottom() - 24, 52, 16), mode_text, self.font("poppins_medium", 7.4), self.hud_warning() if spin else THEME.muted, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p.restore()

    def draw_chassis_gimbal_compass_base(self, p: QPainter, center: QPointF, radius: float, style: str, spin: bool, pulse: float) -> None:
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color("#ffffff", 0.10), 1.0))
        p.drawEllipse(center, radius, radius)
        if style == "Compass":
            for index, angle in enumerate(range(0, 360, 45)):
                outer = self.direction_point(center, angle, radius + 2.0)
                inner = self.direction_point(center, angle, radius - (8.0 if index % 2 == 0 else 5.0))
                p.setPen(QPen(color("#ffffff", 0.30 if index % 2 == 0 else 0.16), 1.1))
                p.drawLine(inner, outer)
            self.draw_text(p, QRectF(center.x() - 8, center.y() - radius - 17, 16, 12), "N", self.font("poppins_bold", 6.8), THEME.muted, Qt.AlignmentFlag.AlignCenter)
        if spin:
            spin_radius = radius + 7.0 + 2.0 * pulse
            p.setPen(QPen(with_alpha(self.hud_warning(), 0.30 + 0.22 * pulse), 1.2))
            p.drawEllipse(center, spin_radius, spin_radius)
        p.restore()

    def draw_chassis_body_direction(self, p: QPainter, center: QPointF, size: float, yaw_deg: float, style: str, spin: bool, pulse: float) -> None:
        p.save()
        p.translate(center)
        p.rotate(yaw_deg)
        body_color = color("#ffffff", 0.22 if style == "Minimal" else 0.18)
        outline = color("#ffffff", 0.54)
        accent = with_alpha(self.hud_warning(), 0.34 + 0.28 * pulse) if spin else color("#ffffff", 0.22)
        if style == "Tank":
            track_w = size * 0.13
            track_h = size * 0.62
            rounded_rect(p, QRectF(-size * 0.31, -track_h / 2, track_w, track_h), 4, color("#ffffff", 0.07), color("#ffffff", 0.18))
            rounded_rect(p, QRectF(size * 0.18, -track_h / 2, track_w, track_h), 4, color("#ffffff", 0.07), color("#ffffff", 0.18))
            rounded_rect(p, QRectF(-size * 0.21, -size * 0.34, size * 0.42, size * 0.68), 5, body_color, outline, 1.2)
        else:
            rounded_rect(p, QRectF(-size * 0.24, -size * 0.32, size * 0.48, size * 0.64), 5, body_color, outline, 1.1)
        p.setPen(QPen(accent, 2.0 if spin else 1.3))
        p.drawLine(QPointF(0, -size * 0.34), QPointF(0, -size * 0.54))
        p.drawLine(QPointF(-size * 0.12, -size * 0.42), QPointF(0, -size * 0.54))
        p.drawLine(QPointF(size * 0.12, -size * 0.42), QPointF(0, -size * 0.54))
        p.restore()

    def draw_gimbal_turret_direction(self, p: QPainter, center: QPointF, size: float, yaw_deg: float, alert: float) -> None:
        p.save()
        p.translate(center)
        p.rotate(yaw_deg)
        turret = with_alpha(self.hud_accent(), 0.76 + 0.20 * (1.0 - alert))
        if alert > 0.55:
            turret = with_alpha(self.hud_warning(), 0.68 + 0.24 * alert)
        p.setPen(QPen(turret, 2.3))
        p.drawLine(QPointF(0, 0), QPointF(0, -size * 0.60))
        p.drawLine(QPointF(0, -size * 0.60), QPointF(-size * 0.07, -size * 0.48))
        p.drawLine(QPointF(0, -size * 0.60), QPointF(size * 0.07, -size * 0.48))
        p.setBrush(color("#050507", 0.86))
        p.drawEllipse(QPointF(0, 0), size * 0.105, size * 0.105)
        p.setBrush(turret)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(0, 0), size * 0.045, size * 0.045)
        p.restore()

    def direction_point(self, center: QPointF, angle_deg: float, radius: float) -> QPointF:
        rad = math.radians(angle_deg - 90.0)
        return QPointF(center.x() + math.cos(rad) * radius, center.y() + math.sin(rad) * radius)

    def animated_angle(self, key: str, target: float, smooth: float) -> float:
        target = normalize_angle_deg(target)
        current = self.hud_angle_values.get(key)
        if current is None:
            self.hud_angle_values[key] = target
            return target
        delta = normalize_angle_deg(target - current)
        alpha = max(0.05, min(0.55, 0.44 * (1.05 - smooth)))
        current = normalize_angle_deg(current + delta * alpha)
        self.hud_angle_values[key] = current
        return current

    def chassis_follow_mode(self) -> str:
        chassis = self.registry.get("ChassisAssist")
        setting = chassis.get_setting("Follow") if chassis else None
        value = str(setting.value).upper() if setting else "FOLLOW"
        if value == "GIMBAL":
            return "FOLLOW"
        if value == "FIELD":
            return "FIELD"
        return "FREE"

    def draw_dynamic_island(self, p: QPainter, width: int) -> None:
        payload = self.dynamic_island_payload()
        title_font = self.font("poppins_bold", 9.4)
        desc_font = self.font("poppins_regular", 8.2)
        p.setFont(title_font)
        title_w = QFontMetricsF(title_font).horizontalAdvance(payload["title"])
        p.setFont(desc_font)
        desc_w = QFontMetricsF(desc_font).horizontalAdvance(payload["description"])
        content_active = bool(payload["active"])
        target_h = 46.0 if content_active else 30.0
        target_w = max(210.0, min(580.0, max(title_w, desc_w) + (66.0 if content_active else 76.0)))
        if not content_active:
            target_w = max(300.0, min(580.0, title_w + desc_w + 92.0))

        island_w = self.dynamic_smooth("hud.dynamic_island.w", target_w, 0.20)
        island_h = self.dynamic_smooth("hud.dynamic_island.h", target_h, 0.20)
        island_x = self.dynamic_smooth("hud.dynamic_island.x", (width - island_w) / 2.0, 0.20)
        rect = QRectF(island_x, 17.0, island_w, island_h)
        radius = min(14.5, rect.height() / 2.0)
        active = self.anim.value("hud.dynamic_island.visible", 1.0, self.anim_duration(0.24), Easings.ease_out_expo, initial=0.0)
        icon_v = self.anim.value(
            "hud.dynamic_island.icon",
            1.0 if content_active else 0.0,
            self.anim_duration(0.30 if content_active else 0.20),
            Easings.ease_out_expo,
        )

        p.save()
        p.setOpacity(active)
        if self.hud_clean():
            self.draw_hud_liquid_glass(p, rect, radius, surface="ProminentGlass")
        else:
            for pad, alpha in ((18.0, 0.045), (10.0, 0.070)):
                rounded_rect(p, rect.adjusted(-pad, -pad, pad, pad), radius + pad, color("#000000", alpha), None)
            rounded_rect(p, rect, radius, self.hud_surface(0.62), self.hud_panel_outline(0.08))
        p.setClipRect(rect.adjusted(0.6, 0.6, -0.6, -0.6))

        icon_size = 24.0 if content_active else 21.0
        icon_rect = QRectF(rect.left() + 7.0, rect.top() + (rect.height() - icon_size) / 2.0, icon_size, icon_size)
        self.draw_dynamic_island_icon(p, icon_rect, str(payload["kind"]), payload["color"], max(0.18, icon_v if content_active else 0.72))

        if content_active:
            self.draw_glass_text(p, QRectF(rect.left() + 39.0, rect.top() + 7.0, rect.width() - 50.0, 16.0), payload["title"], title_font, THEME.text)
            self.draw_glass_text(p, QRectF(rect.left() + 39.0, rect.top() + 22.0, rect.width() - 50.0, 15.0), payload["description"], desc_font, THEME.muted)
            progress = self.anim.value(
                "hud.dynamic_island.progress",
                max(0.0, min(1.0, float(payload["progress"]))),
                self.anim_duration(0.18),
                Easings.ease_out_expo,
                initial=max(0.0, min(1.0, float(payload["progress"]))),
            )
            bar_bg = QRectF(rect.left() + 8.0, rect.bottom() - 7.0, rect.width() - 16.0, 3.0)
            rounded_rect(p, bar_bg, 1.5, color("#ffffff", 0.12))
            filled = QRectF(bar_bg)
            filled.setWidth(max(4.0, bar_bg.width() * progress))
            rounded_rect(p, filled, 1.5, with_alpha(payload["color"], 0.95))
        else:
            logo_grad = QLinearGradient(icon_rect.left(), icon_rect.top(), icon_rect.right(), icon_rect.bottom())
            logo_grad.setColorAt(0.0, with_alpha(self.hud_accent(), 0.70))
            logo_grad.setColorAt(1.0, with_alpha(self.hud_accent(), 0.95))
            p.setPen(QPen(QBrush(logo_grad), 1.4))
            p.drawLine(QPointF(rect.left() + 38.0, rect.top() + 8.0), QPointF(rect.left() + 38.0, rect.bottom() - 8.0))
            self.draw_glass_text(p, QRectF(rect.left() + 46.0, rect.top(), 88.0, rect.height()), payload["title"], title_font, THEME.text)
            self.draw_glass_text(p, QRectF(rect.left() + 126.0, rect.top(), rect.width() - 134.0, rect.height()), payload["description"], desc_font, THEME.muted)
        p.restore()

    def dynamic_smooth(self, key: str, target: float, speed: float) -> float:
        current = self.dynamic_smooth_values.get(key, target)
        step_speed = abs(target - current) * speed
        delta_factor = max(1.0, min(50.0, self.frame_dt * 1000.0)) / 16.0
        smoothing = max(step_speed * delta_factor, 0.15)
        difference = current - target
        if difference > step_speed:
            current = max(current - smoothing, target)
        elif difference < -step_speed:
            current = min(current + smoothing, target)
        else:
            current = target
        self.dynamic_smooth_values[key] = current
        return current

    def dynamic_island_payload(self) -> dict[str, Any]:
        now = time.perf_counter()
        if self.state.last_event != self.dynamic_island_last_event:
            self.dynamic_island_last_event = self.state.last_event
            self.dynamic_island_event_started = now

        recent_toast = None
        for toast in reversed(self.toasts):
            age = now - float(toast["created"])
            if age < 3.0:
                recent_toast = toast
                break

        if recent_toast is not None:
            age = now - float(recent_toast["created"])
            message = str(recent_toast["message"])
            kind, main = self.dynamic_message_kind(message)
            return {
                "active": True,
                "kind": kind,
                "color": main,
                "title": self.dynamic_message_title(message, kind),
                "description": message,
                "progress": max(0.0, 1.0 - age / 3.0),
            }

        event_age = now - self.dynamic_island_event_started
        if self.state.last_event and event_age < 2.7:
            kind, main = self.dynamic_message_kind(self.state.last_event)
            duration = max(1.0, float(getattr(self.state, "match_duration_sec", 300)))
            return {
                "active": True,
                "kind": kind,
                "color": main,
                "title": self.match_mode_manager.current.title,
                "description": self.state.last_event,
                "progress": max(0.0, min(1.0, self.state.stage_countdown_sec / duration)),
            }

        mqtt = "MQTT live" if self.state.mqtt_connected else "MQTT mock"
        serial = "SERIAL live" if self.state.serial_connected else "SERIAL mock"
        text = f"{self.scenario_manager.current.title} | {self.match_mode_manager.current.title} | {self.state.fps:.0f} FPS | {mqtt} | {serial}"
        return {"active": False, "kind": "info", "color": self.hud_accent(), "title": "POLAR-RM", "description": text, "progress": 1.0}

    def dynamic_message_kind(self, message: str) -> tuple[str, QColor]:
        lower = message.lower()
        if any(token in lower for token in ("fail", "error", "disabled", "closed")) or any(token in message for token in ("失败", "关闭", "错误")):
            return "warning", self.hud_danger()
        if any(token in lower for token in ("enabled", "ready", "saved", "loaded", "success")) or any(token in message for token in ("启用", "就绪", "保存", "加载", "打开")):
            return "success", self.hud_success()
        if "gui" in lower or "模式" in message or "Scenario" in message or "Match" in message:
            return "module", self.hud_accent()
        return "info", self.hud_accent()

    def dynamic_message_title(self, message: str, kind: str) -> str:
        if kind == "warning":
            return "Warning"
        if kind == "success":
            return "Success"
        if kind == "module":
            return "Client State"
        if "Radar" in message or "雷达" in message:
            return "Radar"
        return "POLAR-RM"

    def draw_dynamic_island_icon(self, p: QPainter, rect: QRectF, kind: str, main: QColor, amount: float) -> None:
        amount = max(0.0, min(1.0, amount))
        rounded_rect(p, rect, 7.0, with_alpha(main, 0.64), color("#ffffff", 0.10))
        inner = rect.adjusted(2.0, 2.0, -2.0, -2.0)
        if kind == "module":
            rounded_rect(p, inner.adjusted(0.0, inner.height() * 0.25, 0.0, -inner.height() * 0.25), inner.height() * 0.25, color("#1e2028", 0.88))
            knob = inner.height() * 0.42
            kx = inner.left() + 2.0 + (inner.width() - knob - 4.0) * amount
            rounded_rect(p, QRectF(kx, inner.center().y() - knob / 2.0, knob, knob), knob / 2.0, color("#050507", 0.94))
            return
        if kind == "success":
            clip = QRectF(rect.left(), rect.top(), rect.width() * amount, rect.height())
            p.save()
            p.setClipRect(clip)
            p.setPen(QPen(color("#ffffff", 0.92), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(rect.left() + rect.width() * 0.28, rect.center().y()), QPointF(rect.left() + rect.width() * 0.43, rect.bottom() - rect.height() * 0.30))
            p.drawLine(QPointF(rect.left() + rect.width() * 0.43, rect.bottom() - rect.height() * 0.30), QPointF(rect.right() - rect.width() * 0.22, rect.top() + rect.height() * 0.30))
            p.restore()
            return
        if kind == "warning":
            clip = QRectF(rect.left(), rect.bottom() - rect.height() * amount, rect.width(), rect.height() * amount)
            p.save()
            p.setClipRect(clip)
            p.setPen(QPen(color("#ffffff", 0.92), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(rect.center().x(), rect.top() + 5.0), QPointF(rect.center().x(), rect.bottom() - 8.0))
            p.drawPoint(QPointF(rect.center().x(), rect.bottom() - 4.6))
            p.restore()
            return
        p.setPen(QPen(color("#ffffff", 0.92), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        v = inner.height() * 0.72 * amount
        h = inner.width() * 0.72 * amount
        p.drawLine(QPointF(inner.center().x(), inner.center().y() - v / 2.0), QPointF(inner.center().x(), inner.center().y() + v / 2.0))
        p.drawLine(QPointF(inner.center().x() - h / 2.0, inner.center().y()), QPointF(inner.center().x() + h / 2.0, inner.center().y()))

    def draw_right_hud_mode(self, p: QPainter, width: int, height: int, mode: str, target: VisionTarget) -> None:
        hud = self.registry.get("HUD")
        if mode == "ModuleListHud" and self.setting_bool(hud, "Module List", True):
            self.draw_module_list_hud(p, width)
        elif mode == "TacticalMapHud":
            if self.setting_bool(hud, "Module List", True):
                self.draw_module_list_hud(p, width)
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
        if mode != "KeyBindsHud" and not right_list_active and self.setting_bool(hud, "KeyBinds", True):
            self.draw_keybinds_strip(p, width, height)

    def draw_module_list_for_tactical_map(self, p: QPainter, width: int, height: int) -> None:
        map_rect = self.tactical_map_rect(width, height)
        below_start = map_rect.bottom() + 14
        if height - below_start >= 180:
            self.draw_module_list_hud(p, width, start_y=below_start)
            return
        left_space_right_edge = int(map_rect.left() - 12)
        if left_space_right_edge >= 420:
            self.draw_module_list_hud(p, left_space_right_edge)
            return
        self.draw_module_list_hud(p, width, start_y=below_start)

    def draw_match_header(self, p: QPainter, width: int) -> None:
        config = self.match_mode_manager.current
        text = f"{config.title}  {self.state.current_stage}  {self.format_time(self.state.stage_countdown_sec)}"
        if config.key == "RMUL3v3":
            text += f"  VP {self.state.ally_victory_points}:{self.state.enemy_victory_points}"
        else:
            text += f"  SCORE {self.state.red_score}:{self.state.blue_score}"
        font = self.font("poppins_medium", 8.6)
        p.setFont(font)
        rect_w = max(280.0, min(520.0, QFontMetricsF(font).horizontalAdvance(text) + 44.0))
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
        draw_bar(p, line, progress, self.hud_accent(), color("#ffffff", 0.10), 0.6)
        self.draw_glass_text(p, rect.adjusted(14, 0, -14, 0), text, font, THEME.text, Qt.AlignmentFlag.AlignCenter)
        p.restore()

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
        active = self.anim.value("hud.tactical_map", 1.0, self.anim_duration(0.30), Easings.ease_out_expo, initial=0.0)
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
        line_reveal = self.anim.value("hud.tactical_map.line_reveal", 1.0, self.anim_duration(0.42), Easings.ease_out_expo, initial=0.0)
        p.fillRect(QRectF(rect.left() + 10, rect.top() + 4, (rect.width() - 20) * line_reveal, 1.4 if minimal else 2.0 if thin else 2.3), top_line)
        title = f"{config.title} Tactical Map"
        compact = minimal or rect.height() < 220.0
        status_kind = self.tactical_map_status_kind(minimal)
        header_h = 8.0 if minimal else 24.0 if thin else 30.0 if compact else 44.0
        footer_h = self.tactical_map_footer_height(status_kind, minimal, thin, compact)
        detail_alpha = self.anim.value("hud.tactical_map.detail_alpha", 0.0 if minimal else 1.0, self.anim_duration(0.20), Easings.ease_out_expo)
        p.save()
        p.setClipRect(rect.adjusted(1.0, 1.0, -1.0, -1.0))
        if not minimal:
            p.save()
            p.setOpacity(p.opacity() * detail_alpha)
            self.draw_text(p, QRectF(rect.left() + 12, rect.top() + 8, rect.width() - 24, 14), title, self.font("poppins_bold", 7.0 if compact else 8.4), THEME.text)
            p.restore()
        if not compact and not thin:
            p.save()
            p.setOpacity(p.opacity() * detail_alpha)
            self.draw_text(
                p,
                QRectF(rect.left() + 12, rect.top() + 27, rect.width() - 24, 14),
                f"{config.field.width_m:g}m x {config.field.height_m:g}m official field",
                self.font("poppins_regular", 7.2),
                THEME.muted,
            )
            p.restore()
        map_rect = rect.adjusted(6 if minimal else 8 if compact else 10, header_h, -6 if minimal else -8 if compact else -10, -footer_h)
        field_rect = self.draw_official_map(p, map_rect, config)
        map_content_opacity = self.anim.value("hud.tactical_map.content_alpha", 1.0, self.anim_duration(0.26), Easings.ease_out_expo, initial=0.0)
        p.save()
        p.setOpacity(p.opacity() * map_content_opacity)
        self.draw_map_zones(p, field_rect, config.field, compact=compact)
        if self.setting_bool(module, "Map Marks", True):
            self.draw_map_markers(p, field_rect, config.field, compact=compact)
        if self.setting_bool(module, "Robot Dots", True):
            self.draw_map_robots(p, field_rect, config.field, compact=compact)
        p.restore()
        if not compact:
            title_chip = QRectF(field_rect.left() + 6, field_rect.top() + 6, min(188.0, field_rect.width() - 12), 20)
            p.save()
            p.setOpacity(p.opacity() * detail_alpha)
            rounded_rect(p, title_chip, 6, self.hud_surface(0.72), self.hud_panel_outline(0.10))
            self.draw_text(p, title_chip.adjusted(7, 0, -7, 0), f"{config.title} official map", self.font("poppins_medium", 6.9), THEME.text)
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
        if density == "CombatCompact" and not self.tactical_map_hold:
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
        card_w = self.dynamic_smooth("hud.tactical_map.card_w", target_w, 0.18)
        card_h = self.dynamic_smooth("hud.tactical_map.card_h", target_h, 0.18)
        card_x = self.dynamic_smooth("hud.tactical_map.card_x", 12.0, 0.22)
        card_y = self.dynamic_smooth("hud.tactical_map.card_y", 58.0, 0.22)
        return QRectF(card_x, card_y, card_w, card_h)

    def tactical_map_hold_amount(self) -> float:
        return self.anim.value(
            "hud.tactical_map.hold_amount",
            1.0 if self.tactical_map_hold else 0.0,
            self.anim_duration(0.34),
            Easings.ease_out_expo,
            initial=1.0 if self.tactical_map_hold else 0.0,
        )

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

    def tactical_map_status_kind(self, minimal: bool) -> str:
        value = self.tactical_map_status_display()
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
        minimal = self.hud_density() == "CombatCompact" and hold_amount < 0.08
        compact = minimal or rect.height() < 220.0
        display = self.tactical_map_status_display()
        active = self.anim.value("hud.tactical_map.liquid", 1.0, self.anim_duration(0.24), Easings.ease_out_cubic, initial=0.0)
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
            rounded_rect(p, rect, radius, color("#000000", 0.72), color("#ffffff", 0.105 if minimal else 0.085), 0.9)
        left_line = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        left_line.setColorAt(0.0, with_alpha(self.hud_accent(), 0.70))
        left_line.setColorAt(1.0, with_alpha(self.hud_accent(), 0.28))
        p.fillRect(QRectF(rect.left(), rect.top() + 6.0, 2.0, rect.height() - 12.0), left_line)

        title = f"{config.title} Tactical Map"
        header_h = 13.0 if minimal else 22.0 if compact else 27.0
        footer_h = self.liquid_status_footer_height(display, minimal, compact)
        title_font = self.font("poppins_medium", 6.0 if minimal else 7.2 if compact else 8.0)
        title_rect = QRectF(rect.left() + 8.0, rect.top() + 2.0, rect.width() - 16.0, header_h - 2.0)
        self.draw_glass_text(p, title_rect, config.title if minimal else title, title_font, THEME.text)

        map_rect = rect.adjusted(6.0, header_h, -6.0, -footer_h)
        p.save()
        p.setClipRect(rect.adjusted(1.0, 1.0, -1.0, -1.0))
        field_rect = self.draw_official_map(p, map_rect, config)
        content_alpha = self.anim.value("hud.tactical_map.liquid.content", 1.0, self.anim_duration(0.20), Easings.ease_out_cubic, initial=0.0)
        p.save()
        p.setOpacity(p.opacity() * content_alpha)
        self.draw_map_zones(p, field_rect, config.field, compact=compact)
        if self.setting_bool(module, "Map Marks", True):
            self.draw_map_markers(p, field_rect, config.field, compact=compact)
        if self.setting_bool(module, "Robot Dots", True):
            self.draw_liquid_map_robots(p, field_rect, config.field, compact=compact)
        p.restore()
        p.restore()

        if display == "LegacyOrbit":
            self.draw_map_status_orbit_dock(p, rect, field_rect, width, minimal=minimal, compact=compact)
        elif display == "LegacyBars":
            stack_top = field_rect.bottom() + (3.0 if minimal else 6.0)
            stack_h = max(20.0 if compact else 34.0, min(54.0, rect.bottom() - 6.0 - stack_top))
            self.draw_match_status_stack(p, QRectF(field_rect.left(), stack_top, field_rect.width(), stack_h), compact=compact)
        elif display != "Off":
            self.draw_liquid_status_dock(p, rect, field_rect, width, display, minimal=minimal, compact=compact)
        p.restore()

    def official_map_fit_rect(self, rect: QRectF, config: MatchModeConfig) -> QRectF:
        image = self.official_map_image(config)
        if image is None or image.isNull():
            aspect = config.field.width_m / max(0.1, config.field.height_m)
        else:
            aspect = image.width() / max(1.0, image.height())
        return self.fit_aspect(rect, aspect)

    def draw_liquid_map_robots(self, p: QPainter, rect: QRectF, field: FieldLayout, compact: bool = False) -> None:
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
        font = self.font("poppins_medium", 5.8 if compact else 6.7)

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
                pulse_r = (7.0 if compact else 9.0) + (3.0 if compact else 4.6) * (0.5 + 0.5 * math.sin(now * 4.4 + index))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(with_alpha(base, 0.28 + 0.16 * math.sin(now * 3.2)), 1.0))
                p.drawEllipse(point, pulse_r, pulse_r)
            angle = math.degrees(math.atan2(point.y() - own_point.y(), point.x() - own_point.x())) + 90.0
            size = 5.0 if compact else 6.6
            self.draw_liquid_radar_pointer(p, point, angle, with_alpha(base, alpha), style, size, highlight)
            if show_labels and (not compact or highlight):
                label = str(robot.get("id", "?"))
                show_distance = show_values and (not compact or team == "enemy" or distance >= 1.0)
                value = f"{int(distance)}m" if show_distance else ""
                text = f"{label} {value}".strip()
                label_rect = QRectF(point.x() + 6.0, point.y() - 8.0, 44.0, 12.0)
                self.draw_text(p, label_rect.translated(0.5, 0.7), text, font, color("#000000", 0.72))
                self.draw_text(p, label_rect, text, font, THEME.text)
            if show_values:
                bar_w = 15.0 if compact else 21.0
                draw_bar(
                    p,
                    QRectF(point.x() - bar_w * 0.5, point.y() + (5.0 if compact else 7.0), bar_w, 2.0),
                    hp_ratio,
                    with_alpha(base, 0.82),
                    color("#ffffff", 0.12),
                    1.0,
                )
            tooltip = f"{robot.get('name', 'Robot')} {robot.get('id', '?')} | {hp}/{hp_max} HP | {distance:.1f}m"
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
        rounded_rect(p, rect, 4.0, color("#000000", 0.84 + 0.08 * hover), with_alpha(main, 0.34 + 0.24 * hover), 0.9)
        fill = QRectF(rect.left() + 2.0, rect.bottom() - 3.0, max(3.0, (rect.width() - 4.0) * fast_ratio), 2.2)
        rounded_rect(p, fill, 1.0, with_alpha(main, 0.78))
        font = self.font("poppins_medium", 5.7 if display != "LiquidCards" else 5.4)
        text = self.compact_map_status_label(label)
        if self.liquid_map_bool("Show Values", True):
            text = f"{text} {value}"
        self.draw_text(p, rect.adjusted(3.0, 0.0, -3.0, -1.0), text, font, THEME.text, Qt.AlignmentFlag.AlignCenter)
        self.add_hotspot(rect.adjusted(-2.0, -2.0, 2.0, 2.0), "map_status", key, tooltip=tooltip)

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
        return color("#ee4f59") if side == "Red" else color("#4e84f5")

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
        return color("#8d96a5")

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
    ) -> None:
        match_rows, telemetry_rows = self.map_status_groups()
        bottom_rows = match_rows[: 3 if minimal else 4]
        side_rows = telemetry_rows[: 2 if minimal else 4]
        if not bottom_rows and not side_rows:
            return
        liquid_surface = self.hud_clean()
        # Full：外壳 + 条目都有玻璃；Balanced：仅外壳；Lite：外壳轻玻璃
        quality = self.glass_quality()
        item_glass = liquid_surface and quality == "full"
        card_mode = display == "LiquidCards" or (display == "LiquidAuto" and not minimal)
        bottom_top = field_rect.bottom() + (3.0 if minimal else 5.0)
        bottom_rect = QRectF(rect.left() + 7.0, bottom_top, rect.width() - 14.0, max(22.0, rect.bottom() - bottom_top - 5.0))
        if bottom_rows:
            if liquid_surface:
                self.draw_hud_liquid_glass(p, bottom_rect, 5.0, surface="MainGlass")
            else:
                rounded_rect(p, bottom_rect, 5.0, color("#000000", 0.34 if minimal else 0.28), color("#ffffff", 0.055 if minimal else 0.035), 0.6)
            self.draw_liquid_status_row(p, bottom_rect, bottom_rows, card_mode=card_mode, vertical=False, minimal=minimal, dock_key="match", liquid_glass=item_glass)

        if not side_rows:
            return
        row_h = 18.0 if minimal else 24.0 if compact else 26.0
        side_gap = 4.0 if minimal else 5.0
        side_w = 74.0 if minimal else 104.0 if compact else 118.0
        side_h = len(side_rows) * row_h + max(0, len(side_rows) - 1) * side_gap
        side_x = rect.right() + (6.0 if minimal else 9.0)
        side_y = min(rect.bottom() - side_h, max(rect.top() + 14.0, field_rect.top()))
        safe_right = width - 218.0
        if side_x + side_w <= safe_right:
            side_rect = QRectF(side_x, side_y, side_w, side_h)
            shell = side_rect.adjusted(-4.0, -4.0, 4.0, 4.0)
            if liquid_surface:
                self.draw_hud_liquid_glass(p, shell, 6.0, surface="MainGlass")
            else:
                rounded_rect(p, shell, 6.0, color("#000000", 0.30), color("#ffffff", 0.040), 0.6)
            self.draw_liquid_status_row(p, side_rect, side_rows, card_mode=card_mode, vertical=True, minimal=minimal, dock_key="telemetry", liquid_glass=item_glass)
            return
        fold_rect = QRectF(rect.left() + 7.0, rect.bottom() + 5.0, min(rect.width() - 14.0, 250.0), row_h + 2.0)
        if liquid_surface:
            self.draw_hud_liquid_glass(p, fold_rect, 5.0, surface="MainGlass")
        else:
            rounded_rect(p, fold_rect, 5.0, color("#000000", 0.30), color("#ffffff", 0.040), 0.6)
        self.draw_liquid_status_row(p, fold_rect, side_rows, card_mode=card_mode, vertical=False, minimal=minimal, dock_key="telemetry_fold", liquid_glass=item_glass)

    def draw_liquid_status_row(
        self,
        p: QPainter,
        rect: QRectF,
        rows: list[tuple[str, str, float, QColor, str, str]],
        *,
        card_mode: bool,
        vertical: bool,
        minimal: bool,
        dock_key: str,
        liquid_glass: bool = False,
    ) -> None:
        if not rows:
            return
        gap = 4.0 if minimal else 5.0
        enter = self.anim.value(f"liquid.status.dock.{dock_key}", 1.0, self.anim_duration(0.20), Easings.ease_out_cubic, initial=0.0)
        p.save()
        p.setOpacity(p.opacity() * enter)
        if vertical:
            slot_h = (rect.height() - gap * (len(rows) - 1)) / len(rows)
            for index, row in enumerate(rows):
                slot = QRectF(rect.left(), rect.top() + index * (slot_h + gap), rect.width(), slot_h)
                self.draw_liquid_status_item(p, slot, row, card_mode=card_mode, vertical=True, minimal=minimal, dock_key=dock_key, index=index, liquid_glass=liquid_glass)
        else:
            slot_w = (rect.width() - gap * (len(rows) - 1)) / len(rows)
            for index, row in enumerate(rows):
                slot = QRectF(rect.left() + index * (slot_w + gap), rect.top(), slot_w, rect.height())
                self.draw_liquid_status_item(p, slot, row, card_mode=card_mode, vertical=False, minimal=minimal, dock_key=dock_key, index=index, liquid_glass=liquid_glass)
        p.restore()

    def draw_liquid_status_item(
        self,
        p: QPainter,
        rect: QRectF,
        row: tuple[str, str, float, QColor, str, str],
        *,
        card_mode: bool,
        vertical: bool,
        minimal: bool,
        dock_key: str,
        index: int,
        liquid_glass: bool = False,
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
        p.setOpacity(p.opacity() * enter)
        if animation == "Fly":
            item_rect = item_rect.translated((1.0 - enter) * (14.0 if vertical else 0.0), (1.0 - enter) * (8.0 if not vertical else 0.0))
        elif animation == "Flip":
            p.translate(item_rect.center())
            p.scale(0.92 + 0.08 * enter, 0.96 + 0.04 * enter)
            p.translate(-item_rect.center())
        if card_mode:
            self.draw_liquid_status_card(p, item_rect, label, value, ratio, c, key, tooltip, minimal=minimal, liquid_glass=liquid_glass)
        else:
            self.draw_liquid_status_bar(p, item_rect, label, value, ratio, c, key, tooltip, minimal=minimal, liquid_glass=liquid_glass)
        p.restore()

    def liquid_status_color(self, label: str, ratio: float, fallback: QColor) -> QColor:
        label = label.upper()
        ratio = max(0.0, min(1.0, ratio))
        if label in ("ENEMY", "ENMY", "EBASE") or (label in ("HEAT", "POWER", "PWR", "SPEED", "SPD") and ratio >= 0.92):
            return self.hud_danger()
        if label in ("GOLD", "OUT", "OUTPOST") or (label in ("HEAT", "POWER", "PWR", "SPEED", "SPD") and ratio >= 0.78):
            return self.hud_warning()
        if label in ("AMMO",):
            return self.hud_success()
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
            rounded_rect(p, bar_rect, 5.0, color("#000000", 0.68 + 0.10 * hover), with_alpha(main, 0.08 + 0.20 * hover), 0.8)
        lag_rect = QRectF(bar_rect.left(), bar_rect.top(), max(2.0, bar_rect.width() * lag_ratio), bar_rect.height())
        rounded_rect(p, lag_rect, 5.0, with_alpha(main, 0.16 + 0.06 * hover))
        fill_rect = QRectF(bar_rect.left(), bar_rect.top(), max(2.0, bar_rect.width() * fast_ratio), bar_rect.height())
        rounded_rect(p, fill_rect, 5.0, with_alpha(main, 0.58 + 0.12 * hover))
        label_font = self.font("poppins_medium", 5.4 if minimal else 6.4)
        value_font = self.font("poppins_medium", 5.5 if minimal else 6.5)
        short_label = self.compact_map_status_label(label) if minimal else label
        text_left = bar_rect.adjusted(5.0, 0.0, -5.0, 0.0)
        self.draw_text(p, text_left.translated(0.5, 0.7), short_label, label_font, color("#000000", 0.72))
        self.draw_text(p, text_left, short_label, label_font, THEME.text)
        if self.liquid_map_bool("Show Values", True):
            self.draw_text(p, text_left.translated(0.5, 0.7), value, value_font, color("#000000", 0.72), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.draw_text(p, text_left, value, value_font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.add_hotspot(bar_rect.adjusted(-3.0, -3.0, 3.0, 3.0), "map_status", key, tooltip=tooltip)

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
            rounded_rect(p, card_rect, 5.0, color("#000000", 0.68 + 0.10 * hover), with_alpha(main, 0.08 + 0.20 * hover), 0.8)
        p.fillRect(QRectF(card_rect.left(), card_rect.top() + 4.0, 2.0, card_rect.height() - 8.0), with_alpha(main, 0.82))
        label_font = self.font("poppins_medium", 5.2 if minimal else 6.1)
        value_font = self.font("poppins_bold", 5.5 if minimal else 6.7)
        label_text = self.compact_map_status_label(label) if minimal else label
        self.draw_text(p, card_rect.adjusted(6.0, 1.0, -5.0, -10.0), label_text, label_font, THEME.muted)
        if self.liquid_map_bool("Show Values", True):
            self.draw_text(p, card_rect.adjusted(6.0, 1.0, -5.0, -10.0), value, value_font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar = QRectF(card_rect.left() + 6.0, card_rect.bottom() - 5.0, card_rect.width() - 12.0, 2.0)
        draw_bar(p, bar, lag_ratio, with_alpha(main, 0.18), color("#ffffff", 0.10), 1.0)
        draw_bar(p, bar, fast_ratio, with_alpha(main, 0.78), color("#ffffff", 0.00), 1.0)
        self.add_hotspot(card_rect.adjusted(-3.0, -3.0, 3.0, 3.0), "map_status", key, tooltip=tooltip)

    def draw_official_map(self, p: QPainter, rect: QRectF, config: MatchModeConfig) -> QRectF:
        image = self.official_map_image(config)
        opacity_setting = self.registry.get("TacticalMap").get_setting("Opacity") if self.registry.get("TacticalMap") else None
        image_opacity = float(opacity_setting.value) if opacity_setting else 0.72
        rounded_rect(p, rect, 6, color("#050506", 0.72), color("#ffffff", 0.08))
        if image is None or image.isNull():
            self.draw_text(p, rect, "official map asset missing", self.font("poppins_regular", 8.0), self.hud_danger(), Qt.AlignmentFlag.AlignCenter)
            fit = self.fit_aspect(rect, config.field.width_m / max(0.1, config.field.height_m))
        else:
            fit = self.fit_aspect(rect, image.width() / max(1.0, image.height()))
            p.save()
            p.setOpacity(max(0.35, min(1.0, image_opacity)))
            p.drawImage(fit, image)
            p.restore()
            p.fillRect(fit, color("#000000", 0.34))
        rounded_rect(p, fit, 4, color("#000000", 0.0), with_alpha(self.hud_accent(), 0.22), 1.0)
        return fit

    def official_map_image(self, config: MatchModeConfig) -> QImage | None:
        path = self.workspace / "rm_custom_client" / config.field.asset_path
        key = str(path)
        if key not in self.map_images:
            self.map_images[key] = QImage(key)
        image = self.map_images[key]
        return None if image.isNull() else image

    def fit_aspect(self, rect: QRectF, aspect: float) -> QRectF:
        if rect.width() / max(1.0, rect.height()) > aspect:
            h = rect.height()
            w = h * aspect
        else:
            w = rect.width()
            h = w / max(0.1, aspect)
        return QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)

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

    def draw_map_zones(self, p: QPainter, rect: QRectF, field: FieldLayout, compact: bool = False) -> None:
        now = time.perf_counter()
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
            border_alpha = 0.06 if compact else 0.10
            fill_alpha = 0.40 + (0.20 if compact else 0.35) * pulse
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
            tooltip = f"{robot.get('name', 'Robot')} {robot.get('id', '?')} | {hp}/{hp_max} HP"
            self.add_hotspot(QRectF(point.x() - 10, point.y() - 10, 20, 20), "map_robot", robot.get("id"), tooltip=tooltip)

    def draw_map_markers(self, p: QPainter, rect: QRectF, field: FieldLayout, compact: bool = False) -> None:
        now = time.perf_counter()
        font = self.font("poppins_medium", 6.5)
        for index, marker in enumerate(self.state.map_markers):
            point = self.map_point(rect, field, float(marker.get("x", 0.0)), float(marker.get("y", 0.0)))
            pulse = bool(marker.get("pulse", False))
            kind = str(marker.get("kind", "marker"))
            c = self.hud_warning() if kind == "control" else self.hud_success() if kind == "supply" else self.hud_accent()
            if pulse:
                p.setPen(QPen(with_alpha(c, 0.44), 1.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                pulse_r = (5.5 if compact else 8.0) + (2.5 if compact else 4.0) * (0.5 + 0.5 * math.sin(now * 4.6))
                p.drawEllipse(point, pulse_r, pulse_r)
            p.setPen(QPen(c, 1.2))
            half = 4 if compact else 6
            p.drawLine(point + QPointF(-half, 0), point + QPointF(half, 0))
            p.drawLine(point + QPointF(0, -half), point + QPointF(0, half))
            if not compact:
                self.draw_text(p, QRectF(point.x() + 7, point.y() - 8, 48, 13), str(marker.get("label", "")), font, c)

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
        danger = label in ("HEAT", "POWER", "SPEED") and fast_ratio > 0.82
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
        if not lite:
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
        self.draw_text(p, value_rect.translated(0.6, 0.7), value, value_font, color("#000000", 0.72), Qt.AlignmentFlag.AlignCenter)
        self.draw_text(p, value_rect, value, value_font, THEME.text, Qt.AlignmentFlag.AlignCenter)
        p.restore()

        if show_label:
            self.draw_status_label_capsule(p, center, draw_r, diameter, label, tooltip, main, hover_v, label_side)

        self.add_hotspot(label_probe.adjusted(-4.0, -4.0, 8.0, 8.0), "map_status", key, tooltip=tooltip)

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
        self.draw_text(p, text_rect.translated(0.5, 0.6), text, label_font, color("#000000", 0.68), align)
        self.draw_text(p, text_rect, text, label_font, with_alpha(main, 0.94), align)
        p.restore()

    def compact_map_status_label(self, label: str) -> str:
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
        }.get(label, label[:5])

    def _status_src_tag(self) -> str:
        return "g" if self.state.telemetry_source == "gestalt" else "f"

    def _status_known(self, value: float | int) -> bool:
        try:
            return float(value) >= 0
        except (TypeError, ValueError):
            return False

    def _status_value_text(self, value: float | int, *, as_int: bool = True) -> str:
        if not self._status_known(value):
            return "—"
        return str(int(value)) if as_int else str(value)

    def _status_ratio(self, cur: float | int, mx: float | int) -> float:
        if not self._status_known(cur) or not self._status_known(mx) or float(mx) <= 0:
            return 0.0
        return max(0.0, min(1.0, float(cur) / float(mx)))

    def map_status_groups(self) -> tuple[list[tuple[str, str, float, QColor, str, str]], list[tuple[str, str, float, QColor, str, str]]]:
        config = self.match_mode_manager.current
        match_rows: list[tuple[str, str, float, QColor, str, str]] = []
        own_side = self.alliance_side()
        enemy_side = "Blue" if own_side == "Red" else "Red"
        src = self._status_src_tag()
        red_vp = self.state.ally_victory_points if own_side == "Red" else self.state.enemy_victory_points
        blue_vp = self.state.enemy_victory_points if own_side == "Red" else self.state.ally_victory_points
        if config.key == "RMUL3v3":
            if self.registry.get("VictoryPointStatus") and self.registry.get("VictoryPointStatus").enabled:
                match_rows.extend(
                    [
                        ("RED" + (" YOU" if own_side == "Red" else ""), self._status_value_text(red_vp), self._status_ratio(red_vp, 200), self.map_team_color("red"), "red_vp", f"RED VP {self._status_value_text(red_vp)} [{src}]"),
                        ("BLUE" + (" YOU" if own_side == "Blue" else ""), self._status_value_text(blue_vp), self._status_ratio(blue_vp, 200), self.map_team_color("blue"), "blue_vp", f"BLUE VP {self._status_value_text(blue_vp)} [{src}]"),
                        ("CTRL", f"{self._status_value_text(self.state.control_progress)}%" if self._status_known(self.state.control_progress) else "—", self._status_ratio(self.state.control_progress, 100), self.map_control_color(), "control", f"CONTROL {self.state.control_owner} [{src}]"),
                    ]
                )
            if self.registry.get("EconomyPanel") and self.registry.get("EconomyPanel").enabled:
                gold = self.state.remaining_economy
                match_rows.append(("GOLD", self._status_value_text(gold), self._status_ratio(gold, 400) if self._status_known(gold) else 0.0, self.hud_state_color("gold"), "gold", f"GOLD {self._status_value_text(gold)} [{src}]"))
        else:
            if self.registry.get("BaseOutpostStatus") and self.registry.get("BaseOutpostStatus").enabled:
                bh, bhm = self.state.base_health, self.state.base_max_health
                ebh, ebhm = self.state.enemy_base_health, self.state.enemy_base_max_health
                eoh, eohm = self.state.enemy_outpost_health, self.state.enemy_outpost_max_health
                match_rows.extend(
                    [
                        (f"{own_side.upper()} BASE YOU", self._status_value_text(bh) if not self._status_known(bh) else self.short_ring_number(int(bh)), self._status_ratio(bh, bhm if self._status_known(bhm) else max(bh, 1)), self.map_team_color("ally"), "base", f"{own_side} BASE {self._status_value_text(bh)}/{self._status_value_text(bhm)} [{src}]"),
                        (f"{enemy_side.upper()} BASE", self._status_value_text(ebh) if not self._status_known(ebh) else self.short_ring_number(int(ebh)), self._status_ratio(ebh, ebhm if self._status_known(ebhm) else max(ebh, 1)), self.map_team_color("enemy"), "enemy_base", f"{enemy_side} BASE {self._status_value_text(ebh)}/{self._status_value_text(ebhm)} [{src}]"),
                        ("OUTPOST", self._status_value_text(eoh) if not self._status_known(eoh) else self.short_outpost_value(), self._status_ratio(eoh, eohm if self._status_known(eohm) else max(eoh, 1)), self.map_team_color("enemy"), "outpost", f"{enemy_side} OUTPOST {self.state.enemy_outpost_status if self._status_known(eoh) else '—'} [{src}]"),
                    ]
                )
            if self.registry.get("EconomyPanel") and self.registry.get("EconomyPanel").enabled:
                gold = self.state.remaining_economy
                match_rows.append(("GOLD", self._status_value_text(gold), self._status_ratio(gold, 400) if self._status_known(gold) else 0.0, self.hud_state_color("gold"), "gold", f"GOLD {self._status_value_text(gold)} [{src}]"))
            if self.registry.get("ProjectileShop") and self.registry.get("ProjectileShop").enabled:
                ammo = self.state.projectile_allowance_17mm
                match_rows.append(("AMMO", self._status_value_text(ammo), self._status_ratio(ammo, 180) if self._status_known(ammo) else 0.0, self.hud_state_color("ammo"), "ammo", f"AMMO 17mm {self._status_value_text(ammo)} | 42mm {self._status_value_text(self.state.projectile_allowance_42mm)} [{src}]"))
        return match_rows[:5], self.map_telemetry_status_rows()

    def map_telemetry_status_rows(self) -> list[tuple[str, str, float, QColor, str, str]]:
        rows: list[tuple[str, str, float, QColor, str, str]] = []
        thermal = self.registry.get("ThermalPowerPanel")
        if thermal and thermal.enabled:
            ratio = self.state.current_heat / max(1.0, self.state.max_heat)
            cool = float(getattr(self.state, "heat_cooldown_rate", 0.0) or 0.0)
            src = "g" if self.state.telemetry_source == "gestalt" else "f"
            rows.append((
                "HEAT",
                str(int(self.state.current_heat)),
                ratio,
                self.hud_state_color("heat", ratio),
                "heat",
                f"HEAT {int(self.state.current_heat)}/{int(self.state.max_heat)} CD{cool:.0f} [{src}]",
            ))
        power = self.registry.get("PowerLimit")
        if power and power.enabled:
            limit = max(1.0, self.get_setting_number(power, "Limit", float(self.state.power_limit)))
            ratio = self.state.chassis_power_w / limit
            rows.append(("POWER", str(int(self.state.chassis_power_w)), ratio, self.hud_state_color("power", ratio), "power", f"POWER {int(self.state.chassis_power_w)} / {int(limit)}W"))
        speed = self.registry.get("SpeedLimit")
        if speed and speed.enabled:
            limit = max(1.0, self.get_setting_number(speed, "Limit", float(self.state.speed_limit)))
            unit_setting = speed.get_setting("Unit")
            unit = str(unit_setting.value) if unit_setting else "dps"
            ratio = self.state.speed_limit / limit
            rows.append(("SPEED", str(int(self.state.speed_limit)), ratio, self.hud_state_color("speed", ratio), "speed", f"SPEED {int(self.state.speed_limit)} {unit}"))
        capacitor = self.registry.get("Capacitor")
        if capacitor and capacitor.enabled:
            ratio = self.state.capacitor_percent / 100.0
            rows.append(("CAP", f"{self.state.capacitor_percent}%", ratio, self.hud_state_color("cap", ratio), "cap", f"CAP {self.state.capacitor_percent}%"))
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
        rounded_rect(p, rect, 7, self.hud_surface(0.40 if compact else 0.34), self.hud_panel_outline(0.07))
        row_h = rect.height() / max(1, len(rows))
        for index, (label, value, ratio, c) in enumerate(rows):
            y = rect.top() + index * row_h
            label_font = self.font("poppins_medium", 5.6 if compact else 6.8)
            value_font = self.font("poppins_medium", 5.6 if compact else 6.9)
            left_pad = 6 if compact else 8
            self.draw_text(p, QRectF(rect.left() + left_pad, y, rect.width() * 0.38, row_h), label, label_font, THEME.muted)
            self.draw_text(p, QRectF(rect.left() + rect.width() * 0.43, y, rect.width() * 0.52, row_h), value, value_font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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
            self.draw_text(p, QRectF(center.x() - slot_w / 2.0, center.y() - 6.0, slot_w, 12.0), value, self.font("poppins_bold", 6.2 if compact else 7.0), THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.draw_text(p, QRectF(center.x() - slot_w / 2.0, rect.bottom() - (11.0 if compact else 13.0), slot_w, 10.0), label, self.font("poppins_medium", 5.6 if compact else 6.3), with_alpha(c, 0.92), Qt.AlignmentFlag.AlignCenter)

    def match_status_ring_rows(self) -> list[tuple[str, str, float, QColor]]:
        config = self.match_mode_manager.current
        rows: list[tuple[str, str, float, QColor]] = []
        if config.key == "RMUL3v3":
            if self.registry.get("VictoryPointStatus") and self.registry.get("VictoryPointStatus").enabled:
                rows.extend(
                    [
                        ("ALLY", str(self.state.ally_victory_points), self.state.ally_victory_points / 200.0, self.hud_state_color("ally")),
                        ("ENEMY", str(self.state.enemy_victory_points), self.state.enemy_victory_points / 200.0, self.hud_state_color("enemy")),
                        ("CTRL", f"{int(self.state.control_progress)}%", self.state.control_progress / 100.0, self.hud_state_color("control")),
                    ]
                )
            if self.registry.get("ThermalPowerPanel") and self.registry.get("ThermalPowerPanel").enabled:
                ratio = self.state.current_heat / max(1.0, self.state.max_heat)
                rows.append(("HEAT", f"{int(self.state.current_heat)}", ratio, self.hud_state_color("heat", ratio)))
            return rows[:4]

        if self.registry.get("BaseOutpostStatus") and self.registry.get("BaseOutpostStatus").enabled:
            rows.extend(
                [
                    ("BASE", self.short_ring_number(self.state.base_health), self.state.base_health / max(1, self.state.base_max_health), self.hud_state_color("ally")),
                    ("EBASE", self.short_ring_number(self.state.enemy_base_health), self.state.enemy_base_health / max(1, self.state.enemy_base_max_health), self.hud_state_color("enemy")),
                    ("OUT", self.short_outpost_value(), self.state.enemy_outpost_health / max(1, self.state.enemy_outpost_max_health), self.hud_state_color("warning")),
                ]
            )
        if self.registry.get("EconomyPanel") and self.registry.get("EconomyPanel").enabled:
            rows.append(("GOLD", str(int(self.state.remaining_economy)), min(1.0, self.state.remaining_economy / 400.0), self.hud_state_color("gold")))
        if len(rows) < 4 and self.registry.get("ProjectileShop") and self.registry.get("ProjectileShop").enabled:
            rows.append(("AMMO", str(int(self.state.projectile_allowance_17mm)), min(1.0, self.state.projectile_allowance_17mm / 180.0), self.hud_state_color("ammo")))
        if len(rows) < 4 and self.registry.get("ThermalPowerPanel") and self.registry.get("ThermalPowerPanel").enabled:
            ratio = self.state.current_heat / max(1.0, self.state.max_heat)
            rows.append(("HEAT", f"{int(self.state.current_heat)}", ratio, self.hud_state_color("heat", ratio)))
        return rows[:4]

    def short_ring_number(self, value: float) -> str:
        value = float(value)
        if value < 0:
            return "—"
        if abs(value) >= 1000:
            return f"{value / 1000.0:.1f}k".replace(".0k", "k")
        return str(int(value))

    def short_outpost_value(self) -> str:
        if not self._status_known(self.state.enemy_outpost_health):
            return "—"
        status = str(self.state.enemy_outpost_status)
        if status.upper() in ("ACTIVE", "ALIVE", "ONLINE"):
            return "OK"
        if status.upper() in ("DOWN", "DESTROYED", "OFFLINE"):
            return "DOWN"
        if self.state.enemy_outpost_health <= 0:
            return "DOWN"
        return self.short_ring_number(self.state.enemy_outpost_health)

    def match_status_rows(self, compact: bool = False) -> list[tuple[str, str, float, QColor]]:
        config = self.match_mode_manager.current
        rows: list[tuple[str, str, float, QColor]] = []
        if config.key == "RMUL3v3":
            if self.registry.get("VictoryPointStatus") and self.registry.get("VictoryPointStatus").enabled:
                rows.extend(
                    [
                        ("ALLY VP", str(self.state.ally_victory_points), self.state.ally_victory_points / 200.0, self.hud_state_color("ally")),
                        ("ENEMY VP", str(self.state.enemy_victory_points), self.state.enemy_victory_points / 200.0, self.hud_state_color("enemy")),
                        ("CONTROL", f"{self.state.control_owner} {self.state.control_progress}%", self.state.control_progress / 100.0, self.hud_state_color("control")),
                    ]
                )
        elif self.registry.get("BaseOutpostStatus") and self.registry.get("BaseOutpostStatus").enabled:
            rows.extend(
                [
                    ("OWN BASE", f"{self.state.base_health}/{self.state.base_max_health}", self.state.base_health / max(1, self.state.base_max_health), self.hud_state_color("ally")),
                    ("ENEMY BASE", f"{self.state.enemy_base_health}/{self.state.enemy_base_max_health}", self.state.enemy_base_health / max(1, self.state.enemy_base_max_health), self.hud_state_color("enemy")),
                    ("OUTPOST", f"{self.state.enemy_outpost_status}", self.state.enemy_outpost_health / max(1, self.state.enemy_outpost_max_health), self.hud_state_color("warning")),
                ]
            )
        if self.registry.get("EconomyPanel") and self.registry.get("EconomyPanel").enabled:
            rows.append(("GOLD", f"{self.state.remaining_economy} / T{self.state.tech_level}", min(1.0, self.state.remaining_economy / 400.0), self.hud_state_color("gold")))
        if self.registry.get("ProjectileShop") and self.registry.get("ProjectileShop").enabled:
            rows.append(("AMMO", f"17 {self.state.projectile_allowance_17mm} | 42 {self.state.projectile_allowance_42mm}", min(1.0, self.state.projectile_allowance_17mm / 180.0), self.hud_state_color("ammo")))
        if self.registry.get("ThermalPowerPanel") and self.registry.get("ThermalPowerPanel").enabled:
            ratio = self.state.current_heat / max(1.0, self.state.max_heat)
            rows.append(("HEAT/PWR", f"{int(self.state.current_heat)}/{int(self.state.max_heat)}  {int(self.state.chassis_power_w)}W", ratio, self.hud_state_color("heat", ratio)))
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
            heat_ratio = self.state.current_heat / max(1.0, self.state.max_heat)
            cards.append(("HEAT", f"{int(self.state.current_heat)}/{int(self.state.max_heat)}", heat_ratio, self.hud_state_color("heat", heat_ratio), "ThermalPowerPanel"))
        power = self.registry.get("PowerLimit")
        if power and power.enabled:
            limit = max(1.0, self.get_setting_number(power, "Limit", float(self.state.power_limit)))
            ratio = self.state.chassis_power_w / limit
            cards.append(("POWER", f"{int(self.state.chassis_power_w)} / {int(limit)}W", ratio, self.hud_state_color("power", ratio), "PowerLimit"))
        speed = self.registry.get("SpeedLimit")
        if speed and speed.enabled:
            limit = max(1.0, self.get_setting_number(speed, "Limit", float(self.state.speed_limit)))
            unit_setting = speed.get_setting("Unit")
            unit = str(unit_setting.value) if unit_setting else "dps"
            ratio = self.state.speed_limit / max(1.0, limit)
            cards.append(("SPEED", f"{int(self.state.speed_limit)} {unit}", ratio, self.hud_state_color("speed", ratio), "SpeedLimit"))
        capacitor = self.registry.get("Capacitor")
        if capacitor and capacitor.enabled:
            ratio = self.state.capacitor_percent / 100.0
            cards.append(("CAP", f"{self.state.capacitor_percent}%", ratio, self.hud_state_color("cap", ratio), "Capacitor"))
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
            rect = QRectF(x + index * (card_w + gap), y + (1.0 - active) * 12.0, card_w, card_h)
            p.save()
            p.setOpacity(active)
            if self.hud_clean():
                self.draw_hud_liquid_glass(p, rect, 7, surface="SubGlass")
            else:
                glow_rect(p, rect, 8, 0.11)
                rounded_rect(p, rect, 7, self.hud_surface(0.68), self.hud_panel_outline(0.09))
            self.draw_text(p, QRectF(rect.left() + 9, rect.top() + 5, 48, 12), label, self.font("poppins_medium", 7.0), THEME.muted)
            self.draw_text(p, QRectF(rect.left() + 46, rect.top() + 4, rect.width() - 55, 13), value, self.font("poppins_bold", 7.3), THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            draw_bar(p, QRectF(rect.left() + 9, rect.bottom() - 10, rect.width() - 18, 4.2), max(0.0, min(1.0, ratio)), c, color("#ffffff", 0.13), 2.1)
            p.restore()

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
            ratio = max(0.0, min(1.0, ratio))
            row_active = self.anim.value(f"hud.telemetry.compact.{key}", 1.0, self.anim_duration(0.16), Easings.ease_out_pow4, initial=0.0)
            p.setOpacity(active * row_active)
            self.draw_text(p, QRectF(seg_x, y + 4, seg_w * 0.44, 10), label, self.font("poppins_medium", 6.4), THEME.muted)
            self.draw_text(p, QRectF(seg_x + seg_w * 0.38, y + 4, seg_w * 0.62, 10), value.replace(" / ", "/"), self.font("poppins_medium", 6.5), THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            draw_bar(p, QRectF(seg_x, y + 18, seg_w, 3.2), ratio, c, color("#ffffff", 0.12), 1.6)
            seg_x += seg_w + gap
        p.restore()

    def format_time(self, seconds: int) -> str:
        seconds = max(0, int(seconds))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def draw_module_list_hud(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        hud = self.registry.get("HUD")
        setting = hud.get_setting("ModuleList Style") if hud else None
        style = str(setting.value) if setting else MODULE_LIST_STYLES[1]
        if style not in MODULE_LIST_STYLES:
            style = MODULE_LIST_STYLES[1]
        if self.high_res_optimize() and style in ("GlowRows", "DynamicIsland", "StatusBars", "RobotCards"):
            self.draw_module_list_fast_rows(p, width, start_y)
            return
        if style == "PolarLegacy":
            self.draw_module_list_polar_legacy(p, start_y)
        elif style == "GlowRows":
            self.draw_module_list_glow_rows(p, width, start_y)
        elif style == "Segmented":
            self.draw_module_list_segmented(p, width, start_y)
        elif style == "DynamicIsland":
            self.draw_module_list_dynamic_island(p, width, start_y)
        elif style == "KeyBindsRows":
            self.draw_module_list_keybind_rows(p, width, start_y)
        elif style == "StatusBars":
            self.draw_module_list_status_bars(p, width, start_y)
        elif style == "RobotCards":
            self.draw_module_list_robot_cards(p, width, start_y)
        elif style in ("LiquidArrayList", "LiquidArrayTags", "LiquidArrayCompact", "LiquidArrayDock"):
            self.draw_module_list_liquid_array(p, width, style, start_y)
        else:
            self.draw_module_list_rainbow_right(p, width, start_y)

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
            accent = accent_color(idx, time.perf_counter() * 0.38)
            p.save()
            p.setOpacity(entry["alpha"])
            rounded_rect(p, rect, 6, color("#050507", 0.52), color("#ffffff", 0.06), 0.8)
            index_rect = QRectF(rect.left(), rect.top(), 20.0, rect.height())
            rounded_rect(p, index_rect, 6, with_alpha(accent, 0.78))
            self.draw_text(p, index_rect, str(idx + 1), self.font("poppins_bold", 6.6), THEME.text, Qt.AlignmentFlag.AlignCenter)
            cat = module.category[:3].upper()
            self.draw_text(p, QRectF(rect.right() - 27, rect.top(), 24, rect.height()), cat, category_font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.draw_text(p, rect.adjusted(25, 0, -32, 0), module.name, font, THEME.text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
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
        visible.sort(
            key=lambda module: text_width(p, font, f"{module.name}  {self.module_list_tag(module, tag_style)}"),
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
        for name, row in list(self.module_list_rows.items()):
            target = float(row.get("target", 0.0))
            new = bool(row.pop("new", False))
            slide_distance = 34.0 if right_aligned else -34.0
            if animation_style == "Fade":
                slide_distance = 0.0
            alpha = self.anim.value(f"hud.modulelist.alpha.{name}", target, self.anim_duration(0.22), Easings.ease_out_pow3, initial=0.0 if new else target)
            height = self.anim.value(f"hud.modulelist.height.{name}", target, self.anim_duration(0.22), Easings.ease_out_pow3, initial=0.0 if new else target)
            slide = self.anim.value(
                f"hud.modulelist.slide.{name}",
                0.0 if target > 0 else slide_distance,
                self.anim_duration(0.22),
                Easings.ease_out_pow3 if target > 0 else Easings.ease_in_pow3,
                initial=slide_distance if new else 0.0,
            )
            order_initial = float(row.get("previous_order", row.get("order", 0)))
            order_animated = self.anim.value(
                f"hud.modulelist.order.{name}",
                float(row.get("order", 0)),
                self.anim_duration(0.20),
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

    def draw_module_list_liquid_array(self, p: QPainter, width: int, style: str, start_y: float | None = None) -> None:
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
        y0 = 60.0 if start_y is None else start_y
        max_rows = max(1, int((self.height() - y0 - 18.0) // (row_h + gap)))
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
            accent = self.hud_accent()
            p.save()
            p.setOpacity(p.opacity() * entry["alpha"])
            if surface == "LiquidGlass":
                self.draw_hud_liquid_glass(p, rect, 4.0 if compact else 5.0, surface="SubGlass" if compact else "MainGlass", hover=hover)
            elif not compact or hovered:
                rounded_rect(p, rect, 4.0 if compact else 5.0, color("#03060b", 0.48 + 0.16 * hover), color("#ffffff", 0.045 + 0.07 * hover), 0.7)
            rail = QRectF(rect.right() - 4.0, rect.top() + 2.0, 3.0, max(2.0, rect.height() - 4.0)) if right_aligned else QRectF(rect.left() + 1.0, rect.top() + 2.0, 3.0, max(2.0, rect.height() - 4.0))
            rounded_rect(p, rail, 1.2, with_alpha(accent, 0.76 + 0.18 * hover))
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
                        rounded_rect(p, tag_rect, 3.5, with_alpha(accent, 0.12 + 0.08 * hover), with_alpha(accent, 0.22), 0.6)
                if right_aligned:
                    text_rect.setLeft(tag_rect.right() + 5.0)
                else:
                    text_rect.setRight(tag_rect.left() - 5.0)
                self.draw_text(p, tag_rect, tag, tag_font, THEME.text if tags_variant else THEME.muted, Qt.AlignmentFlag.AlignCenter)
            align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter if right_aligned else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            self.draw_text(p, text_rect, module.name, name_font, THEME.text, align)
            if dock:
                progress = self.anim.value(f"hud.liquid_array.progress.{module.name}", 1.0 if module.enabled else 0.0, self.anim_duration(0.20), Easings.ease_out_cubic, initial=1.0)
                bar = QRectF(rect.left() + 7.0, rect.bottom() - 2.3, max(2.0, (rect.width() - 14.0) * progress), 1.1)
                rounded_rect(p, bar, 0.55, with_alpha(accent, 0.62))
            self.add_hotspot(rect, "module_toggle", module.name, tooltip=f"{module.name} | {module.category} | {module.keybind}")
            p.restore()

    def draw_module_list_polar_legacy(self, p: QPainter, start_y: float | None = None) -> None:
        font = self.font("pingfang", 9.2)
        entries = self.module_list_entries(p, font, right_aligned=False)
        x = 4.0
        y = 4.0 if start_y is None else start_y
        logo_font = self.font("pingfang", 9.2, QFont.Weight.Bold)
        glow_text(p, "POLAR", x, y + 11, logo_font, accent_color(0, time.perf_counter()), THEME.cyan, 3)
        self.draw_text(p, QRectF(x + text_width(p, logo_font, "POLAR") + 4, y, 150, 14), f"RM ({self.state.fps:.0f}FPS)", font, THEME.text)
        row_y = y + 16.0
        for idx, entry in enumerate(entries):
            module = entry["module"]
            alpha = entry["alpha"]
            h = 11.0 * entry["height"]
            if h <= 0.1:
                continue
            p.save()
            p.setOpacity(alpha)
            c = accent_color(idx, time.perf_counter() * 0.6)
            self.draw_text(p, QRectF(x + entry["slide"], row_y, 180, h), module.name, font, c)
            p.restore()
            row_y += h

    def draw_module_list_rainbow_right(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        font = self.font("poppins_medium", 9.6)
        entries = self.module_list_entries(p, font, right_aligned=True)
        x = width - 18
        y = 18.0 if start_y is None else start_y
        for idx, entry in enumerate(entries):
            module = entry["module"]
            row_w = text_width(p, font, module.name) + 16
            row_h = 18.0 * entry["height"]
            if row_h <= 0.1:
                continue
            row = QRectF(x - row_w + entry["slide"], y, row_w, row_h)
            p.save()
            p.setOpacity(entry["alpha"])
            self.draw_text(p, row, module.name, font, accent_color(idx, time.perf_counter()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            p.restore()
            y += row_h

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
            p.save()
            p.setOpacity(entry["alpha"])
            glow_rect(p, rect, 7, 0.08)
            rounded_rect(p, rect, 6, color("#050506", 0.68), color("#ffffff", 0.10))
            c = accent_color(idx, time.perf_counter())
            rounded_rect(p, QRectF(rect.right() - 4, rect.top() + 3, 2, max(3, rect.height() - 6)), 1, c)
            self.draw_text(p, rect.adjusted(10, 0, -12, 0), label, font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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
        self.draw_text(p, header.adjusted(37, 0, -8, 0), "modules", font, THEME.cyan, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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
            self.draw_text(p, rect.adjusted(25, 0, -8, 0), module.name, font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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
        target_w = max(250.0, min(620.0, QFontMetricsF(font).horizontalAdvance(text) + 58.0))
        active = max(entry["alpha"] for entry in shown)
        y = 58.0 if start_y is None else start_y
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
            self.draw_text(p, QRectF(x, rect.top(), label_w + 6, rect.height()), module.name, font, accent_color(idx), Qt.AlignmentFlag.AlignCenter)
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
        y = 78.0 if start_y is None else start_y
        rounded_rect(p, QRectF(x, y, panel_w, header_h), 5, color("#000000", 0.74), color("#ffffff", 0.10))
        self.draw_text(p, QRectF(x + 9, y, panel_w - 18, header_h), "Hotkeys", self.font("poppins_medium", 8.7), THEME.text)
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
            self.draw_text(p, name_rect.adjusted(6, 0, -6, 0), module.name, name_font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.draw_text(p, key_rect, key, key_font, accent_color(idx), Qt.AlignmentFlag.AlignCenter)
            p.restore()
            row_y += row_h + 3 * entry["height"]

    def draw_module_list_status_bars(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        name_font = self.font("pingfang", 8.3)
        value_font = self.font("axiforma_bold", 7.8)
        entries = self.module_list_entries(p, name_font, right_aligned=True)
        x = width - 18.0
        y = 78.0 if start_y is None else start_y
        for idx, entry in enumerate(entries):
            module = entry["module"]
            total_w = max(132.0, min(208.0, text_width(p, name_font, module.name) + 58.0))
            row_h = 18.0 * entry["height"]
            if row_h <= 0.1:
                continue
            rect = QRectF(x - total_w + entry["slide"], y, total_w, row_h)
            icon_w = 21.0
            c = accent_color(idx, time.perf_counter())
            ratio = 0.45 + 0.45 * (0.5 + 0.5 * math.sin(time.perf_counter() * 0.8 + idx))
            p.save()
            p.setOpacity(entry["alpha"])
            rounded_rect(p, QRectF(rect.left() + icon_w, rect.top(), rect.width() - icon_w, row_h), 5, color("#1e1e23", 0.46), color("#ffffff", 0.06))
            rounded_rect(p, QRectF(rect.left() + icon_w, rect.top(), max(4.0, (rect.width() - icon_w) * ratio), row_h), 5, with_alpha(c, 0.42))
            rounded_rect(p, QRectF(rect.left(), rect.top(), icon_w, row_h), 5, with_alpha(c, 0.72))
            self.draw_text(p, QRectF(rect.left(), rect.top(), icon_w, row_h), str(idx + 1), value_font, THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.draw_text(p, QRectF(rect.left() + icon_w + 6, rect.top(), rect.width() - icon_w - 46, row_h), module.name, name_font, THEME.text)
            self.draw_text(p, QRectF(rect.right() - 42, rect.top(), 36, row_h), module.category[:3].upper(), value_font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            p.restore()
            y += row_h + 2.0 * entry["height"]

    def draw_module_list_robot_cards(self, p: QPainter, width: int, start_y: float | None = None) -> None:
        font = self.font("pingfang", 8.4)
        sub_font = self.font("poppins_regular", 7.2)
        entries = self.module_list_entries(p, font, right_aligned=True)
        x = width - 18.0
        y = 78.0 if start_y is None else start_y
        for idx, entry in enumerate(entries):
            module = entry["module"]
            row_w = max(158.0, text_width(p, font, module.name) + 86.0)
            row_h = 30.0 * entry["height"]
            if row_h <= 0.1:
                continue
            rect = QRectF(x - row_w + entry["slide"], y, row_w, row_h)
            c = accent_color(idx, time.perf_counter())
            p.save()
            p.setOpacity(entry["alpha"])
            glow_rect(p, rect, 9, 0.07)
            rounded_rect(p, rect, 8, color("#000000", 0.38), color("#ffffff", 0.08))
            badge = QRectF(rect.left() + 5, rect.top() + 5, 20, max(4.0, rect.height() - 10))
            rounded_rect(p, badge, 7, with_alpha(c, 0.60), color("#ffffff", 0.10))
            self.draw_text(p, badge, module.category[0], self.font("poppins_bold", 8.2), THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.draw_text(p, rect.adjusted(32, 3, -54, -13), module.name, font, THEME.text)
            self.draw_text(p, rect.adjusted(32, 15, -54, -2), module.category, sub_font, THEME.muted)
            chip = QRectF(rect.right() - 46, rect.top() + 6, 38, max(4.0, rect.height() - 12))
            rounded_rect(p, chip, 6, color("#ffffff", 0.05), color("#ffffff", 0.08))
            self.draw_text(p, chip, module.keybind if module.keybind != "None" else "ON", sub_font, c, Qt.AlignmentFlag.AlignCenter)
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
        self.draw_text(p, QRectF(x + 9, y + 4, row_w - 18, 14), "KeyBinds", font, THEME.text)
        for i, module in enumerate(rows):
            yy = y + 22 + i * row_h
            self.draw_text(p, QRectF(x + 9, yy, row_w - 56, row_h), module.name, font, THEME.muted)
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
        self.draw_text(p, rect.adjusted(12, 7, -12, -rect.height() + 27), "KeyBinds", self.font("poppins_bold", 10), THEME.text)
        for i, module in enumerate(modules[:11]):
            y = rect.top() + 32 + i * 22
            row = QRectF(rect.left() + 8, y, rect.width() - 16, 19)
            row_active = self.anim.value(f"hud.bind.row.{module.name}", 1.0 if module.enabled else 0.42, self.anim_duration(0.18), Easings.ease_out_quad, initial=0.0)
            rounded_rect(p, row, 5, color("#ffffff", 0.035 + 0.035 * row_active))
            self.draw_text(p, row.adjusted(8, 0, -56, 0), module.name, self.font("poppins_regular", 8.2), THEME.text if module.enabled else THEME.muted)
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
        self.draw_text(p, rect.adjusted(12, 8, -12, -rect.height() + 29), "RobotListHud", self.font("poppins_bold", 10), THEME.text)
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

    def draw_status_cards_hud(self, p: QPainter, width: int) -> None:
        cards = [
            ("GIMPOWER", f"{self.state.gimbal_power}V", self.state.gimbal_power / 30.0),
            ("POWERLIM", f"{self.state.power_limit}W", self.state.power_limit / 120.0),
            ("SPEEDLIM", f"{self.state.speed_limit}dps", self.state.speed_limit / 720.0),
            ("CAP", f"{self.state.capacitor_percent}%", self.state.capacitor_percent / 100.0),
        ]
        x = width - 194
        y = 78.0
        for i, (label, value, ratio) in enumerate(cards):
            active = self.anim.value(f"hud.card.{label}", 1.0, self.anim_duration(0.25 + i * 0.02), Easings.ease_out_pow4, initial=0.0)
            rect = QRectF(x + (1.0 - active) * 30, y + i * 55, 170, 44)
            p.save()
            p.setOpacity(active)
            glow_rect(p, rect, 8, 0.05 if self.hud_clean() else 0.10)
            rounded_rect(p, rect, 8, self.hud_surface(0.72), self.hud_panel_outline(0.09))
            self.draw_text(p, rect.adjusted(12, 6, -12, -20), label, self.font("poppins_medium", 8), THEME.muted)
            self.draw_text(p, rect.adjusted(12, 15, -12, -5), value, self.font("poppins_bold", 11), THEME.text)
            draw_bar(p, QRectF(rect.left() + 82, rect.top() + 27, 72, 5), ratio, self.hud_state_color("warning" if label in ("POWERLIM", "SPEEDLIM") and ratio > 0.78 else "info", ratio), color("#ffffff", 0.12))
            p.restore()

    def draw_rm_indicators_hud(self, p: QPainter, width: int) -> None:
        indicators = [
            ("FRIC", self.state.friction_wheel),
            ("LOCK", self.state.lock_state),
            ("SMLBUFF", self.state.small_buff),
            ("LARBUFF", self.state.large_buff),
            ("ANTIRO", self.state.anti_rotation),
            ("PTE", self.state.pte_ready),
            ("JUP", self.state.jump_ready),
            ("WHE", self.state.wheel_online),
            ("LES", self.state.les_online),
        ]
        rect = QRectF(width - 244, 78, 220, 172)
        active = self.anim.value("hud.rm_indicators", 1.0, self.anim_duration(0.26), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        glow_rect(p, rect, 8, 0.06 if self.hud_clean() else 0.16)
        rounded_rect(p, rect, 8, self.hud_surface(0.72), self.hud_panel_outline(0.09))
        self.draw_text(p, rect.adjusted(12, 8, -12, -rect.height() + 29), "RMIndicatorsHud", self.font("poppins_bold", 10), THEME.text)
        chip_w = 62
        for i, (label, enabled) in enumerate(indicators):
            col = i % 3
            row = i // 3
            chip = QRectF(rect.left() + 12 + col * (chip_w + 6), rect.top() + 36 + row * 28, chip_w, 21)
            enabled_anim = self.anim.value(f"hud.ind.{label}", 1.0 if enabled else 0.0, self.anim_duration(0.18), Easings.ease_out_quad, initial=0.0)
            fill = with_alpha(self.hud_success() if enabled else self.hud_accent(), 0.12 + 0.44 * enabled_anim)
            rounded_rect(p, chip, 6, fill, color("#ffffff", 0.08))
            self.draw_text(p, chip, label, self.font("poppins_medium", 7.3), THEME.text if enabled else THEME.muted, Qt.AlignmentFlag.AlignCenter)
        ammo = f"17mm {self.state.projectile_allowance_17mm} | 42mm {self.state.projectile_allowance_42mm}"
        self.draw_text(p, rect.adjusted(12, 134, -12, -12), ammo, self.font("poppins_regular", 8.2), THEME.muted)
        p.restore()

    def draw_target_hud_by_style(self, p: QPainter, width: int, height: int, target: VisionTarget, title: str) -> None:
        style = self.target_hud_style()
        if self.hud_density() == "DebugFull" or style == "FullCard":
            self.draw_target_card(p, QRectF(22, height - 112, 224, 82), target, title)
        elif style == "BarOnly":
            self.draw_target_bar_only(p, QRectF(22, height - 92, 156, 24), target)
        else:
            self.draw_target_round_compact(p, QRectF(22, height - 102, 154, 42), target)

    def draw_target_round_compact(self, p: QPainter, rect: QRectF, target: VisionTarget) -> None:
        hp_ratio = self.target_ratio(target)
        lag_ratio = self.anim.value(f"hud.target.lag.{target.kind}", hp_ratio, self.anim_duration(1.15), Easings.ease_out_pow5, initial=hp_ratio)
        main_ratio = self.anim.value(f"hud.target.main.{target.kind}", hp_ratio, self.anim_duration(0.34), Easings.ease_out_pow4, initial=hp_ratio)
        active = self.anim.value(f"hud.target.round.{target.kind}", 1.0, self.anim_duration(0.24), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        if self.hud_clean():
            self.draw_hud_liquid_glass(p, rect, 6, surface="SubGlass")
        else:
            rounded_rect(p, rect, 6, self.hud_surface(0.52), self.hud_panel_outline(0.11))
        name = self.target_short_name(target)
        value = f"{hp_ratio * 100:.0f}%"
        self.draw_glass_text(p, QRectF(rect.left() + 8, rect.top() + 4, rect.width() - 55, 14), name, self.font("poppins_medium", 8.4), THEME.text)
        self.draw_glass_text(p, QRectF(rect.right() - 46, rect.top() + 4, 38, 14), value, self.font("poppins_medium", 8.2), self.hud_accent(), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar = QRectF(rect.left() + 8, rect.top() + 23, rect.width() - 16, 4.2)
        rounded_rect(p, bar, 2.1, color("#ffffff", 0.12))
        lag = QRectF(bar)
        lag.setWidth(max(bar.height(), bar.width() * max(0.0, min(1.0, lag_ratio))))
        rounded_rect(p, lag, 2.1, color("#ffffff", 0.20))
        filled = QRectF(bar)
        filled.setWidth(max(bar.height(), bar.width() * max(0.0, min(1.0, main_ratio))))
        draw_bar(p, bar, main_ratio, self.hud_accent(), color("#ffffff", 0.0), 2.1)
        info = self.target_compact_info(target)
        self.draw_glass_text(p, QRectF(rect.left() + 8, rect.bottom() - 13, rect.width() - 16, 10), info, self.font("poppins_regular", 6.8), THEME.muted)
        p.restore()

    def draw_target_bar_only(self, p: QPainter, rect: QRectF, target: VisionTarget) -> None:
        hp_ratio = self.target_ratio(target)
        main_ratio = self.anim.value(f"hud.target.bar.{target.kind}", hp_ratio, self.anim_duration(0.28), Easings.ease_out_pow4, initial=hp_ratio)
        active = self.anim.value(f"hud.target.bar.active.{target.kind}", 1.0, self.anim_duration(0.20), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        self.draw_text(p, QRectF(rect.left(), rect.top(), rect.width(), 11), self.target_short_name(target), self.font("poppins_medium", 7.4), THEME.text)
        draw_bar(p, QRectF(rect.left(), rect.bottom() - 7, rect.width(), 4.0), main_ratio, self.hud_accent(), color("#ffffff", 0.13), 2.0)
        self.draw_text(p, QRectF(rect.right() - 42, rect.top(), 42, 11), f"{hp_ratio * 100:.0f}%", self.font("poppins_medium", 7.4), self.hud_accent(), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p.restore()

    def target_ratio(self, target: VisionTarget) -> float:
        if target.kind == "energy":
            return max(0.0, min(1.0, self.state.energy_rune_progress / 100.0))
        return max(0.0, min(1.0, self.state.target_health / max(1, self.state.target_max_health)))

    def target_short_name(self, target: VisionTarget) -> str:
        if target.kind == "energy":
            return f"RUNE {self.state.energy_rune_phase}"
        return f"ID {self.state.target_id}"

    def target_compact_info(self, target: VisionTarget) -> str:
        if target.kind == "energy":
            return f"{self.state.energy_target_distance_m:.1f}m | {self.state.energy_rune_rotation} | LOCK {str(self.state.energy_target_locked).upper()}"
        return f"{self.state.target_distance_m:.1f}m | LOCK {str(self.state.target_locked).upper()}"

    def draw_target_card(self, p: QPainter, rect: QRectF, target: VisionTarget, title: str) -> None:
        active = self.anim.value(f"hud.target.{title}", 1.0, self.anim_duration(0.24), Easings.ease_out_pow4, initial=0.0)
        p.save()
        p.setOpacity(active)
        glow_rect(p, rect, 9, 0.07 if self.hud_clean() else 0.18)
        rounded_rect(p, rect, 8, self.hud_surface(0.76), self.hud_panel_outline(0.10))
        self.draw_text(p, rect.adjusted(13, 9, -13, -54), title, self.font("poppins_bold", 10), THEME.text)
        if target.kind == "energy":
            ratio = self.state.energy_rune_progress / 100.0
            draw_bar(p, QRectF(rect.left() + 13, rect.top() + 43, rect.width() - 26, 7), ratio, self.hud_accent(), color("#ffffff", 0.14))
            info = f"{self.state.energy_rune_phase} | {self.state.energy_target_distance_m:.1f}m | {self.state.energy_rune_rotation} | LOCK {str(self.state.energy_target_locked).upper()}"
            value = f"{self.state.energy_rune_progress}%"
        else:
            hp_ratio = self.state.target_health / max(1, self.state.target_max_health)
            draw_bar(p, QRectF(rect.left() + 13, rect.top() + 43, rect.width() - 26, 7), hp_ratio, self.hud_accent(), color("#ffffff", 0.14))
            info = f"ID {self.state.target_id} | {self.state.target_distance_m:.1f}m | LOCK {str(self.state.target_locked).upper()}"
            value = f"{target.confidence:.0%}"
        self.draw_text(p, rect.adjusted(13, 56, -13, -8), info, self.font("poppins_regular", 8.2), THEME.muted)
        self.draw_text(p, QRectF(rect.right() - 73, rect.top() + 10, 58, 18), value, self.font("poppins_medium", 9), self.hud_accent(), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p.restore()

    def draw_panel_gui(self, p: QPainter, width: int, height: int, alpha: float) -> None:
        scale_setting = self.get_gui_scale_for_bounds(width, height, 600.0, 440.0, 76.0, 82.0)
        open_scale = 0.98 + 0.02 * alpha
        scale = scale_setting * open_scale
        panel_w = 600.0 * scale
        panel_h = 400.0 * scale
        x = width / 2 - panel_w / 2
        y = height / 2 - panel_h / 2
        panel = QRectF(x, y, panel_w, panel_h)
        glow_rect(p, panel, 11 * scale, 0.34)
        rounded_rect(p, panel, 8 * scale, color("#101014", 0.96), color("#ffffff", 0.13))

        self.draw_profile_widget(p, x + 14 * scale, y + 13 * scale, scale)
        self.draw_panel_category_bar(p, QRectF(x + 414 * scale, y + 18 * scale, 166 * scale, 28 * scale), scale)
        self.panel_modules_rect = QRectF(x + 12 * scale, y + 43 * scale, 160 * scale, 316 * scale)
        settings_rect = QRectF(x + 187 * scale, y + 43 * scale, 401 * scale, 316 * scale)
        self.panel_settings_rect = settings_rect
        self.draw_panel_module_list(p, self.panel_modules_rect, scale)
        self.draw_settings_area(p, settings_rect, self.selected_module, scale)

        search_y = min(height - 60.0, y + panel_h + 15.0 * scale)
        self.draw_search_bar(p, QRectF(width / 2 - 104 * scale, search_y, 208 * scale, 23 * scale), scale)

    def draw_profile_widget(self, p: QPainter, x: float, y: float, scale: float) -> None:
        self.draw_text(p, QRectF(x, y, 160 * scale, 20 * scale), "POLAR-RM", self.font("poppins_bold", 14 * scale), THEME.text)
        sub = f"配置 Default | 缩放 {self.get_gui_scale():.2f}"
        self.draw_text(p, QRectF(x, y + 20 * scale, 210 * scale, 16 * scale), sub, self.font("poppins_regular", 7.8 * scale), THEME.muted)

    def draw_panel_category_bar(self, p: QPainter, rect: QRectF, scale: float) -> None:
        step = rect.width() / len(CATEGORIES)
        for i, category in enumerate(CATEGORIES):
            box = QRectF(rect.left() + i * step, rect.top(), step, rect.height())
            active = category == self.selected_category
            active_v = self.anim.value(f"panel.cat.{category}", 1.0 if active else 0.0, self.anim_duration(0.16), Easings.ease_out_quad)
            if active_v > 0.01:
                rounded_rect(p, box.adjusted(2 * scale, 2 * scale, -2 * scale, -2 * scale), 9 * scale, color("#ffffff", 0.04 * active_v), color("#ffffff", 0.20 * active_v))
            self.draw_text(
                p,
                box,
                self.category_icon(category),
                self.font("material", 12.5 * scale, QFont.Weight.Bold),
                THEME.text if active else color("#7e7e85"),
                Qt.AlignmentFlag.AlignCenter,
            )
            self.add_hotspot(box, "select_category", category, tooltip=category)

    def draw_panel_module_list(self, p: QPainter, rect: QRectF, scale: float) -> None:
        rounded_rect(p, rect, 4 * scale, color("#ffffff", 0.055), color("#ffffff", 0.035))
        self.draw_text(p, rect.adjusted(10 * scale, 7 * scale, -10 * scale, -rect.height() + 30 * scale), self.selected_category, self.font("poppins_bold", 12.5 * scale), THEME.text)
        modules = self.panel_modules()
        top = rect.top() + 38 * scale
        row_h = 22 * scale
        p.save()
        p.setClipRect(rect.adjusted(0, 32 * scale, 0, 0))
        for i, module in enumerate(modules):
            row = QRectF(rect.left() + 6 * scale, top + i * row_h, rect.width() - 12 * scale, 19 * scale)
            selected = module is self.selected_module
            enabled_v = self.anim.value(f"module.enabled.{module.name}", 1.0 if module.enabled else 0.0, self.anim_duration(0.22), Easings.ease_out_cubic)
            select_v = self.anim.value(f"panel.select.{module.name}", 1.0 if selected else 0.0, self.anim_duration(0.18), Easings.ease_out_quad)
            if selected or enabled_v > 0.01:
                rounded_rect(p, row, 5 * scale, color("#ffffff", 0.055 * select_v + 0.035 * enabled_v))
            label_color = THEME.text if module.enabled else color("#ababaf")
            if enabled_v > 0.01:
                glow_text(p, module.name, row.left() + 6 * scale, cap_center_y(p, self.font("poppins_medium", 8.6 * scale), row), self.font("poppins_medium", 8.6 * scale), label_color, accent_color(i), 4 * enabled_v)
            else:
                self.draw_text(p, row.adjusted(6 * scale, 0, -42 * scale, 0), module.name, self.font("poppins_regular", 8.6 * scale), label_color)
            key_box = QRectF(row.right() - 34 * scale, row.top() + 2 * scale, 30 * scale, row.height() - 4 * scale)
            rounded_rect(p, key_box, 4 * scale, color("#ffffff", 0.045), color("#ffffff", 0.06))
            self.draw_text(p, key_box, "KEY", self.font("poppins_medium", 6.3 * scale), THEME.muted, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(row.adjusted(0, 0, -36 * scale, 0), "module_select", module.name, button=1, tooltip=module.description)
            self.add_hotspot(row.adjusted(0, 0, -36 * scale, 0), "module_bind", module.name, button=3, tooltip="绑定按键")
            self.add_hotspot(key_box, "module_bind", module.name, tooltip="绑定按键")
        p.restore()

    def draw_settings_area(self, p: QPainter, rect: QRectF, module: Module, scale: float) -> None:
        rounded_rect(p, rect, 4 * scale, color("#1a1a20", 0.92), color("#ffffff", 0.08))
        header = QRectF(rect.left() + 12 * scale, rect.top() + 9 * scale, rect.width() - 24 * scale, 29 * scale)
        self.draw_text(p, header.adjusted(0, 0, -72 * scale, 0), module.name, self.font("poppins_bold", 13.5 * scale), THEME.text)
        self.draw_toggle_switch(p, QRectF(rect.right() - 57 * scale, rect.top() + 13 * scale, 36 * scale, 16 * scale), module.enabled, ("module_toggle", module.name))

        content = rect.adjusted(10 * scale, 43 * scale, -10 * scale, -10 * scale)
        p.save()
        p.setClipRect(content)
        scroll = self.settings_scroll.get(module.name, 0.0)
        y = content.top() - scroll
        y = self.draw_bind_row(p, QRectF(content.left(), y, content.width(), 27 * scale), module, scale, content)
        for setting in module.settings:
            y = self.draw_setting_row(p, setting, QRectF(content.left(), y, content.width(), 30 * scale), scale)
        if not module.settings:
            self.draw_text(p, QRectF(content.left(), y, content.width(), 44 * scale), module.description or "No settings", self.font("poppins_regular", 8.4 * scale), THEME.muted)
        p.restore()

    def draw_bind_row(self, p: QPainter, rect: QRectF, module: Module, scale: float, clip_rect: QRectF | None = None, compact: bool = False) -> float:
        visible = clip_rect is None or self.rect_visible(rect, clip_rect)
        if visible:
            content_rect = rect.adjusted(0, 2 * scale, 0, -3 * scale)
            rounded_rect(p, content_rect, 5 * scale, color("#ffffff", 0.03 if compact else 0.035), color("#ffffff", 0.055))
            if compact or rect.width() < 160 * scale:
                box_w = min(74.0 * scale, max(48.0 * scale, content_rect.width() * 0.52))
                box = QRectF(content_rect.right() - box_w - 6 * scale, content_rect.top() + 4 * scale, box_w, max(13.0 * scale, content_rect.height() - 8 * scale))
                label_rect = QRectF(content_rect.left() + 7 * scale, content_rect.top(), max(18.0 * scale, box.left() - content_rect.left() - 11 * scale), content_rect.height())
                self.draw_text(p, label_rect, "Keybind", self.font("poppins_regular", 7.0 * scale), THEME.muted)
                key_text = self.compact_keybind(module.keybind)
            else:
                self.draw_text(p, rect.adjusted(8 * scale, 0, -112 * scale, 0), "Keybind", self.font("poppins_regular", 8.4 * scale), THEME.muted)
                box = QRectF(rect.right() - 103 * scale, rect.top() + 5 * scale, 96 * scale, 18 * scale)
                key_text = module.keybind
            rounded_rect(p, box, 5 * scale, color("#ffffff", 0.055), color("#ffffff", 0.10))
            self.draw_text(p, box, key_text, self.font("poppins_medium", (6.9 if compact else 7.8) * scale), THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(box, "module_bind", module.name, tooltip="打开按键绑定覆盖层")
        return rect.bottom()

    def draw_setting_row(self, p: QPainter, setting: Setting, rect: QRectF, scale: float, compact: bool = False) -> float:
        if compact:
            return self.draw_compact_setting_row(p, setting, rect, scale)
        row_h = rect.height()
        content_rect = rect.adjusted(0, 2 * scale, 0, -3 * scale)
        rounded_rect(p, content_rect, 5 * scale, color("#ffffff", 0.03 if not compact else 0.02))
        label_font = self.font("poppins_regular", (8.2 if not compact else 7.4) * scale)
        value_font = self.font("poppins_medium", (7.8 if not compact else 7.0) * scale)
        self.draw_text(p, content_rect.adjusted(8 * scale, 0, -120 * scale, 0), setting.name, label_font, color("#d7d7dc"))
        if setting.kind == "bool":
            self.draw_toggle_switch(p, QRectF(content_rect.right() - 39 * scale, content_rect.top() + 6 * scale, 30 * scale, 13 * scale), bool(setting.value), ("setting_toggle", setting))
        elif setting.kind == "number":
            value = setting.display_value()
            self.draw_text(p, QRectF(content_rect.right() - 78 * scale, content_rect.top(), 70 * scale, content_rect.height() * 0.48), value, value_font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            bar = QRectF(content_rect.left() + content_rect.width() * 0.42, content_rect.bottom() - 9 * scale, content_rect.width() * 0.52, 5 * scale)
            draw_bar(p, bar, setting.ratio(), THEME.pink, color("#ffffff", 0.17), 2.5 * scale)
            self.add_hotspot(bar.adjusted(-4 * scale, -7 * scale, 4 * scale, 7 * scale), "setting_slider", setting)
        elif setting.kind == "mode":
            box = QRectF(content_rect.right() - 112 * scale, content_rect.top() + 5 * scale, 103 * scale, content_rect.height() - 10 * scale)
            rounded_rect(p, box, 5 * scale, color("#303036", 0.95), color("#ffffff", 0.08))
            self.draw_text(p, box.adjusted(6 * scale, 0, -6 * scale, 0), str(setting.value), value_font, THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(box, "setting_mode_next", setting, tooltip="左键/右键循环切换")
        elif setting.kind == "action":
            button = content_rect.adjusted(8 * scale, 4 * scale, -8 * scale, -4 * scale)
            rounded_rect(p, button, 5 * scale, color("#ffffff", 0.92), color("#56fff0", 0.35))
            self.draw_text(p, button, setting.name, value_font, color("#090a0d"), Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(button, "config_action", setting.name, tooltip=setting.description)
        return rect.bottom()

    def draw_compact_setting_row(self, p: QPainter, setting: Setting, rect: QRectF, scale: float) -> float:
        content_rect = rect.adjusted(0, 1.5 * scale, 0, -2.0 * scale)
        if content_rect.height() < 5 * scale:
            return rect.bottom()
        rounded_rect(p, content_rect, 5 * scale, color("#ffffff", 0.022), color("#ffffff", 0.040))
        label_font = self.font("poppins_regular", 7.0 * scale)
        value_font = self.font("poppins_medium", 6.9 * scale)

        if setting.kind == "bool":
            switch = QRectF(content_rect.right() - 37 * scale, content_rect.top() + 5 * scale, 29 * scale, 13 * scale)
            label_rect = QRectF(content_rect.left() + 7 * scale, content_rect.top(), max(16.0 * scale, switch.left() - content_rect.left() - 11 * scale), content_rect.height())
            self.draw_text(p, label_rect, setting.name, label_font, color("#d7d7dc"))
            self.draw_toggle_switch(p, switch, bool(setting.value), ("setting_toggle", setting))
        elif setting.kind == "number":
            value = setting.display_value()
            bar = QRectF(content_rect.left() + 7 * scale, content_rect.bottom() - 6 * scale, content_rect.width() - 14 * scale, 4.2 * scale)
            value_w = min(58.0 * scale, max(36.0 * scale, content_rect.width() * 0.36))
            label_rect = QRectF(content_rect.left() + 7 * scale, content_rect.top() - 1 * scale, max(20.0 * scale, content_rect.width() - value_w - 18 * scale), content_rect.height() * 0.58)
            value_rect = QRectF(content_rect.right() - value_w - 7 * scale, content_rect.top() - 1 * scale, value_w, content_rect.height() * 0.58)
            self.draw_text(p, label_rect, setting.name, label_font, color("#d7d7dc"))
            self.draw_text(p, value_rect, value, value_font, THEME.text, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            draw_bar(p, bar, setting.ratio(), THEME.pink, color("#ffffff", 0.17), 2.1 * scale)
            self.add_hotspot(bar.adjusted(-5 * scale, -8 * scale, 5 * scale, 8 * scale), "setting_slider", setting)
        elif setting.kind == "mode":
            box_w = min(78.0 * scale, max(50.0 * scale, content_rect.width() * 0.55))
            box = QRectF(content_rect.right() - box_w - 6 * scale, content_rect.top() + 4 * scale, box_w, max(13.0 * scale, content_rect.height() - 8 * scale))
            label_rect = QRectF(content_rect.left() + 7 * scale, content_rect.top(), max(18.0 * scale, box.left() - content_rect.left() - 11 * scale), content_rect.height())
            self.draw_text(p, label_rect, setting.name, label_font, color("#d7d7dc"))
            rounded_rect(p, box, 5 * scale, color("#303036", 0.90), color("#ffffff", 0.08))
            self.draw_text(p, box.adjusted(4 * scale, 0, -4 * scale, 0), str(setting.value), value_font, THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(box, "setting_mode_next", setting, tooltip="左键/右键循环切换")
        elif setting.kind == "action":
            button = content_rect.adjusted(6 * scale, 4 * scale, -6 * scale, -4 * scale)
            rounded_rect(p, button, 5 * scale, color("#ffffff", 0.90), color("#56fff0", 0.28))
            self.draw_text(p, button, setting.name, value_font, color("#090a0d"), Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(button, "config_action", setting.name, tooltip=setting.description)
        return rect.bottom()

    def draw_search_bar(self, p: QPainter, rect: QRectF, scale: float) -> None:
        focus_v = self.anim.value("panel.search.focus", 1.0 if self.search_focus else 0.0, self.anim_duration(0.18), Easings.ease_out_quad)
        rounded_rect(p, rect, rect.height() / 2, color("#16161b", 0.92 + 0.06 * focus_v), color("#ffffff", 0.12 + 0.18 * focus_v))
        text = self.search_query if self.search_query else "Search modules..."
        self.draw_text(p, rect.adjusted(12 * scale, 0, -12 * scale, 0), text, self.font("poppins_regular", 7.8 * scale), THEME.text if self.search_query else color("#77777d"))
        self.add_hotspot(rect, "search_focus", None)

    def draw_old_gui(self, p: QPainter, width: int, height: int, _alpha: float) -> None:
        scale = self.get_gui_scale_for_bounds(width, height, 6 * 146.0 + 5 * 12.0, 560.0, 32.0, 80.0)
        panel_w = 146.0 * scale
        row_h = 20.0 * scale
        for i, category in enumerate(CATEGORIES):
            panel_data = self.old_panels[category]
            if not bool(panel_data.get("dragged", False)):
                panel_data["x"] = 20.0 + i * (panel_w + 12.0 * scale)
                panel_data["y"] = max(66.0, 24.0 * min(scale, 1.35))
            x = panel_data["x"]
            y = panel_data["y"]
            header = QRectF(x, y, panel_w, row_h)
            glow_rect(p, header, 4 * scale, 0.12)
            rounded_rect(p, header, 3 * scale, color("#101014", 0.94), color("#ffffff", 0.12))
            self.draw_text(p, header.adjusted(8 * scale, 0, -8 * scale, 0), f"{self.category_icon(category)}  {category}", self.font("poppins_bold", 8.2 * scale), THEME.text)
            self.add_hotspot(header, "old_drag", category, button=1, tooltip="拖动面板")
            self.add_hotspot(header, "old_expand", category, button=3, tooltip="展开分类")

            expanded = bool(panel_data["expanded"])
            open_v = self.anim.value(f"old.panel.{category}", 1.0 if expanded else 0.0, self.anim_duration(0.34), Easings.ease_out_pow5)
            if open_v <= 0.01:
                continue
            yy = y + row_h
            p.save()
            modules = self.registry.by_category(category)
            for idx, module in enumerate(modules):
                row_reveal = self.waterfall_reveal(open_v, idx, len(modules))
                if row_reveal <= 0.025:
                    continue
                row_clip = QRectF(x, yy, panel_w, row_h * row_reveal)
                row = QRectF(x + (1.0 - row_reveal) * 5.0 * scale, yy, panel_w - (1.0 - row_reveal) * 5.0 * scale, row_h)
                p.save()
                p.setClipRect(row_clip.adjusted(-2 * scale, -2 * scale, 2 * scale, 2 * scale))
                p.setOpacity(p.opacity() * row_reveal)
                enabled_v = self.anim.value(f"old.enabled.{module.name}", 1.0 if module.enabled else 0.0, self.anim_duration(0.18), Easings.ease_out_cubic)
                fill = color("#ffffff", 0.82 * enabled_v) if module.enabled else color("#17171c", 0.95)
                rounded_rect(p, row, 0, fill, color("#ffffff", 0.08))
                fg = color("#161616") if module.enabled else color("#bbbbc0")
                self.draw_text(p, row.adjusted(8 * scale, 0, -32 * scale, 0), module.name, self.font("poppins_regular", 7.4 * scale), fg)
                key_box = QRectF(row.right() - 28 * scale, row.top() + 3 * scale, 23 * scale, row.height() - 6 * scale)
                self.draw_text(p, key_box, "K", self.font("poppins_bold", 6.5 * scale), color("#161616") if module.enabled else THEME.muted, Qt.AlignmentFlag.AlignCenter)
                self.add_hotspot(row.adjusted(0, 0, -28 * scale, 0), "module_toggle", module.name, button=1, tooltip=module.description)
                self.add_hotspot(row.adjusted(0, 0, -28 * scale, 0), "module_expand", module.name, button=3, tooltip="展开设置")
                self.add_hotspot(key_box, "module_bind", module.name, tooltip="绑定按键")
                p.restore()
                yy += row_h * row_reveal
                exp = self.anim.value(f"module.expand.{module.name}", 1.0 if module.name in self.module_expanded else 0.0, self.anim_duration(0.36), Easings.ease_out_pow5)
                if exp > 0.01:
                    yy = self.draw_inline_settings(p, module, QRectF(x + 5 * scale, yy, panel_w - 10 * scale, 0), scale, compact=True, max_rows=6, reveal=exp)
            p.restore()

    def draw_new_gui(self, p: QPainter, width: int, height: int, _alpha: float) -> None:
        base_w = len(CATEGORIES) * 120.0 + (len(CATEGORIES) - 1) * 8.0
        scale = self.get_gui_scale_for_bounds(width, height, base_w, 430.0, 56.0, 118.0)
        gap = 8.0 * scale
        panel_w = 120.0 * scale
        total = len(CATEGORIES) * panel_w + (len(CATEGORIES) - 1) * gap
        start_x = width / 2 - total / 2
        y = max(62.0, 36.0 * scale)
        max_h = min(430.0 * scale, max(230.0 * scale, height - y - 58.0))
        for i, category in enumerate(CATEGORIES):
            x = start_x + i * (panel_w + gap)
            modules = self.registry.by_category(category)
            content_h = 25 * scale + sum(self.module_draw_height(module, scale, compact=True) for module in modules)
            panel_h = min(max_h, max(72.0 * scale, content_h + 8 * scale))
            rect = QRectF(x, y, panel_w, panel_h)
            glow_rect(p, rect, 8 * scale, 0.16)
            rounded_rect(p, rect, 7 * scale, color("#151518", 0.96), color("#ffffff", 0.11))
            top_line = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
            top_line.setColorAt(0.0, THEME.pink)
            top_line.setColorAt(1.0, THEME.cyan)
            p.fillRect(QRectF(rect.left() + 3 * scale, rect.top() + 1 * scale, rect.width() - 6 * scale, 2 * scale), top_line)
            self.draw_text(p, QRectF(rect.left() + 8 * scale, rect.top() + 3 * scale, rect.width() - 16 * scale, 22 * scale), category, self.font("poppins_bold", 8.0 * scale), THEME.text)
            self.add_hotspot(rect.adjusted(0, 0, 0, -rect.height() + 24 * scale), "select_category", category, tooltip=category)
            p.save()
            clip = rect.adjusted(0, 26 * scale, 0, -4 * scale)
            p.setClipRect(clip)
            scroll = float(self.new_panels[category]["scroll"])
            yy = rect.top() + 27 * scale - scroll
            for module in modules:
                yy = self.draw_new_module_block(p, module, QRectF(rect.left(), yy, rect.width(), 0), scale)
            p.restore()

    def draw_new_module_block(self, p: QPainter, module: Module, origin: QRectF, scale: float) -> float:
        row = QRectF(origin.left() + 4 * scale, origin.top(), origin.width() - 8 * scale, 21 * scale)
        enabled_v = self.anim.value(f"new.enabled.{module.name}", 1.0 if module.enabled else 0.0, self.anim_duration(0.18), Easings.ease_out_cubic)
        fill = color("#22262b", 0.88 * enabled_v) if module.enabled else color("#17171a", 0.95)
        rounded_rect(p, row, 5 * scale, fill, color("#ffffff", 0.06))
        self.draw_text(p, row.adjusted(6 * scale, 0, -28 * scale, 0), module.name, self.font("poppins_regular", 7.2 * scale), THEME.text if module.enabled else color("#bbbbbf"))
        key_box = QRectF(row.right() - 24 * scale, row.top() + 3 * scale, 19 * scale, row.height() - 6 * scale)
        rounded_rect(p, key_box, 4 * scale, color("#ffffff", 0.04), color("#ffffff", 0.06))
        self.draw_text(p, key_box, "K", self.font("poppins_bold", 6.2 * scale), THEME.muted, Qt.AlignmentFlag.AlignCenter)
        self.add_hotspot(row.adjusted(0, 0, -24 * scale, 0), "module_toggle", module.name, button=1, tooltip=module.description)
        self.add_hotspot(row.adjusted(0, 0, -24 * scale, 0), "module_expand", module.name, button=3, tooltip="展开设置")
        self.add_hotspot(key_box, "module_bind", module.name, tooltip="绑定按键")
        yy = row.bottom()
        exp = self.anim.value(f"new.expand.{module.name}", 1.0 if module.name in self.module_expanded else 0.0, self.anim_duration(0.34), Easings.ease_out_pow5)
        if exp > 0.01:
            yy = self.draw_inline_settings(p, module, QRectF(row.left() + 4 * scale, yy + 2 * scale, row.width() - 8 * scale, 0), scale, compact=True, max_rows=5, reveal=exp)
        return yy + 2 * scale

    def draw_dropdown_gui(self, p: QPainter, width: int, height: int, _alpha: float) -> None:
        scale = self.get_gui_scale_for_bounds(width, height, 6 * 168.0 + 5 * 10.0, 500.0, 38.0, 92.0)
        gap = 10.0 * scale
        available = width - 38.0
        panel_w = max(148.0 * scale, min(226.0 * scale, (available - gap * (len(CATEGORIES) - 1)) / len(CATEGORIES)))
        total = len(CATEGORIES) * panel_w + gap * (len(CATEGORIES) - 1)
        x0 = max(18.0, width / 2 - total / 2)
        y0 = max(60.0, 52.0 * scale)
        for i, category in enumerate(CATEGORIES):
            x = x0 + i * (panel_w + gap)
            self.draw_dropdown_panel(p, category, QRectF(x, y0, panel_w, 0), scale)

    def draw_dropdown_panel(self, p: QPainter, category: str, origin: QRectF, scale: float) -> None:
        modules = self.registry.by_category(category)
        expanded = bool(self.dropdown_panels[category]["expanded"])
        open_v = self.anim.value(f"drop.open.{category}", 1.0 if expanded else 0.0, self.anim_duration(0.2), Easings.ease_out_pow4)
        header_h = 32.0 * scale
        body_h = min(430.0 * scale, open_v * (sum(self.module_draw_height(module, scale, compact=False) for module in modules) + 8 * scale))
        rect = QRectF(origin.left(), origin.top(), origin.width(), header_h + body_h)
        glow_rect(p, rect, 8 * scale, 0.13)
        rounded_rect(p, rect, 8 * scale, color("#242424", 0.96), color("#ffffff", 0.09))
        self.draw_text(p, QRectF(rect.left() + 12 * scale, rect.top(), rect.width() - 24 * scale, header_h), f"{self.category_icon(category)}  {category}", self.font("poppins_bold", 11.0 * scale), THEME.text)
        self.add_hotspot(QRectF(rect.left(), rect.top(), rect.width(), header_h), "dropdown_expand", category, button=3, tooltip="展开分类")
        if open_v <= 0.01:
            return
        p.save()
        p.setClipRect(rect.adjusted(0, header_h, 0, 0))
        yy = rect.top() + header_h
        for idx, module in enumerate(modules):
            row_reveal = self.waterfall_reveal(open_v, idx, len(modules))
            if row_reveal <= 0.025:
                continue
            row_clip = QRectF(rect.left(), yy, rect.width(), 24 * scale * row_reveal)
            row = QRectF(rect.left() + (1.0 - row_reveal) * 6.0 * scale, yy, rect.width() - (1.0 - row_reveal) * 6.0 * scale, 24 * scale)
            p.save()
            p.setClipRect(row_clip.adjusted(0, -1 * scale, 0, 2 * scale))
            p.setOpacity(p.opacity() * row_reveal)
            if module.enabled:
                grad = QLinearGradient(row.left(), row.top(), row.right(), row.top())
                grad.setColorAt(0.0, accent_color(idx, time.perf_counter()))
                grad.setColorAt(1.0, accent_color(idx + 4, time.perf_counter()))
                p.fillRect(row, grad)
            else:
                rounded_rect(p, row, 0, color("#101412", 0.98), color("#ffffff", 0.06))
            self.draw_text(p, row.adjusted(12 * scale, 0, -34 * scale, 0), module.name, self.font("poppins_medium", 8.3 * scale), THEME.text)
            key_box = QRectF(row.right() - 29 * scale, row.top() + 4 * scale, 23 * scale, row.height() - 8 * scale)
            self.draw_text(p, key_box, "K", self.font("poppins_bold", 6.8 * scale), THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(row.adjusted(0, 0, -31 * scale, 0), "module_toggle", module.name, button=1, tooltip=module.description)
            self.add_hotspot(row.adjusted(0, 0, -31 * scale, 0), "module_expand", module.name, button=3, tooltip="展开设置")
            self.add_hotspot(key_box, "module_bind", module.name, tooltip="绑定按键")
            p.restore()
            yy += row.height() * row_reveal
            exp = self.anim.value(f"drop.expand.{module.name}", 1.0 if module.name in self.module_expanded else 0.0, self.anim_duration(0.34), Easings.ease_out_pow5)
            if exp > 0.01:
                yy = self.draw_inline_settings(p, module, QRectF(row.left() + 9 * scale, yy, row.width() - 18 * scale, 0), scale, compact=False, max_rows=6, reveal=exp)
        p.restore()

    def draw_inline_settings(self, p: QPainter, module: Module, origin: QRectF, scale: float, compact: bool, max_rows: int, reveal: float = 1.0) -> float:
        yy = origin.top()
        rows: list[tuple[str, Setting | None, float]] = [("bind", None, self.inline_bind_height(scale, compact))]
        rows.extend(("setting", setting, self.inline_setting_height(setting, scale, compact)) for setting in module.settings[:max_rows])
        for index, (kind, setting, row_h) in enumerate(rows):
            row_v = self.waterfall_reveal(reveal, index, len(rows))
            if row_v <= 0.025:
                continue
            clip_h = row_h * row_v
            slide = (1.0 - row_v) * (7.0 if compact else 9.0) * scale
            row_rect = QRectF(origin.left() + slide, yy, origin.width() - slide, row_h)
            clip = QRectF(origin.left(), yy, origin.width(), clip_h)
            p.save()
            p.setClipRect(clip.adjusted(-2 * scale, -2 * scale, 2 * scale, 3 * scale))
            p.setOpacity(p.opacity() * row_v)
            if kind == "bind":
                self.draw_bind_row(p, row_rect, module, scale, compact=compact)
            elif setting is not None:
                self.draw_setting_row(p, setting, row_rect, scale, compact=compact)
            p.restore()
            yy += clip_h
        return yy

    def module_draw_height(self, module: Module, scale: float, compact: bool) -> float:
        row_h = 21.0 * scale if compact else 24.0 * scale
        exp = self.anim.value(f"height.expand.{module.name}", 1.0 if module.name in self.module_expanded else 0.0, self.anim_duration(0.36), Easings.ease_out_pow5)
        settings_total = self.inline_bind_height(scale, compact) + sum(self.inline_setting_height(setting, scale, compact) for setting in module.settings[:6])
        return row_h + exp * settings_total + 2 * scale

    def inline_bind_height(self, scale: float, compact: bool) -> float:
        return (21.0 if compact else 24.0) * scale

    def inline_setting_height(self, setting: Setting, scale: float, compact: bool) -> float:
        if not compact:
            return 24.0 * scale
        if setting.kind == "number":
            return 26.0 * scale
        if setting.kind == "mode":
            return 23.0 * scale
        return 22.0 * scale

    def waterfall_reveal(self, reveal: float, index: int, total: int) -> float:
        if reveal >= 0.999 or total <= 1:
            return max(0.0, min(1.0, reveal))
        delay = min(0.42, index * 0.065)
        span = max(0.24, 1.0 - delay)
        t = (reveal - delay) / span
        return Easings.ease_out_pow5(t)

    def compact_keybind(self, keybind: str) -> str:
        aliases = {
            "RightShift": "RShift",
            "LeftShift": "LShift",
            "Control": "Ctrl",
            "Escape": "Esc",
        }
        return aliases.get(keybind, keybind)

    def draw_mode_switcher(self, p: QPainter, width: int, height: int) -> None:
        gap = 6.0
        margin = 18.0
        chip_w = min(128.0, (width - margin * 2 - gap * (len(GUI_MODES) - 1)) / len(GUI_MODES))
        total = len(GUI_MODES) * chip_w + (len(GUI_MODES) - 1) * gap
        x = max(margin, width - total - margin)
        y = max(12.0, height - 38.0)
        bg = QRectF(x - 8, y - 6, total + 16, 30)
        rounded_rect(p, bg, 8, color("#08080a", 0.82), color("#ffffff", 0.10))
        for i, mode in enumerate(GUI_MODES):
            rect = QRectF(x + i * (chip_w + gap), y, chip_w, 18)
            active = mode == self.mode_manager.mode
            active_v = self.anim.value(f"gui.mode.{mode}", 1.0 if active else 0.0, self.anim_duration(0.16), Easings.ease_out_quad)
            fill = color("#ffffff", 0.92 * active_v) if active_v > 0.01 else color("#1a1c22", 0.95)
            rounded_rect(p, rect, 5, fill, color("#ffffff", 0.10))
            text = mode.replace("Polar", "P ")
            self.draw_text(p, rect.adjusted(5, 0, -5, 0), text, self.font("poppins_medium", 7.1), color("#08080a") if active else THEME.text, Qt.AlignmentFlag.AlignCenter)
            self.add_hotspot(rect, "gui_mode", mode)

    def draw_toasts(self, p: QPainter, width: int, _height: int) -> None:
        now = time.perf_counter()
        self.toasts = [toast for toast in self.toasts if now - toast["created"] < 3.0]
        for i, toast in enumerate(self.toasts[-5:]):
            age = now - toast["created"]
            fade = 1.0 if age < 2.35 else max(0.0, 1.0 - (age - 2.35) / 0.65)
            slide = Easings.ease_out_expo(min(1.0, age / self.anim_duration(0.22)))
            msg = toast["message"]
            kind, main = self.dynamic_message_kind(msg)
            title = self.dynamic_message_title(msg, kind)
            title_font = self.font("poppins_bold", 7.7)
            body_font = self.font("poppins_regular", 7.3)
            tw = max(210.0, min(430.0, max(QFontMetricsF(title_font).horizontalAdvance(title), QFontMetricsF(body_font).horizontalAdvance(msg)) + 58.0))
            if self.gui_visible:
                y = self.height() - 82.0 - i * 42.0 + (1.0 - slide) * 18.0
            else:
                y = 74.0 + i * 42.0 - (1.0 - slide) * 18.0
            rect = QRectF(width / 2 - tw / 2, y, tw, 36.0)
            p.save()
            p.setOpacity(fade)
            rounded_rect(p, rect.adjusted(-10, -10, 10, 10), 16, color("#000000", 0.035), None)
            rounded_rect(p, rect, 10, color("#050506", 0.74), color("#ffffff", 0.08))
            icon_rect = QRectF(rect.left() + 7, rect.top() + 6, 24, 24)
            icon_v = self.anim.value(f"toast.icon.{id(toast)}", 1.0 if age < 2.7 else 0.0, self.anim_duration(0.24), Easings.ease_out_expo, initial=0.0)
            self.draw_dynamic_island_icon(p, icon_rect, kind, main, icon_v)
            self.draw_text(p, QRectF(rect.left() + 39, rect.top() + 5, rect.width() - 48, 13), title, title_font, THEME.text)
            self.draw_text(p, QRectF(rect.left() + 39, rect.top() + 18, rect.width() - 48, 13), msg, body_font, THEME.muted)
            progress = max(0.0, 1.0 - age / 3.0)
            bar = QRectF(rect.left() + 8, rect.bottom() - 4.0, rect.width() - 16, 2.0)
            draw_bar(p, bar, progress, main, color("#ffffff", 0.10), 1.0)
            p.restore()

    def draw_keybind_overlay(self, p: QPainter, width: int, height: int) -> None:
        module = self.binding_module
        if module is None:
            return
        active = self.anim.value("keybind.overlay", 1.0, self.anim_duration(0.18), Easings.ease_out_pow3, initial=0.0)
        p.save()
        p.setOpacity(active)
        p.fillRect(QRectF(0, 0, width, height), color("#000000", 0.58))
        scale = 0.96 + 0.04 * active
        rect = QRectF(width / 2 - 200 * scale, height / 2 - 90 * scale, 400 * scale, 180 * scale)
        glow_rect(p, rect, 12 * scale, 0.46)
        rounded_rect(p, rect, 11 * scale, color("#0b0b0f", 0.97), color("#ffffff", 0.16))
        self.draw_text(p, QRectF(rect.left(), rect.top() + 28 * scale, rect.width(), 24 * scale), "KEYBIND", self.font("poppins_bold", 18 * scale), THEME.text, Qt.AlignmentFlag.AlignCenter)
        self.draw_text(p, QRectF(rect.left(), rect.top() + 67 * scale, rect.width(), 21 * scale), module.name, self.font("poppins_medium", 11 * scale), THEME.cyan, Qt.AlignmentFlag.AlignCenter)
        self.draw_text(p, QRectF(rect.left(), rect.top() + 98 * scale, rect.width(), 20 * scale), "Press any key to bind", self.font("poppins_regular", 9 * scale), THEME.muted, Qt.AlignmentFlag.AlignCenter)
        phase = time.perf_counter() * 4.6
        for i in range(3):
            dot_alpha = 0.24 + 0.64 * (0.5 + 0.5 * math.sin(phase + i * 0.7))
            p.setBrush(color("#ffffff", dot_alpha))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(rect.center().x() - 16 * scale + i * 16 * scale, rect.bottom() - 37 * scale), 3.3 * scale, 3.3 * scale)
        self.draw_text(p, QRectF(rect.left(), rect.bottom() - 24 * scale, rect.width(), 14 * scale), "ESC 清空绑定", self.font("poppins_regular", 7.4 * scale), color("#888890"), Qt.AlignmentFlag.AlignCenter)
        p.restore()

    def draw_toggle_switch(self, p: QPainter, rect: QRectF, enabled: bool, action: tuple[str, Any] | None = None) -> None:
        t = self.anim.value(f"toggle.{id(action[1]) if action else id(rect)}", 1.0 if enabled else 0.0, self.anim_duration(0.18), Easings.ease_out_cubic)
        fill = QColor(
            round(THEME.gray.red() + (THEME.green.red() - THEME.gray.red()) * t),
            round(THEME.gray.green() + (THEME.green.green() - THEME.gray.green()) * t),
            round(THEME.gray.blue() + (THEME.green.blue() - THEME.gray.blue()) * t),
            220,
        )
        rounded_rect(p, rect, rect.height() / 2, fill)
        knob = rect.height() - 4
        kx = rect.left() + 2 + (rect.width() - knob - 4) * t
        rounded_rect(p, QRectF(kx, rect.top() + 2, knob, knob), knob / 2, THEME.text)
        if action:
            self.add_hotspot(rect.adjusted(-4, -4, 4, 4), action[0], action[1])

    def draw_pill(self, p: QPainter, point: QPointF, text: str, c: QColor) -> None:
        font = self.font("poppins_medium", 8.0)
        p.setFont(font)
        w = max(92.0, QFontMetricsF(font).horizontalAdvance(text) + 18.0)
        rect = QRectF(point.x(), point.y(), w, 20)
        rounded_rect(p, rect, 7, color("#090a0d", 0.86), c, 1.1)
        self.draw_text(p, rect.adjusted(8, 0, -8, 0), text, font, c)

    def draw_tooltip(self, p: QPainter, point: QPointF, text: str) -> None:
        if not text:
            return
        font = self.font("poppins_regular", 8.0)
        p.setFont(font)
        text = self.elide(p, font, text, 340)
        tw = QFontMetricsF(font).horizontalAdvance(text) + 18
        x = min(point.x() + 14, self.width() - tw - 10)
        y = min(point.y() + 18, self.height() - 30)
        rect = QRectF(x, y, tw, 24)
        rounded_rect(p, rect, 6, color("#050506", 0.92), color("#ffffff", 0.14))
        self.draw_text(p, rect.adjusted(9, 0, -9, 0), text, font, THEME.text)

    def add_hotspot(self, rect: QRectF, action: str, payload: Any = None, button: int | None = None, tooltip: str = "") -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return
        self.hotspots.append(Hotspot(QRectF(rect), action, payload, button, tooltip))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.mark_ui_interactive(0.50)
        if self.binding_module is not None:
            return
        button = self.qt_button(event)
        point = event.position()
        if button == 1:
            self.liquid_press_point = QPointF(point)
            self.liquid_press_until = time.perf_counter() + 0.12
        for hotspot in reversed(self.hotspots):
            if hotspot.button not in (None, button):
                continue
            if hotspot.rect.contains(point):
                self.dispatch(hotspot.action, hotspot.payload, point, button, hotspot.rect)
                return
        if button == 1:
            self.search_focus = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.hover_point = event.position()
        self.mark_ui_interactive(0.35)
        self.frame_scheduler.force()
        self.update()
        self.hover_tooltip = ""
        for hotspot in reversed(self.hotspots):
            if hotspot.rect.contains(self.hover_point):
                self.hover_tooltip = hotspot.tooltip
                break
        # Inject Mouse 视角：中心锁定（FPS 式）。光标锁在窗口中心，相对位移注入游戏。
        # 不抢 UI 时：ClickGUI / 启动页 / 拖动 / 绑键 一律不注入。
        if self._mouse_look_warp_skip:
            self._mouse_look_warp_skip = False
        elif (
            not self.home_visible
            and not self.gui_visible
            and self.drag is None
            and self.binding_module is None
            and self.isActiveWindow()
            and self.gestalt_injector.enabled
        ):
            gc = self.registry.get("GestaltControl")
            if (
                gc
                and gc.enabled
                and self.setting_bool(gc, "Inject Enabled", False)
                and self.setting_bool(gc, "Inject Mouse", False)
            ):
                # 相对窗口中心的位移 → 游戏视角
                center_local = QPointF(self.width() * 0.5, self.height() * 0.5)
                center_global = self.mapToGlobal(center_local.toPoint())
                gpos = event.globalPosition().toPoint()
                dx = float(gpos.x() - center_global.x())
                dy = float(gpos.y() - center_global.y())
                if abs(dx) >= 1.0 or abs(dy) >= 1.0:
                    if abs(dx) < 300 and abs(dy) < 300:
                        self.gestalt_injector.mouse_move(dx, dy, restore_cursor=False)
                        self.state.control_inject_status = self.gestalt_injector.status
                    # 锁回中心（会再进一次 mouseMove，用 skip 吃掉）
                    self._mouse_look_warp_skip = True
                    from PySide6.QtGui import QCursor

                    QCursor.setPos(center_global)
                self._last_mouse_inject_pos = QPointF(event.position())
            else:
                self._last_mouse_inject_pos = None
        else:
            self._last_mouse_inject_pos = None
        if self.drag is None:
            return
        point = event.position()
        if self.drag["type"] == "slider":
            setting: Setting = self.drag["setting"]
            rect: QRectF = self.drag["rect"]
            setting.set_from_ratio((point.x() - rect.left()) / max(1.0, rect.width()))
            self.after_setting_changed(setting)
        elif self.drag["type"] == "old_panel":
            panel = self.old_panels[self.drag["category"]]
            panel["dragged"] = True
            panel["x"] = point.x() - self.drag["offset_x"]
            panel["y"] = point.y() - self.drag["offset_y"]

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        if self.drag and self.drag.get("type") == "old_panel":
            self.auto_save_config()
        self.drag = None

    def wheelEvent(self, event: QWheelEvent) -> None:
        point = event.position()
        delta = -event.angleDelta().y() / 120.0 * 28.0
        if self.mode_manager.mode == "PolarPanel" and self.panel_settings_rect.contains(point):
            key = self.selected_module.name
            self.settings_scroll[key] = max(0.0, self.settings_scroll.get(key, 0.0) + delta)
            return
        if self.mode_manager.mode == "PolarNew":
            for category, data in self.new_panels.items():
                for hotspot in self.hotspots:
                    if hotspot.action == "select_category" and hotspot.payload == category and hotspot.rect.adjusted(0, 0, 0, 430).contains(point):
                        data["scroll"] = max(0.0, float(data["scroll"]) + delta)
                        return

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """保证窗口激活时，所有快捷键先到 POLAR（不依赖子控件焦点）。"""
        et = event.type()
        if et in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and isinstance(event, QKeyEvent):
            if not self.isActiveWindow():
                return super().eventFilter(watched, event)
            # 避免重复：只处理发往本窗口树的事件
            if watched is self or (isinstance(watched, QWidget) and self.isAncestorOf(watched)):
                if et == QEvent.Type.KeyPress and not event.isAutoRepeat():
                    if self._route_key_press(event):
                        event.accept()
                        return True
                if et == QEvent.Type.KeyRelease and not event.isAutoRepeat():
                    if self._route_key_release(event):
                        event.accept()
                        return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._route_key_press(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if self._route_key_release(event):
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _route_key_press(self, event: QKeyEvent) -> bool:
        """统一键盘路由。返回 True 表示已处理。"""
        if event.isAutoRepeat():
            return False
        if self.binding_module is not None:
            self.handle_keybind_event(event)
            return True
        if self.home_visible and self.wallpaper_picker_visible:
            if event.key() == Qt.Key.Key_Escape:
                self.wallpaper_picker_visible = False
                return True
            return False
        if self.home_visible:
            if self.handle_startup_key(event):
                return True
            return False
        key_name = self.key_name(event)
        if self.search_focus:
            if event.key() == Qt.Key.Key_Backspace:
                self.search_query = self.search_query[:-1]
            elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.search_focus = False
            elif event.key() == Qt.Key.Key_Escape:
                self.search_focus = False
            elif event.text() and event.text().isprintable():
                self.search_query += event.text()
            return True
        # Tab：按住放大地图（先于模块 toggle，避免变成开关地图）
        if self.handle_tactical_map_hold_event(event, True):
            return True
        # Client 快捷键：R=自瞄、V=小陀螺、RightShift=GUI…
        if self.dispatch_client_hotkey(event, key_name):
            return True
        # L-Shift 等进游戏注入
        if self.handle_control_key_event(event, True):
            return True
        return False

    def _route_key_release(self, event: QKeyEvent) -> bool:
        if event.isAutoRepeat():
            return False
        if self.binding_module is not None or self.search_focus or self.home_visible:
            return False
        if self.handle_tactical_map_hold_event(event, False):
            return True
        if self.handle_control_key_event(event, False):
            return True
        return False

    def client_hotkey_names(self) -> set[str]:
        """只留给 client 的键；其余在 Inject ON 时全部进游戏（含 P/Space/WASD）。

        不要把 space/enter/ctrl 锁死——那会挡住游戏确认/跳跃。
        """
        names = {
            "f7", "f8", "f9", "escape", "rightshift", "tab",
        }
        for module in self.registry.modules:
            # TacticalMap 的 Tab 是 hold 放大，不是 toggle
            if module.name == "TacticalMap":
                continue
            if module.keybind and module.keybind != "None":
                names.add(module.keybind.strip().lower().replace(" ", ""))
        click = self.registry.get("ClickGui")
        if click and click.keybind and click.keybind != "None":
            names.add(click.keybind.strip().lower().replace(" ", ""))
        # 底盘键永不作为 client 保留
        names -= {"w", "a", "s", "d", "shift", "lshift", "leftshift"}
        return names

    def dispatch_client_hotkey(self, event: QKeyEvent, key_name: str) -> bool:
        """处理 client 自用快捷键；命中则绝不注入游戏。"""
        if event.key() == Qt.Key.Key_F7:
            self.toggle_gestalt_inject()
            return True
        if event.key() == Qt.Key.Key_F8 or self.is_right_shift_event(event):
            self.set_gui_visible(not self.gui_visible)
            return True
        if event.key() == Qt.Key.Key_F9:
            mode = self.mode_manager.next_mode()
            self.sync_mode_setting()
            self.toast(f"GUI 模式：{mode}")
            return True
        if event.key() == Qt.Key.Key_Escape and self.gui_visible:
            self.set_gui_visible(False)
            return True
        # 模块绑定：R=自瞄、V=小陀螺…（跳过 TacticalMap，Tab 只放大）
        for module in self.registry.modules:
            if module.name == "TacticalMap":
                continue
            if module.keybind != "None" and self.keybind_matches(module.keybind, key_name, event):
                kn = key_name.strip().upper()
                if kn in ("W", "A", "S", "D", "SHIFT") and self.gestalt_injector.enabled:
                    self.gestalt_injector.set_key(kn, False)
                self.registry.toggle_module(module, source="keybind")
                self.selected_module = module
                self.toast(f"{module.name} {'已启用' if module.enabled else '已关闭'}")
                self.auto_save_config()
                self.activateWindow()
                self.setFocus(Qt.FocusReason.ShortcutFocusReason)
                return True
        return False

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if self.binding_module is not None or self.search_focus:
            return
        if self.handle_tactical_map_hold_event(event, False):
            return
        self.handle_control_key_event(event, False)

    def set_gui_visible(self, visible: bool) -> None:
        """打开 ClickGUI 时暂停键鼠注入并松开已注入按键，保证 client 快捷键/点击可用。"""
        self.gui_visible = visible
        if visible:
            self.gestalt_injector.release_all()
            self._last_mouse_inject_pos = None
            self._inject_paused_for_gui = True
            # 临时关掉 injector.enabled，避免 SendInput 抢焦点
            self.gestalt_injector.configure(
                enabled=False,
                inject_mouse=False,
                keep_client_focus=True,
                mouse_sensitivity=self.get_setting_number(self.registry.get("GestaltControl"), "Mouse Sensitivity", 1.0),
            )
            self._mouse_look_active = False
            self.state.control_inject_status = "paused_gui"
            self.toast("ClickGUI 已打开（注入/钩子已暂停）")
            try:
                self.activateWindow()
                self.setFocus(Qt.FocusReason.ShortcutFocusReason)
            except Exception:
                pass
        else:
            self._inject_paused_for_gui = False
            self.refresh_gestalt_control_runtime()
            self.toast("ClickGUI 已关闭（游戏键→游戏 | 模块键→client）")

    def toggle_gestalt_inject(self) -> None:
        """F7：不打开 GUI 也能开关注入。"""
        module = self.registry.get("GestaltControl")
        if module is None:
            return
        setting = module.get_setting("Inject Enabled")
        if setting is None:
            return
        if not module.enabled:
            module.enabled = True
        setting.value = not bool(setting.value)
        if not setting.value:
            self.gestalt_injector.release_all()
        self.refresh_gestalt_control_runtime()
        self.auto_save_config()
        on = bool(setting.value)
        self.toast(
            f"游戏注入 {'ON' if on else 'OFF'}"
            + ("（P/WASD→游戏，R/V→client）" if on else "（仅 client）")
        )

    def handle_tactical_map_hold_event(self, event: QKeyEvent, pressed: bool) -> bool:
        """Tab/绑定键：按住放大地图，松开缩小。不开关 TacticalMap 模块。"""
        module = self.registry.get("TacticalMap")
        if module is None:
            return False
        key_name = self.key_name(event)
        is_tab_view_key = event.key() == Qt.Key.Key_Tab
        is_bound_view_key = module.keybind != "None" and self.keybind_matches(module.keybind, key_name, event)
        if not is_tab_view_key and not is_bound_view_key:
            return False
        if not event.isAutoRepeat():
            if pressed and not module.enabled:
                if not getattr(self, "_map_hold_disabled_toast", False):
                    self.toast("TacticalMap 模块未启用，无法放大地图")
                    self._map_hold_disabled_toast = True
                self.tactical_map_hold = False
            else:
                self.tactical_map_hold = bool(pressed and module.enabled)
        return True

    def handle_control_key_event(self, event: QKeyEvent, pressed: bool) -> bool:
        """Client 前台时的游戏键注入（P/WASD/…）。client 保留键不进这里。"""
        if event.isAutoRepeat() and event.key() not in (
            Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D, Qt.Key.Key_Shift,
        ):
            # 非底盘键忽略系统 autorepeat（钩子路径会处理游戏前台）
            return False
        if self.home_visible or self.gui_visible:
            return False
        # RightShift 只开 GUI，不当加速
        if self.is_right_shift_event(event):
            return False
        key = self.key_name(event)
        key_u = key.strip().upper()
        key_l = key.strip().lower().replace(" ", "")
        # 左 Shift → 加速
        if event.key() == Qt.Key.Key_Shift and not self.is_right_shift_event(event):
            key_u = "SHIFT"
            key = "SHIFT"
            key_l = "shift"
        # client 模块键（R/V 等）禁止进游戏
        if key_l in self.client_hotkey_names():
            return False

        inject_module = self.registry.get("GestaltControl")
        inject_on = bool(
            inject_module
            and inject_module.enabled
            and self.setting_bool(inject_module, "Inject Enabled", False)
            and not self._inject_paused_for_gui
        )

        # 底盘：走 control 状态机 + WASD 注入
        if key_u in ("W", "A", "S", "D", "SHIFT", "LSHIFT"):
            serial_module = self.registry.get("SerialBridge")
            mock_setting = serial_module.get_setting("Mock Writes") if serial_module else None
            self.control.mock_output = bool(mock_setting.value) if mock_setting else True
            command = self.control.set_key(
                key if key_u != "LSHIFT" else "SHIFT", pressed, self.state, self.registry
            )
            if command is None:
                return False
            if (
                pressed
                and inject_on
                and self.control.last_inject_ok
                and time.perf_counter() - self.last_control_toast > 1.2
            ):
                label = "L-Shift 加速" if key_u in ("SHIFT", "LSHIFT") else "WASD"
                self.toast(f"{label} -> 游戏")
                self.last_control_toast = time.perf_counter()
            return True

        # 其它游戏键（P 开赛、Space、E…）：直接 VK 注入
        if not inject_on or not self.gestalt_injector.enabled:
            return False
        native = int(event.nativeVirtualKey()) if event.nativeVirtualKey() else 0
        vk = native
        if vk <= 0:
            from ..gestalt_input import key_name_to_vk

            vk = key_name_to_vk(key_u) or key_name_to_vk(key_l) or 0
        if vk <= 0:
            return False
        ok = self.gestalt_injector.inject_vk(int(vk), bool(pressed))
        if ok and pressed and time.perf_counter() - self.last_control_toast > 1.5:
            self.toast(f"{key_u or hex(vk)} -> 游戏")
            self.last_control_toast = time.perf_counter()
        return ok

    def handle_keybind_event(self, event: QKeyEvent) -> None:
        module = self.binding_module
        if module is None:
            return
        if event.key() == Qt.Key.Key_Escape:
            module.keybind = "None"
            self.binding_module = None
            self.anim.value("keybind.overlay", 0.0, self.anim_duration(0.16), Easings.ease_out_quad)
            self.toast(f"{module.name} 按键绑定已清空")
            self.auto_save_config()
            return
        name = self.key_name(event)
        if not name:
            return
        module.keybind = name
        self.binding_module = None
        self.toast(f"{module.name} 已绑定到 {name}")
        self.auto_save_config()

    def dispatch(self, action: str, payload: Any, point: QPointF, button: int, rect: QRectF) -> None:
        if action == "module_toggle":
            module = self.registry.get(str(payload))
            if module:
                self.registry.toggle_module(module, source="clickgui")
                self.selected_module = module
                self.toast(f"{module.name} {'已启用' if module.enabled else '已关闭'}")
                self.auto_save_config()
        elif action == "module_select":
            module = self.registry.get(str(payload))
            if module:
                self.selected_module = module
        elif action == "module_expand":
            name = str(payload)
            if name in self.module_expanded:
                self.module_expanded.remove(name)
            else:
                self.module_expanded.add(name)
            self.auto_save_config()
        elif action == "module_bind":
            module = self.registry.get(str(payload))
            if module:
                self.binding_module = module
                self.anim.value("keybind.overlay", 0.0, 0.01, Easings.linear, initial=0.0)
        elif action == "setting_toggle":
            setting: Setting = payload
            setting.value = not bool(setting.value)
            self.after_setting_changed(setting)
        elif action == "setting_slider":
            setting = payload
            setting.set_from_ratio((point.x() - rect.left()) / max(1.0, rect.width()))
            self.drag = {"type": "slider", "setting": setting, "rect": rect}
            self.after_setting_changed(setting)
        elif action == "setting_mode_next":
            setting = payload
            setting.cycle(-1 if button == 3 else 1)
            self.after_setting_changed(setting)
        elif action == "select_category":
            self.selected_category = str(payload)
        elif action == "search_focus":
            self.search_focus = True
        elif action == "gui_mode":
            self.mode_manager.set_mode(str(payload))
            self.sync_mode_setting()
            self.toast(f"GUI 模式：{payload}")
            self.auto_save_config()
        elif action == "old_expand":
            self.old_panels[str(payload)]["expanded"] = not bool(self.old_panels[str(payload)]["expanded"])
            self.auto_save_config()
        elif action == "old_drag" and button == 1:
            panel = self.old_panels[str(payload)]
            panel["dragged"] = True
            self.drag = {
                "type": "old_panel",
                "category": str(payload),
                "offset_x": point.x() - float(panel["x"]),
                "offset_y": point.y() - float(panel["y"]),
            }
        elif action == "dropdown_expand":
            self.dropdown_panels[str(payload)]["expanded"] = not bool(self.dropdown_panels[str(payload)]["expanded"])
            self.auto_save_config()
        elif action == "select_scenario":
            self.apply_scenario(str(payload), close_home=False)
            self.reset_startup_page_animation("match")
            self.startup_step = "match"
            self.auto_save_config()
            self.request_startup_repaint()
        elif action == "select_match_mode":
            self.apply_match_mode(str(payload), close_home=True)
            self.auto_save_config()
            self.request_startup_repaint()
        elif action == "config_action":
            self.handle_config_action(str(payload))
        elif action == "startup_back":
            self.reset_startup_page_animation("scenario")
            self.startup_step = "scenario"
            self.request_startup_repaint()
        elif action == "startup_open_gui":
            self.home_visible = False
            self.startup_step = "done"
            self.gui_visible = True
            self.toast("ClickGUI opened from launcher")
            self.request_startup_repaint()
        elif action == "startup_open_config":
            config_module = self.registry.get("ConfigManager")
            self.selected_category = "System"
            if config_module:
                self.selected_module = config_module
            self.home_visible = False
            self.startup_step = "done"
            self.gui_visible = True
            self.toast("ConfigManager opened from launcher")
            self.request_startup_repaint()
        elif action == "startup_open_wallpaper":
            self.wallpaper_picker_visible = True
            profiles = self.startup_wallpaper.profile_names()
            visual_module = self.registry.get("StartupVisual")
            current = self.setting_mode(visual_module, "Wallpaper Profile", "TexasMP4")
            self.wallpaper_picker_page = profiles.index(current) // 4 if current in profiles else 0
        elif action == "wallpaper_select":
            self.apply_wallpaper_profile(str(payload))
        elif action == "wallpaper_import":
            self.import_wallpaper_from_dialog()
        elif action == "wallpaper_remove":
            self.remove_wallpaper_profile(str(payload))
        elif action == "wallpaper_reset":
            self.reset_wallpaper_profile()
        elif action == "wallpaper_page":
            count = max(1, math.ceil(len(self.startup_wallpaper.profile_names()) / 4))
            self.wallpaper_picker_page = max(0, min(count - 1, self.wallpaper_picker_page + int(payload)))
        elif action == "wallpaper_close":
            self.wallpaper_picker_visible = False
        elif action == "wallpaper_block":
            pass
        elif action == "startup_exit":
            self.close()

    def after_setting_changed(self, setting: Setting) -> None:
        scenario = self.registry.get("Scenario")
        if scenario and setting is scenario.get_setting("Mode"):
            self.apply_scenario(str(setting.value), close_home=False)
            self.auto_save_config()
            return
        match_mode = self.registry.get("MatchMode")
        if match_mode and setting is match_mode.get_setting("Mode"):
            self.apply_match_mode(str(setting.value), close_home=False)
            self.auto_save_config()
            return
        alliance_profile = self.registry.get("AllianceProfile")
        if alliance_profile and setting is alliance_profile.get_setting("Side"):
            self.state.alliance_color = str(setting.value)
            self.vision_kill_detector.reset()
            self.toast(f"阵营：{setting.value}")
            self.auto_save_config()
            return
        if match_mode and setting is match_mode.get_setting("Alliance"):
            self.state.alliance_color = str(setting.value)
            if alliance_profile:
                side = alliance_profile.get_setting("Side")
                if side:
                    side.value = setting.value
            self.vision_kill_detector.reset()
            self.auto_save_config()
            return
        click_gui = self.registry.get("ClickGui")
        if click_gui and setting is click_gui.get_setting("Mode"):
            self.mode_manager.set_mode(str(setting.value))
            self.toast(f"GUI 模式：{setting.value}")
        hud = self.registry.get("HUD")
        if hud and setting is hud.get_setting("Right Display"):
            self.toast(f"右侧 HUD：{setting.value}")
        config_manager = self.registry.get("ConfigManager")
        if config_manager and setting is config_manager.get_setting("Profile"):
            self.reload_current_config()
            return
        elimination_fx = self.registry.get("EliminationFX")
        if elimination_fx and setting is elimination_fx.get_setting("Preset"):
            self.kill_fx.preload_preset_sounds(str(setting.value))
        self.auto_save_config()

    def handle_config_action(self, action_name: str) -> None:
        if action_name == "Save Current":
            self.save_current_config()
        elif action_name == "Reload Profile":
            self.reload_current_config()
        elif action_name == "Import Config":
            self.import_config_from_dialog()
        elif action_name == "Import Wallpaper":
            self.import_wallpaper_from_dialog()
        elif action_name == "Reset Wallpaper":
            self.reset_wallpaper_profile()
        elif action_name in ("Capture Chessboard", "Solve Calibration", "Export Tongji YAML"):
            self.handle_calibration_action(action_name)
        elif action_name in ("Preview FX", "Test Elimination"):
            self.preview_elimination_fx(single=action_name == "Test Elimination")
        elif action_name.startswith("Preview ") and action_name in (
            "Preview Enable", "Preview Disable", "Preview Invalid"
        ):
            audio = self.registry.get("AudioFeedback")
            volume = self.get_setting_number(audio, "UI Volume", 45.0)
            self.ui_audio.preview(
                action_name.removeprefix("Preview ").lower(),
                volume,
                self.setting_mode(audio, "Sound Theme", "Polar"),
            )

    def on_module_toggled(self, module: Module, enabled: bool, source: str) -> None:
        audio = self.registry.get("AudioFeedback")
        active = bool(
            audio
            and (audio.enabled or module is audio)
            and self.setting_bool(audio, "Enabled", True)
            and self.setting_bool(audio, "Module Toggle Sounds", True)
        )
        self.ui_audio.module_toggled(
            enabled,
            source,
            active=active,
            volume=self.get_setting_number(audio, "UI Volume", 45.0),
            theme=self.setting_mode(audio, "Sound Theme", "Polar"),
        )
        # V=SpinMode：client 开关 → 游戏按住/松开 R（小陀螺）。需 Inject Enabled。
        if module.name == "SpinMode":
            if self.gestalt_injector.enabled:
                ok = self.gestalt_injector.set_game_spin(enabled)
                if enabled and not ok:
                    self.toast("小陀螺：游戏 R 注入失败（检查 Input Bridge / F7 注入）")
                elif enabled:
                    self.toast("小陀螺 ON → 游戏 R 按住")
                else:
                    self.toast("小陀螺 OFF → 松开游戏 R")
            elif enabled:
                self.toast("请先 F7 打开 Inject Enabled，才能把 V 映射为游戏小陀螺")
        # R=ArmorAutoAim：只画框 + 外发开关给完整同济；不启停 NUC
        if module.name == "ArmorAutoAim":
            self.aa_state_publisher.set_enabled(enabled)
        # 视觉入站只跟 TongjiVisionBridge / CameraFeed 有关
        if module.name in ("TongjiVisionBridge", "CameraFeed", "RobotOverlay"):
            if not self.home_visible:
                self.refresh_vision_runtime()

    def preview_elimination_fx(self, *, single: bool = False) -> None:
        module = self.registry.get("EliminationFX")
        sequence = "Single" if single else self.setting_mode(module, "Preview Sequence", "Normal 1-6")
        if sequence == "All Kill Types":
            types = ["NORMAL", "HEADSHOT", "EXPLOSION", "CRIT", "ASSIST", "DESTROY_VEHICLE"]
        elif sequence == "Single":
            types = ["NORMAL"]
        else:
            types = ["NORMAL"] * 6
        for index, kill_type in enumerate(types):
            event = CombatEvent(
                "elimination", "preview", True, time.perf_counter(),
                victim_id=101 + index,
                detail="local_preview",
                kill_type=kill_type,
                combo=index + 1,
                victim_name=f"ROBOT {101 + index}",
                distance_m=8.0 + index,
                score_delta=100,
                tags=("ELIMINATION", "ARMOR HIT") if kill_type != "DESTROY_VEHICLE" else ("ROBOT DESTROYED",),
            )
            QTimer.singleShot(index * 650, lambda queued=event: self.accept_combat_event(queued))

    def handle_calibration_action(self, action_name: str) -> None:
        module = self.registry.get("CameraCalibration")
        name = self.setting_mode(module, "Profile", "polar_webcam")
        columns = int(self.get_setting_number(module, "Board Columns", 9))
        rows = int(self.get_setting_number(module, "Board Rows", 6))
        square_size = self.get_setting_number(module, "Square Size", 25.0)
        if action_name == "Capture Chessboard":
            frame = self.camera_worker.latest_frame()
            if frame is not None and self.calibration.capture(frame.bgr, columns, rows, square_size):
                self.toast(f"Calibration sample {self.calibration.sample_count}: corners locked")
            else:
                self.toast(self.calibration.last_error or "Chessboard capture failed", kind="warning")
            return
        if action_name == "Solve Calibration":
            profile = self.calibration.solve(name, columns, rows, square_size)
            if profile is None:
                self.toast(self.calibration.last_error or "Calibration solve failed", kind="warning")
            else:
                self.toast(f"Calibration solved: {profile.rms_error_px:.2f}px RMS")
            return
        bridge_module = self.registry.get("TongjiVisionBridge")
        device = self.setting_mode(bridge_module, "Inference Device", "AUTO")
        path = self.calibration.export_tongji_yaml(name, device)
        if path is None:
            self.toast(self.calibration.last_error or "Tongji YAML export failed", kind="warning")
        else:
            self.toast(f"Tongji YAML exported: {path.name}")

    def handle_startup_key(self, event: QKeyEvent) -> bool:
        if self.startup_step == "match":
            if event.key() == Qt.Key.Key_Backspace:
                self.reset_startup_page_animation("scenario")
                self.startup_step = "scenario"
                self.request_startup_repaint()
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
            self.apply_match_mode(str(key), close_home=True)
            self.auto_save_config()
            self.request_startup_repaint()
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
        self.reset_startup_page_animation("match")
        self.startup_step = "match"
        self.auto_save_config()
        self.request_startup_repaint()
        return True

    def apply_scenario(self, key: str, close_home: bool) -> None:
        config = self.scenario_manager.set_scenario(key)
        self.scenario_manager.apply_to_registry(self.registry)
        self.scenario_manager.apply_to_runtime(self.mqtt, self.udp, self.control)
        if close_home:
            self.home_visible = False
            self.gui_visible = False
        self.toast(f"Scenario: {config.title}")

    def apply_match_mode(self, key: str, close_home: bool, announce: bool = True) -> None:
        config = self.match_mode_manager.set_mode(key)
        self.match_mode_manager.apply_to_registry(self.registry)
        self.seed_match_state(config)
        if close_home:
            self.home_visible = False
            self.gui_visible = False
            self.startup_step = "done"
        if announce:
            self.toast(f"Match mode: {config.title}")

    def seed_match_state(self, config: MatchModeConfig) -> None:
        self.state.match_mode = config.key
        self.state.match_title = config.title
        self.state.match_duration_sec = config.duration_sec
        self.state.stage_countdown_sec = config.duration_sec
        self.state.stage_elapsed_sec = 0
        self.state.start_time = time.perf_counter()
        self.state.current_stage = "MATCH"
        match_module = self.registry.get("MatchMode")
        alliance = match_module.get_setting("Alliance") if match_module else None
        self.state.alliance_color = str(alliance.value) if alliance else "Red"
        if config.key == "RMUL3v3":
            self.state.ally_victory_points = 200
            self.state.enemy_victory_points = 200
            self.state.control_owner = "NEUTRAL"
            self.state.control_progress = 0
            self.state.base_health = 0
            self.state.enemy_base_health = 0
            self.state.outpost_health = 0
            self.state.enemy_outpost_health = 0
        else:
            self.state.base_health = self.state.base_max_health
            self.state.enemy_base_health = self.state.enemy_base_max_health
            self.state.outpost_health = self.state.outpost_max_health
            self.state.enemy_outpost_health = self.state.enemy_outpost_max_health

    def refresh_config_profiles(self) -> None:
        module = self.registry.get("ConfigManager")
        setting = module.get_setting("Profile") if module else None
        if setting is None:
            return
        names = self.config_store.profile_names()
        current = str(setting.value)
        setting.options = names
        setting.value = current if current in names else names[0]

    def refresh_wallpaper_profiles(self) -> None:
        module = self.registry.get("StartupVisual")
        setting = module.get_setting("Wallpaper Profile") if module else None
        if setting is None:
            return
        names = self.startup_wallpaper.profile_names()
        current = str(setting.value)
        setting.options = names
        setting.value = current if current in names else names[0]

    def import_wallpaper_from_dialog(self) -> None:
        start = str(self.workspace / "rm_custom_client" / "assets" / "wallpapers")
        filters = "Wallpaper media (*.mp4 *.webm *.mov *.avi *.mkv *.gif *.png *.jpg *.jpeg)"
        path, _ = QFileDialog.getOpenFileName(self, "Import startup wallpaper", start, filters)
        if not path:
            return
        try:
            profile = self.startup_wallpaper.import_wallpaper(Path(path))
        except (OSError, ValueError) as exc:
            self.toast(f"Wallpaper import failed: {exc}")
            return
        self.refresh_wallpaper_profiles()
        module = self.registry.get("StartupVisual")
        if module:
            profile_setting = module.get_setting("Wallpaper Profile")
            mode_setting = module.get_setting("Background Mode")
            if profile_setting:
                profile_setting.value = profile
            if mode_setting:
                mode_setting.value = "VideoParallax"
        profiles = self.startup_wallpaper.profile_names()
        self.wallpaper_picker_page = profiles.index(profile) // 4 if profile in profiles else 0
        self.toast(f"Wallpaper loaded: {profile}")
        self.auto_save_config()

    def apply_wallpaper_profile(self, profile: str) -> None:
        if profile not in self.startup_wallpaper.profile_names():
            self.toast("壁纸资源不存在，已保留当前壁纸")
            return
        module = self.registry.get("StartupVisual")
        if module is None:
            return
        profile_setting = module.get_setting("Wallpaper Profile")
        mode_setting = module.get_setting("Background Mode")
        if profile_setting:
            profile_setting.value = profile
        if mode_setting:
            if profile == "ParticleGrid":
                mode_setting.value = "ParticleGrid"
            elif profile == "Default3000925581Pkg":
                mode_setting.value = "PkgScene"
            else:
                mode_setting.value = "VideoParallax"
        self.toast(f"壁纸已切换：{self.startup_wallpaper.profile_label(profile)}")
        self.auto_save_config()

    def remove_wallpaper_profile(self, profile: str) -> None:
        if not self.startup_wallpaper.can_remove(profile):
            self.toast("内置壁纸不能删除")
            return
        if not self.startup_wallpaper.remove_wallpaper(profile):
            self.toast("删除壁纸失败")
            return
        self.refresh_wallpaper_profiles()
        self.apply_wallpaper_profile("TexasMP4")
        count = max(1, math.ceil(len(self.startup_wallpaper.profile_names()) / 4))
        self.wallpaper_picker_page = min(self.wallpaper_picker_page, count - 1)
        self.toast("用户壁纸已删除")

    def reset_wallpaper_profile(self) -> None:
        self.apply_wallpaper_profile("TexasMP4")
        self.wallpaper_picker_page = 0

    def current_profile(self) -> str:
        module = self.registry.get("ConfigManager")
        setting = module.get_setting("Profile") if module else None
        return self.config_store.clean_profile_name(str(setting.value if setting else "default"))

    def current_config_data(self) -> dict[str, Any]:
        return export_client_config(
            self.registry,
            gui_mode=self.mode_manager.mode,
            selected_category=self.selected_category,
            selected_module=self.selected_module.name if self.selected_module else "",
            module_expanded=self.module_expanded,
            old_panels=self.old_panels,
            new_panels=self.new_panels,
            dropdown_panels=self.dropdown_panels,
        )

    def load_startup_config(self) -> None:
        module = self.registry.get("ConfigManager")
        if not self.setting_bool(module, "Load On Start", True):
            return
        data = self.config_store.load(self.current_profile())
        if data:
            self.apply_config_data(data, announce=False)

    def apply_config_data(self, data: dict[str, Any], announce: bool = True) -> None:
        self.config_loading = True
        try:
            self.refresh_wallpaper_profiles()
            apply_registry_config(self.registry, data)
            gui_mode = str(data.get("gui_mode", self.mode_manager.mode))
            self.mode_manager.set_mode(gui_mode)
            self.selected_category = str(data.get("selected_category", self.selected_category))
            selected_name = str(data.get("selected_module", self.selected_module.name if self.selected_module else ""))
            selected = self.registry.get(selected_name)
            if selected:
                self.selected_module = selected
            expanded = data.get("module_expanded")
            if isinstance(expanded, list):
                self.module_expanded = {str(name) for name in expanded}
            for target, key in ((self.old_panels, "old_panels"), (self.new_panels, "new_panels"), (self.dropdown_panels, "dropdown_panels")):
                value = data.get(key)
                if isinstance(value, dict):
                    for name, panel_data in value.items():
                        if name in target and isinstance(panel_data, dict):
                            target[name].update(panel_data)
            self.sync_runtime_from_registry()
            self.sync_mode_setting()
            self.refresh_config_profiles()
            self.refresh_wallpaper_profiles()
        finally:
            self.config_loading = False
        if announce:
            self.toast(f"配置已加载：{self.current_profile()}")

    def sync_runtime_from_registry(self) -> None:
        scenario = self.registry.get("Scenario")
        scenario_setting = scenario.get_setting("Mode") if scenario else None
        if scenario_setting:
            self.scenario_manager.set_scenario(str(scenario_setting.value))
            self.scenario_manager.apply_to_runtime(self.mqtt, self.udp, self.control)
        match_mode = self.registry.get("MatchMode")
        match_setting = match_mode.get_setting("Mode") if match_mode else None
        if match_setting:
            config = self.match_mode_manager.set_mode(str(match_setting.value))
            self.seed_match_state(config)
        self.home_visible = self.setting_bool(scenario, "Show Startup", True)
        self.startup_step = "scenario" if self.home_visible else "done"

    def save_current_config(self, announce: bool = True) -> None:
        path = self.config_store.save(self.current_profile(), self.current_config_data())
        self.refresh_config_profiles()
        if announce:
            self.toast(f"配置已保存：{path.name}")

    def reload_current_config(self) -> None:
        data = self.config_store.load(self.current_profile())
        if data is None:
            self.toast("配置重新加载失败")
            return
        self.apply_config_data(data)

    def import_config_from_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入 POLAR-RM 配置", str(self.config_store.config_dir), "JSON 配置 (*.json)")
        if not path:
            return
        try:
            profile = self.config_store.import_file(Path(path))
        except (OSError, ValueError) as exc:
            self.toast(f"Import failed: {exc}")
            return
        self.refresh_config_profiles()
        module = self.registry.get("ConfigManager")
        setting = module.get_setting("Profile") if module else None
        if setting:
            setting.value = profile
        self.reload_current_config()

    def auto_save_config(self) -> None:
        module = self.registry.get("ConfigManager")
        if self.config_loading or not self.setting_bool(module, "Auto Save", True):
            return
        self.save_current_config(announce=False)

    def sync_mode_setting(self) -> None:
        click_gui = self.registry.get("ClickGui")
        if click_gui:
            mode = click_gui.get_setting("Mode")
            if mode:
                mode.value = self.mode_manager.mode

    def panel_modules(self) -> list[Module]:
        if self.search_query:
            query = self.search_query.lower()
            return sorted([m for m in self.registry.modules if query in m.name.lower()], key=lambda module: module.name.lower())
        return self.registry.by_category(self.selected_category)

    def get_setting_number(self, module: Module | None, name: str, default: float) -> float:
        setting = module.get_setting(name) if module else None
        if setting is None:
            return default
        try:
            return float(setting.value)
        except (TypeError, ValueError):
            return default

    def get_auto_ui_scale(self, width: int | None = None, height: int | None = None, strength: float = 1.0) -> float:
        module = self.registry.get("UIScale")
        if not module or not module.enabled or not self.setting_bool(module, "Auto Scale", True):
            return 1.0
        width = self.width() if width is None else width
        height = self.height() if height is None else height
        ref_width = max(320.0, self.get_setting_number(module, "Reference Width", 1280.0))
        ref_height = max(240.0, self.get_setting_number(module, "Reference Height", 720.0))
        raw = math.sqrt(max(1.0, width * height) / (ref_width * ref_height))
        raw = max(0.82, min(1.85, raw))
        if raw < 1.0:
            return raw
        softened = 1.0 + (raw - 1.0) * max(0.0, min(1.0, strength))
        return max(0.82, min(1.85, softened))

    def get_hud_scale(self, width: int | None = None, height: int | None = None) -> float:
        module = self.registry.get("UIScale")
        manual = self.get_setting_number(module, "HUD Scale", 1.0)
        auto_scale = self.get_auto_ui_scale(width, height, strength=0.80)
        if self.high_res_optimize(width, height):
            auto_scale = min(auto_scale, 1.34)
        return max(0.55, min(2.4, manual * auto_scale))

    def high_res_optimize(self, width: int | None = None, height: int | None = None) -> bool:
        module = self.registry.get("UIScale")
        if not self.setting_bool(module, "High Res Optimize", True):
            return False
        width = self.width() if width is None else width
        height = self.height() if height is None else height
        # 原 3e6 几乎只在 2K+ 触发；战斗 UI 在 1080p 也需要 lite。
        return width * height >= 1_400_000

    def video_sample_size(self, width: int, height: int) -> tuple[int, int]:
        if not self.high_res_optimize(width, height):
            return (width, height)
        max_pixels = 1920 * 1080
        pixels = max(1, width * height)
        if pixels <= max_pixels:
            return (width, height)
        scale = math.sqrt(max_pixels / pixels)
        return (max(320, int(width * scale)), max(180, int(height * scale)))

    def get_gui_scale(self) -> float:
        module = self.registry.get("ClickGui")
        setting = module.get_setting("Scale") if module else None
        ui_module = self.registry.get("UIScale")
        gui_scale = self.get_setting_number(ui_module, "GUI Scale", 1.0)
        base = float(setting.value if setting else 1.0)
        return max(0.55, min(1.85, base * gui_scale * self.get_auto_ui_scale(strength=0.52)))

    def get_gui_scale_for_bounds(
        self,
        width: int,
        height: int,
        base_width: float,
        base_height: float,
        margin_x: float,
        margin_y: float,
    ) -> float:
        scale = self.get_gui_scale()
        max_w = (width - margin_x) / max(1.0, base_width)
        max_h = (height - margin_y) / max(1.0, base_height)
        return max(0.55, min(scale, max_w, max_h, 1.85))

    def anim_duration(self, base: float) -> float:
        module = self.registry.get("UIScale")
        speed = self.get_setting_number(module, "Animation Speed", 1.0)
        return max(0.01, base / max(0.2, speed))

    def setting_bool(self, module: Module | None, setting_name: str, default: bool = False) -> bool:
        setting = module.get_setting(setting_name) if module else None
        return bool(setting.value) if setting else default

    def setting_mode(self, module: Module | None, setting_name: str, default: str) -> str:
        setting = module.get_setting(setting_name) if module else None
        return str(setting.value) if setting else default

    def category_icon(self, category: str) -> str:
        return {
            "Combat": "X",
            "Movement": ">",
            "Vision": "O",
            "Strategy": "#",
            "System": "*",
            "Render": "E",
        }.get(category, "?")

    def toast(self, message: str) -> None:
        self.toasts.append({"message": message, "created": time.perf_counter()})
        self.state.push_event(message)

    def font(self, key: str, size: float, weight: QFont.Weight | None = None) -> QFont:
        return self.fonts.font(key, max(1.0, size), weight)

    def draw_text(
        self,
        p: QPainter,
        rect: QRectF,
        text: str,
        font: QFont,
        c: QColor,
        flags: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    ) -> None:
        p.setFont(font)
        p.setPen(c)
        clipped = self.elide(p, font, text, rect.width())
        p.drawText(rect, int(flags), clipped)

    def draw_glass_text(
        self,
        p: QPainter,
        rect: QRectF,
        text: str,
        font: QFont,
        c: QColor,
        flags: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    ) -> None:
        """透明镜片上的最小文字保护，不引入额外暗色面板。"""
        self.draw_text(p, rect.translated(0.80, 0.95), text, font, color("#071018", 0.68), flags)
        self.draw_text(p, rect, text, font, c, flags)

    def elide(self, p: QPainter, font: QFont, text: str, width: float) -> str:
        p.setFont(font)
        return p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(1, int(width)))

    def rect_visible(self, rect: QRectF, clip: QRectF) -> bool:
        return rect.intersects(clip)

    def qt_button(self, event: QMouseEvent) -> int:
        if event.button() == Qt.MouseButton.RightButton:
            return 3
        if event.button() == Qt.MouseButton.MiddleButton:
            return 2
        return 1

    def key_name(self, event: QKeyEvent) -> str:
        key = event.key()
        special = {
            Qt.Key.Key_Space: "Space",
            Qt.Key.Key_Tab: "Tab",
            Qt.Key.Key_Backspace: "Backspace",
            Qt.Key.Key_Return: "Enter",
            Qt.Key.Key_Enter: "Enter",
            Qt.Key.Key_Escape: "Escape",
            Qt.Key.Key_Shift: "RightShift" if self.is_right_shift_event(event) else "Shift",
            Qt.Key.Key_Control: "Ctrl",
            Qt.Key.Key_Alt: "Alt",
        }
        if key in special:
            return special[key]
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35:
            return f"F{key - Qt.Key.Key_F1 + 1}"
        text = event.text()
        if text and text.isprintable():
            return text.upper()
        seq = QKeySequence(key).toString()
        return seq or ""

    def keybind_matches(self, bind: str, key_name: str, event: QKeyEvent) -> bool:
        normalized_bind = bind.strip().lower()
        normalized_key = key_name.strip().lower()
        if normalized_bind == normalized_key:
            return True
        if normalized_bind == "tab" and event.key() == Qt.Key.Key_Tab:
            return True
        if normalized_bind == "rightshift" and self.is_right_shift_event(event):
            return True
        return False

    def is_right_shift_event(self, event: QKeyEvent) -> bool:
        if event.key() != Qt.Key.Key_Shift:
            return False
        if event.nativeVirtualKey() == 161:
            return True
        # PySide/Qt 可能把左右 Shift 都上报为 VK_SHIFT(16)。
        # 在 Windows 上，右 Shift 通常仍会保留 0x36 硬件扫描码。
        if event.nativeScanCode() == 0x36:
            return True
        return False


def main() -> None:
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        surface = QSurfaceFormat()
        surface.setVersion(3, 3)
        surface.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        surface.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
        surface.setSwapInterval(1)
        surface.setDepthBufferSize(0)
        surface.setStencilBufferSize(0)
        QSurfaceFormat.setDefaultFormat(surface)
        app = QApplication(sys.argv)
    app.setApplicationName("POLAR-RM Custom Client")
    window = PolarRMWindow()
    window.show()
    if owns_app:
        raise SystemExit(app.exec())
