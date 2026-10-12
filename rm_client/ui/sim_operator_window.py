"""Original POLAR window with an explicit simulation-only dependency bundle."""
from __future__ import annotations
import argparse
import time
from pathlib import Path
from dataclasses import replace
from PySide6.QtCore import QRectF,Qt
from PySide6.QtGui import QColor,QFont,QPainter,QPixmap,QSurfaceFormat
from PySide6.QtWidgets import QApplication
from .polar_app import PolarRMWindow
from ..sim_operator_runtime import OperatorRuntime


class OperatorWindow(PolarRMWindow):
    def __init__(self,runtime):
        self._simulation_runtime=runtime
        super().__init__()
        self.home_visible=False;self.startup_step='done'
        config=self.match_mode_manager.set_mode('RMUL3v3')
        self.match_mode_manager.current=replace(config,title='SIM truth',field=replace(config.field,title='SIM XY / display only',asset_path='',zones=[]))
        self.state.match_mode='RMUL3v3';self.state.mock_simulation_enabled=False
        self._vision_source='Simulation';self._tongji_enabled=False
        for module,name,value in [('TopMatchBar','Match Scope','All Match Modes'),('CameraFeed','Source','Simulation'),('CameraFeed','Mirror',False),('MatchMode','Mode','RMUL3v3')]:
            setting=self.registry.get(module).get_setting(name)
            if setting is not None:
                if name=='Source' and value not in setting.options:setting.options.append(value)
                setting.value=value
        # These subsystems have no observations/command consumer in this mode.
        # Original modules remain present in Jello, and do not gain live outlets.
        for name in ('TongjiVisionBridge','ArmorAutoAim','EnergyAutoAim','BaseOutpostStatus','EconomyPanel','GimbalPower','PowerLimit','SpeedLimit','BuffTimeline','RadarOverlay','MapMarker','Capacitor'):
            module=self.registry.get(name)
            if module:module.enabled=False
        self.registry.get('Crosshair').enabled=True
        runtime.apply_state(self.state)
        self.setWindowTitle('POLAR-RM · SIM OPERATOR · physical TX disabled')
        runtime.start()

    def on_tick(self):
        self._simulation_runtime.heartbeat(self)
        super().on_tick()
        # Final authority after all legacy presentation-only ticks.
        self._simulation_runtime.apply_state(self.state)

    def draw_rm_hud(self,*args,**kwargs):
        if self.state.mujoco_telemetry_valid:
            return super().draw_rm_hud(*args,**kwargs)
        # Unknown state is not a zero-HP/dead-robot observation.
        if kwargs.get('draw_motion',True):
            return self.paint_hud_motion_overlay(*args[:3])
        return None

    def draw_watermark(self,p):
        p.save();p.setFont(self.font('museo',18,QFont.Weight.Bold));p.setPen(QColor('#eff5ff'))
        p.drawText(QRectF(14,4,410,32),Qt.AlignmentFlag.AlignLeft,'POLAR-RM')
        p.setFont(self.font('poppins_medium',8));p.setPen(QColor('#91b8c3'))
        selected=(self._simulation_runtime.transport.session or {}).get('robot_id','disconnected')
        p.drawText(QRectF(14,37,500,20),Qt.AlignmentFlag.AlignLeft,'SIM FIRST PERSON | '+selected+' | physical TX disabled')
        p.restore()

    def official_map_image(self,config):return None
    def draw_map_zones(self,*args,**kwargs):return None
    def draw_map_markers(self,*args,**kwargs):return None
    def draw_official_map(self,p,rect,config):
        fit=self.fit_aspect(rect,12./8.)
        p.save();p.fillRect(fit,QColor('#131923'));p.setPen(QColor('#34404c'))
        for i in range(1,6):
            x=fit.left()+fit.width()*i/6
            p.drawLine(int(x),int(fit.top()),int(x),int(fit.bottom()))
        for i in range(1,4):
            y=fit.top()+fit.height()*i/4
            p.drawLine(int(fit.left()),int(y),int(fit.right()),int(y))
        p.setFont(QFont('Sans Serif',7));p.setPen(QColor('#b3c8d2'))
        p.drawText(fit.adjusted(4,2,-4,-2),Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,'SIM XY · display only')
        p.restore();return fit

    def refresh_vision_runtime(self):
        self._vision_source='Simulation';self._tongji_enabled=False

    def handle_qualification_action(self,*args,**kwargs):
        self._simulation_runtime.disarm('Physical qualification is unavailable in SIM mode')
        self.toast(self._simulation_runtime.note)

    run_dt7_link_selftest=handle_qualification_action
    run_radio_km_selftest=handle_qualification_action
    start_kmc_qualification_test=handle_qualification_action

    def rescan_serial_ports(self,*args,**kwargs):
        self.toast('Simulation only: physical device scanning is disabled')
        return None

    def ensure_camera_started(self):return None
    def sync_tongji_vision_state(self):return None
    def consume_capture_frame(self):
        # Reuse the original backend-frame upload, without camera/vision sources.
        self.consume_backend_video_frame()
    def normal_backend_video_fresh(self,now=None):
        return self._simulation_runtime.transport.snapshot()[1] is not None
    def normal_video_frame_ready(self,now=None):
        return self.normal_backend_video_fresh() and not self.frame_image.isNull() if self.frame_image is not None else False
    def draw_video(self,p,width,height):
        if self.normal_backend_video_fresh() and self.frame_pixmap is not None and not self.frame_pixmap.isNull():
            image=self.frame_pixmap
            fitted=image.size().scaled(width,height,Qt.AspectRatioMode.KeepAspectRatio)
            p.fillRect(QRectF(0,0,width,height),QColor('#050609'))
            p.drawPixmap(QRectF((width-fitted.width())/2,(height-fitted.height())/2,fitted.width(),fitted.height()),image,QRectF(image.rect()))
        else:
            p.fillRect(QRectF(0,0,width,height),QColor('#050609'))
            p.setPen(QColor('#dedee8'));p.setFont(QFont('Sans Serif',14))
            p.drawText(QRectF(30,height*.35,width-60,120),Qt.AlignmentFlag.AlignCenter,'SIM 第一人称相机未连接\n虚拟运动与发射保持停用')

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_F2 and not event.isAutoRepeat():
            self.control.reset_inputs();self._simulation_runtime.arm()
            self.toast(self._simulation_runtime.note);event.accept();return
        super().keyPressEvent(event)

    def focusOutEvent(self,event):
        self._simulation_runtime.disarm('Window focus lost: press F2 to resume')
        super().focusOutEvent(event)

    def paint_hud_motion_overlay(self,p,width,height,**kwargs):
        if not self.state.mujoco_telemetry_valid:
            scale=self.get_hud_scale(width,height)
            p.save();p.scale(scale,scale);self.draw_watermark(p)
            self.top_match_bar.paint(p,int(width/scale),int(height/scale),time.perf_counter(),hud_scale=scale)
            p.restore();return None
        # Preserve the full original MC/CS/Jello motion rendering.
        result=super().paint_hud_motion_overlay(p,width,height,**kwargs)
        if self.registry.get('Crosshair').enabled and self.normal_backend_video_fresh():
            self.draw_crosshair(p,width,height)
        return result

    def paint_overlay(self,event,**kwargs):
        result=super().paint_overlay(event,**kwargs)
        existing=kwargs.get('painter');p=existing if existing is not None else QPainter(self)
        if p.isActive():
            width,height=kwargs.get('logical_size') or (self.width(),self.height())
            p.save();p.setPen(QColor('#73f2df'));p.setFont(QFont('Sans Serif',10))
            label='SIM ONLY | F2 arm | WASD move | mouse aim | left click fire | F12 stop'
            if not self._simulation_runtime.armed:label='SIM DISARMED | '+self._simulation_runtime.note
            p.drawText(QRectF(18,58,width-36,20),Qt.AlignmentFlag.AlignCenter,label)
            p.restore()
            if existing is None:p.end()
        return result


def main(argv=None):
    parser=argparse.ArgumentParser(description='Original POLAR client / virtual MuJoCo operator only',allow_abbrev=False)
    parser.add_argument('--mujoco-operator',action='store_true',required=True)
    parser.add_argument('--sim-endpoint',default='http://127.0.0.1:8768')
    args=parser.parse_args(argv)
    runtime=OperatorRuntime(args.sim_endpoint)
    surface=QSurfaceFormat();surface.setVersion(3,3);surface.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    QSurfaceFormat.setDefaultFormat(surface)
    app=QApplication.instance() or QApplication(['POLAR-SIM'])
    window=OperatorWindow(runtime);window.show()
    return app.exec()
