"""Startup policy is testable without constructing any UI/backend service."""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.ui.polar.startup_activation import (
    StartupProfilePorts, StartupPresentationPorts, StartupScenarioPorts,
    activate_startup_session,
)

PROFILE = [field.name for field in fields(StartupProfilePorts)]
PRESENTATION = [field.name for field in fields(StartupPresentationPorts)]
PREFIX = PROFILE + PRESENTATION[:-1]


class StartupActivationTests(unittest.TestCase):
    def workflow(self, *, visible=False, pending=False, fail_at=None, mutate=None):
        trace = []
        state = dict(visible=visible, pending=pending, generation=41)
        failure = RuntimeError("startup failure")

        def action(name, result=None):
            def invoke(*args):
                trace.append((name, *args))
                if mutate is not None:
                    mutate(name, state)
                if name == fail_at:
                    raise failure
                return result() if callable(result) else result
            return invoke

        profile = StartupProfilePorts(**{name: action(name) for name in PROFILE})
        presentation = StartupPresentationPorts(**{name: action(name) for name in PRESENTATION})
        scenario = StartupScenarioPorts(
            launcher_visible=action("visible", lambda: state["visible"]),
            configuration_pending=action("pending", lambda: state["pending"]),
            request=action("request", lambda: state["generation"]),
            capture=action("capture"),
        )
        return lambda: activate_startup_session(profile, presentation, scenario), trace, failure

    def test_exact_order_and_generation_capture(self):
        run, trace, _ = self.workflow()
        run()
        self.assertEqual(trace, [(name,) for name in PREFIX + ["visible", "pending", "request"]]
                         + [("capture", 41), ("announce_ready",)])

    def test_launcher_short_circuits_pending_query_and_request(self):
        run, trace, _ = self.workflow(visible=True, fail_at="pending")
        run()
        self.assertEqual(trace, [(name,) for name in PREFIX + ["visible", "announce_ready"]])

    def test_profile_pending_generation_is_never_superseded(self):
        def loading(name, state):
            if name == "load_saved":
                state.update(pending=True, generation=42)
        run, trace, _ = self.workflow(mutate=loading)
        run()
        self.assertEqual(trace, [(name,) for name in PREFIX + ["visible", "pending", "announce_ready"]])

    def test_autostart_and_loaded_scenario_are_read_after_projection(self):
        def change(name, state):
            if name == "load_saved":
                state["generation"] = 73
            if name == "apply_autostart":
                state["visible"] = False
        run, trace, _ = self.workflow(visible=True, mutate=change)
        run()
        self.assertEqual(trace[-2:], [("capture", 73), ("announce_ready",)])

    def test_mode_projection_can_start_pending_configuration(self):
        def change(name, state):
            if name == "sync_mode":
                state["pending"] = True
        run, trace, _ = self.workflow(mutate=change)
        run()
        self.assertNotIn(("request",), trace)
        self.assertEqual(trace[-1], ("announce_ready",))

    def test_every_failure_preserves_exact_prefix_and_exception_identity(self):
        expected = [(name,) for name in PREFIX + ["visible", "pending", "request"]]
        expected += [("capture", 41), ("announce_ready",)]
        for index, (name, *_) in enumerate(expected):
            with self.subTest(step=name):
                run, trace, failure = self.workflow(fail_at=name)
                with self.assertRaises(RuntimeError) as caught:
                    run()
                self.assertIs(caught.exception, failure)
                self.assertEqual(trace, expected[:index + 1])

    def test_no_qt_backend_or_window_import_dependency(self):
        result = subprocess.run([sys.executable, "-c", '''
import sys
from rm_client.ui.polar.startup_activation import activate_startup_session
for name in sys.modules:
    assert not name.startswith(("PySide6", "rm_client.backend", "rm_client.ui.polar_app")), name
'''], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
