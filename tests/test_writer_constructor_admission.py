"""Defensive writer-construction admission faults, using real worker objects.

The injected start wrapper may reject before admission, or call the real
Thread.start and then raise. This is a fault model, not a claim that ordinary
CPython Thread.start commonly raises after admitting a worker. Native handles
are fake; no serial device, network service, or GUI is opened.
"""
from pathlib import Path
import gc
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend.cc_link import AsyncCustomControllerLink
from rm_client.backend.dt7_link import DEFAULT_PROFILE
from rm_client.backend.dt7_worker import AsyncDt7KeyMouseLink
from rm_client.backend.manager import BackendManager
from rm_client.control import KeyboardControlBridge


FACTORIES = {"dt7": AsyncDt7KeyMouseLink, "cc": AsyncCustomControllerLink}


class UnannotatableFailure(BaseException):
    def __setattr__(self, name, value):
        raise RuntimeError("exception annotation rejected")

    def add_note(self, note):
        raise RuntimeError("exception note rejected")


class Handle:
    def __init__(self, mode="ok", *, blocked=False):
        self.mode = mode
        self.is_open = True
        self.entered = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.close_threads = []
        self.writes = []

    def close(self):
        self.close_threads.append(threading.get_ident())
        self.entered.set()
        if not self.release.wait(3):
            raise TimeoutError("test must release fake native close")
        if self.mode == "raise":
            raise OSError("fake native close failed")
        if self.mode == "false":
            return False
        if self.mode != "still_open":
            self.is_open = False

    def write(self, frame):
        self.writes.append(bytes(frame))
        return len(frame)


class WriterConstructorAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.start_thread = threading.Thread.start
        self.owners = []  # weakrefs only: never a substitute for production ownership
        self.workers = []
        self.handles = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for handle in self.handles:
            handle.mode = "ok"
            handle.release.set()
        for reference in self.owners:
            owner = reference()
            if owner is not None:
                owner.close()
                # Baseline cleanup also handles its never-admitted thread.
                owner.close()
                self.assertTrue(owner.wait_closed(2))
        for worker in self.workers:
            if worker.ident is not None:
                worker.join(2)
                self.assertFalse(worker.is_alive())

    def install_handle(self, kind, owner, handle):
        if kind == "dt7":
            owner._link._handle = handle
            owner._link.resolved_port = "FAKE"
            owner._link._handle_requested_port = owner.port
        else:
            owner._serial = handle
        self.handles.append(handle)

    def admission(self, kind, failure, *, state="live", handle=None):
        def start(worker):
            if worker.name != kind + "-writer":
                return self.start_thread(worker)
            owner = worker._target.__self__
            self.owners.append(weakref.ref(owner))
            self.workers.append(worker)
            if handle is not None:
                self.install_handle(kind, owner, handle)
            if state != "never":
                self.start_thread(worker)
            if state == "finished":
                owner.close()
                worker.join(1)
                self.assertFalse(worker.is_alive())
            raise failure
        return patch.object(threading.Thread, "start", start)

    def construct_failure(self, kind, failure, **kwargs):
        try:
            FACTORIES[kind](mock_output=True, **kwargs)
        except BaseException as caught:
            self.assertIs(caught, failure)
            return caught
        self.fail("writer construction hid its admission failure")

    def test_live_late_error_cancels_actual_worker_and_preserves_primary_failure(self):
        for kind in FACTORIES:
            for error_type in (RuntimeError, KeyboardInterrupt):
                with self.subTest(kind=kind, error_type=error_type):
                    failure = error_type("after real worker admission")
                    with self.admission(kind, failure):
                        self.construct_failure(kind, failure)
                    owner = self.owners[-1]()
                    self.assertTrue(owner._closing)
                    self.assertIs(failure.failed_writer, owner)
                    self.assertTrue(owner.wait_closed(1))
                    self.assertFalse(owner.worker_alive)
                    self.assertTrue(owner.closed)

    def test_never_admitted_worker_has_owner_and_background_close_without_false_join(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                failure = RuntimeError("writer rejected before start")
                handle = Handle(blocked=True)
                with self.admission(kind, failure, state="never", handle=handle):
                    begin = time.monotonic()
                    self.construct_failure(kind, failure)
                    self.assertLess(time.monotonic() - begin, 0.1)
                owner = self.owners[-1]()
                self.assertIs(failure.failed_writer, owner)
                self.assertIsNone(self.workers[-1].ident)
                self.assertTrue(handle.entered.wait(1))
                self.assertFalse(owner.wait_closed(0))
                self.assertFalse(owner.closed)
                self.assertFalse(owner.port_handoff_ready)
                self.assertNotIn(threading.get_ident(), handle.close_threads)
                handle.release.set()
                self.assertTrue(owner.wait_closed(1))

    def test_finished_late_error_keeps_completion_without_duplicate_cleanup(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                failure = RuntimeError("after worker already finished")
                handle = Handle()
                with self.admission(kind, failure, state="finished", handle=handle):
                    self.construct_failure(kind, failure)
                owner = self.owners[-1]()
                self.assertIs(failure.failed_writer, owner)
                self.assertIs(owner._thread, self.workers[-1])
                self.assertTrue(owner.wait_closed(0))
                self.assertEqual(len(handle.close_threads), 1)

    def test_finished_failed_finalizer_is_retained_without_implicit_second_attempt(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                failure = RuntimeError("after failed finalizer already finished")
                handle = Handle("raise")
                with self.admission(kind, failure, state="finished", handle=handle):
                    self.construct_failure(kind, failure)
                owner = self.owners[-1]()
                self.assertFalse(owner.wait_closed(1))
                self.assertIs(owner._thread, self.workers[-1])
                self.assertEqual(len(handle.close_threads), 1)
                self.assertFalse(owner.closed)
                handle.mode = "ok"
                owner.close()
                self.assertTrue(owner.wait_closed(1))

    def test_late_error_does_not_wait_for_slow_native_close_or_duplicate_owner(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                failure = RuntimeError("after real admission")
                handle = Handle(blocked=True)
                with self.admission(kind, failure, handle=handle):
                    begin = time.monotonic()
                    self.construct_failure(kind, failure)
                    self.assertLess(time.monotonic() - begin, 0.1)
                owner = self.owners[-1]()
                self.assertIs(failure.failed_writer, owner)
                self.assertTrue(handle.entered.wait(1))
                self.assertTrue(owner.worker_alive)
                self.assertFalse(owner.closed)
                self.assertFalse(owner.port_handoff_ready)
                for _ in range(30):
                    owner.close()
                self.assertEqual(len(handle.close_threads), 1)
                self.assertNotIn(threading.get_ident(), handle.close_threads)
                handle.release.set()
                self.assertTrue(owner.wait_closed(1))

    def test_failed_close_retains_native_owner_for_explicit_background_retry(self):
        for kind in FACTORIES:
            for mode in ("raise", "false", "still_open"):
                with self.subTest(kind=kind, mode=mode):
                    failure = RuntimeError("after real admission")
                    handle = Handle(mode)
                    with self.admission(kind, failure, handle=handle):
                        self.construct_failure(kind, failure)
                    owner = self.owners[-1]()
                    self.assertIs(failure.failed_writer, owner)
                    self.assertFalse(owner.wait_closed(1))
                    self.assertFalse(owner.worker_alive)
                    self.assertFalse(owner.closed)
                    self.assertFalse(owner.port_handoff_ready)
                    retained = owner._link._handle if kind == "dt7" else owner._serial
                    self.assertIs(retained, handle)
                    self.assertEqual(len(handle.close_threads), 1)
                    handle.mode = "ok"
                    owner.close()
                    self.assertTrue(owner.wait_closed(1))
                    self.assertFalse(handle.is_open)
                    self.assertEqual(len(handle.close_threads), 2)
                    self.assertNotIn(threading.get_ident(), handle.close_threads)

    def test_failed_native_close_recovers_after_explicit_retry_admission_rejection(self):
        for kind in FACTORIES:
            for reject_at in ("construction", "start"):
                with self.subTest(kind=kind, reject_at=reject_at):
                    failure = RuntimeError("after original writer admission")
                    handle = Handle("raise")
                    with self.admission(kind, failure, handle=handle):
                        self.construct_failure(kind, failure)
                    owner = failure.failed_writer
                    self.assertFalse(owner.wait_closed(1))
                    self.assertEqual(len(handle.close_threads), 1)
                    target = "threading.Thread" if reject_at == "construction" else "threading.Thread.start"
                    with patch(target, side_effect=RuntimeError("explicit cleanup retry rejected")):
                        with self.assertRaisesRegex(RuntimeError, "explicit cleanup retry rejected"):
                            owner.close()
                    self.assertFalse(owner.wait_closed(0))
                    self.assertFalse(owner.worker_alive)
                    self.assertEqual(len(handle.close_threads), 1)
                    handle.mode = "ok"
                    owner.close()
                    self.assertTrue(owner.wait_closed(1))
                    self.assertFalse(handle.is_open)
                    self.assertEqual(len(handle.close_threads), 2)

    def test_retention_happens_before_worker_construction_and_annotation_is_optional(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                failure = UnannotatableFailure("worker object construction rejected")
                retained = []
                original = threading.Thread
                def create(*args, **kwargs):
                    self.assertEqual(len(retained), 1)
                    if kwargs.get("name") == kind + "-writer":
                        raise failure
                    return original(*args, **kwargs)
                with patch.object(threading, "Thread", create):
                    self.construct_failure(kind, failure, retain_owner=retained.append)
                owner = retained[0]
                self.owners.append(weakref.ref(owner))
                self.assertTrue(owner._closing)
                self.assertTrue(owner.wait_closed(1))

    def test_rejected_cleanup_construction_or_start_preserves_primary_and_retry_path(self):
        for kind in FACTORIES:
            for reject_at in ("construction", "start"):
                with self.subTest(kind=kind, reject_at=reject_at):
                    failure = UnannotatableFailure("initial writer rejected")
                    retained = []
                    original = threading.Thread
                    def create(*args, **kwargs):
                        if reject_at == "construction" and kwargs.get("name") == kind + "-close-retry":
                            raise RuntimeError("cleanup construction rejected")
                        return original(*args, **kwargs)
                    def start(worker):
                        if worker.name == kind + "-writer":
                            raise failure
                        raise RuntimeError("cleanup admission rejected")
                    with patch.object(threading, "Thread", create), patch.object(original, "start", start):
                        self.construct_failure(kind, failure, retain_owner=retained.append)
                    owner = retained[0]
                    self.owners.append(weakref.ref(owner))
                    self.assertTrue(owner._closing)
                    self.assertFalse(owner.worker_alive)
                    self.assertFalse(owner.wait_closed(0))
                    self.assertFalse(owner.closed)
                    owner.close()
                    self.assertTrue(owner.wait_closed(1))

    def test_cleanup_late_start_error_preserves_initial_failure_and_live_cleanup_owner(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                failure = RuntimeError("initial worker rejected")
                handle = Handle(blocked=True)
                retained = []
                def retain(owner):
                    retained.append(owner)
                    self.owners.append(weakref.ref(owner))
                    self.install_handle(kind, owner, handle)
                def start(worker):
                    self.workers.append(worker)
                    if worker.name == kind + "-writer":
                        raise failure
                    self.start_thread(worker)
                    raise RuntimeError("after actual cleanup admission")
                with patch.object(threading.Thread, "start", start):
                    self.construct_failure(kind, failure, retain_owner=retain)
                owner = retained[0]
                self.assertIs(failure.failed_writer, owner)
                self.assertTrue(handle.entered.wait(1))
                self.assertTrue(owner.worker_alive)
                self.assertFalse(owner.wait_closed(0))
                for _ in range(20):
                    owner.close()
                self.assertEqual(len(handle.close_threads), 1)
                handle.release.set()
                self.assertTrue(owner.wait_closed(1))

    def test_raising_retention_callback_is_sealed_before_any_worker_admission(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                failure = UnannotatableFailure("retention callback failed")
                retained = []
                handle = Handle(blocked=True)
                def retain(owner):
                    retained.append(owner)
                    self.owners.append(weakref.ref(owner))
                    self.install_handle(kind, owner, handle)
                    raise failure
                original = threading.Thread
                names = []
                def create(*args, **kwargs):
                    names.append(kwargs.get("name"))
                    return original(*args, **kwargs)
                with patch.object(threading, "Thread", create):
                    self.construct_failure(kind, failure, retain_owner=retain)
                owner = retained[0]
                self.assertNotIn(kind + "-writer", names)
                self.assertTrue(owner._closing)
                self.assertTrue(handle.entered.wait(1))
                self.assertFalse(owner.wait_closed(0))
                self.assertNotIn(threading.get_ident(), handle.close_threads)
                handle.release.set()
                self.assertTrue(owner.wait_closed(1))

    def test_retention_callback_runs_once_outside_worker_lock_before_admission(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                retained = []
                def retain(owner):
                    retained.append(owner)
                    self.owners.append(weakref.ref(owner))
                    acquired = threading.Event()
                    lock = owner._cv if kind == "dt7" else owner._condition
                    def observe():
                        with lock:
                            acquired.set()
                    observer = threading.Thread(target=observe)
                    self.start_thread(observer)
                    observer.join(1)
                    self.assertTrue(acquired.is_set())
                    self.assertFalse(owner.worker_alive)
                owner = FACTORIES[kind](mock_output=True, retain_owner=retain)
                self.assertEqual(retained, [owner])
                self.assertFalse(owner.startup_failed)
                owner.close()
                self.assertTrue(owner.wait_closed(1))

    def test_discarded_exception_does_not_leave_live_orphan_worker(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                failure = RuntimeError("after real admission")
                with self.admission(kind, failure):
                    self.construct_failure(kind, failure)
                worker = self.workers[-1]
                failure.__traceback__ = None
                del failure
                gc.collect()
                worker.join(1)
                self.assertFalse(worker.is_alive())

    def test_cancellation_during_initial_admission_keeps_one_cleanup_owner(self):
        for kind in FACTORIES:
            for stage in ("construction", "start"):
                for result in ("return", "reject", "late_error"):
                    with self.subTest(kind=kind, stage=stage, result=result):
                        failure = RuntimeError("cancelled during initial admission")
                        handle = Handle(blocked=True)
                        retained = []
                        created = []
                        original = threading.Thread
                        def retain(owner):
                            retained.append(owner)
                            self.owners.append(weakref.ref(owner))
                            self.install_handle(kind, owner, handle)
                        def create(*args, **kwargs):
                            if kwargs["name"] == kind + "-writer" and stage == "construction":
                                kwargs["target"].__self__.close()
                                if result == "reject":
                                    raise failure
                            worker = original(*args, **kwargs)
                            created.append(worker)
                            self.workers.append(worker)
                            return worker
                        def start(worker):
                            if worker.name == kind + "-writer":
                                if stage == "start":
                                    worker._target.__self__.close()
                                    if result == "reject":
                                        raise failure
                                self.start_thread(worker)
                                if result == "late_error":
                                    raise failure
                            else:
                                self.start_thread(worker)
                        try:
                            with patch.object(threading, "Thread", create), patch.object(original, "start", start):
                                if result == "return":
                                    owner = FACTORIES[kind](mock_output=True, retain_owner=retain)
                                    self.assertIs(owner, retained[0])
                                else:
                                    self.construct_failure(kind, failure, retain_owner=retain)
                            owner = retained[0]
                            self.assertTrue(owner._closing)
                            self.assertTrue(handle.entered.wait(1))
                            admitted = [worker for worker in created if worker.ident is not None]
                            self.assertEqual(len(admitted), 1)
                            self.assertIs(owner._thread, admitted[0])
                            self.assertEqual(len(handle.close_threads), 1)
                            self.assertFalse(owner.closed)
                        finally:
                            handle.release.set()
                            for worker in created:
                                if worker.ident is not None:
                                    worker.join(1)
                            if retained:
                                retained[0].close()
                                self.assertTrue(retained[0].wait_closed(1))

    def manager_fixture(self, kind):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": directory.name}):
            manager = BackendManager(workspace=directory.name)
        self.addCleanup(manager.shutdown)
        control = KeyboardControlBridge(publisher=None, mock_output=True)
        values = {name: SimpleNamespace(value=value) for name, value in {
            "Direct DT7 Egress": kind == "dt7", "DT7 Real TX": True,
            "DT7 Port": "FAKE", "Custom Controller Egress": kind == "cc",
            "CC Mock Writes": False, "CC Port": "FAKE", "Allow Drive": True,
        }.items()}
        module = SimpleNamespace(enabled=True, get_setting=values.get)
        registry = SimpleNamespace(get=lambda name: module)
        manager.safety.estop_active = False
        manager.safety.allow_dt7_drive = True
        return manager, control, registry

    def configure(self, manager, control, registry):
        with patch("rm_client.backend.manager.dt7_link.load_profile", return_value=dict(DEFAULT_PROFILE)):
            return manager._configure_device_egress(registry, control)

    def test_manager_keeps_unadmitted_owner_when_cleanup_admission_also_fails(self):
        for kind in FACTORIES:
            for reject_at in ("construction", "start"):
                with self.subTest(kind=kind, reject_at=reject_at):
                    manager, control, registry = self.manager_fixture(kind)
                    failure = UnannotatableFailure("initial worker rejected")
                    handle = Handle()
                    original = threading.Thread
                    def create(*args, **kwargs):
                        owner = kwargs["target"].__self__
                        if kwargs["name"] == kind + "-writer":
                            self.assertIs(getattr(manager, "_" + kind + "_link"), owner)
                            self.owners.append(weakref.ref(owner))
                            self.install_handle(kind, owner, handle)
                            if reject_at == "construction":
                                raise failure
                        else:
                            raise RuntimeError("cleanup construction rejected")
                        return original(*args, **kwargs)
                    with patch.object(threading, "Thread", create), \
                            patch.object(original, "start", side_effect=failure):
                        try:
                            self.configure(manager, control, registry)
                        except BaseException as caught:
                            self.assertIs(caught, failure)
                        else:
                            self.fail("manager hid writer admission error")
                    BaseException.__setattr__(failure, "__traceback__", None)
                    gc.collect()
                    owner = getattr(manager, "_" + kind + "_link")
                    self.assertTrue(owner._closing)
                    self.assertFalse(owner.wait_closed(0))
                    self.assertFalse(owner.worker_alive)
                    self.assertEqual(handle.close_threads, [])
                    self.configure(manager, control, registry)
                    self.assertFalse(getattr(control, kind + "_enabled"))
                    self.assertEqual(handle.close_threads, [])
                    owner.close()
                    self.assertTrue(owner.wait_closed(1))
                    self.assertFalse(handle.is_open)
                    self.assertNotIn(threading.get_ident(), handle.close_threads)

    def test_manager_retains_failed_owner_after_traceback_gc_and_blocks_replacement(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                manager, control, registry = self.manager_fixture(kind)
                failure = UnannotatableFailure("after manager writer admission")
                handle = Handle("raise")
                with self.admission(kind, failure, handle=handle):
                    try:
                        self.configure(manager, control, registry)
                    except BaseException as caught:
                        self.assertIs(caught, failure)
                    else:
                        self.fail("manager hid writer admission error")
                reference = self.owners[-1]
                BaseException.__setattr__(failure, "__traceback__", None)
                gc.collect()
                owner = getattr(manager, "_" + kind + "_link", None)
                self.assertIsNotNone(owner)
                self.assertIs(owner, reference())
                self.assertFalse(owner.wait_closed(1))
                self.assertFalse(owner.worker_alive)
                with patch.object(threading.Thread, "start", side_effect=AssertionError("must not readmit on polling")):
                    for _ in range(10):
                        self.configure(manager, control, registry)
                self.assertIs(getattr(manager, "_" + kind + "_link"), owner)
                self.assertIs(getattr(control, kind + "_link"), owner)
                self.assertFalse(getattr(control, kind + "_enabled"))
                self.assertEqual(len(handle.close_threads), 1)
                handle.mode = "ok"
                owner.close()
                self.assertTrue(owner.wait_closed(1))
                self.configure(manager, control, registry)
                replacement = getattr(manager, "_" + kind + "_link")
                self.assertIsNot(replacement, owner)
                self.owners.append(weakref.ref(replacement))


if __name__ == "__main__":
    unittest.main()
