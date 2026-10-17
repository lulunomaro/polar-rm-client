import os
import math
from pathlib import Path
import socket
import struct
import threading
import sys
from tempfile import TemporaryDirectory
import time
from typing import Any
import unittest

SMOKE_SKIPS: list[str] = []

# UI regression tests use deterministic synthetic/backend frames. Never probe
# a developer's physical webcam or load the local OpenVINO worker implicitly.
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
# Do not start QtMultimedia FFmpeg workers in an offscreen test process. Audio
# routing is covered with a deterministic fake pool below; real media preload
# remains enabled in the application and packaged desktop smokes.
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

from rm_client.config_store import ClientConfigStore, apply_registry_config, export_client_config
from rm_client.combat_events import CombatEvent, OfficialMatchFeed, VisionKillDetector
from rm_client.combat_hud import (
    CombatHudAnimator,
    aim_target,
    approach_bar,
    approach_jar,
    ghost_follow,
    meter_settled,
    stage_drop_pct,
    HEAT_PER_SHOT,
    MockHeatStageSimulator,
    ring_end,
)
from rm_client.kill_fx import KillFxLayout, KillFxRenderer, PRESET_ELEMENTS, VALORANT_STYLES
from rm_client.match_chat import ChatEventAdapter, MatchChatController
from rm_client.match_modes import MATCH_MODES, MatchModeManager
from rm_client.model import (
    GUI_MODES,
    LEGACY_GUI_MODES,
    PRIMARY_GUI_MODE,
    HUD_MODES,
    MODULE_LIST_RENDERER_KEYS,
    MODULE_LIST_STYLES,
    TACTICAL_MAP_STATUS_DISPLAYS,
    GuiModeManager,
    Module,
    ModuleRegistry,
    RobotState,
    build_default_registry,
    normalize_gui_mode,
)
from rm_client.economy import (
    EconomyCommandBridge,
    available_actions,
    encode_air_support_command,
    encode_common_command,
)
from rm_client.robot_profiles import apply_profile_to_state, profile_by_key, profiles_for_match, resolve_performance
from rm_client.protocol import RefereeFrameCodec
from rm_client.hero_lob import HeroLobPlanner
from rm_client.auto_spin import ProjectileHitSpinController
from rm_client.native_viewer_telemetry import (
    find_h264_sps_offset,
    parse_vehicle_telemetry,
    parse_video_0310_chunk,
)
from rm_client.backend.manager import BackendManager
from rm_client.backend import protocol as backend_proto
from rm_client.backend.readiness import audit_backend
from rm_client.backend.serial_backend import RmSerialBackend
from rm_client.scenarios import ScenarioManager
from rm_client.startup_wallpaper import PkgVirtualFileSystem, StartupWallpaperManager
from rm_client.transports import FrameAssembler, OfficialMqttBridge, SerialBridgeStub, UDPVideoReceiver, VideoPipeline
from rm_client.tongji_bridge import (
    DEFAULT_PORT,
    TongjiVisionReceiver,
    build_tongji_packet,
    parse_tongji_packet,
)
from rm_client.ui_audio import UiAudioManager
from rm_client.ui.animation import (
    AnimationBank,
    Easings,
    GuiMotionController,
    GuiTransitionTimeline,
    IslandContainerState,
    MapIslandState,
    ReversibleExpoTimeline,
    step_damped_spring,
)
from rm_client.ui.gui_cache import ClickGuiLayerCache
from rm_client.ui.gpu_frame_compositor import compose_combined_backdrop_pixel
from rm_client.ui.clickgui_gpu import ClickGuiGpuRenderer, SmallTextPolicy
from rm_client.ui.island_icon_gpu import island_icon_coverage
from rm_client.ui.layout import HudAnchorStack
from rm_client.ui.overlay_coordinator import OverlayCoordinator, OverlayKind
from rm_client.ui.quick_action_wheel import DEFAULT_ACTIONS, QuickActionWheelState
from rm_client.ui.economy_workspace import EconomyIslandState, EconomyWorkspaceLayer, adaptive_backdrop_dim
from rm_client.ui.minecraft_hud import MinecraftCombatHudRenderer, vanilla_gui_scale
from rm_client.ui.jello_locale import (
    LANGUAGE_CHINESE,
    LANGUAGE_ENGLISH,
    category_label as jello_category_label,
    module_label as jello_module_label,
    option_label as jello_option_label,
    setting_label as jello_setting_label,
)
from rm_client.ui.hero_deploy import HeroDeployInterfaceState, HeroDeployRenderer
from rm_client.ui.startup_gl import TextureOrigin, normalize_image_for_texture
from rm_client.ui.vision_motion import (
    AngleRenderTrack,
    ScalarRenderTrack,
    TargetHudPresenceState,
    VisionRenderInterpolator,
)
from rm_client.vision import ArmorObservation, VisionLossEvidence, VisionResult, PACKET_FRAME, PACKET_HEADER, PACKET_RESULT, PROTOCOL_MAGIC, PROTOCOL_VERSION, TongjiVisionBridge


def _build_settled_window(window_type, app):
    """Finish the real startup profile before testing normal UI interactions."""
    window = window_type()
    try:
        settle_window_lifecycle(window, app)
    except BaseException:
        close_window_safely(window, app)
        raise
    return window


def test_registry() -> None:
    registry = build_default_registry()
    click_gui = registry.get("ClickGui")
    assert click_gui is not None
    assert GUI_MODES == [PRIMARY_GUI_MODE]
    assert click_gui.get_setting("Mode") is None
    old_group = click_gui.get_setting("Old ClickGUIs")
    assert old_group is not None and old_group.kind == "action_group"
    assert old_group.options == LEGACY_GUI_MODES
    assert old_group.value is None
    assert click_gui.keybind == "RightShift"
    assert click_gui.get_setting("Render Backend").options == ["Unified GPU", "Compatibility Painter"]
    assert click_gui.get_setting("Render Backend").value == "Unified GPU"
    assert click_gui.get_setting("Scale").max_value == 2.0
    assert click_gui.get_setting("Scale").step == 0.05
    assert click_gui.get_setting("Language").value == LANGUAGE_CHINESE
    assert click_gui.get_setting("Language").options == [LANGUAGE_ENGLISH, LANGUAGE_CHINESE]
    hero_lob = registry.get("HeroLob")
    assert hero_lob is not None
    assert hero_lob.get_setting("Muzzle Length").value == 0.35
    assert hero_lob.get_setting("Max Roll").value == 8.0
    assert hero_lob.get_setting("Mock Output").value is True
    armor_esp_styles = registry.get("Armor3DESP").get_setting("Style").options
    assert armor_esp_styles[0] == "JelloShadow"
    assert "PolarMechanicalWire" not in armor_esp_styles
    assert registry.get("ArmorAutoAim") is not None
    assert registry.get("EnergyAutoAim") is not None
    assert registry.get("ArmorAutoAim").get_setting("Lock Style") is not None
    assert registry.get("EnergyAutoAim").get_setting("Rune Type") is not None
    assert registry.get("EnergyAutoAim").get_setting("Lock Style") is not None
    assert registry.get("SpinMode") is not None
    hero_lob = registry.get("HeroLob")
    assert hero_lob is not None and hero_lob.enabled is False
    assert hero_lob.keybind == "K"
    assert hero_lob.get_setting("Arc").options == ["High", "Low"]
    assert hero_lob.get_setting("Arm Assisted Lob").kind == "action"
    assert hero_lob.get_setting("Open Deploy Interface").kind == "action"
    auto_spin = registry.get("AutoSpinOnHit")
    assert auto_spin is not None and auto_spin.enabled is False
    assert auto_spin.get_setting("Require Referee Link").value is True
    assert auto_spin.get_setting("Duration").value == 2.5
    assert registry.get("SpinMode").get_setting("Mock Output").value is True
    mc_hud = registry.get("MinecraftCombatHUD")
    assert mc_hud is not None and mc_hud.enabled is False
    assert mc_hud.get_setting("GUI Scale").value == "Auto"
    assert mc_hud.get_setting("Scale Multiplier").value == 1.0
    assert mc_hud.get_setting("Scale Multiplier").min_value == 0.5
    assert mc_hud.get_setting("Scale Multiplier").max_value == 2.0
    assert mc_hud.get_setting("Scale Multiplier").step == 0.05
    assert mc_hud.get_setting("Slot 9").value == "ClickGui"
    assert "HeroLob" in mc_hud.get_setting("Slot 1").options
    assert registry.get("RobotOverlay") is not None
    direction_indicator = registry.get("ChassisGimbalIndicator")
    assert direction_indicator is not None
    assert direction_indicator.enabled is True
    assert direction_indicator.get_setting("Style").options == ["Minimal", "Tank", "Compass"]
    assert direction_indicator.get_setting("Show Angle") is not None
    assert direction_indicator.get_setting("Show Mode") is not None
    assert direction_indicator.get_setting("Size") is not None
    assert direction_indicator.get_setting("Size").value == 56
    assert direction_indicator.get_setting("Smooth") is not None
    combat_target_hud = registry.get("CombatTargetHUD")
    assert combat_target_hud is not None and combat_target_hud.enabled is True
    assert combat_target_hud.category == "Render"
    assert combat_target_hud.get_setting("Visual").value == "Clear"
    assert combat_target_hud.get_setting("Visual").options == ["Classic", "Clear", "Enhanced"]
    assert combat_target_hud.get_setting("Show Bars").value is False
    assert combat_target_hud.get_setting("Show Status Line").value is True
    ui_scale = registry.get("UIScale")
    assert ui_scale is not None
    assert ui_scale.get_setting("Auto Scale") is not None
    assert ui_scale.get_setting("High Res Optimize") is not None
    assert ui_scale.get_setting("HUD Scale") is not None
    assert ui_scale.get_setting("GUI Scale") is not None
    assert ui_scale.get_setting("Animation Speed") is not None
    assert all(hasattr(module, "keybind") for module in registry.modules)
    scenario = registry.get("Scenario")
    assert scenario is not None
    assert scenario.get_setting("Mode").value == "LabDebug"
    assert scenario.get_setting("Startup Style").options == ["LiquidMenu", "PolarLauncher"]
    assert scenario.get_setting("Startup Style").value == "LiquidMenu"
    startup_visual = registry.get("StartupVisual")
    assert startup_visual is not None
    assert startup_visual.enabled is True
    assert startup_visual.get_setting("Background Mode").options == ["PkgScene", "VideoParallax", "ParticleGrid", "Static"]
    assert startup_visual.get_setting("Background Mode").value == "VideoParallax"
    assert startup_visual.get_setting("Wallpaper Profile").value == "BlackHole"
    assert startup_visual.get_setting("Wallpaper Profile").options == ["BlackHole", "TexasMP4", "VendingMachine4K", "Default3000925581Pkg", "ParticleGrid"]
    assert startup_visual.get_setting("Motion Quality").options == ["Smooth60", "Balanced30", "Source"]
    assert startup_visual.get_setting("Motion Quality").value == "Smooth60"
    assert startup_visual.get_setting("Glass Style").options == ["LiquidGlass", "Legacy"]
    assert startup_visual.get_setting("Glass Style").value == "LiquidGlass"
    assert startup_visual.get_setting("Glass Strength").value == 1.0
    assert startup_visual.get_setting("Import Wallpaper").kind == "action"
    assert startup_visual.get_setting("Reset Wallpaper").kind == "action"
    match_mode = registry.get("MatchMode")
    assert match_mode is not None
    assert match_mode.get_setting("Mode").value == "RMUC7v7"
    assert match_mode.get_setting("Alliance") is not None
    alliance = registry.get("AllianceProfile")
    assert alliance is not None
    assert alliance.get_setting("Side").value == "Red"
    tactical_map = registry.get("TacticalMap")
    assert tactical_map is not None
    assert tactical_map.keybind == "Tab"
    assert tactical_map.get_setting("Map Size") is not None
    assert tactical_map.get_setting("Chrome").options == ["Minimal", "Thin", "Full"]
    assert tactical_map.get_setting("Status Display").options == TACTICAL_MAP_STATUS_DISPLAYS
    assert tactical_map.get_setting("Status Display").value == "LiquidAuto"
    assert tactical_map.get_setting("Palette").options == ["RM Tactical", "Liquid Neutral"]
    for display in ("LiquidAuto", "LiquidBars", "LiquidCards"):
        assert display in TACTICAL_MAP_STATUS_DISPLAYS
    assert tactical_map.get_setting("Official Map") is not None
    hud = registry.get("HUD")
    assert hud is not None
    assert hud.get_setting("Restore Compact HUD").kind == "action"
    liquid_map = registry.get("LiquidTacticalMap")
    assert liquid_map is not None
    assert liquid_map.enabled is True
    assert liquid_map.get_setting("Pointer Style").options == ["Triangle", "Wedge", "Dot"]
    assert liquid_map.get_setting("Entity Color").options == ["Team", "Distance", "Health"]
    assert liquid_map.get_setting("Show Labels") is not None
    assert liquid_map.get_setting("Show Values") is not None
    assert liquid_map.get_setting("Dock Animation").options == ["Fly", "Flip", "Fade"]
    tongji = registry.get("TongjiVisionBridge")
    assert tongji is not None and tongji.enabled is True
    assert tongji.get_setting("Vision Source").options == ["Local", "Tongji", "Auto", "Mock"]
    assert tongji.get_setting("Vision Kill Sensitivity").options == ["Lab Responsive", "Balanced", "Match Reliable"]
    assert tongji.get_setting("Vision Kill Sensitivity").value == "Lab Responsive"
    assert tongji.get_setting("UDP Port").value == DEFAULT_PORT
    assert tongji.get_setting("Local Device").options == ["AUTO", "GPU", "CPU"]
    assert registry.get("CameraCalibration").get_setting("Capture Chessboard").kind == "action"
    armor_esp = registry.get("Armor3DESP")
    assert armor_esp is not None
    assert armor_esp.get_setting("Style").value == "JelloShadow"
    assert armor_esp.get_setting("Style").options == ["JelloShadow", "Wireframe3D", "PolarOutline2D", "Prediction Rack"]
    assert armor_esp.get_setting("Prediction Labels").value is False
    assert armor_esp.get_setting("Renderer").options == ["GPU Auto", "CPU"]
    config_manager = registry.get("ConfigManager")
    assert config_manager is not None
    assert config_manager.get_setting("Profile").value == "default"
    assert config_manager.get_setting("Save Current").kind == "action"
    kill_fx = registry.get("EliminationFX")
    assert kill_fx is not None and kill_fx.enabled is True
    assert kill_fx.get_setting("Preset").value == "Default Scrolling"
    assert "Valorant Prime" in kill_fx.get_setting("Preset").options
    assert "Valorant RGX V3" in kill_fx.get_setting("Preset").options
    assert kill_fx.get_setting("Element Visibility").options == ["Preset", "Full HUD", "Icons Only"]
    assert kill_fx.get_setting("Anchor Policy").options == ["Combat Focus", "Original Bottom", "Manual"]
    assert kill_fx.get_setting("Anchor Policy").value == "Combat Focus"
    assert kill_fx.get_setting("Responsive Scale").options == ["Auto", "Fixed"]
    assert kill_fx.get_setting("Scale").max_value == 2.0
    assert kill_fx.get_setting("Evidence Mode").value == "Auto"
    audio = registry.get("AudioFeedback")
    assert audio is not None and audio.enabled is True
    assert audio.get_setting("Enabled").value is True
    assert audio.get_setting("Module Toggle Sounds").value is True
    assert audio.get_setting("Sound Theme").value == "Polar UI"
    assert audio.get_setting("Sound Theme").options == ["Custom", "Polar UI", "Polar Classic", "LiquidBounce"]
    for name in ("Custom Enable Sound", "Custom Disable Sound", "Custom Invalid Sound"):
        assert audio.get_setting(name).value == "No Sound"
    for name in ("Import Enable Sound", "Import Disable Sound", "Import Invalid Sound"):
        assert audio.get_setting(name).kind == "action"
    assert audio.get_setting("UI Volume").value == 45


def test_audio_feedback_and_kill_sound_mapping() -> None:
    sound_root = ROOT / "assets" / "ui_sounds" / "liquidbounce"
    for name in ("click-1.ogg", "click-2.ogg", "click-3.ogg"):
        assert (sound_root / name).stat().st_size > 0
    polar_root = ROOT / "assets" / "ui_sounds" / "polar_classic"
    for name in ("module_on.wav", "module_off.wav"):
        assert (polar_root / name).stat().st_size > 0
    polar_ui_root = ROOT / "assets" / "ui_sounds" / "polar_ui"
    for name in ("module_toggle.wav", "action_success.wav", "panel_transition.wav"):
        assert (polar_ui_root / name).stat().st_size > 0
    jello_root = ROOT / "assets" / "ui_sounds" / "jello"
    for name in UiAudioManager.JELLO_SOUNDS:
        asset = ROOT / "assets" / "ui_sounds" / name
        assert asset.is_file() and asset.stat().st_size > 0

    class FakePool:
        def __init__(self) -> None:
            self.played: list[tuple[str, float]] = []

        def play(self, name: str, volume: float) -> None:
            self.played.append((name, volume))

        def preload(self, _names) -> None:
            pass

    manager = object.__new__(UiAudioManager)
    manager.pool = FakePool()
    manager.root = ROOT / "assets" / "ui_sounds"
    manager.custom_dir = manager.root / "custom"
    manager.module_toggled(True, "config", active=True, volume=45)
    assert manager.pool.played == []
    manager.module_toggled(True, "clickgui", active=True, volume=45, theme="Custom")
    assert manager.pool.played == []
    manager.module_toggled(
        True, "clickgui", active=True, volume=45, theme="Custom",
        custom_sounds={"enable": "custom/enable.wav"},
    )
    assert manager.pool.played[-1] == ("custom/enable.wav", 45)
    manager.module_toggled(True, "clickgui", active=True, volume=45, theme="Polar Classic")
    manager.module_toggled(False, "keybind", active=True, volume=45, theme="Polar Classic")
    assert manager.pool.played[-2:] == [("polar_classic/module_on.wav", 45), ("polar_classic/module_off.wav", 45)]
    manager.preview("invalid", 30, theme="Polar Classic")
    assert manager.pool.played[-1] == ("polar_classic/module_off.wav", 18)
    manager.module_toggled(True, "clickgui", active=True, volume=45, theme="LiquidBounce")
    assert manager.pool.played[-1] == ("liquidbounce/click-2.ogg", 45)
    manager.module_toggled(False, "clickgui", active=True, volume=45, theme="Polar UI")
    assert manager.pool.played[-1] == ("polar_ui/module_toggle.wav", 45)
    manager.gui_feedback("expand", 45, "Polar UI")
    assert manager.pool.played[-1] == ("polar_ui/panel_transition.wav", 45)
    manager.jello_module_toggled(True, active=True, volume=45)
    manager.jello_module_toggled(False, active=True, volume=45)
    assert manager.pool.played[-2:] == [
        ("jello/activate.mp3", 45),
        ("jello/deactivate.mp3", 45),
    ]
    before = list(manager.pool.played)
    manager.jello_module_toggled(True, active=False, volume=45)
    assert manager.pool.played == before
    with TemporaryDirectory() as temp:
        source = Path(temp) / "my-toggle.wav"
        source.write_bytes(b"RIFF-demo")
        manager.root = Path(temp) / "sounds"
        manager.custom_dir = manager.root / "custom"
        manager.custom_dir.mkdir(parents=True)
        imported = manager.import_custom_sound(source, "disable")
        assert imported.startswith("custom/disable-my-toggle")
        assert (manager.root / imported).is_file()

    registry = build_default_registry()
    toggles: list[tuple[str, bool, str]] = []
    registry.add_toggle_listener(lambda module, enabled, source: toggles.append((module.name, enabled, source)))
    target = registry.get("SpeedLimit")
    assert target is not None
    revision = registry.enabled_revision
    registry.toggle_module(target, source="clickgui")
    assert toggles == [("SpeedLimit", target.enabled, "clickgui")]
    assert registry.enabled_revision == revision + 1
    revision = registry.enabled_revision
    target.set_enabled(not target.enabled)
    assert registry.enabled_revision == revision + 1
    assert len(toggles) == 1  # direct writes retain the existing no-UI-event contract
    revision = registry.enabled_revision
    target.enabled = target.enabled
    assert registry.enabled_revision == revision
    target.enabled = not target.enabled
    assert registry.enabled_revision == revision + 1

    renderer = KillFxRenderer(ROOT / "assets" / "kill_fx" / "gd656")
    assert renderer._sound_name("NORMAL", 1, "Default Scrolling") == "killsound_df.ogg"
    assert renderer._sound_name("HEADSHOT", 1, "Battlefield 1") == "headshotkillsound_bf1.ogg"
    assert renderer._sound_name("NORMAL", 6, "Combo") == "combokillsound_6_cf.ogg"
    assert (renderer.sound_root / renderer._sound_name("NORMAL", 1, "Valorant Prime")).exists()


def test_gui_motion_reversal_and_layer_cache() -> None:
    bank = AnimationBank()
    motion = GuiMotionController(bank)
    motion.begin_frame(1.0)
    assert motion.value("reverse", 1.0, 0.30, Easings.ease_out_expo, initial=0.0) == 0.0
    motion.begin_frame(1.12)
    forward = motion.value("reverse", 1.0, 0.30, Easings.ease_out_expo)
    assert 0.0 < forward < 1.0
    motion.begin_frame(1.12)
    reversed_at_start = motion.value("reverse", 0.0, 0.30, Easings.ease_out_expo)
    assert abs(reversed_at_start - forward) < 0.001
    motion.begin_frame(1.28)
    reversed_later = motion.value("reverse", 0.0, 0.30, Easings.ease_out_expo)
    assert 0.0 <= reversed_later < forward
    assert motion.waterfall(0.45, 2, 6) < motion.waterfall(0.45, 0, 6)

    # 慢帧跨过动画终点时必须直接落到目标，不能从旧值重新启动。
    motion.begin_frame(2.0)
    assert motion.value("dropped_frame", 1.0, 0.20, initial=0.0) == 0.0
    motion.begin_frame(2.35)
    assert motion.value("dropped_frame", 1.0, 0.20) == 1.0

    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter

    cache = ClickGuiLayerCache()
    target = QImage(320, 160, QImage.Format.Format_ARGB32_Premultiplied)
    target.fill(QColor(0, 0, 0, 0))
    painter = QPainter(target)
    builds = [0]

    def build(layer: QPainter, rect: QRectF) -> None:
        builds[0] += 1
        layer.fillRect(rect, QColor(40, 80, 120, 180))

    rect = QRectF(30, 20, 160, 90)
    cache.draw(painter, rect, ("smoke",), build)
    cache.draw(painter, rect, ("smoke",), build)
    painter.end()
    assert builds == [1]


def test_vision_render_interpolation_and_presence() -> None:
    def armor(x: float) -> ArmorObservation:
        return ArmorObservation(
            armor_id=3,
            label="three",
            color="blue",
            confidence=0.95,
            points=((x, 80.0), (x + 40.0, 80.0), (x + 40.0, 104.0), (x, 104.0)),
            distance_m=2.0,
            depth_m=2.0,
            plate_id=0,
            target_id=103,
        )

    interpolator = VisionRenderInterpolator(max_extrapolation=0.05)
    first = VisionResult(
        sequence=1,
        timestamp_ns=1,
        source_width=640,
        source_height=360,
        detections=(armor(100.0),),
        aim_armor=armor(100.0),
        tracking_state="tracking",
    )
    second = VisionResult(
        sequence=2,
        timestamp_ns=2,
        source_width=640,
        source_height=360,
        detections=(armor(160.0),),
        aim_armor=armor(160.0),
        tracking_state="tracking",
    )
    assert interpolator.push(first, source="local", received_at=10.0)
    assert interpolator.push(second, source="local", received_at=10.1)
    sampled = interpolator.sample(10.1)
    assert sampled is not None and sampled.aim_armor is not None
    assert 100.0 < sampled.aim_armor.points[0][0] < 160.0

    lost = VisionResult(
        sequence=3,
        timestamp_ns=3,
        source_width=640,
        source_height=360,
        tracking_state="lost",
    )
    interpolator.push(lost, source="local", received_at=10.2)
    hidden = interpolator.sample(10.2)
    assert hidden is not None and not hidden.detections and hidden.aim_armor is None

    presence = TargetHudPresenceState(hold_seconds=10.0, appear_seconds=0.18, fade_seconds=0.26)
    assert presence.update(0.0, False).phase == "Hidden"
    assert presence.update(0.0, True).phase == "Appearing"
    assert presence.update(0.2, True).phase == "Visible"
    assert presence.update(0.21, False).phase == "Grace10s"
    assert presence.update(10.20, False).phase == "Grace10s"
    assert presence.update(10.22, False).phase == "Fading"
    assert presence.update(10.50, False).phase == "Hidden"
    recovered = presence.update(10.51, True)
    assert recovered.phase == "Appearing" and recovered.opacity >= 0.0

    angle = AngleRenderTrack()
    assert angle.sample(0.0, 179.0) == 179.0
    wrapped = angle.sample(0.05, -179.0, response=20.0)
    assert abs(wrapped) > 170.0

    scalar = ScalarRenderTrack()
    assert scalar.sample(0.0, 0.0) == 0.0
    value = scalar.sample(0.05, 100.0, response=20.0)
    assert 0.0 < value < 100.0


def test_kill_fx_combat_focus_layout_and_migration() -> None:
    layout_1080 = KillFxLayout.create(
        1920, 1080, policy="Combat Focus", user_scale=1.0, responsive=True,
        device_pixel_ratio=1.0, x_offset=0, y_offset=0,
    )
    assert abs(layout_1080.anchor.x() - 960.0) < 0.01
    assert abs(layout_1080.anchor.y() - 799.2) < 0.01
    assert abs(layout_1080.scale - 1.0) < 0.01

    layout_2k = KillFxLayout.create(
        2560, 1440, policy="Combat Focus", user_scale=1.0, responsive=True,
        device_pixel_ratio=1.0, x_offset=0, y_offset=0,
    )
    assert abs(layout_2k.anchor.x() - 1280.0) < 0.01
    assert abs(layout_2k.anchor.y() - 1065.6) < 0.01
    assert 1.49 <= layout_2k.scale <= 1.51

    native_2048 = KillFxLayout.create(
        2048, 1152, policy="Combat Focus", user_scale=1.0, responsive=True,
        device_pixel_ratio=1.0, x_offset=0, y_offset=0,
    )
    assert 1.49 <= native_2048.scale <= 1.51

    scaled_desktop = KillFxLayout.create(
        2048, 1152, policy="Combat Focus", user_scale=1.0, responsive=True,
        device_pixel_ratio=1.25, x_offset=0, y_offset=0,
    )
    assert 1.49 <= scaled_desktop.scale <= 1.51

    registry = build_default_registry()
    apply_registry_config(registry, {
        "modules": {
            "EliminationFX": {
                "settings": {
                    "Anchor Policy": "Safe Area",
                    "X Offset": -275,
                    "Y Offset": -20,
                }
            }
        }
    })
    effect = registry.get("EliminationFX")
    assert effect.get_setting("Anchor Policy").value == "Combat Focus"
    assert effect.get_setting("Responsive Scale").value == "Auto"
    assert effect.get_setting("X Offset").value == 0
    assert effect.get_setting("Y Offset").value == 0


def test_kill_fx_assets_and_dual_evidence() -> None:
    assets = ROOT / "assets" / "kill_fx" / "gd656"
    assert (assets / "textures" / "killicon_card_default_t.png").exists()
    assert (assets / "sounds" / "killsound_df.ogg").exists()
    renderer = KillFxRenderer(assets)
    assert renderer.texture_root.exists()
    detector = VisionKillDetector()
    lit = ArmorObservation(103, "three", "blue", 0.92, ((1.0, 1.0), (8.0, 1.0), (8.0, 4.0), (1.0, 4.0)), light_strength=0.8, light_state="lit", target_id=7)
    # 近距离反光可让像素采样仍偏亮；模型明确报 extinguish 时不应被阻塞。
    extinguish = ArmorObservation(103, "three", "extinguish", 0.94, ((1.0, 1.0), (8.0, 1.0), (8.0, 4.0), (1.0, 4.0)), light_strength=0.88, light_state="extinguished", target_id=7)
    detector.process([lit], alliance="Red", frame_luminance=0.4, frame_token=1, now=1.0)
    # 亮灯记录已经超过两秒，仍应保留；重复消费同一视觉帧不能累计确认次数。
    assert detector.process([extinguish], alliance="Red", frame_luminance=0.4, frame_token=2, now=3.2) == []
    assert detector.process([extinguish], alliance="Red", frame_luminance=0.4, frame_token=2, now=3.5) == []
    assert detector.process([extinguish], alliance="Red", frame_luminance=0.4, frame_token=3, now=3.6) == []
    event = detector.process([extinguish], alliance="Red", frame_luminance=0.4, frame_token=4, now=3.8)
    assert len(event) == 1 and event[0].source == "vision"
    assert event[0].detail == "vision_confirmed_lamp_off"
    assert detector.process([extinguish], alliance="Red", frame_luminance=0.4, frame_token=5, now=3.9) == []
    assert detector.is_suppressed(extinguish) is True
    detector.process([lit], alliance="Red", frame_luminance=0.4, frame_token=6, now=4.0)
    assert detector.is_suppressed(extinguish) is False
    feed = OfficialMatchFeed()
    feed.consume("GameStatus", b"\x08\x01", alliance="Red", local_robot_id=3)
    assert feed.is_fresh() is True
    assert feed.is_kill_data_fresh() is False
    events, _updates = feed.consume("Event", b"\x08\x01", alliance="Red", local_robot_id=3)
    assert len(events) == 1 and events[0].source == "referee"
    assert feed.is_kill_data_fresh() is True


def test_vision_kill_miss_after_lit() -> None:
    """灯灭后框消失：曾亮灯 + 短时丢检应确认击杀。"""
    detector = VisionKillDetector()
    dark_loss = lambda sequence: VisionLossEvidence(7, "three", 0.10, 0.80, True, False, False, sequence)
    lit = ArmorObservation(103, "three", "blue", 0.92, ((1.0, 1.0), (8.0, 1.0), (8.0, 4.0), (1.0, 4.0)), light_strength=0.8, light_state="lit", target_id=7)
    detector.process([lit], alliance="Red", frame_luminance=0.4, frame_token=1, now=1.0)
    assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=2, loss_evidence=dark_loss(2), now=1.20) == []
    assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=2, now=1.25) == []  # 同帧不重复计 miss
    assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=3, loss_evidence=dark_loss(3), now=1.30) == []
    assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=4, loss_evidence=dark_loss(4), now=1.35) == []
    events = detector.process([], alliance="Red", frame_luminance=0.4, frame_token=5, loss_evidence=dark_loss(5), now=1.50)
    assert events == []
    assert detector.last_status == "LOSS_NEEDS_DIM_HISTORY"
    assert detector.is_suppressed(lit) is False


def test_vision_kill_same_empty_snapshot_never_times_out() -> None:
    detector = VisionKillDetector()
    lit = ArmorObservation(103, "three", "blue", 0.92, ((100.0, 100.0), (108.0, 100.0), (108.0, 104.0), (100.0, 104.0)), light_strength=0.8, light_state="lit", target_id=7)
    detector.process([lit], alliance="Red", frame_luminance=0.4, frame_token=1, now=1.0)
    assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=2, now=1.2) == []
    assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=2, now=1.8) == []
    assert detector.is_suppressed(lit) is False


def test_vision_kill_dark_loss_requires_prior_dimming() -> None:
    detector = VisionKillDetector()
    lit = ArmorObservation(103, "three", "blue", 0.92, ((100.0, 100.0), (180.0, 100.0), (180.0, 140.0), (100.0, 140.0)), light_strength=0.8, light_state="lit", target_id=7)
    dim = ArmorObservation(103, "three", "blue", 0.92, ((100.0, 100.0), (180.0, 100.0), (180.0, 140.0), (100.0, 140.0)), light_strength=0.1, light_state="lit", target_id=7)
    detector.process([lit], alliance="Red", frame_luminance=0.4, frame_token=1, now=1.0)
    assert detector.process([dim], alliance="Red", frame_luminance=0.4, frame_token=2, now=1.1) == []
    assert detector.process([dim], alliance="Red", frame_luminance=0.4, frame_token=3, now=1.2) == []
    for token, now in ((4, 1.3), (5, 1.4), (6, 1.5)):
        assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=token, loss_evidence=VisionLossEvidence(7, "three", 0.1, 0.8, True, False, False, token), now=now) == []
    events = detector.process([], alliance="Red", frame_luminance=0.4, frame_token=7, loss_evidence=VisionLossEvidence(7, "three", 0.1, 0.8, True, False, False, 7), now=1.65)
    assert len(events) == 1 and events[0].detail == "vision_confirmed_dark_loss_probe"


def test_vision_kill_bright_or_exiting_loss_never_eliminates() -> None:
    detector = VisionKillDetector()
    lit = ArmorObservation(103, "three", "blue", 0.92, ((300.0, 120.0), (360.0, 120.0), (360.0, 150.0), (300.0, 150.0)), light_strength=0.8, light_state="lit", target_id=7)
    detector.process([lit], alliance="Red", frame_luminance=0.4, frame_token=1, frame_size=(640, 360), now=1.0)
    bright_exit = VisionLossEvidence(7, "three", 0.78, 0.80, False, True, False, 2)
    for token, now in ((2, 1.2), (3, 1.3), (4, 1.4), (5, 1.5)):
        assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=token, frame_size=(640, 360), loss_evidence=bright_exit, now=now) == []
    assert detector.last_status == "EXITING_BRIGHT"
    assert detector.is_suppressed(lit) is False


def test_vision_kill_external_missing_data_never_eliminates() -> None:
    detector = VisionKillDetector()
    lit = ArmorObservation(103, "three", "blue", 0.92, ((100.0, 100.0), (180.0, 100.0), (180.0, 140.0), (100.0, 140.0)), light_strength=0.8, light_state="lit", target_id=7)
    detector.process([lit], alliance="Red", frame_luminance=0.4, frame_token=1, now=1.0)
    for token, now in ((2, 1.2), (3, 1.3), (4, 1.4), (5, 1.5)):
        assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=token, now=now) == []
    assert detector.last_status == "LOSS_UNCERTAIN"


def test_vision_kill_exposure_hold_blocks_missing_path() -> None:
    detector = VisionKillDetector()
    lit = ArmorObservation(103, "three", "blue", 0.92, ((100.0, 100.0), (108.0, 100.0), (108.0, 104.0), (100.0, 104.0)), light_strength=0.8, light_state="lit", target_id=7)
    detector.process([lit], alliance="Red", frame_luminance=0.50, frame_token=1, now=0.80)
    for token, now in ((2, 1.00), (3, 1.05), (4, 1.10), (5, 1.15)):
        assert detector.process([], alliance="Red", frame_luminance=0.10, frame_token=token, now=now) == []
    assert detector.is_suppressed(lit) is False


def test_vision_kill_reliable_mode_rejects_missing_path() -> None:
    detector = VisionKillDetector()
    lit = ArmorObservation(103, "three", "blue", 0.92, ((100.0, 100.0), (108.0, 100.0), (108.0, 104.0), (100.0, 104.0)), light_strength=0.8, light_state="lit", target_id=7)
    off = ArmorObservation(103, "three", "extinguish", 0.94, ((100.0, 100.0), (108.0, 100.0), (108.0, 104.0), (100.0, 104.0)), light_strength=0.7, light_state="extinguished", target_id=7)
    detector.process([lit], alliance="Red", frame_luminance=0.4, frame_token=1, now=1.0, sensitivity="Match Reliable")
    for token, now in ((2, 1.2), (3, 1.3), (4, 1.5), (5, 1.6)):
        assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=token, now=now, sensitivity="Match Reliable") == []
    assert detector.process([off], alliance="Red", frame_luminance=0.4, frame_token=6, now=1.7, sensitivity="Match Reliable") == []
    assert detector.process([off], alliance="Red", frame_luminance=0.4, frame_token=7, now=1.8, sensitivity="Match Reliable") == []
    events = detector.process([off], alliance="Red", frame_luminance=0.4, frame_token=8, now=2.0, sensitivity="Match Reliable")
    assert len(events) == 1 and events[0].detail == "vision_confirmed_lamp_off"


def test_vision_kill_dim_uses_target_baseline() -> None:
    detector = VisionKillDetector()
    dark_blue = ArmorObservation(103, "three", "blue", 0.92, ((100.0, 100.0), (108.0, 100.0), (108.0, 104.0), (100.0, 104.0)), light_strength=0.12, light_state="lit", target_id=7)
    for token, now in ((1, 1.0), (2, 1.2), (3, 1.4), (4, 1.6)):
        assert detector.process([dark_blue], alliance="Red", frame_luminance=0.4, frame_token=token, now=now) == []
    assert detector.is_suppressed(dark_blue) is False


def test_vision_kill_no_lit_history_no_miss() -> None:
    detector = VisionKillDetector()
    assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=1, now=1.0) == []
    assert detector.process([], alliance="Red", frame_luminance=0.4, frame_token=2, now=1.5) == []


def test_vision_kill_exposure_pauses_not_clears() -> None:
    detector = VisionKillDetector()
    lit = ArmorObservation(103, "three", "blue", 0.92, ((1.0, 1.0), (8.0, 1.0), (8.0, 4.0), (1.0, 4.0)), light_strength=0.8, light_state="lit", target_id=7)
    extinguish = ArmorObservation(103, "three", "extinguish", 0.94, ((1.0, 1.0), (8.0, 1.0), (8.0, 4.0), (1.0, 4.0)), light_strength=0.1, light_state="extinguished", target_id=7)
    detector.process([lit], alliance="Red", frame_luminance=0.50, frame_token=1, now=1.0)
    assert detector.process([extinguish], alliance="Red", frame_luminance=0.50, frame_token=2, now=1.12) == []
    assert detector.process([extinguish], alliance="Red", frame_luminance=0.50, frame_token=3, now=1.18) == []
    # 全局亮度突变：hold 期间暂停累计，但不应清零已有 frames
    assert detector.process([extinguish], alliance="Red", frame_luminance=0.10, frame_token=4, now=1.22) == []
    assert detector.last_status == "EXPOSURE_HOLD"
    assert detector.process([extinguish], alliance="Red", frame_luminance=0.10, frame_token=5, now=1.28) == []
    # hold 结束后再累计一帧即可满 3 帧并确认
    events = detector.process([extinguish], alliance="Red", frame_luminance=0.10, frame_token=6, now=1.50)
    assert len(events) == 1 and events[0].detail == "vision_confirmed_lamp_off"


def test_vision_kill_dim_lamp() -> None:
    detector = VisionKillDetector()
    lit = ArmorObservation(103, "three", "blue", 0.92, ((1.0, 1.0), (8.0, 1.0), (8.0, 4.0), (1.0, 4.0)), light_strength=0.8, light_state="lit", target_id=7)
    dim = ArmorObservation(103, "three", "blue", 0.90, ((1.0, 1.0), (8.0, 1.0), (8.0, 4.0), (1.0, 4.0)), light_strength=0.12, light_state="lit", target_id=7)
    detector.process([lit], alliance="Red", frame_luminance=0.4, frame_token=1, now=1.0)
    assert detector.process([dim], alliance="Red", frame_luminance=0.4, frame_token=2, now=1.10) == []
    assert detector.process([dim], alliance="Red", frame_luminance=0.4, frame_token=3, now=1.18) == []
    events = detector.process([dim], alliance="Red", frame_luminance=0.4, frame_token=4, now=1.35)
    assert len(events) == 1 and events[0].detail == "vision_confirmed_dim_lamp"


def test_local_target_identity_survives_extinguish() -> None:
    from rm_client.tongji_yolo import LocalDetection, LocalTargetSelector

    selector = LocalTargetSelector()
    lit = LocalDetection("three", "blue", 0.92, (100.0, 80.0, 60.0, 30.0), ((100.0, 80.0), (160.0, 80.0), (160.0, 110.0), (100.0, 110.0)), 0.8)
    extinguished = LocalDetection("three", "extinguish", 0.94, (102.0, 81.0, 60.0, 30.0), ((102.0, 81.0), (162.0, 81.0), (162.0, 111.0), (102.0, 111.0)), 0.12)
    first = selector.select([lit], 640, 360)
    assert selector.select([], 640, 360) is None
    assert selector.select([], 640, 360) is None
    second = selector.select([extinguished], 640, 360)
    assert first is not None and second is not None
    assert first[1] == second[1]

    # 超过延长后的关联窗才换新 track_id
    selector.last_seen_at -= 0.80
    replacement = selector.select([lit], 640, 360)
    assert replacement is not None and replacement[1] != second[1]


def test_gd656_renderer_presets() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assets = ROOT / "assets" / "kill_fx" / "gd656"
    presets = list(PRESET_ELEMENTS) + [f"Valorant {name}" for name in VALORANT_STYLES]
    assert len(VALORANT_STYLES) == 26
    for preset in presets:
        renderer = KillFxRenderer(assets)
        event = CombatEvent(
            "elimination", "preview", True, 10.0,
            victim_id=103, kill_type="HEADSHOT", combo=5,
            victim_name="ROBOT 103", score_delta=100, tags=("ELIMINATION", "ARMOR HIT"),
        )
        renderer.trigger(event, combo_window=5.0, sound_enabled=False, volume=0, preset=preset, visibility="Full HUD", now_override=10.0)
        image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        renderer.draw(
            painter, 1280, 720, preset=preset, visibility="Full HUD", team="CT Blue",
            scale=1.0, anchor_policy="Original Position", x_offset=0.0, y_offset=0.0,
            now_override=10.18,
        )
        painter.end()
        assert any(bytes(image.constBits())), preset
    app.processEvents()


def test_wallpaper_pkg_index_reader() -> None:
    package = ROOT.parent / "3000925581" / "scene.pkg"
    if not package.exists():
        return
    vfs = PkgVirtualFileSystem(package)
    vfs.load_index()
    assert "scene.json" in vfs.entries
    assert len(vfs.entries) > 20
    scene_text = vfs.read_text("scene.json")
    assert '"objects"' in scene_text


def test_wallpaper_profiles_and_user_import() -> None:
    with TemporaryDirectory() as temp:
        workspace = Path(temp)
        builtin = workspace / "rm_custom_client" / "assets" / "wallpapers" / "builtin"
        builtin.mkdir(parents=True)
        (builtin / "texas.mp4").write_bytes(b"texas")
        (builtin / "vending-machine.mp4").write_bytes(b"vending")
        source = workspace / "custom-wallpaper.mp4"
        source.write_bytes(b"custom")
        manager = StartupWallpaperManager(workspace)
        try:
            assert manager.profile_names()[:5] == ["BlackHole", "TexasMP4", "VendingMachine4K", "Default3000925581Pkg", "ParticleGrid"]
            assert manager.resolve_media_profile("VendingMachine4K") == builtin / "vending-machine.mp4"
            profile = manager.import_wallpaper(source)
            assert profile == "User:custom-wallpaper"
            assert profile in manager.profile_names()
            assert manager.can_remove(profile) is True
            assert manager.remove_wallpaper(profile) is True
            assert profile not in manager.profile_names()
            assert manager.can_remove("TexasMP4") is False
        finally:
            manager.release()


def test_wallpaper_primes_cv2_before_worker_start() -> None:
    """The Windows native cv2 import must finish before a decode thread runs."""
    import sys
    from unittest.mock import patch

    from rm_client.startup_wallpaper import VideoWallpaperPlayer

    with TemporaryDirectory() as temp:
        media = Path(temp) / "wallpaper.mp4"
        media.write_bytes(b"not-a-real-video")
        player = VideoWallpaperPlayer()

        class FakeThread:
            def __init__(self, *args, **kwargs) -> None:
                self.started = False

            def is_alive(self) -> bool:
                return self.started

            def start(self) -> None:
                assert "cv2" in sys.modules
                self.started = True

        with patch("rm_client.startup_wallpaper.threading.Thread", FakeThread):
            assert player.load(media)


def test_hud_modes_and_rmui_modules() -> None:
    registry = build_default_registry()
    hud = registry.get("HUD")
    assert hud is not None
    right_display = hud.get_setting("Right Display")
    assert right_display is not None
    assert right_display.options == HUD_MODES
    assert "TacticalMapHud" in HUD_MODES
    assert hud.get_setting("Density").options == ["CombatCompact", "Balanced", "DebugFull"]
    assert hud.get_setting("Visual Tone").options == ["LiquidClean", "PolarLegacy"]
    assert hud.get_setting("Target Style").options == ["RoundCompact", "BarOnly", "FullCard"]
    assert hud.get_setting("Combat Preset").options == ["Polar", "Minecraft 1.21.11"]
    assert hud.get_setting("Combat Preset").value == "Polar"
    assert hud.get_setting("Density").value == "CombatCompact"
    assert hud.get_setting("Visual Tone").value == "LiquidClean"
    assert hud.get_setting("Target Style").value == "RoundCompact"
    module_list_style = hud.get_setting("ModuleList Style")
    assert module_list_style is not None
    assert module_list_style.options == MODULE_LIST_STYLES
    assert "PolarTextList" in MODULE_LIST_STYLES
    assert "PolarRainbowRight" in MODULE_LIST_STYLES
    assert "JelloActiveMods" in MODULE_LIST_STYLES
    assert "DebugWhiteList" not in MODULE_LIST_STYLES
    assert "PolarLegacy" not in MODULE_LIST_STYLES
    assert "RainbowRight" not in MODULE_LIST_STYLES
    assert set(MODULE_LIST_RENDERER_KEYS) == set(MODULE_LIST_STYLES)
    assert len(set(MODULE_LIST_RENDERER_KEYS.values())) == len(MODULE_LIST_STYLES)
    assert MODULE_LIST_RENDERER_KEYS["JelloActiveMods"] == "jello_active_mods"
    assert module_list_style.value == "JelloActiveMods"
    assert hud.get_setting("Jello List Size").options == ["Normal", "Small", "Tiny"]
    assert hud.get_setting("Jello List Sound").value is True
    for style in ("LiquidArrayList", "LiquidArrayTags", "LiquidArrayCompact", "LiquidArrayDock"):
        assert style in MODULE_LIST_STYLES
    assert hud.get_setting("ModuleList Surface").options == ["Native", "LiquidGlass"]
    assert hud.get_setting("ModuleList Alignment").options == ["Right", "Left"]
    assert hud.get_setting("ModuleList Order").options == ["Descending", "Ascending"]
    assert hud.get_setting("Module Tags").options == ["Category", "Keybind", "Off"]
    assert hud.get_setting("Module Animation").options == ["FlyFlip", "Slide", "Fade"]
    for name in (
        "GimbalPower",
        "ChassisGimbalIndicator",
        "PowerLimit",
        "SpeedLimit",
        "Capacitor",
        "FrictionWheel",
        "LockState",
        "BuffStatus",
        "ProjectileAllowance",
        "JumpPTEWheelLES",
        "TacticalMap",
        "LiquidTacticalMap",
        "MatchTimer",
        "BaseOutpostStatus",
        "VictoryPointStatus",
        "EconomyPanel",
        "ProjectileShop",
        "ThermalPowerPanel",
        "BuffTimeline",
    ):
        assert registry.get(name) is not None


def test_match_modes_and_official_map_assets() -> None:
    assert MATCH_MODES["RMUC7v7"].field.width_m == 28.0
    assert MATCH_MODES["RMUC7v7"].field.height_m == 15.0
    assert MATCH_MODES["RMUL3v3"].field.width_m == 12.0
    assert MATCH_MODES["RMUL3v3"].field.height_m == 8.0
    for config in MATCH_MODES.values():
        asset = ROOT / config.field.asset_path
        assert asset.exists(), asset
        assert asset.stat().st_size > 1000


def test_match_mode_manager() -> None:
    registry = build_default_registry()
    manager = MatchModeManager("RMUC7v7")
    manager.apply_to_registry(registry)
    assert registry.get("HUD").get_setting("Right Display").value == "ModuleListHud"
    assert registry.get("TacticalMap").get_setting("Map Size").value == "Compact"
    assert registry.get("EnergyAutoAim").enabled is True
    assert registry.get("BaseOutpostStatus").enabled is True
    manager.set_mode("RMUL3v3")
    manager.apply_to_registry(registry)
    assert registry.get("MatchMode").get_setting("Mode").value == "RMUL3v3"
    assert registry.get("TacticalMap").get_setting("Official Map").value == "RMUL 3v3 Official Map"
    assert registry.get("EnergyAutoAim").enabled is False
    assert registry.get("VictoryPointStatus").enabled is True


def test_config_store_roundtrip() -> None:
    registry = build_default_registry()
    registry.get("ArmorAutoAim").keybind = "J"
    registry.get("ArmorAutoAim").get_setting("Fov").value = 72
    with TemporaryDirectory() as temp_dir:
        store = ClientConfigStore(Path(temp_dir))
        data = export_client_config(
            registry,
            gui_mode="PolarNew",
            selected_category="Combat",
            selected_module="ArmorAutoAim",
            module_expanded={"ArmorAutoAim"},
            old_panels={},
            new_panels={},
            dropdown_panels={},
            polar_windows={"Combat": {"x": 80, "y": 100, "scroll": 0}},
            quick_action_wheel={
                "sectors": 6,
                "radius": 132.0,
                "dead_zone": 42.0,
                "actions": list(DEFAULT_ACTIONS),
                "labels": ["Economy", "", "", "", "", "", "", ""],
                "icons": ["EC", "", "", "", "", "", "", ""],
            },
        )
        store.save("practice", data)
        assert "practice" in store.profile_names()
        loaded = store.load("practice")
        assert loaded is not None
        assert loaded["gui_mode"] == PRIMARY_GUI_MODE
        assert "Old ClickGUIs" not in loaded["modules"]["ClickGui"]["settings"]
        assert loaded["polar_windows"]["Combat"]["x"] == 80
        assert loaded["quick_action_wheel"]["sectors"] == 6
        assert loaded["quick_action_wheel"]["labels"][0] == "Economy"
        assert not list(Path(temp_dir).glob("*.tmp"))
        store.profile_path("bad-version").write_text(
            '{"version":"not-a-number","modules":{}}', encoding="utf-8"
        )
        assert store.load("bad-version") is None
        fresh = build_default_registry()
        apply_registry_config(fresh, loaded)
        assert fresh.get("ArmorAutoAim").keybind == "J"
        assert fresh.get("ArmorAutoAim").get_setting("Fov").value == 72.0
        apply_registry_config(fresh, {
            "modules": {
                "StartupVisual": {"settings": {"Glass Style": "ReGlass"}},
                "HUD": {"settings": {"ModuleList Surface": "ReGlass"}},
                "TacticalMap": {"settings": {"Status Display": "ReGlassCards"}},
            }
        })
        assert fresh.get("StartupVisual").get_setting("Glass Style").value == "LiquidGlass"
        assert fresh.get("HUD").get_setting("ModuleList Surface").value == "LiquidGlass"
        assert fresh.get("TacticalMap").get_setting("Status Display").value == "LiquidCards"
        apply_registry_config(fresh, {
            "modules": {"HUD": {"settings": {"ModuleList Style": "PolarLegacy"}}}
        })
        assert fresh.get("HUD").get_setting("ModuleList Style").value == "PolarTextList"
        apply_registry_config(fresh, {
            "modules": {"HUD": {"settings": {"ModuleList Style": "RainbowRight"}}}
        })
        assert fresh.get("HUD").get_setting("ModuleList Style").value == "PolarRainbowRight"
        apply_registry_config(fresh, {
            "modules": {"HUD": {"settings": {"ModuleList Style": "DebugWhiteList"}}}
        })
        assert fresh.get("HUD").get_setting("ModuleList Style").value == "JelloActiveMods"
        apply_registry_config(fresh, {
            "modules": {"Armor3DESP": {"settings": {"Style": "PolarMechanicalWire"}}}
        })
        assert fresh.get("Armor3DESP").get_setting("Style").value == "JelloShadow"
        apply_registry_config(fresh, {
            "modules": {"Armor3DESP": {"settings": {"Style": "Wireframe3D"}}}
        })
        assert fresh.get("Armor3DESP").get_setting("Style").value == "Wireframe3D"
        apply_registry_config(fresh, {"modules": {"MatchMode": {"settings": {"Alliance": "Blue"}}}})
        assert fresh.get("AllianceProfile").get_setting("Side").value == "Blue"
        apply_registry_config(fresh, {"modules": {"HeroLob": {"keybind": "None"}}})
        assert fresh.get("HeroLob").keybind == "K"


def test_jello_mode_migration() -> None:
    historical = [*LEGACY_GUI_MODES, "VapeUnified", "VapeMulti", "Unknown"]
    for mode in historical:
        assert normalize_gui_mode(mode) == PRIMARY_GUI_MODE
        manager = GuiModeManager(mode)
        assert manager.mode == PRIMARY_GUI_MODE
        assert manager.next_mode() == PRIMARY_GUI_MODE


def test_tongji_bridge_protocol() -> None:
    ready = threading.Event()
    finished = threading.Event()
    port_box: list[int] = []

    def server() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port_box.append(listener.getsockname()[1])
            ready.set()
            client, _address = listener.accept()
            with client:
                header = client.recv(PACKET_HEADER.size)
                magic, version, packet_type, sequence, timestamp_ns, payload_size = PACKET_HEADER.unpack(header)
                assert magic == PROTOCOL_MAGIC
                assert version == PROTOCOL_VERSION
                assert packet_type == PACKET_FRAME
                payload = bytearray()
                while len(payload) < payload_size:
                    payload.extend(client.recv(payload_size - len(payload)))
                assert b"source_width" in payload
                result = b'{"source_width":640,"source_height":360,"tracking_state":"tracking","calibrated":true,"inference_device":"GPU","inference_fps":88.0,"latency_ms":12.5,"detections":[{"id":3,"label":"three","color":"blue","confidence":0.93,"points":[[1,2],[3,2],[3,4],[1,4]],"distance_m":5.2}],"predicted_armors":[{"id":0,"label":"three","color":"predicted","confidence":1.0,"points":[[10,20],[30,20],[30,40],[10,40]],"predicted":true}],"aim_yaw_deg":1.5,"aim_pitch_deg":-0.5}'
                response = PACKET_HEADER.pack(PROTOCOL_MAGIC, PROTOCOL_VERSION, PACKET_RESULT, sequence, timestamp_ns, len(result))
                client.sendall(response + result)
        finished.set()

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    assert ready.wait(1.0)
    bridge = TongjiVisionBridge("127.0.0.1", port_box[0])
    bridge.start()
    bridge.submit_jpeg(7, time.time_ns(), 640, 360, b"jpeg")
    deadline = time.perf_counter() + 2.0
    result = None
    while time.perf_counter() < deadline:
        result = bridge.latest_result(10_000)
        if result is not None:
            break
        time.sleep(0.01)
    bridge.stop()
    assert finished.wait(1.0)
    assert result is not None
    assert result.sequence == 7
    assert result.is_tracking is True
    assert result.inference_device == "GPU"
    assert len(result.detections) == 1
    assert len(result.predicted_armors) == 1


def test_tongji_udp_protocol() -> None:
    receiver = TongjiVisionReceiver(0)
    receiver.start()
    deadline = time.perf_counter() + 1.0
    while receiver._socket is None and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert receiver._socket is not None
    port = receiver._socket.getsockname()[1]
    bad_armor = (
        b'{"protocol":"rm.vision.tongji.v1","frame_w":640,"frame_h":360,'
        b'"armors":[{"id":"abc","points":[[1,2],[30,2],[30,20],[1,20]]}]}'
    )
    parsed_bad_armor = parse_tongji_packet(bad_armor)
    assert parsed_bad_armor is not None and parsed_bad_armor.armors == ()
    bad_pose = parse_tongji_packet(
        b'{"protocol":"rm.vision.tongji.v1","frame_w":640,"frame_h":360,'
        b'"gimbal":{"yaw_rad":"nan","calibrated":"yes"},'
        b'"command":{"pitch_rad":0.25,"calibrated":1}}'
    )
    assert bad_pose is not None
    assert "yaw_rad" not in bad_pose.gimbal and bad_pose.gimbal["calibrated"] is False
    assert bad_pose.command["pitch_rad"] == 0.25 and bad_pose.command["calibrated"] is True
    from rm_client.vision import snapshot_from_tongji_packet

    missing_calibration = parse_tongji_packet(
        b'{"protocol":"rm.vision.tongji.v1","frame_w":640,"frame_h":360,'
        b'"command":{"control":"false","fire":"false"}}'
    )
    assert missing_calibration is not None
    assert missing_calibration.command["control"] is False
    assert missing_calibration.command["fire"] is False
    assert snapshot_from_tongji_packet(missing_calibration).result.calibrated is False
    packet = build_tongji_packet(
        640,
        360,
        [{"name": "three", "color": "blue", "confidence": 0.91, "points": [[1, 2], [30, 2], [30, 20], [1, 20]], "locked": True}],
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as publisher:
        # A structurally invalid packet and a malformed observation used to
        # terminate this thread before the subsequent valid packet arrived.
        publisher.sendto(b'{"protocol":"rm.vision.tongji.v1","frame_w":"bad"}', ("127.0.0.1", port))
        publisher.sendto(bad_armor, ("127.0.0.1", port))
        publisher.sendto(packet, ("127.0.0.1", port))
    received = None
    deadline = time.perf_counter() + 1.0
    while time.perf_counter() < deadline:
        received = receiver.latest(1_000)
        # ``bad_armor`` is a structurally valid telemetry packet whose single
        # malformed observation is intentionally dropped.  It may therefore
        # be published briefly before the following valid packet; wait for the
        # payload this test is actually asserting instead of racing the UDP
        # receiver's latest-only handoff.
        if (
            received is not None
            and received.primary is not None
            and received.primary.get("name") == "three"
        ):
            break
        time.sleep(0.01)
    receiver.stop()
    assert received is not None
    assert received.primary is not None and received.primary["name"] == "three"
    assert receiver.packets_received >= 3
    assert receiver.invalid_packets >= 1
    assert receiver.parse_errors == 0


def test_local_prediction_snapshot() -> None:
    from rm_client.vision import snapshot_from_local_result

    armors = [
        {"id": 0, "plate_id": 0, "name": "three", "color": "blue", "confidence": 0.92, "points": [[100, 100], [160, 100], [160, 130], [100, 130]], "back_points": [[104, 104], [156, 104], [156, 132], [104, 132]], "locked": True, "predicted": False, "depth_m": 4.0},
        {"id": 1, "plate_id": 1, "name": "three", "color": "blue", "confidence": 0.62, "points": [[220, 100], [270, 100], [270, 130], [220, 130]], "back_points": [[224, 104], [266, 104], [266, 132], [224, 132]], "predicted": True, "depth_m": 4.2},
        {"id": 2, "plate_id": 2, "name": "three", "color": "blue", "confidence": 0.62, "points": [[300, 180], [350, 180], [350, 210], [300, 210]], "back_points": [[304, 184], [346, 184], [346, 212], [304, 212]], "predicted": True, "depth_m": 4.5},
        {"id": 3, "plate_id": 3, "name": "three", "color": "blue", "confidence": 0.62, "points": [[200, 250], [250, 250], [250, 280], [200, 280]], "back_points": [[204, 254], [246, 254], [246, 282], [204, 282]], "predicted": True, "depth_m": 4.3},
    ]
    snapshot = snapshot_from_local_result(time.time_ns(), 5, 640, 360, armors, 45.0, 22.0, "CPU")
    assert len(snapshot.result.detections) == 1
    assert len(snapshot.result.predicted_armors) == 3
    assert all(len(armor.back_points) == 4 for armor in snapshot.result.predicted_armors)


def test_calibration_export() -> None:
    from rm_client.calibration import CalibrationProfile, CameraCalibrationManager

    with TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)
        config_dir = workspace / "同济自瞄" / "sp_vision_25" / "configs"
        config_dir.mkdir(parents=True)
        (config_dir / "demo.yaml").write_text("camera_matrix: [1, 0, 2, 0, 3, 4, 0, 0, 1]\ndistort_coeffs: [0, 0, 0, 0, 0]\nR_camera2gimbal: [1, 0, 0, 0, 1, 0, 0, 0, 1]\nt_camera2gimbal: [0, 0, 0]\n", encoding="utf-8")
        manager = CameraCalibrationManager(workspace)
        manager.profile = CalibrationProfile("polar_webcam", 960, 540, 9, 6, 25.0, [800.0, 0.0, 480.0, 0.0, 800.0, 270.0, 0.0, 0.0, 1.0], [0.0] * 5, 0.42, 12)
        exported = manager.export_tongji_yaml("polar_webcam")
        assert exported is not None and exported.exists()
        text = exported.read_text(encoding="utf-8")
        assert "800" in text
        assert manager.is_valid_for(960, 540) is True


def test_armor_3d_esp_render() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from rm_client.ui.polar_app import PolarRMWindow
    from rm_client.vision import ArmorObservation, VisionResult

    app = QApplication.instance() or QApplication([])
    window = _build_settled_window(PolarRMWindow, app)
    window.resize(640, 360)
    # 桌面 smoke 用同尺寸 profile 模拟完成标定，确保预测绘制只依赖桥接结果而非 mock 框。
    from rm_client.calibration import CalibrationProfile

    window.calibration.profile = CalibrationProfile(
        "smoke", 640, 360, 9, 6, 25.0, [500.0] * 9, [0.0] * 5, 0.4, 10
    )
    result = VisionResult(
        sequence=1,
        timestamp_ns=time.time_ns(),
        source_width=640,
        source_height=360,
        calibrated=True,
        tracking_state="tracking",
        detections=(ArmorObservation(3, "three", "blue", 0.93, ((210, 120), (270, 120), (270, 150), (210, 150))),),
        predicted_armors=(
            ArmorObservation(0, "three", "predicted", 1.0, ((310, 112), (365, 116), (365, 146), (310, 142)), 6.0, True, False, 5.6),
            ArmorObservation(1, "three", "predicted", 1.0, ((340, 180), (390, 184), (390, 212), (340, 208)), 8.4, True, False, 7.9),
        ),
        aim_armor=ArmorObservation(-1, "aim", "predicted", 1.0, ((310, 112), (365, 116), (365, 146), (310, 142)), 6.0, True, True, 5.6),
    )
    module = window.registry.get("Armor3DESP")
    assert module is not None
    module.get_setting("Style").value = "PolarMechanicalWire"
    module.get_setting("Prediction Labels").value = False
    image = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    window.draw_armor_3d_esp(painter, 640, 360, result, module)
    painter.end()
    try:
        visible_pixels = sum(
            1
            for y in range(0, image.height(), 2)
            for x in range(0, image.width(), 2)
            if image.pixelColor(x, y).alpha() > 0
        )
        fluorescent_white_pixels = sum(
            1
            for y in range(0, image.height(), 2)
            for x in range(0, image.width(), 2)
            if (
                image.pixelColor(x, y).alpha() > 150
                and image.pixelColor(x, y).red() > 225
                and image.pixelColor(x, y).green() > 225
                and image.pixelColor(x, y).blue() > 225
            )
        )
        saturated_green_pixels = sum(
            1
            for y in range(0, image.height(), 2)
            for x in range(0, image.width(), 2)
            if (
                image.pixelColor(x, y).alpha() > 120
                and image.pixelColor(x, y).green() > image.pixelColor(x, y).red() + 36
                and image.pixelColor(x, y).green() > image.pixelColor(x, y).blue() + 18
            )
        )
        assert 5 < visible_pixels < 2250
        assert fluorescent_white_pixels > 5
        assert saturated_green_pixels == 0
    finally:
        close_window_safely(window, app)
    app.processEvents()


def test_setting_clamp() -> None:
    registry = build_default_registry()
    armor_aim = registry.get("ArmorAutoAim")
    fov = armor_aim.get_setting("Fov")
    fov.set_from_ratio(2.0)
    assert fov.value == fov.max_value
    fov.set_from_ratio(-1.0)
    assert fov.value == fov.min_value


def test_udp_reassembly() -> None:
    assembler = FrameAssembler()
    payload = b"abcdefghi"
    p0 = (7).to_bytes(2, "big") + (0).to_bytes(2, "big") + len(payload).to_bytes(4, "big") + payload[:4]
    p1 = (7).to_bytes(2, "big") + (1).to_bytes(2, "big") + len(payload).to_bytes(4, "big") + payload[4:]
    assert assembler.push_packet(p0) is None
    assert assembler.push_packet(p1) == payload

    def fragment(frame_id: int, index: int, total: int, data: bytes) -> bytes:
        return (
            frame_id.to_bytes(2, "big")
            + index.to_bytes(2, "big")
            + total.to_bytes(4, "big")
            + data
        )

    # Conflicting metadata, missing chunk indexes and conflicting duplicates
    # are corrupt frames, never byte sequences to concatenate/truncate.
    assert assembler.push_packet(fragment(8, 0, 4, b"ab")) is None
    assert assembler.push_packet(fragment(8, 1, 5, b"cd")) is None
    assert 8 not in assembler.frames
    assert assembler.push_packet(fragment(9, 0, 4, b"ab")) is None
    assert assembler.push_packet(fragment(9, 2, 4, b"cd")) is None
    assert 9 not in assembler.frames
    assert assembler.push_packet(fragment(10, 0, 4, b"ab")) is None
    assert assembler.push_packet(fragment(10, 0, 4, b"zz")) is None
    assert 10 not in assembler.frames
    assert assembler.push_packet(fragment(11, 0, FrameAssembler.MAX_FRAME_BYTES + 1, b"x")) is None
    assert assembler.invalid_count >= 4

    flood = FrameAssembler()
    for frame_id in range(FrameAssembler.MAX_IN_FLIGHT + 5):
        assert flood.push_packet(fragment(frame_id, 0, 2, b"x")) is None
    assert len(flood.frames) == FrameAssembler.MAX_IN_FLIGHT
    assert flood.dropped_count == 5

    # The frame-count bound alone allowed 64 partial 16 MiB frames to retain
    # about one GiB. Exercise aggregate eviction with a tiny per-test ceiling.
    memory_bound = FrameAssembler()
    memory_bound.MAX_BUFFERED_BYTES = 5
    assert memory_bound.push_packet(fragment(20, 0, 10, b"abcd")) is None
    assert memory_bound.buffered_bytes == 4
    assert memory_bound.push_packet(fragment(21, 0, 10, b"wxyz")) is None
    assert 20 not in memory_bound.frames and 21 in memory_bound.frames
    assert memory_bound.buffered_bytes == 4 and memory_bound.dropped_count == 1


def test_video_pipeline_reassembly() -> None:
    pipeline = VideoPipeline()
    payload = b"hevc-frame"
    p0 = (11).to_bytes(2, "big") + (0).to_bytes(2, "big") + len(payload).to_bytes(4, "big") + payload[:4]
    p1 = (11).to_bytes(2, "big") + (1).to_bytes(2, "big") + len(payload).to_bytes(4, "big") + payload[4:]
    assert pipeline.push_udp_packet(p0) is None
    assert pipeline.push_udp_packet(p1) == payload
    assert pipeline.frames_received == 1


def test_referee_frame_codec() -> None:
    codec = RefereeFrameCodec()
    frame = codec.encode(0x0311, b"abc", seq=7)
    decoded = codec.decode(b"\x00" + frame + b"\xff")
    assert len(decoded) == 1
    assert decoded[0].cmd_id == 0x0311
    assert decoded[0].seq == 7
    assert decoded[0].data == b"abc"
    broken = bytearray(frame)
    broken[-1] ^= 0x55
    assert codec.decode(bytes(broken)) == []


def test_keyboard_control_bridge() -> None:
    from rm_client.control import KeyboardControlBridge

    class KeyboardPublisher:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def publish_keyboard_mouse(self, mouse_x, mouse_y, mouse_z, **kwargs):
            self.calls.append({
                "mouse_x": mouse_x,
                "mouse_y": mouse_y,
                "mouse_z": mouse_z,
                **kwargs,
            })
            return True, "ok"

    registry = build_default_registry()
    state = RobotState()
    publisher = KeyboardPublisher()
    bridge = KeyboardControlBridge(publisher, mock_output=True)
    command = bridge.set_key("W", True, state, registry)
    assert command is not None
    assert command.vx == 1.0
    assert command.vy == 0.0
    assert command.keyboard_value == 1
    assert b"rm2026.KeyboardMouseControl.v1.3.1" in bridge.last_payload
    assert publisher.calls == []

    bridge.mock_output = False
    bridge.add_mouse_motion(12, 7)
    bridge.add_wheel(120)
    command = bridge.set_key("W", False, state, registry)
    assert command is not None
    assert command.vx == 0.0
    assert publisher.calls[-1]["keyboard_value"] == 0
    assert publisher.calls[-1]["mouse_x"] == 12
    assert publisher.calls[-1]["mouse_y"] == -7
    assert publisher.calls[-1]["mouse_z"] == 120
    assert bridge.mouse_x == bridge.mouse_y == bridge.mouse_z == 0
    bridge.set_key("A", True, state, registry)
    bridge.set_mouse_button(1, True, state, registry)
    before_release = len(publisher.calls)
    neutral = bridge.release_all(state, registry)
    assert len(publisher.calls) == before_release + 1
    assert neutral.keyboard_value == 0
    assert publisher.calls[-1]["keyboard_value"] == 0
    assert publisher.calls[-1]["left"] is False

    class SafetyState:
        value = "LOCKED"

    class Safety:
        def __init__(self) -> None:
            self.control_state = SafetyState()
            self.fighting = False

        def is_fighting(self) -> bool:
            return self.fighting

    publisher.safety = Safety()
    heartbeat = KeyboardControlBridge(publisher, mock_output=False)
    assert heartbeat.tick(state, registry, now=100.0) is None
    locked_calls = len(publisher.calls)
    publisher.safety.control_state.value = "MATCH_ARMED"
    # Arming outside the authoritative fighting stage must not create an
    # uplink that looks like active match traffic.
    assert heartbeat.tick(state, registry, now=100.0) is None
    publisher.safety.fighting = True
    neutral = heartbeat.tick(state, registry, now=100.0)
    assert neutral is not None and neutral.keyboard_value == 0
    assert len(publisher.calls) == locked_calls + 1
    heartbeat.last_send_at = 100.0
    assert heartbeat.tick(state, registry, now=100.249) is None
    assert heartbeat.tick(state, registry, now=100.251) is not None


def test_registry_tick_enabled_modules_only() -> None:
    class CountingModule(Module):
        def __init__(self, name: str, enabled: bool) -> None:
            super().__init__(name=name, category="System", enabled=enabled)
            self.calls = 0

        def on_tick(self, state: RobotState, dt: float) -> None:
            assert isinstance(state, RobotState)
            assert dt == 0.02
            self.calls += 1

    enabled = CountingModule("Enabled", True)
    disabled = CountingModule("Disabled", False)
    ModuleRegistry([enabled, disabled]).tick(RobotState(), 0.02)
    assert enabled.calls == 1
    assert disabled.calls == 0


def test_scenario_manager() -> None:
    registry = build_default_registry()
    manager = ScenarioManager()
    manager.set_scenario("OfficialEvent")
    mqtt = OfficialMqttBridge()
    udp = UDPVideoReceiver()
    serial = SerialBridgeStub()
    from rm_client.control import KeyboardControlBridge

    control = KeyboardControlBridge(serial, mock_output=False)
    manager.apply_to_registry(registry)
    manager.apply_to_runtime(mqtt, udp, control)
    assert registry.get("Scenario").get_setting("Mode").value == "OfficialEvent"
    assert registry.get("TongjiVisionBridge").get_setting("Vision Kill Sensitivity").value == "Match Reliable"
    assert registry.get("MQTTLink").get_setting("Host").value == "192.168.12.1"
    assert registry.get("SerialBridge").get_setting("Mock Writes").value is True
    assert registry.get("EconomyPanel").get_setting("Mock Requests").value is False
    assert registry.get("ProjectileShop").get_setting("Mock Request").value is False
    assert mqtt.host == "192.168.12.1"
    assert mqtt.mock_output is False
    assert udp.port == 3334
    assert control.mock_output is False
    manager.set_scenario("CampusSimulation")
    manager.apply_to_registry(registry)
    assert registry.get("TongjiVisionBridge").get_setting("Vision Kill Sensitivity").value == "Balanced"
    assert registry.get("EconomyPanel").get_setting("Mock Requests").value is True
    manager.set_scenario("LabDebug")
    manager.apply_to_registry(registry)
    assert registry.get("TongjiVisionBridge").get_setting("Vision Kill Sensitivity").value == "Lab Responsive"


def test_official_mqtt_bridge_mock_publish() -> None:
    bridge = OfficialMqttBridge()
    bridge.publish_custom_control(b"abc")
    assert bridge.publish_records[-1].topic == "CustomControl"
    assert bridge.publish_records[-1].mock_output is True
    assert bridge.publish_common_command(b"one", now=10.0) is True
    assert bridge.publish_records[-1].topic == "CommonCommand"
    assert bridge.publish_records[-1].qos == 1
    record_count = len(bridge.publish_records)
    assert bridge.publish_common_command(b"too-fast", now=10.05) is False
    assert len(bridge.publish_records) == record_count
    assert "10Hz" in bridge.last_error
    assert bridge.publish_common_command(b"two", now=10.101) is True
    assert bridge.publish_air_support_command(b"air", now=10.0) is True
    assert bridge.publish_records[-1].topic == "AirSupportCommand"
    assert bridge.publish_records[-1].qos == 1


def test_robot_state_tick() -> None:
    state = RobotState()
    state.tick(0.016)
    assert state.current_health <= state.max_health
    assert state.stage_countdown_sec >= 0
    assert -180.0 <= state.chassis_yaw_deg <= 180.0
    assert -180.0 <= state.gimbal_yaw_deg <= 180.0
    assert -180.0 <= state.relative_yaw_deg <= 180.0
    assert state.robot_rows
    assert state.map_robots
    assert state.map_markers
    assert 0 <= state.capacitor_percent <= 100
    state.match_mode = "RMUL3v3"
    state.match_duration_sec = 300
    state.tick(0.016)
    assert state.match_title == "RMUL 3v3"
    assert 0 <= state.ally_victory_points <= 200


def test_pyside_animation_import() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter
    from PySide6.QtWidgets import QApplication

    from rm_client.ui.animation import AnimationBank, Easings
    from rm_client.ui.polar_app import PolarRMWindow

    bank = AnimationBank()
    value = bank.value("smoke", 1.0, 0.01, Easings.linear, initial=0.0)
    assert 0.0 <= value <= 1.0
    assert PolarRMWindow.__name__ == "PolarRMWindow"
    timeline = GuiTransitionTimeline()
    timeline.set_target(1.0)
    for _ in range(12):
        timeline.update(1.0 / 60.0)
    current = timeline.progress
    timeline.reverse_from_current()
    timeline.update(1.0 / 60.0)
    assert 0.0 <= timeline.progress <= current
    app = QApplication.instance() or QApplication([])
    window = _build_settled_window(PolarRMWindow, app)
    layout = window.module_list_layout(1920, 1080)
    assert layout.anchor_x == 1902.0
    assert layout.anchor_y == 18.0
    assert layout.available_height == 1044.0
    assert "VendingMachine4K" in window.startup_wallpaper.profile_names()
    vending = ROOT / "assets" / "wallpapers" / "builtin" / "vending-machine.mp4"
    if vending.is_file():
        assert vending.exists() and vending.stat().st_size > 1_000_000
        window.startup_wallpaper.video_player.configure((1280, 720), "Smooth60")
        assert window.startup_wallpaper.video_player.load(vending) is True
        deadline = time.perf_counter() + 3.0
        snapshot = window.startup_wallpaper.video_player.snapshot()
        while snapshot.path != vending.resolve() and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.02)
            snapshot = window.startup_wallpaper.video_player.snapshot()
        assert snapshot.path == vending.resolve()
        assert not snapshot.image.isNull()
        assert not snapshot.soft_glass.isNull()
        assert not snapshot.glass.isNull()
    elif os.environ.get("POLAR_REQUIRE_VIDEO_FIXTURE") == "1":
        close_window_safely(window, app)
        raise AssertionError("Required video fixture is missing: vending-machine.mp4")
    else:
        reason = "optional built-in wallpaper decode: vending-machine.mp4 not supplied"
        SMOKE_SKIPS.append(reason)
        print("[SKIP] " + reason)
    window.wallpaper_picker_visible = True
    rendered = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
    rendered.fill(0)
    window.resize(1280, 720)
    window.render(rendered)
    assert not rendered.isNull()
    visual = window.registry.get("StartupVisual")
    assert visual is not None
    glass_style = visual.get_setting("Glass Style")
    assert glass_style is not None
    glass_style.value = "Legacy"
    window.render(rendered)
    glass_style.value = "LiquidGlass"
    tactical_map = window.registry.get("TacticalMap")
    assert tactical_map is not None
    tactical_map.set_enabled(True)
    press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier, "", False, 1)
    repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier, "", True, 1)
    release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier, "", False, 1)
    assert window.handle_tactical_map_hold_event(press, True) is True
    assert window.tactical_map_hold is True
    assert window.handle_tactical_map_hold_event(repeat, True) is True
    assert tactical_map.enabled is True
    assert window.tactical_map_hold is True
    assert window.handle_tactical_map_hold_event(release, False) is True
    assert window.tactical_map_hold is False
    window.home_visible = False
    source = QImage(640, 360, QImage.Format.Format_RGB32)
    source.fill(QColor("#295a76"))
    source_painter = QPainter(source)
    source_painter.fillRect(0, 0, 320, 360, QColor("#193544"))
    source_painter.fillRect(320, 0, 320, 360, QColor("#537f6d"))
    source_painter.end()
    window.update_hud_liquid_source(source)
    assert "hud" in window.liquid_glass.snapshots
    tactical_map.get_setting("Status Display").value = "LiquidCards"
    hud = window.registry.get("HUD")
    assert hud is not None
    hud.get_setting("ModuleList Style").value = "LiquidArrayDock"
    rendered.fill(0)
    window.render(rendered)
    assert any(bytes(rendered.constBits()))
    close_window_safely(window, app)


def test_liquid_glass_snapshot_stability() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from rm_client.ui.liquid_glass import LiquidGlassRenderer

    app = QApplication.instance() or QApplication([])
    background = QImage(320, 180, QImage.Format.Format_RGB32)
    background.fill(QColor("#245a83"))

    def render() -> bytes:
        renderer = LiquidGlassRenderer()
        renderer.update("startup", background, QRectF(0, 0, 320, 180))
        image = QImage(320, 180, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#101820"))
        painter = QPainter(image)
        renderer.draw(painter, QRectF(40, 30, 180, 100), 14, source="startup", mode="prominent")
        painter.end()
        return bytes(image.constBits())

    assert render() == render()
    renderer = LiquidGlassRenderer()
    renderer.update("hud", background, QRectF(0, 0, 320, 180))
    renderer.light_state[("hud", 1, 2, 3, 4)] = 1.0
    renderer.light_time[("hud", 1, 2, 3, 4)] = time.perf_counter()
    renderer.discard("hud")
    assert "hud" not in renderer.snapshots
    assert not any(key[0] == "hud" for key in renderer.light_state)
    assert not any(key[0] == "hud" for key in renderer.light_time)
    app.processEvents()


def test_gpu_glass_commands_do_not_accumulate_between_frames() -> None:
    from PySide6.QtCore import QRectF

    from rm_client.ui.gpu_frame_compositor import GpuFrameCompositor

    compositor = GpuFrameCompositor()
    compositor.begin_frame(rebuild_static_ui=True)
    compositor.begin_static_glass()
    compositor.add_glass(QRectF(10, 10, 120, 40), 20, surface="main")
    compositor.end_static_glass()
    compositor.add_glass(QRectF(200, 10, 160, 30), 15, surface="sub")
    assert len(compositor._static_glass_commands) == 1
    assert len(compositor._frame_glass_commands) == 1

    compositor.begin_frame(rebuild_static_ui=False)
    assert len(compositor._static_glass_commands) == 1
    assert len(compositor._frame_glass_commands) == 0
    compositor.add_glass(QRectF(220, 10, 160, 30), 15, surface="sub")
    assert len(compositor._frame_glass_commands) == 1

    compositor.begin_frame(rebuild_static_ui=True)
    assert len(compositor._static_glass_commands) == 0
    assert len(compositor._frame_glass_commands) == 0


def test_dynamic_texture_upload_restores_qt_text_pixel_store_state() -> None:
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QImage

    from rm_client.ui import gpu_frame_compositor as compositor_module

    class FakeFramebuffer:
        @staticmethod
        def texture() -> int:
            return 73

    class FakeFunctions:
        def __init__(self) -> None:
            self.active_units: list[int] = []
            self.texture_bindings: list[int] = []
            self.pixel_store: list[tuple[int, int]] = []
            self.upload_count = 0

        def glActiveTexture(self, unit: int) -> None:
            self.active_units.append(unit)

        def glBindTexture(self, _target: int, texture: int) -> None:
            self.texture_bindings.append(texture)

        def glPixelStorei(self, name: int, value: int) -> None:
            self.pixel_store.append((name, value))

        def glTexSubImage2D(self, *_args) -> None:
            self.upload_count += 1

    compositor = compositor_module.GpuFrameCompositor()
    compositor.dynamic_fbo = FakeFramebuffer()
    compositor._size = QSize(64, 64)
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 255, 255, 128))
    functions = FakeFunctions()

    assert compositor.upload_dynamic_image(functions, image, 4, 5)
    assert functions.upload_count == 1
    assert functions.texture_bindings[-1] == 0
    assert functions.active_units[-1] == compositor_module._GL_TEXTURE0
    assert functions.pixel_store[-2:] == [
        (compositor_module._GL_UNPACK_ALIGNMENT, 4),
        (compositor_module._GL_PACK_ALIGNMENT, 4),
    ]


def test_hud_liquid_glass_snapshot_stability() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from rm_client.ui.polar_app import PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = _build_settled_window(PolarRMWindow, app)
    window.resize(640, 360)
    source = QImage(640, 360, QImage.Format.Format_RGB32)
    source.fill(QColor("#234c68"))
    window.update_hud_liquid_source(source)

    def render() -> bytes:
        image = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#0d1520"))
        painter = QPainter(image)
        window.draw_hud_liquid_glass(painter, QRectF(30, 40, 180, 82), 8.0, surface="MainGlass")
        painter.end()
        return bytes(image.constBits())

    try:
        assert render() == render()
    finally:
        close_window_safely(window, app)
    app.processEvents()


def test_android_liquid_spring_is_refresh_rate_invariant() -> None:
    def sample(hz: int, *, ratio: float, reverse: bool = False) -> tuple[float, float]:
        value = 0.0
        velocity = 0.0
        dt = 1.0 / float(hz)
        for _ in range(hz // 2):
            value, velocity = step_damped_spring(
                value,
                velocity,
                1.0,
                dt,
                stiffness=300.0,
                damping_ratio=ratio,
            )
        if reverse:
            for _ in range(hz // 2):
                value, velocity = step_damped_spring(
                    value,
                    velocity,
                    0.0,
                    dt,
                    stiffness=300.0,
                    damping_ratio=1.0,
                )
        return value, velocity

    for ratio in (0.5, 0.62, 1.0):
        reference = sample(240, ratio=ratio)
        for hz in (60, 90, 120, 144):
            actual = sample(hz, ratio=ratio)
            assert abs(actual[0] - reference[0]) < 1.0e-9
            assert abs(actual[1] - reference[1]) < 1.0e-8

    reference_reverse = sample(240, ratio=0.5, reverse=True)
    for hz in (60, 90, 120, 144):
        actual = sample(hz, ratio=0.5, reverse=True)
        assert abs(actual[0] - reference_reverse[0]) < 1.0e-9
        assert abs(actual[1] - reference_reverse[1]) < 1.0e-8

    # A missed presentation must advance by elapsed wall time rather than
    # stretching the remainder of the animation into visible slow motion.
    long_frame = step_damped_spring(
        0.0,
        0.0,
        1.0,
        0.18,
        stiffness=300.0,
        damping_ratio=0.5,
    )
    split_value = 0.0
    split_velocity = 0.0
    for _ in range(18):
        split_value, split_velocity = step_damped_spring(
            split_value,
            split_velocity,
            1.0,
            0.01,
            stiffness=300.0,
            damping_ratio=0.5,
        )
    assert abs(long_frame[0] - split_value) < 1.0e-9
    assert abs(long_frame[1] - split_velocity) < 1.0e-8


def test_clickgui_offscreen_modes() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from rm_client.ui.polar_app import PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = _build_settled_window(PolarRMWindow, app)
    window.resize(1280, 720)
    window.home_visible = False
    window.gui_visible = True
    try:
        for mode, draw in (
            ("PolarPanel", window.draw_panel_gui),
            ("PolarOld", window.draw_old_gui),
            ("PolarNew", window.draw_new_gui),
            ("PolarDropdown", window.draw_dropdown_gui),
        ):
            window.mode_manager.set_mode(mode)
            window.gui_motion.begin_frame()
            image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(0, 0, 0, 0))
            painter = QPainter(image)
            window.hotspots.clear()
            draw(painter, 1280, 720, 1.0)
            painter.end()
            assert any(bytes(image.constBits())), mode
            assert window.hotspots, mode
        for mode in GUI_MODES:
            window.mode_manager.set_mode(mode)
            window.gui_motion.begin_frame()
            window.hotspots.clear()
            draw_list = window.build_gpu_clickgui(1280, 720, 1.0)
            assert draw_list.rects, mode
            assert draw_list.texts, mode
            assert draw_list.images, mode
            assert window.hotspots, mode
        for mode in LEGACY_GUI_MODES:
            assert window.jello_state.open_legacy(mode)
            window.gui_motion.begin_frame()
            window.hotspots.clear()
            draw_list = window.build_gpu_clickgui(1280, 720, 1.0)
            assert draw_list.rects, mode
            assert draw_list.texts, mode
            assert any(hotspot.action == "jello_return" for hotspot in window.hotspots), mode
        window.jello_state.return_to_jello()
    finally:
        close_window_safely(window, app)
    app.processEvents()


def test_jello_clickgui_layout_and_old_group() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
    from PySide6.QtGui import QFont, QKeyEvent
    from PySide6.QtWidgets import QApplication
    from rm_client.ui.fonts import ui_text_advance
    from rm_client.ui.jello_clickgui import JelloClickGuiState, JelloMainTimeline
    from rm_client.ui.jello_locale import description_label
    from rm_client.ui.polar_app import PolarRMWindow

    timeline = JelloMainTimeline()
    assert timeline.visual_factor() == 0.0
    timeline.set_target(1.0)
    assert abs(timeline.visual_factor() - 0.5) < 1e-6
    timeline.update(0.1125)
    assert abs(timeline.progress - 0.25) < 1e-6
    assert abs(timeline.blur_radius() - 20.0) < 1e-6
    timeline.reset(0.00625)
    assert timeline.blur_radius() == 1.0  # Java Math.round(0.5F)
    timeline.reset(0.25)
    timeline.set_target(1.0)
    opening_value = timeline.visual_factor()
    timeline.set_target(0.0)
    assert abs(timeline.visual_factor() - (2.0 * 0.25 - 0.25**2)) < 1e-6
    timeline.set_target(1.0)
    assert abs(timeline.visual_factor() - opening_value) < 1e-6
    timeline.update(0.3375)
    assert abs(timeline.progress - 1.0) < 1e-6
    timeline.set_target(0.0)
    timeline.update(0.0625)
    assert abs(timeline.progress - 0.5) < 1e-6
    assert abs(timeline.visual_factor() - 0.75) < 1e-6
    timeline.update(0.0625)
    assert timeline.progress == 0.0

    # Sigma's VerticalScrollBar uses a literal 35 px wheel step in the host,
    # a 0.05-per-frame fade, and stays active for 500 ms after wheel input.
    scrollbar_state = JelloClickGuiState()
    scrollbar_state.mark_scroll("panel:System", now=10.0)
    assert abs(
        scrollbar_state.scrollbar_fade(
            "panel:System", scrollable=True, hovered=False, now=10.0
        )
        - 0.05
    ) < 1e-6
    for _ in range(19):
        scrollbar_state.scrollbar_fade(
            "panel:System", scrollable=True, hovered=False, now=10.49
        )
    assert scrollbar_state._scrollbar_fades["panel:System"] == 1.0
    assert scrollbar_state.scrollbar_fade(
        "panel:System", scrollable=True, hovered=False, now=10.50
    ) == 0.95
    assert scrollbar_state.scrollbar_fade(
        "panel:System", scrollable=False, hovered=False, now=10.50
    ) == 0.0

    app = QApplication.instance() or QApplication([])
    window = _build_settled_window(PolarRMWindow, app)
    try:
        # This visual contract starts in English regardless of the operator's
        # persisted default profile. Language toggling itself is tested below.
        window.registry.get("ClickGui").get_setting("Language").value = LANGUAGE_ENGLISH
        assert window.font("jello_light", 12.0).weight() == QFont.Weight.Light
        assert window.font("jello_medium", 12.0).weight() == QFont.Weight.Medium
        for width, height in ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440)):
            layout = window.jello_clickgui.layout(width, height, 1.0)
            assert set(layout.panels) == set(window.jello_state.panel_scroll)
            assert layout.panels["Combat"] == QRectF(30.0, 30.0, 200.0, 320.0)
            assert layout.panels["Movement"] == QRectF(240.0, 30.0, 200.0, 320.0)
            assert layout.panels["Vision"] == QRectF(450.0, 30.0, 200.0, 320.0)
            assert layout.panels["Strategy"] == QRectF(660.0, 30.0, 200.0, 320.0)
            assert layout.panels["System"] == QRectF(30.0, 360.0, 200.0, 320.0)
            assert layout.panels["Render"] == QRectF(240.0, 360.0, 200.0, 320.0)
            assert layout.options == QRectF(width - 69.0, height - 55.0, 55.0, 41.0)
            for panel in layout.panels.values():
                assert panel.left() >= 0.0 and panel.top() >= 0.0
                assert panel.right() <= width + 0.01
                assert panel.bottom() <= height + 0.01
            assert layout.options.right() <= width + 0.01
            assert layout.options.bottom() <= height + 0.01
        expected_caps = {
            (1000, 620): 1000.0 / 1280.0,
            (1280, 720): 1.0,
            (1600, 900): 1.25,
            (1920, 1080): 1.5,
            (2560, 1440): 2.0,
        }
        for (width, height), expected in expected_caps.items():
            scaled = window.jello_clickgui.layout(width, height, 2.0)
            assert abs(scaled.scale - expected) < 1e-6
            for panel in scaled.panels.values():
                assert panel.left() >= 0.0 and panel.top() >= 0.0
                assert panel.right() <= width + 0.01
                assert panel.bottom() <= height + 0.01
            assert scaled.options.right() <= width + 0.01
            assert scaled.options.bottom() <= height + 0.01
        raster_pages = {
            window.jello_clickgui.raster_scale_for(0.50 + index * 0.05)
            for index in range(31)
        }
        assert raster_pages == {0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0}
        for asset in window.jello_clickgui.image_assets:
            assert asset.is_file() and asset.stat().st_size > 0
        hud_module = window.registry.get("HUD")
        hud_fonts = window._jello_gpu_fonts()
        hud_pages = window._jello_module_text_pages(
            hud_module,
            hud_fonts,
        )

        def assert_description_warmed(text: str, size: float, width: float) -> None:
            # PR17 prewarms the actual lossless wrapped lines. Requiring one
            # unwrapped string would reject correct multi-line descriptions.
            for value in (text, description_label(text, True)):
                lines = window.jello_clickgui.wrapped_description(hud_fonts[size], value, width)
                assert "".join(lines) == value, (size, value, lines)
                assert set(lines).issubset(hud_pages[size]), (size, value, lines)
                assert all(
                    ui_text_advance(hud_fonts[size], line.rstrip()) <= width + 0.01
                    for line in lines
                ), (size, width, lines)

        assert hud_module.name in hud_pages[40.0]
        assert_description_warmed(hud_module.description, 20.0, 440.0)
        for setting in hud_module.settings:
            assert setting.name in hud_pages[25.0]
            assert setting.name in hud_pages[14.0]
            assert_description_warmed(setting.description, 14.0, 460.0)

        window.resize(1280, 720)
        window.home_visible = False
        window.gui_visible = True
        window.gui_timeline.reset(1.0)
        window.gui_timeline.set_target(1.0)
        window.jello_state.begin_session()
        window.gui_motion.begin_frame(time.perf_counter() + 1.0)
        window.hotspots.clear()
        main = window.build_gpu_clickgui(1280, 720, 1.0)
        assert main.rects and main.texts and main.images
        assert all(command.preserve_weight for command in main.texts)
        assert {command.text_profile for command in main.texts} == {"jello"}
        assert {command.raster_scale for command in main.texts} == {1.0}
        assert {viewport["mode"] for viewport in window.gui_scroll_viewports} == {"jello_panel"}
        assert any(hotspot.action == "jello_open_settings" and hotspot.payload == "ClickGui" for hotspot in window.hotspots)
        language_hotspot = next(
            hotspot for hotspot in window.hotspots
            if hotspot.action == "jello_toggle_language"
        )
        assert any(text.text.endswith("· EN") for text in main.texts)
        assert jello_category_label("Combat", True) == "战斗"
        assert jello_module_label("ArmorAutoAim", True) == "装甲自瞄"
        assert jello_setting_label("ModuleList Alignment", True) == "功能列表对齐"
        assert jello_option_label("Unified GPU", True) == "统一 GPU"

        # The profile/default area is the one-click language switch.  It must
        # alter display strings only: module hotspot payloads stay stable keys.
        saved_languages: list[str] = []
        window.auto_save_config = lambda: saved_languages.append(
            str(window.registry.get("ClickGui").get_setting("Language").value)
        )
        window.dispatch(
            language_hotspot.action,
            language_hotspot.payload,
            QPointF(language_hotspot.rect.center()),
            1,
            language_hotspot.rect,
        )
        assert window.registry.get("ClickGui").get_setting("Language").value == LANGUAGE_CHINESE
        window.hotspots.clear()
        chinese_main = window.build_gpu_clickgui(1280, 720, 1.0)
        assert any(text.text == "战斗" for text in chinese_main.texts)
        assert any(text.text == "装甲自瞄" for text in chinese_main.texts)
        assert any(text.text.endswith("· 中文") for text in chinese_main.texts)
        assert any(
            hotspot.action == "jello_module_press" and hotspot.payload == "ArmorAutoAim"
            for hotspot in window.hotspots
        )
        language_hotspot = next(
            hotspot for hotspot in window.hotspots
            if hotspot.action == "jello_toggle_language"
        )
        window.dispatch(
            language_hotspot.action,
            language_hotspot.payload,
            QPointF(language_hotspot.rect.center()),
            1,
            language_hotspot.rect,
        )
        assert window.registry.get("ClickGui").get_setting("Language").value == LANGUAGE_ENGLISH
        assert saved_languages == [LANGUAGE_CHINESE, LANGUAGE_ENGLISH]

        class RoutePool:
            def __init__(self) -> None:
                self.played: list[tuple[str, float]] = []

            def play(self, name: str, volume: float) -> None:
                self.played.append((name, volume))

        original_pool = window.ui_audio.pool
        routed_pool = RoutePool()
        window.ui_audio.pool = routed_pool
        window.on_module_toggled(window.registry.get("ArmorAutoAim"), True, "clickgui")
        window.on_module_toggled(window.registry.get("ArmorAutoAim"), False, "clickgui")
        assert routed_pool.played[-2:] == [
            ("jello/activate.mp3", 45.0),
            ("jello/deactivate.mp3", 45.0),
        ]
        window.ui_audio.pool = original_pool

        window.jello_state.open_settings("ClickGui")
        window.jello_state.update(0.30)
        window.jello_state.toggle_old_group()
        window.jello_state.update(0.30)
        window.gui_motion.begin_frame(time.perf_counter() + 2.0)
        window.hotspots.clear()
        window.build_gpu_clickgui(1280, 720, 1.0)
        settings_viewport = next(
            viewport
            for viewport in window.gui_scroll_viewports
            if viewport["mode"] == "jello_settings"
        )
        window.jello_state.settings_scroll["ClickGui"] = settings_viewport["maximum"]
        window.hotspots.clear()
        settings_draw = window.build_gpu_clickgui(1280, 720, 1.0)
        old_actions = [
            hotspot.payload
            for hotspot in window.hotspots
            if hotspot.action == "jello_open_legacy"
        ]
        assert old_actions == LEGACY_GUI_MODES
        assert any(viewport["mode"] == "jello_settings" for viewport in window.gui_scroll_viewports)
        # Child zoom is a draw transform, exactly as in Sigma's matrix stack;
        # it must not allocate a new glyph atlas at every animation frame.
        assert {
            round(text.font.pointSizeF(), 2)
            for text in [*main.texts, *settings_draw.texts]
            if "Helvetica" in text.font.family()
        } <= {10.5, 13.5, 15.0, 18.75, 30.0}
        modal = next(
            rect.rect
            for rect in settings_draw.rects
            if abs(rect.rect.width() - 500.0) < 0.01
            and abs(rect.rect.height() - 504.0) < 0.01
        )
        assert modal == QRectF(390.0, 128.0, 500.0, 504.0)

        # The exact mode widget opens a vertically animated dropdown and uses
        # an actual option hotspot rather than Polar's click-to-cycle control.
        window.jello_state.toggle_old_group()
        window.jello_state.update(0.30)
        window.hotspots.clear()
        window.build_gpu_clickgui(1280, 720, 1.0)
        mode_hotspot = next(
            hotspot for hotspot in window.hotspots
            if hotspot.action == "jello_mode_dropdown"
            and hotspot.payload[1].name == "Render Backend"
        )
        window.dispatch(
            mode_hotspot.action,
            mode_hotspot.payload,
            QPointF(mode_hotspot.rect.center()),
            1,
            mode_hotspot.rect,
        )
        # Opening the popup must not switch the complete ClickGUI from device
        # pixel alignment to sub-pixel text. Only the rotating arrow moves.
        window.jello_state.update(0.05)
        window.hotspots.clear()
        transition_dropdown = window.build_gpu_clickgui(1280, 720, 1.0)
        rotating_arrows = [
            text
            for text in transition_dropdown.texts
            if text.text == ">" and abs(text.rotation_degrees) > 0.01
        ]
        assert len(rotating_arrows) == 1
        assert rotating_arrows[0].device_pixel_snap is False
        outside_modal = [
            text
            for text in transition_dropdown.texts
            if not QRectF(390.0, 128.0, 500.0, 504.0).contains(text.rect.center())
            and text.text != ">"
        ]
        assert outside_modal and all(text.device_pixel_snap for text in outside_modal)

        # One continuous white backplate sits behind the option rows. It seals
        # the one-pixel AA seam that previously exposed Save Layout's blue UI.
        popup_seals = [
            rect
            for rect in transition_dropdown.rects
            if abs(rect.rect.width() - mode_hotspot.rect.width()) < 0.01
            and rect.rect.height() > 54.0
            and rect.clip is not None
            and rect.fill.red() >= 250
            and rect.fill.green() >= 250
            and rect.fill.blue() >= 250
            and rect.fill.alpha() >= 250
        ]
        assert popup_seals
        window.jello_state.update(0.30)
        window.hotspots.clear()
        window.build_gpu_clickgui(1280, 720, 1.0)
        assert {
            hotspot.payload[1]
            for hotspot in window.hotspots
            if hotspot.action == "jello_mode_select"
        } == {"Unified GPU", "Compatibility Painter"}
        window.jello_state.close_dropdown(immediate=True)

        # HUD contains several mode rows.  The retained dropdown_key must own
        # the only animated arrow/popup throughout both open and close legs;
        # later rows may not steal it (the historical disappearing-triangle
        # regression).
        window.jello_state.open_settings("HUD")
        window.jello_state.update(0.30)
        window.hotspots.clear()
        window.build_gpu_clickgui(1280, 720, 1.0)
        hud_mode_rows = [
            hotspot
            for hotspot in window.hotspots
            if hotspot.action == "jello_mode_dropdown"
        ]
        assert len(hud_mode_rows) >= 2
        target_mode = hud_mode_rows[0]
        target_key, target_setting = target_mode.payload
        assert target_mode.tooltip == str(target_setting.value)
        window.dispatch(
            target_mode.action,
            target_mode.payload,
            QPointF(target_mode.rect.center()),
            1,
            target_mode.rect,
        )
        scroll_before = float(window.jello_state.settings_scroll.get("HUD", 0.0))
        window.jello_state.update(0.12)
        window.hotspots.clear()
        hud_dropdown = window.build_gpu_clickgui(1280, 720, 1.0)
        # Layout only requests a spring target; it may not teleport every row
        # in the settings viewport during this build.
        scroll_after_layout = float(window.jello_state.settings_scroll.get("HUD", 0.0))
        assert abs(scroll_after_layout - scroll_before) < 1.0e-6
        assert window.jello_state.settings_scroll_target("HUD") >= scroll_after_layout
        popup_direction = window.jello_state.dropdown_opens_up
        window.jello_state.update(1.0 / 144.0)
        window.hotspots.clear()
        hud_dropdown = window.build_gpu_clickgui(1280, 720, 1.0)
        scroll_after_frame = float(window.jello_state.settings_scroll.get("HUD", 0.0))
        assert scroll_after_layout <= scroll_after_frame < scroll_after_layout + 8.0
        assert window.jello_state.dropdown_opens_up == popup_direction
        assert window.jello_state.dropdown_key == target_key
        popup_settings = {
            id(hotspot.payload[0])
            for hotspot in window.hotspots
            if hotspot.action == "jello_mode_select"
        }
        assert popup_settings == {id(target_setting)}
        assert sum(
            text.text == ">" and abs(text.rotation_degrees) > 0.01
            for text in hud_dropdown.texts
        ) == 1
        window.jello_state.close_dropdown()
        window.jello_state.update(0.05)
        window.hotspots.clear()
        closing_dropdown = window.build_gpu_clickgui(1280, 720, 1.0)
        assert window.jello_state.dropdown_key == target_key
        assert sum(
            text.text == ">" and abs(text.rotation_degrees) > 0.01
            for text in closing_dropdown.texts
        ) == 1
        window.jello_state.update(0.30)
        assert window.jello_state.dropdown_key is None

        assert window.open_legacy_clickgui("VapeUnified")
        assert window.active_clickgui_mode() == "PolarUnified"
        assert window.current_config_data()["gui_mode"] == PRIMARY_GUI_MODE
        assert "Old ClickGUIs" not in window.current_config_data()["modules"]["ClickGui"]["settings"]
        window.execute_quick_action("clickgui")
        assert window.active_clickgui_mode() == PRIMARY_GUI_MODE
        assert window.open_legacy_clickgui("VapeUnified")
        window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert window.active_clickgui_mode() == PRIMARY_GUI_MODE
        assert window.jello_state.settings_module is None
        assert window.gui_visible is True
        window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert window.gui_visible is False

        window.set_clickgui_visible(True, announce=False, play_sound=False)
        window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F9, Qt.KeyboardModifier.NoModifier))
        assert window.active_clickgui_mode() == PRIMARY_GUI_MODE
        hero_module = window.registry.get("HeroLob")
        hero_module.enabled = True
        hero_module.keybind = "K"
        window.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.NoModifier)
        )
        assert window.hero_deploy_ui.visible and not window.gui_visible
        assert window.overlay_coordinator.modal == OverlayKind.HERO_DEPLOY
        window.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.NoModifier)
        )
        assert not window.hero_deploy_ui.visible
        window.set_clickgui_visible(True, announce=False, play_sound=False)
        stress_font_pages: set[tuple[str, float, int]] = set()
        for index in range(100):
            window.jello_state.begin_session()
            window.gui_timeline.reset((index % 10) / 10.0)
            window.gui_timeline.set_target(1.0)
            stress_draw = window.build_gpu_clickgui(1280, 720, window.gui_timeline.progress)
            stress_font_pages.update(
                (
                    text.font.family(),
                    round(text.font.pointSizeF(), 2),
                    int(text.font.weight()),
                )
                for text in stress_draw.texts
                if "Helvetica" in text.font.family()
            )
            assert window.jello_state.open_legacy("PolarOld")
            window.jello_state.request_reset()
            window.jello_state.finish_reset()
            assert window.jello_state.legacy_mode is None
        assert len(stress_font_pages) <= 3
    finally:
        close_window_safely(window, app)
    app.processEvents()


def test_prediction_geometry_is_bounded() -> None:
    """未标定 PnP 的异常投影不能重新变成横跨画面的色块。"""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF
    from rm_client.ui.polar_app import PolarRMWindow

    reference = [
        QPointF(100, 100), QPointF(180, 104),
        QPointF(178, 142), QPointF(102, 138),
    ]
    oversized = [
        QPointF(-240, 80), QPointF(520, 84),
        QPointF(510, 178), QPointF(-230, 174),
    ]
    oversized_back = [point + QPointF(8, 7) for point in oversized]
    front, back = PolarRMWindow.constrain_prediction_geometry(oversized, oversized_back, reference)
    front_box = QPolygonF(front).boundingRect()
    reference_box = QPolygonF(reference).boundingRect()
    assert front_box.width() <= reference_box.width() * 1.35 + 0.01
    assert front_box.height() <= reference_box.height() * 1.45 + 0.01
    assert len(back) == 4


def test_robot_profiles_and_economy_rules() -> None:
    assert [profile.key for profile in profiles_for_match("RMUL3v3")] == ["Hero", "Infantry1", "Sentinel"]
    assert len(profiles_for_match("RMUC7v7")) == 8
    hero = resolve_performance("RMUC7v7", "Hero", level=10, hero_type="Melee")
    assert (hero.max_health, hero.power_limit_w, hero.heat_limit, hero.cooling_rate) == (450, 120, 240, 30)
    infantry = resolve_performance(
        "RMUL3v3", "Infantry1", infantry_chassis="Health", infantry_shooter="Cooling"
    )
    assert (infantry.max_health, infantry.power_limit_w, infantry.heat_limit, infantry.cooling_rate) == (350, 75, 88, 24)

    state = RobotState()
    profile = profile_by_key("RMUC7v7", "Infantry2")
    apply_profile_to_state(state, profile, resolve_performance("RMUC7v7", "Infantry2"), 1)
    assert state.robot_id == 4
    assert state.projectile_type == "17mm"
    assert state.ammo_capacity == 180
    assert state.remaining_ammo == 180
    state.remaining_economy = 500
    state.total_economy_obtained = 700
    state.stage_countdown_sec = 300
    state.in_supply_zone = True
    state.supply_zone_known = True
    state.out_of_combat = True
    state.can_remote_ammo = True
    state.can_remote_heal = True
    state.economy_mock_mode = True

    state.match_mode = "RMUL3v3"
    state.robot_profile = "Hero"
    rmul_hero = {offer.key: offer for offer in available_actions(state)}
    assert (rmul_hero["rmul_hero_42_1"].minimum, rmul_hero["rmul_hero_42_1"].cost) == (1, 10)
    assert (rmul_hero["rmul_hero_42_10"].minimum, rmul_hero["rmul_hero_42_10"].cost) == (10, 100)

    state.robot_profile = "Infantry1"
    rmul_infantry = {offer.key: offer for offer in available_actions(state)}
    assert (rmul_infantry["rmul_infantry_17_10"].minimum, rmul_infantry["rmul_infantry_17_10"].cost) == (10, 10)
    assert (rmul_infantry["rmul_infantry_17_100"].minimum, rmul_infantry["rmul_infantry_17_100"].cost) == (100, 100)

    state.robot_profile = "Sentinel"
    rmul_sentinel = available_actions(state)
    # In mock mode, revive offer is visible even when alive (disabled).
    assert len(rmul_sentinel) == 2
    sentinel_map = {offer.key: offer for offer in rmul_sentinel}
    assert sentinel_map["rmul_sentinel_ammo_status"].status_only is True
    assert sentinel_map["rmul_sentinel_ammo_status"].enabled is False
    assert sentinel_map["rmul_auto_respawn"].status_only is True
    assert sentinel_map["rmul_auto_respawn"].enabled is False

    state.match_mode = "RMUC7v7"
    state.robot_profile = "Hero"
    state.current_health = state.max_health
    rmuc_hero = {offer.key: offer for offer in available_actions(state)}
    assert (rmuc_hero["rmuc_hero_42_1"].minimum, rmuc_hero["rmuc_hero_42_1"].cost) == (1, 10)
    assert (rmuc_hero["rmuc_hero_42_10"].minimum, rmuc_hero["rmuc_hero_42_10"].cost) == (10, 100)
    assert (rmuc_hero["rmuc_hero_remote_42_10"].minimum, rmuc_hero["rmuc_hero_remote_42_10"].cost) == (10, 150)
    assert rmuc_hero["rmuc_remote_heal"].confirmation == "hold"

    state.robot_profile = "Infantry2"
    rmuc_infantry = {offer.key: offer for offer in available_actions(state)}
    assert (rmuc_infantry["rmuc_infantry_17_10"].minimum, rmuc_infantry["rmuc_infantry_17_10"].cost) == (10, 10)
    assert (rmuc_infantry["rmuc_infantry_17_100"].minimum, rmuc_infantry["rmuc_infantry_17_100"].cost) == (100, 100)
    assert (rmuc_infantry["rmuc_infantry_remote_17_100"].minimum, rmuc_infantry["rmuc_infantry_remote_17_100"].cost) == (100, 150)
    state.in_supply_zone = False
    state.supply_zone_known = False
    unknown_zone = {offer.key: offer for offer in available_actions(state)}["rmuc_infantry_17_10"]
    assert unknown_zone.enabled is True and unknown_zone.estimated is False
    state.supply_zone_known = True
    known_outside = {offer.key: offer for offer in available_actions(state)}["rmuc_infantry_17_10"]
    assert known_outside.enabled is False and known_outside.estimated is False
    state.in_supply_zone = True

    state.robot_profile = "Engineer"
    state.current_health = state.max_health
    # In mock mode, revive offers are visible even when alive (but disabled).
    engineer_alive = {offer.key: offer for offer in available_actions(state)}
    assert set(engineer_alive) == {"rmuc_confirm_respawn", "rmuc_gold_respawn"}
    assert all(not offer.enabled for offer in engineer_alive.values())
    state.current_health = 0
    state.respawn_can_free = True
    state.respawn_can_pay = True
    state.respawn_gold_cost = 137
    engineer_revive = {offer.key: offer for offer in available_actions(state)}
    assert set(engineer_revive) == {"rmuc_confirm_respawn", "rmuc_gold_respawn"}
    assert engineer_revive["rmuc_gold_respawn"].cost == 137
    assert engineer_revive["rmuc_gold_respawn"].estimated is False

    state.robot_profile = "Aerial"
    state.current_health = state.max_health
    state.air_support_active = False
    state.air_support_can_free = True
    air_support = {offer.key: offer for offer in available_actions(state)}
    assert set(air_support) == {"rmuc_air_support_paid", "rmuc_air_support_free"}
    assert all(offer.topic == "AirSupportCommand" for offer in air_support.values())

    assert encode_common_command(1, 10) == b"\x08\x01\x10\x0a"
    assert encode_common_command(5, 150) == b"\x08\x05\x10\x96\x01"
    assert encode_air_support_command(2) == b"\x08\x02"

    state.robot_profile = "Infantry2"
    state.current_health = state.max_health
    state.remaining_economy = 500
    state.remaining_ammo = 180
    state.ammo_capacity = 180
    state.economy_status_revision = 0
    state.economy_command_status = ""
    mock_offer = {offer.key: offer for offer in available_actions(state)}["rmuc_infantry_17_10"]
    bridge = EconomyCommandBridge()
    transaction = bridge.submit(state, mock_offer, mock=True, now=10.0)
    assert transaction.request.mock is True
    assert transaction.request.command_type == 1
    assert transaction.request.command_param == 10
    assert state.remaining_economy == 500
    assert state.remaining_ammo == 180
    assert bridge.update(state, now=10.20).status == "pending"
    assert bridge.update(state, now=10.40).status == "succeeded"
    assert state.remaining_economy == 490
    assert state.remaining_ammo == 190

    state.remaining_economy = 500
    state.remaining_ammo = 180
    state.economy_status_revision = 5
    state.economy_command_status = "rejected"
    sent = []
    official = EconomyCommandBridge()
    official.sender = lambda request: sent.append(request) is None
    official_transaction = official.submit(state, mock_offer, mock=False, now=20.0)
    assert len(sent) == 1
    assert state.remaining_economy == 500
    assert state.remaining_ammo == 180
    assert official.update(state, now=20.10).status == "pending"
    state.economy_status_revision += 1
    assert official.update(state, now=20.20).status == "rejected"

    state.economy_command_status = ""
    state.economy_status_revision += 1
    accepted = EconomyCommandBridge()
    accepted.sender = lambda _request: True
    accepted_transaction = accepted.submit(state, mock_offer, mock=False, now=30.0)
    state.economy_status_revision += 1
    assert accepted.update(state, now=30.10).status == "pending"
    # Shared team gold may change because a teammate buys something; it must
    # not acknowledge our local projectile request.
    state.remaining_economy -= 10
    state.economy_status_revision += 1
    assert accepted.update(state, now=30.15).status == "pending"
    state.remaining_ammo += 10
    state.economy_status_revision += 1
    assert accepted.update(state, now=30.20).status == "succeeded"

    timed_out = EconomyCommandBridge()
    timed_out.sender = lambda _request: True
    timeout_transaction = timed_out.submit(state, mock_offer, mock=False, now=40.0)
    assert timed_out.update(state, now=45.01).status == "timed_out"
    assert "不会自动重试" in timeout_transaction.message

    hero_profile = profile_by_key("RMUC7v7", "Hero")
    apply_profile_to_state(state, hero_profile, resolve_performance("RMUC7v7", "Hero"), 1)
    assert state.projectile_type == "42mm"
    assert state.ammo_capacity == 12
    assert state.remaining_ammo == 12

    registry = build_default_registry()
    robot_module = registry.get("RobotProfile")
    economy_module = registry.get("EconomyPanel")
    assert robot_module is not None and robot_module.get_setting("Role") is not None
    assert economy_module is not None and economy_module.keybind == "B"
    assert economy_module.get_setting("Mock Requests").value is False
    assert registry.get("ProjectileShop") is not None
    assert registry.get("UIScale").get_setting("HUD Refresh Rate").value == "144"


def test_economy_workspace_top_anchor_and_reversal() -> None:
    layer = EconomyWorkspaceLayer()
    compact_start = layer.layout(1280, 720, 0.0)
    compact_island = layer.layout(1280, 720, 0.15)
    compact_reveal = layer.layout(1280, 720, 0.48)
    compact_categories = layer.layout(1280, 720, 0.62)
    compact_offers = layer.layout(1280, 720, 0.72)
    compact_detail = layer.layout(1280, 720, 0.84)
    compact_open = layer.layout(1280, 720, 1.0)
    desktop_open = layer.layout(1600, 900, 1.0)
    wide_open = layer.layout(1920, 1080, 1.0)

    # The sheet grows from a top island rather than drifting into a centered
    # modal. The source control-center layer starts 48dp above its resting
    # position, so the presentation geometry moves down continuously before
    # settling at the shared top anchor.
    assert compact_start.collapsed.top() == compact_open.target_panel.top()
    assert compact_start.panel.top() < compact_island.panel.top() < compact_open.panel.top()
    assert abs(compact_open.panel.top() - compact_open.target_panel.top()) < 0.01
    assert compact_island.panel.height() < compact_reveal.panel.height() < compact_open.panel.height()
    assert compact_island.body_progress < compact_reveal.body_progress < compact_open.body_progress
    # The sheet forms before its final-size content appears.  Categories,
    # offers, and detail deliberately reveal in that order instead of being
    # squeezed into the intermediate island geometry.
    assert compact_reveal.content_alpha == 0.0
    assert 0.0 < compact_categories.category_alpha <= 1.0
    assert compact_categories.offers_alpha == 0.0
    assert compact_offers.offers_alpha > 0.0
    assert compact_detail.detail_alpha > 0.0
    assert compact_categories.category_alpha >= compact_categories.offers_alpha >= compact_categories.detail_alpha
    assert compact_island.body_clip.height() < compact_reveal.body_clip.height() < compact_open.body_clip.height()
    assert compact_island.header_alpha > compact_island.content_alpha
    assert compact_open.grab_handle.width() == 36.0
    assert compact_open.compact is True
    assert desktop_open.compact is False
    assert desktop_open.three_column is False
    assert wide_open.three_column is True
    # Control Center modules use the 56dp inner-control height. Keeping these
    # surfaces at that height prevents a 24/48dp lens from collapsing into a
    # long horizontal smear at compact resolutions.
    assert compact_open.status_strip.height() == 56.0
    assert compact_open.tab_bar.height() == 56.0
    assert desktop_open.status_strip.height() == 56.0
    assert desktop_open.tab_bar.height() == 56.0
    assert wide_open.tab_bar.isEmpty()
    assert not wide_open.category_pane.isEmpty()
    assert wide_open.category_pane.right() < wide_open.offers_pane.left()
    assert wide_open.offers_pane.right() < wide_open.detail_pane.left()
    assert wide_open.detail_pane.right() <= wide_open.panel.right() + 0.01
    for pane in (
        compact_open.offers_pane,
        compact_open.detail_pane,
        compact_open.tab_bar,
        compact_open.status_strip,
        desktop_open.offers_pane,
        desktop_open.detail_pane,
        desktop_open.tab_bar,
        desktop_open.status_strip,
    ):
        assert pane.width() > 0.0 and pane.height() > 0.0

    state = EconomyIslandState()
    state._last_time = 0.0
    state.set_target(1.0, duration=0.62, now=0.0)
    for tick in range(1, 31):
        state.update(now=tick / 120.0)
    opening_progress = state.progress
    assert 0.15 < opening_progress <= 1.12

    # Reversing from the in-flight value preserves a continuous trajectory.
    state.set_target(0.0, duration=0.48, now=30 / 120.0)
    state.update(now=31 / 120.0)
    # A critically damped state may advance for a few display frames while it
    # dissipates opening velocity. It must remain bounded, then contract.
    assert -0.02 <= state.progress <= 1.12
    for tick in range(32, 62):
        state.update(now=tick / 120.0)
    assert state.progress < opening_progress
    for tick in range(62, 200):
        state.update(now=tick / 120.0)
    assert state.progress == 0.0
    assert state.velocity == 0.0

    # A second open at the same viewport must rebase the spring clock and
    # advance without relying on a resize to invalidate renderer state.
    state.set_target(1.0, duration=0.62, now=200 / 120.0)
    state.update(now=201 / 120.0)
    assert state.progress > 0.0
    for tick in range(202, 360):
        state.update(now=tick / 120.0)
    assert state.progress == 1.0
    assert state.velocity == 0.0

    # The reference Control Center applies one fixed 40% dim layer to the
    # complete backdrop, independently of the scene luminance.
    assert adaptive_backdrop_dim(0.22, 1.0) == 0.40
    assert adaptive_backdrop_dim(0.90, 0.5) == 0.20
    assert adaptive_backdrop_dim(0.90, 0.0) == 0.0


def test_control_center_backdrop_treatment_only_invalidates_blur_when_needed() -> None:
    from PySide6.QtCore import QRectF

    from rm_client.ui.gpu_frame_compositor import GpuFrameCompositor

    compositor = GpuFrameCompositor()
    assert compositor.set_backdrop_treatment(
        active=True,
        progress=0.1,
        blur_radius_px=4.0,
        dim_alpha=0.4,
        include_overlay=True,
    )
    full_rect = QRectF(20.0, 30.0, 152.0, 152.0)
    reveal_clip = QRectF(20.0, 80.0, 152.0, 102.0)
    compositor.add_glass(
        full_rect,
        34.0,
        material="LensStrong",
        refraction_height_px=24.0,
        refraction_amount_px=-48.0,
        chromatic_aberration=0.0,
        depth_effect=1.0,
        frost_amount=0.0,
        vibrancy_amount=1.0,
        clip_rect=reveal_clip,
    )
    command = compositor._frame_glass_commands[-1]
    # Reveal clipping must only become a scissor. Mutating the lens geometry
    # changes its SDF and makes the rounded module visibly jump while opening.
    assert command.rect == full_rect
    assert command.clip_rect == reveal_clip
    assert command.refraction_height_px == 24.0
    assert command.refraction_amount_px == -48.0
    assert command.chromatic_aberration == 0.0
    assert command.depth_effect == 1.0
    assert command.frost_amount == 0.0
    assert not compositor.set_backdrop_treatment(
        active=True,
        progress=0.8,
        blur_radius_px=4.0,
        dim_alpha=0.4,
        include_overlay=True,
    )
    assert compositor._backdrop_treatment.progress == 0.8
    assert compositor.set_backdrop_treatment(
        active=True,
        progress=0.8,
        blur_radius_px=6.0,
        dim_alpha=0.4,
        include_overlay=True,
    )
    assert compositor.set_backdrop_treatment(
        active=True,
        progress=0.8,
        blur_radius_px=6.0,
        dim_alpha=0.4,
        include_overlay=True,
        blur_kernel="jello_box20",
    )
    assert compositor._backdrop_treatment.blur_kernel == "jello_box20"
    compositor._blur_valid = True
    compositor._treated_blur_valid = True
    compositor.invalidate_transient_layers()
    assert not compositor._blur_valid
    assert not compositor._treated_blur_valid
    assert not compositor._frame_glass_commands
    assert not compositor._static_glass_commands
    assert not compositor._backdrop_treatment.active


def test_economy_respawn_close_and_reopen_restores_hud_scene() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from rm_client.ui.polar_app import EconomyScenePhase, PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = _build_settled_window(PolarRMWindow, app)
    window.timer.stop()
    try:
        window.home_visible = False
        window.state.match_mode = "RMUC7v7"
        window.state.robot_profile = "Infantry1"
        window.state.current_health = 0
        window.state.respawn_pending = True
        window.state.respawn_can_free = True
        window.state.economy_mock_mode = True
        window.open_economy_workspace("revive")
        offer = next(
            item
            for item in window.economy_offers("revive")
            if item.key == "rmuc_confirm_respawn"
        )
        transaction = window.economy_bridge.submit(
            window.state,
            offer,
            mock=True,
            now=10.0,
        )
        assert transaction.status == "pending"
        assert window.economy_bridge.update(window.state, now=10.5).status == "succeeded"
        assert window.state.current_health > 0

        # Respawn completion immediately turns the selected hold-to-confirm
        # offer into a disabled status row.  The first GPU command list after
        # that transition must still be complete; historically hold_progress
        # was undefined in this branch and paintGL fell back to camera-only.
        window.economy_selected_key = offer.key
        window.economy_island.progress = 1.0
        post_respawn_commands = window.build_gpu_economy_workspace(1280, 720)
        assert post_respawn_commands.commands
        assert any(command.text for command in post_respawn_commands.texts)

        # Even with a healthy GPU renderer, an outer compositor failure must
        # explicitly select the compatibility economy workspace rather than
        # return a frame containing only the camera.
        compat_calls: list[tuple[int, int]] = []
        original_compat = window.draw_economy_overlay_compat
        original_gpu_available = window.clickgui_gpu.available
        try:
            window.clickgui_gpu.available = True
            window.draw_economy_overlay_compat = (
                lambda _p, w, h, _alpha: compat_calls.append((w, h))
            )
            fallback_image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
            fallback_image.fill(0)
            fallback_painter = QPainter(fallback_image)
            window.paint_clickgui_overlay(
                fallback_painter,
                1280,
                720,
                force_economy_compat=True,
            )
            fallback_painter.end()
        finally:
            window.draw_economy_overlay_compat = original_compat
            window.clickgui_gpu.available = original_gpu_available
        assert compat_calls == [(1280, 720)]

        window._gpu_static_ui_key = ("stale-economy",)
        window._gpu_control_backdrop_key = ("stale-control",)
        window._gpu_blur_source_key = ("stale-blur",)
        window._gpu_motion_layer_ready = True
        window.close_economy_workspace(play_sound=False)
        first_close_generation = window._economy_generation
        assert window.economy_phase == EconomyScenePhase.CLOSING
        assert window.economy_stage_active()
        assert window.overlay_coordinator.modal == OverlayKind.ECONOMY
        assert window._gpu_static_ui_key is None
        assert window._gpu_control_backdrop_key is None
        assert window._gpu_blur_source_key is None
        assert not window._gpu_motion_layer_ready

        # The normal HUD is drawable immediately after close, and a second
        # open at the same viewport creates a fresh economy session.
        image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        window.resize(1280, 720)
        window.render(image)
        assert any(bytes(image.constBits()))
        window.open_economy_workspace("ammo")
        assert window.economy_visible
        assert window.economy_phase == EconomyScenePhase.OPENING
        assert window._economy_generation > first_close_generation
        assert window._economy_restore_pending_generation is None
        assert window.overlay_coordinator.modal == OverlayKind.ECONOMY
        # A callback from the first close generation cannot finalize the
        # reopened scene.
        window._economy_restore_pending_generation = first_close_generation
        window._finalize_economy_restore(first_close_generation)
        assert window.economy_phase == EconomyScenePhase.OPENING
        assert window.overlay_coordinator.modal == OverlayKind.ECONOMY
        window._economy_restore_pending_generation = None

        window.close_economy_workspace(play_sound=False)
        second_close_generation = window._economy_generation
        assert window.economy_phase == EconomyScenePhase.CLOSING
        assert window.overlay_coordinator.modal == OverlayKind.ECONOMY
        window.economy_island.progress = 0.0
        window._economy_restore_pending_generation = second_close_generation
        assert not window.economy_stage_active()
        window._finalize_economy_restore(second_close_generation)
        assert window.economy_phase == EconomyScenePhase.CLOSED
        assert window._economy_close_finalized
        assert window.overlay_coordinator.modal is None

        # A single public ClickGUI request made while Economy owns the modal
        # is queued behind the GL restore.  It must not steal input early and
        # must not require a second RightShift after the restored HUD presents.
        window.open_economy_workspace("ammo")
        window.set_clickgui_visible(True, announce=False, play_sound=False)
        queued_generation = window._economy_generation
        assert window.economy_phase == EconomyScenePhase.CLOSING
        assert window._economy_pending_overlay == OverlayKind.CLICKGUI
        assert window.overlay_coordinator.modal == OverlayKind.ECONOMY
        assert not window.gui_visible
        window.economy_island.progress = 0.0
        window._economy_restore_pending_generation = queued_generation
        window._finalize_economy_restore(queued_generation)
        app.processEvents()
        assert window.economy_phase == EconomyScenePhase.CLOSED
        assert window._economy_pending_overlay is None
        assert window.gui_visible
        assert window.overlay_coordinator.modal == OverlayKind.CLICKGUI
        window.set_clickgui_visible(False, announce=False, play_sound=False)
    finally:
        close_window_safely(window, app)
    app.processEvents()


def test_official_economy_status_decode() -> None:
    import struct

    def varint(value: int) -> bytes:
        output = bytearray()
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                output.append(byte | 0x80)
            else:
                output.append(byte)
                return bytes(output)

    def field(number: int, value: int) -> bytes:
        return varint(number << 3) + varint(value)

    feed = OfficialMatchFeed()
    _events, logistics = feed.consume(
        "GlobalLogisticsStatus",
        field(1, 320) + field(2, 780) + field(3, 2) + field(4, 1),
        alliance="Red",
        local_robot_id=3,
    )
    assert logistics["remaining_economy"] == 320
    assert logistics["total_economy_obtained"] == 780
    assert "_economy_revision_increment" not in logistics

    _events, respawn = feed.consume(
        "RobotRespawnStatus",
        field(1, 1) + field(2, 100) + field(3, 35) + field(4, 1) + field(5, 140) + field(6, 1),
        alliance="Red",
        local_robot_id=3,
    )
    assert respawn["respawn_pending"] is True
    assert respawn["respawn_progress"] == 0.35
    assert respawn["respawn_gold_cost"] == 140

    dynamic_payload = (
        field(1, 275)
        + varint((2 << 3) | 5)
        + struct.pack("<f", 42.5)
        + field(4, 63)
        + field(5, 88)
        + field(8, 24)
        + field(9, 156)
        + field(10, 1)
        + field(12, 1)
        + field(13, 1)
    )
    _events, dynamic = feed.consume(
        "RobotDynamicStatus",
        dynamic_payload,
        alliance="Red",
        local_robot_id=3,
    )
    assert dynamic["current_health"] == 275
    assert abs(dynamic["current_heat"] - 42.5) < 0.001
    assert dynamic["remaining_ammo"] == 156
    assert dynamic["out_of_combat"] is True
    assert dynamic["can_remote_heal"] is True
    assert dynamic["can_remote_ammo"] is True

    _events, air = feed.consume(
        "AirSupportStatusSync",
        field(1, 1) + field(2, 18) + field(3, 7),
        alliance="Red",
        local_robot_id=3,
    )
    assert air["air_support_active"] is True
    assert air["air_support_can_free"] is True
    assert air["air_support_cost_coins"] == 7


def test_robot_launcher_and_polar_inertial_scroll() -> None:
    from PySide6.QtWidgets import QApplication
    from rm_client.ui.polar_app import InertialScroll, PolarRMWindow

    motion = InertialScroll()
    motion.set_bounds(180.0)
    motion.add_wheel(72.0)
    samples = [motion.update(1.0 / 144.0) for _ in range(60)]
    assert samples[0] >= 0.0
    assert samples[-1] > samples[0]
    assert 0.0 <= motion.position <= 180.0

    app = QApplication.instance() or QApplication([])
    window = _build_settled_window(PolarRMWindow, app)
    window.resize(1280, 720)
    try:
        window.hotspots.clear()
        launcher = window.build_gpu_robot_profile_launcher(1280, 720, 1.0)
        assert launcher.rects and launcher.texts
        assert any(hotspot.action == "select_robot_profile" for hotspot in window.hotspots)

        window.jello_state.open_legacy("PolarUnified")
        window.gui_visible = True
        window.polar_unified_morph.reset(1.0)
        window.selected_category = "Render"
        window.selected_module = window.registry.get("EliminationFX")
        started = time.perf_counter()
        for index in range(8):
            window.gui_motion.begin_frame(started + index * 0.10)
            window.build_gpu_clickgui(1280, 720, 1.0)
        window.gui_motion.begin_frame(started + 0.81)
        window.gui_scroll_viewports.clear()
        window.build_gpu_clickgui(1280, 720, 1.0)
        modes = {str(viewport["mode"]) for viewport in window.gui_scroll_viewports}
        assert "polar_unified" in modes
        assert "polar_settings" in modes
    finally:
        close_window_safely(window, app)
    app.processEvents()


def test_small_text_policy_and_island_icon_alpha() -> None:
    from PySide6.QtGui import QFont

    policy = SmallTextPolicy()
    assert policy.sample_scale_for(12.0) == 2.0
    assert policy.sample_scale_for(18.0) == 2.0
    assert policy.sample_scale_for(19.0) == 1.0
    assert policy.sample_scale_for(40.0, preserve_weight=True) == 2.0
    assert abs(policy.sample_scale_for(80.0, preserve_weight=True) - 1.2) < 1e-6
    assert policy.sample_scale_for(120.0, preserve_weight=True) == 1.0
    light = QFont("Sans Serif")
    light.setWeight(QFont.Weight.Light)
    preserved = policy.prepare_font(
        light,
        14.0,
        policy.sample_scale_for(14.0),
        preserve_weight=True,
    )
    strengthened = policy.prepare_font(light, 14.0, policy.sample_scale_for(14.0))
    assert preserved.weight() == QFont.Weight.Light
    assert strengthened.weight() == QFont.Weight.Medium
    shaped = ClickGuiGpuRenderer()
    assert shaped._is_cjk_character("汉")
    assert shaped._is_cjk_character("，")
    assert not shaped._is_cjk_character("A")
    layout, advance, line_height, ascent = shaped._jello_text_layout(
        "Jello 字体 AV",
        policy.prepare_font(light, 28.0, 1.0, preserve_weight=True),
    )
    assert layout.cacheEnabled()
    assert advance > 0.0 and line_height > 0.0 and ascent >= 0.0
    assert island_icon_coverage(0.5, 0.5) == 1.0
    assert island_icon_coverage(0.0, 0.0) == 0.0
    assert island_icon_coverage(1.0, 0.0) == 0.0
    assert island_icon_coverage(0.0, 1.0) == 0.0
    assert island_icon_coverage(1.0, 1.0) == 0.0

    from rm_client.ui.polar_app import FrameScheduler

    scheduler = FrameScheduler()
    requested = [
        tick
        for tick in range(0, 71, 7)
        if scheduler.should_request(tick / 1000.0, True, 144.0)
    ]
    # A 7 ms Windows timer should request every callback at 144 Hz rather
    # than alternating 7/14 ms like the old relative-threshold scheduler.
    assert requested == list(range(0, 71, 7))


def test_texture_origin_and_combined_backdrop() -> None:
    from PySide6.QtGui import QColor, QImage

    image = QImage(2, 2, QImage.Format.Format_RGBA8888)
    image.setPixelColor(0, 0, QColor("#ff0000"))
    image.setPixelColor(1, 0, QColor("#00ff00"))
    image.setPixelColor(0, 1, QColor("#0000ff"))
    image.setPixelColor(1, 1, QColor("#ffffff"))
    uploaded = normalize_image_for_texture(image, TextureOrigin.TOP_LEFT_IMAGE)
    assert uploaded.pixelColor(0, 0) == QColor("#0000ff")
    assert uploaded.pixelColor(1, 0) == QColor("#ffffff")
    assert uploaded.pixelColor(0, 1) == QColor("#ff0000")
    preserved = normalize_image_for_texture(image, TextureOrigin.BOTTOM_LEFT_FBO)
    assert preserved.pixelColor(0, 0) == QColor("#ff0000")

    combined = compose_combined_backdrop_pixel((0.2, 0.4, 0.6), (0.4, 0.1, 0.0, 0.5))
    assert combined == (0.5, 0.30000000000000004, 0.3)


def test_island_animation_reversal_and_hud_stack() -> None:
    timeline = ReversibleExpoTimeline(duration=0.22)
    timeline.set_target(1.0)
    for _ in range(7):
        timeline.update(1.0 / 120.0)
    before_reverse = timeline.progress
    assert 0.0 < before_reverse < 1.0
    timeline.set_target(0.0)
    first_reverse = timeline.update(1.0 / 120.0)
    assert 0.0 <= first_reverse < before_reverse
    assert before_reverse - first_reverse < 0.25

    island = IslandContainerState(response_per_60hz_frame=0.20)
    island.set_target_geometry(10.0, 20.0, 220.0, 42.0)
    island.update(1.0 / 60.0)
    island.set_target_geometry(30.0, 40.0, 520.0, 280.0)
    island.set_content_target(1.0, 0.22)
    island.update(1.0 / 60.0)
    assert 220.0 < island.width < 520.0
    assert 42.0 < island.height < 280.0
    assert island.radius == island.target_radius or island.radius < island.target_radius

    tactical = MapIslandState()
    tactical.set_target(12.0, 58.0, 220.0, 170.0, False)
    tactical.update(1.0 / 60.0)
    tactical.set_target(12.0, 58.0, 520.0, 380.0, True)
    tactical.update(1.0 / 60.0)
    expanded_progress = tactical.progress
    tactical.set_target(12.0, 58.0, 220.0, 170.0, False)
    tactical.update(1.0 / 120.0)
    assert 0.0 <= tactical.progress < expanded_progress

    stack = HudAnchorStack(22.0, 680.0, 8.0)
    target = stack.take(160.0, 42.0)
    calibration = stack.take(180.0, 18.0)
    kill_vision = stack.take(190.0, 18.0)
    assert calibration.bottom() + 8.0 <= target.top()
    assert kill_vision.bottom() + 8.0 <= calibration.top()


def test_combat_target_hud_math_and_rendering() -> None:
    assert ring_end(0.0) == 180.0
    assert ring_end(50.0) == 360.0
    assert ring_end(100.0) == 540.0
    assert abs(approach_jar(100.0, 0.0, 1.0 / 60.0) - 85.0) < 1e-9
    assert abs(approach_bar(50.0, 52.0, 1.0 / 60.0) - 50.8) < 1e-9
    assert abs(ghost_follow(100.0, 80.0, 1.0 / 60.0, 12.0) - 98.8) < 1e-9
    assert meter_settled(50.0, 50.2)
    assert not meter_settled(50.0, 60.0)
    # visual-demo: max=20 → stage drop 10% (SELF_HIT_HP=2)
    assert abs(stage_drop_pct(20.0) - 10.0) < 1e-9
    sim = MockHeatStageSimulator()
    sim.reset(48.0)
    assert sim.heat_pct >= 36.0
    # Instant fire jump
    before = sim.heat_pct
    sim.heat_pct = min(100.0, before + HEAT_PER_SHOT)
    assert sim.heat_pct == before + HEAT_PER_SHOT
    # Stage cool after settle + delay (10% demo-style steps, floor 12%)
    sim.heat_anim = sim.heat_pct
    sim.last_fire_at = 0.0
    sim._started_at = 1.0
    cooled = sim.tick(0.1, now=1.0, self_max_hp=20.0, ammo=100)
    assert cooled < before + HEAT_PER_SHOT
    assert cooled >= 12.0

    state = RobotState()
    state.current_health = 450
    state.max_health = 600
    state.current_heat = 60.0
    state.max_heat = 240.0
    state.remaining_ammo = 90
    state.ammo_capacity = 180
    state.target_locked = False
    state.vision_detection_count = 0
    state.vision_tracking_state = "idle"
    assert aim_target(state) == 0.0
    state.vision_tracking_state = "tracking"
    assert aim_target(state) == 60.0
    state.target_locked = True
    assert aim_target(state) == 100.0

    animator = CombatHudAnimator()
    frame = animator.reset(state)
    assert frame.self_anim == 75.0
    assert frame.heat_anim == 25.0
    assert frame.aim_anim == 100.0
    assert frame.ammo_anim == 50.0
    state.current_health = 300
    updated = animator.update(state, 1.0 / 60.0, "Enhanced")
    assert 50.0 < updated.self_anim < 75.0
    assert updated.self_ghost > updated.self_anim

    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from rm_client.ui.polar_app import PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = _build_settled_window(PolarRMWindow, app)
    window.resize(1280, 720)
    module = window.registry.get("CombatTargetHUD")
    assert module is not None
    try:
        signatures: set[tuple[int, int, int, int]] = set()
        for visual in ("Classic", "Clear", "Enhanced"):
            module.get_setting("Visual").value = visual
            window.combat_hud_animator.reset(window.state)
            image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            window.draw_combat_target_hud(painter, 1280, 720, module)
            painter.end()
            visible = 0
            red = green = blue = 0
            for y in range(315, 485, 3):
                for x in range(520, 760, 3):
                    pixel = image.pixelColor(x, y)
                    if pixel.alpha() > 0:
                        visible += 1
                        red += pixel.red()
                        green += pixel.green()
                        blue += pixel.blue()
            assert visible > 20
            signatures.add((visible, red, green, blue))
        assert len(signatures) == 3

        module.get_setting("Visual").value = "Clear"
        module.get_setting("Show Bars").value = False
        no_bars = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
        no_bars.fill(0)
        painter = QPainter(no_bars)
        window.draw_combat_target_hud(painter, 1280, 720, module)
        painter.end()
        assert any(no_bars.pixelColor(x, y).alpha() > 0 for y in range(315, 405, 4) for x in range(590, 690, 4))
        assert not any(no_bars.pixelColor(x, y).alpha() > 0 for y in range(425, 490, 4) for x in range(540, 740, 4))
        for x, y in ((585, 305), (697, 305), (585, 417), (697, 417)):
            assert no_bars.pixelColor(x, y).rgba() == 0

        premultiplied = no_bars.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
        payload = bytes(premultiplied.constBits())
        for offset in range(0, len(payload), 4):
            if payload[offset + 3] == 0:
                assert payload[offset : offset + 3] == b"\x00\x00\x00"

        # The compatibility paint path must not clear or tint the rectangular
        # bounds of the raster layer around the circular HUD.
        base_color = QColor("#315577")
        compat = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
        compat.fill(base_color)
        painter = QPainter(compat)
        window.draw_combat_target_hud_layer(painter, 1280, 720, module)
        painter.end()
        for x, y in ((535, 225), (745, 225), (535, 495), (745, 495)):
            assert compat.pixelColor(x, y).rgba() == base_color.rgba()

        from rm_client.ui.gpu_frame_compositor import GpuFrameCompositor

        assert not hasattr(window, "build_combat_target_hud_upload")
        assert not hasattr(GpuFrameCompositor, "upload_target_image")
    finally:
        close_window_safely(window, app)
    app.processEvents()


def test_ordered_gpu_commands_and_line_only_vision_batch() -> None:
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QFont

    from rm_client.ui.clickgui_gpu import ClickGuiDrawList, GpuImage, GpuRect, GpuText
    from rm_client.ui.vision_wire_gpu import VisionWireBatch

    commands = ClickGuiDrawList()
    commands.rect(QRectF(0, 0, 100, 40), 12, QColor(12, 20, 30, 180))
    commands.text(QRectF(8, 4, 84, 24), "POLAR-RM", QFont("Arial", 12), QColor("white"))
    commands.image(QRectF(72, 4, 20, 15), ROOT / "assets" / "jello" / "options.png")
    commands.rect(QRectF(8, 32, 32, 2), 1, QColor(90, 160, 255, 255))
    assert [command.sequence for command in commands.commands] == [0, 1, 2, 3]
    assert [type(command.payload) for command in commands.commands] == [GpuRect, GpuText, GpuImage, GpuRect]

    wire = VisionWireBatch()
    assert not hasattr(wire, "fill")
    assert not hasattr(wire, "rect")
    assert not hasattr(wire, "polygon")
    wire.fluorescent_polyline(
        [QPointF(10, 10), QPointF(70, 10), QPointF(70, 40), QPointF(10, 40)],
        width=1.4,
        alpha=0.82,
        closed=True,
    )
    # Three glow passes plus the white core are emitted for each edge.
    assert len(wire.lines) == 16
    assert not wire.labels.commands
    for line in wire.lines:
        assert line.color.red() == line.color.green() == line.color.blue() == 255
        assert line.color.alpha() > 0


def test_overlay_coordinator_wheel_and_match_chat() -> None:
    registry = build_default_registry()
    chat_module = registry.get("MatchChat")
    wheel_module = registry.get("QuickActionWheel")
    assert chat_module is not None and chat_module.enabled and chat_module.keybind == "T"
    assert wheel_module is not None and wheel_module.enabled and wheel_module.keybind == "Q"

    coordinator = OverlayCoordinator()
    assert coordinator.begin_radial()
    assert coordinator.blocks_combat_input
    coordinator.end_radial()
    assert coordinator.request_modal(OverlayKind.ECONOMY) is None
    assert not coordinator.begin_radial()
    assert coordinator.request_modal(OverlayKind.CLICKGUI) == OverlayKind.ECONOMY
    assert coordinator.request_modal(OverlayKind.HERO_DEPLOY) == OverlayKind.CLICKGUI
    assert coordinator.modal == OverlayKind.HERO_DEPLOY
    assert coordinator.begin_chat() == OverlayKind.HERO_DEPLOY
    assert coordinator.chat_input and coordinator.modal is None
    assert not coordinator.begin_radial()
    coordinator.end_chat()
    assert not coordinator.blocks_combat_input

    wheel = QuickActionWheelState()
    wheel.open(4.0, 4.0, 800.0, 600.0, now=1.0)
    assert wheel.center_x >= wheel.radius + 18.0
    assert wheel.center_y >= wheel.radius + 18.0
    assert wheel.update_pointer(wheel.center_x, wheel.center_y) == -1
    assert wheel.update_pointer(wheel.center_x, wheel.center_y - wheel.radius) == 0
    assert wheel.release() == "economy"
    wheel.configure(
        sector_count=4,
        custom_labels=["Top action", "", "Right action", "", "Bottom action", "", "Left action", ""],
        custom_icons=["UP", "", "RT", "", "DN", "", "LT", ""],
    )
    assert [sector.action for sector in wheel.sectors()] == [
        DEFAULT_ACTIONS[0],
        DEFAULT_ACTIONS[2],
        DEFAULT_ACTIONS[4],
        DEFAULT_ACTIONS[6],
    ]
    exported = wheel.export_config()
    restored = QuickActionWheelState()
    restored.load_config(exported)
    assert restored.export_config() == exported

    chat = MatchChatController()
    chat.open_input()
    chat.insert("x" * 300)
    assert len(chat.input_text) == 256
    assert chat.send_input(now=2.0)
    assert chat.messages[-1].source == "you"
    chat.history_older()
    assert chat.input_text == "x" * 256
    chat.history_newer()
    assert chat.input_text == ""
    assert chat.add("Radar online", dedupe_key="radar", now=3.0)
    assert not chat.add("Radar online", dedupe_key="radar", now=3.5)
    chat.close_input()
    assert chat.visible_messages(now=12.0)
    assert not chat.visible_messages(now=14.1)
    for index in range(205):
        chat.add(f"line {index}", now=20.0 + index)
    assert len(chat.messages) == 200

    event_chat = MatchChatController()
    adapter = ChatEventAdapter(event_chat)
    state = RobotState()
    state.stage_countdown_sec = 61
    adapter.poll(state, now=100.0)
    state.stage_countdown_sec = 59
    adapter.poll(state, now=101.0)
    assert [message.text for message in event_chat.messages] == [
        "Match ends in 60 seconds"
    ]
    state.stage_countdown_sec = 60
    adapter.poll(state, now=102.0)
    state.stage_countdown_sec = 59
    adapter.poll(state, now=103.0)
    assert [message.text for message in event_chat.messages].count(
        "Match ends in 60 seconds"
    ) == 1
    state.stage_countdown_sec = 300
    adapter.poll(state, now=104.0)
    state.stage_countdown_sec = 59
    adapter.poll(state, now=105.0)
    assert [message.text for message in event_chat.messages].count(
        "Match ends in 60 seconds"
    ) == 2
    state.air_support_active = True
    adapter.poll(state, now=106.0)
    state.air_support_active = False
    adapter.poll(state, now=107.0)
    assert event_chat.messages[-2].text == "Air support activated"
    assert event_chat.messages[-1].text == "Air support ended"


def test_native_viewer_telemetry_and_video_0310() -> None:
    status = b"AUTO READY\x00"
    fixed_size = struct.calcsize("<4sHHIQIHHHHIBBH9f")
    packet = struct.pack(
        "<4sHHIQIHHHHIBBH9f",
        b"PDL1",
        1,
        fixed_size + len(status),
        0b11,
        1_725_000_000_000,
        77,
        1280,
        720,
        5994,
        125,
        3200,
        1,
        0,
        12,
        15.5,
        0.4,
        -3.25,
        -0.2,
        16.8,
        0.0,
        0.0,
        0.0,
        1.0,
    ) + status
    telemetry = parse_vehicle_telemetry(packet)
    assert telemetry is not None
    assert telemetry.camera_online and telemetry.gimbal_online
    assert telemetry.gimbal_mode_name == "AUTO_AIM"
    assert telemetry.bullet_count == 12
    assert abs(telemetry.bullet_speed - 16.8) < 1e-4
    assert telemetry.status_text == "AUTO READY"
    assert parse_vehicle_telemetry(b"PDL1") is None

    payload = bytes((index * 17) & 0xFF for index in range(297))
    video_packet = bytes((0b11, 23, 297 & 0xFF)) + payload
    chunk = parse_video_0310_chunk(video_packet)
    assert chunk is not None and chunk.codec == "h264"
    assert chunk.reset and chunk.sequence == 23 and chunk.payload == payload
    assert parse_video_0310_chunk(bytes((0x80, 0, 0)) + bytes(297)) is None
    assert find_h264_sps_offset(b"junk\x00\x00\x00\x01\x67sps") == 4
    assert find_h264_sps_offset(b"\x00\x00\x01\x65idr") is None

    state = RobotState()
    manager = BackendManager(state=state)
    manager._apply_one(state, "CustomByteBlock", {"data": packet})
    assert state.native_gimbal_online
    assert state.native_telemetry_frame_seq == 77
    assert state.native_status_text == "AUTO READY"
    state.hero_lob_relocalization_valid = True
    state.hero_lob_armed = True
    restarted_packet = bytearray(packet)
    struct.pack_into("<I", restarted_packet, 20, 2)
    manager._apply_one(state, "CustomByteBlock", {"data": restarted_packet})
    assert not state.hero_lob_relocalization_valid
    assert not state.hero_lob_armed

    # uint32 sequence wrap is not a producer restart and must not destroy a
    # valid operator calibration.
    state.native_telemetry_frame_seq = 0xFFFFFFFF
    state.hero_lob_relocalization_valid = True
    wrapped_packet = bytearray(packet)
    struct.pack_into("<I", wrapped_packet, 20, 0)
    manager._apply_one(state, "CustomByteBlock", {"data": wrapped_packet})
    assert state.hero_lob_relocalization_valid
    manager.shutdown()


def test_hero_lob_solver_and_fail_closed_readiness() -> None:
    high = HeroLobPlanner.solve(18.0, 0.0, 16.0, arc="High")
    low = HeroLobPlanner.solve(18.0, 0.0, 16.0, arc="Low")
    assert high.reachable and low.reachable
    assert high.pitch_deg > low.pitch_deg > 0.0
    assert high.flight_time_s > low.flight_time_s
    assert not HeroLobPlanner.solve(100.0, 20.0, 5.0).reachable
    offset = HeroLobPlanner.solve(18.0, 0.0, 16.0, arc="High", muzzle_length_m=0.35)
    assert offset.reachable and offset.iterations > 0
    assert abs(offset.pitch_deg - high.pitch_deg) > 1.0e-5

    state = RobotState(robot_profile="Hero")
    blocked = HeroLobPlanner.readiness(state, hero_range_profile=True)
    assert not blocked.ready and "gimbal offline" in blocked.reasons
    state.native_telemetry_at = time.perf_counter()
    state.native_gimbal_online = True
    state.native_bullet_count = 12
    state.hero_lob_yaw_calibrated = True
    state.hero_lob_pitch_calibrated = True
    state.hero_lob_relocalization_valid = True
    state.current_stage = "MATCH"
    state.remaining_ammo = 12
    state.current_heat = 20.0
    state.max_heat = 240.0
    ready = HeroLobPlanner.readiness(state, hero_range_profile=True)
    assert ready.ready and ready.reasons == ()
    roll = math.radians(12.0) * 0.5
    state.native_quaternion = (math.sin(roll), 0.0, 0.0, math.cos(roll))
    tilted = HeroLobPlanner.readiness(state, hero_range_profile=True)
    assert not tilted.ready and "roll/yaw coupling guard" in tilted.reasons
    state.native_quaternion = (0.0, 0.0, 0.0, 1.0)
    stale = HeroLobPlanner.readiness(
        state,
        hero_range_profile=True,
        now=state.native_telemetry_at + 0.6,
    )
    assert not stale.ready and "telemetry stale" in stale.reasons


def test_projectile_hit_auto_spin_uses_cumulative_deltas() -> None:
    state = RobotState(current_stage="MATCH", current_health=500, current_buffer_energy=50)
    manager = BackendManager(state=state)
    baseline = {
        "total_damage": 30,
        "small_projectile_damage": 20,
        "large_projectile_damage": 10,
        "collision_damage": 0,
        "killer_id": 103,
    }
    manager._apply_one(state, "RobotInjuryStat", baseline)
    assert state.injury_stats_initialized
    assert state.projectile_hit_revision == 0

    second = dict(baseline, total_damage=42, small_projectile_damage=25, large_projectile_damage=17)
    manager._apply_one(state, "RobotInjuryStat", second)
    assert state.projectile_hit_revision == 1
    assert state.last_projectile_damage_delta == 12
    assert state.last_projectile_kind == "17mm+42mm"
    assert state.last_projectile_killer_id == 103

    # Non-projectile damage and a cumulative reset are both ignored.
    manager._apply_one(state, "RobotInjuryStat", dict(second, total_damage=60, collision_damage=18))
    assert state.projectile_hit_revision == 1
    manager._apply_one(
        state,
        "RobotInjuryStat",
        dict(second, total_damage=0, small_projectile_damage=0, large_projectile_damage=0),
    )
    assert state.projectile_hit_revision == 1
    manager._apply_one(
        state,
        "RobotInjuryStat",
        dict(second, total_damage=4, small_projectile_damage=4, large_projectile_damage=0),
    )
    assert state.projectile_hit_revision == 2
    assert state.last_projectile_damage_delta == 4

    registry = build_default_registry()
    feature = registry.get("AutoSpinOnHit")
    feature.set_enabled(True)
    controller = ProjectileHitSpinController()
    controller.seen_revision = state.projectile_hit_revision
    state.projectile_hit_revision += 1
    state.last_projectile_damage_delta = 16
    directive = controller.update(
        state,
        feature,
        spin_enabled=False,
        referee_link=True,
        now=10.0,
    )
    assert directive.action == "enable" and controller.owns_spin
    state.projectile_hit_revision += 1
    state.last_projectile_damage_delta = 7
    repeated = controller.update(
        state,
        feature,
        spin_enabled=True,
        referee_link=True,
        now=11.0,
    )
    assert repeated.action == "none" and controller.owns_spin
    assert controller.update(
        state,
        feature,
        spin_enabled=True,
        referee_link=True,
        now=13.4,
    ).action == "none"
    expiry = controller.update(
        state,
        feature,
        spin_enabled=True,
        referee_link=True,
        now=13.6,
    )
    assert expiry.action == "disable" and not controller.owns_spin

    # Existing operator spin is observed but never claimed or auto-disabled.
    state.projectile_hit_revision += 1
    operator = controller.update(
        state,
        feature,
        spin_enabled=True,
        referee_link=True,
        now=15.0,
    )
    assert operator.action == "none" and not controller.owns_spin
    manager.shutdown()


def test_hero_deploy_console_state_and_responsive_render() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFont, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    interface = HeroDeployInterfaceState()
    interface.open(1.0)
    # The Hero surface reuses the same high-rate reversible spring as the
    # existing Control Center rather than appearing as a new hard-cut modal.
    assert interface.render_visible and interface.active
    for frame in range(1, 151):
        interface.update_presentation(1.0 + frame / 144.0)
    assert interface.progress > 0.995
    assert not interface.arm_confirmation(1, 1.1)
    assert interface.arm_confirmation(1, 2.0)
    interface.begin_request(1, 2.0, mock=True)
    assert interface.sync(0, 3.99) is None
    assert interface.sync(0, 4.0) == 1
    interface.begin_request(0, 5.0, mock=False)
    assert interface.sync(0, 5.1) is None
    assert interface.pending_mode is None

    registry = build_default_registry()
    module = registry.get("HeroLob")
    module.set_enabled(True)
    state = RobotState(robot_profile="Hero", deploy_mode=0)
    state.native_telemetry_at = time.perf_counter()
    state.native_gimbal_online = True
    state.native_bullet_count = 12
    state.native_bullet_speed = 16.0
    state.hero_lob_yaw_calibrated = True
    state.hero_lob_pitch_calibrated = True
    state.hero_lob_relocalization_valid = True
    solution, readiness = HeroLobPlanner.preview_from_state(state, module, hero_range_profile=True)
    renderer = HeroDeployRenderer()
    single_tick_interface = HeroDeployInterfaceState()
    single_tick_interface.open(10.0)
    initial_progress = single_tick_interface.progress
    probe = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
    probe.fill(0)
    probe_painter = QPainter(probe)
    renderer.draw(
        probe_painter,
        640,
        360,
        state=state,
        module=module,
        interface=single_tick_interface,
        solution=solution,
        readiness=readiness,
        now=20.0,
    )
    probe_painter.end()
    assert single_tick_interface.progress == initial_progress
    for width, height in ((1000, 620), (1280, 720), (1600, 900), (1920, 1080), (2560, 1440)):
        layout = renderer.layout(width, height)
        assert layout.collapsed.top() == layout.target_panel.top()
        assert layout.collapsed.center().x() == layout.target_panel.center().x()
        assert layout.root_radius <= layout.panel.height() * 0.5
        assert layout.interactive
        assert layout.panel.left() >= 0 and layout.panel.top() >= 0
        assert layout.panel.right() <= width and layout.panel.bottom() <= height
        assert all(layout.panel.contains(button) for button in layout.buttons)
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        targets = renderer.draw(
            painter,
            width,
            height,
            state=state,
            module=module,
            interface=interface,
            solution=solution,
            readiness=readiness,
            now=6.0,
        )
        painter.end()
        assert len(targets) == 6
        assert all(layout.panel.contains(target.rect) for target in targets)
        assert image.pixelColor(int(layout.panel.center().x()), int(layout.panel.center().y())).alpha() > 0

    glass_calls: list[tuple[str, bool]] = []

    def record_glass(_painter, _rect, _radius, *, surface="MainGlass", clip_rect=None, **_kwargs):
        glass_calls.append((str(surface), clip_rect is not None))

    image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    renderer.draw(
        painter,
        1280,
        720,
        state=state,
        module=module,
        interface=interface,
        solution=solution,
        readiness=readiness,
        now=6.0,
        glass_drawer=record_glass,
    )
    painter.end()
    surfaces = {surface for surface, _clipped in glass_calls}
    assert "ProminentGlass" in surfaces
    assert "MainGlass" in surfaces and "SubGlass" in surfaces
    assert sum(not clipped for _surface, clipped in glass_calls) == 1
    assert all(
        clipped or surface in {"ProminentGlass", "LensIsland"}
        for surface, clipped in glass_calls
    )

    interface.close(7.0)
    assert not interface.visible and interface.render_visible
    for frame in range(1, 151):
        interface.update_presentation(7.0 + frame / 144.0)
    assert interface.progress < 0.001 and not interface.render_visible
    app.processEvents()


def test_hero_native_video_isolation_and_authoritative_lock() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication
    from rm_client.ui.polar_app import PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = _build_settled_window(PolarRMWindow, app)
    window.timer.stop()
    original_backend = window.backend
    original_ensure_camera = window.ensure_camera_started
    now = time.perf_counter()
    native_bgr = np.zeros((100, 320, 3), dtype=np.uint8)
    native_bgr[:, :, 1] = 255

    class NativeOnlyBackend:
        def __init__(self, field_log) -> None:
            self.calls: list[str] = []
            # The production camera boundary reports through the manager's
            # diagnostic port even when this test isolates video selection.
            self.field_log = field_log
            self.mqtt = original_backend.mqtt

        def latest_video_frame(self, _timeout: float, source_mode: str):
            self.calls.append(str(source_mode))
            assert source_mode == "Native 0x0310"
            return native_bgr, now, "native-0x0310"

    fake_backend = NativeOnlyBackend(original_backend.field_log)
    window.backend = fake_backend
    resumed: list[bool] = []
    window.ensure_camera_started = lambda: resumed.append(True)
    try:
        window.home_visible = False
        normal = QImage(320, 180, QImage.Format.Format_RGB888)
        normal.fill(QColor("#ff0000"))
        window.frame_image = normal
        window.frame_pixmap = QPixmap.fromImage(normal)
        window._normal_backend_mode = "webcam"
        window._normal_frame_source = "webcam"
        window._display_frame_sequence += 1
        assert window.consume_hero_native_video_frame()
        assert fake_backend.calls == ["Native 0x0310"]

        pending = QImage(400, 400, QImage.Format.Format_ARGB32_Premultiplied)
        pending.fill(0)
        pending_painter = QPainter(pending)
        window.state.deploy_mode = 0
        window.draw_video(pending_painter, 400, 400)
        pending_painter.end()
        assert pending.pixelColor(200, 200).red() > 240

        window.registry.get("HeroLob").enabled = True
        window.state.deploy_mode = 1
        window.state.deploy_status_revision += 1
        window.update_hero_deploy_state(now + 0.01)
        assert window.hero_deploy_ui.visible
        assert window.overlay_coordinator.modal == OverlayKind.HERO_DEPLOY
        assert not window.close_hero_deploy_interface()

        deployed = QImage(400, 400, QImage.Format.Format_ARGB32_Premultiplied)
        deployed.fill(0)
        deployed_painter = QPainter(deployed)
        assert window.draw_hero_native_background(
            deployed_painter,
            400,
            400,
            now=now + 0.1,
        ) == "live"
        deployed_painter.end()
        # 320:100 is letterboxed into the square; center is Native green and
        # the top bar is neutral, never the red normal camera.
        assert deployed.pixelColor(10, 200).green() > 240
        assert deployed.pixelColor(10, 20).green() < 30
        assert deployed.pixelColor(10, 20).red() < 30

        latest_calls = len(fake_backend.calls)
        window.consume_capture_frame()
        assert len(fake_backend.calls) == latest_calls + 1
        assert fake_backend.calls[-1] == "Native 0x0310"

        window._hero_native_video_at = now - 1.0
        lost = QImage(400, 400, QImage.Format.Format_ARGB32_Premultiplied)
        lost.fill(0)
        lost_painter = QPainter(lost)
        assert window.draw_hero_native_background(
            lost_painter,
            400,
            400,
            now=now + 0.1,
        ) == "lost"
        lost_painter.end()
        assert lost.pixelColor(10, 200).green() < 40
        assert lost.pixelColor(10, 200).red() < 40

        window.state.deploy_mode = 0
        window.state.deploy_status_revision += 1
        window.update_hero_deploy_state(now + 0.2)
        assert resumed
        assert not window.hero_deploy_ui.visible
        assert window.overlay_coordinator.modal is None
        restored = QImage(400, 400, QImage.Format.Format_ARGB32_Premultiplied)
        restored.fill(0)
        restored_painter = QPainter(restored)
        window.draw_video(restored_painter, 400, 400)
        restored_painter.end()
        assert restored.pixelColor(200, 200).red() > 240
    finally:
        window.backend = original_backend
        window.ensure_camera_started = original_ensure_camera
        close_window_safely(window, app)
    app.processEvents()


def test_normal_backend_video_freshness_and_source_ownership() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from rm_client.ui.polar_app import PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = _build_settled_window(PolarRMWindow, app)
    window.timer.stop()
    original_backend = window.backend
    now = time.perf_counter()
    backend_bgr = np.zeros((90, 160, 3), dtype=np.uint8)
    backend_bgr[:, :, 2] = 255

    class FakeUdp:
        def __init__(self) -> None:
            self.clears = 0

        def clear_stale_decoded_frame(self, _timeout: float) -> bool:
            self.clears += 1
            return True

    class FakeBackend:
        def __init__(self) -> None:
            self.udp = FakeUdp()
            self.calls = 0

        def latest_video_frame(self, _timeout: float, source_mode: str):
            self.calls += 1
            assert source_mode == "UDP HEVC"
            if self.calls == 1:
                return backend_bgr, now, "udp"
            return None, 0.0, "none"

    fake_backend = FakeBackend()
    window.backend = fake_backend
    camera = window.registry.get("CameraFeed")
    camera.get_setting("Source").value = "UDP HEVC"
    try:
        window.consume_capture_frame()
        assert window._normal_backend_mode == "udp hevc"
        assert window._normal_frame_source == "backend"
        assert window.frame_image is not None and window.frame_pixmap is not None
        assert window.normal_video_frame_ready(now + 0.1)

        # A later no-frame result releases both the visible texture and the
        # Compatibility Painter glass snapshot; it cannot freeze indefinitely.
        window.update_hud_liquid_source(window.frame_image)
        assert "hud" in window.liquid_glass.snapshots
        window.consume_capture_frame()
        assert fake_backend.udp.clears == 1
        assert window.frame_image is None and window.frame_pixmap is None
        assert window._normal_frame_source == "none"
        assert "hud" not in window.liquid_glass.snapshots

        lost = QImage(320, 180, QImage.Format.Format_ARGB32_Premultiplied)
        lost.fill(QColor("#ffffff"))
        painter = QPainter(lost)
        window.draw_video(painter, 320, 180)
        painter.end()
        assert lost.pixelColor(4, 4).red() < 20

        camera.get_setting("Source").value = "Synthetic"
        calls_before = fake_backend.calls
        window.consume_capture_frame()
        assert fake_backend.calls == calls_before
        assert not window.normal_video_frame_ready()
    finally:
        window.backend = original_backend
        close_window_safely(window, app)
    app.processEvents()


def test_minecraft_hud_assets_layout_and_render() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QFont, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from rm_client.ui.clickgui_gpu import ClickGuiDrawList

    app = QApplication.instance() or QApplication([])
    renderer = MinecraftCombatHudRenderer(ROOT / "assets" / "minecraft_hud")
    assert renderer.available
    registry = build_default_registry()
    module = registry.get("MinecraftCombatHUD")
    state = RobotState()
    state.robot_rows = [
        {"id": 1, "name": "Hero", "hp": 720, "max": 800, "state": "ALIVE"},
        {"id": 3, "name": "Infantry", "hp": 560, "max": 600, "state": "LOCAL"},
        {"id": 4, "name": "Infantry", "hp": 0, "max": 600, "state": "DEAD"},
        {"id": 7, "name": "Sentry", "hp": 500, "max": 600, "state": "PATROL"},
        {"id": 101, "name": "Hero", "hp": 640, "max": 800, "state": "ALIVE"},
        {"id": 103, "name": "Infantry", "hp": 420, "max": 600, "state": "ALIVE"},
        {"id": 104, "name": "Infantry", "hp": 0, "max": 600, "state": "DEAD"},
        {"id": 107, "name": "Sentry", "hp": 530, "max": 600, "state": "PATROL"},
    ]
    texts = tuple(text for text, _color in renderer.scoreboard_lines(state))
    assert len(texts) == 15
    assert texts[0].startswith("§e§l") and texts[1].startswith("§7")
    assert texts.count("") == 3
    assert any("Ally: §a3 §7YOU" in text for text in texts)
    assert any("Enemy: §c3" in text for text in texts)
    assert any(text.startswith("§fBase:") and "§c" in text for text in texts)
    assert any(text.startswith("§fShield:") and "§c" in text for text in texts)
    assert texts[-1] == "§epolar-rm-client"
    assert all("|" not in text and "www.hypixel" not in text for text in texts)
    state.robot_rows.extend(
        [
            {"id": robot_id, "name": "Infantry", "hp": 400 + robot_id, "max": 600, "state": "ALIVE"}
            for robot_id in (2, 5, 6, 102, 105, 106)
        ]
    )
    seven_v_seven = tuple(text for text, _color in renderer.scoreboard_lines(state))
    assert len(seven_v_seven) == 15  # roster growth does not enlarge sidebar
    assert "§aA §fAlly: §a6 §7YOU" in seven_v_seven
    assert "§cE §fEnemy: §c6" in seven_v_seven
    expected = {
        (1000, 620): 2,
        (1280, 720): 3,
        (1600, 900): 3,
        (1920, 1080): 4,
        (2560, 1440): 6,
    }
    for (width, height), scale in expected.items():
        assert vanilla_gui_scale(width, height) == scale
        lines = renderer.scoreboard_lines(state)
        layout = renderer.layout(width, height, "Auto", (text for text, _ in lines))
        assert layout.scale == scale
        assert layout.hotbar.left() >= 0 and layout.hotbar.right() <= width
        assert layout.hotbar.bottom() <= height
        assert layout.scoreboard.left() >= 0 and layout.scoreboard.right() <= width
        assert layout.scoreboard.bottom() <= height
        assert layout.scoreboard.bottom() < layout.hotbar.top()
        effects_right = layout.effects_origin.x() + 154 * scale
        effects_bottom = layout.effects_origin.y() + 24 * scale
        assert effects_right < layout.scoreboard.left()
        assert effects_bottom < layout.hotbar.top()
        assert layout.boss_bars == ()
        assert layout.slot_at(QPointF(layout.slots[4].center())) == 4
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        renderer.draw(painter, width, height, state, registry, module)
        painter.end()
        assert image.pixelColor(int(layout.hotbar.center().x()), int(layout.hotbar.center().y())).alpha() > 0
    previous_scale = 0.0
    multiplier_setting = module.get_setting("Scale Multiplier")
    for multiplier in (0.50, 1.00, 1.50, 2.00):
        multiplier_setting.value = multiplier
        lines = renderer.scoreboard_lines(state)
        layout = renderer.layout(
            1280,
            720,
            "Auto",
            (text for text, _ in lines),
            multiplier,
        )
        assert layout.base_scale == 3
        assert layout.multiplier == multiplier
        assert layout.scale >= previous_scale
        previous_scale = layout.scale
        assert layout.hotbar.left() >= 0.0 and layout.hotbar.right() <= 1280.0
        assert layout.scoreboard.top() >= 0.0 and layout.scoreboard.bottom() <= 720.0
        assert layout.slot_at(layout.slots[7].center()) == 7
        image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        drawn_layout = renderer.draw(painter, 1280, 720, state, registry, module)
        painter.end()
        assert abs(drawn_layout.scale - layout.scale) < 1e-6
        assert image.pixelColor(
            int(drawn_layout.hotbar.center().x()),
            int(drawn_layout.hotbar.center().y()),
        ).alpha() > 0
    multiplier_setting.value = 1.0
    cache_image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
    cache_image.fill(0)
    cache_painter = QPainter(cache_image)
    renderer.draw(cache_painter, 1280, 720, state, registry, module)
    cache_painter.end()
    stable_rebuilds = renderer.layer_rebuilds

    repeat_image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
    repeat_image.fill(0)
    repeat_painter = QPainter(repeat_image)
    renderer.draw(repeat_painter, 1280, 720, state, registry, module)
    repeat_painter.end()
    assert renderer.layer_rebuilds == stable_rebuilds

    previous_heat = state.current_heat
    state.current_heat += 20.0
    time.sleep(0.04)
    changed_image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
    changed_image.fill(0)
    changed_painter = QPainter(changed_image)
    renderer.draw(changed_painter, 1280, 720, state, registry, module)
    changed_painter.end()
    assert renderer.layer_rebuilds == stable_rebuilds + 1
    state.current_heat = previous_heat
    assert renderer.assignments(module)[-1] == "ClickGui"
    drop_commands = ClickGuiDrawList()
    drop_layout = renderer.append_drop_overlay(
        drop_commands,
        1280,
        720,
        "ClickGui",
        renderer.layout(1280, 720).slots[4].center(),
        renderer.assignments(module),
        QFont("Microsoft YaHei UI", 8),
    )
    assert drop_layout.slot_at(drop_layout.slots[4].center()) == 4
    assert drop_commands.images and all(image.nearest for image in drop_commands.images)
    assert len(renderer.font._cache) <= renderer.font.cache_limit
    app.processEvents()


def test_minecraft_hud_keeps_client_overlay_stack() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from rm_client.ui.clickgui_gpu import ClickGuiDrawList, GpuImage, GpuText
    from rm_client.ui.polar_app import PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = _build_settled_window(PolarRMWindow, app)
    window.timer.stop()
    calls: set[str] = set()
    try:
        watermark_image = QImage(360, 80, QImage.Format.Format_ARGB32_Premultiplied)
        watermark_image.fill(0)
        watermark_painter = QPainter(watermark_image)
        window.draw_watermark(watermark_painter)
        watermark_painter.end()
        watermark_cache_key = window._watermark_title_cache.cacheKey()
        watermark_layout_key = window._watermark_title_cache_key
        repeat_watermark_image = QImage(360, 80, QImage.Format.Format_ARGB32_Premultiplied)
        repeat_watermark_image.fill(0)
        repeat_watermark_painter = QPainter(repeat_watermark_image)
        window.draw_watermark(repeat_watermark_painter)
        repeat_watermark_painter.end()
        assert window._watermark_title_cache.cacheKey() == watermark_cache_key
        assert window._watermark_title_cache_key == watermark_layout_key

        window.home_visible = False
        mc = window.registry.get("MinecraftCombatHUD")
        mc.enabled = True
        hud = window.registry.get("HUD")
        hud.get_setting("Right Display").value = "ModuleListHud"
        tactical_map = window.registry.get("TacticalMap")
        tactical_map.enabled = True
        tactical_map.get_setting("Map Size").value = "Full Tactical"
        tactical_map.get_setting("Status Display").value = "LiquidCards"
        window.registry.get("EnergyAutoAim").enabled = False
        window._vision_source = "Local"

        window.minecraft_hud.draw = lambda *_args, **_kwargs: calls.add("minecraft")
        window.top_match_bar.paint = lambda *_args, **_kwargs: calls.add("top_match_bar")
        window.draw_watermark = lambda *_args, **_kwargs: calls.add("watermark")
        window.paint_combat_target_overlay = lambda *_args, **_kwargs: calls.add("target")
        window.draw_dynamic_island = lambda *_args, **_kwargs: calls.add("island")
        window.draw_tactical_map_hud = lambda *_args, **_kwargs: calls.add("map")
        module_list_args: list[tuple[tuple, dict]] = []
        window.draw_minecraft_module_list = lambda *args, **kwargs: (calls.add("modulelist"), module_list_args.append((args, kwargs)))
        window.draw_match_header = lambda *_args, **_kwargs: calls.add("match_header")
        window.draw_chassis_gimbal_indicator = lambda *_args, **_kwargs: calls.add("chassis")
        window.draw_telemetry_strip = lambda *_args, **_kwargs: calls.add("telemetry")
        window.draw_match_chat = lambda *_args, **_kwargs: calls.add("chat")
        window.draw_quick_action_wheel = lambda *_args, **_kwargs: calls.add("radial")
        window.draw_elimination_fx = lambda *_args, **_kwargs: calls.add("elimination")
        window.draw_hero_deploy_interface = lambda *_args, **_kwargs: calls.add("hero")

        image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        window.paint_hud_motion_overlay(
            painter,
            1280,
            720,
            include_vision=False,
        )
        cache_before = len(window._elide_cache)
        elided_a = window.elide(
            painter,
            window.font("poppins_regular", 8.0),
            "Stable repeated HUD label",
            72.0,
        )
        cache_after_first = len(window._elide_cache)
        elided_b = window.elide(
            painter,
            window.font("poppins_regular", 8.0),
            "Stable repeated HUD label",
            72.0,
        )
        painter.end()
        assert elided_a == elided_b
        assert cache_after_first >= cache_before
        assert len(window._elide_cache) == cache_after_first
        assert len(window._elide_cache) <= 4096
        assert {
            "minecraft",
            "top_match_bar",
            "watermark",
            "target",
            "island",
            "map",
            "modulelist",
            "chat",
            "radial",
            "elimination",
            "hero",
        } <= calls
        assert "match_header" not in calls
        assert "chassis" not in calls
        assert "telemetry" not in calls
        assert module_list_args

        # MC uses a dedicated compact map and right-side Sigma list without
        # mutating the user's normal PolarTextList preference.
        window.draw_minecraft_module_list = PolarRMWindow.draw_minecraft_module_list.__get__(window)
        prior_gpu_available = window.clickgui_gpu.available
        window.clickgui_gpu.available = True
        hud.get_setting("Jello List Animations").value = False
        window.registry.get("ArmorAutoAim").enabled = True
        window._collect_gpu_hud_commands = True
        window._gpu_hud_commands = ClickGuiDrawList(copy_payloads=False)
        list_probe = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
        list_probe.fill(0)
        list_painter = QPainter(list_probe)
        window.draw_module_list_debug_white(
            list_painter,
            1280,
            8.0,
            460.0,
            gameplay_only=True,
            max_rows=14,
        )
        command_count_before_glass_text = len(window._gpu_hud_commands.commands)
        window.draw_glass_text(
            list_painter,
            QRectF(620.0, 26.0, 180.0, 24.0),
            "GPU glass text",
            window.font("poppins_regular", 8.2),
            QColor("#f7f9ff"),
        )
        list_painter.end()
        assert len(window._gpu_hud_commands.commands) == command_count_before_glass_text + 2
        assert any(isinstance(command.payload, GpuText) for command in window._gpu_hud_commands.commands)
        assert any(
            isinstance(command.payload, GpuImage)
            and command.payload.source.endswith("hud_shadow.png")
            for command in window._gpu_hud_commands.commands
        )
        window._collect_gpu_hud_commands = False
        window.clickgui_gpu.available = prior_gpu_available
        captured_list: list[tuple[float | None, float | None, dict]] = []
        window.draw_module_list_debug_white = lambda _p, _w, start_y=None, available_height=None, **kwargs: captured_list.append((start_y, available_height, kwargs))
        window._last_minecraft_hud_layout = window.minecraft_hud.layout(
            1280,
            720,
            "Auto",
            (text for text, _ in window.minecraft_hud.scoreboard_lines(window.state)),
        )
        window.draw_minecraft_module_list(painter, 1280, 720, 1.0)
        assert captured_list and captured_list[-1][0] == 8.0
        assert captured_list[-1][1] > 36.0
        assert captured_list[-1][2] == {"gameplay_only": True, "max_rows": 14}
        assert window.tactical_map_rect(1280, 720).width() <= 216.0
        assert window.effective_tactical_map_status_display() == "Off"
        assert window.compact_map_status_label("RED BASE YOU") == "A BASE"
        assert window.compact_map_status_label("BLUE BASE") == "E BASE"
        assert window.compact_map_status_label("RED OUTPOST") == "POST"

        # Leaving MC must restore the ordinary render policy and the exact
        # saved map/list preference; no temporary adaptation is persisted.
        calls.clear()
        mc.enabled = False
        hud.get_setting("Combat Preset").value = "Polar"
        window.draw_module_list_hud = lambda *_args, **_kwargs: calls.add("normal_modulelist")
        restored_image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
        restored_image.fill(0)
        restored_painter = QPainter(restored_image)
        window.paint_hud_motion_overlay(
            restored_painter,
            1280,
            720,
            include_vision=False,
        )
        restored_painter.end()
        assert {"normal_modulelist", "chassis", "telemetry"} <= calls
        assert "minecraft" not in calls
        window.tactical_map_rect(1280, 720)
        assert window.registry.get("TacticalMap").get_setting("Map Size").value == "Full Tactical"
        assert window.tactical_map_state.container.target_width == 360.0
        assert window.effective_tactical_map_status_display() == "LiquidCards"

        # Narrow map cards/bars must allocate distinct bounds instead of
        # painting the value over the label (the reported 2K collision).
        liquid_map = window.registry.get("LiquidTacticalMap")
        liquid_map.get_setting("Show Values").value = True
        text_calls: list[tuple[QRectF, str]] = []
        original_draw_text = window.draw_text
        window.draw_text = lambda _p, rect, text, *_args, **_kwargs: text_calls.append((QRectF(rect), str(text)))
        bounds_image = QImage(180, 100, QImage.Format.Format_ARGB32_Premultiplied)
        bounds_image.fill(0)
        bounds_painter = QPainter(bounds_image)
        window.draw_liquid_status_bar(
            bounds_painter,
            QRectF(4, 4, 62, 28),
            "RED BASE YOU",
            "4996/5000",
            0.8,
            QColor("#55ffff"),
            "bounds_bar",
            "base",
            minimal=True,
        )
        bar_label = text_calls[0][0]
        bar_value = text_calls[2][0]
        assert bar_label.right() < bar_value.left()
        text_calls.clear()
        window.draw_liquid_status_card(
            bounds_painter,
            QRectF(74, 4, 62, 40),
            "RED BASE YOU",
            "4996/5000",
            0.8,
            QColor("#55ffff"),
            "bounds_card",
            "base",
            minimal=True,
        )
        bounds_painter.end()
        card_label = text_calls[0][0]
        card_value = text_calls[1][0]
        assert card_label.bottom() - card_value.top() <= 1.0
        window.draw_text = original_draw_text
    finally:
        close_window_safely(window, app)
    app.processEvents()


def test_estop_zero_frame_bypass_and_backend_readiness() -> None:
    backend = RmSerialBackend(port="COM_TEST", mock_writes=False)
    backend.trigger_estop()
    assert backend.estop_active
    ok, _message = backend.write(b"unsafe")
    assert not ok and backend.frames_written == 0
    ok, reason = backend.write_frame(0x0311, b"\x00" * 30)
    assert not ok and "0x0311" in reason

    state = RobotState()
    manager = BackendManager(state=state)
    manager.mqtt.mock_output = True
    ok, reason = manager.trigger_estop()
    assert ok and reason == "mock"
    assert manager.safety.estop_active and manager.serial.estop_active
    assert manager.mqtt.publish_records[-1].topic == "KeyboardMouseControl"
    assert manager.mqtt.publish_records[-1].payload == backend_proto.encode_keyboard_mouse_control()
    manager.release_estop()
    assert not manager.safety.estop_active and not manager.serial.estop_active
    report = audit_backend(manager, state)
    assert not report.ready
    assert report.verdict == "NOT READY FOR REAL MATCH"
    assert any(gate.requires_live_validation for gate in report.blockers)
    gates = {gate.key: gate for gate in report.gates}
    assert not gates["video_source_declared"].passed
    assert not gates["spin_firmware_schema"].passed
    consumed = manager.apply_readiness(state, live_evidence={"video_source": "udp"})
    assert consumed["verdict"] == "NOT READY FOR REAL MATCH"
    assert state.backend_stats["readiness"] is consumed
    assert "field_signoff" in consumed["blockers"]

    class FakeAuditManager:
        scenario_key = "OfficialEvent"

        @staticmethod
        def stats() -> dict[str, Any]:
            return {
                "scenario": "OfficialEvent",
                "mqtt": {"connected": True, "mock_output": False},
                "udp": {
                    "connected": True,
                    "decoded_connected": True,
                    "decoded_frames": 30,
                },
                "native_0310": {
                    "decoder_ready": False,
                    "decoded_connected": False,
                    "decoded_frames": 0,
                },
            }

    selected_udp = audit_backend(
        FakeAuditManager(),
        RobotState(),
        live_evidence={"video_source": "udp"},
    )
    selected_gates = {gate.key: gate for gate in selected_udp.gates}
    assert selected_gates["video_source_declared"].passed
    assert selected_gates["video_link"].passed
    assert selected_gates["native_0310_decoder"].passed

    selected_native = audit_backend(
        FakeAuditManager(),
        RobotState(),
        live_evidence={"video_source": "native_0310"},
    )
    native_gates = {gate.key: gate for gate in selected_native.gates}
    assert not native_gates["video_link"].passed
    assert not native_gates["native_0310_decoder"].passed
    manager.shutdown()


def run_all_smoke_tests() -> int:
    # Definition-order discovery includes every core module-level test. External
    # model/demo inference lives in tongji_external_integration_test.py and is
    # explicitly inventoried under the registry's external category.
    tests = [(name, value) for name, value in globals().items()
             if name.startswith("test_") and callable(value)
             and getattr(value, "__module__", None) == __name__]
    SMOKE_SKIPS.clear()
    completed = 0
    for name, test in tests:
        print(f"RUN {name}", flush=True)
        before = len(SMOKE_SKIPS)
        try:
            test()
        except unittest.SkipTest as exc:
            SMOKE_SKIPS.append(str(exc))
            print(f"[SKIP] {name}: {exc}", flush=True)
        if len(SMOKE_SKIPS) == before:
            completed += 1
            print(f"[PASS] {name}", flush=True)
        else:
            print(f"[INCOMPLETE] {name}: some checks were not executed", flush=True)
    print(f"smoke checks completed: {completed}/{len(tests)} complete test functions; "
          f"{len(SMOKE_SKIPS)} skipped checks", flush=True)
    return 0 if tests and not SMOKE_SKIPS else 77


if __name__ == "__main__":
    raise SystemExit(run_all_smoke_tests())
