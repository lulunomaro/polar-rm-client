"""Real executor admission must remain visible to native thumbnail shutdown."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.startup_wallpaper import StartupWallpaperManager
from rm_client.thumbnail_decoder import ThumbnailDecoder


class ThumbnailAdmissionAuditTests(unittest.TestCase):
    def test_late_submit_failure_cannot_certify_close_while_capture_read_is_active(self):
        manager = object.__new__(StartupWallpaperManager)
        manager.thumbnail_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='wallpaper-thumb')
        manager.thumbnail_decoder = ThumbnailDecoder()
        manager.thumbnail_cache = {}
        manager.thumbnail_futures = {}
        manager.resolve_media_profile = lambda profile: Path('fake.mp4')
        entered, resume, closed = threading.Event(), threading.Event(), threading.Event()
        workers, submit_errors, close_errors = [], [], []
        real_start = threading.Thread.start
        def read():
            entered.set()
            if not resume.wait(3):
                raise RuntimeError('read barrier timed out')
            return False, None
        capture = SimpleNamespace(read=read, release=lambda: None, isOpened=lambda: False)
        def late_start(worker):
            real_start(worker)
            if worker.name.startswith('wallpaper-thumb'):
                workers.append(worker)
                if not entered.wait(1):
                    raise RuntimeError('worker did not enter read')
                raise RuntimeError('late executor admission failure')
        def close():
            try:
                manager.close_thumbnail_workers()
                closed.set()
            except BaseException as error:
                close_errors.append(error)
        closer = threading.Thread(target=close)
        with patch.dict(sys.modules, {'cv2': SimpleNamespace(VideoCapture=lambda path: capture)}), patch.object(Path, 'exists', return_value=True):
            try:
                with patch.object(threading.Thread, 'start', late_start):
                    try:
                        manager.thumbnail('Custom')
                    except BaseException as error:
                        submit_errors.append(error)
                self.assertTrue(entered.is_set())
                closer.start()
                self.assertFalse(closed.wait(.1), 'thumbnail close certified success while untracked native reader was active')
            finally:
                resume.set()
                if closer.ident is not None:
                    closer.join(2)
                manager.thumbnail_executor.shutdown(wait=True, cancel_futures=True)
                for worker in workers:
                    worker.join(2)
        self.assertEqual(close_errors, [])
        self.assertTrue(closed.is_set())
        self.assertEqual(submit_errors, [])
        self.assertIn('late executor admission failure', manager.thumbnail_decoder.last_error)

    def manager(self):
        manager = object.__new__(StartupWallpaperManager)
        manager.thumbnail_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='wallpaper-thumb')
        manager.thumbnail_decoder = ThumbnailDecoder()
        manager.thumbnail_cache = {}
        manager.thumbnail_futures = {}
        manager.resolve_media_profile = lambda profile: Path('fake.mp4')
        return manager

    def test_late_worker_before_decoder_entry_cannot_open_after_close(self):
        manager = self.manager()
        entered, resume = threading.Event(), threading.Event()
        workers, native_opens = [], []
        real_start = threading.Thread.start
        original_load = manager.thumbnail_decoder.load
        def delayed_load(path):
            entered.set()
            if not resume.wait(2):
                raise RuntimeError('entry barrier timed out')
            return original_load(path)
        def late_start(worker):
            workers.append(worker)
            real_start(worker)
            if not entered.wait(1):
                raise RuntimeError('worker not admitted')
            raise RuntimeError('late admission')
        with patch.dict(sys.modules, {'cv2': SimpleNamespace(VideoCapture=lambda path: native_opens.append(path))}), patch.object(Path, 'exists', return_value=True), patch.object(manager.thumbnail_decoder, 'load', delayed_load):
            try:
                with patch.object(threading.Thread, 'start', late_start):
                    self.assertTrue(manager.thumbnail('Custom').isNull())
                manager.close_thumbnail_workers()
                self.assertTrue(manager.thumbnail_decoder._closed)
            finally:
                resume.set()
                manager.thumbnail_executor.shutdown(wait=True, cancel_futures=True)
                for worker in workers:
                    worker.join(2)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(native_opens, [])

    def test_constructor_rejection_queued_work_is_cancelled_by_close(self):
        manager = self.manager()
        from unittest.mock import Mock
        capture = Mock()
        with patch.dict(sys.modules, {'cv2': SimpleNamespace(VideoCapture=capture)}), patch.object(Path, 'exists', return_value=True):
            with patch('threading.Thread', side_effect=RuntimeError('constructor failure')):
                self.assertTrue(manager.thumbnail('Custom').isNull())
            manager.close_thumbnail_workers()
            capture.assert_not_called()
            self.assertEqual(manager.thumbnail_futures, {})
            self.assertIn('constructor failure', manager.thumbnail_decoder.last_error)

    def test_closed_executor_rejection_does_not_invalidate_existing_cache(self):
        from PySide6.QtGui import QImage
        manager = self.manager()
        cached = QImage(2, 2, QImage.Format.Format_RGB32)
        manager.thumbnail_cache['Cached'] = cached
        manager.close_thumbnail_workers()
        with patch.object(Path, 'exists', return_value=True):
            self.assertIs(manager.thumbnail('Cached'), cached)
            self.assertTrue(manager.thumbnail('Custom').isNull())
        self.assertEqual(manager.thumbnail_futures, {})
        self.assertIn('Thumbnail submission failed', manager.thumbnail_decoder.last_error)

    def test_open_executor_publishes_once_and_cache_hits_do_not_resubmit(self):
        from unittest.mock import Mock
        manager = self.manager()
        capture = SimpleNamespace(read=lambda: (False, None), release=lambda: None, isOpened=lambda: False)
        opener = Mock(return_value=capture)
        try:
            with patch.dict(sys.modules, {'cv2': SimpleNamespace(VideoCapture=opener)}), patch.object(Path, 'exists', return_value=True):
                self.assertTrue(manager.thumbnail('Custom').isNull())
                manager.thumbnail_futures['Custom'].result(timeout=2)
                published = manager.thumbnail('Custom')
                self.assertIs(manager.thumbnail('Custom'), published)
                self.assertEqual(manager.thumbnail_futures, {})
                opener.assert_called_once_with('fake.mp4')
        finally:
            manager.close_thumbnail_workers()

    def test_active_release_failure_finishes_before_close_reports_then_retries(self):
        decoder = ThumbnailDecoder()
        releasing, resume, closed = threading.Event(), threading.Event(), threading.Event()
        errors, releases = [], []
        def release():
            releases.append(True)
            releasing.set()
            if not resume.wait(2):
                raise RuntimeError('release barrier timed out')
            raise OSError('capture release failed')
        capture = SimpleNamespace(read=lambda: (False, None), release=release, isOpened=lambda: False)
        def close():
            try:
                decoder.close()
            except BaseException as error:
                errors.append(error)
            finally:
                closed.set()
        worker = threading.Thread(target=lambda: decoder.load(Path('fake.mp4')))
        closer = threading.Thread(target=close)
        with patch.dict(sys.modules, {'cv2': SimpleNamespace(VideoCapture=lambda path: capture)}):
            worker.start()
            try:
                self.assertTrue(releasing.wait(1))
                closer.start()
                self.assertFalse(closed.wait(.05))
            finally:
                resume.set()
                worker.join(2)
                if closer.ident is not None:
                    closer.join(2)
            self.assertEqual(len(releases), 1)
            self.assertEqual(len(errors), 1)
            self.assertIn('capture release failed', str(errors[0]))
            self.assertEqual(decoder._active_loads, 0)
            self.assertIs(decoder._unreleased_captures[0][0], capture)
            capture.release = lambda: releases.append(True)
            decoder.close()
            self.assertEqual(len(releases), 2)
            self.assertEqual(decoder._unreleased_captures, [])


if __name__ == '__main__':
    unittest.main()
