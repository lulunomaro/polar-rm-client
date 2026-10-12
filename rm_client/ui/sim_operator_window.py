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
from ..match_modes import MATCH_MODES
from ..sim_operator_modes import supports_mouse_angle
from ..sim_drive_frames import supports_drive_frames
from .jello_locale import chinese_enabled
from ..sim_robot_selection import SELECTION_SETTING, selection_profiles


class OperatorWindow(PolarRMWindow):
    def __init__(self,runtime):
        self._simulation_runtime=runtime;self._simulation_configuration_ready=False
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
        if runtime.feature_policy:runtime.feature_policy.notify=self.toast
        runtime.apply_state(self.state)
        self.setWindowTitle('POLAR-RM · SIM OPERATOR · physical TX disabled')
        self._simulation_configuration_ready=True
        runtime.start()

    def on_tick(self):
        self._simulation_runtime.heartbeat(self)
        super().on_tick()
        # Final authority after all legacy presentation-only ticks.
        self._simulation_runtime.apply_state(self.state)

    def restore_sim_configuration(self):
        runtime=self._simulation_runtime
        runtime.feature_policy.restore_locked_settings()
        config=MATCH_MODES['RMUL3v3']
        self.match_mode_manager.current=replace(config,title='SIM truth',field=replace(config.field,title='SIM XY / display only',asset_path='',zones=[]))
        self._vision_source='Simulation';self._tongji_enabled=False
        runtime.apply_state(self.state)
        runtime.feature_policy.sync_identity(self.state)

    def sync_runtime_from_registry(self,*args,**kwargs):
        result=super().sync_runtime_from_registry(*args,**kwargs)
        if self._simulation_configuration_ready:
            self.restore_sim_configuration()
            self._simulation_runtime.disarm('Configuration changed: press F2 to resume')
        return result

    def apply_match_mode(self,*args,**kwargs):
        if self._simulation_configuration_ready:
            self.toast('SIM mode is fixed by the current simulator session');return
        return super().apply_match_mode(*args,**kwargs)

    def apply_robot_profile(self,*args,**kwargs):
        if self._simulation_configuration_ready:
            self._simulation_runtime.feature_policy.sync_identity(self.state)
            self._simulation_runtime.disarm('Robot selection is unavailable in this SIM session')
            self.toast(self._simulation_runtime.note);return
        return super().apply_robot_profile(*args,**kwargs)

    def reload_current_config(self,*args,**kwargs):
        result=super().reload_current_config(*args,**kwargs)
        if self._simulation_configuration_ready:
            self.restore_sim_configuration()
            self._simulation_runtime.disarm('Configuration loaded: press F2 to resume')
        return result

    def toast(self,message,*,kind='info'):
        policy=self._simulation_runtime.feature_policy
        if policy:
            for name in policy.supported:
                if message.startswith(name+' ') and ('已启用' in message or '已关闭' in message):
                    message='SIM request changed: '+name+'; check simulator feedback for execution';break
            for name in policy.unavailable_names:
                if message.startswith(name+' ') and ('已启用' in message or '已关闭' in message):
                    message='SIM unavailable: '+name+' — '+policy.reason(name);break
        return super().toast(message,kind=kind)

    def module_display_label(self,module,label):
        policy=self._simulation_runtime.feature_policy
        if policy.reason(module.name):return '[SIM N/A] '+label
        if module.name in policy.supported:return '[SIM REQUEST] '+label
        return label

    def setting_display_label(self,setting,label):
        module=self._simulation_runtime.feature_policy.owner(setting)
        if module and module.name=='ChassisAssist' and setting.name=='Follow':
            return 'SIM 参考方向' if chinese_enabled(self.registry) else 'SIM drive frame'
        return label

    def setting_is_unavailable(self,setting):
        if setting.name==SELECTION_SETTING:return not selection_profiles(self._simulation_runtime.transport.session) or self._simulation_runtime.transport.selection_pending
        policy=self._simulation_runtime.feature_policy
        module=policy.owner(setting)
        if module is None:return False
        if module.name=='RobotLink' and setting.name.startswith('Bind '):return False
        return module.name in policy.original_descriptions and id(setting) in policy.locked_values and setting.name not in policy.supported.get(module.name,set()) and module.name not in ('Scenario','MatchMode','RobotProfile','AllianceProfile','CameraFeed')

    def after_setting_changed(self,setting):
        if setting.name==SELECTION_SETTING:
            try:self._simulation_runtime.select_robot(self,str(setting.value))
            except (ValueError,TypeError) as exc:
                self._simulation_runtime.disarm('Selection unavailable: '+str(exc));self.control.reset_inputs();self.toast(self._simulation_runtime.note)
            self._simulation_runtime.sync_selection(self)
            return
        policy=self._simulation_runtime.feature_policy
        if policy and policy.reject_setting(setting):
            self._simulation_runtime.disarm('Unsupported setting: virtual input released')
            self.invalidate_gui_cache();return
        module=policy.owner(setting) if policy else None
        if module and module.name in policy.supported:
            self._simulation_runtime.disarm('SIM request changed: release keys, then F2')
            self.control.reset_inputs();self.invalidate_gui_cache()
            return
        return super().after_setting_changed(setting)

    def on_module_toggled(self,module,enabled,source):
        hit=self._simulation_runtime.hit_spin
        if module.name=='SpinMode' and not hit.in_transition(module,enabled):hit.operator_override()
        if module.name in ('SpinMode','ArmorAutoAim','AutoSpinOnHit','ChassisAssist') and not hit.in_transition(module,enabled):
            self._simulation_runtime.disarm('SIM request changed: release keys, then F2')
            self.control.reset_inputs()
        return super().on_module_toggled(module,enabled,source)

    def publish_spin_control_state(self,*args,**kwargs):
        # Only a synchronous private policy context can bypass this release.
        enabled=args[0] if args else kwargs.get('enabled')
        if self._simulation_runtime.hit_spin.in_transition(enabled=enabled):return False
        # Original dispatch can call this before the registry toggle. No device route.
        self._simulation_runtime.disarm('SIM spin request changed: press F2 to resume')
        self.control.reset_inputs();return False

    def update_auto_spin_guard(self,now=None):
        return self._simulation_runtime.hit_spin.update(self)

    def reject_sim_action(self,label):
        self._simulation_runtime.disarm('SIM unavailable: '+label)
        self.toast(self._simulation_runtime.note);return False

    def open_economy_workspace(self,*args,**kwargs):return self.reject_sim_action('purchasing/revival')
    def send_economy_request(self,*args,**kwargs):return self.reject_sim_action('economy commands')
    def open_hero_deploy_interface(self,*args,**kwargs):return self.reject_sim_action('hero deployment')
    def open_match_chat(self,*args,**kwargs):return self.reject_sim_action('chat delivery')
    def open_student_panel(self,panel):
        if panel!='inventory':return self.reject_sim_action(panel+' data/commands')
        return super().open_student_panel(panel)
    def handle_calibration_action(self,*args,**kwargs):return self.reject_sim_action('camera calibration')
    def hud_row_visible(self,module_name,show_setting=None):
        if module_name=='VictoryPointStatus' and show_setting=='Control Zone':return False
        return super().hud_row_visible(module_name,show_setting)

    def effective_spin_enabled(self):return self._simulation_runtime.modes.feedback()['spin']
    def chassis_follow_mode(self):
        if not supports_drive_frames(self._simulation_runtime.transport.session):return 'SIM BODY'
        frame=self._simulation_runtime.modes.feedback()['drive']
        return {'free':'SIM BODY','field':'SIM FIELD','gimbal':'SIM GIMBAL'}.get(frame,'SIM REF ?')
    def rm_indicator_visible(self,key):return False
    def status_card_visible(self,label):return False

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
            mouse_label='mouse angle' if supports_mouse_angle(self._simulation_runtime.transport.session) else 'legacy mouse rate (RTF dependent)'
            label='SIM ONLY | F2 arm | WASD move | '+mouse_label+' | left fire | F12 stop'
            if not self._simulation_runtime.armed:label='SIM DISARMED | '+self._simulation_runtime.note
            p.drawText(QRectF(18,58,width-36,20),Qt.AlignmentFlag.AlignCenter,label)
            feedback=self._simulation_runtime.modes.feedback()
            if any(self._simulation_runtime.modes.support):
                rate=feedback['measured_yaw_rate']
                measured='unknown' if rate is None else f'{rate:.2f} rad/s'
                spin=('ACTIVE ('+str(feedback['spin_origin'])+')') if feedback['spin'] else feedback['spin_reason']
                aim='SOURCE ACTIVE' if feedback['aim'] else feedback['aim_reason']
                p.drawText(QRectF(18,184,width-36,20),Qt.AlignmentFlag.AlignCenter,f'SIM spin: {spin} | measured {measured} | aim: {aim} | manual fire')
                if self._simulation_runtime.modes.drive_support:
                    frame=feedback['drive'] or feedback['drive_reason']
                    p.drawText(QRectF(18,207,width-36,20),Qt.AlignmentFlag.AlignCenter,'SIM driving reference: '+str(frame)+' | no chassis yaw follow')
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
