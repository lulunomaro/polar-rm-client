"""Native-QObject lifetime at two first-feedback-service failure prefixes.

No source loading or playback; QMediaDevices initialization remains real.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
               str(Path(__file__).resolve().parent)]
import test_early_window_factory_failures as fixtures
from rm_client import kill_audio, kill_fx


class FeedbackPartialConstructorFailureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EarlyWindowFactoryFailureTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def assert_prefix_retires(self, target, name):
        refs, destroyed = [], []
        original = kill_audio.ReliableAudioPool.__init__
        error = OSError("first feedback service rejected at " + name)

        def observe(pool, *args, **kwargs):
            original(pool, *args, **kwargs)
            self.fixture.observe_pool(pool, "kill", refs, destroyed)

        def reject(*args, **kwargs):
            raise error

        with patch.object(kill_audio.ReliableAudioPool, "__init__", observe), \
             patch.object(target, name, reject), \
             patch.object(fixtures.assembly, "UiAudioManager",
                          side_effect=AssertionError("second factory must not run")) as second:
            with self.assertRaises(OSError) as caught:
                fixtures.assembly.create_feedback_assets(self.fixture.paths)
            second.assert_not_called()
        self.assertIs(caught.exception, error)
        self.assertEqual(len(refs), 1)
        self.fixture.assert_native_pools_retired(refs, destroyed, ["kill"])

    def test_sound_pool_decoder_resolution_failure_retires_registered_qobject(self):
        self.assert_prefix_retires(kill_audio, "resolve_kill_decoder")

    def test_later_kill_renderer_failure_retires_completed_sound_pool(self):
        self.assert_prefix_retires(kill_fx, "Cs2KillFeedback")


if __name__ == "__main__":
    unittest.main(verbosity=2)
