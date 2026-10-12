"""SIM-only truth: unavailable features cannot masquerade as active control."""
import math,os,unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QEvent,QPointF,QRectF
from PySide6.QtGui import QKeyEvent
from rm_client.model import build_default_registry
from rm_client.sim_operator_runtime import OperatorRuntime
from rm_client.sim_operator_transport import SimOperatorTransport
from rm_client.sim_operator_features import UNAVAILABLE
from rm_client.ui.sim_operator_window import OperatorWindow
from tests.test_sim_operator import session,telemetry

class SimulationTruthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.p=patch.object(SimOperatorTransport,'start');self.p.start()
        self.r=OperatorRuntime();self.w=OperatorWindow(self.r);self.w.timer.stop()
        self.r.transport.install_session(session())
        self.r.transport.install_telemetry(telemetry(),self.r.transport.session)
        self.r.apply_state(self.w.state)
    def tearDown(self):self.w.close();self.r._tmp.cleanup();self.p.stop()
    def test_every_unavailable_module_rejects_toggle_and_direct_enable(self):
        for name in UNAVAILABLE:
            with self.subTest(name=name):
                m=self.w.registry.get(name);self.assertIsNotNone(m)
                self.w.registry.toggle_module(m,source='clickgui');self.assertFalse(m.enabled)
                m.enabled=True;self.assertFalse(m.enabled)
                m.set_enabled(True);self.assertFalse(m.enabled)
                self.assertIn('SIM unavailable',m.description)
    def test_original_registry_behavior_is_not_changed(self):
        r=build_default_registry(discover_devices=False);spin=r.get('SpinMode')
        self.assertTrue(spin.enabled);r.toggle_module(spin);self.assertFalse(spin.enabled)
        spin.enabled=True;self.assertTrue(spin.enabled)
        self.assertNotIn('SIM unavailable',spin.description)
    def test_jello_labels_state_unavailable_without_renaming_modules(self):
        m=self.w.registry.get('SpinMode');self.assertEqual(m.name,'SpinMode')
        self.assertEqual(self.w.module_display_label(m,'小陀螺'),'[SIM N/A] 小陀螺')
        m=self.w.registry.get('TacticalMap');self.assertEqual(self.w.module_display_label(m,'地图'),'地图')
    def test_fov_settings_are_not_silently_editable_as_active_aim(self):
        m=self.w.registry.get('ArmorAutoAim');setting=m.get_setting('Circle Size')
        old=self.r.feature_policy.locked_values[id(setting)];setting.value=480
        self.w.after_setting_changed(setting)
        self.assertEqual(setting.value,old);self.assertFalse(m.enabled)
        self.assertIn('SIM unavailable',self.w.toasts[-1]['message'])
    def test_role_and_team_settings_cannot_claim_a_different_robot(self):
        setting=self.w.registry.get('RobotProfile').get_setting('Role');setting.value='Hero';self.w.after_setting_changed(setting)
        self.assertEqual(setting.value,'Infantry1');self.assertEqual(self.w.state.robot_id,3)
        setting=self.w.registry.get('AllianceProfile').get_setting('Side');setting.value='Blue';self.w.after_setting_changed(setting)
        self.assertEqual(setting.value,'Red')
    def test_hardware_settings_reject_but_drive_keybindings_work(self):
        m=self.w.registry.get('RobotLink');setting=m.get_setting('DT7 Real TX');old=setting.value;setting.value=True;self.w.after_setting_changed(setting)
        self.assertEqual(setting.value,old)
        setting=m.get_setting('Bind W');setting.value='Up';self.w.after_setting_changed(setting)
        self.assertIn('W',self.w.control.resolve_slot('UP'))
    def test_unknown_default_status_flags_are_never_active(self):
        for name in ['friction_wheel','lock_state','anti_rotation','target_locked','small_buff','large_buff','jump_ready','pte_ready','wheel_online','les_online','can_remote_heal','can_remote_ammo']:
            self.assertFalse(getattr(self.w.state,name),name)
        for name in ['friction_wheel','lock_state','anti_rotation','capacitor_percent','current_buffer_energy']:
            self.assertIn(name,self.w.state.mujoco_unknown_fields)
        self.assertFalse(self.w.rm_indicator_visible('FRIC'))
        self.assertFalse(self.w.status_card_visible('CAP'))
    def test_world_and_relative_gimbal_angles_are_consistent(self):
        t=telemetry();t['own']['chassis_yaw']=math.pi/2;t['own']['gimbal_yaw']=.2
        self.r.transport.install_telemetry(t,self.r.transport.session);self.r.apply_state(self.w.state)
        self.assertAlmostEqual(self.w.state.gimbal_yaw_deg,90+math.degrees(.2))
        self.assertAlmostEqual(self.w.state.relative_yaw_deg,math.degrees(.2))
        self.assertAlmostEqual(self.w.state.gimbal_yaw_deg-self.w.state.chassis_yaw_deg,math.degrees(.2))
        self.assertEqual(self.w.chassis_follow_mode(),'SIM BODY')
    def test_real_spin_hotkey_cannot_claim_enabled(self):
        self.w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_X,Qt.KeyboardModifier.NoModifier))
        self.assertFalse(self.w.registry.get('SpinMode').enabled)
        self.assertIn('SIM unavailable',self.w.toasts[-1]['message'])
    def test_original_click_toggle_cannot_claim_enabled(self):
        self.w.dispatch('module_toggle','ArmorAutoAim',QPointF(),1,QRectF(0,0,100,20))
        self.assertFalse(self.w.registry.get('ArmorAutoAim').enabled)
        self.assertIn('SIM unavailable',self.w.toasts[-1]['message'])
    def test_original_hotbar_slot_cannot_enable_unavailable_feature(self):
        self.w.activate_minecraft_function_slot(4)
        self.assertFalse(self.w.registry.get('SpinMode').enabled)
    def test_sim_reported_health_heat_and_ammo_are_not_removed(self):
        self.assertEqual(self.w.state.current_health,200);self.assertEqual(self.w.state.current_heat,10);self.assertEqual(self.w.state.remaining_ammo,20)
        self.assertEqual(self.r.match_bar().countdown,59)

if __name__=='__main__':unittest.main()
