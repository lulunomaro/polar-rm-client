"""Focus-loss safety attempts remain independent; outputs are inert spies."""
from __future__ import annotations

import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client import emergency_actions


def focus_host_type():
    tree = ast.parse((ROOT / "rm_client/ui/polar_app.py").read_text(encoding="utf-8"))
    window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PolarRMWindow")
    method = next(node for node in window.body if isinstance(node, ast.FunctionDef) and node.name == "focusOutEvent")
    class Base:
        def focusOutEvent(self, event):
            self.trace.append("qt-focus")
    node = ast.ClassDef(name="FocusHost", bases=[ast.Name(id="Base", ctx=ast.Load())],
                        keywords=[], body=[method], decorator_list=[])
    scope = dict(vars(emergency_actions), Base=Base)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 "<production-focus-event>", "exec"), scope)
    return scope["FocusHost"]


class FocusLossFailureTests(unittest.TestCase):
    def host(self, failures=(), rejected=False):
        host = focus_host_type()()
        host.trace = []
        host.state = object()
        host.registry = object()
        host.student_ui = SimpleNamespace(dragging="held")
        host.warnings = []
        host.toast = lambda message, **kwargs: host.warnings.append((message, kwargs))
        def action(name, result=None):
            def call(*args):
                host.trace.append(name)
                if name in failures:
                    raise RuntimeError(name + " failed")
                return result
            return call
        host.control = SimpleNamespace(mock_output=False,
            release_all=action("release"), reset_inputs=action("reset"),
            release_direct_dt7=action("dt7"), release_custom_controller=action("cc"),
            clear_pending=action("clear"))
        host.backend = SimpleNamespace(lock_control=action("lock"),
            mqtt=SimpleNamespace(mock_output=True, publish_estop_zero=action(
                "zero", (False, "offline") if rejected else (True, ""))))
        return host

    def test_authority_revocation_precedes_fallible_input_release(self):
        host = self.host()
        host.focusOutEvent(object())
        self.assertEqual(host.trace, ["lock", "release", "zero", "clear", "qt-focus"])
        self.assertEqual(host.student_ui.dragging, "")
        self.assertEqual(host.warnings, [])

    def test_release_failure_still_locks_resets_releases_both_links_and_clears(self):
        host = self.host({"release"})
        host.focusOutEvent(object())
        self.assertEqual(host.trace, ["lock", "release", "reset", "dt7", "cc", "zero", "clear", "qt-focus"])
        self.assertIn("release failed", host.warnings[0][0])

    def test_each_outer_stop_failure_cannot_skip_other_effects_or_qt(self):
        for failure in ("lock", "zero", "clear"):
            with self.subTest(failure=failure):
                host = self.host({failure})
                host.focusOutEvent(object())
                self.assertEqual(host.trace, ["lock", "release", "zero", "clear", "qt-focus"])
                self.assertIn(failure + " failed", host.warnings[0][0])

    def test_all_failures_remain_observable_and_independent(self):
        host = self.host({"lock", "release", "reset", "dt7", "cc", "zero", "clear"})
        host.focusOutEvent(object())
        self.assertEqual(host.trace, ["lock", "release", "reset", "dt7", "cc", "zero", "clear", "qt-focus"])
        for name in host.trace[:-1]:
            self.assertIn(name + " failed", host.warnings[0][0])
        self.assertNotIn("LOCKED", host.warnings[0][0])

    def test_rejected_neutral_is_reported_and_does_not_skip_pending_clear(self):
        host = self.host(rejected=True)
        host.focusOutEvent(object())
        self.assertIn("offline", host.warnings[0][0])
        self.assertEqual(host.trace[-2:], ["clear", "qt-focus"])

    def test_mock_mode_lookup_failure_still_attempts_neutral_cleanup(self):
        host = self.host()
        class BrokenMqtt:
            @property
            def mock_output(self):
                raise RuntimeError("mode lookup failed")
            def publish_estop_zero(self):
                host.trace.append("zero")
                return True, ""
        host.backend.mqtt = BrokenMqtt()
        host.focusOutEvent(object())
        self.assertEqual(host.trace, ["lock", "reset", "dt7", "cc", "zero", "clear", "qt-focus"])
        self.assertIn("mode lookup failed", host.warnings[0][0])

    def test_reporting_failure_still_delegates_to_qt(self):
        host = self.host({"zero"})
        def fail_report(*args, **kwargs):
            raise RuntimeError("report failed")
        host.toast = fail_report
        with self.assertRaisesRegex(RuntimeError, "report failed"):
            host.focusOutEvent(object())
        self.assertEqual(host.trace[-2:], ["clear", "qt-focus"])


if __name__ == "__main__":
    unittest.main()
