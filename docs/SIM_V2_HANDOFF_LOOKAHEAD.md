# Multi-request handoff lookahead

This client-only scheduler revision preserves the server protocol and all
original input, activation and output deadlines. It adds no physics or algorithm
behavior. CPU qualification alone does not establish arrival or real-time LAN
performance.

An ordinary batch consists of sequential requests. Starting it just before a
prefetch recommendation can finish too late to perform the next handoff. Before
that batch, the scheduler now includes its full conservative request cost and
worker wake time. If it would cross the recommendation, it starts prefetch first.

Three states are distinct:

1. Prefetch recommended and forecast fits: fetch before the ordinary batch.
2. Forecast insufficient, but still before the actual deadline: make at most one
   pending handoff attempt. A complete sample after its ACK is mandatory. No new
   old-window batch or replacement window is sent while fetching/waiting or
   populating. Each kind is attempted and acknowledged separately during initial
   population; a partial ACK does not authorize a retry of the first kind.
3. Actual expiry: disarm/release. A new window never revives an expired binding.
   An already admitted successful pulse may update its own kind when its matching
   ACK arrives, while every other kind is still checked independently.

All prior budgets remain: 150 ms maximum server window, 10 ms initial request
costs, 20 ms complete sample budget, 5 ms wake, 5 ms margin, and at least 50 ms
between window requests. A total forecast cost at least the lifetime still stops.
If the minimum allowed issuance itself reaches the actual deadline, it stops.
There is no automatic rebind, new epoch or shortened safety margin.

Renew/keepalive costs are handled before the forecast decision and rechecked
after their ACK. Release remains first. Request timeouts and OS scheduling still
allow fail-closed interruptions; the estimates are not hard real-time promises.

## Tests and explicit expectation changes

- The earlier planner test that required a forecast-only stop now requires one
  bounded fetch with `forecast_insufficient_one_attempt`. The hard-expiry and
  cost-at-lifetime stop tests remain.
- The pure 20 ms cadence assertion uses a one-request batch away from the
  lookahead trigger. A separate test checks that a three-request batch near the
  trigger waits for the minimum issuance interval instead of sending old input.
- Fetching and populating both return wait on a scheduler query. Hard expiry is
  checked first. Initial per-kind attempts cannot be repeated in one population.
- Existing admission/ACK boundary, late generation, dual-kind, manual-window,
  diagnostics and authority regression tests are retained.

From the client checkout root, with the matching server Python root available:

```
PYTHONPATH=.:tests:/path/to/navigation/simulation/mujoco_match \
python -m unittest test_platform_handoff test_platform_handoff_lookahead
```

The portable `tools/qa/verify_early_handoff.py` runs both modules, rejects optimized
Python and requires a new output directory. `replay_handoff_durations.py` accepts
an explicit duration file and compares modeled scheduling. Repeating endpoint
RTT samples under synthetic 20 ms input/5 ms wake schedules does not reconstruct
unrecorded original Qt wakes or network one-way delays. Assigning all RTT before
or after admission gives two modeled extremes, not measured transit splits.

No old failed navigation run is relabeled by these tests. The test-only goal
runner's separate stop guard is not part of the production scheduler.
