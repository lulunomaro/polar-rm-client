# Detached device-egress configuration

## Boundary

`backend/device_egress_settings.py` captures the ten named RobotLink settings as
scalar values. No mutable `Setting`, module, registry, control bridge, safety
object, or writer is retained in the captured request. The resolved DT7/CC
endpoints and the common egress permission value are frozen, slotted dataclasses.
The module imports no Qt, UI, device, or runtime-state layer.

The distinction between raw scalar settings and resolved endpoints is deliberate.
Conversion occurs at the old validation point: disabled or port-conflicted CC
does not parse a malformed frequency, and DT7 profile loading still occurs only
after CC processing and when the DT7 route is selected. Existing falsy defaults,
truthy persisted strings, untrimmed endpoint names, and the conflict gate's
historical `None` versus `Auto` distinction remain unchanged. Validation is not
silently broadened as a side effect of the extraction.

The manager adapters consume named configuration values rather than a dictionary
of live setting objects. They still own the safety-critical sequence: process the
independent CC falling edge, retire old writers only after neutral and close
confirmation, bind the live DT7 safety/evidence guard, then publish masks and
permission. The immutable permission value describes only the common gates;
it does not replace DT7's live qualification check. The worker close and explicit
retry protocols are unchanged.

## Corrected baseline and evidence

The intentional CC port-conflict neutral correction is described separately in
`device_egress_safety_20261016.md`. It was committed and tested before recording
this extraction's baseline, so it is not counted as equivalence with the faulty
old branch.

`tests/fixtures/device_egress_baseline.json` freezes 102 cases after that correction
and before this extraction. SHA-256 values pin the three original manager methods
and two control release adapters. Normal tests only read the fixture. Recording
is explicit and refuses changed sources. Shared initial values plus explicit
case deltas preserve complete state without repeating every unchanged field.

The oracle compares ordered writer creation/release/close, safety calls, control
publication, manager ownership, errors, and partial state after aborts. It covers
permission combinations, absent settings/modules/control, malformed values,
profile and qualification gates, both falling edges, neutral/close failure,
retained targets, angle-port conflicts, and repeated configuration attempts.
All 102 cases remain unchanged after extraction.

Separate tests check immutable values, scalar detachment, each permission
combination, lazy endpoint validation, exact fallback semantics, one read per
setting, and import independence. The real async CC/fake-byte-sink regression
still verifies that a conflict requests neutral on the old owner without relying
on its later watchdog or replenishing its retry budget.

## Reproduction

```sh
export QT_QPA_PLATFORM=offscreen
export POLAR_USER_DATA_ROOT=/tmp/client-device-egress-tests
export PYTHONPATH="$PWD"
python -m unittest discover -s tests -p 'test_device_egress*.py' -v
python tests/test_controller_angle_integration.py -v
python tests/test_transport_safety_regressions.py -v
python tests/test_device_manager_nonblocking.py -v
python tests/dt7_alignment_test.py
python tests/test_dt7_worker_dispatch.py -v
python tests/test_cc_worker_dispatch.py -v
python tools/quality_gate.py
```

Observed locally: 6 characterization methods / 102 frozen cases; 9 settings
boundary methods; 3 actual-worker safety methods; controller-angle integration
14; transport safety 36; manager nonblocking 7; DT7 alignment 112 checks;
DT7 worker dispatch 15; CC worker dispatch 8; static quality gates pass.
These are offline/fake-I/O results, not native hardware or real-match readiness.
The selected integration tests were updated only at their private configuration
method boundary to pass the new value types; their safety assertions remain.
