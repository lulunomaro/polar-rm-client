# Wallpaper native admission retirement

Auditor red fixture: 0184180 (local equivalent 7343093). A real loader paused
inside Thread.start before native admission; final close returned, then the
resumed worker opened VideoCapture after retirement was reported complete.

## Correction

- Reserve each wallpaper thread before calling start, with a completion event.
  Reservations are considered live even before Thread.is_alive becomes true.
- Final close vetoes new loads, cancels the current generation, waits for start
  admission outside the state lock, then joins every retained native worker.
- Replacement cannot bypass a pending admission or retiring worker.
- A request invalidated during caller-side cv2 import cannot admit a worker.
  A stale/cancelled decoder checks admission before image/video native I/O.
- Constructor and unadmitted-start failures become rejected loads with retained
  diagnostics; late-start failures cancel but retain the joinable worker until
  its own native cleanup completes. No native capture is closed by the caller.
- Existing frame retention, bounded release join, full-close behavior and
  capture-release retry contracts remain in force. No visuals/defaults change.

## Verification

From the checkout root, using the hash-pinned shared verification Python with
`PYTHONPATH=. QT_QPA_PLATFORM=offscreen CI=true`:

- tests/test_wallpaper_admission_audit.py: 5 tests pass. Includes original real
  delayed-start/close race, constructor and unadmitted-start rejection, real
  late-start/native-open barriers, import cancellation, video/image stale guards.
- The complete five-test suite repeated ten times: 50 passes.
- tests/test_ui_worker_shutdown.py: 21 passes.
- tests/test_decoder_lifecycle_quality.py: 14 passes.
- tools/quality_gate.py: unchanged baseline passes, 218 modules, 3417 functions,
  zero initialization cycles.
- git diff --check passes.

Tests use real Python threads with inert mocked native capture objects. They do
not qualify actual OpenCV codecs, native desktop rendering or hardware teardown.
Full aggregate verification and independent review belong to integration.
