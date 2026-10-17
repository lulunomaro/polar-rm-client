"""Real serial reader admission failures preserve native-handle ownership."""
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend.manager import BackendManager


class Handle:
    def __init__(self):
        self.is_open = True
        self.fail_close = False
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.close_threads = []

    def read(self, size):
        self.entered.set()
        if not self.release.wait(2):
            raise TimeoutError("fake reader not released")
        raise OSError("fake reader completed")

    def close(self):
        self.close_threads.append(threading.get_ident())
        if self.fail_close:
            raise OSError("fake native close refused")
        self.is_open = False


class SerialReaderAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": self.temp.name}), \
                patch.object(BackendManager, "_build_commit", return_value="TEST"):
            self.manager = BackendManager(workspace=self.temp.name)
        self.serial = self.manager.serial
        self.serial.port = "FAKE"
        self.handle = Handle()
        self.created = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.handle.fail_close = False
        self.handle.release.set()
        for worker in self.created:
            if worker.ident is not None:
                worker.join(2)
        self.manager.shutdown()
        if self.manager.shutdown_completion and not self.manager.shutdown_completion.may_exit:
            self.manager.retry_shutdown()
            self.settle()
        self.serial.stop()
        self.manager.field_log.close(1)

    def settle(self):
        deadline = time.monotonic() + 3
        result = None
        while self.manager.lifecycle_pending:
            completion = self.manager.poll_lifecycle()
            if completion is not None:
                result = completion
            if time.monotonic() >= deadline:
                self.fail("lifecycle did not settle")
            time.sleep(.001)
        return result

    def fail_admission(self, mode):
        original_start = threading.Thread.start

        def start(worker):
            if worker.name != "rm-serial":
                return original_start(worker)
            self.created.append(worker)
            if mode != "never":
                original_start(worker)
                self.assertTrue(self.handle.entered.wait(1))
                if mode == "finished":
                    worker.join(1)
                    self.assertFalse(worker.is_alive())
            raise RuntimeError("injected serial reader admission failure")

        with patch("serial.Serial", return_value=self.handle) as opener, \
                patch.object(threading.Thread, "start", new=start):
            self.serial.start()
        self.assertEqual(opener.call_count, 1)
        self.assertFalse(self.serial.connected)
        self.assertFalse(self.serial._running)
        return self.created[-1]

    def test_never_started_reader_closes_driver_without_false_final_failure(self):
        worker = self.fail_admission("never")
        self.assertIsNone(worker.ident)
        self.assertFalse(self.handle.is_open)
        self.assertIsNone(self.serial._serial)
        self.manager.request_shutdown()
        result = self.settle()
        self.assertTrue(result.may_exit, result.failures)
        self.assertTrue(result.succeeded)
        self.assertIn("injected serial reader", self.serial.last_error)

    def test_never_started_reader_retains_failed_native_close_until_explicit_retry(self):
        self.handle.fail_close = True
        self.fail_admission("never")
        self.assertIs(self.serial._serial, self.handle)
        self.assertTrue(self.handle.is_open)
        with patch("serial.Serial", side_effect=AssertionError("replacement before close")):
            self.serial.start()
        self.manager.request_shutdown()
        first = self.settle()
        self.assertFalse(first.may_exit)
        self.assertIn("fake native close refused", first.failures[0].error)
        self.assertNotIn("cannot join", first.failures[0].error)
        self.assertIs(self.serial._serial, self.handle)
        self.handle.fail_close = False
        self.manager.retry_shutdown()
        result = self.settle()
        self.assertTrue(result.may_exit, result.failures)
        self.assertIsNone(self.serial._serial)
        self.assertFalse(self.handle.is_open)
        self.assertIs(self.manager.initial_shutdown_completion, first)

    def test_started_then_raised_reader_is_retained_until_it_exits(self):
        self.handle.release.clear()
        worker = self.fail_admission("running")
        self.assertTrue(worker.is_alive())
        self.assertIs(self.serial._reader, worker)
        self.assertFalse(self.handle.is_open)
        self.manager.request_shutdown()
        for _ in range(10):
            self.assertIsNone(self.manager.poll_lifecycle())
            self.assertTrue(self.manager.lifecycle_pending)
        self.handle.release.set()
        result = self.settle()
        self.assertTrue(result.may_exit, result.failures)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(self.serial._reader)

    def test_finished_then_raised_reader_remains_joinable_for_final_close(self):
        worker = self.fail_admission("finished")
        self.assertIsNotNone(worker.ident)
        self.assertFalse(worker.is_alive())
        self.assertIs(self.serial._reader, worker)
        self.manager.request_shutdown()
        result = self.settle()
        self.assertTrue(result.may_exit, result.failures)
        self.assertFalse(self.handle.is_open)
        self.assertIsNone(self.serial._reader)

    def test_finished_then_raised_reader_retains_failed_driver_close_for_retry(self):
        self.handle.fail_close = True
        worker = self.fail_admission("finished")
        self.assertIs(self.serial._reader, worker)
        self.assertIsNotNone(worker.ident)
        self.assertIs(self.serial._serial, self.handle)
        self.manager.request_shutdown()
        first = self.settle()
        self.assertFalse(first.may_exit)
        self.assertIn("fake native close refused", first.failures[0].error)
        self.handle.fail_close = False
        self.manager.retry_shutdown()
        result = self.settle()
        self.assertTrue(result.may_exit, result.failures)
        self.assertIsNone(self.serial._serial)
        self.assertFalse(self.handle.is_open)
        self.assertIs(self.manager.initial_shutdown_completion, first)


if __name__ == "__main__":
    unittest.main()
