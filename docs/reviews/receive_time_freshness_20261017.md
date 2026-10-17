# Receipt-time freshness repair, 2026-10-17

## Exact completed checkpoint

Local source `2a2fe5ce5adaed40a3544789768d077169723108` equals public source
`5a1c564f2db460b25d086361a99e985e963463c7`, tree
`ab1ca25eb8808d64cbe4c5c8264075de8eb2987e`.
Complete selected offline profile: **336 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL**.
Aggregate is **NO-GO**, not whole-project or native qualification.
685 declared inputs retained SHA-256
`62729531c533a886b347dd6fa9f5599f7c6b18ba3804bb9e7fa0dfa4b5ae274e`.

## Reproduction and change

Validated raw MQTT queues record local monotonic receipt time. The UI discarded
it and the event feed used processing time. A four-second-old queued event could
therefore acquire a new timestamp and pass the existing fresh-referee readiness
check after a UI delay. Independent actual queue/window probes reproduced this.

The UI now forwards receipt time. Explicit invalid, future, or older-than-2.5s
receipts are rejected before raw projection/event freshness changes. An omitted
argument preserves direct-call compatibility. Events retain their own receipts;
latest freshness clocks never move backward for eligible out-of-order receipts.
The existing stricter 2s readiness gate is unchanged. Profile handoff clears old
history even when the new receipt is invalid.

Nine focused freshness tests plus ten projection tests passed independently.
The projection fixture's artificial timestamp 1.0 was replaced by fresh monotonic
time; all existing state assertions remain. API freeze passed unchanged.
Initial baseline had real delayed-message failures plus expected unsupported-new-
argument errors; those are not all independent behavioral defects.

## Limits and remaining work

This is raw-feed freshness, not rejection of every old decoded state snapshot.
Connection, E-stop, explicit qualification, and device egress gates remain
separate. No physical hardware, Windows desktop, audio, GPU or portable-package
validation was performed. Four incomplete checks still require external CRC,
firmware and installation prerequisites. Historical activation timeout and DT7
strict-budget failure causation remain unproven. Raw logs remain local.

Related: [architecture principle acceptance](clean_code_principle_acceptance_20261017.md),
[authority projection](authority_projection_repair_20261017.md),
[early resource ledger](window_early_resource_audit_20261017.md).
