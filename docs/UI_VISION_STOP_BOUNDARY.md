# Non-waiting vision runtime changes

The window's runtime refresh requests stop with `wait=False` for local inference,
Tongji UDP input and the compatibility TCP bridge. Port/model reconfiguration
uses the same non-waiting contract. A stopped worker retains its thread and native
owner while its existing worker finishes cleanup. Repeated refreshes may start
the latest accepted configuration only after the previous owner has released it;
a retained native close error continues to prevent replacement.

This is stop admission, not proof that native release itself is fast. Final
window close still runs each strict `close()` through the backend lifecycle
coordinator. The window stays alive and processes events while cleanup is held,
and a reported native close failure requires a later explicit close retry.
A late refresh callback cannot run after window closing begins.

## Failure ordering

A backend video-source admission error first revokes all three local worker
owners without joining them, then re-raises the original error to the caller.
The profile transaction therefore still sees failure and can roll back. Each
secondary stop defect is recorded independently with its type, traceback and
existing per-stage throttling. The failed source path never configures or starts
a replacement. In a healthy source transition, all requested disabled-owner
revocations are attempted before any enabled-owner start; a stop defect prevents
new starts and is propagated after the remaining revocations.

## Evidence and limits

- Before this adapter change, the actual window refresh took 2.8008 seconds to
  stop three Event-held production worker owners. Backend admission failure also
  left all three running. Both regressions are tested directly.
- Actual-window tests require refresh below 100 ms, retained owner identity,
  Qt heartbeat progress before worker release, rapid configuration coalescing,
  no new worker admission on source failure, and strict final close completion
  only after release. A separate test retains a real receiver-loop socket after
  an injected native close error and requires explicit final-close retry.
- Only the inference bodies and native socket/process terminals are inert.
  Worker stop/start/close, backend lifecycle, window refresh and Qt event routing
  are production methods. Python network access is rejected by an audit hook.
- These are controlled scheduling and ownership checks. They do not qualify
  real cameras, OpenVINO resource-release behavior, firmware or native GL.

## Webcam caller boundary

The same admission rule now applies at the four webcam stop sites: local capture
is disabled, CameraFeed selects another source, the camera ID/size changes, or a
hero-deploy acknowledgement takes video ownership. Each uses `stop(wait=False)`.
The original camera thread remains owned; only after it exits can a subsequent
refresh start the latest ID/size. The window updates its accepted configuration
only when `start()` succeeds. A refresh during final close does nothing.

Both the normal tick and the direct video-paint entry isolate camera admission
faults through the existing `camera.runtime` typed/throttled diagnostic boundary.
A rejected camera thread no longer prevents later backend/safety ticks, frame
requests, or the CPU synthetic fallback. The camera worker owns retry backoff;
these callers do not grant extra cleanup retries.

Before correction, an Event-held camera made the actual window stop call take
1.2002 seconds. A rejected camera Thread.start escaped both `on_tick` and
`draw_video`, and the tick skipped its backend stage. Actual-window regressions
now cover all four stop sites, heartbeat while held, five coalesced ID/size edits,
strict final close, and the two fault entry points. Native camera IO is inert in
these tests. An already-admitted camera callback can finish after stop; strict
close still waits for the owning thread, and this stage does not claim to cancel
an already executing callback.
