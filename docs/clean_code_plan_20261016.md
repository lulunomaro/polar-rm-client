# Clean Code refactoring plan — 2026-10-16

## Timebox and source

Requested budget: 04:18–09:18 UTC (12:18–17:18 China time).
Starting published candidate: `d64f1528e2e1864ef369537c026b6d3f7127f590`,
source-equivalent local commit `da91fc54b97caffd8f76b1aba86f88105d3c0379`.
The prior stable offline result is 115 PASS, 12 SKIP, 4 INCOMPLETE, 0 FAIL;
missing external/native acceptance is retained, never upgraded to a pass.

Guidance: [Clean Code translation supplied by the user](https://aeryzhao.github.io/cleancode-book/),
especially [functions](https://aeryzhao.github.io/cleancode-book/ch3.html),
[classes](https://aeryzhao.github.io/cleancode-book/ch10.html),
[boundaries](https://aeryzhao.github.io/cleancode-book/ch8.html),
[tests](https://aeryzhao.github.io/cleancode-book/ch9.html), and
[concurrency](https://aeryzhao.github.io/cleancode-book/ch13.html).
These principles guide judgment; arbitrary line limits are not a correctness test.

## Parallel responsibilities

| Lane | Concrete outcome | Boundary |
|---|---|---|
| UI | Explicit scheduling/presentation components and preserved facade | Window/UI only; config adapters coordinated |
| Backend policy | Immutable inputs and pure policy decisions | Manager control logic, not lifecycle orchestration |
| Lifecycle | Owned transition state machine and nonblocking UI requests | Safety neutralization on owner thread; teardown ownership explicit |
| Configuration | One validated transaction/migration boundary | Stable IDs/schema, whole-map bindings, rollback remains fail-closed |
| Engineering checks | Reproducible quality inventory and ratcheted gates | No invented all-green score, no automatic paid CI |
| Independent review | Counterexamples, boundary audit, narrow unowned defects | Reject cosmetic extraction and weakened tests |

## Acceptance matrix

1. Components have a specific reason to change and explicit dependencies. Moving
   methods into more mixins or passing the entire window/manager as an opaque
   host object does not satisfy extraction.
2. Characterization captures existing behavior before replacement. Protocol
   bytes, authority gates, default configuration, key mapping, frame order and
   render results remain compatible unless a documented defect is repaired.
3. Pure decisions are independently testable. I/O, application state mutation,
   rendering and user-facing errors have named boundaries.
4. Worker resources have single ownership, cancellation/generation rules and
   bounded queues. Qt work remains on its owner thread. Neutral-before-close
   obligations are not removed to improve apparent frame latency.
5. Configuration has explicit validation and transaction semantics. Existing
   stored profiles migrate deterministically; failures cannot arm output.
6. Errors remain observable and contextual. Deliberate fallback is distinguished
   from unexpected programming failure; no blanket swallowing to pass tests.
7. Tests assert behavior at useful boundaries. Private compatibility snapshots
   may be retained as transition aids but cannot substitute for behavioral tests.
   Updating a snapshot requires explaining every intended contract change.
8. Metrics distinguish executable complexity from data catalogs and shader text.
   Threshold changes and exemptions are reviewed, not used to hide regressions.
9. Each independently verified stage is committed and integrated with regression
   evidence. Main is not merged automatically; unrelated assets/data are retained.
10. The final report separates achieved improvements, residual debt, failed
    candidates and missing hardware/Windows evidence. It must not claim an
    absolute absence of bugs or universal Clean Code compliance.

## Integration protocol

Each lane has an isolated worktree and branch based on the same candidate.
The parent reviews stage commits, resolves shared-manager/window adapters, runs
focused and aggregate checks on a stable integrated snapshot, and publishes
checkpoints. Final validation uses the exact published source tree and records
skips and incomplete inputs. Review findings feed back into the owning lane.
