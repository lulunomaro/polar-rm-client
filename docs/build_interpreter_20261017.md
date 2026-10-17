# Release interpreter reproducibility, 2026-10-17

## Scope and defect

Based on `1b2f580`. The source launcher provisions `.venv-polar`, whereas the
release script implicitly required `.venv` and invoked its Python before the
build-environment check. A prepared alternative environment could not be selected.
The separate `pyinstaller.exe` path also made interpreter ownership less explicit.

The builder now accepts `-PythonPath`, retains `.venv` as its backward-compatible
default, resolves explicit relative paths through the PowerShell session, and
checks the executable/Python version/PyInstaller module before staging. All
protocol checks, tests, freezing and dependency evidence use that interpreter;
freezing invokes `python -m PyInstaller`. No installation or fallback interpreter
selection is added. Full dependency and desktop gates remain unchanged.

## Verification

Linux x86_64, CPython 3.12.14. Final edited files were checked with the shared,
hash-pinned verification environment:

```sh
/workspace/shared/client-clean-venv/bin/python tests/test_build_interpreter.py
/workspace/shared/client-clean-venv/bin/python tests/test_build_telemetry_environment.py
/workspace/shared/client-clean-venv/bin/python tests/test_build_safety_regressions.py
git diff --check
```

All commands exited 0: 5 + 2 + 22 unittest cases. Interpreter prerequisites execute
in fresh Python subprocesses with inert discovery/version stubs, including
optimized Python rejection cases. PowerShell path/ordering and invocation tests
are explicitly source-contract checks, not executed Windows behavior.

Before the last PowerShell path-resolution adjustment, this scoped invocation
also exited 0 with 3 PASS, 0 SKIP, 0 INCOMPLETE, 0 FAIL (29 cases):

```sh
python tools/verify_all.py --test tests/test_build_interpreter.py --test tests/test_build_telemetry_environment.py --test tests/test_build_safety_regressions.py --json tmp_quality/build-interpreter.json --log-dir tmp_quality/build-interpreter-logs
```

A repeat using `tmp_quality/build-interpreter-final.json` was refused by the
shared verifier lock (INCOMPLETE, exit 2). That attempt is retained separately;
it is not final aggregate evidence. Final direct tests above passed after the
adjustment. The integrating owner must repeat the scoped or broader verifier
once the active run ends. Scratch reports remain ignored and historical evidence
and public baselines were not changed.

## Remaining prerequisites

No PowerShell/Windows desktop is available on this host. Validate on Windows
PowerShell 5.1 with both the default `.venv` and explicit paths containing spaces,
including a shell whose current location differs from its process directory.
Exercise missing/broken Python and missing PyInstaller rejection before staging.
Then use the complete runtime/build requirements, manifest-matched FFmpeg and
wallpapers, and real desktop/OpenGL to perform a full release build and portable
preflight. Offline results do not certify packaging, GPU rendering or a robot.
