"""Opaque fixtures and mocked children only: no physics, policies, or Qt windows."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rm_client import sim_launcher as m
from rm_client.sim_viewer_readiness import observer_ready

class Clock:
 def __init__(self):self.t=0
 def __call__(self):return self.t
 def sleep(self,n):self.t+=n;time.sleep(.001)
class Child:
 def __init__(self,data=b'',exit_code=None,ignore=False):self.stdout=io.BytesIO(data);self.returncode=exit_code;self.ignore=ignore;self.calls=[]
 def poll(self):return self.returncode
 def terminate(self):self.calls.append('terminate');self.returncode=None if self.ignore else 0
 def kill(self):self.calls.append('kill');self.returncode=-9
 def wait(self,timeout):
  self.calls.append(('wait',timeout))
  if self.returncode is None:raise subprocess.TimeoutExpired('mock',timeout)
  return self.returncode

class LauncherTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='launch space 中文;');self.r=Path(self.tmp.name);self.nav=self.r/'nav';self.client=self.r/'client'
  for root,paths in [(self.nav,['rm_match/platform_v2/__main__.py','tools/platform_v2/offline_stage6/serve.py']), (self.client,['main.py','rm_client/ui/sim_platform_window.py','tools/view_sim_platform.py','rm_client/sim_viewer_readiness.py'])]:
   for rel in paths:p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# opaque test fixture\n')
  self.args=m.parser().parse_args(['--mode','manual','--nav-root',str(self.nav),'--nav-python',sys.executable,'--client-python',sys.executable,'--out',str(self.r/'out')])
 def tearDown(self):self.tmp.cleanup()
 def plan(self):return m.make_plan(self.args,self.client,host='linux',environ={})
 def native_spec(self):
  s={'operator_identity':'rmcs-sim-operator-fov-v1','sha256':{},'environment':{},'frozen_build':{},'runtime_mode_contents':'multi_ekf\n'}
  for key in ['binary','executor_yaml','autoaim_config','model']:
   p=self.r/('shenzhen-0526.onnx' if key=='model' else key)
   b=b'opaque non-executable fixture'
   if key=='binary':b=b'\x7fELF\x02\x01'+bytes(12)+b'\x3e\x00'+b'NOT_EXECUTABLE'
   p.write_bytes(b)
   if key=='model':s['model_share']=str(self.r);s['model_sha256']=m.digest(p)
   else:s[key]=str(p);s['sha256'][key]=m.digest(p)
  s['frozen_build']['binary_sha256']=s['sha256']['binary'];s['runtime_mode_sha256']=m.hashlib.sha256(b'multi_ekf\n').hexdigest();s['sha256']['runtime_mode']=s['runtime_mode_sha256']
  p=self.r/'spec.json';p.write_text(json.dumps(s));return p,s
 def test_manual_literal_argv_and_cwd(self):
  p=self.plan();self.assertEqual(p['nav_root'],str(self.nav));self.assertEqual(p['client_root'],str(self.client));self.assertIn('--sim-platform-v2',p['qt_argv']);self.assertNotIn('--native-spec',p['server_argv']);self.assertFalse((self.r/'out').exists())
 def test_default_cli_is_dry_run(self):self.assertFalse(self.args.run)
 def test_unknown_flag_rejected(self):
  with self.assertRaises(SystemExit):m.parser().parse_args(['--mod','manual'])
 def test_missing_repository_rejected(self):
  self.args.nav_root=self.r/'missing'
  with self.assertRaises(OSError):self.plan()
 def test_existing_output_rejected(self):
  self.args.out.mkdir()
  with self.assertRaises(m.LaunchError):self.plan()
 def test_manual_refuses_native(self):
  self.args.native_spec=self.r/'spec.json'
  with self.assertRaises(m.LaunchError):self.plan()
 def test_windows_offline_refused_before_io(self):
  self.args.mode='offline'
  with self.assertRaisesRegex(m.LaunchError,'Linux'):m.make_plan(self.args,self.client,host='win32',environ={})
 def test_windows_manual_no_egl(self):
  p=m.make_plan(self.args,self.client,host='win32',environ={});self.assertEqual(p['server_gl'],'glfw')
 def test_inherited_offscreen_refused(self):
  with self.assertRaises(m.LaunchError):m.make_plan(self.args,self.client,host='linux',environ={'QT_QPA_PLATFORM':'offscreen'})
 def test_nan_camera_refused(self):
  self.args.camera_fps=float('nan')
  with self.assertRaises(m.LaunchError):self.plan()
 def test_port_zero_refused(self):
  self.args.port=0
  with self.assertRaises(m.LaunchError):self.plan()
 def test_native_hash_header_only(self):
  p,s=self.native_spec();r=m.static_native_check(p);self.assertIn('not verified',r['scope'])
 def test_native_reserved_env_refused(self):
  p,s=self.native_spec();s['environment']['AUTOAIM_CONFIG']='other';p.write_text(json.dumps(s))
  with self.assertRaises(m.LaunchError):m.static_native_check(p)
 def test_native_conflicting_binary_refused(self):
  p,s=self.native_spec();s['frozen_build']['binary_sha256']='0'*64;p.write_text(json.dumps(s))
  with self.assertRaises(m.LaunchError):m.static_native_check(p)
 def test_native_bad_model_bytes_refused(self):
  p,s=self.native_spec();(self.r/'shenzhen-0526.onnx').write_bytes(b'changed')
  with self.assertRaises(m.LaunchError):m.static_native_check(p)
 def test_native_relative_path_refused(self):
  p,s=self.native_spec();s['binary']='binary';p.write_text(json.dumps(s))
  with self.assertRaises(m.LaunchError):m.static_native_check(p)
 def test_duplicate_json_refused(self):
  with self.assertRaises(m.LaunchError):m.strict_json('{"x":1,"x":2}')
 def test_nonfinite_json_refused(self):
  with self.assertRaises(m.LaunchError):m.strict_json('{"x":NaN}')
 def test_source_change_before_spawn_refused(self):
  p=self.plan();(self.client/'main.py').write_text('changed')
  with self.assertRaises(m.LaunchError):m.run_plan(p,popen=lambda *a,**k:self.fail('must not spawn'))
  self.assertFalse((self.r/'out').exists())
 def test_ready_wrong_url_refused(self):
  p=self.plan()
  with self.assertRaises(m.LaunchError):m.ready_message(b'{"url":"http://127.0.0.1:8766/?x"}',p)
 def test_pipe_partial_final_line_refused(self):
  r=m.BoundedLines(io.BytesIO(b'{"x":'));r.thread.join(1)
  with self.assertRaises(m.LaunchError):r.next()
 def test_pipe_oversized_line_refused(self):
  r=m.BoundedLines(io.BytesIO(b'x'*(m.MAX_LINE+1)+b'\n'));r.thread.join(1)
  with self.assertRaises(m.LaunchError):r.next()
 def test_pipe_queue_bounded(self):
  r=m.BoundedLines(io.BytesIO(b'{}\n'*(m.MAX_MESSAGES+1)));r.thread.join(1);self.assertLessEqual(r.q.qsize(),m.MAX_MESSAGES)
  with self.assertRaises(m.LaunchError):r.next()
 def test_child_ignores_terminate_is_killed(self):
  c=Child(ignore=True);r=m.stop_child(c);self.assertTrue(r['forced']);self.assertEqual(c.calls,['terminate',('wait',8.),'kill',('wait',2.)])
 def test_no_cleanup_ack_after_join_timeout(self):
  self.assertFalse(m.write_cleanup(self.r,'owner',{'completed':False,'quiesced':False}));self.assertFalse((self.r/'server').exists())
 def test_valid_cleanup_ack_four_fields(self):
  (self.r/'server').mkdir();self.assertTrue(m.write_cleanup(self.r,'owner',dict(completed=True,quiesced=True,session_close_ack=True)));self.assertEqual(set(json.loads((self.r/'server/driver-cleanup.json').read_text())),{'owner_session_id','quiesced','session_close_ack','cleanup_completed'})
 def test_mock_server_startup_timeout_bounded(self):
  c=Clock();child=Child();r=m.run_plan(self.plan(),popen=lambda *a,**k:child,clock=c,sleep=c.sleep);self.assertIn('timeout',r['reason']);self.assertLess(c.t,16);self.assertIn('terminate',child.calls)
 def test_mock_second_spawn_failure_reaps_server(self):
  p=self.plan();ready=dict(url=p['url'],transport='loopback_only',camera='real_opt_in',native_registered=False);child=Child(json.dumps(ready).encode()+b'\n');calls=[];c=Clock()
  def spawn(*a,**k):
   calls.append((a,k))
   if len(calls)==2:raise OSError('mock Qt missing')
   return child
  r=m.run_plan(p,popen=spawn,clock=c,sleep=c.sleep);self.assertIn('Qt missing',r['reason']);self.assertIn('terminate',child.calls);self.assertTrue(all(k['shell'] is False for a,k in calls))
 def test_mock_manual_user_close_reaps_server(self):
  p=self.plan();ready=dict(url=p['url'],transport='loopback_only',camera='real_opt_in',native_registered=False);server=Child(json.dumps(ready).encode()+b'\n');qt=Child(exit_code=0);children=iter([server,qt]);c=Clock();r=m.run_plan(p,popen=lambda *a,**k:next(children),clock=c,sleep=c.sleep);self.assertEqual(r['reason'],'viewer_closed');self.assertIn('terminate',server.calls)

 def test_server_failure_not_hidden_by_clean_viewer_exit(self):
  p=self.plan();ready=dict(url=p['url'],transport='loopback_only',camera='real_opt_in',native_registered=False)
  class Failed(Child):
   n=0
   def poll(self):
    self.n+=1
    if self.n>=2:self.returncode=1
    return self.returncode
  server=Failed(json.dumps(ready).encode()+b'\n');qt=Child(exit_code=0);children=iter([server,qt]);c=Clock()
  r=m.run_plan(p,popen=lambda *a,**k:next(children),clock=c,sleep=c.sleep);self.assertFalse(r['success']);self.assertEqual(r['reason'],'server_failed')
 def test_terminate_race_still_reaped(self):
  class Exiting(Child):
   def terminate(self):self.returncode=3;raise ProcessLookupError('gone')
  c=Exiting();r=m.stop_child(c);self.assertEqual(r['exit_code'],3);self.assertIn(('wait',8.),c.calls)

 def test_observer_episode_mismatch(self):
  with self.assertRaises(m.LaunchError):m.validate_observer_state({'episode':1},{'episode':2,'robots':[{'robot_id':'red_sentry'}]})
 def test_missing_selected_robot_rejected(self):
  with self.assertRaises(m.LaunchError):m.validate_observer_state({'episode':1},{'episode':1,'robots':[{'robot_id':'red_hero'}]})
 def test_matching_observer_identity(self):m.validate_observer_state({'episode':1},{'episode':1,'robots':[{'robot_id':'red_sentry'}]})
 def test_budget_expired_during_ready_never_spawns_viewer(self):
  p=self.plan();ready=dict(url=p['url'],transport='loopback_only',camera='real_opt_in',native_registered=False);child=Child(json.dumps(ready).encode()+b'\n');c=Clock();calls=[]
  def spawn(*a,**k):calls.append(a);c.t=p['max_wall_seconds']+1;return child
  r=m.run_plan(p,popen=spawn,clock=c,sleep=c.sleep);self.assertFalse(r['success']);self.assertEqual(len(calls),1)

class ObserverTests(unittest.TestCase):
 def valid(self):return dict(shown=True,role='spectator',session_id='s',uncertain=False,watch_robot='red_sentry',watch_epoch=1,episode=0,in_registry=True,selection_pending=False,owned_robot=None,claim_pending=False,armed=False)
 def test_no_frame_required(self):self.assertTrue(observer_ready(self.valid()))
 def test_each_safety_condition(self):
  for k,v in [('shown',False),('role','operator'),('session_id',None),('uncertain',True),('watch_robot','red_hero'),('watch_epoch',True),('episode',-1),('in_registry',False),('selection_pending',True),('owned_robot','red_sentry'),('claim_pending',True),('armed',True)]:
   with self.subTest(k=k):d=self.valid();d[k]=v;self.assertFalse(observer_ready(d))
 def test_viewer_ready_endpoint_and_authority(self):
  p={'url':'http://127.0.0.1:8766'};d=dict(event='observer_ready',schema='rm.sim.visible-observer.v1',url=p['url'],shown=True,watch_robot='red_sentry',watch_epoch=0,episode=0,role='spectator',authority='none',frame_presented=False)
  self.assertEqual(m.viewer_ready_message(json.dumps(d),p),d)
  d['authority']='gimbal'
  with self.assertRaises(m.LaunchError):m.viewer_ready_message(json.dumps(d),p)

if __name__=='__main__':unittest.main()
