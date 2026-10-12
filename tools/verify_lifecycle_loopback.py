"""No-GL loopback lifecycle checks with synthetic backend; no native algorithm."""
import sys
if not __debug__:raise SystemExit('Qualification requires Python without -O')
import argparse,json,threading,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--bridge-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(a.bridge_root.absolute())]
from rm_client.sim_platform_client import PlatformClient
from rm_client.sim_platform_lifecycle import RenewalLifecycle
from rm_match.platform_v2.runtime import PlatformRuntime
from rm_match.platform_v2.server import PlatformHTTPServer,TransportConfig
from rm_match.platform_v2.offline_lifecycle import wait_for_driver_cleanup
class Backend:
 robot_ids=('robot',);timestep=.002;sim_time=.4;episode=2
 def neutralize(self):pass
 def close(self):pass
 def display_snapshot(self):return dict(sim_time=self.sim_time,episode=self.episode,phase='running',source='synthetic_lifecycle_test',robots=[])
results=[]
for case in ('success','terminal_revocation','http_uncertain'):
 runtime=PlatformRuntime(Backend());runtime.cached=runtime.backend.display_snapshot();runtime.cached['clock_profile']={'barrier_state':'native_barrier'}
 server=PlatformHTTPServer(TransportConfig(port=0),runtime);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 client=PlatformClient('http://127.0.0.1:'+str(server.server_address[1]),timeout=.15);client.open('operator');owner=client.session_id;client.claim('robot',['gimbal']);life=RenewalLifecycle(client,'robot',['gimbal'],interval=.01);life.observe(client.state());gate=threading.Event();entered=threading.Event()
 try:
  if case=='http_uncertain':
   original=runtime.control.renew
   def slow(*args):entered.set();gate.wait(1.);return original(*args)
   runtime.control.renew=slow;life.start()
   if not entered.wait(.3):raise RuntimeError('Request did not enter actual HTTP handler')
   first=life.close(join_timeout=.01)
   if first['quiesced']:raise RuntimeError('Blocked renewal incorrectly treated as quiesced')
   time.sleep(.18)
  elif case=='terminal_revocation':runtime.close()
  else:
   life.start();deadline=time.monotonic()+.3
   while not any(x['event']=='renew_success' for x in life.events()) and time.monotonic()<deadline:time.sleep(.005)
  cleanup=life.close(join_timeout=.5)
  if not cleanup['quiesced'] or not cleanup['session_close_ack']:raise RuntimeError('Cleanup not completed')
  if cleanup['network_lifecycle_error_free']!=(case!='http_uncertain'):raise RuntimeError('Incorrect error-free classification')
  ack=a.out/(case+'-ack.json');ack.write_text(json.dumps(dict(owner_session_id=owner,quiesced=cleanup['quiesced'],session_close_ack=cleanup['session_close_ack'],cleanup_completed=cleanup['completed'])))
  handshake=wait_for_driver_cleanup(ack,owner,runtime.control,timeout=.1)
  if not handshake['confirmed']:raise RuntimeError('Cleanup handshake not confirmed')
  events=life.events();names=[x['event'] for x in events]
  if names.index('renewal_joined')>=names.index('session_close_request'):raise RuntimeError('Close order violated')
  if case=='http_uncertain' and not any(x['event']=='renew_uncertain' and x['last_known']['episode']==2 and x['last_known']['sim_time']==.4 for x in events):raise RuntimeError('Missing scoped uncertainty')
  results.append(dict(case=case,passed=True,cleanup=cleanup,handshake=handshake,events=events))
 finally:
  gate.set();life.stop_and_join(.5);runtime.close();server.shutdown();server.server_close();thread.join(1)
  if thread.is_alive():raise RuntimeError('HTTP thread failed bounded join')
(a.out/'RESULT.json').write_text(json.dumps(dict(passed=True,scope='synthetic_backend_real_loopback_no_GL_no_native',cases=results),indent=2));print(json.dumps(dict(passed=True,cases=len(results))))
