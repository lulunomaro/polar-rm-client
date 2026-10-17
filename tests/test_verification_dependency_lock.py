"""Offline consistency checks for the scoped, hash-pinned Linux environment."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import unittest
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import verification_provenance

EXPECTED_VERSIONS = {
    "PySide6": "6.8.2", "PySide6-Addons": "6.8.2", "PySide6-Essentials": "6.8.2",
    "shiboken6": "6.8.2", "numpy": "2.5.4", "opencv-python": "5.0.0.93",
    "openvino": "2026.4.1", "openvino-telemetry": "2025.2.0", "paho-mqtt": "2.1.0", "pyserial": "3.5",
}


class VerificationDependencyLockTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "tools/quality/verification_environment.json").read_text(encoding="utf-8"))
        self.lines = (ROOT / "requirements-verification-linux-cp312.txt").read_text(encoding="utf-8").splitlines()

    def test_all_runtime_and_transitive_versions_are_exactly_pinned(self):
        entries = {}
        for line in self.lines:
            if not line.strip() or line.startswith(("#", "--")):
                continue
            match = re.fullmatch(r"([A-Za-z0-9-]+)==([0-9.]+) --hash=sha256:([a-f0-9]{64})", line)
            self.assertIsNotNone(match, line)
            name, version, digest = match.groups()
            self.assertNotIn(name, entries)
            entries[name] = (version, digest)
        self.assertEqual({name: value[0] for name, value in entries.items()}, EXPECTED_VERSIONS)
        self.assertEqual(entries, {item["name"]: (item["version"], item["sha256"])
                                   for item in self.manifest["packages"]})
        self.assertEqual(len(self.manifest["packages"]), len(entries))

    def test_install_policy_requires_wheels_hashes_and_official_index(self):
        options = [line for line in self.lines if line.startswith("--")]
        self.assertEqual(options, ["--index-url https://pypi.org/simple", "--only-binary=:all:", "--require-hashes"])
        for item in self.manifest["packages"]:
            source, wheel = urlparse(item["source"]), urlparse(item["url"])
            self.assertEqual((source.scheme, source.netloc), ("https", "pypi.org"))
            self.assertEqual(source.path, f"/pypi/{item['name']}/{item['version']}/json")
            self.assertEqual((wheel.scheme, wheel.netloc), ("https", "files.pythonhosted.org"))
            self.assertTrue(wheel.path.endswith("/" + item["wheel"]))
            self.assertTrue(item["wheel"].endswith(".whl"))

    def test_platform_scope_is_explicit_and_does_not_replace_runtime_ranges(self):
        self.assertEqual(self.manifest["interpreter"], {"implementation": "CPython", "version": "3.12.14"})
        self.assertEqual(self.manifest["platform"], {"system": "Linux", "machine": "x86_64", "minimum_glibc": "2.28"})
        self.assertIn("not Windows", self.manifest["scope"])
        self.assertIn("PySide6>=6.6,<7", (ROOT / "requirements-core.txt").read_text(encoding="utf-8"))
        self.assertIn("openvino>=2024.0", (ROOT / "requirements.txt").read_text(encoding="utf-8"))

    def test_provenance_includes_the_telemetry_consent_dependency(self):
        self.assertIn("openvino-telemetry", verification_provenance.DEPENDENCIES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
