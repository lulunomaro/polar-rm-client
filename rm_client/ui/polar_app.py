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
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QWidget

from ..backend.dt7_link import status_token as _dt7_status_token
from ..calibration import CameraCalibrationManager
from ..auto_spin import ProjectileHitSpinController
from ..combat_hud import CombatHudAnimator, VALID_COMBAT_HUD_VISUALS, ring_end
from ..combat_events import CombatEvent, OfficialMatchFeed, VisionKillDetector
from ..config_store import ClientConfigStore, apply_registry_config, export_client_config
from ..control import KeyboardControlBridge
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
from .hero_deploy import HeroDeployInterfaceState, HeroDeployRenderer
from .status_locale import dt7_display_token, status_text
from .minecraft_hud import MinecraftCombatHudRenderer
from .top_match_bar_controller import TopMatchBarController
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
# _auto_save_pending/_auto_save_due_at/config_loading 状态字段仍在 __init__ 原位
# 初始化，auto-save 合并写（0.25s 防抖）语义不变；load_startup_config 在 __init__
# 的调用点不变，mixin 方法可用性由 MRO 保证。
from .polar.mixins.persistence_mixin import PersistenceMixin
# 阶段 3b 大域 mixin 化（REFACTOR_BLUEPRINT §5 阶段 3 表 3b 行）：D7 视频管线/
# 后端接线/英雄部署 + D18 官方消息/战斗事件/击杀特效 + D14 Minecraft 功能槽族
# 方法体已机械迁至 mixins/video_backend_mixin.py 的 VideoBackendMixin（零修改，
# 四段不连续区块整体搬迁），由下方 PolarRMWindow 继承装配；tick stage 列表仍在
# on_tick 按原方法名引用这些成员（名字不变）；synthetic_video_cache* /
# _hero_native_* 等状态字段与 NORMAL_BACKEND_VIDEO_STALE_S 类常量仍在原位初始化。
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
# 阶段 3e 大域 mixin 化（REFACTOR_BLUEPRINT §5 阶段 3 表 3e 行）：D15 输入/
# hotspot/dispatch 族（鼠标/滚轮/键盘事件、键盘辅助族、按键工具族、
# _clear_control_inputs_for_lock、dispatch action 路由）已机械迁至
# mixins/input_dispatch_mixin.py 的 InputDispatchMixin（零修改，四段不连续
# 区块整体搬迁），由下方 PolarRMWindow 继承装配；dispatch 的 if/elif 路由链
# 已先经第一步表化为模块级 {action: handler} 注册表（行为等价论证见新模块
# docstring）；add_hotspot/_hotspot_clip 属 D15 输入域核心，按任务纪律留在
# 本文件原位；hotspots/drag/binding_module 等输入状态字段仍在 __init__ 原位
# 初始化；__qualname__ 已钉回 PolarRMWindow.<name>。
from .polar.mixins.input_dispatch_mixin import InputDispatchMixin
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

    NORMAL_BACKEND_VIDEO_STALE_S = 2.0

    def __init__(self) -> None:
        super().__init__()
        self._gpu_prewarm_jobs_pending = None
        self._gpu_prewarm_last_ms = 0.0
        self._gpu_prewarm_max_ms = 0.0
        self._ui_tick_last_ms = 0.0
        self._ui_tick_max_ms = 0.0
        self._ui_tick_gap_ms = 0.0
        self._ui_tick_gap_max_ms = 0.0
        self._ui_tick_stages = {}
        self._ui_tick_perf_log_at = 0.0
        self._gpu_prewarm_timer = QTimer(self)
        self._gpu_prewarm_timer.setSingleShot(True)
        self._gpu_prewarm_timer.timeout.connect(self._run_gpu_prewarm_slice)
        self.setWindowTitle(ui_text("POLAR-RM Custom Client", True))
        self.resize(1280, 720)
        self.setMinimumSize(1000, 620)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)

        simulation = getattr(self, "_simulation_runtime", None)
        self.paths = simulation.paths if simulation is not None else RuntimePaths.discover()
        self.paths.ensure_writable_dirs()
        self.paths.seed_default_config()
        self.workspace = self.paths.workspace_root
        self.registry = simulation.registry() if simulation is not None else build_default_registry()
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
        self.camera_worker = simulation.camera if simulation is not None else CameraCaptureWorker()
        self.camera_started = False
        self._camera_config: tuple[int, int, int] | None = None
        # SharkClient 对齐后端：三种模式统一由 BackendManager 编排（仅接线，不动 UI）
        self.backend = simulation.create_backend(self.state) if simulation is not None else BackendManager(
            workspace=self.workspace,
            state=self.state,
            client_id=str(self.state.robot_id),
            scenario_key=self.scenario_manager.current.key,
        )
        self.mqtt = self.backend.mqtt
        self.top_match_bar = TopMatchBarController(self)
        self.backend.referee_view_observer = self.top_match_bar.observe_referee
        self.official_feed = OfficialMatchFeed()
        self.vision_kill_detector = VisionKillDetector()
        self._official_state_updates: dict[str, Any] = {}
        self.kill_fx = KillFxRenderer(self.paths.asset("kill_fx", "gd656"))
        self.ui_audio = UiAudioManager(
            self.paths.asset("ui_sounds"),
            custom_root=self.paths.user_data_root / "ui_sounds" / "custom",
        )
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
        self.control = simulation.create_control() if simulation is not None else KeyboardControlBridge(self.backend, mock_output=True)
        self.backend.input_clear_hook = self._clear_control_inputs_for_lock
        self._clear_control_inputs_for_lock("startup locked")
        self._qualification_runtime_baseline = self._capture_qualification_runtime_baseline()
        self.auto_spin = ProjectileHitSpinController()
        self.economy_bridge = EconomyCommandBridge()
        self.economy_bridge.sender = self.send_economy_request
        self.config_store = ClientConfigStore(self.paths.configs_root)
        self.calibration = CameraCalibrationManager(self.workspace, profile_dir=self.paths.calibrations_root)
        self.calibration.load("polar_webcam")
        self.tongji_receiver = simulation.tongji_receiver if simulation is not None else TongjiVisionReceiver()
        self.gimbal_command_bridge = GimbalCommandBridge()
        self.composite_vision = CompositeVisionPipeline()
        self.local_vision = simulation.local_vision if simulation is not None else LocalAlignedVision(
            self._client_asset_path("同济自瞄", "sp_vision_25", "assets", "yolov5.xml")
        )
        self.tongji_bridge = simulation.tongji_bridge if simulation is not None else TongjiVisionBridge()
        self._vision_source = "Mock"
        self._tongji_enabled = False
        self._vision_snapshot: VisionSnapshot | None = None
        self._last_capture_sequence = 0
        # Webcam/vision sequence numbers come from CameraCaptureWorker.  Keep
        # the render upload sequence separate so time spent on UDP/Native does
        # not make a later webcam restart wait for thousands of frame numbers.
        self._display_frame_sequence = 0
        self._last_backend_video_at = 0.0
        self._normal_backend_mode = ""
        self._normal_frame_source = "none"
        self._last_vision_result: VisionResult | None = None
        self.vision_render_interpolator = VisionRenderInterpolator()
        self.target_hud_presence = TargetHudPresenceState()
        self.hud_angle_tracks: dict[str, AngleRenderTrack] = {}
        self.telemetry_render_tracks: dict[str, ScalarRenderTrack] = {}
        self._vision_render_result: VisionResult | None = None
        self._dynamic_hud_started_at = time.perf_counter()
        self.dynamic_hud_ms = 0.0
        self._armor_geometry_smooth: dict[tuple[int, int, bool, str], tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float], ...]]] = {}
        self.startup_wallpaper = StartupWallpaperManager(
            self.workspace,
            assets_dir=self.paths.asset("wallpapers"),
            user_dir=self.paths.user_data_root / "wallpapers",
            pkg_dir=self.paths.resource_root / "3000925581",
            cache_dir=self.paths.user_data_root / ".wallpaper_cache" / "3000925581",
        )
        self.scenario_manager.apply_to_registry(self.registry)
        self.scenario_manager.apply_to_runtime(self.mqtt, self.udp, self.control)
        self.match_mode_manager.apply_to_registry(self.registry)
        self.fonts = FontBook()
        self.fonts.load_from_project_assets(self.paths.resource_root)
        self.anim = AnimationBank()
        self.gui_motion = GuiMotionController(self.anim)
        self.gui_cache = ClickGuiLayerCache()
        self.clickgui_gpu = ClickGuiGpuRenderer()
        self.jello_clickgui = JelloClickGuiRenderer(
            self.paths.asset("jello")
        )
        self.minecraft_hud = MinecraftCombatHudRenderer(
            self.paths.asset("minecraft_hud")
        )
        self._last_minecraft_hud_layout = None
        self.student_ui = StudentUiState()
        self.student_renderer = StudentPanelRenderer(self.paths.asset("minecraft_hud"))
        self.server_chat_renderer = MinecraftServerChatRenderer(self.paths.asset("minecraft_hud"))
        self.student_reload_expanded = False
        self._student_pending_panel = None
        self.hero_deploy_ui = HeroDeployInterfaceState()
        self.hero_deploy_renderer = HeroDeployRenderer()
        # Align the baseline instead of leaving it at zero: scenario setup
        # already bumps deploy_status_revision once, and treating that as a
        # state change invalidated the whole compositor scene one second after
        # the launcher appeared — wiping the cached startup glass commands
        # while _startup_static_page_key still claimed the page was cached.
        self._last_deploy_status_revision = int(self.state.deploy_status_revision)
        self._last_deploy_mode = int(self.state.deploy_mode)
        self.hero_native_image: QImage | None = None
        self.hero_native_pixmap: QPixmap | None = None
        self._hero_native_video_at = 0.0
        self._hero_native_sequence = 0
        self._hero_native_source = "none"
        self._hero_native_wait_started = time.perf_counter()
        self.vision_wire_gpu = VisionWireGpuRenderer()
        self.island_icon_gpu = IslandIconGpuRenderer()
        self._gpu_island_icon_commands: list[IslandIconCommand] = []
        self._collect_gpu_island_icons = False
        self._gpu_hud_commands = ClickGuiDrawList()
        self._collect_gpu_hud_commands = False
        self._watermark_title_cache = QImage()
        self._watermark_title_cache_key: tuple[object, ...] | None = None
        self._watermark_title_static_key: tuple[object, ...] | None = None
        self._watermark_cache_next_refresh_at = 0.0
        self.quick_action_wheel_gpu = QuickActionWheelGpuRenderer()
        self._gpu_quick_action_wheel_command: QuickActionWheelCommand | None = None
        self._gpu_quick_action_wheel_labels = ClickGuiDrawList()
        self._collect_gpu_quick_action_wheel = False
        self.gpu_compositor = GpuFrameCompositor()
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
        self.liquid_glass = LiquidGlassRenderer()
        self.startup_gpu = StartupGpuCompositor()
        self.hud_video_gpu = StartupGpuCompositor()
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
        self.render_frame_ms_ema = 0.0
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
        self._auto_save_pending = False
        self._auto_save_due_at = 0.0

        scenario_module = self.registry.get("Scenario")
        self.home_visible = self.setting_bool(scenario_module, "Show Startup", True)
        self.startup_step = "scenario" if self.home_visible else "done"
        self.startup_transition_started = time.perf_counter()
        # The launcher transition is deliberately independent from the card
        # entrance animation.  It retains the old page until the new one has a
        # complete GPU draw list, which prevents a half-initialised 2K frame.
        self.startup_transition_from: str | None = None
        self.startup_transition_to: str | None = None
        self._startup_page_epoch = 0
        self._startup_static_ready_at = self.startup_transition_started + self.anim_duration(0.42)
        self._startup_last_hover_action = ""
        self.gui_visible = False
        self.gui_timeline = JelloMainTimeline()
        self.jello_state = JelloClickGuiState()
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
        self.drag: dict[str, Any] | None = None
        self.binding_module: Module | None = None
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
        self.fps_samples: list[float] = []
        self._last_present_time = time.perf_counter()
        self.last_frame_time = time.perf_counter()
        self._tick_error_count = 0
        self._tick_last_error = ""
        self._tick_last_log_at: dict[str, float] = {}
        self._last_readiness_audit_at = 0.0
        self._field_frame_times_ms: list[float] = []
        self._last_field_frame_log_at = time.perf_counter()
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

        self.old_panels = {
            # Polar Old starts each 140px panel at (20, 20) with a
            # 20px gap.  Stored positions remain logical pixels when dragged.
            category: {"x": 20.0 + i * 160.0, "y": 20.0, "expanded": category in ("Combat", "Vision", "Render"), "dragged": False}
            for i, category in enumerate(CATEGORIES)
        }
        self.new_panels = {category: {"scroll": 0.0} for category in CATEGORIES}
        self.dropdown_panels = {category: {"expanded": True, "scroll": 0.0} for category in CATEGORIES}
        self.polar_windows = {
            category: {"x": 80.0 + (index % 4) * 230.0, "y": 100.0 + (index // 4) * 330.0, "scroll": 0.0}
            for index, category in enumerate([*CATEGORIES, "Profile"])
        }
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
        self.overlay_coordinator = OverlayCoordinator()
        self.quick_action_wheel = QuickActionWheelState()
        self.match_chat = MatchChatController()
        self.chat_event_adapter = ChatEventAdapter(self.match_chat)
        self.student_notice_adapter = StudentNoticeAdapter(self.match_chat)
        self._gpu_warm_pending = True

        self.sync_quick_action_wheel_settings()
        self.apply_match_mode(self.match_mode_manager.current.key, close_home=False, announce=False)
        self.apply_robot_profile(self.current_robot_profile_key(), close_home=False, announce=False)
        self.refresh_config_profiles()
        self.refresh_wallpaper_profiles()
        self.refresh_custom_ui_sound_options()
        self.config_store.ensure_default(self.current_config_data())
        self.load_startup_config()
        self.setWindowTitle(ui_text("POLAR-RM Custom Client", chinese_enabled(self.registry)))
        elimination_fx = self.registry.get("EliminationFX")
        self.kill_fx.preload_preset_sounds(
            self.setting_mode(elimination_fx, "Preset", "Default Scrolling")
        )
        if os.environ.get("POLAR_AUTOSTART", "").lower() in ("1", "true", "yes"):
            self.home_visible = False
            self.startup_step = "done"
        self.sync_mode_setting()
        if not self.home_visible:
            # 自动启动/跳过启动页时按当前场景启动后端（MQTT/UDP/串口）
            self.backend.apply_scenario(self.scenario_manager.current.key, registry=self.registry)
            self.refresh_vision_runtime()
        self.toast("ClickGUI 已就绪：Jello Sigma")

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.on_tick)
        # Drive the UI at the selected display cadence.  Polling at 5 ms and
        # throwing callbacks away produced visibly irregular Sigma timelines.
        self.timer.start(self._render_timer_interval_ms)

    def on_tick(self) -> None:
        now = time.perf_counter()
        self.flush_auto_save_if_due(now)
        self.gui_motion.begin_frame(now)
        self._ui_frame_index += 1
        elapsed = max(0.0, now - self.last_frame_time)
        self._ui_tick_gap_ms = elapsed * 1000.0
        self._ui_tick_gap_max_ms = max(self._ui_tick_gap_max_ms, self._ui_tick_gap_ms)
        # UI timelines follow the display clock.  A first-use glyph/FBO upload
        # can block one frame for more than 50 ms; truncating that elapsed time
        # made Jello's nominal 450 ms open take 700-900 ms and all HUD motion
        # briefly enter slow motion.  Physics/backend ticks retain their own
        # conservative 50 ms cap below.
        ui_dt = max(0.001, min(1.0, elapsed))
        self.last_frame_time = now
        self.frame_dt = ui_dt
        ui_scale_module = self.registry.get("UIScale")
        refresh_setting = ui_scale_module.get_setting("HUD Refresh Rate") if ui_scale_module else None
        try:
            requested_hz = float(refresh_setting.value) if refresh_setting else 144.0
        except (TypeError, ValueError):
            requested_hz = 144.0
        self.render_fps_target = max(120.0, min(self.render_fps_cap, requested_hz))
        requested_interval = max(4, int(round(1000.0 / self.render_fps_target)))
        if requested_interval != self._render_timer_interval_ms:
            self._render_timer_interval_ms = requested_interval
            self.timer.setInterval(requested_interval)
            self.frame_scheduler.force()
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
            self.control.mock_output = bool(self.backend.mqtt.mock_output)
            self.control.release_all(self.state, self.registry)
            self._control_mouse_point = None
        self._combat_input_was_allowed = input_allowed
        for motion in self.polar_settings_scroll_motion.values():
            motion.update(ui_dt)
        self.gui_timeline.set_target(1.0 if self.gui_visible else 0.0)
        self.gui_timeline.update(ui_dt)
        self.jello_state.update(ui_dt)
        if (
            self.jello_state.reset_pending
            and not self.gui_visible
            and self.gui_timeline.progress <= 0.001
        ):
            self.jello_state.finish_reset()
        gui_mode = self.active_clickgui_mode()
        self.polar_unified_morph.set_target(
            1.0 if self.gui_visible and gui_mode == "PolarUnified" else 0.0,
            self.anim_duration(0.62 if self.gui_visible else 0.48),
            now=now,
        )
        self.polar_unified_morph.update(now=now)
        self.economy_island.set_target(
            1.0 if self.economy_visible else 0.0,
            self.anim_duration(0.62 if self.economy_visible else 0.48),
            now=now,
        )
        self.economy_island.update(now=now)
        if (
            self.economy_phase == EconomyScenePhase.OPENING
            and self.economy_island.progress >= 0.999
        ):
            self.economy_phase = EconomyScenePhase.OPEN
        # Hero deploy uses the same reversible Control Center spring as the
        # economy island.  Advance it at the render-timer rate (not the 60 Hz
        # logic gate) so the Dynamic-Island morph remains smooth at 120/144 Hz.
        self.hero_deploy_ui.update_presentation(now)
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
        self.state.economy_mock_mode = self.economy_uses_mock_transport()
        economy_transaction = self.economy_bridge.update(self.state, now)
        if (
            economy_transaction is not None
            and economy_transaction.status != "pending"
            and economy_transaction.sequence != self.economy_last_status_sequence
        ):
            self.economy_last_status_sequence = economy_transaction.sequence
            self.toast(economy_transaction.message)
            match_chat = self.registry.get("MatchChat")
            if (
                match_chat is not None
                and match_chat.enabled
                and self.setting_bool(match_chat, "Referee Notices", True)
            ):
                self.chat_event_adapter.economy(economy_transaction, now)
        self.tactical_map_state.update(ui_dt)
        dynamic_payload = self.dynamic_island_payload()
        content_duration = 0.30 if dynamic_payload["kind"] == "module" else 0.80
        if not dynamic_payload["active"]:
            content_duration = 0.22
        self.dynamic_island_state.set_content_target(
            1.0 if dynamic_payload["active"] else 0.0,
            content_duration,
        )
        self.dynamic_island_state.update(ui_dt)
        logic_interval = 1.0 / 60.0
        # KMC runs on its own 75 Hz sub-tick. Keeping it inside the 60 Hz
        # logic layer capped real output at 60 Hz while the official limiter
        # (and qualification evidence) assumes 75 Hz frames.
        control_interval = 1.0 / 75.0
        if now - self._last_control_tick_time >= control_interval * 0.92:
            self._last_control_tick_time = now
            self._run_tick_stage(
                "control.75hz",
                lambda: self.control.tick(self.state, self.registry, now),
                now,
            )
        if now - self._last_logic_time >= logic_interval * 0.92:
            logic_dt = max(0.001, min(0.05, now - self._last_logic_time))
            self._last_logic_time = now
            if not self.home_visible and not self.hero_deploy_video_cut_active():
                self.ensure_camera_started()
            self.startup_wallpaper.set_active(self.home_visible)
            if not self.home_visible:
                self.refresh_vision_runtime()
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
                ("hero.deploy", lambda: self.update_hero_deploy_state(now)),
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
            match_chat = self.registry.get("MatchChat")
            if (
                match_chat is not None
                and match_chat.enabled
                and self.setting_bool(match_chat, "Referee Notices", True)
            ):
                self.chat_event_adapter.poll(self.state, now)
                self.student_notice_adapter.poll(self.state, now)
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
            or self.hero_deploy_ui.active
            or self.hero_deploy_ui.pending_mode is not None
        )
        if self.frame_scheduler.should_request(now, high_rate, self.render_fps_target):
            self.update()
        self._ui_tick_last_ms = (time.perf_counter() - now) * 1000.0
        self._ui_tick_max_ms = max(self._ui_tick_max_ms, self._ui_tick_last_ms)
        if now - self._ui_tick_perf_log_at >= 5.0:
            self._ui_tick_perf_log_at = now
            metrics = {
                "tick_ms": self._ui_tick_last_ms,
                "tick_max_ms": self._ui_tick_max_ms,
                "wakeup_gap_ms": self._ui_tick_gap_ms,
                "wakeup_gap_max_ms": self._ui_tick_gap_max_ms,
                "stages": {name: dict(stats) for name, stats in self._ui_tick_stages.items()},
                "gpu_prewarm_last_ms": self._gpu_prewarm_last_ms,
                "gpu_prewarm_max_ms": self._gpu_prewarm_max_ms,
            }
            self.state.backend_stats["ui_tick_performance"] = metrics
            try:
                self.backend.field_log.emit("ui_tick_timing", **metrics)
            except Exception:
                _LOG.warning("UI timing diagnostic could not be written", exc_info=True)
            self._ui_tick_stages.clear()
            self._ui_tick_max_ms = self._ui_tick_gap_max_ms = 0.0

    def _run_tick_stage(self, name: str, callback: Any, now: float | None = None) -> bool:
        """Keep one backend/vision fault from killing the Qt timer callback."""

        started = time.perf_counter()
        try:
            callback()
            return True
        except Exception as exc:
            now = time.perf_counter() if now is None else float(now)
            self._tick_error_count += 1
            self._tick_last_error = f"{name}: {type(exc).__name__}: {exc}"
            self.state.backend_stats["ui_tick"] = {
                "errors": self._tick_error_count,
                "last_error": self._tick_last_error,
            }
            if now - self._tick_last_log_at.get(name, 0.0) >= 1.0:
                self._tick_last_log_at[name] = now
                _LOG.exception("UI tick stage failed: %s", name)
                self.backend.field_log.emit(
                    "thread_exception",
                    stage=name,
                    error_type=type(exc).__name__,
                    error=str(exc),
                    count=self._tick_error_count,
                )
            return False
        finally:
            # Preserve exception isolation while exposing work outside paintGL.
            # Collection is cheap and bounded by the fixed list of tick stages.
            stages = getattr(self, "_ui_tick_stages", None)
            if stages is not None:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                stats = stages.setdefault(name, {"count": 0, "total_ms": 0.0, "max_ms": 0.0})
                stats["count"] += 1
                stats["total_ms"] += elapsed_ms
                stats["max_ms"] = max(stats["max_ms"], elapsed_ms)

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
            "deploy_revision": int(self.state.deploy_status_revision),
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
        super().resizeEvent(event)
        self.gui_cache.clear()
        self.map_display_cache.clear()
        self._gpu_background_key = None
        self._gpu_background_transform_key = None
        self._gpu_static_ui_key = None
        self._gpu_motion_layer_ready = False
        self._gpu_motion_layer_updated_at = 0.0
        # Economy text scales with the viewport. Prepare the new font pages on
        # the first idle frame after a resize instead of during the first B
        # press at that resolution.
        self._gpu_warm_pending = True

    def initializeGL(self) -> None:
        self.gpu_compositor.initialize()
        self.vision_wire_gpu.initialize()
        self.island_icon_gpu.initialize()
        self.quick_action_wheel_gpu.initialize()
        if not self.clickgui_gpu.initialize():
            click_gui = self.registry.get("ClickGui")
            backend = click_gui.get_setting("Render Backend") if click_gui else None
            if backend is not None:
                backend.value = "Compatibility Painter"
        self.startup_gpu.initialize()
        self.hud_video_gpu.initialize()
        # Native passes only draw into private FBOs. The window framebuffer is
        # touched once by GpuFrameCompositor, avoiding Qt/GL state collisions.

    def paintGL(self) -> None:
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
        present_dt = max(0.001, min(0.25, presented - self._last_present_time))
        self._last_present_time = presented
        self.fps_samples.append(1.0 / present_dt)
        if len(self.fps_samples) > 90:
            self.fps_samples.pop(0)
        self.state.fps = sum(self.fps_samples) / len(self.fps_samples)
        elapsed_ms = (presented - started) * 1000.0
        self.render_frame_ms_ema = elapsed_ms if self.render_frame_ms_ema <= 0.0 else self.render_frame_ms_ema * 0.90 + elapsed_ms * 0.10
        self._field_frame_times_ms.append(elapsed_ms)
        if len(self._field_frame_times_ms) > 720:
            del self._field_frame_times_ms[:-720]
        if presented - self._last_field_frame_log_at >= 5.0 and self._field_frame_times_ms:
            ordered = sorted(self._field_frame_times_ms)
            p95_index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
            self.backend.field_log.emit(
                "frame_timing",
                samples=len(ordered),
                frame_ms_p95=ordered[p95_index],
                frame_ms_max=ordered[-1],
                frame_ms_ema=self.render_frame_ms_ema,
                fps=self.state.fps,
                render_target_fps=self.render_fps_target,
                compositor_ms=getattr(self.gpu_compositor, "frame_ms", 0.0),
                ui_tick_ms=self._ui_tick_last_ms,
                ui_wakeup_gap_ms=self._ui_tick_gap_ms,
                gpu_prewarm_last_ms=self._gpu_prewarm_last_ms,
                gpu_prewarm_max_ms=self._gpu_prewarm_max_ms,
            )
            self._field_frame_times_ms.clear()
            self._last_field_frame_log_at = presented
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
            20.0: {module.description, jello_description_label(module.description, True)},
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
            pages[14.0].update((setting.name, localized_setting, setting.description, jello_description_label(setting.description, True)))
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
        if not self.clickgui_gpu.available or not self.gpu_compositor.available:
            return
        self._gpu_prewarm_jobs_pending = self._gpu_prewarm_jobs()
        self._gpu_prewarm_timer.start(16)

    def _gpu_prewarm_jobs(self):
        """Allocate shaders and glyph atlases while the launcher is idle.

        Right Shift must only change animation uniforms.  It must not compile a
        shader, allocate an atlas, or rasterise every module label on its first
        press.
        """
        if not self.clickgui_gpu.available:
            return
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
            profile_by_key(self.state.match_mode, self.state.robot_profile).title,
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


    def _run_gpu_prewarm_slice(self) -> None:
        jobs = self._gpu_prewarm_jobs_pending
        if jobs is None or not self.gpu_compositor.available or not self.isVisible():
            return
        context = self.context()
        if context is None or not context.isValid():
            return
        started = time.perf_counter()
        try:
            self.makeCurrent()
            # One atlas/image per turn. The 3 ms target is soft: a single
            # driver upload cannot be preempted. Record real overruns instead
            # of reporting the timer budget as measured frame time.
            while time.perf_counter() - started < 0.003:
                kind, args, kwargs = next(jobs)
                if kind == "text":
                    self.clickgui_gpu.prewarm(*args, **kwargs)
                elif kind == "image":
                    self.clickgui_gpu.prewarm_images(*args)
                break
        except StopIteration:
            self._gpu_prewarm_jobs_pending = None
        except Exception as exc:
            self._gpu_prewarm_jobs_pending = None
            self.backend.field_log.emit("gpu_prewarm_error", error=str(exc))
        finally:
            self.doneCurrent()
            self._gpu_prewarm_last_ms = (time.perf_counter() - started) * 1000.0
            self._gpu_prewarm_max_ms = max(self._gpu_prewarm_max_ms, self._gpu_prewarm_last_ms)
        if self._gpu_prewarm_jobs_pending is not None:
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
            if self.hero_deploy_video_cut_active():
                # The cached compositor owns the aspect-fit/letterbox Hero
                # path.  Never let this legacy fast path upload frame_image.
                return False
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
        if width <= 0 or height <= 0 or not self.gpu_compositor.ensure_size(width, height, ratio):
            return False
        functions = context.extraFunctions()
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
            economy_active = not startup_scene and (
                self.economy_island.progress > 0.01 or self.economy_visible
            )
            clickgui_active = not startup_scene and (
                self.gui_timeline.progress > 0.01 or self.gui_visible
            )
            student_active = not startup_scene and self.student_ui.visible
            escape_active = not startup_scene and self.escape_menu_visible
            modal_backdrop_active = economy_active or clickgui_active or student_active or escape_active
            economy_reveal = (
                max(0.0, min(1.0, self.economy_island.progress))
                if economy_active
                else 0.0
            )
            clickgui_reveal = (
                max(0.0, min(1.0, self.gui_timeline.progress))
                if clickgui_active
                else 0.0
            )
            # Sigma drives a 20 px blur with min(1, raw * 4).  Its black
            # overlay is part of ClickGuiScreen itself, so the compositor must
            # not add Polar's separate dim pass on top of it.
            if economy_active:
                backdrop_active = economy_reveal > 0.0
                backdrop_progress = economy_reveal
                backdrop_blur = 4.0 * economy_reveal
                backdrop_dim = 0.40
                backdrop_blur_kernel = "compact_gaussian"
            elif clickgui_active and self.active_clickgui_mode() == PRIMARY_GUI_MODE:
                backdrop_active = clickgui_reveal > 0.0
                backdrop_progress = 1.0 if backdrop_active else 0.0
                backdrop_blur = self.gui_timeline.blur_radius()
                backdrop_dim = 0.0
                backdrop_blur_kernel = "jello_box20"
            elif escape_active:
                backdrop_active = True
                backdrop_progress = 1.0
                backdrop_blur = 20.0
                backdrop_dim = 0.0
                backdrop_blur_kernel = "jello_box20"
            elif student_active:
                backdrop_active = True
                backdrop_progress = 1.0
                backdrop_blur = 0.0 if self.student_ui.panel=="damage" else 20.0 * min(1.0, self.student_ui.progress * 4.0)
                backdrop_dim = 0.0
                backdrop_blur_kernel = "jello_box20"
            else:
                # Keep the historical compositor treatment when a legacy GUI
                # is entered temporarily through Old ClickGUIs.
                backdrop_active = clickgui_reveal > 0.0
                backdrop_progress = clickgui_reveal
                backdrop_blur = 3.0
                backdrop_dim = 0.26
                backdrop_blur_kernel = "compact_gaussian"
            treatment_changed = self.gpu_compositor.set_backdrop_treatment(
                active=backdrop_active,
                progress=backdrop_progress,
                blur_radius_px=backdrop_blur,
                dim_alpha=backdrop_dim,
                include_overlay=not economy_active,
                blur_kernel=backdrop_blur_kernel,
            )
            control_changed = self.paint_gpu_control_backdrop(
                functions,
                width,
                height,
                ratio,
                startup_scene=startup_scene,
                economy_active=economy_active,
            )
            # Modal vision must never punch crisply through the treated
            # backdrop. Economy additionally clears the complete HUD layer;
            # Jello keeps the cached HUD under Sigma's blur.
            if modal_backdrop_active:
                self.gpu_compositor.clear_vision_layer(functions)
                native_vision_owner = False
            else:
                native_vision_owner = self.paint_gpu_vision_layer(functions, width, height, ratio)

            if startup_scene:
                transition_active = (
                    self.startup_transition_from is not None
                    and self.startup_transition_to is not None
                    and time.perf_counter() - self.startup_transition_started < self.anim_duration(0.42)
                )
                # The stable page can be cached as soon as a transition ends.
                # Waiting for a timer here left a transparent dynamic layer on
                # 2K displays and exposed the previous page for one more frame.
                cache_ready = not transition_active
                if cache_ready:
                    launcher_key = (*static_key, self.scenario_manager.current.key, self.match_mode_manager.current.key, chinese_enabled(self.registry))
                    if launcher_key != self._startup_static_page_key:
                        self.hotspots.clear()
                        self.gpu_compositor.clear_static_glass()
                        self.gpu_compositor.begin_static_glass()
                        commands = self.build_gpu_startup_launcher(width, height, page=self.startup_step, settled=True)
                        self.gpu_compositor.end_static_glass()
                        self.clickgui_gpu.prepare_frame(commands, ratio)
                        device = self.gpu_compositor.paint_device("overlay")
                        if device is None:
                            return False
                        self.gpu_compositor.clear_current(functions, transparent=True)
                        if not self.clickgui_gpu.render(
                            functions,
                            commands,
                            width,
                            height,
                            ratio,
                            atlas_prepared=True,
                        ):
                            return False
                        self.gpu_compositor.release_layer("overlay")
                        self._startup_static_page_key = launcher_key
                    if not self.paint_gpu_startup_interaction_layer(functions, width, height, ratio, cached_static=True):
                        return False
                else:
                    self.gpu_compositor.clear_static_glass()
                    self._startup_static_page_key = None
                    device = self.gpu_compositor.paint_device("overlay")
                    if device is None:
                        return False
                    self.gpu_compositor.clear_current(functions, transparent=True)
                    self.gpu_compositor.release_layer("overlay")
                    if not self.paint_gpu_startup_interaction_layer(functions, width, height, ratio):
                        return False
                self._gpu_static_ui_key = static_key
            else:
                # The liquid-glass specification makes Economy an exclusive
                # scene. Jello and retained GUIs use the normal HUD snapshot
                # path whenever Economy is inactive.
                if economy_active:
                    # Economy is an exclusive scene: camera + economy control
                    # backdrop only.  Clearing the cached host HUD here fixes
                    # the map/ModuleList ghosting that contradicted the sole
                    # liquid-glass specification, without changing any shared
                    # HUD or glass renderer.
                    device = self.gpu_compositor.paint_device("overlay")
                    if device is None:
                        return False
                    device.setDevicePixelRatio(ratio)
                    self.gpu_compositor.clear_current(functions, transparent=True)
                    self.gpu_compositor.release_layer("overlay")
                    self._gpu_static_ui_key = static_key
                elif rebuild_hud_static:
                    device = self.gpu_compositor.paint_device("overlay")
                    if device is None:
                        return False
                    device.setDevicePixelRatio(ratio)
                    self.gpu_compositor.clear_current(functions, transparent=True)
                    self.gpu_compositor.push_glass_owner(GlassOwner.HUD)
                    self.gpu_compositor.begin_static_glass()
                    painter = QPainter(device)
                    self.paint_overlay(
                        None,
                        startup_background_ready=True,
                        hud_background_ready=True,
                        painter=painter,
                        logical_size=(width, height),
                        include_interactive=False,
                        split_motion_layer=not modal_backdrop_active,
                        capture_module_list=modal_backdrop_active,
                    )
                    painter.end()
                    self.gpu_compositor.end_static_glass()
                    self.gpu_compositor.pop_glass_owner()
                    self.gpu_compositor.release_layer("overlay")
                    self._gpu_static_ui_key = static_key

                if economy_active and self.clickgui_gpu.available:
                    # Economy panel is the complete dynamic scene.  Do not draw HUD
                    # motion here: its only backdrop is the live camera texture plus
                    # economy-owned control rails.
                    self.gpu_compositor.push_glass_owner(GlassOwner.ECONOMY)
                    try:
                        commands = self.build_gpu_economy_workspace(width, height)
                    finally:
                        self.gpu_compositor.pop_glass_owner()
                    self.clickgui_gpu.prepare_frame(commands, ratio)

                    device = self.gpu_compositor.paint_device("dynamic")
                    if device is None:
                        return False
                    device.setDevicePixelRatio(ratio)
                    self.gpu_compositor.clear_current(functions, transparent=True)
                    if not self.clickgui_gpu.render(
                        functions, commands, width, height, ratio, atlas_prepared=True,
                    ):
                        self.gpu_compositor.release_layer("dynamic")
                        return False
                    self.gpu_compositor.release_layer("dynamic")
                    self.gpu_compositor.clear_target_layer(functions)
                else:
                    prepared_clickgui_commands: ClickGuiDrawList | None = None
                    prepared_clickgui_hotspots: list[Hotspot] | None = None
                    prepared_clickgui_atlas = False
                    click_gui = self.registry.get("ClickGui")
                    gui_alpha = self.gui_timeline.progress
                    if (
                        (self.gui_visible or gui_alpha > 0.01)
                        and self.setting_mode(click_gui, "Render Backend", "Unified GPU") == "Unified GPU"
                        and self.clickgui_gpu.available
                    ):
                        self.gpu_compositor.push_glass_owner(GlassOwner.CLICKGUI)
                        try:
                            prepared_clickgui_commands = self.build_gpu_clickgui(width, height, gui_alpha)
                        finally:
                            self.gpu_compositor.pop_glass_owner()
                        # Snapshot hit targets before motion overlay / overlay clear
                        # wipes them.  Layout runs once; present reuses this list.
                        prepared_clickgui_hotspots = list(self.hotspots)
                        self.clickgui_gpu.prepare_frame(prepared_clickgui_commands, ratio)
                        prepared_clickgui_atlas = True

                    priority_overlay_active = bool(
                        self.quick_action_wheel.visible
                        or self.match_chat.input_open
                        or self.student_ui.visible
                        or self.escape_menu_visible
                    )
                    now = time.perf_counter()
                    # The wheel is normally held for less than a second, so a
                    # frozen base HUD is the cleanest result.  Chat can stay
                    # open longer; refresh the base at 4 Hz while its input and
                    # caret continue at the display refresh rate.
                    base_refresh_interval = (
                        0.25
                        if (self.match_chat.input_open or self.escape_menu_visible)
                        and not self.quick_action_wheel.visible
                        else float("inf")
                    )
                    refresh_motion_layer = bool(
                        not priority_overlay_active
                        or not self._gpu_motion_layer_ready
                        or now - self._gpu_motion_layer_updated_at
                        >= base_refresh_interval
                    )

                    if refresh_motion_layer:
                        dynamic_started = time.perf_counter()
                        device = self.gpu_compositor.paint_device("dynamic")
                        if device is None:
                            return False
                        device.setDevicePixelRatio(ratio)
                        self.gpu_compositor.clear_current(functions, transparent=True)
                        self.begin_gpu_hud_command_collection()
                        self.begin_gpu_island_icon_collection()
                        self.begin_gpu_quick_action_wheel_collection()
                        painter = QPainter(device)
                        if not self.home_visible and not clickgui_active:
                            # Priority overlays own target_fbo and must not be
                            # baked into this reusable base frame.
                            self.paint_hud_motion_overlay(
                                painter,
                                width,
                                height,
                                include_vision=not native_vision_owner,
                                include_target_hud=(
                                    self.menu_overlay_active()
                                    and not priority_overlay_active
                                ),
                                include_chat=not self.match_chat.input_open,
                                include_radial=not self.quick_action_wheel.visible,
                            )
                        if not self.home_visible:
                            self.paint_clickgui_overlay(
                                painter,
                                width,
                                height,
                                gpu_commands=prepared_clickgui_commands,
                                gpu_hotspots=prepared_clickgui_hotspots,
                                gpu_atlas_prepared=prepared_clickgui_atlas,
                            )
                        painter.end()
                        self.flush_gpu_hud_commands(functions, width, height, ratio)
                        self.flush_gpu_island_icons(functions, width, height, ratio)
                        self.flush_gpu_quick_action_wheel(functions, width, height, ratio)
                        self.gpu_compositor.release_layer("dynamic")
                        self._gpu_motion_layer_ready = True
                        self._gpu_motion_layer_updated_at = now
                        self.gpu_compositor.last_dynamic_ms = (
                            time.perf_counter() - dynamic_started
                        ) * 1000.0

                    if priority_overlay_active:
                        if not self.paint_priority_interaction_layer(
                            functions,
                            width,
                            height,
                            ratio,
                        ):
                            return False
                    elif modal_backdrop_active:
                        self.gpu_compositor.clear_target_layer(functions)
                    elif not self.paint_gpu_target_hud_layer(
                        functions,
                        width,
                        height,
                        ratio,
                    ):
                        return False

            base_blur_dirty = self._gpu_background_changed or control_changed
            if not self.gpu_compositor.prepare_blur(
                functions,
                background_changed=base_blur_dirty,
            ):
                return False
            # Native HUD lenses sample only the scene/control blur. They never
            # read the modal treatment texture, so opening a workspace cannot
            # alter their material or feed a previous HUD frame back into them.
            if not self.gpu_compositor.render_glass(
                functions,
                include_static=True,
                owners={GlassOwner.HUD},
                destination="hud",
                apply_backdrop_treatment=False,
            ):
                return False
            # Capture the complete native HUD once, then soften that stable
            # result for ClickGUI/economy. Overlay lenses stay crisp and sample
            # this treated backdrop rather than a cheaper replacement surface.
            if not self.gpu_compositor.prepare_treated_backdrop(
                functions,
                background_changed=(
                    base_blur_dirty
                    or treatment_changed
                    or rebuild_hud_static
                ),
            ):
                return False
            glass_owners: set[GlassOwner] = set()
            if economy_active:
                glass_owners.add(GlassOwner.ECONOMY)
            if clickgui_active:
                glass_owners.add(GlassOwner.CLICKGUI)
            if self.quick_action_wheel.visible:
                glass_owners.add(GlassOwner.RADIAL)
            if self.hero_deploy_ui.render_visible:
                glass_owners.add(GlassOwner.HERO_DEPLOY)
            if not self.gpu_compositor.render_glass(
                functions,
                include_static=True,
                owners=glass_owners,
                destination="overlay",
            ):
                return False
            presented = self.gpu_compositor.present(
                functions,
                self.defaultFramebufferObject(),
            )
            if presented and restore_attempt and restore_generation is not None:
                self._finalize_economy_restore(restore_generation)
            return presented
        except Exception as exc:
            self.gpu_compositor.last_error = str(exc)
            try:
                self.gpu_compositor.release_layer("background")
                self.gpu_compositor.release_layer("control")
                self.gpu_compositor.release_layer("overlay")
                self.gpu_compositor.end_vision_layer()
                self.gpu_compositor.release_layer("dynamic")
                self.gpu_compositor.release_layer("target")
            except Exception:
                pass
            # Never preserve metadata for a partially rendered modal frame.
            # The compatibility fallback below remains visible for the current
            # present; the next GL frame then performs a complete, deterministic
            # rebuild.  Economy restore generations intentionally stay pending.
            self.invalidate_transient_overlay_scene()
            self.frame_scheduler.force()
            return False
        finally:
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
        device = self.gpu_compositor.paint_device("control")
        if device is None:
            return False
        device.setDevicePixelRatio(ratio)
        self.gpu_compositor.clear_current(functions, transparent=True)
        if economy_active:
            # The reference Control Center applies dim and blur to the complete
            # captured backdrop in the compositor. This layer intentionally
            # stays transparent; rebuilding a full-screen QPainter mask on
            # every spring frame caused both pulsing and avoidable stalls.
            pass
        elif startup_scene and self.startup_step in {"scenario", "match"}:
            painter = QPainter(device)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            scale = max(0.74, min(1.14, self.get_auto_ui_scale(width, height, strength=0.45)))
            menu_x, menu_y, button_w, button_h, gap = self.liquid_menu_layout(width, height, 3, scale)
            accents = (
                [self.startup_menu_accent(index + 2) for index in range(2)] + [color("#ec5ad9")]
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
            painter.end()
        self.gpu_compositor.release_layer("control")
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
        device = self.gpu_compositor.paint_device("dynamic")
        if device is None:
            return False
        try:
            self.gpu_compositor.clear_current(functions, transparent=True)
            rendered = self.clickgui_gpu.render(
                functions,
                commands,
                width,
                height,
                ratio,
                atlas_prepared=True,
            )
            self.gpu_compositor.last_dynamic_ms = (time.perf_counter() - started) * 1000.0
            return rendered
        finally:
            self.gpu_compositor.release_layer("dynamic")

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
            self.student_ui.panel if self.student_ui.visible else None,
            int(self.state.deploy_mode),
            int(self.state.deploy_status_revision),
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
        elif self.hero_deploy_video_cut_active():
            hero_frame_state = self.hero_native_frame_state()
            background_key = (
                "hero-deploy-video-cut",
                chinese_enabled(self.registry),
                int(self.state.deploy_status_revision),
                self._hero_native_sequence,
                hero_frame_state,
                width,
                height,
                round(ratio, 3),
            )
            transform_key = ("hero-deploy-video-cut-transform", width, height)
            self.gpu_compositor.set_scene_uv_offset(0.0, 0.0)
            blur_source_key = (
                "hero-deploy-video-cut-blur",
                chinese_enabled(self.registry),
                int(self.state.deploy_status_revision),
                self._hero_native_sequence,
                hero_frame_state,
                width,
                height,
                round(ratio, 3),
            )
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
        device = self.gpu_compositor.paint_device("background")
        if device is None:
            return False
        device.setDevicePixelRatio(ratio)
        self.gpu_compositor.clear_current(functions, transparent=False)
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
            painter = QPainter(device)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not self.high_res_optimize(width, height))
            if self.home_visible:
                self.draw_startup_background(painter, width, height)
            elif self.hero_deploy_video_cut_active():
                self.draw_hero_native_background(painter, width, height)
            elif self.backend_video_source_selected() and not self.normal_backend_video_fresh():
                self.draw_backend_video_lost(painter, width, height)
            elif self.normal_video_frame_ready() and self.frame_image is not None and not self.frame_image.isNull():
                painter.drawImage(canvas, self.frame_image, QRectF(self.frame_image.rect()))
            else:
                self.draw_synthetic_video(painter, width, height)
            painter.end()
        self.gpu_compositor.release_layer("background")
        self._gpu_background_key = background_key
        self._gpu_background_transform_key = transform_key
        self._gpu_background_changed = blur_source_key != self._gpu_blur_source_key
        self._gpu_blur_source_key = blur_source_key
        self.gpu_compositor.last_background_ms = (time.perf_counter() - started) * 1000.0
        return True

    def tune_render_rate(self, now: float) -> None:
        if now - self._last_render_tune < 0.35 or self.render_frame_ms_ema <= 0.0:
            return
        self._last_render_tune = now
        levels = [60.0, 72.0, 90.0, 120.0, 144.0]
        allowed = [level for level in levels if level <= self.render_fps_cap + 0.5]
        if not allowed:
            allowed = [60.0]
        current = min(range(len(allowed)), key=lambda index: abs(allowed[index] - self.render_fps_target))
        budget_ms = 1000.0 / max(1.0, allowed[current])
        measured_ms = self.render_frame_ms_ema
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
            if not startup_background_ready:
                self.draw_startup_background(p, width, height)
            if self.wallpaper_picker_visible:
                self.draw_wallpaper_picker(p, width, height)
            else:
                self.draw_startup_page_transition(p, width, height)
            if self.hover_tooltip:
                self.draw_tooltip(p, self.hover_point, self.hover_tooltip)
            return bool(p.end()) if owns_painter else True

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
        if not self.economy_stage_active():
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
        if self.hover_tooltip and not self.gui_visible:
            self.draw_tooltip(p, self.hover_point, self.hover_tooltip)

        if not include_interactive:
            return bool(p.end()) if owns_painter else True

        self.paint_clickgui_overlay(
            p,
            width,
            height,
            force_economy_compat=force_economy_compat,
        )
        self.draw_student_frontend(p, width, height)
        self.draw_escape_menu(p, width, height)
        return bool(p.end()) if owns_painter else True

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
        held_tab_list = self.student_ui.visible and self.student_ui.panel=="damage"
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
        if armor_module and armor_module.enabled:
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
        if energy_module and energy_module.enabled and self._vision_source == "Mock":
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
        if minecraft_active and self.setting_bool(hud, "Module List", True):
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
        self.draw_hero_deploy_interface(p, width, height, now)
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
        device = self.gpu_compositor.paint_device("target")
        if device is None:
            return False
        device.setDevicePixelRatio(ratio)
        try:
            self.gpu_compositor.clear_current(functions, transparent=True)
            self.begin_gpu_quick_action_wheel_collection()
            painter = QPainter(device)
            if self.match_chat.input_open:
                self.draw_match_chat(painter, width, height, now=started_at)
            if self.quick_action_wheel.visible:
                self.draw_quick_action_wheel(painter, width, height)
            self.draw_student_frontend(painter, width, height)
            self.draw_escape_menu(painter, width, height)
            painter.end()
            self.flush_gpu_quick_action_wheel(functions, width, height, ratio)
            self.gpu_compositor.last_dynamic_ms = (
                time.perf_counter() - started_at
            ) * 1000.0
            return True
        finally:
            self.gpu_compositor.release_layer("target")

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
        if self.gui_visible or gui_alpha > 0.01:
            # Drop HUD/map hit targets while ClickGUI owns the pointer.
            self.hotspots.clear()
            if self.active_clickgui_mode() != PRIMARY_GUI_MODE:
                p.fillRect(QRectF(0, 0, width, height), with_alpha(THEME.dim, gui_alpha))
            click_gui = self.registry.get("ClickGui")
            direct_gpu = self.setting_mode(click_gui, "Render Backend", "Unified GPU") == "Unified GPU" and self.clickgui_gpu.available
            if direct_gpu:
                if gpu_commands is None:
                    commands = self.build_gpu_clickgui(width, height, gui_alpha)
                else:
                    commands = gpu_commands
                    # Outer path already built layout + atlas once; restore the
                    # snapshot so clear() above does not leave zero hotspots.
                    if gpu_hotspots is not None:
                        self.hotspots.extend(gpu_hotspots)
                    else:
                        self.build_gpu_clickgui(width, height, gui_alpha)
                pixel_ratio = self.devicePixelRatioF()
                if not gpu_atlas_prepared:
                    # Direct compatibility callers do not have the outer
                    # compositor's frame-boundary preparation step.
                    self.clickgui_gpu.prepare_frame(commands, pixel_ratio)
                    gpu_atlas_prepared = True
                p.beginNativePainting()
                context = self.context()
                functions = context.extraFunctions() if context is not None else None
                # QPainter / target-layer uploads may leave non-zero texture units
                # dirty; reset unit 0 before sampling glyph atlases.
                if functions is not None:
                    self.gpu_compositor._restore_qt_texture_state(functions, clear_bindings=True)
                rendered = bool(
                    context
                    and functions is not None
                    and self.clickgui_gpu.render(
                        functions,
                        commands,
                        width,
                        height,
                        pixel_ratio,
                        atlas_prepared=gpu_atlas_prepared,
                    )
                )
                p.endNativePainting()
                if not rendered:
                    # Incomplete atlas snapshot must not permanently flip the user
                    # off Unified GPU; retry prepare+render once this frame.
                    if context is not None and functions is not None:
                        try:
                            self.clickgui_gpu.prepare_frame(commands, pixel_ratio)
                            self.gpu_compositor._restore_qt_texture_state(functions, clear_bindings=True)
                            p.beginNativePainting()
                            rendered = bool(
                                self.clickgui_gpu.render(
                                    functions,
                                    commands,
                                    width,
                                    height,
                                    pixel_ratio,
                                    atlas_prepared=True,
                                )
                            )
                            p.endNativePainting()
                        except Exception:
                            rendered = False
                    if not rendered:
                        backend = click_gui.get_setting("Render Backend") if click_gui else None
                        if backend:
                            backend.value = "Compatibility Painter"
                        direct_gpu = False
            if not direct_gpu:
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

        if (
            self.binding_module is not None
            and self.active_clickgui_mode() in LEGACY_GUI_MODES
            and not (gui_alpha > 0.01 and self.setting_mode(self.registry.get("ClickGui"), "Render Backend", "Unified GPU") == "Unified GPU" and self.clickgui_gpu.available)
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
        if self.hero_deploy_video_cut_active():
            self.open_hero_deploy_interface()
            self.toast("Hero deploy console is locked until exit sync")
            return
        if self.economy_phase != EconomyScenePhase.CLOSED:
            self._economy_pending_overlay = OverlayKind.CHAT
            if self.economy_phase != EconomyScenePhase.CLOSING:
                self.close_economy_workspace(play_sound=False)
            return
        if self.hero_deploy_ui.visible:
            self.close_hero_deploy_interface(force=True)
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
        if self.hero_deploy_video_cut_active():
            return False
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
        failures = []

        def cleanup(label, action):
            try:
                action()
            except Exception as exc:
                failures.append(f"{label}: {exc}")

        # A save or one broken resource must never prevent the other safety
        # and teardown actions. Stop the timer and outputs before disk/GL work.
        try:
            cleanup("timer", self.timer.stop)
            if hasattr(self, "_gpu_prewarm_timer"):
                cleanup("GPU warmup timer", self._gpu_prewarm_timer.stop)
            cleanup("control lock", lambda: self.backend.lock_control("window closing"))
            self.control.mock_output = bool(self.backend.mqtt.mock_output)
            cleanup("control release", lambda: self.control.release_all(self.state, self.registry))
            cleanup("emergency stop", self.backend.trigger_estop)
            cleanup("backend", self.backend.shutdown)
            cleanup("configuration", lambda: self.flush_auto_save(force=True))
            try:
                has_context = self.context() is not None
            except Exception as exc:
                has_context = False
                failures.append(f"GL context: {exc}")
            if has_context:
                try:
                    self.makeCurrent()
                    for name in ("clickgui_gpu", "vision_wire_gpu", "island_icon_gpu", "quick_action_wheel_gpu", "gpu_compositor", "startup_gpu", "hud_video_gpu"):
                        cleanup(name, getattr(self, name).release)
                except Exception as exc:
                    failures.append(f"GL context: {exc}")
                finally:
                    cleanup("GL done", self.doneCurrent)
            for name, method in (("startup_wallpaper", "release"), ("camera_worker", "stop"), ("local_vision", "stop"), ("tongji_receiver", "stop"), ("tongji_bridge", "stop")):
                cleanup(name, getattr(getattr(self, name), method))
        finally:
            if failures:
                self.state.push_event("Exit cleanup warnings: " + "; ".join(failures))
            super().closeEvent(event)

    def focusOutEvent(self, event) -> None:
        """A lost foreground window is an unconditional control epoch break."""

        self.student_ui.dragging = ""
        try:
            self.control.mock_output = bool(self.backend.mqtt.mock_output)
            self.control.release_all(self.state, self.registry)
            self.backend.mqtt.publish_estop_zero()
            self.backend.lock_control("window focus lost")
            self.control.clear_pending()
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
        size_setting = module.get_setting("Size") if module else None
        size = float(size_setting.value if size_setting else 18)
        cx = width / 2
        cy = height / 2
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
        self.clickgui_gpu.render(
            functions,
            commands,
            width,
            height,
            ratio,
            atlas_prepared=True,
        )

    def flush_gpu_island_icons(self, functions, width: int, height: int, ratio: float) -> None:
        self._collect_gpu_island_icons = False
        if not self._gpu_island_icon_commands or not self.island_icon_gpu.available:
            self._gpu_island_icon_commands.clear()
            return
        self.island_icon_gpu.render(
            functions,
            self._gpu_island_icon_commands,
            width,
            height,
            ratio,
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
            return
        if labels.commands and self.clickgui_gpu.available:
            self.clickgui_gpu.prepare_frame(labels, ratio)
            self.clickgui_gpu.render(
                functions,
                labels,
                width,
                height,
                ratio,
                atlas_prepared=True,
            )

    def _client_asset_path(self, *parts: str) -> Path:
        """Resolve assets under rm_custom_client/ regardless of workspace root."""
        primary = self.paths.resource_root.joinpath(*parts)
        if primary.exists():
            return primary
        return self.paths.app_root.joinpath(*parts)

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
        menu_x, menu_y, button_w, button_h, gap = self.liquid_menu_layout(width, height, 3, scale)
        is_match = selected_page == "match"
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
            for index, key in enumerate(MATCH_MODE_KEYS):
                config = MATCH_MODES[key]
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
        profiles = profiles_for_match(self.match_mode_manager.current.key)
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

        profile = profile_by_key(self.match_mode_manager.current.key, selected_key)
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
        module = self.registry.get("QuickActionWheel")
        if module is None:
            return
        slot_names = (
            "Up",
            "Upper Right",
            "Right",
            "Lower Right",
            "Down",
            "Lower Left",
            "Left",
            "Upper Left",
        )
        action_by_label = {label: action for action, label in ACTION_LABELS.items()}
        actions: list[str] = []
        for index, name in enumerate(slot_names):
            setting = module.get_setting(name)
            label = str(setting.value) if setting is not None else ACTION_LABELS[DEFAULT_ACTIONS[index]]
            actions.append(action_by_label.get(label, DEFAULT_ACTIONS[index]))
        self.quick_action_wheel.configure(
            sector_count=int(self.setting_mode(module, "Sectors", "8")),
            radius=self.get_setting_number(module, "Radius", 118.0),
            dead_zone=self.get_setting_number(module, "Dead Zone", 38.0),
            show_labels=self.setting_bool(module, "Show Labels", True),
            label_style=self.setting_mode(module, "Label Style", "Action"),
            icon_style=self.setting_mode(module, "Icon Style", "Initials"),
            actions=actions,
        )

    def apply_quick_action_wheel_to_settings(self) -> None:
        module = self.registry.get("QuickActionWheel")
        if module is None:
            return
        values: dict[str, Any] = {
            "Sectors": str(self.quick_action_wheel.sector_count),
            "Radius": self.quick_action_wheel.radius,
            "Dead Zone": self.quick_action_wheel.dead_zone,
            "Show Labels": self.quick_action_wheel.show_labels,
            "Label Style": self.quick_action_wheel.label_style,
            "Icon Style": self.quick_action_wheel.icon_style,
        }
        slot_names = (
            "Up",
            "Upper Right",
            "Right",
            "Lower Right",
            "Down",
            "Lower Left",
            "Left",
            "Upper Left",
        )
        for index, name in enumerate(slot_names):
            values[name] = ACTION_LABELS.get(
                self.quick_action_wheel.actions[index],
                ACTION_LABELS[DEFAULT_ACTIONS[index]],
            )
        for name, value in values.items():
            setting = module.get_setting(name)
            if setting is not None:
                setting.value = value

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
            layer.translate(-rect.left(), -rect.top())
            build(layer, QRectF(rect))
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
            # 缓存层异常时立即退回直接绘制，交互界面不能因显卡兼容性失效。
            setting = module.get_setting("Render Backend") if module else None
            if setting is not None:
                setting.value = "Compatibility Painter"
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


# ----------------------------------------------------------------------
# 阶段 4 窗口壳收编（REFACTOR_BLUEPRINT §5 阶段 4 / 目标子包结构）：
# main()（QApplication GL 装配 + 创建窗口）已机械迁至
# rm_client/ui/polar/app_entry.py（语句序列零修改），此处 re-export 维持
# §5.6 模块级冻结符号 rm_client.ui.polar_app.main 的兼容面——外部触点
# main.py:59 与 rm_client/app.py:655、tests/api_freeze_test.py 模块符号
# 快照（kind=function / sig="main"）均不受影响。模块级函数 __qualname__
# 恒为 "main"，无需钉回。
#
# 终态决策（方案 A，保守）：PolarRMWindow 与 D0-D3（构造/生命周期、主循环
# tick 编排、GPU 帧编排/FBO 层管理、paint_overlay CPU 绘制总线）保留在
# 本文件原位，不做 WindowShellMixin 化——它们即窗口壳的核心编排（蓝图
# 阶段 4 对最终形态的定义如此），且 __init__ 是状态 schema、构造期调用
# 多个 Mixin 方法，整体搬移属纯搬运高风险零收益。论证全文见
# app_entry.py 模块 docstring。
# ----------------------------------------------------------------------
from .polar.app_entry import main  # noqa: E402  （阶段 4 兼容 re-export）

