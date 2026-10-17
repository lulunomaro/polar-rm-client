"""Start/end evidence guards using inert scripts and temporary source trees."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import verification_sources as sources
from tools import verify_all as verify


class SourceFingerprintTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.write("rm_client/example.py", b"VALUE = 1\n")

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def capture(self):
        return sources.capture_sources(self.root)

    def test_same_bytes_are_independent_of_timestamps_and_walk_order(self):
        self.write("tests/test_example.py", b"assert True\n")
        self.write("tools/contracts.json", b'{"public": true}\n')
        before = self.capture()
        os.utime(self.path, (1, 1))
        original = Path.rglob
        with mock.patch.object(Path, "rglob", lambda path, pattern: reversed(list(original(path, pattern)))):
            after = self.capture()
        self.assertEqual(before, after)
        self.assertEqual(before["file_count"], 3)
        self.assertEqual(sources.compare_sources(before, after)[0], "PASS")
        self.assertNotIn(str(self.root), json.dumps(before))
        self.assertNotIn("example", json.dumps(before))

    def test_changes_additions_removals_and_renames_change_the_fingerprint(self):
        before = self.capture()
        self.path.write_bytes(b"VALUE = 2\n")
        self.assertEqual(sources.compare_sources(before, self.capture())[0], "FAIL")
        self.path.write_bytes(b"VALUE = 1\n")
        added = self.write("tests/test_added.py", b"assert True\n")
        after_add = self.capture()
        self.assertEqual(sources.compare_sources(before, after_add)[0], "FAIL")
        added.unlink()
        self.assertEqual(sources.compare_sources(after_add, self.capture())[0], "FAIL")
        self.assertEqual(before, self.capture())
        self.path.rename(self.path.with_name("renamed.py"))
        self.assertEqual(sources.compare_sources(before, self.capture())[0], "FAIL")

    def test_dependency_and_configuration_contracts_are_inputs(self):
        for name in ("main.py", "requirements-verification.txt", "configs/default.json",
                     "tests/frozen_contract.json", "tools/native/parser.c", "tools/native/parser.h",
                     "tools/build_release.ps1", "rm_client/backend/polar_protocol.json"):
            with self.subTest(name=name):
                before = self.capture()
                path = self.write(name, b"original\n")
                after_add = self.capture()
                self.assertNotEqual(before["sha256"], after_add["sha256"])
                path.write_bytes(b"changed\n")
                self.assertNotEqual(after_add["sha256"], self.capture()["sha256"])

    def test_generated_outputs_and_out_of_scope_assets_are_explicitly_excluded(self):
        before = self.capture()
        for name in ("tests/__pycache__/example.py", "tools/.private/example.py",
                     "tests/_gpu_renderer_runtime.png", "tmp_run/result.json",
                     "docs/evidence/results.json", "assets/texture.png"):
            self.write(name, b"excluded\n")
        self.assertEqual(before, self.capture())
        self.assertIn("excludes assets", sources.SOURCE_SCOPE["limits"])

    def test_empty_or_unreadable_tree_is_unavailable_not_empty_success(self):
        with mock.patch.object(Path, "open", side_effect=PermissionError("private/source-path")):
            result = self.capture()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["error_type"], "PermissionError")
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(sources.compare_sources(result, result)[0], "INCOMPLETE")
        self.path.unlink()
        self.assertEqual(self.capture()["status"], "unavailable")

    def test_linked_source_is_rejected_without_reading_its_target(self):
        # Exercise the filesystem seam without requiring Windows developer-mode
        # permission to create a real symlink or adding a platform skip.
        for linked in (self.path, self.root / "rm_client"):
            with self.subTest(linked=linked.name), \
                 mock.patch.object(Path, "is_symlink", lambda path: path == linked), \
                 mock.patch.object(Path, "open", side_effect=AssertionError("must not read linked source")):
                result = self.capture()
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["error_type"], "ValueError")


class RunnerSourceStabilityTests(unittest.TestCase):
    def run_child(self, body):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "tests/test_inert.py"
            script.parent.mkdir()
            script.write_text(body, encoding="utf-8")
            report = root / "report.json"
            with mock.patch.object(verify, "ROOT", root), \
                 mock.patch.object(verify, "acquire_lock", return_value=True), \
                 contextlib.redirect_stdout(io.StringIO()):
                status = verify.main(["--test", "test_inert", "--json", str(report)])
            return status, json.loads(report.read_text(encoding="utf-8"))

    def test_unchanged_inert_child_passes_without_git_and_reports_exact_scope(self):
        status, report = self.run_child("print('[PASS] inert child executed')\n")
        self.assertEqual(status, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(report["source_stability"]["status"], "PASS")
        self.assertEqual(report["source_stability"]["before"], report["source_stability"]["after"])
        self.assertEqual(report["source_stability"]["scope"], sources.SOURCE_SCOPE)
        self.assertEqual(report["counts"]["PASS"], 1)
        self.assertEqual(report["provenance"]["source_at_start"]["status"], "unavailable")

    def test_real_source_mutation_cannot_produce_a_successful_aggregate(self):
        status, report = self.run_child(
            "from pathlib import Path\n"
            "Path(__file__).write_text('# source changed during execution\\n')\n"
            "print('[PASS] earlier assertion')\n"
        )
        self.assertEqual(status, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["source_stability"]["status"], "FAIL")
        self.assertEqual(report["counts"], {"PASS": 1, "SKIP": 0, "INCOMPLETE": 0, "FAIL": 1})
        self.assertEqual(report["results"][-1]["id"], "verification_source_stability")

    def test_added_source_after_inventory_is_also_rejected(self):
        status, report = self.run_child(
            "from pathlib import Path\n"
            "Path(__file__).with_name('test_added.py').write_text('assert False\\n')\n"
            "print('[PASS] original selected assertion')\n"
        )
        self.assertEqual(status, 1)
        self.assertEqual(report["source_stability"]["status"], "FAIL")
        self.assertEqual(report["selected_count"], 1)
        self.assertEqual(report["source_stability"]["after"]["file_count"], 2)

    def test_unavailable_source_fingerprint_preserves_check_but_marks_incomplete(self):
        failed = {"status": "unavailable", "sha256": None, "file_count": None, "error_type": "PermissionError"}
        with mock.patch.object(verify, "capture_sources", return_value=failed):
            status, report = self.run_child("print('[PASS] inert assertion')\n")
        self.assertEqual(status, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["results"][0]["status"], "PASS")
        self.assertEqual(report["source_stability"]["status"], "INCOMPLETE")
        self.assertEqual(report["counts"]["INCOMPLETE"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
