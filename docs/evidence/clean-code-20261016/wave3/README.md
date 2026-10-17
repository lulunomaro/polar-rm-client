# Intermediate Clean Code regression checkpoint

Recorded 2026-10-16, approximately 05:12 UTC. This is an intermediate result, not whole-project acceptance.

- Tested local commit: `0a9947a9550a4c50ab0c056da44fac89c6e8c62d`
- Exact tested Git tree: `5acb5660df6f543ea3e91c9c2fe31c78ac053d62`
- Raw report SHA-256: `b628c6bace8cdafeb9ec8b267eefce2a699e692eaa3e6fdbd245b311afc0de86`
- 139 PASS, 12 SKIP, 4 INCOMPLETE, 0 FAIL. Aggregate remains incomplete / nonzero exit.
- Scope: Linux, Python 3.12.14, Qt 6.8.2, offscreen and fake-driver tests. No native Windows GPU, actual radio, firmware-on-board, or physical robot qualification.
- The later asynchronous window/configuration integration and catalog extraction are NOT covered by this aggregate.
- The earlier source-text-only gimbal failure was replaced with production behavior assertions; it passes here.

Reproduction with the declared project dependencies:

```sh
CI=true PYTHONPATH=. QT_QPA_PLATFORM=offscreen \
POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1 \
python tools/verify_all.py --profile offline \
  --json results.json --log-dir test-logs
```

No firmware reference was selected. Missing firmware/cross-reference material and the legacy installation-only patch test remain explicitly incomplete or skipped. Do not reinterpret those statuses as passes. The local commit must be published before assuming another clone can resolve it; use the exact tree identity to distinguish this stage from later changes.

Known follow-on review findings: MQTT invalidation exception ownership and audio preferences' cross-store rollback. Their fixes and validations belong to subsequent checkpoints; this report cannot certify them.

Review correction: a suspected undefined render-target name was disproven by the module-level import; it is not a confirmed defect. Normal post-settlement frame coverage is still being expanded.
