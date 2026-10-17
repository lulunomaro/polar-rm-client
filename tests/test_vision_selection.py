"""Source/stop policy with typed inert worker boundaries and no GUI imports."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.vision_selection import VisionSelection, VisionSelectionField


class Worker:
    def __init__(self, name, events, *, running=True):
        self.name, self.events, self.running = name, events, running
        self.failure = None

    def stop(self, *, wait=True):
        self.events.append((self.name, wait))
        if self.failure is not None:
            raise self.failure
        self.running = False


class VisionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.local, self.receiver, self.bridge = [Worker(name, self.events) for name in ("local", "receiver", "bridge")]
        self.owner = VisionSelection(self.local, self.receiver, self.bridge)

    def test_construction_is_inert_and_keeps_exact_worker_owners(self):
        self.assertEqual(self.owner.workers(), (("local", self.local), ("receiver", self.receiver), ("bridge", self.bridge)))
        self.assertEqual(self.events, [])
        self.assertEqual((self.owner.source, self.owner.tongji_enabled), ("Mock", False))

    def test_source_spellings_are_case_sensitive_and_unknown_values_use_local(self):
        for value in ("Local", "Tongji", "Auto", "Mock", "local", "AUTO", " Mock ", "unknown", ""):
            with self.subTest(value=value):
                self.owner.select_source(value)
                self.assertEqual(self.owner.source, value if value in ("Local", "Tongji", "Auto", "Mock") else "Local")
        self.assertEqual(self.events, [])

    def test_source_publication_precedes_enabled_update(self):
        self.owner.tongji_enabled = True
        self.owner.select_source("Mock")
        self.assertEqual((self.owner.source, self.owner.tongji_enabled), ("Mock", True))
        self.owner.set_bridge_enabled(True)
        self.assertEqual((self.owner.source, self.owner.tongji_enabled), ("Mock", False))

    def test_receiver_and_local_requests_match_selection_enabled_and_listen_matrix(self):
        for source in ("Local", "Tongji", "Auto", "Mock"):
            for enabled in (False, True):
                for listen in (False, True):
                    with self.subTest(source=source, enabled=enabled, listen=listen):
                        self.owner.select_source(source)
                        self.owner.set_bridge_enabled(enabled)
                        self.assertEqual(self.owner.receiver_requested(listen), source in ("Tongji", "Auto") and enabled and listen)
                        self.assertEqual(self.owner.local_enabled(enabled), source in ("Local", "Auto") and enabled)

    def test_disabled_capture_publishes_mock_but_does_not_claim_native_release(self):
        self.owner.source, self.owner.tongji_enabled = "Auto", True
        self.owner.disable_capture()
        self.assertEqual((self.owner.source, self.owner.tongji_enabled), ("Mock", False))
        self.assertTrue(all(worker.running for _, worker in self.owner.workers()))
        self.assertEqual(self.events, [])

    def test_stop_plan_always_revokes_legacy_bridge_first(self):
        for receiver in (True, False):
            for local in (True, False):
                with self.subTest(receiver=receiver, local=local):
                    plan = self.owner.unrequested_workers(receiver_requested=receiver, local_requested=local)
                    expected = [("bridge", self.bridge)]
                    if not receiver:
                        expected.append(("receiver", self.receiver))
                    if not local:
                        expected.append(("local", self.local))
                    self.assertEqual(plan, expected)
        self.assertEqual(self.events, [])

    def test_captured_owner_snapshot_survives_replacement_before_revocation(self):
        snapshot = self.owner.workers()
        replacement = Worker("replacement", self.events)
        self.owner.local = replacement
        failure = self.owner.stop_workers(snapshot, report_failure=lambda *args: self.fail("unexpected failure"))
        self.assertIsNone(failure)
        self.assertEqual(self.events, [("local", False), ("receiver", False), ("bridge", False)])
        self.assertFalse(self.local.running)
        self.assertTrue(replacement.running)
        self.assertIs(self.owner.workers()[0][1], replacement)

    def test_inactive_workers_are_not_stopped_or_reported(self):
        self.local.running = self.bridge.running = False
        result = self.owner.stop_workers(self.owner.workers(), report_failure=lambda *args: self.fail("unexpected failure"))
        self.assertIsNone(result)
        self.assertEqual(self.events, [("receiver", False)])

    def test_each_failed_stop_leaves_other_revocations_independent(self):
        for index in range(3):
            with self.subTest(index=index):
                self.events.clear()
                workers = [worker for _, worker in self.owner.workers()]
                for worker in workers:
                    worker.running, worker.failure = True, None
                failure = TypeError("stop failed")
                workers[index].failure = failure
                reports = []
                result = self.owner.stop_workers(self.owner.workers(), report_failure=lambda *args: reports.append(args))
                self.assertIs(result, failure)
                self.assertEqual(self.events, [("local", False), ("receiver", False), ("bridge", False)])
                self.assertEqual(reports, [(workers[index].name, failure)])
                self.assertTrue(all(not worker.running for i, worker in enumerate(workers) if i != index))

    def test_all_errors_are_reported_after_stops_and_first_error_identity_is_preserved(self):
        errors = [TypeError(name) for name in ("local", "receiver", "bridge")]
        for (_, worker), error in zip(self.owner.workers(), errors):
            worker.failure = error
        reports = []
        def report(name, error):
            self.assertEqual(len(self.events), 3)
            reports.append((name, error))
        result = self.owner.stop_workers(self.owner.workers(), report_failure=report)
        self.assertIs(result, errors[0])
        self.assertEqual(reports, list(zip(("local", "receiver", "bridge"), errors)))

    def test_unhandled_sink_failure_cannot_skip_any_native_revocation(self):
        self.local.failure = TypeError("stop failed")
        sink_error = ValueError("sink failed")
        def report(name, error):
            raise sink_error
        with self.assertRaises(ValueError) as caught:
            self.owner.stop_workers(self.owner.workers(), report_failure=report)
        self.assertIs(caught.exception, sink_error)
        self.assertEqual(self.events, [("local", False), ("receiver", False), ("bridge", False)])

    def test_fixed_aliases_write_through_without_shadow_fields(self):
        class Host:
            local_vision = VisionSelectionField("local")
            tongji_receiver = VisionSelectionField("receiver")
            tongji_bridge = VisionSelectionField("bridge")
            _vision_source = VisionSelectionField("source")
            _tongji_enabled = VisionSelectionField("tongji_enabled")
        host = Host()
        host._vision_selection = self.owner
        self.assertIs(host.local_vision, self.local)
        self.assertIs(host.tongji_receiver, self.receiver)
        self.assertIs(host.tongji_bridge, self.bridge)
        host._vision_source, host._tongji_enabled = "Auto", True
        self.assertEqual((self.owner.source, self.owner.tongji_enabled), ("Auto", True))
        replacement = Worker("replacement", self.events)
        host.local_vision = replacement
        self.assertIs(self.owner.local, replacement)
        self.assertEqual(set(host.__dict__), {"_vision_selection"})
        with self.assertRaises(ValueError):
            VisionSelectionField("registry")

    def test_policy_import_has_no_qt_native_model_or_backend_dependency(self):
        source = '''
import importlib.abc
import sys
class RejectRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("PySide6", "cv2", "rm_client.ui.polar_app", "rm_client.vision",
                                "rm_client.backend", "rm_client.model", "rm_client.transports",
                                "rm_client.tongji_bridge", "rm_client.tongji_yolo")):
            raise AssertionError("unexpected runtime import: " + fullname)
sys.meta_path.insert(0, RejectRuntime())
from rm_client.ui.polar.vision_selection import VisionSelection
assert callable(VisionSelection)
'''
        result = subprocess.run([sys.executable, "-c", source],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
