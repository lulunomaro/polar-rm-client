"""Only the initiating left release may commit a Jello module capture."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from PySide6.QtCore import QPointF, QRectF
from rm_client.ui.polar.mixins.input_dispatch_mixin import _on_action_jello_module_press
from test_drag_release_commit import DragHost
from test_pointer_routing_characterization import Event


def release(host, button):
    host.case=replace(host.case,button=button)
    host.mouseReleaseEvent(Event(host,'Release'))


def acquire(host, button=1):
    host.pointer_interaction.begin_press(left_button=button==1)
    _on_action_jello_module_press(host,'Armor',QPointF(10,20),button,QRectF(0,0,30,30))


class DragButtonOwnershipTests(unittest.TestCase):
    def test_production_action_acquires_only_left_button(self):
        for button in (1,2,3):
            with self.subTest(button=button):
                host=DragHost()
                host.pointer_interaction.drag=None
                acquire(host,button)
                self.assertEqual(host.pointer_interaction.drag is not None,button==1)

    def test_other_release_cannot_toggle_or_assign_before_left_release(self):
        for button in (2,3):
            for moved in (False,True):
                with self.subTest(button=button,moved=moved):
                    host=DragHost(moved=moved)
                    original=host.pointer_interaction.drag
                    release(host,button)
                    self.assertIs(host.pointer_interaction.drag,original)
                    self.assertEqual(host.toggle_count,0)
                    self.assertEqual(host.assignments,[])
                    self.assertNotIn(('control.button',),host.trace)
                    release(host,1)
                    self.assertIsNone(host.pointer_interaction.drag)
                    self.assertEqual(host.toggle_count,0 if moved else 1)
                    self.assertEqual(host.assignments,[(2,'Armor')] if moved else [])

    def test_foreign_release_then_focus_cancellation_never_commits_or_sticks(self):
        host=DragHost()
        release(host,3)
        host.pointer_interaction.cancel_epoch()
        release(host,3)
        self.assertTrue(host.pointer_interaction.background_release_owned)
        release(host,1)
        self.assertFalse(host.pointer_interaction.background_release_owned)
        self.assertIsNone(host.pointer_interaction.drag)
        self.assertEqual(host.toggle_count,0)
        acquire(host)
        release(host,1)
        self.assertEqual(host.toggle_count,1)

    def test_matching_release_remains_at_most_once_under_reentry(self):
        host=DragHost(reenter=True)
        release(host,2)
        release(host,1)
        self.assertEqual(host.toggle_count,1)
        self.assertIsNone(host.pointer_interaction.drag)

if __name__=='__main__': unittest.main()
