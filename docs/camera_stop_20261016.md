# Camera stop admission and retained native ownership

## Scope

`CameraCaptureWorker` is the sole owner of its OpenCV capture. This change is
limited to its start/stop/close lifecycle and synthetic-device regression tests.
It does not change local inference, UDP/TCP workers, GPU resources, or native
camera driver behavior. UI caller integration is a separate change.

## Contract

- `stop()` retains the legacy bounded 1.2-second wait. It is not confirmation
  that the native handle has been released.
- `stop(wait=False)` revokes the current epoch, running/connected status, and
  latest visible frame. It does not join a thread or invoke native release.
- The existing capture worker alone performs its initial `release()` in its
  `finally` block. A blocked open, read, conversion, callback, release, or final
  native destruction remains owned by that same retained worker.
- A per-start completion event prevents native admission until `Thread.start()`
  has resolved. Start does not hold the revocation lock while calling it.
  A stopped but unresolved start blocks replacement even when the thread has
  not yet obtained an OS identity. A wrapper that starts then raises revokes
  admission and retains the joinable thread, including an already-exited one.
- Startup allocation/admission failures are surfaced to the caller and enter
  the existing retry backoff. New starts cannot spin at the ordinary UI rate.
- `configure(camera_id, size)` sets the next start's configuration. Each worker
  captures one immutable configuration tuple. The sole production caller,
  `ensure_camera_started()`, stops before reconfiguration, then retries the
  latest requested target after the prior worker exits.
- Late open/read/conversion returns check the epoch before publishing connected
  status, a new frame, or a callback admission. A callback already admitted
  before stop may finish on the retained worker; stop never waits on its code.
- `close()` is strict and belongs on the existing teardown worker. It waits for
  start resolution, the capture worker, admitted callbacks, and initial native
  cleanup. It may wait indefinitely for a native driver that never returns.
- Release exceptions, explicit `False`, and a still-open capture retain the
  exact failed handle and block restart. The first strict close reports failure;
  a fresh explicit close after that report may retry it. Concurrent close calls
  queued before reporting are waiters, not implicit retry requests. A failure
  revision similarly prevents queued retries from consuming a newer failure.
- `cleanup_pending` reports unresolved cleanup ownership, including startup
  admission and strict-close requests. `cleanup_error` reports retained native
  release errors. Neither property itself performs cleanup or retries.

## Reproduction and verification

The original production-window probe held only the camera `_run` body on an
Event. `ensure_camera_started()` still used the real `CameraCaptureWorker.stop()`:
1.200180 seconds elapsed, the Qt heartbeat did not run before return, and the
worker remained alive. This is evidence about a controlled Event wait, not
camera throughput, real driver timing, GPU performance, or true presentation.

Implementation stages: `7b924f0` (red regression), `d7c4f6b` (lifecycle fix),
`329a129` (post-admission delay matrix and constructor failure throttle).
Environment: Linux x86_64, Python 3.12.14, PySide6 6.8.2. All tests set
`CI=true`, `QT_QPA_PLATFORM=offscreen`, `POLAR_DISABLE_LOCAL_CAPTURE=1`, and
`POLAR_DISABLE_MEDIA_PRELOAD=1`. Camera tests replace native boundaries with
inert fake captures and explicit Events; no real camera or GPU is used.

Run from the repository with the project virtual environment:

```sh
PYTHONPATH=.:tests python -m unittest \
  tests.test_camera_stop_admission tests.test_ui_worker_shutdown \
  tests.test_vision_stop_admission tests.test_vision_stop_review
python tests/field_candidate_test.py
python tools/verify_all.py --test test_camera_stop_admission \
  --test test_ui_worker_shutdown --test test_vision_stop_admission \
  --test test_vision_stop_review --test field_candidate_test \
  --test test_test_registry
```

Focused result at the implementation stage: 59 unittest tests passed; the field
candidate script emitted its complete pass marker. The 13-method camera suite
also passed 50 consecutive repetitions (650 test executions). Registry selection
found all six requested suites. These focused results do not replace the shared
aggregate verifier or the separately owned production-window caller tests.
