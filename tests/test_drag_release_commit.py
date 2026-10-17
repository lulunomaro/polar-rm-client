"""Jello release must consume its command before fallible/reentrant effects."""
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'tests'))
from PySide6.QtCore import QPointF
from test_pointer_routing_characterization import Case, Event, Host


class DragHost(Host):
    def __init__(self, *, fault='', reenter=False, moved=False):
        super().__init__(Case(gui=True, hits=()), None)
        self.module=NS(name='Armor', enabled=False)
        self.registry=NS(get=lambda name: self.module, toggle_module=self.toggle)
        self.fault=fault
        self.reenter=reenter
        self.toggle_count=0
        self.successor=None
        self.save_count=0
        self.assignments=[]
        self.pointer_interaction.drag={'type':'jello_module','module':'Armor','moved':moved}
        self.minecraft_hud=NS(layout=lambda *args,**kwargs:NS(slot_at=lambda point:2))
    def toggle(self,module,*,source):
        self.toggle_count+=1
        module.enabled=not module.enabled
        if self.fault=='toggle': raise RuntimeError('toggle observer failed')
        if self.successor is not None:
            self.pointer_interaction.drag=self.successor
        if self.reenter:
            self.reenter=False
            self.mouseReleaseEvent(Event(self,'Release'))
    def toast(self,*args):
        if self.fault=='toast': raise RuntimeError('toast failed')
    def auto_save_config(self):
        self.save_count+=1
        if self.fault=='save': raise RuntimeError('save failed')
    def invalidate_gui_cache(self):
        if self.fault=='cache': raise RuntimeError('cache failed')
    def setting_mode(self,*args): return 'Auto'
    def width(self): return 800
    def height(self): return 600
    def assign_minecraft_function_slot(self,slot,name):
        self.assignments.append((slot,name))
        if self.fault=='assign': raise RuntimeError('assignment notification failed')


class DragReleaseCommitTests(unittest.TestCase):
    def test_completed_toggle_is_not_replayed_after_later_effect_failure(self):
        for fault in ('toggle','toast','save','cache'):
            with self.subTest(fault=fault):
                host=DragHost(fault=fault)
                with self.assertRaises(RuntimeError): host.mouseReleaseEvent(Event(host,'Release'))
                self.assertTrue(host.module.enabled)
                self.assertIsNone(host.pointer_interaction.drag)
                host.fault=''
                host.mouseReleaseEvent(Event(host,'Release'))
                self.assertEqual(host.toggle_count,1)
                self.assertTrue(host.module.enabled)

    def test_reentrant_release_cannot_toggle_the_same_capture_twice(self):
        host=DragHost(reenter=True)
        host.mouseReleaseEvent(Event(host,'Release'))
        self.assertEqual(host.toggle_count,1)
        self.assertTrue(host.module.enabled)
        self.assertIsNone(host.pointer_interaction.drag)

    def test_failed_drop_does_not_replay_slot_assignment(self):
        host=DragHost(fault='assign',moved=True)
        with self.assertRaises(RuntimeError): host.mouseReleaseEvent(Event(host,'Release'))
        self.assertIsNone(host.pointer_interaction.drag)
        host.fault=''
        host.mouseReleaseEvent(Event(host,'Release'))
        self.assertEqual(host.assignments,[(2,'Armor')])

    def test_reentrant_successor_capture_survives_old_commit(self):
        for kind in ('slider','old_panel','polar_window'):
            for fault in ('','toast','save'):
                with self.subTest(kind=kind,fault=fault):
                    host=DragHost(fault=fault)
                    successor={'type':kind,'setting':object(),'rect':object()}
                    host.successor=successor
                    if fault:
                        with self.assertRaises(RuntimeError): host.mouseReleaseEvent(Event(host,'Release'))
                    else:
                        host.mouseReleaseEvent(Event(host,'Release'))
                    self.assertEqual(host.toggle_count,1)
                    self.assertIs(host.pointer_interaction.drag,successor)
                    self.assertEqual(host.save_count,0 if fault=='toast' else 1)

    def test_successful_click_and_drop_commit_once(self):
        for moved in (False,True):
            with self.subTest(moved=moved):
                host=DragHost(moved=moved)
                host.mouseReleaseEvent(Event(host,'Release'))
                host.mouseReleaseEvent(Event(host,'Release'))
                self.assertEqual(host.toggle_count,0 if moved else 1)
                self.assertEqual(host.assignments,[(2,'Armor')] if moved else [])
                self.assertIsNone(host.pointer_interaction.drag)

    def test_lifecycle_and_modal_owners_still_precede_drag_commit(self):
        for owner in ('lifecycle','escape','student','radial'):
            with self.subTest(owner=owner):
                host=DragHost()
                original=host.pointer_interaction.drag
                host.case=replace(host.case,**{owner:True})
                host.overlay_coordinator.radial_active=owner=='radial'
                host.mouseReleaseEvent(Event(host,'Release'))
                self.assertEqual(host.toggle_count,0)
                self.assertIs(host.pointer_interaction.drag,original)

    def test_epoch_cancellation_before_release_never_commits(self):
        host=DragHost()
        host.pointer_interaction.cancel_epoch()
        host.mouseReleaseEvent(Event(host,'Release'))
        self.assertEqual(host.toggle_count,0)
        self.assertFalse(host.pointer_interaction.background_release_owned)

if __name__=='__main__': unittest.main()
