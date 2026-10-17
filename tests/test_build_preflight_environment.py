"""Portable preflight root isolation; no Windows process or qualification.

PowerShell checks are source contracts. RuntimePaths is exercised directly
with isolated environment mappings and does not create directories or files.
"""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.runtime_paths import RuntimePaths


class PortablePreflightEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / "tools/build_release.ps1").read_text(encoding="utf-8-sig")
        cls.function = source.split("function Invoke-PortablePreflight(", 1)[1].split(
            "function Get-TreeHash(", 1)[0]

    def child_environment(self, parent):
        # Read the declared child-only removals from the actual builder.
        # This models an environment mapping, not ProcessStartInfo execution.
        removals = re.findall(r'\$startInfo\.EnvironmentVariables\.Remove\("([^"]+)"\)',
                              self.function)
        child = dict(parent)
        for name in removals:
            child.pop(name, None)
        return child

    def test_override_removed_from_child_before_process_start_only(self):
        removal = '$startInfo.EnvironmentVariables.Remove("POLAR_APP_ROOT")'
        self.assertIn(removal, self.function)
        self.assertLess(self.function.index(removal),
                        self.function.index('[System.Diagnostics.Process]::Start($startInfo)'))
        self.assertNotRegex(self.function, r'\$env:POLAR_APP_ROOT\s*=')
        self.assertNotIn('Remove-Item Env:POLAR_APP_ROOT', self.function)

    def test_inherited_root_cannot_redirect_candidate_preflight(self):
        executable = ROOT / "candidate" / "POLAR-RM.exe"
        other_root = ROOT / "other-installation"
        parent = {"POLAR_APP_ROOT": str(other_root), "PATH": "sentinel-path",
                  "UNRELATED_SETTING": "keep"}
        before = dict(parent)
        contaminated = RuntimePaths.discover(executable=executable, frozen=True,
                                              environ=parent)
        self.assertEqual(contaminated.manifest_path, other_root / "BUILD_MANIFEST.json")
        child = self.child_environment(parent)
        paths = RuntimePaths.discover(executable=executable, frozen=True, environ=child)
        self.assertEqual(paths.app_root, executable.parent)
        self.assertEqual(paths.manifest_path, executable.parent / "BUILD_MANIFEST.json")
        self.assertEqual(paths.ffmpeg_path, executable.parent / "bin" / "ffmpeg.exe")
        self.assertEqual(paths.user_data_root, executable.parent / "user_data")
        self.assertEqual(parent, before)
        self.assertEqual(child, {key: value for key, value in parent.items()
                                 if key != "POLAR_APP_ROOT"})

    def test_absent_override_preserves_remaining_child_environment(self):
        parent = {"PATH": "sentinel-path", "UNRELATED_SETTING": "keep"}
        self.assertEqual(self.child_environment(parent), parent)


if __name__ == "__main__":
    unittest.main()
