"""Failed profile quiescence clears pending before existing stopped rollback."""
from copy import deepcopy
from pathlib import Path
import sys
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_configuration_transactions as configuration_tests


class WindowConfigurationQuiescenceTests(unittest.TestCase):
    setUp = configuration_tests.WindowConfigurationTransactionTests.setUp
    close_window = configuration_tests.WindowConfigurationTransactionTests.close_window

    def test_failed_release_enters_existing_rollback_with_no_pending_input(self):
        window = self.window
        previous = deepcopy(window.current_config_data())
        candidate = deepcopy(previous)
        candidate["modules"]["AllianceProfile"]["settings"]["Side"] = "Blue"
        original_restore = window.restore_configuration_runtime
        original_request = window._request_registry_runtime
        restored = []
        requested = []
        def release(*args):
            window.control._dirty = True
            raise RuntimeError("release rejected")
        def restore(point):
            self.assertFalse(window.control._dirty)
            restored.append(point)
            return original_restore(point)
        def request(*, start=None):
            requested.append(start)
            return original_request(start=start)
        with (
            patch.object(window.control, "release_all", side_effect=release),
            patch.object(window, "restore_configuration_runtime", side_effect=restore),
            patch.object(window, "_request_registry_runtime", side_effect=request),
            patch.object(window.backend, "arm_match_control") as arm,
            patch.object(window, "toast") as toast,
        ):
            window.apply_config_data(candidate)
            self.assertTrue(window.configuration_change_pending)
            configuration_tests.settle_configuration(window)
        self.assertEqual(len(restored), 1)
        self.assertEqual(requested, [False])
        self.assertEqual(window.current_config_data(), previous)
        self.assertFalse(window.configuration_change_pending)
        self.assertFalse(window.control._dirty)
        self.assertNotEqual(window.backend.safety.control_state.value, "ARMED")
        self.assertTrue(window.backend.safety.estop_active)
        arm.assert_not_called()
        self.assertTrue(any("release rejected" in call.args[0] for call in toast.call_args_list))


if __name__ == "__main__":
    unittest.main()
