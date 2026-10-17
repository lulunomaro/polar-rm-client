"""Freeze-process opt-out contract without PyInstaller, SDK imports or packaging."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

# Execute the checked-in spec, replacing only the unavailable freezer's APIs.
# No OpenVINO/CV2 module is loaded. A subprocess prevents spec environment writes
# from leaking into this test or the invoking developer's shell.
INERT_FREEZER = r'''
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

observed = []
for name in ("PyInstaller", "PyInstaller.utils", "PyInstaller.utils.hooks"):
    sys.modules[name] = ModuleType(name)

def collect_all(package):
    observed.append((package, os.environ.get("CI")))
    return [], [], []

def hook_export(name):
    if name != "collect_all":
        raise AttributeError(name)
    observed.append(("hook-import", os.environ.get("CI")))
    return collect_all

sys.modules["PyInstaller.utils.hooks"].__getattr__ = hook_export
spec = Path(sys.argv[1])
namespace = {
    "SPECPATH": str(spec.parent),
    "Analysis": lambda *a, **kw: SimpleNamespace(binaries=[], pure=[], scripts=[], datas=[]),
    "PYZ": lambda *a, **kw: None,
    "EXE": lambda *a, **kw: None,
    "COLLECT": lambda *a, **kw: None,
}
exec(compile(spec.read_text(encoding="utf-8"), str(spec), "exec"), namespace)
print(json.dumps(observed))
'''


class FreezeEnvironmentTests(unittest.TestCase):
    def test_real_spec_opts_out_before_each_inert_hook_and_leaves_parent_untouched(self):
        original = dict(os.environ)
        for inherited in (None, "false", "custom"):
            with self.subTest(inherited=inherited):
                environment = dict(os.environ)
                environment.pop("CI", None)
                if inherited is not None:
                    environment["CI"] = inherited
                result = subprocess.run(
                    [sys.executable, "-c", INERT_FREEZER, str(ROOT / "tools/POLAR-RM.spec")],
                    env=environment, capture_output=True, text=True, timeout=15, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), [["hook-import", "true"], ["cv2", "true"], ["openvino", "true"]])
                self.assertEqual(dict(os.environ), original)

    def test_release_launcher_scopes_and_restores_freezing_environment(self):
        # PowerShell is unavailable on this host. This checks its explicit
        # source contract; the isolated spec test above executes Python only.
        source = (ROOT / "tools/build_release.ps1").read_text(encoding="utf-8-sig")
        block = source.split('    Push-Location $sourceRoot\n', 1)[1]
        block = block.split('    if ($LASTEXITCODE', 1)[0]
        self.assertLess(block.index('$oldFreezeCi = $env:CI'), block.index('    try {'))
        self.assertLess(block.index('$env:CI = "true"'), block.index('& $python -m PyInstaller'))
        restore = block.split('} finally {', 1)[1]
        self.assertIn('if ($null -eq $oldFreezeCi)', restore)
        self.assertIn('Remove-Item Env:CI -ErrorAction SilentlyContinue', restore)
        self.assertIn('$env:CI = $oldFreezeCi', restore)
        self.assertIn('Pop-Location', restore)


if __name__ == "__main__":
    unittest.main()
