"""SIM-only original hit-spin policy with explicit, one-use request transitions.

The existing ProjectileHitSpinController owns duration/cooldown/manual override.
A private synchronous transition context, never a caller-provided source string,
permits only its bounded spin boolean transition inside the current armed lease.
"""
from __future__ import annotations
import math
from .auto_spin import ProjectileHitSpinController
from .sim_projectile_damage import ProjectileDamageConsumer,supports_damage_observation

SUPPORTED_SETTINGS={'Minimum Damage','Duration','Cooldown','Auto Disable'}
FIXED_SETTINGS={'Only During Match':True,'Require Referee Link':True,'Power Guard':False}

class SimHitSpinPolicy:
    def __init__(self,runtime):
        self.runtime=runtime;self.window=None;self.supported=False
        self.consumer=ProjectileDamageConsumer();self.guard=ProjectileHitSpinController()
        self._auto_selected=False;self._transition=None;self._token=None;self._settings_signature=None
        self.reason='unavailable'

    @property
    def origin(self):return 'on_hit' if self._auto_selected else 'manual'

    def configure(self,window):
        self.window=window
        enabled=bool(self.runtime.modes.support[0] and supports_damage_observation(self.runtime.transport.session))
        if enabled==self.supported:return
        self.supported=enabled
        module=window.registry.get('AutoSpinOnHit');policy=self.runtime.feature_policy
        for name,value in FIXED_SETTINGS.items():
            setting=module.get_setting(name)
            setting.value=value;policy.locked_values[id(setting)]=value
        policy.set_supported('AutoSpinOnHit',enabled,SUPPORTED_SETTINGS)
        self.runtime.disarm('SIM hit-spin capability changed: press F2 to resume')
        window.control.reset_inputs();window.invalidate_gui_cache()

    def reset(self):
        # Automatic request state must not resume from an old hit on a later F2.
        if self._auto_selected and self.window is not None:
            self.window.registry.get('SpinMode').enabled=False
        self.consumer.reset();self.guard.reset();self._auto_selected=False
        self._transition=None;self._token=None;self.reason='disarmed'

    def operator_override(self):
        # Preserve the newly selected manual state; only relinquish automation.
        self._auto_selected=False;self._token=None;self._transition=None
        self.consumer.reset();self.guard.reset();self.reason='operator_override'

    def in_transition(self,module=None,enabled=None):
        current=self._transition
        return bool(current is not None and (module is None or current[0] is module)
                    and (enabled is None or current[1] is enabled))

    def consume_transition(self,old_mode,new_mode,old_aim,new_aim):
        token=self._token;self._token=None;t=self.runtime.transport
        if token is None:return False
        old,new,identity,generation,lease,deadline=token
        session=t.session or {}
        current=tuple(session.get(k) for k in ('session_id','epoch','robot_id'))
        return bool(self.runtime.armed and t._focus and t.clock()<=deadline
                    and identity==current and generation==t._generation and lease==t._lease_min_seq
                    and old_mode==old and new_mode==new and old_aim==new_aim)

    def _toggle(self,window,enabled):
        spin=window.registry.get('SpinMode');t=self.runtime.transport
        if spin.enabled is enabled:return
        t.mark_mode_changed()
        old=dict(self.runtime.modes.mode)
        self._auto_selected=enabled
        new=dict(old,spin_enabled=enabled,spin_origin=self.origin)
        session=t.session or {}
        self._token=(old,new,tuple(session.get(k) for k in ('session_id','epoch','robot_id')),
                     t._generation,t._lease_min_seq,t.clock()+.15)
        self._transition=(spin,enabled)
        try:
            window.registry.toggle_module(spin,source='auto_hit_sim' if enabled else 'auto_hit_release_sim')
        finally:self._transition=None
        if spin.enabled is not enabled:
            self.reset();self.runtime.disarm('SIM hit-spin request was not applied')

    def update(self,window):
        self.window=window
        if not self.supported:return
        module=window.registry.get('AutoSpinOnHit');spin=window.registry.get('SpinMode')
        try:
            for name,low,high in (('Minimum Damage',1,500),('Duration',.2,8),('Cooldown',0,10)):
                value=module.get_setting(name).value
                if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
                    raise ValueError('Invalid hit-spin setting')
            if type(module.get_setting('Auto Disable').value) is not bool:
                raise ValueError('Invalid hit-spin Auto Disable')
            for name,wanted in FIXED_SETTINGS.items():
                if module.get_setting(name).value is not wanted:
                    raise ValueError('Unsupported hit-spin safety condition')
        except (ValueError,TypeError,OverflowError):
            self.runtime.disarm('Invalid SIM hit-spin settings');window.control.reset_inputs();return
        signature=(module.enabled,tuple((s.name,s.value) for s in module.settings))
        if self._settings_signature is not None and signature!=self._settings_signature:
            self._settings_signature=signature
            self.runtime.disarm('SIM hit-spin settings changed: press F2 to resume')
            window.control.reset_inputs();return
        self._settings_signature=signature
        telemetry,video,*_=self.runtime.transport.snapshot();t=self.runtime.transport
        live=bool(module.enabled and self.runtime.armed and t._focus and not t._neutral_pending
                  and telemetry is not None and video is not None and telemetry.get('phase')=='running'
                  and telemetry['own']['alive'] and window.isActiveWindow() and window.combat_control_input_allowed())
        if not live:
            self.reset();return
        try:
            delta=self.consumer.observe(telemetry.get('own_damage'),t.session,telemetry['sim_time'],live=True)
        except (ValueError,TypeError,KeyError):
            self.runtime.disarm('Invalid authoritative SIM projectile damage');window.control.reset_inputs();return
        if delta.reason=='identity_changed':
            self.runtime.disarm('SIM judge episode changed: press F2 to resume')
            window.control.reset_inputs();return
        if delta.reason=='baseline':self.guard.reset(delta.revision)
        # This is a dedicated verified SIM predicate. No official/MQTT online
        # field or power/buffer value is invented to satisfy the original guard.
        window.state.projectile_hit_revision=delta.revision
        window.state.last_projectile_damage_delta=delta.damage
        directive=self.guard.update(window.state,module,spin_enabled=bool(spin.enabled),referee_link=True,now=telemetry['sim_time'])
        self.reason=directive.reason or self.guard.last_reason
        if directive.changed:self._toggle(window,directive.action=='enable')
