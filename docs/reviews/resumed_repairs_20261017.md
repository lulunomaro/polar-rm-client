# Resumed repairs and acceptance, 2026-10-17

## Latest corrected-source result

Local `2bcf4b4595dd5a261cb4e4c98b1ac2fb8e209ea6`, equivalent public source
`f4df6bb9e15942ed5b9c7c884d7846c93d7d67ea`, tree
`4a2f7c3210bf5d981246b15eb7633680f9f127b3`, completed the full selected offline
profile: **329 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL**. Aggregate remains
**NO-GO** for the four prerequisites below. All 678 declared source inputs
retained start/end SHA-256
`09a2302cce10a5ff1beb59bca72585ae5c71b61926f22e8d41346e9aaf6fa922`.

Correction compares newly occupied physical keys against the complete previous
physical-key set, preserving slot permutations. Both previously failing test
files remain unchanged. Six focused suites passed 66 methods; a separate reviewer
reran the original 25 methods and seven new methods and checked 1,000 deterministic
permutations. Those focused counts are separate from the aggregate suite counts.
Later documentation-only commits do not change these tested sources.

## Scope

Public candidate `5db330f771e4b1d2f813a88e887185d58199ec10` has the same
tree `87193e8519bd1bb19fa5d99a0e959dc53afcaaa2` as local tested commit
`97228a73b90e7db73533af7e07a8f5a7e8fb0716`.

Four bounded repairs were implemented with inert/offscreen reproduction:

- `KeyboardControlBridge.release_all`: reset/publication failure no longer skips
  independent neutral attempts; preserve first exception and successful return.
- Lock cleanup: nested cleanup cannot release the outer input-blocking owner.
- Controller-angle admission: ordinary start exceptions do not retain a phantom
  worker; rejected predecessors cannot erase a reentrantly admitted successor.
- Preferences: reject newly introduced physical control/module key collisions.

Independent reviewers checked the specific patches and focused tests. Their
results are narrow and do not qualify physical neutral delivery or the project.

## Complete candidate result: rejected

The selected offline profile completed with **327 PASS / 12 SKIP /
4 INCOMPLETE / 2 FAIL**, aggregate **NO-GO**. Both failures are settings binding
compatibility regressions:

- `test_binding_restore_quality`: saved swap/cycle and new-window restoration.
- `test_configuration_transactions`: whole binding-map canonicalization.

The initial collision guard incorrectly classified relocation of an already
shared physical key between control slots as a new collision. A correction must
preserve both original tests unchanged. A later green run cannot replace this
candidate's failed result.

All 678 declared verification input files retained start/end SHA-256
`e2edb124e3c58c4ce3e04e170fdc012bced6748d5267232a59df707440a67866`.
Documentation is outside this fingerprint. Raw logs are retained locally and
are not newly published here.

## Architecture close-out

- [RobotState authority ledger](robot_state_authority_audit_20261017.md): all
  242 declared fields inventoried, with bounded static references and explicit
  unresolved fields. This is not runtime semantic coverage.
- [Early resource ledger](window_early_resource_audit_20261017.md): acquisition,
  publication and cleanup boundaries, including untested early failure prefixes.
- [Principle acceptance](clean_code_principle_acceptance_20261017.md): the supplied
  Clean Code chapters mapped to concrete evidence and remaining gaps.
- [Evidence storage](evidence_storage_audit_20261017.md): lossless preservation
  plan; no bulk evidence removal or archive migration has occurred.

## Unresolved historical and external evidence

The historical 30-second activation timeout has a retained parent traceback but
no original child stack/output that locates the stall. Current diagnostics and
later passes do not establish causation.

The recorded DT7 22.028550 ms observation exceeded the unchanged 20 ms limit.
The original referenced local raw output is unavailable; later isolated passing
measurements do not explain it. Scheduling/contended caller work remain
hypotheses, not confirmed causes.

The four incomplete checks require the external team CRC reference, firmware
tree/host reference and installation preconditions. Native Windows, GPU, real
audio/video/serial and portable-package qualification remain external. Recorded
offscreen evidence is Linux; it must not be presented as Windows acceptance.
