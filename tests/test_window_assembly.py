"""Explicit assembly ports and independent panel defaults, without a window."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ["CI"] = "true"
os.environ["POLAR_DISABLE_LOCAL_CAPTURE"] = "1"
os.environ["POLAR_DISABLE_MEDIA_PRELOAD"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication
from rm_client.runtime_paths import RuntimePaths
from rm_client.ui.polar import window_assembly as assembly
from rm_client.ui.polar.panel_layouts import create_panel_layouts

APP = QApplication.instance() or QApplication([])


class WindowAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.paths = RuntimePaths.discover(environ={"POLAR_USER_DATA_ROOT": self.directory.name}, frozen=False)

    def test_paths_are_prepared_before_seeding_and_return_the_same_authority(self):
        paths = Mock()
        events = []
        paths.ensure_writable_dirs.side_effect = lambda: events.append("directories")
        paths.seed_default_config.side_effect = lambda: events.append("seed")
        with patch.object(assembly.RuntimePaths, "discover", return_value=paths):
            self.assertIs(assembly.prepare_runtime_paths(), paths)
        self.assertEqual(events, ["directories", "seed"])

    def test_failed_path_preparation_does_not_seed_or_hide_the_error(self):
        paths = Mock()
        failure = OSError("directory unavailable")
        paths.ensure_writable_dirs.side_effect = failure
        with patch.object(assembly.RuntimePaths, "discover", return_value=paths):
            with self.assertRaises(OSError) as caught:
                assembly.prepare_runtime_paths()
        self.assertIs(caught.exception, failure)
        paths.seed_default_config.assert_not_called()

    def test_asset_resolution_prefers_resources_and_preserves_app_fallback(self):
        root = Path(self.directory.name)
        paths = SimpleNamespace(resource_root=root / "resources", app_root=root / "app")
        self.assertEqual(assembly.resolve_client_asset(paths, "model", "file.xml"), root / "app/model/file.xml")
        expected = root / "resources/model/file.xml"
        expected.parent.mkdir(parents=True)
        expected.write_text("model")
        self.assertEqual(assembly.resolve_client_asset(paths, "model", "file.xml"), expected)

    def test_vision_services_use_supplied_paths_and_do_not_start_native_workers(self):
        model = Path(self.directory.name) / "models/local.xml"
        services = assembly.create_vision_services(workspace=self.paths.workspace_root,
            calibration_profiles=self.paths.calibrations_root, model_path=model)
        self.assertEqual(services.calibration.workspace, self.paths.workspace_root)
        self.assertEqual(services.calibration.profile_dir, self.paths.calibrations_root)
        self.assertEqual(services.local.model_path, model)
        for owner in (services.receiver, services.local, services.bridge):
            self.assertFalse(owner.running)
            self.assertIsNone(owner._thread)
        self.assertIsNone(services.receiver._socket)
        self.assertIsNone(services.bridge._unreleased_socket)
        self.assertIsNone(services.local._detector)

    def test_failed_calibration_load_does_not_construct_receivers(self):
        calibration = Mock()
        failure = TypeError("bad calibration adapter")
        calibration.load.side_effect = failure
        with patch.object(assembly, "CameraCalibrationManager", return_value=calibration), \
             patch.object(assembly, "TongjiVisionReceiver") as receiver:
            with self.assertRaises(TypeError) as caught:
                assembly.create_vision_services(workspace=self.paths.workspace_root,
                    calibration_profiles=self.paths.calibrations_root, model_path=Path("model.xml"))
        self.assertIs(caught.exception, failure)
        calibration.load.assert_called_once_with("polar_webcam")
        receiver.assert_not_called()

    def test_presentation_shares_one_animation_bank_and_keeps_independent_state(self):
        first = assembly.create_presentation_services(self.paths)
        second = assembly.create_presentation_services(self.paths)
        self.assertIs(first.motion.bank, first.animation)
        self.assertIsNot(first.animation, second.animation)
        self.assertIsNot(first.gui_cache, second.gui_cache)
        self.assertIsNot(first.student_state, second.student_state)
        self.assertEqual(first.jello.asset_root, self.paths.asset("jello"))
        self.assertEqual(first.minecraft_hud.asset_root, self.paths.asset("minecraft_hud"))
        self.assertEqual(first.student_renderer.asset_root, self.paths.asset("minecraft_hud"))
        self.assertFalse(first.clickgui_gpu.available)

    def test_failed_font_loading_stops_before_constructing_renderers(self):
        font_book = Mock()
        failure = TypeError("font adapter failed")
        font_book.load_from_project_assets.side_effect = failure
        with patch.object(assembly, "FontBook", return_value=font_book), \
             patch.object(assembly, "ClickGuiGpuRenderer") as renderer:
            with self.assertRaises(TypeError) as caught:
                assembly.create_presentation_services(self.paths)
        self.assertIs(caught.exception, failure)
        font_book.load_from_project_assets.assert_called_once_with(self.paths.resource_root)
        renderer.assert_not_called()

    def test_gpu_services_remain_uninitialized_until_the_context_owner_initializes(self):
        services = assembly.create_gpu_services()
        for owner in (services.vision_wire, services.island_icon, services.quick_action_wheel,
                      services.compositor, services.startup, services.hud_video):
            self.assertFalse(owner.available)
        self.assertIsNot(services.startup, services.hud_video)
        self.assertIsNone(services.compositor.background_fbo)

    def test_wallpaper_and_feedback_paths_keep_user_writes_outside_packaged_assets(self):
        wallpaper = assembly.create_startup_wallpaper(self.paths)
        self.addCleanup(wallpaper.close_thumbnail_workers)
        self.addCleanup(wallpaper.close)
        feedback = assembly.create_feedback_assets(self.paths)
        self.assertEqual(wallpaper.assets_dir, self.paths.asset("wallpapers"))
        self.assertEqual(wallpaper.user_dir, self.paths.user_data_root / "wallpapers")
        self.assertEqual(feedback.kill_fx.texture_root, self.paths.asset("kill_fx", "gd656") / "textures")
        self.assertEqual(feedback.ui_audio.custom_dir, self.paths.user_data_root / "ui_sounds/custom")
        self.assertIsNone(wallpaper.video_player._thread)

    def test_event_source_ports_preserve_parent_ownership_mode_and_callbacks(self):
        owner = QObject()
        self.addCleanup(owner.deleteLater)
        events = []
        warmup = assembly.create_warmup_timer(owner, lambda: events.append("warmup"))
        self.assertIs(warmup.parent(), owner)
        self.assertTrue(warmup.isSingleShot())
        self.assertFalse(warmup.isActive())
        timer = assembly.start_frame_timer(owner, lambda: events.append("frame"), 7)
        self.addCleanup(timer.stop)
        self.assertIs(timer.parent(), owner)
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.timerType(), Qt.TimerType.PreciseTimer)
        self.assertEqual(timer.interval(), 7)
        warmup.timeout.emit()
        timer.timeout.emit()
        self.assertEqual(events, ["warmup", "frame"])

    def test_frame_timer_can_be_published_inert_before_admission(self):
        owner = QObject()
        self.addCleanup(owner.deleteLater)
        callback = Mock()
        timer = assembly.create_frame_timer(owner, callback)
        self.assertIs(timer.parent(), owner)
        self.assertFalse(timer.isActive())
        self.assertFalse(timer.isSingleShot())
        self.assertEqual(timer.timerType(), Qt.TimerType.PreciseTimer)
        timer.timeout.emit()
        callback.assert_called_once_with()

    def test_panel_default_coordinates_and_mutable_maps_are_instance_local(self):
        categories = ("Combat", "Vision", "Render", "System", "Extras")
        first, second = create_panel_layouts(categories), create_panel_layouts(categories)
        self.assertEqual(first.old["Combat"], {"x": 20., "y": 20., "expanded": True, "dragged": False})
        self.assertEqual(first.old["System"], {"x": 500., "y": 20., "expanded": False, "dragged": False})
        self.assertEqual(first.polar["Extras"], {"x": 80., "y": 430., "scroll": 0.})
        self.assertEqual(first.polar["Profile"], {"x": 310., "y": 430., "scroll": 0.})
        first.old["Combat"]["x"] = 999
        first.new["Combat"]["scroll"] = 8
        first.dropdown["Combat"]["expanded"] = False
        first.polar["Combat"]["x"] = 999
        self.assertEqual(second.old["Combat"]["x"], 20.)
        self.assertEqual(second.new["Combat"]["scroll"], 0.)
        self.assertTrue(second.dropdown["Combat"]["expanded"])
        self.assertEqual(second.polar["Combat"]["x"], 80.)
        self.assertEqual(categories, ("Combat", "Vision", "Render", "System", "Extras"))


if __name__ == "__main__": unittest.main()
