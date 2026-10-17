"""Port discovery must retry rejected admission without duplicating live work."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client import serial_ports


class PortDiscoveryAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (("_port_refresh_running", False), ("_port_cache", []),
                            ("_port_cache_at", float("-inf")), ("_port_cache_generation", 0)):
            self.stack.enter_context(patch.object(serial_ports, name, value))
        self.stack.enter_context(patch.object(serial_ports, "_scan_serial_ports", return_value=[]))

    def assert_retry_can_publish(self):
        scanned = threading.Event()
        port = {"device": "TEST_ONLY", "name": "TEST_ONLY"}
        workers = []
        real_thread = threading.Thread

        def scan():
            scanned.set()
            return [port]

        def create(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        with patch.object(serial_ports, "_scan_serial_ports", side_effect=scan), \
             patch.object(serial_ports.threading, "Thread", side_effect=create):
            serial_ports.port_snapshot()
            self.assertTrue(scanned.wait(1.0), "rejected admission permanently suppressed discovery")
            for worker in workers:
                worker.join(1.0)
                self.assertFalse(worker.is_alive())
            self.assertEqual(serial_ports.port_snapshot(), [port])
        self.assertFalse(serial_ports._port_refresh_running)

    def test_constructor_rejection_clears_admission_and_preserves_error_identity(self):
        failure = RuntimeError("discovery constructor rejected")
        with patch.object(serial_ports.threading, "Thread", side_effect=failure):
            with self.assertRaises(RuntimeError) as caught:
                serial_ports.port_snapshot()
        self.assertIs(caught.exception, failure)
        self.assertFalse(serial_ports._port_refresh_running)
        self.assert_retry_can_publish()

    def test_start_rejection_clears_admission_and_allows_invalidated_retry(self):
        failure = RuntimeError("discovery start rejected")
        with patch.object(serial_ports.threading.Thread, "start", side_effect=failure):
            with self.assertRaises(RuntimeError) as caught:
                serial_ports.port_snapshot()
        self.assertIs(caught.exception, failure)
        serial_ports.invalidate_port_cache()
        self.assertFalse(serial_ports._port_refresh_running)
        self.assert_retry_can_publish()

    def test_late_start_error_does_not_clear_a_genuinely_admitted_refresh(self):
        entered, release = threading.Event(), threading.Event()
        real_start = threading.Thread.start
        failure = RuntimeError("wrapper failed after admission")
        workers = []

        def scan():
            entered.set()
            release.wait(2.0)
            return []

        def admit_then_raise(worker):
            workers.append(worker)
            real_start(worker)
            self.assertTrue(entered.wait(1.0))
            raise failure

        try:
            with patch.object(serial_ports, "_scan_serial_ports", side_effect=scan), \
                 patch.object(threading.Thread, "start", admit_then_raise):
                with self.assertRaises(RuntimeError) as caught:
                    serial_ports.port_snapshot()
                self.assertIs(caught.exception, failure)
                self.assertTrue(serial_ports._port_refresh_running)
                self.assertEqual(serial_ports.port_snapshot(), [])
                self.assertEqual(len(workers), 1)
        finally:
            release.set()
            for worker in workers:
                worker.join(1.0)
                self.assertFalse(worker.is_alive())
        self.assertFalse(serial_ports._port_refresh_running)


    def test_rejected_admission_leaves_cache_and_invalidation_evidence_unchanged(self):
        cached = [{"device": "OLD", "name": "OLD"}]
        serial_ports._port_cache = cached
        serial_ports._port_cache_at = 100.0
        serial_ports._port_cache_generation = 7
        for failure in (RuntimeError("rejected"), KeyboardInterrupt("cancelled"), SystemExit("exit")):
            for target in ("constructor", "start"):
                with self.subTest(failure=type(failure).__name__, target=target):
                    patched = (patch.object(threading, "Thread", side_effect=failure)
                               if target == "constructor" else
                               patch.object(threading.Thread, "start", side_effect=failure))
                    with patch.object(serial_ports.time, "monotonic", return_value=102.0), patched:
                        with self.assertRaises(type(failure)) as caught:
                            serial_ports.port_snapshot()
                    self.assertIs(caught.exception, failure)
                    self.assertFalse(serial_ports.discovery_pending())
                    self.assertIs(serial_ports._port_cache, cached)
                    self.assertEqual(serial_ports._port_cache_at, 100.0)
                    self.assertEqual(serial_ports._port_cache_generation, 7)

    def test_pending_constructor_does_not_block_other_snapshots_or_duplicate_admission(self):
        constructing, release_constructor, second_returned = (threading.Event() for _ in range(3))
        cached = [{"device": "OLD"}]
        serial_ports._port_cache = cached
        workers, errors = [], []
        real_thread = threading.Thread

        def create(*args, **kwargs):
            constructing.set()
            if not release_constructor.wait(2.0):
                raise TimeoutError("test constructor not released")
            worker = real_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        def request(*, second=False):
            try:
                self.assertEqual(serial_ports.port_snapshot(), cached)
                if second:
                    second_returned.set()
            except BaseException as exc:
                errors.append(exc)

        first = real_thread(target=request)
        second = real_thread(target=lambda: request(second=True))
        try:
            with patch.object(threading, "Thread", side_effect=create), \
                 patch.object(serial_ports, "_scan_serial_ports", return_value=cached):
                first.start()
                self.assertTrue(constructing.wait(1.0))
                second.start()
                self.assertTrue(second_returned.wait(1.0), "snapshot waited for another admission")
                self.assertTrue(serial_ports.discovery_pending())
                self.assertEqual(workers, [])
                release_constructor.set()
                first.join(1.0)
                second.join(1.0)
                for worker in workers:
                    worker.join(1.0)
                self.assertEqual(len(workers), 1)
                self.assertEqual(errors, [])
        finally:
            release_constructor.set()
            for worker in [first, second, *workers]:
                if worker.ident is not None:
                    worker.join(1.0)
                    self.assertFalse(worker.is_alive())
        self.assertFalse(serial_ports.discovery_pending())

    def test_simultaneous_snapshot_callers_share_one_admitted_scan(self):
        launch = threading.Barrier(17)
        scan_entered, release_scan = threading.Event(), threading.Event()
        workers, results, errors = [], [], []
        real_thread = threading.Thread

        def scan():
            scan_entered.set()
            if not release_scan.wait(2.0):
                raise TimeoutError("test scan not released")
            return [{"device": "NEW"}]

        def request():
            try:
                launch.wait(1.0)
                results.append(serial_ports.port_snapshot())
            except BaseException as exc:
                errors.append(exc)

        def create(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        callers = [real_thread(target=request) for _ in range(16)]
        try:
            with patch.object(threading, "Thread", side_effect=create), \
                 patch.object(serial_ports, "_scan_serial_ports", side_effect=scan):
                for caller in callers:
                    caller.start()
                launch.wait(1.0)
                self.assertTrue(scan_entered.wait(1.0))
                for caller in callers:
                    caller.join(1.0)
                    self.assertFalse(caller.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(results, [[]] * 16)
                self.assertEqual(len(workers), 1)
                self.assertTrue(serial_ports.discovery_pending())
        finally:
            release_scan.set()
            for worker in [*callers, *workers]:
                if worker.ident is not None:
                    worker.join(1.0)
                    self.assertFalse(worker.is_alive())
        self.assertFalse(serial_ports.discovery_pending())

    def test_finished_worker_is_not_mistaken_for_rejected_start(self):
        failure = RuntimeError("late error after worker finished")
        port = {"device": "NEW"}
        real_start = threading.Thread.start
        workers = []

        def complete_then_raise(worker):
            workers.append(worker)
            real_start(worker)
            worker.join(1.0)
            self.assertFalse(worker.is_alive())
            raise failure

        with patch.object(serial_ports, "_scan_serial_ports", return_value=[port]), \
             patch.object(threading.Thread, "start", complete_then_raise):
            with self.assertRaises(RuntimeError) as caught:
                serial_ports.port_snapshot()
            self.assertIs(caught.exception, failure)
            self.assertIsNotNone(workers[0].ident)
            self.assertFalse(serial_ports.discovery_pending())
            self.assertEqual(serial_ports.port_snapshot(), [port])
            self.assertEqual(len(workers), 1)
        serial_ports.invalidate_port_cache()
        self.assert_retry_can_publish()

    def test_late_old_error_cannot_clear_an_admitted_successor(self):
        successor_entered, release_successor = threading.Event(), threading.Event()
        first_port, second_port = {"device": "FIRST"}, {"device": "SECOND"}
        failure = RuntimeError("old start wrapper failed after successor admission")
        real_start = threading.Thread.start
        workers, scans = [], []

        def scan():
            scans.append(threading.current_thread())
            if len(scans) == 1:
                return [first_port]
            successor_entered.set()
            if not release_successor.wait(2.0):
                raise TimeoutError("test successor not released")
            return [second_port]

        def start(worker):
            workers.append(worker)
            real_start(worker)
            if len(workers) == 1:
                worker.join(1.0)
                self.assertFalse(worker.is_alive())
                serial_ports.invalidate_port_cache()
                self.assertEqual(serial_ports.port_snapshot(), [first_port])
                self.assertTrue(successor_entered.wait(1.0))
                raise failure

        try:
            with patch.object(serial_ports, "_scan_serial_ports", side_effect=scan), \
                 patch.object(threading.Thread, "start", start):
                with self.assertRaises(RuntimeError) as caught:
                    serial_ports.port_snapshot()
                self.assertIs(caught.exception, failure)
                self.assertTrue(serial_ports.discovery_pending())
                for _ in range(20):
                    self.assertEqual(serial_ports.port_snapshot(), [first_port])
                self.assertEqual(len(workers), 2)
                self.assertEqual(len(scans), 2)
        finally:
            release_successor.set()
            for worker in workers:
                worker.join(1.0)
                self.assertFalse(worker.is_alive())
        self.assertFalse(serial_ports.discovery_pending())
        self.assertEqual(serial_ports.port_snapshot(), [second_port])

    def test_invalidation_discards_inflight_result_without_starting_automatic_replacement(self):
        entered, release = threading.Event(), threading.Event()
        serial_ports._port_cache = [{"device": "OLD"}]
        workers, scans = [], []
        real_thread = threading.Thread

        def scan():
            scans.append(threading.current_thread())
            if len(scans) == 1:
                entered.set()
                if not release.wait(2.0):
                    raise TimeoutError("test invalidated scan not released")
                return [{"device": "STALE"}]
            return [{"device": "CURRENT"}]

        def create(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        try:
            with patch.object(serial_ports, "_scan_serial_ports", side_effect=scan), \
                 patch.object(threading, "Thread", side_effect=create):
                self.assertEqual(serial_ports.port_snapshot(), [{"device": "OLD"}])
                self.assertTrue(entered.wait(1.0))
                for _ in range(3):
                    serial_ports.invalidate_port_cache()
                    self.assertEqual(serial_ports.port_snapshot(), [{"device": "OLD"}])
                self.assertEqual(len(workers), 1)
                release.set()
                workers[0].join(1.0)
                self.assertFalse(workers[0].is_alive())
                self.assertFalse(serial_ports.discovery_pending())
                self.assertEqual(serial_ports._port_cache, [{"device": "OLD"}])
                self.assertEqual(serial_ports._port_cache_at, float("-inf"))
                self.assertEqual(serial_ports._port_cache_generation, 3)
                self.assertEqual(len(scans), 1)
                serial_ports.port_snapshot()
                workers[1].join(1.0)
                self.assertFalse(workers[1].is_alive())
                self.assertEqual(serial_ports.port_snapshot(), [{"device": "CURRENT"}])
                self.assertEqual(len(scans), 2)
        finally:
            release.set()
            for worker in workers:
                worker.join(1.0)
                self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
