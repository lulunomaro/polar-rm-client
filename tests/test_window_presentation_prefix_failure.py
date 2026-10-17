"""Full presentation ownership when the next GPU factory rejects admission.

Actual offscreen factories, with playback/source loading and capture disabled.
Native multimedia initialization and process-wide font registration can occur.
No GPU service factory or native GL initialization runs in this failure prefix.
"""
from contextlib import ExitStack
from dataclasses import fields
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import test_window_early_constructor_ownership as ownership
from rm_client.ui import polar_app
import shiboken6


class PresentationPrefixFailureTests(unittest.TestCase):
    def setUp(self):
        self.probe = ownership.EarlyWindowConstructorOwnershipTests()
        self.addCleanup(self.probe.doCleanups)
        self.probe.setUp()

    def test_gpu_factory_rejection_retires_completed_presentation_owners(self):
        probe = self.probe
        original_warmup = polar_app.create_warmup_timer
        original_backend = polar_app.BackendManager
        original_feedback = polar_app.create_feedback_assets
        original_wallpaper = polar_app.create_startup_wallpaper
        original_presentation = polar_app.create_presentation_services
        original_vision = polar_app.create_vision_services
        original_start = threading.Thread.start
        failure = RuntimeError("GPU factory admission rejected")
        phases = []

        def warmup(parent, callback):
            probe.observe("window", parent)
            timer = original_warmup(parent, callback)
            self.assertFalse(timer.isActive())
            return probe.observe("warmup_timer", timer)

        def backend(*args, **kwargs):
            owner = original_backend(*args, **kwargs)
            self.assertIsNone(owner.mqtt.client)
            self.assertIsNone(owner.udp._socket)
            self.assertIsNone(owner.native_decoder._proc)
            probe.observe("field_log", owner.field_log)
            return probe.observe("backend", owner)

        def feedback(paths):
            owners = original_feedback(paths)
            self.assertIsNone(owners.kill_fx.sounds._executor)
            probe.observe("kill_pool", owners.kill_fx.sounds)
            probe.observe("ui_pool", owners.ui_audio.pool)
            return owners

        def vision(**kwargs):
            owners = original_vision(**kwargs)
            self.assertIsNone(owners.receiver._thread)
            self.assertIsNone(owners.receiver._socket)
            self.assertIsNone(owners.local._thread)
            self.assertIsNone(owners.local._detector)
            for field in fields(owners):
                probe.observe("vision." + field.name, getattr(owners, field.name))
            return owners

        def wallpaper(paths):
            owner = original_wallpaper(paths)
            self.assertIsNone(owner.video_player._thread)
            self.assertEqual(owner.thumbnail_executor._threads, set())
            probe.observe("wallpaper_executor", owner.thumbnail_executor)
            probe.observe("wallpaper_video", owner.video_player)
            return probe.observe("wallpaper", owner)

        def presentation(paths):
            owners = original_presentation(paths)
            self.assertFalse(owners.clickgui_gpu.available)
            for field in fields(owners):
                probe.observe("presentation." + field.name, getattr(owners, field.name))
            phases.append("presentation complete")
            return owners

        def reject_gpu():
            self.assertEqual(phases, ["presentation complete"])
            phases.append("GPU rejected")
            raise failure

        def start(worker):
            probe.start_names.append(worker.name)
            if worker.name != "polar-field-log":
                raise AssertionError("unexpected device worker admission")
            probe.threads.append(worker)
            return original_start(worker)

        try:
            with ExitStack() as stack:
                for target, name, replacement in (
                    (polar_app, "create_warmup_timer", warmup),
                    (polar_app, "BackendManager", backend),
                    (polar_app, "create_feedback_assets", feedback),
                    (polar_app, "create_vision_services", vision),
                    (polar_app, "create_startup_wallpaper", wallpaper),
                    (polar_app, "create_presentation_services", presentation),
                    (polar_app, "create_gpu_services", reject_gpu),
                    (polar_app, "CameraCaptureWorker", probe.wrapped("camera", polar_app.CameraCaptureWorker)),
                    (threading.Thread, "start", start),
                ):
                    stack.enter_context(patch.object(target, name, replacement))
                with self.assertRaises(RuntimeError) as caught:
                    polar_app.PolarRMWindow()
                self.assertIs(caught.exception, failure)
            failure.__traceback__ = None
            self.assertEqual(phases, ["presentation complete", "GPU rejected"])
            for worker in probe.threads:
                worker.join(3.0)  # Per worker, not a global shutdown deadline.
            ownership.fixtures.drain()
            self.assertTrue(probe.threads)
            self.assertEqual(set(probe.start_names), {"polar-field-log"})
            self.assertFalse(any(worker.is_alive() for worker in probe.threads))
            retained = [name for name, ref in probe.refs.items() if ref() is not None]
            self.assertEqual(retained, [], "completed presentation-prefix owners retained")
            self.assertCountEqual(probe.destroyed, probe.native_names)
            self.assertEqual(set(probe.native_names), {"window", "warmup_timer", "kill_pool", "ui_pool"})
            for name in ("student_state", "student_renderer", "chat_renderer", "hero_state", "hero_renderer"):
                self.assertIn("presentation." + name, probe.refs)
        finally:
            failure.__traceback__ = None
            logger = probe.refs.get("field_log", lambda: None)()
            if logger is not None:
                logger.close(3.0)
            window = probe.refs.get("window", lambda: None)()
            if window is not None and shiboken6.isValid(window):
                window.deleteLater()
            ownership.fixtures.drain()


if __name__ == "__main__":
    unittest.main()
