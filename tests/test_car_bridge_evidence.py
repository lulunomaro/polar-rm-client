"""Negative controls for the real localhost/fake-board transport script."""
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.verify_all import classify_output


class CarBridgeEvidenceTests(unittest.TestCase):
    def run_script(self, mutation=False):
        code = "from tools import test_car_bridge_transport as test\n"
        if mutation:
            code += "test.Board.close = lambda self: None\n"
        code += "raise SystemExit(test.main())\n"
        return subprocess.run([sys.executable, "-u", "-c", code], cwd=ROOT,
                              capture_output=True, text=True, timeout=25)

    def test_noop_board_close_is_a_real_failure_not_a_pass(self):
        result = self.run_script(mutation=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[FAIL]", result.stdout)
        self.assertEqual(classify_output(result.returncode, result.stdout + result.stderr).status, "FAIL")

    def test_actual_loopback_and_cleanup_have_fifteen_countable_checks(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        classified = classify_output(result.returncode, result.stdout + result.stderr)
        self.assertEqual(classified.status, "PASS")
        self.assertEqual(classified.checks_passed, 15, result.stdout)

    def test_closed_board_cannot_hide_a_server_that_has_not_retired(self):
        from tools import test_car_bridge_transport as script
        bridge = SimpleNamespace(stop=Mock())
        worker = Mock()
        worker.is_alive.return_value = True
        with patch.object(script, "check") as check:
            script.check_bridge_stopped(bridge, worker, SimpleNamespace(closed=True), timeout=0.01)
        bridge.stop.set.assert_called_once_with()
        worker.join.assert_called_once_with(0.01)
        self.assertIs(check.call_args.args[1], False)
        self.assertIn("server_stopped=False", check.call_args.args[2])

    def test_same_line_bridge_prefix_explains_old_missing_assertion_count(self):
        merged = "[bridge] peer admitted[ OK ] connected\n[ OK ] closed\n"
        self.assertEqual(classify_output(0, merged).checks_passed, 1)
        separate = "[bridge] peer admitted\n[ OK ] connected\n[ OK ] closed\n"
        self.assertEqual(classify_output(0, separate).checks_passed, 2)


if __name__ == "__main__":
    unittest.main()
