"""Independent real-thread cancellation while initial worker admission is paused."""
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_writer_constructor_admission import FACTORIES, Handle


class ConcurrentWriterAdmissionTests(unittest.TestCase):
    def test_other_thread_cancels_reserved_admission_without_second_owner(self):
        original_thread = threading.Thread
        original_start = threading.Thread.start
        for kind, factory in FACTORIES.items():
            for stage in ('construction', 'start'):
                for outcome in ('return', 'reject', 'late_error'):
                    with self.subTest(kind=kind, stage=stage, outcome=outcome):
                        handle = Handle(blocked=True)
                        reached, proceed = threading.Event(), threading.Event()
                        owners, workers, results, errors = [], [], [], []
                        primary = RuntimeError('independent initial admission fault')

                        def retain(owner):
                            owners.append(owner)
                            if kind == 'dt7':
                                owner._link._handle = handle
                                owner._link.resolved_port = 'FAKE'
                                owner._link._handle_requested_port = owner.port
                            else:
                                owner._serial = handle

                        def pause():
                            reached.set()
                            if not proceed.wait(2):
                                raise AssertionError('cancelling thread did not release admission')
                            if outcome == 'reject':
                                raise primary

                        def create(*args, **kwargs):
                            if kwargs.get('name') == kind + '-writer' and stage == 'construction':
                                pause()
                            worker = original_thread(*args, **kwargs)
                            workers.append(worker)
                            return worker

                        def start(worker):
                            if worker.name == kind + '-writer':
                                if stage == 'start':
                                    pause()
                                original_start(worker)
                                if outcome == 'late_error':
                                    raise primary
                            else:
                                original_start(worker)

                        def construct():
                            try:
                                results.append(factory(mock_output=True, retain_owner=retain))
                            except BaseException as exc:
                                errors.append(exc)

                        constructor = original_thread(target=construct, name='independent-constructor')
                        try:
                            with patch.object(threading, 'Thread', create), patch.object(original_thread, 'start', start):
                                original_start(constructor)
                                self.assertTrue(reached.wait(1))
                                owner = owners[0]
                                before = time.monotonic()
                                owner.close()
                                self.assertLess(time.monotonic() - before, .1)
                                self.assertTrue(owner._closing)
                                self.assertFalse(owner.worker_alive)
                                self.assertEqual(handle.close_threads, [])
                                proceed.set()
                                constructor.join(2)
                                self.assertFalse(constructor.is_alive())
                                if outcome == 'return':
                                    self.assertEqual(errors, [])
                                    self.assertEqual(results, [owner])
                                else:
                                    self.assertEqual(results, [])
                                    self.assertEqual(errors, [primary])
                                self.assertTrue(handle.entered.wait(1))
                                admitted = [worker for worker in workers if worker.ident is not None]
                                self.assertEqual(admitted, [owner._thread])
                                self.assertEqual(len(handle.close_threads), 1)
                                self.assertFalse(owner.closed)
                                self.assertFalse(owner.port_handoff_ready)
                        finally:
                            proceed.set()
                            handle.release.set()
                            constructor.join(2)
                            for worker in workers:
                                if worker.ident is not None:
                                    worker.join(2)
                                    self.assertFalse(worker.is_alive())
                            for owner in owners:
                                owner.close()
                                self.assertTrue(owner.wait_closed(2))


if __name__ == '__main__':
    unittest.main()
