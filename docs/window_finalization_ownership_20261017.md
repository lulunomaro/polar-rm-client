# Window final retirement ownership

## Scope and invariants

The window used to own both Qt resource binding and the final retirement policy:
its one-shot seal, timer/configuration ordering, GL context admission, independent
release attempts, and accumulated cleanup failures. `WindowLifecycleController`
now owns that policy and its `finalized` / `finalization_failures` state.
`FinalContextPorts` supplies context availability, entry, deferred named resource
resolution and exit; the owner imports no Qt and never receives the window.
The window remains responsible for binding actual resources and presenting errors.
The historical `_window_close_finalized` seam forwards to the same lifecycle owner.

No native close admission, resource list, warning wording, ordering, retry policy,
configuration persistence, runtime gates, rendering or robot protocol changes.
This is not asynchronous GPU cleanup: callbacks still execute on the Qt owner
thread. The caller must only admit final retirement after native shutdown finishes.
Reentrant/repeated finalization is sealed before effects. Each independent owner
or GL release failure remains visible without skipping later releases. Context
inspection failure skips GL work; failed entry still attempts `doneCurrent`.
Warning delivery can fail without permitting a replay.

## Reproducible evidence

Baseline commit `54650e4` added six frozen production-adapter characterization
tests before implementation changed. Independent review added three callback-lookup
characterizations, verified against the original source at `54650e4`; these
exposed and then verified correction of premature binding before the seal/context. These execute the adapter's AST with fake
resources, avoiding Qt import while leaving production cleanup logic intact.
The same unchanged tests passed after extraction. Four additional tests exercise
the explicit owner, including reentry, delayed resource lookup and BaseException.

Verified with Python 3.12.14 / PySide6 6.8.2, `QT_QPA_PLATFORM=offscreen CI=true`:

- `tests/test_window_api_contract.py`: 41 tests
- `tests/test_window_async_lifecycle.py`: 21 tests
- `tests/test_window_construction.py`: 6 tests
- `tests/test_window_constructor_delayed_cleanup.py`: 1 test
- `tests/test_window_constructor_failure.py`: 4 tests
- `tests/test_window_failed_launch.py`: 4 tests
- `tests/test_window_finalization_contract.py`: 9 tests
- `tests/test_window_finalization_owner.py`: 4 tests
- `tests/test_window_lifecycle_controller.py`: 17 tests

The initial stage ran through `tools/verify_all.py` with one `--test` argument
per path above: 9 PASS, 0 SKIP, 0 INCOMPLETE, 0 FAIL (104 unittest tests).
After the review correction, all nine scripts ran directly and passed 107 tests.
The aggregate rerun was blocked by another active verifier; integration must run
that aggregate again rather than treating direct scripts as aggregate evidence.
`tests/api_freeze_test.py --mutation-selftest` independently passed; no baseline
was refreshed. `git diff --check` passed. An earlier run correctly rejected a
concurrent import-only edit; the final clean-source verifier run supersedes it.

These are focused offline checks, not an all-suite or native GL/driver claim.
No hardware, service quota, desktop executor or paid API was used.

## Remaining responsibility debt

The constructor still mixes state assembly with activation callbacks. In
particular, startup activation and shortcut installation occur before the final
frame-timer admission guard: earlier partial construction failures have no complete
window abort contract. A next bounded investigation should characterize failures
at profile-load/scenario-request/shortcut-install boundaries, inventory already
owned workers, then introduce a staged owner only where cleanup is safe. Estimate
1–2 focused engineering hours for characterization and design; implementation and
native-driver validation may require a separate stage. Do not infer these paths
are safe merely because terminal timer admission is covered.
