"""Explicit local simulation launch planning and bounded child supervision.

No Qt, physics, device, network, or process is constructed on import/dry-run.
This is a launcher, not a Windows/native or real-time qualification.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

MAX_LINE=65536
MAX_MESSAGES=32
STAGE7={
 'nav':{'rm_match/platform_v2/offline_lifecycle.py':'61b65a6d71d06d846f81435ef5dc29c5e0788a13920672b0d8e71094bce8e80f','tools/platform_v2/offline_stage6/serve.py':'e71d188b988d7da826197e657ebdd1b77514f26708f6f0a90ca497899dc2e9de'},
 'client':{'rm_client/sim_platform_lifecycle.py':'0bacab944cf4210c86e8a1503be1d3989af4b9737eecc07185239c2923f56357'}}
class LaunchError(ValueError):pass

def plain_file(path,limit=1024*1024*1024):
 path=Path(path).expanduser().resolve(strict=True);info=path.stat()
 if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise LaunchError('Expected bounded ordinary file: '+str(path))
 return path

def python_executable(path,limit=1024*1024*1024):
 # A venv's Python is commonly a symlink to its base interpreter. Calling the
 # resolved target loses pyvenv.cfg discovery and selects a different sys.prefix.
 # Preserve the explicit invocation path, including symlinked parent folders.
 path=Path(path).expanduser()
 if not path.is_absolute():path=Path.cwd()/path
 info=path.stat()
 if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise LaunchError('Expected bounded Python executable: '+str(path))
 if not os.access(path,os.X_OK):raise LaunchError('Python path is not executable: '+str(path))
 return path

def digest(path):
 h=hashlib.sha256()
 with plain_file(path).open('rb') as stream:
  for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()

def strict_json(data):
 def pairs(rows):
  result={}
  for k,v in rows:
   if k in result:raise LaunchError('Duplicate JSON key')
   result[k]=v
  return result
 def constant(_):raise LaunchError('Nonfinite JSON value')
 try:return json.loads(data,object_pairs_hook=pairs,parse_constant=constant)
 except (ValueError,RecursionError) as e:raise LaunchError('Invalid bounded JSON: '+str(e)) from e

def static_native_check(path):
 path=plain_file(path,1024*1024);s=strict_json(path.read_bytes())
 if type(s) is not dict or s.get('operator_identity')!='rmcs-sim-operator-fov-v1':raise LaunchError('Wrong native operator identity')
 env=s.get('environment',{})
 if type(env) is not dict or any(type(k) is not str or type(v) is not str for k,v in env.items()):raise LaunchError('Invalid native environment')
 if {'AUTOAIM_CONFIG','RMCS_HEADLESS_SHARE','ORT_DISABLE_TELEMETRY'} & set(env):raise LaunchError('Reserved native environment override')
 sha=s.get('sha256',{});build=s.get('frozen_build',{})
 if type(sha) is not dict or type(build) is not dict:raise LaunchError('Invalid native hash declarations')
 if sha.get('binary')!=build.get('binary_sha256'):raise LaunchError('Conflicting native binary hashes')
 if s.get('runtime_mode_contents')!='multi_ekf\n':raise LaunchError('Wrong native runtime mode')
 mode=hashlib.sha256(b'multi_ekf\n').hexdigest()
 if s.get('runtime_mode_sha256')!=mode or sha.get('runtime_mode')!=mode:raise LaunchError('Wrong native runtime-mode hash')
 result={}
 for key in ('binary','executor_yaml','autoaim_config','model'):
  value=(str(Path(s['model_share'])/'shenzhen-0526.onnx') if key=='model' else s[key])
  p=Path(value)
  # Original validator resolves relative paths against process cwd. This entry
  # refuses that ambiguous case instead of silently changing frozen semantics.
  if not p.is_absolute():raise LaunchError('Native spec requires explicit absolute local paths; relocate and requalify: '+key)
  actual=digest(p);expected=s.get('model_sha256') if key=='model' else sha.get(key)
  if type(expected) is not str or len(expected)!=64 or actual!=expected:raise LaunchError('Native asset hash mismatch: '+key)
  result[key]={'sha256':actual,'bytes':p.stat().st_size}
 with plain_file(s['binary']).open('rb') as f:head=f.read(20)
 if len(head)<20 or head[:4]!=b'\x7fELF' or head[4]!=2 or head[5]!=1 or int.from_bytes(head[18:20],'little')!=62:raise LaunchError('Expected qualified Linux ELF64 x86-64 binary')
 return dict(spec_sha256=digest(path),assets=result,scope='bytes/header only; shared-library/rpath/ABI availability not verified; server validator remains authoritative')

def parser():
 p=argparse.ArgumentParser(description='Explicit local SIM launcher; default dry-run, no installation or hardware',allow_abbrev=False)
 p.add_argument('--mode',choices=('manual','offline'),required=True)
 p.add_argument('--nav-root',type=Path,required=True)
 p.add_argument('--nav-python',type=Path,required=True)
 p.add_argument('--client-python',type=Path,required=True)
 p.add_argument('--out',type=Path,required=True)
 p.add_argument('--scene-json',type=Path)
 p.add_argument('--native-spec',type=Path)
 p.add_argument('--port',type=int,default=8766)
 p.add_argument('--max-wall-seconds',type=float,default=300.)
 p.add_argument('--camera-fps',type=float,default=2.)
 p.add_argument('--gl',choices=('auto','egl','glfw'),default='auto')
 p.add_argument('--run',action='store_true',help='Start only the declared local server and original Qt SIM viewer')
 return p

def make_plan(a,client_root,*,host=None,environ=None):
 host=sys.platform if host is None else host;env=os.environ if environ is None else environ
 if host not in ('win32','linux'):raise LaunchError('This launcher currently plans Linux/Windows only; other hosts unqualified')
 if a.mode=='offline' and host!='linux':raise LaunchError('OFFLINE native requires Linux; current ELF/shared-library/pipe implementation is not Windows native')
 if not 1024<=a.port<=65535:raise LaunchError('Use an explicit nonprivileged loopback port, 1024..65535')
 if not 0<a.max_wall_seconds<=3600 or not 0<a.camera_fps<=30:raise LaunchError('Wall time and camera FPS must be finite and bounded')
 if a.mode=='manual' and (a.scene_json or a.native_spec):raise LaunchError('Manual mode never accepts native/scene artifacts')
 if a.mode=='offline' and not (a.scene_json and a.native_spec):raise LaunchError('OFFLINE requires explicit scene and native spec')
 if env.get('QT_QPA_PLATFORM','').lower() in ('offscreen','minimal','minimalegl'):raise LaunchError('Visible launcher rejects offscreen/minimal Qt; clear it or use the separate qualification tool')
 if host=='win32' and (a.gl=='egl' or env.get('MUJOCO_GL','').lower()=='egl'):raise LaunchError('EGL server path is Linux-only in this launcher')
 nr=Path(a.nav_root).expanduser().resolve(strict=True);cr=Path(client_root).resolve(strict=True)
 if not nr.is_dir() or not cr.is_dir():raise LaunchError('Repository roots must be directories')
 np=python_executable(a.nav_python);cp=python_executable(a.client_python)
 out=a.out.expanduser().absolute()
 if out.exists() or out.is_symlink():raise LaunchError('Output must be a new directory')
 files={'nav_main':plain_file(nr/'rm_match/platform_v2/__main__.py'),'client_main':plain_file(cr/'main.py'),'client_sim_entry':plain_file(cr/'rm_client/ui/sim_platform_window.py')}
 url=f'http://127.0.0.1:{a.port}'
 server=[str(np),'-u','-m','rm_match.platform_v2','--camera','--port',str(a.port),'--camera-total-fps',str(a.camera_fps),'--max-wall-seconds',str(a.max_wall_seconds)]
 checks={};input_hashes={};warnings=['File/hash preflight does not prove target-interpreter dependencies, renderer or UI work.','Windows desktop and WSL/cross-machine access untested.','Built-in simplified 3v3, not real wheel-leg CAD/RL.']
 gl=a.gl if a.gl!='auto' else ('egl' if host=='linux' and not env.get('DISPLAY') else 'glfw')
 if a.mode=='offline':
  for namespace,entries in STAGE7.items():
   root=nr if namespace=='nav' else cr
   for rel,expected in entries.items():
    path=plain_file(root/rel)
    if digest(path)!=expected:raise LaunchError('Required stage7 lifecycle differs: '+namespace+'/'+rel)
    files[namespace+':'+rel]=path
  plain_file(a.scene_json,256*1024*1024);checks=static_native_check(a.native_spec)
  input_hashes={str(a.scene_json.resolve()):digest(a.scene_json),str(a.native_spec.resolve()):digest(a.native_spec)}
  files['client_visible_entry']=plain_file(cr/'tools/view_sim_platform.py')
  files['client_viewer_readiness']=plain_file(cr/'rm_client/sim_viewer_readiness.py')
  server=[str(np),'-u','-m','tools.platform_v2.offline_stage6.serve','--out',str(out/'server'),'--scene-json',str(a.scene_json.resolve()),'--native-spec',str(a.native_spec.resolve()),'--host-delay','0','--port',str(a.port)]
  warnings+=['OFFLINE is exactly one stationary, fire-off 24-exposure scene, server budget 90 s; not a full match.','Visible original Qt spectator auto-watches red_sentry; no claim or arm.','Scene content and native shared libraries/ABI are not validated by this preflight.']
 qt=[str(cp),str(cr/'main.py'),'--sim-platform-v2','--sim-role','operator' if a.mode=='manual' else 'spectator','--sim-endpoint',url]
 if a.mode=='offline':qt=[str(cp),str(cr/'tools/view_sim_platform.py'),'--sim-endpoint',url]
 return dict(schema='rm.sim.launch.plan.v1',mode=a.mode,host=host,url=url,port=a.port,nav_root=str(nr),client_root=str(cr),out=str(out),server_argv=server,qt_argv=qt,server_gl=gl,max_wall_seconds=min(a.max_wall_seconds,105.) if a.mode=='offline' else a.max_wall_seconds,source_paths={k:str(v) for k,v in files.items()},source_sha256={k:digest(v) for k,v in files.items()},input_sha256=input_hashes,native_static=checks,dependency_status='DECLARED_NOT_IMPORT_TESTED',warnings=warnings)

class BoundedLines:
 """One owned pipe reader; bounded records/memory, never supervisor readline."""
 def __init__(self,stream):
  self.stream=stream;self.q=queue.Queue(MAX_MESSAGES);self.error=None;self.eof=False
  self.thread=threading.Thread(target=self._read,daemon=True,name='sim-server-stdout');self.thread.start()
 def _read(self):
  pending=bytearray()
  try:
   while True:
    chunk=self.stream.read(4096)
    if not chunk:
     if pending:raise LaunchError('Partial final server stdout line')
     break
    pending.extend(chunk)
    while b'\n' in pending:
     line,_,tail=pending.partition(b'\n');pending=bytearray(tail)
     if len(line)>MAX_LINE:raise LaunchError('Oversized server stdout line')
     self.q.put_nowait(bytes(line))
    if len(pending)>MAX_LINE:raise LaunchError('Oversized partial server stdout line')
  except BaseException as e:self.error=type(e).__name__+': '+str(e)
  finally:self.eof=True
 def next(self):
  if self.error:raise LaunchError(self.error)
  try:return self.q.get_nowait()
  except queue.Empty:return None

def ready_message(raw,plan):
 d=strict_json(raw)
 if type(d) is not dict or d.get('url')!=plan['url']:raise LaunchError('Unexpected server ready endpoint')
 if plan['mode']=='offline':
  if d.get('clock_profile')!='offline_modeled_latency_v1' or d.get('captures')!=24:raise LaunchError('Wrong OFFLINE server profile')
  checks=plan['native_static']
  expected={'spec_sha256':checks['spec_sha256'],'source_binary_sha256':checks['assets']['binary']['sha256'],'model_sha256':checks['assets']['model']['sha256'],'scene_sha256':plan['input_sha256'][plan['server_argv'][plan['server_argv'].index('--scene-json')+1]]}
  if any(d.get(k)!=v for k,v in expected.items()):raise LaunchError('Server loaded different native/scene bytes')
 else:
  if d.get('transport')!='loopback_only' or d.get('native_registered') is not False or d.get('camera')!='real_opt_in':raise LaunchError('Wrong manual SIM server profile')
 return d

def stop_child(child,*,grace=8.):
 if child is None:return {'created':False}
 if child.poll() is not None:return {'created':True,'exit_code':child.returncode,'forced':False}
 signaled=True
 try:child.terminate()
 except ProcessLookupError:signaled=False  # It exited between poll and signal; still reap it.
 try:child.wait(timeout=grace);return {'created':True,'exit_code':child.returncode,'terminated':signaled,'forced':False}
 except subprocess.TimeoutExpired:
  try:child.kill()
  except ProcessLookupError:pass
  try:child.wait(timeout=2.);return {'created':True,'exit_code':child.returncode,'terminated':True,'forced':True}
  except subprocess.TimeoutExpired:return {'created':True,'terminated':True,'forced':True,'reaped':False}

def write_cleanup(out,owner,result):
 if not all(result.get(k) is True for k in ('completed','quiesced','session_close_ack')):return False
 target=Path(out)/'server/driver-cleanup.json';temp=target.with_suffix('.json.tmp')
 with temp.open('x',encoding='utf-8') as f:json.dump(dict(owner_session_id=owner,quiesced=True,session_close_ack=True,cleanup_completed=True),f)
 os.replace(temp,target);return True

def verify_plan_bytes(plan):
 for k,p in plan['source_paths'].items():
  if digest(p)!=plan['source_sha256'][k]:raise LaunchError('Launch source changed after planning: '+k)
 for p,h in plan['input_sha256'].items():
  if digest(p)!=h:raise LaunchError('Launch input changed after planning')

def viewer_ready_message(raw,plan):
 d=strict_json(raw)
 if (type(d) is not dict or d.get('event')!='observer_ready' or d.get('schema')!='rm.sim.visible-observer.v1'
     or d.get('url')!=plan['url'] or d.get('shown') is not True or d.get('watch_robot')!='red_sentry'
     or d.get('role')!='spectator' or d.get('authority')!='none' or d.get('frame_presented') is not False
     or type(d.get('watch_epoch')) is not int or d['watch_epoch']<0
     or type(d.get('episode')) is not int or d['episode']<0):raise LaunchError('Invalid visible spectator subscription acknowledgement')
 return d

def validate_observer_state(view_ready,state):
 if type(state) is not dict or type(state.get('episode')) is not int or state['episode']!=view_ready['episode']:
  raise LaunchError('Observer episode differs from current authority state')
 robots=state.get('robots')
 if type(robots) is not list or not any(type(r) is dict and r.get('robot_id')=='red_sentry' for r in robots):
  raise LaunchError('Selected red_sentry is absent from current registry')

def run_plan(plan,*,popen=subprocess.Popen,clock=time.monotonic,sleep=time.sleep,sdk_factory=None,lifecycle_factory=None):
 """Supervise declared children; inject mocks for all phase-v1 execution tests."""
 verify_plan_bytes(plan)
 out=Path(plan['out']);out.mkdir(parents=True,exist_ok=False)
 (out/'plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
 result={'scope':'launcher execution, not renderer/frame/native qualification','reason':'starting','success':False}
 server=qt=reader=qtreader=client=lifecycle=None;owner=None;cleanup=None;terminal=None
 env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')};env['PYTHONDONTWRITEBYTECODE']='1'
 senv=dict(env,MUJOCO_GL=plan['server_gl'],OMP_NUM_THREADS='1')
 logs=[];started=clock();deadline=started+plan['max_wall_seconds']
 def budget():
  if clock()>=deadline:raise LaunchError('Launcher total wall budget expired')
 try:
  for name in ('server-stderr.log','qt-stdout.log','qt-stderr.log'):logs.append((out/name).open('xb'))
  budget()
  server=popen(plan['server_argv'],cwd=plan['nav_root'],env=senv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=logs[0],bufsize=0,shell=False)
  reader=BoundedLines(server.stdout);ready=None
  while clock()<min(started+15,deadline):
   raw=reader.next()
   if raw is not None:ready=ready_message(raw,plan);break
   if server.poll() is not None:raise LaunchError('Server exited before ready')
   sleep(.02)
  if ready is None:raise LaunchError('Bounded server startup timeout')
  budget()
  result['ready_received']=True
  if server.poll() is not None:raise LaunchError('Server exited immediately after ready')
  budget()
  qt=popen(plan['qt_argv'],cwd=plan['client_root'],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE if plan['mode']=='offline' else logs[1],stderr=logs[2],bufsize=0,shell=False)
  if plan['mode']=='offline':
   qtreader=BoundedLines(qt.stdout);view_start=clock();view_ready=None
   while clock()<min(view_start+18,deadline):
    raw=qtreader.next()
    if raw is not None:view_ready=viewer_ready_message(raw,plan);break
    if qt.poll() is not None:raise LaunchError('Viewer exited before subscription acknowledgement')
    if server.poll() is not None:raise LaunchError('Server exited before viewer ready')
    sleep(.02)
   if view_ready is None:raise LaunchError('Bounded visible viewer startup timeout')
   budget()
   result['observer_ready']=view_ready
   if sdk_factory is None:
    from .sim_platform_client import PlatformClient
    sdk_factory=PlatformClient
   if lifecycle_factory is None:
    from .sim_platform_lifecycle import RenewalLifecycle
    lifecycle_factory=RenewalLifecycle
   budget()
   client=sdk_factory(plan['url'],timeout=.25);client.open('operator');owner=client.session_id
   budget();initial_state=client.state();budget();validate_observer_state(view_ready,initial_state)
   client.claim('red_sentry',['gimbal']);budget()
   client.bind('red_sentry','aim','aim_native_rmcs_offline_v1',{});budget()
   lifecycle=lifecycle_factory(client,'red_sentry',['gimbal']);lifecycle.observe(initial_state);lifecycle.start()
  while clock()-started<plan['max_wall_seconds']:
   raw=reader.next()
   if raw is not None:
    msg=strict_json(raw)
    if type(msg) is not dict:raise LaunchError('Invalid server message')
    if msg.get('event')=='offline_terminal':
     if lifecycle is None or terminal is not None:raise LaunchError('Unexpected terminal')
     terminal=msg;cleanup=lifecycle.close(join_timeout=1.);result['cleanup']=cleanup
     result['cleanup_ack_written']=write_cleanup(out,owner,cleanup)
    else:result['last_server_message']=msg
   if qtreader is not None:
    extra=qtreader.next()
    if extra is not None:raise LaunchError('Unexpected duplicate viewer acknowledgement')
   if lifecycle and any(x['event'] in ('renew_uncertain','renew_failure') for x in lifecycle.events()):raise LaunchError('Renewal failed; no retry')
   # A known server failure must not be hidden by a simultaneous clean UI exit.
   server_status=server.poll();qt_status=qt.poll()
   if server_status is not None:
    result['reason']='server_completed' if server.returncode==0 else 'server_failed'
    if plan['mode']=='offline' and terminal and cleanup:
     summary=strict_json(plain_file(out/'server/summary.json',1024*1024).read_bytes())
     result['server_completion']={k:summary.get(k) for k in ('state','completed_captures','lifecycle_cleanup')}
     result['success']=bool(server.returncode==0 and summary.get('state')=='COMPLETED' and summary.get('completed_captures')==24 and summary.get('lifecycle_cleanup',{}).get('confirmed') is True and cleanup.get('network_lifecycle_error_free') is True and result.get('cleanup_ack_written'))
    elif plan['mode']=='manual':result['success']=server.returncode==0
    if qt_status is not None and qt_status!=0:result['success']=False;result['viewer_failed']=True
    break
   if qt_status is not None:
    result['reason']='viewer_closed' if qt_status==0 else 'viewer_failed'
    result['success']=plan['mode']=='manual' and qt_status==0;break
   sleep(.02)
  else:result['reason']='launcher_wall_budget'
 except (Exception,KeyboardInterrupt) as e:result['reason']=type(e).__name__+': '+str(e)
 finally:
  try:
   if lifecycle is not None:
    cleanup=lifecycle.close(join_timeout=1.);result['cleanup']=cleanup;result['lifecycle_events']=lifecycle.events()
   elif client is not None:client.close()
  except BaseException as e:result['cleanup_error']=type(e).__name__+': '+str(e);result['success']=False
  for name,child,grace in [('qt',qt,3.),('server',server,10.)]:
   try:result[name+'_cleanup']=stop_child(child,grace=grace)
   except BaseException as e:result[name+'_cleanup']={'error':type(e).__name__+': '+str(e),'reaped':False};result['success']=False
  for name,r in [('server',reader),('qt',qtreader)]:
   if r is not None:
    r.thread.join(1.);result[name+'_reader_quiesced']=not r.thread.is_alive()
    if r.error:result[name+'_reader_error']=r.error;result['success']=False
    if r.thread.is_alive():result['success']=False
  for f in logs:f.close()
  result['wall_seconds']=clock()-started
  if any(result[k].get('forced') or result[k].get('reaped') is False for k in ('server_cleanup','qt_cleanup')):result['success']=False
  if result['server_cleanup'].get('exit_code') not in (None,0):result['success']=False
  qc=result['qt_cleanup']
  if qc.get('exit_code') not in (None,0) and not (qc.get('terminated') and qc.get('exit_code')==-15):result['success']=False
  (out/'launcher-result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
 return result

def main(argv=None,*,client_root=None):
 p=parser();a=p.parse_args(argv)
 try:plan=make_plan(a,client_root or Path(__file__).resolve().parents[1])
 except (ValueError,OSError,KeyError,TypeError) as e:p.error(str(e))
 print(json.dumps(plan,indent=2,ensure_ascii=False))
 if not a.run:return 0
 result=run_plan(plan);print(json.dumps(result,ensure_ascii=False));return 0 if result['success'] else 1
