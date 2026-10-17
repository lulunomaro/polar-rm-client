"""Economy module geometry/math; no QApplication, painter, compositor or GL."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QRectF
from rm_client.ui.economy_layout import (
    EconomyOfferListGeometry, compat_economy_layout,
    economy_detail_scroll_maximum, economy_header_tile, economy_lens_plan, economy_module_geometry,
)
from rm_client.ui.economy_workspace import EconomyWorkspaceLayer


class OfferGeometryTests(unittest.TestCase):
    def test_scroll_extent_preserves_empty_one_and_many_item_boundaries(self):
        offers = EconomyOfferListGeometry(QRectF(20., 50., 300., 144.), 68., 8., 280., 30.)
        self.assertEqual(offers.scroll_maximum(0), 0.)
        self.assertEqual(offers.scroll_maximum(1), 0.)
        self.assertEqual(offers.scroll_maximum(2), 0.)
        self.assertEqual(offers.scroll_maximum(3), 76.)
        self.assertEqual(offers.card(0, 0), QRectF(30., 50., 280., 68.))
        self.assertEqual(offers.card(1.5, 35.), QRectF(30., 129., 280., 68.))

    def test_detail_extent_retains_empty_history_and_expanded_reload(self):
        self.assertEqual(economy_detail_scroll_maximum(viewport_height=500., reload_height=0., history_count=0), 0.)
        self.assertEqual(economy_detail_scroll_maximum(viewport_height=100., reload_height=0., history_count=0), 94.)
        self.assertEqual(economy_detail_scroll_maximum(viewport_height=100., reload_height=38., history_count=3), 260.)
        self.assertEqual(economy_detail_scroll_maximum(viewport_height=100., reload_height=290., history_count=3), 512.)


class CompatibilityLayoutTests(unittest.TestCase):
    def test_top_anchor_and_responsive_split_at_exact_thresholds(self):
        for width, height, compact in ((1280, 720, True), (1479, 900, True), (1480, 859, True), (1480, 860, False), (1920, 1080, False)):
            with self.subTest(size=(width, height)):
                closed = compat_economy_layout(width, height, scale=1., alpha=0., category_count=4)
                opened = compat_economy_layout(width, height, scale=1., alpha=1., category_count=4)
                self.assertEqual(opened.compact, compact)
                self.assertEqual(closed.panel.top(), opened.panel.top())
                self.assertEqual(closed.panel.height(), 44.)
                self.assertEqual(closed.radius, 22.)
                self.assertGreater(opened.panel.height(), closed.panel.height())
                if compact:
                    self.assertEqual(opened.offers_pane.left(), opened.detail_pane.left())
                    self.assertLess(opened.offers_pane.bottom(), opened.detail_pane.top())
                else:
                    self.assertEqual(opened.offers_pane.top(), opened.detail_pane.top())
                    self.assertLess(opened.offers_pane.right(), opened.detail_pane.left())

    def test_scales_and_clamped_alpha_keep_existing_controls_inside_open_sheet(self):
        for scale in (.82, .90, 1., 1.05, 1.10):
            for width, height in ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440)):
                opened = compat_economy_layout(width, height, scale=scale, alpha=2., category_count=4)
                self.assertEqual(opened.morph, 1.)
                self.assertEqual(opened.content_alpha, 1.)
                self.assertTrue(opened.panel.contains(opened.header))
                self.assertTrue(opened.panel.contains(opened.action_button))
                self.assertTrue(opened.panel.contains(opened.offers.viewport))
                closed = compat_economy_layout(width, height, scale=scale, alpha=-1., category_count=0)
                self.assertEqual(closed.morph, 0.)
                self.assertEqual(closed.content_alpha, 0.)


class ModuleGeometryTests(unittest.TestCase):
    def test_geometry_uses_existing_layout_and_does_not_mutate_input(self):
        layer = EconomyWorkspaceLayer()
        for width, height in ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440)):
            for progress in (0., .48, .62, .72, .84, 1., 1.08):
                layout = layer.layout(width, height, progress)
                original_offer = QRectF(layout.offers_pane)
                geometry = economy_module_geometry(layout, category_count=4)
                self.assertEqual(layout.offers_pane, original_offer)
                self.assertEqual(economy_header_tile(layout), QRectF(layout.header.left() - 16., layout.panel.top(),
                                                                     layout.header.width() + 32., min(layout.panel.height(), 68.)))
                self.assertEqual(geometry.offers_pane, layout.offers_pane.translated(0., layout.offers_offset))
                self.assertEqual(geometry.viewport_clip, geometry.offers.viewport.intersected(layout.body_clip))
                self.assertEqual(geometry.detail_clip, geometry.detail_pane.intersected(layout.body_clip))
                self.assertEqual(geometry.action_button, layout.action_button.translated(0., layout.detail_offset))
                self.assertEqual(len(geometry.status_tiles), 3)
                self.assertEqual(len(geometry.tab_rects), 0 if layout.three_column else 4)
                self.assertEqual(geometry.offers.card_height, 68.)
                self.assertEqual(geometry.offers.card_gap, 8.)

    def test_status_and_tabs_keep_centered_gaps_and_source_sizes(self):
        layout = EconomyWorkspaceLayer().layout(1600, 900, 1.)
        geometry = economy_module_geometry(layout, category_count=4)
        self.assertEqual([rect.width() for rect in geometry.status_tiles], [304., 152., 152.])
        self.assertEqual([rect.width() for rect in geometry.tab_rects], [152.] * 4)
        for tiles, container in ((geometry.status_tiles, layout.status_strip), (geometry.tab_rects, layout.tab_bar)):
            self.assertEqual((tiles[0].left() + tiles[-1].right()) / 2., container.center().x())
            for left, right in zip(tiles, tiles[1:]):
                self.assertEqual(right.left() - left.right(), 8.)

    def test_zero_categories_remains_defined_before_renderer_guard(self):
        layout = EconomyWorkspaceLayer().layout(1280, 720, 1.)
        geometry = economy_module_geometry(layout, category_count=0)
        self.assertEqual(len(geometry.tab_rects), 1)
        self.assertEqual(geometry.tab_width, 152.)


class LensPlanTests(unittest.TestCase):
    def test_invisible_empty_or_clipped_away_tiles_do_not_submit(self):
        rect = QRectF(10., 10., 100., 68.)
        for alpha in (-1., 0., .01):
            self.assertIsNone(economy_lens_plan(rect, alpha))
        self.assertIsNone(economy_lens_plan(QRectF(), 1.))
        self.assertIsNone(economy_lens_plan(rect, 1., clip=QRectF(200., 200., 100., 100.)))

    def test_reference_depth_and_travel_scale_together(self):
        full = economy_lens_plan(QRectF(10., 10., 300., 68.), 1.)
        self.assertEqual((full.radius, full.refraction_height, full.refraction_amount), (34., 24., -48.))
        small = economy_lens_plan(QRectF(10., 10., 300., 34.), .5)
        self.assertEqual((small.radius, small.refraction_height, small.refraction_amount), (17., 6., -12.))
        tiny = economy_lens_plan(QRectF(10., 10., 3., 2.), .02)
        self.assertEqual((tiny.radius, tiny.refraction_height, tiny.refraction_amount), (1., 1., -1.))
        self.assertEqual(economy_lens_plan(full.rect, 2.).opacity, 1.)

    def test_clip_restricts_visibility_without_distorting_original_lens(self):
        rect = QRectF(10., 10., 300., 68.)
        clip = QRectF(10., 40., 100., 30.)
        plan = economy_lens_plan(rect, 1., clip=clip)
        self.assertEqual(plan.rect, rect)
        self.assertEqual(plan.visible, rect.intersected(clip))
        self.assertEqual(plan.radius, 34.)
        rect.translate(50., 50.)
        clip.translate(50., 50.)
        self.assertEqual(plan.rect, QRectF(10., 10., 300., 68.))
        self.assertEqual(plan.clip, QRectF(10., 40., 100., 30.))


if __name__ == "__main__":
    unittest.main()
