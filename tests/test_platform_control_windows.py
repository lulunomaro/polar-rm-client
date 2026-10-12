"""Synthetic Qt-worker sampling and in-flight reply boundaries; no camera claims."""
import unittest
from test_sim_platform_v2 import Client,state,frame
from rm_client.sim_platform_transport import PlatformTransport

class WindowClient(Client):
    control_profile='server_window_v1'
    def __init__(self):super().__init__();self.window_count=0;self.on_window=None;self.on_control=None
    def control_window(self,rid,channels):
        self.window_count+=1;self.calls.append(('window',self.window_count))
        reply=dict(robot_id=rid,window_id=self.window_count,valid_for_s=.15,issued_wall=900000.,expires_wall=900000.15)
        if self.on_window:self.on_window()
        return reply
    def control(self,rid,ch,value,**kw):
        super().control(rid,ch,value,**kw);self.calls[-1]+= (kw,)
        if self.on_control:self.on_control(ch)
        return {'accepted':True}

class SamplingTests(unittest.TestCase):
    def setUp(self):
        self.now=[100.];self.c=WindowClient();self.t=PlatformTransport(role='operator',clock=lambda:self.now[0],client=self.c)
        self.t.connect_once();self.t.install_state(state());self.t.watch('red_infantry');self.t.process_jobs();self.t.install_state(state());self.t.install_frame(frame(self.t),generation=self.t._watch_generation)
        self.t.request_claim();self.t.process_jobs();self.t.activate();self.t.heartbeat(True)
    def input(self):self.t.submit_input(vx_mps=1.,vy_mps=0.,fire=True)
    def controls(self):return [c for c in self.c.calls if c[0]=='control']
    def test_pre_ack_cached_input_never_sent(self):
        self.input();self.t.control_tick();self.t.control_tick();self.assertEqual(self.controls(),[])
        self.input();self.t.control_tick();self.assertEqual(len(self.controls()),3)
        self.assertTrue(all(c[4]['window_id']==1 for c in self.controls()))
    def test_uses_request_start_not_remote_monotonic(self):
        self.c.on_window=lambda:self.now.__setitem__(0,100.04)
        self.input();self.t.control_tick();self.assertAlmostEqual(self.t._window_deadline,100.15)
    def test_late_window_does_not_revive_focus(self):
        self.c.on_window=lambda:self.now.__setitem__(0,100.15)
        self.input();self.t.control_tick();self.assertFalse(self.t._focus);self.assertEqual(self.controls(),[])
    def test_blur_during_ack_cannot_install_window(self):
        self.c.on_window=lambda:self.t.neutral('lost focus')
        self.input();self.t.control_tick();self.assertIsNone(self.t._window);self.assertEqual(self.controls(),[])
    def test_disconnect_during_ack_cannot_install_window(self):
        self.c.on_window=lambda:self.t._failed(ValueError('disconnected'))
        self.input();self.t.control_tick();self.assertIsNone(self.t._window);self.assertIsNone(self.t.snapshot()[0])
    def test_generation_checked_between_channel_sends(self):
        self.input();self.t.control_tick();self.input();self.c.on_control=lambda ch:self.t.neutral()
        self.t.control_tick();self.assertEqual([c[2] for c in self.controls()],['motion'])
    def test_expiry_checked_between_channel_sends(self):
        self.input();self.t.control_tick();self.input();self.c.on_control=lambda ch:self.now.__setitem__(0,100.15)
        self.t.control_tick();self.assertEqual([c[2] for c in self.controls()],['motion']);self.assertFalse(self.t._focus)
    def test_new_window_requires_new_sampling_again(self):
        self.input();self.t.control_tick();self.input();self.t.control_tick();self.now[0]+=.101;self.t.heartbeat(True)
        self.input();self.t.control_tick();self.assertEqual(self.c.window_count,2);self.t.control_tick();self.assertEqual(len(self.controls()),3)
        self.input();self.t.control_tick();self.assertEqual(len(self.controls()),6);self.assertEqual(self.controls()[-1][4]['window_id'],2)
    def test_no_mouse_only_refresh_of_old_keyboard_sample(self):
        self.input();self.t.control_tick();self.t.submit_mouse(.01,0.);self.t.control_tick();self.assertEqual(self.controls(),[])
    def test_default_real_operator_requires_profile(self):
        self.assertEqual(PlatformTransport(role='operator').client.control_profile,'server_window_v1')
        self.assertEqual(PlatformTransport(role='spectator').client.control_profile,'legacy_v2')

if __name__=='__main__':unittest.main()
