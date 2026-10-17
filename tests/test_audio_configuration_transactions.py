"""Audio-page disk, cache, pool, and registry commits form one transaction."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client import audio_preferences as audio
from rm_client.configuration.transactions import ConfigurationApplyError
from rm_client.configuration import files as configuration_files
from rm_client.model import build_default_registry
from rm_client.ui.cs2_settings_center import RegistrySettingsPage
from test_cs2_settings_center import Host


class RecordingPool:
    def __init__(self, *, fail_volume=None):
        self.volumes = []
        self.fail_volume = fail_volume

    def apply_audio_preferences(self):
        volume = audio.current_audio().master_volume
        self.volumes.append(volume)
        if volume == self.fail_volume:
            raise OSError("synthetic pool refresh failure")


class AudioConfigurationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "audio.json"
        self.path_patch = patch.object(audio, "audio_path", return_value=self.path)
        self.pools_patch = patch.object(audio, "POOLS", weakref.WeakSet())
        self.path_patch.start()
        self.pools_patch.start()
        audio.current_audio.cache_clear()
        audio.selected_output.cache_clear()
        self.host = Host()
        self.page = None
        self.addCleanup(self.close)

    def close(self):
        if self.page is not None:
            self.page.deleteLater()
        self.host.deleteLater()
        audio.current_audio.cache_clear()
        audio.selected_output.cache_clear()
        self.pools_patch.stop()
        self.path_patch.stop()
        self.directory.cleanup()

    def saved_audio(self):
        audio.save_audio(audio.AudioPreferences(master_volume=66))
        return self.path.read_bytes()

    def stage_page(self):
        self.page = RegistrySettingsPage(self.host, "音频", build_default_registry())
        self.page.audio_controls["master_volume"].spin.setValue(23)
        self.page.controls[("AudioFeedback", "UI Volume")].spin.setValue(17)
        return self.host.registry.get("AudioFeedback").get_setting("UI Volume")

    def test_audio_disk_failure_rolls_back_registry_and_keeps_page_draft(self):
        before = self.saved_audio()
        setting = self.stage_page()
        previous = setting.value
        baseline = dict(self.page._baseline)
        with patch.object(configuration_files.os, "replace", side_effect=OSError("synthetic disk replace failure")):
            with self.assertRaises((OSError, ValueError)):
                self.page.apply_draft()
        self.assertEqual(setting.value, previous)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.page._baseline, baseline)
        self.assertEqual(self.page.audio_controls["master_volume"].spin.value(), 23)
        self.host.auto_save_config.assert_not_called()
        self.host.backend.trigger_estop.assert_called_once()

    def test_pool_failure_restores_file_cache_all_pools_and_registry(self):
        before = self.saved_audio()
        setting = self.stage_page()
        previous = setting.value
        failing, healthy = RecordingPool(fail_volume=23), RecordingPool()
        audio.POOLS.update((failing, healthy))
        with self.assertRaises((OSError, ValueError)):
            self.page.apply_draft()
        self.assertEqual(setting.value, previous)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(audio.current_audio().master_volume, 66)
        self.assertEqual(failing.volumes, [23, 66])
        self.assertEqual(healthy.volumes, [23, 66])
        self.host.auto_save_config.assert_not_called()

    def test_real_pool_device_setters_restore_all_playback_paths_after_partial_failure(self):
        from rm_client.ui_audio import ReliableAudioPool

        before = self.saved_audio()
        pool = ReliableAudioPool(Path(self.directory.name))
        self.addCleanup(pool.deleteLater)
        voice, fallback, media = Mock(), Mock(), Mock()
        fallback_player, media_player = Mock(), Mock()
        pool._effects = {"cue": [voice]}
        pool._fallback = [(fallback_player, fallback)]
        pool._media_effects = {"cue": [(media_player, media)]}
        pool._pending = {"cue": [20.0]}
        audio.POOLS.add(pool)

        def reject_candidate(device):
            if device == "candidate-device":
                raise OSError("synthetic media output setter failure")

        media.setDevice.side_effect = reject_candidate
        # Exercise the actual pool method and both setter APIs without opening
        # Qt multimedia outputs or claiming a physical device test.
        selector = Mock(side_effect=lambda: "candidate-device" if audio.current_audio().master_volume == 23 else "prior-device")
        with patch("rm_client.ui_audio.selected_output", selector):
            with self.assertRaises(ValueError):
                audio.save_audio(audio.AudioPreferences(master_volume=23))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(pool._pending, {})
        self.assertEqual([call.args[0] for call in voice.setAudioDevice.call_args_list],
                         ["candidate-device", "prior-device"])
        for output in (fallback, media):
            self.assertEqual([call.args[0] for call in output.setDevice.call_args_list],
                             ["candidate-device", "prior-device"])
        for player in (voice, fallback_player, media_player):
            self.assertEqual(player.stop.call_count, 2)

    def test_standalone_audio_pool_failure_restores_missing_file(self):
        pool = RecordingPool(fail_volume=23)
        audio.POOLS.add(pool)
        with self.assertRaises((OSError, ValueError)):
            audio.save_audio(audio.AudioPreferences(master_volume=23))
        self.assertFalse(self.path.exists())
        self.assertEqual(audio.current_audio(), audio.AudioPreferences())
        self.assertEqual(pool.volumes, [23, 100])

    def test_registry_effect_failure_never_commits_audio(self):
        before = self.saved_audio()
        setting = self.stage_page()
        previous = setting.value
        pool = RecordingPool()
        audio.POOLS.add(pool)
        self.host.after_setting_changed.side_effect = OSError("synthetic registry effect failure")
        with self.assertRaises(ConfigurationApplyError):
            self.page.apply_draft()
        self.assertEqual(setting.value, previous)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(pool.volumes, [])
        self.host.auto_save_config.assert_not_called()

    def test_success_commits_both_stores_and_schedules_one_save(self):
        self.saved_audio()
        setting = self.stage_page()
        pool = RecordingPool()
        audio.POOLS.add(pool)
        self.page.apply_draft()
        self.assertEqual(setting.value, 17)
        self.assertEqual(json.loads(self.path.read_text()), asdict(audio.AudioPreferences(master_volume=23)))
        self.assertEqual(pool.volumes, [23])
        self.host.auto_save_config.assert_called_once_with()
        self.assertEqual(self.page._baseline, self.page._draft_values())

    def test_rollback_preserves_exact_file_formatting(self):
        before = b'{ "master_volume": 66, "device_id": "default", "mute_unfocused": false }\n'
        self.path.write_bytes(before)
        audio.current_audio.cache_clear()
        pool = RecordingPool(fail_volume=23)
        audio.POOLS.add(pool)
        with self.assertRaises(ValueError):
            audio.save_audio(audio.AudioPreferences(master_volume=23))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(audio.current_audio().master_volume, 66)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_failed_pool_rollback_does_not_skip_registry_or_other_pools(self):
        before = self.saved_audio()
        setting = self.stage_page()
        previous = setting.value

        class AlwaysFailingPool(RecordingPool):
            def apply_audio_preferences(self):
                super().apply_audio_preferences()
                raise OSError("persistent pool failure")

        failing, healthy = AlwaysFailingPool(), RecordingPool()
        audio.POOLS.update((failing, healthy))
        with self.assertRaises(ConfigurationApplyError) as caught:
            self.page.apply_draft()
        self.assertFalse(caught.exception.restored)
        self.assertEqual(len(caught.exception.restore_errors), 1)
        self.assertIsNone(caught.exception.safety_error)
        self.assertEqual(setting.value, previous)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(healthy.volumes, [23, 66])
        self.host.backend.trigger_estop.assert_called_once()
        self.host.auto_save_config.assert_not_called()

    def test_standalone_disk_rollback_failure_is_reported_honestly(self):
        self.saved_audio()
        pool = RecordingPool(fail_volume=23)
        audio.POOLS.add(pool)
        original_replace = configuration_files.os.replace
        writes = 0

        def fail_restore(source, destination):
            nonlocal writes
            writes += 1
            if writes > 1:
                raise OSError("synthetic rollback disk failure")
            return original_replace(source, destination)

        with patch.object(configuration_files.os, "replace", side_effect=fail_restore):
            with self.assertRaisesRegex(ValueError, "rollback failed.*rollback disk failure"):
                audio.save_audio(audio.AudioPreferences(master_volume=23))
        self.assertEqual(audio.current_audio().master_volume, 23)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
