# Polar connection acquisition boundary

Scope: offline Clean Code refactor of `PolarLinkTransport` connection acquisition.
No codec, frame emission, stop/neutral ordering, control authorization, or native
hardware behavior is changed or qualified. No quality baseline changes.

## Ownership and ordering

- The lifecycle lock reserves one connector and captures an immutable endpoint
  configuration, including mock mode. Native enumeration/open runs outside it.
- `polar_connection.acquire_polar_connection` returns the endpoint, diagnostics,
  and any native owner; it cannot publish a connection, start a writer, or close
  the returned resource. A `StreamOpenError` retains its partial native owner.
- The transport reacquires its existing lock before comparing generation,
  stopping state, and configuration. A stale open is never made visible to TX.
- Rejected/failed handles close before the connector reservation is released.
  Failed close keeps `_serial`, blocks replacement, and remains explicitly
  retryable through the existing stop path.
- Successful publication still clears old RX and control state before admitting
  the existing worker. Stop, estop, join timeout and neutral-burst code are intact.

## Measurement (repository AST metrics)

`_connect_attempt`: 94 -> 57 source lines; 75 -> 45 statements;
18 -> 7 decisions; maximum control depth remains 4.
New acquisition function: 33 source lines, 21 statements, 10 decisions, depth 4.
The bridge-alias decision resides in `PolarConnectionConfig.uses_bridge`.
This is an explicit responsibility split, not elimination of native lifecycle
complexity. Publication still touches the same transport state members.

## Reproduction and actual results

Python: `/workspace/shared/client-clean-venv/bin/python` (3.12.14).
Set `QT_QPA_PLATFORM=offscreen CI=true PYTHONPATH=.` from the checkout root.

- `python tests/test_polar_connection_acquisition.py`: 9 tests pass (6 ran against
  pre-refactor implementation before the characterization commit).
- `python tests/test_polar_async_connect.py`: 5 tests pass, including concurrent
  reservation, cancelled/config-changed opens, slow close, and nonblocking estop.
- `python -m unittest discover -s tests -p test_transport_safety_regressions.py`:
  36 tests pass, including deadman, neutral, write failures and transport guards.
- `python tests/polar_link_test.py`: passes.
- `python tests/polar_bridge_test.py`: passes.
- `python tests/polar_integration_test.py`: passes.
- `python -m unittest discover -s tests -p test_transport_start_admission_matrix.py`:
  2 tests pass. Direct script invocation initially lacked the checkout import
  path; discovery with the explicit path above passes.
- `python tests/test_serial_lifecycle_regressions.py`: 19 tests pass.
- `python tools/quality_gate.py`: passes unchanged baseline, 216 Python modules,
  3405 functions, zero initialization cycles.
- `git diff --check`: passes.

Full aggregate verification belongs to the integrating checkout. These results
use fake streams, not a robot, physical serial port or live TCP bridge.

## Remaining debt

Publication and worker admission are still interleaved under the lifecycle lock.
Polar's connector/stopper admission paths remain separate from the retained
`WriterThread` abstraction used elsewhere; constructor and late-raising start
wrappers need a dedicated characterization matrix before attempting to unify
those ownership rules. Estimated next bounded stage: 2–4 focused engineering
hours for tests, ownership review, refactor and regression evidence. This is an
estimate, not evidence of a verified defect or hardware readiness.
