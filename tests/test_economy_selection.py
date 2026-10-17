"""Pure selection: no Qt imports, no clock, and no UI field mutation."""
import ast
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.economy_values import EconomyOffer
from rm_client.ui.economy_selection import selected_offer

OFFER = EconomyOffer("small", "ammo", "Buy", "Buy", "Rules", "item", 1, 30, 1, 1, True)


class SelectionTests(unittest.TestCase):
    def test_missing_key_falls_back_without_changing_input(self):
        offers = [OFFER, replace(OFFER, key="large")]
        before = offers[:]
        self.assertIs(selected_offer(offers, "missing"), OFFER)
        self.assertIs(selected_offer(offers, "missing"), OFFER)
        self.assertEqual(offers, before)

    def test_matching_disabled_or_status_only_offer_is_still_the_selected_value(self):
        for offer in (replace(OFFER, enabled=False), replace(OFFER, status_only=True)):
            self.assertIs(selected_offer([replace(OFFER, key="other"), offer], "small"), offer)

    def test_empty_offers_have_no_selection(self):
        self.assertIsNone(selected_offer([], "small"))

    def test_production_callers_use_explicit_query_or_command(self):
        legacy_calls = []
        for path in (ROOT / "rm_client").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "economy_selected_offer"):
                    legacy_calls.append((str(path.relative_to(ROOT)), node.lineno))
        self.assertEqual(legacy_calls, [], "Compatibility entry point must not hide mutation in new callers")

    def test_import_does_not_load_ui_or_device_implementations(self):
        code = '''
import sys
from rm_client.ui.economy_selection import selected_offer
assert selected_offer([], "unknown") is None
assert not any(name.startswith(("PySide6", "rm_client.model", "rm_client.backend", "rm_client.ui.polar")) for name in sys.modules)
assert "rm_client.economy" not in sys.modules
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
