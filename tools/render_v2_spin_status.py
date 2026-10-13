"""Exactly two SYNTHETIC original-Qt status layouts. No HTTP/physics/camera."""
import sys
if sys.flags.optimize:raise SystemExit('Run without -O/-OO; synthetic layout checks are required')
import argparse,copy,hashlib,json,os
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QImage,QPainter,QColor,QFont
from PySide6.QtWidgets import QApplication
from test_platform_spin import UI
from test_platform_status import fixture,LayoutTests
from rm_client.sim_platform_status import status_layout,status_lines
from rm_client.ui.sim_platform_status_layout import reserved_regions

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    app=QApplication.instance() or QApplication([]);case=UI();case.setUp()
    result=[]
    try:
        case.enable_cap();r,t,w=case.r,case.t,case.w
        # Synthetic DTOs only. This is deliberately not a server or simulator.
        _,sample_t,raw,_,_=fixture();rid=t.watch_robot;session=t.client.session_id
        raw['control']['leases'][rid]=raw['control']['leases'].pop('a')
        raw['spin_consumption_receipts'][rid]=raw['spin_consumption_receipts'].pop('a')
        raw['motion_feedback'][rid]=raw['motion_feedback'].pop('a')
        for q in raw['spin_consumption_receipts'][rid].values():q['robot_id']=rid;q['manual_inputs']['motion']['owner']=session
        raw['control']['leases'][rid]['motion']['owner']=session
        t.raw_state.update(raw);t.raw_state_at=case.now
        t.owned_robot=rid;t.owned_epochs={'motion':1,'gimbal':1,'fire':1}
        t._window=copy.deepcopy(sample_t._window);t._window.update(session_id=session,robot_id=rid);t._window_deadline=case.now+.09
        t._focus=True;r.armed=True;r.modes.enabled=True;r.modes.signature=(True,'auto',420.)
        w.registry.get('SpinMode').enabled=True
        w.home_visible=False;w.gui_visible=False;w.isActiveWindow=lambda:True;w.combat_control_input_allowed=lambda:True
        w.consume_capture_frame();r.apply_state(w.state)
        first=r.spin_status.snapshot()
        if not first['snapshot_gate']:raise RuntimeError('Synthetic valid fixture did not confirm')
        for name,size,stale in [('synthetic-active-1360.png',(1360,900),False),('synthetic-disarmed-stale-1280.png',(1280,720),True)]:
            if stale:
                case.now+=.75;r.armed=False;t._focus=False;r.modes.enabled=False
                w.registry.get('SpinMode').enabled=False;r.note='Synthetic disarmed; historical sample remains labelled'
            width,height=size;w.resize(width,height)
            image=QImage(width,height,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor('#273139'));p=QPainter(image)
            try:
                if not w.paint_overlay(None,painter=p,logical_size=size,hud_background_ready=True,capture_module_list=True):raise RuntimeError('Original Qt paint failed')
                p.setPen(QColor('#ffd47b'));p.setFont(QFont('Sans Serif',13,QFont.Weight.Bold))
                p.drawText(QRectF(0,height/2-35,width,30),Qt.AlignmentFlag.AlignCenter,'SYNTHETIC UI FIXTURE')
                p.setFont(QFont('Sans Serif',10));p.drawText(QRectF(0,height/2,width,30),Qt.AlignmentFlag.AlignCenter,'CPU original Qt composition | no camera / physics / network')
            finally:p.end()
            if not image.save(str(args.out/name)):raise RuntimeError('PNG save failed')
            layout=w.top_match_bar.last_layout
            if layout is None:raise RuntimeError('Original CS layout missing')
            bounds=asdict(layout)['bounds'];cs=(bounds['x'],bounds['y'],bounds['width'],bounds['height'])
            regions=reserved_regions(w,width,height);rows=status_layout(width,height,8,regions)
            if not rows or any(LayoutTests.overlap(a,b) for a in rows for b in regions):raise RuntimeError('No safe status geometry')
            # Original module rows contain their exact current QPainter rectangles.
            module_rows={k:{n:([v.x(),v.y(),v.width(),v.height()] if isinstance(v,QRectF) else v) for n,v in val.items() if isinstance(v,(str,int,float,bool,QRectF))} for k,val in w.module_list_rows.items()}
            status=r.spin_status.snapshot()
            result.append(dict(file=name,width=width,height=height,sha256=hashlib.sha256((args.out/name).read_bytes()).hexdigest(),status=status,status_lines=status_lines(status),status_rects=rows,reserved_regions=regions,actual_cs_layout=asdict(layout),module_rows=module_rows,synthetic=True))
        (args.out/'RESULT.json').write_text(json.dumps(dict(scope='Exactly two original Qt CPU synthetic layout pictures; no real camera/physics/network',images=result),indent=2)+'\n')
    finally:case.tearDown()
    print(json.dumps(dict(passed=True,images=2,out=str(args.out))))
if __name__=='__main__':main()
