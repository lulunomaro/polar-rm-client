"""Deterministic backend owner/worker races using fake resources only."""
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.backend.manager import BackendManager
from rm_client.control import KeyboardControlBridge
from rm_client.lifecycle import LifecyclePhase, TeardownTask
from rm_client.model import RobotState, build_default_registry
from rm_client.protocol import RefereeFrameCodec


def until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Lifecycle did not settle")
        time.sleep(.001)


class BackendAsyncLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": self.temp.name}):
            self.manager = BackendManager(workspace=self.temp.name, state=RobotState())
        self.releases = []
        self.addCleanup(self.cleanup)
        self.owner = threading.get_ident()
        self.hooks = []
        self.manager.input_clear_hook = lambda reason: self.hooks.append((threading.get_ident(), reason))
        self.starts = []
        for name, resource, method in (("mqtt", self.manager.mqtt, "start"),
                                       ("udp", self.manager.udp, "start"),
                                       ("serial", self.manager.serial, "start"),
                                       ("polar", self.manager.polar, "request_start")):
            setattr(resource, method, lambda name=name: self.starts.append(
                (name, threading.get_ident(), self.manager.scenario_key)))

    def cleanup(self):
        for release in self.releases:
            release.set()
        self.manager.shutdown()
        self.manager.field_log.close(1)

    def block(self, resource, method="stop"):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = getattr(resource, method)
        calls = []
        def blocked():
            calls.append(threading.get_ident())
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release close")
            original()
        setattr(resource, method, blocked)
        return entered, release, calls

    def settle(self):
        results = []
        def finished():
            completion = self.manager.poll_lifecycle()
            if completion is not None:
                results.append(completion)
            return not self.manager.lifecycle_pending
        until(finished)
        return results[-1] if results else None

    def test_request_is_prompt_and_neutralizes_input_on_owner_thread(self):
        control = KeyboardControlBridge(publisher=None, mock_output=True)
        control.last_command = object()
        control.last_payload = b"held W"
        control.dt7_enabled = control.cc_enabled = True
        self.manager._dt7_control_ref = control
        entered, release, calls = self.block(self.manager.udp)
        started = time.perf_counter()
        self.manager.request_scenario("OfficialEvent")
        self.assertLess(time.perf_counter() - started, .05)
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.manager.safety.estop_active)
        self.assertIsNone(control.last_command)
        self.assertEqual(control.last_payload, b"")
        self.assertFalse(control.dt7_enabled or control.cc_enabled)
        self.assertTrue(self.hooks)
        self.assertTrue(all(owner == self.owner for owner, _ in self.hooks))
        self.assertNotEqual(calls[0], self.owner)
        self.assertFalse(self.starts)
        release.set()
        self.assertTrue(self.settle().succeeded)
        self.assertEqual(self.manager.scenario_key, "OfficialEvent")
        self.assertFalse(self.manager.safety.estop_active)
        self.assertEqual(self.manager.safety.control_state.value, "LOCKED")

    def test_many_requests_retain_one_cleanup_and_only_start_latest_target(self):
        entered, release, calls = self.block(self.manager.udp)
        self.manager.request_scenario("OfficialEvent")
        self.assertTrue(entered.wait(1))
        for _ in range(100):
            self.manager.request_scenario("CampusSimulation")
        generation = self.manager.request_scenario("LabDebug")
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.starts)
        release.set()
        completion = self.settle()
        self.assertEqual(completion.generation, generation)
        self.assertTrue(all(scene == "LabDebug" for _, _, scene in self.starts))

    def test_invalid_scenario_settings_fail_closed_without_applying_partial_config(self):
        with patch("rm_client.serial_ports.port_snapshot", return_value=[]):
            registry = build_default_registry()
        registry.get("MQTTLink").get_setting("Port").value = -1
        with self.assertRaises(ValueError):
            self.manager.request_scenario("OfficialEvent", registry=registry)
        self.assertTrue(self.manager.safety.estop_active)
        self.assertTrue(self.manager.lifecycle_pending)
        self.settle()
        self.assertFalse(self.starts)
        self.assertEqual(self.manager.scenario_key, "LabDebug")

    def test_configuration_is_detached_from_later_registry_edits(self):
        with patch("rm_client.serial_ports.port_snapshot", return_value=[]):
            registry = build_default_registry()
        mqtt = registry.get("MQTTLink")
        mqtt.get_setting("Host").value = "chosen.example.invalid"
        mqtt.get_setting("Port").value = 3335
        entered, release, _ = self.block(self.manager.udp)
        self.manager.request_scenario("OfficialEvent", registry=registry)
        self.assertTrue(entered.wait(1))
        mqtt.get_setting("Host").value = "later.example.invalid"
        mqtt.get_setting("Port").value = 3336
        release.set()
        self.settle()
        self.assertEqual((self.manager.mqtt.host, self.manager.mqtt.port),
                         ("chosen.example.invalid", 3335))

    def test_completed_workers_cannot_apply_or_start_without_owner_poll(self):
        entered, release, _ = self.block(self.manager.udp)
        applied = []
        original = self.manager._activate_scenario
        def activate(request):
            applied.append(threading.get_ident())
            original(request)
        self.manager._lifecycle._activate_scenario = activate
        self.manager.request_scenario("OfficialEvent")
        self.assertTrue(entered.wait(1))
        release.set()
        for worker in self.manager._lifecycle._resources._workers.values():
            worker.join(1)
        self.assertFalse(applied)
        self.assertFalse(self.starts)
        self.assertEqual(self.manager.scenario_key, "LabDebug")
        self.settle()
        self.assertEqual(applied, [self.owner])
        self.assertTrue(all(owner == self.owner for name, owner, _ in self.starts if name != "serial"))

    def test_shutdown_supersedes_restart_and_keeps_extra_cleanup_off_owner(self):
        entered, release, _ = self.block(self.manager.udp)
        self.manager.request_scenario("OfficialEvent")
        self.assertTrue(entered.wait(1))
        camera_threads = []
        task = TeardownTask("camera", lambda: camera_threads.append(threading.get_ident()))
        self.manager.request_shutdown(teardown_tasks=(task,))
        self.manager.request_shutdown(teardown_tasks=(task,))
        with self.assertRaises(RuntimeError):
            self.manager.request_scenario("CampusSimulation")
        with self.assertRaises(RuntimeError):
            self.manager.set_client_id(7)
        release.set()
        completion = self.settle()
        self.assertTrue(completion.final)
        self.assertTrue(self.manager.lifecycle_closed)
        self.assertFalse(self.starts)
        self.assertEqual(len(camera_threads), 1)
        self.assertNotEqual(camera_threads[0], self.owner)
        self.assertTrue(self.manager.field_log.snapshot()["closed"])

    def test_failed_close_reports_failure_and_blocks_rearm_or_replacement(self):
        original = self.manager.udp.stop
        self.manager.udp.stop = Mock(side_effect=OSError("driver still owned"))
        self.manager.request_scenario("OfficialEvent")
        completion = self.settle()
        self.assertFalse(completion.succeeded)
        self.assertEqual(self.manager.lifecycle_snapshot.phase, LifecyclePhase.FAILED)
        self.assertFalse(self.starts)
        self.manager.release_estop()
        self.assertTrue(self.manager.safety.estop_active)
        with self.assertRaises(RuntimeError):
            self.manager.begin_qualification(["dt7_selftest"])
        self.manager.udp.stop = original
        self.manager.request_scenario("LabDebug")
        self.assertTrue(self.settle().succeeded)

    def test_apply_failure_is_a_failed_completion_with_no_implicit_rearm(self):
        self.manager._lifecycle._activate_scenario = Mock(side_effect=OSError("config apply failed"))
        self.manager.request_scenario("OfficialEvent")
        completion = self.settle()
        self.assertFalse(completion.succeeded)
        self.assertEqual(completion.failures[-1].resource, "scenario apply")
        self.assertEqual(self.manager.lifecycle_snapshot.phase, LifecyclePhase.FAILED)
        self.manager.release_estop()
        self.assertTrue(self.manager.safety.estop_active)

    def test_operator_estop_during_teardown_is_not_cleared_on_completion(self):
        entered, release, _ = self.block(self.manager.udp)
        self.manager.request_scenario("OfficialEvent")
        self.assertTrue(entered.wait(1))
        self.manager.safety.trigger_estop()
        self.manager.request_scenario("CampusSimulation")
        release.set()
        self.assertTrue(self.settle().succeeded)
        self.assertTrue(self.manager.safety.estop_active)

    def test_backend_ticks_and_rearm_cannot_restart_resources_while_closing(self):
        entered, release, _ = self.block(self.manager.udp)
        self.manager.request_scenario("OfficialEvent")
        self.assertTrue(entered.wait(1))
        for _ in range(100):
            self.manager.tick()
            self.assertFalse(self.manager.tick_polar_link()["active"])
            self.manager.release_estop()
            self.manager.set_active_video_source("UDP HEVC")
        self.assertFalse(self.starts)
        self.assertTrue(self.manager.safety.estop_active)
        release.set()
        self.settle()

    def test_requests_from_worker_reject_before_safety_or_registry_side_effects(self):
        errors = []
        def foreign():
            for action in (lambda: self.manager.request_scenario("OfficialEvent"),
                           self.manager.request_shutdown, lambda: self.manager.set_client_id(7)):
                try:
                    action()
                except RuntimeError as exc:
                    errors.append(str(exc))
        worker = threading.Thread(target=foreign)
        worker.start()
        worker.join(1)
        self.assertEqual(len(errors), 3)
        self.assertFalse(self.hooks)
        self.assertFalse(self.manager.lifecycle_pending)

    def test_serial_open_is_off_owner_and_later_close_waits_for_its_owner(self):
        entered, release, starts = self.block(self.manager.serial, "start")
        self.manager.request_scenario("LabDebug")
        self.settle()
        self.assertTrue(entered.wait(1))
        self.assertNotEqual(starts[0], self.owner)
        self.manager.request_scenario("OfficialEvent")
        self.manager.poll_lifecycle()
        self.assertTrue(self.manager.lifecycle_pending)
        self.assertEqual(len(starts), 1)
        release.set()
        self.assertTrue(self.settle().succeeded)
        until(lambda: len(starts) == 2)

    def test_synchronous_compatibility_applies_before_return(self):
        self.manager.apply_scenario("OfficialEvent")
        self.assertFalse(self.manager.lifecycle_pending)
        self.assertEqual(self.manager.scenario_key, "OfficialEvent")
        self.assertTrue(all(owner == self.owner for _, owner, _ in self.starts))

    def test_identity_change_returns_while_old_mqtt_loop_is_stalled(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        client = SimpleNamespace(disconnect=lambda: None,
                                 loop_stop=lambda: (entered.set(), release.wait(3)))
        self.manager.mqtt.client = client
        self.manager.mqtt._started = True
        started = time.perf_counter()
        self.manager.set_client_id(7)
        self.assertLess(time.perf_counter() - started, .05)
        self.assertTrue(entered.wait(1))
        self.assertIsNone(self.manager.mqtt.client)
        self.assertEqual(self.manager.mqtt.client_id, "7")
        self.assertFalse(self.manager.mqtt.wait_stopped(0))
        self.assertTrue(all(owner == self.owner for owner, _ in self.hooks))
        release.set()
        until(lambda: self.manager.mqtt._cleanup_thread is None)

    def test_new_identity_coalesces_into_pending_scenario(self):
        entered, release, _ = self.block(self.manager.udp)
        self.manager.request_scenario("OfficialEvent", robot_id=3)
        self.assertTrue(entered.wait(1))
        self.manager.set_client_id(7)
        release.set()
        completion = self.settle()
        self.assertEqual(completion.target.robot_id, "7")
        self.assertEqual(self.manager.mqtt.client_id, "7")

    def test_blocked_serial_write_does_not_delay_latch_and_next_write_is_denied(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        writes = []
        class Stream:
            def write(self, data):
                writes.append(bytes(data))
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test serial not released")
                return len(data)
            def close(self):
                pass
        serial = self.manager.serial
        serial._serial = Stream()
        serial.connected = True
        serial.mock_writes = False
        frame = RefereeFrameCodec().encode(0x0201)
        results = []
        writer = threading.Thread(target=lambda: results.append(serial.write(frame)))
        writer.start()
        self.assertTrue(entered.wait(1))
        queued = threading.Thread(target=lambda: results.append(serial.write(frame)))
        queued.start()
        start = time.perf_counter()
        self.manager.request_shutdown()
        self.assertLess(time.perf_counter() - start, .05)
        self.assertTrue(serial.estop_active)
        self.assertTrue(self.manager.safety.estop_active)
        release.set()
        writer.join(1)
        queued.join(1)
        self.assertEqual(len(writes), 1, "already-entered bytes cannot be recalled; next admission must fail")
        self.assertEqual([ok for ok, _ in results], [True, False])
        self.settle()

    def test_log_drain_is_off_owner_after_resource_diagnostics(self):
        calls = []
        logger = self.manager.field_log
        original = logger.close
        def close(timeout=1):
            calls.append((threading.get_ident(), logger.snapshot()["pending_records"]))
            return original(timeout)
        logger.close = close
        self.manager.request_shutdown()
        self.assertTrue(self.settle().succeeded)
        self.assertTrue(calls)
        self.assertNotEqual(calls[0][0], self.owner)

    def test_final_retry_recloses_only_failed_resource_and_preserves_original_result(self):
        calls = []
        fail = [True]
        def close_bad():
            calls.append(("bad", threading.get_ident()))
            if fail[0]:
                raise OSError("native handle retained")
        self.manager.request_shutdown(teardown_tasks=(
            TeardownTask("camera", close_bad),
            TeardownTask("vision", lambda: calls.append(("good", threading.get_ident())))))
        first = self.settle()
        self.assertFalse(first.resources_released)
        self.assertTrue(first.audit.drained)
        self.assertTrue(first.audit.intact)
        self.assertFalse(first.may_exit)
        before = dict(self.manager.field_log.snapshot())
        fail[0] = False
        self.assertEqual(self.manager.retry_shutdown(), 2)
        last = self.settle()
        self.assertTrue(last.may_exit)
        self.assertTrue(last.succeeded)
        self.assertEqual(last.attempt, 2)
        self.assertIs(self.manager.initial_shutdown_completion, first)
        self.assertFalse(first.succeeded)
        self.assertEqual([name for name, thread in calls].count("good"), 1)
        self.assertEqual([name for name, thread in calls].count("bad"), 2)
        self.assertTrue(all(thread != self.owner for name, thread in calls))
        self.assertEqual(self.manager.field_log.snapshot()["dropped_records"], before["dropped_records"])
        with self.assertRaises(RuntimeError):
            self.manager.request_scenario("LabDebug")

    def test_retry_rejects_pending_final_and_never_duplicates_close(self):
        entered, release, calls = self.block(self.manager.udp)
        self.manager.request_shutdown()
        self.assertTrue(entered.wait(1))
        with self.assertRaises(RuntimeError):
            self.manager.retry_shutdown()
        self.assertEqual(len(calls), 1)
        release.set()
        self.assertTrue(self.settle().may_exit)

    def test_drained_audit_loss_allows_exit_but_is_never_overall_success(self):
        logger = self.manager.field_log
        self.assertTrue(logger.flush(1))
        def write_failed(line):
            raise OSError("test audit disk failure")
        logger._write_line = write_failed
        logger.emit("forced_write_failure")
        self.manager.request_shutdown()
        result = self.settle()
        self.assertTrue(result.resources_released)
        self.assertTrue(result.audit.drained)
        self.assertFalse(result.audit.intact)
        self.assertGreater(result.audit.write_errors, 0)
        self.assertTrue(result.may_exit)
        self.assertFalse(result.succeeded)
        self.manager.retry_shutdown()
        retry = self.settle()
        self.assertFalse(retry.audit.intact)
        self.assertFalse(retry.succeeded)
        self.assertTrue(retry.may_exit)
        self.assertIs(self.manager.initial_shutdown_completion, result)

    def test_audit_timeout_keeps_exit_blocked_until_explicit_retry_confirms_drain(self):
        logger = self.manager.field_log
        self.assertTrue(logger.flush(1))
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = logger._write_line
        def blocked(line):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test audit write not released")
            original(line)
        logger._write_line = blocked
        logger.emit("blocked_audit_write")
        self.assertTrue(entered.wait(1))
        self.manager.request_shutdown()
        first = self.settle()
        self.assertTrue(first.resources_released)
        self.assertFalse(first.audit.drained)
        self.assertFalse(first.may_exit)
        release.set()
        until(lambda: logger.snapshot()["closed"])
        self.manager.retry_shutdown()
        result = self.settle()
        self.assertTrue(result.may_exit)
        self.assertTrue(result.succeeded)
        self.assertTrue(result.audit.intact)
        self.assertFalse(first.succeeded)

    def test_shutdown_task_admission_rejects_before_owner_side_effects(self):
        snapshot = self.manager.safety.estop_at
        tasks = tuple(TeardownTask(f"extra-{index}", lambda: None) for index in range(17))
        with self.assertRaises(ValueError):
            self.manager.request_shutdown(teardown_tasks=tasks)
        self.assertEqual(self.manager.safety.estop_at, snapshot)
        self.assertFalse(self.manager.lifecycle_pending)


if __name__ == "__main__":
    unittest.main()
