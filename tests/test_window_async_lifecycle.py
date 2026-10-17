"""Real offscreen Qt window and backend lifecycle, with inert device boundaries.

These tests prove event-loop ownership and teardown ordering, not native driver
or hardware behavior. Capture/media preload stay disabled throughout.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QEvent, QTimer, Qt, QPoint, QPointF, QRectF
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication
from rm_client.lifecycle import LifecyclePhase
from rm_client.ui.polar_app import PolarRMWindow
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])


class WindowAsyncLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": self.directory.name})
        self.environment.start()
        self.window = PolarRMWindow()
        self.window.ui_audio_enabled = False
        audio = self.window.registry.get("AudioFeedback")
        if audio is not None:
            audio.enabled = False
        settle_window_lifecycle(self.window, APP)
        self.window.timer.stop()
        self.addCleanup(self.close_window)

    def close_window(self):
        close_window_safely(self.window, APP)
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.environment.stop()
        self.directory.cleanup()

    def create_unsaved_copy(self):
        window = self.window
        source = window.config_store.profile_path(window.current_profile())
        original = source.read_bytes()
        window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 1.75
        window.registry.get("ConfigManager").get_setting("Profile").value = "close-draft"
        with patch.object(window.config_store, "save", side_effect=OSError("initial save failed")):
            window.save_current_config()
        self.assertEqual(window._unsaved_profile_name, "close-draft")
        return source, original

    def test_close_retries_transient_failed_copy_before_starting_native_shutdown(self):
        source, original = self.create_unsaved_copy()
        window = self.window
        events = []
        save = window.config_store.save
        close_camera = window.camera_worker.close
        def saving(*args, **kwargs):
            events.append("save")
            self.assertFalse(window._window_lifecycle.closing)
            return save(*args, **kwargs)
        def closing_camera():
            events.append("camera")
            self.assertTrue(window.config_store.profile_path("close-draft").exists())
            return close_camera()
        with (
            patch.object(window.config_store, "save", side_effect=saving) as saved,
            patch.object(window.camera_worker, "close", side_effect=closing_camera),
            patch.object(window, "prepare_configuration_for_close", wraps=window.prepare_configuration_for_close) as admission,
        ):
            self.assertFalse(window.close())
            self.poll_until(lambda: window._window_close_finalized)
        self.assertEqual(events, ["save", "camera"])
        self.assertEqual(saved.call_args.args[0], "close-draft")
        admission.assert_called_once()
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(window.config_store.load("close-draft")["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.75)
        self.assertTrue(window.backend.shutdown_completion.may_exit)

    def test_failed_copy_close_keeps_window_and_draft_alive_but_revokes_held_control(self):
        source, original = self.create_unsaved_copy()
        window = self.window
        self.assertIsNotNone(window.backend.begin_qualification({"kmc_heartbeat"}))
        window.control.key_state["W"] = True
        with (
            patch.object(window.config_store, "save", side_effect=OSError("disk unavailable")) as saved,
            patch.object(window.backend, "request_shutdown", wraps=window.backend.request_shutdown) as shutdown,
            patch.object(window.camera_worker, "close", wraps=window.camera_worker.close) as camera_close,
            patch.object(window, "cancel_pending_configuration_for_close") as cancel,
            patch.object(window, "toast") as toast,
        ):
            self.assertFalse(window.close())
            self.assertEqual(saved.call_count, 1)
            self.assertFalse(window._window_lifecycle.closing)
            self.assertFalse(window._window_close_finalized)
            self.assertTrue(window.isEnabled())
            self.assertFalse(window.backend.lifecycle_closed)
            self.assertFalse(window.backend.lifecycle_pending)
            self.assertEqual(window.backend.safety.control_state.value, "LOCKED")
            self.assertFalse(window.control.key_state["W"])
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            APP.processEvents()
            window.on_tick()
            window.flush_auto_save(force=True)
            self.assertEqual(heartbeat, [True])
            self.assertEqual(saved.call_count, 1, "cancelled close must not start a retry loop")
            shutdown.assert_not_called()
            camera_close.assert_not_called()
            cancel.assert_not_called()
            messages = "\n".join(call.args[0] for call in toast.call_args_list)
            self.assertIn("disk unavailable", messages)
            self.assertIn("关闭已取消", messages)
            self.assertIn("重新授权", messages)
        self.assertEqual(window._unsaved_profile_name, "close-draft")
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(window.config_store.profile_path("close-draft").exists())

    def test_draft_admission_failure_never_seals_shutdown_even_if_input_reset_is_broken(self):
        self.create_unsaved_copy()
        window = self.window
        self.assertIsNotNone(window.backend.begin_qualification({"kmc_heartbeat"}))
        with patch.object(window.control, "reset_inputs", side_effect=TypeError("input reset failed")), \
             patch.object(window, "prepare_configuration_for_close", side_effect=ValueError("admission defect")), \
             patch.object(window.backend, "request_shutdown", wraps=window.backend.request_shutdown) as shutdown, \
             patch.object(window, "toast") as toast:
            self.assertFalse(window.close())
            self.assertEqual(window.backend.safety.control_state.value, "LOCKED")
            self.assertFalse(window._window_lifecycle.closing)
            self.assertTrue(window.isEnabled())
            shutdown.assert_not_called()
            messages = "\n".join(call.args[0] for call in toast.call_args_list)
            self.assertIn("input reset failed", messages)
            self.assertIn("admission defect", messages)
            self.assertIn("关闭已取消", messages)
        self.assertEqual(window._unsaved_profile_name, "close-draft")

    def test_cancelled_copy_close_can_be_edited_saved_and_then_closed_explicitly(self):
        source, original = self.create_unsaved_copy()
        window = self.window
        with patch.object(window.config_store, "save", side_effect=OSError("disk unavailable")):
            self.assertFalse(window.close())
        setting = window.registry.get("RobotLink").get_setting("Mouse Sensitivity")
        setting.value = 2.0
        window.after_setting_changed(setting)
        self.assertEqual(setting.value, 2.0)
        window.save_current_config()
        self.assertIsNone(window._unsaved_profile_name)
        with patch.object(window.config_store, "save", wraps=window.config_store.save) as saved:
            close_window_safely(window, APP)
        saved.assert_not_called()
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(window.config_store.load("close-draft")["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 2.0)
        self.assertTrue(window._window_close_finalized)

    def test_dirty_document_close_failure_preserves_input_lock_and_pauses_timer_retries(self):
        window = self.window
        source = window.config_store.profile_path(window.current_profile())
        original = source.read_bytes()
        setting = window.registry.get("RobotLink").get_setting("Mouse Sensitivity")
        setting.value = 1.75
        window.auto_save_config()
        self.assertIsNotNone(window.backend.begin_qualification({"kmc_heartbeat"}))
        window.control.key_state["W"] = True
        with (
            patch.object(window.config_store, "save", side_effect=OSError("accepted file unavailable")) as save,
            patch.object(window.backend, "request_shutdown", wraps=window.backend.request_shutdown) as shutdown,
            patch.object(window, "toast") as toast,
        ):
            self.assertFalse(window.close())
            self.assertFalse(window._window_lifecycle.closing)
            self.assertTrue(window.isEnabled())
            self.assertEqual(save.call_count, 1)
            self.assertEqual(window.backend.safety.control_state.value, "LOCKED")
            self.assertFalse(window.control.key_state["W"])
            self.assertTrue(window._auto_save_pending)
            self.assertIsNone(getattr(window, "_unsaved_profile_name", None))
            setting.value = 2.0
            window.after_setting_changed(setting)
            for _ in range(3):
                window.on_tick()
                window.flush_auto_save_if_due(float("inf"))
                window.flush_auto_save(force=True)
            self.assertEqual(save.call_count, 1)
            shutdown.assert_not_called()
            messages = "\n".join(call.args[0] for call in toast.call_args_list)
            self.assertIn("accepted file unavailable", messages)
            self.assertIn("关闭已取消", messages)
            self.assertIn("重新授权", messages)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(setting.value, 2.0)
        with patch.object(window.config_store, "save", wraps=window.config_store.save) as save:
            self.assertFalse(window.close())
            self.poll_until(lambda: window._window_close_finalized)
        save.assert_called_once()
        self.assertEqual(window.config_store.load(window.current_profile())["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 2.0)

    def test_dirty_document_is_saved_before_native_shutdown_and_not_again_on_final_close(self):
        window = self.window
        window.registry.get("RobotLink").get_setting("Mouse Sensitivity").value = 1.75
        window.auto_save_config()
        native_close = window.camera_worker.close
        def closing_camera():
            saved = window.config_store.load(window.current_profile())
            self.assertEqual(saved["modules"]["RobotLink"]["settings"]["Mouse Sensitivity"], 1.75)
            return native_close()
        with (
            patch.object(window.camera_worker, "close", side_effect=closing_camera),
            patch.object(window.config_store, "save", wraps=window.config_store.save) as save,
            patch.object(window, "prepare_configuration_for_close", wraps=window.prepare_configuration_for_close) as admission,
        ):
            self.assertFalse(window.close())
            self.poll_until(lambda: window._window_close_finalized)
        save.assert_called_once()
        admission.assert_called_once()
        self.assertFalse(window._auto_save_pending)

    def test_first_close_is_nonblocking_and_waits_for_real_worker_completion(self):
        window = self.window
        owner_thread = threading.get_ident()
        entered, unblock = threading.Event(), threading.Event()
        self.addCleanup(unblock.set)
        worker_threads = []
        def slow_camera_close():
            worker_threads.append(threading.get_ident())
            entered.set()
            unblock.wait(2)
        window.control.key_state["W"] = True
        window.backend._lifecycle._resources._warning_after_s = 0
        saved_on_threads = []
        original_save = window.flush_auto_save
        def save(*args, **kwargs):
            saved_on_threads.append(threading.get_ident())
            return original_save(*args, **kwargs)
        with patch.object(window.camera_worker, "close", side_effect=slow_camera_close) as camera_close, \
             patch.object(window, "ensure_camera_started") as start_camera, \
             patch.object(window, "_advance_economy_transaction") as economy_tick, \
             patch.object(window, "flush_auto_save", side_effect=save):
            started = time.monotonic()
            self.assertFalse(window.close())
            self.assertLess(time.monotonic() - started, .5)
            self.assertTrue(entered.wait(1))
            self.assertFalse(window.control.key_state["W"])
            self.assertTrue(window.backend.safety.estop_active)
            self.assertFalse(window.isEnabled())
            self.assertTrue(window.timer.isActive())
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            APP.processEvents()
            window.on_tick()
            self.assertEqual(heartbeat, [True])
            self.assertTrue(window.backend.lifecycle_pending)
            self.assertEqual(window.backend.lifecycle_snapshot.phase, LifecyclePhase.BLOCKED)
            self.assertFalse(window._window_close_finalized)
            start_camera.assert_not_called()
            economy_tick.assert_not_called()
            with patch.object(window, "_paint_shutdown_status") as status, \
                 patch.object(window, "paint_gpu_frame") as frame:
                window.paintGL()
                status.assert_called_once()
                frame.assert_not_called()
            unblock.set()
            close_window_safely(window, APP)
            camera_close.assert_called_once()
        self.assertTrue(window._window_close_finalized)
        self.assertFalse(window.timer.isActive())
        self.assertEqual(saved_on_threads, [owner_thread])
        self.assertTrue(worker_threads)
        self.assertTrue(all(thread != owner_thread for thread in worker_threads))

    def test_scenario_requests_coalesce_and_runtime_stays_stopped_until_poll(self):
        window = self.window
        old_scenario = window.backend.scenario_key
        entered, unblock = threading.Event(), threading.Event()
        self.addCleanup(unblock.set)
        original_stop = window.backend.udp.stop
        def slow_video_stop():
            entered.set()
            unblock.wait(2)
            original_stop()
        with patch.object(window.backend.udp, "stop", side_effect=slow_video_stop), \
             patch.object(window.backend.mqtt, "start") as mqtt_start, \
             patch.object(window.backend.udp, "start") as udp_start, \
             patch.object(window.backend.serial, "start"), \
             patch.object(window, "ensure_camera_started") as camera_start:
            window.apply_scenario("OfficialEvent", close_home=False)
            self.assertTrue(entered.wait(1))
            first_snapshot = window._pending_scenario_configuration
            window.apply_scenario("CampusSimulation", close_home=False)
            latest_snapshot = window._pending_scenario_configuration
            self.assertGreater(latest_snapshot.generation, first_snapshot.generation)
            mode = window.registry.get("Scenario").get_setting("Mode")
            mode.value = "LabDebug"
            window.after_setting_changed(mode)
            self.assertEqual(mode.value, "CampusSimulation")
            self.assertEqual(window.backend.scenario_key, old_scenario)
            window.on_tick()
            mqtt_start.assert_not_called()
            udp_start.assert_not_called()
            camera_start.assert_not_called()
            self.assertTrue(window.backend.lifecycle_pending)
            unblock.set()
            settle_window_lifecycle(window, APP)
            self.assertEqual(window.backend.scenario_key, "CampusSimulation")
            self.assertEqual(window.control.mock_output, window.backend.mqtt.mock_output)
            self.assertFalse(window.configuration_change_pending)
            self.assertFalse(window.backend.lifecycle_pending)
            self.assertTrue(mqtt_start.called)

    def poll_until(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.window._poll_backend_lifecycle()
            APP.processEvents()
            if time.monotonic() >= deadline:
                self.fail(f"Window did not reach condition: {self.window.backend.lifecycle_snapshot}")
            time.sleep(.001)

    def test_failed_capture_owner_keeps_window_alive_until_explicit_retry(self):
        window = self.window
        class Capture:
            opened = True
            releases = 0
            def isOpened(self): return self.opened
            def set(self, *args): pass
            def read(self):
                raise OSError("inert camera read finished")
            def release(self):
                self.releases += 1
                if self.releases == 1:
                    raise OSError("native camera release rejected")
                self.opened = False
        capture = Capture()
        # Exercise the production capture-finally ownership path without
        # opening a device. The first failure must not be retried by polling.
        with patch("cv2.VideoCapture", return_value=capture):
            self.assertTrue(window.camera_worker.start())
            worker = window.camera_worker._thread
            worker.join(1)
            self.assertFalse(worker.is_alive())
        self.assertIs(window.camera_worker._unreleased_capture, capture)
        with patch.object(window, "_show_shutdown_warning") as warning:
            self.assertFalse(window.close())
            self.poll_until(lambda: window.backend.shutdown_completion is not None)
            first = window.backend.shutdown_completion
            self.assertFalse(first.may_exit)
            self.assertFalse(window._window_close_finalized)
            self.assertIs(window.camera_worker._unreleased_capture, capture)
            self.assertEqual(capture.releases, 1)
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            for _ in range(3):
                window.on_tick()
                APP.processEvents()
            self.assertEqual(heartbeat, [True])
            self.assertEqual(capture.releases, 1)
            self.assertFalse(window.isEnabled())
            # The next actual close event is an explicit retry, not a forced
            # destruction of the still-owned native capture.
            self.assertFalse(window.close())
            self.poll_until(lambda: window._window_close_finalized)
            warning.assert_not_called()
        self.assertEqual(capture.releases, 2)
        self.assertIsNone(window.camera_worker._unreleased_capture)
        self.assertIs(window.backend.initial_shutdown_completion, first)
        self.assertTrue(window.backend.shutdown_completion.may_exit)
        self.assertEqual(window.backend.shutdown_completion.attempt, 2)

    def test_drained_audit_loss_warns_once_before_accepting_safe_exit(self):
        window = self.window
        owner = threading.get_ident()
        self.assertFalse(window.backend.field_log.emit("oversized", value="x" * 300000))
        warnings = []
        def acknowledge(message):
            self.assertEqual(threading.get_ident(), owner)
            self.assertFalse(window._window_close_finalized)
            self.assertTrue(window.backend.shutdown_completion.may_exit)
            warnings.append(message)
        with patch.object(window, "_show_shutdown_warning", side_effect=acknowledge):
            close_window_safely(window, APP)
            window._poll_backend_lifecycle()
        self.assertEqual(len(warnings), 1)
        self.assertIn("Dropped records: 1", warnings[0])
        self.assertIn("write errors: 0", warnings[0])
        self.assertIn("audit-log", warnings[0])
        self.assertFalse(window.backend.shutdown_completion.succeeded)
        self.assertTrue(window._window_close_finalized)

    def test_clean_shutdown_never_opens_a_warning_dialog(self):
        with patch.object(self.window, "_show_shutdown_warning") as warning:
            close_window_safely(self.window, APP)
        warning.assert_not_called()
        self.assertTrue(self.window.backend.shutdown_completion.succeeded)

    def test_mqtt_epoch_error_cannot_finish_close_while_cleanup_owner_is_live(self):
        window = self.window
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def stop_loop():
            calls.append(threading.get_ident())
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test MQTT cleanup was not released")
        client = SimpleNamespace(disconnect=lambda: None, loop_stop=stop_loop)
        window.backend.mqtt.client = client
        window.backend.mqtt._started = True
        warnings = []
        with patch.object(window.backend.mqtt, "clear_received_epoch", side_effect=OSError("epoch cleanup failure")), \
             patch.object(window, "_show_shutdown_warning", side_effect=warnings.append):
            self.assertFalse(window.close())
            self.assertTrue(entered.wait(1))
            self.assertIs(window.backend.mqtt._cleanup_client, client)
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            window.on_tick()
            APP.processEvents()
            self.assertEqual(heartbeat, [True])
            self.assertTrue(window.backend.lifecycle_pending)
            self.assertIsNone(window.backend.shutdown_completion)
            self.assertFalse(window._window_close_finalized)
            self.assertFalse(window._window_lifecycle.ready_to_close)
            release.set()
            self.poll_until(lambda: window._window_close_finalized)
        self.assertTrue(window.backend.shutdown_completion.may_exit)
        self.assertFalse(window.backend.shutdown_completion.succeeded)
        self.assertEqual(len(warnings), 1)
        self.assertIn("MQTT invalidate", warnings[0])
        self.assertIn("epoch cleanup failure", warnings[0])
        self.assertTrue(all(thread != threading.get_ident() for thread in calls))

    def test_real_paho_detached_socket_keeps_window_until_explicit_retry(self):
        import paho.mqtt.client as paho
        from rm_client.backend.mqtt_cleanup import MqttClientResources
        window = self.window
        class Socket:
            fd = 17
            fail = True
            def fileno(self): return self.fd
            def close(self):
                if self.fail:
                    raise OSError("native Paho socket retained")
                self.fd = -1
        socket = Socket()
        client = paho.Client(paho.CallbackAPIVersion.VERSION2, reconnect_on_failure=False)
        owner = MqttClientResources(client)
        owner.attach_socket_observers()
        client._sock = socket
        window.backend.mqtt.client = client
        window.backend.mqtt._client_resources = owner
        window.backend.mqtt._started = True
        with self.assertRaises(OSError):
            client._sock_close()  # Production Paho detaches before close.
        self.assertIsNone(client._sock)
        with patch.object(window, "_show_shutdown_warning") as warning:
            self.assertFalse(window.close())
            self.poll_until(lambda: window.backend.shutdown_completion is not None)
            first = window.backend.shutdown_completion
            self.assertFalse(first.may_exit)
            self.assertFalse(window._window_close_finalized)
            self.assertIs(window.backend.mqtt._cleanup_client, client)
            self.assertNotEqual(socket.fileno(), -1)
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            window.on_tick()
            APP.processEvents()
            self.assertEqual(heartbeat, [True])
            socket.fail = False
            self.assertFalse(window.close())
            self.poll_until(lambda: window._window_close_finalized)
            warning.assert_not_called()
        self.assertEqual(socket.fileno(), -1)
        self.assertIsNone(window.backend.mqtt._cleanup_client)
        self.assertTrue(window.backend.shutdown_completion.may_exit)
        self.assertTrue(window.backend.shutdown_completion.succeeded)
        self.assertIs(window.backend.initial_shutdown_completion, first)

    def test_owner_prepare_save_and_context_errors_are_visible_after_native_cleanup(self):
        window = self.window
        notices = []
        with patch.object(window, "cancel_pending_configuration_for_close", side_effect=OSError("restore failed")), \
             patch.object(window, "flush_auto_save", side_effect=OSError("save failed")), \
             patch.object(window, "context", return_value=object()), \
             patch.object(window, "makeCurrent", side_effect=TypeError("context failed")), \
             patch.object(window, "doneCurrent") as done, \
             patch.object(window, "_show_shutdown_cleanup_warning", side_effect=notices.append), \
             patch.object(window, "_show_shutdown_warning") as completion_notice:
            close_window_safely(window, APP)
        self.assertEqual(len(notices), 1)
        self.assertTrue(all(message in notices[0] for message in ("restore failed", "save failed", "context failed")))
        self.assertTrue(window.backend.shutdown_completion.may_exit)
        self.assertTrue(window._window_close_finalized)
        self.assertFalse(window.timer.isActive())
        done.assert_called_once()
        completion_notice.assert_not_called()

    def test_persistent_input_reset_failure_does_not_strand_confirmed_native_cleanup(self):
        window = self.window
        self.assertIsNotNone(window.backend.begin_qualification({"kmc_heartbeat"}))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original_close = window.camera_worker.close
        def close_camera():
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test camera teardown was not released")
            original_close()
        with patch.object(window.control, "reset_inputs", side_effect=TypeError("persistent reset failure")), \
             patch.object(window.camera_worker, "close", side_effect=close_camera) as camera_close, \
             patch.object(window, "ensure_camera_started") as camera_start, \
             patch.object(window, "_show_shutdown_warning") as completion_warning, \
             patch.object(window, "_show_shutdown_cleanup_warning") as owner_warning:
            self.assertFalse(window.close())
            self.assertTrue(entered.wait(1))
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            window.on_tick()
            APP.processEvents()
            self.assertEqual(heartbeat, [True])
            self.assertTrue(window.backend.lifecycle_pending)
            self.assertFalse(window._window_close_finalized)
            self.assertTrue(window.backend.safety.estop_active)
            self.assertNotEqual(window.backend.safety.control_state.value, "QUALIFICATION")
            self.assertFalse(window.combat_control_input_allowed())
            release.set()
            self.poll_until(lambda: window._window_close_finalized)
            camera_close.assert_called_once()
            camera_start.assert_not_called()
            completion_warning.assert_called_once()
            owner_warning.assert_called_once()
            self.assertIn("persistent reset failure", completion_warning.call_args.args[0])
            self.assertIn("TypeError: persistent reset failure", owner_warning.call_args.args[0])
            result = window.backend.shutdown_completion
            self.assertTrue(result.may_exit)
            self.assertTrue(result.resources_released)
            self.assertTrue(result.audit.drained)
            self.assertFalse(result.succeeded)
            self.assertTrue(any("persistent reset failure" in failure.error for failure in result.failures))
            self.assertEqual(window._window_lifecycle.poll_failures, 0)
            self.assertFalse(window.timer.isActive())

    def test_pending_scenario_rejects_real_setting_drag_and_key_edits_before_mutation(self):
        window = self.window
        entered, unblock = threading.Event(), threading.Event()
        self.addCleanup(unblock.set)
        original_stop = window.backend.udp.stop
        def stopped():
            entered.set()
            if not unblock.wait(3):
                raise TimeoutError("test transition was not released")
            original_stop()
        radius = window.registry.get("QuickActionWheel").get_setting("Radius")
        window.drag = {"type": "slider", "setting": radius, "rect": QRectF(0, 0, 100, 20)}
        window.binding_module = window.registry.get("ArmorAutoAim")
        with patch.object(window.backend.udp, "stop", side_effect=stopped), \
             patch.object(window.backend.mqtt, "start"), \
             patch.object(window.backend.udp, "start"), \
             patch.object(window.backend.serial, "start"), \
             patch.object(window, "after_setting_changed", wraps=window.after_setting_changed) as changed:
            window.apply_scenario("CampusSimulation", close_home=False)
            self.assertTrue(entered.wait(1))
            self.assertFalse(window.configuration_change_pending)
            self.assertTrue(window.backend.lifecycle_pending)
            self.assertIsNone(window.drag)
            self.assertIsNone(window.binding_module)
            self.assertTrue(window._jello_background_release_owned)
            toggle = window.registry.get("RobotLink").get_setting("Allow Drive")
            mode = window.registry.get("CameraFeed").get_setting("Source")
            module = window.registry.get("ArmorAutoAim")
            module.keybind = "F7"
            before = (toggle.value, radius.value, mode.value, module.enabled)
            point, rect = QPointF(99, 10), QRectF(0, 0, 100, 20)
            for action, payload in (("setting_toggle", toggle), ("setting_slider", radius),
                                    ("setting_mode_next", mode), ("module_toggle", module.name)):
                window.dispatch(action, payload, point, 1, rect)
            window.drag = {"type": "slider", "setting": radius, "rect": rect}
            move = QMouseEvent(QEvent.Type.MouseMove, point, point, Qt.MouseButton.NoButton,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            window.mouseMoveEvent(move)
            key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F7, Qt.KeyboardModifier.NoModifier)
            window.keyPressEvent(key)
            wheel = QWheelEvent(point, point, QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
            window.wheelEvent(wheel)
            self.assertEqual((toggle.value, radius.value, mode.value, module.enabled), before)
            changed.assert_not_called()
            self.assertTrue(all(event.isAccepted() for event in (move, key, wheel)))
            self.assertFalse(window.combat_control_input_allowed())
            window.drag = None
            unblock.set()
            settle_window_lifecycle(window, APP)
            window.dispatch("setting_toggle", toggle, point, 1, rect)
            self.assertEqual(toggle.value, not before[0])
            changed.assert_called_once_with(toggle)

    def test_window_scenario_entries_restore_direct_callbacks_without_reopening_retired_angles(self):
        window = self.window
        angles = window.backend.controller_angles
        opened = Mock(side_effect=AssertionError("A pending callback attempted to open serial"))
        angles.serial_factory = opened
        for action in ("scenario", "startup_open_gui", "startup_open_config"):
            with self.subTest(action=action):
                entered, release, retired = threading.Event(), threading.Event(), threading.Event()
                original_stop, original_wait = window.backend.udp.stop, angles.wait_closed
                def stopped():
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("test transition was not released")
                    original_stop()
                def closed(timeout=None):
                    result = original_wait(timeout)
                    retired.set()
                    return result
                with patch.object(window.backend.udp, "stop", side_effect=stopped), \
                     patch.object(angles, "wait_closed", side_effect=closed), \
                     patch.object(window.backend.mqtt, "start"), \
                     patch.object(window.backend.udp, "start"), \
                     patch.object(window.backend.serial, "start"):
                    try:
                        if action == "scenario":
                            window.apply_scenario("LabDebug", close_home=False)
                        else:
                            window.dispatch(action, None, QPointF(), 1, QRectF())
                        self.assertTrue(entered.wait(1))
                        self.assertTrue(retired.wait(1))
                        self.assertTrue(window.backend.lifecycle_pending)
                        self.assertFalse(window.configuration_change_pending)
                        self.assertIsNotNone(window._pending_scenario_configuration)
                        module = window.registry.get("ControllerAngles")
                        setting = module.get_setting("Angle RX Port")
                        previous_value, previous_enabled = setting.value, module.enabled
                        module.enabled = True
                        setting.value = "REVIEW_RX"
                        with patch.object(window, "auto_save_config") as save:
                            window.after_setting_changed(setting)
                        self.assertEqual(setting.value, previous_value)
                        self.assertEqual(module.enabled, previous_enabled)
                        self.assertEqual(angles.owned_ports, ())
                        opened.assert_not_called()
                        save.assert_not_called()
                    finally:
                        angles.close()
                        self.assertTrue(original_wait(2))
                        release.set()
                        settle_window_lifecycle(window, APP)
                self.assertIsNone(window._pending_scenario_configuration)

    def test_pending_transition_allows_estop_but_never_estop_release(self):
        window = self.window
        with patch.object(window.backend, "trigger_estop", return_value=(True, "")) as stop, \
             patch.object(window.backend, "release_estop") as release:
            window._window_lifecycle.closing = True
            try:
                window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F12,
                    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
                release.assert_not_called()
                window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F12,
                    Qt.KeyboardModifier.NoModifier))
                stop.assert_called_once()
            finally:
                window._window_lifecycle.closing = False

    def test_main_window_f12_revokes_real_authority_before_a_failed_input_reset(self):
        window = self.window
        token = window.backend.begin_qualification({"kmc_heartbeat"})
        self.assertIsNotNone(token)
        self.assertFalse(window.backend.safety.estop_active)
        with patch.object(window.control, "reset_inputs", side_effect=TypeError("F12 reset failure")), \
             patch.object(window.control, "clear_pending", wraps=window.control.clear_pending) as clear, \
             patch.object(window, "toast") as toast:
            window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F12,
                Qt.KeyboardModifier.NoModifier))
        self.assertTrue(window.backend.safety.estop_active)
        self.assertNotEqual(window.backend.safety.control_state.value, "QUALIFICATION")
        # The failed lock observer now clears pending independently; the
        # F12 adapter retains its own bounded fallback cleanup as well.
        self.assertEqual(clear.call_args_list, [unittest.mock.call(), unittest.mock.call()])
        self.assertIn("TypeError: F12 reset failure", toast.call_args.args[0])
        self.assertEqual(toast.call_args.kwargs, {"kind": "warning"})

    def test_main_window_f12_attempts_real_fallback_lock_when_backend_stop_raises(self):
        window = self.window
        self.assertIsNotNone(window.backend.begin_qualification({"kmc_heartbeat"}))
        with patch.object(window.backend, "trigger_estop", side_effect=OSError("F12 driver failure")), \
             patch.object(window, "toast") as toast:
            window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F12,
                Qt.KeyboardModifier.NoModifier))
        self.assertNotEqual(window.backend.safety.control_state.value, "QUALIFICATION")
        self.assertIn("E-STOP REQUEST INCOMPLETE", toast.call_args.args[0])
        self.assertIn("OSError: F12 driver failure", toast.call_args.args[0])
        self.assertEqual(toast.call_args.kwargs, {"kind": "warning"})

    def test_queued_gpu_warmup_cannot_resume_after_close_requested(self):
        window = self.window
        window._window_lifecycle.closing = True
        with patch.object(window._gpu_prewarm_timer, "start") as start, \
             patch.object(window, "makeCurrent") as current:
            window.prewarm_gpu_clickgui_resources()
            window._run_gpu_prewarm_slice()
        start.assert_not_called()
        current.assert_not_called()
        # Restore the policy state so fixture cleanup starts the actual close.
        window._window_lifecycle.closing = False


if __name__ == "__main__": unittest.main()
