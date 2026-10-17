# Detached controller-angle port-conflict veto

Characterization checkpoint: `90ddf49`, after writer-owner checkpoint `990ba7a`.
The unchanged historical method is retained with a SHA-256 guard in
`tests/fixtures/angle_port_admission_baseline.txt`. Its initial three differential
methods passed before replacement; no historical fixture was regenerated.

## Narrow responsibility boundary

`_sync_controller_angles` previously combined mutable settings/native-owner
observation, an ordered conflict decision and configure/disarm/status effects.
The new `port_admission` module owns only the ordered veto policy over immutable
`PortReservation` values. It imports neither Qt, transport/protocol code nor the
model. Auto conservatively conflicts; a matching explicit canonical key
conflicts; absent/Stub/None keys do not. The first conflict owns the diagnostic.
This is a small explicit policy seam, not a broad manager decomposition.

The manager retains every registry and native-handle read, including eager
capture of both old-writer references and the exact `port_handoff_ready is True`
requirement. It normalizes requested ports at the original point, including
when reception is disabled. Owner aliases remain lazy and short-circuit after
the first conflict. Disabled reception does not normalize owner aliases.
Native owners are reobserved each tick; nothing caches handoff readiness.
Configure, conflict disarm and status publication remain in their original order.
The pure veto never arms forwarding, releases a handle or changes native lifetime.

The existing AST-isolated integration harness now supplies the two new names
in its evaluation namespace; no behavioral assertion was removed or weakened.

## Evidence

Five immutable-baseline differential methods cover the human/enable gates,
forward/mock TX participation, absent sources, ordered configured/prior/referee
claims, literal-True handoff, resolved/requested fallback, real POSIX symlink
aliases, and alias-observation exceptions/short-circuit ordering.
Five standalone policy methods cover explicit/disjoint keys, unresolved Auto,
first-conflict lazy consumption, propagated observation errors and a fresh-process
import that rejects loaded transport/protocol/model/GUI dependencies.

Focused run: **109 methods PASS**, no skips, using:

```sh
QT_QPA_PLATFORM=offscreen python -m unittest \
  tests.test_port_admission_policy tests.test_angle_port_admission_characterization \
  tests.test_controller_angle_integration tests.test_angle_writer_handoff \
  tests.test_device_egress_characterization tests.test_controller_angle_safety_review \
  tests.test_writer_constructor_admission tests.test_writer_concurrent_admission \
  tests.test_writer_retention_cancellation tests.test_writer_close_ownership \
  tests.test_dt7_worker_dispatch tests.test_cc_worker_dispatch
```

Also passed the unchanged API freeze, actual static quality gate and diff check.
Focused output and exact production source digests/whole-tree metrics are in
`docs/evidence/angle-port-admission-20261016/`. The replacement module is included
in those metrics; this seam adds a module and is not presented as a line-count
reduction. Full integration acceptance and native serial/firmware qualification
remain separately required. No physical device, user computer or service-quota
operation was used.
