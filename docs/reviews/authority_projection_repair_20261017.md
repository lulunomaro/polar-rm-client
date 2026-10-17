# Official-feed authority projection repair, 2026-10-17

## Source and scope

Accepted source: `4734c31dff4e5f628237cc49b93c7d79b4a586f8`.
Public counterpart: [`cb66f1f9985e362d775aff64884c912d520b67bf`](https://github.com/lulunomaro/polar-rm-client/commit/cb66f1f9985e362d775aff64884c912d520b67bf).
The integration owner verified the GitHub ref at 18:35 UTC and reported the same
tree `4051067b5849e45802d3a97c939704ef81fbd897` as local source `4734c31`.
The commit IDs differ because the publication commit uses the remote parent;
the public commit is not an object in this local checkout. This document records
the bounded repair, not a passing full aggregate or native/device qualification.

Changed code and regression:

- `rm_client/combat_events.py`
- `rm_client/ui/polar/mixins/video_backend_mixin.py`
- `tests/test_official_feed_projection_authority.py`

## Reproduced failures

Valid payloads were independently encoded and accepted by the real selected
codec, then passed through decoded adapter and raw-feed projection in production
order. The later raw projection previously undid decoded presence semantics:

- GameStatus score-only `1807` preserved MATCH/countdown in decoded state, then
  replaced them with NOT_STARTED/0.
- RobotDynamicStatus ammo-only `482a` preserved existing health, then set it to
  zero, along with unrelated heat and permission flags.
- GlobalUnitStatus field 6 `30d209` is enemy-base health 1234 in the current
  reviewed contract. The raw fallback instead replaced the robot-health array
  with `[1234]`; a later base-health zero could create a false confirmed robot
  elimination. Legacy field 6 has a different meaning, but its unverified roster
  mapping is intentionally diagnostic-only in the legacy state adapter.
- Partial logistics messages could zero balances and fabricate an economy
  status revision. Signed negative countdown became a large unsigned value.
  Partial respawn updates overwrote the adapter's accumulated progress.

## Repair contract

The raw feed now publishes a projected field only when its required wire field
is present (`combat_events.py`, `_STATE_UPDATE_FIELDS` and `consume`). Explicit
zero and false remain updates. Signed int32 countdowns retain the decoded
adapter's nonnegative display clamp. Raw respawn progress requires both operands;
otherwise the decoded adapter's accumulated value remains authoritative.

Current-profile roster interpretation uses field 11 only. Legacy roster input
is not projected into current health or elimination inference. The UI passes
its actual referee profile; a changed profile resets event/roster history.
The repair does not guess a schema from bytes. Existing real field-11 health
zero transitions still produce elimination events.

## Validation and independent review

Before production edits, the first nine new test methods reported 12 failures
including subtests; explicit-zero and genuine roster-transition controls already
passed. After the fix, all **10** methods passed, including the added profile
handoff case. Tests invoke actual codec, adapter, OfficialMatchFeed, and
VideoBackendMixin methods with inert collaborators; no AST replacement methods
are used in the regression file.

Independent review reported acceptance at the exact hashes below. In addition
to rerunning the 10 tests, it reproduced the three primary failures before the
fix and verified the repaired actual-window offscreen chain:
`backend.apply_updates` -> `consume_official_messages` -> `apply_official_state`.
It also checked 49 supported profile/empty-message branch combinations without
KeyError, legacy versus current omission semantics, and explicit-empty roster
retention. These branch checks are not claims that direct invalid feed calls
constitute valid transport input.

Author-run neighboring validation: 50 methods across state-overlay, referee
frontend projection, projection policy and window tick contracts; the existing
`test_official_economy_status_decode`; and unchanged API freeze all passed.
API freeze emitted offscreen GL/audio-unavailable warnings and does not qualify
physical output. No baseline files were changed.

Reproduction commands, using the prepared verification interpreter as `$PYTHON`:

```sh
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen CI=true \
  "$PYTHON" tests/test_official_feed_projection_authority.py
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen CI=true \
  "$PYTHON" -m unittest tests.test_state_overlay_contract \
  tests.test_referee_frontend_projection_contract \
  tests.test_referee_projection_policy tests.test_window_tick_contract
```

Reviewed file SHA-256 values:

- combat_events.py: `cb2351ba552a8e536d860dc74dc1f7065d939b62ef9ae85ede876bf707ff0a5d`
- video_backend_mixin.py: `fede90da462698161bc2dd156115f047a78f34eb0c2b5229c56dccb871cadc1e`
- test_official_feed_projection_authority.py: `c6cdb04fa81271f56e1404a5a78d8a1e97a8e6f55da3b8181b46ee8256b76ed0`

## Limits

This accepts the named presence/schema counterexamples, not universal agreement
between projections. Transport decoding/validation remains upstream; direct
malformed-feed behavior was not redesigned. Receive-time versus consumption-time
freshness, other combat-event interpretation, per-field POLAR expiry, all-field
coverage, full integrated regression and native hardware remain separate.
Live/stale POLAR allowlist precedence is exercised by the focused regression;
no physical actuation is inferred.

Only documentation was written in this reporting stage. No new raw logs or
archives were published, and no source, tests, tools or configs were changed
while the full regression was running. The verifier's source scope excludes
`docs`; the original ledger digest remains historical and a separate static
rescan digest is recorded in that ledger.

## Complete integrated offline result

Exact local source `4734c31dff4e5f628237cc49b93c7d79b4a586f8` / public
`cb66f1f9985e362d775aff64884c912d520b67bf`, tree
`4051067b5849e45802d3a97c939704ef81fbd897`, completed **335 PASS / 12 SKIP /
4 INCOMPLETE / 0 FAIL**. Aggregate remains **NO-GO** because external reference,
firmware and installation prerequisites are incomplete. No hardware acceptance.

All 684 declared source inputs retained start/end SHA-256
`c1b20ad95c7f942be0c17e87ecc10bb9bb265018c9ddd587aab27657df8b10e4`.
This includes early-factory tests, descriptor ownership, retry reservation and
portable preflight environment isolation. Windows/PowerShell execution remains
unperformed; source-contract checks are not a substitute. New raw logs are kept
local, not published. Future source changes require their own result.
