# RobotState ownership and observation ledger

Inspected base: `666c040`. This ledger describes actual writes and retention,
not an assertion that RobotState has a single authoritative writer. No broad
model rewrite or source-generation redesign is included in this checkpoint.

## Producer and projection groups

| Group / fields | Actual producer and boundary | Competing writes / retention |
| --- | --- | --- |
| Robot selection / performance (`robot_id`, `robot_profile`, `level`, `max_health`, `max_heat`, `power_limit`, ammo capacity) | `robot_profiles.apply_profile_to_state` writes operator/profile defaults; RM2026 `RobotStaticStatus` applies present referee identity/performance fields | Profile application also sets current HP/ammo to profile defaults. Referee identity may replace the current local ID while per-ID dictionaries retain old entries. POLAR reports its identity separately as `polar_reported_robot_id`. |
| Local HP/heat/energy/ammo (`current_health`, `current_heat`, `current_chassis_energy`, `current_buffer_energy`, `remaining_ammo`) | `Rm2026StateAdapter._apply_one` publishes present dynamic fields and `robot_dynamics[current robot_id]` | `RobotState.tick` updates demo heat/energy/ammo only while mock enabled and official state inactive; profile/economy mock effects also write some scalars. Active POLAR overlays selected HP/heat/energy fields last. |
| Team HP / ammo vectors | RM2026 `GlobalUnitStatus` stores `official_robot_health` and `official_robot_bullets`; local projectile allowance maps role order `(1,2,3,4,7)` | POLAR uses separate `polar_ally_hp`; StudentLegacy deliberately stores unverified roster vectors in diagnostics rather than guessing this order. Scalar projectile allowances can subsequently be overlaid by POLAR. |
| Frontend table / map / buffs | `Rm2026StateAdapter.sync_frontend_state` derives `robot_rows`, `map_robots`, `buff_rows` from official vectors and per-ID observations | Demo tick creates synthetic rows/map/buffs; manager clears demo projections when official state begins or scenario resets. Projection retains previous map/buff output when corresponding source lists are empty. Row publication occurs before map and buff publication; map re-observes dynamic state afterward. |
| Match / base / logistics / respawn fields | Annual adapter applies field-presence-aware official messages; stage update has safety/log effects | `OfficialRefereeFeed.consume` also returns raw-message scalar updates; frontend `apply_official_state` assigns them later in the same tick. Demo tick and POLAR base/outpost telemetry also overlap subsets. This duplicate frontend path requires separate presence/ownership audit; it is not removed here. |
| Position / module / buff observations | Annual adapter upserts `robot_positions`, `robot_modules`, `robot_module_status`, `buffs`; `RadarInfoToClient` replaces radar detections with arrival timestamp | Per-ID caches are not freshness-tagged per sample. Upserts preserve absent fields. Radar has its own TTL consumer; table/map HP has no per-robot arrival-clock comparison. |
| POLAR telemetry | `PolarStateBridge.apply` accumulates segment overlay; manager copies it; `RobotState.apply_polar_overlay` applies `validated_polar_overlay` allowlist | Overlay cannot write operator authority, methods, official containers, or arbitrary fields. Bridge link freshness is aggregate across handled segments, not per overlay field. Stopping refresh disables application but does not erase every prior scalar. |
| Native telemetry / vision | Manager `_apply_native_telemetry` writes `native_*` timestamps, sequence, pose and bullet observations; frontend vision synchronizers own `vision_*` | POLAR shooting telemetry also writes `native_bullet_speed`. Native localization reset revokes hero-lob trust before diagnostics. No source arbitration for every similarly named scalar is asserted by this ledger. |
| Control / safety state | Manager and safety/control policies own arm, estop, gimbal egress and `polar_*` authority diagnostics | Frontend HP/map selection has no permission to arm, command, or emit wire messages. Model presentation values are not independent control-authority evidence. |

## Ordered runtime writes

`PolarRMWindow._advance_runtime_tick` performs `state.tick` before backend tick
and `apply_updates`; subsequent stages include official raw-message consume/apply,
vision/event updates, registry tick, MQTT sync and finally POLAR link overlay.
This is real last-writer ordering, not a guarantee that source freshness is
comparable across protocols. Existing order is preserved in this checkpoint.

## Epoch and freshness evidence

- MQTT `_on_message` captures client/profile/receive generation and rechecks all
  three before admitting decoded and raw queues. `clear_received_epoch` increments
  generation and clears both queues. An already accepted model value is not
  automatically erased merely because its connection goes stale.
- Manager `_begin_official_state` disables synthetic simulation on the first
  accepted official-message attempt and updates `official_state_at`. This is a
  stream-level timestamp, not a timestamp attached to every field or robot.
- `_reset_state_for_scenario` clears official vectors/per-ID caches/projections,
  POLAR overlay/source containers and injury baselines. It does not reset every
  scalar to a new value. Profile change invokes the same reset before publishing
  a new annual adapter identity.
- POLAR transport invalidates open attempts by generation and clears RX/control
  state on connection lifecycle changes; manager also resets its bridge during
  scenario/shutdown paths. The bridge's freshness clock is not a per-segment
  freshness map. No change to this lifetime contract is made here.
- `official_feed_at` and `official_events_online` belong to the combat-event feed;
  they are not interchangeable with manager official-state freshness or a proof
  that all projection fields are current.

## Confirmed defect and corrected observation boundary

Real codec/adapter producer sequence: local ID3 (or103) receives dynamic HP275;
its position is observed; `RobotStaticStatus` changes local identity to4 (or104);
a subsequent official global vector reports the old robot's HP as zero. Both
frontend table and map previously selected `global_hp or dynamic_hp`, resurrecting
275 HP from the retained former-local observation. The source vector itself was
correct. The defect is selection of missing versus explicitly zero data.

`select_projected_health` now uses None for missing observations:

1. Present dynamic HP wins for the current local robot, including zero.
2. Otherwise a present global HP observation wins, including zero.
3. A missing global slot may still use retained dynamic HP; both absent yields
   the existing zero display default.

This is a deliberate bug correction, not a claim of timestamp arbitration.
Conversions/clamping, alliance role mapping, per-ID caches, publication ordering,
map re-observation, and source objects remain unchanged. No control/wire behavior
is changed. The old frozen oracle remains byte-for-byte intact. A separate test
shows the documented old/new HP and DEAD-state delta and equality of all other
model output; four source-selection mutants are rejected by real-producer tests.

## Residual work

- Audit duplicate raw combat-feed scalar projection against annual optional-field
  presence and profile contracts before changing it. No failure is claimed here
  solely from overlap.
- Decide explicit field-group freshness requirements before adding timestamps or
  erasing retained values. Replacing missing with zero globally would introduce
  false-death observations and is not a valid cleanup shortcut.
- Native/device/match qualification remains external. These tests use encoded
  fixtures and real software producers, not robots or network services.

## Reproducible evidence for the HP correction

Use `/workspace/shared/client-clean-venv/bin/python` (3.12.14), repository root,
with `QT_QPA_PLATFORM=offscreen CI=true PYTHONPATH=.`. Red checkpoint `d304fb0`
runs five producer tests; the red and blue former-local death subcases fail with
`(275, ALIVE, 275)` rather than `(0, DEAD, 0)`. Captured output is
`docs/evidence/state-projection-20261017/health-zero-red.txt`.

After correction:

- `tests/test_referee_health_source_selection.py`: 8 methods pass, including
  present/missing/local source matrix, exact frozen-oracle delta and four rejected
  behavioral mutants (truthiness fallback, global always, local zero as absent,
  and discarded historical fallback).
- `tests/test_referee_frontend_projection_contract.py`: 12 pass unchanged,
  including observed read/write/failure prefixes and map re-observation.
- `tests/test_referee_state_characterization.py`: 7 pass unchanged.
- `tests/test_state_overlay_contract.py`: 11 pass unchanged.
- `tests/polar_bridge_test.py`: passes unchanged.
- `tools/quality_gate.py`: unchanged quality baseline passes, 219 modules,
  3429 functions, no initialization cycles.
- `git diff --check`: passes.

The broader `test_referee*py` discovery initially failed only the domain test's
whole-adapter AST digest because a deliberate semantic correction changes code.
The historical fixture remains unchanged. The test now asserts the old digest
and admits exactly the reviewed corrected digest for this one class; it does not
exclude the adapter or its projection method. All other AST pins and behavioral
oracles remain unchanged. Final discovery output is recorded separately as
`docs/evidence/state-projection-20261017/referee-green.txt`.
