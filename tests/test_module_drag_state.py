"""The pointer owner accumulates module motion and exports consumed values."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPointF
from rm_client.ui.polar.pointer_interaction import PointerInteraction


class ModuleDragStateTests(unittest.TestCase):
    def test_release_is_immutable_and_detached_from_legacy_dictionary(self):
        capture={'type':'jello_module','module':17,'moved':1}
        owner=PointerInteraction(drag=capture)
        command=owner.take_module_drag()
        self.assertIsNone(owner.drag)
        self.assertEqual((command.name,command.moved),('17',True))
        capture['module']='replacement';capture['moved']=False
        self.assertEqual((command.name,command.moved),('17',True))
        with self.assertRaises(FrozenInstanceError):command.name='changed'

    def test_payload_reads_follow_consumption_and_keep_original_read_order(self):
        trace=[]
        owner=PointerInteraction()
        successor={'type':'slider'}
        class Capture(dict):
            def get(self,key,default=None):
                trace.append((key,owner.drag))
                if key=='module':owner.drag=successor
                return super().get(key,default)
        owner.drag=Capture(module='Armor',moved=True)
        command=owner.take_module_drag()
        self.assertEqual(trace,[('module',None),('moved',successor)])
        self.assertIs(owner.drag,successor)
        self.assertEqual((command.name,command.moved),('Armor',True))

    def test_failed_payload_read_does_not_restore_capture(self):
        for failed in ('module','moved'):
            owner=PointerInteraction()
            class Capture(dict):
                def get(self,key,default=None):
                    if key==failed:raise ValueError('bad payload')
                    return super().get(key,default)
            owner.drag=Capture(module='Armor')
            with self.assertRaisesRegex(ValueError,'bad payload'):owner.take_module_drag()
            self.assertIsNone(owner.drag)

    def test_motion_copies_point_and_keeps_moved_latched(self):
        owner=PointerInteraction(drag={'type':'jello_module','start':QPointF(),'moved':False})
        point=QPointF(6,0)
        owner.track_module_drag(point)
        point.setX(100)
        self.assertEqual(owner.drag['point'],QPointF(6,0))
        self.assertTrue(owner.drag['moved'])
        owner.track_module_drag(QPointF())
        self.assertTrue(owner.drag['moved'])

if __name__=='__main__':unittest.main()
