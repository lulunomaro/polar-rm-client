"""Window lifecycle policy does not create Qt objects or device connections."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.lifecycle import LifecycleCompletion, LifecyclePhase, LifecycleSnapshot, TeardownFailure, TeardownTask
from rm_client.backend.lifecycle import AuditCloseStatus, ShutdownCompletion, ShutdownRequest
from rm_client.ui.polar.window_lifecycle import OwnerAction, WindowLifecycleController, run_owner_actions


def snapshot(phase=LifecyclePhase.IDLE, *, final=False, failures=(), resources=()):
    return LifecycleSnapshot(phase, 1, resources, failures, 0, final)


class WindowLifecycleControllerTests(unittest.TestCase):
    def setUp(self):
        self.backend = NS(lifecycle_pending=False, lifecycle_closed=False,
                          lifecycle_snapshot=snapshot(), poll_lifecycle=Mock(return_value=None),
                          request_shutdown=Mock(return_value=1), shutdown_completion=None,
                          retry_shutdown=Mock(return_value=2), lock_control=Mock())
        self.controller = WindowLifecycleController(self.backend)

    def test_idle_allows_normal_runtime(self):
        update = self.controller.poll(Mock())
        self.assertTrue(update.runtime_allowed)
        self.assertFalse(update.schedule_close)

    def test_pending_transition_suppresses_runtime_without_closing(self):
        self.backend.lifecycle_pending = True
        self.backend.lifecycle_snapshot = snapshot(LifecyclePhase.STOPPING)
        self.assertFalse(self.controller.poll(Mock()).runtime_allowed)
        self.assertFalse(self.controller.closing)

    def test_prepare_actions_run_before_request_and_failures_do_not_skip_request(self):
        calls = []
        def broken():
            calls.append("first")
            raise TypeError("bad input")
        self.backend.request_shutdown.side_effect = lambda **_: calls.append("request")
        self.controller.request_close(prepare=(OwnerAction("first", broken), OwnerAction("second", lambda: calls.append("second"))), tasks=())
        self.assertEqual(calls, ["first", "second", "request"])
        self.assertEqual(self.controller.owner_failures[0].error_type, "TypeError")
        self.assertTrue(self.controller.closing)

    def test_repeated_close_does_not_restart_or_repeat_teardown(self):
        prepare = Mock()
        tasks = (TeardownTask("camera", Mock()),)
        for _ in range(2):
            self.controller.request_close(prepare=(OwnerAction("prepare", prepare),), tasks=tasks)
        prepare.assert_called_once()
        self.backend.request_shutdown.assert_called_once_with(teardown_tasks=tasks)

    def test_failed_request_never_allows_close_or_runtime(self):
        self.backend.request_shutdown.side_effect = RuntimeError("request rejected")
        self.controller.request_close(prepare=(), tasks=())
        self.backend.lifecycle_closed = True
        update = self.controller.poll(Mock())
        self.assertFalse(update.schedule_close)
        self.assertFalse(update.runtime_allowed)
        self.assertIn("request rejected", update.notification)

    def test_blocked_resource_stays_pending_and_notice_is_not_repeated(self):
        self.controller.request_close(prepare=(), tasks=())
        self.backend.lifecycle_pending = True
        self.backend.lifecycle_snapshot = snapshot(LifecyclePhase.BLOCKED, final=True, resources=("camera",))
        first = self.controller.poll(Mock())
        second = self.controller.poll(Mock())
        self.assertFalse(first.schedule_close)
        self.assertIn("camera", first.notification)
        self.assertEqual(second.notification, "")
        self.assertIn("camera", self.controller.status_text)

    def test_failed_native_close_never_uses_closed_flag_as_success(self):
        self.controller.request_close(prepare=(), tasks=())
        self.backend.lifecycle_closed = True
        self.backend.lifecycle_snapshot = snapshot(LifecyclePhase.FAILED, final=True,
            failures=(TeardownFailure("camera", "native handle retained"),))
        update = self.controller.poll(Mock())
        self.assertFalse(update.schedule_close)
        self.assertFalse(self.controller.ready_to_close)
        self.assertTrue(update.failed)
        self.assertIn("native handle retained", update.notification)

    def test_successful_final_completion_schedules_exactly_one_second_close(self):
        self.controller.request_close(prepare=(), tasks=())
        self.backend.lifecycle_closed = True
        self.backend.lifecycle_snapshot = snapshot(LifecyclePhase.CLOSED, final=True)
        self.backend.shutdown_completion = ShutdownCompletion(1, ShutdownRequest(), True, (), True, AuditCloseStatus(True, True, 0, 0, 0), 1)
        self.assertTrue(self.controller.poll(Mock()).schedule_close)
        self.assertFalse(self.controller.poll(Mock()).schedule_close)
        self.assertTrue(self.controller.ready_to_close)

    def test_profile_rollback_requested_by_completion_keeps_runtime_blocked(self):
        completion = LifecycleCompletion(1, "profile", False, ())
        self.backend.poll_lifecycle.return_value = completion
        def handle(value):
            self.assertIs(value, completion)
            self.backend.lifecycle_pending = True
            self.backend.lifecycle_snapshot = snapshot(LifecyclePhase.STOPPING)
            return True
        update = self.controller.poll(handle)
        self.assertFalse(update.runtime_allowed)
        self.assertEqual(update.notification, "")

    def test_unhandled_completion_reports_ready_once(self):
        self.backend.poll_lifecycle.return_value = LifecycleCompletion(1, "scenario", False, ())
        handler = Mock(return_value=False)
        update = self.controller.poll(handler)
        self.assertTrue(update.runtime_allowed)
        self.assertEqual(update.notification, "Scenario ready")
        handler.assert_called_once()

    def test_drained_audit_loss_is_visible_but_does_not_claim_native_ownership(self):
        self.controller.request_close(prepare=(), tasks=())
        audit = AuditCloseStatus(True, False, 0, 3, 2)
        self.backend.shutdown_completion = ShutdownCompletion(1, ShutdownRequest(), True, (), True, audit, 1)
        update = self.controller.poll(Mock())
        self.assertTrue(update.schedule_close)
        self.assertTrue(update.failed)
        self.assertIn("Dropped records: 3", update.completion_warning)
        self.assertIn("write errors: 2", update.completion_warning)
        self.assertEqual(self.controller.poll(Mock()).completion_warning, "")

    def test_undrained_audit_cannot_close_even_after_resources_release(self):
        self.controller.request_close(prepare=(), tasks=())
        audit = AuditCloseStatus(False, True, 1, 0, 0)
        self.backend.shutdown_completion = ShutdownCompletion(1, ShutdownRequest(), True, (), True, audit, 1)
        self.assertFalse(self.controller.poll(Mock()).schedule_close)

    def test_explicit_retry_only_replays_completed_failed_cleanup(self):
        self.controller.request_close(prepare=(), tasks=())
        result = ShutdownCompletion(1, ShutdownRequest(), True, (), False, AuditCloseStatus(True, True, 0, 0, 0), 1)
        self.backend.shutdown_completion = result
        self.backend.lifecycle_pending = True
        self.assertFalse(self.controller.retry_close())
        self.backend.retry_shutdown.assert_not_called()
        self.backend.lifecycle_pending = False
        self.assertTrue(self.controller.retry_close())
        self.backend.retry_shutdown.assert_called_once()

    def test_poll_programmer_fault_is_typed_observable_and_fail_closed(self):
        self.backend.poll_lifecycle.side_effect = TypeError("broken completion")
        with self.assertLogs("rm_client.ui.polar.window_lifecycle", level="ERROR") as logs:
            first = self.controller.poll(Mock())
            second = self.controller.poll(Mock())
        self.assertEqual(len(logs.output), 1)
        self.assertIn("TypeError", logs.output[0])
        self.assertFalse(first.runtime_allowed)
        self.assertEqual(second.notification, "")
        self.assertEqual(self.controller.poll_failures, 2)
        self.assertEqual(self.controller.last_poll_failure.error_type, "TypeError")
        self.assertEqual(self.backend.lock_control.call_count, 2)
        self.backend.poll_lifecycle.side_effect = None
        self.backend.poll_lifecycle.return_value = None
        self.assertTrue(self.controller.poll(Mock()).runtime_allowed)

    def test_retry_fault_stays_observable_without_escaping_qt_close_event(self):
        self.controller.request_close(prepare=(), tasks=())
        self.backend.shutdown_completion = ShutdownCompletion(
            1, ShutdownRequest(), True, (), False, AuditCloseStatus(True, True, 0, 0, 0), 1)
        self.backend.retry_shutdown.side_effect = TypeError("retry defect")
        with self.assertLogs("rm_client.ui.polar.window_lifecycle", level="ERROR"):
            self.assertFalse(self.controller.retry_close())
        self.assertEqual(self.controller.owner_failures[-1].error_type, "TypeError")
        self.assertFalse(self.controller.poll(Mock()).schedule_close)

    def test_poll_fault_revokes_stale_close_permission(self):
        self.controller.ready_to_close = True
        self.backend.poll_lifecycle.side_effect = TypeError("broken completion")
        with self.assertLogs("rm_client.ui.polar.window_lifecycle", level="ERROR"):
            self.controller.poll(Mock())
        self.assertFalse(self.controller.ready_to_close)

    def test_owner_cleanup_collects_all_independent_errors(self):
        actions = tuple(OwnerAction(name, Mock(side_effect=OSError(name))) for name in ("save", "GL", "done"))
        failures = run_owner_actions(actions)
        self.assertEqual([f.name for f in failures], ["save", "GL", "done"])
        for action in actions:
            action.run.assert_called_once()


if __name__ == "__main__": unittest.main()
