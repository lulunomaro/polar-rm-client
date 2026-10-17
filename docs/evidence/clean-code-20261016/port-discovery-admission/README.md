# Serial discovery admission rejection

## Reproduced defect

Independent red test: `706b136998a9ee1746c35932eeeea09d0ce133d5`.
It was replayed as `fd625fd` on the domain-boundary branch (`3fa6db7` parent),
without modifying the original three tests.

`port_snapshot()` set `_port_refresh_running=True` before constructing or
starting the discovery thread. If either step raised before admission, no
worker existed to execute the `finally` that cleared the flag. Every later
snapshot, including after explicit cache invalidation, suppressed refresh.
The original exception escaped, but discovery was permanently marked pending.

Pre-fix result: three methods, **two FAIL / one PASS**. Constructor rejection
and start rejection failed. A genuinely admitted worker whose `start` wrapper
subsequently raised already retained its reservation correctly.

## Narrow correction

- A short, separate refresh-admission lock makes simultaneous snapshots reserve
  only one refresh. It is never held across hardware enumeration, thread
  construction, `start`, or a wait/join.
- A constructor or start rejection rolls back only the unadmitted reservation
  and re-raises the exact original exception. Cleanup also applies to an
  interrupted/cancelled admission without swallowing cancellation.
- An admitted thread has a non-`None` `ident`, including after it finishes.
  A late wrapper error therefore cannot clear an active worker or a newer
  successor's reservation. The actual refresh remains responsible for release.
- Cache values, timestamps, generation handling, native scanning, expiry,
  explicit invalidation, and copying/ordering of returned values are unchanged.
  Invalidation still discards a stale in-flight result and requires a later
  snapshot to request replacement; it does not create an automatic retry loop.

No broad scanner abstraction, device access, transport-control change, UI edit,
API baseline update, or aggregate-runner bypass is part of this fix.

## Focused verification

Interpreter: `/workspace/shared/client-quality-venv/bin/python`.

- `tests/test_port_discovery_admission.py`: **9 methods PASS**, retaining the
  original three red tests and adding cache/error identity, cancellation,
  blocked constructor with immediate concurrent snapshots, sixteen simultaneous
  callers, worker-finished-before-late-error, successor-before-old-error, and
  explicit invalidation during a blocked real worker.
- Adjacent suites: **39 methods PASS** across `test_dt7_worker_dispatch`,
  `test_device_manager_nonblocking`, `test_default_catalog_characterization`,
  `test_device_egress_characterization`, and `test_backend_angle_lifecycle`.
- Static quality gate: **PASS**. `git diff --check`: **PASS**.

The scan boundary is fake; worker threads, reservations, cache publication, and
real caller methods are production code. Tests prove controlled ownership and
failure behavior, not native enumeration latency or field readiness. Final
combined-tree verification and remote publication remain parent-owned.
