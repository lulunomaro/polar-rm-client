"""Differential pointer effects, dynamic hit reads and interrupted releases."""
from dataclasses import dataclass
import hashlib
from itertools import product
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QPointF, Qt
from rm_client.ui.polar.mixins import input_dispatch_mixin as current
from rm_client.ui.polar.pointer_interaction import PointerInteraction

REFERENCE = Path(__file__).parent / 'fixtures/pointer_routing_baseline.py'
Baseline = runpy.run_path(str(REFERENCE))['PointerRoutingBaseline']
HASH = '8e5803f9177d051ff5915fed10f5e719c8ad032b3af86b43e7d6bb2bc7ee895d'

@dataclass(frozen=True)
class Case:
    gui: bool = False
    exiting: bool = False
    lifecycle: bool = False
    escape: bool = False
    student: bool = False
    radial: bool = False
    binding: bool = False
    owned: bool = False
    background: bool = False
    hits: tuple = ((None, True, 'bottom'), (3, True, 'right'), (1, False, 'outside'))
    mutate_on_hit: bool = False
    button: int = 1

class Event:
    def __init__(self, host, phase): self.host, self.phase = host, phase
    def button(self): return {1: Qt.MouseButton.LeftButton, 2: Qt.MouseButton.MiddleButton, 3: Qt.MouseButton.RightButton}[self.host.case.button]
    def position(self): return QPointF(10, 20)
    def accept(self): self.host.record('accept', self.phase)

class Hit:
    def __init__(self, host, button, matches, name):
        self.host, self._button, self.matches, self.name = host, button, matches, name
    @property
    def button(self):
        self.host.record('button', self.name)
        return self._button
    @property
    def rect(self):
        self.host.record('rect', self.name)
        return self
    def contains(self, point):
        self.host.record('contains', self.name, point.x(), point.y())
        if self.host.case.mutate_on_hit and self.name == 'outside':
            # Selection must remain lazy: this change is seen by the next item.
            self.host.hotspots[1]._button = 1
            self.host.hover_point = QPointF(77, 88)
        return self.matches
    @property
    def action(self):
        self.host.record('action', self.name)
        return 'jello_dismiss_background' if self.name == 'bottom' else self.name
    @property
    def payload(self):
        self.host.record('payload', self.name)
        return self.name
    @property
    def tooltip(self):
        self.host.record('tooltip', self.name)
        return self.name

class Host(current.InputDispatchMixin):
    def __init__(self, case, fail_at):
        self.case, self.fail_at, self.trace = case, fail_at, []
        self._pointer_interaction = PointerInteraction()
        if case.binding: self.pointer_interaction.binding_module = NS(name='bound')
        if case.background: self.pointer_interaction.own_background_press(QPointF(10, 20))
        if case.owned: self.pointer_interaction.own_release()
        self.gui_visible, self.home_visible = case.gui, False
        self.gui_timeline = NS(progress=1. if case.exiting else 0.)
        self.backend = NS(mqtt=NS(mock_output=True))
        self.quick_action_wheel = NS(active=False, selected_index=0, update_pointer=self.effect('radial.pointer'))
        self.overlay_coordinator = NS(radial_active=case.radial)
        self.frame_scheduler = NS(force=self.effect('frame.force'))
        self.control = NS(set_mouse_button=self.effect('control.button'), add_mouse_motion=self.effect('control.motion'))
        self.registry = NS(get=self.effect('registry.get'))
        self.state = None
        self.tactical_map_hold = False
        self.hotspots = [Hit(self, *hit) for hit in case.hits]
        self.hover_tooltip, self.hover_point = '', QPointF(0, 0)
        self._control_mouse_point = None
        self._startup_last_hover_action = ''
        self.economy_visible, self.economy_hold_key = False, ''
        self.search_focus = True
    def record(self, *entry):
        self.trace.append(entry)
        if len(self.trace) == self.fail_at: raise RuntimeError('injected')
    def effect(self, name):
        def call(*args, **kwargs): self.record(name)
        return call
    def _lifecycle_owns_input(self): return self.case.lifecycle
    def handle_escape_menu_mouse(self, event, phase):
        self.record('escape', phase); return self.case.escape
    def handle_student_mouse(self, event, phase):
        self.record('student', phase); return self.case.student
    def setFocus(self, reason): self.record('focus')
    def cancel_quick_action_wheel(self): self.record('radial.cancel')
    def dispatch(self, action, payload, point, button, rect): self.record('dispatch', action, payload, button)
    def publish_map_click_from_screen(self, point): self.record('map'); return False
    def combat_control_input_allowed(self): self.record('combat.allowed'); return not self.gui_visible
    def set_clickgui_visible(self, visible): self.record('gui', visible); self.gui_visible = visible
    def get_setting_number(self, *args): return 1.
    def setting_bool(self, *args): return False

def run(case, phases, baseline=False, fail_at=None):
    host = Host(case, fail_at)
    error = None
    with patch.object(current.time, 'perf_counter', return_value=10.):
        try:
            for phase in phases:
                owner = Baseline if baseline else current.InputDispatchMixin
                getattr(owner, 'mouse' + phase + 'Event')(host, Event(host, phase))
        except RuntimeError as exc: error = str(exc)
    pointer = host.pointer_interaction
    state = (host.gui_visible, host.search_focus, host.hover_tooltip,
             pointer.background_release_owned, pointer.background_press is not None,
             pointer.background_dragged, host._control_mouse_point is not None)
    return host.trace, state, error

class PointerRoutingCharacterizationTests(unittest.TestCase):
    def test_reference_integrity(self):
        self.assertEqual(hashlib.sha256(REFERENCE.read_bytes()).hexdigest(), HASH)

    def cases(self):
        for button, gui, mutate in product((1,2,3), (False,True), (False,True)):
            yield Case(button=button, gui=gui, mutate_on_hit=mutate)
            yield Case(button=button, gui=gui, mutate_on_hit=mutate, hits=())
        for flag in ('exiting','lifecycle','escape','student','radial','binding','owned','background'):
            for button in (1,2,3):
                yield Case(button=button, **{flag: True})
        yield Case(gui=True, background=True)
        yield Case(gui=True, background=True, hits=((1,True,'bottom'),))
        yield Case(gui=True, background=True, escape=True, student=True)

    def test_dynamic_selection_effect_order_and_failure_prefixes(self):
        for case in self.cases():
            for phases in (('Press',), ('Move',), ('Release',), ('Press','Move','Release')):
                expected = run(case, phases, baseline=True)
                with self.subTest(case=case, phases=phases):
                    self.assertIsNone(expected[-1])
                    self.assertEqual(run(case, phases), expected)
                for fail_at in range(1, len(expected[0]) + 1):
                    with self.subTest(case=case, phases=phases, fail_at=fail_at):
                        self.assertEqual(run(case, phases, fail_at=fail_at),
                                         run(case, phases, baseline=True, fail_at=fail_at))

if __name__ == '__main__': unittest.main()
