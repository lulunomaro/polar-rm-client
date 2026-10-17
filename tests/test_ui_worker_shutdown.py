"""Strict worker shutdown contracts without opening hardware or network sockets."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.startup_wallpaper import StartupWallpaperManager, VideoWallpaperPlayer
from rm_client.thumbnail_decoder import ThumbnailDecoder
from rm_client.lifecycle import LifecycleCoordinator, LifecyclePhase, TeardownTask
from rm_client.tongji_bridge import TongjiVisionReceiver
from rm_client.tongji_yolo import LocalAlignedVision
from rm_client.vision import CameraCaptureWorker, TongjiVisionBridge


class SlowThread:
    """A bounded join times out; a strict join finally observes termination."""

    def __init__(self):
        self.alive = True
        self.joins = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joins.append(timeout)
        if timeout is None:
            self.alive = False


class UiWorkerShutdownTests(unittest.TestCase):
    def test_camera_close_waits_after_legacy_bounded_stop(self):
        camera = CameraCaptureWorker()
        thread = camera._thread = SlowThread()
        camera.close()
        self.assertEqual(thread.joins, [1.2, None])
        self.assertIsNone(camera._thread)

    def test_blocked_camera_keeps_coordinator_pending_until_owner_really_exits(self):
        camera = CameraCaptureWorker()
        unblock = threading.Event()
        camera._thread = threading.Thread(target=unblock.wait, daemon=True)
        camera._thread.start()
        coordinator = LifecycleCoordinator(warning_after_s=0)
        try:
            coordinator.request("closed", [TeardownTask("camera", camera.close)], final=True)
            self.assertIsNone(coordinator.poll())
            self.assertTrue(coordinator.pending)
            self.assertEqual(coordinator.snapshot().phase, LifecyclePhase.BLOCKED)
            self.assertFalse(camera.start())
            unblock.set()
            deadline = time.monotonic() + 1
            completion = None
            while completion is None and time.monotonic() < deadline:
                completion = coordinator.poll()
                if completion is None:
                    time.sleep(.001)
            self.assertIsNotNone(completion)
            self.assertTrue(completion.succeeded)
            self.assertFalse(coordinator.pending)
        finally:
            unblock.set()
            coordinator.wait(1)

    def test_local_vision_close_waits_after_legacy_bounded_stop(self):
        vision = LocalAlignedVision(Path("not-loaded.xml"))
        thread = vision._thread = SlowThread()
        vision.close()
        self.assertEqual(thread.joins, [1.2, None])
        self.assertIsNone(vision._thread)

    def test_tcp_bridge_stop_retains_live_thread_and_prevents_replacement(self):
        bridge = TongjiVisionBridge()
        thread = bridge._thread = SlowThread()
        bridge.stop()
        self.assertIs(bridge._thread, thread)
        with patch("rm_client.vision.threading.Thread") as create:
            bridge.start()
        create.assert_not_called()
        bridge.close()
        self.assertFalse(thread.alive)
        self.assertIsNone(bridge._thread)

    def test_udp_receiver_stop_retains_live_thread_and_prevents_replacement(self):
        receiver = TongjiVisionReceiver()
        thread = receiver._thread = SlowThread()
        receiver.stop()
        self.assertIs(receiver._thread, thread)
        with patch("rm_client.tongji_bridge.threading.Thread") as create:
            receiver.start()
        create.assert_not_called()
        receiver.close()
        self.assertFalse(thread.alive)
        self.assertIsNone(receiver._thread)

    def test_camera_release_failure_remains_owned_and_rejects_restart(self):
        camera = CameraCaptureWorker()
        capture = NS(isOpened=lambda: False, release=Mock(side_effect=OSError("driver release")))
        with patch.dict(sys.modules, {"cv2": NS(VideoCapture=lambda *_: capture)}):
            self.assertTrue(camera.start())
            camera._thread.join(1)
            self.assertFalse(camera._thread.is_alive())
        with self.assertRaisesRegex(RuntimeError, "driver release") as error:
            camera.close()
        self.assertIsInstance(error.exception.__cause__, OSError)
        self.assertFalse(camera.start())
        self.assertIs(camera._unreleased_capture, capture)

    def test_explicit_second_camera_close_retries_retained_capture(self):
        camera = CameraCaptureWorker()
        capture = NS(isOpened=lambda: False, release=Mock(side_effect=OSError("driver release")))
        with patch.dict(sys.modules, {"cv2": NS(VideoCapture=lambda *_: capture)}):
            self.assertTrue(camera.start())
            camera._thread.join(1)
            self.assertFalse(camera._thread.is_alive())
        with self.assertRaises(RuntimeError):
            camera.close()
        self.assertEqual(capture.release.call_count, 1)
        capture.release.side_effect = None
        camera.close()
        self.assertEqual(capture.release.call_count, 2)
        self.assertIsNone(camera._unreleased_capture)
        self.assertIsNone(camera._capture_close_error)

    def test_explicit_second_receiver_close_retries_retained_socket(self):
        receiver = TongjiVisionReceiver()
        sock = Mock()
        sock.close.side_effect = OSError("socket close")
        with patch("rm_client.tongji_bridge.socket.socket", return_value=sock):
            receiver._run()
        with self.assertRaises(RuntimeError):
            receiver.close()
        sock.close.side_effect = None
        sock.fileno.return_value = -1
        receiver.close()
        self.assertIsNone(receiver._socket)
        self.assertIsNone(receiver._socket_close_error)

    def test_camera_release_that_leaves_open_is_not_success(self):
        camera = CameraCaptureWorker()
        capture = NS(isOpened=Mock(side_effect=[False, True]), release=Mock())
        with patch.dict(sys.modules, {"cv2": NS(VideoCapture=lambda *_: capture)}):
            self.assertTrue(camera.start())
            camera._thread.join(1)
            self.assertFalse(camera._thread.is_alive())
        with self.assertRaisesRegex(RuntimeError, "remained open"):
            camera.close()
        self.assertIs(camera._unreleased_capture, capture)

    def test_udp_bind_failure_still_releases_the_created_socket(self):
        receiver = TongjiVisionReceiver()
        sock = Mock()
        sock.bind.side_effect = OSError("cannot bind")
        sock.close.return_value = None
        sock.fileno.return_value = -1
        with patch("rm_client.tongji_bridge.socket.socket", return_value=sock):
            receiver._run()
        sock.close.assert_called_once()
        receiver.close()

    def test_udp_close_failure_retains_socket_and_rejects_restart(self):
        receiver = TongjiVisionReceiver()
        sock = Mock()
        sock.close.side_effect = OSError("socket close")
        with patch("rm_client.tongji_bridge.socket.socket", return_value=sock):
            receiver._run()
        with self.assertRaisesRegex(RuntimeError, "socket close"):
            receiver.close()
        self.assertIs(receiver._socket, sock)
        with patch("rm_client.tongji_bridge.threading.Thread") as create:
            receiver.start()
        create.assert_not_called()

    def test_tcp_bridge_close_failure_retains_socket_and_stops_reconnect(self):
        bridge = TongjiVisionBridge()
        bridge.running = True
        sock = Mock()
        sock.recv.return_value = b""
        sock.close.side_effect = OSError("tcp close")
        with patch("rm_client.vision.socket.create_connection", return_value=sock) as connect, \
             patch("rm_client.vision.time.sleep"):
            bridge._run()
        connect.assert_called_once()
        self.assertFalse(bridge.running)
        self.assertIs(bridge._unreleased_socket, sock)
        with self.assertRaisesRegex(RuntimeError, "tcp close"):
            bridge.close()
        self.assertEqual(sock.close.call_count, 1)
        sock.close.side_effect = None
        sock.fileno.return_value = -1
        bridge.close()
        self.assertIsNone(bridge._unreleased_socket)
        self.assertIsNone(bridge._socket_close_error)

    def test_thumbnail_read_failure_still_releases_native_capture(self):
        decoder = ThumbnailDecoder()
        capture = NS(read=Mock(side_effect=TypeError("bad frame")),
                     release=Mock(), isOpened=lambda: False)
        with patch.dict(sys.modules, {"cv2": NS(VideoCapture=lambda *_: capture)}):
            self.assertTrue(decoder.load(Path("fake.mp4")).isNull())
        capture.release.assert_called_once()
        decoder.raise_if_unreleased()
        self.assertIn("TypeError: bad frame", decoder.last_error)

    def test_thumbnail_release_failure_is_retained_after_future_consumption(self):
        decoder = ThumbnailDecoder()
        capture = NS(read=lambda: (False, None), release=Mock(side_effect=OSError("thumbnail release")))
        with patch.dict(sys.modules, {"cv2": NS(VideoCapture=lambda *_: capture)}):
            self.assertTrue(decoder.load(Path("fake.mp4")).isNull())
        with self.assertRaisesRegex(RuntimeError, "thumbnail release"):
            decoder.raise_if_unreleased()
        self.assertIs(decoder._unreleased_captures[0][0], capture)

    def test_wallpaper_close_waits_for_current_and_retired_generations(self):
        player = VideoWallpaperPlayer()
        older = player._thread = SlowThread()
        player._stop_worker(keep_frame=True, wait=False)
        newer = player._thread = SlowThread()
        player.close()
        self.assertFalse(older.alive)
        self.assertFalse(newer.alive)
        self.assertIsNone(player._thread)

    def test_wallpaper_selection_cannot_replace_a_still_retiring_decoder(self):
        player = VideoWallpaperPlayer()
        older = player._thread = SlowThread()
        with patch.object(Path, "exists", return_value=True), \
             patch("rm_client.startup_wallpaper.threading.Thread") as create:
            self.assertFalse(player.load(Path("new.mp4")))
        create.assert_not_called()
        self.assertIn(older, player._retired_threads)
        player.close()

    def test_wallpaper_rechecks_release_failure_before_replacement(self):
        player = VideoWallpaperPlayer()
        entered_release, finish_release = threading.Event(), threading.Event()
        def release():
            entered_release.set()
            finish_release.wait(1)
            raise OSError("late decoder release")
        capture = NS(isOpened=lambda: False, release=release)
        with patch.dict(sys.modules, {"cv2": NS(VideoCapture=lambda *_: capture)}):
            thread = threading.Thread(target=player._decode_loop,
                                      args=(Path("old.mp4"), 0, threading.Event()), daemon=True)
            player._thread = thread
            thread.start()
            self.assertTrue(entered_release.wait(1))
            original_stop = player._stop_worker
            def stop_then_finish(**kwargs):
                original_stop(**kwargs)
                finish_release.set()
                thread.join(1)
                self.assertFalse(thread.is_alive())
            try:
                with patch.object(Path, "exists", return_value=True), \
                     patch.object(player, "_stop_worker", side_effect=stop_then_finish), \
                     patch("rm_client.startup_wallpaper.threading.Thread") as create:
                    self.assertFalse(player.load(Path("new.mp4")))
                create.assert_not_called()
                self.assertEqual(len(player._unreleased_captures), 1)
            finally:
                finish_release.set()
                thread.join(1)

    def test_wallpaper_release_failure_retains_native_capture(self):
        player = VideoWallpaperPlayer()
        capture = NS(isOpened=lambda: False, release=Mock(side_effect=OSError("decoder release")))
        with patch.dict(sys.modules, {"cv2": NS(VideoCapture=lambda *_: capture)}):
            player._decode_loop(Path("fake.mp4"), 0, threading.Event())
        with self.assertRaisesRegex(RuntimeError, "decoder release"):
            player.close()
        self.assertIs(player._unreleased_captures[0][0], capture)

    def test_explicit_wallpaper_retry_releases_only_unconfirmed_captures(self):
        player = VideoWallpaperPlayer()
        capture = NS(isOpened=lambda: False, release=Mock(side_effect=OSError("decoder release")))
        with patch.dict(sys.modules, {"cv2": NS(VideoCapture=lambda *_: capture)}):
            player._decode_loop(Path("fake.mp4"), 0, threading.Event())
        with self.assertRaises(RuntimeError):
            player.close()
        self.assertEqual(capture.release.call_count, 1)
        capture.release.side_effect = None
        player.close()
        self.assertEqual(capture.release.call_count, 2)
        self.assertEqual(player._unreleased_captures, [])

    def test_explicit_thumbnail_retry_releases_retained_capture(self):
        decoder = ThumbnailDecoder()
        capture = NS(read=lambda: (False, None), isOpened=lambda: False,
                     release=Mock(side_effect=OSError("thumbnail release")))
        with patch.dict(sys.modules, {"cv2": NS(VideoCapture=lambda *_: capture)}):
            decoder.load(Path("fake.mp4"))
        with self.assertRaises(RuntimeError):
            decoder.close()
        self.assertEqual(capture.release.call_count, 1)
        capture.release.side_effect = None
        decoder.close()
        self.assertEqual(capture.release.call_count, 2)
        self.assertEqual(decoder._unreleased_captures, [])

    def test_wallpaper_manager_waits_for_thumbnails_even_when_video_close_fails(self):
        manager = object.__new__(StartupWallpaperManager)
        manager.video_player = NS(close=Mock(side_effect=RuntimeError("video close")))
        manager.thumbnail_executor = NS(shutdown=Mock())
        manager.thumbnail_decoder = ThumbnailDecoder()
        with self.assertRaisesRegex(RuntimeError, "video close"):
            manager.close()
        manager.thumbnail_executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
