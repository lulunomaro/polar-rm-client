# Referee event identity repair

Exact local source `c110fa0dc9f8fceda1d777416f989b9c67c979da` equals public
`8140742e3ee129f176efd86a607bb9573fc1d941`, tree
`67736ba5c566bee1a6e39f0e98cc87a97d9dccec`.
Complete selected offline profile: **342 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL**.
Aggregate remains **NO-GO**. All 691 declared inputs retained start/end SHA-256
`01035316db864b80f6af22f8bacb2930466e72969f4316d37ecde02a3d18d316`.

## Reproduced defects and bounded correction

A schema-valid RobotStaticStatus carrying alive_state without robot_id previously
invented victim0 and a confirmed elimination. Identified static events now require
explicit fields3 and4 and an ID in the existing protocol ranges1..9/101..109.
Explicit local-death handling is preserved. The existing alive_state==2 condition
is unchanged; its enum meaning is not independently qualified by this review.

The HP transition feed inferred the own/enemy split from array length. A short
five-entry array could therefore reinterpret an allied index as an enemy; excess
entries wrapped role IDs. The fixed wire order uses five own roles and five enemy
roles, each ordered(1,2,3,4,7), matching the canonical projection. Missing entries
remain unknown, and tails beyond ten cannot create mapped victims.

Five real-codec/adapter/offscreen-window regression methods cover both alliances,
all enemy roles, missing/invalid static identities, explicit local death, partial
vectors, missing observations and excess tails. Independent complete baseline
module substitution reproduced24 assertion failures with zero errors. Candidate
identity5 + projection10 + receipt-freshness9 methods passed independently.
Existing API freeze remained unchanged.

## Limits

This does not certify every Event or CustomByteBlock interpretation, cumulative
status semantics, protobuf enum meaning, native devices or real match behavior.
The declared current roster contract is not a generic future-year mapping.
Raw logs remain local. Missing external CRC/firmware/install prerequisites,
Windows/GPU/audio/portable validation and historical timing causation stay open.
