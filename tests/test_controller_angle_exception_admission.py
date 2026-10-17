"""Non-RuntimeError admission failures preserve exact worker ownership."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend.controller_angles import AngleRelayConfig, ControllerAngleRelay
from rm_client.backend.lifecycle import close_controller_angles


class ControllerAngleExceptionAdmissionTests(unittest.TestCase):
    def test_rejected_start_releases_unstarted_owner_for_all_exceptions(self):
        for error_type in (OSError, ValueError):
            with self.subTest(error_type=error_type):
                relay = ControllerAngleRelay(
                    serial_factory=lambda **_: self.fail("must not open hardware"))
                error = error_type("injected admission rejection")
                with patch.object(threading.Thread, "start", side_effect=error):
                    with self.assertRaises(error_type) as raised:
                        relay.configure(AngleRelayConfig(enabled=True, rx_port="FAKE-ANGLE"), False)
                self.assertIs(raised.exception, error)
                relay.close()
                close_controller_angles(relay)
                close_controller_angles(relay)
                self.assertIsNone(relay._thread)
                self.assertEqual(relay.owned_ports, ())
                self.assertFalse(relay.armed)

    def test_late_oserror_retains_admitted_owner_until_exit(self):
        entered, release = threading.Event(), threading.Event()
        relay = ControllerAngleRelay(
            serial_factory=lambda **_: self.fail("must not open hardware"))
        real_start = threading.Thread.start
        error = OSError("injected late admission error")

        def inert_worker(*_):
            entered.set()
            release.wait(2)

        def start_then_raise(worker):
            real_start(worker)
            self.assertTrue(entered.wait(1))
            raise error

        try:
            with patch.object(relay, "_run", inert_worker), patch.object(
                    threading.Thread, "start", start_then_raise):
                with self.assertRaises(OSError) as raised:
                    relay.configure(AngleRelayConfig(enabled=True, rx_port="FAKE-ANGLE"), False)
            self.assertIs(raised.exception, error)
            relay.close()
            self.assertFalse(relay.wait_closed(0))
            self.assertIsNotNone(relay._thread.ident)
            self.assertEqual(relay.owned_ports, ("FAKE-ANGLE",))
        finally:
            release.set()
            relay.close()
            self.assertTrue(relay.wait_closed(2))
        self.assertEqual(relay.owned_ports, ())

    def test_rejected_outer_start_preserves_reentrant_successor_owner(self):
        entered, release = threading.Event(), threading.Event()
        relay = ControllerAngleRelay(
            serial_factory=lambda **_: self.fail("must not open hardware"))
        real_start = threading.Thread.start
        outer_worker = []
        successor = []
        error = OSError("outer admission rejected after successor")

        def inert_worker(*_):
            entered.set()
            release.wait(2)

        def reentrant_start(worker):
            if not outer_worker:
                outer_worker.append(worker)
                relay.configure(AngleRelayConfig(enabled=True, rx_port="FAKE-SUCCESSOR"), False)
                successor.append(relay._thread)
                raise error
            real_start(worker)
            self.assertTrue(entered.wait(1))

        try:
            with patch.object(relay, "_run", inert_worker), patch.object(
                    threading.Thread, "start", reentrant_start):
                with self.assertRaises(OSError) as raised:
                    relay.configure(AngleRelayConfig(enabled=True, rx_port="FAKE-OUTER"), False)
            self.assertIs(raised.exception, error)
            self.assertIs(relay._thread, successor[0])
            self.assertIsNotNone(successor[0].ident)
            self.assertEqual(relay.owned_ports, ("FAKE-SUCCESSOR",))
            relay.close()
            self.assertFalse(relay.wait_closed(0))
        finally:
            release.set()
            for worker in successor:
                worker.join(2)
            relay.close()
            self.assertTrue(relay.wait_closed(2))
        self.assertEqual(relay.owned_ports, ())


if __name__ == "__main__":
    unittest.main()
