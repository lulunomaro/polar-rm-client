# RobotState authority audit, 2026-10-17

## Scope and result

This closes the **declared-field inventory/documentation portion** of gate E in
`remaining_acceptance_checklist_20261017.md`; it does not close all semantic
coverage, final integration, or native qualification. RobotState is a shared
projection with explicit ordered writers, not a universal command authority.
No production replacement is justified merely by the number of fields/writers.

Checkout HEAD at inventory: `97228a73b90e7db73533af7e07a8f5a7e8fb0716`. All 221 Python files under
`rm_client` were parsed without importing or executing application code.
Inventory contains **242 declared dataclass fields**. The scan input
digest is `d6a9b5855d1d542da0c2120972dceac7273819ba74512749fdd02d61146702e6`
(SHA-256 over sorted POSIX path, NUL, SHA-256 file bytes). This is a bounded
`rm_client/**/*.py` digest, not the full verifier source fingerprint. Other
work may be integrated concurrently; rerun inventory for a later source.
The original digest and appendix line references are **historical**, not a
certificate of the later repair. At source
`4734c31dff4e5f628237cc49b93c7d79b4a586f8`, a read-only static rescan parsed all
221 Python files and reconfirmed 242 declared fields. Its same-scope digest is
`2c18604c8b4af4bca95003c4a267a765d29fe96524a5f672af8107a83396c334`.
This rescan did not rerun tests or regenerate every writer-reference line.

## Subsequent reproduced defect and accepted repair

The original overlap finding below was a documentation gap at the time of the
inventory. Later execution established actual valid-input inconsistencies:
partial score updates reset the match stage; ammo-only updates zeroed unrelated
health; current-schema enemy-base field 6 replaced the official roster and could
fabricate an elimination. Signed negative countdown and partial respawn updates
also disagreed with the decoded adapter.

Source repair `4734c31dff4e5f628237cc49b93c7d79b4a586f8`
(public `cb66f1f9985e362d775aff64884c912d520b67bf`, identical tree verified by the
integration owner) filters raw state updates
by actual wire-field presence, preserves explicit zero/false, keeps current
roster decoding on field 11 only, and prevents the legacy preview roster from
entering current health/event authority. The actual referee profile is passed
at `video_backend_mixin.py:918-924`; profile changes reset event history.
Countdown sign/clamping agrees with the decoded adapter. Partial respawn progress
is left to the adapter's accumulated snapshot unless both operands are present.

Ten new focused tests passed. Independent offscreen review also reproduced the
three primary failures before the fix through the real window's
`backend.apply_updates` -> `consume_official_messages` -> `apply_official_state`
chain, then verified all three after the fix. This is bounded acceptance of the
named counterexamples, not whole-state equivalence or hardware qualification.
Details, source hashes, publication identifier and limits are recorded in
[Authority projection repair](authority_projection_repair_20261017.md).

## 2026-10-17 addendum: canonical scalar writer in the normal window tick

Accepted source: local `2f95679285b5b16eb38c9c16dfcf5b5967c7d7d3`, public
`e1fed5dc11e6c4d3b547178f36e0af93e5fa9dac`, shared tree
`6dc24020fcbfeec9ec8da426aabf259fe5376aa2`.

The normal window raw consumer now retains only `official_feed_at` from the
feed's update dictionary and still delivers combat events. Decoded backend
application owns referee scalar telemetry and its frontend row synchronization;
raw processing no longer overwrites those scalars after that stage. The adapter
also derives `air_support_can_free` when decoded `left_time` is present, including
explicit zero. Current-profile omission preserves the value; legacy decoding
materializes default fields, so its omitted wire value can canonically clear it.

This supersedes the **current-path** two-writer descriptions in the historical
order and field-group tables below; those findings and original inventory remain
unchanged as the record of why the repair was needed. Direct callers retain the
public feed scalar-return API and explicit `apply_official_state` compatibility.
This is not a universal single writer across demo, POLAR, configuration or direct
compatibility calls.

Inter-stage arrivals deliberately wait for canonical scalar application; adapter
failure or transition blocking no longer gets a raw scalar fallback. Receipt
freshness and event handling remain independent: a fresh packet does not certify
successful scalar application or an atomic state transaction. Independent focused
review accepted the change; the aggregate run was still in progress when this
addendum was written, so no aggregate score is asserted here. Exact baseline,
changed expectations and remaining boundaries are in
[Canonical scalar writer](canonical_scalar_writer_20261017.md).

## Authority and overwrite order (historical; see dated addendum above)

`ui/polar_app.py:985-1040` is the normal window sequencing contract:

1. A due control tick can run before the logic tick, unless modal input blocks it.
   `control.py:500-530` constructs command intent from held inputs and registry
   settings, with state speed/power fallback. It does not grant physical control
   merely because a telemetry field has a value.
2. Capture ingestion and connection LEDs precede `RobotState.tick`.
   `model.py:421-426` returns without simulation when mock simulation is disabled
   or official state is active. Its later writes are demo projections, with
   separate economy-managed guards.
3. Backend heartbeat and decoded referee application run next. In
   `backend/manager.py:405-409,1922-1954`, the first official update clears mock
   frontend state **before** applying data, marks official authority, applies
   messages individually, then synchronizes frontend projections.
4. Readiness, hero deployment/lob, and spin guards run before the official raw
   message/event-feed consumer and its projection. The raw feed is a second
   writer, later than the decoded referee adapter; equal names are intentional
   overlap, not evidence of a data race by themselves.
5. Hit information, vision state synchronization, vision events, and registry
   ticking follow. Vision lock fields therefore override demo lock fields.
6. POLAR link handling is last. `backend/manager.py:916-938` drains segments,
   publishes freshness/diagnostics, copies the bridge overlay, and applies it.
   `model.py:403-419` applies only validated allowlisted values while active.
   Thus valid POLAR shared telemetry wins over earlier demo/referee values on
   this path. It cannot overwrite operator choice, control authority, or the
   official-owned containers through arbitrary overlay keys.

This is ordered, partial publication, not an atomic whole-state transaction.
`_run_tick_stage` isolates stage failures; a failure does not roll back prior
writers. Constructor, configuration, scenario, legacy UI, and explicit
user actions can also write fields outside this tick order. Their source sites
are included where the bounded scan recognizes them.

### Field-group responsibility ledger

| Field group | Producers and overrides | Authority/freshness boundary |
| --- | --- | --- |
| Match/score/economy/health | Model defaults/demo; decoded `backend/referee/rm2026/state_adapter.py`; later `OfficialMatchFeed` updates; allowlisted POLAR health/heat/energy | Official state suppresses demo. Raw feed applies after decoded data; repair `4734c31` requires wire-field presence and preserves explicit zeros. POLAR wins only for allowlisted active overlay values. Economy ownership suppresses selected demo balances. |
| Official robot arrays, positions, buffs and frontend rows/maps | Referee adapter canonical containers, then `sync_frontend_state` at lines 61-169; raw feed supplies current-profile field-11 health only after repair `4734c31` | Projection returns with no authoritative inputs. Explicit local zero-health beats global fallback; rows/maps/buffs retain documented publication order. POLAR uses separate `polar_ally_hp`/`polar_ground_positions`. |
| POLAR telemetry and authority diagnostics | Bridge handlers `polar_bridge.py:176-359`; manager telemetry/authority publishers | Link activity requires received segments and age <= 2 seconds (`polar_bridge.py:50,153-162`). This is link freshness, not a separate timestamp for every cached overlay field. Generic spec outputs stay in overlay; only whitelist fields cross into model. |
| Video provenance and luminance | Video mixin capture/backend ingestion; connection LEDs in window | `video_backend_mixin.py:654-747` selects webcam/backend/synthetic and rejects repeated frames; authoritative deploy video cuts normal consumption. Backend freshness/normal timeline gates apply before publication. Frame source/generation mostly live in dedicated owners, not RobotState. |
| Vision counts, aim, target and locks | `active_tongji_result` and `sync_tongji_vision_state` (`video_backend_mixin.py:873-909,1080-1125`) | Local/receiver `latest` enforce timeout. Auto prefers available Tongji over local (`vision.py:295-313`), not a timestamp comparison between both. Calibrated/FOV tracking and terminal-target rejection control lock. No result clears lock/calibration/counts, but old numeric aim/sequence/target values can remain and must not be treated as fresh authorization. |
| Control and gimbal egress | Control owns input/command intent; manager owns safety/role/bridge gates and publishes diagnostics | `manager.py:880-914,2180-2207` selects a single gimbal egress. Vision publication also checks enabled/non-mock/locked/calibrated at mixin lines 1127-1164. `publish_vision_control` forces fire false. Telemetry presence is not actuator permission. |
| Combat events, kill counts/streak and status | OfficialMatchFeed creates/deduplicates official events; vision detector infers events; mixin accepts them and updates counts | Feed freshness and kill-data freshness are separate (`combat_events.py:166-190`). In OfficialEvent Auto mode, fresh kill-capable referee data suppresses vision kill inference (`video_backend_mixin.py:941-967`). Vision uses timestamp+sequence frame tokens. Evidence-mode filtering occurs before counter updates at lines 969-986. Counts are frontend accepted-event history, not a replacement referee authority. |
| Operator/scenario/configuration/status | Registry, scenario/profile/configuration and UI adapters; reset paths in manager | These are explicit live actions/defaults, not telemetry fields that a generic POLAR segment may control. Appendix names observed sites; full user-action interleavings are not proven here. |

### Generation and retained-value boundaries

- Local vision submit and publication compare lifecycle epoch
  (`tongji_yolo.py:492-503,627-632`); receiver also checks epoch on run admission
  (`tongji_bridge.py:366-371`). `latest` methods at lines 506 and 358 respectively
  apply timeouts. This audit reads those contracts; it does not claim to have
  executed race tests or to have traced every worker publication path.
- `vision_sequence` is a result counter, not a complete cross-source generation
  identity. The selected snapshot/source and worker epochs are separate owners.
- POLAR link expiry stops overlay application; it does not erase the last copied
  scalar readings. Official timestamps and vision connection/lock flags likewise
  must be read with their values. Retention alone is not fresh command authority.
- The two official projections overlap in health, match and economy fields.
  Decoded adapter applies presence selection (`state_adapter.py:171-172`), while
  raw feed has its own parsing/defaults. The initial static audit left their
  agreement unproven. The subsequent repair above closes the named executed
  partial-message/profile counterexamples; universal equivalence remains
  unproven. See the repair report rather than treating the original gap as a
  still-unreproduced concern.

## Existing test anchors, not new execution results

- `tests/test_state_overlay_contract.py:22,56,178,189,206,240`: built-in coverage,
  injection rejection, generic mapping authority, live/stale precedence,
  official-owned fields, timestamp retention.
- `tests/test_referee_frontend_projection_contract.py:103-172`: empty authority,
  explicit local zero, ordered partial publication and malformed inputs.
- `tests/test_vision_stop_admission.py:285,488`: slow-copy/encoding epoch cases.
- `tests/test_normal_video_timeline_contract.py:411,578,668`: stale frames,
  callback-observed source/worker, freshness boundaries.
- `tests/test_vision_selection.py:36-67`: source spelling, ordered selection,
  capture disable and revocation planning.

The initial inventory and latest static rescan did not execute tests. The
subsequent repair has the separately identified focused and independent results
above. Final-source aggregate results, all-field semantic coverage, per-field
POLAR expiry policy, general agreement of both official projections, and physical
output remain distinct verification questions. The
source verifier excludes `docs` (`tools/verification_sources.py:12-23`); this
new document therefore does not alter its declared source input fingerprint.

## Complete declared-field inventory, bounded writer trace

All fields have initializers in `model.py` at the listed declaration line.
`write` means a syntactic attribute assignment; `item` means subscript assignment;
`mutate` means a recognized mutating container call. `overlay` denotes the
explicit allowlist consumed through `model.py:418`; `feed-key` denotes a literal
key in `OfficialMatchFeed.consume` published through the mixin's `setattr` at
historical line 934 (line 938 at repair `4734c31`). These are writer candidates/source references, not universal runtime
reachability or exclusivity proof. Historical `legacy_tk_app.py` sites are
included but do not establish normal-window execution.

The scan covers all declared fields, but only receiver spellings `state`,
`self.state`, `target`, and `self` inside `model.py`; it is not whole-program
alias analysis. Constructor kwargs, external callers, dynamic dictionary keys,
returned mutable aliases, generic reflection, and arbitrary extension attributes
can escape it. Follow-up classifications of default-only fields below are
bounded static findings, **not** proof that the fields are dead, immutable, or
fully covered under dynamic/external calls.

Follow-up at source `2bcf4b4595dd5a261cb4e4c98b1ac2fb8e209ea6` examined
constructor arguments, official-feed literal updates, POLAR allowlists, and
configuration/protocol names for the ten initially untraced scalar fields. No
post-default scalar producer was found in these production paths. Two supply-zone
fields have explicit unknown-state rationale; seven are consumed as presentation
or demo defaults without an established constant-value design guarantee;
`max_chassis_energy` has a decoded canonical-container value but no scalar
projection or production scalar consumer found. This does not establish complete
dynamic coverage or a runtime defect. No tests were run.

### Bounded follow-up: `max_chassis_energy`

Read-only source disposition at local
`197f4284e59efc432750e1cf6051c5b8f0e5ed18`; corresponding public source is
[`a44cf8d477b9733bdfdcd43f9b671260c18d1505`](https://github.com/lulunomaro/polar-rm-client/commit/a44cf8d477b9733bdfdcd43f9b671260c18d1505),
as recorded in `docs/reviews/field_log_admission_repair_20261017.md`.
This follow-up inspected source and existing test expectations; it did not run
additional tests or establish a new runtime qualification.

- Both `rm_client/backend/referee/rm2026/contract.py` and
  `rm_client/backend/referee/student_legacy/contract.py` define
  `RobotStaticStatus` field 14 as `uint32 max_chassis_energy`.
  `rm_client/backend/referee/rm2026/codec.py::_decode_static` decodes it;
  `rm_client/backend/referee/rm2026/state_adapter.py::_apply_one` retains present
  values in `state.robot_statics[state.robot_id]`, without scalar projection.
  `rm_client/backend/referee/student_legacy/state_adapter.py::_apply_one`
  translates presence by field name and delegates to that shared adapter, so
  the canonical container also retains the legacy decoded value.
- `tests/fixtures/referee_state_baseline.json` explicitly records
  `RobotStaticStatus_only_max_chassis_energy`: field 14 value 24 updates
  `robot_statics[3]`. The `RobotStaticStatus_full_wire` and
  `RobotStaticStatus_explicit_zeros_wire` cases also retain this dictionary field.
  `tests/test_referee_state_characterization.py::test_frozen_ordered_projection_and_partial_failure_state`
  checks these frozen observations. They do not establish a requirement to
  project the separate scalar.
- `rm_client/model.py` declares the scalar default as 100. No production scalar
  consumer was found in this bounded review. Actual capacitor displays and
  alerts use `RobotState.effective_capacitor_percent`, calculated from
  `current_buffer_energy` and `max_buffer_energy`; this is a separate quantity,
  not evidence for a chassis-energy ratio or a new mapping.
- Compatibility APIs remain explicit: `VideoBackendMixin.apply_official_state`
  in `rm_client/ui/polar/mixins/video_backend_mixin.py` can assign existing
  state attributes from caller-supplied updates. `OfficialMatchFeed.consume`
  in `rm_client/combat_events.py` still returns public scalar updates, but has
  no `max_chassis_energy` projection. The tests
  `test_explicit_direct_apply_compatibility_remains_available` and
  `test_event_delivery_and_public_scalar_return_are_separate_contracts` in
  `tests/test_official_scalar_writer_characterization.py` preserve those API
  contracts; they do not certify external callers of this particular field.

**Disposition:** retain the bounded default-only scalar classification with
unresolved purpose. No production fix is justified by the inspected evidence.
This is not proof that the field is dead or immutable, nor certification of
external consumers, dynamic attribute access, or arbitrary compatibility API
updates. No mapping or new scalar authority is inferred.

Current `replay.py:1-5,35-60` only summarizes field logs; it is not an established
RobotState writer. Normal and legacy constructors use `RobotState()` without
field overrides (`ui/polar_app.py:491`, `legacy_tk_app.py:59`).

Fields with bounded default-only scalar dispositions: **10**: `base_max_health`, `enemy_base_max_health`, `enemy_outpost_max_health`, `in_supply_zone`, `max_chassis_energy`, `outpost_max_health`, `projectile_shop_price`, `supply_zone_known`, `target_health`, `target_max_health`.

| Field (declaration line) | Observed writer / dynamic contract sites |
| --- | --- |
| `referee_profile` (38) | `rm_client/backend/manager.py`:123,378 (write) |
| `match_mode` (39) | `rm_client/model.py`:511 (write); `rm_client/ui/polar/mixins/persistence_mixin.py`:176 (write) |
| `match_title` (40) | `rm_client/model.py`:488,512 (write); `rm_client/ui/polar/mixins/persistence_mixin.py`:177 (write) |
| `match_duration_sec` (41) | `rm_client/model.py`:489,513 (write); `rm_client/ui/polar/mixins/persistence_mixin.py`:178 (write) |
| `alliance_color` (42) | `rm_client/ui/polar/mixins/persistence_mixin.py`:143,161,184,791 (write) |
| `current_stage` (44) | `rm_client/backend/manager.py`:1187 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:181 (write); `rm_client/combat_events.py`:200 (feed-key); `rm_client/ui/polar/mixins/persistence_mixin.py`:183 (write) |
| `stage_countdown_sec` (45) | `rm_client/backend/referee/rm2026/state_adapter.py`:191 (write); `rm_client/combat_events.py`:204 (feed-key); `rm_client/model.py`:428 (write); `rm_client/ui/polar/mixins/persistence_mixin.py`:179 (write) |
| `stage_elapsed_sec` (46) | `rm_client/backend/referee/rm2026/state_adapter.py`:192 (write); `rm_client/combat_events.py`:205 (feed-key); `rm_client/model.py`:429 (write); `rm_client/ui/polar/mixins/persistence_mixin.py`:180 (write) |
| `current_round` (47) | `rm_client/backend/referee/rm2026/state_adapter.py`:175 (write); `rm_client/combat_events.py`:196 (feed-key) |
| `total_rounds` (48) | `rm_client/backend/referee/rm2026/state_adapter.py`:176 (write); `rm_client/combat_events.py`:197 (feed-key) |
| `red_score` (49) | `rm_client/backend/referee/rm2026/state_adapter.py`:177 (write); `rm_client/combat_events.py`:198 (feed-key); `rm_client/model.py`:514 (write) |
| `blue_score` (50) | `rm_client/backend/referee/rm2026/state_adapter.py`:178 (write); `rm_client/combat_events.py`:199 (feed-key); `rm_client/model.py`:515 (write) |
| `ally_victory_points` (51) | `rm_client/model.py`:494 (write); `rm_client/ui/polar/mixins/persistence_mixin.py`:186 (write) |
| `enemy_victory_points` (52) | `rm_client/model.py`:495 (write); `rm_client/ui/polar/mixins/persistence_mixin.py`:187 (write) |
| `control_owner` (53) | `rm_client/model.py`:496 (write); `rm_client/ui/polar/mixins/persistence_mixin.py`:188 (write) |
| `control_progress` (54) | `rm_client/model.py`:497 (write); `rm_client/ui/polar/mixins/persistence_mixin.py`:189 (write) |
| `control_countdown_sec` (55) | `rm_client/model.py`:498 (write) |
| `current_health` (56) | `rm_client/backend/referee/rm2026/state_adapter.py`:226,245 (write); `rm_client/combat_events.py`:250 (feed-key); `rm_client/economy.py`:223,225 (write); `rm_client/robot_profiles.py`:125 (write); `rm_client/state_overlay.py`:45 (overlay) |
| `max_health` (57) | `rm_client/backend/referee/rm2026/state_adapter.py`:225 (write); `rm_client/robot_profiles.py`:124 (write); `rm_client/state_overlay.py`:47 (overlay) |
| `current_heat` (58) | `rm_client/backend/referee/rm2026/state_adapter.py`:246 (write); `rm_client/combat_events.py`:251 (feed-key); `rm_client/model.py`:449 (write); `rm_client/state_overlay.py`:23 (overlay) |
| `max_heat` (59) | `rm_client/backend/referee/rm2026/state_adapter.py`:228 (write); `rm_client/robot_profiles.py`:127 (write); `rm_client/state_overlay.py`:28 (overlay) |
| `current_chassis_energy` (60) | `rm_client/backend/referee/rm2026/state_adapter.py`:247 (write); `rm_client/combat_events.py`:252 (feed-key); `rm_client/model.py`:450 (write); `rm_client/state_overlay.py`:44 (overlay) |
| `max_chassis_energy` (61) | **Bounded default-only scalar (100); purpose unresolved.** `backend/referee/rm2026/codec.py`:200 decodes field 14; `backend/referee/rm2026/state_adapter.py`:221 retains it in `robot_statics`, without scalar projection. No production scalar consumer found. |
| `current_buffer_energy` (62) | `rm_client/backend/referee/rm2026/state_adapter.py`:248 (write); `rm_client/combat_events.py`:253 (feed-key); `rm_client/model.py`:451 (write) |
| `max_buffer_energy` (63) | `rm_client/backend/referee/rm2026/state_adapter.py`:238 (write) |
| `chassis_power_w` (64) | `rm_client/model.py`:452 (write) |
| `heat_cooldown_rate` (65) | `rm_client/backend/referee/rm2026/state_adapter.py`:230 (write); `rm_client/model.py`:453 (write); `rm_client/robot_profiles.py`:128 (write) |
| `remaining_ammo` (66) | `rm_client/backend/referee/rm2026/state_adapter.py`:250 (write); `rm_client/combat_events.py`:255 (feed-key); `rm_client/economy.py`:219 (write); `rm_client/model.py`:478 (write); `rm_client/robot_profiles.py`:132 (write) |
| `ammo_capacity` (67) | `rm_client/economy.py`:220 (write); `rm_client/robot_profiles.py`:131 (write) |
| `total_projectiles_fired` (68) | `rm_client/backend/referee/rm2026/state_adapter.py`:249 (write); `rm_client/combat_events.py`:254 (feed-key) |
| `remaining_economy` (69) | `rm_client/backend/referee/rm2026/state_adapter.py`:213 (write); `rm_client/combat_events.py`:209 (feed-key); `rm_client/economy.py`:216 (write); `rm_client/model.py`:482 (write) |
| `total_economy_obtained` (70) | `rm_client/backend/referee/rm2026/state_adapter.py`:214 (write); `rm_client/combat_events.py`:210 (feed-key); `rm_client/model.py`:483 (write) |
| `in_supply_zone` (73) | **Intentional unknown default (False) in reviewed paths.** `model.py`:71-74 and `backend/polar_bridge.py`:342-346 explain unavailable RFID mapping. Read by `economy_values.py`:71 and `backend/referee/rm2026/economy_rules.py`:69-74; referee validates purchases when zone is unknown. |
| `supply_zone_known` (74) | **Intentional unknown default (False) in reviewed paths.** `model.py`:71-74 and `backend/polar_bridge.py`:342-346 prohibit guessing an RFID mapping. Read by `economy_values.py`:72 and `backend/referee/rm2026/economy_rules.py`:69-74. |
| `out_of_combat` (75) | `rm_client/backend/referee/rm2026/state_adapter.py`:251 (write); `rm_client/combat_events.py`:256 (feed-key) |
| `can_remote_heal` (76) | `rm_client/backend/referee/rm2026/state_adapter.py`:252 (write); `rm_client/combat_events.py`:257 (feed-key) |
| `can_remote_ammo` (77) | `rm_client/backend/referee/rm2026/state_adapter.py`:253 (write); `rm_client/combat_events.py`:258 (feed-key) |
| `respawn_pending` (78) | `rm_client/backend/referee/rm2026/state_adapter.py`:373 (write); `rm_client/combat_events.py`:242 (feed-key); `rm_client/economy.py`:226 (write) |
| `respawn_progress` (79) | `rm_client/backend/referee/rm2026/state_adapter.py`:379 (write); `rm_client/combat_events.py`:243 (feed-key); `rm_client/economy.py`:227 (write) |
| `respawn_can_free` (80) | `rm_client/backend/referee/rm2026/state_adapter.py`:380 (write); `rm_client/combat_events.py`:244 (feed-key) |
| `respawn_gold_cost` (81) | `rm_client/backend/referee/rm2026/state_adapter.py`:381 (write); `rm_client/combat_events.py`:245 (feed-key) |
| `respawn_can_pay` (82) | `rm_client/backend/referee/rm2026/state_adapter.py`:382 (write); `rm_client/combat_events.py`:246 (feed-key) |
| `air_support_active` (83) | `rm_client/backend/referee/rm2026/state_adapter.py`:322 (write); `rm_client/combat_events.py`:263 (feed-key); `rm_client/economy.py`:229 (write) |
| `air_support_can_free` (84) | `rm_client/combat_events.py`:265 (feed-key) |
| `air_support_free_time_sec` (85) | `rm_client/backend/referee/rm2026/state_adapter.py`:323 (write); `rm_client/combat_events.py`:264 (feed-key) |
| `air_support_cost_coins` (86) | `rm_client/backend/referee/rm2026/state_adapter.py`:324 (write); `rm_client/combat_events.py`:266 (feed-key) |
| `economy_mock_mode` (87) | `rm_client/ui/polar_app.py`:951 (write) |
| `economy_managed` (88) | `rm_client/economy.py`:215 (write) |
| `economy_status_revision` (89) | `rm_client/backend/referee/rm2026/state_adapter.py`:388 (write); `rm_client/economy.py`:230 (write); `rm_client/ui/polar/mixins/video_backend_mixin.py`:936 (write) |
| `economy_command_status` (90) | `rm_client/backend/referee/rm2026/state_adapter.py`:389 (write); `rm_client/economy.py`:231 (write); `rm_client/ui/polar/mixins/video_backend_mixin.py`:937 (write) |
| `tech_level` (91) | `rm_client/backend/referee/rm2026/state_adapter.py`:215 (write); `rm_client/combat_events.py`:211 (feed-key); `rm_client/model.py`:484 (write) |
| `encryption_level` (92) | `rm_client/backend/referee/rm2026/state_adapter.py`:216 (write); `rm_client/combat_events.py`:212 (feed-key); `rm_client/model.py`:485 (write) |
| `robot_id` (93) | `rm_client/backend/referee/rm2026/state_adapter.py`:219 (write); `rm_client/robot_profiles.py`:122 (write) |
| `robot_type` (94) | `rm_client/robot_profiles.py`:121 (write) |
| `robot_profile` (95) | `rm_client/robot_profiles.py`:120 (write) |
| `projectile_type` (96) | `rm_client/robot_profiles.py`:129 (write) |
| `level` (97) | `rm_client/backend/referee/rm2026/state_adapter.py`:223 (write); `rm_client/robot_profiles.py`:123 (write) |
| `target_id` (98) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1113 (write) |
| `target_distance_m` (99) | `rm_client/model.py`:469 (write); `rm_client/ui/polar/mixins/video_backend_mixin.py`:1114 (write) |
| `target_health` (100) | **Bounded default-only demo/display value (320).** Readers: `model.py`:503,532; `ui/polar/renderers/combat_target_hud.py`:580,605; `legacy_tk_app.py`:236. Vision synchronization updates identity/distance/lock, not this scalar. |
| `target_max_health` (101) | **Bounded default-only demo/display value (450).** Readers: `model.py`:503,532; `ui/polar/renderers/combat_target_hud.py`:580,605; `legacy_tk_app.py`:236. Vision synchronization updates identity/distance/lock, not this scalar. |
| `target_locked` (102) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1091,1120,1123 (write) |
| `energy_target_locked` (103) | `rm_client/model.py`:471 (write) |
| `energy_target_distance_m` (104) | `rm_client/model.py`:470 (write) |
| `energy_rune_phase` (105) | `rm_client/backend/referee/rm2026/state_adapter.py`:311 (write); `rm_client/model.py`:472 (write) |
| `energy_rune_progress` (106) | `rm_client/backend/referee/rm2026/state_adapter.py`:313 (write); `rm_client/model.py`:473 (write) |
| `energy_rune_rotation` (107) | `rm_client/model.py`:474 (write) |
| `gimbal_power` (108) | `rm_client/model.py`:455 (write) |
| `power_limit` (109) | `rm_client/backend/referee/rm2026/state_adapter.py`:232 (write); `rm_client/robot_profiles.py`:126 (write); `rm_client/state_overlay.py`:58 (overlay) |
| `speed_limit` (110) | `rm_client/model.py`:456 (write) |
| `chassis_yaw_deg` (111) | `rm_client/model.py`:457 (write); `rm_client/state_overlay.py`:21 (overlay) |
| `gimbal_yaw_deg` (112) | `rm_client/model.py`:458 (write); `rm_client/state_overlay.py`:25 (overlay) |
| `relative_yaw_deg` (113) | `rm_client/model.py`:459 (write); `rm_client/state_overlay.py`:37 (overlay) |
| `capacitor_percent` (114) | `rm_client/model.py`:454 (write) |
| `friction_wheel` (115) | `rm_client/model.py`:460 (write) |
| `lock_state` (116) | `rm_client/model.py`:461 (write); `rm_client/ui/polar/mixins/video_backend_mixin.py`:1092,1121,1124 (write) |
| `small_buff` (117) | `rm_client/model.py`:462 (write); `rm_client/state_overlay.py`:73 (overlay) |
| `large_buff` (118) | `rm_client/model.py`:463 (write); `rm_client/state_overlay.py`:71 (overlay) |
| `anti_rotation` (119) | `rm_client/model.py`:464 (write) |
| `projectile_allowance_17mm` (120) | `rm_client/backend/manager.py`:401 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:46,50,55,58 (write); `rm_client/model.py`:479 (write); `rm_client/state_overlay.py`:59 (overlay) |
| `projectile_allowance_42mm` (121) | `rm_client/backend/manager.py`:402 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:47,51,56,59 (write); `rm_client/model.py`:480 (write); `rm_client/state_overlay.py`:60 (overlay) |
| `projectile_shop_state` (122) | `rm_client/model.py`:486 (write) |
| `projectile_shop_price` (123) | **Bounded default-only demo/display value (50).** Readers: `model.py`:486 (demo shop status); `ui/polar/renderers/tactical_map.py`:2004 (price display). No live producer found. |
| `base_health` (124) | `rm_client/backend/referee/rm2026/state_adapter.py`:194 (write); `rm_client/combat_events.py`:218 (feed-key); `rm_client/model.py`:490,516 (write); `rm_client/state_overlay.py`:42 (overlay); `rm_client/ui/polar/mixins/persistence_mixin.py`:190,195 (write) |
| `base_max_health` (125) | **Bounded default-only presentation value (5000).** Readers: `ui/polar/renderers/tactical_map.py`:1954; `kill_fx.py`:1504; `ui/polar/mixins/persistence_mixin.py`:195. No post-default producer found; not a proven immutable constant. |
| `base_status` (126) | `rm_client/backend/referee/rm2026/state_adapter.py`:195 (write); `rm_client/combat_events.py`:219 (feed-key); `rm_client/model.py`:520 (write) |
| `base_shield` (127) | `rm_client/backend/referee/rm2026/state_adapter.py`:196 (write); `rm_client/combat_events.py`:220 (feed-key); `rm_client/model.py`:524 (write) |
| `enemy_base_health` (128) | `rm_client/backend/referee/rm2026/state_adapter.py`:199 (write); `rm_client/model.py`:491,517 (write); `rm_client/ui/polar/mixins/persistence_mixin.py`:191,196 (write) |
| `enemy_base_max_health` (129) | **Bounded default-only presentation value (5000).** Readers: `ui/polar/renderers/tactical_map.py`:1958; `kill_fx.py`:1506; `ui/polar/mixins/persistence_mixin.py`:196. No post-default producer found; not a proven immutable constant. |
| `enemy_base_status` (130) | `rm_client/backend/referee/rm2026/state_adapter.py`:200 (write); `rm_client/model.py`:521 (write) |
| `enemy_base_shield` (131) | `rm_client/backend/referee/rm2026/state_adapter.py`:201 (write); `rm_client/model.py`:525 (write) |
| `outpost_health` (132) | `rm_client/backend/referee/rm2026/state_adapter.py`:197 (write); `rm_client/combat_events.py`:221 (feed-key); `rm_client/model.py`:492,518 (write); `rm_client/state_overlay.py`:48 (overlay); `rm_client/ui/polar/mixins/persistence_mixin.py`:192,197 (write) |
| `outpost_max_health` (133) | **Bounded default-only presentation/reset value (1500).** Read by `ui/polar/mixins/persistence_mixin.py`:197. No post-default producer found; not a proven immutable constant. |
| `outpost_status` (134) | `rm_client/backend/referee/rm2026/state_adapter.py`:198 (write); `rm_client/combat_events.py`:222 (feed-key); `rm_client/model.py`:522 (write) |
| `enemy_outpost_health` (135) | `rm_client/backend/referee/rm2026/state_adapter.py`:202 (write); `rm_client/model.py`:493,519 (write); `rm_client/ui/polar/mixins/persistence_mixin.py`:193,198 (write) |
| `enemy_outpost_max_health` (136) | **Bounded default-only presentation/reset value (1500).** Readers: `ui/polar/renderers/tactical_map.py`:1962,2284,2322; `ui/polar/mixins/persistence_mixin.py`:198. No post-default producer found; not a proven immutable constant. |
| `enemy_outpost_status` (137) | `rm_client/backend/referee/rm2026/state_adapter.py`:203 (write); `rm_client/model.py`:523 (write) |
| `jump_ready` (138) | `rm_client/model.py`:465 (write) |
| `pte_ready` (139) | `rm_client/model.py`:466 (write) |
| `wheel_online` (140) | `rm_client/model.py`:467 (write); `rm_client/state_overlay.py`:74 (overlay) |
| `les_online` (141) | `rm_client/model.py`:468 (write) |
| `robot_rows` (142) | `rm_client/backend/manager.py`:394 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:130 (write); `rm_client/model.py`:544 (write) |
| `map_robots` (143) | `rm_client/backend/manager.py`:395 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:155 (write); `rm_client/model.py`:499,526 (write) |
| `map_markers` (144) | `rm_client/backend/manager.py`:396 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:277 (write); `rm_client/model.py`:506,535 (write) |
| `buff_rows` (145) | `rm_client/backend/manager.py`:397 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:169 (write); `rm_client/model.py`:539 (write) |
| `mqtt_connected` (146) | `rm_client/legacy_tk_app.py`:132 (write); `rm_client/ui/polar_app.py`:1002 (write) |
| `udp_video_connected` (147) | `rm_client/legacy_tk_app.py`:133 (write); `rm_client/ui/polar_app.py`:1005 (write) |
| `serial_connected` (148) | `rm_client/legacy_tk_app.py`:134 (write); `rm_client/ui/polar_app.py`:1008 (write) |
| `camera_connected` (149) | `rm_client/legacy_tk_app.py`:131 (write); `rm_client/ui/polar_app.py`:1001 (write) |
| `vision_backend` (150) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1082 (write) |
| `vision_connected` (151) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1083 (write) |
| `vision_calibrated` (152) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1088,1099 (write) |
| `vision_tracking_state` (153) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1087,1102 (write) |
| `vision_inference_device` (154) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1103 (write) |
| `vision_inference_fps` (155) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1104 (write) |
| `vision_latency_ms` (156) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1105 (write) |
| `vision_sequence` (157) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1106 (write) |
| `vision_detection_count` (158) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1089,1107 (write) |
| `vision_prediction_count` (159) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1090,1108 (write) |
| `vision_aim_yaw_deg` (160) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1109 (write) |
| `vision_aim_pitch_deg` (161) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:1110 (write) |
| `vision_kill_status` (162) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:945,954,965 (write) |
| `official_feed_at` (163) | `rm_client/combat_events.py`:191 (feed-key); `rm_client/ui/polar/mixins/video_backend_mixin.py`:939 (write) |
| `official_robot_health` (164) | `rm_client/backend/manager.py`:1154 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:206 (write); `rm_client/combat_events.py`:226 (feed-key) |
| `official_robot_bullets` (169) | `rm_client/backend/manager.py`:1155 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:209 (write) |
| `official_events_online` (170) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:927,938 (write) |
| `kill_streak` (171) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:983,985 (write) |
| `visual_kill_count` (172) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:980 (write) |
| `referee_kill_count` (173) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:982 (write) |
| `vision_scene_luminance` (174) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:681,745 (write) |
| `native_telemetry_at` (177) | `rm_client/backend/manager.py`:1753 (write) |
| `native_telemetry_unix_ms` (178) | `rm_client/backend/manager.py`:1754 (write) |
| `native_telemetry_frame_seq` (179) | `rm_client/backend/manager.py`:1755 (write) |
| `native_camera_online` (180) | `rm_client/backend/manager.py`:1756 (write) |
| `native_gimbal_online` (181) | `rm_client/backend/manager.py`:1757 (write) |
| `native_gimbal_mode` (182) | `rm_client/backend/manager.py`:1758 (write) |
| `native_bullet_count` (183) | `rm_client/backend/manager.py`:1759 (write) |
| `native_bullet_speed` (184) | `rm_client/backend/manager.py`:1760 (write); `rm_client/state_overlay.py`:32 (overlay) |
| `native_yaw_deg` (185) | `rm_client/backend/manager.py`:1761 (write) |
| `native_yaw_velocity` (186) | `rm_client/backend/manager.py`:1762 (write) |
| `native_pitch_deg` (187) | `rm_client/backend/manager.py`:1763 (write) |
| `native_pitch_velocity` (188) | `rm_client/backend/manager.py`:1764 (write) |
| `native_quaternion` (189) | `rm_client/backend/manager.py`:1765 (write) |
| `native_status_text` (190) | `rm_client/backend/manager.py`:1766 (write) |
| `hero_lob_yaw_calibrated` (193) | `rm_client/ui/polar/mixins/config_actions_mixin.py`:342,348 (write) |
| `hero_lob_pitch_calibrated` (194) | `rm_client/ui/polar/mixins/config_actions_mixin.py`:362,370 (write) |
| `hero_lob_relocalization_valid` (195) | `rm_client/backend/manager.py`:1768 (write); `rm_client/ui/polar/mixins/config_actions_mixin.py`:313,376 (write) |
| `hero_lob_armed` (196) | `rm_client/backend/manager.py`:1769 (write); `rm_client/ui/polar/mixins/config_actions_mixin.py`:314,322,384,388,992 (write); `rm_client/ui/polar/mixins/video_backend_mixin.py`:430,438 (write) |
| `hero_lob_status` (197) | `rm_client/backend/manager.py`:1770 (write); `rm_client/ui/polar/mixins/config_actions_mixin.py`:315,323,385,389 (write); `rm_client/ui/polar/mixins/video_backend_mixin.py`:251,308,310,312,416,422,432,439 (write) |
| `hero_lob_preview_yaw_deg` (198) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:413 (write) |
| `hero_lob_preview_pitch_deg` (199) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:414 (write) |
| `hero_lob_flight_time_s` (200) | `rm_client/ui/polar/mixins/video_backend_mixin.py`:415 (write) |
| `fps` (201) | `rm_client/legacy_tk_app.py`:130 (write); `rm_client/ui/polar_app.py`:1206 (write) |
| `latency_ms` (202) | `rm_client/model.py`:475 (write) |
| `last_event` (203) | `rm_client/model.py`:556 (write) |
| `events` (204) | `rm_client/model.py`:557 (mutate); `rm_client/ui/polar/mixins/input_dispatch_mixin.py`:1364 (mutate); `rm_client/ui/polar/mixins/video_backend_mixin.py`:341 (mutate) |
| `start_time` (205) | `rm_client/ui/polar/mixins/persistence_mixin.py`:181 (write) |
| `robot_module_status` (208) | `rm_client/backend/manager.py`:1173 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:256 (mutate) |
| `robot_positions` (209) | `rm_client/backend/manager.py`:1156 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:412 (write) |
| `buffs` (210) | `rm_client/backend/manager.py`:1157 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:424 (write) |
| `penalties` (211) | `rm_client/backend/manager.py`:1158 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:436 (write) |
| `active_penalty` (212) | `rm_client/backend/manager.py`:1159 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:437 (write) |
| `radar_detections` (213) | `rm_client/backend/manager.py`:1160 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:279 (write) |
| `radar_detections_at` (216) | `rm_client/backend/manager.py`:1161 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:280 (write) |
| `path_plan` (217) | `rm_client/backend/manager.py`:1162 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:265 (write) |
| `tech_core` (218) | `rm_client/backend/manager.py`:1163 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:282 (mutate) |
| `performance_sync` (219) | `rm_client/backend/manager.py`:1164 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:240,242 (item); `rm_client/backend/referee/rm2026/state_adapter.py`:285 (mutate) |
| `deploy_mode` (220) | `rm_client/backend/manager.py`:1184 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:292 (write); `rm_client/ui/polar/mixins/video_backend_mixin.py`:263 (write) |
| `deploy_status_revision` (221) | `rm_client/backend/manager.py`:1185 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:291 (write); `rm_client/ui/polar/mixins/video_backend_mixin.py`:262 (write) |
| `deploy_status_at` (222) | `rm_client/backend/manager.py`:1186 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:293 (write); `rm_client/ui/polar/mixins/video_backend_mixin.py`:264 (write) |
| `rune_status` (223) | `rm_client/backend/manager.py`:1165 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:309 (mutate) |
| `sentry_status` (224) | `rm_client/backend/manager.py`:1166 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:315 (mutate) |
| `dart_status` (225) | `rm_client/backend/manager.py`:1167 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:317 (mutate) |
| `sentry_ctrl_result` (226) | `rm_client/backend/manager.py`:1168 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:319 (write) |
| `special_mechanisms` (227) | `rm_client/backend/manager.py`:1169 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:328 (write) |
| `injury_stats` (228) | `rm_client/backend/manager.py`:1174 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:342 (mutate) |
| `student_ui_data` (230) | `rm_client/ui/polar/mixins/student_frontend_mixin.py`:77,80 (item); `rm_client/ui/polar/mixins/student_frontend_mixin.py`:82 (mutate) |
| `injury_stats_initialized` (233) | `rm_client/backend/manager.py`:1175 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:367 (write) |
| `projectile_damage_small_initialized` (235) | `rm_client/backend/manager.py`:1176 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:368 (write) |
| `projectile_damage_large_initialized` (236) | `rm_client/backend/manager.py`:1177 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:369 (write) |
| `projectile_damage_small_total` (237) | `rm_client/backend/manager.py`:1178 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:370 (write) |
| `projectile_damage_large_total` (238) | `rm_client/backend/manager.py`:1179 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:371 (write) |
| `projectile_hit_revision` (239) | `rm_client/backend/referee/rm2026/state_adapter.py`:361 (write) |
| `last_projectile_hit_at` (240) | `rm_client/backend/manager.py`:1180 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:362 (write) |
| `last_projectile_damage_delta` (241) | `rm_client/backend/manager.py`:1181 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:363 (write) |
| `last_projectile_kind` (242) | `rm_client/backend/manager.py`:1182 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:364 (write) |
| `last_projectile_killer_id` (243) | `rm_client/backend/manager.py`:1183 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:365 (write) |
| `backend_stats` (244) | `rm_client/backend/manager.py`:616,679,1005,1697,1739,1771,1876,2279 (item); `rm_client/backend/manager.py`:1189 (mutate); `rm_client/backend/manager.py`:1952 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:210,333 (item); `rm_client/backend/referee/rm2026/state_adapter.py`:374 (mutate); `rm_client/backend/referee/student_legacy/state_adapter.py`:18 (mutate); `rm_client/backend/referee/student_legacy/state_adapter.py`:23 (item); `rm_client/ui/polar/mixins/input_dispatch_mixin.py`:1355 (item); `rm_client/ui/polar/mixins/video_backend_mixin.py`:1145,1159 (item); `rm_client/ui/polar_app.py`:1071 (item) |
| `robot_statics` (246) | `rm_client/backend/manager.py`:1170 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:221 (mutate) |
| `robot_dynamics` (247) | `rm_client/backend/manager.py`:1171 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:244 (mutate) |
| `robot_modules` (248) | `rm_client/backend/manager.py`:1172 (mutate); `rm_client/backend/referee/rm2026/state_adapter.py`:257 (mutate) |
| `backend_frontend_synced` (249) | `rm_client/backend/manager.py`:403 (write); `rm_client/backend/referee/rm2026/state_adapter.py`:72 (write) |
| `mock_simulation_enabled` (250) | `rm_client/backend/manager.py`:241 (write) |
| `official_state_active` (251) | `rm_client/backend/manager.py`:408,1152 (write) |
| `official_state_at` (252) | `rm_client/backend/manager.py`:409,1153 (write); `rm_client/model.py`:419 (write) |
| `polar_overlay` (257) | `rm_client/backend/manager.py`:937,1138 (write) |
| `polar_link_active` (258) | `rm_client/backend/manager.py`:877,931,1139 (write) |
| `polar_spin_note` (260) | `rm_client/backend/manager.py`:1022 (write) |
| `polar_link_age_s` (261) | `rm_client/backend/manager.py`:932,1140 (write) |
| `polar_drive_armed` (263) | `rm_client/backend/manager.py`:999 (write) |
| `polar_gimbal_client` (264) | `rm_client/backend/manager.py`:1000 (write) |
| `polar_authority_state` (265) | `rm_client/backend/manager.py`:1001 (write) |
| `polar_authority_reason` (266) | `rm_client/backend/manager.py`:1002 (write) |
| `polar_motion_state` (268) | `rm_client/backend/manager.py`:990 (write) |
| `polar_motion_detail` (269) | `rm_client/backend/manager.py`:991 (write) |
| `polar_segments_received` (270) | `rm_client/backend/manager.py`:933 (write) |
| `polar_dropped_segments` (271) | `rm_client/backend/manager.py`:934 (write) |
| `gimbal_pitch_deg` (273) | `rm_client/state_overlay.py`:24 (overlay) |
| `imu_pitch_deg` (274) | `rm_client/state_overlay.py`:26 (overlay) |
| `imu_roll_deg` (275) | `rm_client/state_overlay.py`:27 (overlay) |
| `chassis_yaw_rate_rps` (276) | `rm_client/state_overlay.py`:22 (overlay) |
| `measured_vx_mps` (277) | `rm_client/state_overlay.py`:29 (overlay) |
| `measured_vy_mps` (278) | `rm_client/state_overlay.py`:30 (overlay) |
| `measured_wz_rps` (279) | `rm_client/state_overlay.py`:31 (overlay) |
| `chassis_error` (281) | `rm_client/state_overlay.py`:68 (overlay) |
| `gimbal_error` (282) | `rm_client/state_overlay.py`:70 (overlay) |
| `shoot_error` (283) | `rm_client/state_overlay.py`:72 (overlay) |
| `drive_ready` (284) | `rm_client/state_overlay.py`:69 (overlay) |
| `armor_id` (285) | `rm_client/state_overlay.py`:41 (overlay) |
| `launch_frequency_hz` (286) | `rm_client/state_overlay.py`:46 (overlay) |
| `bullet_type` (287) | `rm_client/state_overlay.py`:43 (overlay) |
| `recovery_buff` (288) | `rm_client/state_overlay.py`:61 (overlay) |
| `vulnerability_buff` (289) | `rm_client/state_overlay.py`:64 (overlay) |
| `report_cooling_buff` (290) | `rm_client/state_overlay.py`:62 (overlay) |
| `rfid_status` (291) | `rm_client/state_overlay.py`:63 (overlay) |
| `polar_debug_packages` (292) | `rm_client/backend/manager.py`:935,1141 (write); `rm_client/state_overlay.py`:79,102 (overlay) |
| `polar_self_x` (295) | `rm_client/state_overlay.py`:34 (overlay) |
| `polar_self_y` (296) | `rm_client/state_overlay.py`:35 (overlay) |
| `polar_self_angle_deg` (297) | `rm_client/state_overlay.py`:33 (overlay) |
| `polar_ally_hp` (298) | `rm_client/backend/manager.py`:1143 (write); `rm_client/state_overlay.py`:78,100 (overlay) |
| `polar_ground_positions` (299) | `rm_client/backend/manager.py`:1144 (write); `rm_client/state_overlay.py`:80 (overlay) |
| `polar_gold_coin` (300) | `rm_client/state_overlay.py`:53 (overlay) |
| `polar_fortress_allowance` (301) | `rm_client/state_overlay.py`:50 (overlay) |
| `polar_game_progress` (302) | `rm_client/state_overlay.py`:51 (overlay) |
| `polar_game_type` (303) | `rm_client/state_overlay.py`:52 (overlay) |
| `polar_stage_remain_s` (304) | `rm_client/state_overlay.py`:36 (overlay) |
| `polar_event_bitmap` (305) | `rm_client/state_overlay.py`:49 (overlay) |
| `polar_warning_level` (306) | `rm_client/state_overlay.py`:56 (overlay) |
| `polar_warning_robot_id` (307) | `rm_client/state_overlay.py`:57 (overlay) |
| `polar_reported_robot_id` (310) | `rm_client/state_overlay.py`:54 (overlay) |
| `polar_warning_count` (311) | `rm_client/state_overlay.py`:55 (overlay) |
| `polar_module_errors` (312) | `rm_client/backend/manager.py`:936,1142 (write) |

Only this new documentation file was written. No source, tests, tools, configs,
evidence, or baseline files were modified by this audit. No raw logs were added.
