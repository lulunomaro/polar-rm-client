"""Bounded original-Qt + real v2 camera/physics integration, loopback only.

Qt paints the original overlay into QImage (CPU); this is not desktop OpenGL
acceptance. Keyboard/mouse events are synthetic, camera pixels/physics are real.
Supply the separate authorized simulator checkout and its Python environment.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from unittest.mock import patch


def main():
    if not __debug__:raise RuntimeError('Qualification refuses Python -O')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge-directory',required=True,type=Path)
    parser.add_argument('--bridge-python',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtCore import Qt,QEvent,QPointF,QRectF
    from PySide6.QtGui import QImage,QPainter,QKeyEvent,QFocusEvent,QMouseEvent
    from PySide6.QtWidgets import QApplication
    from rm_client.ui import polar_app
    from rm_client.ui.sim_platform_window import PlatformWindow
    from rm_client.sim_platform_runtime import PlatformRuntime
    from rm_client.sim_platform_client import PlatformClient
    from rm_client.sim_robot_selection import SELECTION_SETTING
    env=os.environ.copy();env.update(PYTHONPATH=str(args.bridge_directory.resolve()),MUJOCO_GL='egl',LIBGL_ALWAYS_SOFTWARE='1',MESA_SHADER_CACHE_DISABLE='true')
    log=(out/'bridge.log').open('x')
    child=subprocess.Popen([str(args.bridge_python.absolute()),'-m','rm_match.platform_v2','--camera','--port','0','--camera-total-fps','2','--max-wall-seconds','45'],cwd=args.bridge_directory,env=env,stdout=subprocess.PIPE,stderr=log,text=True)
    result=dict(passed=False,scope='real_sim_camera_and_original_Qt_CPU_overlay_loopback',checks=[],
        limits=dict(cross_machine_LAN=False,desktop_OpenGL=False,live_native_autoaim=False,algorithm_deadline_relaxed=False,synthetic_keyboard_mouse=True,physical_hardware=False))
    windows=[];runtimes=[];probe=None
    def check(name,condition,evidence=None):
        result['checks'].append(dict(name=name,passed=bool(condition),evidence=evidence))
        if not condition:raise RuntimeError('Check failed: '+name)
    try:
        if not select.select([child.stdout],[],[],10)[0]:raise RuntimeError('Camera server startup timeout')
        ready=json.loads(child.stdout.readline());base=ready['url']
        probe=PlatformClient(base,timeout=.5);probe.open('spectator')
        app=QApplication.instance() or QApplication(['POLAR-V2-qualification'])
        with ExitStack() as stack:
            for name in ('BackendManager','CameraCaptureWorker','TongjiVisionReceiver','LocalAlignedVision','TongjiVisionBridge'):
                stack.enter_context(patch.object(polar_app,name,side_effect=AssertionError('Physical constructor called: '+name)))
            for name in ('polar_port_choices','dt7_port_choices'):
                stack.enter_context(patch('rm_client.model.'+name,side_effect=AssertionError('Physical discovery called: '+name)))
            operator=PlatformRuntime(base,role='operator');spectator=PlatformRuntime(base,role='spectator')
            for r in (operator,spectator):
                w=PlatformWindow(r);w.timer.stop();w.resize(1360,900);windows.append(w);runtimes.append(r)
            ow,sw=windows;focus=[True]
            ow.isActiveWindow=lambda:focus[0];sw.isActiveWindow=lambda:True
            def tick(seconds=.04):
                end=time.monotonic()+seconds
                while time.monotonic()<end:
                    app.processEvents()
                    for window in windows:window.on_tick()
                    time.sleep(.008)
            def wait(predicate,timeout=6.):
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    tick(.035)
                    if predicate():return
                raise RuntimeError('Wait timed out; operator='+operator.transport.last_error+'; '+operator.transport.note+'; '+operator.transport.frame_error+'; spectator='+spectator.transport.last_error)
            def key(window,value,pressed=True):
                event=QKeyEvent(QEvent.Type.KeyPress if pressed else QEvent.Type.KeyRelease,value,Qt.KeyboardModifier.NoModifier)
                (window.keyPressEvent if pressed else window.keyReleaseEvent)(event)
            def mouse(window,x,y,pressed=False):
                kind=QEvent.Type.MouseButtonPress if pressed else QEvent.Type.MouseMove
                event=QMouseEvent(kind,QPointF(x,y),QPointF(x,y),Qt.MouseButton.LeftButton if pressed else Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton if pressed else Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
                (window.mousePressEvent if pressed else window.mouseMoveEvent)(event)
            def select_robot(window,rid):
                setting=window.registry.get('RobotProfile').get_setting(SELECTION_SETTING)
                window.dispatch('jello_mode_select',(setting,rid),QPointF(),1,QRectF(0,0,100,20))
            def capture(window,name):
                window.consume_capture_frame();window._simulation_runtime.apply_state(window.state)
                im=QImage(1360,900,QImage.Format.Format_ARGB32_Premultiplied);im.fill(Qt.GlobalColor.black);p=QPainter(im)
                try:painted=window.paint_overlay(None,painter=p,logical_size=(1360,900),capture_module_list=True)
                finally:p.end()
                check(name+'_original_overlay_painted',bool(painted))
                if not im.save(str(out/(name+'.png'))):raise RuntimeError('Capture write failed')
                t=window._simulation_runtime.transport;state,video,stamp,seq,simstamp=t.snapshot()
                return dict(file=name+'.png',sha256=hashlib.sha256((out/(name+'.png')).read_bytes()).hexdigest(),
                    robot_id=state['robot_id'] if state else None,frame_seq=seq,capture_sim_time=simstamp,
                    displayed_age_s=t.clock()-stamp if video is not None else None,shape=None if video is None else list(video.shape))
            wait(lambda:len(operator.transport.robot_ids)==6 and len(spectator.transport.robot_ids)==6)
            select_robot(ow,'red_sentry');select_robot(sw,'blue_sentry')
            wait(lambda:operator.transport.selection_ready() and spectator.transport.selection_ready(),timeout=12.)
            before=probe.state();check('watching_does_not_claim',all(l['owner'] is None for row in before['control']['leases'].values() for l in row.values()))
            check('two_independent_real_cameras',operator.transport.video_native_size==(1440,1080) and spectator.transport.video_native_size==(1440,1080)
                  and not (operator.transport.video==spectator.transport.video).all())
            check('spectator_cannot_arm',not spectator.arm())
            key(sw,Qt.Key.Key_W);mouse(sw,680,450,True);tick(.08)
            check('spectator_input_has_no_control_lease',spectator.transport.owned_robot is None and all(l['owner'] is None for l in probe.state()['control']['leases']['blue_sentry'].values()))
            check('actual_judge_fields_projected',ow.state.mujoco_telemetry_valid and operator.transport.telemetry['own']['ammo']==next(r for r in probe.state()['robots'] if r['robot_id']=='red_sentry')['ammo'])
            result['captures']=[capture(ow,'operator-watch'),capture(sw,'spectator-watch')]
            # Rendering a large CPU image can pause UI heartbeats; re-enter the
            # ordinary tick path before the explicit two-step arm sequence.
            tick(.1);key(ow,Qt.Key.Key_F2);wait(lambda:operator.transport.owned_robot=='red_sentry')
            check('first_f2_only_claims',not operator.armed and not operator.transport._focus)
            key(ow,Qt.Key.Key_F2);tick(.04);check('second_f2_arms',operator.armed)
            initial=operator.transport.telemetry['own'].copy()
            key(ow,Qt.Key.Key_W);mouse(ow,680,450,True);mouse(ow,680,450)
            for i in range(12):mouse(ow,682+i*2,450-i);tick(.025)
            active=probe.state();applied=active['applied_commands']['red_sentry'];own=next(r for r in active['robots'] if r['robot_id']=='red_sentry')
            check('original_key_drives_real_physics',abs(own['xy'][0]-initial['xy'][0])>.005 and applied['force_x']!=0,dict(dx=own['xy'][0]-initial['xy'][0],force_x=applied['force_x']))
            check('original_mouse_moves_real_gimbal',abs(own['gimbal_yaw']-initial['gimbal_yaw'])>.003)
            check('explicit_click_consumes_judge_ammo',own['ammo']<initial['ammo'],dict(before=initial['ammo'],after=own['ammo']))
            check('other_watched_robot_not_controlled',not active['applied_commands']['blue_sentry']['fire'] and active['applied_commands']['blue_sentry']['force_x']==0)
            focus[0]=False;ow.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut));tick(.12)
            blur=probe.state()['applied_commands']['red_sentry']
            check('focus_loss_releases_and_neutralizes',not operator.armed and operator.transport.owned_robot is None and not blur['fire'] and blur['force_x']==blur['force_y']==0.)
            focus[0]=True;tick(.08);check('focus_restore_never_rearms',not operator.armed and not any(ow.control.key_state.values()) and not ow.control.left_button_down)
            oldseq=operator.transport.video_sequence;select_robot(ow,'blue_sentry')
            check('switch_clears_old_frame_immediately',operator.transport.video is None and ow.frame_image is None)
            wait(lambda:operator.transport.selection_ready(),timeout=10.)
            check('switch_watches_new_generation_without_claim',operator.transport.telemetry['robot_id']=='blue_sentry' and operator.transport.video_sequence>oldseq and operator.transport.owned_robot is None)
            result['captures'].append(capture(ow,'operator-switched-watch'))
            check('physical_backends_never_connected',all(not w.backend.serial.connected and not w.backend.mqtt.connected and not w.backend.publish_keyboard_mouse()[0] for w in windows))
            check('native_aim_remains_opt_in_unavailable',not next(d for d in probe.adapters()['adapters'] if d['adapter_id']=='aim_native_rmcs_v1')['available'])
            result['camera_status']=probe.state().get('camera_status',probe.state().get('camera'))
            child.terminate();child.wait(timeout=5);tick(.65)
            check('server_disconnect_clears_state_and_controls',operator.transport.snapshot()[0] is None and not operator.armed and operator.transport.owned_robot is None and not ow.control.left_button_down)
            result['captures'].append(capture(ow,'operator-disconnected'))
            result['passed']=True
    except BaseException as exc:
        result['error']=type(exc).__name__+': '+str(exc)
        raise
    finally:
        for w in windows:
            try:w.close()
            except Exception:pass
        if probe is not None:
            try:probe.close()
            except Exception:pass
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        log.write(child.stdout.read());log.close()
        (out/'qualification.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(passed=result['passed'],checks=len(result['checks']),scope=result['scope'])))

if __name__=='__main__':main()
