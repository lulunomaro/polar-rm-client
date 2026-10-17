"""Read-only verification provenance without importing optional dependencies.

Only interpreter/OS facts, installed distribution versions and Git object IDs
are reported. Environment variables, executable paths, remote URLs, dirty file
names and Git error output are deliberately omitted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
import platform
import re
import subprocess

DEPENDENCIES = (
    "PySide6", "PySide6-Addons", "PySide6-Essentials", "shiboken6",
    "opencv-python", "numpy", "paho-mqtt", "pyserial", "openvino", "openvino-telemetry",
    "PyInstaller", "ziglang",
)


def distribution_versions() -> dict:
    versions = {}
    for name in DEPENDENCIES:
        try:
            version = metadata.version(name)
            if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9.!+_-]+", version):
                raise ValueError("Invalid distribution version metadata")
        except metadata.PackageNotFoundError:
            versions[name] = {"status": "not-installed", "version": None}
        except Exception as exc:
            versions[name] = {"status": "unavailable", "version": None,
                              "error_type": type(exc).__name__}
        else:
            versions[name] = {"status": "installed", "version": version}
    return versions


def source_identity(root: Path) -> dict:
    identity = {"status": "unavailable", "commit": None, "tree": None,
                "dirty": None, "dirty_scope": "tracked-and-untracked-nonignored",
                "reason": None}
    queries = (("commit", ("rev-parse", "--verify", "HEAD")),
               ("tree", ("rev-parse", "--verify")),
               ("dirty", ("status", "--porcelain=v1", "--untracked-files=normal")))
    for field, arguments in queries:
        if field == "tree":
            # HEAD can move between read-only commands. Bind the tree to the
            # captured commit rather than pairing that commit with a new HEAD.
            arguments = (*arguments, f"{identity['commit']}^{{tree}}")
        try:
            result = subprocess.run(
                ["git", "--no-optional-locks", "-C", str(root), *arguments],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            identity["reason"] = {"query": field, "error_type": type(exc).__name__}
            return identity
        if result.returncode:
            identity["reason"] = {"query": field, "returncode": result.returncode}
            return identity
        output = result.stdout.strip()
        if field == "dirty":
            identity[field] = bool(output)
        elif re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", output):
            identity[field] = output
        else:
            identity["reason"] = {"query": field, "error_type": "InvalidGitObjectId"}
            return identity
    identity["status"] = "available"
    return identity


def collect_provenance(root: Path) -> dict:
    """Capture start-of-run facts; missing Git or packages do not claim success."""
    return {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "interpreter": {"implementation": platform.python_implementation(),
                        "version": platform.python_version()},
        "os": {"system": platform.system(), "release": platform.release(),
               "machine": platform.machine()},
        "dependencies": distribution_versions(),
        "source_at_start": source_identity(root),
    }
