"""Draw/interaction ownership with real layout, orchestration and Qt raster.

One legacy parity test constructs an unshown window with capture/media disabled;
all others use a lightweight host. No GL context or connected device is needed.
These checks do not claim GPU-driver or fullscreen pixel acceptance.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from rm_client.model import LEGACY_GUI_MODES, PRIMARY_GUI_MODE, build_default_registry
from rm_client.ui.clickgui_gpu import ClickGuiDrawList
from rm_client.ui.gui_cache import ClickGuiLayerCache
from rm_client.ui.jello_clickgui import JelloClickGuiRenderer, JelloClickGuiState
from rm_client.ui.polar.clickgui_frame import ClickGuiFrame, build_clickgui_frame
from rm_client.ui.polar.mixins.clickgui_builders_mixin import ClickGuiBuildersMixin
from rm_client.ui.polar.primitives import Hotspot
from rm_client.ui.polar_app import PolarRMWindow
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle


APP = QApplication.instance() or QApplication([])


class Host(ClickGuiBuildersMixin):
    add_hotspot = PolarRMWindow.add_hotspot

    def __init__(self):
        self.hotspots = []
        self.gui_scroll_viewports = []
        self.gui_visible = True
        self._hotspot_clip = None
        self.mode = PRIMARY_GUI_MODE
        self.hover_tooltip = ""
        self.binding_module = None
        self.registry = build_default_registry()
        self.jello_state = JelloClickGuiState()
        self.jello_clickgui = JelloClickGuiRenderer(ROOT / "assets/jello")
        self.hover_point = QPointF(-1, -1)
        self.gui_timeline = NS(progress=1.0, target=1.0, visual_factor=lambda: 1.0)

    def active_clickgui_mode(self):
        return self.mode

    def current_profile(self):
        return "Default"

    def font(self, key, size):
        font = QFont("Sans Serif")
        font.setPointSizeF(size)
        return font


class FrameOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.old_hotspot = Hotspot(QRectF(1, 2, 3, 4), "existing_map")
        self.old_viewport = {"key": "existing_hud"}
        self.host.hotspots.append(self.old_hotspot)
        self.host.gui_scroll_viewports.append(self.old_viewport)
        self.hotspots = self.host.hotspots
        self.viewports = self.host.gui_scroll_viewports
        self.clip = QRectF(20, 20, 2, 2)
        self.host._hotspot_clip = self.clip

    def assert_original_geometry(self):
        self.assertIs(self.host.hotspots, self.hotspots)
        self.assertEqual(self.host.hotspots, [self.old_hotspot])
        self.assertIs(self.host.gui_scroll_viewports, self.viewports)
        self.assertEqual(self.host.gui_scroll_viewports, [self.old_viewport])
        self.assertIs(self.host._hotspot_clip, self.clip)

    def test_prepare_isolated_publish_matches_draw_and_does_not_rebuild(self):
        frame = build_clickgui_frame(self.host, 1280, 720, 1.0)
        self.assert_original_geometry()
        self.assertIsInstance(frame, ClickGuiFrame)
        self.assertTrue(frame.commands.commands)
        self.assertTrue(frame.hotspots)
        self.assertTrue(frame.scroll_viewports)
        self.assertNotIn(self.old_hotspot, frame.hotspots)
        self.assertNotIn(self.old_viewport, frame.scroll_viewports)
        self.host.build_gpu_clickgui = Mock(side_effect=AssertionError("layout repeated"))
        for _ in range(3):
            frame.publish_interactions(self.host)
            self.assertEqual(self.host.hotspots, list(frame.hotspots))
            self.assertEqual(self.host.gui_scroll_viewports, list(frame.scroll_viewports))
        self.host.build_gpu_clickgui.assert_not_called()

    def test_clearing_published_lists_does_not_erase_prepared_frame(self):
        frame = build_clickgui_frame(self.host, 1280, 720, 1.0)
        frame.publish_interactions(self.host)
        self.host.hotspots.clear()
        self.host.gui_scroll_viewports.clear()
        self.assertTrue(frame.hotspots)
        self.assertTrue(frame.scroll_viewports)
        frame.publish_interactions(self.host)
        self.assertEqual(self.host.hotspots, list(frame.hotspots))
        self.assertEqual(self.host.gui_scroll_viewports, list(frame.scroll_viewports))

    def test_failed_layout_preserves_previous_geometry_and_clip(self):
        def fail(*args):
            self.host.add_hotspot(QRectF(0, 0, 100, 100), "partial")
            self.host.gui_scroll_viewports = [{"key": "partial"}]
            self.host._hotspot_clip = QRectF(0, 0, 50, 50)
            raise RuntimeError("interrupted layout")

        self.host.build_gpu_clickgui = fail
        with self.assertRaisesRegex(RuntimeError, "interrupted layout"):
            build_clickgui_frame(self.host, 1280, 720, 1.0)
        self.assert_original_geometry()

    def test_missing_clip_attribute_restored_after_success_and_failure(self):
        del self.host._hotspot_clip
        build_clickgui_frame(self.host, 1280, 720, 1.0)
        self.assertFalse(hasattr(self.host, "_hotspot_clip"))
        self.host.build_gpu_clickgui = Mock(side_effect=RuntimeError("failed"))
        with self.assertRaises(RuntimeError):
            build_clickgui_frame(self.host, 1280, 720, 1.0)
        self.assertFalse(hasattr(self.host, "_hotspot_clip"))

    def test_closing_after_prepare_cannot_republish_stale_targets(self):
        frame = build_clickgui_frame(self.host, 1280, 720, 1.0)
        self.host.gui_visible = False
        frame.publish_interactions(self.host)
        self.assertEqual(self.host.hotspots, [])
        self.assertEqual(self.host.gui_scroll_viewports, [])
        self.assertTrue(frame.commands.commands)

    def test_frame_built_closed_stays_display_only_after_reopen(self):
        self.host.gui_visible = False
        frame = build_clickgui_frame(self.host, 1280, 720, 0.5)
        self.assert_original_geometry()
        self.assertEqual(frame.hotspots, ())
        self.assertEqual(frame.scroll_viewports, ())
        self.assertTrue(frame.commands.commands)
        self.host.gui_visible = True
        frame.publish_interactions(self.host)
        self.assertEqual(self.host.hotspots, [])
        self.assertEqual(self.host.gui_scroll_viewports, [])

    def test_settings_payload_keeps_original_identity_and_clip(self):
        self.host.jello_state.open_settings("ClickGui")
        self.host.jello_state.settings_panel_animation.reset(1)
        self.host.jello_state.settings_background_animation.reset(1)
        frame = build_clickgui_frame(self.host, 1280, 720, 1.0)
        settings = self.host.registry.get("ClickGui").settings
        setting_targets = [h for h in frame.hotspots if h.action.startswith("setting_")]
        self.assertTrue(setting_targets)
        self.assertTrue(all(any(h.payload is s for s in settings) for h in setting_targets))
        viewport = next(v for v in frame.scroll_viewports if v["mode"] == "jello_settings")
        self.assertTrue(all(viewport["rect"].contains(h.rect) for h in setting_targets))
        self.assert_original_geometry()


class BuilderCompatibilityTests(unittest.TestCase):
    @staticmethod
    def paint_jello(host, size, dpr=1, commands=None):
        image = QImage(size[0] * dpr, size[1] * dpr, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor("#16374a"))
        painter = QPainter(image)
        try:
            if commands is None:
                host.jello_clickgui.paint_compat(host, host.jello_state, painter, *size, 1.0)
            else:
                # Reuse the real compatibility consumer to compare the captured
                # command stream, without a second layout or fake paint calls.
                with patch.object(host.jello_clickgui, "build", return_value=commands):
                    host.jello_clickgui.paint_compat(host, host.jello_state, painter, *size, 1.0)
        finally:
            painter.end()
        return image

    def test_jello_commands_and_geometry_match_direct_builder_at_multiple_sizes(self):
        for size in ((640, 400), (1280, 720), (2560, 1440)):
            for settings_open in (False, True):
                with self.subTest(size=size, settings=settings_open):
                    direct, captured = Host(), Host()
                    for host in (direct, captured):
                        if settings_open:
                            host.jello_state.open_settings("ClickGui")
                            host.jello_state.settings_panel_animation.reset(1)
                            host.jello_state.settings_background_animation.reset(1)
                    commands = direct.build_gpu_clickgui(*size, 1.0)
                    frame = build_clickgui_frame(captured, *size, 1.0)
                    self.assertEqual(frame.commands, commands)
                    self.assertEqual(list(frame.hotspots), direct.hotspots)
                    self.assertEqual(list(frame.scroll_viewports), direct.gui_scroll_viewports)
                    self.assertEqual(captured.hotspots, [])
                    self.assertEqual(captured.gui_scroll_viewports, [])

    def test_jello_frame_preserves_compatibility_pixels_at_dpr_one_and_two(self):
        for dpr in (1, 2):
            for settings_open in (False, True):
                with self.subTest(dpr=dpr, settings=settings_open):
                    direct, captured = Host(), Host()
                    for host in (direct, captured):
                        if settings_open:
                            host.jello_state.open_settings("ClickGui")
                            host.jello_state.settings_panel_animation.reset(1)
                            host.jello_state.settings_background_animation.reset(1)
                    frame = build_clickgui_frame(captured, 640, 400, 1.0)
                    expected = self.paint_jello(direct, (640, 400), dpr)
                    actual = self.paint_jello(captured, (640, 400), dpr, frame.commands)
                    self.assertEqual(actual, expected)

    def test_legacy_modes_preserve_drawlist_api_and_ordered_dispatch(self):
        builders = {
            "PolarPanel": "build_gpu_panel_gui",
            "PolarOld": "build_gpu_old_gui",
            "PolarNew": "build_gpu_new_gui",
            "PolarDropdown": "build_gpu_dropdown_gui",
            "PolarUnified": "build_gpu_polar_unified",
            "PolarMulti": "build_gpu_polar_multi",
        }
        self.assertEqual(set(builders), set(LEGACY_GUI_MODES))
        for mode, selected in builders.items():
            with self.subTest(mode=mode):
                host = Host()
                host.mode = mode
                calls = []

                def record(name):
                    def build(commands, *args):
                        calls.append(name)
                        commands.rect(QRectF(0, 0, 100, 40), 2, QColor("white"))
                        host._hotspot_clip = QRectF(0, 0, 20, 20)
                        host.add_hotspot(QRectF(0, 0, 100, 40), name)
                        host._hotspot_clip = None
                        host.register_gui_scroll_viewport(mode, name, QRectF(0, 0, 20, 20), 40, 20)
                    return build

                for name in builders.values():
                    setattr(host, name, record(name))
                host.build_gpu_legacy_return = record("return")
                host.build_gpu_toasts = record("toasts")
                frame = build_clickgui_frame(host, 1280, 720, 1.0)
                self.assertIsInstance(frame.commands, ClickGuiDrawList)
                self.assertEqual(calls, [selected, "return", "toasts"])
                self.assertEqual([h.action for h in frame.hotspots], calls)
                self.assertTrue(all(h.rect == QRectF(0, 0, 20, 20) for h in frame.hotspots))
                self.assertEqual([v["key"] for v in frame.scroll_viewports], calls)
                self.assertEqual(host.hotspots, [])
                calls.clear()
                direct = host.build_gpu_clickgui(1280, 720, 1.0)
                self.assertEqual(frame.commands, direct)
                self.assertEqual(list(frame.hotspots), host.hotspots)
                self.assertEqual(list(frame.scroll_viewports), host.gui_scroll_viewports)


class OverlayIntegrationTests(unittest.TestCase):
    """Production paint orchestration, real Qt raster device, inert GL calls."""

    def setUp(self):
        self.host = Host()
        self.host.economy_island = NS(progress=0.0)
        self.host.devicePixelRatioF = lambda: 1.0
        self.functions = object()
        self.host.context = lambda: NS(extraFunctions=lambda: self.functions)
        self.host.setting_mode = lambda module, name, default: (
            str(module.get_setting(name).value) if module and module.get_setting(name) else default
        )
        self.host.clickgui_gpu = NS(available=True, prepare_frame=Mock(), render=Mock(return_value=True))
        self.host.gpu_compositor = NS(_restore_qt_texture_state=Mock())
        self.host.build_gpu_clickgui = Mock(wraps=self.host.build_gpu_clickgui)

    def paint(self, **kwargs):
        image = QImage(640, 400, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#16374a"))
        painter = QPainter(image)
        try:
            PolarRMWindow.paint_clickgui_overlay(self.host, painter, 640, 400, **kwargs)
        finally:
            painter.end()
        return image

    def test_prepared_frame_uses_one_layout_and_prepared_atlas(self):
        frame = build_clickgui_frame(self.host, 640, 400, 1.0)
        self.host.hotspots.append(Hotspot(QRectF(0, 0, 10, 10), "old_map"))
        self.host.gui_scroll_viewports.append({"key": "old_hud"})
        self.paint(gpu_frame=frame, gpu_atlas_prepared=True)
        self.host.build_gpu_clickgui.assert_called_once_with(640, 400, 1.0)
        self.host.clickgui_gpu.prepare_frame.assert_not_called()
        self.host.clickgui_gpu.render.assert_called_once_with(
            self.functions, frame.commands, 640, 400, 1.0, atlas_prepared=True,
        )
        self.assertEqual(self.host.hotspots, list(frame.hotspots))
        self.assertEqual(self.host.gui_scroll_viewports, list(frame.scroll_viewports))

    def test_direct_call_builds_prepares_and_publishes_once(self):
        self.paint()
        self.host.build_gpu_clickgui.assert_called_once_with(640, 400, 1.0)
        self.host.clickgui_gpu.prepare_frame.assert_called_once()
        self.host.clickgui_gpu.render.assert_called_once()
        self.assertTrue(self.host.hotspots)
        self.assertTrue(self.host.gui_scroll_viewports)

    def test_retry_uses_same_commands_and_does_not_rebuild_layout(self):
        attempts = []

        def render(*args, **kwargs):
            self.assertEqual(self.host.hotspots, [])
            self.assertEqual(self.host.gui_scroll_viewports, [])
            attempts.append(args[1])
            return len(attempts) == 2

        self.host.clickgui_gpu.render.side_effect = render
        self.paint()
        self.host.build_gpu_clickgui.assert_called_once()
        self.assertEqual(self.host.clickgui_gpu.prepare_frame.call_count, 2)
        first, second = self.host.clickgui_gpu.render.call_args_list
        self.assertIs(first.args[1], second.args[1])
        self.assertEqual(self.host.registry.get("ClickGui").get_setting("Render Backend").value, "Unified GPU")
        self.assertTrue(self.host.hotspots)

    def test_prepared_frame_painted_during_close_remains_display_only(self):
        frame = build_clickgui_frame(self.host, 640, 400, 1.0)
        self.host.gui_visible = False
        self.host.gui_timeline.progress = 0.5
        self.paint(gpu_frame=frame, gpu_atlas_prepared=True)
        self.host.clickgui_gpu.render.assert_called_once()
        self.assertEqual(self.host.hotspots, [])
        self.assertEqual(self.host.gui_scroll_viewports, [])

    def test_failed_gpu_fallback_has_single_compatibility_geometry_and_same_pixels(self):
        expected_host = Host()
        expected = BuilderCompatibilityTests.paint_jello(expected_host, (640, 400))
        self.host.clickgui_gpu.render.side_effect = [False, False]
        actual = self.paint()
        self.assertEqual(actual, expected)
        self.assertEqual(self.host.hotspots, expected_host.hotspots)
        self.assertEqual(self.host.gui_scroll_viewports, expected_host.gui_scroll_viewports)
        self.assertEqual(self.host.registry.get("ClickGui").get_setting("Render Backend").value, "Unified GPU")

    def test_transient_gpu_failure_recovers_next_frame_without_changing_preference(self):
        setting = self.host.registry.get("ClickGui").get_setting("Render Backend")
        self.host.clickgui_gpu.render.side_effect = [False, False, True]
        self.paint()
        self.assertEqual(setting.value, "Unified GPU")
        frame = build_clickgui_frame(self.host, 640, 400, 1.0)
        self.paint(gpu_frame=frame, gpu_atlas_prepared=True)
        self.assertEqual(self.host.clickgui_gpu.render.call_count, 3)
        self.assertEqual(setting.value, "Unified GPU")
        self.assertEqual(self.host.hotspots, list(frame.hotspots))
        self.assertEqual(self.host.gui_scroll_viewports, list(frame.scroll_viewports))

    def test_previous_drawlist_and_hotspot_kwargs_remain_supported(self):
        commands = self.host.build_gpu_clickgui(640, 400, 1.0)
        hotspots = list(self.host.hotspots)
        viewports = list(self.host.gui_scroll_viewports)
        self.paint(gpu_commands=commands, gpu_hotspots=hotspots, gpu_atlas_prepared=True)
        self.host.build_gpu_clickgui.assert_called_once()
        self.assertEqual(self.host.hotspots, hotspots)
        self.assertEqual(self.host.gui_scroll_viewports, viewports)
        self.host.clickgui_gpu.prepare_frame.assert_not_called()


class CachedLayerRecoveryTests(unittest.TestCase):
    def test_failed_cache_build_restores_painter_and_preserves_preference_for_retry(self):
        host = Host()
        host.gui_cache = ClickGuiLayerCache()
        host.devicePixelRatioF = lambda: 1.0
        host.setting_mode = lambda module, name, default: module.get_setting(name).value
        setting = host.registry.get("ClickGui").get_setting("Render Backend")
        rect = QRectF(12, 18, 100, 70)
        layers = []
        calls = []
        def build(painter, bounds):
            calls.append(bounds)
            if len(calls) == 1:
                layers.append(painter)
                raise TypeError("bad cache geometry")
            painter.fillRect(bounds, QColor("#336699"))
        image = QImage(160, 120, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        try:
            with self.assertLogs("rm_client.ui.polar_app", level="ERROR"):
                PolarRMWindow.draw_gui_cached_layer(host, painter, rect, ("key",), build)
            self.assertEqual(setting.value, "Unified GPU")
            self.assertFalse(layers[0].isActive())
            self.assertTrue(painter.transform().isIdentity())
            self.assertEqual(len(calls), 2)
            first = image.copy()
            image.fill(0)
            PolarRMWindow.draw_gui_cached_layer(host, painter, rect, ("key",), build)
            self.assertEqual(len(calls), 3)
            self.assertEqual(image, first)
            PolarRMWindow.draw_gui_cached_layer(host, painter, rect, ("key",), build)
            self.assertEqual(len(calls), 3, "successful retry should be cached")
        finally:
            painter.end()


class RendererInitializationContractTests(unittest.TestCase):
    def test_context_failure_then_reinitialization_preserves_saved_renderer_choice(self):
        registry = build_default_registry()
        setting = registry.get("ClickGui").get_setting("Render Backend")
        for preference in ("Unified GPU", "Compatibility Painter"):
            with self.subTest(preference=preference):
                setting.value = preference
                host = NS(registry=registry, context=lambda: None)
                for name in ("gpu_compositor", "vision_wire_gpu", "island_icon_gpu", "quick_action_wheel_gpu", "startup_gpu", "hud_video_gpu"):
                    setattr(host, name, NS(initialize=Mock(return_value=True)))
                host.clickgui_gpu = NS(initialize=Mock(side_effect=[False, True]))
                PolarRMWindow.initializeGL(host)
                self.assertEqual(setting.value, preference)
                PolarRMWindow.initializeGL(host)
                self.assertEqual(setting.value, preference)
                self.assertEqual(host.clickgui_gpu.initialize.call_count, 2)


class FullWindowLegacyParityTests(unittest.TestCase):
    def test_all_real_legacy_builders_match_compatibility_api(self):
        window = PolarRMWindow()
        settle_window_lifecycle(window, APP)
        # Quiesce timers and avoid writing the test's UI state into config.
        for timer in window.findChildren(QTimer):
            timer.stop()
        window.flush_auto_save = Mock()
        window.resize(1280, 720)
        window.home_visible = False
        window.gui_visible = True
        window.polar_unified_morph.progress = 1.0
        window.polar_unified_morph.target = 1.0
        window.module_expanded.add(window.selected_module.name)
        try:
            # Both paths must describe the same instant, including rainbow
            # colors and animated rows, rather than comparing adjacent frames.
            with patch("time.perf_counter", return_value=123456.0):
                for mode in LEGACY_GUI_MODES:
                    with self.subTest(mode=mode):
                        window.jello_state.open_legacy(mode)
                        window.gui_motion.begin_frame()
                        window.hotspots = []
                        window.gui_scroll_viewports = []
                        window._hotspot_clip = None
                        direct = window.build_gpu_clickgui(1280, 720, 1.0)
                        hotspots = list(window.hotspots)
                        viewports = list(window.gui_scroll_viewports)
                        frame = build_clickgui_frame(window, 1280, 720, 1.0)
                        self.assertEqual(frame.commands, direct)
                        self.assertEqual(list(frame.hotspots), hotspots)
                        self.assertEqual(list(frame.scroll_viewports), viewports)
                        self.assertGreater(len(frame.commands.commands), 20)
                        self.assertGreater(len(frame.hotspots), 1)
        finally:
            close_window_safely(window, APP)
            window.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
