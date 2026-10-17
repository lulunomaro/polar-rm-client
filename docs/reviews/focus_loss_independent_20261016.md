# Independent focus-loss cleanup review

Candidate: `db15af44ca9e05072a1e0a8541d1298722ad65dc`.
Result: no blocking finding.

Independent rerun: 28 methods in `test_focus_loss_failures`,
`test_window_focus_loss`, `test_fullscreen_input_extended` and
`test_emergency_action_failures` pass, including real offscreen window tests.
An independent matrix exercises all 128 combinations of seven operation failures
(lock, ordinary release, fallback reset, DT7 neutral, custom-controller neutral,
zero publication, pending clear). Every required independent attempt and Qt
finally delegation occurs; lock failure never produces a false LOCKED status.
Four additional cases raise during fallback collaborator attribute resolution,
not just invocation; later operations still occur and failures stay observable.

Source review confirms the real backend delegates first to safety.lock_control,
which sets LOCKED, clears qualification/capabilities and changes generation before
its input-clear callback. Thus input release or zero-publication exceptions cannot
leave authority intact. If locking itself fails, the helper reports incomplete;
it cannot honestly guarantee arbitrary failing backend implementations revoked.
A failed ordinary release triggers independent local reset and dedicated neutral
attempts; it does not retry ordinary publication after a failed reset. Mode lookup
and zero-publication lookup are deferred inside guarded callbacks. Qt receives
focus-out in finally even if warning display raises.

Deliberate behavior changes are appropriately documented: revoke before release,
continue independent cleanup after ordinary Exceptions, and warn on rejected
neutral publication. Dedicated links retain their own pending/error observations.
This is a guarantee of attempts and local authority behavior, not native firmware
neutral receipt. BaseException process-control handling and native OS focus
transport remain outside scope.

Reproduce from the candidate checkout:

```
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest test_focus_loss_failures test_window_focus_loss test_fullscreen_input_extended test_emergency_action_failures
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python docs/evidence/clean-code-20261016/focus-loss-independent/review.py
```

The portable script and logs are retained under the evidence directory above.
They were run on the exact candidate before retention in the review branch.
