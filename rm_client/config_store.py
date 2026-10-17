from __future__ import annotations

import json
from copy import deepcopy
import os
import tempfile
from pathlib import Path
from typing import Any

from .configuration.files import FileSnapshot, FileWriteError
from .configuration.validation import CONFIG_VERSION
from .configuration.registry import apply_profile_modules, prepare_registry_modules
from .configuration.wheel import mirror_wheel_payload, prepare_wheel_profile

DEFAULT_PROFILE = "default"


def validate_client_config(data: Any, registry: Any = None) -> None:
    """Validate complete original and migrated values before mutation."""
    modules = prepare_registry_modules(registry, data)
    if registry is not None:
        prepare_wheel_profile(registry, data, modules or {})


def _atomic_write(path: Path, serialized: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        try:
            handle = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        except Exception as cause:
            # Until fdopen succeeds, the raw descriptor still belongs to us.
            # Once adopted, only the stream context manager may close it.
            try:
                os.close(fd)
            except Exception as cleanup_error:
                raise FileWriteError(cause, cleanup_error) from cause
            raise
        with handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception as cause:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception as cleanup_error:
            raise FileWriteError(cause, cleanup_error) from cause
        raise


class ProfileSaveError(OSError):
    """A caught file-save failure, including every failed recovery attempt."""
    def __init__(self, cause: Exception, restore_errors: tuple[tuple[Path, Exception], ...]) -> None:
        self.cause = cause
        self.restore_errors = restore_errors
        detail = f"{type(cause).__name__}: {cause}"
        for path, error in restore_errors:
            detail += f"; restore {path.name} failed ({type(error).__name__}): {error}"
        super().__init__(detail)

    @property
    def restored(self) -> bool:
        return not self.restore_errors


class ClientConfigStore:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def profile_names(self) -> list[str]:
        names = []
        for path in sorted(self.config_dir.glob("*.json")):
            if path.is_file() and not path.name.startswith("."):
                names.append(path.stem)
        return names or [DEFAULT_PROFILE]

    def profile_path(self, profile: str) -> Path:
        return self.config_dir / f"{self.clean_profile_name(profile)}.json"

    def load(self, profile: str) -> dict[str, Any] | None:
        path = self.profile_path(profile)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            validate_client_config(data)
        except (OSError, UnicodeError, ValueError, OverflowError):
            return None
        return data

    def save(self, profile: str, data: dict[str, Any]) -> Path:
        path = self.profile_path(profile)
        payload = dict(data)
        payload["version"] = CONFIG_VERSION
        payload["profile"] = self.clean_profile_name(profile)
        validate_client_config(payload)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
        # Replacement is atomic under normal OS operation, so readers do not
        # observe a partially written profile. Directory metadata is not
        # fsynced here; power-loss/crash durability is not promised.
        _atomic_write(path, serialized)
        return path

    def save_with_startup(self, profile: str, data: dict[str, Any], *, load_on_start: bool) -> Path:
        """Save both preferences with best-effort rollback of caught failures.

        Each file replacement is atomic, but the pair is NOT crash-atomic:
        termination or another reader may observe an intermediate pair. This
        single-writer operation restores exact prior bytes (or prior absence)
        after ordinary exceptions and reports any incomplete restoration.
        """
        if type(load_on_start) is not bool:
            raise ValueError("load_on_start must be a boolean")
        validate_client_config(data)
        snapshots = (FileSnapshot.capture(self.profile_path(profile)),
                     FileSnapshot.capture(self.config_dir / ".startup.json"))
        try:
            path = self.save(profile, data)
            self.save_startup_preferences(profile, load_on_start)
            return path
        except Exception as cause:
            failures = []
            for snapshot in reversed(snapshots):
                try:
                    snapshot.restore()
                except Exception as error:
                    failures.append((snapshot.path, error))
            raise ProfileSaveError(cause, tuple(failures)) from cause

    def load_startup_preferences(self) -> dict[str, Any] | None:
        try:
            data = json.loads((self.config_dir / ".startup.json").read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("profile"), str) or type(data.get("load_on_start")) is not bool:
                return None
            return {"profile": self.clean_profile_name(data["profile"]), "load_on_start": data["load_on_start"]}
        except (OSError, UnicodeError, ValueError):
            return None

    def save_startup_preferences(self, profile: str, load_on_start: bool) -> None:
        if type(load_on_start) is not bool:
            raise ValueError("load_on_start must be a boolean")
        _atomic_write(self.config_dir / ".startup.json", json.dumps({"profile": self.clean_profile_name(profile), "load_on_start": load_on_start}))

    def ensure_default(self, data: dict[str, Any]) -> Path:
        path = self.profile_path(DEFAULT_PROFILE)
        if not path.exists():
            self.save(DEFAULT_PROFILE, data)
        return path

    def import_file(self, source: Path, *, registry: Any = None) -> str:
        source = Path(source)
        if source.suffix.lower() != ".json" or not source.exists():
            raise ValueError("只能导入已经存在的 .json 配置文件。")
        data = json.loads(source.read_text(encoding="utf-8"))
        validate_client_config(data, registry)
        name = self.clean_profile_name(source.stem)
        target = self.profile_path(name)
        if source.resolve() != target.resolve():
            if target.exists():
                # Keep the prior bytes recoverable even when the previous
                # profile itself was corrupt. A failed backup aborts import.
                _atomic_write(target.with_suffix(".json.bak"), target.read_text(encoding="utf-8"))
            self.save(name, data)
        return name

    def clean_profile_name(self, profile: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(profile).strip())
        return safe or DEFAULT_PROFILE


def export_client_config(
    registry: Any,
    *,
    gui_mode: str,
    selected_category: str,
    selected_module: str,
    module_expanded: set[str],
    old_panels: dict[str, Any],
    new_panels: dict[str, Any],
    dropdown_panels: dict[str, Any],
    polar_windows: dict[str, Any] | None = None,
    quick_action_wheel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = {
        "version": CONFIG_VERSION,
        # Legacy ClickGUI renderers are transient previews. Never serialize
        # one as the profile's startup surface, even when an older caller
        # passes a historical mode name.
        "gui_mode": "JelloSigma",
        "startup_wallpaper_revision": 1,
        "selected_category": selected_category,
        "selected_module": selected_module,
        "module_expanded": sorted(module_expanded),
        "old_panels": old_panels,
        "new_panels": new_panels,
        "dropdown_panels": dropdown_panels,
        "polar_windows": polar_windows or {},
        "quick_action_wheel": quick_action_wheel or {},
        "modules": {
            module.name: {
                "enabled": module.enabled,
                "keybind": module.keybind,
                "settings": {
                    setting.name: setting.value
                    for setting in module.settings
                    if setting.kind != "action_group" and not (module.name == "ConfigManager" and setting.name == "Profile")
                },
            }
            for module in registry.modules
        },
    }
    return mirror_wheel_payload(deepcopy(data))


def apply_registry_config(registry: Any, data: dict[str, Any]) -> None:
    modules = prepare_registry_modules(registry, data)
    wheel = prepare_wheel_profile(registry, data, modules or {})
    if modules is not None:
        apply_profile_modules(registry, modules)
    if wheel is not None:
        wheel.write_module(registry.get("QuickActionWheel"))
