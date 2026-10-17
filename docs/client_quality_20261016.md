# Client quality repair, 2026-10-16

## Scope and integration

This is an incremental repair on the reviewed UI candidate
`b26b7b718e348fbda066412f6d86140727f32e73` (PR #17), not a rewrite.
It also incorporates the reviewed workspace hygiene change from PR #18.
The proposed branch is `refactor/client-quality-20261016`; main is not merged.

## Changes

- Native decoder recovery is single-flight and scheduled off the UI tick.
  Completion is applied by the manager thread. Decoder process/queue/worker
  ownership is per session, and generation checks invalidate stale recovery.
  UDP finalizers cannot close the next session's socket.
- Keybinding edits and profile restore use a shared whole-map validator.
  Swaps and longer cycles restore atomically. Invalid configuration rolls back
  with output locked; settings entrypoints enforce their existing allowed scope.
- `ClickGuiFrame` carries draw commands and matching pointer/scroll geometry.
  Preparation restores host state on exceptions; presentation publishes geometry
  after successful drawing and discards failed GPU geometry before fallback.
  Legacy builder entrypoints remain available.
- A 51-field telemetry contract replaces arbitrary assignment into RobotState.
  Values are validated and mutable containers detached. Custom protocol fields
  remain available through `polar_overlay` without permission to overwrite
  settings, authority flags or methods.
- Verification and Windows release use a shared discovered test inventory.
  Zero-test execution, partial skips, missing prerequisites and timeouts do not
  count as success. Release metadata records actual outcomes instead of fixed
  PASS labels. External inference is a separate, explicit fixture-dependent suite.
- README, contribution guidance and architecture documentation describe the
  current runtime and ownership requirements. Generated artifacts stay separate
  from maintained fixtures and historical evidence.

## Evidence requirements

The accompanying evidence report records the final run and its limitations.
Use the registry to reproduce the scope, not an unqualified count from a chat:

```sh
python tools/verify_all.py --list --profile all
python tools/verify_all.py --profile offline --timeout 120 \
  --log-dir tmp/quality-logs --json tmp/quality-results.json
```

The test environment snapshot in `evidence/client-quality-20261016/` is a Linux
validation environment, not a universal production dependency lock. Qt 6.8.2
matches the prior UI candidate's validation baseline. Initial broad dependency
resolution selected Qt 6.12; a full-window test aborted in that environment.
It was not counted as passing and was repeated after restoring Qt 6.8.2.

Real Qt window construction and raster comparison do not establish desktop GL,
Windows DWM/fullscreen, audio-device output or real-robot acceptance. Those gates
remain separate. Missing team firmware/reference inputs must remain visible in
reports rather than be replaced by mocks and called hardware validation.

## Remaining architecture work

The window still uses shared-state mixins; BackendManager still owns several
responsibilities. This pass does not claim full architectural decoupling.
Scenario changes and shutdown can still perform synchronous work, and diagnostic
file writes remain potential latency sources. Legacy painter/GPU layouts are not
fully unified. These require measured follow-up with compatibility tests, rather
than moving methods between files or removing safety checks to make tests green.

## Final offline result

Final source snapshot: `db48826ba72f72d76f507af42a3e1519d7bf0827`.

- 113 checks PASS, 12 SKIP, 4 INCOMPLETE, 0 FAIL.
- This is **NO-GO for the entire selected scope**, because external firmware and
  team reference inputs were not supplied. No skip was converted into PASS.
- The four incomplete checks are the team CRC reference comparison, the DT7
  link cross-check (firmware and official-client dump), the DT7 UI firmware
  cross-check, and a legacy installation-dependent firmware patch test.
- Twelve other firmware checks explicitly skipped for missing source/header
  fixtures. Desktop/visual/external-inference categories were not selected.
- The final runtime smoke executed 71 functions with zero skipped checks.
- The first diagnostic run exposed stale geometry/font/path expectations,
  a borrowed `QTextLine` whose temporary `QTextLayout` could be destroyed,
  brittle setting-read regexes, and outdated settings-count documentation.
  These were repaired and the complete offline profile rerun on a stable
  committed source snapshot. Production rendering was not changed to make
  the stale tests pass.

See [machine-readable final results](evidence/client-quality-20261016/final-results.json),
[final console](evidence/client-quality-20261016/final-console.txt), and
[first-pass findings](evidence/client-quality-20261016/first-pass-findings.json).
Counts refer to suite/check entries, not a single homogeneous unit-test count.
There is no claim that the architecture is completely debt-free, nor that this
run qualifies a physical robot or native Windows graphics.
