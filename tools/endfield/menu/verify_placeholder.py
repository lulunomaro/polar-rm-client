"""实际 D3D 验证 UI 切换入口、动作及取消后的菜单状态（保留历史脚本名）。"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from rm_client.ui.endfield.menu.native import EndfieldNativeMenu
from tools.endfield.pack import validate
from tools.endfield.verify_hover import hover_reader


def check_window_switch(window, wait, until, out):
    """经真实 Qt 点击右侧条目，并取消确认弹窗，保留 Esc 菜单。"""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QImage
    from unittest.mock import patch
    menu,dpr = window._endfield_menu,window.devicePixelRatioF()
    rect = (ctypes.c_double*4)()
    def point(code):
        until(lambda:bool(menu.api.button(menu.handle,code,rect)))
        return rect[0]+rect[2]/2,rect[1]+rect[3]/2
    x,y = point(9);menu.pointer(1,x,y);wait(.65)
    x,y = point(9);menu.pointer(4,x,y,-30);wait(1.1)
    x,y = point(21);menu.pointer(1,x,y);wait(.65)
    x,y = point(21)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    seen = []
    def cancel_confirmation():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            QTimer.singleShot(20, cancel_confirmation)
            return
        assert dialog.objectName() == "UISwitchConfirmation"
        dialog.grab().save(str(out / "ui_switch_confirmation.png"))
        seen.append(dialog.target_theme)
        dialog.reject()
    with patch.object(window,"dispatch_endfield_action",wraps=window.dispatch_endfield_action) as dispatch:
        QTimer.singleShot(200, cancel_confirmation)
        QTest.mouseClick(window,Qt.MouseButton.LeftButton,pos=QPoint(round(x/dpr),round(y/dpr)))
        until(lambda: bool(seen))
        wait(.25)
        assert window.escape_menu_visible and not window.gui_visible
        dispatch.assert_called_once_with(21)
    raw = out/"ui_switch_cancelled.bgra"
    menu.api.capture(menu.handle,os.fsencode(raw));until(lambda:menu.api.captured(menu.handle)!=0)
    assert menu.api.captured(menu.handle)==1
    width,height = round(window.width()*dpr),round(window.height()*dpr)
    assert QImage(raw.read_bytes(),width,height,width*4,QImage.Format.Format_ARGB32).save(str(raw.with_suffix(".png")))
    return {"code":21,"dpr":dpr,"escape_still_open":True,"client_actions":1,"target":seen[0],"status":"PASS"}


def run(pack: Path, out: Path):
    from PySide6.QtGui import QImage
    validate(pack)
    out.mkdir(parents=True,exist_ok=False)
    report = {"scope":"owned native D3D; UI switch emits action 21 once per click","cases":[]}
    for width,height,chinese in ((1280,720,True),(1920,1080,False)):
        menu = EndfieldNativeMenu(pack)
        def until(test,timeout=15):
            deadline = time.perf_counter()+timeout
            while not test():
                if menu.handle and menu.api.status(menu.handle)==-1:
                    raise RuntimeError(menu.api.error(menu.handle))
                if time.perf_counter()>deadline:raise TimeoutError("UI switch source card")
                time.sleep(.005)
        def point(code):
            rect = (ctypes.c_double*4)()
            until(lambda:bool(menu.api.button(menu.handle,code,rect)))
            return rect[0]+rect[2]/2,rect[1]+rect[3]/2
        def capture(name):
            raw = out/f"{width}_{name}.bgra"
            menu.api.capture(menu.handle,os.fsencode(raw))
            until(lambda:menu.api.captured(menu.handle)!=0)
            assert menu.api.captured(menu.handle)==1
            data = raw.read_bytes()
            assert QImage(data,width,height,width*4,QImage.Format.Format_ARGB32).save(str(raw.with_suffix(".png")))
        try:
            menu.prepare(0,width,height,offscreen=True);until(lambda:menu.ready)
            menu.tick(width=width,height=height,chinese=chinese,mode="RMUL3v3",paused=False)
            menu.request(True);time.sleep(1.1)
            x,y = point(9);menu.pointer(1,x,y);time.sleep(.65)
            x,y = point(9);menu.pointer(4,x,y,-30);time.sleep(1.1)
            x,y = point(21);menu.pointer(1,x,y);time.sleep(.65)
            x,y = point(21);menu.pointer(1,x,y);time.sleep(.3)
            read_hover = hover_reader(menu)
            assert read_hover()["code"]==21
            capture("ui_switch_hover")
            for _ in range(2):
                menu.pointer(2,x,y);time.sleep(.04);menu.pointer(3,x,y);time.sleep(.3)
                assert menu.api.action(menu.handle)==21
                assert menu.api.action(menu.handle)==0
                assert menu.phase==1
            capture("ui_switch_after_click")
            report["cases"].append({"physical_size":[width,height],"chinese":chinese,
                "hover":read_hover(),"clicks":2,"action":21,"phase":menu.phase,"stats":menu.stats()})
            print(f"PASS UI switch {width}x{height}",flush=True)
        finally:
            menu.close();menu.wait_closed();assert menu.handle==0
    report["status"] = "PASS"
    (out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack",type=Path,required=True);parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args();run(args.pack.resolve(),args.out.resolve())
