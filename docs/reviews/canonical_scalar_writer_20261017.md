# Canonical referee scalar writer, 2026-10-17

## Source and decision

- Accepted local source: `2f95679285b5b16eb38c9c16dfcf5b5967c7d7d3`.
- Published source: `e1fed5dc11e6c4d3b547178f36e0af93e5fa9dac`.
- Shared source tree: `6dc24020fcbfeec9ec8da426aabf259fe5376aa2`.
- Historical characterization: local `b65d7ec3013d493765252b490a65a962c7f28691`,
  public `58fb075`.

Remove the duplicate scalar application from the normal window's raw-message
consumer, retaining the decoded backend as that path's referee scalar writer.
This is a bounded simplification of an existing handoff, not a new service layer
or a claim that all RobotState fields have one global writer.

## What the baseline established

`tests/test_official_scalar_writer_characterization.py` initially had eight
passing characterization methods. Real validated MQTT queues, backend application
and an actual offscreen window demonstrated these authority differences:

1. A packet arriving after `backend.apply` but before `official.consume` could
   publish newer raw roster health while canonical frontend rows remained old.
2. A failed adapter application or a transition-blocked backend could still be
   followed by raw scalar publication. The raw path acted as a fallback without
   the canonical adapter's associated processing.
3. `air_support_can_free` was supplied only by the raw path, although the adapter
   already owned the free-time scalar. Blindly removing raw writes would lose
   that economy eligibility derivation.

The same checks showed adapter ownership of accumulated partial respawn progress,
ordinary economy revisions and native PDL1 telemetry. Combat-event acceptance and
receipt freshness were separate effects that must remain.

## Production change and intentional expectation changes

`rm_client/ui/polar/mixins/video_backend_mixin.py::consume_official_messages`
now queues only `official_feed_at` from `OfficialMatchFeed.consume` updates.
It still accepts every admitted combat event. The decoded adapter and subsequent
`sync_frontend_state` retain scalar/row ownership in the normal tick.

`rm_client/backend/referee/rm2026/state_adapter.py` now derives
`air_support_can_free = air_support_free_time_sec > 0` only when decoded
`left_time` is present. Explicit zero clears eligibility. The student legacy
adapter inherits this derivation after its reviewed field-name conversion.
Current-profile omitted `left_time` preserves eligibility; legacy codec default
materialization marks its named fields present, so omitted wire `left_time`
becomes canonical zero there. This change does not impose current-profile wire
presence rules on legacy preview.

The characterization file was deliberately converted into ten acceptance methods:
inter-stage raw arrivals must leave scalar health unchanged until the next
canonical application; adapter failure and transition blocking must not acquire a
raw scalar fallback; free-air eligibility must already be correct after adapter
application. Baseline commit `b65d7ec` preserves the old expectations. This is an
explicit correctness change in timing/failure behavior, not timing equivalence.
Receive-time safety handling already performed by the MQTT callback remains in
place; this report does not claim that all safety updates await the UI tick.

## Compatibility and evidence

- `OfficialMatchFeed.consume` still returns its public scalar update dictionary.
- Explicit callers of `apply_official_state` may still supply scalar updates;
  its compatibility behavior was not removed.
- Receipt-based freshness, event delivery/counters and native CustomByteBlock
  decoded telemetry remain covered.
- Ten desired acceptance methods passed. Loading complete baseline modules in
  memory and installing their two former production methods yielded seven
  assertion failures and zero errors against those desired tests. This was a
  bounded in-process baseline comparison, not an entire baseline checkout run.
- Implementer focused run: **34 PASS** (10 acceptance plus 24 projection,
  freshness and identity neighbors). Independent review: **40 PASS**, including
  numeric-boundary neighbors. Frozen source hashes matched and diff checks passed.
- The aggregate run was in progress at documentation time. No aggregate score or
  completion is asserted here; its result belongs to the integration record.

## Remaining boundaries

`official_events_online` and `official_feed_at` describe packet receipt freshness,
not successful scalar application. A failed adapter can coexist with a fresh
receipt and accepted event. There is no atomic whole-state commit, rollback or
transactional readiness certificate. Per-message adapter mutations before an
exception are not rolled back by this change.

Other ordered writers remain, including demo state, allowlisted POLAR overlays,
configuration/user actions and explicit compatibility calls. No exhaustive queue
overflow/interleaving proof, real transport qualification, native driver/audio
qualification or whole-client clearance follows from the focused offscreen tests.
The public raw decoder remains for compatibility and events; only its production
window scalar publication was removed.

## Integrated candidate and narrow oracle follow-up

The full selected offline profile at local
`2f95679285b5b16eb38c9c16dfcf5b5967c7d7d3` / public
`e1fed5dc11e6c4d3b547178f36e0af93e5fa9dac` completed
**342 PASS / 12 SKIP / 4 INCOMPLETE / 2 FAIL**. All 693 declared inputs retained
SHA-256 `3df5f38f81b23d4b67c87cdd4eb75420004068af3ef2219ac940f6d7afa563d1`.
This is a rejected candidate result, retained as evidence.

Failures were the old exact adapter AST pin and two frozen AirSupport ordered
write traces. The follow-up preserves both historical JSON fixtures and their
hash guards. It pins the entire exact successor adapter AST, and permits only
the documented True/False eligibility writes after the unique frozen free-time
write in the two named full/explicit-zero cases. All remaining observation,
ordering and error comparisons stay intact. The historical snapshot field list
omitted eligibility; desired canonical-writer tests separately check final state.

Independent follow-up passed 25 focused methods. Wrong boolean, missing presence
guard and reordered write each failed mutation checks with two failures and zero
errors. No production changes were made to satisfy these old-oracle checks. A new
complete result is required for the follow-up source; none is claimed here yet.

## Corrected complete result

Local `12e86e9241c7aa655dc326048e83512364259a68` equals public source
`b59b042847f7f2f2d0e0f4c71acfd5151db20cb7`, tree
`91d0f28cd7309db2e17c01da1f5b16a626080795`. The selected offline profile
completed **344 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL**. All 693 declared
inputs retained SHA-256
`59dba361dc56ad54150bb3d0becb82cb9b8ff6bbde1ee293fac7d0408c8fa282`.
Aggregate remains **NO-GO** for external prerequisites. The preceding rejected
342/12/4/2 result and historical fixture identities remain above. No native
Windows/GPU/audio/robot/portable qualification or transactional-state-freshness
guarantee follows from this result. Report-only follow-ups change no source.
