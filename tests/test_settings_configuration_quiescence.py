"""Settings-page quiescence must clear stale input before stopped recovery."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_configuration_transactions as configuration_tests
from rm_client.configuration.transactions import ConfigurationApplyError
from rm_client.ui.settings_application import apply_registry_draft


class SettingsConfigurationQuiescenceTests(unittest.TestCase):
    setUp = configuration_tests.WindowConfigurationTransactionTests.setUp
    close_window = configuration_tests.WindowConfigurationTransactionTests.close_window

    def test_failed_settings_release_clears_pending_before_runtime_restore(self):
        self.assert_quiescence_failure('release')

    def test_failed_settings_lock_clears_pending_before_runtime_restore(self):
        self.assert_quiescence_failure('lock')

    def test_failed_settings_clear_prevents_draft_commit(self):
        self.assert_quiescence_failure('clear')

    def test_secondary_clear_failure_preserves_primary_release_error(self):
        self.assert_quiescence_failure('release', cleanup_failure=True)

    def assert_quiescence_failure(self, fault, cleanup_failure=False):
        window = self.window
        previous = deepcopy(window.current_config_data())
        original_restore = window.restore_configuration_runtime
        original_lock = window.backend.lock_control
        original_clear = window.control.clear_pending
        failure = RuntimeError('settings ' + fault + ' rejected')
        observed = []
        release_attempted = False
        clear_injected = False

        def release(*args):
            nonlocal release_attempted
            release_attempted = True
            window.control._dirty = True
            if fault == 'release':
                raise failure

        def lock(reason):
            if reason == 'settings applied' and fault == 'lock':
                window.control._dirty = True
                raise failure
            return original_lock(reason)

        def clear():
            nonlocal clear_injected
            original_clear()
            # lock_control also clears input internally. Target the explicit
            # post-release adapter clear; later native teardown may recover.
            if release_attempted and not clear_injected:
                clear_injected = True
                if fault == 'clear':
                    raise failure
                if cleanup_failure:
                    raise OSError('settings clear observer rejected')

        def restore(point):
            observed.append(window.control._dirty)
            return original_restore(point)

        with (
            patch.object(window.control, 'release_all', side_effect=release) as released,
            patch.object(window.backend, 'lock_control', side_effect=lock),
            patch.object(window.control, 'clear_pending', side_effect=clear) as cleared,
            patch.object(window, 'after_setting_changed') as applied,
            patch.object(window, 'restore_configuration_runtime', side_effect=restore),
            patch.object(window.backend, 'arm_match_control') as arm,
            patch.object(window, 'auto_save_config') as save,
        ):
            with self.assertRaises(ConfigurationApplyError) as caught:
                apply_registry_draft(window, {('RobotLink', 'Mouse Sensitivity'): 2.0})
            self.assertIs(caught.exception.cause, failure)
            if cleanup_failure:
                self.assertIn('settings clear observer rejected', ' '.join(failure.__notes__))
            configuration_tests.settle_configuration(window)
        self.assertEqual(observed, [False], 'stale input reached runtime restoration')
        self.assertEqual(window.current_config_data(), previous)
        self.assertFalse(window.control._dirty)
        self.assertFalse(window.configuration_change_pending)
        self.assertFalse(window.config_loading)
        self.assertTrue(window.backend.safety.estop_active)
        if fault == 'lock':
            released.assert_not_called()
        self.assertGreaterEqual(cleared.call_count, 1)
        applied.assert_not_called()
        arm.assert_not_called()
        save.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
