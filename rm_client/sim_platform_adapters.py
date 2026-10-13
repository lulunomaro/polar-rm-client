"""Typed SIM adapter catalog/plan. Display truth never becomes algorithm input."""
from __future__ import annotations
import copy
import hashlib
import json
import math

KINDS={'navigation':'motion','aim':'gimbal'}
MANUAL='Manual'
SETTINGS=('V2 Navigation','V2 Aim','V2 Goal X','V2 Goal Y','V2 Aim FOV','V2 Aim Radius X','V2 Aim Radius Y')
KNOWN={'nav_rule_v1':('navigation','goal_world_2d_v1'),
       'aim_pixel_baseline_v1':('aim','empty_v1'),
       'aim_native_rmcs_v1':('aim','native_fov_v1')}

def number(v,lo,hi):
    if type(v) not in (int,float):raise ValueError('Expected finite scalar')
    try:v=float(v)
    except (OverflowError,ValueError):raise ValueError('Expected finite scalar') from None
    if not math.isfinite(v) or not lo<=v<=hi:raise ValueError('Scalar outside declared range')
    return v

def parse_catalog(raw):
    empty=dict(ready=False,reason='Adapter UI contract unavailable',adapters={},robots={},signature='unavailable')
    if type(raw) is not dict or raw.get('schema')!='rm.sim.platform.v2':return empty
    if any(type(raw.get(k)) is not int or raw[k]!=1 for k in ('adapter_ui_version','adapter_activation_version')):return empty
    if raw.get('fire_authority')!='manual_only' or raw.get('readiness')!='declared_support_only':return empty
    descriptors=raw.get('adapters');robots=raw.get('robots')
    if type(descriptors) is not list or len(descriptors)>32 or type(robots) is not list or not 1<=len(robots)<=64:raise ValueError('Invalid catalog bounds')
    parsed={};support={}
    for r in robots:
        if type(r) is not dict or type(r.get('robot_id')) is not str or not 1<=len(r['robot_id'])<=128 or r['robot_id'] in support:raise ValueError('Invalid catalog robot')
        sensors=r.get('declared_sensors')
        if type(sensors) is not list or len(sensors)>16 or any(type(v) is not str for v in sensors) or len(set(sensors))!=len(sensors):raise ValueError('Invalid sensor declaration')
        support[r['robot_id']]=tuple(sensors)
    for d in descriptors:
        if type(d) is not dict or type(d.get('adapter_id')) is not str or not 1<=len(d['adapter_id'])<=128 or d['adapter_id'] in parsed:raise ValueError('Invalid adapter identity')
        aid=d['adapter_id'];reason='';kind=d.get('kind')
        if aid not in KNOWN:reason='Test fixture / unsupported UI contract'
        elif d.get('version')!='1.0.0' or (kind,d.get('ui_config_contract'))!=KNOWN[aid]:reason='Unsupported adapter version/config contract'
        elif type(d.get('available')) is not bool or d.get('available') is not True:reason=str(d.get('reason','Registration unavailable'))
        elif type(d.get('test_only',False)) is not bool or d.get('test_only',False):reason='Test fixture, no operational binding'
        elif d.get('fire_authority')!='manual_only':reason='Unsupported fire authority'
        elif d.get('output_modes')!=(['velocity_body'] if kind=='navigation' else ['gimbal_target']):reason='Unsupported output contract'
        sensors=d.get('required_sensors')
        if type(sensors) is not list or len(sensors)>16 or any(type(v) is not str for v in sensors):reason='Invalid required sensors';sensors=[]
        supported=d.get('supported_robots')
        if supported is not None and (type(supported) is not list or not supported or any(type(v) is not str for v in supported)):reason='Invalid supported robots';supported=[]
        parsed[aid]=dict(adapter_id=aid,kind=kind,reason=reason,selectable=not reason,required_sensors=tuple(sensors),supported_robots=supported,
                         label=str(d.get('label',aid))[:200],config_contract=d.get('ui_config_contract'))
    encoded=json.dumps(raw,sort_keys=True,allow_nan=False,separators=(',',':'))
    return dict(ready=True,reason='',adapters=parsed,robots=support,signature=hashlib.sha256(encoded.encode()).hexdigest())

def candidate(catalog,rid,kind,aid):
    d=catalog.get('adapters',{}).get(aid)
    if not catalog.get('ready') or d is None:return False,'No negotiated adapter'
    if d['kind']!=kind:return False,'Wrong algorithm channel'
    if not d['selectable']:return False,d['reason']
    if rid not in catalog['robots']:return False,'Robot sensor support unknown'
    if d['supported_robots'] is not None and rid not in d['supported_robots']:return False,'Adapter unsupported on this robot'
    missing=set(d['required_sensors'])-set(catalog['robots'][rid])
    if missing:return False,'Sensor provider unavailable: '+','.join(sorted(missing))
    return True,'Registered; sensor/output readiness checked after binding'

def options(catalog,rid,kind):
    return [MANUAL]+[aid for aid in catalog.get('adapters',{}) if candidate(catalog,rid,kind,aid)[0]]

def config_for(aid,values):
    if aid=='nav_rule_v1':return {'goal_world_m':[number(values.get('V2 Goal X',0),-100,100),number(values.get('V2 Goal Y',0),-100,100)]}
    if aid=='aim_pixel_baseline_v1':return {}
    if aid=='aim_native_rmcs_v1':return dict(fov_degrees=number(values.get('V2 Aim FOV',85),10,180),circle_radius_x_norm=number(values.get('V2 Aim Radius X',.25),.001,2),circle_radius_y_norm=number(values.get('V2 Aim Radius Y',.25),.001,2))
    raise ValueError('Unrecognized operational adapter')

def make_plan(catalog,rid,choices,values):
    result={}
    for kind in KINDS:
        aid=choices.get(kind,MANUAL)
        if aid==MANUAL:continue
        ok,reason=candidate(catalog,rid,kind,aid)
        if not ok:raise ValueError(reason)
        result[kind]=dict(adapter_id=aid,config=config_for(aid,values))
    return result

def _feedback(state,rid,kind,expected,session_id):
    """Only identify a historic applied sample when the current binding matches."""
    if not expected:return 'manual'
    if type(state) is not dict:return 'waiting for state'
    b=(state.get('control') or {}).get('bindings',{}).get(rid,{}).get(kind,{})
    if b.get('owner')!=session_id or type(b.get('epoch')) is not int or b['epoch']!=expected['binding_epoch']:return 'revoked / pending'
    receipt=(state.get('manual_consumption_receipts') or {}).get(rid)
    if type(receipt) is not dict or receipt.get('robot_id')!=rid:return 'waiting for sensor/result'
    old=(receipt.get('adapter_authorizations') or {}).get(kind,{})
    if old.get('owner')!=session_id or type(old.get('epoch')) is not int or old['epoch']!=b['epoch']:return 'waiting for current binding sample'
    # Receipt was sampled in this display snapshot's episode by the authority.
    if type(receipt.get('episode')) is not int or receipt['episode']!=state.get('episode'):return 'waiting for current episode sample'
    target=receipt.get('target',{}).get(KINDS[kind])
    if target is None:return 'not applied / '+str((state.get('algorithm_status') or {}).get(rid,{}).get(kind,'waiting for sensor/result'))
    try:
        stamp=number(receipt.get('consume_sim_time'),0,1e12)
        if stamp>number(state.get('sim_time'),0,1e12):return 'not applied / invalid future sample'
    except ValueError:return 'not applied / invalid sample time'
    return 'last applied @ %.3fs (sample)'%stamp

def feedback(state,rid,kind,expected,session_id):
    try:return _feedback(state,rid,kind,expected,session_id)
    except (ValueError,TypeError,KeyError,AttributeError):return 'not applied / invalid feedback'
