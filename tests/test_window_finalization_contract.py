"""Frozen owner-thread retirement traces, without Qt or native resources.

Execute the production window adapter against explicit fake resources. Loading
only its AST avoids substituting the cleanup policy or requiring a GL context.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.ui.polar import window_lifecycle as lifecycle

RESOURCE_NAMES = ("clickgui_gpu", "vision_wire_gpu", "island_icon_gpu",
                  "quick_action_wheel_gpu", "gpu_compositor", "startup_gpu", "hud_video_gpu")


def production_finish():
    tree = ast.parse((ROOT / "rm_client/ui/polar_app.py").read_text())
    window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PolarRMWindow")
    method = next(n for n in window.body if isinstance(n, ast.FunctionDef) and n.name == "_finish_window_shutdown")
    namespace = dict(vars(lifecycle), _LOG=logging.getLogger(__name__))
    exec(compile(ast.Module(body=[method], type_ignores=[]), "window-finalization-adapter", "exec"), namespace)
    return namespace[method.name]


class FinalizationFixture:
    def __init__(self, *, faults=(), context=True):
        self.calls = []
        self.faults = set(faults)
        self._window_lifecycle = lifecycle.WindowLifecycleController(None)
        self._window_close_finalized = False
        self.has_context = context
        self.timer = NS(stop=lambda: self.call("timer"))
        self._gpu_prewarm_timer = NS(stop=lambda: self.call("warmup"))
        self.state = NS(push_event=lambda message: self.call("event", message))
        for name in RESOURCE_NAMES:
            setattr(self, name, NS(release=lambda name=name: self.call(name)))

    @property
    def _window_close_finalized(self):
        return getattr(self._window_lifecycle, "finalized", False)

    @_window_close_finalized.setter
    def _window_close_finalized(self, value):
        self._window_lifecycle.finalized = value

    def call(self, name, *args):
        self.calls.append((name, *args))
        if name in self.faults:
            raise OSError(name + " rejected")

    def flush_auto_save(self, *, force):
        self.call("save", force)

    def context(self):
        self.call("context")
        return object() if self.has_context else None

    def makeCurrent(self):
        self.call("make")

    def doneCurrent(self):
        self.call("done")

    def _show_shutdown_cleanup_warning(self, message):
        self.call("warning", message)


class WindowFinalizationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.finish = staticmethod(production_finish())

    def test_success_order_and_repeated_finish_are_frozen(self):
        fixture = FinalizationFixture()
        self.finish(fixture)
        expected = [("timer",), ("warmup",), ("save", True), ("context",), ("make",)]
        expected += [(name,) for name in RESOURCE_NAMES] + [("done",)]
        self.assertEqual(fixture.calls, expected)
        self.assertTrue(fixture._window_close_finalized)
        self.finish(fixture)
        self.assertEqual(fixture.calls, expected)

    def test_independent_owner_and_gpu_failures_preserve_every_later_release(self):
        for fault in ("timer", "warmup", "save", *RESOURCE_NAMES, "done"):
            with self.subTest(fault=fault):
                fixture = FinalizationFixture(faults=(fault,))
                self.finish(fixture)
                names = [call[0] for call in fixture.calls]
                self.assertEqual(names[:-2], ["timer", "warmup", "save", "context", "make", *RESOURCE_NAMES, "done"])
                self.assertEqual(names[-2:], ["event", "warning"])
                self.assertEqual(fixture.calls[-1][1], fixture.calls[-2][1])
                self.assertIn("OSError: " + fault + " rejected", fixture.calls[-1][1])

    def test_missing_context_skips_all_gl_work(self):
        fixture = FinalizationFixture(context=False)
        self.finish(fixture)
        self.assertEqual(fixture.calls, [("timer",), ("warmup",), ("save", True), ("context",)])

    def test_context_inspection_and_acquisition_have_distinct_cleanup_boundaries(self):
        for fault, expected in (("context", ["timer", "warmup", "save", "context", "event", "warning"]),
                                ("make", ["timer", "warmup", "save", "context", "make", "done", "event", "warning"])):
            with self.subTest(fault=fault):
                fixture = FinalizationFixture(faults=(fault,))
                self.finish(fixture)
                self.assertEqual([call[0] for call in fixture.calls], expected)
                self.assertIn("GL context: OSError: " + fault + " rejected", fixture.calls[-1][1])

    def test_previous_owner_failures_precede_retirement_failures(self):
        fixture = FinalizationFixture(faults=("timer", "done"))
        fixture._window_lifecycle.owner_failures = (lifecycle.OwnerActionFailure("earlier", "ValueError", "prepare failed"),)
        self.finish(fixture)
        self.assertEqual(fixture.calls[-1][1], "Exit cleanup warnings: earlier: ValueError: prepare failed; timer: OSError: timer rejected; GL done: OSError: done rejected")

    def test_prepare_callback_lookup_reentry_cannot_repeat_retirement(self):
        finish = self.finish
        class ReentrantLookup(FinalizationFixture):
            looked_up = False
            def __getattribute__(self, name):
                if name == "timer" and not self.looked_up:
                    self.looked_up = True
                    finish(self)
                return super().__getattribute__(name)
        fixture = ReentrantLookup()
        finish(fixture)
        self.assertEqual([call[0] for call in fixture.calls],
                         ["timer", "warmup", "save", "context", "make", *RESOURCE_NAMES, "done"])

    def test_missing_prepare_callback_seals_before_lookup_failure(self):
        fixture = FinalizationFixture()
        del fixture.timer.stop
        with self.assertRaises(AttributeError):
            self.finish(fixture)
        self.assertTrue(fixture._window_close_finalized)
        self.assertEqual(fixture.calls, [])
        self.finish(fixture)

    def test_context_callbacks_are_resolved_only_in_their_original_phase(self):
        class MissingContextCallbacks(FinalizationFixture):
            def __getattribute__(self, name):
                if name in {"makeCurrent", "doneCurrent"}:
                    raise AttributeError(name + " unavailable")
                return super().__getattribute__(name)
        absent = MissingContextCallbacks(context=False)
        self.finish(absent)
        self.assertEqual(absent.calls, [("timer",), ("warmup",), ("save", True), ("context",)])
        present = MissingContextCallbacks()
        with self.assertRaisesRegex(AttributeError, "doneCurrent unavailable"):
            self.finish(present)
        self.assertTrue(present._window_close_finalized)
        self.assertEqual(present.calls, [("timer",), ("warmup",), ("save", True), ("context",)])

    def test_failed_warning_does_not_replay_finalization(self):
        for fault in ("event", "warning"):
            fixture = FinalizationFixture(faults=("timer", fault))
            with self.assertRaisesRegex(OSError, fault + " rejected"):
                self.finish(fixture)
            before = list(fixture.calls)
            self.finish(fixture)
            self.assertEqual(fixture.calls, before)


if __name__ == "__main__":
    unittest.main()
