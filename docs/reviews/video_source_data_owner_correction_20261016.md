# Correcting data-owner versus cleanup-operation retry state

## Verdict

Accept production fix `d9a61f1240d34cd9842bd5ff8f205395ddb637f4` together with
reviewer test correction `815fc17f52940b8711468231ea4fcbe2310f42e2`.
The exact independently tested combined source is
`e403a1a1dd24d49bd5873892d3a4b9a68808186f`:

- **24 suites, 154 methods PASS**, zero skipped, exit 0.
- Snapshot, corrected evidence, and actual-reader retry suites: **30 rounds,
  300 method executions PASS**, zero skipped, exit 0.
- Independent policy negative control: restoring only live-data-worker-as-busy
  produces exactly three expected failures, native, HEVC and UDP, with no errors.
- Static quality gates PASS.

This supersedes the incorrect data-owner classification accepted in the
[earlier snapshot review](video_source_snapshot_review_20261016.md). Its passing
test counts are historical facts; two assertions encoded the wrong progress
model and have been corrected. The earlier finite thread-admission ownership
fixes remain required and pass the combined run.

## Counterexample and correction

The manager and decoder were real. Only the process boundary was faked, using
actual `os.pipe` read ends for stdout/stderr. An observation-only wrapper first
confirmed both decoder threads entered the real blocking `os.read`. Only the
fake process's `terminate()` closes the write ends to produce EOF.

After ordinary stop-worker admission rejection:

- The data threads and process remained owned and stopped-for-new-input.
- No stop worker existed; neither the close lock nor process-lifecycle lock was
  held; process termination had not been requested.
- Old snapshot policy nevertheless reported cleanup in flight. All 25 explicit
  retries were rejected, so the closer required to unblock those readers could
  never be admitted. Directly admitting one closer terminated and joined them.

The narrow fix removes data-thread liveness from `inflight`, retaining those
sessions as `pending`. It preserves actual stop-worker, native close-lock,
process-lifecycle and recovery-worker evidence. Explicit retry can now admit
exactly the missing closer. While that closer is blocked in termination or
socket close, the snapshot is busy and repeated retries cannot create another.

Independent fixed-source repetition of the real-pipe reproduction confirms
both codecs now report pending/retryable, admit manager retry, terminate and
join their readers. The committed tests additionally exercise actual UDP
receive blocking and late socket publication, including retained failed native
closure and the subsequent explicit retry. Strict final shutdown still joins
and confirms resources before permitting exit.

## Protections preserved

The combined run retains single-closer coalescing, late/unpublished Popen busy
state, generation-bound A-to-B-to-A stale-click rejection, no automatic retry
on periodic refresh, current-owner errors rather than stale diagnostics, and
strict final-shutdown ownership/retry confirmation. No timeout was increased and
no production close/join implementation was relaxed.

## Reproduction and evidence

Use the environment documented in the earlier review. Run its 23 unittest
modules plus `tests.test_video_source_retry_admission` for the 154-method focused
composition. For the 300 repeated executions, run these three modules for 30
rounds: `tests.test_video_source_retry_admission`,
`tests.test_video_source_snapshot_evidence`, and `tests.test_video_source_snapshot`.

All evidence is in
[`docs/evidence/video-source-data-owner-correction-20261016`](../evidence/video-source-data-owner-correction-20261016/):
old and fixed real-pipe reproductions, corrected old-policy failures, combined
fixed-source run, repetitions, policy negative control, static result, and
source/result metadata. The negative wrapper exits 0 only after observing all
three expected native/HEVC/UDP failures. These are focused backend tests;
aggregate, UI rendering, real processes/drivers/devices and GPU qualification are
separate evidence and are not claimed here.
