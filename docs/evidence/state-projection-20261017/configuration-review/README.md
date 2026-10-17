# Independent review: participant rollback lookup

Exact reviewed commit: `2ee80d99f5d32fb554c66c8bb580ef14a3765356` in detached
`review-state-transactions` worktree. Red characterization checkpoint: `4d04f36`.
No production edits were made by this reviewer.

Assessment: no blocking findings. `ConfigurationParticipant` is a structural
protocol with commit/rollback operations; callback resolution is therefore part
of the participant's fallible work. The prior eager tuple expansion could fail
before even the first registry restore and unconditional fail-closed operation.
The corrected per-participant lambda binds `item` at creation, but resolves and
invokes its rollback only inside the existing exception guard. Reverse order,
first/exact second registry restoration, stopped runtime restoration, result
propagation for `RuntimeRestorePending`, and unconditional fail-closed cleanup
remain intact. Lookup and runtime failures accumulate as restore errors, safety
failure remains separate, and the original apply cause remains the chained cause.
Successful transactions never resolve rollback attributes.

The property-backed reproduction is a valid protocol adapter failure; it is not
proof that today's concrete AudioPreferenceChange has a throwing descriptor.
Snapshot lookup and caller callback preparation are separate contracts and were
not rewritten or broadly declared safe by this review.

## Independently executed checks

Python `/workspace/shared/client-clean-venv/bin/python` (3.12.14), exact checkout
root, `QT_QPA_PLATFORM=offscreen CI=true POLAR_AUTOSTART=0`
`POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1 PYTHONPATH=.`:

- `tests/test_configuration_rollback_resolution.py`: 3 pass
- `tests/test_configuration_boundaries.py`: 18 pass
- `tests/test_audio_configuration_transactions.py`: 9 pass
- `tests/test_configuration_transactions.py`: 10 pass

**40 methods pass.** Full direct-script output is in the adjacent `.txt` files.
No skips, production network or device tests, dependency installation, aggregate
verification, native audio qualification or physical neutral confirmation.
