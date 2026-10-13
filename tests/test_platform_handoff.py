"""Pure fake-clock planner and real ControlPlane routing; no sockets/physics."""
import io
import json
import unittest
import urllib.error
import urllib.parse

from rm_client.sim_platform_handoff import HandoffSchedule
from rm_client.sim_platform_client import PlatformClient
from rm_client.sim_platform_transport import PlatformTransport
from rm_match.platform_v2.control import ControlPlane, Rejected
from test_platform_adapters import catalog
from test_sim_platform_v2 import state


def window(index=1, issued=10000., ttl=.15):
    return dict(window_id=index, issued_wall=issued, expires_wall=issued+ttl, valid_for_s=ttl)


class PlannerTests(unittest.TestCase):
    def setUp(self):self.p=HandoffSchedule()
    def install(self):self.p.install(window(),0.,.01)
    def test_initial_inactive_can_wait_without_activation(self):
        self.install();self.assertEqual({},self.p.activation)
        self.assertEqual('wait',self.p.next_action(.02,kinds=2,controls=1,fresh=False))
        self.assertEqual('send',self.p.next_action(.02,kinds=2,controls=1,fresh=True))
    def test_window_request_start_not_ack(self):
        self.p.install(window(),1.,1.1);self.assertEqual(1.15,self.p.deadline)
    def test_expiry_at_equality(self):
        self.install();self.assertEqual('window_expired',self.p.expired(.15))
    def test_current_ack_updates_one_kind_before_old_bound_check(self):
        self.install();self.p.activation={'navigation':.15};self.p.install(window(2,10000.1),.1,.12)
        self.p.acknowledge('navigation',dict(window_id=2,activation_expires=10000.25))
        self.assertIsNone(self.p.expired(.151))
    def test_other_kind_not_renewed_by_first_ack(self):
        self.install();self.p.activation={'navigation':.15,'aim':.15};self.p.install(window(2,10000.1),.1,.12)
        self.p.acknowledge('navigation',dict(window_id=2,activation_expires=10000.25))
        self.assertEqual('activation_expired',self.p.expired(.151))
    def test_wrong_window_and_invalid_deltas_rejected(self):
        self.install()
        for wid,exp in [(True,10000.1),(1.,10000.1),(2,10000.1),(1,float('nan')),(1,10000.),(1,10000.151)]:
            with self.subTest(wid=wid,exp=exp),self.assertRaises(ValueError):
                self.p.acknowledge('aim',dict(window_id=wid,activation_expires=exp))
    def test_once_immediate_then_20ms(self):
        self.install();self.p.begin_batch(.011);self.p.finish_batch()
        self.assertEqual('wait',self.p.next_action(.030,kinds=0,controls=1,fresh=True))
        self.assertEqual('send',self.p.next_action(.031,kinds=0,controls=1,fresh=True))
    def test_populating_cannot_be_replaced(self):
        self.install();self.assertEqual('wait',self.p.next_action(.08,kinds=0,controls=3,fresh=False))
    def test_large_cost_stops_not_hotloop(self):
        self.install();self.p.observe('window',0.,.1)
        self.assertEqual('budget_pressure',self.p.next_action(.02,kinds=2,controls=1,fresh=True))
    def test_reset_preserves_slow_rtt_but_new_session_does_not(self):
        self.p.observe('window',0.,.1);self.p.reset();self.assertEqual(.1,self.p.costs['window'])
        self.p.new_session();self.assertEqual(.01,self.p.costs['window'])
    def test_min_issue_interval_and_maintenance_budget(self):
        self.install();self.p.begin_batch(.011);self.p.finish_batch()
        self.assertEqual('fetch',self.p.next_action(.075,kinds=2,controls=1,fresh=True))
        self.assertEqual('fetch',self.p.next_action(.075,kinds=2,controls=1,fresh=True,maintenance=('renew',)))
        self.assertEqual('forecast_insufficient_one_attempt',self.p.handoff_reason)
    def test_no_revision_no_ordinary_send(self):
        self.install();self.p.begin_batch(.01);self.p.finish_batch()
        self.assertEqual('wait',self.p.next_action(.05,kinds=0,controls=3,fresh=False))


class Response:
    def __init__(self,result):self.body=json.dumps(dict(schema='rm.sim.platform.v2',**result)).encode()
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,n):return self.body[:n]


class Harness:
    def __init__(self,both=False):
        self.now=100.;self.offset=10000.;self.delay={};self.ack_delay={};self.requests=[];self.hook=None
        self.server=ControlPlane(['red_infantry'],clock=lambda:self.now+self.offset)
        self.client=PlatformClient('http://127.0.0.1:8766',control_profile='server_window_v1',diagnostic_clock=lambda:self.now)
        self.client._opener=self
        self.t=PlatformTransport(role='operator',clock=lambda:self.now,client=self.client)
        t=self.t;t.connect_once();t.install_catalog(catalog(),connection_generation=t._connection_generation,serial=1)
        t.install_state(state());t.watch('red_infantry');t.process_jobs();t.install_state(state());t.video=object();t.video_at=self.now
        t.configure_adapters({'navigation':'nav_rule_v1','aim':'aim_pixel_baseline_v1' if both else 'Manual'},{'V2 Goal X':.75})
        t.request_claim();t.process_jobs();t.activate();self.sample();t.control_tick();self.sample();t.control_tick()
        self.now+=.001;self.sample();t.control_tick()
    def sample(self):
        # Synthetic UI/state freshness is explicit; no image or physics claim.
        self.t.heartbeat(True);self.t.submit_input(vx_mps=0,vy_mps=0,fire=False)
        self.t.telemetry_at=self.now;self.t.video_at=self.now
    def tick(self):
        try:self.t.control_tick()
        except Exception as exc:self.t._failed(exc,stage='fake_worker')
    def open(self,req,timeout):
        path=urllib.parse.urlsplit(req.full_url).path;b=json.loads(req.data) if req.data else {};token=req.get_header('Authorization','').removeprefix('Bearer ')
        row=dict(path=path,start=self.now,window_id=b.get('window_id'),kind=b.get('kind'));self.requests.append(row)
        self.now+=self.delay.get(path,0.);row['admission']=self.now
        s=self.server
        try:
            if path=='/v2/sessions':r=s.open_session(b['role'],b.get('control_profile','legacy_v2'))
            elif path=='/v2/watch':r=s.watch(token,b['robot_id'])
            elif path=='/v2/claim':r=s.claim(token,b['robot_id'],b['channels'])
            elif path=='/v2/renew':r=s.renew(token,b['robot_id'],b['lease_epochs'])
            elif path=='/v2/keepalive':r=s.keepalive(token)
            elif path=='/v2/bind':r=s.bind(token,b['robot_id'],b['kind'],b['lease_epochs'],b['adapter_id'],b['config'])
            elif path=='/v2/control_window':r=s.control_window(token,b['robot_id'],b['lease_epochs'])
            elif path=='/v2/adapter_input':r=s.adapter_input(token,b['robot_id'],b['kind'],b['lease_epochs'],b['binding_epoch'],b['seq'],b['window_id'],b['valid_for_s'])
            elif path=='/v2/control':r=s.control(token,b['robot_id'],b['channel'],b['lease_epoch'],b['seq'],b['value'],b['valid_for_s'],b.get('window_id'))
            elif path=='/v2/release':r=s.release(token,b['robot_id'],b['lease_epochs'])
            elif path=='/v2/close':r=s.close_session(token)
            else:raise ValueError(path)
        except Rejected as exc:
            row['rejected']=exc.reason
            raise urllib.error.HTTPError(req.full_url,409,'rejected',{},io.BytesIO(json.dumps({'error':exc.reason}).encode()))
        self.now+=self.ack_delay.get(path,0.);row['ack']=self.now
        if self.hook:self.hook(path)
        return Response(r)


class RoutingTests(unittest.TestCase):
    def deferred_handoff(self,both=False):
        h=Harness(both);h.now=100.065;h.sample();h.tick()
        self.assertEqual('WAIT_POST_ACK_SAMPLE',h.t._handoff.phase)
        h.now=100.140;h.sample()
        return h
    def test_initial_adapter_populates_real_controlplane(self):
        h=Harness();self.assertTrue(h.t._focus);self.assertIn('navigation',h.t._handoff.activation)
    def test_regular_sampling_handles_30ms_window_ack(self):
        h=Harness();h.ack_delay['/v2/control_window']=.030
        next_sample=100.020
        for _ in range(70):
            h.now+=.005
            if h.now>=next_sample:
                h.sample()
                while next_sample<=h.now:next_sample+=.020
            h.tick()
            self.assertTrue(h.t._focus,h.t.note)
        issues=[r['start'] for r in h.requests if r['path']=='/v2/control_window']
        self.assertGreater(len(issues),3)
        self.assertTrue(all(b-a>=.05-1e-9 for a,b in zip(issues,issues[1:])))
    def test_no_new_sample_no_replacement_hotloop(self):
        h=Harness();h.now=100.075;h.sample();h.tick();count=len(h.requests)
        for _ in range(10):h.now+=.001;h.tick()
        self.assertEqual(count,len(h.requests));self.assertEqual('WAIT_POST_ACK_SAMPLE',h.t._handoff.phase)
    def test_focus_loss_during_window_ack_never_restores(self):
        h=Harness();h.hook=lambda p:h.t.neutral('blur') if p=='/v2/control_window' else None
        h.now=100.075;h.sample();h.tick();self.assertFalse(h.t._focus);self.assertIsNone(h.t._window)
    def test_maintenance_spends_old_authority_budget(self):
        h=Harness(both=True);h.now=100.13;h.t._renew_at=99.;h.delay['/v2/renew']=.03
        h.sample();h.tick();self.assertFalse(h.t._focus)
        self.assertFalse(any(r['path']=='/v2/adapter_input' and r['start']>=100.13 for r in h.requests))
    def test_expiry_does_not_automatically_bind(self):
        h=Harness();before=sum(r['path']=='/v2/bind' for r in h.requests)
        h.now=100.15;h.sample();h.tick();h.now+=.01;h.tick()
        self.assertFalse(h.t._focus);self.assertEqual(before,sum(r['path']=='/v2/bind' for r in h.requests))
    def test_server_admission_149_then_ack_151_can_continue_one_kind(self):
        h=self.deferred_handoff();h.delay['/v2/adapter_input']=.009;h.ack_delay['/v2/adapter_input']=.002
        h.tick();self.assertTrue(h.t._focus,h.t.note)
        pulse=[r for r in h.requests if r['path']=='/v2/adapter_input'][-1]
        self.assertAlmostEqual(100.149,pulse['admission']);self.assertAlmostEqual(100.151,pulse['ack'])
    def test_server_admission_at_150_or_151_rejects(self):
        for delay in (.010,.011):
            with self.subTest(delay=delay):
                h=self.deferred_handoff();h.delay['/v2/adapter_input']=delay;h.tick()
                self.assertFalse(h.t._focus);self.assertTrue(h.client.uncertain)
                pulse=[r for r in h.requests if r['path']=='/v2/adapter_input'][-1]
                self.assertEqual('binding_mismatch',pulse['rejected'])
    def test_first_kind_ack_does_not_hide_second_kind_expiry(self):
        h=self.deferred_handoff(True);h.delay['/v2/adapter_input']=.009;h.ack_delay['/v2/adapter_input']=.002
        before=len(h.requests);h.tick();self.assertFalse(h.t._focus)
        self.assertEqual(['navigation'],[r['kind'] for r in h.requests[before:] if r['path']=='/v2/adapter_input'])
        self.assertFalse(any(r['path']=='/v2/control' for r in h.requests[before:]))
    def test_release_has_priority_over_due_renew_and_window(self):
        h=Harness();h.now=100.07;h.t._renew_at=99.;h.t.neutral('blur');before=len(h.requests)
        h.tick();self.assertEqual(['/v2/release'],[r['path'] for r in h.requests[before:]])
    def test_late_activation_ack_after_blur_releases_no_fire(self):
        h=self.deferred_handoff();h.hook=lambda p:h.t.neutral('blur') if p=='/v2/adapter_input' else None
        before=len(h.requests);h.tick();self.assertFalse(h.t._focus)
        self.assertEqual(['/v2/adapter_input','/v2/release'],[r['path'] for r in h.requests[before:]])
    def test_maintenance_and_dual_kind_sequence_remain_bounded(self):
        h=Harness(True);h.ack_delay.update({'/v2/control_window':.015,'/v2/renew':.006,'/v2/keepalive':.006})
        h.t._keepalive_at=90.05
        for _ in range(120):
            h.now+=.005;h.sample();h.tick();self.assertTrue(h.t._focus,h.t.note)
        self.assertTrue(any(r['path']=='/v2/renew' for r in h.requests))
        self.assertGreater(sum(r['path']=='/v2/keepalive' for r in h.requests),1)
    def test_large_observed_delay_stops_without_hotloop(self):
        h=Harness();h.ack_delay['/v2/control_window']=.1
        h.now=100.065;h.sample();h.tick();self.assertFalse(h.t._focus)
        count=len(h.requests)
        for _ in range(100):h.now+=.005;h.sample();h.tick()
        self.assertLessEqual(len(h.requests)-count,1)  # release only, no rebind/window.
    def test_same_revision_cannot_repeat_batch_from_wake(self):
        h=Harness();count=len(h.requests);h.now+=.025;h.tick();h.tick()
        self.assertEqual(count,len(h.requests))
    def test_mouse_only_does_not_populate_handoff(self):
        h=Harness();h.now=100.065;h.sample();h.tick();count=len(h.requests)
        h.t.submit_mouse(.01,0.);h.now+=.005;h.tick();self.assertEqual(count,len(h.requests))
    def test_new_sample_only_wakes_existing_worker(self):
        h=Harness();h.now=100.065;h.sample();h.tick();count=len(h.requests)
        h.t._wake_control.clear();h.sample();self.assertTrue(h.t._wake_control.is_set());self.assertEqual(count,len(h.requests))
    def test_observed_budget_survives_explicit_neutral(self):
        h=Harness();h.t._handoff.observe('window',0.,.050);h.t.neutral('blur')
        self.assertEqual(.05,h.t._handoff.costs['window'])


if __name__=='__main__':unittest.main()
