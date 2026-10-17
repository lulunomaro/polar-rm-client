# DT7 strict tick budget investigation

Integrated baseline f34dbda95752545a6dfc1e4dc43f6f4526fd6e5d failed the unchanged
`test_no_device_real_time_100hz_tick_retry_budget`: max 22.028550001778058 ms,
limit strictly less than 20 ms. Original logs remain in
`/workspace/shared/clean-ten-hour-baseline/logs/tests__test_dt7_worker_dispatch.*.log`.
This is a genuine failed run; the isolated pass does not erase it.

One isolated execution on that exact baseline ran the original unittest method
with wrappers recording wall and current-thread CPU time for poll/send/release.
Command (from the baseline checkout):

```
QT_QPA_PLATFORM=offscreen CI=true PYTHONPATH=. /workspace/shared/client-clean-venv/bin/python /tmp/probe_dt7_budget.py
```

The committed probe.py is the exact script. It preserves the original assertions
and patches only observation wrappers. Result: 250 ticks in 2.500 s; four scans,
three retries; caller median 0.0718 ms, maximum 0.6162 ms. Phase maxima: poll
0.081575 ms, send 0.291675 ms, release 0.566341 ms. Nested poll measurements
are intentionally included inside send/release measurements; phases cannot be
summed. Instrumentation adds timing/list overhead.

Source inspection: native `_link.send`/`_link.release` execute outside the worker
condition lock; waits release that condition. Caller poll delivers safety
callbacks outside the condition. This test installs no blocking safety callback
and patches native target discovery to an immediate no-device result. The
caller can still experience lock contention and scheduling pauses. No recording
of thread CPU, lock wait, or scheduling was available in the original failure,
so **the original cause remains unproven**. This probe neither establishes a
production blocking defect nor proves scheduler/load causation. No production
code or timing threshold was changed. Root retains the integrated failure and
owns aggregate rerun decisions.

## Failure-only gate diagnostics

The existing test now records current-thread CPU around the unchanged caller
wall interval. CPU timing begins before the existing wall start and ends after
the existing duration append; its slightly wider interval is stated explicitly
in the assertion message. The original wall start/end, production calls,
sleep schedule, scan/retry assertions, and strict `< 20` ms oracle are unchanged.
A failing wall assertion now includes the slowest tick index/count, paired CPU
measurement, and scan/retry counts. This reports evidence without assigning a
cause. All 15 DT7 dispatch tests passed after this diagnostic-only edit; caller
maximum in that run was 12.2110 ms. No injected method wrappers run in this gate.
