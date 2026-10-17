"""Fake-only manager integration: hardware waits never become UI frame waits."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.backend.manager import BackendManager
from rm_client.control import KeyboardControlBridge
from rm_client.model import RobotState, build_default_registry
from rm_client import serial_ports


def until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError('worker timeout')
        time.sleep(.002)


class ManagerNonblockingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = BackendManager(workspace=self.temp.name, scenario_key='LabDebug')
        self.state = RobotState()
        self.backend.state = self.state
        with patch.object(serial_ports, 'port_snapshot', return_value=[]):
            self.registry = build_default_registry()
        self.control = KeyboardControlBridge(publisher=None, mock_output=True)
        self.backend.polar.request_start = lambda: None  # no hardware in this fixture

    def test_frame_does_not_wait_for_driver_held_intent_lock(self):
        entered, unblock = threading.Event(), threading.Event()
        def driver():
            with self.backend.polar._intent_lock:
                entered.set()
                unblock.wait(2)
        worker = threading.Thread(target=driver)
        worker.start()
        self.assertTrue(entered.wait(1))
        try:
            start = time.perf_counter()
            for _ in range(1000):
                result = self.backend.tick_polar_link(self.state, self.registry, self.control)
                self.assertEqual(result['polar_status'], 'writer busy')
            self.assertLess(time.perf_counter() - start, .05)
            self.assertEqual(self.backend._polar_ui_busy_skips, 1000)
        finally:
            unblock.set()
            worker.join(1)

    def test_endpoint_settings_wait_for_old_real_stream_neutral_and_close(self):
        self._assert_safe_reconfigure('serial')

    def test_bridge_and_mock_change_neutralizes_old_real_stream(self):
        self._assert_safe_reconfigure('bridge')

    def test_mock_only_change_neutralizes_old_real_stream(self):
        self._assert_safe_reconfigure('mock')

    def _assert_safe_reconfigure(self, kind):
        entered, unblock = threading.Event(), threading.Event()
        frames = []
        class Stream:
            is_open = True
            def write(self, data):
                frames.append(bytes(data))
                return len(data)
            def close(self):
                entered.set()
                unblock.wait(2)
                self.is_open = False
        polar = self.backend.polar
        polar.port = 'COM3'
        polar.mock_writes = False
        polar._serial = Stream()
        polar.connected = True
        module = self.registry.get('RobotLink')
        polar._drive_was_armed = True
        if kind == 'serial':
            module.get_setting('Port').value = 'COM4'
        elif kind == 'bridge':
            module.get_setting('Link Mode').value = 'tcp'
            module.get_setting('Bridge Host').value = 'new.example.invalid'
            module.get_setting('Bridge Port').value = 50571
        module.get_setting('Mock Writes').value = True
        try:
            start = time.perf_counter()
            self.backend.tick_polar_link(self.state, self.registry, self.control)
            self.assertLess(time.perf_counter() - start, .02)
            self.assertTrue(entered.wait(1))
            for _ in range(100):
                self.backend.tick_polar_link(self.state, self.registry, self.control)
            self.assertEqual(polar.port, 'COM3')
            self.assertFalse(polar.mock_writes, 'old real stream must not become mock before neutral flush')
            self.assertTrue(frames)
        finally:
            unblock.set()
            until(lambda: not polar._stopping)
        self.backend.tick_polar_link(self.state, self.registry, self.control)
        if kind == 'serial':
            self.assertEqual(polar.port, 'COM4')
        elif kind == 'bridge':
            self.assertEqual(polar.transport, 'tcp')
            self.assertEqual(polar.host, 'new.example.invalid')
        # A second tick may finish a separate mock-mode transition after the
        # endpoint's cleanup; no old handle can be affected at that point.
        self.backend.tick_polar_link(self.state, self.registry, self.control)
        self.assertTrue(polar.mock_writes)
        self.assertFalse(polar._intent.key_value or polar._intent.vx or polar._intent.vy or polar._intent.wz)

    def test_independent_disable_edges_apply_even_when_polar_writer_busy(self):
        from types import SimpleNamespace
        entered, unblock = threading.Event(), threading.Event()
        releases = []
        dt7 = SimpleNamespace(release=lambda: (releases.append('dt7') or (True, 'ok')))
        cc = SimpleNamespace(release_all=lambda: (releases.append('cc') or (True, 'ok')))
        self.control.dt7_link, self.control.cc_link = dt7, cc
        self.control.dt7_enabled = self.control.cc_enabled = True
        self.backend._dt7_was_enabled = self.backend._cc_was_enabled = True
        self.registry.get('RobotLink').get_setting('Allow Drive').value = False
        def driver():
            with self.backend.polar._intent_lock:
                entered.set()
                unblock.wait(2)
        worker = threading.Thread(target=driver)
        worker.start()
        self.assertTrue(entered.wait(1))
        try:
            result = self.backend.tick_polar_link(self.state, self.registry, self.control)
            self.assertEqual(result['polar_status'], 'writer busy')
            self.assertFalse(self.control.dt7_enabled)
            self.assertFalse(self.control.cc_enabled)
            self.assertEqual(set(releases), {'dt7', 'cc'})
        finally:
            unblock.set()
            worker.join(1)

    def test_ordered_shutdown_waits_for_workers_and_reports_unconfirmed(self):
        from types import SimpleNamespace
        logs, waits = [], []
        self.backend.field_log.close(timeout=1)
        self.backend.field_log = SimpleNamespace(
            emit=lambda event, **data: logs.append((event, data)),
            close=lambda **kwargs: True,
            snapshot=lambda: {"pending_records": 0, "dropped_records": 0,
                "write_errors": 0, "closed": True, "worker_active": False, "closing": False})
        dt7 = SimpleNamespace(asynchronous=True, wire_neutral=False, last_error='fake missing',
            release=lambda: (False, 'fake missing'), request_release=lambda: (False, 'fake missing'),
            close=lambda: None,
            wait_closed=lambda timeout: (waits.append(timeout) or False))
        cc = SimpleNamespace(asynchronous=True, wire_neutral=False, last_error='fake missing',
            release_all=lambda: (False, 'fake missing'), close=lambda: None,
            wait_closed=lambda timeout: (waits.append(timeout) or False))
        self.backend._dt7_link, self.backend._cc_link = dt7, cc
        self.backend.shutdown()
        self.assertEqual(len(waits), 2)
        self.assertTrue(all(0 <= value <= 2.5 for value in waits))
        events = [data for event, data in logs if event == 'device_shutdown_unconfirmed']
        self.assertEqual({item['device'] for item in events}, {'DT7', 'CC'})
        self.assertTrue(all(not item['closed'] for item in events))
        self.assertFalse(self.backend.shutdown_completion.resources_released)
        self.assertFalse(self.backend.shutdown_completion.may_exit)
        self.assertTrue(self.backend.shutdown_completion.audit.drained)

    def test_closed_neutral_writer_is_recreated_after_scenario_shutdown(self):
        module = self.registry.get('RobotLink')
        module.get_setting('Direct DT7 Egress').value = True
        module.get_setting('DT7 Real TX').value = True
        self.backend._configure_device_egress(self.registry, self.control)
        old = self.backend._dt7_link
        self.assertTrue(old.asynchronous)
        old.close()
        self.assertTrue(old.wait_closed())
        self.backend._configure_device_egress(self.registry, self.control)
        new = self.backend._dt7_link
        try:
            self.assertIsNot(old, new)
            self.assertFalse(new.closed)
        finally:
            new.close()
            self.assertTrue(new.wait_closed())


if __name__ == '__main__':
    unittest.main()
