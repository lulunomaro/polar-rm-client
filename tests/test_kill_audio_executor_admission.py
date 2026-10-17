"""Actual executor admission failure cannot hide active kill-cue decode work."""
import os
os.environ.update(QT_QPA_PLATFORM='offscreen', CI='true', POLAR_DISABLE_MEDIA_PRELOAD='1')
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from rm_client.kill_audio import KillSoundPool

APP = QApplication.instance() or QApplication([])


class KillAudioExecutorAdmissionTests(unittest.TestCase):
    def test_late_executor_admission_cannot_hide_active_decode_from_retirement(self):
        entered, resume, retired = threading.Event(), threading.Event(), threading.Event()
        workers, errors = [], []
        real_start = threading.Thread.start
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'cue.ogg').write_bytes(b'inert fixture, never decoded')
            pool = KillSoundPool(root)
            def decode(source):
                entered.set()
                if not resume.wait(3):
                    raise RuntimeError('decode barrier timed out')
                return source
            pool._cache = SimpleNamespace(decode=decode)
            def late_start(worker):
                real_start(worker)
                if worker.name.startswith('kill-cue-decode'):
                    workers.append(worker)
                    if not entered.wait(1):
                        raise RuntimeError('worker never entered decode')
                    raise RuntimeError('late executor admission')
            def wait():
                try:
                    pool.wait_closed()
                    retired.set()
                except BaseException as exc:
                    errors.append(exc)
            waiter = threading.Thread(target=wait)
            try:
                with patch.object(threading.Thread, 'start', late_start):
                    # Existing submission may report rejection. Regardless of
                    # its UI policy, the genuinely admitted decode stays owned.
                    try:
                        pool._queue_decode('cue.ogg')
                    except RuntimeError as exc:
                        self.assertIn('late executor admission', str(exc))
                self.assertTrue(entered.is_set())
                self.assertTrue(workers[0].is_alive())
                pool.close()
                waiter.start()
                self.assertFalse(retired.wait(.1), 'retirement certified while decode was active')
            finally:
                resume.set()
                if waiter.ident is not None:
                    waiter.join(2)
                for worker in workers:
                    worker.join(2)
                pool.close()
                if pool._executor is not None:
                    pool._executor.shutdown(wait=True, cancel_futures=True)
            self.assertFalse(waiter.is_alive())
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(errors, [])
            self.assertTrue(retired.is_set())
            self.assertEqual(pool._decoded, {})

    def test_delayed_unregistered_task_cannot_enter_decoder_after_retirement(self):
        admitted, resume = threading.Event(), threading.Event()
        workers, decoded = [], []
        real_start = threading.Thread.start
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'cue.ogg').write_bytes(b'inert fixture')
            pool = KillSoundPool(root)
            pool._cache = SimpleNamespace(decode=lambda source: decoded.append(source))
            decode_owned = pool._decode_owned
            def delayed(source):
                admitted.set()
                if not resume.wait(3):
                    raise RuntimeError('admission barrier timed out')
                return decode_owned(source)
            def late_start(worker):
                real_start(worker)
                if worker.name.startswith('kill-cue-decode'):
                    workers.append(worker)
                    if not admitted.wait(1):
                        raise RuntimeError('task never admitted')
                    raise RuntimeError('late executor admission')
            try:
                with patch.object(pool, '_decode_owned', delayed), patch.object(threading.Thread, 'start', late_start):
                    self.assertFalse(pool._queue_decode('cue.ogg'))
                    self.assertIn('cue.ogg', pool._conversion_failed)
                    self.assertNotIn('cue.ogg', pool._converting)
                pool.close()
                pool.wait_closed()
                self.assertTrue(workers[0].is_alive())
                self.assertEqual(decoded, [])
            finally:
                resume.set()
                pool.close()
                for worker in workers:
                    worker.join(2)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(decoded, [])
            self.assertEqual(pool._decoded, {})


if __name__ == '__main__':
    unittest.main()
