# Second expanded integration checkpoint

Completed 2026-10-16 06:00 UTC. **165 PASS, 12 SKIP, 3 INCOMPLETE, 4 FAIL**; not an accepted aggregate.

The report includes interpreter, OS, installed package versions and the exact clean source identity captured at start: commit `79655f5e28edb19d109530cf9183d95257b4b3ba`, tree `f613b101f61b17b3dc76ae6873df346729328594`. This is Linux/offscreen/offline evidence only. Use the same `tools/verify_all.py --profile offline` command and environment guards as wave3.

Failures:

- `tests/polar_bridge_test.py`: simplified control fixture lacks the required input-clear protocol; replace with real control behavior rather than relaxing fail-closed production logic.
- `tests/settings_doc_consistency_test.py` and `tools/audit_settings.py`: measured orphan settings 27 versus protected baseline 21; classification/actual consumer analysis is pending. Do not raise the baseline to hide the failure.
- `tools/dt7_link/test_dt7_ui_selftest.py`: a legacy source-text branch assertion must be checked against the extracted command boundary and actual dispatch behavior.

The prior seven failed suites passed after their corrections, but this aggregate adds different failures and therefore remains failed. Later fixes and larger lifecycle/constructor changes are outside this source checkpoint.
