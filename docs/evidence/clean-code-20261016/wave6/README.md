# Expanded integration, stage six

Completed 2026-10-16 06:30 UTC. **179 PASS, 12 SKIP, 4 INCOMPLETE, 3 FAIL**. Exact clean source and interpreter/dependency versions are embedded in results.json (source commit `950f919`). This is not aggregate acceptance.

Three failures require fixture updates without weakening production behavior:

- async configuration test patches the former manager-level MQTT waiter rather than the extracted captured teardown-plan waiter;
- background locale raster harness lacks the actual paint-session owner now required by production rendering;
- DT7 review input harness lacks the pointer-interaction owner now used by control-lock cleanup.

The original waiting/commit, language/cache/pixel, and held-key/neutral assertions must remain exercised. Later correction commits and any new source/profile changes are outside this checkpoint.

Run with the same offline environment guards and `tools/verify_all.py --profile offline` as preceding checkpoints. Hardware, firmware cross-reference and native Windows GPU limitations remain explicit.
