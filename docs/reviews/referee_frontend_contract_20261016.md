# Referee frontend authority: characterization and residual assessment

Fixed parent source: `66deab1`. Characterization checkpoint: `cb1b149`.
No production change is proposed in this stage. Annual codecs, `_apply_one`,
existing fixtures, API manifests and gate budgets remain unchanged.

## Evidence added

The full `sync_frontend_state` method is frozen as exact dedented source and
protected by SHA-256. Eleven tests pass before any replacement. They cover:

- Empty authority does not publish/clear existing projections.
- Local explicitly received zero HP overrides stale positive global HP in both
  roster and map; missing local HP and nonlocal HP keep their original fallback.
- Blue alliance/robot-ID mapping, short arrays and sorted extra observed IDs.
- Duplicate positions retain the first key order and last value.
- Row modules preserve the authoritative dictionary reference, not a deep copy.
- Rows publish before a later map conversion failure; rows and map publish before
  a later buff conversion failure. The synced flag is already set in both cases.
- Map authority is reobserved after row publication. A setter-triggered dynamic
  update can yield row HP 0 and map HP 77 in one call; extraction must not silently
  flatten this into a single eager snapshot.
- Empty positions/buffs retain old projections and numeric errors retain their
  existing failure stages.
- 222 injected state observation/publication failures compare complete trace
  prefixes, exceptions and partial state with the frozen method.

Combined run with existing referee state characterization and domain boundary
suites: **28 methods PASS**, no skips. Log:
`docs/evidence/clean-code-20261016/referee-frontend-contract/focused.log`.

```
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest test_referee_frontend_projection_contract test_referee_state_characterization test_referee_domain_boundaries -v
```

These synthetic malformed/reentrant cases describe behavior, not newly proven
ordinary-user bugs. The existing `_apply_one` fixture characterizes decoded
message projection but does not call this frontend projection method. Existing
backend integration tests cover normal frontend rows; the added tests cover
its previously uncharacterized phase and observation contracts.

## Residual scope and decision

`sync_frontend_state` combines annual roster interpretation and three frontend
projections. Local-versus-global health precedence is duplicated in row/map
construction. However, extracting only the shared HP expression would be a small
wrapper without a meaningful new owner. A larger detached roster snapshot would
alter observed reads and phase failure/publication semantics, while preserving
those semantics through many callback accessors would merely relocate mutable
state coupling. No new source defect or duplicate authority policy large enough
to justify that machinery was established. Therefore this stage stops at tests.

A future substantive change needs a genuinely useful annual roster value contract
and explicitly phase-local observations, with these tests unchanged. It must keep
alliance role ordering, local-zero authority, absent data, references and read
prefixes; it must not precompute/validate every phase or return one atomic combined
projection result. It should reduce policy duplication, not just shorten the
adapter. Broad schema dispatch, generated decoders and `_apply_one` rewrites are
outside this recommendation.

Wire obligations remain exact field numbers/types, signed/packed representation,
presence metadata, explicit-zero behavior, malformed payload rejection and
legacy roster quarantine. Annual codec/adapter separation already has evidence;
this stage does not redo it or claim whole-client cleanliness. Native/physical
referee interoperability and desktop rendering qualification remain absent.
