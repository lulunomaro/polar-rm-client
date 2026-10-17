"""Failed fully assembled activation must retain an owner and drain real teardown.

Native starts are inert; profile staging, lifecycle, Qt timers and abort are real.
Each probe is isolated because a broken constructor cannot return its owner.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WindowActivationFailureTests(unittest.TestCase):
    def probe(self, fault):
        source = '''
            import os, tempfile, time, json, faulthandler
            faulthandler.dump_traceback_later(10, repeat=True)
            from unittest.mock import patch
            os.environ.update(CI="true", QT_QPA_PLATFORM="offscreen",
                POLAR_DISABLE_LOCAL_CAPTURE="1", POLAR_DISABLE_MEDIA_PRELOAD="1")
            with tempfile.TemporaryDirectory() as directory:
                os.environ["POLAR_USER_DATA_ROOT"] = directory
                from PySide6.QtWidgets import QApplication
                from PySide6.QtCore import QTimer
                from rm_client.ui import polar_app
                from rm_client.ui.polar_app import PolarRMWindow
                from rm_client.ui.polar.qt_application import launch_qt_application
                from rm_client.ui.window_shortcuts import WindowShortcuts
                from rm_client.backend.mqtt_backend import RmMqttBackend
                from rm_client.backend.serial_backend import RmSerialBackend
                from rm_client.backend.video_backend import RmVideoBackend
                app = QApplication([])
                owners = []
                failure = RuntimeError("activation boundary failed")
                fault = os.environ["POLAR_ACTIVATION_FAULT"]
                original_load = PolarRMWindow.load_startup_config
                original_timer = polar_app.create_frame_timer
                def fail_shortcuts(shortcuts, host):
                    owners.append(host)
                    raise failure
                def fail_profile(host):
                    original_load(host)
                    owners.append(host)
                    raise failure
                def fail_timer(host, callback):
                    if not owners or fault == "timer_unavailable":
                        if not owners:
                            owners.append(host)
                        raise failure
                    return original_timer(host, callback)
                selected = (patch.object(WindowShortcuts, "__init__", fail_shortcuts)
                    if fault in {"shortcuts", "two_windows"} else patch.object(PolarRMWindow, "load_startup_config", fail_profile)
                    if fault == "profile" else patch.object(polar_app, "create_frame_timer", fail_timer))
                network = []
                def reject_network(event, arguments):
                    if event in {"socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo"}:
                        network.append(event)
                        raise AssertionError("unexpected native network attempt")
                import sys
                sys.addaudithook(reject_network)
                healthy = None
                if fault == "two_windows":
                    with patch.object(PolarRMWindow, "load_startup_config"), patch.dict(os.environ, POLAR_AUTOSTART="0"):
                        healthy = PolarRMWindow()
                    healthy.timer.stop()
                with selected, patch.object(RmMqttBackend, "start") as mqtt, \\
                        patch.object(RmSerialBackend, "start"), patch.object(RmVideoBackend, "start"):
                    try:
                        launch_qt_application(PolarRMWindow)
                    except RuntimeError as caught:
                        assert caught is failure
                    else:
                        raise AssertionError("constructor failure was hidden")
                    window = owners[0]
                    result = dict(timer="timer" in window.__dict__, closing=window._window_lifecycle.closing,
                        retained=id(window) in getattr(app, "_polar_client_windows", {}),
                        pending=window.backend.lifecycle_pending)
                    deadline = time.monotonic() + (3 if result["closing"] and result["timer"] else .05)
                    while not window._window_close_finalized and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(.001)
                    result.update(finalized=window._window_close_finalized,
                        pending_after_events=window.backend.lifecycle_pending, starts=mqtt.call_count)
                    # Rescue only after observations, so the original leak is
                    # testable without abandoning workers at subprocess exit.
                    if not window._window_close_finalized:
                        window.timer = original_timer(window, window.on_tick)
                        window._abort_startup()
                        deadline = time.monotonic() + 3
                        while not window._window_close_finalized and time.monotonic() < deadline:
                            app.processEvents()
                            time.sleep(.001)
                        assert window._window_close_finalized
                    if healthy is not None:
                        assert not healthy._window_lifecycle.closing
                        assert not healthy._window_close_finalized
                        assert healthy.isEnabled()
                        assert not healthy.kill_fx.sounds._closed
                        assert not healthy.timer.isActive()
                        assert id(healthy) not in getattr(app, "_polar_client_windows", {})
                        healthy._abort_startup()
                        deadline = time.monotonic() + 3
                        while not healthy._window_close_finalized and time.monotonic() < deadline:
                            app.processEvents()
                            time.sleep(.001)
                        assert healthy._window_close_finalized
                    assert not network
                    print(json.dumps(result), flush=True)
        '''
        try:
            result = subprocess.run([sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT,
                env=dict(os.environ, POLAR_ACTIVATION_FAULT=fault), text=True,
                capture_output=True, timeout=30)
        except subprocess.TimeoutExpired as failure:
            # subprocess.run preserves captured bytes on timeout but unittest's
            # default traceback omits them. Keep native/Python hang evidence.
            print(f"Probe {fault} stdout: {failure.stdout!r}", file=sys.stderr)
            print(f"Probe {fault} stderr: {failure.stderr!r}", file=sys.stderr)
            raise
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_profile_post_effect_failure_retains_and_retires_owner(self):
        self.assert_retirement("profile")

    def test_shortcut_installation_failure_retains_and_retires_owner(self):
        self.assert_retirement("shortcuts")

    def test_frame_timer_creation_failure_retains_and_retires_owner(self):
        self.assert_retirement("timer")

    def test_failed_abort_timer_retains_blocked_owner_without_starting_runtime(self):
        self.assertEqual(self.probe("timer_unavailable"), dict(timer=False, closing=True,
            retained=True, pending=True, finalized=False, pending_after_events=True, starts=0))

    def test_failed_window_does_not_close_or_retain_another_window(self):
        self.assert_retirement("two_windows")

    def assert_retirement(self, fault):
        result = self.probe(fault)
        self.assertEqual(result, dict(timer=True, closing=True, retained=True, pending=True,
            finalized=True, pending_after_events=False, starts=0))


if __name__ == "__main__":
    unittest.main()
