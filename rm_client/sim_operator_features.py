"""Presentation/interaction truth gates for the basic simulator capability set.

This policy is installed only on a simulation registry. It neither renames nor
removes modules, and cannot alter the normal device registry or its settings.
A feature is enabled in a later milestone only after both command binding and
an authoritative simulator consumer/feedback exist.
"""
from __future__ import annotations
import copy

UNAVAILABLE = {
    'SpinMode':'No simulator chassis-spin consumer yet',
    'AutoSpinOnHit':'No authoritative hit-event/spin bridge yet',
    'ChassisAssist':'Only chassis-body translation is supported',
    'PowerGuard':'No simulator power/buffer model',
    'ArmorAutoAim':'No selected-vehicle automatic aiming pipeline yet',
    'ArmorLock':'No selected-target lock feedback',
    'EnergyAutoAim':'No rune aiming pipeline',
    'HeroLob':'No hero deployment/ballistic command consumer',
    'FireControlStub':'No simulator firing-mode/RPM consumer',
    'FrictionWheel':'No observed or controllable flywheel model',
    'LockState':'No observed lock state',
    'BuffStatus':'No observed buff state',
    'TongjiVisionBridge':'No selected-vehicle vision bridge',
    'CameraCalibration':'Simulation camera calibration capture is not wired',
    'Armor3DESP':'No selected-vehicle detections',
    'RobotOverlay':'No selected-vehicle detections',
    'RadarOverlay':'No radar feed; map truth is display only',
    'CombatTargetHUD':'No selected-target observations',
    'MapMarker':'No simulator map-command consumer',
    'SentinelCommand':'No simulator command consumer',
    'AirSupport':'No simulator command consumer',
    'EconomyPanel':'No simulator purchasing-command consumer',
    'ProjectileShop':'No simulator purchasing-command consumer',
    'BaseOutpostStatus':'No base/outpost observations',
    'ThermalPowerPanel':'Heat is in the SIM HUD; power is unavailable',
    'BuffTimeline':'No authoritative buff events',
    'GimbalPower':'No gimbal power observation',
    'PowerLimit':'No simulator power model',
    'SpeedLimit':'No original speed-limit command consumer',
    'Capacitor':'No capacitor observation',
    'JumpPTEWheelLES':'No corresponding device observations',
    'InjuryStatistics':'Detailed injury events are not mapped',
    'MatchSettlement':'Full settlement events are not mapped',
    'DartPanel':'No simulator dart consumer',
    'SkillConfiguration':'No simulator skill consumer',
    'ReloadingPanel':'No simulator reloading consumer',
    'RuneRingStatus':'No rune observations',
    'ResourceIsland':'No resource-island observations',
    'GainZone':'No gain-zone observations',
    'UWBStatus':'No UWB sensor feed',
    'ArmorStatus':'No armor-module observations',
    'WirelessCharging':'No charging observations',
    'MinecraftBuffs':'No authoritative buff observations',
    'MQTTLink':'Physical transport unavailable in SIM',
    'UDPVideo':'Physical transport unavailable in SIM',
    'SerialBridge':'Physical transport unavailable in SIM',
    'MatchReadiness':'Hardware qualification unavailable in SIM',
    'MatchChat':'No simulator chat/message delivery channel',
    'RobotLink':'Physical links are unavailable; movement key bindings remain editable',
    'EmergencyStop':'Use F12 for the actual virtual emergency stop',
}
# Keep these module rows, but lock operational settings to the actual session.
FIXED_SETTINGS = {'Scenario','MatchMode','RobotProfile','AllianceProfile','CameraFeed'}


class SimFeaturePolicy:
    def __init__(self,registry,notify=lambda message:None):
        self.registry=registry;self.notify=notify;self.locked_values={};self.unavailable_names=frozenset(UNAVAILABLE)
        self._original_toggle=registry.toggle_module
        self.supported={};self.original_descriptions={m.name:m.description for m in registry.modules}
        for module in registry.modules:
            if module.name in UNAVAILABLE:
                module.enabled=False
                module.description='SIM unavailable: '+UNAVAILABLE[module.name]
                previous_hook=module._enabled_change_hook
                def hook(changed,enabled,original=previous_hook):
                    if enabled and self.reason(changed.name):object.__setattr__(changed,'enabled',False)
                    if original:original(changed,bool(changed.enabled))
                module._enabled_change_hook=hook
            if module.name in UNAVAILABLE or module.name in FIXED_SETTINGS:
                for setting in module.settings:
                    if module.name=='RobotLink' and setting.name.startswith('Bind '):continue
                    self.locked_values[id(setting)]=copy.deepcopy(setting.value)
        registry.toggle_module=self.toggle

    def reason(self,name):return '' if name in self.supported else UNAVAILABLE.get(name,'')
    def set_supported(self,name,enabled,settings):
        module=self.registry.get(name)
        if enabled:
            self.supported[name]=set(settings)
            module.description=self.original_descriptions[name]+' (SIM request; status confirmed by simulator)'
        else:
            self.supported.pop(name,None);module.enabled=False
            module.description='SIM unavailable: '+UNAVAILABLE[name]
        self.unavailable_names=frozenset(n for n in UNAVAILABLE if self.reason(n))
    def toggle(self,module,source='internal'):
        target=self.registry.get(module) if isinstance(module,str) else module
        if target is not None and self.reason(target.name):
            self.notify('SIM unavailable: '+target.name+' — '+self.reason(target.name));return target
        return self._original_toggle(module,source)
    def owner(self,setting):
        return next((m for m in self.registry.modules if any(s is setting for s in m.settings)),None)
    def reject_setting(self,setting):
        module=self.owner(setting)
        if module is not None and setting.name in self.supported.get(module.name,set()):return False
        blocked=(id(setting) in self.locked_values or
                 (module is not None and module.name=='RobotLink' and not setting.name.startswith('Bind ')))
        if not blocked:return False
        if id(setting) in self.locked_values:setting.value=copy.deepcopy(self.locked_values[id(setting)])
        self.notify('SIM unavailable: '+(module.name if module else setting.name)+' setting is not a simulator command')
        return True
    def restore_locked_settings(self):
        for module in self.registry.modules:
            for setting in module.settings:
                if id(setting) in self.locked_values and setting.name not in self.supported.get(module.name,set()):setting.value=copy.deepcopy(self.locked_values[id(setting)])

    def sync_identity(self,state):
        pairs=[('RobotProfile','Role',state.robot_profile),('AllianceProfile','Side',state.alliance_color),('MatchMode','Alliance',state.alliance_color)]
        for module_name,name,value in pairs:
            module=self.registry.get(module_name);setting=module.get_setting(name) if module else None
            if setting is not None:setting.value=value;self.locked_values[id(setting)]=copy.deepcopy(value)
