"""SIM driving reference frames, not chassis heading follow or gimbal stability."""
from __future__ import annotations
import math

DRIVE_MODES=('free','gimbal','field')

def supports_drive_frames(session):
    caps=(session or {}).get('capabilities',{})
    modes=caps.get('chassis_drive_modes') if isinstance(caps,dict) else None
    return (isinstance(caps,dict) and type(caps.get('chassis_drive_frame_version')) is int
            and caps['chassis_drive_frame_version']==1
            and isinstance(modes,list) and len(modes)==3 and all(type(m) is str for m in modes)
            and set(modes)==set(DRIVE_MODES))

def finite_vector(value,label):
    if not isinstance(value,list) or len(value)!=2:raise ValueError('Invalid '+label)
    out=[]
    for n in value:
        if type(n) not in (int,float):raise ValueError('Invalid '+label)
        try:f=float(n)
        except (ValueError,OverflowError):raise ValueError('Invalid '+label) from None
        if not math.isfinite(f):raise ValueError('Invalid '+label)
        out.append(f)
    return out

def validate_drive_feedback(data,session):
    if not supports_drive_frames(session):return
    modes=data.get('operator_modes')
    if not isinstance(modes,dict):raise ValueError('Missing drive-frame feedback')
    drive=modes.get('drive')
    if not isinstance(drive,dict):raise ValueError('Missing drive-frame observation')
    if drive.get('mode') not in DRIVE_MODES or type(drive.get('active')) is not bool or type(drive.get('saturated')) is not bool:raise ValueError('Invalid drive-frame status')
    if drive.get('input_kind') not in ('legacy_chassis','drive_axes'):raise ValueError('Unknown drive input kind')
    if type(drive.get('measurement_valid',True)) is not bool:raise ValueError('Invalid drive measurement validity')
    if drive['active'] and drive.get('measurement_valid') is False:raise ValueError('Unqualified drive measurement')
    yaw=drive.get('reference_yaw_rad')
    if type(yaw) not in (int,float):raise ValueError('Invalid drive yaw')
    try:valid=math.isfinite(yaw)
    except (ValueError,OverflowError):valid=False
    if not valid:raise ValueError('Invalid drive yaw')
    for key in ('world_velocity_target_mps','measured_world_velocity_mps','applied_world_force_n'):finite_vector(drive.get(key),key)
    if not isinstance(drive.get('reason'),str):raise ValueError('Missing drive status reason')
    if (modes.get('effective') or {}).get('chassis_follow')!=drive['mode']:raise ValueError('Drive-frame feedback mismatch')
    stamp=drive.get('measurement_sim_time')
    if type(stamp) not in (int,float):raise ValueError('Missing drive measurement time')
    try:valid=math.isfinite(stamp) and 0<=stamp<=data['sim_time']
    except (ValueError,OverflowError,KeyError):valid=False
    if not valid:raise ValueError('Invalid drive measurement time')
    revision=drive.get('input_revision')
    if type(revision) is not int or revision<0:raise ValueError('Invalid drive input revision')
    if drive['active']:
        current=(data.get('control') or {}).get('safety_revision')
        if type(current) is not int or current!=revision:raise ValueError('Old drive input revision')
    # Force/velocity are sampled physical diagnostics, not a promise that a
    # just-revoked lease has already changed the next physics step.
    if not drive['active'] and (drive['mode']!='free' or drive['world_velocity_target_mps']!=[0.,0.]):raise ValueError('Inactive drive cannot report a target')
