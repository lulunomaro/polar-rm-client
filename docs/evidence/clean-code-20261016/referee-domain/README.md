# Referee domain boundary evidence

## Frozen checkpoint, before production changes

Source checkpoint: `71ebb0f570441c12eb0d15b0a992c9cc3e805aeb`.
`tests/test_referee_domain_characterization.py` records seven exact file digests
and refuses fixture generation from changed production files. Ordinary tests
only read `tests/fixtures/referee_domain_baseline.json`.

Frozen observations:

- Both profile descriptors, codec identities, message sets, uplink cadence,
  34 client-ID validations, 140 publish decisions, unsupported-profile errors,
  and actual annual adapter type/host identity.
- Constructor signatures/field order and AST hashes for twelve concrete value,
  scenario, transaction, factory, and projection classes.
- 90 annual offer cases, including malformed source values; every offer field
  is retained, not just names or prices.
- Sixteen actual `ScenarioManager.apply_to_registry` calls on real production
  registries. Assignment traces retain three injected write-failure boundaries
  and the resulting partial registry state.
- Twenty-four actual `EconomyCommandBridge.submit` calls across official,
  student, and unknown profiles, both command topics, and four sender outcomes.
  Each captures encoded bytes, sequence/history/last-request state, sender calls,
  and exceptions so an interrupted side-effect reorder cannot pass unnoticed.
- Read-only/unknown profile precedence, including a copied default profile with
  `read_only=True`, preserves current behavior rather than widening semantics.

Validation before extraction: `python tests/test_referee_domain_characterization.py`
passes all eight unittest methods. No Qt, transport, hardware, user configuration,
or network operation is used. Existing projection/catalog/API fixtures are unchanged.

## Metadata, value, and runtime composition boundaries

- `rm_client.referee_profiles` now owns immutable reviewed identities, endpoints,
  QoS/read-only metadata, supported ordering, and unsupported-profile rejection.
  `model` and `scenarios` consume those values without loading the backend.
- `backend.referee.registry` binds that same metadata to reviewed codecs and
  annual adapter/rule factories. Existing `RefereeProfile` fields, constructor,
  methods, factory decision order, codec cadence, and public import names remain.
  Source-visible annual imports remain available to dependency scanners.
- `rm_client.economy_values` owns immutable `EconomyContext`/`EconomyOffer`.
  Annual policy consumes these values without importing the transaction owner.
  `economy` re-exports the same class objects and keeps `EconomyAction` as an
  alias. Historical pickle globals resolve through those compatibility imports.
- The state adapter still imports the real `RobotState`; runtime type-hint
  resolution continues to work. No `TYPE_CHECKING`, dynamic import indirection,
  proxy host, codec change, UI constructor, or renderer edit closes the cycle.

These are actual one-way dependencies: metadata -> no backend; annual policy ->
values; transaction -> registry -> annual policy -> values; adapter -> model ->
metadata. The pre-change snapshot fails both import-blocking counterexamples
(model importing the backend and annual policy importing the transaction module).
The changed tree executes real catalog/scenario/annual-offer calls with those
imports blocked. Patching metadata before assembly also changes backend and
scenario values together, detecting accidental duplicate hard-coded defaults.

`tests/test_referee_domain_boundaries.py`: ten methods cover the independent
imports, immutable shared metadata, legacy type/pickle aliases, real type-hint
resolution, factory failures/recovery, annual-rule failure/recovery, and the
complete static graph including deferred imports. That graph now has zero
initialization cycles and only the separately documented compatibility-entry
cycle (`ui.polar.app_entry` / `ui.polar_app`). No quality/API baseline was relaxed.

### Focused validation after extraction

Interpreter: `/workspace/shared/client-quality-venv/bin/python` (Python 3.12.14,
PySide6 6.8.2). Environment: `CI=true QT_QPA_PLATFORM=offscreen
POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1`.

- Seven focused suites: 67 unittest methods PASS. Domain characterization,
  dependency boundaries, projection characterization/policy, default catalog,
  projectile baseline, and actual backend video/profile-change failures.
- Five adjacent suites: 66 unittest methods PASS. Transport safety, economy
  confirmation/presentation/localization, and real scenario configuration guard.
- Referee integration: 263 checks PASS, localhost only.
- Backend protocol: 198 checks PASS, including local synthetic decoder paths.
- Existing API freeze: PASS; unchanged fixture (1,114 established members,
  540 signatures, 78 explicit private caller seams).
- Static quality gate: PASS; no baseline update. `git diff --check`: PASS.

An initial adjacent-suite invocation omitted the tests helper path and named one
nonexistent test module, producing three import errors. It was corrected using
`PYTHONPATH="$PWD/tests:$PWD"` and the existing suite names; no test was weakened,
skipped, or edited to suppress the command error. Offscreen Qt audio/native-GL
warnings do not constitute desktop GPU, device, Windows freeze, or field evidence.
The parent still owns final combined-tree verification and publication.
