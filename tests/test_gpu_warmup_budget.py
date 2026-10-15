"""No GL hardware required: execute the actual queue and tick wrappers."""
import ast
from pathlib import Path
import sys
import math
import time
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE = ROOT / 'rm_client/ui/polar_app.py'


def methods(*names):
    tree = ast.parse(SOURCE.read_text())
    window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PolarRMWindow')
    selected = [n for n in window.body if isinstance(n, ast.FunctionDef) and n.name in names]
    cls = ast.ClassDef(name='Window', bases=[], keywords=[], body=selected, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), cls], type_ignores=[])
    scope = {'time': time, 'math': math, 'QTimer': NS(singleShot=lambda delay, callback: callback()),
             'QApplication': NS(activeModalWidget=lambda: None)}
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), 'exec'), scope)
    return scope['Window']


class WarmupTests(unittest.TestCase):
    def make_window(self):
        w = methods('prewarm_gpu_clickgui_resources', '_run_gpu_prewarm_slice')()
        w.calls = []
        w.escape_menu_visible = False
        w.clickgui_gpu = NS(available=True, prewarm=lambda *a, **k: w.calls.append(('text', a)),
                           prewarm_images=lambda *a: w.calls.append(('image', a)))
        w.gpu_compositor = NS(available=True)
        w._gpu_prewarm_jobs = lambda: iter([('text', (('font',), ('label',), 1.0), {}), ('image', (('image.png',),), {})])
        w._gpu_prewarm_timer = NS(start=lambda interval: w.calls.append(('timer', interval)))
        w._gpu_prewarm_last_ms = w._gpu_prewarm_max_ms = 0.0
        w.context = lambda: NS(isValid=lambda: True)
        w.isVisible = lambda: True
        w.makeCurrent = lambda: w.calls.append('current')
        w.doneCurrent = lambda: w.calls.append('done')
        w.backend = NS(field_log=NS(emit=lambda *a, **k: w.calls.append(('error', k))))
        return w

    def test_enqueue_does_not_execute_uploads(self):
        w = self.make_window(); w.prewarm_gpu_clickgui_resources()
        self.assertEqual(w.calls, [('timer', 16)])

    def test_one_page_each_turn_then_completion(self):
        w = self.make_window(); w.prewarm_gpu_clickgui_resources()
        w._run_gpu_prewarm_slice()
        self.assertEqual(sum(isinstance(c, tuple) and c[0] == 'text' for c in w.calls), 1)
        self.assertFalse(any(isinstance(c, tuple) and c[0] == 'image' for c in w.calls))
        w._run_gpu_prewarm_slice(); w._run_gpu_prewarm_slice()
        self.assertIsNone(w._gpu_prewarm_jobs_pending)
        self.assertEqual(w.calls.count('current'), w.calls.count('done'))

    def test_hidden_or_invalid_context_does_not_upload(self):
        for hidden in (True, False):
            w = self.make_window(); w.prewarm_gpu_clickgui_resources(); w.calls.clear()
            if hidden: w.isVisible = lambda: False
            else: w.context = lambda: NS(isValid=lambda: False)
            w._run_gpu_prewarm_slice(); self.assertEqual(w.calls, [])

    def test_escape_and_modal_pause_then_resume_same_queue(self):
        for modal in (False, True):
            w = self.make_window(); w.prewarm_gpu_clickgui_resources(); w.calls.clear()
            jobs = w._gpu_prewarm_jobs_pending
            w.escape_menu_visible = not modal
            app = w._run_gpu_prewarm_slice.__globals__["QApplication"]
            app.activeModalWidget = lambda: object() if modal else None
            w._run_gpu_prewarm_slice()
            self.assertEqual(w.calls, [])
            self.assertIs(w._gpu_prewarm_jobs_pending, jobs)
            w.escape_menu_visible = False
            app.activeModalWidget = lambda: None
            w._run_gpu_prewarm_slice()
            self.assertEqual(sum(isinstance(c, tuple) and c[0] == "text" for c in w.calls), 1)

    def test_failed_upload_cancels_queue_and_releases_context(self):
        w = self.make_window(); w.prewarm_gpu_clickgui_resources()
        def fail(*args, **kwargs): raise RuntimeError('upload')
        w.clickgui_gpu.prewarm = fail; w._run_gpu_prewarm_slice()
        self.assertIsNone(w._gpu_prewarm_jobs_pending)
        self.assertEqual(w.calls[-1], 'done')

    def test_first_paint_never_calls_bulk_prewarm(self):
        tree = ast.parse(SOURCE.read_text())
        window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PolarRMWindow')
        paint = next(n for n in window.body if isinstance(n, ast.FunctionDef) and n.name == 'paintGL')
        calls = [n.func.attr for n in ast.walk(paint) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        self.assertNotIn('prewarm_gpu_clickgui_resources', calls)
        self.assertIn('singleShot', calls)

    def paint_window(self, gpu_presented):
        w = methods('paintGL')()
        w.calls = []
        w.paint_gpu_frame = lambda: gpu_presented
        w._paint_compatibility_frame = lambda: w.calls.append('cpu')
        w._gpu_warm_pending = True
        w._gpu_prewarm_jobs_pending = None
        w._gpu_prewarm_timer = NS(isActive=lambda: False, start=lambda ms: w.calls.append('resume'))
        w.clickgui_gpu = NS(available=True)
        w.prewarm_gpu_clickgui_resources = lambda: w.calls.append('queued')
        w._last_present_time = w._last_field_frame_log_at = time.perf_counter()
        w.fps_samples = []; w.state = NS(); w.render_frame_ms_ema = 0
        w._field_frame_times_ms = []
        return w

    def test_compatibility_frame_does_not_queue_warmup(self):
        w = self.paint_window(False); w.paintGL()
        self.assertEqual(w.calls, ['cpu'])
        self.assertTrue(w._gpu_warm_pending)

    def test_successful_gpu_frame_queues_then_resumes_paused_jobs(self):
        w = self.paint_window(True); w.paintGL()
        self.assertEqual(w.calls, ['queued'])
        w.calls.clear(); w._gpu_prewarm_jobs_pending = iter(())
        w.paintGL(); self.assertEqual(w.calls, ['resume'])

    def test_warmup_only_current_scale_and_one_font_per_job(self):
        source = SOURCE.read_text()
        self.assertIn('raster_scales = (current_raster_scale,)', source)
        self.assertNotIn('0.50 + 0.25 * index for index in range(7)', source)
        self.assertIn('yield ("text", ((font,), economy_labels', source)

    def test_tick_stage_includes_nonrender_work(self):
        w = methods('_run_tick_stage')(); w._ui_tick_stages = {}
        self.assertTrue(w._run_tick_stage('backend.tick', lambda: None))
        self.assertTrue(w._run_tick_stage('backend.tick', lambda: None))
        self.assertEqual(w._ui_tick_stages['backend.tick']['count'], 2)
        self.assertGreaterEqual(w._ui_tick_stages['backend.tick']['total_ms'], 0)


if __name__ == '__main__': unittest.main(verbosity=2)
