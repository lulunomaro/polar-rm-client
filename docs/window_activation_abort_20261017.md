# Retain fully assembled windows when activation fails

## Confirmed defect and bounded change

At checkpoint `9c6a2a4`, three deterministic subprocess tests failed: an exception
after saved-profile staging, during shortcut installation, or during frame-timer
creation escaped the constructor with `lifecycle_pending=True`, no named timer,
no retained application owner, no close request and no shutdown completion.
Processing Qt events did not retire that generation. Tests rescued it only after
recording the failure, by publishing a poll timer and requesting real shutdown.
Native transport starts were inert; profile staging and lifecycle remained real.

The constructor now guards the fully assembled activation boundary through
shortcut installation and frame timer admission. `_abort_startup` publishes an
inert timer only on the failed-construction path when no named timer exists,
then requests the existing asynchronous shutdown. The original exception remains
the raised exception. Successful construction keeps its existing order: no frame
timer is published during profile loading. No partial-resource inventory or
blanket constructor rollback was introduced.

If emergency timer creation is unavailable too, the finally path still revokes
runtime and requests shutdown. The application retains the blocked owner; it is
not falsely reported finalized or released. Another healthy window stays enabled,
its paused timer stays paused, and it is neither closed nor retained by this abort.

## Evidence

Python 3.12.14 / PySide6 6.8.2; offscreen, CI mode; direct scripts:

- `test_window_activation_failure.py`: 5 tests, covering the three original failures,
  unavailable emergency timer, and two-window isolation
- `test_window_construction.py`: 6 tests, including original publication order
- `test_window_constructor_failure.py`: 4 tests
- `test_window_constructor_delayed_cleanup.py`: 1 test
- `test_window_failed_launch.py`: 4 tests
- `test_window_async_lifecycle.py`: 21 tests
- `test_startup_activation.py`: 7 tests
- `test_startup_activation_adapter.py`: 3 tests
- `test_startup_settings_fullscreen.py`: 19 tests
- `api_freeze_test.py --mutation-selftest`: pass; baseline unchanged
- `git diff --check`: pass

Total: 70 unittest tests plus API mutation selftest. Aggregate verification belongs
to the integration stage because another verifier owns shared resources.
The startup adapter test locator now finds exactly one nested activation call
rather than assuming a top-level constructor expression. Its frozen reference,
effect/failure assertions and success requirements are unchanged.

## Review lesson and residual limits

The preceding finalization review found that callback *lookup* can itself mutate,
reenter or raise. Moving a bound method into a port eagerly can violate lifecycle
ordering even when ordinary callback traces agree. Freeze attribute lookup and
failure prefixes as well as callback effects, and keep lookup inside its original
admission phase. The corrected finalization checkpoint is `fb271c8`.

Failures before the fully assembled activation boundary remain outside this abort
contract. Investigate an actual owned worker/resource leak before extending it;
blindly calling full shutdown on half-created services would hide the primary
failure and create new ownership bugs. The next bounded stage would inventory
service-factory acquisition/cleanup failure boundaries (approximately 1–2 hours
for evidence and design). This stage proves no native GL/driver shutdown bound,
no physical device behavior, and no real-match readiness.
