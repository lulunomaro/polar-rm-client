"""Real-file write/cleanup interruption tests; no native audio output is opened."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client import audio_preferences as audio
from rm_client.configuration import files
from rm_client.configuration.transactions import apply_configuration_transaction, ConfigurationApplyError
from rm_client.model import build_default_registry


class Pool:
    def __init__(self):
        self.volumes = []

    def apply_audio_preferences(self):
        self.volumes.append(audio.current_audio().master_volume)


class AudioWriteAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "audio.json"
        self.before = b'{ "master_volume": 66, "device_id": "default", "mute_unfocused": false }\n'
        self.path.write_bytes(self.before)
        self.pool = Pool()
        for target, name, value in ((audio, "audio_path", lambda: self.path),
                                    (audio, "POOLS", weakref.WeakSet((self.pool,)))):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        audio.current_audio.cache_clear()
        audio.selected_output.cache_clear()
        self.addCleanup(audio.current_audio.cache_clear)
        self.addCleanup(audio.selected_output.cache_clear)
        self.assertEqual(audio.current_audio().master_volume, 66)
        self.registry = build_default_registry()
        self.setting = self.registry.get("AudioFeedback").get_setting("UI Volume")
        self.previous_setting = self.setting.value
        self.safety_calls = []

    def change(self):
        return audio.prepare_audio_change(audio.AudioPreferences(master_volume=23))

    def transact(self, change):
        apply_configuration_transaction(
            self.registry, quiesce=lambda: None,
            update=lambda: setattr(self.setting, "value", 17),
            restore_runtime=lambda: None, fail_closed=lambda: self.safety_calls.append("closed"),
            participants=(change,),
        )

    def fail_one_cleanup(self, failure):
        original = Path.unlink
        attempted = []
        def unlink(path, *args, **kwargs):
            if path.suffix == ".tmp" and not attempted:
                attempted.append(path)
                raise failure
            return original(path, *args, **kwargs)
        return patch.object(Path, "unlink", unlink)

    def assert_restored(self, error):
        self.assertTrue(error.restored)
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(audio.current_audio().master_volume, 66)
        self.assertEqual(self.pool.volumes, [66])
        self.assertEqual(self.setting.value, self.previous_setting)
        self.assertEqual(self.safety_calls, ["closed"])

    def test_post_replace_cleanup_failure_restores_exact_disk_cache_pool_and_registry(self):
        failure = PermissionError("temporary cleanup denied after replacement")
        change = self.change()
        with self.fail_one_cleanup(failure), self.assertRaises(ConfigurationApplyError) as caught:
            self.transact(change)
        self.assertIs(caught.exception.cause, failure)
        self.assert_restored(caught.exception)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_post_replace_cleanup_restores_prior_absence(self):
        self.path.unlink()
        audio.current_audio.cache_clear()
        change = self.change()
        with self.fail_one_cleanup(PermissionError("cleanup")), self.assertRaises(ConfigurationApplyError) as caught:
            self.transact(change)
        self.assertTrue(caught.exception.restored)
        self.assertFalse(self.path.exists())
        self.assertEqual(audio.current_audio(), audio.AudioPreferences())
        self.assertEqual(self.pool.volumes, [100])
        self.assertEqual(self.setting.value, self.previous_setting)

    def test_replace_then_raise_wrapper_retains_original_failure_and_rolls_back(self):
        original = files.os.replace
        failure = OSError("replacement admitted then failed")
        calls = []
        def replace(source, destination):
            original(source, destination)
            calls.append(destination)
            if len(calls) == 1:
                raise failure
        with patch.object(files.os, "replace", replace), self.assertRaises(ConfigurationApplyError) as caught:
            self.transact(self.change())
        self.assertIs(caught.exception.cause, failure)
        self.assert_restored(caught.exception)

    def test_pre_replace_failure_conservatively_restores_snapshot(self):
        original = files.os.replace
        failure = OSError("replacement rejected")
        calls = []
        def replace(source, destination):
            calls.append(destination)
            if len(calls) == 1:
                raise failure
            return original(source, destination)
        with patch.object(files.os, "replace", replace), self.assertRaises(ConfigurationApplyError) as caught:
            self.transact(self.change())
        self.assertIs(caught.exception.cause, failure)
        self.assert_restored(caught.exception)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_write_and_cleanup_failures_are_both_structurally_preserved(self):
        primary, cleanup = OSError("replace failed"), PermissionError("cleanup failed")
        with patch.object(files.os, "replace", side_effect=primary), self.fail_one_cleanup(cleanup):
            with self.assertRaises(OSError) as caught:
                files.atomic_write_bytes(self.path, b"candidate")
        self.assertIs(getattr(caught.exception, "cause", None), primary)
        self.assertIs(getattr(caught.exception, "cleanup_error", None), cleanup)
        self.assertIs(caught.exception.__cause__, primary)
        self.assertIn("replace failed", str(caught.exception))
        self.assertIn("cleanup failed", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(len(list(self.path.parent.glob("*.tmp"))), 1)

    def test_failed_restoration_remains_observable_and_retryable(self):
        original = files.os.replace
        failure = OSError("late write failure")
        calls = []
        def replace(source, destination):
            calls.append(destination)
            if len(calls) == 1:
                original(source, destination)
                raise failure
            raise OSError("restore rejected")
        change = self.change()
        with patch.object(files.os, "replace", replace), self.assertRaises(ConfigurationApplyError) as caught:
            self.transact(change)
        self.assertFalse(caught.exception.restored)
        self.assertEqual(len(caught.exception.restore_errors), 1)
        self.assertIs(caught.exception.cause, failure)
        self.assertEqual(json.loads(self.path.read_text())["master_volume"], 23)
        self.assertEqual(audio.current_audio().master_volume, 23)
        change.rollback()
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(audio.current_audio().master_volume, 66)

    def test_unattempted_participant_does_not_overwrite_another_writer(self):
        change = self.change()
        newer = b'{"master_volume": 81}'
        self.path.write_bytes(newer)
        change.rollback()
        self.assertEqual(self.path.read_bytes(), newer)
        self.assertEqual(self.pool.volumes, [])

    def test_standalone_save_restores_after_cleanup_error(self):
        failure = PermissionError("cleanup after standalone save")
        with self.fail_one_cleanup(failure), self.assertRaises(PermissionError) as caught:
            audio.save_audio(audio.AudioPreferences(master_volume=23))
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(audio.current_audio().master_volume, 66)
        self.assertEqual(self.pool.volumes, [66])

    def test_restoration_cleanup_failure_is_not_reported_as_restored(self):
        original = Path.unlink
        def reject_temp_cleanup(path, *args, **kwargs):
            if path.suffix == ".tmp":
                raise PermissionError("persistent cleanup denial")
            return original(path, *args, **kwargs)
        change = self.change()
        with patch.object(Path, "unlink", reject_temp_cleanup), self.assertRaises(ConfigurationApplyError) as caught:
            self.transact(change)
        self.assertFalse(caught.exception.restored)
        self.assertEqual(len(caught.exception.restore_errors), 1)
        # Replacement restored the bytes, but its cleanup did not complete.
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(self.pool.volumes, [66])
        change.rollback()
        self.assertEqual(self.pool.volumes, [66, 66])

    def test_serialization_failure_before_write_admission_leaves_other_owner_intact(self):
        change = self.change()
        newer = b'{"master_volume": 81}'
        self.path.write_bytes(newer)
        with patch.object(audio.json, "dumps", side_effect=ValueError("serialize")):
            with self.assertRaises(ConfigurationApplyError) as caught:
                self.transact(change)
        self.assertTrue(caught.exception.restored)
        self.assertEqual(self.path.read_bytes(), newer)
        self.assertEqual(self.pool.volumes, [])

    def test_success_removes_temp_and_refreshes_once(self):
        self.transact(self.change())
        self.assertEqual(json.loads(self.path.read_text()), asdict(audio.AudioPreferences(master_volume=23)))
        self.assertEqual(self.pool.volumes, [23])
        self.assertEqual(self.safety_calls, [])
        self.assertFalse(list(self.path.parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
