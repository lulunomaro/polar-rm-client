"""Fullscreen state/Win32 ABI and resize contracts; no physical GPU required.

The native API is mocked. These tests do not certify Windows DWM presentation.
"""
from __future__ import annotations

import ast
import ctypes
from dataclasses import replace
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget
from rm_client.ui import cs2_video_settings as video
from rm_client.ui import fullscreen_composition as fullscreen

APP = QApplication.instance() or QApplication([])


class NativeBorderTests(unittest.TestCase):
    def setUp(self):
        self.error = 0
        self.set_error = patch.object(ctypes, "set_last_error", self.set_last_error, create=True)
        self.get_error = patch.object(ctypes, "get_last_error", lambda: self.error, create=True)
        self.set_error.start(); self.get_error.start()
        self.addCleanup(self.set_error.stop); self.addCleanup(self.get_error.stop)

    def set_last_error(self, value):
        self.error = value

    def test_pointer_width_apis_and_handle_arguments(self):
        for pointer_size, suffix, result_type in ((8, "LongPtrW", ctypes.c_ssize_t),
                                                  (4, "LongW", ctypes.c_int32)):
            library = NS()
            getter = Mock(return_value=0); setter = Mock(return_value=0)
            setattr(library, "GetWindow" + suffix, getter)
            setattr(library, "SetWindow" + suffix, setter)
            fullscreen._window_style_functions.cache_clear()
            with patch.object(ctypes, "WinDLL", return_value=library, create=True) as dll, \
                 patch.object(ctypes, "sizeof", return_value=pointer_size):
                self.assertEqual(fullscreen._window_style_functions(), (getter, setter))
            dll.assert_called_once_with("user32", use_last_error=True)
            self.assertEqual(getter.argtypes, (ctypes.c_void_p, ctypes.c_int))
            self.assertIs(getter.restype, result_type)
            self.assertEqual(setter.argtypes, (ctypes.c_void_p, ctypes.c_int, result_type))
            self.assertIs(setter.restype, result_type)
        fullscreen._window_style_functions.cache_clear()

    def test_preserves_unrelated_style_bits_and_large_hwnd(self):
        hwnd = 0x123456789ABC
        style = 0x960A0000
        getter = Mock(side_effect=[style, style | fullscreen._WS_BORDER])
        setter = Mock(return_value=style)
        with patch.object(fullscreen, "_window_style_functions", return_value=(getter, setter)):
            self.assertTrue(fullscreen._ensure_native_border(hwnd))
        setter.assert_called_once_with(hwnd, -16, style | fullscreen._WS_BORDER)
        self.assertEqual(getter.call_count, 2)

    def test_already_guarded_is_idempotent(self):
        getter = Mock(return_value=fullscreen._WS_BORDER); setter = Mock()
        with patch.object(fullscreen, "_window_style_functions", return_value=(getter, setter)):
            self.assertFalse(fullscreen._ensure_native_border(42))
        setter.assert_not_called()

    def test_zero_previous_style_is_success_when_last_error_is_zero(self):
        getter = Mock(side_effect=[0, fullscreen._WS_BORDER]); setter = Mock(return_value=0)
        self.error = 5  # Stale errors must be cleared before each call.
        with patch.object(fullscreen, "_window_style_functions", return_value=(getter, setter)):
            self.assertTrue(fullscreen._ensure_native_border(42))

    def test_get_and_set_failures_do_not_report_success(self):
        def fail(*args):
            self.error = 5
            return 0
        for read_fails in (True, False):
            getter = Mock(side_effect=fail) if read_fails else Mock(return_value=0x10000000)
            setter = Mock(side_effect=fail)
            with patch.object(fullscreen, "_window_style_functions", return_value=(getter, setter)):
                with self.assertRaises(OSError): fullscreen._ensure_native_border(42)
            if read_fails: setter.assert_not_called()

    def test_failed_readback_is_reported(self):
        with patch.object(fullscreen, "_window_style_functions", return_value=(Mock(return_value=0x10000000), Mock(return_value=0))):
            with self.assertRaisesRegex(OSError, "did not retain"):
                fullscreen._ensure_native_border(42)


class GuardLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.host = QWidget()
        self.addCleanup(self.host.deleteLater)
        self.visible = True; self.full = True; self.minimized = False; self.hwnd = 42
        self.host.isVisible = lambda: self.visible
        self.host.isFullScreen = lambda: self.full
        self.host.isMinimized = lambda: self.minimized
        self.host.internalWinId = lambda: self.hwnd
        self.host.winId = Mock(side_effect=AssertionError("must not force a native handle"))
        with patch.object(fullscreen.sys, "platform", "win32"):
            self.guard = fullscreen.install_fullscreen_composition_guard(self.host)

    def test_installs_once_and_other_platforms_are_untouched(self):
        with patch.object(fullscreen.sys, "platform", "win32"):
            self.assertIs(fullscreen.install_fullscreen_composition_guard(self.host), self.guard)
        with patch.object(fullscreen.sys, "platform", "linux"):
            self.assertIsNone(fullscreen.install_fullscreen_composition_guard(self.host))
        self.assertIs(self.guard.parent(), self.host)

    def test_queued_check_uses_latest_state_and_native_handle(self):
        with patch.object(fullscreen, "_ensure_native_border") as native:
            self.guard.eventFilter(self.host, QEvent(QEvent.Type.WinIdChange))
            self.hwnd = 84
            APP.processEvents()
            native.assert_called_once_with(84)
            native.reset_mock()
            self.guard.eventFilter(self.host, QEvent(QEvent.Type.WindowStateChange))
            self.full = False
            APP.processEvents()
            native.assert_not_called()

    def test_hidden_minimized_windowed_and_destroyed_handles_are_skipped(self):
        with patch.object(fullscreen, "_ensure_native_border") as native:
            for attr, value in (("visible", False), ("full", False), ("minimized", True), ("hwnd", 0)):
                old = getattr(self, attr); setattr(self, attr, value)
                self.guard.apply()
                setattr(self, attr, old)
            native.assert_not_called()
            self.host.winId.assert_not_called()

    def test_reenter_restore_and_recreated_handle_are_guarded(self):
        with patch.object(fullscreen, "_ensure_native_border") as native:
            for attr, value, calls in (("full", True, 1), ("full", False, 1),
                                       ("full", True, 2), ("minimized", True, 2),
                                       ("minimized", False, 3), ("hwnd", 100, 4)):
                setattr(self, attr, value)
                self.guard.eventFilter(self.host, QEvent(QEvent.Type.WindowStateChange))
                APP.processEvents()
                self.assertEqual(native.call_count, calls)
            self.assertEqual(native.call_args.args, (100,))

    def test_native_failure_is_visible_and_does_not_escape_qt_callback(self):
        with patch.object(fullscreen, "_ensure_native_border", side_effect=OSError("denied")), \
             self.assertLogs(fullscreen.__name__, level="WARNING") as logs:
            self.guard.apply()
        self.assertIn("denied", logs.output[0])


class RealWidgetLifecycleTests(unittest.TestCase):
    def test_real_qt_state_transitions_and_queued_teardown(self):
        import shiboken6
        host = QWidget()
        guard = fullscreen._FullscreenCompositionGuard(host)
        seen = []
        def observe(hwnd):
            seen.append((hwnd, host.isFullScreen(), host.isMinimized(), host.isVisible()))
        try:
            with patch.object(fullscreen, "_ensure_native_border", side_effect=observe):
                host.showFullScreen(); guard.apply(); APP.processEvents()
                first = len(seen)
                self.assertGreaterEqual(first, 1)
                self.assertTrue(all(row[1:] == (True, False, True) for row in seen))
                host.showNormal(); APP.processEvents()
                self.assertEqual(len(seen), first)
                host.showFullScreen(); APP.processEvents()
                self.assertGreater(len(seen), first)
                second = len(seen)
                host.showMinimized(); APP.processEvents()
                self.assertEqual(len(seen), second)
                host.showFullScreen(); APP.processEvents()
                self.assertGreater(len(seen), second)
                guard.eventFilter(host, QEvent(QEvent.Type.WinIdChange))
                shiboken6.delete(host)
                APP.processEvents()
        finally:
            if shiboken6.isValid(host): shiboken6.delete(host)


class ApplySettingsTests(unittest.TestCase):
    def make_host(self):
        calls = []
        host = NS(backend=NS(lock_control=lambda reason: calls.append("lock")),
                  control=NS(release_all=lambda *args: calls.append("release")),
                  state=object(), registry=object(),
                  showFullScreen=lambda: calls.append("fullscreen"),
                  showNormal=lambda: calls.append("normal"),
                  invalidate_transient_overlay_scene=lambda: calls.append("invalidate"),
                  frame_scheduler=NS(force=lambda: calls.append("force")))
        return host, calls

    def test_guard_follows_fullscreen_and_preserves_control_lock_order(self):
        host, calls = self.make_host()
        def install(host):
            calls.append("install")
            return NS(apply=lambda: calls.append("guard"))
        with patch.object(fullscreen, "install_fullscreen_composition_guard", side_effect=install):
            video.apply_window_settings(host, replace(video.VideoPreferences(), window_mode="Borderless"))
        self.assertEqual(calls, ["lock", "release", "install", "fullscreen", "guard", "invalidate", "force"])

    def test_windowed_uses_existing_qt_restore_without_native_style_mutation(self):
        host, calls = self.make_host()
        with patch.object(fullscreen, "install_fullscreen_composition_guard") as install:
            video.apply_window_settings(host, video.VideoPreferences())
        install.assert_not_called()
        self.assertEqual(calls, ["lock", "release", "normal", "invalidate", "force"])


class ResizeOrderingTests(unittest.TestCase):
    def test_all_cache_metadata_is_invalid_before_qt_synchronous_resize_paint(self):
        source = ROOT / "rm_client/ui/polar_app.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PolarRMWindow")
        methods = [n for n in window.body if isinstance(n, ast.FunctionDef) and n.name in
                   {"resizeEvent", "invalidate_transient_overlay_scene"}]
        snapshots = []
        class QtBase:
            def resizeEvent(self, event):
                # Qt recreates the FBO and invokes paintGL inside this call.
                snapshots.append(dict(vars(self)))
        cls = ast.ClassDef(name="Window", bases=[ast.Name(id="QtBase", ctx=ast.Load())],
                           keywords=[], body=methods, decorator_list=[])
        scope = {"QtBase": QtBase}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(source), "exec"), scope)
        w = scope["Window"]()
        w.gui_cache = {"stale": True}; w.map_display_cache = {"stale": True}
        w.gpu_compositor = NS(invalidate_transient_layers=Mock())
        keys = ("_gpu_background_key", "_gpu_background_transform_key", "_gpu_control_backdrop_key",
                "_gpu_blur_source_key", "_gpu_static_ui_key", "_startup_static_page_key")
        for key in keys: setattr(w, key, ("old",))
        w._gpu_motion_layer_ready = True; w._gpu_motion_layer_updated_at = 123
        w._gpu_warm_pending = False
        w.resizeEvent(object())
        self.assertEqual(len(snapshots), 1)
        painted = snapshots[0]
        self.assertFalse(painted["_gpu_motion_layer_ready"])
        self.assertEqual(painted["_gpu_motion_layer_updated_at"], 0)
        self.assertTrue(painted["_gpu_warm_pending"])
        for key in keys: self.assertIsNone(painted[key], key)
        self.assertEqual(w.gui_cache, {}); self.assertEqual(w.map_display_cache, {})
        w.gpu_compositor.invalidate_transient_layers.assert_called_once()


if __name__ == "__main__": unittest.main()
