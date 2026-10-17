"""Local edit port behavior with inert models, no Qt/window imports."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.drag_editing import apply_slider_ratio, edit_slider, move_panel


class DragEditingTests(unittest.TestCase):
    def test_press_and_move_share_ratio_policy_and_setting_clamps(self):
        for width, x, expected in ((20.,11.,.5),(0.,3.,2.),(.2,0.,-1.)):
            calls=[]
            target=NS(set_from_ratio=calls.append)
            rect=NS(left=lambda:1.,width=lambda:width)
            point=NS(x=lambda:x)
            apply_slider_ratio(target,rect,point)
            self.assertIs(edit_slider(lambda:{'setting':target,'rect':rect},point),target)
            self.assertEqual(calls,[expected,expected])

    def test_slider_reads_current_capture_for_each_payload_field(self):
        calls=[]
        target=NS(set_from_ratio=calls.append)
        bounds=NS(left=lambda:0.,width=lambda:10.)
        captures=iter(({'setting':target},{'rect':bounds}))
        self.assertIs(edit_slider(lambda:next(captures),NS(x=lambda:5.)),target)
        self.assertEqual(calls,[.5])

    def test_cancel_during_ratio_keeps_original_setting_for_notification(self):
        capture={}
        def apply(ratio):capture.clear()
        target=NS(set_from_ratio=apply)
        capture.update(setting=target,rect=NS(left=lambda:0.,width=lambda:10.))
        self.assertIs(edit_slider(lambda:capture,NS(x=lambda:5.)),target)
        self.assertEqual(capture,{})

    def test_panel_uses_live_offsets_after_each_write(self):
        capture={'offset_x':1.,'offset_y':2.}
        class Panel(dict):
            def __setitem__(self,key,value):
                super().__setitem__(key,value)
                if key=='dragged':capture['offset_x']=10.
                if key=='x':capture['offset_y']=20.
        panel=Panel()
        move_panel(panel,NS(x=lambda:50.,y=lambda:60.),lambda:capture,mark_dragged=True)
        self.assertEqual(panel,{'dragged':True,'x':40.,'y':40.})

    def test_failed_y_write_retains_x_and_propagates(self):
        class Panel(dict):
            def __setitem__(self,key,value):
                if key=='y':raise RuntimeError('write failed')
                super().__setitem__(key,value)
        panel=Panel()
        with self.assertRaisesRegex(RuntimeError,'write failed'):
            move_panel(panel,NS(x=lambda:50.,y=lambda:60.),lambda:{'offset_x':1.,'offset_y':2.})
        self.assertEqual(panel,{'x':49.})

    def test_capture_loss_between_axes_is_not_hidden_or_retried(self):
        captures=iter(({'offset_x':1.},None))
        panel={}
        with self.assertRaises(TypeError):
            move_panel(panel,NS(x=lambda:50.,y=lambda:60.),lambda:next(captures))
        self.assertEqual(panel,{'x':49.})

if __name__=='__main__':unittest.main()
