"""Original POLAR window, explicit platform-v2 opt-in; v1 entry stays unchanged."""
from __future__ import annotations
import argparse
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QColor,QFont,QFontMetricsF,QPainter,QSurfaceFormat
from PySide6.QtWidgets import QApplication
from .polar_app import PolarRMWindow
from .sim_operator_window import OperatorWindow
from ..sim_platform_runtime import PlatformRuntime
from ..sim_robot_selection import SELECTION_SETTING
from ..sim_platform_adapters import SETTINGS,options
from ..sim_platform_status import status_lines,status_layout
from .sim_platform_status_layout import reserved_regions

class PlatformWindow(OperatorWindow):
    def __init__(self,runtime):
        super().__init__(runtime);self.setWindowTitle('POLAR-RM · SIM PLATFORM V2 · '+runtime.transport.role.upper())
    def setting_display_label(self,setting,label):
        if setting.name==SELECTION_SETTING:return 'V2 观看车辆'
        return super().setting_display_label(setting,label)
    def setting_is_unavailable(self,setting):
        if setting.name==SELECTION_SETTING:return not self._simulation_runtime.transport.robot_ids
        if setting.name in SETTINGS:
            t=self._simulation_runtime.transport
            if t.role!='operator':return True
            if setting.name in SETTINGS[:2]:return not t.catalog.get('ready')
            if setting.name in SETTINGS[2:4]:return self.registry.get('RobotProfile').get_setting('V2 Navigation').value!='nav_rule_v1'
            return self.registry.get('RobotProfile').get_setting('V2 Aim').value!='aim_native_rmcs_v1'
        return super().setting_is_unavailable(setting)
    def after_setting_changed(self,setting):
        if setting.name in SETTINGS:
            try:self._simulation_runtime.configure_adapter_settings(self)
            except (ValueError,RuntimeError) as exc:
                self._simulation_runtime.disarm('Adapter selection unavailable: '+str(exc));self.control.reset_inputs();self.toast(str(exc));self._simulation_runtime.sync_adapters(self)
            self.invalidate_gui_cache();return
        return super().after_setting_changed(setting)
    def chassis_follow_mode(self):return 'V2 BODY'
    def draw_watermark(self,p):
        r=self._simulation_runtime;t=r.transport
        p.save();p.setFont(self.font('museo',18,QFont.Weight.Bold));p.setPen(QColor('#eff5ff'))
        p.drawText(QRectF(14,4,410,32),Qt.AlignmentFlag.AlignLeft,'POLAR-RM')
        p.setFont(self.font('poppins_medium',8));p.setPen(QColor('#91b8c3'))
        p.drawText(QRectF(14,37,700,20),Qt.AlignmentFlag.AlignLeft,'V2 '+t.role.upper()+' | watching '+str(t.watch_robot or 'none')+' | physical TX disabled')
        p.restore()
    def keyPressEvent(self,event):
        if (self._simulation_runtime.transport.role=='spectator'
            and self.combat_control_input_allowed()
            and event.key() in (Qt.Key.Key_W,Qt.Key.Key_A,Qt.Key.Key_S,Qt.Key.Key_D)):
            self.toast('V2 spectator: movement input disabled');event.accept();return
        self.control.defer_sim_command=True;self.control.deferred_pending=False
        try:result=super().keyPressEvent(event)
        finally:self.control.defer_sim_command=False
        self._simulation_runtime.modes.sync(self)
        # Preserve original key/overlay routing, then synchronously revoke this
        # simulation input if the event transferred ownership to a GUI panel.
        if not self.combat_control_input_allowed():
            self._simulation_runtime.disarm('V2 UI owns keyboard input; rearm after closing')
            self.control.reset_inputs()
        elif self.control.deferred_pending and self._simulation_runtime.armed:
            self.control.send_from_state(self.state,self.registry)
        self.control.deferred_pending=False
        return result
    def keyReleaseEvent(self,event):
        self.control.defer_sim_command=True;self.control.deferred_pending=False
        try:result=super().keyReleaseEvent(event)
        finally:self.control.defer_sim_command=False
        self._simulation_runtime.modes.sync(self)
        if not self.combat_control_input_allowed():
            self._simulation_runtime.disarm('V2 UI owns keyboard input; rearm after closing')
            self.control.reset_inputs()
        elif self.control.deferred_pending and self._simulation_runtime.armed:
            self.control.send_from_state(self.state,self.registry)
        self.control.deferred_pending=False
        return result
    def mousePressEvent(self,event):
        if (self._simulation_runtime.transport.role=='spectator'
            and self.combat_control_input_allowed() and event.button()==Qt.MouseButton.LeftButton):
            self.toast('V2 spectator: fire input disabled');event.accept();return
        return super().mousePressEvent(event)
    def paint_overlay(self,event,**kwargs):
        # An extra paint can precede the next UI heartbeat. Invalidate cached
        # SIM validity before the original MC sidebar reads it; never refresh
        # a stale model by repainting or invent a new telemetry sample.
        state=getattr(self,'state',None)
        if state is not None:
            try:
                identity=self._simulation_runtime.display_identity()
                fresh=identity is not None and getattr(state,'_platform_display_identity',None)==identity
            except Exception:fresh=False
            if not fresh:state.mujoco_telemetry_valid=False;state.mujoco_partial=True
        # Reuse the original renderer once; do not show v1 rate/aim confirmation.
        result=PolarRMWindow.paint_overlay(self,event,**kwargs)
        existing=kwargs.get('painter');p=existing if existing is not None else QPainter(self)
        if p.isActive():
            width,height=kwargs.get('logical_size') or (self.width(),self.height())
            r=self._simulation_runtime;t=r.transport
            clock=(t.raw_state or {}).get('clock_profile',{})
            offline=clock.get('kind')=='offline_modeled_latency_v1'
            if offline:label='OFFLINE / SIL | modeled delays | '+str(clock.get('barrier_state','unknown'))
            elif t.role=='spectator':label='V2 OBSERVER | watch only | movement / gimbal / fire disabled'
            elif r.armed:label=('V2 OPERATOR | '+t.adapter_feedback()+' | fire manual | F12 release') if t.plan else 'V2 MANUAL | WASD / mouse target / left fire | F12 release'
            elif t.owned_robot:label='V2 LEASE CLAIMED, DISARMED | F2 arm | F12 release'
            elif t.claim_pending:label='V2 CLAIM PENDING | no input sent'
            else:label='V2 DISARMED | '+r.note
            camera=t.snapshot()
            age=max(0.,t.clock()-camera[2]) if camera[1] is not None else None
            detail=('CAMERA %.2fs old | low-FPS | display TTL %.1fs' % (age,t.max_frame_age)) if age is not None else 'CAMERA unavailable | manual control disabled'
            presenter=getattr(r,'spin_status',None)
            try:spin=presenter.snapshot() if presenter is not None else dict(reason='unknown')
            except Exception:spin=dict(reason='presentation unavailable')
            lines=['SIM ONLY | '+label,detail]+status_lines(spin)
            font=QFont('Sans Serif',8);metrics=QFontMetricsF(font)
            p.save();p.setFont(font)
            regions=reserved_regions(self,width,height)
            rectangles=status_layout(width,height,len(lines),regions)
            for index,(text,rect) in enumerate(zip(lines,rectangles)):
                p.setPen(QColor('#e3c78b' if index==1 else '#73f2df'))
                shown=metrics.elidedText(text,Qt.TextElideMode.ElideRight,rect[2])
                p.drawText(QRectF(*rect),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,shown)
            p.restore()
            if existing is None:p.end()
        return result

def main(argv=None):
    parser=argparse.ArgumentParser(description='Original POLAR UI, explicit SIM platform v2 opt-in',allow_abbrev=False)
    parser.add_argument('--sim-platform-v2',action='store_true',required=True)
    parser.add_argument('--sim-endpoint',default='http://127.0.0.1:8766')
    parser.add_argument('--sim-role',choices=('spectator','operator'),default='spectator')
    args=parser.parse_args(argv)
    runtime=PlatformRuntime(args.sim_endpoint,role=args.sim_role)
    surface=QSurfaceFormat();surface.setVersion(3,3);surface.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile);QSurfaceFormat.setDefaultFormat(surface)
    app=QApplication.instance() or QApplication(['POLAR-SIM-V2']);window=PlatformWindow(runtime);window.show();return app.exec()

if __name__=='__main__':raise SystemExit(main())
