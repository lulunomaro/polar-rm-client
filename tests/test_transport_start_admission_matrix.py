"""Admission matrix for UDP and the decoder's three native pipe workers."""
import threading
import unittest
from unittest.mock import patch

from rm_client.backend.lifecycle import close_decoder, close_video
from rm_client.backend.video_backend import ContinuousMjpegDecoder, RmVideoBackend
from tests.test_video_close_ownership import Process


class TransportStartAdmissionMatrixTests(unittest.TestCase):
    def test_udp_never_admitted_receiver_opens_no_socket_and_strict_close_finishes(self):
        video = RmVideoBackend()
        with patch('rm_client.transports.socket.socket') as socket_factory, \
             patch('threading.Thread.start', side_effect=RuntimeError('receiver rejected')):
            with self.assertRaisesRegex(RuntimeError, 'receiver rejected'):
                video.start()
        socket_factory.assert_not_called()
        self.assertFalse(video.running)
        self.assertIsNone(video._session)
        self.assertIsNone(video._thread)
        close_video(video)
        self.assertTrue(video.cleanup_complete)

    def test_each_decoder_pipe_admission_failure_keeps_failed_native_close_retryable(self):
        original_start = threading.Thread.start
        for worker_name in ('mjpeg-in-h264', 'mjpeg-dec-h264', 'mjpeg-err-h264'):
            for admitted in (False, True):
                with self.subTest(worker=worker_name, admitted_then_raised=admitted):
                    decoder = ContinuousMjpegDecoder('h264')
                    process = Process()
                    process.stdout.fail_close = True
                    started = []
                    def admit(worker):
                        if worker.name == worker_name:
                            if admitted:
                                original_start(worker)
                                started.append(worker)
                            raise RuntimeError('pipe worker admission fault')
                        original_start(worker)
                        started.append(worker)
                    # Run real Thread objects and native-session bookkeeping;
                    # replace only pipe I/O so synthetic fds cannot reach os.read.
                    with patch('rm_client.backend.video_backend.resolve_ffmpeg_executable', return_value='ffmpeg'), \
                         patch('rm_client.backend.video_backend.subprocess.Popen', return_value=process), \
                         patch.object(decoder, '_run_session_worker', return_value=None), \
                         patch('threading.Thread.start', new=admit):
                        self.assertFalse(decoder.start())
                    self.assertFalse(decoder.available)
                    self.assertFalse(decoder.cleanup_complete)
                    self.assertEqual(len(decoder._retired_sessions), 1)
                    self.assertIs(decoder._retired_sessions[0].proc, process)
                    self.assertTrue(all(not worker.is_alive() for worker in started))
                    with self.assertRaisesRegex(RuntimeError, 'pipe close failed'):
                        close_decoder(decoder, None)
                    process.stdout.fail_close = False
                    close_decoder(decoder, None)
                    self.assertTrue(decoder.cleanup_complete)
                    self.assertFalse(process.alive)
                    self.assertTrue(process.stdin.closed and process.stdout.closed and process.stderr.closed)


if __name__ == '__main__':
    unittest.main()
