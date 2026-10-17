# Clean Code acceptance draft, 2026-10-16

## Snapshot and status

This is an intermediate engineering acceptance draft, not a final all-green
report. Measurements below cover local integrated source
`f70bae188540d37671062afd8dfa00eead90e7ea`, whose measured `rm_client` source digest
is `433636b750e4a50c654637ce6df0677b7db519bd33bbd9c534140922b2c5dd78`.
The commit is local staging provenance, not a required object in a public clone.
The digest and committed gate files support source-independent reproduction.
Later profile, video, and economy-geometry work is outside this snapshot.

The latest completed aggregate available for this draft is
[stage six](evidence/clean-code-20261016/wave6/README.md), at source `950f919`:
**179 PASS, 12 SKIP, 4 INCOMPLETE, 3 FAIL**. It is not acceptance. Its complete
[JSON report](evidence/clean-code-20261016/wave6/results.json) retains interpreter,
OS, installed distribution versions, source commit/tree/dirty status, and each
actual result. Subsequent waiter, paint-owner, and pointer-owner fixture repairs
retain their original behavioral assertions; a later aggregate must establish
the integrated result. Do not combine focused passes with earlier totals and
call that a completed run.

## Relevant chapter guidance

- [Chapter 5: formatting](https://aeryzhao.github.io/cleancode-book/ch5.html)
  motivates consistent organization that helps readers follow related concepts.
  This pass applies narrow readability ratchets; it does not reformat the whole
  repository or turn illustrative file lengths into universal limits.
- [Chapter 9: unit tests](https://aeryzhao.github.io/cleancode-book/ch9.html)
  emphasizes readable, independent, repeatable, self-checking tests. Isolated
  fixtures, explicit completion classification and protected behavior rather
  than incidental private storage are this project's corresponding choices.
- [Chapter 17: smells and heuristics](https://aeryzhao.github.io/cleancode-book/ch17.html)
  highlights straightforward test entrypoints, visible boundary failures and
  testing around discovered defects. The single discovered registry, negative
  cases, source-bound reports and preserved failed checkpoints apply those ideas.

These chapters are selected here as relevant review guidance from the linked book;
they are not presented as a user-specified chapter list or a claim that every
heuristic has been satisfied.

## Discovery and result classification

At the measured snapshot:

- 205 unique suites are discovered: 184 offline, 15 firmware, 4 desktop,
  1 external-inference, and 1 visual-capture suite.
- The offline profile selects 199 suites; its runner also schedules two
  settings audits. The release profile selects 188 suites, including all four
  required native-desktop suites. These are inventories, not executed-pass counts.
- All 69 new test files since the initial candidate are discovered. An additional
  AST inventory found no unlisted Python file under `tests/` or `tools/` containing
  a `test_*` function. Ordinary helpers remain helpers. Discovery is not line or
  branch coverage; no whole-application coverage percentage was established.
- Registry/classification tests passed 34 assertions represented as unittest
  tests; quality tests passed 37, source-classification tests 15, and execution
  provenance tests 16. These focused results do not replace the aggregate.
- Zero tests, empty successful scripts, skipped classes, partial skips, missing
  dependencies, missing completion markers and timeouts cannot silently pass.
  Four native GPU suites remain distinct from offscreen raster and fake-GL tests.

The module-map generator follow-up separately passed 18 source-classification
checks. It now preserves the existing human-maintained section exactly, including
line endings and a missing final newline; repeated generation is idempotent.
A new document without that section receives an explicit unmaintained placeholder,
not old handwritten claims about absent DT7 support.

## Measured architecture, including increases

The [original report](../reports/clean_code/architecture_before.md) remains intact.
The [snapshot report](../reports/clean_code/architecture_f70bae1.md) retains the full
hotspot/coupling table, deferred cycles and measurement limitations.

| Signal | Initial source | This snapshot |
| --- | ---: | ---: |
| Parsed production Python modules | 141 | 181 |
| Functions | 2619 | 3095 |
| Executable AST statements | 34124 | 35920 |
| Decision points | 11995 | 12576 |
| Local import edges | 423 | 536 |
| Initialization-cycle components | 0 | 0 |
| Cycle components including deferred imports | 3 | 3 |
| Retained historical UI functions, included above | 50 | 50 |
| Detected scoped lint findings | 558 | 555 |

The project has more modules, functions, statements, decisions and import edges.
This is not a global complexity reduction. The historical Tk file relocation
receives no complexity-removal credit. Catalog and shader literal spans are
reported separately from executable statements, and neither is judged by an
arbitrary physical-line threshold.

Some important remaining concentrations are explicit:

- `PolarRMWindow.__init__`: 287 statements and 273 direct `self` names, compared
  with 289 and 270 initially. Explicit construction ports do not make the window
  generally decoupled. Its module fan-out rose from 73 to 84; manager fan-out
  rose from 25 to 32. New collaborator edges remain visible.
- `InputDispatchMixin.keyPressEvent`: 189 statements / 112 decisions, compared
  with 183 / 109 initially. Additional lifecycle admission is real complexity,
  not removed from the counts because it protects a safety boundary.
- `Rm2026StateAdapter._apply_one`: 174 statements / 112 decisions, compared with
  176 / 82 initially; literal state writes decreased from 79 to 70. These mixed
  signals require judgment, not a single claimed improvement score.
- Both economy builders and the cached GPU-frame method remain substantial.
  Painter/GPU geometry unification is not credited before its separate evidence.

## Downward-only hotspot ratchet

Eight existing limits were lowered to reviewed measurements. The complete
[before/after record](../reports/clean_code/ratchet_f70bae1.json) records each
reason, the measured Git tree and source digest, and 24 rejected limit-plus-one
metric mutations. The limits do not add an allowance for a new large function.

Each tuple below is **statements / decisions / maximum control depth**:

| Existing symbol | Old limit | New limit |
| --- | --- | --- |
| `BackendManager._tick_polar_link_ready` | 191 / 134 / 4 | 27 / 10 / 1 |
| `apply_registry_config` | 114 / 61 / 4 | 6 / 3 / 1 |
| `ConfigActionsMixin.handle_config_action` | 132 / 64 / 4 | 7 / 4 / 1 |
| `ConfigActionsMixin.handle_qualification_action` | 142 / 74 / 3 | 8 / 2 / 1 |
| `EconomyMixin.build_gpu_economy_workspace` | 240 / 90 / 3 | 218 / 80 / 3 |
| `EconomyMixin.draw_economy_overlay_compat` | 221 / 73 / 3 | 199 / 61 / 3 |
| `PolarRMWindow._paint_gpu_frame_cached` | 226 / 92 / 5 | 167 / 69 / 5 |
| `PolarRMWindow.on_tick` | 111 / 55 / 2 | 11 / 1 / 1 |

`paint_overlay` remains 40 / 20 / 2. Original baseline provenance, lint allowances,
initialization-cycle limits, and dependency-boundary checks are unchanged.
The snapshot passes the smaller limits. Future growth must fail rather than
silently regenerate a permissive baseline. Later verified reductions may lower
limits again.

The reduced entrypoints delegate to tested policy values, configuration
transactions/command adapters, timing, presentation and ownership components.
Their replacement code is still included in whole-tree totals. Relevant behavior
is exercised by policy characterization/differential replay, configuration
command/transaction tests, and window stage-order/normal-frame/lifecycle tests;
small entrypoint metrics alone are not evidence of preserved behavior.

## Compatibility and reproducibility boundaries

The explicit window API contract protects reviewed public members/signatures and
caller-supported private seams. The full historical private-field snapshot is
retained as an explicit opt-in diagnostic, not silently passed. Removed protected
members, changed signatures and ordinary Enum-to-scalar default changes have
negative checks. A matching manifest does not prove full application compatibility.

The real-window normal-frame smoke runs production tick and CPU paint stages,
including pending/resumed admission. Only the terminal window painter device is
redirected to a `QImage`. This qualifies that offscreen raster path, not native
OpenGL, a desktop compositor or a physical display.

Verification records installed versions using metadata without importing optional
SDKs. Missing Git/metadata is explicit. Source-at-start identifies the committed
tree and dirty state, not a digest of ignored resources or a guarantee that
source stayed unchanged throughout execution. Public baseline reconstruction is
offline and content-checked; unavailable history stays unavailable, with no
fetch or fixture rewrite. See [quality gate guidance](quality_gates.md).

A tracked-file check found no runtime `tmp*`, `user_data`, `logs`, `field_evidence`,
`build`, or `dist` paths and no tracked `.log`, `.jsonl`, or `.pyc` files. Reviewed
fixtures and historical reports remain intentionally retained. No runtime logs
or genuine field evidence were deleted to obtain this result.

## Reproduction and outstanding acceptance

Use an installed environment compatible with the client. Static commands require
only Python's standard library:

```sh
python tools/verify_all.py --list --profile all
python tools/quality_gate.py --json tmp_quality/architecture.json
python tests/test_test_registry.py
python tests/test_quality_gates.py
python tests/test_runtime_source_classification.py
python tests/test_verification_provenance.py
```

For the exact measured runtime snapshot, additionally require its recorded digest:

```sh
python tools/quality_gate.py \
  --expect-source-sha256 433636b750e4a50c654637ce6df0677b7db519bd33bbd9c534140922b2c5dd78
```

The next stable integrated aggregate must be run and recorded before replacing
this draft's status. It must include all subsequently integrated tests and retain
unavailable firmware/reference evidence. Real Windows packaging, desktop GL,
DWM/fullscreen, audio devices, camera/driver behavior and real robot/firmware
acceptance remain outside the local static/offscreen results. Freezer telemetry
checks used inert PyInstaller APIs and a static PowerShell scope check; no real
freeze or freezing-time external request was reproduced.
