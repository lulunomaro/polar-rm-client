# Concrete resource-owner evidence matrix

## Evidence update at 09:02 UTC (supersedes earlier open-cell descriptions)

- Historical aggregate `93aa839` finished **309 PASS, 12 SKIP, 3 INCOMPLETE,
  2 FAIL**. It is not a passing final-source certificate. The video retry failure
  has accepted test-order correction `909c579` (independent report `5564597`).
  The 30-second activation subprocess timeout remains unexplained: diagnostic
  `893faa3` and subsequent passing repetitions do not identify its cause.
- Constructor matrix `060f190`, independently accepted by root in `939f506`,
  discharges the named serial starter/reader, actual Paho network thread,
  MQTT cleanup thread and three decoder pipe-thread constructor cells.
  Five methods plus three rejecting mutation controls cover these cells;
  they do not certify every native constructor. Earlier instructions to add
  those exact constructor cases are complete, not a remaining task.
- Kill-cue audio retirement `cc33702` needed reviewer correction `f367729`.
  Red `bf85c5f` is retained. Independent 71-test selection plus an additional
  shutdown/decoder-error drain probe accepts active decode ownership and sealed
  delayed admission. Idle unregistered Python threads may finish asynchronously.
  Add this owner to the matrix: active/failed decode drain **T**, delayed entry
  after seal **T**, actual late executor admission **T**, physical audio **external**.
- Glass command buffer/per-draw geometry `b08427f` is independently accepted:
  86 tests and 48 complete baseline/current GL trace pairs. This discharges the
  selected core-rendering responsibility split while preserving live command
  payload reads, membership snapshot timing and failed cache publication.
- Independent detailed reports and raw evidence are in
  `audio_retirement_independent_20261017.md` and
  `glass_owner_independent_20261017.md` (review commit `b089ec4`).

Additional unresolved strict-budget evidence: root's running full profile at
`f34dbda` failed `test_dt7_worker_dispatch.py::DT7WorkerTests::test_no_device_real_time_100hz_tick_retry_budget`:
maximum 22.028550001778058 ms against the unchanged strict `< 20` ms assertion
(15 methods, one failure). Source log was independently read at
`/workspace/shared/clean-ten-hour-baseline/logs/tests__test_dt7_worker_dispatch.stderr.log`.
The reported isolated passing sample at `750c842` and diagnostics-only `b53d832`
do not establish its cause or resolve that failure. Do not label the still-running
aggregate passing or relax the measured region/threshold.

Remaining work is exact-source aggregate reconciliation, unresolved activation
and DT7 strict-budget failure diagnosis, and the bounded acquisition/state/config/input reviews
already assigned by the integrating owner. Keep native Windows/PowerShell,
GPU/device/audio/codec qualification separate. No new speculative rewrite or
schedule estimate follows from these tests. Earlier historical RED rows are
retained as evidence; their named accepted corrections determine current status.


Source inventory: `1b2f580`. Follow-on candidates are named separately below;
this document never adds runs from distinct revisions into an aggregate.
This supplements, and takes priority for immediate work selection over, the
planning ranges in `remaining_acceptance_checklist_20261017.md`.

## How to read the cells

`T` means a concrete existing test exercises the named contract with a production
owner and an inert native boundary. It is a mapped acceptance obligation, not a
claim this audit reran that test or proved all scheduler interleavings. `G` means
this review did not establish the cell from the inspected tests. `N/A` means the
owner does not perform that operation. `RED` means an executed counterexample.
Construct/start rejection and an actually admitted start-then-raise are different
cells. Fake native release proves bookkeeping, never physical neutral or driver
behavior. The evidence lists below identify exact suites and representative
methods so cells can be revisited without a new broad audit.

| Production owner | Constructor rejection | Start rejection | Admitted late error | Blocked/failed close retained | Explicit retry / replacement veto | Late-result revocation |
| --- | --- | --- | --- | --- | --- | --- |
| Serial starter + reader | G: inspected suites inject start, not constructor | T: same | T: same | T: resource-close, serial-reader | T: resource-close, serial-starter | T: serial lifecycle + starter |
| POLAR connector/writer/stopper | G at base; follow-on below | G at base; follow-on below | G at base; follow-on below | T: resource-close | T: resource-close + acquisition/async | T: serial lifecycle + acquisition/async |
| DT7 writer and CC writer | T: writer-constructor (both adapters) | T: same | T: same | T: writer-close | T: writer-constructor + writer-close | T: writer-concurrent admission (reservation cancellation); protocol-specific egress remains separate |
| Controller angle relay | T: controller-angle | T: same | T: same + independent review | T: resource-close | T: independent review + resource-close | T: late-start old-config preservation; broad traffic-generation cases not certified here |
| MQTT/Paho session + closer | G for every constructor boundary | T: paho-loop | T: paho-loop + cleanup-worker | T: mqtt-cleanup | T: mqtt-cleanup + endpoint-admission | T: mqtt lifecycle nonblocking; endpoint owner serialization |
| UDP video receiver + nested decoder | G for every constructor boundary | T: transport-start matrix | T: transport-start + udp-receiver | T: video-close | T: video-source-retry + video-close | T: udp-receiver late-socket manager close; packet-specific cases not fully mapped here |
| Decoder process + three pipe workers | G for every constructor boundary | T: transport-start matrix (all three) | T: same | T: video-close (pipes, process, reader independently) | T: video-recovery + video-source-retry | T: canceled spawn retains unpublished process; all decoded-pixel generations not certified here |
| Camera capture worker | T: camera-stop | T: same | T: same | T: camera-stop | T: camera-stop | T: late open/read/conversion in camera-stop |
| Vision local model/TCP/UDP worker | T: vision-stop | T: same | T: same | T: vision-stop | T: vision-stop | T: model load, inference, socket packet, parse/postprocess, JPEG copy/encoding |
| Field log writer | T: field-log-admission | T: same | T: same, including successor | T: field-log lifecycle | T: lifecycle + independent review | T: close during admission, in-flight and queued accounting |
| Wallpaper decode worker/capture | G | G | G | T: ui-worker-shutdown, already-live owner only | T: same, already-live owner only | **RED: delayed admission opens native capture after completed close** |
| Thumbnail capture + manager executor | N/A for capture bookkeeping; executor construction not established | G for executor submit/start failure | G for admitted submit wrapper failure | T: ui-worker-shutdown capture failure + executor drain | T: explicit thumbnail retry; concurrent direct load/close is outside normal drained-executor contract | G for all queued/cancelled future interleavings |
| GL compositor and specialized renderers | T for enumerated partial native allocations; Python worker admission N/A | N/A | N/A | T: shader/initialization/specialized owner suites | T: shader/startup-texture explicit retry; framebuffer follow-on below | Frame generation/cache commit is a render contract, not a thread-admission cell |

### Test/source anchors

Paths below are under `tests/`; production owners are in `rm_client/backend/`
except camera/vision/startup helpers and UI GL owners.

- Serial: `test_serial_starter_admission.py` exercises never-admitted starter,
  late open, finished-then-raised, strict unjoinable-owner rejection;
  `test_serial_reader_admission.py` exercises retained driver after reader
  rejection and explicit retry. `test_resource_close_ownership.py` covers
  `test_serial_failed_open_cleanup_cannot_discard_ambiguous_stream` and manager
  transition recovery. `test_serial_lifecycle_regressions.py` covers old frames,
  stale intent at the TX lock, short writes and lingering-worker replacement.
- POLAR: `test_resource_close_ownership.py` covers worker/stop serialization,
  nested TCP socket retention, failed close and rejected late open;
  `test_polar_connection_acquisition.py` / `test_polar_async_connect.py`: acquisition is a follow-on suite to verify at
  integration; async connect already exists at base.
- DT7/CC: `test_writer_constructor_admission.py` uses the real adapters with fake
  handles and both adapter kinds. Tests include live/finished late errors,
  rejection during cleanup, retained close retry, discarded exception/GC,
  retention-callback failure and manager replacement veto. `test_writer_close_ownership.py`
  covers writes and failed close; `test_writer_concurrent_admission.py` tests a
  second caller cancelling a reservation. `test_writer_thread_owner.py` alone
  would not discharge the adapter rows.
- Angle: `test_controller_angle_admission.py` and
  `test_admission_ownership_independent_review.py::IndependentAdmissionReview`
  cover phantom-reservation rejection and retained old configuration;
  `test_resource_close_ownership.py::ResourceCloseTests` contains the
  retained angle-close case (use its method name if the class is renamed).
- MQTT: `test_paho_loop_admission.py` includes wakeup sockets and completed/live
  start-then-raise. `test_mqtt_cleanup_ownership.py` checks actual Paho detachment,
  failed socket retention and original thread survival. `test_cleanup_worker_admission.py`
  covers admitted closer ownership. `test_mqtt_endpoint_admission.py` covers
  failed owner and explicit retry after endpoint repair.
- Video/decoder: `test_transport_start_admission_matrix.py` iterates
  `mjpeg-in-h264`, `mjpeg-dec-h264`, `mjpeg-err-h264`, rejected/admitted starts;
  the native session/worker bookkeeping is production but pipe worker bodies
  are stubbed in that matrix. `test_video_close_ownership.py` separately tests
  failed pipe close, unreaped process and still-live reader. Also
  `test_udp_receiver_admission.py`, `test_video_source_retry_admission.py`,
  `test_video_recovery_admission.py`, `test_cleanup_worker_admission.py`.
- Camera: `test_camera_stop_admission.py` covers constructor/start rejection,
  finished/live late errors, delayed start, owner-thread native release,
  serialized explicit retry, old config/result suppression and callbacks.
- Vision: `test_vision_stop_admission.py` covers the analogous admission matrix,
  local-model retirement, late postprocess/encoding, socket close, queued retry
  revisions and owner-worker self-close rejection.
- Logger: `test_field_log_admission.py`,
  `test_field_log_lifecycle_quality.py`, and
  `test_admission_ownership_independent_review.py`. Accepted/written/pending/
  dropped/errors are asserted separately; drained does not mean intact.
- Wallpaper/thumbnail: `test_ui_worker_shutdown.py` methods prefixed
  `test_wallpaper_`, `test_explicit_wallpaper_`, `test_thumbnail_`,
  `test_explicit_thumbnail_` cover live worker/capture retirement, not the
  pre-start reservation. `StartupWallpaperManager.close_thumbnail_workers`
  drains the executor before `ThumbnailDecoder.close`; direct concurrent
  decoder close is not the same public ownership contract.
- GL: `test_compositor_initialization_ownership.py`,
  `test_compositor_shader_ownership.py`, `test_specialized_gl_owners.py`,
  `test_startup_texture_ownership.py`. Success pixels and native context
  correctness remain separate from retained-handle accounting.

## Executed highest-risk counterexample

`0184180` adds `test_wallpaper_admission_audit.py`. It uses real Python threads,
a barrier before the wallpaper worker's actual `Thread.start`, and a fake
`cv2.VideoCapture`. While admission is paused, `VideoWallpaperPlayer.close`
returns success because `_stop_worker` retains only already-alive workers.
When admission resumes, `_decode_loop` calls the native factory before checking
cancellation. The recorded native-open call observes close already completed.
The one test fails at `1b2f580` production; raw output is in
`../evidence/clean-code-20261017/wallpaper-admission/red.log`.

This is stronger than a missing-test observation. It violates completed-close
truthfulness and can allow decoder work after its manager/window retired.
A correct resolution may retain admission until close finishes, or guarantee
cancelled admission cannot enter native work. It must also block replacement
while an old reserved/admitted owner can still act. No real camera, video,
firmware or GPU was accessed by the reproducer.

## Named follow-on results, not integrated acceptance

- POLAR `9c80e74`: transport author reports seven admission tests, with six
  failures reproduced before correction. Independent render reviewer acceptance
  is owned by that lane; this matrix does not count it as a base-source pass.
- Framebuffer `99c96e9`: transport reviewer reports 70 compositor tests passing;
  native driver behavior remains unqualified. Again this is candidate-specific.
- Window `fb271c8`: this audit independently ran 51 finalization/lifecycle tests
  and 26 callback-lookup failure comparisons against `54650e4`; all pass.
  This resolves the reviewed lazy-lookup/seal-order extraction regression.
  Earlier partial-constructor ownership is a different gate.

## Next finite evidence steps

1. Fix and independently rerun the wallpaper red case; add constructor/start
   rejection, admitted late-error and replacement-veto cases for this owner.
2. Review actual constructor boundaries for serial, MQTT and decoder/UDP before marking
   their `G` cells complete. Existing start tests are not constructor tests;
   only add production changes if a concrete counterexample is found.
3. Exercise real ThreadPoolExecutor submission rejection/cancellation with fake
   native thumbnail capture, including queued work during shutdown. Preserve the
   manager's drain-before-decoder-close contract.
4. After those focused owner cells are resolved, run the integrating owner's
   exact-source aggregate once. Do not rerun the already-covered admission
   families merely to manufacture larger counts, and do not infer native
   qualification from a green matrix.

### Follow-on independently accepted wallpaper correction

`643accd` discharges the wallpaper delayed-admission RED at that candidate,
not retroactively at the base. Independent review passes the 40-method focused
selection and three additional real-thread/inert-capture probes. Constructor and
never-admitted rejection, live/finished late start error, blocked admission,
replacement veto, stale import/decode and retained failed release with explicit
retry now have named focused evidence. Details:
`wallpaper_admission_independent_20261017.md`.

A separate constructor probe establishes the UDP G cell is a real defect at base:
`6b9481e` rejects `threading.Thread` construction before any socket call.
`UDPVideoReceiver.start` leaves `running=True` and an empty session, causing the
next start to return without trying admission. This is a phantom status/retry
failure, not a claim of leaked native socket. Its production correction remains
with the transport lane; preserve the distinction from wallpaper's post-close
native-open violation.

### Follow-on independently accepted UDP constructor correction

`8a354c0` discharges the receiver constructor RED at that candidate. Independent
review passes 19 focused methods including the original red, start/late-start,
nested cleanup and retained retry neighbors. No original oracle was changed.
See `udp_constructor_independent_20261017.md`. The broader MQTT/decoder
constructor cells remain evidence gaps, not asserted defects.

### Follow-on independently accepted thumbnail ownership correction

The executor admission gap became a concrete red at `d6c136b`: actual executor
start-then-raise omitted an active native reader from its join set. Correction
`a23999f` independently passes 32 focused methods and one two-worker native
acquisition/drain probe. Decoder-level terminal admission and activity ownership
cover work even when submit returns no future or executor shutdown misses a
worker; native release/failure retention precedes activity decrement. The
thumbnail constructor/late-admission/drain cells now have explicit candidate
scope evidence. See `thumbnail_admission_independent_20261017.md`. This replaces
the earlier “next evidence gap” status for these specific cases, not all possible
executor behavior or native decode qualification.

### Additional early-construction owner: audio Qt signal listeners

The early-window investigation established a distinct QObject ownership defect:
a UI audio pool and two effects survived ordinary factory-drop/failed construction
because connected callbacks retained their owners. Baseline `53e527e` probe exits
1; weak-listener correction `9dd5a80` exits 0. Independent transport review passes
47 audio-discovery tests and verifies parentless pools, weak global registry,
traceback lifetime and test mock-history controls. While live, callbacks preserve
current handler lookup and voice/status/error arguments; deleted/unowned targets
become no-ops. Exact evidence is committed at `93db40a` under
`../evidence/clean-code-20261017/audio-independent/`.

This supplementary owner is not a native-thread admission row in the 13-owner
matrix. It discharges the specific early audio retention defect and demonstrates
real Qt object lifetime in offscreen tests. It does not close the entire window
partial-constructor ledger or qualify physical audio playback/codec teardown.
