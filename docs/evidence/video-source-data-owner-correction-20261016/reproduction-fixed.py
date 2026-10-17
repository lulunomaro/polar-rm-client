"""Actual decoder pipe readers require a newly admitted closer to terminate."""
import io
import os
import tempfile
import threading
from unittest.mock import patch

from rm_client.backend.manager import BackendManager
from rm_client.backend.video_backend import ContinuousMjpegDecoder
from rm_client.backend.video_source import VideoSourceAdmissionError

class PipeHeldProcess:
    def __init__(self):
        self.stdin = io.BytesIO()
        out_read, self.out_write = os.pipe()
        err_read, self.err_write = os.pipe()
        self.stdout = os.fdopen(out_read, 'rb', buffering=0)
        self.stderr = os.fdopen(err_read, 'rb', buffering=0)
        self.terminated = threading.Event()
        self.terminate_calls = 0
    def poll(self):
        return 0 if self.terminated.is_set() else None
    def terminate(self):
        self.terminate_calls += 1
        for attr in ('out_write', 'err_write'):
            descriptor = getattr(self, attr)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, attr, None)
        self.terminated.set()
    kill = terminate
    def wait(self, timeout=None):
        if not self.terminated.wait(timeout):
            raise TimeoutError('process still alive')
        return 0

for source in ('native', 'hevc'):
    with tempfile.TemporaryDirectory() as root:
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root}), patch.object(BackendManager, '_build_commit', return_value='TEST'):
            manager = BackendManager(workspace=root)
        proc = PipeHeldProcess()
        read_entered = {pipe.fileno(): threading.Event() for pipe in (proc.stdout, proc.stderr)}
        original_read = os.read
        def observed_read(descriptor, size):
            if descriptor in read_entered:
                read_entered[descriptor].set()
            return original_read(descriptor, size)
        reads = patch('rm_client.backend.video_backend.os.read', side_effect=observed_read)
        reads.start()
        try:
            manager.native_decoder.wait_closed()
            manager.udp.wait_closed()
            decoder = ContinuousMjpegDecoder(codec='h264' if source == 'native' else 'h265')
            if source == 'native':
                manager.native_decoder = decoder
                manager.set_active_video_source('Native')
            else:
                manager.udp.h265_decoder = decoder
                manager.set_active_video_source('UDP', start=False)
            with patch('rm_client.backend.video_backend.subprocess.Popen', return_value=proc), patch('rm_client.backend.video_backend.resolve_ffmpeg_executable', return_value='ffmpeg'):
                assert decoder.start()
            assert all(entered.wait(1) for entered in read_entered.values())
            session = decoder._session
            with patch('threading.Thread.start', side_effect=RuntimeError('closer not admitted')):
                try:
                    manager.set_active_video_source('Synthetic')
                except VideoSourceAdmissionError:
                    pass
                else:
                    raise AssertionError('missing admission failure')
            assert decoder._stop_worker is None
            assert not session.close_lock.locked()
            assert not decoder._lifecycle_lock.locked()
            assert proc.terminate_calls == 0
            assert session.threads[1].is_alive() and session.threads[2].is_alive()
            snapshot = manager.video_source_snapshot
            print(source, 'snapshot=', snapshot, 'retryable=', snapshot.retryable, 'terminate_calls=', proc.terminate_calls, flush=True)
            assert not snapshot.inflight_resources
            assert snapshot.retryable
            manager.retry_active_video_source(expected_generation=snapshot.generation)
            assert decoder.wait_closed()
            assert proc.terminate_calls > 0
            assert all(not worker.is_alive() for worker in session.threads)
            assert not manager.video_source_snapshot.pending_resources
            print(source, 'explicit_retry_terminated_process_and_joined_readers=True', flush=True)
        finally:
            proc.terminate()
            manager.shutdown()
            reads.stop()
