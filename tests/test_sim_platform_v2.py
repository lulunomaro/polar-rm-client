"""Synthetic v2 role/lease/watch tests using the actual original Qt window."""
import base64
import copy
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF,QRectF
from rm_client.sim_platform_transport import PlatformTransport,jpeg_dimensions
from rm_client.sim_platform_runtime import PlatformRuntime
from rm_client.ui.sim_platform_window import PlatformWindow
from rm_client.sim_robot_selection import SELECTION_SETTING
from rm_client.model import build_default_registry

class Client:
    uncertain=False;session_id='synthetic-client'
    def __init__(self):self.calls=[];self.epoch=0;self.lease_epoch=0
    def open(self,role):return dict(schema='rm.sim.platform.v2',session_id=self.session_id,role=role)
    def watch(self,rid):self.calls.append(('watch',rid));self.epoch+=1;return dict(robot_id=rid,watch_epoch=self.epoch)
    def claim(self,rid,channels):self.calls.append(('claim',rid));self.lease_epoch+=1;return dict(robot_id=rid,lease_epochs={c:self.lease_epoch for c in channels})
    def release(self,rid,channels):self.calls.append(('release',rid));return {'released':True}
    def renew(self,rid,channels):self.calls.append(('renew',rid));return {}
    def control(self,rid,ch,value,**kw):self.calls.append(('control',rid,ch,copy.deepcopy(value)));return {'accepted':True}
    def keepalive(self):self.calls.append(('keepalive',));return {}
    def close(self):self.calls.append(('close',))

def state(*,complete=True,episode=0,stamp=1.,owner=None,epoch=1):
    robots=[]
    for team in ('red','blue'):
        for role in ('hero','infantry','sentry'):
            r=dict(robot_id=team+'_'+role,team=team,role=role,xy=[0.,0.],hp=300.,alive=True,yaw=0.,gimbal_yaw=0.,gimbal_pitch=0.,vx=0.,vy=0.,wz=0.)
            if complete:r.update(max_hp=300.,ammo=25.,heat=0.,heat_max=100.)
            robots.append(r)
    value=dict(schema='rm.sim.platform.v2',source='simulator_truth_display_only',algorithm_input=False,sim_time=stamp,episode=episode,phase='running',robots=robots,control={'leases':{'red_infantry':{c:{'owner':owner,'epoch':epoch} for c in ('motion','gimbal','fire')}}})
    if complete:value.update(remaining_seconds=299.,victory_points={'red':0,'blue':0})
    return value

def frame(t,*,seq=1,age=0.,robot=None,watch_epoch=None,episode=0):
    image=np.zeros((6,8,3),dtype=np.uint8);ok,encoded=cv2.imencode('.jpg',image);assert ok
    return dict(schema='rm.sim.platform.v2',available=True,reason='available',robot_id=robot or t.watch_robot,watch_epoch=t.watch_epoch if watch_epoch is None else watch_epoch,episode=episode,frame_seq=seq,capture_sim_time=.9,age_wall_s=age,max_frame_age_wall_s=2.5,capture_wall_monotonic=100.-age,encode_completed_wall_monotonic=100.,source='simulated_raw_camera_display_jpeg',full_frame=True,algorithm_input=False,own_qpos_at_capture=[0.]*5,own_qvel_at_capture=[0.]*5,own_odom_at_capture=[0.]*3,own_odom_velocity_at_capture=[0.]*3,calibration={'intrinsics':{'fx':10.,'fy':10.,'cx':4.,'cy':3.,'distortion_model':'brown_conrady_5','distortion':[0.]*5},'extrinsics':{'parent_frame':'gimbal_link','translation_m':[.24,0.,.06],'rotation_xyzw':[-.5,.5,-.5,.5],'calibration_id':'synthetic-fixture'}},image=dict(encoding='jpeg',width=8,height=6,data_base64=base64.b64encode(encoded.tobytes()).decode()))

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.now=[100.];self.client=Client();self.t=PlatformTransport(role='operator',clock=lambda:self.now[0],client=self.client);self.t.connect_once();self.t.install_state(state());self.t.watch('red_infantry');self.t.process_jobs();self.t.install_state(state());self.t.install_frame(frame(self.t),generation=self.t._watch_generation)
    def test_watch_never_claims(self):
        self.assertFalse(any(c[0]=='claim' for c in self.client.calls));self.assertIsNone(self.t.owned_robot)
    def test_explicit_claim_still_requires_activate(self):
        self.assertFalse(self.t.request_claim());self.t.process_jobs();self.assertEqual(self.t.owned_robot,'red_infantry');self.assertFalse(self.t._focus);self.assertFalse(any(c[0]=='control' for c in self.client.calls));self.assertTrue(self.t.activate())
    def claim_arm(self):self.t.request_claim();self.t.process_jobs();self.t.activate();self.t.heartbeat(True)
    def test_mouse_is_absolute_integrated_target_not_rate_time(self):
        self.claim_arm();self.t.submit_input(vx_mps=0,vy_mps=0,fire=False);self.t.submit_mouse(-.024,.012);self.t.control_tick();self.t.control_tick();targets=[c[3] for c in self.client.calls if c[0]=='control' and c[2]=='gimbal'];self.assertEqual(targets[0],targets[1]);self.assertAlmostEqual(targets[0]['yaw_rad'],-.024)
    def test_focus_and_late_heartbeat_release_without_fire(self):
        self.claim_arm();self.t.submit_input(vx_mps=1,vy_mps=0,fire=True);self.now[0]+=.16;self.t.heartbeat(True);self.t.control_tick();self.assertFalse(self.t._focus);self.assertIsNone(self.t.owned_robot);self.assertFalse(any(c[0]=='control' for c in self.client.calls));self.assertIn(('release','red_infantry'),self.client.calls)
    def test_change_watch_releases_without_claiming_next(self):
        self.claim_arm();self.t.watch('blue_hero');self.t.process_jobs();self.assertIsNone(self.t.owned_robot);self.assertIsNone(self.t.video);self.assertEqual([c for c in self.client.calls if c[0]=='claim'],[('claim','red_infantry')]);self.assertIn(('release','red_infantry'),self.client.calls)
    def test_old_watch_epoch_episode_and_generation_rejected(self):
        old=self.t._watch_generation;self.t.watch('blue_hero');self.t.process_jobs();self.t.install_state(state());self.assertFalse(self.t.install_frame(frame(self.t),generation=old))
        for f in (frame(self.t,robot='red_infantry'),frame(self.t,watch_epoch=99),frame(self.t,episode=99)):
            with self.assertRaises(ValueError):self.t.install_frame(f,generation=self.t._watch_generation)
    def test_duplicate_frame_never_refreshes_capture_freshness(self):
        stamp=self.t.video_at;self.now[0]+=.5;self.assertFalse(self.t.install_frame(frame(self.t),generation=self.t._watch_generation));self.assertEqual(self.t.video_at,stamp)
    def test_unavailable_keeps_sequence_floor_and_clears_pixels(self):
        self.t.install_frame({'available':False,'reason':'expired'},generation=self.t._watch_generation);self.assertIsNone(self.t.video);self.assertEqual(self.t.video_sequence,1);self.assertFalse(self.t.install_frame(frame(self.t),generation=self.t._watch_generation))
    def test_invalid_state_latches_uncertain(self):
        self.claim_arm();self.t._failed(ValueError('bad state'));self.assertTrue(self.client.uncertain);self.assertFalse(self.t.selection_ready());self.assertIsNone(self.t.snapshot()[0])
    def test_missing_display_fields_stay_unknown(self):
        self.t.install_state(state(complete=False));self.assertIsNone(self.t.telemetry['own']['ammo']);self.assertIsNone(self.t.telemetry['remaining_seconds']);self.assertIsNone(self.t.telemetry['vp']['red'])
    def test_frame_metadata_and_jpeg_header_are_checked(self):
        for change in [dict(full_frame=False),dict(age_wall_s=3.),dict(own_qpos_at_capture=[]),dict(source='overlaid_truth')]:
            value=frame(self.t,seq=2);value.update(change)
            with self.assertRaises(ValueError):self.t.install_frame(value,generation=self.t._watch_generation)
        with self.assertRaises(ValueError):jpeg_dimensions(b'not jpeg')
    def test_old_claim_reply_releases_instead_of_arming(self):
        self.t.request_claim();old=self.client.claim
        def race(rid,channels):
            result=old(rid,channels);self.t.watch('blue_hero');return result
        self.client.claim=race;self.t.process_jobs();self.t.process_jobs();self.assertIsNone(self.t.owned_robot);self.assertFalse(self.t._focus);self.assertIn(('release','red_infantry'),self.client.calls)
    def test_transfer_and_decode_cannot_rejuvenate_camera(self):
        self.now[0]=101.
        self.t.install_frame(frame(self.t,seq=2,age=.3),generation=self.t._watch_generation,request_started=100.8)
        self.assertAlmostEqual(self.t.video_at,100.5)
    def test_camera_expired_in_transfer_or_decode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.t.install_frame(frame(self.t,seq=2,age=2.4),generation=self.t._watch_generation,request_started=99.8)
        real_decode=cv2.imdecode
        def delayed(*args,**kwargs):
            value=real_decode(*args,**kwargs);self.now[0]+=2.6;return value
        with patch('cv2.imdecode',side_effect=delayed):
            with self.assertRaises(ValueError):self.t.install_frame(frame(self.t,seq=2),generation=self.t._watch_generation)
        self.assertEqual(self.t.video_sequence,1)
    def test_observer_never_claims_or_sends_inputs(self):
        self.t.role='spectator'
        with self.assertRaises(ValueError):self.t.request_claim()
        self.assertFalse(self.t.activate());self.t.submit_input(vx_mps=1,vy_mps=0,fire=True);self.t.control_tick();self.assertFalse(any(c[0] in ('control','claim') for c in self.client.calls))

class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.now=[100.];self.client=Client();self.t=PlatformTransport(role='operator',clock=lambda:self.now[0],client=self.client)
        self.r=PlatformRuntime(transport=self.t)
        with patch.object(self.t,'start'):self.w=PlatformWindow(self.r)
        self.w.timer.stop();self.w.isActiveWindow=lambda:True;self.w.registry.get('AudioFeedback').enabled=False
        self.t.connect_once();self.t.install_state(state());self.r.heartbeat(self.w)
    def tearDown(self):self.w.close();self.r._tmp.cleanup()
    def ready(self):
        self.r.select_robot(self.w,'red_infantry');self.t.process_jobs();self.t.install_state(state());self.t.install_frame(frame(self.t),generation=self.t._watch_generation);self.r.heartbeat(self.w)
    def test_original_jello_selection_changes_watch_only(self):
        s=self.w.registry.get('RobotProfile').get_setting(SELECTION_SETTING);self.w.dispatch('jello_mode_select',(s,'blue_hero'),QPointF(),1,QRectF(0,0,100,20));self.t.process_jobs();self.assertEqual(self.t.watch_robot,'blue_hero');self.assertFalse(self.r.armed);self.assertFalse(any(c[0]=='claim' for c in self.client.calls))
    def test_two_explicit_f2_steps(self):
        self.ready();self.assertFalse(self.r.arm());self.t.process_jobs();self.assertFalse(self.r.armed);self.assertTrue(self.r.arm());self.r.heartbeat(self.w);self.assertTrue(self.r.armed)
    def test_observer_f2_and_keyboard_cannot_send(self):
        self.ready();self.t.role='spectator';self.assertFalse(self.r.arm());self.w.control.key_state['W']=True;self.w.control.left_button_down=True;self.w.control.tick(self.w.state,self.w.registry);self.t.control_tick();self.assertFalse(any(c[0] in ('claim','control') for c in self.client.calls))
    def test_no_camera_means_no_claim_and_no_fake_hud_values(self):
        self.r.select_robot(self.w,'red_infantry');self.t.process_jobs();self.t.install_state(state(complete=False));self.r.apply_state(self.w.state);self.assertFalse(self.r.arm());self.assertFalse(self.w.state.mujoco_telemetry_valid);self.assertTrue(self.w.state.mujoco_partial);self.assertFalse(any(c[0]=='claim' for c in self.client.calls))
    def test_gui_open_releases_existing_lease(self):
        self.ready();self.r.arm();self.t.process_jobs();self.r.arm();self.w.combat_control_input_allowed=lambda:False;self.r.heartbeat(self.w);self.assertFalse(self.r.armed);self.t.control_tick();self.assertIn(('release','red_infantry'),self.client.calls)
    def test_spectator_label_is_read_only_and_blocks_local_combat_state(self):
        from PySide6.QtGui import QKeyEvent,QMouseEvent
        from PySide6.QtCore import Qt,QEvent
        from rm_client.ui.minecraft_hud import MinecraftCombatHudRenderer
        self.ready();self.t.role='spectator';self.r.apply_state(self.w.state)
        self.w.combat_control_input_allowed=lambda:True
        self.w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_W,Qt.KeyboardModifier.NoModifier))
        self.w.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(680,450),QPointF(680,450),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
        self.assertFalse(self.w.control.left_button_down);self.assertFalse(any(self.w.control.key_state.values()))
        self.assertIn('SIM SPECTATOR',MinecraftCombatHudRenderer.scoreboard_lines(self.w.state)[0][0])
    def test_normal_registry_and_physical_backend_remain_unmodified(self):
        self.assertIsNone(build_default_registry(discover_devices=False).get('RobotProfile').get_setting(SELECTION_SETTING));self.assertFalse(self.r.backend.serial.connected);self.assertFalse(self.r.backend.mqtt.connected);self.assertFalse(self.r.backend.publish_keyboard_mouse()[0]);self.assertFalse(self.w.registry.get('ArmorAutoAim').enabled)
if __name__=='__main__':unittest.main()
