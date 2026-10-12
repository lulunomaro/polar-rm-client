"""Only the explicit loopback simulator protocol. No hardware transports.

Control heartbeat, telemetry and camera requests have independent threads.
Latest-value channels are bounded; UI stalls/focus loss expire input locally,
and the simulator independently enforces its watchdog.
"""
from __future__ import annotations
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from .sim_operator_modes import spin_request,aim_request,SPIN_DEFAULT,AIM_DEFAULT,validate_feedback,supported_modes,source_camera_metadata

SCHEMA = 'polar.sim.operator.v1'
MAX_JSON = 2_000_000
MAX_JPEG = 4_000_000


def finite(value, label):
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise ValueError(f'{label} must be finite')
    try:
        number=float(value)
    except (OverflowError,ValueError):
        raise ValueError(f'{label} must be finite') from None
    if not math.isfinite(number):raise ValueError(f'{label} must be finite')
    return number


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Simulator redirects are forbidden')


class SimOperatorTransport:
    def __init__(self, url='http://127.0.0.1:8768', *, clock=time.monotonic):
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise ValueError('Only an explicit HTTP 127.0.0.1 simulator endpoint is supported')
        if parsed.port is None or not 1024<=parsed.port<=65535:raise ValueError('Explicit nonprivileged simulator port required')
        self.url=f'http://127.0.0.1:{parsed.port}'
        self.clock=clock;self._lock=threading.RLock();self._stop=threading.Event()
        self._threads=[];self.session=None;self.telemetry=None;self.telemetry_at=0.0
        self.video_source_sequence=None;self.video_native_size=None
        self.video=None;self.video_at=0.0;self.video_sequence=-1;self.video_sim_time=None
        self._lease_min_seq=1;self._aim_hold_min_seq=1
        self._seq=0;self._offset_ms=0.0;self._input_at=-1e9;self._focus=False
        self._intent=dict(vx_mps=0.,vy_mps=0.,yaw_rate_rps=0.,pitch_rate_rps=0.,fire=False)
        self._mode_request=None;self._aim_request=None
        self.last_error='Simulator disconnected';self.frame_error='No first-person frame'
        self.last_ack=None;self.accepted_count=0;self.rejected_count=0;self._generation=0
        self._focused_since_session=False;self._mouse_pending=[0.,0.];self._last_control_at=None
        self._opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

    def request(self,path,body=None,*,timeout=.15,limit=MAX_JSON):
        data=None if body is None else json.dumps(body,allow_nan=False,separators=(',',':')).encode()
        req=urllib.request.Request(self.url+path,data=data,headers={'Content-Type':'application/json'} if data else {},method='POST' if data else 'GET')
        try:
            response=self._opener.open(req,timeout=timeout)
        except urllib.error.HTTPError as exc:
            if path!='/control' or exc.code!=409:raise
            response=exc
        with response:
            if response.status not in (200,409):raise ValueError('Simulator returned a non-success response')
            raw=response.read(limit+1)
            if len(raw)>limit:raise ValueError('Simulator response exceeds bounded size')
            return raw,dict(response.headers)

    def _json(self,path,body=None,**kwargs):
        raw,_=self.request(path,body,**kwargs)
        data=json.loads(raw)
        if not isinstance(data,dict):raise ValueError('Simulator JSON must be an object')
        return data

    def start(self):
        if self._threads:return
        self._stop.clear()
        for name,target in [('sim-control',self._control_loop),('sim-state',self._telemetry_loop),('sim-camera',self._video_loop)]:
            thread=threading.Thread(name=name,target=target,daemon=True);self._threads.append(thread);thread.start()

    def stop(self):
        self.neutral('client closing')
        try:
            with self._lock:body=self._control_payload()
            if body:self._json('/control',body,timeout=.10)
        except Exception:pass
        self._stop.set()
        for thread in self._threads:thread.join(.25)
        self._threads=[]
        with self._lock:self._clear_session('Simulator disconnected')

    def _clear_session(self,reason):
        self._mode_request=None;self._aim_request=None
        self.session=None;self.telemetry=None;self.video=None;self.video_at=0.;self.video_sequence=-1
        self.telemetry_at=0.;self.video_sim_time=None;self.video_source_sequence=None;self.video_native_size=None;self._generation+=1;self._focused_since_session=False
        self.neutral(reason);self.last_error=str(reason)

    def neutral(self,reason='input released'):
        with self._lock:
            self._intent=dict(vx_mps=0.,vy_mps=0.,yaw_rate_rps=0.,pitch_rate_rps=0.,fire=False)
            self._focus=False;self._input_at=-1e9;self._mouse_pending=[0.,0.]
            if self._aim_request:self._aim_request=dict(self._aim_request,hold=False)
            self.last_error=str(reason)

    def submit_modes(self,mode,aim):
        mode=None if mode is None else spin_request(mode)
        aim=None if aim is None else aim_request(aim)
        with self._lock:
            if aim and aim['hold'] and not (self._aim_request or {}).get('hold'):
                self._aim_hold_min_seq=self._seq+1
            self._mode_request=mode;self._aim_request=aim

    def _expire_input(self):
        if self._focus and self.clock()-self._input_at>.15:
            self.neutral('UI heartbeat expired: press F2 to resume')
            self._focused_since_session=False;self._generation+=1
            return True
        return False

    def heartbeat(self,focused):
        with self._lock:
            if self._expire_input():focused=False
            if focused and not self._focus:self._lease_min_seq=self._seq+1
            self._focus=bool(focused);self._input_at=self.clock()
            if focused and self.session is not None:self._focused_since_session=True
            if not focused:
                self._mouse_pending=[0.,0.]
                self._intent=dict(vx_mps=0.,vy_mps=0.,yaw_rate_rps=0.,pitch_rate_rps=0.,fire=False)

    def submit_input(self,*,vx_mps,vy_mps,yaw_rate_rps,pitch_rate_rps,fire):
        values=[finite(v,k) for v,k in [(vx_mps,'vx'),(vy_mps,'vy'),(yaw_rate_rps,'yaw'),(pitch_rate_rps,'pitch')]]
        if not isinstance(fire,bool):raise ValueError('fire must be bool')
        vx,vy,yaw,pitch=values;length=math.hypot(vx,vy)
        if length>1:vx/=length;vy/=length
        with self._lock:
            if not self._focus or not self._focused_since_session:return
            self._intent=dict(vx_mps=vx,vy_mps=vy,yaw_rate_rps=max(-2.,min(2.,yaw)),pitch_rate_rps=max(-2.,min(2.,pitch)),fire=fire)

    def submit_mouse(self,yaw_delta_rad,pitch_delta_rad):
        values=(finite(yaw_delta_rad,'yaw delta'),finite(pitch_delta_rad,'pitch delta'))
        with self._lock:
            if not self._focus or not self._focused_since_session:return
            # Bounded relative-motion accumulator, cleared on every release.
            # A 75 Hz UI zero tick must not erase motion before 50 Hz egress.
            self._mouse_pending=[max(-.1,min(.1,old+delta)) for old,delta in zip(self._mouse_pending,values)]

    def _control_payload(self):
        if self.session is None:return None
        self._expire_input()
        self._seq+=1
        fresh=self._focus and self._focused_since_session and self.clock()-self._input_at<=.15
        values=dict(self._intent) if fresh else dict(vx_mps=0.,vy_mps=0.,yaw_rate_rps=0.,pitch_rate_rps=0.,fire=False)
        now=self.clock();dt=.02 if self._last_control_at is None else max(.01,min(.1,now-self._last_control_at))
        self._last_control_at=now
        if fresh:
            for key,delta in zip(('yaw_rate_rps','pitch_rate_rps'),self._mouse_pending):
                values[key]=max(-2.,min(2.,values[key]+delta/dt))
        self._mouse_pending=[0.,0.]
        extensions={}
        if self._mode_request is not None:extensions['mode_request']=dict(self._mode_request,spin_enabled=bool(fresh and self._mode_request['spin_enabled']))
        if self._aim_request is not None:extensions['aim_request']=dict(self._aim_request,enabled=bool(fresh and self._aim_request['enabled']),hold=bool(fresh and self._aim_request['hold']))
        return dict(schema=SCHEMA,session_id=self.session['session_id'],epoch=self.session['epoch'],
                    robot_id=self.session['robot_id'],seq=self._seq,sent_at_ms=time.time()*1000+self._offset_ms,
                    focus=bool(fresh),deadman=bool(fresh),**values,**extensions)

    def install_session(self,data,*,request_midpoint_ms=None):
        if data.get('schema')!=SCHEMA or not isinstance(data.get('session_id'),str) or not data['session_id'] or len(data['session_id'])>100:
            raise ValueError('Unknown simulator session')
        if type(data.get('epoch')) is not int or data['epoch']<0:raise ValueError('Invalid epoch')
        if data.get('robot_id') not in {t+'_'+r for t in ('red','blue') for r in ('hero','infantry','sentry')}:raise ValueError('Unknown selected robot')
        server_time=finite(data['server_time_ms'],'server time')
        ttl=finite(data['ttl_ms'],'TTL')
        if ttl!=250:raise ValueError('Unsupported simulator watchdog')
        if data.get('simulation_only') is not True or data.get('hardware_transport') is not False:
            raise ValueError('Endpoint must explicitly declare simulation-only transport')
        if data.get('coordinate_frame')!='chassis_body':raise ValueError('Unsupported control coordinates')
        if data.get('first_person_camera')!=data['robot_id']+'__aim':raise ValueError('Selected first-person camera required')
        with self._lock:
            previous=(self.session or {}).get('session_id'),(self.session or {}).get('epoch'),(self.session or {}).get('robot_id')
            current=data['session_id'],data['epoch'],data['robot_id']
            if previous!=current:
                self._clear_session('New simulator session: input released')
            last_seq=data.get('last_seq',-1)
            if type(last_seq) is not int or not -1<=last_seq<2**53-1:raise ValueError('Invalid server sequence')
            self._seq=max(self._seq,last_seq)
            self.session=dict(data);self._offset_ms=server_time-(time.time()*1000 if request_midpoint_ms is None else request_midpoint_ms)

    def validate_identity(self,data,session):
        return data.get('schema')==SCHEMA and all(data.get(key)==session[key] for key in ('session_id','epoch','robot_id'))

    def install_telemetry(self,data,session):
        if not self.validate_identity(data,session):raise ValueError('Wrong telemetry session/robot')
        if data.get('simulation_only') is not True or data.get('hardware_transport') is not False:raise ValueError('Telemetry must be simulation only')
        sim_time=finite(data['sim_time'],'sim time')
        if sim_time<0:raise ValueError('Negative simulator time')
        own=data.get('own');robots=data.get('robots')
        if not isinstance(own,dict) or own.get('robot_id')!=session['robot_id'] or not isinstance(robots,list):raise ValueError('Invalid selected robot telemetry')
        if len(robots)>6:raise ValueError('Unsupported roster size')
        for key in ('hp','max_hp','ammo','heat','heat_max','chassis_yaw','gimbal_yaw','pitch'):
            finite(own.get(key),key)
        if own['max_hp']<=0 or own['heat_max']<=0:raise ValueError('Invalid selected robot limits')
        if type(own.get('alive')) is not bool:raise ValueError('Invalid alive state')
        if own.get('robot_id')!=own.get('team','')+'_'+own.get('role',''):raise ValueError('Wrong selected team/role')
        finite(data.get('remaining_seconds'),'remaining time')
        vp=data.get('vp',{})
        for team in ('red','blue'):finite(vp.get(team),'victory points')
        if data.get('phase') not in ('idle','running','finished','countdown'):raise ValueError('Unknown match phase')
        if data.get('map_source')!='simulator_truth_display_only':raise ValueError('Map provenance required')
        for robot in [own,*robots]:
            if not isinstance(robot,dict):raise ValueError('Invalid roster entry')
            if robot.get('robot_id')!=robot.get('team','')+'_'+robot.get('role',''):raise ValueError('Wrong robot ID')
            if robot.get('team') not in ('red','blue') or robot.get('role') not in ('hero','infantry','sentry'):raise ValueError('Unknown robot team/role')
            for key in ('hp','max_hp','ammo','heat','heat_max','chassis_yaw','gimbal_yaw','pitch'):
                if robot.get(key) is not None:finite(robot[key],key)
            xy=robot.get('xy')
            if xy is not None:
                if not isinstance(xy,list) or len(xy)!=2:raise ValueError('Invalid XY')
                for x in xy:finite(x,'xy')
        validate_feedback(data,session)
        with self._lock:
            if self.session is None or not self.validate_identity(data,self.session):return False
            if self.telemetry is not None and sim_time<self.telemetry['sim_time']:raise ValueError('Out-of-order telemetry')
            self.telemetry=dict(data);self.telemetry_at=self.clock();self.last_error=''
        return True

    def _control_loop(self):
        while not self._stop.is_set():
            try:
                with self._lock:body=self._control_payload();generation=self._generation
                if body is not None:
                    ack=self._json('/control',body,timeout=.1)
                    if ack.get('schema')!=SCHEMA or type(ack.get('accepted')) is not bool or any(ack.get(k)!=body[k] for k in ('session_id','epoch','robot_id','seq')):raise ValueError('Wrong control acknowledgement')
                    with self._lock:
                        if generation!=self._generation:continue
                        self.last_ack=ack
                        if ack.get('accepted') is True:self.accepted_count+=1
                        else:
                            self.rejected_count+=1
                            self._clear_session('Simulator rejected control: '+str(ack.get('reason','unknown')))
            except Exception as exc:
                with self._lock:
                    if generation==self._generation:self._clear_session(str(exc))
            self._stop.wait(.02)

    def _telemetry_loop(self):
        while not self._stop.is_set():
            try:
                with self._lock:session=self.session;generation=self._generation
                if session is None:
                    began=time.time()*1000
                    discovered=self._json('/session',timeout=.15)
                    ended=time.time()*1000
                    with self._lock:
                        if generation!=self._generation:continue
                        self.install_session(discovered,request_midpoint_ms=(began+ended)/2)
                        generation=self._generation
                    with self._lock:session=dict(self.session)
                data=self._json('/telemetry',timeout=.15)
                self.install_telemetry(data,session)
            except Exception as exc:
                with self._lock:
                    if generation==self._generation:self._clear_session(str(exc))
            self._stop.wait(.03 if self.session else .15)

    def _video_loop(self):
        while not self._stop.is_set():
            with self._lock:session=dict(self.session) if self.session else None;generation=self._generation
            if session is None:self._stop.wait(.1);continue
            try:
                raw,headers=self.request('/frame.jpg',timeout=.35,limit=MAX_JPEG)
                h={k.lower():v for k,v in headers.items()}
                if h.get('content-type','').split(';')[0]!='image/jpeg':raise ValueError('Expected first-person JPEG')
                ident=dict(schema=SCHEMA,session_id=h.get('x-sim-session-id'),epoch=int(h.get('x-sim-epoch','-1')),robot_id=h.get('x-robot-id'))
                index=int(h.get('x-frame-seq','-1'));stamp=finite(float(h.get('x-sim-time','nan')),'frame time')
                if not self.validate_identity(ident,session) or index<0 or stamp<0:raise ValueError('Wrong first-person frame identity')
                if h.get('x-camera-name')!=session['first_person_camera'] or h.get('x-camera-overlays')!='false':raise ValueError('Raw selected first-person camera required')
                width=int(h.get('x-frame-width','0'));height=int(h.get('x-frame-height','0'))
                if not 1<=width<=4096 or not 1<=height<=4096:raise ValueError('Invalid frame dimensions')
                source_index,native_size=source_camera_metadata(h,width,height,session)
                import cv2,numpy as np
                bgr=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
                if bgr is None or bgr.ndim!=3 or bgr.shape[:2]!=(height,width):raise ValueError('Invalid camera image')
                with self._lock:
                    if generation!=self._generation:continue
                    if self.video_sim_time is not None and stamp<self.video_sim_time:raise ValueError('Out-of-order camera time')
                    if self.session and self.validate_identity(ident,self.session) and index>self.video_sequence:
                        self.video_source_sequence=source_index;self.video_native_size=native_size
                        self.video=bgr;self.video_at=self.clock();self.video_sequence=index;self.video_sim_time=stamp;self.frame_error=''
            except Exception as exc:
                with self._lock:
                    if generation==self._generation:
                        self.video=None;self.video_at=0.;self.frame_error=str(exc)
                        self.neutral('Camera invalid: press F2 to resume')
                        self._focused_since_session=False;self._generation+=1
            self._stop.wait(.05)

    def snapshot(self):
        with self._lock:
            now=self.clock();telemetry=self.telemetry if now-self.telemetry_at<=.5 else None
            video=self.video if now-self.video_at<=1.5 else None
            return telemetry,video,self.video_at if video is not None else 0.,self.video_sequence,self.video_sim_time
