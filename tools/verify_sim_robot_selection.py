"""Bounded real service plus original Qt SIM Vehicle action; always manual fire off."""
import argparse,json,os,sys,time,subprocess,select,hashlib
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
if not __debug__:raise SystemExit('Run without Python optimization')
p=argparse.ArgumentParser();p.add_argument('--launch',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
root=Path.cwd();source_hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root/'rm_client').rglob('*.py'))}
launch=json.loads(a.launch.read_text());log=(a.out/'bridge.log').open('w');child=subprocess.Popen(launch['argv'],cwd=launch['cwd'],env=dict(os.environ,**launch.get('env',{})),stdout=subprocess.PIPE,stderr=log,text=True)
result=dict(client_source_sha256=source_hashes,runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),scope='Bounded original Qt dispatch/input + actual selected robot cameras and sim telemetry; CPU screenshots, not desktop/GPU',checks={},transitions=[],physical_tx=False,manual_fire=False);w=None
try:
 if not select.select([child.stdout],[],[],30)[0]:raise RuntimeError('Bridge ready timeout')
 ready=json.loads(child.stdout.readline());result['ready']=ready
 from PySide6.QtWidgets import QApplication
 from PySide6.QtCore import Qt,QEvent,QPointF,QRectF
 from PySide6.QtGui import QKeyEvent,QMouseEvent,QImage,QPainter
 from rm_client.ui import polar_app
 from rm_client.ui.sim_operator_window import OperatorWindow
 from rm_client.sim_operator_runtime import OperatorRuntime
 from rm_client.sim_robot_selection import SELECTION_SETTING
 assert source_hashes=={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root/'rm_client').rglob('*.py'))},'Client changed during import'
 app=QApplication([])
 with ExitStack() as stack:
  for name in ('BackendManager','CameraCaptureWorker','TongjiVisionReceiver','LocalAlignedVision','TongjiVisionBridge'):stack.enter_context(patch.object(polar_app,name,side_effect=AssertionError('physical constructor '+name)))
  for name in ('polar_port_choices','dt7_port_choices'):stack.enter_context(patch('rm_client.model.'+name,side_effect=AssertionError('physical discovery '+name)))
  r=OperatorRuntime(ready['url']);w=OperatorWindow(r);w.timer.stop();w.resize(1360,900);w.isActiveWindow=lambda:True
  w.registry.get('AudioFeedback').enabled=False
  def tick(seconds):
   end=time.monotonic()+seconds
   while time.monotonic()<end:
    app.processEvents();w.on_tick();time.sleep(.007)
  def wait(label,condition,timeout=15):
   end=time.monotonic()+timeout
   while time.monotonic()<end:
    tick(.025);t=r.transport.snapshot()[0]
    if condition(t):return t
   raise AssertionError(label+' timed out: '+r.note+' / '+r.transport.last_error+' / '+r.transport.frame_error)
  def key(k,down=True):
   e=QKeyEvent(QEvent.Type.KeyPress if down else QEvent.Type.KeyRelease,k,Qt.KeyboardModifier.NoModifier)
   (w.keyPressEvent if down else w.keyReleaseEvent)(e)
  initial=wait('initial camera/session',lambda t:t is not None and r.transport.snapshot()[1] is not None and bool(w.registry.get('RobotProfile').get_setting(SELECTION_SETTING).options))
  # Reuse the previously qualified visible-target manual alignment: one
  # actual Qt 12-pixel downward mouse motion, never modifying fixture poses.
  key(Qt.Key.Key_F2);tick(.05);assert r.armed,r.note
  for y in (450,462):
   event=QMouseEvent(QEvent.Type.MouseMove,QPointF(680,y),QPointF(680,y),Qt.MouseButton.NoButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
   w.mouseMoveEvent(event);tick(.03)
  tick(.5)
  armor=w.registry.get('ArmorAutoAim')
  for name,value in [('Fov',180),('Circle Size',480)]:
   setting=armor.get_setting(name);setting.value=value;w.after_setting_changed(setting)
  w.registry.toggle_module(armor,source='clickgui')
  tick(.10);key(Qt.Key.Key_F2);tick(.05);assert r.armed,r.note
  event=QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(680,450),QPointF(680,450),Qt.MouseButton.RightButton,Qt.MouseButton.RightButton,Qt.KeyboardModifier.NoModifier)
  w.mousePressEvent(event)
  initial_active=wait('initial actual source authority',lambda t:t is not None and r.modes.feedback()['aim'],timeout=20)
  result['initial_source_active']=initial_active
  result['checks']['actual_source_active_before_selection']=True
  initial_session=r.transport.session['session_id'];previous_t=initial['sim_time'];initial_epoch=r.transport.session['epoch']
  setting=w.registry.get('RobotProfile').get_setting(SELECTION_SETTING)
  for index in range(6):
   old=dict(r.transport.session);old_t=r.transport.snapshot()[0]
   if not r.armed:key(Qt.Key.Key_F2);tick(.05)
   assert r.armed,r.note
   if index:
    key(Qt.Key.Key_W);tick(.04);assert w.control.key_state['W']
   options=list(setting.options);target=options[(options.index(old['robot_id'])+1)%len(options)]
   w.dispatch('jello_mode_select',(setting,target),QPointF(0,0),1,QRectF(0,0,100,20))
   assert not r.armed and r.transport.selection_pending
   assert r.transport.snapshot()[:2]==(None,None)
   assert not any(w.control.key_state.values()) and not w.control.left_button_down and not w.control.right_button_down
   assert w.frame_image is None and w.frame_pixmap is None
   assert not r.arm()
   t=wait('selected '+target,lambda t:t is not None and t['robot_id']==target and r.transport.session['epoch']==old['epoch']+1 and r.transport.selection_ready())
   assert not r.armed
   assert t['session_id']==initial_session and t['sim_time']>=previous_t
   assert r.transport.session['robot_id']==target and r.transport.session['first_person_camera']==target+'__aim'
   assert r.transport.snapshot()[1] is not None and r.transport.video_sequence>=0
   assert not r.transport.install_telemetry(old_t,old)
   assert setting.value==target
   expected_source=target=='red_sentry'
   assert r.modes.support[1]==expected_source,(target,r.modes.support,r.transport.session)
   for name in ('SpinMode','ArmorAutoAim','AutoSpinOnHit'):assert not w.registry.get(name).enabled
   key(Qt.Key.Key_F2);tick(.05);assert r.armed,r.note
   wait('new identity active control',lambda x:x is not None and x['control']['state']=='active')
   current=r.transport.snapshot()[0];assert not current['applied_operator_command']['fire']
   record=dict(old_identity={k:old[k] for k in ('session_id','epoch','robot_id')},selected_identity={k:r.transport.session[k] for k in ('session_id','epoch','robot_id')},source_supported=expected_source,telemetry=current,camera_sequence=r.transport.video_sequence,camera_native_size=r.transport.video_native_size,settings_value=setting.value)
   result['transitions'].append(record);previous_t=t['sim_time']
   key(Qt.Key.Key_F12);tick(.035);assert not r.armed
  result['checks'].update(original_jello_setting_dispatch=True,six_vehicle_cycle=True,same_match_no_reset=True,selection_clears_inputs_and_old_frame=True,new_epoch_and_neutral_ack=True,no_automatic_rearm=True,old_telemetry_rejected=True,source_capability_red_sentry_only=True,manual_fire_stayed_off=True)
  wait('final fresh snapshot',lambda t:t is not None and r.transport.snapshot()[1] is not None)
  w.consume_capture_frame();r.apply_state(w.state)
  captured=r.transport.snapshot()
  with patch.object(r.transport,'snapshot',return_value=captured):
   for _ in range(2):
    image=QImage(1360,900,QImage.Format_ARGB32_Premultiplied);image.fill(Qt.black);painter=QPainter(image)
    try:assert w.paint_overlay(None,painter=painter,logical_size=(1360,900),capture_module_list=True)
    finally:painter.end()
   image.save(str(a.out/'selection-cycle-safe-stop.png'))
  result['final_snapshot']=captured[0];result['checks']['final_safe_stop']=not r.armed
  assert source_hashes=={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root/'rm_client').rglob('*.py'))},'Client changed during QA'
  result['success']=True
finally:
 if w:w.close()
 if child.poll() is None:child.terminate()
 try:child.wait(timeout=5)
 except subprocess.TimeoutExpired:child.kill();child.wait()
 log.write(child.stdout.read());log.close();(a.out/'summary.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:result[k] for k in ('checks','success','scope')}))
