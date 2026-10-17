"""Synthetic files test reference selection/preconditions, not vendor compatibility."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools/dt7_link' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Dt7ReferenceValidationTests(unittest.TestCase):
    def test_missing_or_empty_application_is_incomplete(self):
        from tools.verify_all import classify_output
        for kind in ('missing', 'empty', 'non_c_only', 'file'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                app = Path(td) / 'application'
                if kind == 'file':
                    app.write_text('not a directory')
                elif kind != 'missing':
                    app.mkdir()
                    if kind == 'non_c_only':
                        (app / 'note.txt').write_text('synthetic')
                module = load_script('test_dt7_ui_selftest')
                output = io.StringIO()
                with patch.dict(os.environ, {'POLAR_FIRMWARE_ROOT': td}), contextlib.redirect_stdout(output):
                    code = module.main()
                self.assertEqual(code, 0)  # Script skips are classified by the aggregate.
                self.assertEqual(classify_output(code, output.getvalue()).status, 'INCOMPLETE')
                self.assertNotIn('探针选的键在固件里真的没有消费者', output.getvalue())

    def test_application_scan_retains_positive_and_negative_assertion(self):
        for source, expected in [('/* synthetic source */', 0), ('int KEY_X;', 1)]:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as td:
                app = Path(td) / 'application/nested'
                app.mkdir(parents=True)
                (app / 'fixture.c').write_text(source)
                module = load_script('test_dt7_ui_selftest')
                with patch.dict(os.environ, {'POLAR_FIRMWARE_ROOT': td}), contextlib.redirect_stdout(io.StringIO()):
                    code = module.main()
                self.assertEqual(code, expected)
                self.assertFalse(module.SKIPPED)

    def test_vendor_override_reads_exact_file_and_retains_assertions(self):
        with tempfile.TemporaryDirectory() as td:
            selected = Path(td) / 'genuine-reference-path.cs'
            selected.write_text('deliberately invalid synthetic definitions')
            module = load_script('test_dt7_link')
            output = io.StringIO()
            with patch.dict(os.environ, {'POLAR_VENDOR_DUMP': str(selected)}), contextlib.redirect_stdout(output):
                module.test_vendor_type_layout_anchor()
            self.assertFalse(module.SKIPPED)
            self.assertGreater(module.FAILED, 0)  # Override does not waive layout checks.
            self.assertIn('[FAIL]', output.getvalue())

    def test_explicit_missing_vendor_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as td:
            default = Path(td) / 'default.cs'
            default.write_text('synthetic invalid definitions')
            missing = Path(td) / 'missing.cs'
            module = load_script('test_dt7_link')
            module.VENDOR_DUMP = default
            output = io.StringIO()
            with patch.dict(os.environ, {'POLAR_VENDOR_DUMP': str(missing)}), contextlib.redirect_stdout(output):
                module.test_vendor_type_layout_anchor()
            self.assertTrue(module.SKIPPED)
            self.assertEqual(module.FAILED, 0)
            self.assertIn(str(missing), output.getvalue())

    def test_default_vendor_path_preserved(self):
        module = load_script('test_dt7_link')
        self.assertEqual(str(module.VENDOR_DUMP), r'D:\rmbuild\dump12\dump.cs')
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'is_file', return_value=False), contextlib.redirect_stdout(io.StringIO()):
            module.test_vendor_type_layout_anchor()
        self.assertTrue(module.SKIPPED)


if __name__ == '__main__':
    unittest.main()
