"""Offline audio-pool regressions; test doubles do not prove audible output.

Run: python tests/test_audio_replay_regressions.py
The production class is loaded from its AST, as in test_ui_state_regressions,
so these checks run without Qt, an audio device, or third-party packages.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
import logging
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.combat_events import CombatEvent


class FakeObject:
    def __init__(self, *_args):
        pass


class FakeUrl:
    @staticmethod
    def fromLocalFile(path):
        return str(path)


class FakeOutput(FakeObject):
    def setDevice(self, device):
        self.device = device

    def setVolume(self, volume):
        self.volume = volume


class FakeSignal:
    def connect(self, callback):
        self.callback = callback


class FakeDevices(FakeObject):
    def __init__(self, *_args):
        self.audioOutputsChanged = FakeSignal()


class FakePlayer(FakeObject):
    defer_start = False

    class PlaybackState:
        StoppedState = 0
        PlayingState = 1

    def __init__(self, *args):
        super().__init__(*args)
        self.calls = []
        self._source = None
        self._state = self.PlaybackState.StoppedState
        self.position = 0
        self.starts = []
        self.errorOccurred = FakeSignal()

    def setAudioOutput(self, output):
        self.output = output

    def source(self):
        return self._source

    def setSource(self, source):
        self.calls.append(("source", source))
        # Qt setSource stops even when the URL matches; an equal URL does
        # not reload the media. stop itself already resets the position.
        self.stop()
        self._source = source

    def playbackState(self):
        return self._state

    def stop(self):
        self.calls.append(("stop",))
        self._state = self.PlaybackState.StoppedState
        self.position = 0

    def setPosition(self, position):
        self.calls.append(("position", position))
        self.position = position

    def play(self):
        self.calls.append(("play",))
        if not self.defer_start:
            self._state = self.PlaybackState.PlayingState
        self.starts.append((self._source, self.position, self.output.volume))

    def finish(self):
        self._state = self.PlaybackState.StoppedState
        self.position = 1000


def load_pool():
    path = ROOT / "rm_client/ui_audio.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "ReliableAudioPool")
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        cls,
    ], type_ignores=[])
    namespace = dict(QObject=FakeObject, QUrl=FakeUrl, QMediaPlayer=FakePlayer,
                     QAudioOutput=FakeOutput, Path=Path, defaultdict=defaultdict,
                     QMediaDevices=FakeDevices, POOLS=set(), selected_output=lambda: None,
                     QGuiApplication=SimpleNamespace(instance=lambda: None), logging=logging,
                     cue_volume=lambda value: max(0.0, min(100.0, float(value))))
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["ReliableAudioPool"]


Pool = load_pool()


def load_renderer():
    path = ROOT / "rm_client/kill_fx.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = {"TriggerContext", "ValorantStyle", "LockedTargetTracker", "KillFxRenderer"}
    constants = {"KILL_TYPES", "TYPE_TEXTURE", "LOCKED_TARGET_TAG", "LOCK_TARGET_WINDOW_S",
                 "VALORANT_STYLES", "PRESET_ELEMENTS"}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in classes:
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_valorant_styles":
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants
                                                 for target in node.targets):
            selected.append(node)
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *selected,
    ], type_ignores=[])
    namespace = dict(dataclass=dataclass, time=time)
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace


FX = load_renderer()


class KillCueSelectionTests(unittest.TestCase):
    def renderer(self, *, native_available=False):
        renderer = object.__new__(FX["KillFxRenderer"])
        renderer.locked_targets = FX["LockedTargetTracker"]()
        renderer.combo_count = 0
        renderer.last_kill_at = 0
        renderer.last_context = None
        renderer.calls = []
        renderer.native_calls = []
        renderer.sounds = SimpleNamespace(play=lambda name, volume: renderer.calls.append((name, volume)))
        for name in ("cs2", "honor", "scrolling", "combo", "card", "card_bar", "battlefield",
                     "valorant", "companion"):
            setattr(renderer, name, SimpleNamespace(trigger=lambda *_: None, clear=lambda: None))
        def native_play(name, volume):
            renderer.native_calls.append((name, volume))
            return native_available
        renderer.cs2.assets = SimpleNamespace(play=native_play)
        return renderer

    def trigger(self, renderer, now, *, preset="Default Scrolling", victim=101,
                sound_enabled=True, toggles=None, kind="elimination", tags=()):
        renderer.trigger(CombatEvent(kind, "preview", True, now, victim_id=victim, tags=tags),
                         combo_window=5, sound_enabled=sound_enabled, volume=55,
                         preset=preset, visibility="Preset", now_override=now,
                         element_toggles=toggles)

    def test_repeated_same_victim_retains_selected_kill_cue_for_every_preset(self):
        presets = [*FX["PRESET_ELEMENTS"], *("Valorant " + name for name in FX["VALORANT_STYLES"])]
        for preset in presets:
            with self.subTest(preset=preset):
                renderer = self.renderer()
                for index in range(12):
                    before = len(renderer.calls)
                    self.trigger(renderer, 100 + index, preset=preset)
                    expected = renderer._sound_name("NORMAL", index + 1, preset)
                    self.assertEqual(renderer.calls[before], (expected, 55))
                    if index:
                        self.assertIn("locked_target", renderer.last_context.tags)
                        self.assertEqual(renderer.calls[before + 1:], [("addscore_df.ogg", 55)])
                    else:
                        self.assertEqual(len(renderer.calls), 1)

    def test_disabling_bonus_keeps_normal_sound_on_every_kill(self):
        renderer = self.renderer()
        for now in (100, 101, 102):
            self.trigger(renderer, now, toggles={"locked_target_bonus": False})
        self.assertEqual(renderer.calls, [("killsound_df.ogg", 55)] * 3)
        self.assertNotIn("locked_target", renderer.last_context.tags)

    def test_different_victims_and_expired_repeat_have_no_bonus(self):
        renderer = self.renderer()
        self.trigger(renderer, 100, victim=101)
        self.trigger(renderer, 101, victim=102)
        self.trigger(renderer, 108, victim=101)
        self.assertEqual(renderer.calls, [("killsound_df.ogg", 55)] * 3)

    def test_explicit_bonus_tag_keeps_kill_cue_even_if_bonus_file_is_missing(self):
        renderer = self.renderer()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "killsound_df.ogg").touch()
            renderer.sounds = Pool(root)
            self.trigger(renderer, 100, tags=("LOCKED_TARGET",))
            self.assertEqual(len(renderer.sounds._fallback), 1)
            player, _ = renderer.sounds._fallback[0]
            self.assertTrue(player.starts[0][0].endswith("killsound_df.ogg"))
            self.assertIn("addscore_df.ogg", renderer.sounds.last_error)

    def test_sound_disabled_and_death_stay_silent(self):
        renderer = self.renderer()
        self.trigger(renderer, 100, sound_enabled=False)
        self.trigger(renderer, 101, sound_enabled=False)
        self.trigger(renderer, 102, kind="death")
        self.assertEqual(renderer.calls, [])

    def test_cs2_native_success_does_not_add_a_fallback_cue(self):
        renderer = self.renderer(native_available=True)
        self.trigger(renderer, 100, preset="CS2 Native")
        self.trigger(renderer, 101, preset="CS2 Native")
        self.assertEqual(renderer.calls, [])
        self.assertEqual(renderer.native_calls, [("kill", .55)] * 2)


def load_method(relative, name, namespace):
    path = ROOT / relative
    tree = ast.parse(path.read_text(encoding="utf-8"))
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        method,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


class PreviewAudioTests(unittest.TestCase):
    """Run the actual preview -> acceptance -> renderer chain with a fake clock."""

    def preview(self, sequence, starts, preset):
        now = [100.0]
        scheduled = []
        timer = SimpleNamespace(singleShot=lambda ms, callback: scheduled.append((now[0] + ms / 1000, callback)))
        namespace = dict(CombatEvent=CombatEvent, time=time, QTimer=timer)
        preview = load_method("rm_client/ui/polar/mixins/config_actions_mixin.py", "preview_elimination_fx", namespace)
        accept = load_method("rm_client/ui/polar/mixins/video_backend_mixin.py", "accept_combat_event", namespace)
        renderer = KillCueSelectionTests().renderer()
        host = SimpleNamespace(
            registry=SimpleNamespace(get=lambda name: SimpleNamespace(enabled=True) if name == "EliminationFX" else None),
            setting_mode=lambda module, key, default: {"Preview Sequence": sequence, "Preset": preset}.get(key, default),
            setting_bool=lambda module, key, default: default,
            get_setting_number=lambda module, key, default: default,
            elimination_element_toggles=lambda effect: {},
            state=SimpleNamespace(visual_kill_count=0, referee_kill_count=0, kill_streak=0, push_event=lambda _: None),
            kill_fx=renderer,
        )
        host.accept_combat_event = lambda event: accept(host, event)
        results = []
        with patch("time.perf_counter", side_effect=lambda: now[0]):
            for start in starts:
                now[0] = start
                before = len(renderer.calls)
                preview(host, single=sequence == "Single")
                while scheduled:
                    when, callback = scheduled.pop(0)
                    now[0] = when
                    callback()
                results.append([name for name, volume in renderer.calls[before:]])
        return results

    def test_test_elimination_button_keeps_cue_on_repeated_clicks(self):
        self.assertEqual(self.preview("Single", [100, 101, 102, 108], "Default Scrolling"), [
            ["killsound_df.ogg"],
            ["killsound_df.ogg", "addscore_df.ogg"],
            ["killsound_df.ogg", "addscore_df.ogg"],
            ["killsound_df.ogg"],
        ])

    def test_repeated_six_kill_preview_keeps_combo_progression(self):
        expected = [f"combokillsound_{index}_cf.ogg" for index in range(1, 7)]
        results = self.preview("Normal 1-6", [100, 104, 110], "Combo")
        self.assertEqual(results[0], expected)
        self.assertEqual(results[1], [cue for primary in expected for cue in (primary, "addscore_df.ogg")])
        self.assertEqual(results[2], expected)


class AudioReplayTests(unittest.TestCase):
    def setUp(self):
        FakePlayer.defer_start = False
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("kill.ogg", "hit.ogg", "combo.ogg"):
            (self.root / name).touch()

    def test_new_voice_is_used_without_stopping_first_kill(self):
        pool = Pool(self.root, fallback_voices=8)
        for _ in range(8):
            self.assertTrue(pool.play("kill.ogg", 55))
        self.assertEqual(len(pool._fallback), 8)
        self.assertEqual([len(player.starts) for player, _ in pool._fallback], [1] * 8)

    def test_hit_does_not_steal_first_kill_while_pool_has_capacity(self):
        pool = Pool(self.root, fallback_voices=8)
        pool.play("kill.ogg", 55)
        first, _ = pool._fallback[0]
        before = list(first.calls)
        pool.play("hit.ogg", 35)
        self.assertEqual(first.calls, before)
        self.assertTrue(first.source().endswith("kill.ogg"))

    def test_loading_voice_is_not_stolen_while_pool_has_capacity(self):
        # Qt can remain StoppedState while LoadingMedia even after play().
        # A new cue must not mistake that pending first start for an idle voice.
        FakePlayer.defer_start = True
        pool = Pool(self.root, fallback_voices=8)
        pool.play("kill.ogg", 55)
        first, _ = pool._fallback[0]
        self.assertEqual(first.playbackState(), FakePlayer.PlaybackState.StoppedState)
        before = list(first.calls)
        pool.play("hit.ogg", 35)
        self.assertEqual(first.calls, before)
        self.assertTrue(pool._fallback[1][0].source().endswith("hit.ogg"))

    def test_sequential_finished_same_source_replays_from_zero(self):
        pool = Pool(self.root, fallback_voices=1)
        for _ in range(20):
            self.assertTrue(pool.play("kill.ogg", 55))
            player, _ = pool._fallback[0]
            self.assertEqual(player.starts[-1][1:], (0, 0.55))
            player.finish()
        self.assertEqual(len(player.starts), 20)
        # Same-source replay should not invoke setSource/decoder setup again.
        self.assertEqual(sum(call[0] == "source" for call in player.calls), 1)

    def test_full_busy_pool_remains_bounded_and_rotates(self):
        pool = Pool(self.root, fallback_voices=2)
        for _ in range(8):
            pool.play("kill.ogg", 55)
        self.assertEqual(len(pool._fallback), 2)
        self.assertEqual([len(player.starts) for player, _ in pool._fallback], [4, 4])

    def test_preloaded_same_source_replays_after_finish(self):
        pool = Pool(self.root)
        pool.preload_media(("kill.ogg",))
        player, _ = pool._media_effects["kill.ogg"][0]
        for _ in range(12):
            pool.play("kill.ogg", 65)
            self.assertEqual(player.starts[-1][1:], (0, 0.65))
            player.finish()
        self.assertEqual(len(player.starts), 12)
        self.assertEqual(sum(call[0] == "source" for call in player.calls), 1)

    def test_different_sources_and_volume_are_preserved(self):
        pool = Pool(self.root, fallback_voices=1)
        for name, volume, expected in (("kill.ogg", 200, 1.0), ("hit.ogg", 10, 0.1),
                                       ("combo.ogg", 45, 0.45), ("kill.ogg", 55, 0.55)):
            pool.play(name, volume)
            player, _ = pool._fallback[0]
            self.assertEqual(player.starts[-1], (str((self.root / name).resolve()), 0, expected))
            player.finish()

    def test_muted_cue_does_not_interrupt_or_allocate(self):
        pool = Pool(self.root, fallback_voices=2)
        pool.play("kill.ogg", 55)
        player, _ = pool._fallback[0]
        before = list(player.calls)
        self.assertTrue(pool.play("hit.ogg", 0))
        self.assertEqual(len(pool._fallback), 1)
        self.assertEqual(player.calls, before)

    def test_missing_sound_does_not_consume_a_voice(self):
        pool = Pool(self.root)
        self.assertFalse(pool.play("missing.ogg", 55))
        self.assertFalse(pool._fallback)
        self.assertIn("missing.ogg", pool.last_error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
