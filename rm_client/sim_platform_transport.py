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

class PlatformTransport:
    def __init__(self,url='http://127.0.0.1:8766',*,role='spectator',clock=time.monotonic,client=None):
        if role not in ('operator','spectator'):raise ValueError('Explicit operator/spectator role required')
        self.client=client or PlatformClient(url,timeout=.15,control_profile='server_window_v1' if role=='operator' else 'legacy_v2');self.role=role;self.clock=clock
        self._lock=threading.RLock();self._stop=threading.Event();self._threads=[]
        self.session=None;self.raw_state=None;self.telemetry=None;self.telemetry_at=0.;self.robot_ids=()
        self.watch_robot=None;self.watch_epoch=None;self._watch_generation=0;self._watch_job=None
        self._claim_job=None;self._release_job=None;self.owned_robot=None;self.owned_epochs={};self.claim_pending=False
        self._generation=0;self._connection_generation=0;self._connected=False;self._control_generation=0;self._claim_serial=0;self._focus=False;self._input_at=-1e9;self._heartbeat_at=-1e9
        self._intent=dict(vx=0.,vy=0.,fire=False);self._angles=None;self._renew_at=0.;self._keepalive_at=0.
        self._input_revision=0;self._window=None;self._window_deadline=0.;self._window_sample_cut=0
        self.video=None;self.video_at=0.;self.video_sequence=-1;self.video_sim_time=None;self.frame_error='No v2 camera provider'
        self.video_native_size=None;self.video_source_sequence=None;self.max_frame_age=2.5
        self.last_error='V2 disconnected';self.note='V2 '+role+'; choose a watched robot'
        self._selection_busy=False;self.selection_pending=False;self.selection_status='';self.selection_target=None
    def start(self):
        if self._threads:return
        self._stop.clear()
        for name,target in [('v2-control',self._control_loop),('v2-state',self._state_loop),('v2-camera',self._camera_loop)]:
            thread=threading.Thread(name=name,target=target,daemon=True);self._threads.append(thread);thread.start()
    def stop(self):
        self.neutral('V2 disconnected');self._stop.set()
        with self._lock:
            self._connection_generation+=1;self._connected=False;self._watch_generation+=1;self._generation+=1
            self.session=None;self.raw_state=None;self.telemetry=None;self.telemetry_at=0.;self.robot_ids=()
            self._watch_job=None;self.watch_epoch=None;self.selection_pending=False
            self._clear_frame('Stopped',reset_identity=True);self.last_error='V2 disconnected'
        for thread in self._threads:thread.join(timeout=.5)
        self._threads=[]
        try:self.client.close()
        except Exception:pass
    def connect_once(self):
        with self._lock:generation=self._connection_generation
        result=self.client.open(self.role)
        with self._lock:
            if self._stop.is_set() or generation!=self._connection_generation:
                self.client.uncertain=True;return result
            self.session=dict(result,robot_id=self.watch_robot,capabilities={});self._generation+=1;self._connection_generation+=1;self._connected=True;self.last_error=''
        return result
    def neutral(self,reason='Input released'):
        with self._lock:
            self._control_generation+=1;self._window=None;self._window_deadline=0.;self._window_sample_cut=self._input_revision
            self._focus=False;self._intent=dict(vx=0.,vy=0.,fire=False);self._angles=None;self._input_at=-1e9
            self._claim_job=None;self.claim_pending=False
            if self.owned_robot is not None:self._release_job=(self.owned_robot,dict(self.owned_epochs))
            self.owned_robot=None;self.owned_epochs={};self.note=reason
    def watch(self,rid):
        with self._lock:
            self.neutral('Watching '+str(rid)+'; no control requested')
            if rid is not None and rid not in self.robot_ids:raise ValueError('Robot is not in server registry')
            self._watch_generation+=1;self._generation+=1;self.watch_robot=rid;self.watch_epoch=None
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
            self._focus=True;self._heartbeat_at=self.clock();self._input_at=self.clock();self._intent=dict(vx=0.,vy=0.,fire=False)
            return True
    def heartbeat(self,allowed):
        with self._lock:
            now=self.clock()
            if self._focus and (not allowed or now-self._heartbeat_at>.15):self.neutral('Input heartbeat/focus lost; reclaim then F2')
            self._heartbeat_at=now
    def submit_input(self,*,vx_mps,vy_mps,yaw_rate_rps=0.,pitch_rate_rps=0.,fire=False):
        with self._lock:
            if not self._focus:return
            vx=finite(vx_mps,'vx',-1,1);vy=finite(vy_mps,'vy',-1,1);norm=max(1.,math.hypot(vx,vy))
            if type(fire) is not bool:raise ValueError('Invalid fire')
            self._intent=dict(vx=vx/norm,vy=vy/norm,fire=fire);self._input_at=self.clock();self._input_revision+=1
    def submit_mouse(self,yaw_delta_rad,pitch_delta_rad):
        with self._lock:
            if not self._focus or self._angles is None:return
            yaw=finite(yaw_delta_rad,'mouse yaw',-1,1);pitch=finite(pitch_delta_rad,'mouse pitch',-1,1)
            self._angles=[max(-3.14,min(3.14,self._angles[0]+yaw)),max(-.4,min(.5,self._angles[1]+pitch))]
    def submit_modes(self,*args):return None
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
            self.raw_state=copy.deepcopy(data);self.robot_ids=tuple(r['robot_id'] for r in mapped)
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
            except ClientError:pass
        if watch:
            rid,generation=watch;result=self.client.watch(rid)
            with self._lock:
                if generation==self._watch_generation:
                    self.watch_epoch=result['watch_epoch'];self.selection_pending=False
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
            generation=self._control_generation
            strict=getattr(self.client,'control_profile','legacy_v2')=='server_window_v1'
            need_window=strict and (self._window is None or now>=self._window_deadline-.05)
        if renew:self.client.renew(rid,list(CHANNELS));self._renew_at=now
        if not active:return
        if need_window:
            # Runs only on the control worker, never the GUI thread. Anchor to
            # request start; server monotonic timestamps are never mapped locally.
            with self._lock:
                if generation!=self._control_generation or not self._focus or rid!=self.owned_robot or epochs!=self.owned_epochs:return
                started=self.clock()
            window=self.client.control_window(rid,list(CHANNELS))
            with self._lock:
                if generation!=self._control_generation or not self._focus or rid!=self.owned_robot or epochs!=self.owned_epochs:return
                deadline=started+window['valid_for_s']
                if self.clock()>=deadline:
                    self.neutral('Input window arrived too late; explicit reclaim required');return
                self._window=window;self._window_deadline=deadline;self._window_sample_cut=self._input_revision
            return  # Wait for an ordinary Qt input sample produced after ACK.
        with self._lock:
            if generation!=self._control_generation or not self._focus:return
            if strict and (self._window is None or self._input_revision<=self._window_sample_cut):return
            intent=dict(self._intent);angles=list(self._angles) if self._angles else None
            window_id=self._window['window_id'] if strict else None
        values=dict(motion=dict(frame='base_link',vx_mps=intent['vx'],vy_mps=intent['vy'],wz_rps=0.),gimbal=dict(frame='base_link',yaw_rad=angles[0],pitch_rad=angles[1]),fire=dict(fire=intent['fire']))
        for channel in CHANNELS:
            with self._lock:
                if generation!=self._control_generation or not self._focus or rid!=self.owned_robot or epochs!=self.owned_epochs:return
                if self.clock()-self._input_at>.15 or (strict and self.clock()>=self._window_deadline):
                    self.neutral('Input sample/window expired before send');return
            options={'window_id':window_id} if strict else {}
            self.client.control(rid,channel,values[channel],valid_for_s=.15,**options)
    def _failed(self,error):
        with self._lock:
            self.neutral('V2 connection error; restart to reconnect');self.client.uncertain=True
            self._connected=False;self._connection_generation+=1;self._watch_generation+=1;self._generation+=1
            self.raw_state=None;self.telemetry=None;self.telemetry_at=0.;self.robot_ids=();self.watch_epoch=None;self._watch_job=None;self.selection_pending=False
            self._clear_frame(str(error),reset_identity=True);self.last_error=str(error)
    def _control_loop(self):
        try:self.connect_once()
        except Exception as exc:self._failed(exc);return
        while not self._stop.is_set():
            try:self.control_tick()
            except Exception as exc:self._failed(exc)
            self._stop.wait(.02)
    def _state_loop(self):
        while not self._stop.is_set():
            with self._lock:ready=self._connected and not self.client.uncertain;generation=self._connection_generation
            if ready:
                try:self.install_state(self.client.state(),connection_generation=generation)
                except Exception as exc:
                    with self._lock:
                        if generation==self._connection_generation:self._failed(exc)
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
                        if generation==self._watch_generation:self._clear_frame(str(exc));self.neutral('Camera error; explicit reclaim required')
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
