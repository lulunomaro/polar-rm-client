"""Restore only the old data-worker-is-cleanup policy as a negative control."""
import unittest
from unittest.mock import patch
from rm_client.backend.video_backend import ContinuousMjpegDecoder
from rm_client.backend.video_source import VideoStopStatus
from rm_client.transports import UDPVideoReceiver
from tests.test_video_source_retry_admission import VideoSourceRetryAdmissionTests

decoder_status = ContinuousMjpegDecoder.stop_status.fget
udp_status = UDPVideoReceiver.stop_status.fget

def wrong_decoder_status(self):
    status = decoder_status(self)
    with self._lock:
        data_alive = any(worker.is_alive() for session in self._retired_sessions
                         for worker in session.threads)
    return VideoStopStatus(status.pending, status.inflight or data_alive, status.errors)

def wrong_udp_status(self):
    status = udp_status(self)
    with self._receiver_lock:
        session = self._session
        data_alive = bool(session and session.stopped.is_set() and
                          session.thread and session.thread.is_alive())
    return VideoStopStatus(status.pending, status.inflight or data_alive, status.errors)

suite = unittest.defaultTestLoader.loadTestsFromTestCase(VideoSourceRetryAdmissionTests)
with patch.object(ContinuousMjpegDecoder, 'stop_status', property(wrong_decoder_status)), \
     patch.object(UDPVideoReceiver, 'stop_status', property(wrong_udp_status)):
    result = unittest.TextTestRunner(verbosity=2).run(suite)
assert len(result.failures) == 3 and not result.errors, 'expected native/HEVC/UDP policy failures'
print('EXPECTED NEGATIVE: restoring data-owner-is-cleanup policy causes native, HEVC and UDP failures')
