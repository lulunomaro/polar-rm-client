# Same-metric architecture comparison — 2026-10-17

## Finding

The quoted **33,597 → 37,377** executable-statement comparison is reproducible:
it compares the PR base `b26b7b7` with earlier PR19 snapshot `6b661c0` using the
same current measurement implementation. At the requested current snapshot
`a4d8774`, the count is **37,434**. These are static function-scoped AST counts,
not executed instructions, physical lines, performance measurements, or a quality score.

Import cycles were removed and selected functions became smaller. However,
local import edges and the central window/manager import fan-out increased.
The evidence therefore supports specific improvements, **not a blanket reduction
in coupling or whole-project size**. Counts alone do not justify a rewrite.

## Fixed inputs and method

| Snapshot | Exact commit |
|---|---|
| PR base | `b26b7b718e348fbda066412f6d86140727f32e73` |
| Earlier PR19 comparison endpoint | `6b661c056cf2cc3501386755a71b72bd6bc0f90f` |
| Current measured snapshot | `a4d8774334586572d17ae06370170606a390b8b3` |

Each snapshot's `rm_client` tree was exported with `git archive` into a temporary
directory, then parsed by the **same** checkout implementation of
[`tools.quality.metrics.measure_tree`](../../tools/quality/metrics.py).
No checkout, application import, hardware access, or repository mutation was
needed for measurement. This report is the only artifact added by this task.

Measurement implementation SHA-256:

- `tools/quality/metrics.py`: `548bcabd7b664a3468d566a0608e40b25ea32ec5bba955c3e22e511481841847`
- `tools/quality/runtime_sources.py`: `e0d560401d7e0eb2b05cdb30c2f4b89db3d58ee2625c7d8dd3da103377d855e7`

Scope is `rm_client/**/*.py`, excluding hidden/cache paths. All discovered files
parsed successfully. Function measurements exclude nested scope bodies from the
outer function and count nested functions separately; string-expression statements
are excluded. Tests, tools, documentation, and evidence logs are outside this scope.
Import edges and fan-out count unique local module targets, including deferred
imports and excluding `TYPE_CHECKING`-only edges. Cycle counts below are numbers
of strongly connected components reported by the existing tool.

## Comparable results

| Same metric | PR base | Earlier PR19 | Current |
|---|---:|---:|---:|
| Parsed Python files | 139 | 221 | 221 |
| Functions | 2,589 | 3,464 | 3,466 |
| Function-scoped executable AST statements | 33,597 | 37,377 | 37,434 |
| Decision count | 11,796 | 13,164 | 13,187 |
| Local import edges | 419 | 615 | 616 |
| Eager non-type-only import cycle components | 0 | 0 | 0 |
| Including deferred non-type-only import cycle components | 3 | 0 | 0 |
| `rm_client.ui.polar_app` import fan-out | 72 | 94 | 94 |
| `rm_client.backend.manager` import fan-out | 25 | 37 | 37 |
| Historical Tk implementation functions | 50 | 50 | 50 |

Current versus PR base: **+3,837 statements**, **+197 local import edges**,
window fan-out **+22**, manager fan-out **+12**. Current versus earlier PR19:
**+57 statements**, **+1 edge**, unchanged central-module fan-out.

Examples of localized improvement from PR base to current:

- `BackendManager._tick_polar_link_ready`: 191 → 27 statements; 134 → 10 decisions.
- Window `__init__`: 289 → 247 statements; 8 → 2 decisions.
- Entire window module: 2,194 → 2,002 statements, while fan-out rises 72 → 94.
- Entire manager module: 1,219 → 1,373 statements, while fan-out rises 25 → 37.

These function reductions do not establish that responsibility disappeared:
extraction can relocate work and add dependencies. The original three deferred
cycle components involved the model/economy/scenario/adapter group,
`ui.cs2_settings_center` with `ui.jello_keybinds`, and
`ui.polar.app_entry` with `ui.polar_app`.

## Source fingerprints and baseline distinction

The metric's `source_tree_sha256` values (normalized decoded Python source,
not Git tree IDs and not full-regression fingerprints) are:

- PR base: `5a04ad95267f31229029c8c6a0e00f446caff8640e6ee990bc568c2b3eced2aa`
- Earlier PR19: `83a5900d96c4f6f567f3c9254484520fd36732542ff976c0ea9aca3ee795a5df`
- Current: `fa4ef91a7abe37a1154ea6bb5e4cda8ad57e5a408a3e586fa59d68c3c1013c5d`

The separately documented quality-gate baseline in
[`docs/quality_gates.md`](../quality_gates.md#reproducing-the-initial-source-measurements-offline)
is `d64f1528e2e1864ef369537c026b6d3f7127f590`, not the PR base above.
The same tool measures that snapshot as 141 files, 2,619 functions,
34,124 statements, 11,995 decisions, 423 edges, window fan-out 73,
manager fan-out 25, and 0 eager / 3 including-deferred cycle components.
Its source digest is
`cd432aed2f9188765812905cab9e76a61aac6c36980d6f8bec3100d91138aca5`.
It must not be substituted for the 33,597-statement endpoint.

## Limits and acceptance meaning

Historical Tk remains included: its move from `rm_client.app` to
`rm_client.legacy_tk_app` is not credited as deleting its implementation.
The historical-function label excludes the old forwarding `main`; the total
function metric still measures all discovered functions. Current imports contain
six unique type-only module-target pairs, five exclusively type-only; the PR base
has none. These are not silently added to the non-type-only edge totals.

AST import graphs do not cover dynamic imports, reflection, runtime callback
routing, inherited mixin state access, or all ownership relationships. Acyclic
imports do not prove low coupling. These measurements do not validate runtime
behavior, latency, device safety, or physical operation, and they do not replace
the RobotState authority ledger or focused regression results. The defensible
acceptance statement is: **specific cycle/hotspot improvements are verified;
overall central dependency reduction remains unproven and these static fan-out
measures increased.** No raw measurement JSON or logs are published by this report.
