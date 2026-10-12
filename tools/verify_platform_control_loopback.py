"""Original Qt inputs -> actual HTTP -> real MuJoCo without rendering.

The camera gate is an explicit tiny synthetic JPEG test fixture. This program
qualifies input/physics/referee/UI-model integration, never camera or desktop QA.
"""
from __future__ import annotations
import sys
if not __debug__:raise SystemExit('Qualification requires normal Python; -O is forbidden')
import argparse,base64,hashlib,json,math,os,select,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def main(argv=None):
    p=argparse.ArgumentParser(allow_abbrev=False);p.add_argument('--nav-root',type=Path,required=True);p.add_argument('--nav-python',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args(argv);a.out.mkdir(parents=True,exist_ok=False)
    def source_hashes():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'rm_client').rglob('*.py'))}
    imported_source=source_hashes()
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    import cv2,numpy as np
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt,QEvent,QPointF
    from PySide6.QtGui import QKeyEvent,QMouseEvent,QFocusEvent
    from rm_client.sim_platform_transport import PlatformTransport
    from rm_client.sim_platform_client import PlatformClient
    from rm_client.sim_platform_runtime import PlatformRuntime
    from rm_client.ui.sim_platform_window import PlatformWindow
    from unittest.mock import patch
    if source_hashes()!=imported_source:raise RuntimeError('Client source changed during import')
    completed=False;checks=[];snapshots=[];worker=None;window=None;observer=None;t=None
    def check(name,condition,detail=None):
        checks.append(dict(name=name,passed=bool(condition),detail=detail))
        if not condition:raise RuntimeError(name)
    try:
        stderr=(a.out/'server.stderr.log').open('x')
        worker=subprocess.Popen([str(a.nav_python.absolute()),str(a.nav_root.resolve()/'tools/platform_v2/control_window/serve_no_gl.py'),'--out',str(a.out/'server'),'--max-wall-seconds','20'],stdout=subprocess.PIPE,stderr=stderr,text=True)
        if not select.select([worker.stdout],[],[],8)[0]:raise RuntimeError('No bounded server ready receipt')
        ready=json.loads(worker.stdout.readline());check('real_noGL_server_ready',ready['event']=='no_gl_control_ready' and ready['camera_available'] is False)
        url=ready['url'];observer=PlatformClient(url,timeout=.5);observer.open('spectator')
        app=QApplication.instance() or QApplication([]);t=PlatformTransport(url,role='operator');r=PlatformRuntime(transport=t)
        with patch.object(t,'start'):window=PlatformWindow(r)
        window.timer.stop();window.isActiveWindow=lambda:True;window.combat_control_input_allowed=lambda:True;window.registry.get('AudioFeedback').enabled=False
        # Test-only gate injection; no production fallback or fake camera mode.
        ok,jpeg=cv2.imencode('.jpg',np.zeros((6,8,3),dtype=np.uint8))
        if not ok:raise RuntimeError('Synthetic fixture encode failed')
        pixels=base64.b64encode(jpeg.tobytes()).decode();seq=[0]
        def fixture_frame():
            with t._lock:
                raw=t.raw_state;rid=t.watch_robot;epoch=t.watch_epoch
                if raw is None or rid is None or epoch is None:return {'available':False,'reason':'synthetic gate waiting'}
                stamp=raw['sim_time'];episode=raw['episode'];seq[0]+=1
            now=time.monotonic()
            return dict(available=True,robot_id=rid,watch_epoch=epoch,episode=episode,frame_seq=seq[0],capture_sim_time=stamp,age_wall_s=0.,max_frame_age_wall_s=2.5,capture_wall_monotonic=now,encode_completed_wall_monotonic=now,source='simulated_raw_camera_display_jpeg',full_frame=True,algorithm_input=False,own_qpos_at_capture=[0.]*5,own_qvel_at_capture=[0.]*5,own_odom_at_capture=[0.]*3,own_odom_velocity_at_capture=[0.]*3,calibration={'intrinsics':{'fx':10.,'fy':10.,'cx':4.,'cy':3.,'distortion_model':'brown_conrady_5','distortion':[0.]*5},'extrinsics':{'parent_frame':'gimbal_link','translation_m':[.24,0.,.06],'rotation_xyzw':[-.5,.5,-.5,.5],'calibration_id':'explicit_synthetic_noGL_test_gate'}},image={'encoding':'jpeg','width':8,'height':6,'data_base64':pixels})
        def tick():
            app.processEvents();r.heartbeat(window);r.apply_state(window.state);window.control.tick(window.state,window.registry)
        def until(predicate,timeout=2.):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                tick()
                if predicate():return True
                time.sleep(.015)
            return False
        def key(k,release=False):
            event=QKeyEvent(QEvent.Type.KeyRelease if release else QEvent.Type.KeyPress,k,Qt.KeyboardModifier.NoModifier)
            (window.keyReleaseEvent if release else window.keyPressEvent)(event)
        def mouse(kind,point,button=Qt.MouseButton.NoButton,buttons=Qt.MouseButton.NoButton):
            event=QMouseEvent(kind,point,point,button,buttons,Qt.KeyboardModifier.NoModifier)
            {QEvent.Type.MouseMove:window.mouseMoveEvent,QEvent.Type.MouseButtonPress:window.mousePressEvent,QEvent.Type.MouseButtonRelease:window.mouseReleaseEvent}[kind](event)
        t.start();check('session_negotiates_server_window_profile',until(lambda:t.session is not None and bool(t.robot_ids)) and t.session['control_profile']=='server_window_v1')
        r.select_robot(window,'red_sentry');check('watch_ack_received',until(lambda:t.watch_epoch is not None))
        actual=t.client.frame();check('real_camera_unavailable_gate_refuses_claim',actual['available'] is False and not r.arm() and t.owned_robot is None)
        t.client.frame=fixture_frame
        check('explicit_synthetic_camera_gate_ready',until(t.selection_ready));before=observer.state();snapshots.append(before)
        key(Qt.Key.Key_F2);check('first_F2_claims_but_stays_disarmed',until(lambda:t.owned_robot=='red_sentry') and not r.armed)
        key(Qt.Key.Key_F2);check('second_F2_explicitly_arms',r.armed)
        key(Qt.Key.Key_W);mouse(QEvent.Type.MouseMove,QPointF(680,450));mouse(QEvent.Type.MouseMove,QPointF(692,444));mouse(QEvent.Type.MouseButtonPress,QPointF(680,450),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
        def progressed():
            data=t.snapshot()[0]
            return bool(data and data['own']['ammo']<750 and abs(data['own']['gimbal_yaw'])>.005 and math.dist(data['own']['xy'],next(v['xy'] for v in before['robots'] if v['robot_id']=='red_sentry'))>.0001)
        check('Qt_W_mouse_left_click_reaches_real_physics',until(progressed,2.))
        moving=observer.state();snapshots.append(moving);own=next(v for v in moving['robots'] if v['robot_id']=='red_sentry');commands=moving['applied_commands']['red_sentry']
        check('judge_ammo_and_heat_are_real_feedback',own['ammo']<750 and own['heat']>0,{'ammo':own['ammo'],'heat':own['heat']})
        check('strict_window_seq_and_consumption_receipt_present',moving['control']['leases']['red_sentry']['motion']['input_window_id'] is not None and moving['manual_consumption_receipts']['red_sentry']['manual_inputs']['motion']['seq']>=0)
        # Model projection is updated by the actual original runtime mapping.
        r.apply_state(window.state);check('original_client_model_has_authoritative_own_HP_ammo',window.state.mujoco_telemetry_valid and t.snapshot()[0]['own']['ammo']<=750)
        window.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut));check('actual_Qt_focus_loss_disarms',not r.armed and not t._focus)
        check('server_releases_and_neutralizes_after_focus_loss',until(lambda: all(v['owner'] is None for v in observer.state()['control']['leases']['red_sentry'].values())))
        time.sleep(.04);stopped=observer.state();snapshots.append(stopped);cmd=stopped['applied_commands']['red_sentry']
        check('real_actuators_zero_and_fire_false_after_release',all(cmd[k]==0. for k in ('force_x','force_y','yaw_torque')) and cmd['fire'] is False and stopped['applied_targets']['red_sentry']['motion'] is None)
        stop_ammo=next(v['ammo'] for v in stopped['robots'] if v['robot_id']=='red_sentry');time.sleep(.20);after=observer.state();snapshots.append(after)
        check('no_hidden_fire_after_focus_loss',next(v['ammo'] for v in after['robots'] if v['robot_id']=='red_sentry')==stop_ammo)
        check('physical_outputs_are_inert',not r.backend.serial.connected and not r.backend.mqtt.connected and r.backend.publish_keyboard_mouse()[0] is False)
        t.stop();check('stop_clears_original_display_caches',t.snapshot()[0] is None and t.snapshot()[1] is None);completed=True
    finally:
        if window is not None:window.close();window._simulation_runtime._tmp.cleanup()
        elif t is not None:t.stop()
        if observer is not None:observer.close()
        if worker is not None:
            worker.terminate()
            try:worker.wait(timeout=3)
            except subprocess.TimeoutExpired:worker.kill();worker.wait(timeout=1);raise RuntimeError('Bounded server stop failed')
        if worker is not None and worker.returncode!=0:checks.append(dict(name='server_exit_zero',passed=False,detail=worker.returncode))
        checks.append(dict(name='client_source_unchanged_during_run',passed=source_hashes()==imported_source))
        (a.out/'result.json').write_text(json.dumps(dict(passed=completed and bool(checks) and all(v['passed'] for v in checks),checks=checks,scope='original Qt event/input logic + strict loopback SDK + real MuJoCo/noGL + referee/client model',camera_gate='explicit 8x6 synthetic JPEG, no real image qualification',visible_window=False,rendering=False,no_hardware_output=True,snapshots=snapshots,source_hashes=imported_source,runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),indent=2)+'\n')
    if not all(v['passed'] for v in checks):raise RuntimeError('Qualification failed')
    print(json.dumps({'passed':True,'checks':len(checks)}))

if __name__=='__main__':main()
