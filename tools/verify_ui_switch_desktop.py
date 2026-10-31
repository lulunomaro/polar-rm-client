"""真实 Windows 窗口验证两套表现层和切换弹窗；设备启动均替换为空操作。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def run(theme, out):
    os.environ.update(QT_QPA_PLATFORM="windows", POLAR_AUTOSTART="0", CI="true",
                      POLAR_DISABLE_LOCAL_CAPTURE="1", POLAR_DISABLE_MEDIA_PRELOAD="1")
    from PySide6.QtCore import Qt, QTimer, QEvent
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    surface = QSurfaceFormat()
    surface.setVersion(3, 3)
    surface.setProfile(QSurfaceFormat.CoreProfile)
    QSurfaceFormat.setDefaultFormat(surface)
    app = QApplication([])
    out.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as profile, patch.dict(os.environ, POLAR_USER_DATA_ROOT=profile):
        from rm_client.ui.ui_theme import save_theme
        save_theme(theme)
        from rm_client.backend.mqtt_backend import RmMqttBackend
        from rm_client.backend.serial_backend import RmSerialBackend
        from rm_client.backend.video_backend import RmVideoBackend
        from rm_client.ui.polar_app import PolarRMWindow
        from rm_client.ui.cs2_settings_center import build_settings_center
        from rm_client.ui.jello_keybinds import build_keyboard_manager
        from rm_client.ui.ui_switch import open_ui_switch
        from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

        def until(predicate, timeout=15):
            deadline = time.monotonic() + timeout
            while not predicate():
                if time.monotonic() >= deadline:
                    raise TimeoutError("UI smoke timed out")
                app.processEvents()
                time.sleep(.005)

        with patch.object(RmMqttBackend, "start"), patch.object(RmSerialBackend, "start"), \
                patch.object(RmVideoBackend, "start"):
            window = PolarRMWindow()
            report = {"theme": theme, "scope": "Windows GPU with inert transports"}
            try:
                window.show()
                assert QTest.qWaitForWindowExposed(window, 5000)
                settle_window_lifecycle(window, app)
                until(lambda: window._endfield_menu.ready or window._endfield_menu.error)
                assert not window._endfield_menu.error, window._endfield_menu.error
                window.home_visible = False
                window.set_clickgui_visible(True, announce=False)
                until(lambda: window.gui_timeline.progress >= .999)
                QTest.qWait(250)
                assert window.gpu_compositor.available, window.gpu_compositor.last_error
                window.grabFramebuffer().save(str(out / "function_list.png"))
                report["renderer"] = type(window.jello_clickgui).__name__
                assert report["renderer"] == ("JelloClickGuiRenderer" if theme == "jello" else "EndfieldWikiRenderer")
                window.set_clickgui_visible(False, announce=False)
                window.set_escape_menu_visible(True)
                until(lambda: window._endfield_menu.phase == 1)
                dialog = build_settings_center(window)
                try:
                    dialog.show()
                    for page in (*dialog.pages.values(), dialog.crosshair):
                        dialog.tabs.setCurrentWidget(page)
                        until(lambda: page.builder.ready or page.builder.error)
                        assert not page.builder.error, page.builder.error
                    dialog.tabs.setCurrentIndex(0)
                    QTest.qWait(300)
                    dialog.grab().save(str(out / "settings.png"))
                    report["settings_pages"] = dialog.tabs.count()
                finally:
                    dialog.close_for_shutdown()
                    dialog.deleteLater()
                keyboard = build_keyboard_manager(window)
                try:
                    keyboard.show()
                    QTest.qWait(650)
                    keyboard.grab().save(str(out / "keybinds.png"))
                    report["keyboard"] = keyboard.objectName()
                finally:
                    keyboard.hide()
                    keyboard.deleteLater()
                captured = []
                def cancel_confirmation():
                    active = app.activeModalWidget()
                    if active is None:
                        QTimer.singleShot(20, cancel_confirmation)
                        return
                    assert active.objectName() == "UISwitchConfirmation"
                    active.grab().save(str(out / "confirmation.png"))
                    captured.append(active.target_theme)
                    active.reject()
                QTimer.singleShot(600, cancel_confirmation)
                open_ui_switch(window)
                assert captured == ["endfield" if theme == "jello" else "jello"]
                assert window.escape_menu_visible
                assert not getattr(app, "_polar_ui_restart", None)
                report["cancel_keeps_menu"] = True
                report["target"] = captured[0]
                report["result"] = "PASS"
            finally:
                close_window_safely(window, app)
                report["retired"] = window._endfield_menu.handle == 0
                (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                window.deleteLater()
                app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
            print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme", choices=("jello", "endfield"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.theme, args.out.resolve())
