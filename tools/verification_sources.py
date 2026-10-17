"""Read-only start/end fingerprints of verification code and contract inputs.

This is not an atomic filesystem snapshot, a dependency lock, or an asset
manifest. It catches persistent edits, additions, and deletions in its declared
scope without importing source modules or requiring Git history.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_DIRECTORIES = ("rm_client", "tests", "tools", "configs")
SOURCE_SUFFIXES = frozenset({".py", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg",
                             ".c", ".cpp", ".h", ".hpp", ".ps1", ".bat", ".spec"})
SOURCE_SCOPE = {
    "directories": list(SOURCE_DIRECTORIES),
    "suffixes": sorted(SOURCE_SUFFIXES),
    "root_files": ["main.py", "requirements*.txt"],
    "exclusions": ["hidden directories", "__pycache__"],
    "limits": "Start/end comparison only; excludes assets, external fixtures, installed packages and transient edits restored between captures.",
}


def source_files(root: Path) -> list[Path]:
    files = set()
    for name in SOURCE_DIRECTORIES:
        directory = root / name
        if directory.is_symlink():
            raise ValueError("Source directory must not be a symbolic link")
        for path in directory.rglob("*"):
            parts = path.relative_to(root).parts
            if any(part.startswith(".") or part == "__pycache__" for part in parts[:-1]):
                continue
            if path.is_symlink():
                # Do not follow a source link outside the selected checkout or
                # silently exclude a linked directory's unmeasured contents.
                raise ValueError("Source input must not be a symbolic link")
            if path.suffix.lower() in SOURCE_SUFFIXES and path.is_file():
                files.add(path)
    files.update(path for path in root.glob("requirements*.txt") if path.is_file())
    if (root / "main.py").is_file():
        files.add(root / "main.py")
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def capture_sources(root: Path) -> dict:
    """Hash exact bytes and relative names; never emit source paths or contents."""
    try:
        paths = source_files(root)
        if not paths:
            raise ValueError("No verification source inputs found")
        digest = hashlib.sha256()
        for path in paths:
            if path.is_symlink():
                raise ValueError("Source input must not be a symbolic link")
            relative = path.relative_to(root).as_posix().encode("utf-8")
            content = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    content.update(chunk)
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(content.digest())
    except (OSError, ValueError) as exc:
        return {"status": "unavailable", "sha256": None, "file_count": None,
                "error_type": type(exc).__name__}
    return {"status": "available", "sha256": digest.hexdigest(), "file_count": len(paths)}


def compare_sources(before: dict, after: dict) -> tuple[str, str]:
    if before["status"] != "available" or after["status"] != "available":
        return "INCOMPLETE", "Could not establish start/end verification source fingerprints"
    if before["sha256"] != after["sha256"]:
        return "FAIL", "Verification code or contract inputs changed during execution; results span different source states"
    return "PASS", "Declared verification source inputs have identical start/end fingerprints"
