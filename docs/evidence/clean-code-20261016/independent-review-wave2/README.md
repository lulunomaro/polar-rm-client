# Independent second-wave review

Fixed-source review of quality guard chain `528b5b1` → `a603171` → `9fb0cb6`
and map chain `add1fb6` → `db5a4df` → `9a585e2` → `93981b8` → `184db55`.
Exact commands, source revisions and hashes are in `manifest.json`. The shared
interpreter was used in separate detached reviewer worktrees; no aggregate lock
was acquired. One initial quality invocation ran at the author's unchanged
worktree, then the full command was repeated in the reviewer worktree; only the
detached rerun is retained as independent evidence here.

The quality changes lower three existing budgets, add an explicit full-import
cycle guard and protect two pure domain roots. Original source identity, other
limits, API and behavior fixtures are unchanged. Real source-mutation and
metric-report negative controls retain distinct purposes.

The map frozen module exactly equals all 143,601 bytes of original `71ebb0f`,
SHA-256 `3a4d7cdef2cf3299da9b32f390538430e9a5e643e62e1db967258a63a0e09b89`.
Fifteen of sixteen original test/helper function ASTs stay exact; the remaining
radar method only adds two malformed-late-row cases, without removing old
assertions. A later negative control isolates frozen class constants.
Independent unchanged-source tests pass before extraction. Per-marker error
boundaries, typed values and adapters retain existing command/painter effects.
Map drawing/status/menu/cache ownership remains broader than these accepted
geometry/observation seams. Native OpenGL is not established.

Review caught a trailing space in a committed author log, so the author's prior
working-diff check was not a full baseline-range check. Docs-only `184db55`
normalizes that single log line, records its original raw hash and corrects the
claim. The full range then passes. No frozen source or production changed.

The retained residual probe documents two new bounded counterexamples that the
parent assigned to separate authors: admitted DT7/CC constructor late failures
leave an unsealed owner, and MQTT malformed mutable-registry ports escape the
intended fallback. The first is defensive fault injection, not a claim that
ordinary CPython start normally raises after admission. The second does not
prove normal UI sliders can produce malformed registry values. The probe closes
all captured workers and does not start network/device traffic.

An additional negative control runs the accepted checker against the actual
original production tree (test-only add1fb6). Both historical deferred SCCs
are rejected while initialization SCCs remain zero; the three lowered budgets
reject seven measured dimensions above their new caps. This is expected
exit 1, not a current candidate failure.
