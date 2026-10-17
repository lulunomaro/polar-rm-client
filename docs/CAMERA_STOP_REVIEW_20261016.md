# Independent camera stop review — 2026-10-16

## Native-worker stage

Production reviewed: `d7c4f6b1475480032fe97424dccc33eab7679eea` plus
`329a129` (local review equivalent `00f9cfb`). Characterization starts at
`7b924f0`. This is a separate camera review; the previous three vision-worker
review does not establish these camera guarantees.

No blocking defect was found within this fixed native-worker scope. Independent
regression `30c17d6` adds five methods in `tests/test_camera_stop_review.py`.
Together with the 13 camera admission methods and 21 older strict-close methods,
**39 methods passed** in the fixed review checkout.

Additional independent cases beyond the author's initial suite:

- Hold `isOpened`, capture property assignment, resize, and post-release
  `isOpened` confirmation. `stop(wait=False)` remains usable, cannot join, clears
  visible state, and cannot admit replacement. Late completion cannot configure
  another property, convert/publish a frame, or invoke the frame callback.
  Native release occurs once on the actual worker, and strict close waits for it.
- Start two explicit retries against the same previously reported failure.
  While the first retry's native release is held, queue the second. When the
  first retry creates a newer failure revision, the queued retry reports that
  failure without consuming it with another release. A fresh later request can
  retry the retained exact capture.
- Cancel one delayed-but-OS-admitted `Thread.start`, then admit a replacement.
  Each generation has a distinct unresolved `_start_done`; the old completed
  Event cannot admit the new native capture. Only the replacement opens, using
  the latest configuration snapshot.
- Reject Thread construction after an earlier worker completed but still owns
  its bookkeeping slot. The rejected attempt preserves the old completed owner
  and old completed admission Event, leaves no pending cleanup, records backoff,
  and permits strict close without repeating native release.
- Keep a real OS-admitted start wrapper unresolved. Default bounded stop returns
  with the worker and admission gate still retained; final close confirms exit
  only after the decision is released. No native factory is entered.

Code review also checked the self-close rejection, per-request close count,
short lifecycle-lock scopes, sole worker ownership of native release, exception
ownership for unstarted versus admitted/finished threads, and explicit retained
capture retry. The close-entry failure revision prevents an already-queued retry
from accidentally acknowledging a newer failure.

## Integrated caller and Qt stage

The later integration review was fixed at
`43df652e1cb76df10ff7f488d4575a396893045e`, including camera stages
`7b924f0`, `d7c4f6b`, `329a129`, `7354c86` and caller `d3b8f9e`.
The independent native test was reapplied as equivalent commit `f62ee13`.
No blocking defect was found in this integrated camera scope.

The author's seven `WindowCameraRuntimeTests` were read and independently
rerun: disabled capture, non-Webcam source, deploy acknowledgement,
coalesced reconfiguration, final-close admission guard, rejected start during a
tick, and rejected start during CPU fallback paint all pass. These use the real
window and preserve safety/repaint progress and strict final-close assertions.

The distinct independent addition, `tests/test_camera_window_heartbeat.py`
(commit `f42eee3`), uses **both the real window and the actual camera `_run`**.
Only the cv2 native boundary is synthetic. It holds native release for two
Qt-scheduled Event durations, 80 ms and 240 ms, while checking:

- The real disabled-capture caller and event handlers never join on the UI
  thread; an explicit guard rejects that operation.
- Qt callbacks and the real `on_tick` continue while the native capture owner
  remains retained and the window is not finalized.
- Closing rejects a late `ensure_camera_started` call.
- After native release and `isOpened` confirmation, the original worker exits,
  the retained thread is cleared, and only then does window completion allow
  exit. Exactly one native release occurs, on the original camera worker.

Combined result: **49 test methods passed** (5 independent native, 14 authored
camera admission, 21 older strict ownership, 7 actual-window caller, 2 independent
Qt/native-release cases). The 18 non-time-budget native camera methods were also
repeated 20 times: **360 method executions passed**. The separate 1.2-second
legacy bounded-stop case was excluded from repetition only; it passed in both
complete targeted runs.

This extends the evidence beyond an inert worker and a single `processEvents`
call. Correctness uses Event ordering, an explicit forbidden-join guard, retained
ownership and actual worker exit; the selected Event durations do not establish
physical camera latency or rendering performance.

## Reproduction

Linux x86_64, Python 3.12.14, PySide6 6.8.2; synthetic native boundaries only.

```sh
CI=true PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen \
POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1 \
/tmp/client-quality-venv/bin/python -m unittest -v \
  tests/test_camera_stop_review.py \
  tests/test_camera_stop_admission.py \
  tests/test_ui_worker_shutdown.py \
  tests.test_window_vision_runtime.WindowCameraRuntimeTests \
  tests/test_camera_window_heartbeat.py
```

The native-stage output is in `/tmp/cc-camera-stop-review.log`; the integrated
49-method output is in `/tmp/cc-camera-caller-review.log`.
The independent file is discovered as an offline suite; `git diff --check`
passed. No production source was changed by this independent review.

## Scope limits

- The native-only stage did not establish UI behavior by itself. Caller and Qt
  claims above are limited to the separately verified integration snapshot.
- No actual camera, OpenCV driver, GPU, codec, socket/network peer or real GL
  rendering was exercised. Controlled Event timing is not device performance.
- A callback already admitted before stop may finish on the retained camera
  worker. The production `on_frame` subscriber exists, unlike the unused TCP
  `on_result` hook. Internal frame revocation and late native-boundary rejection
  do not claim arbitrary callback cancellation. Strict close still awaits it.
- A permanently hung native call keeps strict close pending; no timeout has been
  reinterpreted as proof of release. No full aggregate runner was invoked, and
  its global lock was not bypassed.
