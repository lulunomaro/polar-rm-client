"""Angle receiver admission cannot poison strict close or lose a real owner."""
from __future__ import annotations

from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend.controller_angles import AngleRelayConfig, ControllerAngleRelay
from rm_client.backend.lifecycle import close_controller_angles


class FakeSerial:
    def __init__(self):
        self.is_open = True
        self.in_waiting = 0
        self.close_count = 0

    def reset_input_buffer(self):
        pass

    def read(self, _size):
        return b""

    def close(self):
        self.close_count += 1
        self.is_open = False


class ControllerAngleAdmissionTests(unittest.TestCase):
    def test_rejected_start_releases_phantom_reservation_and_strict_close_can_finish(self):
        relay = ControllerAngleRelay(serial_factory=lambda **_: self.fail("must not open hardware"))
        config = AngleRelayConfig(enabled=True, rx_port="FAKE-ANGLE")
        error = RuntimeError("injected start rejection")
        with patch.object(threading.Thread, "start", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                relay.configure(config, False)
        self.assertIs(raised.exception, error)
        self.assertFalse(relay.armed)
        self.assertEqual(relay.owned_ports, ())
        relay.close()
        close_controller_angles(relay)
        close_controller_angles(relay)
        self.assertEqual(relay.owned_ports, ())

    def test_rejected_constructor_releases_phantom_reservation(self):
        relay = ControllerAngleRelay(serial_factory=lambda **_: self.fail("must not open hardware"))
        error = RuntimeError("injected constructor rejection")
        with patch("rm_client.backend.controller_angles.threading.Thread", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                relay.configure(AngleRelayConfig(enabled=True, rx_port="FAKE-ANGLE"), False)
        self.assertIs(raised.exception, error)
        self.assertEqual(relay.owned_ports, ())
        relay.close()
        close_controller_angles(relay)

    def test_late_start_error_retains_running_owner_until_native_close(self):
        opened, release = threading.Event(), threading.Event()
        serial = FakeSerial()

        def factory(**_):
            opened.set()
            if not release.wait(2):
                raise TimeoutError("test did not release fake open")
            return serial

        relay = ControllerAngleRelay(serial_factory=factory)
        original_start = threading.Thread.start
        error = RuntimeError("injected late start error")

        def start_then_raise(thread):
            original_start(thread)
            self.assertTrue(opened.wait(1))
            raise error

        try:
            with patch.object(threading.Thread, "start", start_then_raise):
                with self.assertRaises(RuntimeError) as raised:
                    relay.configure(AngleRelayConfig(enabled=True, rx_port="FAKE-ANGLE"), False)
            self.assertIs(raised.exception, error)
            self.assertEqual(relay.owned_ports, ("FAKE-ANGLE",))
            relay.close()
            self.assertFalse(relay.wait_closed(0))
            self.assertEqual(relay.owned_ports, ("FAKE-ANGLE",))
            self.assertTrue(serial.is_open)
        finally:
            release.set()
            relay.close()
            self.assertTrue(relay.wait_closed(2))
        self.assertFalse(serial.is_open)
        self.assertEqual(serial.close_count, 1)
        self.assertEqual(relay.owned_ports, ())


if __name__ == "__main__":
    unittest.main()
