# Backend lifecycle boundaries

The backend exposes the same synchronous CLI/test API and asynchronous UI API.
`BackendManager` still captures scenario settings and performs owner-thread
activation. The lifecycle assembly and completion state now have their own
boundary instead of remaining another responsibility of the manager.

## Ownership

- `BackendLifecycleController` owns transition admission, latest-request
  coalescing, final sealing, resource/audit phases, retries, and immutable
  completion results. It receives three named owner actions: invalidate runtime
  caches, finish stopped-runtime bookkeeping, and activate a captured scenario.
  It does not receive a manager, registry, window, or Qt object.
- `TeardownResources` is a frozen set of captured transport owners. It performs
  ordered owner-side authority revocation and builds strict close operations.
  It captures transport objects, not snapshots of mutable native handle fields.
  An already-admitted serial starter or decoder recovery worker remains joined
  by the strict close adapter before release can be confirmed.
- `LifecycleCoordinator` supplies bounded, single-flight background task
  execution. Workers only close their captured resource and report one result.
  The owner polls results and alone may activate a replacement.
- `BackendManager` retains registry-to-request capture, actual configuration and
  startup, and the owner-thread input mailbox. It delegates transition state to
  the controller without allowing workers to call back into the manager.

## Ordering and outcomes

1. Validate request admission before changing authority or invoking observers.
2. On the owner thread, latch E-stop, admit independent neutral requests,
   invalidate connection authority, and invalidate runtime caches.
3. Close independent resources off-thread. A blocked or failed native close
   keeps its owner retained and prevents replacement.
4. Consume completion on the owner. Runtime cache or input observer failures
   remain visible but cannot abandon native teardown or audit draining.
5. A successful nonfinal completion may activate the latest captured scenario.
   An incomplete input clear during activation fails before transport startup.
6. A final completion seals the audit after resource diagnostics are admitted.
   Native release and audit integrity remain separate evidence.

`ShutdownCompletion.may_exit` requires confirmed native release and a drained
writer. Historical audit loss or an owner callback failure still makes
`succeeded` false. The UI must report these warnings rather than relabeling the
entire operation successful. The original completion stays immutable across an
explicit retry; only ended failed resource tasks can be retried. Polling never
starts another closer for a blocked task.

The input mailbox retains a failed clear and continues blocking qualification,
arming, and egress. Its failure is an observer diagnostic, not invented evidence
that a native resource remains open. A late observer failure during the audit
phase is included in final completion too.

## Verification scope

The extraction stage passed 253 tests across 21 suites, including actual manager,
serial/POLAR/TCP/UDP/decoder/Paho retained-owner paths, final retries, persistent
input failures, and real offscreen window closure. Nine direct controller tests
exercise the collaborator without constructing a manager or UI. One composes a
real serial backend whose driver handle appears after teardown admission, fails
close, remains retained, then closes only on an explicit retry.

Run focused boundary verification from the repository root:

```sh
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen /tmp/client-quality-venv/bin/python -m unittest \
  tests.test_backend_lifecycle_controller tests.test_backend_async_lifecycle \
  tests.test_backend_shutdown_retry tests.test_backend_input_owner \
  tests.test_backend_lifecycle_callback_failures tests.test_backend_lifecycle_input_failure \
  tests.test_resource_close_ownership tests.test_video_close_ownership \
  tests.test_writer_close_ownership tests.test_mqtt_cleanup_ownership \
  tests.test_window_async_lifecycle tests.test_window_lifecycle_controller
python tools/quality_gate.py
```

This is stage-local behavior evidence, not a complete integrated acceptance run
or hardware qualification. Serial drivers, robot firmware neutralization,
cameras, real network peers, and native desktop/GPU behavior remain unverified
by fake-driver and offscreen tests. Deferred import cycles and unrelated manager
and UI responsibilities also remain visible in the full quality report.

## Video source stop admission

`ContinuousMjpegDecoder.request_stop` and `UDPVideoReceiver.request_stop` revoke
current epochs immediately and retain a single background closer. New starts
cannot replace retained sessions or live stop workers. A decoder closer observes
the process lifecycle lock before collecting retired owners, so a Popen already
in progress remains included. Empty decoder stops advance the epoch without
creating a redundant worker when no native owner or spawn is in progress.

`RmVideoBackend` revokes both packet and HEVC epochs and independently admits
socket and decoder teardown. The manager's existing video-source setter uses
these explicit nonblocking requests. It publishes the selected source on the
owner thread; native teardown does not apply UI/configuration callbacks.
Synchronous `stop` and `set_decode_enabled` remain available. Strict final-close
adapters join admitted stop workers and still check native release. A
`wait_closed(timeout)` timeout bounds its joins, not an operating system's
possibly blocked native close; only worker/CLI callers may use that method.

`tests.test_backend_video_source_nonblocking`,
`tests.test_video_stop_nonblocking`, and `tests.test_udp_stop_nonblocking` cover
blocked native waits, rapid source changes, failed close ownership, late socket
and process creation, worker admission failure, and final-close composition.

A source request is accepted only after independent stop/start admissions have
returned. An admission exception clears the active-source label, suppresses frame
consumption/recovery, and retains the requested target plus every named phase
failure. The first exception remains the raised error's cause. UDP socket,
HEVC decoder, and Native 0x0310 revocations are attempted independently, even if
recovery-cache reset or another worker admission fails.

`video_source_snapshot` separates the requested/accepted labels, pending native
owners, and admission failures. Pending decoder work includes an unpublished
Popen still inside the process lifecycle lock, including after stop-worker
admission failed. It is a momentary status snapshot; final shutdown still uses
strict `wait_closed` evidence. A selected target with pending owners is not proof
that its frames or replacement native process are ready.

Ordinary same-target refresh preserves and raises an admission failure without
restarting teardown workers. The owner-thread `retry_active_video_source()` API
explicitly retries a failed admission or retained close. The manager rejects a
retry while its read-only `inflight_resources` contains a running closer,
recovery worker, or unpublished process operation; transport-level stop requests
still coalesce. Successful ordinary refresh never retries failed native closure,
including a selected UDP target waiting for an old native owner. UI adoption of
this API is a separate stage.

The snapshot's monotonic `generation` changes on each new accepted request or
explicit retry, not on periodic same-target refresh. A retry may supply
`expected_generation` to reject an A-to-B-to-A stale click before any admission.
`close_failures` comes from retained sessions' current native-close evidence,
separately from synchronous admission `failures`. Historical `last_error` is not
used to infer ownership. `retryable` is true only when a request has failed or
retains pending owners and no cleanup work is in flight. Snapshot reads never
wait for a native close lock; these values remain presentation/admission evidence,
not a replacement for strict final shutdown confirmation.

A live retired pipe reader or UDP receiver is a retained **data owner**, not by
itself an in-flight cleanup operation. If stopper admission failed, an explicit
retry must remain available: terminating the process or closing the socket may
be exactly what lets that reader exit. The newly admitted stopper then makes the
snapshot busy and prevents another closer. Earlier independent tests that called
live data workers busy even with no closer encoded the wrong progress model;
they were corrected alongside actual blocked-reader regressions. Strict final
shutdown still joins and confirms these data owners before allowing exit.

Direct referee-profile changes are dependent applications, not atomic rollback
transactions. Native stop admission precedes publication of the new local
profile. `referee_profile_change` records requested, manager, MQTT, and frontend
profiles plus pending/error state, so an intermediate transport update remains
visible if a later adapter, cache, or source operation fails. Such a failure
keeps work, qualification, arming, and egress gated. An explicit same-profile
setter call or a full scenario request retries application; ordinary polling does
not. Profile equality skips work only after a completed application. Successful
acceptance remains distinct from confirmation that retained native teardown has
finished.

Decoder recovery admission keeps a thread reference only when it has actually
started (`Thread.ident` remains present after completion). If `start()` raises
before admission, the empty slot is released and a typed error plus bounded
backoff remain. If it raises after the real thread starts or finishes, that
joinable owner is retained for strict shutdown. Final-close adapters do not
swallow join errors or infer native closure from an admission failure.

Paho loop admission is adapted by `MqttClientResources.start_network_loop`.
The installed vendor creates wakeup sockets and publishes a thread object before
calling `Thread.start`. On a failed admission, both sockets are retained, while
a never-started thread is removed before the vendor's `loop_stop` can try to join
it. A started or completed thread remains owned. The normal cleanup path still
checks every wakeup/native socket and retains a failed close for explicit final
retry; no loop-stop/join exception is broadly ignored.

The late-raising start-wrapper fault model also applies to coordinator workers.
A real admitted closer remains the sole producer of its resource result. Its
start-wrapper exception is a bounded, separately stored `warnings` diagnostic;
it cannot consume the pending slot, enqueue a second result, or enable retry
while the original closer runs. Never-admitted tasks still produce one genuine
admission-failure result. Generic completion success describes resource results;
its warnings remain separately inspectable. Warnings persist across an explicit
retry of a different, actually failed close without replaying successful jobs.

Final backend completion adds these warnings to its existing user-visible
diagnostic list. Thus `succeeded` is false when a warning exists, while
`resources_released` and `may_exit` use only real resource/audit evidence. The UI's
existing one-time final warning path can report the diagnostic before safe exit.
This injection model is a wrapper raising after ordinary `Thread.start` already
admitted work; it is not a claim that normal operating-system admission does this.
