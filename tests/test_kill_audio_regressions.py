"""Deterministic Qt signal/queue and actual decoder tests, never audio output."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import time
import wave
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from rm_client.audio_preferences import AudioPreferences
from rm_client.kill_audio import KillSoundPool, PcmCueCache, _valid_pcm
from rm_client.kill_fx import KillFxRenderer
from rm_client.ui_audio import ReliableAudioPool

# The audio pool observes application focus, just as it does in production.
APP = QGuiApplication.instance() or QGuiApplication([])

def wav(path):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(2); out.setsampwidth(2); out.setframerate(48000)
        out.writeframes(b'\x01\x00\x02\x00' * 480)
    return path

class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.pool = KillSoundPool(self.root)
        self.devices = patch('rm_client.kill_audio.QMediaDevices', SimpleNamespace(defaultAudioOutput=lambda: SimpleNamespace(isNull=lambda: False)))
        self.devices.start()
    def tearDown(self):
        self.pool.close(); self.devices.stop(); self.temp.cleanup()
    def test_card_unsupported_types_use_original_normal_cue(self):
        renderer = object.__new__(KillFxRenderer)
        for kind in ('ASSIST', 'DESTROY_VEHICLE'):
            self.assertEqual(renderer._sound_name(kind, 1, 'Card'), 'cardkillsound_default_cs.ogg')
        self.assertEqual(renderer._sound_name('HEADSHOT', 1, 'Card'), 'cardkillsound_armorheadshot_cs.ogg')
    def test_all_presets_resolve_to_verified_original_assets(self):
        import json
        from rm_client.model import build_default_registry
        from rm_client.kill_fx import KILL_TYPES
        manifest = json.loads((Path(__file__).parent/'fixtures/kill_fx_audio_manifest.json').read_text())
        renderer = object.__new__(KillFxRenderer)
        presets = build_default_registry().get('EliminationFX').get_setting('Preset').options
        for preset in presets:
            for kind in KILL_TYPES:
                for combo in range(1, 7):
                    self.assertIn(renderer._sound_name(kind, combo, preset), manifest['sounds'], (preset, kind, combo))
    def test_mute_does_not_decode_or_play(self):
        with patch.object(self.pool, '_queue_decode') as decode, patch.object(ReliableAudioPool, 'play') as media:
            self.assertTrue(self.pool.play('cue.ogg', 0))
            decode.assert_not_called(); media.assert_not_called()
    def test_master_muted_cold_request_does_not_enter_decode_queue(self):
        with patch('rm_client.audio_preferences.current_audio', return_value=AudioPreferences(master_volume=0)), patch.object(self.pool, '_queue_decode') as decode, patch.object(ReliableAudioPool, 'play') as play:
            self.assertTrue(self.pool.play('cue.ogg', 55))
            decode.assert_not_called(); play.assert_not_called()
            self.assertFalse(self.pool._waiting)
    def test_focus_muted_cold_request_does_not_enter_decode_queue(self):
        with patch('rm_client.audio_preferences.current_audio', return_value=AudioPreferences(mute_unfocused=True)), patch.object(QGuiApplication, 'applicationState', return_value=Qt.ApplicationState.ApplicationInactive), patch.object(self.pool, '_queue_decode') as decode, patch.object(ReliableAudioPool, 'play') as play:
            self.assertTrue(self.pool.play('cue.ogg', 55))
            decode.assert_not_called(); play.assert_not_called()
            self.assertFalse(self.pool._waiting)
    def test_audio_apply_cancels_cold_requests_before_later_unmute(self):
        with patch.object(self.pool, '_queue_decode', return_value=True), patch.object(ReliableAudioPool, 'play') as play, patch.object(ReliableAudioPool, 'preload'):
            self.pool.play('cue.ogg', 55)
            self.assertTrue(self.pool._waiting)
            self.pool.apply_audio_preferences()
            self.assertFalse(self.pool._waiting)
            self.pool._converted('cue.ogg', wav(self.root/'cached.wav'), '')
            play.assert_not_called()
            self.assertIn('cue.ogg', self.pool._decoded)  # Keep useful cache work.
    def test_focus_loss_cancels_cold_requests_before_focus_returns(self):
        with patch.object(self.pool, '_queue_decode', return_value=True), patch.object(ReliableAudioPool, 'play') as play, patch.object(ReliableAudioPool, 'preload'):
            self.pool.play('cue.ogg', 55)
            with patch('rm_client.audio_preferences.current_audio', return_value=AudioPreferences(mute_unfocused=True)), patch.object(QGuiApplication, 'applicationState', return_value=Qt.ApplicationState.ApplicationInactive):
                self.pool._on_focus_changed(Qt.ApplicationState.ApplicationInactive)
            self.assertFalse(self.pool._waiting)
            self.pool._converted('cue.ogg', wav(self.root/'cached.wav'), '')
            play.assert_not_called()
    def test_focus_loss_without_background_mute_preserves_cold_request(self):
        with patch('rm_client.audio_preferences.current_audio', return_value=AudioPreferences()), patch.object(self.pool, '_queue_decode', return_value=True), patch.object(ReliableAudioPool, 'play', return_value=True) as play, patch.object(ReliableAudioPool, 'preload'):
            self.pool.play('cue.ogg', 55)
            self.pool._on_focus_changed(Qt.ApplicationState.ApplicationInactive)
            self.pool._converted('cue.ogg', wav(self.root/'cached.wav'), '')
            play.assert_called_once_with('cue.ogg', 55)
    def test_master_gain_is_not_applied_twice_by_early_policy_gate(self):
        with patch('rm_client.audio_preferences.current_audio', return_value=AudioPreferences(master_volume=50)), patch.object(self.pool, '_queue_decode', return_value=False), patch.object(ReliableAudioPool, 'play', return_value=True) as play:
            self.assertTrue(self.pool.play('cue.ogg', 55))
            play.assert_called_once_with('cue.ogg', 55)
    def test_close_cancels_all_pending_stops_players_and_rejects_new_requests(self):
        effect, player, preloaded = Mock(), Mock(), Mock()
        self.pool._effects['cue.wav'] = [effect]
        self.pool._fallback.append((player, object()))
        self.pool._media_effects['cue.ogg'] = [(preloaded, object())]
        self.pool._pending['cue.wav'] = [55]
        self.pool._waiting['cue.ogg'] = [(time.monotonic(), 55)]
        self.pool.close()
        self.assertFalse(self.pool._waiting)
        self.assertFalse(self.pool._pending)
        effect.stop.assert_called_once(); player.stop.assert_called_once(); preloaded.stop.assert_called_once()
        with patch.object(self.pool, '_queue_decode') as decode, patch.object(ReliableAudioPool, 'play') as play:
            self.assertFalse(self.pool.play('cue.ogg', 55))
            decode.assert_not_called(); play.assert_not_called()
    def test_missing_output_is_reported_without_play(self):
        with patch('rm_client.kill_audio.QMediaDevices', SimpleNamespace(defaultAudioOutput=lambda: SimpleNamespace(isNull=lambda: True))), patch.object(ReliableAudioPool, 'play') as media:
            self.assertFalse(self.pool.play('cue.ogg', 55))
            self.assertIn('音频输出设备', self.pool.last_error)
            media.assert_not_called()
    def test_cold_request_waits_then_plays_once(self):
        with patch.object(self.pool, '_queue_decode', return_value=True), patch.object(ReliableAudioPool, 'play', return_value=True) as play, patch.object(ReliableAudioPool, 'preload'):
            self.assertTrue(self.pool.play('cue.ogg', 55)); play.assert_not_called()
            self.pool._converted('cue.ogg', wav(self.root/'cached.wav'), '')
            play.assert_called_once_with('cue.ogg', 55)
            self.assertEqual(self.pool._sound_path('cue.ogg'), self.root/'cached.wav')
    def test_decode_failure_falls_back_once(self):
        with patch.object(self.pool, '_queue_decode', return_value=True), patch.object(ReliableAudioPool, 'play', return_value=True) as play:
            self.pool.play('cue.ogg', 31)
            self.pool._converted('cue.ogg', None, 'bad decoder')
            play.assert_called_once_with('cue.ogg', 31)
            self.assertIn('cue.ogg', self.pool._conversion_failed)
    def test_expired_cues_do_not_play_late(self):
        self.pool._waiting['cue.ogg'] = [(time.monotonic()-10, 50)]
        with patch.object(ReliableAudioPool, 'play') as play, patch.object(ReliableAudioPool, 'preload'):
            self.pool._converted('cue.ogg', wav(self.root/'cached.wav'), '')
            play.assert_not_called()
    def test_pending_is_bounded(self):
        with patch.object(self.pool, '_queue_decode', return_value=True):
            for _ in range(100): self.pool.play('cue.ogg', 50)
        self.assertEqual(len(self.pool._waiting['cue.ogg']), self.pool.MAX_PENDING)
    def test_unavailable_decoder_uses_media(self):
        self.pool._cache = None
        with patch.object(ReliableAudioPool, 'play', return_value=False) as play:
            self.assertFalse(self.pool.play('cue.ogg', 65))
            play.assert_called_once_with('cue.ogg', 65)
    def test_conversion_runs_off_caller_thread_and_signal_returns(self):
        import threading
        caller = threading.get_ident(); workers = []
        cue = self.root/'cue.ogg'; cue.write_bytes(b'original')
        decoded = wav(self.root/'cached.wav')
        def decode(source):
            workers.append(threading.get_ident()); return decoded
        self.pool._cache = SimpleNamespace(decode=decode)
        with patch.object(ReliableAudioPool, 'preload'):
            self.assertTrue(self.pool._queue_decode('cue.ogg'))
            deadline = time.monotonic()+3
            while not self.pool._decoded and time.monotonic()<deadline:
                APP.processEvents(); time.sleep(.005)
        self.assertEqual(self.pool._decoded['cue.ogg'], decoded)
        self.assertNotEqual(workers[0], caller)
    def test_cache_content_hash_settings_version_and_atomic_result(self):
        source=self.root/'cue.ogg'; source.write_bytes(b'original')
        calls=[]
        def run(args, **kwargs):
            calls.append(args)
            if '-version' in args: return SimpleNamespace(stdout=b'ffmpeg fixture version')
            wav(Path(args[-1])); return SimpleNamespace(stdout=b'')
        cache=PcmCueCache('ffmpeg', self.root/'cache')
        with patch('rm_client.kill_audio.subprocess.run', side_effect=run):
            first=cache.decode(source); second=cache.decode(source)
            self.assertEqual(first, second); self.assertEqual(len(calls), 2)
            self.assertTrue(_valid_pcm(first)); self.assertEqual(list(cache.root.glob('.decode-*')), [])
            source.write_bytes(b'changed')
            self.assertNotEqual(cache.decode(source), first)
    def test_pcm_waits_for_sound_effect_ready(self):
        from PySide6.QtMultimedia import QSoundEffect
        pool = ReliableAudioPool(self.root)
        path = wav(self.root/'ready.wav')
        calls = []
        voice = SimpleNamespace(status=lambda: QSoundEffect.Status.Loading,
            setVolume=lambda value: calls.append(('volume', value)),
            play=lambda: calls.append(('play',)), isPlaying=lambda: False)
        pool._effects[path.name] = [voice]
        with patch.object(pool, 'preload'):
            self.assertTrue(pool.play(path.name, 55))
        self.assertEqual(calls, [])
        pool._on_status(path.name, voice, QSoundEffect.Status.Ready)
        self.assertEqual(calls, [('volume', .55), ('play',)])
        self.assertEqual(pool._pending[path.name], [])
    def test_pcm_error_uses_one_media_fallback(self):
        from PySide6.QtMultimedia import QSoundEffect
        pool = ReliableAudioPool(self.root)
        pool._pending['cue.wav'] = [55]
        with patch.object(pool, '_play_fallback') as fallback:
            pool._on_status('cue.wav', object(), QSoundEffect.Status.Error)
            pool._on_status('cue.wav', object(), QSoundEffect.Status.Error)
            fallback.assert_called_once()
    def test_media_errors_are_observable(self):
        from PySide6.QtCore import QUrl
        pool = ReliableAudioPool(self.root)
        pool._on_media_error(SimpleNamespace(source=lambda:QUrl.fromLocalFile('/cue.ogg')), 'Unsupported codec')
        self.assertIn('Unsupported codec', pool.last_error)
    def test_decoder_failure_leaves_no_partial_cache(self):
        source=self.root/'cue.ogg';source.write_bytes(b'original')
        cache=PcmCueCache('ffmpeg', self.root/'cache'); cache._version=b'fixture'
        with patch('rm_client.kill_audio.subprocess.run', side_effect=TimeoutError('timeout')):
            with self.assertRaises(TimeoutError):cache.decode(source)
        self.assertEqual(list(cache.root.iterdir()), [])

if __name__ == '__main__': unittest.main()

