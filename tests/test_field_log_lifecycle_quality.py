"""Deterministic field-log queue, loss accounting and lifecycle regressions."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from types import MappingProxyType
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.field_log import ReadOnlyFieldReplay, RotatingJsonlLogger


def until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("logger did not settle")
        time.sleep(0.001)


class FieldLogLifecycleQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "logs"

    def logger(self, **options):
        logger = RotatingJsonlLogger(self.root, **options)
        self.addCleanup(logger.close, 2.0)
        return logger

    def block_writer(self, logger):
        entered, release = threading.Event(), threading.Event()
        lines = []

        def write(line):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test writer was not released")
            lines.append(line)

        patcher = patch.object(logger, "_write_line", side_effect=write)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(release.set)
        return entered, release, lines

    def manager(self):
        from rm_client.backend.manager import BackendManager

        with patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": self.temp.name}):
            manager = BackendManager(workspace=self.temp.name, scenario_key="LabDebug")
        self.addCleanup(manager.field_log.close, 2.0)
        # This fixture exercises logger integration only, without starting or
        # waiting for network, serial, device or decoder resources.
        manager.controller_angles.close = Mock()
        manager.safety.trigger_estop = Mock()
        manager.serial.trigger_estop = manager.serial.stop = Mock()
        manager.polar.request_estop = manager.polar.stop = Mock()
        manager.mqtt.stop = manager.udp.stop = Mock()
        manager.native_decoder.stop = Mock()
        manager._native_recovery.reset = Mock()
        return manager

    def test_manager_scenario_shutdown_does_not_flush_close_or_wait_for_disk(self):
        manager = self.manager()
        logger = manager.field_log
        entered, release, _ = self.block_writer(logger)
        self.assertTrue(logger.emit("before_restart"))
        self.assertTrue(entered.wait(1))
        with patch.object(logger, "flush", wraps=logger.flush) as flush, patch.object(
            logger, "close", wraps=logger.close,
        ) as close:
            started = time.perf_counter()
            manager.shutdown(restart=True)
            self.assertLess(time.perf_counter() - started, 0.05)
            flush.assert_not_called()
            close.assert_not_called()
        self.assertFalse(logger.snapshot()["closing"])
        self.assertTrue(logger.emit("after_restart"))
        diagnostics = manager.stats()["field_log"]
        self.assertGreaterEqual(diagnostics["pending_records"], 3)
        self.assertIn("dropped_records", diagnostics)
        self.assertIn("pending_bytes", diagnostics)
        diagnostics["dropped_records"] = 99
        self.assertEqual(logger.snapshot()["dropped_records"], 0)
        release.set()
        self.assertTrue(logger.close(1))

    def test_manager_final_shutdown_drains_and_exposes_loss_warning(self):
        manager = self.manager()
        logger = manager.field_log
        self.assertTrue(logger.emit("before_shutdown"))
        manager.shutdown()
        self.assertTrue(logger.snapshot()["closed"])
        records = list(ReadOnlyFieldReplay([logger.path]).records())
        self.assertEqual(records[-1]["event"], "backend_shutdown")
        self.assertFalse(records[-1]["restart"])
        self.assertEqual(logger.snapshot()["pending_records"], 0)

        manager = self.manager()
        logger = manager.field_log
        self.assertFalse(logger.emit("invalid", payload=object()))
        with self.assertLogs("rm_client.backend.manager", level="WARNING") as warning:
            manager.shutdown()
        self.assertIn("records lost", warning.output[0])
        self.assertIn("'dropped_invalid': 1", warning.output[0])
        self.assertTrue(logger.snapshot()["closed"])

    def test_construction_and_unused_close_create_no_thread_or_files(self):
        logger = self.logger()
        self.assertFalse(logger.snapshot()["worker_active"])
        self.assertFalse(self.root.exists())
        self.assertTrue(logger.close(0))
        self.assertTrue(logger.close(0))
        self.assertTrue(logger.snapshot()["closed"])
        self.assertEqual(logger.snapshot()["worker_starts"], 0)
        self.assertFalse(self.root.exists())

    def test_disk_stall_does_not_block_producers_and_capacity_includes_inflight(self):
        logger = self.logger(queue_capacity=4)
        entered, release, lines = self.block_writer(logger)
        self.assertTrue(logger.emit("estop", index=0))
        self.assertTrue(entered.wait(1))
        started = time.perf_counter()
        for index in range(1, 11):
            self.assertEqual(logger.emit("estop", index=index), index < 4)
        self.assertLess(time.perf_counter() - started, 0.05)
        before = logger.snapshot()
        self.assertEqual(before["pending_records"], 4)
        self.assertEqual(before["dropped_full"], 7)
        self.assertEqual(before["worker_starts"], 1)
        self.assertFalse(logger.flush(0))
        release.set()
        until(lambda: logger.snapshot()["pending_records"] == 0)
        self.assertFalse(logger.flush(1), "overflow must not disappear after successful writes")
        self.assertEqual([json.loads(line)["index"] for line in lines], list(range(4)))
        self.assertEqual(logger.snapshot()["written_records"], 4)
        self.assertEqual(before["written_records"], 0, "snapshots must be detached")
        with self.assertRaises(TypeError):
            before["written_records"] = 100

    def test_queued_byte_budget_includes_inflight_and_rejects_whole_record(self):
        logger = self.logger(queue_capacity=100, queue_bytes=1024)
        entered, release, lines = self.block_writer(logger)
        self.assertTrue(logger.emit("audit", detail="x" * 600))
        self.assertTrue(entered.wait(1))
        self.assertFalse(logger.emit("audit", detail="x" * 600))
        snapshot = logger.snapshot()
        self.assertEqual(snapshot["pending_records"], 1)
        self.assertLessEqual(snapshot["pending_bytes"], 1024)
        self.assertEqual(snapshot["dropped_full"], 1)
        release.set()
        until(lambda: logger.snapshot()["pending_records"] == 0)
        self.assertEqual(len(lines), 1)
        self.assertEqual(logger.snapshot()["pending_bytes"], 0)

    def test_nested_values_are_immutable_at_emit_time_and_envelope_is_protected(self):
        logger = self.logger()
        entered, release, lines = self.block_writer(logger)
        payload = {"items": [{"value": 1}], "flags": {"a", "b"}, "raw": b"abc"}
        self.assertTrue(logger.emit("control_lock", payload=payload, schema=99, wall_time=-1, monotonic=-1))
        self.assertTrue(entered.wait(1))
        payload["items"][0]["value"] = 2
        payload["flags"].add("c")
        release.set()
        self.assertTrue(logger.flush(1))
        record = json.loads(lines[0])
        self.assertEqual(record["payload"]["items"], [{"value": 1}])
        self.assertEqual(set(record["payload"]["flags"]), {"a", "b"})
        self.assertEqual(record["payload"]["raw"], "YWJj")
        self.assertEqual(record["schema"], 1)
        self.assertGreater(record["wall_time"], 0)
        self.assertGreater(record["monotonic"], 0)

    def test_pathological_snapshots_are_bounded_rejected_and_counted(self):
        logger = self.logger(max_record_bytes=4096)
        cycle = []
        cycle.append(cycle)
        repr_called = []

        class Dangerous:
            def __repr__(self):
                repr_called.append(True)
                raise AssertionError("producer called arbitrary repr")

        class DangerousMapping:
            def __getitem__(self, key):
                repr_called.append(True)
                raise AssertionError("producer called arbitrary mapping lookup")
            def items(self):
                repr_called.append(True)
                raise AssertionError("producer called arbitrary mapping iteration")

        huge = "x" * 1_000_000
        values = [cycle, [0] * 100_000, huge, 1 << 100_000, Dangerous(),
                  {Dangerous(): 1}, MappingProxyType(DangerousMapping())]
        started = time.perf_counter()
        for value in values:
            self.assertFalse(logger.emit("bad_snapshot", value=value))
        self.assertFalse(logger.emit(huge))
        self.assertFalse(logger.emit("wide", **{f"key_{i}": 0 for i in range(5000)}))
        self.assertLess(time.perf_counter() - started, 0.1)
        self.assertEqual(repr_called, [])
        self.assertEqual(logger.snapshot()["dropped_invalid"], 9)
        self.assertFalse(logger.snapshot()["worker_active"])
        self.assertFalse(self.root.exists())

    def test_video_is_excluded_without_snapshotting_and_referee_cap_is_preserved(self):
        logger = self.logger()
        self.assertFalse(logger.emit("video_payload", value=object()))
        self.assertFalse(logger.snapshot()["worker_active"])
        self.assertTrue(logger.referee_payload("referee/raw", b"x" * 70000))
        self.assertTrue(logger.flush(1))
        self.assertTrue(logger.close(1))
        records = list(ReadOnlyFieldReplay([logger.path]).records())
        self.assertEqual(len(records), 1)
        self.assertEqual(len(base64.b64decode(records[0]["payload_b64"])), 65536)
        self.assertEqual(records[0]["payload_bytes"], 70000)
        self.assertTrue(records[0]["truncated"])
        self.assertEqual(logger.snapshot()["ignored_video"], 1)
        self.assertEqual(logger.snapshot()["dropped_records"], 0)

    def test_write_error_is_visible_sanitized_and_does_not_strand_later_work(self):
        logger = self.logger()
        with patch.object(logger, "_write_line", side_effect=[OSError(13, "secret-data", "/private/path"), None]):
            self.assertTrue(logger.emit("estop", index=0))
            self.assertTrue(logger.emit("estop", index=1))
            self.assertFalse(logger.flush(1))
        snapshot = logger.snapshot()
        self.assertEqual(snapshot["pending_records"], 0)
        self.assertEqual(snapshot["written_records"], 1)
        self.assertEqual(snapshot["dropped_write"], 1)
        self.assertEqual(snapshot["write_errors"], 1)
        self.assertEqual(logger.last_error, "PermissionError (errno=13)")
        self.assertNotIn("private", str(snapshot))
        self.assertNotIn("secret", str(snapshot))

    def test_close_timeout_seals_ingress_without_starting_replacement_writer(self):
        logger = self.logger()
        entered, release, lines = self.block_writer(logger)
        self.assertTrue(logger.emit("estop"))
        self.assertTrue(entered.wait(1))
        started = time.perf_counter()
        self.assertFalse(logger.close(0.01))
        self.assertLess(time.perf_counter() - started, 0.1)
        self.assertFalse(logger.emit("after_close"))
        snapshot = logger.snapshot()
        self.assertTrue(snapshot["closing"])
        self.assertFalse(snapshot["closed"])
        self.assertEqual(snapshot["close_timeouts"], 1)
        self.assertEqual(snapshot["dropped_closed"], 1)
        self.assertEqual(snapshot["worker_starts"], 1)
        release.set()
        until(lambda: logger.snapshot()["closed"])
        self.assertEqual(len(lines), 1)
        self.assertEqual(logger.snapshot()["pending_records"], 0)
        self.assertFalse(logger.snapshot()["worker_active"])

    def test_cancel_counts_pending_records_without_pretending_inflight_is_done(self):
        logger = self.logger()
        entered, release, lines = self.block_writer(logger)
        self.assertTrue(logger.emit("estop", index=0))
        self.assertTrue(entered.wait(1))
        self.assertTrue(logger.emit("estop", index=1))
        self.assertTrue(logger.emit("estop", index=2))
        self.assertFalse(logger.close(0, drain=False))
        self.assertFalse(logger.flush(0))
        snapshot = logger.snapshot()
        self.assertEqual(snapshot["pending_records"], 1)
        self.assertEqual(snapshot["dropped_cancelled"], 2)
        release.set()
        until(lambda: logger.snapshot()["closed"])
        self.assertFalse(logger.close(1))
        self.assertEqual(len(lines), 1)
        self.assertEqual(logger.snapshot()["pending_bytes"], 0)

    def test_idle_worker_retires_and_next_emit_restarts_without_reordering(self):
        logger = self.logger(idle_timeout=0.01)
        self.assertTrue(logger.emit("audit", index=0))
        self.assertTrue(logger.flush(1))
        until(lambda: not logger.snapshot()["worker_active"])
        self.assertTrue(logger.emit("audit", index=1))
        self.assertTrue(logger.close(1))
        self.assertEqual(logger.snapshot()["worker_starts"], 2)
        self.assertEqual([r["index"] for r in ReadOnlyFieldReplay([logger.path]).records()], [0, 1])

    def test_thread_start_failure_counts_every_accepted_record_and_can_retry(self):
        logger = self.logger()
        with patch("rm_client.backend.field_log.threading.Thread.start", side_effect=RuntimeError("no thread")):
            self.assertFalse(logger.emit("audit", index=0))
        self.assertEqual(logger.snapshot()["pending_records"], 0)
        self.assertEqual(logger.snapshot()["dropped_write"], 1)
        self.assertTrue(logger.emit("audit", index=1))
        self.assertFalse(logger.flush(1))
        self.assertEqual(logger.snapshot()["written_records"], 1)

    def test_close_during_thread_start_cannot_resurrect_or_write_cancelled_record(self):
        logger = self.logger()
        entered, release = threading.Event(), threading.Event()
        real_start = threading.Thread.start
        results = []

        def delayed_start(thread):
            if thread.name == "polar-field-log":
                entered.set()
                release.wait(2)
            return real_start(thread)

        with patch("rm_client.backend.field_log.threading.Thread.start", new=delayed_start):
            producer = threading.Thread(target=lambda: results.append(logger.emit("audit")))
            producer.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(logger.close(0, drain=False))
                self.assertFalse(logger.emit("late"))
            finally:
                release.set()
                producer.join(1)
        until(lambda: logger.snapshot()["closed"])
        self.assertEqual(logger.snapshot()["dropped_cancelled"], 1)
        self.assertEqual(logger.snapshot()["worker_starts"], 1)
        self.assertEqual(logger.snapshot()["written_records"], 0)
        self.assertFalse(self.root.exists())

    def test_flush_barrier_does_not_wait_for_records_accepted_later(self):
        logger = self.logger()
        entered_first, release_first = threading.Event(), threading.Event()
        entered_second, release_second = threading.Event(), threading.Event()
        barrier_waiting, flush_done = threading.Event(), threading.Event()
        result = []
        real_wait = logger._condition.wait

        def write(line):
            index = json.loads(line)["index"]
            (entered_first if index == 0 else entered_second).set()
            (release_first if index == 0 else release_second).wait(3)

        def wait(timeout=None):
            if threading.current_thread().name == "flush-barrier":
                barrier_waiting.set()
            return real_wait(timeout)

        def flush():
            result.append(logger.flush(2))
            flush_done.set()

        with patch.object(logger, "_write_line", side_effect=write), patch.object(logger._condition, "wait", side_effect=wait):
            self.assertTrue(logger.emit("audit", index=0))
            self.assertTrue(entered_first.wait(1))
            waiter = threading.Thread(target=flush, name="flush-barrier")
            waiter.start()
            try:
                self.assertTrue(barrier_waiting.wait(1))
                self.assertTrue(logger.emit("audit", index=1))
                release_first.set()
                self.assertTrue(entered_second.wait(1))
                self.assertTrue(flush_done.wait(1))
                self.assertEqual(result, [True])
                self.assertEqual(logger.snapshot()["pending_records"], 1)
            finally:
                release_first.set()
                release_second.set()
                waiter.join(1)
                self.assertTrue(logger.close(1))

    def test_concurrent_producers_keep_exact_counters_and_complete_json_records(self):
        logger = self.logger(queue_capacity=1024)
        accepted = []
        threads = [threading.Thread(target=lambda n=n: accepted.extend(
            logger.emit("audit", producer=n, index=i) for i in range(100)
        )) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertTrue(all(accepted))
        self.assertEqual(len(accepted), 800)
        self.assertTrue(logger.close(2))
        snapshot = logger.snapshot()
        self.assertEqual(snapshot["accepted_records"], 800)
        self.assertEqual(snapshot["written_records"], 800)
        self.assertEqual(snapshot["dropped_records"], 0)
        self.assertEqual(snapshot["worker_starts"], 1)
        rows = list(ReadOnlyFieldReplay([logger.path]).records())
        self.assertEqual(len({(row["producer"], row["index"]) for row in rows}), 800)

    def test_short_writes_are_finished_and_zero_progress_is_reported(self):
        logger = self.logger()
        chunks = []

        class ShortFile:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def write(self, data):
                length = min(7, len(data))
                chunks.append(bytes(data[:length]))
                return length

        with patch.object(Path, "open", return_value=ShortFile()):
            self.assertTrue(logger.emit("audit", value=42))
            self.assertTrue(logger.flush(1))
        self.assertEqual(json.loads(b"".join(chunks))["value"], 42)
        with patch.object(Path, "open", return_value=ShortFile()), patch.object(ShortFile, "write", return_value=0):
            self.assertTrue(logger.emit("audit", value=43))
            self.assertFalse(logger.flush(1))
        self.assertEqual(logger.snapshot()["written_records"], 1)
        self.assertEqual(logger.snapshot()["dropped_write"], 1)

    def test_rotated_files_remain_bounded_valid_ndjson_after_explicit_flush(self):
        logger = self.logger(max_bytes=1024, backups=2)
        for index in range(80):
            self.assertTrue(logger.emit("publish_result", index=index, detail="x" * 100))
        self.assertTrue(logger.flush(2))
        self.assertTrue(logger.close(1))
        files = sorted(self.root.glob("*.jsonl*"))
        self.assertEqual(len(files), 3)
        self.assertTrue(all(path.stat().st_size <= 1024 for path in files))
        rows = list(ReadOnlyFieldReplay(files).records())
        self.assertTrue(rows)
        self.assertEqual(max(row["index"] for row in rows), 79)


if __name__ == "__main__":
    unittest.main()
