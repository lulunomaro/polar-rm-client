"""Dependency-free registry and evidence-runner regressions.

Run: python tests/test_test_registry.py
Fixtures are temporary Python scripts; no Qt, firmware, network, or hardware.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import test_registry as registry
from tools import verify_all as verify


class RegistryTests(unittest.TestCase):
    def fixture(self, *names):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("raise AssertionError('discovery must never import me')\n")
        return root

    def test_new_tests_are_discovered_without_manual_registration_or_import(self):
        root = self.fixture("tests/test_new.py", "tests/new_test.py", "tests/nested/test_later.py",
                            "tests/new_runtime_smoke.py", "tests/new_visual_check.py", "tools/deep/test_added.py",
                            "tests/helper.py", "tools/test_registry.py", "tools/ordinary_tool.py")
        entries = registry.discover(root)
        self.assertEqual(len(entries), 6)
        by_path = {entry.path: entry for entry in entries}
        self.assertEqual(by_path["tests/new_runtime_smoke.py"].category, "desktop")
        self.assertEqual(by_path["tests/new_visual_check.py"].category, "visual")
        self.assertIn("tools/deep/test_added.py", by_path)
        self.assertEqual(entries, registry.discover(root))

    def test_real_inventory_has_no_omitted_test_files(self):
        expected = {path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").rglob("*.py")
                    if registry.is_test_file(path)}
        expected.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "tools").rglob("test_*.py")
                        if path.name != "test_registry.py")
        actual = [entry.path for entry in registry.discover(ROOT)]
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(set(actual)))
        self.assertIn("tests/test_test_registry.py", actual)

    def test_release_always_includes_real_runtime_gates_and_new_offline_tests(self):
        entries = registry.discover(ROOT)
        release = {entry.path: entry for entry in registry.select(entries, profile="release")}
        for name in ("gpu_renderer_runtime_smoke", "startup_gpu_runtime_smoke"):
            self.assertEqual(release[f"tests/{name}.py"].category, "desktop")
        for name in ("test_escape_menu_corner_pixels", "test_jello_white_armor_overlay"):
            self.assertEqual(release[f"tests/{name}.py"].args, ("--require-gpu",))
        self.assertIn("tests/test_test_registry.py", release)
        self.assertIn("tests/smoke_test.py", release)
        self.assertNotIn("tests/tongji_external_integration_test.py", release)
        self.assertNotIn("tests/transition_visual_check.py", release)

    def test_deleted_native_gate_cannot_silently_shrink_release(self):
        entries = tuple(entry for entry in registry.discover(ROOT)
                        if entry.path != "tests/startup_gpu_runtime_smoke.py")
        with self.assertRaisesRegex(ValueError, "Required native desktop gates are missing"):
            registry.select(entries, profile="release")

    def test_category_and_explicit_name_override_default_offline_profile(self):
        entries = registry.discover(ROOT)
        desktop = registry.select(entries, categories=("desktop",))
        self.assertTrue(desktop)
        self.assertTrue(all(entry.category == "desktop" for entry in desktop))
        named = registry.select(entries, names=("gpu_renderer_runtime_smoke",))
        self.assertEqual(len(named), 1)
        self.assertEqual(named[0].category, "desktop")

    def test_unknown_ambiguous_empty_and_conflicting_selections_fail_closed(self):
        root = self.fixture("tests/test_same.py", "tools/test_same.py")
        entries = registry.discover(root)
        for names in (("missing",), ("test_same",)):
            with self.subTest(names=names), self.assertRaises(ValueError):
                registry.select(entries, names=names)
        with self.assertRaises(ValueError):
            registry.select(entries, names=("tests/test_same.py",), categories=("desktop",))
        with self.assertRaises(ValueError):
            registry.select(())
        with self.assertRaises(ValueError):
            registry.select(entries, profile="typo")
        with self.assertRaises(ValueError):
            registry.select(entries, categories=("typo",))
        self.assertEqual(len(registry.select(entries, names=("tests\\test_same.py",))), 1)

    def test_inventory_cli_needs_no_dependencies_and_executes_nothing(self):
        with mock.patch.object(verify, "run_entry", side_effect=AssertionError("must not execute")), \
             tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            target = Path(temp) / "inventory.json"
            self.assertEqual(verify.main(["--list", "--profile", "all", "--json", str(target)]), 0)
            result = json.loads(target.read_text())
        self.assertEqual(result["total_discovered"], result["selected_count"])
        self.assertNotIn("passed", result)
        self.assertNotIn("results", result)


class EvidenceClassificationTests(unittest.TestCase):
    def result(self, text, code=0, completion=None):
        return verify.classify_output(code, text, completion_pattern=completion)

    def test_unittest_pass_counts_are_real(self):
        result = self.result("....\nRan 4 tests in 0.02s\n\nOK\n")
        self.assertEqual((result.status, result.tests_run, result.skipped), ("PASS", 4, 0))

    def test_unittest_partial_skip_is_incomplete(self):
        result = self.result("....s\nRan 5 tests in 0.02s\n\nOK (skipped=1)\n")
        self.assertEqual((result.status, result.tests_run, result.skipped), ("INCOMPLETE", 5, 1))

    def test_unittest_all_skipped_is_skip_not_pass(self):
        result = self.result("ss\nRan 2 tests in 0.02s\n\nOK (skipped=2)\n")
        self.assertEqual((result.status, result.tests_run, result.skipped), ("SKIP", 2, 2))

    def test_unittest_skipped_class_with_zero_tests_is_never_green(self):
        result = self.result("s\nRan 0 tests in 0.000s\n\nOK (skipped=1)\n")
        self.assertEqual(result.status, "SKIP")

    def test_zero_tests_or_empty_success_never_pass(self):
        for text in ("", "success", "Ran 0 tests in 0.000s\nOK", "Ran 3 tests in 0.001s\n"):
            with self.subTest(text=text):
                self.assertEqual(self.result(text).status, "INCOMPLETE")

    def test_multiple_unittest_runs_cannot_hide_zero_or_skips(self):
        for tail in ("Ran 0 tests in 0s\nOK", "Ran 1 test in 0s\nOK (skipped=1)"):
            with self.subTest(tail=tail):
                self.assertEqual(self.result("Ran 3 tests in 0s\nOK\n" + tail).status, "INCOMPLETE")

    def test_expected_failure_is_not_counted_as_a_passed_assertion(self):
        result = self.result("x\nRan 1 test in 0s\nOK (expected failures=1)")
        self.assertEqual((result.status, result.tests_run, result.expected_failures), ("INCOMPLETE", 1, 1))
        self.assertIn("unresolved", result.detail)
        mixed = self.result(".sx\nRan 3 tests in 0s\nOK (skipped=1, expected failures=1)")
        self.assertEqual((mixed.status, mixed.skipped, mixed.expected_failures), ("INCOMPLETE", 1, 1))
        multiple = self.result("Ran 1 test in 0s\nOK\nRan 1 test in 0s\nOK (expected failures=1)")
        self.assertEqual((multiple.status, multiple.tests_run, multiple.expected_failures), ("INCOMPLETE", 2, 1))

    def test_legacy_skip_spellings_and_exit_77_never_pass(self):
        for skip in ("[SKIP] missing", "SKIP: missing", "SKIP optional fixture"):
            with self.subTest(skip=skip):
                self.assertEqual(self.result(skip).status, "SKIP")
                self.assertEqual(self.result("[PASS] assertion\n" + skip).status, "INCOMPLETE")
        for incomplete in ("[INCOMPLETE] missing", "INCOMPLETE: no evidence", r"\nINCOMPLETE (0 checks passed; 1 dependency-blocked tests skipped)"):
            self.assertEqual(self.result(incomplete, 77).status, "INCOMPLETE")
        self.assertEqual(self.result("", 77).status, "SKIP")
        self.assertEqual(self.result("[ OK ] assertion", 77).status, "INCOMPLETE")

    def test_failure_takes_precedence_over_skip_or_pass(self):
        result = self.result("[PASS] first\nSKIP: absent\n[FAIL] second", 1)
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(self.result("Ran 2 tests in 0s\nFAILED (failures=1)").status, "FAIL")
        self.assertEqual(self.result("[PASS] first", -9).status, "FAIL")

    def test_swallowed_failure_markers_override_exit_zero(self):
        for marker in ("[FAIL] hidden assertion", "  [FAIL] indented failure", "FAIL: case", "ERROR: case"):
            with self.subTest(marker=marker):
                self.assertEqual(self.result("[PASS] one check\n" + marker).status, "FAIL")
        self.assertEqual(self.result("[PASS] assertion handles the string '[FAIL]'").status, "PASS")

    def test_expected_negative_fixture_labels_do_not_hide_real_assertions(self):
        path = ROOT / "tools/firmware/test_prepare_hero_options.py"
        tree = ast.parse(path.read_text())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ("expected_failure_output", "check")]
        ns = {"contextlib": contextlib, "io": io, "FAILED": 0}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), ns)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with ns["expected_failure_output"]():
                print("  [FAIL] deliberately rejected input")
            ns["check"]("real assertion", True)
        self.assertIn("[EXPECTED-FAIL]", output.getvalue())
        self.assertEqual(self.result(output.getvalue()).status, "PASS")
        with contextlib.redirect_stdout(output):
            ns["check"]("real assertion broke", False)
        self.assertEqual(ns["FAILED"], 1)
        self.assertEqual(self.result(output.getvalue()).status, "FAIL")

    def test_missing_dependency_is_incomplete(self):
        self.assertEqual(self.result("ModuleNotFoundError: No module named 'PySide6'", 1).status, "INCOMPLETE")
        self.assertEqual(self.result("ImportError: invalid symbol", 1).status, "FAIL")

    def test_prose_and_mock_verdicts_are_not_hardware_proof(self):
        for text in ("mock returned PASS", '{"verdict": "PASS"}', "SKIP_WORD is a constant"):
            with self.subTest(text=text):
                self.assertEqual(self.result(text).status, "INCOMPLETE")
        for text in ("[PASS] mock failure correctly rejected", "[ OK ] fake transport counted"):
            self.assertEqual(self.result(text).status, "PASS")
        for entry in registry.discover(ROOT):
            self.assertIn("not board execution" if entry.category == "firmware" else "no physical", entry.evidence)

    def test_named_legacy_contracts_require_nonzero_complete_summary(self):
        for name, output, count in (
            ("tests/backend_protocol_test.py", r"\nALL PASSED (198 checks)", 198),
            ("tests/referee_integration_test.py", "ALL PASSED (230 checks; localhost only; NOT field qualification)", 230),
        ):
            with self.subTest(name=name):
                options = {"completion_pattern": registry.COMPLETION_PATTERNS[name],
                           "check_count_pattern": registry.CHECK_COUNT_PATTERNS[name]}
                result = verify.classify_output(0, output, **options)
                self.assertEqual((result.status, result.checks_passed), ("PASS", count))
                self.assertEqual(verify.classify_output(0, output.replace(str(count), "0"), **options).status, "INCOMPLETE")
                self.assertEqual(verify.classify_output(0, "[PASS] earlier assertion", **options).status, "INCOMPLETE")
        options = {"completion_pattern": registry.COMPLETION_PATTERNS["tests/backend_protocol_test.py"],
                   "check_count_pattern": registry.CHECK_COUNT_PATTERNS["tests/backend_protocol_test.py"]}
        result = verify.classify_output(77, r"\nINCOMPLETE (198 checks passed; 1 dependency-blocked tests skipped)", **options)
        self.assertEqual((result.status, result.checks_passed), ("INCOMPLETE", 198))

    def test_legacy_no_counter_requires_declared_completion(self):
        marker = "GPU renderer runtime smoke passed"
        self.assertEqual(self.result(marker).status, "INCOMPLETE")
        self.assertEqual(self.result(marker, completion="^" + marker + "$").status, "PASS")
        self.assertEqual(self.result("not yet: " + marker, completion="^" + marker + "$").status, "INCOMPLETE")


class RunnerTests(unittest.TestCase):
    def run_fixture(self, source, timeout=3):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "tests/test_fixture.py"
            script.parent.mkdir()
            script.write_text(source)
            logs = root / "logs"
            with mock.patch.object(verify, "ROOT", root):
                result = verify.run_entry(registry.TestEntry("tests/test_fixture.py"), firmware=None,
                                          timeout=timeout, desktop=False, release=False, log_dir=logs)
            stdout = (logs / "tests__test_fixture.stdout.log").read_text()
            stderr = (logs / "tests__test_fixture.stderr.log").read_text()
        return result, stdout, stderr

    def test_real_subprocess_distinguishes_pass_partial_skip_and_zero(self):
        prelude = "import unittest\nclass Example(unittest.TestCase):\n"
        fixtures = ((prelude + " def test_ok(self): self.assertTrue(True)\nunittest.main()", "PASS"),
                    (prelude + " def test_ok(self): pass\n @unittest.skip('fixture missing')\n def test_skip(self): pass\nunittest.main()", "INCOMPLETE"),
                    ("import unittest\nunittest.main()", "INCOMPLETE"))
        for source, expected in fixtures:
            with self.subTest(expected=expected):
                result, stdout, stderr = self.run_fixture(source)
                self.assertEqual(result.status, expected, stdout + stderr)

    def test_real_unittest_expected_failure_cannot_make_the_suite_pass(self):
        source = (
            "import unittest\nclass KnownDefect(unittest.TestCase):\n"
            " @unittest.expectedFailure\n"
            " def test_still_broken(self): self.assertEqual(1, 2)\n"
            "unittest.main()\n"
        )
        result, stdout, stderr = self.run_fixture(source)
        self.assertEqual((result.status, result.tests_run, result.expected_failures), ("INCOMPLETE", 1, 1), stdout + stderr)
        self.assertEqual(result.returncode, 0)

    def test_script_directory_imports_work(self):
        result, _, _ = self.run_fixture("from pathlib import Path\nimport sys\nassert sys.path[0] == str(Path(__file__).parent)\nprint('[PASS] direct-script path preserved')")
        self.assertEqual(result.status, "PASS")

    def test_timeout_is_incomplete_and_keeps_diagnostics(self):
        result, stdout, _ = self.run_fixture("import time\nprint('starting fixture', flush=True)\ntime.sleep(60)", .2)
        self.assertEqual(result.status, "INCOMPLETE")
        self.assertIn("timeout", result.detail)
        self.assertIn("starting fixture", stdout)
        self.assertLess(result.duration_s, 3)

    def test_desktop_opt_in_cannot_be_satisfied_by_offscreen(self):
        entry = registry.TestEntry("tests/gpu_renderer_runtime_smoke.py", "desktop")
        with mock.patch.object(verify.subprocess, "Popen") as popen:
            result = verify.run_entry(entry, firmware=None, timeout=1, desktop=False, release=True, log_dir=None)
            self.assertEqual(result.status, "SKIP")
            popen.assert_not_called()
        if sys.platform != "win32":
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(verify.subprocess, "Popen") as popen:
                result = verify.run_entry(entry, firmware=None, timeout=1, desktop=True, release=True, log_dir=None)
                self.assertEqual(result.status, "SKIP")
                popen.assert_not_called()

    def test_env_binds_exact_source_and_firmware_and_release_video_requirement(self):
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/wrong/tree", "QT_QPA_PLATFORM": "offscreen"}):
            env = verify.child_environment("/isolated", Path("/chosen"), release=True)
        self.assertEqual(env["PYTHONPATH"], str(ROOT))
        self.assertEqual(env["POLAR_FIRMWARE_ROOT"], str(Path("/chosen")))
        self.assertEqual(env["POLAR_REFEREE_REFERENCE"], str(Path("/chosen")))
        self.assertEqual(env["POLAR_FIRMWARE_READ_ONLY"], "1")
        self.assertEqual(env["POLAR_REQUIRE_VIDEO_FIXTURE"], "1")
        self.assertEqual(env["POLAR_USER_DATA_ROOT"], "/isolated")

    def test_inherited_portable_root_cannot_escape_the_owned_user_data(self):
        from rm_client.runtime_paths import RuntimePaths

        inherited = {"POLAR_APP_ROOT": "/other/portable-install", "POLAR_REFEREE_REFERENCE": "/unselected/firmware",
                     "XDG_CACHE_HOME": "/other/cache", "XDG_CONFIG_HOME": "/other/config", "XDG_DATA_HOME": "/other/data"}
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, inherited):
            environment = verify.child_environment(directory, None)
            paths = RuntimePaths.discover(environ=environment)
            self.assertTrue(paths.source_mode)
            self.assertEqual(paths.app_root, ROOT)
            self.assertEqual(paths.user_data_root, Path(directory))
            self.assertNotIn("POLAR_APP_ROOT", environment)
            self.assertNotIn("POLAR_REFEREE_REFERENCE", environment)
            for variable in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
                self.assertEqual(Path(environment[variable]).parent, Path(directory))
            self.assertEqual({key: os.environ[key] for key in inherited}, inherited)

    def test_actual_children_have_separate_writable_cache_and_config_roots(self):
        source = (
            "import os\nfrom pathlib import Path\n"
            "assert 'POLAR_APP_ROOT' not in os.environ\n"
            "assert 'POLAR_REFEREE_REFERENCE' not in os.environ\n"
            "root = Path(os.environ['POLAR_USER_DATA_ROOT'])\n"
            "for name in ('XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):\n"
            " path = Path(os.environ[name])\n"
            " assert path.parent == root\n"
            " assert not (path / 'previous-test').exists()\n"
            " path.mkdir(parents=True, exist_ok=True)\n"
            " (path / 'previous-test').write_text('owned')\n"
            "print('[PASS] isolated writable paths')\n"
        )
        inherited = {"POLAR_APP_ROOT": "/unselected/app", "POLAR_REFEREE_REFERENCE": "/unselected/firmware"}
        with mock.patch.dict(os.environ, inherited):
            for _ in range(2):
                result, stdout, stderr = self.run_fixture(source)
                self.assertEqual(result.status, "PASS", stdout + stderr)

    def test_inherited_autostart_and_debug_data_root_are_not_test_inputs(self):
        source = (
            "import os\n"
            "assert os.environ['POLAR_AUTOSTART'] == '0'\n"
            "assert 'POLAR_STARTUP_USER_DATA_ROOT' not in os.environ\n"
            "print('[PASS] test owns activation and startup data')\n"
        )
        inherited = {"POLAR_AUTOSTART": "true", "POLAR_STARTUP_USER_DATA_ROOT": "/other/operator-data"}
        with mock.patch.dict(os.environ, inherited):
            result, stdout, stderr = self.run_fixture(source)
            self.assertEqual(result.status, "PASS", stdout + stderr)
            self.assertEqual({key: os.environ[key] for key in inherited}, inherited)

    def test_all_runner_children_opt_out_of_telemetry_before_optional_imports(self):
        original = {"CI": "false", "POLAR_DISABLE_LOCAL_CAPTURE": "0", "POLAR_DISABLE_MEDIA_PRELOAD": "0"}
        for desktop in (False, True):
            for release in (False, True):
                with self.subTest(desktop=desktop, release=release), mock.patch.dict(os.environ, original):
                    env = verify.child_environment("/isolated", None, desktop=desktop, release=release)
                    self.assertEqual(env["CI"], "true")
                    self.assertEqual(env["POLAR_DISABLE_LOCAL_CAPTURE"], "1")
                    self.assertEqual(env["POLAR_DISABLE_MEDIA_PRELOAD"], "1")
                    self.assertEqual({key: os.environ[key] for key in original}, original)

    def test_actual_test_child_receives_optout_without_importing_optional_packages(self):
        script = (
            "import os\n"
            "assert os.environ['CI'] == 'true'\n"
            "assert os.environ['POLAR_DISABLE_LOCAL_CAPTURE'] == '1'\n"
            "assert os.environ['POLAR_DISABLE_MEDIA_PRELOAD'] == '1'\n"
            "print('[PASS] opt-out environment is present before optional imports')\n"
        )
        with mock.patch.dict(os.environ, {"CI": "false"}):
            result, stdout, _ = self.run_fixture(script)
        self.assertEqual(result.status, "PASS", stdout)

    def test_timeout_cleanup_still_kills_owned_process_if_tree_cleanup_fails(self):
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with mock.patch.object(verify.os, "name", "nt"), \
             mock.patch.object(verify.subprocess, "run", side_effect=OSError("taskkill unavailable")):
            with self.assertRaises(OSError):
                verify.stop_process_tree(process)
        process.kill.assert_called_once()

    def test_selected_partial_results_fail_aggregate_and_report_all_statuses(self):
        entries = tuple(registry.TestEntry(f"tests/test_{name}.py") for name in ("pass", "skip", "incomplete", "fail"))
        results = [verify.CheckResult(status, "fixture") for status in ("PASS", "SKIP", "INCOMPLETE", "FAIL")]
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(verify, "discover", return_value=entries), \
             mock.patch.object(verify, "acquire_lock", return_value=True), \
             mock.patch.object(verify, "run_entry", side_effect=results), \
             contextlib.redirect_stdout(io.StringIO()):
            path = Path(temp) / "result.json"
            self.assertEqual(verify.main(["--category", "offline", "--json", str(path)]), 1)
            report = json.loads(path.read_text())
        self.assertFalse(report["passed"])
        self.assertEqual(report["counts"], {name: 1 for name in ("PASS", "SKIP", "INCOMPLETE", "FAIL")})

    def test_release_script_uses_registry_and_keeps_native_preflight(self):
        script = (ROOT / "tools/build_release.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"tools\\verify_all.py") --profile release --desktop', script)
        self.assertNotIn("Invoke-RequiredTest", script)
        self.assertIn("_qt_opengl_context", script)
        self.assertIn("Invoke-PortablePreflight", script)
        self.assertIn('$testReport.passed -ne $true', script)
        self.assertIn('registry_results = $testReport.results', script)
        self.assertNotIn('gpu_renderer_runtime = "PASS"', script)


class ReportPublicationTests(unittest.TestCase):
    def prior_report(self, directory):
        path = Path(directory) / "report.json"
        path.write_text(json.dumps({"passed": True, "run_id": "previous-run", "results": [{"status": "PASS"}]}))
        return path

    def assert_incomplete_attempt(self, path, state):
        report = json.loads(path.read_text())
        self.assertFalse(report["passed"])
        self.assertEqual(report["run_state"], state)
        self.assertRegex(report["run_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(report["counts"], {"PASS": 0, "SKIP": 0, "INCOMPLETE": 1, "FAIL": 0})
        self.assertEqual(report["results"][0]["status"], "INCOMPLETE")
        return report

    def test_lock_rejection_replaces_an_old_pass_with_current_incomplete_evidence(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(verify, "acquire_lock", return_value=False), \
             mock.patch.object(verify, "run_entry") as run, \
             contextlib.redirect_stderr(io.StringIO()):
            path = self.prior_report(directory)
            self.assertEqual(verify.main(["--test", "test_test_registry", "--json", str(path)]), 2)
            report = self.assert_incomplete_attempt(path, "blocked")
        self.assertIn("Another verifier", report["results"][0]["detail"])
        run.assert_not_called()

    def test_invalid_firmware_replaces_an_old_pass_before_test_execution(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(verify, "firmware_identity", side_effect=ValueError("invalid fixture")), \
             mock.patch.object(verify, "acquire_lock") as acquire, \
             mock.patch.object(verify, "run_entry") as run, \
             contextlib.redirect_stdout(io.StringIO()):
            path = self.prior_report(directory)
            self.assertEqual(verify.main(["--test", "test_test_registry", "--firmware", directory, "--json", str(path)]), 2)
            report = self.assert_incomplete_attempt(path, "blocked")
        self.assertIn("Invalid firmware", report["results"][0]["detail"])
        acquire.assert_not_called()
        run.assert_not_called()

    def test_interrupted_execution_keeps_current_nonpassing_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(verify, "acquire_lock", return_value=True), \
             mock.patch.object(verify, "run_entry", side_effect=RuntimeError("interrupted fixture")), \
             contextlib.redirect_stdout(io.StringIO()):
            path = self.prior_report(directory)
            with self.assertRaisesRegex(RuntimeError, "interrupted fixture"):
                verify.main(["--test", "test_test_registry", "--json", str(path)])
            report = self.assert_incomplete_attempt(path, "running")
        self.assertIn("has not completed", report["results"][0]["detail"])

    def test_invalid_invocation_does_not_claim_to_have_started_a_new_report(self):
        for arguments in (("--test", "nonexistent-suite"), ("--timeout", "0")):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory, \
                 mock.patch.object(verify, "run_entry") as run, \
                 contextlib.redirect_stderr(io.StringIO()):
                path = self.prior_report(directory)
                previous = path.read_bytes()
                with self.assertRaises(SystemExit) as rejected:
                    verify.main([*arguments, "--json", str(path)])
                self.assertEqual(rejected.exception.code, 2)
                self.assertEqual(path.read_bytes(), previous)
                run.assert_not_called()

    def test_atomic_publish_failure_preserves_complete_previous_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.prior_report(directory)
            previous = path.read_bytes()
            with mock.patch.object(verify.os, "replace", side_effect=OSError("replace denied")):
                with self.assertRaisesRegex(OSError, "replace denied"):
                    verify.write_json(path, {"passed": False})
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            verify.write_json(path, {"passed": False})
            self.assertEqual(json.loads(path.read_text()), {"passed": False})
            self.assertEqual(list(Path(directory).iterdir()), [path])


class SmokeSelectionTests(unittest.TestCase):
    def load_smoke_runner(self):
        path = ROOT / "tests/smoke_test.py"
        tree = ast.parse(path.read_text())
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_all_smoke_tests")
        namespace = {"__name__": "isolated_smoke", "unittest": unittest, "SMOKE_SKIPS": []}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
        return namespace

    def test_smoke_counts_skips_and_does_not_claim_complete(self):
        ns = self.load_smoke_runner()
        exec("def test_ok(): pass\ndef test_missing(): raise unittest.SkipTest('absent')", ns)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(ns["run_all_smoke_tests"](), 77)
        self.assertIn("[PASS] test_ok", output.getvalue())
        self.assertIn("[SKIP] test_missing", output.getvalue())
        self.assertNotIn("[PASS] test_missing", output.getvalue())

    def test_smoke_empty_and_partial_subcheck_cannot_pass(self):
        ns = self.load_smoke_runner()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ns["run_all_smoke_tests"](), 77)
        exec("def test_partial(): SMOKE_SKIPS.append('missing subcheck')", ns)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(ns["run_all_smoke_tests"](), 77)
        self.assertIn("[INCOMPLETE] test_partial", output.getvalue())
        self.assertNotIn("[PASS] test_partial", output.getvalue())

    def test_external_inference_has_explicit_inventory_and_no_silent_smoke_return(self):
        smoke = (ROOT / "tests/smoke_test.py").read_text()
        self.assertNotIn("POLAR_SKIP_EXTERNAL_INTEGRATION", smoke)
        self.assertNotIn("def test_tongji_local_yolo_demo", smoke)
        external = next(entry for entry in registry.discover(ROOT) if entry.path == "tests/tongji_external_integration_test.py")
        self.assertEqual(external.category, "external")
        with mock.patch.dict(os.environ, {}, clear=True):
            result = verify.run_entry(external, firmware=None, timeout=3, desktop=False, release=False, log_dir=None)
        self.assertEqual(result.status, "SKIP")


if __name__ == "__main__":
    unittest.main(verbosity=2)
