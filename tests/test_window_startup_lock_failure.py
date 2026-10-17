"""Actual startup-lock failure prefix before configuration/activation ownership.

Playback/source loading and capture are disabled; multimedia initialization may
still occur. No universal close is invoked against a partially built window.
"""
from contextlib import ExitStack
import logging
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import test_window_early_constructor_ownership as ownership
from rm_client.control import KeyboardControlBridge
from rm_client.ui import polar_app
import shiboken6


class StartupLockFailureTests(unittest.TestCase):
    def setUp(self):
        self.probe = ownership.EarlyWindowConstructorOwnershipTests()
        self.addCleanup(self.probe.doCleanups)
        self.probe.setUp()

    def check_prefix(self, secondary_failure=False):
        probe = self.probe
        primary = RuntimeError("startup reset rejected")
        secondary = OSError("DT7 cleanup rejected")
        attempts, logs, phases = [], [], []
        original_warmup = polar_app.create_warmup_timer
        original_backend = polar_app.BackendManager
        original_feedback = polar_app.create_feedback_assets
        original_start = threading.Thread.start
        original_dt7 = KeyboardControlBridge.release_direct_dt7
        original_cc = KeyboardControlBridge.release_custom_controller
        original_pending = KeyboardControlBridge.clear_pending

        def warmup(parent, callback):
            probe.observe("window", parent)
            return probe.observe("warmup_timer", original_warmup(parent, callback))

        def backend(*args, **kwargs):
            owner = original_backend(*args, **kwargs)
            probe.observe("field_log", owner.field_log)
            return probe.observe("backend", owner)

        def feedback(paths):
            result = original_feedback(paths)
            probe.observe("kill_pool", result.kill_fx.sounds)
            probe.observe("ui_pool", result.ui_audio.pool)
            return result

        def reset(control):
            probe.observe("control", control)
            attempts.append("reset")
            raise primary

        def dt7(control):
            attempts.append("dt7")
            original_dt7(control)
            if secondary_failure:
                raise secondary

        def cc(control):
            attempts.append("cc")
            return original_cc(control)

        def pending(control):
            attempts.append("pending")
            return original_pending(control)

        def forbidden_config(*args, **kwargs):
            phases.append("config")
            raise AssertionError("configuration must not be reached")

        def start(worker):
            probe.start_names.append(worker.name)
            if worker.name != "polar-field-log":
                raise AssertionError("unexpected device worker admission")
            probe.threads.append(worker)
            return original_start(worker)

        # Do not retain LogRecords: their exc_info would artificially root the
        # partial window through the helper frame. Keep only diagnostic text.
        class MessagesOnly(logging.Handler):
            def emit(self, record):
                logs.append(record.getMessage())
        logger = logging.getLogger("rm_client.ui.polar.mixins.input_dispatch_mixin")
        old_handlers, old_propagate = logger.handlers[:], logger.propagate
        logger.handlers = [MessagesOnly()]
        logger.propagate = False
        try:
            with ExitStack() as stack:
                for target, name, replacement in (
                    (polar_app, "create_warmup_timer", warmup),
                    (polar_app, "BackendManager", backend),
                    (polar_app, "create_feedback_assets", feedback),
                    (polar_app, "CameraCaptureWorker", probe.wrapped("camera", polar_app.CameraCaptureWorker)),
                    (polar_app, "ClientConfigStore", forbidden_config),
                    (KeyboardControlBridge, "reset_inputs", reset),
                    (KeyboardControlBridge, "release_direct_dt7", dt7),
                    (KeyboardControlBridge, "release_custom_controller", cc),
                    (KeyboardControlBridge, "clear_pending", pending),
                    (threading.Thread, "start", start),
                ):
                    stack.enter_context(patch.object(target, name, replacement))
                with self.assertRaises(RuntimeError) as caught:
                    polar_app.PolarRMWindow()
                self.assertIs(caught.exception, primary)
            self.assertEqual(attempts, ["reset", "dt7", "cc", "pending"])
            self.assertEqual(phases, [])
            self.assertEqual(logs, ["Locked input cleanup failed"] * (2 if secondary_failure else 1))
            # Secondary exceptions retain their own traceback even after the
            # original exception is caught. Release both intentional test roots.
            primary.__traceback__ = secondary.__traceback__ = None
            primary.__context__ = secondary.__context__ = None
            for worker in probe.threads:
                worker.join(3.0)  # Per worker, observing its real idle exit.
            ownership.fixtures.drain()
            self.assertTrue(probe.threads)
            self.assertEqual(set(probe.start_names), {"polar-field-log"})
            self.assertFalse(any(worker.is_alive() for worker in probe.threads))
            retained = [name for name, ref in probe.refs.items() if ref() is not None]
            self.assertEqual(retained, [], "early startup-lock owners retained")
            self.assertCountEqual(probe.destroyed, probe.native_names)
            self.assertEqual(set(probe.native_names), {"window", "warmup_timer", "kill_pool", "ui_pool"})
        finally:
            logger.handlers, logger.propagate = old_handlers, old_propagate
            primary.__traceback__ = secondary.__traceback__ = None
            field_log = probe.refs.get("field_log", lambda: None)()
            if field_log is not None:
                field_log.close(3.0)
            window = probe.refs.get("window", lambda: None)()
            if window is not None and shiboken6.isValid(window):
                window.deleteLater()
            ownership.fixtures.drain()

    def test_reset_failure_retires_prior_resources_after_independent_neutrals(self):
        self.check_prefix()

    def test_secondary_neutral_failure_preserves_first_error_and_retirement(self):
        self.check_prefix(secondary_failure=True)


if __name__ == "__main__":
    unittest.main()
