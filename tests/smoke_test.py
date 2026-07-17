import os
from pathlib import Path
import socket
import threading
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.config_store import ClientConfigStore, apply_registry_config, export_client_config
from rm_client.combat_events import CombatEvent, OfficialMatchFeed, VisionKillDetector
from rm_client.kill_fx import KillFxLayout, KillFxRenderer, PRESET_ELEMENTS, VALORANT_STYLES
from rm_client.match_modes import MATCH_MODES, MatchModeManager
from rm_client.model import GUI_MODES, HUD_MODES, MODULE_LIST_STYLES, TACTICAL_MAP_STATUS_DISPLAYS, RobotState, build_default_registry
from rm_client.protocol import RefereeFrameCodec
from rm_client.scenarios import ScenarioManager
from rm_client.startup_wallpaper import PkgVirtualFileSystem, StartupWallpaperManager
from rm_client.transports import FrameAssembler, OfficialMqttBridge, SerialBridgeStub, UDPVideoReceiver, VideoPipeline
from rm_client.tongji_bridge import DEFAULT_PORT, TongjiVisionReceiver, build_tongji_packet
from rm_client.ui_audio import UiAudioManager
from rm_client.vision import ArmorObservation, PACKET_FRAME, PACKET_HEADER, PACKET_RESULT, PROTOCOL_MAGIC, PROTOCOL_VERSION, TongjiVisionBridge


def test_registry() -> None:
    registry = build_default_registry()
    click_gui = registry.get("ClickGui")
    assert click_gui is not None
    mode = click_gui.get_setting("Mode")
    assert mode is not None
    assert mode.options == GUI_MODES
    assert registry.get("ArmorAutoAim") is not None
    assert registry.get("EnergyAutoAim") is not None
    assert registry.get("ArmorAutoAim").get_setting("Lock Style") is not None
    assert registry.get("EnergyAutoAim").get_setting("Rune Type") is not None
    assert registry.get("EnergyAutoAim").get_setting("Lock Style") is not None
    assert registry.get("SpinMode") is not None
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
    assert startup_visual.get_setting("Wallpaper Profile").value == "TexasMP4"
    assert startup_visual.get_setting("Wallpaper Profile").options == ["TexasMP4", "VendingMachine4K", "Default3000925581Pkg", "ParticleGrid"]
    assert startup_visual.get_setting("Motion Quality").options == ["Smooth60", "Balanced30", "Source"]
    assert startup_visual.get_setting("Motion Quality").value == "Balanced30"
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
    assert tongji.get_setting("UDP Port").value == DEFAULT_PORT
    assert tongji.get_setting("Local Device").options == ["AUTO", "GPU", "CPU"]
    assert registry.get("CameraCalibration").get_setting("Capture Chessboard").kind == "action"
    armor_esp = registry.get("Armor3DESP")
    assert armor_esp is not None
    assert armor_esp.get_setting("Style").options == ["Wireframe3D", "Zen2D", "Prediction Rack"]
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
    assert audio.get_setting("Sound Theme").value == "Polar"
    assert audio.get_setting("Sound Theme").options == ["Polar", "Zen", "LiquidBounce"]
    assert audio.get_setting("UI Volume").value == 45


def test_audio_feedback_and_kill_sound_mapping() -> None:
    sound_root = ROOT / "assets" / "ui_sounds" / "liquidbounce"
    for name in ("click-1.ogg", "click-2.ogg", "click-3.ogg"):
        assert (sound_root / name).stat().st_size > 0
    zen_root = ROOT / "assets" / "ui_sounds" / "openzen"
    for name in ("truth.wav", "lie.wav"):
        assert (zen_root / name).stat().st_size > 0

    class FakePool:
        def __init__(self) -> None:
            self.played: list[tuple[str, float]] = []

        def play(self, name: str, volume: float) -> None:
            self.played.append((name, volume))

    manager = object.__new__(UiAudioManager)
    manager.pool = FakePool()
    manager.module_toggled(True, "config", active=True, volume=45)
    assert manager.pool.played == []
    manager.module_toggled(True, "clickgui", active=True, volume=45, theme="Zen")
    manager.module_toggled(False, "keybind", active=True, volume=45, theme="Zen")
    assert manager.pool.played == [("openzen/truth.wav", 45), ("openzen/lie.wav", 45)]
    manager.preview("invalid", 30)
    assert manager.pool.played[-1] == ("openzen/lie.wav", 18)
    manager.module_toggled(True, "clickgui", active=True, volume=45, theme="LiquidBounce")
    assert manager.pool.played[-1] == ("liquidbounce/click-2.ogg", 45)

    registry = build_default_registry()
    toggles: list[tuple[str, bool, str]] = []
    registry.add_toggle_listener(lambda module, enabled, source: toggles.append((module.name, enabled, source)))
    target = registry.get("SpeedLimit")
    assert target is not None
    registry.toggle_module(target, source="clickgui")
    assert toggles == [("SpeedLimit", target.enabled, "clickgui")]

    renderer = KillFxRenderer(ROOT / "assets" / "kill_fx" / "gd656")
    assert renderer._sound_name("NORMAL", 1, "Default Scrolling") == "killsound_df.ogg"
    assert renderer._sound_name("HEADSHOT", 1, "Battlefield 1") == "headshotkillsound_bf1.ogg"
    assert renderer._sound_name("NORMAL", 6, "Combo") == "combokillsound_6_cf.ogg"
    assert (renderer.sound_root / renderer._sound_name("NORMAL", 1, "Valorant Prime")).exists()


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
    lit = ArmorObservation(103, "three", "blue", 0.92, ((1.0, 1.0), (8.0, 1.0), (8.0, 4.0), (1.0, 4.0)), light_strength=0.8, light_state="lit")
    extinguish = ArmorObservation(103, "three", "extinguish", 0.94, ((1.0, 1.0), (8.0, 1.0), (8.0, 4.0), (1.0, 4.0)), light_strength=0.12, light_state="extinguished")
    detector.process([lit], alliance="Red", frame_luminance=0.4, now=1.0)
    event = []
    for tick in range(6):
        event = detector.process([extinguish], alliance="Red", frame_luminance=0.4, now=1.1 + tick * 0.10)
    assert len(event) == 1 and event[0].source == "vision"
    feed = OfficialMatchFeed()
    events, _updates = feed.consume("Event", b"\x08\x01", alliance="Red", local_robot_id=3)
    assert len(events) == 1 and events[0].source == "referee"


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
            assert manager.profile_names()[:4] == ["TexasMP4", "VendingMachine4K", "Default3000925581Pkg", "ParticleGrid"]
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
    assert hud.get_setting("Density").value == "CombatCompact"
    assert hud.get_setting("Visual Tone").value == "LiquidClean"
    assert hud.get_setting("Target Style").value == "RoundCompact"
    module_list_style = hud.get_setting("ModuleList Style")
    assert module_list_style is not None
    assert module_list_style.options == MODULE_LIST_STYLES
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
        )
        store.save("practice", data)
        assert "practice" in store.profile_names()
        loaded = store.load("practice")
        assert loaded is not None
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
        apply_registry_config(fresh, {"modules": {"MatchMode": {"settings": {"Alliance": "Blue"}}}})
        assert fresh.get("AllianceProfile").get_setting("Side").value == "Blue"


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
    packet = build_tongji_packet(
        640,
        360,
        [{"name": "three", "color": "blue", "confidence": 0.91, "points": [[1, 2], [30, 2], [30, 20], [1, 20]], "locked": True}],
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as publisher:
        publisher.sendto(packet, ("127.0.0.1", port))
    received = None
    deadline = time.perf_counter() + 1.0
    while time.perf_counter() < deadline:
        received = receiver.latest(1_000)
        if received is not None:
            break
        time.sleep(0.01)
    receiver.stop()
    assert received is not None
    assert received.primary is not None and received.primary["name"] == "three"


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


def test_tongji_local_yolo_demo() -> None:
    """用同济自带 demo 验证 Windows/OpenVINO 路径真的能加载 XML/BIN 并完成一帧推理。"""
    model = ROOT.parent / "同济自瞄" / "sp_vision_25" / "assets" / "yolov5.xml"
    demo = ROOT.parent / "同济自瞄" / "sp_vision_25" / "assets" / "demo" / "demo.avi"
    if not model.exists() or not demo.exists():
        return
    import cv2
    from rm_client.tongji_yolo import TongjiYoloV5

    capture = cv2.VideoCapture(str(demo))
    ok, frame = capture.read()
    capture.release()
    assert ok and frame is not None
    detector = TongjiYoloV5(model, "CPU")
    result = detector.detect(frame)
    assert isinstance(result, list)


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
    window = PolarRMWindow()
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
    image = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#182433"))
    painter = QPainter(image)
    module = window.registry.get("Armor3DESP")
    window.draw_armor_3d_esp(painter, 640, 360, result, module)
    painter.end()
    try:
        assert any(bytes(image.constBits()))
    finally:
        window.close()
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

    registry = build_default_registry()
    state = RobotState()
    serial = SerialBridgeStub()
    bridge = KeyboardControlBridge(serial, mock_output=True)
    command = bridge.set_key("W", True, state, registry)
    assert command is not None
    assert command.vx == 1.0
    assert command.vy == 0.0
    assert b"rm_control.chassis.v1" in bridge.last_payload
    assert len(command.to_custom_control()) == 30
    assert serial.last_write == b""

    bridge.mock_output = False
    command = bridge.set_key("W", False, state, registry)
    assert command is not None
    assert command.vx == 0.0
    assert serial.last_write == bridge.last_payload


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
    assert registry.get("MQTTLink").get_setting("Host").value == "192.168.12.1"
    assert registry.get("SerialBridge").get_setting("Mock Writes").value is True
    assert mqtt.host == "192.168.12.1"
    assert mqtt.mock_output is True
    assert udp.port == 3334
    assert control.mock_output is True


def test_official_mqtt_bridge_mock_publish() -> None:
    bridge = OfficialMqttBridge()
    bridge.publish_custom_control(b"abc")
    assert bridge.publish_records[-1].topic == "CustomControl"
    assert bridge.publish_records[-1].mock_output is True


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
    app = QApplication.instance() or QApplication([])
    window = PolarRMWindow()
    assert "VendingMachine4K" in window.startup_wallpaper.profile_names()
    vending = ROOT / "assets" / "wallpapers" / "builtin" / "vending-machine.mp4"
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
    window.close()


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
    app.processEvents()


def test_hud_liquid_glass_snapshot_stability() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from rm_client.ui.polar_app import PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = PolarRMWindow()
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
        window.close()
    app.processEvents()


if __name__ == "__main__":
    test_registry()
    test_audio_feedback_and_kill_sound_mapping()
    test_kill_fx_combat_focus_layout_and_migration()
    test_kill_fx_assets_and_dual_evidence()
    test_gd656_renderer_presets()
    test_wallpaper_pkg_index_reader()
    test_wallpaper_profiles_and_user_import()
    test_hud_modes_and_rmui_modules()
    test_match_modes_and_official_map_assets()
    test_match_mode_manager()
    test_config_store_roundtrip()
    test_tongji_bridge_protocol()
    test_tongji_udp_protocol()
    test_local_prediction_snapshot()
    test_tongji_local_yolo_demo()
    test_calibration_export()
    test_armor_3d_esp_render()
    test_setting_clamp()
    test_udp_reassembly()
    test_video_pipeline_reassembly()
    test_referee_frame_codec()
    test_keyboard_control_bridge()
    test_scenario_manager()
    test_official_mqtt_bridge_mock_publish()
    test_robot_state_tick()
    test_pyside_animation_import()
    test_liquid_glass_snapshot_stability()
    test_hud_liquid_glass_snapshot_stability()
    print("smoke tests passed")
