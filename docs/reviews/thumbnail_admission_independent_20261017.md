# Independent thumbnail native-activity ownership review

Fixed candidate `a23999ff57de95f9adf0043c43cef0da06cca911`, reviewed in a separate
detached worktree. **PASS for the native-activity admission/drain correction.**

The red case `d6c136b` uses the actual ThreadPoolExecutor. In the pinned Python,
its worker is started before being added to the executor's thread set. A wrapper
that starts the worker then raises can leave a native reader absent from the
set joined by shutdown. This is fault injection at a real ownership boundary,
not a claim that ordinary CPython start normally raises after admission.

The correction does not patch executor-private fields. Decoder load now atomically
checks a terminal admission gate and increments its activity count before native
acquisition; the count decrements only after decode and capture cleanup/retention.
Close seals new admission and waits on a condition without retaining the activity
lock, then applies the existing report-first/explicit-retry capture policy. A late
executor task entering after close cannot open native resources. Manager submission
errors become visible diagnostics and do not escape the thumbnail paint call.

Independent verification:

- 32 focused methods pass: thumbnail admission (6), wallpaper admission (5),
  UI worker shutdown (21). These include the actual executor red case, delayed
  entry, queued constructor rejection, normal cache behavior and release failure.
- An additional two-worker probe pauses both native acquisitions, begins close,
  rejects a third late load, permits one acquisition/release and verifies close
  still pending, then permits the second and observes zero active loads and both
  releases. Real Python threads, inert capture factory; no GUI/device required.
- Candidate diff check passes. No oracle, timeout or join was relaxed.

This proves the enumerated native ownership contracts, not every executor
scheduler interleaving or native video output. A cancelled late pure-Python worker
may still finish its return path after close; the terminal gate prevents native
work. Permanently blocked native I/O can still keep close pending, as it must.
Final combined-source verification remains separate.

Logs and runnable independent probe:
`../evidence/clean-code-20261017/thumbnail-independent/`.
Run the pinned Python with `review.py CHECKOUT_PATH`.
