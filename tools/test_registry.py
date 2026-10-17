"""Authoritative, import-free inventory shared by verification and release.

New test_*.py, *_test.py, *_smoke.py and *_check.py files under tests/,
and test_*.py under tools/, are discovered automatically. Helpers should not
use those names. Metadata below only declares exceptional prerequisites and
completion contracts, never a second hand-maintained list of ordinary suites.
All evidence is scoped: offline assertions do not qualify a real robot.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ("offline", "desktop", "firmware", "external", "visual")
PROFILES = {
    "offline": frozenset(("offline", "firmware")),
    "release": frozenset(("offline", "desktop")),
    "all": frozenset(CATEGORIES),
}

# These need native GL. Offscreen/raster/mocked GL is not equivalent evidence.
DESKTOP_TESTS = {
    "tests/gpu_renderer_runtime_smoke.py": (),
    "tests/startup_gpu_runtime_smoke.py": (),
    "tests/test_escape_menu_corner_pixels.py": ("--require-gpu",),
    "tests/test_jello_white_armor_overlay.py": ("--require-gpu",),
}
# These generate temporary fixtures without an external firmware worktree.
PORTABLE_FIRMWARE_TESTS = frozenset((
    "tools/firmware/km_link/test_serial_parsers_portable.py",
    "tools/firmware/test_prepare_hero_options.py",
))
FIRMWARE_LINK_TESTS = frozenset((
    "tools/dt7_link/test_cc_link.py",
    "tools/dt7_link/test_dbus_frame_vs_firmware.py",
    "tools/dt7_link/test_dt7_link.py",
    "tools/dt7_link/test_dt7_ui_selftest.py",
))
# Legacy assertion scripts without unittest/[PASS]/[OK] counters need their
# exact completion marker. Exit zero alone never constitutes a passing test.
COMPLETION_PATTERNS = {
    "tests/backend_protocol_test.py": r"^(?:\\n)?ALL PASSED \([1-9]\d* checks\)$",
    "tests/referee_integration_test.py": r"^ALL PASSED \([1-9]\d* checks; localhost only; NOT field qualification\)$",
    "tests/dt7_alignment_test.py": r"^DT7 alignment PASS: [1-9]\d* checks;",
    "tests/field_candidate_test.py": r"^FIELD CANDIDATE TESTS PASSED$",
    "tests/gpu_compositor_test.py": r"^gpu compositor test passed$",
    "tests/gpu_renderer_runtime_smoke.py": r"^GPU renderer runtime smoke passed$",
    "tests/startup_gpu_runtime_smoke.py": r"^Startup GPU compositor runtime smoke passed at ",
    "tests/transition_visual_check.py": r"^Transition visual frames captured$",
}


CHECK_COUNT_PATTERNS = {
    "tests/backend_protocol_test.py": r"(?m)^(?:\\n)?(?:ALL PASSED|INCOMPLETE) \((\d+) checks",
    "tests/referee_integration_test.py": r"(?m)^ALL PASSED \((\d+) checks;",
    "tests/dt7_alignment_test.py": r"(?m)^DT7 alignment PASS: (\d+) checks;",
}


@dataclass(frozen=True)
class TestEntry:
    path: str
    category: str = "offline"
    args: tuple[str, ...] = ()
    completion_pattern: str | None = None
    check_count_pattern: str | None = None
    evidence: str = "offline assertions only; no physical hardware qualification"

    @property
    def id(self) -> str:
        return self.path[:-3]

    def to_dict(self) -> dict:
        return {"id": self.id, **asdict(self),
                "profiles": [name for name, categories in PROFILES.items()
                             if self.category in categories]}


def is_test_file(path: Path, *, under_tools: bool = False) -> bool:
    name = path.name
    return name.endswith(".py") and (
        name.startswith("test_") or
        (not under_tools and name.endswith(("_test.py", "_smoke.py", "_check.py")))
    )


def discover(root: Path = ROOT) -> tuple[TestEntry, ...]:
    """List tests without importing Qt, hardware libraries, or test modules."""
    entries = []
    for directory in ("tests", "tools"):
        for path in sorted((root / directory).rglob("*.py")):
            if not is_test_file(path, under_tools=directory == "tools"):
                continue
            if "__pycache__" in path.parts or any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            relative = path.relative_to(root).as_posix()
            # This registry itself is infrastructure, not an executable suite.
            if relative == "tools/test_registry.py":
                continue
            category, args = "offline", ()
            evidence = "offline assertions only; no physical hardware qualification"
            if relative in DESKTOP_TESTS or relative.endswith("_runtime_smoke.py"):
                category, args = "desktop", DESKTOP_TESTS.get(relative, ())
                evidence = "native desktop OpenGL; no physical robot qualification"
            elif relative.endswith("_visual_check.py"):
                category = "visual"
                evidence = "desktop visual capture; no physical robot qualification"
            elif relative.endswith("_external_integration_test.py"):
                category = "external"
                evidence = "explicit external fixtures; no physical robot qualification"
            elif ((relative.startswith("tools/firmware/") and relative not in PORTABLE_FIRMWARE_TESTS)
                  or relative in FIRMWARE_LINK_TESTS):
                category = "firmware"
                evidence = "read-only selected firmware and portable host compilation; not board execution"
            entries.append(TestEntry(relative, category, args, COMPLETION_PATTERNS.get(relative),
                                     CHECK_COUNT_PATTERNS.get(relative), evidence))
    return tuple(entries)


def select(entries: tuple[TestEntry, ...], *, profile: str = "offline",
           categories: tuple[str, ...] = (), names: tuple[str, ...] = ()) -> tuple[TestEntry, ...]:
    """Explicit names select across profiles; category filters stay explicit."""
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile: {profile}")
    unknown_categories = set(categories) - set(CATEGORIES)
    if unknown_categories:
        raise ValueError(f"Unknown categories: {', '.join(sorted(unknown_categories))}")
    matches: set[str] = set()
    if names:
        for name in names:
            normalized = name.replace("\\", "/").removeprefix("./")
            found = [entry for entry in entries if normalized in (
                entry.path, entry.id, Path(entry.path).name, Path(entry.path).stem)]
            if len(found) != 1:
                raise ValueError(f"Test selector {name!r} matched {len(found)} suites; use its full relative path")
            matches.add(found[0].id)
    effective_categories = set(categories or PROFILES[profile])
    if not names and "desktop" in effective_categories:
        missing = set(DESKTOP_TESTS) - {entry.path for entry in entries}
        if missing:
            raise ValueError("Required native desktop gates are missing: " + ", ".join(sorted(missing)))
    selected = tuple(entry for entry in entries
                     if (entry.id in matches if names else entry.category in (categories or PROFILES[profile]))
                     and (not categories or entry.category in categories))
    if not selected:
        raise ValueError("Selection contains zero test suites")
    return selected
