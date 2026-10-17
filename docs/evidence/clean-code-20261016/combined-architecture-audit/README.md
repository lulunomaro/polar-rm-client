# Combined architecture review

Production source: `1417b925fd8b21642fe4957af62a51254db147bc`.
Later evidence-only commits through `ce49dc5` do not change production/tests/tools.
Run in the hash-pinned verification environment, Qt offscreen for API checks:

```sh
python tools/quality_gate.py --json /tmp/static.json --markdown /tmp/static.md
QT_QPA_PLATFORM=offscreen python tests/api_freeze_test.py --mutation-selftest
python tests/test_quality_gates.py
```

The 64-method boundary selection loads these modules with `tests` on sys.path:
`test_crosshair_profile_boundaries`, `test_preference_draft`,
`test_startup_activation`, `test_port_admission_policy`,
`test_window_api_contract`, `test_quality_ratchet_baseline`.

`discovery-boundaries.log` also records every newly added fixture/suite, registry
category, and byte-identity hashes for the baseline, API contract, registry and
metric implementation against `fa2c2b8`. `api.log.gz` is untouched raw output,
compressed to preserve native diagnostics without whitespace normalization.
All commands exited zero. No aggregate runner or global aggregate lock was used.
