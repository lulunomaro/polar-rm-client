# Video-source snapshot and explicit retry review

## Correction: data-owner retry verdict withdrawn

At 07:24 UTC the parent review identified a circular-wait case, reproduced on
`aed368219295b7d3a9bdb8e19782799a4f632f6a`: actual decoder pipe readers can require
process termination by a newly admitted closer. When closer admission failed,
classifying those data workers as cleanup in flight forbade the necessary retry.
The original acceptance below is therefore withdrawn for that distinction.
Items 2 and 3 described behavior that the reviewer incorrectly treated as safe;
the test totals remain historical execution facts, not proof of correct policy.

Regression correction `815fc17` now requires data-only pending owners to permit
explicit closer admission. Its corrected assertions produce two precise failures
against the original production source. The real `os.pipe` reproduction,
where only fake-process `terminate()` releases the actual decoder reads, proves
the failure for both native and HEVC. Existing successful-closer coalescing,
strict final ownership, late-Popen and generation protections remain required.

The correction is independently accepted in the [data-owner retry follow-up](video_source_data_owner_correction_20261016.md),
with exact fixed-source and negative-control evidence.

## Historical verdict and fixed source

Production patch `e5fc526` is independently accepted for this bounded backend
contract. It was applied as `17b33d91c2cda3a59226621a3909ee02ff480929` in the
review worktree, after the prior finite thread-admission audit. The final tested
source was `f85359947d6d2a29c1b1ddeab6c6cafb9327fd95`, adding only the independent
three-method `tests/test_video_source_snapshot_evidence.py` regression.

- 23 focused suites, **152 methods PASS**, zero skipped, process exit 0.
- The five author methods plus three independent methods repeated for 30 rounds:
  **240 method executions PASS**, zero skipped, process exit 0.
- Static quality gates PASS: 184 modules, 3,122 functions, zero initialization
  cycles. Registry list-only discovery classifies the new independent suite as
  offline; it executed no aggregate checks.
- A negative control removes only the same-request/stop-pending guard at runtime.
  The UDP-target refresh test then fails exactly its stopper-identity assertion:
  polling creates a replacement native closer. The negative-control wrapper
  requires that failure and exits 0 only when it is observed.

## Historical observations (items 2 and 3 were incorrectly accepted)

1. A blocked native socket close is busy, without waiting for its native close
   lock. `inflight_resources` disables retry even when `close_failures` still
   describes that retained owner from an earlier attempt. Twenty-five retries
   cannot replace the running closer or change the accepted generation.
2. Retired native and HEVC sessions whose pipe workers are still alive remain
   busy even when admission failed and there is no stop-worker object. Their
   retry budget becomes available only after those workers actually end.
3. An admitted UDP receiver blocked before socket publication remains busy when
   its stop-worker admission fails. Its late socket is still attached to its
   session. Once the receiver ends with failed socket closure, the snapshot
   changes to retryable retained failure without changing the request generation.
4. Replacing the UDP/native/HEVC historical `last_error` strings does not replace
   or invent `close_failures`. These are read from retained sessions. Successful
   native release removes those errors despite old diagnostic text; previously
   returned frozen snapshots preserve their evidence.
5. A-to-B-to-A stale expected-generation retry is rejected before new stop
   admission. An explicit retry advances the generation even with an unchanged
   source target, making the pre-retry click stale as well.
6. Repeated accepted-target refresh neither spends failed admission budget nor
   retries an ended failed native close while UDP has not started. The independent
   negative control demonstrates that this last guard is behaviorally required.
7. Existing strict final-shutdown, partial-retry, source/profile, recovery,
   decoder/UDP/MQTT ownership and lifecycle failure suites remain passing in the
   combined fixed-source run.

## Contract and limits

`retryable` requires a current source request, failed admission or retained
pending ownership/current close error, and no related in-flight work. The chosen
target may already be accepted while old native owners failed to close.
`inflight_resources` and `close_failures` may name the same resource during a
retry. That reports unfinished recovery accurately; it does not falsely erase
prior native failure before release is confirmed.

The UI must pass the displayed generation. `expected_generation=None` remains
available for existing internal explicit callers; backend transition and actual
in-flight checks still apply. A snapshot is presentation/admission evidence,
not a transactional release certificate. Only strict final shutdown establishes
whether the application may exit. This review does not cover UI presentation,
real devices, native drivers, network services or desktop/GPU behavior.

## Reproduction and preserved evidence

Environment: Python 3.12.14, with `CI=true`, `PYTHONPATH=.:tests`,
`QT_QPA_PLATFORM=offscreen`, `POLAR_DISABLE_LOCAL_CAPTURE=1`, and
`POLAR_DISABLE_MEDIA_PRELOAD=1`. Run the 21 modules in the earlier
[finite audit](thread_admission_audit_20261016.md), adding
`tests.test_video_source_snapshot` and `tests.test_video_source_snapshot_evidence`,
through `python -m unittest -v`. No aggregate runner was executed in this review;
its shared lock remained available to the separately scheduled aggregate stage.

Preserved evidence:
- [Fixed-source focused run](../evidence/video-source-snapshot-20261016/focused.txt)
- [Thirty-round repetition](../evidence/video-source-snapshot-20261016/repeat.txt)
- [Negative-control result](../evidence/video-source-snapshot-20261016/negative.txt)
- [Negative-control reproduction](../evidence/video-source-snapshot-20261016/negative_control.py)
- [Source and result metadata](../evidence/video-source-snapshot-20261016/metadata.json)
- [Static quality result](../evidence/video-source-snapshot-20261016/static.txt)
