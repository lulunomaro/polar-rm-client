"""Actual failed-launch ownership, with only native activation and show inert."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FailedWindowLaunchTests(unittest.TestCase):
    def probe_launch(self, *, stage: str, owns_application: bool, delayed_cleanup: bool = False) -> dict:
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
                from PySide6.QtWidgets import QApplication
                from rm_client.ui import polar_app
                from rm_client.ui.polar import app_entry
                from rm_client.backend.mqtt_backend import RmMqttBackend
                from rm_client.backend.serial_backend import RmSerialBackend
                from rm_client.backend.video_backend import RmVideoBackend
                from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle
                app = None if OWNS_APPLICATION else QApplication([])
                references = []
                cleanup_entered = threading.Event()
                cleanup_released = threading.Event()
                failure = RuntimeError("failed launch after actual construction")
                class Window(polar_app.PolarRMWindow):
                    def __init__(self):
                        super().__init__()
                        references.append(weakref.ref(self))
                        if DELAYED_CLEANUP:
                            close_camera = self.camera_worker.close
                            def delayed_close():
                                cleanup_entered.set()
                                assert cleanup_released.wait(5), "test did not release native cleanup"
                                close_camera()
                            self.camera_worker.close = delayed_close
                    def show(self):
                        if STAGE == "show":
                            raise failure
                def restore(window):
                    if STAGE == "restore":
                        raise failure
                with (patch.object(polar_app, "PolarRMWindow", Window),
                      patch("rm_client.ui.cs2_video_settings.restore_startup_window", restore),
                      patch.object(RmMqttBackend, "start") as mqtt,
                      patch.object(RmSerialBackend, "start"),
                      patch.object(RmVideoBackend, "start")):
                    try:
                        app_entry.main()
                    except RuntimeError as caught:
                        assert caught is failure
                    else:
                        raise AssertionError("launch failure must remain visible")
                    app = QApplication.instance()
                    window = references[0]()
                    assert window is not None, "exception traceback owns the failed launch frame"
                    result = {
                        "closing": window._window_lifecycle.closing,
                        "retained": id(window) in getattr(app, "_polar_client_windows", {}),
                        "starts_before_poll": mqtt.call_count,
                    }
                    try:
                        if DELAYED_CLEANUP:
                            assert cleanup_entered.wait(1), "native shutdown was never admitted"
                            # No exec/processEvents/owner polling has run. A stopped
                            # launch remains sealed and explicitly incomplete.
                            time.sleep(.02)
                            result["delayed_pending"] = window.backend.lifecycle_pending
                            result["delayed_finalized"] = window._window_close_finalized
                            result["delayed_retained"] = id(window) in app._polar_client_windows
                            result["delayed_starts"] = mqtt.call_count
                            cleanup_released.set()
                        settle_window_lifecycle(window, app)
                        result["starts_after_poll"] = mqtt.call_count
                        result["finalized"] = window._window_close_finalized
                    finally:
                        cleanup_released.set()
                        close_window_safely(window, app)
                    print("LAUNCH_RESULT=" + json.dumps(result))
        '''.replace("OWNS_APPLICATION", repr(owns_application)).replace("STAGE", repr(stage))
        source = source.replace("DELAYED_CLEANUP", repr(delayed_cleanup))
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'tests'}"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        line = next(line for line in result.stdout.splitlines() if line.startswith("LAUNCH_RESULT="))
        return json.loads(line.split("=", 1)[1])

    def test_failed_show_revokes_pending_activation_and_retains_until_close(self):
        result = self.probe_launch(stage="show", owns_application=False)
        self.assertEqual(result, {
            "closing": True, "retained": True, "starts_before_poll": 0,
            "starts_after_poll": 0, "finalized": True,
        })

    def test_failed_placement_revokes_pending_activation_in_new_application(self):
        result = self.probe_launch(stage="restore", owns_application=True)
        self.assertEqual(result, {
            "closing": True, "retained": True, "starts_before_poll": 0,
            "starts_after_poll": 0, "finalized": True,
        })


    def test_new_application_retains_delayed_shutdown_without_running_an_event_loop(self):
        result = self.probe_launch(stage="restore", owns_application=True, delayed_cleanup=True)
        self.assertEqual(result, {
            "closing": True, "retained": True, "starts_before_poll": 0,
            "delayed_pending": True, "delayed_finalized": False,
            "delayed_retained": True, "delayed_starts": 0,
            "starts_after_poll": 0, "finalized": True,
        })

    def test_existing_application_retains_delayed_shutdown_until_owner_poll(self):
        result = self.probe_launch(stage="show", owns_application=False, delayed_cleanup=True)
        self.assertEqual(result, {
            "closing": True, "retained": True, "starts_before_poll": 0,
            "delayed_pending": True, "delayed_finalized": False,
            "delayed_retained": True, "delayed_starts": 0,
            "starts_after_poll": 0, "finalized": True,
        })


if __name__ == "__main__":
    unittest.main()
