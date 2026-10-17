"""Stdlib-only provenance tests; no optional runtime imports or network."""
from __future__ import annotations

import builtins
import contextlib
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import verification_provenance as provenance
from tools import verify_all as verify
from tools.test_registry import TestEntry

COMMIT = "a" * 40
TREE = "b" * 40


def git_result(output="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, output, stderr)


class SourceIdentityTests(unittest.TestCase):
    def test_clean_commit_tree_and_dirty_state_have_a_stable_shape(self):
        with mock.patch.object(provenance.subprocess, "run", side_effect=[
            git_result(COMMIT + "\n"), git_result(TREE + "\n"), git_result(),
        ]) as run:
            result = provenance.source_identity(Path("private-checkout"))
        self.assertEqual(result, {
            "status": "available", "commit": COMMIT, "tree": TREE, "dirty": False,
            "dirty_scope": "tracked-and-untracked-nonignored", "reason": None,
        })
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[1].args[0][-1], COMMIT + "^{tree}")
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:4], ["git", "--no-optional-locks", "-C", "private-checkout"])
            self.assertIn(call.args[0][4], ("rev-parse", "status"))
            self.assertEqual(call.kwargs, {"capture_output": True, "text": True,
                                         "encoding": "utf-8", "errors": "replace",
                                         "timeout": 5, "check": False})

    def test_head_change_cannot_pair_the_old_commit_with_the_new_tree(self):
        current_head = COMMIT

        def git_read(command, **kwargs):
            nonlocal current_head
            revision = command[-1]
            if revision == "HEAD":
                captured = current_head
                current_head = "c" * 40
                return git_result(captured)
            if command[4] == "status":
                return git_result()
            trees = {COMMIT + "^{tree}": TREE, current_head + "^{tree}": "d" * 40,
                     "HEAD^{tree}": "d" * 40}
            return git_result(trees[revision])

        with mock.patch.object(provenance.subprocess, "run", side_effect=git_read):
            result = provenance.source_identity(ROOT)
        self.assertEqual((result["commit"], result["tree"]), (COMMIT, TREE))

    def test_dirty_file_names_and_paths_are_not_reported(self):
        with mock.patch.object(provenance.subprocess, "run", side_effect=[
            git_result(COMMIT), git_result(TREE), git_result("?? private-operator-name.txt\n"),
        ]):
            result = provenance.source_identity(Path("private-checkout"))
        self.assertTrue(result["dirty"])
        self.assertNotIn("private", json.dumps(result))

    def test_missing_git_is_explicitly_unavailable_not_clean(self):
        with mock.patch.object(provenance.subprocess, "run", side_effect=FileNotFoundError("private/path/git")):
            result = provenance.source_identity(ROOT)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["dirty"])
        self.assertIsNone(result["commit"])
        self.assertIsNone(result["tree"])
        self.assertEqual(result["reason"], {"query": "commit", "error_type": "FileNotFoundError"})
        self.assertNotIn("private", json.dumps(result))

    def test_non_git_snapshot_keeps_command_failure_without_stderr(self):
        with mock.patch.object(provenance.subprocess, "run", return_value=git_result(
            returncode=128, stderr="fatal: private checkout or credential path",
        )):
            result = provenance.source_identity(ROOT)
        self.assertEqual(result["reason"], {"query": "commit", "returncode": 128})
        self.assertIsNone(result["dirty"])
        self.assertNotIn("private", json.dumps(result))

    def test_partial_git_evidence_never_claims_a_complete_identity(self):
        with mock.patch.object(provenance.subprocess, "run", side_effect=[
            git_result(COMMIT), git_result(returncode=1),
        ]):
            result = provenance.source_identity(ROOT)
        self.assertEqual(result["commit"], COMMIT)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], {"query": "tree", "returncode": 1})
        self.assertIsNone(result["dirty"])

    def test_timeout_or_permission_failure_does_not_disclose_command_details(self):
        for error in (subprocess.TimeoutExpired(["git", "private/path"], 5),
                      PermissionError("private/path")):
            with self.subTest(error=type(error).__name__), mock.patch.object(
                provenance.subprocess, "run", side_effect=error,
            ):
                result = provenance.source_identity(ROOT)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["reason"]["error_type"], type(error).__name__)
                self.assertNotIn("private", json.dumps(result))

    def test_malformed_object_id_is_not_accepted_as_a_revision(self):
        with mock.patch.object(provenance.subprocess, "run", return_value=git_result("HEAD")):
            result = provenance.source_identity(ROOT)
        self.assertEqual(result["reason"]["error_type"], "InvalidGitObjectId")
        self.assertIsNone(result["commit"])

    def test_sha256_git_object_ids_are_also_supported(self):
        with mock.patch.object(provenance.subprocess, "run", side_effect=[
            git_result("a" * 64), git_result("b" * 64), git_result(),
        ]):
            result = provenance.source_identity(ROOT)
        self.assertEqual(result["status"], "available")
        self.assertEqual(len(result["commit"]), 64)


class EnvironmentFactsTests(unittest.TestCase):
    def test_installed_missing_and_unreadable_metadata_remain_distinct(self):
        def version(name):
            if name == "PySide6":
                return "6.8.2"
            if name == "numpy":
                raise PermissionError("private-metadata-path")
            raise provenance.metadata.PackageNotFoundError(name)
        with mock.patch.object(provenance.metadata, "version", side_effect=version):
            result = provenance.distribution_versions()
        self.assertEqual(set(result), set(provenance.DEPENDENCIES))
        self.assertEqual(result["PySide6"], {"status": "installed", "version": "6.8.2"})
        self.assertEqual(result["openvino"], {"status": "not-installed", "version": None})
        self.assertEqual(result["numpy"], {"status": "unavailable", "version": None,
                                          "error_type": "PermissionError"})
        self.assertNotIn("private", json.dumps(result))

    def test_malformed_version_cannot_leak_arbitrary_metadata_text(self):
        with mock.patch.object(provenance.metadata, "version", return_value="private/path token=value"):
            result = provenance.distribution_versions()
        self.assertTrue(all(item["status"] == "unavailable" for item in result.values()))
        self.assertNotIn("private", json.dumps(result))

    def test_actual_metadata_collection_does_not_import_heavy_dependencies(self):
        original = builtins.__import__
        forbidden = {"PySide6", "shiboken6", "numpy", "cv2", "paho", "serial", "openvino", "openvino_telemetry", "PyInstaller", "ziglang"}

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in forbidden:
                raise AssertionError(f"Optional package import attempted: {name}")
            return original(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded):
            versions = provenance.distribution_versions()
        self.assertEqual(set(versions), set(provenance.DEPENDENCIES))
        self.assertTrue(all(item.get("error_type") != "AssertionError" for item in versions.values()))

    def test_top_level_schema_reports_only_explicit_nonsecret_facts(self):
        with mock.patch.object(provenance, "distribution_versions", return_value={}), \
             mock.patch.object(provenance, "source_identity", return_value={"status": "unavailable"}):
            result = provenance.collect_provenance(ROOT)
        self.assertEqual(set(result), {"schema_version", "captured_at_utc", "interpreter", "os",
                                       "dependencies", "source_at_start"})
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(set(result["interpreter"]), {"implementation", "version"})
        self.assertEqual(set(result["os"]), {"system", "release", "machine"})
        self.assertEqual(datetime.fromisoformat(result["captured_at_utc"]).utcoffset(), timezone.utc.utcoffset(None))


class RunnerProvenanceTests(unittest.TestCase):
    def test_real_inert_child_report_works_from_a_snapshot_without_git(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(verify, "ROOT", Path(directory)), \
             mock.patch.object(verify, "acquire_lock", return_value=True), \
             contextlib.redirect_stdout(io.StringIO()):
            script = Path(directory) / "tests/test_inert.py"
            script.parent.mkdir()
            script.write_text("print('[PASS] inert child executed')\n", encoding="utf-8")
            report_path = Path(directory) / "report.json"
            self.assertEqual(verify.main(["--test", "test_inert", "--json", str(report_path)]), 0)
            result = json.loads(report_path.read_text())
        self.assertEqual(result["results"][0]["checks_passed"], 1)
        self.assertEqual(result["counts"]["PASS"], 1)
        self.assertEqual(result["provenance"]["source_at_start"]["status"], "unavailable")
        self.assertIsNone(result["provenance"]["source_at_start"]["dirty"])
        self.assertNotIn(directory, json.dumps(result))

    def test_metadata_is_additive_and_preserves_existing_report_consumers(self):
        facts = {"schema_version": 1, "source_at_start": {"status": "unavailable", "dirty": None}}
        entries = (TestEntry("tests/test_inert.py"),)
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(verify, "discover", return_value=entries), \
             mock.patch.object(verify, "acquire_lock", return_value=True), \
             mock.patch.object(verify, "collect_provenance", return_value=facts), \
             mock.patch.object(verify, "run_entry", return_value=verify.CheckResult("PASS", "1 inert check", tests_run=1)), \
             contextlib.redirect_stdout(io.StringIO()):
            report_path = Path(directory) / "report.json"
            self.assertEqual(verify.main(["--test", "test_inert", "--json", str(report_path)]), 0)
            result = json.loads(report_path.read_text())
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["provenance"], facts)
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["results"][0]["tests_run"], 1)
        self.assertEqual(result["counts"], {"PASS": 1, "SKIP": 0, "INCOMPLETE": 0, "FAIL": 0})
        self.assertTrue(result["passed"])
        self.assertIsNone(result["firmware_identity"])

    def test_inventory_does_not_run_git_metadata_or_test_execution(self):
        with mock.patch.object(verify, "discover", return_value=(TestEntry("tests/test_inert.py"),)), \
             mock.patch.object(verify, "collect_provenance") as collect, \
             mock.patch.object(verify, "run_entry") as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(verify.main(["--list"]), 0)
        collect.assert_not_called()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
