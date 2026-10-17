"""Window-free preparation queue tests use only explicit upload/context ports."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.gpu_warmup import GpuWarmupQueue, GpuWarmupField


class GpuWarmupOwnerTests(unittest.TestCase):
    def run_slice(self, owner, jobs=None, *, times=(0., .001, .004), upload=None,
                  release=None, acquire=None, report=None):
        events = []
        samples = iter(times)
        def text(*args, **kwargs):
            events.append(("text", args, kwargs))
            if upload:
                upload()
        owner.run_slice(owner.pending if jobs is None else jobs,
                        uploads=SimpleNamespace(prewarm=text, prewarm_images=lambda *args: events.append(("image", args))),
                        acquire_context=acquire or (lambda: events.append("acquire")),
                        release_context=release or (lambda: events.append("release")),
                        report_error=report or (lambda error: events.append(("error", error))),
                        clock=lambda: next(samples))
        return events

    def test_module_and_queue_are_usable_when_qt_and_runtime_imports_are_denied(self):
        source = '''
            import importlib.abc
            import sys
            class RejectRuntime(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.startswith(("PySide6", "rm_client.ui.polar_app",
                                            "rm_client.backend", "rm_client.model")):
                        raise AssertionError("runtime import: " + fullname)
            sys.meta_path.insert(0, RejectRuntime())
            from rm_client.ui.polar.gpu_warmup import GpuWarmupQueue
            owner = GpuWarmupQueue()
            assert owner.pending is None and owner.last_ms == 0. and owner.max_ms == 0.
        '''
        result = subprocess.run([sys.executable, "-c", textwrap.dedent(source)],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_replace_does_not_advance_or_close_displaced_iterator(self):
        events = []
        def jobs():
            try:
                events.append("advance")
                yield "text", (), {}
            finally:
                events.append("close")
        first, second = jobs(), jobs()
        owner = GpuWarmupQueue()
        owner.replace(first)
        owner.replace(second)
        self.assertEqual(events, [])
        self.assertIs(owner.pending, second)
        self.assertEqual(next(first), ("text", (), {}))
        self.assertEqual(events, ["advance"])
        first.close()
        self.assertEqual(events, ["advance", "close"])

    def test_one_job_per_turn_and_exhaustion_are_distinct_turns(self):
        jobs = iter([("text", ("font",), {"density": 2.}), ("image", ("image",), {})])
        owner = GpuWarmupQueue(jobs)
        self.assertEqual(self.run_slice(owner), ["acquire", ("text", ("font",), {"density": 2.}), "release"])
        self.assertIs(owner.pending, jobs)
        self.assertEqual(self.run_slice(owner), ["acquire", ("image", ("image",)), "release"])
        self.assertIs(owner.pending, jobs)
        self.assertEqual(self.run_slice(owner), ["acquire", "release"])
        self.assertIsNone(owner.pending)

    def test_exact_soft_budget_boundary_keeps_job_pending(self):
        jobs = iter([("text", (), {})])
        owner = GpuWarmupQueue(jobs)
        self.assertEqual(self.run_slice(owner, times=(0., .003, .004)), ["acquire", "release"])
        self.assertIs(owner.pending, jobs)
        self.assertEqual(next(jobs), ("text", (), {}))
        self.assertEqual((owner.last_ms, owner.max_ms), (4., 4.))

    def test_real_duration_retains_peak_across_shorter_subsequent_turns(self):
        owner = GpuWarmupQueue(iter([("text", (), {}), ("text", (), {})]))
        self.run_slice(owner, times=(0., .001, .025))
        self.assertEqual((owner.last_ms, owner.max_ms), (25., 25.))
        self.run_slice(owner, times=(0., .001, .002))
        self.assertEqual((owner.last_ms, owner.max_ms), (2., 25.))

    def test_admitted_snapshot_is_used_when_live_queue_was_replaced_by_a_gate(self):
        old = iter([("text", ("old",), {})])
        replacement = iter([("text", ("new",), {})])
        owner = GpuWarmupQueue(old)
        admitted = owner.pending
        owner.replace(replacement)
        self.assertEqual(self.run_slice(owner, admitted), ["acquire", ("text", ("old",), {}), "release"])
        self.assertIs(owner.pending, replacement)
        self.assertEqual(next(replacement), ("text", ("new",), {}))

    def test_successful_upload_may_replace_pending_queue_without_losing_new_work(self):
        owner = GpuWarmupQueue(iter([("text", (), {})]))
        replacement = iter([("image", (), {})])
        self.run_slice(owner, upload=lambda: owner.replace(replacement))
        self.assertIs(owner.pending, replacement)

    def test_upload_failure_clears_pending_before_error_port_and_releases_context(self):
        owner = GpuWarmupQueue(iter([("text", (), {})]))
        failure = TypeError("upload failed")
        reported = []
        def upload():
            raise failure
        def report(error):
            self.assertIsNone(owner.pending)
            reported.append(error)
        events = self.run_slice(owner, upload=upload, report=report)
        self.assertEqual(reported, [failure])
        self.assertEqual(events[-1], "release")
        self.assertEqual(owner.last_ms, 4.)

    def test_release_failure_propagates_and_does_not_overwrite_previous_measurements(self):
        owner = GpuWarmupQueue(iter([("text", (), {})]), 2., 8.)
        failure = RuntimeError("release failed")
        def release():
            raise failure
        with self.assertRaises(RuntimeError) as caught:
            self.run_slice(owner, release=release)
        self.assertIs(caught.exception, failure)
        self.assertEqual((owner.last_ms, owner.max_ms), (2., 8.))
        self.assertIsNotNone(owner.pending)

    def test_exact_field_aliases_roundtrip_and_forbid_unrelated_state(self):
        class Host:
            pending = GpuWarmupField("pending")
            last = GpuWarmupField("last_ms")
            def __init__(self):
                self._gpu_warmup = GpuWarmupQueue()
        first, second = Host(), Host()
        jobs = iter([])
        first.pending = jobs
        self.assertIs(first._gpu_warmup.pending, jobs)
        first._gpu_warmup.last_ms = 12.
        self.assertEqual(first.last, 12.)
        self.assertIsNone(second.pending)
        self.assertNotIn("pending", first.__dict__)
        self.assertIsInstance(Host.pending, GpuWarmupField)
        with self.assertRaises(ValueError):
            GpuWarmupField("backend")


if __name__ == "__main__":
    unittest.main()
