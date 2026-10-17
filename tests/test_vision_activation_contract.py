"""Freeze real vision admission order, source publication and failure prefixes."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_window_vision_runtime import WindowVisionRuntimeFixture


class VisionActivationContractTests(WindowVisionRuntimeFixture):
    def exercise(self, *, source="Auto", override=None, enabled=True, listen=True,
                 model_exists=True, disabled="0", failure_at=None, closing=False,
                 replace_worker_at=None):
        window, events = self.window, []
        window._vision_source, window._tongji_enabled = "prior", True
        bridge = window.registry.get("TongjiVisionBridge")
        bridge.enabled = enabled
        window._vision_source, window._tongji_enabled = "prior", True
        for worker in self.owners:
            worker.running = True
        failure = ValueError("vision admission stage failed")
        def record(name):
            events.append((name, window._vision_source, window._tongji_enabled))
            if name == failure_at:
                raise failure
        def mode(module, name, default):
            record(name)
            return {"Source": "Webcam", "Vision Source": source, "Local Device": "CPU"}[name]
        def boolean(module, name, default):
            self.assertEqual(name, "Listen")
            record(name)
            return listen
        def number(module, name, default):
            record(name)
            if name == replace_worker_at:
                replacement = SimpleNamespace(
                    configure=lambda *args, **kwargs: record("configure-replacement"),
                    configure_prediction=lambda *args: record("prediction-replacement"),
                    start=lambda: record("start-replacement"),
                )
                if name == "UDP Port":
                    window.tongji_receiver = replacement
                else:
                    window.local_vision = replacement
            return {"UDP Port": 9901, "Armor Count": 4, "Radius M": .23, "Predict Horizon": .06}[name]
        class ModelPath:
            def exists(self):
                record("model-exists")
                return model_exists
        model = ModelPath()
        def asset(*parts):
            self.assertEqual(parts, ("同济自瞄", "sp_vision_25", "assets", "yolov5.xml"))
            record("model-path")
            return model
        def report(stage, callback):
            record(stage)
            try:
                callback()
            except Exception as error:
                self.assertIs(error, failure)
                return False
            self.fail("diagnostic stage did not retain its stop failure")
        def stop(name, worker):
            original = worker.stop
            def revoke(*, wait):
                self.assertIs(wait, False)
                record("stop-" + name)
                original(wait=False)
            return revoke
        def configure_receiver(port, *, wait):
            self.assertEqual(port, 9901)
            self.assertIs(wait, False)
            record("configure-receiver")
        def configure_local(path, device, *, wait):
            self.assertIs(path, model)
            self.assertEqual(device, "CPU")
            self.assertIs(wait, False)
            record("configure-local")
        def prediction(count, radius, horizon):
            self.assertEqual((count, radius, horizon), (4, .23, .06))
            record("prediction")
        with ExitStack() as stack:
            stack.callback(setattr, window, "local_vision", self.owners[0])
            stack.callback(setattr, window, "tongji_receiver", self.owners[1])
            environment = {"POLAR_DISABLE_LOCAL_CAPTURE": disabled}
            if override is not None:
                environment["POLAR_VISION_SOURCE"] = override
            stack.enter_context(patch.dict(os.environ, environment))
            if override is None:
                os.environ.pop("POLAR_VISION_SOURCE", None)
            stack.enter_context(patch.object(window._window_lifecycle, "closing", closing))
            stack.enter_context(patch.object(window, "setting_mode", mode))
            stack.enter_context(patch.object(window, "setting_bool", boolean))
            stack.enter_context(patch.object(window, "get_setting_number", number))
            stack.enter_context(patch.object(window, "_client_asset_path", asset))
            stack.enter_context(patch.object(window, "_run_tick_stage", report))
            stack.enter_context(patch.object(window.backend, "set_active_video_source", lambda value: record("backend-source")))
            for name, worker in zip(("local", "receiver", "bridge"), self.owners):
                stack.enter_context(patch.object(worker, "stop", stop(name, worker)))
            stack.enter_context(patch.object(window.tongji_receiver, "configure", configure_receiver))
            stack.enter_context(patch.object(window.tongji_receiver, "start", lambda: record("start-receiver")))
            stack.enter_context(patch.object(window.local_vision, "configure", configure_local))
            stack.enter_context(patch.object(window.local_vision, "configure_prediction", prediction))
            stack.enter_context(patch.object(window.local_vision, "start", lambda: record("start-local")))
            if failure_at is None:
                window.refresh_vision_runtime()
            else:
                with self.assertRaises(ValueError) as caught:
                    window.refresh_vision_runtime()
                self.assertIs(caught.exception, failure)
        return events

    def test_constructor_and_legacy_aliases_share_one_selection_owner(self):
        owner = self.window._vision_selection
        self.assertIs(owner.local, self.window.local_vision)
        self.assertIs(owner.receiver, self.window.tongji_receiver)
        self.assertIs(owner.bridge, self.window.tongji_bridge)
        self.exercise()
        self.assertEqual((owner.source, owner.tongji_enabled), ("Auto", True))
        for name in ("local_vision", "tongji_receiver", "tongji_bridge", "_vision_source", "_tongji_enabled"):
            self.assertNotIn(name, self.window.__dict__)

    def test_auto_reads_and_activates_receiver_before_local_configuration(self):
        events = self.exercise()
        self.assertEqual([row[0] for row in events], [
            "Source", "backend-source", "Vision Source", "Listen", "model-path", "model-exists",
            "stop-bridge", "UDP Port", "configure-receiver", "start-receiver", "Local Device",
            "configure-local", "Armor Count", "Radius M", "Predict Horizon", "prediction", "start-local",
        ])
        self.assertEqual([row[1:] for row in events[:3]], [("prior", True)] * 3)
        self.assertTrue(all(row[1:] == ("Auto", True) for row in events[3:]))

    def test_selection_matrix_stops_every_unrequested_owner_before_any_start(self):
        for source in ("Local", "Tongji", "Auto", "Mock"):
            for enabled in (True, False):
                for listen in (True, False):
                    for exists in (True, False):
                        with self.subTest(source=source, enabled=enabled, listen=listen, exists=exists):
                            events = self.exercise(source=source, enabled=enabled, listen=listen, model_exists=exists)
                            names = [row[0] for row in events]
                            receiver = source in ("Tongji", "Auto") and enabled and listen
                            local = source in ("Local", "Auto") and enabled and exists
                            stops = ["stop-bridge"] + ([] if receiver else ["stop-receiver"]) + ([] if local else ["stop-local"])
                            self.assertEqual([name for name in names if name.startswith("stop-")], stops)
                            self.assertEqual("start-receiver" in names, receiver)
                            self.assertEqual("start-local" in names, local)
                            if receiver or local:
                                self.assertLess(max(names.index(name) for name in stops),
                                                min(index for index, name in enumerate(names) if name.startswith("start-")))
                            self.assertEqual("model-exists" in names, source in ("Local", "Auto") and enabled)

    def test_environment_override_still_observes_default_setting_before_selection(self):
        events = self.exercise(source="Mock", override="Tongji")
        self.assertEqual(events[2], ("Vision Source", "prior", True))
        self.assertEqual(self.window._vision_source, "Tongji")
        self.assertTrue(self.window._tongji_enabled)
        self.assertNotIn("start-local", [row[0] for row in events])

    def test_unknown_source_normalizes_to_local_before_listen_or_path_reads(self):
        events = self.exercise(source="unknown")
        self.assertEqual(events[3], ("Listen", "Local", False))
        self.assertEqual(self.window._vision_source, "Local")
        self.assertFalse(self.window._tongji_enabled)

    def test_disabled_capture_selects_mock_before_revoking_all_workers(self):
        events = self.exercise(disabled="TRUE")
        self.assertEqual([row[0] for row in events], ["Source", "backend-source", "stop-local", "stop-receiver", "stop-bridge"])
        self.assertTrue(all(row[1:] == ("Mock", False) for row in events[2:]))

    def test_source_read_and_backend_failure_revoke_all_without_publishing_selection(self):
        for stage in ("Source", "backend-source"):
            with self.subTest(stage=stage):
                events = self.exercise(failure_at=stage)
                self.assertEqual([row[0] for row in events][-3:], ["stop-local", "stop-receiver", "stop-bridge"])
                self.assertTrue(all(row[1:] == ("prior", True) for row in events))

    def test_default_setting_failure_remains_visible_even_when_environment_overrides_it(self):
        events = self.exercise(override="Mock", failure_at="Vision Source")
        self.assertEqual(events[-1], ("Vision Source", "prior", True))
        self.assertNotIn("stop-bridge", [row[0] for row in events])

    def test_selection_stays_published_when_listen_or_model_lookup_fails(self):
        for stage in ("Listen", "model-path", "model-exists"):
            with self.subTest(stage=stage):
                events = self.exercise(failure_at=stage)
                self.assertEqual(events[-1], (stage, "Auto", True))
                self.assertNotIn("stop-bridge", [row[0] for row in events])

    def test_disabled_stop_failure_reports_after_independent_revocations(self):
        for owner in ("local", "receiver", "bridge"):
            with self.subTest(owner=owner):
                events = self.exercise(disabled="1", failure_at="stop-" + owner)
                self.assertEqual([row[0] for row in events][-4:],
                                 ["stop-local", "stop-receiver", "stop-bridge", "vision.stop." + owner])
                self.assertTrue(all(row[1:] == ("Mock", False) for row in events[2:]))

    def test_unrequested_stop_failure_prevents_any_replacement_start(self):
        events = self.exercise(source="Local", failure_at="stop-bridge")
        self.assertEqual([row[0] for row in events][-3:], ["stop-bridge", "stop-receiver", "vision.stop.bridge"])
        self.assertNotIn("start-local", [row[0] for row in events])

    def test_receiver_fault_prevents_all_local_setting_reads(self):
        for stage in ("UDP Port", "configure-receiver", "start-receiver"):
            with self.subTest(stage=stage):
                events = self.exercise(failure_at=stage)
                self.assertEqual(events[-1], (stage, "Auto", True))
                self.assertNotIn("Local Device", [row[0] for row in events])

    def test_local_configuration_and_prediction_faults_preserve_receiver_activation(self):
        for stage in ("Local Device", "configure-local", "Armor Count", "Radius M", "Predict Horizon", "prediction", "start-local"):
            with self.subTest(stage=stage):
                events = self.exercise(failure_at=stage)
                self.assertEqual(events[-1], (stage, "Auto", True))
                self.assertIn("start-receiver", [row[0] for row in events])

    def test_receiver_configuration_is_bound_before_reentrant_port_read(self):
        events = self.exercise(replace_worker_at="UDP Port")
        names = [row[0] for row in events]
        port_index = names.index("UDP Port")
        self.assertEqual(names[port_index:port_index + 3],
                         ["UDP Port", "configure-receiver", "start-replacement"])
        self.assertNotIn("configure-replacement", names)
        self.assertNotIn("start-receiver", names)

    def test_local_prediction_is_bound_before_reentrant_overlay_reads(self):
        events = self.exercise(replace_worker_at="Armor Count")
        names = [row[0] for row in events]
        self.assertEqual(names[-5:],
                         ["Armor Count", "Radius M", "Predict Horizon", "prediction", "start-replacement"])
        self.assertNotIn("prediction-replacement", names)
        self.assertNotIn("start-local", names)

    def test_closing_gate_reads_no_settings_and_does_not_mutate_selection(self):
        self.assertEqual(self.exercise(closing=True), [])
        self.assertEqual((self.window._vision_source, self.window._tongji_enabled), ("prior", True))


if __name__ == "__main__":
    unittest.main()
