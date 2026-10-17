"""Pre-extraction tactical map behavior: real window, raster, input and failures.

Frozen reference is the exact map source at 71ebb0f. No fixture regeneration,
real device/network access, aggregate verifier lock or native GPU claims.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ['CI'] = 'true'
os.environ['POLAR_DISABLE_LOCAL_CAPTURE'] = '1'
os.environ['POLAR_DISABLE_MEDIA_PRELOAD'] = '1'
os.environ['POLAR_AUTOSTART'] = '0'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QImage, QKeyEvent, QPainter
from PySide6.QtWidgets import QApplication
from rm_client.match_modes import FieldLayout
from rm_client.ui.animation import MapIslandState
from rm_client.ui.polar_app import PolarRMWindow
from rm_client.ui.polar.renderers import tactical_map as current
from tactical_map_contract_fixture import (
    FROZEN, REFERENCE, REFERENCE_SHA256, RasterTrace, implementation,
    patterned_map, pixels, render, scene, set_setting, value,
)
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])


class TacticalMapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='map-contract-')
        cls.environment = patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': cls.directory.name})
        cls.environment.start()
        cls.window = PolarRMWindow()
        cls.window.timer.stop()
        settle_window_lifecycle(cls.window, APP)
        for timer in cls.window.findChildren(QTimer):
            timer.stop()
        cls.window.ui_audio_enabled = False
        cls.window.registry.get('AudioFeedback').enabled = False

    @classmethod
    def tearDownClass(cls):
        close_window_safely(cls.window, APP)
        cls.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cls.environment.stop()
        cls.directory.cleanup()

    def assert_render_contract(self, actual, expected, *, liquid):
        # The separately authorized input fix publishes liquid geometry where
        # 71ebb0f left constructor/stale values. Keep that old observation and
        # assert the new values against the real reference draw's return value;
        # every command, pixel, hotspot and failure prefix remains exact.
        if liquid and expected['painted_field'] is not None:
            self.assertEqual({key: expected[key] for key in ('field_rect', 'field_key', 'mirrored')},
                             {'field_rect': value(QRectF(7, 9, 101, 103)), 'field_key': None, 'mirrored': False})
            expected = dict(expected, **expected['painted_field'])
        self.assertEqual(actual, expected)

    def test_reference_is_the_exact_fixed_source(self):
        self.assertEqual(hashlib.sha256(REFERENCE.read_bytes()).hexdigest(), REFERENCE_SHA256)

    def test_real_window_complete_scene_raster_commands_and_hits_match(self):
        cases = []
        for liquid in (False, True):
            for mode, side in (('RMUC7v7', 'Red'), ('RMUL3v3', 'Blue')):
                for hold, progress, density in ((False, 0., 'CombatCompact'), (True, .37, 'CombatCompact'),
                                               (True, 1., 'Balanced'), (False, .62, 'DebugFull')):
                    cases.append(dict(liquid=liquid, mode=mode, side=side, hold=hold, progress=progress, density=density))
        for status in ('LiquidAuto', 'LiquidCards', 'LegacyBars', 'LegacyOrbit'):
            cases.append(dict(liquid=True, hold=True, progress=.75, status=status))
            cases.append(dict(liquid=False, hold=True, progress=.75, status=status))
        for index, options in enumerate(cases):
            geometry = dict(width=960 if index % 2 else 1280, height=640 if index % 2 else 720,
                            dpr=(1., 1.25, 2.)[index % 3], scale=(1., .82, 1.10)[index % 3])
            with self.subTest(options=options, geometry=geometry):
                expected = render(self.window, FROZEN, **geometry, **options)
                self.assertIsNone(expected['error'], expected['error'])
                self.assertTrue(expected['commands'])
                self.assertTrue(expected['hotspots'])
                actual = render(self.window, current, **geometry, **options)
                self.assert_render_contract(actual, expected, liquid=options['liquid'])

    def test_frozen_reference_retains_its_own_policy_constants(self):
        results = []
        with patch.object(current.TacticalMapMixin, 'RADAR_MAP_FULL_EXPANSION', 2.):
            for module in (FROZEN, current):
                with scene(self.window, module):
                    set_setting(self.window.registry, 'RadarOverlay', 'Map', 'Full')
                    results.append(self.window.radar_overlay_map_visible(1.))
        self.assertEqual(results, [True, False])

    def test_missing_map_asset_preserves_fallback_raster_and_field_geometry(self):
        for liquid in (False, True):
            options = dict(liquid=liquid, missing=True, hold=True, progress=1.)
            expected = render(self.window, FROZEN, **options)
            self.assertIsNone(expected['error'])
            self.assert_render_contract(render(self.window, current, **options), expected, liquid=liquid)

    def test_partial_render_failures_preserve_effect_prefix_and_original_error(self):
        for liquid in (False, True):
            options = dict(liquid=liquid, hold=True, progress=.75)
            complete = render(self.window, FROZEN, **options)
            self.assertIsNone(complete['error'])
            count = len(complete['commands'])
            points = sorted({1, 2, 3, 7, 20, count // 3, count // 2, count - 1, count})
            for fail_at in points:
                with self.subTest(liquid=liquid, fail_at=fail_at):
                    expected = render(self.window, FROZEN, fail_at=fail_at, **options)
                    self.assertIsNotNone(expected['error'])
                    self.assert_render_contract(render(self.window, current, fail_at=fail_at, **options), expected, liquid=liquid)

    def test_tab_press_autorepeat_reverse_and_resize_keep_continuous_state(self):
        def sequence(module):
            with scene(self.window, module):
                output = []
                for width, height, pressed, repeat, dt in (
                    (1280, 720, False, False, 0.), (1280, 720, True, False, .016),
                    (1280, 720, True, True, .030), (800, 600, False, False, .012),
                    (1920, 1080, True, False, .020), (1920, 1080, False, True, .04),
                    (640, 360, False, False, 1.),
                ):
                    old = value(self.window.tactical_map_state)
                    event = QKeyEvent(QEvent.Type.KeyPress if pressed else QEvent.Type.KeyRelease,
                                      Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier, '', repeat)
                    consumed = self.window.handle_tactical_map_hold_event(event, pressed)
                    rect = self.window.tactical_map_rect(width, height)
                    before_update = value(self.window.tactical_map_state)
                    self.window.tactical_map_state.update(dt)
                    output.append((consumed, self.window.tactical_map_hold, value(rect), old, before_update,
                                   value(self.window.tactical_map_state), self.window.tactical_map_morph_progress()))
                return output
        self.assertEqual(sequence(current), sequence(FROZEN))
        result = sequence(current)
        self.assertTrue(all(row[0] for row in result))
        self.assertEqual([row[1] for row in result], [False, True, True, False, True, True, False])
        self.assertGreater(result[2][-1], result[1][-1])
        self.assertLess(result[3][-1], result[2][-1])

    def test_map_size_minecraft_and_reentrant_policy_queries_match(self):
        for size in ('Compact', 'Wide', 'Full Tactical', 'unknown'):
            for density in ('CombatCompact', 'Balanced', 'DebugFull'):
                for flags in ((False, False), (True, True), (True, False), (False, True)):
                    outputs = []
                    for module in (FROZEN, current):
                        with scene(self.window, module, density=density, map_size=size), \
                             patch.object(self.window, 'minecraft_hud_active', side_effect=flags) as query:
                            result = self.window.tactical_map_rect(300, 180)
                            outputs.append((value(result), value(self.window.tactical_map_state), query.call_count))
                    with self.subTest(size=size, density=density, flags=flags):
                        self.assertEqual(outputs[0], outputs[1])
                        self.assertEqual(outputs[0][-1], 2)

    def test_projection_mirror_clamp_and_click_roundtrip_preserve_backend_boundary(self):
        rng = random.Random(20261016)
        for side in ('Red', 'Blue'):
            for mirrored in (False, True):
                for width, height in ((280., 150.), (.5, .25), (0., 0.), (-10., 20.)):
                    for index in range(12):
                        x, y = rng.uniform(-2., 30.), rng.uniform(-2., 17.)
                        results = []
                        for module in (FROZEN, current):
                            with scene(self.window, module, side=side, hold=True):
                                field = FieldLayout('test', 'Test', '', 28., 15., mirrored)
                                rect = QRectF(30., 50., width, height)
                                point = self.window.map_point(rect, field, x, y)
                                self.window.tactical_map_field_layout = field
                                self.window.tactical_map_field_rect = rect
                                self.window.tactical_map_field_mirrored = mirrored and side == 'Blue'
                                with patch.object(self.window.backend, 'publish_map_click_cmd', return_value=(False, 'blocked')) as send, \
                                     patch.object(self.window, 'toast') as toast:
                                    consumed = self.window.publish_map_click_from_screen(point)
                                results.append((value(point), consumed, send.call_args_list, toast.call_args_list))
                        with self.subTest(side=side, mirrored=mirrored, size=(width, height), index=index):
                            self.assertEqual(results[0], results[1])

    def test_click_gates_and_exceptions_do_not_publish_or_toast_early(self):
        for failure in ('hold', 'field', 'null', 'outside', 'settings', 'backend', 'toast', 'ok'):
            results = []
            for module in (FROZEN, current):
                with scene(self.window, module, hold=failure != 'hold'):
                    self.window.tactical_map_field_layout = None if failure == 'field' else self.window.match_mode_manager.current.field
                    self.window.tactical_map_field_rect = QRectF() if failure == 'null' else QRectF(10, 20, 280, 150)
                    calls = []
                    def send(**kwargs):
                        calls.append(('send', kwargs))
                        if failure == 'backend': raise ValueError('backend defect')
                        return True, ''
                    def toast(text):
                        calls.append(('toast', text))
                        if failure == 'toast': raise ValueError('toast defect')
                    with patch.object(self.window.backend, 'publish_map_click_cmd', side_effect=send), \
                         patch.object(self.window, 'toast', side_effect=toast), ExitStack() as stack:
                        if failure == 'settings':
                            stack.enter_context(patch.object(self.window, 'map_click_command_values', side_effect=ValueError('settings defect')))
                        try:
                            result = self.window.publish_map_click_from_screen(QPointF(500, 500) if failure == 'outside' else QPointF(150, 95))
                        except Exception as exc:
                            result = (type(exc).__name__, str(exc))
                    results.append((result, calls))
            with self.subTest(failure=failure):
                self.assertEqual(results[0], results[1])
                if failure in ('hold', 'field', 'null', 'outside'):
                    self.assertEqual(results[0], (False, []))

    def test_radar_stale_units_malformed_and_highlight_settings_match_raster(self):
        cases = [(age, detections) for age in (-1., 0., 4., 4.001, 5.999, 6., 6.001, 100.)
                 for detections in ([{'target_pos_x': 1400, 'target_pos_y': 750, 'is_high_light': 1}],
                                    [{'target_pos_x': 14, 'target_pos_y': 7.5}, {'target_pos_x': 'bad'}],
                                    [{'target_pos_x': 0, 'target_pos_y': 0}], [])]
        cases.extend([(1., [None]), (1., [{'target_pos_x': float('inf'), 'is_high_light': float('inf')}]),
                      (1., [{'target_pos_x': 1400, 'target_pos_y': 750}, None]),
                      (1., [{'target_pos_x': 1400, 'target_pos_y': 750}, {'is_high_light': float('inf')}])])
        for age, detections in cases:
            results = []
            for module in (FROZEN, current):
                with scene(self.window, module):
                    self.window.state.radar_detections = deepcopy(detections)
                    self.window.state.radar_detections_at = 100. - age
                    image = QImage(350, 220, QImage.Format.Format_ARGB32_Premultiplied); image.fill(0)
                    painter = RasterTrace(image)
                    try:
                        result = module.draw_radar_overlay(self.window, painter, QRectF(10, 10, 280, 150), self.window.match_mode_manager.current.field)
                    except Exception as exc:
                        result = (type(exc).__name__, str(exc))
                    observations = (result, painter.calls, value(self.window.hotspots), painter.depth)
                    painter.finish()
                    results.append((observations, pixels(image)))
            with self.subTest(age=age, detections=detections):
                self.assertEqual(results[0], results[1])
                if age > 6. or not detections:
                    self.assertEqual(results[0][0][0], 0)
                    self.assertEqual(results[0][0][1], [])

    def test_buff_and_penalty_malformed_order_and_limits_match(self):
        cases = (
            ({'penalty_type': 2}, [{'penalty_type': 1}, {'penalty_type': 2}, {'penalty_type': 3}, {'penalty_type': 4}]),
            ({'penalty_type': 'bad'}, [{'penalty_type': 1, 'total_penalty_num': 'bad'}]),
            ({}, [None]), ({'penalty_type': float('inf')}, []),
        )
        for active, penalties in cases:
            results = []
            for module in (FROZEN, current):
                with scene(self.window, module):
                    self.window.state.active_penalty, self.window.state.penalties = deepcopy(active), deepcopy(penalties)
                    image = QImage(350, 220, QImage.Format.Format_ARGB32_Premultiplied); image.fill(0)
                    painter = RasterTrace(image)
                    try:
                        result = module.draw_penalty_strip(self.window, painter, QRectF(10, 10, 280, 150))
                    except Exception as exc: result = (type(exc).__name__, str(exc))
                    observations = (result, painter.calls, value(self.window.hotspots), painter.depth)
                    painter.finish(); results.append((observations, pixels(image)))
            self.assertEqual(results[0], results[1])
        for malformed in (None, 'bad', float('inf'), 7):
            results = []
            for module in (FROZEN, current):
                with scene(self.window, module):
                    self.window.registry.get('BuffTimeline').enabled = True
                    self.window.state.buff_rows += [
                        {'robot_id': 3, 'buff_type': 1, 'active': True, 'remaining': malformed},
                        {'robot_id': 4, 'buff_type': 2, 'active': True},
                        {'robot_id': 3, 'active': True},
                        {'robot_id': 3, 'buff_type': 3, 'active': False},
                    ]
                    self.window.state.special_mechanisms += [None, {'mechanism_id': 0}, {'mechanism_id': 4, 'time_sec': 99}]
                    try: result = self.window.buff_timeline_rows()
                    except Exception as exc: result = (type(exc).__name__, str(exc))
                    results.append(result)
            self.assertEqual(results[0], results[1])

    def test_rounded_map_transform_dpr_corners_cache_identity_and_allocation_failure(self):
        for dpr, scale in ((1., 1.), (1.25, .82), (2., 1.1), (3., 2.), (1., -1.), (1., 0.)):
            for fit in (QRectF(10, 10, 112, 60), QRectF(15, 13, 103, 52), QRectF(20, 20, 92, 40)):
                results = []
                for module in (FROZEN, current):
                    with scene(self.window, module):
                        image = QImage(400, 300, QImage.Format.Format_ARGB32_Premultiplied)
                        image.setDevicePixelRatio(dpr); image.fill(0)
                        painter = QPainter(image); painter.scale(scale, scale)
                        source = patterned_map(); panel = QRectF(10, 10, 112, 60)
                        cuts = self.window._map_fit_cuts_panel_corners(panel, fit, 6.)
                        first = self.window._rounded_map_layer(painter, source, fit, panel, 6.)
                        second = self.window._rounded_map_layer(painter, source, fit, panel, 6.)
                        results.append((cuts, value(first), first is second,
                                        tuple((key[1:], value(item)) for key, item in self.window._map_round_cache.items())))
                        painter.end()
                self.assertEqual(results[0], results[1])
                self.assertTrue(results[0][2])
        for module in (FROZEN, current):
            with scene(self.window, module):
                image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
                painter = QPainter(image)
                try:
                    with patch.object(module, 'QImage', side_effect=MemoryError('allocation')):
                        self.assertIsNone(self.window._rounded_map_layer(painter, patterned_map(), QRectF(0, 0, 20, 20), QRectF(0, 0, 20, 20), 6.))
                    self.assertEqual(self.window._map_round_cache, {})
                finally:
                    painter.end()


    def test_rounded_cache_eviction_source_replacement_and_device_failure_match(self):
        results = []
        for module in (FROZEN, current):
            with scene(self.window, module):
                image = QImage(300, 200, QImage.Format.Format_ARGB32_Premultiplied)
                painter = QPainter(image)
                source = patterned_map()
                observations = []
                try:
                    for index in range(11):
                        fit = QRectF(10, 10, 100 + index, 60)
                        layer = self.window._rounded_map_layer(painter, source, fit, QRectF(10, 10, 112, 60), 6)
                        observations.append((value(layer), len(self.window._map_round_cache)))
                    source.fill(Qt.GlobalColor.red)
                    replaced = self.window._rounded_map_layer(painter, source, fit, QRectF(10, 10, 112, 60), 6)
                    observations.append((value(replaced), len(self.window._map_round_cache)))
                    self.assertNotEqual(observations[-1][0], observations[-2][0])
                finally:
                    painter.end()
                # Only the device query fails; the production fallback still allocates/renders.
                proxy = NS(transform=lambda: NS(m11=lambda: 1.25),
                           device=lambda: NS(devicePixelRatioF=Mock(side_effect=ValueError('DPR missing'))))
                fallback = self.window._rounded_map_layer(proxy, source, QRectF(1, 1, 20, 10), QRectF(1, 1, 20, 10), 6)
                observations.append(value(fallback))
                results.append(observations)
        self.assertEqual(results[0], results[1])
        self.assertEqual([entry[1] for entry in results[0][:11]], [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3])

    def test_radar_setting_callback_failures_remain_per_marker_and_lazy(self):
        for failure in ('none', 'ValueError', 'RuntimeError'):
            results = []
            for module in (FROZEN, current):
                with scene(self.window, module):
                    self.window.state.radar_detections = [
                        {'target_pos_x': 3, 'target_pos_y': 4, 'is_high_light': 0},
                        {'target_pos_x': 6, 'target_pos_y': 7, 'is_high_light': 1},
                        {'target_pos_x': 8, 'target_pos_y': 9, 'is_high_light': 0},
                    ]
                    image = QImage(350, 220, QImage.Format.Format_ARGB32_Premultiplied); image.fill(0)
                    painter = RasterTrace(image)
                    calls = []
                    def highlight():
                        calls.append('highlight')
                        if failure == 'ValueError': raise ValueError('invalid setting')
                        if failure == 'RuntimeError': raise RuntimeError('setting failed')
                        return False
                    with patch.object(self.window, 'radar_high_light_enabled', side_effect=highlight):
                        try:
                            result = module.draw_radar_overlay(self.window, painter, QRectF(10, 10, 280, 150), self.window.match_mode_manager.current.field)
                        except Exception as exc: result = (type(exc).__name__, str(exc))
                    observed = (result, painter.calls, value(self.window.hotspots), calls, painter.depth)
                    painter.finish()
                    results.append((observed, pixels(image)))
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[0][0][3], ['highlight'])
            expected_hotspots = 3 if failure == 'none' else 2 if failure == 'ValueError' else 1
            self.assertEqual(len(results[0][0][2]), expected_hotspots)


if __name__ == '__main__':
    unittest.main()
