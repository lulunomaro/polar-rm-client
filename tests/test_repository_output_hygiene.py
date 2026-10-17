"""Exercise actual Git ignore rules in a disposable, empty repository."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RepositoryOutputHygieneTests(unittest.TestCase):
    def test_documented_verification_environment_is_local_but_evidence_is_visible(self):
        # Git is already a source-verification prerequisite; do not skip silently.
        git = shutil.which('git')
        self.assertIsNotNone(git, 'Git is required to verify repository ignore behavior')
        with tempfile.TemporaryDirectory(prefix='polar-ignore-') as directory:
            root = Path(directory)
            (root / '.gitignore').write_bytes((ROOT / '.gitignore').read_bytes())
            subprocess.run([git, 'init', '--quiet', str(root)], check=True, timeout=15)
            candidates = [
                '.venv-verification/pyvenv.cfg',
                '.venv-verification/bin/python',
                '.venv-verification/lib/python3.12/site-packages/example.py',
                'tests/fixtures/verification/example.json',
                'tests/baselines/verification.png',
                'docs/evidence/verification/result.json',
                'requirements-verification-linux-cp312.txt',
            ]
            result = subprocess.run(
                [git, '-c', 'core.excludesFile=', '-C', str(root),
                 'check-ignore', '--no-index', '--stdin'],
                input='\n'.join(candidates) + '\n', capture_output=True,
                text=True, timeout=15, check=False,
            )
            self.assertIn(result.returncode, (0, 1), result.stderr)
            self.assertEqual(result.stdout.splitlines(), candidates[:3])


if __name__ == '__main__':
    unittest.main(verbosity=2)
