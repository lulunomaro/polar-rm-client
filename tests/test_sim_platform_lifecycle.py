import threading,time,unittest
from rm_client.sim_platform_lifecycle import RenewalLifecycle
class Client:
 def __init__(self):self.calls=[];self.uncertain=False;self.session_id='owner';self.revoked=False;self.block=None;self.entered=threading.Event();self.error=None
 def state(self):
  self.calls.append('state');return dict(sim_time=.4,episode=2,phase='running',clock_profile={'barrier_state':'native_barrier'},control=dict(server_monotonic_s=100.,leases={'robot':{'gimbal':dict(epoch=3,expires=101.,owner=None if self.revoked else self.session_id)}}))
 def renew(self,*a):
  self.calls.append('renew');self.entered.set()
  if self.block:self.block.wait(2)
  if self.error:raise self.error
  return dict(lease_epochs={'gimbal':3},lease_expires={'gimbal':101.})
 def release(self,*a):self.calls.append('release');return {'released':True}
 def close(self):self.calls.append('close');self.session_id=None
class LifecycleTests(unittest.TestCase):
 def make(self):
  c=Client();l=RenewalLifecycle(c,'robot',['gimbal'],interval=.01);l.observe(c.state());c.calls=[];return c,l
 def test_success_records_wall_known_sim_and_ack(self):
  c,l=self.make();self.assertTrue(l.renew_once());events=l.events();self.assertEqual([e['event'] for e in events],['renew_request','renew_success']);self.assertEqual(events[-1]['last_known']['sim_time'],.4);self.assertEqual(events[-1]['last_known']['episode'],2);self.assertEqual(events[-1]['last_known']['lease_epochs'],{'gimbal':3});self.assertLessEqual(events[0]['wall_time'],events[1]['wall_time']);self.assertIn('observed_wall',events[-1]['last_known'])
 def test_uncertain_and_failure_logged_and_no_retry(self):
  for uncertain in (False,True):
   c,l=self.make();c.uncertain=uncertain;c.error=RuntimeError('failure');self.assertFalse(l.renew_once());self.assertEqual(l.events()[-1]['event'],'renew_uncertain' if uncertain else 'renew_failure');self.assertTrue(l._stop.is_set())
 def test_no_snapshot_is_explicit_unknown(self):
  c=Client();l=RenewalLifecycle(c,'robot',['gimbal']);l.renew_once();self.assertIsNone(l.events()[-1]['last_known']);self.assertEqual(l.events()[-1]['context_status'],'unknown')
 def test_malformed_numeric_context_does_not_invent_known_values(self):
  c,l=self.make();v=c.state();v.update(sim_time=float('nan'),episode=True);l.observe(v);l.renew_once();self.assertIsNone(l.events()[-1]['last_known']['sim_time']);self.assertIsNone(l.events()[-1]['last_known']['episode'])
 def test_join_precedes_release_close_and_is_idempotent(self):
  c,l=self.make();l.start();self.assertTrue(c.entered.wait(.3));r=l.close();self.assertTrue(r['completed']);events=[x['event'] for x in l.events()];self.assertLess(events.index('renewal_joined'),events.index('release_request'));self.assertLess(events.index('release_success'),events.index('session_close_request'));calls=list(c.calls);self.assertEqual(l.close(),r);self.assertEqual(c.calls,calls)
 def test_inflight_timeout_does_not_block_or_close_under_sdk_lock(self):
  c,l=self.make();c.block=threading.Event();l.start();self.assertTrue(c.entered.wait(.3));start=time.monotonic();r=l.close(join_timeout=.02);self.assertFalse(r['quiesced']);self.assertLess(time.monotonic()-start,.1);self.assertNotIn('release',c.calls);self.assertNotIn('close',c.calls);c.block.set();self.assertTrue(l.close(join_timeout=.5)['completed'])
 def test_terminal_revocation_is_not_fake_release_success(self):
  c,l=self.make();c.revoked=True;r=l.close();self.assertTrue(r['session_close_ack']);self.assertNotIn('release',c.calls);self.assertIn('release_already_not_owned',[e['event'] for e in l.events()])
 def test_close_exception_is_reported_not_swallowed(self):
  c,l=self.make()
  def fail():raise RuntimeError('close failed')
  c.close=fail;r=l.close();self.assertFalse(r['completed']);self.assertIn('session_close_failed',r['errors']);self.assertIn('session_close_failure',[e['event'] for e in l.events()])
 def test_cannot_start_after_close_or_concurrent_duplicate_start(self):
  c,l=self.make();l.close()
  with self.assertRaises(RuntimeError):l.start()
  c,l=self.make();gate=threading.Barrier(3);results=[]
  def start():
   gate.wait()
   try:l.start();results.append('started')
   except RuntimeError:results.append('rejected')
  workers=[threading.Thread(target=start) for _ in range(2)]
  for t in workers:t.start()
  gate.wait()
  for t in workers:t.join(1)
  self.assertCountEqual(results,['started','rejected']);l.close()
 def test_successful_cleanup_does_not_hide_prior_uncertainty(self):
  c,l=self.make();c.uncertain=True;c.error=RuntimeError('uncertain');l.renew_once();r=l.close();self.assertTrue(r['completed']);self.assertFalse(r['network_lifecycle_error_free']);self.assertEqual(r['prior_renew_errors'],['renew_uncertain'])
if __name__=='__main__':unittest.main()
