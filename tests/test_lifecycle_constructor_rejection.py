"""Rejected cleanup construction settles as retryable failure, never a ghost owner."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.lifecycle import LifecycleCoordinator, TeardownTask
from tests import test_backend_lifecycle_controller as backend_fixture


class LifecycleConstructorRejectionTests(unittest.TestCase):
    def test_each_constructor_rejection_keeps_other_tasks_and_retry_inventory(self):
        real_thread = threading.Thread
        names = ("first", "middle", "last")
        for rejected in names:
            with self.subTest(rejected=rejected):
                coordinator = LifecycleCoordinator(max_resources=3)
                calls = []
                error = OSError("constructor rejected: " + rejected)
                tasks = tuple(TeardownTask(name, lambda name=name: calls.append(name)) for name in names)

                def construct(*args, **kwargs):
                    if kwargs.get("name") == "lifecycle-" + rejected:
                        raise error
                    return real_thread(*args, **kwargs)

                with patch.object(threading, "Thread", construct):
                    coordinator.request("shutdown", tasks, final=True)
                result = coordinator.wait(1)
                self.assertIsNotNone(result)
                self.assertFalse(result.succeeded)
                self.assertEqual([failure.resource for failure in result.failures], [rejected])
                self.assertIn(str(error), result.failures[0].error)
                self.assertCountEqual(calls, [name for name in names if name != rejected])
                self.assertEqual(coordinator.snapshot().pending_resources, ())
                coordinator.retry_failed()
                retry = coordinator.wait(1)
                self.assertTrue(retry.succeeded)
                self.assertCountEqual(calls, names)
                self.assertFalse(result.succeeded)  # The first result is immutable evidence.

    def test_constructor_failure_cannot_complete_an_earlier_live_owner(self):
        coordinator = LifecycleCoordinator(max_resources=2)
        entered, release = threading.Event(), threading.Event()
        real_thread = threading.Thread
        workers = []

        def close():
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test must release inert close")

        def construct(*args, **kwargs):
            if kwargs.get("name") == "lifecycle-rejected":
                raise OSError("second constructor rejected")
            worker = real_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        try:
            with patch.object(threading, "Thread", construct):
                coordinator.request("shutdown", (TeardownTask("live", close),
                    TeardownTask("rejected", lambda: None)), final=True)
            self.assertTrue(entered.wait(1))
            self.assertIsNone(coordinator.poll())
            self.assertEqual(coordinator.snapshot().pending_resources, ("live",))
            self.assertEqual([failure.resource for failure in coordinator.snapshot().failures], ["rejected"])
            with self.assertRaises(RuntimeError):
                coordinator.retry_failed()
        finally:
            release.set()
            for worker in workers:
                worker.join(2)
        result = coordinator.wait(1)
        self.assertIsNotNone(result)
        self.assertEqual([failure.resource for failure in result.failures], ["rejected"])

    def test_rejected_audit_constructor_can_retry_after_resources_are_released(self):
        fixture = backend_fixture.BackendLifecycleControllerTests()
        fixture.setUp()
        real_thread = threading.Thread

        def construct(*args, **kwargs):
            if kwargs.get("name") == "lifecycle-audit-log":
                raise OSError("audit constructor rejected")
            return real_thread(*args, **kwargs)

        with patch.object(threading, "Thread", construct):
            fixture.controller.request_shutdown(fixture.owners)
            first = fixture.controller.wait()
        self.assertTrue(first.resources_released)
        self.assertFalse(first.audit.drained)
        self.assertFalse(first.may_exit)
        self.assertEqual(fixture.audit.close_calls, 0)
        resource_events = tuple(fixture.events)
        fixture.controller.retry_shutdown()
        final = fixture.controller.wait()
        self.assertTrue(final.succeeded)
        self.assertTrue(final.may_exit)
        self.assertEqual(fixture.audit.close_calls, 1)
        self.assertEqual(tuple(fixture.events[:-1]), resource_events)
        self.assertIs(fixture.controller.initial_shutdown_completion, first)


if __name__ == "__main__":
    unittest.main()
