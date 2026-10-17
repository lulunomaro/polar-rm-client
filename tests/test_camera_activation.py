"""Camera admission policy through explicit ports, without GUI or devices."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.camera_activation import CameraActivation, CameraActivationField


class Worker:
    def __init__(self, events, *, running=False, accepted=True, fail=None):
        self.events = events
        self.running = running
        self.accepted = accepted
        self.fail = fail
        self.last_error = "old diagnostic"
        self.error = TypeError("worker admission fault")

    def stop(self, *, wait=True):
        self.events.append(("stop", wait))
        if self.fail == "stop":
            raise self.error
        self.running = False

    def configure(self, camera_id, size):
        self.events.append(("configure", camera_id, size))
        if self.fail == "configure":
            raise self.error

    def start(self):
        self.events.append(("start",))
        if self.fail == "start":
            raise self.error
        return self.accepted


class CameraActivationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.worker = Worker(self.events)
        self.owner = CameraActivation(self.worker)

    def ensure(self, requested=(5, 640, 480), *, fail=None):
        def effect(name):
            self.events.append((name,))
            if name == fail:
                raise self.worker.error
        self.owner.ensure(requested, bind_frame_sink=lambda: effect("bind"),
                          discard_frame=lambda: effect("discard"))

    def test_construction_is_inert_and_owns_exact_worker(self):
        self.assertIs(self.owner.worker, self.worker)
        self.assertEqual(self.events, [])
        self.assertFalse(self.owner.started)
        self.assertIsNone(self.owner.configuration)

    def test_initial_admission_configures_binds_then_commits_accepted_configuration(self):
        self.ensure()
        self.assertEqual(self.events, [("configure", 5, (640, 480)), ("bind",), ("start",)])
        self.assertTrue(self.owner.started)
        self.assertEqual(self.owner.configuration, (5, 640, 480))

    def test_rejected_start_does_not_publish_configuration_as_active(self):
        self.owner.configuration = (2, 800, 600)
        self.worker.accepted = False
        self.ensure()
        self.assertFalse(self.owner.started)
        self.assertEqual(self.owner.configuration, (2, 800, 600))

    def test_matching_live_capture_has_no_native_or_frame_effect(self):
        self.owner.started = self.worker.running = True
        self.owner.configuration = (5, 640, 480)
        self.ensure()
        self.assertEqual(self.events, [])

    def test_dead_capture_is_reconfigured_without_clearing_last_frame(self):
        self.owner.started = True
        self.owner.configuration = (2, 800, 600)
        self.ensure()
        self.assertEqual(self.events, [("configure", 5, (640, 480)), ("bind",), ("start",)])

    def test_changed_live_capture_revokes_before_discard_configure_and_start(self):
        self.owner.started = self.worker.running = True
        self.ensure()
        self.assertEqual(self.events, [("stop", False), ("discard",),
                                      ("configure", 5, (640, 480)), ("bind",), ("start",)])

    def test_stop_failure_preserves_admission_until_it_is_actually_revoked(self):
        self.owner.started = self.worker.running = True
        self.worker.fail = "stop"
        with self.assertRaises(TypeError) as caught:
            self.ensure()
        self.assertIs(caught.exception, self.worker.error)
        self.assertTrue(self.owner.started)
        self.assertIsNone(self.owner.configuration)
        self.assertEqual(self.events, [("stop", False)])

    def test_each_post_stop_fault_keeps_old_config_and_revoked_flag(self):
        for stage in ("discard", "configure", "bind", "start"):
            with self.subTest(stage=stage):
                self.events.clear()
                self.owner.started = self.worker.running = True
                self.owner.configuration = (2, 800, 600)
                self.worker.fail = stage
                with self.assertRaises(TypeError) as caught:
                    self.ensure(fail=stage)
                self.assertIs(caught.exception, self.worker.error)
                self.assertFalse(self.owner.started)
                self.assertEqual(self.owner.configuration, (2, 800, 600))
                self.assertEqual(self.events[-1][0], stage)

    def test_suspension_uses_running_gate_and_preserves_config(self):
        self.worker.running = True
        self.owner.configuration = (2, 800, 600)
        self.owner.suspend(reason="local capture disabled")
        self.assertEqual(self.events, [("stop", False)])
        self.assertFalse(self.owner.started)
        self.assertEqual(self.owner.configuration, (2, 800, 600))
        self.assertEqual(self.worker.last_error, "local capture disabled")

    def test_suspension_without_reason_does_not_erase_worker_diagnostic(self):
        self.owner.started = True
        self.owner.suspend()
        self.assertEqual(self.events, [])
        self.assertFalse(self.owner.started)
        self.assertEqual(self.worker.last_error, "old diagnostic")

    def test_suspension_stop_failure_keeps_flag_and_diagnostic(self):
        self.owner.started = self.worker.running = True
        self.worker.fail = "stop"
        with self.assertRaises(TypeError):
            self.owner.suspend(reason="disabled")
        self.assertTrue(self.owner.started)
        self.assertEqual(self.worker.last_error, "old diagnostic")

    def test_deploy_pause_uses_admitted_gate_even_when_worker_is_not_running(self):
        self.owner.started = True
        self.owner.pause_admitted_capture()
        self.assertEqual(self.events, [("stop", False)])
        self.assertFalse(self.owner.started)
        self.worker.running = True
        self.owner.pause_admitted_capture()
        self.assertEqual(self.events, [("stop", False)])

    def test_deploy_pause_failure_does_not_claim_revocation(self):
        self.owner.started = True
        self.worker.fail = "stop"
        with self.assertRaises(TypeError):
            self.owner.pause_admitted_capture()
        self.assertTrue(self.owner.started)

    def test_reentrant_worker_replacement_is_observed_by_later_binding_and_start(self):
        replacement = Worker(self.events)
        def configure(camera_id, size):
            self.events.append(("replace-worker",))
            self.owner.worker = replacement
        self.worker.configure = configure
        def bind():
            self.assertIs(self.owner.worker, replacement)
            self.events.append(("bind-replacement",))
        self.owner.ensure((5, 640, 480), bind_frame_sink=bind,
                          discard_frame=lambda: self.fail("no live frame to discard"))
        self.assertEqual(self.events, [("replace-worker",), ("bind-replacement",), ("start",)])
        self.assertTrue(self.owner.started)

    def test_named_aliases_read_and_write_one_owner_without_shadow_state(self):
        class Host:
            camera_worker = CameraActivationField("worker")
            camera_started = CameraActivationField("started")
            _camera_config = CameraActivationField("configuration")
        host = Host()
        host._camera_activation = self.owner
        self.assertIs(host.camera_worker, self.worker)
        host.camera_started = True
        host._camera_config = (2, 800, 600)
        self.assertTrue(self.owner.started)
        self.assertEqual(self.owner.configuration, (2, 800, 600))
        replacement = Worker([])
        host.camera_worker = replacement
        self.assertIs(self.owner.worker, replacement)
        self.assertEqual(set(host.__dict__), {"_camera_activation"})
        with self.assertRaises(ValueError):
            CameraActivationField("backend")

    def test_owner_import_rejects_window_backend_model_and_native_dependencies(self):
        source = '''
import importlib.abc
import sys
class RejectRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("PySide6", "cv2", "rm_client.ui.polar_app", "rm_client.vision",
                                "rm_client.backend", "rm_client.model", "rm_client.transports")):
            raise AssertionError("unexpected runtime import: " + fullname)
sys.meta_path.insert(0, RejectRuntime())
from rm_client.ui.polar.camera_activation import CameraActivation
assert callable(CameraActivation)
'''
        result = subprocess.run([sys.executable, "-c", source],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
