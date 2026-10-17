"""Pure map math and value ownership; no Qt application or runtime imports."""
from dataclasses import FrozenInstanceError
import math
from pathlib import Path
import random
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.ui.tactical_map_geometry import (
    MapExtent, MapViewport, NormalizedMapPoint, RoundedMapSampling,
    fit_cuts_panel_corners, normalized_field_coordinate,
)


class MapGeometryTests(unittest.TestCase):
    def test_normal_import_is_qt_model_backend_and_renderer_independent(self):
        script = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('PySide6', 'rm_client.model', 'rm_client.backend', 'rm_client.ui.polar', 'rm_client.vision')):
            raise AssertionError('forbidden dependency: ' + fullname)
sys.meta_path.insert(0, Block())
from rm_client.ui.tactical_map_geometry import MapExtent, MapViewport, NormalizedMapPoint, RoundedMapSampling
assert MapExtent.collapsed(1280, 720, 'Wide', 'CombatCompact', False).width == 170
assert MapViewport(10, 170, 280, 150).screen_point(NormalizedMapPoint(.5, .5)) == (150, 95)
assert RoundedMapSampling.for_extent(20, 10, 1.25, 6).width == 25
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_collapsed_targets_obey_density_and_minecraft_without_changing_mode(self):
        for mode, expected in (('Compact', (220., 170.)), ('Wide', (280., 212.)),
                               ('Full Tactical', (360., 270.)), ('unknown', (280., 212.))):
            self.assertEqual(MapExtent.collapsed(1280, 720, mode, 'Balanced', False), MapExtent(*expected))
            self.assertEqual(MapExtent.collapsed(1280, 720, mode, 'CombatCompact', False), MapExtent(170., 118.))
            self.assertEqual(MapExtent.collapsed(1280, 720, mode, 'CombatCompact', True), MapExtent(216., 136.8))
        self.assertEqual(MapExtent.collapsed(100, 100, 'Wide', 'Balanced', True), MapExtent(168., 116.))
        self.assertEqual(MapExtent.collapsed(3000, 2000, 'Wide', 'Balanced', True), MapExtent(216., 154.))

    def test_target_clamps_and_expansion_do_not_mutate_collapsed_extent(self):
        base = MapExtent(280., 212.)
        self.assertEqual(base.target(1280, 720, True), MapExtent(520., 380.))
        self.assertEqual(base.target(800, 600, True), MapExtent(384., 336.00000000000006))
        self.assertEqual(base.target(100, 100, True), MapExtent(48., 56.00000000000001))
        self.assertEqual(base.target(100, 100, False), MapExtent(190., 150.))
        self.assertEqual(base, MapExtent(280., 212.))
        with self.assertRaises(FrozenInstanceError): base.width = 5

    def test_projection_inverse_mirroring_and_clamping_across_random_viewports(self):
        rng = random.Random(20261018)
        for _ in range(2000):
            width_m, height_m = rng.uniform(.1, 50), rng.uniform(.1, 50)
            x, y = rng.uniform(-1, 2), rng.uniform(-1, 2)
            point = NormalizedMapPoint(normalized_field_coordinate(x * width_m, width_m),
                                       normalized_field_coordinate(y * height_m, height_m))
            viewport = MapViewport(rng.uniform(-50, 50), rng.uniform(50, 400), rng.uniform(1, 900), rng.uniform(1, 600))
            mirrored = bool(rng.randrange(2))
            screen = viewport.screen_point(point.mirrored() if mirrored else point)
            inverse = viewport.normalized_point(*screen)
            inverse = inverse.mirrored() if mirrored else inverse
            fx, fy = inverse.field_metres(width_m, height_m)
            self.assertAlmostEqual(fx, max(0, min(1, x)) * width_m)
            self.assertAlmostEqual(fy, max(0, min(1, y)) * height_m)

    def test_subpixel_zero_negative_and_nan_behavior_is_explicit(self):
        self.assertEqual(normalized_field_coordinate(.05, 0), .5)
        self.assertEqual(normalized_field_coordinate(float('nan'), 28), 1.)
        self.assertEqual(normalized_field_coordinate(float('-inf'), 28), 0.)
        self.assertEqual(normalized_field_coordinate(float('inf'), 28), 1.)
        viewport = MapViewport(10, 20, .5, .25)
        point = viewport.normalized_point(10.5, 19.75)
        self.assertEqual(point, NormalizedMapPoint(.5, .25))
        self.assertEqual(point.field_metres(28, 15), (14, 3.75))
        self.assertEqual(MapViewport(10, 20, -3, 0).normalized_point(7, 20), NormalizedMapPoint(-3, 0))

    def test_corner_mask_threshold_is_strict_with_antialias_margin(self):
        for gx in (-1., 0., 6.99, 7., 7.01, 20.):
            for gy in (-1., 0., 6.99, 7., 7.01, 20.):
                self.assertEqual(fit_cuts_panel_corners((gx, 100), (gy, 100), 6), gx < 7 and gy < 7)
        self.assertTrue(fit_cuts_panel_corners((100, 0), (100, 0), 6))
        self.assertFalse(fit_cuts_panel_corners((0, 0), (7, 7), 6))

    def test_sampling_clamps_scale_keeps_anisotropic_dimensions_and_offset_keys(self):
        for scale, expected in ((-1, .5), (0, .5), (.5, .5), (1.25, 1.25), (4, 4), (20, 4)):
            sampling = RoundedMapSampling.for_extent(11.3, 7.7, scale, 6)
            width, height = math.ceil(11.3 * expected), math.ceil(7.7 * expected)
            self.assertEqual((sampling.width, sampling.height), (width, height))
            self.assertEqual((sampling.scale_x, sampling.scale_y), (width / 11.3, height / 7.7))
            offset = sampling.panel_offset(.37, -.25)
            self.assertEqual(offset, (round(.37 * width / 11.3, 2), round(-.25 * height / 7.7, 2)))
            self.assertEqual(sampling.cache_key(123, offset), (123, width, height, round(6 * expected, 2), offset))
            self.assertNotEqual(sampling.cache_key(123, offset), sampling.cache_key(124, offset))
            with self.assertRaises(FrozenInstanceError): sampling.width = 99

    def test_sampling_tiny_empty_and_invalid_extent_keeps_failure_behavior(self):
        sample = RoundedMapSampling.for_extent(0, -1, 1, 6)
        self.assertEqual((sample.width, sample.height, sample.scale_x, sample.scale_y), (1, 1, 1000, 1000))
        with self.assertRaises(OverflowError): RoundedMapSampling.for_extent(float('inf'), 10, 1, 6)
        with self.assertRaises(ValueError): RoundedMapSampling.for_extent(float('nan'), 10, 1, 6)


if __name__ == '__main__':
    unittest.main()
