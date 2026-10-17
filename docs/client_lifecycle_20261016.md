# Client lifecycle follow-up, 2026-10-16

This continues [the first quality pass](client_quality_20261016.md) on the same
review branch, after remote checkpoint `1e28bb91fe13ff2249b8944c38ae4d888e67dac8`.
It does not merge main or claim real-device qualification.

## Bounded audit logging

`RotatingJsonlLogger.emit` now snapshots supported values into bounded immutable
JSON bytes and enqueues them. Filesystem creation, rotation and writes happen
on one lazy writer, never while holding the producer's state lock. Queue record
and byte limits include in-flight data. Unsupported, too-large, full, failed
and cancelled records have explicit persistent counters; success cannot erase
previous loss. Arbitrary user object repr/iteration hooks are not executed.

The explicit `flush(timeout)` and `close(timeout, drain=...)` operations report
incomplete drain or record loss. They acknowledge OS writes, not fsync durability.
Scenario restarts keep the logger open and never wait for disk; final application
shutdown has an explicit bounded close. Backend diagnostics expose counters.

## MQTT interactive restart ownership

Connection polling and address/profile changes must not synchronously join
Paho's network thread on the UI thread. A single cleanup owner retires the old
client, replacement intent is coalesced, and only the latest valid session can
start after old ownership ends. Stopped sessions remain unavailable to publish,
and stale callbacks cannot alter the replacement session. Final stop retains
ordered cleanup semantics; this is distinct from the interactive restart path.

An additional reproduced same-session race paused a nonzero command after
admission, activated E-stop, then resumed the enqueue. A monotonic control
authority generation now invalidates admitted commands after lock, qualification,
arm or E-stop transitions, and is rechecked immediately before enqueue. The
dedicated fixed-zero emergency path remains available. This closes the tested
pre-enqueue window; it cannot recall already enqueued traffic and is not a
formal cross-thread atomicity guarantee for all safety state.

## Evidence and boundaries

The final result below is generated from a stable source snapshot. Focused tests
use blocked writers/cleanup and explicit events to exercise races; they do not
establish actual filesystem worst-case latency, Windows UI frame rate or real
robot behavior. Real localhost MQTT and codec regressions are still separate
from physical hardware acceptance.

Broader scenario-switch orchestration still calls ordered shutdown, and final
shutdown can wait for transport teardown. The window retains shared-state
mixins. These remaining architectural boundaries are documented rather than
silently described as fixed by this narrower follow-up.

## Final validation

Exact source: `2eb2c7c664673432bb18234f471442cebf83e0d2` (tree `c97e3b206ab2c0b273b2eb95347fcb54bd151dbb`).

- 115 checks PASS, 12 SKIP, 4 INCOMPLETE, 0 FAIL.
- The complete selected scope remains NO-GO for missing external team firmware,
  CRC/reference and official-client dump inputs; no missing input was mocked
  into a passing hardware claim.
- New focused coverage: 18 logger cases and 19 MQTT lifecycle/authority cases;
  real localhost Paho and H.264/H.265 regressions are included separately.
- Native Windows graphics, physical audio, real-robot actuation and complete
  Windows release packaging remain unverified.
- An earlier pass failed only the old endpoint test that intercepted stop/start
  calls instead of exercising lifecycle behavior. It now blocks fake teardown,
  verifies prompt UI return, coalescing and the real replacement endpoint.

[Final structured results](evidence/client-lifecycle-20261016/final-results.json)
[Final console](evidence/client-lifecycle-20261016/final-console.txt)
[Pre-guard deterministic reproduction](evidence/client-lifecycle-20261016/pre-guard-reproduction.txt)
[Earlier diagnostic findings](evidence/client-lifecycle-20261016/first-pass-findings.json)

Counts are suite/check entries, not a homogeneous count of unit assertions.
The source snapshot is reproducible independently from these evidence files.
