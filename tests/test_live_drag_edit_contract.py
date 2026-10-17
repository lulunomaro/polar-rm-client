"""Slider/panel field ordering against the original Qt movement route.

Observes adapter property reads, capture reads, geometry, target lookup and
partial writes. No component API is assumed by this pre-refactor contract.
"""
from dataclasses import dataclass
from itertools import product
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from PySide6.QtCore import QPointF
from test_drag_movement_characterization import MotionCase, MotionHost, snapshot
from test_pointer_routing_characterization import Baseline
from rm_client.ui.polar.mixins.input_dispatch_mixin import InputDispatchMixin


@dataclass(frozen=True)
class Case:
    kind: str = 'slider'
    replacement: str = ''
    observe: bool = True
    width: float = 20.
    ratio_cancel: bool = False


class Point(QPointF):
    def __init__(self, host):
        super().__init__(50., 60.)
        self.host = host
    def x(self):
        self.host.record('point.x')
        return super().x()
    def y(self):
        self.host.record('point.y')
        return super().y()


class Rect:
    def __init__(self, host): self.host = host
    def left(self):
        self.host.record('rect.left')
        if self.host.contract.replacement == 'left':
            self.host.pointer_interaction.drag = self.host.successor
        return 1.
    def width(self):
        self.host.record('rect.width')
        return self.host.contract.width


class Panels(dict):
    def __init__(self, host, panel):
        super().__init__(category=panel)
        self.host = host
    def __getitem__(self, key):
        self.host.record('panels.lookup', key)
        if self.host.contract.replacement == 'lookup':
            self.host.pointer_interaction.drag = self.host.successor
        return super().__getitem__(key)


class Slider:
    def __init__(self, host): self.host, self.ratio = host, 0.
    def __getattribute__(self, name):
        if name == 'set_from_ratio':
            object.__getattribute__(self, 'host').record('slider.method')
        return object.__getattribute__(self, name)
    def set_from_ratio(self, ratio):
        self.host.record('slider.apply', ratio)
        self.ratio = ratio
        if self.host.contract.ratio_cancel:
            self.host.pointer_interaction.cancel_epoch()


class Host(MotionHost):
    def __init__(self, case, fail_at):
        self.observe = False
        super().__init__(MotionCase(kind=case.kind, point=(50.,60.), replacement=case.replacement), fail_at)
        self.contract = case
        self.slider = Slider(self)
        dict.__setitem__(self.original, 'setting', self.slider)
        dict.__setitem__(self.original, 'rect', Rect(self))
        self.old_panels = Panels(self, self.panel)
        self.polar_windows = Panels(self, self.panel)
        self.observe = case.observe
    def __getattribute__(self, name):
        if name in ('pointer_interaction', 'old_panels', 'polar_windows', 'after_setting_changed'):
            if object.__getattribute__(self, 'observe'):
                object.__getattribute__(self, 'record')('host.read', name)
        return super().__getattribute__(name)


class Event:
    def __init__(self, host): self.host = host
    def position(self):
        self.host.record('event.position')
        return Point(self.host)
    def accept(self): self.host.record('event.accept')


def run(case, *, baseline=False, fail_at=None):
    host = Host(case, fail_at)
    error = None
    try:
        (Baseline if baseline else InputDispatchMixin).mouseMoveEvent(host, Event(host))
    except RuntimeError as exc:
        error = str(exc)
    host.observe = False
    state = (snapshot(host.original), snapshot(host.successor), snapshot(host.pointer_interaction.drag),
             dict(host.panel), host.slider.ratio, host.pointer_interaction.background_release_owned)
    return host.trace, state, error


class LiveDragEditContractTests(unittest.TestCase):
    def cases(self):
        for width, cancel, replacement in product((0., .5, 20.), (False, True), ('', 'left')):
            yield Case(width=width, ratio_cancel=cancel, replacement=replacement)
        for kind, replacement in product(('old_panel', 'polar_window'), ('', 'lookup', 'dragged', 'x')):
            yield Case(kind=kind, replacement=replacement)

    def test_property_geometry_effect_order_and_every_failure_prefix(self):
        for case in self.cases():
            expected = run(case, baseline=True)
            with self.subTest(case=case):
                self.assertIsNone(expected[-1])
                self.assertEqual(run(case), expected)
            for failure in range(1, len(expected[0]) + 1):
                with self.subTest(case=case, failure=failure):
                    self.assertEqual(run(case, fail_at=failure), run(case, baseline=True, fail_at=failure))

    def test_cached_capture_mutant_is_rejected_by_live_write_contract(self):
        from rm_client.ui.polar.mixins import input_dispatch_mixin as route
        def cached_panel(panel, point, read_capture, *, mark_dragged=False):
            capture=read_capture()
            if mark_dragged:panel['dragged']=True
            panel['x']=point.x()-capture['offset_x']
            panel['y']=point.y()-capture['offset_y']
        case=Case(kind='old_panel',replacement='x')
        expected=run(case,baseline=True)
        with patch.object(route,'move_panel',cached_panel):
            self.assertNotEqual(run(case),expected)

    def test_slider_retains_original_target_after_capture_is_cancelled(self):
        trace, state, error = run(Case(ratio_cancel=True))
        self.assertIsNone(error)
        self.assertIn(('setting.changed', 2.45), trace)
        self.assertIsNone(state[2])
        self.assertTrue(state[-1])

    def test_panel_lookup_replacement_is_seen_before_axis_writes(self):
        _, state, error = run(Case(kind='polar_window', replacement='lookup'))
        self.assertIsNone(error)
        self.assertEqual((state[3]['x'], state[3]['y']), (20., 20.))

if __name__ == '__main__': unittest.main()
