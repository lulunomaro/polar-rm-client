from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QBrush,
    QCloseEvent,
    QCursor,
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
    QPixmap,
    QPolygonF,
    QRadialGradient,
    QSurfaceFormat,
    QTransform,
    QWheelEvent,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox, QWidget

from ..backend.dt7_link import status_token as _dt7_status_token
from ..calibration import CameraCalibrationManager
from ..emergency_actions import request_control_lock, request_focus_loss
from ..auto_spin import ProjectileHitSpinController
from ..combat_hud import CombatHudAnimator, VALID_COMBAT_HUD_VISUALS, ring_end
from ..combat_events import CombatEvent, OfficialMatchFeed, VisionKillDetector
from ..config_store import ClientConfigStore, apply_registry_config, export_client_config
from ..configuration.wheel import sync_wheel_from_module, sync_module_from_wheel
from ..control import KeyboardControlBridge
from ..lifecycle import TeardownTask
from .polar.clickgui_frame import ClickGuiFrame, build_clickgui_frame
from .polar.overlay_layers import (
    FrameViewport, LayerCacheSnapshot, MotionLayerInputs, OverlayDrawPorts,
    OverlayLayerPlan, OverlayLayerRenderer, StaticLayerMode,
)
from .polar.clickgui_presenter import ClickGuiGpuPresenter, ClickGuiViewport
from .polar.compositor_plan import (
    BackdropTreatment, FinalFrameCompositor, FrameCompositionPlan,
    OverlayScene, motion_layer_needs_refresh,
)
from .polar.window_lifecycle import FinalContextPorts, OwnerAction, OwnerActionFailure, WindowLifecycleController, run_owner_actions
from .polar.presentation_controller import (
    PanelIntent, PanelPresentationController, dynamic_content_duration,
)
from .polar.frame_telemetry import FrameTelemetry, FrameTelemetryField
from .polar.tick_controller import (
    FrameTiming, RenderCadence, RuntimeTickPlan, TickDiagnosticField, TickDiagnostics, TickStageRunner,
)
from ..economy import (
    CATEGORY_TITLES,
    TRANSACTION_LABELS,
    EconomyAction,
    EconomyCommandBridge,
    EconomyContext,
    EconomyOffer,
    EconomyRequest,
    available_actions,
)
from ..hero_lob import HeroLobPlanner
from ..model import (
    CATEGORIES,
    GUI_MODES,
    LEGACY_GUI_MODES,
    PRIMARY_GUI_MODE,
    HUD_MODES,
    MODULE_LIST_STYLES,
    GuiModeManager,
    Module,
    RobotState,
    normalize_gui_mode,
    normalize_legacy_gui_mode,
    Setting,
    build_default_registry,
    normalize_angle_deg,
)
from ..match_modes import MATCH_MODES, MATCH_MODE_KEYS, FieldLayout, MatchModeConfig, MatchModeManager
from ..robot_profiles import (
    robot_id_for_alliance,
    apply_profile_to_state,
    profile_by_key,
    profiles_for_match,
    resolve_performance,
)
from ..kill_fx import KillFxRenderer
from ..match_chat import ChatEventAdapter, MatchChatController
from ..scenarios import SCENARIOS, ScenarioManager
from ..startup_wallpaper import StartupWallpaperManager
from ..tongji_bridge import GimbalCommandBridge, TongjiVisionReceiver
from ..tongji_yolo import LocalAlignedVision
from ..transports import OfficialMqttBridge, SerialBridgeStub, UDPVideoReceiver, VideoPipeline
from ..backend import BackendManager
from ..runtime_paths import RuntimePaths
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
    snapshot_from_local_result,
    snapshot_from_tongji_packet,
)
from .animation import (
    AnimationBank,
    Easings,
    GuiMotionController,
    IslandContainerState,
    MapIslandState,
    MorphState,
    step_damped_spring,
)
from .fonts import FontBook
from .gui_cache import ClickGuiLayerCache
from .clickgui_gpu import ClickGuiDrawList, ClickGuiGpuRenderer
from .jello_clickgui import JelloClickGuiRenderer, JelloClickGuiState, JelloMainTimeline
from rm_client.ui.endfield.wiki import EndfieldWikiRenderer, EndfieldWikiState, EndfieldWikiTimeline
from .jello_locale import (
    chinese_enabled,
    description_label as jello_description_label,
    action_label as jello_action_label,
    ui_text,
    LANGUAGE_CHINESE,
    LANGUAGE_ENGLISH,
    LANGUAGE_SETTING,
    category_label as jello_category_label,
    module_label as jello_module_label,
    option_label as jello_option_label,
    setting_label as jello_setting_label,
)
from .status_locale import dt7_display_token, status_text
from .minecraft_hud import MinecraftCombatHudRenderer
from .top_match_bar_controller import TopMatchBarController
from .cs2_feedback import play_buy_sound
from .cs2_video_settings import render_target, smooth_video
from .gpu_frame_compositor import GlassOwner, GpuFrameCompositor, LensMaterial
from .island_icon_gpu import IslandIconCommand, IslandIconGpuRenderer
from .economy_glass import draw_economy_glass, draw_economy_glass_capsule, draw_economy_glass_well
from .economy_workspace import EconomyIslandState, EconomyWorkspaceLayer
from .liquid_glass import LiquidGlassRenderer
from .layout import HudAnchorStack
from .overlay_coordinator import OverlayCoordinator, OverlayKind
from ..student_ui import StudentUiState, StudentNoticeAdapter
from .student_panels import StudentPanelRenderer
from .minecraft_chat import MinecraftServerChatRenderer
from .polar.mixins.student_frontend_mixin import StudentFrontendMixin
from .polar.mixins.escape_menu_mixin import EscapeMenuMixin
from .painter_lifecycle import finish_painter
from .quick_action_wheel import ACTION_LABELS, DEFAULT_ACTIONS, SECTOR_INDEXES, QuickActionWheelState
from .quick_action_wheel_gpu import QuickActionWheelCommand, QuickActionWheelGpuRenderer
from .startup_gl import StartupGpuCompositor, TextureOrigin


_LOG = logging.getLogger(__name__)

from .vision_wire_gpu import VisionWireBatch, VisionWireGpuRenderer
from .paint import (
    accent_color,
    cap_center_y,
    draw_bar,
    glow_rect,
    glow_text,
    rainbow_color_index,
    rounded_rect,
    text_width,
    with_alpha,
)
from .theme import THEME, color

# P8: hud_state_color 的 PolarLegacy 分支常量表（原为每次调用重建的字面量
# dict；值全部来自模块级 THEME/color 常量，逐项等价搬运）。
_HUD_LEGACY_STATE_COLORS: dict[str, QColor] = {
    "ally": THEME.cyan,
    "enemy": THEME.red,
    "danger": THEME.red,
    "warning": color("#ffd166"),
    "success": THEME.green,
    "gold": color("#ffd166"),
    "ammo": THEME.green,
    "control": THEME.pink,
    "spin": THEME.pink,
}
_HUD_STATE_COLOR_FALLBACK: QColor = THEME.cyan

from .vision_motion import (
    AngleRenderTrack,
    ScalarRenderTrack,
    TargetHudPresenceFrame,
    TargetHudPresenceState,
    VisionRenderInterpolator,
)
# 阶段 1 叶子外迁（REFACTOR_BLUEPRINT §5）：以下符号实现体已迁至 ui/polar/ 子包，
# 此处 re-export 维持 rm_client.ui.polar_app 的模块级 import 兼容面（§5.6）。
from .polar.primitives import (
    EconomyScenePhase,
    FrameScheduler,
    Hotspot,
    InertialScroll,
    ModuleListLayout,
    StartupLayerCache,
)
from .polar.text_metrics import (
    TextMetricsMixin,
    _aspect_fit_image_rect,
    economy_lerp_rect,
    fit_aspect,
    format_time,
    mix_color,
    painter_device_scale,
    short_outpost_value,
    short_ring_number,
)
# 阶段 2a 渲染器提取（REFACTOR_BLUEPRINT §5 表 2a）：D9 战斗目标 HUD 族方法体
# 已机械迁至 renderers/combat_target_hud.py 的 CombatTargetHudMixin（零修改），
# 由下方 PolarRMWindow 继承装配；combat_hud_animator 的构造接线仍在 __init__ 原位。
from .polar.renderers.combat_target_hud import CombatTargetHudMixin
# 阶段 2b 渲染器提取（REFACTOR_BLUEPRINT §5 表 2b 行）：D8 目标锁定/自瞄 FOV/ESP
# 线框族方法体已机械迁至 renderers/esp_wireframe.py 的 EspWireframeMixin（零修改），
# 由下方 PolarRMWindow 继承装配；has_valid_combat_target 按偏差决策留在本文件
# （跨域被 CombatTargetHudMixin 使用，见其原行位注释）。
from .polar.renderers.esp_wireframe import EspWireframeMixin
# 阶段 2c 渲染器提取（REFACTOR_BLUEPRINT §5 表 2c 行）：D12 ModuleList 模块列表
# 族方法体已机械迁至 renderers/module_list.py 的 ModuleListMixin（零修改），
# 由下方 PolarRMWindow 继承装配；_client_asset_path 按偏差决策留在本文件
# （D19 共享辅助，多域经 self 调用）；排序缓存字段仍在 __init__ 原位。
from .polar.renderers.module_list import ModuleListMixin
# 阶段 2d 渲染器提取（REFACTOR_BLUEPRINT §5 表 2d 行）：D10 战术地图/官方地图/
# 状态坞族方法体已机械迁至 renderers/tactical_map.py 的 TacticalMapMixin
# （零修改，含 draw_official_map 缺陷 D 修复区），由下方 PolarRMWindow 继承装配；
# _map_round_cache 保持方法内惰性创建；handle_tactical_map_hold_event（D15 输入域）
# 与 liquid_map_enabled 族（D9 HUD 风格助手）按偏差决策留在本文件。
from .polar.renderers.tactical_map import TacticalMapMixin
from .cs2_scoreboard import paint_tab_scoreboard, prepare_tab_layout, tab_scoreboard_active
from .polar.renderers.dynamic_island import DynamicIslandMixin
# 阶段 2f 渲染器提取（REFACTOR_BLUEPRINT §5 表 2f 行）：D4 启动场景渲染族
# 方法体已机械迁至 renderers/startup_scene.py 的 StartupSceneMixin（零修改），
# 由下方 PolarRMWindow 继承装配；draw_synthetic_video_scene（D7 视频管线）、
# liquid_press_amount（共享辅助）、draw_startup_gpu_background /
# paint_gpu_startup_interaction_layer（D2）与 build_gpu_startup_launcher /
# build_gpu_robot_profile_launcher（D5 装配边界偏差决策）留在本文件；
# build_gpu_startup_button/session/bottom_bar/hover_layer 族已随阶段 3c
# 迁至 mixins/clickgui_builders_mixin.py 的 ClickGuiBuildersMixin。
from .polar.renderers.startup_scene import StartupSceneMixin
# 阶段 3a 大域 mixin 化（REFACTOR_BLUEPRINT §5 阶段 3 表 3a 行）：D17 配置/档案/
# 壁纸持久化族方法体已机械迁至 mixins/persistence_mixin.py 的 PersistenceMixin
# （零修改，三段不连续区块整体搬迁），由下方 PolarRMWindow 继承装配；
# Save ownership is composed explicitly in __init__; config_loading stays with the UI adapter.
# 初始化，auto-save 合并写（0.25s 防抖）语义不变；load_startup_config 在 __init__
# 的调用点不变，mixin 方法可用性由 MRO 保证。
from .polar.mixins.persistence_mixin import PersistenceMixin
# 阶段 3b 大域 mixin 化（REFACTOR_BLUEPRINT §5 阶段 3 表 3b 行）：D7 视频管线/
# 后端接线 + D18 官方消息/战斗事件/击杀特效 + D14 Minecraft 功能槽族
# 方法体已机械迁至 mixins/video_backend_mixin.py 的 VideoBackendMixin（零修改，
# 四段不连续区块整体搬迁），由下方 PolarRMWindow 继承装配；tick stage 列表仍在
# on_tick 按原方法名引用这些成员（名字不变）；synthetic_video_cache* /
# NORMAL_BACKEND_VIDEO_STALE_S 类常量仍在原位初始化。
from .polar.mixins.video_backend_mixin import VideoBackendMixin
# 阶段 3c 大域 mixin 化（REFACTOR_BLUEPRINT §5 阶段 3 表 3c 行）：D5 ClickGUI
# 构建族（GPU builders + 打开/关闭 + legacy CPU GUI）方法体已机械迁至
# mixins/clickgui_builders_mixin.py 的 ClickGuiBuildersMixin（零修改，五段
# 不连续区块整体搬迁），由下方 PolarRMWindow 继承装配；builder 边画边
# add_hotspot 协议与 gui_scroll_viewports 注册机制保持不变（hotspots /
# _hotspot_clip / gui_scroll_viewports 字段仍在 __init__ 原位初始化）；
# build_gpu_startup_launcher / build_gpu_robot_profile_launcher 按保守边界
# 决策留在本文件（详见新模块 docstring 偏差决策节）；invalidate_transient_overlay_scene
# 留守本文件（多域共用），_finalize_economy_restore 已随阶段 3d 迁至 EconomyMixin。
from .polar.mixins.clickgui_builders_mixin import ClickGuiBuildersMixin
# 阶段 3d 大域 mixin 化（REFACTOR_BLUEPRINT §5 阶段 3 表 3d 行）：D6 经济工作区
# 剩余族（legacy/compat 绘制 + 数据传输 + 开合状态机/交互 + 玻璃助手 +
# build_gpu_economy_workspace + start_economy_qualification_test）方法体已机械迁至
# mixins/economy_mixin.py 的 EconomyMixin（零修改，七段不连续区块整体搬迁），由下方
# PolarRMWindow 继承装配；D2↔D6 FBO restore generation 协议：提交端
# _finalize_economy_restore 随域迁入，失效辅助 invalidate_transient_overlay_scene
# 因多域共用按保守边界决策留守本文件；协议状态字段（_economy_generation /
# _economy_restore_pending_generation / _economy_pending_overlay 等）仍全部在
# __init__ 原位初始化，D2 侧写入点不动。
from .polar.mixins.economy_mixin import EconomyMixin
# Event adapters preserve the window facade. PointerInteraction owns capture,
# release and binding epochs; hotspot geometry remains in its UI adapter.
from .polar.mixins.input_dispatch_mixin import InputDispatchMixin
from .polar.pointer_interaction import PointerInteraction
from .polar.panel_layouts import create_panel_layouts
from .polar.qt_application import _abort_failed_launch, launch_qt_application
from .polar.startup_navigation import StartupNavigation, StartupNavigationField
from .polar.startup_activation import (
    StartupProfilePorts, StartupPresentationPorts, StartupScenarioPorts, activate_startup_session,
)
from .polar.gpu_warmup import GpuWarmupQueue, GpuWarmupField
from .polar.camera_activation import CameraActivation, CameraActivationField
from ..configuration.profile_saves import ProfileSaveState
from .polar.profile_save_fields import ProfileSaveField
from .polar.vision_selection import VisionSelection, VisionSelectionField
from .polar.normal_video_timeline import NormalVideoTimeline, NormalVideoField
from .polar.window_assembly import (
    create_feedback_assets, create_gpu_services, create_presentation_services,
    create_startup_wallpaper, create_vision_services, create_warmup_timer,
    create_frame_timer, prepare_runtime_paths, resolve_client_asset, start_frame_timer,
)
# 阶段 3f 大域 mixin 化：D16 动作处理/配置动作/资格赛全族（after_setting_changed/
# handle_config_action/handle_qualification_action/start_kmc_qualification_test/
# restore_compact_hud/on_module_toggled/play_gui_transition_sound/
# preview_elimination_fx/handle_calibration_action 九个方法）已机械迁至
# rm_client/ui/polar/mixins/config_actions_mixin.py 的 ConfigActionsMixin，
# 由下方 PolarRMWindow 继承装配（InputDispatchMixin 之后、QOpenGLWidget 之前，
# MRO 解析不变）；阶段 3e 的 _DISPATCH_HANDLERS 对 handle_config_action /
# after_setting_changed 的引用经 self 属性查找照常解析；__qualname__ 已钉回
# PolarRMWindow.<name>。
from .polar.mixins.config_actions_mixin import ConfigActionsMixin


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
        painter.end()


class PolarHudOverlay(QWidget):
    """覆盖 GPU 战斗底图的交互与 HUD 图层。"""

    def __init__(self, owner: "PolarRMWindow") -> None:
        super().__init__(owner)
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event: QPaintEvent) -> None:
        self.owner.paint_overlay(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.owner.mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.owner.mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.owner.mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.owner.wheelEvent(event)


class PolarRMWindow(TextMetricsMixin, CombatTargetHudMixin, EspWireframeMixin, ModuleListMixin, TacticalMapMixin, DynamicIslandMixin, StartupSceneMixin, PersistenceMixin, VideoBackendMixin, ClickGuiBuildersMixin, EconomyMixin, InputDispatchMixin, ConfigActionsMixin, StudentFrontendMixin, EscapeMenuMixin, QOpenGLWidget):
    """主窗口直接使用 Qt OpenGL 表面，所有 QPainter UI 都由 OpenGL 合成呈现。"""

    # ------------------------------------------------------------------
    # 阶段 1 叶子外迁：以下成员的实现体已机械迁移至
    # rm_client/ui/polar/text_metrics.py（函数体零修改、原签名原样保留），
    # 此处按原定义形态（实例方法 / staticmethod）重新挂回类上，保持 §5.6
    # 冻结面的 kind 与 __qualname__ 不变；调用派发路径与每帧开销不变。
    # ------------------------------------------------------------------
    mix_color = mix_color
    fit_aspect = fit_aspect
    format_time = format_time
    short_ring_number = short_ring_number
    short_outpost_value = short_outpost_value

    economy_lerp_rect = staticmethod(economy_lerp_rect)
    _aspect_fit_image_rect = staticmethod(_aspect_fit_image_rect)
    painter_device_scale = staticmethod(painter_device_scale)

    # Existing diagnostics consumers may still read/write these private seams.
    # Production tick paths use the single owner below, without copied state.
    _last_capture_sequence = NormalVideoField("capture_sequence")
    _display_frame_sequence = NormalVideoField("display_sequence")
    _last_backend_video_at = NormalVideoField("backend_admitted_at")
    _normal_backend_mode = NormalVideoField("requested_mode")
    _normal_frame_source = NormalVideoField("frame_source")

    _accepted_profile = ProfileSaveField("accepted")
    _unsaved_profile_name = ProfileSaveField("draft")
    _auto_save_pending = ProfileSaveField("pending")
    _auto_save_owner = ProfileSaveField("pending_owner")
    _auto_save_due_at = ProfileSaveField("due_at")
    _close_save_retry_required = ProfileSaveField("close_retry_required")
    _config_save_error = ProfileSaveField("error_reported")

    _ui_tick_last_ms = TickDiagnosticField("last_ms")
    _ui_tick_max_ms = TickDiagnosticField("max_ms")
    _ui_tick_gap_ms = TickDiagnosticField("gap_ms")
    _ui_tick_gap_max_ms = TickDiagnosticField("gap_max_ms")
    _ui_tick_stages = TickDiagnosticField("stages")
    _ui_tick_perf_log_at = TickDiagnosticField("reported_at")
    _tick_error_count = TickDiagnosticField("count")
    _tick_last_error = TickDiagnosticField("last_error")
    _tick_last_log_at = TickDiagnosticField("last_logged_at")

    fps_samples = FrameTelemetryField("fps_samples")
    render_frame_ms_ema = FrameTelemetryField("ema_ms")
    _last_present_time = FrameTelemetryField("last_presented_at")
    _field_frame_times_ms = FrameTelemetryField("frame_times_ms")
    _last_field_frame_log_at = FrameTelemetryField("last_reported_at")

    # Startup state is shared with legacy adapters through named fields only.
    # Navigation decisions and transition ownership live outside the window.
    home_visible = StartupNavigationField("visible")
    startup_step = StartupNavigationField("page")
    startup_transition_started = StartupNavigationField("transition_started")
    startup_transition_from = StartupNavigationField("transition_from")
    startup_transition_to = StartupNavigationField("transition_to")
    _startup_page_epoch = StartupNavigationField("page_epoch")
    _startup_static_ready_at = StartupNavigationField("static_ready_at")

    _gpu_prewarm_jobs_pending = GpuWarmupField("pending")
    _gpu_prewarm_last_ms = GpuWarmupField("last_ms")
    _gpu_prewarm_max_ms = GpuWarmupField("max_ms")

    camera_worker = CameraActivationField("worker")
    camera_started = CameraActivationField("started")
    _camera_config = CameraActivationField("configuration")

    local_vision = VisionSelectionField("local")
    tongji_receiver = VisionSelectionField("receiver")
    tongji_bridge = VisionSelectionField("bridge")
    _vision_source = VisionSelectionField("source")
    _tongji_enabled = VisionSelectionField("tongji_enabled")

    @property
    def _window_close_finalized(self) -> bool:
        return self._window_lifecycle.finalized

    @_window_close_finalized.setter
    def _window_close_finalized(self, value: bool) -> None:
        self._window_lifecycle.finalized = value

    NORMAL_BACKEND_VIDEO_STALE_S = 2.0

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self._pointer_interaction = PointerInteraction()
        self._tick_diagnostics = TickDiagnostics()
        self._clickgui_presenter: ClickGuiGpuPresenter | None = None
        self._gpu_warmup = GpuWarmupQueue()
        self._gpu_prewarm_timer = create_warmup_timer(self, self._run_gpu_prewarm_slice)
        self.setWindowTitle(ui_text("POLAR-RM Custom Client", True))
        self.resize(1280, 720)
        self.setMinimumSize(1000, 620)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)

        self.paths = prepare_runtime_paths()
        self.workspace = self.paths.workspace_root
        self.registry = build_default_registry()
        self.state = RobotState()
        self.combat_hud_animator = CombatHudAnimator()
        # CPU fluorescent FOV ring cache: (radius_px, line_w, glow) → (image, half_extent).
        self._fluorescent_circle_cache: dict[tuple[int, int, int], tuple[QImage, float]] = {}
        self._combat_hud_last_time = time.perf_counter()
        self._ui_frame_index = 0
        self._spin_control_sequence = 0
        self._combat_hud_frame_index = -1
        self._jello_active_mods_frame_index = -1
        self.scenario_manager = ScenarioManager("LabDebug")
        self.match_mode_manager = MatchModeManager("RMUC7v7")
        self.mode_manager = GuiModeManager(PRIMARY_GUI_MODE)
        self.vision = MockVisionPipeline()
        self._camera_activation = CameraActivation(CameraCaptureWorker())
        # SharkClient 对齐后端：三种模式统一由 BackendManager 编排（仅接线，不动 UI）
        self.backend = BackendManager(
            workspace=self.workspace,
            state=self.state,
            client_id=str(self.state.robot_id),
            scenario_key=self.scenario_manager.current.key,
        )
        self._window_lifecycle = WindowLifecycleController(self.backend)
        self._window_close_finalized = False
        self.mqtt = self.backend.mqtt
        self.top_match_bar = TopMatchBarController(self)
        self.backend.referee_view_observer = self.top_match_bar.observe_referee
        self.official_feed = OfficialMatchFeed()
        self.vision_kill_detector = VisionKillDetector()
        self._official_state_updates: dict[str, Any] = {}
        feedback = create_feedback_assets(self.paths)
        self.kill_fx = feedback.kill_fx
        self.ui_audio = feedback.ui_audio
        from .endfield.lifecycle import create_presentations
        endfield = create_presentations(self.paths)
        self._endfield_feedback,self._endfield_boot,self._endfield_menu = endfield.feedback,endfield.boot,endfield.menu
        self.backend.referee_view_observer = self._observe_referee_views
        self.registry.add_toggle_listener(self.on_module_toggled)
        # P6: ModuleRegistry 在 toggle/set/direct assignment 三条 enabled
        # 写路径上统一推进代际，静态 key 不再每帧构造 N+1 元组。
        # P8: hud_* 色彩家族的帧级 memo；UI/配置写入路径会显式失效。
        self._hud_palette_cache: tuple[float, bool, QColor, QColor, QColor, QColor] | None = None
        # P2: 云台指示器 ARGB 草图缓冲（尺寸/dpr 匹配时复用，内容仍每帧重画）。
        self._gimbal_scratch_cache: tuple[tuple[object, ...], QImage] | None = None
        self._vision_scene_luminance = 0.0
        # UDP/串口由 backend 提供（保持旧属性名，其余代码无需改动）
        self.udp = self.backend.udp
        self.video_pipeline = VideoPipeline()
        self.serial = self.backend.serial
        # UDP 组装帧接入 VideoPipeline（转发/后续解码入口）
        # Backend UDP has already reassembled a complete frame. Feeding that
        # frame back into VideoPipeline.push_udp_packet treated image bytes as
        # a second Shark fragment header. Keep the compatibility pipeline for
        # explicit tools, but do not double-assemble the production stream.
        self.backend.udp.external_on_frame = None
        self.control = KeyboardControlBridge(self.backend, mock_output=True)
        self.backend.input_clear_hook = self._clear_control_inputs_for_lock
        self._clear_control_inputs_for_lock("startup locked")
        self._qualification_runtime_baseline = self._capture_qualification_runtime_baseline()
        self.auto_spin = ProjectileHitSpinController()
        self.economy_bridge = EconomyCommandBridge()
        self.economy_bridge.sender = self.send_economy_request
        self.config_store = ClientConfigStore(self.paths.configs_root)
        vision_services = create_vision_services(
            workspace=self.workspace, calibration_profiles=self.paths.calibrations_root,
            model_path=self._client_asset_path("同济自瞄", "sp_vision_25", "assets", "yolov5.xml"),
        )
        self.calibration = vision_services.calibration
        self._vision_selection = VisionSelection(
            local=vision_services.local, receiver=vision_services.receiver,
            bridge=vision_services.bridge,
        )
        self.gimbal_command_bridge = vision_services.gimbal_commands
        self.composite_vision = vision_services.composite
        self._vision_snapshot: VisionSnapshot | None = None
        # Capture consumption and display uploads have separate sequence spaces.
        self._normal_video_timeline = NormalVideoTimeline()
        self._last_vision_result: VisionResult | None = None
        self.vision_render_interpolator = VisionRenderInterpolator()
        self.target_hud_presence = TargetHudPresenceState()
        self.hud_angle_tracks: dict[str, AngleRenderTrack] = {}
        self.telemetry_render_tracks: dict[str, ScalarRenderTrack] = {}
        self._vision_render_result: VisionResult | None = None
        self._dynamic_hud_started_at = time.perf_counter()
        self.dynamic_hud_ms = 0.0
        self._armor_geometry_smooth: dict[tuple[int, int, bool, str], tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float], ...]]] = {}
        self.startup_wallpaper = create_startup_wallpaper(self.paths)
        self.scenario_manager.apply_to_registry(self.registry)
        self.scenario_manager.apply_to_runtime(self.mqtt, self.udp, self.control)
        self.match_mode_manager.apply_to_registry(self.registry)
        presentation = create_presentation_services(self.paths)
        self.fonts = presentation.fonts
        self.anim = presentation.animation
        self.gui_motion = presentation.motion
        self.gui_cache = presentation.gui_cache
        self.clickgui_gpu = presentation.clickgui_gpu
        self.jello_clickgui = presentation.jello
        self.minecraft_hud = presentation.minecraft_hud
        self._last_minecraft_hud_layout = None
        self.student_ui = presentation.student_state
        self.student_renderer = presentation.student_renderer
        self.server_chat_renderer = presentation.chat_renderer
        self.student_reload_expanded = False
        self._student_pending_panel = None
        gpu = create_gpu_services()
        self.vision_wire_gpu = gpu.vision_wire
        self.island_icon_gpu = gpu.island_icon
        self._gpu_island_icon_commands: list[IslandIconCommand] = []
        self._collect_gpu_island_icons = False
        self._gpu_hud_commands = ClickGuiDrawList()
        self._collect_gpu_hud_commands = False
        self._watermark_title_cache = QImage()
        self._watermark_title_cache_key: tuple[object, ...] | None = None
        self._watermark_title_static_key: tuple[object, ...] | None = None
        self._watermark_cache_next_refresh_at = 0.0
        self.quick_action_wheel_gpu = gpu.quick_action_wheel
        self._gpu_quick_action_wheel_command: QuickActionWheelCommand | None = None
        self._gpu_quick_action_wheel_labels = ClickGuiDrawList()
        self._collect_gpu_quick_action_wheel = False
        self.gpu_compositor = gpu.compositor
        self._gpu_frame_recording = False
        self._gpu_background_key: tuple[object, ...] | None = None
        self._gpu_background_transform_key: tuple[object, ...] | None = None
        self._gpu_control_backdrop_key: tuple[object, ...] | None = None
        self._gpu_blur_source_key: tuple[object, ...] | None = None
        self._gpu_static_ui_key: tuple[object, ...] | None = None
        self._startup_static_page_key: tuple[object, ...] | None = None
        self._economy_scene_epoch = 0
        self._gpu_background_changed = True
        # The full motion HUD is expensive to repaint with QPainter.  While a
        # high-rate interaction overlay is active, keep the latest complete
        # motion frame and draw only the wheel/chat into the isolated target
        # layer.  Camera uploads and the final present continue independently.
        self._gpu_motion_layer_ready = False
        self._gpu_motion_layer_updated_at = 0.0
        self.clickgui_gpu_layout_ms = 0.0
        self.startup_cache = StartupLayerCache()
        self.frame_scheduler = FrameScheduler()
        self.liquid_glass = gpu.liquid_glass
        self.startup_gpu = gpu.startup
        self.hud_video_gpu = gpu.hud_video
        # 部分 Windows 驱动会在 QPainter 与自定义纹理 shader 连续切页时
        # 损坏 QOpenGLWidget 的帧缓冲。Qt 自身仍负责 OpenGL 合成；先禁用
        # 这条原生视频 shader 路径，保证一级/二级页面切换绝不花屏。
        self.startup_gpu_enabled = False
        self.dynamic_smooth_values: dict[str, float] = {}
        self.dynamic_island_last_event = ""
        self.dynamic_island_event_started = time.perf_counter()
        self.frame_dt = 1.0 / 60.0
        # HUD 刷新与 24/30/60 FPS 的图传解耦。媒体纹理只在新源帧到达时上传，
        # 地图、灵动岛和交互动画继续使用独立的高刷新率 UI 时钟。
        self.render_fps_cap = 240.0
        self.render_fps_target = 144.0
        self._render_timer_interval_ms = max(4, int(round(1000.0 / self.render_fps_target)))
        self._frame_telemetry = FrameTelemetry()
        self._last_render_tune = time.perf_counter()
        self._last_logic_time = time.perf_counter()
        self._last_control_tick_time = 0.0
        self._hud_static_defer_since = 0.0
        self._last_module_toggle_at = 0.0
        # 阶段 1 外迁：文本度量三缓存（_text_metric_cache/_font_height_cache/
        # _elide_cache）改由 TextMetricsMixin 类级初始化，决策说明见
        # rm_client/ui/polar/text_metrics.py。
        # Coalesce slider/drag updates into one atomic profile write.  Saving a
        # complete JSON document for every mouse event caused avoidable frame
        # stalls even after the store itself became crash-safe.
        self._profile_saves = ProfileSaveState()

        scenario_module = self.registry.get("Scenario")
        self._startup_navigation = StartupNavigation(
            visible=self.setting_bool(scenario_module, "Show Startup", True),
        )
        self._startup_navigation.transition_started = time.perf_counter()
        # Page retirement remains independent from card entrance animation;
        # the renderer retains the old page until its replacement is complete.
        self._startup_navigation.defer_static(self.anim_duration(0.42))
        self._startup_last_hover_action = ""
        self.gui_visible = False
        self.gui_timeline = EndfieldWikiTimeline()
        self.jello_state = EndfieldWikiState()
        self.startup_page_morph = MorphState(progress=1.0, target=1.0, duration=0.42)
        self.dynamic_island_state = IslandContainerState(response_per_60hz_frame=0.20)
        self._bottom_left_diagnostics: list[tuple[str, QColor, int]] = []
        self.polar_unified_morph = MorphState(progress=0.0, target=0.0, duration=0.62)
        self.tactical_map_state = MapIslandState()
        # Screen-space field rect cached by the last tactical-map paint so a
        # click during the Tab-expanded hold can be mapped back to metres.
        self.tactical_map_field_rect = QRectF()
        self.tactical_map_field_layout: FieldLayout | None = None
        self.tactical_map_field_mirrored = False
        self.selected_category = "Combat"
        self.selected_module = self.registry.get("ArmorAutoAim") or self.registry.modules[0]
        self.search_query = ""
        self.search_focus = False
        self.hotspots: list[Hotspot] = []
        self.hover_tooltip = ""
        self.hover_point = QPointF(0.0, 0.0)
        self.liquid_press_point = QPointF(-1000.0, -1000.0)
        self.liquid_press_until = 0.0
        self.toasts: list[dict[str, Any]] = []
        # Inline settings start collapsed.  The previous pre-expanded set made
        # Old/New/Dropdown measure every settings block on first open.
        self.module_expanded: set[str] = set()
        self.module_list_rows: dict[str, dict[str, Any]] = {}
        self.settings_scroll: dict[str, float] = {}
        self.panel_module_scroll: dict[str, float] = {}
        # Each GPU scene publishes its own visible scroll region.  Input never
        # guesses from headers, which was the reason long lists bled below
        # their panels in Old/New/Dropdown.
        self.gui_scroll_viewports: list[dict[str, Any]] = []
        self.panel_settings_rect = QRectF()
        self.panel_modules_rect = QRectF()
        self.map_images: dict[str, QImage] = {}
        # Official maps are large source images. Keep their filtered, display-size
        # versions around so the HUD does not resample a PDF-derived field map on
        # every paint while the camera is running.
        self.map_display_cache: dict[tuple[str, int, int], QImage] = {}
        # Elision is queried by dozens of stable HUD labels every display
        # frame.  Cache only the resulting string (never a painter/font
        # texture) so the visual output and liquid-glass ownership stay
        # unchanged while avoiding repeated DirectWrite metric work at 2K.
        # （_elide_cache 现由 TextMetricsMixin 类级初始化，阶段 1 外迁。）
        self._module_list_sort_cache_key: tuple[object, ...] | None = None
        self._module_list_sort_widths: dict[str, float] = {}
        self.synthetic_video_cache: QImage | None = None
        self.synthetic_video_cache_key: tuple[int, int] | None = None
        self.synthetic_video_cache_time = 0.0
        self.frame_image: QImage | None = None
        self.frame_pixmap: QPixmap | None = None
        # 主 HUD 独立保存图传的低分辨率玻璃快照，绝不读取启动页壁纸。
        self._frame_telemetry.last_presented_at = time.perf_counter()
        self.last_frame_time = time.perf_counter()
        self._last_readiness_audit_at = 0.0
        self._frame_telemetry.last_reported_at = time.perf_counter()
        self.last_control_toast = 0.0
        self._control_mouse_point: QPointF | None = None
        self._combat_input_was_allowed = False
        self.tactical_map_hold = False
        self.config_loading = False
        self.wallpaper_picker_visible = False
        self.wallpaper_picker_page = 0
        # The launch surface is a complete native GPU pass. The battle viewport
        # still has a Qt HUD overlay and must not enter the old mixed framebuffer
        # experiment until it can use the same private-FBO final composite.
        # Keeping it off avoids trading stutter for intermittent corruption.
        self.gpu_viewport_enabled = False

        panels = create_panel_layouts(CATEGORIES)
        self.old_panels = panels.old
        self.new_panels = panels.new
        self.dropdown_panels = panels.dropdown
        self.polar_windows = panels.polar
        self.polar_unified_scroll = 0.0
        self.polar_unified_scroll_motion = InertialScroll()
        self.polar_settings_scroll_motion: dict[str, InertialScroll] = {}
        self.economy_visible = False
        self.economy_selected = 0
        self.economy_selected_key = ""
        self.economy_category = "ammo"
        self.economy_opened_at = 0.0
        self.economy_island = EconomyIslandState()
        self._economy_close_finalized = True
        self.economy_phase = EconomyScenePhase.CLOSED
        self._economy_generation = 0
        self._economy_closing_generation: int | None = None
        self._economy_restore_pending_generation: int | None = None
        self._economy_pending_overlay: OverlayKind | None = None
        self.economy_workspace_layer = EconomyWorkspaceLayer()
        self.economy_category_scroll = InertialScroll()
        self.economy_offers_scroll = InertialScroll()
        self.economy_detail_scroll = InertialScroll()
        self.economy_armed_key = ""
        self.economy_armed_until = 0.0
        self.economy_hold_key = ""
        self.economy_hold_started = 0.0
        self.economy_hold_rect = QRectF()
        self.economy_last_status_sequence = 0
        self.overlay_coordinator = OverlayCoordinator(on_input_claimed=self._neutralize_modal_inputs)
        self.quick_action_wheel = QuickActionWheelState()
        self.match_chat = MatchChatController()
        self.chat_event_adapter = ChatEventAdapter(self.match_chat)
        self.student_notice_adapter = StudentNoticeAdapter(self.match_chat)
        self._gpu_warm_pending = True

        def preload_startup_feedback() -> None:
            # Keep registry observation before feedback-service lookup, including
            # partial effects if either collaborator raises during startup.
            elimination_fx = self.registry.get("EliminationFX")
            self.kill_fx.preload_preset_sounds(
                self.setting_mode(elimination_fx, "Preset", "Default Scrolling"))

        try:
            activate_startup_session(
                StartupProfilePorts(
                    sync_wheel=lambda: self.sync_quick_action_wheel_settings(),
                    apply_match=lambda: self.apply_match_mode(
                        self.match_mode_manager.current.key, close_home=False, announce=False),
                    apply_robot=lambda: self.apply_robot_profile(
                        self.current_robot_profile_key(), close_home=False, announce=False),
                    refresh_profiles=lambda: self.refresh_config_profiles(),
                    refresh_wallpapers=lambda: self.refresh_wallpaper_profiles(),
                    refresh_sounds=lambda: self.refresh_custom_ui_sound_options(),
                    ensure_default=lambda: self.config_store.ensure_default(self.current_config_data()),
                    load_saved=lambda: self.load_startup_config(),
                ),
                StartupPresentationPorts(
                    update_title=lambda: self.setWindowTitle(
                        ui_text("POLAR-RM Custom Client", chinese_enabled(self.registry))),
                    preload_feedback=preload_startup_feedback,
                    apply_autostart=lambda: self._startup_navigation.apply_autostart(
                        os.environ.get("POLAR_AUTOSTART", "")),
                    sync_mode=lambda: self.sync_mode_setting(),
                    announce_ready=lambda: self.toast("ClickGUI 已就绪：Jello Sigma"),
                ),
                StartupScenarioPorts(
                    launcher_visible=lambda: self.home_visible,
                    configuration_pending=lambda: self.configuration_change_pending,
                    request=lambda: self.backend.request_scenario(
                        self.scenario_manager.current.key, registry=self.registry),
                    capture=lambda generation: self.capture_pending_scenario_configuration(generation),
                ),
            )

            from .window_shortcuts import WindowShortcuts
            WindowShortcuts(self)  # QObject parent owns the filter for this window.
            self._endfield_feedback.boot.mark("configuration", time.perf_counter())

            # Start event delivery only after profile staging and all callback
            # dependencies exist. Native resource activation still waits for poll.
            self.timer = create_frame_timer(self, self.on_tick)
            self.timer.start(self._render_timer_interval_ms)
        except BaseException as failure:
            # All service owners exist before activation. A failed profile,
            # shortcut or timer admission cannot abandon an already requested
            # lifecycle generation merely because the factory never returns.
            _abort_failed_launch(QApplication.instance(), self, self._abort_startup, failure)
            raise

    def on_tick(self) -> None:
        now = time.perf_counter()
        if not self._poll_backend_lifecycle():
            return
        self._begin_ui_frame(now)
        self._advance_input_and_scroll(self.frame_dt)
        self._advance_panel_presentation(now, self.frame_dt)
        self._advance_economy_transaction(now)
        self._advance_hud_presentation(self.frame_dt)
        self._advance_runtime_tick(now)
        self._request_ui_frame(now)
        self._publish_tick_performance(now)

    def _poll_backend_lifecycle(self) -> bool:
        update = self._window_lifecycle.poll(self._handle_backend_lifecycle_completion)
        if update.notification:
            self.toast(update.notification, kind="warning" if update.failed else "info")
        if update.schedule_close:
            if update.completion_warning:
                # A toast would disappear with the window. Keep confirmed
                # shutdown errors visible before accepting this safe exit.
                self._show_shutdown_warning(update.completion_warning)
            QTimer.singleShot(0, self.close)
        if not update.runtime_allowed:
            # Keep the event loop and status visible, but do not advance input,
            # economy, camera, decoder, or backend work while owners retire.
            self.update()
        return update.runtime_allowed

    def _show_shutdown_warning(self, message: str) -> None:
        """Dialog adapter: keep final errors visible until acknowledged."""
        QMessageBox.warning(None, "Shutdown warning", message)

    def _show_shutdown_cleanup_warning(self, message: str) -> None:
        """Owner-thread save/GL faults remain visible after native cleanup."""
        QMessageBox.warning(None, "Exit cleanup warning", message)

    def _handle_backend_lifecycle_completion(self, completion) -> bool:
        handled = self.on_backend_lifecycle_completion(completion)
        self.control.mock_output = bool(self.backend.mqtt.mock_output)
        return handled

    def _begin_ui_frame(self, now: float) -> None:
        self.flush_auto_save_if_due(now)
        self.gui_motion.begin_frame(now)
        self._ui_frame_index += 1
        timing = FrameTiming.since(now, self.last_frame_time)
        self._tick_diagnostics.record_wakeup(timing.gap_ms)
        self.last_frame_time = now
        self.frame_dt = timing.ui_dt
        ui_scale_module = self.registry.get("UIScale")
        refresh_setting = ui_scale_module.get_setting("HUD Refresh Rate") if ui_scale_module else None
        cadence = RenderCadence.resolve(
            refresh_setting.value if refresh_setting else 144.0,
            self.render_fps_cap, render_target,
        )
        self.render_fps_target = cadence.target_hz
        requested_interval = cadence.timer_interval_ms
        if requested_interval != self._render_timer_interval_ms:
            self._render_timer_interval_ms = requested_interval
            self.timer.setInterval(requested_interval)
            self.frame_scheduler.force()

    def _advance_input_and_scroll(self, ui_dt: float) -> None:
        self.polar_unified_scroll = self.polar_unified_scroll_motion.update(ui_dt)
        self.economy_category_scroll.update(ui_dt)
        self.economy_offers_scroll.update(ui_dt)
        self.economy_detail_scroll.update(ui_dt)
        self.quick_action_wheel.update(ui_dt)
        self.update_student_frontend(ui_dt)
        input_allowed = self.combat_control_input_allowed()
        if self._combat_input_was_allowed and not input_allowed:
            # Opening ClickGUI/chat/economy while holding a key must release the
            # robot immediately; waiting for a key-up event would leave motion
            # latched behind the modal input owner.
            self._neutralize_modal_inputs()
        self._combat_input_was_allowed = input_allowed
        for motion in self.polar_settings_scroll_motion.values():
            motion.update(ui_dt)

    def _advance_panel_presentation(self, now: float, ui_dt: float) -> None:
        controller = PanelPresentationController(
            self.gui_timeline, self.jello_state, self.polar_unified_morph,
            self.economy_island, self.active_clickgui_mode,
        )
        controller.advance(now, ui_dt, PanelIntent(
            gui_visible=self.gui_visible,
            gui_duration=self.anim_duration(0.62 if self.gui_visible else 0.48),
            economy_visible=self.economy_visible,
            economy_duration=self.anim_duration(0.62 if self.economy_visible else 0.48),
        ))
        if (
            self.economy_phase == EconomyScenePhase.OPENING
            and self.economy_island.progress >= 0.999
        ):
            self.economy_phase = EconomyScenePhase.OPEN
        if (
            self.economy_phase == EconomyScenePhase.CLOSING
            and self._economy_closing_generation == self._economy_generation
            and self._economy_restore_pending_generation is None
            and self.economy_island.progress <= 0.001
        ):
            # The event thread cannot safely clear QOpenGLFramebufferObjects.
            # Mark this generation for one atomic clear/rebuild/present pass;
            # modal input ownership is intentionally retained until it wins.
            self._economy_restore_pending_generation = self._economy_generation
            self.invalidate_transient_overlay_scene()
            self.frame_scheduler.force()
            self.update()

    def _advance_economy_transaction(self, now: float) -> None:
        self.state.economy_mock_mode = self.economy_uses_mock_transport()
        economy_transaction = self.economy_bridge.update(self.state, now)
        if (
            economy_transaction is not None
            and economy_transaction.status != "pending"
            and economy_transaction.sequence != self.economy_last_status_sequence
        ):
            self.economy_last_status_sequence = economy_transaction.sequence
            cue = self.economy_workspace_layer.cs2_feedback.observe(economy_transaction, now)
            if cue is not None:
                play_buy_sound(self, cue)
            self.toast(economy_transaction.message)
            match_chat = self.registry.get("MatchChat")
            if (
                match_chat is not None
                and match_chat.enabled
                and self.setting_bool(match_chat, "Referee Notices", True)
            ):
                self.chat_event_adapter.economy(economy_transaction, now)

    def _advance_hud_presentation(self, ui_dt: float) -> None:
        self.tactical_map_state.update(ui_dt)
        dynamic_payload = self.dynamic_island_payload()
        content_duration = dynamic_content_duration(
            active=dynamic_payload["active"], kind=dynamic_payload["kind"],
        )
        self.dynamic_island_state.set_content_target(
            1.0 if dynamic_payload["active"] else 0.0,
            content_duration,
        )
        self.dynamic_island_state.update(ui_dt)

    def _advance_runtime_tick(self, now: float) -> None:
        plan = RuntimeTickPlan.at(now, self._last_control_tick_time, self._last_logic_time)
        if plan.control_due and not self._modal_input_blocked():
            self._last_control_tick_time = now
            self._run_tick_stage(
                "control.75hz",
                lambda: self.control.tick(self.state, self.registry, now),
                now,
            )
        if plan.logic_dt is not None:
            logic_dt = plan.logic_dt
            self._last_logic_time = now
            if not self.home_visible:
                self._run_tick_stage("camera.runtime", self.ensure_camera_started, now)
            self.startup_wallpaper.set_active(self.home_visible)
            if not self.home_visible:
                self._run_tick_stage("vision.runtime", self.refresh_vision_runtime, now)
            self.consume_capture_frame()
            self.state.camera_connected = self.camera_worker.connected
            self.state.mqtt_connected = self.mqtt.connected
            # The full decoder health tree includes queue/process diagnostics;
            # do not rebuild it on every render tick merely for a status LED.
            self.state.udp_video_connected = self.udp.decoded_frame_fresh(
                self.NORMAL_BACKEND_VIDEO_STALE_S
            )
            self.state.serial_connected = self.serial.connected
            self.state.tick(logic_dt)
            # 后端：心跳/安全 tick + 全量协议解码写入 RobotState（UI 只读）
            stages = (
                ("backend.tick", self.backend.tick),
                ("backend.apply", lambda: self.backend.apply_updates(self.state)),
                ("backend.readiness", lambda: self.update_backend_readiness(now)),
                ("hero.lob", self.update_hero_lob_guard),
                ("auto.spin", lambda: self.update_auto_spin_guard(now)),
                ("official.consume", self.consume_official_messages),
                ("official.apply", self.apply_official_state),
                ("fx.hit_info", self.update_elimination_hit_info),
                ("vision.sync", self.sync_tongji_vision_state),
                ("vision.events", self.process_vision_kill_events),
                ("registry", lambda: self.registry.tick(self.state, logic_dt)),
                # 队内 0x5A 链路必须排在最后一环：它的 overlay 要覆盖
                # `state.tick()` 的桌面仿真值，排在前面会被仿真反向盖掉。
                # broker 地址要在 tick 里读：操作员在二级界面改了 Host/Port，
                # 得下一拍就落到真实连接上（不然界面显示的是一个没在用的地址）
                ("mqtt.link", lambda: self.backend.sync_mqtt_link(self.registry)),
                (
                    "polar.link",
                    lambda: self.backend.tick_polar_link(
                        self.state, self.registry, self.control
                    ),
                ),
            )
            for stage_name, callback in stages:
                self._run_tick_stage(stage_name, callback, now)
            if getattr(self, "_endfield_feedback", None) is not None:
                self._run_tick_stage("feedback.endfield", lambda: self._advance_endfield_feedback(now), now)
            match_chat = self.registry.get("MatchChat")
            if (
                match_chat is not None
                and match_chat.enabled
                and self.setting_bool(match_chat, "Referee Notices", True)
            ):
                self.chat_event_adapter.poll(self.state, now)
                self.student_notice_adapter.poll(self.state, now)

    def _request_ui_frame(self, now: float) -> None:
        high_rate = (
            not self.home_visible
            or self.startup_needs_high_rate()
            or self.polar_unified_scroll_motion.active
            or self.tactical_map_state.active()
            or self.dynamic_island_state.active()
            or self.economy_island.active
            or self.economy_island.progress > 0.01
            or self.quick_action_wheel.visible
            or self.match_chat.input_open
        )
        if self.frame_scheduler.should_request(now, high_rate, self.render_fps_target):
            self.update()

    def _observe_referee_views(self, name, data, received_at) -> None:
        # Both consumers see the same already-decoded sample. This adapter
        # adds no subscription, alternate parser, or command publisher.
        self.top_match_bar.observe_referee(name, data, received_at)
        self._endfield_feedback.observe_referee(
            name, data, received_at,
            identity=(self.backend.referee.profile_id, self.state.robot_id),
        )

    def _advance_endfield_feedback(self, now: float) -> None:
        from .endfield.lifecycle import advance_feedback
        advance_feedback(self,now)

    def _publish_tick_performance(self, now: float) -> None:
        report = self._tick_diagnostics.finish_tick(now, time.perf_counter())
        if report is None:
            return
        metrics = report.metrics()
        metrics.update(gpu_prewarm_last_ms=self._gpu_prewarm_last_ms,
                       gpu_prewarm_max_ms=self._gpu_prewarm_max_ms)
        self.state.backend_stats["ui_tick_performance"] = metrics
        try:
            self.backend.field_log.emit("ui_tick_timing", **metrics)
        except Exception:
            try:
                _LOG.warning("UI timing diagnostic could not be written", exc_info=True)
            except Exception:
                pass  # Timing diagnostics must not prevent the callback returning.

    def _run_tick_stage(self, name: str, callback: Any, now: float | None = None) -> bool:
        """Keep backend, vision and diagnostic faults inside their tick boundary."""
        runner = TickStageRunner(self.state.backend_stats, self.backend.field_log.emit, _LOG, time.perf_counter)
        return self._tick_diagnostics.run_stage(runner, name, callback, now=now)

    def update_backend_readiness(self, now: float | None = None, *, force: bool = False) -> dict[str, Any]:
        """Publish the fail-closed audit into runtime state for UI/diagnostics."""

        now = time.perf_counter() if now is None else float(now)
        previous = self.state.backend_stats.get("readiness")
        if not force and now - self._last_readiness_audit_at < 1.0 and isinstance(previous, dict):
            return previous
        self._last_readiness_audit_at = now
        payload = self.backend.apply_readiness(
            self.state,
            live_evidence={"video_source": self.readiness_video_source()},
        )
        return payload

    def readiness_video_source(self) -> str:
        source = self.setting_mode(self.registry.get("CameraFeed"), "Source", "")
        normalized = source.strip().lower()
        if normalized == "legacy official bridge":
            return "legacy_official_bridge"
        if normalized == "legacy vt02 udp":
            return "legacy_vt02_udp"
        if normalized in {"native", "native 0x0310", "0x0310"}:
            return "native_0310"
        if normalized in {"shark udp", "udp hevc", "hevc", "h265"}:
            return "udp"
        return ""

    def _capture_qualification_runtime_baseline(self) -> dict[str, Any]:
        """Snapshot process-local counters that persisted evidence cannot fake.

        Qualification buttons may only certify activity observed after this
        process/session baseline. A decoder frame, deploy transition, economy
        revision, or reconnect left over from an earlier run is not field
        evidence for a newly created session.
        """

        stats = self.backend.stats()
        safety = dict(stats.get("safety_evidence", {}))
        return {
            "captured_at_perf": time.perf_counter(),
            "captured_at_wall": time.time(),
            "economy_revision": int(self.state.economy_status_revision),
            "mqtt_reconnect_count": int(self.backend.mqtt.reconnect_count),
            "estop_at": float(self.backend.safety.estop_at),
            "mqtt_disconnect_lock_at": float(safety.get("mqtt_disconnect_lock_at", 0.0)),
            "mqtt_reconnected_locked_at": float(safety.get("mqtt_reconnected_locked_at", 0.0)),
            "udp_frames": int(dict(stats.get("udp", {})).get("decoded_frames", 0) or 0),
            "native_frames": int(
                dict(stats.get("native_0310", {})).get("decoded_frames", 0) or 0
            ),
        }

    def _qualification_baseline(self) -> dict[str, Any]:
        baseline = getattr(self, "_qualification_runtime_baseline", None)
        if not isinstance(baseline, dict):
            baseline = self._capture_qualification_runtime_baseline()
            self._qualification_runtime_baseline = baseline
        return baseline

    def resizeEvent(self, event) -> None:
        # QOpenGLWidget.resizeEvent recreates its FBO and synchronously calls
        # paintGL. Invalidate BEFORE delegating: a priority overlay can reuse
        # the motion layer, whose replacement texture has no pixels yet.
        self.gui_cache.clear()
        self.map_display_cache.clear()
        self.invalidate_transient_overlay_scene()
        # Economy text scales with the viewport. Prepare the new font pages on
        # the first idle frame after a resize instead of during the first B
        # press at that resolution.
        self._gpu_warm_pending = True
        super().resizeEvent(event)

    def initializeGL(self) -> None:
        from .render_runtime import RenderRuntime
        if not hasattr(self, 'render_runtime'):
            self.render_runtime = RenderRuntime()
        self.gpu_compositor.initialize()
        self.vision_wire_gpu.initialize()
        self.island_icon_gpu.initialize()
        self.quick_action_wheel_gpu.initialize()
        # Availability is runtime state. A context failure must not rewrite the
        # saved rendering preference; a later context may initialize correctly.
        self.clickgui_gpu.initialize()
        self.startup_gpu.initialize()
        self.hud_video_gpu.initialize()
        # Record the actual driver, not the configured backend label. This
        # distinguishes a failed GPU pass / slow compatibility paint from a
        # software or different-adapter OpenGL context on the user's machine.
        observed = False
        try:
            context = self.context()
            functions = context.extraFunctions()
            driver = {}
            for key, enum in (("vendor", 0x1F00), ("renderer", 0x1F01), ("version", 0x1F02)):
                value = functions.glGetString(enum)
                driver[key] = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
            self.render_runtime.initialized(driver, self.gpu_compositor.available, self.gpu_compositor.last_error)
            observed = True
            self.backend.field_log.emit(
                "renderer_init", **driver,
                stencil_bits=context.format().stencilBufferSize(),
                gpu_available=self.gpu_compositor.available,
                gpu_error=self.gpu_compositor.last_error,
            )
        except Exception as exc:
            if not observed:
                self.render_runtime.initialized({'diagnostic_error':str(exc)}, self.gpu_compositor.available,
                    self.gpu_compositor.last_error or str(exc))
            _LOG.debug("OpenGL driver diagnostics unavailable", exc_info=True)
        # Native passes only draw into private FBOs. The window framebuffer is
        # touched once by GpuFrameCompositor, avoiding Qt/GL state collisions.

    def paintGL(self) -> None:
        if self._window_lifecycle.closing:
            self._paint_shutdown_status()
            return
        started = time.perf_counter()
        gpu_presented = self.paint_gpu_frame()
        if not gpu_presented:
            # A modal renderer failure must never degrade into a camera-only
            # frame.  In particular, an economy offer can change availability
            # immediately after the referee/mock response.  If command-list
            # construction fails during that state transition, draw the
            # established compatibility workspace for this frame while the GPU
            # caches are rebuilt on the next tick.
            self._paint_compatibility_frame()
        if gpu_presented and self._gpu_warm_pending and self.clickgui_gpu.available:
            self._gpu_warm_pending = False
            QTimer.singleShot(0, self.prewarm_gpu_clickgui_resources)
        elif (gpu_presented and self._gpu_prewarm_jobs_pending is not None
              and not self._gpu_prewarm_timer.isActive()):
            # A hide or temporary invalid context pauses, rather than wedges,
            # the generator. Resume only after a successful visible GPU frame.
            self._gpu_prewarm_timer.start(16)
        presented = time.perf_counter()
        runtime = getattr(self, 'render_runtime', None)
        if runtime is not None:
            runtime.frame(gpu_presented, (presented-started)*1000, self.gpu_compositor.last_error)
        frame = self._frame_telemetry.record_frame(started, presented)
        self.state.fps = frame.fps
        if frame.report is not None:
            self.backend.field_log.emit(
                "frame_timing",
                **frame.report.metrics(),
                render_target_fps=self.render_fps_target,
                # frame_ms never existed and previously reported a misleading
                # constant zero. This legacy scalar is final-present CPU time;
                # last_pass_ms exposes the individual cached/pass timings.
                compositor_ms=self.gpu_compositor.last_present_ms,
                render_path="unified_gpu" if gpu_presented else "compatibility",
                gpu_error=self.gpu_compositor.last_error,
                last_pass_ms={
                    "background": self.gpu_compositor.last_background_ms,
                    "blur": self.gpu_compositor.last_blur_ms,
                    "glass": self.gpu_compositor.last_glass_ms,
                    "overlay": self.gpu_compositor.last_overlay_ms,
                    "dynamic": self.gpu_compositor.last_dynamic_ms,
                    "present": self.gpu_compositor.last_present_ms,
                },
                ui_tick_ms=self._tick_diagnostics.last_ms,
                ui_wakeup_gap_ms=self._tick_diagnostics.gap_ms,
                gpu_prewarm_last_ms=self._gpu_prewarm_last_ms,
                gpu_prewarm_max_ms=self._gpu_prewarm_max_ms,
                graphics_runtime=runtime.snapshot() if runtime is not None else {},
            )
            self._frame_telemetry.acknowledge_report(frame.report)
        # 不再依据视频或偶发纹理上传耗时自动降档。摄像头保持源帧率，
        # HUD 呈现频率由 UIScale.HUD Refresh Rate 独立控制。

    def _jello_gpu_fonts(self) -> dict[float, QFont]:
        return {
            # Exact sizes used by Sigma 5.1.0's PanelGroup, ModListView and
            # SettingGroup rather than approximations in the Polar type scale.
            14.0: self.font("jello_light", 14.0 * 72.0 / 96.0),
            18.0: self.font("jello_light", 18.0 * 72.0 / 96.0),
            20.0: self.font("jello_light", 20.0 * 72.0 / 96.0),
            25.0: self.font("jello_light", 25.0 * 72.0 / 96.0),
            40.0: self.font("jello_medium", 40.0 * 72.0 / 96.0),
            # ActiveMods (Jello module list) renders module names at
            # Normal/Small/Tiny pixel sizes. Without these pages the first
            # appearance of a name in the list rebuilds the shaped glyph
            # atlas mid-animation and visibly stutters the 150 ms fade.
            15.0: self.font("jello_light", 15.0 * 72.0 / 96.0),
            13.5: self.font("jello_light", 13.5 * 72.0 / 96.0),
            10.5: self.font("jello_light", 10.5 * 72.0 / 96.0),
        }

    def _jello_module_text_pages(
        self,
        module: Module,
        fonts: dict[float, QFont],
    ) -> dict[float, set[str]]:
        """Return immutable shaped lines used by one settings panel."""

        pages: dict[float, set[str]] = {
            14.0: set(),
            18.0: {">", "Run", "Show all", "Hide", "执行", "显示全部", "收起"},
            20.0: {
                line
                for value in (module.description, jello_description_label(module.description, True))
                for line in self.jello_clickgui.wrapped_description(fonts[20.0], value, 440.0)
            },
            25.0: set(),
            40.0: {module.name, jello_module_label(module.name, True)},
        }
        legacy_labels = {
            "PolarOld": "Polar Old",
            "PolarPanel": "Polar Panel",
            "PolarNew": "Polar New",
            "PolarDropdown": "Polar Dropdown",
            "PolarUnified": "Polar One",
            "PolarMulti": "Polar Multi",
        }
        for setting in module.settings:
            localized_setting = jello_setting_label(setting.name, True)
            pages[14.0].update((setting.name, localized_setting))
            for value in (setting.description, jello_description_label(setting.description, True)):
                pages[14.0].update(self.jello_clickgui.wrapped_description(fonts[14.0], value, 460.0))
            pages[25.0].update((setting.name, localized_setting))
            if setting.kind == "action_group":
                for option in setting.options:
                    legacy = legacy_labels.get(str(option), str(option))
                    if str(option) in legacy_labels:
                        pages[14.0].update((legacy, f"旧版 {legacy}"))
                    else:
                        for label in (str(option), jello_action_label(str(option), True)):
                            pages[14.0].add(label)
                            pages[14.0].add(self.jello_clickgui._elided_module_name(fonts[14.0], label, 107.0))
            if setting.kind != "mode":
                continue
            values = {str(setting.value), *(str(option) for option in setting.options)}
            for value in values:
                for display_value in (value, jello_option_label(value, True)):
                    pages[18.0].add(
                        self.jello_clickgui._elided_module_name(
                            fonts[18.0], display_value, 82.0
                        )
                    )
                    pages[18.0].add(
                        self.jello_clickgui._elided_module_name(
                            fonts[18.0], display_value, 103.0
                        )
                    )
        return pages

    def prewarm_jello_module_resources(self, module: Module) -> None:
        """Warm one settings page before its 200/240 ms reveal starts."""

        if isinstance(self.jello_clickgui, EndfieldWikiRenderer):
            # Wiki 的字形页由空闲队列准备；首次缺页仍由统一 GPU prepare_frame 补齐。
            return
        if not self.clickgui_gpu.available or self.context() is None:
            return
        fonts = self._jello_gpu_fonts()
        pages = self._jello_module_text_pages(module, fonts)
        click_gui = self.registry.get("ClickGui")
        scale_setting = click_gui.get_setting("Scale") if click_gui is not None else None
        requested_scale = float(scale_setting.value) if scale_setting is not None else 1.0
        effective_scale = self.jello_clickgui.layout(
            max(1, self.width()),
            max(1, self.height()),
            requested_scale,
        ).scale
        raster_scale = self.jello_clickgui.raster_scale_for(effective_scale)
        self.makeCurrent()
        try:
            for size, font in fonts.items():
                # _jello_gpu_fonts also carries the ActiveMods (module list)
                # pages, which have no settings-page text; skip those sizes.
                self.clickgui_gpu.prewarm(
                    (font,),
                    pages.get(size, ()),
                    self.devicePixelRatioF(),
                    raster_scale=raster_scale,
                    preserve_weight=True,
                    text_profile="jello",
                )
        finally:
            self.doneCurrent()

    def prewarm_gpu_clickgui_resources(self) -> None:
        """Queue incremental GL warmup; preserve event-loop turns between pages."""
        if self._window_lifecycle.closing:
            return
        if not self.clickgui_gpu.available or not self.gpu_compositor.available:
            return
        self._gpu_warmup.replace(self._gpu_prewarm_jobs())
        self._gpu_prewarm_timer.start(16)

    def _gpu_prewarm_jobs(self):
        """Allocate shaders and glyph atlases while the launcher is idle.

        Right Shift must only change animation uniforms.  It must not compile a
        shader, allocate an atlas, or rasterise every module label on its first
        press.
        """
        if not self.clickgui_gpu.available:
            return
        if isinstance(self.jello_clickgui, EndfieldWikiRenderer):
            yield from self.jello_clickgui.prewarm_jobs(self.registry, self.devicePixelRatioF())
        labels = {
            "POLAR-RM", "Search modules...", "Keybind", "None", "KEY", "--", "POLAR VIEW",
            "Combat", "Movement", "Vision", "Strategy", "System", "Render",
            "Profile", "Settings", "Module", "Enable", "Disable",
            "SCENE SELECT", "MATCH MODE", "ROBOT PROFILE", "SESSION",
            "DJI Official", "Campus Sim", "Lab Debug", "RMUC 7v7", "RMUL 3v3",
            "Config", "Import", "Wallpaper", "ClickGUI", "Exit", "Back",
            "Jello Sigma", "Old ClickGUIs", "Show all", "Hide", "ClickGui options",
            "Polar Old", "Polar Panel", "Polar New", "Polar Dropdown", "Polar One", "Polar Multi",
            "CUSTOM CLIENT", "Choose runtime environment", "Press 1/2/3 to select. Enter continues.",
            # Chinese panel subtitle glyphs (配置 / 缩放) + common toast fragments.
            "配置", "缩放", "已就绪", "已保存", "已加载", "导入",
        }
        labels.update(ui_text(label, True) for label in tuple(labels))
        jello_labels_by_size: dict[float, set[str]] = {
            14.0: set(),
            18.0: {">", "Run", "Show all", "Hide", "执行", "显示全部", "收起"},
            20.0: set(),
            25.0: {*CATEGORIES, *(jello_category_label(category, True) for category in CATEGORIES)},
            40.0: set(),
            # ActiveMods font pages (Normal/Small/Tiny), filled below with
            # every module name so the list fade never rebuilds the atlas.
            15.0: set(),
            13.5: set(),
            10.5: set(),
        }
        numeric_characters = set("0123456789.-+%/abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ ")
        for module_index, module in enumerate(self.registry.modules):
            labels.add(module.name)
            labels.add(module.category)
            labels.add(module.keybind)
            # ActiveMods can switch Normal/Small/Tiny while the HUD remains
            # visible.  Warm all three exact Sigma font pages so that the GPU
            # path never allocates a glyph atlas in a combat frame.
            for size in (14.0, 18.0, 20.0):
                jello_labels_by_size[size].add(module.name)
            for size in (15.0, 13.5, 10.5):
                jello_labels_by_size[size].add(module.name)
                jello_labels_by_size[size].add(jello_module_label(module.name, True))
            for setting in module.settings:
                labels.add(setting.name)
                labels.add(setting.display_value())
            if module_index % 8 == 7:
                yield ("prepare", (), {})
        fonts = (
            self.font("poppins_regular", 8.0),
            self.font("poppins_medium", 8.0),
            self.font("poppins_bold", 9.0),
            self.font("poppins_regular", 7.2),
            self.font("poppins_medium", 8.2),
            self.font("poppins_bold", 18.0),
            self.font("museo", 28.0, QFont.Weight.Bold),
            self.font("material", 12.0, QFont.Weight.Bold),
        )
        jello_fonts = self._jello_gpu_fonts()
        profile = self.current_profile()
        jello_labels_by_size[20.0].update((profile, f"{profile} · EN", f"{profile} · 中文"))
        for module_index, module in enumerate(self.registry.modules):
            # Disabled/enabled rows reserve 168/160 logical pixels
            # respectively.  Prewarm both terminal-ellipsis variants.
            jello_labels_by_size[20.0].add(
                self.jello_clickgui._elided_module_name(
                    jello_fonts[20.0], module.name, 168.0
                )
            )
            jello_labels_by_size[20.0].add(
                self.jello_clickgui._elided_module_name(
                    jello_fonts[20.0], module.name, 160.0
                )
            )
            localized_module = jello_module_label(module.name, True)
            jello_labels_by_size[20.0].add(
                self.jello_clickgui._elided_module_name(
                    jello_fonts[20.0], localized_module, 168.0
                )
            )
            jello_labels_by_size[20.0].add(
                self.jello_clickgui._elided_module_name(
                    jello_fonts[20.0], localized_module, 160.0
                )
            )
            if module_index % 8 == 7:
                yield ("prepare", (), {})
        for font in fonts:
            yield ("text", ((font,), labels, self.devicePixelRatioF()), {})
        click_gui = self.registry.get("ClickGui")
        if click_gui is not None:
            click_gui_pages = self._jello_module_text_pages(click_gui, jello_fonts)
            for size, values in click_gui_pages.items():
                jello_labels_by_size[size].update(values)
        scale_setting = click_gui.get_setting("Scale") if click_gui is not None else None
        requested_scale = float(scale_setting.value) if scale_setting is not None else 1.0
        effective_scale = self.jello_clickgui.layout(
            max(1, self.width()),
            max(1, self.height()),
            requested_scale,
        ).scale
        current_raster_scale = self.jello_clickgui.raster_scale_for(effective_scale)
        # Warm only the current density. Other scales are prepared on demand,
        # instead of spending seconds on six pages the user may never see.
        raster_scales = (current_raster_scale,)
        for raster_scale in raster_scales:
            yield ("text", ((jello_fonts[14.0],), ("".join(sorted(numeric_characters)),),
                            self.devicePixelRatioF()),
                   {"raster_scale": raster_scale, "preserve_weight": True})
            for size, font in jello_fonts.items():
                yield ("text", ((font,), jello_labels_by_size[size], self.devicePixelRatioF()),
                       {"raster_scale": raster_scale, "preserve_weight": True, "text_profile": "jello"})
        for source in self.jello_clickgui.image_assets:
            yield ("image", ((source,),), {})
        if "buy_icon" in self.kill_fx.cs2.assets.paths:
            yield ("image", ((self.kill_fx.cs2.assets.paths["buy_icon"],),), {})
        from .cs2_buy_menu import icon_path
        for category in ("ammo", "recovery", "revive", "support"):
            yield ("image", ((icon_path(self,category),),), {})
        for source in (self.minecraft_hud.asset_root / "hotbar.png",
                       self.minecraft_hud.asset_root / "hotbar_selection.png",
                       self._client_asset_path("assets", "jello", "hud_shadow.png")):
            yield ("image", ((source,),), {})

        # The economy workspace uses responsive font sizes and substantially
        # more CJK glyphs than ClickGUI. Prewarm the exact pages for the
        # current viewport so opening it never mutates a glyph atlas.
        economy_labels = {
            "战斗经济", "模拟", "裁判", "累计", "待扣", "可选操作",
            "弹丸", "恢复", "复活", "支援", "仅状态", "预计价格",
            "模拟请求", "裁判系统", "可以购买", "最近交易", "暂无记录",
            "请先选择商品", "请求处理中", "当前不可用", "按住 0.6 秒确认",
            "确认中", "再点一次确认", "确认购买", "关闭经济工作区",
            "等待确认", "等待裁判系统", "操作成功", "请求被拒绝",
            "确认超时", "金币", "远程", "补给区", "二次确认后发送",
            "HP", "AMMO", "MOCK", "G", "0 1 2 3 4 5 6 7 8 9",
            "× / · % . - [ ]",
            self.match_mode_manager.current.title,
            profile_by_key(self.state.match_mode, self.state.robot_profile, season=self.state.rules_season).title,
        }
        for title in CATEGORY_TITLES.values():
            economy_labels.add(title)
        for label in TRANSACTION_LABELS.values():
            economy_labels.add(label)
        for offer in self.economy_offers():
            economy_labels.update(
                (
                    offer.title,
                    offer.short_title,
                    offer.description,
                    offer.unit,
                    offer.reason,
                )
            )
        economy_font_specs = (
            ("poppins_bold", 17.0),
            ("poppins_bold", 15.0),
            ("poppins_bold", 13.5),
            ("poppins_bold", 12.0),
            ("poppins_bold", 10.5),
            ("poppins_bold", 10.0),
            ("poppins_bold", 9.5),
            ("poppins_bold", 9.0),
            ("poppins_bold", 8.6),
            ("poppins_bold", 8.2),
            ("poppins_bold", 6.6),
            ("poppins_medium", 9.0),
            ("poppins_medium", 8.4),
            ("poppins_medium", 8.2),
            ("poppins_medium", 8.0),
            ("poppins_medium", 7.8),
            ("poppins_medium", 7.4),
            ("poppins_medium", 7.2),
            ("poppins_medium", 7.0),
        )
        # Only one of the quantised pages can be rendered at the current
        # viewport.  A resize requests another warmup before the next frame,
        # so building all five pages here only blocked startup and recreated
        # four textures that could not be sampled.
        economy_scale = self.economy_ui_scale(self.width(), self.height())
        economy_fonts = tuple(
            self.font(family, size * economy_scale)
            for family, size in economy_font_specs
        )
        for font in economy_fonts:
            yield ("text", ((font,), economy_labels, self.devicePixelRatioF()), {})
        # CS2 matrix has fixed typography. Warm resident numeric glyphs and
        # static shaped lines separately: changing gold/ammo never uploads a
        # new full-line texture during animation.
        from .fonts import cjk_ui_font
        from .cs2_buy_menu import grid_layout
        matrix = grid_layout(self.economy_workspace_layer,self.width(),self.height(),1.0)
        matrix_scale = max(.78,min(1.10,matrix.target_panel.width()/1500))
        matrix_labels = set(economy_labels)
        matrix_labels.update({"战斗购买","可用金币本次已确认模拟请求裁判系统", "1 弹丸","2 恢复","3 复活","4 支援","5 补给站",
                              "发弹余量","弹药库储备","实体弹丸","补给位置","等待官方状态","已在补给区","未在补给区","本赛制无此商品",
                              "Esc / B 返回  ·  选择商品后在右侧确认  ·  所有交易沿用裁判链路"})
        for bold,size in ((True,20),(True,14),(False,9.1),(False,9.2),(False,9.3),
                          (False,9.5),(True,9.5),(True,13.5),(True,10.5),(False,9),(True,10),(False,8.6)):
            font = self.font("poppins_bold" if bold else "poppins_medium",size*matrix_scale)
            yield ("text",((font,),matrix_labels,self.devicePixelRatioF()),
                   {"preserve_weight":True,"text_profile":"jello"})
        for bold,size in ((False,9.3),(True,9.5),(True,10.5)):
            font = cjk_ui_font(self.font("poppins_bold" if bold else "poppins_medium",size*matrix_scale))
            yield ("text",((font,),matrix_labels,self.devicePixelRatioF()),{"preserve_weight":True})


    def _run_gpu_prewarm_slice(self) -> None:
        if self._window_lifecycle.closing:
            return
        jobs = self._gpu_prewarm_jobs_pending
        if jobs is None or not self.gpu_compositor.available or not self.isVisible():
            return
        # Atlas work is optional while these UI surfaces are interactive.
        # Keep the iterator intact; a successful frame resumes it after close.
        # Esc uses Qt text, not these atlases. Do not let a cold font upload
        # compete with menu input or native settings construction/animation.
        if (self.escape_menu_visible or QApplication.activeModalWidget() is not None
                or getattr(self, "_settings_session", None) is not None):
            return
        context = self.context()
        if context is None or not context.isValid():
            return
        pending = self._gpu_warmup.run_slice(
            jobs,
            uploads=self.clickgui_gpu,
            acquire_context=self.makeCurrent,
            release_context=self.doneCurrent,
            report_error=lambda exc: self.backend.field_log.emit("gpu_prewarm_error", error=str(exc)),
            clock=time.perf_counter,
        )
        if pending:
            self._gpu_prewarm_timer.start(16)


    def paint_direct_gpu_background(self) -> bool:
        """Use native GL only for the opaque source image, then hand the UI to Qt."""
        context = self.context()
        if context is None:
            return False
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return False
        functions = context.extraFunctions()
        ratio = self.devicePixelRatioF()
        canvas = QRectF(0, 0, width, height)
        try:
            functions.glViewport(0, 0, max(1, round(width * ratio)), max(1, round(height * ratio)))
            functions.glClearColor(0.0, 0.0, 0.0, 1.0)
            functions.glClear(0x00004000)
            if self.home_visible:
                module = self.registry.get("StartupVisual")
                mode = self.setting_mode(module, "Background Mode", "VideoParallax")
                profile = self.setting_mode(module, "Wallpaper Profile", "TexasMP4")
                if mode in ("ParticleGrid", "PkgScene") or profile in ("ParticleGrid", "Default3000925581Pkg") or not self.startup_gpu.available:
                    return False
                snapshot = self.startup_wallpaper.prepare_gpu_video(
                    canvas,
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
                self.startup_gpu.update_frame(
                    snapshot.image,
                    self.startup_wallpaper.last_draw_rect,
                    snapshot.sequence,
                    snapshot.frame_interval,
                    self.get_setting_number(module, "Dim Overlay", 0.28),
                    origin=TextureOrigin.TOP_LEFT_IMAGE,
                )
                if not self.startup_gpu.render(functions, canvas):
                    return False
                self.liquid_glass.update(
                    "startup",
                    snapshot.image,
                    self.startup_wallpaper.last_draw_rect,
                    soft=snapshot.soft_glass,
                    lens=snapshot.glass,
                )
                self.paint_overlay(None, startup_background_ready=True)
                return True
            if (
                self.frame_image is None
                or self.frame_image.isNull()
                or not self.hud_video_gpu.available
                or not self.normal_video_frame_ready()
            ):
                return False
            self.hud_video_gpu.update_frame(self.frame_image, canvas, self._display_frame_sequence, 1.0 / 30.0, 0.0)
            self.hud_video_gpu.discard_interpolation_history()
            if not self.hud_video_gpu.render(functions, canvas, interpolate=False):
                return False
            self.paint_overlay(None, hud_background_ready=True)
            return True
        except Exception:
            return False

    def _paint_gpu_frame_cached(self) -> bool:
        """在稳定启动页复用静态 GPU 层，hover 只提交一张小动态图层。"""
        if not self.gpu_compositor.available:
            return False
        context = self.context()
        if context is None:
            return False
        width, height = self.width(), self.height()
        ratio = self.devicePixelRatioF()
        try:
            viewport = FrameViewport(width, height, ratio)
        except ValueError:
            return False
        storage_generation = self.gpu_compositor.fbo_generation
        if width <= 0 or height <= 0 or not self.gpu_compositor.ensure_size(width, height, ratio):
            return False
        if self.gpu_compositor.fbo_generation != storage_generation:
            # Qt handles DPR-only changes without resizeEvent. Storage or its
            # DPR interpretation changed, including a priority overlay's
            # frozen motion layer. Forget readiness before any cache decision.
            self.invalidate_transient_overlay_scene()
        functions = context.extraFunctions()
        presented = False
        committed = False
        recovered = False
        try:
            restore_generation = self._economy_restore_pending_generation
            restore_attempt = bool(
                restore_generation is not None
                and restore_generation == self._economy_generation
                and self.economy_phase == EconomyScenePhase.CLOSING
            )
            if restore_attempt:
                if not self.gpu_compositor.clear_scene_layers(
                    functions,
                    ("overlay", "dynamic", "target"),
                ):
                    return False
                # Atomically invalidate every metadata key in the same GL
                # frame as the physical FBO clear.  The normal branches below
                # must now repopulate static and motion layers before present.
                self.invalidate_transient_overlay_scene()
            startup_scene = self.home_visible and not self.wallpaper_picker_visible and self.clickgui_gpu.available
            static_key = self.gpu_static_ui_key(width, height, ratio)
            rebuild_hud_static = not startup_scene and static_key != self._gpu_static_ui_key
            if rebuild_hud_static and self._should_defer_hud_static_rebuild(static_key):
                # A module toggle repaints the whole static HUD snapshot and
                # the treated backdrop on the exact frame the 150 ms ActiveMods
                # fade starts, which read as a hitch. The stale snapshot is
                # invisible under the live motion layer, so wait for the row
                # animation to settle (bounded) before rebuilding.
                rebuild_hud_static = False
            self.gpu_compositor.begin_frame(rebuild_static_ui=rebuild_hud_static)
            if not self.gpu_compositor.clear_target_layer(functions):
                return False
            self._gpu_frame_recording = True
            if not self.paint_gpu_background(functions, width, height, ratio):
                return False
            scene = OverlayScene.resolve(
                startup_scene=startup_scene,
                economy_visible=self.economy_visible,
                economy_progress=self.economy_island.progress,
                clickgui_visible=self.gui_visible,
                clickgui_progress=self.gui_timeline.progress,
                student_visible=self.student_ui.visible,
                escape_visible=self.escape_menu_visible,
            )
            modal_backdrop_active = scene.modal_backdrop_active
            jello_blur_radius = (
                self.gui_timeline.blur_radius()
                if scene.jello_blur_active and self.active_clickgui_mode() == PRIMARY_GUI_MODE
                else None
            )
            treatment = BackdropTreatment.resolve(
                scene, economy_progress=self.economy_island.progress,
                clickgui_progress=self.gui_timeline.progress,
                jello_blur_radius=jello_blur_radius,
                student_panel=self.student_ui.panel,
                student_progress=self.student_ui.progress,
            )
            treatment_changed = treatment.apply(self.gpu_compositor)
            control_changed = self.paint_gpu_control_backdrop(
                functions,
                width,
                height,
                ratio,
                startup_scene=startup_scene,
                economy_active=scene.economy_active,
            )
            if self.gpu_compositor.frame_rejected:
                return False
            # Modal vision must never punch crisply through the treated
            # backdrop. Economy additionally clears the complete HUD layer;
            # Jello keeps the cached HUD under Sigma's blur.
            if modal_backdrop_active:
                if not self.gpu_compositor.clear_vision_layer(functions):
                    return False
                native_vision_owner = False
            else:
                native_vision_owner = self.paint_gpu_vision_layer(functions, width, height, ratio)
                if self.gpu_compositor.frame_rejected:
                    return False

            if startup_scene:
                transition_active = (
                    self.startup_transition_from is not None
                    and self.startup_transition_to is not None
                    and time.perf_counter() - self.startup_transition_started < self.anim_duration(0.42)
                )
                static_mode = (StaticLayerMode.STARTUP_TRANSITION if transition_active
                               else StaticLayerMode.STARTUP_STABLE)
                launcher_key = None if transition_active else (
                    *static_key, self.scenario_manager.current.key,
                    self.match_mode_manager.current.key, chinese_enabled(self.registry),
                )
            else:
                static_mode = (StaticLayerMode.ECONOMY if scene.economy_active else
                               StaticLayerMode.HUD_REBUILD if rebuild_hud_static else StaticLayerMode.REUSE)
                launcher_key = None
            economy_gpu = scene.economy_active and self.clickgui_gpu.available
            gui_alpha = self.gui_timeline.progress
            prepare_clickgui = False
            if not startup_scene and not economy_gpu:
                click_gui = self.registry.get("ClickGui")
                prepare_clickgui = (
                    (self.gui_visible or gui_alpha > 0.01)
                    and self.setting_mode(click_gui, "Render Backend", "Unified GPU") == "Unified GPU"
                    and self.clickgui_gpu.available
                )
            motion = MotionLayerInputs(
                radial_visible=self.quick_action_wheel.visible,
                chat_open=self.match_chat.input_open,
                student_visible=self.student_ui.visible,
                escape_visible=self.escape_menu_visible,
            )
            plan = OverlayLayerPlan(
                viewport=viewport, storage_generation=self.gpu_compositor.fbo_generation,
                scene=scene, static_mode=static_mode, static_key=static_key,
                startup_key=launcher_key, economy_gpu=economy_gpu,
                prepare_clickgui=prepare_clickgui, motion=motion,
            )
            cache = LayerCacheSnapshot(
                static_key=self._gpu_static_ui_key, startup_key=self._startup_static_page_key,
                motion_ready=self._gpu_motion_layer_ready,
                motion_updated_at=self._gpu_motion_layer_updated_at,
            )
            home_visible = self.home_visible

            def draw_static(painter) -> None:
                self.paint_overlay(
                    None, startup_background_ready=True, hud_background_ready=True,
                    painter=painter, logical_size=(width, height), include_interactive=False,
                    split_motion_layer=not modal_backdrop_active,
                    capture_module_list=modal_backdrop_active,
                )

            def draw_motion(session, prepared) -> bool:
                session.defer_cleanup("gpu-commands", self._discard_gpu_command_collections)
                self.begin_gpu_hud_command_collection()
                self.begin_gpu_island_icon_collection()
                self.begin_gpu_quick_action_wheel_collection()
                painter = session.begin_painter()
                if not home_visible and scene.combat_motion_active:
                    self.paint_hud_motion_overlay(
                        painter, width, height, include_vision=not native_vision_owner,
                        include_target_hud=(self.menu_overlay_active() and not motion.priority_active),
                        include_chat=not motion.chat_open, include_radial=not motion.radial_visible,
                    )
                if scene.draws_clickgui(home_visible=home_visible):
                    self.paint_clickgui_overlay(
                        painter, width, height, gpu_commands=prepared.commands,
                        gpu_frame=prepared.frame, gpu_atlas_prepared=prepared.atlas_prepared,
                    )
                if not session.end_painter():
                    return False
                self.flush_gpu_hud_commands(functions, width, height, ratio)
                self.flush_gpu_island_icons(functions, width, height, ratio)
                self.flush_gpu_quick_action_wheel(functions, width, height, ratio)
                return not self.gpu_compositor.frame_rejected

            draws = OverlayDrawPorts(
                clear_startup_interactions=self.hotspots.clear,
                startup_commands=lambda: self.build_gpu_startup_launcher(
                    width, height, page=self.startup_step, settled=True),
                startup_interaction=lambda cached: self.paint_gpu_startup_interaction_layer(
                    functions, width, height, ratio, **({"cached_static": True} if cached else {})),
                static_hud=draw_static,
                economy_commands=lambda: self.build_gpu_economy_workspace(width, height),
                clickgui_frame=lambda: build_clickgui_frame(self, width, height, gui_alpha),
                motion_hud=draw_motion,
                priority_interaction=lambda: self.paint_priority_interaction_layer(functions, width, height, ratio),
                target_hud=lambda: (self.gpu_compositor.clear_target_layer(functions)
                                    if tab_scoreboard_active(self) else
                                    self.paint_gpu_target_hud_layer(functions, width, height, ratio)),
            )
            updated = OverlayLayerRenderer(
                self.gpu_compositor, self.clickgui_gpu, clock=time.perf_counter,
            ).render(functions, plan, cache, draws)
            if updated is None:
                return False
            self._gpu_static_ui_key = updated.static_key
            self._startup_static_page_key = updated.startup_key
            self._gpu_motion_layer_ready = updated.motion_ready
            self._gpu_motion_layer_updated_at = updated.motion_updated_at

            if self.gpu_compositor.frame_rejected:
                return False
            composition = FrameCompositionPlan(
                background_changed=self._gpu_background_changed,
                control_changed=control_changed,
                treatment_changed=treatment_changed,
                static_ui_rebuilt=rebuild_hud_static,
                overlay_owners=scene.glass_owners(
                    radial_visible=self.quick_action_wheel.visible,
                ),
                target_framebuffer=self.defaultFramebufferObject(),
            )
            presented = FinalFrameCompositor(self.gpu_compositor).present(functions, composition)
            if (native := getattr(self, "_endfield_menu", None)) is not None:
                native.backdrop_frame_ready(presented)
            if presented and restore_attempt and restore_generation is not None:
                self._finalize_economy_restore(restore_generation)
            committed = presented
            return committed
        except Exception as exc:
            self.gpu_compositor.recover_failed_frame(exc)
            recovered = True
            # Never preserve metadata for a partially rendered modal frame.
            # The compatibility fallback below remains visible for the current
            # present; the next GL frame then performs a complete, deterministic
            # rebuild.  Economy restore generations intentionally stay pending.
            return False
        finally:
            if not committed:
                if not recovered:
                    self.gpu_compositor.recover_rejected_frame()
                self.invalidate_transient_overlay_scene()
                self.frame_scheduler.force()
            self._gpu_frame_recording = False

    def paint_gpu_control_backdrop(
        self,
        functions,
        width: int,
        height: int,
        ratio: float,
        *,
        startup_scene: bool,
        economy_active: bool = False,
    ) -> bool:
        """绘制位于镜片下方的控件轨道，并返回本次是否更新。"""
        if economy_active:
            key = (
                "economy",
                self._economy_scene_epoch,
                width,
                height,
                round(ratio, 3),
            )
        else:
            key = (
                "startup" if startup_scene else "hud",
                self.startup_step if startup_scene else "battle",
                self._startup_page_epoch if startup_scene else 0,
                width,
                height,
                round(ratio, 3),
            )
        if key == self._gpu_control_backdrop_key:
            return False
        def draw_control(session) -> bool:
            if economy_active:
                # The reference Control Center applies dim and blur to the complete
                # captured backdrop in the compositor. This layer intentionally
                # stays transparent; rebuilding a full-screen QPainter mask on
                # every spring frame caused both pulsing and avoidable stalls.
                pass
            elif startup_scene and self.startup_step in {"scenario", "match"}:
                painter = session.begin_painter()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                scale = max(0.74, min(1.14, self.get_auto_ui_scale(width, height, strength=0.45)))
                count=len(self.match_mode_manager.mode_keys)+1 if self.startup_step=='match' else 3
                menu_x, menu_y, button_w, button_h, gap = self.liquid_menu_layout(width, height, count, scale)
                accents = (
                    [self.startup_menu_accent(index + 2) for index in range(len(self.match_mode_manager.mode_keys))] + [color("#ec5ad9")]
                    if self.startup_step == "match"
                    else [self.startup_menu_accent(index) for index in range(3)]
                )
                for index, accent in enumerate(accents):
                    rect = QRectF(menu_x, menu_y + index * (button_h + gap), button_w, button_h)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(with_alpha(accent, 0.34))
                    painter.drawRoundedRect(
                        QRectF(
                            rect.left() + 22.0 * scale,
                            rect.center().y() - 2.0 * scale,
                            min(94.0 * scale, rect.width() * 0.24),
                            4.0 * scale,
                        ),
                        2.0 * scale,
                        2.0 * scale,
                    )
                    # 注意：这里不再画按钮左缘的 accent 竖条。稳定页/过渡流的
                    # launcher draw list（build_gpu_startup_button）已经绘制同位
                    # 条并带玻璃圆角 SDF 裁剪；此处重复绘制会在 r=18s 弧角外泄
                    # ~7px。另注：本层（control FBO）的 clip 可靠性不具备跨层/
                    # 跨驱动保证——管线内实验（tmp_corner_verify/clip_audit_out）
                    # 显示 overlay 层 clip 生效而本层曾出现异常，故圆角约束一律
                    # 用引擎无关手段（GPU SDF mask / 路径交集 / 预裁剪），不依赖
                    # QPainter setClip*。
            return True

        if not self.gpu_compositor.paint_layer(functions, "control", ratio, draw_control):
            return False
        self._gpu_control_backdrop_key = key
        return True

    def paint_gpu_frame(self) -> bool:
        """Render the scene into private FBOs, then make one native GL present."""
        return self._paint_gpu_frame_cached()

    def paint_gpu_startup_interaction_layer(self, functions, width: int, height: int, ratio: float, *, cached_static: bool = False) -> bool:
        """Submit only the launcher's high-frequency interaction layer."""
        if not cached_static:
            self.hotspots.clear()
        started = time.perf_counter()
        commands = self.build_gpu_startup_hover_layer(width, height) if cached_static else self.build_gpu_startup_transition(width, height)
        if self.hover_tooltip:
            self.build_gpu_tooltip(commands, self.hover_point, self.hover_tooltip)
        self.build_gpu_toasts(commands, width, height)
        # Glyph page publication may allocate textures. Do it before binding
        # the destination FBO; the following render pass is strictly read-only.
        self.clickgui_gpu.prepare_frame(commands, ratio)
        def draw(session) -> bool:
            rendered = self.clickgui_gpu.render(
                functions, commands, width, height, ratio, atlas_prepared=True,
            )
            self.gpu_compositor.last_dynamic_ms = (time.perf_counter() - started) * 1000.0
            return rendered

        return self.gpu_compositor.paint_layer(functions, "dynamic", ratio, draw)

    def module_list_animation_in_flight(self) -> bool:
        """True while any module-list row is fading/sliding in or out."""
        if self.anim.active("hud.modulelist."):
            return True
        rows = getattr(self, "_jello_active_mods_rows", None) or {}
        for row in rows.values():
            progress = float(row.get("progress", 1.0))
            target = float(row.get("target", 1.0))
            if abs(progress - target) > 0.002:
                return True
        return False

    def _should_defer_hud_static_rebuild(self, static_key: tuple[object, ...]) -> bool:
        """Defer a static HUD repaint while module-list rows are animating.

        Only geometry-identical keys defer (a resize must rebuild
        immediately), and a hard 450 ms deadline stops a stuck row from
        freezing the static layer forever.
        """
        previous = self._gpu_static_ui_key
        if previous is None or len(previous) < 4 or len(static_key) < 4:
            return False
        if tuple(static_key[1:4]) != tuple(previous[1:4]):
            return False
        now = time.perf_counter()
        recently_toggled = now - float(getattr(self, "_last_module_toggle_at", 0.0)) < 0.25
        if not recently_toggled and not self.module_list_animation_in_flight():
            self._hud_static_defer_since = 0.0
            return False
        if self._hud_static_defer_since <= 0.0:
            self._hud_static_defer_since = now
        return now - self._hud_static_defer_since < 0.45

    def gpu_static_ui_key(self, width: int, height: int, ratio: float) -> tuple[object, ...]:
        """Return the low-frequency identity of the stable UI layer.

        摄像头数据可以保持源帧率，但 HUD 的几何动画必须跟随显示刷新率。
        过去这里把整个战斗 HUD 固定在 30 Hz，导致 FPS 计数很高时小地图和
        灵动岛仍然只有 30 帧观感。
        """
        if self.home_visible:
            key: tuple[object, ...] = (
                "startup-ui",
                width,
                height,
                round(ratio, 3),
                chinese_enabled(self.registry),
                self.startup_step,
                self.wallpaper_picker_visible,
                self._startup_page_epoch,
            )
            # Compatibility Painter still paints the launcher into the static
            # FBO, so it needs a modest animation cadence. The Direct GPU
            # path above deliberately never includes time in its static key.
            if not self.clickgui_gpu.available or self.wallpaper_picker_visible:
                return (*key, int(time.perf_counter() * 60.0))
            return key
        # P6: enabled 状态以 registry 代际计数入 key（见 __init__ 接线说明），
        # 避免每帧为比较 key 构造 N+1 元组。
        enabled_revision = self.registry.enabled_revision
        return (
            "battle-ui",
            width,
            height,
            round(ratio, 3),
            chinese_enabled(self.registry),
            self.match_mode_manager.current.key,
            self.state.current_stage,
            self.state.stage_countdown_sec,
            self.state.last_event,
            enabled_revision,
            # Rebuild when a modal takes ownership so the complete native HUD
            # is captured once, then softened as a stable backdrop.
            bool(self.economy_visible or self.economy_island.progress > 0.01),
            self._economy_scene_epoch,
            bool(self.gui_visible or self.gui_timeline.progress > 0.01),
            bool(self.student_ui.visible),
            self.escape_menu_visible,
            self.student_ui.panel if self.student_ui.visible else None,
        )

    def paint_gpu_background(self, functions, width: int, height: int, ratio: float) -> bool:
        blur_source_key: tuple[object, ...]
        canvas = QRectF(0, 0, width, height)
        startup_snapshot = None
        if self.home_visible:
            module = self.registry.get("StartupVisual")
            mode = self.setting_mode(module, "Background Mode", "VideoParallax")
            profile = self.setting_mode(module, "Wallpaper Profile", "TexasMP4")
            if (
                mode not in ("ParticleGrid", "PkgScene")
                and profile not in ("ParticleGrid", "Default3000925581Pkg")
                and self.startup_gpu.available
            ):
                # Advance only the inexpensive parallax transform before the
                # cache decision.  Decoded media and blur snapshots retain
                # their own content sequence and are never rebuilt by mouse
                # movement.
                startup_snapshot = self.startup_wallpaper.prepare_gpu_video(
                    canvas,
                    self.hover_point,
                    self.frame_dt,
                    mode=mode,
                    profile=profile,
                    parallax=self.get_setting_number(module, "Parallax Strength", 22.0),
                    zoom=self.get_setting_number(module, "Background Zoom", 1.0),
                    high_res=self.high_res_optimize(width, height),
                    motion_quality=self.setting_mode(module, "Motion Quality", "Smooth60"),
                )
            content_sequence = int(startup_snapshot.sequence) if startup_snapshot is not None else int(getattr(self.startup_wallpaper.video_player, "_sequence", 0))
            background_key: tuple[object, ...] = (
                "startup",
                width,
                height,
                round(ratio, 3),
                mode,
                profile,
                content_sequence,
            )
            transform_key: tuple[object, ...] = (
                "startup-transform",
                round(float(self.startup_wallpaper.parallax_x), 2),
                round(float(self.startup_wallpaper.parallax_y), 2),
            )
            self.gpu_compositor.set_scene_uv_offset(
                -float(self.startup_wallpaper.parallax_x) / max(1.0, float(width)),
                float(self.startup_wallpaper.parallax_y) / max(1.0, float(height)),
            )
            blur_source_key = ("startup-blur", mode, profile, content_sequence, width, height, round(ratio, 3))
        elif self.backend_video_source_selected() and not self.normal_backend_video_fresh():
            background_key = (
                "backend-video-lost",
                chinese_enabled(self.registry),
                self.setting_mode(self.registry.get("CameraFeed"), "Source", ""),
                width,
                height,
                round(ratio, 3),
            )
            transform_key = ("backend-video-lost-transform", width, height)
            self.gpu_compositor.set_scene_uv_offset(0.0, 0.0)
            blur_source_key = background_key
        elif self.normal_video_frame_ready() and self.frame_pixmap is not None and not self.frame_pixmap.isNull():
            background_key = (
                "camera",
                self._display_frame_sequence,
                width,
                height,
                round(ratio, 3),
            )
            transform_key = ("camera-transform", width, height)
            self.gpu_compositor.set_scene_uv_offset(0.0, 0.0)
            blur_source_key = ("camera-blur", self._display_frame_sequence, width, height, round(ratio, 3))
        else:
            background_key = (
                "synthetic",
                width,
                height,
                round(ratio, 3),
            )
            transform_key = ("synthetic-transform", width, height)
            self.gpu_compositor.set_scene_uv_offset(0.0, 0.0)
            blur_source_key = ("synthetic-blur", width, height, round(ratio, 3))
        if (
            background_key == self._gpu_background_key
            and self.gpu_compositor.background_fbo is not None
        ):
            self._gpu_background_transform_key = transform_key
            self._gpu_background_changed = False
            return True

        started = time.perf_counter()
        def draw_background(session) -> bool:
            # Some Windows drivers corrupt a QOpenGLTexture fed from a changing
            # QImage while an FBO is resized at 2K/DPR boundaries.  The raw video
            # shader stays available for explicit diagnostics, but the production
            # path paints the decoded frame into our *private* scene FBO and still
            # presents it through the single GPU compositor.  This removes the
            # striped/repeated-frame failure without falling back to the window's
            # mixed QPainter/default-framebuffer path.
            # The launcher has one production background path: texture -> private
            # background FBO.  The painter branch remains a GPU-init fallback only.
            raw_video_path = True
            raw_drawn = False
            if self.home_visible:
                if raw_video_path and mode not in ("ParticleGrid", "PkgScene") and profile not in ("ParticleGrid", "Default3000925581Pkg") and self.startup_gpu.available:
                    snapshot = startup_snapshot
                    if snapshot is not None:
                        stable_draw_rect = self.startup_wallpaper.last_draw_rect.translated(
                            -float(self.startup_wallpaper.parallax_x),
                            -float(self.startup_wallpaper.parallax_y),
                        )
                        self.startup_gpu.update_frame(
                            snapshot.image,
                            stable_draw_rect,
                            snapshot.sequence,
                            snapshot.frame_interval,
                            self.get_setting_number(module, "Dim Overlay", 0.28),
                            origin=TextureOrigin.TOP_LEFT_IMAGE,
                        )
                        raw_drawn = self.startup_gpu.render(functions, canvas, interpolate=False)
                        if raw_drawn:
                            self.liquid_glass.update(
                                "startup",
                                snapshot.image,
                                stable_draw_rect,
                                soft=snapshot.soft_glass,
                                lens=snapshot.glass,
                            )
            # Live camera frames deliberately do not use StartupGpuCompositor's
            # current/previous texture pair.  A changing QImage can otherwise race
            # texture reuse on some Windows drivers, producing an intermittent
            # flipped or repeated frame.  Painting the latest immutable QImage into
            # this private FBO still leaves the final composition entirely on the
            # GPU, while guaranteeing that each display frame has one orientation.
            if not raw_drawn:
                painter = session.begin_painter()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth_video(not self.high_res_optimize(width,height)))
                if self.home_visible:
                    self.draw_startup_background(painter, width, height)
                elif self.backend_video_source_selected() and not self.normal_backend_video_fresh():
                    self.draw_backend_video_lost(painter, width, height)
                elif self.normal_video_frame_ready() and self.frame_image is not None and not self.frame_image.isNull():
                    painter.drawImage(canvas, self.frame_image, QRectF(self.frame_image.rect()))
                else:
                    self.draw_synthetic_video(painter, width, height)
            return True

        if not self.gpu_compositor.paint_layer(
            functions, "background", ratio, draw_background, transparent=False,
        ):
            return False
        self._gpu_background_key = background_key
        self._gpu_background_transform_key = transform_key
        self._gpu_background_changed = blur_source_key != self._gpu_blur_source_key
        self._gpu_blur_source_key = blur_source_key
        self.gpu_compositor.last_background_ms = (time.perf_counter() - started) * 1000.0
        return True

    def tune_render_rate(self, now: float) -> None:
        if now - self._last_render_tune < 0.35 or self._frame_telemetry.ema_ms <= 0.0:
            return
        self._last_render_tune = now
        levels = [60.0, 72.0, 90.0, 120.0, 144.0]
        allowed = [level for level in levels if level <= self.render_fps_cap + 0.5]
        if not allowed:
            allowed = [60.0]
        current = min(range(len(allowed)), key=lambda index: abs(allowed[index] - self.render_fps_target))
        budget_ms = 1000.0 / max(1.0, allowed[current])
        measured_ms = self._frame_telemetry.ema_ms
        if self.gpu_compositor.available:
            compositor = self.gpu_compositor
            media_reuse = max(1.0, self.render_fps_target / 30.0)
            ui_reuse = max(
                1.0,
                self.render_fps_target / 30.0,
            ) if not self.home_visible and self.gui_timeline.progress > 0.01 else 1.0
            measured_ms = (
                compositor.last_dynamic_ms
                + compositor.last_glass_ms
                + compositor.last_present_ms
                + compositor.last_overlay_ms / ui_reuse
                + (compositor.last_background_ms + compositor.last_blur_ms) / media_reuse
            )
        # Do not get stranded at 60/72 after a one-off atlas or wallpaper upload.
        # Descend only when a frame is genuinely missing its present budget; climb
        # promptly when the current tier has comfortable headroom.
        if measured_ms > budget_ms * 1.05 and current > 0:
            current -= 1
        elif measured_ms < budget_ms * 0.90 and current + 1 < len(allowed):
            # 资源预热完成后直接回到当前帧耗时能够承受的最高档，避免每
            # 350ms 只爬一级而让用户长时间停留在 60/72 Hz。
            for candidate in range(len(allowed) - 1, current, -1):
                if measured_ms <= (1000.0 / allowed[candidate]) * 0.94:
                    current = candidate
                    break
        self.render_fps_target = allowed[current]

    def _paint_compatibility_frame(self) -> bool:
        """Complete the same close generation after a successful CPU frame."""
        generation = self._economy_restore_pending_generation
        restore = bool(
            generation is not None
            and generation == self._economy_generation
            and self.economy_phase == EconomyScenePhase.CLOSING
            and self.economy_island.progress <= 0.001
            and not self.home_visible
        )
        painted = self.paint_overlay(None, force_economy_compat=True)
        if (native := getattr(self, "_endfield_menu", None)) is not None:
            native.backdrop_frame_ready(painted)
        if painted and restore:
            self.invalidate_transient_overlay_scene()
            self._finalize_economy_restore(generation)
        return bool(painted)

    def paint_overlay(
        self,
        _event: QPaintEvent | None,
        *,
        startup_background_ready: bool = False,
        hud_background_ready: bool = False,
        painter: QPainter | None = None,
        logical_size: tuple[int, int] | None = None,
        include_interactive: bool = True,
        split_motion_layer: bool = False,
        capture_module_list: bool = False,
        force_economy_compat: bool = False,
    ) -> bool:
        owns_painter = painter is None
        p = painter or QPainter(self)
        if not p.isActive():
            return False
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.hotspots.clear()
        width, height = logical_size or (self.width(), self.height())
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not self.high_res_optimize(width, height))

        if self.home_visible:
            self.draw_startup_overlay_content(p, width, height, startup_background_ready)
            return finish_painter(p) if owns_painter else True

        if not hud_background_ready:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(QRectF(0, 0, width, height), Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # 原始结果必须继续传给击杀检测，以便目标重新亮灯时解除屏蔽；
        # 画面层使用过滤后的结果，确认战亡后不再绘制 ESP、预测板和自瞄框。
        raw_tongji_result = self._vision_snapshot.result if self._vision_snapshot is not None else None
        tongji_result = self.vision_result_for_display(raw_tongji_result)
        if not hud_background_ready:
            self.draw_video(p, width, height)
        if not self.economy_stage_active() and not self.escape_menu_visible:
            armor_target = self.tongji_target(width, height, tongji_result) if tongji_result is not None else None
            if armor_target is None:
                # 选择真实桥接后绝不拿 mock 框伪装成检测结果；断链状态只保留 HUD 提示。
                armor_target = (
                    VisionTarget(-160.0, -160.0, 1.0, 1.0, 0.0, "BRIDGE OFFLINE")
                    if self._vision_source != "Mock"
                    else self.vision.process(width, height, self.state, "armor")
                )
            energy_target = self.vision.process(width, height, self.state, "energy")
            self.draw_rm_hud(
                p,
                width,
                height,
                armor_target,
                energy_target,
                tongji_result,
                draw_motion=not split_motion_layer,
                # During normal play ModuleList belongs to the high-refresh
                # motion layer. ClickGUI captures it once into the native HUD
                # snapshot; Economy is exclusive and skips this branch.
                draw_module_list=capture_module_list,
            )
        # EliminationFX is always drawn from paint_hud_motion_overlay so the
        # GPU static-layer cache cannot freeze kill-icon timelines.
        if self.hover_tooltip and not self.gui_visible and not self.escape_menu_visible:
            self.draw_tooltip(p, self.hover_point, self.hover_tooltip)

        if not include_interactive:
            return finish_painter(p) if owns_painter else True

        self.paint_clickgui_overlay(
            p,
            width,
            height,
            force_economy_compat=force_economy_compat,
        )
        self.draw_student_frontend(p, width, height)
        self.draw_escape_menu(p, width, height)
        return finish_painter(p) if owns_painter else True

    def menu_overlay_active(self) -> bool:
        """True while ClickGUI or Economy workspace is open (or animating)."""
        return bool(
            self.gui_visible
            or self.gui_timeline.progress > 0.01
            or self.economy_visible
            or self.economy_island.progress > 0.01
            or self.student_ui.visible
            or self.escape_menu_visible
        )

    def economy_stage_active(self) -> bool:
        """True while economy workspace is open/animating (same stage as menu overlay)."""
        if self.economy_phase == EconomyScenePhase.CLOSED:
            return False
        # Once the close spring is fully settled, the pending GL transaction
        # must render the *normal* HUD into freshly cleared static/dynamic
        # layers before present.  Input ownership is still Economy until that
        # present succeeds, but suppressing draw_rm_hud here would validate a
        # blank static cache—the original "camera only" failure.
        if (
            self.economy_phase == EconomyScenePhase.CLOSING
            and self._economy_restore_pending_generation == self._economy_generation
            and self.economy_island.progress <= 0.001
        ):
            return False
        return True

    def combat_hud_preset(self) -> str:
        hud = self.registry.get("HUD")
        return self.setting_mode(hud, "Combat Preset", "Polar")

    def minecraft_hud_active(self) -> bool:
        module = self.registry.get("MinecraftCombatHUD")
        return bool(
            module
            and (module.enabled or self.combat_hud_preset() == "Minecraft 1.21.11")
            and self.minecraft_hud.available
        )

    def paint_hud_motion_overlay(
        self,
        p: QPainter,
        width: int,
        height: int,
        *,
        include_map: bool | None = None,
        include_vision: bool = True,
        include_target_hud: bool = True,
        include_chat: bool = True,
        include_radial: bool = True,
    ) -> None:
        """以 UI 高刷新率绘制与图传帧率无关的战斗 HUD 动效。

        ClickGUI / 经济工作区打开时仍绘制 ModuleList / 小地图 / 底盘等，
        便于边调边看；菜单层叠在 HUD 之上。
        """
        started_at = time.perf_counter()
        # Every battle element consumes the same monotonic timestamp. Sampling
        # perf_counter independently in each draw helper made one present look
        # like several tiny animation frames when Qt rebuilt multiple FBOs.
        now = float(self.gui_motion.now)
        hud = self.registry.get("HUD")
        scale = self.get_hud_scale(width, height)
        logical_width = max(1, int(width / scale))
        logical_height = max(1, int(height / scale))
        minecraft_active = self.minecraft_hud_active()
        self._last_minecraft_hud_layout = None
        expanded_scores = include_map is not False and tab_scoreboard_active(self)
        prepare_tab_layout(self, width, height, now, include_map=include_map is not False)
        held_tab_list = expanded_scores or (self.student_ui.visible and self.student_ui.panel=="damage")
        if minecraft_active:
            # Original MC chrome is one low-resolution nearest-neighbour
            # layer.  Every client/mod overlay below remains at its own HUD
            # scale, matching Sigma's Minecraft composition model.
            self._last_minecraft_hud_layout = self.minecraft_hud.draw(
                p,
                width,
                height,
                self.state,
                self.registry,
                self.registry.get("MinecraftCombatHUD"),
                show_scoreboard=not expanded_scores,
            )
        p.save()
        p.scale(scale, scale)

        if minecraft_active:
            if self.setting_bool(hud, "Watermark", True):
                self.draw_watermark(p)
            # Match time/base/robot state already lives in the compact MC
            # sidebar.  Repainting POLAR's match header duplicates the same
            # information over the CS match bar and Dynamic Island.

        display_result = self.interpolated_vision_result(now)
        armor_module = self.registry.get("ArmorAutoAim")
        armor_3d = self.registry.get("Armor3DESP")
        if armor_module and armor_module.enabled and not expanded_scores:
            if include_vision and display_result is not None and armor_3d and armor_3d.enabled:
                self.draw_armor_3d_esp(
                    p,
                    logical_width,
                    logical_height,
                    display_result,
                    armor_3d,
                )
            elif include_vision and self._vision_source == "Mock":
                mock_armor = self.vision.process(
                    logical_width,
                    logical_height,
                    self.state,
                    "armor",
                )
                # Mock lock overlay only when the plate sits inside FOV circle.
                if self.vision_target_in_autoaim_fov(mock_armor, logical_width, logical_height):
                    self.draw_target_overlay(p, mock_armor, armor_module)
            # Fluorescent-white FOV circle (ClickGUI: Draw Fov + Circle Size).
            # Skip when economy/menu overlay is open so it doesn't bleed through glass.
            if not self.menu_overlay_active():
                self.draw_armor_fov_circle(p, logical_width, logical_height, armor_module)

        energy_module = self.registry.get("EnergyAutoAim")
        if energy_module and energy_module.enabled and self._vision_source == "Mock" and not expanded_scores:
            mock_energy = self.vision.process(
                logical_width,
                logical_height,
                self.state,
                "energy",
            )
            self.draw_target_overlay(p, mock_energy, energy_module)

        if include_target_hud and not held_tab_list:
            self.paint_combat_target_overlay(
                p,
                logical_width,
                logical_height,
                display_result,
                now,
            )
        if self.setting_bool(hud, "Dynamic Island", True) and not held_tab_list:
            self.draw_dynamic_island(p, logical_width)
        # Pure display layer, no widget/focus/input ownership. Kept outside the
        # static video/HUD cache so stale observations expire without new video.
        if not held_tab_list:
            self.top_match_bar.paint(
                p, logical_width, logical_height, now,
                minecraft_layout=self._last_minecraft_hud_layout,
                hud_scale=scale, include_map=include_map is not False,
            )
        else:
            self.top_match_bar.hide()
        tactical_map = self.registry.get("TacticalMap")
        # Always show map while tuning ClickGUI unless caller forces it off.
        draw_map = True if include_map is None else include_map
        if draw_map and tactical_map and tactical_map.enabled:
            self.draw_tactical_map_hud(p, logical_width, logical_height)
            # BuffTimeline 贴在地图下方那条带上，所以必须跟地图共享同一个
            # 可见性判断：地图没画时它也不能凭空浮在视频上。
            self.draw_buff_timeline_hud(p, logical_width, logical_height)
        # ModuleList lives here so OpenZen RGB + row anim tick every frame.
        if expanded_scores:
            pass  # Tab board temporarily owns the right HUD; settings stay intact.
        elif minecraft_active and self.setting_bool(hud, "Module List", True):
            self.draw_minecraft_module_list(p, logical_width, logical_height, scale)
        elif self.setting_bool(hud, "Module List", True):
            mode_setting = hud.get_setting("Right Display") if hud else None
            mode = str(mode_setting.value) if mode_setting else HUD_MODES[0]
            if mode in ("ModuleListHud", "TacticalMapHud"):
                self.draw_module_list_hud(p, logical_width, logical_height)
            elif self.setting_bool(hud, "KeyBinds", True) and mode != "KeyBindsHud":
                self.draw_keybinds_strip(p, logical_width, logical_height)
        elif self.setting_bool(hud, "KeyBinds", True):
            mode_setting = hud.get_setting("Right Display") if hud else None
            mode = str(mode_setting.value) if mode_setting else HUD_MODES[0]
            if mode != "KeyBindsHud":
                self.draw_keybinds_strip(p, logical_width, logical_height)
        if not minecraft_active:
            # The custom COOL/PWR rows replace these two bottom-centre status
            # surfaces while MC mode is active.  This is a render policy only;
            # leaving MC restores the user's existing modules unchanged.
            self.draw_chassis_gimbal_indicator(p, logical_width, logical_height)
            self.draw_telemetry_strip(p, logical_width, logical_height)
        p.restore()
        self.draw_student_status(p, width, height)
        if include_chat:
            self.draw_match_chat(p, width, height, now=now)
        if include_radial:
            self.draw_quick_action_wheel(p, width, height)
        # Full-resolution kill icons/score popups; not under hud_scale transform.
        # While menu is open, skip elim FX so card bursts do not flash over panels.
        if not self.menu_overlay_active():
            self.draw_elimination_fx(p, width, height)
        paint_tab_scoreboard(self, p, width, height, now, include_map=include_map is not False)
        self.dynamic_hud_ms = (time.perf_counter() - started_at) * 1000.0

    def paint_priority_interaction_layer(
        self,
        functions,
        width: int,
        height: int,
        ratio: float,
    ) -> bool:
        """Draw chat input and the radial wheel without repainting the HUD."""
        started_at = time.perf_counter()
        def draw(session) -> bool:
            self.begin_gpu_quick_action_wheel_collection()
            painter = session.begin_painter()
            if not self.escape_menu_visible:
                if self.match_chat.input_open:
                    self.draw_match_chat(painter, width, height, now=started_at)
                if self.quick_action_wheel.visible:
                    self.draw_quick_action_wheel(painter, width, height)
                self.draw_student_frontend(painter, width, height)
            self.draw_escape_menu(painter, width, height)
            if not session.end_painter():
                return False
            self.flush_gpu_quick_action_wheel(functions, width, height, ratio)
            self.gpu_compositor.last_dynamic_ms = (time.perf_counter() - started_at) * 1000.0
            return True

        return self.gpu_compositor.paint_layer(functions, "target", ratio, draw)

    @staticmethod
    def quick_action_sector_path(
        center: QPointF,
        inner_radius: float,
        outer_radius: float,
        start_angle: float,
        span_angle: float,
    ) -> QPainterPath:
        path = QPainterPath()
        steps = max(8, int(abs(span_angle) * 16.0))
        outer_points = [
            QPointF(
                center.x() + math.cos(start_angle + span_angle * index / steps) * outer_radius,
                center.y() + math.sin(start_angle + span_angle * index / steps) * outer_radius,
            )
            for index in range(steps + 1)
        ]
        inner_points = [
            QPointF(
                center.x() + math.cos(start_angle + span_angle * index / steps) * inner_radius,
                center.y() + math.sin(start_angle + span_angle * index / steps) * inner_radius,
            )
            for index in range(steps, -1, -1)
        ]
        path.moveTo(outer_points[0])
        for point in outer_points[1:]:
            path.lineTo(point)
        for point in inner_points:
            path.lineTo(point)
        path.closeSubpath()
        return path

    def draw_quick_action_wheel(self, p: QPainter, width: int, height: int) -> None:
        state = self.quick_action_wheel
        if not state.visible:
            return
        amount = max(0.0, min(1.0, state.progress))
        eased = Easings.ease_out_expo(amount)
        radius = state.radius * (0.84 + eased * 0.16)
        inner_radius = max(24.0, state.dead_zone * (0.90 + eased * 0.10))
        center = QPointF(state.center_x, state.center_y)
        outer_rect = QRectF(
            center.x() - radius,
            center.y() - radius,
            radius * 2.0,
            radius * 2.0,
        )
        sectors = state.sectors()
        # Translate stock captions only. Custom labels and action IDs remain opaque.
        sector_labels = {
            sector.index: (
                ui_text(ACTION_LABELS.get(sector.action, sector.label), True)
                if chinese_enabled(self.registry)
                and sector.label
                and not state.custom_labels[SECTOR_INDEXES[state.sector_count][sector.index]]
                else sector.label
            )
            for sector in sectors
        }

        if self._collect_gpu_quick_action_wheel and self.quick_action_wheel_gpu.available:
            self._gpu_quick_action_wheel_command = QuickActionWheelCommand(
                center.x(),
                center.y(),
                radius,
                inner_radius,
                len(sectors),
                state.selected_index,
                eased,
            )
            labels = ClickGuiDrawList()
            for index, sector in enumerate(sectors):
                selected = index == state.selected_index
                angle = sector.start_angle + sector.span_angle * 0.5
                content_radius = inner_radius + (radius - inner_radius) * 0.54
                content = QPointF(
                    center.x() + math.cos(angle) * content_radius,
                    center.y() + math.sin(angle) * content_radius,
                )
                if state.icon_style != "Off" and sector.icon:
                    labels.text(
                        QRectF(content.x() - 19.0, content.y() - 17.0, 38.0, 16.0),
                        sector.icon,
                        self.font("poppins_semibold", 8.2),
                        color("#ffffff", eased * (1.0 if selected else 0.88)),
                        Qt.AlignmentFlag.AlignCenter,
                    )
                if state.show_labels and sector.label:
                    labels.text(
                        QRectF(content.x() - 44.0, content.y() + 1.0, 88.0, 28.0),
                        sector_labels[sector.index].replace("\n", " "),
                        self.font("poppins_medium", 7.0),
                        color("#ffffff", eased * (0.98 if selected else 0.74)),
                        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                    )
            labels.text(
                QRectF(
                    center.x() - inner_radius,
                    center.y() - inner_radius,
                    inner_radius * 2.0,
                    inner_radius * 2.0,
                ),
                ui_text("CANCEL" if state.selected_index < 0 else "RELEASE", chinese_enabled(self.registry)),
                self.font("poppins_medium", 6.4),
                color("#ffffff", eased * 0.72),
                Qt.AlignmentFlag.AlignCenter,
            )
            self._gpu_quick_action_wheel_labels = labels
            return

        # Compatibility path deliberately avoids the shared glass recorder.
        # Complex painter clips are not transferred to GPU glass commands and
        # previously exposed every sector's rectangular bounds.
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(eased)
        circle = QPainterPath()
        circle.addEllipse(outer_rect)
        p.setClipPath(circle)
        p.setPen(QPen(color("#ffffff", 0.24), 1.0))
        p.setBrush(color("#071019", 0.72))
        p.drawEllipse(outer_rect)

        for index, sector in enumerate(sectors):
            inset = min(0.025, sector.span_angle * 0.035)
            path = self.quick_action_sector_path(
                center,
                inner_radius + 3.0,
                radius - 3.0,
                sector.start_angle + inset,
                sector.span_angle - inset * 2.0,
            )
            selected = index == state.selected_index
            p.fillPath(
                path,
                color("#4da3ff", 0.34 if selected else 0.075),
            )
            p.setPen(QPen(color("#ffffff", 0.40 if selected else 0.14), 1.0))
            p.drawPath(path)

            angle = sector.start_angle + sector.span_angle * 0.5
            content_radius = inner_radius + (radius - inner_radius) * 0.53
            content = QPointF(
                center.x() + math.cos(angle) * content_radius,
                center.y() + math.sin(angle) * content_radius,
            )
            icon_color = color("#ffffff", 1.0 if selected else 0.86)
            if state.icon_style == "Dot":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(icon_color)
                p.drawEllipse(content, 3.0 if selected else 2.4, 3.0 if selected else 2.4)
            elif sector.icon:
                icon_rect = QRectF(content.x() - 19.0, content.y() - 16.0, 38.0, 16.0)
                self.draw_text(
                    p,
                    icon_rect,
                    sector.icon,
                    self.font("poppins_semibold", 8.2),
                    icon_color,
                    Qt.AlignmentFlag.AlignCenter,
                )
            if state.show_labels and sector.label:
                label_rect = QRectF(content.x() - 43.0, content.y() + 1.0, 86.0, 30.0)
                self.draw_text(
                    p,
                    label_rect,
                    sector_labels[sector.index],
                    self.font("poppins_medium", 7.1),
                    color("#ffffff", 0.98 if selected else 0.76),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                )

        dead_rect = QRectF(
            center.x() - inner_radius,
            center.y() - inner_radius,
            inner_radius * 2.0,
            inner_radius * 2.0,
        )
        p.setPen(QPen(color("#ffffff", 0.20), 1.0))
        p.setBrush(color("#071019", 0.22))
        p.drawEllipse(dead_rect)
        self.draw_text(
            p,
            dead_rect,
            ui_text("CANCEL" if state.selected_index < 0 else "RELEASE", chinese_enabled(self.registry)),
            self.font("poppins_medium", 6.5),
            color("#ffffff", 0.70),
            Qt.AlignmentFlag.AlignCenter,
        )
        p.restore()

    def draw_match_chat(
        self,
        p: QPainter,
        width: int,
        height: int,
        *,
        now: float | None = None,
    ) -> None:
        self.server_chat_renderer.draw(self, p, width, height, time.perf_counter() if now is None else float(now))
        return
    def match_chat_reserved_height(self, *, now: float | None = None) -> float:
        """Return the lower-left vertical space occupied by visible chat."""
        module = self.registry.get("MatchChat")
        if module is None or not module.enabled:
            return 0.0
        rows = self.match_chat.visible_messages(
            now=now,
            closed_lines=int(self.get_setting_number(module, "Closed Lines", 6)),
            open_lines=int(self.get_setting_number(module, "Open Lines", 16)),
            fade_seconds=self.get_setting_number(module, "Fade Seconds", 10.0),
        )
        if not rows and not self.match_chat.input_open:
            return 0.0
        input_space = 29.0 if self.match_chat.input_open else 0.0
        return max(len(rows) * 20.0 + input_space + 10.0, self.server_chat_renderer.last_height)

    def paint_clickgui_overlay(
        self,
        p: QPainter,
        width: int,
        height: int,
        *,
        gpu_commands: ClickGuiDrawList | None = None,
        gpu_hotspots: list[Hotspot] | None = None,
        gpu_frame: ClickGuiFrame | None = None,
        gpu_atlas_prepared: bool = False,
        force_economy_compat: bool = False,
    ) -> None:
        """Draw only the high-frequency ClickGUI layer.

        This layer is cleared every present, while the expensive map, HUD text
        and ModuleList remain cached in the stable UI FBO.
        """

        economy_alpha = self.economy_island.progress
        if economy_alpha > 0.01:
            self.hotspots.clear()
            # The normal path stays bit-for-bit on the GPU.  If the outer
            # compositor rejected this frame, however, rendering the compatible
            # economy sheet is safer than exposing only the camera while the
            # next frame retries a full cache rebuild.
            if force_economy_compat or not self.clickgui_gpu.available:
                self.draw_economy_overlay_compat(p, width, height, economy_alpha)

        gui_alpha = self.gui_timeline.progress
        gpu_presented = False
        if self.gui_visible or gui_alpha > 0.01:
            # Drop HUD/map hit targets while ClickGUI owns the pointer.
            self.hotspots.clear()
            if self.active_clickgui_mode() != PRIMARY_GUI_MODE:
                p.fillRect(QRectF(0, 0, width, height), with_alpha(THEME.dim, gui_alpha))
            click_gui = self.registry.get("ClickGui")
            direct_gpu = self.setting_mode(click_gui, "Render Backend", "Unified GPU") == "Unified GPU" and self.clickgui_gpu.available
            frame_to_publish = None
            if direct_gpu:
                if gpu_frame is not None:
                    commands = gpu_frame.commands
                    frame_to_publish = gpu_frame
                elif gpu_commands is None:
                    frame = build_clickgui_frame(self, width, height, gui_alpha)
                    commands = frame.commands
                    frame_to_publish = frame
                else:
                    commands = gpu_commands
                    # Outer path already built layout + atlas once; restore the
                    # snapshot so clear() above does not leave zero hotspots.
                    if gpu_hotspots is not None:
                        self.hotspots.extend(gpu_hotspots)
                    else:
                        self.build_gpu_clickgui(width, height, gui_alpha)
                context = self.context()
                functions = context.extraFunctions() if context is not None else None
                presenter = getattr(self, "_clickgui_presenter", None)
                if presenter is None or presenter.renderer is not self.clickgui_gpu:
                    presenter = ClickGuiGpuPresenter(
                        self.clickgui_gpu, self.gpu_compositor._restore_qt_texture_state,
                    )
                    self._clickgui_presenter = presenter
                rendered = presenter.present(
                    p, functions, commands,
                    ClickGuiViewport(width, height, self.devicePixelRatioF()),
                    atlas_prepared=gpu_atlas_prepared,
                )
                direct_gpu = rendered
                gpu_presented = rendered
                if rendered and frame_to_publish is not None:
                    frame_to_publish.publish_interactions(self)
            if not direct_gpu:
                # Failed GPU geometry must not coexist with fallback targets.
                self.hotspots.clear()
                self.gui_scroll_viewports = []
                gui_hotspot_start = len(self.hotspots)
                p.save()
                mode = self.active_clickgui_mode()
                if mode == PRIMARY_GUI_MODE:
                    p.setOpacity(1.0)
                    self.gui_scroll_viewports = []
                    self.jello_clickgui.paint_compat(
                        self, self.jello_state, p, width, height, gui_alpha
                    )
                else:
                    p.setOpacity(gui_alpha)
                if mode == "PolarOld":
                    self.draw_old_gui(p, width, height, gui_alpha)
                elif mode == "PolarPanel":
                    self.draw_panel_gui(p, width, height, gui_alpha)
                elif mode == "PolarNew":
                    self.draw_new_gui(p, width, height, gui_alpha)
                elif mode == "PolarDropdown":
                    self.draw_dropdown_gui(p, width, height, gui_alpha)
                elif mode == "PolarUnified":
                    # Compatibility fallback keeps the same one-window
                    # interaction model when OpenGL is unavailable.
                    self.draw_panel_gui(p, width, height, gui_alpha)
                elif mode == "PolarMulti":
                    self.draw_old_gui(p, width, height, gui_alpha)
                if mode in LEGACY_GUI_MODES:
                    self.draw_legacy_return(p, width, height, gui_alpha)
                    self.draw_toasts(p, width, height)
                    if self.hover_tooltip:
                        self.draw_tooltip(p, self.hover_point, self.hover_tooltip)
                p.restore()
                if not self.gui_visible:
                    del self.hotspots[gui_hotspot_start:]
                    self.gui_scroll_viewports = []

        if not self.gui_visible and gui_alpha <= 0.01 and economy_alpha <= 0.01:
            self.draw_video_source_recovery(p, width, height)

        if (
            self.binding_module is not None
            and self.active_clickgui_mode() in LEGACY_GUI_MODES
            and not (gui_alpha > 0.01 and gpu_presented)
        ):
            self.draw_keybind_overlay(p, width, height)

    # ------------------------------------------------------------------
    # 阶段 3d 大域 mixin 化：draw_economy_overlay_legacy（D6 legacy 绘制）与
    # economy_scale_steps / economy_ui_scale / economy_uses_mock_transport /
    # send_economy_request / economy_offers / economy_categories /
    # economy_selected_offer（D6 数据/传输族）已机械迁至
    # rm_client/ui/polar/mixins/economy_mixin.py 的 EconomyMixin，由本类继承提供，调用面不变。
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 阶段 3c 大域 mixin 化：open_legacy_clickgui / return_to_jello_clickgui（D5 打开/关闭组）已机械迁至 rm_client/ui/polar/mixins/clickgui_builders_mixin.py 的 ClickGuiBuildersMixin（方法体零修改），由本类继承提供，调用面不变；紧随其后的 invalidate_transient_overlay_scene（D2 FBO 失效协议辅助）按保守边界决策留在本文件（多域共用），_finalize_economy_restore（D6 冻结面方法）已随阶段 3d 迁至 mixins/economy_mixin.py 的 EconomyMixin。
    # ------------------------------------------------------------------

    def invalidate_transient_overlay_scene(self) -> None:
        """Force one complete HUD/compositor rebuild after a modal transition."""

        self._gpu_background_key = None
        self._gpu_background_transform_key = None
        self._gpu_control_backdrop_key = None
        self._gpu_blur_source_key = None
        self._gpu_static_ui_key = None
        self._gpu_motion_layer_ready = False
        self._gpu_motion_layer_updated_at = 0.0
        self._gpu_background_changed = True
        # The compositor just dropped its static glass commands; the startup
        # page must be rebuilt too, or the launcher keeps its "cached" key and
        # permanently loses every glass panel until a page transition.
        self._startup_static_page_key = None
        self.gpu_compositor.invalidate_transient_layers()

    # ------------------------------------------------------------------
    # 阶段 3d 大域 mixin 化：_finalize_economy_restore（D6 冻结面方法，
    # D2↔D6 FBO restore generation 关闭协议的提交端）已随协议整体迁至
    # rm_client/ui/polar/mixins/economy_mixin.py 的 EconomyMixin；协议另一端的
    # invalidate_transient_overlay_scene（D2 失效协议辅助）被 D1/D2/D5/D7/D16
    # 多域共用 → 按保守边界决策留在本文件（参考阶段 2b 对同一方法的处理），
    # 两端经 self 相互调用的调用面不变。
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 阶段 3c 大域 mixin 化：set_clickgui_visible（D5 打开/关闭组）已机械迁至 rm_client/ui/polar/mixins/clickgui_builders_mixin.py 的 ClickGuiBuildersMixin，由本类继承提供；overlay_coordinator 模态请求/关闭与 economy pending overlay 协议语义零修改随行搬运，调用面不变。
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 阶段 3d 大域 mixin 化：open_economy_workspace / close_economy_workspace
    # （D6 开合状态机）已机械迁至 EconomyMixin；economy pending overlay 与
    # overlay_coordinator 模态请求/关闭协议语义零修改随行搬运，调用面不变。
    # ------------------------------------------------------------------

    def open_match_chat(self) -> None:
        self.close_student_panel(immediate=True)
        module = self.registry.get("MatchChat")
        if module is None or not module.enabled or self.home_visible:
            return
        if self.economy_phase != EconomyScenePhase.CLOSED:
            self._economy_pending_overlay = OverlayKind.CHAT
            if self.economy_phase != EconomyScenePhase.CLOSING:
                self.close_economy_workspace(play_sound=False)
            return
        if self.economy_visible:
            self.close_economy_workspace(play_sound=False)
        if self.gui_visible:
            self.set_clickgui_visible(False, announce=False, play_sound=False)
        self.cancel_quick_action_wheel()
        self.overlay_coordinator.begin_chat()
        self.match_chat.open_input()
        # Rebuild the cached base motion layer once without the closed-chat
        # rows, then let the input surface animate independently above it.
        self._gpu_motion_layer_ready = False
        self.frame_scheduler.force()
        self.update()

    def close_match_chat(self, *, clear: bool = False) -> None:
        if not self.match_chat.input_open and not self.overlay_coordinator.chat_input:
            return
        self.match_chat.close_input(clear=clear)
        self.overlay_coordinator.end_chat()
        # Restore the normal lower-left stack (including fading chat rows) on
        # the first frame after the input layer closes.
        self._gpu_motion_layer_ready = False
        self.frame_scheduler.force()
        self.update()

    def begin_quick_action_wheel(self, point: QPointF) -> bool:
        module = self.registry.get("QuickActionWheel")
        if (
            module is None
            or not module.enabled
            or self.home_visible
            or not self.overlay_coordinator.begin_radial()
        ):
            return False
        self.sync_quick_action_wheel_settings()
        self.quick_action_wheel.open(
            point.x(),
            point.y(),
            self.width(),
            self.height(),
        )
        self.quick_action_wheel.update_pointer(point.x(), point.y())
        self.frame_scheduler.force()
        self.update()
        return True

    def cancel_quick_action_wheel(self) -> None:
        if not self.quick_action_wheel.visible and not self.overlay_coordinator.radial_active:
            return
        self.quick_action_wheel.cancel()
        self.overlay_coordinator.end_radial()
        self.frame_scheduler.force()
        self.update()

    def finish_quick_action_wheel(self) -> None:
        action = self.quick_action_wheel.release()
        self.overlay_coordinator.end_radial()
        if action:
            self.execute_quick_action(action)
        self.frame_scheduler.force()
        self.update()

    def execute_quick_action(self, action: str) -> None:
        if action == "economy":
            self.open_economy_workspace()
            return
        if action == "economy_ammo":
            self.open_economy_workspace("ammo")
            return
        if action == "match_chat":
            self.open_match_chat()
            return
        if action == "clickgui":
            self.set_clickgui_visible(True)
            return
        module_name = {
            "toggle_armor_autoaim": "ArmorAutoAim",
            "toggle_armor_lock": "ArmorLock",
            "toggle_spin": "SpinMode",
            "toggle_friction": "FrictionWheel",
        }.get(action)
        module = self.registry.get(module_name) if module_name else None
        if module is None:
            self.toast("Quick action is unavailable")
            return
        self.registry.toggle_module(module, source="keybind")
        self.selected_module = module
        self.toast(f"{module.name} {'enabled' if module.enabled else 'disabled'}")
        self.auto_save_config()

    # ------------------------------------------------------------------
    # 阶段 3d 大域 mixin 化：select_/submit_/activate_economy_offer、
    # begin_/finish_economy_hold（D6 交互/双击确认/长按协议）与玻璃助手
    # _economy_glass_row / _economy_dim_around / _economy_island_well
    # 已机械迁至 EconomyMixin，由本类继承提供，调用面不变。
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 阶段 3d 大域 mixin 化：draw_economy_overlay_compat（D6 compat 绘制总入口，
    # 阶段 3c 报告明确其属 D6、留待本次——已确认迁入）已机械迁至
    # EconomyMixin，由本类继承提供，GPU 失败回退路径 paint_overlay(...,
    # force_economy_compat=True) 经同名方法继续命中，调用面不变。
    # ------------------------------------------------------------------

    def draw_startup_gpu_background(self) -> bool:
        if not self.startup_gpu_enabled or not self.home_visible or not self.startup_gpu.available:
            return False

        try:
            module = self.registry.get("StartupVisual")
            mode = self.setting_mode(module, "Background Mode", "VideoParallax")
            profile = self.setting_mode(module, "Wallpaper Profile", "TexasMP4")
            snapshot = self.startup_wallpaper.prepare_gpu_video(
                QRectF(0, 0, self.width(), self.height()),
                self.hover_point,
                self.frame_dt,
                mode=mode,
                profile=profile,
                parallax=self.get_setting_number(module, "Parallax Strength", 22.0),
                zoom=self.get_setting_number(module, "Background Zoom", 1.0),
                high_res=self.high_res_optimize(),
                motion_quality=self.setting_mode(module, "Motion Quality", "Smooth60"),
            )
            if snapshot is None:
                return False
            self.startup_gpu.update_frame(
                snapshot.image,
                self.startup_wallpaper.last_draw_rect,
                snapshot.sequence,
                snapshot.frame_interval,
                self.get_setting_number(module, "Dim Overlay", 0.28),
                origin=TextureOrigin.TOP_LEFT_IMAGE,
            )
            self.liquid_glass.update(
                "startup",
                snapshot.image,
                self.startup_wallpaper.last_draw_rect,
                soft=self.startup_wallpaper.soft_glass_frame,
                lens=self.startup_wallpaper.glass_frame,
            )
            context = self.context()
            return bool(context and self.startup_gpu.render(context.functions(), QRectF(0, 0, self.width(), self.height())))
        except Exception as exc:
            # 若驱动或纹理路径不兼容，保持启动页可用并让 QPainter 接管。
            self.startup_gpu.last_error = str(exc)
            self.startup_gpu.available = False
            return False

    def draw_hud_gpu_background(self) -> bool:
        if (
            self.home_visible
            or not self.hud_video_gpu.available
            or self.frame_image is None
            or self.frame_image.isNull()
            or not self.normal_video_frame_ready()
        ):
            return False
        try:
            canvas = QRectF(0, 0, self.width(), self.height())
            self.hud_video_gpu.update_frame(
                self.frame_image,
                canvas,
                self._display_frame_sequence,
                1.0 / 60.0,
                0.0,
            )
            self.hud_video_gpu.discard_interpolation_history()
            context = self.context()
            return bool(context and self.hud_video_gpu.render(context.functions(), canvas, interpolate=False))
        except Exception as exc:
            self.hud_video_gpu.last_error = str(exc)
            self.hud_video_gpu.available = False
            return False

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._window_lifecycle.ready_to_close:
            try:
                self._finish_window_shutdown()
            finally:
                super().closeEvent(event)
            return
        if not self._window_lifecycle.closing:
            if not self._admit_window_close():
                event.ignore()
                return
            self._begin_window_shutdown()
        else:
            self._window_lifecycle.retry_close()
        # A blocked native owner is still owned. A timer polls completion and
        # schedules a second close only after the backend confirms retirement.
        event.ignore()

    def _admit_window_close(self) -> bool:
        """Preserve pending edits before sealing resource ownership."""
        stop = None
        if self.configuration_close_requires_save:
            # A failed disk write cancels close, not the operator's intent to
            # stop driving. Revoke authority before fallible input cleanup and
            # keep it locked until the operator explicitly authorizes again.
            stop = request_control_lock(
                self.backend, self.control, self.backend.mqtt.publish_estop_zero,
            )
            if stop.failures:
                self.toast(stop.message, kind="warning")
        try:
            accepted = self.prepare_configuration_for_close()
        except Exception as exc:
            try:
                _LOG.error("Close configuration admission failed", exc_info=True)
            except Exception:
                pass  # A diagnostic sink cannot cancel admission refusal.
            self.toast(f"关闭保存检查失败：{type(exc).__name__}: {exc}", kind="warning")
            accepted = False
        if not accepted:
            message = "关闭已取消，未保存草稿仍保留。"
            if stop is not None:
                message += "恢复控制需要显式重新授权。"
                message += "比赛控制保持锁定。" if not stop.failures else stop.message
            self.toast(message, kind="warning")
        return accepted

    def _abort_startup(self) -> None:
        """Seal a failed launch without admitting more runtime or asking to save.

        The application owns this wrapper until the normal asynchronous close
        completes. No operator edits have been admitted by a successful launch.
        """
        try:
            if "timer" not in self.__dict__:
                # Failure-only event source: successful construction keeps its
                # original timer publication order, after profile/shortcuts.
                self.timer = create_frame_timer(self, self.on_tick)
        finally:
            # Even unavailable timer service must revoke runtime and request
            # shutdown. Retention preserves the owner if polling cannot run.
            self._begin_window_shutdown()

    def _begin_window_shutdown(self) -> None:
        tasks = (
            TeardownTask("camera", self.camera_worker.close),
            TeardownTask("local-vision", self.local_vision.close),
            TeardownTask("tongji-receiver", self.tongji_receiver.close),
            TeardownTask("tongji-bridge", self.tongji_bridge.close),
            TeardownTask("wallpaper-video", self.startup_wallpaper.video_player.close),
            TeardownTask("wallpaper-thumbnails", self.startup_wallpaper.close_thumbnail_workers),
            TeardownTask("kill-audio-decode", self.kill_fx.sounds.wait_closed),
        )
        prepare = (
            OwnerAction("GPU warmup timer", self._gpu_prewarm_timer.stop),
            OwnerAction("window input", lambda: self.setEnabled(False)),
            OwnerAction("local input", lambda: self._clear_control_inputs_for_lock("window closing")),
            OwnerAction("pending configuration", self.cancel_pending_configuration_for_close),
            OwnerAction("kill audio playback", self.kill_fx.sounds.close),
        )
        settings = getattr(self, "_settings_session", None)
        if settings is not None:
            prepare = (OwnerAction("settings dialog", settings.close_for_shutdown), *prepare)
        team_editor = getattr(self, "_match_team_editor", None)
        if team_editor is not None:
            prepare = (OwnerAction("team identity editor", team_editor.reject), *prepare)
        feedback = getattr(self, "_endfield_feedback", None)
        if feedback is not None:
            prepare = (*prepare, OwnerAction("Endfield voice playback", feedback.voice.stop))
        native = getattr(self, "_endfield_menu", None)
        if native is not None:
            prepare = (*prepare, OwnerAction("Endfield source visual", native.close))
            tasks = (*tasks, TeardownTask("Endfield source renderer", native.wait_closed))
        self._window_lifecycle.request_close(prepare=prepare, tasks=tasks)
        # The main timer remains alive even if a test/tool had paused it. No
        # normal runtime stages execute through the closing branch above.
        self.timer.start(16)
        self.update()

    def _paint_shutdown_status(self) -> None:
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#0b1018"))
            painter.setPen(QColor("#e1e8f0"))
            painter.drawText(
                self.rect().adjusted(40, 40, -40, -40),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._window_lifecycle.status_text,
            )
        finally:
            painter.end()

    def _finish_window_shutdown(self) -> None:
        if self._window_close_finalized:
            return
        failures = self._window_lifecycle.finalize(
            prepare=lambda: (
                OwnerAction("timer", self.timer.stop),
                OwnerAction("GPU warmup timer", self._gpu_prewarm_timer.stop),
                OwnerAction("configuration", lambda: self.flush_auto_save(force=True)),
            ),
            context=FinalContextPorts(
                available=lambda: self.context() is not None,
                enter=lambda: self.makeCurrent(),
                releases=lambda: tuple(
                    OwnerAction(name, getattr(self, name).release)
                    for name in ("clickgui_gpu", "vision_wire_gpu", "island_icon_gpu",
                                 "quick_action_wheel_gpu", "gpu_compositor", "startup_gpu", "hud_video_gpu")
                ),
                leave=lambda: OwnerAction("GL done", self.doneCurrent),
            ),
        )
        if failures:
            message = "Exit cleanup warnings: " + "; ".join(f.summary for f in failures)
            self.state.push_event(message)
            self._show_shutdown_cleanup_warning(message)

    def focusOutEvent(self, event) -> None:
        """A lost foreground window is an unconditional control epoch break."""

        def release_inputs() -> None:
            self.cancel_quick_action_wheel()
            self.control.mock_output = bool(self.backend.mqtt.mock_output)
            self.control.release_all(self.state, self.registry)

        try:
            result = request_focus_loss(
                self.backend, self.control, release_inputs,
                lambda: self.backend.mqtt.publish_estop_zero(),
            )
            self.student_ui.dragging = ""
            if result.failures:
                self.toast(result.message, kind="warning")
        finally:
            super().focusOutEvent(event)
    # ------------------------------------------------------------------
    # 阶段 3e 大域 mixin 化：_clear_control_inputs_for_lock（控制锁定时本地输入清零钩子，D15）已机械迁至 rm_client/ui/polar/mixins/input_dispatch_mixin.py 的 InputDispatchMixin，由本类继承提供，调用面不变。
    # ------------------------------------------------------------------

    # 阶段 2b 偏差决策（REFACTOR_BLUEPRINT §2 D8 / §5 表 2b 行）：
    # has_valid_combat_target 属 D8，但被阶段 2a 判定跨域使用
    # （CombatTargetHudMixin.paint_combat_target_overlay 经 self 调用），
    # 保持原地不动；其余 D8 FOV/锁定判定方法已机械迁至
    # ui/polar/renderers/esp_wireframe.py 的 EspWireframeMixin。
    def has_valid_combat_target(self, result: VisionResult | None) -> bool:
        """Only a real, FOV-admitted armor plate may activate the center TargetHUD."""
        if result is None or self._vision_source == "Mock":
            return False
        if result.tracking_state.lower() not in {"detected", "tracking", "locked"}:
            return False
        # Prefer aim_armor (already FOV-filtered on the display path).
        candidates = []
        if result.aim_armor is not None:
            candidates.append(result.aim_armor)
        for armor in candidates:
            if armor.predicted or armor.held or len(armor.points) != 4:
                continue
            if self.vision_kill_detector.is_extinguished(armor):
                continue
            if self.vision_kill_detector.is_suppressed(armor):
                continue
            if not self.armor_center_in_autoaim_fov(armor, result):
                continue
            return True
        return False

    def draw_hud_liquid_glass(
        self,
        p: QPainter,
        rect: QRectF,
        radius: float,
        *,
        surface: str = "MainGlass",
        hover: float = 0.0,
        clip_rect: QRectF | None = None,
    ) -> None:
        if self._gpu_frame_recording and self.gpu_compositor.available:
            mapped = p.transform().mapRect(rect)
            mapped_clip = p.transform().mapRect(clip_rect) if clip_rect is not None else None
            self.gpu_compositor.add_glass(
                mapped,
                radius * max(abs(p.transform().m11()), abs(p.transform().m22()), 0.01),
                strength=1.08 if surface in ("ProminentGlass", "LensIsland") else 0.82 if surface == "SubGlass" else 1.0,
                surface="sub" if surface == "SubGlass" else "clear" if surface in ("ProminentGlass", "PolarGlass", "LensIsland") else "main",
                hover=hover,
                opacity=p.opacity(),
                material=(
                    LensMaterial.ISLAND
                    if surface == "LensIsland" or (rect.height() <= 58.0 and radius >= rect.height() * 0.44)
                    else LensMaterial.STRONG
                    if surface in ("ProminentGlass", "PolarGlass")
                    else LensMaterial.REGULAR
                ),
                clip_rect=mapped_clip,
            )
            return
        mode = "polar" if surface == "PolarGlass" else "prominent" if surface in ("ProminentGlass", "LensIsland") else "standard"
        self.liquid_glass.draw(
            p, rect, radius, source="hud", mode=mode,
            surface="sub" if surface == "SubGlass" else "main",
            hover=hover, press=self.liquid_press_amount(rect), pointer=self.hover_point,
            lite=self.high_res_optimize(),
        )

    def liquid_press_amount(self, rect: QRectF) -> float:
        if not rect.contains(self.liquid_press_point):
            return 0.0
        remaining = self.liquid_press_until - time.perf_counter()
        return max(0.0, min(1.0, remaining / 0.12))

    def draw_rm_hud(
        self,
        p: QPainter,
        width: int,
        height: int,
        armor_target: VisionTarget,
        energy_target: VisionTarget,
        tongji_result: VisionResult | None = None,
        *,
        draw_motion: bool = True,
        draw_module_list: bool = False,
    ) -> None:
        hud = self.registry.get("HUD")
        armor_module = self.registry.get("ArmorAutoAim")
        energy_module = self.registry.get("EnergyAutoAim")
        armor_enabled = bool(armor_module and armor_module.enabled)
        energy_enabled = bool(energy_module and energy_module.enabled)
        self._bottom_left_diagnostics = []
        # 真实装甲视觉接入后不允许旧 Mock 能量框叠到图传上。
        draw_energy = energy_enabled and self._vision_source == "Mock"
        primary_target = energy_target if draw_energy else armor_target
        if self.minecraft_hud_active():
            # The Minecraft preset is a complete combat chrome.  Keep POLAR's
            # liquid-glass renderers intact by bypassing them rather than
            # changing their shared materials or geometry.
            if draw_motion:
                self.paint_hud_motion_overlay(p, width, height)
            return
        hud_scale = self.get_hud_scale(width, height)
        scaled_width = max(1, int(width / hud_scale))
        scaled_height = max(1, int(height / hud_scale))
        p.save()
        p.scale(hud_scale, hud_scale)
        # ClickGUI keeps battle chrome beneath Sigma's blur. Economy is
        # filtered before this method and remains an exclusive scene.
        if self.setting_bool(hud, "Watermark", True):
            self.draw_watermark(p)
        match_timer = self.registry.get("MatchTimer")
        if match_timer is not None and match_timer.enabled and not self.top_match_bar.enabled():
            self.draw_match_header(p, scaled_width)
        chat_reserve = self.match_chat_reserved_height(now=time.perf_counter()) / hud_scale
        bottom_left = HudAnchorStack(
            22.0,
            scaled_height - 60.0 - chat_reserve,
            8.0,
        )
        if self.setting_bool(hud, "Target HUD", True):
            title = "EnergyTargetHUD" if primary_target.kind == "energy" else "RobotTargetHUD"
            self.draw_target_hud_by_style(p, primary_target, title, bottom_left)
        diagnostics = list(self._bottom_left_diagnostics)
        if self.hud_density() != "DebugFull" and diagnostics:
            diagnostics = [max(diagnostics, key=lambda item: item[2])]
        else:
            diagnostics.sort(key=lambda item: item[2])
        for text_value, text_color, _priority in diagnostics:
            diagnostic_rect = bottom_left.take(220.0, 22.0)
            self.draw_pill(p, diagnostic_rect.topLeft(), text_value, text_color)
        if hud:
            mode_setting = hud.get_setting("Right Display")
            mode = str(mode_setting.value) if mode_setting else HUD_MODES[0]
            self.draw_right_hud_mode(
                p,
                scaled_width,
                scaled_height,
                mode,
                primary_target,
                hud_scale,
                draw_module_list=draw_module_list,
            )
        p.restore()
        if draw_motion:
            self.paint_hud_motion_overlay(p, width, height)

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

    def _hud_style_palette(self) -> tuple[float, bool, QColor, QColor, QColor, QColor]:
        """P8: hud_* 色彩家族的帧级 memo。

        token = ``gui_motion.now``（每 UI tick 推进的单调时钟）：同一 tick 内
        全部助手共享一次 registry 读取；UI 与配置加载路径会在写入 Visual
        Tone 时显式清空缓存。返回的 QColor 为缓存实例，对外一律经 QColor()
        拷贝（调用方可能就地 setAlpha）。
        """
        token = float(self.gui_motion.now)
        cache = self._hud_palette_cache
        if cache is not None and cache[0] == token:
            return cache
        hud = self.registry.get("HUD")
        tone_setting = hud.get_setting("Visual Tone") if hud else None
        tone = str(tone_setting.value) if tone_setting else "LiquidClean"
        if tone not in ("LiquidClean", "PolarLegacy"):
            tone = "LiquidClean"
        clean = tone == "LiquidClean"
        palette: tuple[float, bool, QColor, QColor, QColor, QColor] = (
            token,
            clean,
            color("#5d8cff") if clean else THEME.cyan,
            color("#efbf04") if clean else color("#ffd166"),
            color("#fc4130") if clean else THEME.red,
            color("#4dac68") if clean else THEME.green,
        )
        self._hud_palette_cache = palette
        return palette

    def hud_clean(self) -> bool:
        return self._hud_style_palette()[1]

    def hud_accent(self) -> QColor:
        return QColor(self._hud_style_palette()[2])

    def hud_surface(self, alpha: float = 0.68) -> QColor:
        clean = self._hud_style_palette()[1]
        return color("#000000", alpha if clean else min(0.86, alpha + 0.08))

    def hud_panel_outline(self, alpha: float = 0.10) -> QColor:
        clean = self._hud_style_palette()[1]
        return color("#ffffff", alpha if clean else min(0.20, alpha + 0.04))

    def hud_warning(self) -> QColor:
        return QColor(self._hud_style_palette()[3])

    def hud_danger(self) -> QColor:
        return QColor(self._hud_style_palette()[4])

    def hud_success(self) -> QColor:
        return QColor(self._hud_style_palette()[5])

    def hud_state_color(self, kind: str = "info", ratio: float = 0.0) -> QColor:
        _token, clean, accent, warning, danger, success = self._hud_style_palette()
        if not clean:
            return QColor(_HUD_LEGACY_STATE_COLORS.get(kind, _HUD_STATE_COLOR_FALLBACK))
        if kind in ("enemy", "danger"):
            return QColor(danger)
        if kind in ("warning", "gold", "power"):
            if ratio >= 0.95:
                return QColor(danger)
            return QColor(warning) if ratio >= 0.78 or kind in ("warning", "gold", "power") else QColor(accent)
        if kind in ("success", "ammo", "cap"):
            if kind == "cap" and ratio < 0.18:
                return QColor(danger)
            if kind == "cap" and ratio < 0.34:
                return QColor(warning)
            return QColor(success) if kind in ("success", "ammo") else QColor(accent)
        if kind in ("heat", "speed"):
            if ratio >= 0.92:
                return QColor(danger)
            if ratio >= 0.78:
                return QColor(warning)
            return QColor(accent)
        return QColor(accent)

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
        chinese = chinese_enabled(self.registry)
        z_font = self.font("museo", 18, QFont.Weight.Bold)
        body = self.font("poppins_medium", 10.5)
        glow_radius = 8 if not self.hud_clean() else 4
        # Six glow passes plus three text layouts cost several milliseconds on
        # Qt's OpenGL paint engine.  The status changes far more slowly than the
        # display, so rasterise the exact existing block at DPR density and let
        # Qt retain it as one immutable GPU texture between 4 Hz status samples.
        transform_scale = max(
            0.25,
            math.hypot(p.transform().m11(), p.transform().m12()),
        )
        raster_scale = max(
            1.0,
            min(4.0, transform_scale * max(1.0, float(self.devicePixelRatioF()))),
        )
        accent = self.hud_accent()
        status = status_text("CAM" if self.state.camera_connected else "SYN", chinese)
        scene = self.scenario_manager.current.title
        match = status_text(self.match_mode_manager.current.title, chinese)
        mqtt = status_text("MQTT mock" if getattr(self.mqtt, "mock_output", True) else "MQTT live", chinese)
        serial = status_text("SERIAL mock" if self.control.mock_output else "SERIAL live", chinese)
        # 无线键鼠出口必须在脸上看得见：它决定"插不插 USB 线车动不动"，
        # 出问题时最忌讳的就是界面上找不到任何痕迹。
        # 只放状态、不放计数（计数会每帧变，会打掉这块 4Hz 光栅缓存）。
        # 用"车上回读出来的实况"而不是我们自己猜的开关状态：
        # silent / stalled 这两种情况必须一眼看见，否则现场会一直以为是
        # "客户端没发"，而真正的问题是包没到 C 板。
        km_state = str(getattr(self.control, "radio_km_link_state", "off"))
        km_rx = int(getattr(self.control, "radio_km_robot_rx", -1))
        km = {
            "off": "KM off",
            "blocked": "KM blocked",
            "no-readback": "KM on(?)",
            "live": "KM live\u2191{count}",
            "silent": "KM NO-RX!!",
            "stalled": "KM stall@{count}",
        }.get(km_state, "KM ?")
        km = status_text(km, chinese, count=km_rx)
        # 自定义控制器出口也要同屏可见：它是"选手端在跑才有意义"的一条路，
        # 关掉选手端后界面上必须立刻看出来，否则会被当成链路故障。
        cc = status_text("CC on" if getattr(self.control, "cc_enabled", False) else "CC off", chinese)
        # 直连 DT7：开着但一帧都没发出去，是现场最难查的一种"看起来没问题"。
        # 所以状态与原因必须同屏可见（命令号没定案时是故意不发，不是坏了）。
        dt7_link_obj = getattr(self.control, "dt7_link", None)
        if getattr(self.control, "dt7_enabled", False):
            # 把"真在往哪个口写"显示出来：写错口是"能发但永远不通"的头号原因，
            # 光看开关状态看不出来我们其实把遥控器帧发给了车的 CDC 口。
            # 整段文字由 dt7_link.status_token 统一生成，理由是它得能被离线断言
            # 钉住（"勾了真发 = 显示 real"这个假象骗过一次现场判断）。
            dt7 = _dt7_status_token(
                mock_output=bool(getattr(dt7_link_obj, "mock_output", True)),
                resolved_port=str(getattr(dt7_link_obj, "resolved_port", "") or ""),
                wire_neutral=bool(getattr(dt7_link_obj, "wire_neutral", True)),
                release_pending=bool(getattr(self.control, "dt7_release_pending", False)),
                last_error=str(getattr(self.control, "dt7_last_error", "") or ""))
            dt7 = dt7_display_token(dt7, chinese, resolved_port=str(getattr(dt7_link_obj, "resolved_port", "") or ""))
            gm = str(getattr(self.control, "dt7_mouse_note", "") or "")
            if gm:
                # 不是故障，是固件仲裁：鼠标这条路被上位机云台请求顶掉了
                dt7 += "(鼠标被上位机云台顶掉)"
        else:
            dt7 = ""
        spin_note = str(getattr(self.state, "polar_spin_note", "") or "")
        if spin_note:
            # 自转没发出去时不能只让 KM/DT7 灯亮着，得说出为什么 + 怎么办
            dt7 = (dt7 + " " if dt7 else "") + "(自转没发·详见日志)"
        conflict = str(getattr(self.control, "km_source_conflict", "") or "")
        if conflict:
            dt7 = "KM冲突(!)" + conflict[:40]
        status_line = (f"{status} | {scene} | {match} | {mqtt} | {serial} | {km} | {cc}"
                       + ((" | " + dt7) if dt7 else ""))
        static_key = (
            chinese,
            round(raster_scale, 3),
            z_font.toString(),
            body.toString(),
            int(THEME.text.rgba()),
            int(THEME.muted.rgba()),
            int(accent.rgba()),
            glow_radius,
            status_line,
        )
        now = time.perf_counter()
        needs_status_sample = now >= self._watermark_cache_next_refresh_at
        static_changed = static_key != self._watermark_title_static_key
        if self._watermark_title_cache.isNull() or static_changed or needs_status_sample:
            cache_key = static_key + (round(float(self.state.fps)),)
            self._watermark_title_static_key = static_key
            self._watermark_cache_next_refresh_at = now + 0.250
            if cache_key == self._watermark_title_cache_key and not self._watermark_title_cache.isNull():
                p.drawImage(QPointF(0.0, 0.0), self._watermark_title_cache)
                return
            logical_w, logical_h = 456.0, 56.0
            image = QImage(
                max(1, int(math.ceil(logical_w * raster_scale))),
                max(1, int(math.ceil(logical_h * raster_scale))),
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            image.setDevicePixelRatio(raster_scale)
            image.fill(Qt.GlobalColor.transparent)
            cache_painter = QPainter(image)
            cache_painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            glow_text(
                cache_painter,
                "POLAR-RM",
                12,
                28,
                z_font,
                THEME.text,
                accent,
                glow_radius,
            )
            title_w = self.text_advance(z_font, "POLAR-RM")
            self.draw_text(
                cache_painter,
                QRectF(22 + title_w, 12, 90, 24),
                f"{self.state.fps:.0f} FPS",
                body,
                THEME.text,
            )
            self.draw_text(
                cache_painter,
                QRectF(12, 34, 430, 16),
                status_line,
                self.font("poppins_regular", 8),
                THEME.muted,
            )
            cache_painter.end()
            self._watermark_title_cache = image
            self._watermark_title_cache_key = cache_key
        p.drawImage(QPointF(0.0, 0.0), self._watermark_title_cache)

    # 阶段 2b（REFACTOR_BLUEPRINT §5 表 2b 行）：D8 ESP 绘制族（draw_target_overlay /
    # 荧光白族 / draw_armor_fov_circle / 三种锁框 / target_overlay_label /
    # armor_esp_* / draw_3d_esp_box / constrain_prediction_geometry /
    # draw_clean_3d_esp_box / draw_polar_mechanical_wire / _vision_wire_key /
    # _append_vision_wire_armor / build_vision_wire_batch / paint_gpu_vision_layer /
    # draw_armor_quad / smooth_armor_geometry / draw_armor_3d_esp）已零修改迁至
    # ui/polar/renderers/esp_wireframe.py 的 EspWireframeMixin。
    # 边界保守决策：draw_pill 属 D5 legacy GUI 共享助手（不在 §2 D8 清单）、
    # alliance_side 属 D7 同济桥域，均留在原地。
    #: 准星样式 -> 绘制图元。以前 `Crosshair / Style` 在界面上能选
    #: RM Dot / Circle / Cross 三种，但全仓没人读它——不管选哪个，
    #: 画的都是下面 "RM Dot" 那一组。把它抽成表而不是写在绘制里，
    #: 是为了能不开 QPainter 直接断言几何，不靠比像素。
    #: 默认档 "RM Dot" 与接线前逐图元一致（中心点 + 四条带缺口的手臂），
    #: 所以升级后不会看到准星突变。
    CROSSHAIR_DEFAULT_STYLE = "RM Dot"

    def crosshair_style(self) -> str:
        """`Crosshair / Style` 的当前取值。查不到就回落默认，不改变现行外观。"""
        module = self.registry.get("Crosshair")
        return self.setting_mode(module, "Style", self.CROSSHAIR_DEFAULT_STYLE)

    def crosshair_primitives(self, style: str, cx: float, cy: float, size: float) -> list[tuple]:
        """把一种样式展开成有序的绘制图元列表。

        图元形状：("dot", x, y, r) / ("line", x0, y0, x1, y1) / ("ring", x, y, r)。
        未知样式回落默认，不抛异常——准星画不出来比画得普通严重得多。
        """
        gap = 6.0
        if style == "Circle":
            # 圆环 + 中心点：不伸手臂，否则圆环会与手臂尖端重叠。
            return [("dot", cx, cy, 2.4), ("ring", cx, cy, size)]
        if style == "Cross":
            # 满长十字：穿过中心，无缺口；不画中心点（会被线盖住）。
            return [
                ("line", cx - size, cy, cx + size, cy),
                ("line", cx, cy - size, cx, cy + size),
            ]
        return [
            ("dot", cx, cy, 2.4),
            ("line", cx - size, cy, cx - gap, cy),
            ("line", cx + gap, cy, cx + size, cy),
            ("line", cx, cy - size, cx, cy - gap),
            ("line", cx, cy + gap, cx, cy + size),
        ]

    def draw_crosshair(self, p: QPainter, width: int, height: int) -> None:
        module = self.registry.get("Crosshair")
        if self.crosshair_style() == "CS2":
            # Module-level renderer: no new Window API/state or paint-time I/O.
            from ..cs2_crosshair import paint_crosshair
            if paint_crosshair(p, module, width, height):
                return
        size_setting = module.get_setting("Size") if module else None
        size = float(size_setting.value if size_setting else 18)
        cx = width / 2
        cy = height / 2
        from ..cs2_crosshair import display_scale_from_module
        multiplier = display_scale_from_module(module)
        p.save()
        p.translate(cx, cy)
        p.scale(multiplier, multiplier)
        p.translate(-cx, -cy)
        main = self.crosshair_color()
        p.setPen(QPen(main, 1.7))
        p.setBrush(main)
        for item in self.crosshair_primitives(self.crosshair_style(), cx, cy, size):
            kind = item[0]
            if kind == "dot":
                p.setBrush(main)
                p.drawEllipse(QPointF(item[1], item[2]), item[3], item[3])
            elif kind == "ring":
                # 圆环不能带填充：画成实心圆就糊住目标了。
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(item[1], item[2]), item[3], item[3])
                p.setBrush(main)
            else:
                p.drawLine(QPointF(item[1], item[2]), QPointF(item[3], item[4]))
        p.restore()

    def crosshair_color(self) -> QColor:
        """准星主色。`Crosshair / Heat Color` 决定它跟不跟枪管热量走。

        以前热量色是**无条件**的：关掉这个开关准星照样变红，
        界面承诺了一个能关的东西但它关不了。

        抽成方法而不是写在 `draw_crosshair` 里：颜色可以在不开 QPainter
        的情况下直接断言，不必靠比像素。关掉时用 HUD 主色——不发明一套
        新颜色体系，也不拿白色硬编。
        """
        module = self.registry.get("Crosshair")
        heat_ratio = self.state.current_heat / max(1.0, self.state.max_heat)
        if self.setting_bool(module, "Heat Color", True):
            return self.hud_state_color("heat", heat_ratio)
        return self.hud_accent()

    def _gimbal_scratch_image(
        self,
        key: tuple[object, ...],
        img_w: int,
        img_h: int,
        image_dpr: float,
    ) -> QImage:
        """P2: 云台指示器 ARGB 草图缓冲复用。

        内容（旋转指针/文本）必须每帧重画，但 (kind, w, h, dpr) 匹配时
        底层缓冲不必每帧 malloc。尺寸/dpr 变化即重建（失效条件在 key 中）。
        """
        cache = self._gimbal_scratch_cache
        if cache is not None and cache[0] == key:
            return cache[1]
        image = QImage(
            max(1, int(math.ceil(img_w * image_dpr))),
            max(1, int(math.ceil(img_h * image_dpr))),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.setDevicePixelRatio(image_dpr)
        self._gimbal_scratch_cache = (key, image)
        return image

    def draw_chassis_gimbal_indicator(self, p: QPainter, width: int, height: int) -> None:
        """Bottom chassis/gimbal compass (main & gestalt-lab layout).

        Liquid glass (README 液态玻璃规范): register the lens on the live
        painter via draw_hud_liquid_glass (GPU → add_glass; CPU → liquid_glass).
        Never put an opaque frosted plate under the content — that violates
        “采样镜片后方场景” and looks like a dark card.

        IMPORTANT: never stroke hull/barrel with QPainter directly onto an
        OpenGL paint device. On the GPU dynamic FBO, Qt's AA path turns them
        into opaque stacked tiles. CPU-raster icon+text to a tiny QImage, blit
        once above the glass (text/icons always on top of the lens).
        """
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
        chinese = chinese_enabled(self.registry)
        mode_text = status_text("SPIN" if spin else self.chassis_follow_mode(), chinese)
        alert = min(1.0, abs(relative_yaw) / 90.0)
        pulse = 0.5 + 0.5 * math.sin(time.perf_counter() * 4.0)
        micro = self.hud_density() == "CombatCompact"

        if micro:
            size = min(max(48.0, size), 64.0)
            center = QPointF(width / 2.0, height - 86.0)
            active = self.anim.value("hud.chassis_gimbal.micro", 1.0, self.anim_duration(0.20), Easings.ease_out_pow4, initial=0.0)
            disc_r = size * 0.54
            disc_rect = QRectF(center.x() - disc_r, center.y() - disc_r, disc_r * 2.0, disc_r * 2.0)
            pad = 14.0
            img_w = int(math.ceil(size * 1.55 + pad * 2))
            img_h = int(math.ceil(size * 1.65 + pad * 2))
            origin = QPointF(center.x() - img_w * 0.5, center.y() - img_h * 0.5)
            image_dpr = max(1.0, float(self.devicePixelRatioF()))
            # P2: (micro, w, h, dpr) 匹配时复用草图缓冲，仍 fill(0)+全量重画。
            image = self._gimbal_scratch_image(
                ("gimbal-micro", img_w, img_h, image_dpr), img_w, img_h, image_dpr,
            )
            image.fill(0)
            ip = QPainter(image)
            ip.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            ip.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            ip.translate(-origin.x(), -origin.y())
            # Content only — glass is registered on the live painter below.
            self.draw_chassis_gimbal_compass_base(ip, center, size * 0.40, style, spin, pulse)
            self.draw_chassis_body_direction(ip, center, size, chassis_yaw, style, spin, pulse)
            self.draw_gimbal_turret_direction(ip, center, size, gimbal_yaw, alert)
            if show_angle or alert > 0.66:
                self.draw_glass_text(
                    ip,
                    QRectF(center.x() - 40, center.y() + size * 0.46, 80, 14),
                    f"{relative_yaw:+.0f}°",
                    self.font("poppins_medium", 7.4),
                    self.hud_accent() if alert < 0.66 else self.hud_warning(),
                    Qt.AlignmentFlag.AlignCenter,
                )
            if show_mode:
                self.draw_glass_text(
                    ip,
                    QRectF(center.x() - 40, center.y() - size * 0.68, 80, 12),
                    mode_text,
                    self.font("poppins_medium", 6.6),
                    self.hud_warning() if spin else THEME.muted,
                    Qt.AlignmentFlag.AlignCenter,
                )
            ip.end()
            p.save()
            p.setOpacity(active)
            # Circular island lens under strokes (LensIsland: radius = half height).
            self.draw_hud_liquid_glass(p, disc_rect, disc_r, surface="LensIsland", hover=0.12 * alert)
            p.drawImage(origin, image)
            p.restore()
            return

        panel_w = max(142.0, size * 2.05)
        panel_h = size + 42.0
        x = width / 2.0 - panel_w / 2.0
        y = max(112.0, height - 64.0 - panel_h - 18.0)
        rect = QRectF(x, y, panel_w, panel_h)
        active = self.anim.value("hud.chassis_gimbal.panel", 1.0, self.anim_duration(0.24), Easings.ease_out_pow4, initial=0.0)
        glass_radius = 10.0

        img_w = int(math.ceil(panel_w)) + 6
        img_h = int(math.ceil(panel_h)) + 6
        origin = QPointF(x - 3.0, y - 3.0)
        image_dpr = max(1.0, float(self.devicePixelRatioF()))
        # P2: (panel, w, h, dpr) 匹配时复用草图缓冲，仍 fill(0)+全量重画。
        image = self._gimbal_scratch_image(
            ("gimbal-panel", img_w, img_h, image_dpr), img_w, img_h, image_dpr,
        )
        image.fill(0)
        ip = QPainter(image)
        ip.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ip.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        ip.translate(-origin.x(), -origin.y())

        # Content layer only (no frosted plate — glass is the backdrop).
        top_line = QLinearGradient(rect.left() + 14, rect.top() + 5, rect.right() - 14, rect.top() + 5)
        top_line.setColorAt(0.0, with_alpha(self.hud_accent(), 0.25 + 0.30 * alert))
        top_line.setColorAt(1.0, with_alpha(self.hud_warning() if alert > 0.55 else self.hud_accent(), 0.18 + 0.34 * alert))
        ip.setPen(QPen(QBrush(top_line), 1.6))
        ip.drawLine(QPointF(rect.left() + 16, rect.top() + 5), QPointF(rect.right() - 16, rect.top() + 5))

        center = QPointF(rect.center().x(), rect.top() + 18.0 + size * 0.48)
        radius = size * 0.43
        self.draw_chassis_gimbal_compass_base(ip, center, radius, style, spin, pulse)
        self.draw_chassis_body_direction(ip, center, size, chassis_yaw, style, spin, pulse)
        self.draw_gimbal_turret_direction(ip, center, size, gimbal_yaw, alert)

        if show_angle:
            angle_text = status_text("REL {angle}", chinese, angle=f"{relative_yaw:+.0f}°")
            self.draw_glass_text(
                ip,
                QRectF(rect.left() + 11, rect.bottom() - 24, rect.width() * 0.56, 16),
                angle_text,
                self.font("poppins_medium", 7.6),
                THEME.text,
            )
        if show_mode:
            self.draw_glass_text(
                ip,
                QRectF(rect.right() - 64, rect.bottom() - 24, 52, 16),
                mode_text,
                self.font("poppins_medium", 7.4),
                self.hud_warning() if spin else THEME.muted,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        ip.end()

        p.save()
        p.setOpacity(active)
        # MainGlass / LensRegular: sample scene, edge refraction only; content blit above.
        if self.hud_clean():
            self.draw_hud_liquid_glass(p, rect, glass_radius, surface="MainGlass", hover=0.10 * alert)
        else:
            glow_rect(p, rect, glass_radius, 0.10 * alert)
            self.draw_hud_liquid_glass(p, rect, glass_radius, surface="MainGlass", hover=0.14 * alert)
        p.drawImage(origin, image)
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
            self.draw_text(p, QRectF(center.x() - 8, center.y() - radius - 17, 16, 12), status_text("N", chinese_enabled(self.registry)), self.font("poppins_bold", 6.8), THEME.muted, Qt.AlignmentFlag.AlignCenter)
        if spin:
            spin_radius = radius + 7.0 + 2.0 * pulse
            p.setPen(QPen(with_alpha(self.hud_warning(), 0.30 + 0.22 * pulse), 1.2))
            p.drawEllipse(center, spin_radius, spin_radius)
        p.restore()

    def draw_chassis_body_direction(self, p: QPainter, center: QPointF, size: float, yaw_deg: float, style: str, spin: bool, pulse: float) -> None:
        """Chassis heading — same silhouette as main/gestalt-lab (top-down hull + nose)."""
        p.save()
        p.translate(center)
        p.rotate(yaw_deg)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Slightly higher contrast than stock so hull reads over dark plate / video.
        body_color = color("#ffffff", 0.14 if style == "Minimal" else 0.12)
        outline = color("#f2f6fb", 0.72)
        accent = with_alpha(self.hud_warning(), 0.40 + 0.28 * pulse) if spin else color("#ffffff", 0.55)
        if style == "Tank":
            track_w = size * 0.10
            track_h = size * 0.58
            rounded_rect(p, QRectF(-size * 0.30, -track_h / 2, track_w, track_h), 3, color("#ffffff", 0.05), color("#ffffff", 0.22), 1.0)
            rounded_rect(p, QRectF(size * 0.20, -track_h / 2, track_w, track_h), 3, color("#ffffff", 0.05), color("#ffffff", 0.22), 1.0)
            rounded_rect(p, QRectF(-size * 0.20, -size * 0.32, size * 0.40, size * 0.64), 5, body_color, outline, 1.25)
        else:
            rounded_rect(p, QRectF(-size * 0.22, -size * 0.30, size * 0.44, size * 0.60), 5, body_color, outline, 1.2)
        # Chassis forward chevron
        p.setPen(QPen(accent, 1.7 if spin else 1.4))
        p.drawLine(QPointF(0, -size * 0.30), QPointF(0, -size * 0.50))
        p.drawLine(QPointF(-size * 0.11, -size * 0.40), QPointF(0, -size * 0.50))
        p.drawLine(QPointF(size * 0.11, -size * 0.40), QPointF(0, -size * 0.50))
        p.restore()

    def draw_gimbal_turret_direction(self, p: QPainter, center: QPointF, size: float, yaw_deg: float, alert: float) -> None:
        """Gimbal / muzzle yaw — same layout as main/gestalt-lab (hub + barrel + tip)."""
        p.save()
        p.translate(center)
        p.rotate(yaw_deg)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        turret = with_alpha(self.hud_accent(), 0.88)
        if alert > 0.55:
            turret = with_alpha(self.hud_warning(), 0.72 + 0.22 * alert)
        pen = QPen(turret, 2.15)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(QPointF(0, 0), QPointF(0, -size * 0.58))
        p.drawLine(QPointF(0, -size * 0.58), QPointF(-size * 0.075, -size * 0.46))
        p.drawLine(QPointF(0, -size * 0.58), QPointF(size * 0.075, -size * 0.46))
        p.setBrush(color("#05080e", 0.90))
        p.setPen(QPen(turret, 1.2))
        p.drawEllipse(QPointF(0, 0), size * 0.095, size * 0.095)
        p.setBrush(turret)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(0, 0), size * 0.04, size * 0.04)
        p.restore()

    def direction_point(self, center: QPointF, angle_deg: float, radius: float) -> QPointF:
        rad = math.radians(angle_deg - 90.0)
        return QPointF(center.x() + math.cos(rad) * radius, center.y() + math.sin(rad) * radius)

    def animated_angle(self, key: str, target: float, smooth: float) -> float:
        track = self.hud_angle_tracks.setdefault(key, AngleRenderTrack())
        response = 8.0 + (1.0 - max(0.0, min(1.0, smooth))) * 18.0
        return track.sample(float(self.gui_motion.now), target, response=response)

    def animated_scalar(self, key: str, target: float, response: float = 18.0) -> float:
        track = self.telemetry_render_tracks.setdefault(key, ScalarRenderTrack())
        return track.sample(float(self.gui_motion.now), target, response=response)

    def chassis_follow_mode(self) -> str:
        chassis = self.registry.get("ChassisAssist")
        setting = chassis.get_setting("Follow") if chassis else None
        value = str(setting.value).upper() if setting else "FOLLOW"
        if value == "GIMBAL":
            return "FOLLOW"
        if value == "FIELD":
            return "FIELD"
        return "FREE"

    def dynamic_smooth(self, key: str, target: float, speed: float) -> float:
        current = self.dynamic_smooth_values.get(key, target)
        difference = target - current
        if abs(difference) <= 0.025:
            current = target
        else:
            # DynamicIslandSystem 的核心是按目标差值连续收敛。这里改成
            # 帧率无关的指数形式，避免 60/120/144 Hz 下速度和手感不同。
            dt = max(1.0 / 240.0, min(1.0 / 20.0, float(self.frame_dt)))
            response = max(0.08, min(0.50, float(speed)))
            time_constant = 0.028 / response
            alpha = 1.0 - math.exp(-dt / time_constant)
            current += difference * alpha
        self.dynamic_smooth_values[key] = current
        return current

    def _discard_gpu_command_collections(self) -> None:
        """Stop recording before fallback, without submitting partial batches.

        The layer session registers this before the first collection starts.
        Its independent unwind still ends the painter and releases the FBO if
        drawing or a child renderer fails. Empty successful batches retain
        their existing objects; no replacement draw lists are allocated here.
        """
        self._collect_gpu_hud_commands = False
        self._collect_gpu_island_icons = False
        self._collect_gpu_quick_action_wheel = False
        self._gpu_island_icon_commands.clear()
        self._gpu_quick_action_wheel_command = None
        for commands in (self._gpu_hud_commands, self._gpu_quick_action_wheel_labels):
            commands.rects.clear()
            commands.texts.clear()
            commands.images.clear()
            commands.commands.clear()

    def begin_gpu_island_icon_collection(self) -> None:
        self._gpu_island_icon_commands.clear()
        self._collect_gpu_island_icons = self.island_icon_gpu.available

    def begin_gpu_hud_command_collection(self) -> None:
        """Collect high-frequency HUD glyphs for the existing atlas renderer."""

        self._gpu_hud_commands = ClickGuiDrawList(
            default_text_shadow=False,
            default_text_preserve_weight=True,
            default_text_profile="jello",
            copy_payloads=False,
        )
        self._collect_gpu_hud_commands = self.clickgui_gpu.available

    def flush_gpu_hud_commands(
        self,
        functions,
        width: int,
        height: int,
        ratio: float,
    ) -> None:
        self._collect_gpu_hud_commands = False
        commands = self._gpu_hud_commands
        self._gpu_hud_commands = ClickGuiDrawList()
        if not commands.commands or not self.clickgui_gpu.available:
            return
        self.clickgui_gpu.prepare_frame(commands, ratio)
        if not self.clickgui_gpu.render(
            functions,
            commands,
            width,
            height,
            ratio,
            atlas_prepared=True,
        ):
            self.gpu_compositor.reject_frame(
                "HUD command render rejected", detail=self.clickgui_gpu.last_error,
            )

    def flush_gpu_island_icons(self, functions, width: int, height: int, ratio: float) -> None:
        self._collect_gpu_island_icons = False
        if not self._gpu_island_icon_commands or not self.island_icon_gpu.available:
            self._gpu_island_icon_commands.clear()
            return
        if not self.island_icon_gpu.render(
            functions,
            self._gpu_island_icon_commands,
            width,
            height,
            ratio,
        ):
            self.gpu_compositor.reject_frame(
                "island icon render rejected", detail=self.island_icon_gpu.last_error,
            )
        self._gpu_island_icon_commands.clear()

    def begin_gpu_quick_action_wheel_collection(self) -> None:
        self._gpu_quick_action_wheel_command = None
        self._gpu_quick_action_wheel_labels = ClickGuiDrawList()
        self._collect_gpu_quick_action_wheel = self.quick_action_wheel_gpu.available

    def flush_gpu_quick_action_wheel(
        self,
        functions,
        width: int,
        height: int,
        ratio: float,
    ) -> None:
        self._collect_gpu_quick_action_wheel = False
        command = self._gpu_quick_action_wheel_command
        labels = self._gpu_quick_action_wheel_labels
        self._gpu_quick_action_wheel_command = None
        self._gpu_quick_action_wheel_labels = ClickGuiDrawList()
        if command is None or not self.quick_action_wheel_gpu.available:
            return
        background = self.gpu_compositor.background_fbo
        blur = self.gpu_compositor.blur_fbo
        if background is None:
            return
        blur_texture = (
            blur.texture()
            if blur is not None and getattr(self.gpu_compositor, "_blur_valid", False)
            else background.texture()
        )
        if not self.quick_action_wheel_gpu.render(
            functions,
            command,
            width,
            height,
            ratio,
            background_texture=background.texture(),
            blur_texture=blur_texture,
        ):
            self.gpu_compositor.reject_frame(
                "quick action wheel render rejected", detail=self.quick_action_wheel_gpu.last_error,
            )
            return
        if labels.commands and self.clickgui_gpu.available:
            self.clickgui_gpu.prepare_frame(labels, ratio)
            if not self.clickgui_gpu.render(
                functions,
                labels,
                width,
                height,
                ratio,
                atlas_prepared=True,
            ):
                self.gpu_compositor.reject_frame(
                    "quick action wheel label render rejected", detail=self.clickgui_gpu.last_error,
                )

    def _client_asset_path(self, *parts: str) -> Path:
        """Resolve assets under rm_custom_client/ regardless of workspace root."""
        return resolve_client_asset(self.paths, *parts)

    # ------------------------------------------------------------------
    # 阶段 3d 大域 mixin 化：build_gpu_economy_workspace（D6 GPU 经济工作区
    # 构建，含方法内嵌套 add_control_center_tile 装配闭包）已机械迁至
    # EconomyMixin，由本类继承提供，调用面不变。
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 阶段 3c 大域 mixin 化：build_gpu_clickgui（D5 GPU 构建总入口，含 gui_scroll_viewports 每帧重置与 jello 主视图委托）已机械迁至 rm_client/ui/polar/mixins/clickgui_builders_mixin.py 的 ClickGuiBuildersMixin，由本类继承提供，调用面不变；其后的 build_gpu_startup_launcher / build_gpu_robot_profile_launcher（启动页发射器装配边界）按保守决策留在本文件。
    # ------------------------------------------------------------------

    def build_gpu_startup_launcher(
        self,
        width: int,
        height: int,
        *,
        page: str | None = None,
        visual_alpha: float = 1.0,
        settled: bool = False,
    ) -> ClickGuiDrawList:
        """Build the Liquid launcher with GPU rectangles and glyph-atlas text."""
        selected_page = page or self.startup_step
        if selected_page == "robot":
            return self.build_gpu_robot_profile_launcher(width, height, visual_alpha)
        commands = ClickGuiDrawList()
        scale = max(0.74, min(1.14, self.get_auto_ui_scale(width, height, strength=0.45)))
        active = (
            1.0
            if settled
            else self.anim.value(
                "startup.gpu.open",
                1.0,
                self.anim_duration(0.34),
                Easings.ease_out_pow4,
                initial=0.0,
            )
        ) * max(0.0, min(1.0, visual_alpha))
        is_match = selected_page == "match"
        count=len(self.match_mode_manager.mode_keys)+1 if is_match else 3
        menu_x, menu_y, button_w, button_h, gap = self.liquid_menu_layout(width, height, count, scale)
        title = "MATCH MODE" if is_match else "SCENE SELECT"
        subtitle = (
            ui_text("{title} profile", chinese_enabled(self.registry), title=ui_text(self.startup_scenario_meta(self.scenario_manager.current.key)["title"], chinese_enabled(self.registry)))
            if is_match
            else "Choose runtime environment"
        )
        header_y = 42.0 * scale
        commands.text(QRectF(50.0 * scale, header_y - 20.0 * scale, 330.0 * scale, 32.0 * scale), "POLAR-RM", self.font("museo", 28.0 * scale, QFont.Weight.Bold), self.gpu_alpha(THEME.text, active))
        commands.text(QRectF(50.0 * scale, header_y + 10.0 * scale, 330.0 * scale, 18.0 * scale), ui_text("CUSTOM CLIENT", chinese_enabled(self.registry)), self.font("poppins_medium", 8.5 * scale), self.gpu_alpha(THEME.muted, active))
        notice_w = min(420.0 * scale, width * 0.36)
        notice = QRectF(width / 2.0 - notice_w / 2.0, 28.0 * scale, notice_w, 40.0 * scale)
        self.build_gpu_glass_rect(commands, notice, 15.0 * scale, active, THEME.cyan)
        commands.text(notice.adjusted(18.0 * scale, 4.0 * scale, -18.0 * scale, -19.0 * scale), ui_text(title, chinese_enabled(self.registry)), self.font("poppins_bold", 9.0 * scale), self.gpu_alpha(THEME.text, active), Qt.AlignmentFlag.AlignCenter)
        commands.text(notice.adjusted(18.0 * scale, 19.0 * scale, -18.0 * scale, -4.0 * scale), ui_text(subtitle, chinese_enabled(self.registry)), self.font("poppins_regular", 7.0 * scale), self.gpu_alpha(THEME.muted, active), Qt.AlignmentFlag.AlignCenter)
        profile_w = min(230.0 * scale, width * 0.22)
        profile = QRectF(width - profile_w - 50.0 * scale, 34.0 * scale, profile_w, 28.0 * scale)
        self.build_gpu_glass_rect(commands, profile, 10.0 * scale, active, THEME.cyan)
        commands.text(profile.adjusted(12.0 * scale, 0, -12.0 * scale, 0), ui_text("PROFILE  {profile}", chinese_enabled(self.registry), profile=self.current_profile()), self.font("poppins_medium", 7.2 * scale), self.gpu_alpha(color("#d8e5ec", 0.92), active), Qt.AlignmentFlag.AlignCenter)

        if is_match:
            entries = []
            for index, key in enumerate(self.match_mode_manager.mode_keys):
                config = self.match_mode_manager.config_for(key)
                meta = self.startup_match_meta(key)
                entries.append((key, meta["icon"], meta["title"], meta["subtitle"], f"{config.field.width_m:g}m x {config.field.height_m:g}m  {config.duration_sec // 60}:{config.duration_sec % 60:02d}", "NEXT", "select_match_mode", key, meta["tooltip"], self.startup_menu_accent(index + 2)))
            entries.append(("back", "back", "Back", "Return to scene select", "Backspace also works", "BACK", "startup_back", None, "Return to scene selection.", color("#ec5ad9")))
        else:
            entries = []
            for index, key in enumerate(("OfficialEvent", "CampusSimulation", "LabDebug")):
                config = SCENARIOS[key]
                meta = self.startup_scenario_meta(key)
                entries.append((key, meta["icon"], meta["title"], meta["subtitle"], f"MQTT {config.connection.mqtt_host}:{config.connection.mqtt_port}  UDP {config.connection.udp_port}", "LIVE" if not config.safety.mock_writes else "MOCK", "select_scenario", key, meta["tooltip"], self.startup_menu_accent(index)))
        for index, entry in enumerate(entries):
            self.build_gpu_startup_button(commands, QRectF(menu_x, menu_y + index * (button_h + gap), button_w, button_h), index, entry, scale, active, settled=settled)
        self.build_gpu_startup_session(commands, menu_x, menu_y, button_w, height, scale, active, is_match)
        self.build_gpu_startup_bottom_bar(commands, width, height, scale, active, is_match, settled=settled)
        return commands

    def build_gpu_robot_profile_launcher(self, width: int, height: int, visual_alpha: float = 1.0) -> ClickGuiDrawList:
        commands = ClickGuiDrawList()
        scale = max(0.72, min(1.12, self.get_auto_ui_scale(width, height, strength=0.42)))
        active = self.anim.value(
            "startup.robot.open", 1.0, self.anim_duration(0.32), Easings.ease_out_pow4, initial=0.0
        ) * max(0.0, min(1.0, visual_alpha))
        profiles = profiles_for_match(self.match_mode_manager.current.key, season=self.match_mode_manager.season)
        selected_key = self.current_robot_profile_key()

        commands.text(QRectF(50 * scale, 28 * scale, 340 * scale, 38 * scale), "POLAR-RM", self.font("museo", 28 * scale, QFont.Weight.Bold), self.gpu_alpha(THEME.text, active))
        island_w = min(470 * scale, width * 0.42)
        island = QRectF(width / 2 - island_w / 2, 27 * scale, island_w, 44 * scale)
        self.build_gpu_glass_rect(commands, island, 17 * scale, active, THEME.cyan)
        commands.text(island.adjusted(16 * scale, 3 * scale, -16 * scale, -21 * scale), ui_text("ROBOT PROFILE", chinese_enabled(self.registry)), self.font("poppins_bold", 9 * scale), self.gpu_alpha(THEME.text, active), Qt.AlignmentFlag.AlignCenter)
        commands.text(island.adjusted(16 * scale, 22 * scale, -16 * scale, -3 * scale), ui_text("{title} · Step 3/3", chinese_enabled(self.registry), title=self.match_mode_manager.current.title), self.font("poppins_regular", 7 * scale), self.gpu_alpha(THEME.muted, active), Qt.AlignmentFlag.AlignCenter)

        margin = 46 * scale
        top = 112 * scale
        bottom = 74 * scale
        detail_w = min(330 * scale, width * 0.27)
        gap = 14 * scale
        grid_left = margin
        grid_right = width - margin - detail_w - 24 * scale
        grid_w = max(420 * scale, grid_right - grid_left)
        columns = 4 if grid_w >= 880 * scale else 3
        rows = max(1, math.ceil(len(profiles) / columns))
        card_w = (grid_w - gap * (columns - 1)) / columns
        card_h = min(138 * scale, (height - top - bottom - gap * (rows - 1)) / rows)

        for index, profile in enumerate(profiles):
            column = index % columns
            row = index // columns
            rect = QRectF(grid_left + column * (card_w + gap), top + row * (card_h + gap), card_w, card_h)
            hovered = rect.contains(self.hover_point)
            selected = profile.key == selected_key
            hover = self.anim.value(f"startup.robot.hover.{profile.key}", 1.0 if hovered else 0.0, self.anim_duration(0.15), Easings.ease_out_expo)
            enter = self.startup_stagger_value(index, 0.34, 0.045)
            rect.translate(0, (1.0 - enter) * 22 * scale - hover * 3 * scale)
            opacity = active * enter
            accent = self.startup_menu_accent(index + 1)
            self.build_gpu_glass_rect(
                commands,
                rect,
                12 * scale,
                opacity,
                accent,
                hover=max(hover, 0.45 if selected else 0.0),
                material=LensMaterial.STRONG,
                refraction_height_px=rect.height() * 0.375,
                refraction_amount_px=rect.height() * 0.50,
            )
            if selected:
                # 3px 窄条的圆角被钳到 1.5s，需继承卡片玻璃 r=12s 的圆角裁剪，
                # 否则在卡片左上/左下弧角外泄数像素。
                commands.rect(
                    QRectF(rect.left(), rect.top(), 3 * scale, rect.height()),
                    1.5 * scale,
                    self.gpu_alpha(accent, opacity),
                    clip=QRectF(rect),
                    clip_radius=12 * scale,
                )
            icon = QRectF(rect.left() + 13 * scale, rect.top() + 14 * scale, 42 * scale, 42 * scale)
            commands.rect(icon, 14 * scale, self.gpu_alpha(with_alpha(accent, 0.74), opacity), self.gpu_alpha(color("#ffffff", 0.26), opacity), 0.8)
            commands.text(icon, profile.icon, self.font("poppins_bold", 15 * scale), self.gpu_alpha(THEME.text, opacity), Qt.AlignmentFlag.AlignCenter)
            commands.text(QRectF(icon.right() + 11 * scale, rect.top() + 14 * scale, rect.right() - icon.right() - 20 * scale, 23 * scale), ui_text(profile.short_title, chinese_enabled(self.registry)), self.font("poppins_bold", 10 * scale), self.gpu_alpha(THEME.text, opacity))
            commands.text(QRectF(icon.right() + 11 * scale, rect.top() + 38 * scale, rect.right() - icon.right() - 20 * scale, 17 * scale), ui_text("ID {id}", chinese_enabled(self.registry), id=robot_id_for_alliance(profile, self.alliance_side())), self.font("poppins_medium", 6.8 * scale), self.gpu_alpha(accent, opacity))
            commands.text(rect.adjusted(13 * scale, 67 * scale, -13 * scale, -12 * scale), ui_text(profile.description, chinese_enabled(self.registry)), self.font("poppins_regular", 7.1 * scale), self.gpu_alpha(color("#dce6ec", 0.86), opacity), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.add_hotspot(rect, "select_robot_profile", profile.key, tooltip=ui_text(f"选择{profile.title}并进入战斗 HUD", chinese_enabled(self.registry)))

        profile = profile_by_key(self.match_mode_manager.current.key, selected_key, season=self.match_mode_manager.season)
        performance = self.current_robot_performance(selected_key)
        panel = QRectF(width - margin - detail_w, top, detail_w, min(390 * scale, height - top - bottom))
        self.build_gpu_glass_rect(
            commands,
            panel,
            14 * scale,
            active,
            THEME.cyan,
            material=LensMaterial.REGULAR,
            refraction_height_px=min(18.0 * scale, panel.height() * 0.12),
            refraction_amount_px=min(24.0 * scale, panel.height() * 0.16),
        )
        commands.text(panel.adjusted(20 * scale, 18 * scale, -20 * scale, -panel.height() + 48 * scale), ui_text(profile.title, chinese_enabled(self.registry)), self.font("poppins_bold", 15 * scale), self.gpu_alpha(THEME.text, active))
        commands.text(panel.adjusted(20 * scale, 49 * scale, -20 * scale, -panel.height() + 70 * scale), ui_text("Robot ID {id} · {title}", chinese_enabled(self.registry), id=robot_id_for_alliance(profile, self.alliance_side()), title=ui_text(profile.short_title, chinese_enabled(self.registry))), self.font("poppins_medium", 7.4 * scale), self.gpu_alpha(THEME.cyan, active))
        metrics = [
            ("MAX HP", str(performance.max_health) if performance.max_health else "N/A"),
            ("POWER", f"{performance.power_limit_w} W" if performance.power_limit_w else "N/A"),
            ("HEAT", str(performance.heat_limit) if performance.heat_limit else "N/A"),
            ("COOLING", f"{performance.cooling_rate}/s" if performance.cooling_rate else "N/A"),
            ("PROJECTILE", performance.projectile),
        ]
        y = panel.top() + 88 * scale
        for index, (label, value) in enumerate(metrics):
            row = QRectF(panel.left() + 16 * scale, y + index * 43 * scale, panel.width() - 32 * scale, 32 * scale)
            commands.rect(row, 8 * scale, color("#e8f5ff", 0.075 * active), color("#ffffff", 0.12 * active), 0.7)
            commands.text(row.adjusted(11 * scale, 0, -100 * scale, 0), ui_text(label, chinese_enabled(self.registry)), self.font("poppins_bold", 6.8 * scale), self.gpu_alpha(THEME.muted, active), Qt.AlignmentFlag.AlignVCenter)
            commands.text(row.adjusted(100 * scale, 0, -11 * scale, 0), ui_text(value, chinese_enabled(self.registry)), self.font("poppins_medium", 8.5 * scale), self.gpu_alpha(THEME.text, active), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        back = QRectF(margin, height - 53 * scale, 106 * scale, 34 * scale)
        self.build_gpu_glass_rect(
            commands,
            back,
            back.height() / 2.0,
            active,
            color("#ffffff", 0.68),
            material=LensMaterial.STRONG,
            refraction_height_px=back.height() * 0.375,
            refraction_amount_px=back.height() * 0.50,
        )
        commands.text(back, ui_text("<  BACK", chinese_enabled(self.registry)), self.font("poppins_bold", 7.5 * scale), self.gpu_alpha(THEME.text, active), Qt.AlignmentFlag.AlignCenter)
        self.add_hotspot(back, "startup_back_match", None, tooltip=ui_text("返回比赛模式选择", chinese_enabled(self.registry)))
        settings = QRectF(back.right() + 14 * scale, back.top(), 106 * scale, back.height())
        self.build_gpu_glass_rect(commands, settings, settings.height() / 2.0, active, THEME.cyan)
        commands.text(settings, ui_text("Settings", chinese_enabled(self.registry)), self.font("poppins_bold", 7.5 * scale), self.gpu_alpha(THEME.text, active), Qt.AlignmentFlag.AlignCenter)
        self.add_hotspot(settings, "startup_open_settings", None, tooltip=ui_text("Settings", chinese_enabled(self.registry)))
        return commands

    # ------------------------------------------------------------------
    # 阶段 3c 大域 mixin 化：build_gpu_glass_rect … draw_tooltip 五十九个方法（D5 GPU builders 全族 + legacy CPU GUI 全族：颜色工具 gpu_alpha/gui_*_text、panel/old/new/dropdown 四视图、register_gui_scroll_viewport、PolarUnified/Multi 族、设置行族、mode_switcher/legacy_return/toasts/tooltip/keybind_overlay、draw_panel_gui…draw_tooltip）已机械迁至 rm_client/ui/polar/mixins/clickgui_builders_mixin.py 的 ClickGuiBuildersMixin，由本类继承提供，调用面不变；builder 边画边 add_hotspot 协议与 gui_scroll_viewports 注册机制保持原状（add_hotspot/_hotspot_clip 属 D15 输入域，仍在本文件）。
    # ------------------------------------------------------------------

    def add_hotspot(self, rect: QRectF, action: str, payload: Any = None, button: int | None = None, tooltip: str = "") -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return
        clip = getattr(self, "_hotspot_clip", None)
        if clip is not None:
            rect = rect.intersected(clip)
            if rect.width() <= 0 or rect.height() <= 0:
                return
        self.hotspots.append(Hotspot(QRectF(rect), action, payload, button, tooltip))

    # ------------------------------------------------------------------
    # 阶段 3e 大域 mixin 化：mousePressEvent/mouseMoveEvent/mouseReleaseEvent/wheelEvent/keyPressEvent/keyReleaseEvent 六个 Qt 输入事件 + handle_tactical_map_hold_event/handle_control_key_event/combat_control_input_allowed/toggle_bound_module_for_event/publish_spin_control_state/handle_keybind_event 键盘辅助族 + dispatch（action 路由，第一步已表化为模块级 {action: handler} 注册表并随域迁入）已机械迁至 rm_client/ui/polar/mixins/input_dispatch_mixin.py 的 InputDispatchMixin，由本类继承提供，调用面不变；add_hotspot/_hotspot_clip 属 D15 输入域核心，按任务纪律留在本文件原位。
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 阶段 3f 大域 mixin 化：after_setting_changed/handle_config_action/handle_qualification_action/start_kmc_qualification_test/restore_compact_hud/on_module_toggled/play_gui_transition_sound/preview_elimination_fx/handle_calibration_action 九个方法（D16 动作处理/配置动作/资格赛全族；原位"阶段 3d"迁出注记随连续区块整体搬运）已机械迁至 rm_client/ui/polar/mixins/config_actions_mixin.py 的 ConfigActionsMixin，由本类继承提供，调用面不变；handle_qualification_action 内局部导入 from ..preflight 重定位为 from ....preflight（同一模块 rm_client.preflight，语义等价）；_qualification_* 状态字段与 registry.add_toggle_listener(self.on_module_toggled) 接线仍在 __init__ 原位。
    # ------------------------------------------------------------------

    def sync_quick_action_wheel_settings(self) -> None:
        sync_wheel_from_module(self.registry.get("QuickActionWheel"), self.quick_action_wheel)

    def apply_quick_action_wheel_to_settings(self) -> None:
        sync_module_from_wheel(self.registry.get("QuickActionWheel"), self.quick_action_wheel)

    def draw_gui_cached_layer(
        self,
        painter: QPainter,
        rect: QRectF,
        key: tuple[object, ...],
        build,
    ) -> None:
        module = self.registry.get("ClickGui")
        if self.setting_mode(module, "Render Backend", "Unified GPU") == "Compatibility Painter":
            build(painter, QRectF(rect))
            return

        def local_build(layer: QPainter, local_rect: QRectF) -> None:
            layer.save()
            try:
                layer.translate(-rect.left(), -rect.top())
                build(layer, QRectF(rect))
            finally:
                layer.restore()

        try:
            self.gui_cache.draw(
                painter,
                rect,
                key,
                local_build,
                device_pixel_ratio=self.devicePixelRatioF(),
            )
        except Exception:
            try:
                _LOG.exception("ClickGUI cache failed; using compatibility painting")
            except Exception:
                pass  # A failed diagnostic cannot cancel cache recovery.
            # This frame may bypass the cache without changing the user's
            # persisted choice. The next frame can rebuild a healthy cache.
            self.gui_cache.clear()
            build(painter, QRectF(rect))

    def invalidate_gui_cache(self) -> None:
        self.gui_cache.clear()
        if hasattr(self, "setWindowTitle") and hasattr(self, "windowTitle"):
            title = ui_text("POLAR-RM Custom Client", chinese_enabled(self.registry))
            if self.windowTitle() != title:
                self.setWindowTitle(title)

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

    # ------------------------------------------------------------------
    # 阶段 3c 大域 mixin 化：active_clickgui_mode / gui_mode_label（staticmethod，D5 模式工具）已机械迁至 rm_client/ui/polar/mixins/clickgui_builders_mixin.py 的 ClickGuiBuildersMixin，由本类继承提供，__qualname__ 已钉回 PolarRMWindow.<name>，冻结面不变。
    # ------------------------------------------------------------------

    def toast(self, message: str, *, kind: str = "info") -> None:
        self.toasts.append({"message": message, "created": time.perf_counter(), "kind": kind})
        self.state.push_event(message)

    # ------------------------------------------------------------------
    # 阶段 1 外迁：font/text_advance/font_line_height/draw_text/
    # draw_glass_text/elide 六个文本度量方法已迁入 TextMetricsMixin
    # （rm_client/ui/polar/text_metrics.py），由本类继承提供，调用面不变。
    # ------------------------------------------------------------------

    def rect_visible(self, rect: QRectF, clip: QRectF) -> bool:
        return rect.intersects(clip)

    # ------------------------------------------------------------------
    # 阶段 3e 大域 mixin 化：qt_button/key_name/keybind_matches/is_right_shift_event 按键工具族（D15）已机械迁至 rm_client/ui/polar/mixins/input_dispatch_mixin.py 的 InputDispatchMixin，由本类继承提供，调用面不变。
    # ------------------------------------------------------------------


# ----------------------------------------------------------------------
# 阶段 3e 大域 mixin 化：dispatch 的模块级 {action: handler} 注册表
# （_GUI_CACHE_INVALIDATE_ACTIONS / _DISPATCH_HANDLERS / _on_action_* 函数族）
# 已随 D15 输入/dispatch 域整体迁至 rm_client/ui/polar/mixins/input_dispatch_mixin.py。
# ----------------------------------------------------------------------


def main() -> None:
    """Launch this window through the implementation-independent Qt owner."""
    launch_qt_application(PolarRMWindow)
