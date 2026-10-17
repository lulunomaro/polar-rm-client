"""Native admission must not outlive successful wallpaper retirement."""
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.startup_wallpaper import VideoWallpaperPlayer


class WallpaperAdmissionAuditTests(unittest.TestCase):
    def test_delayed_start_cannot_open_native_capture_after_close_completed(self):
        player = VideoWallpaperPlayer()
        admitted, resume, closed = threading.Event(), threading.Event(), threading.Event()
        late_native_opens, errors, workers = [], [], []
        real_start = threading.Thread.start

        def delayed_start(worker):
            if worker.name.startswith('wallpaper:'):
                workers.append(worker)
                admitted.set()
                if not resume.wait(2):
                    raise RuntimeError('test admission barrier timed out')
            return real_start(worker)

        def capture(path):
            late_native_opens.append(closed.is_set())
            return SimpleNamespace(isOpened=lambda: False, release=lambda: None)

        def load():
            try:
                player.load(Path('fake.mp4'))
            except BaseException as exc:
                errors.append(exc)

        def close():
            try:
                player.close()
                closed.set()
            except BaseException as exc:
                errors.append(exc)

        with patch.dict(sys.modules, {'cv2': SimpleNamespace(VideoCapture=capture)}), \
                patch.object(Path, 'exists', return_value=True), \
                patch.object(threading.Thread, 'start', delayed_start):
            loader = threading.Thread(target=load)
            closer = threading.Thread(target=close)
            loader.start()
            try:
                self.assertTrue(admitted.wait(1), 'load did not reach admission barrier')
                closer.start()
                # Either close waits for the reserved worker, or cancellation
                # prevents that worker entering native code after close returns.
                closed.wait(.2)
            finally:
                resume.set()
                loader.join(2)
                if closer.ident is not None:
                    closer.join(2)
                for worker in workers:
                    if worker.ident is not None:
                        worker.join(2)
            self.assertFalse(loader.is_alive())
            self.assertFalse(closer.is_alive())
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(errors, [])
            self.assertTrue(closed.is_set())
            self.assertNotIn(True, late_native_opens,
                             'native capture opened after close already certified retirement')

    def test_constructor_and_unadmitted_start_failures_are_retryable(self):
        for phase in ("constructor", "start"):
            with self.subTest(phase=phase):
                player = VideoWallpaperPlayer()
                target = "rm_client.startup_wallpaper.threading.Thread" if phase == "constructor" else "threading.Thread.start"
                with patch.dict(sys.modules, {"cv2": SimpleNamespace()}), patch.object(Path, "exists", return_value=True), patch(target, side_effect=RuntimeError("admission failed")):
                    self.assertFalse(player.load(Path("fake.mp4")))
                self.assertEqual(player._pending_starts, {})
                self.assertIsNone(player._thread)
                self.assertIsNone(player.path)
                self.assertIn("admission failed", player.last_error)
                player.close()

    def test_late_start_failure_retains_native_owner_until_final_close(self):
        player = VideoWallpaperPlayer()
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        real_start = threading.Thread.start
        workers, releases, errors = [], [], []
        def capture(path):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("capture barrier timed out")
            return SimpleNamespace(isOpened=lambda: False, release=lambda: releases.append(path))
        def late_start(worker):
            workers.append(worker)
            real_start(worker)
            if not entered.wait(1):
                raise RuntimeError("worker did not enter capture")
            raise RuntimeError("late admission failure")
        def close():
            try:
                player.close()
                closed.set()
            except BaseException as error:
                errors.append(error)
        with patch.dict(sys.modules, {"cv2": SimpleNamespace(VideoCapture=capture)}), patch.object(Path, "exists", return_value=True):
            closer = threading.Thread(target=close)
            try:
                with patch.object(threading.Thread, "start", late_start):
                    self.assertFalse(player.load(Path("fake.mp4")))
                self.assertIn(workers[0], player._retired_threads)
                self.assertFalse(player.load(Path("replacement.mp4")))
                closer.start()
                self.assertFalse(closed.wait(.05))
            finally:
                release.set()
                if closer.ident is not None:
                    closer.join(2)
                for worker in workers:
                    worker.join(2)
            self.assertTrue(closed.is_set())
            self.assertEqual(errors, [])
            self.assertEqual(len(releases), 1)
            self.assertEqual(player._retired_threads, [])

    def test_close_during_import_revokes_not_yet_reserved_load(self):
        import builtins
        player = VideoWallpaperPlayer()
        entered, resume = threading.Event(), threading.Event()
        outcomes, errors, native_opens = [], [], []
        original_import = builtins.__import__
        def importing(name, *args, **kwargs):
            if name == "cv2":
                entered.set()
                if not resume.wait(2):
                    raise RuntimeError("import barrier timed out")
            return original_import(name, *args, **kwargs)
        def load():
            try:
                outcomes.append(player.load(Path("fake.mp4")))
            except BaseException as error:
                errors.append(error)
        cv2 = SimpleNamespace(VideoCapture=lambda path: native_opens.append(path))
        with patch.dict(sys.modules, {"cv2": cv2}), patch.object(Path, "exists", return_value=True), patch.object(builtins, "__import__", importing):
            loader = threading.Thread(target=load)
            loader.start()
            try:
                self.assertTrue(entered.wait(1))
                player.close()
            finally:
                resume.set()
                loader.join(2)
            self.assertFalse(loader.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(outcomes, [False])
            self.assertEqual(native_opens, [])
            self.assertIsNone(player._thread)

    def test_stale_or_cancelled_decoder_never_enters_native_io(self):
        for suffix in (".mp4", ".png"):
            for stale in (True, False):
                with self.subTest(suffix=suffix, stale=stale):
                    player = VideoWallpaperPlayer()
                    event = threading.Event()
                    if not stale:
                        event.set()
                    # Missing native entrypoints make accidental I/O observable
                    # as a current-generation error, as well as by mock calls.
                    from unittest.mock import Mock
                    cv2 = SimpleNamespace(VideoCapture=Mock(), imread=Mock())
                    with patch.dict(sys.modules, {"cv2": cv2}):
                        player._decode_loop(Path("fake" + suffix), -1 if stale else 0, event)
                    cv2.VideoCapture.assert_not_called()
                    cv2.imread.assert_not_called()
                    self.assertEqual(player.last_error, "")


if __name__ == '__main__':
    unittest.main()
