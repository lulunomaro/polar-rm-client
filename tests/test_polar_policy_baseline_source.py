"""A public-clone replay must not depend on unpublished Git object IDs."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import compare_polar_policy as replay


class BaselineSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = b"reviewed reference manager bytes"
        self.digest = hashlib.sha256(self.source).hexdigest()
        self.digest_patch = patch.object(replay, "BASELINE_MANAGER_SHA256", self.digest)
        self.digest_patch.start()
        self.addCleanup(self.digest_patch.stop)

    def result(self, source=b"", code=0):
        return subprocess.CompletedProcess([], code, stdout=source, stderr=b"")

    def test_public_revision_is_preferred_and_read_without_network(self):
        with patch.object(replay.subprocess, "run", return_value=self.result(self.source)) as run:
            self.assertEqual(replay.baseline_source(replay.BASELINE), (self.source, replay.BASELINE))
        run.assert_called_once_with(
            ["git", "show", f"{replay.BASELINE}:{replay.MANAGER_PATH}"],
            cwd=ROOT, capture_output=True, check=False)

    def test_known_local_alias_is_accepted_only_with_identical_digest(self):
        results = [self.result(code=128), self.result(self.source)]
        with patch.object(replay.subprocess, "run", side_effect=results) as run:
            self.assertEqual(replay.baseline_source(replay.BASELINE),
                             (self.source, replay.LOCAL_BASELINE_ALIAS))
        self.assertEqual(run.call_count, 2)

    def test_wrong_source_never_falls_back_to_another_reference(self):
        with patch.object(replay.subprocess, "run", return_value=self.result(b"changed")) as run:
            with self.assertRaisesRegex(RuntimeError, "Refusing baseline"):
                replay.baseline_source(replay.BASELINE)
        self.assertEqual(run.call_count, 1)

    def test_missing_history_reports_actionable_offline_requirement(self):
        with patch.object(replay.subprocess, "run", return_value=self.result(code=128)):
            with self.assertRaises(RuntimeError) as raised:
                replay.baseline_source(replay.BASELINE)
        message = str(raised.exception)
        for expected in (replay.BASELINE, self.digest, "--baseline", "never fetches"):
            self.assertIn(expected, message)

    def test_explicit_reference_cannot_silently_use_default_history(self):
        with patch.object(replay.subprocess, "run", return_value=self.result(code=128)) as run:
            with self.assertRaises(RuntimeError):
                replay.baseline_source("missing-user-reference")
        self.assertEqual(run.call_count, 1)

    def test_missing_baseline_exits_incomplete_before_loading_harness(self):
        with patch.object(sys, "argv", ["compare_polar_policy.py", "--cases", "0"]), \
                patch.object(replay, "baseline_manager", side_effect=RuntimeError("missing source")), \
                patch.object(replay, "characterization_harness") as harness, \
                patch.object(sys, "stderr", io.StringIO()) as error:
            self.assertEqual(replay.main(), 2)
        harness.assert_not_called()
        self.assertIn("INCOMPLETE: missing source", error.getvalue())


class FrozenSourceDigestTests(unittest.TestCase):
    def test_tool_digest_matches_untouched_fixture(self):
        fixture = json.loads((ROOT / "tests/fixtures/backend_policy_baseline.json").read_text())
        self.assertEqual(replay.BASELINE_MANAGER_SHA256, fixture["manager_sha256"])


if __name__ == "__main__":
    unittest.main()
