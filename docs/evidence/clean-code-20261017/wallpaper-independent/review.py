import sys, threading, unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0, sys.argv.pop(1))
from rm_client.startup_wallpaper import VideoWallpaperPlayer

class IndependentAdmissionProbes(unittest.TestCase):
    def test_finished_then_raised_owner_is_joinable_and_cleared(self):
        owner=VideoWallpaperPlayer(); real=threading.Thread.start; workers=[]
        def start(worker):
            workers.append(worker); real(worker); worker.join(1)
            self.assertFalse(worker.is_alive())
            raise RuntimeError('finished then raised')
        with patch.dict(sys.modules,{'cv2':NS()}), patch.object(Path,'exists',return_value=True), patch.object(owner,'_decode_loop'), patch.object(threading.Thread,'start',start):
            self.assertFalse(owner.load(Path('fake.mp4')))
        self.assertEqual(owner._retired_threads,workers)
        owner.close(); self.assertEqual(owner._retired_threads,[])

    def test_pending_rejection_resolves_close_without_joining_unstarted_worker(self):
        owner=VideoWallpaperPlayer(); entered=threading.Event(); release=threading.Event(); closed=threading.Event()
        errors=[]; real=threading.Thread.start
        def start(worker):
            if worker.name.startswith('wallpaper:'):
                entered.set(); self.assertTrue(release.wait(2)); raise RuntimeError('reject delayed start')
            real(worker)
        def load():
            try: self.assertFalse(owner.load(Path('fake.mp4')))
            except BaseException as exc: errors.append(exc)
        def close():
            try: owner.close(); closed.set()
            except BaseException as exc: errors.append(exc)
        with patch.dict(sys.modules,{'cv2':NS()}),patch.object(Path,'exists',return_value=True),patch.object(threading.Thread,'start',start):
            loader=threading.Thread(target=load); closer=threading.Thread(target=close); loader.start()
            try:
                self.assertTrue(entered.wait(1)); closer.start()
                self.assertFalse(closed.wait(.05))
                self.assertFalse(owner.load(Path('replacement.mp4')))
            finally:
                release.set(); loader.join(2)
                if closer.ident is not None: closer.join(2)
        self.assertFalse(loader.is_alive()); self.assertFalse(closer.is_alive())
        self.assertEqual(errors,[]); self.assertTrue(closed.is_set()); self.assertEqual(owner._pending_starts,{})

    def test_final_release_failure_stays_owned_for_explicit_retry(self):
        owner=VideoWallpaperPlayer(); release_error=OSError('release failed'); calls=[]
        def release():
            calls.append('release')
            if len(calls)==1: raise release_error
        capture=NS(isOpened=lambda:False,release=release)
        with patch.dict(sys.modules,{'cv2':NS(VideoCapture=lambda path:capture)}),patch.object(Path,'exists',return_value=True):
            self.assertTrue(owner.load(Path('fake.mp4')))
            worker=owner._thread; worker.join(1); self.assertFalse(worker.is_alive())
            with self.assertRaisesRegex(RuntimeError,'release failed'): owner.close()
            self.assertFalse(owner.load(Path('replacement.mp4')))
            self.assertEqual(calls,['release'])
            self.assertIs(owner._unreleased_captures[0][0],capture)
            owner.close()
            self.assertEqual(calls,['release','release']); self.assertEqual(owner._unreleased_captures,[])

unittest.main(verbosity=2)
