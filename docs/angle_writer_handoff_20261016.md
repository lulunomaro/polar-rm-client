# Confirmed writer-to-angle port handoff

This is an intentional ownership correction, separate from the frozen behavior-preserving extractions.

## Reproduction

`BackendManager._sync_controller_angles` reserved the port of every historical real-output DT7/CC writer. After a writer sent its neutral frame, successfully closed its driver, cleared its handle, and exited its thread, the manager correctly retained the writer object but still treated it as an active port owner. An otherwise eligible angle RX configuration remained disabled indefinitely.

The new regression uses the actual asynchronous writers, their normal frame construction and finalizers, the actual angle relay, and the manager configuration method. Serial I/O is replaced with event-controlled fake handles. Before the correction, the initial six test methods produced ten failed positive-handoff assertions; the unsafe-handoff checks already passed.

## Release proof

Both asynchronous writers now expose a read-only `port_handoff_ready` property. It requires all of:

- Close completion and an exited worker thread.
- No retained driver handle and no remembered close failure.
- Confirmed wire-neutral state and no pending or exhausted neutral release.

The manager removes the historical writer's reservation only for a literal `True` proof. A generic `closed` flag, an unknown writer without this proof, or a truthy string does not suffice. The query performs no join, close retry, or device access. The manager retains the writer object and does not arm angle forwarding. Independently selected egress routes still reserve their requested ports.

Failed neutralization remains a blocker even if physical close succeeds. Driver exceptions, explicit `False`, and a still-open driver preserve ownership until the existing explicit close-retry protocol succeeds. The finalizer's completion flag alone cannot bypass a retained handle or a worker that has not exited.

## Validation

Run with the repository on `PYTHONPATH`, an isolated `POLAR_USER_DATA_ROOT`, and `QT_QPA_PLATFORM=offscreen`:

```sh
python tests/test_angle_writer_handoff.py -v
python tests/test_dt7_angle_ownership.py -v
python tests/test_writer_close_ownership.py -v
python tests/test_controller_angle_integration.py -q
python tests/test_device_manager_nonblocking.py -q
python tests/test_dt7_worker_dispatch.py -q
python tests/test_cc_worker_dispatch.py -q
python tools/quality_gate.py --json /tmp/angle-handoff-quality.json
```

Results: 64 test methods passed, including seven new methods covering both writer implementations. The seven new methods also passed 50 consecutive repetitions (350 method executions). Static quality gates passed for 172 modules and 2,957 functions with no initialization cycles. No frozen fixture changed. These are deterministic fake-I/O and local thread tests, not hardware qualification.
