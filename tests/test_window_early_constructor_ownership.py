"""Real early-window failure prefixes, with playback and device starts disabled.

The production local field-log worker may start and retire through its real idle
path. No universal shutdown is invoked on an incompletely constructed window.
Qt multimedia service initialization remains possible; this is not driver QA.
"""
from contextlib import ExitStack
from pathlib import Path
import sys
import threading
import unittest
import weakref
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
               str(Path(__file__).resolve().parent)]
import test_early_window_factory_failures as fixtures
from PySide6.QtCore import QObject
import shiboken6
from rm_client.ui import polar_app


class EarlyWindowConstructorOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EarlyWindowFactoryFailureTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.refs = {}
        self.native_names = []
        self.destroyed = []
        self.threads = []
        self.start_names = []

    def observe(self, name, owner):
        self.refs[name] = weakref.ref(owner)
        if isinstance(owner, QObject):
            self.native_names.append(name)
            owner.destroyed.connect(lambda *_, label=name: self.destroyed.append(label))
        return owner

    def wrapped(self, name, original):
        def construct(*args, **kwargs):
            owner = original(*args, **kwargs)
            if name in ("camera", "receiver", "local"):
                self.assertIsNone(owner._thread)
            if name == "receiver":
                self.assertIsNone(owner._socket)
            if name == "local":
                self.assertIsNone(owner._detector)
            return self.observe(name, owner)
        return construct

    def assert_failure_prefix_retires(self, stage):
        assembly = fixtures.assembly
        original_warmup = polar_app.create_warmup_timer
        original_backend = polar_app.BackendManager
        original_feedback = polar_app.create_feedback_assets
        original_wallpaper = polar_app.create_startup_wallpaper
        original_start = threading.Thread.start
        failure = RuntimeError("early constructor rejected at " + stage)

        def warmup(parent, callback):
            self.observe("window", parent)
            timer = original_warmup(parent, callback)
            self.assertFalse(timer.isActive())
            return self.observe("warmup_timer", timer)

        def backend(*args, **kwargs):
            owner = original_backend(*args, **kwargs)
            self.assertIsNone(owner.mqtt.client)
            self.assertIsNone(owner.udp._socket)
            self.assertIsNone(owner.native_decoder._proc)
            self.assertIsNone(owner.controller_angles._thread)
            self.observe("field_log", owner.field_log)
            return self.observe("backend", owner)

        def feedback(paths):
            result = original_feedback(paths)
            self.assertIsNone(result.kill_fx.sounds._executor)
            self.observe("kill_pool", result.kill_fx.sounds)
            self.observe("ui_pool", result.ui_audio.pool)
            return result

        def wallpaper(paths):
            owner = original_wallpaper(paths)
            self.assertIsNone(owner.video_player._thread)
            self.assertEqual(owner.thumbnail_executor._threads, set())
            self.observe("wallpaper_executor", owner.thumbnail_executor)
            self.observe("wallpaper_video", owner.video_player)
            return self.observe("wallpaper", owner)

        def start(worker):
            self.start_names.append(worker.name)
            if worker.name != "polar-field-log":
                raise AssertionError("unexpected native/device worker admission")
            self.threads.append(worker)
            return original_start(worker)

        def reject(*args, **kwargs):
            raise failure

        target, name = {
            "config": (polar_app, "ClientConfigStore"),
            # These failures are inside the real aggregate factories, after
            # earlier service arguments have already been constructed.
            "vision": (assembly, "TongjiVisionBridge"),
            "presentation": (assembly, "MinecraftCombatHudRenderer"),
        }[stage]
        try:
            with ExitStack() as stack:
                for module, attr, replacement in (
                    (polar_app, "create_warmup_timer", warmup),
                    (polar_app, "BackendManager", backend),
                    (polar_app, "create_feedback_assets", feedback),
                    (polar_app, "create_startup_wallpaper", wallpaper),
                    (polar_app, "CameraCaptureWorker", self.wrapped("camera", polar_app.CameraCaptureWorker)),
                    (assembly, "TongjiVisionReceiver", self.wrapped("receiver", assembly.TongjiVisionReceiver)),
                    (assembly, "LocalAlignedVision", self.wrapped("local", assembly.LocalAlignedVision)),
                    (assembly, "JelloClickGuiRenderer", self.wrapped("jello", assembly.JelloClickGuiRenderer)),
                    (threading.Thread, "start", start),
                    (target, name, reject),
                ):
                    stack.enter_context(patch.object(module, attr, replacement))
                with self.assertRaises(RuntimeError) as caught:
                    polar_app.PolarRMWindow()
                self.assertIs(caught.exception, failure)
            # The log owns only its local queued writes and self-retires. Do
            # not force-close it before observing the production idle path.
            for worker in self.threads:
                worker.join(3.0)
            fixtures.drain()
            self.assertTrue(self.threads, "probe must observe the actual early log worker")
            self.assertEqual(set(self.start_names), {"polar-field-log"})
            self.assertFalse(any(worker.is_alive() for worker in self.threads))
            retained = [name for name, ref in self.refs.items() if ref() is not None]
            self.assertEqual(retained, [], "early service/window owners retained after unwind")
            self.assertCountEqual(self.destroyed, self.native_names)
            self.assertIn("backend", self.refs)
            if stage != "config":
                self.assertIn("receiver", self.refs)
                self.assertIn("local", self.refs)
            if stage == "presentation":
                self.assertIn("wallpaper_executor", self.refs)
                self.assertIn("jello", self.refs)
        finally:
            # Targeted rescue only after observation, never call window close
            # or its full shutdown routine against a partial constructor.
            logger = self.refs.get("field_log", lambda: None)()
            if logger is not None:
                logger.close(timeout=3.0)
            window = self.refs.get("window", lambda: None)()
            if window is not None and shiboken6.isValid(window):
                window.deleteLater()
            fixtures.drain()

    def test_configuration_constructor_rejection_retires_prior_window_owners(self):
        self.assert_failure_prefix_retires("config")

    def test_partial_vision_factory_failure_retires_receiver_and_local_owners(self):
        self.assert_failure_prefix_retires("vision")

    def test_partial_presentation_failure_retires_prior_wallpaper_and_signal_owners(self):
        self.assert_failure_prefix_retires("presentation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
