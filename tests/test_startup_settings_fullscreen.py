"""Issue #12: real Qt window events plus the production launcher/settings routes.

No network, device, physical display or OpenGL compositor is exercised. Run:
    QT_QPA_PLATFORM=offscreen python tests/test_startup_settings_fullscreen.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
_DATA = tempfile.TemporaryDirectory(prefix="polar-issue12-")
os.environ["POLAR_USER_DATA_ROOT"] = _DATA.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QImage, QKeyEvent, QKeySequence, QPainter, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QLineEdit, QWidget
from rm_client.control import KeyboardControlBridge
from rm_client.model import build_default_registry, RobotState, CONTROL_BIND_OPTIONS, MODULE_BIND_OPTIONS
from rm_client.ui.cs2_settings_center import build_settings_center, open_settings_center, validate_draft
from rm_client.ui.cs2_video_settings import VideoPreferences, apply_window_settings, open_video_settings
from rm_client.ui.polar.mixins.input_dispatch_mixin import InputDispatchMixin
from rm_client.ui.polar.mixins.escape_menu_mixin import EscapeMenuMixin
from rm_client.ui.window_shortcuts import WindowShortcuts, set_fullscreen

APP = QApplication.instance() or QApplication([])


class Host(InputDispatchMixin, EscapeMenuMixin, QWidget):
    """Real QWidget and production key/dispatch methods; inert device adapters."""
    def __init__(self):
        super().__init__()
        self.setGeometry(40, 60, 600, 400)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.registry = build_default_registry()
        self.state = RobotState()
        self.control = KeyboardControlBridge()
        self.control.release_all = Mock(wraps=self.control.release_all)
        self.backend = Mock(mqtt=NS(mock_output=True))
        self.backend.trigger_estop.return_value = (True, "")
        self.frame_scheduler = Mock()
        self.invalidate_transient_overlay_scene = Mock()
        self.invalidate_gui_cache = Mock()
        self.after_setting_changed = Mock()
        self.auto_save_config = Mock()
        self.toast = Mock()
        self.home_visible = True
        self.startup_step = "scenario"
        self.gui_visible = self.economy_visible = self.search_focus = False
        self.binding_module = None
        self.student_ui = NS(visible=False)
        self.hero_deploy_ui = NS(visible=False)
        self.match_chat = NS(input_open=False)
        self.quick_action_wheel = NS(active=False)
        self.overlay_coordinator = NS(radial_active=False, blocks_combat_input=False)
        self.handle_student_action = Mock(return_value=False)
        self.handle_student_key = Mock(return_value=False)
        self.handle_startup_key = Mock(return_value=False)
        self.handle_control_key_event = Mock(return_value=False)
        self.minecraft_hud_active = lambda: False
        self._window_shortcuts = WindowShortcuts(self)
        self.show()
        self.activateWindow()
        APP.processEvents()


class FullscreenTests(unittest.TestCase):
    def setUp(self):
        self.w = Host()

    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def key(self, widget=None):
        QTest.keyClick(widget or self.w, Qt.Key.Key_F11)
        APP.processEvents()

    def test_repeated_f11_both_views_restores_geometry_and_preserves_route(self):
        for home in (True, False):
            self.w.home_visible = home
            original = self.w.geometry()
            for _ in range(5):
                self.key()
                self.assertTrue(self.w.isFullScreen())
                self.key()
                self.assertFalse(self.w.isFullScreen())
                self.assertEqual(self.w.geometry(), original)
                self.assertEqual(self.w.home_visible, home)
            self.assertEqual(self.w.startup_step, "scenario")
        self.w.handle_control_key_event.assert_not_called()
        self.w.backend.arm_match.assert_not_called()

    def test_rapid_pairs_and_resized_normal_window(self):
        self.w.setGeometry(70, 90, 550, 350)
        original = self.w.geometry()
        for _ in range(8):
            QTest.keyClick(self.w, Qt.Key.Key_F11)
            QTest.keyClick(self.w, Qt.Key.Key_F11)
        APP.processEvents()
        self.assertFalse(self.w.isFullScreen())
        self.assertEqual(self.w.geometry(), original)

    def test_repeat_shortcut_override_and_release_never_toggle_twice(self):
        for kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyRelease):
            APP.sendEvent(self.w, QKeyEvent(kind, Qt.Key.Key_F11, Qt.KeyboardModifier.NoModifier))
            self.assertFalse(self.w.isFullScreen())
        self.key()
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            for _ in range(4):
                APP.sendEvent(self.w, QKeyEvent(kind, Qt.Key.Key_F11, Qt.KeyboardModifier.NoModifier, "", True))
                self.assertTrue(self.w.isFullScreen())
        self.assertEqual(self.w.backend.lock_control.call_count, 1)
        self.key()
        self.assertFalse(self.w.isFullScreen())

    def test_global_precedence_preserves_overlays_capture_and_old_f11_binds(self):
        module = self.w.registry.get("TacticalMap")
        module.keybind = "F11"  # old profiles may still contain this binding
        original_enabled = module.enabled
        for name, value in (("search_focus", True), ("binding_module", module),
                            ("_escape_menu_visible", True), ("gui_visible", True)):
            with self.subTest(owner=name):
                setattr(self.w, name, value)
                self.key(); self.assertTrue(self.w.isFullScreen())
                self.key(); self.assertFalse(self.w.isFullScreen())
                self.assertEqual(getattr(self.w, name), value)
                setattr(self.w, name, None if name == "binding_module" else False)
        self.w.home_visible = False
        for owner, attr in ((self.w.match_chat, "input_open"), (self.w.quick_action_wheel, "active"),
                            (self.w.student_ui, "visible"), (self.w.hero_deploy_ui, "visible")):
            setattr(owner, attr, True)
            self.key(); self.key()
            self.assertTrue(getattr(owner, attr))
            setattr(owner, attr, False)
        self.assertEqual(module.keybind, "F11")
        self.assertEqual(module.enabled, original_enabled)
        self.w.handle_student_key.assert_not_called()
        self.w.handle_control_key_event.assert_not_called()

    def test_child_focus_beats_conflicting_qshortcut(self):
        child = QLineEdit(self.w); child.show(); child.setFocus()
        shortcut = QShortcut(QKeySequence("F11"), self.w)
        triggered = Mock(); shortcut.activated.connect(triggered)
        self.key(child); self.assertTrue(self.w.isFullScreen())
        self.key(child); self.assertFalse(self.w.isFullScreen())
        triggered.assert_not_called()
        self.assertEqual(child.text(), "")

    def test_nested_modal_f11_and_f12_leave_settings_open(self):
        dialog = build_settings_center(self.w)
        errors = []
        def check_keys():
            try:
                self.key(dialog.search); self.assertTrue(self.w.isFullScreen())
                nested = QInputDialog(dialog)
                def nested_keys():
                    try:
                        self.key(nested); self.assertFalse(self.w.isFullScreen())
                        QTest.keyClick(nested, Qt.Key.Key_F12)
                    except BaseException as exc: errors.append(exc)
                    finally: nested.reject()
                QTimer.singleShot(0, nested_keys)
                nested.exec()
                self.assertTrue(dialog.isVisible())
                QTest.keyClick(dialog.search, Qt.Key.Key_F12)
            except BaseException as exc: errors.append(exc)
            finally: dialog.reject()
        QTimer.singleShot(0, check_keys)
        dialog.exec(); dialog.deleteLater()
        if errors: raise errors[0]
        self.assertEqual(self.w.backend.trigger_estop.call_count, 2)
        self.w.backend.arm_match.assert_not_called()

    def test_unrelated_window_and_hidden_host_are_ignored(self):
        other = QWidget(); other.show()
        self.key(other)
        self.assertFalse(self.w.isFullScreen())
        self.w.hide()
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F11, Qt.KeyboardModifier.NoModifier)
        self.assertFalse(self.w._window_shortcuts.eventFilter(self.w, event))
        self.assertFalse(self.w.isFullScreen())
        self.w.backend.lock_control.assert_not_called()
        other.close(); other.deleteLater()

    def test_maximized_state_and_normal_size_restore(self):
        normal = self.w.geometry()
        self.w.showMaximized(); APP.processEvents()
        self.assertTrue(self.w.isMaximized())
        for _ in range(3):
            self.key(); self.assertTrue(self.w.isFullScreen())
            self.key(); self.assertTrue(self.w.isMaximized())
        self.w.showNormal(); APP.processEvents()
        self.assertEqual(self.w.geometry(), normal)

    def test_release_and_cache_invalidation_precede_window_transition(self):
        self.w.control.key_state["W"] = True
        self.w._control_mouse_point = QPointF(90, 80)
        order = []
        self.w.backend.lock_control.side_effect = lambda reason: order.append("lock")
        original_release = self.w.control.release_all
        self.w.control.release_all = Mock(side_effect=lambda *args: (order.append("release"), original_release(*args)))
        self.w.invalidate_transient_overlay_scene.side_effect = lambda: order.append("invalidate")
        original_show = self.w.showFullScreen
        self.w.showFullScreen = lambda: (order.append("show"), original_show())
        self.key()
        self.assertEqual(order[:4], ["lock", "release", "invalidate", "show"])
        self.assertFalse(any(self.w.control.key_state.values()))
        self.assertIsNone(self.w._control_mouse_point)
        self.assertFalse(self.w.tactical_map_hold)
        self.w.backend.arm_match.assert_not_called()

    def test_settings_entry_and_f11_share_geometry_snapshot(self):
        original = self.w.geometry()
        apply_window_settings(self.w, VideoPreferences(window_mode="Borderless"))
        self.assertTrue(self.w.isFullScreen())
        self.key()
        self.assertEqual(self.w.geometry(), original)
        self.key()
        apply_window_settings(self.w, VideoPreferences())
        self.assertFalse(self.w.isFullScreen())
        self.assertEqual(self.w.geometry(), original)

    def test_explicit_resolution_exits_fullscreen_and_maximized(self):
        self.w.showMaximized(); APP.processEvents(); self.key()
        apply_window_settings(self.w, VideoPreferences(resolution="1280×720"))
        APP.processEvents()
        self.assertFalse(self.w.isFullScreen())
        self.assertFalse(self.w.isMaximized())
        dpr = self.w.devicePixelRatioF()
        self.assertEqual((self.w.width(), self.w.height()),
                         (round(1280 / dpr), round(720 / dpr)))

    def test_video_dialog_reflects_current_mode_without_persisting_f11(self):
        from rm_client.ui import cs2_video_settings as video
        with patch.object(video, "save_preferences") as save:
            self.key()
            dialog = open_video_settings(self.w, embedded=True)
            self.assertEqual(dialog.controls["window_mode"].currentData(), "Borderless")
            dialog.deleteLater()
            self.key()
            save.assert_not_called()

    def test_filter_teardown_does_not_capture_other_windows(self):
        host = Host(); host.close(); host.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.key()
        self.assertTrue(self.w.isFullScreen())


class StartupSettingsTests(unittest.TestCase):
    def setUp(self): self.w = Host()
    def tearDown(self):
        self.w.hide(); self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_startup_action_keeps_every_page_and_opens_real_modal(self):
        for page in ("scenario", "match", "robot"):
            for outcome in ("accept", "reject", "close", "escape"):
                with self.subTest(page=page, outcome=outcome):
                    self.w.startup_step = page
                    errors = []
                    def dismiss():
                        try:
                            dialog = APP.activeModalWidget()
                            self.assertEqual(dialog.objectName(), "JelloSettingsCenter")
                            self.assertTrue(self.w.home_visible)
                            self.assertEqual(self.w.startup_step, page)
                            if outcome == "escape": QTest.keyClick(dialog.search, Qt.Key.Key_Escape)
                            else: getattr(dialog, outcome)()
                        except BaseException as exc:
                            errors.append(exc)
                            if APP.activeModalWidget(): APP.activeModalWidget().reject()
                    QTimer.singleShot(0, dismiss)
                    self.w.dispatch("startup_open_settings", None, QPointF(), 1, QRectF())
                    if errors: raise errors[0]
                    self.assertTrue(self.w.home_visible)
                    self.assertEqual(self.w.startup_step, page)
                    self.assertFalse(self.w.gui_visible)
        self.w.backend.apply_scenario.assert_not_called()
        self.w.backend.arm_match.assert_not_called()
        self.assertEqual(self.w.backend.lock_control.call_count, 12)
        self.assertEqual(self.w.invalidate_transient_overlay_scene.call_count, 12)

    def test_operator_settings_still_returns_to_operator_menu(self):
        self.w.home_visible = False
        self.w._escape_menu_visible = True
        QTimer.singleShot(0, lambda: APP.activeModalWidget().reject())
        self.w.activate_escape_menu_choice(1)
        self.assertFalse(self.w.home_visible)
        self.assertTrue(self.w.escape_menu_visible)
        self.w.backend.arm_match.assert_not_called()

    def test_f11_is_reserved_in_preferences_and_driver_bridge(self):
        self.assertNotIn("F11", CONTROL_BIND_OPTIONS)
        self.assertNotIn("F11", MODULE_BIND_OPTIONS)
        self.assertFalse(self.w.control.set_bind("W", "F11"))
        self.assertEqual(self.w.control.binds["W"], "W")
        for draft, binds in (({("RobotLink", "Bind W"): "F11"}, {}), ({}, {"TacticalMap": "F11"})):
            with self.assertRaises(ValueError): validate_draft(self.w, draft, binds)


class LauncherIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rm_client.ui.polar_app import PolarRMWindow
        cls.w = PolarRMWindow()
        cls.w.timer.stop(); cls.w._gpu_prewarm_timer.stop()
        cls.w.startup_stagger_value = lambda *args: 1.0
        cls.w.anim.value = lambda _name, target, *args, **kwargs: target

    @classmethod
    def tearDownClass(cls):
        cls.w.close(); cls.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def assert_settings_hotspot(self, width, height):
        settings = [h for h in self.w.hotspots if h.action == "startup_open_settings"]
        self.assertEqual(len(settings), 1)
        self.assertTrue(QRectF(0, 0, width, height).contains(settings[0].rect))
        for hotspot in self.w.hotspots:
            if hotspot is not settings[0]:
                self.assertFalse(hotspot.rect.intersects(settings[0].rect), hotspot.action)

    def test_production_constructor_installs_window_shortcuts(self):
        import gc
        gc.collect()
        shortcut = self.w.findChild(WindowShortcuts)
        self.assertIsInstance(shortcut, WindowShortcuts)
        self.assertIs(shortcut.parent(), self.w)

    def test_gpu_launcher_settings_visible_on_all_pages_and_sizes(self):
        for width, height in ((1000, 620), (1280, 720), (1920, 1080)):
            for page in ("scenario", "match", "robot"):
                with self.subTest(size=(width, height), page=page):
                    self.w.startup_step = page; self.w.hotspots.clear()
                    self.w.build_gpu_startup_launcher(width, height, page=page, settled=True)
                    self.assert_settings_hotspot(width, height)

    def test_cpu_launcher_settings_visible_in_both_styles_all_pages(self):
        for style in ("LiquidMenu", "PolarLauncher"):
            self.w.registry.get("Scenario").get_setting("Startup Style").value = style
            for page in ("scenario", "match", "robot"):
                with self.subTest(style=style, page=page):
                    self.w.startup_step = page; self.w.hotspots.clear()
                    image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
                    image.fill(Qt.GlobalColor.black)
                    painter = QPainter(image)
                    try: self.w.draw_startup_page_content(painter, 1280, 720, page)
                    finally: painter.end()
                    self.assert_settings_hotspot(1280, 720)


if __name__ == "__main__":
    unittest.main()
