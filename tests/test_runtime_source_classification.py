"""Keep historical Tk evidence separate without crediting a file move as work."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.quality.runtime_sources import legacy_implementation_sources
from tools.quality.metrics import measure_tree
from tools import module_backend_map

QT_MAIN = '''
    def main():
        from .ui.polar_app import main as run_gpu_client
        run_gpu_client()
'''
LEGACY_CLASS = '''
    class RMClientApp:
        def draw(self):
            return "LegacyOnly"
'''
LAZY_EXPORT = '''
    from importlib import import_module
    def __getattr__(name):
        legacy = import_module(".legacy_tk_app", package=__package__)
        return getattr(legacy, name)
'''


class RuntimeSourceClassificationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.package = self.root / "rm_client"
        self.package.mkdir()

    def source(self, name, source):
        path = self.package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return path

    def test_old_mixed_app_classifies_only_after_canonical_main_is_verified(self):
        path = self.source("app.py", textwrap.dedent(LEGACY_CLASS) + textwrap.dedent(QT_MAIN))
        self.assertEqual(set(legacy_implementation_sources(self.root)), {path})
        self.source("app.py", textwrap.dedent(LEGACY_CLASS) + "\ndef main():\n    RMClientApp().run()")
        self.assertEqual(legacy_implementation_sources(self.root), {})

    def test_moved_legacy_source_has_same_classification_and_app_stays_current(self):
        self.source("app.py", textwrap.dedent(QT_MAIN) + textwrap.dedent(LAZY_EXPORT))
        legacy = self.source("legacy_tk_app.py", LEGACY_CLASS)
        self.assertEqual(set(legacy_implementation_sources(self.root)), {legacy})

    def test_filename_alone_never_excludes_a_source(self):
        self.source("legacy_tk_app.py", LEGACY_CLASS)
        self.assertEqual(legacy_implementation_sources(self.root), {})
        self.source("app.py", QT_MAIN)
        self.assertEqual(legacy_implementation_sources(self.root), {})

    def test_renamed_or_unverified_target_does_not_create_an_exclusion(self):
        self.source("app.py", textwrap.dedent(LEGACY_CLASS) + textwrap.dedent(QT_MAIN).replace("ui.polar_app", "other_client"))
        self.assertEqual(legacy_implementation_sources(self.root), {})

    def test_extra_startup_behavior_requires_review_before_exclusion(self):
        self.source("app.py", textwrap.dedent(LEGACY_CLASS) + textwrap.dedent(QT_MAIN) + "    RMClientApp().run()\n")
        self.assertEqual(legacy_implementation_sources(self.root), {})

    def test_eager_legacy_import_is_not_a_lazy_compatibility_export(self):
        self.source("app.py", textwrap.dedent(QT_MAIN) + textwrap.dedent(LAZY_EXPORT)
                    + "from .legacy_tk_app import RMClientApp\n")
        self.source("legacy_tk_app.py", LEGACY_CLASS)
        self.assertEqual(legacy_implementation_sources(self.root), {})

    def test_private_getattr_string_elsewhere_does_not_prove_lazy_loading(self):
        self.source("app.py", textwrap.dedent(QT_MAIN) + "\nMODULE = '.legacy_tk_app'\n")
        self.source("legacy_tk_app.py", LEGACY_CLASS)
        self.assertEqual(legacy_implementation_sources(self.root), {})

    def test_getattr_literal_without_import_does_not_prove_lazy_export(self):
        self.source("app.py", textwrap.dedent(QT_MAIN) + "\ndef __getattr__(name):\n    return '.legacy_tk_app'\n")
        self.source("legacy_tk_app.py", LEGACY_CLASS)
        self.assertEqual(legacy_implementation_sources(self.root), {})

    def test_architecture_totals_keep_the_legacy_implementation_after_relocation(self):
        self.source("app.py", textwrap.dedent(LEGACY_CLASS) + textwrap.dedent(QT_MAIN))
        before = measure_tree(self.root)
        self.source("app.py", textwrap.dedent(QT_MAIN) + textwrap.dedent(LAZY_EXPORT))
        self.source("legacy_tk_app.py", LEGACY_CLASS)
        after = measure_tree(self.root)
        self.assertIn("RMClientApp.draw", after["modules"]["rm_client.legacy_tk_app"]["functions"])
        old = before["modules"]["rm_client.app"]["functions"]["RMClientApp.draw"]
        new = after["modules"]["rm_client.legacy_tk_app"]["functions"]["RMClientApp.draw"]
        self.assertEqual(old["statements"], new["statements"])
        self.assertEqual(after["summary"]["historical_ui_functions"], 1)
        self.assertEqual(before["summary"]["historical_ui_functions"], after["summary"]["historical_ui_functions"])
        self.assertGreaterEqual(after["summary"]["functions"], before["summary"]["functions"])

    def test_other_runtime_consumer_prevents_excluding_historical_implementation(self):
        self.source("app.py", textwrap.dedent(QT_MAIN) + textwrap.dedent(LAZY_EXPORT))
        self.source("legacy_tk_app.py", LEGACY_CLASS)
        self.source("ui/consumer.py", "from rm_client.app import RMClientApp")
        self.assertEqual(legacy_implementation_sources(self.root), {})

    def test_current_and_historical_map_evidence_stay_separate(self):
        legacy = self.source("app.py", textwrap.dedent(LEGACY_CLASS) + textwrap.dedent(QT_MAIN))
        current = self.source("ui/live.py", "MODULE = 'CurrentOnly'\n")
        registry = mock.Mock()
        registry.modules = [mock.Mock(name="legacy"), mock.Mock(name="current")]
        registry.modules[0].name = "LegacyOnly"
        registry.modules[1].name = "CurrentOnly"
        with mock.patch.object(module_backend_map, "ROOT", self.root), \
             mock.patch.object(module_backend_map, "PKG", self.package), \
             mock.patch.object(module_backend_map, "build_default_registry", return_value=registry):
            files = module_backend_map.py_files()
            self.assertEqual(files, [current])
            self.assertEqual(set(module_backend_map.py_files(include_legacy=True)), {legacy, current})
            current_hits = module_backend_map.build_index(files)
            old_hits = module_backend_map.legacy_module_mentions(legacy_implementation_sources(self.root))
        self.assertEqual(current_hits["LegacyOnly"], [])
        self.assertTrue(current_hits["CurrentOnly"])
        self.assertTrue(old_hits["LegacyOnly"])
        self.assertNotIn("CurrentOnly", old_hits)

    def test_unverified_legacy_stays_in_current_search_instead_of_disappearing(self):
        legacy = self.source("legacy_tk_app.py", LEGACY_CLASS)
        with mock.patch.object(module_backend_map, "ROOT", self.root), mock.patch.object(module_backend_map, "PKG", self.package):
            self.assertEqual(module_backend_map.py_files(), [legacy])

    def test_catalog_declarations_cannot_become_runtime_consumer_evidence(self):
        self.source("model.py", "MODULE = 'DeclarationOnly'\n")
        self.source("module_catalog.py", "MODULE = 'DeclarationOnly'\n")
        self.source("ui/jello_locale.py", "LABEL = 'DeclarationOnly'\n")
        consumer = self.source("backend/module_catalog.py", "MODULE = 'ActualConsumer'\n")
        registry = mock.Mock()
        registry.modules = [mock.Mock(), mock.Mock()]
        registry.modules[0].name = "DeclarationOnly"
        registry.modules[1].name = "ActualConsumer"
        with mock.patch.object(module_backend_map, "ROOT", self.root), \
             mock.patch.object(module_backend_map, "PKG", self.package), \
             mock.patch.object(module_backend_map, "build_default_registry", return_value=registry):
            for include_legacy in (False, True):
                files = module_backend_map.py_files(include_legacy=include_legacy)
                self.assertEqual(files, [consumer])
                hits = module_backend_map.build_index(files)
                self.assertEqual(hits["DeclarationOnly"], [])
                self.assertEqual(hits["ActualConsumer"], [(consumer, 1)])
        report = measure_tree(self.root)
        self.assertEqual(report["summary"]["files"], 4)
        self.assertIn("rm_client.module_catalog", report["modules"])

    def test_generated_document_retains_manual_section_and_separates_legacy_evidence(self):
        self.source("app.py", textwrap.dedent(LEGACY_CLASS) + textwrap.dedent(QT_MAIN))
        self.source("module_catalog.py", "MODULE = 'LegacyOnly'\n")
        module = mock.Mock()
        module.name = "LegacyOnly"
        module.category = module_backend_map.CATEGORIES[0]
        module.enabled = False
        module.settings = []
        registry = mock.Mock()
        registry.modules = [module]
        registry.get.return_value = module
        with mock.patch.object(module_backend_map, "ROOT", self.root), \
             mock.patch.object(module_backend_map, "PKG", self.package), \
             mock.patch.object(module_backend_map, "build_default_registry", return_value=registry), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module_backend_map.main(), 0)
        document = (self.root / "MODULE_BACKEND_MAP.md").read_text(encoding="utf-8")
        live_section, historical_section = document.split("## 2.1", 1)
        self.assertNotIn("`rm_client/app.py`", live_section)
        self.assertNotIn("`rm_client/module_catalog.py`", live_section)
        self.assertIn("rm_client/app.py", historical_section)
        self.assertIn("module_catalog.py", historical_section)
        self.assertEqual(document.count("## 4."), 1)
        self.assertIn("本节尚未维护", document.split("## 4.", 1)[1])
        self.assertNotIn("本客户端缺的只有", document)

    def test_generator_preserves_current_reviewed_manual_section_and_is_idempotent(self):
        source_document = (ROOT / "MODULE_BACKEND_MAP.md").read_bytes()
        manual = source_document[source_document.index(b"## 4."):]
        self.assertIn("人工维护".encode("utf-8"), manual)
        # Real current prose is the fixture: do not replace it with generated
        # transport assertions, including on repeated regeneration.
        destination = self.root / "MODULE_BACKEND_MAP.md"
        destination.write_bytes(b"# stale generated section\n\n" + manual)
        self.source("consumer.py", 'MODULE = "ManualFixture"')
        module = mock.Mock(name="registry module")
        module.name = "ManualFixture"
        module.category = module_backend_map.CATEGORIES[0]
        module.enabled = False
        module.settings = []
        registry = mock.Mock()
        registry.modules = [module]
        registry.get.return_value = module
        with mock.patch.object(module_backend_map, "ROOT", self.root), \
             mock.patch.object(module_backend_map, "PKG", self.package), \
             mock.patch.object(module_backend_map, "build_default_registry", return_value=registry), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module_backend_map.main(), 0)
            first = destination.read_bytes()
            self.assertEqual(first[first.index(b"## 4."):], manual)
            self.assertNotIn(b"stale generated section", first)
            self.assertEqual(module_backend_map.main(), 0)
            self.assertEqual(destination.read_bytes(), first)

    def test_manual_section_preserves_crlf_and_missing_final_newline(self):
        path = self.root / "MODULE_BACKEND_MAP.md"
        manual = "## 4. Maintained notes\r\n\r\nOperator verified text, no final newline"
        path.write_bytes(("# generated\r\n" + manual).encode("utf-8"))
        self.assertEqual(module_backend_map.retained_manual_section(path), manual)

    def test_unreadable_existing_document_is_not_replaced_with_a_placeholder(self):
        path = self.root / "MODULE_BACKEND_MAP.md"
        path.write_bytes(b"# existing evidence\n\xff")
        with self.assertRaises(UnicodeError):
            module_backend_map.retained_manual_section(path)
        self.assertEqual(path.read_bytes(), b"# existing evidence\n\xff")

    def test_real_repository_classification_has_explicit_entrypoint_evidence(self):
        classified = legacy_implementation_sources(ROOT)
        self.assertEqual(len(classified), 1)
        path, evidence = next(iter(classified.items()))
        self.assertIn(path.name, {"app.py", "legacy_tk_app.py"})
        self.assertIn("rm_client.ui.polar_app.main", evidence)


if __name__ == "__main__":
    unittest.main()
