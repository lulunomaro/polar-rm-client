"""Slider press applies value before capture, then notifies the original target."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.mixins.input_dispatch_mixin import _on_action_setting_slider


class Host:
    def __init__(self, fail_at=None, cancel=False):
        self.calls=[];self.fail_at=fail_at;self.cancel=cancel
        self.pointer_interaction=NS(drag=None)
        self.ratio=None
        self.setting=NS(set_from_ratio=self.apply)
    def record(self,name):
        self.calls.append(name)
        if self.fail_at==len(self.calls):raise RuntimeError(name)
    def apply(self,ratio):
        self.record('ratio')
        self.ratio=ratio
        # Value mutation must precede publishing the new capture.
        assert self.pointer_interaction.drag is None
    def after_setting_changed(self,setting):
        self.record('changed')
        assert setting is self.setting
        assert self.pointer_interaction.drag['setting'] is setting
        if self.cancel:self.pointer_interaction.drag=None


class SliderPressContractTests(unittest.TestCase):
    def test_value_capture_and_notification_order_is_preserved_for_every_button(self):
        for button in (1,2,3):
            host=Host()
            rect=NS(left=lambda:1.,width=lambda:20.)
            _on_action_setting_slider(host,host.setting,NS(x=lambda:11.),button,rect)
            self.assertEqual(host.calls,['ratio','changed'])
            self.assertEqual(host.ratio,.5)
            self.assertEqual(host.pointer_interaction.drag,{'type':'slider','setting':host.setting,'rect':rect})

    def test_failures_keep_exact_applied_prefix(self):
        for failure in (1,2):
            host=Host(fail_at=failure)
            with self.assertRaises(RuntimeError):
                _on_action_setting_slider(host,host.setting,NS(x=lambda:11.),1,NS(left=lambda:1.,width=lambda:20.))
            self.assertEqual(host.ratio,None if failure==1 else .5)
            self.assertEqual(host.pointer_interaction.drag is None,failure==1)
            self.assertEqual(host.calls,['ratio','changed'][:failure])

    def test_notification_cancellation_is_not_overwritten(self):
        host=Host(cancel=True)
        _on_action_setting_slider(host,host.setting,NS(x=lambda:11.),1,NS(left=lambda:1.,width=lambda:20.))
        self.assertIsNone(host.pointer_interaction.drag)
        self.assertEqual(host.ratio,.5)

if __name__=='__main__':unittest.main()
