"""Explicit retry admission cannot reentrantly create a second native closer.

Real threads and fake handles only. Constructor/start wrappers model defensive
admission faults; this does not claim ordinary Thread.start reenters callers.
"""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_writer_constructor_admission import FACTORIES, Handle


class WriterRetryReentrancyTests(unittest.TestCase):
    def test_retry_reserves_owner_through_reentrant_constructor_and_start(self):
        real_thread, real_start = threading.Thread, threading.Thread.start
        for kind, factory in FACTORIES.items():
            for stage in ("constructor", "start"):
                for outcome in ("return", "reject", "late_error"):
                    with self.subTest(kind=kind, stage=stage, outcome=outcome):
                        owner = factory(mock_output=True)
                        handle = Handle(mode="raise")
                        if kind == "dt7":
                            owner._link._handle = handle
                            owner._link.resolved_port = "FAKE"
                            owner._link._handle_requested_port = owner.port
                        else:
                            owner._serial = handle
                        owner.close()
                        self.assertFalse(owner.wait_closed(2))
                        self.assertFalse(owner.worker_alive)
                        handle.mode = "ok"
                        handle.release.clear()
                        handle.entered.clear()
                        handle.close_threads.clear()
                        workers, reentered = [], []
                        primary = OSError("injected retry admission failure")

                        def reenter():
                            if not reentered:
                                reentered.append(True)
                                owner.close()

                        def construct(*args, **kwargs):
                            if stage == "constructor":
                                reenter()
                                if outcome == "reject":
                                    raise primary
                            worker = real_thread(*args, **kwargs)
                            workers.append(worker)
                            return worker

                        def start(worker):
                            if stage == "start":
                                reenter()
                                if outcome == "reject":
                                    raise primary
                            real_start(worker)
                            if outcome == "late_error":
                                raise primary

                        try:
                            with patch.object(threading, "Thread", construct), patch.object(
                                    real_thread, "start", start):
                                if outcome == "return":
                                    owner.close()
                                else:
                                    with self.assertRaises(OSError) as caught:
                                        owner.close()
                                    self.assertIs(caught.exception, primary)
                            admitted = [worker for worker in workers if worker.ident is not None]
                            if outcome == "reject":
                                self.assertEqual(admitted, [])
                                self.assertEqual(handle.close_threads, [])
                            else:
                                self.assertEqual(admitted, [owner._thread])
                                self.assertTrue(handle.entered.wait(1))
                                self.assertEqual(len(handle.close_threads), 1)
                                self.assertFalse(owner.wait_closed(0))
                            self.assertFalse(owner.closed)
                            self.assertFalse(owner.port_handoff_ready)
                            self.assertFalse(owner._worker.starting)
                        finally:
                            handle.release.set()
                            for worker in workers:
                                if worker.ident is not None:
                                    worker.join(2)
                                    self.assertFalse(worker.is_alive())
                            owner.close()
                            self.assertTrue(owner.wait_closed(2))
                        self.assertTrue(owner.port_handoff_ready)


if __name__ == "__main__":
    unittest.main()
