# Tactical map geometry and telemetry boundaries

## Source and pre-change characterization

The source base is `71ebb0f`. Before any production edit, the exact 143,601-byte
`rm_client/ui/polar/renderers/tactical_map.py` was retained as
`tests/fixtures/tactical_map_71ebb0f.py.txt`, SHA-256
`3a4d7cdef2cf3299da9b32f390538430e9a5e643e62e1db967258a63a0e09b89`.
The reference is immutable test input, not an alternate runtime implementation.

`tests/test_tactical_map_contract.py` passes 11 test methods against unchanged
production source. Its separate Qt recorder runs the real `PolarRMWindow`
methods and real `QImage`/`QPainter`/font/path operations. Both complete rendered
pixels and ordered Python paint calls are compared with the frozen reference.
Calls performed internally by Qt text layout are covered by pixels rather than
claimed as independently intercepted Python commands.

The initial suite covers 24 complete map scenes across classic/liquid drawing,
RMUC/RMUL fields, red/blue sides, opening/closing progress, density, four status
styles, logical sizes, external scale and DPR; two missing-asset scenes; sampled
paint-failure prefixes; Tab repeat/reversal/resize; the two separately observed
Minecraft policy reads; 192 projection/click cases; command gates and failures;
radar units/TTL/malformed input; penalty order and buffs; rounded image sampling,
cache identity and allocation failure. These are bounded examples, not a
percentage of total coverage or a desktop GPU qualification.

Reproduction (no aggregate verifier lock):

```
XDG_CACHE_HOME=/tmp/map-contract-xdg QT_QPA_PLATFORM=offscreen \
  /workspace/shared/client-quality-venv/bin/python tests/test_tactical_map_contract.py
```

The saved pre-change output is
`docs/evidence/clean-code-20261016/tactical-map/characterization.log`.
Native audio/offscreen warnings are environment observations, not skipped tests.
No network/device starts are requested. Window construction and asynchronous
final cleanup remain real; time and animation progress are controlled.

## Scope and retained responsibilities

The existing `MapIslandState` remains the animation owner. Constructor,
input-dispatch, cached GPU rendering and backend command authorization are
outside this change. The map adapter will retain registry observation, ordered
effects, painter/image lifetime, cache publication and all existing public
method names. Pure geometry and telemetry values will receive only explicit
inputs, with no window, registry, Qt painter or backend references.

The liquid render path's existing click-geometry publication behavior is part
of the frozen reference; extraction does not silently change it. This suite
does not establish that all existing map behavior is correct.

## Geometry extraction checkpoint

`rm_client/ui/tactical_map_geometry.py` supplies immutable `MapExtent`,
`NormalizedMapPoint`, `MapViewport` and `RoundedMapSampling` values. Target
sizing, projection/inverse projection, corner-mask admission and sampling/key
math are now independently executable without Qt, model, backend, renderer or
vision imports. The module lives outside the eager legacy `polar` package so
this independence is true for ordinary Python imports on this source base.

The adapter keeps both Minecraft observations, normalization before mirror
settings, all click guards, backend invocation/rounding/toast order, lazy cache
creation, transform/DPR observation, native image allocation and cache eviction.
`MapIslandState`, window fields and input dispatch are unchanged. This is not a
claim of full map-renderer or status-row separation.

Author checks: 11 unchanged reference-contract methods, eight pure-geometry
methods (including 2,000 seeded projection/inverse cases), actual API freeze,
static quality gate and `git diff --check` all pass. Saved outputs have the
`geometry-` prefix in the evidence directory. No public/visual/static baseline
or fixture was updated.

## Telemetry value extraction checkpoint

`rm_client/ui/tactical_map_telemetry.py` now owns radar unit normalization and
freshness/opacity, immutable single-marker observations, latest/active penalty
selection, own-robot active buffs and valid special mechanisms. The functions
receive explicit scalar/mapping/sequence values; returned records are immutable
and detached from the source mappings. They neither consume nor change source
telemetry and do not query settings or authorize command publication.

Radar decoding remains per marker, inside the original exception boundary.
Late malformed markers retain earlier paint/hotspot effects. Highlight settings
are still read only for highlighted rows; a handled conversion/setting error
skips that row, while overflow/attribute/runtime failures remain observable.
Penalty ordering, deduplication, count fallback and caps are unchanged. Buff
settings remain separately queried in the adapter; missing mock provenance,
foreign robot IDs and inactive rows are still filtered before duration parsing.
The public timeline result remains a list of ordinary tuples.

The original 11 reference tests are retained, with two additional methods for
source-replacement/cache-eviction/DPR failures and per-marker callback failures.
13 reference-contract + eight geometry + ten telemetry methods pass. Nine
existing neighboring script suites also pass, along with actual API freeze,
static gates and diff checks. Separate scripts use different method/check
counting conventions and are not added into a single coverage total. All three
new test suites are discovered by the normal test registry. Evidence uses the
`telemetry-` prefix. The frozen module and all older fixtures remain unchanged.

The map family still owns broad drawing, status-row UI assembly, event-dialog
routing and cache lifetimes. Those residual responsibilities are explicit; this
checkpoint makes no claim to complete separation of that whole family, physical
robot behavior, native GPU rendering or performance improvement.

## Reference isolation hardening

A test-only follow-up binds frozen class constants as well as frozen methods.
Previously, the paired scene harness used current window constants in both
arms. The extraction did not change those constants, but future constant drift
could have escaped the reference comparison. A negative control changes only
the current full-map threshold and proves the reference remains independently
true while the candidate becomes false. Radar diagnostic fields are now part
of complete-scene observations. All fourteen contract methods pass; production
source and the frozen source file are unchanged by this hardening.

Independent review found that the initial working-diff check did not cover a
then-untracked evidence log. Its line 61 contained one trailing space; the
baseline-to-candidate diff check therefore failed even though production/tests
passed. The follow-up removes only that trailing whitespace and checks the
whole `71ebb0f`-to-candidate diff. The original unnormalized log remains in
`9a585e2`, SHA-256
`1f7d14a72286f0adff1ed8d18c9aea9bfea867dbf86bc963a82b1ea02b267258`.
