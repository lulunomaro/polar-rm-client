"""Local audio output preferences. No paths/configuration in the CS2 install."""
from dataclasses import asdict, dataclass, field
from functools import lru_cache
import json
import weakref

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaDevices
from .runtime_paths import RuntimePaths
from .configuration.files import FileSnapshot, atomic_write_bytes

POOLS = weakref.WeakSet()


@dataclass(frozen=True)
class AudioPreferences:
    device_id: str = "default"
    master_volume: int = 100
    mute_unfocused: bool = False


def validate_audio(data):
    if not isinstance(data, dict) or set(data) - set(asdict(AudioPreferences())):
        raise ValueError("未知音频设置")
    values = {**asdict(AudioPreferences()), **data}
    device = values["device_id"]
    if not isinstance(device, str) or len(device) > 512 or (device != "default" and
            (len(device) % 2 or not device or any(c not in "0123456789abcdef" for c in device))):
        raise ValueError("无效音频设备ID")
    if type(values["master_volume"]) is not int or not 0 <= values["master_volume"] <= 100:
        raise ValueError("无效主音量")
    if type(values["mute_unfocused"]) is not bool:
        raise ValueError("无效后台静音选项")
    return AudioPreferences(**values)


def audio_path():
    return RuntimePaths.discover().user_data_root / "audio_preferences.json"


@lru_cache(maxsize=1)
def current_audio():
    try:
        path = audio_path()
        if path.is_file() and path.stat().st_size <= 4096:
            return validate_audio(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        pass
    return AudioPreferences()


@lru_cache(maxsize=1)
def selected_output():
    device_id = current_audio().device_id
    return next((d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == device_id),
                QMediaDevices.defaultAudioOutput())


def cue_volume(volume):
    prefs = current_audio()
    if prefs.mute_unfocused and QGuiApplication.instance() is not None and QGuiApplication.applicationState() != Qt.ApplicationState.ApplicationActive:
        return 0.0
    return max(0.0, min(100.0, float(volume))) * prefs.master_volume / 100.0


class AudioPreferenceRefreshError(ValueError):
    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__("Audio preference refresh failed: " + "; ".join(str(error) for error in self.errors))


def _refresh_audio_pools():
    current_audio.cache_clear()
    selected_output.cache_clear()
    errors = []
    for pool in list(POOLS):
        try:
            pool.apply_audio_preferences()
        except Exception as error:
            errors.append(error)
    if errors:
        raise AudioPreferenceRefreshError(errors)


@dataclass
class AudioPreferenceChange:
    """Single-writer disk/cache/pool recovery after ordinary exceptions.

    Once a write is attempted, rollback owns the captured prior bytes until
    both file and pool restoration succeed. This is not crash/multiprocess
    atomicity and does not arbitrate concurrent admitted preference writers.
    An unattempted participant never restores over another writer's changes.
    """
    preferences: AudioPreferences
    previous: FileSnapshot
    _restore_required: bool = field(default=False, init=False)

    def commit(self):
        payload = json.dumps(asdict(self.preferences), ensure_ascii=False, indent=2).encode("utf-8")
        # Admission owns recovery before a fallible writer can publish bytes.
        # A raised write/cleanup call does not prove replacement was rejected.
        self._restore_required = True
        atomic_write_bytes(self.previous.path, payload)
        _refresh_audio_pools()

    def rollback(self):
        if not self._restore_required:
            return
        errors = []
        for restore in (self.previous.restore, _refresh_audio_pools):
            try:
                restore()
            except Exception as error:
                errors.append(error)
        if errors:
            raise AudioPreferenceRefreshError(errors)
        self._restore_required = False


def prepare_audio_change(prefs):
    """Validate and snapshot without changing the file, cache, or devices."""
    prefs = validate_audio(asdict(prefs))
    return AudioPreferenceChange(prefs, FileSnapshot.capture(audio_path()))


def save_audio(prefs):
    """Standalone compatibility entry point with the same rollback contract."""
    change = prepare_audio_change(prefs)
    try:
        change.commit()
    except Exception as cause:
        try:
            change.rollback()
        except Exception as restore_error:
            raise ValueError(f"Audio preference update failed: {cause}; rollback failed: {restore_error}") from cause
        raise
