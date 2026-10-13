# Bounded input-window handoff (CPU qualification)

The current lookahead revision is described in SIM_V2_HANDOFF_LOOKAHEAD.md. It
separates the recommended prefetch time, forecast feasibility and hard expiry.

The v2 strict operator path prefetches its next server window before the previous
adapter activation reaches its deadline. The server's 150 ms lifetime, identity
checks and physical-consumption gates are unchanged. No server or actuator source
is part of this increment. Legacy v2's original control-tick implementation is
retained separately.

## Time and authority

- The local deadline is request-start monotonic plus the verified server TTL.
  Reply receipt never restarts the TTL. Server and client clocks need not match.
- A successful adapter acknowledgement updates only that kind's current
  activation bound, using the relative expiry within the **same window ID**.
  Floating-point subtraction is bounded by the verified server expiry; the local
  result is always clamped to the original TTL. Old asynchronous outputs retain
  their own original authorization and action deadlines on the unchanged server.
- An initial inactive binding has no activation deadline yet. Its first input
  still needs a complete sample after the new window ACK and before expiry.
- Every worker task, input send and ACK rechecks focus, generation, freshness and
  deadlines. A pulse admitted before its old deadline may be acknowledged after
  that old deadline: only a valid matching ACK can update that kind before the
  remaining kinds are checked. An expired other kind cancels the whole takeover.
- Late ACKs after release cannot restore ownership. Expiry or insufficient budget
  disarms; another explicit claim/F2 is needed. There is no automatic rebind.

## Bounded scheduling

One existing control worker performs the requests. Release is processed first.
Renew/keepalive due within a predicted handoff are performed before issuing the
window, with a deadline recheck after each response. Their measured latency is
part of the estimate. State and camera have their existing independent polling
threads; contention is possible and no server priority is promised.

Initial engineering estimates are 10 ms per request, 20 ms for the next complete
Qt sample, 5 ms worker wake and 5 ms safety margin. Per-category RTT estimates only
increase during a session. Prefetch adds 10 ms of polling/slack to the greater of
75 ms and the full estimated handoff cost. These are conservative configuration
values, not measured network or OS guarantees.

Windows are issued at least 50 ms apart in one control generation. Each new
window gets at most one immediate first batch after a new complete input sample.
Ordinary batches are at least 20 ms apart and do not repeat the same revision.
A window must be populated once before another can be issued. Estimated cost at least the TTL, or a minimum-issue instant that reaches the
actual earliest deadline, results in `budget_pressure` and disarm. Insufficient
forecast slack below the TTL permits one strictly deadline-bound handoff attempt,
without old-window new inputs or a replacement/retry loop. Explicit rearm does not reset observed slow RTT; a new session does.

The Qt thread only updates the latest input slot and wakes the worker. It sends
no HTTP. A request already in flight can remain effective until its original
server deadline; local focus loss is not instant remote network cancellation.

## CPU checks

From the Polar client checkout root, with existing test dependencies and the
matching navigation Python root available:

```
PYTHONPATH=.:tests:/path/to/navigation/simulation/mujoco_match \
python -m unittest test_platform_handoff test_platform_control_windows \
  test_platform_adapters.CatalogTests test_platform_adapters.WorkerTests
```

`test_platform_handoff` uses the real server ControlPlane in process behind a
fake HTTP router, with a server clock offset and independently scheduled
admission/ACK delays. It opens no socket, Qt application, renderer or physics.
Synthetic display readiness in the tests is not camera qualification.

The older `verify_window_handoff.py` is a historical diagnostics-stage reproducer
for its original frozen client. It intentionally expects the old delayed-handoff
failure and must not be used as an acceptance test of the revised scheduler.
No previous live failure is retroactively assigned a root cause by these new
deterministic cases. No navigation-to-goal or new image qualification is claimed.
