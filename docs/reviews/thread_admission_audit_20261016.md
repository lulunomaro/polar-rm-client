# Independent thread-admission audit, 2026-10-16

## Scope and evidence

This review covers the 10 explicit `Thread.start()` call sites in manager,
serial backend, MQTT backend, video backend, UDP transport, and lifecycle
coordinator, plus Paho's delegated `Client.loop_start()` admission: 11 admission-site classes
in total. The decoder's session-worker call site creates three distinct pipe workers.

The review uses an independent worktree based on root commit `911549d`, followed
by the committed video/profile/recovery patches listed below. No production fixes
were authored by this reviewer; only committed patches were integrated into the
isolated review tree. Tests use real managers, backend objects and installed Paho
where relevant, with fake driver, process, and socket boundaries.
No network peer or hardware is contacted. No aggregate acceptance run is claimed.

Two different fault models are kept separate:

1. Standard admission rejection: `Thread.start()` raises before the worker starts.
2. Defensive late wrapper failure: a wrapper calls the real `Thread.start()` and
   then raises while the worker is running or after it has finished. This is an
   injected wrapper fault, not a claim about normal CPython failure frequency.
   A previously admitted worker must remain owned regardless of that exception.

The finite audit is limited to these sites. No timeout was increased and no join
was made permissive to conceal a leaked or still-running owner.

Environment: Linux x86_64, Python 3.12.14, Paho MQTT 2.1.0, PySide6 6.8.2,
NumPy 2.5.4, OpenCV 5.0.0.93, pyserial 3.5. Test invocation prefix:

```sh
CI=true PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen \
POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1 \
/tmp/client-quality-venv/bin/python -m unittest
```

## Result

**All confirmed defects in the bounded 11-site inventory are corrected and
independently rechecked.** The final isolated integration is
`ecd58d3ab75e451f677fb8db48c8b407d1c380eb`: **144 test methods across 21 suites passed,
exit code 0, with no skips**. The focused run is not the repository-wide aggregate
acceptance run. Root integration and unrelated ongoing changes require their own
fixed-tree verification.

The production patch sequence reviewed here is:
`d3f24fc` → `ddf3605` → `1c31097` → `39763e6` → `a5c6c54` → `2d93fd3` →
`ea7e476` → `7915baf` → `9e4bc1b`, plus the separately authored serial fixes
`aed5449` → `5c83a14`. Lower video stop seams `c84d36b` and `f043ae2` were already
present in the review base. Reviewer-only regressions are `0550274` and `bb995b9`.

## Admission inventory

| Site | Standard rejected admission | Admitted worker followed by wrapper error | Evidence / current status |
|---|---|---|---|
| Manager serial starter (`backend-start-serial`) | Previously poisoned captured final closer; fixed in `aed5449` | Live/finished starters remain joined before native release | `test_serial_starter_admission`: 5 methods independently passed |
| Manager Native recovery (`native-decoder-restart`) | Previously left an unjoinable permanent pointer; fixed in `a5c6c54` | Live/finished workers remain owned; admission errors retain bounded backoff | `test_video_recovery_admission`, both codec branches |
| Serial backend reader (`rm-serial`) | Previously produced a first-final-close false blocker; fixed in `5c83a14` | Live/finished readers and failed native handles remain owned | `test_serial_reader_admission`: 5 methods independently passed |
| MQTT cleanup (`mqtt-close`) | Retains client/socket owners for explicit stop retry | Previously discarded live closer; fixed in `ea7e476` | Existing MQTT lifecycle test plus `test_cleanup_worker_admission` |
| Paho network loop (`Client.loop_start`) | Previously retained an unjoinable thread and leaked wakeup sockets through final retry; fixed in `2d93fd3` | Live/finished network workers remain captured | `test_paho_loop_admission`: 4 methods independently passed against installed Paho |
| HEVC recovery (`h265-decoder-restart`) | Same rejected-reference defect as Native; fixed in `a5c6c54` | Live/finished workers remain owned | `test_video_recovery_admission`, both codec branches |
| Decoder session pipes (`mjpeg-in`, `mjpeg-dec`, `mjpeg-err`) | All three partial-start positions preserve failed native close and can retry | All three admitted-then-raised positions preserve native owner | Independent `bb995b9`, 6 subcases; real Thread/session objects, fake process/pipes, substituted pipe-worker targets |
| Decoder closer (`mjpeg-stop-*`) | Retains retired/in-flight native owners | Previously discarded live closer; fixed in `ea7e476` | `test_video_stop_nonblocking` and `test_cleanup_worker_admission` |
| UDP receiver (`udp-video`) | No socket opened; strict close succeeds | Previously produced false final success and lost a late socket; fixed in `9e4bc1b` | `test_udp_receiver_admission`: 3 methods independently passed, including actual manager final close / retry |
| UDP closer (`udp-video-stop`) | Retains exact socket/receive session | Previously discarded live closer; fixed in `ea7e476` | `test_udp_stop_nonblocking` and `test_cleanup_worker_admission` |
| Lifecycle resource/audit task (`lifecycle-*`) | Existing coordinator test records failure without hanging | Previously allowed concurrent retry; fixed in `7915baf`, one real result plus bounded warning | `test_coordinator_late_admission`: 4 methods independently passed, including real manager final close |

## Confirmed counterexamples before correction

### Rejected recovery / serial starter / Paho network thread

- Native and HEVC recovery: fixed-tree precursor `cd00371` left an initial thread
  object with `ident=None`. Final close and every retry failed with
  `cannot join thread before it is started`, although decoder handles had closed.
- Manager serial starter: `ad20bd0` retained the same unstarted starter in the
  captured final-close task. Both final attempt and retry had `may_exit=False`.
- Serial reader: `4632fb7` closed its fake native handle after reader rejection,
  but `close_serial()` captured the unstarted reader before `serial.stop()` cleared
  it. First final attempt failed spuriously; a second explicit retry succeeded.
- Installed Paho: `ad20bd0`, fake wakeup pair, rejected only the real vendor network
  thread. `loop_stop()` repeatedly tried to join its unstarted `_thread`. Both fake
  wakeup descriptors remained open through first final and explicit retry.

### Late wrapper exception after real admission

- UDP receiver on `aed4068`: real receive worker blocked inside fake socket
  creation; `start()` then raised and cleared both `_session` and `_thread`.
  Actual manager final completion was `may_exit=True`, `succeeded=True` while that
  worker remained alive. When released, socket close failed and no backend session
  reference retained the socket. This was real false release evidence.
- Coordinator on `aed4068`: real `lifecycle-native-decoder` worker blocked in fake
  process wait; admission wrapper then raised. Final completion ended with failure
  while that original worker was alive. `retry_shutdown()` admitted a second real
  `close_decoder` call before the first closer ended. Resource-specific locks
  prevented the particular fake process from being concurrently destroyed, but
  coordinator single-owner / only-ended-retry invariants were violated.

## Profile/source and composite final-close verification

`39763e6` was independently checked on integrated `ad20bd0`: 14 source/profile
methods passed. The original half-applied profile case preserves separate
requested, manager, MQTT, and frontend identities; ordinary ticks do not retry;
an explicit repeated profile request repairs incomplete application even when
some labels already match. Qualification and arming remain blocked during failure.
Successful target acceptance is not native teardown-completion evidence.

Independent regression `0550274` installs actual manager, UDP backend, Native and
HEVC decoders, and a real Paho client with fake native owners. MQTT, UDP socket,
Native pipe, and HEVC pipe initially all reject close. First final completion
retains every owner and blocks exit. Repairing only MQTT and Native releases only
those owners; the next attempt still blocks on UDP/HEVC. Repairing the remaining
owners permits final exit. Successful close tasks are not replayed, and the first
immutable completion retains its original failure evidence. The test explicitly
forbids socket and subprocess creation. It passed independently.

## Checks already run

These are focused test runs, not full aggregate acceptance:

- `39763e6` integrated as `ad20bd0`: source/profile suite, **14 methods PASS**.
- `a5c6c54` integrated as `4632fb7`: recovery, final retry, source/profile,
  **23 methods PASS**.
- `2d93fd3` integrated as `aed4068`: Paho loop admission, MQTT native ownership,
  final retry, independent composite retry, **18 methods PASS**.
- `aed5449` integrated as `510cfc2`: serial starter, composite retry, video recovery,
  Paho admission, **14 methods PASS**.
- `ea7e476` integrated as `c3a542c`, plus independent matrix `bb995b9`:
  cleanup guards and UDP/decoder startup matrix, **5 methods PASS**.
- `5c83a14` integrated as `0a24096`: serial reader/starter, resource ownership,
  shutdown retry, composite retry, **28 methods PASS**.
- `7915baf` integrated as `d83993a`: coordinator/controller/shutdown/input/composite,
  **36 methods PASS**; new late-admission regression and two offscreen window
  lifecycle suites, **42 methods PASS**. Offscreen warnings about unsupported
  OpenGL and unavailable audio are environment limitations, not GPU validation.
- `9e4bc1b` integrated as `ecd58d3`: UDP receiver/source/stop/startup matrix/composite,
  **23 methods PASS**.
- Final `ecd58d3` composition below: **144 methods across 21 suites PASS**, no skips,
  Python exit code 0.
- `verify_all.py --list` found the two reviewer-added suites as `offline`; it did
  not execute the aggregate runner or bypass its global lock.

### Final combined focused command

Using the environment variables above and the same interpreter:

```sh
/tmp/client-quality-venv/bin/python -m unittest \
  tests.test_backend_video_source_nonblocking \
  tests.test_video_recovery_admission \
  tests.test_cleanup_worker_admission \
  tests.test_paho_loop_admission \
  tests.test_serial_starter_admission \
  tests.test_serial_reader_admission \
  tests.test_transport_start_admission_matrix \
  tests.test_backend_composite_shutdown_retry \
  tests.test_coordinator_late_admission \
  tests.test_udp_receiver_admission \
  tests.test_lifecycle_coordinator \
  tests.test_backend_lifecycle_controller \
  tests.test_backend_shutdown_retry \
  tests.test_video_stop_nonblocking \
  tests.test_udp_stop_nonblocking \
  tests.test_video_close_ownership \
  tests.test_mqtt_cleanup_ownership \
  tests.test_mqtt_lifecycle_nonblocking \
  tests.test_decoder_lifecycle_quality \
  tests.test_backend_lifecycle_input_failure \
  tests.test_backend_lifecycle_callback_failures
```

The retained [focused log](../evidence/thread-admission-20261016/focused.txt)
contains intentional fault-injection diagnostics followed by the unittest
summary. Expected injected audit/input failures do not mean those test methods
failed. Evidence metadata includes the fixed source SHA and actual exit code.

## Outside this audit

Other native-worker admission sites are listed to make the boundary explicit,
not labeled correct or defective without a reproduction:

- `startup_wallpaper.py`, `tongji_yolo.py`, `vision.py`, `tongji_bridge.py`
- `serial_ports.py`
- `backend/polar_transport.py`, `backend/controller_angles.py`
- `backend/field_log.py`, `backend/dt7_worker.py`, `backend/cc_link.py`

These require their own scoped evidence if future work changes their lifecycle.
No conclusions here imply real-device, physical-network, OS-driver, or desktop/GPU
qualification.
