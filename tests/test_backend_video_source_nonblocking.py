"""Real source switching composes retained UDP/native owners without UI waits."""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from rm_client.backend.manager import BackendManager
from rm_client.model import RobotState
from rm_client.backend.referee.registry import STUDENT_PREVIEW_PROFILE
from rm_client.backend.video_backend import RmVideoBackend
from rm_client.backend.video_source import VideoSourceAdmissionError
from rm_client.transports import _UDPVideoSession
from tests.test_video_close_ownership import Process, installed_decoder
from tests.test_udp_stop_nonblocking import Socket


class BackendVideoSourceTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root.name}), patch.object(
                BackendManager, '_build_commit', return_value='TEST'):
            self.backend = BackendManager(workspace=root.name)
        self.addCleanup(self.backend.shutdown)
        # Constructor source selection may have admitted empty decoder stops.
        self.assertTrue(self.backend.native_decoder.wait_closed())
        self.assertTrue(self.backend.udp.wait_closed())

    def held_process(self):
        proc = Process()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        wait = proc.wait
        def held_wait(timeout=None):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test process wait not released')
            return wait(timeout)
        proc.wait = held_wait
        return proc, entered, release

    def settle(self):
        result = None
        deadline = time.monotonic() + 2
        while self.backend.lifecycle_pending:
            completion = self.backend.poll_lifecycle()
            if completion is not None:
                result = completion
            if time.monotonic() > deadline:
                self.fail('final teardown did not include source stop owners')
            time.sleep(.001)
        return result

    def test_source_switch_is_prompt_and_final_close_waits_for_all_admitted_stoppers(self):
        native, native_entered, native_release = self.held_process()
        hevc, hevc_entered, hevc_release = self.held_process()
        self.backend.native_decoder, _ = installed_decoder(native)
        self.backend.udp.h265_decoder, _ = installed_decoder(hevc)
        socket = Socket()
        socket.close_release.clear()
        self.addCleanup(socket.close_release.set)
        self.backend.udp._session = _UDPVideoSession(sock=socket)
        self.backend.udp._socket = socket
        self.backend.udp.running = True
        self.backend.udp.decode_h265 = True
        self.backend._active_video_source = 'udp hevc'
        started = time.perf_counter()
        self.backend.set_active_video_source('Synthetic')
        self.assertLess(time.perf_counter() - started, .1)
        self.assertTrue(native_entered.wait(1))
        self.assertTrue(hevc_entered.wait(1))
        self.assertTrue(socket.close_entered.wait(1))
        self.assertFalse(self.backend.udp.running)
        self.assertFalse(self.backend.native_decoder.available)
        self.assertFalse(self.backend.udp.h265_decoder.available)
        self.backend.request_shutdown()
        self.assertIsNone(self.backend.poll_lifecycle())
        self.assertTrue(self.backend.lifecycle_pending)
        self.assertIsNone(self.backend.shutdown_completion)
        native_release.set()
        hevc_release.set()
        socket.close_release.set()
        result = self.settle()
        self.assertTrue(result.may_exit)
        self.assertTrue(result.succeeded)
        self.assertFalse(socket.is_open)
        for decoder in (self.backend.native_decoder, self.backend.udp.h265_decoder):
            self.assertTrue(decoder.cleanup_complete)
            self.assertFalse(decoder._stop_worker.is_alive())
        self.assertFalse(self.backend.udp._stop_worker.is_alive())

    def test_fast_source_changes_cannot_spawn_until_prior_native_close_finishes(self):
        proc, entered, release = self.held_process()
        decoder, _ = installed_decoder(proc)
        self.backend.native_decoder = decoder
        self.backend._active_video_source = 'native 0x0310'
        self.backend.set_active_video_source('Synthetic')
        self.assertTrue(entered.wait(1))
        stopper = decoder._stop_worker
        replacement = Process()
        with patch('rm_client.backend.video_backend.subprocess.Popen', return_value=replacement) as spawn, \
             patch('rm_client.backend.video_backend.resolve_ffmpeg_executable', return_value='ffmpeg'), \
             patch.object(decoder, '_run_session_worker'):
            for _ in range(50):
                self.backend.set_active_video_source('Native 0x0310')
                self.assertFalse(decoder.start())
                self.backend.set_active_video_source('Synthetic')
            self.assertIs(decoder._stop_worker, stopper)
            spawn.assert_not_called()
            release.set()
            self.assertTrue(decoder.wait_closed())
            self.backend.set_active_video_source('Native 0x0310')
            self.assertTrue(decoder.start())
            self.assertTrue(decoder.start())
            self.assertEqual(spawn.call_count, 1)

    def install_three_owners(self):
        self.backend.native_decoder, native = installed_decoder(Process())
        self.backend.udp.h265_decoder, hevc = installed_decoder(Process())
        socket = Socket()
        self.backend.udp._session = _UDPVideoSession(sock=socket)
        self.backend.udp._socket = socket
        self.backend.udp.running = True
        self.backend.udp.decode_h265 = True
        self.backend._active_video_source = 'udp hevc'
        return native, hevc, socket

    def test_failed_manager_admission_revokes_all_owners_and_requires_explicit_retry(self):
        native, hevc, socket = self.install_three_owners()
        first = RuntimeError('socket closer not admitted')
        second = OSError('HEVC closer not admitted')
        third = LookupError('native closer not admitted')
        with patch('threading.Thread.start', side_effect=[first, second, third]) as admission:
            with self.assertRaises(VideoSourceAdmissionError) as caught:
                self.backend.set_active_video_source('Synthetic')
            self.assertIs(caught.exception.__cause__.__cause__, first)
            self.assertEqual(admission.call_count, 3)
            self.assertFalse(self.backend.udp.running)
            self.assertFalse(self.backend.udp.decode_h265)
            self.assertFalse(self.backend.native_decoder.available)
            self.assertFalse(self.backend.udp.h265_decoder.available)
            self.assertIn(native, self.backend.native_decoder._retired_sessions)
            self.assertIn(hevc, self.backend.udp.h265_decoder._retired_sessions)
            self.assertIs(self.backend.udp._session.sock, socket)
            snapshot = self.backend.video_source_snapshot
            self.assertEqual(snapshot.requested_source, 'synthetic')
            self.assertEqual(snapshot.active_source, '')
            self.assertEqual(snapshot.pending_resources, ('UDP/HEVC', 'Native 0x0310'))
            self.assertEqual([failure.phase for failure in snapshot.failures],
                             ['UDP/HEVC/UDP stop', 'UDP/HEVC/HEVC stop', 'Native stop'])
            for _ in range(25):
                with self.assertRaises(VideoSourceAdmissionError):
                    self.backend.set_active_video_source('Synthetic')
                self.backend._recover_video_decoders()
            self.assertEqual(admission.call_count, 3)
            self.assertEqual(self.backend.latest_video_frame(1, 'Auto'), (None, 0.0, ''))
        self.backend.retry_active_video_source()
        self.assertTrue(self.backend.native_decoder.wait_closed())
        self.assertTrue(self.backend.udp.wait_closed())
        self.assertFalse(socket.is_open)
        snapshot = self.backend.video_source_snapshot
        self.assertEqual(snapshot.active_source, 'synthetic')
        self.assertEqual(snapshot.pending_resources, ())
        self.assertEqual(snapshot.failures, ())
        with self.assertRaisesRegex(RuntimeError, 'No failed'):
            self.backend.retry_active_video_source()

    def test_explicit_scenario_can_replace_failed_source_after_strict_teardown(self):
        self.install_three_owners()
        with patch('threading.Thread.start', side_effect=RuntimeError('not admitted')):
            with self.assertRaises(VideoSourceAdmissionError):
                self.backend.set_active_video_source('Synthetic')
        self.backend.request_scenario('LabDebug', start=False)
        result = self.settle()
        self.assertTrue(result.succeeded, result.failures)
        self.assertEqual(self.backend.video_source_snapshot.failures, ())
        self.assertNotEqual(self.backend.video_source_snapshot.active_source, '')

    def test_explicit_retry_retries_failed_native_close_without_replaying_refresh(self):
        native, hevc, socket = self.install_three_owners()
        socket.close_release.clear()
        self.addCleanup(socket.close_release.set)
        native.proc.stdout.fail_close = True
        self.backend.set_active_video_source('Synthetic')
        self.backend.native_decoder._stop_worker.join(1)
        self.assertTrue(socket.close_entered.wait(1))
        self.assertIn(native, self.backend.native_decoder._retired_sessions)
        first = self.backend.native_decoder._stop_worker
        for _ in range(20):
            self.backend.set_active_video_source('Synthetic')
        self.assertIs(self.backend.native_decoder._stop_worker, first)
        self.assertIn('Native 0x0310', self.backend.video_source_snapshot.pending_resources)
        # Native failure alone does not certify that the independent socket
        # closer has finished. Preserve the UI retry contract under a forced
        # overlap rather than depending on which worker wins scheduling.
        busy = self.backend.video_source_snapshot
        udp_closer = self.backend.udp._stop_worker
        self.assertEqual(busy.inflight_resources, ('UDP/HEVC',))
        self.assertFalse(busy.retryable)
        with self.assertRaisesRegex(RuntimeError, 'still in flight'):
            self.backend.retry_active_video_source(expected_generation=busy.generation)
        self.assertIs(self.backend.native_decoder._stop_worker, first)
        self.assertIs(self.backend.udp._stop_worker, udp_closer)
        self.assertEqual(self.backend.video_source_snapshot.generation, busy.generation)
        socket.close_release.set()
        self.assertTrue(self.backend.udp.wait_closed())
        self.assertFalse(socket.is_open)
        self.assertTrue(self.backend.video_source_snapshot.retryable)
        self.assertIn(native, self.backend.native_decoder._retired_sessions)
        native.proc.stdout.fail_close = False
        self.backend.retry_active_video_source()
        self.assertTrue(self.backend.native_decoder.wait_closed())
        self.assertTrue(self.backend.udp.wait_closed())
        self.assertEqual(self.backend.video_source_snapshot.pending_resources, ())

    def test_recovery_reset_failures_cannot_skip_independent_stop_admissions(self):
        native, hevc, socket = self.install_three_owners()
        with patch.object(self.backend.udp.h265_recovery, 'reset', side_effect=TypeError('HEVC cache')), \
             patch.object(self.backend._native_recovery, 'reset', side_effect=ValueError('native cache')):
            with self.assertRaises(VideoSourceAdmissionError) as caught:
                self.backend.set_active_video_source('Synthetic')
        self.assertEqual([failure.phase for failure in caught.exception.failures],
                         ['UDP/HEVC/HEVC recovery reset', 'Native recovery reset'])
        self.assertFalse(self.backend.native_decoder.available)
        self.assertFalse(self.backend.udp.h265_decoder.available)
        self.assertFalse(self.backend.udp.decode_h265)
        self.assertTrue(self.backend.native_decoder.wait_closed())
        self.assertTrue(self.backend.udp.wait_closed())
        self.assertFalse(socket.is_open)
        self.assertEqual(self.backend.video_source_snapshot.active_source, '')

    def test_rejected_native_target_cannot_keep_old_native_frames_available(self):
        native, hevc, _socket = self.install_three_owners()
        with patch('threading.Thread.start', side_effect=RuntimeError('not admitted')):
            with self.assertRaises(VideoSourceAdmissionError):
                self.backend.set_active_video_source('Native 0x0310')
        self.assertFalse(self.backend.native_decoder.available)
        self.assertIn(native, self.backend.native_decoder._retired_sessions)
        self.assertFalse(self.backend.udp.decode_h265)
        self.assertEqual(self.backend.video_source_snapshot.active_source, '')

    def test_profile_change_accepts_locked_target_without_waiting_for_old_decoder(self):
        proc, entered, release = self.held_process()
        decoder, session = installed_decoder(proc)
        self.backend.native_decoder = decoder
        timer = threading.Timer(.15, release.set)
        timer.start()
        self.addCleanup(timer.join)
        started = time.perf_counter()
        self.backend.set_referee_profile(STUDENT_PREVIEW_PROFILE, start_video=False)
        self.assertLess(time.perf_counter() - started, .1)
        self.assertTrue(entered.wait(1))
        self.assertEqual(self.backend.referee.profile_id, STUDENT_PREVIEW_PROFILE)
        self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')
        self.assertFalse(decoder.available)
        self.assertIn(session, decoder._retired_sessions)
        with patch('rm_client.backend.video_backend.subprocess.Popen') as spawn:
            self.assertFalse(decoder.start())
            spawn.assert_not_called()
        release.set()
        self.assertTrue(decoder.wait_closed())

    def test_profile_stop_admission_failure_preserves_identity_and_explicit_retry(self):
        state = self.backend.state = RobotState()
        original = self.backend.referee.profile_id
        state.referee_profile = original
        self.backend.native_decoder, session = installed_decoder(Process())
        with patch('threading.Thread.start', side_effect=RuntimeError('not admitted')):
            with self.assertRaisesRegex(RuntimeError, 'not admitted'):
                self.backend.set_referee_profile(STUDENT_PREVIEW_PROFILE, start_video=False)
        status = self.backend.referee_profile_change
        self.assertFalse(status.completed)
        self.assertEqual(status.requested_profile, STUDENT_PREVIEW_PROFILE)
        self.assertEqual(status.applied_profile, original)
        self.assertEqual(state.referee_profile, original)
        self.assertIn(session, self.backend.native_decoder._retired_sessions)
        self.assertFalse(self.backend.native_decoder.available)
        self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')
        with self.assertRaisesRegex(RuntimeError, 'transition must finish'):
            self.backend.begin_qualification({'keyboard'})
        self.assertFalse(self.backend.arm_match_control(firmware_hash='test')[0])
        self.backend.set_referee_profile(STUDENT_PREVIEW_PROFILE, start_video=False)
        self.assertEqual(self.backend.referee.profile_id, STUDENT_PREVIEW_PROFILE)
        self.assertEqual(state.referee_profile, STUDENT_PREVIEW_PROFILE)
        self.assertTrue(self.backend.referee_profile_change.completed)
        self.assertTrue(self.backend.native_decoder.wait_closed())

    def test_profile_transport_change_then_adapter_failure_remains_explicit_and_locked(self):
        state = self.backend.state = RobotState()
        original = self.backend.referee.profile_id
        state.referee_profile = original
        with patch('rm_client.backend.referee.registry.RefereeProfile.create_state_adapter',
                   side_effect=TypeError('adapter creation failed')):
            with self.assertRaisesRegex(TypeError, 'adapter creation failed'):
                self.backend.set_referee_profile(STUDENT_PREVIEW_PROFILE, start_video=False)
        status = self.backend.referee_profile_change
        self.assertTrue(status.pending)
        self.assertFalse(status.completed)
        self.assertEqual(status.requested_profile, STUDENT_PREVIEW_PROFILE)
        self.assertEqual(status.transport_profile, STUDENT_PREVIEW_PROFILE)
        self.assertEqual(status.applied_profile, original)
        self.assertEqual(status.frontend_profile, original)
        self.assertIn('TypeError: adapter creation failed', status.error)
        self.assertTrue(self.backend._backend_transition_blocks_work())
        self.backend.set_referee_profile(STUDENT_PREVIEW_PROFILE, start_video=False)
        self.assertTrue(self.backend.referee_profile_change.completed)
        self.assertEqual(self.backend.mqtt.referee.profile_id, self.backend.referee.profile_id)

    def test_scenario_request_can_recover_failed_profile_without_automatic_tick_retry(self):
        self.backend.state = RobotState()
        with patch.object(self.backend, 'set_active_video_source',
                          side_effect=TypeError('source application failed')):
            with self.assertRaises(TypeError):
                self.backend.set_referee_profile(STUDENT_PREVIEW_PROFILE, start_video=False)
        with patch.object(self.backend.mqtt, 'poll_connection') as poll:
            for _ in range(10):
                self.backend.tick()
            poll.assert_not_called()
        self.assertFalse(self.backend.referee_profile_change.completed)
        self.backend.request_scenario('LabDebug', start=False)
        result = self.settle()
        self.assertTrue(result.succeeded, result.failures)
        self.assertTrue(self.backend.referee_profile_change.completed)
        self.assertFalse(self.backend._backend_transition_blocks_work())

    def test_late_profile_source_failure_is_not_hidden_by_equal_profile_retry(self):
        state = self.backend.state = RobotState()
        with patch.object(self.backend, 'set_active_video_source',
                          side_effect=TypeError('source application failed')):
            with self.assertRaisesRegex(TypeError, 'source application failed'):
                self.backend.set_referee_profile(STUDENT_PREVIEW_PROFILE, start_video=False)
        self.assertEqual(self.backend.referee.profile_id, STUDENT_PREVIEW_PROFILE)
        self.assertEqual(state.referee_profile, STUDENT_PREVIEW_PROFILE)
        self.assertEqual(self.backend.mqtt.referee.profile_id, STUDENT_PREVIEW_PROFILE)
        self.assertFalse(self.backend.referee_profile_change.completed)
        with patch.object(self.backend, 'set_active_video_source',
                          wraps=self.backend.set_active_video_source) as source:
            self.backend.set_referee_profile(STUDENT_PREVIEW_PROFILE, start_video=False)
        self.assertEqual(source.call_count, 1)
        self.assertTrue(source.call_args.kwargs['_retry'])
        self.assertTrue(self.backend.referee_profile_change.completed)
        self.assertFalse(self.backend._backend_transition_blocks_work())

    def test_invalid_profile_revokes_mqtt_without_joining_its_retained_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def loop_stop():
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test Paho loop not released')
        client = SimpleNamespace(disconnect=lambda: None, loop_stop=loop_stop)
        self.backend.mqtt.client = client
        self.backend.mqtt._started = True
        timer = threading.Timer(.15, release.set)
        timer.start()
        self.addCleanup(timer.join)
        started = time.perf_counter()
        with self.assertRaises(ValueError):
            self.backend.set_referee_profile('invalid-profile')
        self.assertLess(time.perf_counter() - started, .1)
        self.assertTrue(entered.wait(1))
        self.assertIsNone(self.backend.mqtt.client)
        self.assertIs(self.backend.mqtt._cleanup_client, client)
        self.assertEqual(self.backend.safety.control_state.value, 'LOCKED')
        release.set()
        self.assertTrue(self.backend.mqtt.wait_stopped())

    def test_nested_decoder_is_revoked_even_when_socket_closer_admission_fails(self):
        video = RmVideoBackend()
        proc = Process()
        video.h265_decoder, session = installed_decoder(proc)
        socket = Socket()
        video._session = _UDPVideoSession(sock=socket)
        video._socket = socket
        video.running = True
        with patch('threading.Thread.start', side_effect=RuntimeError('stop worker admission failed')):
            with self.assertRaisesRegex(RuntimeError, 'stop worker admission failed'):
                video.request_stop()
        self.assertFalse(video.running)
        self.assertFalse(video.h265_decoder.available)
        self.assertIn(session, video.h265_decoder._retired_sessions)
        self.assertIs(video._session.sock, socket)
        video.stop()
        self.assertTrue(video.wait_closed())


if __name__ == '__main__':
    unittest.main()
