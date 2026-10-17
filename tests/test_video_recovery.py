"""Pure video recovery visibility and stale-intent policy."""
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.backend.video_source import VideoSourceFailure
from rm_client.lifecycle import LifecyclePhase, LifecycleSnapshot, TeardownFailure
from rm_client.ui.polar.video_recovery import VideoRetryToken, video_recovery_notice


class VideoRecoveryPolicyTests(unittest.TestCase):
    def source(self, **changes):
        # Explicit snapshot protocol fixture; real native-owner composition is
        # covered by the manager and actual-window suites.
        values = dict(requested_source='synthetic', active_source='', generation=4,
                      pending_resources=('Native 0x0310',), inflight_resources=(),
                      failures=(), close_failures=(VideoSourceFailure('Native pipe', 'OSError: retained'),),
                      retryable=True)
        values.update(changes)
        return NS(**values)

    def lifecycle(self, **changes):
        return replace(LifecycleSnapshot(LifecyclePhase.IDLE, 3, (), (), 0, False), **changes)

    def notice(self, source=None, lifecycle=None, **changes):
        values = dict(visible=True, lifecycle_pending=False,
                      configuration_pending=False, profile_pending=False)
        values.update(changes)
        return video_recovery_notice(source or self.source(), lifecycle or self.lifecycle(), **values)

    def test_retained_native_failure_is_visible_and_retryable_even_with_pending_owners(self):
        notice = self.notice()
        self.assertEqual(notice.retry_token, VideoRetryToken(4, 3))
        self.assertEqual(notice.pending_resources, ('Native 0x0310',))
        self.assertEqual(notice.errors, ('Native pipe: OSError: retained',))

    def test_live_cleanup_is_visible_but_has_no_retry_intent(self):
        notice = self.notice(self.source(inflight_resources=('Native 0x0310',), retryable=False))
        self.assertIsNone(notice.retry_token)
        self.assertEqual(notice.inflight_resources, ('Native 0x0310',))

    def test_admission_and_current_close_failures_both_survive_the_projection(self):
        notice = self.notice(self.source(failures=(VideoSourceFailure('UDP admission', 'RuntimeError: denied'),)))
        self.assertEqual(notice.errors, ('UDP admission: RuntimeError: denied', 'Native pipe: OSError: retained'))

    def test_healthy_source_has_no_notice_or_interaction(self):
        self.assertIsNone(self.notice(self.source(pending_resources=(), close_failures=(), retryable=False)))

    def test_transition_final_failure_and_hidden_surfaces_do_not_expose_retry(self):
        for guard in ('visible', 'lifecycle_pending', 'configuration_pending', 'profile_pending'):
            with self.subTest(guard=guard):
                self.assertIsNone(self.notice(**{guard: guard != 'visible'}))
        for lifecycle in (
            self.lifecycle(final=True), self.lifecycle(phase=LifecyclePhase.BLOCKED),
            self.lifecycle(phase=LifecyclePhase.FAILED),
            self.lifecycle(failures=(TeardownFailure('owner', 'retained'),)),
        ):
            with self.subTest(lifecycle=lifecycle):
                self.assertIsNone(self.notice(lifecycle=lifecycle))

    def test_both_source_and_runtime_epochs_change_the_retry_token(self):
        first = self.notice().retry_token
        self.assertNotEqual(first, self.notice(self.source(generation=6)).retry_token)
        self.assertNotEqual(first, self.notice(lifecycle=self.lifecycle(generation=5)).retry_token)


if __name__ == '__main__':
    unittest.main()
