"""Negative and portability tests for the explicit window compatibility surface.

No Qt application is created. Real-window construction and a removal/restoration
mutation are separately exercised by api_freeze_test.py --mutation-selftest.
"""
from __future__ import annotations

import ast
import contextlib
from copy import deepcopy
from enum import Enum, IntFlag
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.quality import window_contract
from tools import test_registry

SPEC = importlib.util.spec_from_file_location("api_contract_subject", ROOT / "tests/api_freeze_test.py")
assert SPEC is not None and SPEC.loader is not None
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


class WindowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = json.loads(api.DEFAULT_BASELINE.read_text(encoding="utf-8"))
        cls.contract = window_contract.load_contract(api.DEFAULT_CONTRACT, cls.baseline)

    def current(self):
        current = deepcopy(self.baseline)
        for name, signature in self.contract["python_signatures"].items():
            current["members"][name]["call_signature"] = signature
        return current

    def compare(self, current):
        return api.compare_contract(self.baseline, current, self.contract)

    def test_real_contract_covers_every_protected_python_method(self):
        names = window_contract.protected_names(self.baseline, self.contract)
        expected = {name for name in names if self.baseline["members"][name]["kind"]
                    in window_contract.PYTHON_CALLABLE_KINDS}
        self.assertEqual(set(self.contract["python_signatures"]), expected)
        self.assertTrue(expected)
        self.assertTrue(api.is_clean(self.compare(self.current())))

    def test_private_seams_have_real_independent_caller_evidence(self):
        names_by_path = {}
        for name, sources in self.contract["private_members"].items():
            with self.subTest(name=name):
                self.assertTrue(name.startswith("_"))
                for source in sources:
                    path, line = source.rsplit(":", 1)
                    self.assertTrue(int(line) > 0)
                    self.assertTrue(path.startswith(("tests/", "tools/")))
                    self.assertNotIn("api_freeze", path)
                    if path not in names_by_path:
                        tree = ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))
                        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
                        names.update(node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                                     and isinstance(node.value, str))
                        names_by_path[path] = names
                    self.assertIn(name, names_by_path[path], source)

    def test_only_reviewed_owner_storage_retires_and_public_protection_is_unchanged(self):
        retired = self.contract["retired_private_members"]
        self.assertEqual(set(retired), {"_field_frame_times_ms", "_last_field_frame_log_at"})
        protected = window_contract.protected_names(self.baseline, self.contract)
        public = {name for name in self.baseline["members"] if not name.startswith("_")}
        self.assertEqual({name for name in protected if not name.startswith("_")}, public)
        self.assertTrue({"_last_present_time", "_ui_tick_stages"} <= protected)
        for name, evidence in retired.items():
            with self.subTest(name=name):
                self.assertNotIn(name, protected)
                self.assertIn(name, self.baseline["members"])
                self.assertTrue(evidence["previous_caller_evidence"])
                for path in evidence["behavior_tests"]:
                    self.assertTrue((ROOT / path).is_file(), path)

    def test_retired_storage_removal_is_still_visible_in_the_historic_audit(self):
        current = self.current()
        for name in self.contract["retired_private_members"]:
            del current["members"][name]
        self.assertTrue(api.is_clean(self.compare(current)))
        legacy = api.diff_snapshots(self.baseline, current)
        self.assertEqual(set(legacy["missing"]), set(self.contract["retired_private_members"]))
        self.assertFalse(api.is_clean(legacy))

    def test_retained_capture_clock_and_tick_diagnostics_still_reject_drift(self):
        for name in ("_last_present_time", "_ui_tick_stages"):
            with self.subTest(name=name, mutation="removed"):
                current = self.current()
                del current["members"][name]
                self.assertIn(name, self.compare(current)["missing"])
        # The historic capture clock is volatile; its presence is protected,
        # not its changing value/type. The diagnostics mapping is stable.
        self.assertTrue(self.baseline["members"]["_last_present_time"]["volatile"])
        current = self.current()
        current["members"]["_ui_tick_stages"]["type"] = "invalid fixture type"
        self.assertFalse(api.is_clean(self.compare(current)))

    def test_private_retirement_requires_review_and_cannot_exempt_public_or_active_seams(self):
        template = self.contract["retired_private_members"]["_field_frame_times_ms"]
        mutations = (
            ("public", {"draw_watermark": template}),
            ("active", {"_last_present_time": template}),
            ("unknown", {"_invented_private": template}),
            ("unexplained", {"_field_frame_times_ms": {**template, "reason": ""}}),
            ("missing behavior", {"_field_frame_times_ms": {**template, "behavior_tests": []}}),
            ("missing prior evidence", {"_field_frame_times_ms": {**template, "previous_caller_evidence": []}}),
            ("invalid date", {"_field_frame_times_ms": {**template, "retired_at": "unknown"}}),
        )
        for label, retirements in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                contract = {**self.contract, "retired_private_members": retirements}
                path = Path(directory) / "contract.json"
                path.write_text(json.dumps(contract), encoding="utf-8")
                with self.assertRaises(ValueError):
                    window_contract.load_contract(path, self.baseline)

    def test_removed_public_callable_fails(self):
        current = self.current()
        del current["members"]["draw_watermark"]
        result = self.compare(current)
        self.assertEqual(result["missing"], ["draw_watermark"])
        self.assertFalse(api.is_clean(result))

    def test_public_call_signature_change_fails(self):
        current = self.current()
        current["members"]["on_tick"]["call_signature"] = "(required_new_argument)"
        result = self.compare(current)
        self.assertEqual(result["sig_changed"], [("on_tick", "()", "(required_new_argument)")])
        self.assertFalse(api.is_clean(result))

    def test_missing_signature_evidence_cannot_fall_back_to_qualname(self):
        current = self.current()
        del current["members"]["on_tick"]["call_signature"]
        self.assertFalse(api.is_clean(self.compare(current)))

    def test_provider_move_is_compatible_but_still_visible_in_legacy_audit(self):
        current = self.current()
        current["members"]["on_tick"]["sig"] = "TickAdapter.on_tick"
        self.assertTrue(api.is_clean(self.compare(current)))
        self.assertFalse(api.is_clean(api.diff_snapshots(self.baseline, current)))

    def test_protected_private_method_removal_fails(self):
        current = self.current()
        del current["members"]["_run_tick_stage"]
        self.assertIn("_run_tick_stage", self.compare(current)["missing"])

    def test_protected_private_field_removal_fails(self):
        current = self.current()
        del current["members"]["_ui_tick_stages"]
        self.assertIn("_ui_tick_stages", self.compare(current)["missing"])

    def test_protected_field_type_change_fails(self):
        current = self.current()
        current["members"]["_ui_tick_stages"]["type"] = "str"
        self.assertFalse(api.is_clean(self.compare(current)))

    def test_unreferenced_private_storage_can_change_without_becoming_a_contract(self):
        unprotected = set(self.baseline["members"]) - window_contract.protected_names(self.baseline, self.contract)
        self.assertTrue(unprotected)
        current = self.current()
        for name in unprotected:
            del current["members"][name]
        current["members"]["_new_tick_service"] = {"kind": "plain-attribute", "type": "TickService", "sig": "TickService"}
        self.assertTrue(api.is_clean(self.compare(current)))
        self.assertFalse(api.is_clean(api.diff_snapshots(self.baseline, current)))

    def test_additive_public_member_does_not_break_existing_callers(self):
        current = self.current()
        current["members"]["new_diagnostic"] = {"kind": "plain-attribute", "type": "int", "sig": "1"}
        self.assertTrue(api.is_clean(self.compare(current)))

    def test_public_default_value_change_still_fails(self):
        current = self.current()
        self.assertFalse(current["members"]["gui_visible"].get("volatile"))
        current["members"]["gui_visible"]["sig"] = "incompatible default"
        self.assertFalse(api.is_clean(self.compare(current)))

    def test_callable_kind_change_still_fails(self):
        current = self.current()
        current["members"]["on_tick"]["kind"] = "plain-attribute"
        self.assertFalse(api.is_clean(self.compare(current)))

    def test_current_volatile_marker_cannot_exempt_a_changed_public_field(self):
        current = self.current()
        current["members"]["gui_visible"].update(volatile=True, sig="*volatile*", type="int")
        self.assertFalse(api.is_clean(self.compare(current)))

    def test_module_export_removal_is_still_a_failure(self):
        current = self.current()
        del current["module_symbols"]["PolarRMWindow"]
        self.assertEqual(self.compare(current)["module_missing"], ["PolarRMWindow"])

    def test_known_qt_and_workspace_portability_remain_narrow(self):
        current = self.current()
        for name in api.QT_OPTIONAL_MEMBERS:
            del current["members"][name]
        current["members"]["workspace"] = {"kind": "plain-attribute", "type": "PosixPath", "sig": "PosixPath"}
        self.assertTrue(api.is_clean(self.compare(current)))
        del current["members"]["accessibleName"]
        self.assertFalse(api.is_clean(self.compare(current)))

    def test_projection_does_not_rewrite_either_snapshot_or_manifest(self):
        current = self.current()
        before = deepcopy((self.baseline, current, self.contract))
        self.compare(current)
        self.assertEqual((self.baseline, current, self.contract), before)

    def test_refreshing_historic_baseline_cannot_silently_approve_drift(self):
        changed = deepcopy(self.baseline)
        changed["members"]["gui_visible"]["sig"] = "new default"
        with self.assertRaisesRegex(ValueError, "Historic API snapshot changed"):
            window_contract.load_contract(api.DEFAULT_CONTRACT, changed)

    def test_manifest_missing_signature_is_rejected(self):
        contract = deepcopy(self.contract)
        del contract["python_signatures"]["on_tick"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "signature coverage differs"):
                window_contract.load_contract(path, self.baseline)

    def test_manifest_cannot_silently_add_unknown_private_exemption(self):
        contract = deepcopy(self.contract)
        contract["private_members"]["_invented"] = ["tests/smoke_test.py:1"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid private compatibility member"):
                window_contract.load_contract(path, self.baseline)


class CallableSignatureTests(unittest.TestCase):
    def test_annotations_and_defining_class_do_not_change_call_contract(self):
        class Before:
            def perform(self, value: int, *, enabled: bool = False) -> None:
                pass

        class Extracted:
            def perform(self, value: "str", *, enabled=False):
                pass

        self.assertEqual(window_contract.callable_signature(Before().perform), "(value, *, enabled=False)")
        self.assertEqual(window_contract.callable_signature(Before().perform),
                         window_contract.callable_signature(Extracted().perform))

    def test_parameter_names_defaults_and_kinds_are_preserved(self):
        def old(value, /, optional=2, *, enabled=False):
            pass

        def changed(value, optional=3, enabled=False):
            pass

        self.assertEqual(window_contract.callable_signature(old), "(value, /, optional=2, *, enabled=False)")
        self.assertNotEqual(window_contract.callable_signature(old), window_contract.callable_signature(changed))

    def test_enum_default_uses_stable_value_instead_of_qt_repr(self):
        class Flags(IntFlag):
            LEFT = 1
            CENTER = 128

        def draw(flags=Flags.LEFT | Flags.CENTER):
            pass

        expected = f"(flags=<{Flags.__module__}.{Flags.__qualname__}: 129>)"
        self.assertEqual(window_contract.callable_signature(draw), expected)

    def test_enum_to_scalar_default_change_is_a_real_incompatibility(self):
        class Mode(Enum):
            SAFE = "safe"

        def original(mode=Mode.SAFE):
            return mode.name

        def changed(mode="safe"):
            return mode.name

        self.assertEqual(original(), "SAFE")
        with self.assertRaises(AttributeError):
            changed()
        self.assertNotEqual(window_contract.callable_signature(original),
                            window_contract.callable_signature(changed))

    def test_distinct_enum_types_with_equal_values_have_distinct_signatures(self):
        First = Enum("Mode", {"SAFE": "safe"}, module="first")
        Second = Enum("Mode", {"SAFE": "safe"}, module="second")

        def original(mode=First.SAFE):
            pass

        def changed(mode=Second.SAFE):
            pass

        self.assertNotEqual(window_contract.callable_signature(original),
                            window_contract.callable_signature(changed))

    def test_attribute_is_not_misreported_as_callable_signature(self):
        self.assertIsNone(window_contract.callable_signature(12))
        self.assertIsNone(window_contract.callable_signature(object()))

    def test_contract_capture_does_not_evaluate_new_private_properties(self):
        touched = []

        class Host:
            def on_tick(self):
                pass

            @property
            def _new_implementation_detail(self):
                touched.append(True)
                raise RuntimeError("private property must not be evaluated")

        snapshot = api.collect_window_snapshot(Host(), {"on_tick"})
        self.assertEqual(set(snapshot), {"on_tick"})
        self.assertEqual(touched, [])

    def test_requested_missing_member_is_omitted_for_removal_detection(self):
        self.assertEqual(api.collect_window_snapshot(object(), {"missing"}), {})

    def test_member_capture_includes_the_real_bound_signature(self):
        class Host:
            def perform(self, value, *, enabled=False):
                pass

        entry = api._member_entry(Host(), "perform")
        self.assertEqual(entry["call_signature"], "(value, *, enabled=False)")


class ContractCommandTests(unittest.TestCase):
    def test_default_command_uses_contract_and_legacy_option_does_not(self):
        with mock.patch.object(api, "run_compare", return_value=0) as compare:
            self.assertEqual(api.main([]), 0)
            compare.assert_called_once_with(api.DEFAULT_BASELINE, api.DEFAULT_CONTRACT)
        with mock.patch.object(api, "run_compare", return_value=2) as compare:
            self.assertEqual(api.main(["--legacy-snapshot"]), 2)
            compare.assert_called_once_with(api.DEFAULT_BASELINE, None)

    def test_update_cannot_overwrite_original_evidence_by_default(self):
        for arguments in (["--update"], ["--update", "--legacy-snapshot"]):
            with self.subTest(arguments=arguments), mock.patch.object(api, "run_update") as update:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                    api.main(arguments)
                self.assertEqual(result.exception.code, 2)
                update.assert_not_called()

    def test_runtime_mutation_restores_class_even_when_collection_raises(self):
        class Host:
            def draw_watermark(self):
                pass

            def close(self):
                pass

        original = Host.draw_watermark
        app, window = object(), Host()
        with mock.patch.object(api, "_build_window", return_value=(app, window)), \
             mock.patch.object(api, "collect_module_symbols", return_value={}), \
             mock.patch.object(api, "collect_window_snapshot", side_effect=RuntimeError("fixture failure")), \
             mock.patch.object(api, "close_window_safely") as close:
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                api.run_mutation_selftest(api.DEFAULT_BASELINE)
        self.assertIs(Host.draw_watermark, original)
        close.assert_called_once_with(window, app)

    def test_compare_failure_still_settles_window_shutdown(self):
        app, window = object(), object()
        with mock.patch.object(api, "_build_window", return_value=(app, window)), \
             mock.patch.object(api, "collect_comparison_snapshot", side_effect=RuntimeError("capture failed")), \
             mock.patch.object(api, "close_window_safely") as close:
            with self.assertRaisesRegex(RuntimeError, "capture failed"):
                api.run_compare(api.DEFAULT_BASELINE)
        close.assert_called_once_with(window, app)

    def test_compare_cannot_pass_when_native_cleanup_is_unconfirmed(self):
        output = io.StringIO()
        with mock.patch.object(api, "_build_window", return_value=(object(), object())), \
             mock.patch.object(api, "collect_comparison_snapshot", return_value={}), \
             mock.patch.object(api, "probe_lazy_assignable", return_value=[]), \
             mock.patch.object(api, "close_window_safely", side_effect=AssertionError("cleanup unconfirmed")), \
             contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(AssertionError, "cleanup unconfirmed"):
                api.run_compare(api.DEFAULT_BASELINE)
        self.assertNotIn("PASS", output.getvalue())

    def test_update_does_not_write_a_snapshot_after_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "candidate.json"
            with mock.patch.object(api, "collect_baseline_snapshot", return_value={}), \
                 mock.patch.object(api, "_build_window", return_value=(object(), object())), \
                 mock.patch.object(api, "probe_lazy_assignable", return_value=[]), \
                 mock.patch.object(api, "close_window_safely", side_effect=AssertionError("cleanup unconfirmed")):
                with self.assertRaisesRegex(AssertionError, "cleanup unconfirmed"):
                    api.run_update(destination)
            self.assertFalse(destination.exists())

    def test_legacy_capture_waits_for_shutdown_before_returning_evidence(self):
        app, window = mock.Mock(), object()
        with mock.patch.object(api, "_build_window", return_value=(app, window)), \
             mock.patch.object(api, "collect_window_snapshot", return_value={}), \
             mock.patch.object(api, "_quiesce"), \
             mock.patch.object(api.time, "sleep"), \
             mock.patch.object(api, "close_window_safely", side_effect=AssertionError("cleanup unconfirmed")) as close:
            with self.assertRaisesRegex(AssertionError, "cleanup unconfirmed"):
                api.collect_baseline_snapshot()
        close.assert_called_once_with(window, app)

    def test_new_contract_suite_is_not_omitted_by_runner(self):
        selected = test_registry.select(test_registry.discover(ROOT), names=("test_window_api_contract",))
        self.assertEqual([entry.path for entry in selected], ["tests/test_window_api_contract.py"])


if __name__ == "__main__":
    unittest.main()
