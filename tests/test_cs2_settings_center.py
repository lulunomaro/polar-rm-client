"""Real settings changes, validation, modal input safety and bounded persistence."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QSurfaceFormat, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QInputDialog
from rm_client.control import KeyboardControlBridge
from rm_client.model import RobotState, build_default_registry
from rm_client.ui.cs2_settings_center import (
    settings_specs, validate_draft, apply_registry_draft, build_settings_center, open_settings_center,
)
from rm_client.ui import cs2_video_settings as video
from rm_client import audio_preferences as audio

APP = QApplication.instance() or QApplication([])


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(1280, 720)
        self.registry = build_default_registry(); self.state = RobotState()
        self.control = KeyboardControlBridge()
        self.backend = Mock(mqtt=NS(mock_output=True))
        self.backend.trigger_estop.return_value = (True, "")
        self.frame_scheduler = Mock(); self.toast = Mock()
        self.auto_save_config = Mock(); self.after_setting_changed = Mock()
        self.invalidate_transient_overlay_scene = Mock(); self.invalidate_gui_cache = Mock()
    def key_name(self, event):
        if event.key() == Qt.Key.Key_Shift: return "Shift"
        return event.text().upper()


def ready_page(dialog, name):
    """Exercise the production event-loop loader, never force a blocking fill."""
    page = dialog.pages[name] if name in dialog.pages else dialog.crosshair
    if not dialog.isVisible():
        dialog.show()
    dialog.tabs.setCurrentWidget(page)
    from time import monotonic
    deadline = monotonic() + 8
    while not page.builder.ready and not page.builder.error and monotonic() < deadline:
        QTest.qWait(10)
    if not page.builder.ready:
        raise AssertionError(page.builder.error or "settings page did not finish")
    return page


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.host = Host(); self.addCleanup(self.host.deleteLater)

    def test_page_has_genuine_preferences_not_actuation_gates(self):
        for page in ("音频", "游戏", "键盘 / 鼠标", "界面 / HUD"):
            for module, settings, _ in settings_specs(self.host.registry, page):
                for s in settings:
                    self.assertNotIn(s.name, ("Mock Writes", "Allow Drive", "DT7 Real TX", "Radio KM Egress", "Mock Requests"))
                    self.assertIn(s.kind, ("bool", "mode", "number"))

    def test_invalid_bind_or_numeric_never_partially_applied(self):
        gain = self.host.registry.get("RobotLink").get_setting("Mouse Sensitivity")
        for draft in ({("RobotLink", "Bind W"): "F12"},
                      {("RobotLink", "Bind W"): "S"},
                      {("RobotLink", "Mouse Sensitivity"): float("nan")},
                      {("RobotLink", "Mouse Sensitivity"): 100},
                      {("RobotLink", "Invert Mouse Y"): "false"}):
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                apply_registry_draft(self.host, draft)
            self.assertEqual(gain.value, 1.0)
            self.host.backend.lock_control.assert_not_called()

    def test_bind_swap_atomic_and_valid_change_dispatches_real_hook(self):
        draft = {("RobotLink", "Bind W"): "S", ("RobotLink", "Bind S"): "W",
                 ("RobotLink", "Mouse Sensitivity"): 2.0}
        apply_registry_draft(self.host, draft)
        self.assertEqual((self.host.control.binds["W"], self.host.control.binds["S"]), ("S", "W"))
        self.assertEqual(self.host.registry.get("RobotLink").get_setting("Mouse Sensitivity").value, 2.0)
        self.assertEqual(self.host.after_setting_changed.call_count, 3)
        self.host.auto_save_config.assert_called_once()
        self.host.backend.arm_match.assert_not_called()
        self.assertFalse(any(self.host.control.key_state.values()))

    def test_new_module_hotkey_rejects_duplicates_and_driver_collision(self):
        for binds in ({"TacticalMap": "W"}, {"TacticalMap": "F12"}, {"TacticalMap": "Escape"},
                      {"TacticalMap": "F10", "CombatTargetHUD": "F10"}):
            with self.assertRaises(ValueError): validate_draft(self.host, {}, binds)
        validate_draft(self.host, {}, {m.name: m.keybind for m in self.host.registry.modules})
        apply_registry_draft(self.host, {}, module_binds={"TacticalMap": "F10"})
        self.assertEqual(self.host.registry.get("TacticalMap").keybind, "F10")

    def test_settings_center_all_tabs_embedded_search_and_staged_mouse(self):
        dialog = build_settings_center(self.host); self.addCleanup(dialog.deleteLater)
        dialog.show(); APP.processEvents()
        self.assertEqual([dialog.tabs.tabText(i) for i in range(dialog.tabs.count())],
                         ["视频", "音频", "游戏", "键盘 / 鼠标", "准星", "界面 / HUD"])
        page = ready_page(dialog, "键盘 / 鼠标")
        page.controls[("RobotLink", "Mouse Sensitivity")].spin.setValue(1.75)
        self.assertEqual(self.host.registry.get("RobotLink").get_setting("Mouse Sensitivity").value, 1)
        dialog.tabs.setCurrentWidget(page); dialog.apply_button.click()
        self.assertEqual(self.host.registry.get("RobotLink").get_setting("Mouse Sensitivity").value, 1.75)
        dialog.search.setText("鼠标灵敏度"); APP.processEvents()
        self.assertFalse(page.controls[("RobotLink", "Mouse Sensitivity")].isHidden())
        self.assertTrue(page.controls[("RobotLink", "Bind W")].isHidden())
        dialog.reject()

    def test_modal_and_child_dialog_f12_without_resume(self):
        dialog = build_settings_center(self.host); self.addCleanup(dialog.deleteLater)
        def keys():
            QTest.keyClick(dialog.search, Qt.Key.Key_F12)
            APP.sendEvent(dialog.search, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F12,
                                                   Qt.KeyboardModifier.NoModifier, "", True))
            child = QInputDialog(dialog)
            def child_keys():
                QTest.keyClick(child, Qt.Key.Key_F12); child.reject()
            QTimer.singleShot(0, child_keys); child.exec()
            dialog.reject()
        QTimer.singleShot(0, keys); dialog.exec()
        self.assertEqual(self.host.backend.trigger_estop.call_count, 2)
        self.host.backend.arm_match.assert_not_called()

    def test_open_lifecycle_no_stale_input_or_auto_arm(self):
        dialog = Mock()
        with patch("rm_client.ui.cs2_settings_center.build_settings_center", return_value=dialog):
            open_settings_center(self.host)
        self.host.backend.lock_control.assert_called_once_with("settings opened")
        dialog.exec.assert_called_once(); dialog.deleteLater.assert_called_once()
        self.host.backend.arm_match.assert_not_called()

    def test_mouse_gain_keeps_fractions_and_reset_clears_them(self):
        bridge = self.host.control
        for _ in range(10): bridge.add_mouse_motion(1, 1, .1)
        self.assertEqual((bridge.mouse_x, bridge.mouse_y), (1, -1))
        bridge.reset_inputs(); bridge.add_mouse_motion(4, 3, 2, True)
        self.assertEqual((bridge.mouse_x, bridge.mouse_y), (8, 6))
        bridge.add_mouse_motion(float("nan"), 0, 1)
        self.assertEqual(bridge.mouse_x, 8)
        bridge.reset_inputs(); bridge.add_mouse_motion(1, 1, .05)
        bridge.reset_inputs(); bridge.add_mouse_motion(1, 1, .05)
        self.assertEqual(bridge.mouse_x, 0)


class PreferenceTests(unittest.TestCase):
    def test_video_numeric_validation_sampling_cap_and_surface(self):
        for data in ({"msaa": True}, {"fps_limit": float("nan")}, {"vsync": "false"}, {"extra": 1}):
            with self.assertRaises(ValueError): video.validate_preferences(data)
        prefs = replace(video.VideoPreferences(), fps_limit=30, hud_hz=144, vsync=False, msaa=4, video_detail="720p")
        with patch.object(video, "current_preferences", return_value=prefs):
            self.assertEqual(video.render_target(144), 30)
            width, height = video.sampled_video_size(2560, 1440)
            self.assertEqual((width, height), (1280, 720))
            surface = QSurfaceFormat(); video.configure_surface(surface)
            self.assertEqual((surface.swapInterval(), surface.samples()), (0, 4))

    def test_video_atomic_save_and_bad_file_fallback(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(video, "preferences_path", return_value=Path(temp)/"video.json"):
            video.current_preferences.cache_clear()
            prefs = replace(video.VideoPreferences(), hud_hz=90)
            video.save_preferences(prefs); self.assertEqual(video.current_preferences(), prefs)
            self.assertFalse(list(Path(temp).glob("*.tmp")))
            video.preferences_path().write_text('{"fps_limit":false}')
            video.current_preferences.cache_clear(); self.assertEqual(video.current_preferences(), video.VideoPreferences())
        video.current_preferences.cache_clear()

    def test_bad_dpi_resolution_checked_before_window_mutation_and_startup_safe(self):
        host = Host(); self.addCleanup(host.deleteLater); host.setMinimumSize(1000, 620)
        with patch.object(host, "devicePixelRatioF", return_value=2):
            with self.assertRaises(ValueError): video.apply_window_settings(host, replace(video.VideoPreferences(), resolution="1280×720"))
            self.assertEqual((host.width(), host.height()), (1280, 720)); host.backend.lock_control.assert_not_called()
        with patch.object(video, "preferences_path", return_value=NS(is_file=lambda:True)), patch.object(video, "current_preferences", return_value=video.VideoPreferences()), patch.object(video, "apply_window_settings", side_effect=ValueError):
            video.restore_startup_window(host)

    def test_audio_validated_atomic_volume_and_focus(self):
        for data in ({"master_volume": True}, {"master_volume":101}, {"device_id":"../abc"}, {"mute_unfocused":1}):
            with self.assertRaises(ValueError): audio.validate_audio(data)
        with tempfile.TemporaryDirectory() as temp, patch.object(audio, "audio_path", return_value=Path(temp)/"audio.json"):
            audio.current_audio.cache_clear()
            audio.save_audio(audio.AudioPreferences(master_volume=50))
            self.assertEqual(audio.cue_volume(60), 30)
            self.assertEqual(json.loads(audio.audio_path().read_text())["master_volume"], 50)
            self.assertFalse(list(Path(temp).glob("*.tmp")))
        audio.current_audio.cache_clear(); audio.selected_output.cache_clear()


if __name__ == "__main__": unittest.main()
