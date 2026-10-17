"""Thread-owner contracts; real threads, no device or GUI dependencies."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.backend.writer_thread import WriterThread


class WriterThreadOwnerTests(unittest.TestCase):
    def setUp(self):
        self.condition = threading.Condition()
        self.owner = WriterThread(self.condition)
        self.release = threading.Event()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.release.set()
        self.owner.join(2)
        self.assertFalse(self.owner.alive)

    def reserve(self):
        with self.condition:
            self.owner.reserve()
        self.assertTrue(self.owner.starting)

    def test_reservation_is_not_worker_admission(self):
        self.reserve()
        self.assertTrue(self.owner.never_admitted)
        self.assertFalse(self.owner.alive)
        self.owner.join(None)
        self.owner.start_reserved(self.release.wait, 'reserved-writer')
        self.assertFalse(self.owner.starting)
        self.assertFalse(self.owner.never_admitted)
        self.assertTrue(self.owner.alive)

    def test_construct_rejection_releases_reservation_and_preserves_previous_owner(self):
        self.owner.start(lambda: None, 'previous-writer')
        self.owner.join(2)
        previous = self.owner.thread
        primary = RuntimeError('thread construction rejected')
        self.reserve()
        with patch.object(threading, 'Thread', side_effect=primary):
            with self.assertRaises(RuntimeError) as raised:
                self.owner.start_reserved(lambda: None, 'rejected-writer')
        self.assertIs(raised.exception, primary)
        self.assertIs(self.owner.thread, previous)
        self.assertFalse(self.owner.starting)
        self.assertFalse(self.owner.never_admitted)

    def test_start_rejection_retains_unjoinable_thread_and_clears_reservation(self):
        primary = RuntimeError('start rejected')
        self.reserve()
        with patch.object(threading.Thread, 'start', side_effect=primary):
            with self.assertRaises(RuntimeError) as raised:
                self.owner.start_reserved(lambda: None, 'rejected-writer')
        self.assertIs(raised.exception, primary)
        self.assertIsNotNone(self.owner.thread)
        self.assertTrue(self.owner.never_admitted)
        self.assertFalse(self.owner.starting)
        self.owner.join(None)

    def test_late_start_failure_retains_live_owner_without_claiming_rejection(self):
        original = threading.Thread.start
        primary = KeyboardInterrupt('late wrapper failure')

        def admit_then_raise(thread):
            original(thread)
            raise primary

        self.reserve()
        with patch.object(threading.Thread, 'start', admit_then_raise):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.owner.start_reserved(self.release.wait, 'late-writer')
        self.assertIs(raised.exception, primary)
        self.assertFalse(self.owner.starting)
        self.assertFalse(self.owner.never_admitted)
        self.assertTrue(self.owner.alive)
        self.owner.join(-1)
        self.assertTrue(self.owner.alive)

    def test_join_from_owned_worker_does_not_join_itself(self):
        results = []

        def work():
            self.owner.join(None)
            results.append('returned')

        self.owner.start(work, 'self-observing-writer')
        self.owner.join(2)
        self.assertEqual(results, ['returned'])


if __name__ == '__main__':
    unittest.main()
