"""Synthetic-only contract and no-physical-I/O regression tests."""
import copy
import json
import math
import os
from pathlib import Path
import time
import unittest
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from rm_client.sim_operator_transport import SimOperatorTransport,SCHEMA
from rm_client.sim_operator_runtime import OperatorRuntime,SimInputBridge,SimBackend

FIXTURE=Path(__file__).parent/'fixtures'/'sim_operator'
def session(**kwargs):
    value=json.loads((FIXTURE/'session.fixture.json').read_text());value.update(kwargs);return value

def telemetry(**kwargs):
    own=dict(robot_id='red_infantry',team='red',role='infantry',hp=200,max_hp=200,ammo=20,heat=10,heat_max=100,xy=[-3.,1.],chassis_yaw=0.,gimbal_yaw=.2,pitch=.1,alive=True)
    value=dict(schema=SCHEMA,session_id='synthetic-contract-fixture',epoch=0,robot_id='red_infantry',
        server_time_ms=0.,sim_time=1.,phase='running',remaining_seconds=59.,countdown=None,vp={'red':0,'blue':0},
        own=own,robots=[own.copy()],map_source='simulator_truth_display_only',simulation_only=True,hardware_transport=False,events=[])
    value.update(kwargs);return value

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.now=[100.];self.t=SimOperatorTransport(clock=lambda:self.now[0]);self.t.install_session(session())
    def ready(self):
        import numpy as np
        self.t.install_telemetry(telemetry(),self.t.session)
        self.t.video=np.zeros((3,4,3),dtype=np.uint8);self.t.video_at=self.now[0]
    def test_endpoint_is_exact_loopback_only(self):
        for url in ['https://127.0.0.1:8768','http://localhost:8768','http://127.0.0.2:8768','http://example.test:8768','http://u@127.0.0.1:8768','http://127.0.0.1:8768/path','http://127.0.0.1:8768?q=1','http://127.0.0.1','http://127.0.0.1:80']:
            with self.subTest(url=url),self.assertRaises(ValueError):SimOperatorTransport(url)
    def test_explicit_session_contract(self):
        for changes in [{'simulation_only':False},{'hardware_transport':True},{'ttl_ms':1000},{'coordinate_frame':'world'},{'first_person_camera':'overhead'},{'epoch':True},{'schema':'other'},{'last_seq':float('nan')}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.t.install_session(session(**changes))
    def test_default_is_neutral_and_input_does_not_queue(self):
        self.t.submit_input(vx_mps=1,vy_mps=0,yaw_rate_rps=0,pitch_rate_rps=0,fire=True)
        self.t.heartbeat(True)
        b=self.t._control_payload();self.assertEqual(b['vx_mps'],0);self.assertFalse(b['fire'])
    def test_input_clamps_and_increasing_sequence(self):
        self.t.heartbeat(True);self.t.submit_input(vx_mps=1,vy_mps=1,yaw_rate_rps=99,pitch_rate_rps=-99,fire=True)
        a=self.t._control_payload();b=self.t._control_payload()
        self.assertAlmostEqual(math.hypot(a['vx_mps'],a['vy_mps']),1);self.assertEqual(a['yaw_rate_rps'],2);self.assertEqual(a['pitch_rate_rps'],-2);self.assertTrue(a['fire']);self.assertGreater(b['seq'],a['seq'])
    def test_invalid_input_never_nan(self):
        for value in (float('nan'),float('inf'),10**1000):
            with self.subTest(value_type=type(value).__name__),self.assertRaises(ValueError):
                self.t.submit_input(vx_mps=value,vy_mps=0,yaw_rate_rps=0,pitch_rate_rps=0,fire=False)
                
    def test_huge_telemetry_integer_fails_closed(self):
        data=telemetry();data['own']['ammo']=10**1000
        with self.assertRaises(ValueError):self.t.install_telemetry(data,self.t.session)
    def test_stalled_ui_latches_neutral_and_generation(self):
        self.t.heartbeat(True);self.t.submit_input(vx_mps=1,vy_mps=0,yaw_rate_rps=0,pitch_rate_rps=0,fire=True)
        before=self.t._generation;self.now[0]+=.16
        self.assertFalse(self.t._control_payload()['deadman']);self.assertGreater(self.t._generation,before)
        self.t.heartbeat(True)
        self.assertFalse(self.t._control_payload()['fire']);self.assertEqual(self.t._control_payload()['vx_mps'],0)
    def test_fresh_heartbeat_cannot_hide_stall(self):
        self.t.heartbeat(True);before=self.t._generation;self.now[0]+=.16;self.t.heartbeat(True)
        self.assertGreater(self.t._generation,before);self.assertFalse(self.t._control_payload()['deadman'])
    def test_disconnect_reconnect_uses_server_seq_but_releases_input(self):
        self.t.heartbeat(True);self.t._seq=70;self.t._clear_session('disconnected');self.t.install_session(session(last_seq=72))
        b=self.t._control_payload();self.assertEqual(b['seq'],73);self.assertFalse(b['deadman'])
    def test_telemetry_rejects_wrong_identity_and_reverse_time(self):
        self.ready()
        for data in [telemetry(epoch=1),telemetry(sim_time=.5),telemetry(schema='unknown'),telemetry(hardware_transport=True),telemetry(remaining_seconds=float('nan'))]:
            with self.subTest(data=data),self.assertRaises(ValueError):self.t.install_telemetry(data,self.t.session)
    def test_missing_own_health_not_presented_as_zero(self):
        data=telemetry();data['own']['hp']=None
        with self.assertRaises(ValueError):self.t.install_telemetry(data,self.t.session)
    def test_stale_state_and_video_independent_thresholds(self):
        self.ready();self.now[0]+=.6
        self.assertIsNone(self.t.snapshot()[0]);self.assertIsNotNone(self.t.snapshot()[1])
        self.now[0]+=1.;self.assertIsNone(self.t.snapshot()[1])

class RuntimeTests(TransportTests):
    def setUp(self):
        super().setUp();self.ready();self.r=OperatorRuntime(transport=self.t)
        from rm_client.model import RobotState
        self.state=RobotState();self.r.create_backend(self.state);self.c=self.r.create_control();self.registry=self.r.registry()
        self.w=Mock();self.w.control=self.c;self.w.isActiveWindow.return_value=True;self.w.combat_control_input_allowed.return_value=True
        self.r.heartbeat(self.w)
    def tearDown(self):self.r._tmp.cleanup()
    def test_original_keybinding_axis_mapping(self):
        self.r.arm();self.r.heartbeat(self.w)
        self.c.set_key('W',True,self.state,self.registry);self.c.set_key('A',True,self.state,self.registry);self.c.set_mouse_button(1,True,self.state,self.registry);self.c.add_mouse_motion(4,-4)
        with patch('rm_client.control.KeyboardControlBridge.send_command',side_effect=AssertionError('hardware egress')):
            self.c.tick(self.state,self.registry)
        self.c.tick(self.state,self.registry)  # A following zero-delta UI tick must not erase motion.
        value=self.t._control_payload();self.assertGreater(value['vx_mps'],0);self.assertGreater(value['vy_mps'],0)
        self.assertLess(value['yaw_rate_rps'],0);self.assertGreater(value['pitch_rate_rps'],0);self.assertTrue(value['fire'])
        self.c.tick(self.state,self.registry);self.assertEqual(self.t._control_payload()['yaw_rate_rps'],0)
    def test_focus_loss_latches_disarm_even_without_focus_event(self):
        self.r.arm();self.r.heartbeat(self.w);self.w.isActiveWindow.return_value=False;self.r.heartbeat(self.w)
        self.assertFalse(self.r.armed);self.w.isActiveWindow.return_value=True;self.r.heartbeat(self.w)
        self.assertFalse(self.t._control_payload()['deadman'])
    def test_menu_latches_and_no_old_input(self):
        self.r.arm();self.r.heartbeat(self.w);self.c.set_key('W',True,self.state,self.registry)
        self.w.combat_control_input_allowed.return_value=False;self.r.heartbeat(self.w)
        self.w.combat_control_input_allowed.return_value=True;self.r.heartbeat(self.w)
        self.assertFalse(self.r.armed);self.assertFalse(any(self.c.key_state.values()))
    def test_stall_requires_new_arm(self):
        self.r.arm();self.r.heartbeat(self.w);self.c.set_key('W',True,self.state,self.registry);self.c.tick(self.state,self.registry)
        self.now[0]+=.16;self.t._control_payload();self.r.heartbeat(self.w)
        self.assertFalse(self.r.armed);self.assertFalse(any(self.c.key_state.values()));self.assertFalse(self.t._control_payload()['fire'])
    def test_session_change_requires_new_arm(self):
        self.r.arm();self.t.install_session(session(epoch=1));self.r.heartbeat(self.w);self.assertFalse(self.r.armed)
    def test_dead_or_finished_robot_cannot_arm(self):
        self.t.telemetry['phase']='finished';self.assertFalse(self.r.arm())
        self.t.telemetry['phase']='running';self.t.telemetry['own']['alive']=False;self.assertFalse(self.r.arm())
    def test_projected_original_state(self):
        self.r.apply_state(self.state)
        self.assertEqual(self.state.robot_id,3);self.assertEqual(self.state.current_health,200)
        self.assertEqual(self.state.remaining_ammo,20);self.assertFalse(self.state.mock_simulation_enabled)
        self.assertFalse(self.state.mqtt_connected);self.assertFalse(self.state.serial_connected)
        self.assertEqual(self.state.map_robots[0]['x'],3);self.assertEqual(self.r.match_bar().countdown,59)

class OriginalWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def test_actual_main_window_has_no_physical_constructors_or_scan(self):
        from rm_client.ui import polar_app
        from rm_client.ui.sim_operator_window import OperatorWindow
        targets=['BackendManager','CameraCaptureWorker','TongjiVisionReceiver','LocalAlignedVision','TongjiVisionBridge']
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name in targets:stack.enter_context(patch.object(polar_app,name,side_effect=AssertionError(name)))
            for name in ('polar_port_choices','dt7_port_choices'):stack.enter_context(patch('rm_client.model.'+name,side_effect=AssertionError(name)))
            stack.enter_context(patch('rm_client.serial_ports.port_snapshot',side_effect=AssertionError('scan')))
            stack.enter_context(patch.object(SimOperatorTransport,'start'))
            r=OperatorRuntime();w=OperatorWindow(r);w.timer.stop()
            try:
                self.assertIsInstance(w,polar_app.PolarRMWindow);self.assertIsInstance(w.backend,SimBackend)
                w.rescan_serial_ports();w.on_tick();self.assertFalse(w.state.mock_simulation_enabled)
                self.assertIsNone(w.control.dt7_link);self.assertIsNone(w.control.cc_link)
            finally:w.close();r._tmp.cleanup()

if __name__=='__main__':unittest.main()
