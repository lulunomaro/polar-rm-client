# Settings-page input quiescence review

Baseline: `666c040`; original actual-window reproduction: `37bf6d9`.

The settings adapter performed lock, release and pending-clear in sequence. If
release raised after marking input dirty, transaction recovery received dirty
input before its later fail-closed cleanup. The red test observed `[True]` at
`restore_configuration_runtime`, instead of `[False]`; the original output is
retained in ignored `tmp_quality/settings-quiescence-red.log`.

`apply_registry_draft` now uses the existing `quiesce_configuration_inputs`
helper already used by persistence adapters. Ordering stays lock, release,
clear. Failed lock does not release; either lock or release failure still
attempts pending-clear. A clear error alone aborts the draft. A secondary clear
error remains logged and attached to the primary exception, rather than
replacing it. This guarantees a clear attempt, not successful cleanup when the
clear implementation itself cannot clear state.

Four actual offscreen-window regressions cover lock failure, release failure,
explicit clear failure, and release plus secondary clear failure. They assert
primary error identity, clean input before runtime restore, unchanged accepted
configuration, no setting callback/autosave/arm, stopped authorization and
settled pending lifecycle state. These tests inject a clear error after the
real clear operation. The helper's existing unit tests cover failures of the
cleanup operation itself. This does not claim physical neutral delivery.

The extended test harness initially injected a persistent clear fault, then
incorrectly counted the backend lock's internal clear as the adapter clear.
Those failed outputs remain in ignored `tmp_quality/` as
`test_settings_configuration_quiescence-quiescence.log` and `-v2.log`.
The corrected fault targets the explicit post-release clear once, permitting
subsequent native lifecycle teardown to recover. No production recovery guard
or assertion was relaxed.

## Verification

Use the prepared hash-pinned CPython 3.12.14 interpreter as `PYTHON`, repository
root as working directory, and owned XDG cache/config/data directories:

```sh
export PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen CI=true
export POLAR_AUTOSTART=0 POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1
export XDG_CACHE_HOME="$PWD/tmp_quality/cache"
export XDG_CONFIG_HOME="$PWD/tmp_quality/config"
export XDG_DATA_HOME="$PWD/tmp_quality/data"
for suite in test_settings_configuration_quiescence test_configuration_quiescence \
  test_window_configuration_quiescence test_configuration_transactions \
  test_audio_configuration_transactions test_cs2_settings_center; do
  "$PYTHON" "tests/$suite.py"
done
"$PYTHON" tools/quality_gate.py
git diff --check
```

Results: **4 + 7 + 1 + 10 + 9 + 13 = 44 passed**, no skips. Static gate passed
219 modules / 3,428 functions / zero initialization cycles. Final fault output:
`tmp_quality/test_settings_configuration_quiescence-quiescence-v3.log`; other
suite outputs use `tmp_quality/*-settings-green.log`. No aggregate verifier or
hardware qualification was run.
