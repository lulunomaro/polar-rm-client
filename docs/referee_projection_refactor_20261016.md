# RM2026 presence and projectile projection policy

## Scope

The existing codec remains the only wire decoder. Its strict payload and enum
validation is unchanged. `projection_policy.py` separates two deterministic
projection decisions from the state/effect adapter:

- `select_present_fields` applies the RM2026 optional-field contract. Missing
  fields remain distinct from received zeros; direct compatibility mappings
  without metadata retain their previous identity and behavior.
- `assess_projectile_damage` consumes frozen, slotted baseline/sample values and
  returns a frozen next baseline plus the proven per-calibre deltas. It has no
  runtime state, clock, host, safety or device dependency.

The adapter still performs conversions at their prior positions and publishes
state in the same order. In particular, injury dictionaries are updated before
counter conversion, a hit revision is assigned before its timestamp, and the
next baseline is assigned after hit details. An exception therefore retains the
same partial state. Absent sample values are filled from previous totals before
conversion, preserving the original conversion/failure boundary.

The straightforward message branches remain explicit. This stage does not
claim to eliminate the whole dispatcher or move it into a handler dictionary.
Stage logging, video transitions, receipt timestamps, custom-byte forwarding and
other state projection remain in the adapter for a separate, reviewed effect
boundary. Application-time timestamps are preserved; no freshness policy is
silently changed.

## Frozen evidence

`tests/test_referee_state_characterization.py` records 210 named cases after the
independent-counter safety correction. The approximately 194 KiB fixture shares
one initial state and stores case deltas, exact wire bytes, ordered state and
nested-dictionary writes, host events, and final state after exceptions. It covers
all 25 downlinks, optional/zero fields, unknown names, malformed direct mappings,
strictly rejected wire packets, 17 aborted cases, and seven injected host/clock
failure sites. Every injected site has a test proving it was reached.

The frozen source digests are:

- Adapter class: `5250235cae147faca4b6f5464620319cb902c510adc186b63475841b01fed794`
- Codec module: `7edd296d3768c90017ee69b946661f51690d10ecefdb7657181ccbcd542bf5de`
- Contract module: `514c517137c7c17e2658d4768d7244d3c55d9ac73f1f88dd7e06cc8545fbc15c`

Normal tests never regenerate the fixture. Explicit `--record-baseline` refuses
changed source bytes. The fixture itself was not modified by this extraction.
This is a behavioral oracle for the implementation, not independent proof that
every field interpretation matches official rules. Existing contract tests cover
the separately pinned protocol transcription.

## Checks

All passed with the isolated quality interpreter and offscreen Qt:

```
python tests/test_referee_state_characterization.py -v   # 7 tests / 210 cases
python tests/test_referee_projection_policy.py -v        # 10 tests
python tests/test_projectile_counter_baselines.py -v     # 10 codec-to-consumer tests
python tests/test_transport_safety_regressions.py -v     # 36 tests
python tools/quality_gate.py                            # static gates
```

Five existing focused functions also passed: the cumulative-hit smoke,
backend/frontend synchronization (26 assertions), and official contract,
partial-state epochs and student-preview integration (210 assertions). The map
category regression now invokes the real adapter rather than recompiling its
former monolithic method; its original category assertions are unchanged.

An additional local, source-digest-checked differential replay used seed
20261016: 10,000 randomized cases, including 4,318 aborted cases, produced zero
ordered-effect/state differences and zero unobserved harness failures. This
exploratory result is separate from the 210 committed cases; both sides share
the current codec, model and unchanged host test adapters. No native hardware or
official-event qualification is claimed.
