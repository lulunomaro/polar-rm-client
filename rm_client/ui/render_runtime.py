"""Bounded owner-thread CPU submission diagnostics, never a GPU timer claim."""
from collections import deque
import math


class RenderRuntime:
    def __init__(self):
        self.driver = {}
        self.initializations = 0
        self.initialization_failures = 0
        self.gpu_frames = 0
        self.compatibility_frames = 0
        self.last_fallback = ''
        self.samples = deque(maxlen=720)

    def initialized(self, driver, available, error=''):
        self.driver = dict(driver)
        renderer = str(self.driver.get('renderer','')).lower()
        self.driver['implementation'] = ('software OpenGL' if any(name in renderer for name in
            ('llvmpipe','softpipe','swrast','gdi generic','software rasterizer','microsoft basic render'))
            else 'driver OpenGL (hardware status follows driver identity)')
        self.initializations += 1
        self.initialization_failures += not available
        if not available:
            self.last_fallback = str(error or 'GPU compositor initialization failed')[:1024]

    def frame(self, gpu, elapsed_ms, reason=''):
        if gpu:
            self.gpu_frames += 1
        else:
            self.compatibility_frames += 1
            self.last_fallback = str(reason or 'GPU frame rejected; see render stage diagnostics')[:1024]
        self.samples.append((bool(gpu), max(0., float(elapsed_ms))))

    def snapshot(self):
        ordered = sorted(ms for _, ms in self.samples)
        percentile = lambda p: ordered[max(0, math.ceil(len(ordered)*p)-1)] if ordered else 0.
        return dict(driver=dict(self.driver), initializations=self.initializations,
            initialization_failures=self.initialization_failures, gpu_frames=self.gpu_frames,
            compatibility_frames=self.compatibility_frames, last_fallback=self.last_fallback,
            cpu_submit_p95_ms=percentile(.95), cpu_submit_p99_ms=percentile(.99),
            samples=len(ordered), scope='CPU submission including Qt OpenGL painter; not GPU execution time')
