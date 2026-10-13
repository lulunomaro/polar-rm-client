"""Opt-in v2 loopback sessions; watch is separate from explicit control leases.

No hardware imports. State projection is local UI data, never a referee wire packet.
The SDK and latest-only input slot are independent of Qt and camera rendering.
"""
from __future__ import annotations
import base64
import copy
import math
import threading
import time
from .sim_platform_client import PlatformClient,ClientError
from .sim_platform_adapters import parse_catalog,make_plan,feedback,KINDS
from .sim_platform_handoff import HandoffSchedule

CHANNELS=('motion','gimbal','fire')

def jpeg_dimensions(raw):
    """Read bounded JPEG SOF before asking a decoder to allocate pixel storage."""
    if raw[:2]!=b'\xff\xd8':raise ValueError('Not JPEG')
    p=2
    while p<len(raw):
        if raw[p]!=255:raise ValueError('Malformed JPEG header')
        while p<len(raw) and raw[p]==255:p+=1
        if p>=len(raw):break
        marker=raw[p];p+=1
        if marker in (0xd9,0xda):break
        if marker in (0x01,*range(0xd0,0xd9)):continue
        if p+2>len(raw):break
        length=int.from_bytes(raw[p:p+2],'big')
        if length<2 or p+length>len(raw):break
        if marker in (0xc0,0xc1,0xc2):
            if length<8 or raw[p+2]!=8:raise ValueError('Unsupported JPEG SOF')
            return int.from_bytes(raw[p+5:p+7],'big'),int.from_bytes(raw[p+3:p+5],'big')
        p+=length
    raise ValueError('No valid JPEG frame header')

def finite(v,label,low=-1e9,high=1e12):
    if type(v) not in (float,int):raise ValueError('Invalid '+label)
    try:v=float(v)
    except (ValueError,OverflowError):raise ValueError('Invalid '+label) from None
    if not math.isfinite(v) or not low<=v<=high:raise ValueError('Invalid '+label)
    return v

def integer(v,label,minimum=0):
    if type(v) is not int or not minimum<=v<=2**53-1:raise ValueError('Invalid '+label)
    return v

from .sim_platform_diagnostics import DiagnosticLog,cancellation_code

class PlatformTransport:
    def __init__(self,url='http://127.0.0.1:8766',*,role='spectator',clock=time.monotonic,client=None):
        if role not in ('operator','spectator'):raise ValueError('Explicit operator/spectator role required')
        self.client=client or PlatformClient(url,timeout=.15,control_profile='server_window_v1' if role=='operator' else 'legacy_v2',diagnostic_clock=clock);self.role=role;self.clock=clock
        self._diagnostic_failures=0;self._diagnostics=DiagnosticLog(clock=clock)
        self._lock=threading.RLock();self._stop=threading.Event();self._threads=[]
        self._wake_control=threading.Event();self._handoff=HandoffSchedule();self._batch_revision=-1
        self.session=None;self.raw_state=None;self.raw_state_at=None;self.telemetry=None;self.telemetry_at=0.;self.robot_ids=()
        self.watch_robot=None;self.watch_epoch=None;self._watch_generation=0;self._watch_job=None
        self._claim_job=None;self._release_job=None;self.owned_robot=None;self.owned_epochs={};self.claim_pending=False
        self._generation=0;self._connection_generation=0;self._connected=False;self._control_generation=0;self._claim_serial=0;self._focus=False;self._input_at=-1e9;self._heartbeat_at=-1e9
        self._intent=dict(vx=0.,vy=0.,wz=0.,fire=False,spin=None);self._angles=None;self._renew_at=0.;self._keepalive_at=0.
        self._input_revision=0;self._window=None;self._window_deadline=0.;self._window_sample_cut=0
        self.video=None;self.video_at=0.;self.video_sequence=-1;self.video_sim_time=None;self.frame_error='No v2 camera provider'
        self.video_native_size=None;self.video_source_sequence=None;self.max_frame_age=2.5
        self.last_error='V2 disconnected';self.note='V2 '+role+'; choose a watched robot'
        self._spin_caps=None;self._spin_caps_at=-1e9
        self._selection_busy=False;self.selection_pending=False;self.selection_status='';self.selection_target=None
        self.catalog=parse_catalog(None);self._catalog_serial=0;self._catalog_at=-1e9
        self.plan={};self._plan_generation=0;self._bindings={};self._bind_pending=False;self._activation_acks={}
    def _diagnostic_context(self):
        try:now=self.clock()
        except BaseException:now=None
        raw=self.raw_state if type(self.raw_state) is dict else None
        def age(stamp):return None if now is None or stamp is None or stamp<0 or now<stamp else now-stamp
        return dict(session_id=getattr(self.client,'session_id',None),robot_id=self.owned_robot or self.watch_robot,
            lease_epochs=dict(self.owned_epochs),watch_epoch=self.watch_epoch,
            connection_generation=self._connection_generation,control_generation=self._control_generation,
            watch_generation=self._watch_generation,plan_generation=self._plan_generation,
            input_revision=self._input_revision,window_id=self._window.get('window_id') if self._window else None,
            window_deadline_local=self._window_deadline if self._window else None,
            window_remaining_local_s=self._window_deadline-now if self._window and now is not None else None,
            episode=raw.get('episode') if raw else None,sample_sim_time=raw.get('sim_time') if raw else None,
            sample_received_wall=self.raw_state_at,sample_age_wall_s=age(self.raw_state_at),
            input_age_wall_s=age(self._input_at),heartbeat_age_wall_s=age(self._heartbeat_at),
            focus=self._focus,connected=self._connected,uncertain=bool(self.client.uncertain),
            raw_state_available=raw is not None,telemetry_available=self.telemetry is not None,
            thread=threading.current_thread().name)
    def _record(self,event,*,failure=False,**fields):
        # Observability must never prevent the original neutral/revoke path.
        try:
            with self._lock:
                context=self._diagnostic_context();context.update(fields)
                return self._diagnostics.record(event,failure=failure,**context)
        except BaseException:
            self._diagnostic_failures+=1;return None
    def diagnostics(self):
        # Do not hold the transport lock while reading SDK diagnostics.
        own=self._diagnostics.snapshot();own['dropped_events']=self._diagnostic_failures
        sdk=self.client.diagnostics() if hasattr(self.client,'diagnostics') else None
        return dict(schema='polar.sim.client.diagnostics.v1',transport=own,sdk=sdk,
            meaning='local monotonic events and last-known sampled state; no server clock alignment inferred')
    def start(self):
        if self._threads:return
        self._stop.clear()
        for name,target in [('v2-control',self._control_loop),('v2-state',self._state_loop),('v2-camera',self._camera_loop)]:
            thread=threading.Thread(name=name,target=target,daemon=True);self._threads.append(thread);thread.start()
    def stop(self):
        self._record('stop_started',stage='before_release');self.neutral('V2 disconnected');self._stop.set()
        with self._lock:
            self._connection_generation+=1;self._connected=False;self._watch_generation+=1;self._generation+=1
            self.session=None;self.raw_state=None;self.raw_state_at=None;self.telemetry=None;self.telemetry_at=0.;self.robot_ids=()
            self._watch_job=None;self.watch_epoch=None;self.selection_pending=False
            self._clear_frame('Stopped',reset_identity=True);self.last_error='V2 disconnected'
            self._reset_adapter_negotiation()
        for thread in self._threads:thread.join(timeout=.5)
        alive=sum(thread.is_alive() for thread in self._threads)
        if alive:self._record('stop_workers_not_quiesced',failure=True,stage='bounded_join_elapsed',thread_alive_count=alive)
        self._threads=[]
        try:self.client.close()
        except Exception as exc:self._record('stop_close_failed',failure=True,stage='close_session',exception_type=type(exc).__name__,request_id=getattr(exc,'request_id',None),reason_code=getattr(exc,'code',None) or 'unclassified_error')
        self._record('stop_close_attempt_finished',stage='close_returned',thread_alive_count=alive)
    def connect_once(self):
        with self._lock:generation=self._connection_generation
        result=self.client.open(self.role)
        with self._lock:
            if self._stop.is_set() or generation!=self._connection_generation:
                self.client.uncertain=True;return result
            self._reset_adapter_negotiation();self._handoff.new_session()
            self.session=dict(result,robot_id=self.watch_robot,capabilities={});self._generation+=1;self._connection_generation+=1;self._connected=True;self.last_error=''
        return result
    def neutral(self,reason='Input released'):
        with self._lock:
            self._record('neutral_requested',stage='before_clear_input',reason_code=cancellation_code(reason))
            self._handoff.reset();self._batch_revision=-1;self._wake_control.set()
            self._control_generation+=1;self._window=None;self._window_deadline=0.;self._window_sample_cut=self._input_revision
            self._focus=False;self._intent=dict(vx=0.,vy=0.,wz=0.,fire=False,spin=None);self._angles=None;self._input_at=-1e9
            self._claim_job=None;self.claim_pending=False;self._bindings={};self._bind_pending=False;self._activation_acks={}
            if self.owned_robot is not None:self._release_job=(self.owned_robot,dict(self.owned_epochs))
            self.owned_robot=None;self.owned_epochs={};self.note=reason
    def watch(self,rid):
        with self._lock:
            self.neutral('Watching '+str(rid)+'; no control requested')
            if rid is not None and rid not in self.robot_ids:raise ValueError('Robot is not in server registry')
            self._spin_caps=None;self._spin_caps_at=-1e9
            self._watch_generation+=1;self._generation+=1;self.watch_robot=rid;self.watch_epoch=None
            self.plan={};self._plan_generation+=1
            self._watch_job=(rid,self._watch_generation);self.selection_pending=True
            self.telemetry=None;self._clear_frame('Waiting for current watch camera',reset_identity=True)
            if self.session:self.session['robot_id']=rid
    def _clear_frame(self,reason,*,reset_identity=False):
        self.video=None;self.video_at=0.;self.frame_error=reason
        if reset_identity:self.video_sequence=-1;self.video_sim_time=None
    def request_claim(self):
        with self._lock:
            if self.role!='operator':raise ValueError('Spectator cannot control')
            if self.client.uncertain or self.session is None:raise ValueError('Reconnect the uncertain/disconnected session')
            if not self.selection_ready():raise ValueError('Need current watched robot state and camera')
            if self.claim_pending:return False
            if self.owned_robot==self.watch_robot:return True
            self._claim_serial+=1;self.claim_pending=True;self._claim_job=(self.watch_robot,self._watch_generation,self._control_generation,self._claim_serial);self.note='Claim pending; no controls active'
            return False
    def activate(self):
        with self._lock:
            if self.role!='operator' or self.client.uncertain or self.owned_robot!=self.watch_robot or not self.selection_ready():return False
            own=self.telemetry['own'];self._angles=[own['gimbal_yaw'],own['pitch']]
            self._bindings={};self._activation_acks={};self._bind_pending=bool(self.plan)
            self._focus=True;self._heartbeat_at=self.clock();self._input_at=self.clock();self._intent=dict(vx=0.,vy=0.,wz=0.,fire=False,spin=None)
            return True
    def heartbeat(self,allowed):
        with self._lock:
            now=self.clock()
            if self._focus and (not allowed or now-self._heartbeat_at>.15):self.neutral('Input heartbeat/focus lost; reclaim then F2')
            self._heartbeat_at=now
    def submit_input(self,*,vx_mps,vy_mps,chassis_yaw_rps=0.,spin_request=None,yaw_rate_rps=0.,pitch_rate_rps=0.,fire=False):
        with self._lock:
            if not self._focus:return
            vx=finite(vx_mps,'vx',-1,1);vy=finite(vy_mps,'vy',-1,1);norm=max(1.,math.hypot(vx,vy))
            wz=finite(chassis_yaw_rps,'chassis yaw',-1.,1.)
            from .sim_platform_spin import request
            spin=None if spin_request is None else request(spin_request)
            if spin is not None and (self.spin_capability() is None or 'navigation' in self.plan):raise ValueError('Spin capability or motion ownership unavailable')
            if type(fire) is not bool:raise ValueError('Invalid fire')
            self._intent=dict(vx=vx/norm,vy=vy/norm,wz=wz,fire=fire,spin=spin);self._input_at=self.clock();self._input_revision+=1
            if self._handoff.phase=='WAIT_POST_ACK_SAMPLE':self._wake_control.set()
    def submit_mouse(self,yaw_delta_rad,pitch_delta_rad):
        with self._lock:
            if not self._focus or self._angles is None or 'aim' in self.plan:return
            yaw=finite(yaw_delta_rad,'mouse yaw',-1,1);pitch=finite(pitch_delta_rad,'mouse pitch',-1,1)
            self._angles=[max(-3.14,min(3.14,self._angles[0]+yaw)),max(-.4,min(.5,self._angles[1]+pitch))]
    def submit_modes(self,*args):return None
    def _reset_adapter_negotiation(self):
        self._spin_caps=None;self._spin_caps_at=-1e9
        self.catalog=parse_catalog(None);self.plan={};self._plan_generation+=1
        self._catalog_at=-1e9;self._catalog_serial=0;self._bindings={};self._bind_pending=False;self._activation_acks={}
    def install_catalog(self,raw,*,connection_generation,serial,watch_generation=None):
        catalog=parse_catalog(raw)
        with self._lock:
            if connection_generation!=self._connection_generation or serial<self._catalog_serial or not self._connected or self._stop.is_set() or self.client.uncertain:return False
            if watch_generation is not None and watch_generation!=self._watch_generation:return False
            advertised=raw.get('control_capabilities') if type(raw) is dict else None
            if type(advertised) is dict:
                if type(advertised.get('watch_epoch')) is int and (advertised.get('watch_robot_id')!=self.watch_robot or advertised['watch_epoch']!=self.watch_epoch or advertised.get('session_id')!=self.client.session_id):return False
                if type(advertised.get('episode')) is int and advertised['episode']!=(self.raw_state or {}).get('episode'):return False
            self._catalog_serial=serial
            if catalog['signature']!=self.catalog['signature']:
                self.neutral('Adapter catalog changed; inspect choices then reclaim')
                self.plan={};self._plan_generation+=1
            from .sim_platform_spin import capability
            negotiated=capability(advertised,self.client.session_id,self.watch_robot,self.watch_epoch,(self.raw_state or {}).get('episode'))
            if negotiated!=self._spin_caps:self.neutral('Spin capabilities changed; inspect then reclaim')
            self._spin_caps=negotiated
            self._spin_caps_at=self.clock()
            self.catalog=catalog;return True
    def spin_capability(self):
        with self._lock:
            c=self._spin_caps
            if (getattr(self.client,'control_profile','legacy_v2')!='server_window_v1' or c is None or not self._connected or self.client.uncertain or self._stop.is_set()
                or c['session_id']!=self.client.session_id or c['robot_id']!=self.watch_robot or c['watch_epoch']!=self.watch_epoch or c['episode']!=(self.raw_state or {}).get('episode') or self.clock()-self._spin_caps_at>2.5):return None
            return copy.deepcopy(c)

    def configure_adapters(self,choices,values):
        with self._lock:
            if self.role!='operator':raise ValueError('Spectator cannot configure control bindings')
            plan=make_plan(self.catalog,self.watch_robot,choices,values)
            if plan!=self.plan:
                self.neutral('Adapter plan changed; claim then F2 to bind')
                self.plan=plan;self._plan_generation+=1
            return copy.deepcopy(plan)
    def adapter_feedback(self):
        with self._lock:
            if self._bind_pending:return 'Adapter bind pending; no actuation confirmed'
            if not self.plan:return 'Manual motion + manual gimbal'
            labels=[]
            for kind in self.plan:
                bound=self._bindings.get(kind)
                status=feedback(self.raw_state,self.watch_robot,kind,bound,self.client.session_id) if bound else 'selected, disarmed'
                labels.append(kind+': '+status)
            return ' | '.join(labels)
    def _same_control(self,rid,epochs,generation,plan_generation):
        return (generation==self._control_generation and plan_generation==self._plan_generation
            and self._focus and self._connected and not self._stop.is_set() and not self.client.uncertain
            and rid==self.owned_robot and epochs==self.owned_epochs)
    def _release_late_binding(self,rid,epochs):
        # The remote bind may have succeeded even though its local intent was revoked.
        try:
            self.client.release(rid,list(epochs))
            with self._lock:
                if self._release_job==(rid,epochs):self._release_job=None
        except Exception as exc:
            self._failed(exc,stage='late_binding_cleanup')
    def _bind_plan(self,rid,epochs,generation,plan_generation,plan):
        for kind,item in plan.items():
            with self._lock:
                if not self._same_control(rid,epochs,generation,plan_generation):return
            ack=self.client.bind(rid,kind,item['adapter_id'],item['config'])
            with self._lock:
                valid=self._same_control(rid,epochs,generation,plan_generation)
                if valid:self._bindings[kind]=dict(ack)
            if not valid:
                self._release_late_binding(rid,epochs);return
        with self._lock:
            if self._same_control(rid,epochs,generation,plan_generation):
                self._bind_pending=False;self._window=None;self._window_deadline=0.;self._handoff.reset();self.note='Bound, waiting for a fresh operator authorization sample'

    def selection_ready(self):
        data,video,*_=self.snapshot()
        return bool(not self.client.uncertain and self.watch_robot is not None and self.watch_epoch is not None and data is not None and video is not None and not self.selection_pending)
    def install_state(self,data,*,connection_generation=None):
        with self._lock:
            generation=self._connection_generation if connection_generation is None else connection_generation
            if generation!=self._connection_generation or not self._connected or self._stop.is_set() or self.client.uncertain:return False
        if data.get('schema')!='rm.sim.platform.v2' or data.get('source')!='simulator_truth_display_only' or data.get('algorithm_input') is not False:raise ValueError('Wrong v2 display provenance')
        stamp=finite(data.get('sim_time'),'simulation time',0);episode=integer(data.get('episode'),'episode')
        robots=data.get('robots')
        if type(robots) is not list or not 1<=len(robots)<=64:raise ValueError('Invalid robot registry')
        seen=set();mapped=[]
        for raw in robots:
            rid=raw.get('robot_id')
            if type(rid) is not str or not rid or len(rid)>128 or rid in seen:raise ValueError('Invalid robot identity')
            seen.add(rid)
            if type(raw.get('alive')) is not bool or raw.get('team') not in ('red','blue') or raw.get('role') not in ('hero','infantry','sentry'):raise ValueError('Unsupported current HUD robot profile')
            xy=raw.get('xy')
            if type(xy) is not list or len(xy)!=2:raise ValueError('Invalid robot XY')
            r=dict(robot_id=rid,team=raw['team'],role=raw['role'],alive=raw['alive'],xy=[finite(x,'XY') for x in xy],hp=finite(raw.get('hp'),'HP',0),chassis_yaw=finite(raw.get('chassis_yaw',raw.get('yaw')),'yaw'),gimbal_yaw=finite(raw.get('gimbal_yaw'),'relative yaw'),pitch=finite(raw.get('pitch',raw.get('gimbal_pitch')),'pitch'))
            for k in ('max_hp','ammo','heat','heat_max'):
                r[k]=None if raw.get(k) is None else finite(raw[k],k,0)
            mapped.append(r)
        if data.get('phase') not in ('running','paused','idle','finished','countdown'):raise ValueError('Unknown match phase')
        with self._lock:
            if generation!=self._connection_generation or not self._connected or self._stop.is_set() or self.client.uncertain:return False
            if self.raw_state and (episode<self.raw_state['episode'] or (episode==self.raw_state['episode'] and stamp<self.raw_state['sim_time'])):raise ValueError('Old display state')
            if self.raw_state and episode!=self.raw_state['episode']:
                self.neutral('New episode; explicit claim required');self._clear_frame('New episode',reset_identity=True);self.watch_epoch=None;self._watch_generation+=1;self._watch_job=(self.watch_robot,self._watch_generation);self._generation+=1
            self.raw_state=copy.deepcopy(data);self.raw_state_at=self.clock();self.robot_ids=tuple(r['robot_id'] for r in mapped)
            own=next((r for r in mapped if r['robot_id']==self.watch_robot),None)
            if own is None:self.telemetry=None;return
            remaining=data.get('remaining_seconds');vp=data.get('victory_points',data.get('vp',{}))
            if remaining is not None:remaining=finite(remaining,'remaining seconds',0)
            if type(vp) is not dict:raise ValueError('Invalid score state')
            scores={k:None if vp.get(k) is None else finite(vp[k],k+' score',0) for k in ('red','blue')}
            self.telemetry=dict(robot_id=self.watch_robot,episode=episode,sim_time=stamp,own=own,robots=mapped,phase=data['phase'],remaining_seconds=remaining,vp=scores,map_source='simulator_truth_display_only')
            self.telemetry_at=self.clock();self.last_error=''
            if self.owned_robot:
                leases=(data.get('control') or {}).get('leases',{}).get(self.owned_robot,{})
                if any(leases.get(c,{}).get('owner')!=self.client.session_id or leases.get(c,{}).get('epoch')!=self.owned_epochs.get(c) for c in CHANNELS):self.neutral('Control ownership changed; explicit claim required')
    def snapshot(self):
        with self._lock:
            now=self.clock()
            if not self._connected or self._stop.is_set() or self.client.uncertain:return None,None,0.,self.video_sequence,self.video_sim_time
            state=self.telemetry if now-self.telemetry_at<=.5 else None
            video=self.video if now-self.video_at<=self.max_frame_age else None
            return state,video,self.video_at if video is not None else 0.,self.video_sequence,self.video_sim_time
    def process_jobs(self):
        with self._lock:
            release=self._release_job;self._release_job=None;watch=self._watch_job;self._watch_job=None;claim=self._claim_job;self._claim_job=None
        if release:
            rid,epochs=release
            try:self.client.release(rid,list(epochs))
            except ClientError as exc:
                self._failed(exc,stage='lease_release');return
        if watch:
            rid,generation=watch;result=self.client.watch(rid)
            with self._lock:
                if generation==self._watch_generation:
                    self.watch_epoch=result['watch_epoch'];self.selection_pending=False;self._catalog_at=-1e9
        if claim:
            rid,generation,control_generation,serial=claim
            try:
                result=self.client.claim(rid,list(CHANNELS))
                with self._lock:
                    if generation==self._watch_generation and control_generation==self._control_generation and serial==self._claim_serial and self.claim_pending and self._connected and not self._stop.is_set() and not self.client.uncertain:
                        self.owned_robot=rid;self.owned_epochs=dict(result['lease_epochs']);self._renew_at=self.clock();self.note='Lease claimed, input still disarmed; press F2 again'
                    else:self._release_job=(rid,dict(result['lease_epochs']))
            finally:
                with self._lock:
                    if serial==self._claim_serial:self.claim_pending=False
    def control_tick(self):
        if getattr(self.client,'control_profile','legacy_v2')=='server_window_v1':
            return self._strict_control_tick()
        return self._legacy_control_tick()

    def _legacy_control_tick(self):
        self.process_jobs()
        with self._lock:
            if not self._connected or self._stop.is_set() or self.client.uncertain:return
        if self.session is not None and self.clock()-self._keepalive_at>10:
            self.client.keepalive();self._keepalive_at=self.clock()
        with self._lock:
            now=self.clock();rid=self.owned_robot;epochs=dict(self.owned_epochs)
            if rid is None:return
            state,video,*_=self.snapshot()
            if self.client.uncertain or state is None or video is None or now-self._heartbeat_at>.15:
                self.neutral('V2 state/camera/heartbeat expired');return
            renew=now-self._renew_at>.3
            active=self._focus and now-self._input_at<=.15
            if self._focus and not active:self.neutral('No fresh input; explicit rearm required');return
            generation=self._control_generation;plan_generation=self._plan_generation;plan=copy.deepcopy(self.plan);bind_pending=self._bind_pending
            strict=getattr(self.client,'control_profile','legacy_v2')=='server_window_v1'
            need_window=strict and (self._window is None or now>=self._window_deadline-.05)
        if renew:self.client.renew(rid,list(CHANNELS));self._renew_at=now
        if not active:return
        if bind_pending:
            self._bind_plan(rid,epochs,generation,plan_generation,plan);return
        if need_window:
            # Runs only on the control worker, never the GUI thread. Anchor to
            # request start; server monotonic timestamps are never mapped locally.
            with self._lock:
                if generation!=self._control_generation or not self._focus or rid!=self.owned_robot or epochs!=self.owned_epochs:return
                started=self.clock();self._record('window_requested',stage='control_worker_prefetch')
            window=self.client.control_window(rid,list(CHANNELS))
            with self._lock:
                if generation!=self._control_generation or not self._focus or rid!=self.owned_robot or epochs!=self.owned_epochs:return
                deadline=started+window['valid_for_s']
                if self.clock()>=deadline:
                    self.neutral('Input window arrived too late; explicit reclaim required');return
                self._window=window;self._window_deadline=deadline;self._window_sample_cut=self._input_revision
                self._record('window_installed',stage='await_new_input_sample')
            return  # Wait for an ordinary Qt input sample produced after ACK.
        with self._lock:
            if generation!=self._control_generation or not self._focus:return
            if strict and (self._window is None or self._input_revision<=self._window_sample_cut):return
            intent=dict(self._intent);angles=list(self._angles) if self._angles else None
            window_id=self._window['window_id'] if strict else None
        values=dict(motion=dict(frame='base_link',vx_mps=intent['vx'],vy_mps=intent['vy'],wz_rps=0.),gimbal=dict(frame='base_link',yaw_rad=angles[0],pitch_rad=angles[1]),fire=dict(fire=intent['fire']))
        for kind in plan:
            with self._lock:
                if not self._same_control(rid,epochs,generation,plan_generation):return
                if self.clock()-self._input_at>.15 or self.clock()>=self._window_deadline:
                    self.neutral('Adapter input sample/window expired before send');return
                binding=self._bindings.get(kind)
                if binding is None:self.neutral('Binding missing; explicit reclaim required');return
            self._record('adapter_pulse_sending',stage='before_send',kind=kind,binding_epoch=binding['binding_epoch'])
            ack=self.client.adapter_input(rid,kind,binding['binding_epoch'],window_id=window_id,valid_for_s=.15)
            with self._lock:
                valid=self._same_control(rid,epochs,generation,plan_generation)
                if valid:self._activation_acks[kind]=dict(ack)
            if not valid:
                self._release_late_binding(rid,epochs);return
        for channel in CHANNELS:
            if any(KINDS[k]==channel for k in plan):continue
            with self._lock:
                if not self._same_control(rid,epochs,generation,plan_generation):return
                if self.clock()-self._input_at>.15 or (strict and self.clock()>=self._window_deadline):
                    self.neutral('Input sample/window expired before send');return
            options={'window_id':window_id} if strict else {}
            self.client.control(rid,channel,values[channel],valid_for_s=.15,**options)
    def _strict_control_tick(self):
        """One worker, no retry queue, request-start window and per-kind budgets."""
        self.process_jobs()  # Releases precede maintenance, binding and new input.
        with self._lock:
            if not self._connected or self._stop.is_set() or self.client.uncertain:return
            rid=self.owned_robot;epochs=dict(self.owned_epochs)
            generation=self._control_generation;plan_generation=self._plan_generation
            plan=copy.deepcopy(self.plan);bind_pending=self._bind_pending
            focused=self._focus

        def valid():
            with self._lock:
                if not self._same_control(rid,epochs,generation,plan_generation):return False
                now=self.clock();state,video,*_=self.snapshot()
                reason=self._handoff.expired(now)
                if self._intent.get('spin') is not None and self.spin_capability() is None:
                    reason='spin_capability_unavailable'
                if state is None or video is None or now-self._heartbeat_at>.15 or now-self._input_at>.15:
                    reason='state_camera_or_input_expired'
                if reason:
                    self._record('handoff_cancelled',failure=True,stage='deadline_recheck',reason_code=reason)
                    self.neutral('V2 handoff '+reason+'; explicit reclaim required');return False
                return True

        def request(category,call):
            started=self.clock()
            try:return call()
            finally:
                ended=self.clock()
                with self._lock:self._handoff.observe(category,started,ended)

        # Maintenance due within the predicted handoff is paid before issuance,
        # rather than unexpectedly consuming the short authorization budget.
        controls=sum(not any(KINDS[k]==ch for k in plan) for ch in CHANNELS)
        horizon=self._handoff.cost(len(plan),controls) if focused else 0.
        # Recheck existing authority before and after each shared-worker task.
        if focused and not valid():return
        if self.session is not None and self.clock()+horizon-self._keepalive_at>10:
            request('keepalive',self.client.keepalive);self._keepalive_at=self.clock()
            if focused and not valid():return
        if rid is None:return
        if self.clock()+horizon-self._renew_at>.3:
            request('renew',lambda:self.client.renew(rid,list(CHANNELS)))
            self._renew_at=self.clock()
            if focused and not valid():return
        if not focused:return
        if not valid():return
        if bind_pending:
            self._bind_plan(rid,epochs,generation,plan_generation,plan);return
        with self._lock:
            fresh=self._input_revision>max(self._window_sample_cut,self._batch_revision)
            controls=sum(not any(KINDS[k]==ch for k in plan) for ch in CHANNELS)
            # Budget any maintenance that can become due during this handoff.
            base=self._handoff.cost(len(plan),controls)
            maintenance=[]
            if self.clock()+base-self._renew_at>.3:maintenance.append('renew')
            if self.clock()+base-self._keepalive_at>10:maintenance.append('keepalive')
            action=self._handoff.next_action(self.clock(),kinds=len(plan),controls=controls,
                fresh=fresh,maintenance=maintenance)
            if action not in ('fetch','send','wait'):
                self._record('handoff_cancelled',failure=True,stage='scheduling_budget',reason_code=action)
                self.neutral('V2 handoff '+action+'; explicit reclaim required');return
            if action=='wait':return
        if action=='fetch':
            if not valid():return
            started=self.clock();self._handoff.phase='FETCHING';self._record('window_requested',stage=self._handoff.handoff_reason)
            window=request('window',lambda:self.client.control_window(rid,list(CHANNELS)))
            with self._lock:
                if not valid():return
                if self.clock()>=started+window['valid_for_s']:
                    self.neutral('Input window arrived too late; explicit reclaim required');return
                self._handoff.install(window,started,self.clock())
                self._window=dict(window);self._window_deadline=self._handoff.deadline
                self._window_sample_cut=self._input_revision
                self._record('window_installed',stage='await_new_input_sample')
            return
        with self._lock:
            if not valid():return
            if self._input_revision<=max(self._window_sample_cut,self._batch_revision):return
            intent=dict(self._intent);angles=list(self._angles) if self._angles else None
            if angles is None:self.neutral('Missing gimbal input');return
            revision=self._input_revision;window_id=self._window['window_id']
            self._handoff.begin_batch(self.clock())
        motion=dict(frame='base_link',vx_mps=intent['vx'],vy_mps=intent['vy'],wz_rps=intent['wz'])
        if intent.get('spin') is not None:motion=dict(mode='spin_v1',frame='base_link',vx_mps=intent['vx'],vy_mps=intent['vy'],**intent['spin'])
        values=dict(motion=motion,
            gimbal=dict(frame='base_link',yaw_rad=angles[0],pitch_rad=angles[1]),fire=dict(fire=intent['fire']))
        for kind in plan:
            if not valid():return
            with self._lock:
                binding=self._bindings.get(kind)
                if binding is None:self.neutral('Binding missing; explicit reclaim required');return
            self._record('adapter_pulse_sending',stage='before_send',kind=kind,binding_epoch=binding['binding_epoch'])
            with self._lock:self._handoff.mark_kind_attempt(kind)
            ack=request('pulse',lambda:self.client.adapter_input(rid,kind,binding['binding_epoch'],window_id=window_id,valid_for_s=.15))
            with self._lock:
                same=self._same_control(rid,epochs,generation,plan_generation)
                if same:
                    # An accepted pulse can have renewed this kind before ACK
                    # transit crosses its *old* bound. Update only this kind,
                    # then check all other kinds and the original current window.
                    self._handoff.acknowledge(kind,ack);self._activation_acks[kind]=dict(ack)
            if not same:self._release_late_binding(rid,epochs);return
            if not valid():return
        for channel in CHANNELS:
            if any(KINDS[k]==channel for k in plan):continue
            if not valid():return
            request('control',lambda:self.client.control(rid,channel,values[channel],valid_for_s=.15,window_id=window_id))
            if not valid():return
        with self._lock:
            if not valid():return
            self._batch_revision=revision;self._handoff.finish_batch()

    def _failed(self,error,*,stage='unspecified'):
        with self._lock:
            self._record('failure_before_cancel',failure=True,stage=stage,exception_type=type(error).__name__,request_id=getattr(error,'request_id',None),reason_code=getattr(error,'code',None) or 'unclassified_error')
            self.neutral('V2 connection error; restart to reconnect');self.client.uncertain=True
            self._connected=False;self._connection_generation+=1;self._watch_generation+=1;self._generation+=1
            self.raw_state=None;self.raw_state_at=None;self.telemetry=None;self.telemetry_at=0.;self.robot_ids=();self.watch_epoch=None;self._watch_job=None;self.selection_pending=False
            self._clear_frame(str(error),reset_identity=True);self.last_error=str(error)
            self._reset_adapter_negotiation()
            self._record('failure_cancelled',stage='input_and_display_invalidated')
    def _control_loop(self):
        try:self.connect_once()
        except Exception as exc:self._failed(exc,stage='session_open');return
        while not self._stop.is_set():
            self._wake_control.clear()
            try:self.control_tick()
            except Exception as exc:self._failed(exc,stage='control_loop')
            strict=getattr(self.client,'control_profile','legacy_v2')=='server_window_v1'
            if strict:self._wake_control.wait(.005 if self._focus else .02)
            else:self._stop.wait(.02)
    def _state_loop(self):
        while not self._stop.is_set():
            with self._lock:ready=self._connected and not self.client.uncertain;generation=self._connection_generation;watch_generation=self._watch_generation
            if ready:
                try:
                    if hasattr(self.client,'adapters') and self.clock()-self._catalog_at>2:
                        serial=self._catalog_serial+1;raw=self.client.adapters()
                        self.install_catalog(raw,connection_generation=generation,serial=serial,watch_generation=watch_generation);self._catalog_at=self.clock()
                    self.install_state(self.client.state(),connection_generation=generation)
                except Exception as exc:
                    with self._lock:
                        if generation==self._connection_generation:self._failed(exc,stage='state_loop')
            self._stop.wait(.05)
    def _camera_loop(self):
        while not self._stop.is_set():
            with self._lock:ready=self._connected and not self.client.uncertain and self.watch_epoch is not None;generation=self._watch_generation
            if ready:
                try:
                    request_started=self.clock()
                    self.install_frame(self.client.frame(),generation=generation,request_started=request_started)
                except Exception as exc:
                    with self._lock:
                        if generation==self._watch_generation:
                            self._record('camera_failed_before_cancel',failure=True,stage='camera_loop',exception_type=type(exc).__name__,request_id=getattr(exc,'request_id',None),reason_code=getattr(exc,'code',None) or 'unclassified_error')
                            self._clear_frame(str(exc));self.neutral('Camera error; explicit reclaim required')
            self._stop.wait(.1)
    def install_frame(self,data,*,generation,request_started=None):
        # The camera provider supplies this extension in its separate checkpoint.
        with self._lock:
            if generation!=self._watch_generation or not self._connected or self._stop.is_set() or self.client.uncertain:return False
            if type(data.get('available')) is not bool:raise ValueError('Invalid camera availability')
            if not data['available']:
                self._clear_frame(str(data.get('reason','Camera unavailable')));return False
            watch_epoch=integer(data.get('watch_epoch'),'watch epoch');episode=integer(data.get('episode'),'camera episode')
            if data.get('robot_id')!=self.watch_robot or watch_epoch!=self.watch_epoch or self.raw_state is None or episode!=self.raw_state['episode']:raise ValueError('Wrong watched-camera identity')
            index=integer(data.get('frame_seq'),'frame sequence');stamp=finite(data.get('capture_sim_time'),'capture time',0)
            age=finite(data.get('age_wall_s'),'camera age',0,2.5)
            # Server age excludes response transfer and local decode. Anchor
            # conservatively at request start; never rejuvenate a delayed frame.
            received_at=self.clock()
            started=received_at if request_started is None else finite(request_started,'request start',0,received_at)
            capture_local=started-age
            ttl=finite(data.get('max_frame_age_wall_s'),'camera TTL',.1,2.5)
            if received_at-capture_local>ttl:raise ValueError('Expired camera sample')
            capture_wall=finite(data.get('capture_wall_monotonic'),'capture wall',0)
            encoded_wall=finite(data.get('encode_completed_wall_monotonic'),'encode wall',capture_wall)
            for key,size in [('own_qpos_at_capture',5),('own_qvel_at_capture',5),('own_odom_at_capture',3),('own_odom_velocity_at_capture',3)]:
                v=data.get(key)
                if type(v) is not list or len(v)!=size:raise ValueError('Missing exposure state')
                for n in v:finite(n,key)
            cal=data.get('calibration',{});intr=cal.get('intrinsics',{});ext=cal.get('extrinsics',{})
            for k in ('fx','fy'):finite(intr.get(k),k,1e-6)
            for k in ('cx','cy'):finite(intr.get(k),k)
            if intr.get('distortion_model')!='brown_conrady_5' or type(intr.get('distortion')) is not list or len(intr['distortion'])!=5:raise ValueError('Unknown camera distortion')
            for v in intr['distortion']:finite(v,'distortion')
            if ext.get('parent_frame')!='gimbal_link' or not isinstance(ext.get('calibration_id'),str):raise ValueError('Unknown camera extrinsics')
            for k,n in [('translation_m',3),('rotation_xyzw',4)]:
                values=ext.get(k)
                if type(values) is not list or len(values)!=n:raise ValueError('Invalid camera extrinsics')
                for v in values:finite(v,k)
            if abs(sum(v*v for v in ext['rotation_xyzw'])-1)>1e-6:raise ValueError('Nonunit camera rotation')
            if data.get('source')!='simulated_raw_camera_display_jpeg' or data.get('full_frame') is not True or data.get('algorithm_input') is not False:raise ValueError('Raw full-frame display required')
            if stamp>self.raw_state['sim_time']+.05:raise ValueError('Camera time is ahead of state')
            if index<=self.video_sequence:return False
            if self.video_sim_time is not None and stamp<self.video_sim_time:raise ValueError('Reversed camera time')
        im=data.get('image',{})
        if im.get('encoding')!='jpeg':raise ValueError('Expected display JPEG')
        w=integer(im.get('width'),'image width',1);h=integer(im.get('height'),'image height',1)
        if w>1920 or h>1440:raise ValueError('Camera size exceeds display bound')
        encoded=im.get('data_base64')
        if type(encoded) is not str or len(encoded)>2*1024*1024*4//3+4:raise ValueError('Camera payload exceeds bound')
        raw=base64.b64decode(encoded,validate=True)
        if len(raw)>2*1024*1024 or jpeg_dimensions(raw)!=(w,h):raise ValueError('JPEG header dimensions mismatch')
        import cv2,numpy as np
        bgr=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
        if bgr is None or bgr.shape!=(h,w,3):raise ValueError('Invalid JPEG image')
        with self._lock:
            if generation!=self._watch_generation or not self._connected or self._stop.is_set() or self.client.uncertain:return False
            if self.clock()-capture_local>ttl:raise ValueError('Camera expired during decode')
            self.max_frame_age=ttl;self.video=bgr;self.video_at=capture_local;self.video_sequence=index;self.video_sim_time=stamp;self.video_native_size=(w,h);self.frame_error='';return True
