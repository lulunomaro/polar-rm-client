"""Serial starter admission failures retain only genuinely admitted owners."""
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend.lifecycle import close_serial
from rm_client.backend.manager import BackendManager
from rm_client.model import RobotState


class Handle:
    def __init__(self):
        self.is_open = True
        self.fail_close = False
        self.close_threads = []

    def read(self, size):
        time.sleep(.001)
        return b""

    def close(self):
        self.close_threads.append(threading.get_ident())
        if self.fail_close:
            raise OSError("fake serial close refused")
        self.is_open = False


class SerialStarterAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": self.temp.name}), \
                patch.object(BackendManager, "_build_commit", return_value="TEST"):
            self.manager = BackendManager(workspace=self.temp.name, state=RobotState())
        self.owner = threading.get_ident()
        self.handle = Handle()
        self.open_entered, self.open_release = threading.Event(), threading.Event()
        self.open_release.set()
        self.created = []
        self.starts = []
        self.stops = []
        self.fail_stop = False
        original_start = self.manager.serial.start
        original_stop = self.manager.serial.stop

        def serial_start():
            self.starts.append(threading.get_ident())
            self.manager.serial.port = "FAKE"
            original_start()

        def serial_stop():
            self.stops.append(threading.get_ident())
            if self.fail_stop:
                raise OSError("fake serial stop refused")
            original_stop()

        self.manager.serial.start = serial_start
        self.manager.serial.stop = serial_stop
        self.addCleanup(self.cleanup)
        for target in (patch.object(self.manager.mqtt, "start"),
                       patch.object(self.manager.polar, "request_start"),
                       patch("serial.Serial", side_effect=self.open_serial)):
            target.start()
            self.addCleanup(target.stop)

    def open_serial(self, **kwargs):
        self.open_entered.set()
        if not self.open_release.wait(2):
            raise TimeoutError("fake serial open not released")
        return self.handle

    def cleanup(self):
        self.open_release.set()
        self.handle.fail_close = self.fail_stop = False
        for worker in self.created:
            if worker.ident is not None:
                worker.join(2)
        self.manager.shutdown()
        if self.manager.shutdown_completion and not self.manager.shutdown_completion.may_exit:
            self.manager.retry_shutdown()
            self.settle()
        self.manager.serial.stop()
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
            if worker.name != "backend-start-serial":
                return original_start(worker)
            self.created.append(worker)
            if mode != "never":
                original_start(worker)
                self.assertTrue(self.open_entered.wait(1))
                if mode == "finished":
                    worker.join(1)
                    self.assertFalse(worker.is_alive())
            raise RuntimeError("injected serial starter admission failure")

        with patch.object(threading.Thread, "start", new=start):
            self.manager.request_scenario("LabDebug", start=True)
            result = self.settle()
        self.assertFalse(result.succeeded)
        self.assertIn("injected serial starter", result.failures[0].error)
        self.assertTrue(self.manager.safety.estop_active)
        self.assertTrue(self.manager._backend_transition_blocks_work())
        return self.created[-1]

    def test_never_admitted_starter_does_not_poison_final_shutdown(self):
        worker = self.fail_admission("never")
        stop_count = len(self.stops)
        self.manager.request_shutdown()
        result = self.settle()
        self.assertTrue(result.may_exit, result.failures)
        self.assertTrue(result.succeeded)
        self.assertIsNone(worker.ident)
        self.assertEqual(self.starts, [])
        self.assertEqual(len(self.stops), stop_count + 1)
        self.assertIsNone(self.manager._lifecycle._owners.serial_starter)

    def test_never_admitted_starter_does_not_poison_explicit_close_retry(self):
        self.fail_admission("never")
        self.fail_stop = True
        self.manager.request_shutdown()
        first = self.settle()
        self.assertFalse(first.may_exit)
        self.assertIn("fake serial stop refused", first.failures[0].error)
        self.fail_stop = False
        self.manager.retry_shutdown()
        result = self.settle()
        self.assertTrue(result.may_exit, result.failures)
        self.assertIs(self.manager.initial_shutdown_completion, first)
        self.assertTrue(self.manager.safety.estop_active)
        self.assertEqual(self.starts, [])

    def test_started_then_raised_owner_blocks_close_until_late_open_finishes(self):
        self.open_release.clear()
        worker = self.fail_admission("running")
        self.assertTrue(worker.is_alive())
        self.assertIs(self.manager._serial_start_thread, worker)
        stop_count = len(self.stops)
        self.manager.request_shutdown()
        self.assertIs(self.manager._lifecycle._owners.serial_starter, worker)
        for _ in range(10):
            self.assertIsNone(self.manager.poll_lifecycle())
            self.assertTrue(self.manager.lifecycle_pending)
        self.assertEqual(len(self.stops), stop_count)
        self.assertIsNone(self.manager.serial._serial)
        self.open_release.set()
        result = self.settle()
        self.assertTrue(result.may_exit, result.failures)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.handle.is_open)
        self.assertIsNone(self.manager.serial._serial)
        self.assertTrue(all(thread != self.owner for thread in self.handle.close_threads))

    def test_finished_then_raised_owner_remains_joinable_for_failed_close_retry(self):
        worker = self.fail_admission("finished")
        self.assertFalse(worker.is_alive())
        self.assertIsNotNone(worker.ident)
        self.assertIs(self.manager._serial_start_thread, worker)
        self.handle.fail_close = True
        self.manager.request_shutdown()
        first = self.settle()
        self.assertFalse(first.may_exit)
        self.assertIs(self.manager._lifecycle._owners.serial_starter, worker)
        self.assertIs(self.manager.serial._serial, self.handle)
        self.assertTrue(self.handle.is_open)
        self.assertNotIn("cannot join", first.failures[0].error)
        self.handle.fail_close = False
        self.manager.retry_shutdown()
        result = self.settle()
        self.assertTrue(result.may_exit, result.failures)
        self.assertFalse(self.handle.is_open)
        self.assertEqual(len(self.starts), 1)
        self.assertIs(self.manager.initial_shutdown_completion, first)

    def test_close_adapter_still_rejects_an_unjoinable_captured_owner(self):
        worker = threading.Thread(target=lambda: None)
        with self.assertRaisesRegex(RuntimeError, "cannot join thread before it is started"):
            close_serial(self.manager.serial, worker)
        self.assertEqual(self.stops, [])


if __name__ == "__main__":
    unittest.main()
