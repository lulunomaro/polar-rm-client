"""Window-owned shortcuts that must precede page, modal and binding input."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget


def set_fullscreen(host, enabled: bool) -> None:
    """Keep Qt's normal geometry and maximized state across every entry route."""
    if enabled == host.isFullScreen():
        return
    if enabled:
        host._fullscreen_restore_geometry = (host.normalGeometry(), host.isMaximized())
        host.showFullScreen()
    else:
        geometry = getattr(host, "_fullscreen_restore_geometry", None)
        if geometry is not None and geometry[1]:
            # Going through showNormal() first can overwrite Qt's restore
            # geometry when the platform delivers queued state/resize events.
            host.showMaximized()
        else:
            host.showNormal()
            if geometry is not None and geometry[0].isValid():
                host.setGeometry(geometry[0])
        host._fullscreen_restore_geometry = None


def toggle_fullscreen(host) -> None:
    # F11 is a transient window action, not a saved resolution/quality change.
    # Release held driving input before Qt can change focus or synchronously
    # repaint. Returning to a window never restores input or arms the vehicle.
    host.backend.lock_control("fullscreen toggled")
    host.control.release_all(host.state, host.registry)
    host._control_mouse_point = None
    host.tactical_map_hold = False
    host.invalidate_transient_overlay_scene()
    set_fullscreen(host, not host.isFullScreen())
    host.invalidate_transient_overlay_scene()
    host.frame_scheduler.force()
    host.update()


class WindowShortcuts(QObject):
    """Own F11 for one client window, including its child modal dialogs.

    QShortcut alone loses to ShortcutOverride/key-capture widgets and modal
    windows. An application filter sees those first; the ancestry check keeps
    unrelated windows untouched. Qt removes the filter when its host dies.
    """
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if (event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress,
                            QEvent.Type.KeyRelease)
                and event.key() == Qt.Key.Key_F11
                and isinstance(watched, QWidget)
                and self.host.isVisible()
                and self._owns(watched)):
            if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                toggle_fullscreen(self.host)
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _owns(self, receiver):
        # QWidget.isAncestorOf deliberately stops at child window boundaries,
        # so use QObject ownership to include dialogs and combo popup windows.
        while receiver is not None:
            if receiver is self.host:
                return True
            receiver = receiver.parent()
        return False
