# Window composition boundary review

## 2026-10-16: Qt application ownership

The launch sequence was characterized on `71ebb0f` before changing the module
boundary (`ba30b26`). The trace covers saved surface preferences, application
creation/name, window construction/show, placement restoration, retention,
DeleteOnClose, and event-loop execution. Fresh and reused applications differ
only in the already-established surface/event-loop ownership. Failures at
surface configuration, construction, show and placement retain the original
exception object and stop all subsequent launch effects.

`qt_application.launch_qt_application(window_factory)` now owns that sequence.
It accepts a zero-argument window factory and imports neither the production
window nor a compatibility entry point. `polar_app.main()` supplies its actual
window class. The historical `polar.app_entry.main()` is a one-way wrapper to
the same launcher; `rm_client.app.main()` remains unchanged. There is no longer
a `polar_app -> app_entry -> polar_app` import dependency.

Verification with Python 3.12.14 and PySide6 6.8.2:

- `python tests/test_app_entry_contract.py`: 13 tests PASS, including two real
  Qt windows retained/released independently and preserved owned-loop exit code.
- `python tests/test_window_construction.py`: 6 tests PASS using real window
  construction/profile/lifecycle and offline native-start substitutes.
- Tests use `QT_QPA_PLATFORM=offscreen`, `CI=true`, local capture and media
  preload disabled. They do not establish desktop GPU or physical-device quality.
- No historical API/pixel/performance fixture was modified.

## Remaining scope

The main window still assembles substantial runtime and presentation state.
Removing the entry-point cycle alone does not complete constructor separation.

## 2026-10-16: Package import boundary

A forbidden-import test demonstrated that importing any pure controller under
`rm_client.ui.polar` eagerly loaded PySide6 through the package's primitive and
text-metric re-exports. `polar.__init__` now resolves its same seven named exports
lazily. Explicit imports and import-star return the original objects, and `dir`
still lists the public names. Unknown names fail without loading presentation.

- `python tests/test_polar_package_exports.py`: 2 tests PASS, including fresh
  interpreter imports of FrameTelemetry and FrameTiming with Qt/window imports
  denied. The object-identity export test passed before the implementation change.
- `python tests/test_app_entry_contract.py`: 13 tests PASS after the facade change.

## 2026-10-16: Startup navigation ownership

`StartupNavigation` owns launcher visibility, the interactive page, the outgoing
and incoming page pair, transition clock, page generation, and static-ready
clock. It also owns destination admission, second-step navigation, one-time
transition completion, and the existing environment autostart spellings. Its
module imports neither Qt, model, backend nor transport code. Seven explicit
compatibility descriptors expose those same fields to existing render/input/
configuration adapters; no arbitrary window forwarding or copied shadow state
is used.

The fallible UI effects remain at their existing boundary and in the exact
established sequence. Announcement happens after old-video discard but before
clock sampling. The morph is reset/targeted before the interactive page is
published. The page cache is invalidated before the second duration lookup.
Finishing commits the pair/generation before either GL cache cleanup, even when
that cleanup raises. These partial-state semantics were frozen on the old
implementation in `0f72403`; they were not replaced with an idealized success-only
model. Reverse, duplicate and invalid destinations remain covered.

Focused verification on the shared Python 3.12.14/PySide6 6.8.2 environment:

- Pure owner: 9 unittest methods; real-window transition contract: 6 methods.
- Assembly: 11; construction: 6; async window lifecycle: 21; tick contract: 17.
- Framebuffer generation: 27; escape menu: 13; startup settings/fullscreen: 19;
  startup texture ownership: 5. All 134 unittest methods PASS.
- Existing batch-8 settings script PASS; real API contract PASS with 1114
  established members, 540 signatures and 78 explicit private seams unchanged.
- Static quality gate PASS. The original API/pixel/performance fixtures remain
  unchanged. This stage does not claim full-constructor or GPU/robot acceptance.

## 2026-10-16: Incremental GPU preparation ownership

`GpuWarmupQueue` owns the pending iterator and actual/peak elapsed time. It
executes at most one job through explicit upload, acquire/release-context,
clock and error-reporting ports. It cannot reach a window, Qt application,
backend, timer or device. Visibility, shutdown, modal/escape and context-validity
admission remain in the real window adapter. The constructor creates only the
inert owner and the same QObject-parented inactive single-shot timer.

`2d5ea31` froze the real-window effect/partial-state traces before extraction.
The implementation retains the 3 ms soft pre-upload check, 16 ms reschedule,
text/image/unknown-kind dispatch, snapshot-before-gates behavior, and exact
failure truncation. An exhausted or failed iterator is cleared before reporting;
context release occurs before measuring elapsed time. A failed release remains
visible and preserves earlier measurements. No upload is represented as
preemptible or bounded by the timer target.

- `test_gpu_warmup_owner.py`: 10 pure-owner methods PASS.
- `test_gpu_warmup_contract.py`: 10 actual-window methods PASS.
- Existing `test_gpu_warmup_budget.py`: all 10 assertions/methods retained and
  PASS; its isolated host now creates the production owner and named aliases.
- Window construction: 6; async lifecycle: 21; frame telemetry contract: 6.
  All 63 unittest methods PASS. Real API contract and static gate PASS.
- Cache-frame drawing, the warmup resource generator and native GL code were
  not moved. Physical GPU timing remains outside this offscreen evidence.

## 2026-10-16: Failed launch ownership correction

`88ab1fd` froze a real defect, rather than a desired refactor equivalence: after
show or placement restoration raised, an actual autostart window retained an
active frame timer and pending scenario but had no application retention entry.
Subsequent real owner polling called the MQTT start boundary (recorded 0 → 1)
after the entry point had already reported failure. Both pre-fix regressions
were explicit expected failures, not passing acceptance evidence.

The optional one-method `StartupAbort` port now distinguishes windows that own
asynchronous resources. After a post-construction launch failure, the Qt owner
retains that wrapper and independently invokes its abort port. The production
window starts its existing asynchronous shutdown without interactive save
admission; the lifecycle marks it closing before preparation and replaces the
pending activation with final shutdown. No native joins or nested event loop
were added. Success ordering and all original entry fixtures are unchanged.
The original exception object, including process cancellation, remains visible.
Retention/abort errors are recorded as exception notes and logging; failure of
either diagnostic sink cannot replace the original error or skip the abort.

- `test_window_failed_launch.py`: 4 actual-window methods PASS, including fresh
  and reused applications with a deliberately blocked native close. With no
  event processing the owner remains retained, pending and unfinalized; after
  release and owner polling it closes without any MQTT activation.
- `test_qt_launch_abort.py`: 7 methods PASS, including failures during retention
  attribute/signal setup, abort and diagnostic reporting, plus confirmed release
  from the ordinary destroyed signal.
- Original entry contract: 13; real construction: 6; async close: 21 methods.
  All 51 focused methods PASS. Real API contract and static gate PASS.
- Limits: a factory that raises before returning a fully constructed window is
  not covered. Fresh-application cleanup still needs its caller to process Qt
  events; without that, ownership is retained and shutdown remains incomplete.
  A failed destroyed-signal hookup likewise retains its wrapper conservatively;
  this path is reported and is not claimed as successful disposal. Physical
  native/GPU shutdown quality is outside this offscreen evidence.

## 2026-10-16: Camera activation ownership

`10a10a0` froze 12 actual-window camera activation traces before extraction.
`CameraActivation` now owns the same capture worker, admission flag and last
accepted configuration. Its typed worker port exposes only running/error,
configure, start and stop. Frame binding/discard are explicit effects; it cannot
read Qt, the window, registry, environment, backend or model. Construction is
inert. Three fixed aliases preserve the old worker/flag/configuration access.

The adapter retains close/environment/source gates and numeric setting reads.
The owner performs dead-worker retry, no-wait revocation, reconfiguration and
accepted-configuration publication. Deployment pause uses the prior admitted
flag gate; source-away/disable use the prior worker-running gate. A stop request
is never treated as confirmation that native cleanup finished. The real worker
still rejects replacement while its previous native owner is retiring.

All prior effect/failure ordering is retained. Reconfiguration stops before
revoking the flag, then clears the frame, configures, resolves/binds the current
frame callback and calls start. A false or exceptional start keeps the old
accepted configuration. The reentrant configure fixture proves frame binding
still observes a callback replacement made during configure.

- Pure owner: 16 methods; actual adapter contract: 13 methods (12 unchanged plus
  single-owner/legacy-alias identity), all PASS.
- Existing real vision/camera runtime: 14; actual native-boundary Qt heartbeat
  holds at 80/240 ms: 2; capture stop admission/review: 14 + 5, all PASS.
- Window construction: 6; failed launch: 4; tick contract: 17; async close: 21.
  All 112 focused methods PASS, alongside real API freeze and static gate.
- No capture/native implementation, inference model, field transport, renderer
  or historical fixture was changed. This phase isolates camera admission, not
  all video/vision or all constructor responsibilities.

## 2026-10-16: Vision source and revocation ownership

`b7786a2` and `de31344` froze the actual source/registry/path/native-effect order
before extraction, including all 64 source/enabled/listen/model-presence
combinations, independent stop failures and partial selection publication.
They also capture a subtle Python observation boundary: receiver configure and
local prediction methods are bound before numeric argument reads. A reentrant
setting read may replace the worker, while the already-bound configure method
still targets the prior worker and the subsequent start observes the replacement.

`VisionSelection` therefore owns the narrower coherent boundary: the requested
source/tongji flag, three typed stoppable worker references, pre-request worker
snapshots, disabled/unrequested stop planning and independent revocation/error
collection. It does not own activation or configuration. Those expressions and
all registry/environment/path reads remain in the adapter at their established
points, rather than being hidden inside a callback bundle. The sole callback
port reports a collected stop failure. Five fixed aliases preserve worker and
selection identity for existing consumers and final shutdown.

- Pure selection/revocation policy: 13 methods; actual adapter: 16 methods,
  including 15 pre-extraction contracts and one single-owner/alias check.
- Existing vision/camera runtime: 14; vision stop admission/review: 21 + 4;
  event-held vision refresh: 1 method / 4 heartbeat scenarios. The isolated
  heartbeat host now creates the production owner and aliases; every original
  assertion is retained.
- Existing camera contract: 13; actual camera heartbeat: 2; construction: 6;
  failed launch: 4; tick: 17; async close: 21; assembly: 11. All 143 focused
  methods PASS. Real API freeze and static gate PASS.
- Native worker retirement, real device behavior, complete video/vision
  orchestration and the remaining window composition are outside this phase.

## 2026-10-16: Final constructor event-source admission

The unchanged-source audit `1173a2e` freezes a further failure-ownership defect:
`start_frame_timer` starts its QObject-child timer before returning it. An
injected post-admission exception exits construction before `self.timer` exists;
actual Qt event processing then reaches MQTT start after the caller received
that exception. This models admitted-before-exception failure, not a claim that
normal Qt timer starts routinely throw. The independent audit report is
`docs/reviews/window_constructor_failure_20261016.md`.

`create_frame_timer` now returns an inert, parent-owned precise timer. The
composition root publishes it, then guards only its final `start` with the
existing retained-abort port. Every shutdown dependency exists at that boundary.
A rejected start seals pending activation and retains the complete wrapper;
the existing asynchronous close path remains the sole cleanup owner. The old
`start_frame_timer` callable and success ordering remain available unchanged.

- Four real-window subprocess cases pass: post-effect exception, outright
  rejection, secondary retention/abort/note/log failures, and a persistently
  unavailable cleanup timer. The last case deliberately remains retained,
  closing and incomplete with zero runtime activation; fixture-only manual
  polling occurs after these assertions to drain resources.
- `e523c4f` freezes delayed real camera-close behavior. Actual Qt timer delivery
  keeps the failed window retained and incomplete while native cleanup is held;
  release yields confirmed finalization, application-owner release and a sealed
  audit logger, with zero MQTT starts throughout.
- Existing entry, postconstruction launch failure, actual construction, timer
  assembly, async lifecycle/controller and tick suites plus both new suites:
  102 methods PASS. Real API freeze and static quality gate PASS.
- No earlier partially initialized window is claimed to support abort. A
  permanently unavailable timer/event loop retains incomplete ownership rather
  than manufacturing completion. Hardware and desktop GPU behavior remain
  outside this offscreen evidence.

## 2026-10-16: Accepted profile and delayed-save ownership

`77b4c92` freezes 29 actual-window persistence contracts before extraction;
`docs/reviews/profile_save_contract_20261016.md` records their exact effect order
and four negative controls. `ProfileSaveState` now owns accepted identity and
generation, unsaved draft identity, pending-save ownership/deadline, manual-close
retry intent and the existing error-notification latch. Seven fixed UI aliases
retain the historical fields; the domain owner does not import Qt, UI, storage
or native transports. The constructor creates this owner at the former pending
save initialization point, before any startup profile work.

The owner accepts only profile identities/values and two named query ports when
queueing: identity and clock. Deferred lookup keeps the old pending-flag →
identity → clock publication points, including interrupted/reentrant queries.
Registry selection, profile export, synchronous file transactions, option
refresh and messages remain adapter effects between explicit owner phases.
Acceptance still precedes fallible selection; prior pending ownership is retired
only after that selection succeeds. Dirty-close intent remains distinct from
confirmed write success, and forced flushing does not bypass manual retry.

The baseline's synthetic re-entry outcomes are deliberately unchanged, including
clear-on-success after a nested storage callback queues a newer save. The actual
synchronous storage boundary has no callback/event-pump contract; this phase
neither claims an ordinary-user defect nor silently introduces a new stale-write
completion policy. Existing file transaction/durability guarantees are unchanged.

- Pure owner: 21 methods, including no-Qt/no-transport import, both query re-entry
  points, identity/generation, interrupted publication, stale ownership, close
  intent, successful retirement and non-finite deadline comparison behavior.
- Actual adapter contracts: all 29 original methods unchanged plus one
  single-owner/seven-alias identity check, 30 methods PASS.
- Existing identity/close/file transactions/configuration/async configuration/
  pending scenario/UI-state/construction/async close/final-admission/tick suites
  plus the pure owner: 173 methods PASS. Combined focused scope: 203 methods.
  The UI-state AST harness now creates the production owner and aliases; every
  existing assertion is retained. Actual API freeze and static gate PASS.
- This isolates profile-save policy and state, not the remaining wallpaper,
  registry/runtime transaction, or full window composition responsibilities.

## 2026-10-16: Normal video provenance and timeline ownership

`5700c6c` freezes 30 actual-window source/timeline contracts before extraction;
`docs/NORMAL_VIDEO_TIMELINE_REVIEW_20261016.md` records callback observations and
six negative controls. `NormalVideoTimeline` now owns five linked values:
requested normalized mode, published frame provenance, consumed camera sequence,
admitted backend timestamp and the independent display sequence. Five fixed
aliases preserve the established window fields.

The independently importable, Qt-free owner centralizes capture duplicate
admission, backend newness/freshness, webcam/synthetic readiness and conditional
frame retirement. Mode normalization/registry reads, deployment gates, native
frame lookup, image creation, mirroring, inference submission, luminance and GL/
glass invalidation remain adapter effects. Selection is published after old
frame clearing. A frozen `CapturedFrame` is consumed before image conversion;
backend timestamp/display admission still occurs before inference submission,
while image/source publication remains afterwards. These stages intentionally
do not claim atomic frame publication.

The legacy edge policies remain exact: empty selection has different consumption
and readiness routes; duplicate/older timestamps are rejected, NaN can be
consumed but is not fresh; future positive timestamps are fresh. Freshness still
observes the setting twice, and backend readiness three times. The window reads
workers/modules at their old callback points, including a worker replaced by a
running getter and a submit callback that clears the timeline before later image
publication. Clearing preserves capture/display counters and always discards CPU
glass before checking whether provenance itself is already clear.

- Pure timeline: 18 methods; actual adapter: 30 unchanged baseline methods plus
  one single-owner/five-alias check, 31 methods PASS.
- Existing normal frame/construction/tick/vision/camera/source activation/video
  recovery/source retry/framebuffer generation/async close/final-admission
  suites plus the pure owner: 151 methods PASS. Combined focused scope: 182.
- Both existing hero/native isolation and normal-source ownership smoke
  functions PASS. Actual API freeze and unchanged static gate PASS.
- Pixel handles, capture/inference/native cleanup and cached-renderer
  implementation remain outside this owner. No desktop GPU or device
  qualification is inferred from these offscreen traces.
