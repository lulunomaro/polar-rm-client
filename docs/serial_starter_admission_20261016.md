# Serial starter admission ownership

This intentional lifecycle correction was reproduced on integrated source `f784da9f9ac6903fece1da391d51ab4c8b31f925`.

The asynchronous scenario activation published its `backend-start-serial` thread before calling `Thread.start()`. When admission raised before the thread started, later final teardown captured that unstarted object. The strict serial close adapter could not join it, so it never reached `serial.stop()`. Every explicit retry repeated the same join failure and reported `may_exit=False`.

The admission site now discards the starter only when `start()` raises and `Thread.ident` is still `None`. It re-raises the original failure, leaving scenario authority revoked and the lifecycle failure visible. A thread that really started remains owned, including a wrapper that raises after startup or after the starter already exited. `ident` survives thread exit; `is_alive()` alone would lose the finished-but-admitted distinction.

The close adapter and its strict joins are unchanged. A retained starter still must finish before the serial owner can close, including a native handle published after shutdown was requested. Existing close failures retain that handle for explicit retry.

## Evidence

`tests/test_serial_starter_admission.py` exercises the actual manager, lifecycle controller, serial backend, and real threads with an event-controlled fake serial driver. MQTT and robot connection starts are stubbed; no network or hardware is used.

- Before the correction, two of the initial four methods failed with the exact unstarted-thread join error. Both genuinely admitted-owner cases already passed.
- After the correction, five methods pass: never-admitted final close, never-admitted explicit close retry, running starter with a late-opened handle, already-finished starter with failed driver close/retry, and direct proof that the strict close adapter still rejects an unjoinable owner.
- All five methods passed 50 repetitions (250 method executions).
- Seventy neighboring methods passed across backend asynchronous lifecycle, lifecycle controller, callback/input failures, serial lifecycle, and resource-close ownership tests. Together with the new tests: 75 methods.
- Static quality gates passed: 182 modules, 3,110 functions, no initialization cycles. This is focused local verification, not an aggregate or hardware qualification.

Reproduce with isolated user data and the repository plus `tests` on `PYTHONPATH`:

```sh
QT_QPA_PLATFORM=offscreen python tests/test_serial_starter_admission.py -v
python tests/test_backend_async_lifecycle.py -q
python tests/test_backend_lifecycle_controller.py -q
python tests/test_backend_lifecycle_callback_failures.py -q
python tests/test_backend_lifecycle_input_failure.py -q
python tests/test_serial_lifecycle_regressions.py -q
python tests/test_resource_close_ownership.py -q
python tools/quality_gate.py --json /tmp/serial-admission-quality.json
```

## Adjacent native reader correction

The same fault was independently reproduced for `RmSerialBackend.start`: an exception before the `rm-serial` reader started left an unjoinable reader reference. Even when the already-opened driver closed successfully, the first final teardown failed its strict join. An unsuccessful driver close instead reported the join error and obscured the actual retained-handle failure.

The reader's existing exception handler now clears only a reader whose `ident` is `None`. Its driver close/retention behavior is unchanged. A real reader, including one already finished when its `start()` wrapper raises, remains available to the strict close adapter. A failed native close still prevents replacement and requires explicit retry.

`python tests/test_serial_reader_admission.py -v` adds five actual-manager/serial-backend tests with fake I/O. Before the correction, the two never-admitted cases failed with the exact join error; after it, all five pass. They cover successful final close, failed close and retry, a still-running reader, and an already-finished reader with both successful and failed close. The five methods passed 50 repeated runs (250 executions). Both admission suites plus the seventy neighboring methods passed on the combined source (80 methods). Static quality gates again passed. The strict join adapter remains unchanged.
