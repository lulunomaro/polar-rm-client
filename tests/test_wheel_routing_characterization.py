"""Wheel route differential: viewport read order, exact effects and failures."""
from dataclasses import dataclass, replace
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
sys.path.insert(0, str(ROOT / 'tests'))
from PySide6.QtCore import QPointF
from rm_client.ui.polar.mixins import input_dispatch_mixin as current
from rm_client.ui.polar.primitives import InertialScroll

REFERENCE = Path(__file__).parent / 'fixtures/wheel_routing_baseline.py'
Baseline = runpy.run_path(str(REFERENCE))['WheelRoutingBaseline']
HASH = '30103e1a61d3e24e16c2388b764220f63917656f5180b45b1c394dd19683c04f'

@dataclass(frozen=True)
class Case:
    mode: str = 'jello_panel'
    pixel: float = 0.
    angle: float = 120.
    maximum: float = 70.
    initial: float = 40.
    hits: bool = True
    mutate: bool = False
    lifecycle: bool = False
    escape: bool = False
    student: bool = False
    inventory: bool = False
    chat: bool = False
    economy: bool = False
    economy_region: str = 'offers'
    polar_region: str = ''
    combat: bool = False
    new_motion: bool = False

class TracedDict(dict):
    def __init__(self, host, name, **values):
        super().__init__(values); self.host, self.name = host, name
    def __getitem__(self, key): self.host.record(self.name + '.read', key); return super().__getitem__(key)
    def __setitem__(self, key, value): self.host.record(self.name + '.write', key, value); super().__setitem__(key, value)
    def get(self, key, default=None): self.host.record(self.name + '.get', key); return super().get(key, default)

class Rect:
    def __init__(self, host, name, matches=False, mutate=None):
        self.host, self.name, self.matches, self.mutate = host, name, matches, mutate
    def contains(self, point):
        self.host.record(self.name + '.contains')
        if self.mutate: self.mutate()
        return self.matches
    def height(self): self.host.record(self.name + '.height'); return 160.

class Motion(InertialScroll):
    def __init__(self, host, name): super().__init__(); self.host, self.name = host, name
    def set_bounds(self, maximum): self.host.record(self.name + '.bounds', maximum); super().set_bounds(maximum)
    def add_wheel(self, delta): self.host.record(self.name + '.wheel', delta); super().add_wheel(delta)

class Jello:
    def __init__(self, host):
        self.host = host
        self.panel_scroll = TracedDict(host, 'panel_scroll', key=host.case.initial)
        self._dropdown_scroll = host.case.initial
    @property
    def dropdown_scroll(self): self.host.record('dropdown.read'); return self._dropdown_scroll
    @dropdown_scroll.setter
    def dropdown_scroll(self, value): self.host.record('dropdown.write',value); self._dropdown_scroll=value
    def mark_scroll(self, key): self.host.record('jello.mark', key)
    def scroll_settings_by(self, key, delta, maximum): self.host.record('jello.settings',key,delta,maximum)

class Event:
    def __init__(self, host): self.host=host; self.angle_reads=0
    def position(self): self.host.record('event.position'); return QPointF(10,20)
    def pixelDelta(self): self.host.record('event.pixel'); return QPointF(0,self.host.case.pixel)
    def angleDelta(self):
        self.host.record('event.angle')
        self.angle_reads += 1
        # Dynamic event reads expose eagerly cached deltas on the Jello/control paths.
        return QPointF(0,self.host.case.angle + (self.angle_reads-1)*24 if self.host.case.mutate else self.host.case.angle)
    def accept(self): self.host.record('event.accept')

class Host(current.InputDispatchMixin):
    def __init__(self, case, fail_at):
        self.case, self.fail_at, self.trace = case, fail_at, []
        self.escape_menu_visible=case.escape
        self.student_ui=NS(visible=case.student,panel='inventory' if case.inventory else 'other')
        self.match_chat=NS(input_open=case.chat,scroll=self.effect('chat.scroll'))
        self.economy_visible=case.economy
        self.economy_island=NS(progress=.8)
        self.economy_workspace_layer=NS(layout=self.layout)
        self.economy_offers_scroll=Motion(self,'economy.offers')
        self.economy_detail_scroll=Motion(self,'economy.detail')
        self.frame_scheduler=NS(force=self.effect('frame.force'))
        self.panel_modules_rect=Rect(self,'modules',case.polar_region=='modules')
        self.panel_settings_rect=Rect(self,'settings',case.polar_region=='settings')
        self.selected_category='key'; self.selected_module=NS(name='key')
        self.panel_module_scroll=TracedDict(self,'module_scroll',key=case.initial)
        self.settings_scroll=TracedDict(self,'settings_scroll',key=case.initial)
        self.jello_state=Jello(self)
        self.old_panels={'key':TracedDict(self,'old',scroll=case.initial)}
        self.new_panels={'key':TracedDict(self,'new',scroll=case.initial)}
        self.dropdown_panels={'key':TracedDict(self,'dropdown',scroll=case.initial)}
        self.polar_windows={'key':TracedDict(self,'polar_multi',scroll=case.initial)}
        self.polar_settings_scroll_motion={} if case.new_motion else {'key':Motion(self,'polar.settings')}
        self.polar_unified_scroll_motion=Motion(self,'polar.unified')
        bottom=TracedDict(self,'bottom',rect=Rect(self,'bottom',case.hits),mode=case.mode,key='key',maximum=case.maximum)
        def mutate():
            if case.mutate: dict.__setitem__(bottom,'maximum',case.maximum+3)
        top=TracedDict(self,'top',rect=Rect(self,'top',False,mutate),mode='unread',key='unread',maximum=999)
        self.gui_scroll_viewports=[bottom,top]
        self.registry=NS(get=self.effect('registry.get'))
        self.backend=NS(mqtt=NS(mock_output=True))
        self.control=NS(add_wheel=self.effect('control.wheel'),send_from_state=self.effect('control.send'),mock_output=False)
        self.state=None
    def record(self,*entry):
        self.trace.append(entry)
        if len(self.trace)==self.fail_at: raise RuntimeError('injected')
    def effect(self,name):
        def call(*args): self.record(name,*args if name not in ('control.send',) else ())
        return call
    def _lifecycle_owns_input(self): return self.case.lifecycle
    def handle_student_action(self,*args): self.record('student.action',*args)
    def active_clickgui_mode(self): self.record('gui.mode'); return 'PolarPanel' if self.case.polar_region else 'Jello'
    def panel_modules(self): self.record('modules.list'); return [None]*12
    def width(self): return 800
    def height(self): return 600
    def get_gui_scale_for_bounds(self,*args): self.record('gui.scale'); return 1.
    def get_setting_number(self,*args): self.record('wheel.scale'); return 1.5
    def combat_control_input_allowed(self): self.record('combat.allowed'); return self.case.combat
    def setting_mode(self,*args): return "Legacy"  # economy grid disabled
    def layout(self,*args):
        self.record('economy.layout')
        return NS(offers_viewport=Rect(self,'offers',self.case.economy_region=='offers'),
                  detail_viewport=Rect(self,'detail',self.case.economy_region=='detail'))

def run(case,baseline=False,fail_at=None):
    host=Host(case,fail_at); error=None
    try: (Baseline if baseline else current.InputDispatchMixin).wheelEvent(host,Event(host))
    except RuntimeError as exc: error=str(exc)
    state=(dict(host.panel_module_scroll),dict(host.settings_scroll),dict(host.jello_state.panel_scroll),
           host.jello_state._dropdown_scroll,
           *(dict(getattr(host,name)['key']) for name in ('old_panels','new_panels','dropdown_panels','polar_windows')),
           *((m.position,m.target,m.velocity) for m in (host.economy_offers_scroll,host.economy_detail_scroll,
                                                       host.polar_settings_scroll_motion.get('key', InertialScroll()),host.polar_unified_scroll_motion)),
           host.control.mock_output)
    return host.trace,state,error

class WheelRoutingCharacterizationTests(unittest.TestCase):
    def cases(self):
        modes=('jello_dropdown','jello_panel','jello_settings','old','new','dropdown','polar_multi','polar_settings','polar_unified','unknown')
        for mode,pixel,angle,maximum,mutate in product(modes,(0.,4.,-13.),(120.,-120.,0.),(0.,70.),(False,True)):
            yield Case(mode=mode,pixel=pixel,angle=angle,maximum=maximum,mutate=mutate)
        yield Case(mode='polar_settings',new_motion=True)
        for flag in ('lifecycle','escape','student','chat'):
            yield Case(**{flag:True})
        yield Case(student=True,inventory=True)
        for region in ('offers','detail','outside'): yield Case(economy=True,economy_region=region)
        for region in ('modules','settings'): yield Case(polar_region=region)
        for combat,mutate in product((False,True),(False,True)): yield Case(hits=False,combat=combat,mutate=mutate)
    def test_wrong_scroll_family_is_detected_by_frozen_effects(self):
        from rm_client.ui.polar.scroll_routing import ScrollFamily, scroll_target_at
        def wrong_family(viewports, point):
            plan = scroll_target_at(viewports, point)
            return replace(plan, family=ScrollFamily.UNIFIED_MOTION)
        case = Case(mode='jello_panel')
        expected = run(case, baseline=True)
        with patch.object(current, 'scroll_target_at', side_effect=wrong_family):
            self.assertNotEqual(run(case), expected)

    def test_reference_integrity(self):
        self.assertEqual(hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),HASH)
    def test_exact_effects_live_viewport_reads_and_failure_prefixes(self):
        for case in self.cases():
            expected=run(case,baseline=True)
            with self.subTest(case=case):
                self.assertIsNone(expected[-1]); self.assertEqual(run(case),expected)
            for fail_at in range(1,len(expected[0])+1):
                with self.subTest(case=case,fail_at=fail_at):
                    self.assertEqual(run(case,fail_at=fail_at),run(case,baseline=True,fail_at=fail_at))

if __name__=='__main__': unittest.main()
