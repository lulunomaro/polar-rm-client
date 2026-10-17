# Focus-loss independent cleanup

## Reproduced defect and intentional behavior change

At `c5b9633`, `PolarRMWindow.focusOutEvent` performed mock-mode synchronization,
`release_all`, neutral publication, authority lock, and pending-input clear in a
single try/finally. Any earlier exception skipped later safety actions. The
finally called Qt, but did not revoke authority or clear pending state.
Test-first commit `15687ab` reports 2 failures and 6 errors across 6 methods
(including subtests). This is a failure-cleanup bugfix, not strict preservation
of the original exception prefixes.

`request_focus_loss` now owns the independent safety sequence through narrow
control/input protocols and two explicit callbacks:

1. Attempt authority revocation first, with reason `window focus lost`.
2. Attempt the existing neutral input release path. If any part fails,
   independently attempt local reset, DT7 neutral release and custom-controller
   neutral release. These are idempotent neutral attempts, not ordinary control
   retries; a failed reset never causes a retried ordinary send.
3. Attempt the existing dedicated neutral publication, reporting rejection as
   well as exceptions.
4. Attempt clearing pending input independently of all preceding outcomes.
5. Report incomplete operations; the Qt focus handler still runs in finally,
   including if the warning presentation itself fails.

The normal path retains existing neutral operations, but authority revocation
moves before them. The backend's real lock/input-clear hook may already perform
neutral cleanup, as it did before; duplicate dedicated neutral calls are safe.
Ordinary exceptions are accumulated without skipping other operations. There is
no arm/rearm call, new egress permission, or new output format.

## Evidence

Using the hash-pinned verification environment, with Qt offscreen:

- `test_focus_loss_failures.py`: 7 methods exercising production event-method
  AST, each outer failure, simultaneous failures, mode-lookup failure, neutral
  rejection, success order and Qt-finally despite failed reporting.
- `test_window_focus_loss.py`: 2 real-window tests show qualification capability
  destruction and LOCKED state after reset or ordinary neutral-send failure;
  both dedicated neutral routes are attempted and pending input is cleared.
  A reset failure preserves the backend's explicit `input clear failed;
  explicit re-arm required` reason. Ordinary publication is not attempted after
  reset failure; no rearm is called.
- Existing fullscreen/input: 12; emergency actions: 7; backend input owner: 11;
  backend lifecycle input failure: 5; keyboard differential: 17; real-window
  construction: 6; window API contract: 41. Total: 108 focused tests, all pass.
- Static quality gate passes without baseline changes.

The fullscreen harness adds the new helper to its extracted method's scope;
its behavioral assertions are unchanged. Red/green logs are under
`../evidence/clean-code-20261016/focus-loss/`.

## Limits

Dedicated link methods own retained pending/error state and may report native
failure there instead of raising. This workflow guarantees independent attempts,
not firmware receipt of neutral bytes or successful device shutdown. If reset
itself fails, local held state may remain, while backend authority is revoked
and explicit re-arm remains required. The new helper catches ordinary Exceptions,
consistent with existing stop actions, not process-control BaseExceptions.
Native desktop, hardware and actual network egress were not exercised.
