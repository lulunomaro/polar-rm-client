"""Exact, data-only command selection for the existing settings action IDs.

Display translations never grant command authority. Unknown or near-matching
names stay unresolved, and the UI retains ownership of extension precedence,
operator dialogs, runtime effects, and error presentation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class QualificationCommand(Enum):
    NEW_SESSION = "New Qualification Session"
    RESET_SESSION = "Reset Qualification Session"
    PREFLIGHT = "Run Preflight"
    VIDEO = "Run Video Test"
    KMC = "Run KMC Test"
    DT7_SAFETY = "Record DT7 Safety Test"
    ECONOMY_SEND = "Run Economy Send Test"
    ESTOP = "Record E-Stop Test"
    OBSERVE_ZERO = "Observe Firmware Zero"
    FIRMWARE_ZERO = "Record Firmware Zero Test"
    LOSS_RECOVERY = "Run Loss Recovery Test"
    HERO = "Record Hero Test"
    ECONOMY = "Record Economy Test"
    PRM1_VISION_SCHEMA = "Review PRM1 Vision Schema"
    PRM1_VISION_BENCH = "Run PRM1 Vision Bench"
    PRM1_SPIN_SCHEMA = "Review PRM1 Spin Schema"
    PRM1_SPIN_BENCH = "Run PRM1 Spin Bench"
    START_SOAK = "Start Match Soak"
    STOP_SOAK = "Stop Match Soak"
    EXPORT = "Export Evidence"
    SIGN_OFF = "Field Sign-off"
    ARM = "Arm Match Control"
    LOCK = "Lock Match Control"


class ConfigCommandKind(Enum):
    EDITOR = "editor"
    QUALIFICATION = "qualification"
    SAFETY = "safety"
    DIAGNOSTIC = "diagnostic"
    ANGLES = "angles"
    PROFILE = "profile"
    CALIBRATION = "calibration"
    ELIMINATION = "elimination"
    DEPLOY = "deploy"
    LOB = "lob"
    SOUND_IMPORT = "sound_import"
    SOUND_PREVIEW = "sound_preview"


@dataclass(frozen=True)
class ConfigCommand:
    kind: ConfigCommandKind
    name: str
    sound_slot: str | None = None


_GROUPS = {
    ConfigCommandKind.EDITOR: (
        "Open Keybind Manager", "Open Crosshair Editor", "Import Crosshair Code",
        "Import Crosshair File", "Crosshair Import Details",
    ),
    ConfigCommandKind.QUALIFICATION: tuple(command.value for command in QualificationCommand),
    ConfigCommandKind.SAFETY: ("Trigger E-Stop", "Release E-Stop"),
    ConfigCommandKind.DIAGNOSTIC: ("Run DT7 Link Selftest", "Rescan Serial Ports", "Run Wireless KM Selftest"),
    ConfigCommandKind.ANGLES: ("Arm Angle Forward", "Disarm Angle Forward", "Show Angle Status"),
    ConfigCommandKind.PROFILE: (
        "Save Current", "Reload Profile", "Import Config", "Import Wallpaper", "Reset Wallpaper", "Restore Compact HUD",
    ),
    ConfigCommandKind.CALIBRATION: ("Capture Chessboard", "Solve Calibration", "Export Tongji YAML"),
    ConfigCommandKind.ELIMINATION: ("Preview FX", "Test Elimination"),
    ConfigCommandKind.DEPLOY: ("Open Deploy Interface",),
    ConfigCommandKind.LOB: (
        "Calibrate Lob Yaw", "Calibrate Lob Pitch", "Confirm Relocalization", "Invalidate Relocalization",
        "Preview Lob", "Arm Assisted Lob", "Disarm Assisted Lob",
    ),
}
_COMMANDS = {name: ConfigCommand(kind, name) for kind, names in _GROUPS.items() for name in names}
for _slot in ("enable", "disable", "invalid"):
    for _kind, _name in ((ConfigCommandKind.SOUND_IMPORT, f"Import {_slot.title()} Sound"),
                         (ConfigCommandKind.SOUND_PREVIEW, f"Preview {_slot.title()}")):
        _COMMANDS[_name] = ConfigCommand(_kind, _name, _slot)
del _slot, _kind, _name


def resolve_config_command(name: str) -> ConfigCommand | None:
    return _COMMANDS.get(name)


def resolve_qualification_command(name: str) -> QualificationCommand | None:
    try:
        return QualificationCommand(name)
    except ValueError:
        return None
