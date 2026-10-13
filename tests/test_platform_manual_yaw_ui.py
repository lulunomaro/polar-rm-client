"""Original Qt event routing with inert backend/SDK, no painting or physics."""
import os,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QEvent
from PySide6.QtGui import QKeyEvent,QFocusEvent
from test_platform_adapters import OriginalUiTests

class RoutedUiTests(OriginalUiTests):
    def key(self,key,pressed=True,repeat=False):
        e=QKeyEvent(QEvent.Type.KeyPress if pressed else QEvent.Type.KeyRelease,key,Qt.KeyboardModifier.NoModifier,'',repeat)
        (self.w.keyPressEvent if pressed else self.w.keyReleaseEvent)(e)
    def arm_manual(self):
        self.key(Qt.Key.Key_F2);self.t.process_jobs();self.key(Qt.Key.Key_F2)
        self.assertTrue(self.r.armed);self.assertTrue(self.t._focus)
    def routed_hold(self):
        self.assertTrue(self.w.control.set_bind('E','Right'))
        self.arm_manual();self.key(Qt.Key.Key_Right)
        self.assertEqual(self.t._intent['wz'],1.)
    def test_rebound_physical_keys_keep_protocol_slot_sign(self):
        self.assertTrue(self.w.control.set_bind('Q','Left'));self.assertTrue(self.w.control.set_bind('E','Right'))
        self.arm_manual();self.key(Qt.Key.Key_Right);self.assertEqual(self.t._intent['wz'],1.)
        self.key(Qt.Key.Key_Left);self.assertEqual(self.t._intent['wz'],0.)
        self.key(Qt.Key.Key_Right,False);self.assertEqual(self.t._intent['wz'],-1.)
        self.key(Qt.Key.Key_Left,False);self.assertEqual(self.t._intent['wz'],0.)
    def test_original_q_wheel_keeps_priority_and_clears_held_yaw(self):
        self.w.registry.get('QuickActionWheel').enabled=True;self.w.registry.get('QuickActionWheel').keybind='Q'
        self.routed_hold();self.key(Qt.Key.Key_Q)
        self.assertTrue(self.w.quick_action_wheel.active);self.assertFalse(self.t._focus)
        self.assertFalse(self.r.armed);self.assertEqual(self.t._intent['wz'],0.)
        self.assertFalse(any(self.w.control.key_state.values()))
        self.key(Qt.Key.Key_Right,False);self.assertFalse(self.w.control.key_state['E'])
    def test_original_e_inventory_keeps_priority_and_clears_held_yaw(self):
        self.w.registry.get('MinecraftInventory').enabled=True;self.w.minecraft_hud_active=lambda:True
        self.routed_hold();self.key(Qt.Key.Key_E)
        self.assertTrue(self.w.student_ui.visible);self.assertEqual(self.w.student_ui.panel,'inventory')
        self.assertFalse(self.t._focus);self.assertFalse(self.r.armed);self.assertEqual(self.t._intent['wz'],0.)
        self.key(Qt.Key.Key_Right,False);self.assertFalse(self.w.control.key_state['E'])
    def test_focus_out_clears_yaw_and_no_key_replay(self):
        self.routed_hold();self.w.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        self.assertFalse(self.t._focus);self.assertEqual(self.t._intent['wz'],0.)
        self.assertFalse(any(self.w.control.key_state.values()))
    def test_x_stays_unavailable_and_does_not_claim_spin_active(self):
        self.arm_manual();self.key(Qt.Key.Key_X)
        self.assertFalse(self.w.registry.get('SpinMode').enabled)
        self.assertFalse(self.r.modes.feedback()['spin'])
    def test_f2_inside_inventory_cannot_authorize_motion(self):
        self.w.registry.get('MinecraftInventory').enabled=True;self.w.minecraft_hud_active=lambda:True
        self.key(Qt.Key.Key_E);self.key(Qt.Key.Key_F2)
        self.assertFalse(self.r.armed);self.assertFalse(self.t._focus)

def load_tests(loader,tests,pattern):
    return unittest.TestSuite(RoutedUiTests(n) for n,v in RoutedUiTests.__dict__.items() if n.startswith('test_') and callable(v))
if __name__=='__main__':unittest.main()
