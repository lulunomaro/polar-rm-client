"""Submission uncertainty falls back once without discarding decode ownership.

Real executors/threads use an inert cache; Qt playback is replaced by a recorder.
"""
import os
os.environ.update(QT_QPA_PLATFORM="offscreen", CI="true", POLAR_DISABLE_MEDIA_PRELOAD="1")
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from rm_client.kill_audio import KillSoundPool
from rm_client.ui_audio import ReliableAudioPool

APP = QApplication.instance() or QApplication([])


class KillAudioSubmissionFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("cue.ogg", "other.ogg"):
            (self.root / name).write_bytes(b"inert; never decoded")
        self.pool = KillSoundPool(self.root)
        self.decoded = []
        self.pool._cache = SimpleNamespace(decode=lambda source: self.decoded.append(source.name) or source)
        self.owner = threading.get_ident()
        self.plays = []
        self.patches = [
            patch("rm_client.kill_audio.QMediaDevices", SimpleNamespace(
                defaultAudioOutput=lambda: SimpleNamespace(isNull=lambda: False))),
            patch("rm_client.kill_audio.cue_volume", side_effect=lambda volume: volume),
            patch.object(ReliableAudioPool, "preload"),
            patch.object(ReliableAudioPool, "play", side_effect=self.record_play),
        ]
        for item in self.patches:
            item.start()

    def record_play(self, name, volume):
        self.assertEqual(threading.get_ident(), self.owner)
        self.plays.append((name, volume))
        return True

    def tearDown(self):
        self.pool.close()
        self.pool.wait_closed()
        APP.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def assert_failed_cue(self):
        self.assertNotIn("cue.ogg", self.pool._converting)
        self.assertIn("cue.ogg", self.pool._conversion_failed)
        self.assertNotIn("cue.ogg", self.pool._waiting)
        self.assertNotIn("cue.ogg", self.pool._decoded)

    def test_executor_constructor_rejection_falls_back_without_retry(self):
        with patch("rm_client.kill_audio.ThreadPoolExecutor", side_effect=OSError("executor rejected")) as create:
            self.assertTrue(self.pool.play("cue.ogg", 31))
            self.assertTrue(self.pool.play("cue.ogg", 42))
        create.assert_called_once()
        self.assertEqual(self.plays, [("cue.ogg", 31), ("cue.ogg", 42)])
        self.assert_failed_cue()

    def test_reentrant_constructor_request_joins_one_failure_and_falls_back_once(self):
        entered = []
        def reject(**_):
            if not entered:
                entered.append(True)
                self.assertTrue(self.pool.play("cue.ogg", 22))
            raise OSError("constructor rejected")
        with patch("rm_client.kill_audio.ThreadPoolExecutor", side_effect=reject) as create:
            self.assertTrue(self.pool.play("cue.ogg", 31))
        create.assert_called_once()
        self.assertEqual(self.plays, [("cue.ogg", 22), ("cue.ogg", 31)])
        self.assert_failed_cue()

    def test_submit_rejection_drains_reentrant_fresh_requests_but_not_expired(self):
        executor = Mock()
        self.pool._executor = executor
        def reject(*_):
            self.pool._waiting["cue.ogg"].append((time.monotonic() - 10, 11))
            self.assertTrue(self.pool.play("cue.ogg", 22))
            raise OSError("submit rejected")
        executor.submit.side_effect = reject
        self.assertTrue(self.pool.play("cue.ogg", 31))
        self.assertTrue(self.pool.play("cue.ogg", 42))
        executor.submit.assert_called_once()
        self.assertIs(self.pool._executor, executor)
        self.assertEqual(self.plays, [("cue.ogg", 22), ("cue.ogg", 31), ("cue.ogg", 42)])
        self.assert_failed_cue()

    def test_different_cue_reentry_cannot_replace_executor_or_nest_submit(self):
        for stage in ("constructor", "submit"):
            with self.subTest(stage=stage):
                pool = KillSoundPool(self.root)
                pool._cache = self.pool._cache
                executor = Mock()
                calls = []
                def submit(*_):
                    calls.append("submit")
                    if stage == "submit" and calls.count("submit") == 1:
                        self.assertTrue(pool.play("other.ogg", 22))
                    raise OSError("submission rejected")
                def create(**_):
                    calls.append("constructor")
                    if stage == "constructor" and calls.count("constructor") == 1:
                        self.assertTrue(pool.play("other.ogg", 22))
                    return executor
                executor.submit.side_effect = submit
                try:
                    with patch("rm_client.kill_audio.ThreadPoolExecutor", side_effect=create):
                        self.assertTrue(pool.play("cue.ogg", 31))
                    self.assertEqual(calls, ["constructor", "submit"])
                    self.assertIs(pool._executor, executor)
                    self.assertFalse(pool._decode_admitting)
                    self.assertNotIn("other.ogg", pool._converting)
                finally:
                    pool.close()
                    pool.wait_closed()

    def test_real_queued_work_after_start_rejection_is_not_retried_or_replayed(self):
        real_start = threading.Thread.start
        def reject(worker):
            if worker.name.startswith("kill-cue-decode"):
                raise OSError("not admitted")
            real_start(worker)
        with patch.object(threading.Thread, "start", reject):
            self.assertTrue(self.pool.play("cue.ogg", 31))
        self.assertEqual(self.decoded, [])
        self.assert_failed_cue()
        self.assertTrue(self.pool.play("cue.ogg", 42))
        self.assertTrue(self.pool.play("other.ogg", 53))
        self.pool._executor.shutdown(wait=True)
        APP.processEvents()
        self.assertCountEqual(self.decoded, ["cue.ogg", "other.ogg"])
        self.assertEqual(self.plays, [("cue.ogg", 31), ("cue.ogg", 42), ("other.ogg", 53)])
        self.assert_failed_cue()

    def test_real_running_and_finished_late_errors_never_replay_or_lose_owner(self):
        real_start = threading.Thread.start
        for finished in (False, True):
            with self.subTest(finished=finished):
                # Separate cues avoid resetting the production terminal failure policy.
                name = "other.ogg" if finished else "cue.ogg"
                entered, release = threading.Event(), threading.Event()
                workers = []
                def decode(source):
                    self.decoded.append(source.name)
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError("test must release fake decode")
                    return source
                self.pool._cache = SimpleNamespace(decode=decode)
                if finished:
                    release.set()
                    # The previous executor is sealed; retain it until confirmed
                    # closed before installing a new idle test executor.
                    self.pool._executor.shutdown(wait=True)
                    self.pool._executor = None
                def late_start(worker):
                    real_start(worker)
                    if worker.name.startswith("kill-cue-decode"):
                        workers.append(worker)
                        self.assertTrue(entered.wait(1))
                        if finished:
                            deadline = time.monotonic() + 1
                            while self.pool._active_decodes and time.monotonic() < deadline:
                                time.sleep(.001)
                            self.assertEqual(self.pool._active_decodes, 0)
                        raise OSError("late admission")
                try:
                    with patch.object(threading.Thread, "start", late_start):
                        self.assertTrue(self.pool.play(name, 31))
                    self.assertIn(name, self.pool._conversion_failed)
                    self.assertNotIn(name, self.pool._converting)
                    if not finished:
                        self.assertEqual(self.pool._active_decodes, 1)
                    release.set()
                    self.pool._executor.shutdown(wait=True)
                    for worker in workers:
                        worker.join(2)
                        self.assertFalse(worker.is_alive())
                    APP.processEvents()
                    self.assertNotIn(name, self.pool._decoded)
                    self.assertEqual(self.plays.count((name, 31)), 1)
                finally:
                    release.set()
                    self.pool._executor.shutdown(wait=True, cancel_futures=True)
                    for worker in workers:
                        worker.join(2)

    def test_reentrant_close_during_constructor_never_submits_or_plays(self):
        executor = Mock()
        def create(**_):
            self.pool.close()
            self.pool.wait_closed()
            return executor
        with patch("rm_client.kill_audio.ThreadPoolExecutor", side_effect=create):
            self.assertFalse(self.pool.play("cue.ogg", 31))
        executor.submit.assert_not_called()
        executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        self.assertEqual(self.plays, [])
        self.assertFalse(self.pool._waiting)

    def test_reentrant_close_during_submit_never_queues_or_falls_back(self):
        for reject in (False, True):
            with self.subTest(reject=reject):
                pool = KillSoundPool(self.root)
                pool._cache = self.pool._cache
                executor = pool._executor = Mock()
                def submit(*_):
                    pool.close()
                    if reject:
                        raise OSError("submit rejected after seal")
                    return Mock()
                executor.submit.side_effect = submit
                try:
                    self.assertFalse(pool.play("cue.ogg", 31))
                    self.assertFalse(pool._waiting)
                    self.assertEqual(self.plays, [])
                finally:
                    pool.close()
                    pool.wait_closed()

    def test_reentrant_stop_or_close_cancels_remaining_failure_fallbacks(self):
        for action in ("stop", "close"):
            with self.subTest(action=action):
                pool = KillSoundPool(self.root)
                pool._cache = self.pool._cache
                executor = pool._executor = Mock()
                played = []
                def submit(*_):
                    self.assertTrue(pool.play("cue.ogg", 22))
                    self.assertTrue(pool.play("cue.ogg", 33))
                    raise OSError("submit rejected")
                def play(name, volume):
                    self.assertEqual(threading.get_ident(), self.owner)
                    played.append((name, volume))
                    getattr(pool, action)()
                    return True
                executor.submit.side_effect = submit
                try:
                    with patch.object(ReliableAudioPool, "play", side_effect=play):
                        self.assertFalse(pool.play("cue.ogg", 44))
                    self.assertEqual(played, [("cue.ogg", 22)])
                    self.assertFalse(pool._waiting)
                    self.assertNotIn("cue.ogg", pool._converting)
                finally:
                    pool.close()
                    pool.wait_closed()


if __name__ == "__main__":
    unittest.main()
