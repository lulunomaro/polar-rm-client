"""Manager egress failures must retain the old writer and request neutral."""
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.cc_link import AsyncCustomControllerLink, decode_cc_frame
from rm_client.backend.manager import BackendManager
from rm_client.backend.device_egress_settings import CustomControllerSettings, EgressPermissions
from rm_client.control import KeyboardControlBridge


def until(predicate, timeout=1.0):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError("fake CC writer did not reach expected state")
        time.sleep(0.001)


class DeviceEgressSafetyTests(unittest.TestCase):
    def active_cc_fixture(self, *, fail_neutral=False, was_enabled=True):
        neutral_entered = threading.Event()
        allow_neutral = threading.Event()
        frames = []

        def write(frame):
            frames.append(bytes(frame))
            if decode_cc_frame(frame)["data"][:2] == b"\0\0":
                neutral_entered.set()
                if not allow_neutral.wait(2):
                    raise TimeoutError("fake neutral write was not released")
                if fail_neutral:
                    raise OSError("fake neutral write failed")
            return len(frame)

        # Freeze the worker clock so its later input watchdog cannot make a
        # missing manager falling edge appear to have succeeded.
        link = AsyncCustomControllerLink(port="OLD", mock_output=False,
            write_bytes=write, time_fn=lambda: 100.0, max_hz=1000, release_attempts=1)

        def cleanup():
            allow_neutral.set()
            link.close()
            self.assertTrue(link.wait_closed(2))

        self.addCleanup(cleanup)
        self.assertTrue(link.send_keymouse(pressed=["W"])[0])
        until(lambda: link.sent == 1)
        self.assertFalse(link.wire_neutral)
        manager = object.__new__(BackendManager)
        manager._cc_link = link
        manager._cc_was_enabled = was_enabled
        manager.controller_angles = SimpleNamespace(owned_ports=("CONFLICT",))
        manager._cc_link_for = lambda _: self.fail("conflict must not replace the retained writer")
        control = KeyboardControlBridge(publisher=None, mock_output=True)
        control.cc_link = link
        control.cc_enabled = was_enabled
        settings = CustomControllerSettings(requested_value=True, port_value="CONFLICT", mock_value=False)
        return manager, control, settings, link, frames, neutral_entered, allow_neutral

    def configure(self, manager, control, settings):
        manager._configure_cc_egress(control, settings, EgressPermissions(True, True, False))

    def test_angle_port_conflict_requests_old_writer_neutral_before_return(self):
        manager, control, settings, link, frames, entered, unblock = self.active_cc_fixture()
        self.configure(manager, control, settings)
        self.assertFalse(control.cc_enabled)
        self.assertTrue(control.cc_release_pending)
        self.assertIs(manager._cc_link, link)
        self.assertIs(control.cc_link, link)
        self.assertTrue(entered.wait(0.5))
        self.assertFalse(link.wire_neutral)
        unblock.set()
        until(lambda: link.wire_neutral)
        self.configure(manager, control, settings)
        self.assertFalse(control.cc_release_pending)
        self.assertFalse(control.cc_enabled)
        self.assertEqual(len(frames), 2)
        self.assertEqual(decode_cc_frame(frames[-1])["data"][:2], b"\0\0")
        self.assertIs(manager._cc_link, link)

    def test_conflict_retains_failed_neutral_without_rearming_retry_budget(self):
        manager, control, settings, link, frames, entered, unblock = self.active_cc_fixture(fail_neutral=True)
        self.configure(manager, control, settings)
        self.assertTrue(entered.wait(0.5))
        unblock.set()
        until(lambda: link.failed == 1 and link._release_left == 0)
        for _ in range(20):
            self.configure(manager, control, settings)
        self.assertEqual(len(frames), 2)
        self.assertIs(manager._cc_link, link)
        self.assertIs(control.cc_link, link)
        self.assertFalse(control.cc_enabled)
        self.assertTrue(control.cc_release_pending)
        self.assertFalse(link.wire_neutral)
        self.assertTrue(link.release_pending)

    def test_conflict_also_neutralizes_retained_writer_after_enable_flag_was_lost(self):
        manager, control, settings, link, _, entered, unblock = self.active_cc_fixture(was_enabled=False)
        control.cc_link = None
        self.configure(manager, control, settings)
        self.assertTrue(entered.wait(0.5))
        self.assertIs(control.cc_link, link)
        self.assertTrue(control.cc_release_pending)
        self.assertFalse(control.cc_enabled)
        unblock.set()
        until(lambda: link.wire_neutral)


if __name__ == "__main__":
    unittest.main()
