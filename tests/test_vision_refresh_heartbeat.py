"""Real refresh caller and production lifecycle with inert Event-held workers.

Qt heartbeat observations are controlled scheduling evidence only. These tests
do not open models, sockets, cameras, a native GL context, or a real window.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from rm_client.tongji_yolo import LocalAlignedVision
from rm_client.tongji_bridge import TongjiVisionReceiver
from rm_client.vision import TongjiVisionBridge
from rm_client.ui.polar.mixins.video_backend_mixin import VideoBackendMixin
from rm_client.ui.polar.vision_selection import VisionSelection, VisionSelectionField


class RefreshHost(VideoBackendMixin, SimpleNamespace):
    """Keep real helper dispatch while supplying unrelated window services."""
    local_vision = VisionSelectionField("local")
    tongji_receiver = VisionSelectionField("receiver")
    tongji_bridge = VisionSelectionField("bridge")
    _vision_source = VisionSelectionField("source")
    _tongji_enabled = VisionSelectionField("tongji_enabled")


class VisionRefreshHeartbeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_held_workers_do_not_block_refresh_or_qt_but_delay_strict_close(self):
        for disabled, hold_ms in ((True, 80), (True, 240), (False, 80), (False, 240)):
            with self.subTest(disabled=disabled, event_hold_ms=hold_ms):
                self.check_held_runtime(disabled, hold_ms)

    def check_held_runtime(self, disabled, hold_ms):
        release = threading.Event()
        owners = (LocalAlignedVision(Path("synthetic-old.xml")),
                  TongjiVisionReceiver(1), TongjiVisionBridge())
        entered = [threading.Event() for _ in owners]
        patches, timers = [], []
        refresh_seconds, errors, heartbeats = [], [], []
        closed = threading.Event()
        closer = None
        loop = QEventLoop()
        main_ident = threading.get_ident()

        def held(index):
            def run(*args):
                entered[index].set()
                if not release.wait(5):
                    errors.append("inert worker timeout")
            return run

        def guarding_join(original_join):
            def join(*args, **kwargs):
                if threading.get_ident() == main_ident:
                    raise AssertionError("UI refresh joined a worker")
                return original_join(*args, **kwargs)
            return join

        def refresh():
            if release.is_set():
                return
            try:
                started = time.perf_counter()
                VideoBackendMixin.refresh_vision_runtime(host)
                refresh_seconds.append(time.perf_counter() - started)
            except BaseException as exc:
                errors.append(repr(exc))
                loop.quit()

        def beat():
            if not release.is_set():
                heartbeats.append(time.perf_counter())

        def close_all():
            try:
                for owner in owners:
                    owner.close()
            except BaseException as exc:
                errors.append(repr(exc))
            finally:
                closed.set()

        try:
            for index, owner in enumerate(owners):
                mocked = patch.object(owner, "_run", held(index))
                mocked.start()
                patches.append(mocked)
                owner.start()
            for event in entered:
                self.assertTrue(event.wait(1))
            workers = [owner._thread for owner in owners]
            for worker in workers:
                mocked = patch.object(worker, "join", guarding_join(worker.join))
                mocked.start()
                patches.append(mocked)
            module = SimpleNamespace(enabled=True)
            def mode(_module, name, default):
                return {"Vision Source": "Auto", "Local Device": "CPU"}.get(name, default)
            def number(_module, name, default):
                return 2 if name == "UDP Port" else default
            host = RefreshHost(
                _window_lifecycle=SimpleNamespace(closing=False),
                backend=SimpleNamespace(set_active_video_source=lambda _: None),
                registry=SimpleNamespace(get=lambda _: module), setting_mode=mode,
                setting_bool=lambda *args: True, get_setting_number=number,
                _client_asset_path=lambda *args: Path(__file__),
                _vision_selection=VisionSelection(*owners),
            )
            with patch.dict(os.environ, {
                "POLAR_DISABLE_LOCAL_CAPTURE": "1" if disabled else "0",
                "POLAR_VISION_SOURCE": "Auto",
            }):
                refresh()
                self.assertEqual(errors, [])
                self.assertTrue(all(not owner.running for owner in owners))
                self.assertTrue(all(owner.cleanup_pending for owner in owners))
                self.assertTrue(all(owner._thread is worker for owner, worker in zip(owners, workers)))
                self.assertTrue(all(worker.is_alive() for worker in workers))
                if not disabled:
                    self.assertEqual(owners[0].model_path, Path(__file__))
                    self.assertEqual(owners[0].device, "CPU")
                    self.assertEqual(owners[1].port, 2)
                closer = threading.Thread(target=close_all)
                closer.start()
                for interval, callback, single in (
                    (5, refresh, False), (10, beat, False), (hold_ms, release.set, True),
                    (5, lambda: loop.quit() if closed.is_set() else None, False),
                    (3000, loop.quit, True),
                ):
                    timer = QTimer()
                    timer.setSingleShot(single)
                    timer.timeout.connect(callback)
                    timer.start(interval)
                    timers.append(timer)
                self.assertFalse(closed.is_set())
                loop.exec()
            self.assertEqual(errors, [])
            self.assertTrue(release.is_set(), "loop exited before scheduled release")
            self.assertTrue(closed.is_set(), "strict close did not finish after release")
            self.assertTrue(heartbeats, "no Qt heartbeat while workers were held")
            self.assertGreater(len(refresh_seconds), 1)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertTrue(all(owner._thread is None and not owner.cleanup_pending for owner in owners))
        finally:
            for timer in timers:
                timer.stop()
            release.set()
            if closer is not None:
                closer.join(2)
            for mocked in reversed(patches):
                mocked.stop()
            for owner in owners:
                owner.close()


if __name__ == "__main__":
    unittest.main()
