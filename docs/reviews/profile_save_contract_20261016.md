# Profile-save ownership: pre-extraction contract

Baseline: `cd80ec74955639b1a4b529c979b462cd50cd7887`.
Scope: `PolarRMWindow` / `PersistenceMixin` accepted identity, draft, pending
save, deadlines, and manual-close retry state. This stage changes no production
code, old fixture, verification registry, or aggregate report.

`tests/test_profile_save_contract.py` adds 29 actual-window tests. Its fixtures
use real Qt/registry/lifecycle adapters, a temporary user-data directory, stopped
timers, and inert device effects. Storage methods are replaced where a precise
save effect/exception is needed. The switch test also uses a temporary profile
file so the real profile-list refresh can see its target.

## Order and ownership that the extraction must retain

- Lazy identity resolution reads the current selection only when uncached; a
  failed selection read publishes no identity. A nonempty draft short-circuits
  identity resolution. An empty draft still counts as dirty but is falsey for
  restorable-name fallback.
- Acceptance increments generation even for the same name. It publishes the new
  identity, clears the draft and manual-close retry flag, then writes selection;
  only after that succeeds does it discard pending ownership. The old deadline
  and error marker are not cleared by acceptance itself.
- Scheduling reads selection, then accepted identity for comparison, publishes
  pending, reads accepted identity again for ownership, and finally samples the
  clock. The deadline is replaced, not accumulated. The eventual write exports
  the current live values rather than a queued snapshot.
- Due-time checking precedes stale-generation rejection. A missing pending owner
  falls back to the accepted identity. A forced flush sets deadline zero even
  when pending configuration, draft protection, or manual retry blocks writing.
- Failed accepted writes restore selection, publish pending and its owner, read
  the retry clock, emit a warning when applicable, then set the error marker.
  Clock/selection/toast exceptions retain the corresponding partially published
  state. Failed save-as writes publish draft identity before touching selection,
  then discard pending ownership, without sampling the retry clock.
- Successful writes finish acceptance where required, clear pending/error/retry
  state, then refresh profile options and optionally announce. Refresh or
  selection exceptions retain the state already published before those effects.
  `TypeError` from the write boundary propagates without becoming an ordinary
  caught `OSError`/`ValueError` save failure.
- Close marks manual retry before attempting export/write. Refused close pauses
  automatic writes; each explicit close attempt retries once with current live
  values. A draft target outranks stale pending ownership. Without a draft,
  stale pending ownership is discarded without clearing retry/error history.
- Profile switches flush the old live owner before requesting runtime changes.
  Accepted identity advances only after matching completion. Same-name staged
  reloads retain dirty ownership until completion and reject dirty close.

The exporter omits `ConfigManager/Profile`. The real profile-option refresh can
normalize a selected name absent from storage without changing accepted identity.
Consequently a mock successful write must not be assumed to add a stored name.
These registry/export/selection/refresh effects belong at the adapter boundary;
combining all state updates into one atomic owner operation would change failure
and observation points.

## Bounded defensive re-entry observations

Three tests inject callbacks at identity lookup, clock sampling, and the storage
call. A nested queue during the second identity read can be replaced by the
outer captured owner; a nested clock queue can keep its owner while the outer
clock replaces its deadline. A successful write return currently clears a newer
nested pending save while retaining the newer accepted identity and deadline.

These are passing characterizations of the fixed baseline, **not expected-failure
tests or established ordinary-user defects**. The actual synchronous
`ConfigStore.save_with_startup` has no event pump or callback contract. No
production route for these injected re-entry callbacks was established. A new
generation guard would be a separately justified behavior change, not a silent
part of this extraction.

## Verification

Run from the worktree with:

```sh
CI=true QT_QPA_PLATFORM=offscreen POLAR_DISABLE_LOCAL_CAPTURE=1 \
POLAR_DISABLE_MEDIA_PRELOAD=1 XDG_CACHE_HOME=/tmp/polar-window-cache \
PYTHONPATH=tests /workspace/shared/client-quality-venv/bin/python -m unittest -q \
  test_profile_save_contract test_profile_save_identity \
  test_profile_close_admission test_configuration_transactions
```

Result: **71 tests PASS**, zero skips, failures, or errors; the new suite alone
is **29 PASS**. These overlapping counts must not be added together. Four
in-memory negative controls each caused one assertion failure and zero errors:
clearing pending before selection, removing the second identity read, setting
the save-error marker before toast, and adding a newer-generation success guard.
Production files stayed unchanged during those checks.

This is focused baseline evidence, not aggregate acceptance. Offscreen Qt emits
expected unsupported-OpenGL/no-audio-device diagnostics. Native GUI/GPU, physical
devices, network peers, concurrent writers, and cross-file crash atomicity were
not qualified. The integration owner must register and rerun the suite on the
extracted, assembled tree.
