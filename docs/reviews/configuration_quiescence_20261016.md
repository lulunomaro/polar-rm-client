# Configuration quiescence: independent pending-input cleanup

Base: `66deab1d34ea56e0e9ce07812ef6b565491b704f`.
Test-first checkpoint: `a751fa5` (3 methods, 8 failures and 8 errors across
caller/failure subtests).

## Confirmed responsibility defect

Four persistence adapters duplicated lock → release → clear-pending:
`apply_match_mode`, `apply_robot_profile`, `sync_control_binds`, and
`_quiesce_config_reload`. Any lock/release exception prevented pending cleanup.
The methods combine domain changes with input quiescence, but pending cleanup
is independently required even when quiescence rejects the domain change.

`configuration.quiescence.quiesce_configuration_inputs` now owns this small
contract through explicit, lazy callbacks. It imports no UI or backend. It
attempts revoke then release in their original order. A failed revoke still
skips release; no additional egress is introduced. Pending clear is attempted
whether revoke/release succeeds or raises an ordinary Exception. Failure still
propagates before any subsequent domain operation.

The correction intentionally adds only that independent pending-clear attempt.
It does not add the focus-loss helper's fallback native releases, retry ordinary
publication, or make these profile operations atomic. The spin falling-edge
publication before reload quiescence retains its exact failure prefix. Robot
role selection that precedes identity quiescence likewise remains unchanged.

When both the primary operation and pending clear fail, the original exception
object and traceback are preserved; the cleanup failure is attached as a note
and logged. Failed diagnostic sinks cannot replace the primary exception.
Pending-clear failure alone propagates normally. Process-control BaseExceptions
remain outside this ordinary-error recovery contract.

## Verification

- New adapter/policy suite: 7 methods, including all four callers, lock/release/
  clear failures, dual failures, broken diagnostics, identity mode lookup and
  pre-quiescence spin failure.
- New real-window transaction suite: 1 method. A release callback leaves dirty
  input then raises; restoration observes cleared pending input, requests only
  stopped runtime, restores exact prior configuration, finishes asynchronous
  recovery and retains E-stop/no-rearm.
- Existing configuration boundaries: 18; command adapters: 26; transactions: 10;
  profile save contract: 30; UI state regressions: 30; construction: 6; API
  contract: 41. All 169 focused unittest tests pass.
- Static quality gate passes; no baseline changes.

The AST-based UI regression harness imports the new helper; assertions are
unchanged. Red and focused-green logs are retained under
`../evidence/clean-code-20261016/configuration-quiescence/`.

No native desktop/GPU/device acceptance or live output is claimed. Failure of
clear itself remains explicit, not a claim that all pending input was cleared.
The parent owns fixed-source integrated verification and independent review.
