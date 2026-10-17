"""Input observer faults stay observable without delaying authority changes."""
from copy import copy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.backend.safety import ControlState, OfficialSafety


class SafetyObserverDiagnosticsTests(unittest.TestCase):
    def test_lock_revokes_authority_before_failed_real_hook_and_records_stack(self):
        safety = OfficialSafety()
        safety.begin_qualification({"core_kmc"})
        generation = safety.control_generation
        observed = []

        def broken_input_observer(reason):
            observed.append((reason, safety.control_state, safety._qualification))
            private_payload = "PRIVATE_OBSERVER_PAYLOAD"
            raise TypeError(private_payload)

        safety.input_clear_hook = broken_input_observer
        safety.lock_control("connection epoch changed")
        self.assertEqual(observed, [("connection epoch changed", ControlState.LOCKED, None)])
        self.assertEqual(safety.control_generation, generation + 1)
        self.assertEqual(safety.input_clear_failure_count, 1)
        failure = safety.input_clear_failures[-1]
        self.assertEqual(failure.reason, "connection epoch changed")
        self.assertEqual(failure.exception_type, "builtins.TypeError")
        self.assertIn("broken_input_observer", [frame.function for frame in failure.frames])
        self.assertTrue(all(frame.line_number > 0 for frame in failure.frames))
        # No retained exception/traceback object, locals, message, or source text.
        self.assertNotIn("PRIVATE_OBSERVER_PAYLOAD", str(asdict(failure)))

    def test_estop_failure_never_undoes_latch_or_escapes(self):
        safety = OfficialSafety()
        safety.arm_match({"core_kmc"})
        safety.input_clear_hook = lambda _: (_ for _ in ()).throw(OSError("observer failed"))
        safety.trigger_estop()
        self.assertTrue(safety.estop_active)
        self.assertEqual(safety.control_state, ControlState.ESTOP)
        self.assertFalse(safety._authorized_capabilities)
        self.assertEqual(safety.input_clear_failures[-1].reason, "E-stop triggered")

    def test_history_and_frame_records_are_bounded(self):
        safety = OfficialSafety()

        def deep_failure(depth):
            if depth:
                return deep_failure(depth - 1)
            raise ValueError("ignored payload")

        safety.input_clear_hook = lambda _: deep_failure(30)
        for index in range(20):
            safety.lock_control(f"reason {index}")
        self.assertEqual(safety.input_clear_failure_count, 20)
        self.assertEqual(len(safety.input_clear_failures), 8)
        self.assertEqual([item.reason for item in safety.input_clear_failures],
                         [f"reason {index}" for index in range(12, 20)])
        self.assertTrue(all(len(item.frames) == 12 for item in safety.input_clear_failures))
        self.assertTrue(all(item.trace_truncated for item in safety.input_clear_failures))
        safety.lock_control("r" * 10000)
        self.assertEqual(len(safety.input_clear_failures[-1].reason), 256)

    def test_success_and_absent_hook_do_not_erase_failure_evidence(self):
        safety = OfficialSafety()
        safety.input_clear_hook = lambda _: (_ for _ in ()).throw(RuntimeError("broken"))
        safety.lock_control("failed clear")
        original = safety.input_clear_failures
        calls = []
        safety.input_clear_hook = calls.append
        safety.lock_control("healthy clear")
        safety.input_clear_hook = None
        safety.lock_control("no observer")
        self.assertEqual(calls, ["healthy clear"])
        self.assertEqual(safety.input_clear_failure_count, 1)
        self.assertIs(safety.input_clear_failures, original)

    def test_diagnostics_do_not_call_logging_or_read_source_files(self):
        safety = OfficialSafety()
        safety.input_clear_hook = lambda _: (_ for _ in ()).throw(TypeError("broken"))
        with patch("logging.Logger._log", side_effect=AssertionError("blocking log handler")), \
             patch("builtins.open", side_effect=AssertionError("source file read")):
            safety.lock_control("closed")
        self.assertEqual(safety.input_clear_failure_count, 1)

    def test_worker_policy_snapshot_does_not_mutate_original_diagnostics(self):
        safety = OfficialSafety()
        snapshot = copy(safety)
        snapshot.input_clear_hook = lambda _: (_ for _ in ()).throw(TypeError("broken"))
        snapshot.lock_control("snapshot only")
        self.assertEqual(safety.input_clear_failures, ())
        self.assertEqual(safety.input_clear_failure_count, 0)
        self.assertEqual(snapshot.input_clear_failure_count, 1)


if __name__ == "__main__":
    unittest.main()
