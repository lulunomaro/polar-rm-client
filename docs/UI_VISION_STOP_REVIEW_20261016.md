# Independent vision stop / UI caller review — 2026-10-16

## Result and fixed scope

No remaining blocker was found in the reviewed stop-admission and refresh-caller
scope after the queued-close retry fix and caller wiring. This is **targeted
offline verification**, not a full aggregate or hardware acceptance result.

Reviewed production stages:

- `ba4575ab3e56ce3afb6178bf4bcf82b46618e8c5`: three native-worker
  `stop(wait=False)` APIs and nonblocking local/UDP configuration admission.
- `3a05029dbc46dcaeb935682ec5de25e547fb843f`: queued close requests cannot
  retry a native failure that was not reported when that request entered.
- `5df6104b3b84540627a6c2b35c1f14aa3dfbf178`: actual window refresh uses
  nonblocking stop/configure, preserves source failures, attempts every requested
  revocation, and refuses new refresh work during final window close. This full
  snapshot includes the native stages above as equivalent local commits.

The combined review checkout was fixed at `5df6104` plus test-only commit
`718afb9` (equivalent to independent regression commit `60638de`) and the
heartbeat regression in `tests/test_vision_refresh_heartbeat.py`. No production
source was edited by this review.

## Defect independently reproduced and fixed

At `ba4575a`, two `close()` requests could both enter before the first native
socket-close failure was reported. The second request waited on `_close_lock`,
then observed the first request's newly set `_socket_close_error_reported` and
implicitly retried the failed close. With a native boundary that fails its first
close and succeeds its second, both UDP and TCP owners produced:

1. First caller: `socket close failed: first native close failed`.
2. Already-queued second caller: success, after an unexpected second native close.

The independent regression executes the real production worker with a synthetic
socket and a transparent counted lock. An Event proves both close calls entered
before the worker's first close failure was released. The original stage fails
for **both owners**. After `3a05029`, both waiting callers report the retained
failure, exactly one native close has occurred, start remains blocked, and a
fresh third close made after reporting can retry and release the retained handle.

## Verified ownership and scheduling properties

- Stop immediately revokes `running`, advances the generation, and clears queued
  inputs/results. Late model load, frame copy/JPEG encoding, recv, parsing,
  inference and postprocessing cannot republish into the revoked generation.
- Stop admission does not create a cleanup thread or join/release a native owner.
  Existing real workers own their native close/destruction. Held socket close and
  a successfully published model's held destructor do not retain the UI lifecycle
  lock. Strict close remains pending until those workers actually exit.
- Repeated stop, rapid off/on and repeated reconfiguration retain one admitted
  worker. A retained failed socket prevents new native admission until explicit
  successful retry; `cleanup_pending` and `cleanup_error` distinguish in-flight
  cleanup from a completed worker with retained native failure.
- Constructor rejection, genuinely unstarted `Thread.start()` rejection,
  admitted-then-raised and finished-then-raised cases retain the correct ownership.
  The independent admitted-then-raised case uses each real `_run` and verifies
  that the exception revokes admission before any native factory is called.
  Finished-then-raised coverage uses a real Thread with an inert substituted body.
- Concurrent successful strict closes release the native socket once, on its
  worker. Worker self-close is rejected rather than deadlocking a waiting close.
- The actual window tests preserve the original source transition error even
  when another stop raises, attempt other owners' requested revocations, reject
  refresh during final close, and keep the window unfinalized until retained
  workers/native failures are resolved.

## UI caller negative control and Qt evidence

With the new primitive APIs present but the old UI caller unchanged (fixed
`60638de` review checkout), the original real-refresh/inert-worker experiment
still took **2.805 seconds** in this environment. This independently reproduces
the earlier 2.801-second observation: adding a no-wait API alone did not remove
the caller's three bounded joins. Its queued Qt callback was unserved until
refresh returned and events were processed.

The permanent independent heartbeat regression executes the real
`VideoBackendMixin.refresh_vision_runtime` and `_stop_vision_workers` helper with
production start/stop/close methods. Only unrelated window services and worker
bodies are inert. It exercises both disabled capture and Auto reconfiguration,
with held workers released by the Qt event loop after 80 ms and 240 ms:

- A guard rejects any worker join attempted on the calling/UI thread.
- Repeated refresh and Qt heartbeat continue while the workers remain held.
- Strict close runs separately, cannot complete before the Event release, and
  confirms all three worker handles have exited afterward.
- Both hold durations pass for both caller branches. An observed probe produced
  7/24 and 7/23 heartbeats before release; maximum refresh calls in that run were
  about 0.02–0.13 ms. These values are observations of synthetic scheduling only.

Correctness assertions use forbidden-join guards, Events, retained-owner state
and final exit, **not a claim that a particular host timing qualifies native
hardware or rendering performance**.

## Reproduction and results

Environment: Linux x86_64, Python 3.12.14, PySide6 6.8.2. Interpreter used:
`/tmp/client-quality-venv/bin/python`.

```sh
CI=true PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen \
POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1 \
/tmp/client-quality-venv/bin/python -m unittest -v \
  tests/test_vision_stop_review.py \
  tests/test_vision_stop_admission.py \
  tests/test_ui_worker_shutdown.py \
  tests/test_window_vision_runtime.py \
  tests/test_vision_refresh_heartbeat.py
```

Result: **54 test methods passed**, including the four heartbeat subcases.
Before adding the caller layer, the 25 primitive/independent methods were also
run 50 consecutive times: **1,250 method executions passed**. Both independent
test files are discovered as offline suites by `tools.test_registry`.
`git diff --check` passed.

Local diagnostic logs from this review:

- `/tmp/cc-ui-stop-review-red.log` and `/tmp/cc-ui-stop-review-green.log`
- `/tmp/cc-ui-vision-refresh-baseline-fixed.log`
- `/tmp/cc-ui-vision-refresh-reviewed-red.log` and
  `/tmp/cc-ui-vision-refresh-reviewed-green.log`
- `/tmp/cc-ui-stop-refresh-review-combined.log`

## Limits

- No full `verify_all` invocation was made by this review; its shared global lock
  was not bypassed. The results above do not replace the integration gate.
- No real model, OpenVINO execution, GPU, camera, network peer, native OpenGL
  rendering, or physical device was exercised. Offscreen Qt warned that native
  QOpenGLWidget rendering/audio devices were unavailable, as expected.
- Local model evidence confirms the final Python reference is dropped by the
  worker outside the admission lock and strict close waits for that thread's
  destruction path. It does not independently certify SDK/device allocation
  reclamation.
- A TCP `on_result` callback already admitted before revocation may finish on its
  retained worker; strict close waits for it. The internal result slots cannot be
  republished after revocation. There is no current production `on_result`
  subscriber assignment in the reviewed tree; arbitrary callback cancellation is
  not claimed.
- A truly hung native call intentionally keeps strict close pending. This review
  does not weaken final release confirmation to obtain a faster close result.
