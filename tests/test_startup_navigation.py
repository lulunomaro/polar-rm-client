"""Pure startup navigation policy and explicitly bounded compatibility fields."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.startup_navigation import StartupNavigation, StartupNavigationField


class StartupNavigationTests(unittest.TestCase):
    def test_import_and_use_needs_no_qt_or_runtime_implementation(self):
        source = '''
            import importlib.abc
            import sys
            class RejectRuntime(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.startswith(("PySide6", "rm_client.model", "rm_client.backend",
                                            "rm_client.ui.polar_app", "rm_client.transports")):
                        raise AssertionError("unexpected runtime dependency: " + fullname)
            sys.meta_path.insert(0, RejectRuntime())
            from rm_client.ui.polar.startup_navigation import StartupNavigation
            navigation = StartupNavigation(visible=True)
            assert navigation.allows_destination("robot")
            navigation.announce_destination("robot")
            navigation.transition_started = 10.
            navigation.activate_destination("robot")
            navigation.defer_static(.42)
            assert navigation.finish_transition()
            assert not navigation.finish_transition()
        '''
        result = subprocess.run([sys.executable, "-c", textwrap.dedent(source)],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_initial_page_follows_visibility_without_effects(self):
        for visible, page in ((True, "scenario"), (False, "done")):
            with self.subTest(visible=visible):
                state = StartupNavigation(visible=visible)
                self.assertEqual(state.page, page)
                self.assertEqual(state.transition_started, 0.)
                self.assertIsNone(state.transition_from)
                self.assertIsNone(state.transition_to)
                self.assertEqual(state.page_epoch, 0)

    def test_destination_admission_rejects_unknown_done_and_current(self):
        state = StartupNavigation(visible=True)
        for page in ("scenario", "done", "", "unknown"):
            self.assertFalse(state.allows_destination(page))
        for page in ("match", "robot"):
            self.assertTrue(state.allows_destination(page))
        state.page = "robot"
        self.assertTrue(state.allows_destination("scenario"))
        self.assertFalse(state.allows_destination("robot"))

    def test_announcement_is_distinct_from_interactive_publication(self):
        state = StartupNavigation(visible=True)
        state.announce_destination("match")
        self.assertEqual((state.page, state.transition_from, state.transition_to),
                         ("scenario", "scenario", "match"))
        self.assertEqual(state.page_epoch, 0)
        state.transition_started = 12.
        state.activate_destination("match")
        self.assertEqual((state.page, state.page_epoch), ("match", 1))
        state.defer_static(.75)
        self.assertEqual(state.static_ready_at, 12.75)

    def test_reverse_transition_replaces_pair_with_current_interactive_page(self):
        state = StartupNavigation(visible=True)
        state.announce_destination("match")
        state.activate_destination("match")
        state.announce_destination("scenario")
        self.assertEqual((state.transition_from, state.transition_to), ("match", "scenario"))
        state.activate_destination("scenario")
        self.assertEqual((state.page, state.page_epoch), ("scenario", 2))

    def test_finishing_partial_pair_is_one_time_and_keeps_page_clock_and_visibility(self):
        for outgoing, incoming in (("scenario", None), (None, "robot"), ("", "")):
            with self.subTest(outgoing=outgoing, incoming=incoming):
                state = StartupNavigation(visible=False)
                state.transition_from, state.transition_to = outgoing, incoming
                state.transition_started, state.static_ready_at = 14., 15.
                self.assertTrue(state.finish_transition())
                self.assertFalse(state.finish_transition())
                self.assertEqual((state.page, state.visible), ("done", False))
                self.assertEqual((state.transition_started, state.static_ready_at), (14., 15.))
                self.assertEqual(state.page_epoch, 1)

    def test_autostart_preserves_exact_existing_environment_spellings(self):
        for value in ("1", "true", "yes", "TRUE", "Yes"):
            with self.subTest(value=value):
                state = StartupNavigation(visible=True)
                state.apply_autostart(value)
                self.assertEqual((state.visible, state.page), (False, "done"))
                self.assertEqual(state.page_epoch, 0)
        for value in ("", "0", "false", "yes ", " true", "on"):
            with self.subTest(value=value):
                state = StartupNavigation(visible=True)
                state.apply_autostart(value)
                self.assertEqual((state.visible, state.page), (True, "scenario"))

    def test_second_step_navigation_is_symmetric(self):
        self.assertEqual(StartupNavigation.page_after_scenario(True), "match")
        self.assertEqual(StartupNavigation.page_before_robot(True), "match")
        self.assertEqual(StartupNavigation.page_after_scenario(False), "robot")
        self.assertEqual(StartupNavigation.page_before_robot(False), "scenario")

    def test_compatibility_fields_are_instance_local_and_reject_unrelated_state(self):
        class Host:
            page = StartupNavigationField("page")
            def __init__(self):
                self._startup_navigation = StartupNavigation(visible=True)
        first, second = Host(), Host()
        first.page = "robot"
        self.assertEqual(first._startup_navigation.page, "robot")
        self.assertEqual(second.page, "scenario")
        self.assertNotIn("page", first.__dict__)
        first._startup_navigation.page = "match"
        self.assertEqual(first.page, "match")
        self.assertIsInstance(Host.page, StartupNavigationField)
        with self.assertRaises(ValueError):
            StartupNavigationField("backend")
        with self.assertRaises(ValueError):
            StartupNavigationField("__dict__")


if __name__ == "__main__":
    unittest.main()
