"""Executable evidence that architecture checks measure and fail honestly.

These tests use tiny source trees and the standard library. Fixtures never
import application modules, initialize Qt, probe devices, or use the network.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import quality_gate, test_registry
from tools.quality import gates, metrics


def empty_baseline():
    return {"schema_version": 2, "source_revision": "fixture", "rationale": "test fixture",
            "lint_debt": {}, "runtime_cycles": [], "all_import_cycles": [], "function_budgets": {}}


class SourceFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def source(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def report(self):
        return metrics.measure_tree(self.root)

    def violations(self):
        return gates.check_report(self.report(), empty_baseline())


class FunctionMeasurementTests(unittest.TestCase):
    def measure(self, source):
        function = ast.parse(textwrap.dedent(source)).body[0]
        return metrics.function_metrics(function)

    def test_literal_span_is_not_executable_complexity(self):
        values = self.measure('''\
            def shader():
                """Useful documentation."""
                text = """line one
                line two
                line three
                line four"""
                return text
        ''')
        self.assertEqual(values["statements"], 2)
        self.assertEqual(values["decisions"], 0)
        self.assertEqual(values["max_control_depth"], 0)
        self.assertEqual(values["source_span"], 7)

    def test_nested_function_body_is_not_double_counted(self):
        values = self.measure('''
            def outer(self):
                def helper():
                    self.private = 1
                    if self.state.hidden:
                        return 4
                return helper()
        ''')
        self.assertEqual(values["statements"], 2)
        self.assertEqual(values["decisions"], 0)
        self.assertEqual(values["self_members"], [])
        self.assertEqual(values["state_reads"], [])

    def test_elif_chain_has_one_control_level(self):
        values = self.measure('''
            def select(value):
                if value == 1:
                    return 1
                elif value == 2:
                    return 2
                elif value == 3:
                    return 3
                return 0
        ''')
        self.assertEqual(values["decisions"], 3)
        self.assertEqual(values["max_control_depth"], 1)

    def test_actual_nested_control_and_boolean_decisions_are_counted(self):
        values = self.measure('''
            def consume(items):
                for item in items:
                    if item and item.ready:
                        while item.pending:
                            item.poll()
        ''')
        self.assertEqual(values["decisions"], 4)
        self.assertEqual(values["max_control_depth"], 3)

    def test_state_reads_writes_and_collaborator_names_are_explicit(self):
        values = self.measure('''
            def apply(self, state, *, armed=False):
                self.state.hp = state.hp
                self.state.heat = self.state.heat + 1
                self.backend.publish(state.mode)
        ''')
        self.assertEqual(values["parameters"], 2)
        self.assertEqual(values["state_reads"], ["heat", "hp", "mode"])
        self.assertEqual(values["state_writes"], ["heat", "hp"])
        self.assertEqual(values["self_members"], ["backend", "state"])
        self.assertEqual(values["call_targets"], ["self.backend.publish"])

    def test_variadic_arguments_are_reported_without_arbitrary_limit(self):
        values = self.measure("def adapter(self, *args, **kwargs): return delegate(*args, **kwargs)")
        self.assertTrue(values["variadic"])
        self.assertEqual(values["parameters"], 0)


class ImportGraphTests(SourceFixture):
    def test_measurement_never_imports_source(self):
        self.source("rm_client/example.py", "raise AssertionError('must not run')")
        self.assertEqual(self.report()["summary"]["parsed_files"], 1)

    def test_relative_imports_resolve_real_modules_not_symbol_names(self):
        self.source("rm_client/pkg/__init__.py", "")
        self.source("rm_client/pkg/a.py", "from .b import Behavior\nfrom .. import state")
        self.source("rm_client/pkg/b.py", "class Behavior: pass")
        self.source("rm_client/state.py", "VALUE = 1")
        edges = self.report()["modules"]["rm_client.pkg.a"]["imports"]
        self.assertEqual({edge["target"] for edge in edges}, {"rm_client.pkg.b", "rm_client.state"})

    def test_absolute_and_package_imports_resolve(self):
        self.source("rm_client/__init__.py", "from .a import Behavior")
        self.source("rm_client/a.py", "class Behavior: pass")
        self.source("rm_client/b.py", "import rm_client.a as implementation")
        report = self.report()
        self.assertEqual(report["modules"]["rm_client"]["imports"][0]["target"], "rm_client.a")
        self.assertEqual(report["modules"]["rm_client.b"]["imports"][0]["target"], "rm_client.a")

    def test_runtime_cycle_fails_with_deterministic_members(self):
        self.source("rm_client/a.py", "from . import b")
        self.source("rm_client/b.py", "from . import a")
        report = self.report()
        self.assertEqual(report["runtime_cycles"], [["rm_client.a", "rm_client.b"]])
        self.assertTrue(any("initialization cycle" in item for item in self.violations()))

    def test_deferred_cycle_fails_full_graph_without_initialization_cycle(self):
        self.source("rm_client/a.py", "from . import b")
        self.source("rm_client/b.py", "def make():\n    from . import a\n    return a")
        report = self.report()
        self.assertEqual(report["runtime_cycles"], [])
        self.assertEqual(report["all_import_cycles"], [["rm_client.a", "rm_client.b"]])
        self.assertEqual(self.violations(), [
            "New import cycle including deferred imports: rm_client.a -> rm_client.b"
        ])

    def test_type_checking_cycle_is_not_runtime_evidence(self):
        self.source("rm_client/a.py", "from . import b")
        self.source("rm_client/b.py", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from . import a")
        report = self.report()
        self.assertEqual(report["all_import_cycles"], [])
        self.assertTrue(report["modules"]["rm_client.b"]["imports"][0]["type_only"])
        self.assertEqual(report["runtime_cycles"], [])
        self.assertEqual(self.violations(), [])

    def test_class_body_import_is_eager_and_function_body_is_deferred(self):
        self.source("rm_client/a.py", "class A:\n    from . import b\n    def create(self):\n        from . import c")
        self.source("rm_client/b.py", "")
        self.source("rm_client/c.py", "")
        edges = self.report()["modules"]["rm_client.a"]["imports"]
        self.assertEqual([(edge["target"], edge["deferred"]) for edge in edges],
                         [("rm_client.b", False), ("rm_client.c", True)])

    def test_lower_layer_cannot_import_ui_even_deferred_or_type_only(self):
        self.source("rm_client/ui/view.py", "VALUE = 1")
        for content in ("from rm_client.ui import view",
                        "def create():\n    from rm_client.ui import view",
                        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from rm_client.ui import view"):
            with self.subTest(content=content):
                self.source("rm_client/backend/service.py", content)
                self.assertTrue(any("UI dependency from lower layer" in item for item in self.violations()))

    def test_extracted_domain_services_keep_the_dependency_boundary(self):
        for module in ("module_catalog", "module_settings", "lifecycle",
                       "emergency_actions", "qualification_observations",
                       "referee_profiles", "economy_values"):
            with self.subTest(module=module):
                source = self.source(f"rm_client/{module}.py", "from rm_client.ui import polar_app")
                self.assertTrue(any("UI dependency from lower layer" in item for item in self.violations()))
                source.unlink()

    def test_pure_domain_roots_reject_backend_and_gui_imports_in_every_scope(self):
        targets = ("rm_client.backend.referee.registry", "PySide6.QtCore", "PySide2.QtCore",
                   "PyQt6.QtWidgets", "PyQt5.QtWidgets", "tkinter", "Tkinter")
        for module in ("referee_profiles", "economy_values"):
            for target in targets:
                for statement in (f"import {target}", f"from {target} import Value"):
                    for content in (statement, "def create():\n    " + statement,
                                    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + statement):
                        with self.subTest(module=module, content=content):
                            path = self.source(f"rm_client/{module}.py", content)
                            self.assertTrue(any("Forbidden pure-domain dependency" in item
                                                for item in self.violations()))
                            path.unlink()

    def test_pure_domain_rejects_forbidden_dependencies_through_local_helpers(self):
        for root in ("referee_profiles", "economy_values"):
            for target in ("rm_client.backend.transport", "PySide6.QtCore", "tkinter"):
                for statement in (f"import {target}", f"from {target} import Value"):
                    for content in (statement, "def create():\n    " + statement,
                                    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + statement):
                        with self.subTest(root=root, content=content):
                            source = self.source(f"rm_client/{root}.py", "from .helper import Value")
                            self.source("rm_client/helper.py", content)
                            violations = self.violations()
                            self.assertTrue(any("Forbidden pure-domain dependency" in item
                                                and f"rm_client.{root} -> rm_client.helper" in item
                                                for item in violations), violations)
                            source.unlink()

    def test_pure_domain_transitive_guard_includes_every_local_hop(self):
        self.source("rm_client/economy_values.py", "from .first import Value")
        self.source("rm_client/first.py", "from .second import Value")
        self.source("rm_client/second.py", "from PyQt6.QtCore import Value")
        self.assertTrue(any("rm_client.economy_values -> rm_client.first -> rm_client.second"
                            in item for item in self.violations()))

    def test_pure_domain_helper_ui_dependency_is_not_hidden(self):
        self.source("rm_client/economy_values.py", "from .helper import Value")
        self.source("rm_client/helper.py", "from rm_client.ui.future_view import Value")
        self.assertTrue(any("Forbidden pure-domain dependency" in item
                            for item in self.violations()))

    def test_pure_domain_transitive_walk_terminates_on_type_only_cycle(self):
        self.source("rm_client/economy_values.py", "from .helper import Value")
        self.source("rm_client/helper.py", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .economy_values import Value")
        self.assertEqual(self.violations(), [])

    def test_unreachable_ui_dependencies_do_not_contaminate_pure_roots(self):
        self.source("rm_client/economy_values.py", "from .helper import Value")
        self.source("rm_client/helper.py", "from dataclasses import dataclass\nValue = 1")
        self.source("rm_client/unrelated.py", "from PySide6.QtCore import QObject")
        self.assertEqual(self.violations(), [])

    def test_new_pure_owners_reject_their_reviewed_direct_and_indirect_dependencies(self):
        gui = ("PySide6.QtCore", "PySide2.QtCore", "PyQt6.QtCore", "PyQt5.QtCore",
               "tkinter", "Tkinter", "rm_client.backend.service")
        policies = {
            "rm_client.crosshair_profile": (*gui, "rm_client.ui.view", "rm_client.model",
                                           "rm_client.cs2_crosshair", "rm_client.serial_ports"),
            "rm_client.ui.preference_draft": (*gui, "rm_client.model"),
            "rm_client.ui.polar.startup_activation": (*gui, "rm_client.ui.polar_app",
                                                     "rm_client.config_store", "rm_client.configuration.store"),
        }
        for root, targets in policies.items():
            for target in targets:
                for statement in (f"import {target}", f"from {target} import Value"):
                    for content in (statement, "def use():\n    " + statement,
                                    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + statement):
                        for indirect in (False, True):
                            with self.subTest(root=root, content=content, indirect=indirect):
                                path = self.source(root.replace(".", "/") + ".py",
                                                   "from rm_client.value_support import Value" if indirect else content)
                                self.source("rm_client/value_support.py", content if indirect else "Value = 1")
                                violations = self.violations()
                                self.assertTrue(any("Forbidden pure-domain dependency" in item
                                                    for item in violations), violations)
                                if indirect:
                                    self.assertTrue(any(f"{root} -> rm_client.value_support" in item
                                                        for item in violations), violations)
                                path.unlink()

    def test_ui_hosted_pure_owners_can_use_pure_ui_siblings(self):
        self.source("rm_client/ui/value_support.py", "from dataclasses import dataclass\nValue = 1")
        for root in ("rm_client.ui.preference_draft", "rm_client.ui.polar.startup_activation"):
            self.source(root.replace(".", "/") + ".py", "from rm_client.ui.value_support import Value")
        self.assertEqual(self.violations(), [])

    def test_new_pure_owners_accept_stdlib_and_unrelated_runtime_adapters(self):
        for root in ("rm_client.crosshair_profile", "rm_client.ui.preference_draft",
                     "rm_client.ui.polar.startup_activation"):
            self.source(root.replace(".", "/") + ".py", "from dataclasses import dataclass")
        self.source("rm_client/ui/unrelated.py", "from PySide6.QtCore import QObject")
        self.assertEqual(self.violations(), [])

    def test_pure_domain_stdlib_dependencies_remain_valid(self):
        for module in ("referee_profiles", "economy_values"):
            self.source(f"rm_client/{module}.py", "from dataclasses import dataclass\nfrom typing import Any\nfrom types import MappingProxyType")
        self.assertEqual(self.violations(), [])

    def test_unresolved_ui_import_still_violates_the_dependency_boundary(self):
        self.source("rm_client/backend/service.py", "from rm_client.ui.future_view import View")
        self.assertTrue(any("UI dependency from lower layer" in item for item in self.violations()))

    def test_ui_can_depend_on_lower_layer(self):
        self.source("rm_client/backend/service.py", "VALUE = 1")
        self.source("rm_client/ui/view.py", "from rm_client.backend import service")
        self.assertEqual(self.violations(), [])


class LintRatchetTests(SourceFixture):
    def test_new_bare_except_fails_and_narrow_except_is_accepted(self):
        self.source("rm_client/a.py", "try:\n    call()\nexcept:\n    recover()")
        self.assertTrue(any("bare-except" in item for item in self.violations()))
        self.source("rm_client/a.py", "try:\n    call()\nexcept OSError:\n    recover()")
        self.assertEqual(self.violations(), [])

    def test_existing_debt_is_allowed_but_increase_fails(self):
        self.source("rm_client/a.py", "from external import *")
        baseline = empty_baseline()
        baseline["lint_debt"] = gates.lint_debt(self.report())
        self.assertEqual(gates.check_report(self.report(), baseline), [])
        self.source("rm_client/a.py", "from external import *\nfrom another import *")
        self.assertTrue(gates.check_report(self.report(), baseline))

    def test_debt_cannot_move_to_another_module_unnoticed(self):
        self.source("rm_client/a.py", "from external import *")
        baseline = empty_baseline()
        baseline["lint_debt"] = gates.lint_debt(self.report())
        self.source("rm_client/a.py", "")
        self.source("rm_client/b.py", "from external import *")
        self.assertTrue(any("rm_client/b.py" in item for item in gates.check_report(self.report(), baseline)))

    def test_semicolon_in_literal_or_comment_is_not_a_violation(self):
        self.source("rm_client/a.py", "TEXT = ';'\n# ignored; comment\ndef f():\n    return TEXT")
        self.assertEqual(self.violations(), [])

    def test_real_multistatement_and_inline_suite_fail(self):
        self.source("rm_client/a.py", "a = 1; b = 2\ndef f(): return a")
        violations = self.violations()
        self.assertTrue(any("multiple-statements" in item for item in violations))
        self.assertTrue(any("inline-suite" in item for item in violations))

    def test_ellipsis_only_interface_declaration_is_not_compressed_logic(self):
        self.source("rm_client/a.py", "from typing import Protocol\nclass Port(Protocol):\n    def paint(self) -> None: ...")
        self.assertEqual(self.violations(), [])
        self.source("rm_client/a.py", "def paint(): return 1")
        self.assertTrue(any("inline-suite" in item for item in self.violations()))

    def test_shadowed_definitions_fail_without_erasing_measurements(self):
        self.source("rm_client/a.py", "def f():\n    return 1\ndef f():\n    return 2")
        self.assertTrue(any("duplicate-definition" in item for item in self.violations()))
        functions = self.report()["modules"]["rm_client.a"]["functions"]
        self.assertEqual(len(functions), 2)

    def test_property_setters_and_type_overloads_are_not_shadowing_errors(self):
        self.source("rm_client/a.py", '''
            from typing import overload
            @overload
            def choose(value: int):
                ...
            @overload
            def choose(value: str):
                ...
            def choose(value):
                return value
            class Store:
                @property
                def value(self):
                    return self._value
                @value.setter
                def value(self, new):
                    self._value = new
        ''')
        self.assertEqual(self.violations(), [])

    def test_syntax_error_is_a_failure_not_an_empty_clean_report(self):
        self.source("rm_client/a.py", "def invalid(")
        self.assertTrue(any("Syntax error" in item for item in self.violations()))

    def test_selected_responsibility_growth_fails_and_extraction_reduction_passes(self):
        self.source("rm_client/a.py", "def tick(value):\n    if value:\n        return 1\n    return 0")
        baseline = empty_baseline()
        baseline["function_budgets"] = {"rm_client.a:tick": {"decisions": 0, "rationale": "fixture"}}
        self.assertTrue(any("Responsibility budget grew" in item for item in gates.check_report(self.report(), baseline)))
        self.source("rm_client/a.py", "def tick(value):\n    return handle(value)")
        self.assertEqual(gates.check_report(self.report(), baseline), [])


class QualityCliTests(SourceFixture):
    def run_cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = quality_gate.main(["--root", str(self.root), *args])
        return code, output.getvalue()

    def test_empty_source_tree_fails(self):
        code, output = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("No Python source files", output)
        self.assertNotIn("PASS:", output)

    def test_malformed_baseline_fails_closed(self):
        self.source("rm_client/a.py", "VALUE = 1")
        baseline = self.source("baseline.json", "{}")
        code, output = self.run_cli("--baseline", str(baseline))
        self.assertEqual(code, 2)
        self.assertIn("baseline is incomplete", output)

    def test_missing_full_graph_allowance_cannot_disable_the_guard(self):
        self.source("rm_client/a.py", "VALUE = 1")
        baseline = empty_baseline()
        baseline.pop("all_import_cycles")
        path = self.source("baseline.json", json.dumps(baseline))
        code, output = self.run_cli("--baseline", str(path))
        self.assertEqual(code, 2, output)
        self.assertIn("baseline is incomplete", output)

    def test_real_deferred_import_mutation_fails_cli_and_type_only_control_passes(self):
        self.source("rm_client/a.py", "from . import b")
        self.source("rm_client/b.py", "def make():\n    return 1")
        baseline = self.source("baseline.json", json.dumps(empty_baseline()))
        self.assertEqual(self.run_cli("--baseline", str(baseline))[0], 0)
        self.source("rm_client/b.py", "def make():\n    from . import a\n    return a")
        self.assertEqual(self.report()["runtime_cycles"], [])
        code, output = self.run_cli("--baseline", str(baseline))
        self.assertEqual(code, 1, output)
        self.assertIn("New import cycle including deferred imports", output)
        self.assertNotIn("initialization cycle:", output)
        self.source("rm_client/b.py", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from . import a")
        self.assertEqual(self.run_cli("--baseline", str(baseline))[0], 0)

    def test_indirect_pure_domain_mutation_fails_cli_without_relaxing_baseline(self):
        self.source("rm_client/economy_values.py", "from .helper import Value")
        helper = self.source("rm_client/helper.py", "Value = 1")
        baseline = self.source("baseline.json", json.dumps(empty_baseline()))
        before = baseline.read_bytes()
        self.assertEqual(self.run_cli("--baseline", str(baseline))[0], 0)
        helper.write_text("from PySide6.QtCore import Value\n", encoding="utf-8")
        code, output = self.run_cli("--baseline", str(baseline))
        self.assertEqual(code, 1, output)
        self.assertIn("Forbidden pure-domain dependency", output)
        self.assertIn("rm_client.economy_values -> rm_client.helper", output)
        self.assertNotIn("PASS:", output)
        self.assertEqual(baseline.read_bytes(), before)

    def test_invalid_budget_values_cannot_disable_the_ratchet(self):
        self.source("rm_client/a.py", "def tick():\n    return 1")
        for symbol in ("rm_client.a:tick", "rm_client.retired:old_tick"):
            for limit in (float("nan"), float("inf"), -1, 1.5, True, "1", None):
                with self.subTest(symbol=symbol, limit=limit):
                    baseline = empty_baseline()
                    baseline["function_budgets"] = {symbol: {"statements": limit, "rationale": "fixture"}}
                    path = self.source("baseline.json", json.dumps(baseline))
                    code, output = self.run_cli("--baseline", str(path))
                    self.assertEqual(code, 2, output)
                    self.assertIn("nonnegative integer", output)
                    self.assertNotIn("PASS:", output)

    def test_budget_structure_is_validated_even_for_retired_symbols(self):
        self.source("rm_client/a.py", "VALUE = 1")
        invalid = [[], {"missing_separator": {"statements": 1, "rationale": "fixture"}},
                   {"rm_client.old:tick": []}, {"rm_client.old:tick": {"statements": 1}},
                   {"rm_client.old:tick": {"rationale": "fixture"}},
                   {"rm_client.old:tick": {"line_count": 20, "rationale": "fixture"}}]
        for budgets in invalid:
            with self.subTest(budgets=budgets):
                baseline = empty_baseline()
                baseline["function_budgets"] = budgets
                path = self.source("baseline.json", json.dumps(baseline))
                code, output = self.run_cli("--baseline", str(path))
                self.assertEqual(code, 2, output)
                self.assertNotIn("PASS:", output)

    def test_invalid_cycle_and_metadata_shapes_fail_closed(self):
        self.source("rm_client/a.py", "VALUE = 1")
        for key, value in (("runtime_cycles", "rm_client.a"), ("runtime_cycles", ["rm_client.a"]),
                           ("runtime_cycles", [[None]]), ("runtime_cycles", [["a", "a"]]),
                           ("all_import_cycles", "rm_client.a"), ("all_import_cycles", ["rm_client.a"]),
                           ("all_import_cycles", [[None]]), ("all_import_cycles", [["a", "a"]]),
                           ("lint_debt", []), ("schema_version", True),
                           ("source_revision", ["fixture"]), ("rationale", " ")):
            with self.subTest(key=key, value=value):
                baseline = empty_baseline()
                baseline[key] = value
                path = self.source("baseline.json", json.dumps(baseline))
                code, output = self.run_cli("--baseline", str(path))
                self.assertEqual(code, 2, output)
                self.assertNotIn("PASS:", output)

    def test_check_outputs_reports_without_rewriting_baseline(self):
        self.source("rm_client/a.py", "def function():\n    return 1")
        baseline = self.source("baseline.json", json.dumps(empty_baseline()))
        before = baseline.read_bytes()
        report = self.root / "out" / "report.json"
        markdown = self.root / "out" / "report.md"
        code, output = self.run_cli("--baseline", str(baseline), "--json", str(report), "--markdown", str(markdown))
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(report.read_text())["gate"]["status"], "PASS")
        self.assertIn("not proof of single responsibility", markdown.read_text())
        self.assertEqual(baseline.read_bytes(), before)

    def test_expected_source_digest_checks_content_without_git_history(self):
        self.source("rm_client/a.py", "def function():\n    return 1")
        baseline = self.source("baseline.json", json.dumps(empty_baseline()))
        digest = self.report()["source_tree_sha256"]
        self.assertFalse((self.root / ".git").exists())
        code, output = self.run_cli("--baseline", str(baseline), "--expect-source-sha256", digest)
        self.assertEqual(code, 0, output)
        self.source("rm_client/a.py", "def function():\n    return 2")
        code, output = self.run_cli("--baseline", str(baseline), "--expect-source-sha256", digest)
        self.assertEqual(code, 1, output)
        self.assertIn("Measured source SHA-256 differs", output)
        self.assertNotIn("PASS:", output)

    def test_invalid_expected_source_digest_fails_closed(self):
        self.source("rm_client/a.py", "VALUE = 1")
        for digest in ("", "123", "z" * 64, "A" * 64):
            with self.subTest(digest=digest):
                code, output = self.run_cli("--expect-source-sha256", digest)
                self.assertEqual(code, 2, output)
                self.assertIn("Expected source SHA-256", output)

    def test_new_suite_is_automatically_discovered(self):
        entries = test_registry.discover(ROOT)
        selected = test_registry.select(entries, names=("test_quality_gates",))
        self.assertEqual([entry.path for entry in selected], ["tests/test_quality_gates.py"])
        self.assertEqual(selected[0].category, "offline")

    def test_real_repository_passes_the_reviewed_ratchet(self):
        report = metrics.measure_tree(ROOT)
        self.assertEqual(gates.check_report(report, gates.load_baseline(quality_gate.DEFAULT_BASELINE)), [])
        self.assertGreater(report["summary"]["functions"], 0)


if __name__ == "__main__":
    unittest.main()
