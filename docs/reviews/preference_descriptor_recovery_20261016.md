# Temporary descriptor ownership on rejected stream admission

This is a separate correction after the accepted audio attempted-write stage
`362133a` and its evidence `c065a29`; it is not included in that stage's claims.

## Reproduction and ownership

A real `mkstemp` allocates a descriptor before `os.fdopen` owns it. Injecting an
ordinary rejection from `fdopen` leaves `os.fstat(descriptor)` successful after
`atomic_write_bytes` raises, even though its temporary pathname is removed.
The reviewer probe closes the leaked descriptor itself. This is defensive
admission-failure evidence, not a claim that ordinary successful `fdopen` leaks.

Test-first `a92f9c7` has four methods and three failures. Correction `6c9d677`
uses a small explicit ownership boundary: until `fdopen` returns a stream, the
raw descriptor is owned by the helper. Rejection attempts `os.close`; successful
adoption leaves closing solely to the stream's context manager. No duplicate
raw-descriptor close is added to the successful path.

A sole rejection retains the exact original exception identity. If descriptor
close also fails, the existing `FileWriteError` retains both exact exceptions.
Temporary-path cleanup still runs independently; if it too fails, the outer
structured error retains the inner primary/close pair and the separate unlink
error. No error is silently swallowed. A failed explicit close is reported,
not retried against a potentially reused descriptor.

This remains ordinary-exception local file ownership. It does not provide
cross-process/crash durability or arbitrate concurrently admitted writers.
The separate config-store text writer is not silently changed by this patch.

## Checks

- Four descriptor methods pass: rejected adoption closes; successful adoption
  avoids double close; failed close retains both errors while unlink proceeds;
  simultaneous fdopen/close/unlink failures retain all three.
- 68 audio-write/configuration/profile/settings/draft methods pass.
- Static gate passes with unchanged budgets and existing source accounting.

Raw output is in
`../evidence/clean-code-20261016/preference-descriptor-recovery/`.
Reproduce the targeted suite with
`python tests/test_preference_file_descriptor.py` in the hash-pinned environment.
The test fixture cleans up descriptors deliberately left open by an injected
close failure. No native audio output, robot, user computer or paid service is
used. Full integrated regression remains separate.
