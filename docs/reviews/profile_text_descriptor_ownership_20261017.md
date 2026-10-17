# Profile text-writer descriptor ownership

Reviewed/reproduced production baseline: `f457c6d`. This closes the specific
text-writer gap deliberately excluded from
[the earlier preference descriptor correction](preference_descriptor_recovery_20261016.md),
not all configuration transaction or persistence boundaries.

## Reproduction

`ClientConfigStore.save` and `save_startup_preferences` call the separate
`config_store._atomic_write` text writer. Real `mkstemp` allocates a descriptor;
when injected `os.fdopen` rejection prevents stream adoption, the old writer
removes the temporary path but leaves the descriptor open (`os.fstat` succeeds).
Its cleanup also suppresses an unlink failure, losing evidence of the remaining
file. These are ordinary failure-injection results, not a claim that successful
standard-library stream creation leaks descriptors.

New `tests/test_profile_file_descriptor.py` ran seven methods on the unchanged
baseline: **3 failures, 3 errors, 1 pass**. The failing assertions require closed
descriptors; errors expose absent structured cleanup reporting. Fixtures release
real descriptors left open by deliberately rejected cleanup.

## Narrow correction

Until `fdopen` returns, the writer owns the raw descriptor and attempts its close
on rejection. After adoption, only the stream context manager closes it. The
existing UTF-8 text encoding, LF newline handling, temporary name, flush/fsync,
atomic replacement and successful return behavior remain unchanged.

Existing `FileWriteError` retains an original error plus failed descriptor close
or pathname cleanup. Nested failures retain all three causes. Pathname cleanup
is independent of raw-descriptor close. A failed close is reported without a
blind retry against a possibly reused descriptor. No new public operation or
profile format is introduced. `save_with_startup` retains its existing separate
`ProfileSaveError` and exact-byte rollback contract.

## Focused verification

Prepared interpreter: `../client-verification-venv/bin/python`.

```sh
python tools/verify_all.py \
  --test tests/test_profile_file_descriptor.py \
  --test tests/test_profile_file_transactions.py \
  --test tests/test_preference_file_descriptor.py \
  --test tests/test_configuration_boundaries.py \
  --test tests/test_profile_save_contract.py \
  --test tests/test_profile_save_identity.py
```

Six selected scripts passed, **88 unittest methods**, zero skips/incomplete/fails;
source fingerprint remained stable. The seven new tests include successful
Unicode/LF persistence without raw double-close, failed admission, independent
cleanup failures, and recovery of both original files after first-write rejection.
`git diff --check` passed. Raw red/green and selected aggregate logs stay local;
no full regression was run by this worker.

This is single-writer, ordinary-exception ownership evidence. It does not provide
crash-atomic two-file saving, concurrent writer arbitration, Windows/native device
qualification, or exhaustive rollback of every runtime/UI publication phase.
Independent review and final integrated regression belong to separate checkpoints.
