# Verification environment output hygiene, 2026-10-17

The reproduction recipe in `docs/quality_gates.md` creates a root
`.venv-verification` directory. At `b0f5942`, `.gitignore` excluded `.venv` and
`.venv-polar`, but not that documented environment. `git check-ignore --no-index`
returned exit 1 for both `.venv-verification/pyvenv.cfg` and
`.venv-verification/bin/python`. Python 3.12 environments created by that recipe
could therefore appear in source status and accidentally be staged.

Added only `/.venv-verification/`; no files were deleted or untracked. A new test
copies the real ignore file into a disposable Git repository and asks Git which
candidate paths are ignored. It requires environment config/interpreter/package
paths to be ignored and intentional fixtures, baselines, evidence and the lock
file to remain visible. This avoids a broad pattern that could hide inputs.

Validation on Linux, CPython 3.12.14, using the shared hash-pinned environment:

```sh
/workspace/shared/client-clean-venv/bin/python tests/test_repository_output_hygiene.py
git diff --check
```

Before the ignore fix: one test failed because Git returned no ignored paths.
After: one test passed, exit 0; diff check passed. Original failure log remains
in the ignored `tmp_quality/output-hygiene-red.log`. No aggregate was attempted
while another verification run owned the shared lock. The new suite is discovered
by the standard `test_*.py` registry rule; integration must run it in the final
selected scope. This verifies source hygiene, not dependency installation or
application/native qualification.
