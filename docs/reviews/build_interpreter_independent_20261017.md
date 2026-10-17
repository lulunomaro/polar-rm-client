# Independent release-interpreter review

Candidate `b0f594206fa58612c67d2183770ebd564d5bd235`, independently checked in a
separate detached worktree. Outcome: **PASS for source contracts and executed
Python prerequisite behavior**, not Windows or packaging qualification.

The change resolves one selected interpreter before staging; the previous
hardcoded early protocol invocation and separate freezer executable are replaced
by that interpreter. Default `.venv` behavior is retained; an explicit path is
resolved by the PowerShell session provider API. No installation, fallback,
external download, API relaxation or native-device operation is introduced.
The changed telemetry assertion follows the new invocation spelling without
removing the ordering/environment-restoration assertions.

Independent verification with `/workspace/shared/client-clean-venv/bin/python`:

- 29 methods pass: `test_build_interpreter`,
  `test_build_telemetry_environment`, `test_build_safety_regressions`.
- Four separate inert subprocess probes execute the actual extracted Python
  check under `-O`: Python 3.9.99 + freezer rejected; exact 3.10.0 + freezer
  accepted; 3.10.0 without freezer rejected; 3.14.0 + freezer accepted.
- Full `1b2f580..b0f5942` diff check passes. Candidate source hashes are recorded.

The availability probe uses `find_spec`; it establishes module discoverability,
not that importing/freezing with a broken installation succeeds. Later build
steps still enforce actual process exit and expected artifact checks. The
source-string checks are intentionally limited and cannot verify PowerShell
parsing, provider semantics or native process execution. This host has no `pwsh`.

Before release, execute Windows PowerShell 5.1 tests for default and explicit
paths (including spaces and differing shell/process directories), missing or
broken executables, missing freezer, and the full build/preflight with approved
asset hashes. Parent integration still needs its own fixed-source aggregate.

Raw focused log, independent boundary results and candidate hashes:
`../evidence/clean-code-20261017/project-independent/`.
