# Independent preference draft review

Reviewed fixed candidate `218a89aa2db8d58c2d746939b6b8be03a8445dfa`
(production `9349099`, characterization `89cc633`) against
`fa2c2b8397295d27ada5d105854b751b7dd0940d`.

Verdict: PASS for the bounded pending-edit ownership extraction; no blocker.
This reviewer made no production changes to that lane.

Independently reran 55 methods from `test_registry_draft_contract`,
`test_preference_draft`, `test_cs2_settings_center`, `test_lazy_settings_state`
and `test_settings_dialog_service_wiring` together, then 19 existing methods
from `test_audio_configuration_transactions` and `test_configuration_transactions`.
All pass. Independently exported the exact baseline into a separate directory,
added the unchanged characterization module, and reran its eight methods there;
all pass before extraction as well. These are distinct runs, not an aggregate
whole-client qualification. Shared hash-pinned Python and Qt offscreen were used.

Reviewed production diff and callers:

- The adapter still captures values before transaction dispatch, accepts only on
  successful return, then refreshes. A callback's later widget edits remain
  pending relative to the accepted pre-callback snapshot.
- Reset records all forced keys before any widget writes. Refresh skips forced
  widget reads through the owner's lazy callback, retaining short-circuit and
  exception order. Failed apply cannot clear forced intent or update baseline.
- Clean fields still read live settings, normalize through widgets, then record
  the observed normalized value. Category partitioning and transaction effects
  remain adapter-owned in their original order.
- Repository `_baseline` callers only read/index/compare/copy it. The retained
  read-only mapping preserves those supported operations. `_forced_dirty` had
  no remaining external caller. Arbitrary writes to those old private internals
  are intentionally outside the declared compatibility seam.
- New owner copies the accepted dictionary and exposes a mapping view; ordinary
  widget values are scalar. No deep-copy or immutable nested-value guarantee is
  claimed, and no window-wide transaction semantics are changed.

Logs: `docs/evidence/clean-code-20261016/preference-draft-independent-domain/`.
Candidate's API/static reports were inspected but not independently rerun in this
review; parent fixed-source integration owns those checks. Native rendering,
audio hardware, packaging and physical devices remain unqualified.
