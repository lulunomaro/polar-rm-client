"""CPU-only entry rejection: must not import Qt or launch a simulation."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "verify_sim_hit_spin.py"

class EntryGuards(unittest.TestCase):
    def invoke(self, root, out, flags=(), env=None):
        return subprocess.run([sys.executable, *flags, str(SCRIPT),
            "--client-root", str(root / "missing-client"),
            "--bridge-command-file", str(root / "missing-launch.json"),
            "--out", str(out)], capture_output=True, text=True, env=env)

    def test_optimized_invocations_reject_before_creating_output(self):
        for flags, optimize in ((("-O",), None), (("-OO",), None), ((), "1")):
            with self.subTest(flags=flags, optimize=optimize), tempfile.TemporaryDirectory() as td:
                root = Path(td); out = root / "new"; env = os.environ.copy()
                env.pop("PYTHONOPTIMIZE", None)
                if optimize: env["PYTHONOPTIMIZE"] = optimize
                result = self.invoke(root, out, flags, env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Refusing optimized Python", result.stderr)
                self.assertFalse(out.exists())

    def test_existing_directory_preserves_evidence_and_never_reads_launch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); out = root / "existing"; out.mkdir()
            evidence = out / "summary.json"; evidence.write_bytes(b"original failure")
            result = self.invoke(root, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("fresh, nonexistent directory", result.stderr)
            self.assertNotIn("FileNotFoundError", result.stderr)
            self.assertEqual(evidence.read_bytes(), b"original failure")
            self.assertEqual(list(out.iterdir()), [evidence])

    def test_existing_file_and_dangling_symlink_are_rejected(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as td:
                root = Path(td); out = root / "existing"
                if symlink: out.symlink_to(root / "missing-target")
                else: out.write_bytes(b"preserve")
                result = self.invoke(root, out)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("fresh, nonexistent directory", result.stderr)
                if symlink: self.assertTrue(out.is_symlink())
                else: self.assertEqual(out.read_bytes(), b"preserve")

if __name__ == "__main__": unittest.main()
