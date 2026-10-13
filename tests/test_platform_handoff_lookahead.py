"""Synthetic-clock state separation and multi-request lookahead. No physics."""
import unittest
from rm_client.sim_platform_handoff import HandoffSchedule
from test_platform_handoff import window,Harness


class LookaheadTests(unittest.TestCase):
    def ready(self):
        p=HandoffSchedule();p.install(window(),0.,.003);p.begin_batch(.012);p.finish_batch();return p
    def test_ordinary_batch_cannot_cross_prefetch_trigger(self):
        p=self.ready();self.assertEqual('fetch',p.next_action(.060,kinds=1,controls=2,fresh=True))
        self.assertEqual('batch_crosses_prefetch',p.handoff_reason)
    def test_issue_interval_wait_sends_no_old_input(self):
        p=self.ready();self.assertEqual('wait',p.next_action(.031,kinds=1,controls=2,fresh=True))
        self.assertEqual('waiting_issue_interval',p.handoff_reason)
        self.assertEqual('fetch',p.next_action(.050,kinds=1,controls=2,fresh=True))
    def test_forecast_pressure_not_hard_deadline(self):
        p=self.ready();now=.15-.059552165
        self.assertEqual('fetch',p.next_action(now,kinds=1,controls=2,fresh=True))
        self.assertEqual('forecast_insufficient_one_attempt',p.handoff_reason)
        self.assertEqual('window_expired',p.next_action(.150,kinds=1,controls=2,fresh=True))
    def test_cost_at_ttl_is_still_terminal(self):
        p=self.ready();p.observe('window',0.,.10)
        self.assertEqual('budget_pressure',p.next_action(.05,kinds=1,controls=2,fresh=True))
    def test_manual_only_uses_original_window_hard_expiry(self):
        p=self.ready();self.assertEqual({},p.activation)
        self.assertEqual('window_expired',p.next_action(.15,kinds=0,controls=3,fresh=True))
    def test_first_population_each_kind_attempted_only_once(self):
        p=HandoffSchedule();p.install(window(),0.,.003);p.begin_batch(.01)
        p.mark_kind_attempt('navigation');p.acknowledge('navigation',dict(window_id=1,activation_expires=10000.15))
        with self.assertRaises(ValueError):p.mark_kind_attempt('navigation')
        p.mark_kind_attempt('aim');self.assertEqual({'navigation','aim'},p.population_attempted)
        self.assertEqual({'navigation'},p.population_acked)
        self.assertEqual('wait',p.next_action(.06,kinds=2,controls=1,fresh=True))
    def test_new_window_cannot_refresh_old_kind_deadlines(self):
        p=self.ready();p.activation={'navigation':.14,'aim':.15}
        p.install(window(2,10000.10),.1,.11)
        self.assertEqual({'navigation':.14,'aim':.15},p.activation)
        self.assertEqual('activation_expired',p.expired(.14))
    def test_late_wake_has_only_one_window_without_new_sample(self):
        h=Harness();h.now=100.090447835;h.sample();h.tick()
        count=len(h.requests)
        for _ in range(40):h.now+=.001;h.tick()
        self.assertEqual(count,len(h.requests));self.assertTrue(h.t._focus)
        h.now=100.15;h.tick();self.assertFalse(h.t._focus)
    def test_first_kind_cannot_get_second_opportunity_after_other_expired(self):
        h=Harness(True);h.now=100.090447835;h.sample();h.tick();h.now=100.140;h.sample()
        h.delay['/v2/adapter_input']=.009;h.ack_delay['/v2/adapter_input']=.002
        before=len(h.requests);h.tick();self.assertFalse(h.t._focus);h.now+=.01;h.tick()
        self.assertEqual(['navigation'],[r['kind'] for r in h.requests[before:] if r['path']=='/v2/adapter_input'])
    def test_slow_forecast_does_not_reset_on_new_generation(self):
        p=self.ready();p.observe('control',0.,.06);p.reset()
        self.assertEqual('budget_pressure',p.next_action(1.,kinds=0,controls=3,fresh=True))
    def test_fetching_never_starts_a_second_request(self):
        p=self.ready();p.phase='FETCHING'
        self.assertEqual('wait',p.next_action(.06,kinds=1,controls=2,fresh=True))
        self.assertEqual('window_expired',p.next_action(.15,kinds=1,controls=2,fresh=True))


if __name__=='__main__':unittest.main()
