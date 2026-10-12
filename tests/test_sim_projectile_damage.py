"""Synthetic-only draft contract tests; no UI or simulator capability is enabled."""
import unittest
from types import SimpleNamespace
from rm_client.sim_projectile_damage import SCHEMA,SOURCE,ProjectileDamageConsumer,parse_observation
from rm_client.auto_spin import ProjectileHitSpinController

IDENTITY=dict(session_id='synthetic-session',epoch=0,robot_id='red_infantry')

def sample(revision=0,total=0,event=-1,stamp=1.,**overrides):
    return dict(schema=SCHEMA,source=SOURCE,**IDENTITY,episode=0,revision=revision,total_damage=total,last_event_sequence=event,sim_time=stamp,**overrides)

class DamageConsumerTests(unittest.TestCase):
    def setUp(self):self.consumer=ProjectileDamageConsumer()
    def read(self,s=None,live=True,identity=None,now=None):
        s=sample() if s is None else s
        return self.consumer.observe(s,IDENTITY if identity is None else identity,s['sim_time'] if now is None else now,live=live)
    def test_initial_nonzero_cumulative_sample_is_only_baseline(self):
        d=self.read(sample(8,150,34));self.assertEqual(d.damage,0);self.assertEqual(d.reason,'baseline')
    def test_positive_new_revision_emits_actual_credited_delta(self):
        self.read(sample(2,25,20));d=self.read(sample(3,32,21,2.))
        self.assertEqual(d.damage,7);self.assertEqual(d.reason,'new_projectile_damage')
    def test_dropped_samples_coalesce_without_hardcoded_per_hit_damage(self):
        self.read(sample(2,25,20));self.assertEqual(self.read(sample(5,77,29,2.)).damage,52)
    def test_repeated_snapshot_never_emits_twice(self):
        self.read();s=sample(1,20,4,2.)
        self.assertEqual(self.read(s).damage,20)
        for _ in range(20):self.assertEqual(self.read(s).damage,0)
    def test_pause_same_stamp_and_multiple_revisions_are_valid(self):
        self.read();self.assertEqual(self.read(sample(1,20,4)).damage,20)
        self.assertEqual(self.read(sample(2,22,6)).damage,2)
    def test_new_timestamp_without_damage_is_duplicate(self):
        self.read();self.assertEqual(self.read(sample(stamp=2.)).reason,'duplicate')
    def test_disabled_observations_never_trigger_and_enable_rebaselines(self):
        self.read(live=False);self.assertEqual(self.read(sample(1,20,4),live=False).damage,0)
        self.assertEqual(self.read(sample(2,40,6),live=True).damage,0)
        self.assertEqual(self.read(sample(3,42,7),live=True).damage,2)
    def test_new_lease_does_not_replay_damage_seen_while_disarmed(self):
        self.read();self.read(sample(1,20,4),live=False)
        self.assertEqual(self.read(sample(2,40,5)).damage,0)
        self.assertEqual(self.read(sample(3,60,7)).damage,20)
    def test_disconnect_explicit_reset_never_replays_missed_hits(self):
        self.read();self.consumer.reset()
        self.assertEqual(self.read(sample(10,200,34)).damage,0)
    def test_wrong_identity_invalidates_baseline(self):
        self.read()
        with self.assertRaises(ValueError):self.read(dict(sample(1,20,4),robot_id='blue_infantry'))
        self.assertEqual(self.read(sample(2,40,5)).damage,0)
    def test_new_epoch_and_restored_old_epoch_establish_fresh_baselines(self):
        self.read(sample(4,80,10))
        other=dict(IDENTITY,epoch=1);s=dict(sample(1,10,2),epoch=1)
        self.assertEqual(self.read(s,identity=other).damage,0)
        self.assertEqual(self.read(sample(6,120,15)).damage,0)
    def test_judge_episode_change_rebaselines_even_with_same_envelope(self):
        self.read(sample(4,80,10))
        self.assertEqual(self.read(dict(sample(),episode=1)).damage,0)
        self.assertEqual(self.read(dict(sample(1,20,5),episode=1)).damage,20)
    def test_robot_switch_is_a_new_baseline(self):
        self.read();other=dict(IDENTITY,robot_id='red_sentry')
        self.assertEqual(self.read(dict(sample(9,100,40),robot_id='red_sentry'),identity=other).damage,0)
    def test_regressed_revision_total_sequence_or_time_invalidates(self):
        for bad in (sample(1,40,10,2.),sample(3,19,11,2.),sample(3,60,9,2.),sample(3,60,11,.5)):
            with self.subTest(bad=bad):
                self.consumer.reset();self.read(sample(2,40,10,1.))
                with self.assertRaises(ValueError):self.read(bad)
                self.assertEqual(self.read(sample(4,80,20,3.)).damage,0)
    def test_new_revision_requires_actual_new_event_and_positive_damage(self):
        for bad in (sample(3,40,11),sample(3,60,10),sample(2,60,10),sample(2,40,11)):
            with self.subTest(bad=bad):
                self.consumer.reset();self.read(sample(2,40,10))
                with self.assertRaises(ValueError):self.read(bad)
    def test_stale_future_or_nonfinite_time_cannot_trigger(self):
        for stamp,now in ((1,2),(2,1),(float('nan'),2),(float('inf'),2),(10**1000,2)):
            with self.subTest(stamp_type=type(stamp).__name__),self.assertRaises(ValueError):self.read(sample(1,20,2,stamp),now=now)
    def test_age_boundary_is_inclusive(self):
        self.assertEqual(self.read(sample(),now=1.5).damage,0)
        with self.assertRaises(ValueError):self.read(sample(),now=1.500001)
    def test_hp_and_valid_damage_boolean_are_not_a_projectile_counter(self):
        for s in ({'hp':80},{'valid_damage':True},dict(sample(),source='hp_delta'),dict(sample(),schema='official_referee_bytes')):
            with self.assertRaises(ValueError):self.read(s,now=1.)
    def test_strict_integer_fields_reject_bool_float_negative_or_huge(self):
        for key in ('revision','last_event_sequence','epoch','episode'):
            for value in (True,1.0,-2,2**53,10**1000):
                with self.subTest(key=key,value_type=type(value).__name__),self.assertRaises(ValueError):self.read(dict(sample(),**{key:value}))
    def test_baseline_consistency_cannot_claim_positive_revision_without_damage(self):
        for s in (sample(1,0,1),sample(0,20,1),sample(1,20,-1),sample(0,0,1)):
            with self.assertRaises(ValueError):self.read(s)
    def test_fractional_actual_credit_is_preserved_without_rounding(self):
        self.read();self.assertEqual(self.read(sample(1,.5,1)).damage,.5)
        self.assertEqual(self.read(sample(2,.75,2)).damage,.25)
        self.assertEqual(self.read(sample(4,1.125,4)).damage,.375)
    def test_float_zero_baseline_is_valid(self):
        self.assertEqual(self.read(sample(total=0.)).damage,0)
    def test_invalid_actual_credit_is_rejected(self):
        for value in (True,None,-.1,float('inf'),float('nan'),10**1000,2.**54):
            with self.subTest(value_type=type(value).__name__),self.assertRaises(ValueError):self.read(sample(1,value,1))
    def test_live_must_be_strict_boolean(self):
        for live in (1,'true',None):
            with self.assertRaises(ValueError):self.read(live=live)
    def test_independently_synthetic_fixture(self):
        import json
        from pathlib import Path
        value=json.loads((Path(__file__).parent/'fixtures/sim_operator/own-damage.fixture.json').read_text())
        parsed=parse_observation(value,value,value['sim_time'])
        self.assertEqual(parsed.total_damage,12.5);self.assertEqual(parsed.revision,2)
    def test_no_control_surface_is_provided(self):
        d=self.read();self.assertFalse(hasattr(d,'fire'));self.assertFalse(hasattr(d,'spin_enabled'))
        self.assertFalse(hasattr(self.consumer,'send_command'))

class ExistingHitPolicyContractTests(unittest.TestCase):
    """Exercise existing pure policy only; this does not integrate its SIM hook."""
    def setUp(self):
        self.consumer=ProjectileDamageConsumer();self.guard=ProjectileHitSpinController()
        self.settings={'Power Guard':False,'Require Referee Link':True,'Only During Match':True,'Duration':2.,'Cooldown':1.,'Minimum Damage':5,'Auto Disable':True}
        self.feature=SimpleNamespace(enabled=True,get_setting=lambda key:SimpleNamespace(value=self.settings[key]) if key in self.settings else None)
        self.state=SimpleNamespace(projectile_hit_revision=0,last_projectile_damage_delta=0,current_health=100,current_stage='MATCH',current_buffer_energy=0)
    def observe(self,s,now,spin=False,referee=True):
        d=self.consumer.observe(s,IDENTITY,s['sim_time'],live=True)
        self.state.projectile_hit_revision=d.revision;self.state.last_projectile_damage_delta=d.damage
        return self.guard.update(self.state,self.feature,spin_enabled=spin,referee_link=referee,now=now)
    def test_minimum_damage_uses_actual_delta(self):
        self.observe(sample(),0)
        self.assertEqual(self.observe(sample(1,4,2),1).action,'none')
        self.assertEqual(self.observe(sample(2,9,3),2).action,'enable')
    def test_fractional_actual_credit_does_not_round_up_to_minimum(self):
        self.settings['Minimum Damage']=1;self.observe(sample(),0)
        self.assertEqual(self.observe(sample(1,.5,2),1).action,'none')
        self.assertEqual(self.state.last_projectile_damage_delta,.5)
        self.assertEqual(self.observe(sample(2,1.5,3),2).action,'enable')
    def test_manual_existing_spin_is_not_owned_or_disabled(self):
        self.observe(sample(),0)
        self.assertEqual(self.observe(sample(1,20,2),1,spin=True).action,'none')
        self.assertFalse(self.guard.owns_spin)
        self.assertEqual(self.observe(sample(1,20,2),4,spin=True).action,'none')
    def test_repeated_hit_keeps_ownership_then_expires_once(self):
        self.observe(sample(),0);self.assertEqual(self.observe(sample(1,20,2),1).action,'enable')
        self.observe(sample(2,40,4),2,spin=True);self.assertTrue(self.guard.owns_spin)
        self.assertEqual(self.observe(sample(2,40,4),3,spin=True).action,'none')
        self.assertEqual(self.observe(sample(2,40,4),4,spin=True).action,'disable')
    def test_operator_override_prevents_expiry_from_disabling(self):
        self.observe(sample(),0);self.observe(sample(1,20,2),1);self.guard.operator_override()
        self.assertEqual(self.observe(sample(1,20,2),4,spin=True).action,'none')
    def test_unknown_power_is_not_forged_to_allow_spin(self):
        self.settings['Power Guard']=True;self.observe(sample(),0)
        d=self.observe(sample(1,20,2),1);self.assertEqual(d.action,'none');self.assertEqual(d.reason,'LOW BUFFER ENERGY')
    def test_unverified_referee_or_non_match_or_dead_cannot_enable(self):
        for reason in ('referee','stage','dead'):
            self.setUp();self.observe(sample(),0)
            if reason=='stage':self.state.current_stage='COUNTDOWN'
            if reason=='dead':self.state.current_health=0
            self.assertEqual(self.observe(sample(1,20,2),1,referee=reason!='referee').action,'none')

if __name__=='__main__':unittest.main()
