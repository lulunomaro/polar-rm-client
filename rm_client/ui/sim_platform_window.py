"""Original POLAR window, explicit platform-v2 opt-in; v1 entry stays unchanged."""
from __future__ import annotations
import argparse
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QColor,QFont,QPainter,QSurfaceFormat
from PySide6.QtWidgets import QApplication
from .polar_app import PolarRMWindow
from .sim_operator_window import OperatorWindow
from ..sim_platform_runtime import PlatformRuntime
from ..sim_robot_selection import SELECTION_SETTING

class PlatformWindow(OperatorWindow):
    def __init__(self,runtime):
        super().__init__(runtime);self.setWindowTitle('POLAR-RM · SIM PLATFORM V2 · '+runtime.transport.role.upper())
    def setting_display_label(self,setting,label):
        if setting.name==SELECTION_SETTING:return 'V2 观看车辆'
        return super().setting_display_label(setting,label)
    def setting_is_unavailable(self,setting):
        if setting.name==SELECTION_SETTING:return not self._simulation_runtime.transport.robot_ids
        return super().setting_is_unavailable(setting)
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
        return super().keyPressEvent(event)
    def mousePressEvent(self,event):
        if (self._simulation_runtime.transport.role=='spectator'
            and self.combat_control_input_allowed() and event.button()==Qt.MouseButton.LeftButton):
            self.toast('V2 spectator: fire input disabled');event.accept();return
        return super().mousePressEvent(event)
    def paint_overlay(self,event,**kwargs):
        # Reuse the original renderer once; do not show v1 rate/aim confirmation.
        result=PolarRMWindow.paint_overlay(self,event,**kwargs)
        existing=kwargs.get('painter');p=existing if existing is not None else QPainter(self)
        if p.isActive():
            width,height=kwargs.get('logical_size') or (self.width(),self.height())
            r=self._simulation_runtime;t=r.transport
            if t.role=='spectator':label='V2 OBSERVER | watch only | movement / gimbal / fire disabled'
            elif r.armed:label='V2 MANUAL | WASD / mouse target / left fire | F12 release'
            elif t.owned_robot:label='V2 LEASE CLAIMED, DISARMED | F2 arm | F12 release'
            elif t.claim_pending:label='V2 CLAIM PENDING | no input sent'
            else:label='V2 DISARMED | '+r.note
            p.save();p.setPen(QColor('#73f2df'));p.setFont(QFont('Sans Serif',10));p.drawText(QRectF(18,58,width-36,24),Qt.AlignmentFlag.AlignCenter,label);p.restore()
            camera=t.snapshot()
            age=max(0.,t.clock()-camera[2]) if camera[1] is not None else None
            detail=('CAMERA %.2fs old | low-FPS | display TTL %.1fs' % (age,t.max_frame_age)) if age is not None else 'CAMERA unavailable | manual control disabled'
            p.save();p.setPen(QColor('#e3c78b'));p.setFont(QFont('Sans Serif',8));p.drawText(QRectF(14,62,min(430,width-28),20),Qt.AlignmentFlag.AlignLeft,detail);p.restore()
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
