# Input cleanup failure isolation

Base: public `c22d9ba81d1d8cf975d85df03fb26412c313b524`. Recovery checkout was restored from GitHub; unavailable unpublished changes are not assumed recovered.

## Reproduced defects
Four deterministic offscreen/inert-output tests on the base produced three failures and one error:
- A failing local reset skipped both independent neutral egresses and pending-clear.
- A failing DT7 release skipped custom-controller release and pending-clear.
- A failed MQTT mock-output property lookup escaped modal cleanup before any reset or fail-closed latch.
- A synchronous output callback could inject W during the lock hook's cleanup.

## Change
The lock hook latches input blocking before cleanup, attempts independent actions separately, records every failure and rethrows the primary exception for the existing backend authority latch. Qualification and mouse capture are invalidated even when cleanup fails. A successful explicit cleanup clears the UI latch. Modal mode lookup is inside the guarded cleanup path; ordinary publication requires both valid mode and successful reset.

Focus-loss has its own independent fallback. The existing fixture now asserts exactly two neutral attempts and two pending clears on persistent reset failure (lock observer plus focus fallback); no authority checks were removed and no timeout was increased. The initial focused failure is retained locally.

## Verification
- New four-method regression: original source 3 FAIL / 1 ERROR; modified source 4 PASS.
- Eleven focused test files, 123 unittest methods: PASS (input cleanup, modal epoch, backend owner, backend lifecycle input failure, configuration quiescence, focus loss, tick contract, API contract, escape menu, keyboard characterization, emergency failures).
- Full offline regression: pending at this checkpoint.
- No independent reviewer, real robot, serial hardware, Windows/GPU or field qualification is claimed. Previous complete scores apply only to their original source commits.
- Raw logs remain local and are not included in this publication.

## First complete profile and F12 fixture correction
Source `9271054b55d427b7f83b77f296a2160b76b3ba65` / public `22aa04b61de8d7dc1acf1a033adfbe6d7475a794` completed **323 PASS / 12 SKIP / 4 INCOMPLETE / 2 FAIL**. The two failed files contain three F12 cases expecting one pending-clear call after a failed reset. Pending clear now occurs independently in the lock observer and in the F12 adapter's fallback. The fixture asserts exactly both calls, preserving all authority, E-stop, re-arm rejection and warning checks. No production change is included in this follow-up.

The two corrected files passed 6 + 21 unittest methods. A new full-profile run is required; the failed profile is not overwritten or relabeled.

## Follow-up complete offline result

Exact tested source `86eace0bb2e731eff0dc271899515b99dd5ce46e`, tree `0058a8787830cca3cbe079eada53b1fa971d62f6`, public equivalent `e47ce1646dfda4fe4034c270d1a694865e00f6c2` completed **325 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL**. Aggregate is **NO-GO** because prerequisites remain absent. All 674 declared inputs retained identical start/end SHA256 `8b918e199475605a851642b40d808c81f9b3c35b1e441a5ce1a037615a36d415`.

The four incomplete entries remain the team CRC cross-reference, firmware/dump.cs DT7 comparison, DT7 firmware UI self-test comparison and legacy installation-required firmware patch test. No independent review, Windows/GPU or physical robot qualification was performed. New raw logs are not published. The previous 9271054 failed result remains separate.
