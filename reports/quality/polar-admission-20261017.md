# Polar worker admission ownership repair

Follow-on to the acquisition boundary: independent inspection found constructor
and late `Thread.start()` failures outside the previously protected ownership
paths. These were reproduced before changing production code.

## Red evidence

`python tests/test_polar_thread_admission.py` against ee684de: six tests ran,
three errors and three assertion failures. Connector/stopper/writer construction
exceptions escaped their handlers. Start wrappers that admitted a worker and
then raised dropped the connector reservation, stopper reservation, or writer
reference respectively.

## Repair

- Include construction within each existing admission exception handler.
- Connector failure increments the generation to reject any late open result.
  Never-admitted workers release the reservation; admitted workers retain it
  until their existing close/finally path completes.
- Stopper admission follows the same distinction. The existing worker finally
  path, under the request lock, exclusively clears an admitted stop reservation.
- Writer failure sets the stop event, clears running state, and retains any
  admitted thread until observed exit. Existing failed-stream cleanup remains
  active; a live owner blocks replacement admission.
- No codec, authorized key mask, gimbal ownership, neutral-frame construction,
  or stop-burst ordering was changed.

The targeted suite now passes seven tests: constructor rejection for all three
roles, never-admitted start rejection for all roles, and deterministic late-start
ownership observations for each role. Tests use fake streams, explicit events,
and the existing lifecycle lock to avoid scheduling-dependent observations.

## Verification

Python 3.12.14 at `/workspace/shared/client-clean-venv/bin/python`, checkout root;
`QT_QPA_PLATFORM=offscreen CI=true PYTHONPATH=.` for discovery.

- `tests/test_polar_thread_admission.py`: 7 pass.
- `tests/test_polar_connection_acquisition.py`: 9 pass.
- `tests/test_polar_async_connect.py`: 5 pass.
- unittest discovery `test_transport_safety_regressions.py`: 36 pass.
- `tools/quality_gate.py`: unchanged baseline passes (216 modules, 3405
  functions, zero initialization cycles).
- `git diff --check`: pass.

The integration owner must run full aggregate verification. This is fake-stream
thread/failure evidence, not serial/TCP/robot qualification. Shared admission
abstractions across transports, independent manager completion ownership, and
platform-native blocking I/O remain separate review and qualification work.

## Follow-up: admitted writer owns native retirement

Independent review exercised the actual `_loop` with a finite, event-held read.
The first admission repair retained the worker, but its failure handler still
closed the stream concurrently with that read (behavior inherited from baseline).
This observation alone does not prove a native-driver failure. Supported stream
configuration provides no requirement to close from a different thread to unblock
normal RX: pyserial uses `timeout=0`, and TCP performs a zero-time select before
its bounded socket read. Installed pyserial POSIX source invalidates descriptors
used by read; its Win32 source invalidates overlapped event handles. Avoiding that
unnecessary overlap is a conservative ownership improvement, not a claim that
all serial/TCP implementations are inherently unsafe under concurrent close.

The admission handler now marks the transport disconnected, signals stop, and
leaves close to the admitted writer's existing `finally`. Never-admitted writer
failures still close immediately. No join or additional wait is added to the
caller. If an injected/custom stream fails to return from read, ownership remains
pending rather than being declared complete; this does not qualify broken driver
behavior or change explicit stop's separate timeout/close behavior.

Two additional real-loop tests establish: no close while a read is in flight;
finite normal/error read exit performs close on the writer; failed close retains
the native owner until explicit successful retry; and a writer that finished
before the wrapper raises neither leaks nor closes twice. The overlap matrix
failed three subcases before this follow-up and passes afterward. A third test
covers late connector failure concurrent with stop: no publication, one close,
all workers exited. Admission suite now passes 10 tests; acquisition 9, async 5,
transport safety 36, unchanged static gate and diff whitespace checks pass again.
