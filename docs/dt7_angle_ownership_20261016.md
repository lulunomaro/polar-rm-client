# DT7 waits for confirmed angle-port release

## Intentional safety correction

The angle relay retains `owned_ports` while a serial close is blocked or has
failed. CC already used this ownership reservation. DT7 did not: its manager
adapter could create and enable a real writer on the same port (or unresolved
`Auto`) immediately after requesting the angle relay to stop.

The DT7 adapter now rejects that overlapping real endpoint before creating or
reconfiguring a writer. It first disables direct DT7, rebinds any retained writer
to the control adapter, and requests its bounded neutral operation when the
previous route was enabled, a release is pending, or the writer is non-neutral.
The old writer remains owned; a conflict never closes, discards, or replaces it.
Repeated configuration calls do not replenish the worker's release budget.
Mock output and disjoint explicit endpoints keep their previous behavior.

This changes a safety failure path deliberately. It is separate from the
behavior-preserving device-egress extraction; the frozen 102-case fixture was
not regenerated or altered.

## Reproduction and checks

`tests/test_dt7_angle_ownership.py` uses the actual `ControllerAngleRelay` and
`AsyncDt7KeyMouseLink` workers with event-controlled fake serial/write adapters.
It does not open hardware. The DT7 intent watchdog is 60 seconds, longer than
the test window, so it cannot hide a missing immediate neutral request.

Four initial tests failed against the preceding implementation, then passed
with the guard:

- A blocked angle close must prevent a DT7 writer factory call until release.
- A failed angle close must keep its reservation across polls and release it
  only after a successful explicit close retry; `Auto` cannot evade it.
- An already non-neutral DT7 writer must lose authority and receive neutral
  through the same retained owner while the angle close remains blocked.
- A failed neutral must retain the owner and pending state without increasing
  the existing one-attempt retry budget.

Two additional tests cover restoring a retained writer after its control
reference/enable flag were lost, plus unchanged mock/disjoint behavior.

Focused checks, with `QT_QPA_PLATFORM=offscreen`, isolated
`POLAR_USER_DATA_ROOT`, and `PYTHONPATH` set to the repository root:

```
python tests/test_dt7_angle_ownership.py -v             # 6 tests
python -m unittest discover -s tests -p 'test_device_egress*.py' -v  # 18 tests
python tests/test_controller_angle_integration.py -v   # 14 tests
python tests/test_device_manager_nonblocking.py -v     # 7 tests
python tests/test_dt7_worker_dispatch.py -v            # 15 tests
python tests/test_writer_close_ownership.py -v         # 7 tests
python tools/quality_gate.py                          # static gates pass
```

All passed using the isolated quality venv. These are fake-I/O correctness and
static checks, not a native-driver, physical robot, or official-event approval.
