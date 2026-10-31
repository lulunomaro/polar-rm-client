"""真实Windows Qt窗口验收；使用惰性设备替身和窗口自己的grab，避免设备I/O。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def check_dropdown(combo, capture):
    """实际点击并检查透明度，避免把不可见的透明弹窗误判为展开成功。"""
    from time import monotonic
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    def until(predicate):
        deadline = monotonic() + 3
        while not predicate() and monotonic() < deadline:
            QTest.qWait(10)
        assert predicate(), "Dropdown did not finish its visible/hidden transition"

    original = combo.currentIndex()
    QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
    popup = combo.view().window()
    until(lambda: popup.isVisible() and popup.windowOpacity() > .99
          and not combo._popup_timer.isActive())
    assert popup.grab().save(str(capture))
    # Qt 的双击保护期后再点击列表，模拟两次独立的用户点击。
    QTest.qWait(QApplication.doubleClickInterval())
    target = (original + 1) % combo.count()
    view = combo.view()
    index = combo.model().index(target, combo.modelColumn(), combo.rootModelIndex())
    view.scrollTo(index)
    QTest.mouseMove(view.viewport(), view.visualRect(index).center())
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                     pos=view.visualRect(index).center())
    until(lambda: not popup.isVisible())
    assert combo.currentIndex() == target
    QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
    until(lambda: popup.isVisible() and popup.windowOpacity() > .99
          and not combo._popup_timer.isActive())
    QTest.keyClick(view, Qt.Key.Key_Escape)
    until(lambda: not popup.isVisible())
    assert combo.currentIndex() == target
    combo.setCurrentIndex(original)


def run(out, sizes):
    os.environ["QT_QPA_PLATFORM"] = "windows"
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from test_cs2_settings_center import APP, Host, ready_page
    from rm_client.ui.cs2_settings_center import build_settings_center
    from rm_client.ui.endfield.settings.widgets import SettingsRow
    from rm_client.ui.jello_keybinds import build_keyboard_manager
    host = Host()
    out.mkdir(parents=True,exist_ok=True)
    report = {"platform":APP.platformName(),"dpr":host.devicePixelRatioF(),"cases":[],
              "scope":"native Qt preferences with inert device adapters; no external-window screenshot or game process"}
    try:
        for width,height in sizes:
            host.resize(width,height)
            host.show()
            dialog = build_settings_center(host)
            try:
                dialog.resize(width,height)
                dialog.show()
                QTest.qWait(550)
                for index in range(dialog.tabs.count()):
                    if index:
                        bar = dialog.navigation
                        QTest.mouseClick(bar, Qt.MouseButton.LeftButton,
                                         pos=bar.tabRect(index).center())
                        assert bar.currentIndex() == index
                        assert dialog.tabs.currentIndex() == index
                        ready_page(dialog,dialog.tabs.tabText(index))
                    else:
                        dialog.tabs.setCurrentIndex(0)
                    QTest.qWait(400)
                    name = f"{width}x{height}_page_{index}.png"
                    if not dialog.grab().save(str(out/name)):
                        raise RuntimeError("Could not save native settings capture")
                    rows = dialog.tabs.currentWidget().findChildren(SettingsRow)
                    assert rows
                    for row in rows:
                        assert row.control.geometry().right() <= row.width()
                        assert row.label.width()>30
                    report["cases"].append({"size":[width,height],"page":dialog.tabs.tabText(index),"rows":len(rows),"capture":name})
                page = ready_page(dialog,"键盘 / 鼠标")
                editor = build_keyboard_manager(host,page,parent=dialog)
                try:
                    editor.show()
                    QTest.qWait(550)
                    editor.grab().save(str(out/f"{width}x{height}_bindings.png"))
                    stops = host.backend.trigger_estop.call_count
                    QTest.keyClick(editor,Qt.Key.Key_F12)
                    assert host.backend.trigger_estop.call_count == stops+1
                    editor.reject()
                    QTest.qWait(200)
                    assert not editor.isVisible()
                finally:
                    editor.hide()
                    editor.deleteLater()
                dialog.tabs.setCurrentIndex(0)
                QTest.qWait(400)
                combo = dialog.tabs.widget(0).controls["hud_hz"]
                check_dropdown(combo, out/f"{width}x{height}_dropdown.png")
                dialog.reject()
                QTest.qWait(200)
                assert not dialog.isVisible()
                host.backend.arm_match.assert_not_called()
                host.auto_save_config.assert_not_called()
            finally:
                dialog.hide()
                dialog.deleteLater()
                APP.processEvents()
    finally:
        host.hide()
        host.deleteLater()
        APP.processEvents()
    # 单独走真实OpenGL父窗口的冻结模糊分支；仍不创建任何设备服务。
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    class BackdropHost(QOpenGLWidget):
        def paintGL(self):
            functions = self.context().functions()
            functions.glClearColor(.25,.35,.45,1)
            functions.glClear(0x00004000)
    gl_host = BackdropHost()
    fixture = Host()
    for name in ("registry","state","control","backend","frame_scheduler","auto_save_config",
                 "after_setting_changed","invalidate_transient_overlay_scene","toast"):
        setattr(gl_host,name,getattr(fixture,name))
    gl_host.resize(1280,720)
    gl_host.show()
    QTest.qWait(200)
    dialog = build_settings_center(gl_host)
    try:
        assert gl_host.isValid()
        dialog.show()
        QTest.qWait(550)
        assert not dialog.panel.backdrop.isNull()
        dialog.grab().save(str(out/"gl_backdrop.png"))
        check_dropdown(dialog.tabs.widget(0).controls["hud_hz"], out/"gl_dropdown.png")
        report["gl_backdrop"] = {"valid_context":True,"cached_size":[dialog.panel.backdrop.width(),dialog.panel.backdrop.height()]}
    finally:
        dialog.hide()
        dialog.deleteLater()
        gl_host.hide()
        gl_host.deleteLater()
        fixture.deleteLater()
        APP.processEvents()
    report["status"] = "PASS"
    (out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--compact",action="store_true")
    args = parser.parse_args()
    sizes = ((1280,720),) if args.compact else ((1000,620),(1280,720),(1920,1080),(2560,1440))
    print(json.dumps(run(args.out,sizes),ensure_ascii=True))
