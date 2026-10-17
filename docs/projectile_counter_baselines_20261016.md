# Independent first observations for projectile damage

## Intentional safety correction

`RobotInjuryStat` supplies cumulative damage counters. The existing codec tracks
actual protobuf field presence separately from the zero defaults returned by
its decoder. The state adapter already filters those defaults using
`__present__`, but used one initialization bit for both projectile counters.

A legal packet containing only `small_projectile_damage = 20` (`18 14`) followed
by the first `large_projectile_damage = 10` (`20 0a`) therefore published a false
`+10` hit. With the existing feature, match, referee-link, health and buffer gates
satisfied, the actual `ProjectileHitSpinController` returned `enable`. Neither
packet established a measured increment for the large-projectile counter.

The state now records whether each calibre has an observed baseline. A first
value, including explicit zero, establishes that baseline without publishing a
hit. Missing fields preserve the stored total and never establish a zero
baseline. A later increase contributes only the delta from an observed baseline.
A packet may contain a real increase for one calibre and the first value for the
other; only the proven increase is reported.

Any observed backwards jump, including uint32 wrap, starts a new counter epoch.
Both values in that packet are baselines, and any absent counterpart becomes
unknown until its own next observation. This preserves the old suppression of a
mixed backwards/forwards packet and avoids comparing later data against a stale
pre-reset counter. Scenario/transport reset clears both provenance flags while
preserving the monotonically increasing hit revision.

The new provenance fields have `init=False`, preserving the existing RobotState
constructor parameters. No codec, wire field, official rule, spin duration,
threshold, gate or command-emission path was changed.

## Evidence

`tests/test_projectile_counter_baselines.py` uses independently encoded legal
uint32 payloads, the real RM2026 decoder/state adapter, and the actual existing
spin consumer. It covers both arrival orders, unrelated partial packets,
explicit zero, mixed known/first observations, subsequent valid increments,
duplicates, full and partial reset, uint32 wrap, and the actual manager's epoch
reset adapter. No device or UI actuation is performed.

Before the correction, the 10-test suite reported seven failing assertions
(including both partial-reset subcases). After the correction, all 10 pass.
Existing focused checks also pass:

- The cumulative-hit smoke function.
- Backend frontend synchronization: 26 assertions.
- Official field contract, partial-state epochs and student preview: 210 assertions.
- Transport safety regression suite: 36 tests.
- Frozen custom-byte behavior: 5 tests / 96 unchanged fixture cases.
- API freeze: established members and call signatures unchanged.
- Static quality gates.

Run the regression with an isolated `POLAR_USER_DATA_ROOT`, repository-root
`PYTHONPATH`, and `QT_QPA_PLATFORM=offscreen`:

```
python tests/test_projectile_counter_baselines.py -v
```

This is a separately recorded behavior fix. Any subsequent dispatcher
characterization must use the corrected baseline rather than treating the old
false-hit behavior as an equivalence requirement. These checks do not establish
native hardware or event qualification.
