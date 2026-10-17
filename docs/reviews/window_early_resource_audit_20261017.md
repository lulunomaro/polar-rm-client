# Early window acquisition and failure-cleanup ledger

## Scope and result

Reviewed source: `97228a73b90e7db73533af7e07a8f5a7e8fb0716`, 2026-10-17.
This is a read-only production-source review plus a documentation addition. It
addresses gate C of `remaining_acceptance_checklist_20261017.md`; it does not
replace the accepted late-finalization or individual transport-owner reviews.

**The early-constructor ledger is now enumerated, but exhaustive failure cleanup
is not established.** No new resource-leak defect is asserted by this report.
There is no encompassing early-constructor abort boundary. The existing retained
abort starts at `polar_app.py:792`, after the services below have been assembled.
The outer application launcher calls `window_factory()` before its own try block
(`ui/polar/qt_application.py:87-89`), so it cannot recover the partial object when
that call raises. This is a source-proven boundary limitation, not a proof that
every earlier exception leaks an active resource.

Status terminology:
- **Source:** directly traced construction/publication behavior.
- **Tested:** a named check was executed in this review.
- **Gap:** failure-prefix or native lifetime behavior not established here.
- **External:** physical device/driver/codec/GL behavior requiring qualification.

## Ordered acquisition ledger

Line references below are to `rm_client/ui/polar_app.py` at the pinned source
unless another file is named. Pure scalar/container/UI-state initialization is
grouped; grouping does not assert that every constructor is incapable of raising.

| Order and publication | Acquisition and ownership traced | Failure/retirement boundary and evidence |
| --- | --- | --- |
| 475-486: Qt base, `_gpu_prewarm_timer` | `QOpenGLWidget` base exists first. `window_assembly.create_warmup_timer` creates a parent-owned single-shot QTimer and connects the callback without starting it. Pointer, tick, warmup state are Python owners. | Tested timer parent and inactive state in `test_window_assembly`. Partial-window native destruction after each widget-setting failure was not faulted. No GPU context initialization is established merely by these Python assignments. |
| 488-503: paths, registry, state, scenario/match/GUI managers | `prepare_runtime_paths` discovers paths, creates writable directories, then seeds default configuration. Subsequent objects establish model/policy state. | Path preparation ordering and failed-preparation suppression of seeding are tested. Filesystem effects already completed are not transactional rollback; this review does not require deleting user directories/defaults on constructor failure. Transitive state constructors are not exhaustively faulted. |
| 504: camera activation owner | `vision.CameraCaptureWorker.__init__:329-353` creates locks/events and sets `_thread=None`, without opening a camera. `CameraActivation` publishes that worker through the facade. | Source-traced inert construction. Normal shutdown tasks include camera close at 3265. Early failure after publication has no retained-abort guarantee. Actual capture acquisition belongs to later `start`, outside this row. |
| 506-514: backend, lifecycle, aliases | `backend/manager.py:103-215` constructs safety, MQTT/video/serial/angle/POLAR services, decoder and logger metadata; publishes `WindowLifecycleController` only after BackendManager returns. Decoder constructor has `_proc` and thread fields unset (`video_backend.py:746-791`); logger constructor has `_worker=None` (`backend/field_log.py:32-71`). `_apply_config` uses `set_active_video_source(..., start=False)`; fresh MQTT address changes start only when `_started` was already true. | Source-traced staged transport intent. This is not a complete transitive-constructor fault matrix or a claim that every nested library constructor is resource-free. An exception inside BackendManager occurs before `self.backend` is assigned. Existing native admission matrices remain separately scoped evidence. |
| 515-523: match observer, feedback, toggle listener | TopMatchBarController stores the window and renderer; backend stores its bound observer. `create_feedback_assets` constructs KillFxRenderer first, UiAudioManager second, and returns only after both succeed. Window then publishes `kill_fx` and `ui_audio`, then registry callback. `UiAudioManager` creates/migrates custom files, owns `ReliableAudioPool`, preloads sound effects and may preload media. Audio pool is a QObject with parent-owned devices/effects/media objects and application signal connections. KillSoundPool starts with `_executor=None` (`kill_audio.py:103-119`). | **Tested:** five `test_audio_pool_lifetime` cases, including failed later vision factory and successful close, prove the specific native-QObject retirement observations. They do not prove real codec/device retirement. Failure inside the feedback factory after the first service exists remains a distinct prefix from the tested later-factory failure. Production media preload can do more work than the explicit headless test path. |
| 532-548: compatibility pipeline, control, input hook, economy/config | Backend aliases, VideoPipeline, KeyboardControlBridge and sender callbacks establish references. The startup lock cleanup is invoked at 543 before later services exist. `ClientConfigStore` then creates its configuration directory (`config_store.py:58-60`). | Source establishes callback cycles and the early cleanup call. A failure at startup cleanup escapes before the abort region; it must not be equated with the later modal/lock failure-isolation guarantees. Exact retention of every callback cycle is untested here; a completed config-directory creation is a persistent filesystem effect. |
| 549-559: vision factory and publication | `window_assembly.create_vision_services` creates calibration and loads `polar_webcam` before constructing receiver, command cache, composite pipeline, local vision and bridge. Receiver/local/bridge constructors publish inert owners: no running threads, receiver socket unset, local detector unset. Factory result is then distributed into the window's selection owner and aliases. | Tested inert service fields and calibration-load failure preventing receiver construction. No fault at every later member construction/publication has been injected. Normal close enumerates local vision, receiver and bridge at 3266-3268. |
| 572-575: wallpaper and initial scenario/match application | `StartupWallpaperManager.__init__:743-776` creates package/image metadata, an inert VideoWallpaperPlayer, a ThreadPoolExecutor object, then ThumbnailDecoder. No thumbnail submission appears in that constructor. Scenario application updates registry and transport/control settings; `scenarios.py:162-179` calls MQTT address setter rather than a start API. | Tested wallpaper video thread unset and path wiring. Executor existence is an acquired service even before task admission; no prefix test here proves shutdown if ThumbnailDecoder or a later factory raises. Normal close separately drains video and thumbnail owners (3269-3270). Do not substitute active-thumbnail admission tests for this factory-prefix gap. |
| 576-591: presentation services | `create_presentation_services` loads fonts, creates shared animation/motion, cache and renderer/UI owners, then publishes the aggregate. `ui/fonts.py:29-60` registers discovered fonts with process-wide QFontDatabase; returned font IDs are not retained for removal. Asset images and other renderer internals were not individually lifetime-audited. | Tested animation sharing/instance separation, renderer initial state, and font-load failure suppressing later GPU-renderer construction. Process-wide font registration is an observable non-rolled-back side effect, not by itself an unintended leak. Repeated failed construction and internal renderer failure prefixes remain gaps. |
| 605-641: GPU facade services | `create_gpu_services` constructs renderer/compositor wrappers; actual allocation/shader initialization is deferred to the context owner. Window publishes individual aliases and CPU image/draw buffers. | Tested unavailable/uninitialized owners, no compositor background FBO, and distinct startup/HUD instances. Normal finalization releases named GPU owners under the GL context (3302-3317). Native allocation and partial native initialization are outside this early Python-factory result. |
| 642-783: remaining presentation/navigation state | Scalar/cache/image/layout state, navigation/timeline state, overlays, chat and notices. OverlayCoordinator retains the modal input callback. Frame scheduler exists, but the frame timer has not yet been created or started. | Source ordering. No exhaustive failure injection of this long state-initialization suffix. A failure here occurs after all preceding services, still before the retained-abort boundary. |
| 792-836: activation boundary, for comparison | Profile/presentation/scenario activation, shortcut installation, frame timer creation and start are within `except BaseException`, which invokes `_abort_failed_launch` and rethrows. | Existing accepted activation/finalization evidence applies here. It must not be extended backward to 475-791. `_begin_window_shutdown` eagerly refers to complete camera/vision/wallpaper/audio owners, so calling it on an arbitrary earlier prefix is not an established repair. |

## Executed checks

Interpreter: `../client-verification-venv/bin/python`; Qt offscreen; existing tests
explicitly disable local capture and media preload, use isolated temporary user
directories, and avoid physical device output.

- `python -m unittest discover -s tests -p test_window_assembly.py`: **12 PASS**.
- `python -m unittest discover -s tests -p test_audio_pool_lifetime.py`: **5 PASS**.

These 17 methods were rerun on the pinned source. They establish the specific
assertions listed above, not all acquisition failure prefixes. Qt reported
offscreen/audio-environment warnings; physical playback was not qualified. No
full profile, native Windows run, GL rendering or robot/serial test was performed
for this document.

## Bounded next evidence needed

1. Fault each aggregate factory and later publication boundary after the last
   successfully acquired owner. Record both Python weak-reference and valid
   native-QObject state, plus worker/process/socket/retained-handle ownership;
   do not use garbage-collection success alone as a native close certificate.
2. Separate failures *inside* feedback, backend, vision, wallpaper, presentation
   and GPU factories from failures after those factories return. In particular,
   test wallpaper executor acquisition followed by decoder-constructor failure,
   and first feedback-service success followed by second-service failure.
3. Preserve process-owned or persistent effects explicitly: font registrations,
   runtime directories, seeded configuration and migrated user sounds are not
   automatically window-owned disposable resources.
4. Use actual playback/decoder/device qualification separately for production
   media preload. The existing weak-callback regression closes its named
   QObject-retention defect only.

This ledger closes the documentation inventory gap. Exhaustive early-constructor
cleanup acceptance remains open until the bounded failure matrix supplies
evidence; no blanket rewrite or whole-client clearance follows from this audit.

## Follow-up: three bounded factory-prefix probes

Source `f457c6d222f25ba09d275eab557029d4bbe92492`, 2026-10-17. Added
`tests/test_early_window_factory_failures.py` and ran its three methods with
`../client-verification-venv/bin/python`: **3 PASS**. No production code was
changed for these probes. The original ledger above remains historical evidence;
this addendum narrows the following specific gaps.

1. **Feedback first service succeeds, second constructor rejects.** The actual
   KillFxRenderer and its real native SoundPool are constructed; UiAudioManager
   admission then raises. The original exception is preserved. After references
   unwind and Qt/GC events drain, the renderer weak reference has retired, no
   valid native pool remains, and its QObject `destroyed` signal was observed.
2. **Feedback failure inside the second service.** UiAudioManager constructs its
   real ReliableAudioPool, then injected preload failure raises. Both first and
   second native audio pools emit `destroyed` and neither remains valid after
   draining. This is stronger than a Python weak-reference-only observation.
3. **Wallpaper executor acquired before ThumbnailDecoder rejects.** The actual
   ThreadPoolExecutor and VideoWallpaperPlayer are constructed. The injected
   decoder-constructor failure occurs after executor publication. Its thread set
   is empty at acquisition and failure; no `threading.Thread.start` call occurs.
   Both owner weak references retire after unwind. No active native worker was
   acquired in this prefix, so absence of an explicit executor shutdown here did
   not reproduce an active-resource leak.

The tests use temporary user directories, empty assets, offscreen Qt, and the
existing media-preload/local-capture disabling settings. QMediaPlayer and
QSoundEffect source-loading/playback methods are patched. **Real QMediaDevices
construction can still initialize a platform multimedia service such as
PulseAudio.** Thus these tests do not claim that native multimedia initialization
is absent, nor qualify physical audio output, driver shutdown or real codec
cleanup. Source-loaded/preloaded production media remains outside these probes.

No actual retained-owner/resource-leak defect was reproduced in these three
prefixes. They narrow the feedback and idle-wallpaper factory gaps; they do not
discharge every early-constructor failure, an already-running executor failure,
or the production-media qualification still identified above.

Diagnostic follow-up at the same production source: the retained-native-object
assertion compares the observed count, avoiding repr of a rescued/deleted Qt
wrapper. The three methods pass again. Two in-process rejecting controls that
deliberately retain respectively the first feedback renderer and the wallpaper
executor each produce **one assertion failure, zero errors**. These controls
show the corresponding retirement assertions reject retention; they are not
production defects or extra qualification of native media.

## Actual early-window prefix follow-up

At local source `a4d8774334586572d17ae06370170606a390b8b3`,
`tests/test_window_early_constructor_ownership.py` adds three actual offscreen
window/factory failures: ClientConfigStore construction, partial vision bridge
construction, and partial presentation construction. Three methods passed both
author and independent runs. Two deliberate retained-owner controls (window and
wallpaper executor) each failed cleanly. No production leak was reproduced.

The probes observe native window/timer/audio destroyed signals and weak-reference
retirement of backend, camera, vision, wallpaper/video/executor and renderer
owners. The real early field-log worker may exist before activation; it retires
through its existing one-second idle path. Tests allow three seconds per captured
thread, not a global three-second deadline. Rescue follows the observations and
cannot convert retained-owner evidence to success.

These are bounded failure-prefix checks, not exhaustive constructor coverage.
Native multimedia initialization may still occur; playback/source loading is
suppressed. Physical audio/decoder/device cleanup and audit-log durability remain
unqualified. The tested window is not passed to a universal partial-object close
routine, and no such routine is established as safe by these results.


## Startup-lock actual-window prefix — 2026-10-17 20:40 UTC

The previously unverified startup lock cleanup prefix now has bounded evidence.
Test checkpoint: public `f425fbc5124b86262292d0c95c98b170883da283`, local
`a6cff9bac6151f6298ec7aa521d582266fc28ffb`, tree
`7d39fb342a0c4f8916c038fda8597ad147d3c7b0`. Production code was unchanged;
**no retained-owner leak was reproduced**.

`tests/test_window_startup_lock_failure.py` constructs the actual offscreen
PolarRMWindow and injects reset failure at the startup lock hook, before
ClientConfigStore and the retained abort boundary. Its two methods cover the
primary failure and an additional DT7 neutral-cleanup failure. Both preserve the
original exception, observe reset/DT7/custom-controller/pending cleanup order,
and assert that configuration construction was never reached. Only diagnostic
text is retained; exception/LogRecord traceback roots are released deliberately
before lifetime observations, rather than mistaken for production ownership.

The observed window, warmup timer and both audio pools emit native destroyed
signals; captured backend, camera, control and field-log references retire.
Only the real field-log worker may start, and its existing idle retirement is
observed with a three-second **per-thread** join, not a global shutdown SLA.
No universal partial-window close is called. Playback/source loading and capture
are suppressed; native multimedia service initialization can still occur.

The independent log `/tmp/startup-lock-independent.log` records **5 PASS**:
two new methods plus three neighboring early-window constructor methods.
Independent deliberate window and kill-pool retention controls each yielded
**1 assertion failure, 0 errors before rescue**, as reported by the reviewer.
These controls are test sensitivity evidence, not production defects.

### Completed aggregate update — 2026-10-17 20:47 UTC

Read directly from `../client-startup-lock-full/results.json`, run
`3f80f3462d5c4118bdb8fb638acff378`: **completed, 347 PASS / 12 SKIP /
4 INCOMPLETE / 0 FAIL, NO-GO** (`passed=false`). Tested source is exactly public
`f425fbc5124b86262292d0c95c98b170883da283` / local
`a6cff9bac6151f6298ec7aa521d582266fc28ffb`, tree
`7d39fb342a0c4f8916c038fda8597ad147d3c7b0`, not a later documentation commit.
All 696 declared verification inputs have identical start/end SHA-256
`6cc686c50599abfb5801dfdbba299249f345e2bbae854dda338804d0dcc9f15c`.
This fingerprint excludes documentation/assets/external fixtures/installed
packages and cannot detect transient edits restored between captures. No full
run remains running at this update. Earlier field-log 346 PASS / 12 SKIP /
4 INCOMPLETE / 0 FAIL remains historical NO-GO evidence.

This acceptance closes only the named startup-lock prefix; it does not establish
all constructor failures, physical neutral output, native media teardown or
durable audit logging. Skipped/incomplete checks remain unaccepted.


## Complete presentation prefix — 2026-10-17 20:50 UTC

New test checkpoint: public `c4ceb891da2155eb3038d2960f3493e1eb7b9fb4`, local
`4dac2a53357c9a514bb958dc7f7a1a13162df57e`, tree
`067b71d9f5f0fdb12ae02aad81a110e9264dc7c8`. No production change or resource leak
was reproduced. `tests/test_window_presentation_prefix_failure.py` constructs
actual offscreen presentation services completely, then rejects the next
`create_gpu_services` call before that factory executes. The original exception
is preserved. All captured presentation fields, including student/chat/hero
owners, and previously acquired window/backend/vision/wallpaper owners retire;
window/timer/audio native destroyed signals and field-log idle exit are observed
before rescue. Thread joins remain three seconds per captured worker.

Author selection: **6 PASS** (one new presentation-prefix method, three early-
constructor methods, two startup-lock methods). Independent selection: **7 PASS**
(one new method, three early-constructor methods, three early-factory methods).
These are different selections, not a combined count of unique tests. Both
review lanes report deliberate chat-renderer and hero-state retention controls
producing **1 assertion failure / 0 errors each** before rescue.

No GPU service factory or native GL initialization runs at this rejected prefix.
Playback/source loading and capture are disabled; native multimedia initialization
and process-wide font registration can still occur. This is not an active-GPU
cleanup, physical media teardown, exhaustive constructor or process-global font
rollback guarantee.

### Completed aggregate update — 2026-10-17 20:58 UTC

Verified directly in `../client-presentation-prefix-full/results.json`, run
`5631b31e2f9c4d3db84daa1ed959baeb`: **completed, 348 PASS / 12 SKIP /
4 INCOMPLETE / 0 FAIL, NO-GO** (`passed=false`). Exact tested source is public
`c4ceb891da2155eb3038d2960f3493e1eb7b9fb4` / local
`4dac2a53357c9a514bb958dc7f7a1a13162df57e`, tree
`067b71d9f5f0fdb12ae02aad81a110e9264dc7c8`, not later documentation commits.
All 697 declared verification inputs have identical start/end SHA-256
`d042b413836a84354fb61804b469937edbb023585c9c629ed08cc1292ee8e9d4`.
The fingerprint scope/limits stated above still apply. No aggregate remains
running at this update. Earlier 347 PASS remains historical startup-lock
checkpoint evidence. Finite prefix coverage and native qualification limits
remain unchanged; skipped/incomplete checks are not acceptance.
