"""Deterministic mouse totals, control-lease handshake and fail-closed edges."""
import unittest
from rm_client.sim_operator_transport import SimOperatorTransport,SCHEMA
from rm_client.sim_operator_modes import mouse_angle_request,supports_mouse_angle
from tests.test_sim_operator_modes import mode_session

def angle_session(last=-1):
    s=mode_session();s['capabilities']['mouse_angle_input_version']=1;s['mouse_angle_last_generation']=last
    return s

class MouseAngleTests(unittest.TestCase):
    def setUp(self):
        self.now=[100.];self.t=SimOperatorTransport(clock=lambda:self.now[0]);self.t.install_session(angle_session())
    def neutral_ack(self,during=None):
        sent=[]
        def post(path,body,**kwargs):
            sent.append(body)
            if during:during()
            self.t._stop.set()
            return dict(schema=SCHEMA,accepted=True,**{k:body[k] for k in ('session_id','epoch','robot_id','seq')})
        self.t._json=post;self.t._stop.clear();self.t._control_loop();self.t._stop.clear();return sent[0]
    def arm(self):self.neutral_ack();self.t.heartbeat(True)
    def test_totals_are_independent_of_control_poll_interval(self):
        self.arm();self.t.submit_mouse(.018,-.024)
        bodies=[]
        for dt in (.008,.025,.1,.013):
            self.now[0]+=dt;self.t.heartbeat(True);bodies.append(self.t._control_payload())
        for b in bodies:
            self.assertEqual(b['mouse_angle']['yaw_total_rad'],.018)
            self.assertEqual(b['mouse_angle']['pitch_total_rad'],-.024)
            self.assertEqual(b['yaw_rate_rps'],0);self.assertEqual(b['pitch_rate_rps'],0)
        self.assertEqual(len({b['seq'] for b in bodies}),4)
    def test_coalesced_input_keeps_sum_and_repeat_does_not_add(self):
        self.arm()
        for x,y in [(.02,-.03),(-.01,.005),(.04,-.002)]:self.t.submit_mouse(x,y)
        one=self.t._control_payload();two=self.t._control_payload()
        self.assertAlmostEqual(one['mouse_angle']['yaw_total_rad'],.05)
        self.assertAlmostEqual(one['mouse_angle']['pitch_total_rad'],-.027)
        self.assertEqual(one['mouse_angle'],two['mouse_angle'])
    def test_new_lease_waits_for_current_neutral_ack(self):
        self.t.heartbeat(True);self.t.submit_mouse(.02,0)
        self.t.submit_input(vx_mps=1,vy_mps=0,yaw_rate_rps=1,pitch_rate_rps=1,fire=True)
        blocked=self.t._control_payload();self.assertFalse(blocked['focus']);self.assertFalse(blocked['fire']);self.assertNotIn('mouse_angle',blocked)
        zero=self.neutral_ack();self.assertFalse(zero['focus'])
        active=self.t._control_payload();self.assertTrue(active['focus']);self.assertEqual(active['mouse_angle']['yaw_total_rad'],.02)
    def test_old_neutral_ack_does_not_release_newer_neutral(self):
        self.neutral_ack(lambda:self.t.neutral('new release during request'))
        self.assertTrue(self.t._neutral_pending)
        self.t.heartbeat(True);self.assertFalse(self.t._control_payload()['focus'])
    def test_neutral_revokes_old_totals_and_new_generation_is_zero(self):
        self.arm();self.t.submit_mouse(.08,-.02);before=self.t._control_payload()['mouse_angle']
        self.t.neutral('blur');self.t.submit_mouse(1,1)
        self.assertNotIn('mouse_angle',self.t._control_payload())
        self.arm();after=self.t._control_payload()['mouse_angle']
        self.assertGreater(after['generation'],before['generation'])
        self.assertEqual(after['yaw_total_rad'],0);self.assertEqual(after['pitch_total_rad'],0)
    def test_timeout_latches_and_does_not_replay_angle(self):
        self.arm();self.t.submit_mouse(.08,-.02);old=self.t._control_payload()['mouse_angle']['generation']
        self.now[0]+=.2;self.assertNotIn('mouse_angle',self.t._control_payload())
        self.arm();new=self.t._control_payload()['mouse_angle'];self.assertGreater(new['generation'],old);self.assertEqual(new['yaw_total_rad'],0)
    def test_restart_uses_server_last_generation_floor(self):
        self.t.install_session(angle_session(77));self.arm()
        self.assertEqual(self.t._control_payload()['mouse_angle']['generation'],78)
    def test_server_generation_is_required_and_strict(self):
        for value in (None,True,-2,2**53,float('nan')):
            with self.subTest(value_type=type(value).__name__),self.assertRaises(ValueError):self.t.install_session(angle_session(value))
    def test_generation_exhaustion_never_wraps_or_moves(self):
        self.t.install_session(angle_session(2**53-1));self.arm();b=self.t._control_payload()
        self.assertFalse(b['focus']);self.assertNotIn('mouse_angle',b);self.assertEqual(self.t._mouse_generation,2**53-1)
    def test_new_identity_recovers_exhaustion_without_wrapping_old_session(self):
        self.t.install_session(angle_session(2**53-1));self.arm()
        self.assertFalse(self.t._control_payload()['focus'])
        new=angle_session();new['session_id']='new-synthetic-session'
        self.t.install_session(new);self.arm()
        b=self.t._control_payload();self.assertTrue(b['focus'])
        self.assertEqual(b['mouse_angle']['generation'],1)
        self.assertEqual(b['mouse_angle']['pitch_total_rad'],0)
    def test_same_identity_reconnect_keeps_local_generation_floor(self):
        self.arm();self.t.submit_mouse(.03,.04)
        used=self.t._control_payload()['mouse_angle']['generation']
        self.t._clear_session('connection lost');self.t.install_session(angle_session())
        self.arm();b=self.t._control_payload()
        self.assertGreater(b['mouse_angle']['generation'],used)
        self.assertEqual(b['mouse_angle']['yaw_total_rad'],0)
    def test_invalid_total_or_schema_is_rejected(self):
        valid=dict(generation=1,yaw_total_rad=0.,pitch_total_rad=0.)
        for bad in (None,dict(valid,generation=True),dict(valid,yaw_total_rad=float('inf')),dict(valid,pitch_total_rad=10**1000),dict(valid,extra=1)):
            with self.assertRaises(ValueError):mouse_angle_request(bad)
        self.arm()
        with self.assertRaises(ValueError):self.t.submit_mouse(1000001,0)
        self.assertFalse(self.t._focus);self.assertNotIn('mouse_angle',self.t._control_payload())
    def test_heartbeat_focus_edge_requires_neutral_ack(self):
        self.arm();self.assertTrue(self.t._control_payload()['focus'])
        self.t.heartbeat(False);self.t.heartbeat(True)
        self.assertFalse(self.t._control_payload()['focus'])
        self.neutral_ack();self.assertTrue(self.t._control_payload()['focus'])
    def test_consumed_ack_is_strict_and_not_measured_pose(self):
        from rm_client.sim_operator_modes import validate_feedback
        from tests.test_sim_operator import telemetry
        ack=dict(generation=-1,revoked_generation=-1,consumed_yaw_total_rad=0.,consumed_pitch_total_rad=0.,consumed_control_seq=-1,reason='no_input')
        data=telemetry(control={'mouse_angle_ack':ack});validate_feedback(data,angle_session())
        for bad in (dict(ack,generation=True),dict(ack,consumed_pitch_total_rad=float('nan')),dict(ack,reason='target_reached')):
            with self.assertRaises(ValueError):validate_feedback(telemetry(control={'mouse_angle_ack':bad}),angle_session())
    def test_original_mouse_collector_contributes_each_delta_once(self):
        from rm_client.sim_operator_runtime import OperatorRuntime
        from rm_client.model import RobotState
        r=OperatorRuntime(transport=self.t);registry=r.registry();state=RobotState();control=r.create_control();self.arm()
        try:
            control.add_mouse_motion(14,12);control.send_from_state(state,registry)
            first=self.t._control_payload()['mouse_angle']
            self.assertEqual(first['yaw_total_rad'],-.028);self.assertEqual(first['pitch_total_rad'],-.024)
            for _ in range(10):control.send_from_state(state,registry)
            self.assertEqual(self.t._control_payload()['mouse_angle'],first)
        finally:r._tmp.cleanup()

    def test_independently_synthetic_fixture_matches_request_and_ack(self):
        import json
        from pathlib import Path
        from rm_client.sim_operator_modes import validate_feedback
        from tests.test_sim_operator import telemetry
        f=json.loads((Path(__file__).parent/'fixtures/sim_operator/mouse-angle.fixture.json').read_text())
        self.assertTrue(supports_mouse_angle(f))
        parsed=mouse_angle_request(f['mouse_angle'])
        self.assertGreater(parsed['generation'],f['mouse_angle_last_generation'])
        validate_feedback(telemetry(control={'mouse_angle_ack':f['mouse_angle_ack']}),angle_session())

    def test_legacy_endpoint_remains_explicit_rate_compatibility(self):
        self.t.install_session(mode_session());self.t.heartbeat(True);self.t.submit_mouse(.02,0);body=self.t._control_payload()
        self.assertFalse(supports_mouse_angle(self.t.session));self.assertNotIn('mouse_angle',body);self.assertGreater(body['yaw_rate_rps'],0)

if __name__=='__main__':unittest.main()
