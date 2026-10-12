"""Bounded SIM ModList requests and authoritative presentation gates.

Only protocol capabilities unlock operational settings. Map/roster truth is
never consulted for aiming. The source-video fit defines the requested circle.
"""
from __future__ import annotations
import math
from .sim_projectile_damage import parse_observation,supports_damage_observation
from .sim_drive_frames import DRIVE_MODES,supports_drive_frames,validate_drive_feedback

SPIN_DEFAULT=dict(spin_enabled=False,spin_direction='auto',spin_speed_dps=420.,chassis_follow='free')
AIM_DEFAULT=dict(enabled=False,hold=False,fov_degrees=85.,circle_radius_x_norm=.25,circle_radius_y_norm=.25)

def number(value,low,high):
    if type(value) not in (int,float):raise ValueError('Expected finite number')
    try:valid=math.isfinite(value) and low<=value<=high
    except (OverflowError,ValueError):valid=False
    if not valid:raise ValueError('Number outside simulation contract')
    return float(value)

def spin_request(value,*,drive_frames=False):
    if not isinstance(value,dict) or set(value) not in (set(SPIN_DEFAULT),set(SPIN_DEFAULT)|{'spin_origin'}):raise ValueError('Invalid spin request')
    if 'spin_origin' in value and value['spin_origin'] not in ('manual','on_hit'):raise ValueError('Invalid spin origin')
    if type(value['spin_enabled']) is not bool or value['spin_direction'] not in ('auto','cw','ccw') or value['chassis_follow'] not in (DRIVE_MODES if drive_frames else ('free',)):raise ValueError('Unsupported chassis mode')
    return dict(value,spin_speed_dps=number(value['spin_speed_dps'],120,720))

def aim_request(value):
    if not isinstance(value,dict) or set(value)!=set(AIM_DEFAULT):raise ValueError('Invalid aim request')
    if type(value['enabled']) is not bool or type(value['hold']) is not bool:raise ValueError('Invalid aim flags')
    result=dict(value,fov_degrees=number(value['fov_degrees'],10,180))
    for key in ('circle_radius_x_norm','circle_radius_y_norm'):
        result[key]=number(value[key],1e-9,2.)
    return result

def circle_request(radius_logical,hud_scale,window_width,window_height,video_width,video_height):
    """Logical HUD radius -> source ellipse, excluding letterbox margins.

    Window dimensions are Qt logical pixels; device-pixel ratio cancels because
    video and HUD use the same paint surface. HUD scale still applies once.
    """
    vals=[number(v,1e-9,1e6) for v in (radius_logical,hud_scale,window_width,window_height,video_width,video_height)]
    radius,scale,ww,wh,vw,vh=vals
    fit=min(ww/vw,wh/vh);radius_px=radius*scale
    return number(radius_px/(vw*fit),1e-9,2),number(radius_px/(vh*fit),1e-9,2)

def supports_mouse_angle(session):
    caps=(session or {}).get('capabilities',{})
    return isinstance(caps,dict) and type(caps.get('mouse_angle_input_version')) is int and caps['mouse_angle_input_version']==1

def mouse_angle_request(value):
    if not isinstance(value,dict) or set(value)!={'generation','yaw_total_rad','pitch_total_rad'}:raise ValueError('Invalid mouse-angle request')
    generation=value['generation']
    if type(generation) is not int or not 0<=generation<=2**53-1:raise ValueError('Invalid mouse generation')
    return dict(generation=generation,yaw_total_rad=number(value['yaw_total_rad'],-1e6,1e6),pitch_total_rad=number(value['pitch_total_rad'],-1e6,1e6))

def supported_modes(session):
    caps=(session or {}).get('capabilities',{})
    if not isinstance(caps,dict):return False,False
    spin=(type(caps.get('operator_modes_version')) is int and caps['operator_modes_version']==1 and caps.get('spin_mode') is True and caps.get('chassis_follow_modes')==['free'])
    robots=caps.get('autoaim_robots',[])
    aim=(isinstance(caps.get('autoaim_source_identity'),str) and bool(caps['autoaim_source_identity']) and caps.get('autoaim_fire_authority')=='manual_only' and isinstance(robots,list) and all(isinstance(r,str) for r in robots) and type(caps.get('aim_request_version')) is int and caps['aim_request_version']==1 and caps.get('automatic_targeting') is True and (session or {}).get('robot_id') in robots)
    return spin,aim

def source_camera_metadata(headers,width,height,session):
    if not supported_modes(session)[1]:return None,None
    if headers.get('x-source-camera')!='true':raise ValueError('Qualified source camera required')
    nw=int(headers.get('x-native-frame-width','0'));nh=int(headers.get('x-native-frame-height','0'))
    if not 1<=nw<=16384 or not 1<=nh<=16384 or nw*height!=nh*width:raise ValueError('Invalid native/display mapping')
    seq=None
    if 'x-source-capture-seq' in headers:
        seq=int(headers['x-source-capture-seq'])
        if seq<0:raise ValueError('Invalid source capture index')
    return seq,(nw,nh)

def validate_feedback(data,session):
    """Validate optional feedback before it can become a confirmed UI state."""
    if supports_mouse_angle(session):
        control=data.get('control') or {};ack=control.get('mouse_angle_ack')
        if not isinstance(ack,dict):raise ValueError('Missing consumed mouse-angle acknowledgement')
        for key in ('generation','revoked_generation','consumed_control_seq'):
            if type(ack.get(key)) is not int or not -1<=ack[key]<=2**53-1:raise ValueError('Invalid mouse-angle acknowledgement sequence')
        for key in ('consumed_yaw_total_rad','consumed_pitch_total_rad'):number(ack.get(key),-1e6,1e6)
        if ack.get('reason') not in ('no_input','revoked','consumed','discarded_by_authority'):raise ValueError('Unknown mouse-angle acknowledgement state')
    if supports_damage_observation(session):
        parse_observation(data.get('own_damage'),session,data['sim_time'])
    spin,aim=supported_modes(session)
    mode=data.get('operator_modes')
    if mode is not None:
        if not isinstance(mode,dict) or mode.get('simulation_only') is not True:raise ValueError('Invalid mode feedback')
        spin_request(mode.get('requested'),drive_frames=supports_drive_frames(session))
        effective=mode.get('effective',{})
        if type(effective.get('spin_enabled')) is not bool:raise ValueError('Invalid effective spin')
        for key in ('spin_speed_dps','target_yaw_rate_rps'):number(effective.get(key),-100,1000)
        for key in ('chassis_yaw_rate_rps','yaw_torque'):number(mode.get('measured',{}).get(key),-1e6,1e6)
        if effective.get('chassis_follow') not in (DRIVE_MODES if supports_drive_frames(session) else ('free',)) or effective.get('direction_resolved') not in (None,'cw','ccw'):raise ValueError('Unknown effective mode')
        for key in ('request_seq','effective_seq'):
            v=mode.get(key)
            if v is not None and (type(v) is not int or not -1<=v<2**53):raise ValueError('Invalid mode sequence')
        if effective['spin_enabled'] and (not spin or mode.get('effective_seq') is None):raise ValueError('Unqualified effective spin')
        if 'spin_origin' in mode.get('requested',{}):
            if mode['requested']['spin_origin']=='on_hit' and not supports_damage_observation(session):raise ValueError('Unqualified hit-spin origin')
            expected_origin=mode['requested']['spin_origin'] if effective['spin_enabled'] else None
            if effective.get('spin_origin')!=expected_origin:raise ValueError('Wrong effective spin origin')
    validate_drive_feedback(data,session)
    auto=data.get('autoaim')
    if auto is not None:
        if not isinstance(auto,dict):raise ValueError('Invalid autoaim feedback')
        for key in ('requested','held','available','active','selected','fov_applied'):
            if type(auto.get(key)) is not bool:raise ValueError('Invalid aim feedback flag')
        fov=auto.get('fov')
        if not isinstance(fov,dict) or set(fov) not in (set(AIM_DEFAULT),set(AIM_DEFAULT)-{'enabled','hold'}):raise ValueError('Invalid feedback FOV')
        aim_request(dict(fov,enabled=auto['requested'],hold=auto['held']))
        if auto.get('authority') not in ('manual','source_autoaim') or auto.get('fire_authority')!='manual_only':raise ValueError('Unknown aim authority')
        if auto['active'] and (not aim or not all(auto[k] for k in ('requested','held','available','selected','fov_applied')) or auto.get('in_fov') is not True or auto.get('authority')!='source_autoaim'):raise ValueError('Unqualified automatic aiming')
        seq=auto.get('source_frame_seq');stamp=auto.get('source_sim_time')
        if seq is not None and (type(seq) is not int or seq<0):raise ValueError('Invalid source frame')
        if stamp is not None:number(stamp,0,1e12)
        if auto['active'] and (seq is None or stamp is None or not 0<=data['sim_time']-stamp<=.75):raise ValueError('Stale source aim')
        if auto['active']:
            if auto.get('simulation_only') is not True or auto.get('algorithm')!=(session.get('capabilities') or {}).get('autoaim_source_identity'):raise ValueError('Wrong source authority identity')
            revision=auto.get('input_revision');current=(data.get('control') or {}).get('safety_revision')
            if type(revision) is not int or type(current) is not int or revision<0 or revision!=current:raise ValueError('Old source input revision')

class SimModeBindings:
    def __init__(self,runtime):
        self.runtime=runtime;self.window=None;self.signature=None
        self.mode=dict(SPIN_DEFAULT);self.aim=dict(AIM_DEFAULT);self.support=(False,False);self.drive_support=False
    def sync(self,window):
        self.window=window;transport=self.runtime.transport
        support=supported_modes(transport.session);drive_support=supports_drive_frames(transport.session)
        if support!=self.support or drive_support!=self.drive_support:
            self.support=support;self.drive_support=drive_support
            self.runtime.feature_policy.set_supported('ChassisAssist',drive_support,{'Follow'})
            chassis=window.registry.get('ChassisAssist')
            if drive_support:
                chassis.description='SIM driving reference only: WASD in body/world/gimbal direction. No chassis yaw follow, gimbal stabilization or power/buffer protection.'
                chassis.get_setting('Follow').description='SIM驾驶参考方向：Free车体、Field固定世界、Gimbal云台方向。只改变WASD参考系，不让车体自动追云台。'
            self.runtime.feature_policy.set_supported('SpinMode',support[0],{'Direction','Spin Speed'})
            self.runtime.feature_policy.set_supported('ArmorAutoAim',support[1],{'Fov','Circle Size','Draw Fov'})
            self.runtime.disarm('Simulator capabilities changed: press F2 to resume')
            window.control.reset_inputs();window.invalidate_gui_cache()
        self.runtime.hit_spin.configure(window)
        if not any(support) and not drive_support:
            self.mode=dict(SPIN_DEFAULT);self.aim=dict(AIM_DEFAULT);self.signature=None
            return
        spin=window.registry.get('SpinMode');armor=window.registry.get('ArmorAutoAim')
        value=lambda module,name,default: module.get_setting(name).value if module and module.get_setting(name) else default
        chassis=window.registry.get('ChassisAssist')
        frame=str(value(chassis,'Follow','Free')).lower() if drive_support and chassis.enabled else 'free'
        mode=spin_request(dict(spin_enabled=bool(support[0] and spin.enabled),spin_direction=str(value(spin,'Direction','Auto')).lower(),spin_speed_dps=value(spin,'Spin Speed',420),chassis_follow=frame),drive_frames=drive_support)
        if supports_damage_observation(transport.session):mode['spin_origin']=self.runtime.hit_spin.origin
        aim=dict(AIM_DEFAULT,enabled=bool(support[1] and armor.enabled),fov_degrees=value(armor,'Fov',85))
        video=transport.snapshot()[1]
        if video is not None:
            rx,ry=circle_request(value(armor,'Circle Size',160),window.get_hud_scale(window.width(),window.height()),window.width(),window.height(),video.shape[1],video.shape[0])
            aim.update(circle_radius_x_norm=rx,circle_radius_y_norm=ry)
        aim=aim_request(aim)
        signature=(tuple(mode.items()),tuple(aim.items()))
        if self.signature is not None and signature!=self.signature and not self.runtime.hit_spin.consume_transition(self.mode,mode,self.aim,aim):
            self.runtime.disarm('SIM mode/range changed: release keys, then F2')
            window.control.reset_inputs()
        self.signature=signature;self.mode=mode;self.aim=aim
    def submit(self,right_down):
        aim=dict(self.aim,hold=bool(right_down and self.aim['enabled']))
        self.runtime.transport.submit_modes(self.mode if self.support[0] or self.drive_support else None,aim if self.support[1] else None)
    def feedback(self):
        t=self.runtime.transport.snapshot()[0]
        out=dict(spin=False,spin_origin=None,spin_reason='unavailable',measured_yaw_rate=None,aim=False,aim_available=False,aim_reason='unavailable',drive=None,drive_reason='unavailable')
        if not t:return out
        mode=t.get('operator_modes') or {};auto=t.get('autoaim') or {}
        out['spin_reason']=str(mode.get('reason','unavailable'));out['aim_reason']=str(auto.get('reason','unavailable'))
        out['measured_yaw_rate']=(mode.get('measured') or {}).get('chassis_yaw_rate_rps')
        live=bool(self.runtime.armed and self.runtime.transport._focus and self.window and self.window.isActiveWindow() and self.window.combat_control_input_allowed())
        sequence=mode.get('effective_seq')
        mode_fresh=type(sequence) is int and max(self.runtime.transport._lease_min_seq,self.runtime.transport._mode_min_seq)<=sequence<=self.runtime.transport._seq
        out['spin']=bool(live and mode_fresh and self.mode['spin_enabled'] and mode.get('requested')==self.mode and (mode.get('effective') or {}).get('spin_enabled'))
        drive=mode.get('drive') or {}
        out['drive_reason']=str(drive.get('reason','unavailable'))
        revision=drive.get('input_revision');current=(t.get('control') or {}).get('safety_revision')
        drive_fresh=type(revision) is int and type(current) is int and revision==current
        if live and self.drive_support and mode_fresh and drive_fresh and drive.get('active') and drive.get('measurement_valid',True) and drive.get('mode')==self.mode['chassis_follow'] and mode.get('requested')==self.mode:
            out['drive']=drive['mode']
        if out['spin']:out['spin_origin']=(mode.get('effective') or {}).get('spin_origin','manual')
        out['aim_available']=bool(self.support[1] and auto.get('available'))
        fov=auto.get('fov') or {}
        same_range=all(fov.get(k)==self.aim[k] for k in ('fov_degrees','circle_radius_x_norm','circle_radius_y_norm'))
        sequence=(t.get('control') or {}).get('last_seq')
        aim_fresh=type(sequence) is int and max(self.runtime.transport._lease_min_seq,self.runtime.transport._aim_hold_min_seq)<=sequence<=self.runtime.transport._seq
        out['aim']=bool(live and aim_fresh and self.aim['enabled'] and self.window.control.right_button_down and auto.get('active') and same_range)
        if not live:out['spin_reason']=out['aim_reason']=out['drive_reason']='disarmed'
        return out
