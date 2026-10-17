"""Pure profile identity/save ownership and interrupted publication contracts."""
from pathlib import Path
import math
import subprocess
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.configuration.pending import ProfileIdentity
from rm_client.configuration.profile_saves import ProfileSaveState
from rm_client.ui.polar.profile_save_fields import ProfileSaveField


class ProfileSaveStateTests(unittest.TestCase):
    def test_import_has_no_qt_window_or_native_transport_dependency(self):
        result = subprocess.run([sys.executable, "-c", """
import sys
import rm_client.configuration.profile_saves
assert not any(name.startswith(('PySide6', 'rm_client.ui', 'rm_client.backend'))
               for name in sys.modules), list(sys.modules)
"""], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_identity_is_resolved_once_and_keeps_exact_name(self):
        state = ProfileSaveState()
        name = Mock(return_value="  User Selected  ")
        identity = state.identity(name)
        self.assertEqual(identity, ProfileIdentity("  User Selected  "))
        self.assertIs(state.identity(name), identity)
        name.assert_called_once_with()

    def test_failed_initial_name_does_not_publish_identity(self):
        state = ProfileSaveState()
        failure = ValueError("selection unavailable")
        with self.assertRaises(ValueError) as caught:
            state.identity(Mock(side_effect=failure))
        self.assertIs(caught.exception, failure)
        self.assertIsNone(state.accepted)

    def test_acceptance_advances_generation_and_waits_to_retire_old_save(self):
        old = ProfileIdentity("old", 4)
        state = ProfileSaveState(old, "draft", True, old, 99., True, True)
        state.accept_profile("new", old)
        self.assertEqual(state.accepted, ProfileIdentity("new", 5))
        self.assertIsNone(state.draft)
        self.assertFalse(state.close_retry_required)
        self.assertTrue(state.pending)
        self.assertIs(state.pending_owner, old)
        self.assertTrue(state.error_reported)
        state.cancel_pending()
        self.assertFalse(state.pending)
        self.assertIsNone(state.pending_owner)
        self.assertEqual(state.due_at, 99.)

    def test_same_name_acceptance_still_retires_old_generation(self):
        old = ProfileIdentity("same", 2)
        state = ProfileSaveState(accepted=old, pending=True, pending_owner=old)
        state.accept_profile("same", old)
        self.assertIsNone(state.save_identity(state.accepted))
        self.assertFalse(state.pending)

    def test_absent_owner_defaults_to_accepted_identity(self):
        accepted = ProfileIdentity("current", 3)
        state = ProfileSaveState(accepted=accepted, pending=True)
        self.assertIs(state.save_identity(accepted), accepted)
        self.assertTrue(state.pending)

    def test_queue_publishes_flag_then_identity_then_clock(self):
        accepted = ProfileIdentity("current", 3)
        old = ProfileIdentity("old", 2)
        state = ProfileSaveState(pending_owner=old, due_at=99.)
        observations = []
        def identity():
            observations.append((state.pending, state.pending_owner, state.due_at))
            return accepted
        def clock():
            observations.append((state.pending, state.pending_owner, state.due_at))
            return 12.
        state.defer_save(identity, clock, .25)
        self.assertEqual(observations, [(True, old, 99.), (True, accepted, 99.)])
        self.assertEqual(state.due_at, 12.25)

    def test_identity_failure_preserves_prior_owner_and_due_time(self):
        old = ProfileIdentity("old")
        state = ProfileSaveState(pending_owner=old, due_at=99.)
        failure = RuntimeError("identity unavailable")
        clock = Mock()
        with self.assertRaises(RuntimeError) as caught:
            state.defer_save(Mock(side_effect=failure), clock, .25)
        self.assertIs(caught.exception, failure)
        self.assertTrue(state.pending)
        self.assertIs(state.pending_owner, old)
        self.assertEqual(state.due_at, 99.)
        clock.assert_not_called()

    def test_clock_failure_keeps_published_owner_and_prior_deadline(self):
        accepted = ProfileIdentity("current")
        state = ProfileSaveState(due_at=99.)
        failure = RuntimeError("clock unavailable")
        with self.assertRaises(RuntimeError) as caught:
            state.defer_save(lambda: accepted, Mock(side_effect=failure), 5.)
        self.assertIs(caught.exception, failure)
        self.assertTrue(state.pending)
        self.assertIs(state.pending_owner, accepted)
        self.assertEqual(state.due_at, 99.)

    def test_repeated_queue_replaces_owner_and_deadline(self):
        state = ProfileSaveState()
        for generation, when in ((1, 2.), (2, 4.)):
            identity = ProfileIdentity("same", generation)
            state.defer_save(lambda: identity, lambda: when, .25)
        self.assertEqual(state.pending_owner, ProfileIdentity("same", 2))
        self.assertEqual(state.due_at, 4.25)

    def test_identity_query_reentry_preserves_outer_publication_order(self):
        state = ProfileSaveState()
        outer, nested = ProfileIdentity("outer"), ProfileIdentity("nested")
        def identity():
            state.defer_save(lambda: nested, lambda: 8., .25)
            return outer
        state.defer_save(identity, lambda: 10., .25)
        self.assertIs(state.pending_owner, outer)
        self.assertEqual(state.due_at, 10.25)

    def test_clock_query_reentry_keeps_nested_owner_and_outer_deadline(self):
        state = ProfileSaveState()
        outer, nested = ProfileIdentity("outer"), ProfileIdentity("nested")
        def clock():
            state.defer_save(lambda: nested, lambda: 8., .25)
            return 10.
        state.defer_save(lambda: outer, clock, .25)
        self.assertIs(state.pending_owner, nested)
        self.assertEqual(state.due_at, 10.25)

    def test_clock_reentry_that_accepts_profile_keeps_its_pending_retirement(self):
        previous = ProfileIdentity("outer")
        state = ProfileSaveState(accepted=previous)
        def clock():
            state.accept_profile("new", previous)
            state.cancel_pending()
            return 10.
        state.defer_save(lambda: previous, clock, .25)
        self.assertEqual(state.accepted, ProfileIdentity("new", 1))
        self.assertFalse(state.pending)
        self.assertIsNone(state.pending_owner)
        self.assertEqual(state.due_at, 10.25)

    def test_stale_close_discards_only_pending_save(self):
        accepted = ProfileIdentity("new", 3)
        state = ProfileSaveState(accepted, None, True, ProfileIdentity("old", 2), 8.)
        self.assertIsNone(state.close_intent(None, accepted))
        self.assertFalse(state.close_requires_save)
        self.assertFalse(state.close_retry_required)
        self.assertEqual(state.due_at, 8.)

    def test_close_draft_has_priority_over_stale_pending_owner(self):
        accepted = ProfileIdentity("old")
        state = ProfileSaveState(accepted, "draft", True, ProfileIdentity("stale", 1))
        intent = state.close_intent("draft", accepted)
        self.assertEqual((intent.profile, intent.explicit), ("draft", True))
        self.assertTrue(state.close_retry_required)
        self.assertTrue(state.pending)

    def test_close_uses_snapshot_before_reentrant_identity_read(self):
        accepted = ProfileIdentity("accepted")
        state = ProfileSaveState(accepted=accepted, draft="replacement")
        intent = state.close_intent("captured", accepted)
        self.assertEqual(intent.profile, "captured")
        self.assertTrue(intent.explicit)
        self.assertEqual(state.draft, "replacement")

    def test_empty_draft_still_requires_explicit_close_save(self):
        accepted = ProfileIdentity("accepted")
        state = ProfileSaveState(accepted=accepted, draft="")
        self.assertTrue(state.close_requires_save)
        self.assertTrue(state.automatic_save_blocked)
        intent = state.close_intent("", accepted)
        self.assertEqual((intent.profile, intent.explicit), ("accepted", True))

    def test_success_retires_retry_state_but_not_accepted_identity_or_deadline(self):
        accepted = ProfileIdentity("accepted")
        state = ProfileSaveState(accepted, None, True, accepted, 8., True, True)
        state.complete_save()
        self.assertFalse(state.pending)
        self.assertIsNone(state.pending_owner)
        self.assertFalse(state.error_reported)
        self.assertFalse(state.close_retry_required)
        self.assertIs(state.accepted, accepted)
        self.assertEqual(state.due_at, 8.)

    def test_automatic_save_blocks_only_draft_or_explicit_close_retry(self):
        state = ProfileSaveState(error_reported=True)
        self.assertFalse(state.automatic_save_blocked)
        state.close_retry_required = True
        self.assertTrue(state.automatic_save_blocked)
        state.close_retry_required = False
        state.begin_draft("draft")
        self.assertTrue(state.automatic_save_blocked)

    def test_due_comparison_retains_boundary_and_nonfinite_behavior(self):
        state = ProfileSaveState(due_at=10.)
        self.assertFalse(state.is_due(9.))
        self.assertTrue(state.is_due(10.))
        self.assertTrue(state.is_due(math.nan))
        state.due_at = math.nan
        self.assertTrue(state.is_due(0.))

    def test_compatibility_aliases_are_fixed_and_bidirectional(self):
        class Host:
            pending = ProfileSaveField("pending")
            identity = ProfileSaveField("accepted")
        host = Host()
        host._profile_saves = ProfileSaveState()
        host.pending = True
        self.assertTrue(host._profile_saves.pending)
        accepted = ProfileIdentity("profile")
        host._profile_saves.accepted = accepted
        self.assertIs(host.identity, accepted)
        with self.assertRaises(ValueError):
            ProfileSaveField("window")


if __name__ == "__main__":
    unittest.main()
