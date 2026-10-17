"""Poll the production window lifecycle in Qt tests without synchronous teardown."""
from __future__ import annotations

import time


def settle_window_lifecycle(window, app, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        window._poll_backend_lifecycle()
        app.processEvents()
        if not window.backend.lifecycle_pending and not window.configuration_change_pending:
            failures = window.backend.lifecycle_snapshot.failures
            if failures:
                raise AssertionError(f"Window lifecycle failed: {failures}")
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"Window lifecycle did not settle: {window.backend.lifecycle_snapshot}")
        time.sleep(.001)


def close_window_safely(window, app, timeout: float = 5.0) -> None:
    if window._window_close_finalized:
        return
    window.close()
    deadline = time.monotonic() + timeout
    while not window._window_close_finalized:
        window._poll_backend_lifecycle()
        app.processEvents()
        if window._window_close_finalized:
            return
        snapshot = window.backend.lifecycle_snapshot
        if snapshot.failures and not window.backend.lifecycle_pending:
            raise AssertionError(f"Window shutdown failed: {snapshot.failures}")
        if time.monotonic() >= deadline:
            raise AssertionError(f"Window shutdown did not settle: {snapshot}")
        time.sleep(.001)
