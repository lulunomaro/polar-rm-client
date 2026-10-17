"""Pure generation and restoration-state contracts for profile transactions."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.configuration.pending import (
    CompletionAction, ConfigurationPhase, PendingConfigurationChange,
)
from rm_client.configuration.registry import RegistrySnapshot
from rm_client.configuration.transactions import (
    ConfigurationRestorePoint, RuntimeRestorePending, rollback_configuration_transaction,
)
from rm_client.model import build_default_registry


class PendingConfigurationTests(unittest.TestCase):
    def pending(self, phase=ConfigurationPhase.APPLYING):
        snapshot = RegistrySnapshot.capture(build_default_registry())
        return PendingConfigurationChange(4, phase,
            ConfigurationRestorePoint({}, {}, True, "scenario"), snapshot, snapshot)

    def test_only_matching_generation_commits(self):
        pending = self.pending()
        self.assertEqual(pending.completion_action(3, succeeded=True, final=False), CompletionAction.IGNORE)
        self.assertEqual(pending.completion_action(4, succeeded=True, final=False), CompletionAction.COMMIT)
        self.assertEqual(pending.completion_action(5, succeeded=True, final=False), CompletionAction.ROLLBACK)

    def test_failure_and_restore_have_distinct_terminal_meanings(self):
        self.assertEqual(self.pending().completion_action(4, succeeded=False, final=False), CompletionAction.ROLLBACK)
        pending = self.pending(ConfigurationPhase.RESTORING)
        self.assertEqual(pending.completion_action(4, succeeded=True, final=False), CompletionAction.RESTORED)
        self.assertEqual(pending.completion_action(4, succeeded=False, final=False), CompletionAction.RESTORE_FAILED)

    def test_final_close_cannot_restart_a_profile(self):
        for phase in ConfigurationPhase:
            self.assertEqual(self.pending(phase).completion_action(6, succeeded=False, final=True), CompletionAction.CANCEL)

    def test_pending_runtime_does_not_claim_restoration_complete(self):
        events = []
        error = rollback_configuration_transaction(self.pending().original,
            cause=OSError("activation failed"), restore_runtime=lambda: RuntimeRestorePending(5),
            fail_closed=lambda: events.append("locked"))
        self.assertTrue(error.restore_pending)
        self.assertFalse(error.restored)
        self.assertEqual(error.restore_errors, ())
        self.assertEqual(events, ["locked"])
        self.assertIn("restoration pending", str(error))


if __name__ == "__main__":
    unittest.main()
