# Backend lifecycle boundary (2026-10-16)

## Scope and contract

This stage adds an explicit asynchronous backend API. It does not claim that
all GUI call sites have been migrated, and it does not qualify real hardware.

- `request_scenario(...) -> generation` reads and validates settings on the
  caller/owner thread, captures detached values, revokes input authority, and
  returns without joining drivers. Repeated requests coalesce to one latest
  target while the existing resource owners finish.
- `poll_lifecycle() -> LifecycleCompletion | None` is owner-thread-only. A
  successful completion applies the captured settings and starts the new
  transports on that thread; serial open alone has a captured non-UI owner.
- `request_shutdown(teardown_tasks=...)` can include named non-Qt camera/vision
  stop operations. A final shutdown permanently rejects new scenario/identity
  requests. Final audit draining runs separately after resource diagnostics.
- `lifecycle_pending`, `lifecycle_closed`, and `lifecycle_snapshot` expose pending
  ownership, final completion, phases, generation, and failures.
- `apply_scenario()` and `shutdown()` retain explicit synchronous compatibility
  for CLI/tests. Interactive code must use the request/poll API.

The immediate phase runs safety/input hooks on the owner thread. Only captured
resource stop/wait operations enter workers. MQTT invalidation uses
`stop(wait=False)` on the owner thread; `wait_stopped()` cannot invoke an input
hook. Serial `request_estop()` latches without the driver-held write lock.
Bytes already admitted inside an OS driver cannot be recalled. The new latch
blocks subsequent write admission; it is not a physical neutral acknowledgement.

The coordinator bounds resources, workers, and completion storage. A stuck
resource changes diagnostics to `blocked` but retains its owner. It cannot
silently time out into replacement. Other independent resources still close.
Normal returns from boolean completion APIs are adapted explicitly: false
MQTT/device close results, non-neutral device completion, and failed audit drain
become failure records. Failures retain E-stop and block ordinary work/re-arm;
only an explicit new non-final scenario request retries teardown.

## Validation

Stage-two selected aggregate: 10 suites PASS, 0 SKIP, 0 INCOMPLETE, 0 FAIL.
The selection ran 142 unittest cases plus 461 protocol/localhost assertions.
The new coordinator has 12 deterministic tests; backend integration has 17.
Tests cover coalescing, final-close precedence, owner-thread completion and
hooks, settings snapshots, identity changes with stalled Paho cleanup, blocked
serial writes, serial-open ownership, failed teardown/apply, and E-stop
preservation. Existing shutdown regression tests were moved from AST-isolated
monolithic bodies to the actual lifecycle boundary with fake resources.

Reproduction:

```sh
python tools/verify_all.py \
  --test tests/test_lifecycle_coordinator.py \
  --test tests/test_backend_async_lifecycle.py \
  --test tests/test_device_manager_nonblocking.py \
  --test tests/test_field_log_lifecycle_quality.py \
  --test tests/test_mqtt_lifecycle_nonblocking.py \
  --test tests/test_serial_lifecycle_regressions.py \
  --test tests/test_decoder_lifecycle_quality.py \
  --test tests/backend_protocol_test.py \
  --test tests/test_transport_safety_regressions.py \
  --test tests/referee_integration_test.py
```

This is focused offline evidence, not the full aggregate or desktop/GPU,
firmware, serial/TCP device, broker, camera, or robot qualification.

## Design references

The separation follows the concurrency responsibility and limited shared-data
principles in [Clean Code chapter 13](https://aeryzhao.github.io/cleancode-book/ch13.html),
the explicit adapter boundaries in [chapter 8](https://aeryzhao.github.io/cleancode-book/ch8.html),
and the cohesive responsibility guidance in [chapter 10](https://aeryzhao.github.io/cleancode-book/ch10.html).
Further integration should move lifecycle composition out of the backend facade
without changing the request/poll contract or weakening these tests.

## Close-error ownership follow-up

Driver close errors now preserve the serial/POLAR/controller-angle handle and
its port reservation instead of treating a swallowed exception as released
ownership. Explicit false or still-open close results are also unconfirmed.
A canceled late POLAR connector retains its rejected handle on close failure.
An explicit later teardown retries the same handle; new opens stay blocked.
The actual manager lifecycle reports the failure and does not activate the next
scenario. This does not yet certify video, DT7/CC, or camera close adapters.

Selected close-ownership regression run: 9 suites PASS, 140 unittest cases,
0 SKIP, 0 INCOMPLETE, 0 FAIL. This includes 7 new actual-close failure tests,
existing serial/POLAR async tests, and manager/controller-angle safety suites.
Run the preceding lifecycle selection plus
`tests/test_resource_close_ownership.py`,
`tests/test_polar_async_connect.py`,
`tests/test_controller_angle_integration.py`, and
`tests/top_match_bar_integration_test.py` for the additional boundary coverage.

## Video ownership follow-up

UDP sessions now retain the socket and worker through failed native close,
including a canceled late socket creation. A new receive session cannot replace
that retained owner. Decoder retirement similarly retains the process, all
pipes, and any live I/O workers until exit and close are confirmed. A bounded
compatibility stop no longer discards a timed-out decoder owner; the lifecycle
worker waits for its existing workers and late startup, then reports any
unconfirmed process/pipe closure. UDP lifecycle confirmation includes its nested
HEVC decoder. No process is respawned while a retired session remains.

Selected video regression run: 6 suites PASS, 87 unittest cases plus 198
protocol assertions, 0 SKIP, 0 INCOMPLETE, 0 FAIL. The new video ownership suite
has 8 deterministic cases, including failing socket/pipe closes, unreaped
processes, a pipe worker exceeding the bounded stop, and canceled late opens.

Stable full offline aggregate at `365b38f`: **119 PASS, 12 SKIP,
4 INCOMPLETE, 0 FAIL**. The verifier remains NO-GO because unavailable
firmware/hardware evidence is not a passing qualification. This result includes
the synchronous UI call sites before their separate request/poll migration.

## Explicit final retry and exit policy

Final polling now returns `ShutdownCompletion`, retaining the existing
`generation/target/final/failures/succeeded` interface and adding:

- `resources_released`: every backend/UI teardown task confirmed release.
- `audit.drained`: the audit worker ended with no queued/in-flight records.
- `audit.intact`: no dropped records or write errors were recorded.
- `audit.pending_records`, `audit.dropped_records`, `audit.write_errors`.
- `attempt`: explicit final close attempt number.
- `may_exit`: resources are released and audit is drained. Historical audit
  loss remains an error, so `may_exit` can be true while `succeeded` is false.

The UI may accept close only when `may_exit` is true and must still report any
failed audit integrity. `initial_shutdown_completion` preserves the original
immutable result; `shutdown_completion` exposes the latest result.
`retry_shutdown()` explicitly retries only ended failed tasks, returning the
attempt number. A pending task cannot acquire another worker, successful closes
are not replayed, and final authority remains sealed. Async writer/MQTT retry
admission runs on the owner thread; blocking confirmation remains off-thread.
Retry diagnostics bypass the sealed audit instead of creating rejected records.

Validation on the combined `84e63a0` integration base: 121 unittest cases PASS
across coordinator, actual backend retry, DT7/CC ownership, MQTT lifecycle,
audit lifecycle, and transport safety suites. These include real DT7/CC/MQTT
class composition with fake native handles, delayed audit draining, irreversible
audit loss, preserved original results, and bounded preflight admission.
