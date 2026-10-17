"""Qt dialog-lifetime F12 adapter shared by independent preference dialogs.

The application event filter reaches nested modal dialogs. The dialog owns its
filter and removes it on completion; emergency action sequencing remains in the
Qt-free emergency_actions service.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication

from ..emergency_actions import request_emergency_stop


def install_estop_shortcut(dialog, host):
    # An ordinary WindowShortcut misses nested modal file/import dialogs.
    class EstopFilter(QObject):
        def eventFilter(self, watched, event):
            # Most events are layout/style/paint traffic. Do not enter Qt
            # visibility checks or import Shiboken for every constructed child.
            if event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                return False
            from shiboken6 import isValid
            if event.key() == Qt.Key.Key_F12 and isValid(dialog) and dialog.isVisible():
                if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                    result = request_emergency_stop(host.backend, host.control)
                    if result.failures:
                        host.toast(result.message, kind="warning")
                    else:
                        host.toast(result.message)
                event.accept()
                return True
            return super().eventFilter(watched, event)
    filter_ = EstopFilter(dialog)
    app = QApplication.instance()
    app.installEventFilter(filter_)
    dialog.finished.connect(lambda _result: app.removeEventFilter(filter_))
    return filter_
