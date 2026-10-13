"""Pure CPU event/cadence tests; no Qt application, network, MuJoCo or renderer."""
import io,json,threading,unittest,urllib.error
from rm_client.sim_platform_diagnostics import DiagnosticLog
from rm_client.sim_platform_client import PlatformClient,ClientError
from rm_client.sim_platform_transport import PlatformTransport
from test_platform_adapters import AdapterClient,catalog
from test_sim_platform_v2 import state

class Response:
 def __init__(self,body):self.body=body
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self,n):return self.body[:n]
class Opener:
 def __init__(self,answer):self.answer=answer
 def open(self,request,timeout):
  value=self.answer(request)
  if isinstance(value,Exception):raise value
  return Response(json.dumps(value).encode())

class DiagnosticTests(unittest.TestCase):
 def test_bounded_ring_and_immutable_first_failure(self):
  clock=[1.];log=DiagnosticLog(clock=lambda:clock[0],capacity=8)
  first=log.record('failure',failure=True,stage='control_loop');first['stage']='edited'
  for i in range(40):clock[0]+=.1;log.record('ok',request_id=i)
  d=log.snapshot();self.assertEqual(len(d['events']),8);self.assertEqual(d['total_events'],41);self.assertEqual(d['first_failure']['stage'],'control_loop')
  d['first_failure']['stage']='external';self.assertEqual(log.snapshot()['first_failure']['stage'],'control_loop')
 def test_forbidden_payload_url_and_nonfinite_not_saved(self):
  log=DiagnosticLog();log.record('x',token='SECRET',Authorization='SECRET',url='http://u:p@x',body={'secret':'SECRET'},path='http://u:p@x',sample_age_wall_s=float('nan'),thread='t'*1000)
  data=log.snapshot();text=json.dumps(data,allow_nan=False);self.assertNotIn('SECRET',text);self.assertNotIn('u:p',text);self.assertIsNone(data['events'][0]['thread']);self.assertIsNone(data['events'][0]['sample_age_wall_s'])
 def client(self,answer):
  c=PlatformClient('http://127.0.0.1:8766',diagnostic_clock=lambda:42.);c._token='SENSITIVE_BEARER';c.session_id='sid';c._opener=Opener(answer);return c
 def test_http_rejection_keeps_reason_and_request_identity_without_token(self):
  c=self.client(lambda req:urllib.error.HTTPError(req.full_url,409,'denied',{},io.BytesIO(b'{"error":"binding_mismatch"}')))
  with self.assertRaises(ClientError) as ctx:c._request('/v2/adapter_input',{'robot_id':'r','kind':'navigation','binding_epoch':3,'seq':7,'window_id':2,'lease_epochs':{'motion':1}})
  d=c.diagnostics();f=d['first_failure'];self.assertEqual(f['wall_monotonic'],42.);self.assertEqual(f['reason_code'],'binding_mismatch');self.assertEqual(f['seq'],7);self.assertEqual(ctx.exception.request_id,1);self.assertFalse(c.uncertain);self.assertNotIn('SENSITIVE_BEARER',json.dumps(d))
 def test_noncanonical_server_error_is_redacted(self):
  c=self.client(lambda req:urllib.error.HTTPError(req.full_url,409,'denied',{},io.BytesIO(b'{"error":"https://u:SECRET@host/?token=SECRET"}')))
  with self.assertRaises(ClientError):c._request('/v2/claim',{'robot_id':'r'})
  self.assertEqual(c.diagnostics()['first_failure']['reason_code'],'unclassified_error');self.assertNotIn('SECRET',json.dumps(c.diagnostics()))
 def test_uncertain_exception_keeps_stage_without_arbitrary_message(self):
  c=self.client(lambda req:OSError('https://SECRET@private'))
  with self.assertRaises(ClientError):c.state()
  d=c.diagnostics();self.assertTrue(c.uncertain);self.assertEqual(d['first_failure']['stage'],'response_unverifiable');self.assertNotIn('SECRET',json.dumps(d))
 def test_bad_operation_ack_is_associated_with_that_request(self):
  c=self.client(lambda req:{'schema':'rm.sim.platform.v2','session_id':'wrong','session_expires':123})
  with self.assertRaises(ClientError):c.keepalive()
  self.assertEqual(c.diagnostics()['first_failure']['request_id'],1);self.assertEqual(c.diagnostics()['first_failure']['stage'],'operation_ack_validation')
 def test_close_or_new_success_does_not_erase_first_failure(self):
  c=self.client(lambda req:OSError('bad'))
  with self.assertRaises(ClientError):c.state()
  old=c.diagnostics()['first_failure'];c._opener=Opener(lambda req:{'schema':'rm.sim.platform.v2','closed':True});c.close()
  self.assertEqual(c.diagnostics()['first_failure'],old)
 def transport(self):
  self.now=100.;c=AdapterClient();t=PlatformTransport(role='operator',clock=lambda:self.now,client=c)
  t.connect_once();t.install_catalog(catalog(),connection_generation=t._connection_generation,serial=1);t.install_state(state());t.watch('red_infantry');t.process_jobs();t.install_state(state());t.video=object();t.video_at=100.;return t
 def test_failure_preserves_known_sample_not_current_guessed_sim(self):
  t=self.transport();sample=t.raw_state['sim_time'];self.now=100.125
  t._failed(ClientError('untrusted SECRET',request_id=9,code='binding_mismatch'),stage='control_loop')
  f=t.diagnostics()['transport']['first_failure'];self.assertEqual(f['episode'],0);self.assertEqual(f['sample_sim_time'],sample);self.assertEqual(f['sample_received_wall'],100.);self.assertEqual(f['sample_age_wall_s'],.125);self.assertEqual(f['request_id'],9);self.assertIsNone(t.raw_state);self.assertNotIn('SECRET',json.dumps(t.diagnostics()))
 def test_unknown_state_is_null_and_reconnect_does_not_erase_old_failure(self):
  t=PlatformTransport(role='operator',client=AdapterClient(),clock=lambda:7.)
  t._failed(ValueError('bad'),stage='session_open');first=t.diagnostics()['transport']['first_failure'];self.assertIsNone(first['episode']);self.assertIsNone(first['sample_sim_time']);self.assertIsNone(first['sample_age_wall_s'])
  t.client=AdapterClient();t.connect_once();self.assertEqual(t.diagnostics()['transport']['first_failure'],first)
 def test_stop_retains_prior_failure_and_does_not_log_credentials(self):
  t=self.transport();t._failed(ConnectionError('secret'),stage='state_loop');first=t.diagnostics()['transport']['first_failure'];t.stop();self.assertEqual(t.diagnostics()['transport']['first_failure'],first);self.assertEqual(t.diagnostics()['transport']['events'][-1]['event'],'stop_close_attempt_finished')
 def test_late_window_cancel_stage_kept_without_url(self):
  t=self.transport();t.neutral('Input window arrived too late; http://SECRET')
  row=t.diagnostics()['transport']['events'][-1];self.assertEqual(row['reason_code'],'window_ack_late');self.assertNotIn('SECRET',json.dumps(row))
 def test_recursive_or_string_lease_identity_does_not_recurse_or_leak(self):
  log=DiagnosticLog();cycle={};cycle['motion']=cycle
  for value in (cycle,{'motion':'SECRET'},{'motion':True},{'motion':1.0}):
   row=log.record('x',lease_epochs=value);self.assertIsNone(row['lease_epochs'])
  self.assertNotIn('SECRET',json.dumps(log.snapshot()))
 def test_broken_diagnostic_record_cannot_prevent_neutral_failure_clear(self):
  t=self.transport();t.request_claim();t.process_jobs();t.activate()
  def bad(*args,**kwargs):raise RuntimeError('logging failed')
  t._diagnostics.record=bad;t._failed(ClientError('bad'),stage='control_loop')
  self.assertFalse(t._focus);self.assertIsNone(t.owned_robot);self.assertIsNone(t.raw_state);self.assertTrue(t.client.uncertain);self.assertGreater(t.diagnostics()['transport']['dropped_events'],0)
 def test_broken_diagnostic_clock_cannot_prevent_neutral_failure_clear(self):
  t=self.transport();t.request_claim();t.process_jobs();t.activate()
  def bad():raise RuntimeError('clock failed')
  t.clock=bad;t._failed(ClientError('bad'),stage='control_loop');self.assertFalse(t._focus);self.assertIsNone(t.owned_robot);self.assertIsNone(t.raw_state)
  first=t.diagnostics()['transport']['first_failure'];self.assertIsNotNone(first);self.assertEqual(first['episode'],0);self.assertIsNone(first['sample_age_wall_s'])
 def test_clock_failure_log_remains_serializable(self):
  def bad():raise RuntimeError('clock')
  log=DiagnosticLog(clock=bad);row=log.record('x',failure=True);self.assertIsNone(row['wall_monotonic']);self.assertEqual(log.snapshot()['clock_failures'],1);json.dumps(log.snapshot(),allow_nan=False)
 def test_broken_sdk_recorder_does_not_change_request_semantics(self):
  c=self.client(lambda req:{'schema':'rm.sim.platform.v2','closed':True})
  def bad(*a,**k):raise RuntimeError('record')
  c._diagnostics.record=bad;c.close();self.assertIsNone(c.session_id);self.assertGreater(c.diagnostics()['dropped_events'],0)
 def test_url_credentials_in_identity_values_are_not_recorded(self):
  secret='https://user:SYNTHETIC_PASSWORD@host/path?token=SYNTHETIC_TOKEN'
  c=self.client(lambda req:urllib.error.HTTPError(req.full_url,404,'denied',{},io.BytesIO(b'{"error":"unknown_robot"}')))
  with self.assertRaises(ClientError):c.watch(secret)
  d=c.diagnostics();self.assertIsNone(d['first_failure']['robot_id']);self.assertEqual(d['first_failure']['reason_code'],'unknown_robot')
  for value in ('SYNTHETIC_PASSWORD','SYNTHETIC_TOKEN','https://'):self.assertNotIn(value,json.dumps(d))
 def test_identity_sink_preserves_normal_ids_rejects_url_shapes(self):
  log=DiagnosticLog()
  good=log.record('ok',robot_id='blue_robot.2',session_id='a123',thread='v2-control');self.assertEqual(good['robot_id'],'blue_robot.2')
  for bad in ('https://host','user@host','a/b','a?token=b','a:password','a\\b',' space','x'*129):
   with self.subTest(value=bad):
    row=log.record('x',robot_id=bad,session_id=bad,thread=bad);self.assertIsNone(row['robot_id']);self.assertIsNone(row['session_id'])
 def test_thread_local_request_id_separates_concurrent_ack_validation(self):
  c=self.client(lambda req:{'schema':'rm.sim.platform.v2'});barrier=threading.Barrier(2);ids=[]
  def worker(path):
   c._request(path);barrier.wait()
   try:c._invalid('bad ack')
   except ClientError as e:ids.append(e.request_id)
  threads=[threading.Thread(target=worker,args=(path,)) for path in ('/v2/state','/v2/frame')]
  for t in threads:t.start()
  for t in threads:t.join(1);self.assertFalse(t.is_alive())
  self.assertEqual(len(set(ids)),2)

if __name__=='__main__':unittest.main()
