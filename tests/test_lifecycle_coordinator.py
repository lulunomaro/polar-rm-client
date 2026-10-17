"""Event-controlled ownership tests; no GUI, drivers, sleeps, or network needed."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.lifecycle import LifecycleCoordinator, LifecyclePhase, TeardownTask


class LifecycleCoordinatorTests(unittest.TestCase):
    def blocked_task(self, name="driver"):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def close():
            calls.append(threading.get_ident())
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release driver")
        self.addCleanup(release.set)
        return TeardownTask(name, close), entered, release, calls

    def test_worker_never_completes_on_owner_or_starts_replacement(self):
        coordinator = LifecycleCoordinator[str]()
        task, entered, release, calls = self.blocked_task()
        generation = coordinator.request("new scenario", [task])
        self.assertTrue(entered.wait(1))
        self.assertIsNone(coordinator.poll())
        self.assertTrue(coordinator.pending)
        self.assertNotEqual(calls[0], threading.get_ident())
        release.set()
        result = coordinator.wait(1)
        self.assertEqual((result.generation, result.target), (generation, "new scenario"))
        self.assertTrue(result.succeeded)
        self.assertIsNone(coordinator.poll())

    def test_repeated_requests_coalesce_one_owner_and_latest_target(self):
        coordinator = LifecycleCoordinator[int]()
        task, entered, release, calls = self.blocked_task()
        coordinator.request(0, [task])
        self.assertTrue(entered.wait(1))
        for target in range(1, 1001):
            generation = coordinator.request(target, [task])
        self.assertEqual(len(calls), 1)
        self.assertEqual(coordinator.snapshot().pending_resources, ("driver",))
        release.set()
        result = coordinator.wait(1)
        self.assertEqual((result.generation, result.target), (generation, 1000))

    def test_one_stuck_driver_does_not_prevent_other_resource_cleanup(self):
        coordinator = LifecycleCoordinator[str]()
        task, entered, release, _ = self.blocked_task()
        closed = threading.Event()
        coordinator.request("next", [task, TeardownTask("other", closed.set)])
        self.assertTrue(entered.wait(1))
        self.assertTrue(closed.wait(1))
        self.assertIsNone(coordinator.poll())
        release.set()
        self.assertTrue(coordinator.wait(1).succeeded)

    def test_timeout_retains_owner_and_never_claims_completion(self):
        now = [1.0]
        coordinator = LifecycleCoordinator[str](warning_after_s=3, clock=lambda: now[0])
        task, entered, release, calls = self.blocked_task()
        coordinator.request("first", [task])
        self.assertTrue(entered.wait(1))
        now[0] = 10
        self.assertIsNone(coordinator.poll())
        self.assertEqual(coordinator.snapshot().phase, LifecyclePhase.BLOCKED)
        self.assertTrue(coordinator.pending)
        coordinator.request("latest", [task])
        self.assertIsNone(coordinator.wait(0))
        self.assertEqual(len(calls), 1)
        release.set()
        self.assertEqual(coordinator.wait(1).target, "latest")

    def test_final_close_overrides_pending_restart_and_seals_admission(self):
        coordinator = LifecycleCoordinator[str]()
        task, entered, release, calls = self.blocked_task()
        coordinator.request("restart", [task])
        self.assertTrue(entered.wait(1))
        generation = coordinator.request("exit", final=True)
        with self.assertRaisesRegex(RuntimeError, "replacement is forbidden"):
            coordinator.request("late restart")
        release.set()
        result = coordinator.wait(1)
        self.assertEqual((result.target, result.final), ("exit", True))
        self.assertEqual(coordinator.snapshot().phase, LifecyclePhase.CLOSED)
        self.assertEqual(coordinator.request("exit", [task], final=True), generation)
        self.assertEqual(len(calls), 1)

    def test_final_close_can_add_new_resources_without_reclosing_existing(self):
        coordinator = LifecycleCoordinator[str](max_resources=2)
        task, entered, release, calls = self.blocked_task()
        closed = threading.Event()
        coordinator.request("restart", [task])
        self.assertTrue(entered.wait(1))
        coordinator.request("exit", [task, TeardownTask("camera", closed.set)], final=True)
        self.assertTrue(closed.wait(1))
        self.assertEqual(len(calls), 1)
        release.set()
        self.assertTrue(coordinator.wait(1).succeeded)

    def test_failure_is_reported_without_skipping_other_resources(self):
        coordinator = LifecycleCoordinator[str]()
        closed = threading.Event()
        def fail():
            raise OSError("driver error")
        coordinator.request("next", [TeardownTask("bad", fail), TeardownTask("good", closed.set)])
        result = coordinator.wait(1)
        self.assertTrue(closed.is_set())
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failures[0].resource, "bad")
        self.assertIn("driver error", result.failures[0].error)
        self.assertEqual(coordinator.snapshot().phase, LifecyclePhase.FAILED)

    def test_resource_limit_rejects_atomically_before_admitting_work(self):
        coordinator = LifecycleCoordinator[str](max_resources=1)
        calls = []
        with self.assertRaisesRegex(ValueError, "Too many"):
            coordinator.request("next", [TeardownTask("a", lambda: calls.append("a")),
                                         TeardownTask("b", lambda: calls.append("b"))])
        self.assertFalse(calls)
        self.assertFalse(coordinator.pending)
        self.assertEqual(coordinator.snapshot().generation, 0)

    def test_duplicate_resource_names_reject_before_admission(self):
        coordinator = LifecycleCoordinator[str]()
        with self.assertRaises(ValueError):
            coordinator.request("next", [TeardownTask("a", lambda: None)] * 2)
        self.assertEqual(coordinator.snapshot().generation, 0)

    def test_thread_start_failure_settles_as_failure_without_hanging(self):
        coordinator = LifecycleCoordinator[str]()
        with patch("rm_client.lifecycle.threading.Thread.start", side_effect=RuntimeError("no threads")):
            coordinator.request("next", [TeardownTask("driver", lambda: None)])
        result = coordinator.wait(0)
        self.assertFalse(result.succeeded)
        self.assertIn("no threads", result.failures[0].error)

    def test_requests_polls_and_waits_are_owner_thread_only(self):
        coordinator = LifecycleCoordinator[str]()
        errors = []
        def foreign_thread():
            for operation in (lambda: coordinator.request("next"), coordinator.poll,
                              coordinator.wait, coordinator.snapshot):
                try:
                    operation()
                except RuntimeError as exc:
                    errors.append(str(exc))
        worker = threading.Thread(target=foreign_thread)
        worker.start()
        worker.join(1)
        self.assertEqual(len(errors), 4)
        self.assertEqual(coordinator.snapshot().generation, 0)

    def test_no_resource_transition_completes_only_when_owner_polls(self):
        coordinator = LifecycleCoordinator[str]()
        coordinator.request("nothing to close")
        self.assertTrue(coordinator.pending)
        self.assertTrue(coordinator.poll().succeeded)
        self.assertFalse(coordinator.pending)

    def test_final_retry_replays_only_failed_tasks_and_preserves_seal(self):
        coordinator = LifecycleCoordinator()
        calls = []
        fail = [True]
        def close_bad():
            calls.append("bad")
            if fail[0]:
                raise OSError("still open")
        coordinator.request("final", (TeardownTask("good", lambda: calls.append("good")),
                                      TeardownTask("bad", close_bad)), final=True)
        original = coordinator.wait()
        self.assertFalse(original.succeeded)
        fail[0] = False
        generation = coordinator.retry_failed()
        result = coordinator.wait()
        self.assertTrue(result.succeeded)
        self.assertEqual(result.generation, generation)
        self.assertGreater(generation, original.generation)
        self.assertFalse(original.succeeded)
        self.assertEqual(calls.count("good"), 1)
        self.assertEqual(calls.count("bad"), 2)
        with self.assertRaises(RuntimeError):
            coordinator.request("replacement")
        with self.assertRaises(ValueError):
            coordinator.retry_failed()

    def test_pending_final_retry_cannot_duplicate_running_owner(self):
        release = threading.Event()
        coordinator = LifecycleCoordinator()
        coordinator.request("final", (TeardownTask("blocked", lambda: release.wait(2)),), final=True)
        try:
            with self.assertRaises(RuntimeError):
                coordinator.retry_failed()
        finally:
            release.set()
            coordinator.wait()

    def test_retry_preparation_runs_on_owner_and_close_stays_on_worker(self):
        owner = threading.get_ident()
        attempts = []
        preparations = []
        def close():
            attempts.append(threading.get_ident())
            if len(attempts) == 1:
                raise OSError("first close failed")
        coordinator = LifecycleCoordinator()
        coordinator.request("final", (TeardownTask("resource", close,
            lambda: preparations.append(threading.get_ident())),), final=True)
        coordinator.wait()
        coordinator.retry_failed()
        self.assertTrue(coordinator.wait().succeeded)
        self.assertEqual(preparations, [owner])
        self.assertTrue(all(thread != owner for thread in attempts))

    def test_failed_retry_preparation_does_not_start_another_close(self):
        calls = []
        def close():
            calls.append(True)
            raise OSError("close failed")
        def prepare():
            raise OSError("retry preparation failed")
        coordinator = LifecycleCoordinator()
        coordinator.request("final", (TeardownTask("resource", close, prepare),), final=True)
        coordinator.wait()
        coordinator.retry_failed()
        result = coordinator.wait()
        self.assertFalse(result.succeeded)
        self.assertIn("retry preparation failed", result.failures[0].error)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
