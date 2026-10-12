"""Synthetic driving-frame requests: no ground truth or client-side transform."""
import copy,math,unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF,QRectF
from rm_client.model import build_default_registry
from rm_client.sim_drive_frames import *
from rm_client.sim_operator_modes import SPIN_DEFAULT,AIM_DEFAULT,spin_request,supported_modes
from rm_client.sim_operator_transport import SimOperatorTransport
from rm_client.sim_operator_runtime import OperatorRuntime
from rm_client.ui.sim_operator_window import OperatorWindow
from tests.test_sim_operator import telemetry
from tests.test_sim_operator_modes import mode_session,mode_feedback

def drive_session():
    s=mode_session(False)
    s['capabilities'].update(chassis_drive_frame_version=1,chassis_drive_modes=list(DRIVE_MODES))
    return s

def drive_feedback(frame='free',active=False,seq=1):
    mode=dict(SPIN_DEFAULT,chassis_follow=frame)
    f=mode_feedback(mode,seq)
    f['effective']['chassis_follow']=frame if active else 'free'
    f['drive']=dict(input_kind='drive_axes' if active else 'legacy_chassis',
                   mode=frame if active else 'free',reference_yaw_rad=math.pi/2 if active else 0.,
                   world_velocity_target_mps=[0.,1.] if active else [0.,0.],
                   measured_world_velocity_mps=[.01,.5],applied_world_force_n=[0.,20.] if active else [0.,0.],
                   measurement_valid=True,measurement_sim_time=1.,input_revision=1,saturated=False,active=active,reason='driving' if active else 'released')
    return f

class DriveContractTests(unittest.TestCase):
    def test_capability_preserves_legacy_spin_and_requires_complete_unique_modes(self):
        s=drive_session();self.assertTrue(supports_drive_frames(s));self.assertTrue(supported_modes(s)[0])
        s['capabilities']['chassis_drive_modes'].reverse();self.assertTrue(supports_drive_frames(s))
        for modes in [['free'],['free','field','field'],['free',{},'gimbal'],None]:
            bad=drive_session();bad['capabilities']['chassis_drive_modes']=modes
            self.assertFalse(supports_drive_frames(bad))
        bad=drive_session();bad['capabilities']['chassis_drive_frame_version']=True
        self.assertFalse(supports_drive_frames(bad))
    def test_nonfree_request_requires_capability(self):
        for frame in ('gimbal','field'):
            m=dict(SPIN_DEFAULT,chassis_follow=frame)
            with self.assertRaises(ValueError):spin_request(m)
            self.assertEqual(spin_request(m,drive_frames=True)['chassis_follow'],frame)
    def test_finite_drive_feedback_and_inactive_coasting(self):
        t=telemetry(operator_modes=drive_feedback())
        validate_drive_feedback(t,drive_session())
        t['operator_modes']['drive']['measurement_valid']=False
        validate_drive_feedback(t,drive_session())
        for field,value in [('reference_yaw_rad',float('nan')),('measured_world_velocity_mps',[0,float('inf')]),('world_velocity_target_mps',[0,1]),('mode','gimbal'),('measurement_valid',1)]:
            bad=copy.deepcopy(t);bad['operator_modes']['drive'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_drive_feedback(bad,drive_session())
    def test_active_feedback_cannot_claim_invalid_measurement_or_other_mode(self):
        t=telemetry(operator_modes=drive_feedback('field',True),control=dict(safety_revision=1))
        validate_drive_feedback(t,drive_session())
        t['operator_modes']['drive']['measurement_valid']=False
        with self.assertRaises(ValueError):validate_drive_feedback(t,drive_session())
        t['operator_modes']['drive']['measurement_valid']=True
        t['operator_modes']['effective']['chassis_follow']='gimbal'
        with self.assertRaises(ValueError):validate_drive_feedback(t,drive_session())
    def test_inactive_retains_timestamped_old_force_without_claiming_live_target(self):
        t=telemetry(operator_modes=drive_feedback())
        t['operator_modes']['drive']['applied_world_force_n']=[12.,-3.]
        validate_drive_feedback(t,drive_session())
        del t['operator_modes']['drive']['measurement_sim_time']
        with self.assertRaises(ValueError):validate_drive_feedback(t,drive_session())
    def test_revision_mismatch_cannot_restore_active_drive(self):
        t=telemetry(operator_modes=drive_feedback('gimbal',True),control=dict(safety_revision=2))
        with self.assertRaises(ValueError):validate_drive_feedback(t,drive_session())
    def test_huge_and_boolean_feedback_is_rejected(self):
        for value in [True,10**1000,None]:
            t=telemetry(operator_modes=drive_feedback('field',True),control=dict(safety_revision=1))
            t['operator_modes']['drive']['reference_yaw_rad']=value
            with self.assertRaises(ValueError):validate_drive_feedback(t,drive_session())

class DriveTransportTests(unittest.TestCase):
    def setUp(self):
        self.t=SimOperatorTransport(clock=lambda:100.);self.t.install_session(drive_session());self.t._neutral_pending=False
    def test_new_axes_do_not_reinterpret_legacy_wire_fields(self):
        self.t.heartbeat(True)
        self.t.submit_modes(dict(SPIN_DEFAULT,chassis_follow='field'),None)
        self.t.submit_input(vx_mps=1,vy_mps=1,yaw_rate_rps=0,pitch_rate_rps=0,fire=False)
        p=self.t._control_payload()
        self.assertEqual((p['vx_mps'],p['vy_mps']),(0.,0.))
        self.assertAlmostEqual(p['drive_axes']['forward_mps'],1/math.sqrt(2))
        self.assertAlmostEqual(p['drive_axes']['left_mps'],1/math.sqrt(2))
        self.assertEqual(p['mode_request']['chassis_follow'],'field')
    def test_neutral_keeps_nonfree_release_axes_zero(self):
        self.t.heartbeat(True);self.t.submit_modes(dict(SPIN_DEFAULT,chassis_follow='gimbal'),None)
        self.t.submit_input(vx_mps=1,vy_mps=0,yaw_rate_rps=0,pitch_rate_rps=0,fire=True)
        self.t.neutral();p=self.t._control_payload()
        self.assertEqual(p['drive_axes'],dict(forward_mps=0.,left_mps=0.))
        self.assertFalse(p['focus']);self.assertFalse(p['fire'])
        self.assertEqual(p['mode_request']['chassis_follow'],'gimbal')
    def test_new_frame_capability_requires_neutral_ack_even_without_mouse_capability(self):
        self.t._neutral_pending=True
        self.t.heartbeat(True);self.t.submit_modes(dict(SPIN_DEFAULT,chassis_follow='field'),None)
        self.t.submit_input(vx_mps=1,vy_mps=0,yaw_rate_rps=0,pitch_rate_rps=0,fire=True)
        p=self.t._control_payload()
        self.assertFalse(p['focus']);self.assertFalse(p['fire'])
        self.assertEqual(p['drive_axes'],dict(forward_mps=0.,left_mps=0.))
    def test_legacy_session_stays_body_axes_without_extension(self):
        self.t.install_session(mode_session(False));self.t.heartbeat(True)
        self.t.submit_input(vx_mps=1,vy_mps=0,yaw_rate_rps=0,pitch_rate_rps=0,fire=False)
        p=self.t._control_payload();self.assertEqual(p['vx_mps'],1)
        self.assertNotIn('drive_axes',p)
    def test_invalid_axes_never_serialize(self):
        with self.assertRaises(ValueError):self.t.submit_input(vx_mps=float('nan'),vy_mps=0,yaw_rate_rps=0,pitch_rate_rps=0,fire=False)

class DriveWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.p=patch.object(SimOperatorTransport,'start');self.p.start()
        self.t=SimOperatorTransport(clock=lambda:100.);self.r=OperatorRuntime(transport=self.t);self.w=OperatorWindow(self.r);self.w.timer.stop();self.w.isActiveWindow=lambda:True;self.w.registry.get('AudioFeedback').enabled=False
        self.t.install_session(drive_session());self.t.install_telemetry(telemetry(operator_modes=drive_feedback()),self.t.session)
        self.t.video=np.zeros((240,320,3),dtype=np.uint8);self.t.video_at=100.
        self.r.heartbeat(self.w)
    def tearDown(self):self.w.close();self.r._tmp.cleanup();self.p.stop()
    def test_only_reference_setting_unlocked_and_original_registry_unchanged(self):
        m=self.w.registry.get('ChassisAssist')
        self.assertFalse(m.enabled)
        self.assertFalse(self.w.setting_is_unavailable(m.get_setting('Follow')))
        for name in ('Power Limit','Buffer Protect'):self.assertTrue(self.w.setting_is_unavailable(m.get_setting(name)))
        normal=build_default_registry(discover_devices=False).get('ChassisAssist')
        self.assertEqual(normal.get_setting('Follow').value,'Gimbal');self.assertNotIn('SIM driving',normal.description)
        label=self.w.setting_display_label(m.get_setting('Follow'),'Follow')
        self.assertIn('SIM',label);self.assertNotIn('跟随',label);self.assertNotEqual(label,'Follow')
    def test_original_module_and_dropdown_clear_inputs_before_new_frame(self):
        m=self.w.registry.get('ChassisAssist')
        self.w.registry.toggle_module(m,source='clickgui');self.r.heartbeat(self.w)
        self.assertEqual(self.r.modes.mode['chassis_follow'],'gimbal');self.assertFalse(self.r.armed)
        self.assertTrue(self.r.arm());self.r.heartbeat(self.w);self.w.control.key_state['W']=True
        setting=m.get_setting('Follow')
        self.w.dispatch('jello_mode_select',(setting,'Field'),QPointF(),1,QRectF(0,0,100,20))
        self.assertFalse(self.r.armed);self.assertFalse(any(self.w.control.key_state.values()))
        self.r.heartbeat(self.w);self.assertEqual(self.r.modes.mode['chassis_follow'],'field')
        self.assertIsNone(self.r.modes.feedback()['drive'])
    def test_disabled_module_is_body_reference_even_if_setting_is_field(self):
        m=self.w.registry.get('ChassisAssist');m.get_setting('Follow').value='Field'
        self.r.modes.sync(self.w);self.assertEqual(self.r.modes.mode['chassis_follow'],'free')
    def test_vehicle_selection_clears_old_reference_enable(self):
        m=self.w.registry.get('ChassisAssist');m.enabled=True
        with patch.object(self.t,'select_robot'):
            self.r.select_robot(self.w,'blue_hero')
        self.assertFalse(m.enabled);self.assertFalse(self.r.armed)
    def test_actual_drive_confirmation_is_lease_seq_mode_and_measurement_bound(self):
        m=self.w.registry.get('ChassisAssist');m.enabled=True;m.get_setting('Follow').value='Field'
        self.r.heartbeat(self.w);self.assertTrue(self.r.arm());self.r.heartbeat(self.w)
        self.t._seq=20;self.t._lease_min_seq=10;self.t._mode_min_seq=10
        def install(seq):
            f=drive_feedback('field',True,seq)
            f['requested']=dict(self.r.modes.mode)
            self.t.install_telemetry(telemetry(operator_modes=f,control=dict(safety_revision=1)),self.t.session)
        install(9);self.assertIsNone(self.r.modes.feedback()['drive'])
        install(20);self.assertEqual(self.r.modes.feedback()['drive'],'field')
        self.assertEqual(self.w.chassis_follow_mode(),'SIM FIELD')
        self.r.disarm();self.assertIsNone(self.r.modes.feedback()['drive'])
        self.assertEqual(self.w.chassis_follow_mode(),'SIM REF ?')

if __name__=='__main__':unittest.main()
