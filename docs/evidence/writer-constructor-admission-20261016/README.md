# DT7 / CC writer constructor admission ownership

Base: `c4d3bcf97ef43056bf316bbec593802652120878`.

## Frozen red boundary

Before production changes, `tests/test_writer_constructor_admission.py` executes
real asynchronous writer objects and real `threading.Thread` workers. Handles
are in-memory fakes. The injected admission wrapper either rejects before
starting, calls the real `Thread.start` then raises, or raises after the worker
has already finished. This defensive fault model is **not** a claim about usual
CPython behavior. No real serial/native device or external service is used.

Command (repository root):

```
/workspace/shared/client-quality-venv/bin/python -m unittest tests.test_writer_constructor_admission -v
```

`red-tests.txt` records the unmodified source result: live workers were not
cancelled, manager slots lost the failed constructor owner, and no direct
exception recovery owner existed. Additional red contracts cover the planned
optional `retain_owner` constructor parameter, original `BaseException`
identity even when annotation is rejected, never-admitted cleanup, finished
admission, slow/unconfirmed native close, and cleanup admission failure.

`retain_owner` is an intentional optional API extension; existing positional
arguments and constructor defaults remain supported. It is a caller-owned
retention seam, not a global owner registry. A registered owner must be kept
until close is confirmed, including after a constructor error.

## Ownership changes

Production changes are limited to `rm_client/backend/dt7_worker.py`,
`rm_client/backend/cc_link.py`, and the two writer-construction boundaries in
`rm_client/backend/manager.py`. Codec, frame, safety policy and actor/aim source
is unchanged.

- Inert initialization precedes an optional `retain_owner(owner)` callback.
  The callback runs once outside the worker condition. BackendManager publishes
  the writer into its existing `_dt7_link` / `_cc_link` slot before admission;
  ownership does not depend on exception annotation, traceback, or garbage
  collection. Sync/mock factories receive no new keyword.
- If the callback closes the owner, construction returns that sealed owner;
  it does not create a regular writer behind an admitted cleanup worker.
- A short initial-admission reservation prevents a concurrent `close()` from
  creating a second cleanup owner while Thread construction/start is unresolved.
  No lock is held over the initial `Thread.start`. Cancellation is immediate;
  the admitted worker drains, or a rejected admission gets background cleanup.
- A terminal admission error preserves the exact original `BaseException`.
  Cleanup-admission failure and rejected exception attributes cannot replace
  it. `failed_writer` is best-effort compatibility metadata; callers needing
  guaranteed retention should supply the callback and keep its owner through
  confirmed cleanup. No global recovery registry was introduced.
- `close()` remains asynchronous. A never-admitted owner is not considered
  closed merely because it lacks a live worker. `wait_closed()` skips invalid
  joins, reports unconfirmed close as false, and permits an explicit later close
  retry. A native-close failure retains its handle. A completed failed finalizer
  is not implicitly retried just because the start wrapper subsequently raises.
- Manager polling keeps an unsuccessful startup disabled and retains its
  owner until cleanup is confirmed. Polling does not replenish close retries.
  Existing explicit close/shutdown retry paths remain the recovery boundary.

## Independent counterexamples and verification

The independent reviewer supplied the callback-cancellation counterexample
(original commit `113f4ba`, included as `3ed7522`). Its test method is unchanged;
only the sibling import was made compatible with both standalone and
`python -m unittest` execution. `retention-cancel-red.txt` records both real
writers admitting duplicate closers before the reservation fix.

`admission-cancel-red.txt` separately freezes cancellation during Thread
construction/start: 8 failures across the 12 pre-fix combinations. The reviewer
then supplied `test_writer_concurrent_admission.py` (original `d338c4e`, included
as `b0ed1ae`), which uses a different real thread to cancel while admission is
paused. All 12 combinations confirm one admitted cleanup owner, prompt
cancellation, the original error identity, and truthful close state.

`finished-failed-red.txt` freezes the already-finished failed-finalizer case
before the explicit-retry correction. `constructor-tests.txt` includes direct
constructor and callback cancellation checks. `targeted-tests.txt` records
85 passing methods across constructor/admission, normal dispatch, retained
close, manager shutdown retry, input ownership, angle handoff, egress safety,
and the frozen configuration/effect suites. The frozen fixture itself was not
regenerated; its fake now models the ownership publication callback separately
from endpoint configuration.

The focused suites also passed 10 consecutive in-process runs (17 methods per
run); each run checked that no writer, close-retry, or independent constructor
thread remained alive. See `repeated-tests.txt`. `quality-gate.txt` and
`quality-summary.json` record static gates PASS, 204 parsed Python modules,
3,342 functions, and zero module-initialization cycles. The static report is
source-digest bound; static metrics are not runtime qualification.

Reproduce the focused repeated-run set:

```
/workspace/shared/client-quality-venv/bin/python -m unittest \
  tests.test_writer_constructor_admission \
  tests.test_writer_retention_cancellation \
  tests.test_writer_concurrent_admission -v
```

Reproduce the full targeted set by additionally selecting:
`tests.test_dt7_worker_dispatch`, `tests.test_cc_worker_dispatch`,
`tests.test_writer_close_ownership`, `tests.test_backend_shutdown_retry`,
`tests.test_backend_input_owner`, `tests.test_angle_writer_handoff`,
`tests.test_dt7_angle_ownership`, `tests.test_device_egress_safety`, and
`tests.test_device_egress_characterization`.

## Scope limits

All results are offline fault-injection evidence using real Python workers and
fake native handles. They do not qualify actual serial hardware, Windows
thread/driver behavior, or CPython failure likelihood. A user-supplied callback
can itself block; the API does not make arbitrary caller code nonblocking.
No global verification run, remote write, main-branch merge, external API, paid
service, or user Codex operation was performed in this worktree.

## Parent recovery review, 2026-10-16 11:45 UTC

Worker review was interrupted by the service limit. The parent inspected the
final production diff through 2212fe2, including the additional failed CC
finalizer retry fix, and reran all twelve targeted suites: **86 methods PASS**.
The separate five-suite ownership run passes **32 methods**; these overlap and
must not be summed. Logs are parent-recovery-tests.txt and parent-focused-tests.txt.
This replaces the earlier 85-method count for this final candidate only.
Full integrated verification and physical qualification remain pending.
