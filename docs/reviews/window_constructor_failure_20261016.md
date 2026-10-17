# Constructor failure: final timer admission

Audit source: `661daedbe6ea0a0509d7a2214277965361d941b8`.
Interpreter: shared Python 3.12.14 / PySide6 6.8.2, offscreen, CI, local capture
and media preload disabled, isolated temporary user-data directory.

## Demonstrated defect

The final `PolarRMWindow.__init__` expression at `polar_app.py:785` assigns
`self.timer = start_frame_timer(...)`. That factory creates a QObject-child
QTimer, connects the real `on_tick`, and starts it before returning
(`window_assembly.py:150–155`). If that terminal start admits its effect and
then raises, construction exits before publishing `self.timer`.

The injected fault calls the real `QTimer.start` and then raises the exact
probe exception once. It models partial terminal admission, consistent with
other admitted-before-exception lifecycle faults; it does **not** claim that an
ordinary Qt start normally raises after success. All constructor, profile,
lifecycle, timer, and event-loop behavior remains real. Only MQTT, serial, and
video native start boundaries are inert. Python network operations are denied
and counted by an audit hook.

`launch_qt_application` calls the factory outside its exception boundary
(`qt_application.py:87–95`). The factory never returns the wrapper, so neither
retention nor `StartupAbort` runs. Real Qt event processing then consumes the
pending profile/scenario and reaches MQTT startup **after the caller has
received the construction exception**.

Observed JSON from the isolated pre-fix subprocess:

```json
{
  "active_timers": 1,
  "closing": false,
  "finalized": false,
  "pending_after_events": false,
  "pending_before_events": true,
  "retained": false,
  "starts_after_events": 1,
  "starts_before_events": 0,
  "timer_published": false
}
```

This is escaped scheduled work and missing failure ownership. It is not proof
of a permanently unreclaimable native handle: explicit fixture cleanup and
forced Python collection can destroy the wrapper. Keeping an exception with
its traceback, as the probe does during reporting, retains the failed
constructor frame and is enough for the active timer to run. A second local
probe cleared the exception traceback and dropped the observer's strong window
reference before processing events; ordinary reference cycles still kept it
alive long enough to reach MQTT, although forced collection subsequently
released it. No network connection was actually made.

## Pre-fix regression

`tests/test_window_constructor_failure.py` runs the fault in a subprocess and
asserts the desired ownership, sealing, no-activation, and final-close result.
It is deliberately marked `unittest.expectedFailure`; the pre-fix outcome is
one EXPECTED FAILURE, **not a passing cleanup acceptance**. The exception
identity is asserted inside the subprocess. The cleanup code repairs the lost
named timer only after collecting all observations, solely to drain the test.

Reproduce:

```sh
CI=true QT_QPA_PLATFORM=offscreen POLAR_DISABLE_LOCAL_CAPTURE=1 \
POLAR_DISABLE_MEDIA_PRELOAD=1 \
/workspace/shared/client-quality-venv/bin/python \
  tests/test_window_constructor_failure.py -v

PYTHONPATH=tests /workspace/shared/client-quality-venv/bin/python -c \
'from test_window_constructor_failure import WindowConstructorFailureTests; print(WindowConstructorFailureTests().probe_timer_admission())'
```

The new regression is a pre-fix review artifact. The implementing lane should
remove `expectedFailure`, register it through the ordinary registry if needed,
and rerun it together with construction, launch-abort, and lifecycle suites.
No production source or existing contract fixture was changed by this audit.

## Earlier audio-preload fault: unproven leak

A second local probe raises at the actual late `KillFxRenderer.preload_preset_sounds`
call after `load_startup_config`. At that point there is no frame timer. The
profile/backend lifecycle remains pending, window closing and application
retention are false, and the lazily started field logger remains unsealed.
Processing events did **not** reach MQTT startup. After dropping the traceback
and forcing collection the window was reclaimed; the logger has an existing
one-second idle retirement policy. This does **not** establish a persistent
worker or native-resource leak, and is not a justification for broad
partial-constructor shutdown changes.

## Narrow cleanup contract to validate

Before final timer admission all named owners used by `_begin_window_shutdown`
exist. An inert frame-timer factory can establish its QObject parent and signal
connection, then publish `self.timer` before calling its fallible `start`.
Only that final admission needs a retained-abort guard: retain the complete
wrapper, seal the existing lifecycle, supersede pending activation with final
shutdown, preserve the primary exception, and keep polling until teardown is
confirmed. Existing `start_frame_timer` callers can keep their compatibility
behavior.

Calling `_abort_startup` on an earlier partial constructor is **not** a safe
general contract: `_begin_window_shutdown` assumes camera/vision/wallpaper
owners and `self.timer` already exist. Even this late audio failure has no
`self.timer`, so it cannot use the existing close path without additional
ownership work. This audit does not recommend swallowing faults, synchronous
native joins, immediate deletion of retiring resources, or broad `getattr`
cleanup of arbitrary partially constructed fields.

## Implemented correction and current regression status

The historical expected-failure artifact is retained at `1173a2e`; the current
`test_window_constructor_failure.py` removes that marker after the narrow fix
and adds outright rejection, broken secondary diagnostics, and permanent timer
unavailability. `test_window_constructor_delayed_cleanup.py` adds actual event
processing with held camera cleanup. All five methods now pass. The unavailable
cleanup timer case explicitly asserts retained/incomplete ownership, then uses
manual polling only for fixture cleanup. The existing 97 related methods, real
API freeze and static gate also pass. This changes only the final fully
assembled event-source admission contract; the earlier-construction limit
above remains in force.
