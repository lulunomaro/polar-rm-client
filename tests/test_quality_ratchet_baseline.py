"""Negative controls for every currently reviewed responsibility budget.

Measure production source without importing it. A changed report isolates the
one metric under test; these checks do not regenerate or relax any baseline.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.quality import gates, metrics


class ReviewedResponsibilityBudgetTests(unittest.TestCase):
    def test_every_reviewed_metric_rejects_one_above_its_limit(self):
        baseline = gates.load_baseline(ROOT / "tools/quality/baseline.json")
        report = metrics.measure_tree(ROOT)
        self.assertEqual(gates.check_report(report, baseline), [])
        checked = 0
        for symbol, budgets in baseline["function_budgets"].items():
            module, function = symbol.split(":", 1)
            self.assertIn(function, report["modules"][module]["functions"])
            for metric, limit in budgets.items():
                if metric == "rationale":
                    continue
                with self.subTest(symbol=symbol, metric=metric):
                    changed = copy.deepcopy(report)
                    changed["modules"][module]["functions"][function][metric] = limit + 1
                    self.assertEqual(gates.check_report(changed, baseline), [
                        f"Responsibility budget grew: {symbol} {metric} {limit} -> {limit + 1}"
                    ])
                    checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
