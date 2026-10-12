"""Synchronous, no-retry simulation client. No hardware or persistent credentials.

This does not replace the Qt v1 transport. It is the small v2 boundary for local
multiclient qualification. An uncertain command is never replayed automatically.
"""
from __future__ import annotations
import json
import math
import threading
import urllib.error
import urllib.parse
import urllib.request

SCHEMA='rm.sim.platform.v2'
MAX_RESPONSE_BYTES=1024*1024
class ClientError(RuntimeError):pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ClientError('Redirect forbidden')

class PlatformClient:
    def __init__(self,url,*,timeout=.25):
        u=urllib.parse.urlsplit(url)
        if (u.scheme!='http' or u.hostname!='127.0.0.1' or u.username or u.password or u.path not in ('','/') or u.query or u.fragment or u.port is None or not 1024<=u.port<=65535):raise ValueError('Explicit nonprivileged loopback HTTP endpoint required')
        if not 0<timeout<=2:raise ValueError('Bounded timeout required')
        self.url=f'http://127.0.0.1:{u.port}';self.timeout=timeout
        self._opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self._token=None;self.session_id=None;self.role=None;self._leases={};self._seq={};self._lock=threading.RLock();self.uncertain=False
    def _request(self,path,body=None,*,authenticated=True,max_bytes=MAX_RESPONSE_BYTES):
        headers={}
        if authenticated:
            if self._token is None:raise ClientError('Session not open')
            headers['Authorization']='Bearer '+self._token
        data=None
        if body is not None:headers['Content-Type']='application/json';data=json.dumps(body,allow_nan=False,separators=(',',':')).encode()
        request=urllib.request.Request(self.url+path,data=data,headers=headers,method='GET' if body is None else 'POST')
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
            raise ClientError(str(reason)) from None
        except (OSError,ValueError,RecursionError,ClientError) as e:
            self.uncertain=True
            raise ClientError('Uncertain request; explicit release/reconnect required') from e
        if type(result) is not dict or result.get('schema')!=SCHEMA:
            self.uncertain=True
            raise ClientError('Unknown response contract; explicit reconnect required')
        return result
    def _invalid(self,message):
        self.uncertain=True
        raise ClientError(message+'; explicit reconnect required')
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
            result=self._request('/v2/sessions',{'role':role},authenticated=False)
            if (type(result.get('token')) is not str or not 1<=len(result['token'])<=256 or type(result.get('session_id')) is not str or not 1<=len(result['session_id'])<=128 or result.get('role')!=role or not self._number(result.get('session_expires')) or not self._number(result.get('server_monotonic_s')) or result['session_expires']<=result['server_monotonic_s']):self._invalid('Invalid session response')
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
                for ch in channels:self._leases.pop((rid,ch),None)
    def control(self,rid,channel,value,valid_for_s=.15):
        with self._lock:
            if self.uncertain:raise ClientError('Uncertain session; reconnect before control')
            epoch=self._epochs(rid,[channel])[channel];key=rid,channel,epoch
            seq=self._seq[key]+1;self._seq[key]=seq
            result=self._request('/v2/control',dict(robot_id=rid,channel=channel,lease_epoch=epoch,seq=seq,value=value,valid_for_s=valid_for_s))
            expected=dict(accepted=True,robot_id=rid,channel=channel,lease_epoch=epoch,seq=seq)
            if any(type(result.get(k)) is not type(v) or result.get(k)!=v for k,v in expected.items()):self._invalid('Invalid control acknowledgement')
            return result
    def bind(self,rid,kind,adapter_id,config=None):
        with self._lock:
            if self.uncertain:raise ClientError('Uncertain session; reconnect before binding')
            channel={'navigation':'motion','aim':'gimbal'}[kind]
            result=self._request('/v2/bind',dict(robot_id=rid,kind=kind,lease_epochs=self._epochs(rid,[channel]),adapter_id=adapter_id,config=config or {}))
            if any(result.get(k)!=v for k,v in dict(robot_id=rid,kind=kind,adapter_id=adapter_id).items()) or not self._epoch(result.get('binding_epoch')):self._invalid('Invalid bind response')
            return result
    def unbind(self,rid,kind):
        with self._lock:
            channel={'navigation':'motion','aim':'gimbal'}[kind]
            result=self._request('/v2/unbind',dict(robot_id=rid,kind=kind,lease_epochs=self._epochs(rid,[channel])))
            if result.get('unbound') is not True or not self._epoch(result.get('binding_epoch')):self._invalid('Invalid unbind response')
            return result
    def close(self):
        with self._lock:
            try:
                if self._token is not None:
                    result=self._request('/v2/close',{})
                    if result.get('closed') is not True:self._invalid('Invalid close response')
            finally:self._token=None;self.session_id=None;self.role=None;self._leases.clear();self._seq.clear()
    def __repr__(self):return f'PlatformClient({self.url!r}, role={self.role!r})'
