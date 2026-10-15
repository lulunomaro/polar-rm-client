"""Cooperative settings construction on Qt's GUI thread, never in a paint call."""
from __future__ import annotations

import time
from PySide6.QtCore import QObject, QTimer, Signal


class SettingsBuildQueue(QObject):
    """One bounded group of construction steps per event-loop turn.

    The 3 ms target is soft: an individual Qt font/style operation is not
    interruptible. At most four steps run before yielding, even on fast hosts.
    Hidden tabs pause and retain their draft; dismissal cancels pending work.
    """
    finished = Signal()

    def __init__(self, owner, steps):
        super().__init__(owner)
        self.owner, self.steps = owner, iter(steps)
        self.ready = False
        self.error = ""
        self.active = False
        self.cancelled = False
        self.slices = 0
        self.last_ms = self.max_ms = 0.0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._run_slice)

    def set_active(self, active):
        self.active = bool(active)
        self.timer.stop()
        if self.active and not (self.ready or self.error or self.cancelled):
            self.timer.start(8)

    def cancel(self):
        self.active = False
        self.cancelled = True
        self.timer.stop()
        close = getattr(self.steps, "close", None)
        if close is not None:
            close()

    def _run_slice(self):
        if not self.active or self.ready or self.error or self.cancelled:
            return
        # Do not compete with Jello's cached entrance/exit animation. The
        # current page resumes as soon as the real panel becomes visible.
        if not self.owner.isVisible():
            self.timer.start(16)
            return
        started = time.perf_counter()
        try:
            for _ in range(4):
                stop_after_step = next(self.steps)
                if stop_after_step is True or time.perf_counter() - started >= .003:
                    break
        except StopIteration:
            self.ready = True
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.cancel()
        finally:
            self.last_ms = (time.perf_counter() - started) * 1000
            self.max_ms = max(self.max_ms, self.last_ms)
            self.slices += 1
        if self.ready or self.error:
            self.finished.emit()
        else:
            self.timer.start(8)
