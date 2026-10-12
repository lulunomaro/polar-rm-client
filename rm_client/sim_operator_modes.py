"""Bounded SIM ModList requests and authoritative presentation gates.

Only protocol capabilities unlock operational settings. Map/roster truth is
never consulted for aiming. The source-video fit defines the requested circle.
"""
from __future__ import annotations
import math

SPIN_DEFAULT=dict(spin_enabled=False,spin_direction='auto',spin_speed_dps=420.,chassis_follow='free')
AIM_DEFAULT=dict(enabled=False,hold=False,fov_degrees=85.,circle_radius_x_norm=.25,circle_radius_y_norm=.25)

def number(value,low,high):
    if type(value) not in (int,float):raise ValueError('Expected finite number')
    try:valid=math.isfinite(value) and low<=value<=high
    except (OverflowError,ValueError):valid=False
    if not valid:raise ValueError('Number outside simulation contract')
    return float(value)

def spin_request(value):
    if not isinstance(value,dict) or set(value)!=set(SPIN_DEFAULT):raise ValueError('Invalid spin request')
    if type(value['spin_enabled']) is not bool or value['spin_direction'] not in ('auto','cw','ccw') or value['chassis_follow']!='free':raise ValueError('Unsupported chassis mode')
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
    spin,aim=supported_modes(session)
    mode=data.get('operator_modes')
    if mode is not None:
        if not isinstance(mode,dict) or mode.get('simulation_only') is not True:raise ValueError('Invalid mode feedback')
        spin_request(mode.get('requested'))
        effective=mode.get('effective',{})
        if type(effective.get('spin_enabled')) is not bool:raise ValueError('Invalid effective spin')
        for key in ('spin_speed_dps','target_yaw_rate_rps'):number(effective.get(key),-100,1000)
        for key in ('chassis_yaw_rate_rps','yaw_torque'):number(mode.get('measured',{}).get(key),-1e6,1e6)
        if effective.get('chassis_follow')!='free' or effective.get('direction_resolved') not in (None,'cw','ccw'):raise ValueError('Unknown effective mode')
        for key in ('request_seq','effective_seq'):
            v=mode.get(key)
            if v is not None and (type(v) is not int or not -1<=v<2**53):raise ValueError('Invalid mode sequence')
        if effective['spin_enabled'] and (not spin or mode.get('effective_seq') is None):raise ValueError('Unqualified effective spin')
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
        self.mode=dict(SPIN_DEFAULT);self.aim=dict(AIM_DEFAULT);self.support=(False,False)
    def sync(self,window):
        self.window=window;transport=self.runtime.transport
        support=supported_modes(transport.session)
        if support!=self.support:
            self.support=support
            self.runtime.feature_policy.set_supported('SpinMode',support[0],{'Direction','Spin Speed'})
            self.runtime.feature_policy.set_supported('ArmorAutoAim',support[1],{'Fov','Circle Size','Draw Fov'})
            self.runtime.disarm('Simulator capabilities changed: press F2 to resume')
            window.control.reset_inputs();window.invalidate_gui_cache()
        if not any(support):
            self.mode=dict(SPIN_DEFAULT);self.aim=dict(AIM_DEFAULT);self.signature=None
            return
        spin=window.registry.get('SpinMode');armor=window.registry.get('ArmorAutoAim')
        value=lambda module,name,default: module.get_setting(name).value if module and module.get_setting(name) else default
        mode=spin_request(dict(spin_enabled=bool(support[0] and spin.enabled),spin_direction=str(value(spin,'Direction','Auto')).lower(),spin_speed_dps=value(spin,'Spin Speed',420),chassis_follow='free'))
        aim=dict(AIM_DEFAULT,enabled=bool(support[1] and armor.enabled),fov_degrees=value(armor,'Fov',85))
        video=transport.snapshot()[1]
        if video is not None:
            rx,ry=circle_request(value(armor,'Circle Size',160),window.get_hud_scale(window.width(),window.height()),window.width(),window.height(),video.shape[1],video.shape[0])
            aim.update(circle_radius_x_norm=rx,circle_radius_y_norm=ry)
        aim=aim_request(aim)
        signature=(tuple(mode.items()),tuple(aim.items()))
        if self.signature is not None and signature!=self.signature:
            self.runtime.disarm('SIM mode/range changed: release keys, then F2')
            window.control.reset_inputs()
        self.signature=signature;self.mode=mode;self.aim=aim
    def submit(self,right_down):
        aim=dict(self.aim,hold=bool(right_down and self.aim['enabled']))
        self.runtime.transport.submit_modes(self.mode if self.support[0] else None,aim if self.support[1] else None)
    def feedback(self):
        t=self.runtime.transport.snapshot()[0]
        out=dict(spin=False,spin_reason='unavailable',measured_yaw_rate=None,aim=False,aim_available=False,aim_reason='unavailable')
        if not t:return out
        mode=t.get('operator_modes') or {};auto=t.get('autoaim') or {}
        out['spin_reason']=str(mode.get('reason','unavailable'));out['aim_reason']=str(auto.get('reason','unavailable'))
        out['measured_yaw_rate']=(mode.get('measured') or {}).get('chassis_yaw_rate_rps')
        live=bool(self.runtime.armed and self.runtime.transport._focus and self.window and self.window.isActiveWindow() and self.window.combat_control_input_allowed())
        sequence=mode.get('effective_seq')
        mode_fresh=type(sequence) is int and self.runtime.transport._lease_min_seq<=sequence<=self.runtime.transport._seq
        out['spin']=bool(live and mode_fresh and self.mode['spin_enabled'] and mode.get('requested')==self.mode and (mode.get('effective') or {}).get('spin_enabled'))
        out['aim_available']=bool(self.support[1] and auto.get('available'))
        fov=auto.get('fov') or {}
        same_range=all(fov.get(k)==self.aim[k] for k in ('fov_degrees','circle_radius_x_norm','circle_radius_y_norm'))
        sequence=(t.get('control') or {}).get('last_seq')
        aim_fresh=type(sequence) is int and max(self.runtime.transport._lease_min_seq,self.runtime.transport._aim_hold_min_seq)<=sequence<=self.runtime.transport._seq
        out['aim']=bool(live and aim_fresh and self.aim['enabled'] and self.window.control.right_button_down and auto.get('active') and same_range)
        if not live:out['spin_reason']=out['aim_reason']='disarmed'
        return out
