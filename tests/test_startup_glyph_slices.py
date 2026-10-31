"""Actual Qt raster parity and resumable warmup ownership; no GPU/device I/O."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from PySide6.QtGui import QFont
from PySide6.QtOpenGL import QOpenGLTexture
from PySide6.QtWidgets import QApplication
from rm_client.ui.clickgui_gpu import ClickGuiGpuRenderer
from rm_client.ui.polar.gpu_warmup import GpuWarmupQueue

APP = QApplication.instance() or QApplication([])


class Texture:
    MipMapGeneration = QOpenGLTexture.MipMapGeneration
    Filter = QOpenGLTexture.Filter
    WrapMode = QOpenGLTexture.WrapMode
    created = []

    def __init__(self, image, *_):
        self.image = image.copy()
        self.destroyed = False
        self.created.append(self)

    def setMinificationFilter(self, _): pass
    def setMagnificationFilter(self, _): pass
    def setWrapMode(self, _): pass
    def destroy(self): self.destroyed = True


class GlyphSliceTests(unittest.TestCase):
    def setUp(self):
        Texture.created = []
        self.patch = patch('rm_client.ui.clickgui_gpu.QOpenGLTexture', Texture)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.font = QFont('Microsoft YaHei UI', 13)
        self.text = ''.join(chr(c) for c in range(32, 127)) + '终端设置中文字体测试'

    def test_sliced_raster_and_metrics_equal_synchronous_at_all_densities(self):
        for dpr, scale in ((1, 1), (1.5, 1), (2, .75)):
            with self.subTest(dpr=dpr, scale=scale):
                sync, sliced = ClickGuiGpuRenderer(), ClickGuiGpuRenderer()
                sync._pixel_ratio = sliced._pixel_ratio = dpr
                expected = sync._ensure_atlas(self.font, self.text, raster_scale=scale)
                steps = sliced._atlas_steps(self.font, self.text, raster_scale=scale)
                count = 0
                while True:
                    try:
                        next(steps)
                        count += 1
                        self.assertEqual(sliced.atlases, {})
                    except StopIteration as result:
                        actual = result.value
                        break
                self.assertGreater(count, 10)
                self.assertEqual(expected.texture.image, actual.texture.image)
                self.assertEqual(expected.glyphs, actual.glyphs)
                self.assertEqual(sliced.atlas_generation, 1)

    def test_cancellation_during_measurement_or_paint_leaves_no_texture(self):
        for turns in (1, 8):
            with self.subTest(turns=turns):
                renderer = ClickGuiGpuRenderer()
                steps = renderer._atlas_steps(self.font, self.text)
                for _ in range(turns): next(steps)
                steps.close()
                self.assertEqual(renderer.atlases, {})
                self.assertEqual(Texture.created, [])

    def test_frame_demand_cannot_be_overwritten_by_stale_warmup_page(self):
        renderer = ClickGuiGpuRenderer()
        steps = renderer._atlas_steps(self.font, self.text)
        next(steps)
        interactive = renderer._ensure_atlas(self.font, 'Ω')
        for _ in steps: pass
        final = next(iter(renderer.atlases.values()))
        self.assertTrue(set(self.text+'Ω').issubset(final.glyphs))
        self.assertTrue(interactive.texture.destroyed)
        self.assertEqual(len(Texture.created), 2)

    def test_warm_hit_does_not_upload_or_mutate_page(self):
        renderer = ClickGuiGpuRenderer()
        first = renderer._ensure_atlas(self.font, self.text)
        self.assertIs(first, renderer._ensure_atlas(self.font, self.text))
        self.assertEqual(len(Texture.created), 1)
        self.assertEqual(renderer.atlas_generation, 1)

    def test_shaped_slices_preserve_exact_page_packing_and_pixels(self):
        texts = {f'{i:03} 中文 Module setting {i}' for i in range(160)}
        sync, sliced = ClickGuiGpuRenderer(), ClickGuiGpuRenderer()
        expected = sync._ensure_shaped_atlas(self.font, texts, preserve_weight=True)
        steps = sliced._shaped_atlas_steps(self.font, texts, preserve_weight=True)
        for _ in steps:
            self.assertEqual(sliced.shaped_atlases, {})
        actual = next(iter(sliced.shaped_atlases.values()))
        self.assertEqual(len(expected.pages), len(actual.pages))
        self.assertEqual(expected.lines, actual.lines)
        for left, right in zip(expected.pages, actual.pages):
            self.assertEqual(left.texture.image, right.texture.image)

    def test_shaped_stale_preparation_preserves_interactive_lines(self):
        renderer = ClickGuiGpuRenderer()
        texts = {f'{i} 设置' for i in range(25)}
        steps = renderer._shaped_atlas_steps(self.font, texts)
        next(steps)
        first = renderer._ensure_shaped_atlas(self.font, {'Interactive line'})
        for _ in steps: pass
        final = next(iter(renderer.shaped_atlases.values()))
        self.assertTrue((texts | {'Interactive line'}).issubset(final.lines))
        self.assertIs(final.pages[0].texture, first.pages[0].texture)

    def test_shaped_cancel_has_no_published_layout_or_texture(self):
        renderer = ClickGuiGpuRenderer()
        steps = renderer._shaped_atlas_steps(self.font, {str(i) for i in range(25)})
        next(steps)
        steps.close()
        self.assertEqual(Texture.created, [])
        self.assertEqual(renderer._shaped_layouts, {})
        self.assertEqual(renderer.shaped_atlases, {})


class SliceQueueTests(unittest.TestCase):
    def setUp(self):
        self.time = 0.
        self.events = []
        self.queue = GpuWarmupQueue(iter([('text', (), {})]))

    def run_slice(self, steps):
        return self.queue.run_slice(self.queue.pending, uploads=NS(prewarm_steps=steps),
            acquire_context=lambda: self.events.append('acquire'),
            release_context=lambda: self.events.append('release'),
            report_error=lambda e: self.events.append(str(e)), clock=lambda: self.time)

    def test_budget_yields_and_resumes_exact_iterator_then_completes(self):
        def steps():
            for i in range(3):
                self.events.append(i)
                self.time += .004
                yield
        for i in range(3):
            self.assertTrue(self.run_slice(steps))
            self.assertEqual(self.events.count(i), 1)
            self.assertFalse(self.queue.completed)
        self.run_slice(steps)
        self.assertIsNone(self.queue._steps)
        self.assertFalse(self.run_slice(steps))
        self.assertTrue(self.queue.completed)
        self.assertEqual(self.events.count('acquire'), self.events.count('release'))

    def test_replacement_discards_staged_work_without_consuming_new_jobs(self):
        def steps():
            self.time += .004
            yield
            self.events.append('stale-publication')
        self.run_slice(steps)
        replacement = iter([('prepare', (), {})])
        self.queue.replace(replacement)
        self.assertIsNone(self.queue._steps)
        self.run_slice(steps)
        self.assertNotIn('stale-publication', self.events)

    def test_slice_failure_cancels_and_reports_without_claiming_completion(self):
        def steps():
            yield
            raise ValueError('raster failed')
        self.assertFalse(self.run_slice(steps))
        self.assertFalse(self.queue.completed)
        self.assertIsNone(self.queue._steps)
        self.assertEqual(self.events[-2:], ['raster failed', 'release'])

    def test_reentrant_replacement_does_not_consume_another_old_job(self):
        replacement = iter([('prepare', (), {})])
        def old():
            yield ('text', (), {})
            self.events.append('old-second-job')
            yield ('prepare', (), {})
        self.queue.replace(old())
        def steps():
            self.queue.replace(replacement)
            yield
        self.run_slice(steps)
        self.assertIs(self.queue.pending, replacement)
        self.assertNotIn('old-second-job', self.events)


if __name__ == '__main__': unittest.main()
