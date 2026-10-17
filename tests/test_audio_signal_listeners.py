"""Weak signal forwarding while alive, disconnected and natively deleted."""
from pathlib import Path
import gc
import os
import sys
import tempfile
import unittest
import weakref
from unittest.mock import Mock

os.environ.update(QT_QPA_PLATFORM="offscreen", CI="true", POLAR_DISABLE_MEDIA_PRELOAD="1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer, QSoundEffect
from rm_client.ui_audio import ReliableAudioPool, _effect_status_listener, _media_error_listener

APP = QApplication.instance() or QApplication([])


class AudioSignalListenerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pool = ReliableAudioPool(Path(self.directory.name))
        self.effect = QSoundEffect(self.pool)
        self.player = QMediaPlayer(self.pool)
        self.status = _effect_status_listener(self.pool, self.effect, "test.wav")
        self.error = _media_error_listener(self.pool, self.player)
        self.effect.statusChanged.connect(self.status)
        self.player.errorOccurred.connect(self.error)

    def tearDown(self):
        self.pool.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_live_effect_uses_current_handler_and_status(self):
        self.pool._on_status = Mock()
        self.effect.statusChanged.emit()
        self.pool._on_status.assert_called_once_with("test.wav", self.effect, self.effect.status())
        self.pool._on_status = Mock()
        self.effect.statusChanged.emit()
        self.pool._on_status.assert_called_once_with("test.wav", self.effect, self.effect.status())

    def test_live_media_error_retains_message_and_original_voice(self):
        self.pool._on_media_error = Mock()
        self.player.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "decode failed")
        self.pool._on_media_error.assert_called_once_with(self.player, "decode failed")

    def test_explicit_disconnect_stops_forwarding(self):
        self.pool._on_status = Mock()
        self.pool._on_media_error = Mock()
        self.effect.statusChanged.disconnect(self.status)
        self.player.errorOccurred.disconnect(self.error)
        self.effect.statusChanged.emit()
        self.player.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "ignored")
        self.pool._on_status.assert_not_called()
        self.pool._on_media_error.assert_not_called()

    def test_retained_callbacks_ignore_natively_deleted_wrappers(self):
        self.pool._on_status = Mock()
        self.pool._on_media_error = Mock()
        self.pool.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.status()
        self.error(QMediaPlayer.Error.ResourceError, "late")
        self.pool._on_status.assert_not_called()
        self.pool._on_media_error.assert_not_called()
        # Avoid a second native call in tearDown, while retaining callbacks and
        # Python wrappers for the assertions above.
        self.pool = ReliableAudioPool(Path(self.directory.name))

    def test_retained_callbacks_do_not_own_native_parent_or_voice(self):
        def create():
            pool = ReliableAudioPool(Path(self.directory.name))
            effect, player = QSoundEffect(pool), QMediaPlayer(pool)
            return (_effect_status_listener(pool, effect, "test.wav"),
                    _media_error_listener(pool, player), weakref.ref(pool), weakref.ref(effect), weakref.ref(player))
        status, error, *refs = create()
        gc.collect()
        self.assertTrue(all(ref() is None for ref in refs))
        status()
        error(QMediaPlayer.Error.ResourceError, "late")


if __name__ == "__main__":
    unittest.main()
