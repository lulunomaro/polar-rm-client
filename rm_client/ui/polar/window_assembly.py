"""Typed construction of window services from explicit paths and callbacks.

These factories do not receive a window, install backend observers, start
camera/network/serial workers, or activate a scenario. Local asset loading
remains with each service. The composition root keeps activation ordering and
exposes historical component aliases.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Qt

from ...calibration import CameraCalibrationManager
from ...kill_fx import KillFxRenderer
from ...runtime_paths import RuntimePaths
from ...startup_wallpaper import StartupWallpaperManager
from ...student_ui import StudentUiState
from ...tongji_bridge import GimbalCommandBridge, TongjiVisionReceiver
from ...tongji_yolo import LocalAlignedVision
from ...ui_audio import UiAudioManager
from ...vision import CompositeVisionPipeline, TongjiVisionBridge
from ..animation import AnimationBank, GuiMotionController
from ..clickgui_gpu import ClickGuiGpuRenderer
from ..fonts import FontBook
from ..gpu_frame_compositor import GpuFrameCompositor
from ..gui_cache import ClickGuiLayerCache
from ..hero_deploy import HeroDeployInterfaceState, HeroDeployRenderer
from ..island_icon_gpu import IslandIconGpuRenderer
from ..jello_clickgui import JelloClickGuiRenderer
from ..liquid_glass import LiquidGlassRenderer
from ..minecraft_chat import MinecraftServerChatRenderer
from ..minecraft_hud import MinecraftCombatHudRenderer
from ..quick_action_wheel_gpu import QuickActionWheelGpuRenderer
from ..startup_gl import StartupGpuCompositor
from ..student_panels import StudentPanelRenderer
from ..vision_wire_gpu import VisionWireGpuRenderer


def prepare_runtime_paths() -> RuntimePaths:
    paths = RuntimePaths.discover()
    paths.ensure_writable_dirs()
    paths.seed_default_config()
    return paths


def resolve_client_asset(paths: RuntimePaths, *parts: str) -> Path:
    primary = paths.resource_root.joinpath(*parts)
    return primary if primary.exists() else paths.app_root.joinpath(*parts)


@dataclass(frozen=True)
class FeedbackAssets:
    kill_fx: KillFxRenderer
    ui_audio: UiAudioManager


def create_feedback_assets(paths: RuntimePaths) -> FeedbackAssets:
    return FeedbackAssets(
        KillFxRenderer(paths.asset("kill_fx", "gd656")),
        UiAudioManager(paths.asset("ui_sounds"), custom_root=paths.user_data_root / "ui_sounds" / "custom"),
    )


@dataclass(frozen=True)
class VisionServices:
    calibration: CameraCalibrationManager
    receiver: TongjiVisionReceiver
    gimbal_commands: GimbalCommandBridge
    composite: CompositeVisionPipeline
    local: LocalAlignedVision
    bridge: TongjiVisionBridge


def create_vision_services(*, workspace: Path, calibration_profiles: Path, model_path: Path) -> VisionServices:
    calibration = CameraCalibrationManager(workspace, profile_dir=calibration_profiles)
    calibration.load("polar_webcam")
    return VisionServices(calibration, TongjiVisionReceiver(), GimbalCommandBridge(),
                          CompositeVisionPipeline(), LocalAlignedVision(model_path), TongjiVisionBridge())


def create_startup_wallpaper(paths: RuntimePaths) -> StartupWallpaperManager:
    return StartupWallpaperManager(
        paths.workspace_root,
        assets_dir=paths.asset("wallpapers"),
        user_dir=paths.user_data_root / "wallpapers",
        pkg_dir=paths.resource_root / "3000925581",
        cache_dir=paths.user_data_root / ".wallpaper_cache" / "3000925581",
    )


@dataclass(frozen=True)
class PresentationServices:
    fonts: FontBook
    animation: AnimationBank
    motion: GuiMotionController
    gui_cache: ClickGuiLayerCache
    clickgui_gpu: ClickGuiGpuRenderer
    jello: JelloClickGuiRenderer
    minecraft_hud: MinecraftCombatHudRenderer
    student_state: StudentUiState
    student_renderer: StudentPanelRenderer
    chat_renderer: MinecraftServerChatRenderer
    hero_state: HeroDeployInterfaceState
    hero_renderer: HeroDeployRenderer


def create_presentation_services(paths: RuntimePaths) -> PresentationServices:
    fonts = FontBook()
    fonts.load_from_project_assets(paths.resource_root)
    animation = AnimationBank()
    motion = GuiMotionController(animation)
    return PresentationServices(
        fonts, animation, motion, ClickGuiLayerCache(), ClickGuiGpuRenderer(),
        JelloClickGuiRenderer(paths.asset("jello")),
        MinecraftCombatHudRenderer(paths.asset("minecraft_hud")),
        StudentUiState(), StudentPanelRenderer(paths.asset("minecraft_hud")),
        MinecraftServerChatRenderer(paths.asset("minecraft_hud")),
        HeroDeployInterfaceState(), HeroDeployRenderer(),
    )


@dataclass(frozen=True)
class GpuServices:
    vision_wire: VisionWireGpuRenderer
    island_icon: IslandIconGpuRenderer
    quick_action_wheel: QuickActionWheelGpuRenderer
    compositor: GpuFrameCompositor
    liquid_glass: LiquidGlassRenderer
    startup: StartupGpuCompositor
    hud_video: StartupGpuCompositor


def create_gpu_services() -> GpuServices:
    # Allocation and shader initialization remain in the owner's initializeGL.
    return GpuServices(VisionWireGpuRenderer(), IslandIconGpuRenderer(),
                       QuickActionWheelGpuRenderer(), GpuFrameCompositor(),
                       LiquidGlassRenderer(), StartupGpuCompositor(), StartupGpuCompositor())


def create_warmup_timer(parent: QObject, callback: Callable[[], None]) -> QTimer:
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.timeout.connect(callback)
    return timer


def create_frame_timer(parent: QObject, callback: Callable[[], None]) -> QTimer:
    """Connect an inert event source so its owner can publish it before admission."""
    timer = QTimer(parent)
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.timeout.connect(callback)
    return timer


def start_frame_timer(parent: QObject, callback: Callable[[], None], interval_ms: int) -> QTimer:
    """Compatibility factory for callers that do not need staged admission."""
    timer = create_frame_timer(parent, callback)
    timer.start(interval_ms)
    return timer
