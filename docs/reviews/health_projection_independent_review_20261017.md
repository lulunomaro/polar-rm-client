# Independent HP projection review

Reviewed exact `7d5e25174a13e1d886edd5e006616fd0459688c5` in a separate detached
worktree; preceding red checkpoint `d304fb0`. Accepted, with no blocking finding.

## Producer and source-selection checks

The regression exercises actual encoded referee messages and the annual state
adapter: local robot 3/103 acquires dynamic HP 275, a static identity message
selects 4/104, and a global HP vector reports the previous robot dead. Retained
per-ID observations previously overrode an explicit global zero through a
truthiness fallback. The new selector distinguishes missing from zero for both
table and map. Current-local dynamic HP still wins, including zero; a missing
global observation still admits the historical dynamic fallback.

Source inspection found only `sync_frontend_state` changed among the adapter's
methods. Integer conversion, clamping and publication order remain at their
existing adapter boundaries. The selector does not introduce timestamps or
claim freshness arbitration. No control authorization or transport effect is
introduced.

## Oracle integrity and independent negative control

The historical frontend fixture is byte-for-byte unchanged. The domain AST
exception checks the original exact digest and admits one exact replacement
class digest. Other class pins remain unchanged; this is an explicit reviewed
semantic correction rather than a wildcard exception. The new frozen-delta
test permits only the expected table HP/death and map HP differences and checks
all other model output for equality. Four behavioral mutants remain rejected.

Independently extracted `sync_frontend_state` from `d304fb0` and temporarily
substituted it in memory on the candidate class. The actual-producer death test
then produced the two expected red/blue subtest assertion failures and zero
errors. The original files remained untouched. This verifies the new producer
test detects the pre-fix defect rather than merely agreeing with its helper.

## Executed checks

Use the prepared hash-pinned CPython 3.12.14 interpreter as `PYTHON`, from the
exact detached candidate worktree:

```sh
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen CI=true \
  "$PYTHON" -m unittest discover -s tests -p 'test_referee*.py'
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen CI=true \
  "$PYTHON" tests/test_state_overlay_contract.py
```

Results: **55 referee tests and 11 overlay tests passed**. The review's first
referee invocation accidentally used the configuration worktree and ran 47
tests; that result is not evidence for this candidate. The corrected invocation
verified exact candidate HEAD and the 55-test result above.

No hardware, native packaging, live network or aggregate verification was
performed. The writer's source ledger at
`docs/state_projection_ownership_20261017.md` retains the separate freshness and
duplicate raw-feed observation questions; acceptance does not resolve them.
