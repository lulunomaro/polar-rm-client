# Field-log worker admission repair — 2026-10-17

## Result and checkpoint

The repair extends existing worker-admission failure accounting to ordinary
exceptions beyond `RuntimeError`. It preserves the distinction between a
never-started writer, an active or already-finished writer, and a successor
writer. Recorded failure/loss cannot be reported as intact merely because the
queue later drains. This is not disk or power-loss durability certification.

- Local commit: `197f4284e59efc432750e1cf6051c5b8f0e5ed18`.
- Public counterpart supplied by parent verification:
  [`a44cf8d477b9733bdfdcd43f9b671260c18d1505`](https://github.com/lulunomaro/polar-rm-client/commit/a44cf8d477b9733bdfdcd43f9b671260c18d1505).
- Git tree independently checked locally:
  `daebc6193463f7bc74e24d4f84c106e50959c4ec`.
- Parent commit: `255093e17a5cb1b1ec22a8b3093eb2bc4074436f`.

The subsequent full offline regression completed: **346 PASS / 12 SKIP /
4 INCOMPLETE / 0 FAIL**, overall **NO-GO** (`passed=false`). No duplicate full
run was started for this report; skipped/incomplete checks remain unaccepted.

## Failure boundary and narrow correction

[`RotatingJsonlLogger.emit`](../../rm_client/backend/field_log.py) accepts and
queues a sanitized record before constructing/starting the writer. Previously,
only `RuntimeError` entered `_record_admission_failure`; ordinary `OSError` or
`ValueError` could escape, bypassing truthful admission/error accounting.

The production diff changes three lines: the constructor catch and start catch
now catch `Exception`, and the existing accounting helper's error annotation
matches. This is a bounded worker-admission exception boundary, not a blanket
catch around logging/application behavior. It does not catch `BaseException`.

The existing helper runs under the state lock and records the error. It only
discards queued records and clears pending counts when the failed owner is
still the current owner and has never acquired a thread identity (or construction
failed before an owner existed). Those discarded records count as write loss;
accepted records are not silently erased from accounting.

A start wrapper can raise after starting a real thread, after that thread has
finished, or after a successor has been admitted. Such an exception is not proof
that ownership disappeared. The helper preserves active work and completed
counts; its owner-identity check prevents an old failure from clearing a
successor's queue or worker. A later successful admission also does not erase
prior write-loss/error history.

`flush`/`close` can consequently return false even after pending counts reach
zero. `AuditCloseStatus.drained` and `.intact` communicate different facts:
retirement/drain is not the same as error-free preservation. The production
`flush` documentation explicitly disclaims power-loss durability, and an
already-running OS write cannot be interrupted by close.

## Completed full offline regression

The stage's `results.json` was read directly. Run
`b9108a31ebd1461c93affd616b821afc`, started 2026-10-17 20:29:29 UTC,
records `run_state=completed`, `passed=false`, and **346 PASS / 12 SKIP /
4 INCOMPLETE / 0 FAIL**. Its provenance pins the local commit/tree above;
the public counterpart is the separately verified publication identified above.

Declared source stability is PASS: **695 inputs** have identical start/end
SHA-256 `b448096787d185cda7ad5ccf3df23227997c19a350112502d7daf7d1d23d2625`.
The fingerprint covers selected source/configuration files in `rm_client`,
`tests`, `tools`, `configs`, and root entry/requirements files. It excludes
documentation, assets, external fixtures and installed packages; endpoint
comparison cannot detect transient edits restored between captures.
Zero failures do not establish complete acceptance, physical storage durability,
or hardware qualification.

## Focused verification

The independent logs were inspected:

- New regression against the baseline: **12 failures, 0 errors**. Its test helper
  converts escaping admission exceptions into assertion failures, so this is
  not a claim that the baseline raised no exceptions internally.
- Candidate independent focused group: **35 tests passed**.

The [new regression](../../tests/test_field_log_ordinary_admission.py) executes
six scenarios for each of `OSError` and `ValueError`: constructor rejection,
start rejection, later admission retaining prior loss, active writer after a
late error, completed writer after a late error, and successor preservation.
Real threads exercise the uncertain ownership cases; temporary directories and
inert write recorders keep the assertions independent of physical storage
qualification. Existing lifecycle-quality tests remain in the focused group.

Reviewed SHA-256 values:

| File | SHA-256 |
|---|---|
| `rm_client/backend/field_log.py` | `dbbb8ad5d5b7b4a0d6bb548a06cf1dc33aa347b8897b45d689021fdb8422ee54` |
| `tests/test_field_log_ordinary_admission.py` | `5e23f313c2af593e6060a3e6d42d2d76649bfa6e8b0d884bb842c136a3b383aa` |

This report changes documentation only. No source or tests were altered for
the report, and no raw logs are published. The completed full-run result above
is distinct from focused-test success; physical storage durability and hardware
qualification remain outside this evidence.
