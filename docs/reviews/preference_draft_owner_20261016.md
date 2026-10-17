# Preference draft ownership

Baseline: `fa2c2b8397295d27ada5d105854b751b7dd0940d`.
Characterization commit: `89cc633` (production unchanged).

## Accepted scope

`RegistrySettingsPage` previously owned both Qt widget effects and the pending-edit
state machine. `PreferenceDraft` now owns accepted values, explicit reset intent,
clean/pending decisions, and completion acceptance without Qt, model or backend
imports. This is an ownership seam, not a claim of source reduction: production
adds a 44-line owner and four net adapter lines. The page still owns live registry
and audio observations, widget normalization, transaction dispatch and effects.
No visual/layout construction, keyboard handling, transport authority, setting
validation, default values, or transaction semantics changed.

The existing `_baseline` read seam is retained as a read-only mapping for tests
and diagnostics. Repository callers do not assign it. The old private
`_forced_dirty` state had no outside callers and is now private to the owner.

## Preserved observation/effect contracts

- Incomplete pages neither read drafts nor reset them; apply still rejects.
- Forced reset fields skip widget reads during refresh, even if their defaults
  equal the prior baseline. Every reset field is forced before widget writes.
- Clean fields follow live values; baseline records the widget-normalized value.
  Pending edits retain their old baseline until successful apply.
- Apply captures widget values before callbacks. Reentrant callback edits remain
  pending against that captured snapshot; failed apply retains reset intent.
- Live read/write order, category partition order, audio validation and offline
  device insertion remain in the unchanged adapter sequence.

## Reproduction and evidence

Use the hash-pinned verification environment, `QT_QPA_PLATFORM=offscreen`:

```
python -m unittest discover -s tests -p test_registry_draft_contract.py -v
python -m unittest discover -s tests -p test_preference_draft.py -v
python -m unittest discover -s tests -p test_cs2_settings_center.py -v
python -m unittest discover -s tests -p test_lazy_settings_state.py -v
python -m unittest discover -s tests -p test_settings_dialog_service_wiring.py -v
python tests/api_freeze_test.py
python tools/quality_gate.py
```

Eight characterization tests pass before replacement and unchanged afterward.
Candidate runs pass 55 test methods across those five modules (8 + 7 + 13 + 21 +
6), API freeze and static quality gate. Logs are in
`docs/evidence/clean-code-20261016/preference-draft/`. The pure tests actively block
Qt/model/backend/Tk imports. Native GL/audio warnings remain in offscreen logs;
these are not physical device, native rendering, packaging or performance claims.
The parent must separately validate its fixed integrated candidate.
