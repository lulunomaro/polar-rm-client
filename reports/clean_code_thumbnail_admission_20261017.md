# Thumbnail activity ownership independent of executor bookkeeping

Red checkpoint d6c136b uses a real ThreadPoolExecutor and a real worker blocked
inside mocked native capture.read. Thread.start admits that worker then raises.
CPython's executor registers the thread only after start returns, so shutdown
joins no thread and previously reported completion while native read was active.
The returned Future is also unavailable to the caller on this path.

## Bounded fix

ThumbnailDecoder atomically admits each load under its condition lock, counts it
through capture release (including failure retention), and seals future admission
before waiting for active loads at close. A task delayed before decoder entry is
rejected after close without touching native I/O. Explicit close retries still
retry only retained failed capture releases. Concurrent closes are serialized.

The manager contains executor submission exceptions as an empty thumbnail plus
a diagnostic. Existing cache hits and successful future publication stay intact.
No executor-private fields are read or repaired; no visual/default changes.

## Verification

Hash-pinned shared verification Python; checkout environment:
`PYTHONPATH=. QT_QPA_PLATFORM=offscreen CI=true`.

- tests/test_thumbnail_admission_audit.py: 6 tests pass, including actual executor
  late admission with read active, late admission before decoder entry, constructor
  rejection/cancelled queued work, closed submission/cache preservation, normal
  open future/cache publication, and blocked release failure plus explicit retry.
- The complete six-test suite repeated ten times: 60 passes.
- tests/test_wallpaper_admission_audit.py: 5 pass.
- tests/test_ui_worker_shutdown.py: 21 pass.
- tools/quality_gate.py: unchanged baseline passes, 218 modules, 3419 functions,
  zero initialization cycles.
- git diff --check passes.

This qualifies Python ownership with inert native capture objects, not real
OpenCV codecs, GPUs or devices. Final aggregate and independent review remain
integration-owned. A genuinely non-returning native read keeps final close
pending; the code never claims that resource is retired.
