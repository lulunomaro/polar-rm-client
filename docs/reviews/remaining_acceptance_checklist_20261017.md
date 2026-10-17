# Remaining architecture acceptance: evidence-based close-out

## Current reference-validation checkpoint — 2026-10-17 21:10 UTC

Exact tested public source `734ff5a8356e9571bd8874883ccc02aa3767c9a0`
/ local `99167e88675b66a509f9a8266c71175747452a9e`, tree
`80e30d54bfee9dcb996726f71132510095126cc3`: **349 PASS / 12 SKIP /
4 INCOMPLETE / 0 FAIL**, **NO-GO**. All 698 declared inputs retained SHA-256
`921161f1d2ca8a089fedb769cf236407919a33eecc4aaf858a7319efdb5c096e`.

The DT7 validation now rejects an empty application source tree as missing evidence
and accepts an explicit vendor-dump path without substituting a missing override.
Independent focused validation: five methods passed; historical scripts produced
five failure outcomes and no errors. Synthetic fixtures test these preconditions,
not external compatibility. Actual references remain absent. The legacy patch test
is deliberately excluded from the read-only aggregate even if artifacts exist;
separate disposable-copy evidence cannot silently turn its entry into PASS.
See [external input and qualification handoff](external_acceptance_inputs_20261017.md).

The following dated checkpoints remain historical evidence; no full run is active
at this update. Native Windows/GPU/audio and historical timing causation remain open.


## Current close-out index — 2026-10-17 20:58 UTC

- Earlier completed audio aggregate is public `d8a90c52d3a52b390c1a014750191dc5b760a3b7`
  / local `bd7115ffdcab11c5771fac362aabe97f960b22bd`: **345 PASS / 12 SKIP /
  4 INCOMPLETE / 0 FAIL**, **NO-GO**. See
  [audio admission repair](kill_audio_admission_repair_20261017.md) for exact tree,
  fingerprint and limits. This accepts the tested fallback/ownership boundaries,
  not physical repeated audio playback or the user's native symptom's root cause.
- Later field-log admission candidate public `a44cf8d` / local `197f428` handles
  ordinary constructor/start exceptions while retaining uncertain admitted work
  and loss accounting. Its full offline regression is now **completed: 346 PASS /
  12 SKIP / 4 INCOMPLETE / 0 FAIL, NO-GO** (`passed=false`). Results were read from
  `../client-field-log-full/results.json`, run `b9108a31ebd1461c93affd616b821afc`,
  started 2026-10-17 20:29:29 UTC. Exact local source is
  `197f4284e59efc432750e1cf6051c5b8f0e5ed18`, tree
  `daebc6193463f7bc74e24d4f84c106e50959c4ec`. All 695 declared verification inputs
  have identical start/end SHA-256
  `b448096787d185cda7ad5ccf3df23227997c19a350112502d7daf7d1d23d2625`;
  source stability PASS. This fingerprint excludes docs/assets/external fixtures/
  installed packages and cannot detect transient edits restored between captures.
  This completed result predates the startup-lock test checkpoint below. Drained
  does not mean lossless or fsynced; offline success is not native qualification.
- All 242 declared RobotState fields have bounded writer/default-only dispositions.
  Normal referee scalar publication now belongs to the decoded adapter; raw
  consumption retains receipt freshness/events. Compatibility APIs, dynamic
  extensions and exhaustive runtime semantics are not globally single-writer proof.
- The named serial starter/reader, Paho/MQTT cleanup and decoder constructor
  cells are discharged by the constructor matrix. The early-window ledger is
  enumerated, with bounded feedback, idle-wallpaper and actual-window prefix
  probes; neither establishes every transitive constructor or native lifetime.
- The startup-lock actual-window prefix now has bounded independent acceptance:
  two new tests plus three neighboring constructor tests **5 PASS**. Original
  error preservation, remaining cleanup attempts and observed owner retirement
  pass; deliberate window/kill-pool retention each produces **1 FAIL / 0 ERROR**
  before rescue. No leak was reproduced and production code was unchanged. See
  [early-window ledger](window_early_resource_audit_20261017.md).
  New test source public `f425fbc5124b86262292d0c95c98b170883da283` /
  local `a6cff9bac6151f6298ec7aa521d582266fc28ffb`, tree
  `7d39fb342a0c4f8916c038fda8597ad147d3c7b0`, now has a **completed** full offline
  aggregate: **347 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL, NO-GO** (`passed=false`).
  Verified in `../client-startup-lock-full/results.json`, run
  `3f80f3462d5c4118bdb8fb638acff378`. All 696 declared verification inputs have
  identical start/end SHA-256
  `6cc686c50599abfb5801dfdbba299249f345e2bbae854dda338804d0dcc9f15c`.
  This tests the named test-source checkpoint, not later documentation commits;
  the fingerprint scope/limits above still apply. This completed aggregate
  predates the next presentation-prefix test checkpoint below.
- Complete presentation construction followed by rejection of the next GPU
  factory now has bounded retirement evidence, with no leak reproduced and no
  production changes. Author **6 PASS** = new1 + early-constructor3 + startup-lock2;
  independent **7 PASS** = new1 + early-constructor3 + early-factory3. Both lanes'
  deliberate chat/hero retention controls each fail once with zero errors before
  rescue. No GPU service factory or native GL initialization occurs in this prefix.
  Public `c4ceb891da2155eb3038d2960f3493e1eb7b9fb4` / local
  `4dac2a53357c9a514bb958dc7f7a1a13162df57e`, tree
  `067b71d9f5f0fdb12ae02aad81a110e9264dc7c8`, now has a **completed** full offline
  aggregate: **348 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL, NO-GO** (`passed=false`).
  Verified in `../client-presentation-prefix-full/results.json`, run
  `5631b31e2f9c4d3db84daa1ed959baeb`. All 697 declared verification inputs have
  identical start/end SHA-256
  `d042b413836a84354fb61804b469937edbb023585c9c629ed08cc1292ee8e9d4`.
  This certifies the recorded offline results at that exact test-source
  checkpoint, not later documentation commits or unrun native paths. The stated
  fingerprint limits still apply; no aggregate remains running at this update.
  See the early-window ledger for ownership observations and limits; other
  transitive prefixes and native active-resource qualification remain open.
- Historical activation-timeout/DT7-budget causation, external prerequisites and
  native Windows/GPU/audio/portable/device qualification remain open. No complete
  Clean Code, merge-readiness or match-readiness acceptance is asserted.

The sections below preserve dated evidence. Their old “remaining”, “current” and
“running” wording describes those snapshots, not new work to repeat. This index
supersedes those statuses without altering historical results.

## Historical evidence index — 2026-10-17 20:11 UTC

- Exact source public `b59b042` / local `12e86e9` completed **344 PASS / 12 SKIP /
  4 INCOMPLETE / 0 FAIL**, aggregate **NO-GO**. See
  [canonical production writer](canonical_scalar_writer_20261017.md) for full
  identities, fingerprints, preserved failed candidate and intentional semantics.
- The normal raw-feed consumer now retains receipt freshness/events only; decoded
  adapter owns production scalar publication and air-support eligibility.
  Explicit compatibility APIs remain, so this is not a globally enforced single
  writer. Receipt freshness is not successful-state-application proof.
- [Event validation](event_extension_validation_20261017.md) rejects malformed
  numeric/deep JSON events without dropping valid raw batch siblings.
- [Event identity](event_identity_repair_20261017.md) preserves fixed roster
  mapping and rejects absent/unsupported static identities; enum meanings remain
  unqualified by that review.
- Early constructor, cleanup admission, economy input ownership, hidden wheel,
  and explicit-oracle corrections are recorded in
  [input-owner follow-up](input_owner_followup_20261017.md).
- External CRC/firmware/install prerequisites, historical timing causation and
  native Windows/GPU/audio/portable/device qualification remain open. No whole-
  project Clean Code or merge-readiness claim is made.

## Earlier evidence index — 2026-10-17 18:54 UTC

This section supersedes historical running-state/unfinished-field-inventory
statements below; historical evidence and failures remain preserved.

- Latest completed source is public `5a1c564` / local `2a2fe5c`:
  **336 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL**, aggregate **NO-GO**.
  [Exact source, fingerprint and limits](receive_time_freshness_20261017.md).
- [All 242 declared RobotState fields](robot_state_authority_audit_20261017.md)
  have bounded writer/default-only dispositions. Dynamic alias/extension and
  runtime semantic coverage are not exhaustive. Partial raw-feed overwrites and
  roster profile inference have a reproduced correction; receipt-age inflation
  has a separate reproduced correction.
- [Early construction ownership](window_early_resource_audit_20261017.md) now
  records acquisition boundaries and bounded real-QObject failure probes.
  Remaining factory prefixes and native active-owner qualification stay open.
- [Principle-based acceptance](clean_code_principle_acceptance_20261017.md)
  maps the supplied Clean Code chapters to evidence and gaps. No size score or
  blanket decoupling/completion claim is made.
- Historical activation timeout and DT7 budget causation, missing external
  prerequisites, native Windows/GPU/audio/device and portable delivery validation
  remain open. Later green runs do not close them.

## Historical checkpoints (read with exact source scope)

## Evidence update at 09:02 UTC (supersedes earlier open-cell descriptions)

- Historical aggregate `93aa839` finished **309 PASS, 12 SKIP, 3 INCOMPLETE,
  2 FAIL**. It is not a passing final-source certificate. The video retry failure
  has accepted test-order correction `909c579` (independent report `5564597`).
  The 30-second activation subprocess timeout remains unexplained: diagnostic
  `893faa3` and subsequent passing repetitions do not identify its cause.
- Constructor matrix `060f190`, independently accepted by root in `939f506`,
  discharges the named serial starter/reader, actual Paho network thread,
  MQTT cleanup thread and three decoder pipe-thread constructor cells.
  Five methods plus three rejecting mutation controls cover these cells;
  they do not certify every native constructor. Earlier instructions to add
  those exact constructor cases are complete, not a remaining task.
- Kill-cue audio retirement `cc33702` needed reviewer correction `f367729`.
  Red `bf85c5f` is retained. Independent 71-test selection plus an additional
  shutdown/decoder-error drain probe accepts active decode ownership and sealed
  delayed admission. Idle unregistered Python threads may finish asynchronously.
  Add this owner to the matrix: active/failed decode drain **T**, delayed entry
  after seal **T**, actual late executor admission **T**, physical audio **external**.
- Glass command buffer/per-draw geometry `b08427f` is independently accepted:
  86 tests and 48 complete baseline/current GL trace pairs. This discharges the
  selected core-rendering responsibility split while preserving live command
  payload reads, membership snapshot timing and failed cache publication.
- Independent detailed reports and raw evidence are in
  `audio_retirement_independent_20261017.md` and
  `glass_owner_independent_20261017.md` (review commit `b089ec4`).

Additional unresolved strict-budget evidence: root's running full profile at
`f34dbda` failed `test_dt7_worker_dispatch.py::DT7WorkerTests::test_no_device_real_time_100hz_tick_retry_budget`:
maximum 22.028550001778058 ms against the unchanged strict `< 20` ms assertion
(15 methods, one failure). Source log was independently read at
`/workspace/shared/clean-ten-hour-baseline/logs/tests__test_dt7_worker_dispatch.stderr.log`.
The reported isolated passing sample at `750c842` and diagnostics-only `b53d832`
do not establish its cause or resolve that failure. Do not label the still-running
aggregate passing or relax the measured region/threshold.

Remaining work is exact-source aggregate reconciliation, unresolved activation
and DT7 strict-budget failure diagnosis, and the bounded acquisition/state/config/input reviews
already assigned by the integrating owner. Keep native Windows/PowerShell,
GPU/device/audio/codec qualification separate. No new speculative rewrite or
schedule estimate follows from these tests. Earlier historical RED rows are
retained as evidence; their named accepted corrections determine current status.


## Historical scope — integration at `8dbe624`

This revision replaces the initial broad planning envelope with concrete
candidate-specific dispositions. Base inventory is `1b2f580`; reviewed follow-on
changes are named below. The integrating owner's final combined revision and
aggregate are still required. Latest integrated source is `8dbe624`, including
independently accepted movement candidate `ec80a3a` and its report `92d4af9`.
The earlier `93aa839` full profile is still running at this update; the final
`8dbe624` source profile will follow. No further production edits are planned.
A passing candidate does not retroactively change
base results or certify unrelated paths.

Three statuses must not be conflated:

- **Reproduced code defect:** executed failing contract, with a named red case.
- **Evidence gap:** an important contract has no established test in this review;
  this is not proof of incorrect production code.
- **External qualification:** requires native desktop, devices, firmware or
  packaging prerequisites; mocks and architecture refactoring cannot close it.

No remaining engineer-day total is asserted: earlier broad estimates mixed
already-covered cells, possible redesign and external work. The finite next
steps below are a more useful basis for scheduling. No new extraction is required
merely because a method is long or receives a host object; retain cohesive code
with a named responsibility and an explicit boundary limitation.

## Historical eight-gate update — superseded status, retained evidence

| Gate | Concrete evidence now discharged | Remaining condition and category |
| --- | --- | --- |
| A. Evidence/oracle integrity | Referee metadata oracle fixed at `27eb687`; four red mutations at `95b5a24`, 22 focused methods and static gate pass, independently reproduced. Build interpreter `b0f5942` independently passes 29 methods + four prerequisite boundary probes. | Final integrated source digest, complete suite discovery/results, unchanged protected baselines and all known red cases green at that exact source. **Integration evidence pending**, not a new code defect. |
| B. Resource ownership/admission | Existing explicit adapter tests discharge many close/retry/stale-result cells; see the 13-owner matrix. POLAR admission correction `9c80e74` has separate transport/render review. Wallpaper `643accd` independently passes 40 methods + three extra probes; UDP `8a354c0` independently passes 19 methods. Thumbnail `a23999f` independently passes 32 methods plus a two-native-acquisition drain probe. | Constructor-vs-start coverage must be completed only for the identified matrix gaps, where still unestablished. **Bounded evidence gaps**. The named thumbnail executor submission/shutdown gap is discharged at `a23999f`. Wallpaper post-close open and UDP phantom-session counterexamples are fixed at named candidates; no remaining failure in those reproductions. |
| C. Window construction/activation | Existing startup ordering evidence retained; corrected finalization `fb271c8` independently passes 51 methods + 26 historical lookup/failure-prefix comparisons. | Earlier partial-constructor acquisitions are outside finalization scope. Enumerate acquisitions before the retained-abort boundary and fault each owned-service publication. **Evidence/ownership review gap**. Early audio QObject retention was subsequently reproduced; weak-callback correction `9dd5a80` is independently accepted: 47 audio tests and the same red/green native-QObject retention probe. This discharges the audio acquisition example, not the entire early-constructor ledger. |
| D. Input/authority | Existing emergency, focus-loss, hold-repeat and keyboard historical routing tests retained. Existing egress tests cover positive eligible output as well as individual false gates. | Retain explicit press/release/modal assertions in final-source regression. Shared differential collaborators do not prove their own runtime behavior. **Fixed candidate accepted:** drag ownership correction `8df72aa` passed independent review (22 methods and five pre-fix red failures reported by the input/review lanes). Movement candidate `ec80a3a` is independently accepted (30 tests + two successor/payload-failure probes), included in `8dbe624` but outside the earlier `93aa839` run. |
| E. State/projection ownership | Pure health/map/referee values and their historical/explicit assertions retained. Frozen referee globals now genuinely independent. | At this historical snapshot the field-group ledger was absent. It is now enumerated for all 242 declared fields; dynamic extensions and exhaustive cross-owner runtime semantics remain bounded coverage gaps. Do not require wholesale model replacement without a demonstrated conflicting writer or change reason. |
| F. Configuration/live order | Existing transaction, quiescence, preference draft, descriptor and audio recovery evidence retained; ordinary-error rollback and explicit failed-restoration retry remain separate. | Recheck relevant contracts in final integration; retain live UI/runtime adapters with their documented ordered-effect responsibility. **No new defect selected**. Crash/concurrent-writer atomicity is explicitly outside the implemented guarantee. |
| G. Presentation responsibility | Existing map values, exact rendering/cleanup contracts retained. Framebuffer `99c96e9` has separate transport review; specialized renderer/startup texture ownership already has enumerated fault tests. | Keep source-specific map/ClickGUI/economy/video adapters when cohesive; only split if a concrete independent policy/state owner is identified. **Native rendering qualification blocked externally**; broad host independence is not asserted. |
| H. Whole-inventory disposition | All 24 families have an explicit disposition below; ownership matrix and exact independent reports identify acceptance boundaries. | Integrating owner must attach final source/digest and complete aggregate, then reconcile any new failure. **Final evidence pending**. Zero SCCs and passing counts are not universal semantic coverage. |

The window eager-lookup regression was a genuine extraction defect; its
correction restores historical prefixes rather than weakening tests. Wallpaper
and UDP admission were genuine ownership/status defects. Shared metadata in the
referee test was an oracle defect, not incorrect production metadata. All have
named fixed candidates; none is represented here as permanently open merely
because its historical red evidence remains in the repository.

## Historical disposition of the original 24 families

“Retain” accepts the stated responsibility for the current scope; it does not
certify every runtime path or waive the named tests/native qualification.
“Review gap” identifies bounded work still needed before claiming complete
architecture close-out, not a demand for an automatic rewrite.

| Family | Disposition and rationale | Remaining actionable condition |
| --- | --- | --- |
| 1. Entry/window | **Accepted fixes + review gap:** activation/finalization ports and early audio weak ownership have real responsibilities; window remains composition root. | Audio example discharged at `9dd5a80`; remaining partial-constructor acquisition ledger/fault prefixes and final multiwindow/lifecycle tests. |
| 2. Domain/catalog | **Review gap:** stable catalog/profile/codec values are separated; RobotState intentionally aggregates runtime views. | Field-group producer/overwrite ledger and tests for any conflicting authoritative writes found. |
| 3. Economy | **Retain with boundary:** interaction owner/hold threshold and shared geometry are meaningful; broad view adapter remains. | Preserve selection/query, price/order, hold and render/hit contracts; no cosmetic builder split. |
| 4. Input/overlays | **Drag correction accepted:** ordered routing is meaningful, priority cannot become unordered dispatch. | Final-source regression for accepted `8df72aa` and `ec80a3a`; native mouse delivery remains external. |
| 5. Configuration | **Retain with boundary:** participants/drafts own distinct recovery/state responsibilities; adapter sequences UI/runtime effects. | Final transaction/retry/no-rearm tests; do not promise crash or multiprocess durability. |
| 6. Backend orchestration | **Retain with boundary:** coordinator, teardown and policies separate owned lifetimes from projection; manager remains composition/arbitration adapter. | Exact-source lifecycle/egress tests and resource matrix gaps; no manager-size target. |
| 7. Control/qualification | **Retain with safety invariants:** UI authority, worker freshness and egress checks serve different roles. | Positive and negative egress tests; physical neutral, remote priority and explicit operator qualification remain external. |
| 8. Referee protocol | **Retain with boundary:** annual decoding/projection and metadata are separate; historical partial-publication semantics characterized. | Frozen independent metadata and protocol bytes at final source; reference/annual compatibility prerequisites remain external. |
| 9. Robot protocol/transport | **Fixed candidate accepted within scope:** spec remains authoritative; POLAR admission correction supplements existing retained-native tests. | Final-source regression; physical acknowledgment is not inferred from queue/close status. |
| 10. Device egress | **Retain with boundary:** shared thread ownership does not replace per-adapter native/protocol ownership. | Existing DT7/CC/angle adapter tests and final integration; device qualification separate. |
| 11. Official transports/decoders | **Review gap:** UDP constructor defect has accepted correction; nested process/pipe/socket ownership already extensively tested. | The named MQTT/decoder constructor cells were subsequently discharged by the constructor matrix; retain native qualification and any separately identified untested prefixes. |
| 12. Audit/replay | **Retain:** bounded writer/accounting and replay isolation have specific responsibilities. | Final admission/loss/drain/intact tests; no claim that drained implies lossless. |
| 13. Vision/video UI | **Review gap:** camera/model workers retain native owners and reject stale results; UI coordinates control/combat. | Include cross-owner source/generation/authority in field ledger; native model/driver throughput external. |
| 14. Combat/audio | **Retain with accepted ownership fix:** confirmed-vs-inferred events, dedup and repeat-cue behavior are deliberate; weak signal listeners prevent unintended pool retention. | Final event/audio recovery tests; offscreen QObject lifetime passed, physical sound-device output remains external. |
| 15. Core rendering | **Retain with boundary:** resource/presenter/diagnostic owners meaningful; ordered source-specific passes remain. | Final failed-present/cache/hit-map and framebuffer tests; native context/pixels/cadence external. |
| 16. Specialized GL | **Retain:** named context handles and unit-specific cleanup are cohesive. | Enumerated partial-init/release/retry tests; actual driver state external. |
| 17. Map/status | **Retain with boundary:** pure geometry/radar/status observations extracted; painter/menu/cache adapter intentionally remains. | Preserve alliance/units/TTL/geometry and command gates; no line-count split. |
| 18. ClickGUI layout | **Retain with boundary:** layout, clipping and interaction registration are coupled by view purpose. | Preserve modal ownership, cache invalidation, language/scale and hit targets; host independence not claimed. |
| 19. Other HUD/presentation | **Retain:** style-specific artwork is cohesive even when procedurally large. | Existing view/geometry/source fixtures; native screenshot/performance evidence external. |
| 20. Startup media | **Fixed candidates accepted:** wallpaper admission retained through retirement; thumbnail active native work owned independently of executor join bookkeeping. | Final-source regression for `643accd` and `a23999f`; native decode external. |
| 21. Animation/frame planning | **Retain:** separate clocks/plans/controllers express timing responsibilities. | Preserve resume/no-backlog/pending tests; real presentation cadence external. |
| 22. Assets/locale/geometry | **Retain:** loaders/formatters/painter helpers are cohesive. | Locale/cache/shortcut contracts, installed fonts/assets and CJK bounds externally validated. |
| 23. Paths/preflight | **Retain:** source/portable roots and observational preflight are distinct. Explicit build interpreter accepted in source/Python scope. | Native Windows path-with-spaces/default/explicit interpreter and actual packaged launch. |
| 24. Historical/package facades | **Retain:** compatibility exports and historical source are intentional, counted and not complexity wins. | Keep API identity/frozen fixtures; update misleading migration comments only when relevant. |

## Historical finite next-stage list

1. **Seal the final combined regression:** exact revision/digest, discovery,
   API/static gates and complete profile; list FAIL, SKIP and INCOMPLETE separately.
   Stop and fix any new reproducible regression before declaring this checkpoint.
2. **Historical resource test gaps, now discharged for the named cells:** serial
   starter/reader, MQTT and decoder constructor rejection have subsequent matrix evidence. UDP constructor
   and the named thumbnail submit/late-admission/cancellation cases now have
   accepted corrections and evidence; do not count them as remaining gaps.
   Read production cleanup before adding fixes. Finish when each matrix cell has
   either a precise passing test or a documented inapplicable ownership contract.
3. **Bound early-window construction:** enumerate only resources acquired before
   existing abort ownership; inject each publication failure and retain/release
   already-acquired owners. Finish at a complete finite acquisition list.
4. **Record authoritative field groups:** official/POLAR/simulation/frontend,
   plus video/control/combat generation and freshness; identify producer and
   overwrite order. Add tests/fixes only for uncovered meaningful conflicts.
5. **Complete external qualification separately:** Windows/Qt/GL/audio/video,
   portable packaging and firmware/device field procedure. Record exact platform,
   device/firmware, source, prerequisites and results. Do not transform missing
   equipment into a code-quality defect or a mocked pass.

The drag-release defect (red `61e1cf8`) is now addressed by independently
accepted `8df72aa`, including cross-button ownership. An existing drag fixture
was updated at `0cf5b4` to use the real owner with its 82 assertions unchanged;
the reported independent review passes 30 methods and rejects two deliberately
bad finalizers. These are source-scoped reports from their respective lanes,
not tests rerun or added into this audit's totals.

Early audio-pool QObject retention became a confirmed constructor-failure defect
after traceback/normal-destruction controls. Weak-callback correction `9dd5a80`
is included in `93aa839` and independently accepted by the transport reviewer:
47 audio-discovery tests pass; the same retained-object probe exits 1 at baseline
`53e527e` and 0 at corrected source. Parentless pool, weak global registry and
traceback/mock-history controls isolate the signal reference cycle. Evidence
commit `93db40a`, `../evidence/clean-code-20261017/audio-independent/README.md`,
records exact commands and limits. This accepts real offscreen Qt object lifetime,
not audible output or physical sound-device/codec teardown.
Movement `ec80a3a` is independently accepted in report `92d4af9`: 30 tests
and two payload-conversion/successor-failure probes preserve the six-pixel
threshold, live successor writes, consume-before-command and initiating-button
ownership. No original fixture or public baseline was rewritten.

The `93aa839` aggregate is still running, not a pass, and cannot certify the
later movement edit. Final integrated source `8dbe624` needs its own run. Earlier aggregate
`dcd523c` predates these corrections and cannot certify them.

All reproduced code/oracle defects named in this report now have accepted
corrections. Remaining necessary work is the final combined run and its actual
failures, the specifically unmapped constructor cells, the remaining early-window
acquisition ledger, the authoritative field-group ledger, and external native
qualification. These evidence gaps do not imply new production defects or
justify an automatic rewrite. This report does not declare the whole project clean.

## Evidence

- `resource_owner_evidence_matrix_20261017.md`
- `window_finalization_independent_20261017.md`
- `wallpaper_admission_independent_20261017.md`
- `udp_constructor_independent_20261017.md`
- `thumbnail_admission_independent_20261017.md`
- `module_drag_state_independent_review_20261017.md` (review commit `92d4af9`)
- `build_interpreter_independent_20261017.md`
- `../evidence/clean-code-20261017/oracle-audit/`

Earlier family-level inventory and third-stage acceptance remain historical
source-scoped evidence, not a replacement for the final integrating report.
