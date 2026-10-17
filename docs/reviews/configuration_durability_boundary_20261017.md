# Configuration persistence boundary follow-up

Baseline: `a4d8774`. This is a bounded follow-up to the participant and profile
text-descriptor ownership reviews. No new runtime defect was confirmed.

## Correction

The comment in `ClientConfigStore.save` previously promised that power loss or
process termination must leave either the old or new valid profile. The actual
writer flushes/fsyncs its file and performs `os.replace`, but does not fsync the
containing directory or implement a crash-recovery protocol. The comment now
states atomic replacement under normal OS operation and explicitly declines
power-loss/crash durability. This is a documentation correction, not a newly
implemented durability guarantee.

Only that comment changes. Validation, UTF-8 serialization, temporary-file
ownership, flush/fsync/replace ordering, returned path and exception propagation
remain unchanged. The existing `save_with_startup` documentation already states
that its two-file bundle is not crash-atomic and that recovery covers caught
ordinary exceptions with a single writer; those semantics are retained.

## Reviewed boundaries

- Profile validation and migration before live registry application; imported
  data validation before backup/replacement.
- File snapshots, independent failed rollback reporting, and raw-descriptor
  ownership already fixed in the text writer.
- Pending generation handling, stopped runtime restoration and fail-closed
  cleanup, without treating pending restoration as completion.
- Accepted profile identity, save-as draft protection, autosave debounce and
  manual-close retry. Existing tests explicitly characterize partial publication
  at selector/clock/notification boundaries; those behaviors were not silently
  redefined as globally atomic transactions.

The review does not establish exhaustive setter-failure recovery, concurrent
writer arbitration, crash durability or physical device completion. No new
framework, public API, test relaxation, or speculative production fix was added.

## Verification

Using `../client-verification-venv/bin/python tools/verify_all.py` with:

- `tests/test_configuration_boundaries.py`: 18 tests
- `tests/test_pending_configuration.py`: 4 tests
- `tests/test_profile_file_descriptor.py`: 7 tests
- `tests/test_profile_file_transactions.py`: 10 tests

Selected result: **4 PASS, 0 SKIP, 0 INCOMPLETE, 0 FAIL**, 39 methods, stable source
fingerprint. `git diff --check` passes. No full suite or hardware test was run.
The selected JSON report/logs remain local under `/tmp/config-boundary-review*`.
