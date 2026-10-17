"""A failed constructor must retain a genuinely retiring native owner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConstructorDelayedCleanupTests(unittest.TestCase):
    def test_failed_timer_start_waits_for_native_close_without_runtime_activation(self):
        source = '''
            import json
            import os
            import tempfile
            import threading
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
                from rm_client.ui.polar.qt_application import launch_qt_application
                from rm_client.backend.mqtt_backend import RmMqttBackend
                from rm_client.backend.serial_backend import RmSerialBackend
                from rm_client.backend.video_backend import RmVideoBackend
                app = QApplication([])
                entered = threading.Event()
                released = threading.Event()
                references = []
                original_start = QTimer.start
                failure = RuntimeError("final timer admission failed with native owner retiring")
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
                    original_start(timer, *args)
                    parent = timer.parent()
                    if (not fault_injected and isinstance(parent, PolarRMWindow)
                            and not timer.isSingleShot()
                            and timer.interval() == parent._render_timer_interval_ms):
                        references.append(weakref.ref(parent))
                        original_close = parent.camera_worker.close
                        def hold_camera_close():
                            entered.set()
                            assert released.wait(5), "test did not release native cleanup"
                            original_close()
                        parent.camera_worker.close = hold_camera_close
                        fault_injected = True
                        raise failure
                with (patch.object(QTimer, "start", start_timer),
                      patch.object(RmMqttBackend, "start") as mqtt,
                      patch.object(RmSerialBackend, "start"),
                      patch.object(RmVideoBackend, "start")):
                    try:
                        launch_qt_application(PolarRMWindow)
                    except RuntimeError as caught:
                        assert caught is failure, "constructor exception identity changed"
                    else:
                        raise AssertionError("constructor fault disappeared")
                    window = references[0]()
                    assert window is not None
                    def state():
                        return {
                            "closing": window._window_lifecycle.closing,
                            "retained": id(window) in getattr(app, "_polar_client_windows", {}),
                            "pending": window.backend.lifecycle_pending,
                            "finalized": window._window_close_finalized,
                            "starts": mqtt.call_count,
                        }
                    result = {"before_events": state()}
                    try:
                        result["cleanup_admitted"] = entered.wait(1)
                        deadline = time.monotonic() + .08
                        while time.monotonic() < deadline:
                            app.processEvents()
                            time.sleep(.001)
                        result["held_cleanup"] = state()
                        released.set()
                        deadline = time.monotonic() + 5
                        while not window._window_close_finalized and not mqtt.call_count:
                            app.processEvents()
                            assert time.monotonic() < deadline, "Qt did not complete final cleanup"
                            time.sleep(.001)
                        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                        result["after_release"] = state()
                        result["audit_closed"] = window.backend.field_log.snapshot()["closed"]
                        assert network_attempts == [], network_attempts
                    finally:
                        released.set()
                        # Repair missing timer ownership only to drain the pre-fix
                        # counterexample. All observations have already been saved.
                        if shiboken6.isValid(window) and not window._window_close_finalized:
                            if "timer" not in window.__dict__:
                                timers = [t for t in window.findChildren(QTimer) if not t.isSingleShot()]
                                assert len(timers) == 1
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
                    print("DELAYED_CONSTRUCTOR_RESULT=" + json.dumps(result, sort_keys=True))
        '''
        process = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT,
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        line = next(line for line in process.stdout.splitlines()
                    if line.startswith("DELAYED_CONSTRUCTOR_RESULT="))
        result = json.loads(line.split("=", 1)[1])
        retiring = {
            "closing": True, "retained": True, "pending": True,
            "finalized": False, "starts": 0,
        }
        self.assertEqual(result, {
            "before_events": retiring,
            "cleanup_admitted": True,
            "held_cleanup": retiring,
            "after_release": {
                "closing": True, "retained": False, "pending": False,
                "finalized": True, "starts": 0,
            },
            "audit_closed": True,
        })


if __name__ == "__main__":
    unittest.main()
