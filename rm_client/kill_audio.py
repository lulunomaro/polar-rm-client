"""Cached PCM playback of the original kill cues, without UI-thread decoding.

GD656's original client predecodes Vorbis to PCM. Qt's short-effect backend
accepts PCM WAV, while its platform media backends do not all support OGG.
Use the already bundled FFmpeg (or existing source-mode installation) once
per content/version/settings key. Never install or download a decoder.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import hashlib
import logging
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave

from PySide6.QtCore import QCoreApplication, Signal, Qt
from PySide6.QtMultimedia import QMediaDevices

from .audio_preferences import cue_volume
from .runtime_paths import RuntimePaths
from .ui_audio import ReliableAudioPool, _media_preload_disabled

_LOG = logging.getLogger(__name__)
_PCM_SETTINGS = b'polar-kill-pcm-v1:pcm_s16le:native-rate:native-channels:no-gain'


def resolve_kill_decoder(paths: RuntimePaths) -> str | None:
    candidates = [str(paths.ffmpeg_path)]
    if paths.source_mode:
        candidates += [os.environ.get('POLAR_FFMPEG', ''), shutil.which('ffmpeg') or '']
    return next((str(Path(path).resolve()) for path in candidates if path and Path(path).is_file()), None)


def _valid_pcm(path: Path) -> bool:
    try:
        with wave.open(str(path), 'rb') as audio:
            return audio.getcomptype() == 'NONE' and audio.getsampwidth() == 2 and audio.getnframes() > 0
    except (OSError, EOFError, wave.Error):
        return False


class PcmCueCache:
    """Worker-only file/process work; one instance is used by one worker."""
    def __init__(self, decoder: str, root: Path):
        self.decoder, self.root = decoder, root
        self._version: bytes | None = None

    def decode(self, source: Path) -> Path:
        flags = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)} if os.name == 'nt' else {}
        if self._version is None:
            result = subprocess.run([self.decoder, '-version'], capture_output=True, check=True, timeout=5, **flags)
            self._version = result.stdout
        digest = hashlib.sha256(_PCM_SETTINGS + self._version)
        with source.open('rb') as original:
            for chunk in iter(lambda: original.read(262144), b''):
                digest.update(chunk)
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / (digest.hexdigest() + '.wav')
        if _valid_pcm(target):
            return target
        fd, temporary = tempfile.mkstemp(prefix='.decode-', suffix='.wav', dir=self.root)
        os.close(fd)
        try:
            subprocess.run([
                self.decoder, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                '-threads', '1', '-i', str(source), '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', '-threads', '1', temporary,
            ], capture_output=True, check=True, timeout=10, **flags)
            decoded = Path(temporary)
            if not _valid_pcm(decoded):
                raise ValueError('Decoder did not produce non-empty PCM audio')
            os.replace(decoded, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        # Keep generated data bounded across decoder upgrades/preset changes.
        # A loaded Windows WAV may be locked; leave it for a later cleanup.
        entries = sorted(self.root.glob('*.wav'), key=lambda item: item.stat().st_mtime, reverse=True)
        total = 0
        for index, item in enumerate(entries):
            total += item.stat().st_size
            if item != target and (index >= 128 or total > 128 * 1024 * 1024):
                try:
                    item.unlink()
                except OSError:
                    pass
        return target


class KillSoundPool(ReliableAudioPool):
    """Keep cues alive, gate on Ready, and fall back once if decoding fails."""
    converted = Signal(str, object, str)
    # More than one voice can overlap; cues queued during a cold decode are
    # bounded and expire instead of surprising the user long after a kill.
    MAX_PENDING = 8
    MAX_PENDING_AGE = 2.0

    def __init__(self, root: Path, count: int = 8):
        super().__init__(root, voices_per_sound=2, fallback_voices=count)
        paths = RuntimePaths.discover()
        decoder = resolve_kill_decoder(paths)
        self._cache = PcmCueCache(decoder, paths.user_data_root / 'audio_cache' / 'kill_fx') if decoder else None
        self._executor: ThreadPoolExecutor | None = None
        self._decoded: dict[str, Path] = {}
        self._converting: set[str] = set()
        self._decode_admitting = False
        self._conversion_failed: set[str] = set()
        self._waiting: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self._playback_epoch = 0
        self._closed = False
        self._decodes_finished = threading.Condition()
        self._active_decodes = 0
        self.converted.connect(self._converted, Qt.ConnectionType.QueuedConnection)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.close)

    def _sound_path(self, name: str) -> Path:
        return self._decoded.get(name) or super()._sound_path(name)

    def _queue_decode(self, name: str) -> bool:
        if self._closed or self._cache is None or name in self._conversion_failed:
            return False
        if name in self._decoded or name in self._converting:
            return True
        if self._decode_admitting:
            # Another cue may reenter while the executor is being constructed
            # or submit holds its non-reentrant lock. Use media for this request.
            return False
        if len(self._converting) >= 32:
            return False
        source = super()._sound_path(name)
        if not source.is_file() or source.suffix.lower() == '.wav':
            return False
        self._converting.add(name)
        self._decode_admitting = True
        try:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='kill-cue-decode')
            # Construction can reenter owner callbacks. A newly returned idle
            # executor must not admit work after close already sealed the pool.
            if self._closed:
                self._converting.discard(name)
                self._executor.shutdown(wait=False, cancel_futures=True)
                return False
            future = self._executor.submit(self._decode_owned, source)
        except Exception as exc:
            # submit may have queued or started work before raising. Retain
            # the executor/activity owner, never resubmit this cue, and ignore
            # an unreturned Future's result. Owner-thread fallback is separate
            # from proving that all uncertain decoder activity has retired.
            self._converted(name, None, f'{type(exc).__name__}: {exc}')
            return False
        finally:
            self._decode_admitting = False
        def finished(result):
            try:
                path, error = result.result(), ''
            except Exception as exc:
                detail = getattr(exc, 'stderr', b'') or b''
                if isinstance(detail, bytes):
                    detail = detail.decode('utf-8', errors='replace')
                path, error = None, (str(exc) + ': ' + str(detail)[-2048:]).rstrip(': ')
            if not self._closed:
                try:
                    self.converted.emit(name, path, error)
                except RuntimeError:
                    pass  # Qt owner was destroyed during application shutdown.
        future.add_done_callback(finished)
        return True

    def _decode_owned(self, source: Path) -> Path | None:
        # Thread.start can start a worker and then raise before the executor
        # records it. Track actual decoder entry independently of its futures.
        with self._decodes_finished:
            if self._closed:
                return None
            self._active_decodes += 1
        try:
            return self._cache.decode(source)
        finally:
            with self._decodes_finished:
                self._active_decodes -= 1
                self._decodes_finished.notify_all()

    def preload(self, names):
        if self._closed:
            return
        names = tuple(dict.fromkeys(str(name) for name in names))
        super().preload(names)
        if _media_preload_disabled():
            return
        for name in names:
            self._queue_decode(name)

    def play(self, name: str, volume: float) -> bool:
        if self._closed:
            return False
        self.last_sound = name
        # Check policy before enqueueing a cold decode. Keep the original gain:
        # ReliableAudioPool applies master volume once when playback starts.
        if cue_volume(volume) <= 0:
            return True
        if QMediaDevices.defaultAudioOutput().isNull():
            self.last_error = '没有可用的音频输出设备'
            _LOG.warning('Kill audio: %s', self.last_error)
            return False
        if name in self._decoded:
            if self._decoded[name].is_file():
                return super().play(name, volume)
            self._decoded.pop(name, None)
        playback_epoch = self._playback_epoch
        queued = self._queue_decode(name)
        if self._closed or playback_epoch != self._playback_epoch:
            return False
        if queued:
            pending = self._waiting[name]
            pending.append((time.monotonic(), volume))
            del pending[:-self.MAX_PENDING]
            return True
        # No decoder or a failed conversion: retain Qt media playback, but
        # report asynchronous errors via ReliableAudioPool's media callbacks.
        if self._cache is None:
            _LOG.warning('Kill audio PCM decoder unavailable; using Qt media backend')
        return super().play(name, volume)

    def _converted(self, name: str, path: Path | None, error: str):
        self._converting.discard(name)
        if self._closed:
            return
        pending = self._waiting.pop(name, [])
        playback_epoch = self._playback_epoch
        if path is not None:
            self._decoded[name] = Path(path)
            super().preload((name,))
        else:
            self._conversion_failed.add(name)
            self.last_error = f'击杀音效 PCM 解码失败: {name}: {error}'
            _LOG.warning(self.last_error)
        for requested, volume in pending:
            if self._closed or playback_epoch != self._playback_epoch:
                break
            if time.monotonic() - requested <= self.MAX_PENDING_AGE:
                # Exactly one chosen playback path for each queued request.
                super().play(name, volume)

    def stop(self) -> None:
        # Decode/cache work can finish, but old cues must not replay after a
        # focus restore, audio preference change, or application shutdown.
        self._playback_epoch += 1
        self._waiting.clear()
        super().stop()

    def close(self):
        """Owner-thread seal: stop Qt playback and cancel unstarted decodes.

        This is deliberately nonblocking for application aboutToQuit callers.
        A window lifecycle must also wait_closed on its teardown worker before
        claiming its decoder executor retired.
        """
        with self._decodes_finished:
            self._closed = True
        self.stop()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def wait_closed(self) -> None:
        """Worker-only retirement after close; never call Qt from this boundary."""
        if not self._closed:
            raise RuntimeError("Kill audio must be sealed on its owner thread before retirement")
        try:
            if self._executor is not None:
                # Also cancel here if Qt playback failure interrupted close.
                self._executor.shutdown(wait=True, cancel_futures=True)
        finally:
            # An unregistered worker may already be decoding. A delayed task
            # cannot enter after close seals the same condition above.
            with self._decodes_finished:
                while self._active_decodes:
                    self._decodes_finished.wait()
