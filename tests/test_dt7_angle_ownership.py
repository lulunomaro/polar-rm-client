"""Retained angle serial ownership must block real DT7 replacement and motion."""
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.controller_angles import AngleRelayConfig, ControllerAngleRelay
from rm_client.backend.dt7_link import DEFAULT_PROFILE, Dt7KeyMouseLink, decode_dt7_frame
from rm_client.backend.dt7_worker import AsyncDt7KeyMouseLink
from rm_client.backend.manager import BackendManager
from rm_client.backend.safety import OfficialSafety
from rm_client.control import KeyboardControlBridge


def until(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("fake worker did not reach expected state")
        time.sleep(0.001)


class Dt7AngleOwnershipTests(unittest.TestCase):
    def relay_fixture(self, *, fail_close=False):
        entered, unblock = threading.Event(), threading.Event()
        failure = [fail_close]

        class Stream:
            is_open = True
            in_waiting = 0

            def reset_input_buffer(self):
                pass

            def read(self, size):
                return b""

            def close(self):
                entered.set()
                if not unblock.wait(2):
                    raise TimeoutError("fake angle close was not released")
                if failure[0]:
                    raise OSError("fake angle close failed")
                self.is_open = False

        stream = Stream()
        relay = ControllerAngleRelay(serial_factory=lambda **kwargs: stream)

        def cleanup():
            failure[0] = False
            unblock.set()
            relay.close()
            self.assertTrue(relay.wait_closed(2))

        self.addCleanup(cleanup)
        relay.configure(AngleRelayConfig(enabled=True, rx_port="CONFLICT"), authorized=True)
        until(lambda: relay.connected)
        relay.close()
        self.assertTrue(entered.wait(1))
        self.assertEqual(relay.owned_ports, ("CONFLICT",))
        return relay, stream, failure, unblock

    def manager_fixture(self, relay, *, port="CONFLICT", real_tx=True):
        manager = object.__new__(BackendManager)
        manager.controller_angles = relay
        manager.safety = OfficialSafety()
        manager.safety.allow_dt7_drive = True
        manager.polar_bridge = SimpleNamespace(active=False)
        manager._dt7_was_enabled = manager._cc_was_enabled = False
        control = KeyboardControlBridge(publisher=None, mock_output=True)
        values = {name: SimpleNamespace(value=value) for name, value in {
            "Direct DT7 Egress": True, "DT7 Real TX": real_tx, "DT7 Port": port,
            "Custom Controller Egress": False, "Allow Drive": True,
        }.items()}
        module = SimpleNamespace(enabled=True, get_setting=values.get)
        registry = SimpleNamespace(get=lambda name: module)
        return manager, control, registry

    def inert_factory(self, **kwargs):
        return SimpleNamespace(**kwargs, wire_neutral=True, closed=False)

    def configure(self, manager, control, registry):
        with patch("rm_client.backend.manager.dt7_link.load_profile", return_value=dict(DEFAULT_PROFILE)):
            return manager._configure_device_egress(registry, control)

    def test_blocked_angle_close_prevents_creation_until_owner_releases(self):
        relay, stream, _, unblock = self.relay_fixture()
        manager, control, registry = self.manager_fixture(relay, port=" conflict ")
        with patch("rm_client.backend.manager.AsyncDt7KeyMouseLink", side_effect=self.inert_factory) as factory:
            self.configure(manager, control, registry)
            self.assertFalse(control.dt7_enabled)
            factory.assert_not_called()
            self.assertTrue(stream.is_open)
            unblock.set()
            self.assertTrue(relay.wait_closed(1))
            self.assertEqual(relay.owned_ports, ())
            self.configure(manager, control, registry)
            factory.assert_called_once()
            self.assertTrue(control.dt7_enabled)

    def test_owned_angle_port_does_not_block_mock_or_disjoint_explicit_dt7(self):
        relay, _, _, _ = self.relay_fixture()
        for port, real_tx in (("CONFLICT", False), ("DISJOINT", True)):
            with self.subTest(port=port, real_tx=real_tx):
                manager, control, registry = self.manager_fixture(relay, port=port, real_tx=real_tx)
                factory_name = ("AsyncDt7KeyMouseLink" if real_tx else "dt7_link.Dt7KeyMouseLink")
                with patch("rm_client.backend.manager." + factory_name,
                           side_effect=self.inert_factory) as factory:
                    self.configure(manager, control, registry)
                    factory.assert_called_once()
                    self.assertTrue(control.dt7_enabled)

    def test_failed_angle_close_retains_reservation_until_explicit_retry_confirms(self):
        relay, stream, failure, unblock = self.relay_fixture(fail_close=True)
        unblock.set()
        relay._thread.join(1)
        self.assertFalse(relay._thread.is_alive())
        self.assertEqual(relay.owned_ports, ("CONFLICT",))
        self.assertTrue(stream.is_open)
        manager, control, registry = self.manager_fixture(relay, port="Auto")
        with patch("rm_client.backend.manager.AsyncDt7KeyMouseLink", side_effect=self.inert_factory) as factory:
            for _ in range(10):
                self.configure(manager, control, registry)
            factory.assert_not_called()
            self.assertFalse(control.dt7_enabled)
            self.assertFalse(relay.wait_closed(0))
            self.assertEqual(relay.owned_ports, ("CONFLICT",))
            failure[0] = False
            self.assertTrue(relay.wait_closed(1))
            self.configure(manager, control, registry)
            factory.assert_called_once()

    def active_dt7(self, manager, control, *, fail_neutral=False):
        neutral_entered, allow_neutral = threading.Event(), threading.Event()
        frames = []

        def write(frame):
            frames.append(bytes(frame))
            key = int.from_bytes(decode_dt7_frame(frame)["payload"][8:10], "little")
            if key == 0:
                neutral_entered.set()
                if not allow_neutral.wait(2):
                    raise TimeoutError("fake DT7 neutral was not released")
                if fail_neutral:
                    raise OSError("fake DT7 neutral failed")
            return len(frame)

        inner = Dt7KeyMouseLink(port="CONFLICT", profile=dict(DEFAULT_PROFILE), mock_output=False,
                               write_bytes=write)
        writer = AsyncDt7KeyMouseLink(port="CONFLICT", profile=dict(DEFAULT_PROFILE),
            mock_output=False, safety=manager.safety, link=inner, release_attempts=1,
            max_intent_age_s=60)

        def cleanup():
            allow_neutral.set()
            writer.close()
            self.assertTrue(writer.wait_closed(2))

        self.addCleanup(cleanup)
        self.assertTrue(writer.send(pressed_bits=1)[0])
        until(lambda: writer.sent == 1)
        self.assertFalse(writer.wire_neutral)
        manager._dt7_link = control.dt7_link = writer
        manager._dt7_was_enabled = control.dt7_enabled = True
        return writer, frames, neutral_entered, allow_neutral

    def test_conflict_revokes_active_dt7_and_requests_neutral_on_same_owner(self):
        relay, _, _, _ = self.relay_fixture()
        manager, control, registry = self.manager_fixture(relay)
        writer, frames, entered, unblock = self.active_dt7(manager, control)
        with patch("rm_client.backend.manager.AsyncDt7KeyMouseLink", side_effect=AssertionError("replacement")):
            self.configure(manager, control, registry)
            self.assertFalse(control.dt7_enabled)
            self.assertTrue(control.dt7_release_pending)
            self.assertTrue(entered.wait(0.5))
            self.assertIs(manager._dt7_link, writer)
            self.assertIs(control.dt7_link, writer)
            unblock.set()
            until(lambda: writer.wire_neutral)
            self.configure(manager, control, registry)
            self.assertFalse(control.dt7_enabled)
            self.assertFalse(control.dt7_release_pending)
            self.assertFalse(writer.closed)
            self.assertIs(manager._dt7_link, writer)
            self.assertEqual(len(frames), 2)

    def test_failed_dt7_neutral_retains_owner_and_exhausted_budget(self):
        relay, _, _, _ = self.relay_fixture()
        manager, control, registry = self.manager_fixture(relay)
        writer, frames, entered, unblock = self.active_dt7(manager, control, fail_neutral=True)
        with patch("rm_client.backend.manager.AsyncDt7KeyMouseLink", side_effect=AssertionError("replacement")):
            self.configure(manager, control, registry)
            self.assertTrue(entered.wait(0.5))
            unblock.set()
            until(lambda: writer._release_exhausted)
            for _ in range(10):
                self.configure(manager, control, registry)
            self.assertFalse(control.dt7_enabled)
            self.assertTrue(control.dt7_release_pending)
            self.assertTrue(writer.release_pending)
            self.assertFalse(writer.wire_neutral)
            self.assertIs(manager._dt7_link, writer)
            self.assertEqual(writer.release_attempts, 1)
            self.assertEqual(len(frames), 2)

    def test_non_neutral_owner_is_rebound_when_previous_enable_flag_was_lost(self):
        relay, _, _, _ = self.relay_fixture()
        manager, control, registry = self.manager_fixture(relay)
        writer, _, entered, unblock = self.active_dt7(manager, control)
        manager._dt7_was_enabled = control.dt7_enabled = False
        control.dt7_link = None
        self.configure(manager, control, registry)
        self.assertTrue(entered.wait(0.5))
        self.assertIs(control.dt7_link, writer)
        self.assertIs(manager._dt7_link, writer)
        self.assertFalse(control.dt7_enabled)
        self.assertTrue(control.dt7_release_pending)
        unblock.set()
        until(lambda: writer.wire_neutral)


if __name__ == "__main__":
    unittest.main()
