# Selected correction recheck

2026-10-16 05:39 UTC. Tested local revision `3b036cb5795409af3b02eb092705deeea79474ab`.

All seven suites that failed wave4 were rerun together through the shared verifier: **7 PASS, 0 SKIP, 0 INCOMPLETE, 0 FAIL**. This is a selected recheck, not a complete aggregate or hardware qualification.

Corrections keep behavioral assertions: normal interaction fixtures await actual startup-profile settlement and confirmed asynchronous close; fake backend/audit collaborators now implement the declared pending/close/snapshot contracts; the audio test checks actual file/cache merge rather than spying on a retired save call; the extracted close harness supplies its warning port and checks both errors are visible. The virtual-robot startup test isolates unrelated native MQTT/UDP start while separately testing real disconnect notification clearing the held key. The original failed report is retained in wave4.

Reproduce with the same offscreen/CI environment as wave3 and:

```sh
python tools/verify_all.py --test polar_startup_test --test smoke_test \
  --test student_frontend_test --test test_binding_restore_quality \
  --test test_device_manager_nonblocking --test test_lazy_settings_state \
  --test test_ui_state_regressions --json results.json --log-dir test-logs
```

Additional review findings and subsequent code changes still require their own tests and the next complete aggregate.
