"""Shared economy vocabulary compatibility and UI dependency direction."""
import ast
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client import economy, economy_values
from rm_client.ui import cs2_buy_menu, economy_content


class EconomyVocabularyCompatibility(unittest.TestCase):
    def test_public_label_contents_and_insertion_order_remain_stable(self):
        self.assertEqual(list(economy.CATEGORY_TITLES.items()), [
            ("ammo", "弹丸"), ("recovery", "恢复"), ("revive", "复活"), ("support", "支援"),
        ])
        self.assertEqual(list(economy.TRANSACTION_LABELS.items()), [
            ("confirming", "等待确认"), ("pending", "等待裁判系统"), ("succeeded", "操作成功"),
            ("rejected", "请求被拒绝"), ("timed_out", "确认超时"),
        ])

    def test_existing_consumers_share_label_identity_and_in_place_changes(self):
        for name in ("CATEGORY_TITLES", "TRANSACTION_LABELS"):
            labels = getattr(economy, name)
            for consumer in (economy_values, economy_content, cs2_buy_menu):
                self.assertIs(getattr(consumer, name), labels)
            with patch.dict(labels, {"fixture-label": "visible mutation"}):
                for consumer in (economy_values, economy_content, cs2_buy_menu):
                    self.assertEqual(getattr(consumer, name)["fixture-label"], "visible mutation")
            self.assertNotIn("fixture-label", labels)

    def test_cs2_column_title_snapshot_keeps_existing_copy_semantics(self):
        # This existing derived grid vocabulary intentionally snapshots the
        # shared categories when the module loads; the refactor does not alter it.
        original = cs2_buy_menu.TITLES["ammo"]
        self.assertIsNot(cs2_buy_menu.TITLES, economy.CATEGORY_TITLES)
        with patch.dict(economy.CATEGORY_TITLES, {"ammo": "changed category"}):
            self.assertEqual(economy_content.CATEGORY_TITLES["ammo"], "changed category")
            self.assertEqual(cs2_buy_menu.TITLES["ammo"], original)
        self.assertEqual(cs2_buy_menu.TITLES["supplier"], "补给站")


class EconomyValueDependencyTests(unittest.TestCase):
    def test_ui_value_consumers_work_with_transaction_import_forbidden(self):
        code = r"""
import importlib.abc
import sys
from types import SimpleNamespace
class BlockTransactions(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "rm_client.economy":
            raise AssertionError("UI value consumer imported the transaction owner")
sys.meta_path.insert(0, BlockTransactions())
from rm_client.economy_values import EconomyOffer
from rm_client.ui import cs2_buy_menu, economy_content
from rm_client.ui.economy_interaction import ActivationAction, plan_economy_activation
from rm_client.ui.economy_presentation import EconomyConfirmationState
from rm_client.ui.economy_selection import selected_offer
offer = EconomyOffer("small", "ammo", "Buy", "Buy", "Rules", "item", 1, 30, 1, 1, True)
assert selected_offer([offer], "small") is offer
intent = plan_economy_activation(offer, read_armed_key=lambda: "small", read_armed_until=lambda: 100., clock=lambda: 99.)
assert intent.action is ActivationAction.SUBMIT
assert EconomyConfirmationState(offer, False, "", 0., "small", 100.).present(now=99.).enabled
from PySide6.QtCore import QRectF
from rm_client.ui.economy_workspace import EconomyWorkspaceLayer
assert cs2_buy_menu.grid_layout(EconomyWorkspaceLayer(), 1280, 720, 1.).panel.width() > 0
texts = []
commands = SimpleNamespace(text=lambda rect, text, *args, **kwargs: texts.append(text))
style = economy_content.EconomyContentStyle(lambda *args: None, 1., lambda: False)
economy_content.append_gpu_transaction(commands, economy_content.EconomyTransactionText("pending", "item", "message"),
                                       QRectF(0., 0., 400., 60.), QRectF(0., 0., 400., 60.), 1., style)
assert texts == ["等待裁判系统 · item", "message"]
assert economy_content.TRANSACTION_LABELS is cs2_buy_menu.TRANSACTION_LABELS
assert "rm_client.economy" not in sys.modules
assert not any(name.startswith(("rm_client.model", "rm_client.backend", "rm_client.ui.polar")) for name in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dictionaries_have_one_source_owner_and_are_not_copied_on_reexport(self):
        for name in ("CATEGORY_TITLES", "TRANSACTION_LABELS"):
            owners = []
            for path in (ROOT / "rm_client").rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                    if (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name
                                                           for target in node.targets)):
                        owners.append(str(path.relative_to(ROOT)))
            self.assertEqual(owners, ["rm_client/economy_values.py"])
            self.assertIs(getattr(economy, name), getattr(economy_values, name))


if __name__ == "__main__":
    unittest.main()
