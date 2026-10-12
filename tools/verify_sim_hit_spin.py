"""Original Qt AutoSpinOnHit against an explicit physical-projectile QA service.

The helper, not this client, owns a clearly named test scene and one test shot.
This script never modifies HP, calls a judge hit API or introduces an auto-fire
command. Real renderer samples are used for the client input safety gate.
"""
import argparse,json,os,sys,time,subprocess,select,hashlib
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
if not __debug__:
 raise SystemExit('Refusing optimized Python: run without -O or PYTHONOPTIMIZE; validation assertions are required.')
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--client-root',type=Path,required=True)
p.add_argument('--bridge-command-file',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
a=p.parse_args();sys.path.insert(0,str(a.client_root.absolute()))
# Refuse existing paths before reading the launch description or starting a bridge.
# mkdir itself is exclusive, including dangling symlinks, to avoid a check/use race.
try:a.out.mkdir(parents=True,exist_ok=False)
except FileExistsError:p.error('--out must name a fresh, nonexistent directory')
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
launch=json.loads(a.bridge_command_file.read_text());log=(a.out/'bridge.log').open('w')
child=subprocess.Popen(launch['argv'],cwd=launch['cwd'],stdout=subprocess.PIPE,stderr=log,text=True,env=os.environ.copy())
result=dict(scope='Original Qt + real physical simulated projectile; no desktop/OpenGL qualification',checks={},trace=[],physical_tx=False)
w=None
try:
 if not select.select([child.stdout],[],[],30)[0]:raise RuntimeError('Bridge ready timeout')
 line=child.stdout.readline();log.write(line);log.flush();ready=json.loads(line);result['bridge_ready_identity']=ready
 trigger=Path(ready['trigger_file']);assert trigger.is_absolute() and not trigger.exists()
 from PySide6.QtWidgets import QApplication
 from PySide6.QtCore import Qt,QEvent,QRectF
 from PySide6.QtGui import QKeyEvent,QFocusEvent,QImage,QPainter
 from rm_client.ui import polar_app
 from rm_client.ui.sim_operator_window import OperatorWindow
 from rm_client.sim_operator_runtime import OperatorRuntime
 app=QApplication.instance() or QApplication([])
 with ExitStack() as stack:
  for name in ('BackendManager','CameraCaptureWorker','TongjiVisionReceiver','LocalAlignedVision','TongjiVisionBridge'):
   stack.enter_context(patch.object(polar_app,name,side_effect=AssertionError('physical constructor '+name)))
  for name in ('polar_port_choices','dt7_port_choices'):
   stack.enter_context(patch('rm_client.model.'+name,side_effect=AssertionError('physical discovery '+name)))
  r=OperatorRuntime(ready['url']);w=OperatorWindow(r);w.timer.stop();w.resize(1360,900);focused=[True];w.isActiveWindow=lambda:focused[0]
  w.registry.get('AudioFeedback').enabled=False
  def tick(seconds):
   end=time.monotonic()+seconds
   while time.monotonic()<end:
    app.processEvents();w.on_tick();t=r.transport.snapshot()[0]
    if t is not None:
     result['trace'].append(dict(wall=time.monotonic(),sim_time=t['sim_time'],hp=t['own']['hp'],own_damage=t.get('own_damage'),applied=t.get('applied_operator_command'),feedback=r.modes.feedback(),armed=r.armed,policy_reason=r.hit_spin.reason,auto_selected=r.hit_spin.origin,requested=dict(r.modes.mode)))
    time.sleep(.006)
  def wait(name,predicate,timeout=20):
   end=time.monotonic()+timeout
   while time.monotonic()<end:
    tick(.025);t=r.transport.snapshot()[0]
    if t is not None and predicate(t):result['checks'][name]=True;result[name]=t;return t
   raise AssertionError(name+' timed out: '+r.note+' '+r.transport.last_error)
  def key(k):w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress,k,Qt.KeyboardModifier.NoModifier))
  wait('fresh_camera_and_damage_capability',lambda t:r.hit_spin.supported and r.transport.snapshot()[1] is not None)
  # Warm the original HUD only while disarmed. No active-lease watchdog is
  # bypassed to make screenshots appear successful.
  for _ in range(3):
   w.consume_capture_frame();image=QImage(1360,900,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.black);painter=QPainter(image)
   try:w.paint_overlay(None,painter=painter,logical_size=(1360,900),capture_module_list=True)
   finally:painter.end()
   tick(.05)
  feature=w.registry.get('AutoSpinOnHit');w.registry.toggle_module(feature,source='clickgui');tick(.12)
  key(Qt.Key.Key_F2);tick(.10);assert r.armed
  baseline=wait('armed_baseline',lambda t:r.armed and r.transport._focus and not r.transport._neutral_pending and r.hit_spin.consumer._last is not None and not w.registry.get('SpinMode').enabled)
  before=baseline['own']['hp'];initial_total=baseline['own_damage']['total_damage']
  result['trigger_written_at']=dict(wall=time.monotonic(),sim_time=baseline['sim_time'])
  trigger.write_text('FIRE_ONE_TEST_SHOT')
  damaged=wait('physical_damage_observed',lambda t:t['own_damage']['total_damage']>initial_total and t['own']['hp']<before)
  credit=damaged['own_damage']['total_damage']-initial_total
  assert abs((before-damaged['own']['hp'])-credit)<1e-8
  result['observed_credit']=credit
  spinning=wait('on_hit_spin_measured',lambda t:r.armed and r.modes.feedback()['spin'] and r.modes.feedback()['spin_origin']=='on_hit' and abs(t['operator_modes']['measured']['chassis_yaw_rate_rps'])>.8)
  until=r.hit_spin.guard.active_until;result['policy_active_until_sim_time']=until
  assert until>spinning['sim_time']
  expired=wait('sim_time_expiry_and_braking',lambda t:t['sim_time']>=until and not t['operator_modes']['effective']['spin_enabled'] and not w.registry.get('SpinMode').enabled and abs(t['operator_modes']['measured']['chassis_yaw_rate_rps'])<.2)
  assert r.armed
  # No second hit and no replay from the repeated cumulative counter.
  tick(.15);assert not w.registry.get('SpinMode').enabled;result['checks']['duplicate_counter_no_retrigger']=True
  focused[0]=False;w.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut));tick(.10)
  wait('focus_neutral',lambda t:not r.armed and not t['applied_operator_command']['fire'] and t['applied_operator_command']['yaw_torque']==0.)
  focused[0]=True;tick(.05);assert not r.armed
  # Capture only after the physical chain and safe stop. Record distinct
  # before/after snapshots; never label post-save data as exact paint time.
  w.consume_capture_frame();r.apply_state(w.state);pre=r.transport.snapshot()[0]
  image=QImage(1360,900,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.black);painter=QPainter(image)
  try:assert w.paint_overlay(None,painter=painter,logical_size=(1360,900),capture_module_list=True)
  finally:painter.end()
  image.save(str(a.out/'after-physical-hit-safe-stop.png'))
  (a.out/'after-physical-hit-safe-stop.json').write_text(json.dumps(dict(pre_paint_telemetry=pre,post_save_telemetry=r.transport.snapshot()[0],render_path='Original PolarRMWindow CPU HUD with actual camera; already disarmed; not desktop/OpenGL'),indent=2))
  child.terminate();child.wait(timeout=5);tick(.6)
  assert r.transport.snapshot()[0] is None and not r.armed
  result['checks']['disconnect_no_resume']=True
  result['checks']['controlled_robot_never_fired']=all(not (row['applied'] or {}).get('fire') for row in result['trace'])
  assert result['checks']['controlled_robot_never_fired']
  result['success']=True
finally:
 if w is not None:w.close()
 if child.poll() is None:
  child.terminate()
  try:child.wait(timeout=5)
  except subprocess.TimeoutExpired:child.kill();child.wait()
 log.write(child.stdout.read());log.close();(a.out/'summary.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:result[k] for k in ('success','checks','scope')}))
