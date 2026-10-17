"""Value-only hit geometry: no window, backend, registry or QApplication."""
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath, QTransform
from rm_client.ui.tactical_map_geometry import NormalizedMapPoint
from rm_client.ui.tactical_map_interaction import MapHitGeometry, map_hit_position


class MapHitGeometryTests(unittest.TestCase):
    def test_transforms_roundtrip_without_device_pixel_ratio(self):
        rect = QRectF(17., 91., 448., 240.)
        rng = random.Random(20261016)
        for angle in (0., 17., 90., 180.):
            transform = QTransform().translate(13.5, 7.25).rotate(angle).scale(.82, 1.1).shear(.1, -.07)
            hit = MapHitGeometry.from_paint(rect, rect, transform, None, rounded=False)
            for _ in range(100):
                nx, ny = rng.uniform(.02, .98), rng.uniform(.02, .98)
                screen = transform.map(QPointF(rect.left() + nx * rect.width(), rect.bottom() - ny * rect.height()))
                actual = hit.normalized_point(screen)
                self.assertIsNotNone(actual)
                self.assertAlmostEqual(actual.x, nx, places=12)
                self.assertAlmostEqual(actual.y, ny, places=12)

    def test_inputs_are_detached_and_frozen(self):
        rect, panel = QRectF(10., 20., 200., 100.), QRectF(10., 20., 200., 100.)
        transform = QTransform().translate(5., 7.)
        clip = QPainterPath(); clip.addRect(rect)
        hit = MapHitGeometry.from_paint(rect, panel, transform, clip, rounded=False)
        rect.translate(1000, 1000); panel.translate(1000, 1000); transform.scale(0., 0.); clip.clear()
        self.assertEqual(hit.normalized_point(QPointF(115., 77.)), NormalizedMapPoint(.5, .5))
        with self.assertRaises(AttributeError):
            hit._invertible = False

    def test_clip_and_rounded_silhouette_reject_invisible_corners(self):
        rect = QRectF(10., 20., 200., 100.)
        rounded = MapHitGeometry.from_paint(rect, rect, QTransform(), None, rounded=True)
        square = MapHitGeometry.from_paint(rect, rect, QTransform(), None, rounded=False)
        corner = rect.topLeft() + QPointF(.1, .1)
        self.assertIsNone(rounded.normalized_point(corner))
        self.assertIsNotNone(square.normalized_point(corner))
        self.assertIsNotNone(rounded.normalized_point(QPointF(rect.left() + 6., rect.top() + .1)))
        clip = QPainterPath(); clip.addRect(QRectF(10., 20., 100., 100.))
        clipped = MapHitGeometry.from_paint(rect, rect, QTransform(), clip, rounded=False)
        self.assertIsNotNone(clipped.normalized_point(QPointF(50., 70.)))
        self.assertIsNone(clipped.normalized_point(QPointF(150., 70.)))

    def test_empty_null_and_singular_geometry_never_hit(self):
        rect = QRectF(0., 0., 200., 100.)
        for hit in (MapHitGeometry.empty(),
                    MapHitGeometry.from_paint(QRectF(), rect, QTransform(), None, rounded=False),
                    MapHitGeometry.from_paint(rect, rect, QTransform().scale(0., 0.), None, rounded=False)):
            self.assertIsNone(hit.normalized_point(QPointF(0., 0.)))
            self.assertIsNone(hit.normalized_point(QPointF(100., 50.)))

    def test_no_snapshot_preserves_public_rectangle_direct_call_contract(self):
        rect = QRectF(10., 20., 200., 100.)
        self.assertEqual(map_hit_position(QPointF(10., 120.), rect, None), NormalizedMapPoint(0., 0.))
        self.assertEqual(map_hit_position(QPointF(210., 20.), rect, None), NormalizedMapPoint(1., 1.))
        self.assertIsNone(map_hit_position(QPointF(9.99, 20.), rect, None))
        self.assertIsNone(map_hit_position(QPointF(110., 70.), rect, MapHitGeometry.empty()))
        # Tiny public rectangles retain the historical max(1, extent) inverse.
        self.assertEqual(map_hit_position(QPointF(.5, .25), QRectF(0., 0., .5, .5), None),
                         NormalizedMapPoint(.5, .25))


if __name__ == '__main__':
    unittest.main()
