# Independent failed shader-candidate ownership review

Exact reviewed candidate: `04425d145dc7d9aa53c87bd62d26535b18dfbf2a`.
No production edits were made in that clean candidate worktree.

Independently reran all 60 compositor test methods: PASS. The attached additional
two-method review also passed (three simultaneous-owner subcases plus a private
carrier case); its actual log is `result.txt`.

The independent matrix verifies that an earlier program and the failed compile
candidate can both reject retirement without preventing other cleanup attempts.
Repairing only the prior owner clears only that slot, leaves the exact candidate
retained and admits no new builders. Repairing the candidate subsequently permits
a fresh successful lifetime. The original immutable failure snapshot retains its
compile cause through later cleanup retries.

The private `_compile` boundary deliberately raises the new RuntimeError carrier;
its exact original error/cause and candidate identity are preserved. A direct
private caller must retire that candidate itself, while normal initialize adopts
it and performs the complete cleanup. This documented private error-contract
change is accepted; successful return objects and signatures remain unchanged.

No blocking findings. Native shader compilation, context/driver teardown and
GPU output remain unqualified by inert mocks.

After integrating the named shader candidate, reproduce with:

```sh
QT_QPA_PLATFORM=offscreen python docs/evidence/shader-independent-review-20261016/review.py
```

The isolated review ran this same script body with imports rooted at the exact
candidate worktree; the retained version roots imports in its containing checkout
so it is reproducible after integration. No copied production code is used.
