# Independent wallpaper admission correction review

Fixed candidate `643accdfaa546dbb8c073c706c51ca8d7c8dd83f`, reviewed in a separate
detached worktree. **PASS for the admission/retirement contracts below.**

The original `0184180` red test now passes. Candidate tests plus neighboring
UI-worker and decoder-lifecycle suites pass 40 methods. Three additional
independent probes pass:

1. Real worker finishes before its start wrapper raises; its identity remains
   joinable and final close clears the retained owner.
2. Admission pauses, close waits, a replacement is rejected, then start rejects
   without admission; close completes without joining an unstarted thread.
3. Capture release fails after worker completion; first close reports and retains
   the actual capture, replacement is vetoed, and only explicit second close
   retries and releases it.

The pending-start reservation remains visible between publication and actual
admission. Strict close sets its closing guard, revokes current work, waits for
reservation resolution outside the lifecycle lock and joins admitted retirees.
Cancelled/stale decode entry checks precede native factory calls. The generation
captured before caller-side OpenCV import prevents late import completion from
admitting a revoked request. Constructor/start failure is contained without
turning an actually admitted owner into an empty slot. Original red test and
existing cleanup tests were not relaxed.

This evidence uses real Python threads with inert captures and module stubs;
no native video/camera, desktop or GL behavior is certified. Unbounded native
blocking may still keep strict retirement pending, which is different from
falsely claiming completion. The integrating owner must run its final exact-source
aggregate after all corrections, separately from this candidate review.

Reproducer and logs: `../evidence/clean-code-20261017/wallpaper-independent/`.
Run the independent probe with the pinned Python, `review.py CHECKOUT_PATH`.
