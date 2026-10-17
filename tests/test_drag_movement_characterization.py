"""Frozen movement traces with live capture replacement and partial writes.

The reference is the existing digest-checked 1b2f580 pointer fixture; this suite
adds drag-model effects to that route rather than freezing post-refactor logic.
"""
from dataclasses import dataclass
from itertools import product
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from PySide6.QtCore import QPointF, QRectF
from test_pointer_routing_characterization import Baseline, Case, Event, Host
from rm_client.ui.polar.mixins.input_dispatch_mixin import InputDispatchMixin


def value(item):
    if isinstance(item,QPointF): return (item.x(),item.y())
    return item


@dataclass(frozen=True)
class MotionCase:
    kind: str='jello_module'
    point: tuple=(7.,0.)
    moved: bool=False
    start: str='point'
    replacement: str=''
    cancel_after_setting: bool=False
    cancelled: bool=False


class Capture(dict):
    def __init__(self,host,name,**items):
        super().__init__(items);self.host,self.name=host,name
    def __getitem__(self,key): self.host.record(self.name+'.read',key); return super().__getitem__(key)
    def get(self,key,default=None): self.host.record(self.name+'.get',key); return super().get(key,default)
    def __setitem__(self,key,item):
        self.host.record(self.name+'.write',key,value(item));super().__setitem__(key,item)
        if self.name=='drag' and key=='point' and self.host.motion_case.replacement=='point':
            self.host.pointer_interaction.drag=self.host.successor


class Panel(Capture):
    def __setitem__(self,key,item):
        super().__setitem__(key,item)
        if self.host.motion_case.replacement==key:
            self.host.pointer_interaction.drag=self.host.successor


class Slider:
    def __init__(self,host): self.host=host; self.ratio=0.
    def set_from_ratio(self,ratio):
        self.host.record('slider.ratio',ratio);self.ratio=ratio


class MotionEvent(Event):
    def position(self): return QPointF(*self.host.motion_case.point)


class MotionHost(Host):
    def __init__(self,case,fail_at=None):
        super().__init__(Case(gui=True,hits=()),fail_at)
        self.motion_case=case
        self.slider=Slider(self)
        self.panel=Panel(self,'panel',x=100.,y=200.,dragged=False)
        self.old_panels={'category':self.panel};self.polar_windows={'category':self.panel}
        self.successor=Capture(self,'successor',type=case.kind,offset_x=30.,offset_y=40.,moved=False)
        entries=dict(type=case.kind,offset_x=3.,offset_y=4.,category='category',
                     setting=self.slider,rect=QRectF(1,0,20,10),module='Armor',moved=case.moved)
        if case.start=='point':entries['start']=QPointF(0,0)
        elif case.start=='invalid':entries['start']='not a point'
        self.original=Capture(self,'drag',**entries)
        self.pointer_interaction.drag=self.original
        if case.cancelled:self.pointer_interaction.cancel_epoch()
    def after_setting_changed(self,setting):
        self.record('setting.changed',setting.ratio)
        if self.motion_case.cancel_after_setting:self.pointer_interaction.cancel_epoch()
    def invalidate_gui_cache(self):self.record('cache.invalidate')


def snapshot(capture):
    if capture is None:return None
    return {k:value(v) for k,v in dict(capture).items() if k not in ('setting','rect')}


def run(case,*,baseline=False,fail_at=None):
    host=MotionHost(case,fail_at)
    error=None
    try:
        (Baseline if baseline else InputDispatchMixin).mouseMoveEvent(host,MotionEvent(host,'Move'))
    except RuntimeError as exc:error=str(exc)
    state=(snapshot(host.original),snapshot(host.successor),snapshot(host.pointer_interaction.drag),
           dict(host.panel),host.slider.ratio,host.pointer_interaction.background_release_owned)
    return host.trace,state,error


class DragMovementCharacterizationTests(unittest.TestCase):
    def cases(self):
        for point,moved,start,replacement in product(((0.,0.),(5.99,0.),(6.,0.),(4.,4.),(30.,-12.)),
                                                      (False,True),('point','missing','invalid'),('','point')):
            yield MotionCase(point=point,moved=moved,start=start,replacement=replacement)
        for kind,point,replacement in product(('old_panel','polar_window'),((0.,0.),(30.,-12.)),('','dragged','x')):
            yield MotionCase(kind=kind,point=point,replacement=replacement)
        for point,cancel in product(((-5.,0.),(11.,0.),(35.,0.)),(False,True)):
            yield MotionCase(kind='slider',point=point,cancel_after_setting=cancel)
        for kind in ('slider','jello_module','old_panel','polar_window'):
            yield MotionCase(kind=kind,cancelled=True)

    def test_live_move_effects_and_every_failure_prefix_match_frozen_route(self):
        for case in self.cases():
            expected=run(case,baseline=True)
            with self.subTest(case=case):
                self.assertIsNone(expected[-1]);self.assertEqual(run(case),expected)
            for fail_at in range(1,len(expected[0])+1):
                with self.subTest(case=case,fail_at=fail_at):
                    self.assertEqual(run(case,fail_at=fail_at),run(case,baseline=True,fail_at=fail_at))

    def test_snapshotting_capture_mutation_is_rejected(self):
        from rm_client.ui.polar.pointer_interaction import PointerInteraction
        def stale_update(owner, point):
            drag=owner.drag
            drag['point']=QPointF(point)
            drag['moved']=True
        case=MotionCase(point=(7.,0.),replacement='point')
        expected=run(case,baseline=True)
        with patch.object(PointerInteraction,'track_module_drag',stale_update):
            self.assertNotEqual(run(case),expected)

    def test_panel_y_reads_successor_offset_after_x_write(self):
        _,state,error=run(MotionCase(kind='old_panel',point=(50.,60.),replacement='x'))
        self.assertIsNone(error)
        self.assertEqual(state[3]['x'],47.)
        self.assertEqual(state[3]['y'],20.)

    def test_jello_threshold_is_euclidean_and_inclusive_at_six(self):
        for point,expected in (((6.,0.),True),((5.99,0.),False),((4.,4.),False)):
            _,state,_=run(MotionCase(point=point))
            self.assertEqual(state[0]['moved'],expected)

if __name__=='__main__':unittest.main()
