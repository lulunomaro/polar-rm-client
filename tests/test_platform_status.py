"""Synthetic status data and logical rectangle tests, no physics/network."""
import copy,math,threading,unittest
from types import SimpleNamespace as N
from rm_client.sim_platform_spin import PlatformModes
from rm_client.sim_platform_status import SpinStatusPresenter,status_lines,status_layout

def fixture():
    wanted=dict(direction='auto',speed_dps=420.)
    motion=dict(mode='spin_v1',frame='base_link',vx_mps=0.,vy_mps=0.,**wanted)
    lease=dict(mode_fault=False,owner='s',epoch=1,seq=2,input_window_id=3,input_expires=100.1,input_window_deadline=100.15,expires=101.,input=copy.deepcopy(motion))
    receipt=dict(episode=0,robot_id='a',phase='integration_entry',consume_wall=100.05,consume_sim_time=1.,target={'motion':copy.deepcopy(motion)},manual_inputs={'motion':copy.deepcopy(lease)})
    raw=dict(episode=0,sim_time=1.002,control={'server_monotonic_s':100.06,'leases':{'a':{'motion':copy.deepcopy(lease)}}},spin_consumption_receipts={'a':{'entry':receipt,'final':copy.deepcopy(receipt)}},motion_feedback={'a':dict(integration_sim_time=1.,timestep_seconds=.002,sample_sim_time=1.002,measurement_valid=True,spin_requested=True,measured_yaw_rate_rps=5.,target_yaw_rate_rps=420*math.pi/180,direction_resolved='ccw',saturated=False,applied_yaw_torque_nm=9.)})
    clock=[200.06]
    t=N(raw_state=raw,raw_state_at=200.06,_lock=threading.RLock(),_focus=True,_connected=True,_stop=threading.Event(),_connection_generation=1,_watch_generation=2,watch_epoch=1,role='operator',plan={},owned_robot='a',
        client=N(uncertain=False,session_id='s'),spin_capability=lambda:True,
        _window=dict(window_id=3,lease_epochs={'motion':1},robot_id='a',session_id='s',issued_wall=100.,expires_wall=100.15),
        _window_deadline=200.15,clock=lambda:clock[0],watch_robot='a',owned_epochs={'motion':1})
    r=N(transport=t,armed=True)
    r.modes=m=PlatformModes(r);m.support=(True,False);m.enabled=True;m.signature=(True,'auto',420.);m.cap_identity=('s','a',1,0);m.window=N(isActiveWindow=lambda:True,combat_control_input_allowed=lambda:True)
    return r,t,raw,clock,SpinStatusPresenter(r)

class StatusTests(unittest.TestCase):
    def setUp(self):self.r,self.t,self.raw,self.now,self.p=fixture()
    def test_four_states_are_distinct(self):
        s=self.p.snapshot();self.assertTrue(s['requested']);self.assertTrue(s['admitted']);self.assertTrue(s['snapshot_gate']);self.assertEqual(5.,s['last_applied']['measured_yaw_rate_rps']);self.assertNotEqual(5.,s['last_applied']['target_yaw_rate_rps'])
    def test_request_alone_is_not_admission(self):
        self.raw['control']['leases']['a']['motion']['input']=None;s=self.p.snapshot();self.assertTrue(s['requested']);self.assertFalse(s['admitted']);self.assertFalse(s['snapshot_gate']);self.assertIsNone(s['last_applied'])
    def test_admission_without_application(self):
        self.raw['spin_consumption_receipts']['a']['final']['target']['motion']=None;s=self.p.snapshot();self.assertTrue(s['admitted']);self.assertFalse(s['snapshot_gate']);self.assertIsNone(s['last_applied'])
    def test_disarm_retains_explicit_history(self):
        self.p.snapshot();self.r.armed=False;s=self.p.snapshot();self.assertFalse(s['snapshot_gate']);self.assertFalse(s['admitted']);self.assertEqual(1,s['last_applied']['lease_epoch']);self.assertTrue(any('HIST' in x for x in status_lines(s)))
    def test_repeat_poll_and_paint_do_not_wash_receive_age(self):
        self.p.snapshot();self.now[0]+=.02;self.t.raw_state_at=self.now[0];s=self.p.snapshot();self.assertAlmostEqual(.02,s['last_applied']['receive_age']);self.assertEqual(0,s['snapshot_receive_age']);self.assertAlmostEqual(.02,self.p.snapshot()['last_applied']['receive_age'])
    def test_same_sim_changed_seq_does_not_refresh_history(self):
        self.p.snapshot();self.now[0]+=.02;self.t.raw_state_at=self.now[0]
        for x in (self.raw['control']['leases']['a']['motion'],self.raw['spin_consumption_receipts']['a']['entry']['manual_inputs']['motion'],self.raw['spin_consumption_receipts']['a']['final']['manual_inputs']['motion']):x['seq']=3
        s=self.p.snapshot();self.assertEqual(2,s['last_applied']['seq']);self.assertAlmostEqual(.02,s['last_applied']['receive_age'])
    def test_later_poststep_can_refresh(self):
        self.p.snapshot();self.now[0]+=.02;self.t.raw_state_at=self.now[0];self.raw['sim_time']+=.002
        for q in self.raw['spin_consumption_receipts']['a'].values():q['consume_sim_time']+=.002
        sample=self.raw['motion_feedback']['a'];sample['integration_sim_time']+=.002;sample['sample_sim_time']+=.002
        self.assertEqual(0,self.p.snapshot()['last_applied']['receive_age'])
    def test_disconnect_clears_all_history(self):
        self.p.snapshot();self.t.client.uncertain=True;s=self.p.snapshot();self.assertIsNone(s['last_applied']);self.assertFalse(s['admitted']);self.assertFalse(s['snapshot_gate'])
    def test_identity_change_clears_history(self):
        self.p.snapshot();self.t.watch_epoch+=1;self.r.armed=False;self.assertIsNone(self.p.snapshot()['last_applied'])
    def test_capability_loss_revokes_snapshot_gate(self):
        self.p.snapshot();self.t.spin_capability=lambda:None;s=self.p.snapshot();self.assertFalse(s['admitted']);self.assertFalse(s['snapshot_gate']);self.assertIsNotNone(s['last_applied'])
    def test_old_snapshot_is_never_active(self):
        self.p.snapshot();self.now[0]+=1;s=self.p.snapshot();self.assertFalse(s['snapshot_gate']);self.assertTrue(s['last_applied']['stale'])
    def test_invalid_or_future_local_time(self):
        for at in (200.07,True,float('nan'),float('inf')):
            with self.subTest(at=at):self.t.raw_state_at=at;s=self.p.snapshot();self.assertFalse(s['snapshot_gate']);self.assertIsNone(s['snapshot_receive_age'])
    def test_expiry_equal_rejects(self):
        self.raw['control']['server_monotonic_s']=100.1;s=self.p.snapshot();self.assertFalse(s['admitted']);self.assertFalse(s['snapshot_gate'])
    def test_wrong_window_deadline_rejects(self):
        self.raw['control']['leases']['a']['motion']['input_window_deadline']=100.25;self.assertFalse(self.p.snapshot()['admitted'])
    def test_bool_epoch_rejects(self):
        self.raw['episode']=False;s=self.p.snapshot();self.assertFalse(s['snapshot_gate']);self.assertIsNone(s['last_applied'])
    def test_return_copy_cannot_mutate_history(self):
        s=self.p.snapshot();s['last_applied']['seq']=999;self.assertEqual(2,self.p.snapshot()['last_applied']['seq'])
    def test_auto_navigation_excludes_manual_display_gate(self):
        self.t.plan={'navigation':{}};self.assertFalse(self.p.snapshot()['admitted'])

class LayoutTests(unittest.TestCase):
    @staticmethod
    def overlap(a,b):return a[0]<b[0]+b[2] and b[0]<a[0]+a[2] and a[1]<b[1]+b[3] and b[1]<a[1]+a[3]
    def test_original_overlay_rectangles_intersect(self):self.assertTrue(self.overlap((18,58,1324,24),(14,62,430,20)))
    def test_new_rows_do_not_overlap_each_other_or_reserved_header(self):
        for width,height in [(1360,900),(1920,1080),(1280,720),(960,720)]:
            rows=status_layout(width,height,8)
            for i,a in enumerate(rows):
                self.assertTrue(0<=a[0]<a[0]+a[2]<=width);self.assertTrue(0<=a[1]<a[1]+a[3]<=height)
                self.assertFalse(self.overlap(a,(width/2-230,0,460,180)))
                for b in rows[i+1:]:self.assertFalse(self.overlap(a,b))

class OriginalLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def test_actual_original_regions_four_sizes_two_styles(self):
        from test_platform_adapters import OriginalUiTests
        from PySide6.QtGui import QPicture,QPainter
        from rm_client.ui.sim_platform_status_layout import reserved_regions
        from rm_client.sim_platform_status import overlaps
        case=OriginalUiTests();case.setUp()
        try:
            w=case.w;w.home_visible=False;w.gui_visible=False
            for mc in (True,False):
                w.registry.get('HUD').get_setting('Combat Preset').value='Minecraft 1.21.11' if mc else 'Polar'
                w.registry.get('MinecraftCombatHUD').enabled=mc
                for size in ((1360,900),(1920,1080),(1280,720),(960,720)):
                    with self.subTest(mc=mc,size=size):
                        w.resize(*size);pic=QPicture();p=QPainter(pic)
                        try:w.paint_overlay(None,painter=p,logical_size=size,hud_background_ready=True,capture_module_list=True)
                        finally:p.end()
                        regions=reserved_regions(w,*size);rows=status_layout(*size,8,regions)
                        self.assertTrue(rows,repr((size,mc,regions)))
                        for row in rows:self.assertFalse(any(overlaps(row,r) for r in regions))
        finally:case.tearDown()

class StaleSidebarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def test_extra_paint_invalidates_stale_model_before_original_sidebar(self):
        from test_platform_adapters import OriginalUiTests
        from test_offline_platform_view import Painter
        from unittest.mock import patch
        from rm_client.ui.sim_platform_window import PolarRMWindow
        from rm_client.ui.minecraft_hud import MinecraftCombatHudRenderer
        case=OriginalUiTests();case.setUp()
        try:
            case.r.apply_state(case.w.state);self.assertTrue(case.w.state.mujoco_telemetry_valid)
            case.now+=.75
            before=tuple(x[0] for x in MinecraftCombatHudRenderer.scoreboard_lines(case.w.state))
            self.assertFalse(any('STALE' in x for x in before))
            observed=[]
            def original(*a,**k):observed.extend(x[0] for x in MinecraftCombatHudRenderer.scoreboard_lines(case.w.state));return True
            with patch.object(PolarRMWindow,'paint_overlay',side_effect=original):case.w.paint_overlay(None,painter=Painter(),logical_size=(1360,900))
            self.assertTrue(any('STALE' in x for x in observed));self.assertFalse(any('300' in x for x in observed));self.assertFalse(any('4:59' in x for x in observed))
        finally:case.tearDown()

class ScaledReservationsTests(unittest.TestCase):
    def test_hud_rects_scale_but_minecraft_stays_viewport(self):
        from PySide6.QtCore import QRectF
        from rm_client.ui.sim_platform_status_layout import reserved_regions
        layout=N(bounds=N(x=100.,y=50.,width=200.,height=80.))
        registry=N(get=lambda key:N(enabled=key=='TacticalMap'))
        w=N(top_match_bar=N(last_layout=layout,last_progress_bounds=QRectF(100,130,200,30)),get_hud_scale=lambda *a:1.5,registry=registry,minecraft_hud_active=lambda:True,setting_bool=lambda *a:True,setting_mode=lambda *a:'JelloActiveMods',tactical_map_state=N(container=N(x=10.,y=40.,width=100.,height=80.)),_last_minecraft_hud_layout=N(hotbar=QRectF(400,650,300,50),scoreboard=QRectF(1000,300,250,200)))
        regions=reserved_regions(w,1360,900)
        self.assertIn((150.,75.,300.,120.),regions);self.assertIn((150.,195.,300.,45.),regions)
        self.assertIn((9.,54.,162.,132.),regions)
        self.assertIn((392.,642.,316.,66.),regions)


class MappedIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def check_switch(self,kind):
        from test_platform_adapters import OriginalUiTests
        from test_offline_platform_view import Painter
        from unittest.mock import patch
        from rm_client.ui.sim_platform_window import PolarRMWindow
        case=OriginalUiTests();case.setUp()
        try:
            case.r.apply_state(case.w.state);self.assertTrue(case.w.state.mujoco_telemetry_valid)
            t=case.t
            if kind=='robot':t.watch_robot='red_sentry';t.telemetry['robot_id']='red_sentry';t._watch_generation+=1
            elif kind=='episode':t.raw_state['episode']+=1
            elif kind=='sample':t.raw_state_at+=.01;case.now+=.01
            elif kind=='connection':t._connection_generation+=1
            observed=[]
            with patch.object(PolarRMWindow,'paint_overlay',side_effect=lambda *a,**k:observed.append(case.w.state.mujoco_telemetry_valid)):
                case.w.paint_overlay(None,painter=Painter(),logical_size=(1360,900))
            self.assertEqual([False],observed)
        finally:case.tearDown()
    def test_episode_requires_remap(self):self.check_switch('episode')
    def test_robot_requires_remap(self):self.check_switch('robot')
    def test_new_sample_requires_remap(self):self.check_switch('sample')
    def test_connection_requires_remap(self):self.check_switch('connection')
    def test_mapping_and_fresh_paint_keep_valid(self):
        from test_platform_adapters import OriginalUiTests
        from test_offline_platform_view import Painter
        from unittest.mock import patch
        from rm_client.ui.sim_platform_window import PolarRMWindow
        case=OriginalUiTests();case.setUp()
        try:
            case.r.apply_state(case.w.state)
            with patch.object(PolarRMWindow,'paint_overlay',return_value=True):case.w.paint_overlay(None,painter=Painter(),logical_size=(1360,900))
            self.assertTrue(case.w.state.mujoco_telemetry_valid)
        finally:case.tearDown()

if __name__=='__main__':unittest.main()
