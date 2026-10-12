"""Synthetic selection, replay and original UI acceptance. No bridge/devices."""
import copy
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF,QRectF
from rm_client.model import build_default_registry
from rm_client.sim_robot_selection import selection_profiles,validate_selection_ack,ROBOT_IDS,SELECTION_SETTING,MAX_SEQUENCE
from rm_client.sim_operator_transport import SimOperatorTransport
from rm_client.sim_operator_runtime import OperatorRuntime
from rm_client.ui.sim_operator_window import OperatorWindow
from tests.test_sim_operator import session,telemetry

def selectable(robot='red_infantry',epoch=0,last=-1,**updates):
    s=session(robot_id=robot,epoch=epoch,first_person_camera=robot+'__aim')
    s['capabilities']=dict(s.get('capabilities',{}),robot_selection=True,robot_selection_version=1)
    s['endpoints']=dict(s.get('endpoints',{}),select_robot='/select_robot')
    s['last_selection_id']=last
    s['robot_profiles']=[dict(robot_id=r,team=r.split('_')[0],role=r.split('_')[1],capabilities=dict(automatic_targeting=False,spin_mode=False)) for r in ROBOT_IDS]
    s.update(updates);return s

def selected_telemetry(s):
    t=telemetry(session_id=s['session_id'],epoch=s['epoch'],robot_id=s['robot_id'])
    own=t['own'];own.update(robot_id=s['robot_id'],team=s['robot_id'].split('_')[0],role=s['robot_id'].split('_')[1])
    t['robots']=[dict(own)]
    return t

def ack(body,**updates):
    result=dict(schema='polar.sim.operator.v1',accepted=True,reason='robot_selected',session_id=body['session_id'],epoch=body['epoch']+1,robot_id=body['requested_robot_id'],selection_id=body['selection_id'],last_selection_id=body['selection_id'],previous_robot_id=body['robot_id'],previous_epoch=body['epoch'],requires_rearm=True,match_reset=False,server_time_ms=0)
    result.update(updates);return result

class SelectionContractTests(unittest.TestCase):
    def test_capability_is_explicit(self):
        self.assertEqual(selection_profiles(session()),())
        self.assertEqual(selection_profiles(selectable()),ROBOT_IDS)
    def test_profiles_reject_unknown_duplicate_identity_and_endpoint(self):
        cases=[]
        for replacement in ['http://example.test/select_robot','/other']:
            s=selectable();s['endpoints']['select_robot']=replacement;cases.append(s)
        s=selectable();s['robot_profiles'][1]=s['robot_profiles'][0];cases.append(s)
        s=selectable();s['robot_profiles'][0]['team']='blue';cases.append(s)
        s=selectable();s['robot_profiles'][0]['robot_id']='real_robot';cases.append(s)
        for val in [True,1.5,-2,MAX_SEQUENCE+1]:cases.append(selectable(last=val))
        for value in cases:
            with self.subTest(value=value),self.assertRaises(ValueError):selection_profiles(value)
    def test_success_ack_binds_both_identities_and_release(self):
        body=dict(session_id='s',epoch=2,robot_id='red_infantry',requested_robot_id='blue_hero',selection_id=4)
        self.assertTrue(validate_selection_ack(ack(body),body))
        for update in [dict(selection_id=3),dict(session_id='other'),dict(previous_epoch=1),dict(previous_robot_id='red_hero'),dict(epoch=2),dict(robot_id='red_infantry'),dict(requires_rearm=False),dict(match_reset=True),dict(last_selection_id=True)]:
            with self.subTest(update=update),self.assertRaises(ValueError):validate_selection_ack(ack(body,**update),body)

class SelectionTransportTests(unittest.TestCase):
    def setUp(self):
        self.now=[100.];self.t=SimOperatorTransport(clock=lambda:self.now[0]);self.t.install_session(selectable())
        self.ready()
    def ready(self):
        self.t.install_telemetry(selected_telemetry(self.t.session),self.t.session)
        self.t.video=np.zeros((3,4,3),dtype=np.uint8);self.t.video_at=self.now[0];self.t._neutral_pending=False
    def execute(self,**changes):
        calls=[]
        def request(path,body=None,**kwargs):
            calls.append((path,copy.deepcopy(body)))
            return ack(body,**changes) if path=='/select_robot' else {}
        with patch.object(self.t,'_json',side_effect=request):self.assertTrue(self.t._run_selection())
        return calls
    def test_selection_clears_live_control_old_video_and_single_slot(self):
        self.t.heartbeat(True);self.t.submit_input(vx_mps=1,vy_mps=0,yaw_rate_rps=0,pitch_rate_rps=0,fire=True)
        old_generation=self.t._generation
        self.assertTrue(self.t.select_robot('blue_hero'))
        self.assertGreater(self.t._generation,old_generation)
        self.assertFalse(self.t._focus);self.assertIsNone(self.t.session)
        self.assertEqual(self.t.snapshot()[:2],(None,None))
        self.assertIsNone(self.t._control_payload())
        self.assertFalse(self.t.selection_ready())
        with self.assertRaises(ValueError):self.t.select_robot('red_sentry')
    def test_neutral_is_sent_before_one_selection_and_no_retry(self):
        self.t.select_robot('blue_hero');calls=self.execute()
        self.assertEqual([p for p,b in calls],['/control','/select_robot'])
        self.assertFalse(calls[0][1]['focus']);self.assertFalse(calls[0][1]['fire'])
        self.assertFalse(self.t._run_selection());self.assertTrue(self.t.selection_pending)
        self.assertIsNone(self.t.session)
    def test_uncertain_ack_only_reconciles_never_replays(self):
        self.t.select_robot('blue_hero')
        with patch.object(self.t,'_json',side_effect=TimeoutError('delayed')) as request:
            self.t._run_selection();self.assertEqual(request.call_count,2)
            self.assertFalse(self.t._run_selection());self.assertEqual(request.call_count,2)
        self.assertIn('uncertain',self.t.selection_status)
        self.assertFalse(self.t._selection_busy);self.assertFalse(self.t._focus)
        self.t.install_session(selectable('blue_hero',1,0));self.ready()
        self.assertTrue(self.t.selection_ready());self.assertFalse(self.t._focus)
    def test_rejected_new_sequence_is_reported_as_rejected_not_uncertain(self):
        self.t.select_robot('blue_hero')
        self.execute(accepted=False,reason='expired_or_future_timestamp',last_selection_id=-1,epoch=0,robot_id='red_infantry')
        self.assertIn('Selection rejected: expired_or_future_timestamp',self.t.selection_status)
        self.assertFalse(self.t._selection_busy)
        self.assertTrue(self.t.selection_pending)
    def test_accepted_switch_rejects_old_session_and_telemetry(self):
        old=copy.deepcopy(self.t.session);old_data=selected_telemetry(old)
        self.t.select_robot('blue_hero');self.execute()
        with self.assertRaises(ValueError):self.t.install_session(old)
        self.t.install_session(selectable('blue_hero',1,0))
        self.assertFalse(self.t.install_telemetry(old_data,old))
        self.assertIsNone(self.t.snapshot()[0])
    def test_new_neutral_ack_and_matching_camera_required_for_ready(self):
        self.t.select_robot('blue_hero');self.execute();self.t.install_session(selectable('blue_hero',1,0))
        self.t.install_telemetry(selected_telemetry(self.t.session),self.t.session)
        self.assertFalse(self.t.selection_ready())
        self.t.video=np.zeros((3,4,3),dtype=np.uint8);self.t.video_at=self.now[0]
        self.assertFalse(self.t.selection_ready())
        self.t._neutral_pending=False;self.assertTrue(self.t.selection_ready())
        self.assertFalse(self.t._focus);self.assertFalse(self.t._intent['fire'])
    def test_generation_and_selection_sequence_do_not_replay_on_reconnect(self):
        self.t.select_robot('blue_hero');self.execute();self.t.install_session(selectable('blue_hero',1,0));self.ready();self.t.selection_ready()
        self.t.select_robot('red_sentry');calls=self.execute()
        self.assertEqual(calls[-1][1]['selection_id'],1)
    def test_sequence_exhaustion_fails_closed(self):
        self.t.install_session(selectable(last=MAX_SEQUENCE))
        with self.assertRaises(ValueError):self.t.select_robot('blue_hero')
        self.assertIsNone(self.t._selection_job)
    def test_invalid_selection_also_clears_existing_input(self):
        self.t.heartbeat(True);self.t.submit_input(vx_mps=1,vy_mps=0,yaw_rate_rps=0,pitch_rate_rps=0,fire=True)
        with self.assertRaises(ValueError):self.t.select_robot('unknown')
        self.assertFalse(self.t._focus);self.assertFalse(self.t._intent['fire'])
    def test_same_vehicle_is_noop_and_unknown_vehicle_rejected(self):
        self.assertFalse(self.t.select_robot('red_infantry'))
        with self.assertRaises(ValueError):self.t.select_robot('unknown')
        self.assertIsNone(self.t._selection_job)
    def test_pending_request_does_not_accept_inflight_discovery(self):
        old=copy.deepcopy(self.t.session);self.t.select_robot('blue_hero')
        self.assertFalse(self.t.install_session(old));self.assertIsNone(self.t.session)

class SelectionWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.p=patch.object(SimOperatorTransport,'start');self.p.start()
        self.now=[100.];self.t=SimOperatorTransport(clock=lambda:self.now[0]);self.r=OperatorRuntime(transport=self.t);self.w=OperatorWindow(self.r);self.w.timer.stop();self.w.isActiveWindow=lambda:True
        self.w.registry.get('AudioFeedback').enabled=False
        self.t.install_session(selectable());self.r.sync_selection(self.w)
    def tearDown(self):self.w.close();self.r._tmp.cleanup();self.p.stop()
    def test_setting_is_sim_only_and_original_role_stays_read_only(self):
        self.assertIsNone(build_default_registry(discover_devices=False).get('RobotProfile').get_setting(SELECTION_SETTING))
        setting=self.w.registry.get('RobotProfile').get_setting(SELECTION_SETTING)
        self.assertEqual(setting.options,list(ROBOT_IDS));self.assertFalse(self.w.setting_is_unavailable(setting))
        role=self.w.registry.get('RobotProfile').get_setting('Role');role.value='Hero';self.w.after_setting_changed(role)
        self.assertNotEqual(role.value,'Hero');self.assertIsNone(self.t._selection_job)
    def test_original_setting_callback_queues_sim_and_releases_all_inputs(self):
        self.r.armed=True;self.w.control.key_state['W']=True;self.w.control.left_button_down=True;self.w.control.right_button_down=True
        setting=self.w.registry.get('RobotProfile').get_setting(SELECTION_SETTING)
        self.w.dispatch('jello_mode_select',(setting,'blue_hero'),QPointF(),1,QRectF(0,0,100,20))
        self.assertTrue(self.t.selection_pending);self.assertFalse(self.r.armed)
        self.assertFalse(any(self.w.control.key_state.values()));self.assertFalse(self.w.control.left_button_down);self.assertFalse(self.w.control.right_button_down)
        self.assertTrue(self.w.setting_is_unavailable(setting));self.assertIsNone(self.w.frame_image)
        self.assertFalse(self.r.arm())
    def test_first_f2_synchronizes_new_frame_fit_before_arm(self):
        s=selectable();s['capabilities'].update(operator_modes_version=1,spin_mode=True,chassis_follow_modes=['free'])
        self.t.install_session(s);self.r.heartbeat(self.w)
        self.t.install_telemetry(selected_telemetry(s),s)
        self.t.video=np.zeros((240,320,3),dtype=np.uint8);self.t.video_at=self.now[0]
        self.t._neutral_pending=False
        self.assertTrue(self.r.arm())
        self.r.heartbeat(self.w)
        self.assertTrue(self.r.armed)
    def test_ui_follows_new_authoritative_identity_and_never_arms_it(self):
        self.r.select_robot(self.w,'blue_hero')
        with patch.object(self.t,'_json',side_effect=lambda path,body,**kw:ack(body) if path=='/select_robot' else {}):self.t._run_selection()
        self.t.install_session(selectable('blue_hero',1,0));self.t.install_telemetry(selected_telemetry(self.t.session),self.t.session)
        self.t.video=np.zeros((3,4,3),dtype=np.uint8);self.t.video_at=self.now[0];self.t._neutral_pending=False
        self.r.apply_state(self.w.state);self.r.sync_selection(self.w)
        self.assertEqual(self.w.registry.get('RobotProfile').get_setting(SELECTION_SETTING).value,'blue_hero')
        self.assertEqual(self.w.state.robot_profile,'Hero');self.assertEqual(self.w.state.alliance_color,'Blue')
        self.assertFalse(self.r.armed)
        for name in ('SpinMode','ArmorAutoAim','AutoSpinOnHit'):self.assertFalse(self.w.registry.get(name).enabled)
        self.assertTrue(self.r.arm());self.r.disarm('Focus lost');self.r.sync_selection(self.w)
        self.assertEqual(self.r.note,'Focus lost')

if __name__=='__main__':unittest.main()
