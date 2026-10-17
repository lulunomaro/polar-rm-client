# Retained rejected stage 2

Exact source `93aa839c8ed052692fa83669e92f7999f67db25d`, tree
`f7ca4dc0e61ea3fe78d7ded6756770c1b1a4b8bb`: 309 PASS, 12 SKIP,
3 INCOMPLETE, 2 FAIL. Declared input fingerprints were unchanged.

The failures are retained verbatim:

- Video source retry: a test retried after one native closer finished, while
  the independent UDP closer could still run. The production busy guard correctly
  rejected retry. A later deterministic barrier regression preserves that
  rejection and waits for the independent closer before testing the native retry.
- Window activation failure: the timer-construction subprocess exceeded its
  unchanged 30-second limit. Later focused repetitions pass, but no cause or
  corrective production change has been established. Additional diagnostics are
  not described as a fix.

This profile predates the later module-drag source change and cannot certify it.
No result is overwritten by a later successful rerun. `raw-records.tar.gz`
contains 654 generated verifier outputs, with exact member SHA256/size in
`raw-manifest.json`. The original runner/results/per-suite stdout/stderr remain
unmodified. Runtime platform/dependency/source provenance is in results.json.

The same offline command as the pre-correction report was used, with the
hash-pinned verification environment and fixed external CRC reference. The
complete latest-source profile must be rerun after final integration. Native
Windows, GPU and hardware requirements are separate and unexecuted here.
