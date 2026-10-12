"""Explicit bounded renewal/close lifecycle; no retry or synthetic timestamps."""
from __future__ import annotations
import copy
import math
import threading
import time

class RenewalLifecycle:
    def __init__(self,client,robot_id,channels,*,interval=.15,clock=time.monotonic):
        if not .01<=interval<=1:raise ValueError('Bounded renewal interval required')
        self.client=client;self.robot_id=robot_id;self.channels=tuple(channels)
        self.interval=interval;self.clock=clock;self._lock=threading.Lock();self._close_lock=threading.Lock()
        self._stop=threading.Event();self.thread=None;self._events=[];self._context=None
        self.phase='created';self._request_id=0;self._closed_result=None
    def observe(self,state,*,received_wall=None):
        # These are explicitly last-known observation values, never a claim
        # about the simultaneous simulator state at a future HTTP error.
        received=self.clock() if received_wall is None else received_wall
        if type(state) is not dict or type(received) not in (int,float) or not math.isfinite(received):raise ValueError('Invalid observed context')
        control=state.get('control',{});leases=control.get('leases',{}).get(self.robot_id,{})
        def number(v):
            try:return v if type(v) in (int,float) and 0<=v<=1e12 and math.isfinite(v) else None
            except (ValueError,OverflowError):return None
        def integer(v):return v if type(v) is int and 0<=v<=2**53-1 else None
        def label(v):return v if type(v) is str and len(v)<=256 else None
        context=dict(observed_wall=received,sim_time=number(state.get('sim_time')),episode=integer(state.get('episode')),
            simulation_phase=label(state.get('phase')),offline_phase=label(state.get('clock_profile',{}).get('barrier_state')),
            server_sample_wall=number(control.get('server_monotonic_s')),
            lease_epochs={c:integer(leases.get(c,{}).get('epoch')) for c in self.channels},
            lease_expiries_wall={c:number(leases.get(c,{}).get('expires')) for c in self.channels})
        with self._lock:self._context=copy.deepcopy(context)
    def _emit(self,event,**extra):
        with self._lock:
            row=dict(event=event,wall_time=self.clock(),lifecycle_phase=self.phase,robot_id=self.robot_id,
                channels=list(self.channels),context_status='last_known' if self._context else 'unknown',
                last_known=copy.deepcopy(self._context),**extra)
            self._events.append(row)
        return row
    def events(self):
        with self._lock:return copy.deepcopy(self._events)
    def start(self):
        with self._close_lock:
            if self.thread is not None or self._stop.is_set() or self._closed_result is not None:raise RuntimeError('Renewal lifecycle cannot restart')
            self.phase='renewing';self._emit('renewal_started')
            self.thread=threading.Thread(target=self._run,name='v2-explicit-renewal',daemon=True);self.thread.start()
    def renew_once(self):
        if self._stop.is_set():self._emit('renew_suppressed_after_stop');return False
        self._request_id+=1;rid=self._request_id;self._emit('renew_request',request_id=rid)
        try:
            value=self.client.renew(self.robot_id,self.channels)
            self._emit('renew_success',request_id=rid,ack_lease_epochs=copy.deepcopy(value.get('lease_epochs')),ack_lease_expiries_wall=copy.deepcopy(value.get('lease_expires')))
            return True
        except BaseException as e:
            self._emit('renew_uncertain' if self.client.uncertain else 'renew_failure',request_id=rid,error_type=type(e).__name__,error=str(e),client_uncertain=bool(self.client.uncertain))
            self._stop.set();return False
    def _run(self):
        try:
            while not self._stop.wait(self.interval):
                if not self.renew_once():return
        finally:self._emit('renewal_worker_exited')
    def stop_and_join(self,timeout=1.):
        if self._closed_result is not None:return True
        if not 0<timeout<=3:raise ValueError('Bounded join required')
        self.phase='stopping';self._emit('renewal_stop_requested');self._stop.set()
        if self.thread is not None:self.thread.join(timeout)
        quiet=self.thread is None or not self.thread.is_alive()
        self._emit('renewal_joined' if quiet else 'renewal_join_timeout',quiesced=quiet)
        return quiet
    def close(self,*,join_timeout=1.):
        # No SDK lock is held while joining the HTTP renewal worker.
        with self._close_lock:
            if self._closed_result is not None:return copy.deepcopy(self._closed_result)
            if not self.stop_and_join(join_timeout):return dict(completed=False,quiesced=False,errors=['renewal_not_quiesced'])
            self.phase='closing';errors=[];revoked=False;session_close_ack=False
            try:
                state=self.client.state();self.observe(state)
                row=state['control']['leases'][self.robot_id]
                for c in self.channels:
                    if type(row[c]['epoch']) is not int or row[c]['epoch']<0 or (row[c]['owner'] is not None and type(row[c]['owner']) is not str):raise ValueError('Invalid cleanup lease state')
                revoked=all(row[c]['owner']!=self.client.session_id for c in self.channels)
            except BaseException as e:
                self._emit('close_state_unavailable',error_type=type(e).__name__,error=str(e))
                errors.append('state_unavailable')
            if revoked:self._emit('release_already_not_owned')
            else:
                self._emit('release_request')
                try:self.client.release(self.robot_id,self.channels);self._emit('release_success')
                except BaseException as e:
                    self._emit('release_failure',error_type=type(e).__name__,error=str(e));errors.append('release_failed')
            self._emit('session_close_request')
            try:self.client.close();session_close_ack=True;self._emit('session_close_success')
            except BaseException as e:
                self._emit('session_close_failure',error_type=type(e).__name__,error=str(e));errors.append('session_close_failed')
            prior=[e['event'] for e in self.events() if e['event'] in ('renew_uncertain','renew_failure')]
            self.phase='closed';self._closed_result=dict(completed=not errors,quiesced=True,session_close_ack=session_close_ack,errors=errors,prior_renew_errors=prior,network_lifecycle_error_free=not errors and not prior)
            self._emit('lifecycle_closed',result=copy.deepcopy(self._closed_result))
            return copy.deepcopy(self._closed_result)
