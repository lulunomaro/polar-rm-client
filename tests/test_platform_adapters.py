"""Synthetic capability/worker arbitration; no image/physics qualification."""
import copy
import unittest
from test_sim_platform_v2 import state,frame
from test_platform_control_windows import WindowClient
from rm_client.sim_platform_adapters import parse_catalog,options,make_plan,feedback
from rm_client.sim_platform_transport import PlatformTransport

def catalog(camera=True,native=False):
    def d(aid,kind,config,sensors,**kw):
        return dict(adapter_id=aid,version='1.0.0',kind=kind,label=aid,ui_config_contract=config,required_sensors=sensors,
                    output_modes=['velocity_body'] if kind=='navigation' else ['gimbal_target'],available=True,fire_authority='manual_only',**kw)
    nav=d('nav_rule_v1','navigation','goal_world_2d_v1',['odometry','ranges'])
    pixel=d('aim_pixel_baseline_v1','aim','empty_v1',['camera','odometry'])
    source=d('aim_native_rmcs_v1','aim','native_fov_v1',['camera','odometry'],supported_robots=['red_sentry'])
    source.update(available=native,reason='explicit_trusted_native_spec_required')
    fixture=d('nav_stop_test_v1','navigation','empty_v1',[],test_only=True)
    return dict(schema='rm.sim.platform.v2',adapter_ui_version=1,adapter_activation_version=1,readiness='declared_support_only',fire_authority='manual_only',adapters=[nav,pixel,source,fixture],robots=[dict(robot_id=r,declared_sensors=['odometry','ranges']+(['camera'] if camera else [])) for r in ('red_infantry','red_sentry')])

class CatalogTests(unittest.TestCase):
    def test_actual_choices_distinguish_fixture_native(self):
        c=parse_catalog(catalog());self.assertEqual(options(c,'red_infantry','navigation'),['Manual','nav_rule_v1']);self.assertEqual(options(c,'red_infantry','aim'),['Manual','aim_pixel_baseline_v1'])
    def test_missing_camera_disables_aim(self):self.assertEqual(options(parse_catalog(catalog(False)),'red_sentry','aim'),['Manual'])
    def test_native_explicit_and_supported_robot_only(self):
        c=parse_catalog(catalog(native=True));self.assertIn('aim_native_rmcs_v1',options(c,'red_sentry','aim'));self.assertNotIn('aim_native_rmcs_v1',options(c,'red_infantry','aim'))
    def test_unknown_malformed_descriptors_not_enabled(self):
        for key,value in [('available',1),('version','2.0'),('ui_config_contract','shell'),('test_only',1),('fire_authority','auto')]:
            raw=catalog();raw['adapters'][0][key]=value
            self.assertEqual(['Manual'],options(parse_catalog(raw),'red_sentry','navigation'))
    def test_unknown_top_version_manual_only(self):
        for val in [True,1.,2]:
            c=catalog();c['adapter_ui_version']=val;self.assertFalse(parse_catalog(c)['ready'])
    def test_goal_is_only_explicit_settings(self):
        c=parse_catalog(catalog());p=make_plan(c,'red_sentry',{'navigation':'nav_rule_v1'},{'V2 Goal X':2,'V2 Goal Y':-1,'enemy_truth':[7,7]})
        self.assertEqual(p['navigation']['config'],{'goal_world_m':[2.,-1.]})
    def test_null_target_is_never_active_confirmation(self):
        s={'episode':0,'sim_time':.2,'control':{'bindings':{'r':{'aim':{'owner':'s','epoch':2}}}},'algorithm_status':{'r':{'aim':'active'}},'manual_consumption_receipts':{'r':{'robot_id':'r','episode':0,'consume_sim_time':.1,'adapter_authorizations':{'aim':{'owner':'s','epoch':2}},'target':{'gimbal':None}}}}
        self.assertTrue(feedback(s,'r','aim',{'binding_epoch':2},'s').startswith('not applied'))
    def test_config_nonfinite_bool_rejected(self):
        for v in [True,float('nan'),10**999]:
            with self.assertRaises(ValueError):make_plan(parse_catalog(catalog()),'red_sentry',{'navigation':'nav_rule_v1'},{'V2 Goal X':v})
    def test_no_registration_does_not_change_manual(self):self.assertEqual({},make_plan(parse_catalog(None),'r',{},{}))
    def test_feedback_requires_same_epoch_and_episode(self):
        expected={'binding_epoch':2};s={'episode':0,'sim_time':.2,'control':{'bindings':{'r':{'aim':{'owner':'s','epoch':2}}}},'manual_consumption_receipts':{'r':{'robot_id':'r','episode':0,'consume_sim_time':.1,'adapter_authorizations':{'aim':{'owner':'s','epoch':1}},'target':{'gimbal':{'yaw_rad':.2}}}}}
        self.assertNotIn('last applied',feedback(s,'r','aim',expected,'s'))
        s['manual_consumption_receipts']['r']['adapter_authorizations']['aim']['epoch']=2
        self.assertIn('last applied',feedback(s,'r','aim',expected,'s'))
        s['manual_consumption_receipts']['r']['episode']=1;self.assertNotIn('last applied',feedback(s,'r','aim',expected,'s'))

class AdapterClient(WindowClient):
    def __init__(self):super().__init__();self.on_bind=None;self.on_pulse=None;self.on_release=None;self.epoch=0
    def bind(self,rid,kind,adapter_id,config):
        self.epoch+=1;ack=dict(robot_id=rid,kind=kind,adapter_id=adapter_id,binding_epoch=self.epoch,activation_required=True,activation_version=1)
        self.calls.append(('bind',rid,kind,adapter_id,config))
        if self.on_bind:self.on_bind()
        return ack
    def adapter_input(self,rid,kind,binding_epoch,**kw):
        self.calls.append(('pulse',rid,kind,binding_epoch,kw))
        if self.on_pulse:self.on_pulse()
        return dict(accepted=True,binding_epoch=binding_epoch,window_id=kw['window_id'],activation_expires=900000.15)
    def release(self,rid,channels):
        if self.on_release:self.on_release()
        return super().release(rid,channels)

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.now=[100.];self.c=AdapterClient();self.t=PlatformTransport(role='operator',clock=lambda:self.now[0],client=self.c)
        self.t.connect_once();self.t.install_catalog(catalog(),connection_generation=self.t._connection_generation,serial=1)
        self.t.install_state(state());self.t.watch('red_infantry');self.t.process_jobs();self.t.install_state(state());self.t.install_frame(frame(self.t),generation=self.t._watch_generation)
    def choose(self,both=True):self.t.configure_adapters({'navigation':'nav_rule_v1','aim':'aim_pixel_baseline_v1' if both else 'Manual'},{'V2 Goal X':1})
    def claim(self):self.t.request_claim();self.t.process_jobs();self.t.heartbeat(False)
    def arm(self):self.assertTrue(self.t.activate());self.t.heartbeat(True)
    def sample(self):self.t.submit_input(vx_mps=1,vy_mps=0,fire=True)
    def ops(self,kind):return [c for c in self.c.calls if c[0]==kind]
    def ready(self):
        self.choose();self.claim();self.arm();self.sample();self.t.control_tick() # binds
        self.sample();self.t.control_tick() # fresh window
        self.sample()
    def test_choice_and_claim_never_bind(self):
        self.choose();self.claim();self.t.control_tick();self.assertEqual([],self.ops('bind'));self.assertFalse(self.t._focus)
    def test_second_arm_bind_both_then_fresh_sample(self):
        self.ready();self.assertEqual(2,len(self.ops('bind')));self.assertEqual([],self.ops('pulse'))
        self.t.control_tick();self.assertEqual(2,len(self.ops('pulse')));self.assertEqual(['fire'],[x[2] for x in self.ops('control')])
        self.assertTrue(self.ops('control')[0][3]['fire'])
    def test_aim_mouse_not_queued_for_return_to_manual(self):
        self.ready();before=list(self.t._angles);self.t.submit_mouse(.1,.1);self.assertEqual(before,self.t._angles)
    def test_new_plan_disarms_and_clears(self):
        self.ready();old=self.t._plan_generation;self.t.configure_adapters({'navigation':'nav_rule_v1'},{'V2 Goal X':2})
        self.assertGreater(self.t._plan_generation,old);self.assertFalse(self.t._focus);self.assertIsNone(self.t.owned_robot);self.assertEqual({},self.t._bindings)
    def test_late_bind_blur_releases_remote_lease(self):
        self.choose();self.claim();self.arm();self.c.on_bind=lambda:self.t.neutral('blur');self.sample();self.t.control_tick()
        self.assertEqual({},self.t._bindings);self.assertEqual(1,len(self.ops('release')));self.assertEqual([],self.ops('pulse'))
        self.t.process_jobs();self.assertEqual(1,len(self.ops('release')));self.assertFalse(self.c.uncertain)
    def test_late_bind_cleanup_failure_latches_uncertain(self):
        self.choose();self.claim();self.arm();self.c.on_bind=lambda:self.t.neutral('blur')
        def fail():raise RuntimeError('ambiguous cleanup')
        self.c.on_release=fail;self.sample();self.t.control_tick();self.assertTrue(self.c.uncertain);self.assertFalse(self.t._connected)
    def test_late_activation_releases_and_never_fires(self):
        self.ready();self.c.on_pulse=lambda:self.t.neutral('blur');self.t.control_tick()
        self.assertEqual([],self.ops('control'));self.assertEqual(1,len(self.ops('release')));self.assertEqual({},self.t._activation_acks)
    def test_catalog_update_disarms_old_ack_cannot_restore(self):
        self.ready();raw=catalog();raw['adapters'][0]['label']='updated'
        self.assertTrue(self.t.install_catalog(raw,connection_generation=self.t._connection_generation,serial=3))
        self.assertFalse(self.t.install_catalog(catalog(),connection_generation=self.t._connection_generation,serial=2));self.assertEqual({},self.t.plan);self.assertFalse(self.t._focus)
    def test_disconnect_clears_negotiation_and_plan(self):
        self.ready();generation=self.t._connection_generation
        self.t._failed('disconnect')
        self.assertFalse(self.t.catalog['ready']);self.assertEqual({},self.t.plan);self.assertLess(self.t._catalog_at,0)
        self.assertFalse(self.t.install_catalog(catalog(native=True),connection_generation=generation,serial=9))
    def test_stop_and_new_connection_require_fresh_catalog(self):
        self.choose();self.t.stop();self.assertFalse(self.t.catalog['ready']);self.assertEqual({},self.t.plan)
        self.t._stop.clear();self.t.connect_once();self.assertFalse(self.t.catalog['ready']);self.assertEqual({},self.t.plan)
    def test_watch_resets_to_manual(self):
        self.choose();self.t.watch('red_sentry');self.assertEqual({},self.t.plan)
    def test_missing_sensor_choice_rejected_before_claim(self):
        self.t.install_catalog(catalog(False),connection_generation=self.t._connection_generation,serial=2)
        with self.assertRaises(ValueError):self.t.configure_adapters({'aim':'aim_pixel_baseline_v1'}, {})
        self.assertEqual([],self.ops('claim'))


class OriginalUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        from unittest.mock import patch
        from rm_client.sim_platform_runtime import PlatformRuntime
        from rm_client.ui.sim_platform_window import PlatformWindow
        self.now=100.;self.c=AdapterClient();self.t=PlatformTransport(role='operator',clock=lambda:self.now,client=self.c);self.r=PlatformRuntime(transport=self.t)
        with patch.object(self.t,'start'):self.w=PlatformWindow(self.r)
        self.w.timer.stop();self.w.isActiveWindow=lambda:True;self.w.registry.get('AudioFeedback').enabled=False
        self.t.connect_once();self.t.install_catalog(catalog(),connection_generation=self.t._connection_generation,serial=1)
        self.t.install_state(state());self.r.select_robot(self.w,'red_infantry');self.t.process_jobs();self.t.install_state(state());self.t.install_frame(frame(self.t),generation=self.t._watch_generation);self.r.heartbeat(self.w)
    def tearDown(self):self.w.close();self.r._tmp.cleanup()
    def choose_nav(self):
        from PySide6.QtCore import QPointF,QRectF
        setting=self.w.registry.get('RobotProfile').get_setting('V2 Navigation')
        self.w.dispatch('jello_mode_select',(setting,'nav_rule_v1'),QPointF(),1,QRectF(0,0,100,20))
    def test_original_jello_typed_selection_is_local_only(self):
        self.choose_nav();self.assertEqual(self.t.plan['navigation']['adapter_id'],'nav_rule_v1');self.assertFalse(self.r.armed)
        self.assertFalse(any(x[0] in ('claim','bind','pulse') for x in self.c.calls))
    def test_original_two_f2_authorizes_plan(self):
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import Qt,QEvent
        self.choose_nav()
        event=lambda:QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_F2,Qt.KeyboardModifier.NoModifier)
        self.w.keyPressEvent(event());self.t.process_jobs();self.assertFalse(self.r.armed);self.assertFalse(any(x[0]=='bind' for x in self.c.calls))
        self.w.keyPressEvent(event());self.assertTrue(self.r.armed);self.t.submit_input(vx_mps=0,vy_mps=0);self.t.control_tick()
        self.assertTrue(any(x[0]=='bind' for x in self.c.calls));self.assertFalse(any(x[0]=='pulse' for x in self.c.calls))
    def test_settings_are_sim_only_and_native_na(self):
        from rm_client.model import build_default_registry
        self.assertIsNone(build_default_registry(discover_devices=False).get('RobotProfile').get_setting('V2 Navigation'))
        aim=self.w.registry.get('RobotProfile').get_setting('V2 Aim');self.assertNotIn('aim_native_rmcs_v1',aim.options)
        self.assertTrue(self.w.setting_is_unavailable(self.w.registry.get('RobotProfile').get_setting('V2 Aim FOV')))
        self.t.role='spectator';self.assertTrue(self.w.setting_is_unavailable(aim))

if __name__=='__main__':unittest.main()
