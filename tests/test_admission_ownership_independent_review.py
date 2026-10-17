"""Independent admission/close races preserve native ownership and audit loss."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.backend.field_log import RotatingJsonlLogger
from rm_client.backend.controller_angles import ControllerAngleRelay, AngleRelayConfig
from rm_client.backend.lifecycle import AuditCloseStatus, close_controller_angles


class IndependentAdmissionReview(unittest.TestCase):
    def logger(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        logger = RotatingJsonlLogger(Path(directory.name), idle_timeout=0.01)
        self.addCleanup(logger.close, 2)
        return logger

    def test_concurrent_producers_and_close_during_unadmitted_rejection(self):
        for drain in (True, False):
            with self.subTest(drain=drain):
                logger = self.logger()
                start_entered, reject = threading.Event(), threading.Event()
                result = []
                errors = []
                original_start = threading.Thread.start
                error = RuntimeError('independent admission rejection')
                def start(thread):
                    if thread.name != 'polar-field-log':
                        return original_start(thread)
                    start_entered.set()
                    if not reject.wait(2):
                        raise AssertionError('review did not release rejection')
                    raise error
                def producer():
                    try:
                        result.append(logger.emit('first'))
                    except BaseException as exc:
                        errors.append(exc)
                with patch.object(threading.Thread, 'start', start):
                    producer_thread = threading.Thread(target=producer)
                    producer_thread.start()
                    try:
                        self.assertTrue(start_entered.wait(1))
                        self.assertTrue(logger.emit('second'))
                        self.assertTrue(logger.emit('third'))
                        self.assertFalse(logger.flush(0))
                        self.assertFalse(logger.close(0, drain=drain))
                        self.assertFalse(AuditCloseStatus.capture(logger).drained)
                    finally:
                        reject.set()
                        producer_thread.join(2)
                self.assertFalse(producer_thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(result, [False])
                state = logger.snapshot()
                self.assertEqual(state['accepted_records'], 3)
                self.assertEqual(state['written_records'], 0)
                self.assertEqual(state['pending_records'], 0)
                self.assertEqual(state['pending_bytes'], 0)
                self.assertEqual(state['dropped_write'], 3 if drain else 0)
                self.assertEqual(state['dropped_cancelled'], 0 if drain else 3)
                self.assertEqual(state['dropped_records'], 3)
                self.assertEqual(state['worker_starts'], 0)
                self.assertEqual(state['write_errors'], 1)
                self.assertTrue(state['closed'])
                self.assertFalse(state['worker_active'])
                self.assertFalse(logger.close(0))
                self.assertTrue(AuditCloseStatus.capture(logger).drained)
                self.assertFalse(AuditCloseStatus.capture(logger).intact)

    def test_admitted_late_error_after_cancel_keeps_inflight_and_counts(self):
        logger = self.logger()
        in_write, release = threading.Event(), threading.Event()
        lines = []
        original_start = threading.Thread.start
        def write(line):
            in_write.set()
            if not release.wait(2):
                raise AssertionError('review did not release write')
            lines.append(line)
        def start(thread):
            original_start(thread)
            self.assertTrue(in_write.wait(1))
            self.assertTrue(logger.emit('cancel-me'))
            self.assertFalse(logger.close(0, drain=False))
            raise RuntimeError('late failure after cancellation')
        with patch.object(logger, '_write_line', write):
            try:
                with patch.object(threading.Thread, 'start', start):
                    self.assertFalse(logger.emit('complete-me'))
                state = logger.snapshot()
                self.assertTrue(state['worker_active'])
                self.assertEqual(state['pending_records'], 1)
                self.assertGreater(state['pending_bytes'], 0)
                self.assertEqual(state['dropped_cancelled'], 1)
                self.assertEqual(state['dropped_write'], 0)
                self.assertFalse(AuditCloseStatus.capture(logger).drained)
            finally:
                release.set()
                self.assertFalse(logger.close(2))
        state = logger.snapshot()
        self.assertEqual(len(lines), 1)
        self.assertEqual(state['written_records'], 1)
        self.assertEqual(state['pending_records'], 0)
        self.assertEqual(state['pending_bytes'], 0)
        self.assertEqual(state['dropped_records'], 1)
        self.assertEqual(state['write_errors'], 1)
        self.assertTrue(AuditCloseStatus.capture(logger).drained)
        self.assertFalse(AuditCloseStatus.capture(logger).intact)

    def test_repeated_constructor_rejection_recovery_preserves_loss_and_redacts(self):
        logger = self.logger()
        error = RuntimeError('private payload and /private/path')
        error.errno = 23
        with patch('rm_client.backend.field_log.threading.Thread', side_effect=error):
            self.assertFalse(logger.emit('first'))
            self.assertFalse(logger.emit('second'))
        self.assertTrue(logger.emit('recovery'))
        self.assertFalse(logger.close(2))
        state = logger.snapshot()
        self.assertEqual(state['accepted_records'], 3)
        self.assertEqual(state['written_records'], 1)
        self.assertEqual(state['dropped_write'], 2)
        self.assertEqual(state['write_errors'], 2)
        self.assertEqual(state['last_error'], 'RuntimeError (errno=23)')
        self.assertEqual(state['pending_records'], 0)
        self.assertEqual(state['pending_bytes'], 0)
        self.assertFalse(logger.flush(0))

    def test_angle_rejection_allows_same_configuration_retry_without_phantom_owner(self):
        opened = threading.Event()
        class Serial:
            in_waiting = 0
            is_open = True
            closed = 0
            def reset_input_buffer(self): pass
            def read(self, n): return b''
            def close(self):
                self.closed += 1
                self.is_open = False
        serial = Serial()
        def factory(**options):
            opened.set()
            return serial
        relay = ControllerAngleRelay(serial_factory=factory)
        config = AngleRelayConfig(enabled=True, rx_port='REVIEW-ANGLE')
        error = RuntimeError('reject one attempt')
        with patch.object(threading.Thread, 'start', side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                relay.configure(config, False)
        self.assertIs(raised.exception, error)
        self.assertEqual(relay.owned_ports, ())
        self.assertIsNone(relay._thread)
        self.assertTrue(relay._stop.is_set())
        try:
            relay.configure(config, False)
            self.assertTrue(opened.wait(1))
            self.assertEqual(relay.owned_ports, ('REVIEW-ANGLE',))
        finally:
            relay.close()
            close_controller_angles(relay)
        self.assertEqual(serial.closed, 1)
        self.assertEqual(relay.owned_ports, ())

    def test_angle_late_start_error_keeps_old_config_until_native_close(self):
        opened, release_open = threading.Event(), threading.Event()
        opened_ports = []
        class Serial:
            in_waiting = 0
            is_open = True
            closed = 0
            def reset_input_buffer(self): pass
            def read(self, n): return b''
            def close(self):
                self.closed += 1
                self.is_open = False
        serial = Serial()
        def factory(**options):
            opened_ports.append(options['port'])
            opened.set()
            if not release_open.wait(2):
                raise AssertionError('review did not release open')
            return serial
        relay = ControllerAngleRelay(serial_factory=factory)
        old_config = AngleRelayConfig(enabled=True, rx_port='REVIEW-OLD')
        next_config = AngleRelayConfig(enabled=True, rx_port='REVIEW-NEXT')
        original_start = threading.Thread.start
        error = RuntimeError('real admission then late error')
        def start(thread):
            original_start(thread)
            self.assertTrue(opened.wait(1))
            raise error
        try:
            with patch.object(threading.Thread, 'start', start):
                with self.assertRaises(RuntimeError) as raised:
                    relay.configure(old_config, False)
            self.assertIs(raised.exception, error)
            owner = relay._thread
            relay.configure(next_config, False)
            self.assertIs(relay._thread, owner)
            self.assertEqual(relay.owned_ports, ('REVIEW-OLD',))
            self.assertEqual(opened_ports, ['REVIEW-OLD'])
            self.assertFalse(relay.wait_closed(0))
        finally:
            release_open.set()
            relay.close()
            self.assertTrue(relay.wait_closed(2))
        self.assertEqual(serial.closed, 1)
        self.assertEqual(relay.owned_ports, ())


if __name__ == '__main__':
    unittest.main(verbosity=2)
