"""Esc 界面切换确认，以及正常关机完成后的自动拉起。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from .emergency_shortcut import install_estop_shortcut
from .ui_theme import is_jello, read_theme, save_theme


def build_switch_dialog(host):
    target = "endfield" if is_jello() else "jello"
    name = "Endfield UI" if target == "endfield" else "Jello UI"
    if is_jello():
        from .jello_widgets import JelloDialog, apply_jello_widgets
        dialog = JelloDialog(host)
        root = dialog.panel.layout()
    else:
        dialog = QDialog(host)
        from .fullscreen_composition import FullscreenDialogGuard
        dialog._fullscreen_guard = FullscreenDialogGuard(host.window(), dialog)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(28, 24, 28, 24)
        dialog.setStyleSheet(
            "QDialog {background:white;} QLabel {color:black;background:white;font-size:16px;}"
            "QPushButton {color:black;background:white;border:1px solid black;padding:9px 20px;}"
            "QPushButton:focus,QPushButton:hover {background:#eeeeee;}"
        )
    dialog.target_theme = target
    dialog.setObjectName("UISwitchConfirmation")
    dialog.setWindowTitle("切换 UI")
    dialog.resize(620, 340 if is_jello() else 230)
    root.addWidget(QLabel(f"确认切换到 {name}？"))
    text = QLabel("确认后将保存配置，自动关闭并重新启动终端。\n连接将重新建立，操控保持锁定。")
    text.setWordWrap(True)
    root.addWidget(text, 1)
    buttons = QHBoxLayout()
    cancel = QPushButton("取消")
    accept = QPushButton("确认切换")
    cancel.setObjectName("UISwitchCancel")
    accept.setObjectName("UISwitchConfirm")
    cancel.clicked.connect(dialog.reject)
    accept.clicked.connect(dialog.accept)
    buttons.addStretch()
    buttons.addWidget(cancel)
    buttons.addWidget(accept)
    root.addLayout(buttons)
    if is_jello():
        apply_jello_widgets(dialog)
        dialog.focus_widget = cancel
    for button in (cancel, accept):
        button.setAutoDefault(False)
    cancel.setDefault(True)
    cancel.setFocus(Qt.FocusReason.OtherFocusReason)
    dialog.estop_shortcut = install_estop_shortcut(dialog, host)
    return dialog


def open_ui_switch(host) -> None:
    lifecycle = getattr(host, "_window_lifecycle", None)
    if lifecycle is not None and lifecycle.closing:
        return
    host.backend.lock_control("UI switch confirmation")
    host._neutralize_modal_inputs()
    dialog = build_switch_dialog(host)
    target = dialog.target_theme
    try:
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()
        host._neutralize_modal_inputs()
        host.frame_scheduler.force()
    if accepted and not (lifecycle is not None and lifecycle.closing):
        request_ui_restart(host, target)


def request_ui_restart(host, target: str) -> None:
    """先保存偏好，再走原有异步释放流程；清理未完成时绝不启动第二个终端。"""
    app = QApplication.instance()
    previous = read_theme()
    try:
        save_theme(target)
    except OSError as error:
        QMessageBox.warning(host, "未切换 UI", "界面偏好保存失败：" + str(error))
        return
    app._polar_ui_restart = (host.paths, host._window_lifecycle)
    host.close()
    if not host._window_lifecycle.closing:
        app._polar_ui_restart = None
        save_theme(previous)


def finish_ui_restart(app) -> bool:
    """仅在 Qt 主循环退出、窗口连接清理完成后运行。"""
    request = getattr(app, "_polar_ui_restart", None)
    if request is None:
        return False
    app._polar_ui_restart = None
    paths, lifecycle = request
    if not (lifecycle.ready_to_close and lifecycle.finalized):
        return False
    executable = sys.executable
    frozen = bool(getattr(sys, "frozen", False))
    if not frozen and sys.platform == "win32":
        # 源码运行时沿用同一虚拟环境的 GUI 解释器，避免额外弹出控制台。
        windowed = Path(executable).with_name("pythonw.exe")
        if windowed.is_file():
            executable = str(windowed)
    arguments = sys.argv[1:] if frozen else [str(paths.app_root / "main.py")]
    started, _pid = QProcess.startDetached(executable, arguments, str(paths.app_root))
    if not started:
        QMessageBox.critical(None, "终端重新启动失败", "UI 偏好已保存，请手动重新打开终端。")
    return started
