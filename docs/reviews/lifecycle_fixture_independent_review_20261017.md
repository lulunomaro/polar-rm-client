# Independent lifecycle fixture correction review, 2026-10-17

Reviewed only `0cf5b4ecb9623192b9b974c56feaafa54578a443` in a detached
worktree. The commit changes `tests/test_ui_state_regressions.py`, not production.
Its parent includes separate pending audio regressions; those are outside this
review and are not certified by this result.

The previous namespace stub did not implement the newly extracted finalization
owner. The corrected fixture instantiates the real `WindowLifecycleController`,
marks native retirement ready, includes the actual `_window_close_finalized`
property getter/setter in its AST-extracted window, and supplies the real
`FinalContextPorts`. The backend is inert because the test starts after native
retirement, which its existing no-shutdown assertion explicitly requires.

Compared all assertion-call ASTs against the immediate parent: all **82** were
identical. The test still asserts save/GL failures do not suppress independent
cleanup, one combined warning, finalized state and final superclass invocation.
No skip, expected-failure marker, timeout or production behavior was changed.

With the prepared hash-pinned CPython 3.12.14 environment on Linux:

```sh
"$PYTHON" tests/test_ui_state_regressions.py
```

Result: **30 tests passed**, exit 0. Detached checkout remained clean.

Independent negative controls temporarily patched the real controller's
`finalize` method while executing
`WindowPathTests.test_save_and_gpu_errors_never_skip_remaining_owner_cleanup`:

1. A no-op finalizer returning `None`: one assertion failure, zero errors.
2. A finalizer setting `finalized=True` but skipping cleanup: one assertion
   failure, zero errors.

Both were rejected without editing the fixture or production source. This
confirms the corrected test exercises real finalization and cleanup rather than
accepting a dummy success result. No blocking finding. Full integration/native
qualification remains with the corresponding verification stages.
