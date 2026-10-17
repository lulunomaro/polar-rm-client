"""Operator E-stop must not wait for a serial driver's unrelated write lock."""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.backend.manager import BackendManager


class BackendEstopNonblockingTests(unittest.TestCase):
    def test_actual_serial_write_lock_cannot_delay_polar_estop(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root}), \
                patch.object(BackendManager, '_build_commit', return_value='TEST'):
            backend = BackendManager(workspace=root)
            entered, release = threading.Event(), threading.Event()
            def stalled_driver():
                with backend.serial._write_lock:
                    entered.set()
                    release.wait(1)  # Bound a failing regression's wait, never certify closure.
            worker = threading.Thread(target=stalled_driver)
            worker.start()
            self.assertTrue(entered.wait(1))
            try:
                with patch.object(backend.polar, 'request_estop', wraps=backend.polar.request_estop) as polar:
                    started = time.perf_counter()
                    backend.trigger_estop()  # Actual manager + serial request, on the owner.
                    elapsed = time.perf_counter() - started
                    self.assertLess(elapsed, .1)
                    self.assertTrue(worker.is_alive())
                    self.assertTrue(backend.safety.estop_active)
                    self.assertTrue(backend.serial.estop_active)
                    polar.assert_called_once_with()
            finally:
                release.set()
                worker.join(1)
                backend.shutdown()

    def test_actual_manager_tick_revokes_without_waiting_and_restores_on_later_tick(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root}), \
                patch.object(BackendManager, '_build_commit', return_value='TEST'):
            backend = BackendManager(workspace=root)
            entered, release = threading.Event(), threading.Event()
            def stalled_driver():
                with backend.serial._write_lock:
                    entered.set()
                    release.wait(1)
            worker = threading.Thread(target=stalled_driver)
            worker.start()
            self.assertTrue(entered.wait(1))
            try:
                backend.safety.estop_active = True
                before = time.perf_counter()
                backend.tick()
                self.assertLess(time.perf_counter() - before, .1)
                self.assertTrue(backend.serial.estop_active)
                self.assertTrue(worker.is_alive())
                backend.safety.estop_active = False  # Explicit authority release already happened.
                before = time.perf_counter()
                backend.tick()
                self.assertLess(time.perf_counter() - before, .1)
                self.assertTrue(backend.serial.estop_active, 'permissive restoration waits while driver owns lock')
                self.assertTrue(worker.is_alive())
                release.set()
                worker.join(1)
                backend.tick()
                self.assertFalse(backend.serial.estop_active, 'later tick must not lose restoration')
            finally:
                release.set()
                worker.join(1)
                backend.shutdown()

    def test_release_estop_is_prompt_and_keeps_serial_latched_until_idle_tick(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root}), \
                patch.object(BackendManager, '_build_commit', return_value='TEST'):
            backend = BackendManager(workspace=root)
            backend.trigger_estop()
            entered, release = threading.Event(), threading.Event()
            def stalled_driver():
                with backend.serial._write_lock:
                    entered.set()
                    release.wait(1)
            worker = threading.Thread(target=stalled_driver)
            worker.start()
            self.assertTrue(entered.wait(1))
            try:
                before = time.perf_counter()
                backend.release_estop()
                self.assertLess(time.perf_counter() - before, .1)
                self.assertTrue(worker.is_alive())
                self.assertFalse(backend.safety.estop_active)
                self.assertEqual(backend.safety.control_state.value, 'LOCKED')
                self.assertTrue(backend.serial.estop_active)
                release.set()
                worker.join(1)
                backend.tick()
                self.assertFalse(backend.serial.estop_active)
                self.assertEqual(backend.safety.control_state.value, 'LOCKED')
            finally:
                release.set()
                worker.join(1)
                backend.shutdown()

    def test_new_estop_during_owner_release_wins_over_stale_serial_restoration(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root}), \
                patch.object(BackendManager, '_build_commit', return_value='TEST'):
            backend = BackendManager(workspace=root)
            backend.trigger_estop()
            entered, release = threading.Event(), threading.Event()
            class SafetyView:
                reads = 0
                @property
                def estop_active(self):
                    self.reads += 1
                    value = backend.safety.estop_active
                    if self.reads == 2:
                        entered.set()
                        if not release.wait(1):
                            raise TimeoutError('test E-stop did not finish')
                    return value
            backend.serial.safety = SafetyView()
            errors = []
            def new_estop():
                try:
                    if not entered.wait(1):
                        raise TimeoutError('serial restoration did not reach second safety read')
                    backend.trigger_estop()
                except Exception as exc:
                    errors.append(exc)
                finally:
                    release.set()
            worker = threading.Thread(target=new_estop)
            worker.start()
            try:
                backend.release_estop()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertTrue(backend.safety.estop_active)
                self.assertTrue(backend.serial.estop_active)
                self.assertTrue(backend.polar._estop_active)
                self.assertEqual(backend.safety.control_state.value, 'ESTOP')
            finally:
                release.set()
                worker.join(1)
                backend.serial.safety = backend.safety
                backend.shutdown()

    def test_stale_restore_cannot_clear_a_newer_estop_request(self):
        from rm_client.backend.serial_backend import RmSerialBackend
        entered, release = threading.Event(), threading.Event()
        class SafetySnapshot:
            value = False
            reads = 0
            @property
            def estop_active(self):
                self.reads += 1
                value = self.value
                if self.reads == 2:
                    entered.set()
                    release.wait(1)
                return value
        safety = SafetySnapshot()
        serial = RmSerialBackend(safety=safety)
        serial.estop_active = True
        ticker = threading.Thread(target=serial.tick)
        ticker.start()
        self.assertTrue(entered.wait(1))
        try:
            safety.value = True
            before = time.perf_counter()
            serial.request_estop()
            self.assertLess(time.perf_counter() - before, .1)
            release.set()
            ticker.join(1)
            self.assertFalse(ticker.is_alive())
            self.assertTrue(serial.estop_active, 'old permissive snapshot must not erase newer latch')
            serial.tick()
            self.assertTrue(serial.estop_active)
            safety.value = False
            serial.tick()
            self.assertFalse(serial.estop_active, 'fresh explicit restoration remains possible')
        finally:
            release.set()
            ticker.join(1)


if __name__ == '__main__':
    unittest.main()
