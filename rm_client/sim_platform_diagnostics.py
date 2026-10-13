"""Bounded, credential-free local simulator diagnostics; never a control source."""
from __future__ import annotations
from collections import deque
import copy
import math
import re
import threading
import time

# Only internally selected fields are retained. No URL, headers, body, token,
# arbitrary exception message or response payload is accepted.
FIELDS=frozenset(('request_id','path','method','thread','session_id','robot_id','channel','kind',
 'lease_epoch','lease_epochs','binding_epoch','seq','window_id','connection_generation',
 'control_generation','watch_generation','plan_generation','input_revision','watch_epoch',
 'episode','sample_sim_time','sample_received_wall','sample_age_wall_s','input_age_wall_s',
 'heartbeat_age_wall_s','window_deadline_local','window_remaining_local_s','focus',
 'connected','uncertain','raw_state_available','telemetry_available','stage','reason_code',
 'exception_type','duration_wall_s','control_profile','thread_alive_count'))
PATHS=frozenset(('/v2/sessions','/v2/claim','/v2/renew','/v2/release','/v2/control_window',
 '/v2/control','/v2/adapter_input','/v2/bind','/v2/unbind','/v2/state','/v2/frame',
 '/v2/watch','/v2/robots','/v2/adapters','/v2/keepalive','/v2/close'))
REASONS=frozenset(('unknown_robot','invalid_session','runtime_stopped','read_only_session',
 'lease_mismatch','lease_occupied','stale_seq','stale_adapter_seq','binding_mismatch',
 'channel_bound_to_adapter','stale_control_window','expired_control_window',
 'control_window_lease_mismatch','adapter_activation_expired','adapter_window_profile_required',
 'invalid_control_profile','invalid_motion_frame','motion_limit','gimbal_limit',
 'invalid_fire','invalid_channels','invalid_lease_epochs','invalid_binding',
 'binding_requires_channels','control_window_profile_required','control_window_sequence_exhausted',
 'session_capacity','invalid_kind','request_rejected','request_capacity','unknown_endpoint'))

def reason_code(value):return value if type(value) is str and value in REASONS else 'unclassified_error'

INTEGER_FIELDS=frozenset(('request_id','lease_epoch','binding_epoch','seq','window_id','connection_generation',
 'control_generation','watch_generation','plan_generation','input_revision','watch_epoch','episode','thread_alive_count'))
BOOL_FIELDS=frozenset(('focus','connected','uncertain','raw_state_available','telemetry_available'))
STRING_FIELDS=frozenset(('path','method','thread','session_id','robot_id','channel','kind','stage','reason_code','exception_type','control_profile'))
INTERNAL_REASONS=frozenset(('unclassified_error','session_not_open','encode_error','transport_uncertain',
 'unknown_response_contract','invalid_acknowledgement','disconnected','focus_lost','heartbeat_or_focus_lost',
 'window_ack_late','adapter_sample_or_window_expired','manual_sample_or_window_expired','input_sample_stale',
 'state_camera_heartbeat_stale','plan_changed','catalog_changed','watch_changed','transport_failure','camera_failure',
 'binding_missing','episode_changed','explicit_release','window_expired','activation_expired',
 'budget_pressure','state_camera_or_input_expired'))

def clean(key,value):
 if value is None:return None
 if key=='lease_epochs':
  if type(value) is not dict or len(value)>3 or any(k not in ('motion','gimbal','fire') or type(v) is not int or not 0<=v<=2**53-1 for k,v in value.items()):return None
  return dict(value)
 if key in INTEGER_FIELDS:return value if type(value) is int and 0<=value<=2**53-1 else None
 if key in BOOL_FIELDS:return value if type(value) is bool else None
 if key in STRING_FIELDS:
  if type(value) is not str:return None
  if key in ('session_id','robot_id') and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',value) is None:return None
  if key=='thread' and re.fullmatch(r'[A-Za-z0-9_ .()\-]{1,128}',value) is None:return None
  if key=='channel' and value not in ('motion','gimbal','fire'):return None
  if key=='kind' and value not in ('navigation','aim'):return None
  if key=='method' and value not in ('GET','POST'):return None
  if key=='control_profile' and value not in ('legacy_v2','server_window_v1'):return None
  if key=='reason_code' and value not in REASONS|INTERNAL_REASONS:return 'unclassified_error'
  return value[:128]
 if type(value) not in (int,float):return None
 try:return float(value) if math.isfinite(float(value)) else None
 except (OverflowError,ValueError):return None

class DiagnosticLog:
 def __init__(self,*,clock=time.monotonic,capacity=256):
  if type(capacity) is not int or not 8<=capacity<=1024:raise ValueError('Bounded diagnostic capacity required')
  self.clock=clock;self.clock_failures=0;self._events=deque(maxlen=capacity);self._lock=threading.Lock();self._seq=0;self._first_failure=None
 def record(self,event,*,failure=False,**fields):
  with self._lock:
   self._seq+=1
   try:stamp=clean('wall_monotonic',self.clock())
   except BaseException:stamp=None;self.clock_failures+=1
   result=dict(event=event[:64] if type(event) is str else 'invalid_event',event_seq=self._seq,wall_monotonic=stamp)
   for key,value in fields.items():
    if key not in FIELDS:continue
    if key=='path' and value not in PATHS:value='unrecognized_path'
    result[key]=clean(key,value)
   self._events.append(result)
   if failure and self._first_failure is None:self._first_failure=copy.deepcopy(result)
   return copy.deepcopy(result)
 def elapsed_since(self,stamp):
  try:
   now=clean('wall_monotonic',self.clock())
   return now-stamp if now is not None and stamp is not None else None
  except BaseException:return None
 def snapshot(self):
  with self._lock:return dict(events=copy.deepcopy(list(self._events)),first_failure=copy.deepcopy(self._first_failure),total_events=self._seq,capacity=self._events.maxlen,clock_domain='local_monotonic_only',clock_failures=self.clock_failures)


def cancellation_code(reason):
 # Match fixed local prefixes; never persist arbitrary caller exception text.
 prefixes=(('V2 disconnected','disconnected'),('Window focus lost','focus_lost'),
 ('Input heartbeat/focus lost','heartbeat_or_focus_lost'),('Input window arrived too late','window_ack_late'),
 ('Adapter input sample/window expired','adapter_sample_or_window_expired'),
 ('Input sample/window expired','manual_sample_or_window_expired'),('No fresh input','input_sample_stale'),
 ('V2 state/camera/heartbeat expired','state_camera_heartbeat_stale'),('Adapter plan changed','plan_changed'),
 ('Adapter catalog changed','catalog_changed'),('Watching ','watch_changed'),
 ('V2 connection error','transport_failure'),('Camera error','camera_failure'),
 ('Binding missing','binding_missing'),('New episode','episode_changed'))
 return next((code for prefix,code in prefixes if type(reason) is str and reason.startswith(prefix)),'explicit_release')
