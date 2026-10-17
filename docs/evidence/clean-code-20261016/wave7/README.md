# Stage-seven integrated offline checkpoint

Completed 2026-10-16 06:44 UTC. **186 PASS, 12 SKIP, 4 INCOMPLETE, 0 FAIL**. Exact tested source `f784da9` and its tree, clean flag, OS/interpreter/dependency versions are recorded in results.json.

There are no failures in this complete offline selection; however, missing firmware/reference checks remain incomplete/skipped and the runner correctly exits nonzero. This is not release, native Windows GPU, device or robot qualification, nor whole-project Clean Code acceptance.

Scope includes the integrated accepted-profile save identity and ordinary-error rollback, dirty-close admission, pointer/resource assembly, backend lifecycle controller, referee counter/projection changes, and reviewed renderer resource ownership. It excludes the still-pending nonblocking video source/profile integration and subsequent worker-admission fixes, diagnostics-owner work, or later changes.

Earlier failed aggregates and their targeted corrections remain in adjacent directories. Reproduce with the same offline CI/Qt/media guards and `tools/verify_all.py --profile offline`; do not substitute a targeted suite result for this aggregate.
