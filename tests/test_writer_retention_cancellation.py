"""Independent negative control for cancellation through the new retention port."""
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_writer_constructor_admission import FACTORIES, Handle


class WriterRetentionCancellationTests(unittest.TestCase):
    def test_retention_callback_close_cannot_admit_a_second_native_owner(self):
        for kind, factory in FACTORIES.items():
            with self.subTest(kind=kind):
                handle = Handle(blocked=True)
                retained = []
                workers = []

                def retain(owner):
                    retained.append(owner)
                    if kind == 'dt7':
                        owner._link._handle = handle
                        owner._link.resolved_port = 'FAKE'
                        owner._link._handle_requested_port = owner.port
                    else:
                        owner._serial = handle
                    owner.close()
                    workers.append(owner._thread)
                    self.assertTrue(handle.entered.wait(1))
                    self.assertFalse(owner.closed)

                try:
                    owner = factory(mock_output=True, retain_owner=retain)
                    workers.append(owner._thread)
                    self.assertIs(owner, retained[0])
                    self.assertTrue(owner._closing)
                    # An incorrect second owner is real and can reach the
                    # same blocked native handle before this observation.
                    deadline = time.monotonic() + 0.1
                    while len(handle.close_threads) < 2 and time.monotonic() < deadline:
                        time.sleep(.001)
                    self.assertIs(owner._thread, workers[0])
                    self.assertEqual(len(handle.close_threads), 1)
                    self.assertFalse(owner.closed)
                    self.assertFalse(owner.port_handoff_ready)
                finally:
                    handle.release.set()
                    for worker in set(workers):
                        if worker is not None and worker.ident is not None:
                            worker.join(2)
                            self.assertFalse(worker.is_alive())
                    for owner in retained:
                        owner.close()
                        self.assertTrue(owner.wait_closed(2))


if __name__ == '__main__':
    unittest.main()
