"""Bounded real bridge + original POLAR main-window CPU-render/input QA.

Supply your own bridge directory/Python. No fixture or fake camera is used.
The normal desktop OpenGL path is intentionally not claimed by this check.
"""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bridge-directory',required=True,type=Path)
parser.add_argument('--bridge-python',required=True,type=Path)
parser.add_argument('--out',required=True,type=Path)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
output=args.out.resolve();output.mkdir(parents=True,exist_ok=True)
log=(output/'bridge.log').open('w');env=os.environ.copy();env['PYTHONPATH']=str(args.bridge_directory.resolve())
child=subprocess.Popen([str(args.bridge_python.absolute()),'-m','rm_match.operator','--port','0','--robot','red_infantry','--camera-fps','2','--max-wall-seconds','45'],cwd=args.bridge_directory,env=env,stdout=subprocess.PIPE,stderr=log,text=True)
result={'render_path':'original PolarRMWindow paint_overlay to QImage (CPU), not desktop/OpenGL acceptance','synthetic_input':True,'physical_tx':False}
w=None
try:
    import select
    if not select.select([child.stdout],[],[],10)[0]:raise RuntimeError('Bridge startup timed out')
    ready=child.stdout.readline();log.write(ready);log.flush();base=json.loads(ready)['url']
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt,QEvent,QPointF
    from PySide6.QtGui import QImage,QPainter,QKeyEvent,QFocusEvent,QMouseEvent
    from rm_client.ui import polar_app
    from rm_client.ui.sim_operator_window import OperatorWindow
    from rm_client.sim_operator_runtime import OperatorRuntime
    app=QApplication.instance() or QApplication([])
    with ExitStack() as stack:
        for name in ('BackendManager','CameraCaptureWorker','TongjiVisionReceiver','LocalAlignedVision','TongjiVisionBridge'):
            stack.enter_context(patch.object(polar_app,name,side_effect=AssertionError('physical constructor '+name)))
        for name in ('polar_port_choices','dt7_port_choices'):
            stack.enter_context(patch('rm_client.model.'+name,side_effect=AssertionError('physical discovery '+name)))
        runtime=OperatorRuntime(base);w=OperatorWindow(runtime);w.timer.stop();w.resize(1360,900)
        focused=[True];w.isActiveWindow=lambda:focused[0]
        def tick(seconds):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                app.processEvents();w.on_tick();time.sleep(.008)
        def key(key,down=True):
            event=QKeyEvent(QEvent.Type.KeyPress if down else QEvent.Type.KeyRelease,key,Qt.KeyboardModifier.NoModifier)
            (w.keyPressEvent if down else w.keyReleaseEvent)(event)
        def mouse_button(down=True):
            event=QMouseEvent(QEvent.Type.MouseButtonPress if down else QEvent.Type.MouseButtonRelease,QPointF(680,450),QPointF(680,450),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton if down else Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
            (w.mousePressEvent if down else w.mouseReleaseEvent)(event)
        def mouse_move(x,y):
            event=QMouseEvent(QEvent.Type.MouseMove,QPointF(x,y),QPointF(x,y),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            w.mouseMoveEvent(event)
        def capture(name,warm=True):
            if warm:
                for _ in range(10):
                    tick(.04);capture(name,warm=False)
                return
            w.consume_capture_frame();runtime.apply_state(w.state)
            image=QImage(1360,900,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.black)
            painter=QPainter(image)
            try:assert w.paint_overlay(None,painter=painter,logical_size=(1360,900),capture_module_list=True)
            finally:painter.end()
            assert image.save(str(output/name))
            state,_,_,sequence,frame_time=runtime.transport.snapshot()
            metadata={'image':name,'frame_seq':sequence,'frame_sim_time':frame_time,'telemetry_sim_time':state['sim_time'] if state else None,'armed':runtime.armed,'phase':state.get('phase') if state else None,'robot_id':state.get('robot_id') if state else None,'render_path':result['render_path']}
            (output/(name+'.json')).write_text(json.dumps(metadata,indent=2)+'\n')
        deadline=time.monotonic()+10
        while True:
            tick(.05);t,v,*_=runtime.transport.snapshot()
            if t and v is not None and t['own']['ammo']>0:break
            if time.monotonic()>deadline:raise RuntimeError('State/video unavailable: '+runtime.transport.last_error+' / '+runtime.transport.frame_error)
        result['before']=t;result['window_type']=type(w).__name__;result['backend_type']=type(w.backend).__name__
        tick(.3);capture('operator-ready.png');key(Qt.Key.Key_F2);tick(.05)
        assert runtime.armed, runtime.note
        key(Qt.Key.Key_W);mouse_button(True)
        # Use the original mouse event path, with a stable first point.
        mouse_move(680,450)
        for i in range(20):mouse_move(680+2*(i+1),450-(i+1));tick(.025)
        tick(.45)
        during=runtime.transport.snapshot()[0];result['during']=during
        assert runtime.armed, runtime.note
        assert during['own']['xy'][0]>t['own']['xy'][0]+.01,(t['own'],during['own'])
        assert during['own']['gimbal_yaw']<t['own']['gimbal_yaw']-.01,(t['own'],during['own'])
        assert during['own']['ammo']<t['own']['ammo'],(t['own'],during['own'])
        capture('operator-active.png',warm=False)
        focused[0]=False;w.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut));tick(.15)
        blur=runtime.transport.snapshot()[0];result['after_blur']=blur
        assert not runtime.armed and not blur['applied_operator_command']['fire']
        assert blur['applied_operator_command']['force_x']==0 and blur['applied_operator_command']['force_y']==0
        focused[0]=True;tick(.1);assert not runtime.armed
        assert not any(w.control.key_state.values())
        key(Qt.Key.Key_F2);tick(.05);key(Qt.Key.Key_W);mouse_button(True);tick(.1)
        # Qt stops delivering heartbeats, but transport's independent thread runs.
        time.sleep(.35);tick(.05)
        stalled=runtime.transport.snapshot()[0];result['after_ui_stall']=stalled
        assert not runtime.armed and not stalled['applied_operator_command']['fire']
        assert stalled['applied_operator_command']['force_x']==0 and stalled['applied_operator_command']['force_y']==0
        before_reconnect=stalled['control']['last_seq']
        with runtime.transport._lock:runtime.transport._clear_session('QA connection loss')
        tick(.4)
        reconnected=runtime.transport.snapshot()[0];result['after_reconnect']=reconnected
        assert reconnected is not None and reconnected['control']['last_seq']>before_reconnect
        assert not runtime.armed and not any(w.control.key_state.values())
        assert not reconnected['applied_operator_command']['fire']
        key(Qt.Key.Key_F2);tick(.05);key(Qt.Key.Key_W);mouse_button(True);tick(.1)
        child.terminate();child.wait(timeout=5);tick(.6)
        assert not runtime.armed and runtime.transport.snapshot()[0] is None
        assert not any(w.control.key_state.values()) and not w.control.left_button_down
        capture('operator-disconnected.png')
        result['checks']={'original_main_window':True,'original_keyboard_mouse_path':True,'physical_constructors_blocked':True,'virtual_motion':True,'virtual_gimbal':True,'judge_ammo_consumed':True,'first_person_video':True,'focus_loss_neutral':True,'focus_restore_requires_f2':True,'ui_stall_latches_neutral':True,'disconnect_clears_keys_and_fire':True,'same_session_reconnect_monotonic_seq':True,'no_old_input_replay':True}
        result['success']=True
finally:
    if w is not None:w.close()
    if child.poll() is None:
        child.terminate()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.kill();child.wait()
    log.write(child.stdout.read());log.close()
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ('success','checks','render_path')}))
