from __future__ import annotations

from collections import defaultdict
import logging
import os
from pathlib import Path
import shutil
import weakref
from typing import Iterable

from .runtime_paths import RuntimePaths

from shiboken6 import isValid
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect, QMediaDevices
from .audio_preferences import POOLS, selected_output, cue_volume


def _effect_status_listener(pool, effect, name):
    """A native signal must not become a strong owner of its parent pool."""
    pool_ref, effect_ref = weakref.ref(pool), weakref.ref(effect)

    def changed():
        owner, voice = pool_ref(), effect_ref()
        if owner is not None and voice is not None and isValid(owner) and isValid(voice):
            owner._on_status(name, voice, voice.status())

    return changed


def _media_error_listener(pool, player):
    pool_ref, player_ref = weakref.ref(pool), weakref.ref(player)

    def failed(error, message):
        owner, voice = pool_ref(), player_ref()
        if owner is not None and voice is not None and isValid(owner) and isValid(voice):
            owner._on_media_error(voice, message)

    return failed


class ReliableAudioPool(QObject):
    """预加载短音效，并在 Qt 短音效后端失败时回退到媒体播放器。"""

    def __init__(self, root: Path, voices_per_sound: int = 2, fallback_voices: int = 6,
                 *, custom_root: Path | None = None) -> None:
        super().__init__()
        self.root = Path(root)
        self.custom_root = Path(custom_root) if custom_root is not None else None
        self.voices_per_sound = max(1, voices_per_sound)
        self._effects: dict[str, list[QSoundEffect]] = {}
        self._pending: dict[str, list[float]] = defaultdict(list)
        self._fallback: list[tuple[QMediaPlayer, QAudioOutput]] = []
        self._fallback_index = 0
        self._fallback_voices = max(1, fallback_voices)
        self._media_effects: dict[str, list[tuple[QMediaPlayer, QAudioOutput]]] = {}
        self._media_indices: dict[str, int] = defaultdict(int)
        self.last_sound = ""
        self.last_error = ""
        self.backend = "QSoundEffect"
        POOLS.add(self)
        self._devices = QMediaDevices(self)
        self._devices.audioOutputsChanged.connect(self.apply_audio_preferences)
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_focus_changed)

    def stop(self) -> None:
        """Cancel queued cues and stop every playback path without unloading."""
        self._pending.clear()
        for voices in self._effects.values():
            for voice in voices:
                voice.stop()
        for player, _output in [*self._fallback, *(v for voices in self._media_effects.values() for v in voices)]:
            player.stop()

    def _on_focus_changed(self, _state) -> None:
        if cue_volume(100) <= 0:
            self.stop()

    def apply_audio_preferences(self) -> None:
        selected_output.cache_clear()
        device = selected_output()
        self.stop()
        for voices in self._effects.values():
            for voice in voices:
                voice.setAudioDevice(device)
        for _player, output in [*self._fallback, *(v for voices in self._media_effects.values() for v in voices)]:
            output.setDevice(device)

    def _sound_path(self, name: str) -> Path:
        # Custom selections keep their portable "custom/<filename>" config key.
        # Resolve them outside the immutable, manifest-hashed resource tree.
        if name.startswith("custom/") and self.custom_root is not None:
            filename = name[len("custom/"):]
            if not filename or "/" in filename or "\\" in filename or ":" in filename or filename in {".", ".."}:
                raise ValueError("Invalid custom sound name")
            return self.custom_root / filename
        return self.root / name

    def preload(self, names: Iterable[str]) -> None:
        for name in dict.fromkeys(str(item) for item in names):
            path = self._sound_path(name)
            # Windows 的 QSoundEffect 后端对 OGG/Vorbis 支持不稳定；压缩音频统一交给 Qt FFmpeg 媒体后端。
            if not path.is_file() or path.suffix.lower() != ".wav" or name in self._effects:
                continue
            voices: list[QSoundEffect] = []
            for _ in range(self.voices_per_sound):
                effect = QSoundEffect(self)
                effect.setAudioDevice(selected_output())
                effect.statusChanged.connect(
                    _effect_status_listener(self, effect, name)
                )
                effect.setSource(QUrl.fromLocalFile(str(path.resolve())))
                voices.append(effect)
            self._effects[name] = voices

    def preload_media(self, names: Iterable[str], voices_per_sound: int = 1) -> None:
        """Prime compressed short cues through Qt FFmpeg before first input."""

        voice_count = max(1, int(voices_per_sound))
        for name in dict.fromkeys(str(item) for item in names):
            path = self._sound_path(name)
            if not path.is_file() or path.suffix.lower() == ".wav" or name in self._media_effects:
                continue
            voices: list[tuple[QMediaPlayer, QAudioOutput]] = []
            source = QUrl.fromLocalFile(str(path.resolve()))
            for _ in range(voice_count):
                output = QAudioOutput(self)
                output.setDevice(selected_output())
                player = QMediaPlayer(self)
                player.setAudioOutput(output)
                player.errorOccurred.connect(_media_error_listener(self, player))
                player.setSource(source)
                voices.append((player, output))
            self._media_effects[name] = voices

    def play(self, name: str, volume: float) -> bool:
        volume = cue_volume(volume)
        if volume <= 0:
            return True
        path = self._sound_path(name)
        self.last_sound = name
        if not path.is_file():
            self.last_error = f"音效文件不存在: {path}"
            return False
        if path.suffix.lower() != ".wav":
            # OGG/MP3 等压缩音频不再先创建 QSoundEffect，避免启动时刷解码错误。
            return self._play_fallback(path, volume, "")
        self.preload((name,))
        voices = self._effects.get(name, [])
        ready = [voice for voice in voices if voice.status() == QSoundEffect.Status.Ready]
        if ready:
            voice = next((item for item in ready if not item.isPlaying()), ready[0])
            voice.stop()
            voice.setVolume(max(0.0, min(1.0, volume / 100.0)))
            voice.play()
            self.backend = "QSoundEffect"
            self.last_error = ""
            return True
        if any(voice.status() == QSoundEffect.Status.Loading for voice in voices):
            self._pending[name].append(volume)
            # Each voice emits Ready only once. Keep at most that many cold
            # requests so stale cues cannot remain queued indefinitely.
            del self._pending[name][:-len(voices)]
            return True
        return self._play_fallback(path, volume, "QSoundEffect 无法解码该音效")

    def _on_status(self, name: str, voice: QSoundEffect, status: QSoundEffect.Status) -> None:
        if status == QSoundEffect.Status.Ready and self._pending.get(name):
            volume = self._pending[name].pop(0)
            voice.setVolume(max(0.0, min(1.0, volume / 100.0)))
            voice.play()
            self.backend = "QSoundEffect"
            self.last_error = ""
        elif status == QSoundEffect.Status.Error:
            pending = self._pending.pop(name, [])
            for volume in pending:
                self._play_fallback(self._sound_path(name), volume, "QSoundEffect 加载失败")

    def _on_media_error(self, player: QMediaPlayer, message: str) -> None:
        self.last_error = f"音效播放失败: {player.source().toLocalFile()}: {message}"
        logging.getLogger(__name__).warning(self.last_error)

    def _play_fallback(self, path: Path, volume: float, reason: str) -> bool:
        try:
            relative_name = path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            relative_name = ""
        preloaded = self._media_effects.get(relative_name, [])
        if preloaded:
            index = self._media_indices[relative_name] % len(preloaded)
            self._media_indices[relative_name] = (index + 1) % len(preloaded)
            player, output = preloaded[index]
            player.stop()
            output.setVolume(max(0.0, min(1.0, volume / 100.0)))
            player.play()
            self.backend = "QMediaPlayer"
            self.last_error = reason
            return True
        if len(self._fallback) < self._fallback_voices:
            output = QAudioOutput(self)
            output.setDevice(selected_output())
            player = QMediaPlayer(self)
            player.setAudioOutput(output)
            player.errorOccurred.connect(_media_error_listener(self, player))
            self._fallback.append((player, output))
            # Use the voice we just created. Taking the old cursor modulo a
            # growing pool selects voice 0 twice, interrupting the first kill
            # (or its pending decoder load) while the new voice stays unused.
            index = len(self._fallback) - 1
        else:
            index = self._fallback_index % len(self._fallback)
        player, output = self._fallback[index]
        self._fallback_index = (index + 1) % len(self._fallback)
        player.stop()
        output.setVolume(max(0.0, min(1.0, volume / 100.0)))
        source = QUrl.fromLocalFile(str(path.resolve()))
        if player.source() != source:
            player.setSource(source)
        else:
            player.setPosition(0)
        self.backend = "QMediaPlayer"
        self.last_error = reason
        player.play()
        return True


class UiAudioManager:
    USER_SOURCES = frozenset({"clickgui", "keybind"})
    CUSTOM_THEME = "Custom"
    NO_SOUND = "No Sound"
    AUDIO_EXTENSIONS = frozenset({".wav", ".ogg", ".mp3", ".flac", ".m4a", ".aac"})
    CUSTOM_KINDS = frozenset({"enable", "disable", "invalid"})
    # Original, unmodified short cues from Sigma Jello 5.1.0.  The ClickGUI
    # only invokes activate/deactivate for module state changes; the complete
    # small UI bank is kept available without importing music or minigames.
    JELLO_SOUNDS = (
        "jello/activate.mp3",
        "jello/click.mp3",
        "jello/clicksound.mp3",
        "jello/connect.mp3",
        "jello/deactivate.mp3",
        "jello/error.mp3",
        "jello/pop.mp3",
        "jello/switch.mp3",
    )
    THEMES = {
        "Custom": {},
        "Polar Classic": {
            "enable": "polar_classic/module_on.wav",
            "disable": "polar_classic/module_off.wav",
            "invalid": "polar_classic/module_off.wav",
        },
        "LiquidBounce": {
            "enable": "liquidbounce/click-2.ogg",
            "disable": "liquidbounce/click-1.ogg",
            "invalid": "liquidbounce/click-3.ogg",
        },
        "Polar UI": {
            "enable": "polar_ui/module_toggle.wav",
            "disable": "polar_ui/module_toggle.wav",
            "invalid": "polar_ui/action_success.wav",
        },
    }

    def __init__(self, root: Path, *, custom_root: Path | None = None) -> None:
        self.root = Path(root)
        self.custom_dir = (Path(custom_root) if custom_root is not None else
                           RuntimePaths.discover().user_data_root / "ui_sounds" / "custom")
        self.custom_dir.mkdir(parents=True, exist_ok=True)
        # Preserve older installations' selections without modifying their
        # resources or overwriting newer user-data copies.
        legacy_custom = self.root / "custom"
        self.migration_errors: list[str] = []
        try:
            legacy_files = (list(legacy_custom.iterdir())
                            if legacy_custom.is_dir() and legacy_custom.resolve() != self.custom_dir.resolve()
                            else [])
        except OSError as exc:
            legacy_files = []
            self.migration_errors.append(f"Cannot read legacy custom sounds: {exc}")
        for legacy in legacy_files:
            target = self.custom_dir / legacy.name
            try:
                if legacy.is_file() and legacy.suffix.lower() in self.AUDIO_EXTENSIONS and not target.exists():
                    shutil.copy2(legacy, target)
            except OSError as exc:
                self.migration_errors.append(f"Cannot migrate custom sound {legacy.name}: {exc}")
        for message in self.migration_errors:
            logging.getLogger("rm_client.ui_audio").warning(message)
        self.pool = ReliableAudioPool(self.root, voices_per_sound=2, fallback_voices=4,
                                      custom_root=self.custom_dir)
        self.pool.preload(name for theme in self.THEMES.values() for name in theme.values())
        # These are the two exact cues invoked by Sigma's ModListView.  Set
        # their media sources now so the first module click does not pay MP3
        # probing/decoder startup on the UI thread.
        # QtMultimedia's FFmpeg backend probes compressed media on worker
        # threads.  Creating those workers in an offscreen regression process
        # is both unnecessary (the tests replace the pool before playback) and
        # unsafe on Windows: tearing a QMediaPlayer down while OpenCV is also
        # decoding the wallpaper has intermittently caused an access violation.
        # Production keeps the eager preload so Sigma's first toggle remains
        # latency-free; deterministic/headless runs opt out explicitly.
        if not _media_preload_disabled():
            self.pool.preload_media(("jello/activate.mp3", "jello/deactivate.mp3"))

    def custom_sound_names(self) -> list[str]:
        sounds = [
            "custom/" + path.name
            for path in self.custom_dir.iterdir()
            if path.is_file() and path.suffix.lower() in self.AUDIO_EXTENSIONS
        ]
        return sorted(sounds, key=str.casefold)

    def import_custom_sound(self, source: Path, kind: str) -> str:
        source = Path(source)
        if kind not in self.CUSTOM_KINDS:
            raise ValueError("Unsupported sound slot")
        if not source.is_file() or source.suffix.lower() not in self.AUDIO_EXTENSIONS:
            raise ValueError("Unsupported audio file")
        safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in source.stem).strip("_") or kind
        target = self.custom_dir / f"{kind}-{safe_stem}{source.suffix.lower()}"
        index = 2
        while target.exists() and target.resolve() != source.resolve():
            target = self.custom_dir / f"{kind}-{safe_stem}-{index}{source.suffix.lower()}"
            index += 1
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        relative = "custom/" + target.name
        self.pool.preload((relative,))
        return relative

    @classmethod
    def sound_name(cls, theme: str, kind: str, custom_sounds: dict[str, str] | None = None) -> str | None:
        if theme == cls.CUSTOM_THEME:
            selected = str((custom_sounds or {}).get(kind, cls.NO_SOUND))
            if selected == cls.NO_SOUND or not selected.startswith("custom/"):
                return None
            filename = selected[len("custom/"):]
            return selected if (filename and filename not in {".", ".."}
                                and "/" not in filename and "\\" not in filename and ":" not in filename) else None
        selected = cls.THEMES.get(theme, cls.THEMES["Polar UI"])
        return selected.get(kind, selected["invalid"])

    def module_toggled(
        self,
        enabled: bool,
        source: str,
        *,
        active: bool,
        volume: float,
        theme: str = "Custom",
        custom_sounds: dict[str, str] | None = None,
    ) -> None:
        if active and source in self.USER_SOURCES:
            sound = self.sound_name(theme, "enable" if enabled else "disable", custom_sounds)
            if sound is not None:
                self.pool.play(sound, volume)

    def jello_module_toggled(self, enabled: bool, *, active: bool, volume: float) -> None:
        """Play Sigma's exact module on/off cue, independent of Polar themes."""
        if active:
            self.pool.play(
                "jello/activate.mp3" if enabled else "jello/deactivate.mp3",
                volume,
            )

    def preview(
        self,
        kind: str,
        volume: float,
        theme: str = "Custom",
        custom_sounds: dict[str, str] | None = None,
    ) -> None:
        adjusted = volume * 0.60 if kind == "invalid" and theme == "Polar Classic" else volume
        sound = self.sound_name(theme, kind, custom_sounds)
        if sound is not None:
            self.pool.play(sound, adjusted)

    def gui_feedback(self, kind: str, volume: float, theme: str) -> None:
        """Play a transition cue without making config hydration noisy."""
        if theme != "Polar UI":
            return
        sound = {
            "expand": "polar_ui/panel_transition.wav",
            "collapse": "polar_ui/panel_transition.wav",
            "success": "polar_ui/action_success.wav",
        }.get(kind)
        if sound:
            self.pool.play(sound, volume)


def _media_preload_disabled() -> bool:
    return os.environ.get("POLAR_DISABLE_MEDIA_PRELOAD", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

