"""Retained cleanup for final timer admission before construction returns.

Only native transport starts and one terminal timer fault are substituted.
The baseline expected failure documented an actual launch escape.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WindowConstructorFailureTests(unittest.TestCase):
    def probe_timer_admission(self, fault="after") -> dict:
        source = '''
            from contextlib import ExitStack
            import json
            import os
            import tempfile
            import time
            import weakref
            from unittest.mock import patch
            os.environ.update(CI="true", QT_QPA_PLATFORM="offscreen",
                              POLAR_DISABLE_LOCAL_CAPTURE="1",
                              POLAR_DISABLE_MEDIA_PRELOAD="1", POLAR_AUTOSTART="1")
            with tempfile.TemporaryDirectory() as directory:
                os.environ["POLAR_USER_DATA_ROOT"] = directory
                import shiboken6
                from PySide6.QtCore import QEvent, QTimer
                from PySide6.QtWidgets import QApplication
                from rm_client.ui.polar_app import PolarRMWindow
                from rm_client.ui.polar import qt_application
                from rm_client.ui.polar.qt_application import launch_qt_application
                from rm_client.backend.mqtt_backend import RmMqttBackend
                from rm_client.backend.serial_backend import RmSerialBackend
                from rm_client.backend.video_backend import RmVideoBackend
                app = QApplication([])
                fault = os.environ["POLAR_TEST_TIMER_FAULT"]
                class LaunchFailure(RuntimeError):
                    def add_note(self, note):
                        if fault == "diagnostics":
                            raise RuntimeError("exception notes unavailable")
                        super().add_note(note)
                failure = LaunchFailure("frame timer admission failed: " + fault)
                original_start = QTimer.start
                references = []
                fault_injected = False
                network_attempts = []
                def reject_network(event, arguments):
                    if event in {"socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo"}:
                        network_attempts.append(event)
                        raise AssertionError("Constructor probe must remain offline: " + event)
                import sys
                sys.addaudithook(reject_network)
                def start_timer(timer, *args):
                    global fault_injected
                    parent = timer.parent()
                    target = (isinstance(parent, PolarRMWindow)
                              and not timer.isSingleShot()
                              and args == (parent._render_timer_interval_ms,))
                    if target and not fault_injected:
                        references.append(weakref.ref(parent))
                        fault_injected = True
                        if fault not in {"before", "unavailable"}:
                            original_start(timer, *args)
                        raise failure
                    if (fault == "unavailable" and isinstance(parent, PolarRMWindow)
                            and not timer.isSingleShot()):
                        raise RuntimeError("cleanup timer unavailable")
                    original_start(timer, *args)
                with (patch.object(QTimer, "start", start_timer),
                      patch.object(RmMqttBackend, "start") as mqtt,
                      patch.object(RmSerialBackend, "start"),
                      patch.object(RmVideoBackend, "start"), ExitStack() as stack):
                    if fault == "diagnostics":
                        original_retain = qt_application._retain_window_until_close
                        original_abort = PolarRMWindow._abort_startup
                        def retain_then_fail(application, window):
                            original_retain(application, window)
                            raise RuntimeError("retention post-effect failure")
                        def abort_then_fail(window):
                            original_abort(window)
                            raise RuntimeError("abort post-effect failure")
                        stack.enter_context(patch.object(qt_application, "_retain_window_until_close",
                                                         retain_then_fail))
                        stack.enter_context(patch.object(PolarRMWindow, "_abort_startup", abort_then_fail))
                        stack.enter_context(patch.object(qt_application._LOG, "error",
                                                         side_effect=RuntimeError("log unavailable")))
                    try:
                        launch_qt_application(PolarRMWindow)
                    except RuntimeError as caught:
                        assert caught is failure, "cleanup must preserve the constructor error"
                    else:
                        raise AssertionError("constructor fault must remain visible")
                    window = references[0]()
                    assert window is not None
                    result = {
                        "timer_published": "timer" in window.__dict__,
                        "active_timers": sum(t.isActive() for t in window.findChildren(QTimer)),
                        "closing": window._window_lifecycle.closing,
                        "retained": id(window) in getattr(app, "_polar_client_windows", {}),
                        "pending_before_events": window.backend.lifecycle_pending,
                        "starts_before_events": mqtt.call_count,
                    }
                    try:
                        deadline = time.monotonic() + (.05 if fault == "unavailable" else 2)
                        while not mqtt.call_count and not window._window_close_finalized:
                            app.processEvents()
                            if time.monotonic() >= deadline:
                                break
                            time.sleep(.001)
                        result.update(starts_after_events=mqtt.call_count,
                                      finalized=window._window_close_finalized,
                                      pending_after_events=window.backend.lifecycle_pending)
                        assert network_attempts == [], network_attempts
                    finally:
                        stack.close()
                        # Once observations are complete, restore timer service
                        # so the intentionally unavailable case can be drained.
                        fault = "cleanup"
                        # Recover the lost named timer only for fixture cleanup.
                        # This happens after every behavioral observation above.
                        if shiboken6.isValid(window) and not window._window_close_finalized:
                            timers = [t for t in window.findChildren(QTimer) if not t.isSingleShot()]
                            assert len(timers) == 1
                            if "timer" not in window.__dict__:
                                window.timer = timers[0]
                            window._abort_startup()
                            deadline = time.monotonic() + 5
                            while not window._window_close_finalized:
                                window._poll_backend_lifecycle()
                                app.processEvents()
                                assert time.monotonic() < deadline, "fixture shutdown did not finish"
                                time.sleep(.001)
                        if shiboken6.isValid(window):
                            window.deleteLater()
                        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    print("CONSTRUCTOR_RESULT=" + json.dumps(result, sort_keys=True))
        '''
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT,
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONPATH": str(ROOT), "POLAR_TEST_TIMER_FAULT": fault},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        line = next(line for line in result.stdout.splitlines()
                    if line.startswith("CONSTRUCTOR_RESULT="))
        return json.loads(line.split("=", 1)[1])

    def assert_completed_abort(self, fault):
        result = self.probe_timer_admission(fault)
        self.assertEqual(result, {
            "timer_published": True,
            "active_timers": 1,
            "closing": True,
            "retained": True,
            "pending_before_events": True,
            "starts_before_events": 0,
            "starts_after_events": 0,
            "finalized": True,
            "pending_after_events": False,
        })

    def test_failed_timer_admission_seals_and_retains_before_events(self):
        self.assert_completed_abort("after")

    def test_rejected_timer_admission_uses_cleanup_timer_without_runtime(self):
        self.assert_completed_abort("before")

    def test_cleanup_and_diagnostic_failures_preserve_original_exception(self):
        self.assert_completed_abort("diagnostics")

    def test_unavailable_cleanup_timer_remains_retained_and_explicitly_incomplete(self):
        result = self.probe_timer_admission("unavailable")
        self.assertEqual(result, {
            "timer_published": True,
            "active_timers": 0,
            "closing": True,
            "retained": True,
            "pending_before_events": True,
            "starts_before_events": 0,
            "starts_after_events": 0,
            "finalized": False,
            "pending_after_events": True,
        })


if __name__ == "__main__":
    unittest.main()
