"""Enforce the schema/codec boundary, independently of the retained Qt adapter."""
import ast
import inspect
from pathlib import Path
import subprocess
import sys
import unittest

from rm_client import crosshair_profile as profile
from rm_client import cs2_crosshair as adapter

ROOT = Path(__file__).resolve().parents[1]


class CrosshairProfileBoundaryTests(unittest.TestCase):
    def test_historic_codec_exports_are_identity_aliases(self):
        for name, value in vars(profile).items():
            if name.startswith('__') or inspect.ismodule(value):
                continue
            self.assertIs(getattr(adapter, name), value, name)

    def test_moved_algorithms_match_the_frozen_implementation_ast(self):
        frozen = ast.parse((ROOT / 'tests/fixtures/crosshair_profile_baseline.py').read_text())
        originals = {n.name: n for n in frozen.body if isinstance(n, ast.FunctionDef)}
        current = ast.parse((ROOT / 'rm_client/crosshair_profile.py').read_text())
        names = []
        for node in current.body:
            if isinstance(node, ast.FunctionDef):
                names.append(node.name)
                self.assertEqual(ast.dump(node), ast.dump(originals[node.name]), node.name)
        self.assertEqual(len(names), 12)

    def test_profile_and_catalog_operate_without_adapter_or_runtime_imports(self):
        program = '''
import importlib.abc
import sys
class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("PySide6", "tkinter", "rm_client.model", "rm_client.backend",
                   "rm_client.ui", "rm_client.cs2_crosshair", "rm_client.serial_ports")
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("unexpected runtime import: " + fullname)
sys.meta_path.insert(0, BlockRuntime())
from rm_client.crosshair_profile import parse_external, export_json, encode_share_code, decode_share_code
from rm_client.module_catalog import CatalogChoices, default_modules
values = parse_external('{"length": 6}')
assert parse_external(export_json(values)) == values
assert decode_share_code(encode_share_code(values))["Length"] == 6
choices = CatalogChoices(("LabDebug",), ("RMUC7v7",), ("Infantry1",), "test", ("test",),
                         ("Stub",), ("Stub",), ("Auto",), ("Auto",), ("Auto",))
first = next(m for m in default_modules(choices) if m.name == "Crosshair")
second = next(m for m in default_modules(choices) if m.name == "Crosshair")
first.get_setting("Length").value = 99
assert second.get_setting("Length").value == 5
assert first.get_setting("Gap").min_value == -3840
'''
        result = subprocess.run([sys.executable, '-c', program], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
