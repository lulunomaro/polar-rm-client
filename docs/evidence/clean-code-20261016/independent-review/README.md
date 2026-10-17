# Independent late-candidate review evidence

All scoped candidates below passed independent review. These are separate fixed
source runs, not one combined integration run, release qualification or native
hardware result. The complete responsibility inventory and acceptance limits
remain in `docs/reviews/whole_client_debt_inventory_20261016.md`.

- Vision selection `661daed`: 173 methods, actual API contract.
- Final constructor timer ownership `cd80ec7`: 132 methods and actual API;
  `1173a2e` separately reproduces the escaped-work defect as an expected failure.
- Profile-save owner `9d3a849`: 247 methods and actual API; 29 original tests also
  pass on pre-extraction `77b4c92`. Whitespace-only `15c8608` is accepted.
- Normal-video timeline `f542fcc`: 212 methods, two existing source smoke
  functions, actual API and static gate; 30 original tests also pass on
  pre-extraction `5700c6c`.
- Economy value/vocabulary alignment `f3e0d04`: 145 UI/contract and 35 domain
  methods, actual API and static gate. The three label compatibility tests also
  pass on pre-change `d86f344`.
- Serial discovery correction `633e48d`: 77 focused/neighbor methods, a separate
  nine-method run and 100 repeated nine-method runs, static gate. The original
  `706b136` red fixture has two failures and one pass; its functions remain exact.

The manifest records exact candidate commits, commands, counts and artifact
SHA-256 hashes. The three static summaries omit the per-module mapping, including individual
function, import and lint-finding detail. Source digest, whole-tree totals,
unchanged gate revision/verdict/violations, syntax errors and both
initialization/deferred cycle graphs are retained. Standalone candidates
still have other lanes' unresolved deferred edges; do not mistake their passing
initialization-cycle gates for a whole-tree integration claim.

The failing/expected-failure outcomes in the two red logs are defect evidence,
not successful cleanup. The repeat run is stress repetition, not 900 unique tests. The 77-method
serial run includes the dedicated nine methods. Other candidate suites also
overlap and must not be summed as unique coverage.

Earlier invocation attempts with misspelled suite names produced import errors;
the corrected full commands were rerun from the exact candidate trees and are
what the named corrected logs record. No failing product result was removed or
reclassified. Offscreen Qt/audio warnings and intentionally injected exception
traces remain visible.

The alignment-only economy diff is whitespace-clean. A full baseline-to-economy
range still reports EOF blanks in two original hash-pinned source fixtures;
these were preserved rather than changing behavioral references for cosmetics.
No historical API/visual/behavior fixture or static budget was relaxed.

The separate admission review in `../admission-independent/` was authored by a
different reviewer because the inventory author implemented the angle/logger
corrections. This directory does not turn those authored tests into independent
self-approval. Aggregate verification, packaging and native Windows/GPU/device
acceptance remain separate and parent-owned.
