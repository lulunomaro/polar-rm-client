"""Fake-only ownership regressions for DT7 and custom-controller shutdown."""
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend.cc_link import AsyncCustomControllerLink
from rm_client.backend.dt7_link import DEFAULT_PROFILE, Dt7KeyMouseLink
from rm_client.backend.dt7_worker import AsyncDt7KeyMouseLink


def until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("fake worker did not reach expected state")
        time.sleep(0.002)


class FakeHandle:
    def __init__(self, close_mode="raise", *, write_fails=False):
        self.close_mode = close_mode
        self.write_fails = write_fails
        self.is_open = True
        self.close_threads = []
        self.writes = []
        self.close_entered = threading.Event()
        self.close_unblock = threading.Event()
        self.close_unblock.set()

    def write(self, frame):
        self.writes.append(bytes(frame))
        if self.write_fails:
            raise OSError("fake write failed")
        return len(frame)

    def close(self):
        self.close_threads.append(threading.get_ident())
        self.close_entered.set()
        if not self.close_unblock.wait(2):
            raise TimeoutError("test did not release fake close")
        if self.close_mode == "raise":
            raise OSError("fake close failed")
        if self.close_mode == "false":
            return False
        if self.close_mode == "still_open":
            return None
        self.is_open = False
        return None


def sync_dt7(handle):
    return Dt7KeyMouseLink(
        port="FAKE", profile=dict(DEFAULT_PROFILE), mock_output=False,
        _handle=handle, resolved_port="FAKE", _handle_requested_port="FAKE")


class Dt7CloseOwnershipTests(unittest.TestCase):
    def test_unconfirmed_close_retains_handle_until_explicit_retry(self):
        for mode in ("raise", "false", "still_open"):
            with self.subTest(mode=mode):
                handle = FakeHandle(mode)
                link = sync_dt7(handle)
                with self.assertRaises(OSError):
                    link.close()
                self.assertIs(link._handle, handle)
                self.assertEqual(link.resolved_port, "FAKE")
                handle.close_mode = "ok"
                link.close()
                self.assertIsNone(link._handle)
                self.assertEqual(link.resolved_port, "")

    def test_port_change_cannot_replace_or_reuse_failed_close_handle(self):
        handle = FakeHandle()
        link = sync_dt7(handle)
        link.port = "NEXT"
        replacement = FakeHandle("ok")
        with patch.object(link, "_open_target", return_value=("NEXT", "")), \
                patch("serial.Serial", return_value=replacement) as factory:
            with self.assertRaises(OSError):
                link._emit(b"fake")
            self.assertIs(link._handle, handle)
            factory.assert_not_called()
            link.port = "FAKE"
            with self.assertRaises(RuntimeError):
                link._emit(b"must not reuse failed close")
            self.assertEqual(handle.writes, [])
            self.assertEqual(len(handle.close_threads), 1)
            handle.close_mode = "ok"
            link.close()
            link._emit(b"after explicit recovery")
            factory.assert_called_once()
            self.assertEqual(replacement.writes, [b"after explicit recovery"])
            link.close()

    def test_write_failure_keeps_unconfirmed_handle_and_blocks_reopen(self):
        handle = FakeHandle(write_fails=True)
        link = sync_dt7(handle)
        with patch("serial.Serial") as factory:
            self.assertFalse(link.release()[0])
            self.assertIs(link._handle, handle)
            link._open_backoff_until = 0.0
            self.assertFalse(link.release()[0])
            factory.assert_not_called()
            self.assertEqual(len(handle.writes), 1)
            self.assertEqual(len(handle.close_threads), 1)
        handle.close_mode = "ok"
        link.close()


class AsyncCloseOwnershipTests(unittest.TestCase):
    def make(self, kind, handle):
        if kind == "dt7":
            link = AsyncDt7KeyMouseLink(
                link=sync_dt7(handle), profile=DEFAULT_PROFILE, mock_output=False)
        else:
            link = AsyncCustomControllerLink(mock_output=False, release_attempts=1)
            with link._condition:
                link._serial = handle

        def cleanup():
            handle.close_mode = "ok"
            handle.close_unblock.set()
            link.close()
            link.wait_closed(2)

        self.addCleanup(cleanup)
        return link

    def owned_handle(self, kind, link):
        return link._link._handle if kind == "dt7" else link._serial

    def test_failed_finalizer_is_retained_and_retry_is_single_background_owner(self):
        for kind in ("dt7", "cc"):
            for mode in ("raise", "false", "still_open"):
                with self.subTest(kind=kind, mode=mode):
                    handle = FakeHandle(mode)
                    link = self.make(kind, handle)
                    link.close()
                    self.assertFalse(link.wait_closed(0.5))
                    until(lambda: not link.worker_alive)
                    self.assertFalse(link.closed)
                    self.assertIs(self.owned_handle(kind, link), handle)
                    self.assertIn("close", link.last_error.lower())
                    self.assertEqual(len(handle.close_threads), 1)

                    handle.close_mode = "ok"
                    handle.close_entered.clear()
                    handle.close_unblock.clear()
                    started = time.monotonic()
                    link.close()
                    self.assertLess(time.monotonic() - started, 0.1)
                    self.assertTrue(handle.close_entered.wait(1))
                    for _ in range(100):
                        link.close()
                    self.assertEqual(len(handle.close_threads), 2)
                    self.assertFalse(link.closed)
                    self.assertIs(self.owned_handle(kind, link), handle)
                    if kind == "dt7":
                        self.assertFalse(link.send(pressed_bits=1)[0])
                    else:
                        self.assertFalse(link.send_keymouse(pressed=["W"])[0])
                    handle.close_unblock.set()
                    self.assertTrue(link.wait_closed(1))
                    self.assertIsNone(self.owned_handle(kind, link))
                    self.assertTrue(all(owner != threading.get_ident()
                                        for owner in handle.close_threads))

    def test_explicit_unbounded_wait_finishes_with_failed_close_result(self):
        for kind in ("dt7", "cc"):
            with self.subTest(kind=kind):
                handle = FakeHandle()
                link = self.make(kind, handle)
                result = []
                errors = []

                def wait_in_background():
                    try:
                        result.append(link.wait_closed(None))
                    except Exception as exc:
                        errors.append(exc)

                link.close()
                waiter = threading.Thread(target=wait_in_background, daemon=True)
                waiter.start()
                waiter.join(1)
                self.assertFalse(waiter.is_alive(), "failed close must finish the explicit wait")
                self.assertEqual(errors, [])
                self.assertEqual(result, [False])

    def test_explicit_unbounded_wait_confirms_successful_close(self):
        for kind in ("dt7", "cc"):
            with self.subTest(kind=kind):
                handle = FakeHandle("ok")
                link = self.make(kind, handle)
                result = []
                link.close()
                waiter = threading.Thread(
                    target=lambda: result.append(link.wait_closed(None)), daemon=True)
                waiter.start()
                waiter.join(1)
                self.assertFalse(waiter.is_alive())
                self.assertEqual(result, [True])
                self.assertFalse(handle.is_open)

    def test_cc_failed_disconnect_close_neither_reopens_nor_claims_neutral(self):
        handle = FakeHandle(write_fails=True)
        link = self.make("cc", handle)
        with patch("serial.Serial") as factory:
            self.assertTrue(link.send_keymouse(pressed=["W"])[0])
            until(lambda: bool(handle.close_threads))
            self.assertIs(link._serial, handle)
            until(lambda: not link.worker_alive)
            self.assertFalse(link.wait_closed(None))
            self.assertFalse(link.closed)
            self.assertFalse(link.wire_neutral)
            self.assertTrue(link.release_pending)
            self.assertFalse(link.send_keymouse(pressed=["D"])[0])
            self.assertFalse(link.retry_release()[0])
            factory.assert_not_called()
            self.assertEqual(len(handle.writes), 1)
            self.assertEqual(len(handle.close_threads), 1)


if __name__ == "__main__":
    unittest.main()
