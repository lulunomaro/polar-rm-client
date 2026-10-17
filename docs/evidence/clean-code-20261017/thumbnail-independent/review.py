import sys, threading, unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,sys.argv.pop(1))
from rm_client.thumbnail_decoder import ThumbnailDecoder

class IndependentThumbnailOwnership(unittest.TestCase):
    def test_two_native_acquisitions_stay_owned_until_both_release(self):
        d=ThumbnailDecoder(); entered=[threading.Event(),threading.Event()]; resume=[threading.Event(),threading.Event()]
        closes=[]; errors=[]; done=threading.Event(); count=0; lock=threading.Lock()
        def capture(path):
            nonlocal count
            with lock: i=count; count+=1
            entered[i].set()
            if not resume[i].wait(3): raise RuntimeError('native acquisition timeout')
            return NS(read=lambda:(False,None),isOpened=lambda:False,release=lambda:closes.append(i))
        def load():
            try:d.load(Path('fake.mp4'))
            except BaseException as e:errors.append(e)
        def close():
            try:d.close(); done.set()
            except BaseException as e:errors.append(e)
        with patch.dict(sys.modules,{'cv2':NS(VideoCapture=capture)}):
            workers=[threading.Thread(target=load) for _ in range(2)]; closer=threading.Thread(target=close)
            for w in workers:w.start()
            try:
                self.assertTrue(all(e.wait(1) for e in entered)); closer.start()
                self.assertFalse(done.wait(.05)); self.assertTrue(d.load(Path('late.mp4')).isNull()); self.assertEqual(count,2)
                resume[0].set(); workers[0].join(1)
                self.assertFalse(done.wait(.05)); self.assertEqual(d._active_loads,1)
            finally:
                for e in resume:e.set()
                for w in workers:w.join(2)
                if closer.ident is not None:closer.join(2)
        self.assertTrue(done.is_set());self.assertEqual(errors,[]);self.assertEqual(sorted(closes),[0,1]);self.assertEqual(d._active_loads,0)
        self.assertTrue(all(not w.is_alive() for w in workers));self.assertFalse(closer.is_alive())

unittest.main(verbosity=2)
