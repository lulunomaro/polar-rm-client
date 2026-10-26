"""Launch a supplied Qt window without depending on the client implementation.

The caller provides construction; this boundary configures a newly owned Qt
application, restores placement, retains top-level wrappers and runs only an
application event loop it created. Compatibility modules depend on this module,
never the other way around.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable, Protocol, runtime_checkable

from PySide6.QtCore import Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication, QWidget


_LOG = logging.getLogger(__name__)


@runtime_checkable
class StartupAbort(Protocol):
    """A constructed window that can revoke work after launch admission fails."""

    def _abort_startup(self) -> None: ...


def _report_abort_failure(primary: BaseException, action: str, failure: BaseException) -> None:
    # Diagnostic sinks are secondary to the original launch failure. Neither a
    # custom exception nor a broken logging handler may replace that exception.
    try:
        primary.add_note(f"Failed-launch {action}: {type(failure).__name__}: {failure}")
    except BaseException:
        pass
    try:
        _LOG.error("Failed-launch %s failed", action,
                   exc_info=(type(failure), failure, failure.__traceback__))
    except BaseException:
        pass


def _abort_failed_launch(application: QApplication, window: QWidget,
                         abort: Callable[[], None], primary: BaseException) -> None:
    # Retention writes the strong owner first. Even when Qt attribute/signal
    # setup fails, independently request shutdown and preserve that ownership.
    try:
        _retain_window_until_close(application, window)
    except BaseException as failure:
        _report_abort_failure(primary, "retention", failure)
    try:
        abort()
    except BaseException as failure:
        _report_abort_failure(primary, "abort", failure)


def _retain_window_until_close(application: QApplication, window: QWidget) -> None:
    """Keep top-level Python wrappers alive when reusing a host event loop."""
    windows = getattr(application, "_polar_client_windows", None)
    if windows is None:
        windows = {}
        application._polar_client_windows = windows
    window_id = id(window)
    windows[window_id] = window
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    window.destroyed.connect(lambda: windows.pop(window_id, None))


def launch_qt_application(window_factory: Callable[[], QWidget]) -> None:
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        # 原公告页与主 HUD 的 OpenGL 控件共用 Qt 全局上下文。
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        surface = QSurfaceFormat()
        surface.setVersion(3, 3)
        surface.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        surface.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
        # Configure swaps from the saved preference before creating any GL
        # context. The window's frame scheduler separately controls HUD updates.
        from ..cs2_video_settings import configure_surface
        from ..cs2_video_settings import current_preferences
        from ..graphics_devices import configure_process_graphics
        configure_process_graphics(current_preferences().render_adapter)
        configure_surface(surface)
        surface.setDepthBufferSize(0)
        surface.setStencilBufferSize(0)
        QSurfaceFormat.setDefaultFormat(surface)
        app = QApplication(sys.argv)
    app.setApplicationName("POLAR-RM Custom Client")
    window = window_factory()
    try:
        window.show()
        from ..cs2_video_settings import restore_startup_window
        restore_startup_window(window)
        _retain_window_until_close(app, window)
    except BaseException as failure:
        if isinstance(window, StartupAbort):
            _abort_failed_launch(app, window, window._abort_startup, failure)
        raise
    if owns_app:
        raise SystemExit(app.exec())
