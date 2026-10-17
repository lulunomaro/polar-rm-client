# Audio attempted-write recovery ownership

## Reproduced gap

Base `66deab1` has a real-file interrupted-publication counterexample:
`atomic_write_bytes` replaces the audio preference file, then its final temporary
unlink raises. `AudioPreferenceChange` marked `_written` only after the helper
returned. Rollback therefore skipped the already changed file, while the outer
configuration transaction reported `restored=True` with no restoration errors.
A temporary-file probe observed disk volume 66→23 while that success claim held.

The injected PermissionError demonstrates the exception contract; it is not a
claim that ordinary local filesystems typically fail unlink after replacement.
A replace wrapper admitting the replacement before raising exposes the same
ownership gap. Test-first `86132da` has eight methods and six failures: the
post-replacement and retry cases, an intentional conservative pre-replacement
recovery expectation, and independent write/cleanup error preservation.

## Bounded correction

- A participant assumes recovery responsibility immediately before calling the
  fallible writer, after JSON preparation. A raised helper cannot establish that
  publication did not occur. Even a rejected pre-replacement write therefore
  conservatively attempts exact snapshot restoration and cache/pool refresh.
- Recovery responsibility clears only when file restoration and pool refresh
  both complete. If either fails, the outer transaction records a restore error;
  explicit rollback retry remains possible. Restored bytes alone do not turn a
  subsequent cleanup failure into reported restoration success.
- An unattempted participant or serialization failure before admission remains
  inert. It does not overwrite changes made by another owner after preparation.
- A sole write or cleanup exception retains its original identity. If both the
  write and temporary cleanup fail, `FileWriteError` (an OSError) retains the
  exact initiating exception in `cause` and `__cause__`, and the exact cleanup
  exception in `cleanup_error`. Both appear in its message. The outer transaction
  retains this structured failure as its cause; neither underlying error is
  discarded or relabeled as a successful write.

This is a **single-writer, ordinary-exception recovery contract**. It does not
provide crash durability, multiprocess atomicity, or arbitration between
concurrently admitted writers. It deliberately does not merge the config-store
text writer with the byte writer: their existing cleanup contracts differ.
The transaction orchestrator, validation, registry snapshot, public window API,
quality baseline and profile/startup pair semantics are unchanged.

## Evidence

Production correction checkpoint: `362133a`. A later source-docstring change
only states the single-writer boundary above.

- `test_audio_write_admission.py`: 11 methods pass. Existing bytes/absence,
  cache, pools and registry recovery; before/after replacement; missing temp,
  failed cleanup, dual failures, retry, unattempted competing owner, standalone
  compatibility save and success are explicit cases.
- 57 existing audio/configuration/profile-file/settings/draft methods pass.
- Actual API freeze removal/restoration mutation self-test and static gate pass.
- No original test assertion or budget was weakened. The eight test-first
  methods remain unchanged; three additional controls were added afterward.

Raw logs and hashes are under
`../evidence/clean-code-20261016/audio-write-recovery/`. Reproduce with:

```sh
QT_QPA_PLATFORM=offscreen python tests/test_audio_write_admission.py
```

The existing selection comprises `test_audio_configuration_transactions`,
`test_configuration_transactions`, `test_profile_file_transactions`,
`test_cs2_settings_center`, `test_registry_draft_contract`, and
`test_preference_draft` (with tests on sys.path). Tests use real temporary files
and mocked failure points; they do not open native audio outputs or qualify
power-loss behavior, storage hardware, or physical devices.
