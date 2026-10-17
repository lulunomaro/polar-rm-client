"""Freeze profile-save ownership at cd80ec7 before a state-owner extraction.

These tests call actual PolarRMWindow methods. Only storage, clocks, and named
adapter effects are replaced; expected traces and interrupted states are literal
contracts, not a second implementation. Real registry/Qt lifecycle fixtures use
a temporary user-data directory and stopped/inert device effects. They do not
qualify native devices, crash atomicity, or concurrent writers.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_configuration_transactions as fixtures
from rm_client.configuration.pending import ProfileIdentity
from rm_client.ui.polar.mixins import persistence_mixin as persistence


class ProfileSaveContractTests(unittest.TestCase):
    close_window = fixtures.WindowConfigurationTransactionTests.close_window

    def setUp(self):
        fixtures.WindowConfigurationTransactionTests.setUp(self)
        self.selector = self.window.registry.get("ConfigManager").get_setting("Profile")
        self.original_identity = self.window._accepted_profile_identity()
        self.reset_save_state()
        self.addCleanup(self.reset_save_state)

    def reset_save_state(self):
        window = self.window
        window._accepted_profile = self.original_identity
        window._unsaved_profile_name = None
        window._auto_save_pending = False
        window._auto_save_owner = None
        window._auto_save_due_at = 0.0
        window._close_save_retry_required = False
        window._config_save_error = False
        self.selector.value = self.original_identity.name

    def state(self):
        window = self.window
        return (window._accepted_profile, window._unsaved_profile_name,
                window._auto_save_pending, window._auto_save_owner,
                window._auto_save_due_at, window._close_save_retry_required,
                window._config_save_error, self.selector.value)

    def dirty(self, *, due=17.0):
        window = self.window
        window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 1.75
        window._auto_save_pending = True
        window._auto_save_owner = self.original_identity
        window._auto_save_due_at = due

    def test_legacy_save_fields_share_the_single_constructor_owner(self):
        window = self.window
        owner = window._profile_saves
        identity = ProfileIdentity("owned-profile", 17)
        fields = (
            ("_accepted_profile", "accepted", identity),
            ("_unsaved_profile_name", "draft", "draft-profile"),
            ("_auto_save_pending", "pending", True),
            ("_auto_save_owner", "pending_owner", identity),
            ("_auto_save_due_at", "due_at", 31.25),
            ("_close_save_retry_required", "close_retry_required", True),
            ("_config_save_error", "error_reported", True),
        )
        for alias, field, value in fields:
            with self.subTest(alias=alias):
                previous = getattr(owner, field)
                setattr(window, alias, value)
                self.assertIs(getattr(owner, field), value)
                setattr(owner, field, previous)
                self.assertIs(getattr(window, alias), previous)
        window.auto_save_config()
        self.assertIs(window._profile_saves, owner)
        self.assertTrue(owner.pending)
        self.assertIs(owner.pending_owner, self.original_identity)

    def test_identity_initialization_is_lazy_and_selection_failure_does_not_publish(self):
        window = self.window
        window._accepted_profile = None
        with patch.object(window, "current_profile", side_effect=ValueError("selector read")) as current:
            with self.assertRaisesRegex(ValueError, "selector read"):
                window._accepted_profile_identity()
            self.assertIsNone(window._accepted_profile)
            current.assert_called_once_with()
        with patch.object(window, "current_profile", return_value="new-selection") as current:
            identity = window._accepted_profile_identity()
            self.assertEqual(identity, ProfileIdentity("new-selection", 0))
            self.assertIs(window._accepted_profile_identity(), identity)
            current.assert_called_once_with()

    def test_restorable_draft_short_circuits_identity_read_but_empty_draft_does_not(self):
        window = self.window
        window._unsaved_profile_name = "draft-copy"
        with patch.object(window, "_accepted_profile_identity", side_effect=RuntimeError("identity read")) as identity:
            self.assertEqual(window._restorable_profile_name(), "draft-copy")
            identity.assert_not_called()
            window._unsaved_profile_name = ""
            self.assertTrue(window.configuration_close_requires_save)
            with self.assertRaisesRegex(RuntimeError, "identity read"):
                window._restorable_profile_name()

    def test_accept_selection_failure_publishes_identity_before_clearing_old_pending(self):
        window = self.window
        self.dirty()
        window._unsaved_profile_name = "draft"
        window._close_save_retry_required = True
        window._config_save_error = True
        new_identity = ProfileIdentity("new-profile", self.original_identity.generation + 1)
        seen = []

        def reject_selection(profile):
            seen.append((profile, self.state()))
            raise RuntimeError("selector write")

        with patch.object(window, "_set_profile_selection", side_effect=reject_selection):
            with self.assertRaisesRegex(RuntimeError, "selector write"):
                window._accept_profile_selection("new-profile")
        expected = (new_identity, None, True, self.original_identity, 17.0,
                    False, True, self.original_identity.name)
        self.assertEqual(seen, [("new-profile", expected)])
        self.assertEqual(self.state(), expected)

    def test_accept_same_name_advances_generation_but_keeps_due_and_error_history(self):
        window = self.window
        self.dirty()
        window._config_save_error = True
        window._close_save_retry_required = True
        window._accept_profile_selection(self.original_identity.name)
        self.assertEqual(self.state(), (
            ProfileIdentity(self.original_identity.name, self.original_identity.generation + 1),
            None, False, None, 17.0, False, True, self.original_identity.name))

    def test_schedule_reads_selection_and_identity_twice_before_sampling_clock(self):
        window = self.window
        trace = []
        original_bool = window.setting_bool

        def read_auto_save(module, name, default):
            trace.append(("option", module.name, name, default))
            return original_bool(module, name, default)

        def current():
            trace.append(("selection", self.state()))
            return self.original_identity.name

        def identity():
            trace.append(("identity", self.state()))
            return self.original_identity

        def clock():
            trace.append(("clock", self.state()))
            return 25.0

        before = self.state()
        published = (self.original_identity, None, True, self.original_identity,
                     0.0, False, False, self.original_identity.name)
        with patch.object(window, "setting_bool", side_effect=read_auto_save), \
             patch.object(window, "current_profile", side_effect=current), \
             patch.object(window, "_accepted_profile_identity", side_effect=identity), \
             patch.object(persistence.time, "perf_counter", side_effect=clock):
            window.auto_save_config()
        self.assertEqual(trace, [
            ("option", "ConfigManager", "Auto Save", True),
            ("selection", before), ("identity", before),
            ("identity", (self.original_identity, None, True, None, 0.0,
                          False, False, self.original_identity.name)),
            ("clock", published)])
        self.assertEqual(window._auto_save_due_at, 25.25)

    def test_second_identity_read_failure_keeps_new_pending_with_previous_owner(self):
        window = self.window
        old_owner = ProfileIdentity(self.original_identity.name, -1)
        window._auto_save_owner = old_owner
        window._auto_save_due_at = 99.0
        with patch.object(window, "_accepted_profile_identity", side_effect=[
                self.original_identity, RuntimeError("second identity")]), \
             patch.object(persistence.time, "perf_counter") as clock:
            with self.assertRaisesRegex(RuntimeError, "second identity"):
                window.auto_save_config()
        clock.assert_not_called()
        self.assertTrue(window._auto_save_pending)
        self.assertIs(window._auto_save_owner, old_owner)
        self.assertEqual(window._auto_save_due_at, 99.0)

    def test_schedule_clock_failure_keeps_published_pending_owner_and_old_deadline(self):
        window = self.window
        window._auto_save_due_at = 99.0
        with patch.object(persistence.time, "perf_counter", side_effect=RuntimeError("clock unavailable")):
            with self.assertRaisesRegex(RuntimeError, "clock unavailable"):
                window.auto_save_config()
        self.assertEqual(self.state(), (self.original_identity, None, True,
                         self.original_identity, 99.0, False, False, self.original_identity.name))

    def test_replaced_schedule_uses_latest_deadline_and_exports_latest_live_values(self):
        window = self.window
        sensitivity = window.registry.get("RobotLink").get_setting("Mouse Sensitivity")
        with patch.object(persistence.time, "perf_counter", side_effect=[10.0, 10.1]):
            sensitivity.value = 1.25
            window.auto_save_config()
            first_owner = window._auto_save_owner
            sensitivity.value = 2.0
            window.auto_save_config()
        self.assertIs(window._auto_save_owner, first_owner)
        self.assertEqual(window._auto_save_due_at, 10.35)
        sensitivity.value = 2.5
        with patch.object(window.config_store, "save_with_startup", return_value=Path("saved.json")) as save:
            window.flush_auto_save_if_due(10.30)
            save.assert_not_called()
            window.flush_auto_save_if_due(10.35)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.args[0], self.original_identity.name)
        self.assertEqual(save.call_args.args[1]["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 2.5)
        self.assertFalse(window._auto_save_pending)
        self.assertIsNone(window._auto_save_owner)
        self.assertEqual(window._auto_save_due_at, 10.35)

    def test_schedule_guards_keep_existing_pending_state_and_skip_later_reads(self):
        window = self.window
        auto = window.registry.get("ConfigManager").get_setting("Auto Save")
        for guard in ("loading", "configuration", "disabled", "draft", "selection"):
            with self.subTest(guard=guard):
                self.reset_save_state()
                self.dirty()
                window.config_loading = guard == "loading"
                window._pending_configuration = object() if guard == "configuration" else None
                auto.value = guard != "disabled"
                if guard == "draft":
                    window._unsaved_profile_name = "draft"
                before = self.state()
                try:
                    with patch.object(window, "current_profile", return_value="unaccepted") as current, \
                         patch.object(window, "_accepted_profile_identity", return_value=self.original_identity) as identity, \
                         patch.object(persistence.time, "perf_counter") as clock:
                        window.auto_save_config()
                    self.assertEqual(self.state(), before)
                    self.assertEqual(current.call_count, int(guard == "selection"))
                    self.assertEqual(identity.call_count, int(guard == "selection"))
                    clock.assert_not_called()
                finally:
                    window.config_loading = False
                    window._pending_configuration = None
                    auto.value = True

    def test_force_flush_publishes_zero_deadline_even_when_retry_or_draft_blocks_it(self):
        window = self.window
        for guard in ("configuration", "draft", "manual-retry"):
            with self.subTest(guard=guard):
                self.reset_save_state()
                self.dirty()
                window._pending_configuration = object() if guard == "configuration" else None
                window._unsaved_profile_name = "draft" if guard == "draft" else None
                window._close_save_retry_required = guard == "manual-retry"
                try:
                    with patch.object(persistence.time, "perf_counter") as clock, \
                         patch.object(window, "_save_profile") as save:
                        window.flush_auto_save(force=True)
                    self.assertEqual(window._auto_save_due_at, 0.0)
                    self.assertTrue(window._auto_save_pending)
                    self.assertIs(window._auto_save_owner, self.original_identity)
                    clock.assert_not_called()
                    save.assert_not_called()
                finally:
                    window._pending_configuration = None

    def test_stale_generation_is_checked_only_after_due_time_and_does_not_reset_errors(self):
        window = self.window
        self.dirty()
        stale = ProfileIdentity(self.original_identity.name, self.original_identity.generation - 1)
        window._auto_save_owner = stale
        window._config_save_error = True
        with patch.object(window, "_accepted_profile_identity", wraps=window._accepted_profile_identity) as identity, \
             patch.object(window.config_store, "save_with_startup") as save:
            window.flush_auto_save_if_due(16.0)
            self.assertTrue(window._auto_save_pending)
            identity.assert_not_called()
            window.flush_auto_save_if_due(17.0)
            identity.assert_called_once_with()
            save.assert_not_called()
        self.assertEqual(self.state(), (self.original_identity, None, False, None,
                         17.0, False, True, self.original_identity.name))

    def test_missing_owner_uses_accepted_filename_and_export_excludes_live_selector(self):
        window = self.window
        self.dirty()
        window._auto_save_owner = None
        self.selector.value = "unaccepted-selection"
        with patch.object(window.config_store, "save_with_startup", return_value=Path("saved.json")) as save:
            window.flush_auto_save_if_due(17.0)
        self.assertEqual(save.call_args.args[0], self.original_identity.name)
        self.assertNotIn("Profile", save.call_args.args[1]["modules"]["ConfigManager"]["settings"])
        self.assertIs(window._accepted_profile, self.original_identity)
        # The real refresh adapter normalizes a selector absent from the store.
        self.assertEqual(self.selector.value, self.original_identity.name)

    def test_failed_accepted_save_effects_publish_in_selection_clock_toast_error_order(self):
        window = self.window
        trace = []
        data = {"test": "document"}
        original_select = window._set_profile_selection
        self.selector.value = "unaccepted-selection"

        def export():
            trace.append(("export", self.state()))
            return data

        def load_on_start(module, name, default):
            trace.append(("option", module.name, name, default))
            return False

        def save(profile, document, *, load_on_start):
            trace.append(("save", profile, document, load_on_start))
            raise OSError("write rejected")

        def select(profile):
            trace.append(("select", profile, self.state()))
            original_select(profile)

        def clock():
            trace.append(("clock", self.state()))
            return 20.0

        def toast(message, **kwargs):
            trace.append(("toast", message, kwargs, self.state()))

        before = self.state()
        pending = (self.original_identity, None, True, self.original_identity,
                   0.0, False, False, self.original_identity.name)
        with patch.object(window, "current_config_data", side_effect=export), \
             patch.object(window, "setting_bool", side_effect=load_on_start), \
             patch.object(window.config_store, "save_with_startup", side_effect=save), \
             patch.object(window, "_set_profile_selection", side_effect=select), \
             patch.object(persistence.time, "perf_counter", side_effect=clock), \
             patch.object(window, "toast", side_effect=toast):
            window._save_profile(self.original_identity.name, announce=False)
        self.assertEqual(trace, [
            ("export", before), ("option", "ConfigManager", "Load On Start", True),
            ("save", self.original_identity.name, data, False),
            ("select", self.original_identity.name, before), ("clock", pending),
            ("toast", "配置保存失败，将重试：write rejected", {"kind": "warning"},
             (self.original_identity, None, True, self.original_identity,
              25.0, False, False, self.original_identity.name))])
        self.assertTrue(window._config_save_error)

    def test_failed_accepted_save_selector_exception_precedes_pending_publication(self):
        window = self.window
        self.selector.value = "unaccepted"
        before = self.state()
        with patch.object(window.config_store, "save_with_startup", side_effect=OSError("save")), \
             patch.object(window, "_set_profile_selection", side_effect=RuntimeError("selection")), \
             patch.object(persistence.time, "perf_counter") as clock:
            with self.assertRaisesRegex(RuntimeError, "selection"):
                window._save_profile(self.original_identity.name, announce=False)
        self.assertEqual(self.state(), before)
        clock.assert_not_called()

    def test_failed_accepted_save_clock_exception_keeps_pending_but_not_error_marker(self):
        window = self.window
        window._auto_save_due_at = 91.0
        self.selector.value = "unaccepted"
        with patch.object(window.config_store, "save_with_startup", side_effect=OSError("save")), \
             patch.object(persistence.time, "perf_counter", side_effect=RuntimeError("retry clock")), \
             patch.object(window, "toast") as toast:
            with self.assertRaisesRegex(RuntimeError, "retry clock"):
                window._save_profile(self.original_identity.name, announce=False)
        self.assertEqual(self.state(), (self.original_identity, None, True,
                         self.original_identity, 91.0, False, False, self.original_identity.name))
        toast.assert_not_called()

    def test_failed_save_toast_exception_preserves_retry_but_precedes_error_marker(self):
        window = self.window
        with patch.object(window.config_store, "save_with_startup", side_effect=OSError("save")), \
             patch.object(persistence.time, "perf_counter", return_value=20.0), \
             patch.object(window, "toast", side_effect=RuntimeError("toast")):
            with self.assertRaisesRegex(RuntimeError, "toast"):
                window._save_profile(self.original_identity.name, announce=False)
        self.assertEqual(self.state(), (self.original_identity, None, True,
                         self.original_identity, 25.0, False, False, self.original_identity.name))

    def test_failed_draft_selector_exception_publishes_draft_before_clearing_old_pending(self):
        window = self.window
        self.dirty()
        before = self.state()
        with patch.object(window.config_store, "save_with_startup", side_effect=OSError("save")), \
             patch.object(window, "_set_profile_selection", side_effect=RuntimeError("selection")), \
             patch.object(persistence.time, "perf_counter") as clock:
            with self.assertRaisesRegex(RuntimeError, "selection"):
                window._save_profile("draft-copy", announce=False, explicit=True)
        self.assertEqual(self.state(), (before[0], "draft-copy", *before[2:]))
        clock.assert_not_called()

    def test_draft_failure_does_not_sample_clock_and_manual_source_retry_releases_protection(self):
        window = self.window
        self.dirty()
        window._config_save_error = True
        with patch.object(window.config_store, "save_with_startup", side_effect=OSError("copy")), \
             patch.object(persistence.time, "perf_counter") as clock, \
             patch.object(window, "toast") as toast:
            window._save_profile("draft-copy", announce=False, explicit=True)
        clock.assert_not_called()
        self.assertEqual(self.state(), (self.original_identity, "draft-copy", False,
                         None, 17.0, False, True, "draft-copy"))
        self.assertIn("另存失败", toast.call_args.args[0])
        self.selector.value = self.original_identity.name
        with patch.object(window.config_store, "save_with_startup", return_value=Path("saved.json")) as save:
            window.auto_save_config()
            window.flush_auto_save_if_due(float("inf"))
            save.assert_not_called()
            window.save_current_config(announce=False)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(self.state(), (
            ProfileIdentity(self.original_identity.name, self.original_identity.generation + 1),
            None, False, None, 17.0, False, False, self.original_identity.name))

    def test_successful_write_selector_failure_keeps_new_identity_and_old_pending(self):
        window = self.window
        self.dirty()
        window._unsaved_profile_name = "draft-copy"
        window._close_save_retry_required = True
        window._config_save_error = True
        events = []

        def save(*args, **kwargs):
            events.append("write")
            return Path("saved.json")

        def select(profile):
            events.append("select")
            raise RuntimeError("select after committed write")

        with patch.object(window.config_store, "save_with_startup", side_effect=save), \
             patch.object(window, "_set_profile_selection", side_effect=select), \
             patch.object(window, "refresh_config_profiles") as refresh:
            with self.assertRaisesRegex(RuntimeError, "select after committed write"):
                window._save_profile("draft-copy", announce=False, explicit=True)
        self.assertEqual(events, ["write", "select"])
        self.assertEqual(self.state(), (
            ProfileIdentity("draft-copy", self.original_identity.generation + 1), None,
            True, self.original_identity, 17.0, False, True, self.original_identity.name))
        refresh.assert_not_called()

    def test_refresh_failure_after_success_keeps_cleared_pending_and_retry_state(self):
        window = self.window
        self.dirty()
        window._close_save_retry_required = True
        window._config_save_error = True
        with patch.object(window.config_store, "save_with_startup", return_value=Path("saved.json")), \
             patch.object(window, "refresh_config_profiles", side_effect=RuntimeError("refresh")), \
             patch.object(window, "toast") as toast:
            with self.assertRaisesRegex(RuntimeError, "refresh"):
                window._save_profile(self.original_identity.name, announce=True)
        self.assertEqual(self.state(), (self.original_identity, None, False, None,
                         17.0, False, False, self.original_identity.name))
        toast.assert_not_called()

    def test_unhandled_write_error_propagates_without_reclassifying_as_save_failure(self):
        window = self.window
        self.dirty()
        before = self.state()
        with patch.object(window.config_store, "save_with_startup", side_effect=TypeError("programming error")), \
             patch.object(window, "toast") as toast:
            with self.assertRaisesRegex(TypeError, "programming error"):
                window._save_profile(self.original_identity.name, announce=True)
        self.assertEqual(self.state(), before)
        toast.assert_not_called()

    def test_close_publishes_manual_retry_before_export_exception_and_background_stays_paused(self):
        window = self.window
        self.dirty()
        seen = []

        def fail_export():
            seen.append(self.state())
            raise TypeError("export")

        with patch.object(window, "current_config_data", side_effect=fail_export), \
             patch.object(window.config_store, "save_with_startup") as save:
            with self.assertRaisesRegex(TypeError, "export"):
                window.prepare_configuration_for_close()
            window.auto_save_config()
            window.flush_auto_save(force=True)
            window.flush_auto_save_if_due(float("inf"))
            save.assert_not_called()
        self.assertEqual(seen, [(self.original_identity, None, True, self.original_identity,
                                17.0, True, False, self.original_identity.name)])
        self.assertTrue(window._close_save_retry_required)
        self.assertTrue(window._auto_save_pending)
        self.assertEqual(window._auto_save_due_at, 0.0)

    def test_close_stale_owner_discards_only_pending_but_draft_takes_priority(self):
        window = self.window
        self.dirty()
        stale = ProfileIdentity(self.original_identity.name, -1)
        window._auto_save_owner = stale
        window._close_save_retry_required = True
        window._config_save_error = True
        with patch.object(window, "_save_profile") as save:
            self.assertTrue(window.prepare_configuration_for_close())
            save.assert_not_called()
        self.assertEqual(self.state(), (self.original_identity, None, False, None,
                         17.0, True, True, self.original_identity.name))
        self.dirty()
        window._auto_save_owner = stale
        window._unsaved_profile_name = "owned-draft"
        self.selector.value = "unaccepted-third-name"
        with patch.object(window.config_store, "save_with_startup", return_value=Path("saved.json")) as save:
            self.assertTrue(window.prepare_configuration_for_close())
        self.assertEqual(save.call_args.args[0], "owned-draft")
        self.assertEqual(window._accepted_profile.name, "owned-draft")
        # A mocked write does not publish the new filename to profile_names().
        self.assertEqual(window.current_profile(), self.original_identity.name)
        self.assertIsNone(window._unsaved_profile_name)

    def test_failed_close_retries_once_per_explicit_entry_and_uses_latest_live_edit(self):
        window = self.window
        self.dirty()
        with patch.object(window.config_store, "save_with_startup", side_effect=OSError("close save")) as save, \
             patch.object(persistence.time, "perf_counter", return_value=30.0), \
             patch.object(window, "toast") as toast:
            self.assertFalse(window.prepare_configuration_for_close())
            self.assertEqual(save.call_count, 1)
            window.auto_save_config()
            window.flush_auto_save(force=True)
            window.flush_auto_save_if_due(float("inf"))
            self.assertEqual(save.call_count, 1)
            window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 2.5
            self.assertFalse(window.prepare_configuration_for_close())
            self.assertEqual(save.call_count, 2)
        self.assertEqual(toast.call_count, 2)
        self.assertIn("请手动保存或重试关闭", toast.call_args.args[0])
        self.assertEqual(save.call_args.args[1]["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 2.5)
        self.assertTrue(window._close_save_retry_required)
        self.assertEqual(window._auto_save_due_at, 35.0)

    def test_switch_flushes_old_owner_before_runtime_request_and_accepts_only_matching_completion(self):
        window = self.window
        self.dirty()
        candidate = deepcopy(window.current_config_data())
        candidate["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"] = 2.5
        window.config_store.save("next-profile", candidate)
        self.selector.value = "next-profile"
        trace = []
        request = window.backend.request_scenario

        def save(profile, data, **kwargs):
            trace.append(("write", profile, self.selector.value, self.state(),
                          data["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"]))
            return Path("saved.json")

        def runtime(*args, **kwargs):
            trace.append(("runtime", self.selector.value, window._accepted_profile,
                          window._auto_save_pending))
            return request(*args, **kwargs)

        with patch.object(window.config_store, "save_with_startup", side_effect=save), \
             patch.object(window.backend, "request_scenario", side_effect=runtime):
            window.apply_config_data(candidate, announce=False)
        self.assertEqual(trace, [
            ("write", self.original_identity.name, self.original_identity.name,
             (self.original_identity, None, True, self.original_identity,
              0.0, False, False, self.original_identity.name), 1.75),
            ("runtime", "next-profile", self.original_identity, False)])
        pending = window._pending_configuration
        self.assertIsNotNone(pending)
        self.assertFalse(window.on_backend_lifecycle_completion(SimpleNamespace(
            generation=pending.generation - 1, succeeded=True, final=False)))
        self.assertIs(window._pending_configuration, pending)
        self.assertIs(window._accepted_profile, self.original_identity)
        fixtures.settle_configuration(window)
        self.assertEqual(window._accepted_profile, ProfileIdentity(
            "next-profile", self.original_identity.generation + 1))
        self.assertFalse(window._auto_save_pending)

    def test_reentry_during_second_identity_read_retains_literal_outer_assignment_order(self):
        """Synthetic callback reentry; no production callback route is claimed."""
        window = self.window
        original = window._accepted_profile_identity
        reads = 0
        nested = []
        next_identity = ProfileIdentity("next-profile", self.original_identity.generation + 1)

        def read_identity():
            nonlocal reads
            reads += 1
            captured = original()
            if reads == 2:
                window._accept_profile_selection("next-profile")
                window.auto_save_config()
                nested.append(self.state())
            return captured

        with patch.object(window, "_accepted_profile_identity", side_effect=read_identity), \
             patch.object(persistence.time, "perf_counter", side_effect=[20.0, 10.0]):
            window.auto_save_config()
        self.assertEqual(reads, 5)
        self.assertEqual(nested, [(next_identity, None, True, next_identity,
                                  20.25, False, False, "next-profile")])
        self.assertEqual(self.state(), (next_identity, None, True, self.original_identity,
                         10.25, False, False, "next-profile"))
        with patch.object(window.config_store, "save_with_startup") as save:
            window.flush_auto_save_if_due(30.0)
            save.assert_not_called()
        self.assertFalse(window._auto_save_pending)

    def test_clock_reentry_keeps_nested_owner_but_outer_deadline_assignment_wins(self):
        """Synthetic clock callback freezes current read/effect ordering."""
        window = self.window
        entered = False
        nested = []
        next_identity = ProfileIdentity("next-profile", self.original_identity.generation + 1)

        def clock():
            nonlocal entered
            if entered:
                return 20.0
            entered = True
            window._accept_profile_selection("next-profile")
            window.auto_save_config()
            nested.append(self.state())
            return 10.0

        with patch.object(persistence.time, "perf_counter", side_effect=clock):
            window.auto_save_config()
        self.assertEqual(nested, [(next_identity, None, True, next_identity,
                                  20.25, False, False, "next-profile")])
        self.assertEqual(self.state(), (next_identity, None, True, next_identity,
                         10.25, False, False, "next-profile"))

    def test_successful_write_return_clears_newer_synthetic_reentrant_pending_save(self):
        """Characterize existing synchronous behavior, not a new safety promise.

        The actual store has no callback/event-pump contract. This injected
        callback demonstrates why an extraction must not silently add a fresh
        generation guard or pretend to have fixed an ordinary-user defect.
        """
        window = self.window
        self.dirty()
        next_identity = ProfileIdentity("next-profile", self.original_identity.generation + 1)
        nested = []
        saved = []

        def save(profile, data, **kwargs):
            saved.append((profile, data["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"]))
            window._accept_profile_selection("next-profile")
            window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 2.5
            window.auto_save_config()
            nested.append(self.state())
            return Path("saved.json")

        with patch.object(window.config_store, "save_with_startup", side_effect=save), \
             patch.object(window.config_store, "profile_names", return_value=[self.original_identity.name, "next-profile"]), \
             patch.object(persistence.time, "perf_counter", return_value=100.0):
            window.flush_auto_save_if_due(17.0)
        self.assertEqual(saved, [(self.original_identity.name, 1.75)])
        self.assertEqual(nested, [(next_identity, None, True, next_identity,
                                  100.25, False, False, "next-profile")])
        self.assertEqual(self.state(), (next_identity, None, False, None,
                         100.25, False, False, "next-profile"))
        self.assertEqual(window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value, 2.5)

    def test_same_name_pending_reload_keeps_dirty_owner_and_refuses_close_until_completion(self):
        window = self.window
        candidate = deepcopy(window.current_config_data())
        self.dirty()
        window.apply_config_data(candidate, announce=False)
        pending = window._pending_configuration
        self.assertIsNotNone(pending)
        before = self.state()
        with patch.object(window.config_store, "save_with_startup") as save, \
             patch.object(window, "toast") as toast:
            window.save_current_config()
            window.auto_save_config()
            self.assertFalse(window.prepare_configuration_for_close())
        save.assert_not_called()
        self.assertEqual(toast.call_count, 2)
        self.assertEqual(self.state(), before)
        self.assertIs(window._pending_configuration, pending)
        fixtures.settle_configuration(window)
        self.assertEqual(window._accepted_profile, ProfileIdentity(
            self.original_identity.name, self.original_identity.generation + 1))
        self.assertFalse(window._auto_save_pending)
        self.assertIsNone(window._auto_save_owner)


if __name__ == "__main__":
    unittest.main()
