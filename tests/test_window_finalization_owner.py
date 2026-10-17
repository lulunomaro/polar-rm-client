"""Explicit retirement owner: reentry, deferred resolution and diagnostics."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.window_lifecycle import FinalContextPorts, OwnerAction, WindowLifecycleController


class WindowFinalizationOwnerTests(unittest.TestCase):
    def setUp(self):
        self.owner = WindowLifecycleController(None)
        self.context = FinalContextPorts(Mock(return_value=True), Mock(), Mock(return_value=()), Mock(return_value=OwnerAction("GL done", Mock())))

    def test_reentrant_retirement_is_sealed_before_first_callback(self):
        nested = []
        def prepare():
            self.assertTrue(self.owner.finalized)
            nested.append(self.owner.finalize(prepare=lambda: (), context=self.context))
        self.assertEqual(self.owner.finalize(prepare=lambda: (OwnerAction("prepare", prepare),), context=self.context), ())
        self.assertEqual(nested, [None])
        self.context.enter.assert_called_once()
        self.context.leave.assert_called_once()

    def test_resource_resolution_is_after_entry_and_failure_still_leaves(self):
        calls = []
        def unavailable():
            calls.append("resolve")
            raise AttributeError("resource missing")
        context = FinalContextPorts(lambda: True, lambda: calls.append("enter"), unavailable,
                                    lambda: OwnerAction("GL done", lambda: calls.append("leave")))
        failures = self.owner.finalize(prepare=lambda: (), context=context)
        self.assertEqual(calls, ["enter", "resolve", "leave"])
        self.assertEqual(failures[0].summary, "GL context: AttributeError: resource missing")
        self.assertEqual(self.owner.finalization_failures, failures)

    def test_absent_context_never_resolves_gpu_resources(self):
        self.context.available.return_value = False
        self.assertEqual(self.owner.finalize(prepare=lambda: (), context=self.context), ())
        self.context.enter.assert_not_called()
        self.context.releases.assert_not_called()
        self.context.leave.assert_not_called()

    def test_base_exception_propagates_but_context_leave_and_seal_survive(self):
        self.context.releases.side_effect = KeyboardInterrupt("interrupt")
        with self.assertRaises(KeyboardInterrupt):
            self.owner.finalize(prepare=lambda: (), context=self.context)
        self.context.leave.assert_called_once()
        self.assertIsNone(self.owner.finalize(prepare=lambda: (), context=self.context))
        self.context.enter.assert_called_once()


if __name__ == "__main__":
    unittest.main()
