"""Fixed two-run real-camera OFFLINE/SIL + original Qt CPU display qualification.

Explicit algorithm-lease renewal belongs to this test driver. No simulated fire
or manual motion is requested. Real-time timing is not qualified by this entry.
"""
import sys
if not __debug__:raise SystemExit('Qualification requires Python without -O')
import argparse,copy,hashlib,json,os,select,subprocess,threading,time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
p=argparse.ArgumentParser();p.add_argument('--bridge-root',type=Path,required=True);p.add_argument('--bridge-python',type=Path,required=True);p.add_argument('--scene-json',type=Path,required=True);p.add_argument('--native-spec',type=Path,required=True);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
args.out.mkdir(parents=True,exist_ok=False);root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QPointF,QRectF
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication
from rm_client.ui import polar_app
from rm_client.ui.sim_platform_window import PlatformWindow
from rm_client.sim_platform_runtime import PlatformRuntime
from rm_client.sim_platform_client import PlatformClient
from rm_client.sim_robot_selection import SELECTION_SETTING
app=QApplication.instance() or QApplication(['POLAR-OFFLINE-SIL-qualification'])
def source_hashes():return {str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((root/'rm_client').rglob('*.py'))}
source=source_hashes();source['tools/verify_offline_platform.py']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();(args.out/'client-loaded-source.json').write_text(json.dumps(source,indent=2))
results=[]
for delay in (0.,.15):
 out=args.out/('host-000' if delay==0 else 'host-150');out.mkdir();env=os.environ.copy();env.update(PYTHONPATH=str(args.bridge_root.absolute()),MUJOCO_GL='egl',LIBGL_ALWAYS_SOFTWARE='1',MESA_SHADER_CACHE_DISABLE='true')
 log=(out/'server-stderr.log').open('x');child=subprocess.Popen([str(args.bridge_python.absolute()),str(args.bridge_root.absolute()/'tools/platform_v2/offline_stage6/serve.py'),'--out',str(out.absolute()/'server'),'--scene-json',str(args.scene_json.absolute()),'--native-spec',str(args.native_spec.absolute()),'--host-delay',str(delay)],env=env,stdout=subprocess.PIPE,stderr=log,text=True)
 result=dict(host_delay_s=delay,passed=False,checks=[],captures=[],scope='offline_SIL_original_Qt_CPU_display_not_realtime_or_Windows');results.append(result)
 operator=None;window=None;stop=threading.Event();renew=None;renew_errors=[];renew_receipts=[]
 def check(name,value):
  result['checks'].append(dict(name=name,passed=bool(value)))
  if not value:raise RuntimeError('Check failed: '+name)
 try:
  if not select.select([child.stdout],[],[],12)[0]:raise RuntimeError('Offline server startup timeout')
  line=child.stdout.readline();ready=json.loads(line);result['ready']=ready;url=ready['url']
  operator=PlatformClient(url,timeout=.5);operator.open('operator');operator.claim('red_sentry',['gimbal']);operator.bind('red_sentry','aim','aim_native_rmcs_offline_v1',{})
  def renewal():
   while not stop.wait(.15):
    try:
     now=time.monotonic();operator.renew('red_sentry',['gimbal']);renew_receipts.append(dict(request_wall=now,ack_wall=time.monotonic()))
    except BaseException as e:renew_errors.append(str(e));return
  renew=threading.Thread(target=renewal,daemon=True);renew.start()
  with ExitStack() as stack:
   for name in ('BackendManager','CameraCaptureWorker','TongjiVisionReceiver','LocalAlignedVision','TongjiVisionBridge'):
    stack.enter_context(patch.object(polar_app,name,side_effect=AssertionError('Physical constructor called: '+name)))
   for name in ('polar_port_choices','dt7_port_choices'):stack.enter_context(patch('rm_client.model.'+name,side_effect=AssertionError('Physical discovery called: '+name)))
   view=PlatformRuntime(url,role='spectator');window=PlatformWindow(view);window.timer.stop();window.resize(1360,900);window.isActiveWindow=lambda:True
   chosen=False;captured=False;started=time.monotonic()
   while child.poll() is None:
    app.processEvents();window.on_tick();t=view.transport
    if not chosen and 'red_sentry' in t.robot_ids:
     setting=window.registry.get('RobotProfile').get_setting(SELECTION_SETTING);window.dispatch('jello_mode_select',(setting,'red_sentry'),QPointF(),1,QRectF(0,0,100,20));chosen=True
    with t._lock:state=copy.deepcopy(t.raw_state);frame=t.video is not None
    if not captured and frame and state and state.get('completed_captures',0)>=12:
     window.consume_capture_frame();view.apply_state(window.state)
     # Snapshot the actual received presentation inputs immediately before paint.
     with t._lock:
      telemetry,video,stamp,seq,simstamp=t.snapshot();receipt=dict(watch_epoch=t.watch_epoch,watch_robot=t.watch_robot,frame_seq=seq,capture_sim_time=simstamp,frame_receive_wall=stamp,render_wall=time.monotonic(),clock_profile=copy.deepcopy(t.raw_state.get('clock_profile')),episode=t.raw_state['episode'],native_size=t.video_native_size,received_bgr_sha256=hashlib.sha256(video.tobytes()).hexdigest())
     image=QImage(1360,900,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.black);painter=QPainter(image)
     try:painted=window.paint_overlay(None,painter=painter,logical_size=(1360,900),capture_module_list=True)
     finally:painter.end()
     check('original_Qt_overlay_painted',painted);check('image_saved',image.save(str(out/'offline-observer.png')))
     receipt.update(file='offline-observer.png',sha256=hashlib.sha256((out/'offline-observer.png').read_bytes()).hexdigest());result['captures'].append(receipt);captured=True
     check('offline_label_received',receipt['clock_profile']['kind']=='offline_modeled_latency_v1')
     check('spectator_has_no_control',not view.armed and t.owned_robot is None)
     check('physical_backends_disconnected',not window.backend.serial.connected and not window.backend.mqtt.connected and not window.backend.publish_keyboard_mouse()[0])
    if time.monotonic()-started>100:raise RuntimeError('Outer bounded attempt timeout')
    time.sleep(.008)
   stop.set();renew.join(1);check('server_exit_zero',child.returncode==0)
   result['server_stdout_tail']=child.stdout.read();summary=json.loads((out/'server/summary.json').read_text());result['server_summary']={k:v for k,v in summary.items() if k not in ('source','final_state')}
   check('all_24_exposures_complete',summary['state']=='COMPLETED' and summary['completed_captures']==24)
   check('actual_original_Qt_frame_captured',captured);check('source_unchanged',dict(source_hashes(),**{'tools/verify_offline_platform.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})==source)
   result['passed']=True
 except BaseException as e:
  result['error']=type(e).__name__+':'+str(e)
 finally:
  stop.set()
  if renew:renew.join(1)
  if window:window.close()
  if operator:
   try:operator.close()
   except Exception:pass
  if child.poll() is None:
   child.terminate()
   try:child.wait(8)
   except subprocess.TimeoutExpired:child.kill();child.wait()
  log.close();result['renewal_receipts']=renew_receipts;result['renewal_errors']=renew_errors
  (out/'qualification.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
 print(json.dumps(dict(host_delay_s=delay,passed=result['passed'],error=result.get('error'))),flush=True)
 if not result['passed']:break
(args.out/'qualification.json').write_text(json.dumps(dict(runs=results,passed=len(results)==2 and all(r['passed'] for r in results)),indent=2,allow_nan=False))
if len(results)!=2 or not all(r['passed'] for r in results):raise SystemExit(1)
