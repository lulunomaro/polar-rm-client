"""Synthetic SIM mode/range contracts; no private source fixtures or hardware."""
import copy,math,time,unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QEvent,Qt,QPointF
from PySide6.QtGui import QKeyEvent,QMouseEvent
from PySide6.QtWidgets import QApplication
from rm_client.sim_operator_modes import *
from rm_client.sim_operator_transport import SimOperatorTransport
from rm_client.sim_operator_runtime import OperatorRuntime
from rm_client.ui.sim_operator_window import OperatorWindow
from tests.test_sim_operator import session,telemetry

def mode_session(aim=True):
    return session(capabilities=dict(operator_modes_version=1,spin_mode=True,chassis_follow_modes=['free'],aim_request_version=1,automatic_targeting=aim,autoaim_robots=['red_infantry'],autoaim_source_identity='synthetic-source-v1',autoaim_fire_authority='manual_only'))
def mode_feedback(mode,seq=1):
    return dict(simulation_only=True,requested=mode,request_seq=seq,effective_seq=seq,effective=dict(spin_enabled=mode['spin_enabled'],spin_speed_dps=mode['spin_speed_dps'] if mode['spin_enabled'] else 0,target_yaw_rate_rps=2.,chassis_follow='free',direction_resolved='ccw' if mode['spin_enabled'] else None),measured=dict(chassis_yaw_rate_rps=1.8,yaw_torque=2.),reason='spin_active')
def aim_feedback(aim):
    return dict(simulation_only=True,algorithm='synthetic-source-v1',input_revision=1,requested=aim['enabled'],held=aim['hold'],available=True,active=True,selected=True,in_fov=True,fov_applied=True,authority='source_autoaim',fire_authority='manual_only',source_frame_seq=2,source_sim_time=1.,fov={k:v for k,v in aim.items() if k not in ('enabled','hold')},reason='source_tracking')

class GeometryTests(unittest.TestCase):
    def test_letterbox_circle_uses_video_fit_not_window(self):
        x,y=circle_request(160,1,1360,900,320,240)
        self.assertAlmostEqual(x,160/1200);self.assertAlmostEqual(y,160/900)
    def test_native_display_resolution_agree(self):
        self.assertEqual(circle_request(160,1,1360,900,320,240),circle_request(160,1,1360,900,1440,1080))
    def test_hud_scale_applied_once_and_wide_camera(self):
        x,y=circle_request(80,2,1920,1080,1920,1080)
        self.assertAlmostEqual(x,160/1920);self.assertAlmostEqual(y,160/1080)
    def test_bad_request_numeric_and_extra_fields_fail(self):
        for val in (True,None,float('nan'),10**1000,-1,0):
            with self.subTest(val_type=type(val).__name__),self.assertRaises(ValueError):circle_request(val,1,100,100,320,240)
        for payload in (None,dict(SPIN_DEFAULT,power_guard=True),dict(SPIN_DEFAULT,chassis_follow='gimbal'),dict(SPIN_DEFAULT,spin_speed_dps=721)):
            with self.assertRaises(ValueError):spin_request(payload)
    def test_unsupported_robot_cannot_enable_aim(self):
        self.assertEqual(supported_modes(mode_session()),(True,True))
        self.assertEqual(supported_modes(dict(mode_session(),robot_id='blue_hero')),(True,False))
    def test_source_display_metadata_never_assumes_detector_index(self):
        h={'x-source-camera':'true','x-native-frame-width':'1440','x-native-frame-height':'1080','x-source-capture-seq':'17'}
        self.assertEqual(source_camera_metadata(h,320,240,mode_session()),(17,(1440,1080)))
        del h['x-source-capture-seq'];self.assertEqual(source_camera_metadata(h,320,240,mode_session()),(None,(1440,1080)))
        for bad in (dict(h,**{'x-source-camera':'false'}),dict(h,**{'x-native-frame-width':'1000'}),dict(h,**{'x-source-capture-seq':'-1'})):
            with self.assertRaises(ValueError):source_camera_metadata(bad,320,240,mode_session())
        self.assertEqual(source_camera_metadata({},320,240,session()),(None,None))

    def test_unqualified_stale_or_malformed_active_rejected(self):
        a=dict(AIM_DEFAULT,enabled=True,hold=True);d=telemetry(autoaim=aim_feedback(a),control={'safety_revision':1})
        validate_feedback(d,mode_session())
        for bad in (dict(d,autoaim=dict(d['autoaim'],algorithm='unavailable')),dict(d,control={'safety_revision':2}),dict(d,sim_time=2.),dict(d,autoaim=dict(d['autoaim'],available=False)),dict(d,autoaim=dict(d['autoaim'],source_frame_seq=True))):
            with self.assertRaises(ValueError):validate_feedback(bad,mode_session())
        with self.assertRaises(ValueError):validate_feedback(d,session())

class ModeTransportTests(unittest.TestCase):
    def setUp(self):
        self.now=[100.];self.t=SimOperatorTransport(clock=lambda:self.now[0]);self.t.install_session(mode_session())
    def test_published_contract_examples_are_synthetic_and_valid(self):
        import json
        from pathlib import Path
        root=Path(__file__).parent/'fixtures'/'sim_operator'
        s=json.loads((root/'mode-session.fixture.json').read_text());d=json.loads((root/'mode-telemetry.fixture.json').read_text())
        self.assertEqual(s['session_id'],'synthetic-contract-fixture')
        self.t.install_session(s);self.assertTrue(self.t.install_telemetry(d,s))

    def test_neutral_clears_hold_spin_fire_and_lease(self):
        self.t.heartbeat(True);self.t.submit_modes(dict(SPIN_DEFAULT,spin_enabled=True),dict(AIM_DEFAULT,enabled=True,hold=True))
        a=self.t._control_payload();self.assertTrue(a['mode_request']['spin_enabled']);self.assertTrue(a['aim_request']['hold'])
        self.t.neutral('blur');b=self.t._control_payload()
        self.assertFalse(b['mode_request']['spin_enabled']);self.assertFalse(b['aim_request']['enabled']);self.assertFalse(b['aim_request']['hold']);self.assertFalse(b['fire'])
    def test_watchdog_and_session_reset_never_replay_held_aim(self):
        self.t.heartbeat(True);self.t.submit_modes(None,dict(AIM_DEFAULT,enabled=True,hold=True));self.now[0]+=.2
        self.assertFalse(self.t._control_payload()['aim_request']['hold'])
        self.t._clear_session('new');self.t.install_session(mode_session());self.t.heartbeat(True)
        self.assertNotIn('aim_request',self.t._control_payload())
    def test_right_release_latest_value_replaces_old_hold(self):
        self.t.heartbeat(True);self.t.submit_modes(None,dict(AIM_DEFAULT,enabled=True,hold=True));self.t.submit_modes(None,dict(AIM_DEFAULT,enabled=True,hold=False))
        self.assertFalse(self.t._control_payload()['aim_request']['hold'])

class ModeWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.p=patch.object(SimOperatorTransport,'start');self.p.start();self.r=OperatorRuntime();self.w=OperatorWindow(self.r);self.w.timer.stop();self.w.resize(1360,900);self.w.isActiveWindow=lambda:True
        self.t=self.r.transport;self.t.install_session(mode_session());self.t.install_telemetry(telemetry(),self.t.session)
        self.t.video=np.zeros((240,320,3),np.uint8);self.t.video_at=self.t.clock();self.r.heartbeat(self.w)
    def tearDown(self):self.w.close();self.r._tmp.cleanup();self.p.stop()
    def key(self,k):self.w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress,k,Qt.KeyboardModifier.NoModifier))
    def arm(self):self.key(Qt.Key.Key_F2);self.r.heartbeat(self.w);self.assertTrue(self.r.armed)
    def test_x_uses_existing_modlist_then_rearm_and_ack(self):
        self.arm();self.key(Qt.Key.Key_X);self.assertFalse(self.r.armed);self.assertTrue(self.w.registry.get('SpinMode').enabled)
        self.r.heartbeat(self.w);self.arm();self.w.control.tick(self.w.state,self.w.registry)
        payload=self.t._control_payload();self.assertTrue(payload['mode_request']['spin_enabled']);self.assertFalse(self.w.effective_spin_enabled())
        self.t.install_telemetry(telemetry(operator_modes=mode_feedback(payload['mode_request'],payload['seq'])),self.t.session)
        self.assertTrue(self.w.effective_spin_enabled())
        self.r.disarm('blur');self.assertFalse(self.w.effective_spin_enabled())
    def test_unimplemented_settings_remain_locked(self):
        for module_name,name in [('SpinMode','Power Guard'),('ArmorAutoAim','Smooth'),('ArmorAutoAim','Priority')]:
            m=self.w.registry.get(module_name);s=m.get_setting(name);old=s.value;s.value=False if type(old)is bool else 'Changed';self.w.after_setting_changed(s);self.assertEqual(s.value,old)
        self.assertIn('SIM REQUEST',self.w.module_display_label(self.w.registry.get('SpinMode'),'Spin'))
        self.assertTrue(self.w.setting_is_unavailable(self.w.registry.get('SpinMode').get_setting('Power Guard')))
        self.assertFalse(self.w.setting_is_unavailable(self.w.registry.get('SpinMode').get_setting('Spin Speed')))
    def test_range_edits_disarm_immediately_and_circle_maps_video(self):
        self.arm();s=self.w.registry.get('ArmorAutoAim').get_setting('Circle Size');s.value=32;self.w.after_setting_changed(s);self.assertFalse(self.r.armed)
        self.r.heartbeat(self.w);self.assertAlmostEqual(self.r.modes.aim['circle_radius_x_norm'],32*self.w.get_hud_scale(1360,900)/1200)
    def test_real_right_button_path_and_immediate_release(self):
        self.w.registry.toggle_module('ArmorAutoAim',source='clickgui');self.r.heartbeat(self.w);self.arm()
        def mouse(down):
            ev=QMouseEvent(QEvent.Type.MouseButtonPress if down else QEvent.Type.MouseButtonRelease,QPointF(680,450),QPointF(680,450),Qt.MouseButton.RightButton,Qt.MouseButton.RightButton if down else Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
            (self.w.mousePressEvent if down else self.w.mouseReleaseEvent)(ev);self.w.control.tick(self.w.state,self.w.registry)
        mouse(True);a=self.t._control_payload();self.assertTrue(a['aim_request']['hold']);self.assertFalse(a['fire'])
        self.t.install_telemetry(telemetry(autoaim=aim_feedback(a['aim_request']),control={'last_seq':a['seq'],'safety_revision':1}),self.t.session);self.assertTrue(self.r.modes.feedback()['aim'])
        mouse(False);self.assertFalse(self.r.modes.feedback()['aim']);self.assertFalse(self.t._control_payload()['aim_request']['hold'])
    def test_same_range_release_repress_cannot_revive_old_aim(self):
        self.w.registry.toggle_module('ArmorAutoAim');self.r.heartbeat(self.w);self.arm()
        self.w.control.right_button_down=True;self.w.control.tick(self.w.state,self.w.registry)
        p=self.t._control_payload();self.t.install_telemetry(telemetry(autoaim=aim_feedback(p['aim_request']),control={'last_seq':p['seq'],'safety_revision':1}),self.t.session)
        self.assertTrue(self.r.modes.feedback()['aim'])
        self.w.control.right_button_down=False;self.w.control.tick(self.w.state,self.w.registry)
        self.assertFalse(self.r.modes.feedback()['aim'])
        self.w.control.right_button_down=True;self.w.control.tick(self.w.state,self.w.registry)
        self.assertFalse(self.r.modes.feedback()['aim'])
        fresh=self.t._control_payload();self.t.install_telemetry(telemetry(autoaim=aim_feedback(fresh['aim_request']),control={'last_seq':fresh['seq'],'safety_revision':1}),self.t.session)
        self.assertTrue(self.r.modes.feedback()['aim'])

    def test_old_feedback_does_not_confirm_new_range(self):
        self.w.registry.toggle_module('ArmorAutoAim');self.r.heartbeat(self.w);self.arm();self.w.control.right_button_down=True;self.w.control.tick(self.w.state,self.w.registry)
        old=self.t._control_payload()['aim_request'];self.t.install_telemetry(telemetry(autoaim=aim_feedback(old),control={'last_seq':self.t._seq,'safety_revision':1}),self.t.session)
        s=self.w.registry.get('ArmorAutoAim').get_setting('Fov');s.value=10;self.w.after_setting_changed(s);self.r.heartbeat(self.w);self.assertFalse(self.r.modes.feedback()['aim'])
    def test_old_ack_cannot_confirm_rearmed_same_spin_request(self):
        self.key(Qt.Key.Key_X);self.r.heartbeat(self.w);self.arm();self.w.control.tick(self.w.state,self.w.registry)
        p=self.t._control_payload();self.t.install_telemetry(telemetry(operator_modes=mode_feedback(p['mode_request'],p['seq'])),self.t.session)
        self.assertTrue(self.w.effective_spin_enabled());self.r.disarm('blur');self.arm()
        self.assertFalse(self.w.effective_spin_enabled())

    def test_capability_loss_disables_modules(self):
        self.w.registry.toggle_module('SpinMode');self.t.install_session(session(epoch=1));self.r.heartbeat(self.w)
        self.assertFalse(self.w.registry.get('SpinMode').enabled);self.assertFalse(self.r.armed)

if __name__=='__main__':unittest.main()
