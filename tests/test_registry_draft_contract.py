"""Characterize settings-page draft ownership before replacing its state owner."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('POLAR_DISABLE_MEDIA_PRELOAD', '1')
import unittest
from unittest.mock import patch

from test_cs2_settings_center import APP, Host
from rm_client.ui import cs2_settings_center as settings
from rm_client.model import build_default_registry


class RegistryDraftContract(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.page = settings.RegistrySettingsPage(
            self.host, '键盘 / 鼠标', build_default_registry())
        self.addCleanup(self.host.deleteLater)
        self.addCleanup(self.page.deleteLater)
        self.key = ('RobotLink', 'Mouse Sensitivity')
        self.draft_key = ('setting', *self.key)
        self.spin = self.page.controls[self.key].spin
        self.live = self.host.registry.get(self.key[0]).get_setting(self.key[1])

    def test_clean_fields_follow_live_and_update_normalized_baseline(self):
        self.live.value = 1.234
        self.page.refresh_clean_draft()
        self.assertEqual(self.spin.value(), 1.23)
        self.assertEqual(self.page._baseline[self.draft_key], 1.23)
        self.page.refresh_clean_draft()
        self.assertEqual(self.spin.value(), 1.23)

    def test_pending_edit_survives_live_changes(self):
        self.spin.setValue(2)
        self.live.value = 3
        self.page.refresh_clean_draft()
        self.assertEqual(self.spin.value(), 2)
        self.assertEqual(self.page._baseline[self.draft_key], 1)

    def test_return_to_baseline_resumes_following_live(self):
        self.spin.setValue(2)
        self.spin.setValue(1)
        self.live.value = 3
        self.page.refresh_clean_draft()
        self.assertEqual(self.spin.value(), 3)

    def test_reset_equal_to_baseline_remains_explicit_edit(self):
        self.page.reset_draft()
        self.live.value = 3
        self.page.refresh_clean_draft()
        self.assertEqual(self.spin.value(), 1)
        with patch.object(settings, 'apply_registry_draft') as apply:
            self.page.apply_draft()
        self.assertEqual(apply.call_args.args[1][self.key], 1)
        self.assertEqual(self.spin.value(), 3)

    def test_failed_apply_retains_reset_intent_and_baseline(self):
        baseline = dict(self.page._baseline)
        self.page.reset_draft()
        with patch.object(settings, 'apply_registry_draft', side_effect=RuntimeError('reject')):
            with self.assertRaisesRegex(RuntimeError, 'reject'):
                self.page.apply_draft()
        self.assertEqual(self.page._baseline, baseline)
        self.live.value = 3
        self.page.refresh_clean_draft()
        self.assertEqual(self.spin.value(), 1)
        with patch.object(settings, 'apply_registry_draft') as apply:
            self.page.apply_draft()
        self.assertEqual(apply.call_args.args[1][self.key], 1)

    def test_forced_edit_skips_widget_read_during_refresh(self):
        self.page.reset_draft()
        with patch.object(self.page, '_widget_value', side_effect=AssertionError('unexpected read')):
            self.page.refresh_clean_draft()

    def test_successful_apply_accepts_pre_callback_snapshot_then_reconciles(self):
        self.spin.setValue(2)
        def change_widget(*args, **kwargs):
            self.spin.setValue(4)
        with patch.object(settings, 'apply_registry_draft', side_effect=change_widget):
            self.page.apply_draft()
        self.assertEqual(self.page._baseline[self.draft_key], 2)
        self.assertEqual(self.spin.value(), 4)
        with patch.object(settings, 'apply_registry_draft') as apply:
            self.page.apply_draft()
        self.assertEqual(apply.call_args.args[1][self.key], 4)

    def test_incomplete_page_does_not_observe_or_mutate_draft(self):
        self.page.builder.ready = False
        with patch.object(self.page, '_draft_widgets', side_effect=AssertionError('unexpected read')):
            self.page.refresh_clean_draft()
            self.page.reset_draft()
            with self.assertRaises(ValueError):
                self.page.apply_draft()


if __name__ == '__main__':
    unittest.main()
