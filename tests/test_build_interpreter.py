"""Release interpreter contract; no installer, freezer, desktop or device use.

Python prerequisite code executes in inert subprocesses. PowerShell ordering
and invocation checks are source contracts, not Windows execution evidence.
"""
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BuildInterpreterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'tools/build_release.ps1').read_text(encoding='utf-8-sig')
        cls.probe = re.search(r'^& \$python -c "([^"\n]+)"$', cls.source, re.M).group(1)

    def test_environment_checked_before_any_staging_or_protocol_validation(self):
        source = self.source
        self.assertIn('[string]$PythonPath = ""', source)
        self.assertIn('$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PythonPath)', source)
        self.assertIn('Test-Path -LiteralPath $python -PathType Leaf', source)
        probe = source.index('& $python -c "')
        failure = source.index('if ($LASTEXITCODE -ne 0)', probe)
        staging = source.index('New-Item -ItemType Directory -Force $sourceRoot')
        self.assertLess(probe, failure)
        self.assertLess(failure, staging)
        self.assertLess(staging, source.index('spec = polar_spec.load_spec'))

    def test_selected_interpreter_owns_spec_tests_freezer_and_provenance(self):
        source = self.source
        self.assertEqual(source.count('Join-Path $clientRoot ".venv\\Scripts\\python.exe"'), 1)
        self.assertNotIn('$pyinstaller', source)
        self.assertIn('& $python -c @"\nimport sys', source)
        self.assertIn('& $python (Join-Path $sourceRoot "tools\\verify_all.py")', source)
        self.assertIn('& $python -m PyInstaller --noconfirm --clean', source)
        self.assertIn('(& $python -m pip freeze)', source)

    def probe_result(self, version, installed):
        # Stub only discovery/version in a fresh child, never import PyInstaller.
        bootstrap = ("import sys, importlib.util; "
                     f"sys.version_info = {version!r}; "
                     f"importlib.util.find_spec = lambda name: {installed!r} or None; "
                     f"exec({self.probe!r})")
        return subprocess.run([sys.executable, '-O', '-c', bootstrap],
                              capture_output=True, text=True, timeout=15)

    def test_missing_freezer_is_rejected_even_under_optimized_python(self):
        result = self.probe_result((3, 12), False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Install requirements-build.txt in this interpreter', result.stderr)

    def test_old_python_is_rejected_even_with_freezer(self):
        result = self.probe_result((3, 9), True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python 3.10+ required', result.stderr)

    def test_supported_python_with_freezer_passes_inert_probe(self):
        result = self.probe_result((3, 12), True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
