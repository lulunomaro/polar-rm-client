"""Bounded early-factory failures: inert Qt objects and unstarted executors.

These probes do not qualify physical audio, native decoder or device cleanup.
"""
from pathlib import Path
import gc
import os
import sys
import tempfile
import threading
import unittest
import weakref
from types import SimpleNamespace
from unittest.mock import patch

os.environ.update(QT_QPA_PLATFORM="offscreen", CI="true",
                  POLAR_DISABLE_MEDIA_PRELOAD="1", POLAR_DISABLE_LOCAL_CAPTURE="1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QEvent
from PySide6.QtMultimedia import QMediaPlayer, QSoundEffect
from PySide6.QtWidgets import QApplication
import shiboken6
from rm_client.ui.polar import window_assembly as assembly
from rm_client import startup_wallpaper
from rm_client.ui_audio import ReliableAudioPool

APP = QApplication.instance() or QApplication([])


def drain():
    for _ in range(5):
        gc.collect()
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class EarlyWindowFactoryFailureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="early-window-factory-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.paths = SimpleNamespace(
            user_data_root=self.root / "user",
            asset=lambda *parts: self.root.joinpath("empty-assets", *parts),
        )
        environment = patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": str(self.root / "user")})
        environment.start()
        self.addCleanup(environment.stop)
        # Source loading and playback are disabled. Real QMediaDevices may
        # still initialize the platform multimedia service during construction.
        for owner, name in ((QMediaPlayer, "setSource"), (QMediaPlayer, "play"),
                            (QSoundEffect, "setSource"), (QSoundEffect, "play")):
            context = patch.object(owner, name)
            context.start()
            self.addCleanup(context.stop)

    def observe_pool(self, pool, label, refs, destroyed):
        refs.append(weakref.ref(pool))
        pool.destroyed.connect(lambda *_: destroyed.append(label))

    def assert_native_pools_retired(self, refs, destroyed, expected):
        drain()
        retained = [ref() for ref in refs if ref() is not None and shiboken6.isValid(ref())]
        # Rescue only after recording the failed observation.
        for pool in retained:
            pool.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(len(retained), 0, "valid native audio pool retained after factory failure")
        self.assertCountEqual(destroyed, expected)

    def test_feedback_second_constructor_rejection_retires_first_native_pool(self):
        refs, destroyed, renderers = [], [], []
        real_kill_fx = assembly.KillFxRenderer
        error = OSError("second feedback constructor rejected")

        def first(root):
            renderer = real_kill_fx(root)
            renderers.append(weakref.ref(renderer))
            self.observe_pool(renderer.sounds, "kill", refs, destroyed)
            self.assertIsNone(renderer.sounds._executor)
            return renderer

        def reject(*args, **kwargs):
            raise error

        with patch.object(assembly, "KillFxRenderer", first), \
             patch.object(assembly, "UiAudioManager", reject):
            with self.assertRaises(OSError) as caught:
                assembly.create_feedback_assets(self.paths)
        self.assertIs(caught.exception, error)
        self.assert_native_pools_retired(refs, destroyed, ["kill"])
        self.assertTrue(all(ref() is None for ref in renderers))

    def test_feedback_failure_inside_second_preload_retires_both_native_pools(self):
        refs, destroyed = [], []
        real_kill_fx = assembly.KillFxRenderer
        error = ValueError("second feedback preload rejected")

        def first(root):
            renderer = real_kill_fx(root)
            self.observe_pool(renderer.sounds, "kill", refs, destroyed)
            return renderer

        def reject_preload(pool, *args, **kwargs):
            self.observe_pool(pool, "ui", refs, destroyed)
            raise error

        with patch.object(assembly, "KillFxRenderer", first), \
             patch.object(ReliableAudioPool, "preload", reject_preload):
            with self.assertRaises(ValueError) as caught:
                assembly.create_feedback_assets(self.paths)
        self.assertIs(caught.exception, error)
        self.assert_native_pools_retired(refs, destroyed, ["kill", "ui"])

    def test_wallpaper_decoder_rejection_discards_executor_without_worker_admission(self):
        executors, videos = [], []
        real_executor = startup_wallpaper.ThreadPoolExecutor
        real_video = startup_wallpaper.VideoWallpaperPlayer
        error = OSError("thumbnail decoder constructor rejected")
        order = []

        def executor(*args, **kwargs):
            owner = real_executor(*args, **kwargs)
            executors.append(weakref.ref(owner))
            self.assertEqual(owner._threads, set())
            order.append("executor")
            return owner

        def video():
            owner = real_video()
            videos.append(weakref.ref(owner))
            self.assertIsNone(owner._thread)
            return owner

        def reject_decoder():
            order.append("decoder")
            self.assertEqual(executors[0]()._threads, set())
            raise error

        with patch.object(startup_wallpaper, "ThreadPoolExecutor", executor), \
             patch.object(startup_wallpaper, "VideoWallpaperPlayer", video), \
             patch.object(startup_wallpaper, "ThumbnailDecoder", reject_decoder), \
             patch.object(threading.Thread, "start", side_effect=AssertionError("unexpected worker")) as start:
            with self.assertRaises(OSError) as caught:
                startup_wallpaper.StartupWallpaperManager(self.root)
            start.assert_not_called()
        self.assertIs(caught.exception, error)
        self.assertEqual(order, ["executor", "decoder"])
        drain()
        self.assertTrue(all(ref() is None for ref in executors + videos))


if __name__ == "__main__":
    unittest.main(verbosity=2)
