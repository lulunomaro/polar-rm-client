"""Deterministic in-process protocol timing, no socket/Qt application/physics."""
from pathlib import Path
import argparse,json,sys,inspect,urllib.parse,urllib.error,io,hashlib
p=argparse.ArgumentParser();p.add_argument('--client-root',required=True,type=Path);p.add_argument('--server-root',required=True,type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
if a.out.exists():raise SystemExit('Fresh output required')
sys.path[:0]=[str(a.client_root.resolve()),str((a.client_root/'tests').resolve()),str(a.server_root.resolve())]
from rm_client.sim_platform_client import PlatformClient,ClientError
from rm_client.sim_platform_transport import PlatformTransport
from rm_match.platform_v2.control import ControlPlane,Rejected
from test_platform_adapters import catalog
from test_sim_platform_v2 import state
SCHEMA='rm.sim.platform.v2'
class Response:
 def __init__(self,result):self.bytes=json.dumps(dict(schema=SCHEMA,**result)).encode()
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self,n):return self.bytes[:n]
def run(reply_delay,send_at):
 now=[100.];server=ControlPlane(['red_infantry'],clock=lambda:now[0]+10000.);requests=[];delay=[0.]
 class Router:
  def open(self,request,timeout):
   path=urllib.parse.urlsplit(request.full_url).path;body=json.loads(request.data) if request.data else {};token=request.get_header('Authorization','').removeprefix('Bearer ')
   entry=dict(path=path,request_wall=now[0],seq=body.get('seq'),window_id=body.get('window_id'));requests.append(entry)
   try:
    if path=='/v2/sessions':r=server.open_session(body['role'],body.get('control_profile','legacy_v2'))
    elif path=='/v2/watch':r=server.watch(token,body['robot_id'])
    elif path=='/v2/claim':r=server.claim(token,body['robot_id'],body['channels'])
    elif path=='/v2/renew':r=server.renew(token,body['robot_id'],body['lease_epochs'])
    elif path=='/v2/keepalive':r=server.keepalive(token)
    elif path=='/v2/bind':r=server.bind(token,body['robot_id'],body['kind'],body['lease_epochs'],body['adapter_id'],body['config'])
    elif path=='/v2/control_window':r=server.control_window(token,body['robot_id'],body['lease_epochs'])
    elif path=='/v2/adapter_input':r=server.adapter_input(token,body['robot_id'],body['kind'],body['lease_epochs'],body['binding_epoch'],body['seq'],body['window_id'],body['valid_for_s'])
    elif path=='/v2/control':r=server.control(token,body['robot_id'],body['channel'],body['lease_epoch'],body['seq'],body['value'],body['valid_for_s'],body.get('window_id'))
    elif path=='/v2/release':r=server.release(token,body['robot_id'],body['lease_epochs'])
    elif path=='/v2/close':r=server.close_session(token)
    else:raise ValueError(path)
   except Rejected as exc:
    entry['rejected']=exc.reason
    raise urllib.error.HTTPError(request.full_url,409,'rejected',{},io.BytesIO(json.dumps({'error':exc.reason}).encode()))
   if path=='/v2/control_window':now[0]+=delay[0]
   entry['reply_wall']=now[0]
   return Response(r)
 kwargs=dict(control_profile='server_window_v1')
 if 'diagnostic_clock' in inspect.signature(PlatformClient).parameters:kwargs['diagnostic_clock']=lambda:now[0]
 client=PlatformClient('http://127.0.0.1:8766',**kwargs);client._opener=Router()
 t=PlatformTransport(role='operator',clock=lambda:now[0],client=client);t.connect_once();t.install_catalog(catalog(),connection_generation=t._connection_generation,serial=1);t.install_state(state());t.watch('red_infantry');t.process_jobs();t.install_state(state());t.video=object();t.video_at=now[0]
 t.configure_adapters({'navigation':'nav_rule_v1'},{'V2 Goal X':.75});t.request_claim();t.process_jobs();t.activate()
 def sample():t.heartbeat(True);t.submit_input(vx_mps=0,vy_mps=0,fire=False)
 sample();t.control_tick();sample();t.control_tick();now[0]=100.001;sample();t.control_tick()
 original=server.snapshot()['bindings']['red_infantry']['navigation'];original_epoch=original['epoch']
 now[0]=100.105;delay[0]=reply_delay;sample();t.control_tick();new_window=t._window['window_id'];ack=now[0]
 now[0]=send_at-.010;sample();now[0]=send_at
 error=None
 try:t.control_tick()
 except Exception as exc:
  error=str(exc)
  if 'stage' in inspect.signature(t._failed).parameters:t._failed(exc,stage='control_loop')
  else:t._failed(exc)
 actual=server.snapshot()['bindings']['red_infantry']['navigation']
 return dict(reply_delay_s=reply_delay,new_window_ack_local=ack,next_sample_local=send_at-.010,next_control_tick_local=send_at,original_activation_deadline_server=original['activation_expires'],server_clock_offset=10000.,new_window_id=new_window,client_error=error,client_uncertain=client.uncertain,client_raw_state_is_none=t.raw_state is None,original_epoch=original_epoch,final_epoch=actual['epoch'],binding_still_owned=actual['owner'] is not None,requests=requests,diagnostics=t.diagnostics() if hasattr(t,'diagnostics') else None)
fast=run(0.,100.125);slow=run(.030,100.155)
result=dict(scope='Pure deterministic original client plus actual ControlPlane with synthetic state and fake HTTP; not attribution of historical live failure',fast=fast,delayed_handoff=slow,checks={'fast_handoff_continues':fast['client_error'] is None and fast['binding_still_owned'],'delayed_handoff_safely_rejected':slow['client_error']=='binding_mismatch' and slow['client_uncertain'] and slow['client_raw_state_is_none'] and slow['final_epoch']>slow['original_epoch']},loaded_sources={str(q):hashlib.sha256(q.read_bytes()).hexdigest() for q in (a.client_root/'rm_client/sim_platform_client.py',a.client_root/'rm_client/sim_platform_transport.py',a.server_root/'rm_match/platform_v2/control.py')})
a.out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result['checks']))

if not all(result['checks'].values()):raise SystemExit(1)
