"""Simulation-only dependency bundle for the original POLAR window.

No physical backend instances exist in this runtime. Existing input collection,
key bindings, GUI, match HUD and map are reused; their only command sink is the
loopback simulator client.
"""
from __future__ import annotations
import math
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from .control import KeyboardControlBridge
from .model import build_default_registry
from .runtime_paths import RuntimePaths
from .sim_operator_transport import SimOperatorTransport
from .sim_operator_features import SimFeaturePolicy
from .sim_operator_modes import SimModeBindings
from .top_match_bar import LifeState, MatchBarFrame, RobotCard, TeamStrip

IDS={'red_hero':1,'red_infantry':3,'red_sentry':7,'blue_hero':101,'blue_infantry':103,'blue_sentry':107}


class NoIOService:
    """Explicit inert service interface; never loads a driver or endpoint."""
    connected=False;running=False;mock_output=True;reconnect_count=0;receive_generation=0
    disconnected_at=0.;host='';port=0;last_error='Simulation only';client_id='3'
    external_on_frame=None;on_frame=None
    def start(self,*a,**k):return False
    def stop(self,*a,**k):return None
    def configure(self,*a,**k):return None
    def configure_autoaim_fov(self,*a,**k):return None
    def configure_prediction(self,*a,**k):return None
    def submit(self,*a,**k):return None
    def latest(self,*a,**k):return None
    def latest_frame(self,*a,**k):return None
    def drain_received_messages(self):return []
    def decoded_frame_fresh(self,*a,**k):return False
    def clear_stale_decoded_frame(self,*a,**k):return None
    def reject(self,*a,**k):return False,'Physical transport unavailable in simulation mode'
    publish_estop_zero=reject
    publish_air_support_command=reject
    publish_common_command=reject


class SimInputBridge(KeyboardControlBridge):
    def __init__(self,runtime):
        super().__init__(None,mock_output=True)
        self.runtime=runtime
    def send_command(self,command):
        # Never call super: even configured legacy egress flags have no outlet.
        self.last_command=command;self.last_send_at=time.perf_counter()
        self.runtime.transport.submit_input(vx_mps=command.vx,vy_mps=-command.vy,
            yaw_rate_rps=0.,pitch_rate_rps=0.,
            fire=bool(command.left_button_down))
        self.runtime.transport.submit_mouse(-command.mouse_x*.002,command.mouse_y*.002)
        self.runtime.modes.submit(command.right_button_down)
        self._dirty=False;self._consume_relative_motion()
    def tick(self,state,registry,now=None):
        return self.send_from_state(state,registry)
    def release_direct_dt7(self):return None
    def release_custom_controller(self):return None
    def release_all(self,state,registry):
        self.reset_inputs();self.runtime.transport.neutral('Input released')
        return self.build_command(state,registry)


class SimBackend:
    scenario_key='MuJoCoOperator'
    def __init__(self,runtime,state):
        self.runtime=runtime;self.state=state
        self.mqtt=NoIOService();self.udp=NoIOService();self.serial=NoIOService();self.polar=NoIOService()
        self.referee=SimpleNamespace(profile_id='simulation-observer-v1',protocol_version='not-wire')
        self.safety=SimpleNamespace(estop_at=0.,estop_active=False,control_state=SimpleNamespace(value='LOCKED'))
        self.field_log=SimpleNamespace(emit=lambda *a,**k:None)
        self.qualification=SimpleNamespace(session=None)
        self.input_clear_hook=None;self.referee_view_observer=None
    def reject(self,*a,**k):return False,'Only explicit virtual operator commands are supported'
    publish_keyboard_mouse=reject;publish_spin_control=reject;publish_vision_control=reject
    publish_hero_deploy=reject;publish_map_click_cmd=reject;publish_assembly_command=reject
    publish_dart_command=reject;publish_performance_from_registry=reject;publish_rune_activate=reject
    publish_sentry_command=reject;arm_match_control=reject;record_firmware_zero_observation=reject
    def begin_qualification(self,*a,**k):raise ValueError('Hardware qualification unavailable in simulation mode')
    def apply_scenario(self,*a,**k):
        self.state.mock_simulation_enabled=False
        self.runtime.disarm('Simulation mode is fixed for this window')
    def sync_mqtt_link(self,*a,**k):return 'simulation only'
    def set_active_video_source(self,*a,**k):return None
    def set_client_id(self,robot_id):
        self.mqtt.client_id=str(robot_id)
        self.runtime.disarm('UI identity changed; simulator selection remains authoritative')
    def tick_polar_link(self,*a,**k):return {'physical_tx':False}
    def tick(self):return None
    def gimbal_egress_owner(self,*a,**k):return 'none'
    def stats(self):return {'simulation':True,'physical_tx':False,'safety_evidence':{},'udp':{},'native_0310':{}}
    def apply_readiness(self,state,**k):
        value=dict(simulation=True,physical_tx=False,ready=self.runtime.transport.snapshot()[0] is not None,
                   note='Simulation operator only; no hardware qualification')
        state.backend_stats['readiness']=value
        return value
    def apply_updates(self,state):self.runtime.apply_state(state);return 1
    def latest_video_frame(self,*a,**k):
        _,video,stamp,_,_=self.runtime.transport.snapshot()
        return video,stamp,'mujoco-first-person'
    def lock_control(self,reason='locked'):
        self.runtime.disarm(reason)
        if self.input_clear_hook:self.input_clear_hook(reason)
    def trigger_estop(self):
        self.safety.estop_active=True;self.safety.estop_at=time.perf_counter();self.lock_control('SIM emergency stop')
        return True,'Virtual movement and fire stopped'
    def release_estop(self):self.safety.estop_active=False;self.runtime.disarm('Press F2 to resume virtual input')
    def shutdown(self,*a,**k):self.runtime.stop()
    def simulation_match_bar(self,now):return self.runtime.match_bar()


class OperatorRuntime:
    simulation_only=True
    def __init__(self,url='http://127.0.0.1:8768',*,transport=None):
        self.transport=transport or SimOperatorTransport(url)
        self._tmp=tempfile.TemporaryDirectory(prefix='polar-sim-operator-')
        self.paths=RuntimePaths.discover(environ={'POLAR_USER_DATA_ROOT':self._tmp.name})
        self.camera=NoIOService();self.local_vision=NoIOService();self.tongji_receiver=NoIOService();self.tongji_bridge=NoIOService()
        self.armed=False;self.note='F2: enable virtual operator input; F12: stop'
        self._generation=-1;self.backend=None;self.feature_policy=None;self.modes=SimModeBindings(self)
    def registry(self):
        registry=build_default_registry(discover_devices=False)
        # Session-local options. Original user settings remain untouched.
        for module,name,value in [('MatchMode','Mode','RMUL3v3'),('TopMatchBar','Match Scope','All Match Modes'),
                                  ('CameraFeed','Source','Simulation'),('CameraFeed','Mirror',False)]:
            m=registry.get(module);setting=m.get_setting(name) if m else None
            if setting is not None:
                if name=='Source' and value not in setting.options:setting.options.append(value)
                setting.value=value
        self.feature_policy=SimFeaturePolicy(registry)
        return registry
    def create_backend(self,state):
        self.backend=SimBackend(self,state);return self.backend
    def create_control(self):return SimInputBridge(self)
    def start(self):self.transport.start()
    def stop(self):self.disarm('Disconnected');self.transport.stop()
    def disarm(self,reason='Virtual controls released'):
        self.armed=False;self.note=reason;self.transport.neutral(reason)
    def arm(self):
        telemetry,video,*_=self.transport.snapshot()
        if telemetry is None or video is None or telemetry.get('phase')!='running' or not telemetry['own']['alive']:
            self.note='Need current simulator state and first-person video before controlling';return False
        if self.backend.safety.estop_active:
            self.backend.release_estop()
        self.armed=True;self.note='SIM operator active: WASD / mouse / left click';return True
    def heartbeat(self,window):
        try:self.modes.sync(window)
        except (ValueError,TypeError,OverflowError) as exc:
            self.disarm('Invalid SIM request: '+str(exc));window.control.reset_inputs()
            return
        if self._generation!=self.transport._generation:
            self._generation=self.transport._generation;self.disarm('Session changed: release inputs, then F2')
            window.control.reset_inputs()
        telemetry,video,*_=self.transport.snapshot()
        allowed=bool(self.armed and telemetry is not None and video is not None and telemetry.get('phase')=='running' and telemetry['own']['alive'] and window.isActiveWindow() and window.combat_control_input_allowed())
        self.transport.heartbeat(allowed)
        if self._generation!=self.transport._generation:
            self._generation=self.transport._generation;allowed=False
            self.disarm('UI heartbeat expired: press F2 to resume')
        if not allowed:
            window.control.reset_inputs()
            if self.armed:self.disarm('Input focus unavailable: press F2 to resume')
        if telemetry is None or video is None:
            self.armed=False
    def match_bar(self):
        telemetry=self.transport.snapshot()[0]
        robots=telemetry.get('robots',[]) if telemetry else []
        teams=[]
        for team in ('red','blue'):
            cards=[]
            for name,display_id in IDS.items():
                if not name.startswith(team+'_'):continue
                r=next((r for r in robots if r.get('robot_id')==name),{})
                hp=r.get('hp');maximum=r.get('max_hp')
                cards.append(RobotCard(display_id,name.split('_')[1].upper(),
                    LifeState.UNKNOWN if hp is None else LifeState.ALIVE if hp>0 else LifeState.DEAD,
                    health=None if hp is None else int(hp),max_health=None if maximum is None else int(maximum),
                    local=bool(telemetry and name==telemetry['robot_id'])))
            teams.append(TeamStrip(team.title(),tuple(cards),telemetry.get('vp',{}).get(team) if telemetry else None))
        remaining=(telemetry.get('remaining_seconds') if telemetry else None)
        phase=str(telemetry.get('phase','UNKNOWN')) if telemetry else 'DISCONNECTED'
        return MatchBarFrame(*teams,countdown=None if remaining is None else math.ceil(remaining),score_label='SIM VP',phase=phase,roster_label='SIM 3v3')
    def apply_state(self,state):
        telemetry=self.transport.snapshot()[0]
        state.mock_simulation_enabled=False;state.official_state_active=False;state.mujoco_operator=True
        state.mujoco_unknown_fields=('chassis_power_w','power_limit','capacitor_percent','heat_cooldown_rate','base_health','outpost_health','base_shield','current_chassis_energy','current_buffer_energy','gimbal_power','speed_limit','friction_wheel','lock_state','anti_rotation','target_id','target_distance_m','total_projectiles_fired','remaining_economy','total_economy_obtained','control_progress')
        state.mujoco_telemetry_valid=telemetry is not None
        state.vision_backend='MuJoCo operator camera';state.vision_connected=False
        state.mqtt_connected=state.serial_connected=False;state.polar_link_active=False
        state.small_buff=state.large_buff=state.jump_ready=state.pte_ready=state.wheel_online=state.les_online=False
        state.target_locked=False;state.energy_target_locked=False;state.buff_rows=[]
        state.friction_wheel=state.lock_state=state.anti_rotation=False
        state.vision_tracking_state='unavailable';state.vision_calibrated=False
        state.target_id=-1;state.target_distance_m=state.target_health=state.target_max_health=0
        state.current_chassis_energy=state.current_buffer_energy=state.gimbal_power=state.speed_limit=0
        state.base_shield=state.enemy_base_shield=0;state.base_status=state.enemy_base_status='UNKNOWN'
        state.outpost_status=state.enemy_outpost_status='UNKNOWN'
        state.can_remote_heal=state.can_remote_ammo=state.out_of_combat=False
        state.total_projectiles_fired=state.total_economy_obtained=state.control_progress=0
        state.projectile_shop_state='UNAVAILABLE';state.economy_mock_mode=False
        state.base_health=state.enemy_base_health=state.outpost_health=state.enemy_outpost_health=0
        state.chassis_power_w=state.power_limit=state.capacitor_percent=state.heat_cooldown_rate=0
        state.remaining_economy=0;state.supply_zone_known=False
        state.match_mode='RMUL3v3';state.match_title='MuJoCo operator / SIM ONLY'
        if telemetry is None:
            state.current_stage='NOT_STARTED';state.stage_countdown_sec=0;state.current_health=0
            state.max_health=1;state.remaining_ammo=0;state.current_heat=0;state.max_heat=1;state.map_robots=[]
            return
        own=telemetry['own'];state.robot_id=IDS[own['robot_id']];state.alliance_color=own['team'].title()
        state.robot_profile={'hero':'Hero','infantry':'Infantry1','sentry':'Sentinel'}[own['role']]
        state.robot_type={'hero':'Hero','infantry':'Infantry','sentry':'Sentinel'}[own['role']]
        state.current_stage='MATCH' if telemetry['phase']=='running' else str(telemetry['phase']).upper()
        state.stage_elapsed_sec=int(telemetry['sim_time']);remaining=telemetry.get('remaining_seconds')
        state.stage_countdown_sec=math.ceil(remaining) if remaining is not None else 0
        state.match_duration_sec=int(telemetry['sim_time']+remaining) if remaining is not None else 0
        for field,key in [('current_health','hp'),('max_health','max_hp'),('remaining_ammo','ammo'),('current_heat','heat'),('max_heat','heat_max')]:
            value=own.get(key);setattr(state,field,int(value) if value is not None else 0)
        state.ammo_capacity=max(1,state.remaining_ammo);state.projectile_allowance_17mm=int(state.remaining_ammo) if own['role']!='hero' else 0
        state.projectile_allowance_42mm=int(state.remaining_ammo) if own['role']=='hero' else 0
        for field,key in [('chassis_yaw_deg','chassis_yaw'),('gimbal_yaw_deg','gimbal_yaw')]:
            setattr(state,field,math.degrees(own.get(key) or 0))
        state.relative_yaw_deg=state.gimbal_yaw_deg
        state.gimbal_yaw_deg=(state.chassis_yaw_deg+state.relative_yaw_deg+180)%360-180
        if self.feature_policy:self.feature_policy.sync_identity(state)
        side=own['team'];other='blue' if side=='red' else 'red'
        state.ally_victory_points=telemetry.get('vp',{}).get(side,0);state.enemy_victory_points=telemetry.get('vp',{}).get(other,0)
        owner=telemetry.get('control_owner');state.control_owner='NEUTRAL' if owner is None else 'ALLY' if owner==side else 'ENEMY'
        state.map_robots=[dict(id=IDS[r['robot_id']],name=r['robot_id'],team='ally' if r['team']==side else 'enemy',x=r['xy'][0]+6,y=r['xy'][1]+4,hp=r.get('hp'),max=r.get('max_hp')) for r in telemetry.get('robots',[]) if r.get('robot_id') in IDS and r.get('xy') and r.get('hp') is not None and r.get('max_hp') is not None]
        state.robot_rows=[dict(id=IDS[r['robot_id']],name=r['robot_id'],hp=r.get('hp'),max=r.get('max_hp'),state='SIM') for r in telemetry.get('robots',[]) if r.get('robot_id') in IDS and r.get('hp') is not None and r.get('max_hp') is not None]
