"""Synchronous, no-retry simulation client. No hardware or persistent credentials.

This does not replace the Qt v1 transport. It is the small v2 boundary for local
multiclient qualification. An uncertain command is never replayed automatically.
"""
from __future__ import annotations
import json
import math
import threading
import time
from .sim_platform_diagnostics import DiagnosticLog,reason_code
import urllib.error
import urllib.parse
import urllib.request

SCHEMA='rm.sim.platform.v2'
MAX_RESPONSE_BYTES=1024*1024
class ClientError(RuntimeError):
    def __init__(self,message="",*,request_id=None,code=None):
        super().__init__(message);self.request_id=request_id;self.code=code
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ClientError('Redirect forbidden')

class PlatformClient:
    def __init__(self,url,*,timeout=.25,control_profile='legacy_v2',diagnostic_clock=time.monotonic):
        u=urllib.parse.urlsplit(url)
        if (u.scheme!='http' or u.hostname!='127.0.0.1' or u.username or u.password or u.path not in ('','/') or u.query or u.fragment or u.port is None or not 1024<=u.port<=65535):raise ValueError('Explicit nonprivileged loopback HTTP endpoint required')
        if not 0<timeout<=2:raise ValueError('Bounded timeout required')
        if type(control_profile) is not str or control_profile not in ('legacy_v2','server_window_v1'):raise ValueError('Unknown control profile')
        self._diagnostic_failures=0;self._diagnostics=DiagnosticLog(clock=diagnostic_clock);self._diagnostic_local=threading.local()
        self.control_profile=control_profile;self._windows={};self._last_window_ids={};self._bindings={};self._adapter_seq={}
        self.url=f'http://127.0.0.1:{u.port}';self.timeout=timeout
        self._opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self._token=None;self.session_id=None;self.role=None;self._leases={};self._seq={};self._lock=threading.RLock();self.uncertain=False
    def diagnostics(self):
        result=self._diagnostics.snapshot();result['dropped_events']=self._diagnostic_failures;return result
    def _diagnose(self,*args,**kwargs):
        try:return self._diagnostics.record(*args,**kwargs)
        except BaseException:
            self._diagnostic_failures+=1
            return {'event_seq':None,'wall_monotonic':None}
    def _request(self,path,body=None,*,authenticated=True,max_bytes=MAX_RESPONSE_BYTES):
        fields=dict(path=path,method='GET' if body is None else 'POST',session_id=self.session_id,thread=threading.current_thread().name,control_profile=self.control_profile)
        if type(body) is dict:
            fields.update({key:body[key] for key in ('robot_id','channel','kind','lease_epoch','lease_epochs','binding_epoch','seq','window_id') if key in body})
        started=self._diagnose('request_started',stage='before_encode',**fields);rid=started['event_seq']
        self._diagnostic_local.request_id=rid
        def done(event,stage,*,failure=False,reason=None,exc=None):
            return self._diagnose(event,failure=failure,request_id=rid,stage=stage,
                reason_code=reason,exception_type=type(exc).__name__ if exc else None,
                duration_wall_s=self._diagnostics.elapsed_since(started['wall_monotonic']),**fields)
        headers={}
        if authenticated:
            if self._token is None:
                done('request_failed','before_send',failure=True,reason='session_not_open')
                raise ClientError('Session not open',request_id=rid,code='session_not_open')
            headers['Authorization']='Bearer '+self._token
        data=None
        try:
            if body is not None:headers['Content-Type']='application/json';data=json.dumps(body,allow_nan=False,separators=(',',':')).encode()
            request=urllib.request.Request(self.url+path,data=data,headers=headers,method='GET' if body is None else 'POST')
        except (TypeError,ValueError,OverflowError) as exc:
            done('request_failed','before_send',failure=True,reason='encode_error',exc=exc);raise
        try:
            with self._opener.open(request,timeout=self.timeout) as response:
                raw=response.read(max_bytes+1)
                if len(raw)>max_bytes:raise ClientError('Response too large')
                def reject_constant(_):raise ValueError("Nonfinite JSON")
                result=json.loads(raw,parse_constant=reject_constant)
        except urllib.error.HTTPError as e:
            raw=e.read(4097)
            try:reason=json.loads(raw).get('error','request_rejected') if len(raw)<=4096 else 'request_rejected'
            except (ValueError,TypeError,AttributeError):reason='request_rejected'
            safe=reason_code(reason);done('request_rejected','http_response',failure=True,reason=safe,exc=e)
            raise ClientError(str(reason),request_id=rid,code=safe) from None
        except (OSError,ValueError,RecursionError,ClientError) as e:
            self.uncertain=True;done('request_failed','response_unverifiable',failure=True,reason='transport_uncertain',exc=e)
            raise ClientError('Uncertain request; explicit release/reconnect required',request_id=rid,code='transport_uncertain') from e
        if type(result) is not dict or result.get('schema')!=SCHEMA:
            self.uncertain=True;done('request_failed','response_contract',failure=True,reason='unknown_response_contract')
            raise ClientError('Unknown response contract; explicit reconnect required',request_id=rid,code='unknown_response_contract')
        done('response_received','schema_validated')
        return result
    def _invalid(self,message):
        self.uncertain=True;rid=getattr(self._diagnostic_local,'request_id',None)
        self._diagnose('acknowledgement_invalid',failure=True,request_id=rid,session_id=self.session_id,
            thread=threading.current_thread().name,stage='operation_ack_validation',reason_code='invalid_acknowledgement')
        raise ClientError(message+'; explicit reconnect required',request_id=rid,code='invalid_acknowledgement')
    @staticmethod
    def _number(value):
        if type(value) not in (int,float):return False
        try:return math.isfinite(float(value)) and 0<=value<=1e12
        except (OverflowError,ValueError):return False
    @staticmethod
    def _epoch(value):return type(value) is int and 0<=value<=2**53-1
    def _lease_reply(self,result,rid,channels,expected=None):
        epochs=result.get('lease_epochs');expires=result.get('lease_expires')
        if (result.get('robot_id')!=rid or type(epochs) is not dict or set(epochs)!=set(channels)
            or any(not self._epoch(v) for v in epochs.values()) or (expected is not None and epochs!=expected)
            or type(expires) is not dict or set(expires)!=set(channels) or any(not self._number(v) for v in expires.values())):self._invalid('Invalid lease response')
        return epochs
    def open(self,role='spectator'):
        with self._lock:
            if self._token is not None:raise ClientError('Session already open')
            if role not in ('operator','spectator'):raise ValueError('Unknown session role')
            body={'role':role}
            if self.control_profile!='legacy_v2':body['control_profile']=self.control_profile
            result=self._request('/v2/sessions',body,authenticated=False)
            if (type(result.get('token')) is not str or not 1<=len(result['token'])<=256 or type(result.get('session_id')) is not str or not 1<=len(result['session_id'])<=128 or result.get('role')!=role or not self._number(result.get('session_expires')) or not self._number(result.get('server_monotonic_s')) or result['session_expires']<=result['server_monotonic_s']):self._invalid('Invalid session response')
            if self.control_profile=='server_window_v1':
                if result.get('control_profile')!=self.control_profile or type(result.get('control_window_version')) is not int or result['control_window_version']!=1 or not self._number(result.get('control_window_ttl_s')) or not 0<result['control_window_ttl_s']<=.15:self._invalid('Required server input window profile unavailable')
            self._token=result['token'];self.session_id=result['session_id'];self.role=role;self.uncertain=False
            # Bearer stays memory-only and is deliberately absent from return data.
            return {k:v for k,v in result.items() if k!='token'}
    def watch(self,robot_id):
        result=self._request('/v2/watch',{'robot_id':robot_id})
        if result.get('robot_id')!=robot_id or not self._epoch(result.get('watch_epoch')):self._invalid('Invalid watch acknowledgement')
        return result
    def frame(self):return self._request('/v2/frame',max_bytes=3*1024*1024)
    def state(self):return self._request('/v2/state')
    def robots(self):return self._request('/v2/robots')
    def adapters(self):return self._request('/v2/adapters')
    def keepalive(self):
        result=self._request('/v2/keepalive',{})
        if result.get('session_id')!=self.session_id or not self._number(result.get('session_expires')):self._invalid('Invalid keepalive response')
        return result
    def claim(self,robot_id,channels):
        with self._lock:
            if self.role!='operator':raise ClientError('Spectator is read only')
            if self.uncertain:raise ClientError('Uncertain session; reconnect before claim')
            result=self._request('/v2/claim',dict(robot_id=robot_id,channels=list(channels)))
            epochs=self._lease_reply(result,robot_id,channels)
            for ch,ep in epochs.items():self._leases[robot_id,ch]=ep;self._seq[robot_id,ch,ep]=-1
            return result
    def _epochs(self,rid,channels):
        try:return {ch:self._leases[rid,ch] for ch in channels}
        except KeyError:raise ClientError('Channel not claimed') from None
    def renew(self,rid,channels):
        with self._lock:
            if self.uncertain:raise ClientError('Uncertain session; reconnect before renewal')
            expected=self._epochs(rid,channels);result=self._request('/v2/renew',dict(robot_id=rid,lease_epochs=expected))
            self._lease_reply(result,rid,channels,expected);return result
    def release(self,rid,channels):
        with self._lock:
            epochs=self._epochs(rid,channels)
            try:
                result=self._request('/v2/release',dict(robot_id=rid,lease_epochs=epochs))
                if result.get('released') is not True:self._invalid('Invalid release response')
                return result
            finally:
                self._windows.pop(rid,None)
                for ch in channels:self._leases.pop((rid,ch),None)
                for kind,ch in [('navigation','motion'),('aim','gimbal')]:
                    if ch in channels:self._forget_binding(rid,kind)
    def control_window(self,rid,channels):
        with self._lock:
            if self.uncertain:raise ClientError('Uncertain session; reconnect before input window')
            if self.control_profile!='server_window_v1' or self.role!='operator':raise ClientError('Operator window profile required')
            expected=self._epochs(rid,channels)
            result=self._request('/v2/control_window',dict(robot_id=rid,lease_epochs=expected))
            wid=result.get('window_id');issued=result.get('issued_wall');expires=result.get('expires_wall');ttl=result.get('valid_for_s')
            if (result.get('robot_id')!=rid or result.get('session_id')!=self.session_id or result.get('lease_epochs')!=expected
                or type(result.get('lease_epochs')) is not dict or any(not self._epoch(v) for v in result['lease_epochs'].values())
                or not self._epoch(wid) or wid<=self._last_window_ids.get(rid,-1)
                or not self._number(issued) or not self._number(expires) or not self._number(ttl)
                or not 0<ttl<=.15 or not issued<expires or abs((expires-issued)-ttl)>1e-6):self._invalid('Invalid input window acknowledgement')
            self._windows[rid]=dict(result);self._last_window_ids[rid]=wid
            return result
    def control(self,rid,channel,value,valid_for_s=.15,*,window_id=None):
        with self._lock:
            if self.uncertain:raise ClientError('Uncertain session; reconnect before control')
            epoch=self._epochs(rid,[channel])[channel];key=rid,channel,epoch
            seq=self._seq[key]+1;self._seq[key]=seq
            body=dict(robot_id=rid,channel=channel,lease_epoch=epoch,seq=seq,value=value,valid_for_s=valid_for_s)
            window=self._windows.get(rid)
            if self.control_profile=='server_window_v1':
                if not self._epoch(window_id) or window is None or window_id!=window['window_id'] or window['lease_epochs'].get(channel)!=epoch:raise ClientError('Current input window required')
                body['window_id']=window_id
            elif window_id is not None:raise ClientError('Unexpected input window')
            result=self._request('/v2/control',body)
            expected=dict(accepted=True,robot_id=rid,channel=channel,lease_epoch=epoch,seq=seq)
            if any(type(result.get(k)) is not type(v) or result.get(k)!=v for k,v in expected.items()):self._invalid('Invalid control acknowledgement')
            if self.control_profile=='server_window_v1':
                if type(result.get('window_id')) is not int or result['window_id']!=window_id or not self._number(result.get('input_expires')) or not window['issued_wall']<result['input_expires']<=window['expires_wall']:self._invalid('Invalid window control acknowledgement')
            return result
    def _forget_binding(self,rid,kind):
        self._bindings.pop((rid,kind),None)
        for key in list(self._adapter_seq):
            if key[:2]==(rid,kind):self._adapter_seq.pop(key,None)
    def bind(self,rid,kind,adapter_id,config=None):
        with self._lock:
            if self.uncertain:raise ClientError('Uncertain session; reconnect before binding')
            channel={'navigation':'motion','aim':'gimbal'}[kind]
            self._windows.pop(rid,None)
            result=self._request('/v2/bind',dict(robot_id=rid,kind=kind,lease_epochs=self._epochs(rid,[channel]),adapter_id=adapter_id,config=config or {}))
            if any(result.get(k)!=v for k,v in dict(robot_id=rid,kind=kind,adapter_id=adapter_id).items()) or not self._epoch(result.get('binding_epoch')):self._invalid('Invalid bind response')
            if self.control_profile=='server_window_v1' and (result.get('activation_required') is not True or type(result.get('activation_version')) is not int or result['activation_version']!=1):self._invalid('Adapter deadman capability missing')
            self._forget_binding(rid,kind)
            self._bindings[rid,kind]=result['binding_epoch'];self._adapter_seq[rid,kind,result['binding_epoch']]=-1
            return result
    def unbind(self,rid,kind):
        with self._lock:
            channel={'navigation':'motion','aim':'gimbal'}[kind]
            self._windows.pop(rid,None)
            result=self._request('/v2/unbind',dict(robot_id=rid,kind=kind,lease_epochs=self._epochs(rid,[channel])))
            if result.get('unbound') is not True or not self._epoch(result.get('binding_epoch')):self._invalid('Invalid unbind response')
            self._forget_binding(rid,kind)
            return result
    def adapter_input(self,rid,kind,binding_epoch,*,window_id,valid_for_s=.15):
        with self._lock:
            if self.uncertain:raise ClientError('Uncertain session; reconnect before activation')
            if self.control_profile!='server_window_v1':raise ClientError('Adapter window profile required')
            channel={'navigation':'motion','aim':'gimbal'}[kind]
            if not self._epoch(binding_epoch) or self._bindings.get((rid,kind))!=binding_epoch:raise ClientError('Current binding required')
            epochs=self._epochs(rid,[channel]);window=self._windows.get(rid)
            if not self._epoch(window_id) or window is None or window_id!=window['window_id']:raise ClientError('Current input window required')
            key=rid,kind,binding_epoch;seq=self._adapter_seq[key]+1;self._adapter_seq[key]=seq
            result=self._request('/v2/adapter_input',dict(robot_id=rid,kind=kind,lease_epochs=epochs,
                binding_epoch=binding_epoch,seq=seq,window_id=window_id,valid_for_s=valid_for_s))
            expected=dict(accepted=True,robot_id=rid,kind=kind,binding_epoch=binding_epoch,seq=seq,window_id=window_id)
            if any(type(result.get(k)) is not type(v) or result.get(k)!=v for k,v in expected.items()):self._invalid('Invalid adapter activation acknowledgement')
            if not self._number(result.get('activation_expires')) or not window['issued_wall']<result['activation_expires']<=window['expires_wall']:self._invalid('Invalid adapter activation deadline')
            return result
    def close(self):
        with self._lock:
            try:
                if self._token is not None:
                    result=self._request('/v2/close',{})
                    if result.get('closed') is not True:self._invalid('Invalid close response')
            finally:self._token=None;self.session_id=None;self.role=None;self._leases.clear();self._seq.clear();self._windows.clear();self._last_window_ids.clear();self._bindings.clear();self._adapter_seq.clear()
    def __repr__(self):return f'PlatformClient({self.url!r}, role={self.role!r})'
