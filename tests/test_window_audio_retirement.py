"""Window close must own startup audio decode work, even in a reused Qt app.

The decode latch stands in for file/process work. No codec or physical audio
output is started; real executor, window and lifecycle ownership are exercised.
"""
import os
os.environ.update(QT_QPA_PLATFORM="offscreen", CI="true", POLAR_DISABLE_MEDIA_PRELOAD="1",
                  POLAR_DISABLE_LOCAL_CAPTURE="1")
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication
from rm_client import kill_audio
from rm_client.ui.polar_app import PolarRMWindow
from rm_client.backend.mqtt_backend import RmMqttBackend
from rm_client.backend.serial_backend import RmSerialBackend
from rm_client.backend.video_backend import RmVideoBackend

APP = QApplication.instance() or QApplication([])


class WindowAudioRetirementTests(unittest.TestCase):
    def test_blocked_audio_decode_keeps_window_owned_until_worker_retires(self):
        entered, release = threading.Event(), threading.Event()
        warnings = []
        def decode(cache, source):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("fixture decode was not released")
            return source
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, POLAR_USER_DATA_ROOT=directory))
            stack.enter_context(patch.object(kill_audio, "resolve_kill_decoder", return_value=sys.executable))
            stack.enter_context(patch.object(kill_audio, "_media_preload_disabled", return_value=False))
            stack.enter_context(patch.object(kill_audio.PcmCueCache, "decode", decode))
            for backend in (RmMqttBackend, RmSerialBackend, RmVideoBackend):
                stack.enter_context(patch.object(backend, "start"))
            stack.enter_context(patch.object(PolarRMWindow, "_show_shutdown_warning", lambda _, m: warnings.append(m)))
            stack.enter_context(patch.object(PolarRMWindow, "_show_shutdown_cleanup_warning", lambda _, m: warnings.append(m)))
            window = PolarRMWindow()
            pool = window.kill_fx.sounds
            heartbeat = []
            timer = QTimer()
            timer.timeout.connect(lambda: heartbeat.append(1))
            timer.start(1)
            try:
                self.assertTrue(entered.wait(1))
                window._abort_startup()
                self.assertTrue(pool._closed, "logical close must seal audio before native retirement")
                deadline = time.monotonic() + .1
                while time.monotonic() < deadline:
                    APP.processEvents()
                    time.sleep(.001)
                self.assertFalse(window._window_close_finalized,
                    "a live decode worker is still an owned resource")
                self.assertIn("kill-audio-decode", window.backend.lifecycle_snapshot.pending_resources)
                self.assertTrue(heartbeat, "decoder retirement must not block the Qt owner thread")
                release.set()
                deadline = time.monotonic() + 3
                while not window._window_close_finalized and time.monotonic() < deadline:
                    APP.processEvents()
                    time.sleep(.001)
                self.assertTrue(window._window_close_finalized)
                self.assertFalse(any(worker.is_alive() for worker in pool._executor._threads))
                self.assertEqual(pool._decoded, {}, "a closing pool must not admit late decode completions")
                self.assertEqual(warnings, [])
            finally:
                timer.stop()
                # Fixture rescue follows observations, including on the red
                # baseline where window close never admitted the audio owner.
                pool.close()
                release.set()
                pool._executor.shutdown(wait=True, cancel_futures=True)
                deadline = time.monotonic() + 3
                while not window._window_close_finalized and time.monotonic() < deadline:
                    APP.processEvents()
                    time.sleep(.001)
                window.deleteLater()
                APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
