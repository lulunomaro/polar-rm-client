"""Angle RX may reuse a writer port only after neutral and real owner teardown."""
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.cc_link import AsyncCustomControllerLink
from rm_client.backend.controller_angles import ControllerAngleRelay
from rm_client.backend.dt7_link import DEFAULT_PROFILE, Dt7KeyMouseLink
from rm_client.backend.dt7_worker import AsyncDt7KeyMouseLink
from rm_client.backend.manager import BackendManager
from rm_client.backend.safety import OfficialSafety
from rm_client.model import RobotState


def until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("fake writer did not reach expected state")
        time.sleep(.001)


class Handle:
    def __init__(self, close_mode="ok"):
        self.close_mode = close_mode
        self.is_open = True
        self.fail_writes = False
        self.frames = []
        self.close_threads = []
        self.entered, self.unblock = threading.Event(), threading.Event()
        self.unblock.set()

    def write(self, data):
        self.frames.append(bytes(data))
        if self.fail_writes:
            raise OSError("fake neutral write failed")
        return len(data)

    def close(self):
        self.close_threads.append(threading.get_ident())
        self.entered.set()
        if not self.unblock.wait(2):
            raise TimeoutError("fake close not unblocked")
        if self.close_mode == "raise":
            raise OSError("fake close failure")
        if self.close_mode == "false":
            return False
        if self.close_mode == "still_open":
            return None
        self.is_open = False


class AngleWriterHandoffTests(unittest.TestCase):
    def writer_fixture(self, kind, *, close_mode="ok"):
        handle = Handle(close_mode)
        safety = OfficialSafety()
        safety.allow_dt7_drive = True
        if kind == "dt7":
            inner = Dt7KeyMouseLink(port="FAKE", profile=dict(DEFAULT_PROFILE), mock_output=False,
                _handle=handle, resolved_port="FAKE", _handle_requested_port="FAKE")
            writer = AsyncDt7KeyMouseLink(port="FAKE", profile=dict(DEFAULT_PROFILE), mock_output=False,
                link=inner, safety=safety, release_attempts=1, max_intent_age_s=60)
            send = lambda: writer.send(pressed_bits=1)
        else:
            writer = AsyncCustomControllerLink(port="FAKE", mock_output=False,
                                               release_attempts=1, max_intent_age=60)
            with writer._condition:
                writer._serial = handle
                writer.resolved_port = "FAKE"
            send = lambda: writer.send_keymouse(pressed=["W"])

        def cleanup():
            handle.fail_writes = False
            handle.close_mode = "ok"
            handle.unblock.set()
            writer.close()
            self.assertTrue(writer.wait_closed(2))

        self.addCleanup(cleanup)
        self.assertTrue(send()[0])
        until(lambda: writer.sent == 1)
        self.assertFalse(writer.wire_neutral)
        return writer, handle

    def manager_fixture(self, kind, writer):
        manager = object.__new__(BackendManager)
        manager._backend_transition_blocks_work = lambda: False
        manager.state = RobotState()
        manager.safety = SimpleNamespace(estop_active=False)
        manager.controller_angles = ControllerAngleRelay(start_workers=False)
        self.addCleanup(manager.controller_angles.close)
        setattr(manager, "_" + kind + "_link", writer)
        angle_values = {"Angle RX Port": "FAKE", "Angle TX Port": "Stub",
                        "Forward Angles": False, "Angle Mock Writes": True}
        robot_values = {"Link Mode": "tcp", "Allow Drive": True,
                        "Direct DT7 Egress": False, "Custom Controller Egress": False}
        def module(values):
            settings = {key: SimpleNamespace(value=value) for key, value in values.items()}
            return SimpleNamespace(enabled=True, get_setting=settings.get)
        modules = {"ControllerAngles": module(angle_values), "RobotLink": module(robot_values)}
        return manager, SimpleNamespace(get=modules.get)

    def sync(self, manager, registry):
        status = manager._sync_controller_angles(registry)
        # Reusing a released RX port never implicitly arms angle forwarding.
        self.assertFalse(status["armed"])
        return status

    def owned_handle(self, kind, writer):
        return writer._link._handle if kind == "dt7" else writer._serial

    def test_confirmed_neutral_driver_close_and_worker_exit_release_reservation(self):
        for kind in ("dt7", "cc"):
            with self.subTest(writer=kind):
                writer, handle = self.writer_fixture(kind)
                manager, registry = self.manager_fixture(kind, writer)
                self.assertFalse(self.sync(manager, registry)["enabled"])
                writer.close()
                self.assertTrue(writer.wait_closed(1))
                self.assertTrue(writer.wire_neutral)
                self.assertFalse(writer.release_pending)
                self.assertFalse(writer.worker_alive)
                self.assertFalse(handle.is_open)
                self.assertIsNone(self.owned_handle(kind, writer))
                self.assertTrue(self.sync(manager, registry)["enabled"])
                self.assertIs(getattr(manager, "_" + kind + "_link"), writer)
                self.assertEqual(len(handle.frames), 2)
                self.assertTrue(all(owner != threading.get_ident() for owner in handle.close_threads))

    def test_blocked_driver_close_keeps_port_until_worker_finishes(self):
        for kind in ("dt7", "cc"):
            with self.subTest(writer=kind):
                writer, handle = self.writer_fixture(kind)
                manager, registry = self.manager_fixture(kind, writer)
                handle.unblock.clear()
                writer.close()
                self.assertTrue(handle.entered.wait(1))
                self.assertTrue(writer.wire_neutral)
                self.assertTrue(writer.worker_alive)
                self.assertFalse(self.sync(manager, registry)["enabled"])
                handle.unblock.set()
                self.assertTrue(writer.wait_closed(1))
                self.assertTrue(self.sync(manager, registry)["enabled"])

    def test_failed_close_modes_keep_owner_until_explicit_successful_retry(self):
        for kind in ("dt7", "cc"):
            for mode in ("raise", "false", "still_open"):
                with self.subTest(writer=kind, close_mode=mode):
                    writer, handle = self.writer_fixture(kind, close_mode=mode)
                    manager, registry = self.manager_fixture(kind, writer)
                    writer.close()
                    self.assertFalse(writer.wait_closed(1))
                    self.assertTrue(handle.is_open)
                    self.assertIs(self.owned_handle(kind, writer), handle)
                    for _ in range(10):
                        self.assertFalse(self.sync(manager, registry)["enabled"])
                    self.assertEqual(len(handle.close_threads), 1)
                    handle.close_mode = "ok"
                    writer.close()
                    self.assertTrue(writer.wait_closed(1))
                    self.assertTrue(self.sync(manager, registry)["enabled"])

    def test_successful_driver_close_with_unconfirmed_neutral_stays_blocked(self):
        for kind in ("dt7", "cc"):
            with self.subTest(writer=kind):
                writer, handle = self.writer_fixture(kind)
                manager, registry = self.manager_fixture(kind, writer)
                handle.fail_writes = True
                writer.close()
                self.assertTrue(writer.wait_closed(1))
                self.assertFalse(handle.is_open)
                self.assertFalse(writer.wire_neutral)
                self.assertTrue(writer.release_pending)
                self.assertFalse(self.sync(manager, registry)["enabled"])
                self.assertEqual(len(handle.frames), 2)

    def test_plain_closed_flag_is_not_a_resource_release_proof(self):
        for kind in ("dt7", "cc"):
            for claimed in (False, "true"):
                with self.subTest(writer=kind, claimed=claimed):
                    legacy = SimpleNamespace(mock_output=False, port="FAKE", resolved_port="FAKE",
                        closed=True, worker_alive=False, wire_neutral=True, port_handoff_ready=claimed)
                    manager, registry = self.manager_fixture(kind, legacy)
                    self.assertFalse(self.sync(manager, registry)["enabled"])

    def test_completed_close_and_empty_handle_still_wait_for_worker_exit(self):
        for kind in ("dt7", "cc"):
            with self.subTest(writer=kind):
                writer, handle = self.writer_fixture(kind)
                manager, registry = self.manager_fixture(kind, writer)
                tail_entered, tail_unblock = threading.Event(), threading.Event()
                self.addCleanup(tail_unblock.set)
                original_finalize = writer._finalize_close

                def finalize():
                    original_finalize()
                    tail_entered.set()
                    if not tail_unblock.wait(2):
                        raise TimeoutError("fake worker tail not unblocked")

                writer._finalize_close = finalize
                writer.close()
                self.assertTrue(tail_entered.wait(1))
                self.assertTrue(writer.closed)
                self.assertFalse(handle.is_open)
                self.assertIsNone(self.owned_handle(kind, writer))
                self.assertTrue(writer.wire_neutral)
                self.assertTrue(writer.worker_alive)
                self.assertFalse(self.sync(manager, registry)["enabled"])
                tail_unblock.set()
                self.assertTrue(writer.wait_closed(1))
                self.assertTrue(self.sync(manager, registry)["enabled"])

    def test_even_completed_flag_cannot_hide_a_retained_driver_handle(self):
        for kind in ("dt7", "cc"):
            with self.subTest(writer=kind):
                writer, handle = self.writer_fixture(kind, close_mode="raise")
                manager, registry = self.manager_fixture(kind, writer)
                writer.close()
                self.assertFalse(writer.wait_closed(1))
                self.assertFalse(writer.worker_alive)
                self.assertTrue(writer.wire_neutral)
                # Fault-inject an inconsistent completion flag; the owner still
                # retains the real fake handle, so a handoff must remain denied.
                if kind == "dt7":
                    writer._closed.set()
                else:
                    writer._stopped = True
                try:
                    self.assertTrue(writer.closed)
                    self.assertIs(self.owned_handle(kind, writer), handle)
                    self.assertFalse(self.sync(manager, registry)["enabled"])
                finally:
                    if kind == "dt7":
                        writer._closed.clear()
                    else:
                        writer._stopped = False


if __name__ == "__main__":
    unittest.main()
