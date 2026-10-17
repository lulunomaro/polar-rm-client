# Whole-client responsibility and acceptance inventory

## Scope and fixed source

Independent review begins at `71ebb0f570441c12eb0d15b0a992c9cc3e805aeb`.
Its production-source digest is
`b3780a24711a1529d57a37b8f09f9903bed6e7c1bf69f5eb25a2bead2eeff577`.
The static gate independently reproduces 190 modules, 3,211 functions,
36,668 function-body AST statements, 12,901 decisions, 562 local import edges,
zero initialization SCCs and two SCCs including deferred imports.

This is an inventory and family-level responsibility review, **not** a claim
that every statement has been semantically audited or every runtime path tested.
The [complete module inventory](whole_client_inventory_20261016.json) assigns all
190 measured modules to the 24 families below. Package facades and retained Tk
source remain counted. No module disappears merely because its current purpose
is historical, declarative, generated, a compatibility facade, or a small helper.

Counts locate review work. They do not establish a defect, justify a rewrite,
measure test coverage, or define a clean-code score. In particular, the largest
single function is procedural icon artwork (`crafted_item`); that fact alone is
not a reason to split it into more functions.

## Whole-client responsibility map

In this table, an acceptance condition is the evidence required for a future
change in that family. It is not a blanket statement that the existing family
is defective or that all listed evidence has already been provided.

| Family and main production boundary | Observed remaining responsibility or coupling | Acceptance required for a meaningful change |
| --- | --- | --- |
| Entry and window: `app`, `polar_app`, `polar/app_entry`, `window_assembly`, `window_lifecycle`, shortcuts | Constructor still assembles 261 direct self names and has 277 statements; current factories are typed, but overall activation/order remains in the window. Deferred compatibility entry SCC is real. | Explicit window factory and application ownership; startup state has its own reason to change; actual reused QApplication windows live/close independently; failure prefixes, activation order, async startup/close and frozen caller surface preserved. |
| Domain/catalog: `model`, `module_catalog`, `module_settings`, `scenarios`, profiles/roles/modes, `state_overlay` | `RobotState` combines authoritative telemetry, simulation and frontend fields. Model/scenario defaults still depend on referee runtime registry. Catalog extraction already detaches environment choices and copies mutable options. | Domain imports execute without Qt/UI/backend construction; annual metadata is distinct from runtime factories; authoritative field ownership/types and overlay order remain fixed; defaults, IDs/order and independent registry instances remain characterized. |
| Economy: `economy`, economy mixin, workspace/layout/presentation/glass, CS2 grid | Two builders remain 198/179 statements. `economy_selected_offer()` both queries and mutates selection/quantity. Geometry extraction alone does not separate interaction state from painting. | Separate read-only selection from explicit reconciliation/confirmation commands; shared detached view input; repeated queries/paints cannot silently change authority or selection; preserve offer order/price/quantity, hold deadlines, render/hit geometry and any compatibility wrapper. |
| Input/overlays: input mixin, pointer owner, text editing, escape menu, chat/wheel, coordinator, keybinds | `keyPressEvent` retains 171 statements/100 decisions and an ordered modal/control chain. Action table handlers still receive the complete window. Pointer and text owners are genuine narrower boundaries, not a complete routing separation. | A routing decision explicitly identifies the consumer and press/release owner. Test E-stop first, pending lifecycle, every overlapping modal priority, autorepeat, focus loss, owned release after dismissal, and no control leakage. Do not replace order with unordered handler lookup. |
| Configuration: config store, configuration package, persistence/config-action mixins, settings services/dialogs, audio/video/crosshair preferences | Transactions/participants are explicit; window adapters still combine profile identity, asynchronous scenario completion, UI projection, local files and error presentation. `cs2_crosshair` embeds editor construction with parsing/domain logic. | Preserve stable setting IDs, migrated-input preflight before mutation, exact rollback values/options/binds, generation-matched async completion, neutral/no re-arm, dirty-close decisions and save identity. Single-writer ordinary-error rollback must not be relabeled crash/multiprocess atomicity. |
| Backend orchestration: manager, lifecycle/controller, teardown plan, resource close, coordinator | Manager has 115 functions/1,368 statements and 34 local dependencies. Extracted policies/controller owners are real, but registry capture, device arbitration, projection and publish adapters remain concentrated. | Immutable inputs for decisions; exact command/effect order including exception prefixes; single poller/generation owner; final resource release, audit drain, audit integrity and operator success remain separate states. No second owner or callback-on-worker introduced. |
| Control/qualification: control bridge, safety/readiness/evidence, pure policies/settings, emergency action, spin/lob | Safety decisions span UI-owned authorization, worker snapshots, firmware evidence and explicit operator transitions. These are not reducible to a boolean `enabled`. | Recheck at actual egress; preserved fire=0, drive-key mask, physical remote priority, stale input rejection, one gimbal owner and explicit re-arm. Tests must include every gate independently false and positive eligible output, not only fail-closed examples. |
| Referee protocol: annual codecs/adapters/registry/wire/validation, general protocol/selftest | `_apply_one` still has 174 statements and 81 literal state paths; registry/profile factory/economy value SCC crosses domain and implementation assembly. | Separate metadata/values from runtime factories without a TYPE_CHECKING-only cosmetic fix; annual dispatch, import discoverability, malformed/unknown profile errors, partial writes, JSON shape and command bytes remain characterized. Pure projection should not consume queues or authorize output. |
| Robot protocol/transport: POLAR spec/link/bridge/transport/selftest | Generic spec and pure policy exist, but transport owns connect/read/write/neutral/stop concurrency and bridge owns cross-field derived state. | One authoritative protocol spec and field contract; finite admission/close interleavings; no replacement while old native owner is retained; never count queued neutral as firmware-confirmed zero. Physical validation stays separate. |
| Device egress: DT7/CC workers and links, angle relay, serial discovery/stream | Real resource lifetimes, bounded latest-intent mailboxes and UI observer delivery coexist. Prior 11-site admission audit explicitly excluded these workers. An angle admission defect is reproduced below. | Rejected constructor/start, genuinely admitted late exception, slow open/write/close, retained close failure, explicit retry and port handoff need separate tests. No permissive join or hidden return-success fallback. |
| Official transports/decoders: MQTT, serial, UDP/video, Paho cleanup, native/source policies | Several subclasses share historical transport implementations. Existing admission audit is finite and source-bound, not universal proof. | Real production object plus fake native boundary; epoch revocation before stale traffic; late native handles remain retained; rejected admission vs completed cleanup distinguished; nested process/pipe/socket owners independently released or explicitly retained. |
| Audit/replay: field logger, replay, native viewer telemetry | Bounded queue and loss counters are explicit. Logger constructor/late-start failure still breaks truthfulness at this snapshot. Replay must remain publish-free. | Accepted, written, pending, dropped, error, drained and intact cannot collapse. Include constructor rejection, start rejection, live/finished/next-owner late error, bounded queue under stalled I/O, cumulative error visibility, and no duplicate replay effects. |
| Vision/video UI: vision models/camera/bridge, local inference, gimbal bridge, calibration, video mixin/recovery/motion | Video mixin also owns deploy console, spin/lob guards, vision-control submission and combat feedback. Model/worker boundaries exist but UI data flow remains broad. | Detached latest results with source/generation/freshness; no worker Qt mutation; cleanup failure cannot be mistaken for no data; disabling/re-enabling cannot revive stale aim; local model/driver qualification and native permanent-block limits remain explicit. |
| Combat/audio: event feed/detection, HUD values, kill FX/audio, UI audio, CS2 feedback | Event protobuf decoding and vision track heuristics share `combat_events`; rendering styles and sound selection share `kill_fx`. Repeated-cue/audio lifetime fixes already have evidence. | Official confirmed vs vision inferred events stay distinct; event dedup/reset/track retirement are deterministic; repeated identical sound cues replay; failed decode/output/cache and stop/close are observable without fabricated device success. |
| Core rendering: GPU renderer/compositor/resources, cache/diagnostics, fullscreen, frame/presenter/plan, glass | Cached-frame method remains 167 statements/69 decisions, with state commits mixed into ordered passes. Presenter/diagnostics/resource owners are already explicit. | Detached frame inputs and named paint ports; exact pass order; False and exceptions both reject; each cleanup attempted; original cause plus cleanup failures retained; no generation/cache/hit-map commit before successful present. |
| Specialized GL: startup/video, vision wire, island icon, wheel | Separate renderers own context objects and texture-unit cleanup. Prior fixes cover enumerated failure points, not all driver state. | Partial native initialization, bind=False, texture upload replacement, restore/destroy, unit-specific release and repeated initialization need strict inert-state tests plus separate native GL qualification. |
| Map/status: tactical map renderer | 81 functions/1,520 statements include coordinate projection, map-click command preparation, event menus, telemetry projection, image caches, animation and painting. Historical migration header still describes 55 moved methods and old API qualname constraints. | Detach pure projection/status rows and map interaction commands before rendering ownership work; preserve alliance mirroring, units/TTL, corner masks, selection/command gates, output geometry and localization. Update stale migration comments when relevant; do not blindly split based on size. |
| ClickGUI layout: clickgui builders, Jello renderer/widgets | Layout, visual geometry, interaction registration and open/close policy remain shared-window operations; the frame adapter captures registrations but does not make builders host-independent. | Immutable view input plus explicit draw/interaction results; preserve modal ownership, clipping, animation and language/scale invalidation; unchanged CPU/GPU visual fixtures or individually justified defect corrections. |
| Other HUD/presentation: remaining renderer mixins, Minecraft/hero/student/top bar, armor geometry | Many style-specific methods legitimately have drawing complexity; several mixins still read arbitrary window services. Startup renderer also contains navigation/autostart decisions. | Extract independent policy/data ownership only when it has a specific change reason. Keep hit targets, text/font/scale, alpha/animation, status source and visuals characterized; native screenshot/performance claims need measured native evidence. |
| Startup media: wallpaper/package renderer, thumbnail decoder | File discovery/import, package extraction, frame decode, frame snapshots and painting share a large module. Old decode owners are retained across failed release; worker admission outside prior audit remains a review target. | Import/path validation, no duplicate native owner, cancelled/late generation, repeated selection, close failures and thumbnail lifetime; no directory scans or blocking decode newly added to paint. |
| Animation/frame planning: animation bank, tick/presentation controllers, frame telemetry, primitives/layout | Three clocks and separated controllers are established; adapters still perform ordered window effects. Timing metrics are local work measurements. | Preserve 75 Hz control vs 60 Hz logic intent, skipped/pending behavior, resume without control backlog, monotonic deadlines, and separate working-time from actual presentation/driver cadence. |
| Assets/locale/geometry: asset loaders, fonts, painter helpers, locale modules, text metrics, emergency shortcut | Most are cohesive adapters/pure formatting. Dynamic language and CJK metrics interact with caches; emergency shortcut has ordered safety effects. | Explicit locale/settings input, preserved fallback and invalidation, CJK bounds, original filesystem/error semantics; emergency stop must precede fallible input cleanup on every entry path. |
| Paths/preflight: runtime paths and preflight | Source/portable roots and template seeding are distinct; preflight is observational. | No developer configuration overwrite or unauthorized hardware opening; source/portable paths and writable target isolation characterized; preflight must not seed config or claim packaging qualification. |
| Historical/package facades: retained Tk and package exports | Historical source stays for compatibility. Several old module docstrings describe pure moves or obsolete freeze methodology. | Preserve explicit imports/calling surface and prove current production dispatch separately. Moving historical code or changing classification earns no complexity-reduction credit. |

## Priority findings with concrete counterexamples

Two previously unaudited admission families were reproduced on the fixed source:

1. `ControllerAngleRelay.configure` assigns `_owned_config` and `_thread` before
   successful worker admission. Rejected `Thread.start()` leaves a thread with
   `ident=None`. `close_controller_angles` then fails on every attempt with
   `cannot join thread before it is started`, preserving a phantom port
   reservation although no native handle was opened. Constructor rejection also
   leaves a phantom reservation until a later cleanup operation.
2. `RotatingJsonlLogger.emit` increments acceptance/queues the record before
   constructing its lazy writer. Rejected construction escapes with
   `accepted=1, written=0, pending=1, errors=0`; `close(drain=True)` then returns
   true and sets closed despite the unwritten record. Separately, a wrapper that
   admits a real thread and then raises causes the old handler to drop the live
   owner and reset in-flight counters. A later `AuditCloseStatus.capture` can
   report drained while the writer is still in `_write_line`.

The latter late-wrapper case is defensive fault injection, not a claim that
ordinary CPython `Thread.start()` normally raises after admission. Standard
constructor/start rejection and admitted-owner retention are distinct tests.

Reproduction tests were committed first as `8ff426f`: eight methods run on the
unchanged production base, with three passing, four failing and one error. The
narrow production fix is `70c410c`; it changes only angle admission publication
and logger admission accounting. All eight methods pass afterward, including
an already finished writer and a successor writer started before the old error
is reported. Fifty complete repetitions passed 400 methods; the combined
nine-suite focused run passed 76 methods. No old fixture, join, timeout, API
baseline or production safety gate was relaxed.

These authored fixes subsequently received independent PASS from a different
reviewer at exact source `70c410c`. That reviewer independently reproduced the
original four failures/one error, ran 80 focused/neighbor methods plus 76
lifecycle methods, and added five adversarial probes. Test commit `8b492a4` and
evidence commit `86ce798` preserve those probes and seven hashed logs under
`docs/evidence/clean-code-20261016/admission-independent/`. The reviewer changed
no production source; static gate and diff check also passed. Separate authored
and independent counts remain separate, with no aggregate or native claim.

A subsequent independent audit found a third admission defect in
`serial_ports.port_snapshot`: rejected thread construction/start leaves
`_port_refresh_running=True` although no worker exists. Later snapshots never
retry, including after cache invalidation. Test-only checkpoint `706b136`
reproduces constructor/start rejection as two failures and preserves genuinely
admitted late-error ownership as one passing case. No device scan or native
handle is used. This checkpoint is intentionally red and must not be integrated
as passing evidence without a separately reviewed correction. A different author
subsequently corrected the defect at `633e48d`, after replaying the exact red
fixture as `fd625fd`. Independent review passed 77 focused/neighbor methods and
100 repetitions of all nine admission methods (900 executions), plus the static
gate and full correction-range diff check. All five original fixture functions
remain exact. A short admission lock never spans construction/start/scan; rollback
uses never-admitted identity, preserving live/completed/successor ownership and
cache invalidation evidence. This closes the reproduced serial admission defect,
without qualifying native enumeration or integrated release behavior.

## Fixed-candidate review checkpoints

- `1bf2871` (Qt application boundary, preceding characterization `ba30b26`):
  independently inspected and tested in an exact detached worktree. Entry,
  real-window construction, source classification and API-contract suites passed
  78 methods; actual `api_freeze_test.py` and static quality gate also passed.
  Launch effects/order, exception identity, reused application retention and
  one-way compatibility import are preserved. This accepts that narrow entry
  boundary, not the remaining constructor or desktop OpenGL behavior.
- `7dda9f2` (referee profile/value boundary, preceding fixture `886ad5e`):
  independently passed 69 focused methods, actual API freeze and static gate.
  Pure profile metadata is the common model/scenario/registry authority; shared
  economy values remove the annual-policy-to-transaction edge. Real RobotState
  runtime typing remains intact. Import blockers execute actual callers. The
  fixture contains **90**, not 174, annual offer cases; documentation correction
  `3fa6db7` is part of the reviewed record. Re-exported class definition modules
  truthfully change while historical import/pickle globals remain resolvable.
- `8c4a9d9` (selection query/command boundary, characterization `e64d6e6`):
  independently passed 75 focused methods. The pure selection query does not
  mutate; the explicitly named reconciliation command retains old mutation
  ordering and all seven migrated callers use it. The historical query name
  remains a mutating compatibility wrapper. This alone does not make rendering
  free of reconciliation effects.
- `52d23ed` (startup navigation owner and named lazy package exports):
  independently passed 160 focused methods, actual API freeze and static gate.
  Seven explicit fields and navigation decisions belong to a Qt-free owner;
  window adapters preserve partial-state/effect order on failure. Lazy exports
  preserve identities/import-star while allowing actual Qt-free imports. This
  is a navigation seam, not a claim that the whole constructor is separated.
- `483f0dc` (incremental warmup queue, characterization `2d5ea31`):
  independently passed 63 focused methods, actual API freeze and static gate.
  Queue/timing ownership has explicit upload/context/clock/error ports. Window
  admission and timer ownership remain unchanged. Snapshot-before-gates behavior,
  one-job turns, soft budget, real elapsed timing and failure truncation are
  retained. Existing warmup assertions were not weakened.
- `a65c982` (cached overlay scheduling, frozen reference `f9c4207`):
  independently passed 139 focused methods, actual API freeze and static gate.
  The frozen cached-frame AST exactly matches the original fixed source. The
  executor receives immutable viewport/scene/cache values and nine named draw
  ports; it receives no host/proxy. Cache acceptance requires matching storage
  generation. Source-specific passes, final present/economy commit and legacy
  startup geometry publication remain window responsibilities.
  Test-only `494d710` independently passed three methods, including 88 real
  window cold/warm executions over eleven scene families and two size/DPR pairs,
  command/hit/cache/glass/CPU-raster equality, rejection/retry and post-present
  restore. `2a41d53` independently passed twelve boundary methods for scalar
  overflow, boolean ownership and rejection before stale-generation drawing.
  Raster substitutions do not qualify desktop GPU output or performance.
- `b70f71e` (economy drawing content): independently passed 53 focused methods,
  actual API freeze and static gate. Both copied scene ASTs match the original
  source. Evidence includes the retained 84 digest cases, 72 paired complete
  scenes and 2,344 paired injected-failure cases; those are different evidence
  sets and are not summed into a coverage percentage. Actual-window QImage,
  command and hotspot comparisons also pass. Explicit value/drawing/category
  ports remove arbitrary host access; adapters retain reconciliation, scroll,
  animation and transaction authority. The module still imports the economy
  transaction-owner module for shared values/labels, a remaining dependency
  edge rather than complete transaction-module independence at that checkpoint.
  Follow-on `f3e0d04` closes that edge: 145 independent UI/contract methods plus
  35 domain methods, actual API freeze and static gate pass. Three shared-label
  characterization methods also independently pass on pre-change `d86f344`.
  Values and canonical label dictionaries now share the dependency-free value
  owner; old dictionary aliases/in-place mutation and CS2 derived-copy semantics
  remain unchanged. Forbidden-import tests execute actual value-driven callers.
  The alignment-only diff is whitespace-clean; two existing hash-pinned fixture
  EOF blank lines remain in the full range and were not rewritten for cosmetics.
- `25f0fc6` (verification isolation, source fingerprints and wheel lock):
  independently passed 68 targeted methods. Before/after source fingerprints
  reject persistent scoped source/contract drift; they do not establish atomic
  snapshots or detect a transient change restored before the final capture.
  App-root/reference/XDG isolation is real-path tested. The exact Linux CPython
  3.12 official-wheel lock is inspected; installation evidence belongs to the
  author's separate empty-venv run, not this review. Follow-on `b2c67db` and
  `8080a39` independently passed 65 registry/provenance/source methods: inherited
  autostart/debug data roots cannot escape runner isolation, and real unittest
  expected failures remain INCOMPLETE. No aggregate runner lock was acquired.
- `1a98b17` (report publication): independently passed 69 registry/provenance/
  source methods. Atomic publication, unique attempt IDs and initial INCOMPLETE
  checkpoints prevent a prior PASS from masquerading as an interrupted,
  firmware-rejected or lock-rejected selected execution. Review reproduced a
  documentation overclaim: argument/timeout/selector/discovery errors occur
  before checkpoint creation. Correction `a213378` explicitly states this limit
  and its five publication methods independently pass. Consumers must match
  exit status and the new run ID; a report filename alone proves nothing.
- `b97b2ce` (economy activation/hold decisions): independently passed 35 focused
  methods including frozen interaction/failure prefixes and actual-window
  commands. Immutable decisions retain exact timing boundaries and do not
  authorize transport: successful intent still passes submit/bridge rechecks.
  Follow-on `dafe885` independently passed 36 methods, including 32 legacy-host
  observation cases and their interrupted prefixes. Named lazy clock/key/expiry
  queries preserve the original short-circuit reads; this is compatibility-order
  hardening, not a demonstrated real-window defect.
- `248a89c` (camera admission, preceding contract `10a10a0`): independently
  passed 142 focused methods, actual API freeze and static gate. The owner
  receives a typed worker plus bind/discard effects; registry/environment/source
  gates remain in the adapter. Reentrant binding, no-wait revocation, dead-worker
  retry and failed-start configuration publication are preserved. Existing
  capture ownership, 80/240 ms Qt heartbeat and UI-state regressions pass. This
  accepts camera admission only, not all video/vision responsibilities.
- `661daed` (vision selection/revocation, preceding contracts `b7786a2` and
  `de31344`): independently passed 173 focused methods, actual API freeze and
  static gate. All sixteen original contract function ASTs are unchanged.
  The typed owner holds requested-source state, worker snapshots, stop planning
  and independent revocation/error collection. Configure/start expressions stay
  in the adapter so Python method binding still precedes reentrant numeric
  setting reads. The five fixed aliases preserve the same workers and flags;
  this accepts selection/revocation, not all activation or video orchestration.
- `9d3a849` (profile-save state, preceding contract `77b4c92`): independently
  passed 247 focused methods, actual API freeze and static gate. All thirty-three
  original contract function ASTs are unchanged (29 tests plus helpers). The
  owner holds accepted identity/generation, draft and pending-save ownership,
  deadline, manual-close retry and notification latch. Identity/clock queries
  retain interrupted publication; registry/storage/messages remain ordered
  adapter effects. Synthetic nested-write re-entry behavior is preserved and
  is not claimed as an ordinary-user defect or stronger file durability.
  Whitespace-only follow-up `15c8608` resolves the new EOF blank line; the full
  baseline-to-candidate diff check is clean.
- `f542fcc` (normal-video provenance/timeline, preceding contract `5700c6c`):
  independently passed 212 focused methods, two existing real-window source
  smoke functions, actual API freeze, static gate and full-range diff check.
  All 35 original contract functions are unchanged (30 tests plus helpers);
  all 30 baseline tests also independently pass on the pre-extraction source.
  Five owned values centralize source/newness/freshness/readiness/clear rules.
  Native lookup, Qt image handles, inference and cache effects retain their
  separate observation/publication points. NaN, empty-source, reentrant and
  partial-failure behavior remains characterized rather than silently corrected.

Two subsequent author findings were independently reproduced before correction:
`88ab1fd` has two expected failures for post-launch-error window ownership and
pending activation; `e7b30f6` has five failing command rejection cases, including
real renderer-caught faults that still allow frame presentation. These red
results are intentional defect evidence, separate from extraction equivalence.

Launch correction `610180e` subsequently passed 51 independent methods, actual
API freeze and static gate. The one-method abort port retains the constructed
owner and revokes pending activation through existing asynchronous final
shutdown. Retention attribute/signal failures do not prevent abort; cleanup and
diagnostic failures preserve the primary exception. Old entry/API fixtures are
unchanged. Constructor failure before returning an object remains outside the
contract; without Qt event processing, native close is retained/incomplete
rather than falsely reported complete.

A further final-constructor timer admission defect at `1173a2e` was independently
reproduced: a real timer admitted before the injected exception continued to
activate mocked MQTT after construction failed, with no named timer, retained
owner or closing state. Narrow correction `cd80ec7` passed 132 independent
methods, actual API freeze and static gate. The fully assembled window now
publishes the inert timer before its final start and uses the same retained
abort boundary. Actual event processing keeps delayed native cleanup retained
and prevents runtime activation. Persistently unavailable cleanup timing remains
explicitly retained/incomplete. This does not extend abort support to earlier
partially constructed windows.

Command rejection correction through `318ec2e` passed 61 independent focused
methods, actual API freeze and static gate. HUD/icon/wheel/label rejection now
prevents cache/present/restore publication, and registered child/vision rejection
survives a later framebuffer release rejection. The frozen original defect is
retained. A raw generic paint callback returning False without first registering
a cause can still have cleanup rejection become its recorded reason; this
pre-existing diagnostic limitation is separate from child-command acceptance.

Collection lifetime correction `615aed5` passed 92 independent focused methods,
actual API freeze and static gate. The initial red fixture had one unreached
wheel injection because its inert FBO lacked `texture()`. Separate fixture
correction `df8565d` was independently rerun: exactly ten interrupted stages plus
combined unwind fail (eleven failures), while wheel teardown already passes.
Session-owned collection discard now turns off every recording flag before
fallible cleanup, preserves original and cleanup faults independently, and lets
actual raster fallback paint text again. The frozen missing-text defect remains
an explicit historical counterexample. No native GPU qualification is implied.

Final scalar admission polish `ba96e42` independently passed twelve boundary
methods, including Qt logical dimensions beyond the signed-int limit with DPR
below one and oversized integer timestamps rejected as ValueError. Evidence-only
tip `0fea3bb` has 25 independently checked artifact hashes and matching result-log
hashes. Its author's 252-method clean-source run is accurately reported, but is
separate from the independent 92-method correction run above. The frame report
explicitly discloses whole-tree growth and retained source-specific adapters.

Each count above belongs to its own exact source tree and selected command.
They are independent candidate checks, not a combined integration result.

## Current disposition after the first integrated offline run

This section updates the original snapshot above without rewriting its measured
counts or treating every listed concern as a defect. At fixed integration
`c4d3bcf97ef43056bf316bbec593802652120878` (tree
`7133ab90ca589f2b9afaf6e141186b42f5b6d6c1`), the separately owned complete offline
run reports **256 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL**, with unchanged source
fingerprints before/after (evidence commit `cdaace6`). Its static report measures
204 modules, 3,342 functions, 36,957 statements and 12,999 decisions: whole-tree
size increased, although both measured import graphs now have zero cycles.
This reviewer inspected that report, but did not run the aggregate or acquire
its lock. The JSON inventory also maps all 204 integrated modules to the same
24 families, with per-module source hashes and counts; its original 190-module
snapshot remains intact. The two accepted but not yet integrated map value
modules are listed separately rather than silently added to c4d3bcf totals. Serial correction `633e48d`, the later metric/cycle guard candidates
and the new map candidate are not included in that fixed integration result.

Each family below distinguishes an accepted or already coherent boundary from
remaining semantic/coupling work and from evidence that offline mocks cannot
supply. An adequate boundary does not imply every path in the family is proven.
Only the two explicitly labelled new counterexamples are recommended as new
independent defect lanes; the map lane is already active.

| Family | Adequate boundary / no size-driven change needed | Substantive remaining scope or explicit limit | Native or external qualification still absent |
| --- | --- | --- | --- |
| Entry and window | Qt application ownership, navigation, warmup, camera/vision selection, profile save and video timeline have explicit reviewed owners. Do not split constructor defaults merely to shorten it. | Earlier partial-constructor cleanup remains unsupported; several source-specific activation/effect adapters legitimately stay in the window. Final timer and post-launch abort defects are corrected. | Packaged fresh/reused desktop launch, real event-loop/native cleanup and platform window behavior. |
| Domain/catalog | Pure referee metadata, shared economy values, catalog/settings and scenario defaults no longer require runtime factories. | RobotState still contains telemetry, simulation and frontend state; no new demonstrated field-authority defect justifies a broad model rewrite. | Authoritative field behavior against physical referee/robot sources. |
| Economy | Pure selection, explicit reconciliation, immutable intent and named content ports are accepted; label/value dependencies are detached. | Adapters still own reconciliation, animation, scroll, hotspots and transport safety. Student-reload/CS2 host coupling remains, with no new counterexample. | Physical command acceptance, device gates, native rendering/input timing. |
| Input/overlays | Pointer/text ownership and characterized modal order are substantive existing boundaries; function length alone does not justify a routing rewrite. | Handlers still receive the window and modal priority crosses services. Any extraction needs explicit press/release consumer ownership and unchanged ordered safety effects. | Real focus loss, OS input delivery and physical emergency-stop behavior. |
| Configuration | Migration/validation, participants/rollback and accepted profile-save state have explicit boundaries. | Storage/registry/UI effect ordering remains adapter-owned; synchronous nested-save behavior is preserved. Crash/multiprocess durability and the combined crosshair editor/parser are separate future scopes. | Power-loss durability, actual filesystem/platform failure behavior and packaged configuration paths. |
| Backend orchestration | Lifecycle polling, teardown and policy owners already separate resource release, audit integrity and operator success. No generic manager split is proposed. | New bounded counterexample: MQTT malformed port fallback reconverts the invalid value and raises; fractional values can diverge between registry and backend. Mutable-registry admission scope only. | Real network reconnects, physical arbitration and firmware-confirmed shutdown. |
| Control/qualification | Explicit pure safety decisions and actual egress rechecks are appropriate; retain distinct readiness/authorization/evidence states. | Cross-layer evidence assembly remains intentional. No new proved defect here; require positive qualified output as well as fail-closed tests for any change. | Physical remote priority, firmware evidence and device qualification remain prerequisites. |
| Referee protocol | Metadata/factory and annual-policy/value SCCs are removed with real caller/import-blocker evidence. Wire codecs and adapters remain cohesive. | Large field projection retains many literal paths; size alone is not grounds to split annual dispatch or change partial-write semantics. | Official reference/firmware interoperability and field traffic. |
| Robot protocol/transport | Single protocol spec, pure policy and retained native-resource ownership are established boundaries. | Connect/read/write/neutral concurrency remains transport work. No new demonstrated counterexample in this review; preserve queue-versus-confirmation distinction. | Physical serial traffic, confirmed neutral and permanently blocked driver behavior. |
| Device egress | Angle relay and serial discovery rejected-admission defects have reviewed narrow fixes. Latest-intent mailboxes and retained close are meaningful owners. | New defensive counterexample: DT7/CC constructors whose real Thread.start admits then raises leave a live unsealed owner unavailable to the caller. Not a claim about normal CPython start behavior. | Physical CC/DT7 ports, handoff and native stalled open/write/close. |
| Official transports/decoders | Existing source/native policies and finite owner-admission audits provide bounded seams. | Inherited transport implementations still have specialized lifecycle paths; prior audits are source-bound, not a universal concurrency proof. No new broad rewrite selected. | Real MQTT/UDP/video/serial peers and native process/pipe/socket faults. |
| Audit/replay | Logger acceptance/accounting and live/completed/successor ownership fixes passed independent adversarial review. Replay remains publish-free. | No new defect after that correction. Keep accepted/written/pending/dropped/error/drained/intact separate rather than adding one success flag. | Actual storage faults/durability and sustained field logging throughput. |
| Vision/video UI | Camera activation, vision selection/revocation and normal-frame timeline now own specific policies and state. | Video mixin still joins deploy console, spin/lob, control submission and combat feedback. Native configure/start observation points remain deliberate adapters, not falsely claimed extracted. | Camera/inference drivers, model qualification, native stalls and real aim freshness. |
| Combat/audio | Event deduplication, explicit confirmed-versus-inferred events and repeated-cue ownership already have evidence. | Decoder/track heuristics and style/sound selection remain combined in some modules; no fresh counterexample makes a size-only split valuable. | Audio hardware/cache failures, actual playback and physical combat source correspondence. |
| Core rendering | Immutable frame/layer inputs and named paint ports are accepted; command rejection, first registered cause and collection unwind defects are corrected. | Window retains source passes, final present/restore and legacy startup geometry publication. Raw unregistered callback failure can retain a later cleanup reason; do not overstate first-cause coverage. | Desktop native OpenGL output, driver state, DPI and measured frame cadence/performance. |
| Specialized GL | Renderer-specific resource/context ownership is cohesive; existing finite failure-point tests are useful. | No claim that mocked bind/upload/release checks exhaust driver behavior. Keep resource responsibilities rather than splitting procedural shader/artwork code. | Native contexts, texture units, upload/recreation and cross-platform driver behavior. |
| Map/status | Geometry/projection and telemetry-value seams are independently accepted at 184db55; they are outside the named c4d3bcf integration snapshot. | Tactical map still mixes telemetry observations, interactions, caches and paint. The active map lane is substantive; preserve alliance mirroring, TTL, units and command gates. | Native map presentation, real telemetry rate and physical command coordinates. |
| ClickGUI layout | Frame command/registration snapshots are accepted but do not make builders host-independent. | Live window geometry/services, modal policy and interaction registration remain hidden coupling. A future value-input/result seam needs pixel/hit/priority evidence, not helper(window). | Native font/DPI/animation behavior, real input and packaged assets. |
| Other HUD/presentation | Style-specific drawing complexity is often cohesive; startup navigation policy has been extracted. | Several remaining mixins read arbitrary window services. Extract only identifiable data/policy ownership with frozen hit/text/status/animation behavior. | Desktop screenshots, native font metrics, GPU output and performance. |
| Startup media | Existing retained decode ownership and frame snapshots are meaningful. | Discovery/import/package extraction/decode/paint remain mixed; admission/late-generation and close limits deserve bounded review before any larger split. | Actual media codecs, package files, blocked native decode and thumbnail/resource lifetime. |
| Animation/frame planning | Distinct clocks and control/logic/presentation owners are appropriate; preserve ordered adapter effects. | No new proved defect. Local work-time metrics must not be presented as native presentation cadence or end-to-end latency. | Real scheduling, display cadence, resume behavior and native timing under load. |
| Assets/locale/geometry | Most loaders/formatters/geometry adapters are cohesive; procedural artwork does not need line-count decomposition. | Language/font-dependent cache invalidation and ordered emergency effects remain acceptance obligations, not new defect claims. | Platform fonts, CJK bounds, asset packaging and native shortcut delivery. |
| Paths/preflight | Observational preflight and explicit source/portable roots are sound boundaries; runner root/environment isolation is now real-path tested. | No new source defect selected. Preflight must remain observational and cannot establish packaging or device qualification. | Installed portable/source deployment permissions, platform paths and actual packaging. |
| Historical/package facades | Named lazy package exports preserve import identity while allowing pure owners to import without Qt. Historical facades remain intentionally counted. | Old migration comments can mislead and should be updated with relevant changes; moving retained code earns no reduction credit. | Legacy execution paths are not current production coverage; platform packaging/import availability remains separate. |

## Later independently accepted guards and map values

After the fixed integration report above, quality candidates `528b5b1` →
`a603171` pass 40 independent methods including 27 metric-limit negative
controls, the real static gate and full-range diff check. `9fb0cb6` passes
52 independent methods including 84 pure-root import cases and the same
27 metric controls. The three selected caps decrease; unchanged source/lint/API
baselines and replacement/whole-tree growth accounting remain explicit.
The full-import zero-cycle guard is a new enforcement rule, not a claim that
the earlier baseline already enforced deferred imports. Pure-root backend/Qt/Tk
restriction applies only to referee_profiles/economy_values.

Map candidates `db5a4df` → `9a585e2`, with test-only constant isolation `93981b8`,
pass 73 independent methods plus the actual API freeze and static gate.
All eleven pre-extraction methods independently pass at `add1fb6`; the frozen
module is the exact original source. Explicit immutable geometry, radar,
penalty and timeline observations now have no Qt/model/backend/window import
requirement. The broad map painter, status UI, menu and cache responsibilities
remain. Docs-only `184db55` corrects a committed-log whitespace/diff-check scope
mismatch; the complete baseline-range diff then passes without changing
production or frozen source. These are candidate-specific checks, not part of
the earlier c4d3bcf aggregate. Logs/hashes are retained under
`docs/evidence/clean-code-20261016/independent-review-wave2/`.

## Review rules for the remaining work

- Keep the existing API manifest and historical source/visual/behavior fixtures.
  New characterization must precede its corresponding production replacement.
- Review full caller effects and failure prefixes, not only equal return values.
  A field moved behind `helper(window)` is still shared mutable window state.
- Include replacement implementations in whole-tree counts and disclose growth.
  Removing an import cycle by changing a type annotation alone is not proof of
  separating runtime responsibilities.
- Parent integration needs a new fixed-source aggregate. Passing suites from
  different commits cannot be added together into one imaginary whole-tree run.
- Existing firmware/reference prerequisites, native Windows/desktop GPU/device
  acceptance and packaging remain open. The inventory does not turn the prior
  12 SKIP/4 INCOMPLETE checks or profile-external qualification gaps into passes.

## Bounded indirect dependency guard review (base `fa2c2b8`)

The static gate's two protected pure-domain roots previously rejected only
**direct** backend/Qt/Tk imports. A new local helper could import these dependencies
and be imported by `referee_profiles` or `economy_values` without violating the
gate. This is a demonstrated tooling blind spot, not evidence that those two
production modules currently have that coupling.

The test-first checkpoint `afbdb95` reproduces 38 failing assertions across
36 root/forbidden-target/import-form/scope combinations, one multi-hop path and
one helper-to-UI path. The narrow correction follows resolved local import edges
from each protected root, including deferred and type-only edges, and reports
a deterministic shortest route to each offending module. The UI boundary also
applies along that route. Unreachable GUI imports do not contaminate a pure
root, and a type-only cycle terminates without becoming a runtime-cycle claim.
A separate CLI negative control establishes that the indirect mutation returns
failure while leaving its baseline byte-for-byte unchanged.

The corrected candidate passes 47 quality test methods, the separate reviewed
metric-limit test, and the real static gate. Raw red and green output is retained
in `../evidence/clean-code-20261016/indirect-domain-guard/`. The red run used the
system Python and the green run the parent-provisioned hash-pinned verification
environment. No production source, baseline budget, lint allowance or cycle
allowance changed. The fixed production digest remains
`6ae54216d9d09eeeae53e7da28f68274c64cc8e713dc65804de89ec99a76af70`:
208 modules, 3,368 functions, 37,123 statements, 13,069 decisions and 598 import
edges. These measurements are not size reductions or runtime acceptance.

### What remains outside this guard

- Only the two named roots are protected. New pure owners, including a proposed
  crosshair codec or startup coordinator, need their own import-blocker and
  behavioral evidence; this change does not silently certify them.
- Dynamic imports, reflective aliases and implicit package-initializer effects
  are not inferred. Resolved declared dependency paths are conservative source
  relationships, not a simulated Python execution graph.
- Low counts or zero SCCs cannot prove responsibility ownership. Passing a
  whole window to a smaller helper retains the same coupling; inspect the full
  input/output and callback surfaces.
- Test-method counts do not measure exercised paths. A constructor extraction
  must preserve effect prefixes on failure, setting-observation time, retained
  owner lifetime and asynchronous activation order. A draft-session extraction
  must preserve mutable option copies, cancellation, validation and projection
  without prematurely mutating the registry.
- Shared DT7/CC thread ownership needs separate per-adapter admission/late-start,
  partial-constructor failure, blocked close and terminal-error checks. A common
  helper passing once cannot establish both adapters' protocol behavior.
- GPU retry policy needs failed partial initialization and retry/disposal
  ordering checks; mocked native handles cannot establish driver validity.

These results do not replace the parent's new fixed-source integrated run.
The prior 268 PASS / 12 SKIP / 3 INCOMPLETE result is a separate reported baseline,
not a result of this lane. Hardware, native desktop/GPU and packaging limits
remain unchanged; no full-clean claim is made.

### Independent startup extraction review

The subsequent startup candidate `0167a3c` failed independent comparison against
actual constructor ASTs at the fixed production base: 204 of 208 lookup/call/
boolean/argument/failure-prefix comparisons differed. Binding later callbacks
before the first effect moved attribute-lookup failure prefixes and could retain
stale collaborators; feedback preloading also observed its collaborators before
the original registry query. These are synthetic extraction-equivalence cases,
not a claim about normal-user crashes or physical devices.

Correction `607782c`, independently checked at documentation tip `c5b9633`,
preserves all 208 comparisons with no differences. A detached worktree also
passes 106 focused startup, real-window construction, assembly, asynchronous
lifecycle, delayed constructor cleanup, constructor failure, failed launch,
launch-abort and public-API test methods. The independent probe, red/green output
and focused log are in
`../evidence/clean-code-20261016/independent-startup-audit/`. The probe takes a
checkout path and compares its constructor region to `fa2c2b8` using local Git;
it is a review reproducer, not an installed test-suite dependency.

This accepts the named callback workflow's ordered startup responsibility and
lazy scenario guard. It does not unify window service lifetimes, extend abort
coverage to earlier partial constructors, or certify native OpenGL/audio/device
behavior. Existing unavailable-audio/offscreen warnings remain in the raw log.

### Explicit policies for the three new pure owners

The follow-on gate policy is deliberately root-specific. Existing
`referee_profiles` and `economy_values` restrictions remain unchanged. All five
protected roots reject declared backend, PySide2/6, PyQt5/6 and Tkinter imports,
including dependencies reached through local helpers, deferred scopes and
TYPE_CHECKING scopes. Additional forbidden targets are:

| Protected root | Additional forbidden prefixes |
| --- | --- |
| `rm_client.referee_profiles` | `rm_client.ui` |
| `rm_client.economy_values` | `rm_client.ui` |
| `rm_client.crosshair_profile` | `rm_client.ui`, `rm_client.model`, `rm_client.cs2_crosshair`, `rm_client.serial_ports` |
| `rm_client.ui.preference_draft` | `rm_client.model` |
| `rm_client.ui.polar.startup_activation` | `rm_client.ui.polar_app`, `rm_client.config_store`, `rm_client.configuration` |

The two UI-hosted pure owners may import other pure UI helpers. Directory
placement alone is not a dependency violation. The crosshair owner also joins
the existing lower-layer UI-import rule. These policies encode the reviewed
codec/facade, draft/live-model and startup/runtime-adapter boundaries; they do
not declare every unlisted module pure or prohibit every possible side effect.

Test-first checkpoint `31463b2` exposes 348 failing source mutations across the
three roots, import/from forms, direct/helper paths and eager/deferred/type-only
scopes. The corrected gate passes all 50 quality methods, the separate metric
negative-control test and the real static gate, without modifying a budget or
allowance. Positive controls explicitly allow pure UI siblings and unrelated
runtime adapters. The prior dynamic-import/implicit-initializer limitations
still apply. Runtime import-blocker and behavioral tests remain necessary,
particularly for behavior that is not represented by a declared import.

Raw red/green and separately identified candidate-tree checks are retained under
`../evidence/clean-code-20261016/pure-owner-policies/`. Passing separate candidate
static reports is not a combined integration result or native qualification.

## Combined-source architecture acceptance delta

Independent static/API/discovery checks cover production revision
`1417b925fd8b21642fe4957af62a51254db147bc`, source digest
`70538a0cdb59627162e01682bd703dea0b1fbb00193c45bff82e201e23be16fa`.
Evidence-only `d609914` and `ce49dc5` arrived during this review; their complete
diffs contain documentation/review artifacts only, with no production, suite
or tool changes. These are focused checks, **not** the pending full-profile run.

| Area | Non-cosmetic accepted change | Remaining coupling or evidence limit |
| --- | --- | --- |
| Startup | Named ports own ordered profile preparation, loaded presentation and lazy scenario admission; independent lookup/failure-prefix correction preserves observation order. | Window constructs services, owns timers/abort, and adapts filesystem/backend/UI effects. Earlier partial constructors remain outside retained-abort coverage. |
| Crosshair/catalog | Pure schema and codecs become the catalog's source; facade aliases preserve codec identity and twelve function ASTs. | Qt editor, previews, registry mutation and presentation preferences remain in the facade. This does not make the whole catalog/model runtime pure. |
| Preference editing | A draft owner tracks accepted snapshots, forced edits and pending/live reconciliation. | Widgets normalize values; the adapter assembles and commits real transactions. This is not crash durability, universal deep-copy isolation or a pure settings dialog. |
| Writer lifetime | DT7 and CC share retained thread admission/reservation/join ownership. | Each adapter retains protocol, queue, native-handle release and neutral semantics. Thread ownership is not physical shutdown confirmation. |
| Angle admission | Immutable reservations and an ordered first-conflict veto detach policy while preserving lazy alias observations. | Manager still reads registry/native handles and configures, disarms and publishes status. Policy does not acquire ports or grant forwarding authority. |
| Compositor | Partial setup unbinds independently; failed shader candidates and incomplete retirement remain owned before retry. | Native context/driver validity, actual pixel output and presentation cadence need desktop evidence. Large source-specific rendering passes remain intentional. |
| Focus loss | Authority revocation precedes independently attempted cleanup; failed cleanup no longer skips other neutral routes and pending-input clearing. | An attempt is not a firmware acknowledgement. Ordinary exceptions are handled; process-control exceptions and physical failures remain separate. This is an intentional safety correction, not historical-prefix equivalence. |
| Economy hold | Synthetic autorepeat release no longer finishes a physical Enter/Return confirmation hold. | Modal priority and the rest of keyboard routing still depend on the window. The historical differential explicitly adjusts only the reproduced defect. |
| Architecture gates | Five scoped roots reject forbidden dependencies through declared local helper paths, with negative and positive controls. | Dynamic imports, implicit initializer effects, reflection and arbitrary effects through callbacks remain outside static inference. No general purity or clean-code score is claimed. |

### Reproduction and integrity checks

- Static gate passes. The same metric implementation measures 213 modules,
  3,396 functions, 37,179 statements, 13,082 decisions and 605 local import edges;
  both eager and deferred-inclusive SCC counts are zero. Compared with `fa2c2b8`,
  this is **+5 modules, +28 functions, +56 statements, +13 decisions and +7 edges**.
  Replacements are counted; the result is not presented as a whole-tree shrink.
- Actual window API freeze passes its removal/restoration mutation self-test.
  The unchanged public-contract manifest is not a proof of every runtime path.
- 50 quality-tool methods and a separate 64-method boundary/API/metric selection
  pass. These counts overlap in responsibility and are not an aggregate coverage
  percentage or substitute for the full profile.
- Import-free registry discovery finds 302 suites. All 14 newly added executable
  suites are discovered as offline; the two new historical fixtures are correctly
  not executable suites. No manual suite exemption was added.
- `tools/quality/baseline.json`, `tests/window_api_contract.json`,
  `tools/test_registry.py` and `tools/quality/metrics.py` are byte-for-byte unchanged
  from `fa2c2b8`; hashes are retained in the discovery log. Existing harness edits
  add newly extracted names or establish a fresh GPU lifetime. The keyboard
  differential's explicit repeat-release adjustment accompanies dedicated red
  defect evidence; the historical source fixture remains unchanged.

Raw output, source-scoped summary and hashes are under
`../evidence/clean-code-20261016/combined-architecture-audit/`.
Remaining broad debts include mixed RobotState authority, window/service
assembly, cross-service modal routing, live configuration/UI effect ordering,
video/control/combat coordination and source-specific map/render adapters.
No additional broad rewrite is justified solely by these counts. Existing
hardware, firmware, packaging, native audio/desktop/GPU and unfinished-profile
acceptance limits remain open.

### Third-stage fixed-source follow-on

The [final third-stage architecture audit](final_third_architecture_audit_20261016.md)
records independent focused acceptance at `ade481f`, including actual API mutation,
50 quality tests, metric controls and all nine new suites (49 methods). All 311
suites are discovered without changed exemptions; baseline/API/metric integrity
and earlier policy guards are unchanged. It records meaningful audio recovery,
descriptor admission, quiescence, health projection and MJPEG outcomes, while
retaining broad view/model/runtime coupling and native prerequisites explicitly.
Its 215-module source is larger, not falsely counted as a whole-tree reduction.
Parent-owned full-profile completion remains a separate evidence item.
