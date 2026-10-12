"""Original Qt ModList against an explicitly supplied local simulation service.

The launch JSON contains argv and cwd for your separately installed bridge.
Any special scene must be named in --scene-label; this is not a full match test.
Only CPU composition of the original window is verified, not desktop OpenGL.
"""
import argparse,json,os,subprocess,sys,time,select
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--bridge-command-file',required=True,type=Path)
ap.add_argument('--out',required=True,type=Path)
ap.add_argument('--scene-label',required=True)
ap.add_argument('--aim-case',choices=('narrow','wide','both'),default='both')
ap.add_argument('--manual-pitch-pixels',type=int,default=0,help='Explicit initial mouse-only camera alignment, recorded in evidence')
a=ap.parse_args();launch=json.loads(a.bridge_command_file.read_text())
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
a.out.mkdir(parents=True,exist_ok=True);log=(a.out/'bridge.log').open('w')
child=subprocess.Popen(launch['argv'],cwd=launch['cwd'],stdout=subprocess.PIPE,stderr=log,text=True,env=os.environ.copy())
result=dict(scene=a.scene_label,render_path='original PolarRMWindow CPU QImage + actual first-person camera; not desktop/OpenGL acceptance',physical_tx=False,checks={})
w=None
try:
 if not select.select([child.stdout],[],[],30)[0]:raise RuntimeError('Bridge ready timeout')
 ready=child.stdout.readline();log.write(ready);log.flush();url=json.loads(ready)['url']
 from PySide6.QtWidgets import QApplication
 from PySide6.QtCore import Qt,QEvent,QPointF,QRectF
 from PySide6.QtGui import QImage,QPainter,QKeyEvent,QMouseEvent,QFocusEvent
 from rm_client.ui import polar_app
 from rm_client.ui.sim_operator_window import OperatorWindow
 from rm_client.sim_operator_runtime import OperatorRuntime
 app=QApplication.instance() or QApplication([])
 with ExitStack() as stack:
  for name in ('BackendManager','CameraCaptureWorker','TongjiVisionReceiver','LocalAlignedVision','TongjiVisionBridge'):
   stack.enter_context(patch.object(polar_app,name,side_effect=AssertionError('physical constructor '+name)))
  for name in ('polar_port_choices','dt7_port_choices'):
   stack.enter_context(patch('rm_client.model.'+name,side_effect=AssertionError('physical discovery '+name)))
  r=OperatorRuntime(url);trace=[];original_disarm=r.disarm
  def traced_disarm(reason='released'):
   if not trace or trace[-1]['reason']!=reason:trace.append(dict(wall=time.monotonic(),reason=reason))
   return original_disarm(reason)
  r.disarm=traced_disarm;result['client_release_trace']=trace
  w=OperatorWindow(r);w.timer.stop();w.resize(1360,900);focused=[True];w.isActiveWindow=lambda:focused[0]
  def tick(seconds):
   end=time.monotonic()+seconds
   while time.monotonic()<end:app.processEvents();w.on_tick();time.sleep(.006)
  def wait(name,predicate,timeout=15):
   end=time.monotonic()+timeout
   while time.monotonic()<end:
    tick(.025);t=r.transport.snapshot()[0]
    if t is not None and predicate(t):result['checks'][name]=True;result[name]=t;return t
   raise AssertionError(name+' timed out; '+r.note+'; '+str(trace[-12:])+': '+r.transport.last_error+' / '+r.transport.frame_error+' / '+str(r.transport.snapshot()[0]))
  def key(k,down=True):
   ev=QKeyEvent(QEvent.Type.KeyPress if down else QEvent.Type.KeyRelease,k,Qt.KeyboardModifier.NoModifier)
   (w.keyPressEvent if down else w.keyReleaseEvent)(ev)
  def right(down):
   ev=QMouseEvent(QEvent.Type.MouseButtonPress if down else QEvent.Type.MouseButtonRelease,QPointF(680,450),QPointF(680,450),Qt.MouseButton.RightButton,Qt.MouseButton.RightButton if down else Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
   (w.mousePressEvent if down else w.mouseReleaseEvent)(ev)
  def setting(module,name,value):
   s=w.registry.get(module).get_setting(name);s.value=value;w.after_setting_changed(s);tick(.05)
  def arm():
   tick(.08);key(Qt.Key.Key_F2);tick(.05);assert r.armed,r.note
  def capture(name):
   started=time.monotonic()
   w.consume_capture_frame();r.apply_state(w.state)
   image=QImage(1360,900,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.black);p=QPainter(image)
   try:assert w.paint_overlay(None,painter=p,logical_size=(1360,900),capture_module_list=True)
   finally:p.end()
   image.save(str(a.out/name));result.setdefault('cpu_capture_seconds',[]).append(dict(image=name,seconds=time.monotonic()-started));t,_,_,seq,stamp=r.transport.snapshot()
   (a.out/(name+'.json')).write_text(json.dumps(dict(frame_seq=seq,frame_sim_time=stamp,telemetry=t,feedback=r.modes.feedback(),render_path=result['render_path']),indent=2))
  wait('source_capabilities',lambda t:r.modes.support==(True,True) and r.transport.snapshot()[1] is not None)
  for i in range(8):tick(.04);capture('ready.png')
  result['startup_neutral']=r.transport.snapshot()[0]
  if a.manual_pitch_pixels:
   if not -100<=a.manual_pitch_pixels<=100:raise ValueError('Manual alignment must be bounded')
   arm();before_pitch=r.transport.snapshot()[0]['own']['pitch']
   for y in (450,450+a.manual_pitch_pixels):
    w.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove,QPointF(680,y),QPointF(680,y),Qt.MouseButton.NoButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier));tick(.04)
   tick(.6)
   result['manual_camera_alignment']=dict(pixels=a.manual_pitch_pixels,before_pitch=before_pitch,after_pitch=r.transport.snapshot()[0]['own']['pitch'],method='original Qt mouseMoveEvent only; no world/target coordinates')
   assert abs(result['manual_camera_alignment']['after_pitch']-before_pitch)>.005

  if a.aim_case in ('narrow','both'):
   setting('ArmorAutoAim','Fov',10);setting('ArmorAutoAim','Circle Size',32)
  else:
   setting('ArmorAutoAim','Fov',180);setting('ArmorAutoAim','Circle Size',480)
  w.dispatch('module_toggle','ArmorAutoAim',QPointF(),1,QRectF(0,0,100,20));tick(.05)
  for i in range(5):capture('range-ready.png');tick(.05)
  arm();right(True)
  if a.aim_case in ('narrow','both'):
   narrow=wait('narrow_source_rejection',lambda t:((t.get('autoaim') or {}).get('fov_rejected2d') or 0)>0 and not t['autoaim']['active'])
   assert not r.modes.feedback()['aim'];capture('aim-narrow.png')
  if a.aim_case=='both':
   right(False);setting('ArmorAutoAim','Fov',180);setting('ArmorAutoAim','Circle Size',480)
   assert not r.armed
   for i in range(5):capture('range-ready.png');tick(.05)
   arm();right(True)
  if a.aim_case in ('wide','both'):
   active=wait('wide_source_active',lambda t:(t.get('autoaim') or {}).get('active') and r.modes.feedback()['aim'])
   assert active['source_runtime']['source_calls']>0;assert active['camera']['native_width']==1440
   capture('aim-active.png')
  # CPU offscreen composition can exceed the real 150 ms UI watchdog. Keep
  # that stop intact; explicitly rearm before independently testing release.
  tick(.05)
  if not r.armed:
   result['cpu_capture_caused_safe_disarm']=True
   arm();right(True)
   wait('right_hold_after_explicit_rearm',lambda t:(t.get('autoaim') or {}).get('held'))
  right(False);assert not r.modes.feedback()['aim']
  wait('right_release',lambda t:(t.get('autoaim') or {}).get('reason')=='right_button_released' and not t['autoaim']['active'])
  w.dispatch('module_toggle','ArmorAutoAim',QPointF(),1,QRectF(0,0,100,20));tick(.05)
  setting('SpinMode','Spin Speed',120);setting('SpinMode','Direction','CCW');key(Qt.Key.Key_X);tick(.05)
  assert w.registry.get('SpinMode').enabled and not r.armed;arm()
  wait('ccw_real_rotation',lambda t:r.modes.feedback()['spin'] and t['operator_modes']['measured']['chassis_yaw_rate_rps']>.8)
  setting('SpinMode','Spin Speed',420);arm()
  wait('higher_actual_spin_speed',lambda t:t['operator_modes']['measured']['chassis_yaw_rate_rps']>4.)
  setting('SpinMode','Direction','CW');setting('SpinMode','Spin Speed',120);arm()
  wait('cw_real_rotation',lambda t:t['operator_modes']['measured']['chassis_yaw_rate_rps']<-.8)
  capture('spin-active.png');key(Qt.Key.Key_X);tick(.05);arm()
  wait('spin_off_braking',lambda t:not t['operator_modes']['effective']['spin_enabled'] and abs(t['operator_modes']['measured']['chassis_yaw_rate_rps'])<.2)
  focused[0]=False;w.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut));tick(.1)
  wait('focus_neutral',lambda t:not t['applied_operator_command']['fire'] and t['applied_operator_command']['yaw_torque']==0.)
  assert not r.armed and not r.modes.feedback()['spin'] and not r.modes.feedback()['aim']
  focused[0]=True;tick(.1);assert not r.armed
  key(Qt.Key.Key_F8);tick(.4);capture('modlist-supported-requests.png')
  child.terminate();child.wait(timeout=5);tick(.6)
  assert r.transport.snapshot()[0] is None and not r.armed and not any(w.control.key_state.values())
  result['checks']['disconnect_no_replay']=True;result['checks']['no_automatic_fire']=not any(v.get('applied_operator_command',{}).get('fire') for v in result.values() if isinstance(v,dict))
  result['success']=True
finally:
 if w is not None:w.close()
 if child.poll() is None:
  child.terminate()
  try:child.wait(timeout=5)
  except subprocess.TimeoutExpired:child.kill();child.wait()
 log.write(child.stdout.read());log.close();(a.out/'summary.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:result[k] for k in ('success','checks','render_path','scene')}))
