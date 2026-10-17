# DT7/CC retained writer thread owner

Baseline: `fa2c2b8397295d27ada5d105854b751b7dd0940d`.

## Boundary and preserved contracts

`WriterThread` owns exactly the thread reference and initial-admission
reservation previously duplicated by the DT7 and CC adapters. It has no native
handle, protocol fields, motion intent, neutral budget, or GUI owner. Adapter
conditions still serialize cancellation and cleanup-retry selection. Initial
construction/start remains outside that lock, so a concurrent close cannot wait
on thread construction or admission. The new owner releases the reservation
under the same condition even when thread construction or start raises.

Assignment still precedes `Thread.start()`. A rejected start retains its
unjoinable thread; an admitted-then-raised wrapper retains its live or completed
thread. A failed Thread constructor does not overwrite the previous reference.
Join snapshots that reference and excludes never-admitted and current threads.
The read-only adapter `_thread` property preserves existing diagnostic consumers
without a second mutable thread slot.

Protocol-specific failure sealing, retain-owner publication, exception annotation,
neutral draining, failed native close retention and explicit retry remain in
DT7/CC. In particular, CC's `_close_failed` drain exclusion is unchanged; a shared
thread owner does not pretend that DT7 and CC native finalization are identical.
The class deliberately relies on adapter-serialized admission decisions rather
than adding a second lock or changing the existing native cleanup state machine.

## Characterization and verification

Before production replacement, all 18 existing constructor-admission, concurrent
admission and retention-cancellation methods passed at the baseline. The initial
combined close-owner run hit three missing-pyserial import errors; after the
parent installed pinned dependencies, all focused methods below passed. No
baseline assertion or API fixture was changed.

Five direct thread-owner tests additionally cover reservation without admission,
constructor rejection with a previously completed owner, rejected start,
KeyboardInterrupt after actual admission, and self-join avoidance. The late-start
fault is a defensive wrapper injection, not a claim about normal CPython starts.

Executed in this candidate worktree using Python 3.12 and the parent's pinned
`client-clean-venv`:

```sh
CI=true PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen \
POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1 \
/workspace/shared/client-clean-venv/bin/python -m unittest \
  tests.test_writer_thread_owner \
  tests.test_writer_constructor_admission \
  tests.test_writer_concurrent_admission \
  tests.test_writer_retention_cancellation \
  tests.test_writer_close_ownership \
  tests.test_dt7_worker_dispatch tests.test_cc_worker_dispatch \
  tests.test_angle_writer_handoff \
  tests.test_device_egress_characterization tests.test_device_egress_safety \
  tests.test_device_manager_nonblocking tests.test_controller_frame_regressions \
  tests.test_controller_angle_safety_review tests.test_controller_angle_integration \
  tests.test_dt7_angle_ownership tests.test_backend_input_owner \
  tests.test_backend_shutdown_retry tests.test_transport_safety_regressions
```

Result: **187 methods PASS**, no skips, exit 0. Retained log:
`docs/evidence/writer-thread-owner-20261016/focused.txt` (trailing whitespace normalized).

Also passed: `tests/api_freeze_test.py` (unchanged API baseline),
`tools/quality_gate.py`, and `git diff --check`. Offscreen API checks report the
expected unavailable OpenGL/audio/font-cache environment; they do not establish
native rendering. Neither the aggregate runner nor hardware was run by this lane.

Whole production-tree metrics include the new replacement owner: modules
208 → 209, functions 3368 → 3377, statements 37123 → 37122, decisions
13069 → 13061, import edges 598 → 600. Both import-cycle graphs remain empty.
This is an ownership/deduplication change, not a claim of whole-tree size reduction.
Source digests and metric deltas are retained in the adjacent `metrics.json`.

Physical CC/DT7 traffic, firmware-confirmed neutral and permanently blocked native
drivers remain external qualification limits. Integration with other lanes needs
its own fixed-source aggregate result.
