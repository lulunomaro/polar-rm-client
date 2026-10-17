# Independent video-source retry ordering review

Fixed test candidate `909c579e18c08e7ae6f4dbff6f07e73715acadde`, reproduced red
`539509ae1bee0de49f0f564e415548c1bef7aa72`, each in its own detached worktree.
Outcome: **PASS for the test correction; no production change required.**

The original aggregate at `93aa839` failed because the test waited for the
native decoder closer but immediately requested retry while the independent
UDP closer could still be active. Production correctly refuses that request.
The red checkpoint makes the race deterministic by holding UDP socket close at
an event and reproduces the same `Video-source cleanup is still in flight` error.
Independent red execution returns one unittest error, matching the original.

The corrected test preserves every original assertion and explicitly asserts
non-retryability/rejection during the forced overlap, unchanged native/UDP worker
identity and generation, UDP release after the barrier, and retained native
failure becoming retryable only afterward. The eventual successful retry and
no-refresh-replay assertions remain. No production safety gate, timeout, skip,
API or baseline changed. Waiting for the unrelated owner is the prerequisite
missing from the former fixture; it is not suppressing a production failure.

Independent corrected execution passes 35 methods across backend video source,
retry admission, source snapshot/evidence, native close ownership and UDP stop
suites. Full base-to-candidate diff check passes. Logs are retained under
`../evidence/clean-code-20261017/video-retry-independent/`.

These focused results do not supersede the original aggregate NO-GO at `93aa839`
(309 PASS, 12 SKIP, 3 INCOMPLETE, 2 FAIL). The separate window activation timeout
is not resolved by this test correction. A final corrected-source aggregate is
required. Native streaming/device qualification remains outside this review.
