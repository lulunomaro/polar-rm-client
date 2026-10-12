"""Original Qt registry/request path, with synthetic judge observations only."""
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QEvent,Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from rm_client.sim_operator_transport import SimOperatorTransport
from rm_client.sim_operator_runtime import OperatorRuntime
from rm_client.ui.sim_operator_window import OperatorWindow
from rm_client.sim_operator_modes import spin_request,SPIN_DEFAULT,validate_feedback
from rm_client.sim_projectile_damage import SCHEMA,SOURCE
from tests.test_sim_operator_modes import mode_session,mode_feedback
from tests.test_sim_operator import telemetry

class HitSpinWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.p=patch.object(SimOperatorTransport,'start');self.p.start()
        self.r=OperatorRuntime();self.w=OperatorWindow(self.r);self.w.timer.stop();self.w.resize(1360,900);self.w.isActiveWindow=lambda:True
        self.t=self.r.transport;self.now=[100.];self.t.clock=lambda:self.now[0]
        s=mode_session(aim=False);s['capabilities']['auto_spin_on_hit_damage_version']=1
        self.t.install_session(s);self.install();self.r.heartbeat(self.w);self.r.apply_state(self.w.state)
        self.feature=self.w.registry.get('AutoSpinOnHit');self.spin=self.w.registry.get('SpinMode')
        self.w.registry.get('AudioFeedback').enabled=False
        self.w.registry.toggle_module(self.feature);self.r.heartbeat(self.w);self.w.update_auto_spin_guard()
        self.arm();self.w.update_auto_spin_guard()
    def tearDown(self):self.w.close();self.r._tmp.cleanup();self.p.stop()
    def install(self,revision=0,total=0,event=-1,stamp=1.,episode=0,**extra):
        s=self.t.session
        damage=dict(schema=SCHEMA,source=SOURCE,**{k:s[k] for k in ('session_id','epoch','robot_id')},episode=episode,revision=revision,total_damage=total,last_event_sequence=event,sim_time=stamp)
        data=telemetry(sim_time=stamp,own_damage=damage,**extra)
        self.t.install_telemetry(data,s);self.t.video=np.zeros((240,320,3),np.uint8);self.t.video_at=self.t.clock()
        self.r.apply_state(self.w.state)
    def key(self,k):self.w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress,k,Qt.KeyboardModifier.NoModifier))
    def arm(self):
        self.key(Qt.Key.Key_F2);self.r.heartbeat(self.w);self.assertTrue(self.r.armed)
        # Synthetic transport has no network thread: stand in for a current
        # accepted neutral ACK. The real handshake has separate contract tests.
        self.t._neutral_pending=False
    def hit(self):
        self.install(1,20,2,2.);self.w.update_auto_spin_guard();self.r.heartbeat(self.w)
    def payload(self):self.w.control.tick(self.w.state,self.w.registry);return self.t._control_payload()
    def test_real_registry_policy_toggle_stays_in_current_lease(self):
        self.hit();self.assertTrue(self.r.armed);self.assertTrue(self.spin.enabled)
        body=self.payload();self.assertTrue(body['mode_request']['spin_enabled']);self.assertEqual(body['mode_request']['spin_origin'],'on_hit');self.assertFalse(body['fire'])
        self.assertFalse(self.w.effective_spin_enabled());self.assertIsNone(self.r.modes.feedback()['spin_origin'])
        mode=mode_feedback(body['mode_request'],body['seq']);mode['effective']['spin_origin']='on_hit'
        self.install(1,20,2,2.,operator_modes=mode);self.assertTrue(self.w.effective_spin_enabled());self.assertEqual(self.r.modes.feedback()['spin_origin'],'on_hit')
    def test_only_positive_damage_after_live_baseline_triggers(self):
        self.assertFalse(self.spin.enabled);self.w.update_auto_spin_guard();self.assertFalse(self.spin.enabled)
        self.hit();self.assertTrue(self.spin.enabled)
    def test_same_lease_retrigger_cannot_reuse_old_spin_confirmation(self):
        self.hit();body=self.payload();old=mode_feedback(body['mode_request'],body['seq']);old['effective']['spin_origin']='on_hit'
        self.install(1,20,2,2.,operator_modes=old);self.assertTrue(self.w.effective_spin_enabled())
        self.install(1,20,2,4.5,operator_modes=old);self.w.update_auto_spin_guard();self.r.heartbeat(self.w)
        self.assertFalse(self.spin.enabled)
        self.install(2,40,5,6.,operator_modes=old);self.w.update_auto_spin_guard();self.r.heartbeat(self.w)
        self.assertTrue(self.spin.enabled);self.assertTrue(self.r.armed)
        self.assertFalse(self.w.effective_spin_enabled())
        new=self.payload();fresh=mode_feedback(new['mode_request'],new['seq']);fresh['effective']['spin_origin']='on_hit'
        self.install(2,40,5,6.,operator_modes=fresh);self.assertTrue(self.w.effective_spin_enabled())
    def test_zero_new_damage_cannot_trigger_and_wrong_counter_is_rejected(self):
        self.install(0,0,-1,2.);self.w.update_auto_spin_guard();self.assertFalse(self.spin.enabled)
        with self.assertRaises(ValueError):self.install(1,0,2,3.)
    def test_duration_uses_sim_time_not_wall_time(self):
        self.hit();self.now[0]+=.1;self.t.heartbeat(True)
        self.install(1,20,2,4.49);self.w.update_auto_spin_guard();self.assertTrue(self.spin.enabled)
        self.install(1,20,2,4.5);self.w.update_auto_spin_guard();self.r.heartbeat(self.w)
        self.assertTrue(self.r.armed);self.assertFalse(self.spin.enabled);self.assertEqual(self.payload()['mode_request']['spin_origin'],'manual')
    def test_auto_disable_false_still_drops_old_hit_on_disarm(self):
        setting=self.feature.get_setting('Auto Disable');setting.value=False;self.w.after_setting_changed(setting)
        self.r.heartbeat(self.w);self.w.update_auto_spin_guard();self.arm();self.w.update_auto_spin_guard()
        self.hit();self.install(1,20,2,6.);self.w.update_auto_spin_guard();self.r.heartbeat(self.w)
        self.assertTrue(self.spin.enabled);self.assertEqual(self.r.hit_spin.origin,'on_hit')
        self.r.disarm('blur');self.assertFalse(self.spin.enabled)
    def test_automatic_toggle_cannot_use_legacy_publish_outlet(self):
        with patch.object(self.w.backend,'publish_spin_control',side_effect=AssertionError('legacy spin outlet')):
            self.hit();self.assertTrue(self.spin.enabled);self.assertFalse(self.payload()['fire'])
    def test_duplicate_damage_does_not_extend_duration(self):
        self.hit();until=self.r.hit_spin.guard.active_until
        for _ in range(10):self.w.update_auto_spin_guard()
        self.assertEqual(self.r.hit_spin.guard.active_until,until)
    def test_blur_clears_automatic_module_and_rearm_does_not_replay(self):
        self.hit();self.r.disarm('blur');self.assertFalse(self.spin.enabled)
        self.r.heartbeat(self.w);self.arm();self.w.update_auto_spin_guard()
        self.assertFalse(self.spin.enabled);self.assertFalse(self.payload()['mode_request']['spin_enabled'])
    def test_forged_source_string_never_bypasses_rearm(self):
        self.w.registry.toggle_module(self.spin,source='auto_hit_sim');self.assertFalse(self.r.armed)
        self.assertIsNone(self.r.hit_spin._token)
    def test_only_spin_boolean_token_not_speed_or_range_change(self):
        self.install(1,20,2,2.);self.w.update_auto_spin_guard();self.assertTrue(self.r.armed)
        self.spin.get_setting('Spin Speed').value=120
        self.r.heartbeat(self.w);self.assertFalse(self.r.armed);self.assertFalse(self.spin.enabled)
    def test_token_is_one_use(self):
        self.hit();self.assertIsNone(self.r.hit_spin._token)
        self.spin.enabled=False;self.r.heartbeat(self.w);self.assertFalse(self.r.armed)
    def test_token_expires_with_wall_input_lease(self):
        self.install(1,20,2,2.);self.w.update_auto_spin_guard();self.now[0]+=.151
        self.r.heartbeat(self.w);self.assertFalse(self.r.armed);self.assertFalse(self.spin.enabled)
    def test_episode_reset_disarms_old_automatic_policy(self):
        self.hit();self.install(0,0,-1,3.,episode=1);self.w.update_auto_spin_guard()
        self.assertFalse(self.r.armed);self.assertFalse(self.spin.enabled)
    def test_manual_spin_is_not_closed_when_auto_policy_resets(self):
        self.key(Qt.Key.Key_X);self.r.heartbeat(self.w);self.arm();self.w.update_auto_spin_guard()
        self.install(1,20,2,2.);self.w.update_auto_spin_guard();self.assertTrue(self.spin.enabled)
        self.r.disarm('blur');self.assertTrue(self.spin.enabled)
    def test_unknown_power_and_real_link_are_never_forged(self):
        self.assertFalse(self.feature.get_setting('Power Guard').value)
        self.assertTrue(self.w.setting_is_unavailable(self.feature.get_setting('Power Guard')))
        self.assertTrue(self.w.setting_is_unavailable(self.feature.get_setting('Minimum Buffer')))
        self.assertFalse(self.w.state.official_events_online);self.assertFalse(self.w.state.mqtt_connected)
        self.hit();self.assertFalse(self.w.state.official_events_online);self.assertFalse(self.w.state.mqtt_connected)
    def test_partial_feature_settings_change_requires_new_f2(self):
        setting=self.feature.get_setting('Duration');setting.value=1.;self.w.after_setting_changed(setting)
        self.assertFalse(self.r.armed);self.assertFalse(self.spin.enabled)
    def test_capability_loss_disables_auto_feature_and_its_request(self):
        self.hit();s=mode_session(aim=False);s['epoch']=1;self.t.install_session(s);self.r.heartbeat(self.w)
        self.assertFalse(self.feature.enabled);self.assertFalse(self.spin.enabled);self.assertFalse(self.r.armed)
    def assert_bad_setting_stops_spin(self,name,value):
        self.hit();self.assertTrue(self.r.armed);self.assertTrue(self.spin.enabled)
        self.feature.get_setting(name).value=value;self.w.update_auto_spin_guard()
        self.assertFalse(self.r.armed);self.assertFalse(self.spin.enabled)
        self.assertIn('Invalid SIM hit-spin settings',self.r.note)
    def test_infinite_duration_fails_closed(self):self.assert_bad_setting_stops_spin('Duration',float('inf'))
    def test_overflowing_damage_fails_closed(self):self.assert_bad_setting_stops_spin('Minimum Damage',10**1000)
    def test_negative_cooldown_fails_closed(self):self.assert_bad_setting_stops_spin('Cooldown',-1)
    def test_nonboolean_auto_disable_fails_closed(self):self.assert_bad_setting_stops_spin('Auto Disable','true')
    def test_unsupported_power_condition_fails_closed(self):self.assert_bad_setting_stops_spin('Power Guard',True)
    def test_direct_parameter_edit_cannot_preserve_automatic_lease(self):
        self.hit();self.feature.get_setting('Duration').value=1.
        self.w.update_auto_spin_guard();self.assertFalse(self.r.armed);self.assertFalse(self.spin.enabled)
    def test_new_damage_during_disarm_is_only_baseline_after_rearm(self):
        self.hit();self.r.disarm('blur');self.install(8,160,20,3.)
        self.r.heartbeat(self.w);self.arm();self.w.update_auto_spin_guard()
        self.assertFalse(self.spin.enabled)
        self.install(9,180,21,4.);self.w.update_auto_spin_guard();self.r.heartbeat(self.w)
        self.assertTrue(self.spin.enabled);self.assertTrue(self.r.armed)

    def test_effective_origin_cannot_claim_unrequested_policy(self):
        self.hit();body=self.payload();mode=mode_feedback(body['mode_request'],body['seq']);mode['effective']['spin_origin']='manual'
        with self.assertRaises(ValueError):self.install(1,20,2,2.,operator_modes=mode)

if __name__=='__main__':unittest.main()
