"""Real-worker admission failures preserve audit ownership and loss accounting."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend.field_log import RotatingJsonlLogger
from rm_client.backend.lifecycle import AuditCloseStatus


class FieldLogAdmissionTests(unittest.TestCase):
    def logger(self, **options):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        logger = RotatingJsonlLogger(Path(temp.name), **options)
        self.addCleanup(logger.close, 2.0)
        return logger

    def test_constructor_rejection_accounts_queued_record_and_cannot_report_success(self):
        logger = self.logger()
        with patch("rm_client.backend.field_log.threading.Thread",
                   side_effect=RuntimeError("injected constructor rejection")):
            self.assertFalse(logger.emit("admission"))
        snapshot = logger.snapshot()
        self.assertEqual(snapshot["accepted_records"], 1)
        self.assertEqual(snapshot["pending_records"], 0)
        self.assertEqual(snapshot["pending_bytes"], 0)
        self.assertEqual(snapshot["written_records"], 0)
        self.assertEqual(snapshot["dropped_write"], 1)
        self.assertEqual(snapshot["write_errors"], 1)
        self.assertIn("RuntimeError", snapshot["last_error"])
        self.assertFalse(logger.close(0))
        self.assertEqual(AuditCloseStatus.capture(logger), AuditCloseStatus(True, False, 0, 1, 1))

    def test_rejected_start_accounts_all_queued_records(self):
        logger = self.logger()
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("injected start rejection")):
            self.assertFalse(logger.emit("admission"))
        self.assertFalse(logger.close(0))
        self.assertEqual(AuditCloseStatus.capture(logger), AuditCloseStatus(True, False, 0, 1, 1))
        self.assertFalse(logger.snapshot()["worker_active"])

    def test_admitted_writer_survives_late_start_error_and_prevents_false_drain(self):
        logger = self.logger()
        entered, release = threading.Event(), threading.Event()
        lines = []

        def write(line):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test writer was not released")
            lines.append(line)

        original_start = threading.Thread.start

        def start_then_raise(thread):
            original_start(thread)
            self.assertTrue(entered.wait(1))
            raise RuntimeError("injected error after real admission")

        with patch.object(logger, "_write_line", side_effect=write):
            try:
                with patch.object(threading.Thread, "start", start_then_raise):
                    self.assertFalse(logger.emit("first"))
                snapshot = logger.snapshot()
                self.assertTrue(snapshot["worker_active"])
                self.assertEqual(snapshot["pending_records"], 1)
                self.assertGreater(snapshot["pending_bytes"], 0)
                self.assertEqual(snapshot["dropped_write"], 0)
                self.assertTrue(logger.emit("second"))
                self.assertEqual(logger.snapshot()["worker_starts"], 1)
                self.assertFalse(logger.close(0))
                self.assertFalse(AuditCloseStatus.capture(logger).drained)
            finally:
                release.set()
                logger.close(2)
        snapshot = logger.snapshot()
        self.assertEqual(len(lines), 2)
        self.assertEqual(snapshot["written_records"], 2)
        self.assertEqual(snapshot["pending_records"], 0)
        self.assertEqual(snapshot["pending_bytes"], 0)
        self.assertEqual(snapshot["write_errors"], 1)
        self.assertEqual(snapshot["dropped_records"], 0)
        self.assertTrue(AuditCloseStatus.capture(logger).drained)
        self.assertFalse(AuditCloseStatus.capture(logger).intact)

    def test_finished_writer_after_late_error_keeps_completed_accounting(self):
        logger = self.logger(idle_timeout=0.01)
        lines = []
        original_start = threading.Thread.start

        def finish_then_raise(thread):
            original_start(thread)
            thread.join(1)
            self.assertFalse(thread.is_alive())
            raise RuntimeError("injected error after real worker finished")

        with patch.object(logger, "_write_line", side_effect=lines.append):
            with patch.object(threading.Thread, "start", finish_then_raise):
                self.assertFalse(logger.emit("first"))
            snapshot = logger.snapshot()
            self.assertEqual(snapshot["written_records"], 1)
            self.assertEqual(snapshot["pending_records"], 0)
            self.assertEqual(snapshot["dropped_records"], 0)
            self.assertTrue(logger.emit("second"))
            self.assertFalse(logger.close(2))
        self.assertEqual(len(lines), 2)
        self.assertEqual(logger.snapshot()["written_records"], 2)
        self.assertTrue(AuditCloseStatus.capture(logger).drained)

    def test_late_error_cannot_discard_a_successor_writer(self):
        logger = self.logger(idle_timeout=0.01)
        entered, release = threading.Event(), threading.Event()
        lines = []
        original_start = threading.Thread.start
        first = True

        def write(line):
            if b'"second"' in line:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test successor writer was not released")
            lines.append(line)

        def start_successor_then_raise(thread):
            nonlocal first
            original_start(thread)
            if not first:
                return
            first = False
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertTrue(logger.emit("second"))
            self.assertTrue(entered.wait(1))
            raise RuntimeError("injected error after successor admission")

        with patch.object(logger, "_write_line", side_effect=write):
            try:
                with patch.object(threading.Thread, "start", start_successor_then_raise):
                    self.assertFalse(logger.emit("first"))
                snapshot = logger.snapshot()
                self.assertTrue(snapshot["worker_active"])
                self.assertEqual(snapshot["pending_records"], 1)
                self.assertEqual(snapshot["worker_starts"], 2)
                self.assertFalse(logger.close(0))
                self.assertFalse(AuditCloseStatus.capture(logger).drained)
            finally:
                release.set()
                logger.close(2)
        self.assertEqual(len(lines), 2)
        self.assertEqual(logger.snapshot()["pending_records"], 0)
        self.assertEqual(logger.snapshot()["pending_bytes"], 0)
        self.assertTrue(AuditCloseStatus.capture(logger).drained)


if __name__ == "__main__":
    unittest.main()
