"""Pure input/worker tests with synthetic state/camera and fake SDK."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from dataclasses import replace
from test_platform_control_windows import SamplingTests
from test_platform_adapters import WorkerTests
from rm_client.sim_platform_input import PlatformInputBridge
from rm_client.model import RobotState,build_default_registry

class YawSamplingTests(SamplingTests):
    # Do not inherit test methods into the focused count: an explicit reused
    # fixture is used below by the loader-visible subclass filter.
    def test_yaw_requires_complete_post_ack_sample(self):
        self.t.submit_input(vx_mps=0,vy_mps=0,chassis_yaw_rps=1.)
        self.t.control_tick();self.t.control_tick();self.assertEqual([],self.controls())
        self.t.submit_mouse(.02,0);self.t.control_tick();self.assertEqual([],self.controls())
        self.t.submit_input(vx_mps=0,vy_mps=0,chassis_yaw_rps=-1.);self.t.control_tick()
        self.assertEqual(self.controls()[0][3]['wz_rps'],-1.)
    def test_new_window_never_replays_cached_yaw(self):
        self.t.submit_input(vx_mps=0,vy_mps=0,chassis_yaw_rps=1.);self.t.control_tick()
        self.t.submit_input(vx_mps=0,vy_mps=0,chassis_yaw_rps=1.);self.t.control_tick();before=len(self.controls())
        self.now[0]+=.075;self.t.heartbeat(True);self.t.submit_input(vx_mps=0,vy_mps=0,chassis_yaw_rps=1.);self.t.control_tick()
        self.t.control_tick();self.assertEqual(len(self.controls()),before)
        self.t.submit_input(vx_mps=0,vy_mps=0,chassis_yaw_rps=0.);self.t.control_tick()
        self.assertEqual(self.controls()[-3][3]['wz_rps'],0.)
    def test_yaw_initial_neutral_and_rearm_zero(self):
        self.assertEqual(self.t._intent['wz'],0.)
        self.t.submit_input(vx_mps=0,vy_mps=0,chassis_yaw_rps=1.);self.t.neutral()
        self.assertEqual(self.t._intent['wz'],0.);self.assertFalse(self.t._focus)
        self.t.process_jobs();self.t.request_claim();self.t.process_jobs();self.assertTrue(self.t.activate())
        self.assertEqual(self.t._intent['wz'],0.)
    def test_invalid_chassis_yaw_exact_finite_unit_axis(self):
        for value in (True,False,float('nan'),float('inf'),1.01,-1.01,10**999):
            with self.subTest(value=str(value)[:20]),self.assertRaises(ValueError):
                self.t.submit_input(vx_mps=0,vy_mps=0,chassis_yaw_rps=value)
        self.assertEqual(self.t._intent['wz'],0.)
    def test_late_ack_and_blur_clear_yaw(self):
        self.t.submit_input(vx_mps=0,vy_mps=0,chassis_yaw_rps=1.)
        self.c.on_window=lambda:self.t.neutral('blur')
        self.t.control_tick();self.assertEqual([],self.controls());self.assertEqual(self.t._intent['wz'],0.)
    def test_gimbal_rate_argument_is_not_chassis_yaw(self):
        self.t.submit_input(vx_mps=0,vy_mps=0,yaw_rate_rps=1.)
        self.t.control_tick();self.t.submit_input(vx_mps=0,vy_mps=0,yaw_rate_rps=1.);self.t.control_tick()
        self.assertEqual(self.controls()[0][3]['wz_rps'],0.)

class YawPlanTests(WorkerTests):
    def test_auto_navigation_never_blends_manual_yaw(self):
        self.ready();self.t.submit_input(vx_mps=1,vy_mps=0,chassis_yaw_rps=1.);self.t.control_tick()
        self.assertEqual(['fire'],[x[2] for x in self.ops('control')])
    def test_manual_plan_transition_clears_yaw(self):
        self.ready();self.t.submit_input(vx_mps=0,vy_mps=0,chassis_yaw_rps=1.)
        self.t.configure_adapters({'navigation':'Manual','aim':'Manual'}, {})
        self.assertEqual(self.t._intent['wz'],0.);self.assertFalse(self.t._focus)

class RoutedBridgeTests(unittest.TestCase):
    def setUp(self):
        self.t=SimpleNamespace(submit_input=Mock(),submit_mouse=Mock())
        self.r=SimpleNamespace(transport=self.t,modes=SimpleNamespace(submit=Mock()),disarm=Mock())
        self.bridge=PlatformInputBridge(self.r);self.registry=build_default_registry(discover_devices=False);self.registry.get('SpinMode').enabled=False;self.state=RobotState()
    def test_original_slots_e_q_both(self):
        for q,e,expected in [(False,True,1.),(True,False,-1.),(True,True,0.)]:
            self.bridge.reset_inputs();self.bridge.key_state['Q']=q;self.bridge.key_state['E']=e
            command=self.bridge.build_command(self.state,self.registry);self.bridge.send_command(command)
            self.assertEqual(self.t.submit_input.call_args.kwargs['chassis_yaw_rps'],expected)
    def test_mouse_delta_keeps_own_path(self):
        self.bridge.mouse_x=10;self.bridge.mouse_y=-5
        self.bridge.send_from_state(self.state,self.registry)
        self.t.submit_mouse.assert_called_once_with(-.02,-.01)
    def test_unsupported_spin_neutralizes_instead_of_clamping(self):
        self.registry.get('SpinMode').enabled=True;self.bridge.key_state['E']=True
        self.bridge.send_from_state(self.state,self.registry)
        self.r.disarm.assert_called_once();self.t.submit_input.assert_not_called()
        self.assertFalse(self.bridge.key_state['E'])
    def test_invalid_input_error_disarms(self):
        self.t.submit_input.side_effect=ValueError('invalid')
        self.bridge.send_from_state(self.state,self.registry);self.r.disarm.assert_called_once()

# Keep inherited legacy tests in their own original suite; this module counts
# only its new methods, while reusing setup/helpers without duplicate reporting.
def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite()
    for cls in (YawSamplingTests,YawPlanTests,RoutedBridgeTests):
        for name,value in cls.__dict__.items():
            if name.startswith('test_') and callable(value):suite.addTest(cls(name))
    return suite
if __name__=='__main__':unittest.main()
