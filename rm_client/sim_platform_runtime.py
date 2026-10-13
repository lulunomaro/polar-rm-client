"""Opt-in v2 bundle reusing the original hardware-free operator UI seams."""
from __future__ import annotations
from .sim_operator_runtime import OperatorRuntime,IDS
from .sim_platform_transport import PlatformTransport
from .sim_robot_selection import SELECTION_SETTING
from .sim_platform_adapters import SETTINGS,MANUAL,options,candidate

class NoPlatformModes:
    support=(False,False);drive_support=False;signature=None;window=None
    def sync(self,window):self.window=window
    def submit(self,*args):return None
    def feedback(self):return dict(spin=False,aim=False,drive=None,spin_reason='V2 UI binding unavailable',aim_reason='V2 UI binding unavailable',drive_reason='body reference only',measured_yaw_rate=None,spin_origin=None)

class PlatformRuntime(OperatorRuntime):
    def __init__(self,url='http://127.0.0.1:8766',*,role='spectator',transport=None):
        super().__init__(transport=transport or PlatformTransport(url,role=role))
        from .sim_platform_spin import PlatformModes
        self.modes=PlatformModes(self)
        from .sim_platform_status import SpinStatusPresenter
        self.spin_status=SpinStatusPresenter(self);self.note='V2 '+self.transport.role+'; watch does not request control'
    def create_control(self):
        from .sim_platform_input import PlatformInputBridge
        return PlatformInputBridge(self)
    def registry(self):
        registry=super().registry();setting=registry.get('RobotProfile').get_setting(SELECTION_SETTING)
        setting.description='V2 read-only watched robot. Switching releases old controls, does not claim the new vehicle. Operator: F2 claim, then F2 arm. Spectator: no input.'
        from .model import Setting
        module=registry.get('RobotProfile')
        module.settings.extend([
            Setting('V2 Navigation','mode',MANUAL,options=[MANUAL],description='Explicit motion owner. Choosing releases control; F2 claim then F2 bind/arm.'),
            Setting('V2 Aim','mode',MANUAL,options=[MANUAL],description='Explicit gimbal owner only. Fire is always manual. Native availability is not realtime qualification.'),
            Setting('V2 Goal X','number',0.,min_value=-100,max_value=100,step=.1,unit='m',description='Operator-entered world X goal, no enemy/map-truth target injection.'),
            Setting('V2 Goal Y','number',0.,min_value=-100,max_value=100,step=.1,unit='m',description='Operator-entered world Y goal; blue team uses the same world axes.'),
            Setting('V2 Aim FOV','number',85.,min_value=10,max_value=180,step=1,unit='deg',description='Native wrapper FOV request; no auto fire.'),
            Setting('V2 Aim Radius X','number',.25,min_value=.001,max_value=2,step=.001,description='Native source-frame normalized ellipse X radius.'),
            Setting('V2 Aim Radius Y','number',.25,min_value=.001,max_value=2,step=.001,description='Native source-frame normalized ellipse Y radius.')])
        return registry
    def sync_adapters(self,window):
        t=self.transport;module=window.registry.get('RobotProfile');changed=False
        for kind,name in [('navigation','V2 Navigation'),('aim','V2 Aim')]:
            setting=module.get_setting(name);allowed=options(t.catalog,t.watch_robot,kind)
            selected=t.plan.get(kind,{}).get('adapter_id',MANUAL)
            unavailable=[aid+': N/A '+candidate(t.catalog,t.watch_robot,kind,aid)[1]
                for aid,d in t.catalog.get('adapters',{}).items() if d['kind']==kind and not candidate(t.catalog,t.watch_robot,kind,aid)[0]]
            description='Registered choices only; execution waits for valid sensors/results. '+ '; '.join(unavailable)
            if setting.options!=allowed or setting.value!=selected or setting.description!=description:
                setting.options=allowed;setting.value=selected;setting.description=description;changed=True
        if changed:window.invalidate_gui_cache()
    def configure_adapter_settings(self,window):
        module=window.registry.get('RobotProfile')
        choices={k:module.get_setting(n).value for k,n in [('navigation','V2 Navigation'),('aim','V2 Aim')]}
        values={n:module.get_setting(n).value for n in SETTINGS[2:]}
        self.disarm('Adapter choice/config changed; claim then F2 to bind')
        window.control.reset_inputs()
        self.transport.configure_adapters(choices,values);self.sync_adapters(window)
    def sync_selection(self,window):
        setting=window.registry.get('RobotProfile').get_setting(SELECTION_SETTING)
        options=list(self.transport.robot_ids);value=self.transport.watch_robot or 'not watching'
        if setting.options!=options or setting.value!=value:setting.options=options;setting.value=value;window.invalidate_gui_cache()
        return self.transport.selection_ready()
    def select_robot(self,window,robot_id):
        self.disarm('Changing watched robot; no new control request');window.control.reset_inputs()
        self.transport.watch(robot_id);window.frame_image=None;window.frame_pixmap=None;window.invalidate_gui_cache()
    def arm(self):
        if self.modes.window is not None and not self.modes.sync(self.modes.window):return False
        t=self.transport
        if t.role!='operator':self.note='V2 spectator: watching only, movement and fire unavailable';return False
        if self._generation!=t._generation:
            self._generation=t._generation;self.disarm('New watch/session; fresh F2 required')
        data,video,*_=t.snapshot()
        if data is None or video is None or not t.selection_ready():self.note='Need current watched state and camera before requesting control';return False
        if data['robot_id'] not in IDS or data['phase']!='running' or not data['own']['alive']:
            self.note='Watched robot/profile is not eligible for manual control';return False
        if t.owned_robot!=t.watch_robot:
            try:t.request_claim();self.note='Claim requested; input remains disarmed. After acknowledgement press F2 again.'
            except (ValueError,RuntimeError) as exc:self.note=str(exc)
            return False
        if self.backend and self.backend.safety.estop_active:self.backend.safety.estop_active=False
        self.armed=t.activate();self.note=('V2 adapter plan authorized; waiting for bind/sensor feedback' if t.plan else 'V2 manual operator: WASD / absolute mouse target / left click') if self.armed else 'Lease/camera not ready'
        return self.armed
    def heartbeat(self,window):
        self.modes.sync(window);self.sync_selection(window);self.sync_adapters(window)
        if self._generation!=self.transport._generation:
            self._generation=self.transport._generation;self.disarm('V2 session/watch changed; explicit claim and F2 required');window.control.reset_inputs()
        data,video,*_=self.transport.snapshot()
        allowed=bool(self.armed and self.transport.role=='operator' and not self.transport.client.uncertain
            and window.isActiveWindow() and window.combat_control_input_allowed()
            and data is not None and video is not None and data['own']['alive'] and data['phase']=='running')
        if self.armed and not allowed:self.disarm('V2 focus/state/camera lost; controls released');window.control.reset_inputs()
        self.transport.heartbeat(allowed)
        if self.armed and not self.transport._focus:self.armed=False;self.note=self.transport.note;window.control.reset_inputs()
        elif not self.armed and self.transport.owned_robot:self.note=self.transport.note
    def display_identity(self):
        # A mapped model is only authoritative for this exact local receipt.
        t=self.transport
        with t._lock:
            data=t.snapshot()[0]
            if data is None or t.raw_state is None:return None
            from .sim_platform_spin import number
            try:
                integers=(t._connection_generation,t._watch_generation,t.watch_epoch,t.raw_state.get('episode'))
                if any(type(v) is not int or v<0 for v in integers):return None
                if type(t.client.session_id) is not str or not t.client.session_id:return None
                if type(t.watch_robot) is not str or data.get('robot_id')!=t.watch_robot:return None
                now=number(t.clock(),0,1e15);stamp=number(t.raw_state_at,0,now)
                if now-stamp>.5:return None
                sim=number(t.raw_state.get('sim_time'),0,1e15)
                return (integers[0],integers[1],t.client.session_id,integers[2],
                    t.watch_robot,integers[3],sim,stamp,data['robot_id'])
            except (ValueError,TypeError,KeyError):return None
    def apply_state(self,state):
        with self.transport._lock:
            state._platform_display_identity=None
            self._apply_state_locked(state)
            identity=self.display_identity()
            if identity is None:state.mujoco_telemetry_valid=False;state.mujoco_partial=True
            if state.mujoco_telemetry_valid:state._platform_display_identity=identity
    def _apply_state_locked(self,state):
        data=self.transport.snapshot()[0]
        # Reuse the original presentation mapping only for its actual known roster.
        if data is not None and data['robot_id'] not in IDS:
            state.mujoco_telemetry_valid=False;state.mujoco_partial=True;return
        super().apply_state(state)
        required=('hp','max_hp','ammo','heat','heat_max')
        complete=bool(state.mujoco_telemetry_valid and data and all(data['own'].get(k) is not None for k in required)
            and data['own']['max_hp']>0 and data['own']['heat_max']>0
            and data.get('remaining_seconds') is not None and all(data.get('vp',{}).get(k) is not None for k in ('red','blue')))
        state.mujoco_telemetry_valid=complete;state.mujoco_partial=bool(data and not complete)
        state.mujoco_session_role=self.transport.role;state.match_title='MuJoCo platform v2 / '+self.transport.role
        if not complete:
            missing=tuple(k for k in required if data is None or data['own'].get(k) is None)
            state.mujoco_unknown_fields=tuple(state.mujoco_unknown_fields)+missing
