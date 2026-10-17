"""Native QObject ownership only; no physical audio-driver release claim."""
from pathlib import Path
import gc
import os
import sys
import tempfile
import unittest
import wave
import weakref
from unittest.mock import Mock, patch

os.environ.update(QT_QPA_PLATFORM="offscreen", CI="true", POLAR_DISABLE_MEDIA_PRELOAD="1",
                  POLAR_DISABLE_LOCAL_CAPTURE="1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer
import shiboken6
from rm_client.ui_audio import ReliableAudioPool, UiAudioManager
from rm_client.ui import polar_app
from window_lifecycle_helpers import close_window_safely

APP = QApplication.instance() or QApplication([])


def drain():
    for _ in range(10):
        gc.collect()
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class AudioPoolLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, POLAR_USER_DATA_ROOT=str(self.root / "user"))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        cue = self.root / "polar_classic/module_on.wav"
        cue.parent.mkdir()
        with wave.open(str(cue), "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(8000)
            sound.writeframes(b"\x00\x00" * 80)

    def assert_retired(self, refs):
        drain()
        retained = [ref() for ref in refs if ref() is not None and shiboken6.isValid(ref())]
        # Rescue a red probe only after observation; fixtures must not leak.
        for owner in retained:
            owner.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(len(retained), 0, "audio signal callbacks retained valid native QObjects")

    def test_dropped_successful_feedback_factory_does_not_retain_pool(self):
        def construct():
            manager = UiAudioManager(self.root, custom_root=self.root / "custom")
            self.assertEqual(sum(map(len, manager.pool._effects.values())), 2)
            return [weakref.ref(manager.pool), *(weakref.ref(effect)
                for effects in manager.pool._effects.values() for effect in effects)]
        self.assert_retired(construct())

    def assert_media_path_retires(self, *, preload):
        path = self.root / "test.mp3"
        path.write_bytes(b"fixture; native decoding is disabled")
        def construct():
            pool = ReliableAudioPool(self.root)
            pool._on_media_error = Mock()
            # Exercise real QObject parent/signal ownership at both production
            # connection sites without starting a codec, playback or device.
            with patch.object(QMediaPlayer, "setSource"), patch.object(QMediaPlayer, "play"):
                if preload:
                    pool.preload_media(("test.mp3",))
                    player, output = pool._media_effects["test.mp3"][0]
                else:
                    self.assertTrue(pool._play_fallback(path, 50, "fixture"))
                    player, output = pool._fallback[0]
                player.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "fixture error")
                pool._on_media_error.assert_called_once_with(player, "fixture error")
            # Remove mock call history, whose voice argument is intentionally
            # strong and unrelated to the production callback lifetime.
            pool._on_media_error.reset_mock()
            return [weakref.ref(pool), weakref.ref(player), weakref.ref(output)]
        self.assert_retired(construct())

    def test_preloaded_media_connection_does_not_retain_pool(self):
        self.assert_media_path_retires(preload=True)

    def test_fallback_media_connection_does_not_retain_pool(self):
        self.assert_media_path_retires(preload=False)

    def test_failed_later_window_factory_does_not_retain_feedback_pool(self):
        refs = []
        def feedback(paths):
            from rm_client.ui.polar.window_assembly import FeedbackAssets
            from rm_client.kill_fx import KillFxRenderer
            manager = UiAudioManager(self.root, custom_root=self.root / "custom")
            refs.append(weakref.ref(manager.pool))
            return FeedbackAssets(KillFxRenderer(paths.asset("kill_fx", "gd656")), manager)
        def fail(**kwargs):
            raise RuntimeError("later factory failure")
        def construct():
            with patch.object(polar_app, "create_feedback_assets", feedback), \
                 patch.object(polar_app, "create_vision_services", fail):
                with self.assertRaisesRegex(RuntimeError, "later factory failure"):
                    polar_app.PolarRMWindow()
        construct()
        self.assert_retired(refs)

    def test_successful_window_close_does_not_retain_feedback_pool(self):
        refs = []
        original = polar_app.create_feedback_assets
        def feedback(paths):
            result = original(paths)
            refs.append(weakref.ref(result.ui_audio.pool))
            return result
        def construct_and_close():
            with patch.object(polar_app, "create_feedback_assets", feedback):
                window = polar_app.PolarRMWindow()
            close_window_safely(window, APP)
            window.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        construct_and_close()
        self.assert_retired(refs)


if __name__ == "__main__":
    unittest.main()
