# Window-owned kill-audio decode retirement

## Separate from the intermittent activation-probe timeout

The stage-2 aggregate recorded a 30-second subprocess timeout in the frame-timer
creation failure probe. It was not reproduced in 130 repeated instrumented timer
probes or two full five-case runs on the same affected source. This does **not**
identify or resolve its cause. Diagnostic commits `16c3c6c` and `893faa3` retain
faulthandler output/captured streams on future timeouts, keep the 30-second limit
and every oracle, flush completion output, and work on Python 3.10. Integration
must keep the intermittent failure visible until subsequent evidence resolves it.

## Confirmed audio ownership defect

A separate source audit found that startup feedback can submit real
`kill-cue-decode` executor work. Window shutdown did not include that executor;
`KillSoundPool.close` was only connected to application `aboutToQuit`, which is
not emitted merely because a window in a reused host application closes.

The deterministic baseline probe held a fake decode behind a latch while running
the real executor/window/lifecycle. It observed:

- window finalized: true
- audio pool closed: false
- decode workers alive: 1
- pending conversion callbacks: 6

Commit `95761fb` adds a failing regression before the fix. Native transport starts
are inert, and the decoder function is replaced with a latch; no codec process,
network, physical sound output or hardware is used.

## Correct ownership boundary

- On the Qt owner thread, `KillSoundPool.close()` seals new work, stops playback,
  clears queued cues and cancels unstarted decode tasks without joining.
- `preload()` now rejects work after that seal, as `play()`/decode admission already
  do. Late completions cannot publish decoded media or replay pending cues.
- The bounded existing lifecycle coordinator owns a named `kill-audio-decode`
  teardown task. Its `wait_closed()` joins the executor on a teardown worker and
  does not call Qt. An unsealed pool is rejected rather than silently blocking.
- Even if owner-thread playback stop fails after sealing, worker retirement still
  cancels queued jobs and waits for the running job. The original owner failure
  remains in normal cleanup diagnostics.
- A blocked decoder keeps the window owned/unfinalized while Qt heartbeat events
  continue. Final close is admitted only after admitted decoder activity drains.

Existing nonblocking application-aboutToQuit behavior remains; the window now
adds the missing explicit worker-retirement phase. This is not a hard upper bound
for native decoder completion: its existing subprocess timeouts still apply, and
a stuck worker is retained instead of being falsely called retired.

## Focused evidence

Python 3.12.14 / PySide6 6.8.2, offscreen CI; 69 unittest tests pass:

- `test_window_audio_retirement.py`: 1 real-window latch/heartbeat/late-result case
- `test_kill_audio_regressions.py`: 27, including seal admission, nonblocking close,
  worker wait, independent playback-stop failure, closed preload and stale result
- `test_window_activation_failure.py`: 5; healthy second-window audio stays unsealed
- `test_window_async_lifecycle.py`: 21
- `test_window_constructor_failure.py`: 4
- `test_window_construction.py`: 6
- `test_audio_pool_lifetime.py`: 5
- API mutation selftest: pass, no baseline refresh
- `git diff --check`: pass

No aggregate was run concurrently with the root verifier. Native audio-device,
codec and real-driver behavior remain outside this evidence. This stage closes
one demonstrated acquired-owner gap, not all constructor/lifecycle debt.

## Independent-review correction: late executor admission

The reviewer supplied `bf85c5f` (retained here as `2aa1764`): actual
`Thread.start` can start a decoder and then raise before the executor registers
its thread. Executor shutdown alone then returns while decode is still active.
The fixed boundary guards decoder entry and sealing with one condition, counts
active decodes, and waits for them independently even if executor shutdown raises.
A task delayed before entry cannot start file/process work after the seal.

`test_kill_audio_executor_admission.py` includes both active and delayed real
executor admission cases, using inert decode callbacks. An unregistered idle
worker may exit asynchronously; the proven guarantee is no active or future
file/process decoder work, not that executor bookkeeping knows every thread.
These cases and all 27 kill-audio regressions pass (29 tests total).
