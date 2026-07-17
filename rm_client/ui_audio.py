from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect


class ReliableAudioPool(QObject):
    """预加载短音效，并在 Qt 短音效后端失败时回退到媒体播放器。"""

    def __init__(self, root: Path, voices_per_sound: int = 2, fallback_voices: int = 6) -> None:
        super().__init__()
        self.root = Path(root)
        self.voices_per_sound = max(1, voices_per_sound)
        self._effects: dict[str, list[QSoundEffect]] = {}
        self._pending: dict[str, list[float]] = defaultdict(list)
        self._fallback: list[tuple[QMediaPlayer, QAudioOutput]] = []
        self._fallback_index = 0
        self._fallback_voices = max(1, fallback_voices)
        self.last_sound = ""
        self.last_error = ""
        self.backend = "QSoundEffect"

    def preload(self, names: Iterable[str]) -> None:
        for name in dict.fromkeys(str(item) for item in names):
            path = self.root / name
            if not path.is_file() or name in self._effects:
                continue
            voices: list[QSoundEffect] = []
            for _ in range(self.voices_per_sound):
                effect = QSoundEffect(self)
                effect.statusChanged.connect(
                    lambda sound=name, voice=effect: self._on_status(sound, voice, voice.status())
                )
                effect.setSource(QUrl.fromLocalFile(str(path.resolve())))
                voices.append(effect)
            self._effects[name] = voices

    def play(self, name: str, volume: float) -> bool:
        path = self.root / name
        self.last_sound = name
        if not path.is_file():
            self.last_error = f"音效文件不存在: {path}"
            return False
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
                self._play_fallback(self.root / name, volume, "QSoundEffect 加载失败")

    def _play_fallback(self, path: Path, volume: float, reason: str) -> bool:
        if len(self._fallback) < self._fallback_voices:
            output = QAudioOutput(self)
            player = QMediaPlayer(self)
            player.setAudioOutput(output)
            self._fallback.append((player, output))
        player, output = self._fallback[self._fallback_index % len(self._fallback)]
        self._fallback_index = (self._fallback_index + 1) % len(self._fallback)
        player.stop()
        output.setVolume(max(0.0, min(1.0, volume / 100.0)))
        player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        player.play()
        self.backend = "QMediaPlayer"
        self.last_error = reason
        return True


class UiAudioManager:
    USER_SOURCES = frozenset({"clickgui", "keybind"})
    # Polar = 自研中性 UI 开关音（默认）。
    # Zen / LiquidBounce = 彩蛋/Legacy：上游并非模块开关音，见 NOTICE。
    THEMES = {
        "Polar": {
            "enable": "polar/enable.wav",
            "disable": "polar/disable.wav",
            "invalid": "polar/invalid.wav",
        },
        "Zen": {
            "enable": "openzen/truth.wav",
            "disable": "openzen/lie.wav",
            "invalid": "openzen/lie.wav",
        },
        "LiquidBounce": {
            "enable": "liquidbounce/click-2.ogg",
            "disable": "liquidbounce/click-1.ogg",
            "invalid": "liquidbounce/click-3.ogg",
        },
    }

    def __init__(self, root: Path) -> None:
        self.pool = ReliableAudioPool(root, voices_per_sound=2, fallback_voices=4)
        self.pool.preload(name for theme in self.THEMES.values() for name in theme.values())

    @classmethod
    def sound_name(cls, theme: str, kind: str) -> str:
        selected = cls.THEMES.get(theme, cls.THEMES["Polar"])
        return selected.get(kind, selected["invalid"])

    def module_toggled(self, enabled: bool, source: str, *, active: bool, volume: float, theme: str = "Polar") -> None:
        if active and source in self.USER_SOURCES:
            self.pool.play(self.sound_name(theme, "enable" if enabled else "disable"), volume)

    def preview(self, kind: str, volume: float, theme: str = "Polar") -> None:
        adjusted = volume * 0.60 if kind == "invalid" and theme in ("Zen", "Polar") else volume
        self.pool.play(self.sound_name(theme, kind), adjusted)
