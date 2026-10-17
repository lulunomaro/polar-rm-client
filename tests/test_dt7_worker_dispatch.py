"""Offline regression harness: never opens hardware, including production paths."""
import statistics
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rm_client.backend.dt7_link import Dt7KeyMouseLink, DEFAULT_PROFILE, decode_dt7_frame
from rm_client.backend.dt7_worker import AsyncDt7KeyMouseLink
from rm_client.backend.safety import OfficialSafety
from rm_client import serial_ports


def until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError('worker condition timed out')
        time.sleep(.002)


def safe():
    safety = OfficialSafety()
    safety.allow_dt7_drive = True
    return safety


class DT7WorkerTests(unittest.TestCase):
    def make(self, **kwargs):
        link = AsyncDt7KeyMouseLink(profile=DEFAULT_PROFILE, mock_output=False,
                                   safety=safe(), **kwargs)
        self.addCleanup(link.close)
        return link

    def pending_fault_link(self):
        callbacks = []
        writes = []
        inner = Dt7KeyMouseLink(
            profile=dict(DEFAULT_PROFILE), mock_output=False, wire_neutral=False,
            write_bytes=lambda frame: (writes.append(frame) or len(frame)))
        link = self.make(link=inner)
        link.safety = SimpleNamespace(
            lock_control=lambda reason: callbacks.append(("lock", threading.get_ident())),
            mark_published=lambda *args, **kwargs: callbacks.append(("published", threading.get_ident())),
        )
        with link._cv:
            link._fault = "pending observer notification"
            link._published = (("DT7KeyboardMouseControl",), {})
        return link, callbacks, writes

    def test_worker_request_release_admits_neutral_without_draining_observers(self):
        link, callbacks, writes = self.pending_fault_link()
        request = threading.Thread(target=link.request_release)
        request.start()
        request.join(1)
        self.assertFalse(request.is_alive())
        until(lambda: link.wire_neutral)
        self.assertTrue(writes)
        self.assertEqual(callbacks, [])
        self.assertEqual(int.from_bytes(decode_dt7_frame(writes[-1])["payload"][8:10], "little"), 0)
        link.poll()
        self.assertEqual(callbacks, [("lock", threading.get_ident()),
                                     ("published", threading.get_ident())])
        link.close()
        self.assertTrue(link.wait_closed())

    def test_background_close_keeps_observers_for_owner_poll(self):
        link, callbacks, writes = self.pending_fault_link()
        request = threading.Thread(target=link.close)
        request.start()
        request.join(1)
        self.assertFalse(request.is_alive())
        self.assertTrue(link.wait_closed())
        self.assertTrue(writes)
        self.assertEqual(callbacks, [])
        link.poll()
        self.assertEqual(callbacks, [("lock", threading.get_ident()),
                                     ("published", threading.get_ident())])

    def test_missing_auto_backoff_precedes_discovery(self):
        clock = [10.0]
        link = Dt7KeyMouseLink(port='Auto', profile=dict(DEFAULT_PROFILE),
                              mock_output=False, time_fn=lambda: clock[0])
        with patch.object(link, '_open_target', return_value=('', 'missing')) as scan:
            for _ in range(1000):
                self.assertFalse(link.release()[0])
            self.assertEqual(scan.call_count, 1)
            clock[0] += .51
            self.assertFalse(link.release()[0])
            self.assertEqual(scan.call_count, 2)

    def test_worker_scan_open_write_never_blocks_ui_submission(self):
        calls = []
        writes = []
        entered = threading.Event()
        unblock = threading.Event()
        class Serial:
            def __init__(self, port, *args, **kwargs):
                calls.append(('open', threading.current_thread().name))
                self.port = port
            def write(self, frame):
                calls.append(('write', threading.current_thread().name))
                writes.append(frame)
                entered.set()
                unblock.wait(2)
                return len(frame)
            def close(self):
                calls.append(('close', threading.current_thread().name))
        def scan(*args):
            calls.append(('scan', threading.current_thread().name))
            time.sleep(.0193)
            return 'COM9', ''
        with patch.object(Dt7KeyMouseLink, '_open_target', scan), patch('serial.Serial', Serial):
            link = self.make()
            durations = []
            for i in range(1000):
                start = time.perf_counter()
                link.send(pressed_bits=1)
                durations.append((time.perf_counter()-start)*1000)
            self.assertTrue(entered.wait(1))
            start = time.perf_counter()
            link.release()
            link.close()
            self.assertLess(time.perf_counter()-start, .02)
            unblock.set()
            self.assertTrue(link.wait_closed(3))
            self.assertLess(max(durations), 20)
            self.assertTrue(all(name == 'dt7-writer' for _, name in calls), calls)
            self.assertEqual(int.from_bytes(decode_dt7_frame(writes[-1])['payload'][8:10], 'little'), 0)
            print(f'fake 19.3ms scan / blocked write: enqueue median={statistics.median(durations):.4f}ms max={max(durations):.4f}ms, calls={calls}')

    def test_slow_open_drops_stale_motion_before_first_write(self):
        written = []
        class Serial:
            def __init__(self, port, *args, **kwargs):
                self.port = port
                time.sleep(.2)
            def write(self, frame):
                written.append(frame)
                return len(frame)
            def close(self): pass
        with patch.object(Dt7KeyMouseLink, '_open_target', return_value=('COM9','')), patch('serial.Serial', Serial):
            link = self.make()
            link.send(pressed_bits=1)
            until(lambda: link.release_attempts > 0)
            until(lambda: not link.release_pending)
            self.assertTrue(written)
            self.assertTrue(all(int.from_bytes(decode_dt7_frame(x)['payload'][8:10], 'little') == 0 for x in written))
            link.close()
            self.assertTrue(link.wait_closed())

    def test_disconnect_retries_bounded_and_lock_callback_ui_only(self):
        scan_threads = []
        callbacks = []
        def missing(*args):
            scan_threads.append(threading.current_thread().name)
            return '', 'no DT7'
        with patch.object(Dt7KeyMouseLink, '_open_target', missing):
            link = self.make(release_attempts=3)
            link.safety.input_clear_hook = lambda reason: (callbacks.append(threading.current_thread().name), link.release())
            link.send(pressed_bits=1)
            until(lambda: bool(link._fault))
            for _ in range(1000):
                link.poll()
                link.release()
                link.send(pressed_bits=1)
            until(lambda: link._release_exhausted)
            before = len(scan_threads)
            for _ in range(1000):
                link.poll()
                link.release()
                link.send(pressed_bits=1)
            self.assertEqual(before, len(scan_threads))
            self.assertEqual(link.release_attempts, 3)
            self.assertEqual(callbacks, [threading.current_thread().name])
            self.assertTrue(link.release_pending)
            self.assertFalse(link.wire_neutral)
            self.assertFalse(link.send(pressed_bits=1)[0])
            self.assertLessEqual(len(scan_threads), 4)
            print(f'2000 failed-disconnect UI ticks: scans={len(scan_threads)}, neutral attempts={link.release_attempts}, input-clear callbacks={len(callbacks)}')
            link.close()
            self.assertTrue(link.wait_closed())

    def test_already_neutral_locked_ui_has_no_device_io(self):
        with patch.object(Dt7KeyMouseLink, '_open_target', side_effect=AssertionError('unexpected hardware')):
            link = self.make()
            for _ in range(10000):
                self.assertTrue(link.release()[0])
            self.assertEqual(link.release_attempts, 0)
            link.close()
            self.assertTrue(link.wait_closed())

    def test_release_cancels_motion_during_open_and_reconnect(self):
        entered, unblock = threading.Event(), threading.Event()
        writes = []
        class Serial:
            def __init__(self, port, *args, **kwargs):
                self.port = port
                entered.set()
                unblock.wait(2)
            def write(self, frame):
                writes.append(frame)
                return len(frame)
            def close(self): pass
        with patch.object(Dt7KeyMouseLink, '_open_target', return_value=('COM9','')), patch('serial.Serial', Serial):
            link = self.make(max_intent_age_s=5)
            link.send(pressed_bits=1)
            self.assertTrue(entered.wait(1))
            link.release()
            link.send(pressed_bits=2)
            unblock.set()
            until(lambda: link.release_attempts > 0)
            until(lambda: not link.release_pending)
            self.assertTrue(all(int.from_bytes(decode_dt7_frame(x)['payload'][8:10], 'little') == 0 for x in writes))
            link.close()
            self.assertTrue(link.wait_closed())

    def test_expired_qualification_callback_is_deferred_and_motion_cancelled(self):
        from dataclasses import replace
        callbacks, writes = [], []
        entered, unblock = threading.Event(), threading.Event()
        safety = OfficialSafety()
        token = safety.begin_qualification(['dt7_selftest'])
        safety.input_clear_hook = lambda reason: callbacks.append(threading.current_thread().name)
        class Serial:
            def __init__(self, port, *args, **kwargs):
                self.port = port
                entered.set()
                unblock.wait(2)
            def write(self, frame):
                writes.append(frame)
                return len(frame)
            def close(self): pass
        with patch.object(Dt7KeyMouseLink, '_open_target', return_value=('COM9','')), patch('serial.Serial', Serial):
            link = self.make(max_intent_age_s=1)
            self.addCleanup(unblock.set)
            link.safety = safety
            link.send(pressed_bits=1 << 12, qualification_token=token.value)
            # Expire only once opening has begun. A 25 ms wall-clock race can
            # otherwise expire before any port opens, where no release write
            # is required; that is safe but tests a different lifecycle edge.
            self.assertTrue(entered.wait(1))
            safety._qualification = replace(token, expires_at=time.perf_counter() - 1)
            unblock.set()
            until(lambda: bool(link._fault))
            self.assertEqual(callbacks, [])
            link.poll()
            self.assertEqual(callbacks, [threading.current_thread().name])
            until(lambda: link.release_attempts > 0)
            until(lambda: not link.release_pending)
            self.assertTrue(all(int.from_bytes(decode_dt7_frame(x)['payload'][8:10], 'little') == 0 for x in writes))
            link.close()
            self.assertTrue(link.wait_closed())

    def test_stale_key_up_and_silent_ui_watchdog_force_neutral(self):
        for queue_key_up in (True, False):
            with self.subTest(queue_key_up=queue_key_up):
                entered, unblock = threading.Event(), threading.Event()
                writes = []
                def write(frame):
                    writes.append(frame)
                    if len(writes) == 1:
                        entered.set()
                        unblock.wait(1)
                    return len(frame)
                inner = Dt7KeyMouseLink(profile=dict(DEFAULT_PROFILE), mock_output=False,
                                         write_bytes=write)
                link = self.make(link=inner, max_intent_age_s=.03)
                link.send(pressed_bits=1)
                self.assertTrue(entered.wait(1))
                if queue_key_up:
                    link.send(pressed_bits=0)
                time.sleep(.06)
                unblock.set()
                until(lambda: len(writes) >= 2)
                until(lambda: not link.release_pending)
                self.assertEqual(int.from_bytes(decode_dt7_frame(writes[-1])['payload'][8:10], 'little'), 0)
                self.assertTrue(link.wire_neutral)
                link.close()
                self.assertTrue(link.wait_closed())

    def test_qualification_hold_is_token_bound_and_bounded(self):
        from dataclasses import replace
        writes = []
        inner = Dt7KeyMouseLink(profile=dict(DEFAULT_PROFILE), mock_output=False,
                                 write_bytes=lambda frame: (writes.append(frame) or len(frame)))
        link = self.make(link=inner, max_intent_age_s=.03)
        link.safety = OfficialSafety()
        token = link.safety.begin_qualification(['dt7_selftest'])
        link.send(pressed_bits=1 << 12, qualification_token=token.value)
        until(lambda: len(writes) == 1)
        self.assertFalse(link.begin_qualification_hold('wrong', time.perf_counter() + 1))
        self.assertTrue(link.begin_qualification_hold(token.value, time.perf_counter() + .10))
        time.sleep(.06)
        self.assertEqual(len(writes), 1, 'host watchdog must not contaminate authorized receiver hold test')
        until(lambda: len(writes) >= 2)
        self.assertEqual(int.from_bytes(decode_dt7_frame(writes[-1])['payload'][8:10], 'little'), 0)
        link.close()
        self.assertTrue(link.wait_closed())

    def test_relative_mouse_samples_accumulate_while_keys_are_latest(self):
        import struct
        writes = []
        inner = Dt7KeyMouseLink(profile=dict(DEFAULT_PROFILE), mock_output=False,
                                 write_bytes=lambda frame: (writes.append(frame) or len(frame)))
        inner._next_due = time.perf_counter() + .04
        link = self.make(link=inner)
        for bits in (1, 4, 2):
            link.send(pressed_bits=bits, mouse_dx=3, mouse_y=-2)
        until(lambda: bool(writes))
        x, y, z, left, right, keys, reserved = struct.unpack('<hhhBBHH', decode_dt7_frame(writes[0])['payload'])
        self.assertEqual((x, y, keys), (9, -6, 2))
        link.close()
        self.assertTrue(link.wait_closed())

    def test_no_device_real_time_100hz_tick_retry_budget(self):
        calls = []
        with patch.object(Dt7KeyMouseLink, '_open_target', side_effect=lambda: (calls.append(time.monotonic()) or ('', 'no DT7'))):
            link = self.make()
            started = time.monotonic()
            durations = []
            caller_cpu_ms = []
            for index in range(250):
                cpu_begin = time.thread_time()
                begin = time.perf_counter()
                link.poll()
                link.send(pressed_bits=1)
                if link.release_pending:
                    link.release()
                durations.append((time.perf_counter() - begin) * 1000)
                caller_cpu_ms.append((time.thread_time() - cpu_begin) * 1000)
                time.sleep(max(0, started + (index + 1) / 100 - time.monotonic()))
            elapsed = time.monotonic() - started
            self.assertLessEqual(len(calls), 4)
            self.assertLessEqual(link.release_attempts, 3)
            slowest = max(range(len(durations)), key=durations.__getitem__)
            self.assertLess(max(durations), 20, (
                f"tick={slowest + 1}/{len(durations)}, "
                f"caller_wall_ms={durations[slowest]:.6f}, "
                f"caller_thread_cpu_ms={caller_cpu_ms[slowest]:.6f}, "
                f"scans={len(calls)}, retries={link.release_attempts}; "
                "CPU interval includes wall-clock bookkeeping; cause not inferred"))
            print(f'250 real-time 100Hz ticks / {elapsed:.3f}s: scans={len(calls)}, retries={link.release_attempts}, caller median={statistics.median(durations):.4f}ms max={max(durations):.4f}ms')
            link.close()
            self.assertTrue(link.wait_closed())

    def test_persistent_handle_does_not_rescan_per_frame(self):
        handle = SimpleNamespace(port='COM9', write=lambda data: len(data), close=lambda: None)
        link = Dt7KeyMouseLink(port='Auto', profile=dict(DEFAULT_PROFILE), mock_output=False)
        with patch.object(link, '_open_target', return_value=('COM9','')) as scan, patch('serial.Serial', return_value=handle):
            for _ in range(100):
                self.assertTrue(link.release()[0])
            self.assertEqual(scan.call_count, 1)
        link.close()

    def test_discovery_snapshot_nonblocking_ttl_invalidation(self):
        calls = []
        def scan():
            calls.append(threading.current_thread().name)
            time.sleep(.0193)
            return [{'device':'COM9'}]
        serial_ports.invalidate_port_cache()
        with patch.object(serial_ports, '_scan_serial_ports', scan):
            start = time.perf_counter()
            serial_ports.port_snapshot()
            self.assertLess(time.perf_counter()-start, .015)
            until(lambda: not serial_ports._port_refresh_running)
            self.assertEqual(serial_ports.list_serial_ports(), [{'device':'COM9'}])
            for _ in range(100):
                serial_ports.port_snapshot()
            self.assertEqual(calls, ['serial-discovery'])
            serial_ports.invalidate_port_cache()
            serial_ports.port_snapshot()
            until(lambda: not serial_ports._port_refresh_running)
            self.assertEqual(len(calls), 2)
        serial_ports.invalidate_port_cache()


if __name__ == '__main__':
    unittest.main()
