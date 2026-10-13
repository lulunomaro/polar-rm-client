"""V2 spin capability and historical-consumption truth; no device I/O."""
import copy,math,json,hashlib

def number(v,lo,hi):
 if type(v) not in (int,float):raise ValueError('Invalid spin number')
 try:v=float(v)
 except (ValueError,OverflowError):raise ValueError('Invalid spin number') from None
 if not math.isfinite(v) or not lo<=v<=hi:raise ValueError('Invalid spin number')
 return v

def request(value):
 if type(value) is not dict or set(value)!={'direction','speed_dps'}:raise ValueError('Invalid spin request')
 if type(value['direction']) is not str or value['direction'] not in ('auto','cw','ccw'):raise ValueError('Invalid spin direction')
 return dict(direction=value['direction'],speed_dps=number(value['speed_dps'],120,720))

def capability(raw,session_id,rid,watch_epoch,episode):
 if raw is None:return None
 if type(session_id) is not str or type(rid) is not str or type(watch_epoch) is not int or type(episode) is not int:return None
 try:
  if type(raw) is not dict or raw.get('capability_schema')!='rm.sim.platform.capabilities.v1':return None
  if (raw.get('session_id')!=session_id or raw.get('watch_robot_id')!=rid or type(raw.get('watch_epoch')) is not int or raw['watch_epoch']!=watch_epoch or type(raw.get('episode')) is not int or raw['episode']!=episode):return None
  robots=raw['robots']
  if type(robots) is not list or len(robots)>64 or any(type(r) is not dict or type(r.get('robot_id')) is not str for r in robots):return None
  if len({r['robot_id'] for r in robots})!=len(robots):return None
  c=next(r['spin_control'] for r in robots if r['robot_id']==rid)
  if type(c) is not dict or type(c.get('available')) is not bool or type(c.get('version')) is not int or c['version']!=1:return None
  if not c['available']:return None
  if c.get('simulation_only') is not True or c.get('power_guard') is not False or c.get('world_stabilized_gimbal') is not False or c.get('directions')!=['auto','cw','ccw'] or c.get('resolved_auto_direction')!='ccw':return None
  if number(c['speed_min_dps'],120,120)!=120 or number(c['speed_max_dps'],720,720)!=720:return None
  limits=c['yaw_torque_ctrlrange']
  if type(limits) is not list or len(limits)!=2 or not number(limits[0],-1e9,0)<0<number(limits[1],0,1e9):return None
  return dict(session_id=session_id,robot_id=rid,watch_epoch=watch_epoch,episode=episode,cap=copy.deepcopy(c))
 except (ValueError,KeyError,StopIteration,TypeError):return None

class PlatformModes:
 support=(False,False);drive_support=False
 def __init__(self,runtime):self.runtime=runtime;self.window=None;self.signature=None;self.cap_identity=None;self.enabled=False
 def sync(self,window):
  try:self._sync(window);return True
  except (ValueError,TypeError,KeyError,AttributeError):
   self.enabled=False;self.signature=None
   self.runtime.disarm('Invalid spin configuration; correct it before F2')
   window.control.reset_inputs();window.registry.get('SpinMode').enabled=False
   return False
 def _sync(self,window):
  self.window=window;t=self.runtime.transport;policy=self.runtime.feature_policy;module=window.registry.get('SpinMode')
  cap=t.spin_capability();supported=bool(cap and t.role=='operator' and 'navigation' not in t.plan)
  identity=None if cap is None else (cap['session_id'],cap['robot_id'],cap['watch_epoch'],cap['episode'],hashlib.sha256(json.dumps(cap['cap'],sort_keys=True,allow_nan=False).encode()).hexdigest())
  if supported!=self.support[0] or identity!=self.cap_identity:
   self.support=(supported,False);self.cap_identity=identity
   policy.set_supported('SpinMode',supported,{'Direction','Spin Speed'});module.enabled=False
   if supported:module.description='SIM spin request; measured speed/torque only from actual consumed feedback. Power guard unavailable.'
   self.runtime.disarm('Spin capability changed; select mode and press F2');window.control.reset_inputs();window.invalidate_gui_cache()
  parsed=request(dict(direction=str(module.get_setting('Direction').value).lower(),speed_dps=module.get_setting('Spin Speed').value));direction=parsed['direction'];speed=parsed['speed_dps']
  signature=(bool(supported and module.enabled),direction,speed)
  if self.signature is not None and signature!=self.signature:
   self.runtime.disarm('Spin request changed; release keys and press F2');window.control.reset_inputs()
  self.signature=signature;self.enabled=signature[0]
 def request(self):
  if not self.enabled or not self.support[0]:return None
  return request(dict(direction=self.signature[1],speed_dps=self.signature[2]))
 def submit(self,*_):return None
 def feedback(self):
  with self.runtime.transport._lock:return self._feedback_locked()
 def _feedback_locked(self):
  out=dict(spin=False,aim=False,drive=None,spin_reason='V2 spin unavailable',aim_reason='V2 right-button binding unavailable',drive_reason='body reference only',measured_yaw_rate=None,spin_origin=None)
  t=self.runtime.transport;raw=t.raw_state
  try:wanted=self.request()
  except (ValueError,TypeError,KeyError):return out
  if not self.support[0]:return out
  out['spin_reason']='requested, not applied' if wanted else 'disabled'
  try:
   if not (wanted and self.runtime.armed and t._focus and not t.client.uncertain and self.window and self.window.isActiveWindow() and self.window.combat_control_input_allowed() and t.spin_capability() and t._window and t.clock()<t._window_deadline):return out
   if type(t.owned_epochs.get('motion')) is not int or type(t._window.get('window_id')) is not int:return out
   rid=t.watch_robot;receipts=raw['spin_consumption_receipts'][rid];q=receipts['entry'];last=receipts['final'];l=raw['control']['leases'][rid]['motion'];m=q['target']['motion'];sample=raw['motion_feedback'][rid]
   if type(raw['episode']) is not int or type(self.cap_identity[3]) is not int or raw['episode']!=self.cap_identity[3] or type(q['episode']) is not int or q['episode']!=raw['episode'] or q['robot_id']!=rid or q['phase']!='integration_entry':return out
   lease=q['manual_inputs']['motion']
   if type(lease['epoch']) is not int or lease['epoch']!=t.owned_epochs.get('motion') or type(l['epoch']) is not int or l['epoch']!=lease['epoch'] or lease['owner']!=t.client.session_id or l['owner']!=t.client.session_id or l.get('mode_fault') or l['input'] is None:return out
   if type(lease['seq']) is not int or type(l['seq']) is not int or lease['seq']!=l['seq'] or type(lease['input_window_id']) is not int or lease['input_window_id']!=t._window['window_id']:return out
   if type(m) is not dict or m.get('mode')!='spin_v1' or request({k:m[k] for k in wanted})!=wanted:return out
   if type(l['input']) is not dict or set(l['input'])!=set(m) or l['input']!=m:return out
   for v in (m,l['input']):
    if set(v)!={'mode','frame','vx_mps','vy_mps','direction','speed_dps'} or v['frame']!='base_link':return out
    number(v['vx_mps'],-1,1);number(v['vy_mps'],-1,1);request({k:v[k]for k in wanted})
   if not number(q['consume_sim_time'],0,raw['sim_time'])<=number(sample['sample_sim_time'],0,raw['sim_time']):return out
   if sample['measurement_valid'] is not True or sample.get('spin_requested') is not True or last['target']['motion']!=m:return out
   # All comparisons here are between server-origin timestamps. Never map the
   # server monotonic epoch onto the client's local monotonic clock.
   server_now=number(raw['control']['server_monotonic_s'],0,1e15)
   consume=number(q['consume_wall'],0,server_now);expiry=number(lease['input_expires'],0,1e15)
   if not consume<expiry<=number(lease['input_window_deadline'],0,1e15) or not server_now<expiry or not server_now<number(l['expires'],0,1e15):return out
   if type(l['input_window_id']) is not int or type(l.get('mode_fault')) is not bool or number(l['input_window_deadline'],0,1e15)!=lease['input_window_deadline'] or number(l['input_expires'],0,1e15)!=expiry or l['input_window_id']!=lease['input_window_id']:return out
   if (last['robot_id']!=rid or type(last['episode']) is not int or last['episode']!=q['episode'] or last['phase'] not in ('integration_entry','fire') or number(last['consume_sim_time'],0,1e15)!=q['consume_sim_time']):return out
   final_lease=last['manual_inputs']['motion']
   for key in ('epoch','seq','input_window_id'):
    if type(final_lease[key]) is not int or final_lease[key]!=lease[key]:return out
   if final_lease['owner']!=lease['owner'] or number(final_lease['input_expires'],0,1e15)!=expiry or number(final_lease['input_window_deadline'],0,1e15)!=lease['input_window_deadline']:return out
   if not consume<=number(last['consume_wall'],0,server_now)<expiry:return out
   integration=number(sample['integration_sim_time'],0,raw['sim_time']);dt=number(sample['timestep_seconds'],1e-6,.1);post=number(sample['sample_sim_time'],0,raw['sim_time'])
   if abs(integration-number(q['consume_sim_time'],0,raw['sim_time']))>1e-9 or abs(post-integration-dt)>1e-9 or abs(post-raw['sim_time'])>1e-9:return out
   expected=(-1. if wanted['direction']=='cw' else 1.)*wanted['speed_dps']*math.pi/180.
   if abs(number(sample['target_yaw_rate_rps'],-100,100)-expected)>1e-9 or sample['direction_resolved']!=('cw' if expected<0 else 'ccw') or type(sample['saturated']) is not bool:return out
   torque=number(sample['applied_yaw_torque_nm'],-1e9,1e9);measured=number(sample['measured_yaw_rate_rps'],-1e6,1e6)
   out.update(applied_yaw_torque_nm=torque,saturated=sample['saturated'],target_yaw_rate_rps=expected,measured_yaw_rate=measured)
   out['spin']=True;out['spin_origin']='manual';out['spin_reason']='last applied spin; measured speed separate'
  except (KeyError,TypeError,ValueError,IndexError):pass
  return out
