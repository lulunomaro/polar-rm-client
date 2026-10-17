"""Off-thread thumbnail decoding with explicit native capture retirement."""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from PySide6.QtGui import QImage

from .backend.resource_close import release_capture


class ThumbnailDecoder:
    """Own failed capture releases independently of UI pixmaps and futures."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loads_finished = threading.Condition(self._lock)
        self._close_lock = threading.Lock()
        self._active_loads = 0
        self._closed = False
        self._unreleased_captures: list[tuple[Any, Exception]] = []
        self.last_error = ""
        self._close_errors_reported = False

    def load(self, path: Path) -> QImage:
        with self._loads_finished:
            if self._closed or self._unreleased_captures:
                return QImage()
            self._active_loads += 1
        try:
            return self._decode(path)
        finally:
            with self._loads_finished:
                self._active_loads -= 1
                self._loads_finished.notify_all()

    def _decode(self, path: Path) -> QImage:
        capture = None
        try:
            import cv2  # type: ignore

            capture = cv2.VideoCapture(str(path))
            ok, frame = capture.read()
            if not ok:
                return QImage()
            height, width = frame.shape[:2]
            if width > 480:
                ratio = 480 / float(width)
                frame = cv2.resize(frame, (480, max(2, round(height * ratio))), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width, channels = rgb.shape
            return QImage(rgb.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return QImage()
        finally:
            if capture is not None:
                try:
                    release_capture(capture)
                except Exception as exc:
                    with self._lock:
                        self._unreleased_captures.append((capture, exc))
                        self.last_error = f"Thumbnail capture release failed: {type(exc).__name__}: {exc}"

    def close(self) -> None:
        """Report once, then retry only on a later explicit shutdown request."""
        # Executor.submit can raise after native worker admission but before
        # registering that worker. Own decode activity independently, and deny
        # a delayed task native entry even if executor shutdown already returned.
        with self._close_lock:
            with self._loads_finished:
                self._closed = True
                while self._active_loads:
                    self._loads_finished.wait()
            self._close_captures()

    def _close_captures(self) -> None:
        if self._close_errors_reported:
            with self._lock:
                failures = tuple(self._unreleased_captures)
            retained = []
            for capture, _ in failures:
                try:
                    release_capture(capture)
                except Exception as exc:
                    retained.append((capture, exc))
            with self._lock:
                self._unreleased_captures = retained
        try:
            self.raise_if_unreleased()
        except RuntimeError:
            self._close_errors_reported = True
            raise
        else:
            self._close_errors_reported = False

    def raise_if_unreleased(self) -> None:
        """Call after the executor drains; failed owners remain reachable here."""
        with self._lock:
            failures = tuple(self._unreleased_captures)
        if failures:
            messages = "; ".join(f"{type(error).__name__}: {error}" for _, error in failures)
            raise RuntimeError(f"Thumbnail capture release failed: {messages}") from failures[0][1]
