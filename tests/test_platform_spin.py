"""Synthetic-only typed spin negotiation and original Qt toggle routing."""
import copy,math,unittest
from types import SimpleNamespace
from test_platform_adapters import OriginalUiTests,catalog
from rm_client.sim_platform_spin import capability,request
from PySide6.QtCore import Qt,QEvent
from PySide6.QtGui import QKeyEvent

def cap(t):
 c=dict(version=1,available=True,directions=['auto','cw','ccw'],resolved_auto_direction='ccw',speed_min_dps=120.,speed_max_dps=720.,simulation_only=True,power_guard=False,world_stabilized_gimbal=False,yaw_torque_ctrlrange=[-20.,20.])
 return dict(capability_schema='rm.sim.platform.capabilities.v1',session_id=t.client.session_id,watch_robot_id=t.watch_robot,watch_epoch=t.watch_epoch,episode=t.raw_state['episode'],robots=[dict(robot_id=t.watch_robot,spin_control=c)])
class ParserTests(unittest.TestCase):
 def setUp(self):self.t=SimpleNamespace(client=SimpleNamespace(session_id='session'),watch_robot='r',watch_epoch=1,raw_state={'episode':0});self.raw=cap(self.t)
 def parse(self,v):return capability(v,'session','r',1,0)
 def test_valid(self):self.assertIsNotNone(self.parse(self.raw))
 def test_bad_identity(self):
  for k,v in [('session_id','old'),('watch_robot_id','other'),('watch_epoch',True),('episode',False),('episode',1),('capability_schema','future')]:self.assertIsNone(self.parse(dict(self.raw,**{k:v})))
 def test_bad_capability(self):
  for k,v in [('version',True),('available',1),('speed_max_dps',10**999),('speed_min_dps',float('nan')),('simulation_only',False),('power_guard',True),('yaw_torque_ctrlrange',[0,20])]:
   raw=copy.deepcopy(self.raw);raw['robots'][0]['spin_control'][k]=v;self.assertIsNone(self.parse(raw))
 def test_duplicate_robot(self):self.raw['robots']*=2;self.assertIsNone(self.parse(self.raw))
 def test_spin_request_values(self):
  for v in [dict(direction='cw',speed_dps=120),dict(direction='auto',speed_dps=720)]:self.assertEqual(request(v),v)
  for v in [dict(direction=True,speed_dps=420),dict(direction='cw',speed_dps=True),dict(direction='cw',speed_dps=10**999),dict(direction='cw',speed_dps=721),dict(direction='cw',speed_dps=420,enabled=True)]:
   with self.assertRaises(ValueError):request(v)
class UI(OriginalUiTests):
 def enable_cap(self):
  raw=catalog();raw['control_capabilities']=cap(self.t);self.t.install_catalog(raw,connection_generation=self.t._connection_generation,serial=2);self.r.modes.sync(self.w)
 def key(self,k):self.w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress,k,Qt.KeyboardModifier.NoModifier))
 def arm(self):self.key(Qt.Key.Key_F2);self.t.process_jobs();self.key(Qt.Key.Key_F2);self.assertTrue(self.r.armed)
 def test_capability_never_auto_enables(self):self.enable_cap();self.assertTrue(self.r.modes.support[0]);self.assertFalse(self.w.registry.get('SpinMode').enabled);self.assertFalse(self.r.armed)
 def test_x_changes_clear_then_rearm_with_fresh_sample(self):
  self.enable_cap();self.arm();self.key(Qt.Key.Key_X);self.assertTrue(self.w.registry.get('SpinMode').enabled);self.assertFalse(self.r.armed);self.assertIsNone(self.t._intent['spin']);self.t.process_jobs();self.arm()
  self.w.control.send_from_state(self.w.state,self.w.registry);self.t.control_tick();self.assertFalse(any(x[0]=='control' for x in self.c.calls))
  self.w.control.send_from_state(self.w.state,self.w.registry);self.t.control_tick();motion=[x[3]for x in self.c.calls if x[0]=='control'and x[2]=='motion']
  self.assertEqual(motion[-1],dict(mode='spin_v1',frame='base_link',vx_mps=0.,vy_mps=0.,direction='auto',speed_dps=420.));self.assertNotIn('wz_rps',motion[-1])
 def test_power_guard_mock_locked(self):
  self.enable_cap();m=self.w.registry.get('SpinMode')
  for n in ('Power Guard','Mock Output'):self.assertTrue(self.w.setting_is_unavailable(m.get_setting(n)))
 def test_navigation_disables_spin_and_does_not_unbind(self):
  self.enable_cap();self.choose_nav();self.r.modes.sync(self.w);self.key(Qt.Key.Key_X);self.assertFalse(self.w.registry.get('SpinMode').enabled);self.assertIn('navigation',self.t.plan)
 def test_expired_caps_disarm(self):
  self.enable_cap();self.arm();self.now+=2.6;self.r.modes.sync(self.w);self.assertFalse(self.r.armed);self.assertFalse(self.r.modes.support[0])
 def test_old_cap_reply_rejected(self):
  self.enable_cap();old=self.t._connection_generation;self.t._failed(ValueError('test'));raw=catalog();raw['control_capabilities']=cap(self.t) if self.t.raw_state else None
  self.assertFalse(self.t.install_catalog(raw,connection_generation=old,serial=9));self.assertIsNone(self.t.spin_capability())
 def test_changed_cap_contents_requires_explicit_enable(self):
  self.enable_cap();self.key(Qt.Key.Key_X);raw=catalog();raw['control_capabilities']=cap(self.t);raw['control_capabilities']['robots'][0]['spin_control']['yaw_torque_ctrlrange']=[-10.,10.]
  self.t.install_catalog(raw,connection_generation=self.t._connection_generation,serial=3);self.r.modes.sync(self.w);self.assertFalse(self.w.registry.get('SpinMode').enabled);self.assertFalse(self.r.armed)
 def test_deferred_input_never_issues_before_toggle(self):
  self.enable_cap();self.arm();calls=[];original=self.t.submit_input
  def record(**k):calls.append(k);return original(**k)
  self.t.submit_input=record;self.key(Qt.Key.Key_X);self.assertEqual(calls,[]);self.assertFalse(self.r.armed)

 def test_cap_downgrade_neutralizes_before_next_ui_heartbeat(self):
  self.enable_cap();self.arm();self.t.submit_input(vx_mps=1,vy_mps=0,spin_request=dict(direction='auto',speed_dps=420))
  raw=catalog();raw['control_capabilities']=cap(self.t);raw['control_capabilities']['robots'][0]['spin_control']['available']=False
  self.t.install_catalog(raw,connection_generation=self.t._connection_generation,serial=3)
  self.assertFalse(self.t._focus);self.assertIsNone(self.t._intent['spin']);self.assertIsNone(self.t.owned_robot)
 def test_cap_missing_neutralizes_without_ui(self):
  self.enable_cap();self.arm();self.t.install_catalog(catalog(),connection_generation=self.t._connection_generation,serial=3)
  self.assertFalse(self.t._focus);self.assertIsNone(self.t.owned_robot)
 def test_old_watch_catalog_cannot_clear_current_spin(self):
  self.enable_cap();self.arm();raw=catalog();raw['control_capabilities']=cap(self.t)
  self.assertFalse(self.t.install_catalog(raw,connection_generation=self.t._connection_generation,serial=3,watch_generation=self.t._watch_generation-1));self.assertTrue(self.t._focus)
 def test_bad_config_heartbeat_clears_armed_and_keys(self):
  self.enable_cap();self.arm();self.w.registry.get('SpinMode').get_setting('Spin Speed').value=float('nan');self.r.heartbeat(self.w)
  self.assertFalse(self.r.armed);self.assertFalse(self.t._focus);self.assertFalse(any(self.w.control.key_state.values()))
 def test_expired_capability_control_worker_stops_without_heartbeat(self):
  self.enable_cap();self.arm();self.t.submit_input(vx_mps=1,vy_mps=0,spin_request=dict(direction='auto',speed_dps=420))
  self.t._spin_caps_at=self.now-2.6;self.t.control_tick();self.assertFalse(self.t._focus);self.assertIsNone(self.t._intent['spin'])

 def test_corrupt_speed_keypress_revokes_before_heartbeat(self):
  self.enable_cap();self.arm();self.w.registry.get('SpinMode').get_setting('Spin Speed').value=float('nan');self.key(Qt.Key.Key_W)
  self.assertFalse(self.r.armed);self.assertFalse(self.t._focus);self.assertFalse(any(self.w.control.key_state.values()))
 def test_corrupt_speed_tick_never_throws_or_sends_fire(self):
  self.enable_cap();self.arm();self.w.registry.get('SpinMode').get_setting('Spin Speed').value=float('inf');self.w.control.left_button_down=True
  value=self.w.control.tick(self.w.state,self.w.registry);self.assertFalse(value.left_button_down);self.assertFalse(self.r.armed);self.assertFalse(self.t._focus)
 def test_corrupt_config_release_returns_safe_snapshot(self):
  self.enable_cap();self.arm();self.w.registry.get('SpinMode').get_setting('Spin Speed').value=None
  value=self.w.control.release_all(self.w.state,self.w.registry);self.assertFalse(value.spin_mode);self.assertEqual(0,value.wz);self.assertFalse(self.t._focus)

 def test_corrupt_speed_keyrelease_revokes_before_heartbeat(self):
  self.enable_cap();self.arm();self.key(Qt.Key.Key_W);self.w.registry.get('SpinMode').get_setting('Spin Speed').value=float('nan')
  self.w.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease,Qt.Key.Key_W,Qt.KeyboardModifier.NoModifier))
  self.assertFalse(self.r.armed);self.assertFalse(self.t._focus);self.assertFalse(self.w.control.deferred_pending);self.assertFalse(any(self.w.control.key_state.values()))

 def test_valid_mode_edit_before_keyrelease_cannot_send_old_spin(self):
  self.enable_cap();self.key(Qt.Key.Key_X);self.t.process_jobs();self.arm();self.key(Qt.Key.Key_W)
  values=[];original=self.t.submit_input
  def record(**v):values.append(v);return original(**v)
  self.t.submit_input=record;self.w.registry.get('SpinMode').get_setting('Direction').value='CW'
  self.w.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease,Qt.Key.Key_W,Qt.KeyboardModifier.NoModifier))
  self.assertEqual([],values);self.assertFalse(self.r.armed);self.assertFalse(self.t._focus);self.assertFalse(self.w.control.deferred_pending)

def load_tests(loader,tests,pattern):
 return unittest.TestSuite(cls(n)for cls in (ParserTests,UI)for n,v in cls.__dict__.items()if n.startswith('test_')and callable(v))
if __name__=='__main__':unittest.main()
