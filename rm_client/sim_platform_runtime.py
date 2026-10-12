"""Opt-in v2 bundle reusing the original hardware-free operator UI seams."""
from __future__ import annotations
from .sim_operator_runtime import OperatorRuntime,IDS
from .sim_platform_transport import PlatformTransport
from .sim_robot_selection import SELECTION_SETTING

class NoPlatformModes:
    support=(False,False);drive_support=False;signature=None;window=None
    def sync(self,window):self.window=window
    def submit(self,*args):return None
    def feedback(self):return dict(spin=False,aim=False,drive=None,spin_reason='V2 UI binding unavailable',aim_reason='V2 UI binding unavailable',drive_reason='body reference only',measured_yaw_rate=None,spin_origin=None)

class PlatformRuntime(OperatorRuntime):
    def __init__(self,url='http://127.0.0.1:8766',*,role='spectator',transport=None):
        super().__init__(transport=transport or PlatformTransport(url,role=role))
        self.modes=NoPlatformModes();self.note='V2 '+self.transport.role+'; watch does not request control'
    def registry(self):
        registry=super().registry();setting=registry.get('RobotProfile').get_setting(SELECTION_SETTING)
        setting.description='V2 read-only watched robot. Switching releases old controls, does not claim the new vehicle. Operator: F2 claim, then F2 arm. Spectator: no input.'
        return registry
    def sync_selection(self,window):
        setting=window.registry.get('RobotProfile').get_setting(SELECTION_SETTING)
        options=list(self.transport.robot_ids);value=self.transport.watch_robot or 'not watching'
        if setting.options!=options or setting.value!=value:setting.options=options;setting.value=value;window.invalidate_gui_cache()
        return self.transport.selection_ready()
    def select_robot(self,window,robot_id):
        self.disarm('Changing watched robot; no new control request');window.control.reset_inputs()
        self.transport.watch(robot_id);window.frame_image=None;window.frame_pixmap=None;window.invalidate_gui_cache()
    def arm(self):
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
        self.armed=t.activate();self.note='V2 manual operator: WASD / absolute mouse target / left click' if self.armed else 'Lease/camera not ready'
        return self.armed
    def heartbeat(self,window):
        self.modes.sync(window);self.sync_selection(window)
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
    def apply_state(self,state):
        data=self.transport.snapshot()[0]
        # Reuse the original presentation mapping only for its actual known roster.
        if data is not None and data['robot_id'] not in IDS:
            state.mujoco_telemetry_valid=False;state.mujoco_partial=True;return
        super().apply_state(state)
        required=('hp','max_hp','ammo','heat','heat_max')
        complete=bool(data and all(data['own'].get(k) is not None for k in required)
            and data['own']['max_hp']>0 and data['own']['heat_max']>0
            and data.get('remaining_seconds') is not None and all(data.get('vp',{}).get(k) is not None for k in ('red','blue')))
        state.mujoco_telemetry_valid=complete;state.mujoco_partial=bool(data and not complete)
        state.mujoco_session_role=self.transport.role;state.match_title='MuJoCo platform v2 / '+self.transport.role
        if not complete:
            missing=tuple(k for k in required if data is None or data['own'].get(k) is None)
            state.mujoco_unknown_fields=tuple(state.mujoco_unknown_fields)+missing
